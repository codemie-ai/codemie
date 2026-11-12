# EPMCDME-16427 part 2: stored per-session sort keys - Implementation Plan

> **For agentic workers:** use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** `ctx_per_call` sort, `cost_usd` sort and the `framework=` filter of `/v1/analytics/cli-analytics/sessions` read stored per-session columns on `session_dims` (covering indexes) and meet 825 ms, with byte-equal results to part 1's statements and a fallback to those statements whenever stored values are absent or stale.

**Architecture:** new nullable columns + indexes (revision `a1c1a0000011`); the refresher keeps them current; a dedicated backfill fills them (build-and-swap by default); a readiness marker gates the reader's stored path, otherwise today's statements run unchanged.

**Spec / facts:** `spec.md` (DRAFT, all Proposed), `technical-analysis.md` (prototype numbers; scratch code `prototype-scripts/*.py.txt` is reference only, not lint-clean). Paths relative to `src/codemie/` unless they start with `src/` or `tests/`. Part 1 plan: `../2026-11-04-epmcdme-16427-cli-analytics-sessions-db-pagination/plan.md`.

**Conventions:** Commit per task using the repository's existing convention: subject `EPMCDME-16427: <imperative summary>` (or the new key if decision 6 = separate ticket). Tests mirror the source tree under `tests/codemie/...`. Repository layer must not import the service layer (`test_layering.py`); rates reach the refresher/reader as injected callables or DTOs (`ModelRates` from part 1 `ports.py`). Do not edit the `a1c1a0000010` file or its docstring. Response model, router parameters and served money values do not change.

**Quality gates (every task, plus at the end):**
- `make ruff`
- Unit: `poetry run pytest tests/codemie/repository/cli_analytics tests/codemie/service/analytics tests/codemie/rest_api -q --ignore-glob='*live*'` (verify exact Poetry/pytest invocation in `.ai-run/guides/quality-gates.md` and `testing/testing-patterns.md` before first run).
- Live: only on a throwaway Postgres container bound to a loopback port (PG 14 and 17): `env -u CLI_ANALYTICS_PG_URL TEST_LOCAL_PG_URL=postgresql://...@127.0.0.1:<port>/... pytest -q -n0 <live test files>`. Never against the docker-compose DB.
- Known baseline: 34 live tests fail with "missing keyword-only argument 'scope'" on main too; compare failures against main, do not fix them here.
- No whole-suite gate, browser, review or commit-artifact tasks are planned (the calling flow owns them).

---

## Decisions to settle before Task 1

The requester decides. Until they object, the plan assumes the default (the spec's recommendation). Tasks marked "[Dn]" depend on the answer; if an answer differs, re-plan only those tasks.

| # | Decision | Assumed default | If the answer differs |
|---|---|---|---|
| D1 | Cost key | (a) stored priced `cost_key` + `rates_version` ("b2"); stale handling = version check + re-price job, fallback to today's statement only while versions differ. Optional second connection for the flags statement: not assumed (part 1 caps at 2 connections) | (b) "b1" (six token-kind arrays, 190 B/session, 522 MB index, 8.7 s, misses target): T1 columns/index, T2 write, T3 backfill, T6 cost path and T5 re-price job change; T7 drops the cost AC10 target (limitation documented). Fallback-only (no re-price job): drop T3 re-price step; T6 serves fallback while versions differ |
| D2 | HOT loss | (c) accept now; choose after the T9 write benchmark: (b) side table if bloat/ingest slowdown shows | (a) fillfactor: one `ALTER TABLE ... SET (fillfactor=..)` in T1 + measure in T9. (b) side table `session_sort_keys(session_id PK, ...)`: T1 creates that table instead of columns, T2/T3 write it, T6 reads join it, T6 also touches `session_purge.py` |
| D3 | Backfill | (a) build-and-swap + catch-up via refresher | (b) batched in-place `UPDATE` (10k per batch, indexes built after): T4 becomes a keyset-batch loop, no swap/lock step, T10 swap-lock and catch-up measurements replaced by batch duration |
| D4 | Both `ctx_per_call` indexes | keep both until T9 skew test (AC11) | key-only: drop window-index DDL from T1 (and T8 if it is dropped later) |
| D5 | Unpriced metadata scope for framework | whole filtered window (part 1 decision) | page-scoped: framework pass in T8 drops `models_used`/`kind_masks` from the GROUP BY and index INCLUDE |
| D6 | Ticket | continue under EPMCDME-16427 | commit prefix and branch name only |

- [ ] Record the requester's answers at the top of this file (one line each) before starting Task 1.

---

## Task order and dependencies

T1 -> T2 -> T3 -> T4 -> T5 -> T6 (ctx_per_call) -> T7 (cost) -> T8 (framework) -> T9 -> T10 -> T11 -> T12. The system stays shippable after each task: until T5 sets the marker the reader runs today's statements.

### T1 - Alembic revision `a1c1a0000011`: columns and indexes (AC7, AC16 part) [D2, D4]

**Files:** new `src/external/alembic_cli_analytics/versions/a1c1a0000011_cli_analytics_session_sort_keys.py` (`down_revision="a1c1a0000010"`; repoint if another head lands first); tests `tests/codemie/repository/cli_analytics/postgres/test_migrations.py`.

- Follow `a1c1a0000010`: `SET LOCAL lock_timeout='5s'` once, `ALTER TABLE session_dims ADD COLUMN IF NOT EXISTS` for `cache_read_total bigint, api_call_total bigint, classifier_names text[], models_used text[], kind_masks int[], cost_key numeric, rates_version text` (nullable, no default), then per index `drop_if_invalid` + `CREATE INDEX IF NOT EXISTS`, then restore the runner's `LOCK_TIMEOUT` (`migrations.py:39`).
- Indexes (names `session_dims_ctx_key`, `session_dims_ctx_win`, `session_dims_cost_key`, `session_dims_fw_win`), all `WHERE <SHARED_PREDICATE copied byte-for-byte from a1c1a0000010>`: key `((cache_read_total::float8 / nullif(api_call_total,0)) DESC, session_id COLLATE "C") INCLUDE (started_at)` (verify the expression equals part 1's ctx key in `reader.py` `get_session_page`); window `(started_at) INCLUDE (session_id, cache_read_total, api_call_total)`; cost `(coalesce(cost_key,0) DESC, session_id COLLATE "C") INCLUDE (started_at)`; framework `(started_at) INCLUDE (session_id, classifier_names, models_used, kind_masks)`. No cost window index (never picked).
- Docstring states the HOT consequence (new indexes include columns the refresher rewrites). Downgrade: drop indexes, then columns, `IF EXISTS`.
- Under D3(a) the swap in T4 recreates these indexes by the same names/definitions, so keep the DDL in one constants tuple the backfill command can import (migration keeps its own copy: a migration must not change with code; a test asserts the two are equal).

**Test-first: yes - `test_migrations.py` fails because revision `a1c1a0000011` is absent: asserts single head and `down_revision`, columns nullable with no default, predicate byte-equal to `a1c1a0000010`, idempotent upgrade, an invalid leftover index is dropped and rebuilt, downgrade removes all, `lock_timeout` restored.**
Gate: unit + live migration test on PG 14 and 17.

### T2 - Write path in the refresher (AC6) [D1, D2]

**Files:** `repository/cli_analytics/postgres/rollups.py` (`RollupRefresher._recompute` ~:2254, `_SESSION_KEY_BITS` :803, `_TRIGGER_BITS` ~:2396), `repository/cli_analytics/postgres/session_derived.py` (`update_session_derived` :135, `CLASSIFIER_NAMES_SQL`), new `repository/cli_analytics/postgres/session_sort_keys.py`; tests `tests/codemie/repository/cli_analytics/postgres/test_session_sort_keys.py` (new, SQL-text + live).

- Read first (verify): how `_recompute` batches sessions, how dirty bits are cleared, which job writes `delivery_framework`, and the other `session_dims` writers (`session_binding.py`, `session_purge.py`, `maintenance.py`, `ingestor.py`). Purge must delete or ignore the new columns with the row.
- New `update_session_sort_keys(conn, sessions, rates_for, rates_version, timeout)`: one set-wise statement per batch computing totals (`cost_daily` + `session_usage` subagent totals), names (reuse `CLASSIFIER_NAMES_SQL`, `COLLATE "C"` ordered, NULL when empty), `models_used`/`kind_masks` (same kind-flag rule as part 1 `reader.py` flags fragment), `cost_key` (reuse `cost_key_sql.py` fragments; priced with injected rates), `rates_version`. Write only `IS DISTINCT FROM` rows.
- Call it in the same transaction right after `update_session_derived`, for sessions of keys with `_LOG | _USAGE | _SPANS | _SESSION` bits: define a new gate constant (do not reuse `_SESSION_KEY_BITS`, it excludes `_USAGE`). `rates_for`/`rates_version` are injected callables, as `classify` is; define `rates_version` (verify whether a catalog version exists; else hash of the `ModelRates` of the models in use).

**Test-first: yes - live tests fail because the columns stay NULL after `_recompute`: a new `cost_daily` row, a `session_usage` subagent total (a `_USAGE`-only key must trigger), a new skill name, a changed `commands` array, and a session without names each produce the values the part 1 statements produce; an unchanged recompute updates zero rows; a layering test confirms no service import.**

### T3 - Rates version helper and re-price job (AC12) [D1]

**Files:** `session_sort_keys.py` (add `reprice_cost_keys(conn, rates_for, rates_version, batch)`), service wiring where `classify` is injected into the refresher (verify), tests in `test_session_sort_keys.py`.

- `reprice_cost_keys`: set-wise, keyset-batched by `session_id`, rewrites `cost_key`/`rates_version` for rows whose `rates_version` differs, resumable. Called by a maintenance tick and by the CLI in T4.
- Under D1 fallback-only: skip the job; T7 still needs the version check.

**Test-first: yes - fails because no re-price exists: after changing a rate, rows keep the old version until re-priced; after it, `cost_key` equals the part 1 SQL key for the new rates; a second run touches 0 rows.**

### T4 - Dedicated backfill command (AC8, AC15 part) [D3]

**Files:** new `repository/cli_analytics/postgres/sort_keys_backfill.py` (`python -m codemie.repository.cli_analytics.postgres.sort_keys_backfill [--mode swap|batched] [--batch N] [--resume]`), same audit-log style as `backfill.py`; tests `test_sort_keys_backfill.py`.

- Reason: existing `backfill.py` marks dirty keys only inside raw retention; totals span `cost_daily`/`session_usage`, which outlive it.
- Swap mode (D3a): stage per-group aggregates (totals, names, models/masks, cost), `CREATE TABLE ... AS` joining `session_dims`, then re-create everything `CREATE TABLE AS` drops (verify: primary key, defaults, NOT NULL, constraints, other indexes, grants, sequences/ownership, FKs, autovacuum options), swap names under a short `lock_timeout`, run the T2 refresher step for rows ingested/changed during the build (catch-up by `updated_at`/dirty keys; verify the column to use), build the four indexes, `ANALYZE`. Pause or short write-lock choice is recorded in the runbook (T11).
- Batched mode (D3b): `UPDATE` by `session_id` range, 10k per batch, commit between, resumable, indexes built after.
- Idempotent and resumable; prints counts; writes audit line. It never sets the marker (T5 does).

**Test-first: yes - live test fails because the command does not exist: seeds sessions older than raw retention plus subagent-only and no-cost-row sessions, runs both modes, asserts every column equals the part 1 statements (AC2 equality: ids, key, names, models, masks, 0 mismatches), a rerun changes 0 rows, an interrupted run resumes, a row written during the swap is caught up, constraints/defaults/indexes equal pre-swap.**

### T5 - Readiness marker and fallback plumbing (AC5, AC8 marker part; S8)

**Files:** new control table in a small migration revision `a1c1a0000012` or inside T1 (decide at implementation; if separate, repoint chain and extend T1 tests), `repository/cli_analytics/postgres/session_sort_keys.py` (`read_marker`, `set_marker`), `reader.py` (`_stored_path(f, kind)` returning reason or OK), CLI flag `--set-marker` on the T4 command.

- Marker row: `complete bool`, `rates_version text`. Read once per request. `set_marker` refuses unless no session in the window has NULL columns that today's statements would not produce NULL for (NULL names and no-cost-row NULLs are legal). Marker off = rollback.
- Fallback reasons `not_ready`, `rates_version_differs` (cost only), `stored_null`; per-request log line and counter. Verify whether a metrics facility exists; if not, a rate-limited WARNING.

**Test-first: yes - fails because `_stored_path` is absent: marker off, NULL columns inside the window and a differing version (cost only) each return the matching reason; `set_marker` refuses with NULLs; each fallback increments the counter/emits one log.**

### T6 - Reader: `ctx_per_call` stored path (AC1, AC3, AC5, AC9) [D4]

**Files:** `reader.py` (`get_session_page` ctx branch, `_sessions_cte`, `_ordered_window`); tests `test_reader.py` (SQL text), new `test_live_session_stored_paths.py`.

- When `_stored_path` is OK, order by the stored ratio expression (identical to the index expression) DESC, `session_id COLLATE "C"`; totals and `search`/`is_unattributed`/viewer scope unchanged; otherwise unchanged statements. NULL ratio sorts as today's key (verify what part 1 does with `api_call_count = 0`).

**Test-first: yes - fails because the page still scans `cost_daily`: on a seeded database the stored result equals the fallback result (ids, order, total, row money fields) with and without `search`/`is_unattributed`, over first/last/past-the-end pages; `EXPLAIN` asserts `session_dims_ctx_key` or `_ctx_win` is used, no Seq Scan of `session_dims`, no Sort over the whole window for the key plan (PG 14 and 17).**

### T7 - Reader: `cost_usd` stored path (AC1, AC3, AC4 flags, AC5, AC12) [D1]

**Files:** `reader.py` (`get_cost_page`, `get_window_model_flags`), `cost_key_sql.py`; same test files.

- Order `coalesce(cost_key,0) DESC, session_id COLLATE "C"`; page and exact total from `session_dims_cost_key`. Flags (`metadata.unpriced`/`catalog_source`) from `GROUP BY (models_used, kind_masks)` over the window index, folded through the part 1 kind-to-rate table so results equal `get_window_model_flags`. Used only when stored `rates_version` equals the current one; else part 1 statement. Page rows still priced in Python (`price_session_models`).

**Test-first: yes - fails because cost sort still groups `cost_daily`: equality with the fallback for ids, order, total, `cost_usd`, `cost_available`, `bloat_pct`, `metadata.unpriced`, `catalog_source` on seeds containing ties (< 5e-10 apart), key-0 sessions, no-cost-row sessions, uncatalogued and missing-rate models; after a rate change the old-version rows are never served (fallback or re-priced).**

### T8 - Reader: framework stored path (AC1, AC4, AC5, AC9) [D4, D5]

**Files:** `reader.py` (`get_framework_page`), handler `service/analytics/handlers/cli_analytics_handler.py` only if the contract of `get_framework_window` changes.

- One pass `SELECT classifier_names, models_used, kind_masks, count(*) ... GROUP BY 1,2,3` over the window index (index-only). Python applies `classify_delivery_framework` (customer regex rule, unchanged) to the name sets; `total` = sum of accepted group counts; window flags from the same groups; page = ordered scan with a filter on accepted sets (`= ANY` of the accepted name arrays, verify the cheapest shape by EXPLAIN). Order of the page follows the requested sort (the `start_time` order uses the existing part 1 index).
- Unknown: real group count (prototype 132); note a cap/guard if it is large (verify, do not invent a limit).

**Test-first: yes - fails because the framework request still builds names per session: accepted ids, order, `total`, flags equal part 1 for a regex rule (not a name-prefix rule) including sessions with no names and with `search`; `EXPLAIN` asserts an Index Only Scan on `session_dims_fw_win`.**

### T9 - Benchmarks before/after (AC10, AC11, AC13, AC14, AC16)

**Files:** extend `scripts/cli_analytics_bench/run_sessions_bench.py`, `index_build_bench.py`; results to `<run_dir>/bench/`. **Test-first: no - measurement; outputs are the artifacts.**

- Seed as part 1 (AC9-style, about 1M+ sessions, history beyond the window, `ANALYZE`). For 7/30/90-day windows: default sort (no regression), `ctx_per_call`, `cost_usd`, framework; at least 20 runs for p50/p95; warm, `shared_buffers`-cold (restart), and OS-cache-cold (drop caches or fresh VM; record how). Per-statement times. Each line ends "825 ms met" or "limitation documented, requester approval needed". Prototype expectations only: ctx 104 ms, cost 663 ms (p95 ~1.0 s), framework 770 ms.
- Skew (AC11): `ctx_per_call` with key index only vs both on data whose top keys lie outside the window -> input to D4.
- Write cost (AC13): refresher batch throughput, ingest acquire-wait p99, 503 count, HOT ratio (`pg_stat_user_tables.n_tup_hot_upd`), table/index bloat after a refresher day, with and without the columns/indexes -> input to D2.
- Connections (AC14): at most 2 pool connections and 2 concurrent statements per request; K = 4 and 7 as in part 1 AC16.
- Sizes (AC16): `session_dims` and index sizes before/after vs prototype (1237 -> 1360 MB; ~700 MB indexes).

### T10 - Backfill, swap and re-price measurements (AC12, AC15)

**Files:** results in `<run_dir>/bench/backfill.md`; no source change unless a finding forces one. **Test-first: no - measurement.**

- On the seed with ingest and the refresher running: duration per phase, lock wait and swap lock time, WAL volume (`pg_current_wal_lsn` diff), catch-up result (rows ingested during the swap present and correct), plain index build lock wait vs `CONCURRENTLY` threshold (derive the row-count threshold like part 1 AC18), re-price duration and the fallback window (D1), cold behavior. Compare with prototype: 117 s + 89 s swap, 45-71 min batched. If D2 = side table or fillfactor, repeat T9 write cost after the change.

### T11 - Parity of other endpoints (AC17)

**Files:** `tests/codemie/service/analytics/handlers/test_cli_analytics_stored_keys_parity.py` (new; mirrors part 1 `test_cli_analytics_cost_efficiency_parity.py`).

**Test-first: yes - fails if overview, users, tools, efficiency, Cost and Efficiency results change with the columns present and the marker on versus off, on a fixed seed.**

### T12 - Guide, runbook, rollout and rollback (AC18)

**Files:** `.ai-run/guides/integration/cli-analytics-storage.md` (limits table, HOT note, Sessions list paging, index runbook). **Test-first: no - documentation.**

- Document: columns, indexes, who writes them, the readiness marker, fallback reasons and logs, backfill command and modes, re-price procedure, size, CONCURRENTLY pre-build threshold (T10), `indisvalid` check, indexes creatable by hand under the same names, updated "Known performance limits" table with measured results (T9), HOT consequence (do not edit the `a1c1a0000010` docstring).
- Rollout: (1) deploy migration (columns+indexes, reader still on fallback), (2) deploy refresher write path (T2), (3) run backfill (T4), (4) verify equality sample + NULL count zero, (5) set marker, deploy/enable reader, (6) watch fallback counter and p95. Rollback: marker off (no data change); full removal via migration downgrade. Re-price after a catalog change: run `reprice_cost_keys`; until finished cost sort falls back.

---

## AC coverage

| AC | Task | AC | Task |
|---|---|---|---|
| AC1 | T6, T7, T8 | AC10 | T9 |
| AC2 | T4 (equality), T2 | AC11 | T9 |
| AC3 | T6, T7 | AC12 | T3, T7, T10 |
| AC4 | T7 (flags), T8 | AC13 | T9 |
| AC5 | T5, T6, T7, T8 | AC14 | T9 |
| AC6 | T2 | AC15 | T4, T10 |
| AC7 | T1 | AC16 | T9 |
| AC8 | T4, T5 | AC17 | T11 |
| AC9 | T6, T8 (PG 14 and 17; confirm supported versions) | AC18 | T12 |

## Negative-constraint pass

- Non-goals (S3 partition pruning, S6 cancellation, other endpoints, runner CONCURRENTLY, default sort, search rule): no task touches them; T1 uses plain `CREATE INDEX IF NOT EXISTS`, T11 only checks parity.
- Stored `delivery_framework` label not used for the filter: T8 classifies name sets in Python with the customer's rule.
- No response model, router parameter or served-money change: T6-T8 keep page-row pricing in Python.
- Edit-in-place of `a1c1a0000010` not allowed: T1 is a new revision; its docstring is untouched (T12).
- Repository must not import service: rates are injected (T2, T3).
- No whole-suite gate, browser, review or commit-artifact tasks planned.
