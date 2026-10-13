# Technical Research

**Task**: rest-api routers models pydantic openapi (EPMCDME-15651, delivery step 1 only)
**Generated**: 2026-10-08
**Research path**: codegraph

---

## 1. Original Context

Implement delivery step 1 of EPMCDME-15651 (models move): move request/response Pydantic classes out of src/codemie/rest_api/routers into src/codemie/rest_api/models/ (about 70 classes, about 15 files), removing the services' imports from budget_router / project_budget_router and the handler's import of ToolCallResumeRequest from routers/assistant.py. Regenerate the OpenAPI snapshot exactly once; the reviewed diff must be limited to the 4 same-named PaginationInfo component renames (budget_router, projects, cost_centers, models/user_management). Endpoint names, paths, response models and operationIds stay identical; this is a move-only step (reviewable with git diff --color-moved). Guard tests already exist in tests/codemie/rest_api/router_guards/ (OpenAPI snapshot + route precedence). Requirements: the verbatim ticket body is docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/ticket.md (read it in full) and the user-resolved criteria are in docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/spec.md. A whole-ticket analysis already exists at docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/technical-analysis.md (Sections 2, 6, 7): build on it, do not redo whole-ticket research; focus on step 1 only.
Step-1-specific questions to answer with evidence (path:line): (1) exact inventory of Pydantic request/response/schema classes defined in each router module under src/codemie/rest_api/routers (class, file, line, whether used outside its router), and which of them step 1 should move (all of them, or only those the ticket names); (2) target file in rest_api/models/ for each group, including naming-collision risks with existing models files and the 4 PaginationInfo classes (which are identical, which differ, how their OpenAPI component names change on move and what that implies for response_model references); (3) every importer of those classes (src and tests) that must be re-pointed, including services/budget/budget_service.py, project_budget_service.py, rest_api/handlers/assistant_handlers.py (ToolCallResumeRequest), and every test that imports or patches them (tests/unit/routers, test_xwiki_index_endpoints.py, test_main_retrieval_backend_disabled.py, tests/codemie/service/budget/*); (4) helper constants/validators/enums those classes drag along (e.g. CategoryBudgetSpec, _DURATION_PATTERN) and import-cycle risks when the classes land in rest_api/models/; (5) how the OpenAPI snapshot guard is regenerated (tests/codemie/rest_api/router_guards/regenerate_baselines.py) and how the route-precedence baseline is affected (it should not change).
feature_area: rest-api routers models pydantic openapi
run_dir: docs/superpowers/tasks/2026-10-08-epmcdme-15651-step1-models-move

---

## 2. Codebase Findings

Tools: Read, Glob, codegraph only (no grep, nothing executed). Router heads (imports) were read for every module; full bodies for about 20. Subclass-style classes in unread tails of head-only modules are not ruled out.

### Existing Implementations

Classes defined in `src/codemie/rest_api/routers/` (95 in 18 of 62 modules; none in the other 44).

| Module | Count | Classes (line) |
|---|---|---|
| `budget_router.py` | 7 | BudgetCreateRequest 45, BudgetUpdateRequest 72, BudgetResponse 86, PaginationInfo 114, PaginatedBudgetListResponse 120, BudgetSyncResult 125, BudgetAssignmentBackfillResult 138 |
| `project_budget_router.py` | 15 | ProjectBudgetCreateRequest 60, ProjectBudgetUpdateRequest 81, ProjectBudgetMemberAllocationResponse 90, ProjectBudgetResponse 101, PaginatedProjectBudgetListResponse 127, RebalanceProjectBudgetRequest 134, OverrideMemberAllocationRequest 138, ProjectBudgetMembersResponse 144, CategoryBudgetSpec 525, ProjectBudgetGroupCreateRequest 534, CategoryBudgetSpecUpdate 557, ProjectBudgetGroupUpdateRequest 565, CategoryBudgetDetailResponse 576, ProjectBudgetGroupResponse 591, PaginatedProjectBudgetGroupListResponse 610 |
| `projects.py` | 31 | ProjectCounters 59 through CsvImportValidationResponse 395 (one block, L59-398; includes PaginationInfo 180 and ProjectUpdateRequest 266 with a `model_validator`) |
| `cost_centers.py` | 7 | PaginationInfo 40, CostCenterCreateRequest 46, CostCenterUpdateRequest 51, LinkedProjectResponse 55, CostCenterResponse 68, CostCenterDetailResponse 77, PaginatedCostCenterListResponse 81 |
| `assistant.py` | 7 | BuiltinSubagentListItem 123, ReactionRequest 128, CategoriesRequest 134, PublishToMarketplaceRequest 140, SystemPromptValidationRequest 158, SystemPromptValidationResponse 166, ToolCallResumeRequest 1143 |
| `index.py` | 2 | CreateIndexRequest 457 (`CronExpressionValidatorMixin, BaseGitRepo`), CreateSVNIndexRequest 468 |
| `analytics.py` | 4 | AnalyticsFilterParams 445, AnalyticsQueryParams 463, CLIEnrichedUserBaseParams 2017, CLIEnrichedUserParams 2021 (`Depends()` holders with `Query()` defaults; `config.ANALYTICS_DEFAULT_PAGE_SIZE` read at import) |
| `conversation.py` | 0 | no pydantic import |
| 11 other modules | 22 | ai_kata ReactionRequest 53, KataFilters 59; skill ReactionRequest 68; workflow MaterializeRequest 404, MaterializeSeedResponse 408, WorkflowSaveResponse 484, ValidateWorkflowResponse 490; user_management_router MessageResponse 66, AdminPasswordChangeRequest 72, UserBudgetAssignRequest 78, UserBudgetAssignmentResponse 87, UserBudgetResetRequest 96, BulkUserBudgetSetRequest 106; local_auth_router MessageResponse 54, TokenResponse 60; user_profile_router UserProfileUpdateRequest 37; share ShareConversationRequest 29; conversation_analysis TriggerAnalysisRequest 32; sharepoint_oauth InitiatePKCERequest 51; oauth_router_factory InitiateOAuthRequest 56, ConnectOAuthRequest 67; gitlab_oauth InitiateGitLabOAuthRequest 35 |

The 7 modules named by the ticket (budget, project_budget, projects, cost_centers, assistant, index, analytics) hold 73 classes, matching the brief's "about 70". Used outside their own router in src: only the 9 named in ticket clarification 3 (4 from budget_router, 4 from project_budget_router, ToolCallResumeRequest). All others are router-local in src (codegraph: ProjectListItem and ProjectDetailResponse have callers only in `projects.py`).

Helpers that travel with the classes: `_DURATION_PATTERN = r"^\d+[smhd]$"` (`project_budget_router.py:46`) is used only by 4 class fields (L74, L86, L538, L568). `_ACCESS_DENIED_*` (L47-48) serve helpers, not classes. `projects.py` `_WIDGET_COLUMNS` (L132-157) instantiates `SpendingWidgetColumn` at import. `BudgetCreateRequest`/`BudgetUpdateRequest` use the literal `r"^\d+[dhm]$"`. Enums: `BudgetCategory` and `AllocationMode` from `service/budget/budget_enums.py` (a second `BudgetCategory` exists at `enterprise/litellm/budget_categories.py:20`; `user_management_router` and two budget tests import that one). `EmailStr` on budget and group requests. `_ensure_allowed_budget_group_update_fields` reads `ProjectBudgetGroupUpdateRequest.model_fields` at runtime (L252).

### Architecture and Layers Affected

API routers, request/response models (`rest_api/models/`: 55 flat files plus `usage/` and `share/`; none named project, budget, project_budget or cost_center), services (`service/budget`), handlers (`rest_api/handlers`), guard tests.

### Integration Points

- `service/budget/budget_service.py`: TYPE_CHECKING import of 4 classes L51-56; function-local runtime imports of `BudgetSyncResult` L664 and `BudgetAssignmentBackfillResult` L850.
- `service/budget/project_budget_service.py`: TYPE_CHECKING import of 4 classes L59-65 (7 references to `ProjectBudgetGroupUpdateRequest`; lazy imports not ruled out).
- `rest_api/handlers/assistant_handlers.py` L26-27: TYPE_CHECKING `ToolCallResumeRequest`. The same file already imports `Assistant, AssistantType` from `rest_api.models.assistant` at runtime (L55) and `ToolCallAction` from `core.models` (L48).
- `ide.py` L30-31, `assistant_mapping.py` L31, `assistant_prompt_variable_mapping.py` L28 import router handlers and helpers, not classes (step 2 scope).

### Patterns and Conventions

- pydantic names OpenAPI components by class name; on a same-name clash it uses the module-qualified name `codemie__rest_api__<module>__<Class>`, so a moved clashing class changes its component key.
- Routers with `from __future__ import annotations`: budget_router, project_budget_router, projects, cost_centers.
- Precedent for models importing services: `rest_api/models/index.py` L36-51 imports `service.constants`, `llm_service`, `guardrail_service`, `scheduler_settings_service`. `rest_api/__init__.py` is empty; `rest_api/models/__init__.py` imports only `user_kata_progress`. `service/budget/__init__.py` imports only `budget_models`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/architecture/project-structure.md` read (package map only). Layering, REST and testing guides are summarized in the whole-ticket analysis, Section 3. The ticket names `rest_api/models/project.py` and `rest_api/models/project_budget.py`; for budget_router it says "do the same" without a file name.

### Architectural Decisions

Spec criterion 2: OpenAPI stays identical except that step 1 may regenerate the snapshot once, with the reviewed diff limited to the 4 `PaginationInfo` renames. Criterion 5: step 1 removes the service and handler imports of router classes.

### Derived Conventions

`CreateIndexRequest` docstring (`index.py:458-462`): "Solves circular import issue within the BaseGitRepo file."

---

## 4. Testing Landscape

### Existing Coverage

Importers of the classes (verified by reading imports):
- `tests/codemie/rest_api/routers/test_budget_router.py` L36-44 (BudgetCreateRequest, BudgetUpdateRequest); `test_budget_router_additional.py` L25-30 (BudgetAssignmentBackfillResult).
- `tests/codemie/service/budget/test_budget_service_activity.py` L64, L79 (function-local BudgetCreateRequest, BudgetUpdateRequest). The other `tests/codemie/service/budget/*` files read use `SimpleNamespace` and have no router imports.
- `test_project_budget_router.py` L32-43 (OverrideMemberAllocationRequest, ProjectBudgetResponse); `test_project_budget_router_additional.py` L24-30 (RebalanceProjectBudgetRequest); `test_project_budget_router_schema.py` L17-20 (CategoryBudgetSpec, CategoryBudgetSpecUpdate).
- `test_projects_router.py` L29-53 imports 9 class names (ProjectAssignmentRequest, ProjectAssignmentUpdateRequest, ProjectCreateRequest, ProjectCreateResponse, ProjectDetailResponse, ProjectListItem, ProjectUpdateRequest, PaginatedProjectListResponse, PaginatedSpendsResponse); the file has 3,868 lines and later function-local imports were not read.
- `test_cost_centers_router.py` L20-25 (CostCenterCreateRequest).
- `test_tool_call_resume.py` L79, L121 (function-local ToolCallResumeRequest from `routers.assistant`).
- No class import: `tests/unit/routers/test_assistant_file_attachment.py` (helper function), `test_xwiki_index_endpoints.py` (models.index classes and `routers.index.router`), `test_main_retrieval_backend_disabled.py` (reads `index.router.routes`). `tests/unit/routers/test_analytics_enriched_user.py` L32-38 imports `CLIEnrichedUserParams`, so it matters only if analytics param classes move.
- Not read: ai_kata, skill, workflow, user_management, local_auth, oauth, share, conversation_analysis and assistant router tests.

### Testing Framework and Patterns

pytest; `mock.patch` strings target `codemie.rest_api.routers.<module>.<name>`. Guards: `router_guards/test_openapi_snapshot.py` L31-32 (byte-exact `canonical_json` compare, `baseline.py` L36-75) and `test_route_precedence.py`.

### Coverage Gaps

No test asserts which module holds a class. The importer list is not exhaustive (no grep).

---

## 5. Configuration and Environment

### Environment Variables

Guard profile `PROFILE_ENV` (`guarded_app.py` L52-69): user management on, local IdP, Elasticsearch, LLM proxy plus budget check, workflow generation, MCP auth off, `APP_VERSION` pinned, `ANALYTICS_DEFAULT_PAGE_SIZE=20`.

### Configuration Files

- Snapshot: `tests/codemie/rest_api/router_guards/baselines/openapi.json` (75,073 lines; components L1-34,741) and `route_precedence.json` (array of `{earlier, later, method}`).
- Baseline PaginationInfo components: `codemie__rest_api__models__user_management__PaginationInfo` L34522 (description "Pagination metadata (unchanged from Phase 1)"), `...routers__budget_router__...` L34633 (no description), `...routers__cost_centers__...` L34656 (no description, identical to budget), `...routers__projects__...` L34679 (description "Pagination metadata for list responses"). Other router-defined module-qualified components: ai_kata ReactionRequest L34556, assistant PublishToMarketplaceRequest L34574, assistant ReactionRequest L34615, skill ReactionRequest L34703.
- Regeneration: `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines` from the repo root (`regenerate_baselines.py` L20). It writes BOTH baselines (L46-47) from `load_guarded_app_dump()`, which runs `app_probe.py` in a scrubbed-env subprocess (timeout 180 s, `guarded_app.py` L46), and prints path and pair counts. Route pairs come from `app.routes`, which a class move does not alter.
- `pyproject.toml`: ruff select E,F,B,N,C4,G,T20,RSE,SIM,C,W,PERF,ISC; no type-checking rules; max-complexity 16; FastAPI ^0.133.

### Feature Flags and Deployment Concerns

None. The guard profile fixes the flags. New `.py` files need the Apache header.

---

## 6. Risk Indicators

- Speculative: moving the ai_kata, assistant and skill `ReactionRequest` classes or assistant `PublishToMarketplaceRequest` renames 4 more components (baseline L34556-34703), breaking the "PaginationInfo only" diff.
- Speculative: a move-only step changes 3 PaginationInfo keys (budget_router, cost_centers, projects) and the `$ref` lines of 4 container schemas; the `models/user_management` key stays, so the spec's "4 renames" is likely 3.
- Speculative: the 4 analytics param classes are `Depends()` holders, not schema; moving them forces test re-pointing for no step-1 gain.
- Speculative: routers keep importing moved classes, so un-migrated test imports and patches resolve silently.
- Speculative: `from __future__ import annotations` routers need runtime (not TYPE_CHECKING) imports of moved classes.
- Name clashes: three distinct `PaginationInfo`; two `MessageResponse`; two `BudgetCategory` enums (service vs enterprise).
- `CreateIndexRequest` docstring cites a circular import.
- Inventory uses head-only reads for about 40 modules and no grep; importer list not exhaustive.
- ruff F401 is enforced.

---

## 7. Summary for Complexity Assessment

Step 1 touches routers, a new models layer, two budget services, one handler and about 15 test files. 61 classes sit in the 5 router files the ticket and spec touch (budget 7, project_budget 15, projects 31, cost_centers 7, ToolCallResumeRequest); the 7 ticket-named modules hold 73; all routers hold 95.

Novelty is low: verbatim moves, no import cycle seen (models already import services). The sharp edge is the OpenAPI contract: pydantic keys same-named classes by module path, and four router classes besides PaginationInfo already carry such keys.

Both guards exist; regeneration is one command and leaves route precedence unchanged. Open risks: the rename count and an inexhaustive importer list.

---

## 8. External References

Resolved, read in full: `docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/ticket.md` and `spec.md`; whole-ticket `technical-analysis.md` read for Sections 2, 6, 7. Facts used: services import 4+4 request/result classes (clarification 3); AC5 bans service and handler imports of routers; spec allows one snapshot regeneration with a diff limited to PaginationInfo renames; guard baseline commit `e55bea3005`.
