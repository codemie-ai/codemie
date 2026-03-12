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
from typing import Type, Optional

from azure.devops.v7_0.git import CommentPosition, CommentThreadContext
from azure.devops.v7_0.git.models import (
    GitRefUpdate,
    GitVersionDescriptor,
    GitCommit,
    GitCommitRef,
    GitPush,
    GitPullRequestCommentThread,
    GitPullRequestSearchCriteria,
    Comment,
)
from pydantic import BaseModel, Field, ConfigDict

from codemie_tools.base.codemie_tool import CodeMieTool, logger
from codemie_tools.git.azure_devops.client import AzureDevOpsClient, AzureDevOpsCredentials
from codemie_tools.git.azure_devops.tools_vars import (
    LIST_BRANCHES_TOOL,
    SET_ACTIVE_BRANCH_TOOL,
    LIST_FILES_TOOL,
    LIST_OPEN_PULL_REQUESTS_TOOL,
    GET_PULL_REQUEST_TOOL,
    LIST_PULL_REQUEST_FILES_TOOL,
    CREATE_BRANCH_TOOL,
    READ_FILE_TOOL,
    CREATE_FILE_TOOL,
    UPDATE_FILE_TOOL,
    DELETE_FILE_TOOL,
    GET_WORK_ITEMS_TOOL,
    COMMENT_ON_PULL_REQUEST_TOOL,
    CREATE_PULL_REQUEST_TOOL,
)
from codemie_tools.git.azure_devops.utils import extract_old_new_pairs, generate_diff

BRANCH_NAME_DESC = "The name of the branch, e.g. `my_branch`."


class GitChange:
    """
    Custom GitChange class for Azure DevOps Git API
    """

    def __init__(self, change_type, item_path, content=None, content_type="rawtext"):
        self.change_type = change_type
        self.item = {"path": item_path}
        if content and content_type:
            self.new_content = {"content": content, "contentType": content_type}
        else:
            self.new_content = None

    def to_dict(self):
        change_dict = {"changeType": self.change_type, "item": self.item}
        if self.new_content:
            change_dict["newContent"] = self.new_content
        return change_dict


class BranchInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    """Schema for operations that require a branch name as input."""
    branch_name: str = Field(description=BRANCH_NAME_DESC)


class ListBranchesTool(CodeMieTool):
    """Tool for listing branches in Azure DevOps repository"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = LIST_BRANCHES_TOOL.name
    description: str = LIST_BRANCHES_TOOL.description
    args_schema: Optional[Type[BaseModel]] = None

    def execute(self, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        try:
            branches = [
                branch.name
                for branch in self.client.client.get_branches(
                    repository_id=self.client.repository_id, project=self.client.project
                )
            ]
            if branches:
                branches_str = "\n".join(branches)
                return f"Found {len(branches)} branches in the repository:" f"\n{branches_str}"
            else:
                return "No branches found in the repository"
        except Exception as e:
            return f"Error during attempt to fetch the list of branches: {str(e)}"


class SetActiveBranchTool(CodeMieTool):
    """Tool for setting the active branch in Azure DevOps repository"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = SET_ACTIVE_BRANCH_TOOL.name
    description: str = SET_ACTIVE_BRANCH_TOOL.description
    args_schema: Type[BaseModel] = BranchInput

    def execute(self, branch_name: str, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        current_branches = [
            branch.name
            for branch in self.client.client.get_branches(
                repository_id=self.client.repository_id, project=self.client.project
            )
        ]
        if branch_name in current_branches:
            self.client.active_branch = branch_name
            return f"Switched to branch `{branch_name}`"
        else:
            msg = f"Error {branch_name} does not exist, " + f"in repo with current branches: {str(current_branches)}"
            return msg


class ListFilesInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    directory_path: str = Field(
        default="",
        description="Path to the directory, e.g. `some_dir/inner_dir`. Only input a string, do not include the parameter name.",
    )
    branch_name: Optional[str] = Field(
        default=None, description="Repository branch. If None then active branch will be selected."
    )


class ListFilesTool(CodeMieTool):
    """Tool for listing files in a directory in Azure DevOps repository"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = LIST_FILES_TOOL.name
    description: str = LIST_FILES_TOOL.description
    args_schema: Type[BaseModel] = ListFilesInput

    def execute(self, directory_path: str = "", branch_name: Optional[str] = None, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        branch_to_use = branch_name if branch_name else self.client.active_branch

        try:
            version_descriptor = GitVersionDescriptor(version=branch_to_use, version_type="branch")
            items = self.client.client.get_items(
                repository_id=self.client.repository_id,
                project=self.client.project,
                scope_path=directory_path,
                recursion_level="Full",
                version_descriptor=version_descriptor,
                include_content_metadata=True,
            )

            files = []
            for item in items:
                if item.git_object_type == "blob":
                    files.append(item.path)

            return str(files)
        except Exception as e:
            msg = f"Failed to fetch files from directory due to an error: {str(e)}"
            logger.error(msg)
            return msg


class ListOpenPullRequestsTool(CodeMieTool):
    """Tool for listing open pull requests in Azure DevOps repository"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = LIST_OPEN_PULL_REQUESTS_TOOL.name
    description: str = LIST_OPEN_PULL_REQUESTS_TOOL.description
    args_schema: Optional[Type[BaseModel]] = None

    def execute(self, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        try:
            pull_requests = self.client.client.get_pull_requests(
                repository_id=self.client.repository_id,
                search_criteria=GitPullRequestSearchCriteria(repository_id=self.client.repository_id, status="active"),
                project=self.client.project,
            )

            if pull_requests:
                parsed_prs = self._parse_pull_requests(pull_requests)
                parsed_prs_str = "Found " + str(len(parsed_prs)) + " open pull requests:\n" + str(parsed_prs)
                return parsed_prs_str
            else:
                return "No open pull requests available"
        except Exception as e:
            msg = f"Error during attempt to get active pull request: {str(e)}"
            logger.error(msg)
            return msg

    def _parse_pull_requests(self, pull_requests):
        """Extract information from pull requests"""
        if not isinstance(pull_requests, list):
            pull_requests = [pull_requests]

        parsed = []
        try:
            for pull_request in pull_requests:
                comment_threads = self.client.client.get_threads(
                    repository_id=self.client.repository_id,
                    pull_request_id=pull_request.pull_request_id,
                    project=self.client.project,
                )

                commits = self.client.client.get_pull_request_commits(
                    repository_id=self.client.repository_id,
                    project=self.client.project,
                    pull_request_id=pull_request.pull_request_id,
                )

                commit_details = [{"commit_id": commit.commit_id, "comment": commit.comment} for commit in commits]

                parsed.append(
                    {
                        "title": pull_request.title,
                        "pull_request_id": pull_request.pull_request_id,
                        "commits": commit_details,
                        "comments": self._parse_pull_request_comments(comment_threads),
                    }
                )
        except Exception as e:
            msg = f"Failed to parse pull requests. Error: {str(e)}"
            logger.error(msg)
            return msg

        return parsed

    def _parse_pull_request_comments(self, comment_threads):
        """Extract comments from comment threads"""
        parsed_comments = []
        for thread in comment_threads:
            for comment in thread.comments:
                parsed_comments.append(
                    {
                        "id": comment.id,
                        "author": comment.author.display_name,
                        "content": comment.content,
                        "published_date": comment.published_date.strftime("%Y-%m-%d %H:%M:%S %Z")
                        if comment.published_date
                        else None,
                        "status": thread.status if thread.status else None,
                    }
                )
        return parsed_comments


class GetPullRequestInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    pull_request_id: int = Field(description="The PR number as a string, e.g. `12`")


class GetPullRequestTool(CodeMieTool):
    """Tool for getting a specific pull request in Azure DevOps repository"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = GET_PULL_REQUEST_TOOL.name
    description: str = GET_PULL_REQUEST_TOOL.description
    args_schema: Type[BaseModel] = GetPullRequestInput

    def execute(self, pull_request_id: int, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        try:
            pull_request = self.client.client.get_pull_request_by_id(
                project=self.client.project, pull_request_id=pull_request_id
            )

            if pull_request:
                # Reuse the parsing logic from ListOpenPullRequestsTool
                list_tool = ListOpenPullRequestsTool(client=self.client, credentials=self.credentials)
                parsed_pr = list_tool._parse_pull_requests(pull_request)
                return parsed_pr
            else:
                return f"Pull request with '{pull_request_id}' ID is not found"
        except Exception as e:
            msg = f"Failed to find pull request with '{pull_request_id}' ID. Error: {e}"
            logger.error(msg)
            return msg


class ListPullRequestFilesTool(CodeMieTool):
    """Tool for listing files in a pull request in Azure DevOps repository"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = LIST_PULL_REQUEST_FILES_TOOL.name
    description: str = LIST_PULL_REQUEST_FILES_TOOL.description
    args_schema: Type[BaseModel] = GetPullRequestInput

    def execute(self, pull_request_id: str, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        try:
            pull_request_id = int(pull_request_id)
        except Exception as e:
            return f"Passed argument is not INT type: {pull_request_id}.\nError: {str(e)}"

        try:
            pr_iterations = self.client.client.get_pull_request_iterations(
                repository_id=self.client.repository_id,
                project=self.client.project,
                pull_request_id=pull_request_id,
            )
            last_iteration_id = pr_iterations[-1].id

            changes = self.client.client.get_pull_request_iteration_changes(
                repository_id=self.client.repository_id,
                project=self.client.project,
                pull_request_id=pull_request_id,
                iteration_id=last_iteration_id,
            )
        except Exception as e:
            msg = f"Error during attempt to get Pull Request iterations and changes.\nError: {str(e)}"
            logger.error(msg)
            return msg

        data = []
        source_commit_id = pr_iterations[-1].source_ref_commit.commit_id
        target_commit_id = pr_iterations[-1].target_ref_commit.commit_id

        for change in changes.change_entries:
            path = change.additional_properties["item"]["path"]
            change_type = change.additional_properties["changeType"]

            # it should reflects VersionControlChangeType enum,
            # but the model is not accessible in azure.devops.v7_0.git.models
            if change_type == "edit":
                base_content = self._get_file_content(target_commit_id, path)
                target_content = self._get_file_content(source_commit_id, path)
                diff = generate_diff(base_content, target_content, path)
            else:
                diff = f"Change Type: {change_type}"

            data.append({"path": path, "diff": diff})

        import json

        return json.dumps(data)

    def _get_file_content(self, commit_id, path):
        """Get the content of a file at a specific commit"""
        version_descriptor = GitVersionDescriptor(version=commit_id, version_type="commit")
        try:
            content_generator = self.client.client.get_item_text(
                repository_id=self.client.repository_id,
                project=self.client.project,
                path=path,
                version_descriptor=version_descriptor,
            )
            content = "".join(chunk.decode("utf-8") for chunk in content_generator)
        except Exception as e:
            msg = f"Failed to get item text. Error: {str(e)}"
            logger.error(msg)
            return msg

        return content


class CreateBranchTool(CodeMieTool):
    """Tool for creating a branch in Azure DevOps repository"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = CREATE_BRANCH_TOOL.name
    description: str = CREATE_BRANCH_TOOL.description
    args_schema: Type[BaseModel] = BranchInput

    def execute(self, branch_name: str, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        new_branch_name = branch_name
        if bool(re.search(r"\s", new_branch_name)):
            return f"Branch '{new_branch_name}' contains spaces." "Please remove them or use special characters"

        # Check if the branch already exists
        if self.client.branch_exists(new_branch_name):
            msg = f"Branch '{new_branch_name}' already exists."
            return msg

        active_branch = self.client.active_branch
        base_branch = self.client.client.get_branch(
            repository_id=self.client.repository_id,
            name=active_branch,
            project=self.client.project,
        )

        try:
            ref_update = GitRefUpdate(
                name=f"refs/heads/{new_branch_name}",
                old_object_id="0000000000000000000000000000000000000000",
                new_object_id=base_branch.commit.commit_id,
            )
            ref_update_list = [ref_update]
            self.client.client.update_refs(
                ref_updates=ref_update_list,
                repository_id=self.client.repository_id,
                project=self.client.project,
            )
            self.client.active_branch = new_branch_name
            return f"Branch '{new_branch_name}' created successfully, and set as current active branch."
        except Exception as e:
            msg = f"Failed to create branch. Error: {str(e)}"
            logger.error(msg)
            return msg


class ReadFileInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    file_path: str = Field(
        description="The full file path of the file you would like to read where the path must NOT start with a slash, e.g. `some_dir/my_file.py`."
    )


class ReadFileTool(CodeMieTool):
    """Tool for reading a file from Azure DevOps repository"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = READ_FILE_TOOL.name
    description: str = READ_FILE_TOOL.description
    args_schema: Type[BaseModel] = ReadFileInput

    def execute(self, file_path: str, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        try:
            version_descriptor = GitVersionDescriptor(version=self.client.active_branch, version_type="branch")
            file_content = self.client.client.get_item_text(
                repository_id=self.client.repository_id,
                project=self.client.project,
                path=file_path,
                version_descriptor=version_descriptor,
            )
            # Azure DevOps API returns a generator of bytes, it should be decoded
            decoded_content = "".join([chunk.decode("utf-8") for chunk in file_content])
            return decoded_content
        except Exception as e:
            msg = f"File not found `{file_path}` on branch " f"`{self.client.active_branch}`. Error: {str(e)}"
            logger.error(msg)
            return msg


class CreateFileInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    branch_name: Optional[str] = Field(description=BRANCH_NAME_DESC, default=None)
    file_path: str = Field(description="Path of a file to be created.")
    file_contents: str = Field(description="Content of a file to be put into chat.")


class CreateFileTool(CodeMieTool):
    """Tool for creating a file in Azure DevOps repository"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = CREATE_FILE_TOOL.name
    description: str = CREATE_FILE_TOOL.description
    args_schema: Type[BaseModel] = CreateFileInput

    def execute(self, file_path: str, file_contents: str, branch_name: Optional[str] = None, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        self.client.active_branch = branch_name if branch_name else self.client.active_branch

        if self.client.active_branch == self.client.base_branch:
            return (
                "You're attempting to commit directly to the "
                f"{self.client.base_branch} branch, which is protected. "
                "Please create a new branch and try again."
            )

        try:
            # Check if file already exists
            try:
                self.client.client.get_item(
                    repository_id=self.client.repository_id,
                    project=self.client.project,
                    path=file_path,
                    version_descriptor=GitVersionDescriptor(version=self.client.active_branch, version_type="branch"),
                )
                return (
                    f"File already exists at `{file_path}` "
                    f"on branch `{self.client.active_branch}`. You must use "
                    "`update_file` to modify it."
                )
            except Exception:
                # Expected behavior, file shouldn't exist yet
                pass

            # Get the latest commit ID of the active branch to use as oldObjectId
            branch = self.client.client.get_branch(
                repository_id=self.client.repository_id,
                project=self.client.project,
                name=self.client.active_branch,
            )

            if branch is None or not hasattr(branch, "commit") or not hasattr(branch.commit, "commit_id"):
                return f"Branch `{self.client.active_branch}` does not exist or has no commits."

            latest_commit_id = branch.commit.commit_id

            change = GitChange("add", file_path, file_contents).to_dict()

            ref_update = GitRefUpdate(name=f"refs/heads/{self.client.active_branch}", old_object_id=latest_commit_id)
            new_commit = GitCommit(comment=f"Create {file_path}", changes=[change])
            push = GitPush(commits=[new_commit], ref_updates=[ref_update])

            self.client.client.create_push(
                push=push, repository_id=self.client.repository_id, project=self.client.project
            )

            return f"Created file {file_path}"
        except Exception as e:
            msg = f"Unable to create file due to error:\n{str(e)}"
            logger.error(msg)
            return msg


class UpdateFileInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    branch_name: str = Field(description=BRANCH_NAME_DESC)
    file_path: str = Field(description="Path of a file to be updated.")
    update_query: str = Field(description="Update query used to adjust target file.")


class UpdateFileTool(CodeMieTool):
    """Tool for updating a file in Azure DevOps repository"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = UPDATE_FILE_TOOL.name
    description: str = UPDATE_FILE_TOOL.description
    args_schema: Type[BaseModel] = UpdateFileInput

    def execute(self, branch_name: str, file_path: str, update_query: str, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        self.client.active_branch = branch_name if branch_name else self.client.active_branch

        if self.client.active_branch == self.client.base_branch:
            return (
                "You're attempting to commit directly to the "
                f"{self.client.base_branch} branch, which is protected. "
                "Please create a new branch and try again."
            )

        try:
            # Read the current file content
            read_tool = ReadFileTool(client=self.client, credentials=self.credentials)
            file_content = read_tool.execute(file_path)

            # If we got an error message instead of file content
            if file_content.startswith("File not found"):
                return file_content

            # Update content based on the query
            updated_file_content = file_content
            for old, new in extract_old_new_pairs(update_query):
                if not old.strip():
                    continue
                updated_file_content = updated_file_content.replace(old, new)

            if file_content == updated_file_content:
                return (
                    "File content was not updated because old content was not found or empty. "
                    "It may be helpful to use the read_file action to get "
                    "the current file contents."
                )

            # Get the latest commit ID of the active branch
            branch = self.client.client.get_branch(
                repository_id=self.client.repository_id,
                project=self.client.project,
                name=self.client.active_branch,
            )
            latest_commit_id = branch.commit.commit_id

            # Create the change and push
            change = GitChange("edit", file_path, updated_file_content).to_dict()
            ref_update = GitRefUpdate(name=f"refs/heads/{self.client.active_branch}", old_object_id=latest_commit_id)
            new_commit = GitCommit(comment=f"Update {file_path}", changes=[change])
            push = GitPush(commits=[new_commit], ref_updates=[ref_update])

            self.client.client.create_push(
                push=push, repository_id=self.client.repository_id, project=self.client.project
            )

            return f"Updated file {file_path}"
        except Exception as e:
            msg = f"Unable to update file due to error:\n{str(e)}"
            logger.error(msg)
            return msg


class DeleteFileInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    branch_name: str = Field(description=BRANCH_NAME_DESC)
    file_path: str = Field(
        description="The full file path of the file you would like to delete where the path must NOT start with a slash, e.g. `some_dir/my_file.py`. Only input a string, not the param name."
    )


class DeleteFileTool(CodeMieTool):
    """Tool for deleting a file from Azure DevOps repository"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = DELETE_FILE_TOOL.name
    description: str = DELETE_FILE_TOOL.description
    args_schema: Type[BaseModel] = DeleteFileInput

    def execute(self, branch_name: str, file_path: str, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        try:
            branch = self.client.client.get_branch(
                repository_id=self.client.repository_id, project=self.client.project, name=branch_name
            )

            if not branch:
                return "Branch not found."

            current_commit_id = branch.commit.commit_id

            # Create the change and push
            change = GitChange("delete", file_path).to_dict()
            new_commit = GitCommitRef(comment=f"Delete {file_path}", changes=[change])
            ref_update = GitRefUpdate(
                name=f"refs/heads/{branch_name}",
                old_object_id=current_commit_id,
                new_object_id=None,
            )
            push = GitPush(commits=[new_commit], ref_updates=[ref_update])

            self.client.client.create_push(
                push=push, repository_id=self.client.repository_id, project=self.client.project
            )

            return f"Deleted file {file_path}"
        except Exception as e:
            msg = f"Unable to delete file due to error:\n{str(e)}"
            logger.error(msg)
            return msg


class GetWorkItemsInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    pull_request_id: str = Field(description="The PR number as a string, e.g. `12`")


class GetWorkItemsTool(CodeMieTool):
    """Tool for getting work items associated with a pull request"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = GET_WORK_ITEMS_TOOL.name
    description: str = GET_WORK_ITEMS_TOOL.description
    args_schema: Type[BaseModel] = GetWorkItemsInput

    def execute(self, pull_request_id: str, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        try:
            pull_request_id_int = int(pull_request_id)
            work_items = self.client.client.get_pull_request_work_item_refs(
                repository_id=self.client.repository_id,
                pull_request_id=pull_request_id_int,
                project=self.client.project,
            )

            work_item_ids = [work_item_ref.id for work_item_ref in work_items[:10]]
            return work_item_ids
        except Exception as e:
            msg = f"Unable to get Work Items due to error:\n{str(e)}"
            logger.error(msg)
            return msg


class CommentOnPullRequestInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    pr_number: int = Field(description="Pull Request number")
    comment: str = Field(description="Comment content")
    file_path: str | None = Field(description="File path of the changed file")
    line_number: int | None = Field(description="Line number for a changed file")


class CommentOnPullRequestTool(CodeMieTool):
    """Tool for commenting on a pull request"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = COMMENT_ON_PULL_REQUEST_TOOL.name
    description: str = COMMENT_ON_PULL_REQUEST_TOOL.description
    args_schema: Type[BaseModel] = CommentOnPullRequestInput

    def execute(
        self,
        pr_number: int,
        comment: str,
        file_path: str | None = None,
        line_number: int | None = None,
        *args,
    ):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        try:
            if file_path and not file_path.startswith("/"):
                file_path = "/" + file_path

            pr_comment = Comment(comment_type="text", content=comment)

            pr_comment_context = None
            if file_path and line_number:
                pr_comment_context = CommentThreadContext(
                    file_path=file_path,
                    right_file_start=CommentPosition(
                        line=line_number,
                        offset=1,
                    ),
                    right_file_end=CommentPosition(
                        line=line_number + 1,
                        offset=1,
                    ),
                )

            comment_thread = GitPullRequestCommentThread(
                comments=[pr_comment],
                status="active",
                thread_context=pr_comment_context,
            )
            self.client.client.create_thread(
                comment_thread,
                repository_id=self.client.repository_id,
                pull_request_id=pr_number,
                project=self.client.project,
            )

            return f"Commented on pull request {pr_number}"
        except Exception as e:
            msg = f"Unable to make comment due to error:\n{str(e)}"
            logger.error(msg)
            return msg


class CreatePullRequestInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    pull_request_title: str = Field(description="Title of the pull request")
    pull_request_body: str = Field(description="Body of the pull request")
    branch_name: str = Field(description=BRANCH_NAME_DESC)


class CreatePullRequestTool(CodeMieTool):
    """Tool for creating a pull request"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Optional[AzureDevOpsClient] = Field(exclude=True, default=None)
    credentials: AzureDevOpsCredentials
    name: str = CREATE_PULL_REQUEST_TOOL.name
    description: str = CREATE_PULL_REQUEST_TOOL.description
    args_schema: Type[BaseModel] = CreatePullRequestInput

    def execute(self, pull_request_title: str, pull_request_body: str, branch_name: str, *args):
        if not self.client:
            self.client = AzureDevOpsClient(self.credentials)

        if self.client.active_branch == branch_name:
            return f"Cannot create a pull request because the source branch '{self.client.active_branch}' is the same as the target branch '{branch_name}'"

        try:
            pull_request = {
                "sourceRefName": f"refs/heads/{self.client.active_branch}",
                "targetRefName": f"refs/heads/{branch_name}",
                "title": pull_request_title,
                "description": pull_request_body,
                "reviewers": [],
            }

            response = self.client.client.create_pull_request(
                git_pull_request_to_create=pull_request,
                repository_id=self.client.repository_id,
                project=self.client.project,
            )

            return f"Successfully created PR with ID {response.pull_request_id}"
        except Exception as e:
            msg = f"Unable to create pull request due to error: {str(e)}"
            logger.error(msg)
            return msg
