# Spec: Chat Stop aborts the underlying workflow execution (EPMCDME-11735)

## Problem

A workflow started from chat creates a `WorkflowExecution` whose `conversation_id` is the chat's
conversation id (`workflow_service.py:235-327`, `workflow_execution.py:172`, indexed). The chat Stop
action calls `POST /conversations/{conversation_id}/abort`
(`routers/conversation/streaming.py:43-80`). That handler only calls `GenerationManager().abort(...)`
and marks the last history message INTERRUPTED. The workflow stream's `ThreadedGenerator` is created
with `conversation_id=execution.execution_id` and is never registered with `GenerationManager`
(`workflow_executions.py:302-306`), so the call finds nothing. Nothing flips
`WorkflowExecution.overall_status`, so the nodes (which poll that status, `base_node.py:737`) keep running.

The workflow-side abort (`PUT /workflows/{workflow_id}/executions/{execution_id}/abort`) already works
but is not reached from the chat Stop path.

### Frontend findings (codemie-ui, confirmed)

- The chat prompt Stop button (`ChatPrompt.tsx` `handleStopGeneration`) calls
  `chatGenerationStore.stopChatGeneration(chatId)` (`store/chatGeneration.ts:980-1016`). It aborts the
  local fetch controller, stops completion polling, shows a "generation cancelled" toast, then fires
  `POST v1/conversations/{chatId}/abort` and ignores errors. It never calls the workflow abort endpoint.
- For workflow chats it freezes progress and does not call `interruptChatHistory`, so the UI shows
  cancelled immediately while the execution continues. This is the reported symptom.
- A second path exists: the `ChatControls.tsx` "Cancel" button calls `abortWorkflowChat`
  (`chatGeneration.ts:1215`), which does `PUT v1/workflows/{assistantId}/executions/{executionId}/abort`
  using the last message's executionId. It already aborts the workflow and is not what Stop uses.

Conclusion: the backend conversation abort endpoint is the correct and sufficient fix point. No
frontend change is needed, because Stop already calls that endpoint.

## Approach

Make the conversation abort endpoint propagate to the workflow runtime. After the existing
`GenerationManager` abort, find the conversation's in-progress workflow execution(s) via
`WorkflowExecution.conversation_id` and abort each through the existing
`WorkflowExecutionService.abort()` (which sets ABORTED in the DB, cleans in-flight states and cascades
to an active sub-workflow). The runtime then stops at the next node boundary through existing DB polling.
No new runtime mechanism and no change to node code.

Rejected: aborting on stream disconnect (page refresh must keep running, per the passive-disconnect
design at `assistant_handlers.py:698`); registering the workflow generator in `GenerationManager`
(per-process, does not stop producers, would not cross pods); thread interruption (the codebase uses
cooperative cancellation only).

## Requirements

1. A lookup by `conversation_id` returns executions that are IN_PROGRESS (only). It is a new
   repository/service method, not inline SQL in the router.
2. `abort_conversation_generation` calls it after the generator abort and aborts each match via
   `WorkflowExecutionService`, which needs the workflow config and user.
3. Only IN_PROGRESS executions are touched. `abort()` itself does not guard terminal states, so the
   filter is what keeps SUCCEEDED, FAILED and already-ABORTED executions unchanged.
4. A failure aborting the workflow is logged and must not fail the endpoint or skip the
   INTERRUPTED marking of the chat message (same tolerance as the existing try/except).
5. Permission is unchanged: `Action.WRITE` on the conversation. No new access path.
6. Chat and execution stay consistent: after Stop, the last message is INTERRUPTED and the execution
   is ABORTED. Verify that the runtime's `finally`/`finish()` (`workflow.py:956`) does not later turn
   ABORTED into SUCCEEDED or FAILED. Fix it only if a gap is found (`finish()` already skips ABORTED).
7. Stop with no running workflow (plain assistant chat, or a finished workflow) behaves exactly as today.

## Acceptance criteria

- AC1: Stop on a workflow chat with a running execution sets that execution to ABORTED.
- AC2: After Stop, no further nodes start; the execution does not return to IN_PROGRESS or
  end up SUCCEEDED.
- AC3: Stop on a chat whose execution already finished (SUCCEEDED, FAILED, ABORTED) leaves the
  execution status unchanged and still returns success.
- AC4: Stop on a non-workflow conversation behaves as before.
- AC5: If the workflow abort raises, the endpoint still returns success and marks the message INTERRUPTED.
- AC6: An active sub-workflow is aborted too (via the existing cascade).
- AC7: Regression tests cover: start (execution created, IN_PROGRESS), stop (ABORTED), completed
  (unchanged), plus a router test for `abort_workflow_execution`, which had none.

## Non-goals

- No change to stream-disconnect behavior (refresh or tab close does not abort).
- No hard preemption of a node that is mid-LLM or mid-tool call; it ends at the next boundary.
- No cross-pod `GenerationManager` redesign.
- No change to `abort_workflow_execution` permissions (READ) or its URL/contract.
- No UI/frontend change (not needed, see Frontend findings); no new endpoint.
- The `ChatControls` "Cancel" path (`abortWorkflowChat`) is out of scope and unchanged.
- No change to resume, delete, or sub-workflow semantics.

## Risks

- The UI ignores errors from the conversation abort call, so a backend failure is silent to the user;
  the backend must log it (requirement 4).
- Cooperative cancellation delays the effective stop for a long running node.
- The `fail()`/exception path may overwrite ABORTED with FAILED (unverified); covered by requirement 6.
