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

from atlassian.bitbucket.cloud.repositories import Repository
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field
from requests import HTTPError

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.code.coder.diff_update_coder import update_content_by_task
from codemie_tools.git.bitbucket.tools_vars import (
    LIST_BRANCHES_TOOL,
    CREATE_GIT_BRANCH_TOOL,
    CREATE_PULL_REQUEST_TOOL,
    DELETE_FILE_TOOL,
    CREATE_FILE_TOOL,
    UPDATE_FILE_TOOL,
    UPDATE_FILE_DIFF_TOOL,
    SET_ACTIVE_BRANCH_TOOL,
    CREATE_PR_CHANGE_COMMENT,
    GET_PR_CHANGES,
)
from codemie_tools.git.prompts import UPDATE_CONTENT_PROMPT
from codemie_tools.git.tools import UpdateFileGitTool
from codemie_tools.git.tools_models import ListBranchesToolInput
from codemie_tools.git.utils import GitCredentials, validate_bitbucket

logger = logging.getLogger(__name__)


class BranchInput(BaseModel):
    """Schema for operations that require a branch name as input."""

    branch_name: str = Field(description="The name of the branch, e.g. `my_branch`.")


class CreateBitbucketBranchTool(CodeMieTool):
    """Tool for creating branch in Bitbucket."""

    repo_wrapper: Optional[Repository] = Field(exclude=True)
    credentials: GitCredentials
    name: str = CREATE_GIT_BRANCH_TOOL.name
    description: str = """This tool is a wrapper for the Bitbucket API to create a new branch in the repository."""
    args_schema: Type[BaseModel] = BranchInput

    def execute(self, branch_name: str, *args):
        validate_bitbucket(self.repo_wrapper, self.credentials)
        created_branch = self.repo_wrapper.branches.create(name=branch_name, commit=self.repo_wrapper.base_branch)
        self.repo_wrapper.active_branch = created_branch.name
        return f"Branch '{created_branch.name}' created successfully, and set as current active branch."


class SetActiveBranchTool(CodeMieTool):
    """Tool for setting active branch in Bitbucket."""

    repo_wrapper: Optional[Repository] = Field(exclude=True)
    credentials: GitCredentials
    name: str = SET_ACTIVE_BRANCH_TOOL.name
    description: str = """
    This tool is a wrapper for setting the active branch in the Bitbucket repository,
    similar to `git checkout <branch_name>` and `git switch -c <branch_name>`."""
    args_schema: Type[BaseModel] = BranchInput

    def execute(self, branch_name: str, *args):
        validate_bitbucket(self.repo_wrapper, self.credentials)
        try:
            self.repo_wrapper.active_branch = self.repo_wrapper.branches.get(branch_name).name
            return f"Switched to branch '{branch_name}'."
        except HTTPError as e:
            if e.response.status_code == 404:
                return f"Error {branch_name} does not exist,"
            else:
                return str(e)


class ListBranchesTool(CodeMieTool):
    """Tool for listing branches in Bitbucket."""

    repo_wrapper: Optional[Repository] = Field(exclude=True)
    credentials: GitCredentials
    name: str = LIST_BRANCHES_TOOL.name
    description: str = """
    This tool is a wrapper for the Bitbucket API to fetch a list of all branches in the repository.
    It will return the name of each branch. No input parameters are required."""
    args_schema: Optional[Type[BaseModel]] = ListBranchesToolInput

    def execute(self, query: str = "", **kwargs):
        validate_bitbucket(self.repo_wrapper, self.credentials)
        branches = [branch.name for branch in self.repo_wrapper.branches.each()]
        if branches:
            branches_str = "\n".join(branches)
            return f"Found {len(branches)} branches in the repository:" f"\n{branches_str}"
        else:
            return "No branches found in the repository"


class CreatePRInput(BaseModel):
    pr_title: str = Field(description="Title of pull request. Maybe generated from made changes in the branch.")
    pr_body: str = Field(description="Body or description of the pull request of made changes.")
    source_branch: str = Field(
        description="Source branch of the pull request. Default is the default branch of the repository."
    )


class CreatePRTool(CodeMieTool):
    """Tool for creating pull request in Bitbucket."""

    repo_wrapper: Optional[Repository] = Field(exclude=True)
    credentials: GitCredentials
    name: str = CREATE_PULL_REQUEST_TOOL.name
    description: str = """
    This tool is a wrapper for the Git API to create a new pull request in a Bitbucket repository.
    Strictly follow and provide input parameters based on context.
    """
    args_schema: Type[BaseModel] = CreatePRInput

    def execute(self, pr_title: str, pr_body: str, source_branch: str, *args):
        validate_bitbucket(self.repo_wrapper, self.credentials)
        created_pr = self.repo_wrapper.pullrequests.create(
            pr_title, source_branch, self.repo_wrapper.base_branch, pr_body
        )
        return f"Successfully created PR number {created_pr.id}"


class DeleteFileInput(BaseModel):
    file_path: str = Field(
        description="""File path of file to be deleted. e.g. `src/agents/developer/tools/git/bitbucket_tools.py`.
        **IMPORTANT**: the path must not start with a slash"""
    )
    commit_message: str = Field(
        description="""This commit message can be customized by user
        to provide more context about the deletion of the file.
        If user doesn't provide any message, default message will be used: 'Delete file {file_path}'"""
    )


class DeleteFileTool(CodeMieTool):
    """Tool for deleting a file in Bitbucket."""

    repo_wrapper: Optional[Repository] = Field(exclude=True)
    credentials: GitCredentials
    name: str = DELETE_FILE_TOOL.name
    description: str = """This tool is a wrapper for the BitBucket API, useful when you need to delete a file in a BitBucket repository.
    Simply pass in the full file path of the file you would like to delete. **IMPORTANT**: the path must not start with a slash"""
    args_schema: Type[BaseModel] = DeleteFileInput

    def execute(self, file_path: str, commit_message: str, *args):
        validate_bitbucket(self.repo_wrapper, self.credentials)

        self.repo_wrapper.sources.create(self.repo_wrapper.active_branch, commit_message, file_path)
        return f"Successfully deleted file {file_path}"


class CreateFileInput(BaseModel):
    file_path: str = Field(
        description="""File path of file to be created. e.g. `src/agents/developer/tools/git/bitbucket_tools.py`.
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
    """Tool for creating a file in Bitbucket."""

    repo_wrapper: Optional[Repository] = Field(exclude=True)
    credentials: GitCredentials
    name: str = CREATE_FILE_TOOL.name
    description: str = """This tool is a wrapper for the Bitbucket API, useful when you need to create a file in a Bitbucket repository.
    """
    args_schema: Type[BaseModel] = CreateFileInput

    def execute(self, file_path: str, file_contents: str, commit_message: str, *args):
        validate_bitbucket(self.repo_wrapper, self.credentials)
        self.repo_wrapper.sources.create(
            self.repo_wrapper.active_branch,
            commit_message,
            file_path_to_create_or_update=file_path,
            file_content_to_create_or_update=file_contents,
        )
        return f"Successfully created file {file_path}"


class UpdateFileInput(BaseModel):
    file_path: str = Field(
        description="""File path of file to be updated. e.g. `src/agents/developer/tools/git/bitbucket_tools.py`.
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


class UpdateFileBitbucketTool(UpdateFileGitTool):
    """Tool for updating a file in Bitbucket."""

    repo_wrapper: Optional[Repository] = Field(exclude=True)
    description: str = """This tool is a wrapper for the Bitbucket API, useful when you need to update a
    file in a Bitbucket repository."""
    args_schema: Type[BaseModel] = UpdateFileInput

    def read_file(self, file_path):
        validate_bitbucket(self.repo_wrapper, self.credentials)
        return self.repo_wrapper.sources.read_file_or_directory_contents(self.repo_wrapper.active_branch, file_path)

    def update_file(self, file_path, new_content, commit_message):
        validate_bitbucket(self.repo_wrapper, self.credentials)
        self.repo_wrapper.sources.create(
            self.repo_wrapper.active_branch,
            commit_message,
            file_path_to_create_or_update=file_path,
            file_content_to_create_or_update=new_content,
        )

    @abstractmethod
    def update_content(self, legacy_content, task_details):
        pass


class OpenAIUpdateFileWholeTool(UpdateFileBitbucketTool):
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


class OpenAIUpdateFileDiffTool(UpdateFileBitbucketTool):
    name: str = UPDATE_FILE_DIFF_TOOL.name
    llm_model: BaseChatModel = Field(exclude=True)

    def update_content(self, legacy_content, task_details):
        return update_content_by_task(legacy_content, task_details, self.llm_model)


class GetPullRequestChangesInput(BaseModel):
    pr_number: str = Field(description="""GitLab Merge Request (Pull Request) number""")


class GetPullRequestChanges(CodeMieTool):
    """Tool for getting pull request changes."""

    repo_wrapper: Optional[Repository] = Field(exclude=True)
    credentials: GitCredentials
    name: str = GET_PR_CHANGES.name
    description: str = """This tool is a wrapper for the Bitbucket API, useful when you need to get
    all the changes from pull request in git diff format with added line numbers.
    """
    args_schema: Type[BaseModel] = GetPullRequestChangesInput
    handle_tool_error: bool = True

    def execute(self, pr_number: str, *args):
        validate_bitbucket(self.repo_wrapper, self.credentials)
        try:
            mr = self.repo_wrapper.pullrequests.get(pr_number)

            return f"""title: {mr.title}\ndescription: {mr.description}\n\n{mr.diff()}\n"""
        except HTTPError as e:
            if e.response.status_code == 404:
                raise ToolException(f"Merge request number {pr_number} wasn't found: {e}")


class CreatePullRequestChangeCommentInput(BaseModel):
    pr_number: str = Field(description="""GitLab Merge Request (Pull Request) number""")
    file_path: str = Field(description="""File path of the changed file""")
    line_number: int = Field(description="""Line number from the diff for a changed file""")
    comment: str = Field(description="""Comment content""")


class CreatePullRequestChangeComment(CodeMieTool):
    """Tool for creating a pull request comment."""

    repo_wrapper: Optional[Repository] = Field(exclude=True)
    credentials: GitCredentials
    name: str = CREATE_PR_CHANGE_COMMENT.name
    description: str = """This tool is a wrapper for the Bitbucket API, useful when you need to create
    a comment on a pull request change.
    """
    args_schema: Type[BaseModel] = CreatePullRequestChangeCommentInput
    handle_tool_error: bool = True

    def execute(self, pr_number: str, file_path: str, line_number: int, comment: str, *args):
        validate_bitbucket(self.repo_wrapper, self.credentials)
        try:
            mr = self.repo_wrapper.pullrequests.get(pr_number)

            data = {"file_path": file_path, "line_number": line_number, "content": comment}

            return mr.comment(data)
        except HTTPError as e:
            if e.response.status_code == 404:
                raise ToolException(f"Merge request number {pr_number} wasn't found: {e}")
            else:
                raise e
