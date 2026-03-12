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

"""Unit tests for the A2AClient class."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from codemie.rest_api.a2a.client.client import A2AClient
from codemie.rest_api.a2a.types import (
    A2AClientHTTPError,
    A2AClientJSONError,
    AgentCard,
    AgentCapabilities,
    AgentProvider,
    CancelTaskResponse,
    GetTaskResponse,
    Message,
    SendTaskResponse,
    SendTaskStreamingResponse,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)


@pytest.fixture
def agent_card():
    """Create a sample AgentCard for testing."""
    return AgentCard(
        name="Test Agent",
        description="A test agent",
        url="https://test-agent.example.com/api/agent",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        provider=AgentProvider(organization="Test Org"),
        skills=[],
    )


@pytest.fixture
def sample_message():
    """Create a sample Message for testing."""
    return Message(role="user", parts=[TextPart(text="Hello, agent!")])


@pytest.fixture
def sample_task():
    """Create a sample Task for testing."""
    from datetime import datetime

    return Task(
        id="task-123",
        sessionId="session-123",
        status=TaskStatus(
            state=TaskState.COMPLETED,
            message=Message(role="agent", parts=[TextPart(text="Task completed")]),
        ),
        date=datetime.now(),
        update_date=datetime.now(),
    )


class TestA2AClientInitialization:
    """Tests for A2AClient initialization."""

    def test_init_with_agent_card(self, agent_card):
        """Test initialization with agent card."""
        # Act
        client = A2AClient(agent_card=agent_card)

        # Assert
        assert client.url == "https://test-agent.example.com/api/agent"
        assert client.agent_card == agent_card
        assert client.user_id is None
        assert client.project_name is None
        assert client.integration_id is None

    def test_init_with_url(self):
        """Test initialization with URL."""
        # Act
        client = A2AClient(url="https://custom-agent.example.com/api")

        # Assert
        assert client.url == "https://custom-agent.example.com/api"
        assert client.agent_card is None

    def test_init_with_all_params(self, agent_card):
        """Test initialization with all parameters."""
        # Act
        client = A2AClient(
            agent_card=agent_card,
            user_id="user-123",
            project_name="test-project",
            integration_id="integration-123",
        )

        # Assert
        assert client.url == "https://test-agent.example.com/api/agent"
        assert client.user_id == "user-123"
        assert client.project_name == "test-project"
        assert client.integration_id == "integration-123"

    def test_init_without_agent_card_or_url(self):
        """Test initialization fails without agent card or URL."""
        # Act & Assert
        with pytest.raises(ValueError, match="Must provide either agent_card or url"):
            A2AClient()


class TestA2AClientGetHeader:
    """Tests for A2AClient._get_header method."""

    @patch('codemie.rest_api.a2a.client.client.SettingsService')
    @patch('codemie.rest_api.a2a.client.client.get_auth_header')
    def test_get_header_success(self, mock_get_auth_header, mock_settings_service, agent_card):
        """Test _get_header retrieves credentials and generates header."""
        # Arrange
        client = A2AClient(
            agent_card=agent_card, user_id="user-123", project_name="test-project", integration_id="integration-123"
        )
        mock_creds = {"auth_type": "bearer", "auth_value": "test_token"}
        mock_settings_service.get_a2a_creds.return_value = mock_creds
        mock_get_auth_header.return_value = {"Authorization": "Bearer test_token"}

        # Act
        result = client._get_header()

        # Assert
        assert result == {"Authorization": "Bearer test_token"}
        mock_settings_service.get_a2a_creds.assert_called_once_with(
            user_id="user-123", project_name="test-project", integration_id="integration-123"
        )
        mock_get_auth_header.assert_called_once_with(mock_creds, 'POST', None, None)

    @patch('codemie.rest_api.a2a.client.client.SettingsService')
    @patch('codemie.rest_api.a2a.client.client.logger')
    def test_get_header_failure(self, mock_logger, mock_settings_service, agent_card):
        """Test _get_header returns empty dict on failure."""
        # Arrange
        client = A2AClient(agent_card=agent_card, user_id="user-123", project_name="test-project")
        mock_settings_service.get_a2a_creds.side_effect = Exception("Credentials not found")

        # Act
        result = client._get_header()

        # Assert
        assert result == {}
        mock_logger.error.assert_called_once()

    @patch('codemie.rest_api.a2a.client.client.SettingsService.get_a2a_creds')
    def test_get_header_aws_signature(self, mock_get_a2a_creds):
        """Test _get_header generates header for aws_signature."""
        mock_get_a2a_creds.return_value = {
            'auth_type': 'aws_signature',
            'aws_access_key_id': '***',
            'aws_region': 'eu-central-1',
            'aws_secret_access_key': '***',
            'aws_service_name': 'bedrock-agentcore',
        }

        client = A2AClient(
            agent_card=AgentCard(
                name="Test Agent",
                description="A test agent",
                url="https://bedrock-agentcore.test.com",
                version="1.0.0",
                capabilities=AgentCapabilities(streaming=True),
                provider=AgentProvider(organization="Test Org"),
                skills=[],
                user_id="user-123",
                project_name="test-project",
                integration_id="integration-123",
            )
        )

        headers = client._get_header(method="GET", url=client.url, body=None)

        assert 'Authorization' in headers
        assert 'Content-Type' in headers
        assert 'X-Amz-Date' in headers
        assert headers['Authorization'].startswith('AWS4-HMAC-SHA256 Credential=')


class TestA2AClientSendTask:
    """Tests for A2AClient.send_task method."""

    @pytest.mark.asyncio
    async def test_send_task_success(self, agent_card, sample_message, sample_task):
        """Test successful send_task request."""
        # Arrange
        client = A2AClient(agent_card=agent_card)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "request-123",
            "result": sample_task.model_dump(),
        }
        mock_response.raise_for_status = MagicMock()

        with patch('codemie.rest_api.a2a.client.client.httpx.AsyncClient') as mock_async_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_async_client.return_value.__aenter__.return_value = mock_client_instance

            payload = {
                "id": "task-123",
                "sessionId": "session-123",
                "message": sample_message.model_dump(),
            }

            # Act
            result = await client.send_task(payload)

            # Assert
            assert isinstance(result, SendTaskResponse)
            assert result.result.id == "task-123"
            mock_client_instance.post.assert_called_once()

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.client.httpx.AsyncClient')
    async def test_send_task_http_error(self, mock_async_client, agent_card):
        """Test send_task raises A2AClientHTTPError on HTTP error."""
        # Arrange
        client = A2AClient(agent_card=agent_card)

        mock_response = MagicMock()
        mock_response.status_code = 500
        http_error = httpx.HTTPStatusError("Server error", request=MagicMock(), response=mock_response)

        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(side_effect=http_error)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        payload = {
            "id": "task-123",
            "sessionId": "session-123",
            "message": {"role": "user", "parts": [{"type": "text", "text": "Hello"}]},
        }

        # Act & Assert
        with pytest.raises(A2AClientHTTPError) as exc_info:
            await client.send_task(payload)

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.client.httpx.AsyncClient')
    async def test_send_task_json_decode_error(self, mock_async_client, agent_card):
        """Test send_task raises A2AClientJSONError on JSON decode error."""
        # Arrange
        client = A2AClient(agent_card=agent_card)

        mock_response = MagicMock()
        mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        payload = {
            "id": "task-123",
            "sessionId": "session-123",
            "message": {"role": "user", "parts": [{"type": "text", "text": "Hello"}]},
        }

        # Act & Assert
        with pytest.raises(A2AClientJSONError):
            await client.send_task(payload)


class TestA2AClientSendTaskStreaming:
    """Tests for A2AClient.send_task_streaming method."""

    @pytest.mark.asyncio
    async def test_send_task_streaming_success(self, agent_card):
        """Test successful send_task_streaming request."""
        # Arrange
        client = A2AClient(agent_card=agent_card)

        # Mock SSE events
        mock_event1 = MagicMock()
        mock_event1.data = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "request-123",
                "result": {
                    "id": "task-123",
                    "status": {
                        "state": "working",
                        "message": {"role": "agent", "parts": [{"type": "text", "text": "Working..."}]},
                    },
                    "final": False,
                },
            }
        )

        mock_event2 = MagicMock()
        mock_event2.data = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "request-123",
                "result": {
                    "id": "task-123",
                    "status": {
                        "state": "completed",
                        "message": {"role": "agent", "parts": [{"type": "text", "text": "Done"}]},
                    },
                    "final": True,
                },
            }
        )

        # Patch httpx_sse.connect_sse
        with (
            patch('codemie.rest_api.a2a.client.client.connect_sse') as mock_connect_sse,
            patch('codemie.rest_api.a2a.client.client.httpx.Client') as mock_http_client,
        ):
            mock_event_source = MagicMock()
            mock_event_source.iter_sse.return_value = iter([mock_event1, mock_event2])
            mock_connect_sse.return_value.__enter__.return_value = mock_event_source
            mock_http_client.return_value.__enter__.return_value = MagicMock()

            payload = {
                "id": "task-123",
                "sessionId": "session-123",
                "message": {"role": "user", "parts": [{"type": "text", "text": "Hello"}]},
            }

            # Act
            results = []
            async for response in client.send_task_streaming(payload):
                results.append(response)

            # Assert
            assert len(results) == 2
            assert all(isinstance(r, SendTaskStreamingResponse) for r in results)

    @pytest.mark.asyncio
    async def test_send_task_streaming_json_error(self, agent_card):
        """Test send_task_streaming raises A2AClientJSONError on invalid JSON."""
        # Arrange
        client = A2AClient(agent_card=agent_card)

        mock_event = MagicMock()
        mock_event.data = "invalid json"

        with (
            patch('codemie.rest_api.a2a.client.client.connect_sse') as mock_connect_sse,
            patch('codemie.rest_api.a2a.client.client.httpx.Client'),
        ):
            mock_event_source = MagicMock()
            mock_event_source.iter_sse.return_value = iter([mock_event])
            mock_connect_sse.return_value.__enter__.return_value = mock_event_source

            payload = {
                "id": "task-123",
                "sessionId": "session-123",
                "message": {"role": "user", "parts": [{"type": "text", "text": "Hello"}]},
            }

            # Act & Assert
            with pytest.raises(A2AClientJSONError):
                async for _ in client.send_task_streaming(payload):
                    pass


class TestA2AClientGetTask:
    """Tests for A2AClient.get_task method."""

    @pytest.mark.asyncio
    async def test_get_task_success(self, agent_card, sample_task):
        """Test successful get_task request."""
        # Arrange
        client = A2AClient(agent_card=agent_card)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "request-123",
            "result": sample_task.model_dump(),
        }
        mock_response.raise_for_status = MagicMock()

        with patch('codemie.rest_api.a2a.client.client.httpx.AsyncClient') as mock_async_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_async_client.return_value.__aenter__.return_value = mock_client_instance

            payload = {"id": "task-123"}

            # Act
            result = await client.get_task(payload)

            # Assert
            assert isinstance(result, GetTaskResponse)
            assert result.result.id == "task-123"


class TestA2AClientCancelTask:
    """Tests for A2AClient.cancel_task method."""

    @pytest.mark.asyncio
    async def test_cancel_task_success(self, agent_card, sample_task):
        """Test successful cancel_task request."""
        # Arrange
        client = A2AClient(agent_card=agent_card)
        sample_task.status.state = TaskState.CANCELED

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "request-123",
            "result": sample_task.model_dump(),
        }
        mock_response.raise_for_status = MagicMock()

        with patch('codemie.rest_api.a2a.client.client.httpx.AsyncClient') as mock_async_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_async_client.return_value.__aenter__.return_value = mock_client_instance

            payload = {"id": "task-123"}

            # Act
            result = await client.cancel_task(payload)

            # Assert
            assert isinstance(result, CancelTaskResponse)
            assert result.result.status.state == TaskState.CANCELED


class TestA2AClientCallbacks:
    """Tests for A2AClient task callback methods."""

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.client.httpx.AsyncClient')
    async def test_set_task_callback_success(self, mock_async_client, agent_card):
        """Test successful set_task_callback request."""
        # Arrange
        client = A2AClient(agent_card=agent_card)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "request-123",
            "result": {
                "id": "task-123",
                "pushNotificationConfig": {
                    "url": "https://callback.example.com/webhook",
                    "token": "callback_token",
                },
            },
        }
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        payload = {
            "id": "task-123",
            "pushNotificationConfig": {
                "url": "https://callback.example.com/webhook",
                "token": "callback_token",
            },
        }

        # Act
        result = await client.set_task_callback(payload)

        # Assert
        assert result.result is not None
        assert result.result.id == "task-123"

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.client.httpx.AsyncClient')
    async def test_get_task_callback_success(self, mock_async_client, agent_card):
        """Test successful get_task_callback request."""
        # Arrange
        client = A2AClient(agent_card=agent_card)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "request-123",
            "result": {
                "id": "task-123",
                "pushNotificationConfig": {
                    "url": "https://callback.example.com/webhook",
                },
            },
        }
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        payload = {"id": "task-123"}

        # Act
        result = await client.get_task_callback(payload)

        # Assert
        assert result.result is not None
        assert result.result.id == "task-123"
