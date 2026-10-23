# EPMCDME-15506 Session Activity Range Implementation Plan

> **For agentic workers:** sdlc-standard runs this plan inline with TDD in the current session (no subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every reported CLI analytics session start is ≤ the session's earliest event, the end ≥ its latest, and `duration_ms` is that range, without changing window membership.

**Architecture:** A reader-side activity range (`act` CTE over `sel` for windowed queries, the same range for the single detail session, open-ended without a `session_dims` row) built from `session_dims` columns plus ts-bounded `log_events` / `spans` probes. `get_session_durations`, `get_session_cost_facts`, `get_session_start_times` and `get_session_detail_meta` report from it; the Sessions list handler takes `duration_ms` from the row.

**Tech Stack:** Python 3.12, asyncpg, PostgreSQL (partitioned raw tables), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/tasks/2026-10-16-epmcdme-15506-session-start-before-first-activity/spec.md`

## Global Constraints

- Window membership stays `started_at BETWEEN $start_dt AND $end_dt`, only inside `sel` (`_sessions_cte`); `sel`'s WHERE is untouched.
- Probe bounds: earliest in `[cand_start − interval '1 day', cand_start)`, latest in `(cand_end, cand_end + interval '1 day']`; `cand_start = least(started_at, activity_started_at)`, `cand_end = greatest(last_event_at, ended_at)`.
- Probes read `log_events` and `spans` only, as correlated subqueries (they pass the `sel` scope audit in `test_reader.py`).
- Durations in SQL use `_ms(end) - _ms(start)` (floor ms), same as today.
- Response models unchanged: `start_time: str`, `duration_ms: int`.
- Commits: `EPMCDME-15506: <Capital sentence>` with the repository's attribution trailer.
- Row keys the handler reads stay `started_at`, `last_event_at`, `duration_ms`.

## Review Focus

1. A session whose `activity_started_at` / `ended_at` are NULL (no summary, no recompute yet): `least`/`greatest` must skip NULLs and keep the hook times. → Task 1 live test case.
2. A session with no OTel row at all: the probes return NULL; the range equals the column bounds. → Task 1 live test case.
3. An event exactly at `cand_start`: not "earlier", must not shift anything (half-open bound). → covered by the bound shape assertion in Task 1.
4. Detail of a session with OTel rows and no `session_dims` row: start is the earliest OTel row, not 1970. → Task 2 live test case.
5. Users Avg Session Duration over a corrected session uses the corrected duration under the priced-sessions rule. → Task 1 live test also asserts `LocalAnalyticsHandler(reader).get_users(f, None, None)["avg_session_duration_ms"] == 672_000`.

---

### Task 1: Activity range for the windowed session queries

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py` (`_sessions_cte` select list, new `_activity_cte`, `get_session_durations`, `get_session_cost_facts`, `get_session_start_times`)
- Create: `tests/codemie/repository/cli_analytics/postgres/test_reader_live_session_range.py`
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Produces: `_activity_range(alias: str, bounded: bool) -> tuple[str, str]` (module function in `reader.py`) returning the SQL expressions `(session_start, session_end)` for a row alias exposing `session_id, started_at, activity_started_at, last_event_at, ended_at`.
- Produces: `PostgresCliAnalyticsReader._activity_cte() -> str`, the CTE text `act AS (SELECT session_id, session_start, session_end, duration_ms FROM sel …)`, placed after `sel` in `WITH`.
- `sel` additionally exposes `d.activity_started_at` and `d.ended_at`.
- `get_session_cost_facts` rows: `started_at` = `max(a.session_start)`, `last_event_at` = `max(a.session_end)`, new `duration_ms` = `max(a.duration_ms)`.
- `get_session_start_times` rows: `started_at` = `a.session_start`.
- `get_session_durations` rows: `session_id`, `duration_ms` from `act`.

- [ ] **Step 1: Write the failing live test** `test_reader_live_session_range.py`, same skip harness as `test_reader_live_dispatches.py` (`_get_engine`, cleanup by session id from `session_dims`, `cost_daily`, `hook_events`, `log_events`, `spans`). Seed with `T = now(UTC)` truncated to seconds minus 1 hour:
  - `session_dims` row: `started_at = T`, `last_event_at = T + 10 min`, `activity_started_at` / `ended_at` NULL, `jwt_email = 'range@example.com'`.
  - `cost_daily` row for `T`'s day, `cost_usd 1.0`, `api_call_count 1`.
  - `log_events` row `event_kind 1` at `T − 12 s`; `spans` row `span_kind 1`, `tool_name 'Read'` at `T + 11 min`.
  - `test_reported_range_covers_events_outside_the_hook_times`: with `f = LocalAnalyticsFilter(T − 1 h, T + 1 h)`, `get_session_durations(f)` → `duration_ms == 672_000`; `get_session_cost_facts(f)` row → `started_at == T − 12 s`, `last_event_at == T + 11 min`, `duration_ms == 672_000`; `get_session_start_times(f)` → `[T − 12 s]`.
  - `test_window_membership_still_follows_started_at`: window `[T − 6 s, T + 1 h]` contains the session; window `[T − 1 h, T − 1 s]` does not (`get_session_durations` empty).
  - `test_range_without_otel_rows_is_the_hook_times`: a second session with only `session_dims` (`T`, `T + 10 min`) → `duration_ms == 600_000`, start `T`.

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader_live_session_range.py -v -p no:xdist`
Expected: FAIL on `duration_ms == 672_000` (actual `600_000`) and start `T`. If it reports SKIPPED, the local analytics Postgres is down; start it before continuing.

- [ ] **Step 3: Write the failing SQL-shape tests** in `test_reader.py`:
  - `test_session_queries_report_the_activity_range` (parametrized over `get_session_durations`, `get_session_cost_facts`, `get_session_start_times`): SQL contains `act AS (`, `least(`, `greatest(`, `FROM log_events`, `FROM spans`, and `sql.count("BETWEEN") == 1`.
  - `test_activity_probes_are_bounded_by_a_day_around_the_hook_times`: SQL of `get_session_durations` contains `- interval '1 day'` and `+ interval '1 day'`, a `ts <` bound for the earliest probe and a `ts >` bound for the latest probe.

- [ ] **Step 4: Run them to verify they fail**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k "activity" -v`
Expected: FAIL (no `act AS (` in the SQL).

- [ ] **Step 5: Implement** `_activity_range`, `_activity_cte`, the two extra `sel` columns, and switch the three queries to `act` (`get_session_cost_facts` joins `act a ON a.session_id = c.session_id`). `get_session_durations` / `get_session_start_times` select `FROM act`.

- [ ] **Step 6: Run the live and shape tests, plus the whole reader suite**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py tests/codemie/repository/cli_analytics/postgres/test_reader_live_session_range.py -v -p no:xdist`
Expected: PASS, including `test_every_fact_a_windowed_query_reads_is_scoped_to_the_selected_sessions`, `test_session_start_times_rely_on_sel_alone`, `test_session_durations_have_no_overlap_check`.

- [ ] **Step 7: Check run-time partition pruning** on local PG: `EXPLAIN (ANALYZE, COSTS OFF)` of the `get_session_durations` SQL shows the probe partitions outside the bound as `(never executed)` or removed. Record the observation in the commit body.

- [ ] **Step 8: Commit**

```bash
git add src/codemie/repository/cli_analytics/postgres/reader.py tests/codemie/repository/cli_analytics/postgres/test_reader.py tests/codemie/repository/cli_analytics/postgres/test_reader_live_session_range.py
git commit -m "EPMCDME-15506: Report session start and duration from the activity range"
```

Test-first: yes — `test_reported_range_covers_events_outside_the_hook_times` fails with 600000 ≠ 672000.

### Task 2: Activity range for Session Detail

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py` (`get_session_detail_meta`)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader_live_session_range.py`, `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Consumes: `_activity_range(alias, bounded=False)` from Task 1.
- Produces: `get_session_detail_meta` rows keep `started_at` (now the range start, `coalesce(..., epoch)`) and `duration_ms` (`coalesce(_ms(end) - _ms(start), 0)`).

- [ ] **Step 1: Write the failing tests**
  - Live: `test_session_detail_reports_the_activity_range`: for the Task 1 seed, `LocalAnalyticsHandler(reader).get_session_detail(sid)` → `start_time` is the ISO of `T − 12 s`, `duration_ms == 672_000`, and the first event's timestamp ≥ `start_time`.
  - Live: `test_session_detail_without_dims_starts_at_its_earliest_event`: `cost_daily` + `log_events` at `T` and `T + 30 s`, no `session_dims` → `start_time` is `T`, `duration_ms == 30_000`.
  - Shape: `test_session_detail_meta_reads_the_same_activity_range_as_the_list`: SQL contains the four day-bounded probes and the `'-infinity'` / `'infinity'` fallbacks; the existing service-name tests still pass. (Code review CR-001.)

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader_live_session_range.py tests/codemie/repository/cli_analytics/postgres/test_reader.py -k "detail" -v -p no:xdist`
Expected: FAIL (`duration_ms` 600000; 1970 start without dims).

- [ ] **Step 3: Implement** the detail meta range: the outer row's alias exposes the five columns (`c.session_id` with `d.*` from the LEFT JOIN), expressions from `_activity_range(..., bounded=False)`.

- [ ] **Step 4: Run to verify they pass**, plus `tests/codemie/rest_api/routers/test_cli_analytics_storage_wiring.py`.

- [ ] **Step 5: Commit**: `EPMCDME-15506: Report the session detail start and duration from the activity range`

Test-first: yes — `test_session_detail_reports_the_activity_range` fails with 600000 ≠ 672000.

### Task 3: Sessions list takes the reader's duration

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py` (`get_sessions` loop, ~lines 621-625)
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (`BASE_COST_ROW` gains `"duration_ms": 60_000`)

**Interfaces:**
- Consumes: `get_session_cost_facts` rows with `duration_ms` (Task 1).

- [ ] **Step 1: Write the failing tests**
  - `test_get_sessions_reports_the_reader_duration`: row with `started_at` 00:00:00, `last_event_at` 00:01:00, `duration_ms` 72_000 → session `duration_ms == 72_000`.
  - `test_get_sessions_with_a_missing_reader_duration_reports_zero`: row without `duration_ms` → `duration_ms == 0`.

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k "reader_duration" -v`
Expected: FAIL (60000 from the timedelta).

- [ ] **Step 3: Implement**: `duration_ms = max(0, _i(row.get("duration_ms")))`; drop the timedelta computation and the then-unused `last_event` local.

- [ ] **Step 4: Run the handler suites to verify they pass**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/ -v`
Expected: PASS (existing tests keep 60000 through `BASE_COST_ROW`).

- [ ] **Step 5: Commit**: `EPMCDME-15506: Take the sessions list duration from the reader`

Test-first: yes — `test_get_sessions_reports_the_reader_duration` fails with 60000 ≠ 72000.

### Task 4: Storage guide

**Files:**
- Modify: `.ai-run/guides/integration/cli-analytics-storage.md` (Window Semantics paragraph ~:52, Notes for a reader ~:222)

- [ ] **Step 1:** Window Semantics: membership is still decided by the stored `started_at`; the reported session start / end / duration are the activity range.
- [ ] **Step 2:** Notes for a reader, "Two pairs of times": add the reported range rule (the `cand_start` / `cand_end` formula, `log_events` / `spans` probes within one day on every path, open-ended without a `session_dims` row) and that Sessions list, Session Detail, `get_session_durations` (Users Avg, Overview) and the activity heatmap report it.
- [ ] **Step 3:** Commit: `EPMCDME-15506: Document the reported session activity range`

Test-first: no — documentation only.
