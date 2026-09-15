# Technical Research

**Task**: feature flag schedulers feature toggle visibility
**Generated**: 2026-09-15T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

I need a feature flag for schedulers view. The Jira ticket is EPMCDME-15013. This is about adding a feature flag (possibly a LaunchDarkly or similar feature toggle) to control visibility/access to the Schedulers view/functionality in the CodeMie application. The schedulers REST API was recently introduced (EPMCDME-10682: Schedulers REST API — list, toggle, run history, stats, run detail).

---

## 2. Codebase Findings

### Existing Implementations

**Schedulers REST API (EPMCDME-10682)**
- `src/codemie/rest_api/routers/schedulers.py` — the schedulers router, registered unconditionally via `app.include_router(schedulers.router)` at line 1005 of `src/codemie/rest_api/main.py`; exposes Phase 1–3 endpoints: `GET /v1/schedulers`, `PATCH /v1/schedulers/{id}`, `GET /v1/scheduler-runs`, `GET /v1/scheduler-runs/stats`, `GET /v1/scheduler-runs/{id}`, `GET /v1/scheduler-runs/{id}/logs`, `DELETE /v1/scheduler-runs/{id}`
- `src/codemie/service/scheduler_run_service.py` — service layer for scheduler run CRUD
- `src/codemie/repository/scheduler_run_repository.py` — repository for `SchedulerRun` DB model
- `src/codemie/rest_api/models/scheduler_run.py` — Pydantic models for API request/response
- `src/codemie/service/settings/scheduler_settings_service.py` — service for scheduler settings queries

**Feature Flag Infrastructure**
- `src/codemie/configs/customer_config.py` — `CustomerConfig` class with methods:
  - `is_feature_enabled(feature_key: str) -> bool` — checks `features:<feature_key>` component; defaults to `False` if absent
  - `is_component_enabled(component_id: str) -> bool` — lower-level component check
  - `_apply_feature_env_override()` — applies `FEATURE_<SCREAMING_SNAKE>` env vars at load time to YAML-backed `features:*` components
- `src/codemie/configs/component_resolution.py` — shared resolution logic: YAML → DB overrides → runtime; `ComponentSetting`, `Component` models
- `config/customer/customer-config.yaml` — YAML source of truth listing all components including `features:*` entries (e.g. `features:webSearch`, `features:cliAnalytics`, `features:subWorkflow`)
- `src/codemie/rest_api/routers/customer_config.py` — exposes `GET /v1/config` returning all resolved components for the frontend to read

**Established Feature Flag Guard Patterns**

Two patterns exist in the codebase:

1. **In-handler guard** (preferred for fine-grained control, used by analytics and cli_analytics routers):
   - Define a `_ensure_enabled()` helper that calls `customer_config.is_feature_enabled("<key>")` and raises `ExtendedHTTPException(code=HTTP_403_FORBIDDEN, ...)` when disabled
   - Call `_ensure_enabled()` at the top of each gated endpoint function
   - Example: `src/codemie/rest_api/routers/cli_analytics.py` (lines 132–141), `src/codemie/rest_api/routers/analytics.py` (lines 2125–2130)

2. **Conditional router inclusion** (used when the entire router should not exist):
   - Wrap `app.include_router(...)` in a conditional: e.g., `if is_litellm_enabled() and config.LLM_PROXY_BUDGET_CHECK_ENABLED: app.include_router(budget_router.router)`
   - Only appropriate when the feature is entirely absent rather than disabled for specific users

### Architecture and Layers Affected

- **API layer** (`src/codemie/rest_api/routers/schedulers.py`) — the feature flag guard will be added here
- **Configuration layer** (`src/codemie/configs/customer_config.py`, `config/customer/customer-config.yaml`) — a new `features:schedulersView` component entry must be added to the YAML
- **No changes needed** to service, repository, or DB layers

### Integration Points

- `customer_config` singleton (imported as `from codemie.configs.customer_config import customer_config`) — already available in all router modules; schedulers router does not currently import it
- `ExtendedHTTPException` from `codemie.core.exceptions` — the standard exception type for feature-flag 403/404 responses
- `GET /v1/config` endpoint — the frontend reads all components here; adding the new component to the YAML will automatically expose it to the UI for menu/nav visibility gating

### Patterns and Conventions

- Feature component IDs follow the pattern `features:<camelCaseKey>` (e.g. `features:webSearch`, `features:schedulersView`)
- Env var override auto-derives from the key: `features:schedulersView` → `FEATURE_SCHEDULERS_VIEW`; no explicit declaration needed in `config.py`
- The `_apply_feature_env_override` transform is applied at `CustomerConfig` load time for all `features:*` components
- `ComponentSetting` supports `extra="allow"`, so optional `name` and `description` fields are fine in the YAML entry
- 403 (Forbidden) is the standard HTTP status for a disabled feature (not 404), based on `analytics.py`; `cli_analytics.py` uses 404 — confirm with the product owner which to use for schedulers

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/configuration-patterns.md` — covers env vars and feature flag config
- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router patterns
- `.ai-run/guides/architecture/layered-architecture.md` — layer taxonomy

### Architectural Decisions

- No ADR recorded for the feature flag system specifically; the convention is derived from existing implementations (`analytics.py`, `cli_analytics.py`).
- The `_apply_feature_env_override` mechanism means feature flags can be overridden per-deployment with a single env var without changing YAML, which is the intended operator interface.

### Derived Conventions

- Feature flags for UI visibility belong in `config/customer/customer-config.yaml` under `components` with an `id` of `features:<camelCaseKey>`.
- Backend guards use `customer_config.is_feature_enabled("<camelCaseKey>")` — the `features:` prefix is added internally.
- The frontend reads `/v1/config` to decide whether to show nav items; the backend raises 403 as a defense-in-depth guard.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_schedulers.py` — covers repository, service, and some router endpoints for the Phase 1–3 scheduler API; does **not** test any feature flag gating (none exists yet)
- `tests/unit/routers/test_analytics_enriched_user.py` — reference example: tests both `test_raises_403_when_feature_disabled` and the positive path; uses `_patch_feature(enabled: bool)` helper that patches `customer_config` with a `MagicMock`
- `tests/enterprise/mcp_auth/test_feature_gating.py` — another feature-gating test example

### Testing Framework and Patterns

- pytest with `unittest.mock.patch` and `MagicMock`
- Feature flag tests patch `codemie.rest_api.routers.<module>.customer_config` with a mock whose `is_feature_enabled` is set to `True` or `False`
- `pytest.raises(ExtendedHTTPException)` asserts the guard fires; checks `exc_info.value.code`

### Coverage Gaps

- No test exists for a disabled `features:schedulersView` flag returning 403 on any scheduler endpoint — all seven endpoints would need a disabled-flag test
- No test for the YAML entry being absent (should also return 403, per `is_feature_enabled` default)

---

## 5. Configuration and Environment

### Environment Variables

- `FEATURE_SCHEDULERS_VIEW` (to be added implicitly) — the `_apply_feature_env_override` mechanism will automatically handle this once the `features:schedulersView` component is declared in the YAML; no code change to `config.py` is required

### Configuration Files

- `config/customer/customer-config.yaml` — must receive a new entry:
  ```yaml
  - id: "features:schedulersView"
    settings:
      enabled: true
      name: "Schedulers View"
      description: "Enable the Schedulers administration view and REST API"
  ```
  Setting `enabled: true` by default is consistent with the recently shipped API (it is currently fully accessible); the flag gates future opt-out deployments.

### Feature Flags and Deployment Concerns

- No Helm/Kubernetes manifest changes required — the `FEATURE_SCHEDULERS_VIEW` env var is picked up automatically by the existing override mechanism
- The `/v1/config` endpoint is unauthenticated (no `dependencies` on the router); the new component will be visible in the config response immediately after deployment

---

## 6. Risk Indicators

- **HTTP status code inconsistency**: `cli_analytics.py` uses 404 for a disabled feature; `analytics.py` uses 403. The schedulers router must pick one consistently. The analytics pattern (403) is more semantically correct but product should confirm.
- **No test coverage for feature-flag guard**: `test_schedulers.py` has no tests for the feature-flag disabled path; all seven endpoints need to be covered.
- **Router registered unconditionally**: `schedulers.router` is included at `main.py:1005` with no condition — unlike `budget_router` which uses a conditional include. The in-handler guard approach is required here (not conditional router inclusion) because the router is already shipped.
- **Frontend must also gate the nav item**: The backend guard is defense-in-depth; the frontend reads `GET /v1/config` and checks `features:schedulersView`. If the frontend is not updated to check this flag, the UI will continue showing the Schedulers nav item even when the flag is disabled on the backend (resulting in confusing 403 errors). Frontend coordination required.
- **Default enabled vs disabled**: Setting the YAML entry to `enabled: true` by default preserves backward compatibility with the currently deployed API. Setting it to `false` would break existing deployments that haven't opted in. Confirm default with product owner.
- **Requirements clarity**: The task context (< 100 words) does not specify: (a) which HTTP status on flag-disabled, (b) whether default is enabled or disabled, (c) whether the scope is all scheduler endpoints or only certain ones, (d) whether an admin override (DB setting) is needed.

---

## 7. Summary for Complexity Assessment

This task touches two architectural layers: the **API layer** (one router file, `src/codemie/rest_api/routers/schedulers.py`) and the **Configuration layer** (`config/customer/customer-config.yaml`). The estimated file change surface is small — approximately 3 files: the router (add `_ensure_enabled()` and call it in each endpoint), the YAML config (add one feature entry), and the test file (add guard tests for each endpoint). An optional import addition to `main.py` is not needed since the router is already unconditionally included.

The task follows a well-established pattern with at least two reference implementations (`cli_analytics.py` and `analytics.py`). There is no technical novelty: the full machinery (env override, resolution, YAML declaration, `is_feature_enabled` helper, `ExtendedHTTPException`) already exists and works. The implementation is mechanical — copy the pattern from `cli_analytics.py` or `analytics.py` and apply it to the schedulers router.

Test coverage posture for the feature-flag guard is a gap: `test_schedulers.py` exists and covers the underlying service and router logic, but has zero tests for the disabled-flag path. Seven endpoints need disabled-flag assertions, making the testing surface slightly larger than the production code surface. One unresolved requirement ambiguity (HTTP 403 vs 404, default enabled vs disabled) could cause a minor rework cycle but does not affect structural complexity.
