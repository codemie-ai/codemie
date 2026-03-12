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

import re
import traceback
from typing import Tuple

from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper
from codemie_tools.git.gitlab.custom_gitlab_api_wrapper import CustomGitLabAPIWrapper
from pydantic import BaseModel

from codemie.configs import logger


class GitApiService(BaseModel):
    @classmethod
    def split_git_url(cls, git_url: str) -> Tuple[str, str]:
        regexp_split_url_and_repo = r"(https?:\/\/[^\/]+)(\/.*)"
        url_and_repo = re.split(regexp_split_url_and_repo, git_url)
        url_and_repo = list(filter(None, url_and_repo))

        base_url = url_and_repo[0]
        token_regexp = r"(?<=://).*@"
        match = re.search(token_regexp, base_url)

        if match:
            token = match.group().split("@")[0] + "@"
            base_url = base_url.replace(token, "")

        return base_url, url_and_repo[1].rstrip('/')

    @classmethod
    def init_gitlab_api_wrapper(cls, repo_link: str, base_branch: str, gitlab_token: str):
        base_url, repo_name = cls.split_git_url(repo_link)

        try:
            if not gitlab_token:
                return None
            gitlab = CustomGitLabAPIWrapper(
                gitlab_base_url=base_url,
                gitlab_repository=repo_name.replace(".git", "").replace('/', '', 1),
                gitlab_base_branch=base_branch,
                gitlab_branch=base_branch,
                gitlab_personal_access_token=gitlab_token,
            )
            return gitlab
        except Exception:
            stacktrace = traceback.format_exc()
            logger.error(f"GitLab API wrapper initialisation failed with error: {stacktrace}", exc_info=True)

    @classmethod
    def init_github_api_wrapper(cls, github_access_token: str, repo_link: str = None, base_branch: str = None):
        try:
            if repo_link is not None:
                _, repo_name = cls.split_git_url(repo_link)
                github = CustomGitHubAPIWrapper(
                    github_repository=repo_name.replace(".git", "").replace('/', '', 1),
                    github_base_branch=base_branch,
                    active_branch=base_branch,
                    github_access_token=github_access_token,
                )
            else:
                github = CustomGitHubAPIWrapper(github_access_token=github_access_token)
            return github
        except Exception:
            stacktrace = traceback.format_exc()
            logger.error(f"GitHub API wrapper initialisation failed with error: {stacktrace}", exc_info=True)
