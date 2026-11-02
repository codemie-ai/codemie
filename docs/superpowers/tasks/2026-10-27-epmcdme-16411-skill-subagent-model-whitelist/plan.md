# EPMCDME-16411 Subagent Model Whitelist Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stop `AssistantFactory.build` from dropping skill-enabled builtin subagents and `assistant_ids` sub-assistants when the request names an `llm_model`. Make the swallowed failure logs informative.

**Architecture:** Delete the factory's own whitelist pre-check. The child `AssistantService.build_agent(is_subagent=True)` (`assistant_service.py:561-569`) already runs `enforce_allowed_model` and becomes the single policy. Add one local helper, `describe_exception`, so the swallow-site logs show the exception type plus its code and message.

**Tech Stack:** Python, pytest, `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-10-27-epmcdme-16411-skill-subagent-model-whitelist/spec.md`

## Global Constraints

- Commit per task using the repository's existing convention: `EPMCDME-16411: <Capital sentence>`, with a body ending `Generated with AI` and then `Co-Authored-By: codemie-ai <codemie.ai@gmail.com>`.
- Stage only the files each task names. Never stage `config/customer/customer-config.yaml`, `config/llms/llm-dial-config.yaml`, `.codemie/codemie-cli.config.json` or other tickets' docs.
- Do not touch `ExtendedHTTPException`, `enforce_allowed_model`, `resolve_requested_model`, `get_project_models`, `validate_model_for_asset_creation`, `get_subagent_descriptions_with_contributions` or `config/subagents/subagents.yaml`.
- Existing tests in `test_dependecies.py`, `test_assistant_factory.py` and `test_model_availability_service.py` must pass unmodified.

## Review Focus

- The request has `llm_model=None` but the assistant has a model type. Policy still applies through `assistant.llm_model_type`, and that path is unchanged (covered by the existing tests).
- A cross-project sub-assistant whose populated whitelist excludes the model now falls back rather than being dropped. This is AC5, and it is pinned in Task 1.
- The exception's `message`/`code` is `None` or missing (a plain `ValueError`). The log must still not be empty. Pinned in Task 2.

## Negative-constraint pass

- No `ExtendedHTTPException` change: Task 2 formats locally in `describe_exception`.
- No policy-helper semantic change: Task 1 only deletes factory code, and its tests use the real `enforce_allowed_model`.
- No descriptions, UI, API, DB or registry change: no task touches them.
- No model-specific handling: the tests use arbitrary model ids.

---

### Task 1: Remove the AssistantFactory whitelist pre-check

**Files:**
- Modify: `src/codemie/service/tools/assistant_factory.py:64-95`. Delete the L78-95 pre-check block. Rewrite the docstring (L65-76) to say that model policy is enforced by `AssistantService.build_agent` via `enforce_allowed_model` (unconfigured whitelist means unrestricted, `[]` raises `ModelNotWhitelistedException`, populated falls back via `pick_allowed_model`, global assistants are exempt). Drop the now-unused imports at L20 and L25-27.
- Test: `tests/codemie/service/tools/test_assistant_factory.py` (new class `TestAssistantFactoryModelPolicy`)
- Create test: `tests/codemie/service/assistant/test_create_subagent_executors.py`

**Interfaces:** Produces `AssistantFactory.build()` with the same signature and no project or flag lookup.

`Test-first: yes — with the flag on, an llm_model set and allowed_models=None, build() raises ModelNotWhitelistedException today instead of delegating.`

- [ ] **Step 1: Write the failing tests.** In the new class, run the real `AssistantService.build_agent` up to the policy step. Patch `codemie.service.assistant_service.{set_llm_context, build_unique_file_objects (return {}), BedrockOrchestratorService.is_bedrock_assistant (False), Application.get_by_id}`, `AssistantService.drop_missing_context` (return `[]`) and `AssistantService._apply_conversation_runtime_overrides`. Make `codemie.service.assistant_service.llm_service.get_react_llms` raise a sentinel `_PolicyPassed`, so a sentinel means "policy passed". Patch `codemie.core.dependecies.customer_config.is_feature_enabled` and `codemie.core.dependecies.ModelAvailabilityService.get_project_models` per case. Tests:
  - `test_unconfigured_whitelist_delegates`: flag on, `project.allowed_models=None`, `llm_model="gpt-4.1"`. Expect `_PolicyPassed`, `assistant.llm_model_type == "gpt-4.1"`, and `get_project_models` not called (AC1, AC3).
  - `test_empty_whitelist_raises_from_build_agent`: `allowed_models=[]`. Expect `ModelNotWhitelistedException` (AC4).
  - `test_populated_whitelist_falls_back`: config `allowed_models=["a","b"]`, `default_model="b"`, request `"x"`. Expect `_PolicyPassed` and `llm_model_type == "b"` (AC5).
  - `test_global_assistant_skips_project`: `assistant.is_global=True`, `allowed_models=[]`. Expect `_PolicyPassed` and `Application.get_by_id` not called (AC6).
  - `test_flag_off_unchanged`: flag off, `allowed_models=[]`. Expect `_PolicyPassed` and `llm_model_type == "x"` (AC7).
  - `test_create_assistant_executors_unconfigured_whitelist`: patch `build_agent` at `codemie.service.assistant_service.AssistantService.build_agent` and `Assistant.get_by_ids`. With the flag on, `allowed_models=None` and `llm_model` set, expect one executor returned (AC2).
  - In the new builder file, `test_builtin_subagent_built_with_unconfigured_whitelist`: patch `_merge_skill_builtin_subagents` to return `[BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT]`, `codemie.service.tools.assistant_factory.create_assistant_executors` to return `[]`, `AssistantService.build_agent` to return a mock agent, and `BuiltinSubagentsRegistry.get_system_prompt`. With the flag on and `allowed_models=None`, expect `create_subagent_executors(...)` to return exactly `[agent.agent_executor]` (AC1).
- [ ] **Step 2:** Run `pytest tests/codemie/service/tools/test_assistant_factory.py tests/codemie/service/assistant/test_create_subagent_executors.py -v`. Expect FAIL on the unconfigured, populated, global and builtin cases.
- [ ] **Step 3:** Apply the `assistant_factory.py` changes listed above.
- [ ] **Step 4:** Re-run the step 2 command plus `tests/codemie/core/test_dependecies.py tests/codemie/service/llm/test_model_availability_service.py`. Expect everything to PASS.

### Task 2: Informative subagent failure logs

**Files:**
- Modify: `src/codemie/service/tools/assistant_factory.py`. Add the helper. Use it at L116 (the `build` re-raise log) and at L193 (the `create_assistant_executors` skip log).
- Modify: `src/codemie/service/assistant/assistant_engine_builder.py:246-247`. Log `describe_exception(e)`, imported lazily next to the existing `create_assistant_executors` import at L195.
- Test: `tests/codemie/service/tools/test_assistant_factory.py`, `tests/codemie/service/assistant/test_create_subagent_executors.py`

**Interfaces:** Produces `describe_exception(exc: BaseException) -> str` in `codemie/service/tools/assistant_factory.py`. It returns `type(exc).__name__`, then `code=…` and `message=…` for each attribute that is present and not `None`, then `str(exc)` when that is non-empty. The result is never just an empty string.

`Test-first: yes — the builtin skip log for a ModelNotWhitelistedException currently ends with an empty message after the colon.`

- [ ] **Step 1: Write the failing tests.**
  - `test_describe_exception_model_not_whitelisted`: `e = ModelNotWhitelistedException("x", "proj", details="d")`. Assert `"ModelNotWhitelistedException"` and `str(e.message)` are in the result, and `str(e.code)` too.
  - `test_describe_exception_plain_error`: `ValueError("boom")` gives a result containing `"ValueError"` and `"boom"`.
  - `test_create_assistant_executors_logs_exception_details`: the factory raises `ModelNotWhitelistedException`. Patch `codemie.service.tools.assistant_factory.logger` and assert the `error` call text contains the class name and `e.message`.
  - In the builder file, `test_builtin_failure_log_is_informative`: `_build_builtin_subagent_executor` raises `ModelNotWhitelistedException`. Patch `codemie.service.assistant.assistant_engine_builder.logger` and assert the result is `[]` and that the `error` text contains `"GENERAL_PURPOSE_SUBAGENT"` or the enum value, the class name and `e.message` (AC4, AC8).
- [ ] **Step 2:** Run the two test files. Expect FAIL (`describe_exception` is undefined and the log text is empty).
- [ ] **Step 3:** Implement the helper and swap the three log sites. Keep the `MCPToolLoadException` branches unchanged.
- [ ] **Step 4:** Re-run the two test files. Expect PASS.
