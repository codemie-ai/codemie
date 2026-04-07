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
        """BrokerAuthRequiredException must not be re-wrapped as MCPToolsInfoServiceError."""
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

    def test_broker_auth_required_auth_location_preserved(self, mock_user, mcp_server_config):
        """auth_location on the re-raised exception must be intact."""
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
