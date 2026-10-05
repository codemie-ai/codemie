# Spec: Project Admins can override personal spend (EPMCDME-15234, backend)

## Goal

Let Project Admins override and clear a member's personal spend allocation on budgets of projects they administer. Maintainers keep this ability. Everyone else stays denied.

## Who passes (precise)

Endpoints:
- `PATCH /v1/admin/project-budgets/{budget_id}/members/{user_id}`: `override_member_allocation`, `src/codemie/rest_api/routers/project_budget_router.py:463-486`
- `DELETE /v1/admin/project-budgets/{budget_id}/members/{user_id}/override`: `clear_member_override`, `project_budget_router.py:490-509`

A caller passes when **either** of these holds:
1. `user.is_maintainer`, or
2. `user.is_application_admin(<budget's project_name>)`.

**Why plain Admins are excluded.** In `src/codemie/rest_api/security/user.py:58-76`, `is_maintainer` forces `is_admin=True`, but `is_admin` does **not** imply `is_maintainer`. A plain Admin (`is_admin=True`, `is_maintainer=False`) is therefore denied today by `maintainer_access_only`. They stay denied unless they are Project Admin of the target project. This was decided as "Maintainer and Project Admin only". `ENV=local` makes every user `is_admin` but not `is_maintainer`, so local testing still shows the role difference on these endpoints. Auditors are denied unless they are also Project Admin of the target project.

## Write-access helper

Add a small router-local helper pair to `project_budget_router.py`, next to `_can_read_project_budget` (L239-242). It follows §1.1 of `docs/EPMCDME-15234-project-admin-spend-override.md`:
- a predicate that is true when `user.is_maintainer or user.is_application_admin(project_name)`;
- an ensure-function that raises `ExtendedHTTPException(code=403, message="Access denied", details=<names the project>)` when the predicate is false. Before raising, it logs a warning `access_denied_project_budget_members_manage` with the actor id, the project and the budget id.

Only `override_member_allocation` and `clear_member_override` use the write-access helper. Do not reuse `_can_read_project_budget` for this check, because it grants auditors.

## Router cleanup in scope

The ticket lists "role-based access control for spend management" as an affected area, so the router's existing access checks get two behaviour-neutral cleanups alongside the new helper:
- A shared `_raise_access_denied(details)` builds every 403 in the router: the new ensure-function, `_ensure_project_budget_read_access`, `list_project_budgets` and `update_project_budget_group`. Messages, details and help text are unchanged.
- `_can_read_project_budget` and `update_project_budget_group` call `user.is_application_admin(project_name)` instead of checking `admin_project_names` inline. Both are the same membership test (`src/codemie/rest_api/security/user.py:100-101`).

Neither change alters which roles pass any check.

## Behaviour

- Remove `Depends(maintainer_access_only)` from both endpoints.
- Before calling the service, each endpoint resolves the budget's project through `project_budget_service.get_project_budget_project_name(session, budget_id)`, then calls the ensure-function.
  - The check must run first because `clear_member_override` mutates before it resolves the project (`src/codemie/service/budget/project_budget_service.py:1502-1520`).
  - The lookup reads only the active project budget assignment. It does not load the budget row or the member allocations, which the service loads again anyway. An active assignment always points to an existing project budget: every place that creates one creates a `PROJECT` budget in the same transaction, and deletion soft-deletes both together.
  - The lookup raises 404 (`Project budget not found: {budget_id}`) when there is no active assignment, so the router has no "assignment is None" branch.
- Unknown `budget_id`, or a budget that is not a project budget: 404 with the same message as `get_project_budget`, as today. A non-project budget has no assignment, so it takes the same 404 path.
- Known budget and the caller fails the check: 403. It is not converted to 404.
- Self-override is allowed, with no actor-vs-target check. The existing `MEMBER_ALLOCATION_OVERRIDDEN` activity event records the actor.
- Existing service behaviour, repository, request and response schemas, and provider sync are unchanged. There are two service edits:
  - The new read-only `get_project_budget_project_name` described above.
  - A typing fix: `get_project_budget` is annotated to return a non-optional assignment, which matches what it already guarantees. The router's dead `assignment is None` guards on the read endpoints are removed for the same reason.

## Acceptance criteria

1. A Project Admin of project P can override and clear a member allocation on a budget of P. The response is 200 with the refreshed budget payload.
2. A Project Admin of P gets 403 for a budget of a different project Q.
3. A Maintainer can still override and clear on any project's budget.
4. A plain Admin (not Maintainer, not Project Admin of P), an auditor and a regular user get 403.
5. A non-existent `budget_id` still returns 404.
6. A Project Admin can override their own allocation.
7. For a denied caller, `clear_member_override` performs no mutation: the service is not called.
8. The existing route-dependency tests (`tests/codemie/rest_api/routers/test_project_budget_router.py:357-437`) are updated so the override and clear paths are no longer required to carry `maintainer_access_only`. All other write routes on those paths still carry it, and pure auditors are still denied on all write routes.
9. Spend read paths behave as before: project budget list, get and members, `/v1/analytics/project-member-spending`, and project spends.

## Non-goals

- No change to which roles may create, update, delete, reset or rebalance project budgets, or create, update, delete, reset or rebalance budget groups. The router cleanup above refactors those checks without changing their outcome.
- Override and clear are not widened to plain Admins (`is_admin_or_maintainer`).
- No 403-to-404 conversion for foreign-project budgets. The read endpoints already answer 403 for a foreign budget and 404 for an unknown one, so the same split on override and clear exposes nothing new.
- No self-override restriction.
- No validation that the target `user_id` belongs to the project, beyond the existing 404 for a missing allocation.
- No changes to existing service behaviour, and no repository, schema or migration changes.
- No frontend changes in this repo's scope (see Dependency).

## Dependency

The UI lives in the sibling repo `codemie-ui`. In `src/pages/settings/administration/ProjectDetailsPage.tsx`, `budgets` and `onBudgetsChanged` are gated on `isMaintainer` (L298, L312, L313). That gate must become `isMaintainer || isProjectAdmin` before Project Admins see the override action. This is tracked separately. The backend can ship first: until the UI changes, Project Admins simply won't see the action.

## Testing

- **Endpoint tests:** direct endpoint tests for AC 1-7, following the existing patterns and user fixtures in `test_project_budget_router.py`. The 404 tests stub only the assignment repository, so the real `get_project_budget_project_name` runs. 403 tests assert the exact `details` text.
- **Predicate test:** a unit test of the write-access predicate covering maintainer, Project Admin of the project, Project Admin of another project, plain Admin and auditor.
- **Route-dependency tests:** updated for AC 8. Each test asserts that every expected route path was matched, so it cannot pass vacuously.
- **Service tests:** `get_project_budget_project_name` returns the owning project, and raises 404 when there is no active assignment.
- **Load-count test:** each override and clear request loads the full budget only once, for the response, so a full reload in the permission check fails a test.
