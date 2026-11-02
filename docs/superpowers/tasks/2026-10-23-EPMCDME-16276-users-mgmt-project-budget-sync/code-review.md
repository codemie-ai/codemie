---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 26ed1d14ce87f3e486469558d7b53da723885dca
---

# Code review — 2026-10-23-EPMCDME-16276-users-mgmt-project-budget-sync

**Decision:** approve (check round, confidence final high · check high)  
**Reviewed head:** `26ed1d1` · **Rounds:** final → check

**Closed:** 1 fixed · 0 moot · 0 decided

Targeted check of the one prior blocking finding: CR-001 resolved. At HEAD fce5613f0, TestPersonalProjectGuard runs the real _reject_if_personal_project for assign, update and remove, covering unrelated-404, admin-403 and owner-403, and asserts no repo mutation and no activity event; the guard runs first in all three methods. The docstring now names that class. The .codemie/codemie-cli.config.json hunk is an unrelated, uncommitted user change and was not graded. Defer items from the final round are not re-checked.

## Open — 0

## Pre-existing (not blocking) — 3

- **Provider allocation deleted before DB commit** — `src/codemie/service/project/project_assignment_service.py:294` — `_sync_project_budget_member_removed` calls `provider.delete_member_allocation` inside the open transaction; if `session.commit()` later fails, the provider allocation is gone while the DB membership/PMBA rows remain. Pre-existing: PAS behavior already used by the Projects-management router; spec Non-goals keep ProjectAssignmentService unchanged.
- **Concurrent grant surfaces IntegrityError as 500** — `src/codemie/service/project/project_assignment_service.py:400` — two concurrent POSTs for the same user/project both pass the existence check and the second commit raises an unhandled unique-constraint error (500 instead of 409). Pre-existing: the old UserAccessService grant had the same check-then-insert with no IntegrityError handling, and PAS is out of scope per spec.
- **PAS single assign/remove tests do not assert budget sync** — `tests/codemie/service/project/test_project_assignment_service.py:117` — `test_assign_user_success` / `test_remove_user_success` never assert `_sync_project_budget_member_added/_removed` is called. Pre-existing: PAS test-suite gap predating this change; the sync helpers themselves have direct unit tests and PAS is unchanged.

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| Design 1: grant/update/revoke become thin wrappers delegating to ProjectAssignmentService, commit, invalidate, log, return existing message | pass | _apply_membership_change; positional args match PAS signatures |
| Design 1: ProjectAssignmentService imported lazily inside the methods | pass | lazy import inside each of the three methods |
| Design 2: auto-create removed; missing project returns 404 and get_or_create is not called | pass | get_by_name None -> 404; tests assert get_or_create not called |
| Design 3: validation, guards and errors come from ProjectAssignmentService | pass | UAS checks removed; PAS runs personal, creator, UUID, user and assignment checks |
| Design 4: side effects (PMBA, provider allocation, cache eviction, activity events) come from ProjectAssignmentService | pass | delegation only; PAS calls budget sync helpers and inserts USER_PROJECT_* events |
| Design 5: UAS helpers without callers deleted; still-used error constants kept | pass | both helpers and unused imports/constants removed; remaining _ERRORS keys referenced |
| Design 6 / Non-goals: PAS, routers, models, default endpoints, frontend unchanged | pass | changed files limited to user_access_service.py and its two test files |
| AC1: DELETE soft-deletes PMBAs, deletes provider allocation, evicts cache, records USER_PROJECT_REMOVED via remove_user_from_project | pass | revoke delegates to remove_user_from_project; delegation test present |
| AC2: DELETE of project creator returns 400 and changes nothing | pass | PAS _reject_if_creator raises before mutation; no commit on raise |
| AC3: POST creates PMBAs and records USER_PROJECT_ASSIGNED via assign_user_to_project | pass | grant delegates to assign_user_to_project; delegation test present |
| AC4: POST on missing project returns 404 and creates neither project nor membership | pass | 404 before delegation; test asserts no PAS call, no get_or_create, no commit |
| AC5: PUT records USER_PROJECT_ROLE_UPDATED via update_user_project_role | pass | update delegates; delegation test present |
| AC6: error status codes unchanged, auth cache invalidated after commit, success body unchanged | pass | codes from PAS; invalidate after with-block commit; message dicts unchanged |
| AC7: membership and budget mutations commit in one transaction; nothing committed on raise | pass | single session, single commit; PAS methods do not commit; no-commit-on-raise tests |
| AC8: ProjectAssignmentService and Projects-management routers unchanged and their tests still pass | pending-stage-7 | no PAS/router change in diff; passing-suite evidence owned by Stage 7 |
| AC9: UAS unit tests cover AC1-AC7: delegation args, 404 missing project, commit/invalidate ordering, no commit on raise | pass | Grant/Update/Revoke test classes plus parametrized propagation test |

Standards: commit-format pass · code-quality pass · security pass
