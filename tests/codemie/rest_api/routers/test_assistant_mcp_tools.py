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

"""Tests for the /assistants/mcp_tools endpoint."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from codemie.rest_api.main import app
from codemie.rest_api.routers.assistant import router
from codemie.rest_api.security.user import User

app.include_router(router)
client = TestClient(app)


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    return User(id="test-user-id", email="test@example.com", project_names=["test-project"])


@pytest.fixture
def mock_auth_header():
    """Mock authorization header."""
    return {"Authorization": "Bearer test_token"}


@pytest.fixture(autouse=True)
def mock_authenticate(mock_user):
    """Mock authentication dependency."""
    with patch("codemie.rest_api.routers.assistant.authenticate", return_value=mock_user) as mock:
        yield mock


@pytest.fixture
def mock_mcp_server_config():
    """Mock MCP server configuration."""
    return {
        "name": "GitHub MCP",
        "description": "GitHub API tools",
        "enabled": True,
        "config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "ghp_xxx"},
        },
    }


@pytest.fixture
def mock_mcp_toolkit():
    """Mock MCP toolkit response."""
    return {
        "toolkit": "MCP",
        "label": "GitHub MCP Tools",
        "tools": [
            {
                "name": "create_or_update_file",
                "description": "Create or update a file in repository",
                "label": "Create Or Update File",
            },
            {
                "name": "search_repositories",
                "description": "Search for GitHub repositories",
                "label": "Search Repositories",
            },
        ],
    }


class TestGetMCPTools:
    """Test suite for POST /assistants/mcp_tools endpoint."""

    def test_get_mcp_tools_success(self, mock_auth_header, mock_authenticate, mock_mcp_server_config, mock_mcp_toolkit):
        """Test successful retrieval of MCP tools."""
        with patch(
            "codemie.service.tools.mcp_tools_info_service.MCPToolsInfoService.get_mcp_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = mock_mcp_toolkit

            response = client.post("/v1/assistants/mcp_tools", json=mock_mcp_server_config, headers=mock_auth_header)

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["toolkit"] == "MCP"
            assert len(data[0]["tools"]) == 2
            assert data[0]["tools"][0]["name"] == "create_or_update_file"
            assert data[0]["tools"][1]["name"] == "search_repositories"

            call_args = mock_get_info.call_args
            assert call_args[1]["mcp_server_config"].name == "GitHub MCP"
            assert call_args[1]["mcp_server_config"].enabled is True
            assert call_args[1]["project_name"] is None
            assert call_args[1]["user"] is not None

    def test_get_mcp_tools_with_http_transport(self, mock_auth_header, mock_authenticate, mock_mcp_toolkit):
        """Test retrieval of MCP tools with HTTP transport."""
        http_config = {
            "name": "HTTP MCP Server",
            "description": "HTTP-based MCP server",
            "enabled": True,
            "config": {
                "type": "streamable-http",
                "url": "http://localhost:3001/mcp",
                "headers": {"Authorization": "Bearer token"},
                "env": {},
            },
        }

        with patch(
            "codemie.service.tools.mcp_tools_info_service.MCPToolsInfoService.get_mcp_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = mock_mcp_toolkit

            response = client.post("/v1/assistants/mcp_tools", json=http_config, headers=mock_auth_header)

            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["toolkit"] == "MCP"

            call_args = mock_get_info.call_args
            assert call_args[1]["mcp_server_config"].name == "HTTP MCP Server"
            assert call_args[1]["user"] is not None

    def test_get_mcp_tools_error_handling(self, mock_auth_header, mock_authenticate, mock_mcp_server_config):
        """Test endpoint handles service errors properly."""
        from codemie.service.tools.mcp_tools_info_service import MCPToolsInfoServiceError

        with patch(
            "codemie.service.tools.mcp_tools_info_service.MCPToolsInfoService.get_mcp_toolkit_info"
        ) as mock_get_info:
            mock_get_info.side_effect = MCPToolsInfoServiceError(
                "No MCP tools found for server 'GitHub MCP'", "Please check that the MCP server is running"
            )

            response = client.post("/v1/assistants/mcp_tools", json=mock_mcp_server_config, headers=mock_auth_header)

            assert response.status_code == 422
            data = response.json()
            assert "No MCP tools found" in data["error"]["message"]
            assert "MCP server is running" in data["error"]["details"]

    def test_get_mcp_tools_requires_authentication(self, mock_mcp_server_config):
        """Test endpoint requires authentication."""
        response = client.post("/v1/assistants/mcp_tools", json=mock_mcp_server_config)

        assert response.status_code in [401, 403]

    def test_get_mcp_tools_with_empty_tools_list(self, mock_auth_header, mock_authenticate, mock_mcp_server_config):
        """Test endpoint with toolkit containing empty tools list."""
        empty_toolkit = {"toolkit": "MCP", "label": "GitHub MCP Tools", "tools": []}

        with patch(
            "codemie.service.tools.mcp_tools_info_service.MCPToolsInfoService.get_mcp_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = empty_toolkit

            response = client.post("/v1/assistants/mcp_tools", json=mock_mcp_server_config, headers=mock_auth_header)

            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["toolkit"] == "MCP"
            assert data[0]["tools"] == []

    def test_get_mcp_tools_response_model_structure(
        self, mock_auth_header, mock_authenticate, mock_mcp_server_config, mock_mcp_toolkit
    ):
        """Test response structure matches ToolKit model."""
        with patch(
            "codemie.service.tools.mcp_tools_info_service.MCPToolsInfoService.get_mcp_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = mock_mcp_toolkit

            response = client.post("/v1/assistants/mcp_tools", json=mock_mcp_server_config, headers=mock_auth_header)

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)

            toolkit = data[0]
            assert "toolkit" in toolkit
            assert "tools" in toolkit
            assert isinstance(toolkit["tools"], list)

            if toolkit["tools"]:
                tool = toolkit["tools"][0]
                assert "name" in tool
                assert "description" in tool
                assert "label" in tool

    def test_get_mcp_tools_forces_enabled_true(self, mock_auth_header, mock_authenticate, mock_mcp_toolkit):
        """Test endpoint forces enabled=True regardless of input."""
        disabled_config = {
            "name": "Test MCP",
            "description": "Test server",
            "enabled": False,  # Should be forced to True
            "config": {"command": "npx", "args": ["-y", "test-server"], "env": {}},
        }

        with patch(
            "codemie.service.tools.mcp_tools_info_service.MCPToolsInfoService.get_mcp_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = mock_mcp_toolkit

            response = client.post("/v1/assistants/mcp_tools", json=disabled_config, headers=mock_auth_header)

            assert response.status_code == 200

            # Verify that enabled was forced to True
            call_args = mock_get_info.call_args
            assert call_args[1]["mcp_server_config"].enabled is True

    def test_get_mcp_tools_with_marketplace_config(self, mock_auth_header, mock_authenticate, mock_mcp_toolkit):
        """Test endpoint with MCP config from marketplace catalog."""
        marketplace_config = {
            "name": "Marketplace MCP",
            "description": "From catalog",
            "enabled": True,
            "mcp_config_id": "catalog-config-123",
            "config": {"command": "npx", "args": ["-y", "marketplace-server"], "env": {"API_KEY": "{{API_KEY}}"}},
        }

        with patch(
            "codemie.service.tools.mcp_tools_info_service.MCPToolsInfoService.get_mcp_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = mock_mcp_toolkit

            response = client.post("/v1/assistants/mcp_tools", json=marketplace_config, headers=mock_auth_header)

            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1

            call_args = mock_get_info.call_args
            assert call_args[1]["mcp_server_config"].name == "Marketplace MCP"
            assert call_args[1]["mcp_server_config"].mcp_config_id == "catalog-config-123"

    def test_get_mcp_tools_validates_response_by_alias(
        self, mock_auth_header, mock_authenticate, mock_mcp_server_config
    ):
        """Test response is serialized using field aliases."""
        toolkit_with_aliases = {
            "toolkit": "MCP",
            "label": "Test Tools",
            "tools": [{"name": "_tool", "description": "Tool desc", "label": "Tool Label"}],
        }

        with patch(
            "codemie.service.tools.mcp_tools_info_service.MCPToolsInfoService.get_mcp_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = toolkit_with_aliases

            response = client.post("/v1/assistants/mcp_tools", json=mock_mcp_server_config, headers=mock_auth_header)

            assert response.status_code == 200
            data = response.json()
            assert data[0]["toolkit"] == "MCP"

    def test_get_mcp_tools_broker_auth_required(self, mock_auth_header, mock_authenticate, mock_mcp_server_config):
        """Test endpoint returns 401 with x-user-mcp-auth-location when broker auth is needed."""
        from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException

        with patch(
            "codemie.service.tools.mcp_tools_info_service.MCPToolsInfoService.get_mcp_toolkit_info"
        ) as mock_get_info:
            mock_get_info.side_effect = BrokerAuthRequiredException(
                message="Broker token exchange failed with HTTP 502",
                auth_location="https://auth.example.com/login",
                details="HTTP 502",
            )

            response = client.post("/v1/assistants/mcp_tools", json=mock_mcp_server_config, headers=mock_auth_header)

        assert response.status_code == 401
        assert response.headers.get("x-user-mcp-auth-location") == "https://auth.example.com/login"
        data = response.json()
        assert "Broker token exchange failed" in data["error"]["message"]
        assert data["error"]["login_url"] == "https://auth.example.com/login"

    def test_get_mcp_tools_unexpected_error_handling(self, mock_auth_header, mock_authenticate, mock_mcp_server_config):
        """Test endpoint handles unexpected errors with 500 status."""
        with patch(
            "codemie.service.tools.mcp_tools_info_service.MCPToolsInfoService.get_mcp_toolkit_info"
        ) as mock_get_info:
            mock_get_info.side_effect = RuntimeError("Unexpected error")

            response = client.post("/v1/assistants/mcp_tools", json=mock_mcp_server_config, headers=mock_auth_header)

            assert response.status_code == 500
            data = response.json()
            assert "Failed to retrieve MCP tools" in data["error"]["message"]
            assert "Unexpected error" in data["error"]["details"]
