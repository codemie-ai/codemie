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

from codemie_tools.base.models import ToolMetadata

LIST_BRANCHES_TOOL = ToolMetadata(
    name="list_branches_in_repo",
    description="""
    This tool is a wrapper for the Azure DevOps API to fetch a list of all branches in the repository.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to fetch a list of all branches in the repository.
    It will return the name of each branch. No input parameters are required.
    """,
)

SET_ACTIVE_BRANCH_TOOL = ToolMetadata(
    name="set_active_branch",
    description="""
    This tool is a wrapper for the Azure DevOps API and sets the active branch in the repository.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API and set the active branch in the repository,
    similar to `git checkout <branch_name>` and `git switch -c <branch_name>`.
    """,
)

LIST_FILES_TOOL = ToolMetadata(
    name="list_files",
    description="""
    This tool is a wrapper for the Azure DevOps API to recursively fetch files from a directory in the repo.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to recursively fetch files from a directory in the repo.
    Parameters:
        directory_path (str): Path to the directory
        branch_name (str): The name of the branch where the files to be received.
    """,
)

LIST_OPEN_PULL_REQUESTS_TOOL = ToolMetadata(
    name="list_open_pull_requests",
    description="""
    This tool is a wrapper for the Azure DevOps API to fetch all open pull requests from the repository.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to fetch all open pull requests from the repository.
    Returns a plaintext report containing the number of PRs and each PR's title and ID.
    """,
)

GET_PULL_REQUEST_TOOL = ToolMetadata(
    name="get_pr_changes",
    description="""
    This tool is a wrapper for the Azure DevOps API to fetch a particular pull request by ID.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to fetch a particular pull request by ID.
    Returns a plaintext report containing PR details.
    """,
)

LIST_PULL_REQUEST_FILES_TOOL = ToolMetadata(
    name="list_pull_request_files",
    description="""
    This tool is a wrapper for the Azure DevOps API to fetch the files and their diffs included in a pull request.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to fetch the files and their diffs included in a pull request.
    Returns a list of files and diffs included in the pull request.
    """,
)

CREATE_BRANCH_TOOL = ToolMetadata(
    name="create_branch",
    description="""
    This tool is a wrapper for the Azure DevOps API to create a new branch in the repository.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to create a new branch in the repository and set it as the active bot branch.
    Returns a plaintext success message or an error if the branch already exists.
    """,
)

READ_FILE_TOOL = ToolMetadata(
    name="read_file",
    description="""
    This tool is a wrapper for the Azure DevOps API to read a file from a branch.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to read a file from a branch.
    The path must not start with a slash.
    """,
)

CREATE_FILE_TOOL = ToolMetadata(
    name="create_file",
    description="""
    This tool is a wrapper for the Azure DevOps API to create a new file in the repository.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to create a new file in the repository.
    Parameters:
        branch_name (str): The name of the branch where to create a file.
        file_path (str): The path of the file to be created
        file_contents (str): The content of the file to be created
    """,
)

UPDATE_FILE_TOOL = ToolMetadata(
    name="update_file",
    description="""
    This tool is a wrapper for the Azure DevOps API to update a file's content.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to update a file's content.
    Parameters:
        branch_name (str): The name of the branch where update the file.
        file_path (str): Path to the file for update.
        update_query(str): Contains the file contents requried to be updated.
            The old file contents is wrapped in OLD <<<< and >>>> OLD
            The new file contents is wrapped in NEW <<<< and >>>> NEW
    """,
)

DELETE_FILE_TOOL = ToolMetadata(
    name="delete_file",
    description="""
    This tool is a wrapper for the Azure DevOps API to delete a file from the repository.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to delete a file from the repository.
    Parameters:
        branch_name (str): The name of the branch where the file will be deleted.
        file_path (str): The path of the file to delete.
    """,
)

GET_WORK_ITEMS_TOOL = ToolMetadata(
    name="get_work_items",
    description="""
    This tool is a wrapper for the Azure DevOps API to fetch work items associated with a pull request.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to fetch work items associated with a pull request.
    Parameters:
        pull_request_id (str): The ID for Pull Request based on which to get Work Items
    """,
)

COMMENT_ON_PULL_REQUEST_TOOL = ToolMetadata(
    name="create_pr_change_comment",
    description="""
    This tool is a wrapper for the Azure DevOps API to add a comment to a specific pull request.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to add a comment to a specific pull request.
    Parameters:
        comment_query (str): A string which contains the pull request ID, two newlines, and the comment.
                             For example: "1\n\nThis is a test comment" adds the comment "This is a test comment" to PR 1.
    """,
)

CREATE_PULL_REQUEST_TOOL = ToolMetadata(
    name="create_pull_request",
    description="""
    This tool is a wrapper for the Azure DevOps API to create a pull request.
    """,
    react_description="""
    This tool is a wrapper for the Azure DevOps API to create a pull request from the active branch to the base branch.
    Parameters:
        pull_request_title (str): Title of the pull request.
        pull_request_body (str): Description/body of the pull request.
        branch_name (str): The name of the branch which is used as target branch for pull request.
    """,
)
