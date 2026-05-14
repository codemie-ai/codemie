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

"""Service layer for MCP Configuration Catalog management.

Handles business logic for CRUD operations on MCP server configurations.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.enterprise.mcp_auth.dependencies import (
    _is_missing_required_value,
    has_any_credentials_for_auth_config,
    invalidate_credentials_for_auth_config,
    is_mcp_auth_enabled,
    validate_auth_config_on_save,
)
from codemie.rest_api.models.mcp_config import (
    ConfigWarning,
    MCPConfig,
    MCPConfigCreateRequest,
    MCPConfigListResponse,
    MCPConfigResponse,
    MCPServerConfigData,
    MCPConfigUpdateRequest,
)
from codemie.rest_api.security.user import User
from codemie.service.encryption.base_encryption_service import BaseEncryptionService
from codemie.service.encryption.encryption_factory import EncryptionFactory


# Constants for error messages
MCP_CONFIG_NOT_FOUND_MESSAGE = "MCP configuration not found"
VERIFY_CONFIG_ID_HELP = "Verify the configuration ID"
_AUTH_CONFIG_ID_INDEX = "ix_mcp_configs_auth_config_id"
_OAUTH2_AUTH_TYPE = "oauth2"
_CONFIDENTIAL_CLIENT_TYPE = "confidential"
_HAS_CLIENT_SECRET_FIELD = "has_client_secret"
_CLIENT_SECRET_FIELD = "client_secret"
_AUTH_CONFIG_ID_FIELD = "id"
_AUTH_CONFIG_ID_IMMUTABLE_MESSAGE = "auth_config.id cannot be changed after credentials have been stored"
_TOKEN_INVALIDATION_FAILED_MESSAGE = "Token invalidation failed. Configuration not saved. Please retry."


def _is_auth_config_id_conflict(e: IntegrityError) -> bool:
    """Return True when *e* is a uniqueness violation on ix_mcp_configs_auth_config_id.

    Detection strategy (in priority order):

    1. ``diag.constraint_name`` — driver-agnostic structured metadata.  psycopg2
       ``UniqueViolation``, psycopg3, and other PostgreSQL DBAPI drivers all populate
       ``.diag.constraint_name`` for constraint violations.  No ``isinstance`` guard is
       used; any exception that exposes ``.diag.constraint_name`` is handled here.
    2. ``str(e)`` substring search — fallback for DBAPI drivers that do not expose
       structured diagnostics (e.g. asyncpg, plain JDBC wrappers).
    """
    orig = e.orig
    if orig is not None:
        # Primary: driver-agnostic structured diagnostics.
        constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
        if constraint is not None:
            return constraint == _AUTH_CONFIG_ID_INDEX
        # diag absent or constraint_name is None — fall through to string inspection
    # Fallback: string inspection
    return _AUTH_CONFIG_ID_INDEX in str(e)


def _get_auth_config_validation_errors(config: MCPServerConfigData | dict[str, Any] | None) -> list[str]:
    if isinstance(config, dict):
        config = MCPServerConfigData.model_validate(config)

    if config is None or config.auth_config is None:
        return []

    is_http_transport = bool(config.url and config.url.strip()) or config.type == "streamable-http"
    transport = "http" if is_http_transport else "stdio"
    return validate_auth_config_on_save(config.auth_config, transport)


def _get_raw_auth_config(config: MCPServerConfigData | dict[str, Any] | None) -> dict[str, Any] | None:
    if config is None:
        return None

    if isinstance(config, dict):
        auth_config = config.get("auth_config")
        return auth_config if isinstance(auth_config, dict) else None

    if isinstance(config, MCPServerConfigData):
        return config.auth_config

    return None


def _is_confidential_oauth2_auth_config(auth_config: dict[str, Any] | None) -> bool:
    return bool(
        auth_config
        and auth_config.get("auth_type") == _OAUTH2_AUTH_TYPE
        and auth_config.get("client_type") == _CONFIDENTIAL_CLIENT_TYPE
    )


def _strip_response_only_auth_metadata(auth_config: dict[str, Any] | None) -> None:
    if auth_config is not None:
        auth_config.pop(_HAS_CLIENT_SECRET_FIELD, None)


def _remove_client_secret_fields(auth_config: dict[str, Any] | None) -> None:
    if auth_config is None:
        return

    auth_config.pop(_CLIENT_SECRET_FIELD, None)


def _get_auth_config_id(auth_config: dict[str, Any] | None) -> str | None:
    if auth_config is None:
        return None

    auth_config_id = auth_config.get(_AUTH_CONFIG_ID_FIELD)
    if _is_missing_required_value(auth_config_id):
        return None

    return auth_config_id


def _ensure_auth_config_id(auth_config: dict[str, Any] | None) -> None:
    if auth_config is None or _get_auth_config_id(auth_config) is not None:
        return

    auth_config[_AUTH_CONFIG_ID_FIELD] = str(uuid.uuid4())


def _invalidate_removed_auth_config(previous_auth_config_id: str | None) -> None:
    if previous_auth_config_id is None:
        return

    # Story 2.5 also reuses this helper for non-removal auth_config modifications.
    try:
        invalidate_credentials_for_auth_config(previous_auth_config_id)
    except Exception as exc:
        logger.warning(
            "Rejecting MCP config save due to token invalidation failure "
            f"auth_config_id={previous_auth_config_id}: {exc}"
        )
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_TOKEN_INVALIDATION_FAILED_MESSAGE,
            details="Stored credentials could not be invalidated before saving the configuration change",
            help="Retry the save after token invalidation succeeds",
        ) from exc


def _get_stored_encrypted_secret(config: MCPServerConfigData | dict[str, Any] | None) -> str | None:
    auth_config = _get_raw_auth_config(config)
    if not _is_confidential_oauth2_auth_config(auth_config):
        return None

    client_secret = auth_config.get(_CLIENT_SECRET_FIELD)
    if _is_missing_required_value(client_secret):
        return None

    return client_secret


def _validate_auth_config_for_save(
    config: MCPServerConfigData | dict[str, Any] | None,
    config_id: str | None = None,
) -> None:
    errors = _get_auth_config_validation_errors(config)
    if not errors:
        return

    config_ref = f" config_id={config_id}" if config_id else ""
    logger.warning(f"Rejecting MCP config save due to auth_config validation failure{config_ref}")
    raise ExtendedHTTPException(
        code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message="Invalid auth_config",
        details="; ".join(errors),
        help="Fix the auth_config validation errors and retry the save",
    )


class MCPConfigService:
    """Service for managing MCP server configurations"""

    encryption_service: BaseEncryptionService = EncryptionFactory().get_current_encryption_service()

    @classmethod
    def _encrypt_confidential_client_secret(cls, auth_config: dict[str, Any]) -> None:
        client_secret = auth_config.get(_CLIENT_SECRET_FIELD)
        if _is_missing_required_value(client_secret):
            _remove_client_secret_fields(auth_config)
            return

        auth_config[_CLIENT_SECRET_FIELD] = cls.encryption_service.encrypt(client_secret)

    @classmethod
    def _prepare_auth_config_for_persistence(
        cls,
        auth_config: dict[str, Any] | None,
        existing_encrypted_secret: str | None = None,
        incoming_auth_config: dict[str, Any] | None = None,
    ) -> None:
        if auth_config is None:
            return

        if not _is_confidential_oauth2_auth_config(auth_config):
            _remove_client_secret_fields(auth_config)
            return

        if incoming_auth_config is None:
            cls._encrypt_confidential_client_secret(auth_config)
            return

        incoming_client_secret = incoming_auth_config.get(_CLIENT_SECRET_FIELD)
        if _CLIENT_SECRET_FIELD in incoming_auth_config and not _is_missing_required_value(incoming_client_secret):
            auth_config[_CLIENT_SECRET_FIELD] = cls.encryption_service.encrypt(incoming_client_secret)
            return

        if existing_encrypted_secret is not None:
            auth_config[_CLIENT_SECRET_FIELD] = existing_encrypted_secret
            return

        auth_config.pop(_CLIENT_SECRET_FIELD, None)

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

        auth_config = _get_raw_auth_config(request.config)
        _strip_response_only_auth_metadata(auth_config)
        _ensure_auth_config_id(auth_config)
        _validate_auth_config_for_save(request.config)
        # Intentionally mutate the request config in place before persistence so
        # the stored raw auth_config dict matches the validated/encrypted payload.
        cls._prepare_auth_config_for_persistence(auth_config)

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

        # Save to database — catch unique-index violations for auth_config.id
        try:
            save_response = mcp_config.save()
        except IntegrityError as e:
            if _is_auth_config_id_conflict(e):
                raise ExtendedHTTPException(
                    code=status.HTTP_409_CONFLICT,
                    message="auth_config.id already in use",
                    details="Another MCP configuration already uses this auth_config.id value",
                    help="Use a unique auth_config.id for this MCP server",
                ) from e
            raise
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
        previous_auth_config = _get_raw_auth_config(mcp_config.config)
        previous_auth_config_for_comparison = dict(previous_auth_config) if previous_auth_config else None
        previous_auth_config_id = _get_auth_config_id(previous_auth_config)
        existing_encrypted_secret = _get_stored_encrypted_secret(mcp_config.config)

        update_data = request.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(mcp_config, field, value)

        request_fields_set = getattr(request, "model_fields_set", set(update_data))
        cls._apply_config_field_update(
            mcp_config,
            request_fields_set,
            request,
            update_data=update_data,
            previous_auth_config_id=previous_auth_config_id,
            previous_auth_config_for_comparison=previous_auth_config_for_comparison,
            existing_encrypted_secret=existing_encrypted_secret,
            config_id=config_id,
        )

        # Save changes — catch unique-index violations for auth_config.id
        try:
            mcp_config.update()
        except IntegrityError as e:
            if _is_auth_config_id_conflict(e):
                raise ExtendedHTTPException(
                    code=status.HTTP_409_CONFLICT,
                    message="auth_config.id already in use",
                    details="Another MCP configuration already uses this auth_config.id value",
                    help="Use a unique auth_config.id for this MCP server",
                ) from e
            raise
        logger.info(f"MCP config updated: {config_id}")

        return cls._to_response(mcp_config)

    @classmethod
    def _apply_config_field_update(
        cls,
        mcp_config: Any,
        request_fields_set: set,
        request: Any,
        *,
        update_data: dict,
        previous_auth_config_id: str | None,
        previous_auth_config_for_comparison: dict | None,
        existing_encrypted_secret: str | None,
        config_id: str,
    ) -> None:
        if "config" not in request_fields_set:
            return
        if mcp_config.config is not None:
            auth_config = _get_raw_auth_config(mcp_config.config)
            if auth_config is not None:
                _strip_response_only_auth_metadata(auth_config)
                if previous_auth_config_id is None:
                    _ensure_auth_config_id(auth_config)
                elif _get_auth_config_id(auth_config) is None:
                    auth_config[_AUTH_CONFIG_ID_FIELD] = previous_auth_config_id

                current_auth_config_id = _get_auth_config_id(auth_config)
                if (
                    previous_auth_config_id is not None
                    and current_auth_config_id != previous_auth_config_id
                    and is_mcp_auth_enabled()
                    and has_any_credentials_for_auth_config(previous_auth_config_id)
                ):
                    raise ExtendedHTTPException(
                        code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        message=_AUTH_CONFIG_ID_IMMUTABLE_MESSAGE,
                        details=(
                            "Stored credentials already exist for "
                            f"auth_config.id '{previous_auth_config_id}', "
                            f"requested change to '{current_auth_config_id}'"
                        ),
                        help="Keep the existing auth_config.id or remove stored credentials before changing it",
                    )

                _validate_auth_config_for_save(mcp_config.config, config_id=config_id)
                cls._prepare_auth_config_for_persistence(
                    auth_config,
                    existing_encrypted_secret=existing_encrypted_secret,
                    incoming_auth_config=_get_raw_auth_config(getattr(request, "config", update_data.get("config"))),
                )

            final_auth_config = _get_raw_auth_config(mcp_config.config)
            if previous_auth_config_for_comparison != final_auth_config and previous_auth_config_id is not None:
                _invalidate_removed_auth_config(previous_auth_config_id)
        elif previous_auth_config_for_comparison is not None and previous_auth_config_id is not None:
            _invalidate_removed_auth_config(previous_auth_config_id)

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
    def adjust_usage(cls, increments: set[str], decrements: set[str]) -> None:
        """Bulk-adjust usage counts atomically using SELECT FOR UPDATE to prevent race conditions.

        Args:
            increments: config IDs whose usage_count should be incremented
            decrements: config IDs whose usage_count should be decremented
        """
        all_ids = list(increments | decrements)
        if not all_ids:
            return

        with Session(MCPConfig.get_engine()) as session:
            stmt = select(MCPConfig).where(MCPConfig.id.in_(all_ids)).with_for_update()
            configs = {c.id: c for c in session.exec(stmt).all() if c.id}

            for config_id in increments:
                if cfg := configs.get(config_id):
                    cfg.usage_count += 1
                    logger.debug(f"Incremented usage count for MCP config: {config_id}")

            for config_id in decrements:
                if cfg := configs.get(config_id):
                    cfg.usage_count = max(0, cfg.usage_count - 1)
                    logger.debug(f"Decremented usage count for MCP config: {config_id}")

            session.commit()

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
        response = MCPConfigResponse(**mcp_config.model_dump())

        if response.config and response.config.auth_config is not None:
            response.config = response.config.model_copy(deep=True)
            auth_config = dict(response.config.auth_config)
            if auth_config.get("auth_type") == _OAUTH2_AUTH_TYPE:
                auth_config[_HAS_CLIENT_SECRET_FIELD] = not _is_missing_required_value(
                    auth_config.get(_CLIENT_SECRET_FIELD)
                )
                auth_config.pop(_CLIENT_SECRET_FIELD, None)
            response.config.auth_config = auth_config

        if response.config and response.config.auth_config is not None and not is_mcp_auth_enabled():
            response.warnings.append(
                ConfigWarning(
                    code="inactive_auth_config",
                    message=(
                        f"Auth configuration exists for '{response.name}' but MCP Authorization is not active "
                        "(enterprise package not installed or MCP_AUTH_ENABLED not set)"
                    ),
                    action=(
                        "Enable MCP_AUTH_ENABLED and install the enterprise package, "
                        "or remove the unused auth_config"
                    ),
                )
            )
        return response
