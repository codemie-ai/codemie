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
Service layer for MCP Configuration Catalog management.

Handles business logic for CRUD operations on MCP server configurations.
"""

from typing import Optional, Dict, Any
from fastapi import status

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.mcp_config import (
    MCPConfig,
    MCPConfigCreateRequest,
    MCPConfigUpdateRequest,
    MCPConfigResponse,
    MCPConfigListResponse,
)
from codemie.rest_api.security.user import User


# Constants for error messages
MCP_CONFIG_NOT_FOUND_MESSAGE = "MCP configuration not found"
VERIFY_CONFIG_ID_HELP = "Verify the configuration ID"


class MCPConfigService:
    """Service for managing MCP server configurations"""

    @classmethod
    def create(cls, request: MCPConfigCreateRequest, user: User) -> MCPConfigResponse:
        """
        Create a new MCP configuration.

        Args:
            request: MCP configuration creation request
            user: Authenticated user

        Returns:
            Created MCP configuration

        Raises:
            ExtendedHTTPException: If creation fails or duplicate exists
        """
        logger.info(f"Creating MCP config: {request.name} for user: {user.id}")

        # Check for duplicate name for this user
        existing = MCPConfig.get_by_fields(
            {
                "name": request.name,
                "user_id": user.id,
            }
        )
        if existing:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="MCP configuration already exists",
                details=f"An MCP configuration with name '{request.name}' already exists",
                help="Use a different name or update the existing configuration",
            )

        # Create MCP config
        mcp_config = MCPConfig(
            name=request.name,
            description=request.description,
            server_home_url=request.server_home_url,
            source_url=request.source_url,
            logo_url=request.logo_url,
            categories=request.categories,
            config=request.config,
            required_env_vars=request.required_env_vars,
            user_id=user.id,
            is_public=True,  # Since only administrators can create MCP configs, they are public by default
            is_system=True,  # Since only administrators can create MCP configs, they are system configs
            created_by=CreatedByUser(
                id=user.id,
                name=user.name,
                username=user.username,
            ),
            usage_count=0,
            is_active=True,
        )

        # Save to database
        save_response = mcp_config.save()
        logger.info(f"MCP config created: {save_response}")

        # Return response
        return cls._to_response(mcp_config)

    @classmethod
    def get_by_id(cls, config_id: str) -> MCPConfigResponse:
        """
        Get MCP configuration by ID.

        Args:
            config_id: MCP configuration ID

        Returns:
            MCP configuration

        Raises:
            ExtendedHTTPException: If not found
        """
        mcp_config = MCPConfig.find_by_id(config_id)
        if not mcp_config:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=MCP_CONFIG_NOT_FOUND_MESSAGE,
                details=f"No MCP configuration found with id: {config_id}",
                help=VERIFY_CONFIG_ID_HELP,
            )

        return cls._to_response(mcp_config)

    @classmethod
    def update(cls, config_id: str, request: MCPConfigUpdateRequest) -> MCPConfigResponse:
        """
        Update an existing MCP configuration.

        Args:
            config_id: MCP configuration ID
            request: Update request with fields to change

        Returns:
            Updated MCP configuration

        Raises:
            ExtendedHTTPException: If not found or update fails
        """
        logger.info(f"Updating MCP config: {config_id}")

        # Get existing config
        mcp_config = MCPConfig.find_by_id(config_id)
        if not mcp_config:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=MCP_CONFIG_NOT_FOUND_MESSAGE,
                details=f"No MCP configuration found with id: {config_id}",
                help=VERIFY_CONFIG_ID_HELP,
            )

        # Check for name conflict if name is being changed
        if request.name and request.name != mcp_config.name:
            existing = MCPConfig.get_by_fields(
                {
                    "name": request.name,
                    "user_id": mcp_config.user_id,
                }
            )
            if existing and existing.id != config_id:
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Name already exists",
                    details=f"An MCP configuration with name '{request.name}' already exists",
                    help="Use a different name",
                )

        # Update fields that are provided
        update_data = request.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(mcp_config, field, value)

        # Save changes
        mcp_config.update()
        logger.info(f"MCP config updated: {config_id}")

        return cls._to_response(mcp_config)

    @classmethod
    def delete(cls, config_id: str) -> Dict[str, str]:
        """
        Delete an MCP configuration.

        Args:
            config_id: MCP configuration ID

        Returns:
            Deletion confirmation

        Raises:
            ExtendedHTTPException: If not found or if system config
        """
        logger.info(f"Deleting MCP config: {config_id}")

        # Get existing config
        mcp_config = MCPConfig.find_by_id(config_id)
        if not mcp_config:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=MCP_CONFIG_NOT_FOUND_MESSAGE,
                details=f"No MCP configuration found with id: {config_id}",
                help=VERIFY_CONFIG_ID_HELP,
            )

        # Prevent deletion if config is in use
        if mcp_config.usage_count > 0:
            raise ExtendedHTTPException(
                code=status.HTTP_409_CONFLICT,
                message="Cannot delete MCP configuration in use",
                details=f"This configuration is currently used by {mcp_config.usage_count} assistant(s)",
                help="Remove this MCP server from all assistants before deleting",
            )

        # Delete
        mcp_config.delete()
        logger.info(f"MCP config deleted: {config_id}")

        return {"status": "deleted", "id": config_id}

    @classmethod
    def list_configs(
        cls,
        page: int = 0,
        per_page: int = 20,
        category: Optional[str] = None,
        search: Optional[str] = None,
        is_public: Optional[bool] = None,
        active_only: bool = True,
    ) -> MCPConfigListResponse:
        """
        List MCP configurations with filtering and pagination.

        Args:
            page: Page number (0-indexed)
            per_page: Items per page
            category: Filter by category
            search: Search in name and description
            is_public: Filter by public/private status (None = all)
            active_only: Only return active configurations

        Returns:
            Paginated list of MCP configurations
        """
        logger.info(f"Listing MCP configs, page: {page}, per_page: {per_page}")

        # Build filter criteria
        filters: Dict[str, Any] = {}

        if active_only:
            filters["is_active"] = True

        if is_public is not None:
            filters["is_public"] = is_public

        # Get all configs matching base filters
        all_configs = MCPConfig.get_all_by_fields(filters) if filters else list(MCPConfig.get_all())

        # Apply category filter
        if category:
            all_configs = [config for config in all_configs if category in config.categories]

        # Apply search filter
        if search:
            search_lower = search.lower()
            all_configs = [
                config
                for config in all_configs
                if (
                    search_lower in config.name.lower()
                    or (config.description and search_lower in config.description.lower())
                )
            ]

        # Sort by usage count (most used first) and then by name
        all_configs.sort(key=lambda x: (-x.usage_count, x.name))

        # Apply pagination
        total = len(all_configs)
        start = page * per_page
        end = start + per_page
        paginated_configs = all_configs[start:end]

        # Convert to response models
        config_responses = [cls._to_response(config) for config in paginated_configs]

        return MCPConfigListResponse(
            total=total,
            configs=config_responses,
            page=page,
            per_page=per_page,
        )

    @classmethod
    def increment_usage(cls, config_id: str) -> None:
        """
        Increment usage count for an MCP configuration.

        Args:
            config_id: MCP configuration ID
        """
        mcp_config = MCPConfig.find_by_id(config_id)
        if mcp_config:
            mcp_config.usage_count += 1
            mcp_config.update()
            logger.debug(f"Incremented usage count for MCP config: {config_id}")

    @classmethod
    def decrement_usage(cls, config_id: str) -> None:
        """
        Decrement usage count for an MCP configuration.

        Args:
            config_id: MCP configuration ID
        """
        mcp_config = MCPConfig.find_by_id(config_id)
        if mcp_config and mcp_config.usage_count > 0:
            mcp_config.usage_count -= 1
            mcp_config.update()
            logger.debug(f"Decremented usage count for MCP config: {config_id}")

    @classmethod
    def find_by_name(cls, name: str, user_id: str) -> Optional[MCPConfig]:
        """
        Find MCP configuration by name and user ID.

        Args:
            name: MCP config name
            user_id: User ID who owns the config

        Returns:
            MCPConfig if found, None otherwise
        """
        configs = MCPConfig.get_all_by_fields({"name": name, "user_id": user_id})
        return configs[0] if configs else None

    @classmethod
    def _to_response(cls, mcp_config: MCPConfig) -> MCPConfigResponse:
        """
        Convert MCPConfig model to response model.

        Args:
            mcp_config: MCP configuration database model

        Returns:
            MCPConfigResponse
        """
        return MCPConfigResponse(**mcp_config.model_dump())
