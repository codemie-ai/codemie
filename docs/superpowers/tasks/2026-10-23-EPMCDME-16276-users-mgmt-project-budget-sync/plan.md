# EPMCDME-16276 Users-mgmt membership delegation: Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** Make the Users-management project add, update and remove operations run the Projects-management logic, including budget sync, activity events and guards, by delegating to `ProjectAssignmentService` (PAS).

**Architecture:** `UserAccessService` (UAS) grant/update/revoke become thin wrappers. Each one opens a session, loads the project (404 if it is missing), calls the PAS method, commits, invalidates the auth cache, logs, and returns the unchanged `{"message": ...}`. PAS is imported lazily inside the methods, as in `src/codemie/rest_api/routers/projects/membership.py:49-110`.

**Tech Stack:** Python, FastAPI, SQLModel, pytest + `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-10-23-EPMCDME-16276-users-mgmt-project-budget-sync/spec.md` (authoritative).

Commit per task using the repository's existing convention.

## Global Constraints
- Do not modify `src/codemie/service/project/project_assignment_service.py`, any router, request/response models, set/clear-default, the frontend, or `.codemie/codemie-cli.config.json`.
- Use the action string `"<METHOD> /v1/admin/users/{user_id}/projects/{project_name}"` (POST / PUT / DELETE).
- Success messages stay exactly `"Project access granted successfully"` / `"Project access updated successfully"` / `"Project access removed successfully"`, and the `project_access_granted|updated|removed` log lines stay.
- Order is commit, then `_invalidate_auth_cache(user_id)`. If PAS raises, there is no commit and no invalidation.
- In tests, patch PAS at `codemie.service.project.project_assignment_service.project_assignment_service`. The lazy import resolves the patched module attribute.

## Review Focus
- A missing project on POST returns 404 `"Project not found"`, and neither `get_or_create` nor PAS is called (T1).
- A PAS exception (for example 400 creator, or 403 personal) propagates unchanged with no commit and no invalidation (T1 grant, T2 update/revoke).
- A missing project on PUT/DELETE also returns 404 before PAS is called (T2).
- The UAS-side user-existence check is dropped. PAS's 404 user-not-found now fires after the project lookup, and both are 404 (T1/T2 tests must not expect `user_repository` calls).
- The removed personal-guard tests in `test_user_access_service_personal_constraints.py` must not be silently lost. PAS tests `test_assign_user_personal_project_rejected` and the related ones cover them. T3 replaces that file with propagation tests.

---

### Task 1: Shared delegation helper and `grant_project_access`

**Files:**
- Modify: `src/codemie/service/user/user_access_service.py:88-112`, and add a private helper near `_invalidate_auth_cache` (L190-195)
- Test: `tests/codemie/service/user/test_user_access_service.py:115-245` (rewrite `TestGrantProjectAccess`)

**Interfaces:**
- Produces: `UserAccessService._apply_membership_change(user_id: str, project_name: str, http_method: str, operation: Callable[[Session, Application, str], object]) -> None`. It does the following:
  1. opens `get_session()`;
  2. `application_repository.get_by_name(session, project_name)`, and if that returns None, raises `ExtendedHTTPException(code=404, message=_ERRORS.PROJECT_NOT_FOUND)`;
  3. builds the action string;
  4. calls `operation(session, project, action)`;
  5. `session.commit()`;
  6. `UserAccessService._invalidate_auth_cache(user_id)`.

  The builder may rename it.

- [ ] **Step 1: Write failing tests** in `TestGrantProjectAccess`. Replace the four existing tests (they assert `get_or_create`, the UAS user check, the UAS personal guard and the UAS 409):
  - `test_grant_delegates_to_assign_user_to_project`: `mock_pas.assign_user_to_project.assert_called_once_with(mock_session, project, "user-123", "project-alpha", True, actor, "POST /v1/admin/users/user-123/projects/project-alpha")`. Also assert the result message, `mock_app_repo.get_or_create.assert_not_called()`, and that the log line contains `project_access_granted`.
  - `test_grant_missing_project_returns_404`: with `get_by_name` returning None, expect a 404 with message `"Project not found"`. Assert that PAS is not called and that `commit` and `get_or_create` are not called.
  - `test_grant_commits_then_invalidates`: record call order through `side_effect` appends on the PAS mock, `mock_session.commit` and `patch.object(UserAccessService, "_invalidate_auth_cache")`, then assert `["pas", "commit", "invalidate"]`.
  - `test_grant_pas_error_propagates_without_commit`: the PAS mock raises `ExtendedHTTPException(code=409, message="x")`. Assert the error propagates with code 409, that `commit` is not called and that invalidate is not called.

  For `test_user_access_service_personal_constraints.py`, mark the grant tests (L39, L146, L176) for rewrite in T3. Until then they fail, so in this task run only the main test file.
- [ ] **Step 2: Run and confirm the tests fail.** Run `poetry run pytest tests/codemie/service/user/test_user_access_service.py::TestGrantProjectAccess -v`. Expected: FAIL, because `get_or_create` is called and PAS is not.
- [ ] **Step 3: Implement.** Add the helper and rewrite `grant_project_access`: call `_apply_membership_change(user_id, project_name, "POST", lambda s, p, a: project_assignment_service.assign_user_to_project(s, p, user_id, project_name, is_project_admin, actor, a))` with a lazy import, then log and return. Remove the UAS user check, the personal guard, the 409 check and `get_or_create`.
- [ ] **Step 4: Run and confirm the tests pass.** Run the same command. Expected: PASS.

Test-first: yes — `TestGrantProjectAccess` asserts delegation args, 404 on missing project, commit→invalidate order, no commit on PAS error.

### Task 2: Delegate `update_user_project_access` and `revoke_project_access`

**Files:**
- Modify: `src/codemie/service/user/user_access_service.py:114-141`, `:197-221`
- Test: `tests/codemie/service/user/test_user_access_service.py:247-495` (rewrite `TestUpdateUserProjectAccess` and `TestRevokeProjectAccess`)

**Interfaces:**
- Consumes: `_apply_membership_change` from T1.

- [ ] **Step 1: Write failing tests.** Use the same four-test shape as T1 for each class:
  - Update delegation: `update_user_project_role.assert_called_once_with(mock_session, project, "user-123", "project-alpha", True, actor, "PUT /v1/admin/users/user-123/projects/project-alpha")` and the message `"Project access updated successfully"`.
  - Revoke delegation: `remove_user_from_project.assert_called_once_with(mock_session, project, "user-123", "project-alpha", actor, "DELETE /v1/admin/users/user-123/projects/project-alpha")` and the message `"Project access removed successfully"`.
  - Missing project: 404 `"Project not found"`, and PAS is not called.
  - Order: `["pas", "commit", "invalidate"]`.
  - PAS error: for revoke, raise `ExtendedHTTPException(code=400, ...)`, standing in for the creator guard (AC2). For update, raise a 404. In both cases, no commit and no invalidation.
- [ ] **Step 2: Run and confirm the tests fail.** Run `poetry run pytest tests/codemie/service/user/test_user_access_service.py::TestUpdateUserProjectAccess tests/codemie/service/user/test_user_access_service.py::TestRevokeProjectAccess -v`. Expected: FAIL, because PAS is not called.
- [ ] **Step 3: Implement.** Rewrite both methods through `_apply_membership_change` with `"PUT"` and `"DELETE"`, keeping the existing log lines and messages. Remove the UAS user check, the personal guard and the repository mutations.
- [ ] **Step 4: Run and confirm the tests pass.** Run the same command. Expected: PASS.

Test-first: yes — update/revoke tests assert delegation args, 404 on missing project, ordering, and no commit when PAS raises (incl. 400 creator).

### Task 3: Remove dead UAS helpers and rewrite the personal-constraints tests

**Files:**
- Modify: `src/codemie/service/user/user_access_service.py`. Delete `_reject_if_personal_project` (L340-369), `_log_project_authorization_failure` (L371-388) and `_ERRORS.PERSONAL_PROJECT_MEMBERSHIP` (L37). Drop the imports that are now unused (`datetime`/`UTC`, plus `Session`/`Application` if the helper's type hints no longer need them; let ruff decide). Keep `PROJECT_NOT_FOUND`, `USER_NOT_FOUND` and `NO_PROJECT_ACCESS`, which still have callers.
- Modify: `tests/codemie/service/user/test_user_access_service.py:718-735`. Delete `test_log_project_authorization_failure`.
- Modify: `tests/codemie/service/user/test_user_access_service_personal_constraints.py`. Replace its five tests with propagation tests: PAS raising 403 / 404 for a personal project, on grant, update and revoke, propagates unchanged with no commit. Patch PAS as in T1.

- [ ] **Step 1: Delete, rewrite and verify.** First run `grep -rn "_reject_if_personal_project\|_log_project_authorization_failure\|PERSONAL_PROJECT_MEMBERSHIP" src tests`. Expected: matches only in `project_assignment_service.py`, which has its own copies and stays untouched. Then run `poetry run pytest tests/codemie/service/user/ tests/codemie/service/project/test_project_assignment_service.py tests/codemie/rest_api/routers/test_user_management_router_crud.py tests/codemie/rest_api/routers/test_projects_router.py -v` and `make ruff`. Expected: all pass and ruff is clean. The router tests mock UAS, so they need no change (AC8).

Test-first: no — dead-code removal plus test-file rewrite; behaviour is already pinned by T1/T2.

---

negative-constraints:
- No proxy or budget-resolution membership check: no task touches `proxy_router.py`, `budget_resolution_service.py` or `project_budget_repository.py`.
- No cleanup of orphaned PMBAs: no migration or script task.
- No cross-worker cache invalidation: honored by T1/T2, which leave `_resolution_cache` handling to PAS.
- No auth-cache invalidation on the Projects-management path: PAS and `membership.py` are untouched (Global Constraints).
- No change to request/response models or to set/clear-default: T1/T2 keep the messages, and `_change_default_project` is untouched.
- No frontend change: no task touches it.
- No auto-create: T1 asserts `get_or_create` is not called.
- PAS unchanged (AC8): no task modifies PAS, and T3 runs its tests.
