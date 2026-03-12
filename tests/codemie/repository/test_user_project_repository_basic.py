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

"""Basic CRUD tests for user_project_repository to complete coverage.

Tests cover basic sync methods that weren't covered by the other test files:
- get_by_id
- get_by_user_and_project
- add_project
- remove_project
- update_admin_status
- get_admin_projects
- has_access
- is_admin

Target: >= 80% coverage for user_project_repository.py
"""

from unittest.mock import MagicMock

from codemie.repository.user_project_repository import user_project_repository
from codemie.rest_api.models.user_management import UserProject


class TestGetById:
    """Tests for get_by_id method"""

    def test_returns_project_by_id(self):
        """Returns UserProject record by ID"""
        # Arrange
        mock_session = MagicMock()
        project_id = "up-123"

        user_project = UserProject(id=project_id, user_id="user-1", project_name="project-1", is_project_admin=True)
        mock_session.exec.return_value.first.return_value = user_project

        # Act
        result = user_project_repository.get_by_id(mock_session, project_id)

        # Assert
        assert result is not None
        assert result.id == project_id
        assert result.user_id == "user-1"

    def test_returns_none_for_nonexistent_id(self):
        """Returns None when ID doesn't exist"""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        # Act
        result = user_project_repository.get_by_id(mock_session, "nonexistent-id")

        # Assert
        assert result is None


class TestGetByUserAndProject:
    """Tests for get_by_user_and_project method"""

    def test_returns_specific_assignment(self):
        """Returns UserProject for specific user and project"""
        # Arrange
        mock_session = MagicMock()
        user_id = "user-123"
        project_name = "project-1"

        user_project = UserProject(id="up1", user_id=user_id, project_name=project_name, is_project_admin=False)
        mock_session.exec.return_value.first.return_value = user_project

        # Act
        result = user_project_repository.get_by_user_and_project(mock_session, user_id, project_name)

        # Assert
        assert result is not None
        assert result.user_id == user_id
        assert result.project_name == project_name

    def test_returns_none_when_not_found(self):
        """Returns None when user doesn't have project access"""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        # Act
        result = user_project_repository.get_by_user_and_project(mock_session, "user-123", "nonexistent-project")

        # Assert
        assert result is None


class TestAddProject:
    """Tests for add_project method"""

    def test_creates_project_assignment(self):
        """Creates new project assignment with correct fields"""
        # Arrange
        mock_session = MagicMock()
        user_id = "user-123"
        project_name = "project-1"
        is_project_admin = True

        # Act
        result = user_project_repository.add_project(mock_session, user_id, project_name, is_project_admin)

        # Assert
        assert result.user_id == user_id
        assert result.project_name == project_name
        assert result.is_project_admin is True
        assert result.date is not None
        assert result.update_date is not None
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once()

    def test_defaults_admin_to_false(self):
        """Defaults is_project_admin to False when not specified"""
        # Arrange
        mock_session = MagicMock()

        # Act
        result = user_project_repository.add_project(mock_session, "user-123", "project-1")

        # Assert
        assert result.is_project_admin is False


class TestRemoveProject:
    """Tests for remove_project method"""

    def test_removes_project_assignment(self):
        """Removes project assignment and returns True"""
        # Arrange
        mock_session = MagicMock()
        user_id = "user-123"
        project_name = "project-1"

        user_project = UserProject(id="up1", user_id=user_id, project_name=project_name, is_project_admin=False)
        mock_session.exec.return_value.first.return_value = user_project

        # Act
        result = user_project_repository.remove_project(mock_session, user_id, project_name)

        # Assert
        assert result is True
        mock_session.delete.assert_called_once_with(user_project)
        mock_session.flush.assert_called_once()

    def test_returns_false_when_not_found(self):
        """Returns False when assignment doesn't exist"""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        # Act
        result = user_project_repository.remove_project(mock_session, "user-123", "nonexistent-project")

        # Assert
        assert result is False
        mock_session.delete.assert_not_called()


class TestUpdateAdminStatus:
    """Tests for update_admin_status method"""

    def test_updates_admin_status(self):
        """Updates admin status and update_date"""
        # Arrange
        mock_session = MagicMock()
        user_id = "user-123"
        project_name = "project-1"

        user_project = UserProject(id="up1", user_id=user_id, project_name=project_name, is_project_admin=False)
        mock_session.exec.return_value.first.return_value = user_project

        # Act
        result = user_project_repository.update_admin_status(mock_session, user_id, project_name, True)

        # Assert
        assert result is not None
        assert result.is_project_admin is True
        assert result.update_date is not None
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once()

    def test_returns_none_when_not_found(self):
        """Returns None when assignment doesn't exist"""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        # Act
        result = user_project_repository.update_admin_status(mock_session, "user-123", "nonexistent-project", True)

        # Assert
        assert result is None
        mock_session.add.assert_not_called()


class TestGetAdminProjects:
    """Tests for get_admin_projects method"""

    def test_returns_projects_where_user_is_admin(self):
        """Returns only projects where user has admin status"""
        # Arrange
        mock_session = MagicMock()
        user_id = "user-123"

        admin_projects = [
            UserProject(id="up1", user_id=user_id, project_name="project-1", is_project_admin=True),
            UserProject(id="up2", user_id=user_id, project_name="project-2", is_project_admin=True),
        ]
        mock_session.exec.return_value.all.return_value = admin_projects

        # Act
        result = user_project_repository.get_admin_projects(mock_session, user_id)

        # Assert
        assert len(result) == 2
        assert all(p.is_project_admin for p in result)

    def test_returns_empty_when_user_has_no_admin_projects(self):
        """Returns empty list when user is not admin of any project"""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []

        # Act
        result = user_project_repository.get_admin_projects(mock_session, "user-123")

        # Assert
        assert result == []


class TestHasAccess:
    """Tests for has_access method"""

    def test_returns_true_when_user_has_access(self):
        """Returns True when user has project access"""
        # Arrange
        mock_session = MagicMock()
        user_id = "user-123"
        project_name = "project-1"

        user_project = UserProject(id="up1", user_id=user_id, project_name=project_name, is_project_admin=False)
        mock_session.exec.return_value.first.return_value = user_project

        # Act
        result = user_project_repository.has_access(mock_session, user_id, project_name)

        # Assert
        assert result is True

    def test_returns_false_when_user_has_no_access(self):
        """Returns False when user doesn't have project access"""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        # Act
        result = user_project_repository.has_access(mock_session, "user-123", "project-1")

        # Assert
        assert result is False


class TestIsAdmin:
    """Tests for is_admin method"""

    def test_returns_true_when_user_is_admin(self):
        """Returns True when user is project admin"""
        # Arrange
        mock_session = MagicMock()
        user_id = "user-123"
        project_name = "project-1"

        user_project = UserProject(id="up1", user_id=user_id, project_name=project_name, is_project_admin=True)
        mock_session.exec.return_value.first.return_value = user_project

        # Act
        result = user_project_repository.is_admin(mock_session, user_id, project_name)

        # Assert
        assert result is True

    def test_returns_false_when_user_is_not_admin(self):
        """Returns False when user is not project admin"""
        # Arrange
        mock_session = MagicMock()
        user_id = "user-123"
        project_name = "project-1"

        user_project = UserProject(id="up1", user_id=user_id, project_name=project_name, is_project_admin=False)
        mock_session.exec.return_value.first.return_value = user_project

        # Act
        result = user_project_repository.is_admin(mock_session, user_id, project_name)

        # Assert
        assert result is False

    def test_returns_false_when_user_has_no_access(self):
        """Returns False when user doesn't have project access"""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        # Act
        result = user_project_repository.is_admin(mock_session, "user-123", "project-1")

        # Assert
        assert result is False
