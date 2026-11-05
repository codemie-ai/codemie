# EPMCDME-16713 CLI Analytics: unknown lines are null, not 0 (backend)

## Problem
A session with no line source (no OTel lines metric, no `agent.tool.end` with numeric `lines_added`) is reported with Lines+/Lines-/Net lines = 0. Consumers cannot tell "no lines changed" from "lines unknown". Cause: `lines_daily` is written only when a line source exists, and also omits zero-sum rows (`rollups.py:1764-1782`, HAVING clauses at :1774/:1781). So "no row" means both unknown and real zero. The handler then coerces missing values to 0 through `_i()` (`cli_analytics_handler.py`: :364-365, :505, :535, :683, :830, :1054-1077, :1336).

## Approach
"Row present in `lines_daily` means lines are known." This needs both a writer change and a reader/handler change.

1. Rollup writer (`rollups.py`, `lines_daily` in `RECOMPUTE`). Drop the zero-sum filter from both arms, so a session that has a line source gets a row even when its sums are 0.
   - Source selection stays as is (`_LINES_SESSION`, `_carries_lines`, `_hook_lines`).
   - Mixed sessions keep sum-of-known.
   - `active_time_daily` keeps its own HAVING; it is not touched.
2. Reader (`reader.py`: `get_lines_totals`, `get_lines_daily`, `get_lines_by_user`, `get_lines_by_session`, `get_session_detail_scalars`). Keep returning nothing or NULL when there is no row; that is now the "unknown" signal. Zero rows return real 0.
3. Handler. Replace the `_i()` coercion for lines with null-preserving logic.
   - A session with no row has null lines and null `net_lines`.
   - An aggregate (branch row, repository row, user, overview KPI, efficiency `code_changes`) is null only if ALL its sessions are unknown. Otherwise it is the sum of the known ones.
   - Repository buckets start as null, not 0. `_group_branches_into_repositories` (:294-333) must add null-safely (null + null = null, null + n = n), and `net_lines` is null when both added and removed are null.
   - Overview daily buckets exist only for days that have a `lines_daily` row. A day with no known-lines session is omitted from the chart, as before; it does not get a null bucket. A day whose known lines sum to zero gets a bucket with `net_lines` 0. Building null buckets for every day of the window is out of scope (it changes the chart shape and needs the UI to handle null points).
4. Models (`rest_api/models/cli_analytics.py`): `lines_added`, `lines_removed` and `net_lines` become `int | None` (default None where they currently default to 0) on all 7 models.
   - They are `LocalAnalyticsRepositoryBranchRow`, `LocalAnalyticsRepositoryRow`, `LocalAnalyticsUserRow`, `LocalAnalyticsSessionRow` (the detail model inherits it), `LocalAnalyticsOverviewKPIs`, `LocalAnalyticsOverviewDailyBucket` and `LocalAnalyticsCodeChangesKPIs`.
   - The session row keeps only `net_lines`.
5. Docs: update `.ai-run/guides/integration/cli-analytics-storage.md` (:209) and `cli-analytics-events.md` (:439-442) to state "row present means known; the API returns null when unknown".

## Effects on other `lines_daily` readers (must be handled, not assumed)
- `get_efficiency` builds `code_ids` from sessions with non-zero lines, by truthiness of the session's entry (:1299-1304). Zero rows must not put sessions into the "code change" class. Test non-zero explicitly.
- `get_class_lists(by="day")` (`reader.py:478`) reads `lines_daily`. Restrict it to non-zero rows, so classification is unchanged.
- `ingestor.py:215` selects `DISTINCT day, session_id FROM lines_daily`. Zero rows add keys to that set. Verify during implementation that this is harmless (extra recompute keys only). I have not read the code around it.
- `_LINES_EMAIL` (`reader.py:138`) resolves a session's developer from `lines_daily`. Zero rows must not become a developer source the session did not have before, so `_LINES_EMAIL` and the session email of `get_lines_by_user` read only rows that report changed lines (`lines_added <> 0 OR lines_removed <> 0`). Decided in review (reviewer asked, EPMCDME-16713 MR): developer attribution stays as before zero-sum rows were kept.

## History and rollout
The rollup change applies only to keys recomputed after deploy. Existing sessions that had a real zero have no row and show null until their days are marked dirty (`dirty.py`, `METRIC_FACTS` covers lines_daily). Specify a backfill by marking dirty the days that have hook events with numeric `lines_added` or OTel lines metrics. Whether a ready backfill path exists for `lines_daily` was not traced. Plan it explicitly.

## Acceptance criteria
1. A session without a line source returns null for lines in `/sessions` (`net_lines`), `/sessions/{id}` and `/repositories` (repository and branch rows). The repro session `...0001` must be null.
2. Sessions with OTel or hook line data return the same values as before. The repro control `...0002` still has net 79.
3. A session with a line source that sums to 0 (numeric `lines_added: 0`, or an OTel metric summing to 0) returns 0, not null.
4. Aggregates are null only when all their sessions are unknown, otherwise the sum of the known ones. This applies to `/repositories`, `/users`, `/overview` (KPI) and `/efficiency` `code_changes`. The `/overview` daily chart keeps one bucket per day that has a `lines_daily` row; days with no known lines are omitted, not null.
5. `code_ids` and the day class lists are unchanged by zero rows.
6. The OpenAPI schema shows the lines fields as nullable.
7. A live PostgreSQL test covers the no-line-data, zero-sum and control cases. Handler tests cover null-safe aggregation with all-unknown, some-unknown and none-unknown inputs.

## Non-goals
- UI change in `../codemie-ui` (rendering "-" or "Not reported").
- Root cause of why sessions 8b7e8985 and 711c0464 lacked line data.
- Null buckets for days with no known-lines session in the overview daily chart (the chart omits them, as before).
- A coverage indicator for mixed sessions. They keep sum-of-known; the indicator is a design question to raise on the ticket or MR.
- Adding `lines_added` or `lines_removed` to the session row.
- Changing source selection (hook over OTel) or the `active_time_daily` zero-row rule.

## Decisions
- Store a zero `lines_daily` row for sessions with a line source (row present means known) over a reader-only "no row means null", which turns real zeros into null, and over reading raw events at query time — user, answers Q1
- Aggregate is null only if all its sessions are unknown, otherwise the sum of known, over null-if-any-unknown — user, answers Q2
- Apply null semantics to all endpoints (users, overview KPI, efficiency) over only the three named endpoints — user, answers Q3. The overview daily chart omits days with no known lines instead of emitting null buckets (decided at code review, 2026-11-05).
- Session row keeps only `net_lines` over adding lines_added/lines_removed
- Mixed sessions keep sum-of-known over a coverage indicator
- Live PostgreSQL test required over handler mocks alone, because mocks cannot see SQL changes

## Risks
- A partly unknown aggregate shows the sum of the known sessions, so it can be understated. Note this on the ticket or MR.
- Null changes the OpenAPI schema and the `../codemie-ui` consumer; the UI must handle null (out of scope here).
- Real-zero sessions already stored show null until the backfill runs.
- The zero rows' effect on `ingestor.py:215` and `_LINES_EMAIL` is unverified.
- The fallback quirk stays: a session with numeric `lines_added` and absent `lines_removed` counts removed as 0.
- The MR touches rollup SQL; ask a reviewer to post `/sanity` only if the repo rule applies (it targets dependency, Dockerfile and security changes, so it likely does not).
