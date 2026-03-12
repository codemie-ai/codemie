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

from datetime import datetime
from typing import Union, AsyncIterable
from uuid import uuid4

from codemie.configs import logger
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.a2a.server.task_manager import TaskManager
from codemie.rest_api.a2a.server.utils import (
    are_modalities_compatible,
    new_incompatible_types_error,
    new_not_implemented_error,
)
from codemie.rest_api.a2a.utils import convert_messages_to_chat_messages
from codemie.rest_api.a2a.types import (
    SendTaskRequest,
    SendTaskResponse,
    TaskStatus,
    TaskState,
    Message,
    TextPart,
    GetTaskRequest,
    GetTaskResponse,
    TaskQueryParams,
    TaskNotFoundError,
    Task,
    Artifact,
    TaskSendParams,
    SendTaskStreamingRequest,
    JSONRPCResponse,
    SendTaskStreamingResponse,
)
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService

SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]


class CodemieTaskManager(TaskManager):
    def __init__(self, assistant: Assistant, user: User):
        super().__init__()
        self.user = user
        self.assistant = assistant

    async def on_get_task(self, request: GetTaskRequest) -> GetTaskResponse:
        task_query_params: TaskQueryParams = request.params
        logger.info(f"Getting task {task_query_params.id}")

        task = Task.find_by_id(task_query_params.id)
        if task is None:
            return GetTaskResponse(id=request.id, error=TaskNotFoundError())

        task_result = self._append_task_history(task, task_query_params.historyLength)

        return GetTaskResponse(id=request.id, result=task_result)

    async def on_send_task(self, request: SendTaskRequest) -> SendTaskResponse:
        """Handles the 'send task' request."""
        validation_error = self._validate_request(request)
        if validation_error:
            return SendTaskResponse(id=request.id, error=validation_error.error)

        task = self.upsert_task(request.params)
        return self._invoke_agent(request, task)

    async def on_send_task_subscribe(
        self, request: SendTaskStreamingRequest
    ) -> Union[AsyncIterable[SendTaskStreamingResponse], JSONRPCResponse]:
        raise new_not_implemented_error(request.id)

    @staticmethod
    def upsert_task(task_send_params: TaskSendParams) -> Task:
        logger.debug(f"Upserting task. ID: {task_send_params.id}. SessionId: {task_send_params.sessionId}")
        task = Task.find_by_id(task_send_params.id)
        if not task:
            task = Task(
                id=task_send_params.id,
                sessionId=task_send_params.sessionId,
                status=TaskStatus(state=TaskState.SUBMITTED),
                history=[task_send_params.message],
            )
            task.save(refresh=True)
        else:
            task.history.append(task_send_params.message)
            task.update(refresh=True)

        return task

    @staticmethod
    def _validate_request(request: Union[SendTaskRequest, SendTaskStreamingRequest]) -> JSONRPCResponse | None:
        task_send_params: TaskSendParams = request.params
        if not are_modalities_compatible(task_send_params.acceptedOutputModes, SUPPORTED_CONTENT_TYPES):
            logger.warning(
                "Unsupported output mode. Received %s, Support %s",
                task_send_params.acceptedOutputModes,
                SUPPORTED_CONTENT_TYPES,
            )
            return new_incompatible_types_error(request.id)

        return None

    @staticmethod
    def update_store(task_id: str, status: TaskStatus, artifacts: list[Artifact]) -> Task:
        task = Task.find_by_id(task_id)
        if not task:
            logger.error(f"Task {task_id} not found for updating the task")
            raise ValueError(f"Task {task_id} not found")

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
    def _append_task_history(task: Task, history_length: int | None):
        new_task = task.model_copy()
        if history_length is not None and history_length > 0:
            new_task.history = new_task.history[-history_length:]
        else:
            new_task.history = []
        return new_task

    def _invoke_agent(self, request: SendTaskRequest, task: Task) -> SendTaskResponse:
        agent, assistant_request = self._setup_agent(request, task)
        self.update_store(task_id=request.params.id, status=TaskStatus(state=TaskState.WORKING), artifacts=[])
        try:
            agent_response = agent.invoke_with_a2a_output(assistant_request.text)
            return self._process_agent_response(request, agent_response)
        except Exception as e:
            return self._process_error_response(request, e)

    def _setup_agent(self, request: Union[SendTaskRequest, SendTaskStreamingRequest], task: Task):
        """Set up the agent and assistant request for task processing."""
        task_send_params = request.params
        query = self._get_user_query(task_send_params)
        chat_messages = convert_messages_to_chat_messages(task.history)
        assistant_request = AssistantChatRequest(
            text=query, conversation_id=task_send_params.sessionId, history=chat_messages
        )
        agent = AssistantService.build_agent(
            assistant=self.assistant, request=assistant_request, user=self.user, request_uuid=str(uuid4())
        )
        return agent, assistant_request

    async def _handle_a2a_stream(
        self, request: SendTaskStreamingRequest, task: Task
    ) -> AsyncIterable[SendTaskStreamingResponse]:
        """Handle streaming A2A requests."""
        try:
            # Mark task as working
            self.update_store(task_id=request.params.id, status=TaskStatus(state=TaskState.WORKING), artifacts=[])

            # Setup agent
            agent, assistant_request = self._setup_agent(request, task)

            # Initial response
            yield self._create_streaming_response(request.id, task, TaskState.WORKING)

            # For now, just use the non-streaming implementation
            agent_response = agent.invoke_with_a2a_output(assistant_request.text)

            # Process response
            parts = self._create_response_parts(agent_response.get("content"))
            state = TaskState.INPUT_REQUIRED if agent_response.get("require_user_input") else TaskState.COMPLETED

            # Update task with final state
            artifact = None if state == TaskState.INPUT_REQUIRED else Artifact(parts=parts)
            task_status = TaskStatus(state=state, message=Message(role="agent", parts=parts))
            task = self.update_store(request.params.id, task_status, None if artifact is None else [artifact])

            # Send final response
            yield self._create_streaming_response(request.id, task, state)

        except Exception as e:
            logger.error(f"Error in streaming agent invocation: {e}")
            parts = self._create_response_parts(f"Error: {str(e)}")
            task_status = TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=parts))
            task = self.update_store(request.params.id, task_status, [])
            yield self._create_streaming_response(request.id, task, TaskState.FAILED)

    def _create_streaming_response(self, request_id: str, task: Task, state: TaskState) -> SendTaskStreamingResponse:
        """Create a streaming response object."""
        task_result = self._append_task_history(task, task.history_length if hasattr(task, 'history_length') else None)
        return SendTaskStreamingResponse(id=request_id, result=task_result)

    def _create_response_parts(self, content: str) -> list[dict]:
        """Create response parts from content."""
        return [{"type": "text", "text": content}]

    def _process_error_response(self, request, e) -> SendTaskResponse:
        logger.error(f"Error invoking agent: {e}")
        parts = self._create_response_parts(f"Error: {str(e)}")
        task_status = TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=parts))
        task = self.update_store(request.params.id, task_status, [])
        task_result = self._append_task_history(task, request.params.historyLength)
        return SendTaskResponse(id=request.id, result=task_result)

    def _process_agent_response(self, request: SendTaskRequest, agent_response: dict) -> SendTaskResponse:
        """Processes the agent's response and updates the task store."""
        logger.debug(f"Received response. {agent_response}")
        task_send_params: TaskSendParams = request.params
        task_id = task_send_params.id
        history_length = task_send_params.historyLength

        parts = self._create_response_parts(agent_response.get("content"))
        artifact = None
        if agent_response.get("require_user_input"):
            task_status = TaskStatus(
                state=TaskState.INPUT_REQUIRED,
                message=Message(role="agent", parts=parts),
            )
        else:
            task_status = TaskStatus(state=TaskState.COMPLETED)
            artifact = Artifact(parts=parts)

        task = self.update_store(task_id, task_status, None if artifact is None else [artifact])
        task_result = self._append_task_history(task, history_length)
        return SendTaskResponse(id=request.id, result=task_result)

    @staticmethod
    def _get_user_query(task_send_params: TaskSendParams) -> str:
        part = task_send_params.message.parts[0]
        if not isinstance(part, TextPart):
            raise ValueError("Only text parts are supported")
        return part.text
