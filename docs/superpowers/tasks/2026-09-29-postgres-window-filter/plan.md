# Session-Start Window Filter (PostgreSQL Reader) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** A session is in scope iff `session_dims.started_at` is in `[start_dt, end_dt]`; every windowed `PostgresCliAnalyticsReader` method then includes all of that session's data.

**Architecture:** Put the predicate once in `_sessions_cte` (`sel`). Fact queries drop their `_DAYS`/`_TS`/`_EDGES`/`_INNER_HOURS` predicates and always scope or inner-join to `sel`. Then remove the dead helpers.

**Tech Stack:** Python, asyncpg-style `$name` SQL via `positional()`, pytest with the SQL-recording fake engine.

**Spec:** `C:/Projects/codemie-dev/codemie/docs/superpowers/tasks/2026-09-29-postgres-window-filter/spec.md`

Commit per task using the repository's existing convention (`EPMCDME-####: Description`).

## Global Constraints

- Reader file: `src/codemie/repository/cli_analytics/postgres/reader.py`. Tests: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`.
- Bounds are inclusive and truncated to ms via `window_params`.
- Row shapes and Python types stay stable, and the reader must satisfy `CliAnalyticsReader` (`assert_implements_port`).
- `get_session_detail_*`, `get_skill_names_by_session` and other explicit `session_id` methods are unchanged.
- Do not touch ClickHouse, the router, the handler, retention, or `LocalAnalyticsFilter` handling of users/projects/repositories/branch.
- Negative constraints from the spec are honored as follows. No fact-side day, ts or hour predicate remains (T2-T4). No overlap check remains (T1). No backfill of dimension-less sessions (T2 excludes them). No API or handler change (no task touches them).

## Review Focus

- A window with no session starts returns empty rows and does not error (T1 test).
- `start_dt` equal to `started_at` and `end_dt` equal to `started_at` are both included (T1 test, inclusive `BETWEEN`).
- A session that started before `start_dt` but has facts inside the window yields nothing, so no fact query may re-add a day predicate (T5 audit).
- An unfiltered request (`has_session_filter` False) must still be scoped to the window. `_scope` must therefore always apply (T1).
- Dimension-less sessions (no `started_at`) must vanish from cost queries and no `_EPOCH` defaults may remain (T2).

---

### Task 1: Shared predicate in `sel` and session-level methods

**Files:**
- Modify: `reader.py:149-190` (`_sessions_cte`, `_scope`), `:271-298` (`get_users_last_active`), `:485-493` (`get_session_durations`), `:544-565` (`get_session_start_times`, `get_repository_sessions`)
- Test: `test_reader.py`

**Interfaces:**
- Produces: module constant `_STARTED_IN_WINDOW = "started_at BETWEEN $start_dt::timestamptz AND $end_dt::timestamptz"`. `_sessions_cte(f)` always includes it in the `x` subquery WHERE, combined with the existing filter conditions. `_scope(f, column)` ALWAYS returns `AND {column} IN (SELECT session_id FROM sel)`, no longer gated on `has_session_filter`.

Test-first: yes — a recorded-SQL test asserts that `_sessions_cte` for an unfiltered and a `deny_all` filter contains `started_at BETWEEN $start_dt`. Further tests assert that `get_session_durations` SQL has no `last_event_at >=`, that `get_repository_sessions` SQL reads from `sel` with the window, that `_scope(unfiltered)` is non-empty, and that a window with no rows returns `[]`. `params` truncates `start_dt`/`end_dt` to ms.

- [ ] Write the failing tests above and confirm they fail.
- [ ] Add the constant and apply it in `_sessions_cte` (join with `AND` to the existing `d.started_at IS NOT NULL`). Make `_scope` unconditional.
- [ ] `get_session_durations`: drop the overlap WHERE (`sel` already windows it). `get_session_start_times`: drop the redundant BETWEEN. `get_repository_sessions`: no change beyond `sel`.
- [ ] `get_users_last_active`: remove the `WHERE d.last_event_at >= ... AND d.started_at <= ...` overlap on `w`. Keep the `CASE` last-event logic. Keep the raw `hook_events` `_TS` clip there, because it decides the max timestamp within a session and not session membership. Judgment call: keep the `_TS` constant for this use only.
- [ ] Run `poetry run pytest tests/codemie/repository/cli_analytics/postgres -q` (expect the old `has_session_filter`-dependent audit tests to fail until T5; note which).

### Task 2: cost_daily methods, inner joined to `sel`

**Files:**
- Modify: `reader.py:194-269` (`get_cost_kpis`, `get_model_breakdown`, `get_cost_by_user`, `get_users`, `get_users_daily_activity`) and `:506-542` (`get_session_cost_facts`)
- Test: `test_reader.py`

Test-first: yes — for each method the recorded SQL has no `day BETWEEN`, contains `JOIN sel` (inner) or `IN (SELECT session_id FROM sel)`, and `get_session_cost_facts` SQL contains neither `1970-01-01` nor `LEFT JOIN sel`.

- [ ] Write the failing tests.
- [ ] Remove `{_DAYS}` from each WHERE. Where the query uses `LEFT JOIN sel`, change it to `JOIN sel`. `_scope` stays for the sel-less queries (`get_cost_kpis`, `get_model_breakdown`).
- [ ] `get_session_cost_facts`: drop the `_EPOCH` coalesces (plain `max(s.started_at)`, `max(s.last_event_at)`). The `is_unattributed` HAVING clause keeps working, since it filters on repository. `get_users_daily_activity` keeps grouping by `c.day` (the fact's own day) with no filter on it.
- [ ] Run the file's tests.

### Task 3: lines, turns, files and active-time methods

**Files:**
- Modify: `reader.py:302-392` (`get_lines_totals/daily/by_user/by_session`, `get_turns_by_session`, `get_file_facts_by_session`) and `:495-504` (`get_active_ms_by_session`)
- Test: `test_reader.py`

Test-first: yes — for each method the recorded SQL contains no `day BETWEEN`, still scopes to `sel`, and `get_lines_by_user` uses an inner `JOIN sel`. For `get_file_facts_by_session`, both `tool_facts_daily` and `session_files_daily` branches are scoped and unwindowed.

- [ ] Write the failing tests.
- [ ] Drop `{_DAYS}` (rewrite as `WHERE true` or a bare scope `WHERE session_id IN (SELECT session_id FROM sel)`; do not leave a dangling `AND`). Change `get_lines_by_user` to an inner join. Update the `get_file_facts_by_session` docstring ("across the window's days" becomes "across the session's days").
- [ ] Run the file's tests.

### Task 4: invocation and tool methods without hourly/edge windows

**Files:**
- Modify: `reader.py:394-468` (`_edge_tool_calls`, `_hourly_params`, `get_tool_success_by_session`, `get_tool_usage`, `get_invocations`)
- Test: `test_reader.py`

Test-first: yes — recorded SQL for the three methods contains no `hour >=`, `$h0`, `ts BETWEEN` or `FROM spans` and `log_events` edge branches. It reads `invocations_hourly` scoped to `sel` with no time predicate.

- [ ] Write the failing tests.
- [ ] Rewrite the three methods as a single `invocations_hourly` query each, scoped via `_scope`. Keep the existing `kind` filters, `session_id <> ''`, grouping, ordering and `LIMIT 20`. Delete `_edge_tool_calls`. Whole-session inclusion makes the hour rollups sufficient, and the raw edges only existed for partial-hour windows. Use `window_params` directly.
- [ ] Run the file's tests.

### Task 5: Cleanup, audit test, docs and perf note

**Files:**
- Modify: `reader.py` (`_sessions_cte` docstring; delete `_DAYS`, `_EDGES`, `_INNER_HOURS`, `hour_edges`, `_hourly_params`, `_EPOCH` if unused; drop `d0`/`d1` from `window_params`; keep `_TS` only if T1 kept it). Also `test_reader.py` (audit test, `window_params`, `hour_edges` tests) and `.ai-run/guides/integration/cli-analytics-storage.md` (short "Window semantics" paragraph).

Test-first: yes — the updated SQL-scope audit asserts, for every non-detail port method with the unfiltered, `deny_all` and `projects` filters, that every fact-table branch is scoped by `SELECT session_id FROM sel` (or inner-joined to `sel`) and that no SQL contains `day BETWEEN`, `$d0`, `$h0` or `_EDGES` text. `window_params` no longer returns `d0`/`d1` (and the `hour_edges` tests are removed).

- [ ] Update the tests, then run the reader and filter tests to see them fail or pass against the T1-T4 code. Grep to confirm the constants are unused before deleting them.
- [ ] Update the `_sessions_cte` docstring to "Sessions whose started_at is in the window; the only place the window is decided."
- [ ] Document the semantics in the guide. If a 30-day, ~28.5K-session run is not available locally, note in the guide that the §6.6 latency budget (825 ms) is unverified for whole-session scans.
