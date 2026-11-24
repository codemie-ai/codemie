# Technical Research

**Task**: cli-analytics postgres connection-pool rollups
**Generated**: 2026-11-16
**Research path**: filesystem

---

## 1. Original Context

Jira bug EPMCDME-17301 "[CLI Analytics][Performance] Severe API latency and database contention under concurrent dashboard load" (Critical). The full ticket text (description + QA comment) is at /Users/yevhen_slyva/codemie-dev/codemie/docs/superpowers/tasks/2026-11-16-epmcdme-17301-cli-analytics-concurrency/ticket.local.txt — read it in full; it is the task_description verbatim. User request: "can we investigate a code and create spec/plan for this ticket?".
Summary: CLI Analytics (UI "AI Attribution") misses p95 ≤ 2 s on 7/8 tabs; Overview and Repositories fail for 1 user; all DB tabs degrade 4–13× at 4–8 concurrent users. Claimed root causes at main 66ed87f0e (current HEAD ed978d9b1): F1 ungated connection fan-out — get_projects (cli_analytics_handler.py:1374) / _repository_rows asyncio.gather of 6 statements (:1271), get_session_detail gather of 8 (:1598/1608), get_activity (:1789) outside dashboard_request(); pool of 8 (config.py:239), dashboard acquire has no timeout (engine.py:94); /tools fixed by EPMCDME-17051 as the reference pattern (bounded()/widened()). F2 full-window ranking aggregation in reader.get_repository_ranking (reader.py:2059) and get_project_page (reader.py:2720), no repository/project rollup, 30 s response cache. F3 shared dashboard_slot() semaphore (:783-798) limit pool−4=4 with no light/heavy isolation; no gate-wait / pool-wait metrics; ingest shares the pool (CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS 1 s → 503). Prioritized fixes F1→F2→F3 plus regression tests listed in the ticket. Also check related branch EPMCDME-17052 (local/remote) for overlap, the existing rollup pattern from EPMCDME-17051 (migrations, rollup tables, maintenance jobs), and the guide .ai-run/guides/integration/cli-analytics-storage.md. Verify every claim in the ticket against current code and note any that are wrong or stale.

---

## 2. Codebase Findings

Paths: `H` = `src/codemie/service/analytics/handlers/cli_analytics_handler.py`, `R` = `src/codemie/repository/cli_analytics/postgres/reader.py`. Line numbers on HEAD ed978d9b1; the branch `EPMCDME-17301_cli-analytics-concurrency` has no commits over `main`. Every ticket line number still matches on HEAD.

### Existing Implementations

**Purpose A: cap how many analytics connections one dashboard request and one pod use, so ingestion keeps connections.**
- `dashboard_slot()` `H:783-798`: per-event-loop `asyncio.Semaphore`, FIFO, limit `dashboard_concurrency_limit()` (`settings.py:318-322`): `CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT` when >0 (capped at pool size), else `max(1, pool−4)`. The docstring records the choice: "a light one can wait behind the heavy ones; a gate of its own would give up the pool-minus-four connections ingestion counts on".
- `dashboard_request()` `H:827-843`: `asyncio.timeout` budget (`CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S`, default 540 s, `config.py:269`) plus the slot. The slot wait counts against the budget and raises `RequestBudgetExceeded`. `one_request_budget` `H:809-823` shares one budget per handler method.
- `bounded()` `R:1021-1036`: holds one pool connection (`engine.acquire()` with no timeout) and runs the reader's statements one after another through `_ConnectionPair` (`R:956-1000`). `widened()` `R:1039-1049` adds a second connection only if one is free within `SECOND_CONNECTION_TIMEOUT_S = 0.5` (`R:953`). It is a no-op once `materialize_sel` has made a temp table, because a temp table is visible to its own connection only.
- `materialize_sel()` `R:1058-1084`: builds `sel` once into a TEMP table on the held connection. It only works on a bounded reader.
- Who enters the gate on HEAD (pinned by `test_other_paths_do_not_use_the_gate`, `tests/.../handlers/test_cli_analytics_dashboard_gate.py:167-194`): `_rollup_cost`, `_rollup_overview`, `_rollup_users`, `_users_live`, `get_cost`, `get_efficiency`, `get_overview`, `get_repositories`, `get_tools`. The test comment says "not Sessions or a session detail".
- Outside the gate:
  - `get_projects` `H:1374-1404`: no bounded reader.
  - `get_session_detail` `H:1598-1617`: `asyncio.gather` of 8 statements on `self._repo`.
  - `get_activity` `H:1789-1808`: one statement.
  - `get_sessions` `H:1529-1594`: `bounded()` + `widened()`, so at most 2 connections, but no gate.
- Per-statement acquire: `_q` `R:1146-1151`, `_priced_q` `R:1112`, `get_cost_models` `R:2660`, `get_cost_page` `R:2701` and `get_framework_page` `R:2780` each call `self._engine.acquire()`. On the unbounded reader that is a fresh pool connection per statement.
- Ingest: `ingestor.py:548` `engine.acquire(timeout=settings.ingest_acquire_timeout_s)` (1 s, `config.py:244`). Its 503 path is described in the guide (PostgreSQL Settings).
- Background jobs on the same pool (`runtime.py:134-150`): `cli_analytics_rollup_refresh` (30 s), `cli_analytics_maintenance` (60 min), `cli_analytics_user_rollup` and `cli_analytics_user_rollup_check` (30 s each). Each takes one connection through `advisory_lock` (`engine.py:112-129`), which acquires before trying the lock. Up to 4 connections can be held by jobs at once.

**Purpose B: rank repositories and projects for a window without grouping every session per request.**
- `get_repository_ranking` `R:2059-2142`: one `_priced_q` statement. It builds `sel` (CTE), `priced_groups_cte()`, `project_counts`/`top_project`, `grp`, `money` (`grouped_cost_sql`), and `row_number()` over every group before cutting the page. Rules:
  - order: `session_count DESC`, cost DESC, repository, project (`C` collation);
  - `include_branches` groups by (project, repository, branch) with no paging;
  - search is ILIKE on the repository.
- `get_repositories` `H:1302-1372`: gated and bounded. It runs `get_cost_models`, then the ranking, then `get_repository_facts` (page groups only, `R:2173`), then `get_repository_buckets` (page only, `R:2144`). These are 3–4 sequential statements, and `materialize_sel` is not called.
- `get_project_page` `R:2720-2752` and `project_page_sql.py:41-84`: one `_q` statement. It runs `sel`, then the spend-only `(session, model)` token groups of the whole window (`g AS MATERIALIZED`), then the per-session cost key, then a sum per project, ordered with NULL project last, then LIMIT/OFFSET. It returns the page's session ids.
- `get_projects` then reads, all on the unbounded reader:
  1. `get_cost_models`;
  2. `get_project_page`;
  3. `_repository_rows` (`H:1259-1300`) over the page's sessions with `include_branches=True`, which gathers 6 statements (`H:1271-1278`);
  4. `get_session_funding_costs(window)` (`R:1470`, whole window or the search match).

  That is 9 statements, and each builds its own `sel`.
- Existing rollups (EPMCDME-17051), all keyed by session **start day** and never by repository, branch or project:
  - `user_day_rollup` and `user_day_money_rollup` (`a1c1a0000014`);
  - `user_day_model_rollup` (`a1c1a0000015`);
  - `user_day_overview_rollup`, `_lines` and `_models` (`a1c1a0000017`);
  - day state `user_rollup_day_state`.

  The builder is `user_rollup.py`: `build_day` :603, `refresh` :688, `eligible` :749, `serve` :1236. Rules (module docstring :15-37 and the guide's "User Start-Day Rollup"):
  - scope `ROLLUP_SCOPE` (unrestricted, `exclude` mode) only;
  - filters limited to `subscription_type` and `project_origin`;
  - the window starts at midnight UTC and ends within `LIVE_END_SECONDS = 120` of now;
  - at most `MAX_RESUMED = 2000` resumed sessions, which are read live and combined;
  - rows are computed by running the live reader's statements over one day's cohort (`gather_cohort`), so rollup and live values cannot disagree.
- Searched for a repository/project rollup (grep for `repository`/`project` in the `alembic_cli_analytics/versions` table definitions and `user_rollup.py` keys): none found.

**Purpose C: reuse an answer across identical requests.**
- `dashboard_cache.py`: `cached_dashboard` wraps only `/cost`, `/users` and `/user-charts` (`routers/cli_analytics.py`, calls at ≈657, 697, 736). `RankingCache` (`dashboard_cache.py:184-232`) holds the users ranking and the overview and cost rollup answers, including `_ROLLUP_REFUSED`.
- `/repositories` and `/projects` call the handler directly (router ≈790 and ≈813) and are not cached.

**Purpose D: observe gate wait, pool wait and SQL time.**
- `execution_time_ms` is set in the router (`routers/cli_analytics.py:499`, `:990`). It measures the whole request.
- `register_prometheus_db_pool_metrics` (`configs/prometheus_config.py:74-98`) gauges the application SQLAlchemy pools only (size, checked out, overflow).
- Grep for `get_size`/`get_idle_size` or any metric in `engine.py` and the handler: nothing for the analytics asyncpg pool, the gate or ingest acquire waits.

**Other touched code**
- `engine.py:83-107` `AnalyticsPgEngine.acquire(timeout=None)`: the asyncpg pool, `min_size 0`, `max_size = pool_size` (`CLI_ANALYTICS_PG_POOL_SIZE`, default 8, `config.py:239`).
- `runtime.py`, `cli_analytics_jobs.py:95-127`: APScheduler, `max_instances=1`.

### Decisions Already in the Code
- One FIFO gate shared by light (rollup) and heavy (live) requests: `H:785-786`. Alternative: a separate light lane. Recorded reason: a separate gate would use the connections reserved for ingest.
- Sessions and Session Detail are deliberately left out of the gate: test comment `test_cli_analytics_dashboard_gate.py:181-183`. Reason: none recorded beyond that comment. Sessions instead bounds itself to ≤2 connections (`H:1544-1555`).
- A second connection is only taken if one is free within 0.5 s, to avoid hold-and-wait deadlock (`R:951-953`). This is the convention for "at most 2 at a time".
- Statements on a held connection run one after another. `_read_details` uses a `TaskGroup`, not `gather`, so no connection is released while a sibling is still running (`H:1506-1527`). That is the convention for concurrent statements on a bounded reader.
- `get_tools` is the reference for converting a gather: one `bounded()`, `materialize_sel`, then sequential calls (`H:1812-1822`, comment "six statements at once took six of the pool's eight connections").
- Rollups are keyed by session start day, a session sits in one row, and the value is recomputed via the live statements (`user_rollup.py:17-24`). Repository and project rankings need grouping by repository/branch/project, a dimension the rollups do not store.
- The pricing of rankings uses catalog rates per window (`rates_params`, `get_cost_models`), and the rollups store money under a rates version (`user_rollup_day_state`). A rates change makes every rollup day stale (guide, "The job").
- No dashboard acquire timeout: `bounded()` calls `engine.acquire()` without one. Only the request budget (540 s) bounds a slot wait. The pool wait of a gated request is not bounded by the slot.

### Task Statements Checked Against the Code
- "get_projects (:1374) never enters dashboard_request() and holds no connection": **holds** `H:1374-1404`. It also runs `get_cost_models`, `get_project_page` and `get_session_funding_costs` ungated, which makes 9 statements in total.
- "_repository_rows gathers 6 statements (:1271-1278) on self._repo, each acquiring its own connection (R:1146-1151)": **holds**. Some of those methods go through `_priced_q` or their own `acquire`, not `_q`; the effect is the same.
- "get_session_detail (:1608) gathers 8": **holds** `H:1608-1617`.
- "get_activity (:1789) outside the gate": **holds**. It is a single statement (`R:2889-2895`).
- "pool of 8 (config.py:239)": **holds**.
- "dashboard acquire has no timeout (engine.py:94)": **holds** for the pool acquire (`timeout=None` from `bounded()`). The slot wait is bounded by the 540 s budget.
- "Two concurrent /projects can ask for 12 connections, none counted against the gate": **holds**.
- "/tools fixed by 17051 (:1813-1815)": **holds**.
- "get_repository_ranking (R:2059) / get_project_page (R:2720) group every session of the window before cutting a page": **holds** (`R:2123-2131`; `project_page_sql.py:63-74`).
- "No repository or project rollup exists": **holds**.
- "the 30 s response cache only helps identical repeats": **contradicted for the ranking endpoints**. `/repositories` and `/projects` are not behind any cache; only `/cost`, `/users`, `/user-charts` and the overview/cost rollup reads are (`dashboard_cache.py`, router).
- "dashboard_slot() (:783-798) per-event-loop Semaphore, pool−4 = 4, FIFO": **holds** (`settings.py:322`). Python is 3.12.12 (`Dockerfile` `ARG PYTHON_VERSION=3.12.12`).
- "Rollup-served light requests queue behind heavy": **holds by design**. `_rollup_overview`, `_rollup_cost` and `_rollup_users` enter the same `dashboard_request()` (`H:1029`, `:1052`, `:1159`).
- "on main get_repositories already ranks in SQL on one bounded, gated connection (:1313)": **holds**.
- "EPMCDME-17052 does not touch get_projects, Session Detail, Tools, Activity or gate isolation" / "what 17052 still adds": `origin/EPMCDME-17052_grouped-pricing-integration` has 66 commits not on `main` and is 65 behind. Merge base `b916c904a`, last commit 2026-11-10. 10 of them are `EPMCDME-17052:` commits; the rest are pre-squash `EPMCDME-16427` and `EPMCDME-17051` commits that `main` carries squashed (`266837d80`).
  - The branch has no `dashboard_request`/`dashboard_slot`, no `get_projects`, no rollup migrations (last is `a1c1a0000012`).
  - It still gathers in Overview, Tools and Session Detail (branch handler :539, :608, :972, :1171).
  - Its `get_repository_ranking` already exists on `main`.
  - **Unverified** whether any of its 10 commits has logic that is not on `main`; a per-commit diff against `main` would settle it.
  - No local branch exists; only the remote one.
- Not in the ticket: `get_sessions` is also ungated (≤2 connections each) (`H:1547`).
- Unconfirmed runtime items (pool exhaustion, DB saturation, time split, Preview config): **unverified**. No metrics exist in the code to confirm them (Purpose D).

### Consumers of Changed Contracts
- `LocalAnalyticsHandler.get_projects`, `get_session_detail`, `get_activity`, `_repository_rows`:
  - called from `routers/cli_analytics.py` (≈813, ≈978, ≈884);
  - tests in `test_cli_analytics_handler.py` (`get_projects` :852-918, `get_session_detail` :233 and :1258-1348) stub `self._repo` methods directly. They would need `bounded_to_itself` (`tests/.../support/bounded.py`) if the code reads via `bounded()`.
  - `_repository_rows` has one caller, `get_projects` `H:1390`.
- Gate membership list: `test_other_paths_do_not_use_the_gate` asserts the exact list of methods using `dashboard_request`. Any change to gate membership changes this assertion.
- `test_ports.py:27-33` derives the reader methods the handler calls by regex over `self._repo.` / `repo.` and checks them against the port (`CliAnalyticsReader`, `ports.py`). New reader methods must be declared on the port. Keep the `repo_row` naming (comment `H:1400`).
- `dashboard_concurrency_limit` / `CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT`: used by `dashboard_slot` only, and tested in `test_cli_analytics_dashboard_gate.py:38`.
- `get_repository_ranking` / `get_project_page`: called by `get_repositories` and `get_projects` only. Tested in `test_reader.py`, `test_bounded_reader.py`, `test_live_dashboard_aggregates.py` and `support/aggregating.py`.
- `engine.acquire(timeout)`: callers are `ingestor.py:548`, `_ConnectionPair.widen` `R:984`, every reader statement, `advisory_lock`, and the user rollup (`user_rollup.py:1114`, `:1222`).

### Architecture and Layers Affected
- Router: `routers/cli_analytics.py` (the dashboard routes and `execution_time_ms`).
- Service: `cli_analytics_handler.py` (the gate, the budget and handler methods) and `dashboard_cache.py`.
- Repository/adapter: `postgres/reader.py`, `project_page_sql.py`, `grouped_cost_sql.py`, `engine.py`, `settings.py`, `user_rollup.py`, `runtime.py`.
- Migrations: `src/external/alembic_cli_analytics/versions/` (head `a1c1a0000018`).
- Config: `configs/config.py:239-288`.
- Observability: `configs/prometheus_config.py`.

### Integration Points
- PostgreSQL through a dedicated asyncpg pool. It is shared by dashboards, ingest (`/logs`, `/metrics`, `/traces`, `/event-hooks`) and 4 scheduled jobs. Jobs run on one pod per advisory lock.
- The pricing catalog (`model_rates_for`, `CatalogPricer`, rates version) feeds both the rankings and the rollup staleness.
- The UI's Overview tab sends 4 requests in parallel, including `/repositories?per_page=10` (ticket QA comment).

### Patterns and Conventions
- Handler reads go through the `CliAnalyticsReader` port. The adapter must not import `codemie.service` (guide).
- A gated dashboard method has this shape: `async with dashboard_request(), self._repo.bounded() as repo: await repo.materialize_sel(f); ...sequential...`, with pricing and row building after the block.
- Logging: f-strings, no %-args (guide).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-storage.md`:
  - PostgreSQL Settings: pool 8 "each background job uses one connection", ingest 1 s acquire timeout and 503, cache TTL 30 s "a /users, /user-charts or /cost answer";
  - "User Start-Day Rollup" and "Users tab requests (EPMCDME-17051)";
  - "Known performance limits" covers Sessions measurements only;
  - Operations; Testing (`make test`, unit tests need no DB).
- Handoff `docs/superpowers/handoffs/2026-09-24-cli-analytics-postgres-storage-handoff.md` and design `docs/superpowers/specs/2026-09-23-cli-analytics-postgres-storage-design.md` are named by the guide; not read.

### Architectural Decisions
- The guide: "The API contract is fixed (see design D1/D3/D5/D10/D11)". A new reader query goes into the port and the PostgreSQL reader, with a test.
- The rollup values come from the live statements (no second implementation).
- The guide: "Sizing" and the 825 ms Sessions budget were "an assumption; no agreed latency target". The ticket also calls 2 s a QA target, not an SLA.

### Derived Conventions
- Each rollup ships as:
  - its own revision file in `alembic_cli_analytics/versions` (`revision`/`down_revision`, no `alembic.ini`);
  - a builder job in `runtime.jobs()` with a `*_ENABLED` kill switch and interval settings;
  - a day-state table carrying a rates version;
  - a reader `rollup_*` method that returns None to fall back to live.

---

## 4. Testing Landscape

### Existing Coverage
- `test_cli_analytics_dashboard_gate.py`: the limit formula, FIFO, budget timeout, release on exception and cancel, gate membership, the shared budget.
- `test_bounded_reader.py`: one or two connections, no deadlock on a near-exhausted pool (:184), timeouts, and "a dashboard request holds one connection" (parametrised, :309-353).
- `test_cli_analytics_handler.py`: `get_projects` and `get_session_detail` behaviour.
- `test_live_*`, e.g. `test_live_user_rollup.py`, `test_live_overview_rollup.py`, `test_live_cost_rollup.py`, `test_live_dashboard_aggregates.py`: real PostgreSQL via `support/live_engine.py`.
- `scripts/cli_analytics_bench/`, including `ingest_sim.py` and `run_users_cost_bench.py`.

### Testing Framework and Patterns
- pytest with `pytest.mark.asyncio`.
- Fake readers: a `_Reader` with an `inside`/`peak` counter (gate test :120-150) and `bounded_to_itself`.
- `support/aggregating.py` derives aggregate statements from per-session stubs.
- Live tests run against a real PostgreSQL.

### Coverage Gaps
- No test bounds the concurrent connections of `get_projects`, `get_session_detail` or `get_activity`.
- No test of ingest acquire under dashboard load in CI.
- No parity suite for repository/project rankings against a rollup, because no such rollup exists.
- No metrics tests.

---

## 5. Configuration and Environment

### Environment Variables
- `CLI_ANALYTICS_PG_POOL_SIZE` (8)
- `CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT` (0 → pool−4)
- `CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S` (540)
- `CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS` (1000)
- `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS` (30000)
- `CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S` (30)
- `CLI_ANALYTICS_USER_ROLLUP_*` (`config.py:280-288`)
- `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` (30)

### Configuration Files
- `src/codemie/configs/config.py:239-288`
- `postgres/settings.py`: `AnalyticsPgSettings`, `dashboard_concurrency_limit`.

### Feature Flags and Deployment Concerns
- `CLI_ANALYTICS_USER_ROLLUP_ENABLED` is the kill switch of the existing rollups.
- No deployment manifests in the repository set the pool or gate values. The Preview values are unknown (ticket "Unconfirmed").
- `PROMETHEUS_ENABLED` gates the existing pool gauges.

---

## 6. Risk Indicators
- `get_session_detail` alone gathers 8 statements, which equals the whole pool of 8. One Session Detail can starve ingest (1 s acquire, then 503).
- Gate membership is pinned by an exact-list test (`test_other_paths_do_not_use_the_gate`). Its comment records that Sessions and Session Detail are excluded on purpose; that exclusion is a recorded choice the task overturns (see Decisions).
- `widened()` is a no-op after `materialize_sel` (`R:1045`). "At most 2 through widened()" and "sel once" cannot both apply on one connection.
- Speculative: a repository/branch/project rollup is a new dimension. The existing rollups key by start day, user and class pair, and use rates-version staleness, the eligibility limits (unrestricted scope, two filters, window ending now, ≤2000 resumed) and the search being ILIKE/strpos on names. Non-eligible requests (project admins, other filters, search) would keep the live path.
- Speculative: background jobs (up to 4 connections) and the gate (4) together can occupy the whole pool of 8 with no ingest reserve. The guide's "pool minus four for ingestion" assumes jobs are idle.
- `/repositories` and `/projects` have no response cache, contrary to the ticket's wording (see Task Statements).
- `get_sessions` is ungated too and is not named in the ticket.
- EPMCDME-17052 is mostly superseded by `main` (see Task Statements). Its 10 specific commits are unreviewed against `main`.
- There is no analytics-pool or gate instrumentation, so the claimed time split cannot be verified from code.

---

## 7. Summary for Complexity Assessment

The task touches the service layer (`cli_analytics_handler.py`: gate, budget, `get_projects`, `get_session_detail`, `get_activity`, `_repository_rows`), the PostgreSQL adapter (`reader.py` ranking and project-page statements, `project_page_sql.py`, `engine.py`, `settings.py`) and possibly the rollup stack: a migration under `alembic_cli_analytics/versions` (head `a1c1a0000018`), a builder in the `user_rollup.py` style, and a job in `runtime.py`. Config (`config.py`) and observability (`prometheus_config.py` has gauges for the application pool only) are affected too. F1 follows an exact in-repo precedent (`get_tools` `H:1812-1822`, `get_sessions` with `bounded()`/`widened()`). F2 has no precedent for the repository/branch/project dimension, and the existing rollups carry many eligibility and parity rules. F3 isolation would change a documented design decision recorded in `dashboard_slot`'s docstring.

Every ticket code claim was checked against HEAD and holds, with two exceptions. The "30 s response cache" does not cover `/repositories` or `/projects` at all. And `get_sessions` is a further ungated path the ticket does not mention. The 17052 branch predates the gate and rollups on `main`, so it is largely superseded.

Test posture is good for the gate and the bounded reader: fake readers count peak concurrency, there are live PostgreSQL suites, and bench scripts exist. Gaps are the connection bounds of the ungated methods, ingest-under-load, ranking-rollup parity and metrics. Key risks: Session Detail's 8-way fan-out equals the pool; jobs plus the gate can fill the pool; and a gate-membership test pins the current exclusions.

---

## 8. External References

- `/Users/yevhen_slyva/codemie-dev/codemie/docs/superpowers/tasks/2026-11-16-epmcdme-17301-cli-analytics-concurrency/ticket.local.txt`: resolved and read in full.
  - Acceptance: "p95 ≤ 2,000 ms for every tab, both for 1 user and under the agreed concurrent load (QA evaluation target, not an agreed SLA). Concurrent dashboard traffic causes no ingestion connection-acquire timeouts (503)."
  - Fixes F1 to F3 in order: F1 bounded/sequential/widened, `sel` once, gate activity; F2 pre-aggregated rankings, price only the page, branches on expand; F3 metrics first, then a light lane, an ingest reserve and a bounded acquire timeout. Re-run on Preview after.
  - Regression tests: fake-engine acquire count ≤1 (2 in widened); a table-driven gate-membership test; integration with 4×/projects + detail + ingest probe giving 0 503s; ranking rollup = live after each mutation; light requests completing while heavy slots are held; a CI perf budget with statement count and /projects payload pinned.
  - Measured on Preview: /repositories 1,555 ms server for 8.6 KB; /projects 1,965 ms for 661 KB; the UI spends 0.8–1.5 s after the /projects response.
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/integration/cli-analytics-storage.md`: resolved (facts cited in Section 3).
- `origin/EPMCDME-17052_grouped-pricing-integration`: resolved (remote only; findings under Task Statements).
