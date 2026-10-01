# Spec: Session-Start Window Filter, PostgreSQL Reader

Story: docs/stories/2026-09-28-analytics-reader-window-filter/children/postgres-window-filter/story.md
Scope: `PostgresCliAnalyticsReader` only (`src/codemie/repository/cli_analytics/postgres/reader.py`), plus the shared `filters.py` if the helper lives there.

## Problem

Date-range methods on the PostgreSQL reader decide "in the window" in four different ways: UTC day (`_DAYS`), `ts BETWEEN` (`_TS`), hourly rollup plus raw partial-hour edges (`_EDGES`, `_INNER_HOURS`), and the last-activity overlap in `get_session_durations`. `get_repository_sessions` has no time filter. The handler merges per-session rows from several methods, so these disagreements make totals fail to reconcile.

## Rule

A session is in scope if and only if `session_dims.started_at` is in `[start_dt, end_dt]` (bounds truncated to ms as in `window_params`). Once in scope, all of the session's data is included, whatever the day or timestamp of the individual fact. Every metric belongs to a session, so the window is decided only by the session set.

## Design

- One reusable predicate, defined once and applied inside `_sessions_cte` (`sel`). `sel` becomes the only place the window is decided.
- Every windowed fact query joins or scopes to `sel`. Fact queries carry no per-row day, ts, or hour window predicate and no raw partial-hour edge queries. This covers cost, lines, turns, tool facts, session files, active time, invocations, tool success, users, and users daily activity.
- Fact-to-`sel` joins are inner. Sessions with no `session_dims.started_at` are excluded, so the unattributed bucket disappears. The LEFT JOIN plus 1970-epoch defaults on cost queries go away where they only served that bucket.
- `get_session_durations` uses the same predicate. Its overlap check is removed.
- `get_users_last_active` picks sessions through the new `sel`. Its max-timestamp logic within those sessions is unchanged.
- `get_session_start_times` uses the shared predicate.
- `get_repository_sessions` gains the window through `sel`.
- `_sessions_cte`'s docstring ("Not time-filtered...") is updated. Dead constants and helpers (`_DAYS`, `_TS`, `_EDGES`, `_INNER_HOURS`, `hour_edges`, `_hourly_params`, `d0`/`d1`) are removed only once unused.
- Whether `invocations_hourly` is kept as a pre-aggregated source (joined to `sel` with no time predicate) is an implementation detail, allowed only if it does not change semantics.
- Row shapes and Python types stay stable, and the reader must still satisfy `CliAnalyticsReader` (`ports.py`, `assert_implements_port`).

## Acceptance criteria

1. Every date-range method includes a session iff its start time is in `[start_dt, end_dt]`, inclusive of both bounds. None gates on last activity, event timestamp, or fact day.
2. An in-window session contributes all its facts (cost, tokens, lines, turns, tool calls, active time, files), including facts dated outside the window.
3. A session that started before `start_dt` contributes nothing, even if it has activity inside the window.
4. `get_repository_sessions` applies the same rule.
5. The rule is defined once and reused by every affected method.
6. `get_session_durations` uses the shared rule with no overlap special case.
7. Sessions with no `session_dims.started_at` are excluded from all windowed methods.
8. A window containing no session starts returns empty results, not an error.
9. `get_session_detail_meta`, `get_session_detail_cost`, `get_skill_names_by_session` and other explicit `session_id` methods are unchanged.
10. Tests assert window boundaries (start exactly at `start_dt`, exactly at `end_dt`, just outside), and the SQL-scope audit test is updated for the new structure.
11. A 30-day window at about 28.5K sessions stays within the §6.6 latency budget (at most 825 ms per endpoint), or any regression is documented.
12. `make verify` passes.

## Non-goals

- The ClickHouse reader (the `clickhouse-window-filter` sibling story).
- Any change to the API request or response shape, router, or handler.
- Session-detail and skill-name lookups keyed by `session_id`.
- Changes to how `LocalAnalyticsFilter` handles users, projects, repositories, or branch.
- Changing retention (`session_dims` 365 d, raw 90 d) or the router's window clamping.
- Backfilling `session_dims` for dimension-less sessions.

## Risks

- Excluding dimension-less sessions reduces cost totals and the handler's priced-session universe (D1). This is accepted by decision.
- Whole-session inclusion with a start-only rule can scan more fact rows than day-bounded queries did. Criterion 11 guards this.
- No live-DB tests exist. Boundary correctness is verified through SQL-text assertions unless a data-backed test is added.
