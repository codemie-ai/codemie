# EPMCDME-16319 CIMD client_id Root Path Implementation Plan

> **For agentic workers:** the calling flow chooses the execution mode. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The advertised CIMD client_id (discovery config and served document) is `<base without trailing slash><API root path>/oauth/client-metadata.json`, built in one place.

**Architecture:** A single `build_client_metadata_document_url()` in `codemie.enterprise.mcp_auth._uri` replaces the two duplicated f-strings. `_uri.py` calls it directly. `_discovery.py` calls it through the `dependencies` facade. `router.py` drops its duplicate path constant.

**Tech Stack:** Python, FastAPI, pytest (`monkeypatch`, `TestClient`).

**Spec:** `docs/superpowers/tasks/2026-10-23-epmcdme-16319-cimd-client-id-root-path/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

These are the spec's negative constraints. Each one names the task that honors it, and no task violates any of them.

- The route path `/oauth/client-metadata.json` and the OpenAPI baseline do not change. No task edits a route decorator. Task 2 changes only where the constant is imported from.
- `_build_callback_uri` and redirect_uri do not change, and that includes their missing trailing-slash strip. Task 1 builds the URL with `rstrip('/')` and does not use `_build_callback_uri`. No test asserts a redirect_uri for a base that ends in `/`.
- The URL is evaluated at call time, not at import time in `_constants.py`. Task 1's helper reads config on every call.
- Nothing changes in codemie-enterprise (including its HTTPS validation), deploy-templates, ingress, guides or docs. The plan adds no setting to turn off CIMD and no migration of Redis snapshots. No task touches any of these.
- The existing root-path-free expectations stay byte-identical: `test_client_metadata_bridge.py:28-31` and `test_toolkit_service_auth_resolver.py:1287`. Task 1 appends tests and does not edit those lines.

## Review Focus

- `API_ROOT_PATH` without a leading slash or with a trailing slash (`code-assistant-api/`) still gives `/code-assistant-api/oauth/...`. The helper must call `get_api_root_path()` and never read raw `config.API_ROOT_PATH`. Pinned by the parametrized discovery case in Task 1.
- A root path combined with a base that ends in `/` gives no `//`. Pinned by the parametrized discovery case in Task 1.
- The discovery callsite reaches the helper through `deps`, so a missing re-export fails only at runtime with `AttributeError`. Pinned by the seam test, which calls the real `_prepare_discovered_flow_resolution_config`.
- Config that changes after import is picked up. Every Task 1 test monkeypatches config at test time.

---

### Task 1: Root-path-aware client metadata document URL builder

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_uri.py:121-149`
- Modify: `src/codemie/enterprise/mcp_auth/dependencies.py:120-136`
- Modify: `src/codemie/enterprise/mcp_auth/_discovery.py:34,134`
- Test: `tests/enterprise/mcp_auth/test_client_metadata_bridge.py` (the spec left the file to the builder; the seam test goes here)

**Interfaces:**
- Produces: `build_client_metadata_document_url() -> str` in `_uri.py`, re-exported as `dependencies.build_client_metadata_document_url`.

**Test-first:** yes. With `API_ROOT_PATH="/code-assistant-api"`, neither the served document's `client_id` nor the discovery config's `client_metadata_document_url` contains `/code-assistant-api` today.

- [ ] **Step 1: Append the failing tests.** They reuse the autouse `callback_base_url` fixture, `_build_cimd_client`, `CLIENT_METADATA_PATH`, `CALLBACK_BASE_URL` and `EXPECTED_CLIENT_ID`. Add `from types import SimpleNamespace` and keep the file's typed signatures (`monkeypatch: pytest.MonkeyPatch`, `-> None`).

```python
ROOT_PATH = "/code-assistant-api"
EXPECTED_ROOTED_CLIENT_ID = f"{CALLBACK_BASE_URL}{ROOT_PATH}{CLIENT_METADATA_PATH}"

def test_served_document_client_id_includes_api_root_path_and_shares_redirect_uri_prefix(monkeypatch):
    monkeypatch.setattr(mcp_auth_dependencies.config, "API_ROOT_PATH", ROOT_PATH)
    body = _build_cimd_client(monkeypatch, enabled=True).get(CLIENT_METADATA_PATH).json()
    assert body["client_id"] == EXPECTED_ROOTED_CLIENT_ID
    assert body["redirect_uris"] == [f"{CALLBACK_BASE_URL}{ROOT_PATH}/v1/mcp-auth/oauth2/callback"]
    prefix = f"{CALLBACK_BASE_URL}{ROOT_PATH}/"
    assert body["client_id"].startswith(prefix) and body["redirect_uris"][0].startswith(prefix)

def test_served_document_client_id_without_root_path_strips_trailing_slash_base(monkeypatch):
    monkeypatch.setattr(mcp_auth_dependencies.config, "API_ROOT_PATH", "")
    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", f"{CALLBACK_BASE_URL}/")
    body = _build_cimd_client(monkeypatch, enabled=True).get(CLIENT_METADATA_PATH).json()
    assert body["client_id"] == EXPECTED_CLIENT_ID  # redirect_uri not asserted: its strip is a non-goal

@pytest.mark.parametrize(("base_url", "root_path", "expected"), [
    (CALLBACK_BASE_URL, ROOT_PATH, EXPECTED_ROOTED_CLIENT_ID),
    (f"{CALLBACK_BASE_URL}/", ROOT_PATH, EXPECTED_ROOTED_CLIENT_ID),
    (CALLBACK_BASE_URL, "code-assistant-api/", EXPECTED_ROOTED_CLIENT_ID),
    (f"{CALLBACK_BASE_URL}/", "", EXPECTED_CLIENT_ID),
])
def test_discovered_flow_config_client_metadata_document_url_includes_api_root_path(monkeypatch, base_url, root_path, expected):
    from codemie.enterprise.mcp_auth import _discovery
    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", base_url)
    monkeypatch.setattr(mcp_auth_dependencies.config, "API_ROOT_PATH", root_path)
    monkeypatch.setattr(mcp_auth_dependencies, "_mcp_auth_service",
        SimpleNamespace(config=SimpleNamespace(allow_local_client_metadata_document_url=False)))
    resolved = _discovery._prepare_discovered_flow_resolution_config(allowed_private_networks=())
    assert resolved.client_metadata_document_url == expected
```

- [ ] **Step 2: Run the tests and confirm they fail.** Run `poetry run pytest tests/enterprise/mcp_auth/test_client_metadata_bridge.py -v`. Expected: the root-path served-document test and the three discovery cases with a root path FAIL because `/code-assistant-api` is missing. The two cases with an empty root path already pass; they guard AC3.

- [ ] **Step 3: Implement the helper and wire it into both callsites.**
  - In `_uri.py`, add this directly above `build_client_metadata_document_response`:
    ```python
    def build_client_metadata_document_url() -> str:
        """Strips the base's trailing slash, unlike _build_callback_uri, so a '/'-terminated base never yields '//'."""
        return f"{config.CALLBACK_API_BASE_URL.rstrip('/')}{get_api_root_path()}{_CLIENT_METADATA_DOCUMENT_PATH}"
    ```
  - `_uri.py:125`: replace the f-string with a direct call to `build_client_metadata_document_url()`, the same way line 124 calls `build_redirect_uri()`.
  - `dependencies.py:120-136`: add `build_client_metadata_document_url,` in alphabetical order, between `build_client_metadata_document_response` and `build_redirect_uri`.
  - `_discovery.py:134`: replace the f-string with `deps.build_client_metadata_document_url()`. Delete the import at `_discovery.py:34`, which is now unused, and keep the `config` import.

- [ ] **Step 4: Run the tests and confirm they pass.** Run `poetry run pytest tests/enterprise/mcp_auth/test_client_metadata_bridge.py tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py -v`. Expected: all PASS, including the unchanged AC6 test (`:210-234`), the AC7 test (`:196-207`) and the test at `:1287`. Then run `grep -rn "CALLBACK_API_BASE_URL.rstrip" src/codemie/enterprise/mcp_auth`. Expected: one hit, inside `build_client_metadata_document_url` (AC4).

### Task 2: Single definition of the CIMD path constant

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/router.py:44-46,113`

**Test-first:** no. This only removes a duplicate. The existing route tests in `test_client_metadata_bridge.py` already pin the path (200 document, both 503s, 404 under `/v1/mcp-auth`, AC6, AC7).

- [ ] **Step 1: Delete the duplicate and verify.** Delete `router.py:113`. Next to the existing `codemie.enterprise.mcp_auth._diagnostics` import, add `from codemie.enterprise.mcp_auth._constants import _CLIENT_METADATA_DOCUMENT_PATH`; the spec says to import from `_constants`, not from the facade. Run `poetry run pytest tests/enterprise/mcp_auth/test_client_metadata_bridge.py -v`. Expected: all PASS. Then run `grep -rn "_CLIENT_METADATA_DOCUMENT_PATH = " src/`. Expected: only `_constants.py:40` (AC4).
