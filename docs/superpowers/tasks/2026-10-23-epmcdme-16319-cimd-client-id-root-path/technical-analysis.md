# Technical Research

**Task**: mcp-auth cimd oauth client-metadata api-root-path
**Generated**: 2026-10-23
**Research path**: codegraph

---

## 1. Original Context

Implement Jira EPMCDME-16319 (Bug) "MCP auth: CIMD client_id URL omits API root path (/code-assistant-api)" in the codemie repo (/home/taras_spashchenko/EPAM/cm/codemie). The full Jira description is the source of requirements: problem, preconditions, affected code with line refs, implementation steps 1-7, tests, acceptance criteria AC1-AC7, notes and out of scope. Read it here: /tmp/claude-1000/-home-taras-spashchenko-EPAM-cm-codemie/f1d74add-9585-4f01-8447-0e243614aebf/scratchpad/desc_16319.txt (the file is Jira wiki markup and matches the stored ticket). In short: CodeMie builds the CIMD client_metadata_document_url / client_id as CALLBACK_API_BASE_URL.rstrip('/') + "/oauth/client-metadata.json" in two places (src/codemie/enterprise/mcp_auth/_uri.py build_client_metadata_document_response, and _discovery.py _prepare_discovered_flow_resolution_config). Neither includes get_api_root_path(). Fix it with one helper build_client_metadata_document_url() in _uri.py that keeps the rstrip, re-export it in dependencies.py, call it via deps in _discovery.py, and remove the duplicate _CLIENT_METADATA_DOCUMENT_PATH in router.py (import it from _constants.py). The route path must not change. No codemie-enterprise change. The work is codemie repo only and does not touch deploy-templates (that is EPMCDME-16320).

---

## 2. Codebase Findings

### Existing Implementations
All line refs in the ticket match current source.
- `src/codemie/enterprise/mcp_auth/_uri.py`
  - `_build_callback_uri` (57-72): `f"{config.CALLBACK_API_BASE_URL}{get_api_root_path()}{path}"`, **no rstrip**. It checks the hostname is present and that the scheme is HTTPS (or the host is localhost/127.0.0.1/::1). On failure it calls `_raise_client_error`.
  - `build_redirect_uri` (75-80) wraps it with `_OAUTH2_CALLBACK_PATH`. `build_saml_acs_url` (105-111) wraps it with `_SAML_ACS_PATH`.
  - `build_client_metadata_document_response` (121-149): calls `build_redirect_uri()` as a plain module-local call, not through deps (124). Line 125 has `client_metadata_document_url = f"{config.CALLBACK_API_BASE_URL.rstrip('/')}{_CLIENT_METADATA_DOCUMENT_PATH}"`. A `ValueError` from enterprise `build_client_metadata_document` becomes a 400 `ExtendedHTTPException` (137-143).
  - The module already imports `get_api_root_path` (30) and `_CLIENT_METADATA_DOCUMENT_PATH` (36).
- `src/codemie/enterprise/mcp_auth/_discovery.py`
  - `_deps()` (44-45) returns `sys.modules["codemie.enterprise.mcp_auth.dependencies"]`.
  - `_prepare_discovered_flow_resolution_config` (128-140): `deps.build_redirect_uri()` (133). Line 134 builds the same URL as `_uri.py:125`. Imports: `config` (23) and `_CLIENT_METADATA_DOCUMENT_PATH` from `._constants` (34).
- `src/codemie/enterprise/mcp_auth/dependencies.py`: a re-export facade.
  - Re-exports `_CLIENT_METADATA_DOCUMENT_PATH` from `_constants` (95).
  - The `from ._uri import (...)  # noqa: E402, F401` block (120-136) contains `build_client_metadata_document_response` (131), `build_redirect_uri` (132), `build_saml_acs_url` (133) and `ensure_client_metadata_document_available` (135).
- `src/codemie/enterprise/mcp_auth/_constants.py`: `_CLIENT_METADATA_DOCUMENT_PATH = "/oauth/client-metadata.json"` (40) and `_OAUTH2_CALLBACK_PATH` (39).
- `src/codemie/enterprise/mcp_auth/router.py`
  - Imports its helpers from `codemie.enterprise.mcp_auth.dependencies` (24-43), not from `_constants`.
  - Duplicate `_CLIENT_METADATA_DOCUMENT_PATH = "/oauth/client-metadata.json"` (113).
  - That constant is used by the disabled route on `cimd_router` (503, 136-142) and by the enabled route `client_metadata_document_enabled` (145-171).
  - The enabled route returns 503 if enterprise is unavailable. A query string gives 400 "Query strings are not allowed for this endpoint." (154-160). Any `ExtendedHTTPException` from the builder is replaced by a 400 with `_CLIENT_METADATA_CONFIG_ERROR_DETAILS` (165-171).
  - `cimd_router` and `enabled_cimd_router` have **no prefix** (64, 66). `get_cimd_router` (528-531) selects one by `is_mcp_auth_enabled()`.
- `src/codemie/core/utils.py:778-792` `get_api_root_path()`: returns `/<API_ROOT_PATH stripped of '/'>`, or `""` when unset or None. The value always has a single leading slash and no trailing slash.

### Architecture and Layers Affected
- **Enterprise bridge layer** (`codemie.enterprise.mcp_auth`): `_uri` (URL builders), `_discovery` (discovered-flow config), `dependencies` (facade that tests patch), `_constants`.
- **REST API layer**: `enterprise/mcp_auth/router.py`, the CIMD routes. `rest_api/main.py` registers the CIMD router after the MCP auth router; `test_client_metadata_bridge.py:237-247` asserts that order.

### Integration Points
- codemie-enterprise `codemie_enterprise.mcp_auth.discovery.client_metadata.build_client_metadata_document`. It is lazily imported (`_uri.py:116,128`), and it validates the URL (`ValueError` → 400).
- The discovered flow passes `client_metadata_document_url` and `allow_local_client_metadata_document_url` (taken from `deps._mcp_auth_service.config`) into the enterprise discovery. The resulting snapshot's `flow_config.client_id` is the advertised client_id (`tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py:1287`).

### Patterns and Conventions
- **Deps indirection**: the private modules `_discovery`, `_guards` and `_post_auth` call through `_deps()` so tests can monkeypatch attributes on `dependencies`. An example is `monkeypatch.setattr(dependencies, ...)` in `tests/codemie/service/security/test_logout_token_cleanup.py:46-49`. A helper reached via `deps.` must be in the `dependencies.py` re-export block.
- `_uri.py` calls its own helpers directly, without deps (line 124).
- Precedent for joining the root path: `_constants.py:42-43` evaluates `get_api_root_path() + "/v1/mcp-auth/..."` at **import time**, while `_build_callback_uri` evaluates it at **call time**.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/mcp-integration.md` covers only router registration and init timeouts. It has nothing on mcp_auth, CIMD or the API root path.
- `.ai-run/guides/testing/testing-patterns.md` has a "Seam Tests for Policy Helpers" rule: when a policy helper is extracted, each callsite needs its own test that asserts the value reaching the boundary.

### Architectural Decisions
No ADRs found. The `router.py` docstrings and constants show the CIMD route is deliberately mounted outside `/v1/mcp-auth`, and a test asserts it: `test_cimd_route_is_not_available_under_v1_mcp_auth_prefix`.

### Derived Conventions
- New or re-exported `_uri` symbols go in the alphabetised `from ._uri import (...)` block in `dependencies.py`.
- Error messages and paths live in `_constants.py`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/enterprise/mcp_auth/test_client_metadata_bridge.py`
  - Constants: `CALLBACK_BASE_URL="https://codemie.example.com"`, `EXPECTED_CLIENT_ID = base + "/oauth/client-metadata.json"`, `EXPECTED_REDIRECT_URI = base + "/v1/mcp-auth/oauth2/callback"` (28-31).
  - An autouse fixture monkeypatches `mcp_auth_dependencies.config.CALLBACK_API_BASE_URL`. This is the shared `codemie.configs.config` object (62-64).
  - Covers:
    - the 200 document body and headers (113-142)
    - the disabled 503 (145-157) and the enterprise-unavailable 503 (160-185)
    - 404 under `/v1/mcp-auth` (188-193)
    - **AC7**: the query-string 400 (196-207)
    - **AC6**: an `http://` base URL gives 400 with "Client metadata document configuration is invalid." (210-234)
    - the router registration order in main (237-247)
- `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py:1287`: an end-to-end discovery test asserting `flow_config.client_id == "https://codemie.example.com/oauth/client-metadata.json"`. This is the discovery-path seam.
- Other files in `tests/enterprise/mcp_auth/` were not inspected for `client_metadata_document_url`: `test_discovery_probe_bridge.py`, `test_oauth2_initiate_bridge.py` and 14 more.

### Testing Framework and Patterns
pytest with `monkeypatch` on `dependencies.config` and module attributes. Routes are tested with FastAPI `TestClient` on a throwaway `FastAPI()` that includes `get_cimd_router()` and `extended_http_exception_handler`.

### Coverage Gaps
- No test sets a non-empty `API_ROOT_PATH` for the CIMD URL or the redirect URI.
- No test uses a trailing-slash `CALLBACK_API_BASE_URL`.
- No test checks that client_id and redirect_uri share the same root path.
- Codegraph links no direct tests to `build_redirect_uri` or `_prepare_discovered_flow_resolution_config`. They are covered only indirectly, through the route and the toolkit resolver.

---

## 5. Configuration and Environment

### Environment Variables
- `API_ROOT_PATH` and `CALLBACK_API_BASE_URL`: `Config` in `src/codemie/configs/config.py`. Codegraph did not resolve the field lines.
  - Inference: the test env has an empty `API_ROOT_PATH`, because `EXPECTED_REDIRECT_URI` has no root path and passes today.
- `MCP_AUTH_ENABLED` and `MCP_AUTH_TMS_ENABLED`: read in `_guards.py:116-125`.
- `DISCOVERED_FLOW_TTL_SECONDS=900`: taken from the ticket, not verified in code.

### Configuration Files
`_constants.py` holds the paths. `allow_local_client_metadata_document_url` comes from the enterprise `_mcp_auth_service.config`.

### Feature Flags and Deployment Concerns
- The route set is gated by `is_mcp_auth_enabled()` and by the enterprise package being present.
- The ingress `rewrite-target` strips `/code-assistant-api` (ticket). The ingress or auth fix is EPMCDME-16320, out of scope.
- A glob for `**/openapi*baseline*` found no OpenAPI baseline file. The route path is unchanged, per the ticket.

---

## 6. Risk Indicators

- `router.py` gets its symbols from `dependencies`, which already re-exports `_CLIENT_METADATA_DOCUMENT_PATH` (`dependencies.py:95`). So ticket step 6 ("import from `_constants.py`") can be met by importing either from `_constants` directly or through the facade.
- `_discovery.py:133` resolves its helpers via `deps`. A helper missing from the `dependencies.py` re-export block fails at runtime with `AttributeError`, not at import.
- Trailing-slash asymmetry: `_build_callback_uri` (`_uri.py:58`) does not strip, so a base ending in `/` gives `//` in redirect_uri. The ticket limits the shared-prefix invariant to bases without a trailing slash.
- Two existing tests hard-code root-path-free URLs: `test_client_metadata_bridge.py:30-31` and `test_toolkit_service_auth_resolver.py:1287`. They stay valid only while the test env has an empty `API_ROOT_PATH`.
- Speculative: computing the root path at import time, as in the `_constants.py:42-43` style, would not see a runtime `monkeypatch` of `API_ROOT_PATH`. Call-time evaluation, as in `_build_callback_uri`, would.
- Operational, from the ticket: discovered-flow snapshots in Redis keep the old client_id for up to 900 s. The fix only works once EPMCDME-16320 is deployed with it.
- The guides have no mcp_auth/CIMD documentation, and codegraph did not link the mcp_auth tests to their source.

---

## 7. Summary for Complexity Assessment

The change sits in one package, the enterprise bridge `codemie.enterprise.mcp_auth`, and touches the REST layer only through `router.py`'s duplicate path constant. The ticket names four source files: `_uri.py`, `_discovery.py`, `dependencies.py` and `router.py`. All of its line refs match current code. The two duplicated builders are single one-line f-strings: `_uri.py:125` and `_discovery.py:134`. `_uri.py` already imports both `get_api_root_path` and `_CLIENT_METADATA_DOCUMENT_PATH`. No schema, migration, configuration, codemie-enterprise or route-path change is involved, and nothing here is new: the deps-facade re-export and the root-path join both have direct precedents (`dependencies.py:120-136`, `_uri.py:57-58`).

Test posture is good for the behaviours AC6 and AC7 must preserve. `test_client_metadata_bridge.py` already covers the HTTPS/config 400 and the query-string 400 through the real route, and `test_toolkit_service_auth_resolver.py:1287` covers the discovery client_id. The gaps are exactly the bug's scenarios: no test sets a non-empty `API_ROOT_PATH`, none uses a trailing-slash base URL, and none checks that client_id and redirect_uri share the same prefix. The testing guide's seam-test rule means each of the two callsites (the served document and the discovery config) needs its own assertion.

The main risks are mechanical. A helper that is not re-exported breaks `deps.` resolution at runtime. Two existing tests hard-code root-path-free URLs. `router.py` can drop its duplicate constant by importing either from `_constants` directly or through the facade. Outside the code, the fix depends on EPMCDME-16320 shipping with it and on 15-minute Redis snapshots expiring.

---

## 8. External References

- `/tmp/claude-1000/-home-taras-spashchenko-EPAM-cm-codemie/f1d74add-9585-4f01-8447-0e243614aebf/scratchpad/desc_16319.txt`: **resolved** (Jira wiki markup, 110 lines).
  - **Expected**: with `API_ROOT_PATH=/code-assistant-api` and `CALLBACK_API_BASE_URL=https://codemie.lab.epam.com`, client_id must be `https://codemie.lab.epam.com/code-assistant-api/oauth/client-metadata.json`. Today it is `.../oauth/client-metadata.json`, which gets a 302 to `/oauth2/start`, and the Gateway reports "Response exceeded size limit of 5120 bytes".
  - **Implementation steps**:
    1. Add a single `build_client_metadata_document_url()` in `_uri.py`, returning `CALLBACK_API_BASE_URL` with the trailing slash stripped, plus `get_api_root_path()`, plus `_CLIENT_METADATA_DOCUMENT_PATH`.
    2. Keep `.rstrip('/')`. Do NOT build via `_build_callback_uri` (no strip → `//`, breaks AC3). HTTPS validation stays in enterprise `_validate_client_metadata_document_url`.
    3. Re-export it in `dependencies.py` next to `build_redirect_uri`.
    4. Replace both builders. In `_discovery.py`, call it via `deps`, as `deps.build_redirect_uri()` is called.
    5. Do NOT change the route path `/oauth/client-metadata.json`. The ingress strips the root via `rewrite-target /$1`, and the OpenAPI baseline needs no regen.
    6. Remove `router.py:113` and import it from `_constants.py`.
    7. No codemie-enterprise change.
  - **Tests** (`test_client_metadata_bridge.py` and related):
    - With `API_ROOT_PATH="/code-assistant-api"` and base `https://codemie.example.com`, the served doc's client_id is `https://codemie.example.com/code-assistant-api/oauth/client-metadata.json`.
    - The discovery `client_metadata_document_url` is the same URL.
    - A base `https://codemie.example.com/` gives a URL with no `//`.
    - Invariant: for a base without a trailing slash, client_id and redirect_uri share scheme, host and root-path prefix.
    - Expectations with an empty root path stay valid (e.g. `test_toolkit_service_auth_resolver.py:1287`).
  - **Acceptance criteria**:
    - **AC1**: the advertised client_id includes `/code-assistant-api`.
    - **AC2**: the served document's client_id is byte-equal to its URL.
    - **AC3**: with an empty root path, behaviour is unchanged, including a trailing-slash base.
    - **AC4**: the URL is built in exactly one place, and the path constant is defined in exactly one place.
    - **AC5**: tests cover AC1-3, 6 and 7; the mcp_auth suite and ruff pass.
    - **AC6**: a base that is neither HTTPS nor localhost still gives 400 with the client metadata config error.
    - **AC7**: a query string still gives 400 "Query strings are not allowed for this endpoint."
  - **Out of scope**: a setting to turn CIMD off (force DCR), which needs a codemie-enterprise change.
