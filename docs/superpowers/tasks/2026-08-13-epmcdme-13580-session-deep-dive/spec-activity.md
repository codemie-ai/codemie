# Session Detail — Activity Widgets

Part of [spec.md](spec.md). Covers every widget answering "what did the session actually do"
within one session.

## Dispatches

**What:** Horizontal bar chart — one bar per subagent dispatch, name vs. duration.
**Source:** `SessionDetail.dispatches: list[DispatchEvent]`.
**Backend:** `get_session_dispatches` — Path B only (`agent.subagent.start`/`agent.subagent.stop`
hook events), joined by `agent_id`: `agent.subagent.start` reliably carries `agent_type` (the
dispatch name), `agent.subagent.stop` reliably carries `agent_id` (the join key) but almost never
`agent_type` itself. Deliberately not the trace pipeline — `SpanAttributes['subagent_type']` on
trace spans covers ~0.4% of tool spans, useless for "how often was X dispatched."
**Not a table:** `kind` was originally a column but is confirmed to always be `"subagent"` — Path
B doesn't yet distinguish skill dispatches from subagent dispatches — so it isn't worth an axis or
a column; the chart is just name vs. duration.
**Null duration:** a dispatch whose `agent.subagent.stop` never arrived (crashed, aborted, or the
session ended mid-dispatch) has `duration_ms: null`. This is passed straight into the Chart.js
`data` array as `null`, not coerced to `0` — Chart.js renders a gap for that bar rather than a
fabricated instant-duration bar.
**Not shown:** per-dispatch cost/tokens — `DispatchEvent.cost_usd`/`tokens` exist on the model
(shared with other dispatch-shaped data) but stay at their zero defaults here; per-dispatch spend
isn't attributable with current data (would need the same broken trace correlation). The original
table version had Cost/Tokens columns that always read `$0.0000`/`0` — removed as actively
misleading rather than kept as fake zeros.
**Shown when:** `dispatches.length > 0`.

## Tool Usage

**What:** Table — one row per distinct tool name — calls, success/fail ratio (e.g. `"100 / 1"`,
with an `"(N incomplete)"` suffix if any calls never resolved), total time spent.
**Source:** `SessionDetail.tool_calls: list[ToolCallEvent]`, aggregated client-side (not one row
per call — a session can have hundreds of individual tool invocations, too much to read as a
log; `aggregateToolCalls()` in `CodingAgentsSessionModal.tsx` groups by `tool_name` and sums
`calls` / `success_count` / `failure_count` / `incomplete_count` / `total_duration_ms`).
**Backend for `tool_calls`:** built once, in the handler (`_build_tool_calls`), from
`get_session_tool_events` (Path B `agent.tool.start`/`.end`/`.error` hook events) paired by
`tool_use_id`:
- start + end → `status: "success"`, `duration_ms = end_ts - start_ts`
- start + error → `status: "failure"`, `duration_ms = error_ts - start_ts`, `error_message`
- start with neither → `status: "incomplete"`, `duration_ms: null`
- both an end and an error for the same `tool_use_id` (hook delivery is best-effort, not
  guaranteed exactly-once) → the later timestamp wins, so `status` reflects what actually
  happened last.

This is also the only remaining consumer of `tool_calls` — the original spec had a second,
per-call "Tool Call Log" table and a separate "Failed Tool Calls" table filtered to
`status === 'failure'`; both were removed after live use (too granular / redundant once this
aggregate existed). `error_message` text is still captured in `ToolCallEvent` and available if a
future "show me the actual failures" need reappears — it's just not surfaced as its own widget
today.
**Shown when:** `tool_calls.length > 0` (i.e. `toolUsageItems.length > 0` after aggregation).

## Daily Lines Changed

**What:** Bar chart, lines added (green, positive) vs. removed (red, mirrored negative) per day
this session touched. Same chart shape as `CodingAgentsUserModal`'s identically-named panel, just
scoped to one session instead of a user's full date range — in practice almost always a single
bar, since sessions rarely span a UTC day boundary.
**Source:** `SessionDetail.lines_daily: list[SessionLinesDailyPoint]` (`{day, lines_added,
lines_removed}`).
**Backend:** `get_session_lines_daily` — `coding_agent_lines_daily` WHERE `session_id = ...`
`GROUP BY day`. That table already carries `session_id` directly (unlike the user-scoped
equivalent, `get_user_lines_daily`, which has to resolve email/project across many sessions via a
join) — this query needed no join at all.
**Shown when:** `lines_daily.length > 0`.

## Not covered here

`SessionSummary.lines_added` / `lines_removed` (session-wide totals, no daily breakdown) already
existed before this modal and aren't separately documented — Daily Lines Changed above is the only
new lines-of-code surface this modal added.
