# Technical Research

**Task**: budget attribution default project litellm customer budget assistant marketplace cli proxy
**Generated**: 2026-10-30
**Research path**: filesystem (codegraph returned CODEGRAPH_UNAVAILABLE)

---

## 1. Original Context

EPMCDME-16625 Assistant Budget Attribution Matrix — unify budget attribution (asset project for members, else user's default project D, else personal P) across web/in-app (chat, workflows, skills, indexing) and CLI/IDE proxy paths; marketplace assistants always charge D (supersedes 15109, closes EPMCDME-16466); fallback chain target → D → P; funded = active budget + allocation for user; exhausted budget never rerouted; D derivation (admin default → personal project → alphabetical first project; IDP-claims mode derives personal); workflow nodes/sub-assistants attributed per saved assistant for the starting user; datasource indexing follows project-asset rows; scheduled/webhook triggers attributed as owner with real memberships; CLI explicit non-member project ignored+logged; web and CLI must hit same personal budget (email vs username today); analytics must show charged project plus originally targeted project and fallback kind (default vs personal); budget decisions cached ~1 min. Open question: earlier partial fix in GitLab MR 4665 — check git history. (Full matrix, acceptance criteria and decisions D1–D11 are in the user's original request.)

---

## 2. Codebase Findings

### Existing Implementations
**Branch state**: HEAD = origin/main. Prior work NOT in HEAD: `EPMCDME-16466_marketplace-default-project-budget` (0d9376c42, 7d156ce64, 6c4225bd2 — adds `_default_project_fallback` for is_global only) and `EPMCDME-16566_personal-project-case-insensitive` (35e54b65e — `same_project_name` casefold, `_pick_member` in user.py). EPMCDME-15109/15110/15111 are in main. "MR 4665" not found in git log (likely the 16466 branch).

**Web path**
- `src/codemie/service/llm_service/utils.py` — CORE. `_resolve_effective_project` (l.34-58): only `is_global` assets get membership/default logic; others return `asset.project` unconditionally. `_unfunded_project` probes funded state (via `llm_factory._probe_direct_project_budget_scopes`); `set_llm_context` builds `LiteLLMContext(credentials, current_project, personal_project=user.email, budget_fallback_from)`. Chain on main = target → P (no D).
- `src/codemie/rest_api/models/settings.py:194` `LiteLLMContext` — lacks "fallback kind" and distinct originally-targeted fields.
- `src/codemie/core/dependecies.py` — contextvar `litellm_context`; `get_current_project`, `get_budget_fallback_from` (l.154); `_resolve_user_email` (l.220) feeds customer id (email).
- `src/codemie/enterprise/litellm/llm_factory.py` — direct path: `_configure_direct_runtime_overrides` (l.550), `_probe_direct_project_budget_scopes` (l.688, raw SQL), `_record_direct_budget_attribution` (l.531, mutates ctx; no D step), `_apply_platform_budget` (l.504; customer id = user_email).
- set_llm_context call sites: `agents/langgraph_agent.py` (659,697,740,818,1176), `tool_confirmation/tool_call_confirmation_mixin.py:109`, `rest_api/handlers/assistant_handlers.py:455`, `service/assistant_service.py:538` (chat) and `:841` (build_agent_for_workflow — sub/workflow-node assistants, overwrites contextvar), `routers/assistant/marketplace.py:583`, `generation.py:80,119,159`, `_shared.py:206,244`, `routers/skill.py:781,809,849`, `routers/workflow.py:894,937`, `workflow_executions.py:524`, `workflows/workflow.py:996` (workflow project), indexing `datasource/base_datasource_processor.py:287` and `file/file_datasource_update_processor.py:197`.

**CLI/IDE proxy path** — `src/codemie/enterprise/litellm/proxy_router.py` (2061 lines): `_validate_project_header` (l.413), `_extract_request_info` (l.454; header project used with no membership check; fallback `user.default_project or user.username`), `_flag_personal_budget_fallback` (l.618; no D step), `_resolve_tracking_identity` (l.642; `{username}_codemie_cli` / `_codemie_premium_models`), `_resolve_project_budget_runtime` (l.927), `_create_global_budget_body_stream` (l.885, `check_user_budget(user_email=username)`). **Identity mismatch confirmed**: web = email, CLI = username.

**Budget decision + cache** — `service/budget/budget_resolution_service.py`: `_resolution_cache` TTLCache keyed (project, category, user_id), None cached (negative caching), TTL `BUDGET_RESOLUTION_CACHE_TTL=60`s. Funded = active pba + pmba member allocation + active budget; no spend/exhaustion notion (hard/soft enforced in `enterprise/litellm/dependencies.py:check_user_budget` l.440 and LiteLLM). Funded SQL is triplicated: `project_budget_repository.get_project_budget_context` (l.380), `llm_factory._probe_direct_project_budget_scopes`, `budget_resolution_service.resolve_sync`.

**Default project D** — `rest_api/security/user.py:resolve_default_project` (l.31: stored default → personal (== email) → alphabetical min); `UserProject.is_default`; `User.current_project`. IDP-claims mode (`ENABLE_USER_MANAGEMENT=False`): ephemeral User from JWT (`user_providers/legacy_jwt.py`), no stored default; legacy provider uses username for personal project.

**Triggers** — `triggers/bindings/utils.py:resolve_trigger_user` builds `User(id, username, name, email)` with NO project_names/admin_project_names/default_project. `triggers/bindings/webhook.py:526` and `triggers/actors/datasource.py:1153` use bare `User(id=...)` stubs. Assistant triggers use `sign_internal_request` (real User via auth).

**Analytics** — `service/monitoring/base_monitoring_service.py:_add_budget_fallback_attribute`; `metrics_constants.py:120 BUDGET_FALLBACK_FROM`; CLI: `llm_proxy_monitoring_service.py:~470-520` (PROJECT = personal identity when fallback). Readers: `service/usage_attribution/{constants.py:44, money.py:334, parsing.py:104, queries.py}`, `analytics/handlers/budget_usage_service.py`. Readers treat `budget_fallback_from` as "paid personally" — a D-fallback kind must not be misread as personal.

### Architecture and Layers Affected
Service (`llm_service/utils`, `service/budget`), enterprise LiteLLM integration (`llm_factory`, `proxy_router`, `dependencies`), core DI contextvars, security `User` model, triggers, monitoring/analytics, workflows/datasource call sites, REST routers (call-site only).

### Integration Points
Asset call sites → `set_llm_context` → contextvar → `get_llm_by_credentials` → `llm_factory` → `budget_resolution_service` → repository. `proxy_router` → `budget_resolution_service`/`budget_service`/`dependencies`. Monitoring reads contextvars. Optional enterprise package `codemie_enterprise.litellm`.

### Patterns and Conventions
Contextvar attribution re-set per thread; lazy imports to avoid cycles; sync+async twin implementations; `budget_event=... component=...` log style; process-local TTLCache shared sync/async; `EPMCDME-n` tags in comments; commit format `EPMCDME-NNNNN: Summary`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` architecture/layered, service-layer-patterns, integration/llm-providers, external-services, cli-analytics-*, development/logging|error-handling|configuration, testing/*, quality-gates. No guide dedicated to budget attribution / default project.

### Architectural Decisions
Prior specs: `docs/superpowers/tasks/2026-09-23-epmcdme-15110-default-project-foundation/`, `2026-09-26-epmcdme-15109-ac-review/`, `2026-07-20-epmcdme-13264-...`, `2026-09-01-EPMCDME-14451_...`. 16466/16566 planning artifacts on their branches.

### Derived Conventions
Markers EPMCDME-15111 AC6/AC10 (utils.py:77, proxy_router.py:628), EPMCDME-15738 (user.py:37), EPMCDME-13264 (llm_factory.py:569).

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/service/llm_service/test_utils_litellm_context.py`; `tests/enterprise/litellm/{test_llm_factory,test_proxy_router,test_litellm_dependencies,test_premium_models_budget,test_project_member_runtime_sync,...}.py`; `tests/codemie/service/budget/test_budget_resolution_service.py`; `tests/codemie/rest_api/security/test_user.py`; datasource processor llm-context tests; assistant handler/marketplace/langgraph tests; `tests/codemie/triggers/bindings/*`.

### Testing Framework and Patterns
pytest + unittest.mock; `@patch('codemie.service.llm_service.utils.set_litellm_context')`; MagicMock users; `clear_budget_resolution_cache()` between tests.

### Coverage Gaps
No full matrix test; no CLI header-membership test; no chain target→D→P test on main; no trigger user memberships test; no IDP-claims D derivation test; no web/CLI same-personal-id test; no fallback-kind analytics test; no end-to-end workflow-node/sub-assistant attribution test.

---

## 5. Configuration and Environment

### Environment Variables
`BUDGET_RESOLUTION_CACHE_TTL`(60), `BUDGET_RESOLUTION_CACHE_MAX_SIZE`, `ENABLE_USER_MANAGEMENT`, `LLM_PROXY_MODE`, `LLM_PROXY_ENABLED`, `LLM_PROXY_BUDGET_CHECK_ENABLED`, `LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED`.

### Configuration Files
`src/codemie/configs/config.py`, `litellm_config.yaml`, `deploy-templates/`.

### Feature Flags and Deployment Concerns
Shared-asset routing flag (`_is_shared_asset_creds_override`) overlaps new rule. Per-replica caches (≤60 s staleness). Likely no Alembic migration (analytics are ES metrics); OpenAPI baseline only if API shape changes.

---

## 6. Risk Indicators

- Base branch decision: 16466/16566 work not on main; must rebase on or cherry-pick (user decision needed).
- Funded-probe SQL triplicated — all three must change together.
- Web customer id = email vs CLI = username → personal budget split; changing identity may affect existing LiteLLM customer records/spend history.
- `build_agent_for_workflow` overwrites the contextvar; nested attribution must restore/scope correctly.
- Trigger users are stubs; fixing them touches webhook/cron/datasource triggers and `resolve_trigger_user`.
- Analytics readers equate `budget_fallback_from` with personal-paid; new fallback-kind needs reader updates.
- Negative caching (None) up to 60 s delays new allocations (accepted per AC).
- IDP-claims mode: legacy provider uses username for personal project; D derivation inconsistent.
- Interaction with `LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED` and personal credentials bypass.
- Hard-limit "no reroute" semantics live in `check_user_budget`/LiteLLM; funded probe must not check spend.
- Security-relevant (budget enforcement): MR must request `/sanity`.

---

## 7. Summary for Complexity Assessment

Change spans web attribution (`utils.py`, `llm_factory`), CLI proxy (`proxy_router`), a shared resolver (new module under `service/budget` recommended, with sync+async variants), `User` default derivation, trigger user resolution, `LiteLLMContext`, monitoring/analytics and usage_attribution readers, plus ~15 call sites to verify. Expect 15–25 source files and a large test surface.

Technically it is a consolidation of two hand-synced implementations rather than novel tech, but behavior is cross-cutting (money attribution), involves caches, sync/async duplication, and customer-identity unification with data-continuity implications. Test coverage of existing pieces is decent per-unit but there is no matrix-level coverage.

Key risks: base-branch decision (16466/16566), identity unification, nested/trigger contexts, analytics reader semantics. Likely size L; may warrant split (web resolver / CLI+identity / triggers / analytics).
