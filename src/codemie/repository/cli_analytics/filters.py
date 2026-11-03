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

"""Resolved query filters shared by every CLI Analytics storage adapter."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any


class LocalAnalyticsFilter:
    """Resolved query filters shared by every Local Analytics query.

    `scope` (a ViewerScope) is mandatory and is enforced in the base conditions of the sessions CTE.
    `deny_all` selects no session at all. The router sets it for a project admin who
    administers no project, instead of an impossible project name: PostgreSQL rejects the
    NUL character the old sentinel relied on.
    """

    def __init__(
        self,
        start_dt: datetime,
        end_dt: datetime,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        repositories: list[str] | None = None,
        branch: str | None = None,
        deny_all: bool = False,
        project_unattributed: bool = False,
        subscription_types: list[str] | None = None,
        project_origins: list[str] | None = None,
        *,
        scope: Any,
    ) -> None:
        self.start_dt = start_dt
        self.end_dt = end_dt
        self.users = users or None
        self.projects = projects or None
        self.repositories = repositories or None
        self.branch = branch or None
        self.deny_all = deny_all
        self.project_unattributed = project_unattributed
        self.session_ids: list[str] | None = None
        # Response-vocabulary values (see scope.normalise_*); they only narrow, the scope still applies.
        self.subscription_types = [v.strip().lower() for v in subscription_types or [] if v.strip()] or None
        self.project_origins = [v.strip().lower() for v in project_origins or [] if v.strip()] or None
        # Required, no default: the ViewerScope every list/aggregate is cut to. Absent or malformed denies all.
        self.scope = scope

    def restricted_to(self, session_ids: list[str]) -> LocalAnalyticsFilter:
        """A copy that additionally requires `session_id` to be in `session_ids`. Every other filter stays in
        force: the ids narrow the selection, they never replace the user / project scope."""
        narrowed = copy.copy(self)
        narrowed.session_ids = list(session_ids)
        return narrowed

    @property
    def has_session_filter(self) -> bool:
        return bool(
            self.deny_all
            or self.users
            or self.projects
            or self.repositories
            or self.project_unattributed
            or self.subscription_types
            or self.project_origins
        )

    def params(self) -> dict:
        p: dict = {"start_dt": self.start_dt, "end_dt": self.end_dt}
        if self.users:
            p["users"] = [u.lower() for u in self.users]
        if self.projects and not self.project_unattributed:
            p["projects"] = self.projects
        if self.repositories:
            p["repositories"] = self.repositories
        if self.branch:
            p["branch"] = self.branch
        if self.subscription_types:
            p["subscription_types"] = self.subscription_types
        if self.project_origins:
            p["project_origins"] = self.project_origins
        return p
