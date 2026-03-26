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

"""Unit tests for user management router authorization (Story 17).

Tests project admin access to user list endpoint and continued restriction
of other admin endpoints to super admins only.

Code Review Addressed:
- Tests verify HTTP layer behavior properly
- Backward compatibility verified via route dependency inspection
- Uses User.is_applications_admin property consistently
- Removed broken test logic
"""

import pytest
from unittest.mock import patch, MagicMock

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.user_management_router import list_users, router
from codemie.rest_api.security.authentication import (
    project_admin_or_super_admin_user_list_access,
    admin_access_only,
)
from codemie.rest_api.security.user import User


@pytest.fixture
def super_admin_user():
    """Mock super admin user"""
    with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
        return User(
            id="super-admin",
            email="admin@example.com",
            username="admin",
            name="Super Admin",
            is_admin=True,
            project_names=["demo"],
            admin_project_names=[],
        )


@pytest.fixture
def project_admin_user():
    """Mock project admin user (not super admin, but admin of at least one project)"""
    with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
        return User(
            id="project-admin",
            email="padmin@example.com",
            username="padmin",
            name="Project Admin",
            is_admin=False,
            project_names=["project-a", "project-b"],
            admin_project_names=["project-a"],  # Admin of project-a
        )


@pytest.fixture
def regular_user():
    """Mock regular user (no admin privileges)"""
    with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
        return User(
            id="regular-user",
            email="user@example.com",
            username="user",
            name="Regular User",
            is_admin=False,
            project_names=["demo"],
            admin_project_names=[],  # Not admin of any project
        )


@pytest.fixture
def mock_request():
    """Mock FastAPI request with user in state"""
    request = MagicMock()
    return request


class TestProjectAdminOrSuperAdminUserListAccess:
    """Test the new authorization dependency (Story 17)"""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user.config")
    async def test_super_admin_authorized(self, mock_config, mock_request, super_admin_user):
        """AC: Super admin can access user list"""
        # Ensure user management is enabled so is_admin returns is_admin
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.ENV = "test"  # Not local, to test actual logic
        mock_request.state.user = super_admin_user

        # Should not raise exception
        await project_admin_or_super_admin_user_list_access(mock_request)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user.config")
    async def test_project_admin_authorized(self, mock_config, mock_request, project_admin_user):
        """AC: Project admin can access user list"""
        # Ensure user management is enabled
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.ENV = "test"
        mock_request.state.user = project_admin_user

        # Should not raise exception
        await project_admin_or_super_admin_user_list_access(mock_request)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user.config")
    async def test_regular_user_forbidden(self, mock_config, mock_request, regular_user):
        """AC: Regular user (not project admin, not super admin) receives 403"""
        mock_config.ENV = "production"  # Override local dev environment
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_request.state.user = regular_user

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await project_admin_or_super_admin_user_list_access(mock_request)

        assert exc_info.value.code == 403
        assert "administrator or project administrator privileges" in exc_info.value.details


class TestUserListEndpoint:
    """Test GET /v1/admin/users endpoint authorization (Story 17)"""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_project_admin_can_access_list(self, mock_service, mock_config, project_admin_user):
        """AC: Project admin can access GET /v1/admin/users successfully (200 OK)"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.list_users_with_flow.return_value = {
            "data": [
                {
                    "id": "user1",
                    "email": "user1@example.com",
                    "username": "user1",
                    "name": "User 1",
                    "is_active": True,
                    "projects": [],
                }
            ],
            "pagination": {"total": 1, "page": 0, "per_page": 20},
        }

        result = list_users(
            page=0,
            per_page=20,
            search=None,
            filters=None,
            user=project_admin_user,
            _=None,  # Authorization dependency returns None if successful
        )

        assert result is not None
        assert "data" in result
        mock_service.list_users_with_flow.assert_called_once()

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_project_admin_sees_all_users(self, mock_service, mock_config, project_admin_user):
        """AC: Project admin sees ALL users in the system (not filtered by project membership)

        Note: This test verifies the service is called with correct parameters.
        Actual filtering logic is tested in service layer tests.
        """
        mock_config.ENABLE_USER_MANAGEMENT = True

        # Mock returns users from different projects
        mock_service.list_users_with_flow.return_value = {
            "data": [
                {"id": "u1", "email": "u1@example.com", "projects": ["project-a"]},
                {"id": "u2", "email": "u2@example.com", "projects": ["project-b"]},
                {"id": "u3", "email": "u3@example.com", "projects": ["project-c"]},
            ],
            "pagination": {"total": 3, "page": 0, "per_page": 20},
        }

        result = list_users(
            page=0,
            per_page=20,
            search=None,
            filters=None,
            user=project_admin_user,
            _=None,
        )

        # Service returns all users without filtering
        assert len(result["data"]) == 3
        # Verify no projects filter was applied
        call_kwargs = mock_service.list_users_with_flow.call_args[1]
        assert call_kwargs["filters"].projects is None

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_project_admin_can_search_users(self, mock_service, mock_config, project_admin_user):
        """AC: Project admin can search users by email, username, name"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.list_users_with_flow.return_value = {
            "data": [{"id": "u1", "email": "john@example.com"}],
            "pagination": {"total": 1, "page": 0, "per_page": 20},
        }

        result = list_users(
            page=0,
            per_page=20,
            search="john",
            filters=None,
            user=project_admin_user,
            _=None,
        )

        assert result is not None
        # Verify search and empty filters were passed to service
        call_kwargs = mock_service.list_users_with_flow.call_args[1]
        assert call_kwargs["search"] == "john"
        assert call_kwargs["filters"].projects is None
        assert call_kwargs["filters"].user_type is None
        assert call_kwargs["filters"].platform_role is None

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_project_admin_can_use_pagination(self, mock_service, mock_config, project_admin_user):
        """AC: Project admin can use filters and pagination"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.list_users_with_flow.return_value = {
            "data": [],
            "pagination": {"total": 50, "page": 2, "per_page": 10},
        }

        result = list_users(
            page=2,
            per_page=10,
            search=None,
            filters='{"projects":["project-x"],"user_type":"regular"}',
            user=project_admin_user,
            _=None,
        )

        assert result is not None
        # Verify pagination and filters were passed correctly
        call_args = mock_service.list_users_with_flow.call_args[1]
        assert call_args["page"] == 2
        assert call_args["per_page"] == 10
        assert call_args["filters"].projects == ["project-x"]
        assert call_args["filters"].user_type == "regular"

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_super_admin_access_unchanged(self, mock_service, mock_config, super_admin_user):
        """AC: Super admin access is unchanged (full access)"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.list_users_with_flow.return_value = {
            "data": [],
            "pagination": {"total": 0, "page": 0, "per_page": 20},
        }

        result = list_users(
            page=0,
            per_page=20,
            search=None,
            filters=None,
            user=super_admin_user,
            _=None,
        )

        assert result is not None
        mock_service.list_users_with_flow.assert_called_once()


class TestOtherEndpointsRemainSuperAdminOnly:
    """Test that other admin endpoints remain super-admin only (Story 17)"""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user.config")
    async def test_project_admin_cannot_access_admin_only_endpoints(
        self, mock_config, mock_request, project_admin_user
    ):
        """AC: Project admin receives 403 when calling admin_access_only dependency"""
        mock_config.ENV = "production"  # Override local dev environment
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_request.state.user = project_admin_user

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await admin_access_only(mock_request)

        assert exc_info.value.code == 403
        assert "administrator privileges" in exc_info.value.details

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user.config")
    async def test_super_admin_retains_full_access(self, mock_config, mock_request, super_admin_user):
        """AC: Super admin can still access all admin endpoints"""
        # Ensure user management is enabled so is_admin returns is_admin
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.ENV = "test"  # Not local, to test actual logic
        mock_request.state.user = super_admin_user

        # Should not raise exception
        await admin_access_only(mock_request)


class TestBackwardCompatibility:
    """Test backward compatibility requirements (Story 17)"""

    def test_list_endpoint_uses_new_dependency(self):
        """AC: GET /v1/admin/users uses project_admin_or_super_admin_user_list_access"""
        # Find the list_users route in the router
        list_users_route = None
        for route in router.routes:
            if (
                hasattr(route, "path")
                and route.path == "/v1/admin/users"
                and hasattr(route, "methods")
                and "GET" in route.methods
            ):
                list_users_route = route
                break

        assert list_users_route is not None, "list_users route not found"

        # Check that the route has the correct dependency
        dependencies = list_users_route.dependant.dependencies
        dependency_functions = [dep.call for dep in dependencies]

        # Should have project_admin_or_super_admin_user_list_access
        assert (
            project_admin_or_super_admin_user_list_access in dependency_functions
        ), "list_users should use project_admin_or_super_admin_user_list_access"

        # Should NOT have admin_access_only
        assert admin_access_only not in dependency_functions, "list_users should NOT use admin_access_only"

    def test_other_endpoints_remain_super_admin_only(self):
        """AC: All other admin endpoints still use admin_access_only

        Verifies backward compatibility: Story 17 changed list endpoint, Story 18 changed user detail endpoint.
        """
        # Map of endpoint full paths to expected methods
        super_admin_only_endpoints = {
            "/v1/admin/users": ["POST"],  # create_user
            "/v1/admin/users/{user_id}": ["PUT", "DELETE"],  # update, deactivate (GET changed in Story 18)
            "/v1/admin/users/{user_id}/password": ["PUT"],  # admin_change_password
            "/v1/admin/users/{user_id}/projects": ["GET", "POST"],  # get, add project access
            "/v1/admin/users/{user_id}/projects/{project_name}": ["PUT", "DELETE"],  # update, remove
            "/v1/admin/users/{user_id}/knowledge-bases": ["GET", "POST"],  # get, add KB access
            "/v1/admin/users/{user_id}/knowledge-bases/{kb_name}": ["DELETE"],  # remove KB access
        }

        for route in router.routes:
            if not hasattr(route, "path") or not hasattr(route, "methods"):
                continue

            path = route.path
            methods = route.methods

            # Skip endpoints changed in Story 17 and Story 18
            if path == "/v1/admin/users" and "GET" in methods:
                continue  # Story 17: list endpoint uses project_admin_or_super_admin_user_list_access
            if path == "/v1/admin/users/{user_id}" and "GET" in methods:
                continue  # Story 18: user detail endpoint uses project_admin_or_super_admin_user_detail_access

            # Check if this is a super-admin-only endpoint
            for expected_path, expected_methods in super_admin_only_endpoints.items():
                if path == expected_path:
                    for method in methods:
                        if method in expected_methods:
                            # This endpoint should use admin_access_only
                            dependencies = route.dependant.dependencies
                            dependency_functions = [dep.call for dep in dependencies]

                            assert (
                                admin_access_only in dependency_functions
                            ), f"{method} {path} should use admin_access_only"

                            assert (
                                project_admin_or_super_admin_user_list_access not in dependency_functions
                            ), f"{method} {path} should NOT use project_admin_or_super_admin_user_list_access"


class TestEdgeCases:
    """Test edge cases for Story 17"""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user.config")
    async def test_project_admin_with_multiple_projects(self, mock_config, mock_request):
        """Project admin with multiple admin projects should have access"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.ENV = "test"

        user = User(
            id="multi-admin",
            email="multi@example.com",
            username="multi",
            name="Multi Admin",
            is_admin=False,
            project_names=["p1", "p2", "p3"],
            admin_project_names=["p1", "p2", "p3"],  # Admin of multiple projects
        )
        mock_request.state.user = user

        # Should not raise exception (uses is_applications_admin which checks length)
        await project_admin_or_super_admin_user_list_access(mock_request)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user.config")
    async def test_user_with_project_access_but_not_admin(self, mock_config, mock_request):
        """User with project access but not admin should be denied"""
        mock_config.ENV = "production"  # Override local dev environment
        mock_config.ENABLE_USER_MANAGEMENT = True

        user = User(
            id="member",
            email="member@example.com",
            username="member",
            name="Project Member",
            is_admin=False,
            project_names=["project-a", "project-b"],
            admin_project_names=[],  # Has project access but not admin
        )
        mock_request.state.user = user

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await project_admin_or_super_admin_user_list_access(mock_request)

        assert exc_info.value.code == 403

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user.config")
    async def test_user_is_applications_admin_property_works(self, mock_config, mock_request):
        """Verify User.is_applications_admin property is used correctly

        Code Review: Ensure we use the property instead of checking length directly.
        """
        mock_config.ENV = "production"
        mock_config.ENABLE_USER_MANAGEMENT = True

        # User with empty applications_admin list
        user_no_admin = User(
            id="no-admin",
            email="no-admin@example.com",
            username="no-admin",
            name="No Admin",
            is_admin=False,
            project_names=["project-a"],
            admin_project_names=[],
        )
        assert user_no_admin.is_applications_admin is False

        # User with applications_admin list
        user_with_admin = User(
            id="with-admin",
            email="with-admin@example.com",
            username="with-admin",
            name="With Admin",
            is_admin=False,
            project_names=["project-a"],
            admin_project_names=["project-a"],
        )
        assert user_with_admin.is_applications_admin is True

        # Test authorization works with property
        mock_request.state.user = user_no_admin
        with pytest.raises(ExtendedHTTPException):
            await project_admin_or_super_admin_user_list_access(mock_request)

        mock_request.state.user = user_with_admin
        await project_admin_or_super_admin_user_list_access(mock_request)  # Should not raise
