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

from pydantic import Field
from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes, RequiredField


class GitlabConfig(CodeMieToolConfig):
    """Configuration for GitLab API access."""

    credential_type: CredentialTypes = Field(default=CredentialTypes.GIT, exclude=True, frozen=True)
    url: str = RequiredField(
        description="GitLab instance URL", json_schema_extra={"placeholder": "https://gitlab.example.com"}
    )
    # `default=""` (not RequiredField) is intentional: OAuth-backed instances (auth_type="oauth")
    # carry no PAT. Construction therefore never raises on a missing token; instead the token is
    # enforced at tool-init time by GitlabTool._validate_config via `required_at_runtime` for PAT
    # auth, and waived for OAuth (the token manager supplies the access token per request).
    token: str = Field(
        default="",
        description="GitLab Personal Access Token with appropriate scopes (required for PAT auth; leave empty for OAuth)",
        json_schema_extra={
            "sensitive": True,
            "required_at_runtime": True,
            "help": "https://docs.gitlab.com/ee/user/profile/personal_access_tokens.html",
        },
    )

    # OAuth 2.0 (Authorization Code + PKCE) — populated when this config is loaded from a Git
    # Settings row marked auth_type=oauth (GitLab). The tool consults the GitLab OAuth token manager
    # on every request; fields here are used only to identify the setting.
    auth_type: str = Field(default="pat", description="'pat' (default) or 'oauth' for GitLab 3LO")
    access_token: str = Field(default="", description="OAuth access token (managed by Codemie)", exclude=True)
    refresh_token: str = Field(default="", description="OAuth refresh token (managed by Codemie)", exclude=True)
    expires_at: int = Field(default=0, description="OAuth access token expiry (unix ts)", exclude=True)
    integration_id: str = Field(default="", description="Setting row id — used to refresh OAuth tokens in-place")
    acting_user_id: str = Field(
        default="",
        description="Codemie user id whose per-user OAuth token to use for this integration",
        exclude=True,
    )
