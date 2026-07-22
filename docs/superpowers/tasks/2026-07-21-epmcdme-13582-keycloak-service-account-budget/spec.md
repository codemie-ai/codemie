# EPMCDME-13582: Budget record not created for service account on first Keycloak login

## Problem

When a service account authenticates through Keycloak for the first time,
`create_user_from_idp` adds the user to each IDP-provisioned project but never
calls `_sync_project_budget_member_added`. As a result, the user has no
`ProjectMemberBudgetAssignment` records and is excluded from budget enforcement
until a human manually removes and re-adds them via the UI.

## Scope

Backend only. No database migration required — `_sync_project_budget_member_added`
already contains a skip-if-exists guard, so the call is idempotent and safe for
accounts that already have budget records.

## Design

### New helper: `AuthenticationService._sync_budget_for_idp_projects`

A new `async` static method on `AuthenticationService` that mirrors the existing
`_ensure_projects_exist` pattern:

```python
@staticmethod
async def _sync_budget_for_idp_projects(user_id: str, project_names: list[str]) -> None:
```

For each project name in `project_names`:
1. Open an isolated synchronous session with `with get_session() as session:`.
2. Call `ProjectAssignmentService._sync_project_budget_member_added(session, project_name, user_id, actor_id=None)`.
3. Commit the session.
4. Catch any `Exception`, log a warning, and continue (non-fatal — same policy as `_ensure_projects_exist`).

`actor_id=None` is used because there is no human actor in the Keycloak bootstrap
flow. The `assigned_by` column on `ProjectMemberBudgetAssignment` is `Optional[str]`
and accepts `NULL`.

### Call site in `create_user_from_idp`

After the existing `for project_name in idp_user.project_names:` loop (line 227),
before the KB bootstrap loop, wrapped in a top-level `try/except` so any failure
outside the helper's internal per-project loop (e.g. import error, unexpected
exception before iteration begins) also cannot break login:

```python
try:
    await AuthenticationService._sync_budget_for_idp_projects(db_user.id, idp_user.project_names)
except Exception as e:
    logger.warning(f"budget_sync_failed_on_idp_bootstrap: user_id={db_user.id!r} error={e}")
```

### Minor type-hint fix in `ProjectAssignmentService`

Update `_sync_project_budget_member_added`'s signature:

```python
actor_id: str  →  actor_id: Optional[str]
```

The `assigned_by` DB column is already nullable; this aligns the Python type with
the schema.

## Error handling

Each project is wrapped in a `try/except Exception`. A failure for one project
logs a warning and continues to the next; the outer async session and the login
flow are unaffected. The implementation follows the exact pattern used in
`_ensure_projects_exist`.

## Files changed

| File | Change |
|---|---|
| `src/codemie/service/user/authentication_service.py` | Add `_sync_budget_for_idp_projects`; call it from `create_user_from_idp` |
| `src/codemie/service/project/project_assignment_service.py` | `actor_id: str` → `Optional[str]` in `_sync_project_budget_member_added` |
| `tests/codemie/service/user/test_authentication_service.py` | Update success test to assert helper is called; add non-fatal failure test |

## Testing

- `test_create_user_from_idp_success`: patch `_sync_budget_for_idp_projects` and assert it is called once with the correct `user_id` and `project_names`.
- `test_create_user_from_idp_budget_sync_failure_is_non_fatal`: patch `_sync_budget_for_idp_projects` to raise an exception at the outer level (the mock raises, not the internal loop); assert `create_user_from_idp` completes normally and the exception is not propagated. This validates the call-site `try/except`.

No integration test is needed — the idempotent skip-if-exists guard and the
isolated session pattern are already covered by `test_project_assignment_service.py`.
