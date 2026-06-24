# Approach C: Inline Discovery Safety Net on MCP OAuth2 Initiate Endpoint

**Ticket**: EPMCDME-13049
**Branch**: EPMCDME-13049 (codemie only)
**Date**: 2026-06-24
**Scope**: codemie — `_initiate.py`, `toolkit_service.py`; no codemie-enterprise change
**Size**: M (16/36) — new classmethod + builder + flag thread + heal wiring

---

## Problem

`POST /v1/mcp-auth/oauth2/initiate` dead-ends on a missing discovered-flow snapshot. Two entry points hit this:

1. **By-binding miss** — the user calls the endpoint directly without a `discovered_flow_id`; no snapshot is bound to their session+config combination.
2. **By-id-expired** — the user calls with a `discovered_flow_id` that has expired from Redis (TTL ≤ 900 s).

In both cases the existing loaders call `_raise_client_error` → `HTTP_400`. The user cannot proceed: there is no way to get a working `discovered_flow_id` without calling a tool first, but calling the tool is what triggered the auth requirement.

Root cause and Approach A fix are documented in `local/mcp-discovered-oauth2-reauth-issue.md`. Approach A lands in `codemie-enterprise/resolver.py` and handles the `invalid_grant` → `False`-return case. Approach C is an independent safety net in `codemie` that runs inline discovery on the initiate endpoint so neither entry point can dead-end regardless of resolver behaviour.

---

## Solution: Strategy C-ii — Credential-less Inline Discovery

When the initiate endpoint cannot find a snapshot, run the existing discovery probe inline before returning the `400`:

1. Connect to the MCP server **without credentials** (new `skip_auth_resolution=True` flag).
2. The server returns `401 + WWW-Authenticate`.
3. `_build_discovery_candidate_from_challenge` extracts the challenge.
4. The existing `_run_discovery_probe_and_collect_failures` pipeline runs: SSRF-gated probe → DCR registration → snapshot stored in Redis **keyed by binding** (`user_id + session_binding_hash + mcp_config_id`).
5. Re-read the snapshot by binding. Return `build_discovered_oauth2_initiate_response` with the fresh snapshot.

If any step fails, fall through to the existing `_raise_client_error` — same `400` as today, never a `500`.

---

## Locked Decisions

| # | Question | Decision | Rationale |
|---|---|---|---|
| Q1 | Credential-less connect mechanism | Add `skip_auth_resolution: bool = False` flag | Explicit; A-independent; bypasses all resolvers incl. legacy fallback so no credential is injected regardless of token state |
| Q2 | `_mcp_server_from_config` placement | `@staticmethod` on `MCPToolkitService` | Lives next to `_build_discovery_candidate_from_challenge`; private to the class |
| Q3 | Timeout budget | Reuse `discovery_probe_overall_timeout_seconds` from `_resolve_discovery_probe_runtime_config` | Consistency; already in the pipeline |
| Q4 | Scope | Both entry points (by-binding + by-id-expired) | Collapses onto one binding-keyed re-read after heal |

---

## Architecture

### Heal placement: `build_discovered_oauth2_initiate_response` only

Both heals live in `build_discovered_oauth2_initiate_response` (`_initiate.py:102`), wrapping the snapshot-load ternary at lines 120–128. The shared loaders (`_load_discovered_flow_snapshot_for_binding_or_error`, `_deps._load_discovered_flow_snapshot_or_error`) are **not modified** — they remain the authoritative 400 path.

```
build_discovered_oauth2_initiate_response(_initiate.py:102)
│
├── guards lines 108–117 (wrong config type → 400, unchanged)
├── session_binding_hash = _get_authenticated_bearer_token_hash(user)  [line 119]
│
├── [C] non-raising probe:
│     if discovered_flow_id → store.get(id)           → None on miss/expired
│     else                  → store.get_for_binding() → None on absent
│     (infra errors propagate as-is — only genuine None triggers heal)
│
├── [C] heal if probe is None:
│     lazy import MCPToolkitService
│     MCPToolkitService.ensure_discovered_snapshot_for_server(
│         mcp_config, user.id, session_binding_hash
│     )
│     → if discovered_flow_id returned: re-read by binding
│       (discovery always stores by binding, never by old stale id)
│       → both paths collapse onto one binding re-read
│
└── fallback: existing raising loaders → same 400 as today
```

### `ensure_discovered_snapshot_for_server` pipeline

```
_mcp_server_from_config(mcp_config)
  └─→ MCPServerDetails(name, mcp_config_id, config=MCPServerConfig(**config.model_dump()))

_process_single_mcp_server(mcp_server, cls.get_instance(), skip_auth_resolution=True)
  └─→ _prepare_server_config(..., skip_auth_resolution=True)
        └─→ _resolve_server_auth(..., skip_auth_resolution=True)
              └─→ early return (bypasses enterprise loop + legacy fallback)
  └─→ server returns 401+WWW-Authenticate → MCPToolLoadException

_build_discovery_candidate_from_challenge(mcp_server, exc)
  └─→ {mcp_config_id, mcp_config_name, mcp_resource_url, www_authenticate_header,
        allow_issuer_prefix_match}
      (mcp_config_id from outer MCPServerDetails.mcp_config_id;
       allow_issuer_prefix_match from inner MCPServerConfig via model_dump())
      (_build_mcp_server_config propagates mcp_config_id/name onto inner config
       at toolkit_service.py:1414–1415 — catalog-resolution survival confirmed)

_run_discovery_probe_and_collect_failures(
    discovery_candidates=[candidate],
    user_id=user_id,          ← real user_id (not None) so binding stores correctly
    session_binding_hash=session_binding_hash,
    workflow_execution_id=None,
)
  └─→ side effect: snapshot stored in Redis by binding
  └─→ auth_failures[0].get("discovered_flow_id")
```

**user_id asymmetry is load-bearing**: `_process_single_mcp_server` gets `user_id=None` (credential-less connect); `_run_discovery_probe_and_collect_failures` gets the real `user_id` so `_discovery.py:185` stores the snapshot under the correct binding.

---

## Component Specifications

### `MCPToolkitService._mcp_server_from_config` (new `@staticmethod`)

```python
@staticmethod
def _mcp_server_from_config(mcp_config: Any) -> MCPServerDetails:
    from codemie.rest_api.models.assistant import MCPServerDetails
    from codemie.service.mcp.models import MCPServerConfig
    return MCPServerDetails(
        name=mcp_config.name,
        mcp_config_id=mcp_config.id,
        config=MCPServerConfig(**mcp_config.config.model_dump()),
    )
```

### `MCPToolkitService._resolve_server_auth` (modified)

Add `skip_auth_resolution: bool = False`. When `True`, return immediately before the auth-config guard, the enterprise resolver loop, and the legacy token resolver.

### `MCPToolkitService._prepare_server_config` (modified)

Add `skip_auth_resolution: bool = False`. Thread to `_resolve_server_auth`.

### `MCPToolkitService._process_single_mcp_server` (modified)

Add `skip_auth_resolution: bool = False`. Thread to `_prepare_server_config`.

### `MCPToolkitService.ensure_discovered_snapshot_for_server` (new `@classmethod`)

```python
@classmethod
def ensure_discovered_snapshot_for_server(
    cls,
    *,
    mcp_config: Any,
    user_id: str,
    session_binding_hash: str,
) -> str | None:
    mcp_server = cls._mcp_server_from_config(mcp_config)
    try:
        cls._process_single_mcp_server(
            mcp_server=mcp_server,
            default_toolkit_service=cls.get_instance(),
            user_id=None,
            skip_auth_resolution=True,
        )
        return None                      # no 401 — server accessible without auth
    except MCPToolLoadException as exc:
        candidate = cls._build_discovery_candidate_from_challenge(mcp_server, exc)
        if candidate is None:
            return None                  # 401 but no WWW-Authenticate
    except (MCPAuthenticationRequiredException, BrokerAuthRequiredException):
        return None                      # auth configured — can't use discovery path

    auth_failures, _ = cls._run_discovery_probe_and_collect_failures(
        discovery_candidates=[candidate],
        user_id=user_id,
        session_binding_hash=session_binding_hash,
        workflow_execution_id=None,
    )
    if not auth_failures:
        return None
    return auth_failures[0].get("discovered_flow_id")
```

### Heal wiring in `build_discovered_oauth2_initiate_response` (`_initiate.py`)

Replace the ternary at lines 120–128 with:

```python
# 1. Non-raising probe (only genuine None triggers heal; infra errors propagate)
store = _deps._require_initialized_discovered_flow_store()
probe = store.get(discovered_flow_id) if discovered_flow_id else \
        store.get_for_binding(user.id, session_binding_hash, mcp_config.id)

# 2. Heal on absence
if probe is None:
    from codemie.service.mcp.toolkit_service import MCPToolkitService
    new_flow_id = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id=user.id,
        session_binding_hash=session_binding_hash,
    )
    if new_flow_id is not None:
        probe = store.get_for_binding(user.id, session_binding_hash, mcp_config.id)

# 3. Fallback: original raising loaders (same 400 as today)
snapshot = (
    _deps._load_discovered_flow_snapshot_or_error(discovered_flow_id)
    if (probe is None and discovered_flow_id)
    else _load_discovered_flow_snapshot_for_binding_or_error(
        user_id=user.id,
        session_binding_hash=session_binding_hash,
        mcp_config_id=mcp_config.id,
    )
) if probe is None else probe
```

---

## Error Handling

| Scenario | Handling | HTTP result |
|---|---|---|
| Heal succeeds | Returns `discovered_flow_id`; re-read by binding succeeds | 200 |
| Server accessible without 401 | `_process_single_mcp_server` returns; `ensure_...` returns None | 400 (same as today) |
| 401 but no WWW-Authenticate | `_build_discovery_candidate_from_challenge` returns None | 400 |
| Discovery probe returns no `auth_failures` | `not auth_failures` → None | 400 |
| `MCPAuthenticationRequiredException` | explicit `except` in `ensure_...` | 400 |
| `BrokerAuthRequiredException` | explicit `except` in `ensure_...` | 400 |
| Other exception inside `_process_single_mcp_server` | wrapped to `MCPToolLoadException` (line 750–753) then caught | 400 |
| Redis unavailable in non-raising probe | propagates (not caught); fallback loader raises 503 | 503 (unchanged) |
| Heal + re-read still absent | `probe` stays None; fallback raising loader raises 400 | 400 |

True 400-parity: the only path to a non-400 result is a successful `discovered_flow_id` return from `ensure_...` followed by a successful re-read. Every failure exits via the existing `_raise_client_error`.

---

## Test Obligations

The RED tests (this pass) must cover:

| Test | File | Description |
|---|---|---|
| By-binding miss → heal → 200 | `test_oauth2_initiate_bridge.py` | No snapshot bound; `ensure_...` returns `discovered_flow_id`; re-read succeeds; full initiate response returned |
| By-id-expired → heal → 200 | `test_oauth2_initiate_bridge.py` | `discovered_flow_id` present but store.get returns None; heal; re-read by binding succeeds |
| Discovery fails → same 400 (not 500) | `test_oauth2_initiate_bridge.py` | No snapshot; `ensure_...` returns None; original loader raises `ExtendedHTTPException(400)`, not 500 |
| SSRF path exercised | `test_toolkit_service_auth_resolver.py` | `_run_discovery_probe_and_collect_failures` is called; no raw HTTP |
| Auth rejection precedes discovery | `test_oauth2_initiate_bridge.py` | `_check_mcp_config_access` raises 403 before `ensure_...` is called |
| Valid snapshot → C does not run | `test_oauth2_initiate_bridge.py` | Snapshot present; `ensure_...` never called |
| Catalog-resolution survival | `test_toolkit_service_auth_resolver.py` | Synthetic `MCPServerDetails` from `_mcp_server_from_config` survives `_build_mcp_server_config` → produces a real connect attempt (no None return) |
| `allow_issuer_prefix_match` round-trip | `test_toolkit_service_auth_resolver.py` | `MCPConfig` with `allow_issuer_prefix_match=True` → field present in candidate dict |
| Infra error in probe → 503 preserved | `test_oauth2_initiate_bridge.py` | `store.get_for_binding` raises; heal NOT triggered; 503 propagated |

---

## Constraints

- All discovery goes through `run_mcp_auth_parallel_discovery_probe` — never a raw HTTP fetch.
- `_check_mcp_config_access` runs at `router.py:285` before `build_discovered_oauth2_initiate_response` is called. No change required.
- Failure → `_raise_client_error` → `HTTP_400`. Never surface a `500`.
- No secrets in logs. Reuse `_sanitize_url_for_log`.
- No `codemie-enterprise` changes. Enterprise functions consumed as-is.
- Surgical: touch only `_initiate.py` and `toolkit_service.py` (plus tests).
- Lazy import of `MCPToolkitService` inside `build_discovered_oauth2_initiate_response` to avoid import cycle.
