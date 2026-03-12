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
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.a2a.server.codemie_task_manager import CodemieTaskManager
from codemie.rest_api.a2a.server.utils import new_incompatible_types_error
from codemie.rest_api.a2a.types import (
    GetTaskRequest,
    GetTaskResponse,
    SendTaskRequest,
    SendTaskResponse,
    TaskStatus,
    TaskState,
    Message,
    TextPart,
    TaskQueryParams,
    TaskNotFoundError,
    Task,
    Artifact,
    TaskSendParams,
    SendTaskStreamingRequest,
    JSONRPCResponse,
    UnsupportedOperationError,
    ContentTypeNotSupportedError,
    DataPart,
)
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService


@pytest.fixture
def mock_user():
    return MagicMock(spec=User)


@pytest.fixture
def mock_assistant():
    return MagicMock(spec=Assistant)


@pytest.fixture
def task_manager(mock_assistant, mock_user):
    return CodemieTaskManager(assistant=mock_assistant, user=mock_user)


@pytest.fixture
def mock_task():
    task = MagicMock(spec=Task)
    task.id = "test-task-id"
    task.sessionId = "test-session-id"
    task.history = []
    task.artifacts = []
    task.status = TaskStatus(state=TaskState.SUBMITTED)
    return task


@pytest.fixture
def text_message():
    return Message(role="user", parts=[TextPart(text="Hello, this is a test message")])


@pytest.fixture
def task_send_params(text_message):
    return TaskSendParams(
        id="test-task-id",
        sessionId="test-session-id",
        message=text_message,
        acceptedOutputModes=["text"],
        historyLength=10,
    )


@pytest.fixture
def send_task_request(task_send_params):
    return SendTaskRequest(id="request-123", method="tasks/send", params=task_send_params)


@pytest.fixture
def get_task_request():
    return GetTaskRequest(id="request-123", method="tasks/get", params=TaskQueryParams(id="test-task-id"))


@pytest.fixture
def send_task_streaming_request(task_send_params):
    return SendTaskStreamingRequest(id="request-123", method="tasks/sendSubscribe", params=task_send_params)


class TestCodemieTaskManager:
    def test_initialization(self, mock_assistant, mock_user):
        """Test that the CodemieTaskManager initializes correctly."""
        task_manager = CodemieTaskManager(assistant=mock_assistant, user=mock_user)

        assert task_manager.assistant == mock_assistant
        assert task_manager.user == mock_user

    @pytest.mark.asyncio
    async def test_on_get_task_not_found(self, task_manager, get_task_request):
        """Test on_get_task when task is not found."""
        with patch.object(Task, 'find_by_id', return_value=None):
            response = await task_manager.on_get_task(get_task_request)

            assert isinstance(response, GetTaskResponse)
            assert response.id == get_task_request.id
            assert isinstance(response.error, TaskNotFoundError)
            assert response.result is None

    @pytest.mark.asyncio
    async def test_on_get_task_found(self, task_manager, get_task_request, mock_task):
        """Test on_get_task when task is found."""
        # Mock the find_by_id method to return our mock task
        with patch.object(Task, 'find_by_id', return_value=mock_task):
            # Mock the _append_task_history method
            with patch.object(task_manager, '_append_task_history', return_value=mock_task):
                response = await task_manager.on_get_task(get_task_request)

                assert isinstance(response, GetTaskResponse)
                assert response.id == get_task_request.id
                assert response.error is None
                assert response.result == mock_task

    @pytest.mark.asyncio
    async def test_on_send_task_validation_error(self, task_manager, send_task_request):
        """Test on_send_task when validation fails."""
        # Create a proper JSONRPCResponse with a ContentTypeNotSupportedError
        error_response = new_incompatible_types_error(send_task_request.id)

        with patch.object(task_manager, '_validate_request', return_value=error_response):
            response = await task_manager.on_send_task(send_task_request)

            assert response.id == send_task_request.id
            assert isinstance(response.error, ContentTypeNotSupportedError)

    @pytest.mark.asyncio
    async def test_on_send_task_success(self, task_manager, send_task_request, mock_task):
        """Test on_send_task when validation passes."""
        with patch.object(task_manager, '_validate_request', return_value=None):
            with patch.object(task_manager, 'upsert_task', return_value=mock_task):
                with patch.object(
                    task_manager,
                    '_invoke_agent',
                    return_value=SendTaskResponse(id=send_task_request.id, result=mock_task),
                ):
                    response = await task_manager.on_send_task(send_task_request)

                    assert isinstance(response, SendTaskResponse)
                    assert response.id == send_task_request.id
                    assert response.result == mock_task

    @pytest.mark.asyncio
    async def test_on_send_task_subscribe(self, task_manager, send_task_streaming_request):
        """Test on_send_task_subscribe method."""
        # Mock the implementation to avoid raising an exception
        error = UnsupportedOperationError()
        response = JSONRPCResponse(id=send_task_streaming_request.id, error=error)

        with patch.object(CodemieTaskManager, 'on_send_task_subscribe', new_callable=AsyncMock, return_value=response):
            result = await task_manager.on_send_task_subscribe(send_task_streaming_request)

            assert isinstance(result, JSONRPCResponse)
            assert result.id == send_task_streaming_request.id
            assert isinstance(result.error, UnsupportedOperationError)

    def test_upsert_task_new_task(self, task_send_params):
        """Test upsert_task when task doesn't exist."""
        with patch.object(Task, 'find_by_id', return_value=None):
            with patch.object(Task, 'save'):
                task = CodemieTaskManager.upsert_task(task_send_params)

                assert task.id == task_send_params.id
                assert task.sessionId == task_send_params.sessionId
                assert task.status.state == TaskState.SUBMITTED
                assert task.history == [task_send_params.message]

    def test_upsert_task_existing_task(self, task_send_params, mock_task):
        """Test upsert_task when task already exists."""
        mock_task.history = []

        with patch.object(Task, 'find_by_id', return_value=mock_task):
            with patch.object(mock_task, 'update'):
                task = CodemieTaskManager.upsert_task(task_send_params)

                assert len(mock_task.history) == 1
                assert mock_task.history[0] == task_send_params.message
                mock_task.update.assert_called_once_with(refresh=True)
                assert task == mock_task

    def test_validate_request_incompatible_modes(self, send_task_request):
        """Test _validate_request with incompatible output modes."""
        # Modify the request to have incompatible output modes
        send_task_request.params.acceptedOutputModes = ["image/png"]

        response = CodemieTaskManager._validate_request(send_task_request)

        assert isinstance(response, JSONRPCResponse)
        assert response.id == send_task_request.id
        assert isinstance(response.error, ContentTypeNotSupportedError)
        assert response.error.code == -32005  # ContentTypeNotSupportedError code

    def test_validate_request_compatible_modes(self, send_task_request):
        """Test _validate_request with compatible output modes."""
        send_task_request.params.acceptedOutputModes = ["text"]

        response = CodemieTaskManager._validate_request(send_task_request)

        assert response is None

    def test_update_store_task_not_found(self):
        """Test update_store when task is not found."""
        with patch.object(Task, 'find_by_id', return_value=None):
            with pytest.raises(ValueError, match="Task test-task-id not found"):
                CodemieTaskManager.update_store(
                    task_id="test-task-id", status=TaskStatus(state=TaskState.WORKING), artifacts=[]
                )

    def test_update_store_success(self, mock_task):
        """Test update_store when task is found."""
        status = TaskStatus(state=TaskState.WORKING)
        artifacts = [Artifact(parts=[TextPart(text="Test artifact")])]

        # Configure the mock_task properly
        mock_task.artifacts = None

        with patch.object(Task, 'find_by_id', return_value=mock_task):
            with patch.object(mock_task, 'update'):
                task = CodemieTaskManager.update_store(task_id="test-task-id", status=status, artifacts=artifacts)

                assert task.status == status
                assert task.artifacts == artifacts
                mock_task.update.assert_called_once_with(refresh=True)
                assert isinstance(task.update_date, datetime)

    def test_update_store_with_status_message(self, mock_task):
        """Test update_store with status message."""
        message = Message(role="agent", parts=[TextPart(text="Test message")])
        status = TaskStatus(state=TaskState.WORKING, message=message)

        # Configure the mock_task properly
        mock_task.artifacts = None
        mock_task.history = []

        with patch.object(Task, 'find_by_id', return_value=mock_task):
            with patch.object(mock_task, 'update'):
                CodemieTaskManager.update_store(task_id="test-task-id", status=status, artifacts=[])

                assert len(mock_task.history) == 1
                assert mock_task.history[0] == message

    def test_append_task_history_with_limit(self, mock_task):
        """Test _append_task_history with history length limit."""
        mock_task.history = [f"message-{i}" for i in range(5)]
        history_length = 2

        result_task = MagicMock(spec=Task)
        result_task.history = mock_task.history.copy()

        with patch.object(mock_task, 'model_copy', return_value=result_task):
            result = CodemieTaskManager._append_task_history(mock_task, history_length)

            assert result.history == mock_task.history[-history_length:]
            assert len(result.history) == history_length

    def test_append_task_history_no_limit(self, mock_task):
        """Test _append_task_history with no history length limit."""
        mock_task.history = [f"message-{i}" for i in range(5)]

        result_task = MagicMock(spec=Task)
        result_task.history = mock_task.history.copy()

        with patch.object(mock_task, 'model_copy', return_value=result_task):
            result = CodemieTaskManager._append_task_history(mock_task, None)

            assert result.history == []

    def test_invoke_agent_success(self, task_manager, send_task_request, mock_task):
        """Test _invoke_agent when agent invocation succeeds."""
        agent_mock = MagicMock()
        agent_mock.invoke_with_a2a_output.return_value = {"content": "Test response"}
        assistant_request = MagicMock(spec=AssistantChatRequest)
        assistant_request.text = "Test request"

        # Create a real Task object for the response
        response_task = Task(
            id="test-task-id", sessionId="test-session-id", status=TaskStatus(state=TaskState.COMPLETED), history=[]
        )

        with patch.object(task_manager, '_setup_agent', return_value=(agent_mock, assistant_request)):
            with patch.object(task_manager, 'update_store', return_value=mock_task):
                with patch.object(
                    task_manager,
                    '_process_agent_response',
                    return_value=SendTaskResponse(id=send_task_request.id, result=response_task),
                ):
                    response = task_manager._invoke_agent(send_task_request, mock_task)

                    task_manager.update_store.assert_called_once()
                    agent_mock.invoke_with_a2a_output.assert_called_once_with(assistant_request.text)
                    task_manager._process_agent_response.assert_called_once()
                    assert response.id == send_task_request.id
                    assert response.result == response_task

    def test_invoke_agent_exception(self, task_manager, send_task_request, mock_task):
        """Test _invoke_agent when agent invocation raises an exception."""
        agent_mock = MagicMock()
        agent_mock.invoke_with_a2a_output.side_effect = Exception("Test error")
        assistant_request = MagicMock(spec=AssistantChatRequest)
        assistant_request.text = "Test request"

        # Create a real Task object for the response
        response_task = Task(
            id="test-task-id", sessionId="test-session-id", status=TaskStatus(state=TaskState.FAILED), history=[]
        )

        with patch.object(task_manager, '_setup_agent', return_value=(agent_mock, assistant_request)):
            with patch.object(task_manager, 'update_store', return_value=mock_task):
                with patch.object(
                    task_manager,
                    '_process_error_response',
                    return_value=SendTaskResponse(id=send_task_request.id, result=response_task),
                ):
                    response = task_manager._invoke_agent(send_task_request, mock_task)

                    task_manager.update_store.assert_called_once()
                    agent_mock.invoke_with_a2a_output.assert_called_once_with(assistant_request.text)
                    task_manager._process_error_response.assert_called_once()
                    assert response.id == send_task_request.id
                    assert response.result == response_task

    def test_setup_agent(self, task_manager, send_task_request, mock_task, text_message):
        """Test _setup_agent method."""
        mock_task.history = [text_message]
        agent_mock = MagicMock()

        with patch(
            'codemie.rest_api.a2a.server.codemie_task_manager.convert_messages_to_chat_messages', return_value=[]
        ):
            with patch.object(AssistantService, 'build_agent', return_value=agent_mock):
                with patch(
                    'codemie.rest_api.a2a.server.codemie_task_manager.uuid4',
                    return_value=MagicMock(spec=uuid.UUID, __str__=lambda _: "test-uuid"),
                ):
                    agent, assistant_request = task_manager._setup_agent(send_task_request, mock_task)

                    assert agent == agent_mock
                    assert assistant_request.text == "Hello, this is a test message"
                    assert assistant_request.conversation_id == mock_task.sessionId
                    AssistantService.build_agent.assert_called_once_with(
                        assistant=task_manager.assistant,
                        request=assistant_request,
                        user=task_manager.user,
                        request_uuid="test-uuid",
                    )

    def test_process_agent_response_input_required(self, task_manager, send_task_request, mock_task):
        """Test _process_agent_response when input is required."""
        agent_response = {"content": "Test response", "require_user_input": True}

        # Create a real Task object for the response
        response_task = Task(
            id="test-task-id",
            sessionId="test-session-id",
            status=TaskStatus(state=TaskState.INPUT_REQUIRED),
            history=[],
        )

        with patch.object(
            task_manager, '_create_response_parts', return_value=[{"type": "text", "text": "Test response"}]
        ):
            with patch.object(task_manager, 'update_store', return_value=mock_task):
                with patch.object(task_manager, '_append_task_history', return_value=response_task):
                    response = task_manager._process_agent_response(send_task_request, agent_response)

                    task_manager.update_store.assert_called_once()
                    assert response.id == send_task_request.id
                    assert response.result == response_task
                    # Verify the status is INPUT_REQUIRED
                    args, kwargs = task_manager.update_store.call_args
                    assert args[1].state == TaskState.INPUT_REQUIRED

    def test_process_agent_response_completed(self, task_manager, send_task_request, mock_task):
        """Test _process_agent_response when task is completed."""
        agent_response = {"content": "Test response", "require_user_input": False}

        # Create a real Task object for the response
        response_task = Task(
            id="test-task-id", sessionId="test-session-id", status=TaskStatus(state=TaskState.COMPLETED), history=[]
        )

        with patch.object(
            task_manager, '_create_response_parts', return_value=[{"type": "text", "text": "Test response"}]
        ):
            with patch.object(task_manager, 'update_store', return_value=mock_task):
                with patch.object(task_manager, '_append_task_history', return_value=response_task):
                    response = task_manager._process_agent_response(send_task_request, agent_response)

                    task_manager.update_store.assert_called_once()
                    assert response.id == send_task_request.id
                    assert response.result == response_task
                    # Verify the status is COMPLETED
                    args, kwargs = task_manager.update_store.call_args
                    assert args[1].state == TaskState.COMPLETED

    def test_process_error_response(self, task_manager, send_task_request, mock_task):
        """Test _process_error_response method."""
        exception = Exception("Test error")

        # Create a real Task object for the response
        response_task = Task(
            id="test-task-id", sessionId="test-session-id", status=TaskStatus(state=TaskState.FAILED), history=[]
        )

        with patch.object(
            task_manager, '_create_response_parts', return_value=[{"type": "text", "text": "Error: Test error"}]
        ):
            with patch.object(task_manager, 'update_store', return_value=mock_task):
                with patch.object(task_manager, '_append_task_history', return_value=response_task):
                    response = task_manager._process_error_response(send_task_request, exception)

                    task_manager.update_store.assert_called_once()
                    assert response.id == send_task_request.id
                    assert response.result == response_task
                    # Verify the status is FAILED
                    args, kwargs = task_manager.update_store.call_args
                    assert args[1].state == TaskState.FAILED

    def test_get_user_query_valid(self, task_send_params):
        """Test _get_user_query with valid text part."""
        query = CodemieTaskManager._get_user_query(task_send_params)

        assert query == "Hello, this is a test message"

    def test_get_user_query_invalid(self):
        """Test _get_user_query with invalid part type."""
        # Create a message with a non-TextPart (using DataPart which is a valid part type but not a TextPart)
        message = Message(role="user", parts=[DataPart(data={"key": "value"})])

        task_params = TaskSendParams(id="test-task-id", sessionId="test-session-id", message=message)

        with pytest.raises(ValueError, match="Only text parts are supported"):
            CodemieTaskManager._get_user_query(task_params)

    def test_create_response_parts(self, task_manager):
        """Test _create_response_parts method."""
        content = "Test content"
        parts = task_manager._create_response_parts(content)

        assert len(parts) == 1
        assert parts[0]["type"] == "text"
        assert parts[0]["text"] == content
