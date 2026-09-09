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

"""GitLab OAuth 2.0 endpoints (Authorization Code + PKCE).

Endpoints are sync `def` (not `async def`) because the flow service performs blocking I/O
(httpx + SQLAlchemy). FastAPI runs sync endpoints in a thread pool so the event loop stays free.
The endpoint bodies live in :mod:`codemie.rest_api.routers.oauth_router_factory`; this module only
declares GitLab's provider-specific configuration.
"""

from typing import Optional

from codemie.configs import config
from codemie.rest_api.routers.oauth_router_factory import (
    InitiateOAuthRequest,
    OAuthRouterConfig,
    build_oauth_router,
)
from codemie.service.gitlab_oauth.flow_service import GitLabOAuthFlowService
from codemie.service.oauth.folded_credentials import OAUTH_PROVIDER_GITLAB


class InitiateGitLabOAuthRequest(InitiateOAuthRequest):
    # instance_url selects gitlab.com or a self-hosted GitLab; defaults to
    # GITLAB_OAUTH_DEFAULT_INSTANCE_URL when omitted.
    instance_url: Optional[str] = None


def _get_oauth_service() -> GitLabOAuthFlowService:
    return GitLabOAuthFlowService()


router = build_oauth_router(
    OAuthRouterConfig(
        prefix="/v1/gitlab-oauth",
        tag="GitLab OAuth",
        provider_label="GitLab",
        enabled=lambda: config.GITLAB_OAUTH_ENABLED,
        provider=OAUTH_PROVIDER_GITLAB,
        # Indirect so tests can monkeypatch this module's _get_oauth_service.
        flow_service_factory=lambda: _get_oauth_service(),
        initiate_model=InitiateGitLabOAuthRequest,
        extra_app_keys=("instance_url",),
        missing_app_credentials_message=(
            "GitLab OAuth integration is missing required app credentials. "
            "Please configure client_id, client_secret, callback_base_url, and instance_url."
        ),
        mount_callback=True,
        callback_enabled=lambda: config.GITLAB_OAUTH_ENABLED,
        callback_disabled_html_message="GitLab OAuth is disabled.",
    )
)
