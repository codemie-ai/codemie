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

CREATE_GIT_BRANCH_TOOL = ToolMetadata(
    name="create_branch",
    user_description="""
    Creates a new branch in the repository associated with the current git data source. Uses official Python libraries to create the branch and set it as the current active branch.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Git Server URL
    2. Git Server authentication token
    Usage Note:
    Use this tool in combination with the "List Branches In Repo" tool when you want the assistant to create a new branch only if it doesn't already exist.
    """.strip(),
)

CREATE_PULL_REQUEST_TOOL = ToolMetadata(
    name="create_pull_request",
    label="Create Pull/Merge request",
    user_description="""
    Creates a new pull request (GitHub, Bitbucket) or merge request (GitLab) for an active branch in a Git repository associated with the current git data source. Uses official Python libraries to initiate the request.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Git Server URL
    2. Git Server authentication token
    """.strip(),
)

CREATE_FILE_TOOL = ToolMetadata(
    name="create_file",
    user_description="""
    Creates a new file with specified content in the current active branch of a Git repository associated with the current git data source. Uses official Python libraries to add the file and commit it to the active branch.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Git Server URL
    2. Git Server authentication token
    """.strip(),
)

DELETE_FILE_TOOL = ToolMetadata(
    name="delete_file",
    user_description="""
    Deletes a specified file from the current active branch in the repository associated with the current git data source. Uses official Python libraries to remove the file and commit the deletion.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Git Server URL
    2. Git Server authentication token
    """.strip(),
)

LIST_BRANCHES_TOOL = ToolMetadata(
    name="list_branches_in_repo",
    user_description="""
    Lists all branches in a Git repository associated with the current git data source. Uses official Python libraries to fetch a list of all branches in the repository. It will return the name of each branch.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Git Server URL
    2. Git Server authentication token
    """.strip(),
)

UPDATE_FILE_TOOL = ToolMetadata(
    name="update_file",
    user_description="""
    Updates an existing file in the current active branch of the repository associated with the current git data source. Works by providing the Large Language Model (LLM) with the full file content and asking it to generate a new file with intended changes.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Git Server URL
    2. Git Server authentication token
    Usage Note:
    This tool is most effective for small files due to context limitations of the LLM. It may work poorly with large files.
    """.strip(),
)

UPDATE_FILE_DIFF_TOOL = ToolMetadata(
    name="update_file_diff",
    user_description="""
    Updates an existing file in the current active branch of the repository associated with the current git data source. Uses a "diff" edit format, asking the Large Language Model (LLM) to specify file edits as a series of search/replace blocks.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Git Server URL
    2. Git Server authentication token
    Usage Note:
    This tool is efficient as the model only needs to return parts of the file which have changes. It usually performs on par with "Update File" for small files and much better for large files.
    """.strip(),
)

SET_ACTIVE_BRANCH_TOOL = ToolMetadata(
    name="set_active_branch",
    user_description="""
    Changes the current active branch in the repository associated with the current git data source. All subsequent operations, such as file manipulation or Pull Request/Merge Request creation, will be executed in the context of this newly set active branch.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Git Server URL
    2. Git Server authentication token
    """.strip(),
)
GET_PR_CHANGES = ToolMetadata(
    name="get_pr_changes",
    label="Get Pull/Merge Request Changes",
    user_description="""
    Retrieves all changes associated with a specific Pull Request (GitHub, Bitbucket) or Merge Request (GitLab) in the repository linked to the current git data source. Uses official Python libraries to fetch the diff of changes.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Git Server URL
    2. Git Server authentication token
    Usage Note:
    This tool is typically used in combination with "Create Pull/Merge Request Change Comment" for conducting code reviews. It provides the necessary context for the AI assistant to analyze changes.
    """.strip(),
)
CREATE_PR_CHANGE_COMMENT = ToolMetadata(
    name="create_pr_change_comment",
    label="Create Pull/Merge Request Change Comment",
    user_description="""
    Adds a comment to a specific line of changed code in a Pull Request (GitHub, Bitbucket) or Merge Request (GitLab) in the repository associated with the current git data source. Uses official Python libraries to post the comment.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Git Server URL
    2. Git Server authentication token
    Usage Note:
    Use this tool after "Get Pull/Merge Request Changes" to allow the AI assistant to provide feedback on specific lines of code.
    """.strip(),
)
