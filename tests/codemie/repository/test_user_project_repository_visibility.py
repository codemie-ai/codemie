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

"""Tests for project visibility filtering in user_project_repository.

Story 11: Project Visibility Rules

Tests cover:
- Personal projects visible only to creator + super admin
- Shared projects visible only to members + super admin
- get_visible_projects_for_user filtering logic
"""

from unittest.mock import MagicMock, patch

from codemie.repository.user_project_repository import user_project_repository
from codemie.rest_api.models.user_management import UserProject


class TestProjectVisibilityFiltering:
    """Test get_visible_projects_for_user filters personal projects correctly"""

    @patch("codemie.repository.application_repository.application_repository")
    def test_creator_sees_own_personal_project(self, mock_app_repo):
        """Test: Creator sees their own personal project"""
        # Arrange
        mock_session = MagicMock()
        user_id = "alice-123"

        # Alice has 1 personal project + 1 shared project
        mock_projects = [
            UserProject(user_id=user_id, project_name="alice@example.com", is_project_admin=False),  # Personal
            UserProject(user_id=user_id, project_name="shared-proj", is_project_admin=True),  # Shared
        ]

        # Mock get_by_user_id to return Alice's projects
        with patch.object(user_project_repository, "get_by_user_id", return_value=mock_projects):
            # Story 10 Code Review: Mock bulk project type lookup
            mock_app_repo.get_project_types_bulk.return_value = {
                "alice@example.com": ("personal", user_id),  # Personal project
                "shared-proj": ("shared", user_id),  # Shared project
            }

            # Act: Alice requesting her own projects
            visible = user_project_repository.get_visible_projects_for_user(
                session=mock_session, target_user_id=user_id, requesting_user_id=user_id, is_super_admin=False
            )

            # Assert: Alice sees both projects
            assert len(visible) == 2
            assert visible[0].project_name == "alice@example.com"
            assert visible[1].project_name == "shared-proj"

    @patch("codemie.repository.application_repository.application_repository")
    def test_non_creator_cannot_see_personal_project(self, mock_app_repo):
        """Test: Non-creator/non-member cannot see someone else's projects"""
        # Arrange
        mock_session = MagicMock()
        alice_id = "alice-123"
        bob_id = "bob-456"

        # Alice has 1 personal project + 1 shared project
        mock_projects = [
            UserProject(user_id=alice_id, project_name="alice@example.com", is_project_admin=False),  # Personal
            UserProject(user_id=alice_id, project_name="shared-proj", is_project_admin=True),  # Shared
        ]

        with (
            patch.object(user_project_repository, "get_by_user_id", return_value=mock_projects),
            patch.object(
                user_project_repository,
                "get_project_names_for_user",
                return_value=set(),
            ),
        ):
            # Story 10 Code Review: Mock bulk project type lookup
            mock_app_repo.get_project_types_bulk.return_value = {
                "alice@example.com": ("personal", alice_id),  # Personal project
                "shared-proj": ("shared", alice_id),  # Shared project
            }

            # Act: Bob requesting Alice's projects (Bob is not super admin)
            visible = user_project_repository.get_visible_projects_for_user(
                session=mock_session, target_user_id=alice_id, requesting_user_id=bob_id, is_super_admin=False
            )

            # Assert: Bob sees nothing (not creator of personal, not member of shared)
            assert len(visible) == 0

    @patch("codemie.repository.application_repository.application_repository")
    def test_non_creator_member_sees_shared_project_only(self, mock_app_repo):
        """Test: Non-creator member can see shared project, not personal project"""
        # Arrange
        mock_session = MagicMock()
        alice_id = "alice-123"
        bob_id = "bob-456"

        mock_projects = [
            UserProject(user_id=alice_id, project_name="alice@example.com", is_project_admin=False),  # Personal
            UserProject(user_id=alice_id, project_name="shared-proj", is_project_admin=True),  # Shared
        ]

        with (
            patch.object(user_project_repository, "get_by_user_id", return_value=mock_projects),
            patch.object(
                user_project_repository,
                "get_project_names_for_user",
                return_value={"shared-proj"},
            ),
        ):
            mock_app_repo.get_project_types_bulk.return_value = {
                "alice@example.com": ("personal", alice_id),
                "shared-proj": ("shared", alice_id),
            }

            # Act: Bob requesting Alice's projects (Bob is shared project member)
            visible = user_project_repository.get_visible_projects_for_user(
                session=mock_session, target_user_id=alice_id, requesting_user_id=bob_id, is_super_admin=False
            )

            # Assert: Bob sees shared project only
            assert len(visible) == 1
            assert visible[0].project_name == "shared-proj"

    @patch("codemie.repository.application_repository.application_repository")
    def test_super_admin_sees_all_projects_including_personal(self, mock_app_repo):
        """Test: Super admin sees all projects including personal ones"""
        # Arrange
        mock_session = MagicMock()
        alice_id = "alice-123"
        admin_id = "admin-789"

        # Alice has 1 personal project + 1 shared project
        mock_projects = [
            UserProject(user_id=alice_id, project_name="alice@example.com", is_project_admin=False),  # Personal
            UserProject(user_id=alice_id, project_name="shared-proj", is_project_admin=True),  # Shared
        ]

        with patch.object(user_project_repository, "get_by_user_id", return_value=mock_projects):
            # Story 10 Code Review: Mock bulk project type lookup
            mock_app_repo.get_project_types_bulk.return_value = {
                "alice@example.com": ("personal", alice_id),  # Personal project
                "shared-proj": ("shared", alice_id),  # Shared project
            }

            # Act: Admin requesting Alice's projects (admin IS super admin)
            visible = user_project_repository.get_visible_projects_for_user(
                session=mock_session, target_user_id=alice_id, requesting_user_id=admin_id, is_super_admin=True
            )

            # Assert: Admin sees both projects
            assert len(visible) == 2
            assert visible[0].project_name == "alice@example.com"
            assert visible[1].project_name == "shared-proj"

    @patch("codemie.repository.application_repository.application_repository")
    def test_only_shared_projects_all_visible(self, mock_app_repo):
        """Test: For shared projects, requester sees only memberships they have"""
        # Arrange
        mock_session = MagicMock()
        alice_id = "alice-123"
        bob_id = "bob-456"

        # Alice has 2 shared projects (no personal)
        mock_projects = [
            UserProject(user_id=alice_id, project_name="shared-proj-1", is_project_admin=True),
            UserProject(user_id=alice_id, project_name="shared-proj-2", is_project_admin=False),
        ]

        with (
            patch.object(user_project_repository, "get_by_user_id", return_value=mock_projects),
            patch.object(
                user_project_repository,
                "get_project_names_for_user",
                return_value={"shared-proj-2"},
            ),
        ):
            # Story 10 Code Review: Mock bulk project type lookup
            mock_app_repo.get_project_types_bulk.return_value = {
                "shared-proj-1": ("shared", alice_id),  # Shared project
                "shared-proj-2": ("shared", alice_id),  # Shared project
            }

            # Act: Bob requesting Alice's projects
            visible = user_project_repository.get_visible_projects_for_user(
                session=mock_session, target_user_id=alice_id, requesting_user_id=bob_id, is_super_admin=False
            )

            # Assert: Bob sees only shared project where he is a member
            assert len(visible) == 1
            assert visible[0].project_name == "shared-proj-2"

    def test_no_projects_returns_empty(self):
        """Test: User with no projects returns empty list"""
        # Arrange
        mock_session = MagicMock()
        alice_id = "alice-123"
        bob_id = "bob-456"

        with patch.object(user_project_repository, "get_by_user_id", return_value=[]):
            # Act
            visible = user_project_repository.get_visible_projects_for_user(
                session=mock_session, target_user_id=alice_id, requesting_user_id=bob_id, is_super_admin=False
            )

            # Assert
            assert len(visible) == 0

    def test_get_project_names_for_user_uses_project_name_select(self):
        """Test: get_project_names_for_user fetches names directly (no full-row hydration)."""
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = ["proj-a", "proj-b", "proj-a"]

        result = user_project_repository.get_project_names_for_user(mock_session, "user-123")

        assert result == {"proj-a", "proj-b"}
        query_text = str(mock_session.exec.call_args[0][0]).lower()
        assert "user_projects.project_name" in query_text
