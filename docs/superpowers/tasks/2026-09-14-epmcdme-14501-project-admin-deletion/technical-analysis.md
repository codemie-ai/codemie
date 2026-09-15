# Technical Research

**Task**: project membership rbac deletion
**Generated**: 2026-09-14T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14501: Recreated project with the same name incorrectly inherits Project Admin from deleted project.

After a project is deleted and another project is created with the same name, the Project Admin assignment from the deleted project is incorrectly applied to the new project — a critical RBAC bug.

Preconditions: User A creates a project and becomes its Project Admin. The project is deleted. A new project is created with the same name by a different user.

Expected: New project has clean membership based only on the new creation flow; deleted project assignments do not carry over; project identity is based on unique identifier, not name.

Actual: User A from the deleted project becomes Project Admin of the new project.

Affected areas: project creation backend, deletion logic, membership/role persistence, Project Admin assignment, RBAC.

Acceptance criteria:
1. Recreating a project with the same name does not reuse membership or Project Admin assignments from the deleted project.
2. Deleted project access records are removed or isolated so they cannot affect future projects.
3. Project membership and Project Admin assignment are linked to the unique project identifier, not the name.
4. A user who was Project Admin of a deleted project is not granted access to a same-named new project unless explicitly assigned.
5. Issue is covered by tests for project deletion followed by project name reuse.
6. No regression in valid project creation, deletion, and member-assignment flows.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/project/project_service.py` — `ProjectService.delete_project` and `ProjectService.create_shared_project`; the delete method is where the bug originates
- `src/codemie/service/project/project_assignment_service.py` — `ProjectAssignmentService`; handles assign/remove/bulk operations and budget member sync
- `src/codemie/repository/user_project_repository.py` — `UserProjectRepository`; all `UserProject` CRUD; `get_by_project_name`, `remove_project`, `add_project`
- `src/codemie/repository/application_repository.py` — `application_repository.delete_by_name` (hard delete, line 276–293); `get_by_name_case_insensitive` (no `deleted_at` filter, line 167–170)
- `src/codemie/rest_api/routers/projects.py` — `DELETE /v1/projects/{projectName}` and `POST /v1/projects` route handlers
- `src/codemie/rest_api/models/user_management.py` — `UserProject` SQLModel: `(user_id, project_name)` unique constraint; `project_name` is a plain `VARCHAR(255)` with no FK to `applications`
- `src/codemie/core/models.py` — `Application` model with `deleted_at` field (soft-delete column exists but `delete_by_name` performs a physical delete via `session.delete()`)
- `src/external/alembic/versions/299a10eb05a0_add_user_management_tables.py` — migration that creates `user_projects` table; confirms `project_name` column has no foreign key to `applications`

### Architecture and Layers Affected

- **Router layer**: `projects.py` — `delete_project` and `create_project` endpoints call the service
- **Service layer**: `ProjectService.delete_project` — the source of the omission; does not delete the creator's `UserProject` row before hard-deleting the `Application`
- **Repository layer**: `UserProjectRepository` — provides `get_by_project_name` and `remove_project`; `remove_projects_for_users` bulk-delete method also exists
- **Database schema**: `user_projects` table — no FK constraint from `project_name` to `applications.name`, so orphaned rows survive a hard application delete

### Integration Points

- `invalidate_user_from_cache(user.id)` is called after project creation; the same call is absent from the deletion path for the creator, meaning a cached session for the old creator may already reflect their (stale) membership
- `ProjectAssignmentService._sync_project_budget_member_removed` soft-deletes `ProjectMemberBudgetAssignment` rows; this is only called through the explicit remove-user path, not through project deletion

### Patterns and Conventions

- Project identity throughout the system uses `project_name` (string) as the natural key, not a UUID surrogate. `Application.id` equals `Application.name`.
- `UserProject.project_name` has no FK constraint — project membership rows are decoupled from the `applications` table at the DB level.
- `delete_project` enforces a pre-condition (no non-creator assigned users) but never cleans up the creator's own membership row after the check passes.
- The `Application.deleted_at` field exists for lifecycle auditing but `delete_by_name` uses `session.delete()` (physical row deletion), so the duplicate-name guard in `create_shared_project` relies on row absence, not `deleted_at`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/repository-patterns.md` — establishes that repositories own data access; no mention of cascade cleanup strategy for project deletion
- `.ai-run/guides/architecture/layered-architecture.md` and `.ai-run/guides/architecture/service-layer-patterns.md` — establish the API→Service→Repository layering that the fix must follow

### Architectural Decisions

No ADR or recorded decision was found covering what happens to membership rows when a project is hard-deleted.

### Derived Conventions

- Cleanup of related rows during deletion is handled at the service layer (e.g., `budget_repository.clear_project_on_deleted_budgets` is explicitly called in `delete_project`). The same pattern should apply to `user_projects`.
- Tests for delete logic live in `tests/codemie/service/project/test_project_service_delete_update.py` using `unittest.mock.patch`; no DB session is involved.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/project/test_project_service_delete_update.py` — unit tests for `delete_project`: personal project guard (403), assigned-users guard (409), resource-counts guard (409), success path. Uses mocked repositories.
- `tests/codemie/service/project/test_project_service.py` — tests for `create_shared_project` including duplicate-name guard.
- `tests/codemie/repository/test_user_project_repository_basic.py` — basic CRUD for `UserProjectRepository`.
- `tests/codemie/rest_api/routers/test_projects_router.py` — router-level tests for project endpoints.

### Testing Framework and Patterns

- pytest with `unittest.mock.patch` decorators applied at the service level (repository singletons are patched)
- No DB session fixture in service-layer tests; all repository calls are mocked via `MagicMock`
- Repository tests use an in-memory or test DB fixture (separate concern)

### Coverage Gaps

- No test for the sequence: create project → delete project → create same-named project → assert old creator has no membership in the new project.
- No test that `delete_project` removes the creator's `UserProject` row.
- No test that `invalidate_user_from_cache` is called for the creator on project deletion.

---

## 5. Configuration and Environment

### Environment Variables

- `ENABLE_USER_MANAGEMENT` — gates all project management endpoints; must be true for any of the affected routes to be reachable.

### Configuration Files

- `config/` and `.env.example` — standard config; no project-deletion-specific flags.

### Feature Flags and Deployment Concerns

- No feature flag gates the project deletion path.
- The fix touches only application logic and a migration is not required if the fix is implemented in the service layer (adding a `remove_project` call for the creator before or after hard-deleting the `Application`).

---

## 6. Risk Indicators

- **Root cause is a single missing line**: `delete_project` calls `user_project_repository.get_by_project_name` to check for non-creator users, finds and guards against them, then calls `application_repository.delete_by_name` — but never deletes the creator's own `UserProject` row. All methods needed to fix this already exist in `UserProjectRepository`.
- **No FK cascade at the DB level**: `user_projects.project_name` has no foreign key to `applications.name`, so no DB constraint will catch this class of orphan in the future. Speculative: a FK with `ON DELETE CASCADE` would be a more durable fix but requires a schema migration and regression testing of all cascade paths.
- **`invalidate_user_from_cache` omission on deletion**: creation invalidates the cache for the creating user, but deletion does not invalidate it for the creator. Stale cached sessions for the old creator may briefly show the inherited access even after a correct code fix.
- **`UserProject.project_name` is a recycled name key throughout RBAC**: every RBAC query (`is_admin`, `has_access`, `get_project_names_for_user`, `can_project_admin_view_user`) uses `project_name` as the join key. This makes name reuse structurally unsafe until membership rows are cleaned up on deletion.
- **Budget member allocation rows**: `ProjectMemberBudgetAssignment` rows for the creator are soft-deleted only through the explicit remove-user flow (`_sync_project_budget_member_removed`). If the creator's membership is cleaned via the fix, budget allocation rows for the creator on that project also need to be soft-deleted to avoid phantom budget allocations.
- **Existing delete tests do not assert cleanup**: `test_project_service_delete_update.py` verifies that `delete_by_name` is called on success paths but does not assert that `user_project_repository.remove_project` (or any membership cleanup) is invoked for the creator.

---

## 7. Summary for Complexity Assessment

The bug is a single-point omission in `ProjectService.delete_project` (`src/codemie/service/project/project_service.py`): the creator's `UserProject` row is never removed when a project is deleted. The method guards against non-creator users being present but proceeds to hard-delete the `Application` row without purging the creator's membership. Because `user_projects.project_name` is a plain string with no FK to `applications`, the orphaned row survives and latches onto any future project carrying the same name. All the repository infrastructure needed for the fix (`user_project_repository.remove_project`, `remove_projects_for_users`) exists and is already tested in isolation.

The change surface is narrow: the primary fix is one or two lines in `delete_project` to delete the creator's membership row after the resource checks pass, consistent with how budget rows are already cleaned up in the same method. A secondary concern is `invalidate_user_from_cache`, which should be called for the creator on deletion as it already is on creation. The budget member allocation side-effect (`ProjectAssignmentService._sync_project_budget_member_removed`) should also be triggered for the creator to avoid orphaned `ProjectMemberBudgetAssignment` rows, though this is lower-severity as those rows are soft-deleted anyway.

Test coverage is the main gap: the existing test file for `delete_project` does not cover the name-reuse scenario, and adding that coverage is part of acceptance criterion 5. The fix requires no schema migration if implemented at the service layer, and no new dependencies — the risk of regression in the deletion path is low given the existing guard tests, but the new test must confirm both that membership is cleaned and that the cache is invalidated.

---

## 8. External References

None named by the task.
