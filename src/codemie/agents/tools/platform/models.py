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

"""Pydantic models for Platform Toolkit tool inputs and outputs."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


# ==================== Constants ====================

FILTER_BY_ASSISTANT_IDS_DESC = "Filter by assistant IDs"
FILTER_BY_PROJECT_NAME_DESC = "Filter by project name"
FILTER_BY_USER_NAME = "Filter by user name (creator, case-insensitive)."


# ==================== Input Models ====================


class DateFilterMixin(BaseModel):
    """Mixin for date filtering."""

    since_date: Optional[str] = Field(None, description="ISO 8601 format date (e.g., '2024-01-01T00:00:00Z')")
    last_n_days: Optional[int] = Field(None, description="Filter for last N days", ge=1, le=365)

    @field_validator('since_date')
    @classmethod
    def validate_date_format(cls, v: Optional[str]) -> Optional[str]:
        """Validate ISO 8601 date format."""
        if v is None:
            return v
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except ValueError:
            raise ValueError("Date must be in ISO 8601 format (e.g., '2024-01-01T00:00:00Z')")


class PaginationMixin(BaseModel):
    """Mixin for pagination."""

    limit: int = Field(100, description="Maximum number of results to return", ge=1, le=1000)
    offset: int = Field(0, description="Number of results to skip", ge=0)


class GetAssistantsInput(DateFilterMixin, PaginationMixin):
    """Input schema for get_assistants tool."""

    user_name: Optional[str] = Field(None, description=FILTER_BY_USER_NAME)
    assistant_ids: Optional[List[str]] = Field(None, description=FILTER_BY_ASSISTANT_IDS_DESC)
    project: Optional[str] = Field(None, description=FILTER_BY_PROJECT_NAME_DESC)
    scope: Optional[str] = Field(
        "created_by_user",
        description=(
            "Scope of assistants to retrieve: "
            "'visible_to_user' (default) - assistants visible to the user, "
            "'created_by_user' - assistants created by specific user, "
            "'marketplace' - global marketplace assistants, "
            "'all' - all available assistants"
        ),
    )
    filters: Optional[dict] = Field(None, description="Additional filters to apply (advanced)")
    minimal_response: bool = Field(
        False, description="Return minimal response data. Should be False until user asks explicitly."
    )


class GetConversationMetricsInput(DateFilterMixin, PaginationMixin):
    """Input schema for get_conversation_metrics tool."""

    user_name: Optional[str] = Field(None, description=FILTER_BY_USER_NAME)
    assistant_ids: Optional[List[str]] = Field(None, description=FILTER_BY_ASSISTANT_IDS_DESC)
    project: Optional[str] = Field(None, description=FILTER_BY_PROJECT_NAME_DESC)


class GetRawConversationsInput(DateFilterMixin, PaginationMixin):
    """Input schema for get_raw_conversations tool."""

    user_name: Optional[str] = Field(None, description=FILTER_BY_USER_NAME)
    assistant_ids: Optional[List[str]] = Field(None, description=FILTER_BY_ASSISTANT_IDS_DESC)
    project: Optional[str] = Field(None, description=FILTER_BY_PROJECT_NAME_DESC)
    full_mode: bool = Field(
        False,
        description="Include detailed tool invocation information (tool names and inputs) from conversation thoughts. "
        "Note: Tool results are never included due to potentially large size.",
    )


class GetSpendingInput(DateFilterMixin):
    """Input schema for get_spending tool.

    At least one of user_name, project, assistant_id or workflow_id must be given: a spending figure
    is read for a named subject, and there is no reading of the question under which "no subject"
    means "the whole platform".
    """

    user_name: Optional[str] = Field(
        None,
        description=(
            "Whose spending: a username, an email or a display name. Your own name matches in any case, as do "
            "emails; another person's username or display name must match exactly."
        ),
    )
    assistant_id: Optional[str] = Field(
        None, description="Filter by assistant ID. Mutually exclusive with workflow_id."
    )
    workflow_id: Optional[str] = Field(None, description="Filter by workflow ID. Mutually exclusive with assistant_id.")
    project: Optional[str] = Field(None, description=FILTER_BY_PROJECT_NAME_DESC)
    include_breakdown: bool = Field(
        False, description="Include detailed spending breakdown by assistants and workflows"
    )


class GetKeySpendingInput(BaseModel):
    """Input schema for get_key_spending tool."""

    key_aliases: Optional[List[str]] = Field(
        None, description="Optional list of key aliases to filter. If not provided, returns all keys."
    )
    include_details: bool = Field(True, description="Include detailed key information (models, last_used, etc.)")
    page: int = Field(1, description="Page number for pagination (1-indexed)", ge=1)
    size: int = Field(100, description="Number of results per page", ge=1, le=1000)


class GetConversationAnalyticsInput(DateFilterMixin, PaginationMixin):
    """Input schema for get_conversation_analytics tool."""

    user_name: Optional[str] = Field(None, description=FILTER_BY_USER_NAME)
    project: Optional[str] = Field(None, description=FILTER_BY_PROJECT_NAME_DESC)


# ==================== Output Models ====================


class CreatedByUserOutput(BaseModel):
    """User who created the resource."""

    user_id: str
    name: str
    username: str


class ContextOutput(BaseModel):
    """Context information."""

    context_type: str
    name: str


class ToolOutput(BaseModel):
    """Simplified tool information."""

    toolkit: str
    name: str
    label: str


class AssistantOutput(BaseModel):
    """Assistant details output."""

    id: str
    name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    project: Optional[str] = None
    created_by: CreatedByUserOutput
    created_date: str
    updated_date: str
    llm_model_type: Optional[str] = None
    sub_assistants_ids: list[str] = Field(default_factory=list)
    tools: List[ToolOutput] = Field(default_factory=list)
    context: List[ContextOutput] = Field(default_factory=list)


class GetAssistantsOutput(BaseModel):
    """Output schema for get_assistants tool."""

    total_count: int
    assistants: List[AssistantOutput]


class ConversationMetricOutput(BaseModel):
    """Conversation metric details."""

    conversation_id: str
    user_name: str
    project: str
    assistant_ids: List[str]
    last_interaction_date: str
    total_messages: int
    total_input_tokens: int
    total_output_tokens: int
    # `None` when this conversation has no settled cost. `ConversationMetrics.total_money_spent` is
    # already `Optional`, and `or 0.0` here turned "nobody knows yet" into "it was free" - on a
    # surface an assistant quotes to a person.
    total_money_spent: Optional[float] = None
    average_response_time: float


class ConversationMetricsSummary(BaseModel):
    """Summary statistics for conversation metrics.

    `total_money_spent` is a **sum over the conversations that settled a cost**, which is not the same
    thing as the total. The two flags say which it is, because a subtotal that looks exact is the one
    failure this refactor exists to remove (AD-23/AD-33): `spend_unavailable` when nothing settled at
    all - then the figure is `None`, never `0.0` - and `spend_partial` when some did and some did not.
    """

    total_count: int
    total_messages: int
    total_input_tokens: int
    total_output_tokens: int
    total_money_spent: Optional[float] = None
    spend_unavailable: bool = False
    spend_partial: bool = False


class GetConversationMetricsOutput(BaseModel):
    """Output schema for get_conversation_metrics tool."""

    total_metrics_summary: ConversationMetricsSummary
    metrics: List[ConversationMetricOutput]


class MessageOutput(BaseModel):
    """Message details."""

    role: str
    content: str
    timestamp: str
    history_index: int
    tools_invoked: Optional[List[dict]] = Field(
        None,
        description="Tool invocation details from thoughts (tool_name and tool_input only, no results). "
        "Only included when full_mode=True.",
    )


class ConversationOutput(BaseModel):
    """Conversation details."""

    conversation_id: str
    conversation_name: str
    user_id: str
    user_name: str
    project: str
    created_date: str
    messages: List[MessageOutput]


class GetRawConversationsOutput(BaseModel):
    """Output schema for get_raw_conversations tool."""

    total_count: int
    conversations: List[ConversationOutput]


class SpendingGroupOutput(BaseModel):
    """Spending by group dimension.

    The money fields come from the spend port, so they carry the port's own three states rather than
    a bare number: `None` is **no figure** and never a zero, and the `spend_*` triple says whether
    the figure that is there is short and by how much. An assistant quotes these to a person, so a
    truncated figure that arrives looking whole is the failure this shape prevents.
    """

    dimension_type: str  # "assistant" or "workflow"
    dimension_id: str
    dimension_name: str
    money_spent: Optional[float] = None
    input_tokens: int
    output_tokens: int
    conversation_count: Optional[int] = None  # For assistants
    workflow_execution_count: Optional[int] = None  # For workflows
    # No figure and no items are two different reasons there is no per-item cost, and neither is
    # `0.0` - which on a report reads as "this dimension costs nothing per item".
    average_cost_per_item: Optional[float] = None
    spend_unavailable: bool = False
    spend_partial: bool = False
    spend_missing_amount: Optional[float] = None


class LiteLLMSpendingOutput(BaseModel):
    """LiteLLM customer spending data."""

    customer_id: str
    total_spend: float
    max_budget: Optional[float] = None
    budget_duration: Optional[str] = None
    budget_reset_at: Optional[str] = None


class GetSpendingOutput(BaseModel):
    """Output schema for get_spending tool.

    `total_money_spent` is **one** figure from **one** spend-port read over the whole filter
    conjunction - never a conversation figure added to a workflow figure. Two independently floored
    figures added together are not a figure: if either was short, the sum is short by an unknown
    amount and says nothing about it, and this is the surface an assistant reads a number off and
    quotes to a person.
    """

    # One composed spend-port read over the whole filter conjunction.
    total_money_spent: Optional[float] = None
    spend_unavailable: bool = False
    spend_partial: bool = False
    spend_missing_amount: Optional[float] = None
    # Activity of the same subject as the money. `None` when the `user_name` asked about resolved to no
    # person: activity is keyed on the resolved `users.id`, and an unknown count is not a zero.
    total_input_tokens: Optional[int] = None
    total_output_tokens: Optional[int] = None
    total_conversations: Optional[int] = None
    total_workflow_executions: Optional[int] = None

    # Detailed breakdown (optional, based on include_breakdown parameter)
    spending_breakdown: List[SpendingGroupOutput] = Field(
        default_factory=list, description="Detailed breakdown by assistants and workflows"
    )


class KeySpendingOutput(BaseModel):
    """Single LiteLLM key spending information."""

    # Essential fields (always included)
    key_alias: Optional[str] = None
    spend: float
    max_budget: Optional[float] = None
    budget_duration: Optional[str] = None
    budget_reset_at: Optional[str] = None
    models: Optional[List[str]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_refreshed_at: Optional[str] = None
    team_id: Optional[str] = None
    user_id: Optional[str] = None
    metadata: Optional[dict] = None
    expires: Optional[str] = None
    tpm_limit: Optional[int] = None
    rpm_limit: Optional[int] = None
    max_parallel_requests: Optional[int] = None
    blocked: Optional[bool] = None
    soft_budget_cooldown: Optional[bool] = None


class GetKeySpendingOutput(BaseModel):
    """Output schema for get_key_spending tool."""

    total_keys: int
    total_spend_across_keys: float
    keys: List[KeySpendingOutput]


class ConversationAnalyticsWithMetricsOutput(BaseModel):
    """Combined conversation analytics and metrics output."""

    # Analytics data
    conversation_id: str
    user_id: str
    user_name: str
    project: Optional[str] = None
    assistants_used: List[dict]  # List of AssistantUsed dicts
    topics: List[dict]  # List of TopicAnalysis dicts
    satisfaction: Optional[dict] = None  # SatisfactionMetrics dict
    maturity: Optional[dict] = None  # MaturityAnalysis dict
    anti_patterns: List[dict] = Field(default_factory=list)  # List of AntiPattern dicts
    last_analysis_date: str
    message_count_at_analysis: int
    llm_model_used: str
    analysis_duration_seconds: float

    # Metrics data (from ConversationMetrics)
    total_messages: int
    total_input_tokens: int
    total_output_tokens: int
    # `ConversationMetrics.total_money_spent` is None for a conversation with nothing settled;
    # coercing that to 0.0 here would republish the absent figure as an exact zero.
    total_money_spent: Optional[float] = None
    average_response_time: float
    last_interaction_date: str


class GetConversationAnalyticsOutput(BaseModel):
    """Output schema for get_conversation_analytics tool."""

    total_count: int
    conversation_analytics: List[ConversationAnalyticsWithMetricsOutput]
