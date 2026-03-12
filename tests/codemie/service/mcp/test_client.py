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

from codemie.service.mcp.client import MCPConnectClient, MCP_CONNECT_BUCKET_PLACEHOLDER
from codemie.service.mcp.models import (
    MCPServerConfig,
    MCPToolDefinition,
    MCPToolInvocationResponse,
    MCPExecutionContext,
)
from codemie.service.mcp.client import _hash_remainder
from codemie.configs import config


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


class TestMCPConnectClientListTools:
    """Tests for the list_tools method."""

    @pytest.mark.asyncio
    async def test_list_tools_success(self, server_config, sample_tools_response):
        """Test successful retrieval of tools list."""
        client = MCPConnectClient()

        mock_response = MagicMock()
        mock_response.json.return_value = sample_tools_response
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
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

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_http_response)

            with pytest.raises(ValueError, match="Test error message"):
                await client.list_tools(server_config)

    @pytest.mark.asyncio
    async def test_list_tools_json_error(self, server_config):
        """Test handling of JSON decode errors."""
        client = MCPConnectClient()

        mock_response = MagicMock()
        mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "Not valid JSON"

        with patch("httpx.AsyncClient") as mock_client:
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

        with patch("httpx.AsyncClient") as mock_client:
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

        with patch("httpx.AsyncClient") as mock_client:
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

        with patch("httpx.AsyncClient") as mock_client:
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

        with patch("httpx.AsyncClient") as mock_client:
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

        with patch("httpx.AsyncClient") as mock_client:
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

        with patch("httpx.AsyncClient") as mock_client:
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

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            with pytest.raises(httpx.HTTPStatusError):
                await client.invoke_tool(server_config, tool_name, tool_args)

    @pytest.mark.asyncio
    async def test_invoke_tool_validation_error(self, server_config):
        """Test handling of response validation errors."""
        client = MCPConnectClient()
        tool_name = "test_tool"
        tool_args = {"param1": "value1", "param2": 42}

        mock_response = MagicMock()
        mock_response.json.return_value = {"invalid": "response"}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
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

        with patch("httpx.AsyncClient") as mock_client:
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

        with patch("httpx.AsyncClient") as mock_client:
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

        with patch("httpx.AsyncClient") as mock_client:
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
