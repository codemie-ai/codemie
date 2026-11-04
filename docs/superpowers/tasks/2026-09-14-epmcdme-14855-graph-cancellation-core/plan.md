# Graph Cancellation Core (EPMCDME-14855, slice 1/3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the orphaned LangGraph execution graph on a fatal (token-limit) error and deliver a
proper client-facing error, without touching consecutive-tool-error capping, error consolidation,
or the two REST consumer-loop timeout fixes (those are separate follow-up slices).

**Architecture:** Six independent-but-ordered wiring changes across the agent runtime
(`langgraph_agent.py`), supervisor coordination (`coordinator.py`), the thread bridge
(`core/thread.py`), and the two REST handlers that consume it (`assistant_handlers.py`,
`hedged_handler.py`). No new classes, no new config keys, no new `ErrorCode`s — every field or
hook this plan wires already exists.

**Tech Stack:** Python, pytest, LangGraph 1.1.6, Pydantic (`StreamedGenerationResult`).

**Spec:** `docs/superpowers/tasks/2026-09-14-epmcdme-14855-graph-cancellation-core/spec.md`

**Commit convention:** Commit per task using the repository's existing convention (see
`.ai-run/guides/standards/git-workflow.md`). No separate commit-message steps are spelled out
below.

## Global Constraints

- Backend only (`src/codemie/`, `tests/`) — no `codemie-ui/` changes.
- In scope: A.1, A.2, A.3, A.4, B.7, B.8 only. Out of scope for every task: A.5/A.6
  (consecutive-tool-error cap, error consolidation under `agent_token_limit`), B.9/B.10
  (`hedged_handler.py`'s `queue.Empty` timeout, `assistant_handlers.py`'s unbounded
  `queue.get()`) — do not touch that adjacent logic even when editing a neighboring line.
- No new config keys, no new `ErrorCode`/exception types, no change to `handle_tool_error=True`
  retry-feedback behavior itself.
- A.3 relies solely on `stream.close()` for graph-task cancellation — no new LangGraph
  cancellation API.
- The OTEL demotion-before-close ordering documented at `langgraph_agent.py:909-915` must be
  preserved by any change to `_stream_graph`'s close behavior.
- `ThreadedGenerator.send()`'s post-close guard must be a silent no-op — never raise.

---

### Task 1: `ThreadedGenerator.send()` no-op after close (A.4)

**Test-first: yes — `send()` called on a closed generator must not call `queue.put` or
`_process_thought` (currently it calls `queue.put` unconditionally).**

**Files:**
- Modify: `src/codemie/core/thread.py:72-83`
- Test: `tests/codemie/core/test_thread.py`

**Interfaces:**
- Consumes: `ThreadedGenerator.is_closed()` (already exists, `thread.py:153-154`).
- Produces: `ThreadedGenerator.send(data)` now returns immediately (no `queue.put`, no
  `_process_thought`) whenever `self.is_closed()` is true — later tasks/callers rely on this
  silent-drop behavior, not a raise.

- [ ] **Step 1: Write the failing tests**

```python
def test_send_after_close_is_noop(generator):
    generator.close()
    with patch.object(generator, 'queue') as mock_queue:
        generator.send('{"thought": {"id": "t1", "message": "x"}}')
        mock_queue.put.assert_not_called()


def test_send_after_close_is_noop_for_non_json_payload(generator):
    generator.close()
    with patch.object(generator, 'queue') as mock_queue:
        generator.send('not-json')
        mock_queue.put.assert_not_called()


def test_send_after_close_does_not_process_thought(generator):
    generator.close()
    with patch.object(generator, '_process_thought') as mock_process:
        generator.send('{"thought": {"id": "t1", "message": "x"}}')
        mock_process.assert_not_called()
```

Add these to `tests/codemie/core/test_thread.py` (uses the existing `generator` fixture).

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/core/test_thread.py -v`
Expected: the three new tests FAIL (`mock_queue.put` is still called today).

- [ ] **Step 3: Implement the guard**

In `send()` (`thread.py:72-83`), return before either branch does anything once closed:

```python
def send(self, data):
    if self.is_closed():
        return
    try:
        parsed = json.loads(data)
    except Exception:
        self.queue.put(data)
        return

    thought = parsed.get('thought', {})
    self.queue.put(data)

    if thought:
        self._process_thought(thought)
```

`close()` itself is untouched — it still enqueues its own sentinel/error directly via
`self.queue.put(...)`, not through `send()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/core/test_thread.py -v`
Expected: all tests (new and existing `test_send`, `test_close`, etc.) PASS.

- [ ] **Step 5: Commit**

---

### Task 2: `SupervisorCoordinator` reset entry point (A.3, coordinator half)

**Test-first: yes — resetting the coordinator must clear a dangling `active` handoff so
`binding_for` no longer resolves it (no such method exists today).**

**Files:**
- Modify: `src/codemie/agents/supervisor/coordinator.py:67-166`
- Test: Create `tests/codemie/agents/supervisor/test_coordinator.py` (no prior test file exists
  for this module).

**Interfaces:**
- Produces: `_SupervisorHandoffTracker.reset() -> None` and `SupervisorCoordinator.reset() -> None`
  — Task 4 calls `self._supervisor_coordinator.reset()` on the fatal-error path.

- [ ] **Step 1: Write the failing tests**

```python
from uuid import uuid4

from codemie.agents.supervisor.coordinator import SupervisorCoordinator, _SupervisorHandoffTracker


def _make_coordinator() -> SupervisorCoordinator:
    return SupervisorCoordinator(
        handoff_tool_prefix="transfer_to_",
        extract_agent_name_from_tool=lambda name: name,
        tool_call_id_to_uuid=lambda call_id: uuid4(),
        resolve_display_name=lambda name, a, b: name,
        emit_handoff=lambda *a, **k: None,
        emit_subassistant_back=lambda *a, **k: None,
        set_thread_context=lambda *a, **k: None,
    )


def test_tracker_reset_clears_all_state():
    tracker = _SupervisorHandoffTracker()
    handoff = (uuid4(), "sub_agent", "do a thing", None)
    tracker.queue_pending("sub_agent", handoff)
    tracker.activate("sub_agent", "sub_agent:1", handoff)

    tracker.reset()

    assert tracker.active == {}
    assert tracker.pending == {}
    assert tracker.run_bindings == {}


def test_coordinator_reset_clears_dangling_active_handoff():
    coordinator = _make_coordinator()
    handoff = (uuid4(), "sub_agent", "do a thing", None)
    coordinator.tracker.activate("sub_agent", "sub_agent:1", handoff)
    assert coordinator.binding_for("sub_agent:1") is not None

    coordinator.reset()

    assert coordinator.binding_for("sub_agent:1") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/agents/supervisor/test_coordinator.py -v`
Expected: FAIL with `AttributeError: '_SupervisorHandoffTracker' object has no attribute 'reset'`
(and the coordinator equivalent).

- [ ] **Step 3: Implement `reset()` on both classes**

Add to `_SupervisorHandoffTracker` (alongside `clear_pending`, `coordinator.py:75-76`):

```python
def reset(self) -> None:
    """Clear all handoff state for the current agent run (fatal-error path)."""
    self.run_bindings.clear()
    self.pending.clear()
    self.active.clear()
```

Add to `SupervisorCoordinator` (alongside `binding_for`, `coordinator.py:168-169`):

```python
def reset(self) -> None:
    """Clear the handoff tracker wholesale — called once a fatal error ends the run."""
    self.tracker.reset()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/agents/supervisor/test_coordinator.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

---

### Task 3: Detect truncation inside `_stream_graph`'s own loop (A.1)

**Test-first: yes — a `finish_reason=length` AI message with no tool calls must raise
`TokenLimitExceededException` from inside the loop (today it is silently ignored, no tool calls
means neither existing event-adapter check site fires).**

**Files:**
- Modify: `src/codemie/agents/langgraph_agent.py:894-904` (add a helper + one call site)
- Test: `tests/codemie/agents/test_langgraph_truncation.py`

**Interfaces:**
- Consumes: `self._safe_check_for_truncation(message)` (existing, `langgraph_agent.py:1252-1272`,
  raises `TokenLimitExceededException` on a real truncation match, swallows-and-logs anything
  else), `self.is_valid_ai_message` is NOT reused here (it requires non-empty `content`, which a
  truncated message may lack) — extract the raw `AIMessage` directly instead.
- Produces: `LangGraphAgent._check_truncation_in_chunk(chunk) -> None` — called from
  `_stream_graph`'s loop; Task 4 wraps the loop (including this call) in the new `try/finally`.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/agents/test_langgraph_truncation.py`, reusing `create_mock_agent`:

```python
def test_check_truncation_in_chunk_raises_without_tool_calls():
    """A max_tokens cutoff with no tool call in play must still raise (A.1)."""
    agent = create_mock_agent(llm_model="gpt-4.1")
    agent.subagents = None
    message = AIMessage(content="partial answer, cut off", response_metadata={"finish_reason": "length"})
    chunk = ("updates", {"agent": {"messages": [message]}})

    with pytest.raises(TokenLimitExceededException):
        agent._check_truncation_in_chunk(chunk)


def test_check_truncation_in_chunk_noop_for_non_updates_chunk():
    agent = create_mock_agent(llm_model="gpt-4.1")
    agent.subagents = None
    agent._check_truncation_in_chunk(("messages", ("token", {})))  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_truncation.py -v`
Expected: FAIL with `AttributeError: 'LangGraphAgent' object has no attribute
'_check_truncation_in_chunk'`.

- [ ] **Step 3: Implement the helper and call it from the loop**

Add near `_get_last_ai_message_content` (`langgraph_agent.py:1094-1104`), mirroring its
`target_field`/`node_state` extraction but returning the raw message instead of its content:

```python
def _check_truncation_in_chunk(self, chunk) -> None:
    """Detect max-token truncation for any finished AI message, tool calls or not (A.1)."""
    chunk_type, value = chunk
    if chunk_type != "updates":
        return
    target_field = "agent" if not self.subagents else "supervisor"
    node_state = value.get(target_field)
    if not isinstance(node_state, dict):
        return
    messages = node_state.get("messages")
    if not messages or not isinstance(messages[-1], AIMessage):
        return
    self._safe_check_for_truncation(messages[-1])
```

In `_stream_graph`'s loop (`langgraph_agent.py:898`), call it right after `process_chunk` and
before `_prepare_chunk_for_processing` reshapes the chunk (the raw, un-prepared chunk is what
carries the `"agent"`/`"supervisor"` top-level key this helper expects):

```python
self.process_chunk(chunk, chunks_collector)
self._check_truncation_in_chunk(chunk)
chunk = self._prepare_chunk_for_processing(chunk)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_truncation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

---

### Task 4: Close the stream on every exit and clear handoffs on the fatal path (A.2 + A.3 wiring)

**Test-first: yes — (1) an exception raised inside `_stream_graph`'s loop body must still call
`stream.close()`, which does not happen today (only the hedging-cancel branch closes it); (2) a
fatal exception in `stream()` must call `self._supervisor_coordinator.reset()`, which has no call
site today.**

**Files:**
- Modify: `src/codemie/agents/langgraph_agent.py:788-792` (`stream()`'s except block) and
  `langgraph_agent.py:893-922` (`_stream_graph`'s loop)
- Test: `tests/codemie/agents/test_langgraph_truncation.py` (or a new
  `tests/codemie/agents/test_langgraph_agent_streaming.py` if the truncation file's
  `create_mock_agent` helper needs agent-level attributes the truncation tests don't set —
  reuse/extend `create_mock_agent` either way, do not duplicate it)

**Interfaces:**
- Consumes: Task 2's `SupervisorCoordinator.reset()`.
- Produces: `_stream_graph` now closes `stream` exactly once on every exit path (normal
  completion, hedging-cancel break, or any exception raised inside the loop body, including
  Task 3's `_check_truncation_in_chunk`); `stream()`'s `except Exception` block now also clears
  `self._supervisor_coordinator`'s handoff tracker before/alongside `_send_error_to_thread`.

- [ ] **Step 1: Write the failing tests**

```python
def test_stream_graph_closes_stream_on_process_chunk_exception():
    """A.2: an exception inside the loop body must still close the LangGraph stream."""
    agent = create_mock_agent(llm_model="gpt-4.1")
    agent.subagents = None
    mock_stream = Mock()
    mock_stream.__iter__ = Mock(return_value=iter([("updates", {"agent": {"messages": []}})]))
    agent.agent_executor = Mock(stream=Mock(return_value=mock_stream))
    agent.thread_generator = Mock(is_closed=Mock(return_value=False))
    agent.process_chunk = Mock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        agent._stream_graph({"messages": []})

    mock_stream.close.assert_called_once()


def test_stream_exception_path_resets_supervisor_coordinator():
    """A.3: a fatal exception clears the coordinator's dangling handoff state."""
    agent = create_mock_agent(llm_model="gpt-4.1")
    agent._supervisor_coordinator = Mock()
    agent._agent_streaming = Mock(side_effect=RuntimeError("boom"))
    agent._send_error_to_thread = Mock()
    agent.thread_generator = Mock(is_closed=Mock(return_value=False))
    agent.request_uuid = "test-uuid"
    agent._pending_tool_confirmation = False
    agent.require_tool_confirmation = False
    agent._otel_context = None
    agent.conversation_id = None

    agent.stream()

    agent._supervisor_coordinator.reset.assert_called_once()
```

The second test needs `agent.stream()`'s surrounding collaborators (`propagated_span`,
`set_logging_info`, `set_llm_context`) to run against a real/near-real agent; if `create_mock_agent`
doesn't already stub enough for `stream()` to execute, extend it with the missing attributes
(`llm_model`, `user`, `assistant`) rather than re-mocking `stream()`'s internals — those are
already set by the existing helper.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_truncation.py -v -k "stream_graph_closes or resets_supervisor_coordinator"`
Expected: FAIL — `mock_stream.close` is not called on the exception path today, and
`_supervisor_coordinator.reset` doesn't exist as a call site yet.

- [ ] **Step 3: Wrap `_stream_graph`'s loop in try/finally; call `reset()` on the fatal path**

In `_stream_graph` (`langgraph_agent.py:893-922`), wrap the `for chunk in stream:` loop in
`try/finally`, and drop the now-redundant inline `stream.close()` at line 921 since `finally`
covers that exit too (the demotion call at lines 916-920 still runs, inside the loop body,
strictly before the loop exits and `finally` fires — ordering preserved):

```python
with suppress_stdout():
    try:
        for chunk in stream:
            ...  # unchanged body, only line 921's `stream.close()` is removed
            if self._should_stop_streaming():
                ...  # demotion call unchanged
                break
    finally:
        stream.close()
```

In `stream()`'s `except Exception as e:` block (`langgraph_agent.py:788-789`), reset the
coordinator before formatting the error:

```python
except Exception as e:
    self._supervisor_coordinator.reset()
    self._send_error_to_thread(e, execution_start, chunks_collector)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_truncation.py -v`
Expected: all pass, including the pre-existing truncation tests (unaffected by this change).

- [ ] **Step 5: Commit**

---

### Task 5: Populate `success`/`agent_error`/`tool_errors`/`error_details` in `_send_error_to_thread` (B.7)

**Test-first: yes — the `StreamedGenerationResult` sent on the error path must carry
`success=False` and a populated `agent_error`/`error_details`; today those fields are omitted and
default to `None`.**

**Files:**
- Modify: `src/codemie/agents/langgraph_agent.py:816-838`
- Test: `tests/codemie/agents/test_langgraph_truncation.py` (or the streaming test file added in
  Task 4 — same file either way, do not create a third)

**Interfaces:**
- Consumes: `error_response = handle_agent_exception(e)` (already computed at
  `langgraph_agent.py:820`), `ErrorCategory` (import from `codemie.core.errors`), `AgentErrorDetails.to_full()`/
  `ToolErrorDetails.to_full()` (existing, `core/errors.py:119-121`).
- Produces: no new symbols — `StreamedGenerationResult`'s existing `success`/`agent_error`/
  `tool_errors`/`error_details` fields are now populated on the error path; nothing downstream of
  this task depends on new names.

- [ ] **Step 1: Write the failing test**

```python
import json


def test_send_error_to_thread_populates_error_fields():
    agent = create_mock_agent(llm_model="gpt-4.1")
    agent.thread_generator = Mock()
    agent.thread_context = None
    agent.extended_error = Mock(return_value="sanitized message")

    error = TokenLimitExceededException(
        message="Token limit exceeded", model="gpt-4.1", truncation_reason="finish_reason=length"
    )
    agent._send_error_to_thread(error, execution_start=0.0, chunks_collector=[])

    sent = json.loads(agent.thread_generator.send.call_args[0][0])
    assert sent["success"] is False
    assert sent["agent_error"]["error_code"] == "agent_token_limit"
    assert sent["error_details"] is not None
```

`config.HIDE_AGENT_STREAMING_EXCEPTIONS` must be `False` (its default) for this test so
`extended_error` is exercised as stubbed above.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_truncation.py -v -k send_error_to_thread`
Expected: FAIL — `sent["success"]` is `None` today (field omitted).

- [ ] **Step 3: Wire the fields**

In `_send_error_to_thread` (`langgraph_agent.py:816-838`), after `error_response =
handle_agent_exception(e)` (line 820), compute and pass through the four fields:

```python
from codemie.core.errors import ErrorCategory  # add to the existing errors import at line 51

...

agent_error = error_response.agent_error if error_response.category == ErrorCategory.AGENT else None
tool_errors = [err.to_full() for err in error_response.tool_errors] if error_response.tool_errors else None
error_payload = error_response.get_error()
error_details = error_payload.to_full() if error_payload else None
...
self.thread_generator.send(
    StreamedGenerationResult(
        generated=generated,
        generated_chunk="",
        last=True,
        time_elapsed=time_elapsed,
        context=self.thread_context,
        execution_error=execution_error,
        success=False,
        agent_error=agent_error,
        tool_errors=tool_errors,
        error_details=error_details,
    ).model_dump_json()
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_truncation.py -v`
Expected: PASS, including all pre-existing tests in the file.

- [ ] **Step 5: Commit**

---

### Task 6: Remove the dead `_build_final_chunk`/`last_generation_result` path (B.8)

**Test-first: yes — the existing handler completion-branch tests must keep passing once
`_build_final_chunk` and its call sites are deleted (they exercise `save_chat_history`/`break`,
not the removed dead branch); no new behavior is being added, so the failing step is the
pre-removal grep/assertion cleanup described in Step 1.**

**Files:**
- Modify: `src/codemie/rest_api/handlers/assistant_handlers.py:698-717` (delete the method),
  `assistant_handlers.py:790-792` (remove the call site)
- Modify: `src/codemie/rest_api/handlers/hedged_handler.py:411-424` (remove the call site only —
  leave the surrounding `queue.Empty`/normal-completion control flow, including `save_chat_history`
  and the `break`, untouched)
- Modify: `tests/codemie/rest_api/handlers/test_hedged_handler.py` (remove all 11
  `mock_agent.last_generation_result = None` stub lines: 188, 251, 315, 417, 952, 1016, 1173, 1216,
  1366, 1432, 1461 — they stub a path that no longer exists)
- Test: same file, plus `assistant_handlers.py`'s existing completion-branch tests (wherever they
  live in the handler test suite)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing — this task only removes symbols. Confirm nothing else in `src/` or `tests/`
  references `_build_final_chunk` or `last_generation_result` after this task (grep both terms
  repo-wide; the only expected hits before this task are the ones listed above).

- [ ] **Step 1: Write/update the failing tests**

This is a removal, so "failing first" means: run the existing test suite before touching
production code and confirm the tests that assert on `_build_final_chunk`'s dead-code behavior
still pass today (a green baseline), then delete the assertions that named the now-removed method
so removing it doesn't orphan a broken test:

```python
# In each of the 11 sites listed above, delete the line:
#     mock_agent.last_generation_result = None
# These assert nothing on their own; they only stub an attribute the removed code read.
```

Confirm no test asserts `final_chunk is not None`/`final_chunk.success == ...` anywhere in
`test_hedged_handler.py` — grep for `final_chunk` in that file. If any such assertion exists,
delete it: `_build_final_chunk` always returned `None` in production, so no such assertion can be
legitimately testing observed behavior; it would be dead-code-following, not a real regression
guard.

- [ ] **Step 2: Run the handler test suites to confirm the current (pre-removal) baseline is green**

Run: `poetry run pytest tests/codemie/rest_api/handlers/test_hedged_handler.py tests/codemie/rest_api/handlers/test_assistant_handlers.py -v`
Expected: PASS (baseline, before any production-code deletion in this task).

- [ ] **Step 3: Delete `_build_final_chunk` and both call sites**

In `assistant_handlers.py`, delete the `_build_final_chunk` method (lines 698-717) and its call
site in `_serve_data` (lines 790-792): remove the `final_chunk = self._build_final_chunk(...)`
assignment and the `if final_chunk: yield final_chunk.model_dump_json() + "\n"` block, leaving the
`break` that follows.

In `hedged_handler.py`'s `_stream_agent_path` (lines 411-424), remove the
`final_chunk = self._build_final_chunk(...)` assignment and the `if final_chunk: yield
final_chunk.model_dump_json() + "\n"` block. Leave `save_chat_history(...)` and the trailing
`break` exactly as they are — that logic belongs to the out-of-scope B.9/B.10 slice.

In `test_hedged_handler.py`, delete the 11 `mock_agent.last_generation_result = None` lines.

- [ ] **Step 4: Run tests to verify they still pass**

Run: `poetry run pytest tests/codemie/rest_api/handlers/test_hedged_handler.py tests/codemie/rest_api/handlers/test_assistant_handlers.py -v`
Expected: PASS — the completion-branch tests exercise the same `save_chat_history`/`break` control
flow, now without the removed dead final-chunk step.

Then confirm no references remain: `grep -rn "_build_final_chunk\|last_generation_result" src/ tests/`
Expected: no output.

- [ ] **Step 5: Commit**

---

## Negative-Constraints Pass

- "No new LangGraph-specific cancellation API" (A.3 design note, spec Non-goals) — Task 4 only
  calls the pre-existing `stream.close()`; no checkpointer/interrupt API is introduced. Honored.
- "Do not disturb the existing OTEL demotion-before-close ordering" (A.2, spec + `langgraph_agent.py:909-915`)
  — Task 4 keeps the demotion call inside the loop body, strictly before the loop can exit, and
  moves `stream.close()` to a `finally` that only runs after the loop has already exited; ordering
  is preserved on both the hedging-cancel and exception paths. Honored.
- "`ThreadedGenerator.send()` must not raise" (A.4, spec) — Task 1's guard is an early `return`,
  never an exception. Honored.
- "No new configuration surface, no new `ErrorCode`/exception types" (spec Non-goals) — none of
  Tasks 1-6 add a config key or `ErrorCode`; Task 5 reuses `ErrorCategory`/`AgentErrorDetails`
  that already exist. Honored.
- "No change to `handle_tool_error=True`'s retry-feedback behavior" (spec Non-goals) — no task
  touches `_configure_tools` or `self.handle_tool_error`. Honored.
- "A.5/A.6 and B.9/B.10 are out of scope" (spec Scope + Non-goals) — no task adds a
  consecutive-tool-error counter, touches `on_tool_error`'s per-call sends, or edits the
  `queue.Empty`/unbounded-`get()` logic in `hedged_handler.py`/`assistant_handlers.py`; Task 6
  explicitly calls out leaving that adjacent code untouched. Honored.
- "No frontend (`codemie-ui/`) changes" (spec Non-goals) — no task touches that directory.
  Honored.
- "Do not raise" in A.4 is the only "must not X" tied to a specific mechanism (silent no-op, not
  an exception, not a log-and-drop that still enqueues) — Task 1's implementation matches exactly:
  no `queue.put`, no `_process_thought`, no raise. Honored.
