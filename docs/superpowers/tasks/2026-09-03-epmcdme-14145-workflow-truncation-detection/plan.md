# Workflow Terminal Truncation Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make silently-truncated final assistant answers detectable end-to-end — from the LangGraph terminal-message path, through `TaskResult` and workflow-node persistence, to the state output API — without changing the existing raise-and-fail behavior of the tool-call truncation path.

**Architecture:** Add a non-raising terminal-truncation detector to `LangGraphAgent` and wire it into the two adapter code paths (`parse_message_type`, `parse_supervisor_message_type`) that currently call `_on_llm_end` for a terminal message without any truncation check. Thread the resulting boolean `truncated` signal forward as plain data — through `TaskResult`, `BaseNode._run_success_path`, `WorkflowExecutionService.finish_state`, the `WorkflowExecutionState` table model, and the `WorkflowExecutionStateOutput` API response model — so a `SUCCEEDED` state whose output was actually cut off carries a visible flag instead of looking identical to a complete answer. A confirmatory test locks down the existing (already-correct) LiteLLM `max_tokens` forwarding behavior for criterion 3.

**Tech Stack:** Python, LangGraph, Pydantic v2 / SQLModel, FastAPI, Alembic, pytest, unittest.mock.

**Spec:** `docs/superpowers/tasks/2026-09-03-epmcdme-14145-workflow-truncation-detection/spec.md`

## Global Constraints

- The existing tool-call truncation path (`_safe_check_for_truncation`, `TokenLimitExceededException`, `_check_for_truncated_response`, `_get_truncation_indicator`) is **not modified** — it keeps raising and failing the state as today.
- A truncated terminal message keeps the workflow state `SUCCEEDED`; truncation is surfaced as metadata (`truncated`), never as a new failure status.
- Only `truncated` is persisted and exposed. The raw provider reason (`finish_reason`/`stop_reason`) was dropped: it always co-occurred with `truncated`, and it stored provider-specific spellings for the same condition. Logs still carry it via `_get_truncation_indicator`.
- Editing a state's output (`WorkflowUpdateOutputService._update_state`) clears `truncated`.
- `AIToolsAgent` is out of scope — only `LangGraphAgent`/`LangGraphEventAdapter` are touched.
- No change to `config/llms/*.yaml` `features.max_tokens` flags unless a test surfaces an actual mismatch (none is expected — this plan adds a confirmatory test only).
- Every new/changed field defaults to `None`/`False` so all other call sites and existing callers are unaffected.
- **Two deliberate deviations from the spec's literal text** (both required to make the spec's stated behavior actually work; neither reopens the locked non-raising-signal architectural decision):
  1. **Widened gating condition.** The spec's Design §1 says to call the detector "in their `elif self.agent.is_finish_reason_stop(message):` branch." Read literally, that branch can never be `True` for a genuinely truncated message: `is_finish_reason_stop` only returns `True` for normal completion (`"end_turn"`/`"stop"`), and the preceding `if self.agent.is_valid_ai_message(message):` branch requires non-empty `message.content` — a truncated terminal chunk (empty content, `finish_reason="length"`) satisfies neither branch and today falls through both, silently. Task 2 below therefore widens the condition to `elif self.agent.is_finish_reason_stop(message) or self.agent.is_finish_reason_truncated(message):` in both adapter methods, using a new `is_finish_reason_truncated` method (Task 1). Without this widening, the new detector would simply never be called.
  2. **Explicit new fields on `WorkflowStateEvent`.** `WorkflowStateEvent` (`src/codemie/chains/base.py`) has no `output` field today, even though `finish_state` already passes `output=output` into it — Pydantic v2's default `extra="ignore"` behavior silently drops that kwarg. This is a pre-existing, out-of-scope issue and is **not** being fixed here. But it means the spec's Design §3 requirement ("truncated ... included on the WorkflowStateEvent streamed to thought_queue") only works if the field is declared explicitly on the model (Task 4) — passing it as a bare kwarg would suffer the same silent drop.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/codemie/agents/langgraph_agent.py` | New non-raising terminal-truncation detector (`_detect_terminal_truncation`, `is_finish_reason_truncated`) and the `_last_truncated` instance attribute; reads it in `invoke_task` after the graph runs. |
| `src/codemie/agents/langgraph_event_adapter.py` | Widened gating in `parse_message_type`/`parse_supervisor_message_type` to call the new detector for terminal (non-tool-call) messages; `parse_message_type` restricts it to the `agent` node. |
| `src/codemie/agents/assistant_agent.py` | `TaskResult` gains a `truncated` field; `from_agent_response` and `failed_result` accept and set it. |
| `src/codemie/chains/base.py` | `WorkflowStateEvent` and `Thought` gain a `truncated` field. |
| `src/codemie/service/workflow_execution/workflow_execution_service.py` | `finish_state` gains a `truncated` kwarg, persists it on the state row, and streams it on the `WorkflowStateEvent`. |
| `src/codemie/service/workflow_execution/workflow_update_output_service.py` | `_update_state` clears `truncated` when the output is edited. |
| `src/codemie/workflows/nodes/base_node.py` | `_run_success_path` extracts `truncated` from `raw_output` (via `getattr`, defaulting safely for non-`TaskResult` outputs) and passes it to `finish_state` and `on_node_end`. |
| `src/codemie/core/workflow_models/workflow_execution.py` | `BaseWorkflowExecutionState` and `WorkflowExecutionStateOutput` gain a `truncated` field. |
| `src/external/alembic/versions/t1r2u3n4c5a6_add_truncated_to_workflow_execution_states.py` | Migration adding a `truncated` boolean column (default `false`, not null) to `workflow_execution_states`. |
| `src/codemie/rest_api/routers/workflow_executions.py` | `get_workflow_execution_state_output` fetches the state row once and passes `output`/`truncated` through to the response. |
| `src/codemie/enterprise/litellm/llm_factory.py` | No production change — confirmatory test only. |
| Test files | `tests/codemie/agents/test_langgraph_truncation.py`, `tests/codemie/agents/test_langgraph_event_adapter.py`, `tests/codemie/agents/test_assistant_agent.py`, `tests/codemie/service/workflow_execution/test_workflow_execution_service.py`, `tests/codemie/rest_api/routers/test_workflow_executions.py`, `tests/enterprise/litellm/test_llm_factory.py` — all extended, none created new. |

---

### Task 1: Non-raising terminal-truncation detector on `LangGraphAgent`

**Files:**
- Modify: `src/codemie/agents/langgraph_agent.py:191` (new instance attribute), `src/codemie/agents/langgraph_agent.py:1225` (new methods, inserted directly after `is_finish_reason_tool_calls`)
- Test: `tests/codemie/agents/test_langgraph_truncation.py`

**Interfaces:**
- Consumes: nothing new — reads `message.response_metadata` the same way `_get_truncation_indicator` already does.
- Produces: `LangGraphAgent.is_finish_reason_truncated(message: AIMessage) -> bool`; `LangGraphAgent._detect_terminal_truncation(message: AIMessage) -> None` (sets `self._last_truncated: bool` as a side effect); `self._last_truncated` instance attribute, initialized to `False` in `__init__`. These are consumed by Task 2 (adapter) and Task 3 (`invoke_task`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/agents/test_langgraph_truncation.py`, after the existing `TestTruncationHelperMethods` class:

```python
class TestDetectTerminalTruncation:
    """Tests for the non-raising terminal-message truncation signal (EPMCDME-14145)."""

    def test_is_finish_reason_truncated_openai_length(self):
        agent = create_mock_agent(llm_model="gpt-4.1")
        message = AIMessage(content="partial answer", response_metadata={"finish_reason": "length"})

        assert agent.is_finish_reason_truncated(message) is True

    def test_is_finish_reason_truncated_claude_stop_reason_max_tokens(self):
        agent = create_mock_agent(llm_model="claude-3-5-sonnet")
        message = AIMessage(content="partial answer", response_metadata={"stop_reason": "max_tokens"})

        assert agent.is_finish_reason_truncated(message) is True

    def test_is_finish_reason_truncated_bedrock_camel_case_stop_reason(self):
        agent = create_mock_agent(llm_model="anthropic.claude-3-5-sonnet")
        message = AIMessage(content="partial answer", response_metadata={"stopReason": "max_tokens"})

        assert agent.is_finish_reason_truncated(message) is True

    def test_is_finish_reason_truncated_false_for_normal_completion(self):
        agent = create_mock_agent()
        message = AIMessage(content="complete answer", response_metadata={"finish_reason": "stop"})

        assert agent.is_finish_reason_truncated(message) is False

    def test_is_finish_reason_truncated_false_for_empty_metadata(self):
        agent = create_mock_agent()
        message = AIMessage(content="complete answer", response_metadata={})

        assert agent.is_finish_reason_truncated(message) is False

    def test_detect_terminal_truncation_sets_flags_on_truncated_openai_message(self):
        agent = create_mock_agent()
        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        agent._detect_terminal_truncation(message)

        assert agent._last_truncated is True

    def test_detect_terminal_truncation_sets_flags_on_truncated_claude_message(self):
        agent = create_mock_agent()
        message = AIMessage(content="", response_metadata={"stop_reason": "max_tokens"})

        agent._detect_terminal_truncation(message)

        assert agent._last_truncated is True

    def test_detect_terminal_truncation_leaves_flags_unset_on_normal_completion(self):
        agent = create_mock_agent()
        agent._last_truncated = False
        message = AIMessage(content="complete answer", response_metadata={"finish_reason": "stop"})

        agent._detect_terminal_truncation(message)

        assert agent._last_truncated is False

    def test_detect_terminal_truncation_does_not_raise(self):
        """Unlike _safe_check_for_truncation, this must never raise TokenLimitExceededException."""
        agent = create_mock_agent()
        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        agent._detect_terminal_truncation(message)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_truncation.py::TestDetectTerminalTruncation -v`
Expected: FAIL with `AttributeError: 'LangGraphAgent' object has no attribute 'is_finish_reason_truncated'` (and similarly for `_detect_terminal_truncation`).

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/agents/langgraph_agent.py`, in `__init__`, immediately after the existing line:

```python
        self._current_llm_run_id: uuid.UUID | None = None  # tracks active LLM invocation
```

add:

```python
        self._last_truncated: bool = False  # non-raising terminal truncation signal
```

Then, directly after the existing `is_finish_reason_tool_calls` method:

```python
    def is_finish_reason_tool_calls(self, message: AIMessage) -> bool:
        return hasattr(message, "tool_calls") and bool(message.tool_calls)

    def is_finish_reason_truncated(self, message: AIMessage) -> bool:
        """True when the raw provider finish/stop reason indicates truncation.

        Mirrors the raw-value lookup _get_truncation_indicator performs, but returns
        a plain bool suitable for use as an adapter gating condition.
        """
        response_metadata = message.response_metadata
        if not response_metadata:
            return False
        return self._get_truncation_indicator(response_metadata) is not None

    def _detect_terminal_truncation(self, message: AIMessage) -> None:
        """Non-raising terminal-message truncation signal (EPMCDME-14145).

        Unlike _safe_check_for_truncation (tool-call path, raises TokenLimitExceededException
        and fails the state), this records the truncation flag on an instance attribute
        so the caller can persist it as metadata on an otherwise-SUCCEEDED result. Follows the
        same fail-open pattern: unexpected errors are logged, never raised.
        """
        try:
            response_metadata = message.response_metadata
            if not response_metadata:
                return
            # Assigned on every terminal message, so a later non-truncated message resets a prior flag.
            self._last_truncated = self._get_truncation_indicator(response_metadata) is not None
        except Exception as e:
            logger.error(f"Unexpected error during terminal truncation detection: {e}", exc_info=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_truncation.py::TestDetectTerminalTruncation -v`
Expected: PASS (all 9 new tests).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/agents/langgraph_agent.py tests/codemie/agents/test_langgraph_truncation.py
git commit -m "EPMCDME-14145: add non-raising terminal truncation detector to LangGraphAgent"
```

Test-first: yes — `TestDetectTerminalTruncation` written and run to fail before `is_finish_reason_truncated`/`_detect_terminal_truncation` exist.

---

### Task 2: Wire the detector into `LangGraphEventAdapter`'s terminal-message paths

**Files:**
- Modify: `src/codemie/agents/langgraph_event_adapter.py:143-151` (`parse_message_type`), `src/codemie/agents/langgraph_event_adapter.py:172-183` (`parse_supervisor_message_type`)
- Test: `tests/codemie/agents/test_langgraph_event_adapter.py`

**Interfaces:**
- Consumes: `LangGraphAgent.is_finish_reason_truncated` and `LangGraphAgent._detect_terminal_truncation` from Task 1.
- Produces: guarantees that every terminal (non-tool-call) message passing through `parse_message_type`/`parse_supervisor_message_type` — truncated or not — is checked by `_detect_terminal_truncation` before `_on_llm_end` is called. Task 3 relies on this running before `invoke_task` reads `self._last_truncated`. In `parse_message_type` the restriction to the `agent` node is part of the contract: a terminal message from another node (e.g. `generate_structured_response`) must not reset the agent's flag.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/agents/test_langgraph_event_adapter.py`, as a new class after `TestLangGraphEventAdapter`:

```python
class TestTerminalMessageTruncationDetection:
    """EPMCDME-14145: parse_message_type/parse_supervisor_message_type must run the
    non-raising truncation detector on terminal (non-tool-call) messages, not just
    tool-call messages."""

    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent.is_valid_ai_message.return_value = False
        agent.is_finish_reason_stop.return_value = False
        agent.is_finish_reason_truncated.return_value = False
        agent._detect_terminal_truncation = MagicMock()
        agent._on_llm_end = MagicMock()
        return agent

    def test_parse_message_type_detects_openai_truncation(self, mock_agent):
        mock_agent.is_finish_reason_truncated.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        adapter.parse_message_type((message, {"langgraph_node": "agent"}), chunks_collector=[])

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)
        mock_agent._on_llm_end.assert_called_once_with(response=message, run_id=message.id)

    def test_parse_message_type_detects_claude_truncation(self, mock_agent):
        mock_agent.is_finish_reason_truncated.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={"stop_reason": "max_tokens"})

        adapter.parse_message_type((message, {"langgraph_node": "agent"}), chunks_collector=[])

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)

    def test_parse_message_type_detects_bedrock_truncation(self, mock_agent):
        mock_agent.is_finish_reason_truncated.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={"stopReason": "max_tokens"})

        adapter.parse_message_type((message, {"langgraph_node": "agent"}), chunks_collector=[])

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)

    def test_parse_message_type_normal_completion_still_calls_detector(self, mock_agent):
        """A normal (non-truncated) terminal message still goes through the detector — it is a
        no-op for that case, but must run so the flags are freshly reset/unset per turn."""
        mock_agent.is_finish_reason_stop.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={"finish_reason": "stop"})

        adapter.parse_message_type((message, {"langgraph_node": "agent"}), chunks_collector=[])

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)
        mock_agent._on_llm_end.assert_called_once_with(response=message, run_id=message.id)

    def test_parse_message_type_skips_when_neither_stop_nor_truncated(self, mock_agent):
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={})

        adapter.parse_message_type((message, {"langgraph_node": "agent"}), chunks_collector=[])

        mock_agent._detect_terminal_truncation.assert_not_called()
        mock_agent._on_llm_end.assert_not_called()

    def test_parse_supervisor_message_type_detects_truncation(self, mock_agent):
        mock_agent.is_finish_reason_truncated.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        adapter.parse_supervisor_message_type((message, {}), chunks_collector=[], author="agent_a")

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)
        mock_agent._on_llm_end.assert_called_once_with(response=message, run_id=message.id, author="agent_a")

    def test_parse_supervisor_message_type_normal_completion_still_calls_detector(self, mock_agent):
        mock_agent.is_finish_reason_stop.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={"stop_reason": "end_turn"})

        adapter.parse_supervisor_message_type((message, {}), chunks_collector=[], author="agent_a")

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)

    def test_parse_supervisor_message_type_skips_when_neither_stop_nor_truncated(self, mock_agent):
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={})

        adapter.parse_supervisor_message_type((message, {}), chunks_collector=[], author="agent_a")

        mock_agent._detect_terminal_truncation.assert_not_called()
        mock_agent._on_llm_end.assert_not_called()
```

    def test_parse_message_type_ignores_non_agent_node_terminal_message(self, mock_agent):
        """A structured-output stop from a later node must not reset the agent's truncation flag."""
        mock_agent.is_finish_reason_stop.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="{}", response_metadata={"finish_reason": "stop"})

        adapter.parse_message_type((message, {"langgraph_node": "generate_structured_response"}), [])

        mock_agent._detect_terminal_truncation.assert_not_called()
        mock_agent._on_llm_end.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_event_adapter.py::TestTerminalMessageTruncationDetection -v`
Expected: FAIL — `mock_agent._detect_terminal_truncation.assert_called_once_with(message)` raises `AssertionError: Expected ... to have been called once. Called 0 times.` for the truncated shapes (the stop-only gating never reaches it), and the non-agent-node test fails because the detector is currently called before the node check.

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/agents/langgraph_event_adapter.py`, `parse_message_type` (as built: a separate `if`, so a chunk carrying both content and a stop reason still finalizes; detection and finalization are both gated on the `agent` node):

```python
    def parse_message_type(self, value: Any, chunks_collector: list[str]) -> None:
        message, metadata = value
        self._remember_response_headers(message)

        if self.agent.is_valid_ai_message(message):
            token = extract_text_from_llm_output(message.content)
            self.agent._process_agent_streaming(token, chunks_collector, message.id)

        if (
            self.agent.is_finish_reason_stop(message) or self.agent.is_finish_reason_truncated(message)
        ) and metadata.get("langgraph_node") == "agent":
            self.agent._detect_terminal_truncation(message)
            self._restore_response_headers(message)
            self.agent._on_llm_end(response=message, run_id=message.id)
```

And `parse_supervisor_message_type` (not node-restricted; see spec Risks):

```python
    def parse_supervisor_message_type(
        self,
        value: Any,
        chunks_collector: list[str],
        author: str | None = None,
    ) -> None:
        message, _ = value
        self._remember_response_headers(message)
        if self.agent.is_valid_ai_message(message) and not message.response_metadata.get(METADATA_KEY_HANDOFF_BACK):
            token = extract_text_from_llm_output(message.content)
            self.agent._process_agent_streaming(token, chunks_collector, message.id, author=author)

        if self.agent.is_finish_reason_stop(message) or self.agent.is_finish_reason_truncated(message):
            self.agent._detect_terminal_truncation(message)
            self._restore_response_headers(message)
            self.agent._on_llm_end(response=message, run_id=message.id, author=author)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_event_adapter.py -v`
Expected: PASS (all tests in the file, including the 8 new ones and the 5 pre-existing tool-call tests, which are untouched by this change).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/agents/langgraph_event_adapter.py tests/codemie/agents/test_langgraph_event_adapter.py
git commit -m "EPMCDME-14145: run truncation detector on terminal messages in event adapter"
```

Test-first: yes — `TestTerminalMessageTruncationDetection` written and run to fail before the `elif` conditions are widened.

---

### Task 3: Thread the signal into `TaskResult` via `invoke_task`

**Files:**
- Modify: `src/codemie/agents/assistant_agent.py:96-129` (`TaskResult`, `from_agent_response`), `src/codemie/agents/langgraph_agent.py:621-645` (`invoke_task`)
- Test: `tests/codemie/agents/test_assistant_agent.py`

**Interfaces:**
- Consumes: `self._last_truncated` (Task 1), set as a side effect of `_invoke_agent` → `_stream_graph` → adapter dispatch (Task 2).
- Produces: `TaskResult.truncated: bool = False`; `TaskResult.from_agent_response(response, truncated=False)`; `TaskResult.failed_result(result, original_exc, truncated=False)`. Task 6 relies on `TaskResult` (the `raw_output` a `AgentNode` produces) carrying this field.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/agents/test_assistant_agent.py`, after `test_from_agent_response_pydantic_generated_is_json_serialized`:

```python
def test_from_agent_response_defaults_truncated_to_unset():
    response = make_generation_result(generated="output", success=True)

    result = TaskResult.from_agent_response(response)

    assert result.truncated is False


def test_from_agent_response_carries_truncation_signal_from_generation_result():
    response = make_generation_result(generated="cut off output", success=True)

    result = TaskResult.from_agent_response(response, truncated=True)

    assert result.truncated is True
    assert result.result == "cut off output"


def test_from_agent_response_dict_path_defaults_truncated():
    response = {"output": "agent answer"}

    result = TaskResult.from_agent_response(response)

    assert result.truncated is False
```

Add a new test module-level helper and class for `invoke_task` end-to-end threading, appended to the same file:

```python
from codemie.agents.langgraph_agent import LangGraphAgent


def _make_langgraph_agent():
    agent = LangGraphAgent.__new__(LangGraphAgent)
    agent.assistant = MagicMock()
    agent.user = MagicMock()
    agent.agent_name = "test-agent"
    agent.llm_model = "gpt-4"
    agent._last_truncated = False
    return agent


class TestInvokeTaskTruncationThreading:
    """EPMCDME-14145: invoke_task must surface the terminal-truncation signal set by
    _invoke_agent (via the event adapter) on the returned TaskResult."""

    @patch("codemie.agents.langgraph_agent.set_llm_context")
    def test_invoke_task_carries_truncation_signal_into_task_result(self, _mock_set_context):
        agent = _make_langgraph_agent()

        def fake_invoke_agent(inputs):
            # Simulate the event adapter having detected truncation during streaming.
            agent._last_truncated = True
            return make_generation_result(generated="cut off output", success=True)

        with (
            patch.object(LangGraphAgent, "_get_inputs", return_value={}),
            patch.object(LangGraphAgent, "_invoke_agent", side_effect=fake_invoke_agent),
            patch.object(LangGraphAgent, "_filter_history", return_value=[]),
            patch.object(LangGraphAgent, "_transform_history", return_value=[]),
            patch.object(LangGraphAgent, "_serialize_messages_for_log", return_value=""),
            patch.object(LangGraphAgent, "_truncate_log_content", return_value=""),
        ):
            result = agent.invoke_task(workflow_input="do the thing")

        assert result.result == "cut off output"
        assert result.truncated is True

    @patch("codemie.agents.langgraph_agent.set_llm_context")
    def test_invoke_task_leaves_task_result_unflagged_when_not_truncated(self, _mock_set_context):
        agent = _make_langgraph_agent()

        def fake_invoke_agent(inputs):
            return make_generation_result(generated="complete output", success=True)

        with (
            patch.object(LangGraphAgent, "_get_inputs", return_value={}),
            patch.object(LangGraphAgent, "_invoke_agent", side_effect=fake_invoke_agent),
            patch.object(LangGraphAgent, "_filter_history", return_value=[]),
            patch.object(LangGraphAgent, "_transform_history", return_value=[]),
            patch.object(LangGraphAgent, "_serialize_messages_for_log", return_value=""),
            patch.object(LangGraphAgent, "_truncate_log_content", return_value=""),
        ):
            result = agent.invoke_task(workflow_input="do the thing")

        assert result.result == "complete output"
        assert result.truncated is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/agents/test_assistant_agent.py -v`
Expected: FAIL — `TaskResult.from_agent_response() got an unexpected keyword argument 'truncated'` for the `from_agent_response` tests, and `AssertionError` on `result.truncated` for the `invoke_task` tests.

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/agents/assistant_agent.py` (`TaskResult` gains one field; `failed_result` takes no flag because the failure path has no terminal message to report on):

```python
class TaskResult(BaseModel):
    result: str
    success: bool
    intermediate_steps: list = Field(default_factory=list)
    original_exc: Optional[Any] = None
    truncated: bool = False

    @classmethod
    def failed_result(cls, result: str, original_exc: Any, truncated: bool = False):
        return cls(result=result, success=False, original_exc=original_exc, truncated=truncated)

    @classmethod
    def from_agent_response(cls, response, truncated: bool = False):
        if isinstance(response, GenerationResult):
            result = response.generated
            # Serialize objects from structured outputs as strings to pass to the next step.
            if isinstance(response.generated, dict):
                result = json.dumps(response.generated)
            elif isinstance(response.generated, BaseModel):
                result = response.generated.model_dump_json()
            return cls(result=result or '', success=response.success, truncated=truncated)

        # Determine the result and success
        if 'output' in response:
            result = extract_text_from_llm_output(response['output'])
            success = True
            if 'intermediate_steps' in response:
                return cls(
                    result=result,
                    success=success,
                    intermediate_steps=response['intermediate_steps'],
                    truncated=truncated,
                )
        elif 'generated' in response:
            result = response['generated']
            success = True
        else:
            result = ''
            success = False
        return cls(result=result, success=success, truncated=truncated)
```

In `src/codemie/agents/langgraph_agent.py`, `invoke_task`:

```python
    def invoke_task(self, workflow_input: str = "", history=None, args=None) -> TaskResult:
        """
        Used in workflows invocation
        """
        if args is None:
            args = {}
        if history is None:
            history = []
        # No lock needed: AgentNode.generate_execution_context always builds a fresh instance per call
        # (init_agent_node never passes assistant=), so _last_truncated is never shared across concurrent branches.
        self._last_truncated = False
        set_llm_context(self.assistant, None, self.user, self.llm_model)
        logger.debug(
            f"Invoking workflow task. Agent={self.agent_name}, "
            f"Input={self._truncate_log_content(workflow_input)}, "
            f"ChatHistory={self._serialize_messages_for_log(self._filter_history(self._transform_history(history)))}"
        )
        try:
            inputs = self._get_inputs(workflow_input, history)
            inputs.update(args)
            agent_response = self._invoke_agent(inputs)

            return TaskResult.from_agent_response(agent_response, truncated=self._last_truncated)
        except MCPAuthenticationRequiredException:
            raise
        except Exception as e:
            record_exception_on_span(e)
            return TaskResult.failed_result(
                self._get_user_error_message(e),
                original_exc=e,
                truncated=self._last_truncated,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/agents/test_assistant_agent.py -v`
Expected: PASS (all existing + new tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/agents/assistant_agent.py src/codemie/agents/langgraph_agent.py tests/codemie/agents/test_assistant_agent.py
git commit -m "EPMCDME-14145: thread truncated through TaskResult and invoke_task"
```

Test-first: yes — new `test_from_agent_response_*` and `TestInvokeTaskTruncationThreading` tests written and run to fail before the `truncated` kwargs exist.

---

### Task 4: Add `truncated` to `WorkflowStateEvent`

**Files:**
- Modify: `src/codemie/chains/base.py:109-118`
- Test: none (a plain Pydantic field addition with no branching logic — covered indirectly by Task 5's `finish_state` test asserting the streamed event payload).

**Interfaces:**
- Consumes: nothing.
- Produces: `WorkflowStateEvent.truncated: bool = False`. Task 5 relies on this field existing so that constructing `WorkflowStateEvent(..., truncated=...)` actually keeps it on the model instead of being silently dropped (see Global Constraints, deviation 2).

- [ ] **Step 1: Write minimal implementation**

In `src/codemie/chains/base.py`:

```python
class WorkflowStateEvent(BaseModel):
    """Workflow state change event for streaming"""

    id: Optional[str] = None  # State ID
    name: str  # State name
    task: Optional[str] = None  # Task description
    status: str  # State status
    event_type: WorkflowStateEventType
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    truncated: bool = False  # True when the final answer was cut off by a token limit
```

- [ ] **Step 2: Verify no regression**

Run: `poetry run pytest tests/codemie/service/workflow_execution/ tests/codemie/rest_api/routers/test_workflow_executions.py -v`
Expected: PASS — this is an additive, defaulted field; nothing existing constructs `WorkflowStateEvent` with keyword conflicts.

- [ ] **Step 3: Commit**

```bash
git add src/codemie/chains/base.py
git commit -m "EPMCDME-14145: add truncated field to WorkflowStateEvent"
```

Test-first: no — pure additive field declaration with default values, no new branching behavior to pin down in isolation; verified indirectly by Task 5's test.

---

### Task 5: `WorkflowExecutionService.finish_state` persists and streams the signal

**Files:**
- Modify: `src/codemie/service/workflow_execution/workflow_execution_service.py:372-417`
- Test: `tests/codemie/service/workflow_execution/test_workflow_execution_service.py`

**Interfaces:**
- Consumes: `WorkflowStateEvent.truncated` (Task 4).
- Produces: `finish_state(execution_state_id: str, output: str, status: WorkflowExecutionStatusEnum, truncated: bool = False) -> None`. Task 6 (`BaseNode._run_success_path`) calls this with the new kwarg.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/workflow_execution/test_workflow_execution_service.py`, as a new class:

```python
class TestFinishState:
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.find_by_id")
    def test_persists_truncated_on_state(self, mock_find_by_id, service):
        state = MagicMock()
        mock_find_by_id.return_value = state
        service.thought_queue = None

        service.finish_state(
            execution_state_id="exec-state-1",
            output="cut off output",
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
            truncated=True,
        )

        assert state.output == "cut off output"
        assert state.truncated is True
        assert state.status == WorkflowExecutionStatusEnum.SUCCEEDED
        state.save.assert_called_once()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.find_by_id")
    def test_defaults_truncated_when_not_passed(self, mock_find_by_id, service):
        state = MagicMock()
        mock_find_by_id.return_value = state
        service.thought_queue = None

        service.finish_state(
            execution_state_id="exec-state-2",
            output="complete output",
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
        )

        assert state.truncated is False

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.find_by_id")
    def test_streams_truncated_on_workflow_state_event(self, mock_find_by_id, service):
        state = MagicMock()
        state.id = "exec-state-3"
        state.name = "state_a"
        state.task = "do something"
        state.started_at = None
        mock_find_by_id.return_value = state
        mock_queue = MagicMock()
        service.thought_queue = mock_queue

        service.finish_state(
            execution_state_id="exec-state-3",
            output="cut off output",
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
            truncated=True,
        )

        mock_queue.send.assert_called_once()
        payload = json.loads(mock_queue.send.call_args[0][0])
        assert payload["workflow_state"]["truncated"] is True

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.find_by_id")
    def test_returns_early_when_state_not_found(self, mock_find_by_id, service):
        mock_find_by_id.return_value = None
        service.thought_queue = MagicMock()

        service.finish_state(
            execution_state_id="missing-state",
            output="output",
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
            truncated=True,
        )  # must not raise

        service.thought_queue.send.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/workflow_execution/test_workflow_execution_service.py::TestFinishState -v`
Expected: FAIL — `TypeError: finish_state() got an unexpected keyword argument 'truncated'`.

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/service/workflow_execution/workflow_execution_service.py`:

```python
    def finish_state(
        self,
        execution_state_id: str,
        output: str,
        status: WorkflowExecutionStatusEnum,
        truncated: bool = False,
    ):
        with self.workflow_execution_lock:
            self._refresh_workflow_execution()
            if self.workflow_execution:
                if self.workflow_execution.overall_status != WorkflowExecutionStatusEnum.ABORTED:
                    calculated_tokens = self._calculate_tokens_usage(self.workflow_execution_id)
                    if calculated_tokens is not None:
                        self.workflow_execution.tokens_usage = calculated_tokens
                        self.workflow_execution.update(refresh=REFRESH_WAIT_FOR)
            else:
                logger.warning(
                    f"Workflow execution not found for execution_id: {self.workflow_execution_id}. "
                    "Skipping tokens_usage update."
                )

            state = WorkflowExecutionState.find_by_id(id_=execution_state_id)
            if state is None:
                logger.warning(
                    f"Execution state {execution_state_id} not found when finishing with status {status} — "
                    "it may have been deleted by a concurrent operation."
                )
                return
            state.output = output
            state.status = status
            state.truncated = truncated
            completed_at = datetime.now()
            state.completed_at = completed_at

            state.save()
            print_output = output or ""
            print_output = print_output if len(print_output) <= 100 else f"{print_output[0:50]}...{print_output[-50:]}"
            logger.debug(f"Successfuly saved state execution {execution_state_id} with output: {print_output}")

            # Stream state finish event to client
            if self.thought_queue:
                state_event = WorkflowStateEvent(
                    id=state.id,
                    name=state.name,
                    task=state.task,
                    output=output,  # Include the actual output/result
                    status=status.value,
                    event_type=WorkflowStateEventType.STATE_FINISH,
                    started_at=state.started_at.isoformat() if state.started_at else None,
                    completed_at=completed_at.isoformat(),
                    truncated=truncated,
                )
                result = StreamedGenerationResult(workflow_state=state_event)
                self.thought_queue.send(result.model_dump_json())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/workflow_execution/test_workflow_execution_service.py -v`
Expected: PASS (full file — new `TestFinishState` class plus all pre-existing classes).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/workflow_execution/workflow_execution_service.py tests/codemie/service/workflow_execution/test_workflow_execution_service.py
git commit -m "EPMCDME-14145: persist and stream truncated in finish_state"
```

Test-first: yes — `TestFinishState` written and run to fail before the new kwargs/assignments exist.

---

### Task 6: `BaseNode._run_success_path` extracts and forwards the signal

**Files:**
- Modify: `src/codemie/workflows/nodes/base_node.py:229-239`
- Test: covered by an existing or new node-level test if present; otherwise verified via the integration shown below added to `tests/codemie/service/workflow_execution/test_workflow_execution_service.py` is out of reach (that test targets the service, not the node) — add a focused unit test directly against `_run_success_path` in a new small test module.

**Interfaces:**
- Consumes: `TaskResult.truncated` (Task 3, via `getattr` — safe for any `raw_output` type); `WorkflowExecutionService.finish_state(..., truncated=...)` (Task 5).
- Produces: every `AgentNode` execution whose `raw_output` is a `TaskResult` now passes the real signal into `finish_state`; any other node type's `raw_output` (which has no `truncated` attribute) passes the safe defaults `None`/`False`.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/workflows/nodes/test_base_node_truncation.py`:

```python
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

from unittest.mock import MagicMock

from codemie.agents.assistant_agent import TaskResult
from codemie.core.workflow_models import WorkflowExecutionStatusEnum
from codemie.workflows.nodes.base_node import BaseNode


def _make_node():
    node = BaseNode.__new__(BaseNode)
    node.workflow_execution_service = MagicMock()
    node.callbacks = []
    node.args = {}
    node.kwargs = {}
    return node


def test_run_success_path_forwards_truncation_signal_from_task_result():
    node = _make_node()
    task_result = TaskResult(result="cut off output", success=True, truncated=True)
    node.execute = MagicMock(return_value=task_result)
    node.post_process_output = MagicMock(return_value="cut off output")
    node._is_execution_aborted = MagicMock(return_value=False)
    node.finalize_and_update_state = MagicMock(return_value=("raw", "final"))

    node._run_success_path(
        state_schema=MagicMock(),
        execution_context={},
        execution_state_id="exec-state-1",
        task="do something",
        current_node_name="agent_node",
    )

    node.workflow_execution_service.finish_state.assert_called_once_with(
        execution_state_id="exec-state-1",
        output="cut off output",
        status=WorkflowExecutionStatusEnum.SUCCEEDED,
        truncated=True,
    )


def test_run_success_path_defaults_signal_for_non_task_result_output():
    node = _make_node()
    node.execute = MagicMock(return_value={"some": "dict-shaped output"})
    node.post_process_output = MagicMock(return_value="processed output")
    node._is_execution_aborted = MagicMock(return_value=False)
    node.finalize_and_update_state = MagicMock(return_value=("raw", "final"))

    node._run_success_path(
        state_schema=MagicMock(),
        execution_context={},
        execution_state_id="exec-state-2",
        task="do something",
        current_node_name="other_node",
    )

    node.workflow_execution_service.finish_state.assert_called_once_with(
        execution_state_id="exec-state-2",
        output="processed output",
        status=WorkflowExecutionStatusEnum.SUCCEEDED,
        truncated=False,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/workflows/nodes/test_base_node_truncation.py -v`
Expected: FAIL — `AssertionError` because `finish_state` is currently called without `truncated` kwargs (call-arg mismatch against `assert_called_once_with`).

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/workflows/nodes/base_node.py`, `_run_success_path`:

```python
        raw_output = self.execute(state_schema, execution_context)
        processed_output = self.post_process_output(state_schema, task, raw_output)

        if self._is_execution_aborted():
            status = WorkflowExecutionStatusEnum.ABORTED
        else:
            status = WorkflowExecutionStatusEnum.SUCCEEDED

        truncated = getattr(raw_output, "truncated", False)

        self.workflow_execution_service.finish_state(
            execution_state_id=execution_state_id,
            output=processed_output,
            status=status,
            truncated=truncated,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/workflows/nodes/test_base_node_truncation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/workflows/nodes/base_node.py tests/codemie/workflows/nodes/test_base_node_truncation.py
git commit -m "EPMCDME-14145: forward truncated from node output to finish_state"
```

Test-first: yes — the new test file written and run to fail against the current unkeyworded `finish_state` call before the extraction/forwarding is added.

---

### Task 7: Data model fields + Alembic migration

**Files:**
- Modify: `src/codemie/core/workflow_models/workflow_execution.py:103-121` (`BaseWorkflowExecutionState`), `src/codemie/core/workflow_models/workflow_execution.py:368-370` (`WorkflowExecutionStateOutput`)
- Create: `src/external/alembic/versions/t1r2u3n4c5a6_add_truncated_to_workflow_execution_states.py` (replaces the earlier `..._add_finish_reason_truncated_...` draft, which was never released)
- Test: none new — these are declarative model/schema changes with no branching logic; covered by Task 5's and Task 8's tests exercising the model fields, and the migration itself is verified by running it (Step 2 below), not by a pytest test.

**Interfaces:**
- Consumes: nothing new.
- Produces: `BaseWorkflowExecutionState.truncated: bool = False` (inherited by `WorkflowExecutionState`, `WorkflowExecutionStateResponse`, `WorkflowExecutionStateWithThougths`); `WorkflowExecutionStateOutput.truncated: bool = False`. Task 5 assigns `state.truncated` on a `WorkflowExecutionState` instance (relies on this task); Task 8 reads `state.truncated` and constructs `WorkflowExecutionStateOutput(..., truncated=...)` (relies on this task). One new defaulted, not-null column exists on the `workflow_execution_states` table after the migration runs.

- [ ] **Step 1: Modify the data models**

In `src/codemie/core/workflow_models/workflow_execution.py`:

```python
class BaseWorkflowExecutionState(CommonBaseModel):
    execution_id: str = SQLField(index=True)
    name: str
    state_id: Optional[str] = None
    task: Optional[str] = ""
    status: WorkflowExecutionStatusEnum = WorkflowExecutionStatusEnum.NOT_STARTED
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    preceding_state_ids: Optional[list[str]] = SQLField(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    iteration_number: Optional[int] = None
    history_index: Optional[int] = None
    truncated: bool = False
```

```python
class WorkflowExecutionStateOutput(BaseModel):
    output: Optional[str] = None
    truncated: bool = False
```

- [ ] - [ ] **Step 2: Write the Alembic migration**

Create `src/external/alembic/versions/t1r2u3n4c5a6_add_truncated_to_workflow_execution_states.py` (as built; `revision` and `down_revision` match the existing chain, `e5f9a1c3b7d2`):

```python
"""add_truncated_to_workflow_execution_states

Revision ID: t1r2u3n4c5a6
Revises: e5f9a1c3b7d2
Create Date: 2026-09-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t1r2u3n4c5a6"
down_revision: Union[str, None] = "e5f9a1c3b7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_execution_states",
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("workflow_execution_states", "truncated")
```

- [ ] **Step 3: Run the migration to verify it applies**

Run: `poetry run alembic -c src/external/alembic/alembic.ini upgrade head`
Expected: migration `t1r2u3n4c5a6` applies cleanly with no errors, and `poetry run alembic -c src/external/alembic/alembic.ini heads` now reports `t1r2u3n4c5a6 (head)`.

- [ ] **Step 4: Commit**

```bash
git add src/codemie/core/workflow_models/workflow_execution.py src/external/alembic/versions/t1r2u3n4c5a6_add_truncated_to_workflow_execution_states.py
git commit -m "EPMCDME-14145: add truncated to WorkflowExecutionState + migration"
```

Test-first: no — declarative field/schema additions with no logic branch to pin down in a unit test on their own; correctness is verified by running the migration (Step 3) and by Task 5/Task 8's tests, which exercise these fields through real behavior.

---

### Task 8: API router exposes the signal

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow_executions.py:696-704`
- Test: `tests/codemie/rest_api/routers/test_workflow_executions.py`

**Interfaces:**
- Consumes: `WorkflowExecutionState.truncated`, `WorkflowExecutionStateOutput(truncated=...)` (Task 7).
- Produces: `GET /v1/workflows/{workflow_id}/executions/{execution_id}/states/{state_id}/output` now returns `{"output": ..., "truncated": ...}` — still `200`, still `WorkflowExecutionStateOutput`.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/rest_api/routers/test_workflow_executions.py`, after the existing `test_update_workflow_execution_output_no_permissions` test:

```python
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutionState.get_by_id")
@pytest.mark.asyncio
async def test_get_workflow_execution_state_output_includes_truncation_signal(
    mock_get_by_id,
    _mock_get_workflow,
    mock_ability,
    mock_authentication,
    request_headers,
):
    mock_authentication.authenticate.return_value = USER
    mock_ability.return_value.can.return_value = True
    mock_state = MagicMock()
    mock_state.output = "cut off output"
    mock_state.truncated = True
    mock_get_by_id.return_value = mock_state

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            f"{WORKFLOW_EXECUTIONS_URL}/{EXECUTION_ID}/states/{STATE_ID}/output",
            headers=request_headers,
        )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["output"] == "cut off output"
    assert body["truncated"] is True


@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutionState.get_by_id")
@pytest.mark.asyncio
async def test_get_workflow_execution_state_output_defaults_when_not_truncated(
    mock_get_by_id,
    _mock_get_workflow,
    mock_ability,
    mock_authentication,
    request_headers,
):
    mock_authentication.authenticate.return_value = USER
    mock_ability.return_value.can.return_value = True
    mock_state = MagicMock()
    mock_state.output = "complete output"
    mock_state.truncated = False
    mock_get_by_id.return_value = mock_state

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            f"{WORKFLOW_EXECUTIONS_URL}/{EXECUTION_ID}/states/{STATE_ID}/output",
            headers=request_headers,
        )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["output"] == "complete output"
    assert body["truncated"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest "tests/codemie/rest_api/routers/test_workflow_executions.py::test_get_workflow_execution_state_output_includes_truncation_signal" "tests/codemie/rest_api/routers/test_workflow_executions.py::test_get_workflow_execution_state_output_defaults_when_not_truncated" -v`
Expected: FAIL — the response body has no `"truncated"` key yet (`KeyError` on `body["truncated"]`).

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/rest_api/routers/workflow_executions.py`:

```python
def get_workflow_execution_state_output(
    workflow_id: str, execution_id: str, state_id: str, user: User = Depends(authenticate)
):
    workflow_config = WorkflowService().get_workflow(workflow_id)

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    state = WorkflowExecutionState.get_by_id(state_id)
    return WorkflowExecutionStateOutput(
        output=state.output,
        truncated=state.truncated,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_workflow_executions.py -v`
Expected: PASS (full file).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/routers/workflow_executions.py tests/codemie/rest_api/routers/test_workflow_executions.py
git commit -m "EPMCDME-14145: expose truncated on workflow state output API"
```

Test-first: yes — the two new router tests written and run to fail before the route reads/returns the new fields.

---

### Task 9: Confirmatory test for LiteLLM `max_tokens` forwarding (criterion 3)

**Files:**
- Test: `tests/enterprise/litellm/test_llm_factory.py`

**Interfaces:**
- Consumes: `_filter_litellm_params(base_args: dict, llm_model_details: LLMModel) -> dict` (existing, unmodified).
- Produces: a regression test locking down the existing behavior; no new interface.

- [ ] **Step 1: Write the test**

Add to `tests/enterprise/litellm/test_llm_factory.py`, as a new class:

```python
class TestFilterLiteLLMParamsMaxTokensForwarding:
    """EPMCDME-14145 criterion 3: confirm max_tokens forwarding through the LiteLLM proxy path."""

    def test_max_tokens_forwarded_unchanged_when_feature_enabled(self):
        from codemie.enterprise.litellm.llm_factory import _filter_litellm_params

        mock_model_details = MagicMock()
        mock_model_details.features.temperature = True
        mock_model_details.features.parallel_tool_calls = True
        mock_model_details.features.streaming = True
        mock_model_details.features.max_tokens = True
        mock_model_details.features.top_p = True

        base_args = {"max_tokens": 4096, "temperature": 0.5}

        filtered = _filter_litellm_params(base_args, mock_model_details)

        assert filtered["max_tokens"] == 4096

    def test_max_tokens_filtered_to_none_when_feature_disabled(self):
        from codemie.enterprise.litellm.llm_factory import _filter_litellm_params

        mock_model_details = MagicMock()
        mock_model_details.features.temperature = True
        mock_model_details.features.parallel_tool_calls = True
        mock_model_details.features.streaming = True
        mock_model_details.features.max_tokens = False
        mock_model_details.features.top_p = True

        base_args = {"max_tokens": 4096, "temperature": 0.5}

        filtered = _filter_litellm_params(base_args, mock_model_details)

        assert filtered["max_tokens"] is None
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `poetry run pytest tests/enterprise/litellm/test_llm_factory.py::TestFilterLiteLLMParamsMaxTokensForwarding -v`
Expected: PASS immediately — `_filter_litellm_params` already implements this behavior (lines 1037-1038 of `llm_factory.py`); this test is confirmatory, not a fix. If it fails, that is a real mismatch in existing behavior and becomes explicit follow-up scope per the spec's Design §6 — do not silently patch `llm_factory.py` to make it pass without raising this with the user first.

- [ ] **Step 3: Commit**

```bash
git add tests/enterprise/litellm/test_llm_factory.py
git commit -m "EPMCDME-14145: add confirmatory test for LiteLLM max_tokens forwarding"
```

Test-first: no (by design) — this is a confirmatory/regression test for already-correct behavior (spec Design §6: "This confirms and documents existing behavior. No production code change ... is planned"), so there is no RED phase to drive; the assertion of "PASS immediately" in Step 2 *is* the check that the behavior is what criterion 3 requires.

---

### Task 10: Surface `truncated` on `WorkflowState`-authored conversation thoughts

**Files:**
- Modify: `src/codemie/chains/base.py:38-53` (`Thought`)
- Modify: `src/codemie/service/conversation/history_materializer.py:156-170` (`_get_execution_thoughts`)
- Modify: `src/codemie/workflows/nodes/base_node.py:248-253` (`_run_success_path`'s `on_node_end` call)
- Modify: `src/codemie/workflows/callbacks/graph_callback.py:91-106` (`LanggraphNodeCallback.on_node_end`)
- Test: `tests/codemie/service/test_history_materializer.py`, `tests/codemie/workflows/test_base_node_lifecycle.py`, new `tests/codemie/workflows/callbacks/test_graph_callback.py`

**Interfaces:**
- Consumes: `WorkflowExecutionState.truncated` (Design §4, already persisted by Task 7); the `truncated` local already computed in `_run_success_path` (Task 6, currently only forwarded to `finish_state`).
- Produces: `Thought.truncated: bool = False`; `_get_execution_thoughts` dicts carry `truncated`; `callback.on_node_end(..., truncated=...)` call shape; `LanggraphNodeCallback.on_node_end` sets `truncated` on the streamed `Thought` before `gen.send(...)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/test_history_materializer.py` (new test, exercising `_get_execution_thoughts` directly rather than through the mocked-out `materialize_workflow_conversation` fixture):

```python
class TestGetExecutionThoughts:
    def test_includes_truncated_from_state(self):
        from codemie.service.conversation.history_materializer import _get_execution_thoughts

        state = MagicMock()
        state.id = "state-1"
        state.name = "Some State"
        state.output = "cut off output"
        state.status = WorkflowExecutionStatusEnum.SUCCEEDED
        state.history_index = None
        state.truncated = True

        with patch(
            "codemie.core.workflow_models.WorkflowExecutionState.get_all_by_fields",
            return_value=[state],
        ):
            thoughts = _get_execution_thoughts("exec-1")

        assert thoughts[0]["truncated"] is True
```

Extend `tests/codemie/workflows/test_base_node_lifecycle.py::test_callback_lifecycle_integration` (or add a sibling test) to assert the new kwargs on the existing `on_node_end` assertion:

```python
def test_on_node_end_receives_truncation_signal(mock_workflow_execution_service, mock_thought_queue, mock_callbacks):
    node = MockNode(
        node_name="agent_node",
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        callbacks=mock_callbacks,
    )
    node.mock_execute_result = TaskResult(result="cut off", success=True, truncated=True)

    node(state_schema={MESSAGES_VARIABLE: []}, execution_context={})

    end_call_kwargs = mock_callbacks[0].on_node_end.call_args.kwargs
    assert end_call_kwargs["truncated"] is True
```

(Adjust node construction to match this file's existing `MockNode`/fixture conventions exactly — see the surrounding tests in the same file for the precise constructor signature.)

Create `tests/codemie/workflows/callbacks/test_graph_callback.py`:

```python
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

import json
from unittest.mock import MagicMock

from codemie.workflows.callbacks.graph_callback import LanggraphNodeCallback


def test_on_node_end_sets_truncation_signal_on_streamed_thought():
    gen = MagicMock()
    callback = LanggraphNodeCallback(gen=gen)
    callback.set_current_thought("state-1", "agent_name")

    callback.on_node_end(
        output="cut off output",
        execution_state_id="state-1",
        execution_context={},
        truncated=True,
    )

    sent_payload = json.loads(gen.send.call_args_list[-1].args[0])
    assert sent_payload["thought"]["truncated"] is True


def test_on_node_end_defaults_truncation_signal_when_absent():
    gen = MagicMock()
    callback = LanggraphNodeCallback(gen=gen)
    callback.set_current_thought("state-1", "agent_name")

    callback.on_node_end(output="ok", execution_state_id="state-1", execution_context={})

    sent_payload = json.loads(gen.send.call_args_list[-1].args[0])
    assert sent_payload["thought"]["truncated"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_history_materializer.py::TestGetExecutionThoughts tests/codemie/workflows/test_base_node_lifecycle.py::test_on_node_end_receives_truncation_signal tests/codemie/workflows/callbacks/test_graph_callback.py -v`
Expected: FAIL — `Thought`/the returned dict have no `truncated` keys yet, and `on_node_end` isn't called with those kwargs.

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/chains/base.py`, add to `Thought`:

```python
class Thought(BaseModel):
    ...
    aborted: Optional[bool] = False
    truncated: Optional[bool] = False  # EPMCDME-14145: True when the terminal message was truncated
    children: List['Thought'] = Field(default_factory=list)
```

In `src/codemie/service/conversation/history_materializer.py`, `_get_execution_thoughts`, add to the dict:

```python
        return [
            {
                "id": state.id,
                "author_name": state.name,
                "author_type": "WorkflowState",
                "message": state.output or "",
                "input_text": None,
                "children": [],
                "in_progress": False,
                "interrupted": state.status == WorkflowExecutionStatusEnum.INTERRUPTED,
                "aborted": state.status == WorkflowExecutionStatusEnum.ABORTED,
                "truncated": state.truncated,
            }
            for state in states
            if state.output or state.status == WorkflowExecutionStatusEnum.ABORTED
        ]
```

In `src/codemie/workflows/nodes/base_node.py`, `_run_success_path`:

```python
        for callback in self.callbacks:
            callback.on_node_end(
                output=processed_output,
                execution_state_id=execution_state_id,
                execution_context=execution_context,
                truncated=truncated,
            )
```

In `src/codemie/workflows/callbacks/graph_callback.py`, `on_node_end`:

```python
    def on_node_end(self, output: str, *args, truncated=False, **kwargs):
        state_id = kwargs.get('execution_state_id')
        message = f"{output} \n\n"
        if state_id in self._thoughts:
            current_thought = self._thoughts[state_id]
            current_thought.message = self._escape_message(message)
            current_thought.in_progress = False
            current_thought.truncated = truncated

            self.gen.send(
                StreamedGenerationResult(
                    thought=current_thought,
                    context=self.get_thread_context(state_id),
                ).model_dump_json()
            )

            self.reset_current_thought(state_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/test_history_materializer.py tests/codemie/workflows/test_base_node_lifecycle.py tests/codemie/workflows/callbacks/test_graph_callback.py -v`
Expected: PASS. Also re-run the full existing suite for these three files to confirm no regression on unrelated tests (e.g. `on_node_fail`, which is deliberately untouched — no `truncated` on the failure path).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/chains/base.py src/codemie/service/conversation/history_materializer.py src/codemie/workflows/nodes/base_node.py src/codemie/workflows/callbacks/graph_callback.py tests/codemie/service/test_history_materializer.py tests/codemie/workflows/test_base_node_lifecycle.py tests/codemie/workflows/callbacks/test_graph_callback.py
git commit -m "EPMCDME-14145: surface truncated on WorkflowState-authored conversation thoughts"
```

Test-first: yes — all three new/extended test targets written and run to fail (Step 2) before the `Thought` field additions and the two call-site changes (Step 3).

---

## Self-Review

**1. Spec coverage:**
- Goal 1 (terminal-message detection, not just tool-call) → Tasks 1–2.
- Goal 2 (API exposes `truncated`) → Tasks 3–8 (full thread: detector → `TaskResult` → `finish_state` → data model/migration → router).
- Goal 3 (LiteLLM `max_tokens` confirmed) → Task 9.
- Goal 5 (conversation history thoughts for `WorkflowState`-authored entries surface `truncated`) → Task 10 (both the retrieval/materialization path and the live-streaming path), with the `_filter_thoughts` non-applicability documented in the spec's Scope.
- Goal 4 (regression tests across providers: OpenAI/LiteLLM `finish_reason=length`, Claude/Bedrock `stop_reason`/`stopReason=max_tokens`) → Task 1 (`TestDetectTerminalTruncation`) and Task 2 (`TestTerminalMessageTruncationDetection`), both parametrized across all three provider shapes, for both the primary and supervisor paths.
- Design §1 (non-raising detector + widened gating) → Tasks 1–2, with the deviation from literal spec text documented in Global Constraints.
- Design §2 (`TaskResult`) → Task 3.
- Design §3 (`BaseNode`/`finish_state`/`WorkflowStateEvent`) → Tasks 4–6, with the `WorkflowStateEvent` deviation documented in Global Constraints.
- Design §4 (data model + migration) → Task 7.
- Design §5 (API) → Task 8.
- Design §6 (LiteLLM confirmatory test) → Task 9.
- Risks/Notes: fail-open pattern → `_detect_terminal_truncation`'s try/except in Task 1; both `LLM_PROXY_MODE` paths → not distinguished at the detector level (the raw `finish_reason`/`stop_reason`/`stopReason` keys are provider-shape-driven, not proxy-mode-driven, matching `_get_truncation_indicator`'s own existing behavior — no additional test axis needed beyond the three provider shapes already covered); supervisor path fixed in lockstep → Task 2 covers both `parse_message_type` and `parse_supervisor_message_type` in the same task.

**2. Placeholder scan:** No `TBD`/`TODO` markers; every step has a complete, runnable code block. Every test in every task asserts concrete expected values.

**3. Type consistency:** `is_finish_reason_truncated(message: AIMessage) -> bool` and `_detect_terminal_truncation(message: AIMessage) -> None` (Task 1) are called with exactly these names/signatures in Task 2. `TaskResult.truncated: bool` (Task 3) matches the `getattr(raw_output, "truncated", False)` read in Task 6. `finish_state(..., truncated: bool = False)` (Task 5) matches the call in Task 6. `WorkflowExecutionStateOutput(output=..., truncated=...)` (Task 7's field addition) matches the construction in Task 8. `Thought.truncated` and `on_node_end(..., truncated=...)` (Task 10) match the call in Task 6.

**4. Revision log (2026-10-16):** the `finish_reason` field was removed (commit `0c6477423`), leaving `truncated` only. Also: detection in `parse_message_type` is restricted to the `agent` node; `WorkflowUpdateOutputService._update_state` clears `truncated` on output edits; the migration is renamed and adds only `truncated`. Tasks 1–10 were updated to match the as-built code. Remaining open item: the supervisor path is not node-restricted (see spec Risks).
