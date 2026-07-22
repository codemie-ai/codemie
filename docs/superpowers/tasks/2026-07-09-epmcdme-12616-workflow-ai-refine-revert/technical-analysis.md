# Technical Research

**Task**: workflow ai-refine revert audit-history backend
**Generated**: 2026-07-09T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-12616: Enhance Workflow Edit with 'Refine/Modify with AI' and 'Revert to Previous' Option (Backend only)

Description:
The story proposes enhancing workflow edit with:
- 'Refine/Modify with AI' option for applying AI suggestions to workflow optimization
- 'Revert to Previous' action to restore the last saved workflow version if AI changes are not satisfactory
- Clear availability of both actions in the edit UI
- Audit/history tracking for AI modifications and reverts

Acceptance Criteria:
- 'Refine/Modify with AI' button is added to workflow edit interface (frontend out of scope for this run)
- 'Revert to Previous' option is available after AI modification
- After revert, workflow is restored to last saved version; user can continue editing
- Action history is logged for all AI modifications and reverts
- No regression in workflow edit/save functionality
- Both options are accessible and validated for all workflow types

Scope: Backend only. Focus on:
1. API endpoint(s) for AI-based workflow refinement (accepts a workflow definition and returns AI-refined version)
2. API endpoint(s) for reverting a workflow to its last saved version
3. Audit/history logging for AI modifications and reverts
4. Any service-layer changes to support these operations

---

## 2. Codebase Findings

### Existing Implementations

**Workflow Router (API layer)**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/rest_api/routers/workflow.py`
  - Defines all workflow CRUD endpoints under `/v1` prefix
  - Existing endpoints: `GET /workflows`, `GET /workflows/id/{workflow_id}`, `POST /workflows`, `PUT /workflows/{workflow_id}`, `DELETE /workflows/{workflow_id}`, `POST /workflows/diagram`
  - No existing `refine` or `revert` endpoints
  - Router is registered in `src/codemie/rest_api/main.py:756` via `app.include_router(workflow.router)`

**WorkflowService (Service layer)**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/workflow_service.py`
  - Central orchestration class for workflow CRUD, execution management, and history tracking
  - Key method: `_update_workflow_history(workflow_config, new_history_entry)` — prepends a `YamlConfigHistory` entry to `yaml_config_history` using a raw PostgreSQL `text()` statement
  - Key method: `_update_workflow_values(stored_config, updated_config, user)` — captures current `yaml_config` into history before applying the update
  - Key method: `update_workflow(stored_config, updated_config, user)` — outer entry point that delegates to `_update_workflow_values`
  - No existing revert or AI-refine methods

**WorkflowConfig (DB Model)**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/core/workflow_models/workflow_config.py`
  - SQLModel table: `workflows`
  - `yaml_config: Optional[str]` — primary workflow definition (YAML string)
  - `yaml_config_history: List[YamlConfigHistory]` — JSONB array of prior `yaml_config` snapshots with `date` and `created_by`
  - `YamlConfigHistory` Pydantic model has fields: `yaml_config: str`, `date: datetime`, `created_by: Optional[UserEntity]`
  - No `change_type` or `action` field on `YamlConfigHistory` — cannot currently distinguish a regular save from an AI refine or a revert

**WorkflowConfigRepository**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/repository/workflow_config_repository.py`
  - Thin repository, currently only has `set_publish_state` and `recompute_unique_users_count`
  - No revert or history retrieval helpers

**AI Refine Patterns (Parallel: Assistant and Skill)**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/rest_api/routers/assistant.py:1738` — `POST /v1/assistants/refine` endpoint
  - Delegates to `AssistantGeneratorService.generate_refine_prompt()`
  - Receives draft fields, returns `RefineGeneratorResponse` (per-field LLM recommendations)
  - Uses `PromptGeneratorChain` (chains a `PromptTemplate` with a structured-output LLM via LangChain)
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/rest_api/routers/skill.py:770` — `POST /v1/skills/refine` endpoint
  - Delegates to `SkillGeneratorService.refine_skill_details()`
  - Same `PromptGeneratorChain` pattern, same `RefineGeneratorResponse` return type

**AssistantGeneratorService / PromptGeneratorChain (AI invocation engine)**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/assistant_generator_service.py`
  - `PromptGeneratorChain.from_prompt_template(template, request_id, llm_model)` — builds chain with LLM resolved via `get_llm_by_credentials()`
  - `PromptGeneratorChain.invoke_with_model(base_model, input)` — invokes chain with structured output
  - `generate_refine_prompt()` — callable AI refine entry point used by `assistants/refine`

**Assistant Rollback Pattern**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/assistant/assistant_version_service.py`
  - `AssistantVersionService.rollback_to_version()` — creates a new version from a target historical version; includes robust validation
  - Distinct "versioning" table approach vs. workflow's embedded JSONB history

**WorkflowMonitoringService (Observability)**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/monitoring/workflow_monitoring_service.py`
  - Provides: `send_create_workflow_metric`, `send_update_workflow_metric`, `send_delete_workflow_metric`
  - No `send_ai_refine_workflow_metric` or `send_revert_workflow_metric` yet
  - Extends `BaseMonitoringService.send_count_metric` with OTel metrics

### Architecture and Layers Affected

| Layer | Component | Change Type |
|---|---|---|
| API / Router | `src/codemie/rest_api/routers/workflow.py` | New endpoints added |
| Service / Business Logic | `src/codemie/service/workflow_service.py` | New methods for AI refine and revert |
| AI Chain (new) | New `WorkflowGeneratorService` or extension of `AssistantGeneratorService` | New prompt template + chain invocation |
| DB Model | `src/codemie/core/workflow_models/workflow_config.py` — `YamlConfigHistory` | Schema enhancement: add `change_type` field |
| DB Migration | `src/external/alembic/versions/` | New migration if `YamlConfigHistory` schema changes |
| Monitoring | `src/codemie/service/monitoring/workflow_monitoring_service.py` | New metric methods for AI refine and revert actions |
| Metrics Constants | `src/codemie/service/monitoring/metrics_constants.py` | New metric name constants |

### Integration Points

- **LLM resolution**: `get_llm_by_credentials(llm_model, request_id)` from `codemie.core.dependecies` — used by all existing AI generation services
- **LiteLLM context**: `set_llm_context(None, project, user)` must be called before LLM invocation (present in both `assistants/refine` and `skills/refine` endpoints)
- **Workflow validation**: `WorkflowExecutor.validate_workflow(workflow_config, user, error_format)` — the AI-refined YAML must be validated before being returned or saved
- **YAML parsing**: `WorkflowConfig.parse_execution_config()` / `WorkflowConfig.from_yaml()` — already handles YAML deserialization
- **Access control**: `Ability(user).can(Action.WRITE, workflow)` — existing check that must guard both new endpoints
- **Project access**: `project_access_check(user, request.project)` — called at the router level before service delegation
- **Guardrail assignments**: `GuardrailService` is called after save; revert must re-trigger or preserve guardrail state

### Patterns and Conventions

- **AI refine pattern**: `POST /<resource>/refine` endpoint → calls `<Resource>GeneratorService` → uses `PromptGeneratorChain.from_prompt_template` → `invoke_with_model(ResponseModel, input_dict)` → returns structured recommendations
- **Workflow YAML history**: `_update_workflow_history()` uses raw SQL `text()` with `||` operator to prepend history entries atomically
- **Monitoring**: Wrap service operations in try/except; send `send_count_metric` before re-raising; pass `success=True/False`
- **Error handling**: Raise `ExtendedHTTPException` with `code`, `message`, `details`, `help` fields; catch `ValidationException` separately
- **Background tasks**: `background_tasks.add_task(run_in_thread_pool, update_workflow_schema, ...)` used after workflow save
- **Request ID propagation**: `raw_request.state.uuid` is extracted and passed to all service calls for LLM metric correlation

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/workflows/langgraph-workflows.md` — Covers LangGraph executor; directs to extend `WorkflowExecutor` rather than create parallel paths
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/api/rest-api-patterns.md` — Router registration, error responses, auth dependencies
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/architecture/layered-architecture.md` — API → Service → Repository layering; shared exceptions
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/integration/llm-providers.md` — Use config-driven model resolution; gate LiteLLM with `is_litellm_enabled`
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/testing/testing-patterns.md` — Test location conventions, mocking external dependencies

### Architectural Decisions

- AI generation for assistant and skill domains follows an identical pattern: a stateless POST endpoint that accepts current field values and a `refine_prompt`, invokes `PromptGeneratorChain`, and returns recommendations without persisting them. The workflow AI refine should follow the same approach.
- The `YamlConfigHistory` mechanism is already in place and populated on every `update_workflow` call. The revert operation should restore `yaml_config` from the most recent history entry without introducing a separate version table.
- The `WorkflowConfig` model uses JSONB for `yaml_config_history`, and history entries are prepended using raw SQL to avoid race conditions (seen at `workflow_service.py:639-649`).

### Derived Conventions

- New endpoints in `workflow.py` should follow the `POST /workflows/{workflow_id}/refine` and `POST /workflows/{workflow_id}/revert` path structure (resource-scoped actions)
- All AI-related endpoint logic should call `set_llm_context` and extract `request_id = raw_request.state.uuid`
- Audit logging for AI modifications and reverts should be implemented as metrics via `WorkflowMonitoringService.send_count_metric`, consistent with `send_update_workflow_metric`; the `change_type` dimension should be added to metric attributes
- `YamlConfigHistory` should gain a `change_type: Optional[str]` field (`"manual"`, `"ai_refine"`, `"revert"`) — this is a non-breaking Pydantic change since the field is Optional; however, it is stored in JSONB, so no migration is strictly required for existing rows

---

## 4. Testing Landscape

### Existing Coverage

- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/rest_api/routers/test_workflow.py` — router-level tests for create, read, update, delete, diagram, prebuilt, and access-control cases; uses `AsyncClient` with mocked service calls
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/service/test_workflow_service.py` — service-level tests with mocked DB, `User` fixtures with project membership
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/core/workflow_models/test_workflow_config.py` — model validation tests
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/service/workflow_execution/test_workflow_execution_service.py` — execution service tests

### Testing Framework and Patterns

- **Framework**: pytest with `pytest-asyncio`; `AsyncClient` via `httpx` for async router tests, `TestClient` for sync endpoints
- **Auth mocking**: `Depends(authenticate)` is patched at the `codemie.rest_api.security.authentication` level; `User` fixture provides project membership
- **Service mocking**: `patch("codemie.service.workflow_service.WorkflowService.<method>")` or `patch.object`
- **DB isolation**: tests do not hit a real database; SQLModel operations are mocked via `patch`
- **Fixtures**: `user()`, `admin_user()`, `workflow_config` (in service tests), `create_workflow_request` / `update_workflow_request` (in router tests)

### Coverage Gaps

- No tests for `_update_workflow_history` or `yaml_config_history` contents — history tracking is never asserted in existing tests
- No tests for workflow AI refine (the feature does not yet exist)
- No tests for workflow revert (the feature does not yet exist)
- No tests validating that `change_type` is recorded on history entries
- No tests asserting that `WorkflowMonitoringService` sends the expected metric events for refine or revert

---

## 5. Configuration and Environment

### Environment Variables

- `WORKFLOW_TEMPLATES_DIR` — resolved at `src/codemie/configs/config.py:110` as `Path(...).parents[3] / "config/templates/workflow"` — not directly relevant to this task
- LLM model configuration: driven by `config/llms/llm-azure-config.yaml` and the `MODELS_ENV` environment variable (see README); model resolution via `llm_service.default_llm_model` and `get_llm_by_credentials()`
- `WORKFLOW_DEFAULT_CONCURRENCY`, `WORKFLOW_MAX_CONCURRENCY` — workflow execution limits; not relevant to the edit/refine flow

### Configuration Files

- `/Users/yevhen_slyva/codemie-dev/codemie/config/llms/llm-azure-config.yaml` — defines available LLM models; the AI refine endpoint will select from these
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/configs/config.py` — central config; the `llm_service.default_llm_model` value is derived here

### Feature Flags and Deployment Concerns

- No existing feature flag for workflow AI refine; not required if implementing as a new stateless endpoint
- No DB migration is strictly required if `change_type` is added as an Optional field on `YamlConfigHistory` (JSONB; existing rows will deserialize with `None`)
- If `change_type` is added as a non-null column with a DB default, a migration script in `src/external/alembic/versions/` is required following the pattern of `8f7d26d3ff1c_create_workflows.py`
- The AI refine endpoint is stateless (does not persist); no deployment concern beyond LLM availability

---

## 6. Risk Indicators

- **No existing AI service for workflows**: No `WorkflowGeneratorService` or workflow-specific refine prompt template exists. The implementation must create a new service or extend `AssistantGeneratorService`; a workflow YAML prompt template needs to be authored. This introduces a novel pattern compared to the rest of the codebase.
- **YamlConfigHistory has no `change_type` field**: Adding it as `Optional[str]` is a safe non-breaking Pydantic change for JSONB storage, but the field must be threaded through all history-creation call sites (`_update_workflow_values`). Missing this results in audit history without action labels.
- **Revert operates on a live JSONB prepend pattern**: `_update_workflow_history` uses a raw SQL `||` prepend. A revert must read `yaml_config_history[0]` (the most recent prior version) and call `update_workflow` with that YAML. Care is needed to avoid double-writing the same snapshot to history (revert triggers `update_workflow` which itself records the pre-revert state).
- **No tests for yaml_config_history**: The history tracking code path is completely untested. Any new work in this area must add tests to ensure correctness.
- **Validation of AI-refined YAML is mandatory**: The AI may produce invalid YAML. `WorkflowExecutor.validate_workflow()` must be called on the AI output before returning it to the client (or before persisting, if auto-apply is chosen). Failure modes are already defined via `WorkflowErrorFormat`.
- **LLM prompt design for workflow YAML is non-trivial**: Workflow YAML is significantly more complex than assistant fields. A prompt template that reliably returns valid, improved workflow YAML (rather than free-form text) requires careful design and structured-output enforcement via `invoke_with_model`.
- **Guardrail re-sync on revert**: Current `update_workflow` endpoint calls `GuardrailService.sync_guardrail_assignments_for_entity` after each update. The revert endpoint must decide whether guardrail assignments are preserved, reset, or re-synced from the reverted config.
- **Monitoring metric names not yet defined**: No `WORKFLOW_AI_REFINE_TOTAL_METRIC` or `WORKFLOW_REVERT_TOTAL_METRIC` constants exist in `metrics_constants.py`. These must be added to maintain observability parity with other workflow actions.
- **Circular import risk**: `workflow_service.py` already uses a lazy import of `WorkflowExecutor` to avoid circular dependency (`service/workflow_service.py:160`). Any new service referencing both `WorkflowService` and the AI chain must follow the same lazy-import pattern.

---

## 7. Summary for Complexity Assessment

This task spans the API, Service, and AI-Chain layers, touching four to six files for the core implementation: the workflow router (`workflow.py`), the workflow service (`workflow_service.py`), a new or extended AI generator service, a new workflow-specific prompt template, the `YamlConfigHistory` model (optional schema addition), and the monitoring service and constants. The revert feature is architecturally straightforward because the `yaml_config_history` JSONB column already stores prior states; the implementation is a new service method that reads the most recent history entry and re-applies it via the existing `update_workflow` path, plus a new router endpoint. The audit/history logging requirement is satisfied by adding an optional `change_type` dimension to `YamlConfigHistory` and corresponding monitoring metrics.

The AI refine feature introduces genuine novelty. There is an established `PromptGeneratorChain` pattern (used for assistants and skills) that can be reused, but no workflow-specific refine prompt template exists. Workflow YAML is more complex than assistant fields, so prompt engineering and structured-output design are non-trivial. The refine endpoint should be stateless (return recommended changes without persisting), consistent with the existing assistant and skill refine endpoints. The response model can mirror `RefineGeneratorResponse` or return a revised YAML string directly — the appropriate shape depends on whether the frontend intends to apply changes field-by-field or wholesale.

The affected area has reasonable test coverage for CRUD operations but no tests exist for `yaml_config_history` contents, the history tracking mechanism, or any AI-generation path. New tests are required for: the revert service method (asserting history is read and applied correctly), the AI refine endpoint (mocking `PromptGeneratorChain`), audit field correctness, and monitoring metric emission. The implementation complexity is moderate: the revert path is low-risk given the existing data model; the AI refine path carries higher complexity due to prompt design and LLM output validation requirements.
