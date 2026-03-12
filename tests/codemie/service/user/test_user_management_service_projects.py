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

"""Unit tests for user management service - projects functionality (Story 3)

Tests the refactored response models with projects array structure replacing
applications/applications_admin fields.
"""

import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, patch
from uuid import uuid4

from codemie.rest_api.models.user_management import (
    UserDB,
    UserProject,
    CodeMieUserDetail,
    AdminUserListItem,
    ProjectInfo,
)
from codemie.service.user.user_management_service import UserManagementService


@pytest.fixture
def mock_session():
    """Mock database session"""
    return MagicMock()


@pytest.fixture
def sample_user_db():
    """Sample UserDB for testing"""
    return UserDB(
        id=str(uuid4()),
        username="testuser",
        email="test@example.com",
        name="Test User",
        password_hash="hashed_password",
        auth_source="local",
        user_type="regular",
        is_active=True,
        is_super_admin=False,
        email_verified=True,
        last_login_at=datetime.now(UTC),
        project_limit=3,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


@pytest.fixture
def sample_user_projects():
    """Sample user projects for testing"""
    user_id = str(uuid4())
    return [
        UserProject(
            id=str(uuid4()),
            user_id=user_id,
            project_name="project-a",
            is_project_admin=True,
            date=datetime.now(UTC),
        ),
        UserProject(
            id=str(uuid4()),
            user_id=user_id,
            project_name="project-b",
            is_project_admin=False,
            date=datetime.now(UTC),
        ),
    ]


class TestCodeMieUserDetailProjects:
    """Test CodeMieUserDetail with projects array"""

    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.service.user.user_management_service.user_repository")
    def test_get_user_with_relationships_includes_projects(
        self, mock_repo, mock_user_proj_repo, mock_session, sample_user_db, sample_user_projects
    ):
        """Test that get_user_with_relationships returns projects array"""
        # Arrange
        mock_repo.get_by_id.return_value = sample_user_db
        mock_repo.get_user_projects.return_value = sample_user_projects
        mock_repo.get_user_knowledge_bases.return_value = ["kb1", "kb2"]
        # Story 10: Mock visibility filtering to return all projects (admin can see all)
        mock_user_proj_repo.get_visible_projects_for_user.return_value = sample_user_projects

        # Act - Story 10: Pass requesting user context (self-request as super admin to see all projects)
        result = UserManagementService.get_user_with_relationships(
            mock_session, sample_user_db.id, sample_user_db.id, is_super_admin=True
        )

        # Assert
        assert isinstance(result, CodeMieUserDetail)
        assert len(result.projects) == 2
        assert all(isinstance(p, ProjectInfo) for p in result.projects)
        assert result.projects[0].name == "project-a"
        assert result.projects[0].is_project_admin is True
        assert result.projects[1].name == "project-b"
        assert result.projects[1].is_project_admin is False
        assert result.project_limit == 3
        assert result.knowledge_bases == ["kb1", "kb2"]

    @patch("codemie.service.user.user_management_service.user_repository")
    def test_get_user_with_no_projects(self, mock_repo, mock_session, sample_user_db):
        """Test user with no project assignments returns empty projects array"""
        # Arrange
        mock_repo.get_by_id.return_value = sample_user_db
        mock_repo.get_user_projects.return_value = []
        mock_repo.get_user_knowledge_bases.return_value = []

        # Act - Story 10: Pass requesting user context (self-request as super admin to see all projects)
        result = UserManagementService.get_user_with_relationships(
            mock_session, sample_user_db.id, sample_user_db.id, is_super_admin=True
        )

        # Assert
        assert isinstance(result, CodeMieUserDetail)
        assert result.projects == []  # Empty array, not None
        assert isinstance(result.projects, list)


class TestAdminUserListItemProjects:
    """Test AdminUserListItem with projects array"""

    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.service.user.user_management_service.user_repository")
    def test_list_users_includes_projects(self, mock_repo, mock_user_proj_repo, mock_session, sample_user_db):
        """Test that list_users returns projects array for each user (Story 7)"""
        # Arrange
        users = [sample_user_db]
        user_projects = [
            UserProject(
                id=str(uuid4()),
                user_id=sample_user_db.id,
                project_name="proj1",
                is_project_admin=True,
                date=datetime.now(UTC),
            ),
            UserProject(
                id=str(uuid4()),
                user_id=sample_user_db.id,
                project_name="proj2",
                is_project_admin=False,
                date=datetime.now(UTC),
            ),
        ]
        projects_map = {sample_user_db.id: user_projects}
        # Story 7: Repository now returns (users, projects_map, total)
        mock_repo.list_users.return_value = (users, projects_map, 1)
        # Story 10 Code Review R2: Mock bulk visibility filtering to return filtered map
        mock_user_proj_repo.filter_visible_projects_from_map.return_value = {sample_user_db.id: user_projects}

        # Act - Story 10: Pass requesting user context (admin user sees all projects)
        result = UserManagementService.list_users(
            mock_session, requesting_user_id="admin-user", is_super_admin=True, page=0, per_page=20
        )

        # Assert
        assert len(result.data) == 1
        user_item = result.data[0]
        assert isinstance(user_item, AdminUserListItem)
        assert len(user_item.projects) == 2
        assert user_item.projects[0].name == "proj1"
        assert user_item.projects[0].is_project_admin is True
        assert user_item.projects[1].name == "proj2"
        assert user_item.projects[1].is_project_admin is False
        assert hasattr(user_item, "user_type")  # AC: user_type in list response

    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.service.user.user_management_service.user_repository")
    def test_list_users_batch_fetch_avoids_n_plus_1(self, mock_repo, mock_user_proj_repo, mock_session, sample_user_db):
        """Test that projects are batch fetched to avoid N+1 queries (Story 7: JOIN + Story 10 R2: bulk filter)"""
        # Arrange
        user1 = sample_user_db
        user2 = UserDB(**{**sample_user_db.model_dump(), "id": str(uuid4()), "email": "user2@example.com"})
        users = [user1, user2]
        # Story 7: Repository returns projects via JOIN (no separate get_projects_for_users call)
        mock_repo.list_users.return_value = (users, {}, 2)
        # Story 10 Code Review R2: Mock bulk filtering to return empty filtered map
        mock_user_proj_repo.filter_visible_projects_from_map.return_value = {}

        # Act - Story 10: Pass requesting user context (admin user sees all projects)
        UserManagementService.list_users(
            mock_session, requesting_user_id="admin-user", is_super_admin=True, page=0, per_page=20
        )

        # Assert
        # Story 7: Verify list_users was called (includes JOIN now, no separate batch fetch)
        mock_repo.list_users.assert_called_once()
        # get_projects_for_users should NOT be called (moved into list_users via JOIN)
        mock_repo.get_projects_for_users.assert_not_called()
        # Story 10 Code Review R2: Verify bulk filtering was called (not per-user queries)
        mock_user_proj_repo.filter_visible_projects_from_map.assert_called_once()


class TestProjectInfoModel:
    """Test ProjectInfo response model structure"""

    def test_project_info_structure(self):
        """Test ProjectInfo model has correct fields with snake_case"""
        # Arrange & Act
        project = ProjectInfo(name="test-project", is_project_admin=True)

        # Assert
        assert project.name == "test-project"
        assert project.is_project_admin is True
        # Verify snake_case naming (not isProjectAdmin or is-project-admin)
        assert hasattr(project, "is_project_admin")

    def test_project_info_serialization(self):
        """Test ProjectInfo serializes correctly to JSON"""
        # Arrange
        project = ProjectInfo(name="my-project", is_project_admin=False)

        # Act
        json_data = project.model_dump()

        # Assert
        assert json_data == {"name": "my-project", "is_project_admin": False}
        assert "name" in json_data  # snake_case field name
        assert "is_project_admin" in json_data  # snake_case field name


class TestResponseModelTerminology:
    """Test that response models use 'projects' terminology (Story 3 AC)"""

    def test_admin_user_detail_no_applications_fields(self):
        """Test CodeMieUserDetail does not have applications/applications_admin fields"""
        # Arrange
        detail = CodeMieUserDetail(
            id="user-id",
            username="testuser",
            email="test@example.com",
            name="Test User",
            picture=None,
            user_type="regular",
            is_active=True,
            is_super_admin=False,
            auth_source="local",
            email_verified=True,
            last_login_at=None,
            projects=[ProjectInfo(name="proj1", is_project_admin=True)],
            project_limit=3,
            knowledge_bases=["kb1"],
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
            deleted_at=None,
        )

        # Assert
        assert hasattr(detail, "projects")
        assert hasattr(detail, "project_limit")
        assert not hasattr(detail, "applications")  # Removed field
        assert not hasattr(detail, "applications_admin")  # Removed field

    def test_admin_user_list_item_no_applications_fields(self):
        """Test AdminUserListItem does not have applications/applications_admin fields"""
        # Arrange
        item = AdminUserListItem(
            id="user-id",
            username="testuser",
            email="test@example.com",
            name="Test User",
            user_type="regular",
            is_active=True,
            is_super_admin=False,
            auth_source="local",
            last_login_at=None,
            projects=[ProjectInfo(name="proj1", is_project_admin=False)],
            date=datetime.now(UTC),
        )

        # Assert
        assert hasattr(item, "projects")
        assert hasattr(item, "user_type")  # AC: user_type in list response
        assert not hasattr(item, "applications")  # Removed field
        assert not hasattr(item, "applications_admin")  # Removed field


class TestSnakeCaseNaming:
    """Test that all JSON field names use snake_case (Story 3 AC)"""

    def test_project_info_uses_snake_case(self):
        """Test ProjectInfo uses snake_case field names"""
        project = ProjectInfo(name="test", is_project_admin=True)
        json_data = project.model_dump()

        # Assert all keys are snake_case
        for key in json_data:
            assert "_" in key or key.islower(), f"Field '{key}' is not snake_case"
            assert not any(c.isupper() for c in key), f"Field '{key}' contains uppercase (not snake_case)"

    def test_admin_user_detail_uses_snake_case(self):
        """Test CodeMieUserDetail uses snake_case field names"""
        detail = CodeMieUserDetail(
            id="user-id",
            username="testuser",
            email="test@example.com",
            name="Test User",
            picture=None,
            user_type="regular",
            is_active=True,
            is_super_admin=True,
            auth_source="local",
            email_verified=True,
            last_login_at=None,
            projects=[],
            project_limit=3,
            knowledge_bases=[],
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
            deleted_at=None,
        )
        json_data = detail.model_dump()

        # Check key fields use snake_case
        assert "is_super_admin" in json_data  # Not isAdmin or is-super-admin
        assert "is_active" in json_data
        assert "user_type" in json_data
        assert "auth_source" in json_data
        assert "email_verified" in json_data
        assert "last_login_at" in json_data
        assert "project_limit" in json_data
        assert "knowledge_bases" in json_data


class TestUserResponseSnakeCase:
    """Test UserResponse model JSON serialization (Story 3 High Priority Fix)"""

    def test_user_response_serializes_as_snake_case(self):
        """Test UserResponse produces snake_case JSON (not camelCase)

        This test addresses code review finding:
        - UserResponse must NOT use camelCase aliasing
        - All fields must serialize as snake_case per Story 3 AC
        """
        from codemie.core.models import UserResponse, ProjectInfoResponse

        # Arrange
        user_response = UserResponse(
            user_id="user-123",
            name="Test User",
            username="testuser",
            email="test@example.com",
            is_super_admin=True,
            projects=[ProjectInfoResponse(name="proj1", is_project_admin=True)],
            picture="http://example.com/pic.jpg",
            knowledge_bases=["kb1", "kb2"],
            user_type="regular",
        )

        # Act
        json_data = user_response.model_dump()

        # Assert - verify snake_case primary fields are present
        assert "user_id" in json_data
        assert "is_super_admin" in json_data
        assert "knowledge_bases" in json_data
        assert "user_type" in json_data

        # Assert - legacy camelCase fields exist for UI backward compatibility
        assert "userId" in json_data
        assert "userType" in json_data
        assert "isAdmin" in json_data

        # Assert - camelCase aliases that were never added as fields are absent
        assert "isSuperAdmin" not in json_data
        assert "knowledgeBases" not in json_data

        # Assert - legacy snake_case fields exist with defaults for backward compatibility
        assert "is_admin" in json_data  # legacy field for UI compatibility
        assert "applications" in json_data
        assert "applications_admin" in json_data

    def test_user_response_uses_is_super_admin_not_is_admin(self):
        """Test UserResponse uses is_super_admin (Story 3 terminology standardization)"""
        from codemie.core.models import UserResponse

        # Arrange & Act
        user_response = UserResponse(
            user_id="user-123",
            name="Test User",
            username="testuser",
            email="test@example.com",
            is_super_admin=True,
            projects=[],
            picture="",
            knowledge_bases=[],
            user_type="regular",
        )

        json_data = user_response.model_dump()

        # Assert
        assert "is_super_admin" in json_data
        assert json_data["is_super_admin"] is True
        # Legacy is_admin field exists for backward compatibility but defaults to False
        assert "is_admin" in json_data
        assert json_data["is_admin"] is False


class TestPaginationEnvelope:
    """Test pagination envelope matches Story 3 spec"""

    def test_pagination_info_structure(self):
        """Test PaginationInfo has correct fields (Story 3 AC)"""
        from codemie.rest_api.models.user_management import PaginationInfo

        # Arrange & Act
        pagination = PaginationInfo(total=100, page=2, per_page=20)
        json_data = pagination.model_dump()

        # Assert - verify exact fields from story spec
        assert set(json_data.keys()) == {"total", "page", "per_page"}
        assert json_data["total"] == 100
        assert json_data["page"] == 2
        assert json_data["per_page"] == 20

        # Assert - verify no extra fields (like total_pages)
        assert "total_pages" not in json_data

    @patch("codemie.service.user.user_management_service.user_repository")
    def test_paginated_response_structure(self, mock_repo, mock_session):
        """Test PaginatedUserListResponse matches story spec"""
        from codemie.rest_api.models.user_management import PaginatedUserListResponse

        # Arrange
        # Story 7: Repository returns (users, projects_map, total)
        mock_repo.list_users.return_value = ([], {}, 0)

        # Act - Story 10: Pass requesting user context (admin user sees all projects)
        result = UserManagementService.list_users(
            mock_session, requesting_user_id="admin-user", is_super_admin=True, page=0, per_page=20
        )

        # Assert
        assert isinstance(result, PaginatedUserListResponse)
        assert hasattr(result, "data")
        assert hasattr(result, "pagination")

        pagination_dict = result.pagination.model_dump()
        assert "total" in pagination_dict
        assert "page" in pagination_dict
        assert "per_page" in pagination_dict
        # Verify no extra fields
        assert len(pagination_dict) == 3
