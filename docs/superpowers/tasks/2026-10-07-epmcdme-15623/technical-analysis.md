# Technical Research

**Task**: cost-center budget rollup aggregation
**Generated**: 2026-10-07T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Summary: Display a read-only cost-center budget rollup card with the combined total and platform, cli, and premium_models distribution across all linked projects.

Description: As a platform admin (maintainer) managing a cost center, I want to see its combined project-level budget in one card, so that I do not have to open and manually total every linked project.

Preconditions: The platform admin/maintainer can open a cost center's detail view. Projects may be linked to that cost center through `Application.cost_center_id`; those projects may have budget assignments in any of the three categories.

Scenarios of Use:
1. Open a cost center with budgeted projects and review its combined total and category distribution.
2. Reload the view after a linked project's budget changes and review the updated figures.
3. Open a cost center with no linked projects and see a clear zero/empty state.

Affected Areas: Cost-center budget aggregation and its read path; `CostCenterDetailsPage.tsx`; the existing project-level total-and-breakdown visual pattern.

Context:
- `CostCenter` already exists end-to-end as a project-grouping entity: the backend model in `codemie/src/codemie/core/models.py`, `Application.cost_center_id` linking many projects to one cost center, and an admin UI comprising `CostCentersManagementPage.tsx`, `CostCenterDetailsPage.tsx`, and `CostCenterProjectsManager.tsx`. `CostCenter` has no budget field. This story adds none: the rollup is computed from linked projects, not stored.
- At project level, "total budget + breakdown" is assembled from two pieces: `ProjectSpendingSummary` (current spend, budget limit, percent) and `ProjectAssignedBudgetSummary[]` (one row per category). They are produced in `codemie/src/codemie/rest_api/routers/projects.py` by `_aggregate_budget_window`, `_project_spending_summary`, and `_attach_assigned_budget_summaries`, and rendered as per-category cards by `ProjectBudgetCard.tsx` in `ProjectBudgetsSection.tsx`. "Reuse the card" means reuse the visual pattern, not necessarily lift a component unchanged.
- The budget categories are platform, cli, and premium_models (`BudgetCategory` in backend and frontend).
- No existing helper sums per-category budget amounts across multiple projects. The rollup needs logic to fetch linked projects using `application_repository.list_projects_by_cost_center_id` and sum their category amounts and overall total.
- This story covers only the cost-center-level aggregate card. The per-project drill-down underneath a cost center is a separate follow-up.

Acceptance criteria:
1. A shared, reusable helper (e.g. using `application_repository.list_projects_by_cost_center_id` and `project_budget_assignment_repository.get_assigned_budget_summaries_for_projects`) returns the platform, cli, premium_models, and overall budget sums for a cost center, independent of this card's rendering path. Two other planned stories (`2026-10-05-cost-center-project-drilldown`, `2026-10-05-cost-center-management-budgets-column`) must be able to reuse it rather than reimplementing summation.
2. A cost center with one or more linked projects with assigned budget data: opening its detail view shows the combined total budget and category breakdown summed across those projects.
3. A linked project's budget amount changes: reloading the cost-center detail view reflects updated totals, not stale values.
4. A cost center with no linked projects: the detail view shows a clear zero/empty state, not an error or broken card.
5. A linked project with no budget assigned in a category contributes zero to that category without preventing other categories from being summed.
6. The rollup card follows the existing project-level total-and-breakdown layout and styling closely enough to feel like the same component family.

Out of scope: a cost-center budget entity/record/field/persistence; the per-project drill-down list; changes to creation/editing/deletion of individual project budgets; editing or acting on rollup figures (read-only); cost-center/project linking permissions and the cost-center filter on project management.

Open questions resolved: shared component vs. matching pattern is at developer discretion (visual parity required); live vs. cached rollup is at developer discretion (acceptance criteria require updated totals on reload).

Find: the `CostCenter` model and `Application.cost_center_id` relationship; `application_repository.list_projects_by_cost_center_id` (user already has this symbol open in their editor at codemie/src/codemie/rest_api/routers/cost_centers.py:160 — note the symbol name may actually live in a repository module, confirm exact location); `project_budget_assignment_repository` and its budget-summary methods (`get_assigned_budget_summaries_for_projects` or similar); the `_aggregate_budget_window`, `_project_spending_summary`, `_attach_assigned_budget_summaries` functions in `projects.py` and the `ProjectSpendingSummary`/`ProjectAssignedBudgetSummary` schemas they produce; the `BudgetCategory` enum (backend and frontend); `CostCenterDetailsPage.tsx`, `CostCenterProjectsManager.tsx`, `ProjectBudgetCard.tsx`, `ProjectBudgetsSection.tsx`; existing cost_centers router (`codemie/src/codemie/rest_api/routers/cost_centers.py`) and its service/repository layers; any existing cost-center REST API test files and frontend test files for the above components, to establish conventions for a new rollup endpoint/component and its tests.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/core/models.py:509` — `CostCenter` SQLModel: `id, name, description, created_by, date, update_date, deleted_at`. No budget field, confirming the story's "no persisted rollup" framing.
- `src/codemie/core/models.py:381-396` — `Application` model carries `cost_center_id: Optional[uuid.UUID]` (FK to `cost_centers.id`, indexed). This is the only link between projects and cost centers.
- `src/codemie/repository/application_repository.py:755` — `list_projects_by_cost_center_id(session, cost_center_id) -> list[Application]`: sync, filters `cost_center_id` + `deleted_at IS NULL`, ordered by date desc/name asc. Already used by `get_cost_center_detail`.
- `src/codemie/repository/application_repository.py:765` — `count_active_projects_by_cost_center_id`, used for `project_count` on `CostCenterResponse` and the delete-guard in `CostCenterService.delete`.
- `src/codemie/repository/cost_center_repository.py:28` — `CostCenterRepository`: `get_by_id`, `get_active_by_id`, `get_by_name_case_insensitive`, `create`, `update`, `list_paginated`, `get_by_ids` (sync), `aget_by_ids` (async).
- `src/codemie/service/cost_center_service.py:34` — `CostCenterService`: feature-gate (`ensure_feature_enabled`, checks `customer_config.is_feature_enabled("costCenters")`), admin-gate (`ensure_admin`), `create`, `list_paginated`, `get_or_404`, `update`, `delete` (blocks delete when linked active projects exist), `ensure_exists_for_project`.
- `src/codemie/rest_api/routers/cost_centers.py` — router at prefix `/v1/admin/cost-centers`: `create_cost_center` (POST), `list_cost_centers` (GET), `get_cost_center_detail` (GET `/{costCenterId}`, builds `CostCenterDetailResponse` with `LinkedProjectResponse[]` via `list_projects_by_cost_center_id` + `get_project_member_counts_bulk`), `update_cost_center` (PATCH), `delete_cost_center` (DELETE). All gated by `_ensure_user_management_enabled` and `admin_access_only`.
- `src/codemie/repository/project_budget_repository.py:180` — `ProjectBudgetAssignmentRepository.get_assigned_budget_summaries_for_projects(session, project_names, budget_category=None) -> dict[str, list[ProjectAssignedBudgetSummaryRow]]`: async, batched across many project names in one query (latest-spend subqueries joined to `Budget`/`ProjectBudgetAssignment`). This is the exact batched-by-project-list shape acceptance criterion #1 points at.
- `src/codemie/repository/project_budget_repository.py:46` — `ProjectAssignedBudgetSummaryRow` dataclass: `project_name, budget_id, name, budget_category, soft_budget, max_budget, budget_duration, budget_reset_at, provider_sync_status, member_count, allocated_member_budget_total, current_spending`.
- `src/codemie/rest_api/routers/projects.py:69-90` — `ProjectSpendingSummary` (`current_spending, budget_limit, total_percent`) and `ProjectAssignedBudgetSummary` (`budget_id, name, budget_category: BudgetCategory, soft_budget, max_budget, budget_duration, budget_reset_at, provider_sync_status, member_count, allocated_member_budget_total, current_spending`) — Pydantic response models, single-project shaped.
- `src/codemie/rest_api/routers/projects.py:543` — `_aggregate_budget_window(assigned_budgets, rows, budgets_by_id)`: sums `max_budget` across a single project's assigned-budget rows (or falls back to budgets behind spend rows), returns `(total, earliest_reset)`. Operates per-project, not across projects — the cost-center rollup needs a new cross-project sum, this is not directly reusable as-is but is the closest existing analog.
- `src/codemie/rest_api/routers/projects.py:747` — `_project_spending_summary(key_row, budget_rows, budgets_map, assigned_budgets)`: builds one `ProjectSpendingSummary` for one project.
- `src/codemie/rest_api/routers/projects.py:668` — `_attach_assigned_budget_summaries(items, budget_category)`: calls `get_assigned_budget_summaries_for_projects` once for a batch of project names, groups results per project, builds `ProjectAssignedBudgetSummary[]` per item, skipping rows whose category fails `BudgetCategory(row.budget_category)` validation (logs a warning and continues — this is the existing convention for "unknown category tolerance").
- `src/codemie/service/budget/budget_enums.py:20` and `src/codemie/enterprise/litellm/budget_categories.py:20` — two independent `BudgetCategory(str, Enum)` definitions, both with identical members `PLATFORM="platform", CLI="cli", PREMIUM_MODELS="premium_models"`. The router/service-facing one used by `project_budget_repository` and `projects.py` is `codemie.service.budget.budget_enums.BudgetCategory`.

### Architecture and Layers Affected

- **Router layer**: `src/codemie/rest_api/routers/cost_centers.py` — the natural place for a new rollup endpoint (e.g. `GET /{costCenterId}/budget-rollup` or inlined into `get_cost_center_detail`), following its existing `admin_access_only` + `_ensure_user_management_enabled` + `get_session` pattern.
- **Service layer**: `src/codemie/service/cost_center_service.py` holds cost-center business rules (feature gate, admin gate, `get_or_404`); a rollup computation would likely live either here or as a new small module under `src/codemie/service/budget/` alongside the other budget-focused services.
- **Repository layer**: `application_repository.list_projects_by_cost_center_id` (sync) and `project_budget_assignment_repository.get_assigned_budget_summaries_for_projects` (async) are the two data-access calls acceptance criterion #1 names. One is sync (`Session`), the other async (`AsyncSession`) — the two existing cost-center router endpoints use sync `get_session()`, while `projects.py`'s budget attachment paths use `get_async_session()`. A rollup helper spanning both repos will need to reconcile this session-type split (note only — not a prescribed solution).
- **Frontend**: the ticket names `CostCenterDetailsPage.tsx`, `CostCenterProjectsManager.tsx`, `ProjectBudgetCard.tsx`, `ProjectBudgetsSection.tsx`. None of these files, nor any `.tsx` file, exist anywhere in this repository (confirmed by glob across the full tree). This repository (`codemie`) appears to be backend-only; the frontend lives in a separate, uncheckout repository not available to this research pass.

### Integration Points

- `Application.cost_center_id` → `cost_centers.id` FK is the sole linkage; no join table.
- `get_assigned_budget_summaries_for_projects` already batches across a `project_names: list[str]`, so feeding it the names from `list_projects_by_cost_center_id` is a direct, low-friction integration — no new repository method is strictly required to satisfy the batching shape acceptance criterion #1 implies, though a cost-center-level wrapper/helper function is still needed to do the actual summation and own the "empty state" shape.
- `customer_config.is_feature_enabled("costCenters")` and `config.ENABLE_USER_MANAGEMENT` are the two feature gates every existing cost-center endpoint checks first.

### Patterns and Conventions

- Router functions use `with get_session() as session:` (sync) for cost-center endpoints; budget-attachment code in `projects.py` uses `async with get_async_session() as async_session:`.
- `CostCenterService` methods are `@classmethod`s on a stateless class instantiated once as a module-level singleton (`cost_center_service = CostCenterService()`); repositories follow the same singleton-instance pattern (`cost_center_repository = CostCenterRepository()`, `application_repository`, `project_budget_assignment_repository` implied by call sites).
- Unknown/invalid `budget_category` values from a row are tolerated with a `logger.warning` + skip, not an exception (`_attach_assigned_budget_summaries`), matching AC #5's "contributes zero without preventing other categories" posture.
- Response models are plain Pydantic `BaseModel`s defined directly in the router module (not a separate schemas package), e.g. `CostCenterResponse`, `LinkedProjectResponse`, `ProjectAssignedBudgetSummary`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/layered-architecture.md`, `.ai-run/guides/architecture/service-layer-patterns.md`, `.ai-run/guides/data/repository-patterns.md`, `.ai-run/guides/api/rest-api-patterns.md` exist under `.ai-run/guides/` and are the project's primary AI guidance source per `AGENTS.md`, but were not opened in this pass beyond their listing (budget was spent on code exploration given the task is squarely a "where does X live / what exists" research task). The P0 guide for this task category (API / Database) should be consulted by the next stage before implementing.
- No dedicated guide specific to cost centers or budget rollups was found by filename.

### Architectural Decisions

- Inline code comments establish the only recorded decisions: `CostCenter` intentionally carries no budget field (model definition itself, corroborated by the ticket's "this story adds none"); `Budget.budget_type` distinguishes `global` (personal) vs `project` scope (`budget_models.py:59-81` docstring).

### Derived Conventions

- Cost-center and project-budget code paths are currently separate call graphs: `cost_centers.py` never imports from `projects.py` or `project_budget_repository.py`. A rollup helper is new wiring between two previously unconnected modules — the acceptance criterion's demand for a "shared, reusable helper" independent of this card's rendering path matches: there is no existing cross-project budget summation anywhere in the codebase to extend.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_cost_centers_router.py` — `TestCostCenterRouter` class; covers `test_create_cost_center`, `test_list_cost_centers`, `test_get_cost_center_detail`, and a `_super_admin` fixture helper. No budget-rollup assertions (none exist yet).
- `tests/codemie/service/test_cost_center_service.py` — unit tests for `CostCenterService`: name/description validation, feature-gate rejection for every method, logging-on-success/failure for create/update/delete, delete-blocked-when-linked-projects-exist.
- `tests/codemie/repository/test_project_budget_repository.py` — covers `get_assigned_budget_summaries_for_projects` grouping-by-project behavior directly (`test_get_assigned_budget_summaries_for_projects_groups_rows_by_project`), i.e. the exact repository method the new rollup helper would call.
- `tests/codemie/rest_api/routers/test_projects_router.py` — covers `_attach_assigned_budget_summaries` / `_attach_project_spending_summaries` behavior at the single-project level.
- No frontend test files exist in this repo (consistent with the absence of any `.tsx` source).

### Testing Framework and Patterns

- `pytest` with `unittest.mock.patch`/`MagicMock` for service-layer isolation (patching `cost_center_repository`, `application_repository`, `logger` as module-level attributes of the service module under test).
- Router tests appear to use a `_super_admin`-style fixture/helper to construct an authenticated admin `User` for `TestClient`-style calls (per `TestCostCenterRouter`).

### Coverage Gaps

- No test exercises cross-project budget summation — because no such code exists yet. Any new rollup helper and endpoint will need new tests at both the repository/service level (summation correctness, zero/empty state, missing-category tolerance) and the router level (feature-gate, admin-gate, 404 handling), following the two existing test files above as the template.
- No frontend tests can be assessed from this repository.

---

## 5. Configuration and Environment

### Environment Variables

- `ENABLE_USER_MANAGEMENT` (via `config.ENABLE_USER_MANAGEMENT`) — gates every cost-center endpoint.
- Cost-center name validation pattern: `config.COST_CENTER_NAME_PATTERN` (used by `CostCenterService.validate_name`).
- `BUDGET_STOP_ENABLED`, `INACTIVE_COST_CENTER_BUDGET_STOP_SCHEDULE` — unrelated scheduler config for the inactive-cost-center-budget-stop job, not relevant to a read-only rollup card but found adjacent in the budget/cost-center config surface.

### Configuration Files

- `src/codemie/configs/budget_config.py` — `BudgetConfig` loads `predefined_budgets` from `budgets-config.yaml` (via `config.BUDGETS_CONFIG_DIR`). Not directly relevant to a per-cost-center rollup (which is computed from live `Budget`/`ProjectBudgetAssignment` rows, not predefined config).

### Feature Flags and Deployment Concerns

- `customer_config.is_feature_enabled("costCenters")` is a customer-level feature flag gating the entire cost-center feature; the rollup card inherits this gate automatically if it's served from the same router/service.

---

## 6. Risk Indicators

- **Frontend repository is absent.** None of `CostCenterDetailsPage.tsx`, `CostCenterProjectsManager.tsx`, `ProjectBudgetCard.tsx`, `ProjectBudgetsSection.tsx`, or any `.tsx` file exist anywhere in this checked-out repository. The UI half of this story cannot be researched, planned in file-level detail, or implemented from here without access to the frontend repository — this is a research blocker, not a design question, and should be surfaced to the caller before planning proceeds on the UI side.
- **Sync/async session split.** `application_repository.list_projects_by_cost_center_id` is sync (`Session`/`get_session()`); `project_budget_assignment_repository.get_assigned_budget_summaries_for_projects` is async (`AsyncSession`/`get_async_session()`). A shared helper combining both must resolve this mismatch (e.g. by using an async equivalent of the project lookup, or wrapping sync work) — flagged as a technical joint, not a prescribed fix.
- **Two independent `BudgetCategory` enums** (`service.budget.budget_enums.BudgetCategory` and `enterprise.litellm.budget_categories.BudgetCategory`) exist with identical values; the implementer must use the same one `project_budget_repository`/`projects.py` already uses (`service.budget.budget_enums`) to avoid a subtle enum-identity mismatch.
- **No existing cross-project summation code.** `_aggregate_budget_window` sums across a single project's categories, not across projects' same category — there is no copy-adjust path, this is new logic end to end on the backend.
- **Reuse pressure from two sibling stories** (`2026-10-05-cost-center-project-drilldown`, `2026-10-05-cost-center-management-budgets-column`) that the helper must serve without rework — raises the cost of getting the helper's signature/shape wrong on the first pass.

---

## 7. Summary for Complexity Assessment

The backend side of this task is well-scoped and touches a small, already-coherent surface: the `cost_centers.py` router/service/repository trio for cost-center access and gating, plus the `project_budget_repository.get_assigned_budget_summaries_for_projects` batched-by-project-names repository method as the data source for summation. No new persistence is needed — `CostCenter` deliberately carries no budget field, and the project-level precedent (`_aggregate_budget_window`, `_project_spending_summary`, `ProjectAssignedBudgetSummary`) establishes the shape to mirror, scaled from one project to a list. The main backend novelty is a cross-project summation helper that doesn't exist in any form today, plus reconciling a sync (`list_projects_by_cost_center_id`) and async (`get_assigned_budget_summaries_for_projects`) repository call inside one helper, and ensuring the helper's return shape anticipates two sibling stories' reuse.

The frontend side cannot be assessed from this repository at all: none of the four named `.tsx` files, nor any TypeScript/React source, exist in this checkout. This is a hard research gap, not a complexity signal to score — the calling flow should confirm whether frontend work is in scope for this agent/run or handled via a separate repository/pipeline before sizing the UI portion of the story.

Test coverage for the existing cost-center and project-budget-summary code is solid at both router and repository/service level and gives a clear template (pytest + patch/MagicMock, `TestCostCenterRouter`-style fixtures) for the new rollup helper's own tests, which currently have zero coverage because the capability doesn't exist yet.

---

## 8. External References

None named by the task (the ticket names files by filename only — `CostCenterDetailsPage.tsx`, `CostCenterProjectsManager.tsx`, `ProjectBudgetCard.tsx`, `ProjectBudgetsSection.tsx` — not absolute paths; none resolved anywhere in this repository, see Risk Indicators).
