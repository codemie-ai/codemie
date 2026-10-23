# EPMCDME-15208 Clear Temperature Implementation Plan

**Goal:** An explicit `temperature: null` / `top_p: null` clears the stored value end to end, and outbound LLM requests omit `temperature` when it is None or the model does not support it.

**Architecture:** Backend only. Mapping and versioning already honor `model_fields_set`, so the clear path needs regression tests only. Runtime fix: stop forcing `temperature=1` and stop passing `temperature=None` to providers.

**Conventions:** Commit per task using the repository's existing convention. Follow `.ai-run/guides/testing/testing-patterns.md` and `.ai-run/guides/standards/code-quality.md`; run `make ruff` on touched files.

**Verified in code:**
- `Assistant._map_assistant_request` (`models/assistant.py:893-927`) applies any field in `model_fields_set`, including None, so no source change is needed.
- `create_new_version` (`assistant_version_service.py:106-121`) snapshots an explicit None via `versioned()`.
- `has_configuration_changes` (`assistant_version_compare_service.py:84-87`) overlays an explicit None, so 0.7 to None is detected as a change.
- No existing test asserts `temperature == 1` or sets `features.temperature = False`, so none need updating.
- No existing test does a PUT round trip on `/v1/assistants`, so a router test is not cheap and is skipped. The mapping and version layers cover the contract.

**Negative constraints:** The requirements say not to force `temperature=1` and not to pass `temperature=None` to ChatBedrockConverse. Tasks 3 and 4 honor both. Explicit temperature for supporting models stays unchanged, which Tasks 3 and 4 assert.

## Task 1: Mapping-layer regression tests for temperature and top_p

**Files:** modify `tests/codemie/rest_api/models/test_map_assistant_request_helpers.py` (add after `test_explicit_null_clears_field`, ~line 244).

Test-first: no — characterization tests; the behavior already exists (verified above), so they pass on first run and guard against regression.

- [ ] Add parametrized tests over `temperature` and `top_p`, following the `AssistantBase` + `AssistantRequest` + `_map_assistant_request` pattern at ~231. Cover: set (stored None to 0.5), update (0.7 to 0.2), clear (explicit `None` gives `None`), and omitted (stored value preserved).

## Task 2: Version snapshot regression tests for clear

**Files:** modify `tests/codemie/service/assistant/test_assistant_version_service_critical.py` (next to `test_create_new_version_applies_explicitly_set_fields`, ~line 145) and `tests/codemie/service/assistant/test_assistant_version_compare_service.py` (near ~299).

Test-first: no — characterization tests for already-correct behavior.

- [ ] Version service: with `mock_assistant.temperature = 0.7` and `top_p = 0.9`, build `AssistantRequest(..., temperature=None, top_p=None)`. Assert `mock_config_class.call_args[1]['temperature'] is None` and the same for `top_p`.
- [ ] Compare service: reuse the existing temperature-change test's setup with stored 0.7 and an explicit `temperature=None`. Assert `has_configuration_changes` is True. Add the same for `top_p`.

## Task 3: Omit temperature on the LiteLLM and Azure paths

**Files:**
- Modify `src/codemie/enterprise/litellm/llm_factory.py`: `_build_chat_model_base_args` (:315-327) and `_filter_litellm_params` (:1025-1026).
- Modify `src/codemie/core/dependecies.py`: `filter_params` (:322-323).
- Test `tests/enterprise/litellm/test_llm_factory.py` (new `TestFilterLiteLLMParams` and `TestBuildChatModelBaseArgs` classes) and `tests/codemie/core/test_core_dependencies.py`.

Test-first: yes — failing tests: (a) `_build_chat_model_base_args(temperature=None)` has no `'temperature'` key, and `0.3` is kept; (b) `_filter_litellm_params` with `features.temperature=False` and base_args temperature 0.3 returns a dict without `temperature` (today it returns 1); (c) with `features.temperature=True` and 0.3, temperature stays 0.3; (d) `filter_params` with `features.temperature=False` returns no `temperature` (today 1).

- [ ] Write the tests above using `MagicMock` model details (pattern at `test_llm_factory.py:107`), and confirm they fail.
- [ ] In `_build_chat_model_base_args`, build the dict without `temperature`, then add `args['temperature'] = temperature` only when it is not None.
- [ ] In `_filter_litellm_params`, replace `filtered['temperature'] = 1` with `filtered.pop('temperature', None)`.
- [ ] In `filter_params`, replace `optional_args['temperature'] = 1` with popping `temperature` from both `base_args` and `optional_args`.
- [ ] Re-run the tests and confirm they pass.

## Task 4: Bedrock direct path omits temperature

**Files:**
- Modify `src/codemie/core/dependecies.py`: `get_bedrock_llm` (:452-474).
- Test `tests/codemie/core/test_core_dependencies.py`.

Test-first: yes — failing tests: patch `langchain_aws.ChatBedrockConverse` and `codemie.core.dependecies.get_bedrock_runtime_client`, then call `get_bedrock_llm`. (a) With `temperature=None`, the kwargs passed to the constructor have no `temperature` key. (b) With `features.temperature=False` and `temperature=0.5`, there is no `temperature` key. (c) With `features.temperature=True` and `temperature=0.5`, `temperature == 0.5`. `top_p` behavior is unchanged.

- [ ] Write the tests and confirm they fail.
- [ ] Assemble the constructor kwargs in a dict. Add `temperature` only when it is not None and `llm_model_details.features.temperature is not False`. Then call `ChatBedrockConverse(**kwargs)`.
- [ ] Re-run the tests and confirm they pass.
