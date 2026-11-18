# EPMCDME-17380 Auditor read access to cost centers - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auditor-only users can read cost centers (list, detail, budget-rollup); every write stays admin/maintainer-only.

**Architecture:** Two authorization layers change together. Router: the three GET routes swap `admin_access_only` for the existing `admin_or_maintainer_or_auditor_access` (budget router EPMCDME-10930 convention). Service: `CostCenterService` gets a read check that admits auditors, and `update`/`delete` stop borrowing their admin check from `get_or_404`.

**Tech Stack:** Python, FastAPI, SQLModel, pytest + `unittest.mock`.

**Requirements:** inline (Jira EPMCDME-17380). Research: `technical-analysis.md` in this folder.

Commit per task using the repository's existing convention.

## Acceptance criteria

1. An auditor-only user gets 200 from GET `/v1/admin/cost-centers`, GET `/{costCenterId}` and GET
   `/{costCenterId}/budget-rollup`, with no "Access denied".
2. An auditor-only user gets 403 from POST, PATCH `/{costCenterId}` and DELETE `/{costCenterId}`, and nothing is
   created, changed or deleted.
3. Calling `CostCenterService.update` or `delete` directly as an auditor raises 403 before any repository write.
4. Administrator and maintainer users keep their current results on all six routes.
5. A user with none of administrator, maintainer or auditor gets 403 "Access denied" on all six routes.
6. The feature gate (403 "Feature not available") and `ENABLE_USER_MANAGEMENT` checks keep their current order and
   responses. `get_or_create` and `ensure_exists_for_project` stay free of a role check.
7. Tests cover service-level authorization per role, each route's guard by dependency name, and HTTP-level per-role
   calls against all six routes.

## Global Constraints

- Reuse `admin_or_maintainer_or_auditor_access` (`src/codemie/rest_api/security/authentication.py:248`). No new router guard.
- Write routes keep `admin_access_only`; write service methods keep an admin/maintainer check.
- Do not change the shared guard. Its denial log says `domain=analytics`; this is a known minor issue, left as is.
- Auditor checks use `getattr(user, "is_auditor", False)`, the existing inline pattern.

## Review Focus

- An auditor calling PATCH/DELETE directly through the service (not only the router) must get 403. Task 1 tests this.
- A plain-user `MagicMock` has a truthy `is_auditor` by default. Tests must set `is_auditor = False` explicitly, or the denial tests pass for the wrong reason.
- Order of checks: the feature gate runs before the role check, as today, so the 403 "Feature not available" response is unchanged.
- `get_or_create` / `ensure_exists_for_project` stay unauthenticated (their callers authorize). `test_does_not_require_admin` (`test_cost_center_service.py:289`) must still pass.

---

### Task 1: Service - separate read from write authorization

**Files:**
- Modify: `src/codemie/service/cost_center_service.py:56-59, 121-170`
- Test: `tests/codemie/service/test_cost_center_service.py`

**Interfaces:**
- Produces: `CostCenterService.ensure_read_access(user: User) -> None`: raises `ExtendedHTTPException(code=403, message="Access denied")` unless `user.is_admin_or_maintainer or getattr(user, "is_auditor", False)`.
- Produces: `CostCenterService._find_active_or_404(session, cost_center_id: UUID) -> CostCenter`: runs `ensure_feature_enabled()` and the active lookup, raises 404 "Cost center not found". No role check.
- `get_or_404` (read): `ensure_feature_enabled()`, then `ensure_read_access(user)`, then lookup via `_find_active_or_404`. Same signature as today.
- `list_paginated`: `ensure_admin` replaced by `ensure_read_access`.
- `update` / `delete`: call `ensure_feature_enabled()` then `ensure_admin(user)`, then `_find_active_or_404`. They no longer call `get_or_404`.
- `ensure_admin` and `create` stay unchanged.

Test-first: yes. Auditor `list_paginated` / `get_or_404` currently raise 403. Add a test asserting auditor `update` / `delete` raise 403 even though `get_or_404` now admits auditors.

- [ ] **Step 1: Write failing tests.** Add `_auditor_user()` (MagicMock: `is_admin_or_maintainer=False`, `is_auditor=True`) and `_plain_user()` (both False). Patch the feature gate and `cost_center_repository` the way the existing feature-gate tests do. Tests:
  - `test_auditor_can_list`, `test_auditor_can_get`: no raise, repository called.
  - `test_plain_user_cannot_list`, `test_plain_user_cannot_get`: 403.
  - `test_auditor_cannot_create`, `test_auditor_cannot_update`, `test_auditor_cannot_delete`: 403, and repository `create`/`update` not called and `session.flush` not called.
  - `test_admin_can_update_and_delete`: unchanged admin path still works.
- [ ] **Step 2:** `poetry run pytest tests/codemie/service/test_cost_center_service.py -v`. Expected: the auditor-allowed tests FAIL with 403.
- [ ] **Step 3:** Implement the interfaces above.
- [ ] **Step 4:** Re-run the Step 2 command. Expected: all PASS, including `test_does_not_require_admin`.

### Task 2: Router - widen the three GET routes

**Files:**
- Modify: `src/codemie/rest_api/routers/cost_centers.py:35` (import), `:99`, `:120`, `:152`
- Test: `tests/codemie/rest_api/routers/test_cost_centers_router.py`

**Interfaces:**
- Consumes: Task 1 `get_or_404` / `list_paginated` (signatures unchanged; the router call sites at :103, :124, :158 stay as they are).

Test-first: yes. A dependency-name test asserting the GET routes use `admin_or_maintainer_or_auditor_access` fails today.

- [ ] **Step 1: Write failing tests** (copy the shape of `test_budget_router.py:500-530`: build a `(path, methods)` route map from `router.routes`, then check `dependency.call.__name__` on `route.dependant.dependencies`):
  - `test_cost_center_read_routes_admit_auditor`: for `GET /v1/admin/cost-centers`, `GET /v1/admin/cost-centers/{costCenterId}`, and `GET /v1/admin/cost-centers/{costCenterId}/budget-rollup`, assert `"admin_or_maintainer_or_auditor_access" in calls` and `"admin_access_only" not in calls`.
  - `test_cost_center_write_routes_stay_admin_only`: for POST `""`, PATCH `/{costCenterId}` and DELETE `/{costCenterId}`, assert `"admin_access_only" in calls` and `"admin_or_maintainer_or_auditor_access" not in calls`.
- [ ] **Step 2:** `poetry run pytest tests/codemie/rest_api/routers/test_cost_centers_router.py -v`. Expected: the read test FAILS.
- [ ] **Step 3:** Import `admin_or_maintainer_or_auditor_access` and swap it into the three GET routes' `Depends(...)`. The guard signature is `(request)`, the same as `admin_access_only`, so the OpenAPI baseline (`tests/codemie/rest_api/router_guards/baselines/openapi.json`) should not change. If a router-guard test fails, re-check this rather than regenerating the baseline.
- [ ] **Step 4:** Re-run the Step 2 command and `poetry run pytest tests/codemie/rest_api/router_guards -v`. Expected: all PASS.

### Task 3: HTTP-level per-role tests (done, commit f0bb5badf7)

- [x] Add `tests/codemie/rest_api/routers/test_cost_centers_router_http_auth.py`: TestClient calls per role (auditor,
  maintainer, admin, plain user) against all six routes with the status codes above.

### Task 4: Architecture review follow-ups (AR-2..AR-4)

- [x] Add `is_admin_maintainer_or_auditor` to `permissions.py` and use it in `ensure_read_access`; rename
  `get_or_404` to `get_for_read_or_404`; drop the duplicate feature check from `_find_active_or_404` and document its
  caller contract. Tests: `tests/codemie/rest_api/security/test_permissions.py` plus renamed service/router tests.

---

## Notes (not tasks)

- **UI (codemie-ui, same ticket, branch EPMCDME-17380_auditor-cost-centers-read):** the sidebar `tabs.tsx` must show the Cost centers tab to auditors, and Create/Edit/Delete must be hidden for non-admin/maintainer users. See spec.md, UI section.
- **Known issue:** `admin_or_maintainer_or_auditor_access` logs denials with `domain=analytics`, so cost-center denials will be logged under that domain. Left unchanged on purpose because the guard is shared.
- **Local manual check:** with `ENV=local`, `resolve_is_admin` forces admin, which hides auditor behavior (analysis section 5).
- Spec 10930 put cost centers out of auditor scope. This ticket reverses that decision for reads only.
