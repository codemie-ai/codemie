# Technical Analysis — EPMCDME-12558

## Ticket
**EPMCDME-12558** — Embedding model picker for Confluence data sources always resets to "Text Embedding Ada"

## Root Cause: Two API Backend Bugs

### Bug 1: `UpdateKnowledgeBaseConfluenceRequest` is missing `embedding_model` field

**File**: `src/codemie/rest_api/models/index.py:1692`

The Pydantic request model for updating a Confluence KB does not define an `embedding_model` field:

```python
class UpdateKnowledgeBaseConfluenceRequest(CronExpressionValidatorMixin, BaseModel):
    name: str = Field(min_length=1, max_length=500)
    project_name: str
    description: Optional[str] = None
    project_space_visible: Optional[bool] = None
    cql: Optional[str] = None
    setting_id: Optional[str] = None
    new_project_name: Optional[str] = None
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None
```

**No `embedding_model` field.** FastAPI/Pydantic silently drops the `embedding_model` value from the PUT request payload. The frontend sends it correctly, but the field never reaches the handler.

Compare with the **create** request model which **does** include it:
```python
class IndexKnowledgeBaseConfluenceRequest(CronExpressionValidatorMixin, IndexKnowledgeBaseRequest):
    embedding_model: Optional[str] = None
```

### Bug 2: The update endpoint never passes `embeddings_model` to `update_index()`

**File**: `src/codemie/rest_api/routers/index/kb_atlassian.py:234`

The Confluence update endpoint calls `update_index()` without the `embeddings_model` keyword argument:

```python
kb_index[0].update_index(
    user=user,
    description=request.description,
    project_space_visible=request.project_space_visible,
    cql=request.cql,
    reset_error=False,
    setting_id=request.setting_id,
    project_name=request.new_project_name if hasattr(request, 'new_project_name') else None,
    guardrail_assignments=request.guardrail_assignments,
)
```

`update_index()` accepts `embeddings_model: Optional[str] = None` and correctly persists it when provided — but the caller never supplies it, so `self.embeddings_model` is never updated.

### End-to-End Flow

1. Frontend sends `PUT /v1/index/knowledge_base/confluence` with `embedding_model: "text-embedding-3-small"`
2. FastAPI validates against `UpdateKnowledgeBaseConfluenceRequest` — **drops the field** (Bug 1)
3. Even if the field survived, the handler never passes it to `update_index()` (Bug 2)
4. Database retains the previous or default value → frontend shows "Text Embedding Ada"

## Fix Plan

**File 1** — `src/codemie/rest_api/models/index.py`: Add `embedding_model: Optional[str] = None` to `UpdateKnowledgeBaseConfluenceRequest`.

**File 2** — `src/codemie/rest_api/routers/index/kb_atlassian.py`: Add `embeddings_model=request.embedding_model` to the `update_index()` call.

## Affected Layers
- API request models (Pydantic)
- REST router handlers
- Service layer (`update_index()` is already correct)