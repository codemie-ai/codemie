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

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.service.project.project_assignment_service import ProjectAssignmentService, project_assignment_service


class TestProjectAssignmentServiceValidation:
    """Test suite for ProjectAssignmentService - UUID validation"""

    def test_validate_user_id_valid_uuid(self):
        """Test that _validate_user_id_format accepts valid UUID"""
        # Arrange
        valid_uuid = str(uuid4())

        # Act & Assert
        # Should not raise exception
        ProjectAssignmentService._validate_user_id_format(valid_uuid)

    def test_validate_user_id_invalid_format(self):
        """Test that _validate_user_id_format rejects invalid UUID format"""
        # Arrange
        invalid_uuid = "not-a-valid-uuid"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService._validate_user_id_format(invalid_uuid)

        assert exc_info.value.code == 400
        assert "Invalid user_id format" in exc_info.value.message
        assert "must be a valid UUID" in exc_info.value.details


class TestProjectAssignmentServiceSingleAssignment:
    """Test suite for ProjectAssignmentService - single user assignment"""

    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    @patch("codemie.service.project.project_assignment_service.logger")
    def test_assign_user_success(self, mock_logger, mock_user_project_repo, mock_user_repo):
        """Test successful user assignment to project"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id = str(uuid4())
        project_name = "team-project"
        requesting_user_id = str(uuid4())

        mock_user_repo.get_by_id.return_value = UserDB(id=user_id, email="user@example.com", username="user")
        mock_user_project_repo.get_by_user_and_project.return_value = None

        # Act
        result = ProjectAssignmentService.assign_user_to_project(
            session=mock_session,
            project=project,
            user_id=user_id,
            project_name=project_name,
            is_project_admin=True,
            actor=MagicMock(id=requesting_user_id, is_admin=False),
            action="POST /v1/projects/team-project/users/user-123",
        )

        # Assert
        assert result["message"] == "User assigned to project successfully"
        assert result["user_id"] == user_id
        assert result["project_name"] == project_name
        assert result["is_project_admin"] is True
        mock_user_project_repo.add_project.assert_called_once_with(
            session=mock_session, user_id=user_id, project_name=project_name, is_project_admin=True
        )
        mock_logger.info.assert_called_once()

    def test_assign_user_personal_project_rejected(self):
        """Test that assignment to personal project is rejected (FR-5.1: Hidden as 404)"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="user1-personal", project_type="personal")
        user_id = str(uuid4())
        requesting_user_id = str(uuid4())

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService.assign_user_to_project(
                session=mock_session,
                project=project,
                user_id=user_id,
                project_name="user1-personal",
                is_project_admin=False,
                actor=MagicMock(id=requesting_user_id, is_admin=False),
                action="POST /v1/projects/user1-personal/users/user-123",
            )

        assert exc_info.value.code == 404

    @patch("codemie.service.project.project_assignment_service.user_repository")
    def test_assign_user_not_found(self, mock_user_repo):
        """Test assignment fails when target user does not exist (FR-5.1: 404)"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id = str(uuid4())

        mock_user_repo.get_by_id.return_value = None

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService.assign_user_to_project(
                session=mock_session,
                project=project,
                user_id=user_id,
                project_name="team-project",
                is_project_admin=False,
                actor=MagicMock(id=str(uuid4()), is_admin=False),
                action="POST /v1/projects/team-project/users/user-123",
            )

        assert exc_info.value.code == 404
        assert "User not found" in exc_info.value.message
        assert user_id in exc_info.value.details

    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    def test_assign_user_already_assigned(self, mock_user_project_repo, mock_user_repo):
        """Test assignment fails when user is already assigned to project (FR-5.1: 409)"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id = str(uuid4())

        mock_user_repo.get_by_id.return_value = UserDB(id=user_id, email="user@example.com", username="user")
        mock_user_project_repo.get_by_user_and_project.return_value = UserProject(
            user_id=user_id, project_name="team-project", is_project_admin=False
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService.assign_user_to_project(
                session=mock_session,
                project=project,
                user_id=user_id,
                project_name="team-project",
                is_project_admin=False,
                actor=MagicMock(id=str(uuid4()), is_admin=False),
                action="POST /v1/projects/team-project/users/user-123",
            )

        assert exc_info.value.code == 409
        assert "already assigned" in exc_info.value.message
        assert "PUT endpoint" in exc_info.value.help


class TestProjectAssignmentServiceRoleUpdate:
    """Test suite for ProjectAssignmentService - role update"""

    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    @patch("codemie.service.project.project_assignment_service.logger")
    def test_update_role_success(self, mock_logger, mock_user_project_repo, mock_user_repo):
        """Test successful role update for project member"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id = str(uuid4())
        project_name = "team-project"
        requesting_user_id = str(uuid4())

        mock_user_repo.get_by_id.return_value = UserDB(id=user_id, email="user@example.com", username="user")
        existing_membership = UserProject(user_id=user_id, project_name=project_name, is_project_admin=False)
        mock_user_project_repo.get_by_user_and_project.return_value = existing_membership

        # Act
        result = ProjectAssignmentService.update_user_project_role(
            session=mock_session,
            project=project,
            user_id=user_id,
            project_name=project_name,
            is_project_admin=True,
            actor=MagicMock(id=requesting_user_id, is_admin=False),
            action="PUT /v1/projects/team-project/users/user-123",
        )

        # Assert
        assert result["message"] == "User role updated successfully"
        assert result["user_id"] == user_id
        assert result["project_name"] == project_name
        assert result["is_project_admin"] is True
        mock_user_project_repo.update_admin_status.assert_called_once_with(mock_session, user_id, project_name, True)
        mock_logger.info.assert_called_once()

    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    def test_update_role_not_assigned(self, mock_user_project_repo, mock_user_repo):
        """Test role update fails when user is not assigned to project (FR-5.1: 404)"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id = str(uuid4())

        mock_user_repo.get_by_id.return_value = UserDB(id=user_id, email="user@example.com", username="user")
        mock_user_project_repo.get_by_user_and_project.return_value = None

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService.update_user_project_role(
                session=mock_session,
                project=project,
                user_id=user_id,
                project_name="team-project",
                is_project_admin=True,
                actor=MagicMock(id=str(uuid4()), is_admin=False),
                action="PUT /v1/projects/team-project/users/user-123",
            )

        assert exc_info.value.code == 404
        assert "not assigned" in exc_info.value.message
        assert "POST endpoint" in exc_info.value.help


class TestProjectAssignmentServiceBulkAssign:
    """Test suite for ProjectAssignmentService - bulk assignment"""

    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    @patch("codemie.service.project.project_assignment_service.logger")
    def test_bulk_assign_success_new_users(self, mock_logger, mock_user_project_repo, mock_user_repo):
        """Test successful bulk assignment of new users (FR-5.2: all-or-nothing)"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id1 = str(uuid4())
        user_id2 = str(uuid4())
        users = [
            {"user_id": user_id1, "is_project_admin": True},
            {"user_id": user_id2, "is_project_admin": False},
        ]

        mock_user_repo.get_existing_user_ids.return_value = {user_id1, user_id2}
        mock_user_project_repo.get_by_users_and_project.return_value = {}

        # Act
        results = ProjectAssignmentService.bulk_assign_users_to_project(
            session=mock_session,
            project=project,
            users=users,
            project_name="team-project",
            actor=MagicMock(id=str(uuid4()), is_admin=False),
            action="POST /v1/projects/team-project/users/bulk",
        )

        # Assert
        assert len(results) == 2
        assert results[0]["action"] == "assigned"
        assert results[1]["action"] == "assigned"
        assert mock_session.add.call_count == 2
        mock_session.flush.assert_called_once()
        mock_logger.info.assert_called_once()

    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    @patch("codemie.service.project.project_assignment_service.logger")
    def test_bulk_assign_upsert_existing(self, mock_logger, mock_user_project_repo, mock_user_repo):
        """Test bulk assign with mix of new and existing users (upsert behavior)"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id1 = str(uuid4())
        user_id2 = str(uuid4())
        users = [
            {"user_id": user_id1, "is_project_admin": True},
            {"user_id": user_id2, "is_project_admin": False},
        ]

        mock_user_repo.get_existing_user_ids.return_value = {user_id1, user_id2}
        existing_membership = UserProject(user_id=user_id1, project_name="team-project", is_project_admin=False)
        mock_user_project_repo.get_by_users_and_project.return_value = {user_id1: existing_membership}

        # Act
        results = ProjectAssignmentService.bulk_assign_users_to_project(
            session=mock_session,
            project=project,
            users=users,
            project_name="team-project",
            actor=MagicMock(id=str(uuid4()), is_admin=False),
            action="POST /v1/projects/team-project/users/bulk",
        )

        # Assert
        assert len(results) == 2
        assert results[0]["action"] == "updated"  # user_id1 was existing
        assert results[1]["action"] == "assigned"  # user_id2 is new
        assert results[0]["is_project_admin"] is True  # Updated to True
        mock_session.flush.assert_called_once()
        mock_logger.info.assert_called_once()

    @patch("codemie.service.project.project_assignment_service.user_repository")
    def test_bulk_assign_duplicate_ids(self, mock_user_repo):
        """Test bulk assign fails with duplicate user_ids in request (FR-5.2: 400)"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        duplicate_id = str(uuid4())
        users = [
            {"user_id": duplicate_id, "is_project_admin": True},
            {"user_id": duplicate_id, "is_project_admin": False},
        ]

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService.bulk_assign_users_to_project(
                session=mock_session,
                project=project,
                users=users,
                project_name="team-project",
                actor=MagicMock(id=str(uuid4()), is_admin=False),
                action="POST /v1/projects/team-project/users/bulk",
            )

        assert exc_info.value.code == 400
        assert "Duplicate user IDs" in exc_info.value.message
        assert duplicate_id in exc_info.value.details

    @patch("codemie.service.project.project_assignment_service.user_repository")
    def test_bulk_assign_users_not_found(self, mock_user_repo):
        """Test bulk assign fails when one or more users don't exist (FR-5.2: 404)"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id1 = str(uuid4())
        missing_id = str(uuid4())
        users = [
            {"user_id": user_id1, "is_project_admin": True},
            {"user_id": missing_id, "is_project_admin": False},
        ]

        mock_user_repo.get_existing_user_ids.return_value = {user_id1}  # Only user_id1 exists

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService.bulk_assign_users_to_project(
                session=mock_session,
                project=project,
                users=users,
                project_name="team-project",
                actor=MagicMock(id=str(uuid4()), is_admin=False),
                action="POST /v1/projects/team-project/users/bulk",
            )

        assert exc_info.value.code == 404
        assert "not found" in exc_info.value.message
        assert missing_id in exc_info.value.details


class TestProjectAssignmentServiceBulkRemove:
    """Test suite for ProjectAssignmentService - bulk removal"""

    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    @patch("codemie.service.project.project_assignment_service.logger")
    def test_bulk_remove_success(self, mock_logger, mock_user_project_repo, mock_user_repo):
        """Test successful bulk removal of users from project (FR-5.3: all-or-nothing)"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id1 = str(uuid4())
        user_id2 = str(uuid4())
        user_ids = [user_id1, user_id2]

        mock_user_repo.get_existing_user_ids.return_value = {user_id1, user_id2}
        membership1 = UserProject(user_id=user_id1, project_name="team-project", is_project_admin=False)
        membership2 = UserProject(user_id=user_id2, project_name="team-project", is_project_admin=True)
        mock_user_project_repo.get_by_users_and_project.return_value = {user_id1: membership1, user_id2: membership2}

        # Act
        results = ProjectAssignmentService.bulk_remove_users_from_project(
            session=mock_session,
            project=project,
            user_ids=user_ids,
            project_name="team-project",
            actor=MagicMock(id=str(uuid4()), is_admin=False),
            action="DELETE /v1/projects/team-project/users/bulk",
        )

        # Assert
        assert len(results) == 2
        assert all(r["action"] == "removed" for r in results)
        assert mock_session.delete.call_count == 2
        mock_session.flush.assert_called_once()
        mock_logger.info.assert_called_once()

    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    def test_bulk_remove_not_assigned(self, mock_user_project_repo, mock_user_repo):
        """Test bulk remove fails when one or more users are not assigned (FR-5.3: 404)"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id1 = str(uuid4())
        not_assigned_id = str(uuid4())
        user_ids = [user_id1, not_assigned_id]

        mock_user_repo.get_existing_user_ids.return_value = {user_id1, not_assigned_id}
        membership1 = UserProject(user_id=user_id1, project_name="team-project", is_project_admin=False)
        mock_user_project_repo.get_by_users_and_project.return_value = {user_id1: membership1}  # Only user_id1 assigned

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService.bulk_remove_users_from_project(
                session=mock_session,
                project=project,
                user_ids=user_ids,
                project_name="team-project",
                actor=MagicMock(id=str(uuid4()), is_admin=False),
                action="DELETE /v1/projects/team-project/users/bulk",
            )

        assert exc_info.value.code == 404
        assert "not assigned" in exc_info.value.message
        assert not_assigned_id in exc_info.value.details


class TestProjectAssignmentServiceSingleRemoval:
    """Test suite for ProjectAssignmentService - single user removal"""

    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    @patch("codemie.service.project.project_assignment_service.logger")
    def test_remove_user_success(self, mock_logger, mock_user_project_repo, mock_user_repo):
        """Test successful removal of user from project"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id = str(uuid4())
        project_name = "team-project"
        requesting_user_id = str(uuid4())

        mock_user_repo.get_by_id.return_value = UserDB(id=user_id, email="user@example.com", username="user")
        mock_user_project_repo.remove_project.return_value = True

        # Act
        result = ProjectAssignmentService.remove_user_from_project(
            session=mock_session,
            project=project,
            user_id=user_id,
            project_name=project_name,
            actor=MagicMock(id=requesting_user_id, is_admin=False),
            action="DELETE /v1/projects/team-project/users/user-123",
        )

        # Assert
        assert result["message"] == "User removed from project successfully"
        assert result["user_id"] == user_id
        assert result["project_name"] == project_name
        mock_user_project_repo.remove_project.assert_called_once_with(mock_session, user_id, project_name)
        mock_logger.info.assert_called_once()

    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    def test_remove_user_not_assigned(self, mock_user_project_repo, mock_user_repo):
        """Test removal fails when user is not assigned to project (FR-5.1: 404)"""
        # Arrange
        mock_session = MagicMock()
        project = Application(id=str(uuid4()), name="team-project", project_type="team")
        user_id = str(uuid4())

        mock_user_repo.get_by_id.return_value = UserDB(id=user_id, email="user@example.com", username="user")
        mock_user_project_repo.remove_project.return_value = False  # User not assigned

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService.remove_user_from_project(
                session=mock_session,
                project=project,
                user_id=user_id,
                project_name="team-project",
                actor=MagicMock(id=str(uuid4()), is_admin=False),
                action="DELETE /v1/projects/team-project/users/user-123",
            )

        assert exc_info.value.code == 404
        assert "not assigned" in exc_info.value.message
        assert "Verify the user is assigned" in exc_info.value.help


class TestProjectAssignmentServiceSingleton:
    """Test the project_assignment_service singleton instance"""

    def test_singleton_instance_exists(self):
        """Test that project_assignment_service singleton is properly initialized"""
        # Assert
        assert project_assignment_service is not None
        assert isinstance(project_assignment_service, ProjectAssignmentService)
