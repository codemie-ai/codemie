# Technical Research

**Task**: mcp auth oauth2 discovered reauth resolver
**Generated**: 2026-06-23T00:00:00Z
**Research path**: codegraph + filesystem

---

## 1. Original Context

Fix the discovered OAuth2 MCP re-authentication loop where `invalid_grant` refresh failures in the resolver produce a `SESSION_EXPIRED` status with a generic `initiate_url` (no `discovered_flow_id`) that always returns HTTP 400, trapping users in an un-fulfillable re-authentication loop. The recommended fix (Approach A) is to treat `invalid_grant` in the discovered-flow branch of the resolver like other refresh failures — invalidate the dead credential and fall through to discovery — gated to discovered `auth_config_id`s only. An optional safety-net fix (Approach C) is to make `/oauth2/initiate` self-heal when no snapshot exists for a discovered server by running discovery itself instead of returning 400.

Root cause is in `codemie-enterprise/src/codemie_enterprise/mcp_auth/resolver.py` around line 317-335 — the `ReAuthenticationRequired` (invalid_grant) exception branch raises SESSION_EXPIRED immediately without creating a snapshot, while `TokenNotFound` and `TokenRefreshError` branches return False and fall through to discovery.

Key files to analyze (across two repos):
- codemie-enterprise: `src/codemie_enterprise/mcp_auth/resolver.py` (root cause)
- codemie-enterprise: `src/codemie_enterprise/mcp_auth/discovered_flow.py` (snapshot store)
- codemie-enterprise: `src/codemie_enterprise/mcp_auth/tms_refresh.py` (ReAuthenticationRequired raise)
- codemie: `src/codemie/enterprise/mcp_auth/_initiate.py` (400 endpoint)
- codemie: `src/codemie/enterprise/mcp_auth/router.py` (routing)
- codemie: `src/codemie/enterprise/mcp_auth/_discovery.py` (good snapshot creation path)
- codemie: `src/codemie/enterprise/mcp_auth/_post_auth.py` (other good snapshot path)
- codemie: `src/codemie/enterprise/mcp_auth/dependencies.py` (exception factory)
- codemie: `src/codemie/enterprise/mcp_auth/_uri.py` (session_binding_hash)
- codemie: `src/codemie/enterprise/mcp_auth/_common.py` (URL builders)
- Test files for any of the above

---

## 2. Codebase Findings

### Existing Implementations

#### Root Cause — `_resolve_discovered_server` in resolver.py

File: `/home/taras_spashchenko/EPAM/cm/codemie-enterprise/src/codemie_enterprise/mcp_auth/resolver.py`

The method `MCPAuthResolver._resolve_discovered_server` (lines 287–354) contains the bug. The relevant exception handling block (lines 318–335) is:

```python
except TokenNotFound:
    return False  # no stored token: server is not applicable, skip silently
except ReAuthenticationRequired as exc:
    raise self._authentication_required_factory(
        auth_config_id,
        status=SESSION_EXPIRED,
        auth_type="oauth2",
    ) from exc
except TokenRefreshError:
    self._invalidate_stale_discovered_credential(user_id, auth_config_id)
    return False
```

- `TokenNotFound` → returns `False` (falls through to discovery — correct)
- `TokenRefreshError` → invalidates credential, returns `False` (falls through to discovery — correct)
- `ReAuthenticationRequired` → **immediately raises `SESSION_EXPIRED`** without invalidating or falling through (the bug)

The `ReAuthenticationRequired` exception is raised by `OAuth2RefreshClient._refresh_impl` in `tms_refresh.py` line 144–148 when the token endpoint returns HTTP 400 with `error=invalid_grant`. This means an expired/revoked refresh token produces `SESSION_EXPIRED` with no `discovered_flow_id` in the exception payload, making it unresolvable by the client.

#### Existing `_invalidate_stale_discovered_credential` helper

File: `/home/taras_spashchenko/EPAM/cm/codemie-enterprise/src/codemie_enterprise/mcp_auth/resolver.py` lines 356–369

```python
def _invalidate_stale_discovered_credential(self, user_id: str, auth_config_id: str) -> None:
    try:
        invalidated = self._token_management_system.invalidate_if_stale(user_id, auth_config_id)
        ...
    except Exception:
        logger.warning(...)
```

This helper already exists for `TokenRefreshError`. Approach A reuses it verbatim for `ReAuthenticationRequired` in the discovered-flow branch.

#### Gate condition — `_is_discovered_auth_config_id`

File: `/home/taras_spashchenko/EPAM/cm/codemie/src/codemie/enterprise/mcp_auth/_common.py` line 99

```python
def _is_discovered_auth_config_id(auth_config_id: str | None) -> bool:
    return isinstance(auth_config_id, str) and auth_config_id.startswith("discovered:")
```

The `auth_config_id` at the point of the bug (resolver line 310: `auth_config_id, snapshot = resolved_auth_context`) is always a discovered ID (prefixed `discovered:`) because it was returned from `_resolve_discovered_auth_context`. The gate is therefore already implicitly present — but since `_resolve_discovered_server` is only reached from within the discovered-flow code path, no additional guard is needed in Approach A. The change is entirely self-contained.

#### `/oauth2/initiate` — why it returns 400 for discovered servers without a snapshot

File: `/home/taras_spashchenko/EPAM/cm/codemie/src/codemie/enterprise/mcp_auth/_initiate.py` lines 102–176, function `build_discovered_oauth2_initiate_response`

When called without a `discovered_flow_id`, the function calls `_load_discovered_flow_snapshot_for_binding_or_error` (line 122–127), which calls `_deps._require_initialized_discovered_flow_store().get_for_binding(...)`. If no binding snapshot exists in Redis (which is precisely the case after Approach A's fallthrough invalidates the credential without creating a new snapshot), it raises HTTP 400 via `_raise_client_error`:

```python
if snapshot is None:
    _raise_client_error(
        _INVALID_OAUTH2_CONFIG_MESSAGE,
        "No discovered OAuth2 flow is available for this MCP configuration and session.",
    )
```

This is the second part of the loop: the `SESSION_EXPIRED` exception produced by the resolver carries `initiate_url=/v1/mcp-auth/oauth2/initiate` (no `discovered_flow_id` query param), which the client calls, hitting `build_discovered_oauth2_initiate_response` with no `discovered_flow_id`, which cannot find a snapshot and returns 400.

Approach C would self-heal this endpoint by triggering discovery inline rather than returning 400 when no snapshot is found.

#### Snapshot creation — the good path in `_discovery.py`

File: `/home/taras_spashchenko/EPAM/cm/codemie/src/codemie/enterprise/mcp_auth/_discovery.py` lines 165–229, function `_resolve_discovered_candidate_payload`

This is where a valid `DiscoveredOAuth2FlowSnapshot` is created and stored:

```python
resolution = await resolve_discovered_oauth2_flow(...)
deps._require_initialized_discovered_flow_store().store(resolution.snapshot)
```

The snapshot stores `discovered_flow_id`, `discovered_auth_id`, `flow_config`, and `session_binding_hash`. The `_build_discovered_resolved_payload` function then includes `initiate_url = _build_discovered_initiate_url(resolution.discovered_flow_id)` which produces a URL with `?discovered_flow_id=df_...`. This is the correct URL format that `build_discovered_oauth2_initiate_response` in `_initiate.py` can handle.

#### `_build_discovered_initiate_url` — URL builder

File: `/home/taras_spashchenko/EPAM/cm/codemie/src/codemie/enterprise/mcp_auth/_common.py` line 103

```python
def _build_discovered_initiate_url(discovered_flow_id: str) -> str:
    return f"/v1/mcp-auth/oauth2/initiate?{urlencode({'discovered_flow_id': discovered_flow_id})}"
```

When `SESSION_EXPIRED` is raised in the resolver (the bug), the exception factory `_build_authentication_required_exception` in `dependencies.py` (line 425) calls `get_mcp_auth_status_payload(auth_config_id)` which fetches the `mcp_config` by `auth_config_id`. For a `discovered:` auth_config_id this lookup likely returns `None` (discovered IDs are not stored as persisted `auth_config.id` values), so the payload contains no `mcp_config_id` and no `discovered_flow_id` — making the `initiate_url` generic and non-functional.

#### `_build_authentication_required_exception` — exception factory

File: `/home/taras_spashchenko/EPAM/cm/codemie/src/codemie/enterprise/mcp_auth/dependencies.py` lines 425–440

```python
def _build_authentication_required_exception(
    auth_config_id: str,
    *,
    status: str = "authentication_required",
    auth_type: str | None = None,
    error_context: str | None = None,
) -> MCPAuthenticationRequiredException:
    payload: dict[str, Any] = dict(get_mcp_auth_status_payload(auth_config_id) or {"auth_config_id": auth_config_id})
    payload.update({"status": status, "auth_type": auth_type, "error_context": error_context})
    return MCPAuthenticationRequiredException(payload)
```

For a `discovered:` auth_config_id, `get_mcp_auth_status_payload` returns `None` (no MCPConfig row has `auth_config.id = "discovered:..."`) so the payload only contains `{"auth_config_id": "discovered:..."}`. The resulting exception has no `initiate_url`, no `discovered_flow_id`, and status `SESSION_EXPIRED` — a dead end for the client.

#### `session_binding_hash` — how it flows

File: `/home/taras_spashchenko/EPAM/cm/codemie/src/codemie/enterprise/mcp_auth/_uri.py` line 258–266

```python
def _get_authenticated_bearer_token_hash(user: User) -> str:
    ...
    return hashlib.sha256(user.auth_token.encode("utf-8")).hexdigest()
```

The `session_binding_hash` is the SHA-256 of the user's bearer token. It is set in `execution_context.session_binding_hash` before the resolver is called. The resolver reads it from `execution_context` in `_resolve_discovered_auth_context_from_snapshot` (line 423). This is the key that binds a discovered flow snapshot to a specific user+session.

### Architecture and Layers Affected

| Layer | Component | Change needed |
|---|---|---|
| Token Resolver (codemie-enterprise) | `MCPAuthResolver._resolve_discovered_server` | Approach A: change `ReAuthenticationRequired` handler to invalidate + return False |
| Initiate Endpoint (codemie) | `build_discovered_oauth2_initiate_response` in `_initiate.py` | Approach C (optional): run discovery inline when no snapshot exists |
| Router (codemie) | `initiate_oauth2_enabled` in `router.py` | No change needed for Approach A; minor change if Approach C is implemented |
| Exception Factory (codemie) | `_build_authentication_required_exception` in `dependencies.py` | No change for Approach A |

### Integration Points

- **TMS** (`token_management_system`): `invalidate_if_stale(user_id, auth_config_id)` is called by `_invalidate_stale_discovered_credential`. Already handles exceptions gracefully (logs and continues).
- **Redis discovered flow store** (`_discovered_flow_store`): `get_for_binding` / `store` — used for snapshot lookup in `_resolve_discovered_auth_context_from_snapshot` and for creating snapshots in `_discovery.py`.
- **Discovery cache** (`_discovery_cache`): `get(canonical_resource)` — used as fallback to derive `auth_config_id` without a snapshot, in `_resolve_discovered_auth_config_id_from_cache`.
- **`MCPAuthenticationRequiredException`**: propagated to the client as the auth error payload; its contents (particularly `initiate_url` and `discovered_flow_id`) determine whether the client can retry.
- **`OAuth2RefreshClient.refresh`** (tms_refresh.py line 49): raises `ReAuthenticationRequired("refresh_invalid_grant")` for `invalid_grant` HTTP 400 responses at line 143–152.

### Patterns and Conventions

- **Fall-through pattern for discovered servers**: `TokenNotFound` and `TokenRefreshError` both return `False` from `_resolve_discovered_server`, causing the resolver chain to continue and eventually trigger a new discovery flow. Approach A applies this same pattern to `ReAuthenticationRequired` within the discovered-flow branch.
- **Invalidation before fall-through**: `TokenRefreshError` calls `_invalidate_stale_discovered_credential` before returning `False`. Approach A follows the same sequence for `ReAuthenticationRequired`.
- **Static helper methods**: `_invalidate_stale_discovered_credential` is an instance method; `_remember_oauth_token_data` etc. are `@staticmethod`. No new methods needed for Approach A.
- **Gate on `auth_config_id` prefix**: The `_is_discovered_auth_config_id` check (prefix `discovered:`) exists in `_common.py` and is used in `_post_auth.py`. In the resolver the discovered-branch code is already scoped to discovered IDs by construction — no explicit string prefix check is required.
- **Error propagation**: `SESSION_EXPIRED` without a `discovered_flow_id` is the broken state. The fix avoids raising `SESSION_EXPIRED` from the discovered branch entirely for `ReAuthenticationRequired`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

The `.ai-run/guides/` directory exists in the codemie repo. Relevant guides:
- `/home/taras_spashchenko/EPAM/cm/codemie/.ai-run/guides/integration/mcp-integration.md` — MCP configuration and tools
- `/home/taras_spashchenko/EPAM/cm/codemie/.ai-run/guides/architecture/layered-architecture.md` — layered architecture
- `/home/taras_spashchenko/EPAM/cm/codemie/.ai-run/guides/development/error-handling.md` — typed exceptions

### Architectural Decisions

1. **Discovered vs. static auth_config**: Discovered servers have no persisted `auth_config` dict in the MCPConfig. Their `auth_config_id` is always prefixed `discovered:` and is derived from `(mcp_config_id, canonical_resource, issuer)`. This means `get_mcp_auth_status_payload(discovered_auth_config_id)` always returns `None`.

2. **Snapshot as the handoff mechanism**: The `DiscoveredOAuth2FlowSnapshot` in Redis is the only bridge between the resolver's knowledge of a discovered flow and the `/oauth2/initiate` endpoint. Without a snapshot, the initiate endpoint cannot build the OAuth2 authorization URL.

3. **`session_binding_hash` scoping**: Snapshots are scoped to `(user_id, session_binding_hash, mcp_config_id)`. The session_binding_hash ties the snapshot to the user's current bearer token, providing CSRF-like protection.

4. **Two-level cache**: `_resolve_discovered_auth_context` tries the snapshot store first (fresh, session-bound), then falls back to the discovery cache (coarser-grained, not session-bound). The discovery cache does not contain a snapshot — only the `auth_config_id` needed to look up a token. For a re-auth scenario, the snapshot is essential.

### Derived Conventions

- Exception types from `tms_interface` are treated as control-flow signals: `TokenNotFound` = never authenticated, `TokenRefreshError` = transient/permanent refresh failure, `ReAuthenticationRequired` = user must re-authenticate.
- In non-discovered (static auth_config) paths, `ReAuthenticationRequired` correctly means "show SESSION_EXPIRED and direct user to re-auth" because the `initiate_url` is a known static endpoint (`/v1/mcp-auth/oauth2/initiate` with `mcp_config_id`).
- In the discovered path, the same exception requires a different response because the `initiate_url` must carry a `discovered_flow_id` that only exists after a new discovery+snapshot creation cycle.

---

## 4. Testing Landscape

### Existing Coverage

- `/home/taras_spashchenko/EPAM/cm/codemie-enterprise/tests/mcp_auth/test_resolver.py` — comprehensive resolver tests using `StubTokenManagementSystem` and `DiscoveredOAuth2FlowSnapshot`. Covers `TokenNotFound`, `TokenRefreshError`, and `ReAuthenticationRequired` in the static auth_config branch but likely does **not** cover `ReAuthenticationRequired` in the discovered-flow branch specifically.
- `/home/taras_spashchenko/EPAM/cm/codemie/tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py` — FastAPI integration tests for the `/oauth2/initiate` endpoint.
- `/home/taras_spashchenko/EPAM/cm/codemie-enterprise/tests/mcp_auth/test_oauth2_flow.py` — OAuth2 flow tests.
- `/home/taras_spashchenko/EPAM/cm/codemie-enterprise/tests/mcp_auth/test_tms.py` — TMS tests.

### Testing Framework and Patterns

- **Framework**: pytest (both repos)
- **Fixtures**: `StubTokenManagementSystem` (inline stub in test_resolver.py), `SimpleNamespace` for server_config and execution_context
- **Mocking**: tests patch `_build_authentication_required_exception` directly; discovered flow tests use `DiscoveredOAuth2FlowSnapshot` builder functions
- **Test style**: arrange/act/assert; exception assertions via `pytest.raises`

### Coverage Gaps

1. **`ReAuthenticationRequired` in `_resolve_discovered_server`**: The existing test file likely has no test for `invalid_grant` (ReAuthenticationRequired) in the discovered-flow branch specifically. The new behavior (invalidate + return False) needs a dedicated test in `test_resolver.py`.
2. **`build_discovered_oauth2_initiate_response` with no snapshot (Approach C)**: If Approach C is implemented, a test for the self-healing path needs to be added to `test_oauth2_initiate_bridge.py`.
3. **End-to-end loop test**: No integration test currently exercises the full cycle of `invalid_grant` → discovered re-auth → new snapshot → successful initiate.

---

## 5. Configuration and Environment

### Environment Variables

- `MCP_AUTH_ENABLED` — gates the entire MCP auth feature; both fix approaches are behind this gate
- `MCP_AUTH_HMAC_SECRET` — required for Redis encryption of snapshots
- `MCP_AUTH_REDIS_KEY_NAMESPACE` — Redis namespace for snapshot keys
- `MCP_AUTH_TMS_ENABLED` — enables the real PostgreSQL TMS (vs. mock)
- `MCP_AUTH_TMS_REFRESH_TIMEOUT_SECONDS` — timeout for TMS refresh operations (affects how long before a stale credential is detected)
- `CALLBACK_API_BASE_URL` — used to build redirect URIs and the client metadata document URL (needed for Approach C discovery)

### Configuration Files

- `codemie-enterprise/src/codemie_enterprise/mcp_auth/_shared.py` — shared status constants (`SESSION_EXPIRED`, `AUTHENTICATION_REQUIRED`, `CONFIG_ERROR`)
- `codemie-enterprise/src/codemie_enterprise/mcp_auth/tms_interface.py` — defines `ReAuthenticationRequired`, `TokenNotFound`, `TokenRefreshError`
- `codemie/src/codemie/enterprise/mcp_auth/_constants.py` — UI-facing message strings

### Feature Flags and Deployment Concerns

- The fix is entirely within the enterprise package resolver (Approach A) and optionally the codemie initiate handler (Approach C). No schema migrations, no new env vars, no deployment config changes are needed.
- Redis TTL for discovered flow snapshots is `DISCOVERED_FLOW_TTL_SECONDS = 900` (15 min), `DISCOVERED_FLOW_MIN_TTL_SECONDS = 600` (10 min). After Approach A, a new discovery cycle must complete before a snapshot is available. This happens asynchronously via the discovery probe (toolkit load), not synchronously in the resolver.

---

## 6. Risk Indicators

- **Core risk**: After Approach A's fallthrough (ReAuthenticationRequired → return False), the resolver silently declines the discovered server. The toolkit_service will eventually trigger a new discovery cycle on the next tool load, creating a new snapshot. However, there is a window between token invalidation and new snapshot creation during which the server is unauthenticated.
- **`_invalidate_stale_discovered_credential` behavior**: `invalidate_if_stale` may be a no-op if the credential is already gone. The existing `TokenRefreshError` branch calls this same helper — it has been proven safe in that context.
- **`get_mcp_auth_status_payload` returns None for discovered IDs**: The exception factory falls back to `{"auth_config_id": auth_config_id}` when `get_mcp_auth_status_payload` returns None. This means the `MCPAuthenticationRequiredException` raised from the static auth_config `ReAuthenticationRequired` path also lacks `mcp_config_id` for discovered IDs — but this path is not reached in the discovered-flow branch.
- **Approach C complexity**: Self-healing in `build_discovered_oauth2_initiate_response` requires triggering async discovery from a sync FastAPI endpoint, which mirrors the `_rebuild_discovered_snapshot_from_exact_context` pattern in `_post_auth.py`. This is non-trivial and would require access to the discovery cache, DCR cache, and flow store — all of which are available through `_deps`. Risk is moderate.
- **No covering tests for `_resolve_discovered_server` + `ReAuthenticationRequired`**: The codegraph blast radius analysis explicitly flagged no covering tests for the resolver's discovered-flow branch. New tests are required.
- **No test for `build_discovered_oauth2_initiate_response` without snapshot**: The `test_oauth2_initiate_bridge.py` tests the happy path and the `discovered_flow_id`-provided path, but does not cover the `snapshot is None` branch that Approach C would change.
- **Two-repo change**: Approach A touches only `codemie-enterprise/src/codemie_enterprise/mcp_auth/resolver.py`. Approach C additionally touches `codemie/src/codemie/enterprise/mcp_auth/_initiate.py`. Both repos must be released together if Approach C is included.
- **`invalidate_if_stale` vs. `invalidate`**: The resolver uses `invalidate_if_stale` (not a hard `invalidate`). Confirm that `invalidate_if_stale` behaves correctly for a token that failed with `invalid_grant` — it should invalidate because the token is definitively dead, not merely stale due to a transient error.

---

## 7. Summary for Complexity Assessment

The fix for Approach A is a **minimal, surgical change in a single method** (`_resolve_discovered_server`) in a single file (`resolver.py` in codemie-enterprise). The change is: move the `ReAuthenticationRequired` handler to call `self._invalidate_stale_discovered_credential(user_id, auth_config_id)` and return `False`, exactly mirroring the existing `TokenRefreshError` handler on lines 326–328. No new abstractions, no new dependencies, no API changes, no DB migrations. The risk is low: the helper is already used for `TokenRefreshError` in the same method, and the control flow after the change is identical to `TokenRefreshError`. A test must be added to `test_resolver.py` verifying that `ReAuthenticationRequired` in the discovered branch triggers invalidation and returns `False` (not `SESSION_EXPIRED`).

The affected architectural layers are: Token Resolver (codemie-enterprise) for Approach A, and additionally the OAuth2 Initiate Endpoint (codemie) for the optional Approach C safety net. Approach A alone changes 1 file with approximately 3–5 lines modified. Approach C adds another 1 file change with perhaps 15–30 lines to implement inline self-healing discovery. Test additions would cover 1–2 new test functions per approach.

The test coverage posture for this area is mixed: `test_resolver.py` has broad coverage of the resolver but has a specific gap for `ReAuthenticationRequired` in the discovered-flow sub-path. `test_oauth2_initiate_bridge.py` covers the initiate endpoint but lacks coverage of the no-snapshot discovered path. The key risk factor is behavioral regression in the `TokenRefreshError` path (which must continue to work as before) and ensuring the `invalid_grant` signal from the TMS is correctly classified as a permanent (invalidate) failure rather than a transient (retry) one. The `refresh_invalid_grant` reason code in `tms_refresh.py` is clear — `invalid_grant` is always a permanent OAuth2 error code, making the invalidation approach correct.
