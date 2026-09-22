# Fix PUT /workflows/{id} Guardrail Pre-Persist Validation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent workflow DB writes when guardrail validation fails on PUT /workflows/{id}.

**Architecture:** Add a `dry_run=True` call to `GuardrailService.sync_guardrail_assignments_for_entity` inside `_run_pre_persist_update_validation` so all guardrail existence/permission/project checks run before `workflow_service.update_workflow()` commits to the DB. The real (non-dry_run) sync at the existing call site stays unchanged and fires only after the write succeeds.

**Tech Stack:** Python, FastAPI, SQLModel, pytest-asyncio, unittest.mock.

## Global Constraints

- Single source file change: `src/codemie/rest_api/routers/workflow.py` only.
- Commit per task using the repository's existing convention.

---

## Acceptance criteria

- [ ] PUT /workflows/{id} returns 400 and does NOT persist workflow changes when guardrail validation fails (non-existent guardrail, cross-project assignment, insufficient permission).
- [ ] Successful PUT with valid guardrail assignments continues to persist the workflow and execute the real guardrail sync after the write.

---

### Task 1: Guard DB write behind guardrail dry-run check

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow.py:48, 101-127, 453`
- Test: `tests/codemie/rest_api/routers/test_workflow.py`

**Interfaces:**
- Consumes: `GuardrailService.sync_guardrail_assignments_for_entity(user, entity_type, entity_id, entity_project_name, guardrail_assignments, dry_run=False)` — already present at the module level.
- Produces: updated `_run_pre_persist_update_validation(workflow, proposed, user, error_format, guardrail_assignments=None)` signature; callers must pass `request.guardrail_assignments`.

**Test-first: yes** — `test_update_workflow_guardrail_dry_run_blocks_persist`: patches `sync_guardrail_assignments_for_entity` to raise on any call, patches `update_workflow` as a `MagicMock`, then asserts `update_workflow.assert_not_called()` and `response.status_code == 400`.

- [ ] **Step 1: Write the failing test**

Add the following test to `tests/codemie/rest_api/routers/test_workflow.py` after the existing `test_update_workflow_guardrail_sync_403_wrapped_as_400` block:

```python
@pytest.mark.asyncio
@patch("codemie.core.ability.Ability.can")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
async def test_update_workflow_guardrail_dry_run_blocks_persist(
    mock_get_wf,
    mock_ability,
    update_workflow_request,
    request_header,
):
    """Guardrail dry-run failure must prevent update_workflow from being called."""
    from unittest.mock import AsyncMock, MagicMock

    mock_get_wf.return_value = workflow_config_data
    mock_ability.return_value = True

    guardrail_error = ExtendedHTTPException(
        code=status.HTTP_400_BAD_REQUEST,
        message="Guardrail validation failed",
        details="Cross-project assignment not allowed",
        help="",
    )
    mock_update_workflow = MagicMock()

    with (
        patch(
            "codemie.service.guardrail.guardrail_service.GuardrailService.sync_guardrail_assignments_for_entity",
            side_effect=guardrail_error,
        ),
        patch(
            "codemie.service.workflow_service.WorkflowService.update_workflow",
            mock_update_workflow,
        ),
        patch(
            "codemie.service.workflow_service.WorkflowService.validate_for_update",
            new_callable=AsyncMock,
        ),
        patch("codemie.rest_api.routers.workflow.project_access_check"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put(
                f"/v1/workflows/{workflow_config_data.id}",
                json=update_workflow_request.model_dump(),
                headers=request_header,
            )

    mock_update_workflow.assert_not_called()
    assert response.status_code == status.HTTP_400_BAD_REQUEST
```

- [ ] **Step 2: Run the test to confirm it fails**

```
pytest tests/codemie/rest_api/routers/test_workflow.py::test_update_workflow_guardrail_dry_run_blocks_persist -v
```

Expected: FAIL — `AssertionError` on `mock_update_workflow.assert_not_called()` because `update_workflow` IS currently called before the guardrail check raises.

- [ ] **Step 3: Extend the guardrail import to include `GuardrailAssignmentItem`**

At `src/codemie/rest_api/routers/workflow.py:48`, change:

```python
from codemie.rest_api.models.guardrail import GuardrailEntity
```

to:

```python
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem, GuardrailEntity
```

- [ ] **Step 4: Add `guardrail_assignments` parameter and dry-run call to `_run_pre_persist_update_validation`**

At `src/codemie/rest_api/routers/workflow.py:101-126`, replace the function signature and add the dry-run call inside the existing `try` block, before the MCP/autonomous checks:

```python
async def _run_pre_persist_update_validation(
    workflow: WorkflowConfig,
    proposed: WorkflowConfig,
    user: User,
    error_format: WorkflowErrorFormat,
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
) -> None:
    """MCP, autonomous-mode, executor, and guardrail validation without persisting."""
    try:
        GuardrailService.sync_guardrail_assignments_for_entity(
            user=user,
            entity_type=GuardrailEntity.WORKFLOW,
            entity_id=str(workflow.id),
            entity_project_name=workflow.project,
            guardrail_assignments=guardrail_assignments,
            dry_run=True,
        )
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
        details = (
            e.args[0]
            if error_format == WorkflowErrorFormat.JSON and e.args and isinstance(e.args[0], dict)
            else str(e).strip()
        )
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST, message=WORKFLOW_CONFIGURATION_ERROR, details=details, help=""
        ) from e
```

- [ ] **Step 5: Pass `request.guardrail_assignments` at the call site**

At `src/codemie/rest_api/routers/workflow.py:453`, change:

```python
await _run_pre_persist_update_validation(workflow, updated_config, user, error_format)
```

to:

```python
await _run_pre_persist_update_validation(workflow, updated_config, user, error_format, request.guardrail_assignments)
```

- [ ] **Step 6: Run the test to confirm it passes**

```
pytest tests/codemie/rest_api/routers/test_workflow.py::test_update_workflow_guardrail_dry_run_blocks_persist -v
```

Expected: PASS.

- [ ] **Step 7: Run the full router test suite to confirm no regressions**

```
pytest tests/codemie/rest_api/routers/test_workflow.py -v
```

Expected: all existing tests pass alongside the new one.

---

## Self-Review

**Spec coverage:**
- "Nothing committed to DB if guardrail validation fails" — Task 1 Step 4 adds the dry_run call inside `_run_pre_persist_update_validation`, which runs before `workflow_service.update_workflow()` at line 455. If the dry_run raises, the DB write is never reached. ✓
- "Use existing `dry_run=True` parameter" — the fix calls the existing parameter; no new abstractions. ✓
- "Real (non-dry_run) sync stays in place" — the existing call at lines 457-463 is not touched. ✓
- "Router-layer test asserting update_workflow NOT called" — Task 1 Step 1 covers this. ✓

**Negative constraints:**
- No negative constraints stated explicitly. The requirements say "use it before the update_workflow call" — the plan places the dry_run call first inside `_run_pre_persist_update_validation`, satisfying the ordering requirement. No task removes the real sync call or skips it on the happy path.
- `negative-constraints: none stated beyond the implicit "do not remove the existing real sync call"` — confirmed no task violates this.

**Placeholder scan:** No TBD, TODO, or vague steps present.

**Type consistency:** `GuardrailAssignmentItem` added to the import in Step 3 before it is used in Step 4's signature. `Optional[List[GuardrailAssignmentItem]]` matches the type used in `UpdateWorkflowRequest.guardrail_assignments` (defined in `workflow_models.py:437`).
