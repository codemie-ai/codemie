# Technical Analysis - EPMCDME-14319: Fix Workflow Execution Context Viewer Showing Previous Node After Context as Terminal/Empty on Failure

## Root Cause Analysis
In `base_node.py`, the state transition recording `self.workflow_execution_service.record_transition()` was originally invoked *only* inside `_run_success_path()`, which executes after `self.execute()` and `self.post_process_output()` finish successfully.
When a node starts, if it fails, aborts, times out, or requires MCP authentication during `self.execute()`, execution jumps straight to exception handling. The transition record from the previous node is never saved to the database. Consequently, the UI receives a 404, treats the previous node as terminal, and displays `After Context (Terminal): {}`.

## Resolution
By moving the recording logic to `_record_incoming_transition` and calling it immediately at node start inside `__call__()` right after `start_state()`, the transition is safely persisted before execution begins. This guarantees the previous node's output context is preserved and viewable for debugging.

## Risk Indicators
- **UI/Component Scope**: Zero risk. This is a pure backend orchestration change inside the workflow base execution layer.
- **Data Model Compatibility**: Complete compatibility. Reuses the existing `record_transition` database structure and schemas.
