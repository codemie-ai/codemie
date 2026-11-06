# Technical Research

**Task**: tool-confirmation sub-assistant orchestrator langgraph_supervisor interrupt resume thoughts nested
**Generated**: 2026-10-28
**Research path**: filesystem

Workspace root: `/private/tmp/claude-501/-Users-yanaasadchaya-Projects-epam-codemie-codemie/71e357cc-a240-4f23-9b4e-c78f02136c93/scratchpad/ws`
Repositories: `codemie` (path `codemie`, rev `15e92e1e0`, detached origin/main), `codemie-ui` (path `codemie-ui`, rev `1f1613f9b`, detached origin/main). CodeGraph: `CODEGRAPH_UNAVAILABLE` (no `.codegraph/` in either root).

---

## 1. Original Context

Ticket: EPMCDME-16461 (relates to EPMCDME-13903 tool confirmations and EPMCDME-14900 silently dropped sub-assistants). Verbatim requirements source: `scratchpad/jira/s5.txt` (first line about creating a ticket ignored):

> Title: Tool-call confirmation inside sub-assistants, shown nested under the sub-assistant's step
>
> Context (verified on main): tool-call confirmation (Allow / Deny, tool_call_policy ask_for_approval / approve_for_me, customer min policy) works only for the top-level assistant. For a sub-assistant of an orchestrator the confirmation is not supported: LangGraphAgent.init_agent raises SubagentToolConfirmationUnsupportedException when a sub-assistant resolves to a policy that requires confirmation (langgraph_supervisor wraps sub-assistant graphs so interrupt_before cannot be added to them, EPMCDME-15182). create_assistant_executors catches it and only logs it, so the sub-assistant silently disappears from the orchestrator while its handoff tool is still offered; the user sees no error and the delegation does nothing or fails. Only the orchestrator's own calls (including transfer_to_<sub>) can be confirmed, so after approving the delegation the sub-assistant's tools run without asking. The resulting thought tree on backend and UI also looks up interrupted thoughts and thought parents only at the top level (core/thread.py _nest_to_thought, UI chatGeneration resumeToolCall and parent lookup, ChatAiMessage renders Allow/Deny only for top-level thoughts).
>
> User scenario:
> 1. Enable tool permissions. Create sub-assistant S with a write tool (e.g. Jira create issue) and tool-call policy "ask for approval" (or keep S on auto-approve and set the conversation policy to ask for approval).
> 2. Create orchestrator O with S as a sub-assistant.
> 3. In O's chat ask: "Ask S to create a Jira ticket titled 'test'".
> Today: no Allow/Deny for S's Jira call; S is dropped (server log "Failed to create executor ... tool_call_policy=..."); delegation silently does nothing or fails.
> Expected: S's Jira call waits for the user's decision; Allow/Deny is shown inside S's step in the chat (nested under the orchestrator's delegation thought); Allow runs the call and S continues, Deny returns the denial to S, and the orchestrator continues after S finishes.
>
> Scope:
> Backend:
> - Sub-assistants honour the effective tool-call policy: a sub-assistant's tool call that requires confirmation interrupts the run (sub-assistant execution that supports interrupts, e.g. its own checkpointed graph invoked from the orchestrator instead of the opaque supervisor wrapper), and resume continues the sub-assistant and then the orchestrator.
> - The interrupted thought is emitted nested under the sub-assistant's thought; nested thought lookup works at any depth (parent found at any depth; a decided confirmation replaces its prompt and clears interrupted, as at the top level).
> - resume_tool_call finds and resumes a pending call at any depth.
> - Until the above ships for a configuration, a sub-assistant must never be dropped silently: either the configuration is rejected when saving, or the chat shows a clear error naming the sub-assistant and the policy.
> UI (codemie-ui):
> - Interrupted thoughts and thought parents are found at any depth (chatGeneration resumeToolCall and nested thought placement).
> - Allow/Deny are rendered inside the nested thought that waits for the decision; a thought containing a pending decision at any depth opens automatically and stays visible even when tool outputs are hidden.
> - The prompt stays blocked while any nested confirmation is pending.
>
> Acceptance criteria:
> - The scenario above shows Allow/Deny inside S's step; Allow executes the Jira call, Deny does not, and in both cases S and then O finish their turns.
> - approve_for_me: S's read-only calls run without asking, write calls ask.
> - Customer min policy applies to sub-assistants as well.
> - Reloading the page while a nested confirmation is pending shows it with working Allow/Deny.
> - Top-level confirmations behave as today.
> - No configuration silently drops a sub-assistant; if any unsupported case remains, the user sees an error.
> - Backend and UI tests cover nested interrupt, resume (allow/deny), reload, and nested thought placement.
>
> Note: a UI prototype of nested Allow/Deny and nested thought lookup exists in the codeminions-prototype branches (codemie, codemie-ui).
> Labels: backend, ui, tool-confirmation

---

## 2. Codebase Findings

Repository impact: `codemie` — **affected** (agent build, confirmation mixin, thread, resume handler). `codemie-ui` — **affected** (store, chat message, thought components, prompt).

### Existing Implementations

Backend (`codemie`):
- `codemie:src/codemie/agents/langgraph_agent.py:372-374` — `init_agent` raises `SubagentToolConfirmationUnsupportedException` when `is_subagent and require_tool_confirmation`.
- `codemie:src/codemie/agents/langgraph_agent.py:303-332` — `_build_supervisor_agent` calls `langgraph_supervisor.create_supervisor(agents=self.subagents, tools=tools+handoff_tools, output_mode="last_message", ...)`; `:334-350` `_bake_interrupt_into_supervisor_node` recompiles only the supervisor's own react node with `interrupt_before=["tools"]` (docstring: sub-assistant nodes are "an opaque callable").
- `codemie:src/codemie/agents/langgraph_agent.py:975-981` — `_stream_graph` streams with `subgraphs=bool(self.subagents)` and `interrupt_before=INTERRUPT_BEFORE_TOOLS` when confirmation required; `:1039-1042` calls `ask_for_tool_confirmation` after the stream and recurses on auto-resume.
- `codemie:src/codemie/agents/langgraph_agent.py:572-617` — `_create_custom_handoff_tool` returns `Command(graph=Command.PARENT, goto=agent_name, ...)`; handoff tools built from `subagent_descriptions`, independent of which executors were built (`:391-441`).
- `codemie:src/codemie/core/exceptions.py:36` — exception class (subclass of `ValidationException`).
- `codemie:src/codemie/service/tools/assistant_factory.py:100-117` — `AssistantFactory.build` builds sub-agent with `is_subagent=True`, `AgentStreamingCallback(thread_generator)`, `set_thread_context({}, LATEST)`; returns `agent.agent_executor` (compiled graph only, the `LangGraphAgent` instance is discarded).
- `codemie:src/codemie/service/tools/assistant_factory.py:183-193` — `create_assistant_executors` re-raises only `MCPToolLoadException`; any other exception (incl. the confirmation one) is logged `"Failed to create executor for assistant {id}"` and skipped.
- `codemie:src/codemie/service/assistant/assistant_engine_builder.py:296-326` — `configure_agent_kwargs`: effective policy via `ToolPermissionsService`, `require_tool_confirmation = allow_tool_confirmation and policy != AUTO_APPROVE`; subagents built, then `subagent_descriptions = get_subagent_descriptions(assistant, user)` from config (so a dropped sub-assistant keeps its handoff tool). `assistant_service.py:648-650` passes `allow_tool_confirmation=True` for every LangGraph agent including sub-agents.
- `codemie:src/codemie/service/tool_permissions_service.py:43-90` — feature gate `tool_permissions`, `allow_override` (request/conversation override), customer floor `min_tool_call_policy` clamp via `ToolCallPolicy.stricter`. Already applied per assistant, so it runs for sub-assistants too.
- `codemie:src/codemie/agents/tool_confirmation/tool_call_confirmation_mixin.py` — `ask_for_tool_confirmation` (:42-98) reads `self.agent_executor.get_state(...)` top-level, checks `state.next` contains `"tools"`, APPROVE_FOR_ME auto-resume via `tool.is_safe`, saves `ToolCallPendingEvent`, emits interrupted `Thought` reusing `_find_interrupted_thought()` (:265-270, latest in-progress thought from `thread_generator.thoughts`, top-level list) — the emitted thought carries no `parent_id`. `resume_from_interrupt` (:100-138) re-streams `None`. `reject_tool_call` (:166-197) emits an aborted thought without `parent_id` and `update_state(..., as_node="tools")` on the top-level graph.
- `codemie:src/codemie/agents/tool_confirmation/models.py:20-28` — `ToolCallPendingEvent` fields: `pending_tool_call_id, tool_name, tool_args, display_name, history_index, original_user_message, all_tool_call_ids` (no subgraph namespace / parent thought / sub-assistant identity).
- `codemie:src/codemie/agents/tool_confirmation/conversation_checkpoint_saver.py:38-96` — single checkpoint per conversation keyed only by `thread_id`; ignores `checkpoint_ns`, `put_writes` is a no-op, `list` yields nothing. Backed by `conversation.pending_checkpoint` / `pending_tool_call` (`conversation_checkpoint_service.py:34-80`).
- `codemie:src/codemie/rest_api/routers/assistant/execution.py:203-255` — `POST /assistants/{id}/model/tool-call/resume` validates a checkpoint + pending call exist; `codemie:src/codemie/rest_api/handlers/assistant_handlers.py:1270-1333` rebuilds the orchestrator agent via `AssistantService.build_agent` and calls `resume_from_interrupt` / `reject_tool_call`; clears checkpoint unless re-interrupted.
- `codemie:src/codemie/core/thread.py:123-173` `_process_thought` — top-level id match only; top-level merge replaces message when `interrupted` and copies `interrupted`. `:225-245` `_nest_to_thought` finds parent only in top-level `self.thoughts`; child merge always appends message and never updates `interrupted`.

Frontend (`codemie-ui`):
- `codemie-ui:src/store/chatGeneration.ts:1180-1192` `resumeToolCall` — `lastHistoryItem.thoughts?.find(t => t.id === thoughtId && t.interrupted)` (top level only).
- `codemie-ui:src/store/chatGeneration.ts:1792-1830` `_handleThought` — existing thought found at any depth via `_findThought` (:1833), but new-thought parent lookup is `historyItem.thoughts.find(item => item.id === thought.parent_id)` (top level); unmatched parent → pushed top-level.
- `codemie-ui:src/pages/chat/components/ChatHistory/ChatAiMessage/ChatAiMessage.tsx:277-306` — renders `Thought` when `!hideToolOutputs || thought.interrupted`, and Allow/Deny only for top-level `thought.interrupted`.
- `codemie-ui:src/pages/chat/components/ChatPrompt/ChatPrompt.tsx:150-155` — `isToolCallPending` checks top-level thoughts only.
- `codemie-ui:src/utils/chatHelpers.ts:40` — reload `isInterrupted` top-level only; `:171-189` hydrates top-level thoughts and passes `children` through raw.
- `codemie-ui:src/components/Thought/ThoughtHeader.tsx:30` — "Interrupted" status label; `Thought.tsx` renders children recursively, opens on `isExpanded || isInProgress`.

### Architecture and Layers Affected
- Agent runtime: `LangGraphAgent` supervisor build/stream, `ToolCallConfirmationMixin`, `supervisor/` (coordinator, bootstrap patch for langgraph_supervisor).
- Service: `assistant_factory.create_assistant_executors`, `assistant_engine_builder.configure_agent_kwargs`, `ToolPermissionsService`, `ConversationCheckpointService`.
- API/handler: tool-call resume router + `assistant_handlers` resume handler.
- Core streaming: `ThreadedGenerator` thought tree (`core/thread.py`).
- UI: Valtio-style store `chatGeneration.ts`, `ChatAiMessage`, `Thought*` components, `ChatPrompt`, `chatHelpers` hydration.

### Integration Points
- `langgraph-supervisor = "0.0.31"`, `langgraph = "1.1.6"`, `langgraph-checkpoint = "^3.0.1"`, `langchain-core = "1.3.3"` (`codemie:pyproject.toml`). Compatibility patches in `codemie:src/codemie/agents/supervisor/bootstrap.py` (parallel parent-command merge, ToolNode output combine).
- Sub-agent thoughts reach UI through supervisor callbacks keyed by author with `CallbackParentTracker` (`agents/callbacks/utils/parent_context.py`) and `SupervisorCoordinator` handoff bindings (`agents/supervisor/coordinator.py`).
- Backend → UI contract: streamed `Thought` objects with `id, parent_id, interrupted, aborted, in_progress, children`; resume request `toolCallAction` + `conversationId`, keyed by thought id on UI side.

### Patterns and Conventions
- Confirmation = `interrupt_before=["tools"]` + `get_state().next` inspection; deny = inject `ToolMessage` via `update_state(as_node="tools")`.
- Interrupted thought reuses id of the in-progress tool thought so the UI merges it in place.
- Thought ids for denials derived via `_tool_call_id_to_uuid(tool_call_id)`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `codemie:.ai-run/guides/agents/langchain-agent-patterns.md`, `workflows/langgraph-workflows.md` cover agent/LangGraph patterns; no guide covers tool-call confirmation.
- Prior design material: `codemie:docs/superpowers/work-items/EPMCDME-13903.md`, `docs/superpowers/handoffs/2026-08-17-EPMCDME-13903_chat-tool-call-confirmations-frontend-handoff.md`, `docs/superpowers/tasks/2026-08-17-enforce-tool-call-policy-via-customer-config/spec.md`, `2026-08-17-approve-for-me-tool-mutation-classification/spec.md`.
- `codemie-ui:.ai-run/guides/` exists (architecture, components, patterns); not read in depth.

### Architectural Decisions
- EPMCDME-15182 decision recorded in docstrings (`langgraph_agent.py:334-341`, `exceptions.py:36-45`): fail loudly rather than run sub-assistant tools unconfirmed.
- Single-checkpoint policy for conversation checkpointer (`conversation_checkpoint_saver.py:38-44`).
- EPMCDME-16233 (`core/thread.py` `consumer_aborted`) shares state across sub-agents via `ThreadedGenerator`, since each sub-agent is a separate `LangGraphAgent`.

### Derived Conventions
- Sub-agents are built as independent `LangGraphAgent` instances, only their compiled graph survives into the supervisor.

---

## 4. Testing Landscape

### Existing Coverage
- `codemie:tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py:985-1009` — sub-agent raises / does not raise.
- `codemie:tests/codemie/agents/test_langgraph_tool_confirmation.py`, `tests/codemie/agents/tool_confirmation/` (approve_for_me, script tools, replay on resume), `tests/codemie/agents/test_conversation_checkpoint_saver.py`.
- `codemie:tests/codemie/rest_api/routers/test_tool_call_resume.py`, `tests/codemie/service/assistant/test_configure_agent_kwargs_confirmation.py`, `tests/codemie/service/test_tool_permissions_service.py`.
- `codemie:tests/codemie/core/test_thread.py` — one nested test (finalize in_progress), none for `_nest_to_thought` depth/interrupt merge.
- `codemie-ui:src/store/__tests__/chatGeneration.resumeToolCall.test.ts`, `chatGeneration.toolCallConfirmation.test.ts`, `src/components/Thought/__tests__/Thought.test.tsx`, `ThoughtHeader.test.tsx`; `ChatAiMessage/__tests__/`.

### Testing Framework and Patterns
- Backend: pytest with `unittest.mock.patch` of `create_smart_react_agent` / `get_llm_by_credentials`.
- UI: vitest (`unit`, `integration` projects).

### Coverage Gaps
- No test of a supervisor with interrupt-capable sub-agent, nested interrupt, nested resume/deny, reload of nested pending.
- No test that `create_assistant_executors` surfaces (rather than swallows) a sub-agent build failure.
- `_nest_to_thought` deeper-than-one-level parent and interrupted-child merge untested; UI nested parent placement, nested prompt blocking, nested `isInterrupted` hydration untested.

---

## 5. Configuration and Environment

### Environment Variables
None specific found.

### Configuration Files
- `codemie:config/customer/customer-config.yaml:247-250` — `features:tool_permissions`, setting `min_tool_call_policy: auto_approve | approve_for_me | ask_for_approval`.
- `codemie:src/codemie/rest_api/models/assistant.py:310` — `tool_call_policy` default `AUTO_APPROVE` in assistant `tool_permissions`.

### Feature Flags and Deployment Concerns
- Feature flag `tool_permissions` (customer config) gates all confirmation. Persistence via `conversations.pending_checkpoint` / `pending_tool_call` columns.

---

## 6. Risk Indicators

- `ConversationCheckpointSaver` stores one checkpoint per conversation and ignores `checkpoint_ns`; a checkpointed sub-graph (or `subgraphs` state) would overwrite the parent checkpoint — resume of both levels cannot work on this saver as-is.
- `langgraph_supervisor` 0.0.31 wraps sub-agent graphs opaquely; `create_supervisor` usage, handoff `Command.PARENT`, parallel handoffs and bootstrap patches all assume this wrapper. Replacing it touches handoff/parallel/history code (EPMCDME-14859, 16233 behaviours).
- `ask_for_tool_confirmation`, `reject_tool_call`, `_find_interrupted_thought` read top-level state/thoughts and emit thoughts without `parent_id`; `ToolCallPendingEvent` has no namespace/parent fields.
- Sub-agent `LangGraphAgent` instance is discarded (`AssistantFactory.build` returns only `agent_executor`), so its `tools`, `tool_call_policy` (needed for `is_safe` approve_for_me check) are not reachable from the orchestrator at interrupt time.
- `_pending_tool_confirmation` / `tool_pending_event` are `ClassVar` on the mixin — shared-state hazard when multiple agents in one run interrupt.
- `create_assistant_executors` broad `except Exception` drops sub-assistants silently (also for non-confirmation errors); handoff tools come from config descriptions, so the orchestrator can target a missing node.
- Prototype branches are behind main: backend prototype lacks EPMCDME-16233 `consumer_aborted`; UI prototype lacks `TruncationBadge`/`headerBadge` and `truncated` handling — cannot be merged wholesale.
- Speculative: an "explicit error" fallback likely needs a user-visible error path in `configure_agent_kwargs` or assistant save validation — neither exists for this case today.

---

## 7. Summary for Complexity Assessment

The change spans two repositories and five layers: backend agent runtime (`LangGraphAgent` supervisor build/stream, `ToolCallConfirmationMixin`), services (`assistant_factory`, `assistant_engine_builder`, checkpoint service/saver), the resume API handler, the core thought tree (`core/thread.py`), and the UI store plus chat rendering (`chatGeneration.ts`, `ChatAiMessage.tsx`, `Thought*`, `ChatPrompt.tsx`, `chatHelpers.ts`). The UI and thread-tree parts are small and largely prototyped (depth-aware `_find_thought`/`_findThought`, `hasInterrupted`, `renderActions` on nested `Thought`), though the prototypes trail main.

The backend core is novel: today sub-assistants run inside `langgraph_supervisor`'s opaque wrapper, which cannot carry `interrupt_before`, and the explicit guard raises an exception that `create_assistant_executors` swallows. Supporting a nested interrupt requires sub-assistant execution that can pause and resume, but the existing conversation checkpointer keeps only one checkpoint and ignores namespaces, the pending-call model has no notion of depth, and the confirmation mixin inspects only the top-level graph and thought list. Customer min policy and approve_for_me resolution already run per assistant, so policy logic mostly exists; the sub-agent instance (tools for `is_safe`) is discarded after build.

Tests cover top-level confirmation, resume router, permissions, and the current "sub-agent raises" guard; there are no tests for nested interrupts, nested resume/deny, reload of a nested pending call, nested thought placement, or the silent-drop path. Key risks: checkpointer redesign, replacing/augmenting the supervisor wrapper without regressing handoff/parallel/truncation behaviour, ClassVar shared confirmation state, and divergence of prototype branches from main.

---

## 8. External References

- `/Users/yanaasadchaya/Projects/epam/codemie/codemie/.claude/worktrees/codeminions` — resolved; branch `codeminions-prototype`, head `933712805`. `src/codemie/core/thread.py` adds `_find_thought(thoughts, id)` (recursive, latest match at any depth) used by `_nest_to_thought` for parent lookup; child merge now replaces message when the existing child is `interrupted` ("A decided confirmation replaces its prompt, as at the top level") and copies `interrupted` from the incoming thought. Branch predates EPMCDME-16233 (`mark_consumer_aborted`/`is_consumer_aborted` absent).
- `/Users/yanaasadchaya/Projects/epam/codemie/codemie-ui/.claude/worktrees/codeminions` — resolved; branch `codeminions-prototype`, head `2813d7fdc`. `chatGeneration.ts`: `resumeToolCall` uses `_findThought(lastHistoryItem.thoughts ?? [], thoughtId)` then `if (!targetThought?.interrupted) return`; `_handleThought` parent lookup uses `_findThought(historyItem.thoughts, thought.parent_id)`. New `src/components/Thought/hasInterrupted.ts`: `hasInterrupted = t => !!t.interrupted || !!t.children?.some(hasInterrupted)`. `ChatAiMessage.tsx`: `renderDecision(thought)` (Allow/Deny), visibility `!hideToolOutputs || hasInterrupted(thought)`, passes `renderActions={renderDecision}` to `Thought`. `Thought.tsx`: props `renderActions`, `expandInProgress`; `isOpen = isExpanded || (isInProgress && expandInProgress) || hasInterrupted(thought)`; children receive `renderActions`; `{isEmbedded && thought.interrupted && renderActions?.(thought)}`. `ThoughtHeader.tsx` gains `isOpen` prop. Prototype also contains unrelated CodeMinion code (`HttpExchange`, `WorkspaceSync`, `useWorkspacePathLinks`) and removes main's `TruncationBadge`/`headerBadge`/`truncated` handling — prototype lacks those, not a requirement. `ChatPrompt.tsx` and `chatHelpers.ts` top-level `interrupted` checks are unchanged in the prototype.
