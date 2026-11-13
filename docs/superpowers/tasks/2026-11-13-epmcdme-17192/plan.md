# EPMCDME-17192 Stale Datasource Deletion Order and Re-check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The nightly deletion phase walks idle STALE index groups oldest-first and re-checks each group's activity right before deleting its index.

**Architecture:** Extract detection's signal fetch pair and per-row fold into shared methods. Rewrite `_delete_stale_indexes` as: select idle STALE rows, shared-index guard, order groups in Python (plain values only), fetch metrics once, then walk groups lazily (re-select by id, re-check, reactivate or delete+archive, one commit per group that wrote).

**Tech Stack:** Python, SQLModel/SQLAlchemy `AsyncSession`, pytest (mock-based, `AsyncMock` session).

**Spec:** `docs/superpowers/tasks/2026-11-13-epmcdme-17192/spec.md` (analysis: `technical-analysis.md` same dir)

Commit per task using the repository's existing convention.

## Global Constraints

- Code: `src/codemie/service/stale_datasource/stale_datasource_service.py` (service), `scheduler.py` (summary). No new settings, no schema or `IndexInfo` change, no `complete_progress` call.
- No code comments, no feature docs under `docs/`.
- Tests mock-based like neighbours in `tests/codemie/service/stale_datasource/test_stale_datasource_service.py`; no real DB.
- Naive UTC datetimes. Re-check bound: module constant `_RECHECK_BOUND_FACTOR = 5` times `config.STALE_DATASOURCE_MAX_DELETIONS_PER_RUN`.
- UPDATEs guarded by `lifecycle_state == STALE`; `reactivated` = sum of UPDATE `rowcount`.
- Negative-constraint pass: non-goals honored by T3/T4 (no `complete_progress`, no per-group fetch scoping, guard-skipped rows never re-checked), T5 (`deletion_aborted`/`deletion_capped` lines untouched), T1 (detection behavior unchanged, `NO_UPDATE_DAYS` unused). No task violates a stated non-goal.

## Review Focus

- Rows with `marked_stale_at`, `update_date` and `date` all None: sort first, no crash (T2).
- Post-commit expiry: a row loaded before a commit is never read after it (T4 expiry double over two groups).
- Metrics response with no `result` (fetchers return `{}`): walk proceeds, not an error (T4).
- Group with a mix of active and inactive rows: only active rows reactivated, index kept, no archive (T3).
- Scheduler summary on a night with no idle rows (early-return stats include both new keys) (T5).

**Flag for the caller:** the spec's "check against a real `AsyncSession` over two groups" cannot be done in this area's style (no DB tests here; `IndexInfo` uses a native PG enum). T4 uses the repo precedent `_ExpireOnCommitSession` (`tests/codemie/service/settings/test_settings_transfer_service.py:560`): rows whose loaded state is cleared on commit, over two groups. A real-session run, if wanted, is a one-off outside the repo.

---

### Task 1: Extract shared signal methods

**Files:** Modify `stale_datasource_service.py:163-175` (and add methods near `:357`); Test `test_stale_datasource_service.py`

**Interfaces:**
- Produces: `async def _fetch_activity_signals(self, datasources: list[IndexInfo]) -> tuple[dict[str, datetime], dict[str, datetime]]` (lifecycle map, tool map; calls the two existing fetchers); `def _evaluate_staleness(self, datasource: IndexInfo, lifecycle_map: dict, tool_map: dict) -> bool` (fold of lifecycle, tool, tz-stripped `update_date` via `max(filter(None, ...), default=None)`, then `_is_datasource_stale`).

Test-first: yes — `test_fetch_activity_signals_returns_both_maps` fails (method missing); `test_evaluate_staleness_uses_latest_of_three_signals_and_no_signal_is_stale` fails likewise.

- [ ] Step 1: Add the two tests (patch the two fetchers with `patch.object`; row `MagicMock` with `update_date`; cover lifecycle recent, tool recent, update_date recent, all None -> True, tz-aware `update_date`).
- [ ] Step 2: Implement both methods; make detection (`:163-175`) call them, keeping its per-row `try/except` and `stats["errors"]`. Existing `TestDetectAndMarkStaleWiring` tests (they patch the fetchers) must still pass unchanged (AC14).
- [ ] Step 3: Run `pytest tests/codemie/service/stale_datasource -q`; expected PASS.

### Task 2: Pure group ordering

**Files:** Modify `stale_datasource_service.py` (module-level or static); Test same file.

**Interfaces:**
- Produces: `def _order_index_groups(rows_by_index: dict[str, list[IndexInfo]]) -> list[tuple[str, list[str]]]` returning (index name, row ids) ordered by the group's minimum row key `(stale-mark day, last-update, id)`; day = UTC date of `marked_stale_at`, last-update = `update_date` else `date`; None sorts before any value at both levels; `id` breaks ties. Output holds only strings (no row objects).

Test-first: yes — `TestOrderIndexGroups` fails (function missing).

- [ ] Step 1: Tests: earlier day first; same day by `update_date`; `update_date` None falls back to `date`; `marked_stale_at` None first; both dates None first within a day; group positioned by its earliest row (two-row group); id tie-break; tz-aware `marked_stale_at` converted to UTC date.
- [ ] Step 2: Implement; encode None-first as `(0, None-safe)` vs `(1, value)` tuples. Run the class; expected PASS.

### Task 3: Rewrite the deletion walk (order, re-check, reactivate, archive, limit)

**Files:** Modify `stale_datasource_service.py:384-513` (`_delete_stale_indexes`; add `_idle_stale_stmt(ids: list[str] | None = None)` holding the filter from `:401-423` plus optional `IndexInfo.id.in_(ids)`); Test same file (`TestDeleteStaleIndexes`, `TestDeleteStaleIndexesWiring`).

**Interfaces:**
- Consumes: T1 `_fetch_activity_signals`, `_evaluate_staleness`; T2 `_order_index_groups`.
- Produces: stats dict with the six existing keys plus `reactivated: int` and `recheck_errors: int`, all present in the early return (`0`).

Per spec Behavior steps 1-6: early return with zero stats and no fetch when no idle rows; guard against ACTIVE index names (skipped rows not re-checked, counted per datasource); group + order, capture only strings/ids; one `_fetch_activity_signals` over the remaining rows before any ES delete; per group: `execute(_idle_stale_stmt(ids))`, evaluate each row with the step-4 maps and the fresh row; all inactive -> `asyncio.to_thread(es_client.indices.delete, index=..., ignore_unavailable=True)`, then archive UPDATE (`:502-508` shape), `indexes_deleted += 1`, `datasources_archived += len(rows)`; any active -> guarded reactivation UPDATE (`lifecycle_state=ACTIVE, marked_stale_at=None`), `reactivated += rowcount`, index kept, no slot, other rows stay STALE; failed ES delete -> `deletion_errors += 1`, no slot, nothing written. Build metric payloads and all log values as plain values before the group's commit; commit once per group that wrote (UPDATE issued) and emit `STALE_DATASOURCE_INDEX_DELETED` after it. Stop when `indexes_deleted >= config.STALE_DATASOURCE_MAX_DELETIONS_PER_RUN`; `deletion_capped=True` when an unvisited group remains.

Test-first: yes — `test_groups_deleted_oldest_first`, `test_recently_active_row_reactivated_index_kept_no_slot`, `test_mixed_group_reactivates_only_active_rows_and_keeps_index`, `test_no_signal_row_is_inactive_and_deleted`, `test_early_return_stats_include_reactivated_and_recheck_errors`, `test_limit_counts_deleted_indexes_only` fail against the old walk.

- [ ] Step 1: Update helpers: `_make_stale_row` gains `error=False`, `marked_stale_at`, `update_date`, `date` (datetimes 200 days old so default rows are inactive); add `_execute_script(stale, active, groups)` building the `session.execute` side_effect: stale select, active select, then per group a re-select result and (when a write is expected) one UPDATE result with `rowcount`.
- [ ] Step 2: Rewrite existing `TestDeleteStaleIndexes` and the wiring tests to the new sequence (patch `_fetch_activity_signals` with `return_value=({}, {})`; the cap test now asserts 100 commits, one per deleted group; `test_deletion_called_and_stats_merged` stats gain the two keys). Add the new tests above (assert call order via `mock_thread` call args and the commit count).
- [ ] Step 3: Run tests, expect FAIL; implement; run `pytest tests/codemie/service/stale_datasource -q`, expect PASS.

### Task 4: Failure paths, changed groups, bound, expiry

**Files:** Modify `stale_datasource_service.py` (walk from T3); Test same file.

Adds: constant `_RECHECK_BOUND_FACTOR = 5` (stop after `5 * limit` visited groups, `deletion_capped=True` if groups remain); group whose re-select returns fewer rows than `row_ids` -> keep index, log, no slot, no write; per-row exception while evaluating -> row stays STALE, `recheck_errors += 1`, the group's index kept, no slot, other rows in the group still evaluated and active ones reactivated; fetch exceptions are not caught.

Test-first: yes — each fails against T3: `test_metrics_fetch_error_propagates_before_any_write_or_delete` (no UPDATE, `to_thread` not called, `commit` not called), `test_empty_metrics_maps_are_no_signal_and_walk_proceeds`, `test_row_error_counted_index_kept_no_slot_other_groups_proceed`, `test_group_with_fewer_rows_on_reselect_is_skipped_no_slot`, `test_failed_es_delete_uses_no_slot_and_leaves_rows_stale`, `test_walk_stops_at_five_times_limit_and_sets_capped` (501 reactivating groups -> 500 visited, `reactivated == 500`, rowcount-based), `test_reactivated_counts_update_rowcount_not_rows_found`, `test_no_row_attribute_read_after_commit_over_two_groups`.

- [ ] Step 1: Add `_ExpiringRow` (plain class, `expire()` clears `__dict__`, `get_index_identifier` reads an attribute) and make commit expire every row returned so far (precedent above); the two-group test returns fresh rows on group 2's re-select and asserts both groups archive and both metrics emitted after their own commit.
- [ ] Step 2: Add the tests, run (FAIL), implement, run `pytest tests/codemie/service/stale_datasource -q` (PASS).

### Task 5: Scheduler summary

**Files:** Modify `scheduler.py:79-86`; Create `tests/codemie/service/stale_datasource/test_stale_datasource_scheduler.py`.

Append `reactivated={stats['reactivated']}, recheck_errors={stats['recheck_errors']}` to the swept/deleted line. Leave the `deletion_aborted` branch untouched.

Test-first: yes — `test_summary_logs_reactivated_and_recheck_errors` and `test_summary_with_early_return_stats_does_not_raise` fail (keys not printed).

- [ ] Step 1: Tests build `StaleDatasourceScheduler(MagicMock())`, patch module-level `async_leader_lock` (async context yielding True), `get_async_session`, `MetricsElasticRepository`, `StaleDatasourceService` (its `detect_and_mark_stale_datasources` returning stats) and `logger`; assert the info message contains both counters, including zeros from early-return stats.
- [ ] Step 2: Run (FAIL), implement, run the file plus `tests/codemie/rest_api/test_main_stale_datasource_setup.py` (PASS).
