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

"""Confluence (Atlassian Cloud) OAuth 2.0 endpoints (Authorization Code + PKCE).

The endpoint bodies live in :mod:`codemie.rest_api.routers.oauth_router_factory`; this module only
declares Confluence's provider-specific configuration.

NOTE: Confluence has no ``/callback`` of its own — Atlassian redirects to the shared
``/v1/atlassian-oauth/callback`` (served by the Jira router), which processes Jira and Confluence
flows alike via the shared Atlassian OAuth state store. Only one Callback URL is registered.
"""

from codemie.configs import config
from codemie.rest_api.routers.oauth_router_factory import OAuthRouterConfig, build_oauth_router
from codemie.service.oauth.folded_credentials import OAUTH_PROVIDER_CONFLUENCE
from codemie.service.confluence_oauth.flow_service import ConfluenceOAuthFlowService


def _get_oauth_service() -> ConfluenceOAuthFlowService:
    return ConfluenceOAuthFlowService()


router = build_oauth_router(
    OAuthRouterConfig(
        prefix="/v1/confluence-oauth",
        tag="Confluence OAuth",
        provider_label="Confluence",
        enabled=lambda: config.CONFLUENCE_OAUTH_ENABLED,
        provider=OAUTH_PROVIDER_CONFLUENCE,
        # Indirect so tests can monkeypatch this module's _get_oauth_service.
        flow_service_factory=lambda: _get_oauth_service(),
        missing_app_credentials_message=(
            "Confluence OAuth integration is missing required app credentials. "
            "Please configure client_id and client_secret."
        ),
        mount_callback=False,
    )
)
