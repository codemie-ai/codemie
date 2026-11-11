# Technical Research

**Task**: litellm router max_output_tokens llm_factory
**Generated**: 2026-11-05T16:10:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-16835 (Bug): codemie-jira-assistant fails to create Jira tickets when the output token limit is reached — replies are cut, leaving partially created tickets. Root cause (proven from prod logs 2026-11-04/05): assistants on the LiteLLM auto-router model `claude-router-standard` get no max_tokens from codemie because the router's LiteLLM model_info has no `max_output_tokens`; codemie logs `llm_output_limit model=claude-router-standard max_tokens=unset source=proxy_default` and every truncation is `finish_reason=length output_tokens=4096` (proxy default). Direct tier models declare max_output_tokens=128000 and work. Chosen fix scope (codemie repo): when an LLMModel is a declared LiteLLM auto-router (model_info.litellm_router with tiers) and has no max_output_tokens, derive it from its tier models — use the smallest positive max_output_tokens among the tiers — mirroring how LLMService._router_max_input_tokens derives max_input_tokens from tiers. Then the existing forwarding in enterprise/litellm/llm_factory.py (_resolve_max_output_tokens / _build_chat_model_base_args, gated by LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS and the Claude check) sends max_tokens for the router. Explicit max_output_tokens on the router entry must still win. Out of scope: proxy config change, Jira assistant prompt behavior (AC2/AC3), the 'clean stop at line 100' tier behavior. Acceptance: router requests forward max_tokens = min tier max_output_tokens; regression tests cover the derivation and forwarding.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/configs/llm_config.py`
  - `RouterTier` (L139-143: `model`, `label`), `RouterTiers` (L146-155: required `simple`, `medium`, `complex`, `reasoning`), `LiteLLMRouterConfig` (L158-176: `tiers: RouterTiers | None`).
  - `LLMModel` (L229-279): `max_input_tokens` (L241), `max_output_tokens: Optional[int] = None` (L242), `claude_family` (L245, excluded from serialization), `litellm_router` (L249), `is_declared_litellm_router()` (L271-279). Not frozen.
  - `LlmRouterOption` (L190-211) exposes `max_input_tokens` only; no `max_output_tokens`.
- `src/codemie/enterprise/litellm/models.py::map_litellm_to_llm_model` (L91-227) maps one raw `/model/info` entry. It can't see tier models; `max_output_tokens` is passed through verbatim (L203, L222).
- `src/codemie/enterprise/litellm/dependencies.py::_map_raw_models` (L841-877) maps all raw models, drops disabled ones (L855), and buckets them by type. Called from `get_available_models` (L880+).
- `src/codemie/service/llm_service/llm_service.py`
  - `_router_max_input_tokens(tiers, by_name)` (L145-158) is a staticmethod. It takes the distinct tier names, resolves them by `base_name`, returns None if any tier is missing or has no window, and otherwise returns the min. It is computed per REST option and never stored.
  - `initialize_default_litellm_models` (L735-745) stores the live chat catalog in `_default_litellm_models`. It's the single sink for all three writers: startup and recovery (`rest_api/litellm_models_recovery.py` L48-58, L81-109) and admin reload (`rest_api/routers/admin.py` L240-254).
  - `get_model_details` (L404-451) → `_find_model_or_router_target` (L329-350) returns the router's own LLMModel for a LiteLLM auto-router alias.
  - `_apply_premium_flags` (L960-972) mutates catalog models in place, which is a precedent for in-place updates.
- `src/codemie/core/dependecies.py::get_llm_by_credentials` (L250-336): `get_model_details` → `get_litellm_chat_model(llm_model_details=...)`. For a router alias, `llm_model_details` is the router entry with `max_output_tokens=None` today.
- `src/codemie/core/router_chat_model.py::build_chat_model_for` (L278-311) and `enterprise/litellm/router.py::LiteLLMRouter.candidate_models()` (the alias is the only candidate) mean the router's chat model is built from the router's own catalog entry.
- `src/codemie/enterprise/litellm/llm_factory.py`
  - `_resolve_max_output_tokens` (L393-406): Claude check (name contains "claude" or `claude_family`), positive int only.
  - `_build_chat_model_base_args` (L409-440): forwards `max_tokens` when `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS` is on.
  - `_filter_litellm_params` (L1312-1313): `features.max_tokens is False` → None.
  - `_log_output_limit` (L1321-1336): source labels `model_config`, `disabled_by_feature`, `disabled_by_config`, `proxy_default`.

### Architecture and Layers Affected
- **Service layer:** `LLMService` catalog initialisation and router derivation helpers. This is the primary change site.
- **Config/domain model:** `LLMModel` and `RouterTiers`, read only. No schema change is needed if the derived value is stored in the existing `max_output_tokens` field.
- **Enterprise LLM factory:** `llm_factory.py`, unchanged if the value is derived at catalog load. Forwarding and logging already handle a positive `max_output_tokens` (the log reads `source=model_config`).

### Integration Points
- Catalog writers: startup, recovery loop and admin reload all go through `LLMService.initialize_default_litellm_models`.
- Request path: `get_llm_by_credentials` → `get_model_details` → `get_litellm_chat_model` → `create_litellm_chat_model` → `LiteLLMChatOpenAI(max_tokens=...)`.
- External: LiteLLM proxy `/model/info` provides the router entry (`model_info.litellm_router.tiers`) and the tier entries (`model_info.max_output_tokens`).

### Patterns and Conventions
- Router derivation is a pure staticmethod on `LLMService` over `(tiers, by_name)`, resolved by `base_name`, conservative None for unknown tiers (`_router_max_input_tokens`).
- A single predicate, `is_declared_litellm_router()`, for every router check.
- Lazy imports of enterprise code inside functions to avoid circular imports (see the `_prefer_litellm_router` docstring).
- Catalog models are mutated in place (`_apply_premium_flags`), and tests use `model_copy(update=...)`.
- Defensive handling of live-proxy data: never break the catalog fetch.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` exists. No guide covers `max_output_tokens`, `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS` or auto-routers.
- `.ai-run/guides/testing/testing-patterns.md`: "Seam Tests for Policy Helpers". Test the helper AND the call site at the outer boundary, here the `max_tokens` kwarg passed to `LiteLLMChatOpenAI` for a router.
- `.ai-run/guides/quality-gates.md`: lint gate is `make ruff`.

### Architectural Decisions
- EPMCDME-15787 (`docs/superpowers/tasks/2026-10-12-epmcdme-15787-opus-5-5-max-output-tokens/`, commit 00b964548) added Claude-only forwarding behind `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS`. It didn't handle an auto-router entry with no `max_output_tokens`.
- EPMCDME-15572 exposed `max_output_tokens` in the catalog with no router or service changes.
- The `_router_max_input_tokens` docstring says an unknown tier must never allow a larger value (conservative None).
- The `LiteLLMRouterConfig` docstring says routers are declared explicitly in proxy `model_info`, because LiteLLM gives no router signal.

### Derived Conventions
- Put derivation helpers on `LLMService` as staticmethods, or as pure module functions in `llm_config.py`.
- Log in key=value style (`llm_output_limit model=... max_tokens=... source=...`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/enterprise/litellm/test_llm_factory.py`: `TestMaxOutputTokensForwarding` (L1614-1770, helpers `_model_details`, `_create`, `_create_logged`), `TestFilterLiteLLMParamsMaxTokensForwarding` (L1773-1806).
- `tests/enterprise/litellm/test_models.py`: max_output_tokens mapping, claude_family, litellm_router parsing.
- `tests/codemie/service/test_llm_service.py`: `TestLiteLLMAutoRouterCatalog` (L580+, helpers `_router_model`, `_config`).
- `tests/codemie/service/llm_service/test_llm_service_litellm_integration.py`: `test_initialize_default_litellm_models` (L74-90).
- `tests/codemie/core/test_router.py`, `tests/codemie/core/test_router_chat_model.py`: LiteLLM router resolution and wrapping.

### Testing Framework and Patterns
pytest 8 with pytest-asyncio, pytest-xdist (`-n 3`), pytest-mock, pytest-env. Tests are grouped in classes, use `patch.object(config, ...)`, real Pydantic `LLMModel`/`RouterTiers` in service tests, and `LLMService(LLMConfig(yaml_file=..., llm_models=[...]))` for isolated instances. Enterprise imports are local inside test bodies.

### Coverage Gaps
- `_router_max_input_tokens` has no direct test.
- Nothing covers `max_output_tokens` for a declared LiteLLM router, or forwarding end to end for a router alias.
- No test checks that `initialize_default_litellm_models` changes model fields.
- No test covers routers whose tier models are missing, disabled, or have no `max_output_tokens`.

---

## 5. Configuration and Environment

### Environment Variables
- `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS` (config.py L731-732, default True): kill switch for forwarding.
- `LLM_PROXY_ENABLED`, `LLM_PROXY_MODE=lite_llm`: gate the LiteLLM catalog and factory.
- `LITE_LLM_URL`, `LITE_LLM_APP_KEY`, `MODELS_ENV`.

### Configuration Files
- `config/llms/llm-*-config.yaml`: static YAML catalogs with per-model `max_output_tokens`, and no `litellm_router` entries.
- `litellm_config.yaml`: local proxy config, with no router entry. The prod router config is not in the repo.

### Feature Flags and Deployment Concerns
- `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS` and the per-model `features.max_tokens` (from `supported_openai_params`). In prod, `source=proxy_default` shows `features.max_tokens` is not False for the router.
- Code-only fix. No deployment manifest changes.

---

## 6. Risk Indicators

- **Unknown or disabled tiers.** Disabled tier models are dropped at mapping (`dependencies.py` L855), so they look unknown. Policy choice: min over the known positive tier values, or None when any tier is unknown (the `max_input_tokens` precedent, which keeps the 4096 proxy default). The latter is safer for "must not exceed any tier's limit"; the former is more permissive.
- **Value too high for a tier.** Sending more than a tier's limit makes the provider reject the request, so the min must cover every tier the router can choose.
- **Claude gate.** Forwarding requires "claude" in the router's base_name or `claude_family`. `claude-router-standard` passes on its name. A router alias without "claude" in the name won't forward even if its tiers are Claude, which is a residual gap and out of scope unless widened deliberately.
- **Catalog freshness.** A load-time derivation is recomputed on every catalog write (startup, recovery, admin reload). Tier changes need a reload, the same as for any other model metadata.
- **Circular imports.** `llm_service.py` must not import enterprise eagerly (`_prefer_litellm_router` docstring).
- **Visibility.** `max_output_tokens` is serialized on `LLMModel`, but routers are excluded from chat listings and `LlmRouterOption` has no such field, so no API/UI change is expected. The OpenAPI baseline is unaffected unless the schema changes.
- **Logging.** If the value is stored on the router model, the log will say `source=model_config` and won't show that it came from the tiers. Adding a debug or info line at derivation time would help diagnosis.

---

## 7. Summary for Complexity Assessment

The change sits in the service layer: a pure helper on `LLMService`, mirroring `_router_max_input_tokens`, that computes the smallest positive `max_output_tokens` across a declared LiteLLM auto-router's tier models. It is applied once in `initialize_default_litellm_models`, the single sink for every catalog writer, and only when the router entry has no positive `max_output_tokens` of its own. The existing forwarding path in `enterprise/litellm/llm_factory.py` then sends `max_tokens` for the router unchanged. Expected surface: 1-2 production files (`llm_service.py`, optionally a log line) and 2-3 test files. No schema, API, config or deployment changes.

The technical approach is familiar: it reuses the existing tier-resolution pattern (`by_name` keyed on `base_name`), the existing forwarding and its kill switch, and the in-place catalog mutation precedent. The main design decision is the policy for unknown or disabled tiers (conservative None versus min over the known tiers). A secondary decision is whether to log the derivation.

Test coverage is good for forwarding (`TestMaxOutputTokensForwarding`) and router catalog options, but there's none for router-level limit derivation, for `_router_max_input_tokens`, or for the router alias end to end. The seam-test guide requires both a helper test and an outer-boundary assertion on the `max_tokens` kwarg. Risk is low to moderate: the logic is small and fully reversible through the existing kill switch. The main correctness risk is sending a value above some tier's limit, which the min-across-all-tiers rule prevents.
