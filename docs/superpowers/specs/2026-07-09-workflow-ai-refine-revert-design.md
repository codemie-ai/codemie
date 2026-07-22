# EPMCDME-12616: Workflow AI Refine and Revert — Backend Design

**Ticket:** EPMCDME-12616  
**Branch:** EPMCDME-12616_enhance-workflow-edit-ai-refine-revert  
**Scope:** Backend only  
**Date:** 2026-07-09

---

## Overview

Add two new endpoints to the workflow API:

1. `POST /v1/workflows/{workflow_id}/refine` — stateless AI-powered suggestion: accepts the current `yaml_config` and an optional natural-language instruction, returns a revised `yaml_config` without persisting it.
2. `POST /v1/workflows/{workflow_id}/revert` — restores the workflow's `yaml_config` to the most recent snapshot in `yaml_config_history`.

Both endpoints follow the established patterns used by the assistant and skill domains.

---

## API Contracts

### POST /v1/workflows/{workflow_id}/refine

**Request body:**
```json
{
  "yaml_config": "<current workflow YAML string>",
  "refine_prompt": "Make the retry logic more resilient",  // optional
  "llm_model": "gpt-4o",                                   // optional, defaults to default model
  "project": "my-project"                                  // for auth context
}
```

**Response 200:**
```json
{
  "yaml_config": "<revised workflow YAML string>"
}
```

**Error responses:**
- `400` — LLM returned invalid YAML (YAML parse failed or structural validation failed)
- `403` — User lacks WRITE permission on the workflow
- `404` — Workflow not found

The endpoint loads the workflow to perform the access check, then passes the request body's `yaml_config` to the AI service. Nothing is written to the database.

### POST /v1/workflows/{workflow_id}/revert

**Request body:**
```json
{
  "project": "my-project"
}
```

**Response 200:** `BaseResponseWithData` — same envelope as `PUT /workflows/{id}`:
```json
{ "message": "Workflow reverted successfully", "data": { /* full WorkflowConfig */ } }
```

**Error responses:**
- `400` — `yaml_config_history` is empty; no prior version exists
- `403` — User lacks WRITE permission on the workflow
- `404` — Workflow not found

The revert writes to the database: it restores `yaml_config` from `history[0]` and records the pre-revert state back into history with `change_type="revert"`. Guardrail assignments are re-synced (via the existing `update_workflow` path).

---

## File Changes

### New files

| File | Purpose |
|---|---|
| `src/codemie/service/workflow_generator_service.py` | AI chain logic for workflow refine |
| `src/codemie/rest_api/models/workflow_generator.py` | Request/response Pydantic models |
| `src/codemie/templates/agents/workflow_generator_prompt.py` | Prompt template + `WorkflowRefineDetails` structured-output model |
| `tests/codemie/service/test_workflow_generator_service.py` | Unit tests for `WorkflowGeneratorService` |

### Modified files

| File | Change |
|---|---|
| `src/codemie/core/workflow_models/workflow_config.py` | Add `change_type: Optional[str] = None` to `YamlConfigHistory` |
| `src/codemie/service/workflow_service.py` | Add `revert_workflow()`; add `change_type` param to `_update_workflow_values` |
| `src/codemie/rest_api/routers/workflow.py` | Two new endpoints; import `WorkflowGeneratorService` and new models |
| `src/codemie/service/monitoring/workflow_monitoring_service.py` | `send_workflow_ai_refine_metric()`, `send_workflow_revert_metric()` |
| `src/codemie/service/monitoring/metrics_constants.py` | `WORKFLOW_AI_REFINE_TOTAL_METRIC`, `WORKFLOW_REVERT_TOTAL_METRIC` |
| `tests/codemie/rest_api/routers/test_workflow.py` | Router tests for refine and revert |
| `tests/codemie/service/test_workflow_service.py` | Service tests for revert and `change_type` recording |

---

## Service Layer

### WorkflowGeneratorService

```python
class WorkflowGeneratorService:
    @classmethod
    def refine_workflow(
        cls,
        yaml_config: str,
        refine_prompt: Optional[str],
        user: User,
        llm_model: Optional[str],
        request_id: str,
    ) -> WorkflowRefineResponse:
```

Flow:
1. Build `PromptGeneratorChain.from_prompt_template(WORKFLOW_REFINE_TEMPLATE, request_id, llm_model)`.
2. Call `chain.invoke_with_model(WorkflowRefineDetails, {"yaml_config": yaml_config, "refine_prompt": refine_prompt or ""})`.
3. Validate the returned YAML string (YAML parse; raise `ExtendedHTTPException(400)` on parse failure).
4. Emit `WORKFLOW_AI_REFINE_TOTAL_METRIC` via `emit_llm_token_metric`.
5. Return `WorkflowRefineResponse(yaml_config=result.yaml_config)`.

Error path: catch all exceptions, emit error metric, raise `ExtendedHTTPException(500)`.

### WorkflowService.revert_workflow (new method)

```python
def revert_workflow(self, stored_config: WorkflowConfig, user: User) -> WorkflowConfig:
```

Flow:
1. Check `stored_config.yaml_config_history` is non-empty; raise `ExtendedHTTPException(400, "No history available")` if empty.
2. Read `previous = stored_config.yaml_config_history[0]`.
3. Build a transient `WorkflowConfig` with `yaml_config = previous.yaml_config` (all other fields from `stored_config`).
4. Call `self.update_workflow(stored_config, reverted_config, user, change_type="revert")`.
5. Return the updated `stored_config`.

### _update_workflow_values change_type threading

`_update_workflow_values(stored_config, updated_config, user, change_type=None)` gains a `change_type` keyword argument. The `YamlConfigHistory` entry it creates carries this value:

```python
new_history_entry = YamlConfigHistory(
    yaml_config=stored_config.yaml_config,
    date=datetime.now(),
    created_by=user.as_user_model(),
    change_type=change_type,
)
```

Existing callers pass no argument → `change_type=None`. Only the revert path passes `change_type="revert"`.

---

## Data Model Change

### YamlConfigHistory (workflow_config.py)

```python
class YamlConfigHistory(BaseModel):
    yaml_config: str
    date: datetime
    created_by: Optional[UserEntity] = None
    change_type: Optional[str] = None  # "revert" | None (legacy/manual saves)
```

`change_type` is stored in the `yaml_config_history` JSONB column. No Alembic migration is required — existing rows deserialize with `change_type=None`.

---

## Prompt Template

**File:** `src/codemie/templates/agents/workflow_generator_prompt.py`

Contains:
- `WORKFLOW_REFINE_TEMPLATE: PromptTemplate` — instructs the LLM to return an improved YAML config. Input variables: `yaml_config`, `refine_prompt`.
- `WorkflowRefineDetails(BaseModel)` — single field `yaml_config: str` used as the structured output type with `invoke_with_model`.

The prompt instructs the LLM to: preserve the overall workflow structure, apply only the changes described in `refine_prompt` (or general improvements if empty), return the complete valid YAML, and not include any commentary or markdown fencing.

---

## Request/Response Models

**File:** `src/codemie/rest_api/models/workflow_generator.py`

```python
class WorkflowRefineRequest(BaseModel):
    yaml_config: str
    refine_prompt: Optional[str] = None
    llm_model: Optional[str] = None
    project: Optional[str] = None

class WorkflowRefineResponse(BaseModel):
    yaml_config: str

class WorkflowRevertRequest(BaseModel):
    project: Optional[str] = None
```

The revert response uses `BaseResponseWithData` — the same envelope as `PUT /workflows/{id}`:
`{ "message": "Workflow reverted successfully", "data": <WorkflowConfig> }`

---

## Monitoring

### New metric constants (metrics_constants.py)

```python
WORKFLOW_AI_REFINE_TOTAL_METRIC = "codemie_workflow_ai_refine_total"
WORKFLOW_REVERT_TOTAL_METRIC = "codemie_workflow_revert_total"
```

### New methods (workflow_monitoring_service.py)

```python
@classmethod
def send_workflow_ai_refine_metric(
    cls, user_id, user_name, workflow_id, workflow_name, project,
    success, llm_model=None, mode=WorkflowMode.SEQUENTIAL,
    additional_attributes=None
):
    attributes = cls._build_workflow_attributes(project, success, user_id, user_name, workflow_id, workflow_name, mode)
    if llm_model:
        attributes[MetricsAttributes.LLM_MODEL] = llm_model
    if additional_attributes:
        attributes.update(additional_attributes)
    cls.send_count_metric(name=WORKFLOW_AI_REFINE_TOTAL_METRIC, attributes=attributes)

@classmethod
def send_workflow_revert_metric(
    cls, user_id, user_name, workflow_id, workflow_name, project,
    success, mode=WorkflowMode.SEQUENTIAL, additional_attributes=None
):
    attributes = cls._build_workflow_attributes(project, success, user_id, user_name, workflow_id, workflow_name, mode)
    if additional_attributes:
        attributes.update(additional_attributes)
    cls.send_count_metric(name=WORKFLOW_REVERT_TOTAL_METRIC, attributes=attributes)
```

---

## Error Handling

| Scenario | HTTP status | Where raised |
|---|---|---|
| Workflow not found | 404 | Router (existing `raise_not_found` helper) |
| User lacks WRITE permission | 403 | Router (existing `raise_access_denied`) |
| `yaml_config_history` empty on revert | 400 | `WorkflowService.revert_workflow` |
| LLM returns invalid YAML | 400 | `WorkflowGeneratorService.refine_workflow` |
| LLM unavailable / chain error | 500 | `WorkflowGeneratorService.refine_workflow` |

All `ExtendedHTTPException` instances carry `code`, `message`, `details`, and `help` fields per the existing convention.

---

## Testing

### Router tests (test_workflow.py additions)

- `test_refine_workflow_success` — mocks `WorkflowGeneratorService.refine_workflow`, asserts 200 + `yaml_config`
- `test_refine_workflow_access_denied` — asserts 403 when user lacks WRITE
- `test_refine_workflow_workflow_not_found` — asserts 404
- `test_revert_workflow_success` — mocks `WorkflowService.revert_workflow`, asserts 200 + workflow response
- `test_revert_workflow_no_history` — mocks service to raise 400, asserts 400 propagated

### Service tests (test_workflow_service.py additions)

- `test_revert_workflow_uses_latest_history_entry` — asserts `update_workflow` called with `history[0].yaml_config`
- `test_revert_workflow_raises_when_no_history` — asserts `ExtendedHTTPException(400)`
- `test_update_workflow_history_records_change_type_revert` — asserts history entry has `change_type="revert"` when called from revert path
- `test_update_workflow_history_change_type_none_for_normal_save` — asserts `change_type=None` on normal `update_workflow` call

### Generator service tests (test_workflow_generator_service.py — new)

- `test_refine_workflow_returns_revised_yaml` — mocks chain, asserts revised YAML returned
- `test_refine_workflow_raises_on_invalid_yaml` — mocks chain returning invalid YAML, asserts `ExtendedHTTPException(400)`
- `test_refine_workflow_emits_ai_refine_metric` — asserts metric emitted on success
- `test_refine_workflow_emits_error_on_chain_failure` — asserts 500 on chain exception

---

## Acceptance Criteria Coverage

| AC | How covered |
|---|---|
| 'Refine/Modify with AI' endpoint available | `POST /v1/workflows/{id}/refine` |
| 'Revert to Previous' endpoint available | `POST /v1/workflows/{id}/revert` |
| After revert, workflow restored to last saved version | `revert_workflow()` reads `history[0]`, calls `update_workflow` |
| Action history logged for AI modifications and reverts | `change_type="revert"` in history; monitoring metrics for both actions |
| No regression in workflow edit/save | Existing `update_workflow` path unchanged; `change_type` param is backward-compatible |
| Both options validated for all workflow types | `Ability(user).can(Action.WRITE, workflow)` guards both endpoints |
