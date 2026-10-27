# Stop the supervisor/sub-agent loop after truncation or disconnect — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the truncation/disconnect stop from the stream consumer into the graph itself, via a `pre_model_hook` on the supervisor node and every sub-agent node, so a truncated tool-call response or an aborted stream fails that node immediately instead of looping to the recursion limit.

**Architecture:** One new factory function `_build_truncation_abort_pre_model_hook(agent)` in `src/codemie/agents/supervisor/pre_model_hooks.py`, called once per `LangGraphAgent` instance and returning a closure over that instance (reusing `agent._safe_check_for_truncation`/`agent.thread_generator`), added to the existing `_compose_pre_model_hooks(...)` tuples on both node builders. One new exception `StreamAbortedException`. One new catch branch where the stream consumer already catches agent exceptions, to swallow it silently.

**Tech Stack:** Python, LangGraph / `langgraph_supervisor==0.0.31`, pytest.

**Spec:** `docs/superpowers/tasks/2026-10-22-supervisor-token-limit-loop/spec.md`

## Global Constraints

- Reuse `LangGraphAgent._safe_check_for_truncation`/`_check_for_truncated_response` for detection; do not add a third truncation detector (spec Non-goals).
- Do not touch `langgraph_event_adapter.py`'s existing detection path or `proxy_router.handle_agent_exception`'s `agent_token_limit` formatting (spec Non-goals).
- `StreamAbortedException` is distinct from `TokenLimitExceededException`: never reported as `agent_token_limit`, never surfaced to a user.
- The new hook returns `None` (no-op) on a non-truncated last message and an open/non-cancelled stream — today's tool-calling loop is otherwise unaffected (spec Goal 4).

## Review Focus

- A truncated **sub-agent** response must fail only that sub-agent's node, not silently continue the supervisor's own loop (spec Goal 2) — covered by Task 3's sub-agent-level assertion.
- A disconnect/hedging-cancel mid-turn must stop the *next* model call from any node (supervisor or sub-agent), not just be observed after the fact (spec Goal 3) — covered by Task 2's hook logic and Task 3's test.
- The composed-hook exception must propagate out of a **real compiled graph**, not just out of the free function in isolation — existing fixtures mock `create_supervisor` away, so this is the actual risk the spec flags as unverified (spec Open risks) — covered by Task 3.
- A `StreamAbortedException` raised inside a node must not reach `_send_error_to_thread`/`handle_agent_exception` (would mislabel a disconnect as `agent_token_limit` or another error code) — covered by Task 4's test.
- An ordinary multi-step tool-calling run (no truncation, no abort) must complete unchanged end to end on the real compiled graph, not just return `None` from the hook in isolation — covered by Task 3's negative test.

---

### Task 1: `StreamAbortedException`

**Files:**
- Modify: `src/codemie/core/exceptions.py:304-333` (insert after `TokenLimitExceededException`)
- Test: `tests/codemie/core/test_exceptions.py` (create if absent)

**Interfaces:**
- Produces: `StreamAbortedException(Exception)`, no custom `__init__` (plain message), importable as `from codemie.core.exceptions import StreamAbortedException`.

- [ ] **Step 1: Write the failing test** — `test_stream_aborted_exception_is_plain_exception_not_token_limit`: asserts `StreamAbortedException("x")` is an `Exception`, is **not** a `TokenLimitExceededException`/`ValueError`, and `str(exc) == "x"`.
- [ ] **Step 2: Run it** — `pytest tests/codemie/core/test_exceptions.py -v`, expect FAIL (`ImportError`/`AttributeError`: no `StreamAbortedException`).
- [ ] **Step 3: Implement** — add immediately after `TokenLimitExceededException` (line 333):

```python
class StreamAbortedException(Exception):
    """Raised from inside a graph node when the owning stream was closed or
    hedging-cancelled; callers should stop without surfacing a user-facing error."""
```

- [ ] **Step 4: Run it** — same command, expect PASS.
- [ ] **Step 5: Commit** per the repository's existing convention.

Test-first: yes — the test above, written before `StreamAbortedException` exists.

---

### Task 2: Truncation/abort `pre_model_hook` factory, wired into both builders

**Files:**
- Modify: `src/codemie/agents/supervisor/pre_model_hooks.py` (add new factory function near `_image_artifact_pre_model_hook`)
- Modify: `src/codemie/agents/langgraph_agent.py:297-325` (`_build_supervisor_agent`), `:345-362` (`_build_single_agent`) — add the new hook to each `_compose_pre_model_hooks(...)` call
- Test: `tests/codemie/agents/test_langgraph_truncation.py` (extend)

**Interfaces:**
- Consumes: `agent._safe_check_for_truncation(message: AIMessage) -> None` (raises `TokenLimitExceededException`, `langgraph_agent.py:1304-1324`), `agent.thread_generator.is_closed() -> bool`, `agent.thread_generator.cancellation_reason` (existing, same shape as `_should_stop_streaming`, `langgraph_agent.py:978-986`); `StreamAbortedException` (Task 1).
- Produces: `_build_truncation_abort_pre_model_hook(agent: "LangGraphAgent") -> Callable[[dict[str, Any]], None]` in `pre_model_hooks.py` — a factory, called once as `_build_truncation_abort_pre_model_hook(self)` inside each builder; the returned closure is the actual `pre_model_hook` entry (shape: takes `state: dict[str, Any]`, returns `None`), matching the free-function-over-state shape of `_image_artifact_pre_model_hook` and the other composed hooks. Later tasks (3, 4) use `_build_truncation_abort_pre_model_hook` and `StreamAbortedException` only — no other new names.

- [ ] **Step 1: Write the failing tests** in `test_langgraph_truncation.py`, using `create_mock_agent` (existing helper, extended with a `thread_generator` attribute set to `Mock(is_closed=Mock(return_value=False), cancellation_reason=None)` as the open-stream default):
  - `test_truncation_abort_hook_raises_on_truncated_last_message`: `state["messages"]` ends with an `AIMessage` carrying `response_metadata={"finish_reason": "length"}` and `tool_calls` set → `_build_truncation_abort_pre_model_hook(agent)(state)` raises `TokenLimitExceededException`.
  - `test_truncation_abort_hook_raises_on_closed_stream`: `agent.thread_generator.is_closed()` returns `True`, last message is a normal `AIMessage` with no truncation metadata → the hook raises `StreamAbortedException`.
  - `test_truncation_abort_hook_noop_on_normal_open_stream`: normal last message, `is_closed()` returns `False` → the hook returns `None`.
  - `test_truncation_abort_hook_noop_on_empty_messages`: `state["messages"] = []` → the hook returns `None` (no last message to inspect).
- [ ] **Step 2: Run them** — expect FAIL (`AttributeError`/`ImportError`: no `_build_truncation_abort_pre_model_hook`).
- [ ] **Step 3: Implement `_build_truncation_abort_pre_model_hook(agent)`** in `pre_model_hooks.py`. The returned closure: (a) if `agent.thread_generator is not None and agent.thread_generator.is_closed()`, raise `StreamAbortedException(f"stream for agent {agent.agent_name} was closed or cancelled")`; (b) else, take `state.get("messages") or []`, and if non-empty, call `agent._safe_check_for_truncation(messages[-1])`; (c) otherwise return `None`. Abort check first — cheap, and independent of message content, matching spec's two separate stop conditions.
- [ ] **Step 4: Wire it in** — at `_build_supervisor_agent` (`:316-319`) add `_build_truncation_abort_pre_model_hook(self)` to the `_compose_pre_model_hooks(...)` args; same at `_build_single_agent` (`:355-361`). Import `_build_truncation_abort_pre_model_hook` alongside the other `supervisor.pre_model_hooks`/`supervisor.history` imports at the top of `langgraph_agent.py`.
- [ ] **Step 5: Run the tests** — expect PASS.
- [ ] **Step 6: Commit.**

Test-first: yes — the four hook-behavior tests above, written before `_build_truncation_abort_pre_model_hook` exists.

---

### Task 3: Real compiled-graph verification (supervisor + sub-agent + abort + negative case)

**Files:**
- Create: `tests/codemie/agents/test_langgraph_supervisor_truncation_stops_loop.py`

**Interfaces:**
- Consumes: `langgraph_supervisor.create_supervisor`, `langgraph.prebuilt.create_react_agent` (unmocked); `_build_truncation_abort_pre_model_hook` (Task 2); a stub chat-model (`FakeMessagesListChatModel` from `langchain_core.language_models.fake_chat_models`, or hand-rolled `Runnable`) returning pre-scripted `AIMessage`s, following the `response_metadata` shapes already used in `test_langgraph_truncation.py`.

- [ ] **Step 1: Write the failing tests**:
  - `test_supervisor_node_truncation_stops_further_llm_calls`: build a real `create_supervisor(model=<stub whose supervisor-facing call returns a truncated tool-call AIMessage>, agents=[...], pre_model_hook=_build_truncation_abort_pre_model_hook(fake_agent)).compile()`, invoke it; assert it raises `TokenLimitExceededException` and the supervisor's stub model was called exactly once (no retry toward the recursion limit) — spec Goal 1 / Acceptance criterion 5.
  - `test_subagent_node_truncation_stops_further_llm_calls`: same shape, but the *sub-agent's* stub model (not the supervisor's) returns the truncated response; assert the raise happens and the sub-agent's model was called exactly once — spec Goal 2.
  - `test_aborted_stream_stops_next_model_call`: `fake_agent.thread_generator.is_closed()` returns `True`; assert `StreamAbortedException` is raised before any model call for that turn — spec Goal 3.
  - `test_normal_tool_calling_run_completes_unaffected`: stub model returns a non-truncated tool call, then a final answer, open/non-cancelled `thread_generator`; assert the graph completes normally with the expected number of model calls — negative case, spec Goal 4.
- [ ] **Step 2: Run them** — expect FAIL (fixture/wiring not yet exercised through a real compiled graph — confirms these are not accidentally-passing no-ops).
- [ ] **Step 3: No new production code expected** — this task exercises Task 2's hook through a real graph. If any test fails for a reason other than "fixture still being built" (e.g. `_compose_pre_model_hooks` swallowing the raise, per spec's Open risks), fix the composition call site added in Task 2, not this test file.
- [ ] **Step 4: Run the tests** — expect PASS.
- [ ] **Step 5: Commit.**

Test-first: yes — all four tests above are this task's entire deliverable; each must fail before Task 2's hook is confirmed wired into a real compiled graph, and pass after.

---

### Task 4: Swallow `StreamAbortedException` at the stream-consumer catch site

**Files:**
- Modify: `src/codemie/agents/langgraph_agent.py:789-825` (the `try` wrapping `self._agent_streaming(chunks_collector)`, with its existing `except MCPAuthenticationRequiredException` / `except Exception` / `finally: self.thread_generator.close()`)
- Test: extend `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py`

**Interfaces:**
- Consumes: `StreamAbortedException` (Task 1).

- [ ] **Step 1: Write the failing test** — `test_stream_aborted_exception_sends_no_error_to_thread`: patch the agent's `_agent_streaming` to raise `StreamAbortedException`, call the wrapping streaming method, assert `_send_error_to_thread` is **not** called (mock/spy) and no exception propagates out of the wrapping method.
- [ ] **Step 2: Run it** — expect FAIL (today's generic `except Exception` branch at `:821` calls `_send_error_to_thread`).
- [ ] **Step 3: Implement** — add `except StreamAbortedException: pass` immediately before the existing `except Exception as e:` at `:821`, so it's caught ahead of the generic branch. Import `StreamAbortedException` alongside the existing `TokenLimitExceededException`/`MCPAuthenticationRequiredException` import at `:116`. The existing `finally: self.thread_generator.close()` (idempotent per `core/thread.py:172-175`) still runs unchanged.
- [ ] **Step 4: Run it** — expect PASS.
- [ ] **Step 5: Commit.**

Test-first: yes — the test above, written before the new `except` branch exists.
