# Technical Research

**Task**: llm models catalog litellm llm_models (EPMCDME-14763, backend part)
**Generated**: 2026-09-29
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14763 (backend part) — expose `max_input_tokens` per model through GET /v1/llm_models so the CodeMie CLI (separate repo) can replace its hard-coded Claude 1M-context version table (`supportsOneMillionContext()`) with real data. LiteLLM's /v1/model/info already reports max_input_tokens; CodeMie's catalog drops it today.
Acceptance-level requirements: (1) every real model returned by /v1/llm_models (and other endpoints returning LLMModel: /v1/default_models, /v1/default_models/{category}, /v1/embeddings_models, /v1/llm_models/image_generation) carries `max_input_tokens` when known; (2) field is omitted (not null) when unknown — endpoints already use response_model_exclude_none; (3) no behavior change for existing consumers.
Implementation is ALREADY PRESENT as uncommitted working-tree changes on branch EPMCDME-14763_expose-max-input-tokens (cut from main): `max_input_tokens: Optional[int] = None` added to `LLMModel` in src/codemie/configs/llm_config.py (next to `max_output_tokens`), and `map_litellm_to_llm_model` in src/codemie/enterprise/litellm/models.py now reads `model_info.get("max_input_tokens")` and passes it to LLMModel. Verified manually against a local backend: 57/57 models on /v1/llm_models carry the field; the 3 router options (LlmRouterOption) do not.
Please research (read-only) and document: the LLMModel data flow (YAML static catalogs in config/llms/*.yaml vs live LiteLLM mapping, llm_service.get_all_llm_model_info), every consumer/serializer of LLMModel that a new optional field could affect (responses, DB/ES persistence, dumps to litellm config, snapshot/equality tests, OpenAPI), existing tests around map_litellm_to_llm_model and /v1/llm_models and where a new test would belong (do NOT write tests), whether static YAML catalogs should/could carry max_input_tokens, whether LlmRouterOption would need it, and risks. Note: AGENTS.md says tests are written/run only when explicitly requested by the user; git operations only when requested.
feature_area: llm models catalog litellm llm_models

---

## 2. Codebase Findings

Python 3.12 / Poetry, FastAPI, Pydantic v2, litellm 1.99.2 pinned; `codemie-enterprise` 2.3.44 is an optional dep and is NOT in this repo.

### Existing Implementations
- `src/codemie/configs/llm_config.py:207` `LLMModel(BaseModel)` — domain model. Working-tree diff adds line 219 `max_input_tokens: Optional[int] = None`, directly above `max_output_tokens`.
- `src/codemie/enterprise/litellm/models.py:40` `map_litellm_to_llm_model(litellm_model)` — the only place in `src` that constructs `LLMModel(...)` outside the YAML loader (grep of `LLMModel(` in src: this file line 164 and the class itself). Diff: line 161 `max_input_tokens = model_info.get("max_input_tokens")`, passed at line 179. It is the single mapping choke point; all other fields (cost, features, api_version, etc.) are read from `model_info` the same way.
- Callers of the mapper: `src/codemie/enterprise/litellm/models.py:248` (`get_user_allowed_models`-style path, per-user credentials) and `src/codemie/enterprise/litellm/dependencies.py:906` (`get_available_models`). Both dedupe by `base_name` into `LiteLLMModels(chat_models, embedding_models)`. Re-exported from `codemie/enterprise/__init__.py` and `codemie/enterprise/litellm/__init__.py`.
- `src/codemie/service/llm_service/llm_service.py:52` `get_all_llm_model_info()` — returns `self._default_litellm_models` when `config.LLM_PROXY_ENABLED` and that list is non-empty (populated at line ~503 from `user_models.chat_models`, via `initialize_default_litellm_models`, also called from `admin.py:289` reload), else the enabled models of `llm_config.llm_models` (static YAML). Embeddings analogous (`get_all_embedding_model_info`, `_default_litellm_embeddings`). Comment in the file: no merge between live and YAML catalogs.
- `src/codemie/rest_api/routers/llm_models.py` — five endpoints, all `response_model_exclude_none=True`:
  - `/v1/llm_models` -> `List[Union[LLMModel, LlmRouterOption]]` (chat models + `get_allowed_router_options`)
  - `/v1/llm_models/image_generation`, `/v1/embeddings_models` -> `List[LLMModel]`
  - `/v1/default_models` -> `Dict[str, LLMModel]`; `/v1/default_models/{category_id}` -> `LLMModel`
  No router code change is needed; the new field flows through the response model automatically.

### Architecture and Layers Affected
- Config/domain model layer (`configs/llm_config.py`), enterprise integration adapter (`enterprise/litellm/models.py`), service layer (`llm_service`) as pass-through, REST layer (`routers/llm_models.py`) as pass-through (serialization only). Also the auto-generated OpenAPI schema (`LLMModel` component gains one optional property).

### Integration Points
- Upstream data source: LiteLLM proxy `/model/info` -> enterprise `litellm.get_available_models()` (raw dicts, `model_info` sub-dict) -> core mapper. Field name in LiteLLM is exactly `max_input_tokens`.
- Downstream consumers of `LLMModel` beyond the REST layer read specific attributes only (e.g. `src/codemie/core/dependecies.py:399,427,465,489` use `max_output_tokens`; none use a whole-model dump). Grep for `model_dump`/`.dict()` on LLMModel instances in src found no persistence or dump-to-litellm-config path; `llm_config.py:322` `model_copy` is on switchyard tuning, not LLMModel. No DB/ES model stores an `LLMModel`; no `LLMModel` -> litellm YAML dump exists in this repo (`litellm_config.yaml` at repo root has no `max_input_tokens`/`max_output_tokens`).
- `LlmRouterOption` (`llm_config.py` ~line 180) is a separate REST projection built in `llm_service._build_switchyard_router_option` (~line 120) and `_build_litellm_auto_router_option` (~line 165). It carries only base_name, label, provider, capabilities, tiers, strategy, classifier_model. It does not inherit from LLMModel, so is unaffected by the change (matches the manual 57/57 check, routers absent).

### Patterns and Conventions
- Optional numeric metadata as `Optional[X] = None` on `LLMModel`, omitted on the wire by `response_model_exclude_none`. Existing precedent: `is_premium` ("appears in JSON only when set"), `max_output_tokens`, `api_version`.
- Mapper reads `model_info.get(...)` with no coercion/validation for scalar fields (only cost/categories/switchyard/router get try/except).
- `.ai-run/guides/` exists (API, data, integration/llm-providers guides). Not read in depth; task is a 2-line additive change.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/llm-providers.md` (model provider config) and `.ai-run/guides/api/endpoint-conventions.md` are the relevant guides by AGENTS.md classification; no LLMModel field documentation was searched for beyond this.
- `docs/superpowers/tasks/2026-08-07-epmcdme-13459-fix-token-limit-message/` mentions `max_output_tokens`; unrelated to this change.
- `CHANGELOG.md` has a single commit in history (initial commit); no evidence of per-ticket changelog entries.

### Architectural Decisions
- Code comment in `get_llm_routers`: one catalog source per view (live LiteLLM OR YAML), never merged.
- Comment in `map_litellm_to_llm_model` path / `LiteLLMRouterConfig`: LiteLLM is treated as source of truth when proxy is enabled.

### Derived Conventions
- New LLMModel metadata is added to the model, then to the mapper, plus (optionally) YAML entries. No versioned API schema or snapshot.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/enterprise/litellm/test_models.py` — `TestMapLiteLLMToLLMModel` (line 20): one test per mapped concern, each passing a `{"model_name":..., "model_info": {...}}` dict and importing the mapper inline, e.g. `test_maps_cost_information` (162), `test_handles_missing_cost_information` (183), `test_handles_max_completion_tokens_param` (366). No test references `max_input_tokens`. `TestGetUserAllowedModels` (383) covers mapping+dedupe.
- `tests/codemie/rest_api/routers/test_llm_models.py` — TestClient against `app`, patches `codemie.rest_api.routers.llm_models.llm_service` with autospec, constructs `LLMModel(...)` directly. `test_llm_models_is_premium_serialization` (line 376) is the direct template: asserts the field is present when set and absent (`not in payload`) when None. Other tests: default_models, embeddings, image_generation, include_all filtering.
- `tests/codemie/configs/test_llm_config.py` — YAML loading (`test_llm_config_loading`, line 60); `tests/codemie/service/test_llm_service.py`, `tests/codemie/service/llm_service/test_llm_service_litellm_integration.py`, `test_litellm_service.py` (patches `LiteLLMService.map_litellm_to_llm_model`, enterprise class) cover the service/catalog selection.

### Testing Framework and Patterns
- pytest (`pytest.ini`), `fastapi.testclient.TestClient`, `unittest.mock.patch`/`MagicMock`, plain dict fixtures for LiteLLM payloads. No snapshot/golden-file tests of LLMModel JSON found; equality assertions are per-field, so an added optional field with default None does not break them.

### Coverage Gaps
- No test asserts `max_input_tokens` mapped (present, absent -> None) nor serialized/omitted at the REST boundary. Natural homes: `tests/enterprise/litellm/test_models.py::TestMapLiteLLMToLLMModel` (mapping, present + missing) and `tests/codemie/rest_api/routers/test_llm_models.py` next to `test_llm_models_is_premium_serialization` (omit-when-None, present-when-set; optionally confirm router option lacks it). Per AGENTS.md, do not write or run these unless the user asks.

---

## 5. Configuration and Environment

### Environment Variables
- `LLM_PROXY_ENABLED` (config) selects live LiteLLM vs YAML catalog in `get_all_llm_model_info`. `MODELS_ENV` selects `config/llms/llm-{env}-config.yaml` (`llm_config.py:390`).

### Configuration Files
- `config/llms/llm-{aws,azure,dial,gcp}-config.yaml` — static catalogs (10 azure, 24 dial, 7 aws, 4 gcp `base_name` entries) loaded into `LLMConfig` (`llm_models`, `embeddings_models`) via a YAML settings source. They carry `max_output_tokens` on some entries (azure 9, dial 17, aws 6, gcp 3) but none carry `max_input_tokens` (no occurrence in src/config).
- Because `LLMModel` now declares the field, YAML entries could add `max_input_tokens: <int>` with no loader change (I did not verify how `LLMYamlSettings` treats unknown keys; irrelevant once declared).

### Feature Flags and Deployment Concerns
- No new env var or flag. Backward-compatible additive response field; no migration, no DB change.

---

## 6. Risk Indicators

- Low overall: additive optional field, default None, omitted via `response_model_exclude_none`; no persistence of LLMModel found in this repo.
- YAML-mode (LLM_PROXY_ENABLED false, or live catalog empty) returns models without `max_input_tokens` because no static catalog declares it. The CLI would see the field only in proxy mode. Speculative: if CLI must work in non-proxy deployments, YAML entries would need values, which duplicates LiteLLM data and can drift; acceptable to treat as out of scope since the ticket says LiteLLM is the source.
- Live catalog is cached in `_default_litellm_models` (set at startup / admin reload); after deploy, the field appears only once the catalog is re-initialized (restart or `admin.reload_llm_models`). Per-user catalogs (`enterprise/litellm/models.py:248` path) are fetched on demand.
- Value fidelity: mapper passes `model_info["max_input_tokens"]` unvalidated. If a proxy returned a non-int (e.g. string or float), Pydantic would coerce or raise; a raise is caught by the per-model `except Exception` at `dependencies.py` / `models.py:248` which logs an error and drops that whole model from the catalog. LiteLLM emits ints, so low likelihood, but the blast radius is model disappearance, not a null field. Speculative: cheap hardening would be tolerant parsing.
- LiteLLM values for `max_input_tokens` can be absent for custom/unknown models, giving None and omission (matches requirement 2). For LiteLLM auto-router entries (live LLMModel with `litellm_router`) the projected `LlmRouterOption` has no such field; Speculative: a client that treats routers as models cannot look up context size for them, but router context is ambiguous (depends on tier), so omitting is defensible. The CLI ticket only concerns real Claude models.
- Enterprise package (`LiteLLMService.map_litellm_to_llm_model` referenced in `test_litellm_service.py`) is outside this repo; I could not inspect whether it maps or drops fields itself. Core function above is the one used by the two `dependencies`/`models.py` callers.
- OpenAPI schema for `LLMModel` gains a property; any generated client or contract test on OpenAPI in other repos (CLI, UI) should tolerate an additional optional key. No in-repo schema snapshot was found.
- Unrelated pre-existing working-tree modification: `.ai-run/sdlc-factory/doctor.json` is modified; should not be part of the feature commit.

---

## 7. Summary for Complexity Assessment

The change touches two source files and three layers only nominally: the domain model (`LLMModel` in `configs/llm_config.py`) and the LiteLLM-to-core mapper (`enterprise/litellm/models.py`). The service layer (`llm_service.get_all_llm_model_info`) and all five REST endpoints in `routers/llm_models.py` pass `LLMModel` through unchanged and already use `response_model_exclude_none=True`, so the field appears automatically. It is already implemented in the working tree (about 3 changed lines of production code) and manually verified.

Technical novelty is nil: the exact pattern exists for `max_output_tokens`, `api_version` and `is_premium`. No DB/ES persistence, litellm config dump, or snapshot test of `LLMModel` was found in the repo, so blast radius is limited to the JSON responses and OpenAPI schema. `LlmRouterOption` is an independent projection and does not need the field for the stated requirement.

Test posture: solid existing harnesses (`tests/enterprise/litellm/test_models.py`, `tests/codemie/rest_api/routers/test_llm_models.py` with an `is_premium` serialization template), but there is no test for the new field; adding them is a small unit-level job if the user asks. Main risks are behavioral rather than structural: YAML/non-proxy mode doesn't carry the field, catalog caching delays visibility until reload, and unvalidated passthrough. Expected size band: trivial/small.

---

## 8. External References

None named by the task. (The CLI repo's `supportsOneMillionContext()` is described but no path was given; not consulted.)
