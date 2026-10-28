# Technical Research

**Task**: mcp oauth client-metadata-document dcr (EPMCDME-16344)
**Generated**: 2026-10-27
**Research path**: codegraph

---

## 1. Original Context

Title: Setting to turn off the client metadata document and force Dynamic Client Registration (Story)
Context: CodeMie registers itself with an MCP server's authorization server via either a published client metadata document (CIMD) or Dynamic Client Registration (DCR). It always picks CIMD when the server supports it; no setting changes that. With CIMD the auth server downloads CodeMie's document from the CodeMie host; preview/stage hosts are reachable only from EPAM network, so the EPAM MCP Gateway cannot fetch it and login fails. DCR needs no download. CodeMie already has DCR, a cache of DCR registrations, and a failure path for servers with no usable registration method. CodeMie already holds the server info the choice depends on, so change is codemie only (no codemie-enterprise change). EPMCDME-16327 (declaring refresh-token grant in DCR) is independent/out of scope.
Story: As a CodeMie platform operator, I want to turn off the client metadata document for an environment that authorization servers cannot reach, so users can still sign in to OAuth-protected MCP servers through DCR.
Acceptance Criteria:
1. Setting off (default): server supports CIMD -> CodeMie uses CIMD as today.
2. Setting on: server supports both methods -> CodeMie registers through DCR and login completes.
3. Setting on: server supports only DCR -> DCR as today.
4. Setting on and server supports only CIMD -> connection stops; user sees message saying the server supports only the client metadata document, which is turned off in this environment. Message must NOT say the server lacks support for it.
5. Setting on and CodeMie already registered via DCR with the auth server -> reuse that registration, do not register again.
6. Users already holding tokens: toggling the setting keeps existing MCP connections working without new login.
7. Preview/stage run with setting on (env config outside repo, owner sets value) -> login to EPAM MCP Gateway completes.
8. Prod: setting stays off by default; CIMD stays.
Out of scope: per-server choice; fallback to CIMD when no DCR (D1: stop with clear message); refresh-token grant (16327); ingress path for CIMD (16320); stopping CodeMie from serving the CIMD while setting is on; gateway changes.
Decisions: setting is environment-wide, off by default; codemie repo only.

---

## 2. Codebase Findings

### Existing Implementations
All in this repo, `src/codemie/enterprise/mcp_auth/` (bridge to the separate `codemie_enterprise` package):
- `_discovery.py` — `build_mcp_auth_discovered_auth_gate_payloads` -> `_prepare_discovered_flow_resolution_config` (builds `_DiscoveredFlowResolutionConfig` incl. `client_metadata_document_url` via `build_client_metadata_document_url()`) -> `_resolve_discovered_candidate_payload`, which calls `codemie_enterprise.mcp_auth.resolve_discovered_oauth2_flow(... client_metadata_document_url=..., redirect_uris=..., dcr_credentials_cache=deps._mcp_auth_dcr_credentials_cache, network_options=ClientRegistrationNetworkOptions(...), dcr_timeout_seconds=...)`. A `resolution.status == "config_error"` is turned into a payload by `_build_discovered_config_error_payload` (`_common.py`) with `error_context` (`attempted_mechanisms`, `failure_reasons`, `action`). Local failures use `_build_discovered_failure_payload`. Registration method/reason are already logged (`registration_method`, `registration_reason_code`).
- `_uri.py` — `build_client_metadata_document_url`, `build_client_metadata_document_response`, `ensure_client_metadata_document_available`, `build_redirect_uri`.
- `router.py` — `enabled_cimd_router` serves `_CLIENT_METADATA_DOCUMENT_PATH` (`client_metadata_document_enabled`); `cimd_router` is the disabled 503 variant. Serving stays unchanged per scope.
- `_initiate.py`, `_oauth2_callback.py`, `_post_auth.py`, `dependencies.py` (facade; `_mcp_auth_dcr_credentials_cache`, `_mcp_auth_service.config`), `_guards.py` (`is_mcp_auth_enabled`), `_trust_policy.py`. Not read in depth.

**Where the CIMD-vs-DCR choice is actually made (outside this repo):** `codemie-enterprise/src/codemie_enterprise/mcp_auth/discovery/client_registration.py::resolve_client_registration` — order: pre-registered, then `if metadata.client_id_metadata_document_supported:` return CIMD (`REASON_CIMD_SUPPORTED`, `client_id` = the document URL), then `if not metadata.registration_endpoint:` return `_no_supported_mechanism_failure` (`REASON_NO_SUPPORTED_REGISTRATION_MECHANISM`), else `_resolve_dcr_registration` (cache `REASON_DCR_CACHE_HIT/MISS`). No parameter in its signature disables CIMD. `client_metadata_document_url: str` is required and validated (`_validate_resolve_inputs`). Message text for the no-mechanism failure lives in the enterprise package.

### Architecture and Layers Affected
Config layer (`src/codemie/configs/config.py`) -> enterprise bridge/mcp_auth discovery layer (`_discovery.py`) -> external `codemie_enterprise` registration resolver -> error payload surfaced to user (`_common.py` payload, auth-required handler/UI messaging, e.g. `tests/codemie/rest_api/test_mcp_auth_required_handler.py`).

### Integration Points
- `codemie_enterprise.mcp_auth` (`resolve_discovered_oauth2_flow`, `ClientRegistrationNetworkOptions`, `DCRCredentialsData` cache) — separate repo/package, constraint "codemie only" means its resolver is not modifiable in this ticket.
- Redis DCR credentials cache `deps._mcp_auth_dcr_credentials_cache`; discovered flow store (`_require_initialized_discovered_flow_store`).
- `discovery_result` (AS metadata incl. `client_id_metadata_document_supported`, `registration_endpoint`) is held in codemie as an argument to `_resolve_discovered_candidate_payload`.

### Patterns and Conventions
Settings via `codemie.configs.config` (`config.MCP_AUTH_*`, e.g. `MCP_AUTH_ENFORCE_HTTPS`, `MCP_AUTH_DISCOVERY_CONCURRENCY_LIMIT`, `CALLBACK_API_BASE_URL`); deps accessed via `_deps()` sys.modules indirection; failures returned as `config_error` payloads with `failure_reasons` codes; logs sanitize via `_sanitize_log_field`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/integration/mcp-integration.md`, `development/configuration-patterns.md`, `testing/testing-patterns.md` exist and are relevant; not read in detail (conventions derived from code).

### Architectural Decisions
Ticket decisions: environment-wide, default off, codemie repo only, no CIMD fallback.

### Derived Conventions
Env-var-backed config attribute in `config.py`; deploy values live in `deploy-templates/` (helm) and env config outside the repo.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/enterprise/mcp_auth/` (e.g. `test_mcp_auth_status_bridge.py`), `tests/enterprise/test_mcp_auth_feature_gating.py`
- `tests/codemie/service/mcp/test_client.py`, `tests/codemie/service/security/test_logout_token_cleanup.py` (touch CIMD URL/response helpers)
- `tests/codemie/configs/test_managed_mcp_config.py`, `tests/codemie/rest_api/test_mcp_auth_required_handler.py`, `tests/codemie/agents/test_mcp_auth_required_propagation.py`, `tests/codemie/workflows/test_mcp_auth_required_storage.py`
- Registration-logic tests live in the enterprise repo (`tests/mcp_auth/test_discovery/test_client_registration.py`), not here.

### Testing Framework and Patterns
pytest (`pytest.ini`), mock-based unit tests.

### Coverage Gaps
No test found in this repo specifically for `_discovery.py` registration-method selection (not exhaustively verified).

---

## 5. Configuration and Environment

### Environment Variables
Existing: `MCP_AUTH_ENABLED`, `MCP_AUTH_ENFORCE_HTTPS`, `MCP_AUTH_DISCOVERY_CONCURRENCY_LIMIT`, `CALLBACK_API_BASE_URL`. No existing CIMD on/off setting found.

### Configuration Files
`src/codemie/configs/config.py`; `deploy-templates/` (helm values); `config/`.

### Feature Flags and Deployment Concerns
Preview/stage value set outside repo (AC7). Nothing found that gates serving of the CIMD endpoint by a setting.

---

## 6. Risk Indicators

- Speculative: the selection logic is in `codemie_enterprise.resolve_client_registration`, which has no disable parameter; the "codemie only" decision implies codemie must influence the choice by what it passes in (e.g. altering the AS metadata `client_id_metadata_document_supported` seen by the resolver) — design to be validated against the enterprise package's version installed here and the coercion of `discovery_result`.
- AC4 (CIMD-only + setting on) needs a distinct message; the resolver's native failure (`no_supported_registration_mechanism`) is likely worded as lacking support, which AC4 forbids. Codemie-side error-context/message mapping is where wording lives (`_common.py`, auth-required handler) — unverified.
- AC5 relies on the DCR cache (`dcr_credentials_cache`) being keyed independently of method; reuse behavior is in enterprise code.
- AC6: existing tokens/auth configs created via CIMD carry `client_id` = document URL; refresh/callback paths (`_oauth2_callback.py`, `_post_auth.py`, enterprise `tms_refresh`) were not examined for whether they re-run registration.
- `discovery_result` may be a dataclass/pydantic object or dict (`_get_discovery_result_field`); mutation approach needs care.
- Cross-repo version coupling with `codemie-enterprise`.

---

## 7. Summary for Complexity Assessment

The change touches the config layer (one new boolean setting, default off) and the MCP-auth discovery bridge (`_discovery.py`), plus error-message wiring for the CIMD-only case, and tests/deploy values. Likely file surface is small (config, `_discovery.py`/`_common.py`, tests, helm/docs), but the registration decision itself resides in the external `codemie_enterprise` package, which is out of bounds for this ticket.

Technical novelty is moderate: the existing DCR path, DCR cache and no-mechanism failure path already exist, so reuse is the pattern; the uncertainty is how codemie steers an enterprise-side resolver without changing it, and how to produce the AC4 wording and preserve existing tokens (AC6).

Test posture: related MCP auth tests exist in this repo, but registration-selection tests are in the enterprise repo; new unit tests for the setting on/off matrix (AC1–5, 8) would be new in this repo. Key risks listed in Section 6.

---

## 8. External References

None named by the task (the enterprise package was inspected read-only for context: `codemie-enterprise/src/codemie_enterprise/mcp_auth/discovery/client_registration.py`, facts in Section 2).
