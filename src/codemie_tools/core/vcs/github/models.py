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

from typing import Optional

from pydantic import Field, model_validator

from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes


class GithubConfig(CodeMieToolConfig):
    credential_type: CredentialTypes = Field(default=CredentialTypes.GIT, exclude=True, frozen=True)

    # PAT Authentication
    token: Optional[str] = Field(
        default=None,
        description="GitHub Personal Access Token with appropriate scopes for repository access",
        json_schema_extra={
            "sensitive": True,
            "help": "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token",
        },
    )

    # GitHub App Authentication
    app_id: Optional[int] = Field(
        default=None,
        description="GitHub App ID for GitHub App authentication",
        json_schema_extra={
            "help": "https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/about-creating-github-apps"
        },
    )
    private_key: Optional[str] = Field(
        default=None,
        description="GitHub App private key in PEM format",
        json_schema_extra={
            "sensitive": True,
            "help": "https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps",
        },
    )
    installation_id: Optional[int] = Field(
        default=None,
        description="GitHub App installation ID (optional, will auto-fetch first installation if not provided)",
        json_schema_extra={
            "help": "https://docs.github.com/en/rest/apps/apps#list-installations-for-the-authenticated-app"
        },
    )

    url: Optional[str] = Field(
        default="https://api.github.com",
        description="GitHub API URL, typically https://api.github.com",
        json_schema_extra={"placeholder": "https://api.github.com"},
    )

    @property
    def is_github_app(self) -> bool:
        """Check if GitHub App authentication is configured."""
        return self.app_id is not None and self.private_key is not None

    @model_validator(mode='after')
    def validate_authentication(self):
        """Validate that exactly one authentication method is configured."""
        has_pat = self.token is not None
        has_github_app = self.app_id is not None or self.private_key is not None

        # Check if both auth methods are provided
        if has_pat and has_github_app:
            raise ValueError(
                "Cannot use both PAT and GitHub App authentication. "
                "Provide either 'token' (PAT) or 'app_id' + 'private_key' (GitHub App)"
            )

        # Check if no auth method is provided
        if not has_pat and not has_github_app:
            raise ValueError(
                "Authentication required: provide either 'token' (PAT) or " "'app_id' + 'private_key' (GitHub App)"
            )

        # Check if GitHub App config is incomplete
        if (self.app_id is None) != (self.private_key is None):
            raise ValueError("GitHub App authentication requires both 'app_id' and 'private_key'")

        return self
