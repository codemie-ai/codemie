# Session Detail — Cost Widgets

Part of [spec.md](spec.md). Covers every widget answering "where did the money/tokens go" within
one session.

## Cost Over Time

**What:** Line chart, cumulative cost (USD) per API call, in call order.
**Source:** `SessionDetail.cost_series: list[CostSeriesPoint]`.
**Backend:** `CodingAgentAnalyticsRepository.get_session_cost_series` — `coding_agent_logs` WHERE
`event_name = 'api_request' AND session_id = ...`, one row per call. The handler accumulates
`cost_this_call` into a running total per point; `t` is `event_sequence` (an ordinal), not a
Unix timestamp — the x-axis is "call order," not clock time.
**Shown when:** `cost_series.length > 1` (a single point isn't a trend).

`CostSeriesPoint` also carries a per-call token breakdown (`input_tokens`, `output_tokens`,
`cache_read_tokens`, `cache_creation_tokens`) — not plotted on this chart (would need a second
axis of a wildly different scale), but it's the direct source for Token Composition below, summed
instead of plotted over time.

## Cost by Model

**What:** Horizontal bar chart, cost (USD) per model used in the session. Hover a bar for its
token breakdown (total / input / output / cache read).
**Source:** `SessionDetail.per_model_cost: list[ModelCost]` (existing field, part of
`SessionSummary` — not new to this modal; this chart is the first thing to actually visualize it
here, previously only a plain comma-joined model-name list in the metadata row).
**Shown when:** `per_model_cost.length > 0`.
**Colors:** `generateChartColors(n)`, the same categorical generator used everywhere else in this
codebase's charts — not a bespoke palette.

## Token Composition

**What:** Donut chart, input vs. output vs. cache-read vs. cache-creation tokens, summed across
the whole session.
**Source:** `SessionDetail.cost_series` — same field as Cost Over Time, summed instead of plotted.
No dedicated backend field; this is a pure frontend aggregation (`tokenComposition` memo in
`CodingAgentsSessionModal.tsx`).
**Shown when:** `cost_series.length > 0`.
**Component:** `InlineDonutChart` (shared with `CodingAgentsUserModal`'s identically-named panel).

## Cost by Query Source

**What:** Donut chart, spend bucketed into Main Thread / Subagent / Skill / Command / Other —
shows how much of this session's cost came from delegated work vs. the main conversation thread.
**Source:** `SessionDetail.cost_by_source: list[CostBySourceRow]` (`{name, cost_usd}`, `name` is
the raw `query_source` string e.g. `"agent:Explore"`, `"repl_main_thread"`).
**Backend:** `get_session_cost_by_source` — `coding_agent_cost_daily` (the pre-aggregated daily
rollup; it already carries `session_id` and `query_source` directly, so no join or
`LogAttributes` parsing needed) `GROUP BY query_source`.
**Frontend bucketing:** `classifyQuerySource()` (`utils/analyticsFormatters.ts`) — the exact same
function `CodingAgentsUserModal`'s Cost by Query Source panel uses, so the bucket names/behavior
are identical across both modals.
**Shown when:** `cost_by_source.length > 0`.

## Cost by Agent / Skill

**What:** Table — name, calls, cost (USD), tokens — for every agent/skill invocation tagged in
this session, sorted by cost descending, agent and skill rows interleaved by cost (not grouped).
**Source:** `SessionDetail.agent_cost_totals` + `skill_cost_totals: list[NamedCostStats]`.
**Backend:** `get_session_agent_cost` / `get_session_skill_cost` — `coding_agent_logs` WHERE
`event_name = 'api_request' AND agent_name != '' / skill_name != ''`, grouped by name. Sourced
from native Claude Code telemetry's direct-tag attribution (`LogAttributes['agent.name']` /
`['skill.name']`), independent of the unreliable trace-based subagent pipeline.
**Shown when:** either list is non-empty.

## Stat tile: Total Tokens

Not a chart — one of the five top-row stat tiles (`sessionDetail.tokens.total`, already computed
session-wide on the backend via `_build_token_usage`). No new backend work; it existed on the API
response before this modal ever rendered it.
