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

"""Unit tests for MCPServerTester."""

from unittest.mock import Mock, patch

import pytest

from codemie.core.exceptions import MCPAuthenticationRequiredException
from codemie.rest_api.models.assistant import MCPServerCheckRequest, MCPServerConfig, MCPServerDetails
from codemie.rest_api.models.mcp_config import MCPTestStatus
from codemie.rest_api.security.user import User
from codemie.service.mcp.mcp_tester import MCPServerTester, MCPTestResult, _build_hint_for_category, _get_server_command
from codemie.service.mcp.models import MCPBridgeError, MCPErrorCategory, MCPToolLoadException
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException

_PATCH_TARGET = "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools"


@pytest.fixture
def mock_user():
    user = Mock(spec=User)
    user.id = "test-user-id"
    return user


@pytest.fixture
def mcp_server():
    return MCPServerDetails(name="Test MCP", enabled=True)


@pytest.fixture
def tester(mcp_server, mock_user):
    request = MCPServerCheckRequest(mcp_server=mcp_server)
    return MCPServerTester(request=request, user=mock_user)


class TestMCPServerTesterTest:
    """Verify MCPServerTester.test() behaviour across all outcomes."""

    def test_returns_false_when_tools_empty(self, tester):
        """Empty tools list must produce a False result with the server name in the message."""
        with patch(_PATCH_TARGET, return_value=[]):
            success, message = tester.test()

        assert success is False
        assert "Test MCP" in message

    def test_returns_true_when_tools_found(self, tester):
        """Non-empty tools list must produce (True, 'Success')."""
        with patch(_PATCH_TARGET, return_value=[Mock()]):
            success, message = tester.test()

        assert success is True
        assert message == "Success"

    def test_returns_false_on_generic_exception(self, tester):
        """Generic exceptions must be caught and returned as (False, msg)."""
        with patch(_PATCH_TARGET, side_effect=RuntimeError("connection refused")):
            success, message = tester.test()

        assert success is False
        assert "connection refused" in message

    def test_reraises_broker_auth_exception(self, tester):
        """BrokerAuthRequiredException must propagate unchanged."""
        exc = BrokerAuthRequiredException(
            message="Broker token exchange failed",
            auth_location="https://auth.example.com/login",
            details="HTTP 401",
        )
        with patch(_PATCH_TARGET, side_effect=exc):
            with pytest.raises(BrokerAuthRequiredException) as exc_info:
                tester.test()

        assert exc_info.value is exc

    def test_reraises_mcp_auth_exception(self, tester):
        """MCPAuthenticationRequiredException must propagate unchanged."""
        exc = MCPAuthenticationRequiredException({"error": "authentication_required", "servers": []})
        with patch(_PATCH_TARGET, side_effect=exc):
            with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                tester.test()

        assert exc_info.value is exc

    def test_calls_get_mcp_server_tools_with_single_usage_true(self, tester):
        """get_mcp_server_tools must be called with mcp_server_single_usage=True."""
        with patch(_PATCH_TARGET, return_value=[Mock()]) as mock_get_tools:
            tester.test()

        assert mock_get_tools.call_args.kwargs.get("mcp_server_single_usage") is True


class TestMCPServerTesterRunTest:
    """run_test() returns a structured MCPTestResult for every outcome."""

    def test_empty_tools_returns_warning_no_tools(self, tester):
        with patch(_PATCH_TARGET, return_value=[]):
            result = tester.run_test()

        assert isinstance(result, MCPTestResult)
        assert result.status == MCPTestStatus.WARNING
        assert result.error_category == "no_tools"
        assert result.tools_count == 0
        assert "Test MCP" in result.message

    def test_tools_found_returns_success_with_count(self, tester):
        with patch(_PATCH_TARGET, return_value=[Mock(), Mock()]):
            result = tester.run_test()

        assert result.status == MCPTestStatus.SUCCESS
        assert result.message == "Success"
        assert result.error_category is None
        assert result.tools_count == 2

    @pytest.mark.parametrize("category", list(MCPErrorCategory))
    def test_tool_load_failure_carries_category_value(self, tester, category):
        exc = MCPToolLoadException("Test MCP", MCPBridgeError("boom", status_code=500))
        exc.category = category

        with patch(_PATCH_TARGET, side_effect=exc):
            result = tester.run_test()

        assert result.status == MCPTestStatus.FAILURE
        assert result.error_category == category.value
        assert result.tools_count is None

    def test_generic_failure_has_unknown_category(self, tester):
        with patch(_PATCH_TARGET, side_effect=RuntimeError("connection refused")):
            result = tester.run_test()

        assert result.status == MCPTestStatus.FAILURE
        assert result.error_category == "unknown"
        assert "connection refused" in result.message

    def test_run_test_reraises_broker_auth_exception(self, tester):
        exc = BrokerAuthRequiredException(message="m", auth_location="https://a", details="d")
        with patch(_PATCH_TARGET, side_effect=exc):
            with pytest.raises(BrokerAuthRequiredException):
                tester.run_test()

    def test_run_test_reraises_mcp_auth_exception(self, tester):
        exc = MCPAuthenticationRequiredException({"error": "authentication_required", "servers": []})
        with patch(_PATCH_TARGET, side_effect=exc):
            with pytest.raises(MCPAuthenticationRequiredException):
                tester.run_test()


class TestGetServerCommand:
    def test_returns_top_level_command(self):
        server = MCPServerDetails(name="test-server", command="npx")
        assert _get_server_command(server) == "npx"

    def test_falls_back_to_config_command(self):
        server = MCPServerDetails(name="test-server", config=MCPServerConfig(command="uvx"))
        assert _get_server_command(server) == "uvx"

    def test_returns_none_when_both_absent(self):
        server = MCPServerDetails(name="test-server")
        assert _get_server_command(server) is None


class TestBuildHintForCategory:
    def test_connection_closed_with_npx_mentions_stdout(self):
        hint = _build_hint_for_category(MCPErrorCategory.CONNECTION_CLOSED, True)
        assert "stdout" in hint.lower()

    def test_connection_closed_with_npx_mentions_mcp_connect_init_timeout(self):
        hint = _build_hint_for_category(MCPErrorCategory.CONNECTION_CLOSED, True)
        assert "MCP_CONNECT_INIT_TIMEOUT" in hint

    def test_connection_closed_without_npx_is_generic(self):
        hint = _build_hint_for_category(MCPErrorCategory.CONNECTION_CLOSED, False)
        assert "stdout" not in hint.lower()
        assert "MCP_CONNECT_INIT_TIMEOUT" not in hint

    def test_timeout_mentions_mcp_connect_init_timeout(self):
        hint = _build_hint_for_category(MCPErrorCategory.TIMEOUT, True)
        assert "MCP_CONNECT_INIT_TIMEOUT" in hint

    def test_startup_failure_mentions_env_vars_or_command(self):
        hint = _build_hint_for_category(MCPErrorCategory.STARTUP_FAILURE, True)
        assert "env" in hint.lower() or "command" in hint.lower() or "variable" in hint.lower()

    def test_auth_required_mentions_authentication(self):
        hint = _build_hint_for_category(MCPErrorCategory.AUTH_REQUIRED, True)
        assert "auth" in hint.lower() or "token" in hint.lower() or "credential" in hint.lower()

    def test_unknown_returns_generic_fallback(self):
        hint = _build_hint_for_category(MCPErrorCategory.UNKNOWN, False)
        assert "Please, check the configuration." in hint


class TestMCPServerTesterHintIntegration:
    """EPMCDME-13900: MCPServerTester.test() must build a category-specific hint
    when an MCPToolLoadException propagates from get_mcp_server_tools."""

    def test_mcp_tool_load_exception_uses_category_hint(self, tester):
        original = MCPBridgeError("simulated bridge error", status_code=500)
        exc = MCPToolLoadException("Test MCP", original)
        exc.category = MCPErrorCategory.TIMEOUT

        with patch(_PATCH_TARGET, side_effect=exc):
            success, message = tester.test()

        assert success is False
        assert "MCP_CONNECT_INIT_TIMEOUT" in message


class TestMCPServerTesterNpxRegression:
    """AC5 regression: exercises MCPServerDetails with command='npx' through the full hint path."""

    @pytest.fixture
    def npx_tester(self, mock_user):
        server = MCPServerDetails(name="Test MCP", command="npx")
        request = MCPServerCheckRequest(mcp_server=server)
        return MCPServerTester(request=request, user=mock_user)

    def test_bridge_500_connection_closed_npx_returns_npx_hint(self, npx_tester):
        original = MCPBridgeError("Failed to create MCP client: McpError: Connection closed", status_code=500)
        exc = MCPToolLoadException("Test MCP", original)

        with patch(_PATCH_TARGET, side_effect=exc):
            success, message = npx_tester.test()

        assert success is False
        assert exc.category == MCPErrorCategory.CONNECTION_CLOSED
        assert "stdout" in message.lower()

    def test_bridge_504_connection_closed_body_npx_returns_timeout_hint(self, npx_tester):
        """Primary npx failure mode: bridge 504 with 'connection closed' body must classify
        as TIMEOUT (status-code-first rule) and return the MCP_CONNECT_INIT_TIMEOUT hint."""
        original = MCPBridgeError("McpError: Connection closed", status_code=504)
        exc = MCPToolLoadException("Test MCP", original)

        with patch(_PATCH_TARGET, side_effect=exc):
            success, message = npx_tester.test()

        assert success is False
        assert exc.category == MCPErrorCategory.TIMEOUT
        assert "MCP_CONNECT_INIT_TIMEOUT" in message

    def test_bridge_500_enoent_npx_returns_startup_failure_hint(self, npx_tester):
        original = MCPBridgeError(
            "ENOENT: no such file or directory, open '/tmp/node_modules/some-mcp'", status_code=500
        )
        exc = MCPToolLoadException("Test MCP", original)

        with patch(_PATCH_TARGET, side_effect=exc):
            success, message = npx_tester.test()

        assert success is False
        assert exc.category == MCPErrorCategory.STARTUP_FAILURE
        assert "env" in message.lower() or "command" in message.lower() or "variable" in message.lower()


class TestMissingCredentialsCategory:
    def test_constant_value(self):
        from codemie.service.mcp.mcp_tester import MISSING_CREDENTIALS_CATEGORY

        assert MISSING_CREDENTIALS_CATEGORY == "missing_credentials"
