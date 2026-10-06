# Technical Research

**Task**: workflow categories assistant filter
**Generated**: 2026-09-01T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-12906 (BACKEND SCOPE ONLY): Add optional Categories field to the Workflow model and API. Categories should be stored and retrieved with workflows. Add a categories filter to My Workflows and All Workflows list endpoints. The categories are the same as Assistant categories. Max 3 categories per workflow. This is backend only - no UI changes needed.

Acceptance Criteria (backend):
- Workflow model has optional categories field (0-3 categories)
- Workflow create/update endpoints accept categories
- Workflow retrieve endpoints return categories
- My Workflows and All Workflows list endpoints support filtering by categories
- Workflows without categories are excluded when category filter is applied, included when no filter

---

## 2. Codebase Findings

### Existing Implementations

Most of the plumbing for this feature already exists in the codebase. The following are all already present:

- `src/codemie/core/workflow_models/workflow_config.py` — `WorkflowConfigBase` (line 112): `categories: list[str] = SQLField(default_factory=list, sa_column=Column(JSONB))`, with a GIN index defined in `__table_args__` as `Index("ix_workflows_categories", "categories", postgresql_using="gin")`.
- `src/codemie/core/workflow_models/workflow_config.py` — `WorkflowConfigListResponse` (line 362): `categories: List[str] = Field(default_factory=list)` is included in the minimal list response model.
- `src/external/alembic/versions/k6l7m8n9o0p1_add_workflow_marketplace_columns.py` — migration that adds the `categories` JSONB column (server_default `'[]'::jsonb`) and GIN index to the `workflows` table. Already applied.
- `src/codemie/service/filter/filter_services.py` — `WorkflowFilter.FILTER_CONFIG` already has a `"categories"` entry using `compose_json_array_filter` and `SearchFields.CATEGORIES`.
- `src/codemie/service/workflow_config/workflow_config_index_service.py` — `_query_postgres` applies `WorkflowFilter.add_sql_filters`, so category filtering flows through this path automatically.
- `src/codemie/service/filter/compose_filter_functions.py` — `compose_json_array_filter` uses PostgreSQL `@>` operator: for a list of category IDs it produces `OR`-joined conditions; workflows with an empty `[]` array will not match any `@>` check, satisfying the "exclude when filter applied" AC.
- `src/codemie/rest_api/models/category.py` — `Category` SQLModel table (`categories`) with `id`, `name`, `description`. Shared with assistants.
- `src/codemie/service/assistant/category_service.py` — `DatabaseCategoryService` with `validate_category_ids`, `filter_valid_category_ids`, `enrich_categories`. Singleton `category_service`.
- `src/codemie/repository/category_repository.py` — `CategoryRepository.get_by_ids` used by marketplace service.
- `src/codemie/service/workflow_config/workflow_marketplace_service.py` — `_validate_categories` already validates category IDs against the DB for the marketplace publish path.

**What is missing** (the actual gap):

- `src/codemie/core/workflow_models/workflow_models.py` — `CreateWorkflowRequest` (line 409) has no `categories` field.
- `src/codemie/core/workflow_models/workflow_models.py` — `UpdateWorkflowRequest` (line 427) has no `categories` field.
- `src/codemie/service/workflow_service.py` — `WorkflowService._editable_non_boolean_fields` set (line 56) does not include `"categories"`. `_update_workflow_values` iterates this set to copy fields from `updated_workflow_config` to `stored_config`, so categories sent on update are silently dropped even if the request model were fixed.

### Architecture and Layers Affected

- **API request models** (`src/codemie/core/workflow_models/workflow_models.py`): `CreateWorkflowRequest` and `UpdateWorkflowRequest` need the `categories` field.
- **Service layer** (`src/codemie/service/workflow_service.py`): `_editable_non_boolean_fields` set drives what `_update_workflow_values` propagates; `"categories"` must be added.
- **DB model / response models**: Already have the field. No changes needed.
- **Filter layer**: Already wired. No changes needed.
- **Alembic**: No migration needed; column and index already exist.

### Integration Points

- `src/codemie/rest_api/routers/workflow.py` — `create_workflow` builds `WorkflowConfig(**request.model_dump())`; `update_workflow` builds `WorkflowConfig(**request.model_dump())` then calls `workflow_service.update_workflow`. Both paths pass through `model_dump()`, so the request model's `categories` field will propagate into `WorkflowConfig` automatically once added to the request models.
- `Category` table is shared between assistants and workflows. `category_service` and `CategoryRepository` are reusable.
- `WorkflowMarketplaceService._validate_categories` shows the pattern for ID validation against the DB.

### Patterns and Conventions

- **Assistants pattern**: `AssistantRequest.categories` (line 356): `list[str] = Field(default_factory=list, max_length=3)`. The max-3 constraint is enforced by Pydantic `max_length` on the list field. Validation against the Category table is done in `AssistantBase._check_categories` which calls `category_service.validate_category_ids`.
- **Update pattern**: `WorkflowService._editable_non_boolean_fields` controls which non-boolean fields are copied during `_update_workflow_values`. Adding `"categories"` to this set is sufficient for updates.
- **Filter pattern**: `WorkflowFilter.FILTER_CONFIG` + `BaseFilterData.add_sql_filters` + `compose_json_array_filter` — already in place.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/layered-architecture.md` — documents the request model → service → DB model layer separation.
- `.ai-run/guides/data/database-patterns.md` — SQLModel and session patterns.
- `.ai-run/guides/data/repository-patterns.md` — repositories own data access.

### Architectural Decisions

No recorded ADR specific to categories. The assistant categories implementation (migration `b0f3d91cff8b`, `category_service`, `AssistantRequest.categories`) is the established precedent for this domain.

### Derived Conventions

- Categories are stored as `list[str]` (UUIDs) in a JSONB column, not as a foreign-key join table.
- Max-3 enforcement lives at the Pydantic request model level (`max_length=3` on the Field), not in the DB.
- Category ID validation against the `categories` table is performed before save in the service layer.
- GIN index on the JSONB column supports containment (`@>`) queries efficiently.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/workflow_config/test_workflow_config_index_service.py` — tests for `WorkflowConfigIndexService.run` and `get_users` with various user/scope permutations. Tests inspect raw SQL string to assert WHERE clauses. No test for category filter.
- `tests/codemie/rest_api/routers/test_workflow.py` — router-level tests for create, update, delete, diagram endpoints.
- `tests/codemie/service/test_workflow_service.py` — unit tests for `WorkflowService`.
- `tests/codemie/core/workflow_models/test_workflow_config.py` — model-level tests.

### Testing Framework and Patterns

- pytest with `unittest.mock.patch` and `MagicMock`.
- Router tests use `fastapi.testclient.TestClient` and `httpx.AsyncClient`.
- Service tests mock the Session and assert on generated SQL strings directly.
- No fixtures or factories for workflow test data; objects constructed inline.

### Coverage Gaps

- No test covers categories being passed through `CreateWorkflowRequest` → `WorkflowConfig`.
- No test covers categories being updated via `UpdateWorkflowRequest` → `_update_workflow_values`.
- No test in `test_workflow_config_index_service.py` asserts that a `categories` filter produces the correct `@>` SQL predicate.
- No test for the max-3 validation on the request model.
- No test for invalid category ID rejection on workflow create/update.

---

## 5. Configuration and Environment

### Environment Variables

No env vars specific to workflow categories. The `Category` table is accessed via the standard PostgreSQL engine (`WorkflowConfig.get_engine()`), same as all other models.

### Configuration Files

No feature flags or config changes needed. The `WORKFLOW_GENERATION_ENABLED` flag in `src/codemie/configs/config.py` is unrelated.

### Feature Flags and Deployment Concerns

No deployment concerns. The DB column already exists. No migration needed.

---

## 6. Risk Indicators

- **Silent data loss on update is the primary risk**: `_update_workflow_values` iterates `_editable_non_boolean_fields`; if `"categories"` is not added to that set, any categories submitted on a PUT request are silently discarded. The fix is one word in a set literal, but the failure mode is invisible to callers.
- **max_length=3 is Pydantic-only**: The DB column has no CHECK constraint enforcing ≤3 entries. Callers bypassing the API (direct DB writes, internal services) could store more. Acceptable given existing assistant precedent.
- **Category ID validation decision**: The marketplace publish path validates category IDs (`_validate_categories`). The assistant path validates via `category_service.validate_category_ids`. Whether the create/update path should also validate is unspecified in the task. Skipping validation means bad IDs are stored silently; adding it adds a DB round-trip per save. The assistant pattern validates.
- **Speculative: `_editable_non_boolean_fields` is a set of strings** — adding `"categories"` makes `_update_workflow_values` copy the value via `setattr`, but only when `new_value is not None`. Since `categories` defaults to `[]`, an explicit empty list on an update would not be applied (the `if new_value is not None` guard passes, but `[]` is falsy). This may require the guard to be `if new_value is not None` rather than `if new_value` — the current code uses `if new_value is not None` (line 666), so empty list updates will be applied correctly.
- **No test for the SQL predicate shape**: `test_workflow_config_index_service.py` asserts exact SQL strings; adding a categories filter test that checks for `@>` in the WHERE clause would catch regressions.

---

## 7. Summary for Complexity Assessment

The task is substantially pre-implemented. The `categories` column, GIN index, DB migration, list-response model field, and filter wiring are all already present. The full retrieve path (single workflow and list) already returns `categories`. The filter already works end-to-end in `WorkflowConfigIndexService` via `WorkflowFilter`. The `Category` table, `category_service`, and `CategoryRepository` are shared with assistants and need no changes.

The actual code delta is two changes: (1) add `categories: list[str] = Field(default_factory=list, max_length=3)` to `CreateWorkflowRequest` and `UpdateWorkflowRequest` in `src/codemie/core/workflow_models/workflow_models.py`, and (2) add `"categories"` to `WorkflowService._editable_non_boolean_fields` in `src/codemie/service/workflow_service.py`. Optionally, add category ID validation in the create/update path following the assistant pattern. No migration, no new files, no new infrastructure.

Technical novelty is near zero — the pattern is established by the assistant categories feature. Test coverage of the new paths is absent, so adding tests for the SQL filter predicate, the max-3 Pydantic constraint, and the update propagation is the main new test work. Overall complexity is low: one set entry, two field additions, and optional one-liner validation call.

---

## 8. External References

None named by the task.
