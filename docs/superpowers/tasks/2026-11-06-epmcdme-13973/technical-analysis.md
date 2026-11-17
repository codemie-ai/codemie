# Technical Research

**Task**: scheduler webhook triggers project-ownership assistant workflow (EPMCDME-13973)
**Generated**: 2026-11-06
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13973, Bug, "Webhook and scheduler can trigger assistants or workflows after their project is changed".

**Description**
Webhook and scheduler configurations can still trigger assistants or workflows after the referenced assistant/workflow has been moved to a different project.
When a webhook or scheduler is configured in a project and points to an assistant or workflow from the same project, it works as expected. However, if the referenced assistant or workflow is later moved from the original project to another project, the existing webhook or scheduler remains in the original project and can still trigger that assistant/workflow. This violates expected project isolation and may allow automation from one project to execute an assistant or workflow that now belongs to another project.

Preconditions: user has access to CodeMie; projects {proj1} and {proj2} exist; an assistant or workflow exists in {proj1}; a scheduler or webhook exists in {proj1} configured to trigger it.
Steps: 1-4 create assistant/workflow in proj1, create scheduler/webhook in proj1 targeting it, verify it triggers. 5 change the assistant/workflow project from proj1 to proj2. 6 keep scheduler/webhook in proj1. 7 trigger it. 8 observe it still triggers the assistant/workflow now in proj2.
Expected: the scheduler or webhook in proj1 must not trigger an assistant/workflow that belongs to proj2. The system should validate project ownership at trigger time and prevent execution when the scheduler/webhook project does not match the referenced assistant/workflow project.
Actual: it still triggers.
Affected areas: scheduler execution logic; webhook execution logic; assistant/workflow project ownership validation; backend access control and project isolation; auditability of automated executions.

**Acceptance criteria (from description)**
- When an assistant/workflow is moved from proj1 to proj2, any scheduler or webhook that remains in proj1 must not be able to trigger it.
- Scheduler execution validates that the scheduler project matches the target assistant/workflow project before execution.
- Webhook execution validates that the webhook project matches the target assistant/workflow project before execution.
- If a project mismatch is detected, execution is blocked and a clear error is logged.
- Existing valid scheduler/webhook configurations continue to work when the target stays in the same project.
- Automated tests cover both scheduler and webhook trigger scenarios after assistant/workflow project changes.

**Jira comment (prior automated complexity assessment, M 17/36) — unverified hints:**
- The webhook guard from EPMCDME-13996 (commit d88c6f496) reportedly already exists; scheduler side has no precedent. Remaining webhook gap: mismatch rejected silently (no log line, no failure metric).
- Cron jobs only re-evaluated when scheduler setting changes; resource details cached for 300 s, so check must run at fire time (`_tracked_run`) or via a resource-move hook.
- Components: `Cron` (resource validation, job registration, `_tracked_run`) and `WebhookService`, reusing `AssistantService/WorkflowService.belongs_to_project`. ~5-7 files across `src/triggers` and `tests/triggers`.
- Open decisions: (1) fire-time check vs hook on resource move (ms_teams prune-on-move exists only for that integration); (2) record blocked scheduler run as skipped or failed `SchedulerRun`; (3) status code: webhook guard returns 401, save-time validator returns 404; (4) `SettingsTransferService` can move a scheduler/webhook to another project without re-checking resource ownership; (5) cron workflow path (`Cron.__fetch_resource_details`) does not check the workflow exists.

---

## 2. Codebase Findings

Paths below are under `/home/dmytromelnyk4/work/projects/codemie-dev/codemie`. Python / FastAPI / SQLModel / APScheduler; package is `src/codemie/triggers` (not `src/triggers`).

### Existing Implementations

**Webhook side (guard exists - hint CONFIRMED)**
- `src/codemie/triggers/bindings/webhook.py`: `WebhookService.invoke_webhook_logic` (l.97) loads the Settings row, then dispatches to `handle_assistant` (l.519), `handle_workflow` (l.539), `handle_datasource` (l.565). Each already compares `resource.project != setting.project_name` and raises `HTTPException(401)` with detail "... does not belong to the webhook project ...". Added by d88c6f496 (EPMCDME-13996), tests in `tests/codemie/triggers/bindings/test_webhook_project_guard.py` (3 tests, one per resource type, assert 401 only).
- Gap CONFIRMED: the three guards `raise` with no `logger.*` call, and no `WebhookMonitoringService.send_webhook_invocation_metric(success=False, additional_attributes={"error_cause": ...})`. Every other rejection in `invoke_webhook_logic` (not found, disabled, owner_not_active, unsupported_resource_type) logs and emits a failure metric with an `error_cause`; the mismatch and not-found-resource paths do not. Because the handlers raise, the success metric/log at l.217-229 is not reached either, so a blocked call leaves no audit trace beyond the HTTP 401 response.
- Mismatch check uses strict equality (`assistant.project`, `workflow.project`), so marketplace (`is_global`) assistants in another project are also rejected.
- Not-found-resource 404s (`ASSISTANT_NOT_FOUND`, `WORKFLOW_NOT_FOUND`) are likewise unlogged/unmetered.

**Scheduler side (no guard - hint CONFIRMED)**
- `src/codemie/triggers/bindings/cron.py` (1081 lines), class `Cron` (l.267), `CacheManager(cache_ttl=300)` (l.278; `triggers/bindings/cache_manager.py`).
- Flow: `__watch_settings` (l.420) -> `__get_settings` (all `CredentialTypes.SCHEDULER` rows) -> `__actualize_jobs` (l.502) -> `__valid_setting` (l.600) -> `__validate_resource` (l.585, cached) -> `__fetch_resource_details` (l.558) -> `__actualize_cron_job` (l.643) -> `__schedule_job_by_type` (l.728) -> `__schedule_assistant_job` (l.755) / `__schedule_workflow_job` (l.778).
- `__valid_setting` skips settings unchanged since registration (`__updated_setting`, l.536: compares `setting.update_date` with `Job.modified_at`). Moving an assistant/workflow does not touch the scheduler Settings row, so the registered APScheduler job keeps firing indefinitely with the original `assistant_id` / `workflow_id` kwargs.
- `__fetch_resource_details` for `assistant` only checks existence (`validate_assistant`) and returns `project_name: ""`; the scheduler's own `setting.project_name` is never read in `cron.py` for assistants/workflows. For `workflow` there is NO branch at all: it falls to the final `return {... ""}` default, so existence is not checked (hint 5 CONFIRMED). The `project_name` carried into jobs is the datasource's, not the scheduler's.
- Job kwargs (`__schedule_assistant_job` l.762, `__schedule_workflow_job` l.783) carry `actor_fn, scheduler_id, assistant_id|workflow_id, user_id, job_id, task` (+ `trigger_source`). No project name is passed, so a fire-time check in `_tracked_run` has no scheduler project available without either adding a kwarg (stale after a scheduler move, but scheduler moves update `update_date` -> re-registration) or re-reading the Settings row by `scheduler_id` (== `setting.id`).
- `_tracked_run` (l.85, async): creates a `SchedulerRun(status="running", trigger="scheduled")` via `SchedulerRunRepository`, then `_skipped_for_inactive_owner` (l.141) is the precedent for a fire-time guard: updates the run to `status="skipped"` with `result_data={"content": "..."}` and `logger.warning("Skipped scheduler %s: ...")`, returns None before `actor_fn`. `SchedulerRunStatus` has RUNNING/COMPLETED/FAILED/CANCELLED/SKIPPED (`src/codemie/rest_api/models/scheduler_run.py` l.31-36). On exception the run is `failed` with `error_data` and the exception re-raised.
- `_tracked_run_sync` (l.200) and `_run_unless_owner_inactive` (l.159) serve datasource jobs only (out of ticket scope, which names assistants/workflows; datasource scheduler uses `ds_meta.project_name`, never the setting's project).
- Actors `src/codemie/triggers/actors/assistant.py` (`invoke_assistant`) and `workflow.py` (`invoke_workflow`) make an HTTP call to `/v1/assistants/{id}/model` or `/v1/workflows/{id}/executions` signed as `user_id` via `sign_internal_request`; they do no project check. Authorization at that endpoint is per-user, not per-trigger-project, so the trigger-side check is the only place the isolation can be enforced.

**Ownership helpers (reusable)**
- `AssistantService.belongs_to_project(assistant_id, project_name)` - `src/codemie/service/assistant/assistant_service.py:112`; `WorkflowService.belongs_to_project` - `src/codemie/service/workflow_service.py:818`. Both return `bool`, swallow all exceptions and return False (fail-closed on DB error; indistinguishable from a real mismatch or missing resource).
- Save-time validators: `validate_assistant_ownership` / `validate_workflow_ownership` / `validate_datasource_ownership` in `src/codemie/service/settings/settings_request_validator.py` (l.605-660; dispatch at l.580-584) raise `ExtendedHTTPException(404)`. This is only run when a scheduler/webhook Settings row is created/updated through the API.

**Move paths (where drift originates)**
- Assistant move: `AssistantRepository.update` (`src/codemie/service/assistant/assistant_repository.py:226-245`) detects `assistant.project != old_project_name and not assistant.is_global` and calls `_prune_ms_teams_assistant_id_on_project_move` (ms_teams-only precedent; hint CONFIRMED). No equivalent for scheduler/webhook Settings. Another prune call exists at `rest_api/models/assistant.py:1286`.
- Workflow update: `WorkflowService` update (~l.906 passes `project=updated_workflow_config.project`); no side effects on triggers found.
- `SettingsTransferService` (`src/codemie/service/settings/settings_transfer_service.py`): `transfer`/`transfer_setting` (MOVE or COPY of integrations, incl. scheduler/webhook rows) change `project_name` via `_apply`/`_clone` with collision checks only; no re-check of `resource_id` ownership against the target project (hint 4 CONFIRMED by inspection of the method list; `_unlink_orphaned_datasources` handles datasource credentials only). A moved scheduler row has `update_date` changed, so cron re-registers it, but nothing validates the resource.

### Architecture and Layers Affected
- Trigger layer: `triggers/bindings/webhook.py` (HTTP-driven), `triggers/bindings/cron.py` (APScheduler-driven), `triggers/bindings/utils.py` (`validate_assistant`, `is_trigger_owner_active`).
- Service layer: `AssistantService`, `WorkflowService` (ownership helpers).
- Persistence/audit: `SchedulerRun` (Postgres, `SchedulerRunRepository`), `WebhookMonitoringService` metrics.
- Optionally (if a move hook is chosen): `AssistantRepository.update` and the workflow update path, plus `Settings` repository.

### Integration Points
- `cron.py` -> `triggers/actors/{assistant,workflow}.py` -> internal REST API.
- `webhook.py` -> `WebhookMonitoringService` (metrics), `SettingsService.retrieve_setting` (ES query by `webhook_id` credential).
- `cron.py` imports `validate_assistant`, `validate_datasource`, `is_trigger_owner_active` from `triggers/bindings/utils.py`; service-layer imports from `cron.py` would need to be checked for import cycles (cron already lazy-imports repositories inside `_tracked_run`).

### Patterns and Conventions
- Fire-time guards in this package return early and record: `_skipped_for_inactive_owner` (scheduler: `skipped` run + `logger.warning`) and the webhook `owner_not_active` branch (warning + failure metric with `error_cause`, HTTP 200). The project-mismatch guard is the outlier that does neither.
- Webhook rejections use `error_cause` strings in `additional_attributes` (`webhook_not_found`, `webhook_disabled`, `owner_not_active`, `unsupported_resource_type`).
- Cron private methods use name-mangled `__name`; module-level helpers handle run tracking.
- Logging: printf-style `%s` args with project/webhook ids (see `.ai-run/guides/development/logging-patterns.md`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` exists. Relevant: `architecture/layered-architecture.md` (services own business rules, shared exceptions in `codemie.core`), `development/logging-patterns.md`, `development/error-handling.md`, `testing/testing-patterns.md`, `data/repository-patterns.md`. No guide covers the triggers package or scheduler/webhook semantics specifically.
- `docs/superpowers/tasks/` contains earlier task folders touching schedulers (e.g. `2026-09-15-feature-flag-schedulers-view`, `docs/superpowers/plans/EPMCDME-13171-scheduler-xray-sharepoint.md`); not read, peripheral.

### Architectural Decisions
- EPMCDME-13996 (d88c6f496) decided webhook mismatch -> 401 at invocation. Save-time validators decided 404. No ADR on the scheduler.
- Inline comment at `assistant_repository.py` documents that ms_teams `assistant_ids` has no FK/cascade so pruning is done on move; this is the only recorded "prune on move" decision.

### Derived Conventions
- Guards fail closed; owner-inactive style skip is the established scheduler convention.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/triggers/bindings/test_webhook_project_guard.py` - 401 on mismatch for datasource/assistant/workflow (no assertion on logging/metric; no "matching project passes" test here).
- `tests/codemie/triggers/bindings/test_webhook.py`, `test_webhook_github_integration.py` - webhook flow; the latter got a project-matching mock in d88c6f496.
- `tests/codemie/triggers/bindings/test_cron.py` (1410 lines) - cron scheduling, `_tracked_run_sync`, dropped runs.
- `tests/codemie/triggers/bindings/test_deactivated_trigger_owner.py` - the closest template: async `_tracked_run` tests with `run_repo` fixture (patched `SchedulerRunRepository`), `_assert_skipped(repo)` helper, `patch("codemie.triggers.bindings.cron.is_trigger_owner_active")`, `actor = AsyncMock()`, `@pytest.mark.asyncio`.
- `tests/codemie/service/settings/test_settings_request_validator.py` - ownership validators with `belongs_to_project` patched.
- `tests/codemie/triggers/actors/test_actor_{assistant,workflow}.py` - actors.

### Testing Framework and Patterns
- pytest + pytest-asyncio, `unittest.mock` (`patch`, `MagicMock`, `AsyncMock`); `tests/codemie/triggers/bindings/conftest.py` holds shared fixtures. Tests only run when requested (AGENTS.md).

### Coverage Gaps
- No test for scheduler project-mismatch (no such code).
- No test that a blocked webhook logs/emits a metric; no test for a matching-project webhook passing the guard in the guard file.
- No test for cron workflow resource validation.
- No test for the settings-transfer path with respect to resource ownership.

---

## 5. Configuration and Environment

### Environment Variables
- None specific. `config.SCHEDULER_PROMPT_SIZE_LIMIT` used in cron; `config.ENV`, `config.ENABLE_USER_MANAGEMENT` affect owner resolution only.

### Configuration Files
- None relevant found. Cache TTL is hard-coded: `CacheManager(cache_ttl=300)` in `Cron.__init__`.

### Feature Flags and Deployment Concerns
- No feature flag around triggers found. Cron runs in-process on the node holding the job lock (`triggers/job_lock.py`, `node_controller.py`); a code fix needs no migration unless a new `SchedulerRun` field is added (none required for `skipped`, `result_data` already exists).

---

## 6. Risk Indicators

- Speculative: fire-time check in `_tracked_run` needs the scheduler's project; kwargs do not carry it. Options are a new kwarg set in `__schedule_assistant_job`/`__schedule_workflow_job` (call chain `__actualize_jobs` -> `__actualize_cron_job` -> `__schedule_job_by_type` must be threaded; note `project_name` there is currently the datasource's value) or a re-read of the Settings row at fire time (extra ES query per run, but always current).
- Speculative: the 300 s `CacheManager` and the `update_date`-gated registration mean a registration-time check alone would miss moves; the fire-time check must bypass the cache (use `belongs_to_project`, which does an uncached `find_by_id`).
- `belongs_to_project` conflates DB errors, missing resource and mismatch into False; a transient failure would block a legitimate run. Logging should say "not found or not in project" unless a new helper distinguishes them.
- Webhook mismatch (and resource-not-found) is silent: no log, no metric. Directly fails the "clear error is logged" criterion and the auditability concern.
- Status-code inconsistency: webhook guard 401 vs save-time 404 (open decision 3); changing the existing 401 would break `test_webhook_project_guard.py` and potentially API consumers (GitHub/GitLab auto-deactivation behaviour mentioned in the `should_dispatch` comment applies to non-2xx).
- Datasource scheduler/webhook paths are out of the ticket's text; the datasource scheduler path never compares setting project to datasource project (webhook does). Scope decision needed.
- Cron workflow path does not validate existence at all (`__fetch_resource_details` has no workflow branch); a workflow-existence/ownership check is a behaviour change for stale schedulers (previously ran and failed at the REST call).
- `SettingsTransferService` and the API update path can leave a scheduler/webhook pointing at a resource from a different project; a fire-time guard covers both, a move-hook would not.
- A move-hook approach (prune/disable triggers on assistant/workflow project change) touches `AssistantRepository.update` and workflow update, is not retroactive for already-drifted rows, and does not cover the `SettingsTransferService` path; fire-time check is the only fully covering option.
- `is_global` marketplace assistants: webhook guard rejects them cross-project (strict equality); scheduler should be aligned or explicitly differ.
- Multi-node: guard in `_tracked_run` only runs on the job-lock holder; fine.
- Documentation gap: no guide for the triggers package.

---

## 7. Summary for Complexity Assessment

The change is confined to the trigger layer plus reuse of two existing service helpers. The webhook guard already exists (EPMCDME-13996) and only needs observable rejection (log line and failure metric with an `error_cause`, e.g. mismatch), so roughly one file (`triggers/bindings/webhook.py`) with a small edit. The scheduler has no ownership check anywhere: the natural place is `_tracked_run` in `triggers/bindings/cron.py`, mirroring `_skipped_for_inactive_owner` (a `skipped` or `failed` `SchedulerRun` plus a logged warning/error), calling `AssistantService/WorkflowService.belongs_to_project` against the scheduler's project. The main design wrinkle is that job kwargs do not carry the scheduler project, so it must either be threaded through the registration chain or re-read from the Settings row at fire time.

No new technology, schema, migration or config is needed; the pattern (fire-time guard with run tracking) has direct precedent in the same file, and the test template (`test_deactivated_trigger_owner.py`, `test_webhook_project_guard.py`) is close to what the new tests need. Expected surface: `cron.py`, `webhook.py`, optionally `utils.py` (shared helper), plus 2-3 test files (new scheduler guard tests, extended webhook guard tests, maybe a cron workflow validation test). Prior estimate of 5-7 files is plausible; a minimal fix is closer to 2 source + 2-3 test files.

Test posture is good for the surrounding code (dedicated async `_tracked_run` tests and a webhook guard test file) but has no coverage of project-mismatch logging, the scheduler mismatch, or cron workflow validation. Main risks are product decisions rather than technical difficulty: skipped vs failed status, 401 vs 404, fire-time vs move-hook (fire-time recommended by the evidence since it also covers settings transfer and pre-existing drift), datasource scope, and fail-closed handling of transient errors in `belongs_to_project`.

---

## 8. External References

None named by the task. (Commit d88c6f496 and Jira hints were checked against the repository: git history and source confirm the webhook guard; see Section 2.)
