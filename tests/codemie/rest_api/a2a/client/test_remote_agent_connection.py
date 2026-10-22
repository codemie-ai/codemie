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

"""Unit tests for the RemoteAgentConnections class."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.a2a.client.remote_agent_connection import (
    RemoteAgentConnections,
    is_final_update,
    merge_metadata,
)
from codemie.rest_api.a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    Artifact,
    JSONRPCError,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    SendMessageRPCResponse,
    StreamResponse,
    StreamRPCResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


def _card(streaming):
    return AgentCard(
        name="Agent",
        description="An agent",
        supportedInterfaces=[AgentInterface(url="https://agent.example.com/api", protocolBinding="JSONRPC")],
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=streaming, pushNotifications=False),
        provider=AgentProvider(organization="Test Org"),
        skills=[],
    )


@pytest.fixture
def agent_card_streaming():
    return _card(True)


@pytest.fixture
def agent_card_non_streaming():
    return _card(False)


@pytest.fixture
def sample_request():
    return SendMessageRequest(
        message=Message(role=Role.USER, parts=[Part(text="Hello, agent!")], contextId="session-123"),
    )


@pytest.fixture
def sample_task():
    return Task(
        id="task-123",
        contextId="session-123",
        status=TaskStatus(
            state=TaskState.COMPLETED,
            message=Message(role=Role.AGENT, parts=[Part(text="Task completed")]),
        ),
    )


def _status(state, text=None, task_id="task-123"):
    message = Message(role=Role.AGENT, parts=[Part(text=text)]) if text else None
    return TaskStatusUpdateEvent(
        taskId=task_id, contextId="session-123", status=TaskStatus(state=state, message=message)
    )


def _stream(*payloads):
    """An async generator factory yielding StreamRPCResponse frames wrapping the given payloads."""

    async def generator(*args, **kwargs):
        for payload in payloads:
            key = {
                Task: "task",
                Message: "message",
                TaskStatusUpdateEvent: "statusUpdate",
                TaskArtifactUpdateEvent: "artifactUpdate",
            }[type(payload)]
            yield StreamRPCResponse(id="r", result=StreamResponse(**{key: payload}))

    return generator


def _connection(card, **client_attrs):
    with patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient') as mock_a2a_client:
        client = MagicMock()
        for name, value in client_attrs.items():
            setattr(client, name, value)
        mock_a2a_client.return_value = client
        return RemoteAgentConnections(card), client


class TestIsFinalUpdate:
    @pytest.mark.parametrize(
        "state,expected",
        [
            (TaskState.SUBMITTED, False),
            (TaskState.WORKING, False),
            (TaskState.COMPLETED, True),
            (TaskState.FAILED, True),
            (TaskState.CANCELED, True),
            (TaskState.REJECTED, True),
            (TaskState.INPUT_REQUIRED, True),
            (TaskState.AUTH_REQUIRED, True),
        ],
    )
    def test_status_update_and_task_by_state(self, state, expected):
        assert is_final_update(_status(state)) is expected
        assert is_final_update(Task(id="t", status=TaskStatus(state=state))) is expected

    def test_message_is_final_and_artifact_update_is_not(self):
        assert is_final_update(Message(role=Role.AGENT, parts=[Part(text="x")])) is True
        event = TaskArtifactUpdateEvent(taskId="t", contextId="c", artifact=Artifact(parts=[Part(text="x")]))
        assert is_final_update(event) is False


class TestRemoteAgentConnectionsInitialization:
    @patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient')
    def test_init(self, mock_a2a_client, agent_card_streaming):
        connection = RemoteAgentConnections(agent_card_streaming)

        assert connection.card == agent_card_streaming
        assert connection.conversation_name is None
        assert connection.conversation is None
        assert isinstance(connection.pending_tasks, set) and not connection.pending_tasks
        mock_a2a_client.assert_called_once_with(agent_card_streaming)

    @patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient')
    def test_get_agent(self, mock_a2a_client, agent_card_streaming):
        assert RemoteAgentConnections(agent_card_streaming).get_agent() == agent_card_streaming


class TestSendTaskStreaming:
    @pytest.mark.asyncio
    async def test_callback_gets_submitted_then_updates_and_stops_on_terminal_without_final_flag(
        self, agent_card_streaming, sample_request
    ):
        trailing = _status(TaskState.COMPLETED, "must not be reached", task_id="after")
        connection, _ = _connection(
            agent_card_streaming,
            send_task_streaming=_stream(
                _status(TaskState.WORKING, "Working on it..."), _status(TaskState.COMPLETED, "Done!"), trailing
            ),
        )
        calls = []

        result = await connection.send_task(sample_request, lambda arg, card: calls.append(arg))

        assert result is None
        assert [c.status.state for c in calls] == [TaskState.SUBMITTED, TaskState.WORKING, TaskState.COMPLETED]
        assert calls[0].contextId == "session-123" and calls[0].status.message == sample_request.message
        assert all(getattr(c, "taskId", None) != "after" for c in calls)

    @pytest.mark.asyncio
    async def test_stream_stops_on_input_required(self, agent_card_streaming, sample_request):
        connection, _ = _connection(
            agent_card_streaming,
            send_task_streaming=_stream(
                _status(TaskState.INPUT_REQUIRED, "Which city?"), _status(TaskState.COMPLETED, "late")
            ),
        )
        calls = []
        await connection.send_task(sample_request, lambda arg, card: calls.append(arg))
        assert [c.status.state for c in calls] == [TaskState.SUBMITTED, TaskState.INPUT_REQUIRED]

    @pytest.mark.asyncio
    async def test_artifact_update_does_not_end_stream(self, agent_card_streaming, sample_request):
        artifact_event = TaskArtifactUpdateEvent(
            taskId="task-123", contextId="session-123", artifact=Artifact(parts=[Part(text="answer")])
        )
        connection, _ = _connection(
            agent_card_streaming,
            send_task_streaming=_stream(artifact_event, _status(TaskState.COMPLETED)),
        )
        calls = []
        await connection.send_task(sample_request, lambda arg, card: calls.append(arg))
        assert isinstance(calls[1], TaskArtifactUpdateEvent) and calls[2].status.state == TaskState.COMPLETED

    @pytest.mark.asyncio
    async def test_message_result_is_delivered_and_ends_stream(self, agent_card_streaming, sample_request):
        message = Message(role=Role.AGENT, parts=[Part(text="direct answer")])
        connection, _ = _connection(
            agent_card_streaming, send_task_streaming=_stream(message, _status(TaskState.COMPLETED, "late"))
        )
        calls = []
        await connection.send_task(sample_request, lambda arg, card: calls.append(arg))
        assert calls[1] is message and len(calls) == 2

    @pytest.mark.asyncio
    async def test_task_snapshot_frame_is_delivered(self, agent_card_streaming, sample_request):
        snapshot = Task(id="task-123", contextId="session-123", status=TaskStatus(state=TaskState.WORKING))
        connection, _ = _connection(
            agent_card_streaming, send_task_streaming=_stream(snapshot, _status(TaskState.COMPLETED))
        )
        calls = []
        await connection.send_task(sample_request, lambda arg, card: calls.append(arg))
        assert calls[1] is snapshot and len(calls) == 3

    @pytest.mark.asyncio
    async def test_error_frame_becomes_failed_task_and_ends_stream(self, agent_card_streaming, sample_request):
        async def stream(*args, **kwargs):
            yield StreamRPCResponse(id="r", error=JSONRPCError(code=-32001, message="Task not found"))
            yield StreamRPCResponse(id="r", result=StreamResponse(statusUpdate=_status(TaskState.COMPLETED)))

        connection, _ = _connection(agent_card_streaming, send_task_streaming=stream)
        calls = []
        await connection.send_task(sample_request, lambda arg, card: calls.append(arg))
        assert len(calls) == 2
        assert calls[1].status.state == TaskState.FAILED
        assert "Task not found" in calls[1].status.message.parts[0].text

    @pytest.mark.asyncio
    async def test_without_callback(self, agent_card_streaming, sample_request):
        connection, _ = _connection(
            agent_card_streaming, send_task_streaming=_stream(_status(TaskState.COMPLETED, "Done!"))
        )
        assert await connection.send_task(sample_request, None) is None

    @pytest.mark.asyncio
    async def test_streaming_payload_is_the_v1_wire_dict(self, agent_card_streaming, sample_request):
        captured = {}

        async def stream(payload):
            captured.update(payload)
            return
            yield  # pragma: no cover

        connection, _ = _connection(agent_card_streaming, send_task_streaming=stream)
        await connection.send_task(sample_request, None)

        assert captured["message"]["role"] == "ROLE_USER" and captured["message"]["contextId"] == "session-123"
        assert "taskId" not in captured["message"] and "id" not in captured
        assert SendMessageRequest.model_validate(captured).message.parts[0].text == "Hello, agent!"


class TestSendTaskNonStreaming:
    @pytest.mark.asyncio
    async def test_task_result_is_returned_and_delivered(self, agent_card_non_streaming, sample_request, sample_task):
        response = SendMessageRPCResponse(id="r", result=SendMessageResponse(task=sample_task))
        connection, _ = _connection(agent_card_non_streaming, send_task=AsyncMock(return_value=response))
        calls = []

        result = await connection.send_task(sample_request, lambda arg, card: calls.append(arg))

        assert result.id == "task-123"
        assert calls == [sample_task]

    @pytest.mark.asyncio
    async def test_message_result_is_returned_and_delivered(self, agent_card_non_streaming, sample_request):
        message = Message(role=Role.AGENT, parts=[Part(text="direct")])
        response = SendMessageRPCResponse(id="r", result=SendMessageResponse(message=message))
        connection, _ = _connection(agent_card_non_streaming, send_task=AsyncMock(return_value=response))
        calls = []

        result = await connection.send_task(sample_request, lambda arg, card: calls.append(arg))

        assert result is message and calls == [message]

    @pytest.mark.asyncio
    async def test_error_response_becomes_failed_task(self, agent_card_non_streaming, sample_request):
        response = SendMessageRPCResponse(id="r", error=JSONRPCError(code=-32603, message="Internal error"))
        connection, _ = _connection(agent_card_non_streaming, send_task=AsyncMock(return_value=response))
        calls = []

        result = await connection.send_task(sample_request, lambda arg, card: calls.append(arg))

        assert result.status.state == TaskState.FAILED
        assert "Internal error" in result.status.message.parts[0].text
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_no_result_or_error_returns_none(self, agent_card_non_streaming, sample_request):
        response = SendMessageRPCResponse(id="r")
        connection, _ = _connection(agent_card_non_streaming, send_task=AsyncMock(return_value=response))
        assert await connection.send_task(sample_request, None) is None

    @pytest.mark.asyncio
    async def test_payload_is_the_v1_wire_dict(self, agent_card_non_streaming, sample_request):
        send = AsyncMock(return_value=SendMessageRPCResponse(id="r"))
        connection, _ = _connection(agent_card_non_streaming, send_task=send)
        await connection.send_task(sample_request, None)
        assert send.call_args.args[0]["message"]["role"] == "ROLE_USER"


class TestProcessing:
    def test_status_message_gets_metadata_and_message_id(self, agent_card_streaming):
        connection, _ = _connection(agent_card_streaming)
        request = SendMessageRequest(
            message=Message(role=Role.USER, parts=[Part(text="Hello")]), metadata={"request_id": "req-123"}
        )
        update = _status(TaskState.WORKING, "Working...")
        update.metadata = {"update_id": "upd-123"}
        calls = []

        connection._process_result(request, update, lambda arg, card: calls.append(arg))

        assert calls == [update]
        assert update.metadata == {"update_id": "upd-123", "request_id": "req-123"}
        assert 'message_id' in update.status.message.metadata

    def test_task_results_are_processed_without_touching_orm_metadata(self, agent_card_streaming, sample_task):
        connection, _ = _connection(agent_card_streaming)
        request = SendMessageRequest(message=Message(role=Role.USER, parts=[Part(text="Hi")]), metadata={"a": 1})
        connection._process_result(request, sample_task, None)  # must not raise

    def test_process_error_response_creates_failed_task(self, agent_card_non_streaming, sample_request):
        connection, _ = _connection(agent_card_non_streaming)
        response = SendMessageRPCResponse(id="r", error=JSONRPCError(code=-32603, message="Internal error occurred"))
        calls = []

        result = connection._process_error_response(sample_request, response, lambda arg, card: calls.append(arg))

        assert result.status.state == TaskState.FAILED
        assert result.status.message.parts[0].text == "Internal error occurred"
        assert result.contextId == "session-123" and len(calls) == 1


class TestMergeMetadata:
    """Tests for the merge_metadata utility function."""

    def test_merge_metadata_both_have_metadata(self):
        """Test merging metadata when both target and source have metadata."""
        # Arrange
        target = MagicMock()
        target.metadata = {"key1": "value1", "key2": "value2"}
        source = MagicMock()
        source.metadata = {"key2": "updated_value2", "key3": "value3"}

        # Act
        merge_metadata(target, source)

        # Assert
        assert target.metadata == {"key1": "value1", "key2": "updated_value2", "key3": "value3"}

    def test_merge_metadata_target_has_no_metadata(self):
        """Test merging metadata when target has no metadata."""
        # Arrange
        target = MagicMock()
        target.metadata = None
        source = MagicMock()
        source.metadata = {"key1": "value1"}

        # Act
        merge_metadata(target, source)

        # Assert
        assert target.metadata == {"key1": "value1"}

    def test_merge_metadata_source_has_no_metadata(self):
        """Test merging metadata when source has no metadata."""
        # Arrange
        target = MagicMock()
        target.metadata = {"key1": "value1"}
        source = MagicMock()
        source.metadata = None

        # Act
        merge_metadata(target, source)

        # Assert
        assert target.metadata == {"key1": "value1"}

    def test_merge_metadata_both_have_no_metadata(self):
        """Test merging metadata when both have no metadata."""
        # Arrange
        target = MagicMock()
        target.metadata = None
        source = MagicMock()
        source.metadata = None

        # Act
        merge_metadata(target, source)

        # Assert
        assert target.metadata is None

    def test_merge_metadata_no_metadata_attribute(self):
        """Test merging metadata when objects don't have metadata attribute."""
        # Arrange
        target = MagicMock(spec=[])  # No attributes
        source = MagicMock(spec=[])

        # Act
        merge_metadata(target, source)

        # Assert - Should not raise exception

    def test_merge_metadata_empty_dicts(self):
        """Test merging metadata with empty dictionaries."""
        # Arrange
        target = MagicMock()
        target.metadata = {}
        source = MagicMock()
        source.metadata = {}

        # Act
        merge_metadata(target, source)

        # Assert
        assert target.metadata == {}


@pytest.mark.asyncio
async def test_empty_stream_frame_is_skipped(agent_card_streaming, sample_request):
    async def stream(*args, **kwargs):
        yield StreamRPCResponse(id="r")
        yield StreamRPCResponse(id="r", result=StreamResponse(statusUpdate=_status(TaskState.COMPLETED)))

    connection, _ = _connection(agent_card_streaming, send_task_streaming=stream)
    calls = []
    await connection.send_task(sample_request, lambda arg, card: calls.append(arg))
    assert [c.status.state for c in calls] == [TaskState.SUBMITTED, TaskState.COMPLETED]
