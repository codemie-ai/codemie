# EPMCDME-10937: record skipped and not-started scheduled runs

Size M (complexity-assessment.json). Bug fix; no schema, API or UI change.

## Problem

The original symptom (hourly Confluence reindex never moved "Updated") is believed fixed by EPMCDME-12780 and EPMCDME-13171. Two gaps remain where a scheduled reindex does nothing and leaves no honest trace:

1. **Silent no-op recorded as success.** `with_datasource_job_lock` (`src/codemie/triggers/job_lock.py:95-139`) returns `None` when Elasticsearch is not configured or the advisory lock is held. Datasource actors (`src/codemie/triggers/actors/datasource.py`) log and `return` when index info or credentials are missing. `_tracked_run_sync` (`src/codemie/triggers/bindings/cron.py:158`) ignores the return value and writes `completed`.
2. **Dropped run leaves no row.** With APScheduler `max_instances=1`, a firing that overlaps a running job (EVENT_JOB_MAX_INSTANCES), or a missed one (EVENT_JOB_MISSED), never calls the job function. `Cron.__on_job_event` (`cron.py:276`) only logs it.

## Approach

**Outcome contract (gap 1).** Datasource actors keep returning `None` for "ran". A run that did not start returns a small dict `{"status": "skipped" | "failed", "reason": str}`. This is the same retval-dict style `_tracked_run` already uses.

- `with_datasource_job_lock` returns `skipped` with a reason for "Elasticsearch not configured" and "lock held by another process". Existing warning logs stay.
- The actors' config-missing early returns return `failed` with the reason. That is every early `return` that follows `logger.error(REINDEX_FAILED_MSG, ...)` (missing index info, missing credentials, missing repo). `reindex_git_faq` follows the same pattern; its return value is simply unused today.
- `_tracked_run_sync` inspects the actor's return value:
  - `None` keeps today's `completed` path.
  - `skipped` writes `status="skipped"`, `finished_at`, `duration_ms`, `result_data={"content": reason}` and no `metrics`. `result_data.content` already reaches the run detail view (`scheduler_run_service.py:174`).
  - `failed` writes `status="failed"` and `error_data={"message": reason, "type": "ReindexNotStarted"}`. `error_data` is already rendered as an ERROR log entry (`scheduler_run_service.py:102-109`).
- Callers that ignore the return value are unaffected: the stale-indexing watchdog (`resume_stale_datasource`) and manual reindex.

**Listener rows (gap 2).** `__on_job_event` writes a `scheduler_run` row for EVENT_JOB_MAX_INSTANCES and EVENT_JOB_MISSED:

- `status="skipped"`, `trigger="scheduled"`, `scheduler_id=job_id`, `input_data={"resourceId": job_id}`.
- `started_at` and `finished_at` come from `scheduled_run_time` (now, when absent), with `duration_ms=0`.
- `result_data.content` carries a reason that distinguishes the two cases: "previous run still in progress" for max-instances, "missed its scheduled time" for missed.
- Rows are written only for jobs registered from scheduler settings (`job_id` present in `Cron.jobs`), so internal jobs (`__watch_settings`, `__watch_stale_indexing`) never produce rows. This needs the handler to reach `self.jobs`, so it stops being a `@staticmethod`.
- The write is wrapped so any exception is logged and never propagates into the scheduler. The existing WARNING logs are kept.

**Status value.** `skipped` is added to `SchedulerRunStatus` (`rest_api/models/scheduler_run.py`). The `status` column is a free string, so no migration is needed.

**Logging fix.** `reindex_confluence` missing-credentials branch (`datasource.py:277-283`) omits `payload.index_info.index_type`: 4 arguments for 5 `%s` placeholders, so the message is dropped. Add the missing argument. Every other `REINDEX_FAILED_MSG` call site was checked and passes 5 arguments.

## Acceptance criteria

1. A scheduled datasource run whose actor hits a held advisory lock is recorded `skipped` with a reason, not `completed`.
2. A scheduled datasource run with Elasticsearch not configured is recorded `skipped` with a reason.
3. A scheduled datasource run whose actor returns early on missing index info, credentials or repo is recorded `failed` with the reason in `error_data`.
4. A normal scheduled run is still recorded `completed` with metrics, and "Updated" moves as before.
5. An EVENT_JOB_MAX_INSTANCES event for a registered scheduler job creates a `skipped` run row with an "in progress" reason.
6. An EVENT_JOB_MISSED event for a registered scheduler job creates a `skipped` run row with a "missed" reason.
7. Events for unregistered or internal job ids create no row.
8. A DB failure while writing a listener row is logged and does not raise out of the listener.
9. The `reindex_confluence` missing-credentials log call supplies 5 arguments for 5 placeholders.
10. `get_stats` and `SchedulerRunStats` are unchanged. `successRate` is unchanged. Skipped runs appear in `total` and in the run list only.
11. Manual reindex, the stale-indexing watchdog, `last_reindex_triggered_at` and "Updated" semantics are unchanged.

## Non-goals

- Restart-orphaned `running` scheduler_runs rows.
- `reindex_git_faq` having no run row (scheduled without `_tracked_run_sync`).
- `DatasourceConcurrencyManager` queue-timeout no-op still recorded `completed`.
- Any UI change, new API field, stats counter or `successRate` change.
- Any DB schema or migration change.
- Commit c048ee1be (workflow-generator validation) and "Last indexed" (`last_reindex_triggered_at`, EPMCDME-14857).
- Changing manual reindex or the stale-indexing watchdog.
- Retrying or rescheduling a skipped run.

## Testing notes

Tests are written only when requested (repo policy). Existing homes are `tests/codemie/triggers/bindings/test_cron.py`, `tests/codemie/triggers/test_job_lock.py` and `tests/codemie/triggers/actors/test_actor_datasource.py`.
