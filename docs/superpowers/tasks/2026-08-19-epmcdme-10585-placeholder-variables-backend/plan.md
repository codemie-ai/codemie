# EPMCDME-10585 — Placeholder Variables Backend (Revision 2026-09-14)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `raw_yaml`/rejection approach with `required_variables` on templates and a new `/materialize` endpoint that substitutes variables server-side.

**Architecture:** `placeholders.py` gains `substitute_placeholders`; dead `quote_unquoted_placeholders` is removed. `WorkflowConfigTemplate.from_yaml` drops `raw_yaml` and populates `required_variables` by scanning parsed fields. `_reject_unresolved_placeholders` is removed from `validate_workflow` — persisted workflows treat `${input:...}` as plain text. A new `POST /v1/workflows/prebuilt/{slug}/materialize` endpoint (router + service method) substitutes caller-supplied variables into template fields and returns the seed.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic/SQLModel, PyYAML, pytest.

**Spec:** `docs/superpowers/tasks/2026-08-19-epmcdme-10585-placeholder-variables-backend/spec.md` (revision 2026-09-14 supersedes the file)

## Global Constraints

- `${input:([^}]+)}` is the only placeholder syntax.
- `WorkflowConfig` (persisted table model) must not be touched — placeholder syntax in saved workflows is treated as literal text.
- `raw_yaml` must not appear in any API response after this plan.
- No Alembic migration — `WorkflowConfigTemplate` is a non-table Pydantic model.
- Unrecognised variable names in a materialize request leave the original token intact in the returned text.
- Apache 2.0 license header on all new files.
- Commit per task using the repository's existing convention.

---

### Task 1 — Add `substitute_placeholders`; remove dead code in `placeholders.py`

**Test-first: yes — `ImportError` on `from codemie.workflows.placeholders import substitute_placeholders`**

**Files:**
- Modify: `src/codemie/workflows/placeholders.py`
- Modify: `tests/codemie/workflows/test_placeholders.py`

**Interfaces:**
- Produces: `substitute_placeholders(text: str | None, variables: dict[str, str]) -> str | None`
- Removes: `quote_unquoted_placeholders`, `_UNQUOTED_PLACEHOLDER_PATTERN` (replaced by sentinel approach; no callers remain)

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/workflows/test_placeholders.py`. Remove `test_quote_unquoted_does_not_double_quote` (function being deleted). Add:

```python
from codemie.workflows.placeholders import substitute_placeholders


def test_substitute_replaces_known_variables():
    text = "Hello ${input:team_name}, role is ${input:role}."
    assert substitute_placeholders(text, {"team_name": "Alice", "role": "admin"}) == \
        "Hello Alice, role is admin."


def test_substitute_preserves_unknown_variables():
    text = "Hello ${input:team_name}, ${input:unknown}."
    assert substitute_placeholders(text, {"team_name": "Alice"}) == \
        "Hello Alice, ${input:unknown}."


def test_substitute_none_input_returns_none():
    assert substitute_placeholders(None, {"x": "y"}) is None


def test_substitute_empty_variables_no_change():
    assert substitute_placeholders("Hi ${input:x}.", {}) == "Hi ${input:x}."
```

- [ ] **Step 2: Run — expect FAIL**

```bash
poetry run pytest tests/codemie/workflows/test_placeholders.py -v
```

Expected: `ImportError: cannot import name 'substitute_placeholders'`

- [ ] **Step 3: Add `substitute_placeholders`; remove dead symbols**

In `src/codemie/workflows/placeholders.py`:

1. Delete lines declaring `_UNQUOTED_PLACEHOLDER_PATTERN` and `quote_unquoted_placeholders`.
2. Add after `extract_placeholder_names`:

```python
def substitute_placeholders(text: str | None, variables: dict[str, str]) -> str | None:
    if not text:
        return text
    return PLACEHOLDER_PATTERN.sub(lambda m: variables.get(m.group(1), m.group(0)), text)
```

- [ ] **Step 4: Run — expect PASS**

```bash
poetry run pytest tests/codemie/workflows/test_placeholders.py -v
```

Expected: all tests pass (including existing `test_extract_*` and `test_collect_*`).

---

### Task 2 — Drop `raw_yaml`; add `required_variables` on `WorkflowConfigTemplate`

**Test-first: yes — failing assertion `tmpl.required_variables == ["team_name", "start_hint", "assistant_id"]` (field absent) and `AttributeError` if `raw_yaml` is still accessed**

**Files:**
- Modify: `src/codemie/core/workflow_models/workflow_config.py` (lines 353–372)
- Modify: `tests/codemie/core/workflow_models/test_workflow_config.py`

**Interfaces:**
- Consumes: `collect_unresolved_placeholders` from `codemie.workflows.placeholders` (already exists; add to import at line 28)
- Produces: `WorkflowConfigTemplate.required_variables: list[str]` — unique placeholder names in first-seen order across all template fields

- [ ] **Step 1: Write the failing test**

Replace `test_template_from_yaml_keeps_raw_text_and_loads_unquoted_placeholder` in
`tests/codemie/core/workflow_models/test_workflow_config.py` with:

```python
def test_template_from_yaml_extracts_required_variables(self):
    raw = (
        "name: Example\n"
        "description: Team ${input:team_name}\n"
        "slug: template-placeholder-test\n"
        "mode: Sequential\n"
        "start_hint: ${input:start_hint}\n"
        "execution_config:\n"
        "  assistants: []\n"
        "  states:\n"
        "    - id: step\n"
        '      assistant_id: "${input:assistant_id}"\n'
        "      task: do work\n"
    )
    from codemie.core.workflow_models.workflow_config import WorkflowConfigTemplate
    tmpl = WorkflowConfigTemplate.from_yaml(raw)
    assert tmpl is not None
    assert tmpl.slug == "template-placeholder-test"
    assert "${input:team_name}" in tmpl.description
    assert "${input:assistant_id}" in tmpl.yaml_config
    assert tmpl.required_variables == ["team_name", "start_hint", "assistant_id"]
```

- [ ] **Step 2: Run — expect FAIL**

```bash
poetry run pytest "tests/codemie/core/workflow_models/test_workflow_config.py::TestWorkflowConfig::test_template_from_yaml_extracts_required_variables" -v
```

Expected: `AttributeError: 'WorkflowConfigTemplate' object has no attribute 'required_variables'`

- [ ] **Step 3: Update `WorkflowConfigTemplate`**

In `src/codemie/core/workflow_models/workflow_config.py`:

1. Line 28 — extend the import to include `collect_unresolved_placeholders`:
   ```python
   from codemie.workflows.placeholders import (
       substitute_placeholders_for_yaml_load,
       restore_sentinels_in_obj,
       collect_unresolved_placeholders,
   )
   ```

2. In `WorkflowConfigTemplate` (line 353): replace `raw_yaml: Optional[str] = None` with:
   ```python
   required_variables: list[str] = Field(default_factory=list)
   ```

3. In `from_yaml`, replace `template.raw_yaml = yaml_str` with:
   ```python
   template.required_variables = collect_unresolved_placeholders(
       name=template.name,
       description=template.description,
       start_hint=template.start_hint,
       supervisor_prompt=template.supervisor_prompt,
       yaml_config=template.yaml_config,
   )
   ```

- [ ] **Step 4: Run — expect PASS**

```bash
poetry run pytest tests/codemie/core/workflow_models/test_workflow_config.py -v
```

Expected: all tests pass.

---

### Task 3 — Remove `_reject_unresolved_placeholders` from `validate_workflow`; clean up constants

**Test-first: yes — a `WorkflowConfig` with `${input:...}` in `description` must NOT raise `ValueError` from `validate_workflow`; the test currently fails because the rejection is still active**

**Files:**
- Modify: `src/codemie/workflows/workflow.py` (lines 85, 222–240, 255)
- Modify: `src/codemie/workflows/constants.py`
- Delete: `tests/codemie/workflows/test_unresolved_placeholders_validation.py`
- Modify: `tests/codemie/workflows/test_placeholders.py` (no change needed — `collect_unresolved_placeholders` is kept)

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/workflows/test_placeholders.py` (or a new file `tests/codemie/workflows/test_placeholder_no_rejection.py`):

```python
# tests/codemie/workflows/test_placeholder_no_rejection.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import MagicMock, patch

from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.workflows.workflow import WorkflowExecutor


def test_validate_workflow_accepts_placeholder_tokens_as_plain_text():
    """Persisted workflows treat ${input:...} as literal text — no rejection."""
    wf = WorkflowConfig(
        name="WF",
        description="Team ${input:team_name}",
        yaml_config="assistants: []\nstates: []\n",
        mode="Autonomous",
    )
    user = MagicMock()
    user.project_names = ["app"]

    with (
        patch("codemie.workflows.workflow.validate_workflow_execution_config_yaml"),
        patch.object(WorkflowConfig, "parse_execution_config"),
        patch("codemie.workflows.workflow.validate_workflow_config_resources_availability"),
        patch.object(
            WorkflowExecutor,
            "create_executor",
            MagicMock(return_value=MagicMock(_init_workflow=MagicMock(return_value=None))),
        ),
    ):
        # Must not raise — placeholder token is literal text in persisted workflows
        WorkflowExecutor.validate_workflow(wf, user, error_format="string")
```

- [ ] **Step 2: Run — expect FAIL**

```bash
poetry run pytest tests/codemie/workflows/test_placeholder_no_rejection.py -v
```

Expected: `FAILED` — `ValueError: Unresolved placeholder variables: team_name` (rejection still present)

- [ ] **Step 3: Remove rejection logic from `workflow.py`**

In `src/codemie/workflows/workflow.py`:

1. Remove line 85: `from codemie.workflows.placeholders import collect_unresolved_placeholders`
2. Delete the `_reject_unresolved_placeholders` static method (lines 222–240).
3. Remove line 255: `WorkflowExecutor._reject_unresolved_placeholders(workflow_config, error_format)`

- [ ] **Step 4: Remove `UNRESOLVED_PLACEHOLDER` from `WorkflowErrorType`**

In `src/codemie/workflows/constants.py`, delete:

```python
UNRESOLVED_PLACEHOLDER = "unresolved_placeholder"
```

- [ ] **Step 5: Delete the old rejection test file**

```bash
rm tests/codemie/workflows/test_unresolved_placeholders_validation.py
```

- [ ] **Step 6: Run — expect PASS**

```bash
poetry run pytest tests/codemie/workflows/test_placeholder_no_rejection.py tests/codemie/workflows/test_placeholders.py -v
```

Expected: all tests pass; old rejection test file is gone.

---

### Task 4 — `required_variables` in GET-by-slug; remove `raw_yaml` exclude; add `/materialize`

**Test-first: yes — `POST /v1/workflows/prebuilt/{slug}/materialize` returns 404 (route absent before implementation); GET-by-slug response has no `raw_yaml` key; list response includes `required_variables`**

**Files:**
- Modify: `src/codemie/service/workflow_service.py`
- Modify: `src/codemie/rest_api/routers/workflow.py`
- Modify: `tests/codemie/rest_api/routers/test_workflow.py`

**Interfaces:**
- Consumes: `WorkflowService.get_prebuilt_workflow_by_slug(slug) -> Optional[WorkflowConfigTemplate]` (existing)
- Consumes: `substitute_placeholders` from Task 1
- Produces: `WorkflowService.materialize_template(slug: str, variables: dict[str, str]) -> Optional[dict]`
- Produces: `POST /v1/workflows/prebuilt/{slug}/materialize` → `MaterializeSeedResponse`

- [ ] **Step 1: Write failing tests**

In `tests/codemie/rest_api/routers/test_workflow.py`:

Replace `test_get_prebuilt_workflows_list_excludes_raw_yaml` with:

```python
@patch.object(WorkflowService, '_cached_prebuilt_workflows', new_callable=list)
def test_get_prebuilt_workflows_list_includes_required_variables(mock_cached_workflows):
    wf = WorkflowConfigTemplate(
        name="Template", description="desc", slug="test-slug",
        required_variables=["team_name"],
    )
    mock_cached_workflows.append(wf)

    response = client.get(
        "/v1/workflows/prebuilt",
        headers={"user-id": user.id, "username": user.username, "name": user.name},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert "raw_yaml" not in data[0]
    assert data[0]["required_variables"] == ["team_name"]
```

Replace `test_get_prebuilt_workflow_by_slug_includes_raw_yaml` with:

```python
@patch.object(WorkflowService, '_cached_prebuilt_workflows', new_callable=list)
def test_get_prebuilt_workflow_by_slug_has_required_variables(mock_cached_workflows):
    wf = WorkflowConfigTemplate(
        name="Template", description="desc", slug="test-slug",
        required_variables=["team_name", "role"],
    )
    mock_cached_workflows.append(wf)

    response = client.get(
        "/v1/workflows/prebuilt/test-slug",
        headers={"user-id": user.id, "username": user.username, "name": user.name},
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert "raw_yaml" not in body
    assert body["required_variables"] == ["team_name", "role"]
```

Add at the end of the file:

```python
@patch.object(WorkflowService, '_cached_prebuilt_workflows', new_callable=list)
def test_materialize_substitutes_variables(mock_cached_workflows):
    wf = WorkflowConfigTemplate(
        name="Example",
        description="Hello ${input:team_name}",
        start_hint="Start for ${input:team_name}",
        yaml_config="assistants: []\nteam: ${input:team_name}\n",
        slug="mat-slug",
        required_variables=["team_name"],
    )
    mock_cached_workflows.append(wf)

    response = client.post(
        "/v1/workflows/prebuilt/mat-slug/materialize",
        json={"variables": {"team_name": "Alice"}},
        headers={"user-id": user.id, "username": user.username, "name": user.name},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["description"] == "Hello Alice"
    assert data["start_hint"] == "Start for Alice"
    assert "Alice" in data["yaml_config"]
    assert "${input:team_name}" not in data["yaml_config"]


def test_materialize_unknown_slug_returns_404():
    response = client.post(
        "/v1/workflows/prebuilt/nonexistent-slug/materialize",
        json={"variables": {}},
        headers={"user-id": user.id, "username": user.username, "name": user.name},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
```

- [ ] **Step 2: Run — expect FAIL**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_workflow.py -k "required_variables or materialize" -v
```

Expected: materialize tests fail with 404/405 (route absent); `raw_yaml` exclude tests fail because `raw_yaml` field is already removed in Task 2 (they should now pass trivially — that's fine).

- [ ] **Step 3: Add `materialize_template` to `WorkflowService`**

In `src/codemie/service/workflow_service.py`, after `get_prebuilt_workflow_by_slug`:

```python
def materialize_template(self, slug: str, variables: dict[str, str]) -> Optional[dict]:
    from codemie.workflows.placeholders import substitute_placeholders

    template = self.get_prebuilt_workflow_by_slug(slug)
    if template is None:
        return None
    return {
        "description": substitute_placeholders(template.description, variables),
        "start_hint": substitute_placeholders(template.start_hint, variables),
        "yaml_config": substitute_placeholders(template.yaml_config, variables),
    }
```

- [ ] **Step 4: Add request/response models and endpoint to the router**

In `src/codemie/rest_api/routers/workflow.py`, after the `WorkflowSaveResponse` class:

```python
class MaterializeRequest(BaseModel):
    variables: dict[str, str]


class MaterializeSeedResponse(BaseModel):
    yaml_config: Optional[str] = None
    description: Optional[str] = None
    start_hint: Optional[str] = None
```

Add the endpoint after `get_prebuilt_workflow_by_slug`:

```python
@router.post(
    "/workflows/prebuilt/{slug}/materialize",
    status_code=status.HTTP_200_OK,
    response_model=MaterializeSeedResponse,
    summary="Materialize a prebuilt workflow template",
    description=(
        "Substitutes caller-supplied variables into the template's fields and returns "
        "the filled content as a workflow seed. Tokens with no matching variable are left as-is."
    ),
)
def materialize_prebuilt_workflow(
    slug: str,
    request: MaterializeRequest,
    user: User = Depends(authenticate),
):
    result = workflow_service.materialize_template(slug, request.variables)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow template '{slug}' not found",
        )
    return MaterializeSeedResponse(**result)
```

Also remove `response_model_exclude={"__all__": {"raw_yaml"}}` from the `GET /workflows/prebuilt` decorator — the field no longer exists on the model.

Confirm `HTTPException` is already imported; add if absent: `from fastapi import ..., HTTPException`.

- [ ] **Step 5: Run — expect PASS**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_workflow.py -k "required_variables or materialize" -v
```

Expected: all four new tests pass.

- [ ] **Step 6: Full test suite smoke-check**

```bash
poetry run pytest tests/codemie/workflows/ tests/codemie/core/workflow_models/ tests/codemie/rest_api/routers/test_workflow.py -v
```

Expected: all pass; no references to `raw_yaml` or `UNRESOLVED_PLACEHOLDER` remain in test output.

---

## Done when

- GET-by-slug returns `required_variables: list[str]` and no `raw_yaml`.
- GET list returns `required_variables`; `response_model_exclude` for `raw_yaml` removed.
- `validate_workflow` passes for any `WorkflowConfig` regardless of `${input:...}` tokens.
- `POST /v1/workflows/prebuilt/{slug}/materialize` with `{"variables": {...}}` returns substituted `description`, `start_hint`, `yaml_config`; unknown slugs return 404.
- `test_unresolved_placeholders_validation.py` deleted; all remaining tests green.

**Negative-constraints check:**
- No task adds placeholder-awareness to `WorkflowConfig` or `validate_workflow`. ✓
- No task exposes `raw_yaml` in any response. ✓
- No task reintroduces save-time rejection of `${input:...}` on POST/PUT /workflows. ✓
