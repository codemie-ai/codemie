# Spec: ClickHouse Schema for Claude Code Analytics
# Ticket: EPMCDME-13554 — [AgentAnalytics] ClickHouse Schema Design & Agreement
# Design source: Claude Code CLI Analytics — Solution Design (2026-07-14, Status: Approved)

> **Update (2026-08-14):** the schema grew substantially since the original approval — three more
> derived tables + MVs (`coding_agent_session_identity`, `coding_agent_lines_daily`,
> `coding_agent_active_time_daily`), a `v_session_email` view, more Path B event types, and (most
> importantly) **the team dimension described throughout this spec was removed entirely** — there
> is no `team_name` column, `team.name` resource attribute, or `?team=` filter anywhere in
> `deployment/clickhouse/schema.sql` today. Every AC below that mentions `team_name`/`team.name` is
> stale; left in place with inline callouts rather than deleted, so the history of what was
> descoped is visible. Verified directly against `deployment/clickhouse/schema.sql`.

## Goal

Design and implement the ClickHouse DDL that stores and serves Claude Code analytics data
for the `/v1/analytics/coding-agents/*` read endpoints. The schema is the single source of
truth for all downstream analytics tasks.

## Data Paths

### Path A — Native Claude Code OTel
Claude Code CLI emits OTLP/HTTP protobuf → CodeMie API (proxy) → OTel Collector → ClickHouse.

Cost data flows via the `claude_code.cost.usage` OTel metric — `cost_usd` and all token
counts (including `cache_read_tokens`) are emitted directly by Claude Code. No server-side
pricing table or per-model rate lookup is required.

Key log record: `event.name = 'api_request'` with fields:
- `session.id` (dot-notation), `model`, `input_tokens`, `output_tokens`,
  `cache_read_tokens`, `cache_creation_tokens`, `cost_usd`, `query_source`
- `user.email`, `user.id`, `organization.id`

Optional spans (CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1):
- `claude_code.tool`, `claude_code.tool.execution`, `claude_code.interaction`
- Key span attributes: `tool_name`, `tool_use_id`, `session.id` (dot-notation)

Optional metrics:
- `claude_code.lines_of_code.count`, session and token counters

### Path B — sdlc-factory Plugin Hook Events
Bash hooks → `~/.codemie/analytics/buffer.jsonl` → analytics-sender → CodeMie API
(converts to OTel log records) → OTel Collector → ClickHouse.

> **Update (2026-08-14):** `mv_hook_events` (the MV that routes these into
> `coding_agent_hook_events`) filters on **13** event types today, not 6 — see the corrected list
> below and AC-6.

- `agent.session.start`, `agent.session.stop`, `agent.session.end`, `agent.session.compact`
- `agent.prompt.submit`
- `agent.tool.start`, `agent.tool.end`, `agent.tool.error`, `agent.tool.denied`
- `agent.turn.error`
- `agent.subagent.start`, `agent.subagent.stop` — the pair the Coding Agents tab's Sessions/Dispatches
  widgets are built on; didn't exist at the time of the original design
- `agent.notification`

Key fields promoted into `coding_agent_hook_events` today: `session_id`, `prompt_id`,
`developer_name`, `codemie_project_name`, `cwd`, `git_branch`, `repo_remote`, `permission_mode`,
`source`, `effort`, `tool_name`, `tool_use_id`, `tool_input`, `tool_output` (truncated by
`analytics-hook` before send, not re-truncated here), `error_message`, `error_type`, `reason`,
`agent_id`, `agent_type`, `trigger`, `denial_reason`, `notification_type`, `prompt_body`. The
original design's field list ("`tool_output` truncated 1 KB") undersold both the truncation length
(300 characters per `analytics-hook`, per the ingest-endpoints spec) and the field count — nine of
the fields above (`prompt_id`, `codemie_project_name`, `repo_remote`, `source`, `effort`,
`error_type`, `reason`, `agent_id`, `agent_type`, `trigger`, `denial_reason`, `notification_type`)
didn't exist in the original design at all.

### Team Dimension (both paths) — **removed, no longer applies**
The original design set `team.name` as an OTel resource attribute
(`OTEL_RESOURCE_ATTRIBUTES=team.name=...`) and required it for `?team=` filtering on every read
endpoint. **Team was descoped entirely** during the frontend build-out (no team filter, no team
column, anywhere in the current UI or API) — `deployment/clickhouse/schema.sql` has no
`team_name` column and no `team.name` reference at all today. Every AC below that still mentions
`team_name` documents a decision that was later reversed, not current behavior.

## Acceptance Criteria

### AC-1: Session key normalization
The schema MUST normalize the dual session key. Path A uses `session.id` (dot-notation),
Path B uses `session_id` (underscore). A single promoted column must unify both.

### AC-2: Raw log table
A primary table (`coding_agent_logs`) MUST receive all OTel log records from both paths
via the OTel Collector. It MUST match the otelcol-contrib 0.105.0 ClickHouse exporter
log schema (Timestamp, TraceId, SpanId, SeverityText, ServiceName, Body,
ResourceAttributes, LogAttributes as Map, etc.).

### AC-3: Traces table
A table (`coding_agent_traces`) MUST receive OTel span records. It MUST match the
otelcol-contrib traces schema (TraceId, SpanId, ParentSpanId, SpanName, SpanKind,
Duration, SpanAttributes, ResourceAttributes, Events.* and Links.* nested arrays).

### AC-4: Metrics tables
Tables (`coding_agent_metrics_gauge`, `coding_agent_metrics_sum`) MUST receive OTel
metric data points. They MUST match the otelcol-contrib metrics schema
(MetricName, Attributes Map, TimeUnix, Value, Flags, Exemplars.*). The sum table
additionally includes `IsMonotonic` and `AggregationTemporality`.

> **Update (2026-08-14):** only `coding_agent_metrics_sum` actually exists in
> `deployment/clickhouse/schema.sql` — verified live on 2026-08-01 (per the schema file's own
> comment) that every Claude Code SDK metric is Sum type, so no gauge table was ever created.
> `coding_agent_metrics_gauge` would be auto-created by otelcol-contrib's `create_schema: true` if
> a gauge metric is ever emitted, but nothing has needed it so far. Two Sum-sourced rollups were
> added since the original design and read straight out of `coding_agent_metrics_sum` — see AC-17
> and AC-18 below.

### AC-5: Hook events derived table
A derived table (`coding_agent_hook_events`) MUST exist to efficiently serve session
timeline and tool analytics queries from Path B events without scanning the full
`coding_agent_logs` envelope. It MUST be populated automatically via a Materialized View
without requiring any OTel Collector configuration change.

### AC-6: Materialized View for hook event routing
A Materialized View (`mv_hook_events`) MUST filter `coding_agent_logs` rows where
`LogAttributes['event_type']` is one of the 6 Path B event types and route them to
`coding_agent_hook_events`.

> **Update (2026-08-14):** the live `mv_hook_events` filters on **13** event types, not 6 — see
> the corrected list in "Path B" above. The filter list grows as `analytics-hook` gains new event
> types; treat "6" here as a historical count, not a current invariant to test against.

### AC-7: Daily cost rollup table
A daily cost rollup table (`coding_agent_cost_daily`) MUST exist to efficiently serve
the `/cost` and `/users` read endpoints without querying raw logs on every request.
It MUST aggregate per (day, user_email, model_name, query_source, team_name).

> **Update (2026-08-14):** the live ORDER BY is `(day, session_id, user_email, model_name,
> query_source)` — `team_name` is gone (team removed) and `session_id` was added instead. The
> `session_id` addition matters beyond just being a key change: it's what lets session-scoped
> queries (e.g. `get_session_cost_by_source`, `get_session_lines_daily` — see the
> [Session Detail Deep-Dive](../2026-08-13-epmcdme-13580-session-deep-dive/spec-cost.md)) read this
> rollup directly instead of falling back to raw-log parsing, per the comment in
> `schema.sql`: "session_id is in ORDER BY so proxy sessions (user_email='') remain distinct rows
> and can be resolved via JOIN with coding_agent_hook_events at read time."

### AC-8: Materialized View for cost rollup
A Materialized View (`mv_cost_daily`) MUST filter `coding_agent_logs` rows where
`LogAttributes['event.name'] = 'api_request'` and populate `coding_agent_cost_daily`
with token counts, cost_usd, and team_name. All cost fields are sourced directly from
Claude Code's `claude_code.cost.usage` OTel output — no server-side pricing table needed.

> **Update (2026-08-14):** drop "and team_name" — `mv_cost_daily` populates `day, session_id,
> user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
> cache_read_tokens, cache_creation_tokens, api_call_count`. No team_name column exists to
> populate.

### AC-9: Read endpoint support
The schema MUST support the following read endpoints:
- `GET /v1/analytics/coding-agents/cost` — via `coding_agent_cost_daily`
- `GET /v1/analytics/coding-agents/sessions` — via `coding_agent_logs` + `coding_agent_hook_events`
- `GET /v1/analytics/coding-agents/tools` — via `coding_agent_hook_events` + `coding_agent_traces`
- `GET /v1/analytics/coding-agents/users` — via `coding_agent_cost_daily`

> **Update (2026-08-14):** the endpoint family grew well past these four. Also live today:
> `GET /v1/analytics/coding-agents/` (summary), `GET .../project-folders`,
> `GET .../sessions/{id}` (session detail — reads `coding_agent_cost_daily` +
> `coding_agent_lines_daily` too, not just logs/hook_events; see the session-detail-deep-dive spec
> linked above), and `GET .../users/{email}/detail` (reads `coding_agent_lines_daily` +
> `coding_agent_active_time_daily` + a repo breakdown from hook events). None of these existed —
> or needed schema support — at the time of the original design.

All endpoints MUST support `?team=` filtering. `coding_agent_logs` and
`coding_agent_hook_events` MUST carry a promoted `team_name` column for this filter.
`coding_agent_cost_daily` MUST include `team_name` in its ORDER BY for efficient
team-level rollups.

> **Update (2026-08-14):** none of this AC holds anymore — there is no `?team=` filter on any
> endpoint and no `team_name` column anywhere in the schema. Superseded by `?project_folder=`
> (backed by `codemie_project_name`/`project_folder`, promoted on `coding_agent_logs` and
> `coding_agent_hook_events`) as the closest current equivalent grouping filter.

### AC-10: TTL and partitioning
Raw tables (coding_agent_logs, coding_agent_traces, coding_agent_hook_events, metrics)
MUST have a 90-day TTL. The cost rollup table MUST have a 365-day TTL.
All tables MUST partition by month (toYYYYMM).

> **Update (2026-08-14):** the two newer SummingMergeTree rollups
> (`coding_agent_lines_daily`, `coding_agent_active_time_daily`) both got the same 365-day TTL and
> monthly partitioning as `coding_agent_cost_daily`, consistent with this AC's intent even though
> they postdate it. `coding_agent_session_identity` (AggregatingMergeTree) has **no TTL and no
> partitioning at all** — it's a small per-session identity rollup, not a time-series table, so
> this AC's "all tables" doesn't quite apply to it. Not a violation so much as a table this AC
> never anticipated.

### AC-11: Idempotent DDL
All DDL statements MUST be idempotent (CREATE TABLE IF NOT EXISTS, etc.) so the
schema file can be re-applied safely.

### AC-12: Smoke test
A smoke test script MUST validate that all schema objects exist and the MV routing
pipelines are functional (INSERT → SELECT assertions for both MV paths).

### AC-13: Team dimension promoted across all tables — **removed, not current behavior**
A `team_name` promoted column MUST be present in `coding_agent_logs` (from
`ResourceAttributes['team.name']`), `coding_agent_hook_events` (routed via
`mv_hook_events`), and `coding_agent_cost_daily` (routed via `mv_cost_daily` and
included in the ORDER BY key). Bloom filter index MUST exist on `team_name` in
`coding_agent_logs` and `coding_agent_hook_events`.

> **Update (2026-08-14):** none of this is true of the current schema. There is no `team_name`
> column, no `team.name` attribute reference, and no bloom filter on any such column in
> `deployment/clickhouse/schema.sql`. Kept here (rather than deleted) as the historical record of
> a decision that was later reversed — see "Team Dimension" under Data Paths above for why.

### AC-14: Prompt body stored for agent.prompt.submit events
`coding_agent_hook_events` MUST include a `prompt_body String` column. `mv_hook_events`
MUST populate it from `LogAttributes['prompt_body']` so the user prompt text from
`agent.prompt.submit` events is preserved (EPMCDME-13555).

### AC-15: api_request identity and sequence fields promoted in coding_agent_logs
`coding_agent_logs` MUST include MATERIALIZED columns for `user_id` (from
`LogAttributes['user.id']`), `organization_id` (from `LogAttributes['organization.id']`),
`agent_name` (from `LogAttributes['agent.name']`), `skill_name` (from
`LogAttributes['skill.name']`), and `event_sequence` (from `LogAttributes['event.sequence']`
as UInt32) to support efficient filtering and session timeline ordering without map lookups.
`user_id` MUST have a bloom filter index.

### AC-16: Tool/interaction span fields promoted in coding_agent_traces
`coding_agent_traces` MUST include MATERIALIZED columns for `file_path` (from
`SpanAttributes['file_path']`), `subagent_type` (from `SpanAttributes['subagent_type']`),
`span_skill_name` (from `SpanAttributes['skill_name']`), and `interaction_sequence`
(from `SpanAttributes['interaction.sequence']`) to support `/tools` endpoint queries
without full map scans (EPMCDME-13554, EPMCDME-13561).

> **Note (2026-08-14):** these promoted columns still exist and are still accurate schema-wise, but
> in practice `subagent_type`/`skill_name` on trace spans turned out to have near-zero coverage
> (~0.4%, per `ANALYTICS_DISCOVERY/`) — real dispatch/skill attribution moved to Path B
> (`agent.subagent.start`/`.stop` hook events, `agent_id`/`agent_type` fields — see the updated
> "Path B" section above) and to `coding_agent_logs.agent_name`/`skill_name` (AC-15, native
> `api_request` tagging). These trace columns are still queried as a fallback where Path B has no
> equivalent (e.g. per-tool-call `duration_ms`), just not for subagent/skill identification anymore.

### AC-17: Per-session email identity rollup — new since original design
`coding_agent_session_identity` (AggregatingMergeTree, `ORDER BY session_id`) MUST accumulate two
`SimpleAggregateFunction(max, String)` columns — `jwt_email` (from the CodeMie JWT, primary) and
`developer_name` (git/OS user, fallback) — per session, populated by `mv_session_identity` from
`coding_agent_logs`. A `v_session_email` view MUST expose the 2-level coalesce
(`jwt_email` → `developer_name`) as `resolved_email`, joinable by `session_id`. This exists
because Path B events (which carry rich session context) don't reliably carry a JWT-verified
email — this rollup lets read queries resolve "whose session was this" even when the two paths'
identity signals disagree or one is missing.

### AC-18: Daily lines-of-code rollup — new since original design
`coding_agent_lines_daily` (SummingMergeTree, `ORDER BY (day, session_id, user_email,
model_name)`, 365-day TTL) MUST aggregate `claude_code.lines_of_code.count` Sum metrics into
`lines_added`/`lines_removed` per day/session, populated by `mv_lines_daily` from
`coding_agent_metrics_sum` (split by `Attributes['type'] = 'added' | 'removed'`). Backs the
Sessions-tab and session-detail-modal "Daily Lines Changed" widgets and the user-detail modal's
identically-named panel — see
[Session Detail Deep-Dive](../2026-08-13-epmcdme-13580-session-deep-dive/spec-activity.md).

### AC-19: Daily active-time rollup — new since original design
`coding_agent_active_time_daily` (SummingMergeTree, `ORDER BY (day, session_id, user_email)`,
365-day TTL) MUST aggregate `claude_code.active_time.total` Sum metrics (seconds in the SDK,
stored here as milliseconds — `Value * 1000`) into `active_ms_user`/`active_ms_cli` per day/
session, populated by `mv_active_time_daily`, split by `Attributes['type'] = 'user' | 'cli'`.
Backs the Overview tab's "Active Time" stat tile (handler sums both columns as total non-idle
time) and the user-detail modal's active-time breakdown.

## Out of Scope for v1
- Full assistant response text, raw tool input/output bodies (privacy-sensitive, opt-in)
- MR/ticket context linking sessions to work items
- Reasoning tokens (not yet in native OTel)
- `user_prompt` span attribute not promoted (unbounded text size; accessible via SpanAttributes map)
