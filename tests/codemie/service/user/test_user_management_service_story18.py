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

"""Tests for Story 18: User Detail Filtering for Project Admins

Service layer tests for user_management_service methods updated in Story 18.
"""

import pytest
from unittest.mock import MagicMock, patch

from codemie.service.user.user_management_service import UserManagementService
from codemie.rest_api.models.user_management import UserDB, UserProject


@pytest.fixture
def mock_user():
    """Mock UserDB object"""
    return UserDB(
        id="target_user",
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
        project_limit=3,
    )


@pytest.fixture
def mock_user_projects():
    """Mock UserProject objects"""
    return [
        UserProject(id="up1", user_id="target_user", project_name="ProjectA", is_project_admin=False),
        UserProject(id="up2", user_id="target_user", project_name="ProjectB", is_project_admin=True),
        UserProject(id="up3", user_id="target_user", project_name="ProjectC", is_project_admin=False),
    ]


@pytest.fixture
def mock_filtered_projects():
    """Mock filtered UserProject objects (subset for project admin)"""
    return [
        UserProject(id="up1", user_id="target_user", project_name="ProjectA", is_project_admin=False),
        UserProject(id="up2", user_id="target_user", project_name="ProjectB", is_project_admin=True),
    ]


class TestGetUserWithRelationships:
    """Tests for get_user_with_relationships method with Story 18 enhancements"""

    @patch("codemie.service.user.user_management_service.user_repository.get_by_id")
    @patch("codemie.service.user.user_management_service.user_repository.get_user_knowledge_bases")
    @patch("codemie.repository.user_project_repository.user_project_repository.get_visible_projects_for_user")
    def test_super_admin_sees_all_projects(
        self, mock_get_visible, mock_get_kb, mock_get_user, mock_user, mock_user_projects
    ):
        """Super admin sees all projects using get_visible_projects_for_user"""
        mock_session = MagicMock()
        mock_get_user.return_value = mock_user
        mock_get_kb.return_value = []
        mock_get_visible.return_value = mock_user_projects

        result = UserManagementService.get_user_with_relationships(
            mock_session, "target_user", "super_admin", is_super_admin=True, is_project_admin=False
        )

        # Verify get_visible_projects_for_user was called (Story 10 logic)
        mock_get_visible.assert_called_once_with(mock_session, "target_user", "super_admin", True)

        # Verify all 3 projects are in result
        assert len(result.projects) == 3
        project_names = {p.name for p in result.projects}
        assert project_names == {"ProjectA", "ProjectB", "ProjectC"}

    @patch("codemie.service.user.user_management_service.user_repository.get_by_id")
    @patch("codemie.service.user.user_management_service.user_repository.get_user_knowledge_bases")
    @patch("codemie.repository.user_project_repository.user_project_repository.get_admin_visible_projects_for_user")
    def test_project_admin_sees_filtered_projects(
        self, mock_get_admin_visible, mock_get_kb, mock_get_user, mock_user, mock_filtered_projects
    ):
        """Project admin sees only projects where they are members using get_admin_visible_projects_for_user"""
        mock_session = MagicMock()
        mock_get_user.return_value = mock_user
        mock_get_kb.return_value = []
        mock_get_admin_visible.return_value = mock_filtered_projects

        result = UserManagementService.get_user_with_relationships(
            mock_session, "target_user", "project_admin", is_super_admin=False, is_project_admin=True
        )

        # Verify get_admin_visible_projects_for_user was called (Story 18 logic)
        mock_get_admin_visible.assert_called_once_with(mock_session, "target_user", "project_admin")

        # Verify only 2 projects are in result (filtered)
        assert len(result.projects) == 2
        project_names = {p.name for p in result.projects}
        assert project_names == {"ProjectA", "ProjectB"}

    @patch("codemie.service.user.user_management_service.user_repository.get_by_id")
    @patch("codemie.service.user.user_management_service.user_repository.get_user_knowledge_bases")
    @patch("codemie.repository.user_project_repository.user_project_repository.get_admin_visible_projects_for_user")
    def test_project_admin_sees_no_projects_when_no_overlap(
        self, mock_get_admin_visible, mock_get_kb, mock_get_user, mock_user
    ):
        """Project admin sees empty projects list when they have no shared projects with target user"""
        mock_session = MagicMock()
        mock_get_user.return_value = mock_user
        mock_get_kb.return_value = []
        mock_get_admin_visible.return_value = []

        result = UserManagementService.get_user_with_relationships(
            mock_session, "target_user", "project_admin", is_super_admin=False, is_project_admin=True
        )

        # Verify empty projects list
        assert len(result.projects) == 0

    @patch("codemie.service.user.user_management_service.user_repository.get_by_id")
    @patch("codemie.service.user.user_management_service.user_repository.get_user_knowledge_bases")
    def test_knowledge_bases_shown_in_full(self, mock_get_kb, mock_get_user, mock_user, mock_filtered_projects):
        """Knowledge bases array is shown in full for project admins (no filtering)"""
        mock_session = MagicMock()
        mock_get_user.return_value = mock_user
        mock_kb_list = ["kb1", "kb2", "kb3"]
        mock_get_kb.return_value = mock_kb_list

        with patch(
            "codemie.repository.user_project_repository.user_project_repository.get_admin_visible_projects_for_user",
            return_value=mock_filtered_projects,
        ):
            result = UserManagementService.get_user_with_relationships(
                mock_session, "target_user", "project_admin", is_super_admin=False, is_project_admin=True
            )

        # Verify all knowledge bases are returned
        assert result.knowledge_bases == mock_kb_list
        assert len(result.knowledge_bases) == 3

    @patch("codemie.service.user.user_management_service.user_repository.get_by_id")
    def test_returns_none_for_non_existent_user(self, mock_get_user):
        """Returns None when target user does not exist"""
        mock_session = MagicMock()
        mock_get_user.return_value = None

        result = UserManagementService.get_user_with_relationships(
            mock_session, "non_existent_user", "admin", is_super_admin=True, is_project_admin=False
        )

        assert result is None

    @patch("codemie.service.user.user_management_service.user_repository.get_by_id")
    @patch("codemie.service.user.user_management_service.user_repository.get_user_knowledge_bases")
    def test_regular_user_gets_empty_projects(self, mock_get_kb, mock_get_user, mock_user):
        """Regular user (not super admin, not project admin) gets empty projects list"""
        mock_session = MagicMock()
        mock_get_user.return_value = mock_user
        mock_get_kb.return_value = []

        result = UserManagementService.get_user_with_relationships(
            mock_session, "target_user", "regular_user", is_super_admin=False, is_project_admin=False
        )

        # Regular users should get empty projects (they shouldn't reach this point due to API auth)
        assert len(result.projects) == 0

    @patch("codemie.service.user.user_management_service.user_repository.get_by_id")
    @patch("codemie.service.user.user_management_service.user_repository.get_user_knowledge_bases")
    @patch("codemie.repository.user_project_repository.user_project_repository.get_admin_visible_projects_for_user")
    def test_preserves_is_project_admin_flag(
        self, mock_get_admin_visible, mock_get_kb, mock_get_user, mock_user, mock_filtered_projects
    ):
        """Response preserves is_project_admin flag from UserProject records"""
        mock_session = MagicMock()
        mock_get_user.return_value = mock_user
        mock_get_kb.return_value = []
        mock_get_admin_visible.return_value = mock_filtered_projects

        result = UserManagementService.get_user_with_relationships(
            mock_session, "target_user", "project_admin", is_super_admin=False, is_project_admin=True
        )

        # Check is_project_admin flags are preserved
        project_a = next(p for p in result.projects if p.name == "ProjectA")
        project_b = next(p for p in result.projects if p.name == "ProjectB")

        assert project_a.is_project_admin is False
        assert project_b.is_project_admin is True


class TestGetUserDetailFlow:
    """Tests for get_user_detail flow method"""

    @patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
    def test_passes_is_project_admin_flag(self, mock_get_relationships, mock_user):
        """Passes is_project_admin flag to get_user_with_relationships"""
        from codemie.rest_api.models.user_management import CodeMieUserDetail

        admin_detail = CodeMieUserDetail(
            id=mock_user.id,
            username=mock_user.username,
            email=mock_user.email,
            name=mock_user.name,
            picture=mock_user.picture,
            user_type=mock_user.user_type,
            is_active=mock_user.is_active,
            is_super_admin=mock_user.is_super_admin,
            auth_source=mock_user.auth_source,
            email_verified=mock_user.email_verified,
            last_login_at=mock_user.last_login_at,
            projects=[],
            project_limit=mock_user.project_limit,
            knowledge_bases=[],
            date=mock_user.date,
            update_date=mock_user.update_date,
            deleted_at=mock_user.deleted_at,
        )
        mock_get_relationships.return_value = admin_detail

        UserManagementService.get_user_detail(
            "target_user", "project_admin", is_super_admin=False, is_project_admin=True
        )

        # Verify is_project_admin flag was passed
        args, _ = mock_get_relationships.call_args
        assert args[2] == "project_admin"  # requesting_user_id
        assert args[3] is False  # is_super_admin
        assert args[4] is True  # is_project_admin

    @patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
    def test_raises_404_when_user_not_found(self, mock_get_relationships):
        """Raises ExtendedHTTPException 404 when user not found"""
        from codemie.core.exceptions import ExtendedHTTPException

        mock_get_relationships.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserManagementService.get_user_detail(
                "non_existent_user", "admin", is_super_admin=True, is_project_admin=False
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User not found"
