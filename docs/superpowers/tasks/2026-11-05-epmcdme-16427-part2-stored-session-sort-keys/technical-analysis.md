# Technical Research

**Task**: cli analytics sessions session_dims (EPMCDME-16427 part 2)
**Generated**: 2026-11-06
**Research path**: filesystem

---

## 1. Original Context

Implement EPMCDME-16427 part 2: "stored per-session sort keys for the CLI Analytics Sessions list" (Jira ticket EPMCDME-16427, follow-up to part 1 which is the parent branch EPMCDME-16427_sessions-db-pagination). Part 1 moved paging/sort/framework filter into PostgreSQL; only default start_time sort meets the 825 ms target. Part 2 adds nullable columns + partial/covering indexes on session_dims (cache_read_total, api_call_total, classifier_names, models_used, kind_masks, cost_key, rates_version), a new Alembic migration after head a1c1a0000010, refresher/maintenance derivation, and reader changes so sort_by=ctx_per_call, sort_by=cost_usd and framework=<name> use the stored keys with fallback to today's statements. The authoritative requirements are the three Jira attachments (verbatim copies): <task-dir>/ticket-attachments/part2-spec.md (ACs, columns, indexes, migration, open decisions), part2-plan.md, part2-prototype-findings.md. Read all three in full; they are the task statement. Prior part-1 task dir for context: docs/superpowers/tasks/2026-11-04-epmcdme-16427-cli-analytics-sessions-db-pagination/ (spec.md, technical-analysis.md). Note the spec is marked DRAFT with "Open decisions for the requester" — list those open decisions and anything in the spec that conflicts with the current code (including part 1's later activity-fact columns has_activity/last_activity_day/resumed in a1c1a0000010).

---

## 2. Codebase Findings

Paths below are under `src/codemie/repository/cli_analytics/postgres/` unless stated.

### Existing Implementations

**Purpose A: per-session `ctx_per_call` key.**
- `reader.py:1167` `_sort_clause` (ctx branch): `cost_daily` sums per session joined to `_totals_join()` (subagent totals from `session_usage`); key = `CASE WHEN calls=0 THEN 0::numeric ELSE round((cache_read + totals)/calls, 9) END`. Order `sort_key DESC, session_id COLLATE "C"`. Consumers: `_ordered_window` (:1190), `get_session_page` (:1238), `get_framework_page` (:1371).
- Window set `sel` (`_sessions_cte` :647, `_admitted_arms` :267) is a five-arm UNION on the activity rule (started in window and `has_activity`; underived NULL arms; `resumed` with `last_activity_day`; one-day edge arm). `ids_only`/`with_start` variants exist for ctx and framework.

**Purpose B: cost key + unpriced flags.**
- `cost_key_sql.py` (`session_cost_key_sql`, `cost_key_from_groups_sql`, `kind_flags_from_groups_sql`, `cost_groups_cte`); `reader.py:1342` `get_cost_page` (page + exact total + flags in one statement via `session_page_sql.cost_page_sql`); `reader.py:817` `get_window_model_flags`; pricing twin `CatalogPricer.price` / `price_session_models` in service layer; rates injected as `ModelRates` (`ports.py:42`); `model_rates_for` at `service/analytics/handlers/coding_agent_pricing.py:118`. No rate/catalog version exists anywhere (grep `rates_version|catalog_version`: no hit).

**Purpose C: framework filter.**
- `reader.py:1371` `get_framework_page` (temp table of names via `framework_names_sql`, `ANALYZE`, distinct sets, Python `matching`, `framework_page_sql`, `framework_count_sql`); `session_derived.py:60` `classifier_names_by_session_sql` (set-wise) and `CLASSIFIER_NAMES_SQL` (per row); `delivery_framework.py:76` `classify_delivery_framework`, injected at `service/analytics/cli_analytics_jobs.py:101` (`runtime.set_classifier`). Stored `session_dims.delivery_framework` exists but is not used for the filter.

**Purpose D: write path (refresher).**
- `rollups.py:2292` `RollupRefresher._recompute`: single transaction; after `RECOMPUTE` statements calls `update_session_derived` (:2312, sessions of keys with `_SESSION_KEY_BITS` at :804 = `_LOG|_SPANS|_METRICS|_SESSION`) then `derive_session_activity(conn, all session ids of keys)` (:2314), then releases keys. `_TRIGGER_BITS` at :2436 (`cost_daily` `_LOG`, `session_usage` `_USAGE`, `invocations_hourly` `_SPANS|_LOG`, `session_skills` `_LOG`).
- `session_activity.py:93` `derive_session_activity` (DERIVE_SQL, every session of the batch) and `backfill_session_activity` (:102, NULL sessions, FOR UPDATE SKIP LOCKED, batch per transaction) called from `maintenance.py:306` `_derive_session_activity`. This is the existing "stored derived fact + NULL = not derived yet + maintenance backfill + reader fallback" mechanism for the same pattern part 2 proposes; the spec proposes a separate backfill CLI and a control-table marker instead.
- Writers of `session_dims`: `session_binding.py:73` (ingest; binding columns and `updated_at` only), `session_purge.py:132,181` (DELETE rows), `rollups.py` (DIMENSIONS and SESSION upserts).

**Purpose E: schema.**
- `src/external/alembic_cli_analytics/versions/a1c1a0000010_cli_analytics_session_list_indexes.py`: adds `has_activity`, `last_activity_day`, `resumed` and six indexes (`session_dims_started_at`, `_started_shared`, `_active_started`, `_active_shared_cov`, `_resumed_last`, `_activity_pending`). Constants `SHARED_PREDICATE`, `drop_if_invalid`, `INDEXES`, `INDEX_LOCK_TIMEOUT`; restores `migrations.py:39` `LOCK_TIMEOUT = "2min"`. Chain: 0001 -> 0006 -> 0009 -> 0007 -> 0010 (single head `a1c1a0000010`; its `down_revision` is `a1c1a0000007`).

**Other touched code**: handler `service/analytics/handlers/cli_analytics_handler.py`; router `rest_api/routers/cli_analytics.py`; `session_page_sql.py`; `backfill.py`; `scripts/cli_analytics_bench/`; guide `.ai-run/guides/integration/cli-analytics-storage.md`.

### Decisions Already in the Code
- Order: key DESC then `session_id COLLATE "C"` for every sort (`reader.py` docstring :1196; guide line 350).
- Money: `round(..., 9)`; ctx key is also `round(..., 9)` numeric (not float8), 0 without calls.
- Derived facts are NULL until derived; reader asks source tables for NULL (a1c1a0000010 docstring; `session_activity.py`). No control table or readiness marker exists in the repo.
- Facts are written inside the refresher transaction; the refresher holds advisory lock "rollup-refresher"; maintenance runs under lock "maintenance" and can overlap (both orders tested).
- Migration convention: SQL constants copied, not imported (`SHARED_PREDICATE`); plain `CREATE INDEX IF NOT EXISTS` with 5 s lock timeout; CONCURRENTLY pre-build above a 150,000-row threshold documented in the guide (line 424); the guide notes "an index on the idle time would cost HOT updates of `session_dims`" (line 423).
- Classifier names set-wise variant bounds invocations by each session's start (`i.hour >= date_trunc('hour', started_at) - 1h`), so stored names depend on `started_at`.

### Task Statements Checked Against the Code
- "Head is `a1c1a0000010`": holds (single head).
- Spec `rollups.py:2254` / `_SESSION_KEY_BITS` `:803`: contradicted by line numbers only; now `:2292` and `:804`.
- Spec/plan "the `a1c1a0000010` HOT note": partly holds; the revision already says the facts update is non-HOT (they are indexed), "again only when its activity days change". The spec's "HOT staying possible" claim is only true for the predicate columns. The refresher now already rewrites `has_activity/last_activity_day/resumed` on the same rows in the same transaction.
- Spec S2 "all indexes partial with `SHARED_PREDICATE`": the shipped window rule (`sel`) is an activity-based five-arm UNION, not `started_at` in window. Prototype indexes (`(started_at) INCLUDE ...`) cover only arm-(1)-like reads; arms (2)-(5) (NULL facts, resumed, edge-day sessions) read `session_dims` otherwise. The spec does not mention `has_activity`, `last_activity_day`, `resumed` at all. Contradicted/unaddressed. The prototype ran before the activity facts existed (compare guide line 354: shared-scope ids-only reads use `session_dims_active_shared_cov`).
- Spec S1/AC3 ratio key `cache_read_total::float8 / nullif(api_call_total,0)`: contradicted. Today's key is numeric `round(x/calls, 9)`, 0 (not NULL) when calls = 0. An index expression must equal that, and tie-order depends on the rounding.
- Spec "S6 `get_session_page` ... read stored columns": ctx page in code goes through `_ordered_window` -> `filtered` CTE with `search`/`is_unattributed`/scope joins; key-ordered index scan applies only if the filtered set is expressible over the index (viewer scope other than shared-unrestricted `_narrows_window` reads full rows).
- Spec baseline numbers (ctx 7.9 s, cost 22-24 s, framework 23 s): contradicted by the guide's later "Final code, measured 2026-11-06" paragraph (guide line 409): on the final code, at 30 days the cost sort, `ctx_per_call` and framework time out at 30 s (7 days: ctx 7.46 s, cost 20.1 s, framework 21.2 s), and the default sort is 2.55 s p50 because of the mid-day-start exact check. The 2026-11-05 approved limits are superseded; the requester approved the 2026-11-06 state.
- Spec S4 "`session_usage` not in `_SESSION_KEY_BITS`": holds (:804). `derive_session_activity` already runs for all keys' sessions, so there is a precedent for a no-bits-gate call.
- Spec S5 "`backfill.py` marks dirty keys only in raw retention": unverified here (file not read); `backfill_session_activity` in `maintenance.py` is a second existing backfill path the spec does not mention.
- Spec "[not verified] supported PostgreSQL versions 14 and 17": docker-compose uses `pgvector/pgvector:pg17`; 14 unverified.
- Spec "metrics facility [not verified]": grep of the package shows no counter facility; only logging.
- Spec/plan non-goal "do not use stored `delivery_framework`": holds; `delivery_framework` is still written by `update_session_derived`.

### Consumers of Changed Contracts
- `get_session_page`, `get_cost_page`, `get_framework_page`, `get_window_model_flags`: called from `cli_analytics_handler.py` (`get_sessions`, `_read_window`, `_ctx_per_call`); tests `test_reader.py`, `test_live_session_page.py`, `test_live_session_cost_key.py`, `test_live_framework_sessions.py`, `test_live_framework_names_parity.py`, `test_live_session_activity_window.py`.
- `RollupRefresher._recompute`: `test_rollups.py` checks every RECOMPUTE statement's gate against `_TRIGGER_BITS`; a new gate constant or step interacts with it.
- `session_dims` writers/readers: `session_purge.py` deletes whole rows (new columns go with the row); `session_binding.py` insert does not name the new columns.
- `test_migrations.py` asserts head/revision and the `a1c1a0000010` statements; `test_layering.py`-style tests forbid repository importing service.

### Architecture and Layers Affected
Migration (alembic), repository/postgres (reader, rollups, session_derived/new module, maintenance or a new CLI), service handler (rates injection), ports (rates callable), guide and bench scripts.

### Integration Points
Refresher <- `cli_analytics_jobs.py` (injects classifier; rates callable would be injected the same way). Reader <- handler (supplies `rates`, `matching`). PostgreSQL only; no other service.

### Patterns and Conventions
Injected callables for service logic into the repository; NULL = not derived; `IS DISTINCT FROM` writes; set-wise statements; migration constants copied; `drop_if_invalid` + `IF NOT EXISTS`; unit SQL-text tests plus `test_live_*` against a throwaway Postgres.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/integration/cli-analytics-storage.md` covers the area (stored activity facts line 61, Sessions paging line 350-355, limits table 383-411, index runbook 424). `.ai-run/guides/integration/cli-analytics-events.md` and `data/database-patterns.md` adjacent.

### Architectural Decisions
Part 1 spec non-goal "S4 materialized session facts" now reversed by part 2. Part 1 decisions: bounded-2 connections, unpriced metadata scope = whole window for cost and framework (user 2026-11-04).

### Derived Conventions
See Patterns above.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/repository/cli_analytics/postgres/`: `test_migrations.py`, `test_reader.py`, `test_rollups.py`, `test_session_derived.py`, `test_session_activity.py`, `test_live_session_activity.py`, `test_live_session_activity_window.py`, `test_live_session_cost_key.py`, `test_live_session_page.py`, `test_live_framework_*`, `test_maintenance.py`, `test_session_purge.py`, `test_backfill.py`.

### Testing Framework and Patterns
pytest; `test_live_*` need `TEST_LOCAL_PG_URL` on a throwaway Postgres. Plan notes 34 live tests fail on main with "missing keyword-only argument 'scope'" (not verified here).

### Coverage Gaps
No code or tests exist for stored sort keys, rates versioning, readiness marker or fallback counters. Skewed-data and write-amplification measurements are not covered.

---

## 5. Configuration and Environment

### Environment Variables
`CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS` (30000), `CLI_ANALYTICS_PG_POOL_SIZE` (8), `CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES`, `CLI_ANALYTICS_SESSION_RETENTION_DAYS`, `CLI_ANALYTICS_SESSION_PURGE_BATCH_SIZE`.

### Configuration Files
`settings.py` in the package; migration runner `migrations.py` (LOCK_TIMEOUT 2min).

### Feature Flags and Deployment Concerns
No flag or control table exists; the spec's readiness marker is new. Plain index builds hold SHARE lock; guide threshold 150,000 rows for CONCURRENTLY pre-build applies (new, larger indexes untested).

---

## 6. Risk Indicators

- Spec vs `sel`: the activity-based five-arm window rule (a1c1a0000010) is absent from the spec; index predicates, window index shape and equality (AC1, AC2) must be re-derived. Prototype timings predate it.
- Ctx key expression mismatch (numeric `round(...,9)`, 0 on no calls vs spec float8/NULL); index expression must match the query byte-for-byte.
- Baseline numbers in the spec are superseded by the 2026-11-06 guide measurements (timeouts at 30 days).
- Two overlapping derivation mechanisms: existing `derive_session_activity` + maintenance backfill vs the spec's new step + dedicated CLI + marker; the NULL-means-not-derived semantic is already how reader falls back, whereas the spec says readiness cannot be read from columns.
- Stored names depend on `started_at` (invocation lower bound): a moved start requires re-derivation.
- No rate/catalog version exists; `rates_version` must be defined (open).
- HOT already lost for activity-fact updates; new indexes include rewritten columns (open decision 2).
- Speculative: the stored path must cover search, `is_unattributed`, non-shared viewer scopes, and `_narrows_window` cases, or fall back for them; the spec says only "with and without search and is_unattributed".
- Speculative: migration-with-backfill rollout on a table with plain-build SHARE locks above 150,000 rows.
- Open decisions (spec list): 1 cost key (a stored priced + version vs b1 arrays; stale handling), 2 HOT loss (fillfactor / side table / accept), 3 backfill method (build-and-swap vs batched), 4 keep both ctx indexes, 5 unpriced metadata scope for framework, 6 ticket (continue vs new). Plan adds D-defaults identical to recommendations; "record answers before Task 1" is unchecked.
- Spec unmeasured items: real name-set cardinality, swap lock time, WAL, skewed data, PG 14, metrics facility.

---

## 7. Summary for Complexity Assessment

Layers: Alembic migration, PostgreSQL repository (reader, refresher, new derivation module, new backfill CLI, readiness marker/control table), service wiring for injected rates, guide and benchmarks. The change surface is large: seven columns, four indexes, a revision, three reader paths with fallbacks, refresher step, backfill command, marker and tests including live PG equality/EXPLAIN checks, and benchmarks (plan has 12 tasks).

Novelty is moderate to high: no rate versioning, no control table, no metrics counters exist; the stored-fact-with-NULL-fallback pattern does exist (`session_activity.py`), and is the nearest template. The spec was prototyped before part 1's activity-based window rule landed and does not account for `has_activity/last_activity_day/resumed`, the numeric rounded ctx key, or the final-code timings; the index and equality designs need rework against `sel`.

Test posture is strong for the neighbouring features (live parity tests) but none exist for this feature. Six requester decisions are open; defaults are the spec's recommendations.

---

## 8. External References

None named by the task beyond the three attachments, all resolved under `docs/superpowers/tasks/2026-11-05-epmcdme-16427-part2-stored-session-sort-keys/ticket-attachments/` (`part2-spec.md`, `part2-plan.md`, `part2-prototype-findings.md`; read in full). The prototype scripts folder `prototype-scripts/` was not read (reference only). The plan cites `technical-analysis.md` of the same name for prototype numbers, which in this directory is the prototype document `part2-prototype-findings.md`.
