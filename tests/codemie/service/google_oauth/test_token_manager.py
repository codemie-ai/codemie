# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Unit tests for GoogleOAuthTokenManager."""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.google_oauth.token_manager import GoogleOAuthTokenManager
from codemie_tools.base.models import CredentialTypes


@pytest.fixture
def mock_encryption():
    """Mock encryption service."""
    mock_enc = MagicMock()
    # encrypt() should return bytes
    mock_enc.encrypt.side_effect = lambda x: f"encrypted_{x}".encode() if isinstance(x, str) else b"encrypted_" + x
    # decrypt() should accept bytes and return string
    mock_enc.decrypt.side_effect = lambda x: (
        x.decode().replace("encrypted_", "") if isinstance(x, bytes) else str(x).replace("encrypted_", "")
    )
    return mock_enc


@pytest.fixture
def token_manager(mock_encryption):
    """GoogleOAuthTokenManager instance with mocked encryption."""
    return GoogleOAuthTokenManager(encryption_service=mock_encryption)


class TestGetValidAccessToken:
    """Test get_valid_access_token() method."""

    @patch("codemie.service.google_oauth.token_manager.Settings")
    def test_returns_decrypted_token_when_not_expired(self, mock_settings_class, token_manager, mock_encryption):
        """Should return decrypted access token when not expired."""
        # Setup setting
        mock_setting = MagicMock()
        mock_setting.credential_type = CredentialTypes.GOOGLE_OAUTH
        mock_cred_access = MagicMock()
        mock_cred_access.key = "access_token"
        mock_cred_access.value = mock_encryption.encrypt("my_access_token")
        mock_cred_refresh = MagicMock()
        mock_cred_refresh.key = "refresh_token"
        mock_cred_refresh.value = mock_encryption.encrypt("my_refresh_token")
        mock_cred_expires = MagicMock()
        mock_cred_expires.key = "expires_at"
        mock_cred_expires.value = str(int(time.time()) + 3600)  # Expires in 1 hour
        mock_setting.credential_values = [mock_cred_access, mock_cred_refresh, mock_cred_expires]
        mock_settings_class.find_by_id.return_value = mock_setting

        token = token_manager.get_valid_access_token("setting_123")

        assert token == "my_access_token"

    @patch("codemie.service.google_oauth.token_manager.Settings")
    def test_raises_404_when_setting_missing(self, mock_settings_class, token_manager):
        """Should raise 404 when setting doesn't exist."""
        mock_settings_class.find_by_id.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            token_manager.get_valid_access_token("missing_setting")
        assert exc_info.value.code == 404

    @patch("codemie.service.google_oauth.token_manager.Settings")
    def test_raises_400_on_wrong_credential_type(self, mock_settings_class, token_manager):
        """Should raise 400 when credential type is not GOOGLE_OAUTH."""
        mock_setting = MagicMock()
        mock_setting.credential_type = CredentialTypes.JIRA
        mock_settings_class.find_by_id.return_value = mock_setting

        with pytest.raises(ExtendedHTTPException) as exc_info:
            token_manager.get_valid_access_token("setting_123")
        assert exc_info.value.code == 400
        assert "not a Google OAuth credential" in exc_info.value.message

    @patch("codemie.service.google_oauth.token_manager.Settings")
    def test_raises_400_when_access_token_missing(self, mock_settings_class, token_manager, mock_encryption):
        """Should raise 400 when access_token is missing."""
        mock_setting = MagicMock()
        mock_setting.id = "setting_123"
        mock_setting.credential_type = CredentialTypes.GOOGLE_OAUTH
        mock_cred_refresh = MagicMock()
        mock_cred_refresh.key = "refresh_token"
        mock_cred_refresh.value = mock_encryption.encrypt("my_refresh_token")
        mock_setting.credential_values = [mock_cred_refresh]
        mock_settings_class.find_by_id.return_value = mock_setting

        with pytest.raises(ExtendedHTTPException) as exc_info:
            token_manager.get_valid_access_token("setting_123")
        assert exc_info.value.code == 400
        assert "missing access_token" in exc_info.value.message

    @patch("codemie.service.google_oauth.token_manager.Settings")
    def test_raises_400_when_refresh_token_missing(self, mock_settings_class, token_manager, mock_encryption):
        """Should raise 400 when refresh_token is missing."""
        mock_setting = MagicMock()
        mock_setting.id = "setting_123"
        mock_setting.credential_type = CredentialTypes.GOOGLE_OAUTH
        mock_cred_access = MagicMock()
        mock_cred_access.key = "access_token"
        mock_cred_access.value = mock_encryption.encrypt("my_access_token")
        mock_cred_expires = MagicMock()
        mock_cred_expires.key = "expires_at"
        mock_cred_expires.value = str(int(time.time()) + 3600)
        mock_setting.credential_values = [mock_cred_access, mock_cred_expires]
        mock_settings_class.find_by_id.return_value = mock_setting

        with pytest.raises(ExtendedHTTPException) as exc_info:
            token_manager.get_valid_access_token("setting_123")
        assert exc_info.value.code == 400
        assert "missing refresh_token" in exc_info.value.message

    @patch("codemie.service.google_oauth.token_manager.Settings")
    @patch("codemie.service.google_oauth.token_manager.Credentials")
    @patch("codemie.service.google_oauth.token_manager.Request")
    def test_refreshes_expired_token(
        self, mock_request_class, mock_credentials_class, mock_settings_class, token_manager, mock_encryption
    ):
        """Should refresh token when within 5 minutes of expiry."""
        # Setup setting with expired token
        mock_setting = MagicMock()
        mock_setting.id = "setting_123"
        mock_setting.credential_type = CredentialTypes.GOOGLE_OAUTH
        mock_cred_access = MagicMock()
        mock_cred_access.key = "access_token"
        mock_cred_access.value = mock_encryption.encrypt("old_token")
        mock_cred_refresh = MagicMock()
        mock_cred_refresh.key = "refresh_token"
        mock_cred_refresh.value = mock_encryption.encrypt("refresh_token")
        mock_cred_expires = MagicMock()
        mock_cred_expires.key = "expires_at"
        mock_cred_expires.value = str(int(time.time()) + 60)  # Expires in 1 minute (within buffer)
        mock_setting.credential_values = [mock_cred_access, mock_cred_refresh, mock_cred_expires]
        mock_settings_class.find_by_id.return_value = mock_setting

        # Setup credentials refresh
        mock_credentials = MagicMock()
        mock_credentials.token = "new_access_token"
        mock_credentials.refresh_token = "refresh_token"
        mock_credentials.expiry = datetime.now(UTC) + timedelta(hours=1)
        mock_credentials_class.return_value = mock_credentials

        token_manager.get_valid_access_token("setting_123")

        # Should have called credentials.refresh
        mock_credentials.refresh.assert_called_once()
        # Should have updated the setting
        mock_setting.update.assert_called_once()

    @patch("codemie.service.google_oauth.token_manager.Settings")
    def test_handles_malformed_expires_at(self, mock_settings_class, token_manager, mock_encryption):
        """Should treat malformed expires_at as expired and trigger refresh."""
        mock_setting = MagicMock()
        mock_setting.id = "setting_123"
        mock_setting.credential_type = CredentialTypes.GOOGLE_OAUTH
        mock_cred_access = MagicMock()
        mock_cred_access.key = "access_token"
        mock_cred_access.value = mock_encryption.encrypt("my_token")
        mock_cred_refresh = MagicMock()
        mock_cred_refresh.key = "refresh_token"
        mock_cred_refresh.value = mock_encryption.encrypt("refresh_token")
        mock_cred_expires = MagicMock()
        mock_cred_expires.key = "expires_at"
        mock_cred_expires.value = "not_a_number"  # Malformed
        mock_setting.credential_values = [mock_cred_access, mock_cred_refresh, mock_cred_expires]
        mock_settings_class.find_by_id.return_value = mock_setting

        # Should trigger refresh since expires_at is malformed (treated as 0)
        with patch.object(
            token_manager, '_refresh_token', return_value=mock_encryption.encrypt("new_token")
        ) as mock_refresh:
            token_manager.get_valid_access_token("setting_123")
            mock_refresh.assert_called_once()


class TestRefreshToken:
    """Test _refresh_token() method."""

    @patch("codemie.service.google_oauth.token_manager.Credentials")
    @patch("codemie.service.google_oauth.token_manager.Request")
    def test_calls_credentials_api(self, mock_request_class, mock_credentials_class, token_manager, mock_encryption):
        """Should use Google Credentials API to refresh token."""
        mock_setting = MagicMock()
        mock_setting.id = "setting_123"
        mock_setting.credential_values = []

        encrypted_refresh = mock_encryption.encrypt("refresh_token")

        # Setup credentials
        mock_credentials = MagicMock()
        mock_credentials.token = "new_token"
        mock_credentials.refresh_token = "new_refresh_token"
        mock_credentials.expiry = datetime.now(UTC) + timedelta(hours=1)
        mock_credentials_class.return_value = mock_credentials

        token_manager._refresh_token(mock_setting, encrypted_refresh)

        # Should have built Credentials with refresh token
        mock_credentials_class.assert_called_once()
        # Should have called refresh
        mock_credentials.refresh.assert_called_once()

    @patch("codemie.service.google_oauth.token_manager.Credentials")
    @patch("codemie.service.google_oauth.token_manager.Request")
    def test_raises_400_on_invalid_grant(
        self, mock_request_class, mock_credentials_class, token_manager, mock_encryption
    ):
        """Should raise 400 when refresh token revoked (invalid_grant)."""
        mock_setting = MagicMock()
        mock_setting.id = "setting_123"
        encrypted_refresh = mock_encryption.encrypt("refresh_token")

        # Setup credentials to raise invalid_grant error
        mock_credentials = MagicMock()
        mock_credentials.refresh.side_effect = Exception("invalid_grant: Token has been revoked")
        mock_credentials_class.return_value = mock_credentials

        with pytest.raises(ExtendedHTTPException) as exc_info:
            token_manager._refresh_token(mock_setting, encrypted_refresh)
        assert exc_info.value.code == 400
        assert "revoked" in exc_info.value.message.lower()

    @patch("codemie.service.google_oauth.token_manager.Credentials")
    @patch("codemie.service.google_oauth.token_manager.Request")
    def test_raises_502_on_other_errors(
        self, mock_request_class, mock_credentials_class, token_manager, mock_encryption
    ):
        """Should raise 502 for non-invalid_grant errors."""
        mock_setting = MagicMock()
        mock_setting.id = "setting_123"
        encrypted_refresh = mock_encryption.encrypt("refresh_token")

        # Setup credentials to raise generic error
        mock_credentials = MagicMock()
        mock_credentials.refresh.side_effect = Exception("Network error")
        mock_credentials_class.return_value = mock_credentials

        with pytest.raises(ExtendedHTTPException) as exc_info:
            token_manager._refresh_token(mock_setting, encrypted_refresh)
        assert exc_info.value.code == 502

    @patch("codemie.service.google_oauth.token_manager.Credentials")
    @patch("codemie.service.google_oauth.token_manager.Request")
    def test_502_message_does_not_leak_raw_exception_text(
        self, mock_request_class, mock_credentials_class, token_manager, mock_encryption
    ):
        """502 message must stay static; raw provider error text must not reach the client."""
        mock_setting = MagicMock()
        mock_setting.id = "setting_123"
        encrypted_refresh = mock_encryption.encrypt("refresh_token")

        mock_credentials = MagicMock()
        mock_credentials.refresh.side_effect = Exception("secret internal detail: upstream connection refused")
        mock_credentials_class.return_value = mock_credentials

        with pytest.raises(ExtendedHTTPException) as exc_info:
            token_manager._refresh_token(mock_setting, encrypted_refresh)

        assert exc_info.value.code == 502
        assert "secret internal detail" not in exc_info.value.message

    @patch("codemie.service.google_oauth.token_manager.Credentials")
    @patch("codemie.service.google_oauth.token_manager.Request")
    def test_updates_setting_credentials(
        self, mock_request_class, mock_credentials_class, token_manager, mock_encryption
    ):
        """Should persist refreshed credentials back to Settings."""
        mock_setting = MagicMock()
        mock_setting.id = "setting_123"
        mock_cred_access = MagicMock()
        mock_cred_access.key = "access_token"
        mock_cred_expires = MagicMock()
        mock_cred_expires.key = "expires_at"
        mock_setting.credential_values = [mock_cred_access, mock_cred_expires]

        encrypted_refresh = mock_encryption.encrypt("refresh_token")

        # Setup credentials
        mock_credentials = MagicMock()
        mock_credentials.token = "new_token"
        mock_credentials.refresh_token = "new_refresh_token"
        mock_credentials.expiry = datetime.now(UTC) + timedelta(hours=1)
        mock_credentials_class.return_value = mock_credentials

        token_manager._refresh_token(mock_setting, encrypted_refresh)

        # Should have updated credential values
        mock_setting.update.assert_called_once()
        # access_token and expires_at should have been updated
        assert mock_cred_access.value != mock_encryption.encrypt("old_token")

    @patch("codemie.service.google_oauth.token_manager.Credentials")
    @patch("codemie.service.google_oauth.token_manager.Request")
    def test_updates_refresh_token_when_rotated(
        self, mock_request_class, mock_credentials_class, token_manager, mock_encryption
    ):
        """Should update refresh_token when Google rotates it."""
        mock_setting = MagicMock()
        mock_setting.id = "setting_123"
        mock_cred_access = MagicMock()
        mock_cred_access.key = "access_token"
        mock_cred_refresh = MagicMock()
        mock_cred_refresh.key = "refresh_token"
        mock_cred_refresh.value = mock_encryption.encrypt("old_refresh")
        mock_cred_expires = MagicMock()
        mock_cred_expires.key = "expires_at"
        mock_setting.credential_values = [mock_cred_access, mock_cred_refresh, mock_cred_expires]

        encrypted_refresh = mock_encryption.encrypt("old_refresh")

        # Setup credentials with new refresh token
        mock_credentials = MagicMock()
        mock_credentials.token = "new_token"
        mock_credentials.refresh_token = "new_refresh_token"  # Rotated
        mock_credentials.expiry = datetime.now(UTC) + timedelta(hours=1)
        mock_credentials_class.return_value = mock_credentials

        token_manager._refresh_token(mock_setting, encrypted_refresh)

        # Should have updated refresh_token
        mock_setting.update.assert_called_once()


class TestEnsureDatetimeAware:
    """Test _ensure_datetime_aware() helper."""

    def test_makes_naive_datetime_aware(self, token_manager):
        """Should add UTC timezone to naive datetime."""
        naive_dt = datetime(2026, 7, 3, 12, 0, 0)
        aware_dt = token_manager._ensure_datetime_aware(naive_dt)
        assert aware_dt.tzinfo == UTC

    def test_preserves_aware_datetime(self, token_manager):
        """Should not modify already-aware datetime."""
        aware_dt = datetime(2026, 7, 3, 12, 0, 0, tzinfo=UTC)
        result_dt = token_manager._ensure_datetime_aware(aware_dt)
        assert result_dt.tzinfo == UTC
        assert result_dt == aware_dt


class TestUpdateSettingCredentials:
    """Test _update_setting_credentials() helper."""

    def test_updates_existing_credential_keys(self, token_manager):
        """Should update values for existing credential keys."""
        mock_setting = MagicMock()
        mock_cred_access = MagicMock()
        mock_cred_access.key = "access_token"
        mock_cred_access.value = "old_value"
        mock_setting.credential_values = [mock_cred_access]

        token_manager._update_setting_credentials(mock_setting, access_token="new_value")

        assert mock_cred_access.value == "new_value"
        mock_setting.update.assert_called_once()

    def test_appends_new_credential_keys(self, token_manager):
        """Should append new credential keys that don't exist."""
        mock_setting = MagicMock()
        mock_setting.credential_values = []

        token_manager._update_setting_credentials(mock_setting, access_token="new_value", expires_at="12345")

        # Should have appended 2 new credentials
        assert len(mock_setting.credential_values) == 2
        mock_setting.update.assert_called_once()

    def test_skips_none_values(self, token_manager):
        """Should skip updating keys with None values."""
        mock_setting = MagicMock()
        mock_cred_access = MagicMock()
        mock_cred_access.key = "access_token"
        mock_cred_access.value = "old_value"
        mock_setting.credential_values = [mock_cred_access]

        token_manager._update_setting_credentials(mock_setting, access_token=None, expires_at="12345")

        # access_token should NOT be updated (None)
        assert mock_cred_access.value == "old_value"
        # expires_at should be appended (not None)
        assert len(mock_setting.credential_values) == 2
        mock_setting.update.assert_called_once()
