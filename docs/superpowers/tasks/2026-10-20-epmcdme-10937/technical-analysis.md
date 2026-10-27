# Technical Research

**Task**: scheduler cron triggers scheduler_run datasource reindex job lock
**Generated**: 2026-10-20
**Research path**: codegraph

---

## 1. Original Context

Ticket EPMCDME-10937 (Bug, Major): "Scheduler-based reindex does not update datasource or 'updated' field as per cron schedule". Reporter: scheduled hourly reindex of Confluence datasource did not move "Updated" (update_date) between Mar 2 and Mar 10 2026, and there was no trace of indexation.
Original acceptance criteria: scheduler-based reindex for all supported datasource types triggers indexation and updates "updated" per cron; no regression for manual reindex or other cron expressions; "Updated" changes accordingly to a successful run.

TRIAGE ANALYSIS COMMENT (Oct 2026, by the assignee) — this defines the real remaining scope:
- Likely already fixed for the original symptom: a successful scheduled run always moves "Updated" (complete_progress -> update_progress sets update_date). Fixed by EPMCDME-12780 (one invalid datasource setting crashed the scheduler settings watcher) and EPMCDME-13171 (c4da1ea87: APScheduler 1s misfire grace dropped jobs; fix also stops one failing setting aborting the scheduling pass, adds per-datasource job lock, logs `Scheduled job MISSED/SKIPPED/FAILED`).
- Not reproducible locally: hourly Confluence datasource, 15:00 UTC scheduled run -> scheduler run trigger `scheduled`, status `completed`, update_date moved, last_reindex_triggered_at (EPMCDME-14857) set. Minor finding: scheduler run `metrics` column empty although a page was indexed.
- Commit c048ee1be (branch EPMCDME-10937_fix-schedule-based-datasource-reindex-update) only changes workflow-generator validation and is mistagged; NOT part of this fix. Do not touch it.
- Field clarification: ticket is about "Updated" (update_date, set on run completion), NOT "Last indexed" (last_reindex_triggered_at, EPMCDME-14857).
- REMAINING GAP 1 (confirmed locally): silent no-op scheduled runs are recorded as successful. Happens when the datasource PostgreSQL advisory job lock is held, when Confluence credentials/config are missing (actor logs and returns), or when Elasticsearch isn't configured. The scheduler_run row is marked `completed`, nothing indexed, "Updated" doesn't move. Lock test: run took 23 ms vs 2810 ms normal, datasource stayed completed=true/error=false; only trace is WARNING "Skipping reindex_confluence - datasource is already being reindexed by another process".
- REMAINING GAP 2 (confirmed locally): with APScheduler max_instances=1, a scheduled time firing while the previous run is still in progress is skipped by APScheduler before the job function is called, so no scheduler_run row is written at all (only WARNING "Scheduled job SKIPPED - previous run still in progress: job_id=..."). Happens whenever a reindex outlasts its cron interval.
- Side finding: a run killed by a backend restart stays `running` in scheduler_runs forever (no finish time).
- SUGGESTED FIX: (1) Gap 1: have `with_datasource_job_lock` and the actors' early returns report a skip, and have `_tracked_run_sync` record such runs as `skipped` (or `failed`) with the reason instead of `completed`. (2) Gap 2: have the scheduler event listener (`__on_job_event` in `triggers/bindings/cron.py`) also write a `scheduler_run` row (`skipped`/`missed`) for EVENT_JOB_MAX_INSTANCES and EVENT_JOB_MISSED, not just log them. Fix 1 alone does not cover gap 2 because the job function never runs.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/triggers/bindings/cron.py` — `Cron` class (APScheduler `AsyncIOScheduler`); module-level `_tracked_run` (async, assistant/workflow, L79) and `_tracked_run_sync` (L158, datasource actors).
  - `_tracked_run_sync` creates a `SchedulerRun(status="running", trigger="scheduled", input_data={"resourceId":...})`, calls `actor_fn(**kwargs)` and ignores its return value. On normal return it writes `status="completed"` plus `metrics` from `_extract_datasource_metrics(payload)`. On exception it writes `failed` plus `error_data` and re-raises.
  - `_extract_datasource_metrics` (L132) reloads `IndexInfo` and returns `tokens_usage` or `None`. It is the source of the empty `metrics` side finding.
  - `Cron.start_async` (L242-273) builds the scheduler with `job_defaults = {misfire_grace_time: None, coalesce: True, max_instances: 1}`, executors `default` (thread pool, `CRON_SCHEDULER_MAX_WORKERS`) and `asyncio`, and registers one listener: `add_listener(self.__on_job_event, EVENT_JOB_MISSED | EVENT_JOB_ERROR | EVENT_JOB_MAX_INSTANCES)`.
  - `__on_job_event` (L275-305) is a `@staticmethod` that only logs. Events carry `job_id`, `scheduled_run_time` (MISSED/ERROR) and `exception` (ERROR). The `job_id` is the scheduler setting id, which equals `scheduler_id` in `scheduler_runs`.
  - `__schedule_datasource_job` (L793) and `__schedule_knowledge_base_job` (L863-1034) add jobs per index type.
    - Jobs wrapped in `_tracked_run_sync` (kwargs `actor_fn`, `scheduler_id=job_id`, `resource_id=job_id`, `payload`): svn, code/summary/chunk_summary, jira, confluence, google, azure_devops_wiki, azure_devops_work_item, xwiki, xray, sharepoint.
    - `reindex_git_faq` is added directly at L916-922 with `kwargs={"payload": payload}` and no `_tracked_run_sync`. It writes no `scheduler_run` row at all.
- `src/codemie/triggers/job_lock.py` — `with_datasource_job_lock` (L95-139) is the decorator for datasource actors. It has two silent early exits, both `return None` after a `logger.warning`:
  - `ElasticSearchClient.is_configured()` is False: "Skipping %s - Elasticsearch is not configured ...".
  - The advisory lock is not acquired: "Skipping %s - datasource is already being reindexed by another process: datasource_id=%s".
  - If no datasource id can be found it logs a warning and runs the function unlocked.
  - `datasource_job_lock` is a context manager built on `LeaderLockContext(lock_key=datasource_lock_key(id), engine=_get_lock_engine())`. `datasource_lock_key` returns a two-int key from a BLAKE2b digest.
  - Because the decorator returns `None` in every case, the caller cannot tell skipped from done today.
- `src/codemie/triggers/actors/datasource.py` — `@with_datasource_job_lock` actors: `reindex_code`, `reindex_svn`, `reindex_jira`, `reindex_confluence` (L234-309), google, azure wiki, azure work item, xwiki, xray, sharepoint, git_faq and `resume_stale_datasource`. Codegraph lists about 13 decorated sites.
  - Every actor begins with `IndexInfo.stamp_reindex_triggered_at(...)`.
  - Early returns that only log and return `None`: missing `confluence_index_info` (L258-270), missing Confluence creds (L275-284), and `not svn_repo` in `reindex_svn` (L144-154). The same shape recurs in `reindex_code` (~L99-105).
  - Real work goes through `datasource_concurrency_manager.run(processor.reprocess, processor.index)`. That also returns `None` on queue timeout after `set_error` (`src/codemie/datasource/datasource_concurrency_manager.py` L110-118), so a concurrency-queue timeout is another path recorded as `completed`.
- `src/codemie/rest_api/models/scheduler_run.py` — `SchedulerRunStatus` StrEnum (RUNNING, COMPLETED, FAILED, CANCELLED) and the `SchedulerRun` SQLModel (table `codemie.scheduler_runs`).
  - `status` is a free `str` column. The enum is not used as a DB constraint here, and no `skipped` or `missed` value exists yet.
  - Relevant columns: `scheduler_id`, `status`, `trigger`, `started_at`, `finished_at`, `duration_ms`, `input_data`, `result_data` (JSONB), `error_data` (JSONB), `logs`, `metrics`.
  - `SchedulerRunStats` (L123) has fixed counters `total/completed/failed/running/cancelled/successRate/averageDurationMs`.
- `src/codemie/repository/scheduler_run_repository.py` — `create`, `update(run_id, fields)`, `get_by_id`, `list_runs(status: List[str] ...)`, `get_stats`, `delete_run`, `get_logs_page`.
  - `get_stats` is raw SQL with `COUNT(*) FILTER (WHERE status = 'completed'|'failed'|'running'|'cancelled')`. `successRate = completed / (completed + failed)`.
  - A new status would be counted in `total` only, and would not enter the success rate.
  - `list_runs` filters `status IN (...)` and accepts arbitrary strings.
  - `delete_run` blocks only `status = 'running'`.
- `src/codemie/service/scheduler_run_service.py` — `SchedulerRunService` builds `SchedulerRunDetail`. `_build_logs` appends an ERROR `LogEntry` from `error_data["message"]` and `["type"]`. This is the existing UI channel for a failure reason.
- `src/codemie/rest_api/routers/schedulers.py` — REST layer for scheduler runs. Its status query-param handling was not inspected. Check whether it validates against `SchedulerRunStatus`.
- `src/codemie/datasource/base_datasource_processor.py` — `schedule()` (L118), `reprocess()` (L387) and `process()`. This is where "Updated" (`update_date`) is set on completion through `complete_progress` / `update_progress` (per triage; not re-verified here).

### Architecture and Layers Affected
- Trigger or scheduler binding: `triggers/bindings/cron.py`, the APScheduler wiring and event listener.
- Trigger actors: `triggers/actors/datasource.py` plus `triggers/job_lock.py`, the decorator and lock.
- Persistence: `SchedulerRun` model and `SchedulerRunRepository`, in PostgreSQL schema `codemie`.
- Service and REST read side: `SchedulerRunService`, `routers/schedulers.py`, and the stats and list SQL.
- Datasource processing (`datasource_concurrency_manager.py`, `base_datasource_processor.py`) is a downstream source of `None` returns, not necessarily a change site.

### Integration Points
- APScheduler `AsyncIOScheduler` events `EVENT_JOB_MISSED`, `EVENT_JOB_ERROR` and `EVENT_JOB_MAX_INSTANCES` to `Cron.__on_job_event`.
- The `_tracked_run_sync` wrapper runs on the APScheduler thread-pool executor. `__on_job_event` runs on the scheduler's event dispatch, so any DB write there is synchronous in that thread.
- PostgreSQL advisory locks via `LeaderLockContext` (`codemie.utils.leader_lock`) using a dedicated engine.
- `ElasticSearchClient.is_configured()` (`RETRIEVAL_BACKEND=none` or `ELASTIC_URL` unset).
- `SettingsService.get_confluence_creds` and similar credential lookups.
- `stale-indexing watchdog` (`Cron.__watch_stale_indexing` -> `resume_stale_datasource`). It calls a `@with_datasource_job_lock` actor positionally, outside `_tracked_run_sync`, so any change to the decorator's return contract also reaches this caller.

### Patterns and Conventions
- Actors return `None` and signal failure only by logging. `_tracked_run` (the async variant) already uses a retval dict (`conversation_id`, `resource_execution_id`, `metrics`) to enrich the run row. `_tracked_run_sync` has no equivalent.
- The `functools.wraps` decorator `with_datasource_job_lock` accepts both kwarg and positional payloads.
- Status strings are plain literals in `cron.py` ("running", "completed", "failed") and in repository SQL. The enum is not referenced there.
- Logging uses `%s` lazy formatting in `cron.py` and actors. Logger comes from `codemie.configs`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists. Relevant: `architecture/layered-architecture.md`, `architecture/service-layer-patterns.md`, `data/repository-patterns.md`, `data/database-patterns.md`, `development/error-handling.md`, `development/logging-patterns.md`, `testing/testing-patterns.md`, `testing/testing-service-patterns.md`, `quality-gates.md`. They were not read in this pass; they are listed for the downstream stages. No guide specific to the scheduler or cron triggers was found in the file list.

### Architectural Decisions
Inline docstrings record the intent. `job_lock.py` module docstring: a lock miss means "skip and say so". The `__on_job_event` docstring: dropped runs "looked exactly like the feature being broken". The comment on `with_datasource_job_lock` says a missing datasource id runs unlocked because "a skipped reindex is not" rare. `start_async` comments explain `misfire_grace_time=None`, `coalesce` and `max_instances=1`.

### Derived Conventions
New run states must be handled in three read paths: `SchedulerRunStatus`, the `get_stats` SQL, and the response models. Migrations are under Alembic. No `scheduler_run` migration file was found by the `alembic/**/*scheduler_run*` glob. `status` is an unconstrained string column in the model, but the DB constraint was not checked.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/triggers/bindings/test_cron.py` — `Cron` job scheduling and `Job` tracking. It covers `test_reindex_code_job`, the add_assistant/add_workflow job tests and `remove_jobs_for_deleted_settings`, and exercises `__on_job_event` and `_tracked_run_sync` paths via the cron module.
- `tests/codemie/triggers/bindings/test_cron_prompt_extension.py` — prompt handling in cron.
- `tests/codemie/triggers/test_job_lock.py` — `with_datasource_job_lock`, `datasource_lock_key` and lock acquisition.
- `tests/codemie/triggers/actors/test_actor_datasource.py` — actor tests such as `test_reindex_confluence` and `test_reindex_confluence_stamps_triggered_at`.
- `tests/codemie/rest_api/routers/test_schedulers.py` — `SchedulerRunService` and router tests (get/stats/delete 404/409).
- `tests/codemie/repository/test_scheduler_run_repository_postgres.py` — only a stale `__pycache__` entry was found, so the source file may be absent or renamed; verify.
- `tests/codemie/datasource/test_base_datasource_processor.py` and `tests/codemie/datasource/test_datasource_concurrency_manager*` — not confirmed.

### Testing Framework and Patterns
pytest 8.3.x on Python 3.12 (from the `.pyc` name). Project policy in `.ai-run/guides/testing/testing-patterns.md`: tests are only written or run when explicitly asked. Enterprise-dependent tests are marked, with a `--skip-enterprise` option (recent commit a25dfbdc2).

### Coverage Gaps
- No test for `__on_job_event` writing a run row (it only logs today).
- No test pinning that a lock-skip or ES-not-configured run is recorded as `completed` today.
- `reindex_git_faq` untracked scheduling has no test of run-row creation.
- Restart-orphaned `running` rows have no handling or test.

---

## 5. Configuration and Environment

### Environment Variables
- `CRON_SCHEDULER_MAX_WORKERS` — thread-pool size and size of the dedicated lock engine pool.
- `STALE_INDEXING_WATCHDOG_ENABLED`, with `STORAGE_CONFIG.stale_indexing_threshold_seconds` and `stale_indexing_resume_batch_size` — the watchdog that calls `resume_stale_datasource`.
- `RETRIEVAL_BACKEND` and `ELASTIC_URL` — drive the `ElasticSearchClient.is_configured()` guard.
- `TIMEZONE` (`config.TIMEZONE`) — default cron timezone.
- `SCHEDULER_PROMPT_SIZE_LIMIT` — assistant and workflow prompts only.
- `PG_POOL_SIZE` — main pool, deliberately separate from the lock engine.

### Configuration Files
`datasources-config.yaml` in `DATASOURCES_CONFIG_DIR`, loaded by `datasources_config.py` into `STORAGE_CONFIG`. Config class: `codemie.configs.config`.

### Feature Flags and Deployment Concerns
Multi-pod deployment is a design assumption (leader lock, advisory lock). Scheduler settings are rows in `settings` with credential type SCHEDULER, polled every 10 s by `__watch_settings`. The same table is joined in `get_stats`.

---

## 6. Risk Indicators

- Return-contract fan-out: `with_datasource_job_lock` is applied at about 13 sites plus the watchdog's `resume_stale_datasource`. Every actor currently returns `None` for success and skip alike. Any skip signal changes shared behaviour (`src/codemie/triggers/job_lock.py`, `src/codemie/triggers/actors/datasource.py`).
- Run-status consumers: `get_stats` SQL counts only the four known statuses and bases `successRate` on `completed`+`failed`. A new `skipped`/`missed` status would be in `total` only, and its effect on the UI counters and filters is unverified (`scheduler_run_repository.py`, `scheduler_run.py`).
- Listener thread context: `__on_job_event` is a staticmethod running in the scheduler's event dispatch with no DB access today. Adding `SchedulerRunRepository().create` there adds a synchronous DB write, and a failure there must not break the listener.
- The `EVENT_JOB_MAX_INSTANCES` event carries `job_id` but no `started_at` of the skipped run. MISSED carries `scheduled_run_time`. `input_data["resourceId"]` is set by `_tracked_run_sync` as `job_id`, so a listener-written row needs the same shape to render consistently.
- Logging bug on the missing-credentials path: `reindex_confluence` L277-283 calls `logger.error(REINDEX_FAILED_MSG, ...)` with 4 args against 5 `%s` placeholders. The message is dropped by the logging module (a "Logging error", not a visible trace). This concerns the same silent no-op scenario as Gap 1. The same pattern may exist in other actors.
- `reindex_git_faq` is scheduled without `_tracked_run_sync` (cron.py L916-922), so it has no run row at all, which is a third untracked path.
- A concurrency-queue timeout in `DatasourceConcurrencyManager.run` also returns `None`, so it is another "completed" no-op beyond the three named in the triage.
- Restart-orphaned `running` rows (side finding) are out of the suggested fix. Using the `delete_run` guard, such rows are undeletable (`status != 'running'`).
- Local-reproduction-only evidence: Gap 1 and Gap 2 were confirmed locally by the assignee; nothing here re-verified them.
- Speculative: a `SchedulerRunStatus` addition may need UI or API consumers updated, and a DB check constraint or migration if one exists (none found by glob).
- Speculative: reporting a skip may be done either through an actor return value or an exception. Which one fits the existing `_tracked_run` retval-dict convention is a design choice for the spec.
- Mistagged commit c048ee1be is explicitly out of scope; do not touch it.

---

## 7. Summary for Complexity Assessment

The change sits in the trigger layer, in roughly three source files: `triggers/bindings/cron.py` (`_tracked_run_sync` and `__on_job_event`), `triggers/job_lock.py` (decorator return contract) and `triggers/actors/datasource.py` (early-return paths). It also touches `rest_api/models/scheduler_run.py` and possibly `scheduler_run_repository.py` for the new status and the stats SQL. The existing patterns are all present: `_tracked_run`'s retval-dict enrichment, the `SchedulerRunRepository.create/update` API, and the free-string `status` column. There is no new subsystem or external dependency.

Technical novelty is low to moderate. The decorator is shared by about 13 actors plus the watchdog, so the signalling contract (return value versus exception) affects all of them. Gap 2 needs a DB write from the APScheduler event listener, which is a new execution context and has no test today. Read paths (`get_stats` SQL, list filters, UI status handling) are a coupling risk for any new status value.

Test posture: unit tests exist for cron scheduling, job lock, datasource actors and the scheduler-run service and router. None assert the status recorded for a skipped run, and none cover the listener writing rows, so new tests would be needed for both gaps. Main risks are the shared return contract, the unverified downstream effects of a new status, the untracked `reindex_git_faq`, and the logging-arity bug on the missing-credentials path.

---

## 8. External References

None named by the task. The commit and tickets cited (c048ee1be, c4da1ea87, EPMCDME-12780, 13171, 14857) were taken from the task text and not opened.
