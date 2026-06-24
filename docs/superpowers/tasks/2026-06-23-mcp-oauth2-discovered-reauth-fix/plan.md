# MCP OAuth2 Discovered Re-auth Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the `invalid_grant` re-auth dead loop for discovered OAuth2 MCP servers by making the resolver fall through to discovery instead of emitting an un-fulfillable `SESSION_EXPIRED` exception.

**Architecture:** Single method change in `MCPAuthResolver._resolve_discovered_server` (codemie-enterprise). Replace the `ReAuthenticationRequired` raise (lines 320–325) with the same invalidate-and-return-False pattern already used for `TokenRefreshError` on lines 326–328. No other files change.

**Tech Stack:** Python, pytest, poetry (codemie-enterprise repo only)

---

## File Map

| Action | File | Purpose |
|---|---|---|
| Modify | `codemie-enterprise/src/codemie_enterprise/mcp_auth/resolver.py:320–325` | Replace `ReAuthenticationRequired` raise with invalidate + return False |
| Modify | `codemie-enterprise/tests/mcp_auth/test_resolver.py` | Add two new tests (happy path + invalidate-raises resilience) for the discovered-flow `ReAuthenticationRequired` branch |

No other files.

---

## Task 1: Write the failing tests (RED)

**Files:**
- Modify: `codemie-enterprise/tests/mcp_auth/test_resolver.py`

All imports needed (`ReAuthenticationRequired`, `DiscoveryMetadataCacheEntry`, etc.) are already present in the file.

- [ ] **Step 1: Add the two new test functions**

Append both functions after `test_discovered_flow_token_refresh_error_falls_through_even_if_invalidate_raises` (line ~916) and before `test_discovered_flow_tms_unavailable_still_raises_config_error` (line ~919). Insert them between those two existing tests.

```python
def test_discovered_flow_invalid_grant_invalidates_credential_and_falls_through() -> None:
    canonical_resource = "https://mcp.example.com/api/mcp"
    issuer = "https://auth.example.com"
    discovered_auth_id = derive_discovered_auth_config_id("mcp-config-1", canonical_resource, issuer)
    invalidate_calls: list[tuple[str, str]] = []

    class TrackingTMS(StubTokenManagementSystem):
        def __init__(self) -> None:
            super().__init__(error=ReAuthenticationRequired("refresh_invalid_grant"))

        def invalidate_if_stale(self, user_id: str, auth_config_id: str) -> bool:
            invalidate_calls.append((user_id, auth_config_id))
            return True

    discovery_cache = SimpleNamespace(
        get=lambda resource: (
            DiscoveryMetadataCacheEntry(
                protected_resource_metadata={},
                authorization_server_metadata={"issuer": issuer},
            )
            if resource == canonical_resource
            else None
        )
    )
    resolver = _build_resolver(TrackingTMS(), endpoint_probe=lambda _: None, discovery_cache=discovery_cache)
    server_config = SimpleNamespace(
        auth_config=None,
        headers={},
        env={},
        url="https://MCP.Example.Com:443/api/mcp?v=1#section",
        mcp_config_id="mcp-config-1",
    )
    execution_context = SimpleNamespace(auth_headers={})

    # Must fall through — no exception raised, no auth headers injected
    resolver.resolve(server_config, "user-1", execution_context)

    assert invalidate_calls == [("user-1", discovered_auth_id)]
    assert execution_context.auth_headers == {}


def test_discovered_flow_invalid_grant_falls_through_even_if_invalidate_raises() -> None:
    canonical_resource = "https://mcp.example.com/api/mcp"
    issuer = "https://auth.example.com"

    class FailingInvalidateTMS(StubTokenManagementSystem):
        def __init__(self) -> None:
            super().__init__(error=ReAuthenticationRequired("refresh_invalid_grant"))

        def invalidate_if_stale(self, user_id: str, auth_config_id: str) -> bool:
            raise RuntimeError("db_down")

    discovery_cache = SimpleNamespace(
        get=lambda resource: (
            DiscoveryMetadataCacheEntry(
                protected_resource_metadata={},
                authorization_server_metadata={"issuer": issuer},
            )
            if resource == canonical_resource
            else None
        )
    )
    resolver = _build_resolver(
        FailingInvalidateTMS(), endpoint_probe=lambda _: None, discovery_cache=discovery_cache
    )
    server_config = SimpleNamespace(
        auth_config=None,
        headers={},
        env={},
        url="https://MCP.Example.Com:443/api/mcp?v=1#section",
        mcp_config_id="mcp-config-1",
    )
    execution_context = SimpleNamespace(auth_headers={})

    # Even with invalidation failure, no exception is raised — falls through
    resolver.resolve(server_config, "user-1", execution_context)

    assert execution_context.auth_headers == {}
```

- [ ] **Step 2: Run the new tests — confirm they FAIL**

Working directory: `codemie-enterprise/`

```bash
poetry run pytest tests/mcp_auth/test_resolver.py::test_discovered_flow_invalid_grant_invalidates_credential_and_falls_through tests/mcp_auth/test_resolver.py::test_discovered_flow_invalid_grant_falls_through_even_if_invalidate_raises -v
```

Expected: both FAIL. The first test raises `RuntimeError: session_expired|oauth2|None|discovered:...` because the current code raises instead of falling through. The second test also raises for the same reason.

---

## Task 2: Implement the fix and make tests GREEN

**Files:**
- Modify: `codemie-enterprise/src/codemie_enterprise/mcp_auth/resolver.py:320–325`

- [ ] **Step 3: Replace the ReAuthenticationRequired handler**

In `_resolve_discovered_server`, replace lines 320–325:

```python
# BEFORE (lines 320–325)
        except ReAuthenticationRequired as exc:
            raise self._authentication_required_factory(
                auth_config_id,
                status=SESSION_EXPIRED,
                auth_type="oauth2",
            ) from exc
```

with:

```python
# AFTER
        except ReAuthenticationRequired:
            self._invalidate_stale_discovered_credential(user_id, auth_config_id)
            return False
```

The indentation is 8 spaces (inside `try` inside `_resolve_discovered_server`). The `except TokenRefreshError` block immediately below (which remains unchanged) gives the exact surrounding context.

The `SESSION_EXPIRED` import on line 9 of resolver.py is still used by `_retrieve_token_data` (line 204), so do not remove it.

- [ ] **Step 4: Run the new tests — confirm they PASS**

```bash
poetry run pytest tests/mcp_auth/test_resolver.py::test_discovered_flow_invalid_grant_invalidates_credential_and_falls_through tests/mcp_auth/test_resolver.py::test_discovered_flow_invalid_grant_falls_through_even_if_invalidate_raises -v
```

Expected: both PASS.

- [ ] **Step 5: Run the full resolver test suite — confirm no regressions**

```bash
poetry run pytest tests/mcp_auth/test_resolver.py -v
```

Expected: all existing tests PASS, including:
- `test_resolve_maps_expired_oauth2_refresh_failure_to_session_expired` — configured-flow `ReAuthenticationRequired` still raises `session_expired` (line 202 path is untouched)
- `test_resolve_maps_expired_saml_session_to_session_expired` — SAML path unchanged
- `test_discovered_flow_token_refresh_error_invalidates_stale_credential_and_falls_through` — `TokenRefreshError` path unchanged

- [ ] **Step 6: Run the full test suite**

```bash
poetry run pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

Working directory: `codemie-enterprise/`

```bash
git add src/codemie_enterprise/mcp_auth/resolver.py tests/mcp_auth/test_resolver.py
git commit -m "EPMCDME-13049: Fix discovered OAuth2 MCP re-auth loop on invalid_grant"
```
