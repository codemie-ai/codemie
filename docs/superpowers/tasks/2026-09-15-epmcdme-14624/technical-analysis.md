# Technical Research

**Task**: chat conversation thoughts tool-call persistence
**Generated**: 2026-09-15T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Jira ticket EPMCDME-14624 (Bug): "Tool call shows interrupted status after page reload"

Summary: Tool calls that completed successfully during a live chat session are displayed as
interrupted after the user reloads the page and returns to the same conversation.

Description: During a live chat session, completed tool calls are displayed correctly with their
result and success status. After the page is reloaded, the UI reads the conversation history from
the database and renders the same tool call with an interrupted or stale state.

Root cause hypothesis: intermediate "Codemie Thoughts" streaming entries are persisted to the
conversation document with `in_progress: true`, and this flag is not cleared when the assistant
response completes. When historical conversations are rendered, the UI interprets `in_progress: true`
on a thought as an interrupted state.

Preconditions:
- User is authenticated in CodeMie.
- User has access to any assistant that can trigger a tool call, such as a knowledge-base search.
- Conversation history persistence is enabled.
- The assistant response completes successfully during the live chat session.

Steps to Reproduce:
1. Open any assistant.
2. Ask a question that triggers a tool call, e.g. a knowledge-base search.
3. Wait for the assistant response to complete.
4. Confirm the tool call shows its result with a success status during the live session.
5. Reload the page (F5).
6. Navigate back to the same conversation.
7. Observe the rendered tool call state in the conversation history.

Expected Result:
- All thoughts in a completed conversation are persisted with `in_progress: false`.
- Successfully completed tool calls remain displayed with their actual completion status after reload.
- Historical conversation rendering does not mark completed tool calls as interrupted.
- The UI reflects the real persisted tool-call lifecycle and does not infer interruption from stale
  intermediate thought state.

Actual Result:
- An intermediate thought entry remains persisted with `in_progress: true`.
- After page reload, the UI reads this persisted value from conversation history.
- The historical tool call is rendered as interrupted, even though it completed successfully during
  the live session.

Evidence:
Endpoint: GET http://localhost:5173/api/v1/conversations/d8b9d84a-5654-4f69-a8ce-e565de2b703a
Relevant response excerpt shows an intermediate thought entry with:
  thought id: 02d7131a-ac2e-4f57-bd14-335c643ac9a8
  in_progress: true
This value should be `false` because the response completed successfully. The thoughts before and
after this entry both have `in_progress: false`, confirming the lifecycle was not finalized for the
intermediate entry.

Affected Areas:
- Conversation history persistence
- "Codemie Thoughts" streaming lifecycle finalization
- Tool-call status persistence
- Chat history rendering after page reload
- UI interpretation of `in_progress`, `interrupted`, and tool-call completion state

Acceptance Criteria:
1. All thoughts belonging to a completed assistant response are persisted with `in_progress: false`.
2. Intermediate "Codemie Thoughts" entries are finalized when the response completes successfully.
3. Reloading the page and reopening the same conversation does not change a successfully completed
   tool call into an interrupted state.
4. Historical conversation rendering uses the actual persisted completion/error/interruption state
   and does not show stale `in_progress: true` entries as interrupted when the response completed.
5. The provided reproduction scenario is fixed and verified with a tool call, such as a knowledge-base
   search.
6. Regression testing confirms that genuinely interrupted or failed tool calls are still rendered
   with the correct interrupted or error status.
7. Existing live streaming behavior during an active response is not regressed.

---

## 2. Codebase Findings

### Existing Implementations

**Thought data model**
- `src/codemie/chains/base.py:39-58` — `Thought` (Pydantic `BaseModel`): `id`, `parent_id`, `metadata`,
  `in_progress: bool = False`, `input_text`, `message`, `author_type`, `author_name`, `output_format`,
  `error`, `interrupted`, `aborted`, `children`, `routing`. This is the canonical shape persisted into
  `GeneratedMessage.thoughts`.

**Live streaming accumulation (in-memory, per-request)**
- `src/codemie/core/thread.py` — `ThreadedGenerator` (used by the standard/non-hedged assistant path).
  `send()` parses each streamed NDJSON chunk, forwards it to the client queue, and also calls
  `_process_thought()`, which merges the incoming thought into `self.thoughts` (a plain list of
  dicts, keyed by `thought['id']`). For every incoming chunk, `existing_thought['in_progress'] =
  in_progress` is set directly from whatever `in_progress` value the chunk carried — there is no
  finalization step that forces it `False` once the overall response is done; the field simply
  reflects the *last chunk seen* for that thought id. This same list, `generator_queue.thoughts`, is
  what later gets persisted as chat history.
- `src/codemie/core/thought_queue.py` — `ThoughtQueue`/`ThoughtQueueItem`/`ThoughtContext`, the
  parallel structure used by workflow execution (not the plain assistant-chat path).

**Callback that produces thought lifecycle events**
- `src/codemie/agents/callbacks/agent_streaming_callback.py` — `AgentStreamingCallback` (langchain
  `StreamingStdOutCallbackHandler`). Key lifecycle:
  - `on_llm_start` → `ThoughtInMemoryStorage.create_thought(..., tool_name="CodeMie Thoughts",
    in_progress=True)`, sent immediately (`_send_thought`). This is the exact author name
    (`GENERIC_TOOL_NAME = "CodeMie Thoughts"`) named in the ticket.
  - `on_llm_new_token` → appends token text, does not touch `in_progress` (thought stays `True`
    until finalized).
  - `on_llm_end` (`agent_streaming_callback.py:198-252`) → sets `in_progress=False` and sends, BUT
    only deletes the thought from in-memory storage if `model_resolved or isinstance(response, str)
    or already_finalized` (line 251). If none of those hold (routing metadata never resolves), the
    Thought instance is kept in storage "for a later call" that may never arrive; if a redundant
    `on_llm_end` call *does* arrive later with no new routing info, `already_finalized` short-circuits
    and returns **without calling `_send_thought` again** (lines 219, 232-234) — i.e. the only sent
    finalize is the first one. Whether or not the very last event for a given `run_id`'s thought
    actually carries `in_progress: false` depends on this callback correctly firing `on_llm_end`
    (or `on_llm_error`) for every `on_llm_start`; there is no independent "force-finalize on response
    completion" safety net elsewhere in this callback.
  - `on_tool_start`/`on_tool_end`/`on_tool_error` follow the same create → in_progress=True →
    update/in_progress=False → delete-from-storage pattern for genuine tool-call thoughts.
- A closely related, already-fixed defect in the same callback is documented at
  `docs/superpowers/tasks/2026-09-09-workflow-thought-persistence-fix/spec.md` (EPMCDME-14850): a
  double `on_llm_end` invocation (from `LangGraphEventAdapter` calling it once each from the
  `"messages"` and `"updates"` stream modes) used to re-send a finalized thought and crash the
  workflow `ThoughtConsumer` with an `IntegrityError` on duplicate insert. That fix made a *second*
  `on_llm_end` call for an already-finalized thought a safe no-op (skip send, always delete from
  storage). It did not touch the plain assistant-chat persistence path below, and does not add any
  "sweep and force `in_progress=False`" step at response-completion time.

**Where the accumulated thoughts get persisted (this is where the ticket's stale flag survives)**
- `src/codemie/rest_api/handlers/assistant_handlers.py`:
  - `_serve_data` (lines 743-793): the standard streaming response loop. On the terminal
    `StopIteration` from the generator queue (normal, successful completion — the ticket's exact
    scenario), it calls `self.save_chat_history(ChatHistoryData(..., thoughts=generator_queue.thoughts,
    ...))` directly, with **no finalization pass** over `generator_queue.thoughts` before persisting.
  - `_save_history_for_disconnect` (lines 664-696): the *genuine* client-disconnect/interrupt path —
    persists the same `threaded_generator.thoughts` list but with `status=ConversationStatus.INTERRUPTED`.
  - `save_chat_history` (line 410) → `ConversationService.upsert_chat_history(..., thoughts=
    self._filter_thoughts(data.thoughts), ...)`.
  - `_filter_thoughts` (lines 468-515) has **two branches gated by
    `_is_conversation_replay_v2_enabled()`** (a `DynamicConfigService` flag, default `True` — see
    Configuration section):
    - **Flag disabled (legacy/v1) branch** (lines 469-487): rebuilds each `Thought` WITHOUT passing
      `in_progress` at all, so every persisted thought gets the Pydantic default `in_progress=False`
      — this branch cannot exhibit the bug, because it silently drops whatever value was accumulated.
    - **Flag enabled (v2, default-on) branch** (lines 489-515): rebuilds each `Thought` with
      `in_progress=thought.get('in_progress', False)` — i.e. it **carries the raw accumulated value
      through verbatim**, including a stale `True` left over from an intermediate "CodeMie Thoughts"
      entry whose finalize event never updated `generator_queue.thoughts` (or was lost/raced). This is
      the most direct candidate location matching the ticket's evidence: a `GET
      /api/v1/conversations/{id}` response with one intermediate thought `in_progress: true`
      surrounded by thoughts correctly `in_progress: false`.
- `src/codemie/rest_api/handlers/hedged_handler.py` (lines ~400-425): the hedged-request coordinator
  has the same shape — on normal completion it calls `self.save_chat_history(ChatHistoryData(...,
  agent_queue.thoughts, ...))` with no finalization step either, reusing the same
  `save_chat_history`/`_filter_thoughts` pipeline described above.

**A parallel, already-defensive pattern exists for workflows**
- `src/codemie/service/workflow_execution/workflow_execution_service.py`:
  - `_update_assistant_response_in_history` (lines ~500-552) sets `assistant_message.thoughts =
    self._get_thoughts_from_states()` when persisting the final workflow chat turn.
  - `_get_thoughts_from_states` (lines ~650-680) **hardcodes `"in_progress": False`** unconditionally
    for every thought dict it builds when materializing a completed workflow turn's history — i.e.
    the workflow path already has a deliberate "force finalize on completion" step; the plain
    assistant-chat path (`assistant_handlers.py`) does not.

### Architecture and Layers Affected

- **API / router layer**: `src/codemie/rest_api/routers/conversation.py` — `GET
  /conversations/{conversation_id}` (`get_conversation_by_id`, line ~190) is the exact endpoint named
  in the ticket's evidence; it serves the persisted `Conversation.history` (list of `GeneratedMessage`,
  each with a `thoughts: Thought[]` field).
- **Request handler layer**: `src/codemie/rest_api/handlers/assistant_handlers.py`
  (`AssistantRequestHandler`, `StandardAssistantHandler`) and `src/codemie/rest_api/handlers/
  hedged_handler.py` — own the streaming response loop and the exact moment history is persisted.
- **Agent callback layer**: `src/codemie/agents/callbacks/agent_streaming_callback.py`
  (`AgentStreamingCallback`, `ThoughtInMemoryStorage`) — produces every thought lifecycle event
  (`in_progress: True` → `False`) that flows into the generator.
- **Core accumulation layer**: `src/codemie/core/thread.py` (`ThreadedGenerator._process_thought`) —
  merges streamed thought events into the list that becomes persisted history.
- **Service layer**: `src/codemie/service/conversation_service.py` (`ConversationService
  .upsert_chat_history`) — persists `GeneratedMessage` into `Conversation.history` (MongoDB-backed
  document, based on `Conversation.find_by_id` / `to_chat_history` patterns referenced in tests).
- **History projection/replay layer** (feeds thoughts back to the LLM on the *next* turn, not the
  raw GET response): `src/codemie/service/conversation/history_projection_service.py` —
  `_normalize_status` (lines 513-520) explicitly maps `in_progress: True` (or `status ==
  TOOL_STATUS_RUNNING`) to `TOOL_STATUS_INTERRUPTED` for tool-replay records built from persisted
  thoughts, but only for thoughts classified as *replayable tools* by `_is_replayable_tool` (line 220)
  — generic `"CodeMie Thoughts"` entries are explicitly excluded from this classification
  (`GENERIC_THOUGHT_NAMES`/`NORMALIZED_GENERIC_THOUGHT_NAMES`, lines 42-43, checked at line 222). This
  confirms the backend itself already treats a persisted `in_progress: True` tool thought as
  "interrupted" when re-projecting history for the model — the same semantic the ticket says the UI
  is applying when rendering history for the human.

### Integration Points

- No third-party/external service SDK is directly implicated; the affected code is internal
  streaming/persistence plumbing shared by every assistant chat turn, independent of which tool ran
  (knowledge-base search is just the ticket's reproduction vehicle).
- `src/codemie/service/dynamic_config_service.py` (`DynamicConfigService.get_bool_value_safe`) gates
  the `_filter_thoughts` branch split via `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY` — a runtime
  feature flag, not a static env var, so the active code path can differ per environment/tenant
  without a redeploy.
- The same `AgentStreamingCallback`/`ThreadedGenerator` combination is reused across
  `assistant_handlers.py` (standard) and `hedged_handler.py` (hedged fast-path), so a fix confined to
  one handler alone would not cover the other.

### Patterns and Conventions

- **Finalize-on-persist pattern (already established elsewhere, absent here)**: workflow history
  persistence (`workflow_execution_service._get_thoughts_from_states`) unconditionally sets
  `"in_progress": False` when building the final, completed-turn view of thoughts; the plain
  assistant-chat persistence path has no equivalent step.
- **Idempotent-finalize pattern for the callback layer**: the EPMCDME-14850 fix documented in
  `docs/superpowers/tasks/2026-09-09-workflow-thought-persistence-fix/spec.md` established the
  precedent of making a redundant `on_llm_end` call for an already-finalized thought a safe no-op
  rather than re-sending/re-persisting — a template for any similar idempotency fix in this area.
  Follow-up risk explicitly flagged there and left unaddressed: "If ... only a single stream-mode
  chunk ever arrives for a turn ... and that one call never resolves the model, the thought is sent
  once and retained" — an in-memory leak, not a crash, but consistent with a thought's last-sent state
  possibly staying at whatever value was true when the single call fired.
- **Nested/children thoughts**: `thread.py._nest_to_thought`/`_nest_to_latest_thought` maintain
  `in_progress` on nested child dicts inside a parent's `children` list — any finalize step added at
  persistence time would need to recurse into `children`, not just the top-level thought list, to be
  complete.
- **Feature-flagged behavior split**: `_filter_thoughts`'s two code paths (replay-v2 flag) is the
  established pattern for staged rollout in this handler; any fix must consider whether it needs to
  apply to both branches or only the v2 (default-on) branch that actually preserves `in_progress`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No `.ai-run/guides/` entry is dedicated to thought/`in_progress` lifecycle specifically. Closest
guides: `.ai-run/guides/agents/langchain-agent-patterns.md` (callback runtime patterns) and
`.ai-run/guides/development/error-handling.md` (referenced by the related workflow-thought fix for
its "contextual, not silent" logging convention). Neither was found to define the correct
finalization contract for `in_progress`; conventions below are derived from code and from the two
directly relevant prior task docs found under `docs/superpowers/tasks/`.

### Architectural Decisions

- `docs/superpowers/tasks/2026-09-09-workflow-thought-persistence-fix/spec.md` (EPMCDME-14850) —
  directly relevant prior fix in the same callback (`agent_streaming_callback.py.on_llm_end`) for a
  different symptom (duplicate-insert crash in the *workflow* `ThoughtConsumer`, not the plain-chat
  `in_progress` staleness this ticket reports). Its "Open Risk (accepted)" section explicitly flags
  that a thought can be sent once and left in a state that a later call was supposed to finish, with
  no follow-up. Read closely before touching `on_llm_end` again, to avoid reintroducing the
  IntegrityError it fixed.
- `docs/superpowers/tasks/2026-08-14-epmcdme-14075-workflow-progress-hydrate/frontend-handoff.md`
  (EPMCDME-14075) — establishes the documented contract for `in_progress` on **workflow** thoughts:
  `in_progress: true` intentionally means "this step is still running" (a live-progress signal the
  UI polls for), and is explicitly NOT equivalent to "interrupted" — `interrupted` is a distinct
  field. This confirms `in_progress: true` is only a legitimate signal while a run is genuinely
  ongoing; once a turn is complete there is no code path that is supposed to leave any thought with
  `in_progress: true` still set, for either workflows or plain chat.
- `src/codemie/service/conversation/history_projection_service.py._normalize_status` — an
  un-commented but load-bearing decision: on the history-projection/replay side, the backend itself
  already collapses "persisted `in_progress: True`" into `TOOL_STATUS_INTERRUPTED` for replayable
  tool thoughts. No inline comment explains why, but it corroborates that a stale `in_progress: True`
  is broadly treated as "interrupted" throughout this codebase, not just by the (external) UI.

### Derived Conventions

- A thought is only ever supposed to be `in_progress: True` momentarily, between its "start" event and
  its "end/error" event, for the duration of a single live turn.
- Once a turn's response is fully generated (successfully or not) and is about to be persisted as
  history, every thought belonging to that turn — including nested `children` — should read
  `in_progress: False`, `interrupted`/`error`/`aborted` reflecting the true terminal state. The
  workflow path already enforces this explicitly at the point of persistence; the plain-chat path
  (`assistant_handlers.py`/`hedged_handler.py`) currently persists whatever the accumulator last held.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/core/test_thread.py` — only 6 tests (`test_init`, `test_iter`, `test_next`,
  `test_send`, `test_is_closed`, `test_close`) covering `ThreadedGenerator`'s queue mechanics; no test
  exercises `_process_thought`'s merge behavior, in particular no test asserts what happens to
  `self.thoughts[...]['in_progress']` when a thought's finalize event is missing or delayed.
- `tests/codemie/agents/callbacks/test_agent_streaming_callback.py` — has multiple assertions that
  `mock_thought.in_progress is False` after `on_tool_end`/`on_llm_end`/`on_tool_error` (lines ~152,
  203, 275, 327, 359), and a comment at line ~490 acknowledging the "thought is finalized (in_progress
  False) but kept in storage (model_resolved ...)" branch. These tests cover the callback's own
  in-memory `Thought` object correctness, not what ultimately lands in
  `ThreadedGenerator.thoughts`/persisted history.
- `tests/codemie/rest_api/handlers/test_assistant_handlers.py`:
  - `test_filter_thoughts_drops_replay_only_entries_when_feature_flag_disabled` (line 722) — exercises
    the v1 (flag-disabled) branch; input thoughts have no `in_progress` key at all.
  - `test_filter_thoughts_preserves_parent_id_when_feature_flag_enabled` (line 757) — exercises the v2
    (flag-enabled) branch, but the single input thought is given `"in_progress": False` — there is no
    test asserting what `_filter_thoughts` does when an input thought carries `"in_progress": True`
    into a completed/successful save. This is the precise gap matching the ticket.
- `tests/codemie/rest_api/handlers/test_assistant_handlers_streaming.py` — has a disconnect-path test
  around "Agent has been interrupted by client" (line ~463) covering `_save_history_for_disconnect`'s
  security-error guard, not the normal-completion `_serve_data` StopIteration branch's thought
  finalization.
- `docs/superpowers/tasks/2026-09-09-workflow-thought-persistence-fix/spec.md` lists tests added for
  the *workflow* `ThoughtConsumer` duplicate-id scenario — a different persistence path
  (`WorkflowExecutionStateThought` SQL rows) from this ticket's `Conversation.history` (Mongo-style
  document) path.

### Testing Framework and Patterns

- `pytest` (`^8.3.1`) with `pytest-asyncio`, `pytest-mock`, `pytest-cov`, `pytest-env`, `pytest-httpx`
  (from `pyproject.toml`).
- Mocking pattern: `unittest.mock.Mock`/`MagicMock` with `spec=` against the real class (e.g. `Mock(
  spec=AssistantChatRequest)` in `test_get_thoughts_from_callback.py`).
  `test_agent_streaming_callback.py` references a "real_thought_queue" fixture pattern (per the
  EPMCDME-14850 spec) for integration-style tests wiring a real queue through a real consumer.
- Feature-flag tests patch `DynamicConfigService.get_typed_value`/`get_bool_value_safe` directly
  rather than setting environment variables.

### Coverage Gaps

- No test asserts end-to-end that a successfully completed `_serve_data`/hedged-coordinator turn
  persists every thought (including any lingering `"CodeMie Thoughts"` generic entry) with
  `in_progress: False` when the raw accumulator held a stale `True`.
- No test covers `ThreadedGenerator._process_thought`'s behavior when a thought's *last* incoming
  event has `in_progress: True` and no further event ever arrives for that id (the scenario this
  ticket's evidence describes).
- No test covers nested `children` thoughts retaining a stale `in_progress: True` at persistence
  time.
- No test exists at the `AssistantRequestHandler.save_chat_history`/`_filter_thoughts` boundary for
  the "response completed successfully but an intermediate thought never got its finalize event"
  case specifically (only the "was already False" and "key absent" cases are covered today).

---

## 5. Configuration and Environment

### Environment Variables

No feature-area-specific static environment variable was found; the relevant toggle is a runtime
dynamic config value (see Feature Flags below), not a process environment variable.

### Configuration Files

- `src/codemie/configs/config.py:746` — `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED: bool = True`
  (static fallback default consumed by the dynamic-config lookups below).
- `src/codemie/service/constants.py:49` — `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY =
  "AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED"`.

### Feature Flags and Deployment Concerns

- `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED` — read via `DynamicConfigService.get_bool_value_safe`/
  `get_typed_value` in five places: `assistant_handlers.py:518-521` (gates `_filter_thoughts`'s
  in_progress-preserving branch, default `True`), `assistant_agent.py:147-148`,
  `callback_utils.py:49-50`, `langgraph_agent.py:130-131`, and
  `history_compaction_service.py:36-37`. Because the default is `True`, the in_progress-preserving
  branch of `_filter_thoughts` is the one active out of the box — i.e. this bug is live in the
  default configuration, not an edge-case flag combination.
- No Dockerfile/CI/CD manifest reference to this flag or to thought persistence was found; this is a
  pure application-logic/runtime-config concern, not a deployment-time one.

---

## 6. Risk Indicators

- Speculative: the most direct fix location is a finalization step where `generator_queue.thoughts`
  (and any nested `children`) is swept to force `in_progress=False` immediately before
  `save_chat_history` is called on a successful completion (`assistant_handlers.py._serve_data`'s
  `StopIteration` branch and its `hedged_handler.py` counterpart) — mirroring the existing
  `workflow_execution_service._get_thoughts_from_states` convention — but confirming this is a design
  decision belonging to the spec/plan stage, not a discovered fact.
- Speculative: whether `_save_history_for_disconnect` (genuine interruption) should be left untouched
  is itself a design question — AC #6 requires genuinely interrupted/failed tool calls to keep showing
  as interrupted/error, so any finalize-on-success step must be scoped to the success path only, not
  applied to the disconnect/interrupt path.
- The fix must cover **two** near-duplicate handler code paths (`assistant_handlers.py._serve_data`
  and `hedged_handler.py`'s equivalent coordinator loop) that both call `save_chat_history` with a raw
  accumulator's `.thoughts`; missing either one leaves a partial fix.
- `_filter_thoughts` has two feature-flag branches; the v1 (flag-disabled) branch already
  incidentally "fixes" this by never passing `in_progress` through — a fix applied only inside
  `_filter_thoughts` itself must be careful not to depend on which branch is currently enabled, since
  the flag is a runtime toggle that can change per environment.
- `ThreadedGenerator._process_thought` (`core/thread.py`) has no unit test coverage of its merge
  logic at all; changes here carry above-average regression risk without new tests.
- The related, already-shipped EPMCDME-14850 fix in the exact same callback
  (`agent_streaming_callback.py.on_llm_end`) shows this specific finalize/idempotency logic has broken
  in non-obvious ways before (dual invocation, retained-vs-deleted storage state) — any further change
  to `on_llm_end` itself (as opposed to a downstream finalize sweep) should be read against that spec
  closely to avoid reopening the IntegrityError it fixed.
- `history_projection_service._normalize_status` independently treats persisted `in_progress: True`
  as `TOOL_STATUS_INTERRUPTED` for tool-replay purposes (feeding the *next* LLM turn's context) — a
  fix that only changes what gets persisted (not this projection function) is consistent with AC #4
  ("historical conversation rendering ... does not show stale in_progress: true entries as
  interrupted"), since correctly persisted data makes this function's behavior moot for this bug, but
  this function is a second place where "in_progress True → interrupted" logic already lives in this
  backend and is worth checking for consistency once the persistence fix is designed.
- No existing test infrastructure exercises the full `AgentStreamingCallback` → `ThreadedGenerator` →
  `save_chat_history` chain end-to-end; a regression test for this ticket will likely need to be a new
  integration-style test similar to the "real_thought_queue" pattern referenced in the EPMCDME-14850
  spec, rather than an extension of an existing one.

---

## 7. Summary for Complexity Assessment

This bug sits at the boundary between three layers: the LangChain callback that emits per-token/
per-tool thought lifecycle events (`agent_streaming_callback.py`), the in-memory accumulator that
merges those events into a list for the duration of one request (`core/thread.py`'s
`ThreadedGenerator`), and the request-handler code that persists that accumulated list as
conversation history on completion (`assistant_handlers.py._serve_data` and its
`hedged_handler.py` counterpart, both funneling through `save_chat_history` →
`_filter_thoughts`). The concrete defect is a missing finalization step: nothing forces
`in_progress=False` across all accumulated (and nested) thoughts at the moment a turn is
persisted as successfully completed, so whatever value the last-seen event for a given thought
happened to carry — potentially a stale `True` from a generic "CodeMie Thoughts" LLM-streaming
entry whose `on_llm_end` never fired or updated the shared list — survives verbatim into the
database. The default-on `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED` flag makes this the live,
default-configuration behavior, since its disabled branch happens to mask the bug by dropping
`in_progress` entirely.

Technical novelty is low — the codebase already has a working precedent for exactly this
finalize-on-persist pattern in the workflow path (`workflow_execution_service
._get_thoughts_from_states` hardcodes `in_progress: False`), and a directly analogous prior fix
(EPMCDME-14850) in the very same callback establishes both the idiom and the pitfalls (duplicate
`on_llm_end` invocations, retained-vs-deleted in-memory storage) to avoid reintroducing. The main
complexity is surface area and care, not invention: at least two handler code paths
(`assistant_handlers.py`, `hedged_handler.py`) share the bug, nested `children` thoughts need the
same sweep, and the fix must be scoped strictly to the successful-completion path so genuinely
interrupted/failed tool calls (AC #6) are not masked.

Test coverage is a real gap: `ThreadedGenerator._process_thought`'s merge logic has zero existing
unit tests, the two `_filter_thoughts` tests both use inputs where `in_progress` is already `False`
or absent (never `True`), and no test exercises the full callback→accumulator→persistence chain for
a turn where an intermediate thought's finalize event never lands. Any fix will need new tests at
several of these layers rather than being able to extend an existing suite in place. Risk is
moderate: correctly identifying and touching every path that persists `generator_queue.thoughts`/
`agent_queue.thoughts` on success, without altering the already-fixed `on_llm_end` idempotency
behavior or the genuine-interruption/disconnect path, is the crux of getting this right.

---

## 8. External References

None named by the task. The ticket's "Evidence" section quotes a `GET
/api/v1/conversations/{id}` response excerpt inline (not a file/path reference), which was treated
as reproduction evidence during code research rather than an external source to read.
