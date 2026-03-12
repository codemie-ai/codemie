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

"""Unit tests for PasswordManagementService

Tests password operations including:
- Password changes (self-service and admin)
- Password reset flows
- Password reset token creation and verification
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_management import EmailVerificationToken, UserDB
from codemie.service.user.password_management_service import PasswordManagementService


class TestChangePassword:
    """Tests for change_password() core method"""

    @patch("codemie.service.user.password_management_service.user_repository")
    @patch("codemie.service.password_service.password_service")
    @patch("codemie.service.user.password_management_service.config")
    def test_change_password_success(self, mock_config, mock_pwd_service, mock_user_repo):
        """Successfully changes password with valid current password"""
        # Arrange
        session = MagicMock()
        user_id = str(uuid4())
        current_password = "OldPassword123"
        new_password = "NewPassword456"
        old_hash = "$argon2id$v=19$m=65536,t=3,p=4$oldhash"
        new_hash = "$argon2id$v=19$m=65536,t=3,p=4$newhash"

        mock_config.PASSWORD_MIN_LENGTH = 8

        mock_user = UserDB(
            id=user_id,
            username="testuser",
            email="test@example.com",
            password_hash=old_hash,
            is_active=True,
        )

        mock_user_repo.get_by_id.return_value = mock_user
        mock_pwd_service.verify_password.return_value = True
        mock_pwd_service.hash_password.return_value = new_hash

        # Act
        result = PasswordManagementService.change_password(
            session, user_id, new_password, current_password=current_password
        )

        # Assert
        assert result is True
        mock_user_repo.get_by_id.assert_called_once_with(session, user_id)
        mock_pwd_service.verify_password.assert_called_once_with(old_hash, current_password)
        mock_pwd_service.hash_password.assert_called_once_with(new_password)
        mock_user_repo.update.assert_called_once_with(session, user_id, password_hash=new_hash)

    @patch("codemie.service.user.password_management_service.user_repository")
    def test_change_password_user_not_found(self, mock_user_repo):
        """Raises 404 when user does not exist"""
        # Arrange
        session = MagicMock()
        user_id = str(uuid4())
        new_password = "NewPassword123"

        mock_user_repo.get_by_id.return_value = None

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            PasswordManagementService.change_password(session, user_id, new_password, current_password="old")

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User not found"

    @patch("codemie.service.user.password_management_service.user_repository")
    @patch("codemie.service.password_service.password_service")
    def test_change_password_wrong_current_password(self, mock_pwd_service, mock_user_repo):
        """Raises 401 when current password is incorrect"""
        # Arrange
        session = MagicMock()
        user_id = str(uuid4())
        current_password = "WrongPassword"
        new_password = "NewPassword123"

        mock_user = UserDB(
            id=user_id,
            username="testuser",
            email="test@example.com",
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$hash",
            is_active=True,
        )

        mock_user_repo.get_by_id.return_value = mock_user
        mock_pwd_service.verify_password.return_value = False

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            PasswordManagementService.change_password(session, user_id, new_password, current_password=current_password)

        assert exc_info.value.code == 401
        assert exc_info.value.message == "Current password is incorrect"

    @patch("codemie.service.user.password_management_service.user_repository")
    def test_change_password_no_password_hash(self, mock_user_repo):
        """Raises 400 when user has no password set (IDP user trying self-service)"""
        # Arrange
        session = MagicMock()
        user_id = str(uuid4())
        current_password = "SomePassword"
        new_password = "NewPassword123"

        mock_user = UserDB(
            id=user_id,
            username="idpuser",
            email="idp@example.com",
            password_hash=None,  # No password set (IDP user)
            is_active=True,
        )

        mock_user_repo.get_by_id.return_value = mock_user

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            PasswordManagementService.change_password(session, user_id, new_password, current_password=current_password)

        assert exc_info.value.code == 400
        assert exc_info.value.message == "User has no password set"

    @patch("codemie.service.user.password_management_service.user_repository")
    @patch("codemie.service.password_service.password_service")
    @patch("codemie.service.user.password_management_service.config")
    def test_change_password_too_short(self, mock_config, mock_pwd_service, mock_user_repo):
        """Raises 400 when new password is too short"""
        # Arrange
        session = MagicMock()
        user_id = str(uuid4())
        current_password = "OldPassword123"
        new_password = "short"  # Too short

        mock_config.PASSWORD_MIN_LENGTH = 8

        mock_user = UserDB(
            id=user_id,
            username="testuser",
            email="test@example.com",
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$hash",
            is_active=True,
        )

        mock_user_repo.get_by_id.return_value = mock_user
        mock_pwd_service.verify_password.return_value = True

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            PasswordManagementService.change_password(session, user_id, new_password, current_password=current_password)

        assert exc_info.value.code == 400
        assert "Password must be at least 8 characters" in exc_info.value.message

    @patch("codemie.service.user.password_management_service.user_repository")
    @patch("codemie.service.password_service.password_service")
    @patch("codemie.service.user.password_management_service.config")
    def test_change_password_no_current_password_admin(self, mock_config, mock_pwd_service, mock_user_repo):
        """Admin can change password without providing current password"""
        # Arrange
        session = MagicMock()
        user_id = str(uuid4())
        new_password = "NewPassword123"
        new_hash = "$argon2id$v=19$m=65536,t=3,p=4$newhash"

        mock_config.PASSWORD_MIN_LENGTH = 8

        mock_user = UserDB(
            id=user_id,
            username="testuser",
            email="test@example.com",
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$oldhash",
            is_active=True,
        )

        mock_user_repo.get_by_id.return_value = mock_user
        mock_pwd_service.hash_password.return_value = new_hash

        # Act
        result = PasswordManagementService.change_password(session, user_id, new_password, current_password=None)

        # Assert
        assert result is True
        # Verify password should NOT be called when current_password is None
        mock_pwd_service.verify_password.assert_not_called()
        mock_pwd_service.hash_password.assert_called_once_with(new_password)
        mock_user_repo.update.assert_called_once_with(session, user_id, password_hash=new_hash)


class TestCreateResetToken:
    """Tests for create_reset_token() method"""

    @patch("codemie.service.user.password_management_service.user_repository")
    @patch("codemie.service.user.password_management_service.email_token_repository")
    def test_create_reset_token_success(self, mock_token_repo, mock_user_repo):
        """Creates reset token for active local user"""
        # Arrange
        session = MagicMock()
        email = "user@example.com"
        user_id = str(uuid4())
        raw_token = "raw_token_abc123"

        mock_user = UserDB(
            id=user_id,
            username="testuser",
            email=email,
            is_active=True,
            auth_source="local",
        )

        mock_token_record = MagicMock(spec=EmailVerificationToken)
        mock_token_record.id = str(uuid4())
        mock_token_record.user_id = user_id
        mock_token_record.email = email
        mock_token_record.token_type = "password_reset"

        mock_user_repo.get_by_email.return_value = mock_user
        mock_token_repo.create_token.return_value = (raw_token, mock_token_record)

        # Act
        result = PasswordManagementService.create_reset_token(session, email)

        # Assert
        assert result == raw_token
        mock_user_repo.get_by_email.assert_called_once_with(session, email)
        mock_token_repo.invalidate_previous_tokens.assert_called_once_with(session, user_id, "password_reset")
        mock_token_repo.create_token.assert_called_once_with(
            session, user_id, email, "password_reset", expires_in_hours=24
        )

    @patch("codemie.service.user.password_management_service.user_repository")
    def test_create_reset_token_user_not_found(self, mock_user_repo):
        """Returns None when user does not exist (privacy-safe)"""
        # Arrange
        session = MagicMock()
        email = "nonexistent@example.com"

        mock_user_repo.get_by_email.return_value = None

        # Act
        result = PasswordManagementService.create_reset_token(session, email)

        # Assert
        assert result is None

    @patch("codemie.service.user.password_management_service.user_repository")
    def test_create_reset_token_inactive_user(self, mock_user_repo):
        """Returns None when user is inactive (privacy-safe)"""
        # Arrange
        session = MagicMock()
        email = "inactive@example.com"
        user_id = str(uuid4())

        mock_user = UserDB(
            id=user_id,
            username="inactiveuser",
            email=email,
            is_active=False,  # Inactive
            auth_source="local",
        )

        mock_user_repo.get_by_email.return_value = mock_user

        # Act
        result = PasswordManagementService.create_reset_token(session, email)

        # Assert
        assert result is None

    @patch("codemie.service.user.password_management_service.user_repository")
    def test_create_reset_token_non_local_user(self, mock_user_repo):
        """Returns None when user is IDP user (privacy-safe)"""
        # Arrange
        session = MagicMock()
        email = "idp@example.com"
        user_id = str(uuid4())

        mock_user = UserDB(
            id=user_id,
            username="idpuser",
            email=email,
            is_active=True,
            auth_source="keycloak",  # Non-local auth source
        )

        mock_user_repo.get_by_email.return_value = mock_user

        # Act
        result = PasswordManagementService.create_reset_token(session, email)

        # Assert
        assert result is None


class TestResetPassword:
    """Tests for reset_password() method"""

    @patch("codemie.service.user.password_management_service.email_token_repository")
    @patch("codemie.service.password_service.password_service")
    @patch("codemie.service.user.password_management_service.user_repository")
    @patch("codemie.service.user.password_management_service.config")
    def test_reset_password_success(self, mock_config, mock_user_repo, mock_pwd_service, mock_token_repo):
        """Successfully resets password with valid token"""
        # Arrange
        session = MagicMock()
        raw_token = "valid_token_xyz"
        new_password = "NewPassword789"
        new_hash = "$argon2id$v=19$m=65536,t=3,p=4$newhash"
        user_id = str(uuid4())

        mock_config.PASSWORD_MIN_LENGTH = 8

        mock_token_record = MagicMock(spec=EmailVerificationToken)
        mock_token_record.id = str(uuid4())
        mock_token_record.user_id = user_id
        mock_token_record.email = "user@example.com"
        mock_token_record.token_type = "password_reset"

        mock_token_repo.verify_token.return_value = mock_token_record
        mock_pwd_service.hash_password.return_value = new_hash

        # Act
        result = PasswordManagementService.reset_password(session, raw_token, new_password)

        # Assert
        assert result is True
        mock_token_repo.verify_token.assert_called_once_with(session, raw_token, "password_reset")
        mock_token_repo.mark_used.assert_called_once_with(session, mock_token_record.id)
        mock_pwd_service.hash_password.assert_called_once_with(new_password)
        mock_user_repo.update.assert_called_once_with(session, user_id, password_hash=new_hash)

    @patch("codemie.service.user.password_management_service.email_token_repository")
    def test_reset_password_invalid_token(self, mock_token_repo):
        """Raises 400 when token is invalid or expired"""
        # Arrange
        session = MagicMock()
        raw_token = "invalid_token"
        new_password = "NewPassword123"

        mock_token_repo.verify_token.return_value = None  # Invalid token

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            PasswordManagementService.reset_password(session, raw_token, new_password)

        assert exc_info.value.code == 400
        assert exc_info.value.message == "Invalid or expired token"

    @patch("codemie.service.user.password_management_service.email_token_repository")
    @patch("codemie.service.user.password_management_service.config")
    def test_reset_password_too_short(self, mock_config, mock_token_repo):
        """Raises 400 when new password is too short"""
        # Arrange
        session = MagicMock()
        raw_token = "valid_token"
        new_password = "short"  # Too short
        user_id = str(uuid4())

        mock_config.PASSWORD_MIN_LENGTH = 8

        mock_token_record = MagicMock(spec=EmailVerificationToken)
        mock_token_record.id = str(uuid4())
        mock_token_record.user_id = user_id
        mock_token_record.email = "user@example.com"
        mock_token_record.token_type = "password_reset"

        mock_token_repo.verify_token.return_value = mock_token_record

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            PasswordManagementService.reset_password(session, raw_token, new_password)

        assert exc_info.value.code == 400
        assert "Password must be at least 8 characters" in exc_info.value.message


class TestRequestPasswordResetFlow:
    """Tests for request_password_reset_flow() complete flow"""

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.email_service.email_service")
    @patch.object(PasswordManagementService, "create_reset_token", return_value="reset_token_abc")
    async def test_request_password_reset_flow_success(self, mock_create_token, mock_email_service, mock_get_session):
        """Sends password reset email when user exists and is eligible"""
        # Arrange
        email = "user@example.com"
        raw_token = "reset_token_abc"
        mock_session = MagicMock()

        # Mock context manager
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = None
        mock_email_service.send_password_reset_email = AsyncMock()

        # Act
        result = await PasswordManagementService.request_password_reset_flow(email)

        # Assert
        assert result == {"message": "If the email exists, a password reset link has been sent"}
        mock_create_token.assert_called_once_with(mock_session, email)
        mock_session.commit.assert_called_once()
        mock_email_service.send_password_reset_email.assert_called_once_with(email, raw_token)

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.email_service.email_service")
    @patch.object(PasswordManagementService, "create_reset_token", return_value=None)
    async def test_request_password_reset_flow_user_not_found(
        self, mock_create_token, mock_email_service, mock_get_session
    ):
        """Returns success message even when user not found (privacy-safe)"""
        # Arrange
        email = "nonexistent@example.com"
        mock_session = MagicMock()

        # Mock context manager
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = None
        mock_email_service.send_password_reset_email = AsyncMock()

        # Act
        result = await PasswordManagementService.request_password_reset_flow(email)

        # Assert
        assert result == {"message": "If the email exists, a password reset link has been sent"}
        mock_create_token.assert_called_once_with(mock_session, email)
        mock_session.commit.assert_called_once()
        # Email should NOT be sent when token is None
        mock_email_service.send_password_reset_email.assert_not_called()


class TestResetPasswordWithToken:
    """Tests for reset_password_with_token() flow"""

    @patch("codemie.clients.postgres.get_session")
    @patch.object(PasswordManagementService, "reset_password", return_value=True)
    def test_reset_password_with_token_delegates(self, mock_reset_password, mock_get_session):
        """Delegates to reset_password with session management"""
        # Arrange
        token = "reset_token_xyz"
        new_password = "NewPassword123"
        mock_session = MagicMock()

        # Mock context manager
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = None

        # Act
        result = PasswordManagementService.reset_password_with_token(token, new_password)

        # Assert
        assert result == {"message": "Password reset successfully"}
        mock_reset_password.assert_called_once_with(mock_session, token, new_password)
        mock_session.commit.assert_called_once()


class TestChangePasswordAuthenticated:
    """Tests for change_password_authenticated() flow"""

    @patch("codemie.clients.postgres.get_session")
    @patch.object(PasswordManagementService, "change_password", return_value=True)
    def test_change_password_authenticated_delegates(self, mock_change_password, mock_get_session):
        """Delegates to change_password with current password verification"""
        # Arrange
        user_id = str(uuid4())
        current_password = "OldPassword123"
        new_password = "NewPassword456"
        mock_session = MagicMock()

        # Mock context manager
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = None

        # Act
        result = PasswordManagementService.change_password_authenticated(user_id, current_password, new_password)

        # Assert
        assert result == {"message": "Password changed successfully"}
        mock_change_password.assert_called_once_with(
            mock_session, user_id, new_password, current_password=current_password
        )
        mock_session.commit.assert_called_once()


class TestAdminChangePasswordFlow:
    """Tests for admin_change_password_flow() method"""

    @patch("codemie.clients.postgres.get_session")
    @patch.object(PasswordManagementService, "change_password", return_value=True)
    def test_admin_change_password_flow_delegates(self, mock_change_password, mock_get_session):
        """Admin changes user password without current password"""
        # Arrange
        user_id = str(uuid4())
        new_password = "NewPassword789"
        actor_user_id = str(uuid4())
        mock_session = MagicMock()

        # Mock context manager
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = None

        # Act
        result = PasswordManagementService.admin_change_password_flow(user_id, new_password, actor_user_id)

        # Assert
        assert result == {"message": "Password changed successfully"}
        # Should call with current_password=None (admin override)
        mock_change_password.assert_called_once_with(mock_session, user_id, new_password, current_password=None)
        mock_session.commit.assert_called_once()


class TestPasswordManagementServiceSingleton:
    """Tests for the password_management_service singleton"""

    def test_singleton_instance_exists(self):
        """Verify singleton instance is properly initialized"""
        # Import to check instantiation
        from codemie.service.user.password_management_service import password_management_service

        # Assert
        assert password_management_service is not None
        assert isinstance(password_management_service, PasswordManagementService)
