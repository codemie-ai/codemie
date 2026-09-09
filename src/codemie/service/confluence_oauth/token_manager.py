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

"""Confluence OAuth token lifecycle manager backed by enterprise TMS."""

from codemie.core.exceptions import ConfluenceAuthRequiredException, ExtendedHTTPException
from codemie.rest_api.models.settings import Settings
from codemie.service.oauth.token_port import ToolOAuthTokenPort, map_tms_error_to_http
from codemie.service.oauth.folded_credentials import OAUTH_PROVIDER_CONFLUENCE, oauth_provider


class ConfluenceOAuthTokenManager:
    """Resolves per-user Confluence OAuth tokens via enterprise TMS."""

    def __init__(self, encryption_service=None):
        self.encryption_service = encryption_service

    def get_valid_access_token(self, setting_id: str, user_id: str) -> str:
        setting = Settings.find_by_id(setting_id)
        if setting is None:
            raise ExtendedHTTPException(404, f"Setting '{setting_id}' not found")
        if oauth_provider(setting) != OAUTH_PROVIDER_CONFLUENCE:
            raise ExtendedHTTPException(
                400, f"Setting '{setting_id}' is not a Confluence OAuth credential (type: {setting.credential_type})"
            )
        try:
            token_data = ToolOAuthTokenPort.get_oauth2_token_or_none(user_id=user_id, integration_id=setting_id)
        except Exception as exc:
            raise map_tms_error_to_http("Confluence", exc)
        if token_data is None or not token_data.access_token:
            raise ConfluenceAuthRequiredException(setting_id)
        return token_data.access_token

    def get_cloud_id(self, setting_id: str, user_id: str) -> str:
        try:
            token_data = ToolOAuthTokenPort.get_oauth2_token_or_none(user_id=user_id, integration_id=setting_id)
        except Exception as exc:
            # A TMS outage / crypto / refresh failure is not the same as "no cloud_id" — surface it
            # as the mapped 5xx instead of masquerading as a missing Atlassian site.
            raise map_tms_error_to_http("Confluence", exc)
        if token_data is None:
            return ""
        metadata = token_data.provider_metadata or {}
        if isinstance(metadata, dict):
            return metadata.get("cloud_id", "") or ""
        return ""
