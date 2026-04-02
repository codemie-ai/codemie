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

from datetime import datetime, UTC
from typing import Literal, Optional
from uuid import UUID

import logging

from fastapi import APIRouter, Depends, Path, Query, Request, UploadFile
from pydantic import BaseModel, Field, model_validator

from codemie.clients.postgres import get_session
from codemie.configs import config
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.repository.cost_center_repository import cost_center_repository
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.project.project_service import project_service
from codemie.service.project.project_visibility_service import project_visibility_service


router = APIRouter(
    tags=["Projects"],
    prefix="/v1",
    dependencies=[],
)

# NOTE: All endpoints use synchronous `def` handlers, consistent with the
# established codebase pattern. FastAPI auto-offloads sync handlers to a threadpool.


class ProjectCounters(BaseModel):
    """Resource counters for a project — reusable across different response shapes."""

    assistants_count: int = 0
    workflows_count: int = 0
    integrations_count: int = 0
    datasources_count: int = 0
    skills_count: int = 0


class ProjectListItem(BaseModel):
    """Project list response item with member counts (Story 16)"""

    name: str
    description: Optional[str] = None
    project_type: str
    created_by: Optional[str] = None
    user_count: int
    admin_count: int
    created_at: Optional[datetime] = None
    counters: Optional[ProjectCounters] = None
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None


class PaginationInfo(BaseModel):
    """Pagination metadata for list responses"""

    total: int
    page: int
    per_page: int


class PaginatedProjectListResponse(BaseModel):
    """Paginated project list response (Story 16)"""

    data: list[ProjectListItem]
    pagination: PaginationInfo


class ProjectMember(BaseModel):
    """Project member with role (Story 16)"""

    user_id: str
    is_project_admin: bool
    date: Optional[datetime] = None


class ProjectDetailResponse(BaseModel):
    """Project detail response with member list (Story 16)"""

    name: str
    description: Optional[str] = None
    project_type: str
    created_by: Optional[str] = None
    user_count: int
    admin_count: int
    created_at: Optional[datetime] = None
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None
    members: list[ProjectMember]


class ProjectCreateRequest(BaseModel):
    name: str
    description: str = Field(description="Project description")
    cost_center_id: Optional[UUID] = None


class ProjectCreateResponse(BaseModel):
    name: str
    description: str
    project_type: str
    created_by: str
    created_at: datetime
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cost_center_id: Optional[UUID] = None
    clear_cost_center: bool = False

    @model_validator(mode="after")
    def validate_non_empty(self):
        if (
            self.name is None
            and self.description is None
            and self.cost_center_id is None
            and not self.clear_cost_center
        ):
            raise ValueError("At least one mutable field must be provided")
        if self.cost_center_id is not None and self.clear_cost_center:
            raise ValueError("Provide either cost_center_id or clear_cost_center")
        return self


class ProjectAssignmentRequest(BaseModel):
    user_id: str
    is_project_admin: bool


class ProjectAssignmentUpdateRequest(BaseModel):
    is_project_admin: bool


class ProjectAssignmentResponse(BaseModel):
    message: str
    user_id: str
    project_name: str
    is_project_admin: Optional[bool] = None


class BulkAssignmentUserItem(BaseModel):
    """Single user entry in bulk assignment request."""

    user_id: str
    is_project_admin: bool


class BulkAssignmentRequest(BaseModel):
    """Bulk assignment request body - assigns new users and/or updates existing roles."""

    users: list[BulkAssignmentUserItem] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="List of users to assign/update (1-1000)",
    )


class ProjectUpdateResponse(BaseModel):
    """Response after successful project update."""

    name: str
    description: Optional[str] = None
    project_type: str
    message: str


class ProjectDeleteResponse(BaseModel):
    """Response after successful project deletion."""

    message: str
    name: str


class BulkAssignmentResultItem(BaseModel):
    """Per-user result in bulk assignment response."""

    user_id: str
    action: Literal["assigned", "updated", "removed"]
    is_project_admin: Optional[bool] = None


class BulkAssignmentResponse(BaseModel):
    """Response for bulk assignment operations."""

    message: str
    project_name: str
    total: int
    results: list[BulkAssignmentResultItem]


logger = logging.getLogger(__name__)


def _resolve_cost_center_name(cost_center_id: UUID | None) -> str | None:
    if cost_center_id is None:
        return None

    with get_session() as session:
        cost_center = cost_center_repository.get_by_id(session, cost_center_id)
        return cost_center.name if cost_center else None


@router.post("/projects", response_model=ProjectCreateResponse, status_code=201)
def create_project(payload: ProjectCreateRequest, user: User = Depends(authenticate)):
    """Create a new shared project."""
    _ensure_user_management_enabled()
    create_kwargs = {
        "user": user,
        "project_name": payload.name,
        "description": payload.description,
    }
    if payload.cost_center_id is not None:
        create_kwargs["cost_center_id"] = payload.cost_center_id

    project = project_service.create_shared_project(
        **create_kwargs,
    )

    return ProjectCreateResponse(
        name=project.name,
        description=project.description or payload.description,
        project_type=project.project_type,
        created_by=project.created_by or user.id,
        created_at=project.date,
        cost_center_id=getattr(project, "cost_center_id", None),
        cost_center_name=_resolve_cost_center_name(getattr(project, "cost_center_id", None)),
    )


@router.get("/projects", response_model=PaginatedProjectListResponse)
def list_projects(
    search: Optional[str] = Query(
        None, description="Search by project name or description (substring match, visibility-filtered)"
    ),
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
    per_page: int = Query(20, ge=10, le=100, description="Items per page (10-100)"),
    include_counters: bool = Query(
        True,
        description="Include per-project resource counters (assistants, workflows, integrations, datasources, skills)",
    ),
    user: User = Depends(authenticate),
):
    """List projects visible to current user with pagination and search.

    Story 16: Project Management API - List endpoint with pagination.

    Regular users see only projects they are members of.
    Super admins see all projects (personal + shared).

    Response includes user_count, admin_count, and optional resource counters for each project.
    """

    _ensure_user_management_enabled()

    with get_session() as session:
        enriched_projects, total_count = project_visibility_service.list_visible_projects_paginated(
            session=session,
            user_id=user.id,
            is_admin=user.is_admin,
            search=search,
            page=page,
            per_page=per_page,
            include_counters=include_counters,
        )

        return PaginatedProjectListResponse(
            data=[ProjectListItem(**proj) for proj in enriched_projects],
            pagination=PaginationInfo(total=total_count, page=page, per_page=per_page),
        )


@router.get("/projects/{projectName}", response_model=ProjectDetailResponse)
def get_project_detail(
    request: Request,
    project_name: str = Path(alias="projectName"),
    user: User = Depends(authenticate),
):
    """Get project detail with member list if project is visible to current user.

    Story 16: Project Management API - Detail endpoint with member list.

    Returns 404 if project doesn't exist or user doesn't have access (not 403).

    Response includes:
    - Project metadata (name, description, type, creator, timestamps)
    - Member counts (user_count, admin_count)
    - Full member list with roles
    """

    _ensure_user_management_enabled()

    with get_session() as session:
        project_detail = project_visibility_service.get_visible_project_with_members(
            session=session,
            project_name=project_name,
            user_id=user.id,
            is_admin=user.is_admin,
            action=f"{request.method} {request.url.path}",
        )

        return ProjectDetailResponse(
            name=project_detail["name"],
            description=project_detail["description"],
            project_type=project_detail["project_type"],
            created_by=project_detail["created_by"],
            created_at=project_detail["created_at"],
            user_count=project_detail["user_count"],
            admin_count=project_detail["admin_count"],
            cost_center_id=project_detail.get("cost_center_id"),
            cost_center_name=project_detail.get("cost_center_name"),
            members=[ProjectMember(**m) for m in project_detail["members"]],
        )


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
        description=payload.description,
        cost_center_id=None if payload.clear_cost_center else payload.cost_center_id,
        clear_cost_center=payload.clear_cost_center,
    )

    return ProjectCreateResponse(
        name=project.name,
        description=project.description or "",
        project_type=project.project_type,
        created_by=project.created_by or user.id,
        created_at=project.date or datetime.now(UTC),
        cost_center_id=getattr(project, "cost_center_id", None),
        cost_center_name=_resolve_cost_center_name(getattr(project, "cost_center_id", None)),
    )


def _authorize_project_access(
    request: Request,
    project_name: str = Path(alias="projectName"),
    user: User = Depends(authenticate),
) -> Application:
    """Authorize project-level write access for project admins and super admins.

    Returns the project if the user has WRITE permission (owner, project admin, or super admin).
    Returns 404 instead of 403 to avoid revealing project existence to unauthorized users.
    """
    _ensure_user_management_enabled()

    action = f"{request.method} {request.url.path}"
    with get_session() as session:
        project = application_repository.get_by_name(session, project_name)

    # Treat deleted projects as non-existent
    if not project or project.deleted_at is not None:
        _raise_project_not_found(user_id=user.id, project_name=project_name, action=action)

    # Use Ability — returns 404 (not 403) to avoid revealing project existence
    if not Ability(user).can(Action.WRITE, project):
        _raise_project_not_found(user_id=user.id, project_name=project_name, action=action)

    return project


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


@router.post("/projects/{projectName}/assignment", response_model=ProjectAssignmentResponse)
def assign_user_to_project(
    request: Request,
    payload: ProjectAssignmentRequest,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Assign a user to project if requester is project admin or super admin."""
    from codemie.service.project.project_assignment_service import project_assignment_service

    with get_session() as session:
        result = project_assignment_service.assign_user_to_project(
            session=session,
            project=authorized_project,
            user_id=payload.user_id,
            project_name=project_name,
            is_project_admin=payload.is_project_admin,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return ProjectAssignmentResponse(**result)


@router.put("/projects/{projectName}/assignment/{userId}", response_model=ProjectAssignmentResponse)
def update_user_project_assignment(
    request: Request,
    payload: ProjectAssignmentUpdateRequest,
    project_name: str = Path(alias="projectName"),
    user_id: str = Path(alias="userId"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Update user's project-admin flag for a visible project."""
    from codemie.service.project.project_assignment_service import project_assignment_service

    with get_session() as session:
        result = project_assignment_service.update_user_project_role(
            session=session,
            project=authorized_project,
            user_id=user_id,
            project_name=project_name,
            is_project_admin=payload.is_project_admin,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return ProjectAssignmentResponse(**result)


@router.delete("/projects/{projectName}/assignment/{userId}", response_model=ProjectAssignmentResponse)
def remove_user_from_project(
    request: Request,
    project_name: str = Path(alias="projectName"),
    user_id: str = Path(alias="userId"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Remove user from project if requester is project admin or super admin."""
    from codemie.service.project.project_assignment_service import project_assignment_service

    with get_session() as session:
        result = project_assignment_service.remove_user_from_project(
            session=session,
            project=authorized_project,
            user_id=user_id,
            project_name=project_name,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return ProjectAssignmentResponse(**result)


@router.post("/projects/{projectName}/assignments", response_model=BulkAssignmentResponse)
def bulk_assign_users_to_project(
    request: Request,
    payload: BulkAssignmentRequest,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Bulk assign/upsert users to a project.

    Assigns new users and updates roles for existing members in a single
    atomic operation. All users are validated before any changes are applied.
    """
    from codemie.service.project.project_assignment_service import project_assignment_service

    with get_session() as session:
        results = project_assignment_service.bulk_assign_users_to_project(
            session=session,
            project=authorized_project,
            users=[{"user_id": u.user_id, "is_project_admin": u.is_project_admin} for u in payload.users],
            project_name=project_name,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return BulkAssignmentResponse(
        message=f"Bulk assignment completed: {len(results)} users processed",
        project_name=project_name,
        total=len(results),
        results=[BulkAssignmentResultItem(**r) for r in results],
    )


@router.post("/projects/{projectName}/import-users", response_model=BulkAssignmentResponse)
def assign_users_from_csv(
    request: Request,
    file: UploadFile,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Assign users to a project from a CSV file upload.

    CSV must include a header row with 'email' and 'role' columns.
    Allowed roles: 'administrator' (project admin), 'user' (regular member).

    All rows are validated before any changes are applied.
    Returns 422 with details if any emails are invalid, roles are unrecognized,
    or users cannot be found.
    """
    from codemie.service.project.csv_import_service import MAX_CSV_BYTES, csv_import_service

    _ensure_user_management_enabled()

    content = file.file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise ExtendedHTTPException(
            code=413,
            message="File too large",
            details=f"CSV file must not exceed {MAX_CSV_BYTES // (1024 * 1024)} MB",
        )

    with get_session() as session:
        results = csv_import_service.assign_from_csv(
            session=session,
            content=content,
            project=authorized_project,
            project_name=project_name,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return BulkAssignmentResponse(
        message=f"CSV import completed: {len(results)} users processed",
        project_name=project_name,
        total=len(results),
        results=[BulkAssignmentResultItem(**r) for r in results],
    )


@router.delete("/projects/{projectName}/assignments", response_model=BulkAssignmentResponse)
def bulk_remove_users_from_project(
    request: Request,
    user_id: list[str] = Query(
        ...,
        min_length=1,
        max_length=100,
        description="User IDs to remove (pass multiple times: ?user_id=x&user_id=y)",
    ),
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Bulk remove users from a project.

    Removes multiple users from the project in a single atomic operation.
    All users are validated before any removals are applied.
    """
    from codemie.service.project.project_assignment_service import project_assignment_service

    with get_session() as session:
        results = project_assignment_service.bulk_remove_users_from_project(
            session=session,
            project=authorized_project,
            user_ids=user_id,
            project_name=project_name,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return BulkAssignmentResponse(
        message=f"Bulk removal completed: {len(results)} users removed",
        project_name=project_name,
        total=len(results),
        results=[BulkAssignmentResultItem(**r) for r in results],
    )


def _raise_project_not_found(user_id: str, project_name: str, action: str) -> None:
    """Log a security-safe warning and raise 404.

    Project name is intentionally omitted from the log to prevent PII leakage.
    """
    timestamp = datetime.now(UTC).isoformat()
    http_method = action.split()[0] if action else "UNKNOWN"
    logger.warning(f"project_authorization_failed: user_id={user_id}, method={http_method}, timestamp={timestamp}")
    raise ExtendedHTTPException(code=404, message="Project not found")


def _ensure_user_management_enabled() -> None:
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message="User management not enabled")
