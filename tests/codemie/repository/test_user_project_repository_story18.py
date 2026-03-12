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

Repository layer tests for user_project_repository methods added in Story 18.
"""

from unittest.mock import MagicMock, patch

from codemie.repository.user_project_repository import user_project_repository
from codemie.rest_api.models.user_management import UserProject


class TestCanProjectAdminViewUser:
    """Tests for can_project_admin_view_user method"""

    def test_project_admin_can_view_user_in_their_project(self):
        """Project admin can view user who is in a project they admin"""
        # Admin A is admin of ProjectX, User B is in ProjectX
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 1  # Count > 0

        result = user_project_repository.can_project_admin_view_user(mock_session, "admin_a", "user_b")
        assert result is True

    def test_project_admin_cannot_view_user_not_in_their_projects(self):
        """Project admin cannot view user who is not in any project they admin"""
        # Admin A is NOT admin of ProjectZ (User C's only project)
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 0  # Count = 0

        result = user_project_repository.can_project_admin_view_user(mock_session, "admin_a", "user_c")
        assert result is False

    def test_project_admin_can_view_user_in_multiple_shared_projects(self):
        """Project admin can view user when they share multiple projects"""
        # Admin A is admin of ProjectX and ProjectY, User B is in both
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 2  # Count = 2 (multiple shared projects)

        result = user_project_repository.can_project_admin_view_user(mock_session, "admin_a", "user_b")
        assert result is True

    def test_non_admin_member_cannot_view_user(self):
        """User who is member but not admin of shared project cannot view other user"""
        # Admin A is regular member (not admin) of ProjectW
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 0  # Count = 0 (not admin of any shared project)

        result = user_project_repository.can_project_admin_view_user(mock_session, "admin_a", "user_b")
        assert result is False

    def test_user_with_no_admin_projects_cannot_view_anyone(self):
        """User with no admin projects cannot view any user"""
        # User B has no admin projects
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 0  # Count = 0

        result = user_project_repository.can_project_admin_view_user(mock_session, "user_b", "user_c")
        assert result is False

    def test_non_existent_admin_returns_false(self):
        """Non-existent admin user returns False"""
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 0  # Count = 0

        result = user_project_repository.can_project_admin_view_user(mock_session, "non_existent_admin", "user_b")
        assert result is False

    def test_non_existent_target_user_returns_false(self):
        """Non-existent target user returns False"""
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 0  # Count = 0

        result = user_project_repository.can_project_admin_view_user(mock_session, "admin_a", "non_existent_user")
        assert result is False


class TestGetAdminVisibleProjectsForUser:
    """Tests for get_admin_visible_projects_for_user method"""

    @patch.object(user_project_repository, "get_by_user_id")
    @patch.object(user_project_repository, "get_project_names_for_user")
    def test_returns_only_projects_where_admin_is_member(self, mock_get_project_names, mock_get_by_user_id):
        """Project admin sees only projects where they are also a member"""
        # Admin A is member of ProjectX and ProjectW (among User B's projects)
        # User B is in ProjectX, ProjectZ, ProjectW
        # Admin A should only see ProjectX and ProjectW (not ProjectZ)
        mock_session = MagicMock()

        # User B's projects
        user_b_projects = [
            UserProject(id="up1", user_id="user_b", project_name="ProjectX", is_project_admin=False),
            UserProject(id="up2", user_id="user_b", project_name="ProjectZ", is_project_admin=False),
            UserProject(id="up3", user_id="user_b", project_name="ProjectW", is_project_admin=False),
        ]
        mock_get_by_user_id.return_value = user_b_projects

        # Admin A's project memberships
        mock_get_project_names.return_value = {"ProjectX", "ProjectW", "ProjectY"}

        visible_projects = user_project_repository.get_admin_visible_projects_for_user(
            mock_session, "user_b", "admin_a"
        )

        project_names = {p.project_name for p in visible_projects}
        assert project_names == {"ProjectX", "ProjectW"}
        assert len(visible_projects) == 2

    @patch.object(user_project_repository, "get_by_user_id")
    @patch.object(user_project_repository, "get_project_names_for_user")
    def test_returns_empty_for_user_with_no_shared_projects(self, mock_get_project_names, mock_get_by_user_id):
        """Returns empty list when admin has no shared projects with target user"""
        # Admin A has no shared projects with User C (User C only in ProjectZ)
        mock_session = MagicMock()

        user_c_projects = [
            UserProject(id="up1", user_id="user_c", project_name="ProjectZ", is_project_admin=False),
        ]
        mock_get_by_user_id.return_value = user_c_projects

        # Admin A's project memberships (doesn't include ProjectZ)
        mock_get_project_names.return_value = {"ProjectX", "ProjectW", "ProjectY"}

        visible_projects = user_project_repository.get_admin_visible_projects_for_user(
            mock_session, "user_c", "admin_a"
        )

        assert len(visible_projects) == 0

    @patch.object(user_project_repository, "get_by_user_id")
    def test_returns_empty_for_non_existent_target_user(self, mock_get_by_user_id):
        """Returns empty list for non-existent target user"""
        mock_session = MagicMock()
        mock_get_by_user_id.return_value = []  # No projects for non-existent user

        visible_projects = user_project_repository.get_admin_visible_projects_for_user(
            mock_session, "non_existent_user", "admin_a"
        )

        assert len(visible_projects) == 0

    @patch.object(user_project_repository, "get_by_user_id")
    @patch.object(user_project_repository, "get_project_names_for_user")
    def test_returns_empty_for_non_existent_admin(self, mock_get_project_names, mock_get_by_user_id):
        """Returns empty list for non-existent admin user"""
        mock_session = MagicMock()

        user_b_projects = [
            UserProject(id="up1", user_id="user_b", project_name="ProjectX", is_project_admin=False),
        ]
        mock_get_by_user_id.return_value = user_b_projects

        # Non-existent admin has no projects
        mock_get_project_names.return_value = set()

        visible_projects = user_project_repository.get_admin_visible_projects_for_user(
            mock_session, "user_b", "non_existent_admin"
        )

        assert len(visible_projects) == 0

    @patch.object(user_project_repository, "get_by_user_id")
    @patch.object(user_project_repository, "get_project_names_for_user")
    def test_filtering_based_on_membership_not_admin_status(self, mock_get_project_names, mock_get_by_user_id):
        """Filtering is based on admin's membership, not admin status"""
        # Admin A is regular member (not admin) of ProjectW
        # User B is also in ProjectW
        # ProjectW should still be visible because Admin A is a member
        mock_session = MagicMock()

        user_b_projects = [
            UserProject(id="up1", user_id="user_b", project_name="ProjectX", is_project_admin=False),
            UserProject(id="up2", user_id="user_b", project_name="ProjectW", is_project_admin=False),
        ]
        mock_get_by_user_id.return_value = user_b_projects

        # Admin A is member of ProjectW (even though not admin)
        mock_get_project_names.return_value = {"ProjectX", "ProjectW"}

        visible_projects = user_project_repository.get_admin_visible_projects_for_user(
            mock_session, "user_b", "admin_a"
        )

        project_names = {p.project_name for p in visible_projects}
        assert "ProjectW" in project_names

    @patch.object(user_project_repository, "get_by_user_id")
    @patch.object(user_project_repository, "get_project_names_for_user")
    def test_preserves_user_project_attributes(self, mock_get_project_names, mock_get_by_user_id):
        """Returned UserProject objects preserve original attributes"""
        mock_session = MagicMock()

        user_b_projects = [
            UserProject(id="up1", user_id="user_b", project_name="ProjectX", is_project_admin=False),
            UserProject(id="up2", user_id="user_b", project_name="ProjectW", is_project_admin=True),
        ]
        mock_get_by_user_id.return_value = user_b_projects

        mock_get_project_names.return_value = {"ProjectX", "ProjectW"}

        visible_projects = user_project_repository.get_admin_visible_projects_for_user(
            mock_session, "user_b", "admin_a"
        )

        # Find ProjectX in results
        project_x = next(p for p in visible_projects if p.project_name == "ProjectX")

        # Verify attributes from User B's perspective (not admin)
        assert project_x.user_id == "user_b"
        assert project_x.project_name == "ProjectX"
        assert project_x.is_project_admin is False  # User B is not admin of ProjectX
