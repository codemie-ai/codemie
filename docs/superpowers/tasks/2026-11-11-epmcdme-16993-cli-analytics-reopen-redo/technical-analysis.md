# Technical Research

**Task**: cli-analytics postgres reader rollups session-window-admission duration kpi-tiles session-binding
**Generated**: 2026-11-11
**Research path**: filesystem (the codegraph MCP tool was not available in this environment; five parallel Explore agents with Glob/Grep/Read/git were used)
**HEAD**: bfdd2dcc1e, branch EPMCDME-16993_cli-analytics-reopen-redo. The last commit touching reader.py and rollups.py is 1a579c6a62 (EPMCDME-16633, 2026-11-10).

Path abbreviations used below:
- `R` = `src/codemie/repository/cli_analytics/postgres/reader.py`
- `RU` = `src/codemie/repository/cli_analytics/postgres/rollups.py`
- `V` = `src/codemie/repository/cli_analytics/vocabulary.py`
- `H` = `src/codemie/service/analytics/handlers/cli_analytics_handler.py`
- `RT` = `src/codemie/rest_api/routers/cli_analytics.py`

---

## 1. Original Context

task_context (verbatim from user):

"Check this document, here described what is done on cli side and what is need to be done on a backecnd - C:\Users\OlehPrusak\codemie-dev\codemie-code\docs\superpowers\tasks\2026-11-10-otlp-continued-reopened-redo\backend-handoff.md . Validate this ifnormation- do not blindly trust it. Here is the tasks im working on - https://jiraeu.epam.com/browse/EPMCDME-16993 https://jiraeu.epam.com/browse/EPMCDME-16992 Check the descriptions, acceptance criteria and expected result, and check the comments - there described specific cases i need to cover as well (especiaaly expected result)"

Additional inputs from the caller:
- Full Jira content (fetched 2026-11-11) is in `docs/superpowers/tasks/2026-11-11-epmcdme-16993-cli-analytics-reopen-redo/work-item.md`.
- The CLI handoff is `C:\Users\OlehPrusak\codemie-dev\codemie-code\docs\superpowers\tasks\2026-11-10-otlp-continued-reopened-redo\backend-handoff.md`.
- Binding research (handoff claim 2.4) is in `research-binding.md` in the same run dir. It was not redone; four of its references were spot-checked.

Note on provenance: this analysis was assembled by an orchestrator from five research agents' reports. Claims about the ticket and handoff text (for example "ticket says line 257") come from those agents' reading of the two documents. Every code claim below was verified by an agent against HEAD, with file:line.

---

## 2. Codebase Findings

### Existing Implementations

**Reader (admission, windowing, display range)** - `R`
- Window constants at R:165-177. `_STARTED_IN_WINDOW` is `started_at BETWEEN $start_dt AND $end_dt` (R:165). `_START_DAY_UTC`/`_END_DAY_UTC` are at R:169-170. `_FIRST_WHOLE_DAY_UTC`/`_LAST_WHOLE_DAY_UTC` are at R:176-177.
- `_ACTIVITY_TABLES = (turns_daily, tool_facts_daily, cost_daily, session_usage, lines_daily)` at R:180. `_activity_exists(alias, days=None, fenced=False)` at R:183-194 builds an OR of `EXISTS(... ax.session_id = alias.session_id [AND ax.day in range] [OFFSET 0])` over those tables.
- `_ACTIVITY_HOOK_TYPES` at R:200-209: prompt.submit, tool.start/end/error/denied, subagent.start, subagent.usage, `SKILL_DISPATCH_EVENT`. Lifecycle hooks (session.start/env/end, git.snapshot, summary) are excluded.
- `_RAW_ACTIVITY_SOURCES` at R:212-218 and `_raw_activity_in_window` at R:221-229. Raw activity is:
  - activity hooks;
  - any `usage_requests` row;
  - `log_events` with `event_kind <> 0` (OTHER);
  - `spans` with `span_kind <> 0`;
  - lines metrics, all with `ts` inside the window.
- `_active_in_window(alias, fenced, raw_first)` at R:232-245 is `(whole_days OR (window_days AND raw))`.
  - "Activity" means a row in one of the 5 rollup tables on a UTC day wholly inside the window.
  - On a partly covered edge day it also needs a raw record stamped inside the window.
- `_has_any_activity(alias)` at R:248-256 is `_activity_exists(alias)` with NO day bound. It is unbounded by the period.
- `_SESSION_ADMITTED_IN_WINDOW` at R:259 is `((_STARTED_IN_WINDOW AND _has_any_activity('d')) OR _active_in_window('d'))`. It is a reference rule only: no query uses it, and it appears only in docstrings (R:271, R:701) and the storage guide.
- `_admitted_arms(alias, columns, scope)` at R:270-301 is the live admission SQL. It is five UNION ALL selects over `session_dims`, all with `started_at IS NOT NULL`:
  1. R:294 `started AND a.has_activity` (stored fact).
  2. R:295 `started AND a.has_activity IS NULL AND _has_any_activity(a)`.
  3. R:296 `NOT started AND has_activity IS NULL AND _active_in_window(a)`.
  4. R:297-298 `outside AND a.resumed AND a.last_activity_day >= _START_DAY_UTC AND _active_in_window(a, fenced)`.
  5. R:299-300 `outside AND NOT resumed AND on_edge_day AND _active_in_window(a, fenced, raw_first)`.
- Stored facts `has_activity`, `last_activity_day`, `resumed` are derived from the same 5 tables by `postgres/session_activity.py` `DERIVE_SQL` (lines 66-85) and cover any day. They were added by alembic `a1c1a0000011` (commit b9202b422f, EPMCDME-16427).
- `_sessions_cte` is at R:691-743 (ids_only form at R:726, normal form at R:743). `_scope` is at R:757-759 (`session_id IN (SELECT session_id FROM sel)`). All windowed methods go through it.
- `_activity_range(alias)` at R:549-575 gives the reported start and end:
  - `start = least(started_at, activity_started_at)` (R:558).
  - `end = greatest(last_event_at, ended_at)` (R:559).
  - R:560-575 extends both with min/max `ts` of `log_events` and `spans` for the session within 1 day before the start and 1 day after the end. There is no `event_kind`/`span_kind` filter, so OTHER-kind housekeeping records count.
- Callers of `_activity_range`:
  - `_activity_cte` (R:746-754), used by the session list and `get_session_durations`;
  - `get_session_detail_meta` (R:1575-1630; calls at R:1578, 1622-1625).
- List duration comes from `get_session_cost_facts` (R:1192-1237, `duration_ms` R:1224-1226) and the handler (H:868-881). Detail `duration_ms = coalesce(ms(end) - ms(start), 0)` (R:1612) and `started_at = coalesce(r.session_start, epoch)` (R:1611).
- `active_ms` is not in the list payload. Detail reads it from `get_session_detail_scalars` (R:1668-1672) as a sum over ALL days of `active_time_daily`.

**Rollups** - `RU`
- `session_dims.started_at` and `last_event_at` are set at RU:2069-2092 (DIMENSIONS upsert).
  - `min/max(h.ts) FILTER (event_type = ANY(_DIM_TYPES))` at RU:2072-2073, from `hook_events` only. `_DIM_TYPES` is at RU:177.
  - On conflict (RU:2087-2088): `started_at = LEAST(sd.started_at, EXCLUDED.started_at)` and `last_event_at = GREATEST(...)`.
  - `started_at` can therefore only move earlier. `last_event_at` only later. Native OTel records never touch either column.
- `session_dims.activity_started_at` and `ended_at`:
  - `_ENDED_AT` is at RU:1285-1288: `CASE WHEN sm.ts IS NULL THEN fb.last_ts WHEN _IS_FINAL_SUMMARY THEN summary.ended_at ELSE greatest(summary.ended_at, fb.last_ts) END`.
  - `_ACTIVITY_STARTED_AT` is at RU:1290: `_summary_or(summary attrs.started_at, "fb.first_ts")`. `_summary_or` is at RU:1272-1273. `_json_ts` is at RU:1072-1088; an invalid string gives NULL.
  - Source A is the latest `agent.session.summary` hook (`sm` join, RU:1141-1146), using the client-supplied `attrs.started_at`/`ended_at`.
  - Source B (no summary, or a provisional one with `is_final=false`) is `fb.first_ts`/`fb.last_ts` = min/max `ts` over ALL raw rows of the session with no kind filter: `hook_events` (all types, including env/git.snapshot/session.start), `usage_requests`, `log_events`, `spans`, `metric_points` (`_RAW_RANGE`, RU:1104-1109, 1189, 1196).
  - Merge on conflict (RU:1397-1416):
    - when a new summary wins (`_SUMMARY_WINS`, RU:1051), `coalesce(EXCLUDED, sd)`, so a newer summary can even move the value later;
    - on the fallback path, `least(...)`/`greatest(...)` at RU:1399-1400, so it never moves later.
    - `duration_ms = ended_at - activity_started_at` (RU:1295-1296) is NOT read by the dashboard readers (storage guide line ~331).
- `hook_events.ts` is the client event `timestamp`; the server uses `now()` only when it is missing or invalid (`hook_events.py:33-44`). The hook ingest path therefore takes client-supplied times verbatim.
- Counters `session_dims.tool_calls`/`agents` are summary-first with a rollup fallback (RU:1156, 1166-1168, 1304, 1310).
- `session_dims` columns are defined in:
  - `src/external/alembic_cli_analytics/versions/a1c1a0000001_..._initial_schema.py:253-266` (`started_at`, `last_event_at`);
  - `a1c1a0000006_cli_analytics_schema_extension.py:57-65, 179` (`ended_at`, `duration_ms`, `active_ms`, `activity_started_at`, `summary_ts`);
  - `a1c1a0000007_..._session_routing.py:42` (`owner_email`, `subscription_type`, `project_origin`, `route_note`);
  - `a1c1a0000011_..._session_activity_facts.py:72-79` (`has_activity`, `last_activity_day`, `resumed`).
  The alembic head is `a1c1a0000011`.

**Vocabulary** - `V`
- `DIMENSION_HOOK_TYPES` (V:78-94) has 13 entries:
  - agent.session.start/stop/end/compact;
  - agent.prompt.submit;
  - agent.tool.start/end/error/denied;
  - agent.turn.error;
  - agent.subagent.start/stop;
  - agent.notification.
- Count is pinned by `tests/codemie/repository/cli_analytics/test_vocabulary.py:60-61`.
- `SKILL_DISPATCH_EVENT = "agent.skill.dispatch"` (V:97) is deliberately excluded. `agent.session.summary` and `agent.session.env` are also excluded.
- `EVENT_KIND` (V:61-68) maps only api_request, user_prompt, skill_activated, tool_result, tool_decision, api_error. Every other OTel event name (all six startup/housekeeping events) becomes `EventKind.OTHER` (0).
- There is no housekeeping list or filter anywhere in the vocabulary.

**OTLP ingest** - `postgres/otlp.py`
- `_log_from_record` (otlp.py:682-720) appends a `LogRow` to `log_events` unless `_hook_log` claims it. `event_kind = EVENT_KIND.get(name, OTHER)` at :701.
- Nothing is dropped or filtered at ingest. Session id comes from `session.id` or `session_id` (otlp.py:644, 689, 758, 848, 908).

**Binding** (claim 2.4; see section 5 for the verdict)
- `postgres/session_binding.py:86-93, 187`, `rest_api/routers/cli_analytics_ingest_route.py:61-70, 197-209`, `cli_analytics_project_gate.py:433-503`, `postgres/ingestor.py:505, 567-584`, `postgres/cli_classification.py:35-42`, `scope.py:158`.

**Handler/Router** - `H`, `RT`
- Routes: `/overview` (RT:525, H:514), `/cost` (RT:544, H:618), `/users` (RT:563, H:687), `/user-charts` (RT:585), `/repositories` (RT:625, H:768), `/activity` (RT:676, H:1228), `/efficiency` (RT:696, H:1318), `/frameworks` (RT:715), `/sessions/{trace_id}` (RT:778), plus the sessions list (H:956), `/tools` (H:1248).

### Architecture and Layers Affected

- **Ingest/Persistence**: raw tables `hook_events`, `usage_requests`, `log_events`, `spans`, `metric_points` (`ingestor.py`, `otlp.py`, `hook_events.py`); `session_dims` binding columns (`session_binding.py`).
- **Rollup (Service/Repository)**: `RollupRefresher` and `rollups.py` write `session_dims`, `turns_daily`, `tool_facts_daily`, `cost_daily`, `session_usage`, `lines_daily`, `invocations_hourly`, `active_time_daily`, and the derived `session_activity.py` facts.
- **Reader (Repository)**: `PostgresCliAnalyticsReader` admission (`_admitted_arms`), display range (`_activity_range`), and fact queries.
- **Handler (Service)**: `cli_analytics_handler.py` shapes list, detail and aggregates, with money and token rules (`cost_key_sql.py`).
- **API**: `cli_analytics.py` router.
- **DB migrations**: `src/external/alembic_cli_analytics/versions/` (needed if stored facts change).

### Integration Points

- CLI to backend: OTLP logs/metrics/traces and event-hooks, with X-CodeMie-* evidence headers (8 headers in `EVIDENCE_HEADERS`, `cli_analytics_ingest_route.py:61-70`).
- `session_dims` is the hub: binding writes owner/class/origin. Rollups write times and counters. The reader reads both.
- `test_session_activity.py` asserts `ACTIVITY_TABLES == reader._ACTIVITY_TABLES`. The reader tuple and the derived-fact tuple must stay in sync.
- The reader's `_ACTIVITY_HOOK_TYPES` (R:200-209) is a separate copy of "activity hook types" from `DIMENSION_HOOK_TYPES` (V:78-94). The two lists intentionally differ.

### Patterns and Conventions

- SQL lives in module-level string constants composed with f-strings, using asyncpg-style `$start_dt`/`$end_dt` parameters. Tests assert fragments of these strings.
- Every windowed fact query is scoped by `session_id IN (SELECT session_id FROM sel)`. `sel` is the admitted session set and is the only place the period is applied.
- The "fenced"/`OFFSET 0` and `raw_first` options on `_active_in_window` are planner hints from EPMCDME-16427. Semantics are intended to be the same as the reference rule `_SESSION_ADMITTED_IN_WINDOW`. Keep both in sync when changing admission.
- Rollup merges are monotonic: `LEAST` for starts and `GREATEST` for ends. Values widen and never shrink, except when a winning summary overwrites via `coalesce`.
- The guides `.ai-run/guides/integration/cli-analytics-storage.md` and `cli-analytics-events.md` document these contracts. They must be updated with any contract change (storage guide ~299-306 and ~331 on the `_activity_range` probes; events guide line ~118 on the six startup events not moving a session's start or end).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-storage.md`: documents `_activity_range` (~line 331), the 1-day probes (299-306), and that `session_dims.duration_ms` is not read by the dashboard.
- `.ai-run/guides/integration/cli-analytics-events.md`: line 118 says "None of the six moves a session's start or end: `started_at` and `last_event_at` come from the 13 activity hook events only." This is true for the stored columns. It is FALSE for the reported end, because `_activity_range` probes `log_events`/`spans` without a kind filter. The guide statement needs a qualifier.
- `.ai-run/guides/data/database-patterns.md` and `.ai-run/guides/testing/testing-patterns.md`: general SQLModel and pytest policy. The testing guide says to use the narrowest scope and to report the exact command.
- `.ai-run/guides/development/local-testing.md`: report missing prerequisites as blocked, not failed.
- None of the testing guides mention `TEST_LOCAL_PG_URL` or the CLI analytics live tests.

### Architectural Decisions
- EPMCDME-16496 (commit e95e1d2a08, 2026-11-05) introduced the activity-based window and the `_has_any_activity`/`_active_in_window` pair. It did not touch `_activity_range` and did not day-bound `_has_any_activity`. Before it the window was `started_at BETWEEN` only.
- EPMCDME-16427 (commit b9202b422f, 2026-11-09) split admission into five index-served arms with stored facts. Semantics intentionally unchanged. The doc notes one accepted divergence for windows older than the rollup retention.
- EPMCDME-15506 (ae985d2dbf, 2026-10-23, plus ef04bf6c11 and 61dbc6beae) introduced `_activity_range`. EPMCDME-15584 (c4f5bd5445, 2026-10-26) added the `ended_at`/`duration_ms`/`activity_started_at` fallback and the provisional-summary rule.
- No commit or code for EPMCDME-16847 (the housekeeping filter mentioned by the caller) exists in this repo or branch: `git log --all --grep=16847` is empty, and no "housekeeping" appears in `src`, the guides or the tests. It lives only in the task docs (work-item.md:49, research-binding.md:31,44). A fix for it must be built here, or it is on another branch not available locally.

### Derived Conventions
- The `started_at IS NOT NULL` (a dimension hook exists) requirement gates all five arms. A session with cost rows but no `session_dims.started_at` has a detail page (R:1582-1588) but is in no aggregate.
- Lifecycle events are intentionally "not activity" in admission (R:200-209). That convention is not applied to the display range or to `last_event_at`, which is the gap behind the tickets.

---

## 4. Testing Landscape

### Existing Coverage

Non-live tests that encode current behavior (all under `tests/codemie/repository/cli_analytics/postgres/` unless noted):
- Admission, SQL-shape only:
  - `test_reader.py:453` `test_sessions_cte_decides_the_window_by_session_start` (asserts the five arm fragments `_ARM_STARTED`, `_ARM_UNDERIVED`, `_ARM_OUTSIDE_UNDERIVED`, `_ARM_RESUMED`, `_ARM_ONE_DAY`, defined at :435-449).
  - `test_reader.py:465` `test_sessions_cte_times_a_partial_window_day_by_its_raw_records`.
  - `test_reader.py:519` `test_session_start_times_rely_on_sel_alone`.
  - `test_reader.py:556`, `:633`, `:1368`, `:1377` (window bound handling).
  - `test_session_activity.py:26,36,40,49` (stored-fact invariants).
- Duration and range:
  - `test_reader.py:527` `test_session_queries_report_the_activity_range`.
  - `test_reader.py:538` `test_activity_probes_are_bounded_by_a_day_around_the_hook_times`.
  - `test_reader.py:479` `test_session_durations_have_no_overlap_check`.
  - `test_reader.py:547` `test_users_last_active_has_no_overlap_where`.
  - `test_reader.py:1035` start-time page orders by the stored start.
  - `test_rollups.py:1563` `test_a_computed_session_duration_is_never_negative`.
  - `test_rollups.py:1590` `..._malformed_is_final_value_defaults_to_final...`.
  - Handler tests under `tests/codemie/service/analytics/handlers/`: `test_cli_analytics_handler.py:106, 574, 1541, 1485, 1507`.
- KPI tiles and scope:
  - `test_reader.py:648` parametrized `test_cost_methods_have_no_day_window_and_are_bound_to_sel`. This encodes the current all-time behavior for admitted sessions, so a fix that adds day filters will break it and require an intentional update.
  - `test_reader.py:386` `test_every_fact_a_windowed_query_reads_is_scoped_to_the_selected_sessions`.
  - `test_reader.py:1151`.
  - Handler: `test_cli_analytics_handler.py:1643, 1718`; `test_cli_analytics_cost_efficiency_parity.py:89`; `test_cli_analytics_class_lists.py:79, 315`.
- Rollups: fake-connection tests in `test_rollups.py` (`QueueConnection`, `Engine` at :30-77; tests at :79, 89, 97, 121, 249, 628, 649, 900) and `test_session_derived.py` (`DerivedConnection`, `ClassifierSpy`, :29, :49). `test_dirty.py:290`.
- Vocabulary: `tests/codemie/repository/cli_analytics/test_vocabulary.py:60-61`.
- Binding: `test_cli_analytics_session_resolution.py` (:199, 426, 440, 455), `test_cli_analytics_evidence_routing.py` (:457, 519, 643, 667), `test_cli_analytics_funding.py:134`, `test_cli_analytics_consent_routing.py:206`, `test_ingestor.py:561`, `test_binding_contract_columns.py`, `test_legacy_bound_destination.py`.

Live PostgreSQL tests (real DB):
- `test_live_route_pricing.py` covers route-pricing money. It runs the real `RollupRefresher` and the real `PostgresCliAnalyticsReader`/`LocalAnalyticsHandler`.
- `test_live_session_subagent_cost.py` covers the EPMCDME-16441 subagent totals.
- Per the history, `test_live_session_activity_window.py` (798 lines, added by e95e1d2a08) and `test_live_session_activity.py` / `test_live_session_page.py` (b9202b422f) were added as live tests. The test agent found no source for them under `tests/` at HEAD, only two live files plus stale `.pyc` for deleted live tests. This is a discrepancy: the git history says these files were added, so they may have been deleted or renamed later. Resolve it with `git log --diff-filter=D --name-only -- tests/codemie/repository/cli_analytics/postgres` before relying on them. The caller's brief expects live tests for admission, and none exist at HEAD.

### Testing Framework and Patterns
- pytest ^8.3.1 (pyproject.toml:200), pytest-asyncio ^1.4.0, pytest-cov, pytest-env, pytest-mock, pytest-httpx, pytest-xdist ^3.6.1. Python >=3.12,<3.14.
- `pytest.ini`: `addopts = --import-mode=importlib -n 3`, `pythonpath = src`, `asyncio_default_fixture_loop_scope = function`. Env pins `PG_URL=postgresql://pg:pg123@localhost:111/postgres` (a dead port), so the default suite never touches a real DB. The only registered marker is `enterprise`.
- The default pattern is fake asyncpg connection/engine classes plus string assertions on SQL constants. There are no data-object "fake sessions".
- Live pattern:
  - Gate: `_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")` with `pytestmark = skipif(not _LOCAL_PG_URL)` (test_live_route_pricing.py:57-58). The route-pricing file builds its engine through `AnalyticsPgSettings.from_config(Config(...))` and `PostgresAnalyticsRuntime(...).start()` (migrates the DB) under an advisory lock. The subagent-cost file defaults to `postgresql://postgres:password@localhost:5432/postgres` and does not migrate.
  - Both skip, not fail, when the DB is unreachable.
  - Rollup refreshes recompute every queued key in the DB, so tests take `live_rollup_lock(engine)` (`tests/codemie/repository/cli_analytics/support/live_rollup_lock.py`, `pg_advisory_lock(0x726F6C6C7570)`).
  - There is no live-PG fixture in `conftest.py`. `tests/conftest.py:143` mocks `PostgresClient.get_engine`.
- Command (from the test docstrings; `-n0` is required to override the xdist default):
  `TEST_LOCAL_PG_URL=postgresql://<user>:<pw>@<host>:<port>/<db> poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py`
- Makefile: `test:` is `poetry run pytest tests/` (Makefile:30-31). `verify` is `ruff license gitleaks test`. There is no make target for live PG tests. Use a disposable DB.
- Per AGENTS.md, tests are only written or run when the user explicitly asks.

### Coverage Gaps
- No real-DB test of the admission rule. Arm-logic bugs would pass the SQL-shape tests.
- No test exercises `session_dims.started_at`/`last_event_at` end to end (ingest to rollup to reader).
- No test for the housekeeping/lifecycle-only continuation: no records, no code.
- No test for Start/End display when `ended_at` is NULL.
- No test that an evidence request with only lifecycle or housekeeping records must not bind a session.
- No test of detail-vs-aggregate tool-call and agent parity.
- No test for a reopen on D2 versus a D1 session (the 16993 scenario). Two fixtures are required (see section 7).

---

## 5. Handoff Claim Verification

Claims come from handoff sections 2.1-2.5 and the ticket root-cause sections, as read by the research agents. The handoff's own line numbers were stale on every item checked. Where the claim's gist was right but the details were wrong, the verdict is PARTIAL.

| # | Claim (handoff / ticket) | Verdict | Actual code location on HEAD | Notes |
|---|---|---|---|---|
| 2.1a | `_SESSION_ADMITTED_IN_WINDOW` admits `started_in_window AND _has_any_activity OR _active_in_window` | PARTIAL | R:259 (ticket/handoff cite 257) | Formula is right, but it is not live SQL: no query uses it. Live admission is `_admitted_arms` R:270-301 (handoff cites 268), five arms with stored facts. Docstring mentions R:271, R:701. |
| 2.1b | `_has_any_activity` is unbounded by the period | CONFIRMED | R:248-256 (handoff cites 214-222) | `_activity_exists(alias)` with no `days`. Arms 1 and 2 (R:294-295) use it, arm 1 via the stored `has_activity` fact (any day, `session_activity.py:66-85`). A session whose `started_at` is in the window is admitted if it has rows on ANY day. |
| 2.1c | A reopen whose first dimension hook lands on D2 moves `started_at` into D2 | PARTIAL | RU:2072, RU:2087 | Only if the session had no earlier dimension hook. `started_at = LEAST(...)` never moves later. It fits 8ac53605 only if D1 had copied usage/OTel rows but no D1 dimension hooks. A session with real D1 prompt/tool hooks keeps `started_at` on D1 and is not admitted through arm 1. Regression tests need both fixtures. |
| 2.1d | Admission reads time bounds (`last_event_at`/`ended_at`) | WRONG | R:270-301 | Admission never reads `last_event_at`, `ended_at` or `activity_started_at`. It uses `started_at`, the stored facts, and the 5 day-row tables. |
| 2.1e | "Activity" in `_active_in_window` | CONFIRMED (detail) | R:180-194, R:200-229, R:232-245 | Rows in turns_daily, tool_facts_daily, cost_daily, session_usage, lines_daily on a whole in-window UTC day. On a partial day it also needs a raw record: activity hooks, any usage_request, log_events with kind<>OTHER, spans with kind<>OTHER, lines metrics. The six startup records are OTHER and do not count. |
| 2.1f | Extra: a `user_prompt` OTel record (the `/exit` one) is NOT activity | WRONG (nuance) | R:212-218; V:61-68 | `user_prompt` is kind 2, not OTHER, so it counts as raw in-window activity. It admits a session through arms 4/5 for a partial-day window. It creates no day rows (`turns_daily` comes from spans and the prompt.submit hook, RU:1887-1898), so whole-day admission is unaffected. Whether the CLI sends it is a CLI question (agent notes "CLI <= 0.16.6"). |
| 2.2a | `last_event_at` is set from the dimension hooks, including session.start/stop/end | CONFIRMED | RU:2072-2073, 2087-2088; V:78-94 | Lifecycle hooks move `last_event_at`. This feeds users "last active" (R:954-979) and the reported end. |
| 2.2b | Native Claude Code OTel records (hook_registered, plugin_loaded, managed_settings_resolved, mcp_server_connection, hook_execution_*, user_prompt) move `started_at`/`last_event_at` | WRONG for those columns | RU:2072-2073 (hook_events only); otlp.py:682-720 | They cannot move either column. They go to `log_events` (OTHER, except `user_prompt`) and nothing is filtered at ingest. |
| 2.2c | The same records move the displayed End/Duration | CONFIRMED (path is the probes) | R:559-575; RU:1104-1109, 1189, 1285-1290 | Two routes. (1) `_activity_range` probes `log_events`/`spans` within 1 day after the stored end, no kind filter. (2) `fb.last_ts`/`fb.first_ts` span ALL raw tables when there is no summary or a provisional one. A final summary is unaffected. A record more than 1 day past the end is not reached by the probe, so a 6-day duration comes through `last_event_at` via session.start/stop/end hooks. |
| 2.2d | A housekeeping filter was already added (EPMCDME-16847) | NOT FOUND | none | No commit, no code, no test. `git log --all --grep=16847` is empty. The nearest rule is the lifecycle-hook exclusion in `_ACTIVITY_HOOK_TYPES` (R:200-218), which is admission only and does not apply to duration or `last_event_at`. |
| 2.2e | `_activity_range` end = `greatest(last_event_at, ended_at)` | PARTIAL | R:559, R:575 | Incomplete: it also takes the max `ts` of `log_events`/`spans` within +1 day. Start likewise includes the min `ts` within -1 day (R:558, R:575). The handoff omits this. |
| 2.3a | KPI tiles (`get_cost_kpis`) sum all `cost_daily` rows of admitted sessions with no day filter | CONFIRMED | R:817-830; `_cost_rows` R:410-416 | Scoped to `sel` only. Same for `get_session_model_costs` R:851-859 and `get_lines_daily` R:994-1006. By design for ANY multi-day session, not only reopens. Fixing it needs day filters in the fact queries, a separate change from admission. |
| 2.3b | Fixing admission alone is enough | WRONG | R:270-301 vs R:817-1006 | Even with a correct admission, a genuinely resumed session shows its D1 cost in a D2 window. The `get_lines_by_session` docstring (R:1022-1025) says this is intentional. A product decision is needed on whether the tiles are in-window amounts. |
| 2.3c | Overview and other endpoints agree for the same period | WRONG | see section 8 | Tokens differ (cost_daily only vs plus `session_usage` totals), money excludes personal-billed but tokens do not, and `/frameworks` uses raw `started_at`. |
| 2.4 | Session class / project origin binding: a later open rebinds | PARTIAL (previous research folded in; 4 references re-verified) | `session_binding.py:86-93, 187`; `cli_analytics_ingest_route.py:61-70, 197-209`; `cli_analytics_project_gate.py:433-503`; `ingestor.py:505, 567-584` | Binding is first-write-wins (`ON CONFLICT ... WHERE sd.owner_email IS NULL`), so a bound session is never rebound. Stored classes are only codemie/corporate/personal/unknown (`scope.py:158`); `ai-run-sso` is not a class. The D2 native open was the first evidence request and bound the session as `unknown`, via `project_origin=legacy_project` when a legacy project name exists. D1's traffic was legacy and did not bind. No event-type filter exists. Any request with a session id and `record_count > 0` binds, even lifecycle or housekeeping only, and binding runs before ledger dedup. Verified OK: the bind SQL (:86-92) and `_TOUCH_BOUND_SQL` (:93), `EVIDENCE_HEADERS` and the legacy-gate branch (:61-70, :197-199), `resolve_session` legacy branch (:460-478), `ingestor.py` `bind` at :582, ledger at :584, `if not batch.record_count` at :505. No reference had shifted. |
| 2.5a | Displayed Start = `least(started_at, activity_started_at)` | PARTIAL | R:558, R:575 | Also includes the earliest `log_events`/`spans` row within 1 day before. Used by list and detail alike. |
| 2.5b | `activity_started_at` comes from the summary event | PARTIAL | RU:1290, RU:1104-1109, RU:1397-1416 | The summary's `attrs.started_at` if present. Otherwise `fb.first_ts` = min over ALL raw rows (hooks of all types, usage, logs, spans, metrics). So hook, OTel and usage timestamps can all set it. |
| 2.5c | A fixed CLI that sends no usage or summary cannot show the parent's start | PARTIAL | RU:2087, RU:1399, RU:1397-1416 | The backend never copies a parent's timestamp (no parent/conversation link anywhere in the `cli_analytics` package). The value must come from rows stored under the child's own session id. Source cannot settle which of three paths happened (see section 6). Importantly, `started_at` and the fallback `activity_started_at` only move earlier, so a session already stored with the parent's first line cannot be fixed by a later CLI. |
| 2.5d | Detail vs aggregate tool-call/agent counts differ | CONFIRMED (cause identified) | R:1664-1728 (detail), R:1090-1118, R:1130-1136 (aggregate) | See section 6. The detail takes `greatest(summary, observed)`. The aggregate uses observed hook/OTel rollups only. |
| T-lastactive | Users "last active" is moved by session lifecycle hooks | CONFIRMED | R:954-979; RU:2073 | `session_dims.last_event_at` is used when `<= $end_dt`, with no lower bound. Otherwise it falls back to `max(h.ts)` of dimension hooks in the window. OTel and usage never count. |

---

## 6. EPMCDME-16992 - Start of a continuation with zero own usage

**How Start is computed (HEAD):** R:558 `least(started_at, activity_started_at)`, then R:575 `least(start, min(log_events.ts, spans.ts) within 1 day before)`. List (`_activity_cte` R:746-754) and detail (R:1578, 1611-1612) use the same expression. Handler start time is H:1119 for detail and H:868-881 for the list.

**What sets each input:**
- `started_at`: dimension hooks only (min `ts`, RU:2072). A leftover value can only move earlier (RU:2087).
- `activity_started_at`: the summary's `attrs.started_at`, else min `ts` over ALL raw rows (RU:1290, 1104-1109).
- The only code that can put a parent's time on a child is data carried in the child's own rows. No backend code links a session to a parent (grep for conversation_id/parent_session/continuation in the package found only unrelated hits).

**Candidate paths for Started 16:10:48.156 vs own first line 16:23:52 (comment 16079089). Which one occurred cannot be determined from source alone.**
- (a) A summary whose `attrs.started_at` is the parent's timestamp goes straight into `activity_started_at` via RU:1290 and wins `least` at R:558. The 808,718 ms duration (about 13.5 min) matches 16:10:48 to roughly 16:24. This is the weakest link. The handoff says the fixed CLI sends no summary for work-less continuations. If it still did, or the QA session pre-dated that rule, this is the cause.
- (b) Stale state. A QA-built continuation that was ingested earlier with the old CLI already had `started_at`/`activity_started_at` stored with the copied timestamp. Later fixed-CLI data cannot raise either (RU:2087 `LEAST`, RU:1399 `least`). Only a newer winning summary could replace `activity_started_at` (RU:1397-1416). The Jira comment says QA continuations were reused, so reuse of the same session id is plausible. This explains the result by itself. The handoff does not mention this path, and it means the backend alone cannot repair existing rows.
- (c) A raw row (hook with a client-supplied timestamp copied from the parent, an OTel log, a usage row) stored under the child's id feeds `fb.first_ts` (RU:1189) when there is no summary. AC1 reporting cost 0 and tokens 0 argues against copied usage rows, so this is less likely for usage but still possible for hook rows.

**Data that would settle it (run on the Preview DB for the child session id C):**
- `SELECT started_at, activity_started_at, summary_ts, last_event_at, ended_at FROM session_dims WHERE session_id = C`. A `started_at` near 16:10:48.156 means a dimension hook carried it. Only `activity_started_at` near it means a summary or the raw fallback.
- `SELECT ts, attrs->>'started_at' FROM hook_events WHERE session_id = C AND event_type = 'agent.session.summary'`.
- min `ts` and count per raw table for C: `hook_events` (by `event_type`), `usage_requests`, `log_events`, `spans`, `metric_points`. Also the ingest time/`created_at` of those rows, to see whether they pre-date the fix.

**Detail vs aggregate tool-call and agent mismatch (the "111 vs 0" case):**
- Aggregate side reads only the observed rollups:
  - tools: `get_tool_usage` (R:1109-1118) and `get_tool_success_by_session` (R:1090-1107) use `invocations_hourly kind = 1`; the handler sums `r["tool_calls"]` (H:416, 544, 561, 716);
  - agents: `get_invocations` (R:1130-1136) uses `invocations_hourly kind IN (2, 3)`.
- Detail side (`get_session_detail_scalars`, R:1664-1728):
  - `tool_call_count` is the same observed sum (R:1680-1681);
  - `tool_calls_total = greatest(summary tool_calls, observed)` (R:1685-1687). The handler exposes both at H:1124-1125. The "111" is this summary-merged value;
  - `agent_count = greatest(nullif(tool_facts_daily.agent_count + hook correction, 0), summary agents total)` (R:1688-1715), where `tool_facts_daily.agent_count` comes from spans with `subagent_type` (RU:1909-1914), the correction adds raw `agent.subagent.start` hooks not yet rolled up (R:1697-1712), and the summary map total is `_SUMMARY_MAP_TOTAL.format(c="agents")` (R:1714). The aggregate uses `invocations_hourly` kind 3 only.
- A summary built from a continuation's copied transcript (old CLI) keeps 111 tool calls / 2 agents in `session_dims` (RU:1304, 1310). Detail shows them via `greatest`, while the rollup-based aggregates show 0. Nothing reconciles the two.
- Detail is also unbounded in time and is not restricted to `sel`.

---

## 7. Risk Indicators

- **Fix needs changes in more than one place.** Admission arm 1 (R:294) and arm 2 (R:295) must both change, because the stored `has_activity` fast path would otherwise keep the old unbounded behavior. A migration or backfill of the stored facts may be needed. The reference constant R:259 and the docstrings should be kept consistent.
- **Admission alone does not fix the KPI tiles** (claim 2.3). Day filters on `get_cost_kpis`, `get_session_model_costs`, `get_lines_*`, `get_users*`, `get_cost_by_user`, `invocations_hourly` and `active_time_daily` queries would be a wide contract change. A product decision is needed on in-window versus whole-session amounts for multi-day sessions. The existing test `test_reader.py:648` pins the current "no day window" behavior.
- **Display range vs stored time columns.** `_activity_range` probes `log_events`/`spans` with no `event_kind`/`span_kind` filter (R:560-575), and `fb.first_ts`/`fb.last_ts` span all raw tables (RU:1104-1109). The lifecycle hooks `session.start/stop/end` also move `last_event_at` (V:80-94). A real fix needs a decision on which event types count, and a single shared list so reader and rollups do not drift. The existing reader list `_ACTIVITY_HOOK_TYPES` (R:200-209) is a candidate.
- **Monotonic merges make existing rows unrepairable by a corrected ingest.** `started_at` (`LEAST`, RU:2087) and fallback `activity_started_at` (`least`, RU:1399) never move later. Already-stored polluted rows (D2 `session.start`, parent timestamps, housekeeping-extended ends) need a backfill/recompute, or a reader-side clamp. This affects how a fix is verified on Preview: an existing session will not heal on its own.
- **EPMCDME-16847 housekeeping filter does not exist in this repo.** The caller expected it might. Plan to build it, not to extend it. Confirm with the CLI side whether a separate branch holds it.
- **The `/exit` `user_prompt` log record** (kind 2) counts as raw in-window activity (R:212-218) and can admit a session for a partial-day window through arms 4/5. It is a specific case from the ticket comments to cover. Whether it also creates a `turns_daily` row via an `agent.prompt.submit` hook was not verified.
- **Binding trap.** Lifecycle-only or housekeeping-only evidence requests bind a session as `unknown` and `legacy_project`. Binding is first-write-wins (`session_binding.py:86-92`), so a wrong bind (the D2 open) is permanent. No test covers this. Changing it touches the security- and billing-relevant funding route, so it needs care (EPMCDME-16849 consent/funding).
- **Cross-endpoint inconsistencies (section 8).** Fixing one endpoint without the others will leave the numbers disagreeing.
- **Live test gap.** The tests that would guard an admission or duration fix must be live PG tests. The infrastructure exists (`TEST_LOCAL_PG_URL`, `-n0`, `live_rollup_lock`) but there is no marker, no Makefile target and no docs. The history lists live admission test files (`test_live_session_activity_window.py` etc.) that are not on disk at HEAD.
- **Version drift.** Every line reference in the handoff and ticket is stale (the handoff cites 214-222, 257, 268 for what is now R:248-256, R:259, R:270). The code moved in 16427 (2026-11-09) and 16633 (2026-11-10). Re-verify line numbers before use.
- **Sparse requirement visibility.** The ticket and handoff text were read only by the research agents. The orchestrator did not read the originals. Acceptance criteria, expected results and the comment-specific cases that this task must cover need to be extracted from `work-item.md` by the next stage. Only the cases that surfaced in the agent reports (comments 16079046, 16079089; AC1 cost/tokens 0; Duration 808,718 ms; 8ac53605; "Date Nov 01 in Last 7 Days"; the 6-day duration; "Dead sessions"; $7.46) are reflected here.
- **No usable pre-existing admission tests.** Existing tests would not notice a semantic regression in the arms.

### Cross-endpoint disagreement table (same period)

| method | route | filter basis | file:line |
|---|---|---|---|
| `_sessions_cte`/`sel` | all aggregate routes | PERIOD, applied only as admission | R:691-743 |
| `get_cost_kpis` | `/overview` RT:525, `/cost` RT:544, `/efficiency` RT:696 | admitted sessions, all-time `cost_daily`, no day filter | R:807-830 |
| `get_session_model_costs` | `/overview`, `/cost`, `/users`, `/user-charts`, `/repositories`, `/efficiency`, `/sessions` rows | admitted sessions, all-time `cost_daily` plus `session_usage` subagent totals | R:851-859; `cost_key_sql.py:70-115` |
| `get_lines_daily` | `/overview` only (H:517) | admitted sessions, all days of those sessions (D1 bars show in a D2 window; `data_as_of` also derives from it, H:591) | R:994-1006 |
| `get_lines_totals`, `get_lines_by_session`, `get_lines_by_user` | `/overview`, `/efficiency`, `/sessions`, `/repositories`, `/users` | admitted sessions, all-time | R:983-1034 |
| `get_model_breakdown`, `get_cost_by_user`, `get_session_funding_costs` | `/overview`, `/tools`, `/cost`, `/users`, `/repositories` | admitted sessions, all-time | R:832-909 |
| `get_users`, `get_users_daily_activity` | `/users`, `/user-charts` | admitted sessions. Day buckets come from all 5 tables on ALL days, so a D1 bucket appears in a D2 window | R:911-952 |
| `get_users_last_active` | `/users`, `/user-charts` | admitted sessions. `last_event_at` with no lower bound | R:954-979 |
| `get_session_cost_facts` | sessions list, `/repositories`, `/efficiency` | admitted sessions, all-time. Start/end from `_activity_range` | R:1192-1237 |
| `get_turns_by_session`, `get_file_facts_by_session`, `get_tool_success_by_session`, `get_tool_usage`, `get_invocations` | `/overview`, `/users`, `/repositories`, `/sessions`, `/efficiency`, `/tools` | admitted sessions, all-time | R:1038, 1056, 1090, 1109, 1122 |
| `get_session_durations` | `/overview` (H:521), `/users` (H:694) | admitted sessions, all-time activity range | R:1172-1179 |
| `get_session_start_times` | `/activity` (H:1228) | admitted sessions. The heatmap bucket is the activity-range START, which can lie outside the window (the ticket's Sunday D1 17:00) | R:1512-1518 |
| `get_framework_sessions` | `/frameworks` RT:715 | PERIOD directly: `started_at BETWEEN`, with no activity check and no `sel` | R:1534-1565 |
| `get_session_detail_*` | `/sessions/{trace_id}` RT:778 | NEITHER. By session id, all-time. `active_ms` all-time (R:1668-1672) | R:1575-1830 |
| `get_repository_sessions` | no caller found in the handler | admitted sessions | R:1520-1532 |

Known disagreements for the same period:
- Token totals differ by source. `get_cost_kpis`, `get_users` and `get_cost_by_user` read `cost_daily` only. The list, `/repositories` and `/efficiency` use `get_session_cost_facts(include_costless=True)`, which adds `session_usage` subagent totals (R:419-453). Overview and users token totals can be lower than the list sum.
- Money (`spend_only=True`, `cost_key_sql.py:61-64`) excludes personal-billed sessions. The token sums in `get_cost_kpis`/`get_users` do not. The list per-row cost is not spend-filtered (H:961). The list cost sum can exceed the overview total.
- `/frameworks` counts a prompt-less session started in the window, but every other route excludes it.
- Detail resolves sessions that have cost rows but no `session_dims.started_at` (R:1582-1588). No aggregate lists them.
- A session can be listed in a window yet show a Start outside it (window membership uses raw `started_at` at R:294, display uses `least(...)` at R:558). This is the ticket's "Date Nov 01 in Last 7 Days".

---

## 8. Summary for Complexity Assessment

**Layers and file surface.** The work touches the Reader (`reader.py`: arms 1/2 at R:294-295, `_activity_range` R:549-575, fact queries R:800-1230, and possibly the detail path R:1575-1830). It also touches Rollups (`rollups.py`: `session_dims` time columns RU:1104-1109, 1285-1290, 2069-2092) and Ingest/Binding (`session_binding.py`, `ingestor.py`, project gate). Handler changes are likely if tile semantics or the tool/agent merge change (`cli_analytics_handler.py`). A DB migration plus backfill is likely, because stored facts (`has_activity`, `last_activity_day`) and monotonic time columns hold polluted data. Expect roughly 6-10 source files plus 1 migration, the two integration guides, and many test files. The most invasive item is bounding the tile and fact queries by day (claim 2.3). Treat it as a separate, optional slice that needs a product decision.

**Novelty and risk.** No new architectural patterns are needed: the code follows the existing SQL-string-constant and arm-union conventions, and the tests follow the existing live-PG pattern. However, the SQL is performance-sensitive (EPMCDME-16427 added arms, fences and stored facts to make admission index-served). Any change to admission must preserve the planner behavior and the documented retention divergence. The central risks are semantic. Four independent time sources (hook dimension, summary, raw fallback, reader probes) must agree on what "activity" means. Existing polluted rows cannot be healed by the monotonic merges. EPMCDME-16847 was assumed to exist but is absent. The root cause of the 16992 Start symptom cannot be settled from source: it needs a DB query on the Preview session (three candidate paths). Some handoff claims are wrong or imprecise (2.1c, 2.1d, 2.2b, 2.3b), so requirements should be taken from the Jira comments, not the handoff.

**Test coverage posture.** Mixed and weak where it matters. The reader and rollup SQL has heavy SQL-text assertions and fake-connection tests, but no real-database test of admission, session times or housekeeping at HEAD. One test (`test_reader.py:648`) explicitly pins the current "no day filter" tile behavior and will need an intentional change. Live PG tests exist only for route pricing and subagent cost, run manually with `TEST_LOCAL_PG_URL` and `-n0`, with no marker or make target. Realistic verification of this work needs new live tests with at least two fixtures: a reopen session with real D1 dimension hooks, and one whose first dimension hook is on D2. It also needs a lifecycle/housekeeping-only continuation, the `/exit` `user_prompt` case, and a multi-day genuinely resumed session. Suggested complexity: high. Many integration points, a data-migration concern and cross-endpoint consistency, though each individual change is small.
