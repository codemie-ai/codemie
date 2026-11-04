# Technical Research

**Task**: langgraph agent streaming token-limit error-handling supervisor (EPMCDME-14855)
**Generated**: 2026-09-14
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14855 (backend-only scope, repository /Users/viktarpapou/EPAM/EPM-SDAI/codemie/).

Bug: When an agent run hits the configured output-token limit (finish_reason=length), the LangGraph
execution graph is not actually stopped — only the stream consumer is torn down. Orphaned graph tasks
(react-loop / parallel Send branches) keep running for many minutes (observed ~21 min, ~27 wasted LLM
calls in a production log), retrying via handle_tool_error=True on incomplete tool-call args, until
recursion_limit=150 eventually kicks in. Meanwhile the UI shows "CodeMie thought" stuck in-progress
forever because the client never receives a final/error chunk.

A prior manual investigation (by the user, then spot-verified by an Explore agent against current
HEAD) already identified these exact locations — use them as your starting point and confirm/refine
rather than exploring blind, but do verify current line numbers and behavior yourself since code
moves:

- src/codemie/agents/langgraph_agent.py
  - `_stream_graph` (currently ~lines 854-931): `for chunk in stream:` (~894) with
    `if self._should_stop_streaming(): stream.close(); break` (~905-922) — stream.close() is called
    ONLY in that normal-cancellation branch, there is no try/finally around the loop, so an exception
    propagating out of `self.process_chunk(...)` (~898), e.g. TokenLimitExceededException, leaves the
    LangGraph stream open and its underlying graph tasks orphaned/running.
  - `stream()` (~line 722): `except Exception as e: self._send_error_to_thread(...)` (~788-789),
    `finally: if auth_required_error is None: self.thread_generator.close()` (~790-792) — only the
    thread_generator (consumer-facing) is closed, not the LangGraph stream itself.
  - `_send_error_to_thread` (~lines 816-838): builds a StreamedGenerationResult with `generated`,
    `generated_chunk=""`, `last=True`, `time_elapsed`, `context`, `execution_error` — does NOT set
    `success`, `agent_error`, `tool_errors`, `error_details` (fields exist on the model but stay
    default/None).
  - constructor ~line 155 sets `self.handle_tool_error = True` (default), applied to every tool via
    `tool.handle_tool_error = self.handle_tool_error` (~line 1133) — this is what causes tool
    validation ToolExceptions to be fed back to the model as an error message instead of raising,
    producing the retry loop.
  - `_should_stop_streaming` (~933-941): checks `self.thread_generator.is_closed()`.
  - `_check_for_truncated_response` (~line 1324) and `_log_incomplete_tool_calls` (~line 1296) — only
    called from `_safe_check_for_truncation`, itself only called from
    langgraph_event_adapter.py's `parse_update_type` (~186) and `handle_supervisor_tool_calls` (~260)
    — i.e. only in the consumer/event-adapter path, never inside the graph's own execution.
    `TokenLimitExceededException` itself is raised inside `_check_for_truncated_response`
    (~line 1369).
  - `_promote_pending_handoff` (~1205), `_buffer_supervisor_context` (~1212),
    `_flush_buffered_supervisor_contexts` (~1217) — supervisor chunk buffering, on this class, not on
    coordinator.py.

- src/codemie/agents/supervisor/coordinator.py: `SupervisorCoordinator` (~line 147) with
  `queue_supervisor_handoffs` (~171), `handle_supervisor_handoff_back` (~192),
  `handle_supervisor_subassistant_result` (~213), `promote_pending_handoff` (~233) — tracks
  pending/active handoffs between supervisor and sub-agents; does NOT itself handle sub-agent errors.
  Check whether a sub-agent erroring with agent_token_limit can leave a dangling "active" handoff in
  this tracker.

- src/codemie/errors.py: `_try_token_limit` (~656-663), `classify()` (~729-741) — agent_token_limit
  classification.

- src/codemie/config.py: `AI_AGENT_RECURSION_LIMIT: int = 150` (~line 744) — the only backstop that
  eventually ends an orphaned graph run.

- core/thread.py: `ThreadedGenerator` — `send()` (~line 72) enqueues via `self.queue.put(data)`
  (~line 80) WITHOUT checking `is_closed()` first; `is_closed()` exists (~153-154). This lets an
  orphaned task keep pushing into a closed generator.

- hedged_handler.py: the timeout/break logic (`agent_queue.get(timeout=300)` -> on `queue.Empty` ->
  `break` with NO final chunk and NO save_chat_history) lives in `_stream_agent_path` (~379-434,
  around ~392-397) — NOT in `_coordinate_stream` as an earlier guess assumed; confirm exact current
  location/lines.

- assistant_handlers.py: `generator_queue.queue.get()` with no timeout (~line 764);
  `_build_final_chunk` (~line 698) reads `agent.last_generation_result` via `getattr(..., None)`
  (~line 707) — nothing in src/ ever assigns that attribute (only test mocks do), so this is dead
  code in production.

- agent_streaming_callback.py: `on_tool_error` (~line 386) updates/sends an error-thought per
  run_id on every tool error — this is why ~27 separate raw tool-validation errors show up instead of
  one consolidated token-limit message.

- chains/base.py: `StreamedGenerationResult` (~lines 123-150) already defines `success` (~137),
  `agent_error` (~138), `tool_errors` (~139), `error_details` (~142) — the fields B.7/B.8 need to
  populate/wire through exist already.

Scope for the eventual implementation (do NOT implement anything yourself — this is research only):
backend items only, in codemie/: (A.1) detect truncation inside the graph execution path itself, not
only in the consumer/event-adapter path; (A.2) guarantee the LangGraph stream is closed on the
exception path in _stream_graph; (A.3) cancel orphaned graph tasks (react-loop iterations, parallel
Send branches) once a fatal error is detected, and make sure SupervisorCoordinator doesn't leave
dangling active handoffs; (A.4) guard ThreadedGenerator.send() against enqueueing after is_closed();
(A.5) cap on consecutive tool validation errors that forces a fatal exit; (A.6) consolidate repeated
tool-call validation errors under agent_token_limit instead of emitting each separately; (B.7)
populate success/agent_error/error_details in _send_error_to_thread; (B.8) fix or remove the dead
agent.last_generation_result / _build_final_chunk code path; (B.9) hedged path: on queue.Empty timeout,
send a final error chunk and call save_chat_history instead of silently breaking; (B.10)
assistant_handlers.py generator_queue.queue.get() needs a timeout with proper handling.
Explicitly OUT of scope: codemie-ui/ (frontend) — do not research or touch it.

---

## 2. Codebase Findings

### Existing Implementations

All prior-investigation claims were re-verified against current HEAD; line numbers shift slightly but
the described behavior is accurate. Corrections/refinements below.

- `src/codemie/agents/langgraph_agent.py`
  - `_stream_graph` spans lines 854-931. The stream is created at line 883
    (`self.agent_executor.stream(..., stream_mode=["updates", "messages"], subgraphs=bool(self.subagents), ...)`).
    The `for chunk in stream:` loop is at line 894, inside a `with suppress_stdout():` block (line 893).
    `self.process_chunk(chunk, chunks_collector)` is called at line 898 with no surrounding
    try/except/finally — an exception raised here (or in `_get_last_ai_message_content` /
    `_update_last_message_if_needed`) propagates straight out of `_stream_graph` without ever calling
    `stream.close()`. `stream.close()` is called only at line 921, inside the
    `if self._should_stop_streaming():` branch (lines 905-922), immediately followed by `break`.
  - `stream()` spans lines 722-792. The try block covers `_agent_streaming` (line 759); it catches
    `MCPAuthenticationRequiredException` separately (line 784, re-raises after closing the thread
    generator with the exception) and a general `except Exception as e:` (line 788) that calls
    `self._send_error_to_thread(e, execution_start, chunks_collector)` (line 789). The `finally`
    block (790-792) closes `self.thread_generator` only when no auth-required error occurred. Nothing
    in this method or in `_send_error_to_thread` touches `self.agent_executor` or the LangGraph
    `stream` object created inside `_stream_graph` — that object is local to `_stream_graph`'s call
    stack and is only reachable from there.
  - `_send_error_to_thread` spans lines 816-838. It builds a `StreamedGenerationResult` with
    `generated`, `generated_chunk=""`, `last=True`, `time_elapsed`, `context=self.thread_context`,
    `execution_error=execution_error` (from `_process_chunks`, line 828). It does not set `success`,
    `agent_error`, `tool_errors`, or `error_details` — those keyword arguments are simply omitted, so
    the Pydantic model defaults (`None`) are used.
  - Constructor: `handle_tool_error: bool = True` parameter at line 155, stored as
    `self.handle_tool_error = handle_tool_error` at line 184. Applied per-tool in `_configure_tools`
    at line 1133 (`tool.handle_tool_error = self.handle_tool_error`), unconditionally for every tool in
    `self.tools` (loop starts line 1117) — no bypass for a "fatal" mode.
  - `_should_stop_streaming` spans lines 933-941; checks `self.thread_generator.is_closed()` (935) and
    inspects `self.thread_generator.cancellation_reason` only to select a log message / hedging
    demotion path — it does not consult any exception state.
  - `_check_for_truncated_response` (lines 1324-1373) raises `TokenLimitExceededException` at line
    1369. `_log_incomplete_tool_calls` is at lines 1296-1322. Both are called only from
    `_safe_check_for_truncation` (lines 1252-1272), which itself is called from two places, both in
    `langgraph_event_adapter.py`: `parse_update_type` (line 186) and `handle_supervisor_tool_calls`
    (line 261) — confirmed neither call site is inside `_stream_graph`'s loop body directly; they run
    as a side effect of `process_chunk` -> `_process_chunk_for_agent`/`_process_chunk_for_supervisor` ->
    the event adapter, which IS called from inside the `for chunk in stream:` loop (line 898) before
    `stream.close()` would ever run. So truncation detection already happens per-chunk during
    iteration, but nothing stops the underlying graph tasks that produced/will produce further chunks.
  - `_promote_pending_handoff` (1205-1207), `_buffer_supervisor_context` (1212-1215),
    `_flush_buffered_supervisor_contexts` (1217-1223) confirmed on `LangGraphAgent`, delegating handoff
    bookkeeping to `self._supervisor_coordinator` (a `SupervisorCoordinator` instance) while buffering
    the actual streamed chunk context locally.
  - Parallel Send branches: line 497 (`parallel_sends.append(Send(parallel_agent_name, {...}))`) shows
    the graph fans out via `langgraph.types.Send` for parallel sub-agent dispatch — these are the
    "parallel Send branches" the task refers to as orphan-able.
  - `recursion_limit` flows from the constructor param (line 152, default
    `config.AI_AGENT_RECURSION_LIMIT`) into `self.recursion_limit` (175) and finally into the LangGraph
    run config at line 1060 (`run_config.update({"recursion_limit": self.recursion_limit})`) — this is
    the only mechanism that terminates an orphaned graph today.

- `src/codemie/agents/supervisor/coordinator.py` — `SupervisorCoordinator` class at line 147.
  Confirmed methods: `queue_supervisor_handoffs` (171), `handle_supervisor_handoff_back` (192),
  `handle_supervisor_subassistant_result` (213), `promote_pending_handoff` (233), plus
  `binding_for` (168) and `rebind_author_to_active_handoff` (256). Handoff state lives on the
  internal `_SupervisorHandoffTracker` (`self.tracker`, line 166) with three dicts: `run_bindings`,
  `pending`, `active` (lines 71-73). `active` entries are only removed via `complete()` (114-123),
  called from `handle_supervisor_handoff_back` and `handle_supervisor_subassistant_result` — both are
  reached only via the normal "handoff back" / "subassistant result" chunk-parsing paths in
  `langgraph_event_adapter.py`. Nothing in `SupervisorCoordinator` or its call sites reacts to a
  sub-agent raising `TokenLimitExceededException` (or any exception): if a sub-agent run under an
  active handoff blows up, `tracker.active[raw_author]` keeps the entry (no `complete()` call), so
  `binding_for(author)` continues to resolve it — a dangling active handoff is possible if error
  handling short-circuits before `handle_supervisor_handoff_back`/`handle_supervisor_subassistant_result`
  run. No sub-agent-error hook exists on this class today.

- `src/codemie/core/errors.py` (NOT `src/codemie/errors.py` — that path does not exist; the module is
  under `core/`). `AgentErrorClassifier._try_token_limit` at lines 656-663: matches on substrings
  `"max_output_tokens"`, `"truncat"`, `"token limit"`, `"token limit exceeded"` in the lowercased
  exception message, and returns `build_agent_error_dict(ErrorCode.AGENT_TOKEN_LIMIT)` — no direct
  `isinstance(exc, TokenLimitExceededException)` check, it is purely string-matching based.
  `AgentErrorClassifier.classify` at lines 729-744 wraps `parse_exception` (699-727), which tries, in
  order: budget exceeded, timeout, token limit, callback failure, network error, configuration error
  (first match wins) and returns an `ErrorResponse` with `category=ErrorCategory.AGENT` and the built
  `AgentErrorDetails`. This classifier is invoked by `handle_agent_exception` (imported in
  `langgraph_agent.py` from `codemie.enterprise.litellm.proxy_router`), which is exactly what
  `_send_error_to_thread` calls (line 820: `error_response = handle_agent_exception(e)`) — so the
  `agent_error` details this classifier computes are available at that call site but currently
  discarded (see B.7 gap above).

- `src/codemie/configs/config.py` (NOT `src/codemie/config.py`). `AI_AGENT_RECURSION_LIMIT: int = 150`
  confirmed at line 744. Adjacent: `AGENT_MSG_TOKEN_LIMIT` (line 920, friendly user message),
  `HIDE_AGENT_STREAMING_EXCEPTIONS: bool = False` (line 931), `CODEMIE_SUPPORT` (639). No existing
  "max consecutive tool errors" or similar cap setting exists anywhere in this config file.

- `src/codemie/core/thread.py` — `ThreadedGenerator`. `send()` at lines 72-83: on the happy path
  (JSON-parseable `data`) it calls `self.queue.put(data)` at line 80 unconditionally, with no
  `is_closed()` check anywhere in the method; the exception fallback branch (non-JSON `data`, lines
  73-77) also calls `self.queue.put(data)` (line 76) unguarded. `close()` (144-151) sets
  `self.closed = True` and pushes a sentinel/exception onto the same queue; `is_closed()` (153-154)
  just returns `self.closed`. Because `send()` never checks `is_closed()`, an orphaned graph task that
  keeps calling back into a now-closed `ThreadedGenerator` will keep enqueuing behind the `StopIteration`
  sentinel already placed by `close()` — the consumer already stopped reading (per `_stream_agent_path`
  / `_serve_data`), so those extra items sit in the queue forever (a minor memory-growth side effect
  layered on top of the CPU/LLM-cost waste from A.2/A.3).

- `src/codemie/rest_api/handlers/hedged_handler.py` — `_stream_agent_path` spans lines 379-434 (NOT
  `_coordinate_stream`, which is a distinct method at 646-728 that calls `_stream_agent_path` at line
  695). Inside `_stream_agent_path`'s `while True:` loop (392): `value = agent_queue.get(timeout=300)`
  (394) is wrapped in `try/except queue.Empty:` (393/395) which logs an error (396) and `break`s (397)
  with no final chunk yielded and no `self.save_chat_history(...)` call — confirmed exactly as
  described. Contrast with the normal-completion branch a few lines below (398-425), which does build
  a final chunk via `_build_final_chunk` and does call `save_chat_history` before `break`ing.

- `src/codemie/rest_api/handlers/assistant_handlers.py` — `_serve_data`'s consumer loop (lines
  743-780ish) calls `generator_queue.queue.get()` at line 764 with no timeout argument — confirmed. If
  the underlying agent thread hangs (orphaned graph task never sends `StopIteration`), this consumer
  blocks forever with no fallback. `_build_final_chunk` (698-717) reads
  `agent_result = getattr(agent, "last_generation_result", None)` at line 707, and returns `None`
  immediately if falsy (708-709) — a `grep -rn "last_generation_result" src/ tests/` confirms the
  attribute is set ONLY in `tests/codemie/rest_api/handlers/test_hedged_handler.py` (11 occurrences,
  all `mock_agent.last_generation_result = None`); no production code under `src/` ever assigns this
  attribute on a real agent instance, so `_build_final_chunk` always returns `None` in production
  today, making the `success`/`agent_error`/`tool_errors` population path in this method dead code.

- `src/codemie/agents/callbacks/agent_streaming_callback.py` — `on_tool_error` at lines 386-414:
  keyed per `run_id` via `storage.get(str(run_id))` (396); if a thought exists for that run_id it is
  updated with `error=True` and the raw error message (399-406), classified via
  `_classify_execution_error(error)` (400), and sent via `self._send_thought(thought, execution_error)`
  (413). Each tool call gets its own `run_id`, so N failed tool calls in the retry loop produce N
  separate error-thought sends — this is the mechanism generating ~27 distinct error surfaces in the
  reported production log instead of one consolidated message.

- `src/codemie/chains/base.py` — `StreamedGenerationResult` at lines 123-150. Confirmed fields:
  `success: Optional[bool] = None` (137), `agent_error: Optional[AgentErrorDetails] = None` (138),
  `tool_errors: Optional[list[dict]] = None` (139-141), `error_details: Optional[dict] = None`
  (142-144). All four already exist on the model with docstring-level descriptions; no schema change
  needed to populate them, only wiring at the call sites (`_send_error_to_thread`,
  `_build_final_chunk`).

### Architecture and Layers Affected

- **Agent runtime layer** (`src/codemie/agents/`): `LangGraphAgent` (`langgraph_agent.py`) is the
  central orchestrator — owns `_stream_graph`, `stream()`, `_send_error_to_thread`,
  `_check_for_truncated_response`, `_configure_tools` (tool `handle_tool_error` wiring), and holds a
  `SupervisorCoordinator` instance (`self._supervisor_coordinator`).
- **Event adapter layer** (`langgraph_event_adapter.py`): `LangGraphEventAdapter` /
  `LangGraphCallbackBridge` — translates raw LangGraph stream chunks (`"updates"`/`"messages"` modes)
  into agent callback invocations; this is where truncation is currently detected
  (`_safe_check_for_truncation` call sites at lines 186 and 261), but only as a side effect of
  chunk-by-chunk parsing, not as an independent graph-execution guard.
- **Supervisor coordination layer** (`src/codemie/agents/supervisor/`): `coordinator.py`
  (`SupervisorCoordinator`, `_SupervisorHandoffTracker`), `bootstrap.py`, `history.py`,
  `pre_model_hooks.py`, `constants.py` — tracks handoff lifecycle between the supervisor node and
  sub-agent nodes.
- **Streaming/thread-bridge layer** (`src/codemie/core/thread.py`): `ThreadedGenerator` — the
  producer/consumer queue bridging the agent's worker thread to the HTTP streaming response.
  `MessageQueue` Protocol (line 32) formalizes the `send`/`close`/`is_closed` contract.
  `HedgingCancellationReason` (line 44) is the only structured cancellation-reason enum in this file.
- **REST handler layer** (`src/codemie/rest_api/handlers/`): `hedged_handler.py`
  (`HedgedAssistantHandler._stream_agent_path`, `_coordinate_stream`, `_handle_stream`) and
  `assistant_handlers.py` (`StandardAssistantHandler._serve_data`, `_build_final_chunk`,
  `_run_stream`) — both are consumer-side loops reading from a `ThreadedGenerator`.
- **Callback layer** (`src/codemie/agents/callbacks/agent_streaming_callback.py`):
  `AgentStreamingCallback.on_tool_error` — per-tool-call error surfacing to the UI thought stream.
- **Error classification layer** (`src/codemie/core/errors.py`): `AgentErrorClassifier` (token limit,
  timeout, budget, callback, network, configuration classifiers) and `LiteLLMErrorClassifier` — both
  feed `handle_agent_exception` in `codemie.enterprise.litellm.proxy_router` (not read in this pass;
  out of the ticket's named file list, but is the glue between `_send_error_to_thread` and
  `AgentErrorClassifier.classify`).
- **Chain/model layer** (`src/codemie/chains/base.py`): `StreamedGenerationResult`,
  `GenerationResult`, `Thought` — shared response/error schema already carrying the fields B.7/B.8
  need to populate.
- **Configuration layer** (`src/codemie/configs/config.py`): `AI_AGENT_RECURSION_LIMIT`,
  `AGENT_MSG_TOKEN_LIMIT`, `HIDE_AGENT_STREAMING_EXCEPTIONS` — the only backstop/config surface today.

### Integration Points

- `LangGraphAgent._stream_graph` drives `self.agent_executor.stream(...)` — a compiled LangGraph
  graph (`langgraph` 1.1.6, per `pyproject.toml`); the "orphaned graph tasks" the ticket describes are
  LangGraph-internal execution units (react-loop iterations, `Send`-dispatched parallel branches) that
  the current code has no handle on beyond letting the Python generator (`stream`) go out of scope on
  the happy/cancellation path.
- `LangGraphAgent` imports and composes `SupervisorCoordinator` (`agents/supervisor/coordinator.py`),
  `LangGraphEventAdapter`/`LangGraphCallbackBridge` (`langgraph_event_adapter.py`), and
  `ToolCallConfirmationMixin` — all in the same package.
- `stream()`'s exception path calls `handle_agent_exception` from
  `codemie.enterprise.litellm.proxy_router`, which in turn is expected to use
  `AgentErrorClassifier`/`LiteLLMErrorClassifier` in `core/errors.py` to build an `ErrorResponse`.
- `hedged_handler.py`'s `HedgedAssistantHandler` subclasses `StandardAssistantHandler`
  (`assistant_handlers.py`) and reuses its `_build_final_chunk`, `save_chat_history`, `ChatHistoryData`.
  Both handlers consume the same `ThreadedGenerator` contract from `core/thread.py`.
- `AgentStreamingCallback` (`agent_streaming_callback.py`) is registered as a LangChain
  `BaseCallbackHandler` and is the path by which `on_tool_error`/`on_tool_end` events reach the
  `ThreadedGenerator` (via `self._send_thought` -> presumably `thread_generator.send`, not directly
  read in this pass).
- `langgraph-supervisor` (`create_supervisor`, imported at line 60 of `langgraph_agent.py`) is a
  third-party package (`langgraph_supervisor`) wrapping multi-agent handoff graph construction;
  `SupervisorCoordinator` is CodeMie's own bookkeeping layered on top of it, not a fork of it.

### Patterns and Conventions

- **Delegation via dunder-mangled private wrappers**: `LangGraphAgent` exposes thin
  `__parse_message_type`/`__parse_update_type`/`__handle_supervisor_*` wrapper methods (lines
  1171-1226) that simply forward to `self._event_adapter` or `self._supervisor_coordinator` — the
  event adapter and coordinator are constructed with callables/bound methods passed in
  (`SupervisorCoordinator.__init__` at 148-166 takes `extract_agent_name_from_tool`,
  `tool_call_id_to_uuid`, `emit_handoff`, etc. as injected callables) rather than holding a reference
  to the whole `LangGraphAgent`. Any new coordinator method (e.g., a hypothetical
  `handle_subagent_error`) would follow this same injected-callable pattern, not take `self` (agent)
  directly.
- **Safe-wrapper-around-raiser pattern**: `_safe_check_for_truncation` wraps
  `_check_for_truncated_response`, re-raising only the expected `TokenLimitExceededException` and
  swallowing (logging) anything else — a defensive pattern for detection logic that must not itself
  crash the agent loop.
- **`MessageQueue` Protocol** (`core/thread.py` line 32): formalizes `__iter__`/`__next__`/`send`/
  `close`/`is_closed` — any change to `ThreadedGenerator.send()` semantics (A.4) should keep
  conformance to this Protocol so other implementers (if any) stay interchangeable.
  `grep` found no second implementer of this Protocol in `src/`.
  `HedgingCancellationReason` (thread.py line 44) is the existing enum-like class for structured
  `close(reason=...)` values — a new "fatal error" cancellation reason would naturally live here.
  `close()`'s `error` kwarg already supports enqueuing a `BaseException` for `__next__`/`get()` to
  re-raise, which is the existing mechanism `stream()` uses for `MCPAuthenticationRequiredException`
  (line 786: `self.thread_generator.close(e)`); a fatal token-limit error could plausibly reuse this
  path rather than inventing a new one.
- **Dataclass-based structured state**: `_SupervisorChunkContext` (frozen dataclass),
  `_HedgeRaceState` (dataclass) — both used to bundle related mutable/immutable state instead of loose
  tuples/dicts; any new state introduced for A.3/A.5 (e.g., a consecutive-error counter, an "is fatal"
  flag) would fit this convention.
  `HandoffRunBinding = tuple[UUID, str | None]` and `PendingHandoff = tuple[UUID, str | None, str, str | None]`
  are module-level type aliases in `coordinator.py` for the tuple shapes flowing through the tracker.
- **Config-driven friendly messages + error codes**: `configs/config.py` holds all
  `AGENT_MSG_*`/`LITELLM_MSG_*` user-facing strings; `core/error_constants.py` holds the `ErrorCode`
  enum (`AGENT_TOKEN_LIMIT = "agent_token_limit"`, line 81) and the
  `ErrorCode -> friendly message` mapping (`AGENT_ERROR_FRIENDLY_MESSAGES`, referenced at
  `error_constants.py:286`). A new "consolidated tool-error under token limit" message or a
  "consecutive tool error cap exceeded" message would follow this same config+constants pairing.
- **Classifier chain pattern**: `AgentErrorClassifier.parse_exception` (errors.py, 699-727) tries a
  fixed ordered list of `_try_*` methods, first match wins — string-substring matching on
  `str(exception).lower()`, not `isinstance` checks. `_try_token_limit` currently matches loosely on
  `"truncat"`/`"token limit"`/`"max_output_tokens"`; any new exception type introduced for A.5's
  "fatal cap exceeded" would need either a matching substring in its message or a new `_try_*` method
  to classify cleanly under `agent_token_limit` as the task's A.6 requires.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/workflows/langgraph-workflows.md` — covers `WorkflowExecutor`/workflow nodes
  (`src/codemie/workflows/`), a different LangGraph consumer than the agent runtime this ticket
  touches. Its stated principle ("extend the existing executor/node model rather than a parallel
  execution path") is analogous guidance but the guide itself does not cover `LangGraphAgent`,
  `_stream_graph`, or graph-task cancellation.
- `.ai-run/guides/agents/langchain-agent-patterns.md` — covers `AIToolsAgent`
  (`src/codemie/agents/assistant_agent.py:136`) and `TaskResult.from_agent_response`
  (`assistant_agent.py:99`); general guidance to extend/configure existing agent classes and reuse
  callback/run-config helpers rather than building a parallel runtime. Does not mention
  `LangGraphAgent`, streaming cancellation, or token-limit handling specifically.
- `.ai-run/guides/development/error-handling.md` — general typed-exception and sanitized-failure
  guidance (`ValidationException`/`ExtendedHTTPException`, sanitize provider-failure messages,
  produce a distinct message per failure mode rather than one catch-all). Directly relevant
  principle for B.7/A.6: "one message per failure mode" and "log the substitution with identifying
  context" — supports consolidating the ~27 tool-validation errors under one `agent_token_limit`
  message rather than passing each raw error through.
- No guide under `.ai-run/guides/` specifically documents `_stream_graph`, `ThreadedGenerator`,
  `SupervisorCoordinator`, or the hedging (`hedged_handler.py`) request-racing design; those are
  documented only via inline docstrings/comments in the source files themselves (see below).

### Architectural Decisions

- No formal ADR files found for this domain. Inline architectural rationale exists as docstrings and
  comments in the source, most notably:
  - `_stream_graph`'s docstring "Notes" section explains the disconnect-monitoring intent (but predates
    this bug's exception-path gap).
  - The comment block at `_stream_graph` lines 909-915 explaining why the OTEL trace demotion must
    happen *before* `stream.close()` (span lifecycle / Langfuse control-flow exception ordering) —
    relevant context for A.2: any exception-path `stream.close()` addition must respect this
    demotion-before-close ordering if it wants to preserve trace-cancellation semantics.
  - `HedgedAssistantHandler`'s class docstring (hedged_handler.py lines 129-145) documents the
    fast-path/agent-path race design and explicitly calls out that `ThreadedGenerator.close()` is
    "honoured" by both `AIToolsAgent` and `LangGraphAgent` via `is_closed()` checks "at each chunk
    boundary" — this is the existing cooperative-cancellation contract that A.2/A.3 must extend to
    also stop the graph itself, not just the chunk consumer.
  - `_FastPathOutcome`/`FastPathResult` docstrings explain the "no out-parameters, single return
    value" design idiom used throughout `hedged_handler.py`.

### Derived Conventions

- Error-code enum + friendly-message-map pairing (`ErrorCode` in `core/error_constants.py` +
  `AGENT_MSG_*` in `configs/config.py`) is the standard way new agent-level error categories are
  surfaced to the classifier and to the user.
- New cross-cutting counters/flags (a consecutive-tool-error counter, a "fatal" flag) are conventionally
  attached either to the relevant frozen/plain dataclass (`_SupervisorChunkContext`, `_HedgeRaceState`)
  or as plain instance attributes on `LangGraphAgent` (mirroring `self.handle_tool_error`,
  `self.recursion_limit`), not as global/module state.
- Config additions follow the existing `AI_AGENT_*` / `AGENT_MSG_*` naming and live in
  `src/codemie/configs/config.py` alongside `AI_AGENT_RECURSION_LIMIT`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/agents/test_langgraph_truncation.py` — thorough unit coverage of
  `_check_for_truncated_response`, `_get_truncation_indicator`, `_log_incomplete_tool_calls`,
  `_safe_check_for_truncation` (all format variants: OpenAI `finish_reason`, Claude `stop_reason`,
  Bedrock camelCase `stopReason`), plus `extended_error()` message sanitization for
  `AGENT_TOKEN_LIMIT` (ensures tool names / raw exception text don't leak to the user). Uses a
  hand-built `object.__new__(LangGraphAgent)` mock-agent helper (`create_mock_agent`, lines 25-55)
  rather than constructing a real agent — this is the pattern any new test for `_stream_graph`'s
  exception path would likely need to follow, since full `LangGraphAgent` construction requires many
  collaborators.
- `tests/codemie/agents/test_langgraph_event_adapter.py` — covers the event adapter's chunk parsing
  (not read in full this pass, but its existence confirms `parse_update_type`/
  `handle_supervisor_tool_calls` have adapter-level tests separate from the truncation-detection tests).
- `tests/codemie/core/test_thread.py` — six tests: `test_init`, `test_iter`, `test_next`, `test_send`,
  `test_is_closed`, `test_close`. `test_send` (lines 50-53) only asserts `queue.put` is called with
  the given data on a generator that is NOT closed — there is no test exercising `send()` after
  `close()`, i.e., no coverage of the exact behavior A.4 needs to change.
- `tests/codemie/rest_api/handlers/test_hedged_handler.py` — large suite (60+ tests) covering
  fast-path/agent-path racing, metric emission, config validation. Confirmed via grep: every
  `last_generation_result` reference in the whole test tree is `mock_agent.last_generation_result = None`
  in this file — i.e., existing tests already stub this attribute to `None` (matching current
  production behavior of "always None"), so they exercise `_build_final_chunk`'s early-return path,
  not a populated-result path. No test found in this file that exercises the `queue.Empty` timeout
  branch of `_stream_agent_path` (lines 392-397) — grep for `test_.*timeout` inside this file did not
  surface a queue-Empty-specific test among the listed `def test_` names (only fast-path timeout races
  in `_coordinate_stream`'s `first_result_ready.wait(timeout=timeout_s)`, a different timeout).
- `tests/codemie/core/test_errors.py` — includes `test_classify_token_limit` (line 352), confirming
  `AgentErrorClassifier`/`classify()` has direct token-limit test coverage already.
- `tests/codemie/agents/callbacks/test_agent_streaming_callback.py` — includes
  `test_on_tool_error_sends_with_error_flag` (line 342) and `test_on_tool_error_unknown_run_id_is_noop`
  (line 364) — per-call error-thought behavior is tested, but nothing tests consolidation of multiple
  consecutive tool errors into one message (A.6 is untested by construction, since the behavior
  doesn't exist yet).
- No test file exists for `src/codemie/agents/supervisor/coordinator.py` specifically (the only
  `test_coordinator.py` hit is under `tests/enterprise/migration/`, an unrelated migration-coordinator).
  `SupervisorCoordinator`/`_SupervisorHandoffTracker` handoff-lifecycle logic (queue/promote/complete/
  dangling-active-handoff scenarios) has no dedicated unit tests found.

### Testing Framework and Patterns

- `pytest` (see `pytest.ini`: `testpaths = tests`, `pythonpath = src`, `--import-mode=importlib`,
  env vars `ENV=local`, `REPOS_LOCAL_DIR`, `PG_URL` set via `pytest-env`).
- Heavy use of `unittest.mock.Mock`/`patch.object` for isolating single methods (e.g.,
  `test_thread.py`'s `patch.object(generator, 'queue')`, `test_langgraph_truncation.py`'s
  `object.__new__(LangGraphAgent)` + manually-set attributes).
- Fixture-based setup for handler-level tests (`tests/codemie/rest_api/handlers/test_hedged_handler.py`
  uses pytest fixtures `handler`, `request_`, `raw_request`, `hedging_config`, etc., not read in full).

### Coverage Gaps

- No test exercises the exception path through `_stream_graph`'s `for chunk in stream:` loop (i.e.,
  no test asserts `stream.close()` is or isn't called when `process_chunk` raises) — this is exactly
  what A.2 will change, and it is currently unverified in either direction.
- No test exercises graph-task/Send-branch cancellation (A.3) — no test constructs a real or
  substantially-mocked `agent_executor` to observe orphaned-task behavior; the existing truncation
  tests operate purely on message/metadata objects, not on the graph execution itself.
- No test covers `SupervisorCoordinator` leaving a dangling `active` handoff entry when a sub-agent
  errors (A.3's coordinator half) — no coordinator unit tests exist at all.
- No test covers `ThreadedGenerator.send()` behavior after `close()` (A.4) — `test_send` only covers
  the open-generator case.
- No test covers a "cap on consecutive tool validation errors" (A.5) — the feature doesn't exist yet,
  so naturally untested; but also no existing counter/threshold pattern elsewhere in the codebase to
  model it on besides `AI_AGENT_RECURSION_LIMIT`.
- No test covers consolidating repeated tool-call validation errors into one `agent_token_limit`
  message (A.6) — `on_tool_error` tests are strictly per-call.
- No test covers `_send_error_to_thread` populating `success`/`agent_error`/`error_details` (B.7) —
  existing tests around this method were not found (no `test_send_error_to_thread` hits).
- No test covers `_build_final_chunk` with a real (non-`None`) `agent_result` (B.8) — every existing
  stub sets `last_generation_result = None`.
- No test covers the `queue.Empty` timeout branch in `_stream_agent_path` sending a final chunk /
  calling `save_chat_history` (B.9) — behavior doesn't exist yet.
- No test covers `assistant_handlers.py`'s `generator_queue.queue.get()` with a timeout (B.10) —
  behavior doesn't exist yet.

---

## 5. Configuration and Environment

### Environment Variables

- No `os.environ`/`getenv` calls were found directly inside `langgraph_agent.py`, `coordinator.py`,
  `thread.py`, `hedged_handler.py`, `assistant_handlers.py`, or `agent_streaming_callback.py`. All
  configuration for this domain flows through the `Config`/`config` singleton
  (`codemie.configs.config`, `codemie.configs.Config`), which itself is presumably populated from
  environment variables elsewhere in `configs/` (not traced further — out of the named-file scope for
  this ticket).

### Configuration Files

- `src/codemie/configs/config.py` — single large `Config` class (pydantic-settings style, based on
  `int`/`bool`/`str` class-level annotated defaults). Relevant existing keys:
  `AI_AGENT_RECURSION_LIMIT: int = 150` (line 744), `AGENT_MSG_TOKEN_LIMIT: str = ...` (920),
  `AGENT_MSG_TIMEOUT`, `AGENT_MSG_BUDGET_EXCEEDED`, `AGENT_MSG_CALLBACK_FAILURE`,
  `AGENT_MSG_NETWORK_ERROR`, `AGENT_MSG_CONFIGURATION_ERROR`, `AGENT_MSG_FALLBACK` (919-928),
  `HIDE_AGENT_STREAMING_EXCEPTIONS: bool = False` (931), `CODEMIE_SUPPORT`/`CODEMIE_SUPPORT_MSG`
  (639-640). No `MAX_CONSECUTIVE_TOOL_ERRORS`-style key exists yet anywhere in this file (grepped for
  `MAX_CONSECUTIVE`/`consecutive` — no hits).
- `src/codemie/core/error_constants.py` — `ErrorCode` enum including `AGENT_TOKEN_LIMIT = "agent_token_limit"`
  (line 81) and `AGENT_ERROR_FRIENDLY_MESSAGES` mapping `ErrorCode -> config.AGENT_MSG_*` (line 286 for
  the token-limit entry).

### Feature Flags and Deployment Concerns

- `HIDE_AGENT_STREAMING_EXCEPTIONS` (config.py:931) is the one existing feature-flag-like toggle in
  this domain: when true, `_process_chunks` (langgraph_agent.py:794-814) replaces the real
  generated/error text with `cfg.CUSTOM_GUARDRAILS_MESSAGE`/`cfg.CUSTOM_STACKTRACE_MESSAGE`. Any new
  user-facing message change (A.6/B.7) should be checked against this flag's branching in
  `_process_chunks` to avoid being silently overridden.
- No Dockerfile/CI/CD/deployment-manifest references to token-limit handling, recursion limits, or
  graph-cancellation were found in this pass (not deeply searched — out of the ticket's named-file
  scope; `Dockerfile`/`deploy-templates/` exist at repo root but were not inspected for
  feature-area-specific content).
- No secrets/vault references are relevant to this bug — it is purely an in-process
  execution-lifecycle/error-handling gap, not a credentials or external-service configuration issue.

---

## 6. Risk Indicators

- **A.2/A.3 depend on LangGraph/`langgraph` 1.1.6 internals for actual task cancellation.**
  Speculative: closing the Python generator returned by `agent_executor.stream(...)` (e.g., via
  `stream.close()` in a `finally`, mirroring the existing normal-cancellation branch) may or may not
  actually terminate in-flight async tasks spawned for parallel `Send` branches — LangGraph's
  `pregel`/executor internals determine whether `GeneratorExit`/`.close()` propagates as real task
  cancellation. This needs verification against the installed `langgraph==1.1.6` version's actual
  cancellation semantics before assuming `stream.close()` alone satisfies A.3; it may need additional
  LangGraph-specific cancellation APIs (e.g., checkpointer/thread interrupt, or driving the executor
  with an explicit cancellation token) not identified in this filesystem pass.
- **The OTEL span-demotion-before-close ordering** (`_stream_graph` lines 909-915) is a subtle,
  already-fragile sequencing requirement; any exception-path fix (A.2) that adds a second
  `stream.close()` call site must preserve or explicitly re-derive this ordering, or tracing will
  silently regress (spans won't demote correctly, appearing as unresolved ERROR states in Langfuse).
- **`AgentErrorClassifier._try_token_limit` is substring-based, not type-based.** A.5's "cap on
  consecutive tool validation errors" will raise some new exception/message; for A.6's
  "consolidate under agent_token_limit" to work through the existing classifier chain, whatever new
  exception is raised must produce a message containing one of `"max_output_tokens"`, `"truncat"`,
  `"token limit"`, or `"token limit exceeded"` (case-insensitive) — otherwise it will silently fall
  through to a different `_try_*` classifier or `InternalErrorClassifier`'s generic fallback, defeating
  the consolidation goal. Alternatively, `_try_token_limit` itself would need direct `isinstance`
  awareness of the new exception/counter-exceeded state, which is a change to `core/errors.py`.
- **No dedicated tests exist for `SupervisorCoordinator`'s handoff tracker** (`active`/`pending`/
  `run_bindings` dicts) — A.3's "make sure SupervisorCoordinator doesn't leave dangling active
  handoffs" requires reasoning about `_SupervisorHandoffTracker.active` cleanup with zero existing
  test scaffolding to build on or regression-guard against; this raises the risk of an untested,
  hard-to-verify concurrency/bookkeeping fix.
- **`_build_final_chunk`/`agent.last_generation_result` is confirmed fully dead in production** (only
  test mocks set it) — B.8's "fix or remove" choice has a real behavioral fork: removing the dead path
  changes nothing observable today, while "fixing" it (making `LangGraphAgent` actually set
  `last_generation_result`) is a materially larger change that touches `stream()`/`_agent_streaming`'s
  success/failure bookkeeping and would need its own new test coverage from scratch.
  Speculative: fixing it likely requires assigning `self.last_generation_result` somewhere in
  `stream()`'s try/except/finally, e.g., alongside `_send_error_to_thread` and the happy-path
  `thread_generator.send(...)` call — but this is a design decision, not something observed in the
  current code.
  Speculative: this needs a decision (fix vs. remove) before implementation, since the two paths have
  very different blast radii; this analysis intentionally leaves that decision to the spec/plan stage.
- **`_stream_agent_path`'s 300-second `queue.Empty` timeout (B.9) and `assistant_handlers.py`'s
  unbounded `queue.get()` (B.10)** are two independent consumer loops with different current failure
  behavior (silent break with no history save vs. unbounded block) — a fix must account for both
  call sites independently; there is no shared consumer-loop helper between
  `HedgedAssistantHandler._stream_agent_path` and `StandardAssistantHandler._serve_data` to change
  once and get both for free.
- **`ThreadedGenerator.send()` is used by many callers beyond the agent's own error path**
  (`_send_thought` in `agent_streaming_callback.py`, likely others not traced in this pass) — A.4's
  `is_closed()` guard in `send()` must not silently swallow legitimate in-flight sends from callbacks
  that haven't yet observed a `close()` race; the exact semantics of "guard against enqueueing after
  is_closed()" (log-and-drop vs. raise vs. no-op) will affect multiple call sites project-wide, not
  just the token-limit scenario this ticket is about.
- **No env-var or feature-flag currently exists for any of A.1-A.6/B.7-B.10** — every one of these
  items is new configuration surface (e.g., a consecutive-tool-error cap needs a new `AI_AGENT_*`
  config key) with no precedent in this exact shape to copy beyond `AI_AGENT_RECURSION_LIMIT`'s
  int-default pattern.

---

## 7. Summary for Complexity Assessment

This ticket touches five architectural layers in `codemie/`: the agent runtime
(`LangGraphAgent._stream_graph`, `stream()`, `_send_error_to_thread`, `_configure_tools` in
`langgraph_agent.py`), supervisor coordination (`SupervisorCoordinator`/`_SupervisorHandoffTracker` in
`coordinator.py`), the streaming thread-bridge (`ThreadedGenerator` in `thread.py`), two independent
REST consumer loops (`hedged_handler.py`'s `_stream_agent_path` and `assistant_handlers.py`'s
`_serve_data`), and the streaming callback layer (`agent_streaming_callback.py`'s `on_tool_error`). All
ten scoped sub-items (A.1-A.6, B.7-B.10) were independently re-confirmed against current HEAD; line
numbers drifted only slightly from the prior investigation and no location claim was found to be wrong,
though two file-path claims in the ticket (`src/codemie/errors.py`, `src/codemie/config.py`) resolve to
`src/codemie/core/errors.py` and `src/codemie/configs/config.py` respectively.

Technical novelty is concentrated in A.2/A.3: actually cancelling LangGraph-internal execution
(react-loop iterations, `Send`-dispatched parallel branches) has no existing precedent in this codebase
— the only current graph-termination mechanism is the passive `recursion_limit=150` backstop, and
closing the Python `stream` generator's cooperative-cancellation behavior against real
task-cancellation in `langgraph==1.1.6` is unverified from source alone. The remaining items (A.4, B.7,
B.8, B.9, B.10) are comparatively mechanical wiring changes against fields/hooks that already exist
(`StreamedGenerationResult.success`/`agent_error`/`error_details`, `ThreadedGenerator.is_closed()`,
`AgentErrorClassifier.classify()`), while A.5/A.6 require new configuration surface and a
substring-matching classifier chain (`AgentErrorClassifier._try_token_limit`) that will need either a
carefully-worded new exception message or a direct code change to classify correctly.

Test coverage is a significant gap across nearly every touched surface: `_stream_graph`'s exception
path, graph/Send-branch cancellation, `SupervisorCoordinator`'s dangling-handoff scenario,
`ThreadedGenerator.send()`-after-close, the consecutive-tool-error cap, error consolidation, and both
consumer-loop timeout fixes are all currently untested (only truncation *detection* — not the
downstream stop-the-graph behavior — has thorough tests, in `test_langgraph_truncation.py`). Combined
with the LangGraph-internals uncertainty in A.2/A.3 and the two independent, non-shared consumer loops
in B.9/B.10, this points to a change with real cross-layer coordination risk despite several sub-items
being individually small.

---

## 8. External References

None named by the task. The task's file/line citations (e.g.,
`src/codemie/agents/langgraph_agent.py`, `src/codemie/agents/supervisor/coordinator.py`,
`src/codemie/errors.py`, `src/codemie/config.py`, `core/thread.py`, `hedged_handler.py`,
`assistant_handlers.py`, `agent_streaming_callback.py`, `chains/base.py`) are all files inside the
repository under research (`/Users/viktarpapou/EPAM/EPM-SDAI/codemie/`), not external sources — they
are covered in full under Section 2 above, including the two path corrections noted there
(`src/codemie/errors.py` -> `src/codemie/core/errors.py`; `src/codemie/config.py` ->
`src/codemie/configs/config.py`).
