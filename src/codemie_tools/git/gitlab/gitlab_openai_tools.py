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
from abc import abstractmethod
from operator import itemgetter
from typing import Type, Dict, Union, Optional

from gitlab.exceptions import GitlabGetError
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.utils import parse_tool_input
from codemie_tools.code.coder.diff_update_coder import update_content_by_task
from codemie_tools.git.gitlab.custom_gitlab_api_wrapper import CustomGitLabAPIWrapper
from codemie_tools.git.gitlab.tools_vars import UPDATE_FILE_TOOL, UPDATE_FILE_DIFF_TOOL
from codemie_tools.git.prompts import UPDATE_CONTENT_PROMPT
from codemie_tools.git.tools import UpdateFileGitTool
from codemie_tools.git.tools_models import ListBranchesToolInput
from codemie_tools.git.utils import validate_gitlab_wrapper, GitCredentials

logger = logging.getLogger(__name__)


class BranchInput(BaseModel):
    """Schema for operations that require a branch name as input."""

    branch_name: str = Field(description="The name of the branch, e.g. `my_branch`.")


class CreateGitLabBranchTool(CodeMieTool):
    api_wrapper: Optional[CustomGitLabAPIWrapper] = Field(exclude=True, default=None)
    credentials: GitCredentials
    name: str = "create_branch"
    description: str = """This tool is a wrapper for the GitLab API to create a new branch in the repository."""
    args_schema: Type[BaseModel] = BranchInput

    def execute(self, branch_name: str, *args):
        validate_gitlab_wrapper(self.api_wrapper, self.credentials)
        return self.api_wrapper.create_branch(branch_name)


class CreatePRInput(BaseModel):
    pr_title: str = Field(description="Title of pull request. Maybe generated from made changes in the branch.")
    pr_body: str = Field(description="Body or description of the pull request of made changes.")


class CreatePRTool(CodeMieTool):
    api_wrapper: Optional[CustomGitLabAPIWrapper] = Field(exclude=True, default=None)
    credentials: GitCredentials
    name: str = "create_pull_request"
    description: str = """This tool is a wrapper for the GitLab API to create a new pull request in a GitLab repository.
    Strictly follow and provide input parameters based on context.
    """
    args_schema: Type[BaseModel] = CreatePRInput

    def execute(self, pr_title: str, pr_body: str, *args):
        validate_gitlab_wrapper(self.api_wrapper, self.credentials)
        return self.api_wrapper.create_pull_request(pr_title + "\n\n" + pr_body)


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
    api_wrapper: Optional[CustomGitLabAPIWrapper] = Field(exclude=True, default=None)
    credentials: GitCredentials
    name: str = "delete_file"
    description: str = """This tool is a wrapper for the GitLab API, useful when you need to delete a file in a GitLab repository.
    Simply pass in the full file path of the file you would like to delete. **IMPORTANT**: the path must not start with a slash"""
    args_schema: Type[BaseModel] = DeleteFileInput

    def execute(self, file_path: str, commit_message: str, *args):
        validate_gitlab_wrapper(self.api_wrapper, self.credentials)
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
    api_wrapper: Optional[CustomGitLabAPIWrapper] = Field(exclude=True, default=None)
    credentials: GitCredentials
    name: str = "create_file"
    description: str = """This tool is a wrapper for the GitLab API, useful when you need to create a file in a GitLab repository.
    """
    args_schema: Type[BaseModel] = CreateFileInput

    def execute(self, file_path: str, file_contents: str, commit_message: str, *args):
        validate_gitlab_wrapper(self.api_wrapper, self.credentials)
        return self.api_wrapper.create_file(file_path + "\n " + file_contents, commit_message)


class SetActiveBranchTool(CodeMieTool):
    api_wrapper: Optional[CustomGitLabAPIWrapper] = Field(exclude=True, default=None)
    credentials: GitCredentials
    name: str = "set_active_branch"
    description: str = """
    This tool is a wrapper for the Git API and set the active branch in the repository, similar to `git checkout <branch_name>` and `git switch -c <branch_name>`."""
    args_schema: Type[BaseModel] = BranchInput

    def execute(self, branch_name: str, *args):
        validate_gitlab_wrapper(self.api_wrapper, self.credentials)
        return self.api_wrapper.set_active_branch(branch_name=branch_name)


class ListBranchesTool(CodeMieTool):
    """Tool for interacting with the GitHub API."""

    api_wrapper: Optional[CustomGitLabAPIWrapper] = Field(exclude=True, default=None)
    credentials: GitCredentials
    name: str = "list_branches_in_repo"
    description: str = """This tool is a wrapper for the Git API to fetch a list of all branches in the repository.
    It will return the name of each branch. No input parameters are required."""
    args_schema: Optional[Type[BaseModel]] = ListBranchesToolInput

    def execute(self, *args):
        validate_gitlab_wrapper(self.api_wrapper, self.credentials)
        return self.api_wrapper.list_branches_in_repo()


class UpdateFileInput(BaseModel):
    file_path: str = Field(
        description="""File path of file to be updated. e.g. `src/agents/developer/tools/git/github_tools.py`.
    **IMPORTANT**: the path must not start with a slash"""
    )
    task_details: str = Field(
        description="""String. Specify detailed task description for
    file which must be updated or provide detailed generated reference what should be done"""
    )
    commit_message: str = Field(
        description="""This commit message can be customized by user
    to provide more context about the changes made in the file.
    If user doesn't provide any message, default message will be used: 'Update file {file_path}'"""
    )


class UpdateFileGitLabTool(UpdateFileGitTool):
    api_wrapper: Optional[CustomGitLabAPIWrapper] = Field(exclude=True, default=None)
    description: str = """This tool is a wrapper for the GitHub API, useful when you need to update a file in a
    GitHub repository."""
    args_schema: Type[BaseModel] = UpdateFileInput

    def read_file(self, file_path):
        validate_gitlab_wrapper(self.api_wrapper, self.credentials)
        return self.api_wrapper.read_file(file_path)

    def update_file(self, file_path, new_content, commit_message):
        self.api_wrapper.replace_file_content(file_path + "\n" + new_content, commit_message)

    @abstractmethod
    def update_content(self, legacy_content, task_details):
        pass


class OpenAIUpdateFileWholeTool(UpdateFileGitLabTool):
    """Tool for interacting with the GitHub API."""

    name: str = UPDATE_FILE_TOOL.name
    description: str = UPDATE_FILE_TOOL.description
    llm_model: BaseChatModel = Field(exclude=True)
    update_prompt: PromptTemplate = UPDATE_CONTENT_PROMPT

    def update_content(self, legacy_content, task_details):
        updated_content = self._chain.invoke(
            input={
                "question": task_details,
                "context": legacy_content,
            }
        )

        return self._parse_response(updated_content), ""

    def _parse_input(self, tool_input: Union[str, Dict], tool_call_id: Optional[str], *args, **kwargs):
        return parse_tool_input(self.args_schema, tool_input)

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
            | self.update_prompt
            | self.llm_model
            | StrOutputParser()
        )
        return chain


class OpenAIUpdateFileDiffTool(UpdateFileGitLabTool):
    """Tool for interacting with the GitHub API."""

    name: str = UPDATE_FILE_DIFF_TOOL.name
    description: str = UPDATE_FILE_DIFF_TOOL.description
    llm_model: BaseChatModel = Field(exclude=True)

    def update_content(self, legacy_content, task_details):
        return update_content_by_task(legacy_content, task_details, self.llm_model)


def get_position(line_number, file_path, mr):
    changes = mr.changes()["changes"]
    # Get first change
    change = next((item for item in changes if item.get("new_path") == file_path), None)
    if change is None:
        change = next((item for item in changes if item.get("old_path") == file_path), None)
    if change is None:
        raise ValueError(f"Change for file {file_path} wasn't found in PR")

    position = get_diff_w_position(change=change)[line_number][0]

    position.update(
        {
            "base_sha": mr.diff_refs["base_sha"],
            "head_sha": mr.diff_refs["head_sha"],
            "start_sha": mr.diff_refs["start_sha"],
            'position_type': 'text',
        }
    )

    return position


def get_diff_w_position(change):
    diff = change["diff"]
    diff_with_ln = {}
    # Regular expression to extract old and new line numbers
    pattern = r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@"
    # GitLab API requires new path and line for added lines, old path and files for removed lines.
    # For unchaged lines it requires both.
    for index, line in enumerate(diff.split("\n")):
        position = {}
        match = re.match(pattern, line)
        if match:
            old_line = int(match.group(1))
            new_line = int(match.group(3))
        elif line.startswith("+"):
            position["new_line"] = new_line
            position["new_path"] = change["new_path"]
            new_line += 1
        elif line.startswith("-"):
            position["old_line"] = old_line
            position["old_path"] = change["old_path"]
            old_line += 1
        elif line.startswith(" "):
            position["old_line"] = old_line
            position["old_path"] = change["old_path"]
            position["new_line"] = new_line
            position["new_path"] = change["new_path"]
            new_line += 1
            old_line += 1
        elif line.startswith("\\"):
            # Assign previos position to \\ metadata
            position = diff_with_ln[index - 1][0]
        else:
            # Stop at final empty line
            break

        diff_with_ln[index] = [position, line]

        # Assign next position to @@ metadata
        if index > 0 and diff_with_ln[index - 1][1].startswith("@"):
            diff_with_ln[index - 1][0] = position

    return diff_with_ln


class GetPullRequesChangesInput(BaseModel):
    pr_number: str = Field(description="""GitLab Merge Request (Pull Request) number""")


class GetPullRequesChanges(CodeMieTool):
    api_wrapper: Optional[CustomGitLabAPIWrapper] = Field(exclude=True, default=None)
    credentials: GitCredentials
    name: str = "get_pr_changes"
    description: str = """This tool is a wrapper for the GitLab API, useful when you need to get all the changes from pull request in git diff format with added line numbers.
    """
    args_schema: Type[BaseModel] = GetPullRequesChangesInput
    handle_tool_error: bool = True

    def execute(self, pr_number: str, *args):
        validate_gitlab_wrapper(self.api_wrapper, self.credentials)
        try:
            repo = self.api_wrapper.gitlab_repo_instance
            try:
                mr = repo.mergerequests.get(pr_number)
            except GitlabGetError as e:
                if e.response_code == 404:
                    raise ToolException(f"Merge request number {pr_number} wasn't found: {e}")

            res = f"""title: {mr.title}\ndescription: {mr.description}\n\n"""

            for change in mr.changes()["changes"]:
                diff_w_position = get_diff_w_position(change=change)
                diff = "\n".join([str(line_num) + ":" + line[1] for line_num, line in diff_w_position.items()])

                res = res + f"""diff --git a/{change["old_path"]} b/{change["new_path"]}\n{diff}\n"""

            return res
        except ToolException:
            raise
        except Exception as e:
            raise ToolException(f"An error occurred: {e}")


class CreatePullRequestChangeCommentInput(BaseModel):
    pr_number: str = Field(description="""GitLab Merge Request (Pull Request) number""")
    file_path: str = Field(description="""File path of the changed file""")
    line_number: int = Field(description="""Line number from the diff for a changed file""")
    comment: str = Field(description="""Comment content""")


class CreatePullRequestChangeComment(CodeMieTool):
    api_wrapper: Optional[CustomGitLabAPIWrapper] = Field(exclude=True, default=None)
    credentials: GitCredentials
    name: str = "create_pr_change_comment"
    description: str = """This tool is a wrapper for the GitLab API, useful when you need to create a comment on a pull request change.
    """
    args_schema: Type[BaseModel] = CreatePullRequestChangeCommentInput
    handle_tool_error: bool = True

    def execute(self, pr_number: str, file_path: str, line_number: int, comment: str, *args):
        validate_gitlab_wrapper(self.api_wrapper, self.credentials)
        repo = self.api_wrapper.gitlab_repo_instance
        try:
            mr = repo.mergerequests.get(pr_number)
        except GitlabGetError as e:
            if e.response_code == 404:
                raise ToolException(f"Merge request number {pr_number} wasn't found: {e}")
        try:
            position = get_position(file_path=file_path, line_number=line_number, mr=mr)

            mr.discussions.create({"body": comment, "position": position})
            return "Comment added"
        except Exception as e:
            raise ToolException(f"An error occurred: {e}")
