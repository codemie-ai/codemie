# EPMCDME-17051 Users, User Charts and Cost in SQL: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/users`, `/user-charts` and `/cost` each run on one held connection, get rows already aggregated in SQL by a shared grouped-cost module, and answer a timeout with 504. Responses stay as they are.

**Architecture:** A new `postgres/grouped_cost_sql.py` prices every (session, raw model) of `sel` once (`priced_groups`) and aggregates several groupings from it (`gc`). The handlers call new typed reader methods in sequence inside `reader.bounded()` under an `asyncio.timeout` budget. Row building happens after the block.

**Tech Stack:** Python 3.12, asyncpg, PostgreSQL, FastAPI, pytest (`pytest.mark.asyncio`, live PG tests).

**Spec:** `docs/superpowers/tasks/2026-11-10-epmcdme-17051-users-cost-sql/spec.md`. **Contract:** `docs/stories/2026-11-09-cli-analytics-python-load/shared-cost-sql-contract.md`.

Branch `EPMCDME-17051-grouped-cost-sql`. Commit per task using the repository's existing convention (`.ai-run/guides/standards/git-workflow.md`), and keep the commits separate. Tasks 1-3 are the shared point: commit and push them before anything else, then tell the 17052 developer which commit to branch from.

Paths: `H` = `src/codemie/service/analytics/handlers/cli_analytics_handler.py`, `R` = `src/codemie/repository/cli_analytics/postgres/reader.py`, `P` = `src/codemie/repository/cli_analytics/ports.py`, `CK` = `.../postgres/cost_key_sql.py`, `FB` = `.../handlers/funding_buckets.py`, `API` = `src/codemie/rest_api/routers/cli_analytics.py`, `LT` = `tests/codemie/repository/cli_analytics/postgres/`.

## Global Constraints

- Rounding: `cost` and `cache_read_cost` are each `round(_, 9)` per (session, raw model); every sum is a `numeric` sum of those values. The clamp stays at that grain.
- `unpriced_models` sorted `COLLATE "C"`. A blank model is left out of `model` groupings only. A NULL route, or one outside `FUNDING_ROUTES`, becomes `unknown`.
- `spend` = `NOT personal_billed` (one fact, so the two columns cannot drift).
- Do not change: `model_token_groups`, `KIND_TOKENS`, `RATE_COLUMNS`, `rates_params`, `get_session_model_costs`, `get_session_funding_costs`, `price_bucket_rows`, `buckets`, `price_session_models`. Do not read stored `session_dims.cost_key`. Add no pod-wide connection limit.
- The OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json` stays byte-identical.
- Request budget `CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S` = 540, below the 600 s `proxy-read-timeout` (`deploy-templates/values.yaml:273`; every ingress in that file says 600). The target environment's proxy chain is outside the repo, so an operator confirms it.
- Scratch DB for the bench: `postgresql://postgres:scratch@127.0.0.1:55420/scratch`.

## Review Focus

1. A developer whose name comes from a fallback cost or lines email keeps their turns, tool calls and lines on the page (Task 2 test, Task 8 test).
2. A session with only subagent-totals rows counts in `/cost` totals, per model and in the window buckets, but in no user's figure (Task 3 case, Task 7 test).
3. Two unpriced models that differ only in case are listed in code-point order on a non-C database (Task 3 case).
4. A window cost of exactly 0 comes back from SQL as `int` and must serialise as today's `0.0` (Task 6 test).
5. Users tie order: equal `round(cost, 9)` sorts by developer in code-point order, not by locale (Task 8 test).

---

### Task 1: Public pricing builders and one FUNDING_ROUTES

**Files:** Modify `CK:134-147`, `src/codemie/repository/cli_analytics/scope.py`, `FB:27`, `src/codemie/rest_api/routers/cli_analytics_ingest_route.py:99`. Test `LT/test_cost_key_sql.py` (create if absent).

**Produces:** `cost_terms() -> str` (today's `_cost_key_terms`), `cache_read_term() -> str` (the cache-read kind's CASE term only), `unpriced_test() -> str` (bool: some kind with tokens > 0 has a NULL rate), `RATES_JOIN: str` (today's `_RATES_JOIN`), all in `CK`; `FUNDING_ROUTES = ("api_based", "subscription", "unknown")` in `scope.py`.

- [ ] Test-first: yes — `test_public_pricing_builders_keep_session_cost_key_unchanged` fails on import. It asserts that `session_cost_key_sql()` and `cost_key_from_groups_sql()` return the same text as before (pin today's strings), that `FB.FUNDING_ROUTES is scope.FUNDING_ROUTES`, and that the ingest route's `_FUNDING_VALUES == set(FUNDING_ROUTES)`.
- [ ] Rename the two private builders to public, add the two new ones, and point every internal caller at them. Move `FUNDING_ROUTES` into `scope.py` next to `sql_personal_billed`, and import it in `FB` and the ingest route.
- [ ] Run the new test and `LT/test_live_session_cost_key.py`. Both pass, so Sessions results are unchanged.

### Task 2: Developer column on `sel` and the `developers` filter

**Files:** Modify `src/codemie/repository/cli_analytics/filters.py:24` (add `developers: list[str] | None = None`), `R` `_sessions_cte` (around `:735`), `_filter_conditions` (`:713`) and `_DEVELOPER`. Test `LT/test_live_developer_filter.py`.

**Produces:** `_sessions_cte(f, ..., with_developer: bool = False)`. With it on, `sel` has a `developer text` column: today's `_DEVELOPER` expression, computed once per session. `f.developers` applies `developer = ANY($developers::text[])` and is valid only together with `with_developer`. It raises `ValueError` otherwise.

- [ ] Test-first: yes — `test_developers_filter_keeps_sessions_named_by_fallback_email`. Seed a session with no own email whose developer comes from its cost email. `developers=[that name]` returns it, and `users=[that email]` does not. `test_sel_without_developer_is_unchanged` pins the text of an existing caller's `sel`.
- [ ] Implement it. Existing callers do not pass the flag, so their SQL is unchanged.

### Task 3: `grouped_cost_sql.py` and the parity suite

**Files:** Create `src/codemie/repository/cli_analytics/postgres/grouped_cost_sql.py`, `LT/test_live_grouped_cost.py` (fixture style from `LT/test_live_session_cost_key.py`).

**Interfaces (the contract):** the signatures and columns of `priced_groups_cte()`, `GroupKey`, `Grouping` and `grouped_cost_sql(groupings)` exactly as in spec §Design (Shared module). Consumes Task 1's builders and Task 2's `sel.developer`.

- [ ] Test-first: yes — `test_live_grouped_cost.py` fails on import. Cases:
  - every pricing case of `test_sql_cost_key_and_flags_equal_the_python_pricer_on_every_pricing_case`;
  - a model with no rates; a route outside the list and a NULL route;
  - a mixed-case unpriced pair (`Foo-X`, `foo-x`); a blank model;
  - a Personal-paid session; a session with only subagent totals; a costless session (no row).

  For each case, assert:
  - every grouping the contract table lists for 17051 and 17052 (`cost_usd`, `cache_read_cost_usd`, `session_count`, `unpriced_models`, `cost_incomplete`) equals the Python path (`price_session_models` + `price_bucket_rows`/`buckets`) within 1e-9 per group, with lists matched exactly;
  - `priced_groups` summed by `session_id WHERE spend` equals `by_session` and `cache_read_by_session` within 1e-9;
  - the spend `[]` row equals `total` and `cache_read`;
  - `cost_daily_only` leaves out the subagent-only session.
- [ ] Implement `priced_groups` as one `MATERIALIZED` CTE. Join `model_token_groups(normalized=True)` to `sel` once per session for `developer`, `project_name`, `repository`, `branch`, `funding_route` and `personal_billed`. Add `has_cost_daily` from an `EXISTS` on `cost_daily`. `cost`/`cache_read_cost` = `round(<builder>::numeric, 9)` over `RATES_JOIN`, and `unpriced` = `unpriced_test()`.
- [ ] Build `gc` as one `GROUP BY GROUPING SETS` or a `UNION ALL` per grouping (the implementer picks). It returns `grouping`, the union of the key columns (NULL where unused), the sums, `count(DISTINCT session_id)`, `array_agg(DISTINCT model_name ORDER BY model_name COLLATE "C") FILTER (WHERE unpriced)` coalesced to `'{}'`, and `cost_incomplete`. `Grouping` validation raises `ValueError` on an unknown key or a duplicate name.
- [ ] Run the suite and the Task 1 test. Commit, push, and announce the commit to 17052.

### Task 4: Baseline recorder (run before any handler change)

**Files:** Create `scripts/cli_analytics_bench/run_users_cost_bench.py`, following `run_sessions_bench.py` (its `Recorder`/`instrument`/`open_engine`/`handler_for`/`make_filter`). Modify `scripts/cli_analytics_bench/endpoint_data.py` and `ENDPOINT_DATA.md`.

- [ ] Test-first: no — a bench script; its run against the scratch DB is the check. Steps:
  - Add an `edge-cases` subcommand to `endpoint_data.py` (an append run). It seeds what criterion 1 needs and the generator lacks: a session with only subagent-totals rows, a developer named only by a fallback cost or lines email, and two unpriced models differing only in case. `report` prints a count for each, and for the existing edge cases.
  - Add a `baseline --out DIR --days 90 --runs N` subcommand. It calls the handlers directly: `get_users(f, 0, 50)`, `get_users(f, None, None)` with `total_count` set to `None` as `API:590-591` does, and `get_cost(f)`, plus `pricer.unpriced`. It writes `users.json`, `user_charts.json` and `cost.json` (data plus `metadata.unpriced`), and `timings.json` (wall time per run, per-statement times).
  - Add `compare --baseline DIR --out DIR`, which implements criteria 6-7: dollars within $0.01; counts, membership and every other field exact; order exact except rows whose costs differ by < $0.000001; `unpriced`, `unpriced_models` and `cost_incomplete` exact. `0` and `0.0` are equal. It reports any session whose rounded cost is 0 while its Python cost is above 0 (spec, Cost above zero) instead of hiding it.
  - Run `edge-cases`, `report`, and then `baseline --out scripts/cli_analytics_bench/results/17051-baseline` on this commit, which has no handler change yet. Keep the output directory out of git.

### Task 5: 504 on timeout through `handle_errors(timeout_504=...)`

**Files:** Modify `API:263-297`, the `/sessions` inline mapping (`API:~745-765`), and the decorators of `/users`, `/user-charts` and `/cost`. Test `tests/codemie/rest_api/routers/test_cli_analytics_timeouts.py`.

**Produces:** `handle_errors(endpoint_name: str, timeout_504: str | None = None)`. When it is set, `asyncpg.QueryCanceledError` and `TimeoutError` are caught before the 503 branches and raise 504. The message is the given text, and it names the time limit and `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS`.

- [ ] Test-first: yes — `test_timeout_504_maps_query_cancel_and_timeout_error`. Both exceptions give 504 with that message on an opted endpoint, and 503 on one that has not opted in. `/sessions` keeps its exact message text.
- [ ] Implement it. `/sessions` passes its existing text, and the three endpoints pass a shared constant. The OpenAPI baseline test still passes.

### Task 6: Typed cost results, bucket conversion and the request budget

**Files:** Modify `P` (types and constants), `FB` (new helper), `src/codemie/repository/cli_analytics/postgres/settings.py` (budget). Test `tests/codemie/service/analytics/handlers/test_cli_analytics_funding_buckets.py`.

**Produces:**
- in `P`: `CostGroup` (frozen: `keys: Mapping[str, str | bool | None]`, `cost_usd: float`, `cache_read_cost_usd: float`, `session_count: int`, `unpriced_models: tuple[str, ...]`, `cost_incomplete: bool`);
- `GroupedMoney(groups: Mapping[str, list[CostGroup]])`, keyed by the constants `G_TOTAL, G_USER, G_MODEL, G_ROUTE, G_PERSONAL, G_USER_ROUTE, G_USER_PERSONAL, G_MODEL_ROUTE, G_MODEL_PERSONAL`. `Grouping` itself stays inside the adapter;
- in `FB`: `buckets_from_groups(route_rows: list[CostGroup], personal_rows: list[CostGroup]) -> dict`, the same shape as `buckets()`, where a route with no row gets cost 0.0, session_count 0, `[]` and False;
- the setting `dashboard_request_budget_s: float = 540`.

- [ ] Test-first: yes — `test_buckets_from_groups_equals_buckets_on_fixture`. For each existing `buckets()` fixture, the `CostGroup` rows built from it convert to the same dict, an absent route included. `test_zero_cost_int_serialises_as_float` covers a `cost_usd` given as `int` 0.
- [ ] Implement it. Convert every money value with `float()` in the reader, where rows become `CostGroup`.

### Task 7: `/cost` in one connection

**Files:** Modify `P`, `R` (`get_cost_by_user` drops `session_ids`; add `get_cost_money`), `H:611-664`, and the fakes in `tests/codemie/service/analytics/handlers/`. Tests `test_cli_analytics_handler.py`, `LT/test_live_catalog_money.py`.

**Produces:** `get_cost_money(f, rates: Sequence[ModelRates]) -> GroupedMoney`. One statement does `sel` (with developer), `priced_groups`, and `gc` with spend `[]`, `[user]` (`cost_daily_only`) and `[model]`, plus all `[funding_route]`, `[personal_billed]`, `[user, funding_route]`/`[user, personal_billed]` (both `cost_daily_only`), `[model, funding_route]` and `[model, personal_billed]`.

- [ ] Test-first: yes — these fail against the current handler:
  - `test_cost_runs_in_one_bounded_connection_in_order`: the fake's top-level reader raises on any call, and its bounded reader records the order rates → money → kpis → class lists ×2 → cost_by_user;
  - `test_cost_budget_timeout_releases_connection`: a 0.01 s budget raises `TimeoutError` and the bounded context has exited;
  - `test_cost_subagent_only_session_in_totals_not_users`;
  - `test_cost_empty_filter_returns_todays_empty_response`;
  - `test_cost_unpriced_from_spend_total_only`.
- [ ] Rewrite `get_cost`. Inside `async with asyncio.timeout(budget), reader.bounded() as r:` it runs `get_cost_models` → `model_rates_for` → `get_cost_money` → `get_cost_kpis` → both `get_class_lists` → `get_cost_by_user`. After the block it joins money and buckets onto `cost_by_user` rows by user, builds `cost_by_model` from the spend `[model]` rows, and builds `models_without_spend` from the models in the all-scope model groupings that have no spend row. Both keep today's sorts. It adds the spend `[]` `unpriced_models` to `pricer._unpriced` and keeps the return type `(dict, CatalogPricer)`.
- [ ] Run the handler and live money tests, and `test_cli_analytics_cost_efficiency_parity.py` (the `/cost` golden). All pass.

### Task 8: Users reader methods

**Files:** Modify `P`, `R` (`get_users`, `get_lines_by_user`, `get_users_last_active`, `get_users_daily_activity`; add the two below). Test `LT/test_live_users_page.py`.

**Produces:**
- `get_users_money_page(f, rates, limit: int | None, offset: int) -> UsersMoneyPage(developers: list[str], total_count: int, money: GroupedMoney)`. One statement: `sel` (developer), `priced_groups`, `gc` spend `[]`/`[user]` and all `[funding_route]`, `[personal_billed]`, `[user, funding_route]`, `[user, personal_billed]`. The ranking is the distinct `sel.developer` LEFT JOIN spend `[user]`, `ORDER BY round(coalesce(cost_usd, 0), 9) DESC, developer COLLATE "C"`, `count(*) OVER ()`, LIMIT/OFFSET when `limit` is set. The per-user rows are returned for the page's developers only.
- `get_developer_session_facts(f) -> Rows` (`developer, turns, tool_calls, tool_successes, avg_session_duration_ms`): sums with NULL as 0, and the average skips NULL and keeps real zeros (`H:736-745`). It covers every session of `sel` for each developer, because `developers` keeps all of that developer's sessions.
- `get_users` without `session_ids`, producer `cost_usd` or ORDER BY. All four existing methods honour `f.developers`. Lines stay on `_session_developer("l.session_email")` and filter on that same value.

- [ ] Test-first: yes — these fail now:
  - `test_users_page_ranks_ties_code_point_and_counts_total` (names `B`, `a` at equal cost);
  - `test_users_page_buckets_only_page_developers`;
  - `test_developer_facts_include_fallback_email_sessions`;
  - `test_avg_duration_skips_null_keeps_zero`;
  - `test_users_no_session_ids_column`.
- [ ] Implement them and run the tests.

### Task 9: `/users` and `/user-charts` in one connection

**Files:** Modify `H:668-759`, the handler fakes, and `.ai-run/guides/integration/cli-analytics-storage.md` (mark "dashboard endpoints keep their own acquires" superseded for these three requests). Tests `test_cli_analytics_handler.py`, `test_cli_analytics_handler_ordering.py`, `LT/test_live_costless_sessions.py`.

- [ ] Test-first: yes — these fail now:
  - `test_users_runs_in_one_bounded_connection_in_order`: rates → money page → `get_users`, facts, lines, last active, daily activity with `developers` = the page (`/user-charts`: `None`);
  - `test_users_never_fetches_file_facts`;
  - `test_users_budget_timeout_releases_connection`;
  - `test_users_empty_filter_returns_todays_empty_response`;
  - `test_user_charts_returns_every_user`, with `total_count` set by the router as today.
- [ ] Rewrite `get_users` with today's signature. The block is `asyncio.timeout(budget)` + `bounded()`, and the statements run in sequence. The row build happens after the block, in the page order from `UsersMoneyPage.developers`, with buckets from `buckets_from_groups` and `metadata.unpriced` from the spend `[]` row. Remove the `get_file_facts_by_session` call.
- [ ] Run these tests and the live costless test (`total_count == 2`). Update the guide line.

### Task 10: Volume and k=3 load bench

**Files:** Modify `scripts/cli_analytics_bench/run_users_cost_bench.py` (reuse `ingest_sim.py` and the refresher stand-in of `run_sessions_bench.py`), `bench-results.md`.

- [ ] Test-first: no — measurement against the scratch DB. Steps:
  - `compare` against the Task 4 baseline: criteria 6-7 hold, criterion 2 holds (no 503 or 504), and criterion 14 holds (wall time next to each statement's time).
  - `load --k 3`: 3 Users tabs (`/users` + `/user-charts`) and 3 Cost tabs per round, with ingest and both background jobs running.
    - Pass when ingest has no 503 and no dashboard request returns 503 or 504.
    - Every request must be no slower than its baseline time.
    - Ingest p99 must be at most **250 ms** above a no-dashboard run (criterion 13 threshold).
    - Also record the peak pool connections per request (must be 1).
  - `explain`: `EXPLAIN (ANALYZE, BUFFERS)` of the Cost money statement and the Users money-page statement. Report temp blocks written, `pg_stat_database.temp_files`, and `temp_file_limit`. Time the token read in the `IN (SELECT session_id FROM sel)` form against the form joined to `sel`.
  - Measure event-loop lag during one `/user-charts`, as the max `asyncio.sleep(0.01)` overshoot. The bound is **200 ms**.
- [ ] Only if a measurement fails its bound:
  - Join form faster: build `priced_groups`' token read joined to `sel` inside `grouped_cost_sql.py`, and leave `model_token_groups` unchanged. The Task 3 suite still passes.
  - Event-loop lag over 200 ms: move the `/user-charts` row build and the `_respond` body plus ETag into `asyncio.to_thread`, with the response unchanged.
  - A k=3 failure: stop and report it as a new pool decision (spec AR-5). Do not add a limit.
- [ ] Record the results in `bench-results.md`.
