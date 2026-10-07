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

"""Tests for set_default_project / clear_default_project endpoint functions (EPMCDME-15110).

Calls the endpoint functions directly (this router's established test style — see
test_user_management_router_crud.py:927), passing None for the Depends(...) parameter.
TestDefaultProjectPermissionGate proves both routes carry the platform admin/maintainer
dependency and that it rejects auditor, plain-user and project-admin callers.
"""

from unittest.mock import patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.user_management_router import clear_default_project, set_default_project
from codemie.rest_api.security.user import User


@pytest.fixture
def admin_user():
    return User(
        id="admin-1",
        email="admin@example.com",
        username="admin",
        name="Admin",
        is_admin=True,
        is_maintainer=False,
        is_auditor=False,
        project_names=["demo"],
        admin_project_names=[],
    )


class TestSetDefaultProjectEndpoint:
    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_calls_service_with_correct_args(self, mock_service, mock_config, admin_user):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.set_default_project.return_value = {"message": "Default project set successfully"}

        result = set_default_project("user-456", "demo-project", admin_user, None)

        assert result == {"message": "Default project set successfully"}
        mock_service.set_default_project.assert_called_once_with(
            user_id="user-456", project_name="demo-project", actor=admin_user
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_disabled_management_returns_400(self, mock_config, admin_user):
        mock_config.ENABLE_USER_MANAGEMENT = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            set_default_project("user-456", "demo-project", admin_user, None)

        assert exc_info.value.code == 400


class TestClearDefaultProjectEndpoint:
    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_calls_service_with_correct_args(self, mock_service, mock_config, admin_user):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.clear_default_project.return_value = {"message": "Default project cleared successfully"}

        result = clear_default_project("user-456", "demo-project", admin_user, None)

        assert result == {"message": "Default project cleared successfully"}
        mock_service.clear_default_project.assert_called_once_with(
            user_id="user-456", project_name="demo-project", actor=admin_user
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_disabled_management_returns_400(self, mock_config, admin_user):
        mock_config.ENABLE_USER_MANAGEMENT = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            clear_default_project("user-456", "demo-project", admin_user, None)

        assert exc_info.value.code == 400


class TestDefaultProjectPermissionGate:
    """15110 AC4: only platform admins/maintainers may set or clear a default — never auditors,
    plain users or project admins (is_project_admin shares the membership row, easy to confuse)."""

    @pytest.mark.parametrize("method,path", [("PUT", "set"), ("DELETE", "clear")])
    def test_routes_require_platform_admin_or_maintainer(self, method, path):
        from codemie.rest_api.routers.user_management_router import router
        from codemie.rest_api.security.authentication import admin_or_maintainer_access_only

        route = next(
            r
            for r in router.routes
            if r.path.endswith("/{user_id}/projects/{project_name}/default") and method in r.methods
        )
        dependencies = {d.call for d in route.dependant.dependencies}
        assert admin_or_maintainer_access_only in dependencies

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", True)
    @patch("codemie.rest_api.security.user.config.ENV", "production")
    @pytest.mark.parametrize(
        "flags,allowed",
        [
            ({"is_admin": True}, True),
            ({"is_maintainer": True}, True),
            ({"is_auditor": True}, False),
            ({}, False),
            ({"admin_project_names": ["proj-a"]}, False),
        ],
        ids=["admin", "maintainer", "auditor", "plain-user", "project-admin"],
    )
    async def test_dependency_allows_only_platform_roles(self, flags, allowed):
        from unittest.mock import MagicMock

        from codemie.rest_api.security.authentication import admin_or_maintainer_access_only

        actor = User(id="actor-1", username="actor", project_names=["proj-a"], **flags)
        request = MagicMock()
        request.state.user = actor

        if allowed:
            await admin_or_maintainer_access_only(request)
        else:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await admin_or_maintainer_access_only(request)
            assert exc_info.value.code == 403
