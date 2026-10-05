# Technical Research

**Task**: litellm models llm_config max_output_tokens
**Generated**: 2026-10-05T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Expose LiteLLM's max_output_tokens in GET /v1/llm_models (including include_all=true). LiteLLM's /v1/model/info returns model_info.max_output_tokens (e.g. gpt-4.1 -> 32768). The backend drops it: map_litellm_to_llm_model in src/codemie/enterprise/litellm/models.py reads max_input_tokens from model_info but never max_output_tokens, so LLMModel.max_output_tokens stays None. LLMModel.max_output_tokens: Optional[int] already exists (src/codemie/configs/llm_config.py), and the router uses response_model_exclude_none=True, so no schema/router/service changes are needed. Change (minimal, one mapping): In map_litellm_to_llm_model (src/codemie/enterprise/litellm/models.py), next to max_input_tokens = model_info.get("max_input_tokens"), add max_output_tokens = model_info.get("max_output_tokens"), and pass max_output_tokens=max_output_tokens to the LLMModel(...) constructor next to max_input_tokens=max_input_tokens.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/enterprise/litellm/models.py:40` — `map_litellm_to_llm_model(litellm_model: dict) -> LLMModel`. Translates a raw LiteLLM `/v1/model/info` entry into the core `LLMModel`. Reads `model_info` and `litellm_params` sub-dicts off the raw dict and extracts fields one at a time (provider mapping, features, cost, categories, switchyard, litellm_router, multimodal/react_agent flags, etc.).
  - Line 161: `max_input_tokens = model_info.get("max_input_tokens")` — the sibling extraction the task's new line sits next to.
  - Lines 164-182: the `LLMModel(...)` constructor call where `max_input_tokens=max_input_tokens` is passed (line 179, confirmed in current source) and where `max_output_tokens=max_output_tokens` must be added.
  - There is currently no `model_info.get("max_output_tokens")` read anywhere in this function.
- `src/codemie/enterprise/litellm/models.py:185` — `get_user_allowed_models(user_id, user_applications)`. One of two call sites of `map_litellm_to_llm_model`; builds the user-scoped (external-user, LiteLLM-credential-based) model list and would pick up the fix automatically since it calls the same mapping function.
- `src/codemie/enterprise/litellm/dependencies.py:858` — `get_available_models(user_id=None, api_key=None)`. The other call site of `map_litellm_to_llm_model`; this is the path `GET /v1/llm_models` exercises for the standard (non-external) listing via the service layer.
- `src/codemie/configs/llm_config.py:209` — `LLMModel` Pydantic model. Line 221 declares `max_input_tokens: Optional[int] = None` with the comment "Context window; lets clients (e.g. CodeMie CLI) detect 1M-context models"; line 222 already declares `max_output_tokens: Optional[int] = None` with no accompanying comment. Both fields exist today — no schema change required.
- `src/codemie/rest_api/routers/llm_models.py:34-63` — `GET /v1/llm_models` route (`get_llm_models`). Declares `response_model=List[Union[LLMModel, LlmRouterOption]]` with `response_model_exclude_none=True` (line 37). Delegates to `llm_service.get_allowed_chat_models(user, include_all=include_all)` and `llm_service.get_allowed_router_options(include_all=include_all)`. The `include_all` flag only toggles `forbidden_for_web` filtering; it does not gate which model fields are serialized, so `max_output_tokens` becoming non-None flows through for both `include_all=True` and `include_all=False` calls once populated upstream.
- `src/codemie/service/llm_service/llm_service.py:536` — `get_allowed_models`, the service-layer entry point referenced by the router; sits between the router and `get_available_models`/`get_user_allowed_models`.

### Architecture and Layers Affected

- **Enterprise integration/mapping layer** (`codemie.enterprise.litellm.models`, `codemie.enterprise.litellm.dependencies`) — sole layer touched by the described minimal change; converts the external LiteLLM wire shape into the internal domain model.
- **Domain/config schema layer** (`codemie.configs.llm_config.LLMModel`) — already carries the target field; not modified by this task.
- **Service layer** (`codemie.service.llm_service.llm_service`) — orchestrates calls into the mapping layer; not modified by this task per the ticket's own scoping.
- **API layer** (`codemie.rest_api.routers.llm_models`) — serializes `LLMModel` with `response_model_exclude_none=True`; not modified by this task per the ticket's own scoping.

### Integration Points

- External: LiteLLM proxy's `/v1/model/info` endpoint, consumed by `LiteLLMService.get_available_models()` (enterprise package, outside this repo's direct source tree) and returned as the `raw_models` list that both `get_available_models()` and `get_user_allowed_models()` iterate and pass through `map_litellm_to_llm_model`.
- Internal: both callers of `map_litellm_to_llm_model` (`dependencies.get_available_models`, `models.get_user_allowed_models`) sit downstream of `LiteLLMService` and upstream of `LiteLLMModels` (chat/embedding split) and ultimately `llm_service.get_allowed_models`.

### Patterns and Conventions

- Every scalar field extraction in `map_litellm_to_llm_model` follows the same shape: `local_var = model_info.get("<litellm_key>")` immediately before the `LLMModel(...)` call, then passed as `field_name=local_var` into the constructor in roughly declaration order. `max_input_tokens` is the direct precedent for `max_output_tokens` — same `Optional[int]`, same dict, no transformation or default coercion applied (unlike e.g. `multimodal` or `enabled`, which coerce truthiness).
- Malformed/absent nested structures elsewhere in this function (e.g. `switchyard`, `litellm_router`) are defended with try/except and logging, but plain scalar `.get()` calls (like `max_input_tokens`) are not — consistent with `max_output_tokens` needing no such guard either.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/llm-providers.md` — covers LiteLLM as "provider-backed enterprise functionality," instructs gating behavior with `is_litellm_enabled`/config, and routing outbound header changes through the shared tagging helper. It does not document the `/v1/model/info` → `LLMModel` field mapping in `map_litellm_to_llm_model` specifically; the task's own description is the most precise and current source for that mapping.
- No guide under `.ai-run/guides/` documents `map_litellm_to_llm_model` or the `max_input_tokens`/`max_output_tokens` fields by name.

### Architectural Decisions

- No ADR or recorded decision specific to `max_output_tokens` or output-token exposure was found. The sibling field `max_input_tokens` carries an inline code comment explaining its purpose ("Context window; lets clients ... detect 1M-context models" — `llm_config.py:221`); `max_output_tokens` (line 222) carries no such comment, consistent with it being declared but never populated until now.

### Derived Conventions

- The one-line-per-field extraction-then-pass-through convention in `map_litellm_to_llm_model` (see Patterns above) is derived entirely from reading the function's current source, not from a guide.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/enterprise/litellm/test_models.py` — `TestMapLiteLLMToLLMModel` class, exercising `map_litellm_to_llm_model` field-by-field: `test_maps_basic_model_info`, `test_maps_api_version`, `test_handles_empty_api_version_correctly`, `test_maps_provider_strings_correctly`, `test_maps_features_correctly`, `test_handles_missing_cost_information`, `test_maps_default_categories`, `test_handles_invalid_categories_gracefully`, `test_sets_default_flag_when_global_category_present`, `test_maps_multimodal_flag`, `test_maps_supports_image_generation_flag`, `test_maps_react_agent_from_function_calling`, `test_maps_litellm_router_declaration`, `test_handles_missing_litellm_router`, plus ~24 further tests not individually surfaced by this exploration (file has more tests beyond those shown). No test asserting `max_input_tokens` or `max_output_tokens` mapping was observed in the portions of the file returned by codegraph; this is a plausible coverage gap but not confirmed exhaustively, since roughly half the file's tests were not shown verbatim.
- `tests/codemie/service/llm_service/test_litellm_service.py` — `test_get_available_models_cache_hit`, `test_get_available_models_cache_miss`, `test_get_available_models_http_error`, `test_map_and_deduplicate_models_chat_only`, `test_map_and_deduplicate_models_embedding_only`, `test_map_and_deduplicate_models_mixed`, `test_map_and_deduplicate_models_deduplication`, `test_map_and_deduplicate_models_handles_mapping_error`, `test_get_user_allowed_models_cache_hit`, `test_get_user_allowed_models_fetch_success` — exercise the two call sites (`get_available_models`, `get_user_allowed_models`) at an integration level, instantiating `LLMModel` objects as fixtures/expectations.
- `tests/enterprise/litellm/test_litellm_dependencies.py` — `test_returns_empty_when_service_unavailable`, `test_maps_and_deduplicates_models`, `test_returns_none_when_not_enabled`, `test_returns_service_when_enabled` — cover `get_available_models`'s own dependency-wiring behavior.
- `tests/codemie/rest_api/routers/test_llm_models.py` — referenced as a test file for `LLMModel` via router-level tests (full content not retrieved by this exploration).

### Testing Framework and Patterns

- pytest, consistent with `.ai-run/guides/testing/testing-patterns.md` conventions referenced in AGENTS.md. Model-mapping tests construct a raw `litellm_model` dict fixture inline (`model_name`, `model_info`, optionally `litellm_params`), call `map_litellm_to_llm_model` directly, and assert on individual `LLMModel` output fields — no mocking framework needed since the function is pure.

### Coverage Gaps

- No test in the shown portion of `tests/enterprise/litellm/test_models.py` directly asserts on `max_input_tokens` or `max_output_tokens` propagation from `model_info` into `LLMModel`. Adding the described one-line change without an accompanying test (if tests are in scope) would leave this specific field pairing unverified by the existing suite's visible tests.

---

## 5. Configuration and Environment

### Environment Variables

- None specific to `max_output_tokens` or this mapping were found. `MODELS_ENV` (documented in `.ai-run/guides/integration/llm-providers.md`, evidenced at `README.md:61`) governs which `llm-<env>-config.yaml` is loaded by `LLMConfig` (`src/codemie/configs/llm_config.py:392`), but that path is for the static YAML catalog, not the LiteLLM-proxy-sourced dynamic mapping this task touches.

### Configuration Files

- `src/codemie/configs/llm_config.py` — defines `LLMModel` (the Pydantic schema already carrying `max_output_tokens`) and loads `llm-{MODELS_ENV}-config.yaml` for the static model catalog; unrelated to the dynamic LiteLLM proxy mapping path this task changes.
- No config file governs the LiteLLM `/v1/model/info` → `LLMModel` field mapping itself; it is pure Python logic in `map_litellm_to_llm_model`.

### Feature Flags and Deployment Concerns

- `is_litellm_enabled()` (`src/codemie/enterprise/litellm/dependencies.py:44`) gates whether the LiteLLM-backed path runs at all (checks `HAS_LITELLM` and `config.LLM_PROXY_ENABLED`), but this gating is unrelated to and unaffected by the `max_output_tokens` field addition — the mapping function already runs unconditionally once this gate passes.

---

## 6. Risk Indicators

- Low technical risk overall: the change is a single additional `.get()` read plus one constructor kwarg, directly mirroring an existing, already-tested pattern (`max_input_tokens`) in the same function.
- Coverage gap: no test in the visible portion of `tests/enterprise/litellm/test_models.py` currently asserts `max_input_tokens`/`max_output_tokens` mapping; if the task's scope includes tests, a new test case following the existing `TestMapLiteLLMToLLMModel` pattern would need to be added, but this exploration could not confirm with certainty that no such test exists further down in the file (only part of the file was returned).
- The task's own description already fully specifies the exact code change (file, line context, field name, constructor placement) — there is minimal design ambiguity left for the spec/plan stages to resolve beyond deciding whether to add a test.
- Speculative: if a test is added, it would most naturally extend `TestMapLiteLLMToLLMModel` with a case asserting `result.max_output_tokens == <value>` using a `model_info` dict containing `"max_output_tokens": <value>`, mirroring `test_maps_api_version`'s shape — this is a design choice for the spec/plan stage, not a discovered requirement.

---

## 7. Summary for Complexity Assessment

This task touches exactly one function, `map_litellm_to_llm_model` in `src/codemie/enterprise/litellm/models.py`, in exactly one layer: the enterprise LiteLLM-to-core-domain mapping layer. The target schema field (`LLMModel.max_output_tokens`, `src/codemie/configs/llm_config.py:222`) already exists, and the API layer (`GET /v1/llm_models`, `src/codemie/rest_api/routers/llm_models.py`) already serializes it correctly via `response_model_exclude_none=True` with no gating by `include_all`. No router, service, or schema changes are needed — the task's own description confirms this and it is corroborated by the current source of all three layers. File-change surface is therefore minimal: one function, two new lines, following an existing, already-tested sibling pattern (`max_input_tokens`) line-for-line.

Technical novelty is essentially zero — the change duplicates an established extraction/constructor pattern already present and tested in the same function for a near-identical field. The two call sites of `map_litellm_to_llm_model` (`dependencies.get_available_models` and `models.get_user_allowed_models`) both pick up the fix automatically with no additional wiring.

The main gap worth flagging is testing posture: the existing `TestMapLiteLLMToLLMModel` suite in `tests/enterprise/litellm/test_models.py` does not visibly cover `max_input_tokens`/`max_output_tokens` mapping, so if tests are in scope for this task, one new test case is the only additional surface area — still a single-digit line change. No external services, migrations, or config changes are implicated.

---

## 8. External References

None named by the task. The task's `task_context` is a fully self-contained ticket description naming only in-repo files (`src/codemie/enterprise/litellm/models.py`, `src/codemie/configs/llm_config.py`) already covered in Section 2; it names no external file path or URL to resolve.
