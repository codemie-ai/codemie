# EPMCDME-16427 part 2: stored per-session sort keys for the CLI Analytics Sessions list

Status: DRAFT. The requester has accepted only that this is a follow-up to part 1, that it needs a schema change (new nullable columns and indexes on `session_dims`), and that nothing is implemented now. Every design choice below is **Proposed** until the requester answers "Open decisions for the requester". Numbers come from `technical-analysis.md` (scratch-database prototype, PostgreSQL 17.11, 30-day window = 957,057 of 1,900,083 sessions, synthetic data, noisy host). Anything not measured there is marked **[not measured]** and is either an AC (a measurement the implementation takes) or a risk.

## Why

Part 1 (branch `EPMCDME-16427_sessions-db-pagination`) moved paging, sorting and the framework filter into PostgreSQL. Only the default `start_time` sort meets the 825 ms target (about 0.14 s). Shipped under approved limits (storage guide, limits table):

| Request (30-day window, dev DB) | p50 today |
|---|---|
| `sort_by=ctx_per_call` | 7.9 s |
| `sort_by=cost_usd` | 22-24 s, p95 above the 30 s statement timeout (HTTP 503) |
| `framework=<name>` | about 23 s |

The cause is that each request recomputes a per-session value over about 960k sessions: sums over `cost_daily` and `session_usage`, per-(session, model) pricing, and the classifier names of every session (`reader.py` `get_session_page`, `get_cost_page`, `get_framework_page`; `cost_key_sql.py`; `session_derived.py` `CLASSIFIER_NAMES_SQL`). Part 1's spec lists "S4 materialized session facts or precomputed cost columns" as a non-goal. This is that change.

## What (all Proposed)

**S1: columns.** Nullable columns on `session_dims`, no defaults, no NOT NULL. NULL means "not computed yet", except where stated.

| Column | Type | Content |
|---|---|---|
| `cache_read_total` | bigint | cache-read tokens: `cost_daily` rows plus `session_usage` subagent totals |
| `api_call_total` | bigint | API calls, same sources |
| `classifier_names` | text[] | `CLASSIFIER_NAMES_SQL` result, ordered `COLLATE "C"`; NULL when the session has no name |
| `models_used` | text[] | raw model names of the session |
| `kind_masks` | int[] | per model in `models_used`, which priced token kinds it used (input of the unpriced flags) |
| `cost_key` | numeric | today's cost sort key, `round(..., 9)`, priced with the rate set `rates_version` |
| `rates_version` | text | identifies the rate set `cost_key` was priced with |

The prototype stored 16 B (totals), 58 B (names), 68 B (models + masks) and 11 B (cost key, version) per session. Exact types and the bitmask layout are fixed at implementation; the ones above are a proposal (the prototype's types are not recorded in the analysis). Because "no names" and "no cost rows" are stored as NULL, readiness cannot be read from the columns: see S6.

**S2: indexes.** All partial with `SHARED_PREDICATE` of `a1c1a0000010` (copied into the new migration, not imported, because a migration must not change with the code). Prototype sizes and build times:

| Index | Size | Build | Serves |
|---|---|---|---|
| key: `((cache_read_total::float8 / nullif(api_call_total,0)) DESC, session_id COLLATE "C") INCLUDE (started_at)` (exact ratio expression must equal today's key) | 147 MB | 2.8 s | `ctx_per_call` page |
| window: `(started_at) INCLUDE (session_id, cache_read_total, api_call_total)` | 163 MB | 2.6 s | `ctx_per_call` for old/narrow windows (463 ms request) |
| cost key: `(coalesce(cost_key, 0) DESC, session_id COLLATE "C") INCLUDE (started_at)` | 148 MB | 3.3 s | cost page |
| framework window: `(started_at) INCLUDE (session_id, classifier_names, models_used, kind_masks)` | 389-437 MB | 3.4-5.2 s | framework sets, flags, page |

Total new index size about 700 MB (ctx key, cost key, framework window) next to 446 MB of existing indexes and a 1237 MB table (1360 MB after the totals/names/models columns). A cost window index was never picked by the planner and is dropped from the proposal. Without covering indexes the stored columns do not help (12-14 s per pass, framework about 45.8 s).

**S3: migration.** New revision `a1c1a0000011` after head `a1c1a0000010` (the `down_revision` is repointed if another revision lands first). Same conventions as `a1c1a0000010`: `SET LOCAL lock_timeout = '5s'` once, `ALTER TABLE session_dims ADD COLUMN IF NOT EXISTS ...` (nullable, no default, so no table rewrite), `drop_if_invalid` then `CREATE INDEX IF NOT EXISTS` per index, then restore the runner's `LOCK_TIMEOUT` (`migrations.py:39`). Downgrade drops the indexes, then the columns, with `IF EXISTS`. Indexes are built after the backfill, not in the migration, if decision 3 chooses build-and-swap (see S5).

**S4: write path.** The refresher is `RollupRefresher._recompute` (`rollups.py:2254`): after the `RECOMPUTE` statements it calls `update_session_derived(conn, sessions, classify, timeout)` (`session_derived.py:135`) for the sessions of keys with `_SESSION_KEY_BITS` (`rollups.py:803`, `_LOG | _SPANS | _METRICS | _SESSION`). Proposal: a sibling step in the same transaction that recomputes the stored columns of the affected sessions, one set-wise statement per batch. It runs for keys with `_LOG | _USAGE | _SPANS | _SESSION` bits, because `cost_daily` is written under `_LOG`, `session_usage` under `_USAGE` (not in `_SESSION_KEY_BITS`, so reusing that set would miss subagent totals), `invocations_hourly` under `_SPANS | _LOG`, `session_skills` under `_LOG` (`_TRIGGER_BITS`, `rollups.py:2396`). Rates reach the refresher the way `classify` does: an injected callable (the repository must not import the service). Prototype: recompute of one session 4.3-4.5 ms p50 (p95 6.5-8 ms); a one-row update adds 1.0-1.9 ms with the new indexes. A write must not change a row whose values are equal (as `_WRITE_DERIVED` does with `IS DISTINCT FROM`).

**S5: backfill.** The existing `python -m codemie.repository.cli_analytics.postgres.backfill` marks (day, session) keys dirty inside raw retention only; the stored totals span `cost_daily` and `session_usage`, which outlive raw retention, so it cannot fill old sessions. Proposal: a dedicated command (same package, same audit-log style) that fills the columns for all sessions. Method per decision 3. Prototype (scratch, UNLOGGED, so WAL cost understated): build-and-swap about 117 s for totals/names/models/masks plus 89 s for the cost groups; in-place batched 45 min (no new indexes) to 71 min (three new indexes) extrapolated; whole-table in-place `UPDATE` 799 s without secondary indexes, unfinished after 22 minutes with them.

**S6: reader and cutover.** `get_session_page` (`ctx_per_call`), `get_cost_page` (`cost_usd`) and `get_framework_page` read the stored columns when, and only when, a readiness marker says the backfill is complete (and, for cost, the stored `rates_version` equals the current rate set). Otherwise they run today's statements unchanged. Proposed marker: one row in a small control table (backfill complete, current `rates_version`), read once per request; the exact home is fixed at implementation. NULL never sorts as zero except `cost_key`: today's key is 0 for sessions without cost rows (18 such sessions in the prototype), kept by `coalesce(cost_key, 0)`. Framework: one pass returns `GROUP BY (classifier_names, models_used, kind_masks)` (132 groups in the prototype); Python applies the customer's regex rule (`classify_delivery_framework`, unchanged) to the name sets, the exact `total` is the sum of accepted group counts, the window flags come from the same groups, and the page is an ordered index scan filtered to the accepted sets. Served `cost_usd`, `cost_available`, `bloat_pct` are still priced in Python for the page rows. Response model, router parameters, viewer scope, search, and the `metadata.unpriced` / `catalog_source` scope (whole filtered window for the cost sort and the framework filter, page-scoped otherwise) are unchanged.

**S7: rollout, rollback, runbook.** Order: deploy the migration (columns only), run the backfill, build the indexes, flip the marker, deploy/enable the reader. Rollback is flipping the marker off (the reader falls back to today's statements; no data change). Columns and indexes are dropped by the downgrade. The storage guide gains: the CONCURRENTLY pre-build threshold for the new indexes (a plain build takes a SHARE lock; derive the threshold as in part 1 AC18, **[not measured]** for these indexes under ingest), the `indisvalid` check, the note that indexes can be created by hand under the same names, the backfill command, and the re-price procedure.

**S8: observability.** A counter and a log line per request that took the fallback, labelled by reason (`not_ready`, `rates_version_differs`, `stored_null`), and a gauge/log of sessions with NULL stored columns inside the window. Whether the project has a metrics facility to attach them to is **[not verified]**; at minimum a rate-limited WARNING.

## Open decisions for the requester

Nothing below is approved. Recommendations are the prototype's.

1. **Cost key.** (a) Stored priced `cost_key` + `rates_version` ("b2"): 663 ms warm serial (page 1.6 ms, total 102 ms, flags 559 ms), request p95 about 1.0 s (misses 825 ms at p95), cold-ish about 0.85 s, 34x. (b) Token-kind arrays priced at request time ("b1"): 8.7 s (p95 9.0 s), cold 10.2 s, 2.6x, 190 B per session and a 522 MB index; does not meet the target. Recommend (a). Stale-on-rate-change handling, if (a): either a version check plus a re-price job (staging took 5.5 s in the prototype, but it rewrites the column and the re-price cost was **[not measured]**), or fall back to today's statement (22-24 s, may time out) while versions differ. Recommend version check + re-price job, with the fallback only for the duration of the re-price. Running the flags statement on a second connection would give about 0.56 s **[not measured]**; part 1 caps a request at 2 connections, so it needs its own decision if wanted.
2. **HOT loss.** The new indexes include columns the refresher rewrites: 0 of 200 updates HOT, versus 165 of 200 (pass 2) without them; per-row update 5.0 / 2.7 ms versus 3.1 / 1.7 ms. Part 1's `a1c1a0000010` docstring relied on HOT staying possible. Options: (a) lower `session_dims` fillfactor (cost: larger table; HOT benefit **[not measured]**); (b) a narrow side table keyed by `session_id` holding the seven columns, indexed there, which keeps `session_dims` HOT (cost: a join in every read path and a second table to purge in `session_purge.py`); (c) accept the loss (cost: 1.6-1.8x per-row write cost, more bloat, VACUUM load **[not measured]**). Recommend (b) if the write-amplification benchmark (AC16) shows bloat or ingest slowdown, otherwise (c); decide after AC16.
3. **Backfill method and cutover.** (a) Build-and-swap (`CREATE TABLE AS` + swap, about 2 min in the prototype for totals/names/models/masks, plus 89 s for cost; needs a write pause or a catch-up step for rows ingested during the swap, **[not measured]**, and recreation of non-index constraints and defaults, which `CREATE TABLE AS` dropped in the prototype). (b) In-place batched `UPDATE`, 10k per batch: 45-71 min, no pause, slower with the indexes (build them after). Recommend (a) with a catch-up through the refresher; (b) is the safe fallback. Cutover rule under either: stored path only when the marker says complete; otherwise today's statements.
4. **Keep both `ctx_per_call` indexes?** Key-ordered index only (147 MB) worked on uniform synthetic data (10 ms for old windows); on real skewed data a key-ordered scan with the window as a filter can degrade when the top keys lie outside the window (**[not measured]**). Window index (163 MB) bounds that case. Recommend keeping both until AC15 is measured on skewed data.
5. **Unpriced-model metadata scope for the framework filter.** Stays the whole filtered window (part 1 decision, user, 2026-11-04) unless reversed. Reversing would drop the flags from the framework pass; not recommended.
6. **Ticket.** Continue under EPMCDME-16427 (requester calls it "part 2 of this ticket") or open a separate ticket. Recommend a sub-task or linked ticket, because part 1 is unmerged and this one is not scheduled; the requester decides.

## Acceptance criteria

- AC1: For `sort_by=ctx_per_call`, `sort_by=cost_usd` and `framework=`, with and without `search` and `is_unattributed`, when the stored path is used, the page ids, their order, `total`, per-row `cost_usd` / `cost_available` / `bloat_pct`, `metadata.unpriced` and `catalog_source` equal the part 1 statements' results on the same data. Response model and router parameters are unchanged.
- AC2: A live equality test over every session of a seeded window compares the stored `cache_read_total`, `api_call_total`, `classifier_names`, `models_used`, `kind_masks` and `cost_key` with today's statements: 0 mismatches (prototype: 0 over 957,057 sessions). It includes sessions without cost rows (stored NULL, key 0), key-0 sessions, subagent totals-only sessions and ties.
- AC3: Order is the key DESC, then `session_id COLLATE "C"`. `cost_key` equals `round(price_session_models cost, 9)` within the part 1 epsilon rule; the existing token/rate case table is reused for the SQL pricing of the stored key.
- AC4: The framework pass groups by `(classifier_names, models_used, kind_masks)`; accepted sessions and `total` equal today's live classification for the customer's regex rule (the prototype used a name-prefix rule, not the customer's), and the window flags equal `get_window_model_flags`.
- AC5: Fallback: with the readiness marker off, with NULL stored columns in the window, or with a different `rates_version` (cost sort only), each of the three requests returns today's results through today's statements; a test per reason also asserts the fallback counter (S8). NULL is never ordered as zero except the `cost_key` rule in S6.
- AC6: Refresher: after a recompute of keys carrying `_LOG`, `_USAGE`, `_SPANS` or `_SESSION` bits, the sessions' stored columns equal the AC2 values. Tests: a new `cost_daily` row, a subagent `session_usage` total, a new skill name, a changed `commands` array, a session without names. An unchanged recompute writes no row.
- AC7: Migration tests (extending `test_migrations.py`): upgrade from `a1c1a0000010` adds the columns nullable and the indexes with `SHARED_PREDICATE` byte-equal to `a1c1a0000010`; upgrade is idempotent; an invalid leftover index of the same name is dropped and rebuilt; downgrade removes them; `lock_timeout` is restored to the runner's value; the chain has one head.
- AC8: The backfill command fills all sessions including those older than raw retention, is resumable and idempotent, reports counts, logs an audit line, and sets the readiness marker only when no session in the window has NULL columns that today's statements would not produce NULL for.
- AC9: Live tests on PostgreSQL 14 and 17 (the versions the project supports; **[not verified]** the supported versions, confirm) on a bulk-seeded database use `EXPLAIN` assertions: the three page statements use the key-ordered or window index (no `Seq Scan` of `session_dims`, no `Sort` over the whole window for the key-ordered plans), and the framework pass is an Index Only Scan on the framework window index.
- AC10: Before/after benchmarks on the part 1 AC9-style seed record, for 7-, 30- and 90-day windows: warm p50 and p95 (at least 20 runs, because the prototype's p95 came from 3-5 runs), and cold (empty `shared_buffers`; also OS-cache-cold, which the prototype did not do), for the three requests plus the default sort (no regression). Each states "825 ms met, or limitation documented and approved by the requester". Prototype expectations, not results: `ctx_per_call` about 104 ms, cost about 663 ms (p95 about 1.0 s), framework about 770 ms.
- AC11: Skewed-data check for decision 4: the `ctx_per_call` page is measured with the key index only and with both, on data where the top keys lie outside the window.
- AC12: Rates change: after a catalog/rate change the cost sort returns today's values (fallback or re-priced, per decision 1) and never serves a `cost_key` priced with another rate set; the re-price time and the fallback duration are measured on the seed.
- AC13: Write cost: refresher batch throughput and ingest latency (acquire wait p99, 503 count) are measured with and without the new columns and indexes, with ingest running; HOT-update ratio and table/index bloat after a refresher day are recorded, as input to decision 2.
- AC14: Connection use: a Sessions request still holds at most 2 pool connections and runs at most 2 statements concurrently (part 1 AC10); re-measured at K = 4 and 7 as in part 1 AC16.
- AC15: Backfill run on the seed under concurrent ingest and the refresher: duration, lock wait and swap lock time (if build-and-swap), WAL volume, and the catch-up result are recorded; the runbook threshold for the plain index build is derived from them.
- AC16: Size: `session_dims` and index sizes before/after are recorded and compared with the prototype (table 1237 -> 1360 MB for totals/names/models; about 700 MB of new indexes).
- AC17: Other endpoints (overview, users, tools, efficiency, Cost and Efficiency views) return the same results before and after on a fixed seed.
- AC18: Docs: `.ai-run/guides/integration/cli-analytics-storage.md` documents the columns, indexes, who writes them, the readiness marker, the fallback, the backfill command, the runbook, and updates the "Known performance limits" table and the HOT note; the `a1c1a0000010` docstring claim is not edited (the new revision's docstring states the HOT consequence).

## Non-goals

- S3 partition pruning.
- S6 request cancellation.
- Changing the response model, router parameters or served money values.
- Using the stored `session_dims.delivery_framework` label for the filter: the rule is the customer's regex config in Python, so the filter needs name sets.
- Changing other endpoints.
- Adding CONCURRENTLY support to the migration runner.
- Changing the default `start_time` sort or the search rule.

## Decisions

- Part 2 is a follow-up to part 1 with a schema change (nullable columns and indexes on `session_dims`); nothing implemented now. Answers whether stored facts are in scope. — user
- Stored columns plus covering indexes over further query tuning, because without covering indexes the columns did not help (12-14 s). Answers how to meet 825 ms. — agent (prototype), Proposed
- Classifier name sets are stored, not the label, and the regex rule stays in Python. Answers how the framework filter stays exact. — agent, Proposed
- Fallback to today's statements whenever stored values are absent, incomplete or stale, over serving partial results. Answers cutover safety. — agent, Proposed
- Decisions 1-6 under "Open decisions for the requester": none made.

## Assumptions and risks

- Prototype data is synthetic and uniform (3 models carry the volume, `session_skills` empty, 21 distinct name sets, 4 of 10 models priced); real name-set cardinality, and so the framework group count, is **[not measured]**.
- The cost p95 (about 1.0 s) and the cold-ish framework run (1.29-1.66 s) miss 825 ms even in the prototype; AC10 may end in an approved limitation.
- The prototype ran on a noisy host (load average 13-70), 3-5 runs per p95; cold-ish means `shared_buffers` only.
- How the real rate set is versioned is **[not verified]**: no code was read for a catalog version; the implementation must define `rates_version` (for example a hash of the DTO rates for the models in use).
- Concurrency of a real backfill/swap with ingest and the refresher, the swap lock time, and WAL volume are **[not measured]**.
- `session_dims` writers other than the refresher (`session_binding.py`, `session_purge.py`, `maintenance.py`, `ingestor.py`) were not read for effects on the new columns; purge must delete or ignore them with the row.
- Money-value parity relies on `round(..., 9)` matching (0 mismatches in the prototype).
- Part 1 is unmerged, so the revision id and `down_revision` may change.
