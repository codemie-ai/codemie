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

"""Unit tests for the A2ACardResolver class."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from codemie.rest_api.a2a.client.card_resolver import A2ACardResolver
from codemie.rest_api.a2a.types import AgentCard


@pytest.fixture
def card_resolver():
    """Create an A2ACardResolver instance for testing."""
    return A2ACardResolver()


@pytest.fixture
def sample_agent_card_data():
    """Create sample agent card data for testing."""
    return {
        "name": "Test Agent",
        "description": "A test agent for unit tests",
        "supportedInterfaces": [
            {"url": "https://test-agent.example.com/api/agent", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "version": "1.0.0",
        "capabilities": {"streaming": True, "pushNotifications": False},
        "provider": {"organization": "Test Org", "url": "https://test-org.example.com"},
        "skills": [
            {
                "id": "test_skill",
                "name": "Test Skill",
                "description": "A test skill",
                "tags": ["test"],
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain"],
            }
        ],
    }


def _mock_card_response(mock_async_client, card_data):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = card_data
    response.text = "response text"
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    mock_async_client.return_value.__aenter__.return_value = client


class TestA2ACardResolverInitialization:
    """Tests for A2ACardResolver initialization."""

    def test_init(self, card_resolver):
        """Test basic initialization."""
        # Assert
        assert isinstance(card_resolver, A2ACardResolver)
        assert not hasattr(card_resolver, "bedrock_agentcore")


class TestNormalizeUrl:
    """Tests for A2ACardResolver.normalize_url method."""

    def test_normalize_url_with_trailing_slash(self, card_resolver):
        """Test normalizing URL with trailing slash."""
        # Arrange
        url = "https://example.com/api/"

        # Act
        result = card_resolver.normalize_url(url)

        # Assert
        assert result == "https://example.com/api"

    def test_normalize_url_without_trailing_slash(self, card_resolver):
        """Test normalizing URL without trailing slash."""
        # Arrange
        url = "https://example.com/api"

        # Act
        result = card_resolver.normalize_url(url)

        # Assert
        assert result == "https://example.com/api"

    def test_normalize_url_multiple_trailing_slashes(self, card_resolver):
        """Test normalizing URL with multiple trailing slashes."""
        # Arrange
        url = "https://example.com/api///"

        # Act
        result = card_resolver.normalize_url(url)

        # Assert
        # Only removes one trailing slash at a time
        assert result == "https://example.com/api//"

    def test_normalize_url_root_path(self, card_resolver):
        """Test normalizing root URL with trailing slash."""
        # Arrange
        url = "https://example.com/"

        # Act
        result = card_resolver.normalize_url(url)

        # Assert
        assert result == "https://example.com"


class TestBuildAgentJsonUrl:
    """Tests for A2ACardResolver.build_agent_json_url method."""

    def test_build_agent_json_url_simple_url(self, card_resolver):
        """Test building agent-card.json URL from simple base URL."""
        # Arrange
        base_url = "https://example.com/api"

        # Act
        result = card_resolver.build_agent_json_url(base_url)

        # Assert
        assert result == "https://example.com/api/.well-known/agent-card.json"

    def test_build_agent_json_url_with_trailing_slash(self, card_resolver):
        """Test building agent-card.json URL from URL with trailing slash."""
        # Arrange
        base_url = "https://example.com/api/"

        # Act
        result = card_resolver.build_agent_json_url(base_url)

        # Assert
        assert result == "https://example.com/api/.well-known/agent-card.json"

    def test_build_agent_json_url_already_complete(self, card_resolver):
        """Test building agent-card.json URL when already complete."""
        # Arrange
        base_url = "https://example.com/api/.well-known/agent-card.json"

        # Act
        result = card_resolver.build_agent_json_url(base_url)

        # Assert
        assert result == "https://example.com/api/.well-known/agent-card.json"

    def test_build_agent_json_url_root_domain(self, card_resolver):
        """Test building agent-card.json URL from root domain."""
        # Arrange
        base_url = "https://example.com"

        # Act
        result = card_resolver.build_agent_json_url(base_url)

        # Assert
        assert result == "https://example.com/.well-known/agent-card.json"


class TestBedrockAgentCore:
    def test_agentcore_url_is_built_like_any_other(self, card_resolver):
        url = card_resolver.build_agent_json_url("https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/x")
        assert url == "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/x/.well-known/agent-card.json"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("url", "expected"),
        [("https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/x", True), ("https://agent.example.com", False)],
    )
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_agentcore_host_only_tags_the_card(
        self, mock_async_client, url, expected, card_resolver, sample_agent_card_data
    ):
        _mock_card_response(mock_async_client, sample_agent_card_data)

        success, card, error = await card_resolver.fetch_agent_card(url)

        assert success is True, error
        assert card.bedrock_agentcore is expected


class TestFetchAgentCard:
    """Tests for A2ACardResolver.fetch_agent_card method."""

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_success(self, mock_async_client, card_resolver, sample_agent_card_data):
        """Test successful agent card fetching."""
        # Arrange
        url = "https://test-agent.example.com/api"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_agent_card_data
        mock_response.text = "response text"

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        # Act
        success, agent_card, error_message = await card_resolver.fetch_agent_card(url)

        # Assert
        assert success is True
        assert isinstance(agent_card, AgentCard)
        assert agent_card.name == "Test Agent"
        assert agent_card.version == "1.0.0"
        assert error_message == ""
        mock_client_instance.get.assert_called_once()

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_rejects_legacy_url_card_without_version(
        self, mock_async_client, card_resolver, sample_agent_card_data
    ):
        """A pre-v1.0 card (top-level `url`, no version) is refused at registration."""
        legacy = {k: v for k, v in sample_agent_card_data.items() if k != "supportedInterfaces"}
        legacy["url"] = "https://legacy-agent.example.com/rpc"
        _mock_card_response(mock_async_client, legacy)

        success, agent_card, error_message = await card_resolver.fetch_agent_card("https://legacy-agent.example.com")

        assert success is False and agent_card is None
        assert "does not support A2A protocol 1.0" in error_message and "0.3.0" in error_message

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_accepts_sdk_1x_card_with_legacy_compat_version(
        self, mock_async_client, card_resolver, sample_agent_card_data
    ):
        """a2a-sdk 1.x serves versionless interfaces next to v0.3 compat fields (protocolVersion, url, transport)."""
        data = {**sample_agent_card_data}
        data["supportedInterfaces"] = [{"url": "https://sdk.example.com/", "protocolBinding": "JSONRPC"}]
        data.update(protocolVersion="0.3", url="https://sdk.example.com/", preferredTransport="JSONRPC")
        _mock_card_response(mock_async_client, data)

        success, agent_card, error_message = await card_resolver.fetch_agent_card("https://sdk.example.com")

        assert success is True, error_message
        assert agent_card.supportedInterfaces[0].protocolVersion == "1.0"

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_rejects_card_without_jsonrpc_interface(
        self, mock_async_client, card_resolver, sample_agent_card_data
    ):
        data = {**sample_agent_card_data}
        data["supportedInterfaces"] = [{"url": "https://g.example.com", "protocolBinding": "GRPC"}]
        _mock_card_response(mock_async_client, data)

        success, agent_card, error_message = await card_resolver.fetch_agent_card("https://g.example.com")

        assert success is False and agent_card is None
        assert "no JSON-RPC interface" in error_message

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_rejects_full_v03_card(self, mock_async_client, card_resolver):
        """A complete v0.3 remote card parses but is refused at registration: CodeMie speaks v1.0 only."""
        legacy = {
            "protocolVersion": "0.3.0",
            "name": "Currency Agent",
            "description": "Converts currencies",
            "url": "https://currency.example.com/",
            "preferredTransport": "JSONRPC",
            "version": "1.0.0",
            "capabilities": {"streaming": True, "pushNotifications": False},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [{"id": "convert", "name": "Convert", "description": "Convert", "tags": ["fx"]}],
            "supportsAuthenticatedExtendedCard": False,
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = legacy
        mock_response.text = "response text"
        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        success, agent_card, error_message = await card_resolver.fetch_agent_card("https://currency.example.com")

        assert success is False and agent_card is None
        assert "does not support A2A protocol 1.0" in error_message and "0.3.0" in error_message

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_with_authentication(self, mock_async_client, card_resolver, sample_agent_card_data):
        """Test fetching agent card with authentication credentials."""
        # Arrange
        url = "https://test-agent.example.com/api"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_agent_card_data
        mock_response.text = "response text"

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        with (
            patch('codemie.rest_api.a2a.client.card_resolver.SettingsService') as mock_settings,
            patch('codemie.rest_api.a2a.client.card_resolver.get_auth_header') as mock_get_auth_header,
        ):
            mock_settings.get_a2a_creds.return_value = {"auth_type": "bearer", "auth_value": "test_token"}
            mock_get_auth_header.return_value = {"Authorization": "Bearer test_token"}

            # Act
            success, agent_card, error_message = await card_resolver.fetch_agent_card(
                url, user_id="user-123", project_name="test-project", integration_id="integration-123"
            )

            # Assert
            assert success is True
            assert isinstance(agent_card, AgentCard)
            mock_settings.get_a2a_creds.assert_called_once_with(
                user_id="user-123", project_name="test-project", integration_id="integration-123"
            )

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_http_404(self, mock_async_client, card_resolver):
        """Test fetching agent card returns error on 404."""
        # Arrange
        url = "https://test-agent.example.com/api"

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        # Act
        success, agent_card, error_message = await card_resolver.fetch_agent_card(url)

        # Assert
        assert success is False
        assert agent_card is None
        assert "HTTP 404" in error_message

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_http_500(self, mock_async_client, card_resolver):
        """Test fetching agent card returns error on 500."""
        # Arrange
        url = "https://test-agent.example.com/api"

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        # Act
        success, agent_card, error_message = await card_resolver.fetch_agent_card(url)

        # Assert
        assert success is False
        assert agent_card is None
        assert "HTTP 500" in error_message

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_invalid_json(self, mock_async_client, card_resolver):
        """Test fetching agent card returns error on invalid JSON."""
        # Arrange
        url = "https://test-agent.example.com/api"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "invalid json"

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        # Act
        success, agent_card, error_message = await card_resolver.fetch_agent_card(url)

        # Assert
        assert success is False
        assert agent_card is None
        assert "Invalid agent card format" in error_message

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_invalid_schema(self, mock_async_client, card_resolver):
        """Test fetching agent card returns error on invalid schema."""
        # Arrange
        url = "https://test-agent.example.com/api"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "Test Agent"}  # Missing required fields
        mock_response.text = '{"name": "Test Agent"}'

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        # Act
        success, agent_card, error_message = await card_resolver.fetch_agent_card(url)

        # Assert
        assert success is False
        assert agent_card is None
        assert "Invalid agent card format" in error_message

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_network_error(self, mock_async_client, card_resolver):
        """Test fetching agent card returns error on network error."""
        # Arrange
        url = "https://test-agent.example.com/api"

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        # Act
        success, agent_card, error_message = await card_resolver.fetch_agent_card(url)

        # Assert
        assert success is False
        assert agent_card is None
        assert "Error fetching agent card" in error_message

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_timeout(self, mock_async_client, card_resolver):
        """Test fetching agent card returns error on timeout."""
        # Arrange
        url = "https://test-agent.example.com/api"

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(side_effect=httpx.TimeoutException("Request timeout"))
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        # Act
        success, agent_card, error_message = await card_resolver.fetch_agent_card(url)

        # Assert
        assert success is False
        assert agent_card is None
        assert "Error fetching agent card" in error_message

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_agent_card_auth_failure_continues(
        self, mock_async_client, card_resolver, sample_agent_card_data
    ):
        """Test fetching agent card continues when authentication fails."""
        # Arrange
        url = "https://test-agent.example.com/api"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_agent_card_data
        mock_response.text = "response text"

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        with patch('codemie.rest_api.a2a.client.card_resolver.SettingsService') as mock_settings:
            mock_settings.get_a2a_creds.side_effect = Exception("Credentials error")

            # Act
            success, agent_card, error_message = await card_resolver.fetch_agent_card(
                url, user_id="user-123", project_name="test-project"
            )

            # Assert - Should still succeed even though auth failed
            assert success is True
            assert isinstance(agent_card, AgentCard)

    @pytest.mark.asyncio
    @patch('codemie.rest_api.a2a.client.card_resolver.httpx.AsyncClient')
    async def test_fetch_sends_a2a_version_header(self, mock_async_client, card_resolver, sample_agent_card_data):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_agent_card_data
        mock_response.text = "t"
        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        with (
            patch('codemie.rest_api.a2a.client.card_resolver.SettingsService') as mock_settings,
            patch('codemie.rest_api.a2a.client.card_resolver.get_auth_header', return_value={"Authorization": "x"}),
        ):
            mock_settings.get_a2a_creds.return_value = {"auth_type": "bearer"}
            await card_resolver.fetch_agent_card("https://a.example.com", user_id="u", project_name="p")

        headers = mock_client_instance.get.call_args.kwargs["headers"]
        assert headers["A2A-Version"] == "1.0"
        assert headers["Authorization"] == "x"
        assert mock_client_instance.get.call_args.args[0].endswith("/.well-known/agent-card.json")
