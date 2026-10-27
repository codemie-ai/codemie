---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 01751c54c94eb2c0b65973425b1e1d6cadbf1164
---

# Code review — 2026-10-20-epmcdme-10937

**Decision:** approve (check round, confidence final high · check high)  
**Reviewed head:** `01751c5` · **Rounds:** final → check

**Closed:** 1 fixed · 0 moot · 0 decided

CR-001 resolved: cron.py now only submits the scheduler_run insert to a single-worker ThreadPoolExecutor from the listener (submit wrapped in try/except, write wrapped in its own try/except, executor shut down in shutdown()), so the event loop no longer blocks; a new test asserts the write runs off the listener thread. No new findings discovered by design.

## Open — 0

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| 1. Held advisory lock recorded skipped with reason, not completed | pass | job_lock.py + cron.py skipped branch; tested |
| 2. Elasticsearch not configured recorded skipped with reason | pass | job_lock.py returns skipped outcome; tested |
| 3. Actor early return on missing info/credentials/repo recorded failed with reason | pass | datasource.py returns reindex_outcome failed; error_data type ReindexNotStarted |
| 4. Normal scheduled run still completed with metrics | pass | else-branch unchanged |
| 5. EVENT_JOB_MAX_INSTANCES creates skipped row with in-progress reason | pass | cron.py __record_dropped_run; tested |
| 6. EVENT_JOB_MISSED creates skipped row with missed reason | pass | started_at from scheduled_run_time; tested |
| 7. Unregistered/internal job ids create no row | pass | early return when job_id not in self.jobs; tested |
| 8. DB failure in listener logged, not raised | pass | try/except Exception with logger.error; tested |
| 9. reindex_confluence log call arity fixed | pass | 5 args for 5 placeholders; tested |
| 10. Stats, SchedulerRunStats and successRate unchanged | pass | only SKIPPED enum added |
| 11. Manual reindex, watchdog, last_reindex_triggered_at unchanged | pass | resume_* and manual paths untouched |
| Outcome contract: None for ran, dict skipped\|failed with reason | pass | reindex_outcome helper |
| _tracked_run_sync skipped/failed field writes | pass | no metrics on skipped; ReindexNotStarted on failed |
| Listener row fields | pass | status skipped, trigger scheduled, duration_ms 0, resourceId input |
| skipped added to SchedulerRunStatus, no migration | pass | SKIPPED enum only |
| Non-goals respected | pass | no UI/API/stats/schema change |

Standards: commit-format pass · code-quality pass · security pass
