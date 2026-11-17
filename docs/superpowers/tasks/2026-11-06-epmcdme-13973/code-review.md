---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: afd186c81773cfedb196e180a255bf9fa790e5c6
---

# Code review — 2026-11-06-epmcdme-13973

**Decision:** approve (check round, confidence final high · check high)  
**Reviewed head:** `afd186c` · **Rounds:** final → check

**Closed:** 2 fixed · 0 moot · 0 decided

Both prior blocking findings are resolved in current source: the project-mismatch guard now lives in cron_project_guard.py (cron.py only imports and calls it) and payload_datasource_id is annotated as payload: object. No new findings are discovered on the check round.

## Open — 0

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| AC1: scheduler in proj1 targeting assistant/workflow now in proj2 does not invoke actor; run recorded skipped; error logged | pass | _tracked_run skips via _skipped_for_project_mismatch before actor_fn; covered by tests for assistant and workflow |
| AC2: scheduler targeting datasource now in proj2 does not run actor (tracked and untracked paths) | pass | _tracked_run_sync and _run_unless_owner_inactive both check payload.index_info.id; tests cover both |
| AC3: webhook mismatch for assistant/workflow/datasource rejected with 401, error logged, failure metric project_mismatch | pass | _reject_project_mismatch used in all three handlers; detail texts unchanged |
| AC4: matching project and target still execute as before (scheduler and webhook) | pass | match tests for tracked, sync, untracked and the three webhook handlers |
| AC5: scheduler check runs on every fire, no 300 s cache or registration-time payload | pass | Settings re-read and belongs_to_project called per run |
| AC6: automated tests cover scheduler and webhook mismatch/match, missing Settings row and missing target | pass | test_scheduler_project_guard.py and test_webhook_project_guard.py |
| Fail closed: missing Settings row, missing target or datasource payload without id counts as blocked | pass | _project_mismatch returns True on any exception or falsy belongs; tested |
| Blocked tracked run: no actor call, status skipped with readable result_data, no exception | pass | shared _skipped_for_project_mismatch |
| Datasource check via belongs_to_project | pass | IndexStatusService.belongs_to_project at index_service.py:293 |
| Non-goals respected: no registration-time check, helpers unchanged, no schema/cache/webhook-code changes | pass | diff touches only cron.py wrappers, webhook.py mismatch branches and tests |

Standards: commit-format pass · code-quality partial (final round) · security pass
