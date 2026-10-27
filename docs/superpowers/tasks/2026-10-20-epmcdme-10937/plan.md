# EPMCDME-10937 Record Skipped Scheduled Runs Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Scheduled datasource runs that do nothing are recorded `skipped`/`failed`, and APScheduler max-instances/missed drops get a `scheduler_run` row.

**Spec:** `docs/superpowers/tasks/2026-10-20-epmcdme-10937/spec.md`. Commit per task using the repository's existing convention (`EPMCDME-10937: Description`).

**Constraints:** No schema, API, UI, stats or `get_stats` change. Do not touch manual reindex, the watchdog (`resume_stale_datasource`) or commit c048ee1be. Callers that ignore actor return values must stay unaffected.

## Task 1: Outcome contract and `_tracked_run_sync`

**Files:** Modify `src/codemie/rest_api/models/scheduler_run.py` (`SchedulerRunStatus`), `src/codemie/triggers/job_lock.py:95-139`, `src/codemie/triggers/bindings/cron.py:158-196`. Test `tests/codemie/triggers/bindings/test_cron.py`, `tests/codemie/triggers/test_job_lock.py`.

**Produces:** `SchedulerRunStatus.SKIPPED = "skipped"`; `reindex_outcome(status: str, reason: str) -> dict` in `job_lock.py`, returning `{"status": status, "reason": reason}` (used by Task 2).

- [ ] Write failing tests:
  - `test_job_lock.py`: ES-not-configured and lock-held (lines ~105-117, ~224-238) return `{"status": "skipped", "reason": <non-empty>}`; update their `result is None` assertions.
  - `test_cron.py`: replace `test_tracked_run_sync_lock_skip_recorded_as_completed` (L1077) with a skipped-dict case asserting `status == "skipped"`, `result_data == {"content": reason}`, `finished_at`, `duration_ms`, and no `metrics` key. Add a failed-dict case asserting `status == "failed"` and `error_data == {"message": reason, "type": "ReindexNotStarted"}`. Keep the `None` -> `completed` + metrics tests (L1036, L1140) unchanged.
- [ ] Implement: add the enum member and `reindex_outcome`. In `with_datasource_job_lock` replace the two `return None` skips (L111, L134) with `reindex_outcome("skipped", ...)`, keeping the warning logs. In `_tracked_run_sync` capture `actor_fn(**kwargs)`'s return and branch on `dict` status `skipped` / `failed`, otherwise the existing completed path. Use the string literals the function already uses.
- [ ] Run `pytest tests/codemie/triggers/test_job_lock.py tests/codemie/triggers/bindings/test_cron.py -q`; expect PASS.

Test-first: yes — a held lock or unconfigured ES currently yields `None`/`completed`; tests expect `skipped`/`failed` with reason.

## Task 2: Actor early returns and Confluence log arity

**Files:** Modify `src/codemie/triggers/actors/datasource.py` (the 16 bare `return` statements that directly follow a `logger.error(REINDEX_FAILED_MSG, ...)` call, in reindex_code, svn, jira, confluence x2, google, git_faq, azure wiki x2, azure work item x2, xray, sharepoint x2, xwiki x2; L105-766), and the L277-283 call. Test `tests/codemie/triggers/actors/test_actor_datasource.py`.

- [ ] Write failing tests: (a) `reindex_confluence` with no confluence index info returns `{"status": "failed", "reason": <the error_msg>}`; (b) with missing creds likewise, and the `logger.error` mock call has 6 positional args (format string plus 5), the fourth being `payload.index_info.index_type`; (c) parametrized or one more case for `reindex_svn` missing repo. Patch the lock to acquire and ES as configured as the existing tests do.
- [ ] Implement: at each of those returns use `return reindex_outcome("failed", error_msg)` (import from `codemie.triggers.job_lock`; `error_msg` is already in scope at all 16). In `reindex_confluence` missing-creds branch add `payload.index_info.index_type` as the first argument after `REINDEX_FAILED_MSG`.
- [ ] Run `pytest tests/codemie/triggers/actors/test_actor_datasource.py -q`; expect PASS.

Test-first: yes — these paths return `None` today and the creds branch passes 4 args for 5 placeholders.

## Task 3: Listener rows for max-instances and missed

**Files:** Modify `src/codemie/triggers/bindings/cron.py:275-305` (`__on_job_event`; drop `@staticmethod`, take `self`; `add_listener` at L265 already passes `self.__on_job_event`). Test `tests/codemie/triggers/bindings/test_cron.py`; call via `cron._Cron__on_job_event(event)`.

- [ ] Write failing tests with a `SchedulerRunRepository` patch (same patch style as the `_tracked_run_sync` tests) and a `Cron()` whose `jobs` holds `"job-1"`:
  - MAX_INSTANCES event for `job-1` -> `create` called once with a `SchedulerRun` having `status="skipped"`, `trigger="scheduled"`, `scheduler_id="job-1"`, `input_data={"resourceId": "job-1"}`, `duration_ms=0`, `result_data["content"]` containing "in progress".
  - MISSED event -> same shape, content containing "missed", `started_at` equal to the event's `scheduled_run_time`.
  - Unregistered id and an internal job id (not in `cron.jobs`) -> `create` not called.
  - `create` raising -> no exception escapes, error logged.
  - EVENT_JOB_ERROR -> no row (log only).
- [ ] Implement: for MISSED and MAX_INSTANCES, after the existing warning, if `event.job_id in self.jobs` write the row via a small private helper (`started_at = getattr(event, "scheduled_run_time", None) or now`; `finished_at = started_at`), wrapped in `try/except Exception` that logs with `%s` formatting and swallows. Import repository/model lazily like `_tracked_run_sync` does.
- [ ] Run `pytest tests/codemie/triggers/bindings/test_cron.py -q`; expect PASS.

Test-first: yes — the listener only logs today, so no row exists for a dropped run.

## Negative-constraint check

negative-constraints honored: no migration/schema (Task 1 adds an enum value only; `status` is a free string); `get_stats`/`SchedulerRunStats` untouched (spec AC10); watchdog and manual reindex ignore return values and are untouched; `reindex_git_faq` gets no run row (only its return value changes, unused today); concurrency-queue timeout and orphaned `running` rows are not handled; listener swallows DB errors (AC8).
