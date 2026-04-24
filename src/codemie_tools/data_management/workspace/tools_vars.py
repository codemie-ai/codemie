# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

AGENT_WORKSPACE_TOOLKIT = "AgentWorkspace"

LIST_WORKSPACE_FILES_TOOL = ToolMetadata(
    name="list_workspace_files",
    description="List files stored in the persistent conversation workspace.",
    label="List workspace files",
    user_description="Allows the AI assistant to inspect the persistent workspace attached to the current conversation.",
)

READ_WORKSPACE_FILE_TOOL = ToolMetadata(
    name="read_workspace_file",
    description="Read file contents from the persistent conversation workspace.",
    label="Read workspace file",
    user_description="Allows the AI assistant to read a file stored in the persistent conversation workspace.",
)

WRITE_WORKSPACE_FILE_TOOL = ToolMetadata(
    name="write_workspace_file",
    description="Create or overwrite a text file in the persistent conversation workspace.",
    label="Write workspace file",
    user_description="Allows the AI assistant to create or update a text file in the persistent conversation workspace.",
)

EDIT_WORKSPACE_FILE_TOOL = ToolMetadata(
    name="edit_workspace_file",
    description=(
        "Replace text in a workspace file using file_path, old_string, new_string, and replace_all. "
        "If multiple matches exist and replace_all is false, the tool returns an ambiguity message instead of editing."
    ),
    label="Edit workspace file",
    user_description="Allows the AI assistant to make deterministic text edits in a workspace file.",
)

DELETE_WORKSPACE_FILE_TOOL = ToolMetadata(
    name="delete_workspace_file",
    description="Delete a file from the persistent conversation workspace.",
    label="Delete workspace file",
    user_description="Allows the AI assistant to delete a file stored in the persistent conversation workspace.",
)

GREP_WORKSPACE_FILES_TOOL = ToolMetadata(
    name="grep_workspace_files",
    description="Search text files in the persistent conversation workspace for matching lines.",
    label="Grep workspace files",
    user_description="Allows the AI assistant to search across text files in the persistent conversation workspace.",
)

EXECUTE_WORKSPACE_SCRIPT_TOOL = ToolMetadata(
    name="execute_workspace_script",
    description=(
        "Execute a Python script stored in the persistent conversation workspace. "
        "The script runs with the workspace materialized as the working directory so it can read sibling files by local path."
    ),
    label="Execute workspace script",
    user_description="Allows the AI assistant to run a Python script stored in the persistent conversation workspace.",
)
