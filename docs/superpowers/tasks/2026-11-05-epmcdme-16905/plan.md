# Provisioning Project Budget Writes — Implementation Plan (EPMCDME-16905)

> The calling flow picks the execution mode. Steps use `- [ ]`. Commit per task using the repository's existing convention.

**Goal:** `PUT|PATCH|DELETE /v1/provisioning/projects/{project_name}/budget`, one all-or-nothing call each.
**Architecture:** A read-only check phase, then commit 1 (sync: project, cost center, admins), then commit 2 (async: group and LiteLLM). A 4xx after commit 1 becomes 500, a category whose sync is not ok becomes 502, and a resend converges.
**Tech Stack:** FastAPI, SQLModel/SQLAlchemy, pydantic v2, pytest-asyncio. **Spec:** `spec.md` (+ `story.md` AC1–12). `svc` and `pbs` are as in the spec.

## Global Constraints
- `X-Change-Reference` is 1–64 chars and required. The path name is stripped, lowercased and 3–100 chars.
- A new group is named `f"{client.name} {reference}"[:100]`, with duration `30d`.
- Every log line has `domain=provisioning`, `client=` and `change_reference=`. Events are named `provisioning_*`. Logs and the OpenAPI baseline are asserted verbatim.
- A 4xx after commit 1 gives 500 `"partly applied; send the same request again"`. A category whose sync is not ok after commit 2 gives 502 with the same message. LiteLLM's 502 passes through.
- Tests run with `poetry run pytest <path> -q`.

## Negative constraints
- No single transaction and no compensating rollback; cancel never creates and never needs manage-projects → T7.
- The provider sync changes only as Design 6 says → T2. The admin API's routes, bodies and codes and the existing public `pbs` signatures stay as they are → T1, T2.
- No re-link, rename or admin removal → T4, T6. No project limit or creator membership: T6 reuses `create_shared_project_in_session`.
- The reference appears only in logs and a new group's name; T4's event does not carry it → T7.
- No vendor names, no action field, no POST → T5, T6, T8. A route declares only manage-budgets, with one 403 path → T3, T8.
- The cause never goes in a 500's `details`, and the handler stays as it is (`main.py:1564-1572`). The cause is logged; the request uuid on the log line comes from `main.py:1546-1549` → T7.
- ITSM ServiceNow code stays as it is → T7's grep covers only the provisioning names.

## Review Focus
- RF1: A resend after a commit-2 failure on a new project gives 200 with `created_project: false`, not 409 (T7).
- RF2: A path name cased differently from the stored one finds the same project, and the response gives the stored name (T7).
- RF3: A `project.cost_center` equal to the project name is created and linked (T6).
- RF4: A listed admin who is already an admin is reported as assigned and not promoted again (T5, T6).
- RF5: PATCH with no budget creates one (200, `created_budget: true`). A merged platform of 0 gives 400 (T7).

---

### Task 1: Read-only group plan check in `pbs`
**Files:** `src/codemie/service/budget/project_budget_service.py` (:1917-1933, :2163-2168, :2204, :2246-2251). Tests: `tests/codemie/service/budget/test_project_budget_service_group_creation.py`, `test_project_budget_service.py`.
**Produces:** `async ProjectBudgetService.check_group_request(session, project_name: str, data: ProjectBudgetGroupCreateRequest | ProjectBudgetGroupUpdateRequest) -> None`. Writes nothing; raises 400.
Test-first: yes — an update below members' fixed limits writes the row and rotates the key before its 400.
- [ ] Tests:
  - `test_check_group_request_rejects_*`: duration, category, missing platform, pct sum, `_validate_constraints`, and fixed member limits over the planned max or soft limit (`pbs:274-276`).
  - `test_update_group_below_fixed_limits_is_400_and_writes_nothing` (AC19): no row update and no provider call.
  - `test_create_group_runs_check_first`.
- [ ] Run: FAIL.
- [ ] Implement:
  - Effective total and duration come from `data`, or else from the active group.
  - Run `_DURATION_RE` and `_validate_group_categories`. Then, for each category with pct > 0, run `_validate_constraints(_resolve_soft_amount(...), amount, ...)`. For an existing category, also run `_equal_amounts_with_fixed_overrides` over its active allocations. `ValidationException` becomes 400.
  - Call it first in group create, and in group update after the lookup (:2165).
  - Delete the inline checks it replaces.
- [ ] Run `tests/codemie/service/budget`: PASS.

### Task 2: Sync only changed or failed categories; update without a provider ref
**Files:** `pbs:704-714`, `src/codemie/enterprise/litellm/budget_provider_adapter.py:888-913`. Tests: `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` and a new `tests/codemie/enterprise/litellm/test_budget_provider_adapter_update.py`.
Test-first: yes — resending the same amounts calls `provider.update_project_budget` for every category.
- [ ] Tests:
  - `test_update_with_stored_values_makes_no_provider_call`: sync ok, called directly and through the group update (AC20).
  - `test_update_after_failed_sync_resyncs`.
  - Adapter `test_update_without_provider_ref_recreates_key`: the key is recreated and `_delete_project_api_key` is never called with `None`.
  - Update the tests that expect `missing_provider_budget_ref`.
- [ ] Run: FAIL.
- [ ] Implement:
  - `amounts_changed` is true when the effective max, soft limit or duration differs from the stored value (`Decimal(str(x))`), or when the stored `sync_status` is not ok.
  - The adapter returns FAILED only when `service is None`. It deletes the old key only when the old ref is set.
- [ ] Run: PASS.

### Task 3: Public `require_permission`
**Files:** `src/codemie/rest_api/security/provisioning_access.py:79-94`. Tests: `tests/codemie/rest_api/security/test_provisioning_access.py`.
**Produces:** `require_permission(client: ProvisioningClient | None, permission: ProvisioningPermission, actor_user_id: str) -> ProvisioningClient`. Same 403 body and `access_denied_provisioning_permission` line as today. `RequirePermission.__call__` delegates to it, and the module docstring lists it.
Test-first: yes — the function does not exist.
- [ ] Tests: a client with the permission is returned. A client without it, or `None`, gets 403 and the verbatim log line. The existing tests stay green.
- [ ] Run: FAIL.
- [ ] Implement.
- [ ] Run: PASS.

### Task 4: Conditional cost-center link
**Files:** `src/codemie/service/project/project_service.py` (after :252), `src/codemie/repository/application_repository.py`. Tests: `tests/codemie/service/project/test_project_service.py`.
**Produces:**
- `ProjectService.link_cost_center_in_session(session, *, actor: User, project: Application, cost_center_id: UUID) -> UUID | None`: returns the stored link; no commit.
- It uses `application_repository.set_cost_center_if_unset(session, project_id: str, cost_center_id: UUID) -> bool`: `UPDATE … WHERE id = :id AND cost_center_id IS NULL`.

Test-first: yes — the method does not exist.
- [ ] Tests: an unlinked project gets the new link and a `PROJECT_UPDATED` event (as :322-331). A linked project keeps its link, with no event.
- [ ] Run: FAIL.
- [ ] Implement.
- [ ] Run: PASS.

### Task 5: Caller-neutral models
**Files:** `src/codemie/rest_api/models/provisioning.py`. Move the tests with `git mv tests/codemie/rest_api/models/test_servicenow_integration.py tests/codemie/rest_api/models/test_provisioning.py` and rewrite them.
**Produces:**
- `ProvisioningProjectName`: Annotated str, stripped, lowercased, 3–100.
- `ProvisioningProjectBlock(cost_center: str | None, admin_emails: list[NormalizedEmail])`.
- `ProvisioningBudgetRequest(amounts, project: ProvisioningProjectBlock | None = None)`.
- `ProvisioningBudgetSetRequest`: platform > 0.
- `ProvisioningBudgetCategoryResponse`, `ProvisioningBudgetGroupResponse`, `ProvisioningBudgetResponse`: the historic spec's fields (:80-102) plus `created_budget`, with `admins_not_assigned` in place of `admins_pending`.
- `ProvisioningValidationErrorResponse`: `{"error": {"message": str, "details": list[dict]}}` (`main.py:1806-1814`).

Request models use `extra="forbid"`. The rules come from `models/servicenow_integration.py:25-29,50-87`.
Test-first: yes — the models do not exist.
- [ ] Tests:
  - An unknown field at the top level or in `project` is an error.
  - `amounts` missing, empty, negative or `inf` is an error.
  - Set with platform 0 is an error; adjust with platform 0 is valid.
  - Emails are normalized and deduplicated, and so is `cost_center`.
  - The schema has no `action`, `epam_project_code` or `servicenow`.
- [ ] Run: FAIL.
- [ ] Implement.
- [ ] Run: PASS.

### Task 6: Project steps in `provisioning_service.py`
**Files:** `src/codemie/service/provisioning_service.py` (docstring "Project operations of the provisioning API."), `src/codemie/service/cost_center_service.py:102`. Tests: `tests/codemie/service/test_provisioning_service.py`.
**Consumes:** T4.
**Produces:** public classmethods. The caller authorizes, and nothing commits.
- `find_project(session, project_name) -> Application | None`: deleted 409, personal 403 (`svc:297-311`).
- `check_project_prerequisites(session, project: Application | None, project_name: str, cost_center: str | None) -> None`. A new project's name must be valid. The cost-center checks (`svc:274-282`) run only when the cost center would be created or linked.
- `resolve_project(session, actor, project_name, cost_center) -> ResolvedProject(project, cost_center: str | None, created_project, created_cost_center)`. It re-reads the project in `session`, creates it or links it (T4), and reports the stored link.
- `assign_admins(session, actor, project, emails, action: str) -> AdminAssignment(assigned, not_assigned, changed_user_ids)`: add-only (`svc:490-523`).

The help text in both files becomes `"Restore the cost center or use a different cost center name."`.
Test-first: yes — the functions do not exist.
- [ ] Tests (port the `svc` cases):
  - Linked to A, B named → stays on A, and B is not checked (AC8).
  - Unlinked, B named → linked to B.
  - Cost center equal to the project name → created and linked (RF3).
  - Unknown email or service account → `not_assigned`, and nobody is removed (AC9).
  - An existing admin is not in `changed_user_ids` (RF4).
  - Cost centers disabled → 403; a deleted cost center → 409.
- [ ] Run: FAIL.
- [ ] Implement.
- [ ] Run: PASS.

### Task 7: `ProvisioningBudgetService`
**Files:**
- `git mv src/codemie/service/servicenow_integration_service.py src/codemie/service/provisioning_budget_service.py`
- `git mv tests/codemie/service/test_servicenow_integration_service.py tests/codemie/service/test_provisioning_budget_service.py`
- Delete `src/codemie/rest_api/models/servicenow_integration.py`.
- `src/codemie/repository/project_budget_repository.py:944`: `get_active_by_project` gets `for_update: bool = False`.

**Consumes:** T1, T3, T5, T6.
**Produces:**
- `set_budget(actor: User, client: ProvisioningClient, reference: str, project_name: str, request: ProvisioningBudgetSetRequest) -> ProvisioningBudgetResult`
- `adjust_budget(…, request: ProvisioningBudgetRequest)`, same result
- `cancel_budget(actor, client, reference, project_name) -> None`

The result adds `created_budget` and renames `admins_pending` to `admins_not_assigned`. Each entry point passes its own route as the admin label (`"PUT /v1/provisioning/projects/{project_name}/budget"`, `"PATCH …"`). There is no action enum.
Test-first: yes — the service does not exist, and the checks still run after commit 1.
- [ ] Tests (adapt the moved cases, then add these):
  - **Rejections write nothing; commit 1 never runs.** Covers deleted 409, personal 403, D12 403 (missing project, or a block that names a cost center or admin), names, cost centers disabled, `check_group_request` 400 and merged platform 0 (AC6, AC10, AC17, AC19, RF5).
  - **Allowed without manage-projects:** a budget-only client with no block or an empty block, and cancel.
  - **Set and adjust:**
    - A set on a new project creates everything, including the group `"Portal RITM1"`, with `created_budget` true (AC1).
    - Set replaces (AC2). Adjust merges, and 0 removes a category (AC3). PATCH with no budget creates one.
  - **Cancel:** deletes, is a no-op when there is no budget, and gives 404 for a missing project (AC4).
  - **Partial writes:**
    - `pbs` create raises 409 and the re-read finds no group → 500, commit 1 stays, and the resend creates the group (AC14, AC7).
    - LiteLLM's 502 passes through, and the resend creates (AC15).
    - A category whose `sync_status` is not ok → 502 (AC20).
  - **Concurrency and resend:**
    - Commit 2 re-plans against the locked group's amounts, so an adjust keeps a category that changed after the check.
    - A resend after a commit-2 failure gives `created_project` false and no 409 (RF1).
    - A path name in another case resolves to the stored project (RF2).
  - **Logs:** the verbatim lines `provisioning_budget_request_started|completed|failed`, `provisioning_budget_partly_applied`, `provisioning_sync_conflict_retry` and `provisioning_project_resolved` (AC5).
- [ ] Run: FAIL.
- [ ] Implement:
  - **Check phase:**
    1. `find_project`.
    2. `require_permission(client, MANAGE_PROJECTS, actor.id)` when D12 applies.
    3. `check_project_prerequisites`.
    4. Plan the amounts (`svc:76-145`, unchanged).
    5. Build the exact group request; a `pydantic.ValidationError` becomes 400.
    6. `check_group_request`.
  - **Commit 1:** `resolve_project` and `assign_admins`, with one `IntegrityError` retry and cache invalidation (`svc:384-445`).
  - **Commit 2:**
    - Read the group `for_update`. If it exists, re-read, re-plan and update. If not, create, with the 409 fallback of `svc:350-361`.
    - A category whose sync is not ok gives 502.
    - A 4xx is logged as `provisioning_budget_partly_applied` with its cause and raised as 500.
- [ ] Run `tests/codemie/service`. Then `rg -n "servicenow_integration|ServiceNowBudget|ServiceNowIntegration|servicenow_(budget|sync|project)_" src/codemie tests/codemie` must find nothing (AC18).

### Task 8: Budget routes
**Files:** create `src/codemie/rest_api/routers/provisioning/budgets.py`, included at `routers/provisioning/__init__.py:39`. Tests: `tests/codemie/rest_api/routers/test_provisioning_budgets_router.py`. Regenerate the baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json`.
**Consumes:** T5, T7.
**Parameters:**
- `project_name: ProvisioningProjectName`
- `change_reference: Annotated[str, Header(alias="X-Change-Reference", min_length=1, max_length=64)]`
- `actor: User = Depends(authenticate)`
- `client = Depends(RequirePermission(MANAGE_BUDGETS))`

**Status codes:** PUT sets `response.status_code` to 201 when `created_budget` is true and 200 otherwise (`routers/conversation/history.py:261`). PATCH answers 200, DELETE 204.
**`responses`:** 403 `ErrorResponse` (its description names the manage-projects condition), 404, 409, 422 `ProvisioningValidationErrorResponse`, 500, 502.
Test-first: yes — the routes give 404.
- [ ] Tests (fixtures as in `test_provisioning_router.py:44-85`, service mocked):
  - PUT gives 201 or 200, PATCH 200, DELETE 204 (AC13).
  - A missing header or a 65-char one → 422 (AC5).
  - The AC16 bodies → 422 in the platform shape.
  - `"  My-Proj "` reaches the service as `my-proj`; a 2-char name → 422.
  - No manage-budgets → 403 on all three.
  - The operations and schemas contain no `servicenow`, `epam_project_code` or `action` (AC11).
  - `TestRouteContract` stays green (AC17).
- [ ] Run: FAIL.
- [ ] Implement. Map the result to the response in the router.
- [ ] Run `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`. The diff must contain only the three operations and the new schemas. Then run `tests/codemie/rest_api`: PASS.

### Task 9: Provisioning guide and pointers
Test-first: no — documentation only.
- [ ] Write `.ai-run/guides/api/provisioning-api.md`.
  - **Coverage:** the list in story AC12. Link `endpoint-conventions.md` and state only where provisioning departs from it: PUT creates the parent, the block is add-only, PUT answers 201/200, and unknown fields are rejected (the first `extra="forbid"` model in `rest_api`).
  - **Registry:**
    - Systems that call `/v1/provisioning` go in the provisioning client registry. Signed-request apps stay in authorized applications.
    - `provisioning:manage-projects` lets a client create shared projects and make any active user an admin of any existing shared project.
    - `provisioning:manage-budgets` replaces or cancels the budget of every shared project, including one a maintainer set. Grant both accordingly.
  - **Permissions:** one declared permission per route. A permission the target state triggers is checked with `require_permission` in the check phase. The `routers/provisioning/__init__.py:15-20` docstring says the same.
  - **Admins:** they are only added. A future endpoint whose target is the project must say whether its admin list replaces or adds.
  - **Pointers:** AGENTS.md gets a Guide Imports row and a Task Classifier row. Add a pointer at `endpoint-conventions.md:27-29`.
