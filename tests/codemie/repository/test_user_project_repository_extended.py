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

"""Extended tests for user_project_repository to improve coverage.

Tests cover:
- get_by_project_name
- delete_all_for_user
- get_by_users_and_project
- remove_projects_for_users
- Async variants (aget_by_user_id, aget_by_user_and_project, aget_by_project_name, aadd_project, aremove_project)

Target: >= 80% coverage for user_project_repository.py
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.user_project_repository import user_project_repository
from codemie.rest_api.models.user_management import UserProject


class TestGetByProjectName:
    """Tests for get_by_project_name method"""

    def test_returns_all_users_for_project(self):
        """Returns all users with access to a specific project"""
        # Arrange
        mock_session = MagicMock()
        project_name = "shared-project"

        user_projects = [
            UserProject(id="up1", user_id="user-1", project_name=project_name, is_project_admin=True),
            UserProject(id="up2", user_id="user-2", project_name=project_name, is_project_admin=False),
            UserProject(id="up3", user_id="user-3", project_name=project_name, is_project_admin=False),
        ]

        mock_session.exec.return_value.all.return_value = user_projects

        # Act
        result = user_project_repository.get_by_project_name(mock_session, project_name)

        # Assert
        assert len(result) == 3
        assert all(up.project_name == project_name for up in result)
        assert result[0].user_id == "user-1"
        assert result[0].is_project_admin is True
        assert result[1].is_project_admin is False

    def test_returns_empty_list_for_nonexistent_project(self):
        """Returns empty list when project has no users"""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []

        # Act
        result = user_project_repository.get_by_project_name(mock_session, "nonexistent-project")

        # Assert
        assert result == []


class TestDeleteAllForUser:
    """Tests for delete_all_for_user method"""

    def test_deletes_all_projects_for_user(self):
        """Deletes all project access records for a user"""
        # Arrange
        mock_session = MagicMock()
        user_id = "user-123"

        user_projects = [
            UserProject(id="up1", user_id=user_id, project_name="project-1", is_project_admin=True),
            UserProject(id="up2", user_id=user_id, project_name="project-2", is_project_admin=False),
            UserProject(id="up3", user_id=user_id, project_name="project-3", is_project_admin=False),
        ]

        mock_session.exec.return_value.all.return_value = user_projects

        # Act
        count = user_project_repository.delete_all_for_user(mock_session, user_id)

        # Assert
        assert count == 3
        assert mock_session.delete.call_count == 3
        mock_session.flush.assert_called_once()

    def test_returns_zero_when_user_has_no_projects(self):
        """Returns 0 when user has no project access"""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []

        # Act
        count = user_project_repository.delete_all_for_user(mock_session, "user-no-projects")

        # Assert
        assert count == 0
        mock_session.delete.assert_not_called()
        mock_session.flush.assert_called_once()


class TestGetByUsersAndProject:
    """Tests for get_by_users_and_project method"""

    def test_returns_existing_assignments_for_multiple_users(self):
        """Returns dict mapping user_id to UserProject for users with assignments"""
        # Arrange
        mock_session = MagicMock()
        user_ids = ["user-1", "user-2", "user-3"]
        project_name = "shared-project"

        existing_assignments = [
            UserProject(id="up1", user_id="user-1", project_name=project_name, is_project_admin=True),
            UserProject(id="up3", user_id="user-3", project_name=project_name, is_project_admin=False),
        ]

        mock_session.exec.return_value.all.return_value = existing_assignments

        # Act
        result = user_project_repository.get_by_users_and_project(mock_session, user_ids, project_name)

        # Assert
        assert len(result) == 2
        assert "user-1" in result
        assert "user-3" in result
        assert "user-2" not in result  # No assignment for user-2
        assert result["user-1"].is_project_admin is True
        assert result["user-3"].is_project_admin is False

    def test_returns_empty_dict_for_empty_user_list(self):
        """Returns empty dict when user_ids list is empty"""
        # Arrange
        mock_session = MagicMock()

        # Act
        result = user_project_repository.get_by_users_and_project(mock_session, [], "project")

        # Assert
        assert result == {}
        mock_session.exec.assert_not_called()

    def test_returns_empty_dict_when_no_assignments_found(self):
        """Returns empty dict when none of the users have assignments"""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []

        # Act
        result = user_project_repository.get_by_users_and_project(
            mock_session, ["user-1", "user-2"], "nonexistent-project"
        )

        # Assert
        assert result == {}


class TestRemoveProjectsForUsers:
    """Tests for remove_projects_for_users method"""

    def test_removes_project_access_for_multiple_users(self):
        """Removes project access for all specified users"""
        # Arrange
        mock_session = MagicMock()
        user_ids = ["user-1", "user-2", "user-3"]
        project_name = "shared-project"

        records_to_delete = [
            UserProject(id="up1", user_id="user-1", project_name=project_name, is_project_admin=True),
            UserProject(id="up2", user_id="user-2", project_name=project_name, is_project_admin=False),
            UserProject(id="up3", user_id="user-3", project_name=project_name, is_project_admin=False),
        ]

        mock_session.exec.return_value.all.return_value = records_to_delete

        # Act
        count = user_project_repository.remove_projects_for_users(mock_session, user_ids, project_name)

        # Assert
        assert count == 3
        assert mock_session.delete.call_count == 3
        mock_session.flush.assert_called_once()

    def test_returns_zero_for_empty_user_list(self):
        """Returns 0 when user_ids list is empty"""
        # Arrange
        mock_session = MagicMock()

        # Act
        count = user_project_repository.remove_projects_for_users(mock_session, [], "project")

        # Assert
        assert count == 0
        mock_session.exec.assert_not_called()
        mock_session.delete.assert_not_called()

    def test_handles_partial_assignments(self):
        """Handles case where only some users have assignments"""
        # Arrange
        mock_session = MagicMock()
        user_ids = ["user-1", "user-2", "user-3"]
        project_name = "shared-project"

        # Only user-1 and user-3 have assignments
        records_to_delete = [
            UserProject(id="up1", user_id="user-1", project_name=project_name, is_project_admin=True),
            UserProject(id="up3", user_id="user-3", project_name=project_name, is_project_admin=False),
        ]

        mock_session.exec.return_value.all.return_value = records_to_delete

        # Act
        count = user_project_repository.remove_projects_for_users(mock_session, user_ids, project_name)

        # Assert
        assert count == 2
        assert mock_session.delete.call_count == 2


# ===========================================
# Async Methods Tests
# ===========================================


class TestAsyncMethods:
    """Tests for async variants of repository methods"""

    @pytest.mark.asyncio
    async def test_aget_by_user_id_returns_projects(self):
        """aget_by_user_id returns all projects for user (async)"""
        # Arrange
        mock_session = AsyncMock()
        user_id = "user-123"

        user_projects = [
            UserProject(id="up1", user_id=user_id, project_name="project-1", is_project_admin=True),
            UserProject(id="up2", user_id=user_id, project_name="project-2", is_project_admin=False),
        ]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = user_projects
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Act
        result = await user_project_repository.aget_by_user_id(mock_session, user_id)

        # Assert
        assert len(result) == 2
        assert result[0].project_name == "project-1"
        assert result[1].project_name == "project-2"
        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aget_by_user_id_returns_empty_for_nonexistent_user(self):
        """aget_by_user_id returns empty list for user with no projects"""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Act
        result = await user_project_repository.aget_by_user_id(mock_session, "nonexistent-user")

        # Assert
        assert result == []

    @pytest.mark.asyncio
    async def test_aget_by_user_and_project_returns_assignment(self):
        """aget_by_user_and_project returns specific assignment (async)"""
        # Arrange
        mock_session = AsyncMock()
        user_id = "user-123"
        project_name = "project-1"

        user_project = UserProject(id="up1", user_id=user_id, project_name=project_name, is_project_admin=True)

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = user_project
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Act
        result = await user_project_repository.aget_by_user_and_project(mock_session, user_id, project_name)

        # Assert
        assert result is not None
        assert result.user_id == user_id
        assert result.project_name == project_name
        assert result.is_project_admin is True

    @pytest.mark.asyncio
    async def test_aget_by_user_and_project_returns_none_when_not_found(self):
        """aget_by_user_and_project returns None when assignment doesn't exist"""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Act
        result = await user_project_repository.aget_by_user_and_project(mock_session, "user-123", "nonexistent-project")

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_aget_by_project_name_returns_all_users(self):
        """aget_by_project_name returns all users for project (async)"""
        # Arrange
        mock_session = AsyncMock()
        project_name = "shared-project"

        user_projects = [
            UserProject(id="up1", user_id="user-1", project_name=project_name, is_project_admin=True),
            UserProject(id="up2", user_id="user-2", project_name=project_name, is_project_admin=False),
        ]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = user_projects
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Act
        result = await user_project_repository.aget_by_project_name(mock_session, project_name)

        # Assert
        assert len(result) == 2
        assert all(up.project_name == project_name for up in result)

    @pytest.mark.asyncio
    async def test_aget_by_project_name_returns_empty_for_nonexistent_project(self):
        """aget_by_project_name returns empty list for nonexistent project"""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Act
        result = await user_project_repository.aget_by_project_name(mock_session, "nonexistent-project")

        # Assert
        assert result == []

    @pytest.mark.asyncio
    async def test_aadd_project_creates_assignment(self):
        """aadd_project creates new project assignment (async)"""
        # Arrange
        mock_session = AsyncMock()
        user_id = "user-123"
        project_name = "project-1"
        is_project_admin = True

        # Mock flush and refresh
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        # Act
        result = await user_project_repository.aadd_project(mock_session, user_id, project_name, is_project_admin)

        # Assert
        assert result.user_id == user_id
        assert result.project_name == project_name
        assert result.is_project_admin is True
        assert result.date is not None
        assert result.update_date is not None
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_aadd_project_defaults_admin_to_false(self):
        """aadd_project defaults is_project_admin to False"""
        # Arrange
        mock_session = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        # Act
        result = await user_project_repository.aadd_project(mock_session, "user-123", "project-1")

        # Assert
        assert result.is_project_admin is False

    @pytest.mark.asyncio
    async def test_aremove_project_removes_assignment(self):
        """aremove_project removes project assignment (async)"""
        # Arrange
        mock_session = AsyncMock()
        user_id = "user-123"
        project_name = "project-1"

        user_project = UserProject(id="up1", user_id=user_id, project_name=project_name, is_project_admin=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = user_project
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.delete = AsyncMock()
        mock_session.flush = AsyncMock()

        # Act
        result = await user_project_repository.aremove_project(mock_session, user_id, project_name)

        # Assert
        assert result is True
        mock_session.delete.assert_awaited_once_with(user_project)
        mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aremove_project_returns_false_when_not_found(self):
        """aremove_project returns False when assignment doesn't exist"""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.delete = AsyncMock()

        # Act
        result = await user_project_repository.aremove_project(mock_session, "user-123", "nonexistent-project")

        # Assert
        assert result is False
        mock_session.delete.assert_not_awaited()
