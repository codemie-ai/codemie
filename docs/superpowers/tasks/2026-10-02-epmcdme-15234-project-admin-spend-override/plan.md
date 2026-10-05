# Project Admin Spend Override Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let Maintainers and Project Admins of the budget's project override and clear a member's spend allocation. Everyone else gets 403.

**Architecture:** This is a router-only change. A router-local write-access helper pair goes next to `_can_read_project_budget`. Both endpoints drop `Depends(maintainer_access_only)`, resolve the budget's project with `project_budget_service.get_project_budget` **before** calling the service, and then run the helper.

**Tech Stack:** FastAPI, pytest (`pytest.mark.asyncio`, `AsyncMock`/`patch`).

**Spec:** `docs/superpowers/tasks/2026-10-02-epmcdme-15234-project-admin-spend-override/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- Predicate: `user.is_maintainer or user.is_application_admin(project_name)`. Do not use `is_admin_or_maintainer`, and do not reuse `_can_read_project_budget`, because it grants auditors.
- Do not change these: `authentication.py`, `update_project_budget_group`, the inline `admin_project_names` checks, other routers, the service, the repository, the schemas, or the frontend.
- A foreign-project budget returns 403. Do not convert it to 404. An unknown budget returns 404 from `get_project_budget`. Do not add an "assignment is None" branch.
- Do not add a self-override restriction.

## Review Focus

- Denied caller on DELETE: `project_budget_service.clear_member_override` must never be awaited. It mutates before it resolves the project.
- Plain Admin (`is_admin=True`, `is_maintainer=False`, no project-admin role) must get 403. Build the user under `ENV="dev"` so that `resolve_is_admin` does not mask the role.
- An auditor who is also Project Admin of P must pass.
- A Project Admin targeting their own `user_id` must pass.
- The other write routes (`/`, `/{budget_id}`, `/reset`, `/rebalance`) must still carry `maintainer_access_only`.

---

### Task 1: Write-access helper pair

**Files:**
- Modify: `src/codemie/rest_api/routers/project_budget_router.py`. Add the helpers after `_ensure_project_budget_read_access` (L252-260).
- Test: `tests/codemie/rest_api/routers/test_project_budget_router.py`

**Interfaces:**
- Produces: `_can_manage_project_budget_members(user: User, project_name: str) -> bool` and `_ensure_project_budget_manage_access(user: User, project_name: str) -> None`.

Test-first: yes — a parametrized `test_can_manage_project_budget_members` fails with an ImportError. Its cases: maintainer is True, Project Admin of P is True, Project Admin of Q is False, plain Admin is False, auditor is False, auditor who is Project Admin of P is True, regular user is False. A second test, `test_ensure_project_budget_manage_access_raises_403`, checks that the raised exception has code 403, message `"Access denied"`, and details that contain the project name.

- [ ] Write both tests. Build the users with the existing fixtures (`_admin_user`, `_project_admin_user`, `_maintainer_user`, `_regular_user_for_group_write`) or with `User(...)` under `patch.object(config, "ENV", "dev")` and `ENABLE_USER_MANAGEMENT=True`. Run them and confirm they fail.
- [ ] Implement the helpers. The ensure-function mirrors `_ensure_project_budget_read_access`:

```python
def _can_manage_project_budget_members(user: User, project_name: str) -> bool:
    return user.is_maintainer or user.is_application_admin(project_name)


def _ensure_project_budget_manage_access(user: User, project_name: str) -> None:
    if not _can_manage_project_budget_members(user, project_name):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=_ACCESS_DENIED_MESSAGE,
            details=f"You do not have permission to manage member budgets for '{project_name}'.",
            help=_ACCESS_DENIED_HELP,
        )
```

  Match the exception kwargs to the existing read helper.
- [ ] Run `pytest tests/codemie/rest_api/routers/test_project_budget_router.py -k "manage" -v` and confirm it passes.

### Task 2: Gate override and clear on project write access

**Files:**
- Modify: `src/codemie/rest_api/routers/project_budget_router.py:448-493`
- Test: `tests/codemie/rest_api/routers/test_project_budget_router.py`. Update L360-421 and add new endpoint tests.
- Test: `tests/codemie/rest_api/routers/test_project_budget_router_additional.py:155-167`. This is the existing `clear_member_override` call.

**Interfaces:**
- Consumes: `_ensure_project_budget_manage_access` from Task 1.

Test-first: yes — endpoint tests call `override_member_allocation` / `clear_member_override` directly. Patch `get_async_session` and the `project_budget_service.*` methods. The mocked `get_project_budget` returns an assignment with `project_name="P"`. The tests fail for these reasons: a Project Admin of P gets 200 for both PATCH and DELETE, including when the target is their own id, but today `maintainer_access_only` is wired; a Project Admin of Q, a plain Admin, an auditor and a regular user get no 403 from the endpoint body; and for a denied caller, `clear_member_override` / `override_member_allocation` service mocks are awaited.

- [ ] Write the endpoint tests. Cover:
  - AC1: Project Admin of P gets 200 on both endpoints.
  - AC2: Project Admin of Q gets 403.
  - AC3: maintainer gets 200.
  - AC4: plain Admin, auditor and regular user get 403.
  - AC5: `get_project_budget` raising a 404 `ExtendedHTTPException` propagates as 404, and the service is not awaited.
  - AC6: a Project Admin with `user_id == user.id` gets 200.
  - AC7: denied caller, the service is not awaited (`assert_not_awaited`).
- [ ] Update the route-dependency tests for AC8:
  - `test_project_budget_write_routes_keep_maintainer_dependency` keeps only the four non-member paths. Add an assertion that the two member paths do **not** carry `maintainer_access_only`.
  - `test_project_budget_write_routes_deny_pure_auditor` keeps only those four paths, so that `checked_routes == len(write_routes)` still holds. Auditor denial on the two member paths is covered by the AC4 endpoint tests. Note this in the docstring.
- [ ] Update the existing additional-test call (`test_project_budget_router_additional.py:155-160`) for the new signature and predicate:
  - Drop the `_=None` kwarg, because the parameter is removed.
  - Replace `_admin_user()` with a maintainer, or with a Project Admin of the mocked assignment's project. A plain admin is now denied.
  - Keep `actor_id` in `assert_awaited_once_with` consistent with the new user's id.
- [ ] Run the tests and confirm they fail.
- [ ] In both endpoints, remove `_: None = Depends(maintainer_access_only)`. Inside `async with get_async_session() as session:` and before the service call, add `_, assignment, _ = await project_budget_service.get_project_budget(session, budget_id)` followed by `_ensure_project_budget_manage_access(user, assignment.project_name)`. Leave the rest of each body (service call, commit, reload, response) unchanged. Keep the `maintainer_access_only` import, because other routes still use it.
- [ ] Run `pytest tests/codemie/rest_api/routers/test_project_budget_router.py tests/codemie/rest_api/routers/test_project_budget_router_additional.py -v` and confirm it passes.

---

## Post-review changes

Tasks 1 and 2 above shipped as planned. Two later commits changed the design. Where the tasks above disagree with this section, this section and `spec.md` are current.

### Code review fixes (`692485038`)

- `_ensure_project_budget_manage_access` logs `access_denied_project_budget_member_write` with the actor id and project before raising 403.
- All 403s in the router are built by one helper, `_raise_access_denied`, instead of inline `ExtendedHTTPException(...)` calls like the Task 1 snippet.
- `_can_read_project_budget` and `update_project_budget_group` call `user.is_application_admin(project_name)` instead of checking `admin_project_names` inline. This relaxes the Global Constraint against touching them. Behaviour is the same, because `User.admin_project_names` always defaults to an empty list.
- `get_project_budget` is typed to return a non-optional assignment, which relaxes the "do not change the service" constraint. The dead `assignment is None` guards on the read endpoints are removed.
- The shared test users moved to `tests/codemie/rest_api/routers/project_budget_helpers.py`.

### Single-query permission check (`9fe38ed6c`)

- Task 2's pre-check called `get_project_budget`, which loads the budget row, the assignment and every member allocation, and used only the project name. The service then loaded the same data again.
- The pre-check now calls a new read-only service method, `project_budget_service.get_project_budget_project_name(session, budget_id)`. It reads only the active assignment and raises the same 404 when there is none. This is no longer a router-only change.
- New tests:
  - Service: the new method returns the owning project, and raises 404 without an active assignment.
  - Router: each override and clear request loads the full budget once, for the response.
