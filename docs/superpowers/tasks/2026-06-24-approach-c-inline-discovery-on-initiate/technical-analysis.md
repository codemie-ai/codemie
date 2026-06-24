# Technical Research

**Task**: mcp-auth oauth2 initiate discovery probe inline toolkit-service
**Generated**: 2026-06-24T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Approach C — Inline discovery safety net on the MCP OAuth2 *initiate* endpoint.

The goal is to make `POST /v1/mcp-auth/oauth2/initiate` in the `codemie` backend (not `codemie-enterprise`) never dead-end on a missing discovered-flow snapshot. When the endpoint cannot find a snapshot (either by-binding or by-id-expired), it should run the existing discovery probe inline and either return a fresh `discovered_flow_id` or fall back to the same `400` it returns today.

Key concerns:
1. Where exactly is `POST /v1/mcp-auth/oauth2/initiate` handled? Find the router file, handler function, and all code paths (by-binding miss, by-id-expired).
2. `MCPToolkitService._process_single_mcp_server` and the resolver chain at `toolkit_service.py:891` — understand how credential-less connection works, and whether a `skip_auth_resolution`-style flag or returning `False` from the resolver is the right way to do a credential-less connect for the discovery probe.
3. `run_mcp_auth_parallel_discovery_probe` — find its signature, where it lives, what it calls (SSRF checks: `allowed_private_networks`, `enforce_https`, `trust_policy_service`).
4. `_resolve_discovery_probe_runtime_config` — signature and usage.
5. `_run_coroutine_sync` — where it is, what it wraps.
6. There should be an `ensure_discovered_snapshot_for_server` helper (brief §6.1) — find it or confirm it does not exist yet.
7. There is NO `_mcp_server_from_config` builder (brief §6.2 confirms this). Understand what `MCPServerDetails` fields are required and where `MCPConfig` lives so we can design the forward mapping.
8. Find `_check_mcp_config_access` — confirm it runs before any discovery logic.
9. Find `_raise_client_error` — confirm the `400` fallback signature.
10. Locate the existing tests for the initiate endpoint so we know where to add the new failing tests.

Primary repos: `codemie` (all work here), `codemie-enterprise` (consumed as-is, no change).

Additional context files (read these first):
- `local/approach-c-inline-discovery-on-initiate.md` — validated implementation brief, verified 2026-06-24
- `local/mcp-discovered-oauth2-reauth-issue.md` — root cause analysis

---

## 2. Codebase Findings

### Existing Implementations

**Router and handler (Q1):**
- `src/codemie/enterprise/mcp_auth/router.py:278–324` — `def initiate_oauth2_enabled(...)` (sync FastAPI route for `POST /v1/mcp-auth/oauth2/initiate`)
  - Line 284: `mcp_config = _get_mcp_config_or_raise(payload.mcp_config_id)`
  - Line 285: `_check_mcp_config_access(user, mcp_config)` — **runs before any discovery**
  - Lines 286–300: recovery-flow branch (insufficient-scope, not C's concern)
  - Lines 301–308: **by-id branch** — `resolved_discovered_flow_id` → `build_discovered_oauth2_initiate_response(..., discovered_flow_id=resolved_discovered_flow_id)`
  - Lines 309–316: **by-binding branch** — `raw_mcp_auth_config is None` → `build_discovered_oauth2_initiate_response(..., discovered_flow_id=None)`
  - Lines 317–324: configured-OAuth2 branch (not C's concern)

**`build_discovered_oauth2_initiate_response` (the two `400` sites):**
- `src/codemie/enterprise/mcp_auth/_initiate.py:102–176`
  - Line 120–128: loads snapshot either by-id (`_deps._load_discovered_flow_snapshot_or_error(discovered_flow_id)`) or by-binding (`_load_discovered_flow_snapshot_for_binding_or_error(user_id, session_binding_hash, mcp_config_id)`)
  - Line 135: guards that `snapshot.status == "authentication_required"` and `snapshot.flow_config is not None`

**By-binding loader — the `400` intercept point for C (by-binding miss):**
- `src/codemie/enterprise/mcp_auth/_initiate.py:239–268` — `def _load_discovered_flow_snapshot_for_binding_or_error(...)`
  - Line 246: `store.get_for_binding(user_id, session_binding_hash, mcp_config_id)` — returns `None` when snapshot absent
  - Lines 263–267: **raises `400` when `snapshot is None`** — this is where by-binding miss heal goes

**By-id loader — the `400` intercept point for C (by-id expired):**
- `src/codemie/enterprise/mcp_auth/_oauth2_callback.py:181–216` — `def _load_discovered_flow_snapshot_or_error(discovered_flow_id, ...)`
  - Line 183: `store.get(discovered_flow_id)` — returns `None` when TTL expired
  - Lines 205–215: raises `400` (or `CallbackPageError`) when `snapshot is None` — by-id miss heal goes here

**`_check_mcp_config_access` (Q8):**
- `src/codemie/enterprise/mcp_auth/router.py:182–190`
- Confirmed: runs at `router.py:285`, **before** `build_discovered_oauth2_initiate_response` is called
- Raises `HTTP_403_FORBIDDEN` if user is not admin/maintainer, not owner, and config is not public

**`_raise_client_error` (Q9):**
- `src/codemie/enterprise/mcp_auth/_common.py:69–70`
- Signature: `def _raise_client_error(message: str, details: str, *, code: int = status.HTTP_400_BAD_REQUEST) -> NoReturn`
- Raises `ExtendedHTTPException` with help text `"Review the MCP auth configuration."`
- Default is `HTTP_400_BAD_REQUEST`. Confirmed: `503`s nearby are unrelated Redis/enterprise-package raise sites.

**`_process_single_mcp_server` and resolver chain (Q2):**
- `src/codemie/service/mcp/toolkit_service.py:677–753` — `@classmethod def _process_single_mcp_server(...)`
  - Called by `_process_single_server_for_tools` (line 335), which catches `MCPToolLoadException` and calls `_build_discovery_candidate_from_challenge`
  - Internally calls `_prepare_server_config` then `toolkit_service.get_toolkit(...).get_tools()`
- Resolver chain at `toolkit_service.py:891–896`:
  ```python
  for resolver in cls._auth_resolvers:
      if resolver.can_handle(server_config):
          handled = resolver.resolve(server_config, user_id, execution_context)
          if handled is False:
              continue  # no creds → proceeds to connect → server returns 401
          return         # creds injected
  ```
  - No `skip_auth_resolution` flag exists today; must be added for C-ii strategy
  - `server_config.auth_config is not None` guard at line 881: if `auth_config` is set and empty, raises `MCPAuthenticationRequiredException`. If `auth_config is None`, resolvers run; discovered resolver is allowed to `return False` (no credential → credential-less connect)

**`run_mcp_auth_parallel_discovery_probe` (Q3):**
- `src/codemie/enterprise/mcp_auth/_discovery.py:54–97`
- Signature: `async def run_mcp_auth_parallel_discovery_probe(candidates, *, allowed_private_networks, trust_policy_service) -> list[Any]`
- Internally uses `probe_discovery_eligible_servers` from `codemie_enterprise.mcp_auth.discovery`
- Applies: `allowed_private_networks`, `enforce_https` (from `mcp_auth_config`), `discovery_timeout_seconds` per phase
- `trust_policy_service` gates which authorization server domains are trusted

**`_resolve_discovery_probe_runtime_config` (Q4):**
- `src/codemie/service/mcp/toolkit_service.py:408–427`
- Signature: `@staticmethod def _resolve_discovery_probe_runtime_config() -> tuple[tuple[str, ...], Any]`
- Reads `allowed_private_networks` and builds `trust_policy_service` **synchronously** (DB-backed, must resolve on caller's loop before the async bridge)
- Returns `(allowed_private_networks, trust_policy_service)`

**`_run_coroutine_sync` (Q5):**
- `src/codemie/service/mcp/toolkit_service.py:429–440`
- Signature: `@staticmethod def _run_coroutine_sync(coroutine: Any) -> Any`
- If already inside a running loop: submits `asyncio.run(coroutine)` to a `ThreadPoolExecutor` worker
- If not inside a loop: calls `asyncio.run(coroutine)` directly
- Used by `_run_discovery_probe_and_collect_failures` to bridge async probe from the sync initiate endpoint

**`ensure_discovered_snapshot_for_server` (Q6):**
- **Does NOT exist.** Confirmed absent from `toolkit_service.py` and all `src/codemie` modules.
- Must be introduced as part of Approach C.

**`_mcp_server_from_config` builder (Q7):**
- **Does NOT exist.** Confirmed absent.
- Forward mapping: `MCPConfig` → `MCPServerDetails` must be added.
- `MCPConfig.config` is `MCPServerConfigData` (DB model at `rest_api/models/mcp_config.py:97–156`)
- `MCPServerDetails` is the runtime model at `rest_api/models/assistant.py:149–178`; it holds `name`, `mcp_config_id`, and an inline `config: MCPServerConfig` (`service/mcp/models.py:135–218`)
- The two config types (`MCPServerConfigData` vs `MCPServerConfig`) share the same fields: `url`, `type`, `headers`, `env`, `auth_config`, `allow_issuer_prefix_match`. `MCPServerConfig` has additional runtime-only fields (`mcp_config_id`, `mcp_config_name`, `bucket_key`).
- Mapping strategy: `MCPServerDetails(name=mcp_config.name, mcp_config_id=mcp_config.id, config=MCPServerConfig(**mcp_config.config.model_dump()))` — straightforward field projection.

**`_run_discovery_probe_and_collect_failures` (called by the new helper):**
- `src/codemie/service/mcp/toolkit_service.py:367–406`
- Signature: `@classmethod def _run_discovery_probe_and_collect_failures(cls, *, discovery_candidates, user_id, session_binding_hash, workflow_execution_id) -> tuple[list[dict], list[dict]]`
- Returns `(auth_failures, discovery_warnings)`
- **Stores the snapshot as a side effect** inside `build_mcp_auth_discovered_auth_gate_payloads` → `_resolve_discovered_candidate_payload` → `discovered_flow_store.store(resolution.snapshot)` (at `_discovery.py:208`)

**`_build_discovery_candidate_from_challenge` (produces the candidate from the 401):**
- `src/codemie/service/mcp/toolkit_service.py:539–566`
- Signature: `@classmethod def _build_discovery_candidate_from_challenge(cls, mcp_server: MCPServerDetails, exc: MCPToolLoadException) -> dict | None`
- Guards: `server_config.auth_config is None` (no persisted auth config), `cause` is `httpx.HTTPStatusError`, `status == 401`, `WWW-Authenticate` header present
- Returns the candidate dict with `mcp_resource_url`, `www_authenticate_header`, `allow_issuer_prefix_match`

**`MCPExecutionContext`:**
- `src/codemie/service/mcp/models.py:32–91`
- Fields relevant to the helper: `user_id`, `session_binding_hash`, `workflow_execution_id`

**`access_control.resolve_catalog_config` (reverse mapping, for reference):**
- `src/codemie/service/mcp/access_control.py:157–185`
- Takes an `MCPServerDetails` with `mcp_config_id` and no inline `config`, fetches `MCPConfig`, and returns `mcp_server.model_copy(update={"config": MCPServerConfig(**entry.config.model_dump())})`
- This is the **reverse** direction (catalog → runtime detail). The forward mapping C needs is analogous.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| **API** | `router.py:278` — `initiate_oauth2_enabled` (sync FastAPI route) |
| **Service / Business Logic** | `_initiate.py` — `build_discovered_oauth2_initiate_response`, `_load_discovered_flow_snapshot_for_binding_or_error` |
| **Service / Toolkit** | `toolkit_service.py` — `MCPToolkitService._run_discovery_probe_and_collect_failures`, `_resolve_discovery_probe_runtime_config`, `_run_coroutine_sync`, `_build_discovery_candidate_from_challenge`, `_process_single_mcp_server` |
| **Enterprise Bridge** | `_discovery.py` — `run_mcp_auth_parallel_discovery_probe`, `build_mcp_auth_discovered_auth_gate_payloads` |
| **External** | `codemie-enterprise` — `resolve_discovered_oauth2_flow`, `discovered_flow_store.store`, `probe_discovery_eligible_servers` (consumed as-is, no change) |
| **DB / Redis Persistence** | Snapshot stored in Redis via `codemie_enterprise.mcp_auth.discovered_flow.RedisDiscoveredFlowStore` |

### Integration Points

- `MCPToolkitService` (service layer) imported by the by-binding loader in `_initiate.py` for the new helper call
- `_resolve_discovery_probe_runtime_config` reads from DB-backed config tables (synchronous, pre-bridge)
- `build_static_trust_policy_service` and `read_mcp_auth_discovery_private_network_allowlist_config_sync` imported from `codemie.enterprise.mcp_auth.dependencies`
- `run_mcp_auth_parallel_discovery_probe` → `codemie_enterprise.mcp_auth.discovery.probe_discovery_eligible_servers` (network: SSRF-gated external calls)
- `build_mcp_auth_discovered_auth_gate_payloads` → `codemie_enterprise.mcp_auth.resolve_discovered_oauth2_flow` (network: DCR registration)
- `discovered_flow_store.store` → Redis (network write, TTL ≤ 900 s)
- `MCPConfig.find_by_id` — Postgres (called inside `_get_mcp_config_or_raise` at router layer, already done before C's code runs)

### Patterns and Conventions

- **Lazy enterprise import pattern**: enterprise modules are imported inside function bodies with `try/except ImportError` → raises `HTTP_503_SERVICE_UNAVAILABLE`. The new helper must follow this pattern when accessing `codemie_enterprise.mcp_auth`.
- **`_deps` module alias**: `from . import dependencies as _deps` in `_initiate.py` for testability — helpers are patched as `dependencies.X`, not `_initiate.X`.
- **`@classmethod` on `MCPToolkitService`**: `ensure_discovered_snapshot_for_server` must be a `@classmethod` to reuse the service's static helpers.
- **`_raise_client_error` as the 400 boundary**: all 400s in `_initiate.py` go through this helper; the fallback in C must call it with the same message string.
- **`_sanitize_url_for_log`**: used for all URL logging in `toolkit_service.py`; must be used in the new helper's log line.
- **Sync-first**: the initiate endpoint is a sync FastAPI handler; async probing is bridged via `_run_coroutine_sync`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

Relevant guides in `.ai-run/guides/`:

- `.ai-run/guides/integration/mcp-integration.md` — MCP configuration and tools patterns
- `.ai-run/guides/architecture/service-layer-patterns.md` — Service orchestration
- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router patterns
- `.ai-run/guides/development/error-handling.md` — Typed exceptions and `ExtendedHTTPException` usage
- `.ai-run/guides/testing/testing-patterns.md` — pytest policy and patterns
- `.ai-run/guides/testing/testing-api-patterns.md` — API test patterns (relevant for `test_oauth2_initiate_bridge.py`)

Also: `local/approach-c-inline-discovery-on-initiate.md` is the authoritative implementation brief (verified 2026-06-24) and supersedes any guide for this specific task.

### Architectural Decisions

1. **Enterprise package as runtime optional dependency**: all enterprise MCP auth code is behind `try/except ImportError`; the `codemie` package must degrade gracefully when `codemie-enterprise` is absent. This applies to the new helper.
2. **Sync↔async bridge**: the probe is async; the initiate endpoint is sync. The `_run_coroutine_sync` bridge was explicitly designed for this case. Must not be bypassed.
3. **SSRF must go through `run_mcp_auth_parallel_discovery_probe`**: C-iii (raw HTTP) is rejected precisely because it would bypass these controls. C-ii uses the same probe path.
4. **Session binding hash = `sha256(bearer_token)`**: the hash used in `_initiate.py` (`_get_authenticated_bearer_token_hash`) and the hash stored in the snapshot by the probe (`_get_current_session_binding_hash` in `toolkit_service.py`) must be the same — and they are (both `sha256(auth_token)`), so a snapshot stored by the inline probe will be found by the by-binding lookup.
5. **Approach A has landed on `EPMCDME-13049`** (not yet merged to `main`): `resolver.py:320–322` now invalidates the dead credential and returns `False`. This means the resolver no longer raises before connecting for the dead-credential case. However C-ii is still recommended because it is A-independent (see brief §10).

### Derived Conventions

- `_load_discovered_flow_snapshot_for_binding_or_error` wraps `store.get_for_binding` in a try/except that converts `ExtendedHTTPException` (Redis unavailable) to a `400` — matching the existing error handling pattern. The new heal logic must be inserted **between** the `get_for_binding` call and the `snapshot is None` raise at line 263.
- The `_deps` alias and the note `# tests patch helpers as dependencies.X` at `_initiate.py:43–44` mean the new helper call in `_initiate.py` should access `MCPToolkitService` via a direct import (not through `_deps`), and any testable sub-function should be patchable by module path.

---

## 4. Testing Landscape

### Existing Coverage

**Primary test file for the initiate endpoint:**
- `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py` (30.8 KB)
  - Tests the router (`enabled_router`) and `build_*` functions in `dependencies.py` / `_initiate.py`
  - Covers: by-id branch, by-binding branch (`discovered_flow_id=None`), recovery flow, access control (403, 404), session-binding / auth-token edge cases, redirect URI construction, localhost warning
  - Uses `monkeypatch` on `mcp_auth_router` module attributes (`build_discovered_oauth2_initiate_response`, `build_oauth2_initiate_response`, `build_recovery_oauth2_initiate_response`)
  - Uses `app.dependency_overrides[router_authenticate]` for auth bypass
  - Key test: `test_initiate_route_uses_discovered_binding_fallback_for_no_auth_config` — exercises the by-binding path, currently monkeypatches the whole builder; the new tests will need to patch at a lower level (or exercise inline discovery directly)

**Discovery probe tests:**
- `tests/enterprise/mcp_auth/test_discovery_probe_bridge.py` (16.2 KB) — unit tests for `run_mcp_auth_parallel_discovery_probe` and `build_mcp_auth_discovered_auth_gate_payloads`

**Toolkit service auth resolver tests:**
- `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py` (95.3 KB) — extensive tests for resolver chain, `_process_single_mcp_server`, `_build_discovery_candidate_from_challenge`

### Testing Framework and Patterns

- **pytest** with `monkeypatch`, `TestClient` (FastAPI), `SimpleNamespace` for faking enterprise types
- **Pattern**: enterprise package is stubbed via `monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", SimpleNamespace(...))` for unit isolation
- **Pattern**: `_deps` module attributes (`_pkce_store`, `_redis_encryption`, etc.) patched directly on the module object
- **Pattern**: router-level tests monkeypatch the builder functions by name on the `mcp_auth_router` module
- **No `asyncio` marker** on route tests (sync handler); `@pytest.mark.asyncio` only on async probe tests

### Coverage Gaps

The following areas will need new tests (none exist yet):
1. **`ensure_discovered_snapshot_for_server`**: no test for this helper (it does not exist yet)
   - Happy path: server returns 401 + WWW-Authenticate → candidate built → probe run → snapshot stored → `discovered_flow_id` returned
   - No-auth path: server returns tools (no 401) → returns `None`
   - No-challenge path: server returns 401 without WWW-Authenticate → returns `None`
   - SSRF pass-through: asserts `run_mcp_auth_parallel_discovery_probe` is called (not a raw HTTP call)
2. **By-binding miss → heal in `_load_discovered_flow_snapshot_for_binding_or_error`**: `POST /v1/mcp-auth/oauth2/initiate` with `mcp_config_id` only, no snapshot present → inline discovery runs → `200` with `auth_url`
3. **By-id miss (expired) → heal in `_load_discovered_flow_snapshot_or_error`**: `?discovered_flow_id=<expired>` → inline discovery runs → `200` with fresh flow id
4. **Discovery genuinely fails** (server unreachable / no challenge) → unchanged `400` (not `500`)
5. **Authorization ordering**: user without config access → `403` before any discovery runs
6. **`_mcp_server_from_config` builder**: unit test verifying field mapping is correct

---

## 5. Configuration and Environment

### Environment Variables

Relevant to the inline discovery path (resolved by `_resolve_discovery_probe_runtime_config`):
- `MCP_AUTH_ENABLED` — feature flag; `run_mcp_auth_parallel_discovery_probe` returns `[]` if false
- `MCP_AUTH_DISCOVERY_CONCURRENCY_LIMIT` (default 5) — controls concurrency inside the probe
- `MCP_AUTH_ENFORCE_HTTPS` — passed to AS metadata discovery kwargs in `run_mcp_auth_parallel_discovery_probe`
- `CALLBACK_API_BASE_URL` — used by `_prepare_discovered_flow_resolution_config` to build redirect URI and client metadata URL

Config fields from `mcp_auth_config` (read at probe time via `_mcp_auth_service.config`):
- `discovery_probe_overall_timeout_seconds` — overall timeout for `probe_discovery_eligible_servers`
- `resource_metadata_discovery_timeout_seconds` — per-phase PRM timeout
- `as_metadata_discovery_timeout_seconds` — per-phase AS discovery timeout
- `dcr_registration_timeout_seconds` — DCR timeout
- `enforce_https` — per `MCPAuthServiceConfig`
- `allow_local_client_metadata_document_url` — flag for DCR

### Configuration Files

- `src/codemie/configs/config.py` — `Config.MCP_AUTH_DISCOVERY_CONCURRENCY_LIMIT`, `MCP_AUTH_ENFORCE_HTTPS`, `MCP_AUTH_ENABLED`
- `src/codemie/enterprise/mcp_auth/dependencies.py` — `_mcp_auth_service`, `_mcp_auth_discovery_cache`, `_mcp_auth_dcr_credentials_cache`, `build_static_trust_policy_service`, `read_mcp_auth_discovery_private_network_allowlist_config_sync`, `read_mcp_auth_trusted_as_domains_config_sync`

### Feature Flags and Deployment Concerns

- `MCP_AUTH_ENABLED` — must be true for any inline discovery to run (checked inside `run_mcp_auth_parallel_discovery_probe`)
- **DCR side effect**: the inline probe may register a new OAuth2 client with the external AS. This is a network write during a user-facing POST. Acceptable per brief §9 (idempotent via `dcr_credentials_cache`).
- **Latency**: connect + PRM discovery + AS metadata + possibly DCR can take several seconds. Must be bounded by `discovery_probe_overall_timeout_seconds`.
- **No new secrets in logs**: `_sanitize_url_for_log` must be used; `WWW-Authenticate` content must not be logged.

---

## 6. Risk Indicators

- **`ensure_discovered_snapshot_for_server` does not exist** — greenfield addition in `toolkit_service.py`; no existing tests, no precedent for this call pattern from the auth endpoint layer.
- **`_mcp_server_from_config` forward mapping does not exist** — must be added; `MCPServerConfigData` (DB model) and `MCPServerConfig` (runtime model) share fields but are different types; mapping must be exact to avoid `allow_issuer_prefix_match` defaulting to `False` when DB value is `True`.
- **Sync↔async bridging in a sync FastAPI handler**: `_run_coroutine_sync` uses `ThreadPoolExecutor` when inside a running loop. The initiate endpoint is a sync FastAPI handler (runs in a threadpool by default), so `asyncio.get_running_loop()` may or may not raise `RuntimeError` depending on whether FastAPI wraps it. Test must verify the bridge works correctly in the test-client context.
- **C-ii requires a credential-less connect mode**: `_process_single_mcp_server` currently always calls `_resolve_auth_config` (the resolver chain). A `skip_auth_resolution` flag or equivalent guard must be added cleanly without breaking existing callers. No test currently covers a forced credential-less connect.
- **`_load_discovered_flow_snapshot_for_binding_or_error` exception masking**: the function catches `ExtendedHTTPException` from `get_for_binding` and calls `_raise_client_error` with a **different message** (line 252–255). The heal logic must be inserted after the raw `get_for_binding` call **inside the try block**, before the exception re-raise — or extracted to separate the store access from the error transformation.
- **No test for by-id expired → heal path** in the callback module; `_load_discovered_flow_snapshot_or_error` in `_oauth2_callback.py:181` is tested indirectly through callback-flow tests but not for the heal scenario.
- **Approach A not yet merged to `main`**: on `main`, `resolver.py:320` still raises `SESSION_EXPIRED` before connecting. C-ii must not depend on A being present to obtain the 401 challenge (the point of C-ii being A-independent).
- **`_INVALID_OAUTH2_CONFIG_MESSAGE` constant** is used in the by-binding 400 (`_initiate.py:265`) but `_INVALID_MCP_AUTH_CONFIG_MESSAGE` is used in the by-binding ExtendedHTTPException catch (`_initiate.py:253`). The heal fallback must preserve the exact message for UI compatibility.
- **No authentication against the MCP server during discovery**: the credential-less connect intentionally sends no token. If the MCP server is currently rate-limiting unauthenticated requests, the discovery probe would fail and C correctly falls back to `400` — acceptable per design.

---

## 7. Summary for Complexity Assessment

The task requires adding an inline discovery safety net to `POST /v1/mcp-auth/oauth2/initiate` in the `codemie` backend. It touches three architectural layers: the API/router layer (no change needed; routing already delegates correctly), the service/business-logic layer (`_initiate.py` — `_load_discovered_flow_snapshot_for_binding_or_error` and optionally `_load_discovered_flow_snapshot_or_error`), and the toolkit service layer (`toolkit_service.py` — a new `@classmethod ensure_discovered_snapshot_for_server`). Additionally, a small new builder function `_mcp_server_from_config` must be introduced to map `MCPConfig` (DB model) to `MCPServerDetails` (runtime model). The estimated file-change surface is 3–4 files: `toolkit_service.py` (new method + optional credential-less connect mode), `_initiate.py` (by-binding heal), optionally `_oauth2_callback.py` (by-id heal), and a new builder location (likely alongside `_initiate.py` or `toolkit_service.py`). Total new code is moderate: ~80–120 lines of production code + ~150–200 lines of tests.

The task introduces two genuinely novel patterns: (1) a credential-less MCP server connect initiated from the auth endpoint rather than the toolkit-loading path, requiring a new `skip_auth_resolution` mode in `_process_single_mcp_server` or the resolver chain; and (2) a sync-initiated async discovery probe called from inside a sync endpoint handler using the existing `_run_coroutine_sync` bridge. Both patterns have precedents in the codebase (the bridge already exists; the resolver's `return False` path already works for the no-credential case) but neither has been exercised from the auth endpoint layer before. The most complex correctness concern is ensuring the same `session_binding_hash` is used in both the probe's snapshot write (inside `build_mcp_auth_discovered_auth_gate_payloads`) and the by-binding lookup — verified to be the same `sha256(auth_token)` computation.

Test coverage for the affected area is strong at the unit level (`test_toolkit_service_auth_resolver.py`, `test_discovery_probe_bridge.py`) and at the endpoint level (`test_oauth2_initiate_bridge.py`), but the specific by-binding-miss-and-heal scenario is completely untested (the helper does not yet exist). The new tests must use the established `SimpleNamespace` + `monkeypatch` pattern for enterprise isolation. Key risk factors for complexity scoring: the credential-less connect mode is a new extension to `_process_single_mcp_server` with no current flag/mode support; the sync↔async bridge has subtle behavior differences depending on whether FastAPI's threadpool wraps the handler with a running loop; and `_load_discovered_flow_snapshot_for_binding_or_error`'s exception-catch structure requires care to insert the heal without disrupting the Redis-unavailable → 503 path.
