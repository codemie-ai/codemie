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

"""Shared bridge between a tool OAuth flow and TMS-backed per-user token storage."""

from __future__ import annotations

from codemie.service.encryption.encryption_factory import EncryptionFactory
from codemie.service.jira_oauth.constants import TOKEN_URL as _ATLASSIAN_TOKEN_URL
from codemie.service.oauth.errors import (
    OAuthIntegrationConfigError,
    OAuthIntegrationNotFoundError,
    OAuthTokenDataError,
    OAuthTokenPersistenceError,
)
from codemie.service.oauth.token_port import ToolOAuthTokenPort


class ToolOAuthSettingsService:
    """Common scaffolding for the per-provider OAuth settings services.

    Subclasses set ``provider_label`` (used in member-facing error messages) and ``_APP_KEYS`` (the
    app-credential keys that live on the shared Settings row and must survive alias-only updates),
    and implement ``persist_user_token``.
    """

    provider_label: str = ""
    # App-credential keys preserved on updates that only touch alias / is_global. Tokens no longer
    # live on the setting — they are stored per-user in enterprise TMS.
    _APP_KEYS: frozenset[str] = frozenset()

    def __init__(self, encryption_service=None):
        self.encryption_service = encryption_service or EncryptionFactory().get_current_encryption_service()

    @classmethod
    def get_preserved_credential_keys(cls, existing_credentials, prepared_cred_keys: list[str]) -> list[str]:
        """Preserve app-credential keys during updates that only touch alias / is_global."""
        existing_keys = [
            cred.key
            for cred in existing_credentials
            if cred.key in cls._APP_KEYS and cred.key not in prepared_cred_keys
        ]
        return prepared_cred_keys + existing_keys


class AtlassianOAuthSettingsService(ToolOAuthSettingsService):
    """Shared Jira/Confluence settings service.

    Jira and Confluence authenticate against the same Atlassian 3LO app, so their token-persistence
    logic is identical apart from the ``provider_label`` in member-facing messages.
    """

    _APP_KEYS = frozenset({"client_id", "client_secret"})

    def persist_user_token(self, *, token_data: dict, user_id: str, setting_id: str) -> None:
        """Write the acting user's tokens (and Atlassian cloud_id) from a completed flow into TMS.

        token_data is the adapter's finalized callback payload, handed in directly — it never passes
        through the result store.
        """
        from codemie.rest_api.models.settings import Settings
        from codemie_enterprise.mcp_auth import OAuth2RefreshMetadata

        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")
        if not access_token or not refresh_token:
            raise OAuthTokenDataError(
                "OAuth tokens missing from Atlassian response. "
                "Ensure the OAuth application requests the 'offline_access' scope and try again."
            )
        setting = Settings.find_by_id(setting_id)
        if setting is None:
            raise OAuthIntegrationNotFoundError(f"Integration '{setting_id}' not found")
        app_creds = {c.key: c.value for c in setting.credential_values}
        client_id = app_creds.get("client_id") or ""
        encrypted_client_secret = app_creds.get("client_secret", "")
        if not client_id or not encrypted_client_secret:
            raise OAuthIntegrationConfigError(
                f"{self.provider_label} OAuth integration is missing required app credentials. "
                "Please configure client_id and client_secret."
            )
        try:
            client_secret = self.encryption_service.decrypt(encrypted_client_secret)
        except Exception as exc:
            raise OAuthIntegrationConfigError(
                f"Failed to decrypt {self.provider_label} OAuth client_secret: {exc}"
            ) from exc
        try:
            expires_in_seconds = int(token_data.get("expires_in") or 3600)
        except (ValueError, TypeError):
            expires_in_seconds = 3600
        scopes = [scope for scope in (token_data.get("scopes") or "").split() if scope]
        refresh_metadata = OAuth2RefreshMetadata(
            token_endpoint=_ATLASSIAN_TOKEN_URL,
            client_id=client_id,
            client_auth_method="client_secret_post",
            client_secret=client_secret,
            scopes=scopes,
            flow_source="admin",
        )
        provider_metadata = {
            "cloud_id": token_data.get("cloud_id", ""),
            "site_url": token_data.get("site_url", ""),
            "site_name": token_data.get("site_name", ""),
        }
        try:
            ToolOAuthTokenPort.save_oauth2_token(
                user_id=user_id,
                integration_id=setting_id,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in_seconds=expires_in_seconds,
                scope=token_data.get("scopes") or None,
                scopes=scopes,
                refresh_metadata=refresh_metadata,
                provider_username=token_data.get("username", ""),
                provider_user_id=token_data.get("account_id", ""),
                provider_metadata=provider_metadata,
            )
        except Exception as exc:
            raise OAuthTokenPersistenceError(
                f"{self.provider_label} OAuth token service failed to persist credentials: {exc}"
            ) from exc
