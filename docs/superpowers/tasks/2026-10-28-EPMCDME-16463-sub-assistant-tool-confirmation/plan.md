# Sub-assistant tool-call confirmation (EPMCDME-16463) Implementation Plan

> **For agentic workers:** implement task by task, in order. Each task leaves the branch green. Steps use `- [ ]`.

**Goal:** At any depth, a tool call that its agent's resolved policy requires to confirm pauses the whole run. The waiting step sits under its immediate container, and decisions resume the call and every agent above it, including after a reload.

**Architecture:** There is one `post_model_hook` gate per LangGraph agent, built on dynamic `interrupt()`. It uses one call-identity function and carries decisions in `config["configurable"]["codemie_tool_decisions"]`. A namespace-aware `ConversationCheckpointSaver` stores the checkpoints, and a format-2 `StoredPendingToolCall` record has a single owner, `ConversationCheckpointService`. `langgraph_supervisor` stays. The spec (`spec.md`, rules 1-10) is the authority, and the spike conclusions in the 2026-10-30 spec holds the verified LangGraph behaviour.

**Tech stack:** Python 3.12, langgraph 1.1.6, langgraph-supervisor 0.0.31, langgraph-checkpoint 3.x, SQLModel/Postgres JSONB, pytest.

**Conventions:** Use `codegraph explore "<symbol>"` before reading files. Commit per task using the repository's existing convention. Tests use the spike's scripted `BaseChatModel` pattern (`spike/common.py` in the scratchpad, reference only), so any re-run shows up in call counters. Every "runs once" assertion counts tool invocations.

**Release note (for the MR description, not a task):** the rollback runbook is `UPDATE conversations SET pending_tool_call = NULL, pending_checkpoint = NULL WHERE pending_tool_call->>'format' = '2'`.

---

### Task 1: Namespace-aware checkpoint saver
**Files:** Modify `src/codemie/agents/tool_confirmation/conversation_checkpoint_saver.py:38-96`, `src/codemie/service/conversation_checkpoint_service.py:34-80`. Test `tests/codemie/agents/test_conversation_checkpoint_saver.py`.
Test-first: yes — a test where `put` at ns `""` and at ns `agent_a:<id>` both survive, `put_writes` round-trips as `pending_writes`, `__interrupt__` is replaced on a second write, and `get_tuple` returns `checkpoint_ns`/`checkpoint_id`. It fails on today's single-record saver.
- [ ] Write tests for rule 8:
  - the shape is `{"namespaces": {ns: {checkpoint, metadata, writes}}}`, latest entry per ns;
  - writes are keyed `(checkpoint_id, task_id, idx)` and use `WRITES_IDX_MAP`;
  - order independence: `put(N)` drops only older writes, and writes for an older checkpoint are ignored;
  - `list` yields nothing.
  Add spike row 3a/Q1 graph tests on this saver: an outer `get_state(cfg).tasks[].interrupts` finds a nested interrupt, and a finished parallel sibling is not re-run on resume.
- [ ] Implement it. Each saver call is one atomic column `UPDATE ... jsonb_set(coalesce(...))` on `pending_checkpoint` only (AR-4), never a whole-row `conv.update()`. Keep `__pregel_checkpointer` at the top level of the config.
- [ ] MOD-2: extract a small saver base with `load`/`update` callables that owns the namespace and writes rules. The workflow saver stays single-record and does not use it yet.

### Task 2: Step tree at any depth
**Files:** Modify `src/codemie/core/thread.py:123-245` and `src/codemie/service/assistant/tool_output_visibility.py`. Test `tests/codemie/core/test_thread.py` and the visibility tests.
Test-first: yes — a thought whose `parent_id` is a grandchild is placed under it, and an incoming non-interrupted thought replaces an interrupted child's message and clears `interrupted`. Both fail today.
- [ ] Add one recursive `_find_thought(thoughts, id)` that returns the latest match at any depth. `_process_thought` and `_nest_to_thought` both use it.
- [ ] Child merge: an applied decision replaces the prompt and copies `interrupted`. An orphan stays at the top level with its `parent_id`. `consumer_aborted` (EPMCDME-16233) is untouched.
- [ ] `tool_output_visibility.py`: keep `parent_id` and walk the tree, so a hidden-output tree keeps the waiting step and its ancestors (AC14).
- [ ] Tests: depths 1-3, replacement, orphan (AC8).

### Task 3: Deterministic tool ids and smart-selection hygiene
**Files:** Modify `src/codemie/agents/smart_tool_state.py:99`, `src/codemie/agents/smart_react_agent.py:41-338` (`_resolve_available_tools`, `agent_node`, graph name), `src/codemie/core/exceptions.py`. Test `tests/codemie/agents/` (smart selection tests).
Test-first: yes — two registries built from the same tools give equal ids; unresolved selected ids raise `PendingCallStructuralMismatch`; the compiled smart graph's name is not `"LangGraph"`. All fail today.
- [ ] Ids become `f"{tool.name}_{toolkit or ''}"`, deterministic.
- [ ] In `_resolve_available_tools`, a selected id that does not resolve raises the new `PendingCallStructuralMismatch(agent_name, detail)` (a `ValidationException` subclass) instead of falling back to all tools.
- [ ] The smart graph gets the agent's unique name.
- [ ] `agent_node` re-raises `GraphBubbleUp` without logging it at ERROR.

### Task 4: Contract models and the call-identity function
**Files:** Modify `src/codemie/agents/tool_confirmation/models.py:20-28`, `src/codemie/rest_api/models/assistant.py:1761`. Create `src/codemie/agents/tool_confirmation/call_identity.py`. Test `tests/codemie/agents/tool_confirmation/test_call_identity.py`.
Test-first: yes — `assign_call_identities` on an AI message with repeated `call_0` ids, and on an id already answered by a `ToolMessage`, returns unique wire ids matching `^[a-zA-Z0-9_-]{1,40}$`. A non-repeated root id is unchanged.
- [ ] New models (contract fixed by the spec):
```python
class PendingCallLocation(BaseModel):
    agent_path: list[str]; agent_names: list[str]; parent_thought_id: str
class DecisionItem(BaseModel):
    thought_id: str; tool_call_id: str; location_key: str; checkpoint_ns: str
    event: ToolCallPendingEvent; decision: Literal["allow", "deny"] | None = None
    producer: str = "langgraph"                       # MOD-1
class StoredPendingToolCall(BaseModel):
    format: Literal[2] = 2; items: list[DecisionItem]; root_assistant_id: str
    saved: bool = False; claimed: bool = False; payload: dict  # producer payload
@dataclass(frozen=True)
class CallIdentity:
    wire_id: str; location_key: str               # carrier key = f"{location_key}/{wire_id}"
def assign_call_identities(messages, ai_message, location_key) -> list[CallIdentity]: ...
```
- [ ] Add `ToolCallPendingEvent.location: PendingCallLocation | None = None` and `ToolCallResumeRequest.thought_id: str | None = None`.
- [ ] Encoding decision (rule 3, AR-2): the location is a composite key beside the id, never inside it. A rewrite is `<raw[:28]>_<8-hex of xxh3(raw, position)>`, which is deterministic and judged by position, empty ids included.
- [ ] `location_key` comes from the gate task's namespace with the `post_model_hook` segment and task ids reduced by a stable hash (per instance, plan note). Two parallel instances of one S therefore differ, and resume reproduces them. The root key is `""`.

### Task 5: One owner of the pending record
**Files:** Modify `src/codemie/service/conversation_checkpoint_service.py` (incl. `save_interrupt_context:55`). Test `tests/codemie/service/test_conversation_checkpoint_service.py`.
Test-first: yes — `record_decision` on an undecided item of an unsaved record returns `NOT_SAVED` and changes nothing. Two concurrent final decisions give one `CLAIMED` and one `CONFLICT`. Both fail because the API does not exist.
- [ ] The service is the only reader and writer of `pending_tool_call` (format 2 only). It provides:
  - `pending_event(conversation)` (AR-2 projection): the current undecided item's event, or an old-format event as stored;
  - `classify(record) -> NONE | OLD | V2`;
  - `write_pause(...)`;
  - `mark_saved()`;
  - `record_decision(conv_id, thought_id|None, action, history_index, thoughts_patch)`: one compare-and-set `UPDATE` that writes the item and the saved thoughts together;
  - `claim()` and `release_claim()`;
  - `clear()`.
- [ ] Every write is a column-level statement. `save_interrupt_context`'s whole-row update stops writing these columns (AR-4). A test shows that a chat-history save or rename between the pause and the final decision undoes nothing.
- [ ] Conversation GET (`rest_api/routers/conversation/`) uses `pending_event()` instead of `from_attributes`. The GET succeeds for old-format and new-format rows.

### Task 6: The `post_model_hook` gate
**Files:** Create `src/codemie/agents/tool_confirmation/confirmation_gate.py`. Test `tests/codemie/agents/tool_confirmation/test_confirmation_gate.py`.
Test-first: yes — a root `create_react_agent` with the gate and a mixed batch [read, write] under approve-for-me pauses with only the write shown and `read_tool == 0`. After Allow: read 1, write 1.
- [ ] `build_confirmation_hook(policy, tools, location, pauses_own_calls)` returns a hook that runs these steps in order, with no side effects before `interrupt()`:
  1. assign identities (Task 4) and rewrite the AI message in place (same message id);
  2. for each call, read the carried decision first and use it only when its `ns` equals the gate's own `checkpoint_ns`;
  3. classify with policy and `is_safe` only the calls that have no decision;
  4. if any call is undecided and `pauses_own_calls` is set, `interrupt()` once with all items;
  5. on resume, a still-undecided call means deny, and the hook returns denial `ToolMessage`s.
  With `pauses_own_calls=False` it applies the identity only.
- [ ] Tests (spike Q1-Q3):
  - the policy flips to auto-approve between pause and resume, and a Deny still never runs the call;
  - a carrier entry with a foreign `ns` is ignored;
  - all-denied routes back to the model;
  - two agents batch, then one combined `Command(resume={ids: "ack"})`, and each tool runs once.

### Task 7: Resolve the policy once and the build context
**Files:** Modify `src/codemie/service/assistant_service.py:513-650`, `src/codemie/service/assistant/assistant_engine_builder.py:296-326`, `src/codemie/service/tools/assistant_factory.py:100-193`. Test `tests/codemie/service/assistant/test_configure_agent_kwargs_confirmation.py`.
Test-first: yes — `build_agent` calls `ToolPermissionsService` once per assistant, before tool assembly. A → B → A raises `SubagentCycleException` naming the cycle (AC22).
- [ ] Resolve effective permissions right after `_apply_conversation_runtime_overrides` (`assistant_service.py:543`) and derive `pauses_own_calls`.
- [ ] Pass one `SubagentBuildContext(can_resume_pause, ancestor_keys, path_key, resume_entry)` down (STR-4). The path key is the assistant id, or `__builtin__<value>` for builtins.
- [ ] Return one `SubagentHandle(agent, executor, tree_requires_confirmation, pauses_own_calls, policy)` up from `AssistantFactory.build`, which keeps the `LangGraphAgent` instance.
- [ ] `configure_agent_kwargs` receives the result instead of resolving again. `tree_requires_confirmation` is `pauses_own_calls` OR any child's flag. Only the root creates the conversation row.
- [ ] This task does not change behaviour: the sub-agent refusal stays until Task 10.

### Task 8: The gate at the root and the supervisor, with the static path removed
**Files:** Modify `src/codemie/agents/langgraph_agent.py:303-374, 975-1042`, `src/codemie/agents/tool_confirmation/tool_call_confirmation_mixin.py`, `src/codemie/rest_api/handlers/assistant_handlers.py:1266-1333`. Rewrite `tests/codemie/agents/tool_confirmation/test_ask_for_tool_confirmation_approve_for_me.py`, the run-config tests in `tests/codemie/agents/test_langgraph_tool_confirmation.py`, and `tests/codemie/rest_api/routers/test_tool_call_resume.py:120-157`.
Test-first: yes — a single top-level write call under ask-for-approval emits the same `ToolCallPendingEvent` and the same step id as main, with `location=None` (AC11). It fails once the static path is gone, until detection is rewritten.
- [ ] Pass `post_model_hook` to the standard `create_react_agent` and to `create_supervisor(post_model_hook=…)`.
- [ ] Delete `INTERRUPT_BEFORE_TOOLS` usage, `_bake_interrupt_into_supervisor_node`, the mixin's `state.next` detection, `reject_tool_call` and `deny_unreviewed_sibling_tool_calls`.
- [ ] Mixin detection reads the outer `get_state(cfg).tasks[].interrupts`, skipping tasks whose `result is not None`, and writes the record via `write_pause`. `mark_saved` runs after the chat history is saved.
- [ ] `resume_from_interrupt(decisions)` streams `Command(resume={every pending id: "ack"})` with the carrier set. Stored interrupt ids that differ from pending ones raise `PendingCallStructuralMismatch`.
- [ ] `_pending_tool_confirmation` and `tool_pending_event` become per-instance, not `ClassVar`.
- [ ] Interim handler: map Allow/Deny onto the single shown item. Task 12 replaces this.
- [ ] Tests:
  - root mixed batch (AC5);
  - a policy switch during the pause (AC19, root);
  - O on a confirming policy delegating to S1 and S2 in parallel: both shown, Allow both, each S runs once, and the `supervisor/bootstrap.py` patches and the EPMCDME-16233 abort hold;
  - a chunk-order test on pinned langgraph with `subgraphs=True`: the paused message's tool step starts before its waiting step.

### Task 9: Call identity in the stream and container placement
**Files:** Modify `src/codemie/agents/langgraph_event_adapter.py:239-269, 326-343`, `src/codemie/agents/supervisor/history.py:321-339`, `src/codemie/agents/callbacks/utils/parent_context.py`, `src/codemie/agents/supervisor/coordinator.py`. Test `tests/codemie/agents/test_langgraph_event_adapter.py`.
Test-first: yes — an auto-approve O whose endpoint numbers calls `call_0` delegates to S1 in round 1 and S2 in round 2. The two delegation steps get distinct ids, equal to `_tool_call_id_to_uuid(identity)`. This fails today because both are `uuid5(call_0)`.
- [ ] When `tree_requires_confirmation` is set, attach the identity-only hook (Task 6) to every agent of the tree.
- [ ] The adapter starts tool and handoff steps from `assign_call_identities` (the same function, never a second derivation), on pausing passes as well.
- [ ] The container is `_tool_call_id_to_uuid` of the latest matching handoff identity (AC26).
- [ ] Tests:
  - AC27's three shapes: the streamed step, its result and its container carry one identity;
  - nested chunk order (plan note).

### Task 10: Nested pause and resume
**Files:** Modify `src/codemie/agents/langgraph_agent.py:372-374, 391-441`, `src/codemie/agents/tool_confirmation/tool_call_confirmation_mixin.py`, `src/codemie/service/tools/assistant_factory.py`. Test `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py` and a new `tests/codemie/agents/tool_confirmation/test_nested_confirmation.py`.
Test-first: yes — S on ask-for-approval under O pauses at `location={agent_path:[S key], parent_thought_id: <O→S step>}`, and today's `SubagentToolConfirmationUnsupportedException` path no longer fires (AC1). The existing "sub-agent raises" test at `:985-1009` is replaced.
- [ ] When `can_resume_pause` is set, sub-agents are built with the gate and no refusal. The interrupted thought is emitted with `parent_id` set to its container.
- [ ] Resume seeds the generator with the turn's saved thoughts and binds each path agent again to its container.
- [ ] Tests, all on a rebuilt graph over the Task 1 saver:
  - Allow and Deny (AC2, AC3);
  - reload from Postgres reproduces the interrupt ids (AC4);
  - customer minimum and override applied to S (AC6);
  - a builtin S (AC20);
  - O → O2 → S, with parallel pauses at depth 2 and O2's own call under O → O2 (AC21);
  - two calls of S in one message (AC15 runtime part);
  - O hands off to the same S twice in parallel, on one id, with an identical location on resume;
  - a consumer abort during a resumed nested run leaves the record cleared or decidable (AC25);
  - a nested two-round call whose resumed messages pass each provider family's langchain message conversion (Anthropic/Bedrock, OpenAI/Azure, Gemini) with ids matching the Task 4 regex.

### Task 11: Channels and exclusion in one list
**Files:** Modify `src/codemie/service/tools/assistant_factory.py:120-193`, `src/codemie/service/assistant/assistant_engine_builder.py`, the callers at `assistant_handlers.py:663, 1146, 1207, 1308`, `hedged_handler.py:485`, `rest_api/a2a/server/codemie_task_manager.py:277`, `service/assistant/assistant_health_check_service.py:305` and the workflow assistant node. Test `tests/codemie/service/tools/test_assistant_factory.py`.
Test-first: yes — with `can_resume_pause=False`, a confirming S is absent from both the handoff tools and `subagent_descriptions`, and a warning names S, its policy and the channel. O still answers. This fails today because the handoff tool is still offered.
- [ ] `create_subagent_executors` returns `built` and `excluded` lists (AR-5). Descriptions and handoff tools come from built entries only.
- [ ] Exclusion rules: when `can_resume_pause` is false, a confirming S is excluded. A confirming `AIToolsAgent` S is excluded in every channel. Other build errors keep main's handling (EPMCDME-14900 is a non-goal).
- [ ] `can_resume_pause` is true for the stream, resume and hedged paths. The hedged path streams the same agent and already pauses at the root on main. It is false for sync, background, workflow nodes, the A2A server and the health check.
- [ ] Root inputs stay as on main: `allow_tool_confirmation=True` for any LangGraph root, and `False` for a workflow root.
- [ ] Tests: AC7 and AC16, including a builtin and a versioned S, and an A2A or health-check root on ask-for-approval that never runs its write call.

### Task 12: Resume API, authorisation and decisions
**Files:** Modify `src/codemie/rest_api/routers/assistant/execution.py:203-255`, `src/codemie/rest_api/handlers/assistant_handlers.py:119, 1266-1333, 1628`. Test `tests/codemie/rest_api/routers/test_tool_call_resume.py`.
Test-first: yes — the router has no checkpoint logic. A test handler's `handle_tool_call_resume` is reached via `get_request_handler` only after the WRITE check, and once per pause (AC18). It fails because the 404 check still sits in the router.
- [ ] Add a final `AssistantRequestHandler.resume_tool_call(request, conversation)`. Its checks run in this order:
  1. READ on the assistant, WRITE on the conversation, membership (moved from `:1272-1283`);
  2. no record gives 404;
  3. an old or unknown format is cleared with the AC11 error;
  4. `root_assistant_id` is checked on format-2 records only;
  5. no saved marker gives 409;
  6. the compare-and-set decision. A missing `thought_id` is accepted only for a one-item pause, and otherwise gives 409.
  A non-final decision updates the saved thoughts at `history_index` (no new entry) and streams the next waiting step. The final decision claims the record and calls the hook once. `A2AAssistantHandler` keeps refusing.
- [ ] The claim is released on every exit path: the hook's build and checkpoint check run inside the `try` that releases it.
- [ ] Tests through the real paths:
  - AC11: POST with and without `thought_id` against a main-shaped row (a static-breakpoint checkpoint), and no tool runs;
  - AC4 and AC15: reload through `GET /v1/conversations/{id}`;
  - AC9: stale `thought_id` gives 409, and an id-less Allow on a two-call pause gives 409;
  - AC24: two concurrent Allows;
  - AC28: an old-pod fixture that copies main's `ToolCallPendingEvent` and `StoredCheckpoint` (not imported) raises on a format-2 row;
  - AC19: after confirmation is turned off and a new message is sent, Allow gives 404.

### Task 13: Restoring the resumed request and structural mismatch
**Files:** Modify `src/codemie/rest_api/handlers/assistant_handlers.py:1266-1333`, `src/codemie/rest_api/routers/assistant/execution.py:268-277`, `src/codemie/service/tools/toolkit_lookup_service.py:402-430`. Test `tests/codemie/rest_api/handlers/test_tool_call_resume_restore.py`.
Test-first: yes — a resume restores the stored `hide_tool_outputs`, `root_version`, `skill_ids` and `request_tool_call_policy` exactly as received (`None` stays `None`). A removed sub-assistant on the path clears the record with an error naming it (AC10).
- [ ] The payload stores:
  - the interrupt ids;
  - each path agent's node name, resolved policy and looked-up toolkit names (STR-3);
  - `root_version`, `sub_assistants_versions`, `skill_ids`, `hide_tool_outputs`, `llm_model`, `original_user_message`;
  - `request_tool_call_policy`.
- [ ] Resume applies these the way `_prepare_assistant_for_execution` does. A pending record forces `ConversationCheckpointSaver` and gates the stored path, whatever the rebuilt tree says.
- [ ] Later calls in the turn are clamped with `ToolCallPolicy.stricter` to each agent's stored policy.
- [ ] Only a changed path agent, node name, pinned version, waiting tool or interrupt id is a mismatch. A policy change is not.
- [ ] The turn-start clear runs whenever the conversation has a pending record (AR-4).

### Task 14: Confirmation under smart selection
**Files:** Modify `src/codemie/agents/smart_react_agent.py:338` (`_create_sub_agent`), `src/codemie/agents/langgraph_agent.py`. Test `tests/codemie/agents/tool_confirmation/test_smart_selection_confirmation.py`.
Test-first: yes — a single assistant with smart selection on ask-for-approval pauses on its write call (AC23). It fails because `_create_sub_agent` passes no hook.
- [ ] Pass the gate as `post_model_hook` into `_create_sub_agent`'s `create_react_agent`.
- [ ] Tests:
  - root and depth-1 smart S resume with the same selected tool set (AC13);
  - a mixed batch under smart selection and under smart selection at depth runs each call once (AC5);
  - a policy switch during the pause under smart selection (AC19).

---

## Negative-constraint pass
- **UI, the CodeMinion handler and the multi-item contract.** No task touches them. Task 5's projection returns one item.
- **Confirmation in workflow, A2A, health-check, sync and background channels; `AIToolsAgent` confirmation.** None is added. Task 11 excludes these cases.
- **Save-time depth rule, policy-model changes, other drop reasons.** None is added. Task 11 keeps main's handling for other errors.
- **Migration, depth limit, resuming old formats.** None is added: Task 1 uses JSONB, and Task 12 clears old formats.
- **Fallback to all tools.** Task 3 removes it.
- **Policy checked before the carried decision.** Forbidden by Task 6's order and test. Deny never runs a call (Tasks 6 and 8).
- **Consumers deriving their own identity.** Forbidden: Task 9 reuses Task 4's function.
- **Ids outside `[a-zA-Z0-9_-]`.** Tasks 4 and 10 prevent them.
- **`checkpoint_ns` or interrupt ids in the API.** Tasks 4 and 12 keep them out. The router has no checkpoint logic (Task 12).
- **Decisions seeded from thoughts.** They are not: they come from items (Task 12). A resolved policy is never stored as an override (Task 13).
- **Smart graph named `"LangGraph"`.** Task 3 renames it. No feature flag is added.
