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
from typing import List, Any, Dict, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.models import ToolKit, Tool, ToolSet
from codemie_tools.base.utils import humanize_error
from codemie_tools.git.azure_devops.toolkit import AzureDevOpsToolkit
from codemie_tools.git.bitbucket.bitbucket_toolkit import CustomBitbucketToolkit
from codemie_tools.git.github.github_toolkit import CustomGitHubToolkit
from codemie_tools.git.github.tools_vars import (
    SET_ACTIVE_BRANCH_TOOL,
    UPDATE_FILE_TOOL,
    LIST_BRANCHES_TOOL,
    DELETE_FILE_TOOL,
    CREATE_FILE_TOOL,
    CREATE_PULL_REQUEST_TOOL,
    CREATE_GIT_BRANCH_TOOL,
    GET_PR_CHANGES,
    CREATE_PR_CHANGE_COMMENT,
    UPDATE_FILE_DIFF_TOOL,
)
from codemie_tools.git.gitlab.gitlab_toolkit import CustomGitLabToolkit
from codemie_tools.git.utils import (
    GitCredentials,
    validate_bitbucket,
    validate_github_wrapper,
    validate_gitlab_wrapper,
    validate_azure_devops_credentials,
    split_git_url,
)
from codemie_tools.git.utils import (
    TYPE_GITHUB,
    TYPE_GITLAB,
    TYPE_BITBUCKET,
    TYPE_AZURE_DEVOPS,
    TYPE_UNKNOWN,
)

logger = logging.getLogger(__name__)


class GitToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.GIT
    settings_config: bool = True
    tools: List[Tool] = [
        Tool.from_metadata(CREATE_GIT_BRANCH_TOOL),
        Tool.from_metadata(SET_ACTIVE_BRANCH_TOOL),
        Tool.from_metadata(LIST_BRANCHES_TOOL),
        Tool.from_metadata(CREATE_FILE_TOOL),
        Tool.from_metadata(UPDATE_FILE_TOOL),
        Tool.from_metadata(UPDATE_FILE_DIFF_TOOL),
        Tool.from_metadata(DELETE_FILE_TOOL),
        Tool.from_metadata(CREATE_PULL_REQUEST_TOOL),
        Tool.from_metadata(GET_PR_CHANGES),
        Tool.from_metadata(CREATE_PR_CHANGE_COMMENT),
    ]


class GitToolkit(BaseToolkit):
    git_toolkit: Any = None

    @classmethod
    def get_tools_ui_info(cls, *args, **kwargs):
        return GitToolkitUI().model_dump()

    def get_tools(self) -> List[BaseTool]:
        tools = []
        for tool in self.git_toolkit.get_tools():
            tool.name = tool.name.replace(" ", "_")
            tools.append(tool)
        return tools

    @classmethod
    def get_toolkit(cls, configs: Dict[str, Any], llm_model: Optional[BaseChatModel] = None):
        repo_type = configs.get("repo_type", TYPE_UNKNOWN)
        if repo_type == TYPE_GITLAB:
            toolkit = CustomGitLabToolkit.get_toolkit(configs=configs, llm_model=llm_model)
        elif repo_type == TYPE_GITHUB:
            toolkit = CustomGitHubToolkit.get_toolkit(configs=configs, llm_model=llm_model)
        elif repo_type == TYPE_BITBUCKET:
            toolkit = CustomBitbucketToolkit.get_toolkit(configs, llm_model)
        elif repo_type == TYPE_AZURE_DEVOPS:
            toolkit = AzureDevOpsToolkit.get_toolkit(configs)
        else:
            logger.error(f"Unknown repository type: {repo_type}")
            raise ValueError("Unknown repository type")
        return GitToolkit(git_toolkit=toolkit)

    @classmethod
    def git_integration_healthcheck(cls, configs: Dict[str, Any]):
        try:
            split_git_url(configs.get("repo_link").rstrip('/'))
            repo_type = configs.get("repo_type", TYPE_UNKNOWN)
            credentials = GitCredentials(**configs)
            if repo_type == TYPE_GITLAB:
                validate_gitlab_wrapper(api_wrapper=None, git_creds=credentials)
            elif repo_type == TYPE_GITHUB:
                validate_github_wrapper(api_wrapper=None, git_creds=credentials)
            elif repo_type == TYPE_BITBUCKET:
                validate_bitbucket(bitbucket=None, git_creds=credentials)
            elif repo_type == TYPE_AZURE_DEVOPS:
                validate_azure_devops_credentials(configs=configs)
            else:
                msg = f"Unknown repository type: {repo_type}"
                logger.error(msg)
                raise ValueError(msg)
        except IndexError:
            return False, "Testing the connection requires the full repository URL"
        except Exception as e:
            return False, humanize_error(e)

        return True, ""
