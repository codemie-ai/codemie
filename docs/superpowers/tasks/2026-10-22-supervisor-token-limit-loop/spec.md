# Spec: Stop the supervisor/sub-agent loop after truncation or disconnect

**Ticket**: EPMCDME-16233 · **Status**: ready for planning

## Intent

A truncated tool-call response (output-token limit hit) is detected today only by the
stream *consumer* (`langgraph_event_adapter.py`, `langgraph_agent.py._stream_graph`). The
underlying LangGraph subgraph — supervisor and sub-agent nodes compiled by
`create_supervisor` (`langgraph_supervisor==0.0.31`) — keeps running in a background-executor
worker thread regardless, re-calling the failing tool until the subgraph's recursion limit.
This burns LLM calls, wall time and cost after the user has already received the error (seen:
47 extra calls, 17.5 min, $6.10, ~95% of spend after the error). The fix moves the stop
*into the graph*, using the `pre_model_hook` extension point already wired on both node types.

## Goals (acceptance criteria)

1. Supervisor node: when its own last tool-call response was truncated at the output-token
   limit, no further LLM call happens in that turn — the node fails immediately instead of
   retrying toward the recursion limit.
2. Sub-agent node: same stop, scoped to that sub-agent's own execution, when *its* last
   tool-call response was truncated.
3. Disconnect / hedging-cancel: when the stream consumer has already marked the run as
   aborted (client disconnect or hedging-cancel) and a node is about to make its next model
   call, that call does not happen.
4. Negative case: a normal, non-truncated tool-call response and a still-open, non-cancelled
   stream are unaffected — the tool-calling loop continues exactly as today.
5. Verification: at least one test exercises a real, unmocked `create_supervisor(...).compile()`
   graph, not the existing `create_supervisor`-patched fixture, and asserts no LLM call occurs
   after a truncated response.

## Non-goals

- EPMCDME-14855 (UI "stuck in progress" symptom after a truncated call) — separate ticket,
  consumer-side only, same files; this spec does not touch `langgraph_event_adapter.py`'s
  existing detection path.
- The unexplained Tessa-assistant handoff-truncation case — follow-up, not reproduced here.
- Raising `AI_AGENT_RECURSION_LIMIT` (150→1000, EPMCDME-15638) — separate ticket; this fix is
  what makes that change safe, not a prerequisite for it.
- Deduplicating `LangGraphAgent._get_truncation_indicator` and the module-level
  `get_truncation_indicator` in `langgraph_event_adapter.py` into one shared implementation —
  this spec reuses the former from the new hook (see Design) but leaves the latter, and the
  consumer-side call sites that use it, untouched.
- Graceful degradation (e.g. supervisor continuing without a failed sub-agent) — the stop is a
  hard failure of that node, matching today's user-facing `agent_token_limit` error; no new
  recovery behavior is introduced.
- Any change to how the API boundary (`proxy_router.handle_agent_exception`,
  `_send_error_to_thread`) formats the existing `agent_token_limit` error.

## Design

**New hook, same composition pattern.** Add one pre_model_hook function to
`src/codemie/agents/supervisor/pre_model_hooks.py`, following the existing shape (free function
over `state: dict[str, Any]` returning `None` on no-op) used by
`_strip_handoff_back_messages_pre_model_hook` / `_subagent_task_pre_model_hook` /
`_image_artifact_pre_model_hook`. Add it to the existing `_compose_pre_model_hooks(...)` tuples
in both `_build_supervisor_agent` (`langgraph_agent.py:~297`) and `_build_single_agent`
(`langgraph_agent.py:~345`), so it runs identically on the supervisor's own react node and on
every sub-agent node — the mechanism `create_supervisor`/`create_smart_react_agent` already
forward `pre_model_hook` into needs no new wiring.

**Truncation check, reused not duplicated.** A `pre_model_hook` runs *before* the next model
call and only sees `state["messages"]`/`state["llm_input_messages"]`; to catch that the
*previous* call was truncated, the hook inspects the last `AIMessage` already appended to
`state["messages"]`. It calls the existing instance-bound check
(`LangGraphAgent._get_truncation_indicator` / `_check_for_truncated_response`,
`langgraph_agent.py:~1304-1366`) rather than adding a third detector. On a positive result it
raises `TokenLimitExceededException` (`core/exceptions.py:304`) — the same exception
`proxy_router.handle_agent_exception` already converts to the user-facing `agent_token_limit`
error, so only the raise site moves (stream consumer → graph node), not the error contract.

**Abort flag for disconnect / hedging-cancel.** The hook closure (built once per
`LangGraphAgent.init_agent()`, already closing over `self`) also checks the agent's
`ThreadedGenerator` (`core/thread.py`) — `is_closed()` / `cancellation_reason` — before letting
the next model call proceed. On a positive result it raises a new exception, distinct from
`TokenLimitExceededException`, so a disconnect/cancel is never mislabeled as a token-limit
error in logs or (if a stream somehow still reads it) to a user:

```python
# core/exceptions.py — new, alongside TokenLimitExceededException
class StreamAbortedException(Exception):
    """Raised from inside a graph node when the owning stream was closed or
    hedging-cancelled; callers should stop without surfacing a user-facing error."""
```

`proxy_router.handle_agent_exception` (or the nearest existing catch site around
`.stream()`) swallows `StreamAbortedException` silently — no error is sent to a thread that
already has no listener.

**Negative case.** When the last message is not truncated and the stream is open/not
cancelled, the hook returns `None`, identically to the other composed hooks — no change to
today's tool-calling loop.

**Exception propagation.** `_compose_pre_model_hooks` has no `try/except` today, so a raise
from one hook should propagate unmodified out of the node and out of `.stream()`. This is
asserted, not assumed: it is exactly what acceptance criterion 5's new fixture must prove
against a real compiled graph, since no current test compiles one.

## Testing

A new fixture builds `create_supervisor(...).compile()` (and/or `create_smart_react_agent`)
unmocked, with a stub LLM returning a response carrying a truncation-indicating
`finish_reason`/`stop_reason`. The test asserts: (a) the raise happens inside the node, (b) no
further LLM invocation is recorded afterward, for both a supervisor-level and a sub-agent-level
truncation, and (c) a disconnect/cancelled `ThreadedGenerator` produces the same no-further-call
result via `StreamAbortedException`. A companion negative test confirms an ordinary multi-step
tool-calling run completes unaffected.

## Open risks

- Exception propagation through `_compose_pre_model_hooks` inside a real compiled graph is
  unverified until the new fixture (above) exists; if it swallows or wraps the exception
  unexpectedly, the hook's placement (before vs. wrapping the composition call) may need to
  change — a plan-time, not spec-time, decision.
- EPMCDME-14855 touches the same two files (`langgraph_agent.py`, `langgraph_event_adapter.py`)
  and is "Ready for Review"; landing both in parallel risks a rebase collision.
