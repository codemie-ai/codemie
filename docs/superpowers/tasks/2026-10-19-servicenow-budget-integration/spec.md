# ServiceNow Budget Integration — Spec

## Context

EPAM's Support Portal (ServiceNow) is adding the catalog item "Manage EPAM/AI Run CodeMie Monthly Budget for a Project". A requester asks to set, adjust or cancel a project's monthly budget (Platform / CLI / Premium Models). A Delivery Manager or Project Manager approves it in ServiceNow, and ServiceNow then calls CodeMie.

The integration does exactly what a maintainer does in the CodeMie UI's project budget modal, for one project:

- it manages the project's **budget group** (one per project: total amount, split by category percentages);
- it makes sure the project, and optionally its cost center, exists;
- it assigns the requester and approvers as project admins.

ServiceNow sends raw identifiers only: emails, the EPAM project code, and an optional CodeMie project name. CodeMie owns all the resolution logic.

## Decisions

| Topic | Decision |
|---|---|
| Budget model | Budget groups (`ProjectBudgetService` group methods), the same model the UI uses. Never the per-category budget API. |
| Amounts | Absolute, per project, per budget period. Converted to `total_amount` + per-category `pct`. |
| Action flows | Each action has its own handler. Every request **checks first and writes second**: all validation, lookups and amount math run before the first write, so a request that fails a check changes nothing. Flow diagram: `budget-flow.md`. |
| `set` | Upsert, replace. Creates the project (and cost center) and the budget group when missing. The request is the whole budget: categories not sent are removed. `platform` must be > 0 (422). |
| `adjust` | Upsert, merge. Creates the project (and cost center) and the budget group when missing. Merged into the current amounts (zero when there is no budget yet): categories not sent are unchanged; a category sent as `0` is removed. The resulting `platform` must be > 0 (400). |
| `cancel` | Delete only; never creates anything. 404 when the project doesn't exist. Deletes the group with the same service call as the UI's delete (`delete_project_budget_group`); 200 no-op when the project has no budget. `amounts` and `admin_emails` are ignored. |
| Cost center | Only when `epam_project_code != codemie_project_name`: get-or-create the cost center named after the EPAM code and link a **newly created** project to it. An existing project's cost center link is never changed. |
| Deleted names | A name that matches a soft-deleted project or cost center is rejected with 409. |
| Invalid names | Names are lowercased. The project name is validated only when `set`/`adjust` is about to create the project; the cost center code only when a cost center is about to be created (missing project and names differ). Failures return 400. Existing projects are never rejected for their names. |
| Admins | `set` and `adjust` only. Assigned through `ProjectAssignmentService`, committed **before** the budget is applied so equal allocations include them. Unknown and service-account emails are reported in `admins_pending`. |
| Service account | Keycloak client-credentials account, `user_type=service_account`. No admin rights, no project memberships. It can reach only the two integration routes, and only it can reach them. |
| Toggle | Env vars only: `SERVICENOW_INTEGRATION_ENABLED` and `SERVICENOW_SERVICE_ACCOUNT_ID`. |
| Duration | `30d` on group creation, as accepted. Not changed on update. |
| Soft limits | Not sent by ServiceNow. Categories whose amount is unchanged keep their current soft limit; new or changed categories get the existing 80% default. |

## API contract

Both routes live under the prefix `/v1/integrations/servicenow`.

### `GET /v1/integrations/servicenow/projects`

Lists the projects the given people belong to, to populate the catalog item's optional "CodeMie Project Name" field.

```
GET /v1/integrations/servicenow/projects?emails=requester@epam.com&emails=approver@epam.com
```

- `emails`: 1–20 values, validated as emails.
- Returns the deduplicated union of **shared, non-deleted** projects that any active, non-deleted, non-service-account user with one of those emails is a member of, sorted by `project_name`. Service-account emails contribute nothing, mirroring the admin rule.
- Personal projects are excluded. Emails with no matching user contribute nothing and are not an error.

```json
[
  { "project_name": "epm-aimf", "display_name": "AI Multimodal Framework" }
]
```

`display_name` is `null` when the project has none.

### `POST /v1/integrations/servicenow/budgets`

```json
{
  "action": "set",
  "request_id": "RITM0012345",
  "epam_project_code": "nbs-cmpa",
  "codemie_project_name": "epm-aimf",
  "admin_emails": ["requester@epam.com", "approver@epam.com"],
  "amounts": { "platform": 500.0, "cli": 100.0, "premium_models": 50.0 }
}
```

| Field | Rules |
|---|---|
| `action` | `set`, `adjust` or `cancel` |
| `request_id` | Required, 1–64 chars. Logged for correlation with the RITM/TASK; it plays no part in behavior. |
| `epam_project_code` | Required, 3–100 chars (the minimum shared by the project and cost center name rules). Lowercased before use. |
| `codemie_project_name` | Optional, 3–100 chars. Defaults to `epam_project_code`. Lowercased before use. |
| `admin_emails` | 0–20 emails, default `[]`. Lowercased and deduplicated. Used by `set` and `adjust`; ignored by `cancel`. |
| `amounts` | Keys: `platform`, `cli`, `premium_models` (`BudgetCategory`). Finite values ≥ 0; `Infinity`/`NaN` are rejected. No upper cap, matching the existing budget APIs. Required and non-empty for `set` / `adjust`; `set` additionally requires `platform` > 0. Ignored by `cancel`. |

**Response, 200:**

```json
{
  "project_name": "epm-aimf",
  "cost_center": "nbs-cmpa",
  "created_project": true,
  "created_cost_center": false,
  "budget": {
    "total_amount": 650.0,
    "budget_duration": "30d",
    "categories": [
      { "category": "platform", "max_budget": 500.0, "soft_budget": 400.0, "budget_reset_at": "2026-11-18T00:00:00Z" }
    ]
  },
  "admins_assigned": ["approver@epam.com"],
  "admins_pending": ["requester@epam.com"]
}
```

- `cost_center` is the name of the cost center the project is **actually** linked to, or `null`.
- `budget` is the group's state after the change, or `null` after `cancel`.
- `admins_assigned` lists every requested email whose user is a project admin after the call, including those who already were. `admins_pending` lists emails with no active, non-service-account CodeMie user. Both are empty for `cancel`.

**Errors.** All errors use the app's standard error shape.

| Code | When |
|---|---|
| 422 | Request schema or cross-field rules: unknown category, negative or non-finite amount, `amounts` missing or empty for `set`/`adjust`, `set` without `platform` > 0, too many emails, invalid email |
| 400 | Business rules that need current data: a name that must be created is invalid after lowercasing; `adjust` whose merged budget has `platform` 0 |
| 403 | The caller is not the ServiceNow service account; the target project is a personal project; a cost center must be created and the `costCenters` feature is off |
| 404 | `cancel` on a project that doesn't exist. Both routes when the integration is disabled (router not registered). |
| 409 | A name matches a soft-deleted project, or a cost center that must be created matches a soft-deleted one |

All of these are raised before the first write.

## Resolution flow (`POST /budgets`)

Diagram: `budget-flow.md`. `apply_budget_request` only dispatches to one handler per action (`set`, `adjust`, `cancel`) and does the shared logging and response; each handler runs a **check phase** (read-only, raises every 4xx) and then a **write phase**. The write phase runs as two transactions: a sync one for the project, cost center and admins, then an async one for the budget group. Every write is get-or-create or skip-if-done, so a retry with the same input converges on the same state.

| | `set` | `adjust` | `cancel` |
|---|---|---|---|
| **Check** | find project; if missing, validate project name (+ cost center code when names differ); read group; target = request | find project; if missing, validate project name (+ cost center code when names differ); read group; target = current + request; platform > 0 else 400 | find project; missing → 404; read group |
| **Write** | provision project (+ cost center) if missing; assign admins; create or replace group | provision project (+ cost center) if missing; assign admins; create or merge-update group | delete group if present (same call as the UI's delete), else no-op |

### 1. Find project (read-only, all actions)

`application_repository.get_by_name_case_insensitive`, no writes:

| State | Behavior |
|---|---|
| Name matches a soft-deleted project | 409 |
| Exists, personal | 403 |
| Exists, shared | Reused as-is; its cost center link is untouched and no cost center is created. `cost_center` in the response is its current active link. Its name is not re-validated. |
| Missing | `set`/`adjust`: validate the project name, and the cost center code when the names differ (400); `cancel`: 404 `"Project '<name>' does not exist"` |

### 2. Sync transaction: provision and admins (`set` and `adjust` only)

One `get_session()`, committed once at the end.

**Provision** (only when the project is missing):

| Names | Behavior |
|---|---|
| Equal | Create the shared project with no cost center |
| Differ | `CostCenterService.get_or_create` by the EPAM code (409 if it matches a soft-deleted cost center; 403 if `costCenters` is off), then create the project linked to it |

Projects are created through `ProjectService.create_shared_project_in_session` with the service account as actor. It doesn't add a membership and doesn't check the project limit.

**Admins:**

1. Resolve `admin_emails` with `UserRepository.get_active_by_emails`. Users with `user_type == service_account` count as not found.
2. Look up existing memberships for the resolved users. Skip users who are already project admins.
3. Pass the rest to `ProjectAssignmentService.bulk_assign_users_to_project` with `is_project_admin=True`. It adds new members, promotes existing ones, and creates their budget allocations if a group already exists.
4. After the commit, call `invalidate_user_from_cache` for every user whose membership changed.

**Races:** on `IntegrityError` (a concurrent request created the same project or cost center), roll back and run the sync step once more. The second run finds the rows and reuses them.

### 3. Async transaction: budget group

The current group is read in the check phase (`project_budget_group_repository.get_active_by_project` and `get_project_budget_group`) and the target amounts are computed there. The write runs in one async session, committed once.

| Action | No group | Group exists |
|---|---|---|
| `set` | `create_project_budget_group` | `update_project_budget_group` with the request as the whole budget |
| `adjust` | `create_project_budget_group` from the merged amounts (current = 0) | Merge the request into the current amounts, then `update_project_budget_group` |
| `cancel` | No change | `delete_project_budget_group` (the same call the UI's `DELETE /v1/admin/project-budget-groups/{id}` makes) |

**Amounts to a group payload** (pure functions in the service; computed in the check phase):

- The resulting amounts are: `set` → the request (missing categories = 0); `adjust` → current amounts (zero when there is no group) overlaid with the request.
- Validate: `platform` > 0, else 400 (for `set` the model already guarantees it).
- `total_amount = sum(amounts)`. Each non-platform `pct = amount / total × 100`, computed with `Decimal` and not rounded. `platform` gets `100 − sum(other pcts)`, so the percentages sum to 100. The amounts the budget service derives back (`total × pct / 100`) stay within 0.01 of the request at any total.
- The payload **always carries all three categories** plus `total_amount`, because the group update requires percentages summing to 100. Removed categories are sent with `pct = 0`, which the update deletes.
- `soft_budget`: for categories already in the group whose amount is unchanged, send the current `soft_budget`; otherwise leave it out (80% default).
- On create: `name = "ServiceNow <request_id>"` (truncated to 100 chars), `budget_duration = "30d"`, notification fields at their defaults. On update, name, duration, description and notification settings aren't sent and stay unchanged.
- **Race:** a concurrent request can create the group first. When `create_project_budget_group` returns 409 (the other group is committed) or the insert raises `IntegrityError` (the unique active-group index on `project_budget_groups`), roll back the session, re-read the group and update it once, recomputing the amounts from the re-read group with the action's own rule (replace for `set`, merge for `adjust`).
- **Project name:** every budget call uses the project's name **as stored** (`Application.name` from step 2), not the lowercased request value. The budget service matches the name case-sensitively, so a project created before lowercase-only names would otherwise return 404.

### 4. Response

The service returns a `ServiceNowBudgetResult` dataclass: the project, its cost center, the two `created_*` flags, the `ProjectBudgetGroupFullResult` or `None`, and the two admin lists. The router turns it into `ServiceNowBudgetResponse`.

**Partial failure:** check-phase errors never write anything. A failure *during* the write phase (for example the budget provider is unreachable after step 2 committed) leaves the project and admins in place and returns the error; a retry completes the budget.

## Authentication and access

### Configuration

A "ServiceNow Integration" banner block in `config.py`:

```python
SERVICENOW_INTEGRATION_ENABLED: bool = False
SERVICENOW_SERVICE_ACCOUNT_ID: str = ""
```

- When disabled, the router isn't registered (both routes 404) and the scope check isn't applied.
- The integration **requires `ENABLE_USER_MANAGEMENT=true`**: stored users, `user_type` and memberships only exist with it. When the integration is enabled without it, a startup warning is logged and the router isn't registered.
- When enabled with an empty ID, a startup warning is logged and no caller can match, so both routes return 403.
- Containment matters: through these routes the account can create and delete budget groups and change totals, which in the UI needs a maintainer. The scope check in `authenticate()` is what limits it to these two routes.

### Identifying the service account

`is_servicenow_service_account(user)` is true when `config.SERVICENOW_INTEGRATION_ENABLED`, `user.id == config.SERVICENOW_SERVICE_ACCOUNT_ID` (non-empty), and `user.user_type == SERVICE_ACCOUNT_USER_TYPE`.

### Scope restriction

`enforce_servicenow_scope(user, request)` lives in the new module `rest_api/security/servicenow_access.py`. It is called lazily inside `authenticate()`'s external branch, right after the Teams block and before the user is stored on the request.

If the caller is the ServiceNow account and `(request.method, request.scope["route"].path)` isn't in the allowlist, it raises 403 and logs `access_denied_servicenow_scope`. A missing route in the scope counts as not allowed.

The allowlist is built lazily from the integration router's own routes (`{(method, route.path)}`), so paths are defined in one place. Bind-key internal calls never reach this branch.

### Route guard

`servicenow_integration_access_only(request)` sits in `authentication.py` next to the other `*_access_only` guards. It returns 403 with the shared `ACCESS_DENIED_MESSAGE` constants unless `request.state.user` is the ServiceNow account. Regular users, admins and other service accounts can't call the integration routes.

## Components

### New

| File | Contents |
|---|---|
| `src/codemie/rest_api/routers/servicenow_integration.py` | `router = APIRouter(tags=["ServiceNow Integration"], prefix="/v1/integrations/servicenow", dependencies=[])`. Two `async def` handlers with `Depends(authenticate)` and `Depends(servicenow_integration_access_only)`. Validation lives in the models; the handlers delegate to the service and format the response. |
| `src/codemie/rest_api/models/servicenow_integration.py` | `ServiceNowBudgetAction(str, Enum)`, `ServiceNowBudgetRequest` (cross-field rules in `@model_validator(mode="after")`), `ServiceNowBudgetResponse`, `ServiceNowBudgetCategoryResponse`, `ServiceNowBudgetGroupResponse`, `ServiceNowProjectResponse` |
| `src/codemie/service/servicenow_integration_service.py` | `ServiceNowIntegrationService` (classmethods) plus a `servicenow_integration_service` module-level instance. Public: `list_projects(emails)` and `apply_budget_request(actor, request)`. One handler per action (`_handle_set`, `_handle_adjust`, `_handle_cancel`); `set`/`adjust` share a check phase (`_check_upsert`: find project, validate names and cost center only when creating, read group, compute amounts) and a write phase (sync provision + `_assign_admins`, then `_write_group`); `cancel` uses `_find_project` and `_delete_group`. Pure amount functions: `replace_amounts`, `merge_amounts`, `build_category_specs`. Opens both sessions; the sync part runs via `await asyncio.to_thread(...)`. |
| `src/codemie/rest_api/security/servicenow_access.py` | `is_servicenow_service_account`, `enforce_servicenow_scope`, and the lazily built allowlist. Module docstring in the style of `teams_authentication_resolver.py`. |

### Changed

| File | Change |
|---|---|
| `configs/config.py` | The two settings |
| `rest_api/main.py` | `if config.SERVICENOW_INTEGRATION_ENABLED: app.include_router(servicenow_integration.router)` beside the user-management gate. Startup warning when enabled with an empty ID. |
| `rest_api/security/authentication.py` | Lazy call to `enforce_servicenow_scope` after the Teams block; the new `servicenow_integration_access_only` guard |
| `service/project/project_service.py` | New `create_shared_project_in_session(session, *, actor, name, cost_center_id=None) -> Application`: validates the name, checks for duplicates (409, including soft-deleted), creates the project, initializes allowed and default models, writes the PROJECT_CREATED activity event, flushes. No membership, no limit check, no commit. `create_shared_project` is refactored to call it, with unchanged public behavior. |
| `service/cost_center_service.py` | New `get_or_create(session, *, name, created_by) -> tuple[CostCenter, bool]`: checks the feature flag, validates the name, returns the active match, 409 on a soft-deleted match, otherwise creates. No admin check, flush only. `create` reuses the shared creation code. |
| `repository/user_repository.py` | New `get_active_by_emails(session, emails) -> dict[str, UserDB]`: case-insensitive like `get_by_emails`, plus the `is_active` / `deleted_at` filter from `get_active_by_id` |
| `repository/application_repository.py` | New `list_shared_projects_for_users(session, user_ids) -> list[Application]`: `UserProject` joined to `Application`, shared, not deleted, distinct, ordered by name |
| `tests/codemie/rest_api/router_guards/guarded_app.py` | Add `SERVICENOW_INTEGRATION_ENABLED=true` to `PROFILE_ENV` and the docstring's router list |
| `tests/codemie/rest_api/router_guards/baselines/*` | Regenerated with `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`; the diff is reviewed in the MR |

The new methods that skip permission checks say so in their docstrings ("caller must authorize").

### Reused without change

`ProjectBudgetService` group methods and `get_project_budget_group`; `PCT_SUM_TOLERANCE` / `DEFAULT_SOFT_LIMIT_PCT`; `project_budget_group_repository.get_active_by_project`; `ProjectAssignmentService.bulk_assign_users_to_project`; `user_project_repository` membership reads; `BudgetCategory`; `SERVICE_ACCOUNT_USER_TYPE`; `invalidate_user_from_cache`; `ExtendedHTTPException`.

## Conventions

- **Logging:** `event_name: key=value, ..., domain=servicenow_integration`, with `!r` on names and emails.
  - `servicenow_budget_request_started` and `servicenow_budget_request_completed` carry `request_id`, `action`, `project`, `cost_center`, `created_project`, `created_cost_center`, `admins_assigned` / `admins_pending` counts and `actor_user_id`.
  - Failures are logged with `request_id` before re-raising.
  - `access_denied_servicenow_scope: actor_user_id=..., method=..., route=..., domain=servicenow_integration`.
- **Errors:** `ExtendedHTTPException(code, message, details, help)`; repeated messages live in module constants.
- **Audit:** the activity events come from the reused services, with the service account as actor.
- **Imports:** `from codemie.configs import config` and `from codemie.configs.logger import logger`; lazy import of the security module inside `authenticate()`, matching the Teams resolver.

## Testing

Feature-level tests, grouped by boundary, following `.ai-run/guides/testing/`. Boundaries are mocked; assertions check the values that reach them (seam tests).

**`tests/codemie/rest_api/routers/test_servicenow_integration_router.py`**: the API contract through the FastAPI client, with the service mocked.
- Both endpoints: success response shape; every 422 rule; service errors (400/403/404/409) come back in the structured error shape.
- The guard: the ServiceNow account passes; a regular user, an admin, a service account with a different ID, and the right ID with the wrong `user_type` all get 403.

**`tests/codemie/service/test_servicenow_integration_service.py`**: orchestration, with repositories, `ProjectService`, `CostCenterService`, `ProjectAssignmentService` and `ProjectBudgetService` mocked.
- **Resolution:** existing shared project reused, its cost center untouched; personal project 403; soft-deleted project 409; missing with equal names; missing with different names, cost center created or reused; soft-deleted cost center 409; invalid names 400 after lowercasing; an `IntegrityError` retry that reuses the row.
- **Admins:** unknown email and service-account email go to pending; already admin is skipped; member promoted; non-member added; emails deduplicated and lowercased; the bulk assignment is committed before any budget call; the cache is invalidated for changed users only.
- **Budget:**
  - the exact `total_amount`, percentages and `soft_budget` passed for `set` on a new project, `set` replacing a group (categories removed with pct 0), and `adjust` merge (including removal with 0);
  - unchanged categories keep their soft limit;
  - platform 0 after an adjust merge is 400; `adjust` without a group or project creates them; `cancel` with and without a group, and on a missing project (404); failed checks write nothing;
  - a 409 create race falls back to update;
  - amounts with awkward ratios and large totals (e.g. 1,000,000) convert to percentages within the existing tolerance of 100 and derive back within 0.01.
- **`list_projects`:** union and deduplication across emails, delegation to the new repository query.

**`tests/codemie/rest_api/security/test_servicenow_access.py`**, plus cases added to the existing `test_authentication.py`:
- `enforce_servicenow_scope`: allowed route and method; same path with another method; other route; missing route; integration disabled; ID mismatch; wrong `user_type`; empty configured ID.
- `authenticate()` seam: the ServiceNow account on a blocked route gets 403; on an allowed route it passes; other users are unaffected.

**Existing test files:**
- `project_service`: `create_shared_project_in_session` doesn't add the creator, skips the limit, returns 409 on a soft-deleted name, initializes models, and writes the activity event. The existing `create_shared_project` tests pass unchanged.
- `cost_center_service`: `get_or_create` returns the existing row, creates a new one, returns 409 on a soft-deleted match, and checks the feature flag. The existing `create` tests pass unchanged.
- Repository tests for `get_active_by_emails` and `list_shared_projects_for_users`, in the existing repository test files.

## Setup notes (DevOps / ServiceNow)

1. In Keycloak, create a confidential client for ServiceNow with service accounts enabled. Copy the Teams bot client's settings and mappers so the token carries the claims CodeMie's Keycloak provider reads (`sub`, `preferred_username`, `name`, `email`, `realm_access`).
2. Add the attribute `user_type=service_account` to that client's service-account user and map it into the access token. It must be in place **before** the first call: `user_type` is only stored on first sign-in.
3. Read the service-account user's ID in the Keycloak admin console. That ID is the token's `sub`, which CodeMie stores as the user ID. Set it as `SERVICENOW_SERVICE_ACCOUNT_ID`.
4. Make sure `ENABLE_USER_MANAGEMENT=true`, set `SERVICENOW_INTEGRATION_ENABLED=true`, and redeploy.
5. In ServiceNow, use an OAuth profile with the client-credentials grant against `https://<keycloak-host>/realms/<realm>/protocol/openid-connect/token`, and send the token as `Authorization: Bearer <token>`.

## Out of scope

- Creating CodeMie users from an email. Unknown approvers are reported in `admins_pending`.
- Standalone cost-center management endpoints.
- Changing an existing project's cost center.
- Removing admins or members that ServiceNow added earlier.
- Changing budget duration, soft limits or notification settings from ServiceNow.
- Signature verification beyond the gateway's token validation, and any change to the Teams bot integration.

## Known limitations

- Concurrent `adjust` requests on the same project can lose an update: current amounts are read in the check phase and written after the sync transaction, without row locking (the UI path doesn't lock either).
- A project found in the check phase isn't re-checked for deletion inside the write transaction; a project soft-deleted in that millisecond window still gets admins and a budget.
- `cancel` discards well-formed `amounts`/`admin_emails`; malformed values still fail field validation with 422.
- The budget service syncs to the provider (LiteLLM) before the transaction commits. If a concurrent group creation is rolled back (see the race in step 3), provider budgets created by the losing request can be left behind. The UI path has the same behavior; fixing it is out of scope.
- A 403 from the scope check is logged twice: once by the check, and once by `authenticate()`'s generic "Authentication failed" handler. That matches how every other auth error is logged today.
- EPAM codes that don't fit the cost center pattern (one hyphen, lowercase letters and digits) are rejected with 400 when a cost center has to be created (missing project and names differ).

## Open items for the ServiceNow team

- Whether `admin_emails` should carry the requester, the approvers, or both.
- How requester and approver emails are taken from ServiceNow's session and approval data.
