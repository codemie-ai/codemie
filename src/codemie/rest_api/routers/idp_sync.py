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

"""IdP-sync management API for the customer sync job (EPMCDME-16895).

Callable only by the one service account named in ``IDP_SYNC_SERVICE_ACCOUNT_USERNAME``. Every call is one
transaction: the handler commits once at the end and rolls back on any error.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlmodel import Session

from codemie.clients.postgres import get_session
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.idp_sync import (
    IdpAssignRequest,
    IdpUserPage,
    IdpUserResponse,
    IdpUserUpsertRequest,
)
from codemie.rest_api.security.idp_sync_guard import require_idp_sync_account
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_service import budget_service
from codemie.service.project.project_assignment_service import project_assignment_service
from codemie.service.user.authentication_service import invalidate_user_from_cache
from codemie.service.user.idp_sync_service import idp_sync_service

router = APIRouter(
    tags=["IdP sync"],
    prefix="/v1/idp-sync",
    dependencies=[Depends(require_idp_sync_account)],
)

_MAX_PAGE_SIZE = 100
_MAX_NAME_LENGTH = 255

UserIdPath = Annotated[str, Path(min_length=1, max_length=_MAX_NAME_LENGTH, description="User id in the IdP.")]
ProjectNamePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=_MAX_NAME_LENGTH,
        pattern=r"^\S(.*\S)?$",
        description="Project name, not blank, without leading or trailing spaces.",
    ),
]


@contextmanager
def _transaction() -> Iterator[Session]:
    """One session per call: commit at the end, roll back on any error."""
    with get_session() as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


@router.put(
    "/users/{user_id}",
    status_code=status.HTTP_200_OK,
    response_model=IdpUserResponse,
    summary="Create or update a user",
    description="Idempotent. Creates the user without a personal project, updates profile, the admin, maintainer "
    "and auditor flags and the user type (regular or external), and reactivates a deactivated user, keeping its "
    "project memberships and budget allocations. An omitted is_maintainer, is_auditor or user_type leaves the "
    "stored value alone; is_maintainer true needs is_admin true (422); removing the last active maintainer or "
    "administrator is refused with 409.",
)
def upsert_user(
    user_id: UserIdPath,
    request: IdpUserUpsertRequest,
    actor: User = Depends(require_idp_sync_account),  # noqa: B008 - FastAPI dependency injection
) -> IdpUserResponse:
    if request.id != user_id:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="User id mismatch",
            details="The id in the path and in the body must be the same.",
        )
    with _transaction() as session:
        result, created = idp_sync_service.upsert_user(session, actor, request)
    invalidate_user_from_cache(user_id)
    if created:
        budget_service.schedule_default_budget_provisioning(user_id, request.username)
    return result


@router.get(
    "/users/{user_id}",
    response_model=IdpUserResponse,
    summary="Get a user with memberships",
    description="Includes deactivated users.",
)
def get_user(user_id: UserIdPath) -> IdpUserResponse:
    with get_session() as session:
        return idp_sync_service.get_user(session, user_id)


@router.get(
    "/users",
    response_model=IdpUserPage,
    summary="List users",
    description="All users, deactivated included, ordered by id, with their project memberships.",
)
def list_users(
    page: int = Query(0, ge=0, description="Page number, starting from 0."),
    per_page: int = Query(_MAX_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE, description="Page size, up to 100."),
) -> IdpUserPage:
    with get_session() as session:
        return idp_sync_service.list_users(session, page, per_page)


@router.put(
    "/users/{user_id}/projects/{project_name}",
    response_model=IdpUserResponse,
    summary="Assign a user to a project",
    description="Idempotent. Creates the project when it does not exist; updates the project admin flag when it "
    "differs. The name of the personal project of the user is the user email: it creates that project when missing, "
    "turns an unshared project of that name into it, and adds the user to it. Another user's personal project and "
    "a project with other members are refused with 409.",
)
def assign_project(
    user_id: UserIdPath,
    project_name: ProjectNamePath,
    is_project_admin: bool = Query(False, description="Whether the user administers the project."),
    actor: User = Depends(require_idp_sync_account),  # noqa: B008 - FastAPI dependency injection
) -> IdpUserResponse:
    request = IdpAssignRequest(project_name=project_name, is_project_admin=is_project_admin)
    with _transaction() as session:
        result, changed_project = idp_sync_service.assign(session, actor, user_id, request)
    invalidate_user_from_cache(user_id)
    if changed_project:
        project_assignment_service.refresh_member_shares(changed_project)
    return result


@router.delete(
    "/users/{user_id}/projects/{project_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a user from a project",
    description="Idempotent: a user that is not a member is already done. The user may be removed from their own "
    "personal project; the project stays. Another user's personal project and the project creator are refused "
    "with 409.",
)
def unassign_project(
    user_id: UserIdPath,
    project_name: ProjectNamePath,
    actor: User = Depends(require_idp_sync_account),  # noqa: B008 - FastAPI dependency injection
) -> Response:
    with _transaction() as session:
        changed_project = idp_sync_service.unassign(session, actor, user_id, project_name)
    invalidate_user_from_cache(user_id)
    if changed_project:
        project_assignment_service.refresh_member_shares(changed_project)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/users/{user_id}/deactivate",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a user",
    description="Idempotent. A maintainer can be deactivated too. The last active administrator or maintainer is "
    "refused with 409, and so is the sync account itself.",
)
def deactivate_user(
    user_id: UserIdPath,
    actor: User = Depends(require_idp_sync_account),  # noqa: B008 - FastAPI dependency injection
) -> Response:
    with _transaction() as session:
        deactivated = idp_sync_service.deactivate(session, actor, user_id)
    if deactivated:
        idp_sync_service.after_deactivation_commit(user_id, actor.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
