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

"""Tests for UserAccessService - User access management for projects and knowledge bases.

Covers:
- get_user_projects_list
- grant_project_access / update_user_project_access / revoke_project_access
  (delegation to ProjectAssignmentService)
- get_user_knowledge_bases_list
- grant_kb_access
- revoke_kb_access
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.user.user_access_service import UserAccessService


class TestGetUserProjectsList:
    """Tests for get_user_projects_list method"""

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    def test_get_user_projects_list_returns_projects(self, mock_user_project_repo, mock_user_repo, mock_get_session):
        """Test: Returns list of user's projects with correct structure"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-123")
        mock_user_repo.get_by_id.return_value = mock_user

        mock_project_1 = MagicMock(
            project_name="project-alpha",
            is_project_admin=True,
            date=datetime(2024, 1, 1, 12, 0, 0),
        )
        mock_project_2 = MagicMock(
            project_name="project-beta",
            is_project_admin=False,
            date=datetime(2024, 1, 2, 12, 0, 0),
        )
        mock_user_project_repo.get_by_user_id.return_value = [mock_project_1, mock_project_2]

        # Act
        result = UserAccessService.get_user_projects_list("user-123")

        # Assert
        assert "projects" in result
        assert len(result["projects"]) == 2
        assert result["projects"][0]["project_name"] == "project-alpha"
        assert result["projects"][0]["is_project_admin"] is True
        assert result["projects"][1]["project_name"] == "project-beta"
        assert result["projects"][1]["is_project_admin"] is False
        mock_user_repo.get_by_id.assert_called_once_with(mock_session, "user-123")
        mock_user_project_repo.get_by_user_id.assert_called_once_with(mock_session, "user-123")

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    def test_get_user_projects_list_empty_list(self, mock_user_project_repo, mock_user_repo, mock_get_session):
        """Test: Returns empty list when user has no projects"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-456")
        mock_user_repo.get_by_id.return_value = mock_user
        mock_user_project_repo.get_by_user_id.return_value = []

        # Act
        result = UserAccessService.get_user_projects_list("user-456")

        # Assert
        assert result == {"projects": []}
        mock_user_repo.get_by_id.assert_called_once_with(mock_session, "user-456")

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_get_user_projects_list_user_not_found(self, mock_user_repo, mock_get_session):
        """Test: Raises 404 when user does not exist"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = None

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.get_user_projects_list("nonexistent-user")

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User not found"


PAS_PATH = "codemie.service.project.project_assignment_service.project_assignment_service"


def _actor():
    return MagicMock(id="admin-456", is_admin=False, is_maintainer=False, is_admin_or_maintainer=False)


def _session(mock_get_session):
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    return mock_session


class TestGrantProjectAccess:
    """Tests for grant_project_access: delegates to ProjectAssignmentService"""

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.logger")
    @patch(PAS_PATH)
    def test_grant_delegates_to_assign_user_to_project(self, mock_pas, mock_logger, mock_app_repo, mock_get_session):
        mock_session = _session(mock_get_session)
        project = MagicMock(name="project-alpha")
        mock_app_repo.get_by_name.return_value = project
        actor = _actor()

        result = UserAccessService.grant_project_access(
            user_id="user-123", project_name="project-alpha", is_project_admin=True, actor=actor
        )

        assert result == {"message": "Project access granted successfully"}
        mock_app_repo.get_by_name.assert_called_once_with(mock_session, "project-alpha")
        mock_pas.assign_user_to_project.assert_called_once_with(
            mock_session,
            project,
            "user-123",
            "project-alpha",
            True,
            actor,
            "POST /v1/admin/users/user-123/projects/project-alpha",
        )
        mock_app_repo.get_or_create.assert_not_called()
        mock_session.commit.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]
        assert "project_access_granted" in log_message
        assert "actor_user_id=admin-456" in log_message
        assert "target_user_id=user-123" in log_message
        assert "domain=user_management" in log_message

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch(PAS_PATH)
    def test_grant_missing_project_returns_404(self, mock_pas, mock_app_repo, mock_get_session):
        mock_session = _session(mock_get_session)
        mock_app_repo.get_by_name.return_value = None

        with patch.object(UserAccessService, "_invalidate_auth_cache") as mock_invalidate:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                UserAccessService.grant_project_access(
                    user_id="user-123", project_name="missing", is_project_admin=False, actor=_actor()
                )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        mock_pas.assign_user_to_project.assert_not_called()
        mock_app_repo.get_or_create.assert_not_called()
        mock_session.commit.assert_not_called()
        mock_invalidate.assert_not_called()

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch(PAS_PATH)
    def test_grant_commits_then_invalidates(self, mock_pas, mock_app_repo, mock_get_session):
        mock_session = _session(mock_get_session)
        mock_app_repo.get_by_name.return_value = MagicMock()
        calls = []
        mock_pas.assign_user_to_project.side_effect = lambda *a, **k: calls.append("pas")
        mock_session.commit.side_effect = lambda: calls.append("commit")

        with patch.object(
            UserAccessService, "_invalidate_auth_cache", side_effect=lambda uid: calls.append("invalidate")
        ) as mock_invalidate:
            UserAccessService.grant_project_access(
                user_id="user-123", project_name="project-alpha", is_project_admin=False, actor=_actor()
            )

        assert calls == ["pas", "commit", "invalidate"]
        mock_invalidate.assert_called_once_with("user-123")

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch(PAS_PATH)
    def test_grant_pas_error_propagates_without_commit(self, mock_pas, mock_app_repo, mock_get_session):
        mock_session = _session(mock_get_session)
        mock_app_repo.get_by_name.return_value = MagicMock()
        mock_pas.assign_user_to_project.side_effect = ExtendedHTTPException(code=409, message="x")

        with patch.object(UserAccessService, "_invalidate_auth_cache") as mock_invalidate:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                UserAccessService.grant_project_access(
                    user_id="user-123", project_name="project-alpha", is_project_admin=False, actor=_actor()
                )

        assert exc_info.value.code == 409
        assert exc_info.value.message == "x"
        mock_session.commit.assert_not_called()
        mock_invalidate.assert_not_called()


class TestUpdateUserProjectAccess:
    """Tests for update_user_project_access: delegates to ProjectAssignmentService"""

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.logger")
    @patch(PAS_PATH)
    def test_update_delegates_to_update_user_project_role(self, mock_pas, mock_logger, mock_app_repo, mock_get_session):
        mock_session = _session(mock_get_session)
        project = MagicMock(name="project-alpha")
        mock_app_repo.get_by_name.return_value = project
        actor = _actor()

        result = UserAccessService.update_user_project_access(
            user_id="user-123", project_name="project-alpha", is_project_admin=True, actor=actor
        )

        assert result == {"message": "Project access updated successfully"}
        mock_pas.update_user_project_role.assert_called_once_with(
            mock_session,
            project,
            "user-123",
            "project-alpha",
            True,
            actor,
            "PUT /v1/admin/users/user-123/projects/project-alpha",
        )
        mock_session.commit.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]
        assert "project_access_updated" in log_message
        assert "domain=user_management" in log_message

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch(PAS_PATH)
    def test_update_missing_project_returns_404(self, mock_pas, mock_app_repo, mock_get_session):
        mock_session = _session(mock_get_session)
        mock_app_repo.get_by_name.return_value = None

        with patch.object(UserAccessService, "_invalidate_auth_cache") as mock_invalidate:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                UserAccessService.update_user_project_access(
                    user_id="user-123", project_name="missing", is_project_admin=True, actor=_actor()
                )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        mock_pas.update_user_project_role.assert_not_called()
        mock_session.commit.assert_not_called()
        mock_invalidate.assert_not_called()

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch(PAS_PATH)
    def test_update_commits_then_invalidates(self, mock_pas, mock_app_repo, mock_get_session):
        mock_session = _session(mock_get_session)
        mock_app_repo.get_by_name.return_value = MagicMock()
        calls = []
        mock_pas.update_user_project_role.side_effect = lambda *a, **k: calls.append("pas")
        mock_session.commit.side_effect = lambda: calls.append("commit")

        with patch.object(
            UserAccessService, "_invalidate_auth_cache", side_effect=lambda uid: calls.append("invalidate")
        ):
            UserAccessService.update_user_project_access(
                user_id="user-123", project_name="project-alpha", is_project_admin=True, actor=_actor()
            )

        assert calls == ["pas", "commit", "invalidate"]

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch(PAS_PATH)
    def test_update_pas_error_propagates_without_commit(self, mock_pas, mock_app_repo, mock_get_session):
        mock_session = _session(mock_get_session)
        mock_app_repo.get_by_name.return_value = MagicMock()
        mock_pas.update_user_project_role.side_effect = ExtendedHTTPException(
            code=404, message="User is not assigned to this project"
        )

        with patch.object(UserAccessService, "_invalidate_auth_cache") as mock_invalidate:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                UserAccessService.update_user_project_access(
                    user_id="user-123", project_name="project-alpha", is_project_admin=True, actor=_actor()
                )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User is not assigned to this project"
        mock_session.commit.assert_not_called()
        mock_invalidate.assert_not_called()


class TestRevokeProjectAccess:
    """Tests for revoke_project_access: delegates to ProjectAssignmentService"""

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.logger")
    @patch(PAS_PATH)
    def test_revoke_delegates_to_remove_user_from_project(self, mock_pas, mock_logger, mock_app_repo, mock_get_session):
        mock_session = _session(mock_get_session)
        project = MagicMock(name="project-alpha")
        mock_app_repo.get_by_name.return_value = project
        actor = _actor()

        result = UserAccessService.revoke_project_access(user_id="user-123", project_name="project-alpha", actor=actor)

        assert result == {"message": "Project access removed successfully"}
        mock_pas.remove_user_from_project.assert_called_once_with(
            mock_session,
            project,
            "user-123",
            "project-alpha",
            actor,
            "DELETE /v1/admin/users/user-123/projects/project-alpha",
        )
        mock_session.commit.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]
        assert "project_access_removed" in log_message
        assert "actor_user_id=admin-456" in log_message
        assert "domain=user_management" in log_message

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch(PAS_PATH)
    def test_revoke_missing_project_returns_404(self, mock_pas, mock_app_repo, mock_get_session):
        mock_session = _session(mock_get_session)
        mock_app_repo.get_by_name.return_value = None

        with patch.object(UserAccessService, "_invalidate_auth_cache") as mock_invalidate:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                UserAccessService.revoke_project_access(user_id="user-123", project_name="missing", actor=_actor())

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        mock_pas.remove_user_from_project.assert_not_called()
        mock_session.commit.assert_not_called()
        mock_invalidate.assert_not_called()

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch(PAS_PATH)
    def test_revoke_commits_then_invalidates(self, mock_pas, mock_app_repo, mock_get_session):
        mock_session = _session(mock_get_session)
        mock_app_repo.get_by_name.return_value = MagicMock()
        calls = []
        mock_pas.remove_user_from_project.side_effect = lambda *a, **k: calls.append("pas")
        mock_session.commit.side_effect = lambda: calls.append("commit")

        with patch.object(
            UserAccessService, "_invalidate_auth_cache", side_effect=lambda uid: calls.append("invalidate")
        ):
            UserAccessService.revoke_project_access(user_id="user-123", project_name="project-alpha", actor=_actor())

        assert calls == ["pas", "commit", "invalidate"]

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch(PAS_PATH)
    def test_revoke_creator_guard_error_propagates_without_commit(self, mock_pas, mock_app_repo, mock_get_session):
        """AC2: PAS's 400 creator guard reaches the caller unchanged."""
        mock_session = _session(mock_get_session)
        mock_app_repo.get_by_name.return_value = MagicMock()
        mock_pas.remove_user_from_project.side_effect = ExtendedHTTPException(
            code=400, message="Cannot remove project creator"
        )

        with patch.object(UserAccessService, "_invalidate_auth_cache") as mock_invalidate:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                UserAccessService.revoke_project_access(
                    user_id="user-123", project_name="project-alpha", actor=_actor()
                )

        assert exc_info.value.code == 400
        assert exc_info.value.message == "Cannot remove project creator"
        mock_session.commit.assert_not_called()
        mock_invalidate.assert_not_called()


class TestGetUserKnowledgeBasesList:
    """Tests for get_user_knowledge_bases_list method"""

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.user_kb_repository")
    def test_get_user_knowledge_bases_list_returns_kbs(self, mock_user_kb_repo, mock_user_repo, mock_get_session):
        """Test: Returns list of user's knowledge bases with correct structure"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-123")
        mock_user_repo.get_by_id.return_value = mock_user

        mock_kb_1 = MagicMock(kb_name="kb-alpha", date=datetime(2024, 1, 1, 12, 0, 0))
        mock_kb_2 = MagicMock(kb_name="kb-beta", date=datetime(2024, 1, 2, 12, 0, 0))
        mock_user_kb_repo.get_by_user_id.return_value = [mock_kb_1, mock_kb_2]

        # Act
        result = UserAccessService.get_user_knowledge_bases_list("user-123")

        # Assert
        assert "knowledge_bases" in result
        assert len(result["knowledge_bases"]) == 2
        assert result["knowledge_bases"][0]["kb_name"] == "kb-alpha"
        assert result["knowledge_bases"][1]["kb_name"] == "kb-beta"
        mock_user_repo.get_by_id.assert_called_once_with(mock_session, "user-123")
        mock_user_kb_repo.get_by_user_id.assert_called_once_with(mock_session, "user-123")

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.user_kb_repository")
    def test_get_user_knowledge_bases_list_empty_list(self, mock_user_kb_repo, mock_user_repo, mock_get_session):
        """Test: Returns empty list when user has no knowledge bases"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-456")
        mock_user_repo.get_by_id.return_value = mock_user
        mock_user_kb_repo.get_by_user_id.return_value = []

        # Act
        result = UserAccessService.get_user_knowledge_bases_list("user-456")

        # Assert
        assert result == {"knowledge_bases": []}
        mock_user_repo.get_by_id.assert_called_once_with(mock_session, "user-456")

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_get_user_knowledge_bases_list_user_not_found(self, mock_user_repo, mock_get_session):
        """Test: Raises 404 when user does not exist"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = None

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.get_user_knowledge_bases_list("nonexistent-user")

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User not found"


class TestGrantKBAccess:
    """Tests for grant_kb_access method"""

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.user_kb_repository")
    @patch("codemie.service.user.user_access_service.logger")
    def test_grant_kb_access_success(self, mock_logger, mock_user_kb_repo, mock_user_repo, mock_get_session):
        """Test: Successfully grants knowledge base access to user"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-123")
        mock_user_repo.get_by_id.return_value = mock_user
        mock_user_kb_repo.get_by_user_and_kb.return_value = None

        # Act
        result = UserAccessService.grant_kb_access(user_id="user-123", kb_name="kb-alpha", actor_user_id="admin-456")

        # Assert
        assert result["message"] == "Knowledge base access granted successfully"
        mock_user_kb_repo.add_kb.assert_called_once_with(mock_session, "user-123", "kb-alpha")
        mock_session.commit.assert_called_once()
        mock_logger.info.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]
        assert "kb_access_granted" in log_message
        assert "actor_user_id=admin-456" in log_message
        assert "target_user_id=user-123" in log_message
        assert "kb=kb-alpha" in log_message

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_grant_kb_access_user_not_found(self, mock_user_repo, mock_get_session):
        """Test: Raises 404 when target user does not exist"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = None

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.grant_kb_access(user_id="nonexistent-user", kb_name="kb-alpha", actor_user_id="admin-456")

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User not found"

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.user_kb_repository")
    def test_grant_kb_access_already_has_access(self, mock_user_kb_repo, mock_user_repo, mock_get_session):
        """Test: Raises 409 when user already has access to knowledge base"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-123")
        mock_user_repo.get_by_id.return_value = mock_user

        mock_existing = MagicMock(kb_name="kb-alpha")
        mock_user_kb_repo.get_by_user_and_kb.return_value = mock_existing

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.grant_kb_access(user_id="user-123", kb_name="kb-alpha", actor_user_id="admin-456")

        assert exc_info.value.code == 409
        assert exc_info.value.message == "User already has access to this knowledge base"


class TestRevokeKBAccess:
    """Tests for revoke_kb_access method"""

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.user_kb_repository")
    @patch("codemie.service.user.user_access_service.logger")
    def test_revoke_kb_access_success(self, mock_logger, mock_user_kb_repo, mock_user_repo, mock_get_session):
        """Test: Successfully revokes user's knowledge base access"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-123")
        mock_user_repo.get_by_id.return_value = mock_user
        mock_user_kb_repo.remove_kb.return_value = True

        # Act
        result = UserAccessService.revoke_kb_access(user_id="user-123", kb_name="kb-alpha", actor_user_id="admin-456")

        # Assert
        assert result["message"] == "Knowledge base access removed successfully"
        mock_user_kb_repo.remove_kb.assert_called_once_with(mock_session, "user-123", "kb-alpha")
        mock_session.commit.assert_called_once()
        mock_logger.info.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]
        assert "kb_access_removed" in log_message
        assert "actor_user_id=admin-456" in log_message
        assert "kb=kb-alpha" in log_message

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_revoke_kb_access_user_not_found(self, mock_user_repo, mock_get_session):
        """Test: Raises 404 when target user does not exist"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = None

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.revoke_kb_access(
                user_id="nonexistent-user", kb_name="kb-alpha", actor_user_id="admin-456"
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User not found"

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.user_kb_repository")
    def test_revoke_kb_access_no_existing_access(self, mock_user_kb_repo, mock_user_repo, mock_get_session):
        """Test: Raises 404 when user does not have access to knowledge base"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-123")
        mock_user_repo.get_by_id.return_value = mock_user
        mock_user_kb_repo.remove_kb.return_value = False

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.revoke_kb_access(user_id="user-123", kb_name="kb-alpha", actor_user_id="admin-456")

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User does not have access to this knowledge base"


class TestHelperMethods:
    """Tests for helper methods in UserAccessService"""

    def test_build_project_access_log_details(self):
        """Test: Builds consistent log details without PII"""
        # Act
        result = UserAccessService._build_project_access_log_details(
            actor_user_id="admin-456", target_user_id="user-123"
        )

        # Assert
        assert "actor_user_id=admin-456" in result
        assert "target_user_id=user-123" in result
        assert ", " in result


class TestGetUserProjectsListDefaultField:
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_includes_is_default_in_projects_list(self, mock_user_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_upr.get_by_user_id.return_value = [
            MagicMock(project_name="proj-a", is_project_admin=False, is_default=True, date=None),
        ]

        result = UserAccessService.get_user_projects_list("user-1")

        assert result["projects"][0]["is_default"] is True

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_derives_personal_default_when_none_stored(self, mock_user_repo, mock_upr, mock_get_session):
        """EPMCDME-15738: with nothing stored, the personal project is reported as the default."""
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1", email="u@example.com")
        mock_upr.get_by_user_id.return_value = [
            MagicMock(project_name="alpha", is_project_admin=False, is_default=False, date=None),
            MagicMock(project_name="u@example.com", is_project_admin=False, is_default=False, date=None),
        ]

        result = UserAccessService.get_user_projects_list("user-1")

        assert {p["project_name"]: p["is_default"] for p in result["projects"]} == {
            "alpha": False,
            "u@example.com": True,
        }
