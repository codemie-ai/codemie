# Spec: Finalize `in_progress` on persisted thoughts (EPMCDME-14624)

## Problem

`GET /api/v1/conversations/{id}` can return a completed assistant turn where an
intermediate "CodeMie Thoughts" entry is persisted with `in_progress: true`. The UI (and
`history_projection_service._normalize_status`, `src/codemie/service/conversation/history_projection_service.py:513-520`)
treats a persisted `in_progress: true` as an interrupted state, so a successfully
completed tool call renders as interrupted after page reload.

Root cause: two independent accumulators mirror the *last* streaming event seen for a
thought id, with no step that forces `in_progress=False` once the turn is known to have
completed successfully:
- `generator_queue.thoughts` / `agent_queue.thoughts`, built by
  `ThreadedGenerator._process_thought` (`src/codemie/core/thread.py:85-135`), from events
  emitted by `AgentStreamingCallback` (`src/codemie/agents/callbacks/agent_streaming_callback.py:198-252`).
- `AgentInvokeCallback.thoughts` (`src/codemie/agents/callbacks/agent_invoke_callback.py`,
  `thought_processing`/`on_llm_end`/`on_tool_end`/`on_tool_error`), read via
  `agent.get_thoughts_from_callback()` (`src/codemie/agents/assistant_agent.py:375-380`).
  Same structural gap: a thought is appended/updated with whatever `in_progress` value its
  last-seen lifecycle event carried (`agent_invoke_callback.py:148,162`), and nothing sweeps
  the list before `get_thoughts_from_callback()` returns it. If the paired close event
  (`on_llm_end`/`on_tool_end`/`on_tool_error`) never fires for a given `run_id` — the same
  class of LangChain-callback-lifecycle unreliability documented for `AgentStreamingCallback`
  in EPMCDME-14850 — the orphaned thought stays `in_progress: true` in `self.thoughts`
  indefinitely. Confirmed leaking; not merely "identical mechanism so assumed."

Unlike both of these, the workflow path's `WorkflowExecutionService._get_thoughts_from_states`
(`src/codemie/service/workflow_execution/workflow_execution_service.py:646-677`) already
hardcodes `"in_progress": False` when materializing a completed turn.

### Verified call-site inventory

`save_chat_history` is called with a non-empty, unfinalized `thoughts` list on **five**
success paths (not two):

| # | Call site | Source of `thoughts` |
|---|---|---|
| 1 | `AssistantRequestHandler._serve_data`, `StopIteration` branch (`assistant_handlers.py:774-783`) | `generator_queue.thoughts` (`ThreadedGenerator`) |
| 2 | Hedged coordinator success branch (`hedged_handler.py:414-422`) | `agent_queue.thoughts` (`ThreadedGenerator`) |
| 3 | `A2AAssistantHandler._handle_a2a_stream`, `StopIteration` branch (`assistant_handlers.py:1238-1245`) | `generator_queue.thoughts` (`ThreadedGenerator`) — same mechanism as #1/#2 |
| 4 | `_background_generate` (`assistant_handlers.py:948-957`) | `agent.get_thoughts_from_callback()` → `AgentInvokeCallback.thoughts` |
| 5 | `_handle_sync` (`assistant_handlers.py:999-1008`) | `agent.get_thoughts_from_callback()` → `AgentInvokeCallback.thoughts` |

Sites #1-#3 share one mechanism (`ThreadedGenerator`); sites #4-#5 share a second, distinct
mechanism (`AgentInvokeCallback`) that was investigated fresh for this revision and found to
have the same unfinalized-last-event gap, not merely assumed by analogy.

## Approach

Add one small shared helper that snapshots a turn's accumulated thought list and forces
`in_progress=False` on every entry and, recursively, on every nested `children` entry. Call
it at **all five** verified call sites above, on the raw list, immediately before it is
wrapped into `ChatHistoryData` — i.e. before `save_chat_history`/`_filter_thoughts` see it. No
other field (`error`, `interrupted`, `aborted`) is touched; those are already computed
correctly by the callback layer and must reflect the true terminal state as-is.

```python
def finalize_thoughts(thoughts: list[dict]) -> list[dict]:
    """Return thoughts with in_progress forced False, recursively into children.
    Call only on a turn's raw accumulator once the turn has completed successfully."""
```

This mirrors the existing workflow-path convention rather than inventing a new one, and keeps
the change confined to the call sites that own "this turn is done, successfully" — regardless
of which of the two accumulator mechanisms fed them.

## Non-goals

- No change to `agent_streaming_callback.py` / `on_llm_end`, `agent_invoke_callback.py`'s
  callback methods, or any callback lifecycle event on either mechanism. Touching
  `agent_streaming_callback.py` risks reopening the duplicate-invocation `IntegrityError` fixed
  by EPMCDME-14850 (`docs/superpowers/tasks/2026-09-09-workflow-thought-persistence-fix/spec.md`).
- No change to `AssistantRequestHandler._filter_thoughts`
  (`src/codemie/rest_api/handlers/assistant_handlers.py:468-515`). It is shared by the
  disconnect path too; forcing `in_progress=False` there would mask genuinely-interrupted
  thoughts and violate AC #6. The new helper runs upstream of it instead.
- No change to `_save_history_for_disconnect`
  (`src/codemie/rest_api/handlers/assistant_handlers.py:664-696`) or to `ConversationStatus
  .INTERRUPTED` handling. Genuinely interrupted/failed turns keep whatever `in_progress`/
  `interrupted`/`error` state they already carry.
- No change to the tool-confirmation flow. It already persists `in_progress=False`/
  `interrupted=True` correctly (`tool_call_confirmation_mixin.py:90-91`) and is unaffected by
  this change.
- No change to `ThreadedGenerator._process_thought`'s merge semantics
  (`src/codemie/core/thread.py:85-135`). `in_progress: true` remains a legitimate transient
  live-streaming signal during an active response (AC #7); only the pre-persistence snapshot
  is swept.
- No change to `history_projection_service._normalize_status`
  (`src/codemie/service/conversation/history_projection_service.py:513-520`). Correctly
  persisted data makes its `in_progress → interrupted` mapping moot for this bug.
- No change to `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED` behavior or either `_filter_thoughts`
  branch; the fix runs before that flag check and is agnostic to it.
- No UI/frontend change — the backend is persisting stale data; fixing persistence is the fix.
- **This is a persistence-boundary fix, not a root-cause fix.** The orphan thought produced by
  a tool's nested/unpaired LLM call (the "CodeMie Thoughts" entry whose close event never
  fires) is still created and still persisted exactly as before — only its `in_progress` flag
  changes from `true` to `false`. If that orphan thought's *content* is itself undesirable to
  render (e.g. a raw, unfiltered SEARCH/REPLACE block or partial tool payload), it will still
  render after this fix — just labeled Success/completed instead of Aborted/interrupted. This
  fix does not evaluate or filter thought content, and does not address why an orphan thought
  is produced in the first place.
- **No data migration and no UI change is in scope.** Conversations completed and persisted
  before this fix ships keep whatever `in_progress` value is already stored; reloading them
  will continue to show the stale interrupted/Aborted rendering exactly as today. This fix only
  prevents the bug on turns that complete *after* it ships. Backfilling or migrating
  already-persisted conversations to correct historical `in_progress` values is explicitly out
  of scope for this spec.

## Acceptance criteria

- Every thought (including nested `children`) in a turn that completes successfully through
  any of the five verified call sites (`_serve_data`, the hedged coordinator, `_handle_a2a_stream`,
  `_background_generate`, `_handle_sync`) is persisted with `in_progress: false`.
- Reload + reopen of a conversation whose tool call completed successfully during the live
  session, **for a turn completed after this fix ships**, no longer shows that tool call as
  interrupted. (Conversations completed before the fix shipped are unaffected — see Non-goals.)
- Historical conversation rendering, for turns completed after this fix ships, uses the actual
  persisted completion/error/interruption state rather than a stale `in_progress: true` —
  narrowing the ticket's AC #3/#4 to post-fix turns, since no migration of already-persisted
  data is in scope.
- A genuinely interrupted/disconnected turn (`_save_history_for_disconnect`) is unaffected by
  this change and still persists/renders as interrupted.
- A turn where a tool genuinely errored still persists `error`/`aborted` as before; only
  `in_progress` is forced false.
- Live streaming of an in-progress turn (before completion) is unaffected — no behavior change
  during an active response.
- New unit test(s) for the finalize helper itself (top-level + nested `children`, including the
  "last event was `in_progress: true`, no further event ever arrives" case from the ticket's
  evidence).
- New/updated test(s) at all five call sites (`_serve_data`, hedged coordinator,
  `_handle_a2a_stream`, `_background_generate`, `_handle_sync`) asserting `save_chat_history`
  is invoked with fully finalized thoughts on the success path.
- Existing `_filter_thoughts` tests and `test_agent_streaming_callback.py` assertions continue
  to pass unmodified.

## Known test gaps this closes

`ThreadedGenerator._process_thought` currently has zero unit tests; both existing
`_filter_thoughts` tests only exercise `in_progress` already `False` or absent, never a stale
`True` carried into a successful save. `AgentInvokeCallback.thoughts` similarly has no test
asserting behavior when a thought's closing event never arrives for its `run_id`. The
implementation plan should add coverage for exactly the stale-`True`-on-success case (both
accumulator mechanisms), plus the nested-`children` case, per
`tests/codemie/core/test_thread.py`, `tests/codemie/rest_api/handlers/test_assistant_handlers.py`,
and `tests/codemie/agents/test_assistant_agent/test_get_thoughts_from_callback.py`.

## Open risks / follow-ups

- The reviewer suggested that backfilling/migrating already-persisted conversations (so old
  history stops showing stale interrupted state on reload) could be a separate follow-up
  ticket. That is flagged here as a candidate follow-up, not pulled into this spec's scope —
  see the data-migration non-goal above.
