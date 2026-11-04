# EPMCDME-14855 Slice 2 — Tool-Error Cap + Consumer-Loop Hardening

Backend (`codemie/`) only. Depends on slice 1 (A.1-A.4, B.7, B.8, `2026-09-14-epmcdme-14855-graph-cancellation-core`),
which is on this same branch and not yet merged to main: the
LangGraph stream is now closed and orphaned graph tasks are cancelled on any exception raised
inside `_stream_graph`'s loop, and `StreamedGenerationResult.success`/`agent_error`/`error_details`
are already populated by `_send_error_to_thread`. This slice builds on that exception path rather
than duplicating it.

## A.5 — Cap on consecutive tool errors

Add `AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS: int = 5` to `configs/config.py`, next to
`AI_AGENT_RECURSION_LIMIT` (`configs/config.py:744`), same convention (env-configurable, shipped
default, no deploy-time requirement).

`LangGraphAgent` (constructor, `langgraph_agent.py:155/184`) gains a plain instance attribute
tracking the current consecutive-tool-error streak, mirroring how `self.handle_tool_error` and
`self.recursion_limit` are already held. It increments on any tool error (validation or
execution — this slice does not attempt to distinguish the two, see Non-goals) and resets to zero
on any tool success. Because it's an instance attribute, each `LangGraphAgent` — including one
per parallel sub-agent `Send` branch — tracks its own independent streak (AC6).

When the streak reaches the configured cap, instead of returning an error string to the model
(today's `handle_tool_error=True` path, `_configure_tools`, `langgraph_agent.py:1153-1180`), the
tool-error handling raises a new exception — e.g. `MaxConsecutiveToolErrorsExceededException` —
which `AgentErrorClassifier._try_tool_error_limit` recognises by type and classifies as its own
`ErrorCode.AGENT_TOOL_ERROR_LIMIT` (friendly message `AGENT_MSG_TOOL_ERROR_LIMIT`). It is deliberately
not folded into `agent_token_limit`: that code's advice ("ask for a shorter response") is wrong when an
integration failed repeatedly, and analytics must tell the two causes apart. Raising (rather than swallowing via
`handle_tool_error`) lets the exception propagate out of the tool-call stack into `_stream_graph`,
where slice 1's already-merged exception path closes the stream, cancels the graph, and drives
`_send_error_to_thread` — this is the same path `TokenLimitExceededException` already uses; no
parallel error-handling path is introduced.

```python
class MaxConsecutiveToolErrorsExceededException(Exception):
    """Raised when AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS consecutive tool errors occur.
    Classified by type as ErrorCode.AGENT_TOOL_ERROR_LIMIT."""
```

## A.6 — Cap repeated tool-error messages (capped, not consolidated)

**Review outcome (MR 4276):** AC4 is met as "capped, not consolidated". The cap (A.5) bounds the retry
burst, but the client still shows one Failed thought per call: `on_tool_start` already sent each call's
own thought, and both `ThreadedGenerator._process_thought` and the UI's `_handleThought` append
`message` when an id repeats, so the first streak thought accumulates every error text. True
consolidation needs either skipping the per-call thought once a streak is active or a replace mode for
thoughts (a UI change); it is a follow-up together with a shared streak tracker, since the bookkeeping
currently lives in both `AgentStreamingCallback` and `LangGraphAgent`.

Original intent:

`AgentStreamingCallback.on_tool_error` (`agent_streaming_callback.py:386-414`) currently keys
thoughts by the per-tool-call `run_id`, so N consecutive failures send N separate error thoughts.
Change the keying so consecutive tool errors within one streak update the *same* thought in place
(`storage.update_thought`) rather than creating a new one per call — one visible message per
streak, not one per call. The streak identity/count is the same counter A.5 maintains (one
counter, not two); a tool success ends the streak and any subsequent error starts a new one.

Because A.5 now caps the streak at `AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS`, the consolidated
in-flight thought is superseded by the single final `agent_token_limit`-classified message that
`_send_error_to_thread` sends when the cap trips (via A.5's exception) — consumers see one
consolidated surface for the whole retry-and-fail sequence, not a burst followed by a final error.

`HIDE_AGENT_STREAMING_EXCEPTIONS` (`configs/config.py:931`) is not bypassed: when true,
`_process_chunks` (`langgraph_agent.py:794-814`) still substitutes the sanitized
guardrails/stacktrace text for the human-readable message exactly as it does for any other agent
error today. The structured `agent_error`/`error_code=agent_token_limit` fields remain populated
regardless of the flag, since that population already happens independently of the text
substitution.

## B.9 — `hedged_handler.py::_stream_agent_path` queue.Empty handling

On `queue.Empty` from `agent_queue.get(timeout=300)` (`hedged_handler.py:392-397`), instead of
logging and breaking silently: build a final error `StreamedGenerationResult` (`success=False`,
`agent_error`/`error_details` populated via the existing timeout branch of
`AgentErrorClassifier.parse_exception`, using `AGENT_MSG_TIMEOUT`), `yield` it as the final chunk,
and call `self.save_chat_history(...)` with the accumulated `response`/`thoughts` — the same shape
already used on the normal `StopIteration` completion path a few lines below
(`hedged_handler.py:405-420`), substituting the error result for `response.generated`.

## B.10 — `assistant_handlers.py::_serve_data` unbounded queue.get()

`generator_queue.queue.get()` (`assistant_handlers.py:743`) has no timeout. Wrap it with
`timeout=300` (same value as B.9, for symmetry) and a `try/except queue.Empty:` branch that
mirrors B.9: build a final error chunk the same way, `yield` it, and call
`self.save_chat_history(...)` before breaking — not raise, to match B.9's break-not-raise
handling. B.9 and B.10 are fixed independently; no shared consumer-loop helper is introduced
(per the ticket's explicit note that none exists today).

## C.1 — Persist the error flag on the thought of a truncated LLM call (added 2026-10-22)

Problem: a response cut at the output-token limit aborts the run, but the thought of that LLM call
was streamed and saved without `error`, so the UI showed it Failed live (a UI guess) and Success
after a reload.

Requirement: the thought of the truncated LLM call is flagged `error=true`, closed, sent on the
stream and persisted. Only what is known is flagged; there is no fallback.

- The thought is identified by the truncated message: the callback storage is keyed by
  `AIMessage.id`. `_check_for_truncated_response` calls `on_thought_failed(message.id, author)`
  before raising `TokenLimitExceededException`.
- `AgentStreamingCallback.on_thought_failed` flags the open thought (`error=True`,
  `in_progress=False`), resends it with an empty `message` (the text was already streamed token by
  token; `ThreadedGenerator` appends `message`) and removes it from storage. No open thought for
  that id: no-op. A thought is never invented.
- Truncated text with `invalid_tool_calls` (`_finalize_truncated_text`): the thought is flagged
  before `_on_llm_end`, which removes it from storage. The existing finalize-then-raise order is
  kept; the cost is that this failed thought can lose its routed-model label.
- `Thought.error` already exists in the backend model and in both SDKs: no new field, no API or SDK
  change.

Not flagged (not known): stream timeouts, failures with no open thought, and truncation in a
supervisor sub-agent when the author is not passed down (`_check_truncation_in_chunk`). Tool errors
and LLM errors were already flagged (`on_tool_error`, `on_llm_error`).

AC: the flagged thought is sent with `error=true` and persisted through `ThreadedGenerator`; the
text is not duplicated; no open thought sends nothing; other thoughts are untouched; a complete
response flags nothing.

## Non-goals

- (Superseded by review: `core/errors.py` gained `_try_tool_error_limit`, a typed rule, replacing the
  original plan of matching a message substring.)
- No shared helper unifying `hedged_handler.py`'s and `assistant_handlers.py`'s consumer loops.
- B.9/B.10 do not signal or cancel the producer/agent thread — only consumer-side final-chunk +
  history-save behavior; graph/task cancellation is slice 1's (already-merged) concern.
- A.5's cap does not distinguish tool-validation errors from other tool-runtime errors; it counts
  any consecutive tool error.
- No `SupervisorCoordinator` changes — dangling-handoff cleanup on the new fatal exception relies
  entirely on slice 1's already-merged exception-path handling.
- No `codemie-ui/` changes.
- No new toggle or behavior change to `HIDE_AGENT_STREAMING_EXCEPTIONS` itself.

## Testing (AC7 remainder)

- A.5: streak counter increments per tool error, resets on success, raises
  `MaxConsecutiveToolErrorsExceededException` exactly at the configured cap, and that it classifies as
  `agent_tool_error_limit`; each of two independently-constructed `LangGraphAgent`s (simulating parallel
  sub-agents) maintains its own streak (AC6). Use the `create_mock_agent`
  (`object.__new__(LangGraphAgent)`) pattern from `test_langgraph_truncation.py`.
- A.6: `on_tool_error` called N times for the same streak updates the same `storage.update_thought`
  target (the first streak thought), not N. This does not mean the client renders one message: see A.6.
- B.9: `queue.Empty` on `_stream_agent_path` yields exactly one final error chunk and calls
  `save_chat_history` once, using existing `test_hedged_handler.py` fixtures.
- B.10: `queue.Empty` on `_serve_data`'s `generator_queue.queue.get(timeout=300)` yields a final
  error chunk and calls `save_chat_history`; confirm no existing test file assumption is broken (no
  dedicated `_serve_data` test file was found — this may be a new file).

## Risks

- LangGraph 1.1.6's actual cancellation semantics for parallel `Send` branches (slice 1's
  concern) still bound how reliably A.5's fatal exit stops sibling branches versus only the branch
  that hit the cap — not resolved in this slice.
