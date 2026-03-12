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
)

CREATE_PULL_REQUEST_TOOL = ToolMetadata(
    name="create_pull_request",
)

CREATE_FILE_TOOL = ToolMetadata(
    name="create_file",
)

DELETE_FILE_TOOL = ToolMetadata(
    name="delete_file",
)

LIST_BRANCHES_TOOL = ToolMetadata(
    name="list_branches_in_repo",
)

UPDATE_FILE_TOOL = ToolMetadata(
    name="update_file",
)

UPDATE_FILE_DIFF_TOOL = ToolMetadata(
    name="update_file_diff",
)

SET_ACTIVE_BRANCH_TOOL = ToolMetadata(
    name="set_active_branch",
)
GET_PR_CHANGES = ToolMetadata(
    name="get_pr_changes",
)
CREATE_PR_CHANGE_COMMENT = ToolMetadata(
    name="create_pr_change_comment",
)
