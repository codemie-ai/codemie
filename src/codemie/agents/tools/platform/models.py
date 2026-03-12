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
    """Input schema for get_spending tool."""

    user_name: Optional[str] = Field(None, description=FILTER_BY_USER_NAME)
    assistant_id: Optional[str] = Field(None, description="Filter by assistant ID")
    workflow_id: Optional[str] = Field(None, description="Filter by workflow ID")
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
    total_money_spent: float
    average_response_time: float


class ConversationMetricsSummary(BaseModel):
    """Summary statistics for conversation metrics."""

    total_count: int
    total_messages: int
    total_input_tokens: int
    total_output_tokens: int
    total_money_spent: float


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
    """Spending by group dimension."""

    dimension_type: str  # "assistant" or "workflow"
    dimension_id: str
    dimension_name: str
    money_spent: float
    input_tokens: int
    output_tokens: int
    conversation_count: Optional[int] = None  # For assistants
    workflow_execution_count: Optional[int] = None  # For workflows
    average_cost_per_item: float


class LiteLLMSpendingOutput(BaseModel):
    """LiteLLM customer spending data."""

    customer_id: str
    total_spend: float
    max_budget: Optional[float] = None
    budget_duration: Optional[str] = None
    budget_reset_at: Optional[str] = None


class GetSpendingOutput(BaseModel):
    """Output schema for get_spending tool."""

    # Totals from ConversationMetrics
    total_money_spent: float
    total_input_tokens: int
    total_output_tokens: int
    total_conversations: int
    total_workflow_executions: int

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
    total_money_spent: float
    average_response_time: float
    last_interaction_date: str


class GetConversationAnalyticsOutput(BaseModel):
    """Output schema for get_conversation_analytics tool."""

    total_count: int
    conversation_analytics: List[ConversationAnalyticsWithMetricsOutput]
