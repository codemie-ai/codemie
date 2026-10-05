# Expose LiteLLM max_output_tokens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `GET /v1/llm_models` (both `include_all=true` and `include_all=false`) return LiteLLM's `max_output_tokens` instead of dropping it, with explicit test coverage for both the present and absent cases.

**Architecture:** `map_litellm_to_llm_model` already extracts `max_input_tokens` from `model_info` and passes it into the `LLMModel` constructor. Add the sibling `max_output_tokens` extraction and constructor kwarg, one line each, immediately next to the existing ones. No other layer changes: `LLMModel.max_output_tokens` already exists and the router already serializes it via `response_model_exclude_none=True` regardless of `include_all`.

**Tech Stack:** Python, Pydantic (`LLMModel`), pytest (extends existing `TestMapLiteLLMToLLMModel` suite in `tests/enterprise/litellm/test_models.py`).

**Spec:** Requirements supplied inline by the caller via the plan.approved gate (no spec.md) — see Acceptance criteria below. Technical analysis: `docs/superpowers/tasks/2026-10-05-expose-litellm-max-output-tokens/technical-analysis.md`.

**Commit per task using the repository's existing convention.**

## Global Constraints

- Touch only `src/codemie/enterprise/litellm/models.py` and `tests/enterprise/litellm/test_models.py`. No schema, router, or service changes.
- Keep the production change a line-for-line mirror of the existing `max_input_tokens` extraction/pass-through pattern — no transformation, coercion, or default applied.
- Test-driven: the two new test cases must exist and fail (RED) before the production lines are added, then pass (GREEN) after.

## Review Focus

- `model_info` missing `max_output_tokens` entirely (older LiteLLM proxy versions) — `.get()` must return `None`, not raise. Covered by Task 1's "absent" test.
- `model_info["max_output_tokens"]` present as a real value (e.g. `32768`) — must pass through unchanged. Covered by Task 1's "present" test.
- `include_all=false` listing path (`forbidden_for_web`-filtered) — must still carry the field through, since filtering only affects which models appear, not which fields are serialized. Not re-tested here: no existing test in this suite exercises `include_all` filtering at this unit's level, and adding one is out of scope for a mapping-function test.
- The `get_user_allowed_models` call site (`models.py:185`) — picks up the fix automatically since it calls the same function; no separate wiring task needed or added.
- `response_model_exclude_none=True` on the router — confirms a model whose `max_output_tokens` is still `None` continues to omit the key from the response rather than serializing `null`; no router change needed, and the "absent" test already pins the `None` behavior this depends on.

negative-constraints:
- "no schema/router/service changes are needed" — honored: Task 1 modifies only `models.py`'s `map_litellm_to_llm_model` and its test file; no edits to `llm_config.py`, `llm_models.py` (router), or `llm_service.py`.
- Previous round's "tests only when explicitly requested" no longer applies — tests were explicitly requested this round, so Task 1 is `Test-first: yes` with two new test cases.
- implicit non-goal: do not change the two call sites (`get_available_models`, `get_user_allowed_models`) — honored: neither is touched; both pick up the fix automatically by calling the same function.

## Acceptance criteria

- [ ] `map_litellm_to_llm_model` extracts `max_output_tokens` from `model_info` the same way it extracts `max_input_tokens`, and passes it into the `LLMModel(...)` constructor as `max_output_tokens=max_output_tokens`.
- [ ] A test asserts that when `model_info` contains `"max_output_tokens": 32768`, `result.max_output_tokens == 32768`.
- [ ] A test asserts that when `model_info` does not contain `"max_output_tokens"`, `result.max_output_tokens is None`.
- [ ] `GET /v1/llm_models` responses (both `include_all=true` and `include_all=false`) include `max_output_tokens` for models where LiteLLM's `/v1/model/info` reports it, with no router/schema/service changes.

---

### Task 1: Add failing tests, then map `max_output_tokens` from LiteLLM model_info into `LLMModel`

**Files:**
- Modify: `tests/enterprise/litellm/test_models.py` (append two tests to `TestMapLiteLLMToLLMModel`, after `test_maps_api_version` at line 45-63)
- Modify: `src/codemie/enterprise/litellm/models.py:161` (add extraction line) and `:179` (add constructor kwarg)

**Interfaces:**
- Consumes: `model_info: dict`, already a local in `map_litellm_to_llm_model` (from `litellm_model.get("model_info", {})`).
- Produces: `LLMModel.max_output_tokens` (field already declared in `src/codemie/configs/llm_config.py:222`) now populated instead of always `None`. Both existing call sites (`dependencies.py:858` `get_available_models`, `models.py:185` `get_user_allowed_models`) consume this transparently.

**Test-first: yes — `test_maps_max_output_tokens_when_present` and `test_maps_max_output_tokens_when_absent` must fail RED against the current code (both currently see `result.max_output_tokens is None` since the field is never populated), then pass GREEN after the two production lines are added.**

- [ ] **Step 1: Write the two failing tests**

  Append to the `TestMapLiteLLMToLLMModel` class in `tests/enterprise/litellm/test_models.py`, following the `test_maps_api_version` shape:

  ```python
  def test_maps_max_output_tokens_when_present(self):
      litellm_model = {
          "model_name": "azure/gpt-4",
          "model_info": {
              "litellm_provider": "azure",
              "id": "gpt-4",
              "label": "GPT-4",
              "enabled": True,
              "max_output_tokens": 32768,
          },
      }

      from codemie.enterprise.litellm.models import map_litellm_to_llm_model

      result = map_litellm_to_llm_model(litellm_model)

      assert result.max_output_tokens == 32768

  def test_maps_max_output_tokens_when_absent(self):
      litellm_model = {
          "model_name": "azure/gpt-4",
          "model_info": {
              "litellm_provider": "azure",
              "id": "gpt-4",
              "label": "GPT-4",
              "enabled": True,
          },
      }

      from codemie.enterprise.litellm.models import map_litellm_to_llm_model

      result = map_litellm_to_llm_model(litellm_model)

      assert result.max_output_tokens is None
  ```

- [ ] **Step 2: Run the new tests to verify they fail**

  Run: `poetry run pytest tests/enterprise/litellm/test_models.py -k max_output_tokens -v`
  Expected: `test_maps_max_output_tokens_when_present` FAILs (`32768 != None`); `test_maps_max_output_tokens_when_absent` PASSes already (both assert against the same unpopulated field, so only the "present" case can fail meaningfully pre-fix — confirm this is the actual observed result before proceeding).

- [ ] **Step 3: Add the extraction line**

  In `src/codemie/enterprise/litellm/models.py`, immediately after line 161 (`max_input_tokens = model_info.get("max_input_tokens")`), add:

  ```python
  max_output_tokens = model_info.get("max_output_tokens")
  ```

- [ ] **Step 4: Pass it into the `LLMModel` constructor**

  In the same file, immediately after the `max_input_tokens=max_input_tokens,` line inside the `LLMModel(...)` call (originally line 179, now shifted by one line from Step 3), add:

  ```python
  max_output_tokens=max_output_tokens,
  ```

- [ ] **Step 5: Run the tests to verify they pass**

  Run: `poetry run pytest tests/enterprise/litellm/test_models.py -k max_output_tokens -v`
  Expected: both `test_maps_max_output_tokens_when_present` and `test_maps_max_output_tokens_when_absent` PASS.

- [ ] **Step 6: Run the full suite for this module to check for regressions**

  Run: `poetry run pytest tests/enterprise/litellm/test_models.py -v`
  Expected: all tests PASS, including the pre-existing ones (e.g. `test_maps_api_version`, `test_maps_basic_model_info`) unchanged.

- [ ] **Step 7: Commit**

  Commit per the repository's existing convention (see `.ai-run/guides/standards/git-workflow.md` for ticket-reference format).
