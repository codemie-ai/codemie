# EPMCDME-15392 Remove user TMS tokens on logout Implementation Plan

> **For agentic workers:** use superpowers:subagent-driven-development or superpowers:executing-plans. Commit per task using the repository's existing convention. Tests: `poetry run pytest <path> -v`.

**Goal:** Both logout routes delete the user's TMS rows and purge this pod's per-user token caches; logout never fails because of it.
**Architecture:** One sync, never-raising unit, called by both routes via awaited `asyncio.to_thread`. Reuses `tms_vault_ops.delete_all_for_user`; cache purge is cache-only (no extra TMS round-trips).
**Spec:** `docs/superpowers/tasks/2026-09-30-epmcdme-15392/spec.md`

## Global Constraints

- The unit never raises, enqueues, retries or schedules. Routes add no try/except.
- Logs carry `user_id` and `type(exc).__name__` only: no `str(exc)`, `exc_info`, `logger.exception`.
- Only the codemie repo changes; cookie, redirect and response bodies stay as they are.
- **"TMS in use" = `is_mcp_auth_enabled() or is_tool_oauth_enabled()`** (the only TMS writers). `get_token_management_system()` is no test: it builds a Postgres/Redis TMS on first call and raises `RuntimeError` when the package is absent.

## Review Focus

TMS/audit/handle failure still completes logout (2-4); other users' rows survive (2); both features off is a silent no-op (2); failed delete still purges the pod cache (2); unresolvable Keycloak session gives 302, no purge (4).

### Task 1: Cache-only purge primitives

**Files:** Modify `src/codemie/service/security/tms_token_store.py:168-193`, `token_exchange_service.py:219-230`, `oidc_token_exchange_service.py`; tests in the matching `tests/codemie/service/security/test_*.py`.
**Produces:** `TMSTokenStore.purge_fallback_for_user(user_id: str) -> None`; `TokenExchangeService.purge_user_cache(user_id: str) -> None`; `OIDCTokenExchangeService.purge_user_cache(user_id: str) -> None`.
**Test-first:** yes: seed caches for two users, purge one, assert only its keys vanish and no TMS delete is called.

- [ ] Write the failing tests.
- [ ] `TMSTokenStore`: move the prefix removal (190-193) into `purge_fallback_for_user`; `invalidate_all_for_user` calls it.
- [ ] `TokenExchangeService`: pop `_cache_key(user_id, p)` per `_STORE_ALIASES` principal, then `_store.purge_fallback_for_user` if `_store` is set. Not `clear_cache(user_id)`: its `_store.invalidate` is a TMS delete per alias.
- [ ] `OIDCTokenExchangeService`: same for `_store`, plus `_cache` keys prefixed `oidc_exchange:{user_id}:`.
- [ ] Run the three test files, expect PASS.

### Task 2: Logout cleanup unit

**Files:** Create `src/codemie/service/security/logout_token_cleanup.py`, `tests/codemie/service/security/test_logout_token_cleanup.py`, `test_logout_token_cleanup_mock_tms.py`. Modify `src/codemie/service/oauth_security.py` (add after line 37).
**Consumes:** Task 1 methods; `tms_vault_ops.delete_all_for_user(tms, audit_cm, user_id)`; `is_mcp_auth_enabled`, `get_token_management_system`, `tms_audit_context(source, correlation_id)` from `codemie.enterprise.mcp_auth.dependencies`.
**Produces:** `remove_user_tokens_on_logout(user_id: str) -> None`; `is_tool_oauth_enabled() -> bool` (ORs the three `*_OAUTH_ENABLED` flags).
**Test-first:** yes: unit tests fail on import; the mock-TMS test fails because user A's rows remain.

```python
def remove_user_tokens_on_logout(user_id: str) -> None:
    try:
        if not _tms_in_use():
            return
        try:
            _delete_all_tms_tokens(user_id)
        finally:
            _purge_process_caches(user_id)
    except Exception as exc:
        logger.warning(f"Logout token cleanup failed for user_id={user_id}: {type(exc).__name__}")
```

- [ ] Write the failing tests. Unit file (patch the bridge functions):
  - success: delete called with `user_id`, source `user_logout`, correlation id `user_id`;
  - `TMSUnavailable`, `TMSAuditError` (local classes), `RuntimeError` from handle lookup: no raise, one warning with the class name and no sentinel secret from the message, `enqueue_mcp_auth_cleanup` never called, purge methods still called;
  - both features off: handle never requested; `is_tool_oauth_enabled` true for any single flag.

  Mock-TMS file (`pytest.importorskip("codemie_enterprise.mcp_auth")`, `dependencies._tms` monkeypatched, MCP auth on): seed `MockTokenManagementSystem` for user A (`__idp_token__`, an MCP config id, a tool integration id) and user B, plus a `TokenExchangeService._store` fallback entry for A. After the call A's rows raise `TokenNotFound`, A's fallback entry is gone, B's rows remain.
- [ ] Implement `_tms_in_use()`, `_delete_all_tms_tokens(user_id)` (`delete_all_for_user` inside `tms_audit_context("user_logout", user_id)`), `_purge_process_caches(user_id)` (both Task 1 methods on the module singletons). Defer bridge imports into bodies, as `token_port.py:47` does. Leave the other inline flag expressions alone: their tests patch `config` per module.
- [ ] Run both new files, expect PASS.

### Task 3: Local `POST /v1/local-auth/logout`

**Files:** Modify `src/codemie/rest_api/routers/local_auth_router.py:231-253`. Test: `tests/codemie/rest_api/routers/test_local_auth_router.py:584-603`.
**Test-first:** yes: cleanup is never called with the user id yet.

- [ ] Add to `TestLogoutEndpoint`: cleanup called once with `mock_user.id` (patch in the router module); with a failure injected at `logout_token_cleanup._delete_all_tms_tokens` (`_tms_in_use` true), message, `delete_cookie` and event insert are unchanged and nothing raises.
- [ ] Make `await asyncio.to_thread(remove_user_tokens_on_logout, _user.id)` the first statement of `logout`, so a failing activity insert cannot skip the purge. Run the file, expect PASS.

### Task 4: Keycloak `GET /v1/user/log_out`

**Files:** Modify `src/codemie/rest_api/routers/user.py:135-144`. Test: `tests/codemie/rest_api/routers/test_user_router.py` (existing `AsyncClient` setup).
**Produces:** `async def _resolve_logout_user_id(request: Request) -> str | None` (module-private).
**Test-first:** yes: no purge happens for a resolved user yet.

- [ ] Write route tests (`follow_redirects=False`; patch `get_user_provider`, `get_idp_provider`, `remove_user_tokens_on_logout` in the router module):
  - resolved user: cleanup called with its id, 302, `Location` is `config.KEYCLOAK_LOGOUT_URL`, `Set-Cookie` deletes the session cookie;
  - provider raises with a sentinel in its message: no cleanup, 302, warning lacks the sentinel;
  - failure injected at `_delete_all_tms_tokens`: still 302.
- [ ] Make `logout` `async def logout(request: Request)`. `_resolve_logout_user_id` awaits `get_user_provider().authenticate_and_load_user(request, get_idp_provider())` (the pair `authenticate` uses, `authentication.py:134-142`), returns `user.id`, and on any `Exception` logs the class name only and returns `None`. Not `authenticate` itself: it logs tracebacks on every expired-session logout. With an id, `await asyncio.to_thread(remove_user_tokens_on_logout, user_id)`; otherwise log a sanitized "cleanup skipped" warning. Redirect and cookie deletion stay. Run the file, expect PASS.

## Negative-constraint pass

Honored: no retry/queue/worker or `enqueue_mcp_auth_cleanup` (Task 2 asserts); no fail-closed path (Tasks 3-4 failure tests); no change to deactivation cleanup, `authenticate` or store error contracts (Task 1 only extracts a helper); no enterprise/mcp-connect/UI, toolkit-cache, cross-pod, revocation or Keycloak `USER_LOGOUT` work; no exception text in logs (Tasks 2, 4 assert).
