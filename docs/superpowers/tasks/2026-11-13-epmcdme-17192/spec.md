# EPMCDME-17192: Stale datasource deletion, oldest-first order and activity re-check

## Goal

The nightly deletion phase (`StaleDatasourceService._delete_stale_indexes`, `src/codemie/service/stale_datasource/stale_datasource_service.py:384`) selects idle STALE rows with no ordering (`:401-425`) and never looks at activity again. The per-run index limit (`:451-458`) defers an arbitrary subset, and a datasource used again after being marked stale still loses its index; today only a reindex (`IndexInfo.complete_progress`, `rest_api/models/index.py:646`) brings it back.

Delete the longest-stale indexes first, and re-check each index's datasources right before deleting it, sparing any that became active again.

## Behavior

Inside `_delete_stale_indexes`:

1. Select idle STALE rows (unchanged filter). None: return the zero stats (every key, `reactivated` and `recheck_errors` included, as `0`), no metrics query.
2. Shared-index guard, before the re-check and the limit (unchanged): rows whose index (`get_index_identifier()`) is used by an ACTIVE row are skipped and counted in `skipped_shared_index`. Skipped rows are not re-checked.
3. Group the rest by index and order the groups (see Ordering). The walk state from here on is plain values: an ordered list of (index name, row ids).
4. Fetch metrics once for all remaining rows, before any ES delete (see Re-check).
5. Walk the groups lazily. For each group: re-select its rows by id with the idle-STALE filter (rows loaded after the previous group's commit); if fewer rows come back than the group had, the group changed under the run (reindex started, reactivated elsewhere): keep the index, log, no limit slot. Otherwise re-check each row against the step-4 signals plus its fresh `update_date`:
   - any row errors or is recently active: reactivate the recently active rows (STALE -> ACTIVE, `marked_stale_at` cleared, one UPDATE guarded by `lifecycle_state == STALE`), keep the index, the other rows stay STALE, no limit slot;
   - all rows inactive and none errored: delete the ES index (`ignore_unavailable=True`, run in a thread as today); on success archive the rows (guarded UPDATE, `:502-508`), then count the index against `STALE_DATASOURCE_MAX_DELETIONS_PER_RUN`. A failed ES delete counts `deletion_errors`, leaves rows STALE and uses no slot.
6. Stop when the number of deleted indexes reaches the limit, the re-check bound is reached, or the groups are exhausted. `deletion_capped=True` when unvisited groups remain.

**Commit and session.** One commit per group that wrote anything (reactivation and/or archive); a group that wrote nothing does not commit. The session is a plain `AsyncSession` with default `expire_on_commit` (`src/codemie/clients/postgres.py:422`), so every row loaded before a commit is expired after it. Nothing is read from an expired row: step 3 keeps only strings, ids and sort keys, step 5 re-selects each group, and everything a group needs after its commit (metrics payloads, counts) is captured as plain values before it. `STALE_DATASOURCE_INDEX_DELETED` metrics are emitted after the group's commit. A DB or commit error propagates to the outer handler (`:204-207`), which rolls back the open group; groups committed earlier stay.

**Re-check.** Same signals and threshold as detection: lifecycle events, tool usage and `update_date`, folded to the latest timestamp, then `_is_datasource_stale` with `STALE_DATASOURCE_NO_USAGE_DAYS`. The two fetches become one shared method returning both maps, and the per-row fold (inline at `:169-175`) becomes a second method; detection calls both with its own per-row error handling, behavior unchanged. Both fetchers scan the whole metrics index and use the row list only to match buckets (`:283-355`), so scoping them per group would cost two full scans per group; metrics are fetched once, which is also why a group's signals can be minutes old by the time it is reached. No signal at all (`:371-372`) is inactive.

**Failures.**
- Metrics store failure: the fail-closed fetchers raise at step 4, before any reactivation or ES delete; the exception propagates and the outer handler rolls back and re-raises. The marking commit (`:192`) stays.
- Only a raised fetch error is a metrics store failure. A response with no `result` aggregation (what a missing metrics index returns, `metrics_elastic_repository.py:199-201`), failed shards or a timeout is not: the re-check reads it as "no signal", exactly as detection does, and the walk proceeds.
- Per-row error while folding a row: the row stays STALE, is counted in `recheck_errors`, and its group's index is not deleted and uses no slot. This covers re-check errors only.

**Ordering.** Rows sort by (stale-mark day, last-update, id), all ascending: stale-mark day is the UTC date of `marked_stale_at`; last-update is `update_date`, else `date`; NULL sorts before any value at both levels; `id` breaks ties. A group takes the minimum key of its rows. Sorting is in Python on the loaded rows.

**Re-check bound.** The walk visits at most 5 times `STALE_DATASOURCE_MAX_DELETIONS_PER_RUN` groups per run, whatever each outcome (module constant, no new setting).

**Summary.** Stats gain `reactivated` (sum of the guarded UPDATE rowcounts) and `recheck_errors`, always present. `scheduler.py:79-86` prints both. `stale_rows_swept` still counts all idle rows selected in step 1.

## Acceptance Criteria

- AC1: Groups are walked by stale-mark day ascending, then last-update (`update_date`, else `date`) ascending; no `marked_stale_at` first, no dates first within a day; a group is positioned by its earliest row.
- AC2: The shared-index guard runs before the limit and before any re-check; skipped rows are neither re-checked nor reactivated.
- AC3: A group's rows are re-checked just before its index would be deleted, with detection's signals and `STALE_DATASOURCE_NO_USAGE_DAYS`; metrics are fetched once, before the first ES delete.
- AC4: A group with a recently active row: those rows return to ACTIVE with `marked_stale_at` cleared, the index is kept, the other rows stay STALE and are not archived, and the group uses no limit slot.
- AC5: A group whose rows are all inactive and error-free has its index deleted and its rows archived, and only then counts against the limit; the limit counts deleted indexes. The walk stops at the limit; `deletion_capped=True` when groups remain unvisited.
- AC6: A row with no signal at all is inactive.
- AC7: A metrics fetch that raises leaves no row reactivated, deleted or archived, and the error reaches the caller. A response with no `result` aggregation, failed shards or a timeout does not raise and is read as "no signal", as in detection.
- AC8: A per-row error leaves that row STALE, counted in `recheck_errors`; its group's index is kept with no slot; other groups proceed.
- AC9: A group whose row set changed since step 1 is skipped with its index kept and no slot.
- AC10: The walk visits at most 5 times the per-run limit in groups; `deletion_capped=True` if groups remain.
- AC11: No row object is read after a commit that loaded it; each group commits once if it wrote anything, and metrics are emitted after that commit.
- AC12: Reactivation and archive UPDATEs are guarded by `lifecycle_state == STALE`; `reactivated` counts only changed rows; rows that stay STALE are not written.
- AC13: The run summary log includes `reactivated` and `recheck_errors`, also on a night with no idle STALE rows.
- AC14: Detection results are unchanged by the extraction.

## Non-goals

- Persisting a "last used" timestamp.
- Changing `STALE_DATASOURCE_NO_USAGE_DAYS`, other thresholds or `STALE_DATASOURCE_MAX_DELETIONS_PER_RUN`.
- Restoring deleted indexes.
- Protecting indexes shared with reindexing, queued or errored datasources ("errored" means `IndexInfo.error`, not a re-check error).
- Re-checking or reactivating rows the shared-index guard skips.
- Calling `IndexInfo.complete_progress`, or changing `IndexInfo` columns, the schema or a migration.
- Scoping the metrics fetchers per group or datasource.
- Cleaning up the dead `deletion_aborted` branch or printing `deletion_capped` in the scheduler.
- Using `STALE_DATASOURCE_NO_UPDATE_DAYS`.
- Changing detection's treatment of a missing or partial metrics response.

## Decisions

- Re-check each index group's datasources lazily, immediately before deleting its index, over re-checking all idle STALE rows up front: the requester's design; the up-front design is rejected (it needed a reactivation commit followed by work on a pre-commit snapshot, and re-checked rows that were never reached) — user.
- A group with a recently active datasource keeps its index, reactivates the active rows and uses no limit slot — user.
- The limit counts indexes actually deleted, and a group is counted only after its delete — user.
- The shared-index guard runs before the limit and before any re-check — user.
- A metrics-store failure aborts the run before anything is deleted, over catching it and returning stats — user.
- A per-datasource error leaves the row STALE, is counted in `recheck_errors` and keeps its index, over treating it as inactive or folding it into `errors` or `deletion_errors` — user.
- A row with no signal at all is inactive — user.
- The run summary logs `reactivated` and `recheck_errors` — user.
- Only a raised fetch error counts as a metrics store failure in the re-check, over also treating a missing `result` aggregation, failed shards or a timeout as one: main's deletion phase never consults metrics, so deleting on a missing or partial response is no worse than main, and the strict rule would stop a deployment without a metrics index from ever deleting, a regression against main. The strict rule is the rejected alternative — user, answers AR-2.
- Fetch metrics once for all remaining rows over per-group fetches: the fetchers scan the whole metrics index and the row list only matches buckets, so per-group scoping needs a new query filter and two scans per group. Proposal.
- Re-select each group's rows by id before re-checking, and skip a group whose rows no longer all match, over reusing the step-1 rows: step-1 rows are expired by the first commit, and the re-select picks up a concurrent reindex or reactivation. Proposal.
- Commit once per group that wrote anything over one commit at the end: each outcome persists before the next ES call and no transaction spans the whole walk. Proposal.
- A group with a mix of active and inactive rows reactivates only the active rows and leaves the rest STALE over deleting the index for the rest: the index is still used by an ACTIVE row; the leftovers are skipped by the guard from the next night. Proposal.
- A failed ES delete uses no limit slot over counting as an attempt: the limit is of deleted indexes; the re-check bound caps the attempts. Proposal.
- Bound the walk at 5 times the per-run limit in groups, set `deletion_capped` when it stops with groups left, over no bound: reactivating groups use no slot, so the first night after deploy could otherwise walk thousands of groups under the leader lock. Factor 5 is an assumption. Proposal.
- Reactivate with a guarded bulk UPDATE in the service session over `IndexInfo.complete_progress`: the latter bumps `update_date` (`index.py:1206`), a signal the re-check reads, and resets `completed`, `is_fetching`, `is_queued`, `error` and `current_state` through its own sync session.
- Take `reactivated` from the UPDATE rowcounts over the number of rows found active.
- Extract the fetch pair and the signal fold into shared methods used by detection and re-check over duplicating them: "same signals and threshold" then holds by construction.
- Stale-mark day is the UTC date of `marked_stale_at` over the full timestamp.
- Sort in Python on loaded rows over SQL ORDER BY: explicit NULL rules, testable with the area's mock-based tests.
- NULL dates sort first at both levels, and `id` breaks ties, for deterministic selection.
- Put both new keys in the early-return stats: the scheduler line reads them every night.

## Risks

- Re-check signals are fetched once and can be minutes old when a late group is reached; the per-group re-select refreshes `update_date` and row state, not metrics.
- The race between the last re-select and the ES delete remains (existing window, `:462-466`); the archive guard protects the row, not the index.
- A partly active group leaves its inactive rows STALE and skipped by the guard until the active row goes stale again.
- Tool-usage attribution has a name-only fallback across projects (`:355`); a same-named datasource elsewhere can keep a row alive.
- A missing or partial metrics response (no `result` aggregation, failed shards, a timeout) reads as "no signal" in both detection and the re-check, as on main, so such a night can delete the index of a recently used datasource whose only evidence is in the missing metrics.
- With ES down, each visited group fails its delete and uses no slot; the walk still reads up to 5 times the limit in groups. Groups with a recurring re-check error sit at the head of the order every night and eat bound slots.
- A DB or commit error mid-walk propagates, so earlier groups stay committed but the run summary is not logged.
- The metrics scan count stays at four per night (two in detection, two in the re-check); one Postgres connection is held across them. Cost unmeasured.
- `update_date` is written by flows other than use (e.g. `update_progress`, `index.py:1206`); if they touch STALE rows, the re-check reactivates on a signal that is not use. Unverified.
- The post-commit expiry is derived from the session construction, not run; the plan should check it against a real `AsyncSession` over two groups.
- Existing deletion tests encode the `session.execute` order and use rows without date fields; they need the per-group steps, date attributes on the row helper and a scheduler-line test including the early-return stats.
