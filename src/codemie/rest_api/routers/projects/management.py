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

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Path, Request

from codemie.clients.postgres import get_session
from codemie.core.models import Application
from codemie.rest_api.models.project import (
    AllowedModelsUpdateRequest,
    AllowedModelsUpdateResponse,
    ProjectCreateResponse,
    ProjectDeleteResponse,
    ProjectOwnershipUpdateRequest,
    ProjectOwnershipUpdateResponse,
    ProjectUpdateRequest,
)
from codemie.rest_api.routers.projects._common import (
    _authorize_project_access,
    _ensure_user_management_enabled,
    _resolve_cost_center_name,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.project.project_service import project_service
from codemie.service.settings.settings import SettingsService


router = APIRouter()


@router.patch("/projects/{projectName}", response_model=ProjectCreateResponse)
def update_project(
    payload: ProjectUpdateRequest,
    project_name: str = Path(alias="projectName"),
    user: User = Depends(authenticate),
):
    _ensure_user_management_enabled()

    project = project_service.update_project(
        user=user,
        project_name=project_name,
        name=payload.name,
        display_name=payload.display_name,
        clear_display_name=payload.clear_display_name,
        description=payload.description,
        clear_description=payload.clear_description,
        cost_center_id=None if payload.clear_cost_center else payload.cost_center_id,
        clear_cost_center=payload.clear_cost_center,
        enforce_member_spend_limits=payload.enforce_member_spend_limits,
        chargeback_enabled=payload.chargeback_enabled,
        chargeback_attribution=payload.chargeback_attribution,
    )

    return ProjectCreateResponse(
        name=project.name,
        display_name=project.display_name,
        description=project.description,
        project_type=project.project_type,
        created_by=project.created_by or user.id,
        created_at=project.date or datetime.now(UTC),
        cost_center_id=getattr(project, "cost_center_id", None),
        cost_center_name=_resolve_cost_center_name(getattr(project, "cost_center_id", None)),
        enforce_member_spend_limits=SettingsService.get_enforce_member_spend_limits(project.name),
        chargeback_enabled=project.chargeback_enabled,
        chargeback_attribution=project.chargeback_attribution,
        allowed_models=getattr(project, "allowed_models", None),
    )


@router.patch("/projects/{projectName}/allowed-models", response_model=AllowedModelsUpdateResponse)
def update_allowed_models(
    payload: AllowedModelsUpdateRequest,
    project_name: str = Path(alias="projectName"),
    user: User = Depends(authenticate),
):
    """Update the allowed_models field for a project.

    Only project admins and maintainers can update this field.
    Returns 400 if allowed_models is empty or contains no chat models.
    Returns 403 if user lacks admin/maintainer role.
    Returns 404 if project doesn't exist.
    """
    _ensure_user_management_enabled()

    project = project_service.update_allowed_models(user, project_name, payload.allowed_models, payload.default_model)

    return AllowedModelsUpdateResponse(
        name=project.name,
        display_name=project.display_name,
        description=project.description or "",
        project_type=project.project_type,
        created_by=project.created_by or user.id,
        created_at=project.date or datetime.now(UTC),
        cost_center_id=getattr(project, "cost_center_id", None),
        cost_center_name=_resolve_cost_center_name(getattr(project, "cost_center_id", None)),
        allowed_models=project.allowed_models,
        default_model=project.default_model,
    )


@router.put("/projects/{projectName}/ownership", response_model=ProjectOwnershipUpdateResponse)
def update_project_ownership(
    payload: ProjectOwnershipUpdateRequest,
    request: Request,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Reassign ownership of a project to another existing platform user."""
    project = project_service.update_project_ownership(
        user=request.state.user,
        project_name=authorized_project.name,
        new_owner_id=payload.user_id,
    )
    return ProjectOwnershipUpdateResponse(
        name=project.name,
        created_by=project.created_by,
        message=f"Ownership of '{project.name}' transferred to '{project.created_by}'",
    )


@router.delete("/projects/{projectName}", response_model=ProjectDeleteResponse)
def delete_project(
    request: Request,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Hard-delete a project if it has no assigned resources.

    Returns 409 if the project has any assistants, workflows, skills,
    datasources, or integrations assigned.
    Returns 403 if the project is a personal project.
    """
    with get_session() as session:
        project_service.delete_project(
            session=session,
            project_name=authorized_project.name,
            project_type=authorized_project.project_type,
            actor_id=request.state.user.id,
            action=f"{request.method} {request.url.path}",
            creator_id=authorized_project.created_by,
        )
        session.commit()

    return ProjectDeleteResponse(
        message=f"Project '{project_name}' deleted successfully",
        name=project_name,
    )
