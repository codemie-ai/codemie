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

"""Register repository remotes to a shared project (EPMCDME-16440). The remote is never logged."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel

from codemie.clients.postgres import get_session
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.rest_api.routers.projects._common import _ensure_user_management_enabled, _raise_project_not_found
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.project.repository_registration_service import (
    RepositoryRegistrationRepository,
    registration_key,
)

router = APIRouter()


class RepositoryRemoteRequest(BaseModel):
    """Name the repository by exactly one of: `remote` (a raw URL or scp spelling, normalized) or `key` (the
    identity a response returned, taken exactly as is). `host:8443/a/b` is a different repository as a raw scp
    remote and as a key, which is why the two are separate fields. A response item is a valid body."""

    remote: str | None = None
    key: str | None = None


class RepositoryRegistrationResponse(BaseModel):
    key: str
    project_name: str


def _authorize(
    request: Request,
    project_name: str = Path(alias="projectName"),
    user: User = Depends(authenticate),
) -> Application:
    """Global admin/maintainer or this project's admin; 404 otherwise (does not reveal the project)."""
    _ensure_user_management_enabled()
    with get_session() as session:
        project = application_repository.get_by_name(session, project_name)
    action = f"{request.method} {request.url.path}"
    if not project or project.deleted_at is not None:
        _raise_project_not_found(user_id=user.id, action=action)
    if not (user.is_admin_or_maintainer or user.is_application_admin(project_name)):
        _raise_project_not_found(user_id=user.id, action=action)
    return project


def _shared(project: Application = Depends(_authorize)) -> Application:
    if project.project_type == Application.ProjectType.PERSONAL:
        raise ExtendedHTTPException(code=400, message="Personal projects cannot have registered repositories")
    return project


def _out(row) -> RepositoryRegistrationResponse:
    return RepositoryRegistrationResponse(key=row.normalized_remote, project_name=row.project_name)


@router.get("/projects/{projectName}/repositories", response_model=list[RepositoryRegistrationResponse])
def list_repositories(project: Application = Depends(_authorize)):
    with get_session() as session:
        return [_out(r) for r in RepositoryRegistrationRepository.list_for_project(session, project.name)]


@router.put("/projects/{projectName}/repositories", response_model=RepositoryRegistrationResponse)
def register_repository(
    payload: RepositoryRemoteRequest,
    project: Application = Depends(_shared),
    user: User = Depends(authenticate),
):
    with get_session() as session:
        return _out(
            RepositoryRegistrationRepository.register(
                session, project.name, user.id, remote=payload.remote, key=payload.key
            )
        )


@router.delete("/projects/{projectName}/repositories", response_model=RepositoryRegistrationResponse)
def unregister_repository(payload: RepositoryRemoteRequest, project: Application = Depends(_authorize)):
    try:
        registration_key(payload.remote, payload.key)
    except ValueError:
        raise ExtendedHTTPException(code=400, message="Unparseable repository remote") from None
    with get_session() as session:
        key = RepositoryRegistrationRepository.unregister(session, project.name, remote=payload.remote, key=payload.key)
    if key is None:
        raise ExtendedHTTPException(code=404, message="Repository registration not found")
    return RepositoryRegistrationResponse(key=key, project_name=project.name)
