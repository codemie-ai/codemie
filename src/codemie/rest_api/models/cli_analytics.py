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

from typing import Literal

from pydantic import BaseModel, Field

_ISO8601 = "ISO 8601 datetime string"
_SAME_AS_DEVELOPER_NAME = "Same value as developer_name"


class _ClassLists(BaseModel):
    """Sorted distinct subscription types / project origins among an aggregate entity's selected sessions."""

    subscription_types: list[str] = Field(
        default_factory=list,
        description="Distinct subscription types (codemie, corporate, personal, unknown, other), sorted",
    )
    project_origins: list[str] = Field(
        default_factory=list,
        description="Distinct project origins (linked_folder, profile, registered_repository, default_project, "
        "legacy_project, none, other), sorted",
    )


# ── Envelope ──────────────────────────────────────────────────────────────────


class LocalAnalyticsResponseMetadata(BaseModel):
    timestamp: str = Field(..., description="ISO 8601 datetime when the response was generated")
    data_as_of: str = Field(..., description="ISO 8601 date/datetime of the most recent data included")
    execution_time_ms: float = Field(..., description="Total query execution time in milliseconds")
    unpriced_models: list[str] = Field(
        default_factory=list,
        description=(
            "Models present in the result with a token kind they used and no catalog price for it (or no "
            "catalog entry at all; an empty model name is reported as \"\"). Their tokens of that kind contribute "
            "0 to every money value. Diagnostic only - the frontend contract ignores this field."
        ),
    )
    catalog_source: str | None = Field(
        None,
        description=(
            "The catalog that priced the money of this response: 'litellm' (the LiteLLM proxy's model info) or "
            "'yaml' (the LLM config files); named on every money endpoint, even when the selection is empty. "
            "Null on an endpoint that returns no money (/tools, /activity)."
        ),
    )
    cost_incomplete: bool = Field(
        False, description="True when unpriced_models is not empty: the money values are partial, not a real 0."
    )


class LocalAnalyticsCostBucket(BaseModel):
    """Catalog money of one estimate bucket, describing only that bucket's models."""

    cost_usd: float = 0.0
    session_count: int = 0
    cost_incomplete: bool = Field(False, description="True when some of the bucket's tokens had no catalog price")
    unpriced_models: list[str] = Field(default_factory=list, description="The bucket's models without a price")


class LocalAnalyticsFundingBuckets(BaseModel):
    """Money by the client-asserted funding route of each session (its first claim; no claim reads unknown)."""

    api_based: LocalAnalyticsCostBucket = Field(default_factory=LocalAnalyticsCostBucket)
    subscription: LocalAnalyticsCostBucket = Field(default_factory=LocalAnalyticsCostBucket)
    unknown: LocalAnalyticsCostBucket = Field(default_factory=LocalAnalyticsCostBucket)


class _EstimateBuckets(BaseModel):
    """Additive estimates next to the spend: every selected session by funding route, and the personal-billed
    sessions (by derived billing class) whose cost stays out of every spend value."""

    funding: LocalAnalyticsFundingBuckets = Field(
        default_factory=LocalAnalyticsFundingBuckets, description="Client-asserted estimate, not proof of billing"
    )
    personal_billed: LocalAnalyticsCostBucket = Field(default_factory=LocalAnalyticsCostBucket)


class PaginationMetadata(BaseModel):
    page: int
    per_page: int
    total_count: int
    has_more: bool


# ── Overview ──────────────────────────────────────────────────────────────────


class LocalAnalyticsOverviewKPIs(_ClassLists):
    total_sessions: int
    total_cost_usd: float
    duration_ms: int
    total_turns: int
    total_tool_calls: int
    tool_call_success_rate: float
    total_files_changed: int
    net_lines: int | None
    dead_sessions: int
    total_input_tokens: int
    total_output_tokens: int
    total_cache_creation_tokens: int
    total_cache_read_tokens: int
    total_tokens: int
    cache_read_cost_usd: float
    bloat_pct: float
    avg_context_per_call: float


class LocalAnalyticsOverviewDailyBucket(_ClassLists):
    day: str = Field(..., description="ISO date, YYYY-MM-DD")
    net_lines: int | None


class LocalAnalyticsModelBreakdownRow(_ClassLists):
    model_name: str
    session_count: int


class LocalAnalyticsOverviewData(_EstimateBuckets):
    kpis: LocalAnalyticsOverviewKPIs
    daily_buckets: list[LocalAnalyticsOverviewDailyBucket]
    model_breakdown: list[LocalAnalyticsModelBreakdownRow]


class LocalAnalyticsOverviewResponse(BaseModel):
    data: LocalAnalyticsOverviewData
    metadata: LocalAnalyticsResponseMetadata


# ── Repositories ──────────────────────────────────────────────────────────────


class LocalAnalyticsRepositoryBranchRow(_ClassLists):
    branch: str | None = None
    session_count: int
    turns: int
    cost_usd: float
    files_changed: int
    lines_added: int | None
    lines_removed: int | None
    net_lines: int | None
    tool_calls: int
    tool_calls_success: int
    tool_success_rate: float = Field(
        ..., description="Percent, one decimal (0.0-100.0). Σsuccess / Σcalls, never averaged."
    )


class LocalAnalyticsRepositoryRow(_ClassLists, _EstimateBuckets):
    repository: str | None = Field(
        None, description="Repository name. None for the unattributed bucket (plugin was not active)."
    )
    session_count: int
    turns: int
    cost_usd: float
    files_changed: int
    lines_added: int | None
    lines_removed: int | None
    net_lines: int | None
    tool_calls: int
    tool_calls_success: int
    tool_success_rate: float = Field(
        ..., description="Percent, one decimal (0.0-100.0). Σsuccess / Σcalls, never averaged."
    )
    project_name: str | None = None
    branches: list[LocalAnalyticsRepositoryBranchRow] | None = Field(
        None,
        description="Present only when include_branches=True: this repository's branches, each with its own "
        "tool_success_rate.",
    )


class LocalAnalyticsRepositoriesData(_EstimateBuckets):
    rows: list[LocalAnalyticsRepositoryRow]
    pagination: PaginationMetadata


class LocalAnalyticsRepositoriesResponse(BaseModel):
    data: LocalAnalyticsRepositoriesData
    metadata: LocalAnalyticsResponseMetadata


# ── Users ─────────────────────────────────────────────────────────────────────


class LocalAnalyticsDailyActivityBucket(_ClassLists):
    day: str = Field(..., description="ISO date, YYYY-MM-DD")
    session_count: int


class LocalAnalyticsUserRow(_ClassLists, _EstimateBuckets):
    developer_name: str = Field(..., description="Resolved user email")
    user_email: str = Field("", description=_SAME_AS_DEVELOPER_NAME)
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
    net_lines: int | None = None
    daily_activity: list[LocalAnalyticsDailyActivityBucket] = Field(default_factory=list)


class LocalAnalyticsUsersData(_EstimateBuckets):
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
    user_email: str = Field("", description=_SAME_AS_DEVELOPER_NAME)
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
    turns: int | None = Field(
        None,
        description="User turns, whole-session. None is unknown (no summary reports it, no observed turn); 0 is a "
        "reported zero. At least the observed turns; complete only when summary_state is final and summary_metrics "
        "lists 'turns'.",
    )
    summary_state: Literal["none", "provisional", "final"] = Field(
        "none",
        description="'none' (no session summary), 'provisional' (a snapshot that later events can exceed) or 'final'.",
    )
    summary_metrics: list[str] = Field(
        default_factory=list,
        description="Metrics the latest summary reports validly: any of 'turns', 'tool_calls', 'agents', 'skills'.",
    )
    tool_call_count: int = Field(
        0, description="Tool calls observed in events: the denominator of the success rate. Can be below the total."
    )
    tool_calls_total: int | None = Field(
        None,
        description="Whole-session tool calls of the latest session summary, never below tool_call_count. None when "
        "no summary reports it (unknown); 0 is a real zero.",
    )
    api_call_count: int = 0
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    ctx_per_call: float = 0.0
    cost_usd: float
    cost_available: bool = True
    net_lines: int | None = None
    bloat_pct: float = 0.0
    delivery_framework: str | None = Field(None, description="Detected delivery framework")
    subscription_type: str = Field("unknown", description="codemie, corporate, personal, unknown or other")
    project_origin: str = Field(
        "none",
        description=(
            "personal, linked_folder, profile, registered_repository, default_project, legacy_project, none or other"
        ),
    )
    route_note: str | None = Field(
        None,
        description="Diagnostic routing note stored with the session (folder_not_linked, linked_project_unavailable, "
        "registered_project_inaccessible, registration_ambiguous). None for sessions without one, including legacy "
        "data.",
    )
    routing_label: str | None = Field(
        None,
        description='"folder not linked to a project" for a default-project session whose folder had no usable '
        "project or repository link (also when the link was inaccessible); None otherwise.",
    )


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
    event_type: str = Field(
        ...,
        description="'api_request', 'tool_call' or 'subagent_totals' (the summed usage of a subagent that has no "
        "request of its own: one aggregate per model, not a request)",
    )
    model_name: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = Field(
        None,
        description="Cache-write tokens (5m + 1h + unsplit) of an api_request or subagent_totals event; "
        "null for a tool_call.",
    )
    cost_usd: float | None = None
    tool_name: str | None = None


class LocalAnalyticsToolWithSuccessRow(_ClassLists):
    tool_name: str
    call_count: int
    success_count: int
    success_rate: float


class LocalAnalyticsDispatchRow(BaseModel):
    label: str
    kind: str = Field(..., description="'session' | 'agent' | 'skill' | 'command'")
    start_offset_ms: int
    duration_ms: int | None = Field(None, description="null when an agent's stop never arrived")
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


class LocalAnalyticsSessionDetail(LocalAnalyticsSessionRow):
    events: list[LocalAnalyticsSessionEvent] = Field(default_factory=list)
    active_ms: int = 0
    agent_count: int | None = Field(None, description="Subagent dispatches; None unknown, 0 reported zero (see turns).")
    skill_count: int | None = Field(None, description="Skill invocations; None unknown, 0 reported zero (see turns).")
    cache_read_cost_usd: float = 0.0
    tool_calls_success: int = 0
    tool_success_rate: float = Field(
        0.0,
        description="Percent, one decimal (0.0-100.0). _pct(tool_calls_success, tool_call_count): observed calls only.",
    )
    tools: list[LocalAnalyticsToolWithSuccessRow] = Field(default_factory=list)
    dispatches: list[LocalAnalyticsDispatchRow] = Field(default_factory=list)
    harness_name: str = Field(
        ..., description="Resolved display label for the coding harness/client, e.g. 'Claude Code'."
    )


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


class LocalAnalyticsCostKPIs(_ClassLists):
    total_sessions: int
    total_cost_usd: float
    total_tokens: int
    avg_cost_per_session: float


class LocalAnalyticsCostByUserRow(_ClassLists, _EstimateBuckets):
    developer_name: str
    user_email: str = Field("", description=_SAME_AS_DEVELOPER_NAME)
    cost_usd: float


class LocalAnalyticsCostByModelRow(_ClassLists, _EstimateBuckets):
    model_name: str
    cost_usd: float


class LocalAnalyticsCostData(_EstimateBuckets):
    kpis: LocalAnalyticsCostKPIs
    cost_by_user: list[LocalAnalyticsCostByUserRow] = Field(default_factory=list)
    cost_by_model: list[LocalAnalyticsCostByModelRow] = Field(default_factory=list)
    models_without_spend: list[LocalAnalyticsCostByModelRow] = Field(
        default_factory=list,
        description="Models only the estimate priced (no spend): cost_usd 0 plus buckets; never in cost_by_model",
    )


class LocalAnalyticsCostResponse(BaseModel):
    data: LocalAnalyticsCostData
    metadata: LocalAnalyticsResponseMetadata


# ── Efficiency ────────────────────────────────────────────────────────────────


class LocalAnalyticsEfficiencyKPIs(_ClassLists):
    avg_context_per_call: float = 0.0
    worst_session_ctx_per_call: float | None = None
    worst_session_prompt: str | None = None
    worst_session_trace_id: str | None = None
    cache_read_cost_usd: float = 0.0
    bloat_pct: float = 0.0
    total_cost_usd: float = 0.0


class LocalAnalyticsDeadSessionsKPIs(_ClassLists):
    count: int = 0
    pct_of_sessions: float = 0.0
    wasted_cost_usd: float = 0.0
    avg_cost_per_dead: float | None = None


class LocalAnalyticsSessionDepthBucket(_ClassLists):
    bucket: str
    count: int


class LocalAnalyticsCodeChangesKPIs(_ClassLists):
    files_changed: int = 0
    files_written: int = 0
    files_edited: int = 0
    net_lines: int | None = None


class LocalAnalyticsEfficiencyData(_EstimateBuckets):
    kpis: LocalAnalyticsEfficiencyKPIs
    dead_sessions: LocalAnalyticsDeadSessionsKPIs
    session_depth: list[LocalAnalyticsSessionDepthBucket]
    code_changes: LocalAnalyticsCodeChangesKPIs


class LocalAnalyticsEfficiencyResponse(BaseModel):
    data: LocalAnalyticsEfficiencyData
    metadata: LocalAnalyticsResponseMetadata


# ── Tools ─────────────────────────────────────────────────────────────────────


class LocalAnalyticsTokensByModelRow(_ClassLists):
    model_name: str
    total_tokens: int


class LocalAnalyticsInvocationRow(_ClassLists):
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
