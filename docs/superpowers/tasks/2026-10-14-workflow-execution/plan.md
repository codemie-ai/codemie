# Chat Stop aborts the underlying workflow execution (EPMCDME-11735) Implementation Plan

**Goal:** `POST /conversations/{id}/abort` also aborts the conversation's IN_PROGRESS workflow executions via the existing `WorkflowExecutionService.abort()`.

**Architecture:** Conversation abort router -> new lookup on `WorkflowExecution` -> new service helper that reuses `abort()` (DB status flip, sub-workflow cascade, nodes stop via existing polling). No runtime, node, or frontend change.

**Conventions:** Tests in pytest with `unittest.mock`, run via docker/WSL. Commit per task using the repository's existing convention.

---

### Task 1: Lookup of in-progress executions by conversation

**Files:** modify `src/codemie/core/workflow_models/workflow_execution.py` (next to `get_by_execution_id`, ~line 187); test in `tests/codemie/core/` alongside existing workflow execution model tests.

- Add classmethod `get_in_progress_by_conversation_id(conversation_id: str)` using `cls.get_all_by_fields({"conversation_id": conversation_id})` then keeping only `overall_status == WorkflowExecutionStatusEnum.IN_PROGRESS`. Return `[]` for an empty id.

Test-first: yes — mixed IN_PROGRESS/SUCCEEDED/FAILED/ABORTED rows for one conversation: only the IN_PROGRESS one is returned.

### Task 2: Service helper that aborts a conversation's executions

**Files:** modify `src/codemie/service/workflow_execution/workflow_execution_service.py` (new staticmethod after `abort`, ~line 158); test in `tests/codemie/service/workflow_execution/test_workflow_execution_service.py`.

- Add `abort_by_conversation_id(conversation_id: str, user: User) -> int`: for each execution from Task 1, load config with `WorkflowService().get_workflow(execution.workflow_id)` (deferred import, same circular-import reason as `_abort_active_sub_execution`), build `WorkflowExecutionService(workflow_config, execution.execution_id, user).abort()`. Catch and log per-execution exceptions so one failure does not skip the others; return the number aborted.

Test-first: yes — with two IN_PROGRESS executions, `abort()` is called for each; when the first raises, the second is still aborted; with none, nothing is called and 0 returned.

### Task 3: Wire into the conversation abort endpoint

**Files:** modify `src/codemie/rest_api/routers/conversation/streaming.py:64-80`; test in `tests/codemie/rest_api/routers/test_assistant_refresh_flow.py` next to `test_abort_endpoint_success`.

- After the `GenerationManager().abort(...)` call and before the INTERRUPTED marking, call `WorkflowExecutionService.abort_by_conversation_id(conversation_id, user)` inside its own `try/except Exception` that logs with `logger.error`. Permission check (`Action.WRITE` on the conversation) stays unchanged.

Test-first: yes — endpoint calls the helper with the conversation id and user; when the helper raises, response is still success and the last in-progress message is marked INTERRUPTED; for a conversation with no executions the response is unchanged.

### Task 4: ABORTED must not be overwritten by FAILED

**Files:** modify `src/codemie/service/workflow_execution/workflow_execution_service.py:60-83` (`fail`); test in the same service test file.

- After `_refresh_workflow_execution()` in `fail()`, if the refreshed `overall_status` is ABORTED, log and return without setting FAILED (mirrors the guard `finish()` already has at line ~260). `workflow.py:1274` calls `fail()` from `_handle_task_exception`, so an exception raised while stopping an aborted run would otherwise flip the status.

Test-first: yes — `fail()` on an execution whose status is ABORTED leaves it ABORTED and does not call `update`; `fail()` on IN_PROGRESS still sets FAILED.

### Task 5: Regression tests for completed runs and the workflow abort router

**Files:** `tests/codemie/rest_api/routers/test_workflow_executions.py` (add the router test); `tests/codemie/service/workflow_execution/test_workflow_execution_service.py` (completed case).

- Router: `PUT /v1/workflows/{workflow_id}/executions/{execution_id}/abort` returns 200 and calls `WorkflowExecutionService.abort` when the user has READ; returns access denied when not (router at `workflow_executions.py:375`, currently untested).
- Completed: `abort_by_conversation_id` for a conversation whose only executions are SUCCEEDED/FAILED/ABORTED does not call `abort()` and leaves statuses unchanged (AC3).

Test-first: no — adds coverage only for behavior implemented in Tasks 1-4 and existing router code.

---

**Negative-constraint pass:** Non-goals honored: no change to stream-disconnect (no task touches `routers/utils.py` or `assistant_handlers.py`); no `GenerationManager` registration or thread interruption (Task 2 reuses DB-based `abort()`); `abort_workflow_execution` permissions/URL untouched (Task 5 only tests it); no new endpoint or frontend change (Task 3 edits the existing endpoint); resume/delete/sub-workflow semantics untouched (cascade reused as is). Requirement 4 (failure must not fail the endpoint or skip INTERRUPTED marking) honored by Task 3's separate try/except placed before the marking.
