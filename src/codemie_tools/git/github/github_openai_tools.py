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
from abc import abstractmethod
from operator import itemgetter
from typing import Type, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.code.coder.diff_update_coder import update_content_by_task
from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper
from codemie_tools.git.github.tools_vars import (
    LIST_BRANCHES_TOOL,
    CREATE_GIT_BRANCH_TOOL,
    CREATE_PULL_REQUEST_TOOL,
    DELETE_FILE_TOOL,
    CREATE_FILE_TOOL,
    UPDATE_FILE_TOOL,
    UPDATE_FILE_DIFF_TOOL,
    SET_ACTIVE_BRANCH_TOOL,
)
from codemie_tools.git.prompts import UPDATE_CONTENT_PROMPT
from codemie_tools.git.tools import UpdateFileGitTool
from codemie_tools.git.tools_models import ListBranchesToolInput
from codemie_tools.git.utils import validate_github_wrapper, GitCredentials

logger = logging.getLogger(__name__)


class BranchInput(BaseModel):
    """Schema for operations that require a branch name as input."""

    branch_name: str = Field(description="The name of the branch, e.g. `my_branch`.")


class CreateGithubBranchTool(CodeMieTool):
    """Tool for interacting with the GitHub API."""

    api_wrapper: Optional[CustomGitHubAPIWrapper] = Field(exclude=True)
    credentials: GitCredentials
    name: str = CREATE_GIT_BRANCH_TOOL.name
    description: str = """This tool is a wrapper for the Git API to create a new branch in the repository."""
    args_schema: Type[BaseModel] = BranchInput

    def execute(self, branch_name: str, *args):
        validate_github_wrapper(self.api_wrapper, self.credentials)
        return self.api_wrapper.create_branch(proposed_branch_name=branch_name)


class SetActiveBranchTool(CodeMieTool):
    api_wrapper: Optional[CustomGitHubAPIWrapper] = Field(exclude=True)
    credentials: GitCredentials
    name: str = SET_ACTIVE_BRANCH_TOOL.name
    description: str = """
    This tool is a wrapper for the Git API and set the active branch in the repository, similar to `git checkout <branch_name>` and `git switch -c <branch_name>`."""
    args_schema: Type[BaseModel] = BranchInput

    def execute(self, branch_name: str, *args):
        validate_github_wrapper(self.api_wrapper, self.credentials)
        return self.api_wrapper.set_active_branch(branch_name=branch_name)


class ListBranchesTool(CodeMieTool):
    """Tool for interacting with the GitHub API."""

    api_wrapper: Optional[CustomGitHubAPIWrapper] = Field(exclude=True)
    credentials: GitCredentials
    name: str = LIST_BRANCHES_TOOL.name
    description: str = """This tool is a wrapper for the Git API to fetch a list of all branches in the repository.
    It will return the name of each branch. No input parameters are required."""
    args_schema: Optional[Type[BaseModel]] = ListBranchesToolInput

    def execute(self, query: str = "", **kwargs):
        validate_github_wrapper(self.api_wrapper, self.credentials)
        return self.api_wrapper.list_branches_in_repo()


class CreatePRInput(BaseModel):
    pr_title: str = Field(description="Title of pull request. Maybe generated from made changes in the branch.")
    pr_body: str = Field(description="Body or description of the pull request of made changes.")
    base_branch: str = Field(
        description="Base branch of the pull request. Default is the default branch of the repository."
    )


class CreatePRTool(CodeMieTool):
    """Tool for interacting with the GitHub API."""

    api_wrapper: Optional[CustomGitHubAPIWrapper] = Field(exclude=True)
    credentials: GitCredentials
    name: str = CREATE_PULL_REQUEST_TOOL.name
    description: str = """This tool is a wrapper for the Git API to create a new pull request in a GitHub repository.
    Strictly follow and provide input parameters based on context.
    """
    args_schema: Type[BaseModel] = CreatePRInput

    def execute(self, pr_title: str, pr_body: str, base_branch: str, *args):
        validate_github_wrapper(self.api_wrapper, self.credentials)
        target_branch = base_branch if base_branch else self.api_wrapper.default_branch
        pr = self.api_wrapper.github_repo_instance.create_pull(
            title=pr_title,
            body=pr_body,
            head=self.api_wrapper.active_branch,
            base=target_branch,
        )
        return f"Successfully created PR number {pr.number}"


class DeleteFileInput(BaseModel):
    file_path: str = Field(
        description="""File path of file to be deleted. e.g. `src/agents/developer/tools/git/github_tools.py`.
    **IMPORTANT**: the path must not start with a slash"""
    )
    commit_message: str = Field(
        description="""This commit message can be customized by user
        to provide more context about the deletion of the file.
        If user doesn't provide any message, default message will be used: 'Delete file {file_path}'"""
    )


class DeleteFileTool(CodeMieTool):
    """Tool for interacting with the GitHub API."""

    api_wrapper: Optional[CustomGitHubAPIWrapper] = Field(exclude=True)
    credentials: GitCredentials
    name: str = DELETE_FILE_TOOL.name
    description: str = """This tool is a wrapper for the GitHub API, useful when you need to delete a file in a GitHub repository.
    Simply pass in the full file path of the file you would like to delete. **IMPORTANT**: the path must not start with a slash"""
    args_schema: Type[BaseModel] = DeleteFileInput

    def execute(self, file_path: str, commit_message: str, *args):
        validate_github_wrapper(self.api_wrapper, self.credentials)
        logger.debug("List branches in the repository.")
        return self.api_wrapper.delete_file(file_path, commit_message)


class CreateFileInput(BaseModel):
    file_path: str = Field(
        description="""File path of file to be created. e.g. `src/agents/developer/tools/git/github_tools.py`.
    **IMPORTANT**: the path must not start with a slash"""
    )
    file_contents: str = Field(
        description="""
    Full file content to be created. It must be without any escapes, just raw content to CREATE in GIT.
    Generate full file content for this field without any additional texts, escapes, just raw code content.
    You MUST NOT ignore, skip or comment any details, PROVIDE FULL CONTENT including all content based on all best practices.
    """
    )
    commit_message: str = Field(
        description="""This commit message can be customized by user
        to provide more context about the changes made in the file.
        If user doesn't provide any message, default message will be used: 'Create file {file_path}'"""
    )


class CreateFileTool(CodeMieTool):
    """Tool for interacting with the GitHub API."""

    api_wrapper: Optional[CustomGitHubAPIWrapper] = Field(exclude=True)
    credentials: GitCredentials
    name: str = CREATE_FILE_TOOL.name
    description: str = """This tool is a wrapper for the GitHub API, useful when you need to create a file in a GitHub repository.

    """
    args_schema: Type[BaseModel] = CreateFileInput

    def execute(self, file_path: str, file_contents: str, commit_message: str, *args):
        validate_github_wrapper(self.api_wrapper, self.credentials)
        return self.api_wrapper.create_file(file_path + "\n " + file_contents, commit_message)


class UpdateFileInput(BaseModel):
    file_path: str = Field(
        description="""File path of file to be updated. e.g. `src/agents/developer/tools/git/github_tools.py`.
    **IMPORTANT**: the path must not start with a slash"""
    )
    task_details: str = Field(
        description="""String. Specify detailed task description for file which must be updated
    or provide detailed generated reference what should be done"""
    )
    commit_message: str = Field(
        description="""This commit message can be customized by user
    to provide more context about the changes made in the file.
    If user doesn't provide any message, default message will be used: 'Update file {file_path}'"""
    )


class UpdateFileGitHubTool(UpdateFileGitTool):
    api_wrapper: Optional[CustomGitHubAPIWrapper] = Field(exclude=True)
    description: str = """This tool is a wrapper for the GitHub API, useful when you need to update a file in a
    GitHub repository."""
    args_schema: Type[BaseModel] = UpdateFileInput

    def read_file(self, file_path):
        validate_github_wrapper(self.api_wrapper, self.credentials)
        return self.api_wrapper.read_file(file_path)

    def update_file(self, file_path, new_content, commit_message):
        self.api_wrapper.github_repo_instance.update_file(
            path=file_path,
            message=commit_message,
            content=new_content,
            branch=self.api_wrapper.active_branch,
            sha=self.api_wrapper.github_repo_instance.get_contents(file_path, ref=self.api_wrapper.active_branch).sha,
        )

    @abstractmethod
    def update_content(self, legacy_content, task_details):
        pass


class OpenAIUpdateFileWholeTool(UpdateFileGitHubTool):
    name: str = UPDATE_FILE_TOOL.name
    llm_model: BaseChatModel = Field(exclude=True)

    def update_content(self, legacy_content, task_details):
        updated_content = self._chain.invoke(
            input={
                "question": task_details,
                "context": legacy_content,
            }
        )
        return self._parse_response(updated_content), ""

    def _parse_response(self, response: str):
        try:
            if response.startswith("```"):
                return response.split("\n", 1)[1]
            else:
                return response
        except Exception as e:
            logger.error(f"Error during parsing new content: {e}")
        return response

    @property
    def _chain(self):
        chain = (
            {
                "question": itemgetter("question"),
                "context": itemgetter("context"),
            }
            | UPDATE_CONTENT_PROMPT
            | self.llm_model
            | StrOutputParser()
        )
        return chain


class OpenAIUpdateFileDiffTool(UpdateFileGitHubTool):
    name: str = UPDATE_FILE_DIFF_TOOL.name
    llm_model: BaseChatModel = Field(exclude=True)

    def update_content(self, legacy_content, task_details):
        return update_content_by_task(legacy_content, task_details, self.llm_model)
