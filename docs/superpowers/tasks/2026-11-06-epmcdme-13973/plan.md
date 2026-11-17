# EPMCDME-13973 Project-mismatch guard for scheduler and webhook triggers - Implementation Plan

**Goal:** Block scheduler runs whose target now lives in another project than the scheduler, and make the existing webhook 401 guard auditable (log + failure metric).
**Spec:** `spec.md` in this folder. **Research:** `technical-analysis.md`.
**Conventions:** Commit per task using the repository's existing convention. Do not run the whole-suite gates (the flow owns them). Run only the named test files per task.

Fire-time, uncached checks only. No changes to registration, job kwargs, cache TTL, `belongs_to_project` helpers, `SettingsTransferService`, the webhook 401/404 codes, or `SchedulerRun` schema.

## Task 1: Webhook mismatch is logged and metered

**Files:** modify `src/codemie/triggers/bindings/webhook.py` (`handle_assistant` ~l.528, `handle_workflow` ~l.548, `handle_datasource` ~l.576); extend `tests/codemie/triggers/bindings/test_webhook_project_guard.py`.

Test-first: yes - for each of assistant, workflow, datasource a mismatching project still yields 401 AND `logger.error` is called and `WebhookMonitoringService.send_webhook_invocation_metric` is called with `success=False` and `additional_attributes={"error_cause": "project_mismatch"}`; a matching project passes the guard (no 401, background task/invoke reached).

- [ ] Add a private classmethod `_reject_project_mismatch(setting, resource_type, resource_id, detail)` on `WebhookService`: `logger.error` with webhook alias, `setting.credential("webhook_id")`, resource type/id and `setting.project_name` (printf `%s` style, as in the other rejections around l.156); call `send_webhook_invocation_metric` exactly like the `owner_not_active` branch (l.162-171) but `error_cause="project_mismatch"`; then raise the same `HTTPException(401, detail)`.
- [ ] In the three mismatch branches replace the bare `raise HTTPException(401, ...)` with a call to the helper, passing the existing detail strings unchanged. Use `ResourceType.*.value` for `resource_type`.
- [ ] Extend the guard test file (existing 3 tests are the template): patch the module `logger` and `WebhookMonitoringService`, add the assertions above and one matching-project case per resource type. Datasource matching case must also patch `resolve_trigger_user` as the existing tests do.

## Task 2: Scheduler guard for assistant and workflow runs

**Files:** modify `src/codemie/triggers/bindings/cron.py` (`_tracked_run` l.85-138, new helpers next to `_skipped_for_inactive_owner` l.141); modify `tests/codemie/triggers/bindings/conftest.py`; new `tests/codemie/triggers/bindings/test_scheduler_project_guard.py` (model on `test_deactivated_trigger_owner.py`: `run_repo` fixture, `_assert_skipped`, `AsyncMock` actor, `@pytest.mark.asyncio`).

Test-first: yes - `_tracked_run` with a scheduler Settings row in proj1 and an assistant (then workflow) for which `belongs_to_project` is False does not call `actor_fn`, updates the run to `status="skipped"` with `result_data.content`, logs an error and returns None; with `belongs_to_project` True it calls the actor; with `Settings.get_by_id` raising (missing row) or no `assistant_id`/`workflow_id` it is blocked (fail closed).

- [ ] Add module helper `_project_mismatch(scheduler_id, resource_type, resource_id) -> bool` in `cron.py`:
  - `Settings.get_by_id(scheduler_id)` inside try/except Exception, read fresh (no `CacheManager`); on failure or empty `resource_id` log error and return True.
  - Dispatch on `resource_type` ("assistant" -> `AssistantService.belongs_to_project`, "workflow" -> `WorkflowService.belongs_to_project`, "datasource" -> `IndexService.belongs_to_project`). Import all three **lazily inside the function** (import-cycle risk; `cron.py` already lazy-imports repositories in `_tracked_run`). Task 3 supplies the datasource case's tests.
  - Falsy result -> `logger.error("Blocked scheduler %s: %s %s not found or not in scheduler project '%s'", ...)` and return True. Never raise.
- [ ] Add `_skipped_for_project_mismatch(repo, run, started_at, scheduler_id, resource_type, resource_id) -> bool` mirroring `_skipped_for_inactive_owner`: when `_project_mismatch(...)` is True, update the run to `skipped` with `finished_at`, `duration_ms`, `result_data={"content": "The scheduled resource does not belong to the scheduler's project"}` and return True.
- [ ] In `_tracked_run`, right after the owner-inactive check (l.109-110), derive `(resource_type, resource_id)` from `kwargs["assistant_id"]` / `kwargs["workflow_id"]` and return None when `_skipped_for_project_mismatch` is True.
- [ ] `tests/codemie/triggers/bindings/conftest.py`: add an autouse fixture patching `codemie.triggers.bindings.cron._project_mismatch` to `return_value=False` (same style as the `is_trigger_owner_active` patch at l.20-27) so existing `test_cron.py`, `test_cron_prompt_extension.py` and `test_deactivated_trigger_owner.py` tests, which pass no real Settings row, keep passing. The new test file overrides it by patching the underlying `Settings`, `AssistantService` and `WorkflowService` and calling the real function (re-patch `_project_mismatch` to `wraps` the original, or capture the original at import time of the test module).

## Task 3: Scheduler guard for datasource runs

**Files:** modify `src/codemie/triggers/bindings/cron.py` (`_tracked_run_sync` l.200-247, `_run_unless_owner_inactive` l.159-165); extend `tests/codemie/triggers/bindings/test_scheduler_project_guard.py`.

Test-first: yes - `_tracked_run_sync` with a payload whose `index_info.id` maps to a datasource now in another project does not call `actor_fn`, records `skipped`, returns None; `_run_unless_owner_inactive` in the same situation returns None without calling `actor_fn`, logs an error and creates no run; matching project runs the actor on both; a payload with no `index_info` / no id, a missing Settings row and a missing datasource are blocked on both; `payload=MagicMock()` and a missing payload do not raise.

- [ ] Datasource resource id is `getattr(getattr(payload, "index_info", None), "id", None)` (payload from `kwargs.get("payload")`; tolerate absent payload). In `_tracked_run_sync` call `_skipped_for_project_mismatch(repo, run, started_at, scheduler_id, "datasource", resource_id)` after the owner-inactive check (l.217-219) and return None when True.
- [ ] In `_run_unless_owner_inactive`, after the owner check, `if _project_mismatch(scheduler_id, "datasource", resource_id): return None` (no run exists; the helper already logs).
- [ ] Datasource branch of `_project_mismatch` calls `IndexService.belongs_to_project(resource_id, setting.project_name)`; it reads a fresh `IndexInfo` by id (not the payload's `IndexInfo`). Code runs in the APScheduler thread pool, which is fine because `Settings.get_by_id` / `IndexInfo.find_by_id` open their own sessions. Missing `belongs_to_project` result (None/False) is blocked.
- [ ] Tests: patch `Settings.get_by_id`, `IndexService.belongs_to_project` at its source module (lazy import resolves there) and use the real `_project_mismatch` as in Task 2.

## Self-review

- Coverage of spec criteria 1-6: AC1 Task 2; AC2 Task 3; AC3 Task 1; AC4 match cases in all three tasks; AC5 helper reads fresh, no cache/payload project; AC6 tests in Tasks 1-3 (missing Settings row, missing target included).
- negative-constraints: honored. No registration-time check or workflow existence check in `Cron.__fetch_resource_details`/`__valid_setting` (no task touches them); webhook 401 and save-time 404 unchanged (Task 1 keeps detail strings); no move hook, pruning or `SettingsTransferService` change; no log/metric on webhook not-found 404 paths (Task 1 touches only mismatch branches); no change to `belongs_to_project` helpers, `SchedulerRun` status/field or migration (only existing `skipped` and `result_data`); no change to job kwargs, registration or cache TTL (helper re-reads Settings); no `SchedulerRun` for the Git FAQ path (Task 3 logs and returns None).
