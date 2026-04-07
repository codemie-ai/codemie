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

TOOLS_USAGE_TOTAL_METRIC = "codemie_tools_usage_total"
TOOLS_USAGE_TOKENS_METRIC = "codemie_tools_usage_tokens"
TOOLS_USAGE_ERRORS_METRIC = "codemie_tools_usage_errors_total"
MCP_SERVERS_ASSISTANT_METRIC = "codemie_mcp_servers"
PLUGIN_KEYS_TOTAL_METRIC = "codemie_plugin_keys_usage_total"
PLUGIN_KEYS_ERRORS_METRIC = "codemie_plugin_keys_errors_total"
PLUGIN_KEYS_INIT_FAILURES_METRIC = "codemie_plugin_keys_init_failures_total"
PLUGIN_AUTH_METRIC = "codemie_plugin_auth_total"
LLM_HARD_BUDGET_LIMIT = "codemie_llm_hard_budget_limit"
LLM_SOFT_BUDGET_LIMIT = "codemie_llm_soft_budget_limit"
LITE_LLM_CREATE_BUDGET = "codemie_litellm_create_budget"
LITE_LLM_CREATE_CUSTOMER = "codemie_litellm_create_customer"
LITE_LLM_DELETE_CUSTOMER = "codemie_litellm_delete_customer"

ASSISTANT_GENERATOR_TOTAL_METRIC = "codemie_assistant_generator_total"
ASSISTANT_GENERATOR_ERRORS_METRIC = "codemie_assistant_generator_errors_total"
PROMPT_GENERATOR_TOTAL_METRIC = "codemie_prompt_generator_total"
PROMPT_GENERATOR_ERRORS_METRIC = "codemie_prompt_generator_errors_total"
MARKETPLACE_ASSISTANT_VALIDATION_SUCCESS_METRIC = "codemie_marketplace_assistant_validation_success"
MARKETPLACE_ASSISTANT_VALIDATION_FAILED_METRIC = "codemie_marketplace_assistant_validation_failed"
MARKETPLACE_ASSISTANT_VALIDATION_ERROR_METRIC = "codemie_marketplace_assistant_validation_error"
MARKETPLACE_ASSISTANT_VALIDATION_LLM_INVOKE_METRIC = "codemie_marketplace_assistant_validation_llm_invoke"

KATA_MANAGEMENT_METRIC = "codemie_kata_management"
KATA_REACTION_METRIC = "codemie_kata_reaction"
KATA_PROGRESS_METRIC = "codemie_kata_progress"

LLM_ERROR_TOTAL_METRIC = "codemie_llm_error_total"

SKILL_MANAGEMENT_METRIC = "codemie_skill_management"
SKILL_ATTACHED_METRIC = "codemie_skill_attached"
SKILL_TOOL_INVOKED_METRIC = "codemie_skill_tool_invoked"
SKILL_EXPORTED_METRIC = "codemie_skill_exported"
SKILL_GENERATOR_TOTAL_METRIC = "codemie_skill_generator_total"
SKILL_GENERATOR_ERRORS_METRIC = "codemie_skill_generator_errors_total"


class MetricsAttributes:
    USER_ID = "user_id"
    USER_NAME = "user_name"
    USER_EMAIL = "user_email"
    EMBEDDINGS_MODEL = "embeddings_model"
    PROJECT = "project"
    REPO_NAME = "repo_name"
    DATASOURCE_TYPE = "datasource_type"
    STATUS = "status"
    ERROR = "error"
    WORKFLOW_NAME = "workflow_name"
    MODE = "mode"
    WORKFLOW_ID = "workflow_id"
    EXECUTION_ID = "execution_id"
    ASSISTANT_ID = "assistant_id"
    ASSISTANT_NAME = "assistant_name"
    ASSISTANT_DESCRIPTION = "assistant_description"
    LLM_MODEL = "llm_model"
    TOOL_NAME = "tool_name"
    SLUG = "slug"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    CACHE_READ_INPUT_TOKENS = "cache_read_input_tokens"
    CACHE_CREATION_TOKENS = "cache_creation_tokens"
    MONEY_SPENT = "money_spent"
    CACHED_TOKENS_MONEY_SPENT = "cached_tokens_money_spent"
    CACHE_CREATION_TOKENS_MONEY_SPENT = "cache_creation_tokens_money_spent"
    EXECUTION_TIME = "execution_time"
    CONVERSATION_ID = "conversation_id"
    REQUEST_UUID = "request_uuid"
    REQUEST_ID = "request_id"
    SESSION_ID = "session_id"
    WEBHOOK_ID = "webhook_id"
    WEBHOOK_ALIAS = "webhook_alias"
    WEBHOOK_RESOURCE_TYPE = "resource_type"
    WEBHOOK_RESOURCE_ID = "resource_id"
    MCP_SERVER_NAME = "mcp_name"
    MCP_SERVER_CONFIG = "mcp_config"
    TOOL_TYPE = "tool_type"
    NESTED_ASSISTANTS_COUNT = "nested_assistants_count"
    PLUGIN_KEY = "plugin_key"
    PLUGIN_SUBJECT = "plugin_subject"
    FAILURE_TYPE = "failure_type"
    KATA_ID = "kata_id"
    KATA_TITLE = "kata_title"
    KATA_STATUS = "kata_status"
    REACTION_TYPE = "reaction_type"
    PROGRESS_STATUS = "progress_status"
    OPERATION = "operation"
    CODEMIE_CLI = "codemie_cli"
    CODEMIE_CLIENT = "codemie_client"
    SKILL_ID = "skill_id"
    SKILL_NAME = "skill_name"
    SKILL_VISIBILITY = "skill_visibility"
    SKILL_CATEGORIES = "skill_categories"
    LLM_ERROR_CODE = "llm_error_code"
