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

from dataclasses import dataclass

from codemie.configs import logger
from codemie.core.exceptions import MCPAuthenticationRequiredException
from codemie.rest_api.models.assistant import MCPServerDetails, MCPServerCheckRequest
from codemie.rest_api.models.mcp_config import MCPTestStatus
from codemie.rest_api.security.user import User
from codemie.service.mcp.models import MCPErrorCategory, MCPToolLoadException
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException

NO_TOOLS_CATEGORY = "no_tools"
UNKNOWN_CATEGORY = "unknown"
MISSING_CREDENTIALS_CATEGORY = "missing_credentials"
RESTRICTED_MODE_CATEGORY = "restricted_mode"


@dataclass(frozen=True)
class MCPTestResult:
    """Structured outcome of an MCP server connectivity test."""

    status: MCPTestStatus
    message: str
    error_category: str | None = None
    tools_count: int | None = None


def _get_server_command(mcp_server: MCPServerDetails) -> str | None:
    """Return the MCP server's launch command, checking top-level field before config."""
    if mcp_server.command:
        return mcp_server.command
    if mcp_server.config and mcp_server.config.command:
        return mcp_server.config.command
    return None


def _build_hint_for_category(category: MCPErrorCategory, is_npx: bool) -> str:
    """Return a category-specific diagnostic hint for the user."""
    if category == MCPErrorCategory.CONNECTION_CLOSED:
        if is_npx:
            return (
                "The npx server closed the MCP connection. Common causes:\n"
                "1. npm may write progress or error output to stdout — "
                "MCP requires clean stdout (only JSON-RPC messages). "
                "Run 'npx <package> --version' locally to check for unexpected output.\n"
                "2. The package may not exist or may require a specific environment. "
                "Confirm the package name and that all required environment variables are set.\n"
                "3. If this server downloads packages on first use, the MCP bridge's "
                "MCP_CONNECT_INIT_TIMEOUT (default 30s) may expire before initialization completes."
            )
        return "Check that the server process starts correctly and produces only valid MCP JSON-RPC output."

    if category == MCPErrorCategory.TIMEOUT:
        return (
            "The MCP bridge timed out waiting for server initialization. "
            "If this server downloads packages on first use, "
            "increase MCP_CONNECT_INIT_TIMEOUT on the bridge service."
        )

    if category == MCPErrorCategory.STARTUP_FAILURE:
        return (
            "The server process failed to start. "
            "Verify the command path, that all required environment variables are set "
            "in the MCP server configuration, and that the package or binary is accessible."
        )

    if category == MCPErrorCategory.AUTH_REQUIRED:
        return (
            "Authentication is required. "
            "Check that authentication tokens or credentials are present "
            "in the MCP server environment configuration."
        )

    return "Please, check the configuration."


class MCPServerTester:
    mcp_server: MCPServerDetails

    def __init__(self, request: MCPServerCheckRequest, user: User):
        self.mcp_server = request.mcp_server
        self.user = user

    def run_test(self) -> MCPTestResult:
        """Run the connectivity test and return a structured result.

        Authentication exceptions are re-raised so callers can trigger the auth flow.
        """
        try:
            tools = MCPToolkitService.get_mcp_server_tools(
                mcp_servers=[self.mcp_server], user_id=self.user.id, mcp_server_single_usage=True
            )

            if not tools:
                logger.warning("Connection to '%s' succeeded but no tools were retrieved.", self.mcp_server.name)
                return MCPTestResult(
                    status=MCPTestStatus.WARNING,
                    message=(
                        f"Connected to '{self.mcp_server.name}' but no tools were retrieved. "
                        "Please check that the MCP server is running and has tools configured."
                    ),
                    error_category=NO_TOOLS_CATEGORY,
                    tools_count=0,
                )

            logger.info("Testing passed for MCP tools from %s server. Tools count=%d", self.mcp_server.name, len(tools))
            return MCPTestResult(status=MCPTestStatus.SUCCESS, message='Success', tools_count=len(tools))
        except BrokerAuthRequiredException:
            raise
        except MCPAuthenticationRequiredException:
            raise
        except MCPToolLoadException as e:
            is_npx = _get_server_command(self.mcp_server) == "npx"
            hint = _build_hint_for_category(e.category, is_npx)
            return MCPTestResult(status=MCPTestStatus.FAILURE, message=f"{e}.\n{hint}", error_category=e.category.value)
        except Exception as e:
            return MCPTestResult(
                status=MCPTestStatus.FAILURE,
                message=f"{e}.\nPlease, check the configuration.",
                error_category=UNKNOWN_CATEGORY,
            )

    def test(self) -> tuple[bool, str]:
        result = self.run_test()
        return result.status == MCPTestStatus.SUCCESS, result.message
