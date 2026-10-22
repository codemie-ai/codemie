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

import asyncio
from datetime import datetime
from typing import AsyncIterable
from uuid import uuid4

from codemie.configs import logger
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.a2a.server.task_manager import TaskManager
from codemie.rest_api.a2a.server.utils import are_modalities_compatible
from codemie.rest_api.a2a.utils import (
    assistant_to_agent_card,
    convert_messages_to_chat_messages,
    public_agent_card_dict,
)
from codemie.rest_api.a2a.types import (
    Artifact,
    CancelTaskRPCRequest,
    CancelTaskRPCResponse,
    ContentTypeNotSupportedError,
    GetExtendedAgentCardRPCRequest,
    GetExtendedAgentCardRPCResponse,
    GetTaskRPCRequest,
    GetTaskRPCResponse,
    InvalidParamsError,
    JSONRPCError,
    Message,
    Part,
    Role,
    SendMessageResponse,
    SendMessageRPCRequest,
    SendMessageRPCResponse,
    SendStreamingMessageRPCRequest,
    StreamResponse,
    StreamRPCResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    UnsupportedOperationError,
    bad_request,
    error_info,
)
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService

SUPPORTED_CONTENT_TYPES = ["text/plain"]
TERMINAL_STATES = (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED)


class CodemieTaskManager(TaskManager):
    def __init__(self, assistant: Assistant, user: User):
        super().__init__()
        self.user = user
        self.assistant = assistant

    async def on_get_task(self, request: GetTaskRPCRequest) -> GetTaskRPCResponse:
        params = request.params
        logger.info(f"Getting task {params.id}")

        task = self._find_owned_task(params.id)
        if task is None:
            return GetTaskRPCResponse(id=request.id, error=self._task_not_found(params.id))

        return GetTaskRPCResponse(id=request.id, result=self._append_task_history(task, params.historyLength))

    async def on_cancel_task(self, request: CancelTaskRPCRequest) -> CancelTaskRPCResponse:
        params = request.params
        logger.info(f"Canceling task {params.id}")

        task = self._find_owned_task(params.id)
        if task is None:
            return CancelTaskRPCResponse(id=request.id, error=self._task_not_found(params.id))

        if task.status.state in TERMINAL_STATES:
            error = TaskNotCancelableError(data=[error_info("TASK_NOT_CANCELABLE", taskId=params.id)])
            return CancelTaskRPCResponse(id=request.id, error=error)

        updated_task = self.update_store(task_id=params.id, status=TaskStatus(state=TaskState.CANCELED), artifacts=[])
        return CancelTaskRPCResponse(id=request.id, result=updated_task)

    async def on_get_extended_agent_card(
        self, request: GetExtendedAgentCardRPCRequest, http_request
    ) -> GetExtendedAgentCardRPCResponse:
        card = assistant_to_agent_card(self.assistant, http_request)
        return GetExtendedAgentCardRPCResponse(id=request.id, result=public_agent_card_dict(card))

    async def on_send_task(self, request: SendMessageRPCRequest) -> SendMessageRPCResponse:
        """Handles the 'SendMessage' request."""
        error = self._validate_request(request) or self._check_existing_task(request)
        if error:
            return SendMessageRPCResponse(id=request.id, error=error)

        task = self.upsert_task(request.params.message, assistant_id=self.assistant.id, user_id=self.user.id)
        return await asyncio.to_thread(self._invoke_agent, request, task)

    async def on_send_streaming_message(
        self, request: SendStreamingMessageRPCRequest
    ) -> AsyncIterable[StreamRPCResponse]:
        error = self._validate_request(request) or self._check_existing_task(request)
        if error:
            yield StreamRPCResponse(id=request.id, error=error)
            return

        task = self.upsert_task(request.params.message, assistant_id=self.assistant.id, user_id=self.user.id)
        async for frame in self._handle_a2a_stream(request, task):
            yield frame

    @staticmethod
    def _task_not_found(task_id: str) -> TaskNotFoundError:
        return TaskNotFoundError(data=[error_info("TASK_NOT_FOUND", taskId=task_id)])

    @staticmethod
    def _context_id(task: Task) -> str:
        return task.contextId or task.sessionId or ""

    def _is_owner(self, task: Task) -> bool:
        """A task belongs to the (assistant, user) pair that created it; legacy unowned tasks belong to no one."""
        return task.assistant_id == self.assistant.id and task.user_id == self.user.id

    def _find_owned_task(self, task_id: str) -> Task | None:
        task = Task.find_by_id(task_id)
        if task is None or not self._is_owner(task):
            return None
        return task

    def _check_existing_task(
        self, request: SendMessageRPCRequest | SendStreamingMessageRPCRequest
    ) -> JSONRPCError | None:
        """A message naming a task must continue an owned, non-terminal task of the same context."""
        message = request.params.message
        if not message.taskId:
            return None
        existing = self._find_owned_task(message.taskId)
        if existing is None:
            return self._task_not_found(message.taskId)
        if existing.status.state in TERMINAL_STATES:
            return UnsupportedOperationError(data=[error_info("UNSUPPORTED_OPERATION", taskId=message.taskId)])
        if message.contextId and message.contextId != self._context_id(existing):
            return InvalidParamsError(
                data=[bad_request([("message.contextId", "contextId does not match the context of the given task")])]
            )
        return None

    @staticmethod
    def upsert_task(message: Message, assistant_id: str | None = None, user_id: str | None = None) -> Task:
        """Create a task for the message (server-generated ids) or append it to the task it names."""
        existing = Task.find_by_id(message.taskId) if message.taskId else None
        if existing is not None:
            stamped = message.model_copy(update={"contextId": CodemieTaskManager._context_id(existing)})
            logger.debug(f"Continuing task. ID: {existing.id}. ContextId: {stamped.contextId}")
            if existing.history is None:
                existing.history = []
            existing.history.append(stamped)
            existing.update(refresh=True)
            return existing

        task_id = uuid4().hex
        context_id = message.contextId or uuid4().hex
        logger.debug(f"Creating task. ID: {task_id}. ContextId: {context_id}")
        stamped = message.model_copy(update={"taskId": task_id, "contextId": context_id})
        task = Task(
            id=task_id,
            contextId=context_id,
            assistant_id=assistant_id,
            user_id=user_id,
            status=TaskStatus(state=TaskState.SUBMITTED),
            history=[stamped],
        )
        task.save(refresh=True)
        return task

    @staticmethod
    def _validate_request(request: SendMessageRPCRequest | SendStreamingMessageRPCRequest) -> JSONRPCError | None:
        params = request.params
        configuration = params.configuration
        if configuration is not None:
            if configuration.returnImmediately:
                return UnsupportedOperationError(
                    message="returnImmediately is not supported",
                    data=[error_info("UNSUPPORTED_OPERATION", parameter="configuration.returnImmediately")],
                )
            if not are_modalities_compatible(configuration.acceptedOutputModes, SUPPORTED_CONTENT_TYPES):
                logger.warning(
                    "Unsupported output mode. Received %s, Support %s",
                    configuration.acceptedOutputModes,
                    SUPPORTED_CONTENT_TYPES,
                )
                return ContentTypeNotSupportedError()

        if any(part.text is None for part in params.message.parts):
            return InvalidParamsError(data=[bad_request([("message.parts", "Only text parts are supported")])])

        return None

    @staticmethod
    def update_store(task_id: str, status: TaskStatus, artifacts: list[Artifact] | None) -> Task:
        task = Task.find_by_id(task_id)
        if not task:
            logger.error(f"Task {task_id} not found for updating the task")
            raise ValueError(f"Task {task_id} not found")

        if task.status.state in TERMINAL_STATES and status.state != task.status.state:
            # Terminal states are final: e.g. a CancelTask that landed while the agent was running
            # must not be overwritten by the late completion/failure write.
            logger.info(f"Task {task_id} is already {task.status.state}; ignoring update to {status.state}")
            return task

        task.status = status
        logger.info(f"Update task {task_id}. Status {task.status.state}")
        if status.message is not None:
            task.history.append(status.message)

        if artifacts is not None:
            if task.artifacts is None:
                task.artifacts = []
            task.artifacts.extend(artifacts)
        task.update_date = datetime.now()
        task.update(refresh=True)
        return task

    @staticmethod
    def _append_task_history(task: Task, history_length: int | None) -> Task:
        """Copy of the task with history shaped per spec: unset -> full, 0 -> omitted, N -> last N."""
        new_task = task.model_copy()
        history = list(task.history or [])
        if history_length is None:
            new_task.history = history
        elif history_length > 0:
            new_task.history = history[-history_length:]
        else:
            new_task.history = None
        return new_task

    @staticmethod
    def _history_length(request: SendMessageRPCRequest | SendStreamingMessageRPCRequest) -> int | None:
        configuration = request.params.configuration
        return configuration.historyLength if configuration else None

    def _invoke_agent(self, request: SendMessageRPCRequest, task: Task) -> SendMessageRPCResponse:
        try:
            self.update_store(task_id=task.id, status=TaskStatus(state=TaskState.WORKING), artifacts=[])
            agent, assistant_request = self._setup_agent(request, task)
            agent_response = agent.invoke_with_a2a_output(assistant_request.text)
            return self._process_agent_response(request, task, agent_response)
        except Exception as e:
            return self._process_error_response(request, task, e)

    def _run_agent(self, request: SendMessageRPCRequest | SendStreamingMessageRPCRequest, task: Task) -> dict:
        agent, assistant_request = self._setup_agent(request, task)
        return agent.invoke_with_a2a_output(assistant_request.text)

    def _setup_agent(self, request: SendMessageRPCRequest | SendStreamingMessageRPCRequest, task: Task):
        """Set up the agent and assistant request for task processing."""
        query = self._get_user_query(request.params.message)
        chat_messages = convert_messages_to_chat_messages(task.history or [])
        assistant_request = AssistantChatRequest(
            text=query, conversation_id=self._context_id(task), history=chat_messages
        )
        agent = AssistantService.build_agent(
            assistant=self.assistant, request=assistant_request, user=self.user, request_uuid=str(uuid4())
        )
        return agent, assistant_request

    def _agent_message(self, task: Task, parts: list[Part]) -> Message:
        return Message(role=Role.AGENT, parts=parts, contextId=self._context_id(task), taskId=task.id)

    async def _handle_a2a_stream(
        self, request: SendStreamingMessageRPCRequest, task: Task
    ) -> AsyncIterable[StreamRPCResponse]:
        """Stream `{task}`, WORKING, optional artifact, then a terminal / INPUT_REQUIRED status update."""
        history_length = self._history_length(request)
        yield StreamRPCResponse(
            id=request.id, result=StreamResponse(task=self._append_task_history(task, history_length))
        )
        try:
            task = self.update_store(task_id=task.id, status=TaskStatus(state=TaskState.WORKING), artifacts=[])
            yield self._status_frame(request.id, task, TaskState.WORKING)

            # The agent run is blocking; keep it off the event loop so other requests are not stalled.
            agent_response = await asyncio.to_thread(self._run_agent, request, task)

            parts = self._create_response_parts(agent_response.get("content"))
            if agent_response.get("require_user_input"):
                state, artifact = TaskState.INPUT_REQUIRED, None
                message = self._agent_message(task, parts)
            else:
                state, artifact, message = TaskState.COMPLETED, Artifact(parts=parts), None

            task = self.update_store(
                task.id, TaskStatus(state=state, message=message), [artifact] if artifact else None
            )

            # A concurrent CancelTask wins over the late agent result
            if task.status.state == TaskState.CANCELED:
                yield self._status_frame(request.id, task, TaskState.CANCELED)
                return
            if artifact is not None:
                yield self._artifact_frame(request.id, task, artifact)
            yield self._status_frame(request.id, task, state, message)

        except Exception as e:
            logger.error(f"Error in streaming agent invocation: {e}")
            message = self._agent_message(task, self._create_response_parts(f"Error: {str(e)}"))
            task = self.update_store(task.id, TaskStatus(state=TaskState.FAILED, message=message), [])
            if task.status.state == TaskState.CANCELED:
                yield self._status_frame(request.id, task, TaskState.CANCELED)
            else:
                yield self._status_frame(request.id, task, TaskState.FAILED, message)

    def _status_frame(
        self, request_id, task: Task, state: TaskState, message: Message | None = None
    ) -> StreamRPCResponse:
        """A statusUpdate frame, optionally carrying the agent message."""
        event = TaskStatusUpdateEvent(
            taskId=task.id, contextId=self._context_id(task), status=TaskStatus(state=state, message=message)
        )
        return StreamRPCResponse(id=request_id, result=StreamResponse(statusUpdate=event))

    def _artifact_frame(self, request_id, task: Task, artifact: Artifact) -> StreamRPCResponse:
        """An artifactUpdate frame."""
        event = TaskArtifactUpdateEvent(taskId=task.id, contextId=self._context_id(task), artifact=artifact)
        return StreamRPCResponse(id=request_id, result=StreamResponse(artifactUpdate=event))

    def _create_response_parts(self, content: str | None) -> list[Part]:
        """Create response parts from content."""
        return [Part(text=content or "")]

    def _process_error_response(
        self, request: SendMessageRPCRequest, task: Task, e: Exception
    ) -> SendMessageRPCResponse:
        logger.error(f"Error invoking agent: {e}")
        message = self._agent_message(task, self._create_response_parts(f"Error: {str(e)}"))
        updated = self.update_store(task.id, TaskStatus(state=TaskState.FAILED, message=message), [])
        return self._task_response(request, updated)

    def _process_agent_response(
        self, request: SendMessageRPCRequest, task: Task, agent_response: dict
    ) -> SendMessageRPCResponse:
        """Processes the agent's response and updates the task store."""
        logger.debug(f"Received response. {agent_response}")
        parts = self._create_response_parts(agent_response.get("content"))
        artifact = None
        if agent_response.get("require_user_input"):
            task_status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=self._agent_message(task, parts))
        else:
            task_status = TaskStatus(state=TaskState.COMPLETED)
            artifact = Artifact(parts=parts)

        updated = self.update_store(task.id, task_status, None if artifact is None else [artifact])
        return self._task_response(request, updated)

    def _task_response(self, request: SendMessageRPCRequest, task: Task) -> SendMessageRPCResponse:
        shaped = self._append_task_history(task, self._history_length(request))
        return SendMessageRPCResponse(id=request.id, result=SendMessageResponse(task=shaped))

    @staticmethod
    def _get_user_query(message: Message) -> str:
        if any(part.text is None for part in message.parts):
            raise ValueError("Only text parts are supported")
        return "\n".join(part.text for part in message.parts)
