# Implementation Plan: EPMCDME-12558 — Embedding Model Picker Reset for Confluence Data Sources

**Date**: 2026-10-14
**Complexity**: XS (score 7)
**Scope**: Confluence embedding_model regression (bugs 1 & 2 only)

---

## Files Touched

| # | File | Change |
|---|---|---|
| 1 | `src/codemie/rest_api/models/index.py` | Add `embedding_model` field to `UpdateKnowledgeBaseConfluenceRequest` |
| 2 | `src/codemie/rest_api/routers/index/kb_atlassian.py` | Pass `embeddings_model=request.embedding_model` to `update_index()` call |

---

## Tasks

### Task 1 — Add `embedding_model` field to `UpdateKnowledgeBaseConfluenceRequest`

**File**: `src/codemie/rest_api/models/index.py` L1692-1701

Add the field after the `setting_id` line (L1698) so the model can accept `embedding_model` from the client:

```python
setting_id: Optional[str] = None
embedding_model: Optional[str] = None
```

**Rationale**: The `Create` request (`IndexKnowledgeBaseConfluenceRequest`) has this field, and every other KB update model (`UpdateKnowledgeBaseJiraRequest`, `UpdateKnowledgeBaseXrayRequest`, `UpdateKnowledgeBaseFilesRequest`) carries it. Without it, FastAPI strips the field from the PUT payload — the cause of bug 1.

---

### Task 2 — Pass `embeddings_model` to `update_index()` in the confluence PUT handler

**File**: `src/codemie/rest_api/routers/index/kb_atlassian.py` L234-243

Add one keyword: `embeddings_model=request.embedding_model` to the existing `kb_index[0].update_index(...)` call.

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
    embeddings_model=request.embedding_model,
)
```

**Rationale**: Even when the field is present on the request model, the endpoint never forwards the value to `update_index()`. Without it the field has no effect — the cause of bug 2.

---

## Verification

- `make ruff` must pass (no lint or format errors).
- API-level test: PUT `/index/knowledge_base/confluence` with a non-default `embedding_model` value (e.g., `"text-embedding-3-large"`), then GET the index and confirm `embeddings_model` reflects the saved value.
- The field must be `null`-tolerant (optional). Sending a PUT without it should leave the existing embedding model unchanged.

## Excluded

- Bug 3 (same gap in other KB update models like Jira, Xray, Files, etc.) falls under a separate ticket.
- No test files are created or modified — existing tests remain unaffected.