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

"""User builders and session stubs shared by the project budget router tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

from codemie.configs import config
from codemie.rest_api.security.user import User


def _build_user(**fields) -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(**fields)


def admin_user() -> User:
    return _build_user(id="admin-1", username="admin@example.com", email="admin@example.com", is_admin=True)


def maintainer_user() -> User:
    return _build_user(id="maint-1", username="maint@example.com", email="maint@example.com", is_maintainer=True)


def project_admin_user(projects: list[str]) -> User:
    return _build_user(
        id="proj-admin-1",
        username="proj-admin@example.com",
        email="proj-admin@example.com",
        is_admin=False,
        admin_project_names=projects,
        project_names=projects,
    )


def auditor_user(admin_projects: list[str] | None = None) -> User:
    projects = admin_projects or []
    return _build_user(
        id="auditor-1",
        username="auditor@example.com",
        email="auditor@example.com",
        is_auditor=True,
        admin_project_names=projects,
        project_names=projects,
    )


def regular_user() -> User:
    return _build_user(id="other-1", username="other@example.com", email="other@example.com")


@asynccontextmanager
async def mock_session_ctx(session):
    yield session
