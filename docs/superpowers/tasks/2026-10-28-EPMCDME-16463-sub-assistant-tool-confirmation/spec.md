# Sub-assistant tool calls pause for confirmation and resume — Spec

> **Superseded in part (2026-11-02).** The 2026-10-30 spec `docs/superpowers/tasks/2026-10-30-EPMCDME-16463-confirmation-storage-rework/spec.md` replaces this one for storage, resume, structural checks and smart-selection resume. The gate placement and call identity here are current, except where rule 1 of the new spec applies: one graph shape, the identity hook on every LangGraph agent.

**Ticket**: EPMCDME-16463 (sub-task of EPMCDME-16461) · **Repo**: codemie (backend) · **Size**: XL, one run (story D4) · **Evidence**: spike conclusions in the 2026-10-30 spec ("Why the gate, and not `interrupt_before`"), `story.md` (revised copy), `arch-review/` (rounds 1–5)

## Problem

On main today:
- A sub-assistant whose effective policy requires confirmation is refused at build (`langgraph_agent.py:372-374`). The refusal is swallowed (`assistant_factory.py:183-193`), but the sub-assistant's handoff tool is still offered.
- Only the root can pause, through `interrupt_before` (`langgraph_agent.py:329-350`).
- The saver keeps one checkpoint per conversation (`conversation_checkpoint_saver.py:38-96`).
- The mixin and the step tree look only at the top level (`tool_call_confirmation_mixin.py:42-270`, `core/thread.py:123-245`).

## Goal

At any depth, a tool call that the calling agent's resolved policy requires to confirm pauses the whole run. The waiting step is shown where the call happened. The user's decisions resume the call and then every agent above it. All of this works after a reload.

**Principle:** everything this story does not cover behaves as on main. These are the intended exceptions:
1. The batch rule (rule 2).
2. Confirmation for a single assistant with smart selection (AC23).
3. An `AIToolsAgent` sub-assistant that requires confirmation is excluded. Main does not refuse it (rule 6).
4. Pending records written by main's code are cleared instead of resumed (rule 8).

## Approach

1. **One gate: `post_model_hook`.** Every LangGraph agent whose own resolved policy requires confirmation gets a gate in `post_model_hook`.
   - **Attachment.** The call-identity step (rule 3) is attached to every agent of a tree whose `tree_requires_confirmation` is set. The interrupt is attached only to agents with `pauses_own_calls`.
   - **When it runs.** After the model and before routing, so before any tool task is scheduled. It calls `interrupt()` at most once per AI message, listing every undecided call.
   - **Where it is attached.**
     - the standard `create_react_agent`;
     - the inner agent that smart selection builds (`smart_react_agent.py` `_create_sub_agent`);
     - the supervisor's own react agent, through `create_supervisor(post_model_hook=…)` (`langgraph_supervisor/supervisor.py:221`).
   - **Plumbing.** CodeMie passes the hook through none of these today, so that plumbing is in scope.
   - **What is removed.** The static `interrupt_before` path, `_bake_interrupt_into_supervisor_node`, and the mixin's `state.next` detection, `reject_tool_call` and `deny_unreviewed_sibling_tool_calls`. There is one apply mechanism (rule 3).
   - **Requirements on the gate.** It is deterministic and has no side effects before `interrupt()`. Graph names are unique: a smart-selection graph is not named `"LangGraph"`.
   - **Tool ids.** `ToolRegistry` ids are deterministic (name plus toolkit, not `uuid4`). On resume, selected ids that do not resolve are a structural mismatch (AC10), and nothing falls back to all tools.
   - **Rejected alternatives.**
     - A per-call v2 `ToolNode` gate runs a safe sibling twice at depth (spike, "Decision carrier and batch rule", Q2).
     - A `version="v1"` `ToolNode` gate loses v2's per-call `Send`.
2. **The batch rule.** This is an intended change at all depths, including the top level.
   - **Unit of decision.** The gate decides over the whole AI message. If any call in it needs confirmation, the gate interrupts and nothing runs in that superstep. Safe calls of a mixed batch wait for their unsafe siblings.
   - **Ask for approval.** Every call is shown.
   - **Approve for me.** Safe calls are recorded as decided `allow` and never shown. Each unsafe call is shown.
   - **Deciding.** All calls of a pause, including those of parallel siblings, are decided one request at a time, and nothing runs meanwhile. The last decision triggers one resume that applies them all.
   - **Resume.** The gate returns a denial `ToolMessage` for each denied call. The prebuilt `post_model_hook_router` `Send`s only the allowed calls, or routes back to the model if every call was denied. Every call runs exactly once.
   - **What it replaces.** Today's top-level rule shows `tool_calls[0]` whatever its safety and silently denies the rest (`mixin:61-79`, `:140-163`). Single-call turns are unchanged.
   - **Tests.** No existing test encodes the old rule. The mixin-API tests (`tool_confirmation/test_ask_for_tool_confirmation_approve_for_me.py`, and the run-config tests in `test_langgraph_tool_confirmation.py`) are rewritten for the gate with the same single-call expectations.
3. **One identity per decision, one apply mechanism.**
   - **Decision item.** Each call of a pause has one decision item, which is the only home of its decision. It holds:
     - `thought_id`, the API address;
     - `tool_call_id`;
     - `checkpoint_ns`: the gate task's own namespace, including its `post_model_hook` segment;
     - the call's display event: a full `ToolCallPendingEvent` with name, args, display name and `location`;
     - `decision`: `allow`, `deny` or `None`.

     One interrupt covers one AI message of one agent.
   - **Applied decision vs display copy (AR-3).** The decision that is applied is the one in the decision item, and nowhere else. The saved thought's `decision` is only a display copy of it, and the two are always written together. The resume seeds the decisions from the items, never from the thoughts.
   - **Carrier (spike-verified).**
     - The decisions travel as `config["configurable"]["codemie_tool_decisions"] = {call identity: {"action", "ns"}}`, keyed by the call identity defined below.
     - The gate checks the carried decision before any policy classification. Checking the policy first lost a Deny.
     - A gate uses an entry only when the entry's `ns` is its own namespace.
     - A call still undecided after the resume is denied, so the gate fails closed. Deny never runs the call.
   - **Command.** `Command(resume={every pending interrupt id: "ack"})`. The values are ignored. Stored ids are only compared with the pending ones, and a difference is a structural mismatch.
   - **Call identity (AR-1).** The gate defines each call's identity once, before it interrupts or routes, and makes it unique within the pause.
     - **Rewritten ids.** An id is rewritten deterministically when it is already answered by a `ToolMessage` in this agent's state, or when it repeats inside the AI message (judged by position, empty ids included).
     - **Below the root.** The identity also carries the agent's location, so two siblings never share one.
     - **One identity for every consumer.** No consumer derives its own. The consumers are: the decision items, the carrier, the `ToolMessage`s, `thought_id`, the streamed tool and handoff steps, their results, and rule 10's container.
     - **How it is computed.** The identity is one function of the agent's messages and its location, defined once.
       - Every agent of a tree whose `tree_requires_confirmation` is set applies it in its `post_model_hook`. Only an agent with `pauses_own_calls` also interrupts.
       - The stream starts each tool and handoff step from that identity, on a pausing pass as on any other.
       - The pending event's `pending_tool_call_id` and `all_tool_call_ids` carry it.
     - **At the root.** A call whose id is not repeated and matches `^[a-zA-Z0-9_-]{1,40}$` keeps today's id and `thought_id` (AC11). An id that does not match (empty, unsafe characters, over 40 characters) is rewritten at every level, the root included (Revised at code review, CR-004).
     - **Left to the plan.**
       - Whether the location is a suffix in the rewritten id or a composite key.
       - Whether the stream applies the function itself or reads the interrupt payload.

       Either way, an id the model sees (the AI message's call and its `ToolMessage`), rewritten or not, stays within `[a-zA-Z0-9_-]` and the providers' length limit. Where the location does not fit, it is a composite key beside the id, not part of it. A test resumes a nested two-round call on each configured provider family.
   - **Later calls in the turn.** Calls without a carried decision are classified by each agent's rebuilt policy, clamped with `ToolCallPolicy.stricter` to that agent's stored policy.
4. **The stored record.**
   - **Header.** `pending_tool_call` holds a `StoredPendingToolCall` with a producer-neutral header: `format: 2`, the decision items, `root_assistant_id`, the claim and `producer`.
   - **LangGraph payload.**
     - the interrupt ids;
     - each path agent's node name and resolved policy;
     - `root_version`, `sub_assistants_versions`, `skill_ids`, `hide_tool_outputs`, `llm_model` and `original_user_message`;
     - `request_tool_call_policy`: the request override exactly as received, possibly `None`, restored as `request.tool_call_policy`. A resolved policy is never carried as an override.
   - **API (AR-2).** Every reader that returns the pending event uses one projection of the header: the conversation GET, the resume and the stream. For `format: 2`, the projection is the current undecided item's event. For the old format, it is the event as stored, so a click reaches the AC11 error.
   - **Resume.**
     - A pending record selects `ConversationCheckpointSaver` and gates the stored path, whatever the rebuilt tree says.
     - The resume applies the root version and skills as `_prepare_assistant_for_execution` does, and the stored hide flag, which closes the gap at `execution.py:268-277`.
     - The resumed request carries every restored key.
   - **Structural mismatch (AC10).** The resume fails with an error naming the agent, and the record is cleared, only if an agent on the path, its node name, a pinned version, the waiting tool or an interrupt id is gone or changed. A policy change is not an error.
5. **Resolve the policy once.** `build_agent` resolves the effective permissions right after `_apply_conversation_runtime_overrides` (`assistant_service.py:543`), before tool assembly (`:574`, `:591`) and before the class choice (`:615`).
   - **Derived there.** `pauses_own_calls` and the exclusion (rule 6). The result is passed into `configure_agent_kwargs` instead of being resolved again (`assistant_engine_builder.py:298`).
   - **Tree flag.** `tree_requires_confirmation` is `pauses_own_calls` OR any child's flag.
     - It selects only the saver and detection.
     - The turn-start clear (AR-4) runs whenever the conversation has a pending record, whatever the tree flag.
   - **Conversation row.** Only the root creates it.
6. **Channels.**
   - **Root.** The root keeps main's input: `allow_tool_confirmation=True` for any LangGraph root (`assistant_service.py:648`), and a workflow root keeps `False` (`:936-938`).
   - **Sub-assistants.** `can_resume_pause`, set by the caller, affects only how sub-assistants are built:
     - true for the stream path (`assistant_handlers.py:663`) and the resume path (`:1308`);
     - for the hedged path (`hedged_handler.py:485`), carried to the plan;
     - false for sync (`:1207`), background (`:1146`), workflow nodes, the A2A server (`codemie_task_manager.py:277`) and the health check (`assistant_health_check_service.py:305`).
   - **Excluded.**
     - Under a root with `can_resume_pause` false: a sub-assistant whose effective policy (own, override or customer minimum, the same input as `langgraph_agent.py:373`) requires confirmation.
     - In every channel: an `AIToolsAgent` sub-assistant (`:615`) that requires confirmation.

     This matches a refused LangGraph sub-assistant on main, except that the excluded one gets no handoff tool and is left out of the descriptions. A warning names the sub-assistant, its policy and the channel. The rest of the orchestrator builds normally.
7. **Depth and cycles.** Any depth is supported.
   - **Mechanics.** Namespaces nest and `agent_path` holds the full chain. Detection reads the outer `get_state(cfg).tasks[].interrupts`, skipping tasks whose `result` is not `None`.
   - **Cycles.** An assistant that appears again on its own build chain (A → B → A) is refused with an error naming the cycle.
   - **Save-time rule.** The save-time rule (`assistant_integration_validator.py:100`) is a follow-up.
8. **Saver and record formats.**
   - **Checkpoint shape.** `pending_checkpoint` holds `{"namespaces": {ns: {checkpoint, metadata, writes}}}`, keeping the latest entry per namespace. `get_tuple` returns `thread_id`, `checkpoint_ns` and `checkpoint_id`.
   - **Writes.**
     - They are keyed by `(checkpoint_id, task_id, idx)`.
     - `__interrupt__` and `__resume__` are replaced, not appended (`WRITES_IDX_MAP`).
     - Nothing depends on the order of `put` and `put_writes`: `put(N)` drops only older writes, writes for an older checkpoint are ignored, and `get_tuple` returns only the stored checkpoint's writes.
   - **Statements.** Each saver call is one atomic column `UPDATE`, which builds missing parents (`coalesce`) before the `jsonb_set`.
   - **Config and cleanup.** `__pregel_checkpointer` stays at the top level of the config. The row is cleared when the run completes.
   - **Old-format records (AR-1).** These are records written by main's code: a static-breakpoint checkpoint and a pending record without `format: 2`. They come from chats waiting at deploy time and from old pods during a rolling deploy. For single assistants and orchestrators alike, they are cleared with an AC10-style error asking the user to send the request again. No tool runs, on Allow or on Deny.
   - **Old pods fail closed** on new-format records (verified against main's parsing):
     - main's saver validates `pending_checkpoint` as `StoredCheckpoint`, which requires top-level `checkpoint` and `metadata` strings (`conversation_checkpoint_saver.py:31-35`, `:59`);
     - main's service builds `ToolCallPendingEvent(**data)`, which requires `pending_tool_call_id`, `tool_name` and `tool_args` (`models.py:20-27`, `conversation_checkpoint_service.py:53`).

     The new shapes never carry any of these keys at the top level, so an old pod raises a validation error in `_resume_tool_call` (`execution.py:216`) or `get_tuple` before any agent is built or any call runs. An old pod's conversation read of such a row may also error during the window.
9. **Deciding, claiming, authorising.**
   - **Authorisation (AR-2).** A final `AssistantRequestHandler` method runs before the existing hook `handle_tool_call_resume(request, conversation)`, which is declared on the base. It first checks READ on the assistant, WRITE on the conversation and membership, as on main (moved from `assistant_handlers.py:1272-1283`). Then, in this order:
     1. No record gives 404. This check runs for every decision, final or not.
     2. An old or unknown format (no `format: 2`, or a format this code does not know) is cleared with the AC11 error.
     3. `root_assistant_id` is checked, on format-2 records only, before the saved marker and the compare-and-set.
   - **Saved marker (AR-3).** A decision is accepted only after the pausing turn's save has completed. The header carries a saved marker, written after the chat history. Before it is set, the answer is 409 "still saving, retry", and nothing changes.
   - **Recording a decision (AR-3).** One compare-and-set `UPDATE` on `pending_tool_call` writes `decision` on the item whose `thought_id` matches. It matches only if that item is undecided and the record is not claimed; otherwise the answer is 409 and nothing changes.
     - **Requests without `thought_id`.** They are accepted only when the pause has exactly one shown item.
     - **A non-final decision** runs nothing. In the same request, it updates the turn's saved thoughts at `history_index`, seeded from them as the resume is: the decided step gets `decision`, and the next item's waiting step is added under its container. No new history entry is created. That waiting step is then streamed. On reload the UI reads both from the chat history. The item and the thoughts are written together or not at all.
     - **The final decision** sets the claim, and the hook is called once.
   - **Handlers.** The 404 checkpoint check moves from `execution.py:203-221` into the ordered checks above. `A2AAssistantHandler` keeps refusing. A CodeMinion handler only overrides the hook.
10. **Placement and the step tree.**
    - **Container.** A waiting step's `parent_id` is its immediate container:
      - at the root, its tool-call step, as today;
      - for S called by O, the step O → S;
      - for O → O2 → S, the step O2 → S;
      - for O2's own call, the step O → O2.

      The container id is `_tool_call_id_to_uuid` of the latest matching handoff message (`supervisor/history.py:321-339`).
    - **Step tree.** `core/thread.py` uses one recursive find for both paths. An applied decision replaces the prompt and clears `interrupted`. An orphan step is kept at the top level with its `parent_id`, and `consumer_aborted` stays.
    - **Resume.** It seeds the generator with the turn's saved thoughts and binds each path agent again to its container.
    - **Hidden tool outputs.** `tool_output_visibility.py` keeps `parent_id` and walks the tree.

## Contract (the backend defines it, EPMCDME-16464 consumes it; additive)

**Waiting step (the prototype's contract).**
- It is a `Thought` with `interrupted=True` and `parent_id` set to its immediate container. The UI addresses it by its `id`.
- A step that is decided but whose pause is not yet applied carries `decision: "allow" | "deny"`, keeps `interrupted=True` and has no result. It is saved in the chat history (rule 9).
- After the resume, the step keeps its id and `interrupted` is cleared.

**Event.**

```python
class PendingCallLocation(BaseModel):   # one per decision; EPMCDME-16342's list carries one per item
    agent_path: list[str]                # stable keys, root's child first: assistant id, or builtin "__builtin__<value>"
    agent_names: list[str]               # display only
    parent_thought_id: str               # == the waiting step's parent_id

class ToolCallPendingEvent(...):        # existing fields unchanged
    location: PendingCallLocation | None = None   # None at the root; a single assistant's event is identical to today
```

**Builtins.** Builtins are clones with `id=None` (`assistant_engine_builder.py:160-178`, `:222-224`), so their key is the builtin name.

**Request.** `ToolCallResumeRequest` gains an optional `thought_id`. `checkpoint_ns` and the interrupt ids never appear in the API.

**Release constraints.**
- The backend and the UI (EPMCDME-16464) deploy together, with no feature flag.
- The overlap of a rolling deploy is accepted. It is short, no data is lost, and old pods fail closed (rule 8).

## Acceptance criteria

- AC1: With S on ask-for-approval and O on any policy, S's call pauses under O → S, with `location={agent_path:[S key], parent_thought_id}`.
- AC2: Allow runs the call; then S and O finish.
- AC3: Deny does not run the call. S gets the denial, then S and O finish.
- AC4: After a reload, with a fresh build from saved state on Postgres, Allow and Deny work, the interrupt ids are reproduced, and the resumed steps are nested.
- AC5: Approve-for-me with a mixed batch: the gate interrupts once per AI message and nothing runs before the decision. Safe calls are never shown. Each unsafe call is shown and runs only on Allow. Every call runs exactly once, at the top level, at depth, under smart selection and under smart selection at depth.
- AC6: The customer minimum and the override apply to S.
- AC7: In channels that can resume a pause, no LangGraph agent is excluded because of its policy (the `AIToolsAgent` case is AC16).
- AC8: The step tree finds a parent at depths 1–3. An applied step is replaced and its `interrupted` cleared. An orphan step is kept.
- AC9: Parallel siblings that are both paused:
  - The calls are shown one at a time, nothing runs before the last decision, and one resume applies all of them, so each tool runs once.
  - A stale `thought_id` gets 409. An Allow without a `thought_id` on a two-call pause gets 409 and decides nothing.
  - On Postgres, 50 runs each: `get_state` after a reload finds the interrupts; S1 reads while S2 pauses, and S1's tool runs once.
- AC10: Only a structural mismatch fails, with an error naming the agent, and clears the record.
- AC11: A single call at the top level whose id is not repeated and matches `^[a-zA-Z0-9_-]{1,40}$` behaves as today: same event, same step, `location=None`. An id outside that pattern is rewritten (Revised at code review, CR-004).
  - An old-format record, single assistant or orchestrator, is cleared with the error, and no tool runs on Allow or on Deny. The fixture for this test is written by main's code shape (a static-breakpoint checkpoint), not built on the new graph.
  - The conversation GET succeeds for both an old-format row and a new-format row.
  - The test posts to the resume endpoint both with and without `thought_id`.
  - The resume request uses an assistant id that is a member of the conversation.
- AC12: Backend tests cover every AC.
- AC13: A smart-selection S that confirms pauses and resumes with the same tool set.
- AC14: With hide tool outputs on, the nested waiting step keeps its `parent_id` and its ancestors.
- AC15: S makes two guarded calls in one AI message:
  - its gate interrupts once and the calls are shown one at a time;
  - after the first decision, a reload shows the first step decided and the second waiting, both read from the chat history;
  - one resume applies both decisions, and each call runs once;
  - a decision sent before the pausing turn's save completes gets 409 and changes nothing.
- AC16: Under sync or background chat, a workflow node, the A2A server or the health check, an S whose effective policy requires confirmation is excluded. It has no handoff tool, is not in the descriptions, and a warning names S, its policy and the channel. O still answers.
  - An `AIToolsAgent` S that requires confirmation is excluded the same way in every channel.
  - An A2A or health-check root on ask-for-approval still never runs its write call.
  - A workflow root runs as on main.
- AC17 (contract, checked at review): every waiting step has `interrupted=True` and `parent_id` = its immediate container, and it is addressed by its id. It carries `decision` until its pause is applied, and it keeps its id.
- AC18: The router has no checkpoint logic. A test handler's hook is reached through `get_request_handler`, only after WRITE is checked, and once per pause.
- AC19: If S is switched to auto-approve, or the permissions flag is turned off, during the pause, Deny still does not run the call. This holds at the root, at depth and under smart selection. Later calls are clamped to the stored policy. Old-format records follow AC11. If the user sends a new message after confirmation is turned off, the earlier pause is gone, and an Allow on it gets 404 with no tool run.
- AC20: A builtin sub-agent pauses and resumes.
- AC21: The chain O → O2 → S works end to end:
  - a pause at S goes under O2 → S;
  - Allow, Deny and reload work;
  - parallel pauses at depth 2 work;
  - O2's own call goes under O → O2.
- AC22: A → B → A is refused with an error naming the cycle.
- AC23 (intended change): a single assistant with smart selection and a confirming policy pauses.
- AC24: Of two concurrent Allows on the last item, one runs and the other gets 409. The tool runs once.
- AC25: A consumer abort (EPMCDME-16233) during a resumed nested run leaves the record either cleared or decidable.
- AC26: With a replayed handoff (EPMCDME-14859), the waiting step goes under the latest delegation step.
- AC27: Each of the following is shown, decided and run once, with no rejection:
  - two siblings of one pause whose calls share an id;
  - two calls with one id in one AI message;
  - a call id already answered earlier in the agent's state.

  The streamed step, the waiting step, its container, the result and the Allow address all carry the same call identity. A carried decision is never applied by a gate in another namespace.
- AC28: Given a new-format record, main's code (old-pod fixture) raises a validation error on resume and no tool runs.

## Non-goals

- The UI (EPMCDME-16464) and the CodeMinion handler.
- A multi-item pending contract (EPMCDME-16342).
- Confirmation in workflow nodes, the A2A server, the health check, sync chat and background chat.
- Confirmation for `AIToolsAgent` agents.
- The save-time depth rule.
- Changes to the policy model.
- Sub-assistant drops for other reasons (EPMCDME-14900).
- A migration, a depth limit, or resuming old-format records.

## Decisions

Taken by the user:
- One dynamic-`interrupt()` gate everywhere, over static `interrupt_before`. — user
- The gate on the smart-selection inner agent, over turning smart selection off. — user, spike
- `langgraph_supervisor` kept, over a CodeMie sub-assistant node. — user, spike
- Calls of a pause decided one at a time and applied in one resume. — user, spike
- Approve-for-me runs safe calls and asks for each unsafe one, and ask-for-approval shows every call, at all levels, over "first call, deny the rest". — user
- Main's behaviour kept outside the story, except for the four named exceptions. — user
- The root's confirmation input kept as on main in every channel. — user
- Sub-assistants in non-resumable channels, and `AIToolsAgent` sub-assistants that require confirmation, excluded cleanly with a warning. — user
- Backend and UI deploy together with no flag; the rolling-deploy overlap is accepted, with old pods failing closed. — user
- Old-format records cleared with an error for single assistants and orchestrators, over a legacy `update_state` branch. — user
- A namespace map in the existing JSONB, over a new table. — user
- A structural mismatch fails and clears, over resuming anyway. — user
- Any depth, with cycles refused. — user
- The waiting step goes under its immediate container. — user
- An optional `thought_id` with 409. — user
- The existing hook, with authorisation in the base class. — user
- The location contract is fixed now. — user
- A repeated tool-call id rewritten deterministically by the gate, over a live check of every endpoint or a run failure on repeats. — user

Taken in the spec:
- The gate placed in `post_model_hook`, over a per-call or v1 `ToolNode` gate. — spike
- The carried decision checked before the policy; carrier `codemie_tool_decisions` keyed by the gate's call identity, with `ns` = the gate task's namespace. — spike
- Fail closed for old pods through main's required fields, plus `format: 2`, over a version check that main does not have.
- A non-final decision saved both in the header and in the turn's saved thoughts, over keeping it in the header only.
- The decision item as the single home of a decision.
- An id-less request accepted only for a one-item pause.
- The request override stored as received, plus a policy per agent.
- The policy resolved once, before tools and agent class.
- `can_resume_pause` set per caller and used for sub-assistants only.
- Deterministic tool ids, with no fallback on resume.
- Stable path keys; the container taken from the latest handoff; pending state per instance.

## Risks and follow-ups

- **Story copy.** The story copy is updated (`story.md`). Syncing it to `docs/stories/` and to Jira EPMCDME-16461 is a follow-up.
- **Carried to the plan:**
  - AR-4: column-level writes of `pending_tool_call` and `pending_checkpoint`, and claim release on every exit path.
  - AR-5: exclusion enforced in one list; descriptions derived only from built entries.
  - STR-3: toolkit lookup inputs on resume.
  - STR-4: a build context object.
  - MOD-1 and MOD-2.
  - REU-1: the save-rule ticket.
  - The hedged handler.
  - The agent's location for the call identity: build it from the task namespace (per instance), not from `agent_path` alone. Test that two parallel handoffs from O to the same S both pause on one id and that the location is identical on resume.
  - Stream chunk order on the pausing pass: log the chunk order with the pinned langgraph and `subgraphs=True`, and confirm that a paused message's steps start before its waiting step, both at the root and nested.
- **Not checked.** Remote (Bedrock) sub-assistants; saver cost over long histories.
- **Accepted side effects.**
  - Safe calls of a mixed batch wait for their unsafe siblings.
  - Chats waiting at deploy time are asked again.
- **Security gate.** A gate that is not deterministic, or that classifies a call before reading its carried decision, lets write calls run unconfirmed.
