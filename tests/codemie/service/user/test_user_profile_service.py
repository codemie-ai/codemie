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
from codemie.service.user.user_profile_service import UserProfileService, user_profile_service


class TestBuildProfileUpdates:
    """Test suite for _build_profile_updates helper method"""

    @patch("codemie.service.user.user_profile_service.UserProfileService._validate_email_uniqueness")
    @patch("codemie.service.user.user_profile_service.config")
    def test_build_profile_updates_name_only(self, mock_config, mock_validate_email):
        """Test building profile updates with only name change"""
        # Arrange
        mock_session = MagicMock()
        user_id = str(uuid4())
        db_user = UserDB(
            id=user_id,
            email="user@example.com",
            name="Old Name",
            username="user",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_super_admin=False,
        )
        name = "New Name"

        # Act
        updates, email_changed, new_email = UserProfileService._build_profile_updates(
            mock_session, user_id, db_user, name=name, picture=None, email=None
        )

        # Assert
        assert updates == {"name": "New Name"}
        assert email_changed is False
        assert new_email is None
        mock_validate_email.assert_not_called()

    @patch("codemie.service.user.user_profile_service.UserProfileService._validate_email_uniqueness")
    @patch("codemie.service.user.user_profile_service.config")
    def test_build_profile_updates_picture_only(self, mock_config, mock_validate_email):
        """Test building profile updates with only picture change"""
        # Arrange
        mock_session = MagicMock()
        user_id = str(uuid4())
        db_user = UserDB(
            id=user_id,
            email="user@example.com",
            name="User Name",
            username="user",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_super_admin=False,
        )
        picture = "https://example.com/new-picture.jpg"

        # Act
        updates, email_changed, new_email = UserProfileService._build_profile_updates(
            mock_session, user_id, db_user, name=None, picture=picture, email=None
        )

        # Assert
        assert updates == {"picture": "https://example.com/new-picture.jpg"}
        assert email_changed is False
        assert new_email is None
        mock_validate_email.assert_not_called()

    @patch("codemie.service.user.user_profile_service.UserProfileService._validate_email_uniqueness")
    @patch("codemie.service.user.user_profile_service.config")
    def test_build_profile_updates_email_change(self, mock_config, mock_validate_email):
        """Test building profile updates with email change - marks as unverified"""
        # Arrange
        mock_config.EMAIL_VERIFICATION_ENABLED = True
        mock_session = MagicMock()
        user_id = str(uuid4())
        db_user = UserDB(
            id=user_id,
            email="old@example.com",
            name="User Name",
            username="user",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_super_admin=False,
        )
        new_email = "new@example.com"

        # Act
        updates, email_changed, returned_email = UserProfileService._build_profile_updates(
            mock_session, user_id, db_user, name=None, picture=None, email=new_email
        )

        # Assert
        assert updates == {"email": "new@example.com", "email_verified": False}
        assert email_changed is True
        assert returned_email == "new@example.com"
        mock_validate_email.assert_called_once_with(mock_session, new_email, user_id)

    @patch("codemie.service.user.user_profile_service.UserProfileService._validate_email_uniqueness")
    @patch("codemie.service.user.user_profile_service.config")
    def test_build_profile_updates_email_same(self, mock_config, mock_validate_email):
        """Test building profile updates when email is same as current - no change"""
        # Arrange
        mock_config.EMAIL_VERIFICATION_ENABLED = True
        mock_session = MagicMock()
        user_id = str(uuid4())
        current_email = "user@example.com"
        db_user = UserDB(
            id=user_id,
            email=current_email,
            name="Old Name",
            username="user",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_super_admin=False,
        )
        name = "New Name"

        # Act
        updates, email_changed, new_email = UserProfileService._build_profile_updates(
            mock_session, user_id, db_user, name=name, picture=None, email=current_email
        )

        # Assert
        assert updates == {"name": "New Name"}
        assert email_changed is False
        assert new_email is None
        mock_validate_email.assert_not_called()

    @patch("codemie.service.user.user_profile_service.UserProfileService._validate_email_uniqueness")
    @patch("codemie.service.user.user_profile_service.config")
    def test_build_profile_updates_no_fields(self, mock_config, mock_validate_email):
        """Test building profile updates with no fields to update - raises 400"""
        # Arrange
        mock_session = MagicMock()
        user_id = str(uuid4())
        db_user = UserDB(
            id=user_id,
            email="user@example.com",
            name="User Name",
            username="user",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_super_admin=False,
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserProfileService._build_profile_updates(
                mock_session, user_id, db_user, name=None, picture=None, email=None
            )

        assert exc_info.value.code == 400
        assert "No fields to update" in exc_info.value.message

    @patch("codemie.service.user.user_profile_service.UserProfileService._validate_email_uniqueness")
    @patch("codemie.service.user.user_profile_service.config")
    def test_build_profile_updates_email_verification_disabled(self, mock_config, mock_validate_email):
        """Test email change when verification disabled - does not mark as unverified"""
        # Arrange
        mock_config.EMAIL_VERIFICATION_ENABLED = False
        mock_session = MagicMock()
        user_id = str(uuid4())
        db_user = UserDB(
            id=user_id,
            email="old@example.com",
            name="User Name",
            username="user",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_super_admin=False,
        )
        new_email = "new@example.com"

        # Act
        updates, email_changed, returned_email = UserProfileService._build_profile_updates(
            mock_session, user_id, db_user, name=None, picture=None, email=new_email
        )

        # Assert
        assert updates == {"email": "new@example.com"}
        assert "email_verified" not in updates
        assert email_changed is True
        assert returned_email == "new@example.com"
        mock_validate_email.assert_called_once_with(mock_session, new_email, user_id)


class TestValidateEmailUniqueness:
    """Test suite for _validate_email_uniqueness helper method"""

    @patch("codemie.service.user.user_profile_service.user_repository")
    def test_validate_email_uniqueness_available(self, mock_user_repo):
        """Test email validation when email is available"""
        # Arrange
        mock_session = MagicMock()
        email = "available@example.com"
        user_id = str(uuid4())
        mock_user_repo.get_by_email.return_value = None

        # Act
        UserProfileService._validate_email_uniqueness(mock_session, email, user_id)

        # Assert
        mock_user_repo.get_by_email.assert_called_once_with(mock_session, email)

    @patch("codemie.service.user.user_profile_service.user_repository")
    def test_validate_email_uniqueness_taken_by_other(self, mock_user_repo):
        """Test email validation when email is taken by another user - raises 409"""
        # Arrange
        mock_session = MagicMock()
        email = "taken@example.com"
        user_id = str(uuid4())
        other_user_id = str(uuid4())

        existing_user = UserDB(
            id=other_user_id,
            email=email,
            name="Other User",
            username="other",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_super_admin=False,
        )
        mock_user_repo.get_by_email.return_value = existing_user

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserProfileService._validate_email_uniqueness(mock_session, email, user_id)

        assert exc_info.value.code == 409
        assert "Email already in use" in exc_info.value.message

    @patch("codemie.service.user.user_profile_service.user_repository")
    def test_validate_email_uniqueness_same_user(self, mock_user_repo):
        """Test email validation when email belongs to same user - no error"""
        # Arrange
        mock_session = MagicMock()
        email = "user@example.com"
        user_id = str(uuid4())

        same_user = UserDB(
            id=user_id,
            email=email,
            name="User",
            username="user",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_super_admin=False,
        )
        mock_user_repo.get_by_email.return_value = same_user

        # Act
        UserProfileService._validate_email_uniqueness(mock_session, email, user_id)

        # Assert - no exception raised
        mock_user_repo.get_by_email.assert_called_once_with(mock_session, email)


class TestSendVerificationEmailSafe:
    """Test suite for _send_verification_email_safe helper method"""

    @pytest.mark.asyncio
    @patch("codemie.service.email_service.email_service")
    async def test_send_verification_email_safe_success(self, mock_email_service):
        """Test sending verification email successfully"""
        # Arrange
        email = "user@example.com"
        token = "verification-token-123"
        mock_email_service.send_verification_email = AsyncMock()

        # Act
        await UserProfileService._send_verification_email_safe(email, token)

        # Assert
        mock_email_service.send_verification_email.assert_called_once_with(email, token)

    @pytest.mark.asyncio
    @patch("codemie.service.user.user_profile_service.logger")
    @patch("codemie.service.email_service.email_service")
    async def test_send_verification_email_safe_failure(self, mock_email_service, mock_logger):
        """Test fail-safe pattern when email sending fails - swallows exception"""
        # Arrange
        email = "user@example.com"
        token = "verification-token-123"
        error_message = "SMTP connection failed"
        mock_email_service.send_verification_email = AsyncMock(side_effect=Exception(error_message))

        # Act - should not raise exception
        await UserProfileService._send_verification_email_safe(email, token)

        # Assert
        mock_email_service.send_verification_email.assert_called_once_with(email, token)
        mock_logger.warning.assert_called_once()
        warning_call = mock_logger.warning.call_args[0][0]
        assert "Failed to send verification email" in warning_call
        assert error_message in warning_call


class TestUpdateProfile:
    """Test suite for update_profile core method"""

    @pytest.mark.asyncio
    @patch("codemie.service.user.user_profile_service.UserProfileService._send_verification_email_safe")
    @patch("codemie.service.user.user_profile_service.config")
    @patch("codemie.service.user.user_profile_service.email_token_repository")
    @patch("codemie.service.user.user_profile_service.user_repository")
    @patch("codemie.clients.postgres.get_session")
    async def test_update_profile_name_change(
        self,
        mock_get_session,
        mock_user_repo,
        mock_email_token_repo,
        mock_config,
        mock_send_email,
    ):
        """Test updating profile with name change only"""
        # Arrange
        mock_config.EMAIL_VERIFICATION_ENABLED = True
        user_id = str(uuid4())
        old_name = "Old Name"
        new_name = "New Name"

        db_user = UserDB(
            id=user_id,
            email="user@example.com",
            name=old_name,
            username="user",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_super_admin=False,
        )

        updated_user = UserDB(
            id=user_id,
            email="user@example.com",
            name=new_name,
            username="user",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_super_admin=False,
        )

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user_repo.get_by_id.return_value = db_user
        mock_user_repo.get_by_email.return_value = None
        mock_user_repo.update.return_value = updated_user

        # Act
        result = await UserProfileService.update_profile(user_id, name=new_name)

        # Assert
        assert result.name == new_name
        mock_user_repo.update.assert_called_once_with(mock_session, user_id, name=new_name)
        mock_session.commit.assert_called_once()
        mock_send_email.assert_not_called()

    @pytest.mark.asyncio
    @patch("codemie.service.user.personal_project_service.personal_project_service")
    @patch("codemie.service.user.user_profile_service.UserProfileService._send_verification_email_safe")
    @patch("codemie.service.user.user_profile_service.config")
    @patch("codemie.service.user.user_profile_service.email_token_repository")
    @patch("codemie.service.user.user_profile_service.user_repository")
    @patch("codemie.clients.postgres.get_session")
    async def test_update_profile_email_change_with_verification(
        self,
        mock_get_session,
        mock_user_repo,
        mock_email_token_repo,
        mock_config,
        mock_send_email,
        mock_personal_project_service,
    ):
        """Test updating profile with email change - sends verification email and reconciles project"""
        # Arrange
        mock_config.EMAIL_VERIFICATION_ENABLED = True
        user_id = str(uuid4())
        old_email = "old@example.com"
        new_email = "new@example.com"
        verification_token = "token-123"

        db_user = UserDB(
            id=user_id,
            email=old_email,
            name="User Name",
            username="user",
            auth_source="local",
            email_verified=True,
            is_active=True,
            is_super_admin=False,
        )

        updated_user = UserDB(
            id=user_id,
            email=new_email,
            name="User Name",
            username="user",
            auth_source="local",
            email_verified=False,
            is_active=True,
            is_super_admin=False,
        )

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_user_repo.get_by_id.return_value = db_user
        mock_user_repo.get_by_email.return_value = None
        mock_user_repo.update.return_value = updated_user
        mock_email_token_repo.create_token.return_value = (verification_token, MagicMock())
        mock_personal_project_service.reconcile_personal_project_on_email_change = AsyncMock()

        # Act
        result = await UserProfileService.update_profile(user_id, email=new_email)

        # Assert
        assert result.email == new_email
        assert result.email_verified is False
        mock_user_repo.update.assert_called_once_with(mock_session, user_id, email=new_email, email_verified=False)
        mock_email_token_repo.create_token.assert_called_once_with(
            mock_session, user_id, new_email, "email_verification"
        )
        mock_session.commit.assert_called_once()
        mock_send_email.assert_called_once_with(new_email, verification_token)
        mock_personal_project_service.reconcile_personal_project_on_email_change.assert_called_once_with(
            user_id, old_email, new_email
        )

    @pytest.mark.asyncio
    @patch("codemie.service.user.user_profile_service.config")
    @patch("codemie.service.user.user_profile_service.user_repository")
    @patch("codemie.clients.postgres.get_session")
    async def test_update_profile_user_not_found(self, mock_get_session, mock_user_repo, mock_config):
        """Test update profile when user not found - raises 404"""
        # Arrange
        user_id = str(uuid4())
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = None

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await UserProfileService.update_profile(user_id, name="New Name")

        assert exc_info.value.code == 404
        assert "User not found" in exc_info.value.message


class TestServiceSingleton:
    """Test that the module exports a singleton instance"""

    def test_user_profile_service_singleton(self):
        """Test that user_profile_service singleton is available"""
        assert user_profile_service is not None
        assert isinstance(user_profile_service, UserProfileService)
