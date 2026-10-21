# Technical Research

**Task**: litellm models startup retry
**Generated**: 2026-10-19
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-12423 (Bug, Jira). Summary: Codemie silently falls back to default model configuration if LiteLLM service is unavailable at container startup, requiring manual restart to enable LiteLLM models.

Description: When Codemie is configured to use LiteLLM and the LiteLLM service is unavailable (wrong endpoint / not ready) at Codemie container startup, Codemie fails to fetch models, logs errors ("Error fetching models ([Errno -2] Name or service not known)", then "0 LiteLLM chat/embedding models initialized"), and silently initializes with zero LiteLLM models using default config. LiteLLM models are not used until the container is manually restarted. Expected: Codemie should not silently fall back; it should notify operator/admin that LiteLLM models are missing, implement a retry or detection mechanism to load LiteLLM models when the service becomes available, and avoid requiring a manual restart.

Affected areas: startup logic, model loading/initialization, LiteLLM integration handling, error handling/reporting.

Acceptance criteria:
1. If LiteLLM is unavailable at startup, log a clear warning/error; do not silently ignore missing models.
2. Implement a retry mechanism or automated LiteLLM model loading when the service becomes available.
3. Expose notification in logs/UI so operators know LiteLLM models are not active.
4. Manual restart not required for LiteLLM models to become available after service recovery.
5. Regression tested with both available and unavailable LiteLLM scenarios.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/main.py:153` `_initialize_litellm_models()` - one-shot startup load: calls `get_available_models()`, then `llm_service.initialize_default_litellm_models(models)`, logs "Initialized N chat models, and M embedding models." Wrapped in `try/except Exception` that only logs `Failed to initialize LiteLLM models`. No retry.
- `src/codemie/rest_api/main.py:370` `_setup_litellm_features()` - if `is_litellm_enabled()`: calls `_initialize_litellm_models()`, then (if `LLM_PROXY_BUDGET_CHECK_ENABLED`) `_setup_litellm_cache_cleanup_scheduler()`. Called once in `lifespan` (line ~861), before `_initialize_preconfigured_content()` (which depends on LiteLLM default model resolving; a test enforces this order).
- `src/codemie/enterprise/litellm/dependencies.py:858` `get_available_models(user_id, api_key)` - returns `LiteLLMModels`; returns empty `LiteLLMModels()` if service is None, if raw list empty, AND on any exception (`logger.error("Error getting available models")`). Errors are therefore indistinguishable from "no models" to the caller; this is the root of the silent fallback. The "Error fetching models ([Errno -2]...)" text is not in this repo (grep found none), so it likely originates in the external `codemie_enterprise.litellm.LiteLLMService.get_available_models` (not inspected, outside repo).
- `src/codemie/service/llm_service/llm_service.py:683` `initialize_default_litellm_models()` - overwrites `_default_litellm_models` / `_default_litellm_embeddings`, logs "Initialized N unique LiteLLM chat models and M unique ... embedding models" (this is the "0 ... initialized" message; logged at info even for 0).
- `llm_service.py:52-80` `get_all_llm_model_info()` / `get_all_embedding_model_info()` - the silent fallback: if `config.LLM_PROXY_ENABLED and self._default_litellm_models` is falsy, returns YAML models. `get_llm_routers()` derives from the same.
- `src/codemie/rest_api/routers/admin.py:~270-290` - admin "reload LLM models" endpoint: `require_litellm_enabled()`, `get_active_llm_proxy_provider().reload_models_cache()`, `get_available_models()`, `llm_service.initialize_default_litellm_models(models)`. A manual path exists today and is the reuse candidate for a reload routine.
- `src/codemie/enterprise/litellm/llm_proxy_provider_adapter.py` - `LiteLLMLLMProxyProvider`: `is_available()` returns True always; `reload_models_cache()` clears `service.models_cache`.
- `src/codemie/enterprise/litellm/dependencies.py:~70` `initialize_litellm_from_config()` - builds `LiteLLMConfig` (url, master key, `max_retries` from `LITELLM_MAX_RETRIES` or 3, timeouts, cache TTLs). Service construction does not contact LiteLLM; failure to reach it only surfaces at the models fetch.
- `main.py:336` `_initialize_enterprise_services` - registers service, proxy provider, budget provider; `ensure_team_exists` failure only warns.

### Architecture and Layers Affected
- App lifecycle/startup (`rest_api/main.py` lifespan, background tasks list `tasks`, APScheduler usage).
- Enterprise integration adapter (`enterprise/litellm/dependencies.py`).
- Service layer (`service/llm_service/llm_service.py` - model catalog state).
- API layer (`rest_api/routers/common.py` health endpoints, `routers/admin.py` reload).
- Config (`configs/config.py`).

### Integration Points
- External package `codemie_enterprise.litellm` (`LiteLLMService`), gated by `HAS_LITELLM` in `enterprise/loader.py`.
- `llm_service` singleton consumed by assistants, preconfigured content, routers (Switchyard `build_switchyard_routers`).
- `main.py` imports `service.model_provider_readiness` (via `app.state.model_provider_readiness`).

### Patterns and Conventions
- Startup background work: `asyncio.create_task(..., name=...)` appended to `tasks` (e.g. `_schedule_customer_config_refresh`, a sleep-loop task; `_schedule_budget_reconciliation`, `_schedule_startup_recovery`), and cancelled on shutdown. Also `AsyncIOScheduler` jobs (`_setup_litellm_cache_cleanup_scheduler`, `_setup_*_scheduler(app)`).
- Blocking calls offloaded with `asyncio.to_thread` (e.g. `ensure_team_exists`).
- Non-fatal startup pattern: log warning, never abort (`_warm_customer_config`, `_initialize_deployment_version`, model provider readiness with `asyncio.wait_for` timeout).
- Operator-visible readiness pattern: `app.state.model_provider_readiness` (NamedTuple `ModelProviderReadiness(status, provider, missing)`; enum `ModelProviderStatus`: configured / not_configured / not_checked), logged via `log_model_provider_readiness`, exposed on unauthenticated `GET /v1/healthcheck/model` (`routers/common.py:69`). Comment there: `missing` entries must name settings, never values.
- Config: pydantic settings in `configs/config.py` with inline-commented defaults (LITELLM_* near lines 765-775).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists. Relevant: `integration/external-services.md`, `integration/llm-providers.md`, `development/error-handling.md`, `development/logging-patterns.md`, `development/configuration-patterns.md`, `architecture/service-layer-patterns.md`, `testing/testing-patterns.md`, `testing/local-verification.md`, `quality-gates.md`. (Listed from directory/AGENTS.md; contents not read in this pass.)

### Architectural Decisions
Inline comments in `main.py`: preconfigured content must run after `_setup_litellm_features()` so default model resolves against LiteLLM rather than YAML (`_initialize_preconfigured_content` docstring); readiness checks never block startup (EPMCDME-14653).

### Derived Conventions
Failures at startup are logged and swallowed; new status is surfaced via `app.state` and healthcheck routes; singletons registered at lifespan.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/test_startup_integration.py` - lifespan with LiteLLM enabled; patches `_initialize_litellm_models`, `initialize_litellm_from_config`, `is_litellm_enabled`; asserts ordering (litellm models before preconfigured assistants). Has `TestLiteLLMServiceInitialization`.
- `tests/codemie/service/llm_service/test_llm_service_litellm_integration.py` - `initialize_default_litellm_models` including empty case, fallback selection.
- `tests/codemie/service/llm_service/test_litellm_service.py`, `test_utils_litellm_context.py`.

### Testing Framework and Patterns
pytest (`pytest.ini`), `unittest.mock.patch`, async tests for lifespan, fixtures like `mock_litellm_service`, `mock_non_litellm_startup`.

### Coverage Gaps
No test found for `_initialize_litellm_models` failure/empty-result behavior, for `get_available_models` exception-to-empty conversion, or for any recovery/retry path (none exists). Not verified for healthcheck route tests regarding LiteLLM.

---

## 5. Configuration and Environment

### Environment Variables
Existing: `LLM_PROXY_ENABLED`, `LLM_PROXY_MODE`, `LITE_LLM_URL`, `LITE_LLM_MASTER_KEY`, `LITE_LLM_APP_KEY`, `LITELLM_MAX_RETRIES` (read via hasattr, not declared in the grep'd lines), `LITELLM_REQUEST_TIMEOUT` (5.0), `LITELLM_LIST_REQUEST_TIMEOUT` (30.0), `LITELLM_MODELS_CACHE_TTL` (1800), `LITELLM_CUSTOMER_CACHE_TTL` (300), `LITELLM_FAIL_OPEN_ON_503`, `LLM_PROXY_BUDGET_CHECK_ENABLED`. No existing setting for model-load retry interval/attempts.

### Configuration Files
`src/codemie/configs/config.py` (pydantic settings), `litellm_config.yaml` (root, LiteLLM proxy config), `config/` directory, `deploy-templates/`, `docker-compose.yml`.

### Feature Flags and Deployment Concerns
`is_litellm_enabled()` = `HAS_LITELLM and config.LLM_PROXY_ENABLED`. Deployment ordering (Codemie starting before LiteLLM in compose/helm) is the trigger scenario; not investigated in deploy-templates.

---

## 6. Risk Indicators

- Speculative: any recovery must distinguish "fetch failed" from "zero models"; today `get_available_models` collapses both into empty `LiteLLMModels()`, and the underlying enterprise package (external) may also swallow errors, so the failure signal may need to be detected inside that package or by a changed contract.
- Speculative: `models_cache` in the enterprise service may cache an empty/failed result for `LITELLM_MODELS_CACHE_TTL` (30 min); not verified (package outside repo).
- Silent fallback is implemented in `llm_service.get_all_llm_model_info` (empty list -> YAML models); preconfigured assistants created at startup against YAML default model may persist wrong default after later recovery (`_initialize_preconfigured_content` ordering dependency).
- Speculative: a background retry touches shared `llm_service` state from a task/thread; assignment of two lists is not atomic as a pair.
- Multi-replica deployments: each pod retries independently.
- Test `test_startup_integration.py` patches `_initialize_litellm_models` by name; renaming/restructuring affects it.
- Guides not read in detail; UI notification (AC 3) requires a surface; only `/v1/healthcheck/model` and logs exist in this repo (UI code not part of repo).

---

## 7. Summary for Complexity Assessment

The change sits in the startup/lifespan layer (`rest_api/main.py`), the LiteLLM enterprise adapter (`enterprise/litellm/dependencies.py`), the `llm_service` catalog state, and potentially the common healthcheck/admin routers. The core defect is a one-shot load whose exceptions and empty results are swallowed at two levels, after which `llm_service` silently serves YAML models. Likely file surface is small (3-6 source files plus config) with established patterns to follow (background asyncio tasks in `lifespan`, `app.state` readiness status with healthcheck route, admin reload endpoint logic).

Technical novelty is low-moderate: no existing retry/backoff loop for model loading, but analogous refresh loops (`customer_config_refresh`) and readiness reporting exist. The main uncertainty is the external `codemie_enterprise` package, which was not inspected and where the "Error fetching models" log originates and where caching occurs.

Test coverage: startup ordering and `initialize_default_litellm_models` have tests; failure, empty and recovery paths have none, so AC5 requires new tests for both available/unavailable cases. Key risks: error-vs-empty ambiguity, stale cache, preconfigured content created against fallback model, and where to surface the UI notification.

---

## 8. External References

None named by the task
