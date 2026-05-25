# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies
from codemie.rest_api.main import extended_http_exception_handler

CLIENT_METADATA_PATH = "/oauth/client-metadata.json"
CALLBACK_BASE_URL = "https://codemie.example.com"
EXPECTED_CLIENT_ID = f"{CALLBACK_BASE_URL}{CLIENT_METADATA_PATH}"
EXPECTED_REDIRECT_URI = f"{CALLBACK_BASE_URL}/v1/mcp-auth/oauth2/callback"
RAW_QUERY = (
    "client_secret=secret-sentinel&scope=scope-sentinel&resource=https://resource.example.com/"
    "&user_id=user-id-sentinel"
)
RAW_QUERY_URL = f"http://testserver{CLIENT_METADATA_PATH}?{RAW_QUERY}"
TAINTED_HEADERS = {
    "Authorization": "Bearer bearer-token-sentinel",
    "Cookie": "session=cookie-sentinel",
    "X-Project-User-Id": "user-id-sentinel",
}
FORBIDDEN_FRAGMENTS = (
    "client_secret",
    "secret-sentinel",
    "Authorization",
    "Authorization: Bearer bearer-token-sentinel",
    "bearer-token-sentinel",
    "Cookie",
    "Cookie: session=cookie-sentinel",
    "cookie-sentinel",
    "user_id",
    "user-id-sentinel",
    "scope",
    "scope-sentinel",
    "resource",
    "https://resource.example.com/",
    RAW_QUERY,
    RAW_QUERY_URL,
)
ALLOWED_NON_ECHO_FRAGMENTS = ("MCP Authorization",)


@pytest.fixture(autouse=True)
def callback_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", CALLBACK_BASE_URL)


def _build_cimd_client(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> TestClient:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    monkeypatch.setattr(mcp_auth_router, "is_mcp_auth_enabled", lambda: enabled)
    app = FastAPI()
    app.include_router(mcp_auth_router.get_cimd_router())
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    return TestClient(app)


def _build_combined_enabled_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    monkeypatch.setattr(mcp_auth_router, "is_mcp_auth_enabled", lambda: True)
    app = FastAPI()
    app.include_router(mcp_auth_router.get_mcp_auth_router())
    app.include_router(mcp_auth_router.get_cimd_router())
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    return TestClient(app)


def _remove_allowed_non_echo_fragments(surface: str) -> str:
    for fragment in ALLOWED_NON_ECHO_FRAGMENTS:
        surface = surface.replace(fragment, "")
    return surface


def _assert_no_echo(
    response_text: str,
    response_json: object,
    caplog: pytest.LogCaptureFixture,
    *,
    error_text: str = "",
) -> None:
    surfaces = (
        response_text,
        json.dumps(response_json, sort_keys=True),
        caplog.text,
        error_text,
    )
    for fragment in FORBIDDEN_FRAGMENTS:
        for surface in surfaces:
            surface = _remove_allowed_non_echo_fragments(surface)
            assert fragment not in surface


def test_enabled_cimd_route_returns_public_document_headers_and_does_not_require_auth(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _build_cimd_client(monkeypatch, enabled=True)

    unauthenticated = client.get(CLIENT_METADATA_PATH)
    tainted = client.get(CLIENT_METADATA_PATH, headers=TAINTED_HEADERS)

    for response in (unauthenticated, tainted):
        assert response.status_code == status.HTTP_200_OK
        assert response.headers["content-type"].startswith("application/json")
        assert response.headers["cache-control"] == "max-age=3600"
        assert response.json() == {
            "client_id": EXPECTED_CLIENT_ID,
            "client_name": "CodeMie Platform",
            "redirect_uris": [EXPECTED_REDIRECT_URI],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        assert set(response.json()) == {
            "client_id",
            "client_name",
            "redirect_uris",
            "grant_types",
            "response_types",
            "token_endpoint_auth_method",
        }
        _assert_no_echo(response.text, response.json(), caplog)


def test_disabled_cimd_route_returns_503_payload_without_echoing_tainted_inputs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from codemie.enterprise.mcp_auth.router import _DISABLED_RESPONSE

    client = _build_cimd_client(monkeypatch, enabled=False)

    response = client.get(f"{CLIENT_METADATA_PATH}?{RAW_QUERY}", headers=TAINTED_HEADERS)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == _DISABLED_RESPONSE.model_dump()
    _assert_no_echo(response.text, response.json(), caplog)


def test_unavailable_cimd_route_returns_503_before_query_rejection_without_echoing_tainted_inputs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth.router import _DISABLED_RESPONSE, MCPAuthEnterpriseUnavailableError

    def unavailable_cimd() -> None:
        raise MCPAuthEnterpriseUnavailableError

    monkeypatch.setattr(mcp_auth_router, "ensure_client_metadata_document_available", unavailable_cimd)
    client = _build_cimd_client(monkeypatch, enabled=True)

    response = client.get(f"{CLIENT_METADATA_PATH}?{RAW_QUERY}", headers=TAINTED_HEADERS)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == _DISABLED_RESPONSE.model_dump()
    assert not {
        "client_id",
        "client_name",
        "redirect_uris",
        "grant_types",
        "response_types",
        "token_endpoint_auth_method",
    }.intersection(response.json())
    _assert_no_echo(response.text, response.json(), caplog)


def test_cimd_route_is_not_available_under_v1_mcp_auth_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_combined_enabled_client(monkeypatch)

    response = client.get("/v1/mcp-auth/oauth/client-metadata.json")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_enabled_cimd_route_rejects_query_without_echoing_tainted_inputs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _build_cimd_client(monkeypatch, enabled=True)

    response = client.get(f"{CLIENT_METADATA_PATH}?{RAW_QUERY}", headers=TAINTED_HEADERS)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["message"] == "Invalid client metadata document request"
    assert response.json()["error"]["details"] == "Query strings are not allowed for this endpoint."
    _assert_no_echo(response.text, response.json(), caplog)


def test_enabled_cimd_route_sanitizes_invalid_configured_base_url_without_echoing_tainted_values(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        mcp_auth_dependencies.config,
        "CALLBACK_API_BASE_URL",
        f"http://codemie.example.com?{RAW_QUERY}",
    )
    client = _build_cimd_client(monkeypatch, enabled=True)

    response = client.get(CLIENT_METADATA_PATH, headers=TAINTED_HEADERS)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not {
        "client_id",
        "client_name",
        "redirect_uris",
        "grant_types",
        "response_types",
        "token_endpoint_auth_method",
    }.intersection(response.json())
    _assert_no_echo(response.text, response.json(), caplog)
    assert response.json()["error"]["message"] == "Invalid MCP auth configuration"
    assert response.json()["error"]["details"] == "Client metadata document configuration is invalid."


def test_main_registers_cimd_router_separately_after_mcp_auth_router() -> None:
    source = Path("src/codemie/rest_api/main.py").read_text(encoding="utf-8")

    assert "get_cimd_router" in source
    mcp_import_index = source.index("get_mcp_auth_router")
    cimd_import_index = source.index("get_cimd_router")
    mcp_include_index = source.index("app.include_router(get_mcp_auth_router())")
    cimd_include_index = source.index("app.include_router(get_cimd_router())")

    assert mcp_import_index < cimd_import_index
    assert mcp_include_index < cimd_include_index
