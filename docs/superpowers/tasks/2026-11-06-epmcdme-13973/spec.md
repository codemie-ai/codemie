# EPMCDME-13973: Block scheduler and webhook triggers on project mismatch

## Problem

A scheduler or webhook Settings row belongs to a project (`Settings.project_name`) and
references an assistant, workflow or datasource by id. When that resource is moved to another
project, the trigger is not touched, so it keeps firing a resource it no longer shares a project
with. This breaks project isolation.

Current state (from technical-analysis.md):
- Webhook: the guard exists (EPMCDME-13996). `WebhookService.handle_assistant`,
  `handle_workflow` and `handle_datasource` in `src/codemie/triggers/bindings/webhook.py`
  (~l.519-581) raise 401 on mismatch, but log nothing and emit no failure metric, so a blocked
  call is not auditable.
- Scheduler: no guard. Jobs are registered once per Settings `update_date`, and the resource
  lookup is cached 300 s, so a move is never noticed. `src/codemie/triggers/bindings/cron.py`
  has three run wrappers, none of which checks ownership:
  - `_tracked_run` (~l.85): assistant and workflow jobs.
  - `_tracked_run_sync` (~l.200): datasource jobs (svn, code, jira, confluence, google, azure
    devops, xwiki, xray, sharepoint), payload carries the `IndexInfo` read at registration.
  - `_run_unless_owner_inactive` (~l.159): untracked, Git FAQ datasource jobs only; no
    `SchedulerRun` is created.

## Approach

Check ownership at fire time, on every run, uncached. This is the only option that also covers
moves done through `SettingsTransferService` and triggers that have already drifted. A
resource-move hook was ruled out: it touches assistant and workflow update paths, is not
retroactive, and misses settings transfer. A registration-time check was also ruled out (see
Non-goals).

### Scheduler

One shared helper in `cron.py` decides whether a run is blocked: it re-reads the scheduler's
Settings row (`Settings.get_by_id(scheduler_id)`; the job id equals the Settings id) and
compares the Settings `project_name` with the target's current project, uncached:
- assistant: `AssistantService.belongs_to_project`
- workflow: `WorkflowService.belongs_to_project`
- datasource: `IndexService.belongs_to_project` (`IndexInfo.project_name`, fresh
  `IndexInfo.find_by_id`; not the `IndexInfo` held in the job payload or the 300 s cache)

The helper logs at error level (scheduler id, resource type and id, scheduler project) and
returns blocked. A missing Settings row, a missing target, or a datasource payload without an
`IndexInfo` id all count as blocked (fail closed). Job kwargs and registration are not changed:
kwargs are forwarded verbatim to the actors and a re-read is always current.

Where it is called, next to the existing owner-inactive check in each wrapper:
- `_tracked_run`: resource type and id come from `assistant_id` / `workflow_id` in kwargs.
- `_tracked_run_sync`: resource id is `payload.index_info.id`. The svn and code branches build
  their payload with the same `index_info`, so all repo types go through the same check.
- `_run_unless_owner_inactive`: resource id from `payload.index_info.id`.

On block, the tracked wrappers do not call `actor_fn`, mark the run `skipped` with a
human-readable `result_data.content`, and return None (no exception, the job keeps its
schedule). A small `_skipped_for_project_mismatch` mirroring `_skipped_for_inactive_owner`
is shared by both tracked wrappers. The untracked Git FAQ wrapper has no run to record, so it
logs (via the helper) and returns None, exactly as it already does for an inactive owner.

### Webhook

- Keep the 401 response and detail text unchanged.
- In each of the three existing mismatch branches (assistant, workflow, datasource), before
  raising, log at error level (webhook alias/id, resource type and id, webhook project) and emit
  `WebhookMonitoringService.send_webhook_invocation_metric(success=False, ...)` with
  `additional_attributes={"error_cause": "project_mismatch"}`, matching the other rejections in
  `invoke_webhook_logic`.

## Acceptance criteria

1. A scheduler in proj1 targeting an assistant or workflow now in proj2 does not invoke the
   actor; its run is recorded as `skipped` and an error is logged.
2. A scheduler in proj1 targeting a datasource now in proj2 does not run the reindex actor; its
   run is recorded as `skipped` (`_tracked_run_sync`) or, for the untracked Git FAQ path, not run
   and an error is logged.
3. A webhook in proj1 targeting an assistant, workflow or datasource now in proj2 is rejected
   with 401, an error is logged, and a failure metric with `error_cause=project_mismatch` is
   emitted.
4. Scheduler and webhook with matching project and target still execute as before.
5. The scheduler check runs on every fire and uses neither the 300 s resource cache nor the
   registration-time payload.
6. Automated tests cover: scheduler mismatch and match for assistant, workflow and datasource
   (both `_tracked_run_sync` and `_run_unless_owner_inactive`), including missing Settings row
   and missing target; webhook mismatch (asserting log and metric) and match for assistant,
   workflow and datasource.

## Non-goals

- No registration-time check in `Cron.__fetch_resource_details` / `__valid_setting` (this
  includes adding a workflow existence check there). `__valid_setting` exits early for
  registered, unchanged settings, and returning None does not unregister an existing job, so it
  cannot replace the fire-time check.
- No change to the webhook 401 status code or to save-time 404 validation.
- No resource-move hook, and no pruning/disabling of triggers when a resource changes project.
- No change to `SettingsTransferService`; the fire-time check already covers its output.
- No log or metric for the webhook resource-not-found 404 paths.
- No change to the `belongs_to_project` helpers, no new `SchedulerRun` status or field, no
  migration.
- No change to job registration, job kwargs, or cache TTL.
- No `SchedulerRun` for the untracked Git FAQ path.

## Known trade-offs

- The `belongs_to_project` helpers return False on any exception, so a transient DB error skips
  one run (the next cron tick retries). The log says "not found or not in project" so it is not
  misread as a confirmed mismatch.
- Marketplace (`is_global`) assistants from another project are blocked, identical to the
  existing webhook behaviour and save-time validation.
- Extra reads per scheduled run: one Settings row and one resource row.
- Datasource schedulers that already drifted will start recording `skipped` runs after deploy,
  where today they reindex the moved datasource.
