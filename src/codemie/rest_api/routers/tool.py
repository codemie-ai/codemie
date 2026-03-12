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

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, status

from codemie_tools.base import toolkit_provider
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.tool import SchemaField, ToolInvokeRequest, ToolInvokeResponse, ToolSchemaResponse
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.tools.tools_info_service import ToolsInfoService
from codemie.service.tools.tool_execution_service import ToolExecutionService
from codemie.service.tools.discovery import ToolDiscoveryService

router = APIRouter(tags=["Tool"], prefix="/v1", dependencies=[Depends(authenticate)])


@router.get("/tools", response_model=List[str])
async def get_tools(user: User = Depends(authenticate)):
    """
    Get list of all available tool names (backward compatible)
    """
    try:
        toolkits = ToolsInfoService.get_tools_info(user=user)
        tool_names = [tool.get("name") for toolkit in toolkits for tool in toolkit.get("tools")]
        return tool_names
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR, message="Failed to retrieve tools information", details=str(e)
        )


@router.get("/tools/configs", response_model=List[Dict[str, Any]])
async def get_tools_configs(user: User = Depends(authenticate)):
    """
    Get all available tool configuration schemas from toolkit_provider.

    Returns a list of tool configuration schemas, where each schema contains:
    - Config name as key
    - Config details including class name and field definitions with metadata
    """
    try:
        configs = toolkit_provider.get_available_tools_configs_info()
        return configs
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve tool configurations",
            details=str(e),
        )


@router.get("/tools/{tool_name}/schema", response_model=ToolSchemaResponse)
async def get_tool_schema(
    tool_name: str, user: User = Depends(authenticate), setting_id: Optional[str] = None
) -> ToolSchemaResponse:
    """
    Get configuration and arguments schema for a specific tool by name.

    Args:
        tool_name: Name of the tool to get schema for
        user: Authenticated user (from dependency)
        setting_id: Optional setting ID for runtime tool discovery (e.g., plugin tools)

    Returns:
        ToolSchemaResponse with credentials and arguments schema

    Raises:
        ExtendedHTTPException: If tool not found (404)
    """
    formatted_schema = ToolDiscoveryService.get_formatted_tool_schema(
        tool_name=tool_name, user=user, setting_id=setting_id
    )

    if not formatted_schema:
        raise ExtendedHTTPException(code=status.HTTP_404_NOT_FOUND, message=f"Tool '{tool_name}' not found")

    # Convert formatted schema to response model with SchemaField objects
    creds_schema = {
        field_name: SchemaField(type=field_info['type'], required=field_info['required'])
        for field_name, field_info in formatted_schema.creds_schema.items()
    }

    args_schema = {
        field_name: SchemaField(type=field_info['type'], required=field_info['required'])
        for field_name, field_info in formatted_schema.args_schema.items()
    }

    return ToolSchemaResponse(tool_name=tool_name, creds_schema=creds_schema, args_schema=args_schema)


@router.post(
    "/tools/{tool_name}/invoke",
    status_code=status.HTTP_200_OK,
    response_model=ToolInvokeResponse,
)
def invoke_tool(request: ToolInvokeRequest, tool_name: str, user: User = Depends(authenticate)):
    try:
        output = ToolExecutionService.invoke(request, tool_name, user)
        return ToolInvokeResponse(output=str(output))
    except Exception as e:
        return ToolInvokeResponse(error=str(e))
