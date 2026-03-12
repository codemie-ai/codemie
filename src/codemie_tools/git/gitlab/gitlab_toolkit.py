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

"""GitLab Toolkit."""

import logging
from typing import List, Optional, Dict, Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.git.gitlab.gitlab_openai_tools import (
    OpenAIUpdateFileWholeTool,
    OpenAIUpdateFileDiffTool,
    CreateFileTool,
    DeleteFileTool,
    CreatePRTool,
    ListBranchesTool,
    SetActiveBranchTool,
    CreateGitLabBranchTool,
    GetPullRequesChanges,
    CreatePullRequestChangeComment,
)
from codemie_tools.git.utils import GitCredentials, init_gitlab_api_wrapper

logger = logging.getLogger(__name__)


class CustomGitLabToolkit(BaseToolkit):
    git_creds: GitCredentials
    api_wrapper: Optional[Any] = None
    llm_model: Optional[Any] = None

    @classmethod
    def get_tools_ui_info(cls, *args, **kwargs):
        # no need this function at this moment
        pass

    @classmethod
    def get_toolkit(cls, configs: Dict[str, Any], llm_model: Optional[BaseChatModel] = None):
        git_creds = GitCredentials(**configs)
        api_wrapper = init_gitlab_api_wrapper(git_creds)
        return CustomGitLabToolkit(git_creds=git_creds, api_wrapper=api_wrapper, llm_model=llm_model)

    def get_tools(self) -> List[BaseTool]:
        tools = [
            CreateFileTool(api_wrapper=self.api_wrapper, credentials=self.git_creds),
            DeleteFileTool(api_wrapper=self.api_wrapper, credentials=self.git_creds),
            CreatePRTool(api_wrapper=self.api_wrapper, credentials=self.git_creds),
            ListBranchesTool(api_wrapper=self.api_wrapper, credentials=self.git_creds),
            SetActiveBranchTool(api_wrapper=self.api_wrapper, credentials=self.git_creds),
            CreateGitLabBranchTool(api_wrapper=self.api_wrapper, credentials=self.git_creds),
            GetPullRequesChanges(api_wrapper=self.api_wrapper, credentials=self.git_creds),
            CreatePullRequestChangeComment(api_wrapper=self.api_wrapper, credentials=self.git_creds),
            OpenAIUpdateFileWholeTool(
                api_wrapper=self.api_wrapper, credentials=self.git_creds, llm_model=self.llm_model
            ),
            OpenAIUpdateFileDiffTool(
                api_wrapper=self.api_wrapper, credentials=self.git_creds, llm_model=self.llm_model
            ),
        ]
        return tools
