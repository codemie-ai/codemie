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

from codemie.rest_api.a2a.client.remote_agent_connection import RemoteAgentConnections, merge_metadata
from codemie.rest_api.a2a.types import (
    AgentCard,
    AgentCapabilities,
    AgentProvider,
    Message,
    Task,
    TaskSendParams,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)


@pytest.fixture
def agent_card_streaming():
    """Create a sample AgentCard with streaming enabled."""
    return AgentCard(
        name="Streaming Agent",
        description="An agent with streaming support",
        url="https://streaming-agent.example.com/api",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True, pushNotifications=False),
        provider=AgentProvider(organization="Test Org"),
        skills=[],
    )


@pytest.fixture
def agent_card_non_streaming():
    """Create a sample AgentCard without streaming."""
    return AgentCard(
        name="Non-Streaming Agent",
        description="An agent without streaming support",
        url="https://non-streaming-agent.example.com/api",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
        provider=AgentProvider(organization="Test Org"),
        skills=[],
    )


@pytest.fixture
def sample_task_request():
    """Create a sample TaskSendParams for testing."""
    return TaskSendParams(
        id="task-123",
        sessionId="session-123",
        message=Message(role="user", parts=[TextPart(text="Hello, agent!")]),
    )


@pytest.fixture
def sample_task():
    """Create a sample Task for testing."""
    return Task(
        id="task-123",
        sessionId="session-123",
        status=TaskStatus(
            state=TaskState.COMPLETED,
            message=Message(role="agent", parts=[TextPart(text="Task completed")]),
        ),
    )


class TestRemoteAgentConnectionsInitialization:
    """Tests for RemoteAgentConnections initialization."""

    @patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient')
    def test_init(self, mock_a2a_client, agent_card_streaming):
        """Test initialization of RemoteAgentConnections."""
        # Act
        connection = RemoteAgentConnections(agent_card_streaming)

        # Assert
        assert connection.card == agent_card_streaming
        assert connection.conversation_name is None
        assert connection.conversation is None
        assert isinstance(connection.pending_tasks, set)
        assert len(connection.pending_tasks) == 0
        mock_a2a_client.assert_called_once_with(agent_card_streaming)

    @patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient')
    def test_get_agent(self, mock_a2a_client, agent_card_streaming):
        """Test get_agent method returns agent card."""
        # Arrange
        connection = RemoteAgentConnections(agent_card_streaming)

        # Act
        result = connection.get_agent()

        # Assert
        assert result == agent_card_streaming


class TestRemoteAgentConnectionsSendTaskStreaming:
    """Tests for RemoteAgentConnections.send_task method with streaming."""

    @pytest.mark.asyncio
    async def test_send_task_streaming_with_callback(self, agent_card_streaming, sample_task_request):
        """Test send_task with streaming and callback."""
        # Mock streaming responses
        status_update1 = TaskStatusUpdateEvent(
            id="task-123",
            status=TaskStatus(
                state=TaskState.WORKING,
                message=Message(role="agent", parts=[TextPart(text="Working on it...")]),
            ),
            final=False,
        )

        status_update2 = TaskStatusUpdateEvent(
            id="task-123",
            status=TaskStatus(
                state=TaskState.COMPLETED,
                message=Message(role="agent", parts=[TextPart(text="Done!")]),
            ),
            final=True,
        )

        async def mock_streaming_generator(*args, **kwargs):
            from codemie.rest_api.a2a.types import SendTaskStreamingResponse

            yield SendTaskStreamingResponse(result=status_update1)
            yield SendTaskStreamingResponse(result=status_update2)

        with patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient') as mock_a2a_client:
            mock_client_instance = MagicMock()
            mock_client_instance.send_task_streaming = mock_streaming_generator
            mock_a2a_client.return_value = mock_client_instance

            # Arrange
            connection = RemoteAgentConnections(agent_card_streaming)

            callback_calls = []

            def task_callback(task_arg, agent_card):
                callback_calls.append((task_arg, agent_card))

            # Act
            await connection.send_task(sample_task_request, task_callback)

            # Assert
            # Should call callback 3 times: initial SUBMITTED, then 2 status updates
            assert len(callback_calls) == 3
            assert callback_calls[0][0].status.state == TaskState.SUBMITTED
            assert callback_calls[1][0].status.state == TaskState.WORKING
            assert callback_calls[2][0].status.state == TaskState.COMPLETED

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient')
    async def test_send_task_streaming_without_callback(
        self, mock_a2a_client, agent_card_streaming, sample_task_request
    ):
        """Test send_task with streaming but no callback."""
        # Arrange
        connection = RemoteAgentConnections(agent_card_streaming)

        status_update = TaskStatusUpdateEvent(
            id="task-123",
            status=TaskStatus(
                state=TaskState.COMPLETED,
                message=Message(role="agent", parts=[TextPart(text="Done!")]),
            ),
            final=True,
        )

        async def mock_streaming_generator(*args, **kwargs):
            from codemie.rest_api.a2a.types import SendTaskStreamingResponse

            yield SendTaskStreamingResponse(result=status_update)

        mock_client_instance = MagicMock()
        mock_client_instance.send_task_streaming = mock_streaming_generator
        mock_a2a_client.return_value = mock_client_instance

        # Act
        result = await connection.send_task(sample_task_request, None)

        # Assert
        assert result is None  # Streaming returns None


class TestRemoteAgentConnectionsSendTaskNonStreaming:
    """Tests for RemoteAgentConnections.send_task method without streaming."""

    @pytest.mark.asyncio
    async def test_send_task_non_streaming_success(self, agent_card_non_streaming, sample_task_request, sample_task):
        """Test send_task without streaming returns task successfully."""
        from codemie.rest_api.a2a.types import SendTaskResponse

        mock_response = SendTaskResponse(result=sample_task, error=None)

        with patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient') as mock_a2a_client:
            mock_client_instance = MagicMock()
            mock_client_instance.send_task = AsyncMock(return_value=mock_response)
            mock_a2a_client.return_value = mock_client_instance

            # Arrange
            connection = RemoteAgentConnections(agent_card_non_streaming)

            callback_calls = []

            def task_callback(task_arg, agent_card):
                callback_calls.append((task_arg, agent_card))

            # Act
            result = await connection.send_task(sample_task_request, task_callback)

            # Assert
            assert result is not None
            assert result.id == "task-123"
            assert len(callback_calls) == 1
            assert callback_calls[0][0] == sample_task

    @pytest.mark.asyncio
    async def test_send_task_non_streaming_error(self, agent_card_non_streaming, sample_task_request):
        """Test send_task without streaming handles error response."""
        from codemie.rest_api.a2a.types import JSONRPCError, SendTaskResponse

        mock_error = JSONRPCError(code=-32603, message="Internal error")
        mock_response = SendTaskResponse(result=None, error=mock_error)

        with patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient') as mock_a2a_client:
            mock_client_instance = MagicMock()
            mock_client_instance.send_task = AsyncMock(return_value=mock_response)
            mock_a2a_client.return_value = mock_client_instance

            # Arrange
            connection = RemoteAgentConnections(agent_card_non_streaming)

            callback_calls = []

            def task_callback(task_arg, agent_card):
                callback_calls.append((task_arg, agent_card))

            # Act
            result = await connection.send_task(sample_task_request, task_callback)

            # Assert
            assert result is not None
            assert result.status.state == TaskState.FAILED
            assert "Internal error" in result.status.message.parts[0].text
            assert len(callback_calls) == 1

    @pytest.mark.asyncio
    async def test_send_task_non_streaming_no_result_or_error(self, agent_card_non_streaming, sample_task_request):
        """Test send_task without streaming handles missing result and error."""
        from codemie.rest_api.a2a.types import SendTaskResponse

        mock_response = SendTaskResponse(result=None, error=None)

        with patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient') as mock_a2a_client:
            mock_client_instance = MagicMock()
            mock_client_instance.send_task = AsyncMock(return_value=mock_response)
            mock_a2a_client.return_value = mock_client_instance

            # Arrange
            connection = RemoteAgentConnections(agent_card_non_streaming)

            # Act
            result = await connection.send_task(sample_task_request, None)

            # Assert
            assert result is None


class TestProcessTaskResponse:
    """Tests for RemoteAgentConnections._process_task_response method."""

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient')
    async def test_process_task_response_with_status_message(self, mock_a2a_client, agent_card_streaming):
        """Test _process_task_response propagates metadata to status message."""
        # Arrange
        connection = RemoteAgentConnections(agent_card_streaming)

        request = TaskSendParams(
            id="task-123",
            sessionId="session-123",
            message=Message(
                role="user",
                parts=[TextPart(text="Hello")],
                metadata={"request_id": "req-123"},
            ),
        )

        status_update = TaskStatusUpdateEvent(
            id="task-123",
            status=TaskStatus(
                state=TaskState.WORKING,
                message=Message(role="agent", parts=[TextPart(text="Working...")]),
            ),
            metadata={"update_id": "upd-123"},
        )

        from codemie.rest_api.a2a.types import SendTaskStreamingResponse

        response = SendTaskStreamingResponse(result=status_update)

        callback_calls = []

        def task_callback(task_arg, agent_card):
            callback_calls.append((task_arg, agent_card))

        # Act
        await connection._process_task_response(request, response, task_callback)

        # Assert
        assert len(callback_calls) == 1
        # Check that message_id was added to metadata
        assert 'message_id' in response.result.status.message.metadata

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient')
    async def test_process_task_response_without_callback(self, mock_a2a_client, agent_card_streaming):
        """Test _process_task_response without callback doesn't fail."""
        # Arrange
        connection = RemoteAgentConnections(agent_card_streaming)

        request = TaskSendParams(
            id="task-123",
            sessionId="session-123",
            message=Message(role="user", parts=[TextPart(text="Hello")]),
        )

        status_update = TaskStatusUpdateEvent(
            id="task-123",
            status=TaskStatus(
                state=TaskState.WORKING,
                message=Message(role="agent", parts=[TextPart(text="Working...")]),
            ),
        )

        from codemie.rest_api.a2a.types import SendTaskStreamingResponse

        response = SendTaskStreamingResponse(result=status_update)

        # Act
        await connection._process_task_response(request, response, None)

        # Assert - Should not raise exception


class TestProcessErrorResponse:
    """Tests for RemoteAgentConnections._process_error_response method."""

    @patch('codemie.rest_api.a2a.client.remote_agent_connection.A2AClient')
    def test_process_error_response_with_callback(self, mock_a2a_client, agent_card_non_streaming):
        """Test _process_error_response creates failed task."""
        # Arrange
        connection = RemoteAgentConnections(agent_card_non_streaming)

        request = TaskSendParams(
            id="task-123",
            sessionId="session-123",
            message=Message(role="user", parts=[TextPart(text="Hello")]),
        )

        from codemie.rest_api.a2a.types import JSONRPCError, SendTaskResponse

        mock_error = JSONRPCError(code=-32603, message="Internal error occurred")
        response = SendTaskResponse(result=None, error=mock_error)

        callback_calls = []

        def task_callback(task_arg, agent_card):
            callback_calls.append((task_arg, agent_card))

        # Act
        result = connection._process_error_response(request, response, task_callback)

        # Assert
        assert result.status.state == TaskState.FAILED
        assert "Internal error occurred" in result.status.message.parts[0].text
        assert len(callback_calls) == 1


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
