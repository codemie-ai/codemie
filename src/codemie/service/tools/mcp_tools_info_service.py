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

from typing import Optional
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.rest_api.security.user import User
from codemie.configs import logger
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException


class MCPToolsInfoServiceError(Exception):
    """Exception raised when MCPToolsInfoService operations fail."""

    def __init__(self, message: str, details: Optional[str] = None):
        self.message = message
        self.details = details
        super().__init__(message)


class MCPToolsInfoService:
    """Service for retrieving actual MCP tools information from MCP servers"""

    @staticmethod
    def get_mcp_toolkit_info(
        mcp_server_config: MCPServerDetails, user: User, project_name: Optional[str] = None
    ) -> dict:
        """
        Get actual list of current MCP tools data
        for specific MCP server configuration in ToolKit format

        Args:
            mcp_server_config: MCP server configuration including command, args, and env
            user: User making the request
            project_name: Optional project name for credential resolution

        Returns:
            ToolKit-formatted dict with MCP tools, or empty toolkit if no tools available

        Raises:
            ValueError: If MCP server configuration is invalid or tools cannot be retrieved
        """
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        try:
            # Get tools from MCP server
            tools = MCPToolkitService.get_mcp_server_tools(
                mcp_servers=[mcp_server_config],
                user_id=user.id,
                project_name=project_name,
            )

            if not tools:
                logger.info(
                    f"No MCP tools found for server '{mcp_server_config.name}'. "
                    f"MCP server may not have tools configured or may be unreachable."
                )
                raise MCPToolsInfoServiceError(
                    f"No MCP tools found for server '{mcp_server_config.name}'",
                    "Please check that the MCP server is running, tools are available, and the configuration is correct.",
                )

            # Convert MCP tools to toolkit format
            tools_info = []
            for tool in tools:
                if not tool.name:
                    continue

                tools_info.append(
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "label": tool.name.replace("_", " ").title(),
                    }
                )

            logger.info(f"Retrieved {len(tools_info)} MCP tools from server '{mcp_server_config.name}'")

            return {"toolkit": "MCP", "label": f"{mcp_server_config.name} Tools", "tools": tools_info}

        except MCPToolsInfoServiceError:
            raise
        except BrokerAuthRequiredException:
            raise
        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"Error retrieving MCP toolkit info from '{mcp_server_config.name}': {error_msg}", exc_info=True
            )
            raise MCPToolsInfoServiceError(
                f"Could not retrieve MCP tools from '{mcp_server_config.name}'", error_msg
            ) from e

    @staticmethod
    def _get_empty_toolkit(server_name: str) -> dict:
        """
        Return empty toolkit structure for MCP.

        Args:
            server_name: Name of the MCP server

        Returns:
            Empty toolkit dict
        """
        return {"toolkit": "MCP", "label": f"{server_name} Tools", "tools": []}
