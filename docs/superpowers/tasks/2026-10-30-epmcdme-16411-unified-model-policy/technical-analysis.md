# Technical Research

**Task**: project model whitelist policy assistant subagent model resolution
**Generated**: 2026-10-30
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-16411 (MR !4647, branch EPMCDME-16411_skill-subagent-model-whitelist) — address reviewer Yana Asadchaya's comment (note #2501663, 2026-10-30) on unifying project model enforcement, in this MR, no follow-ups.

Reviewer comment verbatim:
"The rule from my earlier comment still doesn't hold for sub-assistants, and the root model doesn't follow it either. Example: flag on, project whitelist [D, M] with default D, a sub-assistant configured with M, and the user picks X in the chat (X isn't in the whitelist). The rule says the sub-assistant should run on M. It runs on D, or on the orchestrator's own model if that one is allowed. Across projects, the child's whitelist is checked against the parent's replacement model, not against X. So a child whose project allows X still doesn't get it. The root has the same problem: _prepare_assistant_for_execution (_ask.py:130-132) resolves 'chat pick → project default' before build_agent runs. By the time build_agent sees assistant_model, it already holds that result, so the root never falls back to its own model either.

Root cause: the model is chosen in two places, by two different orders (_ask.py and build_agent). Inputs get overwritten with the result: assistant.llm_model_type and request.llm_model are rewritten in place (assistant_service.py:566-569); every child then receives the parent's result as if the user had picked it. The write-back into request only existed to feed the AssistantFactory pre-check, which this MR removes. The whitelist policy is implemented five times with different semantics: enforce_allowed_model, resolve_requested_model, resolve_model_for_execution, validate_model_for_asset_creation and _ensure_project_allows_model. get_project_models collapses None into [], so each place has to tell them apart again, and they don't agree. resolve_requested_model treats [] as unrestricted while the others treat it as deny-all.

Proposed fix (in this MR, no follow-ups):
1. One ProjectModelPolicy (unrestricted / deny-all / whitelist) with choose(candidates), which logs and records a metric when it substitutes a model, and check(model), which rejects without substituting. All five call sites use it, and get_project_models stops collapsing None.
2. One resolve_assistant_model(assistant, user_pick) = policy.choose([user_pick, assistant.llm_model_type]), called once per assistant in build_agent for the root and for every child. Remove the pre-resolution in _ask.py.
3. Inputs stay untouched. request.llm_model always means the user's pick, and the resolved model is passed on explicitly. Built-in clones get the parent's resolved model explicitly.
4. The supervisor's subagent descriptions are built from the executors that actually got built, so a subagent that failed to build isn't described.
5. A test for the parent → child chain through the real build_agent, in the same project and across projects, plus a table test for the policy."

Agreed rule from earlier review (same for every child — built-in subagent or assistant_ids sub-assistant — and the root): chat-picked model if the assistant's project whitelist allows it → the assistant's own llm_model_type if allowed → project default → first allowed. Flag off, unconfigured whitelist (None), [] (deny-all) and global assistants keep current handling, so default deployments behave as on main. Also note conversation-stored llm_model (_apply_conversation_runtime_overrides) currently stores resolved model, not user pick — consider.

---

## 2. Codebase Findings

### Existing Implementations

Policy core, `src/codemie/service/llm/model_availability_service.py`:
- `PROJECT_MODEL_OVERRIDE_FEATURE = "projectModelOverride"` (L28); `ProjectModelConfig(allowed_models: list[str], default_model: str)` (L31-36).
- `is_model_allowed(identifiers, allowed_models)` (L43) — set-membership helper, also used by `llm_service.py:257,831` for model-list filtering.
- `pick_allowed_model(requested, allowed, default, assistant_model=None)` (L54-74) — already implements the agreed order: requested → assistant_model → default → `allowed[0]`. No logging/metric.
- `_get_project_models_uncached` / `get_project_models` (L77-102) — `allowed_models is None` returns `ProjectModelConfig([], default or "")` (the None→[] collapse, L89-90); populated/empty list without default raises `NoDefaultModelException`; missing project raises `NotFoundException`.
- `resolve_requested_model(project, requested, is_global)` (L105-141) — flag off / no project / global / NotFound / `not config.allowed_models` → passthrough. Because of the collapse, both None and `[]` pass through (deny-all treated as unrestricted). Calls `pick_allowed_model` without `assistant_model`. Returns `(picked, picked != requested)`.
- `validate_model_for_asset_creation(model_id, project)` (L144-204) — reads raw `Application`; skips when `allowed_models is None and not default_model`; None with a default set falls through to "Project has no allowed models" rejection (L187); `[]` rejects; not-in-list rejects; emits `MODEL_AVAILABILITY_VALIDATIONS_TOTAL_METRIC` allowed/blocked.
- `resolve_model_for_execution(requested, project, is_global, fallback_model)` (L218-307) — raw `Application`; None → `requested or default or fallback`; `[]` raises; unset requested → default; not-in-list → default (no assistant-model step, no first-allowed hardening); logs `fallback_applied` and emits `MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC`.

Other implementations:
- `enforce_allowed_model(requested, project: Application|None, assistant_model=None)` — `src/codemie/core/dependecies.py:231-273`. No project/flag off → passthrough; raw `project.allowed_models is None` → `requested or project.default_model`; else `get_project_models`, `[]` raises `ModelNotWhitelistedException`, else `pick_allowed_model(..., assistant_model=...)`. Also invoked inside `get_llm_by_credentials` (L286) without `assistant_model`.
- `ToolkitService._ensure_project_allows_model(name, project_name)` — `src/codemie/service/tools/toolkit_service.py:861-872`; check-only (reject, no substitution) for LLM-supplied OCR model names via `_build_ocr_model` (L849-858). Reads raw field; None → no-op.

Root resolution path (chat):
- `_resolve_chosen_execution_model` — `src/codemie/rest_api/routers/assistant/_ask.py:87-101`: `request.llm_model or assistant.llm_model_type` → `resolve_requested_model` (no assistant-model step).
- `_prepare_assistant_for_execution` — `_ask.py:109-134`; writes result into `assistant.llm_model_type` (L130-132). Called from `rest_api/routers/assistant/execution.py:187,325`; resume path (L250) intentionally skips it.

`AssistantService.build_agent` — `src/codemie/service/assistant_service.py:513-660`:
- L541 `request = deepcopy(request)`; L543 `_apply_conversation_runtime_overrides` (L89-110) sets `request.llm_model = conversation.llm_model` when `'llm_model' not in request.model_fields_set`.
- L561-569: `llm_model = request.llm_model or assistant.llm_model_type`; project loaded unless global; `enforce_allowed_model(..., assistant_model=assistant.llm_model_type)`; then `assistant.llm_model_type = llm_model` (in-place on caller's object) and `request.llm_model = llm_model` if set.
- Resolved `llm_model` flows to `ToolkitService.get_tools`, `agent_kwargs["llm_model"]`, and `_configure_langgraph_agent` → `LangGraphAssistantBuilder.configure_agent_kwargs`.
- `build_agent_for_workflow` (L800+) uses `assistant.llm_model_type` (set from workflow node, L736-742) with no `enforce_allowed_model` call; it also calls `_configure_langgraph_agent` (L936) so workflow assistants can spawn subagents.
- Other `build_agent` callers: `rest_api/handlers/assistant_handlers.py:663,1146,1207,1308`, `hedged_handler.py:485`, `a2a/server/codemie_task_manager.py:277`, `service/assistant/assistant_health_check_service.py:305`.

Child construction, `src/codemie/service/assistant/assistant_engine_builder.py`:
- `create_subagent_executors` (L181-251) passes the parent's resolved `llm_model` and the (mutated) `request` to `create_assistant_executors` and to `_build_builtin_subagent_executor`; builtin failures are logged and skipped.
- `_build_builtin_subagent_executor` (L136-178) — `parent.model_copy(deep=False)`, so the clone inherits the parent's already-rewritten `llm_model_type`.
- `get_subagent_descriptions_with_contributions` (L254-278) — rebuilds the description dict independently from `Assistant.get_by_ids` + effective builtins, not from built executors.
- `configure_agent_kwargs` (L281-327) — L315-327 builds subagents, then calls `get_subagent_descriptions(assistant, user)` separately.
- `src/codemie/service/tools/assistant_factory.py`: `AssistantFactory.__init__` stores `llm_model` but `build()` (L61-98) does not use it — calls `AssistantService.build_agent(assistant, request.model_copy(), ..., is_subagent=True)`. `create_assistant_executors` (L101+) applies version pins and swallows per-child failures (MCP and generic).

Agent-level re-resolution:
- `AIToolsAgent._resolve_llm_model` — `src/codemie/agents/assistant_agent.py:~289-314` calls `resolve_model_for_execution(requested=self.llm_model, project=assistant.project, is_global, fallback_model=self.llm_model)` (memoized).
- Workflow nodes: `workflows/nodes/base_node.py:148` (`fail_open` option); `llm_service.py:526` wrapper.

Read-only/UI: `rest_api/routers/assistant/catalog.py:189-200` `_resolve_effective_llm_model` uses `resolve_requested_model` to show `project_default` in the detail response. Asset creation: `rest_api/routers/assistant/management.py:246-249` calls `validate_model_for_asset_creation`.

Conversation model: `Conversation.llm_model` is written only by `ConversationService.update_conversation` (`service/conversation_service.py:1250-1251`, from the user's PATCH request); no code path found writing a resolved model into it. `upsert_chat_history` (L426) and `assistant_handlers.py:417` compute `request.llm_model or assistant.llm_model_type` for metrics — both see post-resolution values today.

### Architecture and Layers Affected
- Router layer: `rest_api/routers/assistant/_ask.py`, `catalog.py`, `management.py`.
- Service layer: `assistant_service.py` (`build_agent`, `_apply_conversation_runtime_overrides`), `assistant/assistant_engine_builder.py`, `tools/assistant_factory.py`, `tools/toolkit_service.py`, `llm/model_availability_service.py`, `llm_service/llm_service.py`.
- Core: `core/dependecies.py` (`enforce_allowed_model`, `get_llm_by_credentials`).
- Agents/workflows: `agents/assistant_agent.py`, `workflows/nodes/base_node.py`.

### Integration Points
- `Application` (`core/models`) — `allowed_models: list|None`, `default_model`; managed via `PATCH/DELETE /projects/{name}/allowed-models` (`rest_api/routers/projects/management.py:95-128`).
- `customer_config.is_feature_enabled("projectModelOverride")` — gate for all paths (string literal duplicated in `dependecies.py:249`, `toolkit_service.py:868`).
- `BaseMonitoringService.send_count_metric` with `MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC` / `MODEL_AVAILABILITY_VALIDATIONS_TOTAL_METRIC`.
- Exceptions: `ModelNotWhitelistedException`, `NoDefaultModelException`, `NotFoundException` (`core/exceptions.py`).

### Patterns and Conventions
- Static-method service classes; structured `key=value` log lines (`model_availability_event=...`).
- Cross-project child lookup via `Assistant.get_by_ids(user, ids, parent_assistant=...)`.
- Lazy in-function imports to break circular deps (`AssistantFactory.build`, `create_subagent_executors`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
No guide in `.ai-run/guides/` covers the project model whitelist (only an unrelated mention in `agents/custom-tool-creation.md:53`). Relevant generic guides: `architecture/service-layer-patterns.md`, `development/logging-patterns.md`, `testing/testing-service-patterns.md`.

### Architectural Decisions
- Prior MR artifacts: `docs/superpowers/tasks/2026-10-27-epmcdme-16411-skill-subagent-model-whitelist/spec.md` (amended in commit de12d584b for the sub-assistant rule) and `plan.md`; review rounds under `docs/superpowers/reviews/2026-10-29-epmcdme-16411-mr4647-review{,-2,-3}/`.
- Inline: `AssistantFactory.build` docstring (L65-76) records that policy is delegated to `build_agent`; `_apply_conversation_runtime_overrides` comment (L112-118) says the Assistant must not be mutated in place with request overrides — `build_agent` L566 does exactly that for `llm_model_type`.

### Derived Conventions
- Raw `Application.allowed_models` is re-read in three places specifically to distinguish None from `[]` (comments at `dependecies.py:255-258`, `toolkit_service.py:865-866`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/core/test_dependecies.py:199-293` — `enforce_allowed_model` cases (no project, flag off, allowed, fallback to default, fallback to assistant model, first allowed, deny-all raise, unconfigured).
- `tests/codemie/service/tools/test_assistant_factory.py:420-623` — factory → real `build_agent` up to `get_react_llms` (sentinel `_PolicyPassed`); asserts in-place `assistant.llm_model_type == ...`; patches `codemie.core.dependecies.ModelAvailabilityService.get_project_models` and `Application.get_by_id`; `_apply_conversation_runtime_overrides` patched out. Single assistant only — no parent→child chain.
- `tests/codemie/service/assistant/test_create_subagent_executors.py` — 2 tests (builtin with unconfigured whitelist; failure logging).
- `tests/codemie/service/llm/test_model_availability_service.py` — `get_project_models`, `resolve_model_for_execution`, `validate_model_for_asset_creation`.
- `tests/codemie/rest_api/routers/test_project_model_availability{,_execution,_integration}.py`, `test_project_settings_models_e2e.py`, `test_assistant.py` (catalog detail), `test_toolkit_service.py` (OCR check), `workflows/nodes/test_model_selection.py`.
- `tests/codemie/service/test_assistant_service_methods.py:367-424` — `_apply_conversation_runtime_overrides`.

### Testing Framework and Patterns
pytest with `unittest.mock` (`Mock(spec=...)`, `patch` on module paths). Behaviour is asserted through mocked `Application.get_by_id` and flag patches.

### Coverage Gaps
- No test drives parent → child through real `build_agent` (same or cross project).
- No test for `_prepare_assistant_for_execution` + `build_agent` combined order.
- `get_subagent_descriptions_with_contributions`: no tests within 3 caller hops (codegraph).
- No test of `resolve_requested_model` with `[]` (deny-all) semantics.

---

## 5. Configuration and Environment

### Environment Variables
None specific; `config.ENABLE_LANGGRAPH_AITOOLS_AGENT` selects `LangGraphAgent` (only that path builds subagents via `_configure_langgraph_agent`).

### Configuration Files
`customer_config` feature `projectModelOverride` (default deployments: off). Builtin subagent registry YAML at `BuiltinSubagentsRegistry.CONFIG_PATH`.

### Feature Flags and Deployment Concerns
All policy paths short-circuit when `projectModelOverride` is off; DB fields `allowed_models`/`default_model` on `Application`. No migrations observed in this area.

---

## 6. Risk Indicators

- Six resolution points exist for one chat turn, not two: `_ask.py:130`, `build_agent:565`, `AIToolsAgent._resolve_llm_model` (`resolve_model_for_execution`, different order, no assistant-model step), `get_llm_by_credentials:286` (`enforce_allowed_model` without assistant model), plus workflow `base_node.py:148`. The reviewer counts five policy implementations; the agent-level re-resolution is an additional consumer that could still override a correctly chosen model.
- Semantic drift for "None + default_model set": `validate_model_for_asset_creation` rejects (falls to L187), `resolve_model_for_execution`/`enforce_allowed_model` substitute `default_model` when requested is blank, `_ensure_project_allows_model` and `resolve_requested_model` pass through. A unified policy must pick one; "keep current handling" is ambiguous here.
- `[]`: `resolve_requested_model` passes through (catalog detail + `_ask.py`); others raise. Changing it alters catalog response and the root pre-check.
- In-place mutation: `build_agent` L566 writes into the caller's `assistant` (`execution_assistant`), and `request.llm_model` assignment adds `llm_model` to `model_fields_set`, so a child's `_apply_conversation_runtime_overrides` will not apply the conversation model. Downstream readers (`assistant_handlers.py:417`, `conversation_service.py:426` metrics) currently observe the resolved value.
- Conversation model: code shows `Conversation.llm_model` set only from the user's PATCH; the "stores resolved model" premise was not confirmed in source. However the conversation override is applied inside `build_agent`, after `_ask.py` pre-resolution, so root sees it in a different order than direct request picks.
- `AssistantFactory.llm_model` is stored and unused; builtin clones inherit through `parent.model_copy(deep=False)`.
- `build_agent_for_workflow` path has no `enforce_allowed_model` and can spawn subagents with the workflow request.
- Existing tests assert in-place mutation and patch `codemie.core.dependecies.ModelAvailabilityService.get_project_models`; consolidation will break those patch targets.
- `build_agent` is called from 7 sites (handlers, hedged, A2A, health check); A2A/hedged/health-check do not go through `_prepare_assistant_for_execution`, so removing it changes nothing for them but they rely on `build_agent` alone.
- Speculative: descriptions/executors alignment (fix item 4) likely requires `create_subagent_executors` to return names alongside executors, touching `configure_agent_kwargs` and its tests (`test_configure_agent_kwargs_confirmation.py`, `test_script_tool_confirmation.py`).

---

## 7. Summary for Complexity Assessment

The change spans router (`_ask.py`, `catalog.py`, `management.py`), service (`assistant_service.build_agent`, `assistant_engine_builder`, `assistant_factory`, `toolkit_service`, `model_availability_service`) and core (`dependecies.enforce_allowed_model`) layers, with agent-level (`AIToolsAgent._resolve_llm_model`) and workflow (`base_node`) consumers of the same policy. The selection order the reviewer asks for already exists in `pick_allowed_model`; the problem is that five functions wrap it with divergent None/`[]`/missing-default semantics and that `build_agent` writes its result back into `assistant.llm_model_type` and `request.llm_model`, which every child (assistant_ids and builtin clones) then inherits as the "user pick".

Novelty is moderate: no new external integration, but the consolidation is a semantics-sensitive refactor across ~10 source files with ~24 test files referencing the policy surface. Several of those tests assert the in-place mutation or patch `core.dependecies` import paths and will need rewriting. Edge semantics (None with default set, `[]` in `resolve_requested_model`, missing project, global assistants) differ per site today, so "keep current handling" must be pinned per call site.

Test posture: unit coverage of each function is good, but there is no parent → child chain test through real `build_agent`, no test of `_ask.py` + `build_agent` ordering, and subagent descriptions are untested. Key risks: the additional agent-level re-resolution in `AIToolsAgent`, conversation-override ordering and `model_fields_set` side effects, metric/log behaviour moving into a shared policy, and seven `build_agent` callers that depend on its current mutating contract.

---

## 8. External References

None named by the task. (The MR/review note are cited by ID only; prior in-repo spec at `docs/superpowers/tasks/2026-10-27-epmcdme-16411-skill-subagent-model-whitelist/spec.md`.)
