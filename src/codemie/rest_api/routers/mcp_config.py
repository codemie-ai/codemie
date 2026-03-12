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
REST API router for MCP Server Configuration Catalog.

Provides CRUD endpoints for managing shareable MCP server configurations.
"""

from typing import Optional
from fastapi import APIRouter, status, Depends, Query

from codemie.configs.logger import logger
from codemie.rest_api.models.mcp_config import (
    MCPConfigCreateRequest,
    MCPConfigUpdateRequest,
    MCPConfigResponse,
    MCPConfigListResponse,
)
from codemie.rest_api.security.authentication import authenticate, admin_access_only
from codemie.rest_api.security.user import User
from codemie.service.mcp_config_service import MCPConfigService

router = APIRouter(
    tags=["MCP Configuration"],
    prefix="/v1",
    dependencies=[],
)


@router.post(
    "/mcp-configs",
    status_code=status.HTTP_201_CREATED,
    response_model=MCPConfigResponse,
    summary="Create MCP Configuration",
    description="Create a new MCP server configuration in the catalog (Admin only)",
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)
def create_mcp_config(
    request: MCPConfigCreateRequest,
    user: User = Depends(authenticate),
) -> MCPConfigResponse:
    """
    Create a new MCP server configuration.

    This endpoint allows users to add new MCP server configurations to the catalog.
    These configurations can be shared (if marked as public) and reused across multiple assistants.

    **Request Body:**
    - **name**: Name of the MCP server (required, unique per user/project)
    - **description**: Description of what the server does (optional)
    - **server_home_url**: Link to MCP server documentation (optional)
    - **source_url**: Link to source code repository (optional)
    - **logo_url**: URL to server logo/icon (optional)
    - **categories**: List of categories (max 3) for filtering (optional)
    - **config**: MCP server configuration object (required)
      - **command** or **url**: Either command or URL must be provided
      - **args**: List of command arguments (optional)
      - **headers**: HTTP headers for URL-based servers (optional)
      - **env**: Environment variables (optional)
      - **type**: Transport type (optional)
      - **auth_token**: Authentication token (optional)
      - **single_usage**: Whether server is single-use or persistent (optional)
    - **required_env_vars**: List of required environment variables (optional)
      - **name**: Variable name
      - **description**: What the variable represents
      - **required**: Whether it's required (default: true)
    - **is_public**: Make this configuration public/shareable (default: false)

    **Returns:**
    - Created MCP configuration with assigned ID
    """
    logger.info(f"Creating MCP config: {request.name} by user: {user.id}")
    return MCPConfigService.create(request, user)


@router.get(
    "/mcp-configs/{config_id}",
    status_code=status.HTTP_200_OK,
    response_model=MCPConfigResponse,
    summary="Get MCP Configuration",
    description="Get a specific MCP server configuration by ID",
)
def get_mcp_config(
    config_id: str,
    user: User = Depends(authenticate),
) -> MCPConfigResponse:
    """
    Get an MCP server configuration by ID.

    Available to all authenticated users.

    **Path Parameters:**
    - **config_id**: Unique identifier of the MCP configuration

    **Returns:**
    - MCP configuration details
    """
    logger.info(f"Getting MCP config: {config_id} for user: {user.id}")
    return MCPConfigService.get_by_id(config_id)


@router.get(
    "/mcp-configs",
    status_code=status.HTTP_200_OK,
    response_model=MCPConfigListResponse,
    summary="List MCP Configurations",
    description="List all MCP server configurations with filtering and pagination",
)
def list_mcp_configs(
    user: User = Depends(authenticate),
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search in name and description"),
    is_public: Optional[bool] = Query(None, description="Filter by public/private status"),
    active_only: bool = Query(True, description="Only return active configurations"),
) -> MCPConfigListResponse:
    """
    List MCP server configurations with filtering and pagination.

    Available to all authenticated users. Returns all MCP configurations with optional filters.

    Results are sorted by usage count (most used first) and then alphabetically by name.

    **Query Parameters:**
    - **page**: Page number, starting from 0 (default: 0)
    - **per_page**: Number of items per page (default: 20, max: 100)
    - **category**: Filter by specific category (optional)
    - **search**: Search text to filter by name or description (optional)
    - **is_public**: Filter by public/private status (optional, None = all)
    - **active_only**: Only return active configurations (default: true)

    **Returns:**
    - Paginated list of MCP configurations with total count
    """
    logger.info(
        f"Listing MCP configs for user: {user.id}, page: {page}, per_page: {per_page}, "
        f"category: {category}, search: {search}"
    )
    return MCPConfigService.list_configs(
        page=page,
        per_page=per_page,
        category=category,
        search=search,
        is_public=is_public,
        active_only=active_only,
    )


@router.put(
    "/mcp-configs/{config_id}",
    status_code=status.HTTP_200_OK,
    response_model=MCPConfigResponse,
    summary="Update MCP Configuration",
    description="Update an existing MCP server configuration (Admin only)",
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)
def update_mcp_config(
    config_id: str,
    request: MCPConfigUpdateRequest,
    user: User = Depends(authenticate),
) -> MCPConfigResponse:
    """
    Update an existing MCP server configuration.

    **Admin only.** System configurations can be updated by admins.
    All fields in the request are optional - only provided fields will be updated.

    **Path Parameters:**
    - **config_id**: Unique identifier of the MCP configuration to update

    **Request Body (all fields optional):**
    - **name**: New name for the MCP server
    - **description**: New description
    - **server_home_url**: New documentation URL
    - **source_url**: New source code URL
    - **logo_url**: New logo URL
    - **categories**: New list of categories (max 3)
    - **config**: Updated MCP server configuration
    - **required_env_vars**: Updated list of required environment variables
    - **is_public**: Change public/private status
    - **is_active**: Activate or deactivate the configuration

    **Returns:**
    - Updated MCP configuration
    """
    logger.info(f"Updating MCP config: {config_id} by admin: {user.id}")
    return MCPConfigService.update(config_id, request)


@router.delete(
    "/mcp-configs/{config_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete MCP Configuration",
    description="Delete an MCP server configuration (Admin only)",
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)
def delete_mcp_config(
    config_id: str,
    user: User = Depends(authenticate),
) -> dict:
    """
    Delete an MCP server configuration.

    **Admin only.** System configurations cannot be deleted.

    **Path Parameters:**
    - **config_id**: Unique identifier of the MCP configuration to delete

    **Returns:**
    - Deletion confirmation with status and ID
    """
    logger.info(f"Deleting MCP config: {config_id} by admin: {user.id}")
    return MCPConfigService.delete(config_id)
