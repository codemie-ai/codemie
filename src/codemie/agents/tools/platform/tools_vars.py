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

# Tool metadata constants
from codemie_tools.base.models import ToolMetadata

GET_ASSISTANTS_TOOL = ToolMetadata(
    name="get_assistants",
    label="Get Assistants",
    description="""Retrieve assistants with platform analytics filters.
    This is helpful to analyse and evaluate agents details.
    If user defined particular user name, you cannot use scopes other then 'created_by_user'. Say it to user.
    Query assistants created in the platform with
    scope-based filtering (visible_to_user, created_by_user, marketplace, all - 'created_by_user' is default)
    and advanced filters like user_name, project, assistant_ids, and date ranges.
    Permissions are automatically enforced based on user role and scope.""",
)

GET_CONVERSATION_METRICS_TOOL = ToolMetadata(
    name="get_conversation_metrics",
    label="Get Conversation Metrics",
    description="""Retrieve conversation metrics aggregated at the conversation level.
    Useful when user asks to fetch details and metrics about last N conversations to analyse spending, used assistants,
    amount of tokens, avg metrics and so on. Can be used by default when user asks anything general about conversations
    analysis (if not RAW data). Get metrics like total tokens, money spent, and average response time.""",
)

GET_RAW_CONVERSATIONS_TOOL = ToolMetadata(
    name="get_raw_conversations",
    label="Get Raw Conversations",
    description="""Retrieve raw conversation data including message history.
    Use full_mode=True to include detailed tool invocation information (tool names and inputs) from conversation
    thoughts. IMPORTANT: use 'full_mode=true' ONLY if user directly asked full or detailed info.
    Note: Tool results are never included to avoid large response payloads.
    Useful for analyzing conversation content and understanding tool usage patterns, user's AI adoption and
    the maturity of talking to AI agents in general.""",
)

GET_SPENDING_TOOL = ToolMetadata(
    name="get_spending",
    label="Get Spending Analytics",
    description="""Retrieve spending analytics aggregated by dimension (user, project, assistant, workflow).
    Combines conversation metrics data with LiteLLM customer spending.""",
)

GET_KEY_SPENDING_TOOL = ToolMetadata(
    name="get_key_spending",
    label="Get LiteLLM Key Spending",
    description="""Retrieve spending analytics for LiteLLM API keys by their aliases. Get spending data, budget limits,
    and usage details for specific key aliases or all keys. Supports pagination with page and size parameters.
    Use it ONLY when user directly asks for spending from LiteLLM.
    Tool for monitoring LiteLLM API key usage and costs across the platform.""",
)

GET_CONVERSATION_ANALYTICS_TOOL = ToolMetadata(
    name="get_conversation_analytics",
    label="Get Conversation Analytics",
    description="""Retrieve conversation analytics with combined metrics data.
    Returns LLM-analyzed conversation insights (topics, satisfaction, maturity, anti-patterns) joined with usage metrics
    (tokens, costs, response times). Useful for comprehensive conversation analysis including both qualitative insights
    and quantitative measurements. Filter by user, project, and date ranges to analyze conversation quality and costs
    together. Each result combines AI-generated analytics with actual usage metrics for complete visibility.""",
)
