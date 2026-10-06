# EPMCDME-14859: Fix orphaned tool message from nested sub-assistant handoff

## Problem

When a parent (supervisor) assistant delegates to a sub-assistant that itself
calls a tool, the final user-facing answer is correct but a raw Azure/LiteLLM
400 error is appended after it:

```
Error code: 400 - {'error': {'message': "litellm.BadRequestError: AzureException
BadRequestError - Invalid parameter: messages with role 'tool' must be a response
to a preceeding message with 'tool_calls'..."}}
```

Calling the same sub-assistant directly does not reproduce the error — only
delegation through a parent supervisor triggers it.

## Root cause

`src/codemie/agents/supervisor/history.py`, `_consume_pending_handoff_message`
(lines 102-137). The supervisor's pre-model history hook queues a pending
`transfer_to_<subagent>` handoff, then consumes the next `AIMessage` whose
`name` matches the sub-agent as that handoff's terminal answer — using
**author-name matching only**, with no check for `message.tool_calls`.

The real message sequence for a sub-agent that itself calls a tool is:

```
AIMessage(jiratesthelp, tool_calls=[generic_jira_tool])   # intermediate
ToolMessage(generic_jira_tool): Jira result
AIMessage(jiratesthelp): terminal answer
```

`_consume_pending_handoff_message` matches the **first** `AIMessage(name="jiratesthelp")`
it sees — the intermediate tool-call message — and consumes it as the handoff
result. This removes the assistant's tool-call declaration from the
provider-facing history while leaving the matching `ToolMessage` in place,
producing an orphaned `role='tool'` message with no preceding `tool_calls`.
Azure/OpenAI correctly rejects that sequence.

Direct sub-assistant invocation never runs through this supervisor
compaction hook, which is why it doesn't reproduce there.

## Fix

### 1. History fix (root cause) — design option 3, defer and reorder

**Design history.** Two earlier designs were considered and rejected:

- **Option 1** (preserve the nested pair verbatim, in place): disproven by
  hand trace. The supervisor's own `transfer_to_<subagent>` call sits
  unresolved (its `ToolMessage` isn't reconstructed until the terminal
  answer arrives) while a *second* `AIMessage(tool_calls=[...])` — the
  sub-agent's own tool call — appears before it resolves.
  `sanitize_rich_history_for_llm`'s existing sanitizer
  (`agent_runtime_utils.py`, `_RichHistoryToolReplaySanitizer`) tracks only
  one open assistant tool-call block at a time; a second one appearing
  before the first resolves triggers its `consecutive_assistant_tool_calls`
  drop rule, discarding the *outer* handoff call and re-orphaning the
  eventual result — this mirrors the Azure/OpenAI protocol rule itself, not
  a sanitizer limitation to patch.
- **Option 2** (atomically drop the nested pair from the LLM-replay view):
  this is the design that was actually implemented, TDD'd, and live-verified
  in a prior session (documented in the extended
  `D:\Projects\EPMCDME-14859-recovery-notes.md`). It produces valid,
  minimal output (`[Human, supervisor_handoff_call, ToolMessage(final
  answer)]`) and has zero UI impact since the UI's live nested-tool-call
  display is built by `coordinator.py`/`langgraph_event_adapter.py` from
  live stream chunks at execution time, never from this hook's output.
  **Explicitly rejected by the user for this implementation**: dropping the
  sub-agent's intermediate tool-call/tool-result pair from the LLM-replay
  history — even though it never touches persisted state or the live
  UI — was ruled unacceptable. The user wants every message retained in
  what gets replayed to the LLM on a later turn, not merely retained in the
  UI/persisted state.

**Adopted fix — option 3, defer and reorder.** Every message is kept.
Nothing is dropped. Only the *position* of the supervisor's own
`transfer_to_<subagent>` call (and its reconstructed answer) shifts: instead
of sitting at its original chronological position (before the sub-agent's
nested exchange), it is deferred and re-emitted immediately next to its own
answer, once the sub-agent's terminal (non-tool-call) response arrives. This
produces a fully valid, complete sequence:

```
Before (persisted / UI order — unaffected either way):
Human, AI(supervisor, call-123), Tool(task), AI(jiratesthelp, tool_calls=[jira]), Tool(jira), AI(jiratesthelp, final)

After (LLM-replay order for the next model call):
Human, AI(jiratesthelp, tool_calls=[jira]), Tool(jira), AI(supervisor, call-123), Tool(call-123, final answer)
```

Verified by hand-tracing every existing test in
`test_langgraph_multi_assistant_handoffs.py`, plus a dedicated
sub-agent-driven ripple analysis, against this design (see Testing section)
before implementation. Key findings that shaped the exact mechanics below:

- Deferral must be **conditional**, not unconditional: the supervisor's
  handoff-call `AIMessage` may only be popped from `filtered_messages` and
  held back when it is the message *immediately preceding* the handoff-task
  `ToolMessage` currently being processed (true by construction whenever
  nothing has intervened yet). If a duplicate/placeholder `ToolMessage`
  already sits between them (the "already visible" and "empty placeholder"
  test scenarios), the call must **not** be popped — unconditional deferral
  would relocate it past an already-resolved answer and invert a valid
  call/response pair into an invalid one.
- The still-unresolved-handoff (`[no response]`) path
  (`_append_pending_handoffs`) must learn the same trick symmetrically: if
  the handoff's call was deferred and the sub-agent never produces a
  terminal answer, the deferred `AIMessage` must be re-emitted immediately
  before its `[no response]` placeholder — otherwise the call is popped but
  never re-added, silently losing it.
- The already-committed `tool_calls` guard in
  `_consume_pending_handoff_message` (`if message.tool_calls: return
  False`) remains necessary: without it, the sub-agent's own intermediate
  tool-call message would still be wrongly matched against the pending
  handoff by author name and consumed as the terminal answer, exactly
  reproducing the original bug.
- The parallel-handoff path is untouched: it already synthesizes its
  handoff-call `AIMessage` on demand at consume time (never eagerly
  appends it), so it was never affected by this defect and needs no change.

**Implementation shape** in `src/codemie/agents/supervisor/history.py`:

- A new helper, `_pop_matching_supervisor_call(filtered_messages, tool_call_id)`,
  pops and returns `filtered_messages[-1]` only if it is an `AIMessage`
  whose `tool_calls` contains a matching id; otherwise returns `None`.
- `_queue_pending_handoff_message`'s single-handoff branch calls this
  helper when queuing a handoff task. If it returns an `AIMessage`, that
  message (not the old `(name, tool_call_id)` tuple) is stored in
  `pending_single_handoffs`; otherwise the existing tuple form is stored
  unchanged (preserving today's behavior for the duplicate/placeholder
  cases).
- `_consume_pending_handoff_message`'s single-handoff branch handles both
  stored forms: if the popped pending item is an `AIMessage`, it is
  re-appended immediately before the reconstructed `ToolMessage`; if it's
  the tuple form, behavior is unchanged from today.
- `_append_pending_handoffs`'s single-handoff loop gains the same
  AIMessage-or-tuple handling, re-emitting a deferred `AIMessage` before its
  `[no response]` placeholder.
- `pending_single_handoffs`'s type changes from
  `dict[str, deque[tuple[str, str | None]]]` to
  `dict[str, deque[AIMessage | tuple[str, str | None]]]` throughout.

No other function changes: `_queue_pending_handoff_message`'s parallel
branch, `_is_parallel_supervisor_handoff_message`, and the trailing
`sanitize_rich_history_for_llm` sweep are all orthogonal and continue to
operate correctly on the now-valid, fully-content-preserving message list.

### 2. Streaming presentation guard (secondary, defense in depth)

`src/codemie/agents/langgraph_agent.py`, `_send_error_to_thread`
(lines 816-838) unconditionally appends the error/user message onto
`chunks_collector` before joining it into `generated`. If the sub-agent had
already produced a successful answer (accumulated in `chunks_collector`
during streaming) and a *later* model call then raises, the raw error text
gets concatenated onto the successful answer in the user-visible response.

Fix: only append the user-facing error message when nothing has succeeded
yet.

```python
def _send_error_to_thread(self, e: Exception, execution_start: float, chunks_collector: list[str]) -> None:
    ...
    if not "".join(chunks_collector).strip():
        chunks_collector.append(user_message)
    generated, execution_error = self._process_chunks(chunks_collector, config, llm_error_code)
    ...
```

`execution_error` is still set from `llm_error_code` regardless, so the
error is still tracked/logged internally — only the user-visible
concatenation is suppressed when a successful answer already exists.

This is defense in depth: with fix #1 in place, the specific 400 in this
ticket's repro no longer occurs, so this path won't trigger for this exact
scenario. It is included per explicit request to guard against any other
backend error arising after a successful streamed answer.

## Testing

Extend `tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py`:

- New test reproducing the exact ticket shape: a single-handoff queue to
  `jiratesthelp`, followed by `AIMessage(jiratesthelp, tool_calls=[...])`,
  `ToolMessage(generic_jira_tool)`, then a terminal
  `AIMessage(jiratesthelp)`. Assert:
  - the intermediate `AIMessage(tool_calls=...)` and its `ToolMessage` are
    both retained, adjacent, and unmodified in `llm_input_messages`;
  - the terminal `AIMessage`'s content becomes the `transfer_to_jiratesthelp`
    handoff's `ToolMessage` result;
  - no `ToolMessage` in the output lacks a preceding `AIMessage.tool_calls`
    entry with a matching id (protocol-level assertion, not just "no crash").
- Run the full existing suite in this file to confirm no regressions across
  parallel handoffs, single handoffs, `[no response]` placeholders, and the
  standalone-orphan sanitization test.

New test for the streaming guard (likely a new or existing test module
covering `LangGraphAgent`, using a stubbed/mocked `chunks_collector` and
exception path — exact location decided during planning):

- `chunks_collector` non-empty (simulating a prior successful answer) +
  exception raised → `generated` from `_process_chunks` equals the original
  joined content, with no error text appended; `execution_error` is still
  set.
- `chunks_collector` empty + exception raised → existing behavior preserved
  (user-facing error message is appended, since nothing succeeded).

## Out of scope

- `EPMCDME-13889`'s nested-supervisor author-attribution fix
  (`_resolve_pending_handoff_author`, `_stamp_subagent_identity`,
  `_close_unanswered_tool_calls`) — solves a superset problem (a sub-agent
  that is itself a supervisor, answering under the generic `"supervisor"`
  node name). Not required for this ticket's single-level repro. Reference
  only.
- Resurrecting reverted commit `1cd636dcc` (`EPMCDME-13657`) — confirmed
  superseded; current `main` already independently re-added everything that
  revert removed (`sanitize_rich_history_for_llm` calls, `[no response]`
  placeholders).
