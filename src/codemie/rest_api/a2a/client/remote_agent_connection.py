# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import uuid
from typing import Callable

from codemie.rest_api.a2a.client.client import A2AClient
from codemie.rest_api.a2a.types import (
    AgentCard,
    JSONRPCResponse,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

TaskCallbackArg = Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
TaskUpdateCallback = Callable[[TaskCallbackArg, AgentCard], Task]

# A stream ends on a terminal state or an interrupted one (the agent waits for the user).
FINAL_STATES = (
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELED,
    TaskState.REJECTED,
    TaskState.INPUT_REQUIRED,
    TaskState.AUTH_REQUIRED,
)


def is_final_update(update: TaskCallbackArg) -> bool:
    """True when the update ends the exchange: a direct Message, or a Task / status update in a final state."""
    if isinstance(update, Message):
        return True
    if isinstance(update, (Task, TaskStatusUpdateEvent)):
        return update.status.state in FINAL_STATES
    return False


class RemoteAgentConnections:
    """A class to hold the connections to the remote agents."""

    def __init__(self, agent_card: AgentCard):
        self.agent_client = A2AClient(agent_card)
        self.card = agent_card

        self.conversation_name = None
        self.conversation = None
        self.pending_tasks = set()

    def get_agent(self) -> AgentCard:
        return self.card

    async def send_task(
        self,
        request: SendMessageRequest,
        task_callback: TaskUpdateCallback | None,
    ) -> Task | Message | None:
        payload = request.model_dump(by_alias=True, exclude_none=True)
        if self.card.capabilities.streaming:
            await self._send_streaming(request, payload, task_callback)
            return None
        return await self._send_non_streaming(request, payload, task_callback)

    async def _send_streaming(self, request: SendMessageRequest, payload: dict, task_callback) -> None:
        if task_callback:
            task_callback(
                Task(
                    id=request.message.taskId,
                    contextId=request.message.contextId,
                    status=TaskStatus(state=TaskState.SUBMITTED, message=request.message),
                    history=[request.message],
                ),
                self.card,
            )
        async for response in self.agent_client.send_task_streaming(payload):
            if response.error:
                self._process_error_response(request, response, task_callback)
                break
            stream_response = response.result
            if stream_response is None:
                continue  # empty frame (no result, no error): nothing to deliver
            update = (
                stream_response.task
                or stream_response.message
                or stream_response.statusUpdate
                or stream_response.artifactUpdate
            )
            self._process_result(request, update, task_callback)
            if is_final_update(update):
                break

    async def _send_non_streaming(
        self, request: SendMessageRequest, payload: dict, task_callback
    ) -> Task | Message | None:
        response = await self.agent_client.send_task(payload)
        if response.result:
            result = response.result.task or response.result.message
            self._process_result(request, result, task_callback)
            return result
        if response.error:
            return self._process_error_response(request, response, task_callback)
        return None

    def _process_result(self, request: SendMessageRequest, result: TaskCallbackArg, task_callback):
        merge_metadata(result, request)
        # For task status updates, we need to propagate metadata and provide
        # a unique message id.
        status = getattr(result, 'status', None)
        if status is not None and status.message:
            merge_metadata(status.message, request.message)
            m = status.message
            if not m.metadata:
                m.metadata = {}
            if 'message_id' in m.metadata:
                m.metadata['last_message_id'] = m.metadata['message_id']
            m.metadata['message_id'] = str(uuid.uuid4())
        if task_callback:
            task_callback(result, self.card)

    def _process_error_response(self, request: SendMessageRequest, response: JSONRPCResponse, task_callback):
        parts = [Part(text=response.error.message)]
        task = Task(
            id=request.message.taskId,
            contextId=request.message.contextId,
            status=TaskStatus(state=TaskState.FAILED, message=Message(role=Role.AGENT, parts=parts)),
        )
        if task_callback:
            task_callback(task, self.card)
        return task


def merge_metadata(target, source):
    if not hasattr(target, 'metadata') or not hasattr(source, 'metadata'):
        return
    if target.metadata is not None and not isinstance(target.metadata, dict):
        return  # e.g. SQLModel tables expose their SQLAlchemy MetaData under this name
    if target.metadata and source.metadata:
        target.metadata.update(source.metadata)
    elif source.metadata:
        target.metadata = dict(**source.metadata)
