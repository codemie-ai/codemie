# Technical Research

**Task**: workflow chat stop cancellation execution
**Generated**: 2026-10-14
**Research path**: codegraph

---

## 1. Original Context

Jira EPMCDME-11735, Bug
Summary: Stopping workflow chat does not stop the underlying workflow execution.
Description: Users should be able to stop workflow execution when they stop a workflow running from chat. Currently, if a workflow is started via chat and the user clicks stop during execution, only the chat is stopped while the underlying workflow continues running. Inconsistent behavior, unintended background execution and resource consumption.
Steps: open a workflow, create any loop, save, click "Start chat", send a message to trigger execution, click "Stop execution" in chat during the run, check workflow execution state.
Expected: Stopping chat stops the underlying workflow execution; it does not continue in background; chat stop action is propagated consistently to workflow runtime.
Actual: Chat is stopped for the user; underlying workflow execution continues running.
Affected areas: Workflow chat integration; workflow execution cancellation logic; chat stop action handling; workflow runtime state synchronization.
Acceptance criteria:
- When a workflow is triggered from chat and user clicks stop, the underlying workflow execution is stopped as well.
- Workflow execution does not continue in background after chat stop action.
- Chat UI and workflow execution state remain consistent after stop action.
- Users can reliably interrupt workflow runs started from chat.
- Regression testing confirms correct behavior for start, stop, and completed workflow chat executions.

---

## 2. Codebase Findings

### Existing Implementations
Two independent abort paths exist today, not connected to each other:

- Workflow abort (works for the workflow API):
  - `src/codemie/rest_api/routers/workflow_executions.py:375` `abort_workflow_execution`, `PUT /v1/workflows/{workflow_id}/executions/{execution_id}/abort`. Checks `Ability.READ` on the workflow config, then calls `WorkflowExecutionService(...).abort()`. No test found within 3 caller hops by codegraph.
  - `src/codemie/service/workflow_execution/workflow_execution_service.py:125` `abort()`: under `workflow_execution_lock`, sets `overall_status = ABORTED`, recomputes tokens, `update(refresh=True)`, `_cleanup_inflight_states(ABORTED)`, sends metric, clears request summary, then `_abort_active_sub_execution()` (line 159), which cascades to the child via `active_sub_execution_id` unless the child status is in `_TERMINAL_STATUSES`.
  - `finish()` (line 260) skips marking SUCCEEDED when status is already ABORTED. `fail()` (line 60) sets FAILED.
  - Runtime cooperation is polling, not thread interruption: `src/codemie/workflows/nodes/base_node.py:737` `_is_execution_aborted()` reads the DB row status; `_run_success_path` (line 294) checks it before the node starts (raises `ExecutionAbortedException`, line 85) and after `execute` (marks the state ABORTED). `src/codemie/workflows/nodes/sub_workflow_node.py:141` `_handle_child_outcome` raises `ExecutionAbortedException` when the child is ABORTED.
- Chat/conversation abort (does not touch workflow):
  - `src/codemie/rest_api/routers/conversation/streaming.py:47` `abort_conversation_generation`, `PUT /conversations/{conversation_id}/abort`. Loads `Conversation`, checks `Action.WRITE`, calls `GenerationManager().abort(conversation_id)`, then marks the last history message INTERRUPTED. It contains no reference to workflow executions.
  - `src/codemie/service/generation_manager.py:55` `GenerationManager.abort` (singleton registry `conversation_id -> set[ThreadedGenerator]`) calls `gen.close(reason=CancellationReason.ABORTED_BY_USER)` on registered generators.
  - `src/codemie/core/thread.py:172` `ThreadedGenerator.close` only flags `closed`, stores `cancellation_reason` and pushes `StopIteration` to queues. It does not stop producers.
- Workflow streaming:
  - `src/codemie/rest_api/routers/utils.py:163` `_handle_streaming_execution` registers `_handle_client_disconnect` (line 194), which closes the `ThreadedGenerator`. `_serve_workflow_stream` (line 201) submits `workflow.stream_to_client` to `producer_executor` and yields NDJSON, injecting `workflow_execution_id` into each chunk. Closing the generator does not call `WorkflowExecutionService.abort()`, and the producer thread keeps running.
  - `src/codemie/workflows/workflow.py:956` `_execute_workflow_stream` runs the graph, in `finally` closes `thought_queue`, calls `finish()` only when `workflow_succeeded`, and deletes virtual assistants.
- Assistant chat side: `src/codemie/rest_api/handlers/assistant_handlers.py:698` `_handle_client_disconnect` is deliberately passive (disconnect does not cancel; page refresh must keep generating). `AssistantChatRequest` in `src/codemie/core/models.py` has a `workflow_execution_id` field (description "Identifier for the workflow execution"), covered only by serialization tests.
- I did not locate (not shown by codegraph) the exact code that maps "workflow Start chat" to a conversation id and execution id, nor the UI call made by "Stop execution". `CreateWorkflowExecutionRequest.conversation_id` (`src/codemie/core/workflow_models/workflow_execution.py:312`) and `WorkflowExecution.conversation_id` (line 172) link an execution to a conversation. Further exploration of `create_workflow_execution` (workflow_executions.py:238) and `WorkflowService.create_workflow_execution` is needed to confirm which endpoint the chat Stop button calls.

### Architecture and Layers Affected
- API/router layer: `routers/workflow_executions.py`, `routers/conversation/streaming.py`, `routers/utils.py`.
- Service layer: `WorkflowExecutionService`, `WorkflowService`, `GenerationManager`.
- Workflow runtime: `WorkflowExecutor` (`workflows/workflow.py`), `BaseNode`, `SubWorkflowNode`.
- Data layer: `WorkflowExecution`, `WorkflowExecutionState` (SQLModel, `core/workflow_models/workflow_execution.py`); `Conversation`.

### Integration Points
- `workflow_execution_service` and `workflow_service` have a circular dependency worked around with deferred imports (`_abort_active_sub_execution`).
- Abort status travels through the DB row (`WorkflowExecution.overall_status`), read by nodes, so it works across threads and pods without in-process signalling.
- `WorkflowExecution.conversation_id` and `parent_execution_id`/`active_sub_execution_id` are the existing linkage fields.

### Patterns and Conventions
- Routers check `Ability(user).can(Action.X, entity)` then `raise_access_denied`.
- Status enum `WorkflowExecutionStatusEnum` includes ABORTED; terminal-status checks use `_TERMINAL_STATUSES`.
- Layered architecture, service orchestration, workflow behavior in node packages (guide `langgraph-workflows.md`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/workflows/langgraph-workflows.md`: extend `WorkflowExecutor`/nodes, do not create a separate graph execution path. Other P0 guides per AGENTS.md: architecture/layered-architecture.md, testing/testing-patterns.md.

### Architectural Decisions
None found beyond the inline docstring of `_abort_active_sub_execution` (deferred import) and the passive-disconnect comment in `assistant_handlers.py:698`.

### Derived Conventions
Cancellation = DB status flip plus cooperative polling at node boundaries; there is no thread cancellation.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/workflow_execution/test_workflow_execution_service.py`: covers `abort` (for example `test_completed_at_set_on_aborted_states`, per-state save failure tolerance).
- `tests/codemie/workflows/test_base_node_lifecycle.py`: `ExecutionAbortedException` / `aborting_execute` transition recording.
- `tests/codemie/rest_api/routers/test_routers_utils.py`: `_serve_workflow_stream`, `_handle_client_disconnect`.
- `tests/codemie/rest_api/routers/test_workflow_executions.py`, `test_workflow_executions_delete_on_completion.py`, `test_resume_workflow_execution.py`: router tests.
- `tests/codemie/rest_api/routers/test_assistant_refresh_flow.py`: `GenerationManager` abort and the conversation abort endpoint (`test_abort_endpoint_success`).
- `tests/codemie/core/test_models_workflow_execution_id.py`: `AssistantChatRequest.workflow_execution_id` serialization only.

### Testing Framework and Patterns
pytest with `unittest.mock` (`patch`, `MagicMock`), session mocking for SQLModel. Tests run via docker per the user's memory notes.

### Coverage Gaps
- `abort_workflow_execution` router has no test within 3 hops.
- No test links conversation abort to workflow execution status.
- No test for generator close mid-run causing the workflow to stop.

---

## 5. Configuration and Environment

### Environment Variables
`THREAD_POOL_MAX_WORKERS` (producer pool in `routers/utils.py`). No cancellation-specific variable found.

### Configuration Files
None specific. Codegraph config query returned nothing cancellation-related.

### Feature Flags and Deployment Concerns
None found. Multi-pod: because status lives in the DB, abort works across pods; `GenerationManager` is per-process in-memory.

---

## 6. Risk Indicators

- Two decoupled abort mechanisms (conversation `GenerationManager` vs `WorkflowExecutionService.abort`); `abort_conversation_generation` does not reference workflow executions (`streaming.py:47`).
- Cooperative cancellation only: an in-progress node (long LLM or tool call, loop body) runs to completion before the next `_is_execution_aborted` check (`base_node.py:302`, `316`).
- `_execute_workflow_stream` `finally` and `finish()` are guarded for ABORTED, but `fail()` and the exception path (`_handle_task_exception`) may overwrite ABORTED with FAILED; unverified.
- `GenerationManager` registry is in-process, so a stop request hitting another pod would not find the generator.
- Not verified: which endpoint the chat Stop button calls and how conversation_id maps to execution_id for workflow chat; `abort_workflow_execution` permission check is READ, not WRITE.
- Speculative: the fix may need a lookup from conversation to its in-progress `WorkflowExecution` (via `WorkflowExecution.conversation_id`, an existing indexed field) in the conversation abort path; spec must decide.
- Speculative: loop-based workflows have more node boundaries, but a single long node may delay effective stop.
- Test gaps listed in Section 4.

---

## 7. Summary for Complexity Assessment

The change is localized to the router and service layers (conversation abort path, `WorkflowExecutionService.abort`, possibly `routers/utils.py` stream disconnect handling), with the runtime already honoring ABORTED status through DB polling in `BaseNode`. Existing machinery is reusable: `WorkflowExecutionService.abort()` already cascades to sub-workflows, and `WorkflowExecution.conversation_id` already links an execution to a conversation. Expected file surface is small (roughly 2 to 4 source files plus tests), with no novel patterns evidenced in the code.

Uncertainty is mainly in the unverified chat-to-execution wiring (which endpoint the Stop button calls and whether a workflow chat uses a conversation id at all), the interplay between ABORTED and FAILED/finish terminal states, and cooperative-only cancellation timing. Multi-pod behavior is safe for workflow abort (DB-backed) but not for `GenerationManager`.

Test posture: the service `abort()` and node abort handling are tested; the workflow abort router and the chat-stop-to-workflow link are not. Regression tests for start, stop and completed chat flows would be new.

---

## 8. External References

None named by the task.
