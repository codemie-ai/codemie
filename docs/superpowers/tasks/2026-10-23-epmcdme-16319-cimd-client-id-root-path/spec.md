# EPMCDME-16319: CIMD client_id URL includes the API root path

## Intent

CodeMie advertises its OAuth CIMD client_id as `CALLBACK_API_BASE_URL + /oauth/client-metadata.json`, without `get_api_root_path()`. When `API_ROOT_PATH=/code-assistant-api` (prod), that URL goes to the UI host, not the backend. The redirect_uri (`_uri.py:57-80`) does include the root path, so the two values disagree, and MCP Gateway logins fail for every user. Fix: build the client_id with the root path, in one place. This is half of the incident fix. The ingress half is EPMCDME-16320.

## Goals

- The advertised client_id and the client_id inside the served document both equal `<base without trailing slash><root path>/oauth/client-metadata.json`.
- One builder makes that URL, and one constant defines its path.

## Non-goals

- No change to the route path `/oauth/client-metadata.json` (`router.py:136-171`). The ingress strips the root path. The OpenAPI baseline (`tests/codemie/rest_api/router_guards/baselines/openapi.json`) stays as it is.
- No change to `_build_callback_uri` / redirect_uri. That includes its missing trailing-slash strip.
- No change to codemie-enterprise (including its HTTPS validation), deploy-templates or ingress (EPMCDME-16320).
- No setting to turn CIMD off or force DCR.
- No migration or invalidation of discovered-flow snapshots already in Redis (they expire within 900 s).
- No guide or documentation updates.

## Approaches

1. **Chosen:** add a new `_uri.py` helper that keeps the existing `rstrip('/')`, called from both callsites. This is the ticket's prescription.
2. Reuse `_build_callback_uri`. Rejected: it does not strip the trailing slash, so a base ending in `/` yields `//` (breaks AC3).
3. Compute the URL at import time in `_constants.py`, like `_constants.py:42-43`. Rejected: it would miss config read at call time and would differ from how the redirect URI is built.

## Design (decided)

- New `build_client_metadata_document_url() -> str` in `_uri.py`. It takes no arguments and reads `config.CALLBACK_API_BASE_URL.rstrip('/')`, `get_api_root_path()` and `_CLIENT_METADATA_DOCUMENT_PATH` when called.
- `build_client_metadata_document_response` (`_uri.py:125`) calls it directly, matching how it already calls `build_redirect_uri` in that module.
- Re-export it in the alphabetised `from ._uri import (...)` block of `dependencies.py` (120-136).
- `_prepare_discovered_flow_resolution_config` (`_discovery.py:134`) calls `deps.build_client_metadata_document_url()`. Remove the `_CLIENT_METADATA_DOCUMENT_PATH` import that becomes unused (`_discovery.py:34`). `config` stays imported because it is still used.
- Delete `router.py:113`. `router.py` imports `_CLIENT_METADATA_DOCUMENT_PATH` from `._constants`.

## Acceptance criteria

- AC1: with `API_ROOT_PATH=/code-assistant-api`, the discovery config's `client_metadata_document_url` is `https://<host>/code-assistant-api/oauth/client-metadata.json`.
- AC2: with the same config, the served document's `client_id` is byte-equal to that URL.
- AC3: with an empty root path, the URL is unchanged, including for a base URL that ends in `/` (no `//`).
- AC4: the URL f-string exists only in `build_client_metadata_document_url`. `_CLIENT_METADATA_DOCUMENT_PATH` is assigned only in `_constants.py`.
- AC5: tests cover AC1-AC3, AC6, AC7 and the invariant. The mcp_auth suites, `test_toolkit_service_auth_resolver.py` and ruff pass.
- AC6: a base URL that is neither HTTPS nor localhost still returns 400 with the client metadata configuration error (existing test at `test_client_metadata_bridge.py:210-234`).
- AC7: a query string still returns 400 "Query strings are not allowed for this endpoint." (existing test at `test_client_metadata_bridge.py:196-207`).
- Invariant: for a base URL without a trailing slash, client_id and redirect_uri share the `<base><root path>` prefix.

## Tests

- Served document (route via `TestClient`): one case with a root path (AC2 and the invariant), and one with an empty root path and a trailing-slash base (AC3).
- Discovery seam: assert the `client_metadata_document_url` returned by `_prepare_discovered_flow_resolution_config` (AC1). The testing guide's seam-test rule requires one assertion per callsite.
- Tests set `API_ROOT_PATH` explicitly on `config` with monkeypatch, including `""` for AC3. Existing root-path-free expectations (`test_client_metadata_bridge.py:28-31`, `test_toolkit_service_auth_resolver.py:1287`) stay unchanged.

## Left to the builder

- Which test file holds the discovery seam test, and the names of the test functions.
