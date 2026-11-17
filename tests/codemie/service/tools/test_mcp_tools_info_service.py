# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Unit tests for MCPToolsInfoService."""

from unittest.mock import Mock, patch

import pytest

from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.rest_api.security.user import User
from codemie.service.mcp.models import MCPMissingCredentialsError
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException
from codemie.service.tools.mcp_tools_info_service import MCPToolsInfoService, MCPToolsInfoServiceError


@pytest.fixture
def mock_user():
    user = Mock(spec=User)
    user.id = "test-user-id"
    return user


@pytest.fixture
def mcp_server_config():
    return MCPServerDetails(name="Test MCP", enabled=True)


class TestMCPToolsInfoServiceBrokerAuth:
    """Verify BrokerAuthRequiredException is not swallowed by get_mcp_toolkit_info."""

    def test_broker_auth_required_propagates_unchanged(self, mock_user, mcp_server_config):
        """BrokerAuthRequiredException must propagate as-is with all original attributes intact."""
        exc = BrokerAuthRequiredException(
            message="Broker token exchange failed with HTTP 502",
            auth_location="https://auth.example.com/login",
            details="HTTP 502",
        )

        with patch(
            "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools",
            side_effect=exc,
        ):
            with pytest.raises(BrokerAuthRequiredException) as exc_info:
                MCPToolsInfoService.get_mcp_toolkit_info(
                    mcp_server_config=mcp_server_config,
                    user=mock_user,
                )

        assert exc_info.value is exc
        assert exc_info.value.auth_location == "https://auth.example.com/login"
        assert exc_info.value.message == "Broker token exchange failed with HTTP 502"

    def test_other_exceptions_still_wrapped_as_service_error(self, mock_user, mcp_server_config):
        """Non-broker exceptions must still be wrapped as MCPToolsInfoServiceError."""
        with patch(
            "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools",
            side_effect=RuntimeError("connection refused"),
        ):
            with pytest.raises(MCPToolsInfoServiceError) as exc_info:
                MCPToolsInfoService.get_mcp_toolkit_info(
                    mcp_server_config=mcp_server_config,
                    user=mock_user,
                )

        assert "Test MCP" in exc_info.value.message
        assert "connection refused" in exc_info.value.details

    def test_missing_credentials_named_in_service_error(self, mock_user, mcp_server_config):
        """A missing required env var is reported by name, not as a generic retrieval failure."""
        with patch(
            "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools",
            side_effect=MCPMissingCredentialsError("Test MCP", ["TOKEN"]),
        ):
            with pytest.raises(MCPToolsInfoServiceError) as exc_info:
                MCPToolsInfoService.get_mcp_toolkit_info(
                    mcp_server_config=mcp_server_config,
                    user=mock_user,
                )

        assert exc_info.value.message == "Missing required credentials: TOKEN"
        assert "Test MCP" in exc_info.value.details


class TestMCPToolsInfoServiceSingleUsageMode:
    """Verify get_mcp_toolkit_info uses mcp_server_single_usage=True."""

    def test_get_mcp_server_tools_called_with_single_usage_true(self, mock_user, mcp_server_config):
        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"

        with patch(
            "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools",
            return_value=[mock_tool],
        ) as mock_get_tools:
            MCPToolsInfoService.get_mcp_toolkit_info(
                mcp_server_config=mcp_server_config,
                user=mock_user,
            )

        assert mock_get_tools.call_args.kwargs.get("mcp_server_single_usage") is True

    def test_get_mcp_server_tools_called_allowing_inline_credentials(self, mock_user, mcp_server_config):
        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"

        with patch(
            "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools",
            return_value=[mock_tool],
        ) as mock_get_tools:
            MCPToolsInfoService.get_mcp_toolkit_info(
                mcp_server_config=mcp_server_config,
                user=mock_user,
            )

        assert mock_get_tools.call_args.kwargs["allow_inline_credentials"] is True

    def test_success_path_returns_toolkit_dict(self, mock_user, mcp_server_config):
        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"

        with patch(
            "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools",
            return_value=[mock_tool],
        ):
            result = MCPToolsInfoService.get_mcp_toolkit_info(
                mcp_server_config=mcp_server_config,
                user=mock_user,
            )

        assert result["toolkit"] == "MCP"
        assert "tools" in result
        assert len(result["tools"]) == 1
        assert result["tools"][0]["name"] == "test_tool"

    def test_empty_tools_raises_service_error(self, mock_user, mcp_server_config):
        with patch(
            "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools",
            return_value=[],
        ):
            with pytest.raises(MCPToolsInfoServiceError):
                MCPToolsInfoService.get_mcp_toolkit_info(
                    mcp_server_config=mcp_server_config,
                    user=mock_user,
                )
