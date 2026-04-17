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

"""Shared constants for CLI analytics handlers."""

from __future__ import annotations

# Elasticsearch field constants
TIMESTAMP_FIELD = "@timestamp"
REPOSITORY_KEYWORD_FIELD = "attributes.repository.keyword"
SESSION_DURATION_MS_FIELD = "attributes.session_duration_ms"
RESPONSE_STATUS_FIELD = "attributes.response_status"
TOTAL_LINES_ADDED_FIELD = "attributes.total_lines_added"
TOTAL_TOOL_CALLS_FIELD = "attributes.total_tool_calls"
TOTAL_USER_PROMPTS_FIELD = "attributes.total_user_prompts"
TOTAL_LINES_REMOVED_FIELD = "attributes.total_lines_removed"
FILES_CREATED_FIELD = "attributes.files_created"
FILES_MODIFIED_FIELD = "attributes.files_modified"
FILES_DELETED_FIELD = "attributes.files_deleted"
SESSION_ID_KEYWORD_FIELD = "attributes.session_id.keyword"
SESSION_STATUS_KEYWORD_FIELD = "attributes.status.keyword"
LLM_MODEL_KEYWORD_FIELD = "attributes.llm_model.keyword"
CLI_REQUEST_FIELD = "attributes.cli_request"
INPUT_TOKENS_FIELD = "attributes.input_tokens"
OUTPUT_TOKENS_FIELD = "attributes.output_tokens"
CACHE_READ_INPUT_TOKENS_FIELD = "attributes.cache_read_input_tokens"
CACHE_CREATION_TOKENS_FIELD = "attributes.cache_creation_tokens"
MONEY_SPENT_FIELD = "attributes.money_spent"

# Tool usage fields
TOOL_NAMES_FIELD = "attributes.tool_names"
TOOL_COUNTS_FIELD = "attributes.tool_counts"
TOOL_NAMES_ATTR_KEY = "tool_names"
TOOL_COUNTS_ATTR_KEY = "tool_counts"
USER_ID_KEYWORD_FIELD = "attributes.user_id.keyword"
USER_NAME_KEYWORD_FIELD = "attributes.user_name.keyword"
USER_EMAIL_RAW_FIELD = "attributes.user_email"
BRANCH_KEYWORD_FIELD = "attributes.branch.keyword"

# Special values
N_A_VALUE = "N/A"
PROJECT_TYPE_PERSONAL = "personal"
PROJECT_TYPE_TEAM = "team"
LEARNING_REPO_PATTERNS = [r"tutorial", r"learn", r"course", r"training", r"workshop", r"sample", r"example"]
TESTING_REPO_PATTERNS = [r"test", r"spec", r"qa", r"mock", r"fixture"]
EXPERIMENTAL_REPO_PATTERNS = [r"demo", r"poc", r"spike", r"experiment", r"playground", r"sandbox", r"scratch"]
PET_PROJECT_REPO_PATTERNS = [r"personal", r"my[-_]?project", r"side[-_]?project"]
LOCAL_PATH_PATTERNS = [
    r"^/Users/",
    r"^/home/",
    r"^[A-Z]:[/\\\\]Users[/\\\\]",
    r"Downloads",
    r"Desktop",
    r"tmp",
    r"temp",
]
PRODUCTION_BRANCH_PATTERNS = [r"[A-Z]+-\d+", r"^(feature|feat|fix|bugfix|hotfix|release)[-_/]"]
NON_PRODUCTION_BRANCH_PATTERNS = [r"^(test|tmp|temp|sandbox|playground|experiment)[-_/]"]
PERSONAL_PROJECT_DOMAINS = ("@epam.com", "@epamneoris.com", "@firstderivative.com")
TERMINAL_TOOL_MATCHERS = ("bash", "run_shell_command")
READ_SEARCH_TOOL_MATCHERS = ("read", "grep", "glob", "find", "search", "webfetch", "websearch")
CODE_CHANGE_TOOL_MATCHERS = ("edit", "write", "notebookedit", "replace")
PLANNING_TOOL_MATCHERS = ("task", "askuserquestion", "ask_user", "enterplanmode", "exitplanmode")
AGENT_TOOL_MATCHERS = ("agent", "skill")
SESSION_COMPLETED_STATUSES = ("completed", "failed", "interrupted")
TOTAL_COST_LABEL = "Total Cost"
USAGE_COUNT_LABEL = "Usage Count"
NET_LINES_LABEL = "Net Lines"
