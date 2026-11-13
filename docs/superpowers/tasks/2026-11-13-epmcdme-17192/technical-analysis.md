# Technical Research

**Task**: stale datasource cleanup deletion
**Generated**: 2026-11-13
**Research path**: codegraph

---

## 1. Original Context

task_context (EPMCDME-17192, https://jiraeu.epam.com/browse/EPMCDME-17192, Task) — "Stale Datasource Deletion — Oldest-First Order and Activity Re-check":

1. The nightly stale-datasource deletion phase currently takes idle stale datasources in no order, so the per-run index limit defers an arbitrary subset. It also never re-checks activity, so a datasource used again after being marked stale still loses its index (only a reindex reactivates it).
2. Required: delete in order of stale-mark day (earliest first), then least recently updated (falling back to creation date). Datasources with no mark date go first. Datasources sharing an index are positioned by the earliest of them, and the limit counts indexes, not datasources.
3. Required: before the shared-index check and the limit, re-check all idle stale datasources using the same signals and threshold as detection. Recently active ones return to active with the stale mark cleared, and their shared index is kept. If the metrics store fails, nothing is deleted that run. A per-datasource error leaves that datasource stale and counted. A datasource with no signal at all counts as inactive. The run summary logs the number reactivated.

Out of scope: persisting a "last used" timestamp, changing thresholds or the per-run limit, restoring deleted indexes, and protecting indexes shared with reindexing, queued or errored datasources. Open questions: none.

feature_area: stale datasource cleanup deletion
run_dir: /Users/bohdan_maliar/Projects/codemie-dev/codemie/docs/superpowers/tasks/2026-11-13-epmcdme-17192

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A: delete the indexes of stale datasources, bounded per run**
- `src/codemie/service/stale_datasource/stale_datasource_service.py:384` `StaleDatasourceService._delete_stale_indexes` — the deletion phase. Called only from `detect_and_mark_stale_datasources` (`:194-196`), after the marking commit (`:192`), gated by `config.STALE_DATASOURCE_DELETION_ENABLED`. Steps in the code:
  1. `:401-425` select STALE rows with `completed == True`, `is_fetching` False/NULL, `is_queued` False/NULL, `error` False/NULL. **No `order_by`.**
  2. `:432-434` shared-index guard: all rows with `lifecycle_state == ACTIVE` -> set of `get_index_identifier()`.
  3. `:437-447` group stale rows by `get_index_identifier()` into `dict[str, list[IndexInfo]]` (insertion order = DB return order); a row whose index is in the ACTIVE set is skipped and counted in `skipped_shared_index`. The count is per datasource.
  4. `:451-458` cap: if `len(candidates) > STALE_DATASOURCE_MAX_DELETIONS_PER_RUN` set `deletion_capped=True` and keep `list(candidates.items())[:N]` (counts indexes, first N in insertion order).
  5. `:467-497` per index: `asyncio.to_thread(es_client.indices.delete, index=..., ignore_unavailable=True)`; on success counts `indexes_deleted`, `datasources_archived += len(rows)`, queues a `STALE_DATASOURCE_INDEX_DELETED` metric per row; on exception `deletion_errors += 1`, rows stay STALE.
  6. `:502-511` one `UPDATE ... SET lifecycle_state=ARCHIVED WHERE id IN (...) AND lifecycle_state == STALE`, one commit, then metrics emitted after commit.
  - Stats keys returned: `stale_rows_swept, indexes_deleted, datasources_archived, skipped_shared_index, deletion_errors, deletion_capped`. Returns early (`:428`) with those zeros when no idle STALE rows.
  - Tests: `tests/codemie/service/stale_datasource/test_stale_datasource_service.py` `TestDeleteStaleIndexes` (`:502`) and `TestDeleteStaleIndexesWiring` (`:748`).

**Purpose B: decide whether a datasource has been used recently (detection signals and threshold)**
- `stale_datasource_service.py:137` `detect_and_mark_stale_datasources` — computes per candidate: `lifecycle_ts = lifecycle_map.get(id)`, `tool_ts = tool_map.get(id)`, `update_ts = datasource.update_date` (tz stripped, `:172-173`), `last_activity = max(filter(None, [lifecycle_ts, tool_ts, update_ts]), default=None)` (`:174`), then `_is_datasource_stale(ds, last_activity)` (`:175`). This signal composition is inline in the loop (`:167-190`), not a reusable method.
- `:357` `_is_datasource_stale` — `(now - last_activity).days >= config.STALE_DATASOURCE_NO_USAGE_DAYS` (default 90); returns True with a warning log when `last_activity` is None (no signal = stale/inactive).
- `:275` `_fetch_lifecycle_metrics(datasources)` and `:312` `_fetch_tool_usage_metrics(datasources)` — ES aggregations over metrics index via `_paginate_composite_agg` (`:243`); both take a list of `IndexInfo` and return `{ds_id: datetime}`; both return `{}` for an empty list; keys by `(project_name, repo_name, index_type)` and by computed tool names (`_compute_base_tool_names`, `:88`). `_paginate_composite_agg` is fail-closed: ES errors propagate (docstring `:251-254`).
- Fail behaviour of the metrics store: `MetricsElasticRepository()` as built by the scheduler (`scheduler.py:74`) uses the default `fail_open_on_unavailable=False` (`src/codemie/repository/metrics_elastic_repository.py:85,95-105`), so `execute_aggregation_query` raises `ExtendedHTTPException(503)` when ES is unavailable or unconfigured, and 400/500/503 on API errors. `NotFoundError` on the metrics index returns empty aggregations instead of raising (`:199-201`).
- Today's failure path: an exception in the metrics fetch aborts `detect_and_mark_stale_datasources` before the marking commit and deletion; outer `except` (`:204-207`) rolls back and re-raises (test `test_es_failure_aborts_run_without_marking`, `:461`).
- Per-datasource error handling in detection: inner `try/except` (`:168,185-190`) increments `stats["errors"]`, logs, and continues.

**Purpose C: return a stale datasource to active (reactivation)**
- `src/codemie/rest_api/models/index.py:646` `IndexInfo.complete_progress` — sets `completed=True, is_fetching=False, is_queued=False, error=False, lifecycle_state=ACTIVE, marked_stale_at=None` on the instance and persists via `update_progress(ProgressUpdate(... lifecycle_state=ACTIVE, clear_marked_stale_at=True))` (`:658-668`). This is a reindex-completion path (also resets `current_state`), a synchronous model-level write using its own engine session (`IndexInfo.get_engine()`), not the service's `AsyncSession`.
- `index.py:284` `ProgressUpdate.clear_marked_stale_at` and `:1201-1204` (`_build_progress_update_kwargs`): `lifecycle_state` and `marked_stale_at=None` are written via the progress-update kwargs. Tests: `tests/codemie/rest_api/models/test_index_info.py:484-517`.
- Within the service, the only transition writer is `_mark_as_stale` (`stale_datasource_service.py:374`: sets `lifecycle_state=STALE`, `marked_stale_at=now`, `session.add`). No service code sets a row back to ACTIVE.
- Migration `src/external/alembic/versions/b5c6d7e8f9a0_reset_stale_lifecycle_state_on_deploy.py` bulk-resets STALE to ACTIVE and clears `marked_stale_at` (one-off, deploy time).

**Purpose D: which ES index does a datasource own (shared-index identity)**
- `index.py:1257` `IndexInfo.get_index_identifier` — KB/llm_routing/platform types: `KnowledgeBase(name=repo_name or f"{project}-{repo}", type).get_identifier()` depending on `uses_legacy_es_naming` (legacy names omit the project, so different projects can share one index); other types: `sanitize_es_index_name("project-repo-index_type")`. 13 callers across service/router code; the stale service is one.

**Purpose E: run summary / reporting**
- `src/codemie/service/stale_datasource/scheduler.py:65-95` `_run_stale_detection` builds the INFO summary from the stats dict: `evaluated, newly_stale, errors` and, when `stats.get("stale_rows_swept") is not None`, `swept, deleted, archived, skipped_shared, deletion_errors`. It reads `stats.get("deletion_aborted")` (`:77`), a key the service no longer produces (an earlier design had a circuit breaker; now the cap sets `deletion_capped`, which the summary does not print). Scheduler job/leader lock: `STALE_DATASOURCE_LOCK_ID` in `src/codemie/service/stale_datasource/config.py`; registered at `src/codemie/rest_api/main.py:799` (`_setup_stale_datasource_scheduler`).
- Tests for the scheduler line: `tests/codemie/rest_api/test_main_stale_datasource_setup.py` (registration only); none found for `_run_stale_detection`'s log string.

**Other touched code**
- `src/codemie/rest_api/models/index.py:217-218,369` `IndexInfo` columns: `date` (creation, Optional), `update_date` (Optional), `marked_stale_at` (Optional datetime, indexed, naive UTC), `lifecycle_state` (native PG enum `lifecyclestate`: ACTIVE/STALE/ARCHIVED, indexed). Naive UTC datetimes are the convention (`datetime.now(UTC).replace(tzinfo=None)`).
- `src/codemie/service/analytics/metric_names.py:50` `STALE_DATASOURCE_INDEX_DELETED`.
- `src/codemie/configs/config.py:1090-1097` config block (see Section 5).

### Decisions Already in the Code

- Idle-row definition for deletion: `completed`, not fetching, not queued, no error (`:401-423`); detection candidates additionally require `date < grace_cutoff` and `lifecycle_state == ACTIVE` (`:224-238`). So the grace-period filter is not applied to STALE rows in the deletion query. Reason: none found.
- Fail-closed on metrics errors in detection (`:251-254`, recorded in docstring): incomplete signals abort the run to avoid mass-marking.
- Write ordering in deletion (`:385-389`, recorded): ES delete first, PG ARCHIVED update second, one commit at the end; failed ES deletes stay STALE and retry next night. TOCTOU note recorded at `:462-466`: the archive UPDATE is guarded by `lifecycle_state == STALE` so a row that became ACTIVE through `complete_progress()` is not clobbered.
- Metrics emitted only after PG commit (`:461`, recorded).
- The cap counts indexes, not datasources (`:451`), and takes the first N of an insertion-ordered dict; order is whatever PostgreSQL returns (no ORDER BY). Reason: none found.
- Shared-index guard compares against ACTIVE rows only (`:432`); STALE rows of other idle datasources are grouped together; non-idle STALE rows (queued/fetching/error) are not part of either set (the task puts protecting them out of scope).
- Stats are a plain dict merged with `stats.update(deletion_stats)` (`:196`); the scheduler consumes it by key.
- Data in the needed direction/form: stale-mark day is `marked_stale_at` (full datetime, nullable); "least recently updated, falling back to creation date" maps to `update_date` (nullable) then `date` (nullable). Both are stored as naive UTC datetimes; no code in this area sorts by them. Other code sorts IndexInfo by `date.desc()` in Python-side SQLModel statements (`index.py:1372`).
- Detection uses `update_date` as one of three activity signals; `STALE_DATASOURCE_NO_UPDATE_DAYS` (config.py:1093) is defined but unused by the service (a prior spec lists it as drift, `docs/superpowers/tasks/2026-07-28-stale-datasource-es-deletion/spec.md:206`).
- Test convention: mock-based, `MagicMock` rows, `AsyncMock` session; `session.execute` mocked with a positional `side_effect` list whose order mirrors the query sequence (select STALE rows, select ACTIVE rows, then the UPDATE) (e.g. `:524-530`). `asyncio.to_thread` patched; `ElasticSearchClient` and `AgentMonitoringService` patched at the service module path.

### Task Statements Checked Against the Code

- "currently takes idle stale datasources in no order" — holds (`:401-425`, no `order_by`; grouping dict preserves return order, `:437`).
- "the per-run index limit defers an arbitrary subset" — holds (`:458`, first N of unordered dict).
- "never re-checks activity, so a datasource used again after being marked stale still loses its index" — holds: `_delete_stale_indexes` never calls the metric fetchers; only `_get_candidate_datasources` (ACTIVE rows only, `:228`) feeds them.
- "only a reindex reactivates it" — holds for the code in this repo: `complete_progress` (`index.py:646`) is the only writer of STALE/ARCHIVED -> ACTIVE found besides the one-off migration.
- "the limit counts indexes, not datasources" (as a required property) — current cap already counts indexes (`:451`); keeps this existing mechanism.
- "same signals and threshold as detection" — the signals (lifecycle, tool usage, `update_date`) and threshold (`_is_datasource_stale`, `STALE_DATASOURCE_NO_USAGE_DAYS`) exist; the combination step is inline in the detection loop (`:169-175`), so "same" currently means duplicating or extracting that block. Fetchers and `_is_datasource_stale` are reusable as they are.
- "A datasource with no signal at all counts as inactive" — holds for `_is_datasource_stale` (`:371-372`, returns True). Note `update_date` is itself a signal, so "no signal" means no ES hit and `update_date` None.
- "If the metrics store fails, nothing is deleted that run" — fail-closed fetchers already raise (see Purpose B); in the deletion phase today no fetch occurs, so this behaviour is new to the phase. The outer `except` in `detect_and_mark_stale_datasources` (`:204-207`) would rollback and re-raise if a raise happens in the deletion phase, after the marking commit.
- "A per-datasource error leaves that datasource stale and counted" — detection already counts per-datasource exceptions in `stats["errors"]` (`:185-190`); deletion stats have `deletion_errors` (per index, `:493`). Which counter this uses is not stated in the task.
- "Datasources sharing an index are positioned by the earliest of them" — existing grouping by `get_index_identifier()` (`:437`) is the mechanism; ordering inside it does not exist.
- "The run summary logs the number reactivated" — scheduler summary (`scheduler.py:79-93`) prints a fixed set of keys; no reactivation key exists today. Keeps `scheduler.py` stat-by-key consumption in place.
- "Open questions: none" — unverified against the code on: tie-breaking inside equal dates; "stale-mark day" (date vs datetime of `marked_stale_at`); behaviour of rows where `date` and `update_date` are both None; which counter a per-datasource error increments. Code gives no answer for these.

### Consumers of Changed Contracts

- Stats dict returned by `_delete_stale_indexes` / `detect_and_mark_stale_datasources`: consumed by `scheduler.py:76-93` (keys read: `deletion_aborted`, `stale_rows_swept`, `indexes_deleted`, `datasources_archived`, `skipped_shared_index`, `deletion_errors`, `total_evaluated`, `newly_marked_stale`, `errors`); asserted in tests `test_deletion_called_and_stats_merged_when_flag_enabled` (`:769`) and `TestDeleteStaleIndexes` (`:509-745`). `stats.update(deletion_stats)` at `service:196`. Searched `stale_rows_swept|indexes_deleted|deletion_capped` across src and tests; no other consumer found.
- `_is_datasource_stale(datasource, last_activity)`: callers: detection loop (`:175`), tests `TestIsDatasourceStale` (`:326-354`).
- `_fetch_lifecycle_metrics` / `_fetch_tool_usage_metrics`: callers: detection (`:163-164`); tests `TestFetchLifecycleMetrics` (`:112`), `TestFetchToolUsageMetrics` (`:180`), `TestDetectAndMarkStaleWiring` patches them (`:361-479`) and `TestDeleteStaleIndexesWiring` patches them with `return_value={}` (`:754-755`, `:781-782`).
- `session.execute` call sequence in `_delete_stale_indexes`: tests feed positional `side_effect` lists (stale select, active select, UPDATE); every `TestDeleteStaleIndexes` test depends on the number and order of `execute` calls (`:509-745`), as does the early-return test (`:509-518`: one `execute` result `[]` and `commit` not called).
- Row MagicMock helper `_make_stale_row` (`:488`) sets `lifecycle_state, completed, is_fetching, is_queued, get_index_identifier` but not `marked_stale_at`, `update_date`, `date`, `repo_name` types for sorting or the `error` field.
- `lifecycle_state` STALE -> ACTIVE: other readers of `lifecycle_state` in `src`: only `index.py` (model + `complete_progress`, `_build_progress_update_kwargs`) and the stale service; no router/UI filter on it found in `src` (hosted_applications' `lifecycle_state` is an unrelated column). `marked_stale_at` appears in the OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json:12835` (response schema).
- `IndexInfo.get_index_identifier` — 13 callers; unchanged by this task.

### Architecture and Layers Affected

- Service layer: `StaleDatasourceService` (`src/codemie/service/stale_datasource/`).
- Scheduler/job layer: `StaleDatasourceScheduler._run_stale_detection` (summary log only).
- Repository/infra: `MetricsElasticRepository` (ES metrics, read-only), `ElasticSearchClient` (index delete), async SQLAlchemy `AsyncSession` (PG).
- Model layer: `IndexInfo` columns/state (read; writes via ORM attribute + `session.add`, or core `sa_update` as in `:503`).

### Integration Points

- ES metrics index via `MetricsElasticRepository.execute_aggregation_query` (async; fail-closed by default).
- ES datasource indexes via sync `ElasticSearchClient.get_client().indices.delete` run in `asyncio.to_thread`.
- PostgreSQL `index_info` through the `AsyncSession` passed in by `get_async_session()` (`scheduler.py:73`); the whole run is under `async_leader_lock(STALE_DATASOURCE_LOCK_ID)`.
- Concurrent writer: `IndexInfo.complete_progress` / reindex flows on other pods use sync sessions (race noted in-code at `:462-466`).
- Monitoring: `AgentMonitoringService.send_count_metric`.

### Patterns and Conventions

- Plain async methods on the service with a stats dict; per-item try/except that logs `exc_info=True` and counts errors.
- Select via `sqlmodel.select(...).where(...)`, `or_(col == False, col.is_(None))` pattern with `# noqa: E712`.
- Bulk state change via `sqlalchemy.update` with a `lifecycle_state == STALE` guard; single commit per phase.
- Naive-UTC datetime handling (`datetime.now(UTC).replace(tzinfo=None)`; `update_date` stripped of tzinfo before comparison).
- No code comments added by the user's convention (memory); logs are f-strings via `codemie.configs.logger`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` has no stale-datasource guide (searched "stale"; hits are unrelated: CLI analytics, security, FAQ git). Relevant general guides: `architecture/service-layer-patterns.md`, `data/database-patterns.md`, `data/elasticsearch-integration.md`, `testing/testing-service-patterns.md`, `testing/testing-patterns.md`, `development/logging-patterns.md`, `quality-gates.md`. Not read in full; none is specific to this feature.

### Architectural Decisions
- Prior task folder `docs/superpowers/tasks/2026-07-28-stale-datasource-es-deletion/` (spec.md, technical-analysis.md, code-review-final.json) records the deletion phase design: crash-safe ordering, STALE-guard on archive UPDATE, reactivation via `complete_progress()` (AC-8), `NO_UPDATE_DAYS` drift as a separate ticket. Its spec described a circuit breaker (`deletion_aborted`) that the current code replaced with a cap (`deletion_capped`); the scheduler still reads the old key.

### Derived Conventions
- Stats keys are additive; the scheduler formats them by hand.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/stale_datasource/test_stale_datasource_service.py` (the only test file for the package): tool-name computation, both metric fetchers (including ES errors propagating), `_is_datasource_stale` (recent, old, exact threshold, no signals), detection wiring (lifecycle/tool/update signals, ES failure aborts without marking), `_delete_stale_indexes` (empty, happy path, group archive of two rows on one index, shared-index guard, cap at 100 of 101, per-index failure continues, `ignore_unavailable`, metrics not emitted on commit failure, error rows), deletion wiring (flag on/off).
- `tests/codemie/rest_api/test_main_stale_datasource_setup.py` — scheduler registration only.
- `tests/codemie/rest_api/models/test_index_info.py:484-517` — `clear_marked_stale_at` / `complete_progress` reactivation.
- `tests/codemie/configs/test_config.py:77-87` — deletion flag requires enable flag.

### Testing Framework and Patterns
- pytest + `pytest.mark.asyncio`, `unittest.mock` (`AsyncMock`, `MagicMock`, `patch`, `patch.object`); module-path patches for `config`, `ElasticSearchClient`, `AgentMonitoringService`; positional `side_effect` for `session.execute`. No real DB or ES. (Project memory: tests match area style, mock-based.)

### Coverage Gaps
- No test of ordering (all rows in cap test have identical attributes; mocks have no `marked_stale_at`/`update_date`/`date`).
- No test for re-check, reactivation, or summary string in `scheduler._run_stale_detection`.
- No test that a metrics failure occurs in the deletion phase (current deletion tests never touch the fetchers).
- No test for NULL ordering (`marked_stale_at` None, `update_date` None, `date` None).

---

## 5. Configuration and Environment

### Environment Variables
(`src/codemie/configs/config.py:1090-1097`)
- `STALE_DATASOURCE_ENABLED` (False), `STALE_DATASOURCE_SCHEDULE` ("0 3 * * *" UTC), `STALE_DATASOURCE_NO_USAGE_DAYS` (90; the threshold in `_is_datasource_stale`), `STALE_DATASOURCE_NO_UPDATE_DAYS` (120; unused by the service), `STALE_DATASOURCE_GRACE_DAYS` (7), `STALE_DATASOURCE_BATCH_SIZE` (100; ES composite page size), `STALE_DATASOURCE_DELETION_ENABLED` (False), `STALE_DATASOURCE_MAX_DELETIONS_PER_RUN` (100).
- Validators: `config.py:1238-1248` (cron validity; deletion requires enable).
- Metrics index name: `config.ELASTIC_METRICS_INDEX` (used by `MetricsElasticRepository`).

### Configuration Files
- `deploy-templates/values.yaml` carries the eight `STALE_DATASOURCE_*` vars (per prior task AC-13); not re-verified here.
- Advisory lock id: `src/codemie/service/stale_datasource/config.py` (`STALE_DATASOURCE_LOCK_ID`).

### Feature Flags and Deployment Concerns
- Deletion is off by default and needs `STALE_DATASOURCE_ENABLED` as well. Multi-pod safety via `async_leader_lock`.

---

## 6. Risk Indicators

- Reactivation: no existing service-level STALE -> ACTIVE writer; `IndexInfo.complete_progress` (`index.py:646`) is a reindex-completion writer that also touches `completed/is_fetching/is_queued/error/current_state` and uses its own sync session. See Purpose C.
- Signal composition for "same signals and threshold" is inline in the detection loop (`stale_datasource_service.py:167-190`), not a method; tests of detection patch the fetchers and rely on that loop. See Purpose B.
- Metrics fetchers take `list[IndexInfo]` keyed by `(project, repo_name, index_type)`; `_fetch_tool_usage_metrics` has a name-only fallback attributing a signal to several datasources with the same `repo_name` across projects (`:355`), so recheck of STALE rows inherits this attribution.
- Shared-index guard (`:432`) currently reads ACTIVE rows with a fresh select after the STALE select; the position of any re-check relative to it determines what that select sees (same session, unflushed ORM changes vs. executed UPDATE). Speculative: the order of `session.execute` calls is relied on by every existing deletion test.
- Concurrency window: another pod's `complete_progress()` between the STALE select and the archive UPDATE is only protected by the `lifecycle_state == STALE` guard (`:506`); a reactivated row's index would still have been deleted from ES at `:473` before that guard applies (in-code TOCTOU note `:462-466`).
- Stats/summary contract: scheduler reads `deletion_aborted` which the service never sets (dead branch, `scheduler.py:77`); `deletion_capped` is not logged; any new stat must also be added in `scheduler.py:79-93` and is untested there.
- Statement "no stale-datasource ordering exists anywhere" holds; nothing in the code sorts these rows today, so NULL handling (`marked_stale_at`, `update_date`, `date` all Optional) and Python-side vs SQL ordering are unspecified by the repo.
- Existing mechanism for the same purpose kept in place by task statements: the index-counting cap and the ACTIVE-only shared-index guard (see Task Statements).
- Test fragility: `_make_stale_row` MagicMocks lack the date fields; `session.execute` side_effect lists encode the query sequence.
- Metrics-store error behaviour depends on `MetricsElasticRepository` default fail-closed; `NotFoundError` on the metrics index yields empty aggregations rather than an error, so a missing metrics index looks like "no signal" except for `update_date`.
- No guide for this feature; all conventions are derived from code.

---

## 7. Summary for Complexity Assessment

The change sits almost entirely in one service class, `StaleDatasourceService` in `stale_datasource_service.py` (`_delete_stale_indexes`, with signal evaluation currently inline in `detect_and_mark_stale_datasources`), plus a small touch to the scheduler summary string in `scheduler.py`. Layers: service, scheduler log, and read-only use of the ES metrics repository and PostgreSQL `index_info` columns that already exist (`marked_stale_at`, `update_date`, `date`, `lifecycle_state`); the research found no model or schema work in the code that exists. The existing metric fetchers and `_is_datasource_stale` already take the signals and threshold that "same as detection" refers to, and they are fail-closed on metrics-store errors.

Technical novelty is low to moderate: ordering of an unordered query with nullable datetimes, grouping by shared ES index, a second pass of ES aggregation over STALE rows, and a state transition (STALE -> ACTIVE) the service does not do today. The existing writer for that transition (`IndexInfo.complete_progress`) is a reindex path with different side effects. Concurrency with reindex on other pods is documented in-code and handled with a state guard on the archive UPDATE.

Test posture: one mock-based test module covers the area well for the existing behaviour, but nothing covers ordering, re-check, reactivation, or the scheduler's summary line. Existing deletion tests encode the number and order of `session.execute` calls and use MagicMock rows without date fields, so they are sensitive to any change in the query sequence or row attributes. Key risks: signal composition not extracted, no service-level reactivation, dead `deletion_aborted` key and absent new summary key in the scheduler, and null-date ordering unspecified.

---

## 8. External References

None named by the task (a Jira URL, EPMCDME-17192, is given as an identifier; it is outside this repository and was not opened).
