# EPMCDME-16427 Part 2: Stored Session Sort Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** `sort_by=ctx_per_call`, `sort_by=cost_usd` and `framework=` read stored per-session columns on `session_dims`, with per-session fallback to today's statements.

**Architecture:** New nullable columns plus a kill-switch control row; the refresher and the maintenance job derive them; `rates_version` is the per-session readiness stamp; readers serve fresh sessions from stored columns and merge in the live-computed set S. Indexes land last, after EXPLAIN against the real `sel` window.

**Tech Stack:** PostgreSQL, Alembic (`src/external/alembic_cli_analytics`), pytest (`test_live_*` need `TEST_LOCAL_PG_URL`, throwaway Postgres only).

**Spec:** `spec.md` in this directory (analysis: `technical-analysis.md`). Paths below are under `src/codemie/repository/cli_analytics/postgres/` unless they start with `src/`, `tests/`, `scripts/`, `.ai-run/`.

Commit per task using the repository's existing convention. Each task leaves the tree green; the reader stays on today's statements until `enabled` is set.

## Global Constraints

- Head is `a1c1a0000010`; never edit that file. Copy `SHARED_PREDICATE` byte-for-byte; plain `CREATE INDEX IF NOT EXISTS` with `drop_if_invalid`, 5 s lock timeout, restore `migrations.py:39` `LOCK_TIMEOUT`.
- Ctx key is today's `round(cache_read/calls, 9)` numeric, 0 on no calls; order key DESC, `session_id COLLATE "C"`. Money `round(..., 9)`.
- Repository never imports service (`test_layering.py`); rates arrive as injected callables (`ModelRates`, `ports.py:42`).
- Response model, router parameters, default sort, search rule, other endpoints unchanged. At most 2 pool connections and 2 concurrent statements per request.
- `rates_version` non-NULL = derived; `cost_key` also needs it equal to current. Cap on `|S|` default 5000 (setting). No `delivery_framework` use for the filter.

## Review Focus

- Session with zero calls, no cost rows, or subagent totals only: key 0, never NULL-sorted (tasks 3, 5, 6).
- Session whose `started_at` moves: names re-derived (task 3).
- Catalog change mid-flight: stale `cost_key` never served (tasks 4, 6).
- Viewer scope / `is_unattributed` / search not expressible on stored columns: fallback, same results (tasks 5-7).
- Refresher and maintenance overlapping in either order (task 4).

---

### Task 1: Revision `a1c1a0000011` - columns and control table

**Files:** Create `src/external/alembic_cli_analytics/versions/a1c1a0000011_cli_analytics_session_sort_columns.py`; Test `tests/codemie/repository/cli_analytics/postgres/test_migrations.py`.

**Produces:** `session_dims` columns `cache_read_total bigint, api_call_total bigint, classifier_names text[], models_used text[], kind_masks int[], cost_key numeric, rates_version text` (nullable, no default); table `session_sort_keys_control(enabled boolean NOT NULL DEFAULT false)` with one row.

- [ ] Test-first: yes - `test_migrations.py` fails (revision absent): single head with `down_revision="a1c1a0000010"`, columns nullable no default, control row exists with `enabled=false`, upgrade idempotent, downgrade drops table and columns, `lock_timeout` restored.
- [ ] Implement with `ADD COLUMN IF NOT EXISTS` and `CREATE TABLE IF NOT EXISTS`; docstring states the non-HOT consequence. Run the migration tests.

### Task 2: Rates version and shared kind masks

**Files:** Create `rates_version.py`, `kind_masks.py`; Modify `cost_key_sql.py` (use shared mask definition); Test `test_rates_version.py`, `test_cost_key_sql.py` (existing).

**Produces:** `rates_version(rates: Mapping[str, ModelRates]) -> str` (sorted models, fixed number formatting, sha-256 prefix); `class RatesVersionProvider(rates_callable)` with `.current() -> str`, recomputing only when the callable's output changes; `KIND_BITS` and `kind_mask_sql()` consumed by both `cost_key_sql.py` and the refresher.

- [ ] Test-first: yes - fails (modules absent): equal rate sets in different insertion order give one version; one rate changed gives another; provider calls the hash once for unchanged output; `cost_key_sql` text output unchanged after refactor.
- [ ] Implement; wire the provider where `runtime.set_classifier` is injected (`service/analytics/cli_analytics_jobs.py:101`) as `set_rates(callable)` on runtime, reader and maintenance.

### Task 3: Refresher write path

**Files:** Create `session_sort_keys.py`; Modify `rollups.py` (`_recompute` ~:2292, new gate constant `_SORT_KEY_BITS = _LOG|_USAGE|_SPANS|_SESSION`), `session_derived.py` (expose names for reuse); Test `test_session_sort_keys.py` (live), `test_rollups.py`.

**Produces:** `update_session_sort_keys(conn, session_ids, names, rates, rates_version) -> int` (rows written), one set-wise statement, `IS DISTINCT FROM`, derives only sessions with changed rows, called after `update_session_derived`/`derive_session_activity` in the same transaction.

- [ ] Test-first: yes - live tests fail (columns stay NULL after `_recompute`): new `cost_daily` row, `_USAGE`-only subagent total, new skill name, changed commands, nameless session, zero-call and no-cost-row sessions (key 0), moved `started_at` re-derives names; each column equals today's statements (AC2/AC3, reuse the token/rate case table); unchanged recompute writes 0 rows; `test_rollups.py` gate-vs-`_TRIGGER_BITS` check holds.
- [ ] Implement per signature.

### Task 4: Maintenance backfill, re-price, CLI and enable command

**Files:** Modify `maintenance.py` (next to `_derive_session_activity` :306), `session_sort_keys.py`; Create `sort_keys_cli.py` (audit-log style of `backfill.py`); Test `test_session_sort_keys_backfill.py`, `test_maintenance.py`.

**Produces:** `backfill_session_sort_keys(conn, rates, version, batch) -> BackfillResult` (NULL stamps, `FOR UPDATE SKIP LOCKED`, one transaction per batch, paced); `reprice_cost_keys(...)` (stamp differs); CLI `backfill|reprice|enable|disable` taking the "maintenance" advisory lock; `enable` nulls stamps of sessions active since the switch was cleared, then sets `enabled`.

- [ ] Test-first: yes - fails (functions absent): sessions older than raw retention, subagent-only and no-cost-row sessions fill with 0 mismatches; rerun changes 0 rows; interrupted run resumes; safe vs refresher and maintenance in both orders; changed rate then maintenance re-prices and `cost_key` of the old version is never served in between; `enable` re-nulls stamps.
- [ ] Implement; report counts, duration and WAL delta.

### Task 5: Reader foundation and `ctx_per_call` stored path

**Files:** Modify `reader.py` (`_sort_clause` :1167, `_ordered_window` :1190, `get_session_page` :1238), `settings.py` (cap `CLI_ANALYTICS_SORT_KEYS_S_CAP`, default 5000), `session_page_sql.py`; Test `test_reader.py`, new `test_live_session_stored_paths.py`.

**Produces:** `_stored_mode(conn, f) -> StoredMode` (reads control row once; reasons `disabled`, `s_over_cap`; rate-limited WARNING with reason and `|S|`); `_stale_ids(...)` returning S; merge by key DESC, `session_id COLLATE "C"`; page, `total` from the merged set.

- [ ] Test-first: yes - fails (no stored path): stored result equals fallback (ids, order, total, row money fields) with and without `search`/`is_unattributed`, each viewer scope, first/last/past-end pages, all five `sel` arms; window of only fresh, mixed, only S; `enabled` off and `|S|` over cap equal today's results and log the reason; non-expressible scope takes fallback; request uses at most 2 connections.
- [ ] Implement stored read over the `sel` window with S computed by today's per-session statement restricted to S's ids.

### Task 6: `cost_usd` stored path

**Files:** Modify `reader.py` (`get_cost_page` :1342, `get_window_model_flags` :817), `session_page_sql.py`, `cost_key_sql.py`; Test `test_live_session_stored_paths.py`.

- [ ] Test-first: yes - fails: equality with fallback for ids, order, `total`, `cost_usd`, `cost_available`, `bloat_pct`, `metadata.unpriced`, `catalog_source` on seeds with ties under 5e-10, key-0 and no-cost-row sessions, uncatalogued and missing-rate models; fresh = stamp equals current version, other stamps join S; after a rate change old-version rows are never used.
- [ ] Implement: order `cost_key DESC, session_id COLLATE "C"`; flags from `GROUP BY (models_used, kind_masks)` folded through the part 1 kind-to-rate table; page rows still priced in Python.

### Task 7: Framework stored path

**Files:** Modify `reader.py` (`get_framework_page` :1371), `session_derived.py` (reuse `classifier_names_by_session_sql` for S); Test `test_live_session_stored_paths.py`, existing `test_live_framework_*`.

- [ ] Test-first: yes - fails: accepted ids, order, `total`, flags equal part 1 for a regex rule (not name prefix), including nameless sessions, `search`, mixed fresh/S; group count reported; unpriced metadata scope stays the whole filtered window.
- [ ] Implement: group fresh sessions by `(classifier_names, models_used, kind_masks)`, S names join the same groups, Python applies unchanged `classify_delivery_framework`, `total` is the sum of accepted group counts.

### Task 8: Index revision `a1c1a0000012`

**Files:** Create `src/external/alembic_cli_analytics/versions/a1c1a0000012_cli_analytics_session_sort_indexes.py`; Test `test_migrations.py`, `test_live_session_stored_paths.py`.

**Produces:** `session_dims_ctx_key`, `session_dims_ctx_win`, `session_dims_cost_key`, `session_dims_fw_win`, partial on copied `SHARED_PREDICATE`, expressions equal to Task 5/6 query text; shapes settled by EXPLAIN against `sel` before writing.

- [ ] Test-first: yes - fails (revision absent; EXPLAIN shows Seq Scan): predicate byte-equal to `a1c1a0000010`, idempotent, invalid index rebuilt, downgrade clean, one head; per `sel` arm EXPLAIN uses the new indexes, no Seq Scan of `session_dims`, no whole-window Sort on key plans, Index Only Scan on the framework index, Heap Fetches reported; PG 17 required, 14 if available else noted.
- [ ] Implement; arms the indexes cannot serve are recorded for the bench limitation report.

### Task 9: Benchmarks

**Files:** Modify `scripts/cli_analytics_bench/` (sessions bench, index build bench). **Test-first: no - measurement; outputs are the artifacts.**

- [ ] 7/30/90 days, warm p50/p95 (>= 20 runs) and cold, three sorts plus default sort, quiescent and under concurrent ingest (share of non-empty S, `|S|`, cap-fallback rate), skewed data, framework group count, refresher step time and HOT ratio, backfill duration/WAL, index and `session_dims` sizes, ingest acquire-wait p99 and 503 count, K=4 and 7 connections; each line ends "825 ms met" or "limitation documented for approval"; derive the plain-build lock threshold.

### Task 10: Storage guide

**Files:** Modify `.ai-run/guides/integration/cli-analytics-storage.md`. **Test-first: no - documentation.**

- [ ] Document columns, indexes, writers, control row, stamp and S merge, fallback and log reasons, maintenance backfill/re-price and CLI, rollout order (migration, refresher and maintenance before reader, backfill, indexes, `enable`) and rollback, updated limits table with Task 9 numbers, HOT note (without editing the `a1c1a0000010` docstring).

---

## Negative-constraint pass

- Do not change response model, router params, default sort, search rule, other endpoints: tasks 5-7 touch only the three sort/filter paths; Task 5 test asserts unchanged results.
- Do not use stored `delivery_framework`: Task 7 classifies name sets via the unchanged classifier; the writer is untouched.
- No CONCURRENTLY in the runner, no build-and-swap, side table, fillfactor or b1 arrays: tasks 4 and 8 use batched in-place backfill and plain index builds.
- No partition pruning or request cancellation: not planned.
- Do not edit `a1c1a0000010`: new revisions only (tasks 1, 8).
- Repository must not import service: rates injected (task 2).
- Stored ctx key not float8/`nullif`: tasks 3 and 5 use numeric `round(,9)`.

negative-constraints: checked.
