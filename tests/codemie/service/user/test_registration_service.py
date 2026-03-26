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

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_management import UserDB
from codemie.service.user.registration_service import RegistrationService, registration_service


class TestRegistrationService:
    """Test suite for RegistrationService - user registration and verification"""

    @patch("codemie.service.user.registration_service.user_repository")
    @patch("codemie.service.password_service.password_service")
    @patch("codemie.service.user.registration_service.config")
    def test_register_user_success(self, mock_config, mock_password_service, mock_user_repo):
        """Test successful user registration"""
        # Arrange
        mock_config.PASSWORD_MIN_LENGTH = 8
        mock_config.EMAIL_VERIFICATION_ENABLED = True
        mock_config.USER_PROJECT_LIMIT = 10

        mock_session = MagicMock()
        email = "newuser@example.com"
        username = "newuser"
        password = "securePassword123"
        name = "New User"

        mock_user_repo.exists_by_email.return_value = False
        mock_user_repo.exists_by_username.return_value = False
        mock_password_service.hash_password.return_value = "hashed_password"

        expected_user = UserDB(
            id=str(uuid4()),
            email=email,
            username=username,
            name=name,
            password_hash="hashed_password",
            auth_source="local",
            email_verified=False,
            is_active=True,
            is_admin=False,
            project_limit=10,
        )
        mock_user_repo.create.return_value = expected_user

        # Act
        result = RegistrationService.register_user(mock_session, email, username, password, name)

        # Assert
        assert result == expected_user
        mock_password_service.hash_password.assert_called_once_with(password)
        mock_user_repo.create.assert_called_once()

    @patch("codemie.service.user.registration_service.user_repository")
    @patch("codemie.service.user.registration_service.config")
    def test_register_user_password_too_short(self, mock_config, mock_user_repo):
        """Test registration fails with password too short"""
        # Arrange
        mock_config.PASSWORD_MIN_LENGTH = 8
        mock_session = MagicMock()

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            RegistrationService.register_user(mock_session, "user@example.com", "user", "short", "User")

        assert exc_info.value.code == 400
        assert "at least 8 characters" in exc_info.value.message

    @patch("codemie.service.user.registration_service.user_repository")
    @patch("codemie.service.user.registration_service.config")
    def test_register_user_email_exists(self, mock_config, mock_user_repo):
        """Test registration fails when email already exists"""
        # Arrange
        mock_config.PASSWORD_MIN_LENGTH = 8
        mock_session = MagicMock()
        mock_user_repo.exists_by_email.return_value = True

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            RegistrationService.register_user(mock_session, "existing@example.com", "newuser", "password123", "User")

        assert exc_info.value.code == 409
        assert "Email already registered" in exc_info.value.message

    @patch("codemie.service.user.registration_service.user_repository")
    @patch("codemie.service.user.registration_service.config")
    def test_register_user_username_exists(self, mock_config, mock_user_repo):
        """Test registration fails when username already taken"""
        # Arrange
        mock_config.PASSWORD_MIN_LENGTH = 8
        mock_session = MagicMock()
        mock_user_repo.exists_by_email.return_value = False
        mock_user_repo.exists_by_username.return_value = True

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            RegistrationService.register_user(mock_session, "user@example.com", "existinguser", "password123", "User")

        assert exc_info.value.code == 409
        assert "Username already taken" in exc_info.value.message

    @patch("codemie.service.user.registration_service.user_repository")
    @patch("codemie.service.password_service.password_service")
    @patch("codemie.service.user.registration_service.config")
    def test_register_user_email_verification_disabled(self, mock_config, mock_password_service, mock_user_repo):
        """Test user registered with email_verified=True when verification disabled"""
        # Arrange
        mock_config.PASSWORD_MIN_LENGTH = 8
        mock_config.EMAIL_VERIFICATION_ENABLED = False
        mock_config.USER_PROJECT_LIMIT = 10

        mock_session = MagicMock()
        mock_user_repo.exists_by_email.return_value = False
        mock_user_repo.exists_by_username.return_value = False
        mock_password_service.hash_password.return_value = "hashed"

        created_user = UserDB(
            id=str(uuid4()),
            email="user@example.com",
            username="user",
            name="User",
            password_hash="hashed",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_admin=False,
            project_limit=10,
        )
        mock_user_repo.create.return_value = created_user

        # Act
        RegistrationService.register_user(mock_session, "user@example.com", "user", "password123")

        # Assert
        # Check that the user created has email_verified=True
        call_args = mock_user_repo.create.call_args[0][1]
        assert call_args.email_verified is True

    @patch("codemie.service.user.registration_service.email_token_repository")
    @patch("codemie.service.user.registration_service.user_repository")
    def test_verify_email_success(self, mock_user_repo, mock_token_repo):
        """Test successful email verification"""
        # Arrange
        mock_session = MagicMock()
        raw_token = "verification-token-123"
        user_id = str(uuid4())

        mock_token_record = MagicMock()
        mock_token_record.id = "token-id-123"
        mock_token_record.user_id = user_id
        mock_token_repo.verify_token.return_value = mock_token_record

        verified_user = UserDB(
            id=user_id,
            email="user@example.com",
            username="user",
            name="User",
            password_hash="hash",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_admin=False,
            project_limit=10,
        )
        mock_user_repo.update.return_value = verified_user

        # Act
        result = RegistrationService.verify_email(mock_session, raw_token)

        # Assert
        assert result == verified_user
        mock_token_repo.verify_token.assert_called_once_with(mock_session, raw_token, "email_verification")
        mock_token_repo.mark_used.assert_called_once_with(mock_session, "token-id-123")
        mock_user_repo.update.assert_called_once_with(mock_session, user_id, email_verified=True)

    @patch("codemie.service.user.registration_service.email_token_repository")
    def test_verify_email_invalid_token(self, mock_token_repo):
        """Test verification fails with invalid/expired token"""
        # Arrange
        mock_session = MagicMock()
        raw_token = "invalid-token"
        mock_token_repo.verify_token.return_value = None

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            RegistrationService.verify_email(mock_session, raw_token)

        assert exc_info.value.code == 400
        assert "Invalid or expired token" in exc_info.value.message

    @patch("codemie.service.user.registration_service.email_token_repository")
    @patch("codemie.service.user.registration_service.user_repository")
    def test_verify_email_user_not_found(self, mock_user_repo, mock_token_repo):
        """Test verification fails when user not found"""
        # Arrange
        mock_session = MagicMock()
        raw_token = "token-with-deleted-user"

        mock_token_record = MagicMock()
        mock_token_record.id = "token-id"
        mock_token_record.user_id = str(uuid4())
        mock_token_repo.verify_token.return_value = mock_token_record
        mock_user_repo.update.return_value = None  # User not found

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            RegistrationService.verify_email(mock_session, raw_token)

        assert exc_info.value.code == 404
        assert "User not found" in exc_info.value.message

    @pytest.mark.asyncio
    @patch("codemie.service.user.registration_service.personal_project_service")
    @patch("codemie.service.email_service.email_service")
    @patch("codemie.service.user.registration_service.email_token_repository")
    @patch("codemie.service.user.registration_service.user_repository")
    @patch("codemie.service.password_service.password_service")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.registration_service.config")
    async def test_register_user_with_flow_email_verification(
        self,
        mock_config,
        mock_get_session,
        mock_password_service,
        mock_user_repo,
        mock_token_repo,
        mock_email_service,
        mock_personal_project_service,
    ):
        """Test registration flow with email verification enabled"""
        # Arrange
        mock_config.PASSWORD_MIN_LENGTH = 8
        mock_config.EMAIL_VERIFICATION_ENABLED = True
        mock_config.USER_PROJECT_LIMIT = 10

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        email = "newuser@example.com"
        username = "newuser"
        password = "password123"
        user_id = str(uuid4())

        mock_user_repo.exists_by_email.return_value = False
        mock_user_repo.exists_by_username.return_value = False
        mock_password_service.hash_password.return_value = "hashed"

        registered_user = UserDB(
            id=user_id,
            email=email,
            username=username,
            name=username,
            password_hash="hashed",
            auth_source="local",
            email_verified=False,
            is_active=True,
            is_admin=False,
            project_limit=10,
        )
        mock_user_repo.create.return_value = registered_user

        raw_token = "verification-token"
        mock_token_repo.create_token.return_value = (raw_token, MagicMock())
        mock_email_service.send_verification_email = AsyncMock()
        mock_personal_project_service.ensure_personal_project_async = AsyncMock()

        # Act
        result = await RegistrationService.register_user_with_flow(email, username, password)

        # Assert
        assert result["type"] == "message"
        assert "check your email" in result["message"].lower()
        mock_email_service.send_verification_email.assert_called_once_with(email, raw_token)
        mock_session.commit.assert_called_once()
        mock_personal_project_service.ensure_personal_project_async.assert_called_once_with(user_id, email)

    @pytest.mark.asyncio
    @patch("codemie.service.user.registration_service.personal_project_service")
    @patch("codemie.service.email_service.email_service")
    @patch("codemie.service.user.registration_service.email_token_repository")
    @patch("codemie.service.user.registration_service.user_repository")
    @patch("codemie.service.password_service.password_service")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.registration_service.config")
    async def test_register_user_with_flow_email_send_failure(
        self,
        mock_config,
        mock_get_session,
        mock_password_service,
        mock_user_repo,
        mock_token_repo,
        mock_email_service,
        mock_personal_project_service,
    ):
        """Test registration flow fails when email send fails"""
        # Arrange
        mock_config.PASSWORD_MIN_LENGTH = 8
        mock_config.EMAIL_VERIFICATION_ENABLED = True
        mock_config.USER_PROJECT_LIMIT = 10

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user_repo.exists_by_email.return_value = False
        mock_user_repo.exists_by_username.return_value = False
        mock_password_service.hash_password.return_value = "hashed"

        registered_user = UserDB(
            id=str(uuid4()),
            email="user@example.com",
            username="user",
            name="user",
            password_hash="hashed",
            auth_source="local",
            email_verified=False,
            is_active=True,
            is_admin=False,
            project_limit=10,
        )
        mock_user_repo.create.return_value = registered_user

        mock_token_repo.create_token.return_value = ("token", MagicMock())
        mock_email_service.send_verification_email = AsyncMock(side_effect=Exception("SMTP error"))

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await RegistrationService.register_user_with_flow("user@example.com", "user", "password123")

        assert exc_info.value.code == 500
        assert "Failed to send verification email" in exc_info.value.message
        # Session should not have been committed
        assert not mock_session.commit.called

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.rest_api.security.jwt_local.generate_access_token")
    @patch("codemie.service.user.registration_service.personal_project_service")
    @patch("codemie.service.user.registration_service.user_repository")
    @patch("codemie.service.password_service.password_service")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.registration_service.config")
    async def test_register_user_with_flow_instant_login(
        self,
        mock_config,
        mock_get_session,
        mock_password_service,
        mock_user_repo,
        mock_personal_project_service,
        mock_generate_token,
        mock_user_project_repo,
        mock_get_async_session,
    ):
        """Test registration flow with instant login (email verification disabled)"""
        # Arrange
        mock_config.PASSWORD_MIN_LENGTH = 8
        mock_config.EMAIL_VERIFICATION_ENABLED = False
        mock_config.USER_PROJECT_LIMIT = 10

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        email = "instant@example.com"
        username = "instant"
        password = "password123"
        user_id = str(uuid4())

        mock_user_repo.exists_by_email.return_value = False
        mock_user_repo.exists_by_username.return_value = False
        mock_password_service.hash_password.return_value = "hashed"

        registered_user = UserDB(
            id=user_id,
            email=email,
            username=username,
            name=username,
            password_hash="hashed",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_admin=False,
            project_limit=10,
        )
        mock_user_repo.create.return_value = registered_user

        mock_generate_token.return_value = "jwt-token-123"
        mock_personal_project_service.ensure_personal_project_async = AsyncMock()

        # Mock async session context manager
        mock_async_session = AsyncMock()
        cm = AsyncMock()
        cm.__aenter__.return_value = mock_async_session
        cm.__aexit__.return_value = False
        mock_get_async_session.return_value = cm

        mock_user_project_repo.aget_by_user_id = AsyncMock(return_value=[])

        # Act
        result = await RegistrationService.register_user_with_flow(email, username, password)

        # Assert
        assert result["type"] == "token"
        assert result["access_token"] == "jwt-token-123"
        assert result["user"].email == email
        mock_session.commit.assert_called_once()
        mock_personal_project_service.ensure_personal_project_async.assert_called_once_with(user_id, email)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.jwt_local.generate_access_token")
    @patch("codemie.service.user.registration_service.email_token_repository")
    @patch("codemie.service.user.registration_service.user_repository")
    @patch("codemie.clients.postgres.get_session")
    async def test_verify_email_and_login_success(
        self, mock_get_session, mock_user_repo, mock_token_repo, mock_generate_token
    ):
        """Test email verification with login token generation"""
        # Arrange
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        raw_token = "verify-token"
        user_id = str(uuid4())
        email = "verify@example.com"

        mock_token_record = MagicMock()
        mock_token_record.id = "token-id"
        mock_token_record.user_id = user_id
        mock_token_repo.verify_token.return_value = mock_token_record

        verified_user = UserDB(
            id=user_id,
            email=email,
            username="verify",
            name="Verify User",
            password_hash="hash",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_admin=False,
            project_limit=10,
        )
        mock_user_repo.update.return_value = verified_user

        mock_generate_token.return_value = "access-token-456"

        # Act
        result = RegistrationService.verify_email_and_login(raw_token)

        # Assert
        assert result["message"] == "Email verified successfully"
        assert result["access_token"] == "access-token-456"
        mock_session.commit.assert_called_once()
        mock_generate_token.assert_called_once_with(user_id, email, "local")


class TestRegistrationServiceSingleton:
    """Test the registration_service singleton instance"""

    def test_singleton_instance_exists(self):
        """Test that registration_service singleton is properly initialized"""
        assert registration_service is not None
        assert isinstance(registration_service, RegistrationService)
