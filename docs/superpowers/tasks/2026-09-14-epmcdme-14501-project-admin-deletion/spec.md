# EPMCDME-14501: Fix Project Admin Inheritance on Same-Name Project Recreation

## Problem

When a project is deleted and a new project is created with the same name, the original Project Admin gains unauthorized access to the new project. The root cause is in `ProjectService.delete_project` (`src/codemie/service/project/project_service.py`): the method removes the `Application` row but never removes the creator's `UserProject` row. Because `user_projects.project_name` is a plain `VARCHAR(255)` with no FK to `applications`, the orphaned row persists and re-attaches to any future project sharing that name. Every RBAC query (`is_admin`, `has_access`, `get_project_names_for_user`, `can_project_admin_view_user`) uses `project_name` as the join key, so the orphaned row immediately grants the old creator Project Admin access on the new project.

## Scope

Single service method change: `ProjectService.delete_project`. No schema migration, no router changes, no new dependencies. All repository methods needed already exist.

## Design

### Primary fix — remove creator membership on deletion

After all pre-deletion guards pass (no non-creator assigned users, no active resources) and `application_repository.delete_by_name` is called, the service must also remove the creator's `UserProject` row via `UserProjectRepository.remove_project`. This follows the existing pattern in `delete_project` where `budget_repository.clear_project_on_deleted_budgets` is called to clean up related rows after the `Application` is deleted.

### Secondary fix — cache invalidation for the creator

`invalidate_user_from_cache(user.id)` is called on the project creation path for the creating user. The same call must be added to the deletion success path so the old creator's cached RBAC session is immediately invalidated and does not transiently reflect stale Project Admin membership.

### Secondary fix — budget assignment cleanup for the creator

`ProjectMemberBudgetAssignment` rows for the creator are only soft-deleted through the explicit remove-user flow (`ProjectAssignmentService._sync_project_budget_member_removed`), which is not invoked by the deletion path today. This method must also be triggered for the creator during project deletion to avoid phantom budget allocations surviving the project.

### Unchanged behavior

The duplicate-name guard in `create_shared_project` relies on `Application` row absence after a physical delete — this remains correct and is not changed. `application_repository.delete_by_name` continues to use physical deletion. No router, API contract, or response model changes are made.

## Acceptance Criteria

1. After deleting a project and creating a new project with the same name, the original creator has no `UserProject` row and no Project Admin role in the new project.
2. `ProjectService.delete_project` calls `user_project_repository.remove_project` for the creator's `UserProject` row on every successful deletion path.
3. `ProjectService.delete_project` calls `invalidate_user_from_cache` for the creator on every successful deletion path.
4. `ProjectAssignmentService._sync_project_budget_member_removed` (or its equivalent) is triggered for the creator's budget rows on project deletion.
5. A new unit test in `tests/codemie/service/project/test_project_service_delete_update.py` covers the delete-then-same-name-recreate sequence and asserts the old creator has no membership in the new project.
6. Existing tests extended to assert membership cleanup and cache invalidation are invoked on the deletion success path.
7. All existing tests in `test_project_service_delete_update.py`, `test_project_service.py`, `test_user_project_repository_basic.py`, and `test_projects_router.py` continue to pass without modification.

## Non-Goals

- Adding a database-level FK constraint (`ON DELETE CASCADE`) between `user_projects.project_name` and `applications.name` — deferred to a separate schema hardening task.
- Changing `Application.deleted_at` from physical to soft deletion.
- Changing how RBAC queries are keyed (`project_name` remains the natural key throughout the RBAC layer).
- Modifying any router, API contract, or response model.
- Cleaning up `UserProject` rows for non-creator assigned users — these are already blocked at deletion time by the 409 guard.
- Backfilling orphaned `UserProject` rows from past deletions — no data migration.
