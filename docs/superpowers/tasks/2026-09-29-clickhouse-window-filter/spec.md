# Spec: Session-Start Window Filter, ClickHouse Reader

Story: docs/stories/2026-09-28-analytics-reader-window-filter/children/clickhouse-window-filter/story.md
Reference design: docs/superpowers/tasks/2026-09-29-postgres-window-filter/spec.md (implemented; commits EPMCDME-15253)
Scope: `ClickHouseCliAnalyticsReader` (`src/codemie/repository/cli_analytics/clickhouse/reader.py`) and its tests. `filters.py` is not expected to change.

## Problem

The ClickHouse reader decides "in the window" per fact query: `day BETWEEN toDate(...)` on cost, lines, turns, files and active-time facts; raw `Timestamp BETWEEN` on tool, invocation and last-active queries; a last-activity overlap in `get_session_durations` (reader.py:494-503). `get_repository_sessions` has no time filter. `_sessions_cte` (reader.py:89-134) says time is deliberately not filtered. The PostgreSQL reader now uses one session-start rule, so the two engines return different totals for the same filter.

## Rule

Identical to PostgreSQL. A session is in scope iff `started_at` from `v_session_dimensions` is in `[start_dt, end_dt]`, inclusive. Once in scope, all its data is included, whatever the day or timestamp of the fact.

## Design

- Add one module-level predicate constant in `clickhouse/reader.py`, the counterpart of PG's `_STARTED_IN_WINDOW`, applied inside `_sessions_cte`. `sel` is the only place the window is decided. This includes the deny-all case and the existing user, project, repository and branch conditions.
- Every windowed fact query is scoped to `sel` with no per-row day or timestamp window predicate. Fact-to-`sel` joins are inner, as in PG. The unattributed bucket and the LEFT JOIN plus epoch defaults that only served it are removed. `_session_scope` is dropped or reduced where the join makes it redundant.
- `get_session_durations`: overlap check removed; uses `sel` only.
- `get_session_start_times`: uses `sel`; no separate `BETWEEN`.
- `get_repository_sessions`: gains the window through `sel`.
- `get_users_last_active`: sessions come from `sel`. Match PG's final behaviour (postgres/reader.py:261-275): the reported time is the latest dimension hook event at or before `end_dt` for each in-window session. Existing ClickHouse `Timestamp` clipping of those hook events may stay if it yields the same rows.
- Tool success, tool usage and invocations keep reading raw events (no hourly rollup in ClickHouse). Only the fact-side `Timestamp BETWEEN` predicates are replaced by the `sel` scope.
- `_sessions_cte` docstring and the reader's module docstring state the session-start-only contract and how it matches PG.
- Bounds: `f.params()` already binds `DateTime64(3)`, which truncates to milliseconds. This is the behaviour PG's `truncate_to_ms` emulates, so ClickHouse needs no extra truncation step.
- Row shapes, Python types and the `CliAnalyticsReader` port are unchanged.

## Acceptance criteria

1. Every date-range method includes a session iff its start is in `[start_dt, end_dt]`, inclusive of both bounds; none gates on last activity, event timestamp or fact day.
2. An in-window session contributes all its facts, including facts dated outside the window.
3. A session that started before `start_dt` contributes nothing, even with in-window activity.
4. `get_repository_sessions` applies the same rule.
5. The rule is defined once and reused by every affected method.
6. `get_session_durations` has no overlap special case.
7. Sessions with no `v_session_dimensions` row (no `started_at`) are excluded from all windowed methods, as in PG.
8. `get_users_last_active` selects sessions through `sel` and matches PG's result for the same filter.
9. A window with no session starts returns empty results, not an error.
10. Explicit `session_id` methods (`get_session_detail_*`, `get_skill_names_by_session`) are unchanged.
11. Tests assert boundaries (start exactly at `start_dt`, exactly at `end_dt`, just outside) and that no fact query keeps its own day or timestamp window predicate, using SQL-text assertions as in the PG tests.
12. `make verify` passes.

## Non-goals

- The PostgreSQL reader.
- Any change to API request or response shape, router or handler.
- Session-detail and skill-name lookups keyed by `session_id`.
- A ClickHouse hourly rollup for tool or invocation facts.
- Changes to `LocalAnalyticsFilter` semantics, retention (raw 90 d, rollups 365 d) or router window clamping.
- Backfilling sessions that lack dimension events.
- A live cross-engine row-level parity test harness (no live DB tests exist).

## Risks

- Excluding dimension-less sessions lowers cost totals on ClickHouse. This mirrors the decision already accepted for PG.
- Whole-session inclusion may scan more raw rows than day-bounded queries did, notably in tool and invocation queries; raw events have a 90 d TTL. Not benchmarked here.
- Parity is checked by SQL-text assertions and review against PG, not by data-backed tests.
