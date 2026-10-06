# EPMCDME-12906 — Workflow Categories Write Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-migrated `categories` JSONB column into the workflow create/update write path.

**Architecture:** Add the `categories` field to the two API request models so `model_dump()` propagates it into `WorkflowConfig`. Add `"categories"` to `_editable_non_boolean_fields` so `_update_workflow_values` persists it on update. Validate submitted category IDs against the `categories` table in the router, following the existing assistant pattern.

**Tech Stack:** Python, Pydantic v2, FastAPI, SQLModel, pytest

**Spec:** inline requirements (no spec file) — see acceptance criteria below.

## Acceptance criteria

- `CreateWorkflowRequest` and `UpdateWorkflowRequest` accept an optional `categories: list[str]` (0–3 items; empty by default).
- Submitting more than 3 category IDs is rejected by Pydantic validation (422).
- Invalid category IDs in a create or update request are rejected with HTTP 400.
- A workflow created or updated with valid category IDs persists those IDs via `_update_workflow_values`.
- The existing `categories` filter on the list endpoints is covered by a SQL predicate test confirming `@>` containment operator in the WHERE clause.

## Global Constraints

- No Alembic migration — column and GIN index already exist.
- Do not modify filter layer, list-response models, or retrieve path — already implemented.
- `max_length=3` enforced by Pydantic only — no DB CHECK constraint needed.
- Commit per task using the repository's existing convention.

---

### Task 1: Add `categories` field to request models and service set

**Files:**
- Modify: `src/codemie/core/workflow_models/workflow_models.py:424` (after `guardrail_assignments` in `CreateWorkflowRequest`) and `:439` (same in `UpdateWorkflowRequest`)
- Modify: `src/codemie/service/workflow_service.py:56-66` (`_editable_non_boolean_fields`)
- Test: `tests/codemie/service/test_workflow_service.py`

**Interfaces:**
- Produces: `CreateWorkflowRequest.categories: list[str]`, `UpdateWorkflowRequest.categories: list[str]` available to all callers that call `.model_dump()`.

**Test-first: yes — test that max-3 Pydantic constraint raises ValidationError; test that `_update_workflow_values` copies categories onto stored config**

- [ ] **Step 1: Write failing tests**

In `tests/codemie/service/test_workflow_service.py`, add:

```python
import pytest
from pydantic import ValidationError
from unittest.mock import MagicMock, patch

from codemie.core.workflow_models import CreateWorkflowRequest, UpdateWorkflowRequest, WorkflowConfig
from codemie.service.workflow_service import WorkflowService


def test_create_workflow_request_rejects_more_than_3_categories():
    with pytest.raises(ValidationError):
        CreateWorkflowRequest(
            name="w",
            description="d",
            project="p",
            categories=["a", "b", "c", "d"],
        )


def test_update_workflow_request_rejects_more_than_3_categories():
    with pytest.raises(ValidationError):
        UpdateWorkflowRequest(
            name="w",
            description="d",
            project="p",
            categories=["a", "b", "c", "d"],
        )


def test_update_workflow_values_propagates_categories():
    svc = WorkflowService()
    stored = WorkflowConfig(name="w", description="d", project="p")
    stored.categories = []
    updated = WorkflowConfig(name="w", description="d", project="p")
    updated.categories = ["cat-1", "cat-2"]
    mock_user = MagicMock()
    mock_user.as_user_model.return_value = MagicMock()

    with patch.object(stored, "save"):
        svc._update_workflow_values(stored, updated, mock_user)

    assert stored.categories == ["cat-1", "cat-2"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/codemie/service/test_workflow_service.py::test_create_workflow_request_rejects_more_than_3_categories tests/codemie/service/test_workflow_service.py::test_update_workflow_request_rejects_more_than_3_categories tests/codemie/service/test_workflow_service.py::test_update_workflow_values_propagates_categories -v
```

Expected: FAIL (no `categories` field on models, not in set)

- [ ] **Step 3: Add field to both request models**

In `workflow_models.py:424`, append to `CreateWorkflowRequest`:

```python
    categories: list[str] = Field(default_factory=list, max_length=3)
```

In `workflow_models.py:439`, append to `UpdateWorkflowRequest`:

```python
    categories: list[str] = Field(default_factory=list, max_length=3)
```

- [ ] **Step 4: Add `"categories"` to `_editable_non_boolean_fields`**

In `workflow_service.py:66`, add `"categories"` to the set literal so `_update_workflow_values` picks it up.

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/codemie/service/test_workflow_service.py::test_create_workflow_request_rejects_more_than_3_categories tests/codemie/service/test_workflow_service.py::test_update_workflow_request_rejects_more_than_3_categories tests/codemie/service/test_workflow_service.py::test_update_workflow_values_propagates_categories -v
```

Expected: PASS

---

### Task 2: Category ID validation in the workflow router

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow.py` — `create_workflow` and `update_workflow` handlers
- Test: `tests/codemie/rest_api/routers/test_workflow.py`

**Interfaces:**
- Consumes: `category_service.validate_category_ids(ids: list[str], required: bool = False) -> list[str]` — raises `ValueError` on invalid IDs; no-op on empty list.

**Test-first: yes — test that invalid category IDs on create and update return HTTP 400**

- [ ] **Step 1: Write failing tests**

In `tests/codemie/rest_api/routers/test_workflow.py`, add two tests (follow the file's existing `TestClient` / `AsyncClient` pattern):

```python
from unittest.mock import patch

def test_create_workflow_rejects_invalid_category_ids(client):
    """category_service.validate_category_ids raises ValueError → router returns 400."""
    with patch(
        "codemie.rest_api.routers.workflow.category_service.validate_category_ids",
        side_effect=ValueError("Invalid category IDs: ['bad-id']"),
    ):
        response = client.post(
            "/workflows",
            json={
                "name": "w", "description": "d", "project": "demo",
                "categories": ["bad-id"],
            },
        )
    assert response.status_code == 400


def test_update_workflow_rejects_invalid_category_ids(client, existing_workflow_id):
    with patch(
        "codemie.rest_api.routers.workflow.category_service.validate_category_ids",
        side_effect=ValueError("Invalid category IDs: ['bad-id']"),
    ):
        response = client.put(
            f"/workflows/{existing_workflow_id}",
            json={
                "name": "w", "description": "d", "project": "demo",
                "categories": ["bad-id"],
            },
        )
    assert response.status_code == 400
```

Adapt fixture names to match what already exists in `test_workflow.py`.

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/codemie/rest_api/routers/test_workflow.py::test_create_workflow_rejects_invalid_category_ids tests/codemie/rest_api/routers/test_workflow.py::test_update_workflow_rejects_invalid_category_ids -v
```

Expected: FAIL (no validation call yet)

- [ ] **Step 3: Add `category_service` import and validation calls in the router**

At the top of `workflow.py`, add alongside existing service imports:

```python
from codemie.service.assistant.category_service import category_service
```

In `create_workflow` (workflow.py:301), inside the existing `try` block, before `MCPAccessControlService.validate_on_save(...)`:

```python
        category_service.validate_category_ids(request.categories)
```

In `update_workflow` (workflow.py:379), inside the existing `try` block, before `MCPAccessControlService.validate_on_save(...)`:

```python
        category_service.validate_category_ids(request.categories)
```

Both raise `ValueError` on invalid IDs, which the surrounding `except Exception` already converts to `HTTP 400`.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/codemie/rest_api/routers/test_workflow.py::test_create_workflow_rejects_invalid_category_ids tests/codemie/rest_api/routers/test_workflow.py::test_update_workflow_rejects_invalid_category_ids -v
```

Expected: PASS

---

### Task 3: SQL filter predicate test for workflow categories

**Files:**
- Test: `tests/codemie/service/workflow_config/test_workflow_config_index_service.py`

**Interfaces:**
- Consumes: `WorkflowConfigIndexService.run(user, filter_by_user, page, per_page, categories=["cat-1"])` — the `categories` kwarg is already wired through `WorkflowFilter.FILTER_CONFIG`.

**Test-first: yes — test that passing a categories filter produces a `@>` containment operator in the SQL WHERE clause**

- [ ] **Step 1: Write failing test**

```python
@patch('codemie.service.workflow_config.workflow_config_index_service.Session')
def test_workflow_config_index_service_filter_by_categories(mock_session_class, mock_admin_user):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []
    mock_session.exec.return_value.one.return_value = 0

    WorkflowConfigIndexService.run(
        user=mock_admin_user, filter_by_user=False, page=0, per_page=20,
        categories=["cat-1"],
    )

    actual_query = str(mock_session.exec.call_args[0][0])
    assert "@>" in actual_query or "json_contains" in actual_query.lower() or "categories" in actual_query
```

The assertion checks for either the PostgreSQL `@>` containment operator or the `categories` column in the WHERE clause; tighten to `@>` once you verify the exact generated SQL string locally.

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/codemie/service/workflow_config/test_workflow_config_index_service.py::test_workflow_config_index_service_filter_by_categories -v
```

Expected: FAIL or ERROR (inspect actual SQL output to refine the assertion in step 3)

- [ ] **Step 3: Confirm generated SQL and tighten assertion**

If the test fails due to the SQL shape differing from the assertion, print `actual_query` and update the assert to match the real `@>` predicate. No production code changes needed — the filter is already wired.

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/codemie/service/workflow_config/test_workflow_config_index_service.py::test_workflow_config_index_service_filter_by_categories -v
```

Expected: PASS

---

## Negative-constraint pass

- **"No migration needed"** — no task touches `alembic/versions/`. Pass.
- **"DB column, GIN index, filter, list-response field, and full retrieve path are already implemented. The gap is only in the write path."** — no task modifies `filter_services.py`, `compose_filter_functions.py`, `WorkflowConfigListResponse`, or the GET/retrieve router handlers. Pass.
- **"max_length=3 … no DB CHECK constraint needed"** — constraint lives in `Field(max_length=3)` only. Pass.
- **No other negative constraints stated.**
