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

"""Names of the tools added automatically from attached datasources.

Imports nothing, so the assistant model can depend on it without loading the tool stack.
Tool metadata in agents/tools uses these names, so the two cannot drift.
"""

REPO_TREE_TOOL_NAME = "get_repository_file_tree"
CODE_SEARCH_TOOL_NAME = "search_code_repo"
SEARCH_KB_TOOL_NAME = "search_kb"

DEFAULT_CONTEXT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        REPO_TREE_TOOL_NAME,
        CODE_SEARCH_TOOL_NAME,
        SEARCH_KB_TOOL_NAME,
    }
)
