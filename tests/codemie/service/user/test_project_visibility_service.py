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

"""Tests for project visibility service."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.user.project_visibility_service import ProjectVisibilityService


class TestProjectVisibilityService:
    @patch("codemie.service.user.project_visibility_service.logger")
    def test_get_visible_project_or_404_logs_and_raises(self, mock_logger):
        mock_session = MagicMock()

        with patch(
            "codemie.service.user.project_visibility_service.application_repository.get_visible_project",
            return_value=None,
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                ProjectVisibilityService.get_visible_project_or_404(
                    session=mock_session,
                    project_name="hidden-proj",
                    user_id="user-1",
                    is_super_admin=False,
                    action="get_project_detail",
                )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        assert mock_logger.warning.call_count == 1
        log_message = mock_logger.warning.call_args[0][0]
        # Story 16 R3: PII removal - project_name no longer logged
        # action format is "METHOD /path" - only method part is logged
        assert "user_id=user-1" in log_message
        # action="get_project_detail" has no space, so entire string becomes method
        assert "method=get_project_detail" in log_message
        assert "timestamp=" in log_message
        # project_name should NOT be in logs (PII removal)
        assert "hidden-proj" not in log_message

    def test_ensure_project_admin_or_super_admin_allows_super_admin(self):
        mock_session = MagicMock()
        mock_project = MagicMock(project_type="shared", created_by="owner-1")

        with patch(
            "codemie.service.user.project_visibility_service.application_repository.get_project_authorization_context",
            return_value=(mock_project, None),
        ) as mock_context:
            result = ProjectVisibilityService.ensure_project_admin_or_super_admin_or_404(
                session=mock_session,
                project_name="shared-proj",
                user_id="admin-1",
                is_super_admin=True,
                action="assign_user_to_project",
            )

        assert result is mock_project
        mock_context.assert_called_once_with(
            session=mock_session,
            project_name="shared-proj",
            user_id="admin-1",
        )

    def test_ensure_project_admin_or_super_admin_fails_visibility_before_admin_check(self):
        """Visibility must be checked before admin status for personal projects."""
        mock_session = MagicMock()
        hidden_personal_project = MagicMock(project_type="personal", created_by="owner-1")

        with (
            patch(
                "codemie.service.user.project_visibility_service.application_repository.get_project_authorization_context",
                return_value=(hidden_personal_project, True),
            ),
            patch.object(
                ProjectVisibilityService,
                "raise_project_not_found",
                side_effect=ExtendedHTTPException(code=404, message="Project not found"),
            ),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                ProjectVisibilityService.ensure_project_admin_or_super_admin_or_404(
                    session=mock_session,
                    project_name="owner@example.com",
                    user_id="non-owner",
                    is_super_admin=False,
                    action="assign_user_to_project",
                )

        assert exc_info.value.code == 404

    def test_ensure_project_admin_or_super_admin_requires_admin_for_non_super_admin(self):
        mock_session = MagicMock()
        shared_project = MagicMock(project_type="shared", created_by="owner-1")

        with (
            patch(
                "codemie.service.user.project_visibility_service.application_repository.get_project_authorization_context",
                return_value=(shared_project, False),
            ) as mock_context,
            patch(
                "codemie.service.user.project_visibility_service.application_repository.get_visible_project"
            ) as mock_visible_lookup,
            patch.object(
                ProjectVisibilityService,
                "raise_project_not_found",
                side_effect=ExtendedHTTPException(code=404, message="Project not found"),
            ),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                ProjectVisibilityService.ensure_project_admin_or_super_admin_or_404(
                    session=mock_session,
                    project_name="shared-proj",
                    user_id="member-1",
                    is_super_admin=False,
                    action="assign_user_to_project",
                )

        assert exc_info.value.code == 404
        mock_context.assert_called_once()
        mock_visible_lookup.assert_not_called()

    def test_ensure_project_admin_or_super_admin_stops_before_admin_check_when_not_visible(self):
        """If visibility fails, admin check must not evaluate membership truthiness."""

        class BoolGuard:
            def __bool__(self):
                raise AssertionError("Admin check should not execute before visibility check")

        mock_session = MagicMock()
        shared_project = MagicMock(project_type="shared", created_by="owner-1")

        with (
            patch(
                "codemie.service.user.project_visibility_service.application_repository.get_project_authorization_context",
                return_value=(shared_project, BoolGuard()),
            ),
            patch.object(
                ProjectVisibilityService,
                "_is_project_visible_to_user",
                return_value=False,
            ),
            patch.object(
                ProjectVisibilityService,
                "raise_project_not_found",
                side_effect=ExtendedHTTPException(code=404, message="Project not found"),
            ),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                ProjectVisibilityService.ensure_project_admin_or_super_admin_or_404(
                    session=mock_session,
                    project_name="shared-proj",
                    user_id="member-1",
                    is_super_admin=False,
                    action="POST /v1/projects/shared-proj/assignment",
                )

        assert exc_info.value.code == 404

    def test_ensure_project_admin_or_super_admin_succeeds_for_project_admin(self):
        mock_session = MagicMock()
        shared_project = MagicMock(project_type="shared", created_by="owner-1")

        with patch(
            "codemie.service.user.project_visibility_service.application_repository.get_project_authorization_context",
            return_value=(shared_project, True),
        ) as mock_context:
            result = ProjectVisibilityService.ensure_project_admin_or_super_admin_or_404(
                session=mock_session,
                project_name="shared-proj",
                user_id="project-admin",
                is_super_admin=False,
                action="assign_user_to_project",
            )

        assert result is shared_project
        mock_context.assert_called_once_with(
            session=mock_session,
            project_name="shared-proj",
            user_id="project-admin",
        )

    def test_authorize_project_admin_or_super_admin_uses_service_layer_session(self):
        mock_session = MagicMock()
        expected_project = MagicMock(project_type="shared")

        with (
            patch("codemie.clients.postgres.get_session") as mock_get_session,
            patch.object(
                ProjectVisibilityService,
                "ensure_project_admin_or_super_admin_or_404",
                return_value=expected_project,
            ) as mock_authorize,
        ):
            mock_get_session.return_value.__enter__.return_value = mock_session
            result = ProjectVisibilityService.authorize_project_admin_or_super_admin(
                project_name="shared-proj",
                user_id="user-1",
                is_super_admin=False,
                action="POST /v1/projects/shared-proj/assignment",
            )

        assert result is expected_project
        mock_authorize.assert_called_once_with(
            session=mock_session,
            project_name="shared-proj",
            user_id="user-1",
            is_super_admin=False,
            action="POST /v1/projects/shared-proj/assignment",
        )

    def test_is_project_visible_super_admin_sees_all(self):
        """Test that super admin can see any project regardless of type or ownership."""
        # Arrange
        personal_project = MagicMock(project_type="personal", created_by="other-user")

        # Act
        result = ProjectVisibilityService._is_project_visible_to_user(
            project=personal_project,
            user_id="admin-user",
            is_super_admin=True,
            membership_is_project_admin=None,
        )

        # Assert
        assert result is True

    def test_is_project_visible_personal_project_owner(self):
        """Test that personal project owner can see their own project."""
        # Arrange
        personal_project = MagicMock(project_type="personal", created_by="owner-user")

        # Act
        result = ProjectVisibilityService._is_project_visible_to_user(
            project=personal_project,
            user_id="owner-user",
            is_super_admin=False,
            membership_is_project_admin=None,
        )

        # Assert
        assert result is True

    def test_is_project_visible_personal_project_non_owner(self):
        """Test that non-owner cannot see personal project."""
        # Arrange
        personal_project = MagicMock(project_type="personal", created_by="owner-user")

        # Act
        result = ProjectVisibilityService._is_project_visible_to_user(
            project=personal_project,
            user_id="other-user",
            is_super_admin=False,
            membership_is_project_admin=None,
        )

        # Assert
        assert result is False

    def test_is_project_visible_shared_project_member(self):
        """Test that shared project member can see project (membership_is_project_admin is not None)."""
        # Arrange
        shared_project = MagicMock(project_type="shared", created_by="owner-user")

        # Act - regular member (is_project_admin=False)
        result = ProjectVisibilityService._is_project_visible_to_user(
            project=shared_project,
            user_id="member-user",
            is_super_admin=False,
            membership_is_project_admin=False,
        )

        # Assert
        assert result is True

    def test_is_project_visible_shared_project_admin(self):
        """Test that shared project admin can see project."""
        # Arrange
        shared_project = MagicMock(project_type="shared", created_by="owner-user")

        # Act - project admin (is_project_admin=True)
        result = ProjectVisibilityService._is_project_visible_to_user(
            project=shared_project,
            user_id="admin-user",
            is_super_admin=False,
            membership_is_project_admin=True,
        )

        # Assert
        assert result is True

    def test_is_project_visible_shared_project_non_member(self):
        """Test that non-member cannot see shared project."""
        # Arrange
        shared_project = MagicMock(project_type="shared", created_by="owner-user")

        # Act - no membership (membership_is_project_admin=None)
        result = ProjectVisibilityService._is_project_visible_to_user(
            project=shared_project,
            user_id="other-user",
            is_super_admin=False,
            membership_is_project_admin=None,
        )

        # Assert
        assert result is False

    @patch("codemie.service.user.project_visibility_service.logger")
    def test_raise_project_not_found_with_method_and_path(self, mock_logger):
        """Test raise_project_not_found extracts HTTP method from action string."""
        # Arrange
        action = "POST /v1/projects/test-project/assignment"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectVisibilityService.raise_project_not_found(
                user_id="user-123",
                project_name="test-project",
                action=action,
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"

        # Verify logging
        mock_logger.warning.assert_called_once()
        log_message = mock_logger.warning.call_args[0][0]
        assert "user_id=user-123" in log_message
        assert "method=POST" in log_message
        assert "timestamp=" in log_message
        # PII: project_name should NOT be logged
        assert "test-project" not in log_message

    @patch("codemie.service.user.project_visibility_service.logger")
    def test_raise_project_not_found_with_method_only(self, mock_logger):
        """Test raise_project_not_found handles action without path (no space)."""
        # Arrange
        action = "DELETE"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectVisibilityService.raise_project_not_found(
                user_id="user-456",
                project_name="hidden-project",
                action=action,
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"

        # Verify logging
        mock_logger.warning.assert_called_once()
        log_message = mock_logger.warning.call_args[0][0]
        assert "user_id=user-456" in log_message
        assert "method=DELETE" in log_message
        assert "timestamp=" in log_message

    @patch("codemie.service.user.project_visibility_service.logger")
    def test_raise_project_not_found_with_empty_action(self, mock_logger):
        """Test raise_project_not_found handles empty action string."""
        # Arrange
        action = ""

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectVisibilityService.raise_project_not_found(
                user_id="user-789",
                project_name="some-project",
                action=action,
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"

        # Verify logging - should default to UNKNOWN
        mock_logger.warning.assert_called_once()
        log_message = mock_logger.warning.call_args[0][0]
        assert "user_id=user-789" in log_message
        assert "method=UNKNOWN" in log_message
        assert "timestamp=" in log_message

    def test_authorize_project_admin_or_super_admin_delegates_to_ensure_method(self):
        """Test that authorize method creates session and delegates to ensure method."""
        # Arrange
        mock_session = MagicMock()
        expected_project = MagicMock(project_type="shared", created_by="owner-1")

        with (
            patch("codemie.clients.postgres.get_session") as mock_get_session,
            patch.object(
                ProjectVisibilityService,
                "ensure_project_admin_or_super_admin_or_404",
                return_value=expected_project,
            ) as mock_ensure,
        ):
            mock_get_session.return_value.__enter__.return_value = mock_session

            # Act
            result = ProjectVisibilityService.authorize_project_admin_or_super_admin(
                project_name="test-proj",
                user_id="test-user",
                is_super_admin=True,
                action="GET /v1/projects/test-proj",
            )

        # Assert
        assert result is expected_project
        mock_ensure.assert_called_once_with(
            session=mock_session,
            project_name="test-proj",
            user_id="test-user",
            is_super_admin=True,
            action="GET /v1/projects/test-proj",
        )

    def test_authorize_project_admin_or_super_admin_raises_when_not_authorized(self):
        """Test that authorize method propagates 404 when user is not authorized."""
        # Arrange
        mock_session = MagicMock()

        with (
            patch("codemie.clients.postgres.get_session") as mock_get_session,
            patch.object(
                ProjectVisibilityService,
                "ensure_project_admin_or_super_admin_or_404",
                side_effect=ExtendedHTTPException(code=404, message="Project not found"),
            ),
        ):
            mock_get_session.return_value.__enter__.return_value = mock_session

            # Act & Assert
            with pytest.raises(ExtendedHTTPException) as exc_info:
                ProjectVisibilityService.authorize_project_admin_or_super_admin(
                    project_name="forbidden-proj",
                    user_id="regular-user",
                    is_super_admin=False,
                    action="POST /v1/projects/forbidden-proj/assignment",
                )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
