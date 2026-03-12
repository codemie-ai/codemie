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

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from codemie.rest_api.models.dynamic_config import (
    DynamicConfigCreateRequest,
    DynamicConfigUpdateRequest,
    DynamicConfigResponse,
)
from codemie.rest_api.security.authentication import authenticate, admin_access_only
from codemie.rest_api.security.user import User
from codemie.service.dynamic_config_service import DynamicConfigService
from codemie.core.exceptions import ExtendedHTTPException

router = APIRouter(
    tags=["dynamic-config"],
    prefix="/v1/dynamic-config",
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)


@router.get("/", status_code=status.HTTP_200_OK, response_model=list[DynamicConfigResponse])
def list_all_configs() -> list[DynamicConfigResponse]:
    """
    List all dynamic configurations.

    Requires super-admin authentication.

    Returns:
        List of all dynamic configuration entries ordered by key
    """
    configs = DynamicConfigService.list_all()
    return [
        DynamicConfigResponse(
            id=c.id,
            key=c.key,
            value=c.value,
            value_type=c.value_type,
            description=c.description,
            created_at=c.date,
            updated_at=c.update_date,
            updated_by=c.updated_by,
        )
        for c in configs
    ]


@router.get("/{key}", status_code=status.HTTP_200_OK, response_model=DynamicConfigResponse)
def get_config_by_key(key: str) -> DynamicConfigResponse:
    """
    Get a specific configuration by key.

    Requires super-admin authentication.

    Args:
        key: Configuration key (UPPER_SNAKE_CASE)

    Returns:
        Dynamic configuration entry

    Raises:
        404: Configuration key not found
    """
    # Use service layer to get config model
    config = DynamicConfigService.get_by_key(key)

    if config is None:
        raise ExtendedHTTPException(
            code=404, message="Config not found", details=f"Configuration key '{key}' does not exist"
        )

    return DynamicConfigResponse(
        id=config.id,
        key=config.key,
        value=config.value,
        value_type=config.value_type,
        description=config.description,
        created_at=config.date,
        updated_at=config.update_date,
        updated_by=config.updated_by,
    )


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=DynamicConfigResponse)
def create_config(
    request: DynamicConfigCreateRequest, current_user: User = Depends(authenticate)
) -> DynamicConfigResponse:
    """
    Create a new dynamic configuration.

    Requires super-admin authentication.

    Args:
        request: Configuration creation request with key, value, type, and optional description

    Returns:
        Created dynamic configuration entry

    Raises:
        400: Invalid key format or value validation error
        409: Configuration key already exists
    """
    # Check if key already exists using service layer
    existing = DynamicConfigService.get_by_key(request.key)

    if existing:
        raise ExtendedHTTPException(
            code=409,
            message="Config already exists",
            details=f"Configuration key '{request.key}' already exists. Use PUT to update.",
        )

    # Create new config
    config = DynamicConfigService.set(
        key=request.key,
        value=request.value,
        value_type=request.value_type,
        description=request.description,
        user=current_user,
    )

    return DynamicConfigResponse(
        id=config.id,
        key=config.key,
        value=config.value,
        value_type=config.value_type,
        description=config.description,
        created_at=config.date,
        updated_at=config.update_date,
        updated_by=config.updated_by,
    )


@router.put("/{key}", status_code=status.HTTP_200_OK, response_model=DynamicConfigResponse)
def update_config(
    key: str, request: DynamicConfigUpdateRequest, current_user: User = Depends(authenticate)
) -> DynamicConfigResponse:
    """
    Update an existing dynamic configuration.

    Requires super-admin authentication.

    Args:
        key: Configuration key to update
        request: Update request with new value, optional type, and optional description

    Returns:
        Updated dynamic configuration entry

    Raises:
        400: Invalid value or type validation error
        404: Configuration key not found
    """
    # Check if config exists using service layer
    existing = DynamicConfigService.get_by_key(key)

    if existing is None:
        raise ExtendedHTTPException(
            code=404, message="Config not found", details=f"Configuration key '{key}' does not exist"
        )

    # Use existing type if not provided in update
    value_type = request.value_type if request.value_type is not None else existing.value_type

    # Validate value is compatible with type before calling service
    # This provides early validation and consistent error responses
    DynamicConfigService.convert_value(request.value, value_type)

    # Update config
    config = DynamicConfigService.set(
        key=key,
        value=request.value,
        value_type=value_type,
        description=request.description if request.description is not None else existing.description,
        user=current_user,
    )

    return DynamicConfigResponse(
        id=config.id,
        key=config.key,
        value=config.value,
        value_type=config.value_type,
        description=config.description,
        created_at=config.date,
        updated_at=config.update_date,
        updated_by=config.updated_by,
    )


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_config(key: str, current_user: User = Depends(authenticate)):
    """
    Delete a dynamic configuration.

    Requires super-admin authentication.

    Args:
        key: Configuration key to delete

    Raises:
        404: Configuration key not found
    """
    DynamicConfigService.delete(key=key, user=current_user)
