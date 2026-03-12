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

import logging
import re
import traceback
from enum import Enum
from typing import Optional, Tuple, Dict, Any

from atlassian.bitbucket.cloud.repositories import Repository
from pydantic import BaseModel, Field

from codemie_tools.base.errors import InvalidCredentialsError
from codemie_tools.git.azure_devops.tools import AzureDevOpsClient
from codemie_tools.git.bitbucket.custom_bitbucket_wrapper import (
    CustomBitbucketApiWrapper,
)
from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper
from codemie_tools.git.gitlab.custom_gitlab_api_wrapper import CustomGitLabAPIWrapper

logger = logging.getLogger(__name__)

NO_GIT_CREDS_FOUND_LOG = "No Git credentials found for this repository"
CLIENT_ERROR_MESSAGE = "Client is not initialized"
NO_GIT_CREDS_FOUND_MESSAGE = "No Git credentials found for repository. Provide Git credentials in 'Integrations'"

TYPE_GITHUB: str = "github"
TYPE_GITLAB: str = "gitlab"
TYPE_BITBUCKET: str = "bitbucket"
TYPE_AZURE_DEVOPS: str = "azure_devops"
TYPE_UNKNOWN: str = "unknown"


class GitAuthType(str, Enum):
    """Git authentication types."""

    PAT = "pat"
    GITHUB_APP = "github_app"


class GitCredentials(BaseModel):
    # Authentication type (OAuth pattern)
    auth_type: Optional[str] = Field(
        default="pat", description="Authentication type: 'pat' for Personal Access Token or 'github_app' for GitHub App"
    )

    # PAT Authentication (used by all Git providers)
    token: Optional[str] = Field(
        default=None,
        description="Personal Access Token with appropriate permissions for repository access",
        example="ghp_1a2b3c4d5e6f7g8h9i0j...",
    )
    token_name: Optional[str] = Field(
        default=None, description="Token name or identifier for reference", example="my-project-token"
    )

    # GitHub App Authentication (GitHub only)
    app_id: Optional[int] = Field(default=None, description="GitHub App ID for GitHub App authentication (GitHub only)")
    private_key: Optional[str] = Field(default=None, description="GitHub App private key in PEM format (GitHub only)")
    installation_id: Optional[int] = Field(
        default=None,
        description="GitHub App installation ID (optional, will auto-fetch first installation if not provided)",
    )

    repo_link: str = Field(
        description="URL to your Git server, e.g. https://gitlab.example.com or https://github.com",
        example="https://gitlab.example.com/username/repository.git",
    )
    base_branch: str = Field(description="Base branch to use, defaults to main/master", example="main")
    repo_type: str = Field(
        default=TYPE_UNKNOWN, description="Repository type: github, gitlab, bitbucket, or azure_devops"
    )

    @property
    def is_github_app(self) -> bool:
        """Check if GitHub App authentication is configured."""
        return self.app_id is not None and self.private_key is not None

    def model_post_init(self, __context):
        """Validate authentication configuration after initialization."""
        # Determine if this is a GitHub repository
        is_github = self.repo_type == TYPE_GITHUB or "github.com" in self.repo_link.lower()

        # Check if GitHub App fields are provided for non-GitHub providers
        if not is_github and (
            self.app_id is not None or self.private_key is not None or self.installation_id is not None
        ):
            raise ValueError(
                f"GitHub App authentication (app_id, private_key, installation_id) "
                f"is only supported for GitHub repositories, not for {self.repo_type}. "
                f"Use 'token' for authentication with {self.repo_type}."
            )

        # Validate GitHub authentication
        if is_github:
            has_pat = self.token is not None
            has_github_app = self.app_id is not None or self.private_key is not None

            # Check if both auth methods are provided
            if has_pat and has_github_app:
                raise ValueError(
                    "Cannot use both PAT and GitHub App authentication for GitHub. "
                    "Provide either 'token' (PAT) or 'app_id' + 'private_key' (GitHub App)"
                )

            # Check if no auth method is provided
            if not has_pat and not has_github_app:
                raise ValueError(
                    "GitHub authentication required: provide either 'token' (PAT) or "
                    "'app_id' + 'private_key' (GitHub App)"
                )

            # Check if GitHub App config is incomplete
            if (self.app_id is None) != (self.private_key is None):
                raise ValueError("GitHub App authentication requires both 'app_id' and 'private_key'")
        else:
            # Non-GitHub providers must have a token
            if not self.token:
                raise ValueError(f"Token is required for {self.repo_type} authentication")


def init_gitlab_api_wrapper(
    git_creds: GitCredentials,
) -> Optional[CustomGitLabAPIWrapper]:
    base_url, repo_name = split_git_url(git_creds.repo_link)
    try:
        if not git_creds.token:
            return None
        return CustomGitLabAPIWrapper(
            gitlab_base_url=base_url,
            gitlab_repository=repo_name.replace(".git", "").replace("/", "", 1),
            gitlab_base_branch=git_creds.base_branch,
            gitlab_branch=git_creds.base_branch,
            gitlab_personal_access_token=git_creds.token,
        )
    except Exception:
        stacktrace = traceback.format_exc()
        logger.error(
            f"GitLab API wrapper initialisation failed with error: {stacktrace}",
            exc_info=True,
        )
        return None


def init_github_api_wrapper(git_creds: GitCredentials):
    try:
        # Check if we have any authentication credentials
        if not git_creds.token and not git_creds.is_github_app:
            return None

        # Build wrapper arguments
        wrapper_args = {
            "github_base_branch": git_creds.base_branch,
            "active_branch": git_creds.base_branch,
        }

        # Add repository if provided
        if git_creds.repo_link is not None:
            _, repo_name = split_git_url(git_creds.repo_link)
            wrapper_args["github_repository"] = repo_name.replace(".git", "").replace("/", "", 1)

        # Add authentication credentials
        if git_creds.is_github_app:
            # GitHub App authentication
            wrapper_args["github_app_id"] = git_creds.app_id
            wrapper_args["github_app_private_key"] = git_creds.private_key
            if git_creds.installation_id:
                wrapper_args["github_app_installation_id"] = git_creds.installation_id
        else:
            # PAT authentication
            wrapper_args["github_access_token"] = git_creds.token

        github = CustomGitHubAPIWrapper(**wrapper_args)
        return github
    except Exception:
        stacktrace = traceback.format_exc()
        logger.error(
            f"GitHub API wrapper initialisation failed with error: {stacktrace}",
            exc_info=True,
        )
        return None


def init_bitbucket_api_wrapper(git_creds: GitCredentials):
    try:
        if not git_creds.token:
            return None
        base_url, project_and_repo_str = split_git_url(git_creds.repo_link)
        project_and_repo = project_and_repo_str.replace("/", "", 1).split("/")
        project_key = project_and_repo[0]
        repo_name = project_and_repo[1].replace(".git", "").replace("/", "", 1)
        return CustomBitbucketApiWrapper(
            url=base_url,
            token=git_creds.token,
            project_key=project_key,
            repository_slug=repo_name,
            base_branch=git_creds.base_branch,
            active_branch=git_creds.base_branch,
        )
    except Exception:
        stacktrace = traceback.format_exc()
        logger.error(
            f"Bitbucket API wrapper initialisation failed with error: {stacktrace}",
            exc_info=True,
        )
        return None


def validate_azure_devops_credentials(*, configs: Dict[str, Any]):
    try:
        azure_credentials = AzureDevOpsClient.init_credentials(configs=configs)
        client = AzureDevOpsClient(azure_credentials)
        client.verify_connection()
    except Exception as e:
        stacktrace = traceback.format_exc()
        logger.error(
            f"AzureDevOpsClient initialization failed with error: {stacktrace}",
            exc_info=True,
        )
        raise InvalidCredentialsError(f"Azure DevOps API client initialization failed: {str(e)}")


def validate_gitlab_wrapper(api_wrapper: [CustomGitLabAPIWrapper], git_creds: GitCredentials):
    if api_wrapper is not None:
        return
    if git_creds.token is None or git_creds.token == "":
        logger.error(NO_GIT_CREDS_FOUND_LOG)
        raise InvalidCredentialsError(NO_GIT_CREDS_FOUND_MESSAGE)
    try:
        gitlab_api_wrapper = init_gitlab_api_wrapper(git_creds)
        if gitlab_api_wrapper is None:
            raise InvalidCredentialsError(CLIENT_ERROR_MESSAGE)
        return gitlab_api_wrapper
    except Exception:
        stacktrace = traceback.format_exc()
        logger.error(
            f"GitLab API client initialisation failed with error: {stacktrace}",
            exc_info=True,
        )
        raise InvalidCredentialsError(
            "GitLab API client initialisation failed: Please check your GitLab credentials are "
            "provided in 'User Settings'"
        )


def validate_bitbucket(bitbucket: Optional[Repository], git_creds: GitCredentials):
    if bitbucket is not None:
        return

    if git_creds.token is None or git_creds.token == "":
        logger.error(NO_GIT_CREDS_FOUND_LOG)
        raise InvalidCredentialsError(NO_GIT_CREDS_FOUND_MESSAGE)
    try:
        bitbucket = init_bitbucket_api_wrapper(git_creds)
        if bitbucket is None:
            raise InvalidCredentialsError(CLIENT_ERROR_MESSAGE)
        return bitbucket
    except Exception:
        stacktrace = traceback.format_exc()
        logger.error(
            f"Bitbucket API client initialisation failed with error: {stacktrace}",
            exc_info=True,
        )
        raise InvalidCredentialsError(
            "Bitbucket API client initialisation failed: Please check your Git credentials "
            "are provided in 'User Settings'"
        )


def validate_github_wrapper(api_wrapper: Optional[CustomGitHubAPIWrapper], git_creds: GitCredentials):
    if api_wrapper is not None:
        return

    # Check if we have any authentication credentials
    has_credentials = (git_creds.token is not None and git_creds.token != "") or git_creds.is_github_app

    if not has_credentials:
        logger.error(NO_GIT_CREDS_FOUND_LOG)
        raise InvalidCredentialsError(NO_GIT_CREDS_FOUND_MESSAGE)
    try:
        github_api_wrapper = init_github_api_wrapper(git_creds)
        if github_api_wrapper is None:
            raise InvalidCredentialsError(CLIENT_ERROR_MESSAGE)
        return github_api_wrapper
    except Exception:
        stacktrace = traceback.format_exc()
        logger.error(
            f"GitHub API client initialisation failed with error: {stacktrace}",
            exc_info=True,
        )
        raise InvalidCredentialsError(
            "GitHub API client initialisation failed: Please check your Git credentials "
            "are provided in 'User Settings'"
        )


def split_git_url(git_url: str) -> Tuple[str, str]:
    regexp_split_url_and_repo = r"(https?:\/\/[^\/]+)(\/.*)"
    url_and_repo = re.split(regexp_split_url_and_repo, git_url)
    url_and_repo = list(filter(None, url_and_repo))

    base_url = url_and_repo[0]
    token_regexp = r"(?<=://).*@"  # NOSONAR
    match = re.search(token_regexp, base_url)

    if match:
        token = match.group().split("@")[0] + "@"
        base_url = base_url.replace(token, "")

    return base_url, url_and_repo[1].rstrip("/")
