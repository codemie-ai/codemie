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

"""Unit tests for user management router CRUD operations.

Tests cover user CRUD, project access management, and knowledge base access management
endpoints in the user management router.

Coverage target: >= 80% for user_management_router.py
"""

import pytest
from unittest.mock import patch
from datetime import datetime

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.user_management_router import (
    create_user,
    update_user,
    deactivate_user,
    admin_change_password,
    list_users,
    get_user,
    get_user_projects,
    add_project_access,
    update_project_access,
    remove_project_access,
    get_user_knowledge_bases,
    add_knowledge_base_access,
    remove_knowledge_base_access,
    AdminPasswordChangeRequest,  # Defined inline in router
)
from codemie.rest_api.models.user_management import (
    UserCreateRequest,
    UserUpdateRequest,
    CodeMieUserDetail,
    ProjectAccessRequest,
    ProjectAccessUpdateRequest,
    KnowledgeBaseAccessRequest,
    PaginatedUserListResponse,
    AdminUserListItem,
    PaginationInfo,
)
from codemie.rest_api.security.user import User


# ===========================================
# Fixtures
# ===========================================


@pytest.fixture
def admin_user():
    """Mock super admin user for testing admin-only endpoints."""
    return User(
        id="admin-123",
        email="admin@example.com",
        username="admin",
        name="Admin User",
        is_super_admin=True,
        project_names=["demo"],
        admin_project_names=[],
    )


@pytest.fixture
def regular_user():
    """Mock regular user for testing access control."""
    return User(
        id="user-123",
        email="user@example.com",
        username="user",
        name="Regular User",
        is_super_admin=False,
        project_names=["demo"],
        admin_project_names=[],
    )


@pytest.fixture
def mock_user_detail():
    """Mock CodeMieUserDetail response."""
    return CodeMieUserDetail(
        id="user-456",
        username="newuser",
        email="newuser@example.com",
        name="New User",
        picture=None,
        user_type="regular",
        is_active=True,
        is_super_admin=False,
        auth_source="local",
        email_verified=False,
        last_login_at=None,
        projects=[],
        project_limit=None,
        knowledge_bases=[],
        date=datetime.now(),
        update_date=datetime.now(),
        deleted_at=None,
    )


# ===========================================
# User CRUD Tests
# ===========================================


class TestCreateUser:
    """Tests for POST /v1/admin/users endpoint."""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_create_user_success(
        self,
        mock_service,
        mock_config,
        admin_user,
        mock_user_detail,
    ):
        """Test successful user creation in local mode.

        AC: Super admin can create users when ENABLE_USER_MANAGEMENT=True and IDP_PROVIDER=local
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"

        request_data = UserCreateRequest(
            email="newuser@example.com",
            username="newuser",
            password="SecurePass123!",
            name="New User",
            is_super_admin=False,
        )

        mock_service.create_local_user_with_flow.return_value = mock_user_detail

        # Act
        result = create_user(request_data, admin_user, None)

        # Assert
        assert result == mock_user_detail
        mock_service.create_local_user_with_flow.assert_called_once_with(
            email="newuser@example.com",
            username="newuser",
            password="SecurePass123!",
            name="New User",
            is_super_admin=False,
            actor_user_id="admin-123",
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_create_user_disabled_management(self, mock_config, admin_user):
        """Test user creation fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        request_data = UserCreateRequest(
            email="newuser@example.com",
            username="newuser",
            password="SecurePass123!",
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            create_user(request_data, admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_create_user_non_local_mode(self, mock_config, admin_user):
        """Test user creation fails in IDP mode.

        AC: Returns 400 when IDP_PROVIDER is not 'local'
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "keycloak"

        request_data = UserCreateRequest(
            email="newuser@example.com",
            username="newuser",
            password="SecurePass123!",
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            create_user(request_data, admin_user, None)

        assert exc_info.value.code == 400
        assert "User creation only available in local auth mode" in exc_info.value.message


class TestUpdateUser:
    """Tests for PUT /v1/admin/users/{user_id} endpoint."""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_update_user_success(
        self,
        mock_service,
        mock_config,
        admin_user,
        mock_user_detail,
    ):
        """Test successful user update.

        AC: Super admin can update user fields
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        request_data = UserUpdateRequest(
            name="Updated Name",
            picture="https://example.com/pic.jpg",
            is_active=True,
        )

        updated_user = mock_user_detail.model_copy()
        updated_user.name = "Updated Name"
        mock_service.update_user_fields.return_value = updated_user

        # Act
        result = update_user("user-456", request_data, admin_user, None)

        # Assert
        assert result.name == "Updated Name"
        mock_service.update_user_fields.assert_called_once_with(
            user_id="user-456",
            actor_user_id="admin-123",
            name="Updated Name",
            picture="https://example.com/pic.jpg",
            email=None,
            username=None,
            user_type=None,
            is_super_admin=None,
            is_active=True,
            project_limit=None,
            project_limit_provided=False,
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_update_user_with_project_limit(
        self,
        mock_service,
        mock_config,
        admin_user,
        mock_user_detail,
    ):
        """Test updating user with explicit project limit.

        AC: Supports setting project_limit to explicit values or null
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        request_data = UserUpdateRequest(
            project_limit=5,
        )
        request_data.project_limit_provided = True

        mock_service.update_user_fields.return_value = mock_user_detail

        # Act
        update_user("user-456", request_data, admin_user, None)

        # Assert
        mock_service.update_user_fields.assert_called_once()
        call_kwargs = mock_service.update_user_fields.call_args[1]
        assert call_kwargs["project_limit"] == 5
        assert call_kwargs["project_limit_provided"] is True

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_update_user_disabled_management(self, mock_config, admin_user):
        """Test user update fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        request_data = UserUpdateRequest(name="Updated Name")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_user("user-456", request_data, admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message


class TestDeactivateUser:
    """Tests for DELETE /v1/admin/users/{user_id} endpoint."""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_deactivate_user_success(
        self,
        mock_service,
        mock_config,
        admin_user,
    ):
        """Test successful user deactivation.

        AC: Super admin can deactivate users (soft delete)
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        mock_service.deactivate_user_flow.return_value = {"message": "User deactivated"}

        # Act
        result = deactivate_user("user-456", admin_user, None)

        # Assert
        assert result == {"message": "User deactivated"}
        mock_service.deactivate_user_flow.assert_called_once_with(
            "user-456",
            "admin-123",
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_deactivate_user_disabled_management(self, mock_config, admin_user):
        """Test user deactivation fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            deactivate_user("user-456", admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message


class TestAdminChangePassword:
    """Tests for PUT /v1/admin/users/{user_id}/password endpoint."""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.password_management_service")
    def test_admin_change_password_success(
        self,
        mock_service,
        mock_config,
        admin_user,
    ):
        """Test successful admin password change.

        AC: Super admin can change user password without current password
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        request_data = AdminPasswordChangeRequest(new_password="NewSecurePass123!")

        mock_service.admin_change_password_flow.return_value = {"message": "Password changed"}

        # Act
        result = admin_change_password("user-456", request_data, admin_user, None)

        # Assert
        assert result == {"message": "Password changed"}
        mock_service.admin_change_password_flow.assert_called_once_with(
            user_id="user-456",
            new_password="NewSecurePass123!",
            actor_user_id="admin-123",
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_admin_change_password_disabled_management(self, mock_config, admin_user):
        """Test admin password change fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        request_data = AdminPasswordChangeRequest(new_password="NewSecurePass123!")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            admin_change_password("user-456", request_data, admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message


# ===========================================
# Project Access Management Tests
# ===========================================


class TestProjectAccess:
    """Tests for project access management endpoints."""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_assign_project_access_success(
        self,
        mock_service,
        mock_config,
        admin_user,
    ):
        """Test successful project access assignment.

        AC: Super admin can grant project access to users
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        request_data = ProjectAccessRequest(
            project_name="demo-project",
            is_project_admin=False,
        )

        mock_service.grant_project_access.return_value = {"message": "Access granted"}

        # Act
        result = add_project_access("user-456", request_data, admin_user, None)

        # Assert
        assert result == {"message": "Access granted"}
        mock_service.grant_project_access.assert_called_once_with(
            user_id="user-456",
            project_name="demo-project",
            is_project_admin=False,
            actor_user_id="admin-123",
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_assign_project_access_as_admin(
        self,
        mock_service,
        mock_config,
        admin_user,
    ):
        """Test assigning project access with admin privileges.

        AC: Can assign users as project admins
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        request_data = ProjectAccessRequest(
            project_name="demo-project",
            is_project_admin=True,
        )

        mock_service.grant_project_access.return_value = {"message": "Access granted"}

        # Act
        add_project_access("user-456", request_data, admin_user, None)

        # Assert
        mock_service.grant_project_access.assert_called_once()
        call_kwargs = mock_service.grant_project_access.call_args[1]
        assert call_kwargs["is_project_admin"] is True

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_assign_project_access_disabled_management(self, mock_config, admin_user):
        """Test project access assignment fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        request_data = ProjectAccessRequest(project_name="demo-project")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            add_project_access("user-456", request_data, admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_revoke_project_access_success(
        self,
        mock_service,
        mock_config,
        admin_user,
    ):
        """Test successful project access revocation.

        AC: Super admin can revoke project access from users
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        mock_service.revoke_project_access.return_value = {"message": "Access revoked"}

        # Act
        result = remove_project_access("user-456", "demo-project", admin_user, None)

        # Assert
        assert result == {"message": "Access revoked"}
        mock_service.revoke_project_access.assert_called_once_with(
            user_id="user-456",
            project_name="demo-project",
            actor_user_id="admin-123",
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_revoke_project_access_disabled_management(self, mock_config, admin_user):
        """Test project access revocation fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            remove_project_access("user-456", "demo-project", admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message


# ===========================================
# Knowledge Base Access Management Tests
# ===========================================


class TestKnowledgeBaseAccess:
    """Tests for knowledge base access management endpoints."""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_assign_kb_access_success(
        self,
        mock_service,
        mock_config,
        admin_user,
    ):
        """Test successful knowledge base access assignment.

        AC: Super admin can grant knowledge base access to users
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        request_data = KnowledgeBaseAccessRequest(kb_name="docs-kb")

        mock_service.grant_kb_access.return_value = {"message": "KB access granted"}

        # Act
        result = add_knowledge_base_access("user-456", request_data, admin_user, None)

        # Assert
        assert result == {"message": "KB access granted"}
        mock_service.grant_kb_access.assert_called_once_with(
            user_id="user-456",
            kb_name="docs-kb",
            actor_user_id="admin-123",
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_assign_kb_access_disabled_management(self, mock_config, admin_user):
        """Test knowledge base access assignment fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        request_data = KnowledgeBaseAccessRequest(kb_name="docs-kb")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            add_knowledge_base_access("user-456", request_data, admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_revoke_kb_access_success(
        self,
        mock_service,
        mock_config,
        admin_user,
    ):
        """Test successful knowledge base access revocation.

        AC: Super admin can revoke knowledge base access from users
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        mock_service.revoke_kb_access.return_value = {"message": "KB access revoked"}

        # Act
        result = remove_knowledge_base_access("user-456", "docs-kb", admin_user, None)

        # Assert
        assert result == {"message": "KB access revoked"}
        mock_service.revoke_kb_access.assert_called_once_with(
            user_id="user-456",
            kb_name="docs-kb",
            actor_user_id="admin-123",
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_revoke_kb_access_disabled_management(self, mock_config, admin_user):
        """Test knowledge base access revocation fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            remove_knowledge_base_access("user-456", "docs-kb", admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message


# ===========================================
# Additional Endpoint Tests for Coverage
# ===========================================


class TestListUsers:
    """Tests for GET /v1/admin/users endpoint."""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_list_users_success(
        self,
        mock_service,
        mock_config,
        admin_user,
    ):
        """Test successful user listing with pagination.

        AC: Super admin can list users with pagination
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        mock_response = PaginatedUserListResponse(
            data=[
                AdminUserListItem(
                    id="user-1",
                    username="user1",
                    email="user1@example.com",
                    name="User One",
                    user_type="regular",
                    is_active=True,
                    is_super_admin=False,
                    auth_source="local",
                    last_login_at=None,
                    projects=[],
                    date=datetime.now(),
                )
            ],
            pagination=PaginationInfo(total=1, page=0, per_page=20),
        )

        mock_service.list_users_with_flow.return_value = mock_response

        # Act
        result = list_users(
            page=0,
            per_page=20,
            search=None,
            is_active=None,
            project_name=None,
            user_type=None,
            user=admin_user,
            _=None,
        )

        # Assert
        assert result == mock_response
        mock_service.list_users_with_flow.assert_called_once()

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_list_users_disabled_management(self, mock_config, admin_user):
        """Test user listing fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_users(
                page=0,
                per_page=20,
                search=None,
                is_active=None,
                project_name=None,
                user_type=None,
                user=admin_user,
                _=None,
            )

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_list_users_invalid_per_page(self, mock_config, admin_user):
        """Test user listing fails with invalid per_page value.

        AC: Returns 400 when per_page is not 10, 20, 50, or 100
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_users(
                page=0,
                per_page=15,  # Invalid value
                search=None,
                is_active=None,
                project_name=None,
                user_type=None,
                user=admin_user,
                _=None,
            )

        assert exc_info.value.code == 400
        assert "per_page must be one of: 10, 20, 50, 100" in exc_info.value.message


class TestGetUser:
    """Tests for GET /v1/admin/users/{user_id} endpoint."""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_get_user_success(
        self,
        mock_service,
        mock_config,
        admin_user,
        mock_user_detail,
    ):
        """Test successful user detail retrieval.

        AC: Super admin can get user details
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        mock_service.get_user_detail.return_value = mock_user_detail

        # Act
        result = get_user("user-456", admin_user, None)

        # Assert
        assert result == mock_user_detail
        mock_service.get_user_detail.assert_called_once()

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_get_user_disabled_management(self, mock_config, admin_user):
        """Test user detail retrieval fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_user("user-456", admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message


class TestGetUserProjects:
    """Tests for GET /v1/admin/users/{user_id}/projects endpoint."""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_get_user_projects_success(
        self,
        mock_service,
        mock_config,
        admin_user,
    ):
        """Test successful retrieval of user's project access.

        AC: Super admin can view user's project access
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        mock_service.get_user_projects_list.return_value = {
            "projects": [{"project_name": "demo-project", "is_project_admin": False, "date": datetime.now()}]
        }

        # Act
        result = get_user_projects("user-456", admin_user, None)

        # Assert
        assert "projects" in result
        assert len(result["projects"]) == 1
        mock_service.get_user_projects_list.assert_called_once_with("user-456")

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_get_user_projects_disabled_management(self, mock_config, admin_user):
        """Test getting user projects fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_user_projects("user-456", admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message


class TestUpdateProjectAccess:
    """Tests for PUT /v1/admin/users/{user_id}/projects/{project_name} endpoint."""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_update_project_access_success(
        self,
        mock_service,
        mock_config,
        admin_user,
    ):
        """Test successful project access update.

        AC: Super admin can update user's project admin status
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        request_data = ProjectAccessUpdateRequest(is_project_admin=True)

        mock_service.update_user_project_access.return_value = {"message": "Access updated"}

        # Act
        result = update_project_access("user-456", "demo-project", request_data, admin_user, None)

        # Assert
        assert result == {"message": "Access updated"}
        mock_service.update_user_project_access.assert_called_once_with(
            user_id="user-456",
            project_name="demo-project",
            is_project_admin=True,
            actor_user_id="admin-123",
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_update_project_access_disabled_management(self, mock_config, admin_user):
        """Test project access update fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        request_data = ProjectAccessUpdateRequest(is_project_admin=True)

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_project_access("user-456", "demo-project", request_data, admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message


class TestGetUserKnowledgeBases:
    """Tests for GET /v1/admin/users/{user_id}/knowledge-bases endpoint."""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_get_user_knowledge_bases_success(
        self,
        mock_service,
        mock_config,
        admin_user,
    ):
        """Test successful retrieval of user's knowledge base access.

        AC: Super admin can view user's knowledge base access
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        mock_service.get_user_knowledge_bases_list.return_value = {
            "knowledge_bases": [{"kb_name": "docs-kb", "date": datetime.now()}]
        }

        # Act
        result = get_user_knowledge_bases("user-456", admin_user, None)

        # Assert
        assert "knowledge_bases" in result
        assert len(result["knowledge_bases"]) == 1
        mock_service.get_user_knowledge_bases_list.assert_called_once_with("user-456")

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_get_user_knowledge_bases_disabled_management(self, mock_config, admin_user):
        """Test getting user knowledge bases fails when user management is disabled.

        AC: Returns 400 when ENABLE_USER_MANAGEMENT=False
        """
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_user_knowledge_bases("user-456", admin_user, None)

        assert exc_info.value.code == 400
        assert "User management not enabled" in exc_info.value.message
