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

"""Who may see and change the per-project ``cli_analytics_enabled`` setting.

Write is reserved for global admins/maintainers. Read adds Project Admins of that one project.
``is_applications_admin``, auditors and the ``platform_admin`` role label never grant either.
"""

from __future__ import annotations

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User

FIELD = "cli_analytics_enabled"


def can_view(user: User, project_name: str) -> bool:
    return user.is_admin_or_maintainer or user.is_application_admin(project_name)


def visible_value(user: User, project_name: str, value: bool) -> bool | None:
    """The value for a viewer allowed to see it, else None (the response key is then omitted)."""
    return value if can_view(user, project_name) else None


def require_write(user: User, fields_set: set[str]) -> None:
    """403 when the request carries the field (any value, even null/unchanged) without global write."""
    if FIELD in fields_set and not user.is_admin_or_maintainer:
        raise ExtendedHTTPException(
            code=403,
            message="Access denied",
            details="Only global administrators and maintainers can change the CLI analytics setting.",
        )
