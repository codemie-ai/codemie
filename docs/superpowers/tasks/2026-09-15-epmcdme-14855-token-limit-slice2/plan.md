# EPMCDME-14855 Slice 2/2 — Token-Limit Error Consolidation + Consumer-Loop Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a consecutive-tool-error fatal-exit cap (A.5), consolidate the repeated tool-error
thoughts it would otherwise cause under one visible message via the existing
`agent_token_limit` classifier (A.6), and give both streaming consumer loops
(`hedged_handler.py`, `assistant_handlers.py`) a `queue.Empty` timeout that yields a final error
chunk and saves history instead of hanging/breaking silently (B.9/B.10).

**Architecture:** Builds entirely on slice 1 (A.1-A.4, B.7, B.8, already merged): any exception
raised inside `LangGraphAgent._stream_graph`'s loop is already caught, the stream is already
closed/cancelled, and `_send_error_to_thread` already populates `success`/`agent_error`/
`error_details`. This slice adds one new fatal exception (A.5) that rides that exact path, a
callback-side consolidation of the thoughts it produces along the way (A.6), and two independent,
mechanical consumer-loop fixes (B.9/B.10) reusing the same classifier (`AgentErrorClassifier`
via `handle_agent_exception`) `_send_error_to_thread` already uses.

**Tech Stack:** Python, pytest, pydantic-settings (`Config`), LangChain/LangGraph tool callbacks.

**Spec:** `docs/superpowers/tasks/2026-09-15-epmcdme-14855-token-limit-slice2/spec.md`

## Global Constraints

- Backend (`codemie/`) only. No `codemie-ui/` changes.
- No change to `core/errors.py`'s classifier chain — A.5's new exception must satisfy the
  *existing* `AgentErrorClassifier._try_token_limit` substring match (`"max_output_tokens"`,
  `"truncat"`, `"token limit"`, `"token limit exceeded"`, case-insensitive).
- No shared helper unifying `hedged_handler.py`'s and `assistant_handlers.py`'s consumer loops —
  fix each independently.
- B.9/B.10 are consumer-side only: no signalling/cancelling the producer/agent thread.
- A.5's cap counts any consecutive tool error (validation or execution) — no distinguishing.
- No `SupervisorCoordinator` changes. No change to `HIDE_AGENT_STREAMING_EXCEPTIONS` itself.
- Commit per task using the repository's existing convention (see `git log` for the prevailing
  style) — one commit per task below.

---

### Task 1: A.5a — config key + per-instance consecutive-tool-error counter

**Files:**
- Modify: `src/codemie/configs/config.py:744` (new key next to `AI_AGENT_RECURSION_LIMIT`)
- Modify: `src/codemie/agents/langgraph_agent.py:184` (constructor, new instance attribute),
  `src/codemie/agents/langgraph_agent.py:1445-1452` (`_on_tool_end`),
  `src/codemie/agents/langgraph_agent.py:1473-1474` (`_on_tool_error`)
- Test: Create `tests/codemie/agents/test_langgraph_tool_error_cap.py`

**Interfaces:**
- Produces: `LangGraphAgent._consecutive_tool_error_count: int` — incremented in `_on_tool_error`,
  reset to `0` in `_on_tool_end`. Task 2 reads/raises on this same attribute.
- Produces: `config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS: int` (default `5`).

- [ ] **Step 1: Write the failing test**

```python
import uuid
from unittest.mock import Mock
from codemie.agents.langgraph_agent import LangGraphAgent


def _make_agent():
    agent = object.__new__(LangGraphAgent)
    agent._consecutive_tool_error_count = 0
    agent._callback_bridge = Mock()
    return agent


def test_on_tool_error_increments_streak_counter():
    agent = _make_agent()
    agent._on_tool_error("boom", run_id=uuid.uuid4())
    assert agent._consecutive_tool_error_count == 1


def test_on_tool_end_resets_streak_counter():
    agent = _make_agent()
    agent._consecutive_tool_error_count = 3
    agent._on_tool_end("ok", run_id=uuid.uuid4())
    assert agent._consecutive_tool_error_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/agents/test_langgraph_tool_error_cap.py -v`
Expected: FAIL — `_consecutive_tool_error_count` not set/not incremented (attribute exists from
the helper but `_on_tool_error`/`_on_tool_end` don't touch it yet).

- [ ] **Step 3: Implement**

Add `AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS: int = 5` immediately after
`AI_AGENT_RECURSION_LIMIT` in `config.py:744`. In the constructor (`langgraph_agent.py:184`, next
to `self.handle_tool_error = handle_tool_error`), add
`self._consecutive_tool_error_count = 0`. Change `_on_tool_end`
(`langgraph_agent.py:1445-1452`) to set `self._consecutive_tool_error_count = 0` before delegating
to `self._callback_bridge.on_tool_end(...)`. Change `_on_tool_error`
(`langgraph_agent.py:1473-1474`) to do `self._consecutive_tool_error_count += 1` before delegating
to `self._callback_bridge.on_tool_error(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/agents/test_langgraph_tool_error_cap.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 2: A.5b — raise `MaxConsecutiveToolErrorsExceededException` at the cap

**Files:**
- Modify: `src/codemie/core/exceptions.py` (new exception class, after `TokenLimitExceededException`)
- Modify: `src/codemie/agents/langgraph_agent.py:112` (import),
  `src/codemie/agents/langgraph_agent.py:1473-1474` (`_on_tool_error`)
- Test: `tests/codemie/agents/test_langgraph_tool_error_cap.py` (extend)

**Interfaces:**
- Consumes: `LangGraphAgent._consecutive_tool_error_count` (Task 1), `config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS` (Task 1).
- Produces: `codemie.core.exceptions.MaxConsecutiveToolErrorsExceededException` — raised, not
  swallowed by `handle_tool_error`; propagates through the already-merged slice-1 exception path
  in `_stream_graph`. Message contains `"token limit"` so it satisfies
  `AgentErrorClassifier._try_token_limit` unchanged.

- [ ] **Step 1: Write the failing test**

```python
from codemie.core.exceptions import MaxConsecutiveToolErrorsExceededException
from codemie.core.errors import AgentErrorClassifier, ErrorCode
from codemie.configs import config


def test_on_tool_error_raises_at_configured_cap():
    agent = _make_agent()
    for _ in range(config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS - 1):
        agent._on_tool_error("boom", run_id=uuid.uuid4())

    with pytest.raises(MaxConsecutiveToolErrorsExceededException) as exc_info:
        agent._on_tool_error("boom", run_id=uuid.uuid4())

    classified = AgentErrorClassifier().classify(exc_info.value)
    assert classified.agent_error.error_code == ErrorCode.AGENT_TOKEN_LIMIT


def test_on_tool_error_delegates_below_the_cap():
    agent = _make_agent()
    agent._on_tool_error("boom", run_id=uuid.uuid4())
    agent._callback_bridge.on_tool_error.assert_called_once()
```

(add `import pytest` at the top of the test file)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/agents/test_langgraph_tool_error_cap.py -v`
Expected: FAIL — `MaxConsecutiveToolErrorsExceededException` doesn't exist / cap never raises.

- [ ] **Step 3: Implement**

```python
class MaxConsecutiveToolErrorsExceededException(Exception):
    """Raised when AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS consecutive tool errors occur in a row.

    The message deliberately contains a substring AgentErrorClassifier._try_token_limit
    already matches ("token limit"), so this fatal exit is classified and surfaced as
    ErrorCode.AGENT_TOKEN_LIMIT without any change to core/errors.py.
    """
```

Add this class to `src/codemie/core/exceptions.py` after `TokenLimitExceededException`. Add it to
the existing import at `langgraph_agent.py:112`. Change `_on_tool_error` to:

```python
def _on_tool_error(self, output, run_id: UUID | None = None, author: str | None = None):
    self._consecutive_tool_error_count += 1
    if self._consecutive_tool_error_count >= config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS:
        raise MaxConsecutiveToolErrorsExceededException(
            f"{self._consecutive_tool_error_count} consecutive tool errors "
            "(token limit exceeded threshold); aborting run."
        )
    self._callback_bridge.on_tool_error(output, run_id=run_id, author=author)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/agents/test_langgraph_tool_error_cap.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 3: A.6 — consolidate consecutive tool-error thoughts into one streak

**Files:**
- Modify: `src/codemie/agents/callbacks/agent_streaming_callback.py:105-117` (`__init__`),
  `:348-384` (`on_tool_end`), `:386-414` (`on_tool_error`)
- Test: `tests/codemie/agents/callbacks/test_agent_streaming_callback.py` (add alongside
  `test_on_tool_error_sends_with_error_flag` at line 342)

**Interfaces:**
- Produces: `AgentStreamingCallback._active_error_streak: dict[str | None, str]` — maps `author`
  to the `Thought.id` currently representing that author's in-flight error streak.

- [ ] **Step 1: Write the failing test**

```python
@patch('codemie.agents.callbacks.agent_streaming_callback.set_logging_info')
@patch('codemie.agents.callbacks.agent_streaming_callback.StreamedGenerationResult')
def test_on_tool_error_consecutive_calls_reuse_same_thought_id(mock_streamed_result, mock_logging):
    mock_queue = MagicMock()
    mock_streamed_result.return_value = Mock(model_dump_json=Mock(return_value='{}'))
    callback = AgentStreamingCallback(gen=mock_queue)

    run_id_1, run_id_2 = uuid.uuid4(), uuid.uuid4()
    callback.thoughts_storage[str(run_id_1)] = Mock(spec=Thought, id=str(run_id_1), metadata=None)
    callback.thoughts_storage[str(run_id_2)] = Mock(spec=Thought, id=str(run_id_2), metadata=None)

    callback.on_tool_error(Exception("boom 1"), run_id=run_id_1)
    first_thought = mock_streamed_result.call_args.kwargs['thought']
    callback.on_tool_error(Exception("boom 2"), run_id=run_id_2)
    second_thought = mock_streamed_result.call_args.kwargs['thought']

    assert first_thought.id == second_thought.id


@patch('codemie.agents.callbacks.agent_streaming_callback.set_logging_info')
def test_on_tool_end_success_starts_a_new_streak(mock_logging):
    mock_queue = MagicMock()
    callback = AgentStreamingCallback(gen=mock_queue)
    callback._active_error_streak[None] = "some-old-id"

    callback.thoughts_storage[str(uuid.uuid4())] = Mock(spec=Thought, metadata=None)
    run_id = list(callback.thoughts_storage)[0]
    callback.on_tool_end("ok", run_id=run_id)

    assert None not in callback._active_error_streak
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/agents/callbacks/test_agent_streaming_callback.py -k streak -v`
Expected: FAIL — `_active_error_streak` doesn't exist yet; ids differ per call.

- [ ] **Step 3: Implement**

In `__init__` (after `self._storages = {...}` at line 114), add
`self._active_error_streak: dict[str | None, str] = {}`.

In `on_tool_error` (`:386-414`), after `thought = storage.update_thought(run_id, message=message,
in_progress=False, error=True)` and before `self._send_thought(thought, execution_error)`: if
`self._active_error_streak.get(author)` is set, overwrite `thought.id` with that value so the
client updates the existing bubble in place; otherwise this is the first error of a new streak —
record `self._active_error_streak[author] = thought.id`. Storage cleanup
(`storage.delete_thought(run_id)`) stays keyed by the original `run_id`, unaffected by the `id`
override.

In `on_tool_end` (`:348-384`), at the top of the method (before the early-return on `thought is
None`), add `self._active_error_streak.pop(author, None)` — a tool success always ends any
in-flight error streak for that author.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/agents/callbacks/test_agent_streaming_callback.py -v`
Expected: PASS (including the two pre-existing tests at lines 342 and 364, unaffected)

- [ ] **Step 5: Commit**

---

### Task 4: B.9 — `hedged_handler.py::_stream_agent_path` queue.Empty handling

**Files:**
- Modify: `src/codemie/rest_api/handlers/hedged_handler.py:31` (import),
  `:393-397` (the `queue.Empty` branch)
- Test: `tests/codemie/rest_api/handlers/test_hedged_handler.py` (new test class)

**Interfaces:**
- Consumes: `StreamedGenerationResult` (`chains/base.py`), `handle_agent_exception`
  (`codemie.enterprise.litellm.proxy_router`, same helper `_send_error_to_thread` already uses).

- [ ] **Step 1: Write the failing test**

```python
import queue
from unittest.mock import Mock
from time import time
from codemie.core.errors import ErrorDetailLevel


def test_stream_agent_path_queue_empty_yields_final_chunk_and_saves_history(handler, request_):
    agent_queue = Mock()
    agent_queue.get = Mock(side_effect=queue.Empty)
    agent_queue.thoughts = []

    with patch.object(handler, "save_chat_history") as mock_save:
        chunks = list(
            handler._stream_agent_path(
                agent=Mock(),
                agent_queue=agent_queue,
                execution_start=time(),
                request=request_,
                include_tool_errors=False,
                error_detail_level=ErrorDetailLevel.STANDARD,
            )
        )

    assert len(chunks) == 1
    data = json.loads(chunks[0])
    assert data["last"] is True
    assert data["success"] is False
    mock_save.assert_called_once()
```

Add this to `test_hedged_handler.py` (reuses the module's existing `handler`/`request_`
fixtures; `queue`, `json`, `Mock`, `patch.object` are already imported there).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/rest_api/handlers/test_hedged_handler.py -k queue_empty -v`
Expected: FAIL — today's code yields nothing and never calls `save_chat_history` on
`queue.Empty`.

- [ ] **Step 3: Implement**

Add `from codemie.enterprise.litellm.proxy_router import handle_agent_exception` to the imports.
Replace the `except queue.Empty:` branch at `hedged_handler.py:395-397`:

```python
except queue.Empty:
    logger.error(f"[HEDGED][coordinator] agent queue timed out assistant_id={self.assistant.id}")
    error_response = handle_agent_exception(TimeoutError("Agent stream timed out waiting for the next chunk"))
    error = error_response.get_error()
    timeout_result = StreamedGenerationResult(
        generated=error.message,
        generated_chunk="",
        last=True,
        time_elapsed=time() - execution_start,
        success=False,
        agent_error=error_response.agent_error,
        error_details=error.model_dump(mode="json") if error else None,
    )
    yield timeout_result.model_dump_json()
    self.save_chat_history(
        ChatHistoryData(
            execution_start,
            request,
            timeout_result.generated,
            agent_queue.thoughts,
            user_message_received_at=user_message_received_at,
        )
    )
    break
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/rest_api/handlers/test_hedged_handler.py -v`
Expected: PASS (full file, to catch any regression in the 60+ existing tests)

- [ ] **Step 5: Commit**

---

### Task 5: B.10 — `assistant_handlers.py::_serve_data` unbounded queue.get()

**Files:**
- Modify: `src/codemie/rest_api/handlers/assistant_handlers.py:15-79` (add `import queue` and the
  `handle_agent_exception` import), `:742-743` (the blocking `get()` call)
- Test: `tests/codemie/rest_api/handlers/test_assistant_handlers_streaming.py` (new test)

**Interfaces:**
- Consumes: same `StreamedGenerationResult` / `handle_agent_exception` pattern as Task 4 (B.9),
  applied independently per the plan's non-goal of not sharing a helper between the two handlers.

- [ ] **Step 1: Write the failing test**

```python
import queue
from unittest.mock import Mock


def test_serve_data_queue_empty_yields_final_chunk_and_saves_history(mock_user, mock_assistant):
    handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
    generator_queue = ThreadedGenerator()
    generator_queue.queue.get = Mock(side_effect=queue.Empty)

    with patch.object(handler, "save_chat_history") as mock_save:
        chunks = list(
            handler._serve_data(
                stream=lambda: None,
                generator_queue=generator_queue,
                request=AssistantChatRequest(text="run", stream=True),
                execution_start=0.0,
            )
        )

    assert len(chunks) == 1
    data = json.loads(chunks[0])
    assert data["last"] is True
    assert data["success"] is False
    mock_save.assert_called_once()
```

Add this to `test_assistant_handlers_streaming.py` (reuses its existing `mock_user`/
`mock_assistant` fixtures and imports; `queue` needs adding to that file's imports).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/rest_api/handlers/test_assistant_handlers_streaming.py -k queue_empty -v`
Expected: FAIL — today's unbounded `.get()` never raises `queue.Empty` (the monkeypatched
`side_effect` is never reached the way the fix will reach it) / no final chunk is yielded.

- [ ] **Step 3: Implement**

Add `import queue` to `assistant_handlers.py`'s imports, and
`from codemie.enterprise.litellm.proxy_router import handle_agent_exception`. Replace
`value = generator_queue.queue.get()` at `assistant_handlers.py:743` with a `try/except`:

```python
try:
    value = generator_queue.queue.get(timeout=300)
except queue.Empty:
    logger.error(f"[STANDARD] generator queue timed out assistant_id={self.assistant.id}")
    error_response = handle_agent_exception(TimeoutError("Agent stream timed out waiting for the next chunk"))
    error = error_response.get_error()
    timeout_result = StreamedGenerationResult(
        generated=error.message,
        generated_chunk="",
        last=True,
        time_elapsed=time() - execution_start,
        success=False,
        agent_error=error_response.agent_error,
        error_details=error.model_dump(mode="json") if error else None,
    )
    yield timeout_result.model_dump_json()
    self.save_chat_history(
        ChatHistoryData(
            execution_start=execution_start,
            request=request,
            response=timeout_result.generated,
            thoughts=generator_queue.thoughts,
            user_message_received_at=user_message_received_at,
            a2ui_envelopes=a2ui_envelopes or None,
        )
    )
    break
```

This sits inside the existing `while True:` loop (`:742`), as a new `try/except` wrapping only the
`.get()` call — the untouched `if isinstance(value, BaseException): raise value` branch right
after it (`:744-746`) still handles the *different*, pre-existing case of a producer-signalled
exception, not a timeout.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/rest_api/handlers/test_assistant_handlers_streaming.py -v`
Expected: PASS (full file, including the pre-existing `_serve_data` tests at lines 302 and 335)

- [ ] **Step 5: Commit**

---

## Negative-constraint check

- No change to `core/errors.py`: Tasks 1-5 touch `configs/config.py`, `core/exceptions.py`,
  `langgraph_agent.py`, `agent_streaming_callback.py`, `hedged_handler.py`,
  `assistant_handlers.py` only — `core/errors.py` is read, never modified.
- No shared B.9/B.10 helper: Tasks 4 and 5 each inline their own `try/except queue.Empty` block;
  no new function is introduced that both files import.
- B.9/B.10 don't touch the producer thread: neither task's steps call `close()`, cancel, or
  signal `agent`/the worker thread — only `yield` + `save_chat_history` on the consumer side.
- A.5 doesn't distinguish error kinds: Task 1/2's counter increments on every `_on_tool_error`
  call regardless of the underlying error's type or message.
- No `SupervisorCoordinator`/`codemie-ui/`/`HIDE_AGENT_STREAMING_EXCEPTIONS` changes: none of the
  5 tasks touch `supervisor/coordinator.py`, any file under `codemie-ui/`, or the
  `HIDE_AGENT_STREAMING_EXCEPTIONS` branch in `_process_chunks`.
