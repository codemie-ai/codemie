# EPMCDME-16427 part 2: stored per-session sort keys for the CLI Analytics Sessions list

Authoritative inputs: `ticket-attachments/part2-spec.md`, `part2-plan.md`, `part2-prototype-findings.md`, `technical-analysis.md`. This spec adopts the attachment spec and corrects it where the current code (after `a1c1a0000010`) contradicts it. One run, as decided by the user.

## Why

Part 1 moved paging, sort and framework filter into PostgreSQL, but each of `sort_by=ctx_per_call`, `sort_by=cost_usd` and `framework=` still recomputes a per-session value over the whole window. On the final part 1 code at 30 days all three time out at 30 s (storage guide, "Final code, measured 2026-11-06"). The target is 825 ms. Fix: store the per-session values on `session_dims`, index them, and read them, falling back to today's statements whenever the stored values cannot be trusted.

## What

**Columns** (nullable, no default, added by new revision after head `a1c1a0000010`): `cache_read_total` bigint, `api_call_total` bigint, `classifier_names` text[] (ordered `COLLATE "C"`, NULL = no names), `models_used` text[], `kind_masks` int[] (per model in `models_used`, priced token kinds used), `cost_key` numeric (today's key, `round(..., 9)`), `rates_version` text. `rates_version` is the per-session stamp and the single readiness signal: non-NULL means "this session's stored columns are derived" (the repo's NULL = not derived convention, `session_activity.py`) and, for `cost_key` only, names the rate set that priced it. It carries readiness because "no names" and "no cost rows" are legitimately NULL elsewhere.

**Ctx key.** The stored ctx index and the stored read use exactly today's key: numeric `round((cache_read_total)/api_call_total, 9)`, and 0 (not NULL) when calls = 0 (`reader.py` `_sort_clause`). The attachment's float8/`nullif` expression is not used. Order stays key DESC, `session_id COLLATE "C"`.

**Window set.** Stored reads use the same window rule as part 1 (`sel`, the activity-based five-arm union with `has_activity`, `last_activity_day`, `resumed`), not "started_at in window". Index shapes (four indexes: ctx key, ctx window, cost key, framework window) are fixed at implementation against `sel` and verified by EXPLAIN (AC8); predicates copy `SHARED_PREDICATE` from `a1c1a0000010`. Arms the indexes cannot serve read `session_dims` as part 1 does, still from stored columns. The 825 ms target is assessed for the main arm (started in window with activity); the other arms (underived NULL, resumed, edge day) are expected to be small, and AC8/AC10 report each arm separately. An arm that misses is a documented limitation for approval, never silently accepted.

**Rates version.** One global version: a hash of the full rate set the injected rates callable returns (`ModelRates`, `ports.py:42`; `model_rates_for`). It is injected into the refresher, maintenance and reader like `classify`; the repository does not import the service. Deterministic (sorted models, fixed number formatting, so a catalog refresh with no real change keeps the version) and computed once per process, recomputed only when the callable's output changes, never per request.

**Control table.** One small row: the kill switch `enabled`, default off. No completion flag and no stored version: readiness is per session. Read once per request.

**Write path.** A step in `RollupRefresher._recompute`, same transaction, after `update_session_derived`/`derive_session_activity`, for sessions of keys carrying `_LOG|_USAGE|_SPANS|_SESSION` bits (`session_usage` is `_USAGE`, absent from `_SESSION_KEY_BITS`). One set-wise statement per batch; writes only when a value differs (`IS DISTINCT FROM`); it derives only sessions whose rows changed and reuses the names `update_session_derived` computes. Being in the recompute transaction, stored values and rollups commit together, so a failed or dropped key leaves both old. Stored names depend on `started_at`; a moved start re-derives them. `test_rollups.py` gate-vs-`_TRIGGER_BITS` check must hold. One definition of the kind masks is shared by the refresher and `cost_key_sql.py`.

**Backfill and re-price.** Run by the maintenance job (under its "maintenance" advisory lock, paced batches, the `backfill_session_activity` pattern at `maintenance.py:306`): sessions with `rates_version IS NULL` are derived, `FOR UPDATE SKIP LOCKED`, one transaction per batch; sessions whose `rates_version` differs from the current one get `cost_key` and stamp rewritten, so a catalog change heals without an operator. A CLI command in the postgres package (audit-log style of `backfill.py`) is a thin wrapper that takes the same lock. Covers sessions older than raw retention (totals come from `cost_daily` and `session_usage`). Idempotent, resumable, reports counts, duration and WAL. Indexes are built after the first backfill (plain build, `drop_if_invalid` + `IF NOT EXISTS`; the guide gives the CONCURRENTLY pre-build threshold).

**Reader (hybrid, per session).** `get_session_page` (ctx), `get_cost_page`, `get_framework_page` read stored columns for the window's fresh sessions and today's statements for the rest. Fresh = `rates_version` non-NULL (ctx, framework) or equal to the reader's current version (cost). The rest, set S (typically sessions ingested since the last refresher cycle, or priced by another rate set), is computed by today's per-session statements restricted to S's ids and merged with the stored rows by key DESC, `session_id COLLATE "C"`; page, `total` and flags come from the merged set. Equality guarantee: a fresh session's stored value equals what today's statement returns, because both derive from rollups committed in the same transaction as the stored value. If `enabled` is off, or `|S|` exceeds a cap (setting, default 5000), the request runs today's statements unchanged. Framework: one pass groups fresh sessions by `(classifier_names, models_used, kind_masks)`; names of S sessions (set-wise `classifier_names_by_session_sql`) join the same groups; Python applies `classify_delivery_framework` (unchanged) to name sets; `total` is the sum of accepted group counts; window flags come from the same groups; unpriced metadata scope stays the whole filtered window. Search, `is_unattributed` and viewer scopes are supported on the stored path when expressible over the stored columns, else they take the fallback. `cost_key` of sessions without cost rows is 0. Invariant: the page order (stored key) and displayed `cost_usd` use the same rate set, because a cost key priced by another rate set is never used.

**Observability.** No metrics facility exists in the package; each full fallback and each merge with S logs a rate-limited WARNING with reason (`disabled`, `s_over_cap`) and `|S|`.

**Rollout and rollback.** Migration (columns, control table), deploy refresher and maintenance before reader, backfill, build indexes, set `enabled`. Rollback: clear `enabled` (reader falls back, no data change); downgrade drops indexes, control table, columns. Re-enabling after a period on old code (which writes no stored columns): the enable command nulls the stamps of sessions active since the switch was cleared, so they re-derive as S.

## Acceptance criteria

- AC1: With the stored path in use, page ids, order, `total`, per-row `cost_usd`/`cost_available`/`bloat_pct`, `metadata.unpriced` and `catalog_source` equal today's statements for ctx, cost and framework, with and without `search` and `is_unattributed`, and for each viewer scope. Response model and router parameters unchanged.
- AC2: A live test compares every stored column with today's statements over a seeded window: 0 mismatches, covering no-cost-row sessions (key 0), zero-call sessions, subagent-totals-only sessions, ties, all five `sel` arms.
- AC3: `cost_key` equals `round(price_session_models, 9)` under the part 1 epsilon rule; the existing token/rate case table is reused.
- AC4: Framework accepted sessions, `total` and flags equal live classification and `get_window_model_flags`.
- AC5: Results equal today's statements for a window of only fresh sessions, a mixed window (fresh, NULL stamp, stamp of another rate set for cost) and a window of only S; `enabled` off and `|S|` over the cap return today's results and log the reason; NULL never sorts as zero except the `cost_key` rule.
- AC6: Refresher: after a recompute carrying `_LOG`, `_USAGE`, `_SPANS` or `_SESSION` bits the stored columns equal AC2 values (new `cost_daily` row, subagent total, new skill name, changed commands, nameless session); an unchanged recompute writes nothing.
- AC7: Migration tests extend `test_migrations.py`: nullable columns, indexes with `SHARED_PREDICATE` byte-equal to `a1c1a0000010`, idempotent upgrade, invalid index rebuilt, downgrade clean, `lock_timeout` restored, one head.
- AC8: Live EXPLAIN assertions, per `sel` arm: the three stored statements use the new indexes (no Seq Scan of `session_dims`, no Sort over the whole window for key-ordered plans); framework pass is an Index Only Scan on the framework index; Heap Fetches reported. The assertions run on every supported PostgreSQL version: 17 required, 14 run if available, otherwise documented as unverified for approval.
- AC9: Backfill fills all sessions, is resumable and idempotent, and is safe against the refresher and maintenance in both orders; a rates change followed by a maintenance run re-prices, and the reader never serves `cost_key` of another rate set in between. Re-price time and S-merge duration are recorded.
- AC10: Benchmarks (`scripts/cli_analytics_bench/`) for 7/30/90 days, warm p50/p95 (>= 20 runs) and cold, the three sorts plus the default sort (no regression), both on a quiescent database and under concurrent ingest, the latter stating the share of requests with non-empty S, `|S|` and the cap-fallback rate; each states "825 ms met, or limitation documented for approval". Includes skewed data with top keys outside the window (ctx indexes), framework group count, write cost (refresher step time and throughput, HOT ratio, bloat), backfill duration/WAL under ingest, index sizes, and `session_dims` size before and after. Under concurrent ingest, the ingest acquire-wait p99 and 503 count with the stored columns, indexes and a running backfill/index build are no worse than baseline, or the difference is documented for approval; the plain-index-build lock threshold is derived from that run.
- AC11: A request still uses at most 2 pool connections and 2 concurrent statements; other endpoints return identical results. Re-measured at K=4 and 7.
- AC12: Guide `.ai-run/guides/integration/cli-analytics-storage.md` documents columns, indexes, writers, control row, the stamp and S merge, fallback, maintenance backfill/re-price and its CLI, runbook, updated limits table and HOT note.

## Non-goals

- Partition pruning; request cancellation.
- Changing response model, router parameters, served money values, other endpoints, the default `start_time` sort or the search rule.
- Using stored `session_dims.delivery_framework` for the filter: the classifier is injected at request time and its rules can change, so a stored label would need re-derivation; `classifier_names` is rule-independent. Removing the `delivery_framework` writer is a separate follow-up, not in this run.
- CONCURRENTLY support in the migration runner.
- Build-and-swap backfill, a side table, a second-connection flags statement, fillfactor tuning (unless AC10 write-cost data later calls for them).
- Storing per-model token-kind totals to price at request time (rejected option b1; misses target).
- Editing the `a1c1a0000010` docstring.

## Decisions

- Cost key: stored priced `cost_key` + global `rates_version` over request-time token-kind arrays (misses target). Stale: re-priced by the scheduled maintenance backfill, served live for sessions in S meanwhile. Delegated, spec recommendation.
- HOT loss: accept it (option c) over fillfactor or side table; HOT is already lost for the activity facts; revisit only if AC10 write cost shows harm. Delegated, deviates from "decide after benchmark" by defaulting now.
- Backfill: in-place batched `FOR UPDATE SKIP LOCKED` over build-and-swap. Spec recommended swap; swap needs a write pause, unmeasured catch-up, and would drop constraints on a table that now carries activity facts and indexes, while the batched pattern already exists in the repo. Delegated, deviates from the spec recommendation.
- Backfill and re-price run in the maintenance job with the CLI as a wrapper, over an unscheduled standalone CLI. Review AR-1, AR-2.
- Keep both ctx indexes (key and window) until AC10 skew measurement. Delegated, spec recommendation.
- Framework unpriced metadata scope stays the whole filtered window (part 1 decision). Delegated, spec recommendation.
- Ticket: continue under EPMCDME-16427 as one run, over a linked ticket. — user
- Stored ctx key is today's numeric `round(,9)` with 0 on no calls over the attachment's float8 ratio, because the order and tie-breaks must match today's. Forced by current code.
- Stored reads use part 1's `sel` window over `started_at`-only windows. Forced by current code.
- Readiness is a per-session, version-aware `rates_version` stamp with per-session live fallback (set S merged with stored rows) over a window-wide all-or-nothing gate with a completion flag, because fresh sessions would send nearly every live request to the slow path. Review AR-1.
- The control row is only a kill switch, over a flag plus stored current version. Review AR-1.
- The current rates version is process-local, cached, over a stored version read per request. Review AR-2.
- A cap on `|S|` (default 5000) with whole-request fallback over unbounded merging. Agent call.
- Rate-limited WARNING over counters, because no metrics facility exists. Agent call.

## Assumptions and risks

- Prototype data is synthetic and pre-dates `sel`; timings are expectations, AC10 may end in a documented limitation.
- Global rates version marks all costs stale on any catalog change: the cost sort uses the S merge (and the cap fallback) until the maintenance re-price finishes; ctx and framework are unaffected.
- Pods on different code or catalog versions during a rolling deploy can each see the other's stamps as S; correct but slower until they converge.
- NULL-stamp rate under real ingest is unmeasured; AC10 measures it.
- Plain index builds hold a SHARE lock; threshold for the new indexes is unmeasured.
- Supported PostgreSQL versions: compose uses 17; 14 unverified.
