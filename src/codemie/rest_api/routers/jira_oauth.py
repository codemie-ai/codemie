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

"""Jira (Atlassian Cloud) OAuth 2.0 endpoints (Authorization Code + PKCE).

The endpoint bodies live in :mod:`codemie.rest_api.routers.oauth_router_factory`; this module only
declares Jira's provider-specific configuration.

The ``/callback`` mounted here is the shared Atlassian callback used by BOTH Jira and Confluence
flows (Atlassian 3LO is one platform app, so a single Callback URL is registered). It must stay
reachable when either product's OAuth is enabled — gating it on JIRA_OAUTH_ENABLED alone would 503 a
valid Confluence callback whenever Jira OAuth is disabled.
"""

from codemie.configs import config
from codemie.rest_api.routers.oauth_router_factory import OAuthRouterConfig, build_oauth_router
from codemie.service.oauth.folded_credentials import OAUTH_PROVIDER_JIRA
from codemie.service.jira_oauth.flow_service import JiraOAuthFlowService


def _get_oauth_service() -> JiraOAuthFlowService:
    return JiraOAuthFlowService()


router = build_oauth_router(
    OAuthRouterConfig(
        prefix="/v1/atlassian-oauth",
        tag="Jira OAuth",
        provider_label="Jira",
        enabled=lambda: config.JIRA_OAUTH_ENABLED,
        provider=OAUTH_PROVIDER_JIRA,
        # Indirect so tests can monkeypatch this module's _get_oauth_service.
        flow_service_factory=lambda: _get_oauth_service(),
        missing_app_credentials_message=(
            "Jira OAuth integration is missing required app credentials. "
            "Please configure client_id and client_secret."
        ),
        mount_callback=True,
        callback_enabled=lambda: config.JIRA_OAUTH_ENABLED or config.CONFLUENCE_OAUTH_ENABLED,
        callback_disabled_html_message="Atlassian OAuth is disabled.",
    )
)
