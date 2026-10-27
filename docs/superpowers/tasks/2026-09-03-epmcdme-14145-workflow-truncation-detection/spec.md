# Spec: Workflow state output can be silently truncated without a truncated flag

**Ticket**: EPMCDME-14145
**Date**: 2026-09-03
**Last revised**: 2026-10-16 (single `truncated` flag; `finish_reason` removed; output edits clear the flag)

## Problem

Final assistant answers may be cut mid-generation by a model's token limit and are still stored as a `SUCCEEDED` workflow state output, because the existing truncation detector only runs on tool-call messages. Callers of the state output API get a `200` response with incomplete content and no signal that anything is wrong.

Root cause: `LangGraphEventAdapter.parse_message_type` and `parse_supervisor_message_type` (`src/codemie/agents/langgraph_event_adapter.py`) each have an `elif self.agent.is_finish_reason_stop(message):` branch that calls `_on_llm_end` directly for the terminal (non-tool-call) assistant message, without ever invoking the truncation detector (`_safe_check_for_truncation`/`_get_truncation_indicator`) that the tool-call branches already use.

## Goals (Acceptance Criteria)

- Truncation detection runs on terminal messages (final assistant answers), not only tool-call messages.
- The state output API exposes a boolean `truncated` signal, not just a bare `output` string.
- The LiteLLM proxy path's `max_tokens` behavior is confirmed and covered by a regression test.
- Regression tests cover the final-answer `finish_reason=length` / `stop_reason=max_tokens` case across providers (OpenAI/LiteLLM, native Claude, Bedrock).
- Conversation history thoughts for `WorkflowState`-authored entries (`author_type: "WorkflowState"`) surface `truncated` too — both when a past conversation is retrieved/materialized, and live as a workflow streams.
- Editing a state's output clears its `truncated` flag, so the "may be incomplete" marker does not outlive the output it described.

## Key Design Decision: non-raising signal, not a raised exception

When a terminal (non-tool-call) message is detected as truncated, the workflow state is marked `SUCCEEDED` as today, but a `truncated` signal is persisted and surfaced via the API. The existing tool-call path — which raises `TokenLimitExceededException` and turns the state `FAILED` — is left unchanged; it protects a different failure mode (a tool call built from truncated arguments would break tool invocation) and should keep failing loudly.

Rationale: the ticket's Expected Result frames the fix as "Output is complete, **or** the API exposes a truncation signal", i.e. metadata on an otherwise-successful result, not a new way to fail the workflow. This also mirrors the existing precedent in `src/codemie/workflows/utils/utils.py` (`_truncated: True` flag on an oversized serialized state) rather than reusing FAILED-status machinery built for the tool-call failure class.

### Why only `truncated`, not the raw provider reason

An earlier revision also persisted the raw provider value (`finish_reason`: `"length"`, `"max_tokens"`). It was removed because `truncated` is always equal to "a raw reason was present": every write sets the two as a pair, so the raw value added a second column, extra model fields, and a UI tooltip, without adding information. It also stored provider-specific spellings (`length` from OpenAI/LiteLLM, `max_tokens` from Anthropic/Bedrock) for the same condition. The provider reason remains available in logs (`_get_truncation_indicator` debug strings).

## Scope

In scope:
- `LangGraphAgent` / `LangGraphEventAdapter` (the terminal-message detection gap — this is the only agent implementation the tech-analysis located the bug in).
- `TaskResult` (`src/codemie/agents/assistant_agent.py`).
- `AgentNode` / `BaseNode` (`src/codemie/workflows/nodes/`).
- `WorkflowExecutionService.finish_state` (`src/codemie/service/workflow_execution/workflow_execution_service.py`).
- `WorkflowUpdateOutputService._update_state` (`src/codemie/service/workflow_execution/workflow_update_output_service.py`) — clears `truncated` when the output is edited.
- `WorkflowExecutionState` / `WorkflowExecutionStateOutput` (`src/codemie/core/workflow_models/workflow_execution.py`) plus an Alembic migration.
- `get_workflow_execution_state_output` router (`src/codemie/rest_api/routers/workflow_executions.py`).
- A confirmatory test for LiteLLM proxy `max_tokens` forwarding (`src/codemie/enterprise/litellm/llm_factory.py::_filter_litellm_params`).
- `Thought` (`src/codemie/chains/base.py`).
- `_get_execution_thoughts` (`src/codemie/service/conversation/history_materializer.py`).
- `BaseNode._run_success_path`'s `on_node_end` callback invocation (`src/codemie/workflows/nodes/base_node.py`).
- `LanggraphNodeCallback.on_node_end` (`src/codemie/workflows/callbacks/graph_callback.py`).

Out of scope:
- `AIToolsAgent` (the separate AgentExecutor-based agent implementation, sibling to `LangGraphAgent`) — nothing in the ticket or the codebase research indicates it shares this gap; not touched.
- Any change to `config/llms/*.yaml` `features.max_tokens` flags — criterion 3 is scoped as verify-and-test only, unless the new test surfaces an actual mismatch, in which case that becomes explicit follow-up work, not silently folded into this fix.
- The existing tool-call truncation path's raise-and-fail behavior (`_safe_check_for_truncation`, `TokenLimitExceededException`) — unchanged.
- `AssistantRequestHandler._filter_thoughts` (`src/codemie/rest_api/handlers/assistant_handlers.py`) — confirmed unaffected. Chat-execution workflow assistant messages are persisted to `Conversation.history` with `thoughts=[]` directly from `workflow_service.py` (bypassing `save_chat_history`/`_filter_thoughts` entirely); the real thought list is reconstructed only at retrieval time by `_get_execution_thoughts`. `_filter_thoughts` belongs to the separate non-workflow chat save path and never sees `WorkflowState`-authored thoughts.

## Design

### 1. Non-raising terminal detector (agent/LLM integration layer)

Add `LangGraphAgent._detect_terminal_truncation(message: AIMessage) -> None`. It delegates to the existing `_get_truncation_indicator(response_metadata)` helper, which already knows the provider shapes (`finish_reason` in `{"length", "max_tokens"}` for OpenAI/LiteLLM; `stop_reason` / `stopReason == "max_tokens"` for Claude and Bedrock). The result is recorded on one instance attribute, instead of raising:

- `self._last_truncated: bool` — `True` when the terminal message is truncated, `False` otherwise.

The attribute is assigned on every terminal message, so a later non-truncated message in the same invoke resets a prior truncated turn's flag (per-supervisor-turn reset). It is also reset to `False` at the start of each `invoke_task` call before the graph runs. The detector fails open: unexpected errors are logged, never raised.

`LangGraphAgent.is_finish_reason_truncated(message: AIMessage) -> bool` is the gating predicate. It returns `True` when `_get_truncation_indicator` matches.

**Gating.** `LangGraphEventAdapter` dispatches on `is_finish_reason_stop(message) or is_finish_reason_truncated(message)`. A truncated terminal chunk usually has empty content and a non-`stop` reason, so the stop check alone would never reach the detector.

**Node restriction.** In `parse_message_type` the detector and `_on_llm_end` run only when the chunk's `langgraph_node` is `"agent"`. Structured-output and other nodes also emit terminal stop messages; without this restriction, a stop from `generate_structured_response` (which follows the agent) would reset the agent's flag and persist "not truncated" for a truncated answer. The supervisor path (`parse_supervisor_message_type`) is not yet node-restricted; see Risks.

The existing tool-call branches (`parse_update_type`, `handle_supervisor_tool_calls`) and their call to `_safe_check_for_truncation` (raise-based) are untouched.

### 2. Threading the signal into `TaskResult`

`TaskResult` (`src/codemie/agents/assistant_agent.py`) gains:

- `truncated: bool = False`

`LangGraphAgent.invoke_task` reads `self._last_truncated` after `_invoke_agent(...)` returns (and in the failure branch) and passes it into `TaskResult.from_agent_response(...)` / `TaskResult.failed_result(...)`. Both accept `truncated` and default it to `False` for every other call site, so no other caller changes behavior.

This only applies to the LangGraph-based agent path. `AIToolsAgent.invoke_task` is not modified.

### 3. Workflow orchestration → persistence

In `BaseNode._run_success_path` (`src/codemie/workflows/nodes/base_node.py`), after `processed_output = self.post_process_output(...)` runs, extract from the still-in-scope `raw_output`:

```python
truncated = getattr(raw_output, "truncated", False)
```

(`getattr` with a default is deliberate: not every node type's `raw_output` is a `TaskResult`, and this must not raise for those.)

Pass it into `WorkflowExecutionService.finish_state(...)`, whose signature gains one optional keyword argument:

```python
def finish_state(
    self,
    execution_state_id: str,
    output: str,
    status: WorkflowExecutionStatusEnum,
    truncated: bool = False,
) -> None:
```

`finish_state` persists it onto `state.truncated` alongside the existing `state.output` / `state.status` assignment, and includes it on the `WorkflowStateEvent` streamed to `thought_queue` for parity with the persisted row.

The two other existing call sites of `finish_state` (`_handle_execution_exception`, `_handle_mcp_auth_required`) are untouched — they don't pass the new kwarg, so the field defaults to `False` for those (unrelated) failure paths.

### 4. Data model + migration

Add to `BaseWorkflowExecutionState` (`src/codemie/core/workflow_models/workflow_execution.py`), so the table model `WorkflowExecutionState` and the read models that derive from it (`WorkflowExecutionStateResponse`, `WorkflowExecutionStateWithThougths`) all pick it up:

```python
truncated: bool = False
```

Add the same field explicitly to `WorkflowExecutionStateOutput` (a plain `BaseModel`, not derived from `BaseWorkflowExecutionState`):

```python
class WorkflowExecutionStateOutput(BaseModel):
    output: Optional[str] = None
    truncated: bool = False
```

The Alembic migration (`src/external/alembic/versions/t1r2u3n4c5a6_add_truncated_to_workflow_execution_states.py`, revision `t1r2u3n4c5a6`, revises `e5f9a1c3b7d2`) adds one column to `workflow_execution_states`: `truncated` (boolean, `server_default` `false`, not null). The branch-only migration was rewritten in place and renamed; it has not been released.

### 5. API

`get_workflow_execution_state_output` (`src/codemie/rest_api/routers/workflow_executions.py`) returns the output and flag from the same fetched row:

```python
state = WorkflowExecutionState.get_by_id(state_id)
return WorkflowExecutionStateOutput(output=state.output, truncated=state.truncated)
```

No route signature or status-code change — still `200`, with one additional boolean field that existing clients can ignore.

### 6. LiteLLM proxy `max_tokens` behavior (criterion 3)

Add a test on `_filter_litellm_params` (`src/codemie/enterprise/litellm/llm_factory.py`) asserting:
- a model with `features.max_tokens: true` in its `config/llms/*.yaml` entry forwards the configured `max_tokens` value unchanged through the filtered params, and
- a model with `features.max_tokens: false` has `max_tokens` filtered to `None`.

This confirms and documents existing behavior. No production code change to `llm_factory.py` is planned; if the test surfaces an actual mismatch (e.g. a wrongly-set flag in a model's YAML entry), that becomes explicit follow-up scope, not silently absorbed here.

### 7. Conversation thought surface (`WorkflowState`-authored thoughts)

Two independent construction sites currently build thought dicts/objects with `author_type: "WorkflowState"`, and both need the signal:

- **Retrieval/materialization path** — `history_materializer.py::_get_execution_thoughts` builds a plain dict per `WorkflowExecutionState` row when a past conversation is fetched. Add `"truncated": state.truncated` to that dict.
- **Live streaming path** — `graph_callback.py::LanggraphNodeCallback` builds a `Thought` directly during execution and finalizes/streams it in `on_node_end` at the same point `BaseNode._run_success_path` already computes `truncated` (Design §3) and calls `callback.on_node_end(...)`. Extend that call to pass `truncated=truncated`, and have `on_node_end` set it on `current_thought` before `self.gen.send(...)`.

Both sites populate the same `Thought` model (`chains/base.py`), which must declare `truncated` explicitly (mirroring the `WorkflowStateEvent` addition in Design §3) — otherwise Pydantic's default `extra="ignore"` silently drops it.

`AssistantRequestHandler._filter_thoughts` (`assistant_handlers.py`) was checked and is not on this path (see Scope, out of scope) — no change needed there.

### 8. Clearing the flag on output edit

`WorkflowUpdateOutputService._update_state` replaces `state.output` when a user edits a cut-off output. It also sets `state.truncated = False` in the same save, because the "may be incomplete" marker described the old text. The root `WorkflowExecutionStateThought` row is not updated: that table has no `truncated` column, and the chat history badge is derived from the state row, so clearing the state is sufficient for reloads.

A live thought already streamed to the client keeps its badge until the client reloads or clears it locally.

## Testing

- Extend `tests/codemie/agents/test_langgraph_event_adapter.py`:
  - Drive a terminal (non-tool-call) `AIMessage` with `finish_reason="length"` (OpenAI/LiteLLM shape) through `parse_message_type`; assert `agent._detect_terminal_truncation` is called and no exception is raised.
  - Same for native Claude (`stop_reason="max_tokens"`) and Bedrock (`stopReason="max_tokens"` camelCase) shapes.
  - Same three provider shapes through `parse_supervisor_message_type` (the supervisor/multi-agent final-answer path).
  - A normal-completion terminal message (`finish_reason="stop"` / `stop_reason="end_turn"`) still invokes the detector, which resets the flag.
  - A terminal message from a non-`agent` node (e.g. `generate_structured_response`) does not invoke the detector or `_on_llm_end`.
- Extend `tests/codemie/agents/test_langgraph_truncation.py`:
  - `_detect_terminal_truncation` sets `_last_truncated` for OpenAI, Claude and Bedrock shapes, and clears it on a later non-truncated message.
  - `invoke_task` threads `_last_truncated` into `TaskResult.truncated`, and resets it before each invocation.
- Extend `tests/codemie/agents/test_assistant_agent.py` for `TaskResult.truncated` defaults and `from_agent_response` / `failed_result` passthrough.
- New test for `get_workflow_execution_state_output` (`tests/codemie/rest_api/routers/test_workflow_executions.py`) asserting the response includes `truncated` sourced from the DB row.
- Extend `tests/enterprise/litellm/test_llm_factory.py` with the `_filter_litellm_params` `max_tokens`-forwarding test described above.
- New/extended test for `WorkflowExecutionService.finish_state` (`tests/codemie/service/workflow_execution/test_workflow_execution_service.py`) asserting the kwarg persists onto `WorkflowExecutionState` and appears on the streamed `WorkflowStateEvent`.
- Extend `tests/codemie/service/workflow_execution/test_workflow_update_output_service.py` (or the nearest existing test module) asserting that `_update_state` clears `truncated`.
- New test for `_get_execution_thoughts` (`tests/codemie/service/test_history_materializer.py`) asserting `truncated` is copied from the `WorkflowExecutionState` row into the returned dict.
- Extend `tests/codemie/workflows/nodes/test_base_node_truncation.py` to assert `on_node_end` is called with `truncated` kwargs.
- New test module for `LanggraphNodeCallback.on_node_end` (`tests/codemie/workflows/callbacks/test_graph_callback.py`) asserting the streamed `Thought` carries `truncated` when passed in.

## Risks / Notes

- `_detect_terminal_truncation` inherits the existing fail-open pattern: unexpected errors inside detection are logged, not raised, consistent with `_safe_check_for_truncation`'s existing behavior on the tool-call path. This is accepted existing practice, not a regression being introduced.
- Both `internal` and `lite_llm` `LLM_PROXY_MODE` paths should be exercised by the new tests where practical, since response metadata shape may differ between direct provider SDKs and LiteLLM-proxied OpenAI-compatible responses.
- The supervisor final-answer path (`parse_supervisor_message_type`) is fixed for detection in lockstep with the primary path (Design §1). It is **not** restricted to the `agent` node, unlike the primary path. Whether a non-agent supervisor-stream stop can reset the flag is an open question, to be resolved before this is considered complete for multi-agent workflows.
- `truncated` is persisted from the agent node's terminal message only. Tool-call truncation still fails the state (unchanged), so a `truncated=True` row is always a `SUCCEEDED` state with an incomplete answer.
- The migration `t1r2u3n4c5a6` was rewritten on this branch before release. If any shared environment applied the earlier revision (which added `finish_reason` too), that environment needs a manual fix-up before this migration is consistent with it.
