# Technical Research

**Task**: supervisor history subagent handoff tool-calls
**Generated**: 2026-09-11
**Research path**: filesystem (codegraph unavailable)

---

## 1. Original Context

EPMCDME-14859: Assistant-to-sub-assistant delegation returns valid sub-assistant response but appends Azure/LiteLLM 400 error ('messages with role tool must be a response to a preceding message with tool_calls', param messages.[N].role). Root cause confirmed by prior investigation (see D:\Projects\EPMCDME-14859-recovery-notes.md): the supervisor's history compactor in src/codemie/agents/supervisor/history.py (_strip_handoff_back_messages_pre_model_hook / _consume_pending_handoff_message) mistakes a sub-agent's intermediate AIMessage(tool_calls=[...]) for the sub-agent's final handoff answer. It removes/consumes that assistant tool-call declaration as the transfer_to_<subagent> result while leaving the matching ToolMessage in history, producing an orphaned role='tool' message that Azure/OpenAI rejects. Direct sub-assistant invocation bypasses this compactor and does not reproduce. There is also a secondary presentation defect: LangGraphAgent's streaming chunks_collector appends the raw exception text to an already-successful sub-agent answer when the later supervisor model call then fails. Fix must be at the source (history/protocol correctness): a sub-agent AIMessage with tool_calls must never be treated as the terminal handoff result; only consume the terminal AIMessage as the handoff answer, and any compaction of the nested tool-call/tool-result pair must be atomic (both members together, never split into an orphan). Two candidate designs are documented in the recovery notes (preserve nested pair in supervisor-visible history vs. atomically drop both members). Must keep current main's existing supervisor behaviors compatible: parallel supervisor handoffs, sanitize_rich_history_for_llm, task-message filtering, and the '[no response]' incomplete-handoff placeholders (EPMCDME-13657 was reverted on main - do not resurrect it wholesale). A broader historical fix series lives on origin/EPMCDME-13889_soften-subagent-constraint - reference-only, do not cherry-pick wholesale.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/agents/supervisor/history.py` — supervisor history compaction/reconstruction hooks; contains the bug's root cause. Key functions: `_strip_handoff_back_messages_pre_model_hook`, `_consume_pending_handoff_message`, `_queue_pending_handoff_message`, `_append_pending_handoffs`, `_subagent_task_pre_model_hook`, `_strip_subagent_task_messages_pre_model_hook`.
- `src/codemie/agents/agent_runtime_utils.py` — `sanitize_rich_history_for_llm` and `_RichHistoryToolReplaySanitizer` (the generic orphan-tool-call sanitizer used by `history.py` and `langgraph_agent.py`); also `filter_history`/`transform_history`.
- `src/codemie/agents/langgraph_agent.py` — `LangGraphAgent`: builds supervisor/single agent graphs, wires pre_model_hooks (`_build_supervisor_agent` ~289, `_build_single_agent` ~307), streaming loop (`_stream_graph` ~854-931), `chunks_collector` construction (~749) and exception handling (`_send_error_to_thread` ~816-838), handoff-tool message construction (`_resolve_handoff_messages` ~407-425, calls `sanitize_rich_history_for_llm` on the prefix only, not the final AIMessage).
- `src/codemie/agents/langgraph_event_adapter.py` — `LangGraphEventAdapter`/`LangGraphCallbackBridge`: consumes LangGraph stream chunks and appends streamed tokens into `chunks_collector` via `process_agent_streaming` → `LangGraphAgent.process_output` (`langgraph_agent.py:1474`).
- `src/codemie/agents/supervisor/pre_model_hooks.py` — `_image_artifact_pre_model_hook` and `_compose_pre_model_hooks` (generic hook-composition helper; merges `llm_input_messages` sequentially).
- `src/codemie/agents/supervisor/bootstrap.py` — LangGraph-supervisor compatibility monkeypatches. Does **not** currently patch `_make_call_agent` (that patch exists only on the reference branch).
- `src/codemie/agents/supervisor/coordinator.py` — `SupervisorCoordinator`/`_SupervisorHandoffTracker`: tracks pending/active handoffs by tool-call id and author name for UI thought emission. Operates on the same author-name matching assumption as `history.py`'s `_consume_pending_handoff_message`.
- `src/codemie/agents/supervisor/constants.py` — `METADATA_KEY_HANDOFF_DESTINATION`, `METADATA_KEY_HANDOFF_BACK`, `METADATA_KEY_PARALLEL_SUBAGENT_PARENT_HANDOFF`, `METADATA_KEY_SUBAGENT_TASK`.
- `tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py` — 603 lines; primary regression suite for the compactor/handoff-tool logic.

### Architecture and Layers Affected

Agent-runtime layer (`LangGraphAgent`) → event-adapter layer (`LangGraphEventAdapter`/`LangGraphCallbackBridge`) → supervisor sub-layer (`bootstrap.py`, `coordinator.py`, `history.py`, `pre_model_hooks.py`) → shared runtime utils (`agent_runtime_utils.py`). No DB/router layer touched — entirely in-process LangGraph message-history reconstruction and SSE streaming.

### Integration Points

- `codemie.agents.langgraph_agent` → `codemie.agents.supervisor.history` (imports the four pre_model_hook functions).
- `codemie.agents.langgraph_agent` → `codemie.agents.supervisor.bootstrap`, `coordinator`, `pre_model_hooks`.
- `codemie.agents.langgraph_agent` → `codemie.agents.langgraph_event_adapter`.
- `codemie.agents.supervisor.history` → `codemie.agents.agent_runtime_utils` (`sanitize_rich_history_for_llm`) → `supervisor.constants`, `core.constants`, `core.utils`.
- No circular deps: nothing in `history.py`/`agent_runtime_utils.py` imports from `langgraph_agent.py`.

### Patterns and Conventions

- **Pre-model-hook composition**: `_compose_pre_model_hooks(*hooks)` — each hook receives/returns partial state updates keyed by `llm_input_messages`; composed sequentially. Hooks return `{}` for no-op or `{"llm_input_messages": [...]}` for a rewrite. `filtered_messages == messages` is the standard no-op check throughout `history.py`.
- **Pending/consume queue pattern**: `_queue_pending_handoff_message` enqueues a pending handoff keyed by destination agent name; `_consume_pending_handoff_message` later matches an incoming `AIMessage` **by `message.name` only — no `tool_calls` check** — and treats it as the handoff's terminal answer. **This is the defect.**
- **Structural sanitizer** (`_RichHistoryToolReplaySanitizer` in `agent_runtime_utils.py`): walks a message list, holds a pending AI tool-call block until all its tool_call_ids are answered, drops incomplete blocks, drops orphan `ToolMessage`s with no matching pending call. Applied as a sweep at the *end* of `_strip_handoff_back_messages_pre_model_hook` (`history.py:238`) — i.e., *after* the buggy consumption logic already stripped the intermediate `AIMessage(tool_calls=...)`. It operates on the already-mangled list, so at best it silently drops the orphaned tool result too (losing the sub-agent's real answer content), which matches the "sanitize hides but doesn't fix" behavior the recovery notes flag.
- **`[no response]` placeholder** in `_append_pending_handoffs` (`history.py:191-213`) — distinct case: a handoff whose sub-agent never replied at all (not the mid-stream orphan bug).
- **Author-name-based attribution**: both `history.py._consume_pending_handoff_message` and `coordinator.py`'s `_SupervisorHandoffTracker` rely on `message.name` to attribute a reply to a pending handoff — fragile for nested supervisors (a sub-agent that is itself a supervisor answers under LangGraph's internal node name `"supervisor"`, not its own name). This broader gap is what the unmerged `EPMCDME-13889` branch targets; out of scope for this ticket's direct single-level repro but useful context.

---

## 3. Documentation Findings

### Guides and Architecture Docs

Per `AGENTS.md` routing table, applicable P0 guides: `.ai-run/guides/agents/langchain-agent-patterns.md` (Agents category) and `.ai-run/guides/workflows/langgraph-workflows.md` (Workflows category). Load both before implementation/spec.

### Architectural Decisions

- Git revert `1cd636dcc` ("Revert EPMCDME-13657: Tightening logic to avoid orphan tool calls...") confirmed via `git show`. It reverted a mixed commit touching both handoff-history sanitization *and* unrelated workspace-tool-list code. Specifically it reverted:
  1. `agent_runtime_utils.py`: `filter_history`'s legacy non-rich-history branch, removing a `sanitize_rich_history_for_llm` call.
  2. `langgraph_agent.py`: removed a `sanitize_rich_history_for_llm` call in the handoff-tool message builder.
  3. `history.py`: removed the sanitizer import; reverted `[no response]` placeholders back to `""`; reverted a tail-sanitization call in `_subagent_task_pre_model_hook`.
  4. The corresponding test assertion.

  **Current `main` has since independently re-added every one of these pieces** (re-added `sanitize_rich_history_for_llm` calls at `langgraph_agent.py:423-424` and `history.py:238`/`289`; re-added `[no response]` placeholders at `history.py:202,212`). The revert is stale/superseded relative to current `main` — do not treat it as "still reverted" or attempt to resurrect it; it targeted a different, now-diverged code state and mixed in unrelated workspace-tool changes.

- Reference-only branch `origin/EPMCDME-13889_soften-subagent-constraint` (7 commits, diverges 20 files / 1045+ / 56- from merge-base `6ff06d48b`) contains a broader unmerged nested-supervisor fix series:
  - Adds `NESTED_SUPERVISOR_NODE_NAME = "supervisor"` and `_resolve_pending_handoff_author()` in `history.py` — matches an incoming AIMessage to a pending handoff by name, falling back to unambiguous-single-outstanding-handoff resolution when the name is the generic `"supervisor"` node name.
  - Converts `_strip_handoff_back_messages_pre_model_hook`/`_subagent_task_pre_model_hook` into factory functions parameterized by the owning agent's own name (`_is_incoming_task_for`).
  - Adds `_close_unanswered_tool_calls()` — for every `AIMessage.tool_calls` entry with no matching `ToolMessage` anywhere in the list, inserts an empty `ToolMessage` placeholder **immediately after** the AIMessage that made the call (not at the end of the list). This directly targets the orphaned-tool-result shape from this ticket. Its docstring was explicitly narrowed in a follow-up commit to avoid over-claiming beyond the evidenced 400-error repro.
  - `bootstrap.py` adds `_patched_make_call_agent` + `_stamp_subagent_identity()` rewriting outgoing `AIMessage.name` from a subagent node to its registered name — fixes nested-supervisor author misattribution (out of scope here, but the underlying principle "an AIMessage with tool_calls is never a terminal handoff answer" is directly relevant reference material).
  - Also touches `coordinator.py`, `core/thread.py`, `service/tools/assistant_factory.py`; adds `test_thought_nesting.py`, extends `test_langgraph_multi_assistant_handoffs.py` (+156 lines), adds `test_langgraph_multi_assistant_supervisor.py`, `test_supervisor_bootstrap.py`. None merged to `main`. Per recovery notes: do not cherry-pick wholesale — port only what a failing test on current main demonstrates necessary.

### Derived Conventions

Pre_model_hooks return `{}` for no-op or `{"llm_input_messages": [...]}` for a rewrite; hook composition merges via `_compose_pre_model_hooks`. No fixtures/mocks/factories in the relevant test file — pure unit tests constructing `AIMessage`/`ToolMessage`/`HumanMessage` literally and calling the hook function directly with `{"messages": [...]}`, asserting on `result["llm_input_messages"]` or `result == {}`.

---

## 4. Testing Landscape

### Existing Coverage

`tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py` (603 lines) covers:
- `_create_custom_handoff_tool` clean single/parallel subagent context construction (36-149).
- `_subagent_task_pre_model_hook` scoping, including preserving follow-up child tool-call/result pairs (151-215).
- `_strip_subagent_task_messages_pre_model_hook` (217-228).
- `_strip_handoff_back_messages_pre_model_hook` — the function under investigation — across: plain handoff-back stripping (230-238), parallel-parent-handoff hiding when resolved (240-274) and when pending/`[no response]` (276-308), single-parent-handoff hiding (310-348), non-duplication of an already-visible result (350-395), replacing an empty placeholder (397-439), full parent-context preservation (441-476), reconstructing a valid sequence after two parallel subagents complete (478-575), and `test_strip_handoff_back_messages_pre_model_hook_sanitizes_orphan_tool_calls_when_filter_is_noop` (577-604) — asserts a **standalone** orphan `AIMessage(tool_calls=[...])` with no matching `ToolMessage` gets dropped by the trailing sanitizer call.

### Testing Framework and Patterns

pytest (per `.ai-run/guides/testing/testing-patterns.md`). Direct, dependency-free protocol-testing style: construct message objects literally, call the hook function, assert on the returned dict. No LangGraph runtime, LLM mocks, or checkpointer needed for these unit tests.

### Coverage Gaps

1. **No test reproduces the exact bug**: a sub-agent's **intermediate** `AIMessage(tool_calls=[...])` being mistaken for the terminal handoff answer by `_consume_pending_handoff_message`. The closest existing test (`test_subagent_task_pre_model_hook_preserves_follow_up_subagent_state`, 172-215) tests the analogous shape only for `_subagent_task_pre_model_hook` (the sub-agent's own inbound-task hook), not for `_strip_handoff_back_messages_pre_model_hook` (the parent supervisor's hook) — exactly where the recovery notes say the defect lives.
2. No test exercises the interaction between the handoff-consumption logic and the trailing `sanitize_rich_history_for_llm` sweep when the consumption logic itself produces the orphan (the existing orphan test at line 577 covers only a standalone orphan never part of a handoff).
3. No test covers the secondary streaming `chunks_collector` append-to-successful-answer defect in `_send_error_to_thread`/`_stream_graph`.

---

## 5. Configuration and Environment

### Environment Variables

None directly gate this feature area — pure application logic.

### Configuration Files

None specific; behavior driven by `codemie.configs.config`, e.g. `HIDE_AGENT_STREAMING_EXCEPTIONS`, `CUSTOM_STACKTRACE_MESSAGE` (used in `_process_chunks`/`_send_error_to_thread`), `DISABLE_PARALLEL_TOOLS_CALLING_MODELS` (used in `bootstrap.py`/`langgraph_agent.py`).

### Feature Flags and Deployment Concerns

`cfg.HIDE_AGENT_STREAMING_EXCEPTIONS` toggles whether the raw exception text or a generic message is appended in `_process_chunks` (`langgraph_agent.py:809-814`) — directly relevant to the secondary presentation defect, since even with the flag on, `chunks_collector` still gets a message concatenated onto whatever successful sub-agent content already accumulated. No deployment/config changes required for the root-cause fix; local repro requires rebuilding the backend Docker image since `D:\Projects\docker-compose.yaml` does not volume-mount `codemie/src` (dev-loop note only, from recovery notes).

---

## 6. Risk Indicators

- The root-cause fix touches a pre_model_hook that many existing tests assert exact message-list equality against — any change to `_consume_pending_handoff_message`'s matching rule must be validated against all of `test_langgraph_multi_assistant_handoffs.py`, not just a new regression test.
- The trailing `sanitize_rich_history_for_llm` sweep can mask the true effect of a source-level fix (it may silently "fix" the symptom by dropping the orphan) — the new regression test must assert on the **specific** retained/dropped messages, not merely "no exception raised", to avoid a false-positive pass via sanitizer-driven data loss.
- Two viable designs are open (preserve nested pair vs. atomic drop-both) — this is a judgment call for brainstorming/spec, not purely mechanical; current UI displays nested tool calls, which favors preservation for observability, but must be checked against existing parallel-handoff tests that assert particular message shapes.
- The historical revert (`1cd636dcc`) is fully superseded on current `main` — a risk if anyone assumes reverted code is "still absent"; confirmed it is not.
- The reference branch `EPMCDME-13889` solves a superset problem (nested-supervisor name attribution) — risk of over-scoping the fix if `_close_unanswered_tool_calls`-style logic is ported without adapting it to avoid touching the (separate, working) `[no response]`/parallel-handoff paths already on `main`.
- No test currently covers the secondary streaming-presentation defect (`chunks_collector` append) — if in scope, requires new test infrastructure (mocking `agent_executor.stream`), a heavier lift than the pure pre_model_hook unit tests.

---

## 7. Summary for Complexity Assessment

The bug is fully localized to the supervisor sub-layer of the agent-runtime (`src/codemie/agents/supervisor/history.py`), specifically `_consume_pending_handoff_message`'s author-name-only matching, which mistakes a sub-agent's intermediate tool-call `AIMessage` for its terminal handoff answer. The fix is a single targeted change (add a `tool_calls` guard so only a terminal AIMessage is consumed as the handoff result) plus a decision on how to keep the tool-call/tool-result pair intact in the surrounding history (preserve nested pair vs. atomic removal) — this is a well-understood, single-file source change with no DB/config/deployment surface.

The primary complexity driver is not the fix itself but the **regression testing requirement**: the existing 603-line test suite exercises many overlapping message-shape scenarios (parallel handoffs, single handoffs, empty placeholders, orphan sanitization) that any change to the consumption logic must continue to satisfy, and a new test must precisely reconstruct the ticket's exact nested shape (`AIMessage(tool_calls) → ToolMessage → terminal AIMessage`) plus assert on exact retained-message content (not just "no crash") to be meaningful. A secondary, optional defect (streaming exception concatenation in `langgraph_agent.py`) has zero existing test infrastructure and would add meaningfully to scope if included — the recovery notes explicitly treat it as "defense in depth, review separately" rather than required for this ticket's acceptance criteria.

Overall: low architectural risk, low file-change surface (primarily `history.py` + its test file), but moderate requirements-clarity risk owing to the two open design choices, which brainstorming/spec must resolve before planning.
