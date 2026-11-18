# EPMCDME-17380 - Auditor read access to cost centers (spec)

## Intent

A user with only the CodeMie auditor role gets "Access denied" ("This action requires administrator or maintainer
privileges.") on Settings > Administration > Cost centers. Auditor is a read-only platform role, so it must be able to
view cost centers but never change them. Spec EPMCDME-10930 left cost centers out of auditor scope
(`docs/superpowers/tasks/2026-08-12-epmcdme-10930-add-auditor-role/spec.md:21`). This ticket brings them in, for
reads only.

## Current behavior (from technical-analysis.md)

- Router `src/codemie/rest_api/routers/cost_centers.py` (prefix `/v1/admin/cost-centers`) guards all six routes
  with `admin_access_only` (`security/authentication.py:187`). Reads: GET list, GET `/{costCenterId}`, GET
  `/{costCenterId}/budget-rollup`. Writes: POST, PATCH `/{costCenterId}`, DELETE `/{costCenterId}`.
- `CostCenterService.ensure_admin` (`service/cost_center_service.py:57`) checks the role a second time, through
  `create`, `list_paginated` and `get_or_404`. `update` and `delete` get their admin check only by calling
  `get_or_404`. The budget-rollup route resolves the cost center through `get_or_404`.
- `admin_or_maintainer_or_auditor_access` (`authentication.py:248`) already admits auditors to budgets, users and
  analytics reads.

## Acceptance criteria

1. An auditor-only user gets a successful response (200) from GET `/v1/admin/cost-centers`, GET
   `/v1/admin/cost-centers/{costCenterId}` and GET `/v1/admin/cost-centers/{costCenterId}/budget-rollup`, with no
   "Access denied". (Jira AC1, AC2)
2. An auditor-only user gets 403 from POST `/v1/admin/cost-centers`, PATCH `/{costCenterId}` and DELETE
   `/{costCenterId}`, and nothing is created, changed or deleted. (Jira AC3, API part)
3. Calling `CostCenterService.update` or `delete` directly as an auditor raises 403 before any repository write,
   even though `get_or_404` now admits auditors.
4. Administrator and maintainer users keep their current results on all six routes. (Jira AC4)
5. A user with none of administrator, maintainer or auditor gets 403 "Access denied" on all six routes. (Jira AC5)
6. The feature gate (403 "Feature not available") and `ENABLE_USER_MANAGEMENT` checks keep their current order and
   responses. `get_or_create` and `ensure_exists_for_project` stay free of a role check.
7. Tests cover: service-level read/write authorization per role; each route's guard by dependency name; and
   HTTP-level TestClient calls per role (auditor, maintainer, admin, plain user) against all six routes with the
   status codes above.

## UI (codemie-ui)

In scope under the same ticket, in the codemie-ui repo (branch EPMCDME-17380_auditor-cost-centers-read). The gate for
the page is the sidebar `tabs.tsx`, not the route (the route has only a feature-flag guard).

8. An auditor-only user sees the Cost centers tab in the Administration sidebar. (Jira AC1 page part)
9. Create, Edit and Delete controls on the Cost centers list and details pages are hidden for users who are neither
   administrator nor maintainer. (Jira AC3, UI part)

## Non-goals

- A new router guard or any change to `admin_or_maintainer_or_auditor_access`, including its `domain=analytics`
  denial log.
- Auditor write access of any kind, or auditor access to other cost-center flows (provisioning, project creation).
- Schema, migration or configuration changes.

## Approaches

- **Chosen:** widen the three GET routes to the existing auditor guard and split read from write authorization in
  the service. Follows the budget router convention from EPMCDME-10930 (`budget_router.py:87,144`).
- Router-only swap: rejected. The service `ensure_admin` would still return 403 to the auditor.
- Widening `ensure_admin` or `get_or_404` alone: rejected. `update`/`delete` would then admit auditors at the
  service layer, leaving only the router to refuse writes.

## Decisions

- UI edits ship under the same ticket in codemie-ui, with the backend change (reverses the earlier backend-only
  decision, after architecture review AR-1) — user
- Reuse `admin_or_maintainer_or_auditor_access` over a new guard — user
- Widen GET budget-rollup with list and detail over keeping it admin-only (the detail page needs it) — user
- Write routes keep `admin_access_only` over any widening — user
- Separate service `ensure_read_access` (list_paginated, get_or_404) from `ensure_admin` (create, update, delete)
  over one widened check — user
- `update`/`delete` do their own admin check then a role-free lookup over reusing `get_or_404` — user
- Leave the shared guard's `domain=analytics` denial log unchanged over fixing it here — user
- Verify with service, dependency-name and per-role HTTP TestClient tests over dependency-name tests only — user

## Left to the builder

Helper names and test fixture shapes (see plan.md). Note: with `ENV=local`, `resolve_is_admin` forces admin, so
auditor tests must patch it as `test_projects_router_auditor.py:41` does.

## Architecture review follow-ups (applied)

- AR-2: the read rule is a named predicate `is_admin_maintainer_or_auditor` in `security/permissions.py`, used by
  `CostCenterService.ensure_read_access`. The shared router guard and its log are unchanged.
- AR-3: `get_or_404` renamed to `get_for_read_or_404`; the write-side lookup is the private role-free
  `_find_active_or_404` (callers check the feature gate and the admin role first).
- AR-4: `_find_active_or_404` no longer repeats the feature-gate check; every caller runs `ensure_feature_enabled()`
  first, so the order and responses of the feature gate and `ENABLE_USER_MANAGEMENT` are unchanged.
- AR-5: plan.md acceptance criteria match this spec and name the per-role HTTP test file.
