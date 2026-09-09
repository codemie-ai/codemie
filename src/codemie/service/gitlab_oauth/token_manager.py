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

"""GitLab OAuth token lifecycle manager backed by enterprise TMS."""

import httpx

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException, GitLabAuthRequiredException
from codemie.rest_api.models.settings import Settings
from codemie.service.encryption.encryption_factory import EncryptionFactory
from codemie.service.gitlab_oauth.constants import ensure_instance_allowed
from codemie.service.oauth.token_port import ToolOAuthTokenPort, map_tms_error_to_http
from codemie.service.oauth.folded_credentials import OAUTH_PROVIDER_GITLAB, oauth_provider


class GitLabOAuthTokenManager:
    """Resolves per-user GitLab OAuth tokens via enterprise TMS."""

    def __init__(self, encryption_service=None):
        self.encryption_service = encryption_service or EncryptionFactory().get_current_encryption_service()

    def get_valid_access_token(self, setting_id: str, user_id: str) -> str:
        setting = Settings.find_by_id(setting_id)
        if setting is None:
            raise ExtendedHTTPException(404, f"Setting '{setting_id}' not found")
        if oauth_provider(setting) != OAUTH_PROVIDER_GITLAB:
            raise ExtendedHTTPException(
                400,
                f"Setting '{setting_id}' is not a GitLab OAuth credential (type: {setting.credential_type})",
            )
        try:
            token_data = ToolOAuthTokenPort.get_oauth2_token_or_none(user_id=user_id, integration_id=setting_id)
        except Exception as exc:
            raise map_tms_error_to_http("GitLab", exc)
        if token_data is None or not token_data.access_token:
            # Ride the shared auth-required propagation (like Jira/Confluence) so tool execution
            # surfaces a GitLab connect gate instead of a generic 400 client error.
            raise GitLabAuthRequiredException(setting_id)
        return token_data.access_token

    def _read_client_credentials(self, setting: Settings) -> tuple[str, str]:
        creds = {cred.key: cred.value for cred in setting.credential_values}
        client_id = creds.get("client_id") or ""
        encrypted_secret = creds.get("client_secret", "")
        if not encrypted_secret:
            raise ExtendedHTTPException(
                400,
                "GitLab OAuth integration is missing client_secret. Please reconnect the integration.",
            )
        if not client_id:
            raise ExtendedHTTPException(
                400,
                "GitLab OAuth integration is missing client_id. Please reconnect the integration.",
            )
        try:
            return client_id, self.encryption_service.decrypt(encrypted_secret)
        except Exception as exc:
            logger.error(f"GitLab OAuth: failed to decrypt client_secret for setting {setting.id}: {exc}")
            raise ExtendedHTTPException(
                502,
                "Failed to read GitLab OAuth integration credentials. Please reconnect the integration.",
            )

    def revoke_token(self, setting_id: str, user_id: str) -> None:
        """Best-effort provider-side revoke, then token deletion is handled by the caller."""
        setting = Settings.find_by_id(setting_id)
        if setting is None:
            return

        try:
            token_data = ToolOAuthTokenPort.get_oauth2_token_or_none(user_id=user_id, integration_id=setting_id)
        except Exception as exc:
            logger.warning(f"GitLab OAuth: revoke lookup failed for setting {setting_id}: {exc}")
            return
        if token_data is None or not token_data.refresh_token:
            return

        creds = {cred.key: cred.value for cred in setting.credential_values}
        instance_url = creds.get("instance_url", "")
        if not instance_url:
            return
        try:
            normalized_instance_url = ensure_instance_allowed(instance_url)
        except ValueError as exc:
            logger.warning(f"GitLab OAuth: revoke skipped — instance not allowed for {setting_id}: {exc}")
            return

        client_id, client_secret = self._read_client_credentials(setting)
        try:
            with httpx.Client(timeout=10) as client:
                response = client.post(
                    f"{normalized_instance_url.rstrip('/')}/oauth/revoke",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "token": token_data.refresh_token,
                    },
                )
            # A non-2xx revoke means the grant may still be live at GitLab even though we are about
            # to delete the local token — surface it as a real warning instead of silently assuming
            # success (best-effort: we still let the caller proceed with local deletion).
            if response.is_success:
                logger.info(f"GitLab OAuth: revoked refresh token for setting {setting_id}")
            else:
                logger.warning(
                    f"GitLab OAuth: provider revoke returned {response.status_code} for setting "
                    f"{setting_id}; the grant may still be active at GitLab."
                )
        except Exception as exc:
            logger.warning(f"GitLab OAuth: token revoke best-effort failed for {setting_id}: {exc}")
