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

"""Bridge between GitLab OAuth flow and TMS-backed per-user token storage."""

import time
from collections.abc import Callable

from codemie.configs import logger
from codemie.rest_api.models.settings import CredentialValues
from codemie.service.gitlab_oauth.constants import ensure_instance_allowed, token_url
from codemie.service.oauth.errors import (
    OAuthIntegrationConfigError,
    OAuthIntegrationNotFoundError,
    OAuthTokenDataError,
    OAuthTokenPersistenceError,
)
from codemie.service.oauth.settings_base import ToolOAuthSettingsService
from codemie.service.oauth.token_port import ToolOAuthTokenPort
from codemie_enterprise.mcp_auth import OAuth2RefreshMetadata


class GitLabOAuthSettingsService(ToolOAuthSettingsService):
    """Extract credentials from a completed GitLab OAuth flow and manage lifecycle in Settings."""

    provider_label = "GitLab"
    _APP_KEYS = frozenset({"instance_url", "client_id", "client_secret"})

    @staticmethod
    def _get_credential_value(credentials: list[CredentialValues], key: str) -> str | None:
        return next((c.value for c in credentials if c.key == key), None)

    def _recover_refresh_token(
        self, existing_credentials: list[CredentialValues], username: str, instance_url: str
    ) -> str | None:
        """Return the decrypted refresh_token from a matching existing setting, else None.

        GitLab occasionally omits the refresh_token on re-consent; when the existing setting
        targets the same instance + username we can safely reuse its stored refresh_token.
        """
        existing_username = self._get_credential_value(existing_credentials, "username")
        existing_instance = self._get_credential_value(existing_credentials, "instance_url")
        if not (
            existing_username
            and existing_instance
            and username
            and instance_url
            and existing_username == username
            and existing_instance == instance_url
        ):
            return None
        encrypted_refresh = self._get_credential_value(existing_credentials, "refresh_token")
        if not encrypted_refresh:
            return None
        return self.encryption_service.decrypt(encrypted_refresh)

    def _vault_refresh_token(self, user_id: str, setting_id: str, username: str, instance_url: str) -> str | None:
        """Return the user's stored refresh_token when it belongs to the same GitLab identity.

        Per-user tokens live in the enterprise token vault, not on the Settings row, so this is
        where a re-consent that omits `refresh_token` can recover one. Reuse is gated on the
        stored token pointing at the same instance and the same GitLab account, otherwise the
        recovered token would refresh a different identity's session.
        """
        if not username or not instance_url:
            return None
        try:
            stored = ToolOAuthTokenPort.get_oauth2_token_or_none(user_id=user_id, integration_id=setting_id)
        except Exception as exc:
            logger.warning(f"GitLab OAuth: refresh token recovery lookup failed for setting {setting_id}: {exc}")
            return None
        if stored is None or not stored.refresh_token:
            return None
        metadata = getattr(stored, "provider_metadata", None) or {}
        if str(metadata.get("instance_url") or "") != instance_url:
            return None
        if (getattr(stored, "provider_username", "") or "") != username:
            return None
        return stored.refresh_token

    def _token_credentials(
        self,
        token_data: dict,
        existing_credentials: list[CredentialValues] | None = None,
        refresh_token_fallback: Callable[[str, str], str | None] | None = None,
    ) -> dict:
        """Shape the adapter's finalized token_data into the fields needed to persist to TMS.

        The token_data dict comes straight from the callback (no result-store round-trip). Applies
        GitLab's refresh-token recovery for the re-consent case where GitLab omits refresh_token.
        """
        access_token = token_data.get("access_token", "")
        if not access_token:
            raise OAuthTokenDataError("OAuth access token missing: the OAuth response did not include an access_token.")

        refresh_token = token_data.get("refresh_token", "")
        instance_url = token_data.get("instance_url", "")
        username = token_data.get("username", "")

        # Recover refresh_token from an existing setting on the same instance + username when GitLab
        # omits it on re-consent (rare, but possible if the app is reconfigured without offline access).
        if not refresh_token and existing_credentials:
            refresh_token = self._recover_refresh_token(existing_credentials, username, instance_url) or refresh_token
        if not refresh_token and refresh_token_fallback is not None:
            refresh_token = refresh_token_fallback(username, instance_url) or refresh_token

        if not refresh_token:
            raise OAuthTokenDataError(
                f"OAuth refresh token missing: no refresh token available for {username}. "
                "Ensure the OAuth application has the requested scopes and try again."
            )

        try:
            expires_in = int(token_data.get("expires_in") or 7200)
        except (ValueError, TypeError):
            expires_in = 7200

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": str(int(time.time()) + expires_in),
            "instance_url": instance_url,
            "scopes": token_data.get("scopes", ""),
            "username": username,
            "user_id": token_data.get("user_id", ""),
        }

    def persist_user_token(
        self,
        *,
        token_data: dict,
        user_id: str,
        setting_id: str,
        existing_credentials: list[CredentialValues] | None = None,
    ) -> None:
        """Write the acting user's tokens from a completed OAuth flow into enterprise TMS."""
        from codemie.rest_api.models.settings import Settings

        creds = self._token_credentials(
            token_data,
            existing_credentials,
            refresh_token_fallback=lambda username, instance_url: self._vault_refresh_token(
                user_id, setting_id, username, instance_url
            ),
        )
        setting = Settings.find_by_id(setting_id)
        if setting is None:
            raise OAuthIntegrationNotFoundError(f"Integration '{setting_id}' not found")
        app_creds = {c.key: c.value for c in setting.credential_values}
        client_id = app_creds.get("client_id") or ""
        encrypted_client_secret = app_creds.get("client_secret", "")
        raw_instance_url = app_creds.get("instance_url") or ""
        if not client_id or not encrypted_client_secret or not raw_instance_url:
            raise OAuthIntegrationConfigError(
                "GitLab OAuth integration is missing required app credentials. "
                "Please configure client_id, client_secret, and instance_url."
            )
        try:
            client_secret = self.encryption_service.decrypt(encrypted_client_secret)
        except Exception as exc:
            raise OAuthIntegrationConfigError(f"Failed to decrypt GitLab OAuth client_secret: {exc}") from exc
        normalized_instance_url = ensure_instance_allowed(raw_instance_url)
        scopes = [scope for scope in (creds.get("scopes") or "").split() if scope]
        try:
            expires_at = int(creds.get("expires_at", "0") or 0)
        except (ValueError, TypeError):
            expires_at = 0
        expires_in_seconds = max(expires_at - int(time.time()), 1)
        refresh_metadata = OAuth2RefreshMetadata(
            token_endpoint=token_url(normalized_instance_url),
            client_id=client_id,
            client_auth_method="client_secret_post",
            client_secret=client_secret,
            scopes=scopes,
            flow_source="admin",
        )
        try:
            ToolOAuthTokenPort.save_oauth2_token(
                user_id=user_id,
                integration_id=setting_id,
                access_token=creds["access_token"],
                refresh_token=creds["refresh_token"],
                expires_in_seconds=expires_in_seconds,
                scope=creds.get("scopes") or None,
                scopes=scopes,
                refresh_metadata=refresh_metadata,
                provider_username=creds.get("username", ""),
                provider_user_id=creds.get("user_id", ""),
                provider_metadata={"instance_url": normalized_instance_url},
            )
        except Exception as exc:
            raise OAuthTokenPersistenceError(
                f"GitLab OAuth token service failed to persist credentials: {exc}"
            ) from exc
