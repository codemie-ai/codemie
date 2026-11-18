# Technical Research

**Task**: cost-centers auditor role authorization
**Generated**: 2026-11-18
**Research path**: filesystem

---

## 1. Original Context

Summary: Auditor-only user has no read access to cost centers

Description: A user who holds only the CodeMie auditor role cannot read cost centers. Opening Settings > Administration > Cost centers shows an "Access denied" error: "This action requires administrator or maintainer privileges." The auditor role is a read-only platform role, so it should be able to view cost centers.

Preconditions: The user is signed in with the auditor role only (no administrator or maintainer role). The user has access to the Settings > Administration section.

Steps: Sign in as auditor-only user; open Settings > Administration > Cost centers (/settings/administration/cost-centers); observe the error and the failed request.

Expected: The auditor-only user can open the Cost centers page and view cost centers read-only. Create, edit, and delete actions are not available.

Actual: "Access denied" modal; cost-centers request returns {"error":{"message":"Access denied","details":"This action requires administrator or maintainer privileges.","help":"..."}}.

Affected Areas: Cost centers page and route; Cost centers API read endpoint and its authorization check; Role-based access for the auditor role.

Acceptance Criteria:
1. A user with only the auditor role can open Settings > Administration > Cost centers without an "Access denied" error.
2. The auditor-only user can view the cost center list and details.
3. Create, edit, and delete actions for cost centers are not available to the auditor-only user, and the API refuses any write request from that user.
4. Administrator and maintainer access to cost centers is unchanged.
5. A user with neither the auditor, administrator, nor maintainer role still gets "Access denied".

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A - decide who may read cost centers.** Two layers, both admin/maintainer only today:
- Router `src/codemie/rest_api/routers/cost_centers.py` (prefix `/v1/admin/cost-centers`). Every route takes `Depends(authenticate)` + `Depends(admin_access_only)`:
  - Reads: `GET ""` list (:93/:99), `GET /{costCenterId}` detail incl. linked projects + member counts (:116/:120), `GET /{costCenterId}/budget-rollup` (:148/:152).
  - Writes: `POST ""` (:74/:78), `PATCH /{costCenterId}` (:169/:174), `DELETE /{costCenterId}` (:189/:193).
  - Each also calls `_ensure_user_management_enabled()` (400 when `ENABLE_USER_MANAGEMENT` is false).
- `admin_access_only` - `src/codemie/rest_api/security/authentication.py:187`: 403 unless `user.is_admin_or_maintainer`; details `_ADMIN_OR_MAINTAINER_REQUIRED` = "This action requires administrator or maintainer privileges." (:43). This is the exact text in the bug report, so the router dependency is what currently rejects the auditor.
- Service `CostCenterService.ensure_admin` - `src/codemie/service/cost_center_service.py:57`: `if not user.is_admin_or_maintainer: raise 403 "Access denied"` (no details). Called by `create` (:74), `list_paginated` (:124), `get_or_404` (:130). `update` (:138) and `delete` (:159) get their admin check only by calling `get_or_404`. The router's `budget-rollup` also resolves the name through `get_or_404` (cost_centers.py:158).
- Feature gate `ensure_feature_enabled` (:62): 403 "Feature not available" unless `features:costCenters` is on (`config/customer/customer-config.yaml:183`, enabled: true). It is not role-based.
- `get_cost_center_budget_summary` (`src/codemie/service/budget/cost_center_budget_summary.py:253`) checks only `ensure_budget_management_enabled()` (:263); it has no role check.

**Purpose B - admit auditors to read-only admin data.** The guard already exists:
- `admin_or_maintainer_or_auditor_access` - `authentication.py:248`: passes when `is_admin_or_maintainer or is_auditor`; otherwise 403 with details "This action requires administrator, maintainer, or auditor privileges."; logs `access_denied_auditor ... domain=analytics` (the domain is hard-coded).
- Consumers: `routers/budget_router.py:87,144` (budget list/detail; writes use `maintainer_access_only`), `routers/admin.py:112`, `routers/user_management_router.py:338,444,525`, `routers/analytics/leaderboard.py` (many), `routers/analytics/usage_attribution.py:90`.
- Inline service/router checks that use the same rule: `getattr(user, "is_auditor", False)` in `project_budget_router.py:181,271`, `projects/crud.py:289,351`, `project_spending_service.py:258,482`, `user_management_service.py:704`.
- Role model: `User.is_auditor` (`rest_api/security/user.py:152`), a boolean flag next to `is_admin`/`is_maintainer`; `is_admin_or_maintainer` (user.py:195 -> `security/permissions.py:20`) does not include auditors.

Other touched code: `CostCenterService.get_or_create` (:88) and `ensure_exists_for_project` (:148) skip `ensure_admin` (callers authorize). Callers: `provisioning_service.py:110-152` and `project_service.py:237,564`.

### Decisions Already in the Code
- EPMCDME-10930 (auditor role) excluded cost centers on purpose. `docs/superpowers/tasks/2026-08-12-epmcdme-10930-add-auditor-role/spec.md:21` says: "**Out of scope**: Activity Events page ..., Teams Bot Integration, Cost Centers, enterprise admin items". It records no reason. The same spec (:11-17) gives auditors read-only Analytics, Projects, Users, Budgets: "Auditor alone cannot perform any write action."
- Convention for widening reads to auditors: swap the read routes' dependency to `admin_or_maintainer_or_auditor_access` and leave write routes on their stricter guard (budget_router.py:87/144 vs maintainer-only writes). Tests assert this by dependency name (`test_budget_router.py:500-530`).
- Cost centers enforce authorization twice (router dependency plus service `ensure_admin`). Budgets enforce it only in the router. In the cost center service, the write paths use the read helper's check through `get_or_404`.

### Task Statements Checked Against the Code
- "cost-centers request returns ... 'This action requires administrator or maintainer privileges.'" - **holds**: `admin_access_only` details text (authentication.py:43,187).
- "Cost centers API read endpoint and its authorization check" (singular) - **contradicted in count**: three read endpoints (list, detail, budget-rollup) plus a service-level check on each (cost_center_service.py:124,130).
- "The auditor role is a read-only platform role" - **holds** for the scope in spec 10930. Cost centers were explicitly left out (spec.md:21).
- AC3 "API refuses any write request" - **holds today**: router `admin_access_only` on POST/PATCH/DELETE. The service `ensure_admin` reached via `create` and `get_or_404` is a second layer.
- AC5 neither role -> "Access denied" - **holds** for both guards: each raises 403 with message "Access denied". The `details` text differs between the two guards.
- Page and route (/settings/administration/cost-centers) - **unverified**: UI repo is outside this repository.

### Consumers of Changed Contracts
- `CostCenterService.ensure_admin` / `get_or_404`: router list/detail/budget-rollup/update/delete (cost_centers.py:103,124,158,178,197). The service writes `update` and `delete` depend on `get_or_404` for their admin check. No other callers in `src/` (searched `cost_center_service.`, `CostCenterService.`, `ensure_admin(`).
- `admin_or_maintainer_or_auditor_access`: consumers listed under Purpose B. Its signature `(request)` matches `admin_access_only`, so swapping it adds no OpenAPI parameter. Baseline: `tests/codemie/rest_api/router_guards/baselines/openapi.json:40788-41104`.
- Test `test_cost_center_service.py:289` `test_does_not_require_admin` patches `CostCenterService.ensure_admin` by name.

### Architecture and Layers Affected
- API: `routers/cost_centers.py` dependencies.
- Security: `security/authentication.py` guards (reuse; no change found necessary to locate).
- Service: `service/cost_center_service.py` `ensure_admin` / read methods.

### Integration Points
- `application_repository` (project counts, linked projects, member counts) on detail/list. The budget-rollup reads the spend ledger via `get_cost_center_budget_summary`. Neither has per-user scoping.
- `customer_config` feature `features:costCenters`; `budgetManagement` gate on rollup.

### Patterns and Conventions
- FastAPI `Depends(authenticate)` + a role dependency that reads `request.state.user`. Existing auditor checks use `getattr(user, "is_auditor", False)`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` present. No guide covers auditor or cost-center authorization. `api/provisioning-api.md` mentions cost centers only for provisioning. `development/security-patterns.md` has no auditor mention.

### Architectural Decisions
- Spec 10930 (`docs/superpowers/tasks/2026-08-12-epmcdme-10930-add-auditor-role/spec.md:9-21, 337-345`): auditor is a boolean flag, read-only, cost centers out of scope.

### Derived Conventions
- Read routes use `admin_or_maintainer_or_auditor_access`. Write routes keep admin/maintainer guards. Tests check dependency names on `APIRoute.dependant.dependencies`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_cost_centers_router.py`: create/list/detail/budget-rollup tested by calling handlers directly with `_=None` and a super-admin `User`. Dependencies are never exercised, and there are no role tests.
- `tests/codemie/service/test_cost_center_service.py`: validation, logging, feature-gate 403s, `get_or_create`. Its mock user sets `is_admin_or_maintainer = True` (:28). No denial test for a non-admin or an auditor.
- Guard tests: `tests/codemie/rest_api/security/test_authentication_auditor.py` (admin/maintainer/auditor/none for `admin_or_maintainer_or_auditor_access`).
- Patterns to copy: `test_budget_router.py:500-530` (read routes use the auditor guard, write routes keep the strict guard) and `:852` `_override_identity` (TestClient with `authenticate` override). `test_projects_router_auditor.py:41` `_make_auditor()` patches `config.ENV`/`ENABLE_USER_MANAGEMENT` so `resolve_is_admin` does not force admin.

### Testing Framework and Patterns
- pytest, `unittest.mock.patch` on module-level symbols, `asyncio.run` for async handlers, `SimpleNamespace` fixtures.

### Coverage Gaps
- No test of which guard each cost-center route uses, no auditor or regular-user denial test, and no service `ensure_admin` denial test.

---

## 5. Configuration and Environment

### Environment Variables
- `ENABLE_USER_MANAGEMENT` (`configs/config.py:308`): every cost-center route needs it. With `ENV=local`, `User.resolve_is_admin` sets `is_admin=True` (user.py), so a local manual check hides auditor behaviour.

### Configuration Files
- `config/customer/customer-config.yaml:183` `features:costCenters` (enabled). The budget-rollup also needs `features:budgetManagement`.

### Feature Flags and Deployment Concerns
- None role-specific. No migrations: `is_auditor` already exists (`alembic/versions/u2v3w4x5y6z7_add_is_auditor_to_users.py`).

---

## 6. Risk Indicators

- Two authorization layers. Changing only the router dependency still gives the auditor 403 "Access denied" (no details) from `CostCenterService.ensure_admin` via `list_paginated`/`get_or_404` (cost_center_service.py:124,130).
- `update`/`delete` take their service-level admin check only from `get_or_404` (:138,:159). Speculative: widening `get_or_404` for reads would also relax the service-level check on writes, leaving only the router dependency to refuse them.
- The detail page probably also loads `GET /{id}/budget-rollup` (same `admin_access_only`). If it stays admin-only, the auditor would get a partial page or an error. The UI repo was not inspected.
- The spec 10930 non-goal (spec.md:21) puts cost centers out of auditor scope. This ticket reverses it, and no recorded reason was found.
- Router tests bypass dependencies (`_=None`), so a guard regression on write routes (AC3/AC4) would go unnoticed without new tests.
- `admin_or_maintainer_or_auditor_access` logs `domain=analytics` for every denial, so cost-center denials would be logged under the wrong domain.
- UI side (separate repo): the sidebar/route gate for Cost centers and the create/edit/delete controls need auditor handling. Spec 10930 relied on write controls being `isAdmin`-gated.

---

## 7. Summary for Complexity Assessment

The change is confined to one router (`routers/cost_centers.py`, 3 read routes of 6) and one service (`service/cost_center_service.py`, `ensure_admin` and the read methods `list_paginated`/`get_or_404`). The reusable guard `admin_or_maintainer_or_auditor_access` (authentication.py:248) already exists and admits auditors elsewhere: budgets, users, admin and analytics. No schema, migration, config or new pattern is involved.

There is little novelty: budget_router followed the same read-widening convention for EPMCDME-10930, and that is the template. The only design point is the double check. The service's `ensure_admin` sits behind `get_or_404`, which `update` and `delete` share, so separating read authorization from write authorization in the service is where care is needed.

Test coverage is weak for authorization: cost-center router tests call handlers directly with the dependency stubbed, and the service tests have no role-denial cases. The budget router tests give a ready pattern for dependency-name and TestClient identity-override tests. UI changes live in a separate repo.

---

## 8. External References

None named by the task.
