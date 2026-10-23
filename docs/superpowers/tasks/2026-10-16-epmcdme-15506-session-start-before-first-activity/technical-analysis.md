# Technical Research

**Task**: cli analytics sessions duration
**Generated**: 2026-10-16
**Research path**: filesystem

(The codegraph MCP tool is not available in this environment, so research used the filesystem fallback: one orientation pass plus four parallel Explore threads. All paths are relative to `/Users/mariia_konchak/Documents/codemie-dev/codemie`.)

---

## 1. Original Context

EPMCDME-15506 [CLI Analytics][Sessions] Session start time is later than its own first recorded activity, causing incorrect session duration.

Ticket (verbatim summary): Environment CodeMie Preview, Analytics -> CLI Analytics -> Sessions / Session Detail. For session b4685a4a-681c-409f-833e-d2255b4b142a an event attributed to the session occurred at 2026-09-21 15:56:17 UTC while the Sessions API reports start_time 2026-09-21 15:56:29 UTC (12 s later). duration_ms = 628000. Expected: start_time must not be later than the earliest event/activity that belongs to the same session. Acceptance criteria: (1) For every session, start_time is not later than the earliest event included in that session. (2) Session timeline fields are derived consistently from the same session identity. (3) Session duration is recalculated from the documented timestamp rules. (4) The fix is covered by an automated test containing an event earlier than the currently derived session start. (5) Existing Users Avg Session Duration calculations continue to use the corrected session duration data according to their documented aggregation rule.

Root-cause analysis already done in conversation (verify, extend, do not just repeat):
- Backend (this repo, /Users/mariia_konchak/Documents/codemie-dev/codemie): session_dims.started_at = min(ts) of the 13 DIMENSION_HOOK_TYPES hook events only (src/codemie/repository/cli_analytics/postgres/rollups.py ~1622-1637; vocabulary.py:79). Sessions list duration = last_event_at - started_at (src/codemie/service/analytics/handlers/cli_analytics_handler.py ~621-625); Session Detail meta duration = last_event_at - started_at (src/codemie/repository/cli_analytics/postgres/reader.py get_session_detail_meta ~545-570). Session Detail events come from OTel log_events (api_request) and spans (tool_call) (reader.py get_session_detail_events ~623). session_dims also has activity_started_at / ended_at / duration_ms (transcript or raw-record range) per .ai-run/guides/integration/cli-analytics-storage.md (~lines 52, 144, 196-197, 222). Window membership uses started_at (reader.py _sessions_cte / _STARTED_IN_WINDOW).
- Client (/Users/mariia_konchak/Documents/codemie-code, OUT OF SCOPE for code changes here, context only): OtlpAgentAdapter.ts:126 stamps hookTime = new Date() after isTracked/ensureOtlpProxy/evaluate/resolveCommonFields, so hook timestamps lag real activity.

Scope of this task: BACKEND fix in the codemie repo only. Research specifically: every place start_time / duration_ms / avg session duration is produced for CLI analytics sessions (list, detail, users avg session duration, get_session_durations / reader.py ~466, get_session_start_times ~521, dispatch bar positions _bar_position relative to session_start in cli_analytics_handler.py), what "earliest event of the session" sources exist (hook_events, log_events, spans, usage_requests, activity_started_at), whether activity_started_at is populated for OTel-only and plugin sessions, how rollups compute activity_started_at fallback (fb.first_ts), the alembic schema for session_dims, existing tests for these readers/handlers (tests/ paths, PostgreSQL integration test harness vs unit mocks), and the trade-off of changing session_dims.started_at itself (window semantics, documented contract) vs correcting only the reported start_time/duration in readers. Also check the users-avg-session-duration handler path and the guide text that documents duration rules.

Keep the research focused and write technical-analysis.md as soon as the findings are in.

---

## 2. Codebase Findings

### Existing Implementations

**Writer side: rollups (`src/codemie/repository/cli_analytics/postgres/rollups.py`)**
- `rollups.py:1622-1641`: the DIMENSIONS upsert, which is the ONLY writer of `session_dims.started_at` and `last_event_at`. Hypothesis confirmed.
  - Values: `min(h.ts) FILTER (WHERE h.event_type = ANY(_DIM_TYPES))` and `max(...)`, read from `hook_events` only.
  - On conflict: `started_at = LEAST(sd.started_at, EXCLUDED.started_at)`, `last_event_at = GREATEST(...)`.
  - `_DIM_TYPES` is defined at `rollups.py:155` from `vocabulary.DIMENSION_HOOK_TYPES` (`src/codemie/repository/cli_analytics/vocabulary.py:79-93`, 13 types; `agent.skill.dispatch` is not one of them).
  - `_earliest_merged` (`rollups.py:189-194`) keys the "earliest value" columns on `sd.started_at < EXCLUDED.started_at`.
- `rollups.py:660-665`: comment saying the SESSION family "never [writes] started_at, last_event_at or another column of the initial schema". So `started_at` (DIMENSIONS, hooks only) and `activity_started_at` (SESSION) come from separate statements and nothing ties them together.
- `rollups.py:924-928` `_RAW_RANGE`: `min(ts)` / `max(ts)` over ALL raw tables: `hook_events` (all types), `usage_requests`, `log_events`, `spans`, `metric_points`. Line 1005 exposes this as `fb.first_ts` / `fb.last_ts`. It is used only when `_FALLBACK_DUE` (`rollups.py:869`: `sm.ts IS NULL AND cur.summary_ts IS NULL`) holds.
- `rollups.py:1112-1114`:
  - `activity_started_at = _summary_or(_json_ts(sm.attrs,'started_at'), fb.first_ts)`
  - `ended_at = _summary_or(.. 'ended_at', fb.last_ts)`
  - `duration_ms = _summary_or(summary duration_ms, _FALLBACK_DURATION)`
  - `_FALLBACK_DURATION = trunc(epoch(fb.last_ts - fb.first_ts)*1000)` (lines 872, 1067).
- `rollups.py:1138-1157` merge on conflict:
  - When the summary wins (`_SUMMARY_WINS`, line 865), its values are written as sent.
  - When the fallback runs (`_FALLBACK_RUNS`, line 866): `activity_started_at = least(...)`, `ended_at = greatest(...)`, `duration_ms = ms(greatest(ended) - least(activity_started))`.
  - Otherwise the stored value stays.
- `rollups.py:867` `_PURGED_TAIL`; `rollups.py:853`: over a purged tail, the stored fallback values are kept.
- `rollups.py:1330`: a duration computed from two client times is NULL when the end is before the start.
- `rollups.py:1619-1621`: comment saying a recompute merges into the stored row and keeps the earliest values when raw rows were purged.

**Ingest dirty marking (`src/codemie/repository/cli_analytics/postgres/dirty.py`)**
- `dirty.py:126-128`: hook ingest marks DIMENSIONS|SESSION.
- `dirty.py:100-121`: log, span and usage ingest marks SESSION only, never DIMENSIONS. **OTel data can never move `started_at`.**

**Reader side (`src/codemie/repository/cli_analytics/postgres/reader.py`)**
- `reader.py:53` `_STARTED_IN_WINDOW = "started_at BETWEEN $start_dt AND $end_dt"`.
- `reader.py:155-192` `_sessions_cte` (`sel`):
  - `WHERE d.started_at IS NOT NULL AND {_STARTED_IN_WINDOW}`.
  - Exposes `d.started_at` and `d.last_event_at` (lines 186-187).
  - This is the single place that decides window membership.
- `reader.py:131` `_ms(x)` = `floor(epoch*1000)::bigint`.
- `reader.py:462-469` `get_session_durations`: `_ms(last_event_at) - _ms(started_at) AS duration_ms FROM sel`. It feeds the overview KPI and Users Avg Session Duration.
- `reader.py:482-519` `get_session_cost_facts`: `max(s.started_at)`, `max(s.last_event_at)` (lines 506-507). It feeds the Sessions list.
- `reader.py:521-527` `get_session_start_times`: `SELECT started_at FROM sel`. It feeds the activity heatmap.
- `reader.py:545-572` `get_session_detail_meta`:
  - CTE `h` = the earliest row of `log_events` ∪ `spans` (`ORDER BY ts, resource_id LIMIT 1`), used ONLY to pick `otel_resources.service_name`.
  - `started_at = coalesce(d.started_at, epoch)` (line 564).
  - `duration_ms = coalesce(_ms(d.last_event_at) - _ms(d.started_at), 0)` (line 565).
  - The row is driven by `cost_daily` (line 548). The router also calls it for project scoping (`cli_analytics.py:643`).
  - **The earliest OTel timestamp is already computed in this query, but it is not used for `started_at`.**
- `reader.py:623-639` `get_session_detail_events`: `log_events.ts` (event_kind API_REQUEST → `api_request`) and `spans.ts` (span_kind TOOL, tool_name not empty → `tool_call`). It returns no hook events. The event in the ticket that precedes the start is one of these.
- `reader.py:641-769` `get_session_detail_dispatches`:
  - `dispatch_start = coalesce(agent.tool.start hook ts / ack ts, spans.ts)` (line 739).
  - Slash-command skills use `hook_events.ts` (line 761).
- `reader.py:277-303` `get_users_last_active`: uses `d.last_event_at`, or `max(h.ts)` over the dimension hooks.
- `reader.py:201-275`: cost KPIs, model breakdown, users and daily activity are all scoped by `sel`, i.e. by `started_at`.

**Handler (`src/codemie/service/analytics/handlers/cli_analytics_handler.py`, `LocalAnalyticsHandler` at line 266)**
- Lines 621-625, Sessions list: `duration_ms = max(0, int((last_event - started_at).total_seconds()*1000))`, Python timedelta math (`_ms` floor semantics can differ by 1 ms). `start_time = _iso(started_at)` at line 634. Line 679: `data_as_of = max(start_time)`.
- Lines 344 and 357, Overview KPI: `duration_ms = sum(get_session_durations if sid in cost_sessions)`.
- Lines 503-506, Users Avg Session Duration (the "D1 rule"):
  - `avg = int(sum(durations)/len(durations))` over `get_session_durations`, restricted to priced sessions.
  - `None` when there are no priced sessions.
  - Returned as `avg_session_duration_ms` (line 515).
- Lines 737-761, Session detail: `started_at = meta["started_at"]`, `duration_ms = _i(meta["duration_ms"])`. Both are passed to `_build_dispatches` (lines 739-748).
- Lines 92-106 `_bar_position`:
  - `offset = int((dispatch_start - session_start).total_seconds()*1000)`.
  - An agent with `offset < 0` gets `(0, None)`, so an agent dispatched before a late `started_at` loses its bar. Skills and commands can show a negative offset.
- Lines 75-79 `_clamp_to_session`: clamps to `session_duration_ms`.
- Lines 798-803: the synthetic session lane is `start_offset_ms=0, duration_ms=session_duration_ms`.
- Lines 843-857 `get_activity`: the heatmap by weekday and hour of `started_at`.

**Router and models**
- `src/codemie/rest_api/routers/cli_analytics.py`:
  - prefix `/v1/analytics/cli-analytics` (line 132)
  - `/overview` 387-404, `/users` 427-445, `/user-charts` 448-465, `/activity` 537-552, `/sessions` 587-616, `/sessions/{trace_id}` 619-662
  - Detail `data_as_of = start_time` (line 658).
- `src/codemie/rest_api/models/cli_analytics.py`:
  - `LocalAnalyticsSessionRow` (169): `start_time: str` (181), `duration_ms: int` (182)
  - `LocalAnalyticsSessionDetail` (244); `LocalAnalyticsSessionEvent.timestamp` (215)
  - `LocalAnalyticsDispatchRow` (232): `start_offset_ms`, `duration_ms: int | None`
  - `LocalAnalyticsUsersData.avg_session_duration_ms: int | None` (157)
  - `LocalAnalyticsOverviewKPIs.duration_ms` (61)
  - There is no `end_time` field.
- `src/codemie/repository/cli_analytics/ports.py`: the `CliAnalyticsReader` protocol (lines 150, 161, 165, 173).

**Schema (`src/external/alembic_cli_analytics/versions/`)**
- `a1c1a0000001_cli_analytics_initial_schema.py:253-266`: `session_dims` has `session_id` PK, `started_at` (NULL when only non-dimension hooks exist), `last_event_at`, the dimension columns, `jwt_email`, `dev_name_max` and `updated_at`.
- `a1c1a0000006_cli_analytics_schema_extension.py`:
  - `_SESSION_DIMS_A` (52-66) adds `ended_at`, `duration_ms`, `active_ms`, etc.
  - `_SESSION_DIMS_B` (170-180) adds `activity_started_at` and `summary_ts`.
  - Index `session_dims_user_started (user_email, started_at)` (line 104).
- No SQLModel exists. Access is raw asyncpg.

### Architecture and Layers Affected
- **API**: `rest_api/routers/cli_analytics.py`. Probably no changes, since the response models stay as they are.
- **Service**: `LocalAnalyticsHandler` in `cli_analytics_handler.py`: sessions list duration, detail, users avg, overview KPI, `_bar_position` / `_build_dispatches`.
- **Repository**: `postgres/reader.py` (session queries) and the `ports.py` protocol if signatures change.
- **DB-Persistence / rollup pipeline**: `postgres/rollups.py` (DIMENSIONS and SESSION statements) and `postgres/dirty.py` (which ingest marks DIMENSIONS). These change only if the fix moves `session_dims.started_at` itself.
- **Migrations**: none needed. `activity_started_at` and `ended_at` already exist.

### Integration Points
- Data flow: router → `LocalAnalyticsHandler` → `CliAnalyticsReader` port → `PostgresCliAnalyticsReader` → tables. Rollups and dirty marking are on the ingest/scheduler side (`cli_analytics_rollup_refresh` job every 30 s).
- **"Earliest event of the session" sources**, all keyed by the same text `session_id`:

  | Source | Time column | Notes |
  |---|---|---|
  | `hook_events` | `ts` | Client-stamped and late (client issue). Only the 13 dimension types feed `started_at`. Index `(session_id, ts)`. |
  | `log_events` | `ts` | `event_kind` 1 = api_request. Index `(session_id, ts)`. Shown as detail events. |
  | `spans` | `ts` (span start) | `span_kind` TOOL is shown as `tool_call`. Index `(session_id, ts)`. |
  | `usage_requests` | `ts` | Index `(session_id, ts)`. |
  | `metric_points` | `ts` | |
  | `session_dims.activity_started_at` / `ended_at` | precomputed | Raw range over all of the above (fallback), or the client summary's `started_at` / `ended_at` when an `agent.session.summary` hook exists. |

- **Where `activity_started_at` is populated:**
  - OTel-only sessions: yes, via the fallback raw range. But such sessions have `started_at` NULL and are excluded from `sel` entirely.
  - Plugin sessions without a summary: yes, via the raw range across all 5 tables.
  - Plugin sessions with a summary: yes, from the client's transcript first line, which is NOT guaranteed to be ≤ the earliest OTel event.
- Shared constant: `vocabulary.DIMENSION_HOOK_TYPES` is used by rollups (`_DIM_TYPES`) and by the reader (`get_users_last_active`, reader.py:50).
- `maintenance.py:426-443`: the idle-session purge is keyed on `coalesce(greatest(last_event_at, ended_at), updated_at)`.

### Patterns and Conventions
- SQL is built from f-strings with `$name` placeholders and converted by `positional()` (reader.py:83). Times are normalised to naive UTC (reader.py:97).
- Millisecond diffs use `_ms(b) - _ms(a)` (floor) in the reader and `_ms_between` (trunc) in rollups.
- Upserts merge monotonically (LEAST/GREATEST, a NULL never overwrites). The summary-vs-fallback rule is `_SUMMARY_WINS` / `_FALLBACK_RUNS`.
- The single window predicate lives in `_sessions_cte`. Every windowed query reads `FROM sel`, and a test enforces this.
- A new reader query goes into the port and the PostgreSQL reader, together with a test (storage guide :21).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-storage.md`:
  - `:52` Window rule: "a session is in a date window if and only if `session_dims.started_at` is in `[start_dt, end_dt]` ... The predicate lives once, in `_sessions_cte` (`sel`) ... Sessions without `started_at` are excluded."
  - `:222` Two pairs of times: "`started_at` and `last_event_at` are hook times and decide the dashboard windows. `activity_started_at` and `ended_at` are the first and the last transcript line (the raw range in the fallback) and give the length of the session."
  - `:196-197` `activity_started_at`/`ended_at` come from the transcript when there is a summary, otherwise from the earliest/latest raw record. `duration_ms` is the summary value, otherwise "`ended_at − activity_started_at`: wall clock, idle time included".
  - `:133`, `:137-139`, `:142`, `:144` Merge kinds: a NULL never overwrites; summary columns are written only from a summary that is not older; fallback columns only while `summary_ts` is NULL; purged-tail rule.
  - `:118-119`, `:128` SESSION statements run for bits 1/4/8/32. A day is recomputed only inside raw retention.
  - `:252`, `:257-258` Backfill command and `ANALYZE` after it.
  - `:21` The API contract is fixed (D1/D3/D5/D10/D11).
- `.ai-run/guides/integration/cli-analytics-events.md`:
  - `:43` "`session_dims.started_at` and `last_event_at` come from the 13 activity hook events only. A session that sent none of those is in no dashboard window."
  - `:256-258` The summary's `started_at` is stored in `activity_started_at`. The summary's `duration_ms` = `ended_at − started_at` (client-supplied).
  - `:390-396` Identity and re-send rules: identity is `(type, session_id, event_id)`, and the first stored version stays.
- `.ai-run/guides/testing/testing-patterns.md`: mock provider boundaries unless integration is required.

### Architectural Decisions
- **The guide documents the length rule but the code does not follow it.** Per guide `:222`/`:197`, session length = `activity_started_at`/`ended_at`/`duration_ms`. The reader and handler instead compute `last_event_at − started_at` in every place: list, detail, `get_session_durations`, users avg and overview. Fixing this so the code follows the documented rule may be the most direct reading of AC3 ("documented timestamp rules").
- **EPMCDME-15228** (`docs/superpowers/tasks/2026-10-01-cli-analytics-users-avg-duration-priced-sessions/plan.md`), the D1 rule: Users `avg_session_duration_ms` = `int(sum/len)` over priced sessions only (the `cost_daily` JOIN `sel` universe), else `None`. AC5 must preserve this. A related remote branch exists: `EPMCDME-15228_CLI_Analytics_Incorrect_Avg_Session_Duration`.
- `docs/superpowers/tasks/2026-09-29-clickhouse-window-filter/spec.md:13,19,36`: the session-start window rule; `get_session_durations` uses `sel` only, with no overlap check.
- `docs/superpowers/specs/2026-09-23-cli-analytics-postgres-storage-design.md:1114-1117, :501`: the original `session_dims` design. `last_event_at` = max over the 13 hook types. D12 (negative dispatch duration) is "Not fixed" (`:155`).
- `docs/superpowers/tasks/2026-10-01-cli-analytics-sessions-timezone-filter/spec.md:48-49,97`: where `start_time` is built (line numbers have since moved, to roughly 634 and 737-761).
- `docs/superpowers/tasks/2026-08-13-epmcdme-13580-session-deep-dive/spec.md:40`: "Never fabricate a zero". A missing duration stays null. Note that `get_session_detail_meta` currently coalesces to 0.
- `rollups.py:663` comment: the SESSION family never writes `started_at`/`last_event_at`. This is an intentional separation.

### Derived Conventions
- Monotonic merges: start columns only move earlier (LEAST) and end columns only move later (GREATEST). A rebuild can therefore repair a too-late start, but cannot reset a too-early one.
- Computed client durations are NULL when the end is before the start.
- Commit format: `EPMCDME-15506: <Description>`.
- No TODO/HACK/NOTE/DECISION markers exist in `rollups.py`, `reader.py`, `vocabulary.py` or `cli_analytics_handler.py`. CHANGELOG.md has no relevant entries.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/repository/cli_analytics/postgres/test_rollups.py`: SQL-text tests via fake connections (`QueueConnection`, `Engine`, `PoisonQueue`, `GateQueue`).
  - Session-related: `test_session_statements_follow_dimensions_and_run_for_bits_1_4_8_32`, `test_recompute_updates_derived_columns_after_session_statements`, `test_a_computed_subagent_duration_is_never_negative`.
  - Assertions on `rollups._SESSION_DIMS` are around lines 1030-1050.
  - Nothing asserts how `started_at` is computed.
- `tests/codemie/repository/cli_analytics/postgres/test_reader.py`: SQL-text tests via `_SqlRecorder` / `_ArgRecorder`.
  - Window and start: `test_sessions_cte_decides_the_window_by_session_start`, `test_session_durations_have_no_overlap_check`, `test_session_start_times_rely_on_sel_alone`, `test_a_window_without_session_starts_returns_no_rows`, `test_session_start_boundaries_are_inclusive_at_both_ends`.
  - Session detail: `test_session_detail_meta_resolves_the_earliest_service_name`, `test_session_detail_meta_service_name_tiebreak_is_deterministic`.
  - Other: `test_every_fact_a_windowed_query_reads_is_scoped_to_the_selected_sessions`, `test_agent_dispatch_starts_at_its_launch_and_skills_at_their_tool_span`.
- `tests/codemie/repository/cli_analytics/postgres/test_session_derived.py`: derived columns (classifier, timeouts). No start or duration tests.
- `tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py`: Thread A reports it runs against a live Postgres via `TEST_LOCAL_PG_URL`. Thread B reports no PG harness at all. **Verify before planning.** If it is opt-in live PG, it is the only template for a real insert-then-query test.
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`: `LocalAnalyticsHandler` with `MagicMock`/`AsyncMock` repo rows.
  - Users avg: `_users_repo`, `test_get_users_avg_duration_only_priced_sessions`, `test_get_users_avg_duration_none_when_no_priced_sessions` (around lines 387-406).
  - Sessions list: `test_get_sessions_start_time_carries_utc_offset_for_naive_started_at`, built on `BASE_COST_ROW` (lines 44-45, `started_at`/`last_event_at`).
  - Detail: `make_detail_handler`, which mocks meta with `duration_ms: 0`.
  - Dispatches: `test_agent_dispatch_starts_at_dispatch_start_not_span_start`, `test_agent_run_is_clamped_to_the_session_end`, `test_agent_launched_before_the_session_has_null_duration_and_a_zero_offset`, `test_parallel_agents_render_as_overlapping_bars`, and others.
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler_ordering.py`: `test_sessions_with_equal_start_times_are_ordered_by_trace_id`.
- `tests/codemie/rest_api/routers/test_cli_analytics_storage_wiring.py`: router-to-reader wiring, including the use of detail meta for project scoping.
- Builders: `tests/codemie/repository/cli_analytics/support/hook_event_builders.py`, `otlp_builders.py`, `contracts.py`.

### Testing Framework and Patterns
- pytest 8, pytest-asyncio 1.x, pytest-xdist, pytest-env, pytest-mock and pytest-cov.
- Config is in `pytest.ini`: `testpaths=tests`, `pythonpath=src`, `--import-mode=importlib -n 3`, `asyncio_default_fixture_loop_scope=function`, and dummy `PG_URL` on port 111. Async tests need an explicit `@pytest.mark.asyncio`.
- `tests/conftest.py`: autouse `mock_database_engine` (session scope) and `isolate_run_reporting_state`; loads `tests/.env.test`.
- Repository tests assert on the SQL text (substring, regex, or `_blocks` parsing). Handler tests feed hand-made row dicts and call static helpers directly (`LocalAnalyticsHandler._build_dispatches`).
- Make targets: `make test`, `make verify` (ruff, license, gitleaks, test), `make test-harness` (external API sanity check).

### Coverage Gaps
- No test has a log_event or span earlier than the first dimension hook, at any level.
- No assertion on the `started_at` / `last_event_at` expression in the DIMENSIONS SQL.
- No reader test touches `activity_started_at` / `ended_at` / `duration_ms` in `get_session_durations`, `get_session_cost_facts`, `get_session_detail_meta` or `get_session_start_times`.
- `_bar_position` has no direct test.
- The users avg tests use fixed `duration_ms` values, so no test proves the average follows a corrected duration.
- Neither the sessions-list duration nor the detail `duration_ms` is asserted against its source columns.
- For AC4, the realistic options are:
  - handler-level tests where the reader rows carry an earlier activity start (list, detail, users avg, dispatch offsets);
  - SQL-shape tests in `test_reader.py` / `test_rollups.py`;
  - optionally a live-PG test, if `test_reader_live_dispatches.py` provides an opt-in harness.

---

## 5. Configuration and Environment

### Environment Variables
- `CLI_ANALYTICS_PG_URL`, `CLI_ANALYTICS_PG_SCHEMA` (default `codemie_analytics`), plus pool and timeout vars.
- `CLI_ANALYTICS_RAW_RETENTION_DAYS` (90): also the limit on how far back a rebuild can go.
- `CLI_ANALYTICS_ROLLUP_RETENTION_DAYS` (365).
- `CLI_ANALYTICS_SESSION_RETENTION_DAYS` (0 = keep forever).
- `CLI_ANALYTICS_DEDUP_RETENTION_DAYS` (14).
- `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` (30), `CLI_ANALYTICS_ROLLUP_BATCH_SIZE` (5000).
- `CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES` (60), `CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS` (4).
- The fix needs no new variables.

### Configuration Files
- `src/codemie/configs/config.py:200-226`: CLI analytics settings. A validator around line 986 requires rollup retention ≥ raw retention.
- `src/codemie/repository/cli_analytics/postgres/settings.py`: `AnalyticsPgSettings.from_config`.
- `src/codemie/repository/cli_analytics/factory.py`: storage is Postgres only.
- `config/customer/customer-config.yaml:119`: `features:cliAnalytics` (default `enabled: false`).

### Feature Flags and Deployment Concerns
- The only flag is `cliAnalytics`. When it is off, the router returns 404 (`cli_analytics.py:138`) and the jobs don't start (`service/analytics/cli_analytics_jobs.py:89`). There is no flag specific to sessions.
- Migrations: the separate Alembic env `src/external/alembic_cli_analytics/` runs at runtime start under an advisory lock. No migration is needed.
- **Backfill/recompute**: `python -m codemie.repository.cli_analytics.postgres.backfill --from YYYY-MM-DD --to YYYY-MM-DD [--now]` (`postgres/backfill.py`, documented as "repair after a fix").
  - It is limited to raw retention (90 days): older `session_dims` rows stay stale.
  - With `--now` it does not write `delivery_framework`.
  - Because the merge is `LEAST`, a fix that moves `started_at` EARLIER will be applied to existing rows by a backfill.
- A read-time-only fix (computing from columns that already exist) corrects historical rows immediately, without a backfill.

---

## 6. Risk Indicators

- **Window semantics change if `session_dims.started_at` itself moves.** `sel` (reader.py:155-192) scopes every dashboard query: KPIs, users, cost, model breakdown, activity heatmap, sessions. An earlier `started_at` can move sessions across a day or window boundary and change the totals. The documented contract (storage guide :52, :222; events guide :43) says `started_at` = hook times only. Any rollup-side fix must update both guides.
- **Ingest-path change needed for a rollup-side fix.** `dirty.py:100-121` never marks DIMENSIONS for log, span or usage ingest, so changing the DIMENSIONS SQL alone would not react to OTel rows that arrive later. This would need dirty-bit changes too. A DIMENSIONS min/max over five partitioned tables also costs more.
- **Summary-sourced `activity_started_at` is client-stamped.** For plugin sessions with `agent.session.summary`, `activity_started_at` is the transcript's first line, not the raw range. It may still be later than the earliest OTel `log_events`/`spans` row, so using `least(started_at, activity_started_at)` alone does not guarantee AC1. To guarantee it, the reader would also need `min(ts)` from `log_events`/`spans`, at least for the events shown in detail.
- **Summary `duration_ms` vs a computed range.** The documented length rule prefers the summary's `duration_ms` (client value). If start_time is corrected to an earlier raw event while `duration_ms` comes from the summary, then start + duration may not line up with the event range (AC2 consistency). This needs an explicit decision.
- **Five or more places compute duration independently**, with slightly different math:
  - `get_session_durations` (SQL `_ms` floor)
  - sessions list (handler timedelta)
  - `get_session_detail_meta` (SQL, coalesces to 0)
  - users avg and overview KPI (from `get_session_durations`)
  - dispatch clamping (`session_ms`)
  Missing one breaks AC2/AC3.
- **OTel-only sessions** (no dimension hooks) have `started_at` NULL and are excluded from every window. This is out of scope per the contract, but it is an edge case to state explicitly.
- `get_session_detail_meta` uses `coalesce(..., 0)` for duration, which goes against the "never fabricate a zero" rule from EPMCDME-13580.
- `_bar_position`: an agent dispatched before the (late) session start gets offset 0 and a null duration. A corrected, earlier start changes the dispatch offsets and the clamping. Existing test `test_agent_launched_before_the_session_has_null_duration_and_a_zero_offset` encodes the current behaviour and may need updating.
- **Test harness gap**: no confirmed real-PG harness (the threads disagree about `test_reader_live_dispatches.py`). SQL-shape tests cannot prove ordering at the data level, which weakens AC4 unless handler-level tests carry the behavioural assertion.
- **Historical data**: a rollup-side fix repairs only sessions still inside raw retention (90 days), and only after running the backfill. A read-time fix avoids this.
- **The API contract is fixed** (storage guide :21). Response models must keep their shape (`start_time: str`, `duration_ms: int`).
- **Regression risk on EPMCDME-15228** (D1 priced-sessions avg rule). The `get_session_durations` contract is shared.

---

## 7. Summary for Complexity Assessment

The defect sits in the Repository and Service layers of the CLI analytics read path, and optionally in the rollup pipeline.
- `session_dims.started_at` / `last_event_at` are written only by the DIMENSIONS upsert (`rollups.py:1622-1641`), from the 13 client-stamped dimension hook types. OTel ingest never marks DIMENSIONS (`dirty.py`).
- Every reader and handler computes session length as `last_event_at − started_at`: `get_session_durations` (reader.py:462-469), `get_session_cost_facts` → sessions list (handler 621-625), `get_session_detail_meta` (reader.py:545-572), Users Avg (handler 503-506), the overview KPI, and the dispatch timeline (`_bar_position`, `_clamp_to_session`).
- Session Detail events come from `log_events`/`spans` (reader.py:623-639), which can be earlier. That is the ticket's symptom.
- The guide already documents a separate length rule (`activity_started_at`/`ended_at`/`duration_ms`, storage guide :197/:222), and the code does not apply it.

Likely file change surface is about 2-4 source files:
- `reader.py` (the 3-4 session queries)
- `cli_analytics_handler.py` (list, detail, dispatch, users and overview paths)
- possibly `ports.py`
- the two CLI analytics guides
- if the team decides to move `session_dims.started_at` itself: also `rollups.py` and `dirty.py`
plus 2-3 test files (`test_reader.py`, `test_cli_analytics_handler.py`, possibly `test_rollups.py`). No migration, config change or feature flag is needed.

Technical novelty is low to moderate. The work follows established patterns: f-string SQL over `sel`, SQL-shape tests, and handler tests with mocked rows. The main decision is a design trade-off with real consequences:
- **(a) Read-time correction (lower risk).** Keep `started_at` as the window key and derive the reported start, end and duration from the earliest/latest activity: `least(started_at, activity_started_at, min OTel ts)`, and so on. This repairs history immediately and does not change window membership.
- **(b) Rollup-side change to `started_at`.** This changes window semantics across all dashboard KPIs, requires dirty-bit changes on OTel ingest, contradicts two guides, and only repairs history within 90 days after a backfill.

A further subtlety: for summary-bearing plugin sessions, `activity_started_at` is itself client-stamped, so AC1 may require the raw `log_events`/`spans` minimum. Combining a corrected start with a summary-sourced `duration_ms` also needs an explicit rule for AC2/AC3.

Test posture is mixed. There are many unit tests, but they check SQL strings or use mocked rows. None covers an event that precedes the derived start, nothing asserts the derivation of `started_at` or duration, and the users avg tests use fixed durations. Whether a real-PG harness exists is unclear (`test_reader_live_dispatches.py` may be opt-in via `TEST_LOCAL_PG_URL`). Key scoring factors:
- the need to change 5+ duration call sites consistently
- the regression surface of the shared `sel` window and of `get_session_durations` (EPMCDME-15228 D1 rule)
- existing tests that encode current behaviour (agent-before-session offset)
- the need for documentation updates

With option (a), this looks like a moderate-complexity bug fix (standard path). Option (b) raises it to high because of the cross-cutting change to window semantics.
