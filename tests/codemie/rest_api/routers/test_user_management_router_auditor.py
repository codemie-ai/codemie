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

"""Tests for auditor access in user management router (EPMCDME-10930)."""

import pytest
from unittest.mock import patch

from codemie.configs import config
from codemie.rest_api.routers.user_management_router import get_user, list_users, router
from codemie.rest_api.security.authentication import admin_access_only, admin_or_maintainer_or_auditor_access
from codemie.rest_api.security.user import User


@pytest.fixture
def auditor_user():
    with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
        return User(
            id="auditor-1",
            email="auditor@example.com",
            username="auditor",
            name="Auditor",
            is_admin=False,
            is_maintainer=False,
            is_auditor=True,
            project_names=["demo"],
            admin_project_names=[],
        )


class TestAuditorListUsers:
    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_auditor_can_list_users(self, mock_service, mock_config, auditor_user):
        """Auditor gets 200 and list_users_with_flow is called with is_project_admin=True."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.list_users_with_flow.return_value = {
            "data": [],
            "pagination": {"total": 0, "page": 0, "per_page": 20},
        }

        result = list_users(
            page=0, per_page=20, search=None, filters=None, sort_by=None, sort_order="asc", user=auditor_user
        )

        assert result is not None
        call_kwargs = mock_service.list_users_with_flow.call_args
        assert call_kwargs.kwargs.get("is_project_admin") is True or (call_kwargs.args and call_kwargs.args[1] is True)

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_auditor_sees_full_user_list(self, mock_service, mock_config, auditor_user):
        """EPMCDME-10930 spec 5.2: GET /v1/admin/users returns the full, non-empty user
        list for a pure auditor (not filtered down to zero results)."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.list_users_with_flow.return_value = {
            "data": [
                {"id": "u1", "email": "u1@example.com", "projects": ["project-a"]},
                {"id": "u2", "email": "u2@example.com", "projects": ["project-b"]},
                {"id": "u3", "email": "u3@example.com", "projects": ["project-c"]},
            ],
            "pagination": {"total": 3, "page": 0, "per_page": 20},
        }

        result = list_users(
            page=0, per_page=20, search=None, filters=None, sort_by=None, sort_order="asc", user=auditor_user
        )

        assert len(result["data"]) == 3

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_non_auditor_regular_user_is_project_admin_false(self, mock_service, mock_config):
        """Regular user (no admin/auditor) gets is_project_admin=False."""
        with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
            regular = User(
                id="reg-1",
                email="reg@example.com",
                username="reg",
                is_admin=False,
                is_maintainer=False,
                is_auditor=False,
                admin_project_names=[],
            )
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.list_users_with_flow.return_value = {
            "data": [],
            "pagination": {"total": 0, "page": 0, "per_page": 20},
        }
        list_users(page=0, per_page=20, search=None, filters=None, sort_by=None, sort_order="asc", user=regular)
        call_kwargs = mock_service.list_users_with_flow.call_args
        assert call_kwargs.kwargs.get("is_project_admin") is False or (
            call_kwargs.args and call_kwargs.args[1] is False
        )


class TestAuditorGetUserDetail:
    """EPMCDME-10930 regression: pure auditor must not get an empty projects list.

    get_user() previously derived is_admin from user.is_admin_or_maintainer only, so a
    pure auditor (is_admin=False, is_maintainer=False, is_applications_admin=False) fell
    into get_user_with_relationships()'s "regular users should not reach here" branch and
    always got back projects=[], even though the auditor is authorized past the route guard.
    """

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_auditor_get_user_passes_is_admin_true(self, mock_service, mock_config, auditor_user):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.get_user_detail.return_value = None

        get_user(user_id="target-1", user=auditor_user)

        call = mock_service.get_user_detail.call_args
        is_admin_arg = call.args[2] if len(call.args) > 2 else call.kwargs.get("is_admin")
        assert is_admin_arg is True

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_non_auditor_regular_user_get_user_passes_is_admin_false(self, mock_service, mock_config):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.get_user_detail.return_value = None
        with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
            regular = User(
                id="reg-1",
                email="reg@example.com",
                username="reg",
                is_admin=False,
                is_maintainer=False,
                is_auditor=False,
                admin_project_names=[],
            )

        get_user(user_id="target-1", user=regular)

        call = mock_service.get_user_detail.call_args
        is_admin_arg = call.args[2] if len(call.args) > 2 else call.kwargs.get("is_admin")
        assert is_admin_arg is False


class TestAuditorReadRouteDependencyWiring:
    """EPMCDME-10930 spec 5.2: confirm the auditor-inclusive dependency is actually
    wired onto the read routes it's supposed to gate (not just that the dependency
    function itself works in isolation).
    """

    def _find_route(self, path: str, method: str):
        for route in router.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route
        raise AssertionError(f"{method} {path} route not found")

    @pytest.mark.parametrize(
        "path",
        [
            "/v1/admin/users/{user_id}/projects",
            "/v1/admin/users/{user_id}/knowledge-bases",
            "/v1/admin/users/{user_id}/budgets",
        ],
    )
    def test_route_uses_auditor_inclusive_dependency(self, path):
        route = self._find_route(path, "GET")
        dependency_functions = [dep.call for dep in route.dependant.dependencies]
        assert admin_or_maintainer_or_auditor_access in dependency_functions
        assert admin_access_only not in dependency_functions

    def test_list_users_route_has_no_role_gate(self):
        """GET /v1/admin/users (EPMCDME-14298): restored to authenticate-only so
        project admins can list their own projects' users; is_project_admin scoping
        happens in list_users_with_flow, not via a route-level role dependency.
        """
        route = self._find_route("/v1/admin/users", "GET")
        dependency_functions = [dep.call for dep in route.dependant.dependencies]
        assert admin_or_maintainer_or_auditor_access not in dependency_functions
        assert admin_access_only not in dependency_functions


class TestAuditorRepositoryFilter:
    def test_user_repository_is_auditor_filter_applied(self):
        """_apply_filters applies is_auditor=True as a WHERE clause."""
        from sqlmodel import select
        from codemie.rest_api.models.user_management import UserListFilters, UserDB
        from codemie.repository.user_repository import UserRepository

        query = select(UserDB)
        result = UserRepository._apply_filters(query, None, UserListFilters(is_auditor=True))
        compiled = str(result.whereclause.compile())
        assert "is_auditor" in compiled

    def test_user_repository_no_filter_when_is_auditor_none(self):
        """_apply_filters does NOT add is_auditor clause when filter is None."""
        from sqlmodel import select
        from codemie.rest_api.models.user_management import UserListFilters, UserDB

        from codemie.repository.user_repository import UserRepository

        query = select(UserDB)
        result = UserRepository._apply_filters(query, None, UserListFilters(is_auditor=None))
        compiled = str(result.whereclause.compile() if result.whereclause is not None else "")
        assert "is_auditor" not in compiled

    def test_user_repository_is_auditor_filter_applied_with_platform_admin_projects_combo(self):
        """EPMCDME-10930 regression: is_auditor must not be dropped when combined with
        platform_role=platform_admin + projects, which takes an early-return branch that
        previously bypassed the is_auditor where-clause entirely.
        """
        from sqlmodel import select
        from codemie.rest_api.models.user_management import UserListFilters, UserDB, PlatformRole

        from codemie.repository.user_repository import UserRepository

        query = select(UserDB)
        result = UserRepository._apply_filters(
            query,
            None,
            UserListFilters(is_auditor=True, platform_role=PlatformRole.PLATFORM_ADMIN, projects=["demo"]),
        )
        compiled = str(result.whereclause.compile())
        assert "is_auditor" in compiled

    def test_user_repository_is_auditor_filter_applied_with_user_role_projects_combo(self):
        """Same regression, for the platform_role=user + projects early-return branch."""
        from sqlmodel import select
        from codemie.rest_api.models.user_management import UserListFilters, UserDB, PlatformRole

        from codemie.repository.user_repository import UserRepository

        query = select(UserDB)
        result = UserRepository._apply_filters(
            query,
            None,
            UserListFilters(is_auditor=True, platform_role=PlatformRole.USER, projects=["demo"]),
        )
        compiled = str(result.whereclause.compile())
        assert "is_auditor" in compiled
