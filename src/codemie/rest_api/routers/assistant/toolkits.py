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

from pydantic import BaseModel

from codemie_tools.base.models import ToolKit
from fastapi import APIRouter, status, Request, Depends

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException
from codemie.rest_api.models.assistant import (
    Context,
    MCPServerDetails,
)
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException
from codemie.service.tools import ToolsInfoService
from codemie.service.tools.plugin_tools_info_service import PluginToolsInfoService, PluginToolsInfoServiceError

from codemie.service.subagents.builtin_subagents_registry import BuiltinSubagentsRegistry


router = APIRouter()


class BuiltinSubagentListItem(BaseModel):
    id: str
    display_name: str


@router.get(
    "/assistants/tools",
    status_code=status.HTTP_200_OK,
    response_model=list[ToolKit],
    response_model_by_alias=True,
)
def get_tools(request: Request, user: User = Depends(authenticate)):
    """
    Returns all available tools
    """
    tools_info = ToolsInfoService.get_tools_info(show_for_ui=True, user=user)
    return tools_info


@router.get(
    "/assistants/hedgeable_tools",
    status_code=status.HTTP_200_OK,
    response_model=list[ToolKit],
    response_model_by_alias=True,
)
def get_hedgeable_tools(request: Request, user: User = Depends(authenticate)):
    """Returns tools that support request hedging (extend CodeMieHedgeTool)."""
    return ToolsInfoService.get_hedgeable_tools_info(user=user)


@router.get(
    "/assistants/builtin_subagents",
    status_code=status.HTTP_200_OK,
    response_model=list[BuiltinSubagentListItem],
    response_model_by_alias=True,
)
def list_builtin_subagents(user: User = Depends(authenticate)):
    """Return available builtin subagents catalog."""
    return [
        BuiltinSubagentListItem(id=item.id.value, display_name=item.display_name)
        for item in BuiltinSubagentsRegistry.list_available()
    ]


@router.get(
    "/assistants/plugin_tools",
    status_code=status.HTTP_200_OK,
    response_model=list[ToolKit],
    response_model_by_alias=True,
)
def get_plugin_tools(request: Request, user: User = Depends(authenticate), plugin_setting_id: Optional[str] = None):
    """
    Returns plugin tools from plugin integration.

    Args:
        plugin_setting_id: Optional settings ID containing plugin integration credentials.
                          If not provided, attempts to use default plugin setting for user.

    Returns:
        List containing the plugin toolkit with actual plugin tools.
        Returns empty toolkit (with empty tools array) if no plugin tools available.

    Example:
        GET /v1/assistants/plugin_tools
        GET /v1/assistants/plugin_tools?plugin_setting_id=abc-123
    """
    try:
        plugin_toolkit_info = PluginToolsInfoService.get_plugin_toolkit_info(
            plugin_setting_id=plugin_setting_id, user=user, project_name=None
        )

        return [plugin_toolkit_info]
    except PluginToolsInfoServiceError as e:
        logger.error(f"Failed getting plugin tools: {e.message}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=e.message,
            details=e.details,
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error getting plugin tools: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve plugin tools",
            details=f"An unexpected error occurred: {str(e)}",
            help="Please try again or contact support if the issue persists.",
        ) from e


@router.post(
    "/assistants/mcp_tools",
    status_code=status.HTTP_200_OK,
    response_model=list[ToolKit],
    response_model_by_alias=True,
)
def get_mcp_tools(
    request: MCPServerDetails,
    user: User = Depends(authenticate),
):
    """
    Returns MCP tools from MCP server configuration.

    Accepts an MCP server configuration and returns the list of available tools
    from that MCP server. This allows users to preview tools before adding
    an MCP server to an assistant.

    Args:
        request: MCP server configuration including command, args, and environment variables

    Returns:
        List containing the MCP toolkit with actual tools from the server.
        Returns empty toolkit (with empty tools array) if no tools are available.

    Example:
        POST /v1/assistants/mcp_tools
        {
            "name": "GitHub MCP",
            "description": "GitHub API tools",
            "config": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "ghp_xxx"}
            }
        }
    """
    from codemie.service.tools.mcp_tools_info_service import MCPToolsInfoService, MCPToolsInfoServiceError

    try:
        mcp_server = request.model_copy(update={"enabled": True})

        toolkit_info = MCPToolsInfoService.get_mcp_toolkit_info(
            mcp_server_config=mcp_server, user=user, project_name=None
        )

        return [toolkit_info]

    except BrokerAuthRequiredException:
        raise
    except MCPAuthenticationRequiredException:
        raise
    except MCPToolsInfoServiceError as e:
        logger.error(f"Failed getting MCP tools: {e.message}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=e.message,
            details=e.details,
            help=(
                "Please check the MCP server configuration (command, args, environment variables) "
                "and ensure the MCP server is accessible."
            ),
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error getting MCP tools: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve MCP tools",
            details=f"An unexpected error occurred: {str(e)}",
            help="Please try again or contact support if the issue persists.",
        ) from e


@router.get(
    "/assistants/context",
    status_code=status.HTTP_200_OK,
    response_model=list[dict],
)
def get_assistants_context(project_name: str, user: User = Depends(authenticate)):
    """
    Returns all available assistants context (index info - repo or kb)
    """
    result = IndexInfo.get_all_for_user(user=user, project_name=project_name)

    response = [
        {
            "name": item.repo_name,
            "context_type": Context.index_info_type(item),
            "id": item.id,
        }
        for item in result
    ]

    return response
