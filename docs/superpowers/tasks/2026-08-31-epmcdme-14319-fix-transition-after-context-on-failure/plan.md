# Implementation Plan - EPMCDME-14319: Fix Workflow Execution Context Viewer Showing Previous Node After Context as Terminal/Empty on Failure

## Goal Description
In CodeMie Workflows, when a workflow node fails, inspecting the **State transition for context** viewer of the *previous state* displays `After Context (Terminal)` with an empty JSON object `{}`. 

The goal is to ensure that the previous state's actual **After Context** (the exact state snapshot produced by that state and passed into the failing state) is preserved, persisted, and displayed accurately to allow debugging failed nodes, while ensuring truly terminal states in successful executions still behave correctly.

---

## Proposed Changes
- Move state transition recording from `_run_success_path` into a dedicated helper `_record_incoming_transition`.
- Call `_record_incoming_transition` inside `__call__()` right after `self.workflow_execution_service.start_state()`. This guarantees that transitions are persisted regardless of whether node execution succeeds, fails, aborts, or requires MCP authentication.
- Remove the delayed `record_transition` call from `_run_success_path()` to prevent duplicate transition writes.

---

## Verification Plan
1. Run base node lifecycle unit tests inside the backend container:
   ```bash
   podman exec codemie-dev-codemie-1 poetry run pytest tests/codemie/workflows/test_base_node_lifecycle.py
   ```
2. Run transition index service tests:
   ```bash
   podman exec codemie-dev-codemie-1 poetry run pytest tests/codemie/service/workflow_execution/test_workflow_execution_transitions_index_service.py
   ```
3. Run router transition API tests:
   ```bash
   podman exec codemie-dev-codemie-1 poetry run pytest tests/codemie/rest_api/routers/test_workflow_execution_transitions.py
   ```
