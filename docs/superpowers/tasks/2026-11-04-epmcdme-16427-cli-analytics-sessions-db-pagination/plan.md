# EPMCDME-16427 Sessions list DB pagination - Implementation Plan

**Goal:** `/v1/analytics/cli-analytics/sessions` pages, sorts and searches in Postgres (ids first, details for page ids only), gains a `started_at` index, and holds a bounded number of pool connections per request.

**Source:** `spec.md` (approved) and `technical-analysis.md`. Paths are relative to `src/codemie/` unless they start with `src/` or `tests/`.

**Conventions:** Commit per task using the repository's existing convention. Tests live under `tests/codemie/...` mirroring the source tree. Live tests are gated by `TEST_LOCAL_PG_URL`. The repository layer must not import the service layer (`test_layering.py`).

Task order matters: T1 -> T2 -> T3 -> T4; T5 is independent; T6 needs T4; T7 needs T4-T6; T8 needs all.

---

### T1 - Pricing rate DTO and single home for the kind-to-rate table

**Files:** `repository/cli_analytics/ports.py` (new `ModelRates` DTO, frozen dataclass: raw model name, per-kind rates, `None` for missing); `service/analytics/handlers/coding_agent_pricing.py:96-145` (`CatalogPricer`).

- Add a pure helper in `coding_agent_pricing.py` that builds `ModelRates` for a raw model through the existing `lookup_cost_config` memo.
- Add `CatalogPricer.record_unpriced_from_flags(rows)`: rows are `(raw_model, kind-used flags)`. It applies the same kind-to-rate table that `price()` uses (extract that table once, used by both). Uncatalogued with any used kind, or catalogued with a missing rate for a used kind, is recorded in `_unpriced`.
- No change to `price()` results.

**Test-first: yes - a single table of token/rate cases (clamped cache split, missing rate, zero and negative sums, split and unsplit cache) fails because `record_unpriced_from_flags` and `ModelRates` do not exist; the same table later drives the SQL key in T2.**
Test file: `tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py` (extend). Include: uncatalogued model with only zero tokens is not unpriced.

### T2 - Reader: shared token grouping, SQL cost key, unpriced flag statements

**Files:** `repository/cli_analytics/postgres/reader.py` (near `get_session_model_costs` :538, `_totals_join` :209), `ports.py` (`CliAnalyticsReader` Protocol).

- New module-level shared SQL fragment: one token grouping per (session, raw model) from `cost_daily` UNION subagent totals of `session_usage`, scoped by `session_id IN (SELECT session_id FROM sel)`. `get_session_model_costs`, the cost key and the flags all read this fragment (rewire `get_session_model_costs` onto it without changing its rows).
- New shared fragment for the cost key: SQL twin of `CatalogPricer.price` (same kind-to-rate table, cache-split clamp, `tokens <= 0` skip, missing rate = 0, rates bound as float8 from `ModelRates`). Key is `round(cost, 9)`.
- New shared fragment for the kind flags: `bool_or(kind tokens > 0)` per raw `model_name` after the clamp. All flag producers (below and T3's framework method) use this fragment only.
- New method `get_window_model_flags(f) -> list[(raw_model, flags)]` (also the "model list" for the cost sort); viewer scope first via `sel`.

**Test-first: yes - live test fails because the SQL key and flags do not exist: for every case in T1's table, SQL key equals `price_session_models` per session within an epsilon, and SQL flags match the models `price()` records; a case with two costs differing by under 5e-10 orders by `session_id`.**
Tests: `tests/codemie/repository/cli_analytics/postgres/test_live_session_cost_key.py` (new, live) and SQL-text assertions in `test_reader.py`.

### T3 - Reader: DB-chosen page for every sort, search, and framework window method

**Files:** `repository/cli_analytics/postgres/reader.py:870-933` (`get_session_page`), `reader.py:110` (`_escape_like`), `ports.py:36` (`SESSION_PAGE_SORTS`).

- `SESSION_PAGE_SORTS = ("start_time", "cost_usd", "ctx_per_call")`. `get_session_page` gains an optional `rates` argument (required for `cost_usd`, else `ValueError`).
- Orders: `start_time` = `d.started_at DESC, session_id COLLATE "C"` (drops `_activity_cte` for the key); `cost_usd` = `round(cost,9) DESC, session_id COLLATE "C"`; `ctx_per_call` keeps its key with the `COLLATE "C"` tie-break.
- Search: `ILIKE ... ESCAPE '\'` over `first_prompt`, `session_title`, `repository`, `branch`, for every sort; NUL returns `(0, [])`. `total` stays an exact count.
- New sibling `get_framework_window(f, sort_by, rates, search, is_unattributed)`: returns all ordered window ids, each with its skill names (existing `CLASSIFIER_NAMES_SQL`) and its raw-model kind flags (T2 fragment), built on the same order builder. Flags cover the whole filtered window (decision for the open question).

**Test-first: yes - tests fail because `cost_usd` raises `ValueError` and `session_title` is not searched: SQL-text recorder tests (order clause per sort, search terms, COLLATE tie-break, clamped offset) and a live test over fixtures captured from today's behaviour covering first page, last partial page, past the end, clamped offset, search with `%`/`_`/`\`, NUL, `is_unattributed`, no repeats or skips across pages.**
Tests: `test_reader.py:299-330,766-819`, `test_live_session_page.py` (rewrite to compare against captured fixtures, since the Python path is retired).

### T4 - Handler: DB-paged `get_sessions`, Python paging branch retired

**Files:** `service/analytics/handlers/cli_analytics_handler.py:839-964` (`_filter_sessions`, `_page_sessions`, `get_sessions`, `_session_row` untouched), `rest_api/routers/cli_analytics.py:694`.

- Always: resolve `ModelRates` for the distinct models (flags statement) -> page ids from the DB -> `f.restricted_to(page_ids)` -> detail queries -> `get_skill_names_by_session` -> `price_session_models` for page rows (served `cost_usd`, `cost_available`, `bloat_pct` unchanged).
- Framework: use `get_framework_window`, classify with `classify_delivery_framework`, keep matches in SQL order, count for `total`, slice the page, then run details for the page ids.
- Unpriced metadata: `CatalogPricer.record_unpriced_from_flags` from the window flags for every sort (whole filtered window); `catalog_source` unchanged.
- Delete `in_sql`, `named_first_prompt`, the Python search and the sort map in `_page_sessions`.

**Test-first: yes - tests fail because cost sort and framework still page in Python: handler tests with a fake reader assert detail queries receive only page ids for every sort and filter combination; unpriced cases (uncatalogued model only off the page; priced model missing a rate for a used kind off the page; uncatalogued model with only zero-token rows not listed; `own_only` lists only the viewer's models; framework + search case); a test ties `typing.get_args` of the router's `sort_by` Literal to `SESSION_PAGE_SORTS`.**
Tests: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py:69-300,1308` and sibling `_ordering.py`, `_class_lists.py`; `tests/codemie/rest_api/routers/test_cli_analytics_owner_sessions.py`; viewer-scope assertions (non-admin `own_only`, admin excludes Personal) kept green.

### T5 - Alembic revision: `started_at` index

**Files:** new `src/external/alembic_cli_analytics/versions/a1c1a0000010_cli_analytics_session_started_at_index.py` (`down_revision="a1c1a0000007"`); `repository/cli_analytics/postgres/migrations.py:39` (export `LOCK_TIMEOUT` use).

- `UPGRADE`: `SET LOCAL lock_timeout = '5s'`, `CREATE INDEX IF NOT EXISTS session_dims_started_at ON session_dims (started_at)`, then restore the runner's value from the runner's own `LOCK_TIMEOUT` constant (do not rely on "stays last"). `DOWNGRADE`: `DROP INDEX IF EXISTS`.
- Migration file imports the constant lazily or via a shared constants module so the layering stays valid; keep style of `a1c1a0000007`.

**Test-first: yes - `test_migrations.py` fails because revision `a1c1a0000010` is absent: asserts chain (`down_revision == a1c1a0000007`, single head), statement order (set 5s, create idempotent index, restore runner timeout), and downgrade drops the index.**
Test file: `tests/codemie/repository/cli_analytics/postgres/test_migrations.py`.

### T6 - Connection use: `pinned()` reader (default candidate) and bounded-2 candidate

**Files:** `repository/cli_analytics/postgres/engine.py:84`, `reader.py:360` (`_q`), `ports.py`, handler `get_sessions`.

- Port method `pinned()` (async context manager): acquires one connection, returns a reader whose `_q` runs on it. The handler runs its SQL statements sequentially inside `pinned()` and releases the connection before pricing and framework classification (Python work outside the hold).
- Bounded-2 candidate: implemented only on a scratch branch/patch used by T7's benchmark; not committed. After T7 the loser is deleted: no switch, no setting. If `pinned()` loses, remove the port method too and ship bounded-2 (hold two connections for the request).
- Other endpoints keep their current gathers.

**Test-first: yes - fails because no pinned reader exists: a fake-pool test asserts a Sessions request for each sort and the framework filter never has more than one connection acquired at a time, and that it is released before pricing runs.**
Test file: `tests/codemie/repository/cli_analytics/postgres/test_pinned_reader.py` (new) plus handler assertion.

### T7 - Benchmark and EXPLAIN scripts with recorded output (AC9, AC15, AC16, AC18)

**Files:** new `scripts/cli_analytics_bench/` (seed extension, runner) and recorded results in `<task_dir>/bench/`.

- Seed with history longer than the window and `session_usage`, `log_events`, `spans` at production ratios; run `ANALYZE`.
- `EXPLAIN (ANALYZE, BUFFERS)` for 7/30/90-day windows with selected-row fractions, including the window token statement.
- Before/after (main vs branch) p50/p95 for default, cost, `ctx_per_call`, search, framework (incl. fallback label), with per-statement times (flags/model list, page, details) for every sort. Pass bar is only "825 ms met, or limitation documented for requester approval".
- Connection run: K = 4 and 7 concurrent 30-day requests mixing default/cost/framework, refresher active, ingest running; record acquire wait p99 per request type, pool occupancy, ingest 503s, for `pinned()` and bounded-2. Apply AC16 rule to pick the winner and delete the loser (T6).
- AC18: time the plain index build on the 1M seed under ingest and refresher: lock wait and build time separately, failed attempts under the 5 s timeout, refresh batch time with and without the index.

**Test-first: no - measurement scripts; outputs are the recorded artifacts.**

### T8 - Guide, docstrings, Cost/Efficiency parity (AC12, AC17)

**Files:** `.ai-run/guides/integration/cli-analytics-storage.md` (l.53, 57, 169, 329-330, 364 region), reader/handler docstrings, `tests/codemie/service/analytics/handlers/` parity test.

- Guide: new paging/sort/search rules; l.169 corrected to "stored first prompt"; runbook: CONCURRENTLY pre-build under the same name, `indisvalid` check and `DROP INDEX CONCURRENTLY` rebuild, upgrade as no-op; threshold row count from T7 numbers; a failed build leaves the schema not ready, pauses the refresher on that pod and repeats each tick, so the pre-build is required above the threshold; symptom line (repeating migration failure, stale rollups).
- Note the index adds no HOT exclusion (`started_at` already indexed via `session_dims_user_started`).

**Test-first: yes - a parity test fails if Cost and Efficiency results differ before/after for a fixed seed and window: runs `get_cost` and `get_efficiency` over a fake reader/live seed and compares to captured baseline output.**
Test file: `tests/codemie/service/analytics/handlers/test_cli_analytics_cost_efficiency_parity.py` (new).

---

## Negative-constraint pass

- Non-goals S3, S4, S6: no partition, materialized, or cancellation task. Honored by all tasks.
- No stored `delivery_framework` / `cost_daily.cost_usd` for list filter or sort: T2 prices from tokens and rates; T3/T4 classify in Python via `classify_delivery_framework`.
- No CONCURRENTLY in the runner: T5 uses plain `CREATE INDEX IF NOT EXISTS`; the runbook is in T8.
- No response model, router parameter or served-money change: T4 keeps `_session_row` and `price_session_models`.
- No connection change in other endpoints: T6 touches only Sessions.
- Repository must not import service: rates travel as a DTO in `ports.py` (T1).
- Loser candidate leaves no switch or setting: T6/T7.
- No whole-suite gate, browser, review or commit tasks are planned.
