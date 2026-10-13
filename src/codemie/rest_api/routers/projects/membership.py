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

from fastapi import APIRouter, Depends, Path, Query, Request, UploadFile

from codemie.clients.postgres import get_session
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.rest_api.models.project import (
    BulkAssignmentRequest,
    BulkAssignmentResponse,
    BulkAssignmentResultItem,
    CsvImportRowResult,
    CsvImportValidationResponse,
    ProjectAssignmentRequest,
    ProjectAssignmentResponse,
    ProjectAssignmentUpdateRequest,
)
from codemie.rest_api.routers.projects._common import (
    _authorize_project_access,
    _ensure_user_management_enabled,
)


router = APIRouter()


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


@router.post("/projects/{projectName}/import-users/validate", response_model=CsvImportValidationResponse)
def validate_users_from_csv(
    file: UploadFile,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Dry-run validation of a CSV file for user import.

    Validates each row (email format, role, system user existence) without
    modifying any data. Returns per-row results with email, role, and error.
    Structural errors (decode failure, missing columns, row count) still return 422.
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
        results = csv_import_service.validate_csv(session=session, content=content)

    return CsvImportValidationResponse(users=[CsvImportRowResult(**r) for r in results])


@router.post("/projects/{projectName}/import-users", response_model=BulkAssignmentResponse)
def import_users_from_csv(
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
