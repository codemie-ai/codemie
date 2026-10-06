# EPMCDME-14452: LiteLLM Virtual Key Model Enforcement Design

**Date**: 2026-09-04  
**Ticket**: EPMCDME-14452  
**Epic**: EPMCDME-14349 (Project-Level Model Availability)  
**Branch**: EPMCDME-14452_Project_Level_Model_Availability  
**Status**: Design Phase

---

## Executive Summary

This design spec covers the **enforcement path** for project-level model availability in LiteLLM. When a project's `allowed_models` field is saved (via ticket 14452-A's PATCH endpoint), regenerate all three per-category virtual keys (PLATFORM, CLI, PREMIUM_MODELS) with the new model allow-list. The LiteLLM proxy then enforces the narrowing on subsequent API calls.

**Key design decision**: Regenerate keys on every save of `allowed_models`, ensuring the virtual key metadata always reflects the current project state. When `allowed_models` is NULL (unset), regenerate with `models=None` to allow all models through.

---

## Problem Statement

**Current state** (after ticket 14452-A):
- Backend can store and retrieve a project's `allowed_models` field via PATCH `/projects/{projectName}/allowed-models`
- Virtual keys exist in LiteLLM with per-category scoping (PLATFORM, CLI, PREMIUM_MODELS)
- Virtual key metadata includes a `models` field (list of allowed model identifiers)
- **Gap**: When `allowed_models` is saved, the virtual keys are NOT regenerated to reflect the new constraint

**Desired state**:
- Save `allowed_models` → automatically regenerate all three virtual keys with the updated `models` list
- External users calling LiteLLM with a project-scoped key receive only models in the project's allow-list
- When `allowed_models` is NULL, all models pass through (no narrowing applied)

---

## Design: Enforce via LiteLLM Adapter

### Overview

Leverage the existing **LiteLLM budget provider adapter** and its virtual key regeneration machinery. The adapter already has methods to recreate keys with a `models` parameter; we wire the project's `allowed_models` field into that parameter when a save occurs.

### Architecture

```
User Action: PATCH /projects/{projectName}/allowed-models
                 ↓
         [Router validates, authorizes]
                 ↓
         [Save allowed_models to DB]
                 ↓
         [Call ProjectBudgetService.ensure_project_budget(..., models=allowed_models)]
                 ↓
         [Service passes models to LiteLLM adapter.ensure_project_budget()]
                 ↓
         [Adapter calls _recreate_project_budget_key_alias() for each category]
                 ↓
         [Each key regenerated with metadata: { "models": allowed_models, ... }]
                 ↓
         [LiteLLM stores updated keys]
                 ↓
         Response: Updated project with allowed_models field
```

### Data Flow: Step by Step

1. **API Request**
   - `PATCH /projects/{projectName}/allowed-models`
   - Body: `{ "allowed_models": ["gpt-4-turbo", "claude-opus"] }` or `{ "allowed_models": null }`

2. **Router Layer** (`src/codemie/rest_api/routers/projects.py`)
   - Validate request (non-empty list, or null)
   - Authorize (admin/maintainer roles only)
   - Save to database: `project.allowed_models = request.allowed_models`
   - **Trigger enforcement**: Call service to regenerate keys

3. **Service Layer** (`src/codemie/service/budget/project_budget_service.py`)
   - Retrieve the saved `allowed_models` value
   - Call `provider.ensure_project_budget(..., models=project.allowed_models)`
   - This ensures the budget keys exist with the new model list

4. **Adapter Layer** (`src/codemie/enterprise/litellm/budget_provider_adapter.py`)
   - For each category (PLATFORM, CLI, PREMIUM_MODELS):
     - Call `_recreate_project_budget_key_alias(models=project.allowed_models)`
   - Each method:
     - Calls `service._generate_project_key(models=...)`
     - Stores `models` in virtual key metadata
     - Returns updated BudgetProviderState

5. **LiteLLM Response**
   - Virtual keys updated with new `models` field
   - Next API call from an external user using this key → LiteLLM enforces the narrowing

6. **Router Response**
   - Return `AllowedModelsUpdateResponse` with updated field
   - HTTP 200 success

### NULL Behavior (Model Narrowing Decision)

**Decision**: When `allowed_models` is NULL (unset), regenerate keys with `models=None`.

**Semantics**:
- `models=None` tells LiteLLM "no restriction; pass through all models the external user can access"
- This is the "all models allowed" state — no narrowing applied
- Existing external users with their own LiteLLM catalogue get full access within the project

**Rationale**:
- Ensures virtual key state is always synchronized with the database state
- Regenerating on every save (including clears) keeps the system predictable
- Avoids stale keys lingering with old restrictions after a project is "unrestricted"

### Integration Points

#### Trigger Point (Already Wired)
**File**: `src/codemie/rest_api/routers/projects.py`
- PATCH `/projects/{projectName}/allowed-models` endpoint (from ticket 14452-A)
- Already saves `allowed_models` to the database
- **New responsibility**: Call the service to regenerate keys immediately after save

#### Service Layer
**File**: `src/codemie/service/budget/project_budget_service.py`
- Method signature already supports: `ensure_project_budget(..., models: list[str] | None)`
- **Current state**: Inconsistent — some paths pass `models=data.models`, others pass `models=None`
- **Change**: Normalize all budget creation/update paths to pass the project's current `allowed_models` value

#### Adapter Layer
**File**: `src/codemie/enterprise/litellm/budget_provider_adapter.py`
- `_recreate_project_budget_key_alias(models: list[str] | None)` (lines 399–473)
  - Already accepts `models` parameter
  - Already passes to `service._generate_project_key()` at line 435
  - **No changes needed** — infrastructure is ready

- `_build_project_budget_state_from_key_state()` (lines 330–356)
  - Already stores `models` in metadata at line 352: `"models": models or []`
  - **No changes needed**

#### Model Picker
**File**: `src/codemie/service/llm_service/llm_service.py`
- `get_allowed_chat_models()` (lines 390–411)
- Calls LiteLLM model-info endpoint with member's virtual key
- LiteLLM already respects the key's `models` field
- **Verification needed**: End-to-end test to confirm narrowing works

---

## Boundary Conditions & Edge Cases

### Case 1: Project with `allowed_models` set
- User calls PATCH with `["gpt-4-turbo"]`
- Regenerate all three keys with `models=["gpt-4-turbo"]`
- External users on this project get only gpt-4-turbo

### Case 2: Project with `allowed_models = NULL` (default)
- Regenerate all three keys with `models=None` (or empty list)
- External users get all models they have access to

### Case 3: Change from one list to another
- Project starts with `["gpt-4-turbo"]`
- User updates to `["claude-opus", "claude-3-sonnet"]`
- Regenerate all three keys with new list
- External users immediately get access only to the new models

### Case 4: Clear `allowed_models` back to NULL
- Project has `["gpt-4-turbo"]`
- User sends PATCH with `{ "allowed_models": null }`
- Regenerate all three keys with `models=None`
- External users regain full access

### Case 5: Multiple categories must stay in sync
- All three keys (PLATFORM, CLI, PREMIUM_MODELS) for a project receive **identical** `models` lists
- No per-category narrowing — the project-level restriction applies uniformly
- Implementation must enforce this invariant

---

## Implementation Scope

### What Changes

1. **Router** (`projects.py`)
   - PATCH endpoint already saves `allowed_models`
   - Add call to service after save: `await project_budget_service.ensure_project_budget(..., models=project.allowed_models)`

2. **Service** (`project_budget_service.py`)
   - Review all calls to `provider.ensure_project_budget()`
   - Normalize to pass `models=project.allowed_models` (or `None` if not set)
   - Ensure consistency across creation, update, and sync paths

### What Stays the Same

- LiteLLM adapter: Already has the infrastructure
- Model picker: Already respects key's `models` field (needs verification only)
- Virtual key schema: No changes to key structure or metadata format
- Database schema: `allowed_models` field added in ticket 14452-A

---

## Testing Strategy

### Unit Tests
1. **Adapter test**: Call `_recreate_project_budget_key_alias(models=["gpt-4"])` → verify it calls `service._generate_project_key()` with correct `models`
2. **Service test**: Call `ensure_project_budget(..., models=["gpt-4"])` → verify adapter is called with correct `models`
3. **Validation test**: Empty list rejected; None accepted; non-empty list accepted

### Integration Tests
1. **Router + Service**: PATCH endpoint → verify service is called with project's `allowed_models`
2. **Service + Adapter**: Verify adapter regenerates all three keys with identical `models` list
3. **NULL handling**: PATCH with `null` → verify keys regenerated with `models=None`

### End-to-End Tests
1. Create project → set `allowed_models` → call LiteLLM with project key → verify only allowed models returned
2. Create project → leave `allowed_models` as NULL → call LiteLLM → verify all models accessible
3. Update `allowed_models` → verify old restriction no longer applies

### Regression Tests
1. Existing projects without `allowed_models` still work
2. Virtual key generation (existing flows) unaffected
3. Model picker continues to work with and without narrowing

---

## Error Handling

### Validation Errors
- **Empty list**: "allowed_models cannot be an empty list"
- **Non-list input**: Standard request validation error
- **Invalid model names**: Passed through to LiteLLM; LiteLLM rejects at runtime

### Authorization Errors
- **Insufficient role**: "Only admins and maintainers can update allowed_models"
- Follows existing project-level authorization patterns

### LiteLLM Errors
- If key regeneration fails in LiteLLM, propagate as 500 Internal Server Error
- Include error details in logs for debugging
- User receives: "Failed to update project model restrictions. Please try again."

---

## Success Criteria

- [x] Design approved by user
- [ ] All tests pass (unit, integration, end-to-end)
- [ ] Regression tests confirm existing flows unaffected
- [ ] Code follows project patterns (layered architecture, error handling, logging)
- [ ] PR description explains NULL → no narrowing decision
- [ ] Deploy to staging, verify end-to-end narrowing works with LiteLLM
- [ ] No performance impact on project creation or budget sync

---

## References

- **Ticket 14452-A**: Backend CRUD API for `allowed_models` field (completed)
- **Epic 14349**: Project-Level Model Availability
- **Architecture guide**: `.ai-run/guides/architecture/layered-architecture.md`
- **Error handling guide**: `.ai-run/guides/development/error-handling.md`
- **LiteLLM adapter**: `src/codemie/enterprise/litellm/budget_provider_adapter.py`
- **Budget service**: `src/codemie/service/budget/project_budget_service.py`

---

## Appendix: Key File Locations

| Component | File | Lines |
|-----------|------|-------|
| API Endpoint | `src/codemie/rest_api/routers/projects.py` | PATCH handler |
| Service Layer | `src/codemie/service/budget/project_budget_service.py` | ~580, ~952, ~1871, ~2232 |
| Adapter | `src/codemie/enterprise/litellm/budget_provider_adapter.py` | 330–356, 399–473, 917, 978 |
| Model Picker | `src/codemie/service/llm_service/llm_service.py` | 390–411 |
| Tests | `tests/codemie/rest_api/routers/test_projects_router.py` | (new tests added) |
| Migration | `src/external/alembic/versions/b2c3d4e5f6a7_*.py` | (from 14452-A) |

