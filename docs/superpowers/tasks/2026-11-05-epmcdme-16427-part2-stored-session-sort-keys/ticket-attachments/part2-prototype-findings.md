# Technical Research

**Task**: EPMCDME-16427 part 2: stored per-session sort keys for the CLI Analytics Sessions list (`ctx_per_call` sort, `cost_usd` sort, framework filter)
**Generated**: 2026-11-05
**Research path**: prototype measurements plus code reading (facts only; no codegraph)
**Status of this document**: facts from part 1 of the ticket and from a scratch-database prototype. Items marked **[not verified]** were not checked in code or measured.

## 1. Why part 2 exists

Part 1 (branch `EPMCDME-16427_sessions-db-pagination`) moved paging, sorting and the framework filter into PostgreSQL. It shipped under approved limits (`.ai-run/guides/integration/cli-analytics-storage.md`, limits table): only the default `start_time` sort meets the 825 ms target. Part 1's spec (`docs/superpowers/tasks/2026-11-04-epmcdme-16427-cli-analytics-sessions-db-pagination/spec.md`, **Non-goals**) excludes "S4 materialized session facts or precomputed cost columns" and "using the stored `session_dims.delivery_framework` or `cost_daily.cost_usd` for list filtering or sorting". The guide states that meeting the target for the last three rows "needs a schema change and was out of scope".

Part 2 is that schema change.

## 2. State after part 1 (dev database, 30-day window, about 960k sessions of 1.9M, parallel off, noisy host)

| Request | p50 |
|---|---|
| `sort_by=start_time` (default) | about 0.14 s |
| `sort_by=ctx_per_call` | 12.6 s before part 1's covering index, 7.9 s after |
| `sort_by=cost_usd` | 22-24 s, p95 above the 30 s statement timeout |
| `framework=<name>` (`start_time` sort) | about 41 s before, about 23 s after the covering-index `sel` change |

Pool `statement_timeout` is 30 s (`CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS`, default 30000); a timeout surfaces as `QueryCanceledError` and HTTP 503.

Why they are slow (EXPLAIN, part 1):
- `ctx_per_call` key: `sum(cache_read_tokens) / sum(api_call_count)` per session over `cost_daily` (monthly range partitions, no day pruning because a session's token rows can precede its stored `started_at`), plus the subagent totals (`session_usage`). About 960k session groups; CPU-bound after the index-only `sel`.
- `cost_usd` key: per-(session, model) token grouping over the window's `cost_daily` rows, priced with catalog rates, summed per session (`cost_key_sql.py`; `session_cost_key_sql`, `cost_key_from_groups_sql`, `kind_flags_from_groups_sql`).
- Framework filter (`reader.py` `get_framework_page`): builds the classifier names of every session of the window (`classifier_names_by_session_sql`, `CLASSIFIER_NAMES_SQL` in `postgres/session_derived.py`: union of the keys of `skills`, keys of `agents`, `session_skills` names, `invocations_hourly` rows of kind 2, string elements of `commands`) into a temp table, reads the distinct name sets, the customer's regex rule runs in Python (`matching`), then the page is cut; plus a whole-window model-flags statement for `metadata.unpriced` / `catalog_source`.

## 3. Prototype: stored per-session columns (scratch PostgreSQL 17.11, 30-day window = 957,057 of 1,900,083 sessions)

Setup: scratch container, `shared_buffers` 128 MB, `work_mem` 32 MB, parallel workers off; copied read-only from the dev database: `session_dims`, `cost_daily` (15 partitions), `session_usage`, `session_skills`, `invocations_hourly` (kinds 2 and 4). Dev data is synthetic: three models carry the volume, `session_skills` is empty, skills/agents are NULL, so classifier names come only from `commands` and `invocations_hourly`; 21 distinct name sets. Rates came from the repo's `model_rates_for` (4 of 10 models priced; the 1h cache-write price is missing). Host load average 13-70 during the work. Scripts: `prototype-scripts/`; early numbers: `prototype-interim.md`.

### 3.1 Columns added to `session_dims` (all nullable)

| Column | Content | Stored size per session |
|---|---|---|
| `cache_read_total`, `api_call_total` | total cache-read tokens (cost rows plus subagent totals) and total API calls | 16 B |
| `classifier_names` (`text[]`) | the `CLASSIFIER_NAMES_SQL` result, ordered `COLLATE "C"`, NULL when empty | 58 B |
| `models_used` (`text[]`), `kind_masks` | raw model names of the session and which priced token kinds each used (the input of the unpriced flags) | 68 B |
| `cost_key` (numeric), `rates_version` (text) | the existing cost sort key (`round(..., 9)`) priced with the rate set named by `rates_version` (prototype: `'v1'`) | 11 B |

A variant "b1" stored six token-kind arrays instead of a priced key (190 B per session, 522 MB index): see 3.4.

### 3.2 Indexes (partial predicate = `SHARED_PREDICATE` of migration `a1c1a0000010`, definitions matched dev by md5)

| Index | Size | Build | Serves |
|---|---|---|---|
| `proto_ctx_key` `((ratio) DESC, session_id COLLATE "C") INCLUDE (started_at)` | 147 MB | 2.8 s | `ctx_per_call` page |
| `proto_ctx_win` `(started_at) INCLUDE (session_id, cache_read_total, api_call_total)` | 163 MB | 2.6 s | `ctx_per_call` via window scan plus top-N sort (463 ms request) |
| `proto_cost_key` `(coalesce(cost_key, 0) DESC, session_id COLLATE "C") INCLUDE (started_at)` | 148 MB | 3.3 s | cost page |
| `proto_cost_win` `(started_at) INCLUDE (session_id, cost_key)` | 147 MB | 2.8 s | never picked by the planner |
| `proto_fw_win` `(started_at) INCLUDE (session_id, classifier_names, models_used, kind_masks)` | 389 MB (437 MB after later updates) | 3.4-5.2 s | framework sets, flags, page |
| `proto_b1_win` `(started_at) INCLUDE (session_id, six token arrays)` | 522 MB | 3.9 s | b1 only |

Two key indexes plus the one wide framework window index are enough for all three requests. Existing indexes for comparison: `session_dims_started_shared` 41 MB, `session_dims_started_shared_cov` 130 MB (part 1).

### 3.3 Results (p50, warm; cold-ish = after `docker restart`, empty `shared_buffers`, warm OS cache, one run)

| Request | Today (scratch; dev) | Stored columns + indexes, warm | Cold-ish | Speed-up | Identical |
|---|---|---|---|---|---|
| `ctx_per_call` | 8.6 s (7.7-13.1 s); dev 7.9 s | page 1.8 ms + exact total 101 ms = about 104 ms (p95 about 112 ms) | 46 + 220 ms | about 80x | yes |
| `cost_usd`, stored priced key (b2) | 22.5 s; dev 22-24 s | page 1.6 ms + total 102 ms + flags 559 ms = 663 ms serial (flags p95 896 ms, request p95 about 1.0 s) | about 0.85 s | about 34x | yes |
| `cost_usd`, token kinds priced at request time (b1) | same | 8.7 s (p95 9.0 s) | 10.2 s | 2.6x | yes |
| framework (`start_time` + `superpowers*`) | 34.5 s (names 20-23 s, flags 10-13 s); dev 36-38 s | one group-by pass 768 ms (p95 807 ms) + page 2.2 ms = 770 ms | 1.29-1.66 s | about 45x | yes |

Page-detail statements add about 0.3 s to both today's and the new request. The 825 ms target holds warm for `ctx_per_call`, cost b2 (p50) and framework; it does not hold at p95 for cost b2, cold-ish for framework, or at all for b1. Running the cost flags statement on a second connection concurrently with page + total would give about 0.56 s (not measured).

Without the covering indexes the stored columns do not help: 12-14 s per pass (heap reads of wide rows), and the framework request was about 45.8 s.

Framework design that worked: one pass returns `GROUP BY (classifier_names, models_used, kind_masks)` (132 groups), Python matches the sets, the exact accepted total is the sum of group counts, the window flags come from the same groups, and the page is an ordered index scan with a filter on the accepted sets.

Planner choices for the `ctx_per_call` page with both indexes present: the key-ordered index for the 30-day window (1 ms) and 7-day window (81 ms); the window index plus sort for old, narrow windows (1.7-1.9 ms). The key index alone also worked on this uniform synthetic data (10 ms for old windows); on real skewed data a key-ordered scan with the window as a filter can degrade when the top keys are outside the window, so both indexes were kept.

### 3.4 Equality checks (stored versus today's statements)

- Whole-window key comparison over all 957,057 sessions: 0 mismatches for the `ctx_per_call` key, the cost key (b1 and b2) and the classifier names.
- Top-20 ids and order, exact totals and kind flags equal.
- Framework: 383,027 accepted sessions (same as today), same first 20 ids, same 7 accepted sets of 21. The accepted set in the prototype is every set containing a name starting with `superpowers` (case-insensitive), not the customer's regex rule.
- Per-row `CLASSIFIER_NAMES_SQL` on a 4,000-session sample equals the set-wise stored result.
- No ties, rounding, NULL or totals-only subagent mismatches. 18 sessions have no cost rows (NULL stored, key 0 on read); 76 sessions have key 0.

### 3.5 Backfill (scratch, UNLOGGED copy, `maintenance_work_mem` 512 MB, `work_mem` 256 MB for those steps)

- Groups A + C (totals, names, models, masks) by `CREATE TABLE AS` and swap: 117 s total (staging 8.7 + 28.2 + 10.4 + 11.8 s; CTAS 34.7 s; swap + primary key 8.5 s; five secondary indexes 7.7 s; VACUUM 5 s).
- Groups B1 + B2 by `CREATE TABLE AS` and swap: 89 s (staging 12.0 s and 5.5 s; CTAS 44.6 s).
- In-place whole-table `UPDATE` of group A: 799 s with only the primary key present (table grew 1237 MB to 2329 MB with dead space plain VACUUM does not return); with the five secondary indexes it did not finish in more than 22 minutes, twice. About 75% of samples were `DataFileRead` (I/O bound; the VM has about 1.3 GB of page cache).
- In-place batched `UPDATE` by `session_id` range (100k sessions, batches of 10k, committing between): 705 rows/s without the new indexes (about 45 min for 1.9M rows), 448 rows/s with three of the new indexes (about 71 min).
- Conclusion of the prototype: build-and-swap (about 2 minutes) is far better than any in-place backfill. The scratch table was UNLOGGED, so WAL cost of updates and index builds is understated.

### 3.6 Refresher cost and HOT updates

- Recompute of one session (`cost_daily` plus `session_usage` totals plus names plus priced key plus masks), p50 over 200 random sessions: 4.3-4.5 ms (p95 6.5-8 ms).
- One-row `UPDATE` rewriting the stored columns:

| Case | p50 | HOT updates |
|---|---|---|
| Without the new indexes, pass 1 (full pages) | 3.1 ms | 1 of 200 |
| Without the new indexes, pass 2 | 1.7 ms | 165 of 200 |
| With the new indexes, pass 1 | 5.0 ms | 0 of 200 |
| With the new indexes, pass 2 | 2.7 ms | 0 of 200 |

The new indexes add about 1.0-1.9 ms per row (1.6-1.8x). The part 1 migration docstring (`a1c1a0000010`) argues that the shared indexes keep HOT updates possible because they cover columns written once. The new indexes include columns the refresher rewrites, so HOT is lost for every refresher update. Options named by the prototype: a lower fillfactor, or a narrow side table keyed by `session_id` that keeps `session_dims` HOT.

### 3.7 Table size

`session_dims` 1237 MB originally; 1360 MB after groups A + C; 1856 MB after adding the b1 and b2 groups (the b1 arrays are most of the increase). Existing indexes 446 MB; the new indexes add about 700 MB for ctx key, cost key and framework window.

## 4. Open risks and unmeasured items from the prototype

- Cost b2 stales on any catalog or rate change: every stored `cost_key` is invalid until re-priced. Needs a `rates_version` check and a full re-price (staging took 5.5 s in the prototype, but it rewrites the column), or a fallback to today's statement while the versions differ. The fallback and the re-price cost after a catalog change were not measured.
- Skewed real data for the key-ordered index plan was not measured (synthetic uniform data only).
- Exact-money rounding: the key is `round(..., 9)` and matched exactly on all keys here; the spec's "served money values unchanged" rule still applies.
- p95 values come from 3-5 runs and are close to the maximum; cold-ish means `shared_buffers` only, not an OS-cache-cold run.
- Defaults, NOT NULL and constraints other than the primary key were dropped by `CREATE TABLE AS` in the prototype (indexes were recreated and match dev).
- Concurrency with ingest and the refresher during a real backfill, and the lock time of the swap, were not measured. **[not verified]** how the real refresher writes `session_dims` (batch size, which job recomputes `delivery_framework`, dirty-bit handling); the guide says `active_ms`, `source_cost_usd`, `delivery_framework`, `feature_id` are rollup-derived and recomputed each time, and a rebuild via `python -m codemie.repository.cli_analytics.postgres.backfill --from ... --to ... [--now]` exists. The implementer must read the refresher before designing the write path.

## 5. Where the code lives (pointers; read before designing)

- Reader: `src/codemie/repository/cli_analytics/postgres/reader.py` (`_sessions_cte` with `ids_only` / `with_start`, `_ordered_window`, `get_session_page`, `get_cost_page`, `get_framework_page`, `get_window_model_flags`, `_narrows_window`).
- Cost key SQL: `src/codemie/repository/cli_analytics/postgres/cost_key_sql.py`.
- Classifier names and rollup-derived columns: `src/codemie/repository/cli_analytics/postgres/session_derived.py`.
- Migration of part 1 (three session-list indexes): `src/external/alembic_cli_analytics/versions/a1c1a0000010_cli_analytics_session_list_indexes.py`; its tests: `tests/codemie/repository/cli_analytics/postgres/test_migrations.py`.
- Handler: `src/codemie/service/analytics/handlers/cli_analytics_handler.py` (`get_sessions`, `_read_window`, `_ctx_per_call`).
- Router: `src/codemie/rest_api/routers/cli_analytics.py` (`get_sessions`).
- Storage guide: `.ai-run/guides/integration/cli-analytics-storage.md` (Sessions list paging, index runbook, limits table, rollup-derived columns).
- Bench: `scripts/cli_analytics_bench/run_sessions_bench.py` (latency | explain | connections) and `index_build_bench.py`.
- Part 1 task folder: `docs/superpowers/tasks/2026-11-04-epmcdme-16427-cli-analytics-sessions-db-pagination/` (spec, plan, bench notes).

## 6. Constraints carried over

- Response model, router parameters and served money values do not change (part 1 non-goal, still the rule unless the part 2 spec states otherwise).
- The framework rule is the customer's regex config and stays in Python.
- Migrations: CLI analytics Alembic chain; the part 1 head is `a1c1a0000010` (unmerged at the time of writing; part 2's `down_revision` must follow whatever head exists when it is written).
- Money values and `cost_available` / unpriced-model semantics must stay exactly as today.
