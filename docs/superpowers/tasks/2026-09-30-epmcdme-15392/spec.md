# EPMCDME-15392: Remove user TMS tokens on explicit logout

Scope: size L (complexity-assessment.json). Requirements: ticket ACs 1-7 (technical-analysis.md, section 1).

## Goal

An explicit CodeMie logout removes every token the Token Management System (TMS) holds for the user, so the next login must re-authenticate every integration, MCP server and token-exchange flow from scratch. Logout itself must never break because of it.

## Settled by the user

- Logout always completes on both routes. A TMS failure never turns into an error response.
- There is no scheduled, queued, retried or background cleanup anywhere, including the failure path. `enqueue_mcp_auth_cleanup` (`enterprise/mcp_auth/dependencies.py:593`) and the USER_OFFBOARDING worker are not used.
- On `GET /v1/user/log_out`, resolve the user best-effort through the configured IdP. If the session is already invalid, skip the purge with a warning and still log out.

## Behavior

One new sync service unit under `src/codemie/service/security/` (name left to the plan). It takes a `user_id` and never raises.

1. It resolves the TMS handle with `get_token_management_system()` (`dependencies.py:471`). If no TMS is in use in this deployment, it returns silently (a no-op, not a failure).
2. It calls `tms_vault_ops.delete_all_for_user` (`tms_vault_ops.py:64`) inside `tms_audit_context("user_logout", user_id)` (`dependencies.py:500`). This one user-wide delete covers token-exchange, MCP OAuth/SAML and tool-OAuth rows.
3. It purges this process's per-user TTL caches: the `TokenExchangeService` cache (`token_exchange_service.py:219`) and the `TMSTokenStore` fallback (`tms_token_store.py:168`). This runs even if step 2 failed and adds no extra TMS round-trips. `invalidate_all_for_user` is not the delete primitive, because it swallows errors silently.
4. Any exception from steps 1-3 is caught at this one boundary. It logs one warning containing the `user_id` and the exception class name only. It never logs exception text, `str(exc)`, a traceback or any token value.

**Local `POST /v1/local-auth/logout`** (`local_auth_router.py:231`): after `authenticate`, run the unit off the event loop with `_user.id`. The existing cookie deletion, `USER_LOGOUT` activity event and response stay unchanged.

**Keycloak `GET /v1/user/log_out`** (`user.py:135`): resolve the user through the same machinery `authenticate` uses (`security/authentication.py:100`). The id must equal the `User.id` that keyed the TMS rows. Resolution never raises and never reads identity from a query parameter or any unverified input. If it resolves, run the unit. If not, log a sanitized warning and skip the purge. The cookie deletion and the 302 to `KEYCLOAK_LOGOUT_URL` stay unchanged, and the route may become async. The route stays cookie-usable (`idp/factory.py:89-93`).

## Acceptance mapping

- AC1: on both routes, a resolved user's TMS rows are deleted whenever the delete succeeds.
- AC2, AC3: with the rows gone, the next use of each flow finds no token and must re-authenticate. The logout pod's own caches are purged too.
- AC4: this is met only in part.
  - Handled safely: logout still completes, nothing leaks, and the failure is logged as a sanitized warning.
  - Not guaranteed: no reuse of stale tokens. A failed delete leaves the rows in TMS until they expire, and nothing retries it. Only the warning makes the failure visible.
  - The same applies to a Keycloak logout with an unresolvable session.
- AC5: no token value, exception text or traceback is added to any log, response or UI by this change.
- AC6: response bodies, redirects and cookie handling are byte-for-byte as today.
- AC7: automated tests below.

## Tests

- Unit tests for the cleanup unit:
  - Success path. The delete is called with the user id and the audit source `user_logout`.
  - Each failure type (TMSUnavailable, TMSAuditError, and RuntimeError from the handle lookup) returns without raising. The warning contains no sentinel secret placed in the exception message, and `enqueue_mcp_auth_cleanup` is never called.
  - The no-TMS no-op path.
  - Local caches are purged even when the delete fails.
- Route tests:
  - POST: cleanup is called with the user id. The cookie, event and message are unchanged, including when cleanup fails.
  - GET (currently untested): a resolved user is purged and gets a 302 plus cookie deletion. An unresolved user is not purged and still gets the 302. A cleanup failure still gets the 302.
- A logout-then-login test with `MockTokenManagementSystem`: seed rows for one user (exchange alias, an MCP config, a tool integration) and one other user, log out, then assert the first user's rows are gone and the other user's rows remain.

## Non-goals

- No change to codemie-enterprise, codemie-mcp-connect-service or codemie-ui (`auth.ts:81-95` stays).
- No fail-closed behavior, no logout error path, no retry, worker or scheduled cleanup.
- No change to the existing user-deactivation cleanup, `authenticate` (including its own failure logging), or the `TMSTokenStore` / `ToolOAuthTokenPort` error contracts.
- No new `USER_LOGOUT` activity event on the Keycloak route.
- No handling of session expiry or automatic logout, only explicit logout.
- No cross-pod cache purge, and no `MCPToolkitFactory` or mcp-connect client-cache cleanup.
- No upstream-provider token revocation.

## Open risks

- A failed delete leaves tokens in TMS until expiry with no retry. This is the accepted trade-off of the "complete logout, no scheduled cleanup" decision.
- A Keycloak logout with an expired session, or with identity the app cannot resolve (for example JWKS validation on and no Bearer header), skips the purge.
- Other pods' TTL caches can serve a token for up to `TOKEN_CACHE_TTL` (default 300s).
- The rule for "TMS in use" is left to the plan. `get_token_management_system` raises `RuntimeError` both when the package is absent and on a production misconfiguration.
- Calling `authenticate` at logout time reuses its DB user load and its own failure logging.
- A state-changing GET is CSRF-able. That already holds today for cookie deletion.
