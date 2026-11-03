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

from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.analytics._common import (
    ERROR_MSG_ACCESS_DENIED,
    ERROR_MSG_ADMIN_HELP,
)
from codemie.rest_api.security.user import User


def _get_user_all_projects(user: User) -> list[str]:
    """Get all projects user has access to (regular + admin).

    Args:
        user: Authenticated user object

    Returns:
        List of all project names user can access
    """
    return list(set(user.project_names) | set(user.admin_project_names))


def _authorize_admin_budget_view(
    caller: User,
    target_project_names: set[str],
) -> None:
    """Raise HTTP 403 if caller is not authorized to view the target user's budget."""
    if caller.is_admin or getattr(caller, "is_auditor", False):
        return
    caller_admin_projects = set(caller.admin_project_names or [])
    if caller_admin_projects & target_project_names:
        return
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message=ERROR_MSG_ACCESS_DENIED,
        help=ERROR_MSG_ADMIN_HELP,
    )
