# Fix: Discovered OAuth2 MCP re-authentication dead loop

**Date**: 2026-06-23  
**Scope**: `codemie-enterprise` — `resolver.py` only  
**Size**: S (1 file, ~5 lines changed, 1 new test)

---

## Problem

When a user's stored OAuth2 refresh token for a "discovered" MCP server expires and the token endpoint returns `invalid_grant`, the backend emits a `SESSION_EXPIRED` re-auth instruction pointing at a bare `/v1/mcp-auth/oauth2/initiate` URL with no `discovered_flow_id`. That endpoint requires a per-session Redis snapshot to build the OAuth2 authorization URL; no snapshot is ever created by the session-expired path, so the endpoint returns HTTP 400 every time. The user is trapped in a loop.

Root cause: `_resolve_discovered_server` in `resolver.py` handles `ReAuthenticationRequired` (invalid_grant) by immediately raising `SESSION_EXPIRED`, while `TokenNotFound` and `TokenRefreshError` in the same method return `False` and fall through to the discovery probe — which correctly creates a snapshot and returns a working `discovered_flow_id`'d initiate URL.

---

## Solution

Make `ReAuthenticationRequired` in `_resolve_discovered_server` behave identically to `TokenRefreshError`: invalidate the dead credential, return `False`, and let the existing discovery probe create a fresh snapshot.

### Change

**File**: `codemie-enterprise/src/codemie_enterprise/mcp_auth/resolver.py`, lines 320–325

```python
# BEFORE
except ReAuthenticationRequired as exc:
    raise self._authentication_required_factory(
        auth_config_id,
        status=SESSION_EXPIRED,
        auth_type="oauth2",
    ) from exc

# AFTER
except ReAuthenticationRequired:
    self._invalidate_stale_discovered_credential(user_id, auth_config_id)
    return False
```

No other files change. No schema migrations, no new env vars, no API changes.

### Gating

No explicit `_is_discovered_auth_config_id` guard is needed. `_resolve_discovered_server` is only reached after `_can_handle_discovered_server` passes, which requires the server to have no persisted `auth_config` and a valid URL + `mcp_config_id`. Every `auth_config_id` resolved inside that method is therefore always a `discovered:*` ID. The configured-flow `ReAuthenticationRequired` handler in `_retrieve_token_data` (line 202) is untouched.

### Fixed data flow

1. User triggers a tool call → resolver finds a stored discovered token → TMS refresh fails with `invalid_grant` → `ReAuthenticationRequired`
2. Resolver calls `_invalidate_stale_discovered_credential`, returns `False`
3. The toolkit service proceeds to connect without credentials → MCP server returns 401 → discovery probe runs → new `DiscoveredOAuth2FlowSnapshot` stored in Redis
4. `authentication_required` payload includes `initiate_url=...?discovered_flow_id=<new_id>`
5. User clicks Re-authenticate → `POST /oauth2/initiate?discovered_flow_id=<new_id>` → snapshot found → OAuth2 flow starts

### What does NOT change

- `_retrieve_token_data` (line 202) — configured-flow `ReAuthenticationRequired` handler: SESSION_EXPIRED is actionable there because the initiate endpoint can rebuild from the stored `auth_config`. Untouched.
- Dialog status badge: changes from `session_expired` → `authentication_required`. The discovery path produces `authentication_required`, which is more accurate (the user must authenticate — the credential is gone).
- Approach C (self-healing `/oauth2/initiate`): descoped. The bare-URL path it would fix is not the primary failure. C needs its own proper scoping (covering both by-id and by-binding TTL misses, extracting a shared `ensure_discovered_snapshot` helper reused by the toolkit). Separate ticket.
- Approach D (decouple snapshot from bearer token, extend TTL): structural improvement, separate ticket.

---

## Tests

**File**: `codemie-enterprise/tests/mcp_auth/test_resolver.py`

One new test case:

> Given a discovered server with a stored token whose TMS `retrieve` raises `ReAuthenticationRequired`, assert that:
> 1. `_invalidate_stale_discovered_credential` is called
> 2. `resolve()` does **not** raise `MCPAuthenticationRequiredException` (no SESSION_EXPIRED)
> 3. `resolve()` returns a falsy value (falls through, allowing discovery to run)

Regression guard: existing tests for configured-flow `ReAuthenticationRequired` (line 202 path) must continue passing without modification.

Fixtures available: `StubTokenManagementSystem`, `DiscoveredOAuth2FlowSnapshot` builders, and `SimpleNamespace` mocks for `server_config` and `execution_context` — all already used in neighboring tests.

---

## Verification

- Deploy to staging; force-expire a stored refresh token for a discovered MCP server; confirm clicking Re-authenticate starts the OAuth2 flow instead of looping.
- Confirm configured-flow re-auth (e.g. SAML SESSION_EXPIRED) is unchanged.
