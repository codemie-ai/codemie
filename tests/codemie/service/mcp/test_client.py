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

"""
Tests for the MCP Connect Client.

This module contains tests for the MCPConnectClient class, which
handles communication with the MCP-Connect service.
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from codemie.core.exceptions import MCPAuthenticationRequiredException
from codemie.enterprise.mcp_auth.dependencies import MCPPostAuth401Result
from codemie.service.mcp.client import BUCKET_KEY, MCPConnectClient, MCP_CONNECT_BUCKET_PLACEHOLDER
from codemie.service.mcp.models import (
    MCPServerConfig,
    MCPToolDefinition,
    MCPToolInvocationResponse,
    MCPExecutionContext,
)
from codemie.service.mcp.client import _hash_remainder
from codemie.configs import config
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException


@pytest.fixture
def server_config():
    """Fixture providing a basic server configuration."""
    return MCPServerConfig(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": "test-token"},
        auth_token=None,
    )


@pytest.fixture
def server_config_with_auth():
    """Fixture providing a server configuration with authentication."""
    return MCPServerConfig(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": "test-token"},
        auth_token="test-auth-token",
    )


@pytest.fixture
def sample_tools_response():
    """Fixture providing a sample tools list response."""
    return {
        "tools": [
            {
                "name": "test_tool",
                "description": "A test tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "param1": {"type": "string", "description": "First parameter"},
                        "param2": {"type": "number", "description": "Second parameter"},
                    },
                    "required": ["param1"],
                },
            }
        ]
    }


@pytest.fixture
def sample_tool_invocation_response():
    """Fixture providing a sample tool invocation response."""
    return {"content": [{"type": "text", "text": "Tool execution successful"}], "isError": False}


@pytest.fixture
def sample_tool_error_response():
    """Fixture providing a sample tool error response."""
    return {"content": [{"type": "error", "text": "Tool execution failed"}], "isError": True}


class TestMCPConnectClientInitialization:
    """Tests for the initialization of MCPConnectClient."""

    def test_init_default_url(self):
        """Test client initialization with default URL."""
        client = MCPConnectClient()
        assert client.base_url == "http://localhost:3000"
        assert client.bridge_endpoint == "http://localhost:3000/bridge"

    def test_init_custom_url(self):
        """Test client initialization with custom URL."""
        client = MCPConnectClient("https://custom-mcp.example.com")
        assert client.base_url == "https://custom-mcp.example.com"
        assert client.bridge_endpoint == "https://custom-mcp.example.com/bridge"

    def test_init_url_trailing_slash_handling(self):
        """Test client initialization with URL that has a trailing slash."""
        client = MCPConnectClient("https://custom-mcp.example.com/")
        assert client.base_url == "https://custom-mcp.example.com"
        assert client.bridge_endpoint == "https://custom-mcp.example.com/bridge"


class TestMCPConnectClientHeaders:
    """Tests for the header generation functionality."""

    def test_get_headers_no_auth(self, server_config):
        """Test header generation without authentication token."""
        from codemie.service.mcp.client import _get_headers, _get_bucket_no

        bucket_no = _get_bucket_no(server_config)
        headers = _get_headers(bucket_no, server_config)
        assert "Authorization" not in headers
        assert "Content-Type" in headers
        assert headers["Content-Type"] == "application/json"
        assert "X-MCP-Connect-Bucket" in headers
        assert headers["X-MCP-Connect-Bucket"] == str(bucket_no)

    def test_get_headers_with_auth(self, server_config_with_auth):
        """Test header generation with authentication token."""
        from codemie.service.mcp.client import _get_headers, _get_bucket_no

        bucket_no = _get_bucket_no(server_config_with_auth)
        headers = _get_headers(bucket_no, server_config_with_auth)
        assert "Content-Type" in headers
        assert headers["Content-Type"] == "application/json"
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test-auth-token"
        assert "X-MCP-Connect-Bucket" in headers
        assert headers["X-MCP-Connect-Bucket"] == str(bucket_no)

    def test_get_bucket_no_uses_local_bucket_key_without_env_serialization(self, server_config):
        """Test bucket routing can use local context without bridge env leakage."""
        from codemie.service.mcp.client import _get_bucket_no

        server_config.env[BUCKET_KEY] = "legacy-conversation"
        legacy_bucket = _get_bucket_no(server_config)
        server_config.env.pop(BUCKET_KEY)
        object.__setattr__(server_config, "bucket_key", "legacy-conversation")

        assert _get_bucket_no(server_config) == legacy_bucket


class TestMCPConnectClientListTools:
    """Tests for the list_tools method."""

    @pytest.mark.asyncio
    async def test_list_tools_success(self, server_config, sample_tools_response):
        """Test successful retrieval of tools list."""
        client = MCPConnectClient()

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tools_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            tools = await client.list_tools(server_config)

            # Verify request
            # Get expected bucket number from server_config
            from codemie.service.mcp.client import _get_bucket_no

            bucket_no = _get_bucket_no(server_config)

            mock_client.return_value.__aenter__.return_value.post.assert_called_once_with(
                client.bridge_endpoint,
                json={
                    "method": "tools/list",
                    "serverPath": server_config.command,
                    "args": server_config.args,
                    "params": {},
                    "env": server_config.env,
                    "mcp_headers": {},
                    "single_usage": False,  # Added for lifecycle support
                },
                headers={"Content-Type": "application/json", "X-MCP-Connect-Bucket": str(bucket_no)},
            )

            # Verify response parsing
            assert len(tools) == 1
            assert isinstance(tools[0], MCPToolDefinition)
            assert tools[0].name == "test_tool"
            assert tools[0].description == "A test tool"

    @pytest.mark.asyncio
    async def test_list_tools_http_error(self, server_config):
        """Test handling of HTTP errors during tool listing."""
        client = MCPConnectClient()

        # Mock response for HTTPStatusError
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.reason_phrase = "Internal Server Error"
        mock_response.text = '{"error": "Test error message"}'
        mock_response.json.return_value = {"error": "Test error message"}

        mock_request = MagicMock()

        http_error = httpx.HTTPStatusError("Error", request=mock_request, response=mock_response)

        mock_http_response = MagicMock()
        mock_http_response.raise_for_status.side_effect = http_error

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_http_response)

            with pytest.raises(ValueError, match="Test error message"):
                await client.list_tools(server_config)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status_code", "challenge"),
        [
            (401, 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"'),
            (
                403,
                'Bearer error="insufficient_scope", scope="tools:list", '
                'resource_metadata="https://mcp.example.com/meta"',
            ),
        ],
    )
    async def test_list_tools_auth_challenge_http_error_preserves_response(
        self,
        server_config,
        status_code,
        challenge,
    ):
        """Test auth-challenge bridge errors keep the original HTTPX response."""
        client = MCPConnectClient()
        request = httpx.Request("POST", client.bridge_endpoint)
        mock_response = httpx.Response(
            status_code,
            headers={"WWW-Authenticate": challenge},
            json={"error": "authentication required"},
            request=request,
        )

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.list_tools(server_config)

        assert exc_info.value.response.status_code == status_code
        assert exc_info.value.response.headers["WWW-Authenticate"] == challenge

    @pytest.mark.asyncio
    async def test_list_tools_bare_401_raises_broker_auth_required(self, server_config):
        """A 401 from MCP-Connect without WWW-Authenticate signals broker auth, not server challenge."""
        client = MCPConnectClient()
        request = httpx.Request("POST", client.bridge_endpoint)
        mock_response = httpx.Response(
            401,
            json={"error": "session expired"},
            request=request,
        )

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            with pytest.raises(BrokerAuthRequiredException) as exc_info:
                await client.list_tools(server_config)

        assert exc_info.value.message == "Authentication required. Please log in to access the MCP server."
        assert exc_info.value.details == "HTTP 401"
        assert exc_info.value.auth_location == config.BROKER_AUTH_LOCATION_URL

    @pytest.mark.asyncio
    async def test_list_tools_json_error(self, server_config):
        """Test handling of JSON decode errors."""
        client = MCPConnectClient()

        mock_response = MagicMock()
        mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "Not valid JSON"

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            with pytest.raises(ValueError, match="Invalid JSON response"):
                await client.list_tools(server_config)

    @pytest.mark.asyncio
    async def test_list_tools_with_auth(self, server_config_with_auth, sample_tools_response):
        """Test tool listing with authentication."""
        client = MCPConnectClient()

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tools_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            await client.list_tools(server_config_with_auth)

            # Get expected bucket number from server config
            from codemie.service.mcp.client import _get_bucket_no

            bucket_no = _get_bucket_no(server_config_with_auth)

            # Verify request includes auth header and bucket header
            mock_client.return_value.__aenter__.return_value.post.assert_called_once_with(
                client.bridge_endpoint,
                json={
                    "method": "tools/list",
                    "serverPath": server_config_with_auth.command,
                    "args": server_config_with_auth.args,
                    "params": {},
                    "env": server_config_with_auth.env,
                    "mcp_headers": {},
                    "single_usage": False,  # Added for lifecycle support
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer test-auth-token",
                    "X-MCP-Connect-Bucket": str(bucket_no),
                },
            )

    @pytest.mark.asyncio
    async def test_list_tools_does_not_send_local_bucket_key_in_env(self, server_config, sample_tools_response):
        """Test conversation routing key is local-only and not sent to MCP-Connect."""
        client = MCPConnectClient()
        object.__setattr__(server_config, "bucket_key", "conversation-1")
        server_config.env[BUCKET_KEY] = "legacy-leak"
        server_config.env.pop(BUCKET_KEY)

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tools_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            await client.list_tools(server_config)

            post_kwargs = mock_client.return_value.__aenter__.return_value.post.call_args.kwargs

        assert post_kwargs["json"]["env"] == {"GITHUB_PERSONAL_ACCESS_TOKEN": "test-token"}
        assert BUCKET_KEY not in post_kwargs["json"]["env"]

    @pytest.mark.asyncio
    async def test_list_tools_merges_request_scoped_auth_headers_without_mutating_server_config(
        self,
        sample_tools_response,
    ):
        client = MCPConnectClient()
        server_config = MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "test-token"},
            headers={"Authorization": "Bearer stale", "X-Static": "static-value"},
        )
        execution_context = MCPExecutionContext(
            user_id="user-123",
            auth_headers={"Authorization": "Bearer fresh", "X-Request-Auth": "request-value"},
        )
        original_headers = server_config.headers.copy()

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tools_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            await client.list_tools(server_config, execution_context)

            from codemie.service.mcp.client import _get_bucket_no

            bucket_no = _get_bucket_no(server_config)

            mock_client.return_value.__aenter__.return_value.post.assert_called_once_with(
                client.bridge_endpoint,
                json={
                    "method": "tools/list",
                    "serverPath": server_config.command,
                    "args": server_config.args,
                    "params": {},
                    "env": server_config.env,
                    "mcp_headers": {
                        "Authorization": "Bearer fresh",
                        "X-Static": "static-value",
                        "X-Request-Auth": "request-value",
                    },
                    "single_usage": False,
                    "user_id": "user-123",
                },
                headers={"Content-Type": "application/json", "X-MCP-Connect-Bucket": str(bucket_no)},
            )

        assert server_config.headers == original_headers

    @pytest.mark.asyncio
    @pytest.mark.parametrize("auth_headers", [None, {}])
    async def test_list_tools_keeps_static_headers_when_auth_headers_missing_or_empty(
        self,
        sample_tools_response,
        auth_headers,
    ):
        client = MCPConnectClient()
        server_config = MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "test-token"},
            headers={"Authorization": "Bearer static", "X-Static": "static-value"},
        )
        execution_context = MCPExecutionContext(user_id="user-123", auth_headers=auth_headers)
        original_headers = server_config.headers.copy()

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tools_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            await client.list_tools(server_config, execution_context)

            from codemie.service.mcp.client import _get_bucket_no

            bucket_no = _get_bucket_no(server_config)

            mock_client.return_value.__aenter__.return_value.post.assert_called_once_with(
                client.bridge_endpoint,
                json={
                    "method": "tools/list",
                    "serverPath": server_config.command,
                    "args": server_config.args,
                    "params": {},
                    "env": server_config.env,
                    "mcp_headers": original_headers,
                    "single_usage": False,
                    "user_id": "user-123",
                },
                headers={"Content-Type": "application/json", "X-MCP-Connect-Bucket": str(bucket_no)},
            )

        assert server_config.headers == original_headers

    @pytest.mark.asyncio
    async def test_invoke_tool_with_execution_context(self, server_config, sample_tool_invocation_response):
        """Test tool invocation with execution context."""
        client = MCPConnectClient()
        tool_name = "test_tool"
        tool_args = {"param1": "value1", "param2": 42}
        execution_context = MCPExecutionContext(
            user_id="user-123",
            assistant_id="assistant-456",
            project_name="test-project",
            workflow_execution_id="workflow-789",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tool_invocation_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            response = await client.invoke_tool(server_config, tool_name, tool_args, execution_context)

            # Verify request includes context fields
            from codemie.service.mcp.client import _get_bucket_no

            bucket_no = _get_bucket_no(server_config)

            mock_client.return_value.__aenter__.return_value.post.assert_called_once_with(
                client.bridge_endpoint,
                json={
                    "method": "tools/call",
                    "serverPath": server_config.command,
                    "args": server_config.args,
                    "params": {"name": tool_name, "arguments": tool_args},
                    "env": server_config.env,
                    "mcp_headers": {},
                    "single_usage": False,
                    "user_id": "user-123",
                    "assistant_id": "assistant-456",
                    "project_name": "test-project",
                    "workflow_execution_id": "workflow-789",
                },
                headers={"Content-Type": "application/json", "X-MCP-Connect-Bucket": str(bucket_no)},
            )

            # Verify response parsing
            assert isinstance(response, MCPToolInvocationResponse)
            assert len(response.content) == 1
            assert response.content[0].text == "Tool execution successful"
            assert response.isError is False

    @pytest.mark.asyncio
    async def test_invoke_tool_merges_request_scoped_auth_headers_without_mutating_server_config(
        self,
        sample_tool_invocation_response,
    ):
        client = MCPConnectClient()
        server_config = MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "test-token"},
            headers={"Authorization": "Bearer stale", "X-Static": "static-value"},
        )
        execution_context = MCPExecutionContext(
            user_id="user-123",
            workflow_execution_id="workflow-789",
            auth_headers={"Authorization": "Bearer fresh", "X-Request-Auth": "request-value"},
        )
        original_headers = server_config.headers.copy()

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tool_invocation_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            await client.invoke_tool(server_config, "test_tool", {"param1": "value1"}, execution_context)

            from codemie.service.mcp.client import _get_bucket_no

            bucket_no = _get_bucket_no(server_config)

            mock_client.return_value.__aenter__.return_value.post.assert_called_once_with(
                client.bridge_endpoint,
                json={
                    "method": "tools/call",
                    "serverPath": server_config.command,
                    "args": server_config.args,
                    "params": {"name": "test_tool", "arguments": {"param1": "value1"}},
                    "env": server_config.env,
                    "mcp_headers": {
                        "Authorization": "Bearer fresh",
                        "X-Static": "static-value",
                        "X-Request-Auth": "request-value",
                    },
                    "single_usage": False,
                    "user_id": "user-123",
                    "workflow_execution_id": "workflow-789",
                },
                headers={"Content-Type": "application/json", "X-MCP-Connect-Bucket": str(bucket_no)},
            )

        assert server_config.headers == original_headers

    @pytest.mark.asyncio
    @pytest.mark.parametrize("auth_headers", [None, {}])
    async def test_invoke_tool_keeps_static_headers_when_auth_headers_missing_or_empty(
        self,
        sample_tool_invocation_response,
        auth_headers,
    ):
        client = MCPConnectClient()
        server_config = MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "test-token"},
            headers={"Authorization": "Bearer static", "X-Static": "static-value"},
        )
        execution_context = MCPExecutionContext(
            user_id="user-123",
            workflow_execution_id="workflow-789",
            auth_headers=auth_headers,
        )
        original_headers = server_config.headers.copy()

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tool_invocation_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            await client.invoke_tool(server_config, "test_tool", {"param1": "value1"}, execution_context)

            from codemie.service.mcp.client import _get_bucket_no

            bucket_no = _get_bucket_no(server_config)

            mock_client.return_value.__aenter__.return_value.post.assert_called_once_with(
                client.bridge_endpoint,
                json={
                    "method": "tools/call",
                    "serverPath": server_config.command,
                    "args": server_config.args,
                    "params": {"name": "test_tool", "arguments": {"param1": "value1"}},
                    "env": server_config.env,
                    "mcp_headers": original_headers,
                    "single_usage": False,
                    "user_id": "user-123",
                    "workflow_execution_id": "workflow-789",
                },
                headers={"Content-Type": "application/json", "X-MCP-Connect-Bucket": str(bucket_no)},
            )

        assert server_config.headers == original_headers

    @pytest.mark.asyncio
    async def test_invoke_tool_with_partial_execution_context(self, server_config, sample_tool_invocation_response):
        """Test tool invocation with partial execution context."""
        client = MCPConnectClient()
        tool_name = "test_tool"
        tool_args = {"param1": "value1"}
        execution_context = MCPExecutionContext(
            user_id="user-123",
            workflow_execution_id="workflow-789",
            # assistant_id and project_name are None
        )

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tool_invocation_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            response = await client.invoke_tool(server_config, tool_name, tool_args, execution_context)

            # Verify request includes context fields (even None values)
            from codemie.service.mcp.client import _get_bucket_no

            bucket_no = _get_bucket_no(server_config)

            mock_client.return_value.__aenter__.return_value.post.assert_called_once_with(
                client.bridge_endpoint,
                json={
                    "method": "tools/call",
                    "serverPath": server_config.command,
                    "args": server_config.args,
                    "params": {"name": tool_name, "arguments": tool_args},
                    "env": server_config.env,
                    "mcp_headers": {},
                    "single_usage": False,
                    "user_id": "user-123",
                    "workflow_execution_id": "workflow-789",
                },
                headers={"Content-Type": "application/json", "X-MCP-Connect-Bucket": str(bucket_no)},
            )

            # Verify response parsing
            assert isinstance(response, MCPToolInvocationResponse)

    @pytest.mark.asyncio
    async def test_invoke_tool_without_execution_context(self, server_config, sample_tool_invocation_response):
        """Test tool invocation without execution context (backward compatibility)."""
        client = MCPConnectClient()
        tool_name = "test_tool"
        tool_args = {"param1": "value1", "param2": 42}

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tool_invocation_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            response = await client.invoke_tool(server_config, tool_name, tool_args)

            # Verify request does not include context fields
            from codemie.service.mcp.client import _get_bucket_no

            bucket_no = _get_bucket_no(server_config)

            mock_client.return_value.__aenter__.return_value.post.assert_called_once_with(
                client.bridge_endpoint,
                json={
                    "method": "tools/call",
                    "serverPath": server_config.command,
                    "args": server_config.args,
                    "params": {"name": tool_name, "arguments": tool_args},
                    "env": server_config.env,
                    "mcp_headers": {},
                    "single_usage": False,
                },
                headers={"Content-Type": "application/json", "X-MCP-Connect-Bucket": str(bucket_no)},
            )

            # Verify response parsing
            assert isinstance(response, MCPToolInvocationResponse)

    @pytest.mark.asyncio
    async def test_invoke_tool_success(self, server_config, sample_tool_invocation_response):
        """Test successful tool invocation."""
        client = MCPConnectClient()
        tool_name = "test_tool"
        tool_args = {"param1": "value1", "param2": 42}

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tool_invocation_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            response = await client.invoke_tool(server_config, tool_name, tool_args)

            # Verify request
            # Get expected bucket number from server config
            from codemie.service.mcp.client import _get_bucket_no

            bucket_no = _get_bucket_no(server_config)

            mock_client.return_value.__aenter__.return_value.post.assert_called_once_with(
                client.bridge_endpoint,
                json={
                    "method": "tools/call",
                    "serverPath": server_config.command,
                    "args": server_config.args,
                    "params": {"name": tool_name, "arguments": tool_args},
                    "env": server_config.env,
                    "mcp_headers": {},
                    "single_usage": False,  # Added for lifecycle support
                },
                headers={"Content-Type": "application/json", "X-MCP-Connect-Bucket": str(bucket_no)},
            )

            # Verify response parsing
            assert isinstance(response, MCPToolInvocationResponse)
            assert len(response.content) == 1
            assert response.content[0].text == "Tool execution successful"
            assert response.isError is False

    @pytest.mark.asyncio
    async def test_invoke_tool_error_response(self, server_config, sample_tool_error_response):
        """Test handling of error responses from the tool."""
        client = MCPConnectClient()
        tool_name = "test_tool"
        tool_args = {"param1": "value1", "param2": 42}

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tool_error_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            response = await client.invoke_tool(server_config, tool_name, tool_args)

            # Verify response shows error
            assert response.isError is True
            assert response.content[0].text == "Tool execution failed"
            assert response.content[0].type == "error"

    @pytest.mark.asyncio
    async def test_invoke_tool_http_error(self, server_config):
        """Test handling of HTTP errors during invocation."""
        client = MCPConnectClient()
        tool_name = "test_tool"
        tool_args = {"param1": "value1", "param2": 42}

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=MagicMock()
        )

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            with pytest.raises(httpx.HTTPStatusError):
                await client.invoke_tool(server_config, tool_name, tool_args)

    async def test_invoke_tool_raises_auth_required_for_recoverable_insufficient_scope(self, server_config):
        """Post-auth 403 insufficient_scope is converted to auth-required recovery before generic wrapping."""
        client = MCPConnectClient()
        server_config.url = "https://mcp.example.com/mcp"
        server_config.command = None
        server_config.auth_config = {
            "id": "auth-config-1",
            "auth_type": "oauth2",
            "authorization_url": "https://auth.example.com/oauth2/authorize",
            "token_url": "https://auth.example.com/oauth2/token",
            "client_id": "client-1",
            "client_type": "public",
            "scopes": ["read"],
            "token_delivery": {"method": "header", "key": "Authorization"},
        }
        server_config.mcp_config_id = "mcp-config-1"
        server_config.mcp_config_name = "OneHub"
        execution_context = MCPExecutionContext(
            user_id="user-1",
            conversation_id="conversation-1",
            oauth2_token_data={"scope": "read", "scopes": ["ignored"]},
        )
        mock_response = httpx.Response(
            403,
            headers={
                "WWW-Authenticate": (
                    'Bearer error="insufficient_scope", scope="read write admin", '
                    'resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource?tenant=secret"'
                )
            },
            request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"),
        )

        with patch(
            "codemie.service.mcp.client.build_mcp_insufficient_scope_auth_exception", return_value=None
        ) as bridge:
            bridge.return_value = MCPAuthenticationRequiredException(
                {
                    "error": "authentication_required",
                    "servers": [
                        {
                            "mcp_config_id": "mcp-config-1",
                            "mcp_config_name": "OneHub",
                            "status": "authentication_required",
                            "error": "insufficient_scope",
                            "action": "reauthenticate",
                        }
                    ],
                }
            )
            with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

                with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                    await client.invoke_tool(server_config, "test_tool", {}, execution_context)

        assert exc_info.value.payload["servers"][0]["error"] == "insufficient_scope"
        bridge.assert_called_once()
        assert bridge.call_args.kwargs["www_authenticate_header"].startswith("Bearer ")

    @pytest.mark.asyncio
    async def test_invoke_tool_retries_once_after_successful_post_auth_refresh(self, server_config):
        """Post-auth 401 invalid_token refreshes token and retries tools/call once with refreshed header."""
        client = MCPConnectClient()
        server_config.url = "https://mcp.example.com/mcp"
        server_config.command = None
        server_config.type = "streamable-http"
        server_config.auth_config = {"id": "auth-config-1", "auth_type": "oauth2"}
        execution_context = MCPExecutionContext(
            user_id="user-1",
            conversation_id="conversation-1",
            auth_headers={"Authorization": "Bearer old-access", "X-Request-Auth-Context": "ctx-1"},
        )
        server_config.headers = {"X-Static-MCP-Header": "static-1", "Authorization": "Bearer static-old-access"}
        first_response = httpx.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"),
        )
        second_response = httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "ok"}], "isError": False},
            request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"),
        )

        with patch(
            "codemie.service.mcp.client.build_mcp_post_auth_401_result",
            return_value=MCPPostAuth401Result(retry_auth_headers={"Authorization": "Bearer fresh-access"}),
        ) as bridge:
            with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
                post = AsyncMock(side_effect=[first_response, second_response])
                mock_client.return_value.__aenter__.return_value.post = post

                response = await client.invoke_tool(server_config, "test_tool", {"a": 1}, execution_context)

        assert response.content[0].text == "ok"
        assert post.call_count == 2
        retry_body = post.call_args_list[1].kwargs["json"]
        assert retry_body["mcp_headers"]["Authorization"] == "Bearer fresh-access"
        assert retry_body["mcp_headers"]["Authorization"] != execution_context.auth_headers["Authorization"]
        assert retry_body["mcp_headers"]["X-Request-Auth-Context"] == "ctx-1"
        assert retry_body["mcp_headers"]["X-Static-MCP-Header"] == "static-1"
        bridge.assert_called_once()
        assert bridge.call_args.kwargs["status_code"] == 401
        assert bridge.call_args.kwargs["www_authenticate_header"] == 'Bearer error="invalid_token"'

    @pytest.mark.asyncio
    async def test_invoke_tool_second_401_after_refresh_prompts_without_second_refresh(self, server_config):
        """A retry 401 is converted to auth-required and does not enter a refresh loop."""
        client = MCPConnectClient()
        server_config.url = "https://mcp.example.com/mcp"
        server_config.command = None
        server_config.type = "streamable-http"
        server_config.auth_config = {"id": "auth-config-1", "auth_type": "oauth2"}
        execution_context = MCPExecutionContext(user_id="user-1", conversation_id="conversation-1")
        auth_exception = MCPAuthenticationRequiredException(
            {
                "error": "authentication_required",
                "servers": [{"status": "session_expired", "reason": "retry_401_after_refresh"}],
            }
        )
        first_response = httpx.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"),
        )
        retry_response = httpx.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"),
        )

        with patch(
            "codemie.service.mcp.client.build_mcp_post_auth_401_result",
            side_effect=[
                MCPPostAuth401Result(retry_auth_headers={"Authorization": "Bearer fresh-access"}),
                MCPPostAuth401Result(auth_exception=auth_exception),
            ],
        ) as bridge:
            with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                    side_effect=[first_response, retry_response]
                )

                with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                    await client.invoke_tool(server_config, "test_tool", {}, execution_context)

        assert exc_info.value.payload["servers"][0]["reason"] == "retry_401_after_refresh"
        assert bridge.call_count == 2
        assert bridge.call_args_list[1].kwargs["refresh_allowed"] is False

    @pytest.mark.asyncio
    async def test_invoke_tool_401_without_challenge_prompts_for_authenticated_http_call(self, server_config):
        """Authenticated HTTP tools/call 401 without WWW-Authenticate prompts directly before generic wrapping."""
        client = MCPConnectClient()
        server_config.url = "https://mcp.example.com/mcp"
        server_config.command = None
        server_config.type = "streamable-http"
        server_config.auth_config = {"id": "auth-config-1", "auth_type": "oauth2"}
        auth_exception = MCPAuthenticationRequiredException(
            {
                "error": "authentication_required",
                "servers": [{"status": "session_expired", "reason": "missing_www_authenticate"}],
            }
        )
        mock_response = httpx.Response(401, request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"))

        with patch(
            "codemie.service.mcp.client.build_mcp_post_auth_401_result",
            return_value=MCPPostAuth401Result(auth_exception=auth_exception),
        ) as bridge:
            with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

                with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                    await client.invoke_tool(server_config, "test_tool", {})

        assert exc_info.value.payload["servers"][0]["reason"] == "missing_www_authenticate"
        bridge.assert_called_once()

    @pytest.mark.asyncio
    async def test_invoke_tool_static_authorization_401_prompts_for_authenticated_http_call(self, server_config):
        """Static Authorization headers count as authenticated post-auth HTTP calls."""
        client = MCPConnectClient()
        server_config.url = "https://mcp.example.com/mcp"
        server_config.command = None
        server_config.type = "streamable-http"
        server_config.headers = {"Authorization": "Bearer static-access"}
        auth_exception = MCPAuthenticationRequiredException(
            {
                "error": "authentication_required",
                "servers": [{"status": "session_expired", "reason": "missing_www_authenticate"}],
            }
        )
        mock_response = httpx.Response(401, request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"))

        with patch(
            "codemie.service.mcp.client.build_mcp_post_auth_401_result",
            return_value=MCPPostAuth401Result(auth_exception=auth_exception),
        ) as bridge:
            with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

                with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                    await client.invoke_tool(server_config, "test_tool", {})

        assert exc_info.value.payload["servers"][0]["reason"] == "missing_www_authenticate"
        bridge.assert_called_once()

    @pytest.mark.asyncio
    async def test_invoke_tool_bearer_non_invalid_token_prompts_without_refresh(self, server_config):
        """Bearer non-invalid_token 401 is handled at client boundary without retry."""
        client = MCPConnectClient()
        server_config.url = "https://mcp.example.com/mcp"
        server_config.command = None
        server_config.type = "streamable-http"
        server_config.auth_config = {"id": "auth-config-1", "auth_type": "oauth2"}
        auth_exception = MCPAuthenticationRequiredException(
            {
                "error": "authentication_required",
                "servers": [{"status": "session_expired", "reason": "unsupported_bearer_error"}],
            }
        )
        mock_response = httpx.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_request"'},
            request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"),
        )

        with patch(
            "codemie.service.mcp.client.build_mcp_post_auth_401_result",
            return_value=MCPPostAuth401Result(auth_exception=auth_exception),
        ) as bridge:
            with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
                post = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aenter__.return_value.post = post

                with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                    await client.invoke_tool(server_config, "test_tool", {})

        assert exc_info.value.payload["servers"][0]["reason"] == "unsupported_bearer_error"
        assert post.call_count == 1
        bridge.assert_called_once()
        assert bridge.call_args.kwargs["www_authenticate_header"] == 'Bearer error="invalid_request"'

    @pytest.mark.asyncio
    async def test_invoke_tool_keeps_unsupported_403_on_generic_path(self, server_config):
        """Unsupported 403 challenges still raise HTTPStatusError instead of auth-required recovery."""
        client = MCPConnectClient()
        mock_response = httpx.Response(
            403,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"),
        )

        with patch("codemie.service.mcp.client.build_mcp_insufficient_scope_auth_exception", return_value=None):
            with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

                with pytest.raises(httpx.HTTPStatusError):
                    await client.invoke_tool(server_config, "test_tool", {})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("headers", [{}, {"WWW-Authenticate": "Basic realm=example"}])
    async def test_invoke_tool_keeps_missing_or_non_bearer_403_on_generic_path(self, server_config, headers):
        """Non-auth 403 and missing/unsupported auth challenges remain generic MCP failures."""
        client = MCPConnectClient()
        mock_response = httpx.Response(
            403,
            headers=headers,
            request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"),
        )

        with patch(
            "codemie.service.mcp.client.build_mcp_insufficient_scope_auth_exception", return_value=None
        ) as bridge:
            with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

                with pytest.raises(httpx.HTTPStatusError):
                    await client.invoke_tool(server_config, "test_tool", {})

        if "WWW-Authenticate" in headers:
            bridge.assert_called_once()
        else:
            bridge.assert_not_called()

    @pytest.mark.asyncio
    async def test_invoke_tool_bare_401_raises_broker_auth_required(self, server_config):
        """A bare 401 (no WWW-Authenticate, no auth context) signals broker auth required."""
        client = MCPConnectClient()
        mock_response = httpx.Response(
            401,
            json={"error": "session expired"},
            request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"),
        )

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            with pytest.raises(BrokerAuthRequiredException) as exc_info:
                await client.invoke_tool(server_config, "test_tool", {"param": "value"})

        assert exc_info.value.message == "Authentication required. Please log in to access the MCP server."
        assert exc_info.value.details == "HTTP 401"
        assert exc_info.value.auth_location == config.BROKER_AUTH_LOCATION_URL

    @pytest.mark.asyncio
    async def test_invoke_tool_non_401_http_error_still_raises_http_status_error(self, server_config):
        """Non-401 HTTP errors from invoke_tool must still raise httpx.HTTPStatusError."""
        client = MCPConnectClient()
        mock_response = httpx.Response(
            500,
            json={"error": "internal"},
            request=httpx.Request("POST", "https://mcp-connect.example.com/bridge"),
        )

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.invoke_tool(server_config, "test_tool", {"param": "value"})

        assert exc_info.value.response.status_code == 500

    @pytest.mark.asyncio
    async def test_invoke_tool_validation_error(self, server_config):
        """Test handling of response validation errors."""
        client = MCPConnectClient()
        tool_name = "test_tool"
        tool_args = {"param1": "value1", "param2": 42}

        mock_response = MagicMock()
        mock_response.json.return_value = {"invalid": "response"}
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            with pytest.raises(ValueError, match="Invalid response from MCP-Connect"):
                await client.invoke_tool(server_config, tool_name, tool_args)

    @pytest.mark.asyncio
    async def test_invoke_tool_with_auth(self, server_config_with_auth, sample_tool_invocation_response):
        """Test tool invocation with authentication."""
        client = MCPConnectClient()
        tool_name = "test_tool"
        tool_args = {"param1": "value1", "param2": 42}

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tool_invocation_response
        mock_response.raise_for_status = MagicMock()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            await client.invoke_tool(server_config_with_auth, tool_name, tool_args)

            # Get expected bucket number from server config
            from codemie.service.mcp.client import _get_bucket_no

            bucket_no = _get_bucket_no(server_config_with_auth)

            # Verify request includes auth header and bucket header
            mock_client.return_value.__aenter__.return_value.post.assert_called_once_with(
                client.bridge_endpoint,
                json={
                    "method": "tools/call",
                    "serverPath": server_config_with_auth.command,
                    "args": server_config_with_auth.args,
                    "params": {"name": tool_name, "arguments": tool_args},
                    "env": server_config_with_auth.env,
                    "mcp_headers": {},
                    "single_usage": False,  # Added for lifecycle support
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer test-auth-token",
                    "X-MCP-Connect-Bucket": str(bucket_no),
                },
            )


class TestMCPConnectClientTimeout:
    """Tests for timeout handling."""

    def test_timeout_configuration(self):
        """Verify the client has proper timeout configuration."""
        client = MCPConnectClient()
        assert client.timeout.read == 300.0
        assert client.timeout.connect == 300.0

    @pytest.mark.asyncio
    async def test_list_tools_timeout(self, server_config):
        """Test behavior when list_tools request times out."""
        client = MCPConnectClient()

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.TimeoutException("Request timed out")
            )

            with pytest.raises(httpx.TimeoutException, match="Request timed out"):
                await client.list_tools(server_config)

    @pytest.mark.asyncio
    async def test_invoke_tool_timeout(self, server_config):
        """Test behavior when invoke_tool request times out."""
        client = MCPConnectClient()
        tool_name = "test_tool"
        tool_args = {"param1": "value1", "param2": 42}

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.TimeoutException("Request timed out")
            )

            with pytest.raises(httpx.TimeoutException, match="Request timed out"):
                await client.invoke_tool(server_config, tool_name, tool_args)


class TestMCPConnectClientBridgeEndpoint:
    """Tests for the _get_actual_bridge_endpoint_url method."""

    def test_bridge_endpoint_with_placeholder(self):
        """Test URL generation when bridge endpoint contains the {MCP_CONNECT_BUCKET} placeholder."""
        base_url = f"https://mcp-connect.example-{MCP_CONNECT_BUCKET_PLACEHOLDER}.com"
        client = MCPConnectClient(base_url=base_url)
        bucket_number = 5

        # Call the method and verify results
        actual_url = client._get_actual_bridge_endpoint_url(bucket_number)
        expected_url = "https://mcp-connect.example-5.com/bridge"

        assert actual_url == expected_url

    def test_bridge_endpoint_without_placeholder(self):
        """Test URL generation when bridge endpoint doesn't contain the placeholder."""
        base_url = "https://mcp-connect.example.com"
        client = MCPConnectClient(base_url=base_url)
        bucket_number = 5

        # Call the method and verify results
        actual_url = client._get_actual_bridge_endpoint_url(bucket_number)
        expected_url = "https://mcp-connect.example.com/bridge"

        assert actual_url == expected_url
        # Verify no modifications were made to the URL
        assert actual_url == client.bridge_endpoint
        assert str(bucket_number) not in actual_url

    def test_bridge_endpoint_various_bucket_numbers(self):
        """Test URL generation with different bucket number formats."""
        base_url = f"https://mcp-connect.example-{MCP_CONNECT_BUCKET_PLACEHOLDER}.com"
        client = MCPConnectClient(base_url=base_url)

        # Test single digit
        assert client._get_actual_bridge_endpoint_url(5) == "https://mcp-connect.example-5.com/bridge"

        # Test multiple digits
        assert client._get_actual_bridge_endpoint_url(42) == "https://mcp-connect.example-42.com/bridge"
        assert client._get_actual_bridge_endpoint_url(100) == "https://mcp-connect.example-100.com/bridge"


class TestMCPConnectClientBucketNumber:
    """Tests for the _get_bucket_no function."""

    def test_get_bucket_no_with_none_env(self):
        """Test bucket number generation when server_config.env is None."""
        from codemie.service.mcp.client import _get_bucket_no
        from codemie.configs import config

        server_config = MCPServerConfig(command="test", args=["arg1"], env=None, auth_token=None)

        bucket_no = _get_bucket_no(server_config)

        # Verify result is an integer and within valid range
        assert isinstance(bucket_no, int)
        assert 0 <= bucket_no < config.MCP_CONNECT_BUCKETS_COUNT

        # Verify consistency - same input should yield same bucket
        bucket_no2 = _get_bucket_no(server_config)
        assert bucket_no == bucket_no2

    def test_get_bucket_no_missing_bucket_key(self):
        """Test bucket number generation when BUCKET_KEY is not in env."""
        from codemie.service.mcp.client import _get_bucket_no
        from codemie.configs import config

        # Test with empty env
        server_config_empty = MCPServerConfig(command="test", args=["arg1"], env={}, auth_token=None)
        bucket_no_empty = _get_bucket_no(server_config_empty)
        assert isinstance(bucket_no_empty, int)
        assert 0 <= bucket_no_empty < config.MCP_CONNECT_BUCKETS_COUNT

        # Test with env containing other keys
        server_config_other = MCPServerConfig(
            command="test", args=["arg1"], env={"OTHER_KEY": "value", "ANOTHER_KEY": "test"}, auth_token=None
        )
        bucket_no_other = _get_bucket_no(server_config_other)
        assert isinstance(bucket_no_other, int)
        assert 0 <= bucket_no_other < config.MCP_CONNECT_BUCKETS_COUNT

    def test_get_bucket_no_hashes_repr_hidden_config_fields(self, monkeypatch):
        """Test bucket key generation keeps using fields hidden from MCPServerConfig repr."""
        from codemie.service.mcp import client

        captured_bucket_keys: list[str] = []

        def fake_hash_remainder(bucket_key: str) -> int:
            captured_bucket_keys.append(bucket_key)
            return 0

        monkeypatch.setattr(client, "_hash_remainder", fake_hash_remainder)
        server_config = MCPServerConfig(
            url="https://mcp-a.example.com/api/mcp",
            type="streamable-http",
            headers={"X-Trace": "trace-a"},
            env={},
        )

        assert client._get_bucket_no(server_config) == 0
        assert "https://mcp-a.example.com/api/mcp" in captured_bucket_keys[0]
        assert "X-Trace" in captured_bucket_keys[0]

    def test_bucket_number_range(self):
        """Test that bucket numbers are always within valid range."""
        from codemie.service.mcp.client import _get_bucket_no
        from codemie.configs import config

        test_cases = [
            {"BUCKET_KEY": "test1"},
            {"BUCKET_KEY": "very_long_key_value" * 1000},  # Very long value
            {"BUCKET_KEY": "special!@#$%^&*()chars"},  # Special characters
            {"BUCKET_KEY": ""},  # Empty string
            {"BUCKET_KEY": "🐍🔥"},  # Unicode characters
            {"BUCKET_KEY": "\n\t\r"},  # Whitespace and control characters
        ]

        for env in test_cases:
            server_config = MCPServerConfig(command="test", args=["arg1"], env=env, auth_token=None)

            # First invocation
            bucket_no = _get_bucket_no(server_config)
            assert isinstance(bucket_no, int)
            assert 0 <= bucket_no < config.MCP_CONNECT_BUCKETS_COUNT

            # Test consistency - same input should produce same bucket
            bucket_no2 = _get_bucket_no(server_config)
            assert bucket_no == bucket_no2, f"Inconsistent bucket numbers for input: {env}"

    def test_bucket_number_consistency_across_instances(self):
        """Test that identical configs get same bucket even with different instances."""
        from codemie.service.mcp.client import _get_bucket_no

        env = {"BUCKET_KEY": "test_value"}

        # Create two identical configs
        config1 = MCPServerConfig(command="test", args=["arg1"], env=env, auth_token=None)

        config2 = MCPServerConfig(command="test", args=["arg1"], env=env, auth_token=None)

        # Both should map to the same bucket
        bucket1 = _get_bucket_no(config1)
        bucket2 = _get_bucket_no(config2)
        assert bucket1 == bucket2


class TestHashRemainderBasicInputs:
    """Tests for basic input types for the _hash_remainder function."""

    def test_standard_strings(self):
        """Test standard ASCII string inputs."""
        test_cases = ["test", "example", "bucket"]
        for test_str in test_cases:
            result = _hash_remainder(test_str)
            assert isinstance(result, int)
            assert 0 <= result < config.MCP_CONNECT_BUCKETS_COUNT
            # Test consistency
            assert _hash_remainder(test_str) == result

    def test_empty_string(self):
        """Test empty string input."""
        result = _hash_remainder("")
        assert isinstance(result, int)
        assert 0 <= result < config.MCP_CONNECT_BUCKETS_COUNT
        # Test consistency
        assert _hash_remainder("") == result

    def test_single_character_strings(self):
        """Test single character string inputs."""
        test_cases = ["a", "1", "@"]
        for test_str in test_cases:
            result = _hash_remainder(test_str)
            assert isinstance(result, int)
            assert 0 <= result < config.MCP_CONNECT_BUCKETS_COUNT
            # Test consistency
            assert _hash_remainder(test_str) == result

    def test_whitespace_strings(self):
        """Test strings containing only whitespace."""
        test_cases = [" ", "   ", "\t", "\n"]
        for test_str in test_cases:
            result = _hash_remainder(test_str)
            assert isinstance(result, int)
            assert 0 <= result < config.MCP_CONNECT_BUCKETS_COUNT
            # Test consistency
            assert _hash_remainder(test_str) == result


class TestHashRemainderSpecialStrings:
    """Tests for special string types for the _hash_remainder function."""

    def test_unicode_strings(self):
        """Test Unicode string handling."""
        test_cases = [
            "привет",  # Cyrillic
            "你好",  # Chinese
            "مرحبا",  # Arabic
            "🐍",  # Emoji (Snake)
            "👋🏻",  # Emoji with skin tone modifier
            "🔥",  # Emoji (Fire)
            "hello世界",  # Mixed ASCII and Unicode
        ]
        for test_str in test_cases:
            result = _hash_remainder(test_str)
            assert isinstance(result, int)
            assert 0 <= result < config.MCP_CONNECT_BUCKETS_COUNT
            # Test consistency across different Python versions
            assert _hash_remainder(test_str) == result

    def test_special_characters(self):
        """Test strings with special characters."""
        test_cases = [
            "!@#$%^&*()",
            "\n\t\r",
            "  \n  @#$  ",
            "\\special\\path\\string",
            "http://example.com/path?query=value",
            "<html>tags</html>",
        ]
        for test_str in test_cases:
            result = _hash_remainder(test_str)
            assert isinstance(result, int)
            assert 0 <= result < config.MCP_CONNECT_BUCKETS_COUNT
            assert _hash_remainder(test_str) == result


class TestHashRemainderEdgeCases:
    """Tests for edge cases of the _hash_remainder function."""

    def test_extreme_string_lengths(self):
        """Test strings of various lengths including extremes."""
        test_cases = [
            "",  # Empty string
            "a" * 10000,  # Very long string
            "x",  # Single character
            "a" * 1000000,  # Extremely long string
        ]
        for test_str in test_cases:
            result = _hash_remainder(test_str)
            assert isinstance(result, int)
            assert 0 <= result < config.MCP_CONNECT_BUCKETS_COUNT
            # Verify consistency
            assert _hash_remainder(test_str) == result


class TestHashRemainderConsistency:
    """Tests for consistency of the _hash_remainder function."""

    def test_multiple_calls_consistency(self):
        """Test that multiple calls with the same input produce the same output."""
        test_str = "test_consistency"
        initial_result = _hash_remainder(test_str)

        # Multiple calls should return the same result
        for _ in range(1000):
            assert _hash_remainder(test_str) == initial_result

    def test_cross_instance_consistency(self):
        """Test consistency across different instances."""
        test_cases = ["cross_instance_test", "another_test_string", "🌟 special case 123"]

        # Store initial results
        initial_results = {test_str: _hash_remainder(test_str) for test_str in test_cases}

        # Verify results remain consistent in subsequent calls
        for test_str, initial_result in initial_results.items():
            assert _hash_remainder(test_str) == initial_result


class TestHashRemainderRange:
    """Tests for output range of the _hash_remainder function."""

    def test_output_range(self):
        """Test that all outputs fall within the valid range."""
        test_strings = [
            "range_test_1",
            "some_very_long_string_for_testing_" * 100,
            "12345",
            "!@#$%^&*()",
            "unicode_☺_test",
            "",
        ]

        for test_str in test_strings:
            result = _hash_remainder(test_str)
            assert 0 <= result < config.MCP_CONNECT_BUCKETS_COUNT


class TestHashRemainderDistribution:
    """Tests for distribution characteristics of the _hash_remainder function."""

    def test_basic_distribution(self):
        """Test basic distribution of hash values."""
        # Generate test strings
        test_strings = [f"test_string_{i}" for i in range(1000)]

        # Collect results
        results = [_hash_remainder(s) for s in test_strings]

        # Check distribution properties
        unique_values = set(results)

        # Should use multiple buckets
        assert len(unique_values) > 1
        # All values should be in valid range
        assert all(0 <= x < config.MCP_CONNECT_BUCKETS_COUNT for x in results)

    def test_similar_strings_distribution(self):
        """Test distribution for similar strings."""
        # Generate similar strings with small variations
        base_string = "test_string_"
        similar_strings = [f"{base_string}{i:03d}" for i in range(100)]

        # Collect results
        results = [_hash_remainder(s) for s in similar_strings]
        unique_values = set(results)

        # Similar strings should still distribute across multiple buckets
        assert len(unique_values) > 1
        # All values should be in valid range
        assert all(0 <= x < config.MCP_CONNECT_BUCKETS_COUNT for x in results)
