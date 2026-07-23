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

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from decimal import Decimal
from unittest.mock import AsyncMock


from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.service.budget.budget_enums import AllocationMode, BudgetCategory, SyncStatus
from codemie.service.budget.budget_models import Budget, ProjectBudgetAssignment, ProjectMemberBudgetAssignment
from codemie.service.budget.provider import MemberBudgetSpendSnapshot
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

    @patch("codemie.service.project.project_assignment_service.config.ENV", "production")
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


class TestRejectIfCreator:
    """Test suite for ProjectAssignmentService._reject_if_creator"""

    @patch("codemie.service.project.project_assignment_service.user_repository")
    def test_raises_400_when_creator_is_sole_target(self, mock_user_repo):
        """Test raises 400 when the only target user is the project creator"""
        creator_id = str(uuid4())
        creator_username = "project-creator"
        project = Application(id=str(uuid4()), name="team-project", project_type="team", created_by=creator_id)
        mock_user_repo.get_by_id.return_value = UserDB(
            id=creator_id, email="creator@example.com", username=creator_username
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService._reject_if_creator(MagicMock(), project, [creator_id], "team-project")

        assert exc_info.value.code == 400
        assert "Cannot remove the project creator" in exc_info.value.message
        assert creator_username in exc_info.value.details
        assert "must always remain a member" in exc_info.value.help

    @patch("codemie.service.project.project_assignment_service.user_repository")
    def test_raises_400_when_creator_among_multiple_ids(self, mock_user_repo):
        """Test raises 400 when creator is one of multiple user_ids"""
        creator_id = str(uuid4())
        other_id = str(uuid4())
        creator_username = "project-creator"
        project = Application(id=str(uuid4()), name="team-project", project_type="team", created_by=creator_id)
        mock_user_repo.get_by_id.return_value = UserDB(
            id=creator_id, email="creator@example.com", username=creator_username
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService._reject_if_creator(MagicMock(), project, [other_id, creator_id], "team-project")

        assert exc_info.value.code == 400
        assert creator_username in exc_info.value.details

    def test_passes_when_creator_not_in_list(self):
        """Test no exception raised when creator is not among the target user_ids"""
        creator_id = str(uuid4())
        project = Application(id=str(uuid4()), name="team-project", project_type="team", created_by=creator_id)

        # Should not raise — get_by_id is never called when creator is not in the list
        ProjectAssignmentService._reject_if_creator(MagicMock(), project, [str(uuid4()), str(uuid4())], "team-project")


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
                actor=MagicMock(
                    id=requesting_user_id,
                    is_admin=False,
                    is_maintainer=False,
                    is_admin_or_maintainer=False,
                ),
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

    @patch("codemie.service.project.project_assignment_service.activity_event_repository")
    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    @patch("codemie.service.project.project_assignment_service.logger")
    def test_bulk_assign_success_new_users(
        self, mock_logger, mock_user_project_repo, mock_user_repo, mock_activity_repo
    ):
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

    @patch("codemie.service.project.project_assignment_service.activity_event_repository")
    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    @patch("codemie.service.project.project_assignment_service.logger")
    def test_bulk_assign_upsert_existing(self, mock_logger, mock_user_project_repo, mock_user_repo, mock_activity_repo):
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

    @patch("codemie.service.project.project_assignment_service.activity_event_repository")
    @patch("codemie.service.project.project_assignment_service.user_repository")
    @patch("codemie.service.project.project_assignment_service.user_project_repository")
    @patch("codemie.service.project.project_assignment_service.logger")
    def test_bulk_remove_success(self, mock_logger, mock_user_project_repo, mock_user_repo, mock_activity_repo):
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
    def test_bulk_remove_creator_raises_400(self, mock_user_repo):
        """Test that including the project creator in bulk removal raises 400"""
        creator_id = str(uuid4())
        other_id = str(uuid4())
        creator_username = "project-creator"
        project = Application(id=str(uuid4()), name="team-project", project_type="team", created_by=creator_id)
        mock_user_repo.get_by_id.return_value = UserDB(
            id=creator_id, email="creator@example.com", username=creator_username
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService.bulk_remove_users_from_project(
                session=MagicMock(),
                project=project,
                user_ids=[other_id, creator_id],
                project_name="team-project",
                actor=MagicMock(id=str(uuid4()), is_admin=False),
                action="DELETE /v1/projects/team-project/users/bulk",
            )

        assert exc_info.value.code == 400
        assert "Cannot remove the project creator" in exc_info.value.message
        assert creator_username in exc_info.value.details

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
    def test_remove_creator_raises_400(self, mock_user_repo):
        """Test that removing the project creator raises 400"""
        creator_id = str(uuid4())
        creator_username = "project-creator"
        project = Application(id=str(uuid4()), name="team-project", project_type="team", created_by=creator_id)
        mock_user_repo.get_by_id.return_value = UserDB(
            id=creator_id, email="creator@example.com", username=creator_username
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectAssignmentService.remove_user_from_project(
                session=MagicMock(),
                project=project,
                user_id=creator_id,
                project_name="team-project",
                actor=MagicMock(id=str(uuid4()), is_admin=False),
                action="DELETE /v1/projects/team-project/users/creator-id",
            )

        assert exc_info.value.code == 400
        assert "Cannot remove the project creator" in exc_info.value.message
        assert creator_username in exc_info.value.details

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


class TestGetMemberAddedAllocationAmounts:
    """Unit tests for _get_member_added_allocation_amounts helper."""

    def _make_budget(self, soft: float = 100.0, max_b: float = 200.0):
        return Budget(
            budget_id="bgt-1",
            budget_type="project",
            name="test-budget",
            soft_budget=soft,
            max_budget=max_b,
            budget_duration="30d",
            budget_category="platform",
            created_by="admin",
        )

    def _make_member_alloc(self, soft: float, max_b: float, mode: str = "equal", deleted: bool = False):
        alloc = ProjectMemberBudgetAssignment(
            project_name="proj",
            budget_category="platform",
            project_budget_id="bgt-1",
            user_id="other-user",
            allocation_mode=mode,
            allocated_soft_budget=soft,
            allocated_max_budget=max_b,
            assigned_by="admin",
        )
        if deleted:
            alloc.deleted_at = datetime.now(timezone.utc)
        return alloc

    def test_copies_from_equal_allocation(self):
        """Returns soft/max from first active equal-mode allocation."""
        session = MagicMock()
        budget = self._make_budget(soft=100.0, max_b=200.0)
        equal_alloc = self._make_member_alloc(soft=50.0, max_b=100.0, mode="equal")
        session.exec.return_value.first.return_value = equal_alloc

        soft, max_b = ProjectAssignmentService._get_member_added_allocation_amounts(
            session, "proj", "platform", "bgt-1", budget
        )

        assert soft == 50.0
        assert max_b == 100.0

    def test_falls_back_to_budget_when_no_equal_allocation(self):
        """Returns budget.soft_budget / budget.max_budget when no equal allocation exists."""
        session = MagicMock()
        budget = self._make_budget(soft=100.0, max_b=200.0)
        session.exec.return_value.first.return_value = None

        soft, max_b = ProjectAssignmentService._get_member_added_allocation_amounts(
            session, "proj", "platform", "bgt-1", budget
        )

        assert soft == 100.0
        assert max_b == 200.0

    def test_query_filters_equal_mode_only(self):
        """The SQL query must filter allocation_mode == 'equal' so fixed overrides are ignored."""
        session = MagicMock()
        budget = self._make_budget()
        session.exec.return_value.first.return_value = None

        ProjectAssignmentService._get_member_added_allocation_amounts(session, "proj", "platform", "bgt-1", budget)

        session.exec.assert_called_once()
        stmt = session.exec.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "equal" in compiled

    def test_query_filters_by_project_category_budget_id(self):
        """Verify the query selects on project_name, budget_category, project_budget_id, allocation_mode, and deleted_at."""
        session = MagicMock()
        budget = self._make_budget()
        session.exec.return_value.first.return_value = None

        ProjectAssignmentService._get_member_added_allocation_amounts(session, "my-proj", "cli", "bgt-99", budget)

        session.exec.assert_called_once()
        stmt = session.exec.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        for fragment in (
            "project_member_budget_assignments.project_name = 'my-proj'",
            "project_member_budget_assignments.budget_category = 'cli'",
            "project_member_budget_assignments.project_budget_id = 'bgt-99'",
            "project_member_budget_assignments.allocation_mode = 'equal'",
            "project_member_budget_assignments.pmba_deleted_at IS NULL",
        ):
            assert fragment in compiled


class TestSyncProjectBudgetMemberAdded:
    """Tests for _sync_project_budget_member_added — allocation amounts behavior."""

    def _make_assignment(self, budget_id: str = "bgt-1", category: str = "platform") -> ProjectBudgetAssignment:
        return ProjectBudgetAssignment(
            id="pba-1",
            project_name="proj",
            budget_category=category,
            budget_id=budget_id,
            allocation_mode="equal",
            assigned_by="admin",
        )

    def _make_budget(self, soft: float = 100.0, max_b: float = 200.0, budget_id: str = "bgt-1") -> Budget:
        return Budget(
            budget_id=budget_id,
            budget_type="project",
            name="test-budget",
            soft_budget=soft,
            max_budget=max_b,
            budget_duration="30d",
            budget_category="platform",
            created_by="admin",
        )

    def test_new_member_copies_from_equal_allocation(self):
        """New member gets soft/max copied from existing equal allocation, not zero."""
        session = MagicMock()
        assignment = self._make_assignment()
        budget = self._make_budget(soft=100.0, max_b=200.0)

        # exec() is called three times:
        # 1. ProjectBudgetAssignment list (active project budget assignments)
        # 2. existing member allocation check -> no duplicate
        # 3. _get_member_added_allocation_amounts: equal alloc found
        exec_results = [
            MagicMock(all=MagicMock(return_value=[assignment])),
            MagicMock(first=MagicMock(return_value=None)),
            MagicMock(
                first=MagicMock(
                    return_value=MagicMock(
                        allocated_soft_budget=50.0,
                        allocated_max_budget=90.0,
                    )
                )
            ),
        ]
        session.exec.side_effect = exec_results
        session.get.return_value = budget

        ProjectAssignmentService._sync_project_budget_member_added(session, "proj", "new-user", "actor")

        added_calls = list(session.add.call_args_list)
        assert len(added_calls) >= 1
        alloc = added_calls[0][0][0]
        assert isinstance(alloc, ProjectMemberBudgetAssignment)
        assert alloc.allocated_soft_budget == 50.0
        assert alloc.allocated_max_budget == 90.0
        assert alloc.allocation_mode == AllocationMode.EQUAL.value
        assert alloc.sync_status == SyncStatus.PENDING
        assert alloc.provider_metadata is None

    def test_new_member_falls_back_to_budget_amounts(self):
        """New member gets budget.soft_budget / budget.max_budget when no equal alloc exists."""
        session = MagicMock()
        assignment = self._make_assignment()
        budget = self._make_budget(soft=100.0, max_b=200.0)

        exec_results = [
            MagicMock(all=MagicMock(return_value=[assignment])),
            MagicMock(first=MagicMock(return_value=None)),
            MagicMock(first=MagicMock(return_value=None)),
        ]
        session.exec.side_effect = exec_results
        session.get.return_value = budget

        ProjectAssignmentService._sync_project_budget_member_added(session, "proj", "new-user", "actor")

        added_calls = session.add.call_args_list
        alloc = added_calls[0][0][0]
        assert isinstance(alloc, ProjectMemberBudgetAssignment)
        assert alloc.allocated_soft_budget == 100.0
        assert alloc.allocated_max_budget == 200.0
        assert alloc.allocation_mode == AllocationMode.EQUAL.value
        assert alloc.sync_status == SyncStatus.PENDING
        assert alloc.provider_metadata is None

    @patch("codemie.service.project.project_assignment_service.get_active_provider")
    def test_member_add_creates_pending_allocation_without_provider_sync(self, mock_get_provider):
        """Allocation stays pending and no provider sync runs when adding a member."""
        session = MagicMock()
        assignment = self._make_assignment()
        budget = self._make_budget(soft=100.0, max_b=200.0)

        session.exec.side_effect = [
            MagicMock(all=MagicMock(return_value=[assignment])),
            MagicMock(first=MagicMock(return_value=None)),
            MagicMock(first=MagicMock(return_value=None)),
        ]
        session.get.return_value = budget

        ProjectAssignmentService._sync_project_budget_member_added(session, "proj", "new-user", "actor")

        allocation = next(
            call[0][0] for call in session.add.call_args_list if isinstance(call[0][0], ProjectMemberBudgetAssignment)
        )
        assert allocation.sync_status == SyncStatus.PENDING
        assert allocation.provider_metadata is None
        mock_get_provider.assert_not_called()

    def test_skips_creation_when_member_already_has_allocation(self):
        """If user already has an active allocation for the budget, skip creation."""
        session = MagicMock()
        assignment = self._make_assignment()
        budget = self._make_budget()
        existing_alloc = ProjectMemberBudgetAssignment(
            project_name="proj",
            budget_category="platform",
            project_budget_id="bgt-1",
            user_id="existing-user",
            allocation_mode="equal",
            allocated_soft_budget=50.0,
            allocated_max_budget=100.0,
            assigned_by="admin",
        )

        exec_results = [
            MagicMock(all=MagicMock(return_value=[assignment])),
            MagicMock(first=MagicMock(return_value=existing_alloc)),
        ]
        session.exec.side_effect = exec_results
        session.get.return_value = budget

        ProjectAssignmentService._sync_project_budget_member_added(session, "proj", "existing-user", "actor")

        session.add.assert_not_called()


class TestProjectAssignmentServiceSingleton:
    """Test the project_assignment_service singleton instance"""

    def test_singleton_instance_exists(self):
        """Test that project_assignment_service singleton is properly initialized"""
        # Assert
        assert project_assignment_service is not None
        assert isinstance(project_assignment_service, ProjectAssignmentService)


class TestProjectSpendTrackingRepositorySingleton:
    def test_singleton_exists_and_is_correct_type(self):
        from codemie.repository.project_spend_tracking_repository import project_spend_tracking_repository

        assert project_spend_tracking_repository is not None
        assert isinstance(project_spend_tracking_repository, ProjectSpendTrackingRepository)


class TestCaptureTerminalMemberSpend:
    """Unit tests for ProjectAssignmentService._capture_terminal_member_spend."""

    def _make_allocation(self, provider_member_ref=None):
        raw = {}
        if provider_member_ref:
            raw["provider_member_ref"] = provider_member_ref
        return ProjectMemberBudgetAssignment(
            project_name="myproject",
            budget_category="platform",
            project_budget_id="bgt-1",
            user_id="user-abc",
            allocation_mode="equal",
            allocated_soft_budget=50.0,
            allocated_max_budget=100.0,
            assigned_by="admin",
            budget_id="bgt-1",
            provider_metadata={"raw": raw} if raw else {},
        )

    def _make_snapshot(self, spend="5.00"):
        return MemberBudgetSpendSnapshot(
            project_name="myproject",
            budget_category=BudgetCategory.PLATFORM,
            budget_id="bgt-1",
            user_id="user-abc",
            spend=Decimal(spend),
            provider_subject_id="subj-1",
        )

    @pytest.mark.asyncio
    @patch("codemie.service.project.project_assignment_service.project_spend_tracking_repository")
    @patch("codemie.service.project.project_assignment_service.get_async_session")
    async def test_happy_path_bootstrap_no_prev_row(self, mock_get_session, mock_repo):
        """Bootstrap case: no previous row — daily_spend equals full snapshot spend."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        allocation = self._make_allocation(provider_member_ref="ref-1")
        snapshot = self._make_snapshot("5.00")

        mock_provider = AsyncMock()
        mock_provider.collect_member_budget_spend_for_refs.return_value = [snapshot]

        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_repo.get_latest_before_by_member_budget_ids = AsyncMock(return_value={})
        mock_repo.insert_member_budget_entries = AsyncMock()

        await ProjectAssignmentService._capture_terminal_member_spend(allocation, mock_provider, now)

        mock_provider.collect_member_budget_spend_for_refs.assert_awaited_once_with({"ref-1"})
        mock_repo.insert_member_budget_entries.assert_awaited_once()
        inserted_rows = mock_repo.insert_member_budget_entries.call_args[0][1]
        assert len(inserted_rows) == 1
        row = inserted_rows[0]
        assert row.daily_spend == Decimal("5.0000000000")
        assert row.cumulative_spend == Decimal("5.0000000000")
        assert row.budget_period_spend == Decimal("5.0000000000")
        assert row.project_name == "myproject"
        assert row.user_id == "user-abc"
        assert row.budget_id == "bgt-1"
        assert row.budget_category == "platform"
        assert row.spend_date == now

    @pytest.mark.asyncio
    @patch("codemie.service.project.project_assignment_service.project_spend_tracking_repository")
    @patch("codemie.service.project.project_assignment_service.get_async_session")
    async def test_happy_path_with_prev_row_computes_delta(self, mock_get_session, mock_repo):
        """Delta case: previous row exists — daily_spend is the difference."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        allocation = self._make_allocation(provider_member_ref="ref-1")
        snapshot = self._make_snapshot("9.00")

        prev_row = MagicMock()
        prev_row.budget_period_spend = Decimal("6.00")
        prev_row.cumulative_spend = Decimal("20.00")

        mock_provider = AsyncMock()
        mock_provider.collect_member_budget_spend_for_refs.return_value = [snapshot]

        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_repo.get_latest_before_by_member_budget_ids = AsyncMock(
            return_value={("myproject", "bgt-1", "user-abc"): prev_row}
        )
        mock_repo.insert_member_budget_entries = AsyncMock()

        await ProjectAssignmentService._capture_terminal_member_spend(allocation, mock_provider, now)

        inserted_rows = mock_repo.insert_member_budget_entries.call_args[0][1]
        row = inserted_rows[0]
        assert row.daily_spend == Decimal("3.0000000000")  # 9 - 6
        assert row.cumulative_spend == Decimal("23.0000000000")  # 20 + 3
        assert row.budget_period_spend == Decimal("9.0000000000")

    @pytest.mark.asyncio
    async def test_no_provider_member_ref_skips_silently(self):
        """No provider_member_ref in metadata — collection skipped, no error."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        allocation = self._make_allocation(provider_member_ref=None)
        mock_provider = AsyncMock()

        await ProjectAssignmentService._capture_terminal_member_spend(allocation, mock_provider, now)

        mock_provider.collect_member_budget_spend_for_refs.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("codemie.service.project.project_assignment_service.project_spend_tracking_repository")
    @patch("codemie.service.project.project_assignment_service.get_async_session")
    async def test_zero_delta_skips_insert(self, mock_get_session, mock_repo):
        """Delta is zero — row insert is skipped."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        allocation = self._make_allocation(provider_member_ref="ref-1")
        snapshot = self._make_snapshot("6.00")

        prev_row = MagicMock()
        prev_row.budget_period_spend = Decimal("6.00")
        prev_row.cumulative_spend = Decimal("20.00")

        mock_provider = AsyncMock()
        mock_provider.collect_member_budget_spend_for_refs.return_value = [snapshot]

        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_repo.get_latest_before_by_member_budget_ids = AsyncMock(
            return_value={("myproject", "bgt-1", "user-abc"): prev_row}
        )
        mock_repo.insert_member_budget_entries = AsyncMock()

        await ProjectAssignmentService._capture_terminal_member_spend(allocation, mock_provider, now)

        mock_repo.insert_member_budget_entries.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_provider_snapshots_skips(self):
        """Provider returns no snapshots — insert skipped."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        allocation = self._make_allocation(provider_member_ref="ref-1")
        mock_provider = AsyncMock()
        mock_provider.collect_member_budget_spend_for_refs.return_value = []

        await ProjectAssignmentService._capture_terminal_member_spend(allocation, mock_provider, now)

        mock_provider.collect_member_budget_spend_for_refs.assert_awaited_once_with({"ref-1"})

    @pytest.mark.asyncio
    @patch("codemie.service.project.project_assignment_service.project_spend_tracking_repository")
    @patch("codemie.service.project.project_assignment_service.get_async_session")
    async def test_provider_subject_id_falls_back_to_ref_when_snapshot_has_none(self, mock_get_session, mock_repo):
        """Snapshot with provider_subject_id=None — row falls back to provider_member_ref."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        allocation = self._make_allocation(provider_member_ref="ref-fallback")
        snapshot = MemberBudgetSpendSnapshot(
            project_name="myproject",
            budget_category=BudgetCategory.PLATFORM,
            budget_id="bgt-1",
            user_id="user-abc",
            spend=Decimal("7.00"),
            provider_subject_id=None,
        )

        mock_provider = AsyncMock()
        mock_provider.collect_member_budget_spend_for_refs.return_value = [snapshot]

        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_repo.get_latest_before_by_member_budget_ids = AsyncMock(return_value={})
        mock_repo.insert_member_budget_entries = AsyncMock()

        await ProjectAssignmentService._capture_terminal_member_spend(allocation, mock_provider, now)

        inserted_rows = mock_repo.insert_member_budget_entries.call_args[0][1]
        row = inserted_rows[0]
        assert row.provider_subject_id == "ref-fallback"


class TestSyncProjectBudgetMemberRemoved:
    """Unit tests for ProjectAssignmentService._sync_project_budget_member_removed."""

    def _make_allocation(self, category="platform", provider_member_ref="ref-1"):
        raw = {"provider_member_ref": provider_member_ref} if provider_member_ref else {}
        return ProjectMemberBudgetAssignment(
            project_name="myproject",
            budget_category=category,
            project_budget_id="bgt-1",
            user_id="user-abc",
            allocation_mode="equal",
            allocated_soft_budget=50.0,
            allocated_max_budget=100.0,
            assigned_by="admin",
            budget_id="bgt-1",
            provider_metadata={"raw": raw},
        )

    @patch("codemie.service.project.project_assignment_service.get_active_provider")
    def test_spend_capture_failure_is_fail_open(self, mock_get_provider):
        """Exception in spend capture is swallowed; deletion still proceeds."""
        session = MagicMock()
        alloc = self._make_allocation()
        session.exec.return_value.all.return_value = [alloc]

        mock_provider = MagicMock()
        mock_provider.delete_member_allocation.return_value = MagicMock()
        mock_get_provider.return_value = mock_provider

        with patch.object(ProjectAssignmentService, "_run_budget_provider_coro") as mock_run:
            mock_run.side_effect = [Exception("LiteLLM timeout"), None]
            ProjectAssignmentService._sync_project_budget_member_removed(session, "myproject", "user-abc")

        assert alloc.deleted_at is not None
        assert mock_run.call_count == 2

    @patch("codemie.service.project.project_assignment_service.get_active_provider")
    def test_no_allocations_returns_early(self, mock_get_provider):
        """No active allocations: provider never called."""
        session = MagicMock()
        session.exec.return_value.all.return_value = []

        ProjectAssignmentService._sync_project_budget_member_removed(session, "myproject", "user-abc")

        mock_get_provider.assert_not_called()

    @patch("codemie.service.project.project_assignment_service.get_active_provider")
    def test_allocation_soft_deleted_after_capture_and_delete(self, mock_get_provider):
        """deleted_at is set on allocation after both capture and delete run."""
        session = MagicMock()
        alloc = self._make_allocation()
        session.exec.return_value.all.return_value = [alloc]

        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider

        with patch.object(ProjectAssignmentService, "_run_budget_provider_coro", return_value=None):
            ProjectAssignmentService._sync_project_budget_member_removed(session, "myproject", "user-abc")

        assert alloc.deleted_at is not None
        session.flush.assert_called_once()

    @patch("codemie.service.project.project_assignment_service.ProjectAssignmentService._capture_terminal_member_spend")
    @patch("codemie.service.project.project_assignment_service.get_active_provider")
    def test_bulk_removal_captures_spend_per_allocation(self, mock_get_provider, mock_capture):
        """Bulk removal: _capture_terminal_member_spend fires once per allocation."""
        session = MagicMock()
        alloc1 = self._make_allocation(category="platform", provider_member_ref="ref-1")
        alloc2 = self._make_allocation(category="cli", provider_member_ref="ref-2")
        session.exec.return_value.all.return_value = [alloc1, alloc2]

        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider
        mock_capture.return_value = MagicMock()

        with patch.object(ProjectAssignmentService, "_run_budget_provider_coro", return_value=None):
            ProjectAssignmentService._sync_project_budget_member_removed(session, "myproject", "user-abc")

        assert mock_capture.call_count == 2
        assert alloc1.deleted_at is not None
        assert alloc2.deleted_at is not None
