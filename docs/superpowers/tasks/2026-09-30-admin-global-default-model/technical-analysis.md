# Technical Research

**Task**: admin llm global default model
**Generated**: 2026-09-30
**Research path**: filesystem

---

## 1. Original Context

Implement an admin-selectable GLOBAL default LLM model in backend repo /Users/Aliaksei_Hurynovich/Developer/_codemie/codemie and corresponding Model management UI in sibling ../codemie-ui. Persist admin choice; without choice use configured global default. Admin can view/set/clear; validate selected model exists and selectable; apply effective global default consistently to internal fallback and model API `default` and `default_for_categories`; UI provides set action and shows selected model. Do not add or manage CODE/CLI defaults; existing code category remains configured and its category-specific default takes precedence. Assess status changes, provider outages, routers, per-user model lists, and cross-repo constraints.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/configs/llm_config.py`: `ModelCategory.GLOBAL` and `CODE`, `ModelStatus`, `LLMModel`, and `LlmRouterOption`. `LLMModel.populate_default_field` sets `default=True` when `global` is declared; it does not clear an existing `default` on later mutation.
- `src/codemie/service/llm_service/llm_service.py`: `LLMService` sources either live LiteLLM models or YAML models, merges persisted status, resolves global/category defaults, and exposes `default_llm_model` to many internal callers. `_prefer_litellm_router` chooses a default auto-router before concrete models. `assert_model_selectable` rejects deprecated, disabled, or provider-offline known models, but intentionally does not reject unknown names.
- `src/codemie/rest_api/routers/llm_models.py`: `/v1/llm_models` returns a user's allowed models plus global router options; `/v1/default_models` delegates to category resolution; `/v1/default_models/{category_id}` scans the user's allowed *chat models* and falls back to its first item. The latter excludes auto-routers and does not delegate to `get_default_model_for_category`.
- `src/codemie/rest_api/routers/admin.py`: admin-only model status GET/PUT/bulk routes, and LiteLLM reload. No admin default write route exists.
- `src/codemie/rest_api/models/llm_model_status.py`, `src/codemie/repository/llm_model_status_repository.py`, `src/external/alembic/versions/854f0f549445_create_llm_model_status_table.py`: persisted status override, editor metadata, repository upsert, and migration precedent.
- `src/codemie/enterprise/litellm/models.py`: maps proxy `default_for_categories` into `LLMModel`; `litellm_config.yaml` contains multiple global declarations. `config/llms/` holds YAML source variants.
- `../codemie-ui/src/pages/settings/administration/ModelsManagementPage.tsx`, `components/ModelActions.tsx`, `src/store/adminModels.ts`, `src/types/entity/adminModel.ts`: admin table renders category labels; action menu currently changes only status; store fetches `/v1/llm_models?include_all=true` and issues status PUT.
- `../codemie-ui/src/store/appInfo.ts` and `src/pages/help/ModelsCatalog/ModelsCatalogPage.tsx`: general catalog consumes `default` and `default_for_categories` directly from `/v1/llm_models` and renders “Default for”.

### Architecture and Layers Affected

- Backend API: FastAPI routers and central authentication/admin dependencies; request/response models in `src/codemie/core/models.py` or narrow model module.
- Backend domain/service: `LLMService` owns catalog, default resolution, model filtering, and router projections.
- Persistence: SQLModel, `get_session`, repository, Alembic migrations under `src/external/alembic/versions/`.
- UI: React page/components, Valtio store, TypeScript entity type, shared API wrapper.

### Integration Points

- `config.LLM_PROXY_ENABLED` selects live LiteLLM catalog after initialization; otherwise YAML catalog is used. Proxy reload refreshes catalog.
- External users with a project/user LiteLLM integration receive a separate permitted model list in `get_allowed_models`; the status override is merged into that list. Router options are built from the global catalog, not a user's list.
- `default_llm_model` is used by assistant, workflow, generator, tool, and request-model fallback paths. Startup initializes the proxy before importing preconfigured content (`src/codemie/rest_api/main.py`).

### Patterns and Conventions

- Routers use `Depends(admin_access_only)` and `Depends(authenticate)`; services own selection; repositories own SQLModel operations.
- Status override reads use a last-successful-value cache on DB failure; YAML model objects are reused and mutated by status merge.
- UI convention is component → Valtio store → `src/utils/api.ts`; the store parses native `Response.json()`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- Backend: `.ai-run/guides/architecture/layered-architecture.md`, `api/rest-api-patterns.md`, `data/database-patterns.md`, `data/repository-patterns.md`, `integration/llm-providers.md`, `development/security-patterns.md`, `quality-gates.md`, `development/setup-guide.md`.
- UI: `../codemie-ui/.ai-run/guides/architecture/architecture.md`, `patterns/state-management.md`, `development/api-integration.md`; `../codemie-ui/AGENTS.md` routes to these guides.

### Architectural Decisions

- Service comments establish proxy model catalog as authoritative when initialized, router-first global default precedence, and a status cache to avoid silently restoring admin-disabled models during database outage.
- UI guide explicitly requires store-owned API requests; backend database guide requires Alembic for persistent schema changes.

### Derived Conventions

- Backend status route returns `BaseResponseWithData`, persists editor identity, and validates base name against the current catalog.
- UI menu uses `NavigationMore`; destructive status changes show `ConfirmationModal`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/test_llm_service.py`: default category/router precedence, model visibility, provider-disabled and persisted status behavior.
- `tests/codemie/rest_api/routers/test_llm_models.py`: list and per-user default endpoints, first-model fallback, `include_all`.
- `tests/codemie/rest_api/routers/test_admin_router.py` and `tests/codemie/repository/test_llm_model_status_repository.py`: admin status authorization, validation, persistence.
- `../codemie-ui/src/store/__tests__/adminModels.test.ts`, `src/pages/settings/administration/__tests__/ModelsManagementPage.test.tsx`, `ModelActions.test.tsx`, and help catalog tests cover current UI surfaces.

### Testing Framework and Patterns

- Backend uses pytest with mocked services/sessions in route tests and in-memory SQLite for status repository tests; UI uses Vitest and React Testing Library with mocked API wrapper.

### Coverage Gaps

- No tests found for an admin global-default override or its interaction with status changes, proxy reload, routers, per-user model lists, or UI set/clear actions.

---

## 5. Configuration and Environment

### Environment Variables

- `LLM_PROXY_ENABLED` toggles proxy catalog. LiteLLM model cache and request timeouts are defined in `src/codemie/configs/config.py`; `MODELS_ENV` selects provider YAML per the LLM provider guide.

### Configuration Files

- `litellm_config.yaml` carries proxy default-category metadata; `config/llms/` contains YAML models; `src/codemie/configs/llm_config.py` defines domain fields. No admin-default configuration field found.

### Feature Flags and Deployment Concerns

- Migrations run at startup via `alembic_upgrade_postgres()` in `src/codemie/rest_api/main.py`. Backend and UI are separate repositories. The UI checkout is clean on `EPMCDME-15077_model-management_FE`, tracking `origin/EPMCDME-15077_model-management_FE`. UI `AGENTS.md` requires `npm ci` before trusting UI gates; no UI changes can be written from this session's writable roots without filesystem escalation.

---

## 6. Risk Indicators

- Speculative: An override must be reconciled against a changing live catalog and status. Current `_get_global_default_model` checks `enabled` but ignores persisted `status`; `default_llm_model` may use a disabled default as last resort. Policy for invalidated admin selection needs definition.
- Speculative: Project/user LiteLLM catalogs may omit the globally chosen model; `/v1/default_models/{category_id}` currently chooses among allowed plain models and cannot return an auto-router. Catalog metadata must not advertise an inaccessible choice as selectable for that user.
- Speculative: Multiple global declarations and router-first precedence mean changing only the selected model's flag can leave duplicate `global` labels. `LLMModel.populate_default_field` only sets `True`, so an old default's `default` field may remain stale unless explicitly normalized.
- Speculative: `get_all_llm_model_info` can return shared mutable YAML/proxy model objects. In-place projections could contaminate configured defaults, cached lists, or user views; test repeated set/clear and reload.
- Speculative: Admin status changes and default selection are separate writes. A model can become disabled/deprecated or provider-offline after selection; avoid a dead or misleading effective default.
- Speculative: `/v1/llm_models` adds global router options even when a user's allowed model list differs; `get_allowed_chat_models` excludes auto-routers. The admin UI currently sees router options with no `status` field; selectability behavior needs deliberate scope.
- Speculative: UI checkout is outside writable roots, so editing it requires escalation or a different authorized write mechanism. The flow's planned cross-repo implementation and git handoff must account for this.

---

## 7. Summary for Complexity Assessment

This feature spans backend FastAPI routes, `LLMService` default resolution and catalog projection, SQLModel/repository persistence, an Alembic migration, and the sibling React/Valtio admin page. Existing status-management code provides a close persistence and authorization pattern; global/category defaults and router behavior already exist but are driven by configuration metadata.

The main novelty is keeping one effective global choice consistent across internal fallback, `/v1/default_models`, per-user `/v1/llm_models` metadata, and auto-router projections while leaving `code` declarations untouched. Existing tests cover adjacent behavior, but not runtime default overrides. Live proxy reloads, provider outages, status transitions, and external user catalogs create the key edge cases. The UI repository is outside this session's writable roots.

---

## 8. External References

- Resolved `../codemie-ui` to `/Users/Aliaksei_Hurynovich/Developer/_codemie/codemie-ui`. Its `AGENTS.md` identifies this as the React/TypeScript CodeMie frontend and directs page state through Valtio stores and the shared API client. Relevant files are `src/pages/settings/administration/ModelsManagementPage.tsx`, `components/ModelActions.tsx`, `src/store/adminModels.ts`, `src/store/appInfo.ts`, and `src/pages/help/ModelsCatalog/ModelsCatalogPage.tsx`: they display model default-category metadata, but the admin action menu only changes status.
