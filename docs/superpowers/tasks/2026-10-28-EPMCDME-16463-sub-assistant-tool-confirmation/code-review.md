---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: ebce9878e7159cf994e6eacecd7e4f8a5ba63ed2
---

# Code review — 2026-10-28-EPMCDME-16463-sub-assistant-tool-confirmation

**Decision:** approve (check round, confidence final low · check medium)  
**Reviewed head:** `ebce987` · **Rounds:** final → check

**Closed:** 18 fixed · 0 moot · 1 decided

All 19 prior blocking findings are resolved: 18 code findings confirmed in current source and the added tests, and CR-004 by the recorded user decision to keep rewriting unsafe ids (the spec amendment is still open for the controller). Confidence is medium, not high, because the live-Postgres tests for CR-017, CR-018 and CR-019 run only when TEST_LOCAL_PG_URL is set and were not run here, and CR-002's assumed smart-selection gate namespace was matched in code, not observed at runtime. The brief was written to code-review-brief.local.md.

## Open — 0

## Decided — 1

- **CR-004** · Unsafe non-repeated root ids are rewritten — `src/codemie/agents/tool_confirmation/call_identity.py:90` — **Keep the current behaviour: call ids failing ^[a-zA-Z0-9_-]{1,40}$ are rewritten at every level, including a single root call; no code change; the spec (Rule 3 AR-1, AC11) is amended by the controller.** User decision: a rewritten id always satisfies provider id limits, which is safer than forwarding an unsafe id unchanged at the root.

## Pre-existing (not blocking) — 1

- **Tool is_safe exception crashes the gate** — `src/codemie/agents/tool_confirmation/confirmation_gate.py:102` — `tool_call_is_safe` calls the tool's `is_safe(args)` with no exception handling; a tool whose `is_safe` raises crashes the post_model_hook and the run instead of failing closed to "needs confirmation". Pre-existing: main's `ToolCallConfirmationMixin._tool_call_is_safe` (mixin line 261-263 on main) calls `tool.is_safe(...)` unguarded the same way; the change moved the behaviour, it did not introduce it.

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| Rule 1: one gate in post_model_hook on standard, smart inner and supervisor agents | pass | post_model_hook wired in langgraph_agent.py and smart_react_agent.py |
| Rule 1: identity step on every agent of a confirming tree; interrupt only where pauses_own_calls | pass | _confirmation_hook; with_call_identity rebuild |
| Rule 1: gate after model, before routing; one interrupt per AI message | pass | confirmation_gate.confirmation_hook |
| Rule 1: remove interrupt_before, baked supervisor interrupt, state.next detection, reject/deny helpers | pass | removed |
| Rule 1: gate deterministic before interrupt(); smart graph not named LangGraph | pass | compile(name=name) |
| Rule 1: deterministic ToolRegistry ids; unresolved selected ids are a structural mismatch | pass | smart_tool_state.py, _resolve_available_tools |
| Rule 2: any call needing confirmation interrupts the whole AI message | pass | gate interrupts if any decision is None |
| Rule 2: ask shows every call; approve-for-me auto-allows safe calls, never shown | pass | call_needs_confirmation |
| Rule 2: calls decided one request at a time; last decision triggers one resume | pass | record_decision CAS + claim |
| Rule 2: denial ToolMessage per denied call; router sends only allowed; each call runs once | pass | gate denials; nested tests |
| Rule 2: mixin-API tests rewritten for the gate | pass | rewritten |
| Rule 3: one decision item per call with thought_id, tool_call_id, checkpoint_ns, event, decision | pass | DecisionItem |
| Rule 3 AR-3: applied decision only from the item; thought decision is a display copy | pass | handler builds decisions from record.items |
| Rule 3: carrier codemie_tool_decisions keyed by identity, own ns only, undecided denied | pass | build_decision_carrier, _carried_decisions |
| Rule 3: Command(resume={interrupt id: ack}); id difference is a structural mismatch | pass | _check_pending_interrupts |
| Rule 3 AR-1: ids rewritten only when answered or repeated; non-repeated root id keeps today's id | partial (final round) | assign_call_identities also rewrites ids failing ^[a-zA-Z0-9_-]{1,40}$, root included; see CR-004 |
| Rule 3: identity carries agent location below root; one identity function; stream starts steps from it | pass | CallIdentity; event adapter deferral |
| Rule 3: model-visible ids within [a-zA-Z0-9_-]; nested two-round call per provider family | pass | provider-family test present |
| Rule 3: later calls clamped with ToolCallPolicy.stricter to stored policy | pass | ResolvedToolPolicy.clamped_to; stricter exists in core/models.py |
| Rule 4: StoredPendingToolCall header: format 2, items, root_assistant_id, claim, producer | partial (final round) | no header-level producer; only per DecisionItem; see CR-006 |
| Rule 4: LangGraph payload with interrupt ids, path agents, policies, versions, skills, hide flag, model, request policy | pass | build_resume_payload |
| Rule 4 AR-2: one header projection for GET, resume and stream; old format as stored | pass | pending_event projection |
| Rule 4: pending record selects saver and gates stored path; resume restores root version, skills, hide flag | pass | _restored_chat_request, _restored_assistant |
| Rule 4 AC10: only a structural mismatch fails and clears; policy change is not an error | pass | check_stored_path |
| Rule 5: effective permissions resolved once after runtime overrides, passed to configure_agent_kwargs | pass | build_agent_handle |
| Rule 5: tree_requires_confirmation = own OR any child; selects saver and detection | pass | tree_confirms |
| Rule 5 AR-4: turn-start clear whenever a pending record exists | pass | _guard_conversation_not_finished; see CR-012/CR-013 for its guards |
| Rule 5: only the root creates the conversation row | pass | configure_agent_kwargs |
| Rule 6: root keeps main's input; workflow root False | pass | allow_tool_confirmation per channel |
| Rule 6: can_resume_pause true for stream/resume/hedged, false for sync, background, workflow, A2A, health | pass | BuildChannel; wiring untested, see CR-014 |
| Rule 6: exclude confirming sub-assistant under non-resumable root and AIToolsAgent everywhere, with warning | pass | subagent_exclusion |
| Rule 7: any depth; namespaces nest; detection skips tasks with a result | pass | _pending_gate_interrupts |
| Rule 7: A -> B -> A refused naming the cycle | pass | SubagentCycleException |
| Rule 8: pending_checkpoint holds namespaces, latest per ns; get_tuple ids | pass | NamespaceCheckpointSaverBase |
| Rule 8: writes keyed by (checkpoint_id, task_id, idx); special keys replaced; order independent | pass | put_writes keys; SQL untested on Postgres, see CR-017 |
| Rule 8: each saver call one atomic column UPDATE with coalesce | pass | _WITH_NAMESPACES |
| Rule 8: __pregel_checkpointer top-level; row cleared when the run completes | partial (final round) | no clear on non-pausing completion; only stream start, resume finally, turn-start, errors; see CR-001 |
| Rule 8 AR-1: old-format records cleared with AC10-style error; no tool runs | pass | _pending_record_or_raise |
| Rule 8: old pods fail closed on new-format records | pass | test_old_pod_models_raise_on_a_format_2_row |
| Rule 9 AR-2: final base method authorises, 404, old format cleared, root id, before saved marker and CAS | pass | resume_tool_call ordering |
| Rule 9 AR-3: decision accepted only after saved marker; else 409 | pass | record.saved + SQL condition |
| Rule 9 AR-3: one CAS UPDATE on matching undecided item of unclaimed record; else 409 | pass | _RECORD_DECISION_SQL; untested on Postgres, see CR-018 |
| Rule 9: requests without thought_id accepted only when the pause has exactly one shown item | partial (final round) | handler and SQL count all items incl. auto-allowed; see CR-010 |
| Rule 9: non-final decision runs nothing, patches saved thoughts, streams waiting step | pass | _decided_thoughts + thoughts_patch |
| Rule 9: final decision claims, hook called once; 404 check moved; A2A refuses | pass | _call_resume_hook; claim release gap see CR-015 |
| Rule 10: waiting step parent_id is its immediate container (latest handoff) | pass | _pending_location |
| Rule 10: one recursive find in core/thread.py; applied decision replaces prompt; orphan kept | pass | thread.py _find_thought |
| Rule 10: resume seeds generator with saved thoughts and binds each path agent to its container | pass | lens pass; reviewer found depth>=2 lookup is top-level only, see CR-008 |
| Rule 10: tool_output_visibility keeps parent_id and walks the tree | pass | _approval_tree |
| Contract: waiting step Thought interrupted=True, parent_id container, decision until applied | pass | Thought.decision |
| Contract: PendingCallLocation and location None at root | pass | models.py |
| Contract: builtin path key __builtin__<value> | pass | path_key |
| Contract: ToolCallResumeRequest optional thought_id; no ns/interrupt ids in API | pass | openapi baseline updated |
| Release: no feature flag; old pods fail closed | pass | no flag |
| AC1: S pauses under O -> S with location | pass | test_nested_confirmation.py |
| AC2: Allow runs the call; S and O finish | pass | resume tests |
| AC3: Deny does not run; S gets denial; S and O finish | pass | deny tests |
| AC4: after reload, fresh build from saved state on Postgres; Allow and Deny work | partial (final round) | reload tests use in-memory FakeCheckpointService only; see CR-017 |
| AC5: approve-for-me mixed batch interrupts once, safe never shown, every call once | pass | approve_for_me and smart tests |
| AC6: customer minimum and override apply to S | pass | resolve_tool_policy per sub-assistant |
| AC7: resumable channels exclude no LangGraph agent for its policy | pass | test present |
| AC8: step tree finds parent at depths 1-3; applied step replaced | pass | test_thread.py |
| AC9: parallel paused siblings one at a time, one resume; 409 cases; Postgres 50 runs | partial (final round) | Postgres 50-run test absent; see CR-019 |
| AC10: only structural mismatch fails, naming agent, clears | pass | tests present |
| AC11: single top-level call behaves as today; old format cleared; GET; resume with/without thought_id | pass | tests present; unsafe-id rewrite see CR-004 |
| AC12: backend tests cover every AC | partial (final round) | no Postgres tests for AC4/AC9; no AC26 test; see CR-003, CR-017, CR-019 |
| AC13: smart-selection S pauses and resumes with the same tool set | pass | test present |
| AC14: hide tool outputs keeps nested waiting step parent_id and ancestors | pass | test present |
| AC15: two guarded calls of S: one interrupt, one at a time, reload, one resume, early 409 | pass | tests present |
| AC16: non-resumable channels exclude confirming S; AIToolsAgent S excluded; workflow as main | pass | test_assistant_factory.py |
| AC17: every waiting step interrupted, parent_id container, by id, decision until applied | pass | see Contract row |
| AC18: router has no checkpoint logic; hook via get_request_handler after WRITE | pass | execution.py |
| AC19: policy relaxed during pause: Deny still does not run; later calls clamped; new message clears | pass | tests present |
| AC20: builtin sub-agent pauses and resumes | pass | test present |
| AC21: O -> O2 -> S end to end incl. reload and parallel at depth 2 | pass | tests present; depth-2 rebind gap see CR-008 |
| AC22: A -> B -> A refused naming the cycle | pass | tests present |
| AC23: single smart-selection assistant with confirming policy pauses | pass | test present; step start see CR-002 |
| AC24: two concurrent Allows on the last item: one runs, other 409 | pass | tests present (fake CAS) |
| AC25: consumer abort during resumed nested run leaves record cleared or decidable | pass | test present; pre-stream disconnect see CR-015 |
| AC26: replayed handoff: waiting step under the latest delegation step | pass | implemented via latest_handoff_identity; untested, see CR-003 |
| AC27: shared/repeated/answered ids each shown, decided, run once; carried decision ns-bound | pass | adapter and call_identity tests |
| AC28: main's code raises on a new-format record; no tool runs | pass | test present |
| Non-goal: no confirmation in workflow, A2A server, health check, sync, background; none for AIToolsAgent | pass | excluded |
| Non-goal: no migration, no depth limit, no resuming old-format records, no policy-model change | pass | JSONB reuse only |

Standards: commit-format pass · code-quality partial (final round) · security pass
