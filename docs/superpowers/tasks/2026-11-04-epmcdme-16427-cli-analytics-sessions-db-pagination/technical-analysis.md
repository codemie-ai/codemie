# Technical Research

**Task**: cli-analytics sessions pagination postgres
**Generated**: 2026-11-04
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-16427 (Bug, Ready for dev): CLI Analytics Sessions list is slow — app-level pagination over the full window instead of DB-level pagination.
Endpoint: GET /v1/analytics/cli-analytics/sessions (router src/codemie/rest_api/routers/cli_analytics.py ~line 684; handler src/codemie/service/analytics/handlers/cli_analytics_handler.py ~lines 584-680; reader src/codemie/repository/cli_analytics/postgres/reader.py, `_sessions_cte` ~line 391, `get_session_page`; scope src/codemie/repository/cli_analytics/scope.py).
Reported: 30-day window takes 8.6-12.9 s; 366,724 rows retrieved to render 20 sessions. Perf target 825 ms.
Required minimum scope:
 - S2: DB-level pagination, search and sort — select the page's session IDs first, then load details only for those IDs. Response shape, financial values (cost) and filters must remain unchanged. Today `get_session_page` pages in SQL only for start_time and ctx_per_call sorts without a framework filter; cost_usd sort and framework filter run in Python over the whole window.
 - S1: an index supporting the session_dims.started_at window (existing indexes: session_dims_pkey, session_dims_story, session_dims_user_started (user_email, started_at)); migration must be safe on a populated DB (migrations in src/external/alembic_cli_analytics/versions/, new file with revision/down_revision); verify with EXPLAIN.
 - S5: reduce concurrent DB operations per Sessions request so ingestion is not starved (pool size 8, ingest acquire timeout 1000 ms).
Out of scope: S3 partition pruning, S4 materialized facts, S6 request cancellation.
Ticket open questions: framework filtering, unpriced-model metadata, SLA, connection management.
Guide: .ai-run/guides/integration/cli-analytics-storage.md (window decided once in _sessions_cte; ANALYZE after bulk loads; 825 ms budget unverified for whole-session scans).
Local env: dockerized Postgres (container codemie-dev-postgres-1, schema codemie_analytics) seeded with 1,000,000 synthetic session_dims + cost_daily rows over 30 days via local/scripts/seed_cli_analytics_sessions.py; cost_daily is partitioned by day. Viewer scope: non-admin forced own_only; admin excludes Personal sessions.

---

## 2. Codebase Findings

Paths below are relative to `src/codemie/` unless noted. Branch `EPMCDME-16427_sessions-db-pagination` has no code diff vs `main` (only `.codemie/codemie-cli.config.json`).

### Existing Implementations

**Purpose A: page / sort / search sessions in the database**
- `repository/cli_analytics/postgres/reader.py:870` `get_session_page(f, sort_by, page, per_page, search, is_unattributed) -> (total, session_ids)`. Rules: sorts only `SESSION_PAGE_SORTS = ("start_time", "ctx_per_call")` (`repository/cli_analytics/ports.py:36`), otherwise `ValueError`; NUL in search returns `(0, [])`; search = `ILIKE` with `ESCAPE '\'` over `first_prompt`, `repository`, `branch` (`_escape_like`, :110); `is_unattributed` → `s.repository = ''`; order `sort_key DESC, session_id COLLATE "C"`; `LIMIT/OFFSET` with offset clamped to `2**62` (:83); `total = count(*) FROM filtered`. `start_time` key = `act.session_start` (needs `_activity_cte`, which runs 4 correlated `log_events`/`spans` probes per session in `sel`, :325-351). `ctx_per_call` key = `round((cache_read + subagent totals cache_read)/calls, 9)` from `cost_daily` + `_totals_join()` (:209).
  - Consumer: only `service/analytics/handlers/cli_analytics_handler.py:906`. Only one implementation of the port (no ClickHouse reader exists in `repository/cli_analytics/`).
  - Distance: the count and the sort key are computed over every session of `sel` (whole window) before LIMIT; no `cost_usd` sort, no framework filter.
- `service/analytics/handlers/cli_analytics_handler.py:888` `get_sessions`: `in_sql = sort_by in SESSION_PAGE_SORTS and not framework` (:902). In-SQL path: `get_session_page` → `f.restricted_to(page_ids)` (`repository/cli_analytics/filters.py:63`, adds `session_id = ANY(...)` inside `sel`, keeps scope) → 5 queries via `asyncio.gather` (:911-917) → `get_skill_names_by_session` (:920). Python path: same 5+1 queries over the whole window, then `_filter_sessions` (:839; search also matches the *served* prompt and the cleaned first prompt of named sessions via `strip_pasted_content`), price, `_page_sessions` (:861; sort by trace_id then key DESC, slice).
- `handlers/cli_analytics_handler.py:794` `_session_row` builds the response row; `cost_usd`, `cost_available`, `bloat_pct` are set after pricing (:954-958).

**Purpose B: the session's cost (`cost_usd`) as served**
- `service/analytics/handlers/session_money.py:94` `price_session_models` prices `get_session_model_costs` rows (`reader.py:538`, token sums per (session, raw model) from `cost_daily` UNION subagent totals of `session_usage`) with `CatalogPricer` (`handlers/coding_agent_pricing.py:96`, `lookup_cost_config(model)` from `llm_service`, per request, records `unpriced`). `priced_session_ids` (:79) decides `cost_available`.
- Stored `cost_daily.cost_usd` exists and is summed by `get_session_cost_facts` (`reader.py:859`), but the guide states it "hold[s] the producer's cost ... stored and not used for money" (guide line 330). The handler overwrites `cost_usd` with the catalog price.
- Consumers of catalog pricing beyond the list: overview, cost, users, repositories, efficiency (`handlers/cli_analytics_handler.py:472,576,647,732,1246`), session detail.

**Purpose C: filter sessions by delivery framework**
- Read-time: `service/analytics/delivery_framework.py:76` `classify_delivery_framework(skill_names)` — first matching customer-config regex (`cliAnalytics.deliveryFramework.rules`) wins, else fallback label (default "Pure chat"). Input from `reader.py:783` `get_skill_names_by_session` (`CLASSIFIER_NAMES_SQL`, detail-gated per id).
- Stored: `session_dims.delivery_framework` (column added in `src/external/alembic_cli_analytics/versions/a1c1a0000006...py:56`), written by the refresher via `repository/cli_analytics/postgres/session_derived.py:107` `update_session_derived` with the classifier injected at `service/analytics/cli_analytics_jobs.py:101`. Already read in SQL by `reader.py:957` `get_framework_sessions` (`delivery_framework = ANY($labels)`, usage attribution A5). Guide line 161/306: NULL after a `--now` backfill until the next recompute; "a stored label updates on the next recompute; existing stored labels are not backfilled"; a renamed rule leaves old stored labels.

**Purpose D: index for the `started_at` window**
- `_sessions_cte` (`reader.py:391`): `FROM session_dims d WHERE d.started_at IS NOT NULL AND started_at BETWEEN $start_dt AND $end_dt` (`_STARTED_IN_WINDOW`, :97), then outer `x` filtered by viewer scope (first) + `_filter_conditions` (:369). Used by ~30 reader methods.
- Indexes on `session_dims`: PK `session_id`; `session_dims_user_started (user_email, started_at)` and `session_dims_story (story_id)` (`a1c1a0000006...py:104-105`). The scope predicates use normalised expressions (`scope.py:49` `sql_norm` = `lower(btrim(coalesce(...)))` over `owner_email`, `subscription_type`, `project_origin`), and the list's user expression is `_SESSION_USER_EMAIL` (`reader.py:100`) — not the raw `user_email` column.
- None found: no index leading on `started_at`.

**Purpose E: per-request connection usage vs ingestion**
- `repository/cli_analytics/postgres/engine.py:84` `acquire(timeout=None)`; every reader query `_q` (`reader.py:360`) acquires its own pooled connection. Pool `max_size = CLI_ANALYTICS_PG_POOL_SIZE` (8, `configs/config.py:211`), `min_size=0`. Ingest acquires with `ingest_acquire_timeout_s` (`postgres/ingestor.py:522`) and answers 503 on timeout. Background jobs each use one connection (guide line 30).
- Sessions request today: SQL path 1 + 5 concurrent + 1 = up to 5 connections at once; Python path 5 concurrent + 1. Other endpoints gather similarly (`get_overview` :463, `get_tools` :1173, `get_efficiency` :1244).

**Other touched code**
- `rest_api/routers/cli_analytics.py:683` `/sessions`: `sort_by: Literal["start_time","cost_usd","ctx_per_call"]`, `per_page` ≤ `MAX_PER_PAGE=1000` (:101), default 20; `filters.resolve_sessions` (:329) forces `own_only` for non-admins.
- `repository/cli_analytics/scope.py:145` `sql_list_condition`.

### Decisions Already in the Code
- `cost_usd` sort and `framework` filter in Python: "neither is expressible in SQL" (`handlers/cli_analytics_handler.py:900-901`; guide line 329). Alternative: stored `delivery_framework` (exists, Purpose C) / pushing catalog prices into SQL; none used for the list.
- The window is decided only in `_sessions_cte` (module docstring `reader.py:19-21`; guide line 53). Facts carry no day predicate; per-session scoping is `session_id IN (SELECT session_id FROM sel)`.
- Viewer scope is always the first `sel` condition (`reader.py:397`); `restricted_to` narrows, never replaces scope (`filters.py:64`).
- Stable ordering: key DESC then `session_id COLLATE "C"` (SQL) / trace_id (Python) so pages neither repeat nor skip.
- Migrations: hand-written SQL lists `UPGRADE`/`DOWNGRADE` executed by `op.execute`; revision ids `a1c1a00000NN`; chain `0001 → 0006 → 0009 → 0007` (head = `a1c1a0000007`, `down_revision="a1c1a0000009"`). Runner `repository/cli_analytics/postgres/migrations.py:42` runs `command.upgrade(..., "head")` inside one `engine.begin()` transaction holding `pg_advisory_xact_lock`, `lock_timeout = 2min` (:39); `src/external/alembic_cli_analytics/env.py:38` `transactional_ddl=True`. Run at app startup (online only).
- Guide decision: no index on the idle time of `session_dims` "would cost HOT updates of `session_dims` on every rollup update of an active session" (guide line 364) — the refresher updates `session_dims` rows continuously.

### Task Statements Checked Against the Code
- "handler ~lines 584-680" — contradicted: `get_sessions` is at `cli_analytics_handler.py:888-964` (584-680 is `get_cost`/`get_users`).
- "router ~line 684" — holds (`cli_analytics.py:683`).
- "`get_session_page` pages in SQL only for start_time and ctx_per_call without framework; cost_usd and framework run in Python over the whole window" — holds (`ports.py:36`, handler :902).
- "Response shape, financial values (cost) must remain unchanged" — keeps `CatalogPricer` + `price_session_models` (Purpose B) in place; consumers listed there. Stored `cost_daily.cost_usd` differs by design.
- "framework filtering" open question — keeps read-time `classify_delivery_framework` in place; stored `session_dims.delivery_framework` exists with staleness/NULL rules (Purpose C).
- "existing indexes: session_dims_pkey, session_dims_story, session_dims_user_started" — holds (`a1c1a0000006...py:104-105`).
- "pool size 8, ingest acquire timeout 1000 ms" — holds (`config.py:211,216`).
- "cost_daily is partitioned by day" — unverified here (initial schema not read for its partitioning; guide describes weekly partitions for raw tables); settle with `\d+ codemie_analytics.cost_daily`.
- "seeded via local/scripts/seed_cli_analytics_sessions.py" — holds; file is gitignored (`.gitignore:51 local/*`), seeds `session_dims` + `cost_daily` only (no `log_events`/`spans`/`session_usage`).
- "non-admin forced own_only; admin excludes Personal" — holds (`cli_analytics.py:329-338`, `scope.py` modes).

### Consumers of Changed Contracts
- `get_session_page` / `SESSION_PAGE_SORTS`: `handlers/cli_analytics_handler.py:38,902,906`; reader :884; tests `tests/.../postgres/test_reader.py:299-330,766-810`, `test_live_session_page.py`, handler tests :120-195.
- `_sessions_cte` / `_STARTED_IN_WINDOW`: ~30 reader methods (`reader.py:500-951`); `get_framework_sessions` repeats `_STARTED_IN_WINDOW` (:975); tests assert its SQL text (`test_reader.py:184-206,372`).
- `get_session_cost_facts`, `get_session_model_costs`, `get_turns_by_session`, `get_tool_success_by_session`, `get_lines_by_session`: shared with overview, users, repositories, efficiency (handler :468-490, :643-647, :727-732, :1246-1251).
- `session_dims` (index target): written by the refresher (`postgres/rollups.py`, `session_derived.py`), purged by `postgres/session_purge.py`.

### Architecture and Layers Affected
- API: `rest_api/routers/cli_analytics.py` `/sessions`.
- Service: `LocalAnalyticsHandler.get_sessions`, `session_money.py`, `delivery_framework.py`.
- Repository port + adapter: `ports.py` `CliAnalyticsReader`, `postgres/reader.py`, `filters.py`, `scope.py`, `postgres/engine.py`.
- Schema: `src/external/alembic_cli_analytics/versions/`.

### Integration Points
- Dedicated asyncpg pool (`engine.py`), shared by dashboard reads, ingest (`ingestor.py`), background jobs.
- Model catalog via `llm_service` (`coding_agent_pricing.py:104,114`).
- Customer config (`customer_config.get_feature_setting("cliAnalytics","deliveryFramework")`).
- Layering test `tests/codemie/repository/cli_analytics/test_layering.py`: repository must not import service (classifier is injected).

### Patterns and Conventions
- SQL built as f-strings with `$name` params converted by `positional` (`reader.py:245`); values always bound, never interpolated.
- `COLLATE "C"` for string order; `normalise` maps asyncpg types.
- Port Protocol in `ports.py` mirrors every reader method.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-storage.md` (71 KB): window rule (l.53), 825 ms unverified (l.57), `jit=off` (l.45), sessions list paging (l.329), cost fields (l.328,330), `delivery_framework` (l.161,306), ANALYZE after bulk load (l.367), migration/revision notes (l.73), measured latencies at x3 (l.348).
- `.ai-run/guides/data/database-patterns.md`, `quality-gates.md`, `testing/testing-patterns.md` apply generally.

### Architectural Decisions
- See Decisions Already in the Code. `a1c1a0000009` docstring: "The down_revision must be repointed to the head on main if another CLI analytics migration lands first."

### Derived Conventions
- Each behaviour rule of the reader is documented both in a docstring and the guide; guide updates accompany reader changes.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/repository/cli_analytics/postgres/test_reader.py`: SQL-text recorder tests (window, scope, page limit/offset, clamped offset, escaped search).
- `test_live_session_page.py`: SQL-paged vs Python-paged equality on every page, order/total, user scope (skipped unless `TEST_LOCAL_PG_URL`).
- Handler: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py:69-300,1308` (reader-paged path, Python path for cost/framework, costless/unpriced logging, framework classification), `_ordering.py`, `_spend_scope.py`, `_class_lists.py`, `_routing_fields.py`.
- Router: `tests/codemie/rest_api/routers/test_cli_analytics_owner_sessions.py`.
- Migrations: `tests/codemie/repository/cli_analytics/postgres/test_migrations.py` (asserts revision chain, e.g. `a1c1a0000007.down_revision == a1c1a0000009`, statement shapes).
- Other live tests touching sessions: `test_live_catalog_money.py`, `test_live_owner_scope.py`, `test_live_costless_sessions.py`, `test_live_first_prompt.py`, `test_live_session_subagent_cost.py`, `test_reader_live_session_range.py`.

### Testing Framework and Patterns
- pytest + pytest-asyncio; fake readers (`page_reader`, `make_handler` in handler tests); SQL recorders; live tests gated by `TEST_LOCAL_PG_URL`.

### Coverage Gaps
- No test of connection concurrency per request or of ingest acquisition under dashboard load.
- No EXPLAIN / plan test for `_sessions_cte`.
- No live test covering `cost_usd` sort or framework filter in SQL (none exists).

---

## 5. Configuration and Environment

### Environment Variables
- `CLI_ANALYTICS_PG_POOL_SIZE` (8), `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS` (30 000), `CLI_ANALYTICS_PG_WORK_MEM` (32MB), `CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS` (1000), `CLI_ANALYTICS_PG_INGEST_STATEMENT_TIMEOUT_MS` (10 000), `CLI_ANALYTICS_PG_SCHEMA` (`configs/config.py:209-219`); `TEST_LOCAL_PG_URL` for live tests.

### Configuration Files
- `configs/config.py`; `repository/cli_analytics/postgres/settings.py:317-378`; customer config `cliAnalytics.deliveryFramework`.

### Feature Flags and Deployment Concerns
- Feature flag `cliAnalytics` (`customer_config.is_feature_enabled`). Migrations run at app startup under advisory lock in one transaction with 2 min lock timeout.

---

## 6. Risk Indicators

- Keeps catalog pricing in place (Purpose B): `cost_usd` is priced in Python per (session, model) from `llm_service`; stored `cost_daily.cost_usd` is the producer's and is documented as not used for money.
- Keeps read-time framework classification in place (Purpose C): stored `session_dims.delivery_framework` can be NULL (backfill `--now`) or carry a renamed rule's label; it differs from read-time classification until recompute.
- `total` in `get_session_page` counts the whole filtered window; `start_time` sort key needs `_activity_cte` probes of `log_events`/`spans` for every session in `sel`, not just the page.
- Migration runner wraps all revisions in one transaction (`env.py:38`, `migrations.py:49`); `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block. A plain `CREATE INDEX` takes a SHARE lock on `session_dims`, blocking refresher writes during the build.
- The revision chain is non-monotonic (`0007` follows `0009`); `test_migrations.py` asserts chain links.
- An extra `session_dims` index adds write cost on frequent refresher updates (guide l.364 rejected an idle-time index for HOT-update reasons).
- Scope/user predicates use normalised expressions (`sql_norm`, `_SESSION_USER_EMAIL`), so `session_dims_user_started` does not match them.
- `test_reader.py` asserts `_sessions_cte` SQL text; ~30 methods share it.
- Search semantics differ between paths: the Python path also matches the served prompt / cleaned first prompt (`strip_pasted_content`); the SQL path matches stored `first_prompt` with `ILIKE`.
- Seed data has no `log_events`/`spans`/`session_usage`; local timings may understate `_activity_cte` and totals-join cost. Guide requires `ANALYZE` after bulk loads.
- Handler line references in the ticket are stale (actual `get_sessions` at :888).

---

## 7. Summary for Complexity Assessment

The change sits in the CLI analytics repository adapter (`postgres/reader.py`, its port `ports.py`, `filters.py`), the service handler `LocalAnalyticsHandler.get_sessions`, and the CLI analytics Alembic chain; the router contract is fixed (`sort_by` literal, response model). A SQL paging path already exists (`get_session_page` + `restricted_to`) for two of three sorts and is tested in parity with the Python path, so the mechanism to extend is in place; the cost sort and the framework filter are the two branches still paged in Python, and both rest on logic outside SQL today (catalog pricing via `llm_service`; customer-config regex classification, with a stored but possibly stale `session_dims.delivery_framework`).

Technical novelty: moderate — no existing index on `started_at`, and the migration runner runs every revision in one transaction with `transactional_ddl=True`, which bears on how a populated-table index is built. Connection usage per request (up to 5 concurrent acquisitions on a pool of 8 shared with ingest and jobs) has no existing limiter in the reader or handler.

Test posture is good for behaviour (SQL-text recorder tests, live parity tests gated by `TEST_LOCAL_PG_URL`, handler fakes, migration-chain tests) but has no coverage for plans/EXPLAIN, connection concurrency, or ingest starvation. Key risks: preserving exact cost values and framework semantics, search semantic drift between paths, total-count cost over the whole window, migration locking on `session_dims`, and `_sessions_cte` being shared by ~30 queries.

---

## 8. External References

- `.ai-run/guides/integration/cli-analytics-storage.md` (in repo, resolved). Facts used: "a session is in a date window if and only if `session_dims.started_at` is in `[start_dt, end_dt]` ... The predicate lives once, in `_sessions_cte`" (l.53); "The section 6.6 latency budget (825 ms per endpoint) is unverified for these whole-session scans" (l.57); "`cost_usd` sorts by catalog prices and `framework` filters by customer regex config, neither expressible in SQL, so those requests are filtered, sorted and paged in Python over the whole window" (l.329); "`cost_daily.cost_usd` and `session_dims.source_cost_usd` hold the producer's cost ... stored and not used for money" (l.330); "After a bulk load or backfill: Run `ANALYZE` on the analytics schema" (l.367); `CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS` "Ingest answers 503 (the plugin retries) instead of waiting past its 2 s timeout" (l.34).
- `local/scripts/seed_cli_analytics_sessions.py` (resolved, gitignored): COPY-seeds `session_dims` and `cost_daily` over N days, 200 users, 50 repos, 3 models, DSN `postgresql://postgres:password@localhost:5432/postgres`, schema `codemie_analytics`.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- Class doc: "one instance per API request". `__init__` reads `llm_service.catalog_source()` (`"litellm"` when `LLM_PROXY_ENABLED` and LiteLLM models loaded, else `"yaml"`; `llm_service.py:73`). Holds `_costs: dict[model -> CostConfig|None]` (memo, each model resolved once via `lookup_cost_config`, `:51`) and `_unpriced: set`.
- `TokenCounts` (`:85`) built from rows by `session_money.token_counts` (`session_money.py:62`) from `input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cache_creation_5m_tokens, cache_creation_1h_tokens`.
- `session_money.price_session_models` (`session_money.py:94-118`): per (session, model) row prices full counts and cache_read-only counts; accumulates `total`, `cache_read`, `by_session`, `cache_read_by_session`, `by_model` (keyed on `normalized_model_name`); logs a warning if `pricer.unpriced`. Creates its own `CatalogPricer()` when none passed.
- `cli_analytics_handler.get_session_detail` (`cli_analytics_handler.py:1028`) — direct per-model call.
- `in_sql = sort_by in SESSION_PAGE_SORTS and not framework`; `SESSION_PAGE_SORTS = ("start_time", "ctx_per_call")` (`repository/cli_analytics/ports.py:36`). Comment `:900-901`: "cost_usd sorts by catalog prices and framework filters by customer regex config: neither is expressible in SQL, so those requests are filtered, sorted and paged in Python over the whole window."
- Returns `money.pricer`; router reads `pricer.unpriced` and `pricer.catalog_source` into `metadata` (`rest_api/routers/cli_analytics.py:713`).
- Migration `src/external/alembic_cli_analytics/versions/a1c1a0000009_cli_analytics_session_title.py:33-35` (ADD/DROP COLUMN only).
- Written by rollups: in `_COMMON_TEXT` (`repository/cli_analytics/postgres/rollups.py:814`), `_SESSION_DIMS_COLUMNS` (`:839`, so never copied to attrs), blank-value guard `_BLANK_TRIMMED` (`:908`), value expr `left(nullif(btrim(lv.v->>'session_title', _BLANKS), ''), 200)` (`:1346`). Latest non-empty value of event field `session_title` wins.
- Served via `_served_prompt(title, first_prompt, repository, branch)` (`cli_analytics_handler.py:142-148`): `title.strip(BLANK_CHARS) or strip_pasted_content(first_prompt) or repository or branch`. Callers: `_session_row` `:817-819` (Sessions list `prompt`), session detail `:1064`, efficiency `worst_session_prompt` `:1280-1282`.
- One `CatalogPricer` per request so `unpriced`/`catalog_source` in metadata cover everything priced (`session_money.py:95-96` docstring).
- `session_title` is stored trimmed and capped at 200 chars; `first_prompt` is matched raw in SQL while the guide says search matches "the cleaned first prompt" (`cli-analytics-storage.md:169`).
- Spec "SQL applies the same arithmetic as `CatalogPricer.price`, including the cache-split clamping": the arithmetic to reproduce is at `coding_agent_pricing.py:121-145` (described above); unpriced-model recording in `_unpriced` is a side effect the SQL would not produce.
- Spec "Search matches ... `session_title` ... for every sort": contradicted as a description of today's SQL path (`reader.py:892-896` omits it); holds for the Python path only through served `prompt`.
- `get_session_page` signature/semantics: handler `:905`; tests `test_reader.py:299-330, 799-819`, `test_live_session_page.py:175-274`.
- Built in `CliAnalyticsHandler.get_sessions`, `src/codemie/service/analytics/handlers/cli_analytics_handler.py:944-951`. It is built only when `not in_sql`. `in_sql = sort_by in SESSION_PAGE_SORTS and not framework` (`:905`), and `SESSION_PAGE_SORTS = ("start_time", "ctx_per_call")` (`repository/cli_analytics/ports.py:36`). So it is built for `sort_by="cost_usd"` or for any request with a `framework`.
- `strip_pasted_content` is in `src/codemie/repository/cli_analytics/vocabulary.py:190`. It removes pasted-content tags, keeps the enclosed text and trims the ends.
- Data source: `get_session_cost_facts` selects `max(s.first_prompt) AS prompt, max(s.session_title) AS session_title` (`postgres/reader.py:853-854`). Both come from `_sessions_cte` (`reader.py:414-415`, `coalesce(d.first_prompt,'')`, `coalesce(d.session_title,'')` from `session_dims d`).
- Lock order is sorted, which prevents deadlocks (`session_binding.py:52`, `ingestor.py:580-582`).
- Spec says the retired Python branch includes `named_first_prompt`: holds. It exists only in the `not in_sql` branch. Retiring it removes the name and first-prompt match for named sessions unless the SQL search adds `session_title`. Today's SQL search has no `session_title` term (`reader.py:893-896`).
- Spec risk bullet `session_binding.py:73-79` (the ingest binding blocked by a SHARE lock during a plain index build, answering 503): holds. Lines 72-79 are `_BIND_SQL` (INSERT/UPDATE on `session_dims`) and `_TOUCH_BOUND_SQL` (UPDATE). Both are writes that a SHARE lock blocks. A blocked statement ends in the ingest `statement_timeout`, which `_transaction` maps to `TelemetryStorageUnavailableError` (503). `BINDINGS_SQL` (SELECT) is not blocked by SHARE.
