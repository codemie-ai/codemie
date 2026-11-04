# Spec: Graph cancellation on fatal error (EPMCDME-14855, slice 1/3 — graph-cancellation-core)

## Problem

When an agent run hits a fatal error (primarily token-limit truncation), the LangGraph execution
graph keeps running orphaned tasks for minutes after the client-facing stream has been torn down,
because: truncation is only detected from a code path gated on tool-call presence, the LangGraph
`stream` is never closed on the exception path, `SupervisorCoordinator` can be left holding a stale
active handoff, `ThreadedGenerator.send()` will happily enqueue into an already-closed generator,
`_send_error_to_thread` doesn't populate the structured error fields the client needs, and a second,
dead delivery path for those same fields (`_build_final_chunk`) sits unused in production.

## Scope

In scope (this slice only): A.1–A.4, B.7, B.8 as defined below. Explicitly out of scope: A.5/A.6
(consecutive-tool-error cap, error consolidation under `agent_token_limit`) and B.9/B.10
(`hedged_handler.py` `queue.Empty` timeout handling, `assistant_handlers.py` unbounded
`queue.get()`) — both are separate follow-up slices that depend on this one landing first.

## A.1 — Detect truncation inside the graph execution path itself

`langgraph_event_adapter.py`'s two `_safe_check_for_truncation` call sites
(`parse_update_type:186`, `handle_supervisor_tool_calls:261`) only fire when the last AI message in
an `"updates"` chunk has tool calls (`is_finish_reason_tool_calls`). A response truncated by
`max_tokens` with no tool call in play satisfies neither that branch nor the `is_finish_reason_stop`
branch, so it is never checked — the only backstop is `recursion_limit=150`.

Add a truncation check directly inside `_stream_graph`'s `for chunk in stream:` loop
(`langgraph_agent.py:894-898`, alongside/after `self.process_chunk(chunk, ...)`) that runs for any
finished AI message regardless of whether it carries tool calls, reusing
`_check_for_truncated_response`/`_get_truncation_indicator`'s existing format-detection logic
(OpenAI `finish_reason`, Claude `stop_reason`, Bedrock `stopReason`). This makes detection a property
of graph execution itself, not solely a side effect of event-adapter chunk parsing.

## A.2 — Guarantee the LangGraph stream is closed on the exception path

Wrap `_stream_graph`'s loop body in `try/finally` so `stream.close()` (currently only reached inside
the `if self._should_stop_streaming():` branch, `langgraph_agent.py:905-922`) also runs when an
exception propagates out of the loop (from `process_chunk`, the new A.1 check, or elsewhere). Do not
disturb the existing OTEL demotion-before-close ordering documented at `langgraph_agent.py:909-915`:
any exception-path close must still close last relative to trace-cancellation bookkeeping needed for
correct span-error-level behavior.

## A.3 — Cancel orphaned graph tasks and clear dangling handoffs

Once a fatal exception is caught, closing the `stream` generator (per A.2) is the mechanism used to
signal LangGraph to stop react-loop iterations and parallel `Send` branches — this codebase has no
other cancellation API, and no alternative was found in the LangGraph integration. Whether
`stream.close()` fully terminates in-flight `langgraph==1.1.6` tasks (vs. only stopping local
iteration) is unverified from source and is called out below as an open risk for implementation-time
verification, not a design choice to resolve here.

On the same fatal-error path, reset `SupervisorCoordinator`'s handoff tracker
(`_SupervisorHandoffTracker.active`/`pending`/`run_bindings`, `coordinator.py:70-73`) for the current
agent run. The tracker instance is one-per-`LangGraphAgent`-run (constructed in `LangGraphAgent`'s
`__init__`), so a wholesale clear of all three dicts is safe and sufficient — no per-run-id filtering
is needed. Add a reset entry point on `SupervisorCoordinator`/`_SupervisorHandoffTracker` following
the existing injected-callable pattern (`coordinator.py:147-166`), called from the same place `A.2`'s
exception handling lives (`_stream_graph`'s new `finally`, or `stream()`'s `except Exception` block —
implementation's choice, whichever keeps `self._supervisor_coordinator` reachable).

## A.4 — Guard `ThreadedGenerator.send()` against sending after close

`ThreadedGenerator.send()` (`core/thread.py:72-83`) currently calls `self.queue.put(data)`
unconditionally, both on the happy path and the non-JSON fallback branch. Make `send()` a silent
no-op (skip `queue.put` and the `_process_thought` side effect) once `self.is_closed()` is true. Do
not raise: this mirrors the codebase's existing cooperative-cancellation convention (e.g.
`_safe_check_for_truncation`'s swallow-and-log pattern) and matches the documented contract that
`close()` is already "honoured" by consumers at each chunk boundary
(`hedged_handler.py`'s `HedgedAssistantHandler` class docstring) — callers of `send()` (including
`agent_streaming_callback.py`'s `_send_thought`) must not start raising on a legitimate close race.
`close()` itself is unaffected: it still enqueues its own sentinel/error directly via `self.queue`.

## B.7 — Populate `success`/`agent_error`/`error_details` in `_send_error_to_thread`

`_send_error_to_thread` (`langgraph_agent.py:816-838`) already computes `error_response =
handle_agent_exception(e)` (line 820), an `ErrorResponse` (`core/errors.py:341-374`) carrying
`category`, `agent_error` (`AgentErrorDetails`), and `tool_errors`. Populate the
`StreamedGenerationResult` built at the end of this method with `success=False`,
`agent_error=error_response.agent_error` (when `category == ErrorCategory.AGENT`),
`tool_errors=error_response.tool_errors`, and `error_details` derived from
`error_response.get_error()` (e.g. via its `to_full()`/`to_standard()` formatting, consistent with
`BaseError.format_for_level`). No schema change: `StreamedGenerationResult` already defines all four
fields (`chains/base.py:137-144`); this is wiring only.

## B.8 — Remove the dead `agent.last_generation_result`/`_build_final_chunk` path

**Decision: remove, not fix.** `_build_final_chunk` (`assistant_handlers.py:698-717`) reads
`getattr(agent, "last_generation_result", None)` and returns `None` whenever that's falsy — which is
always in production (only test mocks ever set the attribute). Its entire purpose was to surface
`success`/`agent_error`/`tool_errors` on a final chunk, which B.7 now delivers directly to the client
via the `StreamedGenerationResult` sent through the `ThreadedGenerator` on both the happy path
(`stream():772-783`) and the error path (`_send_error_to_thread`). Delete `_build_final_chunk` and
both of its call sites: `assistant_handlers.py:790` and `hedged_handler.py:411`. At each call site,
remove the `final_chunk = self._build_final_chunk(...)` assignment and whatever conditional
send/yield of `final_chunk` depended on it, without touching the surrounding `queue.Empty`-timeout or
normal-completion control flow that those methods otherwise implement (that logic belongs to the
consumer-loop-timeouts follow-up slice and must be left intact).

## Acceptance Criteria

- A finished AI message chunk that is truncated by the token limit is detected inside
  `_stream_graph`'s own loop even when the message carries no tool calls.
- `stream.close()` runs on every exit from `_stream_graph`'s loop — normal cancellation, natural
  completion, and any exception — and the OTEL demotion-before-close ordering is preserved on the
  exception path.
- On a fatal error, `SupervisorCoordinator`'s `active`/`pending`/`run_bindings` state is fully
  cleared for that agent run; no dangling active handoff remains resolvable via `binding_for`.
- `ThreadedGenerator.send()` called after `close()` is a no-op: no item is enqueued, no exception is
  raised, and `_process_thought`'s side effects do not run.
- `_send_error_to_thread` sends a `StreamedGenerationResult` with `success=False` and populated
  `agent_error`/`tool_errors`/`error_details` reflecting `handle_agent_exception`'s classification.
- `_build_final_chunk` no longer exists in `assistant_handlers.py`; neither `hedged_handler.py` nor
  `assistant_handlers.py` references `agent.last_generation_result` or `_build_final_chunk` anywhere
  (including tests, which should stop stubbing the attribute rather than assert on dead code).
- All existing tests for `_stream_agent_path`'s normal-completion branch and `_serve_data`'s
  completion branch pass with the final-chunk logic removed (i.e., updated to reflect that B.7's
  fields now arrive through the normal `StreamedGenerationResult` stream, not a synthesized final
  chunk).

## Non-goals

- A.5/A.6 (consecutive-tool-error cap, consolidating repeated tool errors under
  `agent_token_limit`) — separate slice.
- B.9/B.10 (`hedged_handler.py`'s `queue.Empty` timeout, `assistant_handlers.py`'s unbounded
  `queue.get()`) — separate slice; the `queue.Empty`/`break` logic in `_stream_agent_path`
  (~392-397) is untouched here even though B.8's removal edits an adjacent line (411) in the same
  method.
- No new LangGraph-specific cancellation API (checkpointer interrupt, explicit task cancellation
  token, etc.) is introduced — A.3 relies solely on the existing `stream.close()` mechanism.
- No new configuration surface, no new `ErrorCode`/exception types, no frontend (`codemie-ui/`)
  changes.
- No change to `handle_tool_error=True`'s retry-feedback behavior itself (that's what generates the
  ~27 wasted calls today) — this slice stops the graph and clears state once a fatal error is
  detected; it does not change tool-error retry policy.

## Open Risks

- Whether closing the Python `stream` generator actually terminates in-flight LangGraph react-loop /
  `Send`-branch tasks is unverified against installed `langgraph==1.1.6` internals. A.3's fix is
  best-effort on the only mechanism identified in this codebase; if implementation-time verification
  shows generator-close doesn't propagate as real task cancellation, a follow-up using a
  LangGraph-specific cancellation API may be needed.
- If `thread_generator` is already closed when `_send_error_to_thread` runs (e.g. a hedging
  fast-path race), A.4's guard silently drops that error result instead of leaving it unread in the
  queue. No client-visible regression (the consumer already stopped reading), but worth a code
  comment at implementation time so it isn't mistaken for a bug later.
- No dedicated unit tests exist today for `SupervisorCoordinator`'s tracker or for
  `ThreadedGenerator.send()`-after-close; this slice's implementation will need to add both from
  scratch rather than extend existing coverage.
