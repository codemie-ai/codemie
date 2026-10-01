# Session-Start Window Filter (ClickHouse Reader) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** A session is in scope iff `v_session_dimensions.started_at` is in `[start_dt, end_dt]`; every windowed `ClickHouseCliAnalyticsReader` method then includes all of that session's data, matching the PostgreSQL reader.

**Architecture:** Put the predicate once in `_sessions_cte` (`sel`). Fact queries drop their `day BETWEEN` / `Timestamp BETWEEN` predicates and always scope or inner-join to `sel`.

**Tech Stack:** Python, ClickHouse `{name:Type}` bind params via `f.params()`, pytest with the SQL-recording fake query fn.

**Spec:** `C:/Projects/codemie-dev/codemie/docs/superpowers/tasks/2026-09-29-clickhouse-window-filter/spec.md`. Reference design: `docs/superpowers/tasks/2026-09-29-postgres-window-filter/plan.md` (already implemented).

Commit per task using the repository's existing convention (`EPMCDME-####: Description`).

## Global Constraints

- Reader: `src/codemie/repository/cli_analytics/clickhouse/reader.py`. Tests: `tests/codemie/repository/cli_analytics/clickhouse/test_reader.py` (223 lines, no window assertions yet; reuse its `_flt` helper and fake `query_fn`).
- Bounds are inclusive. `f.params()` already binds `DateTime64(3)` (ms truncation), so add no truncation step. `filters.py` is not changed.
- Row shapes, Python types and the `CliAnalyticsReader` port stay stable (`assert_implements_port`).
- `get_session_detail_*` and `get_skill_names_by_session` are unchanged.
- Do not touch the PostgreSQL reader, router, handler, retention, or `LocalAnalyticsFilter` semantics.
- Negative constraints: no per-fact day/timestamp window predicate remains (T2-T4); no overlap check in `get_session_durations` (T1); dimension-less sessions are not backfilled, they drop out (T1, T2); no ClickHouse hourly rollup is added (T4); no API/handler change (no task touches them).

## Review Focus

- Window with no session starts returns `[]`, not an error (T1 test).
- `started_at == start_dt` and `started_at == end_dt` are both included (T1, inclusive `BETWEEN`).
- A session started before `start_dt` with in-window facts yields nothing, so no fact query may keep a day predicate (T5 audit).
- An unfiltered request must still be scoped to the window, so `_session_scope` must always apply (T1).
- Dimension-less/cost-only sessions vanish from cost queries, including the "unknown" user bucket (T2).

---

### Task 1: Shared predicate in `sel` and session-level methods

**Files:**
- Modify: `reader.py:88-141` (`_sessions_cte`, `_session_scope`), `:494-505` (`get_session_durations`), `:562-590` (`get_session_start_times`, `get_repository_sessions`)
- Test: `test_reader.py`

**Interfaces:**
- Produces: module constant `_STARTED_IN_WINDOW = "started_at BETWEEN {start_dt:DateTime64(3)} AND {end_dt:DateTime64(3)}"`. `_sessions_cte(f)` always puts it in the WHERE of the outer `sel` subquery, ANDed with the existing conditions (deny-all, users, projects, repositories, branch). `_session_scope(f, column)` ALWAYS returns `AND {column} IN (SELECT session_id FROM sel)`.

Test-first: yes — a recorded-SQL test asserts `_sessions_cte` for an unfiltered and a `deny_all` filter contains `started_at BETWEEN {start_dt`; `_session_scope` on an unfiltered filter is non-empty; `get_session_durations` SQL has no `last_event_at >=`; `get_session_start_times` has no second `BETWEEN` outside the CTE; `get_repository_sessions` SQL carries the window through `sel`; an empty result returns `[]`.

- [ ] Write the failing tests and confirm they fail.
- [ ] Add the constant and apply it in `_sessions_cte` (the WHERE is currently built only when conditions exist, so always emit it). Make `_session_scope` unconditional. Rewrite the `_sessions_cte` docstring to "Sessions whose started_at is in the window; the only place the window is decided."
- [ ] `get_session_durations`: drop the overlap WHERE, fix its docstring. `get_session_start_times`: drop the redundant `BETWEEN`. `get_repository_sessions`: no change beyond `sel`.
- [ ] Run `poetry run pytest tests/codemie/repository/cli_analytics/clickhouse -q` (existing tests depending on `has_session_filter` gating may fail until T5; note which).

### Task 2: Cost methods, inner-joined to `sel`

**Files:**
- Modify: `reader.py:145-234` (`get_cost_kpis`, `get_model_breakdown`, `get_cost_by_user`, `get_users`, `get_users_daily_activity`) and `:520-560` (`get_session_cost_facts`)
- Test: `test_reader.py`

Test-first: yes — for each method the recorded SQL has no `day BETWEEN`, and contains `JOIN sel` (not `LEFT JOIN sel`) or `IN (SELECT session_id FROM sel)`.

- [ ] Write the failing tests.
- [ ] Remove the `day BETWEEN toDate(...)` line from each WHERE (rewrite so no dangling `AND`, e.g. `WHERE 1 = 1` or a bare scope clause). Change `LEFT JOIN sel` to `JOIN sel` in `get_cost_by_user`, `get_users`, `get_users_daily_activity`, `get_session_cost_facts`.
- [ ] The `coalesce(... c.user_email, 'unknown')` fallbacks may remain harmlessly; `get_session_cost_facts` keeps its `is_unattributed` HAVING (it filters on repository). `get_users_daily_activity` keeps grouping by `c.day` with no filter on it.
- [ ] Run the file's tests.

### Task 3: lines, turns, files and active-time methods

**Files:**
- Modify: `reader.py:260-312` (`get_lines_totals/daily/by_user/by_session`), `:316-357` (`get_turns_by_session`, `get_file_facts_by_session`), `:507-518` (`get_active_ms_by_session`)
- Test: `test_reader.py`

Test-first: yes — recorded SQL for each method has no `day BETWEEN` and is scoped to `sel`; `get_lines_by_user` uses inner `JOIN sel`.

- [ ] Write the failing tests.
- [ ] Drop the day predicate (no dangling `AND`). `get_lines_by_user`: inner join. Update the `get_turns_by_session` docstring ("filtered by the same day-range predicate") and any "window's days" wording in `get_file_facts_by_session`.
- [ ] Run the file's tests.

### Task 4: Last-active, tool and invocation methods

**Files:**
- Modify: `reader.py:236-256` (`get_users_last_active`), `:359-462` (`get_tool_success_by_session`, `get_tool_usage`, `get_invocations`)
- Test: `test_reader.py`

Test-first: yes — recorded SQL for the tool/invocation methods has no `Timestamp BETWEEN` and scopes the tool/log branches via `IN (SELECT session_id FROM sel)`. `get_users_last_active` SQL inner-joins `sel` and has no lower `Timestamp` bound (only `h.Timestamp <= {end_dt...}`).

- [ ] Write the failing tests.
- [ ] Tool/invocation methods: still read raw `coding_agent_traces`/`coding_agent_logs`. Remove each `Timestamp BETWEEN` line, including the `e` (execution-span) subqueries in `get_tool_success_by_session` and `get_tool_usage`; they join on `tool_use_id` to the scoped `t`. Keep `_TOOL_SPAN`/`_EXEC_SPAN` filters, `session_id != ''`, grouping, ordering and `LIMIT 20`.
- [ ] `get_users_last_active` (`:251`): change to `JOIN sel s`, replace the `BETWEEN` with `h.Timestamp <= {end_dt:DateTime64(3)}`, so it reports the latest hook event at or before `end_dt` for each in-window session (PG parity, `postgres/reader.py:261-275`). Drop the now-redundant scope clause.
- [ ] Run the file's tests.

### Task 5: Audit test, module docs

**Files:**
- Modify: `reader.py` (module docstring near the top), `test_reader.py`; `.ai-run/guides/integration/cli-analytics-storage.md` (short "Window semantics" paragraph, shared with PG if one exists)

Test-first: yes — a parametrized audit over every non-detail port method with unfiltered, `deny_all` and `projects` filters asserts that no recorded SQL contains `day BETWEEN` or `Timestamp BETWEEN` outside the `sel` definition, that each fact-table branch is scoped by `SELECT session_id FROM sel` or inner-joined to `sel`, and that a bound-equal session (start at `start_dt` / at `end_dt`) SQL uses the inclusive `BETWEEN`.

- [ ] Write the audit test; run and fix any remaining stragglers in T1-T4 code.
- [ ] Module docstring: state the session-start-only contract and that it matches the PG reader (CH parity note; raw-event scans for tool/invocation, no hourly rollup).
- [ ] Guide paragraph. Note that whole-session raw scans on tool/invocation queries are not benchmarked (raw TTL 90 d).
