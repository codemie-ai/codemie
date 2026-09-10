# Fix Workflow Thought-Persistence Regression (EPMCDME-14850) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop workflow LLM turns from losing persisted thoughts when `on_llm_end` is invoked twice for the same `run_id` with no resolved Switchyard model — the default/common path.

**Architecture:** Three independent, additive fixes: (1) restore per-callback idempotency in `AgentStreamingCallback.on_llm_end` so a redundant finalize doesn't re-send and always clears storage; (2) add a narrow duplicate-id guard in `ThoughtConsumer.consume()` as a defensive backstop; (3) attach a `Future` done-callback in `run_consumer_in_thread_pool` so any consumer-thread exception is logged instead of vanishing. `LangGraphEventAdapter`'s dual stop-branch dispatch is not touched — it is intentional (Non-Goals).

**Tech Stack:** Python, pytest, unittest.mock.

**Commit convention:** Commit per task using the repository's existing convention (see `.ai-run/guides/standards/git-workflow.md`).

---

### Task 1: Restore `on_llm_end` idempotency for redundant finalize calls

**Files:**
- Modify: `src/codemie/agents/callbacks/agent_streaming_callback.py:199-248` (`on_llm_end`)
- Test: `tests/codemie/agents/callbacks/test_agent_streaming_callback.py`

- [ ] **Step 1: Write the failing test**

Add near the existing `test_on_llm_end_stamps_routed_model_and_cost_on_thought_and_metadata` (line 416):

```python
def test_on_llm_end_redundant_finalize_does_not_resend_or_leak_thought() -> None:
    """A second on_llm_end call for an already-finalized run_id, with no new routing
    metadata, is a no-op send and clears the thought from storage instead of
    duplicating it (regression for EPMCDME-14850)."""
    generator = ThreadedGenerator()
    callback = AgentStreamingCallback(gen=generator)
    run_id = uuid.uuid4()

    callback.on_llm_start(None, [], run_id=run_id)
    callback.on_llm_new_token("Hi ", run_id=run_id)
    callback.on_llm_end(AIMessage(content="Hi there"), run_id=run_id)
    callback.on_llm_end(AIMessage(content="Hi there"), run_id=run_id)

    assert len(generator.thoughts) == 1
    assert callback._get_storage(None).get(str(run_id)) is None
```

Needs `from langchain_core.messages import AIMessage` and `from codemie.core.thread import ThreadedGenerator` — both already imported in the file (top-level `AIMessage` import may need adding if only imported inside the routed test; check top of file and hoist if so).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/agents/callbacks/test_agent_streaming_callback.py::test_on_llm_end_redundant_finalize_does_not_resend_or_leak_thought -v`
Expected: FAIL — `len(generator.thoughts) == 2` (both calls send; second call's stored thought is never deleted).

- [ ] **Step 3: Implement the idempotency fix**

`on_llm_end` currently deletes the thought only when `model_resolved or isinstance(response, str)` (line 247), which leaves an already-sent thought in storage whenever the model never resolves — so a second call for the same `run_id` re-sends it. Before building `update_kwargs`, capture `already_finalized = existing_thought is not None and existing_thought.in_progress is False` (true exactly when this is a redundant call). Compute `has_new_routing = bool(routing_fields or metadata)` right after `_build_routing_update_fields` is called. If `already_finalized and not has_new_routing`: call `storage.delete_thought(run_id)` and `return` immediately — no re-send. Otherwise proceed as today, except: when `already_finalized` is true, force `update_kwargs["message"] = ""` (skip the existing-message/AIMessage-content branch — nothing new to add) so a resend carrying only new routing metadata never repeats text; and change the final deletion condition to `if model_resolved or isinstance(response, str) or already_finalized:` so a redundant call always leaves the thought removed afterward, regardless of whether this particular call resolved the model. First-time calls (`already_finalized` false) are unaffected — same behavior as today, preserving the existing routed-model test.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/agents/callbacks/test_agent_streaming_callback.py -v`
Expected: PASS, including `test_on_llm_end_stamps_routed_model_and_cost_on_thought_and_metadata` (no regression).

- [ ] **Step 5: Commit**

---

### Task 2: Guard `ThoughtConsumer.consume()` against a duplicate already-persisted id

**Files:**
- Modify: `src/codemie/service/workflow_execution/thought_consumer.py:34-79`
- Test: `tests/codemie/service/workflow_execution/test_thought_consumer.py`

- [ ] **Step 1: Write the failing test**

Add a new test near `test_error_handling_during_save`:

```python
@patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
def test_consume_skips_duplicate_finalize_for_already_persisted_id(
    self, mock_thought_model, mock_message_queue, sample_thought_context
):
    """A second in_progress=False item for an id already persisted is skipped, logged,
    and does not stop the consumer loop; later items still process."""
    thought = Thought(
        id="thought-dup", message="hello", in_progress=False,
        author_type=ThoughtAuthorType.Agent, author_name="Agent",
    )
    later_thought = Thought(
        id="thought-later", message="world", in_progress=False,
        author_type=ThoughtAuthorType.Agent, author_name="Agent",
    )
    for item_data in [thought, thought, later_thought]:
        mock_message_queue.queue.put(ThoughtQueueItem(data=item_data, context=sample_thought_context))
    mock_message_queue.queue.put(StopIteration)

    mock_thought_instance = MagicMock()
    mock_thought_model.return_value = mock_thought_instance
    consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

    consumer.consume()

    assert mock_thought_instance.save.call_count == 2  # thought-dup once, thought-later once
```

Also update the `test_error_handling_during_save` docstring/comment (lines 526-540) from "should raise the exception since there's no try/except in the code" to state it covers a genuinely unexpected `save()` failure for a non-duplicate id — the new duplicate-id guard is narrower than a blanket try/except and does not change this path.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/workflow_execution/test_thought_consumer.py::TestThoughtConsumer::test_consume_skips_duplicate_finalize_for_already_persisted_id -v`
Expected: FAIL — `save.call_count == 3` (no dedupe today; a second insert with the real model would raise `IntegrityError`, but the mock lets both duplicate calls through).

- [ ] **Step 3: Implement the duplicate-id guard**

In `ThoughtConsumer.__init__` (line 34), add `self._persisted_ids: set[str] = set()`. In `consume()`, right after the `if thought_data.in_progress: continue` check (line 67) and before constructing `WorkflowExecutionStateThought` (line 69), add:

```python
if thought_data.id in self._persisted_ids:
    logger.info(
        "ThoughtConsumer: skipping duplicate finalize for already-persisted thought "
        "id=%s execution_state_id=%s",
        thought_data.id,
        context.execution_state_id,
    )
    self.cache.pop(thought_data.id, None)
    continue
```

After `thought.save(refresh=True)` (line 78), add `self._persisted_ids.add(thought_data.id)` before `self.cache.pop(thought_data.id)`. This is a narrow guard on the known-duplicate-id case only — any other exception from `save()` still propagates uncaught, unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/workflow_execution/test_thought_consumer.py -v`
Expected: PASS, including unmodified `test_error_handling_during_save`.

- [ ] **Step 5: Commit**

---

### Task 3: Log unhandled consumer-thread exceptions via the thread-pool `Future`

**Files:**
- Modify: `src/codemie/rest_api/routers/utils.py:15-18,106-108`
- Test: `tests/codemie/rest_api/routers/test_routers_utils.py`

- [ ] **Step 1: Write the failing test**

Add to `test_routers_utils.py`:

```python
def test_run_consumer_in_thread_pool_logs_unhandled_exception():
    """An exception raised inside the submitted consumer function is logged via the
    Future's done-callback instead of vanishing silently (EPMCDME-14850)."""
    from codemie.rest_api.routers.utils import run_consumer_in_thread_pool

    def _boom():
        raise ValueError("consumer failed")

    with patch("codemie.rest_api.routers.utils.logger") as mock_logger:
        future = run_consumer_in_thread_pool(_boom)
        future.exception(timeout=5)  # block until the thread finishes

    mock_logger.error.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_routers_utils.py::test_run_consumer_in_thread_pool_logs_unhandled_exception -v`
Expected: FAIL — `mock_logger.error` not called (no done-callback exists today).

- [ ] **Step 3: Implement the done-callback**

Add `Future` to the existing `from concurrent.futures import ThreadPoolExecutor` import (line 18) → `from concurrent.futures import Future, ThreadPoolExecutor`. Before `run_consumer_in_thread_pool` (line 106), add:

```python
def _log_consumer_exception(future: Future) -> None:
    exc = future.exception()
    if exc is not None:
        logger.error("ThoughtConsumer: consumer thread failed with an unhandled exception", exc_info=exc)
```

Change `run_consumer_in_thread_pool` (lines 106-108) to attach it: `future = consumer_executor.submit(func, *args); future.add_done_callback(_log_consumer_exception); return future`. This only observes/logs the existing `Future` — no retry, no behavior change to `consume()` itself.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_routers_utils.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

---

### Task 4: Add an integration-style regression test for the duplicate-finalize scenario

**Files:**
- Test: `tests/codemie/service/workflow_execution/test_thought_consumer.py`

**Test-first: no — this task adds only test coverage; Tasks 1-3 already implement and unit-test the fix, so there is no new production behavior to drive out. It proves the fix holds when a real `ThoughtQueue` (not hand-built `ThoughtQueueItem`s) feeds a real `ThoughtConsumer`, per the spec's acceptance criteria.**

- [ ] **Step 1: Write the integration-style test**

Add to `test_thought_consumer.py`, reusing the `real_thought_queue`-style pattern from `test_agent_streaming_callback.py` (a real `ThoughtQueue`, not a mock). `ThoughtQueue.send` reads `execution_state_id` from the parsed JSON's `context` dict (`codemie/core/thought_queue.py:70-73`), not from `set_context`, so pass it explicitly on each `StreamedGenerationResult`:

```python
@patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
def test_real_thought_queue_duplicate_finalize_persists_once_and_loop_survives(self, mock_thought_model):
    """A real ThoughtQueue delivering the same finalized thought id twice (simulating
    the double on_llm_end scenario) results in exactly one persisted row, and the
    consumer loop keeps processing subsequent items."""
    from codemie.core.thought_queue import ThoughtQueue

    real_queue = ThoughtQueue()
    consumer = ThoughtConsumer(workflow_execution_id="exec-dup", message_queue=real_queue)

    def _send(thought_id: str, message: str, in_progress: bool) -> None:
        thought = Thought(
            id=thought_id, message=message, in_progress=in_progress,
            author_type=ThoughtAuthorType.Agent, author_name="Agent",
        )
        result = StreamedGenerationResult(thought=thought, context={"execution_state_id": "exec-state-dup"})
        real_queue.send(result.model_dump_json())

    _send("dup-1", "hello", True)
    _send("dup-1", "hello", False)  # first finalize
    _send("dup-1", "hello", False)  # redundant finalize — must not raise
    _send("dup-2", "later", False)  # loop must still process this
    real_queue.close()

    mock_thought_instance = MagicMock()
    mock_thought_model.return_value = mock_thought_instance

    consumer.consume()  # must return normally, not raise

    assert mock_thought_instance.save.call_count == 2
```

Add `StreamedGenerationResult` to the file's existing `from codemie.chains.base import Thought, ThoughtAuthorType` import (line 23).

- [ ] **Step 2: Run the test**

Run: `poetry run pytest tests/codemie/service/workflow_execution/test_thought_consumer.py::TestThoughtConsumer::test_real_thought_queue_duplicate_finalize_persists_once_and_loop_survives -v`
Expected: PASS (Tasks 1-3 already make this true; if it fails, the guard in Task 2 or its `context.execution_state_id` gate needs revisiting before proceeding).

- [ ] **Step 3: Commit**
