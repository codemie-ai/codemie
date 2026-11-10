# Technical Research

**Task**: mcp mcp-connect standalone feature-flags customer-config assistant-configuration tools-configuration mcp-servers
**Generated**: 2026-10-28
**Research path**: filesystem

Workspace root: `/Users/Aliaksei_Lyzo/Documents/Projects/Runway/codemie-dev`. Revisions: `codemie` main@cd7c915b9, `codemie-ui` main@6a628ab0f, `codemie-onboarding` @80d996e (branch `EPMCDME-000000_Project_model_management_video`).

---

## 1. Original Context

EPMCDME-15689 — Hide the MCP Servers section from the assistant configuration page.
Make MCP Servers availability on the assistant configuration page conditional on standalone mode, the applicable feature flag, and MCP external-dependency configuration. The assistant configuration page currently displays the MCP Servers section under Tools configuration. For non-standalone deployments, preserve the current behavior. For standalone deployments, the backend must check whether the applicable MCP feature flag is enabled and whether the required external-dependency configuration (e.g. MCP Connect service configuration: MCP_CONNECT_URL and key) is provided. The backend must return unavailable/false if the feature flag is disabled or required config is missing. The UI uses this result to hide MCP Servers functionality and degrade gracefully. Reuse an existing feature flag (e.g. MCP_CONNECT_ENABLED / mcpConnect in GET /v1/config feature flags) if available; do not add a duplicate. Standalone flag (STANDALONE setting, single gate for standalone-only logic, not exposed to UI) is implemented in EPMCDME-16473 (story docs/stories/2026-10-28-standalone-flag-dependency-checks). Latest ticket comment from reporter: hide only if corresponding service is NOT configured for Standalone.
Acceptance: non-standalone unchanged; standalone checks flag + MCP Connect config; flag reused; standalone + flag off or config missing/empty -> backend returns false; UI hides MCP Servers on false; standalone + flag on + config present -> UI shows and MCP works.

---

## 2. Codebase Findings

### Repository impact
- `codemie` — **affected**: owns `mcpConnect` in `GET /v1/config` and `MCP_CONNECT_*` settings.
- `codemie-ui` — **affected (consumer only)**: already hides MCP Servers when `mcpConnect` is absent/disabled (evidence below). No existing code needs a new flag.
- `codemie-onboarding` — **not_affected**: no `MCP_CONNECT` / `mcpConnect` references found.

### Existing Implementations
- `codemie:src/codemie/configs/config.py:588-590` — `MCP_CONNECT_ENABLED: bool = True`, `MCP_CONNECT_URL: str = "http://localhost:3000"`, `MCP_CONNECT_BUCKETS_COUNT: int = 10`. No `STANDALONE` field on main (grep for `standalone` in `src/` finds no setting). No MCP Connect service-level key/token setting exists.
- `codemie:config/customer/customer-config.yaml:42-44` — YAML component `id: "mcpConnect"`, `settings.enabled: true`. Not prefixed `features:`, so the `FEATURE_*` env override (`customer_config.py` `_apply_feature_env_override`) does not apply to it. Not registered in `customer_config_declarations.py`, so not DB-overridable via the declarations API.
- `codemie:src/codemie/configs/customer_config.py:30-49` — `CONFIG_IDS` registry of runtime-computed components; `_get_runtime_config()` (~L155-300) builds them from env/system state. Runtime ids are "never overridable from YAML or the database" and YAML entries with those ids are dropped (`get_enabled_components`, `component_resolution.resolve_all`).
- `codemie:src/codemie/configs/component_resolution.py:108-126` — `resolve_all`: YAML components (minus runtime ids) + DB overrides, then runtime components appended unchanged.
- `codemie:src/codemie/service/customer_config_service.py:157-171` — `resolve_components()` serves `GET /v1/config`; filters to enabled only; already post-filters one component by env (`LEADERBOARD_ENABLED`).
- `codemie:src/codemie/rest_api/routers/customer_config.py:69` — `GET /v1/config` (unauthenticated) returns `resolve_components()`.
- Backend runtime enforcement of `MCP_CONNECT_ENABLED`: `codemie:src/codemie/service/tools/toolkit_service.py:1055` (MCP tools only added when enabled), `codemie:src/codemie/workflows/nodes/tool_node.py:125` (raises if disabled).
- `codemie:src/codemie/service/mcp/toolkit_service.py:1994` — `MCPToolkitService.init_singleton(MCPConnectClient(config.MCP_CONNECT_URL))` at import time.
- `codemie:src/codemie/service/mcp/client.py:~394-400` — HTTP headers: `Authorization: Bearer` from per-server `server_config.auth_token` only; per-assistant `mcp_connect_auth_token` field in `codemie:src/codemie/rest_api/models/assistant.py:230`.
- Precedent: `codemie:src/codemie/clients/elasticsearch.py:28-30` — `ElasticSearchClient.is_configured()` = `retrieval_available(config) and bool(config.ELASTIC_URL)`, surfaced as runtime flags `features:knowledgeBases|datasources|codeIndexing` (EPMCDME-14652).
- Related, distinct flag: `mcpCustomServersDisabled` (`customer-config.yaml:252`, `service/mcp/access_control.py`) — restricts custom servers, not availability.
- UI: `codemie-ui:src/constants/featureFlags.ts:26` `MCP_CONNECT: 'mcpConnect'`; `src/hooks/useFeatureFlags.ts:72` `useMcpEnabled()`; `src/utils/featureFlags.ts:53` `isMcpEnabled()` (missing component => false).
- UI assistant page: `codemie-ui:src/pages/assistants/components/AssistantForm/components/Toolkits/ToolsConfiguration.tsx:102,264` — MCP section rendered only when `showMcpServers && isMcpFeatureEnabled`.
- Other UI consumers of the same flag: `src/pages/workflows/editor/configPanels/components/ToolSelector.tsx:58`, `src/pages/settings/administration/MCPManagementPage.tsx:71`, `src/pages/settings/tabs.tsx:124`, `src/router.tsx:612` (FeatureGuard on `/settings/administration/mcps`), `src/configs/onboarding/assistantsOverview.ts` (tour steps).

### Architecture and Layers Affected
- Backend config layer: `config.py`, `customer_config.py` (YAML + runtime components), `component_resolution.py`.
- Backend service/API: `customer_config_service.resolve_components`, `GET /v1/config`.
- Backend MCP execution: `toolkit_service.py`, `tool_node.py`, `service/mcp/*` (gated by `MCP_CONNECT_ENABLED`).
- UI feature-flag layer (`appInfoStore.configs`) and assistant form `ToolsConfiguration`.

### Integration Points
- `codemie` `GET /v1/config` → `codemie-ui` `appInfoStore.configs` → `useMcpEnabled`/`isMcpEnabled` (evidence both sides: router L69; `useFeatureFlags.ts:65-73`).
- Backend → external MCP Connect service at `MCP_CONNECT_URL` (`MCPConnectClient`).
- Dependency: EPMCDME-16473 `STANDALONE` setting + single gate — story Approved but not on `codemie` main (no commits/branches matching 16473 found).

### Patterns and Conventions
- Env/system-derived availability is exposed as a runtime component in `CONFIG_IDS` + `_get_runtime_config()`; one `Component(id, ComponentSetting(enabled=...))` per flag.
- "Configured" helpers as classmethods (`ElasticSearchClient.is_configured`).
- Env-driven post-filter in `resolve_components` (LEADERBOARD_ENABLED).

---

## 3. Documentation Findings

### Guides and Architecture Docs
No `.ai-run/guides/` consulted for this domain; `codemie/AGENTS.md`/`CLAUDE.md` exist per repo. Prior analyses: `codemie:docs/superpowers/tasks/2026-09-07-EPMCDME-14652-backend-es-capability-hiding/technical-analysis.md` (ES capability hiding precedent), `.../2026-07-08-preconfigured-assistant-mcp-improvements/technical-analysis.md`.

### Architectural Decisions
- `customer_config.py` comments: runtime features "never overridable from YAML or the database"; the UI hides OAuth toggles off the same env flags the routers enforce (single source of truth, cf. MR !4070 #22 comment on marketplace flags).
- 16473 story D1: dependency checks and "MCP_CONNECT_ENABLED / mcpConnect interplay" explicitly deferred to follow-ups (this ticket). D2: standalone Dockerfile sets empty `MCP_CONNECT_URL`.

### Derived Conventions
Today `mcpConnect` (YAML, UI-facing) and `MCP_CONNECT_ENABLED` (env, backend enforcement) are two independent switches with no code linking them.

---

## 4. Testing Landscape

### Existing Coverage
- `codemie:tests/codemie/configs/test_customer_config.py` — runtime components, `PRE_EXISTING_SHIPPED_COMPONENT_IDS` (includes `mcpConnect`, ~L837).
- `codemie:tests/codemie/service/tools/test_toolkit_service_headers.py`, `tests/codemie/workflows/test_tool_node_context.py` — mock `config.MCP_CONNECT_ENABLED` True/False.
- `codemie:tests/codemie/service/mcp/test_client.py`, `test_toolkit_service_integration.py` (patches `MCP_CONNECT_URL`).
- `codemie:tests/codemie/standalone/test_standalone_compose_contract.py` — standalone compose contract.
- `codemie-ui:src/pages/assistants/__tests__/NewAssistantPage.integration.test.tsx:1718-1745` — MCP section hidden when `mcpConnect` absent, shown when enabled.

### Testing Framework and Patterns
Backend pytest with `unittest.mock.patch` on `config.*`; UI vitest 5.0.2 with `mockAPI('GET','v1/config', [...])`.

### Coverage Gaps
- No tests for a `mcpConnect` value derived from env/standalone state (none exists).
- No test linking `mcpConnect` and `MCP_CONNECT_ENABLED`.
- No STANDALONE tests (setting absent on main).

---

## 5. Configuration and Environment

### Environment Variables
`MCP_CONNECT_ENABLED` (default True), `MCP_CONNECT_URL` (default `http://localhost:3000`, non-empty), `MCP_CONNECT_BUCKETS_COUNT`, `STANDALONE` (planned by 16473; not present). No MCP Connect key env var exists in any repo searched.

### Configuration Files
`codemie:config/customer/customer-config.yaml` (`mcpConnect`, `mcpCustomServersDisabled`); `codemie:standalone/Dockerfile`, `codemie:standalone/docker-compose.standalone.yml` (no MCP vars set on main).

### Feature Flags and Deployment Concerns
`mcpConnect` is the existing UI flag; `MCP_CONNECT_ENABLED` the existing backend flag. No `MCP_CONNECT*` references in `codemie/deploy-templates`.

---

## 6. Risk Indicators

- Hard dependency on EPMCDME-16473 (`STANDALONE` + gate) which is not on `codemie` main.
- "MCP Connect key" named by the ticket does not exist as a deployment setting; only per-server `auth_token` / per-assistant `mcp_connect_auth_token`. Requirement needs clarification.
- `MCP_CONNECT_URL` defaults to a non-empty localhost URL, so "missing/empty" only holds if the standalone image blanks it (16473 D2).
- Two independent flags (`mcpConnect` YAML vs `MCP_CONNECT_ENABLED` env); "which flag is reused" is ambiguous.
- Speculative: moving `mcpConnect` into `CONFIG_IDS` would make YAML values ignored for all deployments, breaking "non-standalone unchanged"; a standalone-only post-filter (like `LEADERBOARD_ENABLED`) avoids that.
- Speculative: hiding only the UI flag leaves backend MCP execution gated by `MCP_CONNECT_ENABLED` alone; assistants with saved MCP servers may still call an unconfigured URL.
- The same `mcpConnect` flag also hides workflow ToolSelector MCP, admin MCP page, settings tab and onboarding tour steps — broader UI effect than the assistant page.
- `MCPConnectClient(config.MCP_CONNECT_URL)` is constructed at import; behaviour with empty URL is untested.

---

## 7. Summary for Complexity Assessment

The change is backend-centred in `codemie`: the `mcpConnect` component returned by `GET /v1/config` (YAML-backed, `customer-config.yaml:42`) needs to become unavailable in standalone mode when MCP is disabled or MCP Connect config is missing. Relevant surface is `configs/config.py`, `configs/customer_config.py`, `service/customer_config_service.py`, and possibly the `MCP_CONNECT_ENABLED` enforcement points (`toolkit_service.py:1055`, `tool_node.py:125`). Clear precedents exist: ES capability hiding via `ElasticSearchClient.is_configured()` runtime flags and the env-driven `LEADERBOARD_ENABLED` post-filter in `resolve_components`.

The UI already hides the MCP Servers section on the assistant page when `mcpConnect` is absent (`ToolsConfiguration.tsx:264`), with an integration test covering it, so UI work is likely zero or test-only. Technical novelty is low; the main uncertainties are requirement-level: the standalone gate (EPMCDME-16473) is not yet merged, no "MCP Connect key" setting exists, and two separate flags (`mcpConnect`, `MCP_CONNECT_ENABLED`) both claim "the MCP flag".

Test posture is good around customer config and MCP enable/disable mocking (pytest, vitest), with gaps only where new logic would live. Estimated footprint: 2-4 backend files plus tests in one repo, optional UI test.

---

## 8. External References

- `docs/stories/2026-10-28-standalone-flag-dependency-checks/story.md` (workspace root) — resolved. Facts: EPMCDME-16473, Status Approved, repo `codemie` only; adds `STANDALONE` setting, default off, "not exposed in customer-config and is not passed to the UI"; "standalone-only path is entered through that single gate"; standalone Dockerfile sets `STANDALONE=true` and empty `ELASTIC_URL`, `MERMAID_SERVER_URL`, `MCP_CONNECT_URL`, `REDIS_HOST`; Out of Scope: "Checking whether dependency configuration (... MCP Connect URL and key ...) is present" and "the `MCP_CONNECT_ENABLED` / `mcpConnect` interplay" (deferred to follow-ups such as this ticket). Not yet implemented on `codemie` main@cd7c915b9.
