# Technical Research

**Task**: logout tms token cleanup mcp auth
**Generated**: 2026-09-30
**Research path**: codegraph

---

## 1. Original Context

Jira EPMCDME-15392, Task.

Summary: Remove all user TMS tokens on explicit CodeMie logout

Description:
When a user explicitly logs out from CodeMie, all tokens associated with that user and stored in TMS must be removed.
CodeMie must treat an explicit user logout as a full authentication reset for that user. After logout, any tokens stored in TMS for the user must be removed so they cannot be reused by CodeMie, integrations, MCP authentication, or downstream authentication flows. This ensures that when the same user logs back in, they are forced to pass all required authentications from scratch instead of silently reusing previously stored tokens.

Preconditions: user is signed in to CodeMie; user has one or more tokens stored in TMS; user initiates logout explicitly from CodeMie.

Scenarios of Use: 1) User signs in. 2) During normal usage CodeMie stores one or more user tokens in TMS. 3) User clicks explicit logout. 4) CodeMie completes logout and removes all TMS tokens associated with the user. 5) User signs in again. 6) CodeMie does not reuse any previously stored TMS tokens. 7) User must pass all relevant authentication flows from scratch.

Affected Areas: CodeMie logout flow; authentication and session management; TMS token storage; token cleanup and revocation logic; integrations or MCP flows that rely on TMS-stored user tokens; security and access governance controls.

Acceptance Criteria:
1. Given a signed-in user has tokens stored in TMS, when the user explicitly logs out from CodeMie, then all TMS tokens associated with that user are removed.
2. Given the user logs out explicitly, when the user logs back in, then CodeMie does not reuse any TMS tokens that existed before logout.
3. Given the user logs back in after explicit logout, when any integration or authentication-dependent flow is used, then the user is required to complete the required authentication flow from scratch.
4. Given TMS token deletion fails during logout, then the failure is handled safely and does not allow stale tokens to be silently reused.
5. Sensitive token values are not exposed in logs, UI, Jira, or error messages during logout or cleanup.
6. Existing logout behavior remains functional and redirects the user to the expected signed-out state.
7. Automated or manual validation covers logout with existing TMS tokens and subsequent login/authentication behavior.

Related tickets (context only): EPMCDME-14224 (Bug, In Progress: interactive MCP OAuth2 authentication never completes for affected users; only workaround is deleting their stored credentials); EPMCDME-15382 (Bug, Ready for Testing: MCP OAuth sign-in window closed by the EPAM IdP opener guard — CodeMie waits 60s for a callback that never comes). Epic: EPMCDME-13284.

---

## 2. Codebase Findings

### Existing Implementations

Logout entry points (neither touches TMS today):
- `src/codemie/rest_api/routers/local_auth_router.py:231-253` — `POST /v1/local-auth/logout`, `Depends(authenticate)`; deletes auth cookie, inserts `UserManagementEvent.USER_LOGOUT` activity event via `get_async_session()`, returns `MessageResponse`.
- `src/codemie/rest_api/routers/user.py:135-144` — `GET /v1/user/log_out` (`include_in_schema=False`), sync, **no auth dependency**; deletes `get_idp_provider().get_session_cookie()` cookie, 302 to `config.KEYCLOAK_LOGOUT_URL`. `idp/factory.py:91-93` comment calls it a "cookie-only path".
- `codemie-ui/src/store/auth.ts:81-95` — `logout()`: local auth -> `api.post('v1/local-auth/logout')`, clear userStore, `location.assign('/auth/sign-in')`; `ENV.LOCAL` mode -> no-op; otherwise full-page navigation `document.location.href = {BASE_URL}/v1/user/log_out`.

TMS access and user-wide delete primitives already present:
- `src/codemie/enterprise/mcp_auth/dependencies.py` — `initialize_mcp_auth()` (626-731) injects the TMS handle into `TokenExchangeService.set_tms`, `OIDCTokenExchangeService.set_tms`, `ToolOAuthTokenPort.set_tms`; `get_token_management_system()` (471, standalone build when MCP auth is off); `tms_audit_context(source, correlation_id)` (500); `_build_token_management_system` (505: Postgres TMS, or `MockTokenManagementSystem` when TMS disabled + allow-mock; production requires TMS enabled).
- `src/codemie/service/security/tms_vault_ops.py:64` — `delete_all_for_user(tms, audit_cm, user_id)`; also `delete_token`, `invalidate_by_config`.
- `src/codemie/service/security/tms_token_store.py:168-193` — `TMSTokenStore.invalidate_all_for_user(user_id)`: audit error -> `TokenProviderException`; `TMSUnavailable/TMSPersistenceError/TMSCryptoError` swallowed (`pass`); then purges the process-local `_fallback` TTLCache. Only caller is its unit test.
- `src/codemie/service/security/token_exchange_service.py:219-230` — `clear_cache(user_id)`: per principal alias (`__idp_token__`, `__client_token__`, lines 45-48) calls `TMSTokenStore.invalidate` and pops the in-memory cache. Docstring (87-88) says "Clear cache on logout", but no logout path calls it; codegraph shows no caller passing `user_id` and no covering tests.
- `src/codemie/service/oauth/token_port.py` — `ToolOAuthTokenPort` (`delete` per integration, `invalidate_by_integration`, `save_oauth2_token`, `get_oauth2_token_or_none`); deliberately NOT via `TMSTokenStore` fallback so TMS failures propagate (docstring 15-25); `map_tms_error_to_http` (204-223) returns sanitized 503/502/500.
- `enqueue_mcp_auth_cleanup(user_id)` (`dependencies.py:593-606`) -> asyncio bridge queue -> `codemie_enterprise/mcp_auth/service.py` `MCPAuthService.enqueue_cleanup` (87) -> daemon worker `_run_cleanup_worker` (122-147) calling `tms.delete_all_for_user` under audit source `USER_OFFBOARDING`, exponential-backoff retry. Best effort: skipped if bridge/service not initialised, dropped if queue full. Sole caller: `UserManagementService._schedule_mcp_auth_cleanup_after_commit` (`service/user/user_management_service.py:1205`, user deactivation).
- Enterprise TMS (`codemie-enterprise/src/codemie_enterprise/mcp_auth/`): `tms_interface.py:131` `delete_all_for_user`; `tms_postgres.py:717-757` deletes in one connection, writes a `DELETE_ALL_FOR_USER` audit row in the same transaction, raises `TMSAuditError`/`TMSUnavailable`/mapped persistence errors; `tms_mock.py:84` mock equivalent.

What writes user tokens to TMS (all keyed by user_id, so a user-wide delete reaches each):
- Token exchange: `TokenExchangeService._get_token_for_principal` stores the user's bearer under alias `__idp_token__` with `expires_at=parse_jwt_exp(token)`.
- MCP OAuth2/SAML: mcp_auth callbacks (`enterprise/mcp_auth/_oauth2_callback.py::_store_callback_token`), keyed by `auth_config_id`.
- Tool OAuth (GitLab/Jira/Confluence): `ToolOAuthTokenPort.save_oauth2_token`, keyed by integration (setting) id.

### Architecture and Layers Affected
- REST router: `user.py`, `local_auth_router.py` (auth via `authenticate`, `security/authentication.py:100`, sets request-scoped user context).
- Service/security: `service/security/*`, `service/oauth/token_port.py`, `service/user/*`.
- Enterprise bridge: `codemie/enterprise/mcp_auth/dependencies.py` (optional package `codemie_enterprise`, imports deferred).
- External package: `codemie-enterprise` mcp_auth TMS.
- Frontend: `codemie-ui` `src/store/auth.ts`, Keycloak theme `LogoutConfirm.tsx`.
- IdP: Keycloak redirect (`KEYCLOAK_LOGOUT_URL`); `IdpFactory` registry (LocalIdp in base, enterprise registers Keycloak/OIDC), optionally wrapped by `JwksValidatingIdp`, which requires an `Authorization: Bearer` header.

### Integration Points
- `codemie_enterprise.mcp_auth` (TMS; exceptions `TMSUnavailable`, `TMSPersistenceError`, `TMSAuditError`, `TMSCryptoError`, `TokenNotFound`, `ReAuthenticationRequired`).
- Activity log: `activity_event_repository.async_insert` with `UserManagementEvent.USER_LOGOUT`.
- `codemie-mcp-connect-service`: no TMS or per-user credential store found; its client cache key (`mcp_connect/client/cache.py:210`) hashes `mcp_headers`/`env` (not examined further).

### Patterns and Conventions
- Enterprise imports deferred inside method bodies; no enterprise types cross service boundaries.
- Audit context manager passed in by callers (`tms_vault_ops` docstring); TMS ops run inside it.
- Two deliberately different error contracts: `TMSTokenStore` (fail-soft with TTLCache fallback) vs `ToolOAuthTokenPort` (propagate, map to sanitized HTTP errors).
- Tokens never logged (`_add_user_token_if_needed` "SECURITY" comment; `TMSTokenStore.put` masks `client_secret`; Postgres TMS logs `[REDACTED]` ids).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists. Opened: `integration/mcp-integration.md` (timeouts and router placement only, nothing on TMS/logout), `development/security-patterns.md` (use `authenticate`; never log tokens or auth headers). Present but not opened: `development/error-handling.md`, `development/logging-patterns.md`, `development/configuration-patterns.md`, `architecture/service-layer-patterns.md`, `testing/testing-patterns.md`, `testing/testing-api-patterns.md`, `testing/testing-service-patterns.md`. No guide covers TMS or logout.

### Architectural Decisions
- `user_management_service.py:1206-1209`: "approved deviation" comment; no shared user-lifecycle hook registry exists, so MCP auth cleanup is scheduled directly from deactivation paths.
- `token_port.py` docstring: TMSTokenStore's fallback cache is unsuitable for tool OAuth (could serve a stale/revoked token during a TMS outage).

### Derived Conventions
Cleanup is user-scoped and audited with a named audit source (`token_exchange`, `tool_oauth_*`, `USER_OFFBOARDING`). Logout of a local-auth user is audited as an activity event.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_local_auth_router.py:584-603` `TestLogoutEndpoint::test_logout_clears_cookie` — calls `logout(mock_response, _user=mock_user)` directly, patches router `config`, asserts message and `delete_cookie`.
- `GET /v1/user/log_out` — codegraph reports no covering tests.
- `tests/codemie/service/security/test_tms_token_store.py` — get/put/invalidate incl. audit error -> `TokenProviderException`; `test_invalidate_all_for_user` (340-350) covers success path and fallback purge only.
- `tests/codemie/service/security/test_token_exchange_factory.py` — TokenExchangeService; `tests/codemie/service/oauth/test_token_port_connection.py`, `test_oauth_token_vault_guard.py` — ToolOAuthTokenPort.
- `tests/enterprise/mcp_auth/test_feature_gating.py` (bridge-unavailable `enqueue_mcp_auth_cleanup`), `test_tms_bridge.py`.
- codemie-enterprise: `tests/mcp_auth/test_tms.py`, `test_tms_postgres.py`, `test_service.py`.
- codemie-ui: `authStore` has no covering tests per codegraph.

### Testing Framework and Patterns
pytest with `pytest.mark.asyncio`, `MagicMock`/`patch`, fixtures `mock_tms`, `audit_ctx`, `store`, `mock_response`, `mock_user`. Version not determined.

### Coverage Gaps
`tms_vault_ops.delete_all_for_user` (no covering tests), `GET /v1/user/log_out`, `TokenExchangeService.clear_cache`, failure paths of user-wide delete, logout-then-login behaviour.

---

## 5. Configuration and Environment

### Environment Variables
All in `src/codemie/configs/config.py` (`Config`, pydantic settings, `.env` loaded): `MCP_AUTH_ENABLED`, `MCP_AUTH_TMS_ENABLED`, `MCP_AUTH_TMS_ALLOW_MOCK`, `MCP_AUTH_TMS_AUDIT_REQUIRED`, `MCP_AUTH_TMS_AUDIT_FALLBACK_ENABLED`, `MCP_AUTH_TMS_AUDIT_SANITIZE_DIAGNOSTICS`, `MCP_AUTH_TMS_KMS_KEY_ID`, `MCP_AUTH_TMS_REDIS_LOCK_ENABLED`, `MCP_AUTH_HMAC_SECRET`, `MCP_AUTH_REDIS_KEY_NAMESPACE`, `TOKEN_CACHE_TTL` (default 300), `TOKEN_CACHE_MAX_SIZE` (1024), `BROKER_TOKEN_URLS`, `TOKEN_EXCHANGE_URL`, `KEYCLOAK_LOGOUT_URL`, `AUTH_COOKIE_NAME/PATH/*`, `IDP_PROVIDER`, `ENABLE_USER_MANAGEMENT`, `JWKS_VALIDATION_ENABLED`, `GITLAB_OAUTH_ENABLED`, `JIRA_OAUTH_ENABLED`, `CONFLUENCE_OAUTH_ENABLED`.

### Configuration Files
`src/codemie/configs/config.py` only for this domain. Untracked `config/customer/managed-mcp-servers.yaml` (managed MCP catalog) is unrelated.

### Feature Flags and Deployment Concerns
TMS handle may be a Mock, absent (`TokenExchangeService._store is None` -> in-memory TTLCache only), or standalone (tool OAuth with MCP auth off). Tool OAuth requires TMS when any tool OAuth flag is on (`token_port._require_enterprise_tms_ready`). Production forbids mock TMS.

---

## 6. Risk Indicators

- AC4: `TMSTokenStore.invalidate`/`invalidate_all_for_user` swallow TMS unavailable/persistence/crypto errors; `enqueue_mcp_auth_cleanup` is async, dropped when bridge uninitialised or queue full. Neither fails loudly to the caller.
- `GET /v1/user/log_out` (the UI's non-local route, browser navigation) has no `authenticate` dependency, no resolved user id, no tests.
- Stale-read paths: `TMSTokenStore.get` falls back to `_fallback` on `TokenNotFound`/outage; `TokenExchangeService._cache` and `_fallback` are per-process TTL caches (300 s), unreachable from another pod.
- `clear_cache(user_id)` covers only the two exchange aliases (`__idp_token__` holds the user's own IdP bearer), not MCP or tool-OAuth rows.
- Unexamined per-user state outside TMS: `MCPToolkitFactory` cache (global `clear_cache`), mcp-connect client cache keyed on hashed headers/env.
- AC5: TMS exception text flows through `TokenProviderException(details=...)`; `USER_LOGOUT` audit exists only on the local route.
- Speculative: likely a call to an existing user-wide delete from both handlers; open design points are fail-closed semantics, sync vs queued, and user-id resolution on the Keycloak route. No enterprise change evidenced.
- Speculative: AC2/3/7 validation likely needs a manual/integration check with `MockTokenManagementSystem`; no logout-then-login harness exists.

---

## 7. Summary for Complexity Assessment

Layers: REST routers (`local_auth_router.py` POST, `user.py` GET redirect), service/security (`tms_token_store.py`, `tms_vault_ops.py`, `token_exchange_service.py`, `token_port.py`), enterprise bridge (`enterprise/mcp_auth/dependencies.py`), and codemie-ui `authStore`. Neither logout handler touches TMS today. User-wide delete primitives exist down to `codemie-enterprise` (`delete_all_for_user`) but no logout path calls them; the sole production consumer is the async, retrying user-deactivation cleanup.

Mechanism is familiar; failure semantics are the hard part. Existing helpers swallow TMS errors or run best-effort in a background thread, the opposite of AC4, and the Keycloak route is an unauthenticated GET redirect without tests. Coverage: one direct-call test for local logout; none for GET log_out; `invalidate_all_for_user` tested on success only. No guide documents TMS or logout.

---

## 8. External References

None named by the task.
