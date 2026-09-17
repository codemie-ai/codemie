# Finalize `in_progress` on Persisted Thoughts Implementation Plan (EPMCDME-14624)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Force `in_progress=False` on every thought (and nested `children`) persisted from a chat turn that completed successfully, at all five verified call sites, without touching callback lifecycle code, `_filter_thoughts`, the disconnect/interrupt path, or history-projection code.

**Architecture:** Add one pure helper, `finalize_thoughts`, colocated with `ThreadedGenerator`/`_process_thought` in `core/thread.py` (the module that already owns this dict shape). Call it immediately before each of the five raw thought lists is wrapped into `ChatHistoryData`, on the success branch only.

**Tech Stack:** Python, pytest, `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-09-15-epmcdme-14624/spec.md`

## Global Constraints

- Do not modify `agent_streaming_callback.py`, `agent_invoke_callback.py`, `_filter_thoughts` (`assistant_handlers.py:468-515`), `_save_history_for_disconnect` (`assistant_handlers.py:664-696`), `ThreadedGenerator._process_thought`'s merge logic, `history_projection_service._normalize_status`, the `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED` flag, the tool-confirmation flow, or any frontend code.
- Only `in_progress` is forced; `error`/`interrupted`/`aborted` are left exactly as accumulated.
- Commit per task using the repository's existing convention.

---

### Task 1: Add `finalize_thoughts` helper + unit tests + organic-leak confirmation for both accumulator mechanisms

**Files:**
- Modify: `src/codemie/core/thread.py` (add function near `_process_thought`, line ~85)
- Test: `tests/codemie/core/test_thread.py`
- Test: `tests/codemie/agents/callbacks/test_agent_invoke_callback.py` (no production code change — this file only gains a confirmation test for the second accumulator mechanism)

**Interfaces:**
- Produces: `finalize_thoughts(thoughts: list[dict]) -> list[dict]` — mutates each dict's `in_progress` to `False` in place, recursing into `children` when present; returns the same list. Later tasks import this from `codemie.core.thread`.

**Confirmed by reading `agent_invoke_callback.py` for this revision:** `on_tool_start` (line ~280-294) unconditionally calls `self.thought_processing(current_thought)` right after creating the thought with `in_progress=True` — it does not wait for a paired closing event. Nothing else revisits that list entry unless `on_tool_end`/`on_tool_error` later fires for the same `run_id` and calls `thought_processing` again with `in_progress=False`. There is no safeguard (timeout, sweep, or finalize-on-return in `get_thoughts_from_callback()`, which just returns `callback.thoughts` as-is) that would clear a stale `True` if the closing event never arrives. The leak is real, not merely structural-by-analogy, so the test below is planned as the reviewer specified rather than flagged for revisit.

Test-first: yes for the helper — `test_finalize_thoughts_forces_top_level_and_nested_in_progress_false` fails because `finalize_thoughts` does not exist yet. The two organic-leak confirmation tests below are **not** test-first against `finalize_thoughts` (they exercise pre-existing accumulator code and are expected to pass immediately, before `finalize_thoughts` exists) — they exist to empirically prove, from a realistic event sequence and a read of the accumulator's own state, that the bug `finalize_thoughts` fixes is real in both mechanisms, per the spec's "Known test gaps this closes" commitment.

- [ ] **Step 1: Write failing tests** in `tests/codemie/core/test_thread.py`:

```python
from codemie.core.thread import finalize_thoughts

def test_finalize_thoughts_forces_top_level_in_progress_false():
    thoughts = [{'id': 't1', 'in_progress': True, 'children': []}]
    assert finalize_thoughts(thoughts)[0]['in_progress'] is False

def test_finalize_thoughts_forces_nested_children_in_progress_false():
    thoughts = [{'id': 't1', 'in_progress': False,
                 'children': [{'id': 'c1', 'in_progress': True, 'children': []}]}]
    finalize_thoughts(thoughts)
    assert thoughts[0]['children'][0]['in_progress'] is False

def test_finalize_thoughts_handles_missing_children_key():
    thoughts = [{'id': 't1', 'in_progress': True}]
    finalize_thoughts(thoughts)
    assert thoughts[0]['in_progress'] is False

def test_finalize_thoughts_leaves_other_fields_untouched():
    thoughts = [{'id': 't1', 'in_progress': True, 'error': True, 'aborted': True, 'interrupted': False}]
    finalize_thoughts(thoughts)
    assert thoughts[0] == {'id': 't1', 'in_progress': False, 'error': True, 'aborted': True, 'interrupted': False}
```

Run `pytest tests/codemie/core/test_thread.py -k finalize_thoughts -v`; expect `ImportError`/collection failure.

- [ ] **Step 1b: Add the `ThreadedGenerator` organic-leak confirmation test**, same file, using the existing `generator` fixture and real `send()`/`_process_thought()` — no mocking of `_process_thought` itself, and add `import json` to this test file's imports:

```python
def test_process_thought_leaves_orphaned_thought_in_progress_true_when_closing_event_never_arrives(generator):
    # Mirrors a real streaming turn: one thought's start+end events both arrive normally;
    # a second thought (e.g. a "CodeMie Thoughts" generic LLM entry) only ever gets its
    # start event — its closing event is lost, the same LangChain callback-lifecycle
    # unreliability documented in EPMCDME-14850. This reads generator.thoughts directly,
    # pre-finalize-helper, to prove the raw accumulator itself holds the stale value.
    generator.send(json.dumps({'thought': {'id': 'resolved', 'in_progress': True, 'message': 'start'}}))
    generator.send(json.dumps({'thought': {'id': 'resolved', 'in_progress': False, 'message': ' end'}}))
    generator.send(json.dumps({'thought': {'id': 'orphaned', 'in_progress': True, 'message': 'start'}}))
    # no closing event ever sent for 'orphaned'

    resolved = next(t for t in generator.thoughts if t['id'] == 'resolved')
    orphaned = next(t for t in generator.thoughts if t['id'] == 'orphaned')
    assert resolved['in_progress'] is False
    assert orphaned['in_progress'] is True
```

This test passes against current code (no `finalize_thoughts` involved) and documents, by construction, exactly the gap `finalize_thoughts` closes for call sites #1-#3.

- [ ] **Step 1c: Add the `AgentInvokeCallback` organic-leak confirmation test** in `tests/codemie/agents/callbacks/test_agent_invoke_callback.py`, using the existing `callback` fixture and real `on_tool_start`/`on_tool_end` methods (same pattern as the file's existing `test_overlapping_tool_activity_for_same_author_keeps_distinct_parents`); add `import uuid` if not already present:

```python
def test_get_thoughts_from_callback_retains_stale_in_progress_true_when_tool_end_never_fires(callback):
    # on_tool_start appends in_progress=True immediately via thought_processing(); nothing
    # revisits that entry unless on_tool_end/on_tool_error fires for the same run_id. This
    # proves, by running the real callback, that an unpaired start leaves the stale True in
    # callback.thoughts (== get_thoughts_from_callback()'s return value) with no sweep.
    resolved_run_id = uuid.uuid4()
    orphaned_run_id = uuid.uuid4()

    callback.on_tool_start({"name": "search_kb"}, "query one", run_id=resolved_run_id)
    callback.on_tool_end("result one", run_id=resolved_run_id)

    callback.on_tool_start({"name": "search_kb"}, "query two", run_id=orphaned_run_id)
    # on_tool_end/on_tool_error never fires for orphaned_run_id

    resolved = next(t for t in callback.thoughts if t['input_text'] == 'query one')
    orphaned = next(t for t in callback.thoughts if t['input_text'] == 'query two')
    assert resolved['in_progress'] is False
    assert orphaned['in_progress'] is True
```

This test also passes against current code and documents the equivalent gap for call sites #4-#5.

- [ ] **Step 2: Implement** in `src/codemie/core/thread.py`, right after `_process_thought`:

```python
def finalize_thoughts(thoughts: list[dict]) -> list[dict]:
    """Force in_progress=False on every thought and, recursively, every
    nested children entry. Call only on a turn's raw accumulated thought
    list once the turn has completed successfully; no other field is
    touched."""
    for thought in thoughts:
        thought['in_progress'] = False
        children = thought.get('children')
        if children:
            finalize_thoughts(children)
    return thoughts
```

- [ ] **Step 3: Run `pytest tests/codemie/core/test_thread.py tests/codemie/agents/callbacks/test_agent_invoke_callback.py -v`, confirm all pass (including the two organic-leak confirmation tests and every pre-existing test in both files), then commit.**

---

### Task 2: Wire into `_serve_data` and `_handle_a2a_stream`

**Files:**
- Modify: `src/codemie/rest_api/handlers/assistant_handlers.py:774-783` (`StandardAssistantHandler._serve_data`, `StopIteration` branch) and `:1238-1245` (`A2AAssistantHandler._handle_a2a_stream`, else branch)
- Test: `tests/codemie/rest_api/handlers/test_assistant_handlers_streaming.py`

**Interfaces:**
- Consumes: `finalize_thoughts(thoughts: list[dict]) -> list[dict]` from Task 1.

Test-first: yes — `test_serve_data_finalizes_stale_in_progress_thought_on_success` fails because `_serve_data` still persists the raw stale value.

- [ ] **Step 1: Write failing test**, modeled on the existing `test_serve_data_reraises_mcp_auth_required_from_worker_queue` pattern (same file, real `ThreadedGenerator`, `list(handler._serve_data(...))`):

```python
def test_serve_data_finalizes_stale_in_progress_thought_on_success(mock_user, mock_assistant):
    handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
    generator_queue = ThreadedGenerator()
    generator_queue.thoughts.append({'id': 't1', 'in_progress': True, 'children': []})

    def stream() -> None:
        generator_queue.queue.put(StopIteration)

    with patch.object(handler, "save_chat_history") as mock_save:
        list(handler._serve_data(
            stream=stream, generator_queue=generator_queue,
            request=AssistantChatRequest(text="run", stream=True), execution_start=0.0,
        ))

    persisted = mock_save.call_args[0][0].thoughts
    assert persisted[0]['in_progress'] is False
```

Add the equivalent for `_handle_a2a_stream` (drive `task_callback`/`stream_generator` with `final=True` on an `A2AAssistantHandler` whose `generator_queue.thoughts` has a stale `True` entry; assert the same on `mock_save.call_args`).

- [ ] **Step 2: Implement.** Import `finalize_thoughts` from `codemie.core.thread` alongside the existing `ThreadedGenerator` import (line 51). At `:779` replace `thoughts=generator_queue.thoughts` with `thoughts=finalize_thoughts(generator_queue.thoughts)`; same substitution at `:1243`.

- [ ] **Step 3: Run both tests plus the existing `test_serve_data_reraises_*` tests in the same file, confirm all pass, then commit.**

---

### Task 3: Wire into `_background_generate` and `_handle_sync`

**Files:**
- Modify: `src/codemie/rest_api/handlers/assistant_handlers.py:948-957` (`_background_generate`) and `:999-1008` (`_handle_sync`)
- Test: `tests/codemie/rest_api/handlers/test_assistant_handlers.py`

**Interfaces:**
- Consumes: `finalize_thoughts` from Task 1 (already imported in this module by Task 2).

Test-first: yes — `test_handle_sync_finalizes_stale_in_progress_thought_on_success` fails because `_handle_sync` still persists the raw callback thoughts unchanged.

- [ ] **Step 1: Write failing tests**, mocking `agent.get_thoughts_from_callback()` to return `[{'id': 't1', 'in_progress': True, 'children': []}]` and asserting `mock_save.call_args[0][0].thoughts[0]['in_progress'] is False` — one test per method, following this module's existing `handler`/`mock_user`/`mock_assistant` fixtures and `patch.object(handler, "save_chat_history")` style (see `test_filter_thoughts_*` neighbors, line ~722, for import/patch conventions). These tests mock the callback's return value directly; the organic proof that a real `AgentInvokeCallback` can produce that stale `True` value is already covered by Task 1's Step 1c and is not re-derived here.

- [ ] **Step 2: Implement.** At `:948` change `thoughts = agent.get_thoughts_from_callback()` to `thoughts = finalize_thoughts(agent.get_thoughts_from_callback())`; same substitution at `:999`.

- [ ] **Step 3: Run the two new tests plus the existing `test_filter_thoughts_*` tests in this file, confirm all pass, then commit.**

---

### Task 4: Wire into the hedged coordinator

**Files:**
- Modify: `src/codemie/rest_api/handlers/hedged_handler.py:414-422` (`_stream_agent_path` success branch)
- Test: `tests/codemie/rest_api/handlers/test_hedged_handler.py`

**Interfaces:**
- Consumes: `finalize_thoughts` from Task 1.

Test-first: yes — `test_save_chat_history_finalizes_stale_in_progress_thought` fails because the coordinator still persists `agent_queue.thoughts` unchanged.

- [ ] **Step 1: Write failing test**, reusing the `_run_stream` helper pattern at line 241 (real `ThreadedGenerator` patched in as `real_tg`): before invoking `handler._handle_stream(...)`, append `{'id': 't1', 'in_progress': True, 'children': []}` to `real_tg.thoughts`; after draining the response, assert `mock_save.call_args[0][0].thoughts[0]['in_progress'] is False` (mirrors `test_save_chat_history_called_with_agent_response`, line 284).

- [ ] **Step 2: Implement.** Import `finalize_thoughts` from `codemie.core.thread` in `hedged_handler.py`. At `:419` replace the `agent_queue.thoughts` argument to `ChatHistoryData` with `finalize_thoughts(agent_queue.thoughts)`.

- [ ] **Step 3: Run `pytest tests/codemie/rest_api/handlers/test_hedged_handler.py -v`, confirm all pass (including the existing `test_save_chat_history_called_with_agent_response` and fast-path tests), then commit.**

---

## Self-review

**Spec coverage:** All five verified call sites (`_serve_data`, hedged coordinator, `_handle_a2a_stream`, `_background_generate`, `_handle_sync`) are wired in Tasks 2-4; the shared helper with nested-`children` and stale-`True`-with-no-further-event behavior is unit-tested in Task 1; existing `_filter_thoughts`/`test_agent_streaming_callback.py` tests are left unmodified (no task touches those files). Task 1 additionally satisfies the spec's "Known test gaps this closes" commitment to organically reproduce the stale-`True`-on-success leak through **both** accumulator mechanisms — `ThreadedGenerator._process_thought` (Step 1b, via real `send()` calls with one thought's closing event withheld) and `AgentInvokeCallback` (Step 1c, via real `on_tool_start` with no matching `on_tool_end`) — reading each accumulator's own resulting state directly, before `finalize_thoughts` is ever applied, rather than only hand-building fixture dicts for the helper's own unit tests.

**Negative-constraints pass:**
- "No change to `agent_streaming_callback.py`/`agent_invoke_callback.py` callback methods" — honored; no task modifies either file, including Task 1's new Step 1c, which only adds a test that calls existing `AgentInvokeCallback` methods.
- "No change to `_filter_thoughts`" — honored; Task 3's edits stop at the point where `thoughts` is assigned, upstream of `save_chat_history`/`_filter_thoughts`.
- "No change to `_save_history_for_disconnect` / `ConversationStatus.INTERRUPTED`" — honored; no task touches `assistant_handlers.py:664-696`, so the disconnect path keeps persisting whatever it already does.
- "No change to `ThreadedGenerator._process_thought`'s merge semantics" — honored; Task 1 adds a new, separate function rather than editing `_process_thought`, and Step 1b's new test only calls `_process_thought` via the existing `send()` path, asserting on its current behavior rather than altering it.
- "No change to `history_projection_service._normalize_status` or the replay-v2 flag" — honored; no task touches either.
- "Only `in_progress` is forced; `error`/`interrupted`/`aborted` untouched" — enforced by `finalize_thoughts`'s implementation (Task 1) and asserted by `test_finalize_thoughts_leaves_other_fields_untouched`.
- "No data migration, no UI/frontend change" — honored; no task touches frontend code or writes a migration/backfill script.

**Placeholder/type-consistency scan:** No TBDs; `finalize_thoughts(thoughts: list[dict]) -> list[dict]` is the single signature referenced identically across Tasks 2-4.
