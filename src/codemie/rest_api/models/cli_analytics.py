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

"""Response models for the Local Analytics endpoints.

These models are a CONTRACT with the codemie-ui Local Analytics tab
(`src/types/localAnalytics.ts`). Field names, nesting and types must match that
contract exactly; the underlying data source on this backend differs from the
reference implementation and is documented per-field in the repository layer.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

_ISO8601 = "ISO 8601 datetime string"


# ── Envelope ──────────────────────────────────────────────────────────────────


class LocalAnalyticsResponseMetadata(BaseModel):
    timestamp: str = Field(..., description="ISO 8601 datetime when the response was generated")
    data_as_of: str = Field(..., description="ISO 8601 date/datetime of the most recent data included")
    execution_time_ms: float = Field(..., description="Total query execution time in milliseconds")
    unpriced_models: list[str] = Field(
        default_factory=list,
        description=(
            "Models present in the result whose cache-read rate is absent from the active LLM cost config. "
            "Their cache-read tokens contribute 0.0 to cache_read_cost_usd / bloat_pct. Diagnostic only - "
            "the frontend contract ignores this field."
        ),
    )


class PaginationMetadata(BaseModel):
    page: int
    per_page: int
    total_count: int
    has_more: bool


# ── Overview ──────────────────────────────────────────────────────────────────


class LocalAnalyticsOverviewKPIs(BaseModel):
    total_sessions: int
    total_cost_usd: float
    duration_ms: int
    total_turns: int
    total_tool_calls: int
    tool_call_success_rate: float
    total_files_changed: int
    net_lines: int
    dead_sessions: int
    total_input_tokens: int
    total_output_tokens: int
    total_cache_creation_tokens: int
    total_cache_read_tokens: int
    total_tokens: int
    cache_read_cost_usd: float
    bloat_pct: float
    avg_context_per_call: float


class LocalAnalyticsOverviewDailyBucket(BaseModel):
    day: str = Field(..., description="ISO date, YYYY-MM-DD")
    net_lines: int


class LocalAnalyticsModelBreakdownRow(BaseModel):
    model_name: str
    session_count: int


class LocalAnalyticsOverviewData(BaseModel):
    kpis: LocalAnalyticsOverviewKPIs
    daily_buckets: list[LocalAnalyticsOverviewDailyBucket]
    model_breakdown: list[LocalAnalyticsModelBreakdownRow]


class LocalAnalyticsOverviewResponse(BaseModel):
    data: LocalAnalyticsOverviewData
    metadata: LocalAnalyticsResponseMetadata


# ── Repositories ──────────────────────────────────────────────────────────────


class LocalAnalyticsRepositoryRow(BaseModel):
    repository: str | None = Field(
        None, description="Repository name. None for the unattributed bucket (plugin was not active)."
    )
    branch: str | None = None
    session_count: int
    turns: int
    cost_usd: float
    files_changed: int
    lines_added: int
    lines_removed: int
    net_lines: int
    tool_success_rate: float
    project_name: str | None = None


class LocalAnalyticsRepositoriesData(BaseModel):
    rows: list[LocalAnalyticsRepositoryRow]
    pagination: PaginationMetadata


class LocalAnalyticsRepositoriesResponse(BaseModel):
    data: LocalAnalyticsRepositoriesData
    metadata: LocalAnalyticsResponseMetadata


# ── Users ─────────────────────────────────────────────────────────────────────


class LocalAnalyticsDailyActivityBucket(BaseModel):
    day: str = Field(..., description="ISO date, YYYY-MM-DD")
    session_count: int


class LocalAnalyticsUserRow(BaseModel):
    developer_name: str = Field(..., description="Resolved user email")
    session_count: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_read_tokens: int
    cache_creation_tokens: int
    turns: int = 0
    tool_calls: int = 0
    last_active: str | None = Field(None, description=_ISO8601)
    user_id: str | None = Field(None, description="CodeMie user UUID when resolvable")
    tool_success_rate: float = Field(0.0, description="Percent, one decimal (0.0-100.0)")
    top_model: str | None = None
    net_lines: int = 0
    daily_activity: list[LocalAnalyticsDailyActivityBucket] = Field(default_factory=list)


class LocalAnalyticsUsersData(BaseModel):
    rows: list[LocalAnalyticsUserRow]
    avg_session_duration_ms: int | None = None
    total_count: int | None = None


class LocalAnalyticsUsersResponse(BaseModel):
    data: LocalAnalyticsUsersData
    metadata: LocalAnalyticsResponseMetadata


# ── Sessions ──────────────────────────────────────────────────────────────────


class LocalAnalyticsSessionRow(BaseModel):
    trace_id: str
    developer_name: str
    repository: str | None = Field(
        None, description="Repository cwd at session start. None when the analytics plugin was not active."
    )
    branch: str | None = Field(
        None,
        description="Git branch at session start. None when plugin was not active or cwd is not a git repo.",
    )
    prompt: str = ""
    start_time: str = Field(..., description=_ISO8601)
    duration_ms: int
    model_name: str
    turns: int = 0
    tool_call_count: int = 0
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float
    net_lines: int = 0
    bloat_pct: float = 0.0
    delivery_framework: str | None = Field(None, description="Detected delivery framework")


class LocalAnalyticsFrameworksResponse(BaseModel):
    data: list[str]


class LocalAnalyticsSessionsData(BaseModel):
    sessions: list[LocalAnalyticsSessionRow]
    page: int
    per_page: int
    total: int


class LocalAnalyticsSessionsResponse(BaseModel):
    data: LocalAnalyticsSessionsData
    metadata: LocalAnalyticsResponseMetadata


class LocalAnalyticsSessionEvent(BaseModel):
    timestamp: str = Field(..., description=_ISO8601)
    event_type: str = Field(..., description="'api_request' or 'tool_call'")
    model_name: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cost_usd: float | None = None
    tool_name: str | None = None


class LocalAnalyticsToolWithSuccessRow(BaseModel):
    tool_name: str
    call_count: int
    success_count: int
    success_rate: float


class LocalAnalyticsDispatchRow(BaseModel):
    label: str
    kind: str = Field(..., description="'session' | 'agent' | 'skill' | 'command'")
    start_offset_ms: int
    duration_ms: int
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


class LocalAnalyticsSessionDetail(LocalAnalyticsSessionRow):
    events: list[LocalAnalyticsSessionEvent] = Field(default_factory=list)
    active_ms: int = 0
    agent_count: int = 0
    skill_count: int = 0
    cache_read_cost_usd: float = 0.0
    tool_calls_success: int = 0
    tools: list[LocalAnalyticsToolWithSuccessRow] = Field(default_factory=list)
    dispatches: list[LocalAnalyticsDispatchRow] = Field(default_factory=list)


class LocalAnalyticsSessionDetailResponse(BaseModel):
    data: LocalAnalyticsSessionDetail
    metadata: LocalAnalyticsResponseMetadata


# ── Activity ──────────────────────────────────────────────────────────────────


class LocalAnalyticsActivityData(BaseModel):
    heat: list[list[int]] = Field(..., description="[7][24] - heat[weekday][hour], weekday 0=Mon..6=Sun, UTC")
    by_hour: list[int] = Field(..., description="length 24")
    by_weekday: list[int] = Field(..., description="length 7")


class LocalAnalyticsActivityResponse(BaseModel):
    data: LocalAnalyticsActivityData
    metadata: LocalAnalyticsResponseMetadata


# ── Cost ──────────────────────────────────────────────────────────────────────


class LocalAnalyticsCostKPIs(BaseModel):
    total_sessions: int
    total_cost_usd: float
    total_tokens: int
    avg_cost_per_session: float


class LocalAnalyticsCostByUserRow(BaseModel):
    developer_name: str
    cost_usd: float


class LocalAnalyticsCostByModelRow(BaseModel):
    model_name: str
    cost_usd: float


class LocalAnalyticsCostData(BaseModel):
    kpis: LocalAnalyticsCostKPIs
    cost_by_user: list[LocalAnalyticsCostByUserRow] = Field(default_factory=list)
    cost_by_model: list[LocalAnalyticsCostByModelRow] = Field(default_factory=list)


class LocalAnalyticsCostResponse(BaseModel):
    data: LocalAnalyticsCostData
    metadata: LocalAnalyticsResponseMetadata


# ── Efficiency ────────────────────────────────────────────────────────────────


class LocalAnalyticsEfficiencyKPIs(BaseModel):
    avg_context_per_call: float = 0.0
    worst_session_ctx_per_call: float | None = None
    worst_session_prompt: str | None = None
    worst_session_trace_id: str | None = None
    cache_read_cost_usd: float = 0.0
    bloat_pct: float = 0.0


class LocalAnalyticsDeadSessionsKPIs(BaseModel):
    count: int = 0
    pct_of_sessions: float = 0.0
    wasted_cost_usd: float = 0.0
    avg_cost_per_dead: float | None = None


class LocalAnalyticsSessionDepthBucket(BaseModel):
    bucket: str
    count: int


class LocalAnalyticsCodeChangesKPIs(BaseModel):
    files_changed: int = 0
    files_written: int = 0
    files_edited: int = 0
    net_lines: int = 0


class LocalAnalyticsEfficiencyData(BaseModel):
    kpis: LocalAnalyticsEfficiencyKPIs
    dead_sessions: LocalAnalyticsDeadSessionsKPIs
    session_depth: list[LocalAnalyticsSessionDepthBucket]
    code_changes: LocalAnalyticsCodeChangesKPIs


class LocalAnalyticsEfficiencyResponse(BaseModel):
    data: LocalAnalyticsEfficiencyData
    metadata: LocalAnalyticsResponseMetadata


# ── Tools ─────────────────────────────────────────────────────────────────────


class LocalAnalyticsTokensByModelRow(BaseModel):
    model_name: str
    total_tokens: int


class LocalAnalyticsInvocationRow(BaseModel):
    name: str
    count: int


class LocalAnalyticsToolsData(BaseModel):
    tool_usage: list[LocalAnalyticsToolWithSuccessRow] = Field(default_factory=list)
    tokens_by_model: list[LocalAnalyticsTokensByModelRow] = Field(default_factory=list)
    skills_invoked: list[LocalAnalyticsInvocationRow] = Field(default_factory=list)
    agent_subtypes: list[LocalAnalyticsInvocationRow] = Field(default_factory=list)
    slash_commands: list[LocalAnalyticsInvocationRow] = Field(default_factory=list)


class LocalAnalyticsToolsResponse(BaseModel):
    data: LocalAnalyticsToolsData
    metadata: LocalAnalyticsResponseMetadata
