# Technical Research

**Task**: subagent assistant-factory model-whitelist projectModelOverride
**Generated**: 2026-10-27
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-16411 — Sonnet 5.5 and Gemini Argon do not use the subagents enabled by the epam-pptx skill.

Ticket description: When using Sonnet 5.5 or Gemini Argon with the `epam-pptx` skill, the model does not discover or use the subagents that the skill enables. The assistant does not need separately configured subagents; the skill itself enables them.
Acceptance Criteria:
1. Sonnet 5.5 uses the subagents enabled by `epam-pptx`.
2. Gemini Argon uses the subagents enabled by `epam-pptx`.
3. The skill-enabled subagent behavior can be verified during a run.
4. No separate assistant-level subagent configuration is required for this skill workflow.
5. Existing behavior for other supported models and skills is not regressed.

Root cause ALREADY ESTABLISHED (lab HAR evidence + local reproduction) — not model-specific; any explicit request.llm_model (GPT-4.1 too) triggers it:
- AssistantFactory.build (src/codemie/service/tools/assistant_factory.py ~L77-117): when customer feature `projectModelOverride` is enabled and the request carries llm_model, it checks `request.llm_model not in ModelAvailabilityService.get_project_models(project.id).allowed_models` and raises ModelNotWhitelistedException. get_project_models converts an unconfigured project whitelist (allowed_models=None) into [], so every model is rejected.
- The parent assistant path, enforce_allowed_model (src/codemie/core/dependecies.py:231), treats allowed_models None as "no restriction" and passes.
- LangGraphAssistantBuilder.create_subagent_executors (src/codemie/service/assistant/assistant_engine_builder.py ~L181-247) catches the exception and logs "Failed to create builtin subagent executor for GENERAL_PURPOSE_SUBAGENT: " with an empty message (exception str is empty), so the subagent silently disappears from the supervisor handoff tools.
- The UI sends llmModel only after the user changes the model in the assistant's General configuration; with llmModel null the subagent works.
Intended fix: make the subagent-side whitelist check consistent with enforce_allowed_model (skip when project allowed_models is None, ideally reuse the shared function / resolve_requested_model), make the swallowed error log informative, and add unit tests (tests/codemie/service/tools/test_assistant_factory.py and builder tests). Also check whether sub-assistants configured via assistant_ids and other AssistantFactory.build callers are affected the same way, and whether anything relies on the current deny-all behavior.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/service/tools/assistant_factory.py`
  - `AssistantFactory.build` (L64-117): the hard-block check at L79-95. Looks the project up with `Application.get_by_id(self.assistant.project)`, gates on `self.request.llm_model` and `customer_config.is_feature_enabled("projectModelOverride")` (a string literal, not the constant), then runs `self.request.llm_model not in config.allowed_models` as a plain list membership test. It never reads the raw `Application.allowed_models`, so it cannot tell `None` (unrestricted) apart from `[]` (deny-all). It ignores `assistant.is_global`. Its outer `except Exception` (L115-117) logs `str(e)` and re-raises.
  - `create_assistant_executors` (L120-195): builds one `AssistantFactory` per `assistant_ids` entry. `MCPToolLoadException` is re-raised. Any other exception is logged at L193 as `Failed to create executor for assistant {id}: {str(e)}` and skipped.
- `src/codemie/service/assistant/assistant_engine_builder.py`, `LangGraphAssistantBuilder`
  - `_merge_skill_builtin_subagents` (L101-118): merges `enabled_builtin_subagents` from the assistant and from its skills. This is how `epam-pptx` turns on `GENERAL_PURPOSE_SUBAGENT`.
  - `_build_builtin_subagent_executor` (L135-178): makes a shallow clone of the parent (with `assistant_ids=[]`, `enabled_builtin_subagents=[]`, a builtin system prompt and runtime recursion flags) and passes the same `request` to `AssistantFactory(...).build()`. The clone keeps the parent's `project`.
  - `create_subagent_executors` (L180-250): first the sub-assistants via `create_assistant_executors`, then the builtins. A builtin failure is logged at L247 as `Failed to create builtin subagent executor for {builtin.value}: {e}` and skipped.
  - `get_subagent_descriptions_with_contributions` (L252-277): still lists builtin descriptions even when the executor failed to build. This was observed, not verified at runtime.
- `src/codemie/core/dependecies.py::enforce_allowed_model` (L231-269): the parent-side policy. No project, or the flag off, means pass-through. `project.allowed_models is None` means return the request (or the project default). `[]` raises `ModelNotWhitelistedException`. Otherwise `pick_allowed_model`, which falls back instead of raising.
- `src/codemie/service/llm/model_availability_service.py`
  - `_get_project_models_uncached` (L69-89): turns `allowed_models None` into `ProjectModelConfig(allowed_models=[])`. The source of the ambiguity.
  - `resolve_requested_model` (L97-133): degrades gracefully when there is no project, the assistant is global, the flag is off, the project is not found, or the list is empty. `[]` is treated as "no whitelist" here, which differs from `enforce_allowed_model`'s deny-all. Callers are `_ask.py::_resolve_chosen_execution_model` (L87-101) and `catalog.py`.
  - `PROJECT_MODEL_OVERRIDE_FEATURE = "projectModelOverride"` (L28). `is_model_allowed` and `pick_allowed_model` are at L43-66.
- `src/codemie/core/exceptions.py`: `ExtendedHTTPException.__init__` (L82-86) sets `code/message/details/help` but never calls `super().__init__()`, so `str(e)` is `""` for every subclass. That includes `ModelNotWhitelistedException` (L342-349). It explains the empty log text at L247 and L193, and in `AssistantFactory.build` L116.
- `src/codemie/service/assistant_service.py::build_agent` (L512+): resolves `llm_model = request.llm_model or assistant.llm_model_type`, sets `project = None` when `assistant.is_global`, calls `enforce_allowed_model`, then sets `request.llm_model = llm_model` on a deepcopy. The subagent factory receives a request whose `llm_model` the parent has already enforced.

### Architecture and Layers Affected
- Service layer: `AssistantFactory`, `LangGraphAssistantBuilder`, `AssistantService.build_agent`, `ModelAvailabilityService`.
- Core layer: `enforce_allowed_model` in `core/dependecies.py`, and `ExtendedHTTPException` in `core/exceptions.py`.
- API layer, read-only context: `_ask.py` model resolution.

### Integration Points
- Call chain: `AssistantService.build_agent` → `_configure_langgraph_agent` → `LangGraphAssistantBuilder.configure_agent_kwargs` (L279-326) → `create_subagent_executors` → {`create_assistant_executors`, `_build_builtin_subagent_executor`} → `AssistantFactory.build` → `AssistantService.build_agent(is_subagent=True)`. The last call runs `enforce_allowed_model` again for the child.
- The codegraph blast radius shows exactly two `AssistantFactory` instantiation sites: `_build_builtin_subagent_executor` and `create_assistant_executors`.
- `customer_config.is_feature_enabled` drives the feature flag.

### Patterns and Conventions
- Builtin and sub-assistant failures are non-fatal: log and skip. `MCPToolLoadException` is the only typed exception with a dedicated log branch, and it is re-raised for sub-assistants.
- The model policy is split across three helpers with different `[]`/`None` semantics: `enforce_allowed_model`, `resolve_requested_model` and `validate_model_for_asset_creation` (the last checks the raw `app.allowed_models`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/testing/testing-service-patterns.md`: mock provider and repository boundaries and test service decisions. Async tests use pytest-asyncio.
- `.ai-run/guides/integration/llm-providers.md` exists. It was not read because it covers provider configuration, not the whitelist.

### Architectural Decisions
- An inline comment in `enforce_allowed_model` (L253-256) explicitly documents the `None` vs `[]` ambiguity created by `get_project_models` and says the raw field must be checked first. `AssistantFactory.build` does not follow it.
- The `AssistantFactory.build` docstring states the intent: "hard block ... No fallback."

### Derived Conventions
- Project lookup: `Application.get_by_id(assistant.project)`, skipped for global assistants in `build_agent`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/tools/test_assistant_factory.py`: init, build delegation, `is_subagent=True`, the thread generator, exception propagation, and `create_assistant_executors` (basic, parent, version pinning, remote-deleted skip, factory exception skip, get_by_ids failure, empty list). Every fixture uses `request.llm_model = None`, so the whitelist branch has no tests.
- `tests/codemie/core/test_dependecies.py`: `enforce_allowed_model`, including `test_deny_all_whitelist_raises_instead_of_bypassing` and `test_deny_all_whitelist_raises_even_with_blank_requested_model`. These pin the deny-all behavior for a configured empty `[]` whitelist.
- `tests/codemie/service/llm/test_model_availability_service.py`: `get_project_models`, including `test_get_project_models_unconfigured_project_does_not_raise`, plus `validate_model_for_asset_creation` and the empty-whitelist cases.
- `tests/codemie/service/assistant/test_configure_agent_kwargs_confirmation.py` and `tests/codemie/agents/tool_confirmation/test_script_tool_confirmation.py` cover `create_subagent_executors` indirectly.
- `tests/unit/subagents/test_builtin_subagents_recursion_guard.py` and `test_builtin_subagents_dedupe.py` are logic-only. They exercise a local helper, not the builder.
- `tests/codemie/service/subagents/test_builtin_subagents_registry.py` covers the registry.

### Testing Framework and Patterns
- pytest with `unittest.mock` (`Mock(spec=...)`, `@patch("codemie.service.tools.assistant_factory.<name>")`). `AssistantService.build_agent` is patched at `codemie.service.assistant_service.AssistantService.build_agent`.

### Coverage Gaps
- No test of `AssistantFactory.build` with `request.llm_model` set and `projectModelOverride` enabled, for any of the `None`, `[]` or populated whitelist states.
- No direct unit test of `LangGraphAssistantBuilder.create_subagent_executors` or `_build_builtin_subagent_executor`, including the content of the failure log.

---

## 5. Configuration and Environment

### Environment Variables
- None specific. `config.BUILTIN_SUBAGENTS_CONFIG_PATH` locates `config/subagents/subagents.yaml`.

### Configuration Files
- `config/subagents/subagents.yaml`: builtin subagent display names and system prompts.
- Customer config (`config/customer/customer-config.yaml`, which has local modifications in the working tree) toggles the `projectModelOverride` feature.

### Feature Flags and Deployment Concerns
- `projectModelOverride` is the only gate. When it is off, the bug cannot occur.
- Project-level `Application.allowed_models` and `default_model` live in the DB.

---

## 6. Risk Indicators

- The `None` vs `[]` semantics are inconsistent across helpers. `enforce_allowed_model` treats `[]` as deny-all, which is pinned by tests. `resolve_requested_model` treats `[]` as unrestricted. Choosing which one to reuse changes `[]` behavior for subagents.
- `AssistantFactory.build` ignores `assistant.is_global`, while `build_agent` skips project enforcement for global assistants. That is a second divergence.
- Sub-assistants via `assistant_ids` go through the same `AssistantFactory.build` check. They are affected identically and their failures are logged with an empty message (`assistant_factory.py` L193). Each one is checked against its own `project`, which may differ from the parent's.
- With a populated whitelist, the parent has already narrowed `request.llm_model` into it. The factory check then fails only for a sub-assistant in a different project. In that case the factory raises, where `enforce_allowed_model` would fall back. Removing the hard block changes that behavior.
- `ExtendedHTTPException` has an empty `str()`. Any log that uses `{e}` or `str(e)` on these exceptions is blank. Changing `__init__` globally is broad: there are many subclasses and the exceptions reach FastAPI handlers.
- The factory pre-check is redundant with the `enforce_allowed_model` that the child `build_agent` runs. Speculative: removing it instead of fixing it would be the smallest change, but that is a design decision.
- Supervisor descriptions (`get_subagent_descriptions_with_contributions`) may still list a builtin whose executor failed. This was not verified.

---

## 7. Summary for Complexity Assessment

The change is confined to the service layer. It centers on `AssistantFactory.build` (`src/codemie/service/tools/assistant_factory.py` L77-95) and on the failure logging in `LangGraphAssistantBuilder.create_subagent_executors` (`assistant_engine_builder.py` L238-247) and `create_assistant_executors` (L192-193). It sits next to the existing policy helpers `enforce_allowed_model` (`core/dependecies.py`) and `ModelAvailabilityService.resolve_requested_model`, which it can reuse. `AssistantFactory` has exactly two instantiation sites, and both are in the subagent path. The root cause is confirmed: `get_project_models` collapses `None` to `[]`, and the factory does a raw `in` check. The parent path documents and handles this case. The empty log text comes from `ExtendedHTTPException` never calling `super().__init__()`.

Nothing here is technically new. The patterns (feature-flag gate, project lookup, `pick_allowed_model`) already exist. The main design subtlety is the inconsistent `[]` semantics: deny-all in `enforce_allowed_model` (pinned by tests) versus unrestricted in `resolve_requested_model`. On top of that, the factory skips the global-assistant exemption and raises rather than falling back when a sub-assistant's project differs from the parent's.

On tests, `test_assistant_factory.py` exists, but every fixture uses `llm_model=None`, so the whitelist branch has zero tests. The builder's `create_subagent_executors` has only indirect coverage. Unit tests for `None`, `[]`, populated, flag-off and global cases, plus a log-content assertion, are straightforward with the existing mock patterns.

---

## 8. External References

None named by the task.
