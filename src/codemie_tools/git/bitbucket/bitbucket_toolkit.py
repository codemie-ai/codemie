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

"""Bitbucket Toolkit."""

from typing import List, Optional, Any, Dict

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.models import ToolKit, ToolSet, Tool
from codemie_tools.git.bitbucket.bitbucket_openai_tools import (
    CreateFileTool,
    DeleteFileTool,
    CreatePRTool,
    ListBranchesTool,
    CreateBitbucketBranchTool,
    OpenAIUpdateFileWholeTool,
    OpenAIUpdateFileDiffTool,
    SetActiveBranchTool,
    CreatePullRequestChangeComment,
    GetPullRequestChanges,
)
from codemie_tools.git.bitbucket.tools_vars import (
    SET_ACTIVE_BRANCH_TOOL,
    CREATE_FILE_TOOL,
    UPDATE_FILE_TOOL,
    UPDATE_FILE_DIFF_TOOL,
    DELETE_FILE_TOOL,
    CREATE_PULL_REQUEST_TOOL,
    CREATE_GIT_BRANCH_TOOL,
    GET_PR_CHANGES,
    CREATE_PR_CHANGE_COMMENT,
    LIST_BRANCHES_TOOL,
)
from codemie_tools.git.utils import GitCredentials, init_bitbucket_api_wrapper


class BitbucketToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.GIT
    settings_config: bool = True
    tools: List[Tool] = [
        Tool.from_metadata(SET_ACTIVE_BRANCH_TOOL),
        Tool.from_metadata(CREATE_FILE_TOOL),
        Tool.from_metadata(UPDATE_FILE_TOOL),
        Tool.from_metadata(UPDATE_FILE_DIFF_TOOL),
        Tool.from_metadata(DELETE_FILE_TOOL),
        Tool.from_metadata(CREATE_PULL_REQUEST_TOOL),
        Tool.from_metadata(CREATE_GIT_BRANCH_TOOL),
        Tool.from_metadata(LIST_BRANCHES_TOOL),
        Tool.from_metadata(GET_PR_CHANGES),
        Tool.from_metadata(CREATE_PR_CHANGE_COMMENT),
    ]


class CustomBitbucketToolkit(BaseToolkit):
    git_creds: GitCredentials
    api_wrapper: Optional[Any] = None
    llm_model: Optional[Any] = None

    @classmethod
    def get_tools_ui_info(cls, *args, **kwargs):
        return BitbucketToolkitUI().model_dump()

    @classmethod
    def get_toolkit(
        cls, configs: Dict[str, Any], llm_model: Optional[BaseChatModel] = None
    ) -> 'CustomBitbucketToolkit':
        git_creds = GitCredentials(**configs)
        api_wrapper = init_bitbucket_api_wrapper(git_creds)
        return CustomBitbucketToolkit(git_creds=git_creds, api_wrapper=api_wrapper, llm_model=llm_model)

    def get_tools(self) -> List[BaseTool]:
        tools = [
            CreateBitbucketBranchTool(repo_wrapper=self.api_wrapper, credentials=self.git_creds),
            SetActiveBranchTool(repo_wrapper=self.api_wrapper, credentials=self.git_creds),
            ListBranchesTool(repo_wrapper=self.api_wrapper, credentials=self.git_creds),
            CreatePRTool(repo_wrapper=self.api_wrapper, credentials=self.git_creds),
            DeleteFileTool(repo_wrapper=self.api_wrapper, credentials=self.git_creds),
            CreateFileTool(repo_wrapper=self.api_wrapper, credentials=self.git_creds),
            GetPullRequestChanges(repo_wrapper=self.api_wrapper, credentials=self.git_creds),
            CreatePullRequestChangeComment(repo_wrapper=self.api_wrapper, credentials=self.git_creds),
            OpenAIUpdateFileWholeTool(
                repo_wrapper=self.api_wrapper, credentials=self.git_creds, llm_model=self.llm_model
            ),
            OpenAIUpdateFileDiffTool(
                repo_wrapper=self.api_wrapper, credentials=self.git_creds, llm_model=self.llm_model
            ),
        ]
        return tools
