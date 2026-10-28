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

"""Google OAuth Token Manager - handles token lifecycle and refresh."""

import time
from datetime import UTC, datetime

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from sqlalchemy.orm import attributes

from codemie.configs import config, logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import CredentialValues, Settings
from codemie.service.encryption.encryption_factory import EncryptionFactory
from codemie.service.google_oauth.constants import TOKEN_REFRESH_BUFFER
from codemie_tools.base.models import CredentialTypes


class GoogleOAuthTokenManager:
    """Manages Google OAuth token lifecycle and refresh.

    Handles:
    - Token validation and refresh
    - Credential persistence to Settings
    - Expiry tracking with 5-minute buffer
    """

    def __init__(self, encryption_service=None):
        """Initialize token manager.

        Args:
            encryption_service: Optional encryption service (defaults to current encryption service).
        """
        self.encryption_service = encryption_service or EncryptionFactory().get_current_encryption_service()

    @staticmethod
    def _ensure_datetime_aware(dt: datetime) -> datetime:
        """Ensure datetime is timezone-aware (UTC).

        Google Credentials API returns timezone-naive datetimes in UTC.
        This helper makes them timezone-aware to avoid datetime arithmetic errors.

        Args:
            dt: Datetime that may be naive or aware.

        Returns:
            Timezone-aware datetime in UTC.
        """
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt

    def get_valid_access_token(self, setting_id: str) -> str:
        """Return non-expired Google OAuth access token for given setting.

        Loads Settings record, verifies credential type, and refreshes token
        when within 5 minutes of expiry.

        Args:
            setting_id: Primary key of Settings row.

        Returns:
            Plaintext (decrypted) access token ready to use as Bearer.

        Raises:
            ExtendedHTTPException: 404 if setting doesn't exist.
            ExtendedHTTPException: 400 if credential type is not GOOGLE_OAUTH.
            ExtendedHTTPException: 502 if token refresh fails.
        """
        # Load and validate setting
        setting = Settings.find_by_id(setting_id)
        if setting is None:
            raise ExtendedHTTPException(404, f"Setting '{setting_id}' not found")

        if setting.credential_type != CredentialTypes.GOOGLE_OAUTH:
            raise ExtendedHTTPException(
                400,
                f"Setting '{setting_id}' is not a Google OAuth credential (type: {setting.credential_type})",
            )

        creds = {cred.key: cred.value for cred in setting.credential_values}
        encrypted_access_token = creds.get("access_token", "")
        encrypted_refresh_token = creds.get("refresh_token", "")

        expires_at_raw = creds.get("expires_at", 0) or 0
        try:
            expires_at = int(expires_at_raw)
        except (ValueError, TypeError):
            logger.warning(
                f"Google OAuth: malformed expires_at '{expires_at_raw}' for setting {setting_id}, treating as expired"
            )
            expires_at = 0

        if not encrypted_access_token or not encrypted_refresh_token:
            missing_token = 'access_token' if not encrypted_access_token else 'refresh_token'
            raise ExtendedHTTPException(
                400,
                f"Google OAuth integration is missing {missing_token}. Please re-authenticate your Google account.",
            )

        if time.time() + TOKEN_REFRESH_BUFFER >= expires_at:
            logger.info(f"Google OAuth: access token for setting {setting_id} needs refresh")
            encrypted_access_token = self._refresh_token(setting, encrypted_refresh_token)

        return self.encryption_service.decrypt(encrypted_access_token)

    def _refresh_token(self, setting: Settings, encrypted_refresh_token: str) -> str:
        """Refresh expired Google OAuth access token using Credentials API.

        Decrypts refresh token, calls Credentials.refresh(), encrypts new access token,
        and persists updated credentials back to Settings row.

        Args:
            setting: Live Settings ORM object to update in-place.
            encrypted_refresh_token: Encrypted refresh token from credential_values.

        Returns:
            Encrypted new access token (ready to store/decrypt).

        Raises:
            ExtendedHTTPException: 400 if refresh token revoked (invalid_grant).
            ExtendedHTTPException: 502 for other token refresh failures.
        """
        # Decrypt refresh token
        try:
            refresh_token = self.encryption_service.decrypt(encrypted_refresh_token)
        except Exception as exc:
            logger.error(f"Google OAuth: failed to decrypt refresh_token for setting {setting.id}: {exc}")
            raise ExtendedHTTPException(502, "Failed to decrypt refresh token")

        # Refresh using Google Credentials API
        try:
            credentials = Credentials(
                token=None,  # No current access token
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=config.GOOGLE_OAUTH_CLIENT_ID,
                client_secret=config.GOOGLE_OAUTH_CLIENT_SECRET,
            )

            request = Request()
            credentials.refresh(request)

            # Extract refreshed token data
            new_access_token = credentials.token
            new_refresh_token = credentials.refresh_token  # May be rotated

            # Calculate expires_at (credentials.expiry is timezone-naive UTC)
            if credentials.expiry:
                expiry = self._ensure_datetime_aware(credentials.expiry)
                new_expires_at = int(expiry.timestamp())
            else:
                new_expires_at = int(time.time()) + 3600

        except HttpError as exc:
            status_code = exc.resp.status
            error_details = exc.error_details if hasattr(exc, 'error_details') else str(exc)
            logger.error(
                f"Google OAuth: HTTP {status_code} during token refresh for setting {setting.id}. "
                f"ErrorDetails={error_details}"
            )

            if "invalid_grant" in str(exc).lower():
                logger.warning(
                    f"Google OAuth: invalid_grant for setting {setting.id} — "
                    "refresh token revoked; user must re-authorise"
                )
                raise ExtendedHTTPException(400, "Google OAuth token has been revoked. Please re-authenticate.")

            raise ExtendedHTTPException(502, "Google OAuth token service failed to process credentials.")
        except Exception as exc:
            error_msg = str(exc)
            logger.error(f"Google OAuth: token refresh failed for setting {setting.id}: {exc}")

            if "invalid_grant" in error_msg.lower():
                logger.warning(
                    f"Google OAuth: invalid_grant for setting {setting.id} — "
                    "refresh token revoked; user must re-authorise"
                )
                raise ExtendedHTTPException(400, "Google OAuth token has been revoked. Please re-authenticate.")

            raise ExtendedHTTPException(502, "Failed to refresh Google OAuth token.")

        # Encrypt new tokens
        encrypted_new_access_token = self.encryption_service.encrypt(new_access_token)
        encrypted_new_refresh_token = (
            self.encryption_service.encrypt(new_refresh_token)
            if new_refresh_token and new_refresh_token != refresh_token
            else None
        )

        # Update the current setting
        self._update_setting_credentials(
            setting,
            access_token=encrypted_new_access_token,
            expires_at=str(new_expires_at),
            refresh_token=encrypted_new_refresh_token,
        )

        # Update all other integrations that share the same refresh_token
        self._update_shared_token_integrations(
            user_id=setting.user_id,
            email=self._get_email_from_setting(setting),
            current_setting_id=setting.id,
            encrypted_refresh_token=encrypted_refresh_token,
            new_access_token=encrypted_new_access_token,
            new_expires_at=str(new_expires_at),
            new_refresh_token=encrypted_new_refresh_token,
        )

        logger.info(f"Google OAuth: access token refreshed for setting {setting.id}")
        return encrypted_new_access_token

    def _update_setting_credentials(self, setting: Settings, **updates) -> None:
        """Update credential_values in Settings row.

        Args:
            setting: Live Settings ORM object.
            **updates: Key-value pairs to update (None values are skipped).
        """
        existing_dict = {cred.key: cred for cred in setting.credential_values}
        for key, value in updates.items():
            if value is None:
                continue
            if key in existing_dict:
                existing_dict[key].value = value
                attributes.flag_modified(setting, "credential_values")
            else:
                setting.credential_values.append(CredentialValues(key=key, value=value))
        setting.update()

    def _get_email_from_setting(self, setting: Settings) -> str | None:
        """Extract email from credential_values.

        Args:
            setting: Settings ORM object.

        Returns:
            Email string, or None if not found.
        """
        for cred in setting.credential_values:
            if cred.key == "email":
                return cred.value
        return None

    def _update_shared_token_integrations(
        self,
        user_id: str,
        email: str | None,
        current_setting_id: str,
        encrypted_refresh_token: str,
        new_access_token: str,
        new_expires_at: str,
        new_refresh_token: str | None,
    ) -> None:
        """Update all other GoogleOAuth integrations that share the same refresh_token.

        When a user has multiple integrations for the same Google account (via token reuse),
        refreshing the token in one should update all of them to avoid stale tokens.

        Args:
            user_id: User ID to search settings for.
            email: Email address to match (if None, skip update).
            current_setting_id: ID of the setting that was just updated (skip this one).
            encrypted_refresh_token: The encrypted refresh_token that was just used.
            new_access_token: Encrypted new access token.
            new_expires_at: New expiry timestamp as string.
            new_refresh_token: Encrypted new refresh token if rotated, else None.
        """
        if not email:
            return

        try:
            from codemie.rest_api.models.settings import Settings
            from codemie_tools.base.models import CredentialTypes

            # Find all GoogleOAuth settings for this user with the same email (raw from DB, not masked)
            google_settings = [
                s
                for s in Settings.get_by_user_id(user_id, credential_type=CredentialTypes.GOOGLE_OAUTH)
                if s.id != current_setting_id
            ]

            for setting in google_settings:
                # Check if email matches
                setting_email = next((c.value for c in setting.credential_values if c.key == "email"), None)
                if setting_email != email:
                    continue

                # Check if refresh_token matches (same shared token)
                setting_refresh = next((c.value for c in setting.credential_values if c.key == "refresh_token"), None)
                if setting_refresh != encrypted_refresh_token:
                    continue

                # Update this shared integration
                updates = {
                    "access_token": new_access_token,
                    "expires_at": new_expires_at,
                }
                if new_refresh_token:
                    updates["refresh_token"] = new_refresh_token

                self._update_setting_credentials(setting, **updates)
                logger.info(
                    f"Google OAuth: updated shared integration {setting.id} "
                    f"(same refresh_token as {current_setting_id})"
                )

        except Exception as exc:
            # Don't fail the refresh if shared update fails - just log it
            logger.warning(f"Google OAuth: failed to update shared integrations for {current_setting_id}: {exc}")

    def _has_other_integrations_with_same_token(
        self, user_id: str, email: str, encrypted_refresh_token: str, exclude_setting_id: str
    ) -> bool:
        """Check if other integrations share the same refresh_token.

        Args:
            user_id: User ID to search.
            email: Email to match.
            encrypted_refresh_token: The encrypted refresh_token to compare.
            exclude_setting_id: Setting ID to exclude from the search.

        Returns:
            True if at least one other integration shares the same token.
        """
        try:
            from codemie.rest_api.models.settings import Settings
            from codemie_tools.base.models import CredentialTypes

            # Get raw settings from DB (not masked)
            google_settings = [
                s
                for s in Settings.get_by_user_id(user_id, credential_type=CredentialTypes.GOOGLE_OAUTH)
                if s.id != exclude_setting_id
            ]

            for setting in google_settings:
                setting_email = next((c.value for c in setting.credential_values if c.key == "email"), None)
                if setting_email != email:
                    continue

                setting_refresh = next((c.value for c in setting.credential_values if c.key == "refresh_token"), None)
                if setting_refresh == encrypted_refresh_token:
                    return True

            return False
        except Exception as exc:
            logger.warning(f"Google OAuth: failed to check for shared integrations: {exc}")
            return False

    def revoke_token_from_credentials(
        self,
        user_id: str,
        setting_id: str,
        email: str,
        encrypted_refresh_token: str,
    ) -> None:
        """Revoke Google OAuth token from explicit credentials.

        Used when revoking credentials that are being replaced (not deleted).
        Only revokes with Google if this is the LAST integration using this token.

        Args:
            user_id: User ID for ownership check.
            setting_id: Setting ID (for exclusion check and logging).
            email: Email associated with the token.
            encrypted_refresh_token: Encrypted refresh token to revoke.
        """
        if not encrypted_refresh_token:
            logger.info(f"Google OAuth: no refresh token to revoke for setting {setting_id}")
            return

        # Check if other integrations share this refresh_token
        # Only revoke with Google if this is the LAST integration using this token
        if email and self._has_other_integrations_with_same_token(user_id, email, encrypted_refresh_token, setting_id):
            logger.info(
                f"Google OAuth: skipping revocation for setting {setting_id} - "
                f"other integrations for {email} share the same refresh_token"
            )
            return

        # Decrypt and revoke
        try:
            refresh_token = self.encryption_service.decrypt(encrypted_refresh_token)
            response = requests.post(
                "https://oauth2.googleapis.com/revoke",
                data={"token": refresh_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code == 200:
                logger.info(f"Google OAuth: successfully revoked token for setting {setting_id}")
            else:
                logger.warning(
                    f"Google OAuth: token revocation returned HTTP {response.status_code} for setting {setting_id}"
                )
        except Exception as exc:
            logger.warning(f"Google OAuth: failed to revoke token for setting {setting_id}: {exc}")

    def revoke_token(self, setting_id: str) -> None:
        """Revoke Google OAuth tokens by calling Google's revocation endpoint.

        Loads the setting from database and revokes its current credentials.
        Use this for deletion. For updates, use revoke_token_from_credentials().

        Args:
            setting_id: Primary key of Settings row.

        Raises:
            ExtendedHTTPException: 404 if setting doesn't exist.
            ExtendedHTTPException: 400 if credential type is not GOOGLE_OAUTH.
        """
        # Load and validate setting
        setting = Settings.find_by_id(setting_id)
        if setting is None:
            raise ExtendedHTTPException(404, f"Setting '{setting_id}' not found")

        if setting.credential_type != CredentialTypes.GOOGLE_OAUTH:
            raise ExtendedHTTPException(
                400,
                f"Setting '{setting_id}' is not a Google OAuth credential (type: {setting.credential_type})",
            )

        # Extract refresh token and email
        creds = {cred.key: cred.value for cred in setting.credential_values}
        encrypted_refresh_token = creds.get("refresh_token", "")
        email = creds.get("email", "")

        # Delegate to the core revocation logic
        self.revoke_token_from_credentials(setting.user_id, setting_id, email, encrypted_refresh_token)
