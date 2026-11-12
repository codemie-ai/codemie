# EPMCDME-16427 part 2: Sessions list benchmark results (2026-11-06)

Scratch PostgreSQL 17 in Docker (macOS VM), 300k sessions over 120 days. Windows: 7 days = 16.9k sessions, 30 = 71k,
90 = 214k. Times are ms of `get_sessions` (p50/p95). There is no agreed latency target (825 ms was an assumption). Request statement timeout: the pool's 30 s.
Two runs are recorded: **run 1** (the tables from "Quiescent" onward) and **run 2** (review round, the sections
below, a fresh database with the migrations applied before the load, 8 warm runs per cell). Where they disagree, run 2
is the later measurement; both come from one noisy Docker VM, so differences under about 2x are noise (run 2 cost 7d:
1129 stored vs 930 fallback, p95 1607 vs 2639).

## Known limitation (AC8, AC10)

There is no agreed latency target: the 825 ms used in the columns below was an assumption. The default sort and the
search are fast. `ctx_per_call`, `cost_usd`, the framework filter and their combinations take seconds at this size,
(the runs below predate the change that reads the unfiltered ctx key inputs from `session_dims_fw_win` index-only and
the unstamped sessions from `session_dims_rates_version`; the cost list still Seq Scans `session_dims`, AC8 for cost). The
benchmarks are re-run to show what the work achieves.

## Run 2: before (fallback = today's statements, kill switch off) vs after (stored on), warm p50/p95

| request | 7d before | 7d after | 30d before | 30d after | 90d before | 90d after | vs 825 ms (assumed) |
|---|---|---|---|---|---|---|---|
| default | 109/124 | 96/164 | 156/233 | 120/156 | 204/243 | 171/302 | met |
| search | 91/139 | 92/99 | 429/649 | 280/310 | 275/379 | 214/295 | met |
| ctx_per_call | 382/526 | 475/585 | 2569/4432 | 644/1000 | 5872/7215 | 980/1160 | 7d met; 30d/90d slow |
| cost_usd | 930/2639 | 1129/1607 | 5077/5861 | 2420/3120 | 12707/13151 | 4866/6140 | slow |
| framework | 1019/2895 | 1275/1698 | 5326/6183 | 1665/2099 | 12664/15475 | 2576/5599 | slow |
| framework + ctx_per_call | 1030/1151 | 1782/2621 | 7000/9120 | 2004/2356 | 17702/19974 | 3157/4573 | slow |
| framework + cost_usd | 1048/1150 | 2067/2416 | 6986/9510 | 3654/4833 | 18258/21741 | 8585/11423 | slow |
| framework (group by name) | 1299/1827 | 2036/2528 | 7381/9127 | 3127/4166 | 17140/20687 | 9776/12121 | slow |

What each type reaches (stored on, fully stamped): default and search stay under the target at every window;
ctx_per_call is 0.5-1.0 s p50; cost_usd 1.1-4.9 s; framework 1.3-2.6 s; framework with a sort 2.0-8.6 s. Stored is
about 4x (ctx_per_call 30d), 2x (cost 30d), 3x (framework 30d) and 2.6-5x at 90 days faster than the fallback, but
NOT at 7 days: there the fallback is as fast or faster for cost, framework and their combinations (small windows; the
stored path pays the `session_dims` scan plus the merge). The earlier claim "stored beats fallback at every window" is
withdrawn.

## Run 2: unstamped sessions (set S) are expensive (the S-merge, AC9)

30-day window, stored on, a number of the window's sessions without a stamp (|S|), no ingest, 5 runs, p50/p95:

| \|S\| | ctx_per_call | cost_usd | framework |
|---|---|---|---|
| 0 | 644/1000 | 2420/3120 | 1665/2099 |
| 190 | 1452/3168 | 7621/11948 | 28539/31938 (at the 30 s timeout) |
| 1899 | 3648/8198 | 7465/8415 | statement timeout |

A small S multiplies the cost of cost_usd (3x) and the framework filter (17x): the live computation of S and the
merge are not proportional to |S|. The merge is correct, but every unstamped session in a window slows the request, so
the first backfill (4.4k sessions/s, about 70 s per 300k sessions in run 1) and the maintenance job matter more than
the S cap (5000) suggests. The readers start off and the maintenance job turns them on once every session is stamped; an unstamped table above the cap
(`s_over_cap`) takes today's statements; a partly stamped one pays the merge until the backfill ends. A run under a
concurrent ingest stand-in (5 sessions/s, |S| about 1900) timed out for ctx_per_call, cost and framework. This is a
measurement gap closed and a limitation found, not a fix in this change.

## Run 2: re-price, backfill, framework groups, ingest baseline

- Re-price of all 300k stamps to a new rate set (`sort_keys_cli reprice`, batch 1000, no pause, four indexes
  maintained): 257.2 s (1.2k sessions/s), WAL 1.23 GB. Meanwhile the readers compute every session whose stamp differs
  live (`cost_key` is trusted only at the current version). Backfill of 1995 sessions: 16.4 s (WAL 45 MB), of 200:
  2.3 s. Run 1 backfill of 300k sessions: 68.1 s.
- Framework group count: 184 distinct classifier-name sets in the 30-day and in the 90-day window.
- Ingest baseline (K=4 concurrent requests, hog = cost, pool 8, ingest-like probe every 100 ms with the 1 s acquire
  timeout): stored on 3 of 24 probes got the acquire timeout (503), wait p99 1000 ms; stored off (today's statements)
  3 of 27, p99 1000 ms. The same: the change adds no ingest 503s; the pool is full (8 of 8) in both, the existing limit
  of the two-connection request.

## Indexes (decision 2026-11-06)

`pg_stat_user_indexes` after run 2 (every request type, 7/30/90 days, stored on and off): `idx_scan`
`session_dims_ctx_key` 0, `session_dims_ctx_win` 0, `session_dims_cost_key` 0, `session_dims_fw_win` 12,701. The three
unused indexes (101 + 82 + 89 MB at 300k rows) are removed from revision `a1c1a0000012`; `session_dims_fw_win` is kept
(the framework statements scan it). Sizes: bulk-built after the load the four indexes were about 78 MB together (run
1: 141.7 MB total index size against 63.5 MB before); maintained through the load and the re-price they were 355 MB
(run 2); the 123 MB figure of run 1 mixed the two. The HOT ratio stays 0.0 for the remaining index.

PostgreSQL 14: the SQL of the stored statements and the migration were verified on 14 and 17 (storage guide); the
benchmark itself ran on 17 only and no 14 timing is claimed.

## Quiescent, warm, stored on (fallback = kill switch off, 5-6 runs)

| request | 7d | 30d | 90d | 30d fallback | 90d fallback | verdict |
|---|---|---|---|---|---|---|
| default sort | 96/124 | 119/143 | 162/246 | 134/161 | 141/153 | fast, no regression |
| search | - | 291/392 | 228/373 | 320/401 | 218/323 | fast |
| ctx_per_call | 550/1383 | 646/907 | 962/1562 | 1420/1582 | 4734/6164 | 7d p95, 90d p95 miss: slow (user, 2026-11-06) (30d p95 907 ms also misses) |
| cost_usd | 1769/9256 | 2732/4206 | 7304/10393 | 4317/4356 | 10098/12337 | slow (user, 2026-11-06) |
| framework | 1858/2359 | 1656/3265 | 2851/4008 | 3603/4193 | 9016/9763 | slow (user, 2026-11-06) |
| framework + ctx_per_call | 2620/5859 | 2238/3742 | 3768/5081 | 5623/8466 | 13937/17933 | slow (user, 2026-11-06) |
| framework + cost_usd | 2593/3982 | 3706/4030 | 8066/10387 | 5127/6429 | 14986/16811 | slow (user, 2026-11-06) |
| framework_fallback (group by name) | - | 4133/6274 | 7703/9722 | 5085/6652 | 12336/14184 | slow (user, 2026-11-06) |

Run 1: stored beat fallback at 30 and 90 days (ctx_per_call 90d: 4.9x), not at 7 days (see run 2). Parity (ids, order, total, rows) is identical stored vs
fallback for all request types at 7/30/90 days. Part 1 baseline: all three sorts time out at 30 s at 30 days.

## Cold (server restarted, one run per request, OS cache of the VM not dropped), stored / fallback

| | 7d | 30d | 90d |
|---|---|---|---|
| default | 7224 / 5930 | 1274 / 950 | 940 / 937 |
| ctx_per_call | 1659 / 908 | 1824 / 2267 | 1521 / 8263 |
| cost_usd | 4006 / 3896 | 5017 / 4652 | 6832 / 13055 |
| framework | 5820 / 4141 | 4659 / 5638 | 3687 / 12269 |

All cold values: slow (user, 2026-11-06) (the 7d default is dominated by the first-touch of a cold table, not
by the change; the 30/90 default is slow cold on fallback as well).

## Concurrent ingest stand-in (`ingest_sim.py`: inserts + 30 s refresher cycle incl. the sort-key step), 30 days

- 50 sessions/s (10.2k inserted, 7 cycles of ~1860 sessions): default 125/143 (fast), search 324/398 (fast);
  cost, ctx_per_call and framework requests timed out (run 1 said 10 s; the request statement timeout is the pool's 30 s, 10 s is the ingest statement timeout): slow (user, 2026-11-06).
  Refresher sort-key step 964 ms p50 / 1699 ms max per cycle (about 2000 sessions/s); activity derive 204 ms p50.
- 5 sessions/s (6.2k inserted, 21 cycles of ~358): default 198/329 (fast), search 305/377 (fast), ctx_per_call
  1361/1801, cost 3587/4442, framework 3381/4377: slow (user, 2026-11-06). Step 195 ms p50 / 767 ms max.
- Share of requests with non-empty S: 0.0, |S| p50/max 0/0, cap-fallback rate 0.0 in both runs: the stand-in's cycle
  derives new sessions before a request sees them in a non-derived state, so S was never observed non-empty; S-merge
  cost is measured in run 2 above.
- HOT ratio of `session_dims` updates: 0 of 106 (a 3-run trial at 50/s) and 0 of 23497 (the 50/s long run) = 0.0. The sort-key,
  activity and window columns are all indexed (partial indexes on the copied predicate), so updates of them cannot be
  heap-only; every refresher update writes new index entries (WAL 362 MB for the 300k backfill). Table bloat after the
  50k-session skew backfill: 154.0 -> 163.1 MB table, VACUUM did not shrink it.

## Skewed data (50k extra sessions started 100-119 days ago with the highest ctx_per_call and cost of all), 30 days

default 130/198 (fast), ctx_per_call 701/1018 (the skew keys sit outside the window: the window index serves it;
p95 misses: slow (user, 2026-11-06)), cost 3595/4544, framework 2152/2998: slow (user, 2026-11-06).
No degradation against the unskewed run beyond noise.

## Connections (K concurrent requests, pool 8, one request holds 2 connections, hog = 1 slow statement)

| K | hog | peak held | request errors | ingest probes | ingest 503 | ingest acquire wait p99 | default wait p99 |
|---|---|---|---|---|---|---|---|
| 4 | cost | 8 | 0 | 30 | 1 | 1000 ms | 458 ms |
| 4 | framework | 8 | 0 | 18 | 0 | 566 ms | 372 ms |
| 7 | cost | 8 | 0 | 16 | 1 | 1000 ms | 873 ms |
| 7 | framework | 8 | 0 | 19 | 0 | 918 ms | 865 ms |

At K=4 the pool is fully occupied (8/8); one ingest probe in 16-30 hit the 1 s acquire timeout (503) with the cost hog.
No part 1 baseline was run for this table (the shipped request holds two connections in both versions): run 2 above adds the baseline (stored off): no difference.

## Backfill, sizes

- Activity derive 25.6 s / 316 MB WAL; sort keys 68.1 s (4.4k sessions/s, 300 batches of 1000) / 362 MB WAL (300k).
  Skew load of 50k: activity 4.5 s / 88 MB, sort keys 14.3 s / 110 MB.
- `session_dims` 72.7 MB table + 63.5 MB indexes before; 129.4 MB + 141.7 MB after columns and four new indexes
  (the four indexes about 78 MB bulk-built, 355 MB maintained through the load: see Indexes). 366k rows after skew: 163.1 MB + 354.8 MB.

## Plain index build under writers (`index_build_bench.py --revision 12 --only-all`), four indexes in one transaction

| rows | lock wait | SHARE lock hold | build times (ctx_key / ctx_win / cost_key / fw_win) | max binding UPDATE blocked |
|---|---|---|---|---|
| 100k | 702 ms | 534 ms | 160 / 117 / 152 / 96 ms | 1193 ms |
| 300k idle (earlier run) | - | 2640 ms | 890 / 710 / 620 / 420 ms | - |
| 600k | 397 ms | 3374 ms | 1100 / 938 / 626 / 701 ms | 3783 ms |

Throughput 114-187k rows/s, so the SHARE lock exceeds the 1 s ingest acquire timeout from about 115-180k rows and the
5 s `lock_timeout` from about 570-890k rows. Operators: use the plain build up to ~100k rows in a quiet window; beyond
that run it off-hours or in a maintenance pause of ingest. The 300k writer run aborted twice with a deadlock between the
bench's own binding and refresher writers (a bench artifact on identical row ranges, not the build); the 300k figure is
the idle run.

## Limits of this run

Single Docker VM, one data shape, cold = server restart only, one run per cold cell; 90 d `cost` p50 7.3 s means every sort except the default one
is slow, until the reader statements stop scanning `session_dims`.

## EPMCDME-17051: /users, /user-charts and /cost on 600K sessions (Task 10, one run each)

Scratch DB `127.0.0.1:55420/scratch`, 600,014 sessions, about 8,000 users, window 90 days pinned at 2026-11-10T17:27:25 UTC,
pool 8, `work_mem` 32 MB, `temp_file_limit` -1, Docker VM (noisy; one run per cell, `--runs 1`). Recordings are under
`results/` (untracked): `17051-baseline-600k`, `17051-new-600k`, `17051-explain-600k`, `17051-lag-600k`, `17051-load-600k`.
The pre-change handlers were recorded from commit `5f95cd6e23` in a detached worktree outside the repo
(`run_users_cost_bench.py baseline --days 90 --runs 1`).

### Wall time against the baseline (criterion 14: wall next to each statement)

Statements and handlers run with the 30 s limit lifted (`BENCH_STATEMENT_TIMEOUT_MS=900000`) so the times can be read.

| request | baseline wall | baseline statements (sum, max) | new wall (join form) | new statements (sum, max) | new wall before the join form |
|---|---|---|---|---|---|
| /users | 511.8 s | 707.5 s, 128.8 s (10) | 59.1 s | 59.0 s, 23.6 s (10) | 86.4 s |
| /user-charts | 510.6 s | 901.5 s, 161.1 s (10) | 195.0 s | 193.2 s, 58.7 s (10) | 237.8 s |
| /cost | 379.8 s | 192.4 s, 44.4 s (6) | 85.1 s | 84.6 s, 31.6 s (8) | 110.8 s |

(The baseline statements overlap on several connections, so their sum exceeds the wall time. The new handlers run one
statement at a time on one connection, so wall is about the sum; the rest is Python: row build under 2 s.)
Per statement (ms) of the new code, in call order:
users `24.8, 32.7, 4050, 51962 -> 23.6 s after the join form, 6545, 2846, 9848, 3292, 1761, 5950`;
user-charts `.., 2.8 s, 29.0 s (money page), 6.5 s, 20.7 s, 58.7 s, 15.9 s, 2.5 s, 57.1 s`;
cost `.., 3.4 s, 31.6 s (money), 19.4 s, 2.9 s, 9.3 s, 18.0 s` (full lists in `timings.json`).
Output parity at 600K (`compare`, criteria 6-7): users 38,038 nodes, user_charts 3,127,274, cost 357,857, all equal,
0 tolerated near-tie moves, zero-rounding sessions 0 / 0, both before and after the join form.

### EXPLAIN (ANALYZE, BUFFERS) of the money statements (`explain`)

| statement | before the join form | after the join form |
|---|---|---|
| Cost money (`get_cost_money`) | 53.2 s, temp read/written 362,759 / 200,939 blocks | 46.1 s, 370,727 / 208,579 blocks |
| Users money page (`get_users_money_page(50, 0)`) | 41.6 s, 308,975 / 168,041 blocks | 25.2 s, 316,943 / 175,683 blocks |

`pg_stat_database.temp_files` 269 -> 298 (+29 files, +2.9 GB) across the two EXPLAINs; `temp_file_limit` = -1 (no limit),
`work_mem` = 32 MB. Spills: ten `Sort Method: external merge` nodes of 69-147 MB (one per grouping arm over the 799K
priced groups) and a hash batch. The sequential scans of `cost_daily` are the `EXISTS (SELECT 1 FROM cost_daily ...)` of
`has_cost_daily` (all 840K rows, about 0.3 s in total); the token read probes the per-partition `session_id` indexes.
The time is in the 799K-row priced set (token read, `session_dims` joins, merge join with the rates), then one sort of
those rows per grouping arm. No missing index shows in the plan (see the report for the verdict).

Token read, `IN (SELECT session_id FROM sel)` form against `sel` joined (count and token sums identical): 16.6 / 16.7 s
against 4.5 / 4.2 s (first measurement 18.0 / 19.4 s against 5.2 / 4.9 s). The join form was faster, so
`priced_groups_cte` now builds it (`grouped_cost_sql.py`); `model_token_groups` is unchanged.

### Event loop lag during one /user-charts (max `asyncio.sleep(0.01)` overshoot, bound 200 ms)

| | max overshoot | where |
|---|---|---|
| Before any change | 5,901 ms | row build (about 1.5 s) and `_respond` (4.4 s, body 49.6 MB) |
| Row build and `_respond` (body and ETag) in `asyncio.to_thread` | 973 ms (an earlier run 1,363 ms) | the 40,015-row money-page result at 46 s and the 374,121-row daily-activity result at 222-228 s, both converted by the reader's `_q` on the loop, plus the build thread competing for the GIL |

Bound 200 ms: FAIL. What remains is outside the planned fix (the reader's row normalisation on the loop).

### k = 3 (`load --k 3`: 3 x (/users + /user-charts) + 3 x /cost, ingest stand-in at 50 sessions/s, two background jobs)

Run with the 30 s statement limit lifted, pool 8, ingest acquire timeout 1 s; phase A is the same background without any
dashboard request (180 s), phase B one round of nine requests.

| check | result |
|---|---|
| ingest 503 | phase A 0 of 1,848 probes; phase B 381 of 1,519: FAIL |
| dashboard 503 / 504 | 4 of 9 returned 504 at the 540 s budget (three /user-charts, one /cost): FAIL |
| every request no slower than baseline | the 200s took 425.0 s (/users, baseline 511.8), 436.4 and 436.6 s (/cost, baseline 379.8): FAIL for /cost |
| ingest p99 against no-dashboard | 1,000 ms against 33.5 ms, +966.5 ms (bound +250 ms): FAIL |
| peak pool connections per request | 1 for all nine: pass |

Why: nine requests hold nine connections for minutes against a pool of 8 with two held by the background jobs part of
the time. The last /cost waited 424.7 s for a connection (acquire wait) and then ran out of its budget; ingest probes
find the pool full. This is the pool decision of spec AR-5; no limit was added.

## Follow-up A (EPMCDME-17051): pre-aggregated grouped money and join-scoped fact reads

Code: e6308979d5 (grouped_cost_sql builds small cells once, every arm sums the cells; `priced_groups` and `gc` unchanged) and 2c96843a9a (the user-charts fact reads scope to `sel` by join instead of `IN (SELECT ...)`). The host was heavily loaded during these runs (load average about 15, another agent running benches), so absolute times are 1.5-2x noisy; read the plan figures, not only the wall.

Parity
- 300K (127.0.0.1:55421, 1 run, 30 s limit lifted): all equal for users (34,025 nodes), user_charts (1,949,071) and cost (330,686), 0 tolerated moves, zero-rounding 0/0. Wall: users 41.3 s, user_charts 33.7 s, cost 41.9 s.
- 600K (55420): the compare reports DIFFERENT (200 listed differences per endpoint), but the DB no longer equals the baseline: it holds 438 more sessions in the window than when 17051-baseline-600k was recorded (leftover test rows such as `edge17051` sessions and model `m`, e.g. cost_by_model api_based sonnet 149,904 vs 149,466 sessions). An old-code re-recording on the same DB state could not be completed (the background runs were killed three times). So 600K parity is not established by this run.

600K statement times (30 s limit lifted, loaded host; the run with the shipped 30 s limit aborted with QueryCanceledError on the first statement over it)
- Wall: users 125.2 s, user_charts 174.2 s, cost 109.8 s (task 10: 59.1 / 195.0 / 85.1 s on a quieter host).
- Money statements: Cost money 33.5 s, Users money page 33.3 s, User-charts money page 44.3 s; user_charts fact reads 24.6, 26.9, 26.5 s (were 58.7 and 57.1 s).

EXPLAIN (ANALYZE, BUFFERS), 600K
- Cost money 56.3 s (was 46.1 s), Users money page 32.5 s (was 25.2 s) on the loaded host. temp_files +28 / temp_bytes +2.2 GB for the two (was +29 / +2.9 GB). The ten per-arm external sorts are gone: the aggregation phase after `priced_groups` is about 13 s (two hash aggregates, 81 and 86 MB spilled, plus two sorts) instead of about 24 s; each arm now takes 0.1-0.5 s.
- What remains is the build of `priced_groups` itself (22 s on the quiet run, 39 s here): token-group hash aggregate with 21 batches, a 153 MB external sort for the merge join with `sel`, and the CTE tuplestore. Its internals are outside this change; the grouped statements cannot fall below that.

Fact reads (EXPLAIN, same loaded host, before -> after the join form): get_developer_session_facts 95.1 -> 38.2 s, get_users 41.0 -> 23.7 s, get_lines_by_user 30.9 -> 10.1 s, get_users_daily_activity about 85 -> about 55 s (still over 30 s: the five-table UNION of (session, day) pairs sorts 2.4M rows).

## EPMCDME-17051 follow-up C: ANALYZE, work_mem, k=3 with the per-pod cap (600K)

- Statistics: 271 of 279 tables in schema codemie_analytics were never analyzed (the 600K DB was loaded without ANALYZE: a test-database artifact; production is an operator matter). After a database-wide ANALYZE: Cost money 50.0 s (56.3 before), Users money page 27.4 s (32.5), temp +2.18 GB. The 40,000-vs-837,487 estimate at the token-group HashAggregate and the 125M Merge Join estimate remain, the plan is unchanged. Not a statistics problem.
- work_mem (SET LOCAL, explain only, not shipped): 128 MB gives Cost money 57.7 s, Users 28.2 s (temp 1.54 GB, one external sort left); 256 MB gives 53.9 s and 41.5 s (temp 0.69 GB, no external sort). Spill goes down, time does not: the statements are compute-bound. Container memory 1.6 -> 3.1 / 3.3 GiB for two statements; no setting added.
- k=3 (`load --k 3`, shipped 30 s statement limit, cap = pool-2 = 6): FAIL. All 9 dashboard requests 504 (first statements cancelled at 30.3 s; the queued Cost requests failed at 78.6 s). Ingest 503: 0/1,909 alone, 10/752 with dashboards; ingest p99 17.9 ms -> 1,000 ms (+982 ms, bound +250). Peak pool connections 8 of 8: six dashboard slots plus two background jobs, so pool-2 is too many slots; pool-4 would keep two for ingest.
- Results (untracked): `17051-explain-600k-fu-c-{analyzed,wm128,wm256}`, `17051-load-600k-fu-c`.

## Spike: stored token groups (EPMCDME-17051, 2026-11-11)

Question: is a stored tokens-only table `session_model_groups` plus live pricing at read (same `cost_terms()`, `cache_read_term()`, `unpriced_test()`, `RATES_JOIN`, clamp and `round(_, 9)`) fast enough at 600K sessions? Scratch DB 127.0.0.1:55420 (analyzed since follow-up C), 90-day window pinned to the baseline `meta.json` end (`2026-11-10T17:27:25`), `work_mem` 32 MB, `maintenance_work_mem` 256 MB, one statement at a time. The table was a scratch table `spike_session_model_groups` (PK `(session_id, model_name)`, no other index), dropped at the end; no migration, no product code touched. The throwaway script is `scripts/cli_analytics_bench/spike_stored_groups.py` (uncommitted).

Build (all 650,291 sessions of `session_dims`, from `model_token_groups(normalized=True)` in the join form plus `has_cost_daily` as the correlated EXISTS)
- INSERT 30.4 s; VACUUM ANALYZE 0.6 s. 898,387 rows for 650,281 sessions; 18,173 rows without `cost_daily` (subagent totals only).
- Size: heap 153 MB, PK 103 MB, 257 MB in all (the plan assumed about 250 MB: confirmed). Longest `session_id + model_name` key 76 bytes (longest model name 44 chars), far under the btree entry limit (about 2.7 KB); see risks in the report.
- Window rows: 801,865 groups for the 568,831 sessions of `sel` (784,571 with `cost_daily`).

(a) Scan of the stored rows for the selected sessions (`sel` with developer, joined)
- `sel` alone: 7.8 / 8.2 / 8.5 s (EXPLAIN 8.8 s). It is a fixed cost of every variant, live or stored (the `_admitted_arms` arm for "activity in window" probes 51,449 sessions against eight partitioned tables, 7.3 s).
- `sel` + stored scan, wall: 13.6 / 15.3 s (join), 14.1 / 13.9 s (`IN`); EXPLAIN 10.8 s; with `sel` materialized first 9.1 / 10.2 / 11.3 s. So the stored rows themselves cost about 1 to 3 s (seq scan 0.3 s, hash 0.6 s). Plan said 3.2 s for the scan: it did not include `sel`; with `sel` it is 11 to 15 s.

(b) Pricing at read (rates join, cost, cache_read_cost, unpriced) over those rows
- Whole `priced_groups` from the stored source (with `session_dims` join, funding route, personal_billed): 13.5 / 15.1 s wall, EXPLAIN 15.0 s. The same CTE with the pricing expressions and the rates join removed: 11.7 / 11.6 / 11.4 s; with pricing 13.0 / 14.7 / 12.5 s. Live pricing adds 1.1 to 3.3 s, median 1.4 s (the hash left join with the 9-row rates array 1.7 s in EXPLAIN). Rule (more than about 3 s over the pure scan -> store cost too): not met, tokens-only.
- For reference, today's live `priced_groups` alone (same session, same host): 34.3 s.

(c) Full statements from the stored source (`priced_groups` replaced in a copy of the statement text), 600K, `work_mem` 32 MB
| statement | live (current code) | stored, cells (follow-up A) | stored, plain per-arm sorts |
|---|---|---|---|
| Cost money (9 groupings) | 33.3 s | 22.9 / 24.2 s (EXPLAIN 22.6 / 24.8) | 34.5 s |
| Users money page (limit 50) | 29.7 s | 21.8 / 21.8 s (EXPLAIN 21.9 / 21.6) | 26.3 s |
| User-charts money (users page, no limit, 40,017 rows) | not run | 20.3 / 22.4 s | 28.3 s |

Where the time goes in the Cost money statement from the stored source: `sel` about 8 to 9 s, stored read plus `session_dims` join plus pricing about 6 s, the cells aggregation and nine arms about 7.6 s (users page 6.9 s). The plain per-arm form is 4 to 12 s slower than the cells form.

Correctness: the stored cells form and the stored plain form against the live statement, all rows (Cost 40,053, Users page 255 rows): 0 mismatches in cost, cache_read_cost, session_count, unpriced_models and cost_incomplete; maximum absolute difference 0 (tolerance 1e-9). Sums of cost per grouping equal in all nine groupings (total 783,316.120413 USD). Cells vs plain on the users page without limit (40,017 rows): equal.

Memory: container peak 1.84 GiB (1.55 GiB idle) during the cells runs, sampled every 3 s; no spill beyond what the live statements show. Container limit 7.75 GiB.

Decision: tokens-only. Projected per statement against the 30 s limit (stored rows, cells aggregation, no live arm): Cost money 23 to 25 s, Users money page 22 s, User-charts money 20 to 22 s; all under 30 s with 5 to 8 s of margin, of which about 8 s is `sel`. Not covered: the user-charts fact reads (25 to 55 s, a separate ticket), the live arm for unstamped sessions, and k = 3 concurrency (three concurrent statements at 22 to 25 s each on this host were not run).

## Stored model groups (EPMCDME-17051)

The final code: table `session_model_groups` (tokens only), `model_groups_rev` marker, separate switch, the three requests' money statements read the stored groups (`priced_groups_cte(stored=True)`, live arm for `$stale`), per-pod dashboard cap = pool - 4 (4 of 8). Results are untracked under `results/17051-*-stored*`. The host was busy during the 600K runs (load average 11 to 12, the dev application container at about 35% CPU), so wall times are noisy; the EXPLAIN numbers are the cleaner reading of the statements.

Parity (300K DB, 300,019 sessions, window pinned to the baseline end `2026-11-10T15:31:28`, groups switch on, table fully backfilled)
- Stored against live on the same data (live recorded with `disable-groups`, then `enable-groups`): /users, /user-charts and /cost all equal (34,028, 1,949,131 and 330,750 nodes, 0 tolerated near-tie moves). This is the parity result.
- Stored against the original `17051-baseline`: 9, 10 and 16 differences, all from 5 `test-sortkeys-3cae0ee9f562-*` sessions (user `sortkeys-3cae0ee9f562@example.com`, model `m`) that the OOM-killed test runs left in the DB after the baseline was recorded (3 more sessions in the window, one more user, one more unpriced model). Not a code difference.
- 300K timings, one run each (stored / live, same data): /users 20.9 / 17.5 s, /user-charts 33.4 / 36.1 s, /cost 29.1 / 36.5 s, limit lifted. At 300K the gain is small; the groups table was already filled by earlier runs, so no 300K backfill duration was measured.

Backfill on the 600K DB (`sort_keys_cli backfill-groups --batch 500 --pause-ms 50`, `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS=120000`): 644,291 pending sessions in 361 s, about 1,780 sessions/s, no OOM. Result 898,387 rows, 238 MB with the primary key (`VACUUM (ANALYZE)` afterwards). Container 1.13 GiB at 90 s into the run.

600K statements, `explain` (limit lifted; `results/17051-explain-600k-stored`)
| statement | before (live) | spike projection | final code |
|---|---|---|---|
| Cost money | 33 to 57 s | 23 to 25 s | 26.8 s |
| Users money page | 25 to 42 s | 22 s | 20.2 s |
| Token read (join form) | n/a | n/a | 6.6 s (the `IN (SELECT ...)` form 21 to 28 s) |

Both are under the 30 s limit, with 3 s of margin for Cost. Where the time goes (Cost): `sel` 3 to 5 s inside the plan, the stored read, join and pricing about 7 s, the cells aggregation 7 to 10 s. The planner still seq-scans `cost_daily` and `session_usage` partitions in both statements (cause not checked; the live arm should be empty on a fully backfilled DB).

600K requests, `compare` against `17051-baseline-600k`, limit lifted, handler wall (statements sum)
| request | previous (join form) | run 1 | runs 2 (r2, two runs) |
|---|---|---|---|
| /users | 59.1 s | 124.0 s | 96.9 / 95.8 s |
| /user-charts | 195.0 s | 150.6 s | 138.9 / 136.6 s |
| /cost | 85.1 s | 113.6 s | 103.4 / 104.6 s |
In these runs the money statement was 22.3 s (Users), 28.1 s (Cost) and 22 to 30 s (User-charts money); every other statement of the three requests (the fact reads, each re-reading `sel`) took 8 to 28 s. /users and /cost are slower than the earlier join-form figures, which I cannot attribute to the code: the non-money statements are unchanged and the host load average was 11 to 12. This needs a re-run on a quiet host before the numbers are quoted. The differences against the 600K baseline are expected (438 more sessions, see above) and were not analysed; parity is judged at 300K.

k = 3 (`load --k 3`, shipped 30 s statement limit, cap pool - 4 = 4, `results/17051-load-600k-stored`): still FAIL. All nine dashboard requests answer 504 (first statements cancelled at 30.3 s, the queued ones at 83.7 s and 120.5 s); wall 42.6 s for the first four, 83.7 s, 120.5 s. Peak 1 connection per request. Ingest improved: 0 of 1,282 probes got 503 with dashboards (previous run with cap pool - 2: 10 of 752); ingest p99 12.8 ms alone, 66.1 ms with dashboards (+53.3 ms, bound +250 ms; previous 1,000 ms, +982 ms). Container memory peak 2.35 GiB of 7.75 GiB. No `signal 9` in the container log.

Still failing against the spec bounds
- k = 3: every dashboard request still times out. With four heavy statements running at once each takes over 30 s on this host; one money statement alone is 20 to 27 s.
- The whole /user-charts request (about 140 to 150 s) and the whole /users and /cost requests (about 100 s) are far over 30 s per request, but each statement is limited, not the request; the fact reads of /user-charts and the repeated `sel` per statement (8 to 12 s each) are not covered by this ticket.
- Cost money has about 3 s of margin to the 30 s statement limit at 600K.

Out of scope here: the user-charts fact reads (25 to 55 s per statement), event-loop lag during /user-charts, the repeated `sel` evaluation in every statement, a per-user-day rollup (the next step if the margin is not enough).

## Shared `sel` temp table per dashboard request (EPMCDME-17051)

Change: `/cost`, `/users` and `/user-charts` call `materialize_sel(f)` first. The window's `sel` (with the developer column and the stored flag columns) is built once into a temp table `request_sel_<uuid>` of the request's held connection and ANALYZEd; every later statement of the request (models, money, KPIs, class lists, per-user facts, lines, last active, daily activity) reads it instead of re-evaluating the admission arms. The builders and the output columns of `priced_groups` and `gc` are unchanged. `bounded()` drops the table before the connection goes back (also after an error or a cancel; checked live). For the measurement I stopped the dev application container (`codemie-dev-codemie-1`, started again afterwards); the host load average stayed at 10 to 14 anyway (other processes), so the times are still not quiet-host figures.

Parity (300K DB, pinned window `2026-11-10T15:31:28`, stored groups on, against the live-form recording `17051-live-300k-same-data`): /users, /user-charts and /cost all equal (34,028, 1,949,131 and 330,750 nodes). 300K requests, one run: /users 30.7 s, /user-charts 41.1 s, /cost 22.3 s; no statement over 14 s.

600K statements, `explain` (limit lifted, `sel` in the temp table; `results/17051-explain-600k-sel`)
| statement | stored groups (before this change) | with the shared `sel` |
|---|---|---|
| temp table build (once per request) | n/a | 5.3 s |
| Cost money | 26.8 s | 17.7 s |
| Users money page | 20.2 s | 15.5 s |

600K requests, `compare` against `17051-baseline-600k`, limit lifted, one run (`results/17051-new-sel-600k`); statements in the order run, seconds (the temp table build is not in the list)
| request | before (stored groups only, runs 2) | now | statements now |
|---|---|---|---|
| /users | 96.9 / 95.8 s | 46.5 s | 1.6, 12.5 (money page), 3.8, 1.3, 6.4, 1.7, 2.0, 3.2 |
| /user-charts | 138.9 / 136.6 s | 68.3 s | 1.3, 12.4 (money), 4.6, 8.1, 13.0, 3.9, 2.3, 10.0 |
| /cost | 103.4 / 104.6 s | 47.7 s | 0.9, 15.9 (money), 5.1, 2.0, 7.2, 5.2 |
Every statement, including the daily-activity read and the other user-charts fact reads, is under 16 s alone, so none exceeds the 30 s limit on its own; no EXPLAIN of the daily-activity read was needed. The differences against the 600K baseline are the 438 extra sessions, as before.

k = 3 (`load --k 3`, shipped 30 s statement limit, `results/17051-load-600k-sel`), cap pool - 4 = 4: still FAIL. 1 of 9 requests answers 200 (cost #9, 156.4 s, run alone in the last wave); 8 answer 504 (the first four after 46.7 s, the next four after 90.7 s). Ingest: 0 of 1,607 probes got 503; p99 18.4 ms alone, 52.7 ms with dashboards (+34.3 ms, bound +250 ms; before the shared `sel`: +53.3 ms). Container memory peak 3.9 GiB of 7.75 GiB, no `signal 9`. With `CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT=2` (`results/17051-load-600k-sel-cap2`): 2 of 9 answer 200 (users #3 at 117 s, user_charts #4 at 164.6 s), 7 answer 504; ingest 0 of 2,470 503s, p99 59.4 ms. So the 504s are statements that stretch past 30 s when two to four heavy requests run at once on this host (about 17 s alone for the money statement), not a single slow statement.

Still failing: k = 3 (and even two concurrent dashboard requests) at 600K on this host; the money statement at 15 to 18 s alone has no room for 2x slowdown under the 30 s limit. Out of scope and not started: a per-user-day rollup. Not measured: which statement is cancelled in the concurrent runs (the load report keeps no statement list for failed requests).
