# Project-Level LLM Model Access Control

**Date:** 2026-09-16  
**Status:** Design Phase  
**Ticket:** EPMCDME-14349  

## Executive Summary

Implement enforcement of project-level model whitelists at two critical points:
1. **Asset Creation (Stage 1)**: Prevent users from creating assistants/workflows with non-whitelisted models
2. **Model Execution (Stage 2)**: Fall back to the project's default model when a disabled model is requested during execution

The system will use **Option B architecture** (Two Separate Enforcement Points) with a **shared validation service** (`ModelAvailabilityService`) to prevent logic drift.

## Current State

### Database & Models
- **`src/codemie/core/models.py:398-399`** — `Application` model already has:
  - `allowed_models: Optional[list[str]]` (PostgreSQL ARRAY column)
  - `default_model: Optional[str]` (nullable string, max 255 chars)
- **Migration exists** (`c3d4e5f6a7b8_add_default_model_to_applications.py`)
- **Project-level settings** implemented in EPMCDME-14349 (UI + backend)

### Current Gaps
1. **Asset Creation Validation**: No check in `POST /v1/assistants` or `POST /v1/workflows` to verify model is whitelisted
2. **Model Execution**: No fallback logic when a workflow/assistant tries to use a disabled model
3. **Credential Resolution Pattern**: Credentials use cache + resolution functions (`resolve_litellm_user_credentials`) — we follow this pattern

## Architecture: Option B (Two Separate Enforcement Points)

### Core Service: `ModelAvailabilityService`

**Location:** `src/codemie/service/llm/model_availability_service.py` (new file)

```python
class ModelAvailabilityService:
    """Validates and resolves model availability against project constraints."""

    @staticmethod
    def validate_model_for_asset_creation(
        model_id: str,
        project_name: str,
    ) -> None:
        """
        Validate that a model is whitelisted for the project.
        
        Raises:
            - ModelNotWhitelistedException: if model not in allowed list
            - ProjectNotFoundException: if project doesn't exist
            - NoDefaultModelException: if project has no allowed_models/default_model
        """

    @staticmethod
    def resolve_model_for_execution(
        requested_model: str,
        project_name: str,
    ) -> tuple[str, bool]:
        """
        Resolve actual model to use, with fallback.
        
        Returns:
            (actual_model_id, was_fallback_applied)
        
        Falls back to default_model if requested_model is not whitelisted.
        Raises ModelNotWhitelistedException if neither requested nor default is available.
        """

    @staticmethod
    def get_project_models(project_name: str) -> ProjectModelConfig:
        """Fetch allowed_models and default_model for project."""
```

### Enforcement Point 1: Asset Creation

**Location:** `src/codemie/rest_api/routers/assistant.py` (POST `/v1/assistants`)

```python
@router.post("/v1/assistants", response_model=Assistant)
def create_assistant(request: AssistantRequest, user: User = Depends(get_current_user)):
    # Validation: Check model is whitelisted
    if request.llm_model_type:
        try:
            ModelAvailabilityService.validate_model_for_asset_creation(
                request.llm_model_type,
                request.project,
            )
        except ModelNotWhitelistedException:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{request.llm_model_type}' is not allowed for project '{request.project}'. "
                       f"Please use the project's available models."
            )
    # ... rest of creation logic
```

**User Notification (Stage 1):** Backend returns 400 with detail message. Frontend catches and shows error:
```
"Model 'claude-opus' is not allowed for project 'my-project'. Please use the project's available models."
```

### Enforcement Point 2: Model Execution

**Location:** `src/codemie/service/llm_service/llm_service.py` (new method in `LLMService`)

```python
class LLMService:
    def resolve_model_with_project_availability(
        self,
        requested_model: str,
        project_name: str,
    ) -> tuple[str, bool]:
        """
        Check if requested_model is available in project.
        If not, fall back to default_model.
        
        Returns (actual_model, was_fallback_applied).
        """
        actual_model, was_fallback = ModelAvailabilityService.resolve_model_for_execution(
            requested_model, project_name
        )
        
        if was_fallback:
            logger.info(
                f"model_availability_event=fallback_applied "
                f"requested_model={requested_model!r} "
                f"project={project_name!r} "
                f"fallback_model={actual_model!r}"
            )
        
        return actual_model, was_fallback
```

**Integration Point:** Called from workflow nodes (e.g., `StateProcessorNode`, `AgentNode`) before model invocation.

**User Notification (Stage 2):** When workflow execution finishes:
```python
# In workflow result handler or callback
if model_was_fallback:
    add_to_notification_queue(
        user_id=user.id,
        notification_type="MODEL_UNAVAILABLE",
        message=f"Model '{requested_model}' is not available in project '{project}'. "
                f"Using fallback model '{actual_model}' instead.",
    )
```

## Data Flow

### Stage 1: Asset Creation

```
User: POST /v1/assistants with llm_model_type="gpt-4"
  ↓
AssistantRouter.create_assistant()
  ↓
ModelAvailabilityService.validate_model_for_asset_creation(
  model_id="gpt-4", 
  project_name="my-project"
)
  ├─ Query Application table: SELECT allowed_models FROM applications WHERE name = "my-project"
  ├─ Check: "gpt-4" IN allowed_models?
  │  ├─ YES → Continue to create assistant
  │  └─ NO → Raise ModelNotWhitelistedException
  └─ If exception: Return 400 with error message
```

### Stage 2: Model Execution

```
WorkflowExecution: Node needs to invoke model="claude-opus"
  ↓
Node calls: get_llm_by_credentials(..., model_name="claude-opus", project=...)
  ↓
LLMService.resolve_model_with_project_availability(
  requested_model="claude-opus",
  project_name="my-project"
)
  ├─ Query Application table: SELECT allowed_models, default_model FROM applications WHERE name = "my-project"
  ├─ Check: "claude-opus" IN allowed_models?
  │  ├─ YES → Return ("claude-opus", False) # no fallback
  │  └─ NO → 
  │     ├─ Return (default_model, True) # with fallback flag
  │     └─ Log: model_availability_event=fallback_applied
  └─ Callback/Handler triggers notification to user
```

## Exception Hierarchy

**Location:** `src/codemie/core/exceptions.py` (new exceptions)

```python
class ModelAvailabilityException(ExtendedHTTPException):
    """Base for model availability errors."""
    pass

class ModelNotWhitelistedException(ModelAvailabilityException):
    status_code = 400
    error_code = "MODEL_NOT_WHITELISTED"
    
class NoDefaultModelException(ModelAvailabilityException):
    status_code = 500
    error_code = "NO_DEFAULT_MODEL"
```

## Edge Cases & Solutions

| Scenario | Behavior | Rationale |
|----------|----------|-----------|
| Model is in whitelist but later disabled via admin panel | Fallback at execution time | Admin can disable without breaking existing assets |
| No default model set (structural impossibility per requirements) | Never happens | Project creation enforces default_model is set |
| Requested model = default model | Execute normally | No fallback needed |
| Default model itself is not in whitelist (structural impossibility) | Never happens | `validate_default_model()` ensures default ∈ allowed_models |
| Project has empty whitelist | System blocks all model usage | Admin must fix by adding models |
| User tries to use model not in their project's whitelist | 400 at creation, fallback at execution | Two-stage enforcement |

## Integration Points with Existing Patterns

### Credential Resolution Pattern Match
- **Caching**: `ModelAvailabilityService` will cache `(project_name) → ProjectModelConfig` with TTL
- **Resolution**: Follow `resolve_litellm_user_credentials()` pattern (cache + uncached helper)
- **Logging**: Use same format as credential events (`event=..., project=..., model=...`)

### Workflow Context Threading
- **Existing pattern**: `workflow_config.project` is already available in workflow execution
- **Pass through**: Project name threads through workflow nodes naturally
- **No new context object needed**: Reuse existing `project` field

### User Notification Pattern
- **Queue-based**: Use existing background notification system (if exists) or webhook
- **Per-execution tracking**: Store fallback event in workflow execution state

## Files to Create/Modify

### New Files
1. **`src/codemie/service/llm/model_availability_service.py`** — Core validation & resolution service
2. **Tests:** `tests/codemie/service/llm/test_model_availability_service.py`

### Modified Files
1. **`src/codemie/rest_api/routers/assistant.py`** — Add validation in POST /v1/assistants
2. **`src/codemie/rest_api/routers/workflow.py`** — Add validation in POST /v1/workflows
3. **`src/codemie/service/llm_service/llm_service.py`** — Add `resolve_model_with_project_availability()`
4. **`src/codemie/workflows/nodes/agent_node.py`** — Call resolve before model invocation
5. **`src/codemie/workflows/nodes/state_processor_node.py`** — Call resolve before model invocation
6. **`src/codemie/core/exceptions.py`** — Add exception classes
7. **`src/codemie/service/project/project_service.py`** — Validation helper (ensure default ∈ allowed)

### Database
- No migration needed (fields already exist from EPMCDME-14349)

## Testing Strategy

### Unit Tests
1. `validate_model_for_asset_creation()` — model in/not-in whitelist
2. `resolve_model_for_execution()` — fallback behavior
3. Edge cases: empty whitelist, missing default, model not in allowed list

### Integration Tests
1. **Stage 1**: POST `/v1/assistants` with whitelisted vs non-whitelisted model
2. **Stage 2**: Execute workflow with model that becomes unavailable

### E2E Flow
1. Create project with allowed_models = ["gpt-4", "claude-opus"], default = "gpt-4"
2. Create assistant with model = "claude-opus" (should succeed)
3. Admin removes "claude-opus" from whitelist
4. Execute assistant (should fallback to "gpt-4", show notification)

## Logging & Observability

### Log Events
```
model_availability_event=validation_attempted project=... model=... user=...
model_availability_event=validation_failed project=... model=... reason=...
model_availability_event=fallback_applied requested_model=... fallback_model=... project=...
```

### Metrics
- `model_availability.validations.total` (counter, label: result=[allowed|blocked])
- `model_availability.fallbacks.total` (counter, label: project, model)

## Backwards Compatibility

- **Existing projects without whitelist**: Use global model list (no change)
- **Existing projects with whitelist but no default**: Require admin to set one (one-time action)
- **Existing assistants**: No retroactive validation (fallback only at execution time)

## Success Criteria

✅ **Stage 1**: Users cannot create assets with non-whitelisted models (400 response)  
✅ **Stage 2**: Non-whitelisted models fallback to default at execution (with notification)  
✅ **Validation shared**: Both stages call same `ModelAvailabilityService` helper  
✅ **Edge cases handled**: No scenario allows invalid model state  
✅ **Logging**: Full audit trail of validations and fallbacks  
✅ **Tests**: >80% coverage on service layer  

## Open Questions (Resolved ✅)

- ~~Which enforcement architecture? (Option A vs B)~~ → **Option B chosen** with shared service
- ~~Who gets notifications?~~ → Creator only (both stages)
- ~~Fallback per-execution or permanent?~~ → Per-execution only (silent in backend, popup in UI)
- ~~Default model mandatory?~~ → **Yes**, enforced at project creation
- ~~Empty whitelist handling?~~ → Blocks all usage (admin responsibility to fix)
