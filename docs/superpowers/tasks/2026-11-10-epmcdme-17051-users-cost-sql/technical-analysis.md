# Technical Research

**Task**: cli analytics dashboard postgres reader handler pricing funding buckets connection pool
**Generated**: 2026-11-09
**Research path**: filesystem

---

## 1. Original Context

Create sub-bugs under parent EPMCDME-16427 for the CLI analytics requests with the heaviest Python work; combine requests that share cause and fix. Goal: move Python logic to SQL where possible and speed up requests; no 503/504; dashboard numbers must keep matching today's results; no timings measured. Traffic: about 8,000 users, 200,000 sessions per month, 600K per 90-day window. Requests: /users and /user-charts (9+1 statements each; all sessions loaded into Python, paginate last at cli_analytics_handler.py:749-752; buckets() at funding_buckets.py:68 linear scan per user; get_users fetches get_file_facts_by_session but never uses file_rows); /cost (5+1 statements; buckets() per user and model; per-session-model rows priced in Python); /overview (12+1 statements, gather at :508, per-session sets summed at :536-541) and /repositories?page=0&per_page=10 (6+1; buckets all sessions in Python, returns 10, :799-820). Constraints: cache split clamped per row (coding_agent_pricing.py:121) so grouped SQL pricing may differ; unpriced-model reporting happens during Python pricing; SQL pricing exists in cost_key_sql.py with model_rates_for/ModelRates; pool CLI_ANALYTICS_PG_POOL_SIZE=8 per pod, ingest acquire timeout 1 s gives 503, statement timeout 30 s gives 504; only /sessions uses bounded().

---

## 2. Codebase Findings

Paths: `H` = `src/codemie/service/analytics/handlers/cli_analytics_handler.py`, `R` = `src/codemie/repository/cli_analytics/postgres/reader.py`, `FB` = `.../handlers/funding_buckets.py`, `CP` = `.../handlers/coding_agent_pricing.py`, `SM` = `.../handlers/session_money.py`, `CK` = `.../postgres/cost_key_sql.py`, `API` = `src/codemie/rest_api/routers/cli_analytics.py`.

### Existing Implementations

**Purpose: price tokens at the catalog (every money value).**
- `CatalogPricer.price_flagged` `CP:177`: per row, skips kinds with tokens <= 0; a missing rate or model adds 0 and records the model in `unpriced`. One instance per request.
- `kind_tokens` `CP:118`: clamps 1h to the write total, then 5m to the rest; the remainder is priced as 5m.
- `price_session_models` `SM:94`: one pass over `get_session_model_costs` rows; sums `total`, `cache_read`, `by_session`, `cache_read_by_session`, `by_model` (key = normalised name, blank name excluded from `by_model` only); warns on unpriced. Callers: `H:524,620,693,787,1020,1083,1324`.
- SQL twin `CK`: `KIND_TOKENS` / `RATE_COLUMNS` in `postgres/kind_masks.py` apply the same clamp on each `(session, raw model)` group `g`. `session_cost_key_sql` `CK:175` yields `k(session_id, cost_key)`, `round(cost, 9)` numeric, per session, rates bound as float8 via `rates_params` `CK:125`. Used today by the Sessions cost sort only. Rates come from `model_rates_for` `CP:138` (`H:919`) over `get_cost_models` (`R:1516`).
- Token grouping `model_token_groups` `CK:97`: `cost_daily` plus `session_usage` subagent-totals rows, one row per session and raw model. `spend_only` drops Personal-paid sessions via `_spend` `CK:54`.
- Stored per session on `session_dims`: `cost_key`, `models_used`, `kind_masks`, `rates_version`, `cache_read_total`, `api_call_total`, gated by `session_sort_keys_control.enabled`, the rates stamp and `s_cap` 5000 (`_stored_mode` `R:1416`). Written from `model_token_groups` with no spend filter and no funding route (`postgres/session_sort_keys.py:81-110`). Consumers: Sessions list only. A session's stored `cost_key` is a whole-session rounded value, not split by model or kind.

**Purpose: unpriced-model reporting (`metadata.unpriced`).**
- Produced as a side effect of `price_flagged`, `CP:194-195`; read by the router from `pricer.unpriced` at `API:531,550,572,593,643`.
- SQL-flag path already exists: `get_window_model_flags` `R:892` returns per-model `bool_or(kind tokens > 0)`; `CatalogPricer.record_unpriced_from_flags` `CP:198` records unpriced from them (`H:992`, Sessions cost sort and framework filter). Scope there is the whole filtered window.
- `/cost`, `/users`, `/overview`: scope = spend rows (`spend_only=True`). `/repositories`: spend rows of `returned` sessions only (`H:787`). Estimate buckets use a separate `CatalogPricer` (`FB:37`) that never reaches `metadata.unpriced`; each bucket carries its own `unpriced_models` and `cost_incomplete`.

**Purpose: funding and personal-billed buckets.**
- `get_session_funding_costs` `R:913`: per session and raw model, every session of `sel` (no spend filter), with `funding_route` and `personal_billed`. Runs after the gather, sequentially: the "+1".
- `price_bucket_rows` `FB:34`: route outside `FUNDING_ROUTES` becomes `unknown`. `_bucket` `FB:57`: `cost_usd` sum, distinct `session_count`, sorted raw `unpriced_models`, `cost_incomplete`.
- `buckets` `FB:68`: full scan of `priced` per call, 4 `_bucket` calls. Called per user (`H:732`, `H:638`), per model (`H:646`, `FB:88`), per page row (`H:829`), and once whole-window (`H:522,662,758,830,1409`). Comment at `FB:71` records the linear scan as a known shortcut. `/users` per-user calls run for every user before pagination (`H:732` vs `H:749-752`).

**Purpose: per-session facts summed in Python.**
- `get_turns_by_session` `R:1078` (`greatest(nullif(t.turns,0), summary turns)`; NULL possible), `get_file_facts_by_session` `R:1096` (driven from `tool_facts_daily`, so a session with files and no tool row is absent), `get_tool_success_by_session` `R:1130`, `get_session_durations` `R:1212` (NULL = unknown), `get_lines_by_session` `R:1062`.
- Rules at the sum: `/overview` treats NULL duration as 0 (`H:541`); `/users` average skips NULL and keeps real zeros (`H:740-745`). Per-developer sums (`H:710-712`) join on `session_ids` arrays from `get_users` `R:951` (`array_agg(DISTINCT session_id)`, all sessions of all users in one statement result).
- `dead_sessions` `H:594-607`: `money.by_session > 0` and `files_changed == 0`, needs catalog cost per session.

**Purpose: paging and ordering.**
- `/users`: order is catalog `cost_usd` DESC at `round(_, 9)` (`_float_sort_key` `H:217`), then `developer_name` ascending by Python string order (`H:747-748`), page cut at `H:749-752`. `R:951`'s own `ORDER BY` uses the producer `cost_usd` and the handler discards it.
- `/user-charts`: same handler with `page=None`, unpaginated by contract (`API:590-591`); every row is returned.
- `/repositories`: `_build_repository_rows` `H:378`: buckets by `(repository,)` or, with branches, `(project, repository, branch)`, then roll-up `H:331`. Order: `(session_count, round(cost,9))` DESC, ties by repository then project name. `project_name` = most frequent, ties alphabetical (`_most_frequent` `H:261`). `lines_*` are NULL-preserving (`_nsum`). Omitting both page params means all repositories with nested branches (`API:635-638`).
- `/sessions` is the only precedent for DB paging (`get_session_page` `R:1372`, `get_cost_page` `R:1536`; ties `session_id COLLATE "C"`).

**Other touched code**
- `get_users_daily_activity` `R:974` (UNION of five tables, per developer per day), `get_users_last_active` `R:994` (raw `hook_events` probe past window end), `get_lines_by_user` `R:1048`, `get_cost_by_user` `R:939`, `get_session_cost_facts` `R:1232`, `get_class_lists` `R:799`, `get_cost_kpis` `R:838`, `get_model_breakdown` `R:863`.
- `_sessions_cte` is rebuilt in every statement (five-arm `UNION ALL`, `R:292-326`). Each statement of a request computes `sel` on its own; `ids_only` applies only to a few fast paths.

### Decisions Already in the Code

- All money priced at read time at today's catalog; rates are not stored in SQL (`.ai-run/guides/integration/cli-analytics-storage.md` Money). Rates travel as bind arrays. Reason: catalog is LiteLLM or YAML at runtime.
- Reader port is a Protocol; handler never imports the adapter (guide, Where Code Belongs). API contract is fixed (design D1/D3/D5/D10/D11).
- `/sessions` holds one connection, statements in sequence, pricing outside the block (`bounded()` `R:666`, `widened()` `R:679`, `H:981`). "Other endpoints keep their own acquires" (guide, Money). Alternative for these endpoints was not recorded.
- Facts are not period-clipped: a `sel` session contributes all its days (guide, Known gap). Any window-scoped SQL has to keep this.
- Developer identity goes through one `_DEVELOPER` correlated expression `R:412`; lines use `_session_developer("l.session_email")` `R:1053`.
- Token columns of `/users`/KPIs exclude subagent totals rows; money includes them (guide, accepted residuals). Tokens and money therefore come from different row sets.
- Sort ties in Python use code-point order; the reader's convention for matching that is `COLLATE "C"` (`_C` `R:143`).
- Data form: per-session money by model and funding route is stored nowhere; only per-session whole `cost_key` is. Per-developer rollups do not exist. Usual alternative: aggregate at read time by `sel`.

### Task Statements Checked Against the Code

- /users and /user-charts 9+1 statements, all sessions in Python, paginate last at :749-752: **holds** (`H:681-694`, `API:570,590`).
- `buckets()` at `funding_buckets.py:68` linear scan per user: **holds**.
- `get_users` fetches `get_file_facts_by_session`, never uses `file_rows`: **holds** (`H:677,687`; no later read).
- /cost 5+1, buckets per user and model, per-session-model rows priced in Python: **holds** (`H:612-621,638,646`).
- /overview 12+1, gather at :508, sums at :536-541: **holds**. The sums are single passes over per-session rows; `H:522` is the +1.
- /repositories 6+1, Python bucketing, returns 10: **holds** (`H:778-788,799-820`). `buckets` runs only for page rows (`H:828-829`) plus once for `returned`; bucketing runs over all sessions.
- Cache split clamped per row, so grouped SQL pricing may differ: **holds**. The clamp's grain is the `(session, raw model)` row. `KIND_TOKENS` applies it at that same grain and `test_sql_cost_key_and_flags_equal_the_python_pricer_on_every_pricing_case` proves equality to within 1e-9 after rounding. A clamp on a coarser group is not covered.
- Unpriced reporting happens during Python pricing: **keeps a mechanism in place** where Python pricing stays. `record_unpriced_from_flags` is a SQL-flag alternative already used by Sessions (see Existing Implementations).
- SQL pricing exists in `cost_key_sql.py` with `model_rates_for`/`ModelRates`: **holds**. It rounds to 9 decimals per session, and returns only a per-session total.
- Pool 8 per pod; ingest acquire 1 s gives 503: **holds** (`settings.py:371`, `ingestor.py:543`, `API:122`).
- Statement timeout 30 s gives 504: **contradicted for these endpoints**. Only `/sessions` maps it to 504 (`API:749-761`). On `/users`, `/user-charts`, `/cost`, `/overview`, `/repositories`, `handle_errors` turns `asyncpg.QueryCanceledError` (a `PostgresError`) into **503** (`API:282-289`), and the client-side `TimeoutError` (an `OSError` on Python 3.12) into **503** (`API:290-295`). A 504 from an ingress or gateway in front of the API is outside this repository: **unverified**, deployment config would settle it.
- Only /sessions uses `bounded()`: **holds** (`H:981`, only caller).
- No timings measured: **holds for these endpoints at this volume**. Guide Sizing has 30-day medians only at 8,221 sessions, `/overview` projected about 0.8 s at about 500 developers; `scripts/cli_analytics_bench/` benchmarks Sessions only.
- 8,000 users, 200K sessions a month, 600K per 90 days: **unverified** (traffic figures). Window clamp to `CLI_ANALYTICS_RAW_RETENTION_DAYS` (90) confirmed at `API:372-385`.

### Consumers of Changed Contracts

Shared things these requests depend on, searched by name:
- `get_session_model_costs`: `H:517,615,690,784,1314`, session detail variant separate; tests `test_bounded_reader.py`, `test_live_session_cost_key.py`, `test_live_owner_scope.py`.
- `get_session_funding_costs`, `price_bucket_rows`, `buckets`: `H:522,621,694,788,1409` (Efficiency included), `FB:88`.
- `get_turns_by_session`, `get_file_facts_by_session`: also Efficiency (`H:1316-1317`). `get_tool_success_by_session`: only `/overview`, `/users`, `/repositories`, Sessions path via `H`. `get_session_durations`: `/overview`, `/users`, plus live tests.
- `get_cost_kpis`, `get_class_lists`: also Efficiency, Tools (`H:1245-1247,1313`).
- `price_session_models`: also Sessions and session detail, Efficiency.
- `CliAnalyticsReader` port (`ports.py`): every reader method is a Protocol member, and handler tests supply fakes.
- Response models `LocalAnalytics{Overview,Cost,Users,Repositories}Response`; `tests/codemie/rest_api/router_guards/baselines/openapi.json` pins them.
- Dashboard UI is outside this repository; not searched.

### Architecture and Layers Affected

API router (`API`: endpoints, error mapping), service handler (`H`, `FB`, `SM`, `CP`), repository port (`ports.py`), PostgreSQL adapter (`R`, `CK`, `kind_masks.py`, `stored_mode.py`), and the analytics schema where stored columns are involved (`src/external/alembic_cli_analytics/`).

### Integration Points

- `llm_service.get_all_llm_model_info()` (catalog; LiteLLM or YAML) via `CP`.
- `UserIdentityResolver.resolve_rows` per row after handler returns (`API:597-613`), for all rows on `/user-charts`.
- Dedicated asyncpg pool (`postgres/engine.py`): `acquire()` with `timeout=None` for dashboard statements waits without limit; ingest acquires with 1 s. Background jobs take one connection each from the same pool.

### Patterns and Conventions

- Each reader method calls `_q` `R:691`, acquiring its own connection; `asyncio.gather` therefore takes up to N connections at once: 12 on `/overview` and 9 on `/users` against a pool of 8.
- Python pricing, bucketing and row building run directly in the async handlers: no `to_thread` or executor in `H`, `FB`, `SM`, or `API`, so that work runs on the event loop of the pod.
- SQL fragments composed from constants; named `$param` rewritten by `positional`; `sel` CTE required by every builder in `CK`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/cli-analytics-storage.md`: settings, Window Semantics, Money, Stored Session Sort Keys, Known performance limits. Line 215 says dashboard endpoints do not read the session tables yet.
- `.ai-run/guides/quality-gates.md`, `.ai-run/guides/data/database-optimization.md` (general).
- `docs/superpowers/tasks/2026-11-05-epmcdme-16427-part2-stored-session-sort-keys/` (spec, plan, analysis of the sibling work), `docs/superpowers/plans/2026-08-25-cli-analytics-optimization.md`.

### Architectural Decisions

- Part 2 chose stored per-session values for Sessions; kill switch off until every session is stamped; `sort_keys_cli disable` is the rollback.
- Section 6.6 latency budget is 825 ms per endpoint, measured on Sessions only.
- `FB:71` shortcut comment; `API:752` comment on `bounded()` waiting without limit.

### Derived Conventions

Handler prices once per row and sums every way so totals add up (guide, Money). New reader queries go into the port and the PostgreSQL reader with a test.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (46 references to these handlers), `test_cli_analytics_handler_ordering.py`, `test_cli_analytics_spend_scope.py`, `test_cli_analytics_class_lists.py`, `test_cli_analytics_funding_buckets.py` (7 tests).
- `test_cli_analytics_cost_efficiency_parity.py` with `data/cost_efficiency_baseline.json`: golden output of `/cost` and Efficiency, including `unpriced`.
- `tests/codemie/repository/cli_analytics/postgres/`: `test_reader.py`, `test_bounded_reader.py`, `test_live_funding_buckets.py`, `test_live_catalog_money.py`, `test_live_costless_sessions.py`, `test_live_owner_scope.py`, `test_live_session_cost_key.py` (SQL vs Python pricer).
- `tests/codemie/rest_api/routers/test_cli_analytics_funding.py`, `test_cli_analytics_storage_wiring.py`.

### Testing Framework and Patterns

pytest with `pytest.mark.asyncio`; fake readers for handlers; live tests against a real PostgreSQL; golden JSON regenerated on main only.

### Coverage Gaps

- No golden baseline for `/users`, `/user-charts`, `/overview`, `/repositories` like the one `/cost` has.
- No test of SQL pricing parity above the session grain, or for funding-route grouping.
- `scripts/cli_analytics_bench/` has no scenario for these endpoints; no volume or pool-exhaustion test for them.
- 503 mapping of `QueryCanceledError` on these endpoints has no dedicated assertion found (searched `test_cli_analytics_storage_wiring.py` by file name only).

---

## 5. Configuration and Environment

### Environment Variables

`CLI_ANALYTICS_PG_POOL_SIZE` (8), `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS` (30000), `CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS` (1000), `CLI_ANALYTICS_PG_WORK_MEM` (32MB), `CLI_ANALYTICS_RAW_RETENTION_DAYS` (90), `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` (30). Client command timeout = statement timeout + 5 s (`engine.py:43,75`).

### Configuration Files

`src/codemie/configs/config.py`, `postgres/settings.py`.

### Feature Flags and Deployment Concerns

`session_sort_keys_control.enabled` (DB kill switch, Sessions only). Sessions run `jit=off`. Connection must be direct or session-pooled. `MAX_PER_PAGE` 1000 (`API:110`).

---

## 6. Risk Indicators

- Traffic arithmetic from the task's figures, not measured: `/users` at 8,000 users runs `buckets` 8,001 times, each over about 600K or more rows, on the event loop, about 4.8 billion row tests per request.
- Pod-wide effect: synchronous Python work blocks the event loop that also serves ingest and every other request (no offload found).
- Pool: 12 and 9 concurrent statements exceed the pool of 8, so one `/overview` or `/users` request can hold every connection; ingest then waits 1 s and answers 503 (`ingestor.py:543`).
- `503` vs `504`: statement timeouts on these endpoints surface as 503 labelled storage unavailable; an acceptance criterion written for 504 would miss them (see Task Statements).
- Parity: `/users` page order depends on catalog cost at 9 decimals and Python string tie-breaks; a SQL sort needs `COLLATE "C"` and the same rounding. `H:601-607` dead sessions and `by_session` need session-level catalog cost.
- Parity: `round(cost, 9)` per session in `cost_key` vs unrounded float sums in Python; numbers can differ in the last digits.
- Parity: clamp grain is `(session, raw model)`. Coarser SQL groups change results; `by_model` key is the normalised name, the funding rows' grouping also carries `funding_route` and billing class.
- NULL semantics differ per surface (durations, turns, `lines_*`), and files come only from sessions with tool rows.
- `/user-charts` is unpaginated by contract, so paging in SQL does not reduce its row count; `_attach_user_ids` runs on all rows.
- Stored `cost_key` is unusable for spend totals by model or kind, and only valid under stamp and kill-switch rules (see Existing Implementations).
- Task statement keeps Python unpriced reporting in place while `record_unpriced_from_flags` exists: see Task Statements.
- Speculative: `model_rates_for` per model at request time remains a Python step for any SQL pricing.
- Shared readers also feed Efficiency, Tools and Sessions: changing them affects endpoints outside this ticket (see Consumers).
- Undocumented: no timings at target volume; 825 ms budget verified for Sessions only.

---

## 7. Summary for Complexity Assessment

The change surface covers one handler (`cli_analytics_handler.py`, four methods), `funding_buckets.py`, `session_money.py`, the reader and port, plus possibly the router's error mapping. All four request families load every session of the window into Python and price or bucket there; each statement rebuilds the five-arm `sel`. Natural groupings by cause: per-entity `buckets()` scans (`/users`, `/user-charts`, `/cost`, `/repositories`), per-session fact fetching with Python sums (`/overview`, `/users`, `/repositories`), and pool saturation by `asyncio.gather` (`/overview` 12, `/users` 9 against a pool of 8).

Technical novelty is moderate: SQL pricing, per-kind clamp, rate binding, kind flags and unpriced-from-flags all exist for Sessions, along with `bounded()`. They produce per-session totals only; aggregation by developer, repository, model and funding route in SQL has no precedent here. The hard constraint is numeric parity across float sums, 9-decimal rounding, clamp grain, NULL handling and sort ties.

Test posture is good at handler and live-reader level, but only `/cost` has a golden baseline and no volume benchmark exists for these endpoints. Key risks: event-loop blocking, pool exhaustion causing ingest 503s, statement timeouts surfacing as 503 rather than 504 on these endpoints, and shared readers also feeding Efficiency, Tools and Sessions.

---

## 8. External References

None named by the task.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- Consumers: `API:549` returns it through `LocalAnalyticsCostResponse`. Tests: `test_cli_analytics_handler.py:2050,2150`, `test_cli_analytics_spend_scope.py:142`, `test_cli_analytics_funding_buckets.py:54,181,186-190`, `test_cli_analytics_class_lists.py:193`, live `test_live_catalog_money.py:299`, `test_live_owner_scope.py:667,842`. `test_live_owner_scope.py:842` asserts `sum(cost_by_model.cost_usd) == approx(kpis.total_cost_usd)`. No other src reader was found.
- It is called once, at `H:661`. Tests: `test_cli_analytics_funding_buckets.py:48,186-195` (personal-only model, blank model never a row). There is no live (Postgres) test for it.
- Contract: `LocalAnalyticsUsersData.total_count: int | None = None` `M:236`. It is a bare field, not inside a `pagination` object. The `pagination{total_count}` shape in `service/analytics/query_pipeline.py:108` and `response_formatter.py:89` belongs to the Elasticsearch analytics pipeline, not to CLI analytics.
- Consumers: `/users` `API:570` passes it through. `/user-charts` `API:590-591` calls `get_users(f, None, None)` and then overwrites `data["total_count"] = None  # unpaginated by contract`. Tests: `test_live_costless_sessions.py:470` (`== 2`), `test_cli_analytics_storage_wiring.py:466` (mocked return).
- `/users` and `/user-charts` return the same value. Tests: `test_cli_analytics_handler.py:1488,1495,1514` (2000, None, expected); live `test_live_costless_sessions.py:484` (`(d_priced + d_costless) // 2`), `test_reader_live_session_range.py:160` (`672_000`).
- Defined in migration `src/external/alembic_cli_analytics/versions/a1c1a0000012_cli_analytics_session_sort_keys.py:98` (`numeric`). It is part of the INCLUDE list of the sort-keys index (`:127`). The `rates_version` stamp names the rate set that priced it (`:25-26`, `postgres/rates_version.py:17`).
- Written only by `SORT_KEYS_SQL` `postgres/session_sort_keys.py:85-140`. That statement computes `coalesce(ck.cost_key, 0)` from `cost_key_from_groups_sql()` (`postgres/cost_key_sql.py:157-162`, `round(sum(terms)::numeric, 9)` per session) over `cost_groups_cte()` `cost_key_sql.py:151-154` (`model_token_groups(normalized=False)`, no spend argument). Oversized sessions get a NULL `rates_version`. Callers: `backfill_session_sort_keys` / `reprice_cost_keys` `session_sort_keys.py:325`, `maintenance.py:369`, `sort_keys_cli.py:131`.
- Read only by the Sessions list cost sort. Readers: `stored_cost_key()` / `COST_KEY_COLUMNS` `postgres/session_page_sql.py:217-226`, `R:1320-1341`, `R:1399`. The read is gated by `_stored_mode` `R:1495-1520`: scope expressible, current rates version, control row enabled, and `|S| <= s_cap`.
- Tests: `test_live_session_cost_key.py`, `test_live_session_sort_keys_backfill.py`, `test_reader.py:1043-1189,2095-2109`, `test_rates_version.py:90-96`.
- Not to be confused with `_cost_key(column)` `postgres/rollups.py:771`, an unrelated rollup grouping-key helper.
- Spec "No use of the stored `session_dims.cost_key` ... because it is a whole-session value": **holds**. The stored value is per session, unsplit and unfiltered by spend (`session_sort_keys.py:111`). It is also not read on any of the three endpoints today.
