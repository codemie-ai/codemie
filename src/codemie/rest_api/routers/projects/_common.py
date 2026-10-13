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

import logging
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

from fastapi import Depends, Path, Request

from codemie.clients.postgres import get_session
from codemie.configs import config
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.repository.cost_center_repository import cost_center_repository
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User


logger = logging.getLogger(__name__)


def _resolve_cost_center_name(cost_center_id: UUID | None) -> str | None:
    if cost_center_id is None:
        return None

    with get_session() as session:
        cost_center = cost_center_repository.get_by_id(session, cost_center_id)
        return cost_center.name if cost_center else None


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
        _raise_project_not_found(user_id=user.id, action=action)

    # Use Ability — returns 404 (not 403) to avoid revealing project existence
    if not Ability(user).can(Action.WRITE, project):
        _raise_project_not_found(user_id=user.id, action=action)

    return project


def _raise_project_not_found(user_id: str, action: str) -> NoReturn:
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
