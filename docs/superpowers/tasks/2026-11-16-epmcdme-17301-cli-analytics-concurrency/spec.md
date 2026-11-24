# EPMCDME-17301 — CLI Analytics latency and pool contention under concurrent dashboard load

## Intent

The AI Attribution dashboard misses p95 ≤ 2 s on Overview and Repositories for one user. Under concurrent users every DB-backed tab degrades 4–13× (4–8 users in the UI stages; 4–13× is the k6 ramp to 18 VUs). Dashboard peaks can also starve telemetry ingest (1 s acquire timeout, then 503), because dashboards, ingest and five background jobs (rollup refresher, user rollup, its check, the warm-up, maintenance) share one asyncpg pool of 8 (`config.py:239`). This spec fixes the three causes the ticket confirms:
- **F1:** ungated connection fan-out.
- **F2:** whole-window ranking aggregation on every request.
- **F3:** one shared FIFO gate, no isolation and no metrics.

Paths: `H` = `service/analytics/handlers/cli_analytics_handler.py`, `R` = `repository/cli_analytics/postgres/reader.py`.

## Delivery

One MR with phased commits, in the order F1 → F3 → F2. Each phase leaves the system working and can be reviewed on its own. F1 can be cherry-picked early.

## Phase F1 — gate and bound every DB-reading dashboard method

- `get_session_detail` (H:1598-1617) runs its 8 statements one after another on one bounded connection.
- `get_activity` (H:1789-1808) enters the gate.
- `get_sessions` (H:1529-1594) enters the gate and keeps `bounded()`+`widened()`, so it uses at most 2 connections.
- `get_projects` is rebuilt in F2 on the SQL ranking; until then it runs under `dashboard_request()` on one `bounded()` reader with `materialize_sel` and sequential statements, following `get_tools` (H:1812-1822).
- `/projects` and Session Detail get the same timeout-to-504 mapping as the other tabs (`_statement_timeout_as_504`), so a budget expiry is not shown as "storage unavailable".
- Gate membership is defined in one table. Every DB-reading handler method must sit in the heavy gate or the light lane.

## Phase F3 — instrumentation, then isolation

**Metrics**, exported to Prometheus behind `PROMETHEUS_ENABLED` and also written as one log line per dashboard request:
- per request: gate wait, pool acquire wait, SQL time, statement count;
- per pod: pool in-use (split by caller: dashboard, job, ingest) and waiting gauges, ingest acquire-wait histogram, ingest 503 counter, rollup-refused counter by reason (rates, missing day, eligibility).
- Labels and the log line carry endpoint and lane only: no filter values, e-mails or search text.
- Per-request stats live in one place reachable by engine, reader and router (a request-scoped context opened by `dashboard_request`); statements are counted at one connection-level hook so rollup-served requests are counted too.

**Ingest reserve.** One cap inside `AnalyticsPgEngine.acquire` limits every non-ingest acquire to `pool_size − reserve` (6 of 8; reserve configurable, default 2). The only caller that bypasses it is the ingest transaction (`ingestor.py:548`), through an explicit named path. This covers every direct acquirer: heavy gate, light lane, `widen` (R:984), the rollup check (`runtime.py:161`), the warm-up job (`cli_analytics_jobs.py:71-92`), the builder and `advisory_lock`. The `timeout` a caller passes bounds the cap wait and the pool wait together; the 30 s default applies only when the caller passes none. So `widened()` under a full cap still gives up after about 0.5 s and runs on one connection.

**Sizes.** One settings function derives the sizes from `(pool, reserve)`: heavy gate 3, light lane 2, jobs 1 by default (measured at 600K sessions; decided in review: 3/2/1 over 2/2/2), each configurable, and a pool too small for one connection per lane keeps one slot per unset lane and shares the cap, and it rejects any configuration where heavy + light + jobs exceeds `pool − reserve`. It replaces the pool−4 formula (`settings.py:318-322`); `CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT` is clamped by it. The rollup jobs (check, warm-up, builder, refresher) draw only from their own jobs allowance inside the cap, so they cannot take the 2 connections reserved for ingest or the lanes' permits. The heavy gate's remaining purpose is bounding concurrent heavy statements; its docstring says so.

**Light lane.** Work bounded by the rollup or by one keyed session takes a separate light lane: `_rollup_overview`, `_rollup_cost`, `_rollup_users`, rollup-served rankings, Session Detail and Activity. It no longer queues behind heavy slots. A merge of resumed sessions that runs live, like a rollup refusal, re-enters the heavy gate.

**Fail-fast acquire.** A dashboard acquire, including the cap wait, is bounded by a new setting (default 30 s, a backstop to lower once the gate-wait metrics are read). On expiry the request raises a subtype of `RequestBudgetExceeded`: still a 504, with its own message naming the new setting and saying to retry, not the 540 s message. The slot wait before it is bounded only by the 540 s budget.

## Phase F2 — ranking rollup, price only the page, branches on demand

**One grouping.** `get_projects` is rebuilt on the SQL path: `get_project_page` for order and page names, then `get_repository_ranking(include_branches=True)` and `get_repository_facts` restricted to the page's projects, plus a project-level route/billed grouping. `_repository_rows` (H:1259-1300) and `_build_session_repository_rows` (H:352-397) are deleted after a parity test of per-group cost and funding buckets against the old output on the live seed. Rounding differences are accepted or fixed before the switch.

**Rollup.** The ranking rollup is a new shape (`SHAPE_RANKING`) of the user rollup, written by the same `build_day` under the same job, day state, rates version and kill switch (`CLI_ANALYTICS_USER_ROLLUP_ENABLED`). It adds no new job. Its own alembic revision follows `a1c1a0000018`. Already-built days are rebuilt once after deploy, newest first. Rows are keyed by start day × class pair × project × repository × branch and carry session count, turns, files, lines, tool counts, subscription/origin sets, spend and route/billed money per funding route under the rates version, and per-model spend flags. Values come from the live statements over each day's cohort, so rollup and live cannot disagree.

**Who it serves.** Overview top-10, Repositories ranking and `/projects` page, only when eligible under the existing rules (`user_rollup.py:15-37`): unrestricted scope, filters limited to `subscription_type` and `project_origin`, a window ending now, at most 2000 resumed sessions. The ranking rollup has its own eligibility predicate, started as a copy of `eligible()`. A request with a search never reads the rollup (search is not in the filter `eligible()` sees), and any cached ranking key includes page and per_page. When the rollup cannot answer, the reader's `rollup_*` method returns None and the request re-enters the heavy gate on the live path.

**Price only the page.** On the live path the rank key (cost per group) and the count/total parts are computed in SQL for every group, as today. Facts, buckets, funding costs and per-model detail are read and priced for the page's groups only. On the rollup path rank and totals come from stored money under the rates version.

**Branches on demand.** The branches call (`GET /branches`, the EPMCDME-17349 contract the UI calls) is keyed by repository: it returns the branches of one repository, scoped by the viewer-scope resolver as `/projects` (`routers/cli_analytics.py:456-488`). A project-keyed call is not built (decided in review). `/projects` keeps returning branches until the frontend switches over.

## Acceptance criteria

1. With a fake engine that counts concurrent `acquire()`, `get_projects`, `get_session_detail` and `get_activity` hold at most 1 connection, and `get_sessions` at most 2.
2. A table-driven test fails when any DB-reading handler method enters neither the heavy gate nor the light lane.
3. Live PostgreSQL, pool 8: 4 concurrent `/projects` + a Session Detail while every job holds its allowance, with an ingest probe running. Ingest acquire stays under its timeout and there are 0 ingest 503s.
4. Live: rollup-served rankings equal live rankings (counts, cost, order, ties, NULL project last) after each mutation type (new session, resumed session, rates change, late event), including filtered by subscription type and origin.
5. Live: rollup-served light requests complete while every heavy slot and every jobs permit is held.
6. Unit test on the cap: non-ingest acquires never exceed `pool_size − reserve`; jobs never exceed their allowance; the only caller that bypasses the cap is the ingest transaction; a configuration with heavy + light + jobs above the cap is rejected.
7. `widened()` under a full cap runs on one connection after about 0.5 s and does not hold-and-wait.
8. A dashboard acquire that waits longer than the configured timeout fails with the 504 subtype (own message), and the slot and cap are released. A dead store still reads as "storage unavailable".
9. Each metric listed in F3 is emitted (unit test with a registry); labels and the log line contain no filter values.
10. Live test on a seeded dataset at least as large as Preview (5,763 sessions, 1,237 projects): statement count per `/repositories` and `/projects` page 0 is pinned, and the `/projects` payload is not larger than today's. The bench reports page-0 server time against 2,000 ms and the rollup rebuild time after a rates change.
11. `/projects` response shape is unchanged, and its per-group cost and buckets match the old Python assembly (parity test). The branches call returns the branches of the repository it is asked for, equal to the ones `/projects` and Repositories return for that repository.
12. Branches call scoping (repository-keyed): the viewer scope applies as for `/projects`, so a project admin asking for a repository outside their admin projects gets no rows.
13. Ineligible ranking requests (project-admin scope, other filters) return the same results as before, on the gated live path. A request with a search never reads the rollup.
14. A test fails when a page-only part (facts, buckets, funding costs, per-model detail) is computed over the whole window.
15. Target, checked by the QA Preview re-run after deploy: p95 ≤ 2,000 ms per tab at 1 and at 8 concurrent users, and 0 ingest 503s. The Repositories tab's client time depends on the frontend follow-up (Non-goals).

## Non-goals

- Porting or merging EPMCDME-17052. Its branch is behind `main`; `main` already carries its ranking-in-SQL work. Whether its 10 commits hold anything not on `main` is noted, not pursued.
- A response cache on `/repositories` or `/projects`.
- Rollup coverage for project-admin scope, search, or user/developer filters.
- Removing branches from `/projects`, and any frontend change, including the branches-on-expand UI switch. This is a known frontend follow-up.
- A separate ingest pool; pool or PostgreSQL sizing changes (the ticket allows them only if the metrics point there).
- Timing fields in API responses; the API contract stays fixed.
- Running the Preview re-run or the `pg_stat_statements` load test (QA/ops).
- Reworking the Sessions paging and sort keys from EPMCDME-16427.

## Decisions

- Rollup serves the eligible case only (unrestricted scope, no search, subscription/origin filters), over also covering project-admin scope and search — user
- A branches-of-one-project call alongside unchanged `/projects`, over dropping branches from `/projects` now — user
- Ingest reserve of 2 inside the shared pool via a non-ingest cap of pool−2, over a separate ingest pool — user
- Rollup jobs get their own small cap inside the non-ingest cap, over sharing the cap with the dashboard lanes — user, answers AR-1
- The branches call stays keyed by repository (the contract the UI calls), over a project-keyed call; it enforces the same viewer scoping as `/projects` — user, answers AR-3
- The load target is 8 concurrent users, over an unstated load — user, answers AR-10
- The branches-on-expand UI switch is a known frontend follow-up, over doing it in this MR — user, answers AR-9
- `/projects` rebuilt on the SQL grouping with the Python assembly deleted, over keeping it or narrowing F2 — user, answers AR-2
- The ranking rollup is a new shape of the user rollup (one job, one kill switch), over a separate stack — user, answers AR-8
- Bounded dashboard acquire timeout as a setting defaulting to 30 s, over waiting out the 540 s budget — user
- Perf test pins statement count and payload with the time budget reported by the bench script, over a hard wall-clock assert — user
- No response cache on `/repositories` and `/projects`, over adding the 30 s cache — user
- The cap lives in `AnalyticsPgEngine.acquire` with ingest as the only bypass, over placing it at the gate and `advisory_lock`
- One timeout covers cap wait and pool wait, over a separate cap wait
- One settings function derives heavy, light and jobs sizes and rejects overflow, over pool−4 plus pool−reserve side by side
- The acquire timeout raises a 504 subtype with its own message, over reusing the 540 s budget message
- Session Detail and Activity in the light lane, over the heavy gate
- A rollup refusal or live resumed-session merge re-enters the heavy gate, over finishing in the light lane
- Session Detail statements run sequentially on one bounded connection, over a TaskGroup or `widened()`
- `get_sessions` gated but keeping `bounded()`+`widened()` without materializing, over converting it to `materialize_sel`
- Gate membership pinned by a table over heavy gate and light lane, over the exact-list assertion and its Sessions exclusion
- Live path computes rank key and totals for every group and prices the rest for the page only, over pricing only the page everywhere
- Metrics to Prometheus plus a per-request log line, over timing fields in responses
- Ranking rollup has its own eligibility predicate, over sharing `eligible()`

## Open risks

- The p95 target is confirmed only by the Preview re-run. The code provides the mechanisms and the pinned tests.
- The Repositories tab's client time (0.8–1.5 s after the 661 KB `/projects` response) stays until the frontend follow-up lands, so AC15 for that tab may still miss.
- Ineligible requests (project admins, search, user filters) stay live and can still be slow at production volume.
- The gate wait is bounded only by the 540 s budget; 30 s covers pool and cap waits only, and one acquire timeout cannot tell pool wait from connect.
- After a rates change, rankings fall back to the heavy live path until the rollup is rebuilt; already-built days also rebuild once after deploy.
- Grouped SQL pricing may differ by cents from the old per-session Python pricing; the parity test decides.
- Lane sizes, the ingest reserve of 2 and the jobs allowance are defaults until the F3 metrics are read on Preview.
