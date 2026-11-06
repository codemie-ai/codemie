# Technical Research

**Task**: provisioning project budget writes composite all-or-nothing change reference guide
**Generated**: 2026-11-05 (sliced from ../../technical-analysis.md)
**Research path**: codegraph (parent run)

---

## 1. Original Context

This slice covers the second child of the parent story (`../../story.md`), which depends on the clients-and-lookup child: registry, permissions, access lock, `/v1/provisioning` router package. It adds `PUT|PATCH|DELETE /v1/provisioning/projects/{project_name}/budget` for set, adjust and cancel. Each is one composite, all-or-nothing call that also creates the project and cost center and assigns admins when needed.

Rules:
- every check runs before the first write;
- every 4xx means nothing changed, and a partly applied request answers 5xx;
- resending completes an interrupted request;
- a required `X-Change-Reference` header is recorded in log lines and in a new budget's name `"<client name> <reference>"`;
- `provisioning:manage-projects` is required only when the call would create the project or carries a project block.

The slice also documents the provisioning pattern in `.ai-run/guides/api/` and AGENTS.md, and regenerates the OpenAPI baseline.

---

## 2. Codebase Findings

Paths: `svc` = `src/codemie/service/servicenow_integration_service.py`; `rtr` = `src/codemie/rest_api/routers/servicenow_integration.py`; `mdl` = `src/codemie/rest_api/models/servicenow_integration.py`.

### Existing Implementations

**P2. Apply a budget to a project, creating project / cost center / admins in one call**
- `rtr`: `POST /budgets` :162-197 → `apply_budget_request`; `_to_budget_response` :44-73.
- `mdl`: action enum :32-37; request :40-101 (`request_id`, `epam_project_code`, optional `codemie_project_name` defaulting to code, normalized/deduped `admin_emails`, `amounts` per `BudgetCategory`; set needs platform>0; cancel clears the rest); response :126-142 (`created_project`, `created_cost_center`, `admins_assigned`, `admins_pending`).
- `svc` rules: set = unsent categories → 0 (:76-77); adjust = unsent kept, 0 removes (:80-83); platform must stay >0 → 400 (:90-91); soft budget preserved (:142).
- `svc` order: `_check_upsert` reads only (:246-261) → `_resolve_and_assign` (sync, **commit 1** :438, one retry on `IntegrityError` :413-419) → `_write_group` (async, **commit 2** :338). Project lookup case-insensitive; deleted → 409, personal → 403 (:297-311). Cost center created only when code ≠ project name (:478-481); existing project's cost center never changed (:471-474). Admins: active non-service-account users promoted via `bulk_assign_users_to_project` with `ASSIGN_ADMINS_ACTION = "POST /v1/integrations/servicenow/budgets"` (:67, :504-537), others → pending (never revisited, :514-517), nobody removed. Group name `f"ServiceNow {request_id}"[:100]` (:345), duration `30d`; create 409 → re-plan + update (:350-361). Cancel: 404 missing project, 200 no-op without group (:212-226).
- Sibling: admin API `/v1/admin/project-budget-groups` (`project_budget_router.py:521-602`, maintainer-only, group id in path, POST 201 / DELETE 204).

**P4. Record who changed what**
- Activity events `service/activity/activity_models.py:171-188`; not used for the change reference (parent D5). `request_id` only in `svc` logger lines (`domain=servicenow_integration`, :174-200, :416-450).

**Other touched code**: `models/analytics.py:175-186` (`ErrorResponse`); `cost_center_service.py:62-69` (`costCenters` feature → 403), `:88-106` (`get_or_create`, help text "use a different EPAM project code" :102).

### Decisions Already in the Code
- Two transactions (sync then async) instead of one (`svc` :233-242); reuses sync and async services.
- Reused helpers run "in caller's transaction, caller must authorize": `create_shared_project_in_session` skips project limit and adds no creator membership (`project_service.py:193-252`); `CostCenterService.get_or_create` (:88-106).
- Conventions: PUT = full replace (`endpoint-conventions.md:27-29`) — provisioning departs (documented in guide); dynamic 201/200 precedent at `routers/conversation/history.py:261`; headers via `Annotated[..., Header(alias=...)]` (`routers/metrics.py:47-52`).

### Task Statements Checked Against the Code
- "every check before the first write" — **contradicted in part**: `create_project_budget_group` validation (`project_budget_service.py:1928-1944`) and the conflict re-plan 400 (`svc` :360) run after commit 1; the conflict path can also answer 409 after commit 1 (:352-358). Provider failure returns 502 (`project_budget_service.py:657-661`).
- Two commits plus LiteLLM: provider sync `project_budget_service.py:2027`, member sync `project_assignment_service.py:525`.
- Resend completes a request: find-or-create, promote only changed admins, create→update fallback — holds.
- "existing ErrorResponse … 422 shape" — 422 body (`main.py:1801-1826`) has `details` as a list; `ErrorDetail.details` is `str`.
- Existing project with no cost center: today returned as is (:471-474); new rule links it (child D10).

### Consumers of Changed Contracts
- `mdl`: `rtr`, `svc` :47, tests. `ASSIGN_ADMINS_ACTION`: test `svc` :378. Log strings asserted in `test_servicenow_integration_service.py:1187-1223`.
- OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json`.

### Architecture and Layers Affected
API (budget routes in the provisioning router package, request/response models, header, dynamic 201/200, 204); Service (moved, caller-neutral budget service over project, cost-center, assignment and budget services; checks moved before commit 1); docs (API guide, AGENTS.md).

### Integration Points
`project_budget_service` (LiteLLM provider sync), `ProjectAssignmentService` (member budget sync), `project_service`, `cost_center_service`, `user_repository.get_by_emails`, `invalidate_user_from_cache`.

### Patterns and Conventions
Classmethod service singleton; sync work via `asyncio.to_thread`; `ExtendedHTTPException(code, message, details, help)`; `key=value` logs with `domain=`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/api/rest-api-patterns.md`, `.ai-run/guides/api/endpoint-conventions.md` (PUT full-replace rule the provisioning guide departs from), `.ai-run/guides/testing/testing-api-patterns.md:26-42` (baseline regen). No guide covers provisioning yet; no `AGENTS.md` row.

### Architectural Decisions
Historic run `docs/superpowers/tasks/2026-10-19-servicenow-budget-integration/` (spec, plan, `budget-flow.md`).

### Derived Conventions
See Decisions Already in the Code.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/service/test_servicenow_integration_service.py` (set/adjust/cancel, conflicts, cost centers, log strings); `tests/codemie/rest_api/routers/test_servicenow_integration_router.py` (validation, mapping); router guards `test_openapi_snapshot.py`.

### Testing Framework and Patterns
pytest ^8.3.1, pytest-asyncio ^1.4.0; `TestClient` on a bare app, `dependency_overrides[authenticate]`, `AsyncMock` service.

### Coverage Gaps
No test for a resend after a provider failure on a later budget category (`project_budget_service.py:1962-2040`); no concurrency test for the conflict after commit 1.

---

## 5. Configuration and Environment

### Environment Variables
`ACTIVITY_EVENTS_ENABLED` (not used for the reference). Provisioning flag and registry come from the clients-and-lookup child.

### Configuration Files
None new.

### Feature Flags and Deployment Concerns
Customer feature `costCenters` gates cost-center creation (403 when disabled).

---

## 6. Risk Indicators
- Checks after commit 1 must move before it; the concurrent-creation conflict after commit 1 must answer 5xx.
- Resend must converge after a provider failure mid-categories (provider idempotency unverified).
- Log strings and the OpenAPI schema are asserted verbatim; baseline regen required.
- 422 body shape differs from the `ErrorResponse` model.
- Service-path projects skip the project limit and get no creator membership (kept, child D6).

---

## 7. Summary for Complexity Assessment

Touches API (three write routes with header, dynamic 201/200 and 204, caller-neutral models), Service (~540-line budget service moved and renamed; checks reordered across a two-commit write with LiteLLM sync; permission check that depends on whether the call creates the project or carries a project block) and docs (provisioning guide, AGENTS.md). Business logic exists and is well tested. Key risks: reordering checks without breaking idempotent resend, 5xx mapping for partial writes, verbatim log/OpenAPI assertions.

---

## 8. External References

None named by the task.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- `routers/provisioning/__init__.py:33-37`: parent `APIRouter(prefix="/v1/provisioning")`, deps `authenticate` → `provisioning_clients_only`. `:39` includes `projects.router`. `mount_provisioning_api` `:42-58` mounts only if `PROVISIONING_API_ENABLED` and `ENABLE_USER_MANAGEMENT`, then `set_provisioning_routes(router)`. Called at `main.py:117,1511`.
- `routers/provisioning/projects.py:40-68`: only `GET /projects`. It has no path param, uses `RequirePermission(READ_PROJECTS)` and logs `provisioning_member_lookup: client=…, domain=provisioning`.
- `enforce_provisioning_scope` `:54-68` is called from `authentication.py:155`.
- `configs/provisioning_clients_config.py:40-43`: `READ_PROJECTS`, `MANAGE_PROJECTS`, `MANAGE_BUDGETS`. No route uses the last two yet. `ProvisioningClient.permissions: frozenset` (`:51`), `name` stripped and non-empty (`:53-59`). Registry: `config/provisioning_clients/provisioning-clients-config.yaml`.
- `service/provisioning_service.py:15-44`: docstring "Read operations of the provisioning API". The only method is `list_member_projects`. Consumers: `projects.py:25,60`, `tests/codemie/service/test_provisioning_service.py`.
- `rest_api/models/provisioning.py:20-26`: only `ProvisioningProjectResponse`. Consumers: `projects.py:23`, `test_provisioning_router.py:211`.
- JSON shape exists only in the historic spec `2026-10-19-…/spec.md:80-102`:
- `created_budget` is an unused kwarg of `_sync_created_project_budget` (`pbs:612`, `_ = created_budget` `:663`). It is passed at `pbs:1002,2030,2413` and in tests (`test_project_budget_service.py:171-873`).
- `created_budgets: int` is an unrelated sync counter (`models/budget.py:116`).
- It is passed to the provider as `project_budget_id` (`budget_provider_adapter.py:816-824`).
- The project API names a cost center by `cost_center_id` (UUID) in requests and `cost_center_name` in responses (`models/project.py:159-160,201-202,252-253,296-297`).
- `ProjectService.update_project` (`project_service.py:255-336`):
- `codemie_tools/itsm/toolkit.py:19-25`;
- `CredentialTypes.SERVICENOW = "ServiceNow"` (`codemie_tools/base/models.py:97`);
- `service/settings/settings.py:37,236`;
- `service/settings/settings_tester.py:41-42,101,141`;
- `tests/…/callable_tool_classes.txt:129`;
- OpenAPI baseline enum `openapi.json:8994`.
- Per-route permission contract: `test_provisioning_router.py:194-204` asserts **exactly one** `RequirePermission` per route, ordered after `authenticate` and `provisioning_clients_only`. Reason: the module docstring (`__init__.py:15-20`).
- Admin action label: `bulk_assign_users_to_project(…, action)` uses `action` only to log the HTTP method on a personal-project rejection (`project_assignment_service.py:225-240`). `svc` rejects personal projects first (`:305-310`). Constant: `ASSIGN_ADMINS_ACTION` (`svc:67`).
- Request models: no `rest_api/models` class sets `extra="forbid"`. The precedent is `provisioning_clients_config.py:47`, `routers/marketplace_management.py` and `service/marketplace_management/models.py`.
- Group create payload: `ProjectBudgetGroupCreateRequest` validates `name` 1–100, `total_amount` > 0 and the duration pattern when it is constructed (`project_budget.py:131-139`). This is a pydantic error, not `ExtendedHTTPException`.
- "Sub-router included at `__init__.py:39`, so lock and guard cover it (`pa:46-76`)": **holds**. The allowlist comes from `router.routes` at mount (`pa:49-51`), and the guard is a parent dependency (`__init__.py:36`).
- "A 4xx after commit 1 becomes 500 … original message in `details`": **contradicted**. For `code == 500`, `extended_http_exception_handler` replaces `details` with `INTERNAL_ERROR_DETAILS` + request id (`main.py:1565-1572,1653-1661`). Codes > 500, such as 502, keep `details` (`:1665-1667`).
- "Link an unlinked existing project (D10)": **keeps a mechanism in place**. Today the link is set only at creation, and the only update path is `ProjectService.update_project`, with its own commit and actor authorization. Consumers: `routers/projects/management.py:60`.
- `main.py:1511`;
- `test_provisioning_router.py:31,194-214` (one-permission rule over **all** routes; vendor-free OpenAPI only for `GET /projects`);
- `test_guarded_app_profile.py:36-41,59-62`.
- `pbs` public group methods used by `svc`: `create_…` `:1917`, `update_…` `:2151`, `delete_…` `:2425`, `get_project_budget_group` `:2063`. These are also called by the admin router `project_budget_router.py:530,584,601`.
