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

"""Tests for bulk visibility filtering optimization (Story 11).

Story 11: Shared project visibility is membership-based.

Tests cover:
- Bulk filtering of pre-fetched projects_map
- Visibility rules applied to multiple users in single bulk operation
- Single project type query for all projects across all users
"""

from unittest.mock import MagicMock, patch

from codemie.repository.user_project_repository import user_project_repository
from codemie.rest_api.models.user_management import UserProject


class TestBulkVisibilityFiltering:
    """Test filter_visible_projects_from_map bulk filtering"""

    @patch("codemie.repository.application_repository.application_repository")
    def test_bulk_filter_applies_membership_visibility_rules(self, mock_app_repo):
        """Test: Bulk filtering applies visibility rules across multiple users"""
        # Arrange
        mock_session = MagicMock()
        alice_id = "alice-123"
        bob_id = "bob-456"
        requester_id = "requester-789"

        # Alice has 1 personal + 1 shared
        alice_projects = [
            UserProject(user_id=alice_id, project_name="alice@example.com", is_project_admin=False),
            UserProject(user_id=alice_id, project_name="shared-proj", is_project_admin=True),
        ]

        # Bob has 2 shared
        bob_projects = [
            UserProject(user_id=bob_id, project_name="bob-proj-1", is_project_admin=True),
            UserProject(user_id=bob_id, project_name="bob-proj-2", is_project_admin=False),
        ]

        projects_map = {alice_id: alice_projects, bob_id: bob_projects}

        # Mock single bulk lookup for all projects
        mock_app_repo.get_project_types_bulk.return_value = {
            "alice@example.com": ("personal", alice_id),
            "shared-proj": ("shared", alice_id),
            "bob-proj-1": ("shared", bob_id),
            "bob-proj-2": ("shared", bob_id),
        }

        with patch.object(user_project_repository, "get_project_names_for_user", return_value={"shared-proj"}):
            # Act: Non-super-admin requesting visibility filtering
            filtered = user_project_repository.filter_visible_projects_from_map(
                mock_session, projects_map, requesting_user_id=requester_id, is_super_admin=False
            )

        # Assert: only requester memberships are visible
        assert [p.project_name for p in filtered[alice_id]] == ["shared-proj"]
        assert filtered[bob_id] == []

        # Verify single bulk query was made (not per-user)
        mock_app_repo.get_project_types_bulk.assert_called_once()
        call_args = mock_app_repo.get_project_types_bulk.call_args[0][1]
        assert set(call_args) == {"alice@example.com", "shared-proj", "bob-proj-1", "bob-proj-2"}

    @patch("codemie.repository.application_repository.application_repository")
    def test_bulk_filter_super_admin_sees_all_projects(self, mock_app_repo):
        """Test: Super admin sees all projects including personal ones"""
        # Arrange
        mock_session = MagicMock()
        alice_id = "alice-123"
        admin_id = "admin-789"

        alice_projects = [
            UserProject(user_id=alice_id, project_name="alice@example.com", is_project_admin=False),
            UserProject(user_id=alice_id, project_name="shared-proj", is_project_admin=True),
        ]

        projects_map = {alice_id: alice_projects}

        mock_app_repo.get_project_types_bulk.return_value = {
            "alice@example.com": ("personal", alice_id),
            "shared-proj": ("shared", alice_id),
        }

        # Act: Super admin requesting
        filtered = user_project_repository.filter_visible_projects_from_map(
            mock_session, projects_map, requesting_user_id=admin_id, is_super_admin=True
        )

        # Assert: Super admin sees both projects
        assert len(filtered[alice_id]) == 2
        assert filtered[alice_id][0].project_name == "alice@example.com"
        assert filtered[alice_id][1].project_name == "shared-proj"

    @patch("codemie.repository.application_repository.application_repository")
    def test_bulk_filter_user_sees_own_personal_projects(self, mock_app_repo):
        """Test: User sees their own personal projects"""
        # Arrange
        mock_session = MagicMock()
        alice_id = "alice-123"

        alice_projects = [
            UserProject(user_id=alice_id, project_name="alice@example.com", is_project_admin=False),
            UserProject(user_id=alice_id, project_name="shared-proj", is_project_admin=True),
        ]

        projects_map = {alice_id: alice_projects}

        mock_app_repo.get_project_types_bulk.return_value = {
            "alice@example.com": ("personal", alice_id),
            "shared-proj": ("shared", alice_id),
        }

        # Act: Alice requesting her own projects
        filtered = user_project_repository.filter_visible_projects_from_map(
            mock_session, projects_map, requesting_user_id=alice_id, is_super_admin=False
        )

        # Assert: Alice sees both projects
        assert len(filtered[alice_id]) == 2
        assert filtered[alice_id][0].project_name == "alice@example.com"
        assert filtered[alice_id][1].project_name == "shared-proj"

    @patch("codemie.repository.application_repository.application_repository")
    def test_bulk_filter_uses_requester_lookup_when_requester_not_in_map(self, mock_app_repo):
        """Test: requester memberships are loaded once when requester not in current page"""
        # Arrange
        mock_session = MagicMock()
        requester_id = "requester-1"
        target_id = "target-1"
        projects_map = {
            target_id: [
                UserProject(user_id=target_id, project_name="shared-1", is_project_admin=False),
                UserProject(user_id=target_id, project_name="shared-2", is_project_admin=False),
            ],
        }

        mock_app_repo.get_project_types_bulk.return_value = {
            "shared-1": ("shared", target_id),
            "shared-2": ("shared", target_id),
        }

        with patch.object(user_project_repository, "get_project_names_for_user", return_value={"shared-2"}) as mock_get:
            # Act
            filtered = user_project_repository.filter_visible_projects_from_map(
                mock_session, projects_map, requesting_user_id=requester_id, is_super_admin=False
            )

        # Assert
        mock_get.assert_called_once_with(mock_session, requester_id)
        assert [p.project_name for p in filtered[target_id]] == ["shared-2"]

    @patch("codemie.repository.application_repository.application_repository")
    def test_bulk_filter_reuses_requester_projects_from_map(self, mock_app_repo):
        """Test: skip requester lookup when requester is part of paginated map"""
        # Arrange
        mock_session = MagicMock()
        requester_id = "requester-1"
        target_id = "target-1"
        projects_map = {
            requester_id: [UserProject(user_id=requester_id, project_name="shared-1", is_project_admin=True)],
            target_id: [
                UserProject(user_id=target_id, project_name="shared-1", is_project_admin=False),
                UserProject(user_id=target_id, project_name="shared-2", is_project_admin=False),
            ],
        }

        mock_app_repo.get_project_types_bulk.return_value = {
            "shared-1": ("shared", requester_id),
            "shared-2": ("shared", target_id),
        }

        with patch.object(user_project_repository, "get_project_names_for_user") as mock_get:
            # Act
            filtered = user_project_repository.filter_visible_projects_from_map(
                mock_session, projects_map, requesting_user_id=requester_id, is_super_admin=False
            )

        # Assert
        mock_get.assert_not_called()
        assert [p.project_name for p in filtered[target_id]] == ["shared-1"]

    @patch("codemie.repository.application_repository.application_repository")
    def test_bulk_filter_handles_empty_projects_map(self, mock_app_repo):
        """Test: Handles empty projects_map gracefully"""
        # Arrange
        mock_session = MagicMock()

        # Act
        filtered = user_project_repository.filter_visible_projects_from_map(
            mock_session, {}, requesting_user_id="user-123", is_super_admin=False
        )

        # Assert
        assert filtered == {}
        mock_app_repo.get_project_types_bulk.assert_not_called()

    @patch("codemie.repository.application_repository.application_repository")
    def test_bulk_filter_single_query_for_all_projects(self, mock_app_repo):
        """Test: Only one query made for all projects across all users (N+1 prevention)"""
        # Arrange
        mock_session = MagicMock()
        user1_id = "user-1"
        user2_id = "user-2"
        user3_id = "user-3"

        # 3 users with multiple projects each
        projects_map = {
            user1_id: [
                UserProject(user_id=user1_id, project_name="proj-1", is_project_admin=True),
                UserProject(user_id=user1_id, project_name="proj-2", is_project_admin=False),
            ],
            user2_id: [
                UserProject(user_id=user2_id, project_name="proj-3", is_project_admin=True),
                UserProject(user_id=user2_id, project_name="proj-4", is_project_admin=False),
            ],
            user3_id: [UserProject(user_id=user3_id, project_name="proj-5", is_project_admin=True)],
        }

        # Mock bulk lookup returns all as shared
        mock_app_repo.get_project_types_bulk.return_value = {
            "proj-1": ("shared", user1_id),
            "proj-2": ("shared", user1_id),
            "proj-3": ("shared", user2_id),
            "proj-4": ("shared", user2_id),
            "proj-5": ("shared", user3_id),
        }

        # Act
        user_project_repository.filter_visible_projects_from_map(
            mock_session, projects_map, requesting_user_id="requester", is_super_admin=False
        )

        # Assert: Only ONE bulk query made (not 3 separate queries for 3 users)
        assert mock_app_repo.get_project_types_bulk.call_count == 1
        call_args = mock_app_repo.get_project_types_bulk.call_args[0][1]
        assert len(call_args) == 5  # All 5 projects queried in single call
