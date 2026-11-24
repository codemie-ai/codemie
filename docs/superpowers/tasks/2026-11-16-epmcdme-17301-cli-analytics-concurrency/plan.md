# EPMCDME-17301 CLI Analytics concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Bound dashboard DB fan-out, isolate light/heavy/ingest/job connections with metrics, and serve repository/project rankings from SQL plus a ranking rollup.

**Architecture:** F1 gates and bounds every reader method (pattern: `get_tools`). F3 adds a connection cap in `AnalyticsPgEngine.acquire`, a light lane, a jobs allowance and metrics. F2 rebuilds `/projects` on the SQL ranking and adds `SHAPE_RANKING` to the user rollup.

**Tech Stack:** Python 3.12, FastAPI, asyncpg, Alembic (cli_analytics), prometheus_client, pytest.

**Spec:** `docs/superpowers/tasks/2026-11-16-epmcdme-17301-cli-analytics-concurrency/spec.md`. Analysis: `technical-analysis.md` in the same dir.

Commit per task using the repository's existing convention. One MR, three phases (F1, F3, F2); each phase's last task ends the phase and leaves the system working. Paths: `H` = `src/codemie/service/analytics/handlers/cli_analytics_handler.py`, `R` = `src/codemie/repository/cli_analytics/postgres/reader.py`, tests under `tests/codemie/service/analytics/` and `tests/codemie/repository/cli_analytics/` (follow the existing sibling test files). New reader methods go on the port (`ports.py`); `test_ports.py` checks it.

## Global Constraints

- Logging with f-strings; the adapter never imports `codemie.service`; API contract unchanged (no timing fields); no response cache on `/repositories`, `/projects`.
- Ingest transaction (`ingestor.py:548`) is the only cap bypass. Sizes default: pool 8, reserve 2, heavy 2, light 2, jobs 2; acquire timeout 30 s.
- Metric labels and the log line: endpoint and lane only; no filter values, e-mails, search text.
- Rollup kill switch stays `CLI_ANALYTICS_USER_ROLLUP_ENABLED`; no new job.

## Review Focus

- Search request must never read the ranking rollup; ranking cache keys include page and per_page.
- Dead store still reads as "storage unavailable", not the new acquire-timeout 504.
- `widened()` under a full cap gives up after about 0.5 s (no hold-and-wait).
- Unattributed project and out-of-scope project on the branches call return no rows.
- NULL project sorts last and ties order identically on rollup and live.

---

## Phase F1 — gate and bound

### Task 1: Bound Session Detail, Activity, Sessions, Projects
**Files:** Modify `H:1374-1404`, `H:1598-1617`, `H:1789-1808`, `H:1529-1594`; Test `test_cli_analytics_handler.py`, `test_bounded_reader.py`.
- Consumes: `dashboard_request()`, `bounded()`, `widened()`, `materialize_sel`.
- Produces: all four methods under `dashboard_request()` (heavy gate for now).

Test-first: yes — with a counting fake engine, `get_projects`, `get_session_detail`, `get_activity` peak at 1 connection and `get_sessions` at 2 (parametrise next to `test_bounded_reader.py:309-353`); a statement timeout from `/projects` and Session Detail maps to 504 via `_statement_timeout_as_504`.

- [ ] Write the failing tests; run them.
- [ ] `get_session_detail`: replace the `asyncio.gather` of 8 with sequential calls on one `bounded()` reader. `get_activity` and `get_sessions` enter `dashboard_request()`. `get_projects` runs under `dashboard_request()` on one `bounded()` reader with `materialize_sel`, sequential, following `H:1812-1822`; wrap the router calls (`routers/cli_analytics.py` ≈813, ≈978) in `_statement_timeout_as_504`. Update existing stubs with `bounded_to_itself`.
- [ ] Run the touched test files; all pass.

### Task 2: Gate-membership table (ends F1)
**Files:** Modify `test_cli_analytics_dashboard_gate.py:167-194`; `H` (one `LANE_BY_METHOD` mapping of method name to `"heavy" | "light"`).
- Produces: `LANE_BY_METHOD: dict[str, str]`, consumed by Task 5.

Test-first: yes — a table-driven test that fails when any DB-reading handler method (found by scanning `self._repo.` use) is in neither lane; replaces `test_other_paths_do_not_use_the_gate` and its Sessions exclusion comment.

- [ ] Write the test and the table (all lanes `heavy` for now); run; pass.

## Phase F3 — metrics, cap, lanes, timeout

### Task 3: Request stats context and metrics
**Files:** Create `postgres/request_stats.py` (a `ContextVar` stats object: gate_wait, pool_wait, sql_time, statements); Modify `engine.py`, `H` (`dashboard_request` opens it and writes one log line), `routers/cli_analytics.py`, `configs/prometheus_config.py`; Test `test_request_stats.py`, `test_cli_analytics_metrics.py`.
- Produces: `request_stats.current() -> RequestStats | None`; Prometheus metrics: per-request gate-wait, pool-wait, SQL-time histograms and statement-count; per-pod in-use gauge by caller (`dashboard|job|ingest`), waiting gauge, ingest acquire-wait histogram, ingest 503 counter, `rollup_refused_total{reason=rates|missing_day|eligibility}`.

Test-first: yes — with a registry, every listed metric is emitted; a rollup-served request still counts statements; the log line and labels contain no filter value or e-mail.

- [ ] Write failing tests.
- [ ] Count statements and time at one connection-level hook installed at acquire (check the pinned asyncpg's query-logger support); write pool wait in `engine.acquire`; "waiting" gauge via a counter around acquire. Register under `PROMETHEUS_ENABLED`. Increment the refused counter where the reader's `rollup_*` methods return None (`user_rollup.py serve`).
- [ ] Run tests; pass.

### Task 4: Sizes function and ingest-reserve cap
**Files:** Modify `postgres/settings.py:318-322`, `engine.py:83-107`, `ingestor.py:548`, `runtime.py:161`, `cli_analytics_jobs.py:71-92`, `config.py:239-288`; Test `test_engine_cap.py`, `test_cli_analytics_dashboard_gate.py`.
- Produces: `lane_sizes(pool, reserve, ...) -> LaneSizes(heavy, light, jobs)` raising on `heavy+light+jobs > pool-reserve`; `AnalyticsPgEngine.acquire(timeout=None, *, caller="dashboard"|"job")`; `AnalyticsPgEngine.acquire_ingest(timeout)`; settings `CLI_ANALYTICS_PG_INGEST_RESERVE`, `..._HEAVY_SLOTS`, `..._LIGHT_SLOTS`, `..._JOBS_SLOTS`. `CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT` clamped by it.

Test-first: yes — non-ingest acquires never exceed `pool-reserve`; jobs never exceed their allowance; only `acquire_ingest` bypasses the cap; overflow config rejected; `widened()` under a full cap runs on one connection after about 0.5 s (`R:984`, no hold-and-wait); `timeout` bounds cap plus pool wait together, 30 s default only when none passed.

- [ ] Write failing tests.
- [ ] Implement one semaphore-based cap inside `acquire`; route `advisory_lock`, the rollup check, warm-up and the builder through `caller="job"`; ingest uses `acquire_ingest`.
- [ ] Run tests; pass.

### Task 5: Light lane and acquire-timeout subtype
**Files:** Modify `H:783-843` (`dashboard_slot`, `dashboard_request(lane=...)`, docstring), `H:1029,1052,1159` and the methods in `LANE_BY_METHOD`, `engine.py`, `routers/cli_analytics.py`, `config.py`; Test `test_cli_analytics_dashboard_gate.py`, `test_cli_analytics_acquire_timeout.py`.
- Consumes: `LaneSizes`, `LANE_BY_METHOD`. Produces: `DashboardAcquireTimeout(RequestBudgetExceeded)`, setting `CLI_ANALYTICS_DASHBOARD_ACQUIRE_TIMEOUT_S` (30).

Test-first: yes — light requests finish while every heavy slot and jobs permit is held; a rollup refusal or live resumed-session merge re-enters the heavy gate; an acquire waiting past the timeout raises the subtype, maps to 504 with its own message naming the setting, and slot and cap are released; a dead store still gives "storage unavailable".

- [ ] Write failing tests.
- [ ] Light lane: `_rollup_overview`, `_rollup_cost`, `_rollup_users`, rollup-served rankings, Session Detail, Activity. Update the `dashboard_slot` docstring (heavy gate bounds concurrent heavy statements). Update `LANE_BY_METHOD`.
- [ ] Add the live test: 4 concurrent `/projects` plus Session Detail with jobs holding their allowance and an ingest probe, 0 ingest 503s (live engine in `support/live_engine.py`). Run; pass. **Phase F3 ends.**

## Phase F2 — rollup, page pricing, branches

### Task 6: `/projects` on the SQL ranking
**Files:** Modify `H:1259-1404`, `H:352-397` (delete `_repository_rows` and `_build_session_repository_rows` after the parity test), `R:2059-2142`, `R:2720-2752`, `project_page_sql.py`, `ports.py`; Test `test_projects_parity.py`, `test_live_dashboard_aggregates.py`.

Test-first: yes — on the live seed, per-group cost and funding buckets from the new path equal the old Python assembly output (captured before deletion); a test fails if facts, buckets, funding costs or per-model detail are computed over the whole window (statement text restricted to the page's groups); response shape unchanged.

- [ ] Write the parity test first against the old code; record rounding differences, fix or accept.
- [ ] `get_projects`: `get_project_page`, then `get_repository_ranking(include_branches=True)` and `get_repository_facts` restricted to page projects, plus a project-level route/billed grouping. Rank key and totals stay in SQL for all groups; the rest priced for the page only. Delete the old assembly. Run; pass.

### Task 7: Ranking rollup shape
**Files:** Create `src/external/alembic_cli_analytics/versions/<rev>_ranking_rollup.py` (down_revision `a1c1a0000018`); Modify `user_rollup.py` (`SHAPE_RANKING`, `build_day` :603, `serve` :1236, a copy of `eligible()` as `ranking_eligible`), `R` (`rollup_repository_ranking`, `rollup_project_page` returning None to fall back), `ports.py`, `H` (serve with live fallback in the heavy gate); Test `test_live_ranking_rollup.py`.
- Row key: start day, class pair, project, repository, branch; carries counts, turns, files, lines, tool counts, subscription/origin sets, spend and route/billed money per funding route under the rates version, per-model spend flags.

Test-first: yes — live, rollup-served rankings equal live (counts, cost, order, ties, NULL project last) after new session, resumed session, rates change and late event, including filtered by subscription type and origin; a request with a search never reads the rollup; any cached ranking key includes page and per_page; ineligible scope or filters return the old results on the gated live path.

- [ ] Write the failing tests.
- [ ] Add the migration and the shape; rows come from the live statements over each day's cohort. Rebuild existing days once after deploy, newest first. Serve Overview top-10, Repositories and `/projects` page when eligible.
- [ ] Run; pass.

### Task 8: Branches-of-one-project call
**Files:** Modify `routers/cli_analytics.py` (new route beside ≈790, using the scope resolver at :456-488), `H` (`get_project_branches`), `ports.py`; Test `test_cli_analytics_router.py`, `test_cli_analytics_handler.py`.

Test-first: yes — returns the same branches `/projects` returns for that project; a project admin asking for a project outside their admin projects gets no rows; a non-super-admin asking for the unattributed project (`project_unattributed`) gets none.

- [ ] Write failing tests, then implement as Repositories filtered with `projects=[name]` and branches included. Run; pass.

### Task 9: Perf pin and bench (ends F2)
**Files:** Create `test_live_ranking_perf.py`; Modify `scripts/cli_analytics_bench/` (report page-0 server time for `/repositories`, `/projects` vs 2,000 ms, and rollup rebuild time after a rates change); Modify `.ai-run/guides/integration/cli-analytics-storage.md` (settings, lanes, cap, metrics, ranking shape).

Test-first: yes — on a seed of at least 5,763 sessions and 1,237 projects, statement count for `/repositories` and `/projects` page 0 equals a pinned number and the `/projects` payload is not larger than the pre-change baseline; time is reported, not asserted.

- [ ] Write the test and bench changes; run; pass; update the guide.

---

negative-constraints: honored — no response cache (Global Constraints, no task adds one); no frontend change or branch removal (Task 8 adds a call only); no separate ingest pool (Task 4 cap); no rollup for admin scope or search (Task 7); no new job (Task 7); no timing fields in responses (Task 3); Sessions paging untouched (Task 1 keeps `bounded()`+`widened()`); no 17052 port.
