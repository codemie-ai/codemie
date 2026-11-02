---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: c19e41311847a4eb591ffa4494c0f1532eae4e80
---

# Code review — 2026-10-30-epmcdme-16411-unified-model-policy

**Decision:** approve (check round, confidence final low · check high)  
**Reviewed head:** `c19e413` · **Rounds:** final → check

**Closed:** 5 fixed · 0 moot · 0 decided

Targeted check: all 5 prior blocking code findings (CR-001..CR-005) are resolved; each was verified against current source in changed_files. No new findings are raised on this round; business_review and standards_review are carried forward from the final round unchanged.

## Open — 0

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| Design 1: ProjectModelPolicy unrestricted / deny-all / whitelist modes from raw Application | pass | ProjectModelPolicy.for_project; test_project_model_policy.py TestModes |
| Design 1: choose order (allowed candidate, default, first allowed); deny-all raises | pass | ProjectModelPolicy.choose; TestChoose |
| Design 1: choose logs+metric on substitution; check rejects without substituting | pass | _report_fallback and check(); TestChoose, TestCheck |
| Design 1: populated whitelist with no default still raises NoDefaultModelException | pass | for_project; test_whitelist_without_default_raises |
| Design 1: get_project_models keeps None distinct from [] | pass | ProjectModelConfig.allowed_models list\|None; test_unconfigured_project_keeps_none |
| Design 1: the five whitelist sites delegate to the policy | pass | dependecies, model_availability_service x3, toolkit_service delegate |
| Design 2: resolve_assistant_model called once per assistant in build_agent (root and children) | pass | assistant_service.build_agent; children via AssistantFactory.build |
| Design 2: _ask.py pre-resolution removed | pass | _resolve_chosen_execution_model deleted |
| Design 3: build_agent writes to neither assistant nor request | pass | write-back removed; conversation pick returned, not set on request |
| Design 3: resolved model passed explicitly into tools and agent kwargs | pass | llm_model passed to ToolkitService.get_tools and agent_kwargs |
| Design 3: built-in clones get the parent's resolved model explicitly | pass | _build_builtin_subagent_executor(resolved_model=...) |
| Design 3: unused AssistantFactory.llm_model removed | pass | removed from AssistantFactory and create_assistant_executors |
| Design 3: handlers and conversation_service read the resolved model from the returned agent | partial (final round) | handlers re-resolve via resolve_execution_model; see CR-001 |
| Design 4: supervisor descriptions filtered to built executor names | pass | assistant_engine_builder built_names filter |
| Design 5: agent-level re-resolution via policy.choose([model]) incl. workflow base_node | partial (final round) | base_node path uses choose([requested, fallback_model]); see CR-003 |
| Catalog: [] whitelist shows assistant's own model with no marker | pass | catalog._resolve_effective_llm_model; dedicated test |
| AC1: ProjectModelPolicy is the only implementation; None distinct from [] | pass | see Design 1 rows |
| AC2: build_agent resolves once per assistant; _ask.py no longer resolves | pass | assistant_service.build_agent; _ask.py hunk |
| AC3: inputs untouched; clones on parent's model; agent exposes model and readers use it | partial (final round) | readers re-resolve rather than use agent model; see CR-001 |
| AC4: unbuilt subagent missing from handoff tools and descriptions | pass | test_unbuilt_sub_assistant_is_absent_from_supervisor_descriptions |
| AC5: policy table test | pass | test_project_model_policy.py |
| AC6: same-project chain through real build_agent | pass | test_model_resolution_chain.py; test_assistant_factory.py |
| AC7: cross-project child runs on pick while parent substitutes | pass | test_child_in_project_allowing_pick_runs_on_pick_while_parent_substitutes |
| AC8: conversation-stored llm_model is the pick for root and children | pass | _user_model_pick; root and child tests |
| AC9: flag off, [] at execution and global behave as on main | pass | TestAssistantFactoryModelPolicy; catalog [] test |
| AC10: mutation-asserting tests rewritten; quality gates pass | pending-stage-7 | tests rewritten; gate results are Stage 7 evidence |
| Behaviour change 1: asset creation with None whitelist + default is unrestricted | pass | validate_model_for_asset_creation via policy; test pins it |
| Behaviour change 2: unallowed pick falls back to own llm_model_type before default | pass | choose([user_pick, llm_model_type]) |
| Behaviour change 3: cross-project children check the user's original pick | pass | AC7 test |
| Behaviour change 4: children honour conversation-stored llm_model | pass | AC8 test |
| Non-goal: no root resolution for build_agent_for_workflow | pass | not touched |
| Non-goal: no change to whitelist-without-default handling | pass | for_project still raises NoDefaultModelException |
| Non-goal: no write-back of resolved model into Conversation.llm_model | pass | conversation.llm_model set only from request fields_set |
| Non-goal: no REST contract, DB schema, UI or subagents.yaml change | pass | no such files in the diff |
| Non-goal: no change to ExtendedHTTPException or describe_exception | pass | untouched |

Standards: commit-format pass · code-quality partial (final round) · security pass
