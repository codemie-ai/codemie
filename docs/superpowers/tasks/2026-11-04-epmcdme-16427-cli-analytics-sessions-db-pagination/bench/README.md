# T7 measurements so far (local Docker Postgres, shared with the user's benchmark seed)

Target: codemie_analytics on codemie-dev-postgres-1: 1.9M session_dims rows, 992k in the last 30 days, 225k in 7 days,
`session_dims_started_at` already present (so the T5 index build cannot be timed here), cold/IO-bound (EXPLAIN shows
`shared read=139018` buffers; the CTE `sel` heap scan alone takes 6-7 s for 225k rows), /dev/shm too small for parallel plans.
Read-only. Script: scripts/cli_analytics_bench/run_sessions_bench.py (latency | explain | connections).

| window | request | branch p50 | main p50 |
|---|---|---|---|
| 7d | default | 15.1 s (3 runs) | 10.3 s (1 run) |
| 7d | cost | 23.8 s | DiskFullError (shm) |
| 7d | ctx_per_call | 22.8 s | not run |
| 7d | search | 14.9 s | not run |
| 7d | framework / fallback | statement timeout (30 s) | not run |
| 30d | default | 25.0 s | not run |
| 30d | cost, ctx_per_call, search, framework | statement timeout (30 s) | not run |

Per-statement times and plans: latency-branch-*.json, explain-branch-7d-default.json.
The 825 ms bar is not met in this environment. Not measured: K=4/7 connection runs (pinned vs bounded2), AC18 index build
timings, ingest 503s, refresher interplay: they need a disposable copy of the data and a running ingest/refresher.

## T4b: default sort, 30d, page 0 (EXPLAIN / per-statement ms)
| statement | before | after |
|---|---|---|
| window model flags (whole window) | 11928 | 11322 (dominant; hash semi join of 1M sessions with 1.9M cost_daily rows + 1M-group aggregate spilling to disk) |
| page + count (one statement) | 3224 | page 17 ms + count 1706 ms (index-ordered LIMIT; count still reads the 1M heap rows for the scope predicate) |
| 5 detail statements + skills | 1-22 each | unchanged (already by primary key on the page ids) |
Detail statements were never the problem: restricted_to(page_ids) already uses session_dims_pkey. See explain-before-30d-default.json,
explain-after-30d-default.json, latency-after-t4b-30d.json (p50 14.3 s, was 25.0 s; flags statement is ~80% of it).

## T4c: page-scoped unpriced metadata (default/ctx/search) + partial count index (dev DB, warm, 3 runs)
Index built on the dev DB: `session_dims_started_shared` (started_at) WHERE started_at IS NOT NULL AND NOT <private scope>;
plain CREATE INDEX 2.8 s on 1.9M rows (no ingest/refresher load, so not the AC18 figure), 41 MB, VACUUM ANALYZE 2.1 s.
Count plan: Parallel Index Only Scan on it, Heap Fetches 891 of ~990k rows.

| default sort | before T4b | after T4b | after T4c |
|---|---|---|---|
| flags (window-wide) | 11928 ms (30d) | 11322 ms | not issued |
| page | in count stmt | 17 ms | 2.3 ms (30d) / 4.8 ms (7d) |
| count | 3224 ms (page+count) | 1706 ms | 105 ms (30d) / 67 ms (7d) |
| details x5 + skills | 1-22 ms | 3-13 ms | 2-35 ms |
| request p50 | 25043 ms (30d) | 14321 ms | 137 ms (30d), 295 ms (7d; p95 4.9 s, one cold run) |

Other sorts at 30d (1 run, after T4c): ctx_per_call 12.6 s (its 21 s token/cache aggregation over the window is first statement);
search, cost, framework: statement timeout 30 s (window-wide work is inherent to those paths). Not yet addressed.
Raw: latency-after-t4c-{7,30}d.json, latency-after-t4c-30d-others.json, explain-after-t4c-30d-default.json.

## T4d: search and ctx_per_call (dev DB, 3 runs, p50; statements: window-wide first, then page details)
| request | window | before | after | first (window-wide) statement before -> after |
|---|---|---|---|---|
| search ("fix", start_time) | 30d | statement timeout (30 s) | 2.4 s | >30 s -> 1.8 s (page + exact count in one pass) |
| search | 7d | not measured | 1.9 s | -> 2.3 s |
| ctx_per_call | 30d | 12.6 s (1 run) | 10.5 s | ~21 s cold / 13 s -> 9.9 s |
| ctx_per_call | 7d | not measured | 8.2 s | -> 7.1 s |
Diagnosis. Search: the wide CTE (all activity columns) was materialised, and the index-ordered page walked the whole 1M-row
window with random heap reads because "fix" matches ~6 rows (the page needs 20), then the count scanned it again. A bare
ILIKE seq scan of the window is ~4 s cold. Now: lean CTE plus the two searched text columns, one statement, `count(*) OVER ()` and a
top-N sort. It is bound by the ILIKE pass itself; a leading-wildcard ILIKE cannot use a btree.
ctx_per_call: the sort key is (sum cache_read + subagent totals) / sum api_call_count per session, an aggregate over every
cost_daily row of the window (~1M session groups, hash aggregate spills ~64 MB) joined to session_usage subagent totals; no stored
column holds it (session_dims has api_calls but no cache-read tokens). Page, count and details are cheap once the key exists.

## Documented limitations (for T8 and the MR description; requester approval at review)
- Search: about 2 s at 30d (1.8 s window statement; 7d about 1.9 s) on 1M sessions. A leading-wildcard ILIKE over first_prompt, session_title, repository, branch cannot use a btree, so one pass over the window is needed for the exact count; no pg_trgm index (user decision).
- ctx_per_call sort: about 10 s at 30d (7d about 8 s). The key is an aggregate of cache-read and api-call counts over every cost_daily row of the window (about 1M session groups, spilling to disk) plus subagent totals; no stored column (S4 out of scope), no work_mem change (user decision).

## T4e: cost sort and framework filter, 30d, EXPLAIN ANALYZE per statement (parallel off for the plans: /dev/shm of the container is too small for parallel hash joins)
Framework (window statement returns every ordered session id with its names; Python classifies):
| statement | before | after |
|---|---|---|
| window (ids + names) | 284 s (988k correlated name subqueries, 0.18 ms each, index scan 71 s) | 23 s (set-wise names: each source read once, session_dims once) |
| model flags | 9.3 s | 6.9 s (12 s in the request) |
| request | statement timeout | one run 58 s wall (24.7 s + 12.1 s SQL, the rest is Python on 988k rows) |
Cost sort (ran as-is, no change): flags 12-19 s + key/page/count statement 22.5 s (hash join of the window with 1.9M cost_daily rows 11 s,
(session, model) group 988k rows spilling 110 MB, second aggregation to the session key 82 MB spill, rest is the sort: 0.3 s);
the page and the count already share one `filtered` CTE (the key is computed once). Request: statement timeout.
Raw: explain-t4e-30d.json, explain-t4e-after-30d-framework.json, latency-after-t4e-30d.json.

## T4f: cost sort (dev DB, 3 runs; statements in order)
| statement | before | after |
|---|---|---|
| model flags (window, (session, model) groups) | 12-19 s | gone (flags come from the key statement's groups) |
| model list for the rates | - | 0.7-1.0 s (distinct model_name over cost_daily and the subagent totals; unscoped) |
| key + page + count + flags | 22.5 s | 15.2 s (30d) / 12.1 s (7d): groups built once (MATERIALIZED CTE), no normalised-name regexps |
| detail statements | 2-120 ms | 2-118 ms |
| request p50 | statement timeout (> 30 s; 35-40 s of SQL) | 16.7 s (30d, p95 20.5 s) / 10.4 s (7d, p95 16.7 s) |
Left in the key statement (30d plan): the (session, model) group aggregation over the window's token rows 8 s (spills 107 MB), the
per-session key aggregation 2 s (spills 77 MB), the window scan 1.7 s. Not done: pruning cost_daily by day (a session's token rows can
precede its stored started_at), a single GROUP BY session (the cache-split clamp and the tokens > 0 skip are per (session, model) sums).
Raw: latency-after-t4f-{7,30}d.json, explain-t4f-30d-cost.json.

## T4g: framework filter via distinct name sets (dev DB; the 30d runs are near the 30 s statement timeout and noisy)
Flow on one held connection and one transaction: (1) CREATE TEMP TABLE of the filtered window's classifier names, built set-wise;
(2) read the distinct name sets (a handful, 0.05-0.45 s), Python classifies them; (3) page statement over the sessions whose set is
accepted (start_time page 6-15 ms; cost-sort page 9 s: groups + key + flags); (4) the other sorts read the window model flags.
| request | window | before (T4e) | after |
|---|---|---|---|
| framework (start_time) | 7d | not measured | p50 21.5 s (p95 24.2 s): names 11.0 s, sets 0.05 s, page 6 ms, flags 13.5 s |
| framework (start_time) | 30d | one run 58 s (SQL 24.7 + 12.1 s, Python ~20 s) | 37.6 s (names 25.5 s, page 13 ms, flags 12.8 s); later runs hit the 30 s timeout in the names or the flags statement |
| framework, fallback label, cost sort | 7d | not measured | one clean run 23.5 s (names 13.0 s, page+key+flags 9.4 s); a 3-run set had one 30 s timeout |
| framework, fallback label, cost sort | 30d | timeout | one run 36.4 s (names 23.1 s, page 9.0 s); other runs timed out |
Versus T4e the SQL no longer returns or classifies a million rows (Python time ~20 s gone). The names pass (set-wise union of five
sources, an external-merge sort of 1.45M rows and a group aggregate) is 11-25 s and the model flags 13 s: together at or over the 30 s limit
at 30d on this 1M-session window. Raw: latency-after-t4g-{7,30}d.json (3 runs), latency-t4g-probe*.json (single runs).
- Framework filter: 7d about 22-24 s; 30d 36-38 s when it completes (names pass 11-25 s + model flags 13 s), and it hits the 30 s statement timeout intermittently. Cost sort: 7d 10 s, 30d 17 s (p50). Cause: both read the whole window's token rows and the names of every session; no stored column or rollup (user decision).

## T7b: AC16 connection comparison (dev DB, noisy; synthetic stand-ins stated)
Setup: pool of 8 (default), 30-day requests, `connections` mode of run_sessions_bench.py. K concurrent workers (3 rounds each) cycle default / search /
ctx_per_call; one cost-sort request is started first as the pool hog (it holds its connection(s) 15-35 s). The refresher is a stand-in: one pool connection
held 2 s of every 4 s (the real refresher was not running). Ingest is a stand-in probe: acquire a pool connection with the ingest timeout (1 s) every 100 ms;
a timeout counts as an ingest 503. Because ctx_per_call takes ~10 s here, the mix is dominated by long holds; the conclusion is about pool pressure
with slow requests, not about the 825 ms target. No `main` run for comparison (main's gathers hold up to 5 connections per request).
| candidate | K | peak held / pool occupancy p95 | ingest acquire p99 | ingest 503 / probes | request errors (30 s timeouts) | acquire wait p99 default / search / ctx_per_call / cost |
|---|---|---|---|---|---|---|
| pinned | 4 | 7 / 6 | 363 ms | 3 / 322 | 3 | 65 / 64 / 63 / 66 ms |
| bounded-2 | 4 | 8 / 6 | 33 ms | 0 / 337 | 4 | 113 / 109 / 101 / 131 ms |
| pinned | 7 | 8 / 8 | 1000 ms | 28 / 168 | 9 | 1319 / 777 / 5899 / 122 ms |
| bounded-2 | 7 | 8 / 8 | 1000 ms | 25 / 103 | 10 | 439 / 599 / 1267 / 67 ms |
AC16 rule: a candidate qualifies only with ingest acquire p99 < 1000 ms and no ingest 503 at K = 4 and 7. Neither qualifies: pinned fails both K
(3 and 28 ingest 503s), bounded-2 passes K = 4 (0 503) and fails K = 7 (25 503s, p99 at the 1 s timeout). With a pool of 8, K = 7 requests plus the hog and
the refresher (9+ connection holders) exceed the pool whichever candidate runs, so the K = 7 failure is the pool size meeting slow requests. The 3 pinned K = 4
ingest 503s, with a free connection available, point at connection-open time under a saturated database (noise cannot be ruled out). Per the spec the requester
decides at MR review; `pinned()` stays shipped (default), the bounded-2 candidate stays in the bench script, nothing is deleted.
Raw: connections-{pinned,bounded2}-k{4,7}.json.

## T7b: AC18 plain index build (disposable copy ac18.session_dims, 1,000,000 rows, 703 MB with its 3 other indexes; dropped afterwards)
Writers during the build: binding-like UPDATE of 50 sessions every 50 ms, and a refresher-like transaction updating 5000 rows holding its row locks 0.8 s (and in a
second run 8 s). Build = `LOCK TABLE ... IN SHARE MODE` under `lock_timeout = 5s` (lock wait timed alone), then `CREATE INDEX` (build timed alone).
| index | refresher hold | lock wait | build | failed attempts at 5 s | longest binding UPDATE after/at the build |
|---|---|---|---|---|---|
| session_dims_started_at | 0.8 s | 1.05 s | 1.36 s | 0 | 2.4 s |
| session_dims_started_shared | 0.8 s | 0.93 s | 1.17 s | 0 | 2.1 s |
| session_dims_started_at | 8 s | 1.16 s | 4.57 s | 0 | (first batch after the build waited 5.1 s) |
| session_dims_started_shared | 8 s | 0.87 s | 1.68 s | 0 | 2.5 s |
Refresh batch (5000-row UPDATE) before vs after the index, hold 0.8 s: 567 -> 404 ms (started_at), 483 -> 700 ms (started_shared); n = 5-6 batches each, inside the
noise: no measurable per-batch cost (neither index is on a column the batch rewrites per event; started_at already has an index). No attempt failed under the 5 s
lock_timeout because the lock waited for at most one batch's remaining hold (a real batch may run up to 5 minutes: then the attempt fails after 5 s and the next job retries).
Writers are blocked for lock wait + build: 2.1-5.8 s here, longer than the 1 s ingest acquire timeout, so ingest bindings answer 503 (plugins retry).
Runbook threshold derived from this: build time is about 1.2-1.7 s per million rows idle and up to 4.6 s per million with a busy refresher; a plain build
blocks writers for the lock wait plus the build, which exceeds the 1 s ingest timeout from about 0.5 million rows. Plain build at deploy is acceptable
below 1,000,000 `session_dims` rows (blocked 2-6 s on this hardware); above that pre-build both indexes CONCURRENTLY. Raw: index-build-ac18-hold{0.8s,8s}.json.

## T7b: the three live tests that failed against the large dev DB
Against a temporary database (migrated, partitions made), main (worktree /tmp/codemie-main, removed afterwards): test_live_harness_label and
test_live_summary_skill_names fail on main too (TypeError: the reader method needs the keyword-only `scope`: stale tests); test_live_session_subagent_cost passes
(10) on both main and the branch: its earlier failure was the large dev DB, not the code. Not fixed.

## T6b: bounded-2 shipped (requester decision after AC16)
`pinned()` and its fake/tests are removed; `bounded()` acquires two connections (second within 0.5 s, else one), runs at most two statements at once, releases both
before pricing. Dev DB sanity (30d, default sort, 3 runs): p50 117 ms (page 2 ms, count 87 ms, details 2-10 ms); 12 concurrent default requests leave
0 connections held and all 8 pool connections idle (no leak; peak 8 = pool full, no deadlock). Live tests on a temporary database: session page 6, owner scope 21.

## AC15 baseline and AC9 plans (fix-up of the final review, 2026-11-05; dev DB, 1.9M `session_dims` rows, shared, idle at the start)
Requester approved the documented limits (Stage 6 review, 2026-11-05) once the `main` baseline below was measured. `main` ran from a git worktree of `main` in /tmp
(removed afterwards) with the branch's bench script, read-only; `latency`, 3 runs after one warm-up, p50, 30 s statement timeout. The dev DB already holds the
branch's two indexes, which helps `main` only. `--no-parallel` is a new session-level option of the script (`SET max_parallel_workers_per_gather = 0`), used because
/dev/shm is too small for parallel hash joins (`DiskFullError`, which recurred on `main` cost and framework in the parallel set).
| request | window | main, parallel on | main, parallel off | branch, parallel on | branch, parallel off |
|---|---|---|---|---|---|
| default | 7d | timeout (30 s) | 12.6 s | 43.7 ms | 49.3 ms |
| default | 30d | timeout (30 s) | 27.7 s (p95 29.8 s) | 114.7 ms | 149.8 ms |
| search | 7d | timeout | 17.8 s | 2.9 s | 8.4 s |
| search | 30d | 15.8 s | 22.2 s | timeout (latest run; 2.4 s at T4d) | 10.7 s |
| ctx_per_call | 7d | timeout | 17.6 s | 8.2 s | 11.6 s |
| ctx_per_call | 30d | 21.8 s | 15.7 s | 19.7 s (10.5 s at T4d) | 21.4 s |
| cost | 7d | DiskFullError | timeout | 8.7 s | timeout |
| cost | 30d | DiskFullError | timeout | timeout (latest run; 16.7 s at T4f) | timeout |
| framework | 7d | DiskFullError | timeout | 19.6 s | 31.6 s |
| framework | 30d | DiskFullError | timeout (window statement alone 284 s unbounded, T4e) | timeout (36-38 s when it completes, T4g) | timeout |
Framework filter vs main: not a regression. main fails every run (timeout, or DiskFullError with parallel workers); the branch completes it at 7d and intermittently at 30d.
The branch numbers drift run to run (the 30d search, cost and ctx_per_call runs above are slower than the T4d/T4f runs on the same data), so the table is a like-for-like
comparison on one host, not a precise figure. Raw: latency-main-full-{7,30}d.json (parallel on), latency-main-serial-{7,30}d.json, latency-branch-parallel-{7,30}d.json,
latency-branch-serial-{7,30}d.json.

AC9 EXPLAIN (ANALYZE, BUFFERS) of the shipped statements (parallel off, no statement timeout), explain-ac9-{7,30,90}d.json. The dev data holds history since 2025-12-08 (333 days when the plans were taken), longer than the longest window (90 days);
its raw tables are almost empty (18 `log_events`, no `spans`, one `hook_events` row), so the raw-record ratios are not production's. The raw records
are large and git-ignored; `explain-ac9-trimmed.json` in this folder is the tracked, trimmed copy of the 2026-11-06 re-run on the final code. Selected rows of 1,900,082: 7d 198,972 (10.5%), 30d 966,248 (50.9%), 90d 1,174,262 (61.8%).
| statement | 7d | 30d | 90d |
|---|---|---|---|
| default: page (index-ordered LIMIT on session_dims_started_shared) | 7.0 ms | 0.5 ms | 0.9 ms |
| default: count (index-only on session_dims_started_shared) | 103 ms | 215 ms | 295 ms |
| search: window page + count (one pass, no usable index) | 8.5 s | 9.2 s | 24.6 s |
| ctx_per_call: key + page + count | 12.3 s | 22.1 s | 30.6 s |
| cost: model list (rates) / key + page + count + flags | 2.1 s / 13.5 s | 1.0 s / 21.8 s | 1.2 s / 34.9 s |
| framework: names (CREATE TEMP TABLE select) / model flags | 16.4 s / 22.5 s | 30.3 s / 14.6 s | 26.2 s / 23.2 s |
The window token statements (ctx_per_call, cost, flags, names) read every cost_daily / invocations_hourly partition, so they grow with the window; day pruning is not safe
(a session's token rows can precede its stored started_at). The framework page statement reads the temp table of names, so it cannot be planned alone (6-15 ms measured).

## T6c: window phase on one connection (MR review, 2026-11-06)
The K = 4 and K = 7 runs above are of the first bounded-2 shape (both connections held through the whole request). The shipped shape is now `bounded()` (one connection, window statements one after another) and `widened()` (a second one around the detail statements only). The slow paths (cost sort, framework filter, `ctx_per_call`) hold one connection for most of their run, so four of them hold 4 of the pool's 8, not all 8. The AC16 stand-in was not re-run for this shape; the unit tests pin the connection counts (`test_bounded_reader.py`).

## AC9 re-taken on a copy of the dev data, MR schema (2026-11-06, MR review)
Why a copy: the dev database is at the part 2 revisions (part 2's own `a1c1a0000011` and `a1c1a0000012`, before part 2 is renumbered after the rebase onto `main`) and holds part 2's columns and indexes
(`session_dims_fw_win`, `_cost_key`, `_ctx_key`, `_ctx_win`), so plans taken on it are not the plans of this MR. The
analytics schema was copied into a scratch PostgreSQL 17 (1,011,198 `session_dims` rows, every session derived),
downgraded with Alembic to this MR's schema (then revision `a1c1a0000010`, now `a1c1a0000011` after the rebase onto `main`), `VACUUM (FULL, ANALYZE)` on `session_dims`, `VACUUM (ANALYZE)` on the rollups,
one warm-up run discarded, the second kept. The dev database itself was only read.
Scope: the default sort and the search, the requests part 1 makes fast. The cost sort, `ctx_per_call` and the framework
filter are the limits of AC15 (part 2) and are not AC9 evidence. Windows start mid-day (now minus N days), which
exercises the first-day exactness arm; the UI presets start at 00:00 UTC.

| window | default: page | default: count | search: window statement (+ 7 detail statements, 0.1 to 19 ms each) |
|---|---|---|---|
| 7 d | 4.05 s | 3.36 s | 5.54 s |
| 30 d | 3.75 s | 2.74 s | 4.59 s |
| 90 d | 8 ms | 0.36 s | 3.79 s |

The default page and count are an ordered walk and an index-only scan of `session_dims_active_shared_cov`. The 4 s of the
7- and 30-day pages is the exact check of the sessions that started before the mid-day window start on the window's first
day (rows 0 returned, 4,043 ms): the cost documented in the guide. Search is one pass over the window's `session_dims`
text columns (a Bitmap Heap Scan at 7 days, a Seq Scan at 30 and 90). Not reproduced: the production ratios of
`session_usage`, `log_events` and `spans` (the dev raw tables are almost empty) and a history longer than the window at
every size. Plans: `explain-ac9-trimmed.json`. The first attempt, on the cold freshly restored copy, planned the default
page on `session_dims_started_shared` and took 3.9 / 8.0 s; after the vacuum and a warm-up it uses the covering index.

