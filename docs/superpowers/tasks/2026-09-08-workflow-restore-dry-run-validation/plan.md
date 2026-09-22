# Workflow Validate Dry-Run Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /v1/workflows/{workflow_id}/validate` that runs the PUT pre-persist validation pipeline without persisting, via a shared helper extracted from `update_workflow`.

**Architecture:** A module-private async helper `_run_pre_persist_update_validation` is added to `src/codemie/rest_api/routers/workflow.py`; it owns MCP access-control, autonomous-mode rejection (403), and the `validate_for_update` call, plus the outer except chain that converts failures to 400. Both PUT and the new POST validate endpoint call this helper. The validate endpoint returns a plain 200 body on success with no persist, no guardrail sync, and no background tasks.

**Tech Stack:** FastAPI, Python asyncio, pytest-asyncio, unittest.mock (AsyncMock/patch), httpx ASGITransport/AsyncClient.

## Global Constraints

- Commit per task using the repository's existing convention.
- The validate endpoint must not call `update_workflow`, `GuardrailService.sync_guardrail_assignments_for_entity`, `background_tasks.add_task`, or emit update metrics.
- `validate_for_update` remains the sole caller of `WorkflowExecutor.validate_workflow` on the update path; the new endpoint must not call `validate_workflow` directly.
- `_strip_workflow_mcp_servers` mutates the proposed config in memory; the mutated object must never reach a persist call.
- No changes to `codemie/workflows/config_resources_validation.py`; the live validation path is `codemie/workflows/validation/`.

---

## Acceptance Criteria

- POST /v1/workflows/{workflow_id}/validate returns 200 `{"message": "Workflow configuration is valid"}` for a valid SEQUENTIAL config.
- Returns 400 with the identical error envelope to PUT on validation failure (bad YAML, missing assistant, MCP violation).
- Returns 403 when `proposed.mode == WorkflowMode.AUTONOMOUS`.
- Returns 401 without WRITE ability; 404 for an unknown workflow_id.
- `update_workflow` is never called from the validate endpoint (verified by assertion in tests).
- All existing `test_update_workflow*` tests pass unchanged after the PUT refactor.

---

### Task 1: Extract helper and refactor PUT

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow.py:98` (insert helper), `workflow.py:416–467` (replace inline pipeline)
- Test: `tests/codemie/rest_api/routers/test_workflow.py` — existing tests only; no edits, used for regression.

**Interfaces:**
- Produces: `async def _run_pre_persist_update_validation(workflow, proposed: WorkflowConfig, user: User, error_format: WorkflowErrorFormat) -> None` — raises on any validation failure, returns `None` on success.

**Test-first:** Yes — run existing `test_update_workflow*` as baseline before touching code, re-run after to confirm no regression. The existing `test_update_workflow` asserts `update_workflow` was called once, which is the requirement-c regression check.

- [ ] **Step 1: Baseline**

  Run: `pytest tests/codemie/rest_api/routers/test_workflow.py -k update_workflow -v`
  Expected: all matching tests PASS.

- [ ] **Step 2: Insert helper** — add after `_strip_workflow_mcp_servers` (line 97), before the `update_workflow` route definition:

```python
async def _run_pre_persist_update_validation(
    workflow, proposed: WorkflowConfig, user: User, error_format: WorkflowErrorFormat
) -> None:
    """MCP, autonomous-mode, and executor validation without persisting."""
    try:
        MCPAccessControlService.validate_on_save(_collect_workflow_mcp_servers(proposed))
        _strip_workflow_mcp_servers(proposed)
        if proposed.mode == WorkflowMode.AUTONOMOUS:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Autonomous workflows are disabled",
                details="Updating workflows to autonomous mode is not allowed. Only sequential workflows can be used.",
                help="Please set the workflow mode to 'SEQUENTIAL' instead.",
            )
        await workflow_service.validate_for_update(workflow, proposed, user, error_format)
    except (ValidationException, NotFoundException, ExtendedHTTPException):
        raise
    except Exception as e:
        details = e.args[0] if error_format == WorkflowErrorFormat.JSON and e.args and isinstance(e.args[0], dict) else str(e).strip()
        raise ExtendedHTTPException(code=status.HTTP_400_BAD_REQUEST, message=WORKFLOW_CONFIGURATION_ERROR, details=details, help="") from e
```

  Note: `except ExtendedHTTPException: raise` is required so the autonomous-mode 403 propagates rather than being re-wrapped to 400 by the generic `except Exception` handler.

- [ ] **Step 3: Refactor `update_workflow` (lines 416–467)**

  After the inner `parse_execution_config` try/except (lines 405–414), replace the inlined five-step pipeline and its outer `except ValidationException/NotFoundException/Exception` block (lines 416–467) with:

  `logger.debug(f"Update workflow. Request: {request}")`, then `await _run_pre_persist_update_validation(workflow, updated_config, user, error_format)`, then `updated_workflow = workflow_service.update_workflow(...)`. The guardrail sync, `background_tasks.add_task`, and return statement are unchanged. The helper now owns the error handling that was in the removed except block.

- [ ] **Step 4: Regression run**

  Run: `pytest tests/codemie/rest_api/routers/test_workflow.py -k update_workflow -v`
  Expected: all PASS (including the `test_update_workflow` assertion that `update_workflow` was called once).

---

### Task 2: Add POST validate endpoint and tests

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow.py` (add endpoint after `update_workflow`)
- Modify: `tests/codemie/rest_api/routers/test_workflow.py` (add 5 test functions at end)

**Interfaces:**
- Consumes: `_run_pre_persist_update_validation` from Task 1.
- Produces: `POST /v1/workflows/{workflow_id}/validate` → 200 `{"message": "Workflow configuration is valid"}`.

**Test-first:** Yes — write tests first, confirm 404/405 failure, add endpoint, confirm all pass.

- [ ] **Step 1: Write five failing tests** — add to `test_workflow.py` following the `test_update_workflow` pattern at line 384 (`@pytest.mark.asyncio`, `@patch("codemie.core.ability.Ability.can")`, `@patch("...WorkflowService.get_workflow")`, `AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")`). All POST to `f"/v1/workflows/{workflow_id}/validate"`.

  **a. `test_validate_workflow_valid_config`** — `mock_get_wf=workflow_config_data`, `mock_ability=True`, patch `validate_for_update` (AsyncMock) as `mock_validate`, patch `update_workflow` as `mock_update`, patch `project_access_check`. Assert status 200, `response.json()["message"] == "Workflow configuration is valid"`, `mock_validate.assert_called_once()`, `mock_update.assert_not_called()`.

  **b. `test_validate_workflow_missing_assistant`** — patch `validate_for_update` with `side_effect=ValidationException("Assistant 'missing-id' does not exist")`, patch `project_access_check`. `mock_get_wf=workflow_config_data`, `mock_ability=True`. Assert status 400, `"Assistant 'missing-id' does not exist"` in `response.json()["error"]["message"]`.

  **c. `test_validate_workflow_no_permissions`** — `mock_get_wf=workflow_config_data`, `mock_ability=False`. Assert status 401, `response.json()["error"]["message"] == "Access denied"`.

  **d. `test_validate_workflow_not_found`** — `mock_get_wf.side_effect=KeyError()`. No ability patch. Assert status 404, `response.json()["error"]["message"] == "Workflow not found"`.

  **e. `test_validate_global_workflow_calls_validate_for_update`** — `mock_get_wf` returns `WorkflowConfig(id="workflow_123", name="Published Workflow", description="...", yaml_config=test_yaml_config, project="demo", is_global=True)`, `mock_ability=True`, patch `validate_for_update` (AsyncMock, no side_effect) as `mock_validate`, patch `project_access_check`. Assert status 200, `mock_validate.assert_called_once()`.

- [ ] **Step 2: Confirm failure**

  Run: `pytest tests/codemie/rest_api/routers/test_workflow.py -k validate_workflow -v`
  Expected: 5 FAILED (404 or 405 — route not yet registered).

- [ ] **Step 3: Add endpoint** — insert in `workflow.py` immediately after `update_workflow` (after current line 467):

```python
@router.post("/workflows/{workflow_id}/validate", status_code=status.HTTP_200_OK, response_model=BaseResponse, response_model_by_alias=True)
async def validate_workflow(
    workflow_id: str, request: UpdateWorkflowRequest, user: User = Depends(authenticate),
    error_format: WorkflowErrorFormat = Query(WorkflowErrorFormat.STRING, description="Error format: 'string' or 'json'"),
):
    try:
        workflow = workflow_service.get_workflow(workflow_id=workflow_id)
    except Exception:
        raise_not_found(resource_id=workflow_id, resource_type="Workflow")
    project_access_check(user, request.project)
    if not Ability(user).can(Action.WRITE, workflow):
        raise_access_denied("validate")
    try:
        proposed = WorkflowConfig(**request.model_dump())
        proposed.parse_execution_config()
    except Exception as e:
        raise ExtendedHTTPException(code=status.HTTP_400_BAD_REQUEST, message=WORKFLOW_CONFIGURATION_ERROR, details=str(e).strip(), help="") from e
    await _run_pre_persist_update_validation(workflow, proposed, user, error_format)
    return {"message": "Workflow configuration is valid"}
```

- [ ] **Step 4: Run validate tests**

  Run: `pytest tests/codemie/rest_api/routers/test_workflow.py -k validate_workflow -v`
  Expected: 5 PASS.

- [ ] **Step 5: Full suite**

  Run: `pytest tests/codemie/rest_api/routers/test_workflow.py -v`
  Expected: all previously passing tests still PASS, 5 new tests PASS.
