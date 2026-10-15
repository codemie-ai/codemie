# Technical Research

**Task**: budget project hard-limit validation
**Generated**: 2026-10-15
**Research path**: codegraph

---

## 1. Original Context

Ticket EPMCDME-16015 — "Platform Budget Hard Limit Must Be Greater Than Zero". Implement the BACKEND part only (this repo).
Story: As a platform administrator managing project budgets, I want the Platform Hard Limit to always be greater than 0 when I create or update a budget so that a project can never be left without usable Platform budget.
Context: Project budgets are managed from Administration → Projects via Create Budget / Update Budget popups. Form: Name, Reset Period, Description, Total Budget, Distribution (Platform / CLI / Premium Models), Hard Limit and Soft Limit per category. A separate older single-category budget popup already requires a positive hard limit; not part of this change. Existing projects may already hold a Platform budget of 0; they must keep loading (GET/list) without errors.
Backend acceptance criteria:
- AC5: Given an existing project whose saved Platform budget is 0, when the administrator saves an update to the budget, then backend validation still applies and a Platform Hard Limit of 0 is rejected.
- AC6: CLI Hard Limit, Premium Models Hard Limit, and any Soft Limit (including Platform Soft Limit) may be 0 and the budget can be saved.
- AC7: A create or update budget request with a Platform Hard Limit of 0 sent directly to the backend is rejected with a validation error and nothing is saved.
- AC8: A create or update budget request with a Platform Hard Limit greater than 0 is accepted as before.
Out of scope: restrictions on CLI/Premium/soft limits; migration of existing zero-budget projects; changes to the older single-category budget flow.
Find: the request models/endpoints for project budget create/update (multi-category: platform/cli/premium), where hard limit validation currently lives (incl. the older single-category positive-hard-limit rule to mirror), response models used for reading existing budgets (must NOT reject 0), error handling conventions, and existing tests to extend.

---

## 2. Codebase Findings

### Existing Implementations

**Multi-category "budget group" flow (the Create/Update Budget popups):**
- `src/codemie/rest_api/routers/project_budget_router.py` — `group_router`:
  - `POST ''` -> `create_project_budget_group` (L510-523), `maintainer_access_only`.
  - `PUT '/{group_id}'` -> `update_project_budget_group` (L559-578); admin/maintainer or project app-admin; `_ensure_allowed_budget_group_update_fields` (L176) limits project admins to `categories`.
  - `GET ''` / `GET '/{group_id}'` read groups via `ProjectBudgetGroupResponse`.
- `src/codemie/rest_api/models/project_budget.py`:
  - `CategoryBudgetSpec` (L121): `pct: float` (no bounds), `soft_budget: Optional[float] = Field(ge=0)`.
  - `ProjectBudgetGroupCreateRequest` (L130): `total_amount: float = Field(gt=0)`, `categories: dict[str, CategoryBudgetSpec]`.
  - `CategoryBudgetSpecUpdate` (L153): `pct: float` (schema hint 0..100, description "Set to 0 to remove this category from the group"), `soft_budget ge=0`.
  - `ProjectBudgetGroupUpdateRequest` (L161): all optional; `total_amount gt=0`; `categories: Optional[dict[str, CategoryBudgetSpecUpdate]]`.
- **There is no per-category hard-limit field in the API.** The category hard limit (`Budget.max_budget`) is derived as `total_amount * pct / 100` (Decimal math) in `project_budget_service.py` L1867 (create) and L2181 (update). With `total_amount > 0` already enforced, Platform hard limit == 0 iff `categories["platform"].pct == 0`.
- `src/codemie/service/budget/project_budget_service.py`:
  - `_validate_group_categories(categories, total_amount)` (L1765-1795, `@staticmethod`): rejects unknown keys, requires `'platform'` key ("'platform' category is required"), each pct finite and 0..100, sum == 100 within `PCT_SUM_TOLERANCE` (L69), `total_amount > 0`. All raise `ExtendedHTTPException(code=400, ...)`. **Currently allows platform pct = 0.**
  - `create_project_budget_group` (L1818): calls `_validate_group_categories` (L1834) before any insert; categories with `pct <= 0` are skipped (no Budget row created, L1864).
  - `update_project_budget_group` (L2048): order is `_update_group_scalar_fields` (L2065, name/description/duration) -> notification updates (L2082) -> `_validate_group_categories(data.categories, eff_total)` only `if data.categories is not None` (L2100-2101) -> `_update_group_categories` (pct<=0 deletes existing category budget L2184-2187; else update/create).

**Older single-category flow (out of scope, rule to mirror):**
- `ProjectBudgetCreateRequest.max_budget: float = Field(gt=0)` (L42) and `ProjectBudgetUpdateRequest.max_budget: Optional[float] = Field(gt=0)` (L55); `soft_budget ge=0`. Pydantic -> 422.
- Global budgets: `BudgetService._validate_constraints` (`src/codemie/service/budget/budget_service.py` L131-148) raises `ValidationException("max_budget must be > 0")`.

**Read/response models (must keep accepting 0):**
- `ProjectBudgetGroupResponse` (L187) / `CategoryBudgetDetailResponse` (L172, `max_budget: float`, no bounds), `ProjectBudgetResponse` (L71, `max_budget: float`, no bounds). Read paths (`_load_group_full_result` L1797) never call `_validate_group_categories`. A platform-0 group simply has no platform category row.

### Architecture and Layers Affected
- API schema layer: `rest_api/models/project_budget.py` (group request models).
- Service layer: `ProjectBudgetService._validate_group_categories` (shared by create and update).
- Router layer unchanged in shape; errors propagate as `ExtendedHTTPException`.

### Integration Points
- `_update_group_categories` / create loop call the active budget provider (LiteLLM) via `_sync_created_project_budget` / `update_project_budget`; validation runs before any provider call in both paths.
- `get_async_session` (`src/codemie/clients/postgres.py` L272) is a plain `AsyncSession` context; routers commit only after the service returns, so an exception raised in the service discards pending scalar/notification updates (no commit).

### Patterns and Conventions
- Group-flow business rules live in the service and raise `ExtendedHTTPException(code=400, message=...)`; the group request models deliberately leave `pct` unbounded (tests assert negatives/>100 pass the model and are rejected by the service with 400).
- Single-category flow uses Pydantic `gt=0` (422).

---

## 3. Documentation Findings

### Guides and Architecture Docs
No budget-specific guide in `.ai-run/guides/`. Relevant general guides: `development/error-handling.md`, `testing/testing-patterns.md`, `testing/testing-service-patterns.md`, `api/rest-api-patterns.md`.

### Architectural Decisions
- `CategoryBudgetSpecUpdate.pct` description: "Set to 0 to remove this category from the group" — pct=0 is the delete signal for a category on update.
- Test file docstring `test_project_budget_router_schema.py`: model accepts out-of-range pct; "service raises 400".

### Derived Conventions
- Validation errors for the group flow: 400 via `ExtendedHTTPException`, message naming the offending category key (tests assert `offending_key in message`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/budget/test_project_budget_service_group_creation.py` — direct unit tests of `_validate_group_categories` using `SimpleNamespace(pct=...)`:
  - `test_platform_zero_passes_validation` (L50-59) **asserts platform pct 0 is accepted — directly contradicts AC7.**
  - `test_platform_only_passes_validation`, `test_two_categories_with_zero_passes_validation` (cli/premium 0 accepted — AC6 reference).
  - `test_sum_not_100_fails_validation`, `test_platform_missing_fails_validation`, parametrized `test_system_rejects_invalid_distribution_values` (negative/nan/just-below-zero; asserts code 400 and key in message).
- `tests/codemie/rest_api/routers/test_project_budget_router_schema.py` — `CategoryBudgetSpec`/`CategoryBudgetSpecUpdate` accept pct 0, negative, >100 at model level.
- `tests/codemie/rest_api/routers/test_project_budget_router.py` — group update access tests (`test_update_group_write_access`, project-admin categories-only tests).
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` — `update_project_budget_group` service tests (e.g. `test_update_project_budget_group_logs_on_scalar_only_edit`).
- Also present: `test_project_budget_service.py`, `test_project_budget_create_enrichment_guard.py`, `test_project_budget_router_additional.py`.

### Testing Framework and Patterns
pytest, `unittest.mock.patch`, `SimpleNamespace` specs for static validator tests; router tests patch the service and a `_session_ctx` helper.

### Coverage Gaps
- codegraph reports no tests within 3 hops for `create_project_budget_group` (service and router).
- No test for update of a group whose current platform category is absent (platform 0) with platform pct 0 in payload.

---

## 5. Configuration and Environment

### Environment Variables
None specific to this validation.

### Configuration Files
`budgets-config.yaml` (`src/codemie/configs/budget_config.py`) governs predefined global budgets only; not involved.

### Feature Flags and Deployment Concerns
None found; no DB migration involved in existing code paths for this rule.

---

## 6. Risk Indicators

- `test_platform_zero_passes_validation` in `test_project_budget_service_group_creation.py` encodes the opposite behavior and will fail once the rule changes.
- `update_project_budget_group` validates only when `data.categories is not None` (L2100); an update omitting `categories` (name/description/duration/total_amount/notification-only) on a platform-0 group is not checked. Whether AC5 "saves an update" covers such payloads is a spec decision.
- `update_project_budget_group` applies scalar and notification DB writes (L2065, L2082) before category validation (L2101); correctness of AC7 "nothing is saved" relies on the router not committing after the exception (current behavior).
- Two distinct error conventions exist: service 400 `ExtendedHTTPException` (group flow) vs Pydantic `gt=0` 422 (single-category flow); the `pct` field doc on `CategoryBudgetSpecUpdate` says 0 removes a category, which conflicts with platform semantics.
- Speculative: the change may be expressible as a single additional check in `_validate_group_categories`, since it is the shared entry point for create and update.
- `create_project_budget_group` has no direct tests (codegraph), so end-to-end create rejection is untested.

---

## 7. Summary for Complexity Assessment

The multi-category Create/Update Budget popups map to the budget-group endpoints `POST /…/groups` and `PUT /…/groups/{group_id}` in `project_budget_router.py`. The API carries no per-category hard-limit field: each category's hard limit is `total_amount * pct / 100`, and `total_amount > 0` is already enforced, so a Platform hard limit of 0 is the same thing as `categories["platform"].pct == 0`. Create and update share one service validator, `ProjectBudgetService._validate_group_categories` in `project_budget_service.py`. It already requires the `platform` key and returns 400 for a bad pct, but it currently accepts a platform pct of 0. The read paths (`ProjectBudgetGroupResponse`, `CategoryBudgetDetailResponse`, `ProjectBudgetResponse`) put no bounds on the amounts and never call the validator, so existing platform-0 groups keep loading.

The touched surface is small: the service layer, possibly the schema layer, and tests. The pattern to follow already exists in the same validator (400 `ExtendedHTTPException` with the category key in the message). The older single-category rule is Pydantic `max_budget gt=0` (422), and `BudgetService._validate_constraints` covers global budgets. Nothing about this is technically new.

Tests already target the validator in `test_project_budget_service_group_creation.py`, but `test_platform_zero_passes_validation` asserts the behaviour this ticket forbids. `create_project_budget_group` has no direct tests. Key risks: on update, validation runs only when `categories` is present. Scalar and notification writes happen before validation, and only the absence of a commit keeps them from being saved. The pct=0 "remove category" semantics would no longer apply to platform.

---

## 8. External References

None named by the task.
