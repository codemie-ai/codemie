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

"""Tests for personal project constraints in user_access_service.

Story 10: Personal Project Constraints Enforcement

Tests cover:
- Assignment blocking (grant/update/revoke) returns 404
- Super admin cannot override (also gets 404)
- Error messages don't reveal project type
"""

import pytest
from unittest.mock import MagicMock, patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.user.user_access_service import UserAccessService


class TestPersonalProjectAssignmentBlocking:
    """Test personal project assignment operations are blocked with 404"""

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.logger")
    def test_grant_access_to_personal_project_returns_404(
        self, mock_logger, mock_app_repo, mock_user_repo, mock_get_session
    ):
        """Test: Attempting to add user to personal project returns 404"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-123")
        mock_user_repo.get_by_id.return_value = mock_user

        # Personal project check returns True
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="personal", created_by="other-user")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.grant_project_access(
                user_id="user-123",
                project_name="alice@example.com",
                is_project_admin=False,
                actor=MagicMock(id="admin-456", is_admin=False),
            )

        # Assert: 404 (not 403) to hide project existence
        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        assert mock_logger.warning.call_count == 1
        log_message = mock_logger.warning.call_args[0][0]
        assert "project_authorization_failed" in log_message
        assert "user_id=admin-456" in log_message
        assert "method=grant_project_access" in log_message

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.logger")
    def test_update_access_to_personal_project_returns_404(
        self, mock_logger, mock_app_repo, mock_user_repo, mock_get_session
    ):
        """Test: Attempting to update user's personal project access returns 404"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-123")
        mock_user_repo.get_by_id.return_value = mock_user

        # Personal project check returns True
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="personal", created_by="other-user")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.update_user_project_access(
                user_id="user-123",
                project_name="alice@example.com",
                is_project_admin=True,
                actor=MagicMock(id="admin-456", is_admin=False),
            )

        # Assert: 404 (not 403) to hide project existence
        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        assert mock_logger.warning.call_count == 1
        log_message = mock_logger.warning.call_args[0][0]
        assert "project_authorization_failed" in log_message
        assert "user_id=admin-456" in log_message
        assert "method=update_user_project_access" in log_message

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.logger")
    def test_revoke_access_from_personal_project_returns_404(
        self, mock_logger, mock_app_repo, mock_user_repo, mock_get_session
    ):
        """Test: Attempting to remove user from personal project returns 404"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-123")
        mock_user_repo.get_by_id.return_value = mock_user

        # Personal project check returns True
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="personal", created_by="other-user")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.revoke_project_access(
                user_id="user-123",
                project_name="alice@example.com",
                actor=MagicMock(id="admin-456", is_admin=False),
            )

        # Assert: 404 (not 403) to hide project existence
        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        assert mock_logger.warning.call_count == 1
        log_message = mock_logger.warning.call_args[0][0]
        assert "project_authorization_failed" in log_message
        assert "user_id=admin-456" in log_message
        assert "method=revoke_project_access" in log_message

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    def test_grant_access_to_shared_project_succeeds(
        self, mock_user_project_repo, mock_app_repo, mock_user_repo, mock_get_session
    ):
        """Test: Adding user to shared project works normally"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-123")
        mock_user_repo.get_by_id.return_value = mock_user

        # Shared project (not personal)
        mock_app_repo.get_by_name.return_value = None  # Not a personal project
        mock_user_project_repo.get_by_user_and_project.return_value = None  # No existing access

        # Act
        result = UserAccessService.grant_project_access(
            user_id="user-123",
            project_name="shared-project",
            is_project_admin=False,
            actor=MagicMock(id="admin-456", is_admin=False),
        )

        # Assert: Success
        assert result["message"] == "Project access granted successfully"
        mock_user_project_repo.add_project.assert_called_once()

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    def test_super_admin_cannot_add_user_to_personal_project(self, mock_app_repo, mock_user_repo, mock_get_session):
        """Test: Even super admin gets 404 when attempting to add user to personal project"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user = MagicMock(id="user-123")
        mock_user_repo.get_by_id.return_value = mock_user

        # Personal project check returns True
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="personal", created_by="other-user")

        # Act & Assert: Super admin as actor
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.grant_project_access(
                user_id="user-123",
                project_name="alice@example.com",
                is_project_admin=False,
                actor=MagicMock(id="super-admin-789", is_admin=False),  # Non-super-admin actor
            )

        # Assert: Still 404 (no special treatment for super admin)
        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
