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

import base64
import hashlib
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import codemie.rest_api.routers.sharepoint_oauth as sharepoint_oauth
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User

_TEST_USER = User(
    id="test_user_id",
    username="test_user",
    name="Test User",
    project_names=[],
    admin_project_names=[],
    knowledge_bases=[],
    auth_token=None,
)


def _build_app() -> FastAPI:
    _app = FastAPI()

    @_app.exception_handler(ExtendedHTTPException)
    async def _exc_handler(request, exc: ExtendedHTTPException):
        return JSONResponse(
            status_code=exc.code,
            content={"error": {"message": exc.message, "details": exc.details, "help": exc.help}},
        )

    _app.include_router(sharepoint_oauth.router)
    _app.dependency_overrides[sharepoint_oauth.authenticate] = lambda: _TEST_USER
    return _app


app = _build_app()
client = TestClient(app)


@pytest.fixture(autouse=True)
def enable_pkce(monkeypatch):
    monkeypatch.setattr("codemie.rest_api.routers.sharepoint_oauth.config.SHAREPOINT_PKCE_ENABLED", True)


# ---------------------------------------------------------------------------
# PKCE utility tests
# ---------------------------------------------------------------------------


def test_html_page_escapes_xss():
    page = sharepoint_oauth._html_page(False, "<script>alert('xss')</script>")
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_generate_code_verifier_is_url_safe_and_long_enough():
    verifier = sharepoint_oauth._pkce_service._generate_code_verifier()
    assert len(verifier) >= 43
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in verifier)


def test_generate_code_challenge_is_base64url_sha256():
    verifier = "test_verifier_value"
    expected_digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode()
    assert sharepoint_oauth._pkce_service._generate_code_challenge(verifier) == expected


def test_generate_code_challenge_has_no_padding():
    verifier = sharepoint_oauth._pkce_service._generate_code_verifier()
    challenge = sharepoint_oauth._pkce_service._generate_code_challenge(verifier)
    assert "=" not in challenge


# ---------------------------------------------------------------------------
# POST /v1/sharepoint/oauth/initiate tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.set.return_value = True
    with patch.object(sharepoint_oauth._pkce_service, "_redis", redis):
        yield redis


def test_initiate_returns_auth_url_and_state(mock_redis):
    response = client.post("/v1/sharepoint/oauth/initiate", json={})
    assert response.status_code == 200
    data = response.json()
    assert "auth_url" in data
    assert "state" in data
    assert "login.microsoftonline.com" in data["auth_url"]
    assert "code_challenge" in data["auth_url"]
    assert "code_challenge_method=S256" in data["auth_url"]
    mock_redis.set.assert_called_once()
    call_args = mock_redis.set.call_args
    key = call_args[0][0]
    assert key.startswith("codemie:sp_pkce:state:")
    assert call_args[1]["ex"] == 600


def test_initiate_custom_client_id(mock_redis):
    response = client.post(
        "/v1/sharepoint/oauth/initiate",
        json={"client_id": "custom-client-id", "tenant_id": "my-tenant"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "custom-client-id" in data["auth_url"]
    assert "my-tenant" in data["auth_url"]
    stored_raw = mock_redis.set.call_args[0][1]
    stored = json.loads(stored_raw)
    assert stored["client_id"] == "custom-client-id"
    assert stored["tenant_id"] == "my-tenant"
    assert stored["user_id"] == "test_user_id"


def test_initiate_redis_failure():
    redis = MagicMock()
    redis.set.side_effect = Exception("Redis unavailable")
    with patch.object(sharepoint_oauth._pkce_service, "_redis", redis):
        response = client.post("/v1/sharepoint/oauth/initiate", json={})
    assert response.status_code == 502


# ---------------------------------------------------------------------------
# GET /v1/sharepoint/oauth/callback tests
# ---------------------------------------------------------------------------


def _make_state_value(code_verifier="verifier123", client_id="test-client", tenant_id="", user_id="test_user_id"):
    return json.dumps(
        {"code_verifier": code_verifier, "client_id": client_id, "tenant_id": tenant_id, "user_id": user_id}
    ).encode()


def test_callback_success(httpx_mock):
    state = "valid-state-abc"
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = _make_state_value()
    mock_redis.set.return_value = True

    httpx_mock.add_response(
        method="POST",
        url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        json={"access_token": "tok-abc", "token_type": "Bearer"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="GET",
        url="https://graph.microsoft.com/v1.0/me?$select=userPrincipalName",
        json={"userPrincipalName": "user@example.com"},
        status_code=200,
    )

    with patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis):
        response = client.get(f"/v1/sharepoint/oauth/callback?code=auth-code&state={state}")

    assert response.status_code == 200
    assert "Authentication" in response.text
    assert response.headers.get("x-frame-options") == "DENY"
    assert "default-src" in response.headers.get("content-security-policy", "")

    mock_redis.getdel.assert_called_with(f"codemie:sp_pkce:state:{state}")

    result_call = mock_redis.set.call_args
    assert result_call[0][0] == f"codemie:sp_pkce:result:{state}"
    result_stored = json.loads(result_call[0][1])
    assert result_stored["status"] == "success"
    assert result_stored["access_token"] == "tok-abc"
    assert result_stored["username"] == "user@example.com"
    assert result_stored["user_id"] == "test_user_id"
    assert result_call[1]["ex"] == 300


def test_callback_microsoft_error_param():
    state = "error-state"
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = _make_state_value()
    mock_redis.set.return_value = True

    with patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis):
        response = client.get(f"/v1/sharepoint/oauth/callback?error=access_denied&state={state}")

    assert response.status_code == 200
    assert "Authentication" in response.text

    result_call = mock_redis.set.call_args
    result_stored = json.loads(result_call[0][1])
    assert result_stored["status"] == "error"
    assert "declined" in result_stored["message"].lower() or "access_denied" in result_stored["message"].lower()


def test_callback_invalid_state():
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = None

    with patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis):
        response = client.get("/v1/sharepoint/oauth/callback?code=some-code&state=unknown-state")

    assert response.status_code == 400
    assert "Invalid" in response.text or "expired" in response.text.lower()
    mock_redis.set.assert_not_called()


def test_callback_token_exchange_http_error(httpx_mock):
    state = "httpx-error-state"
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = _make_state_value()
    mock_redis.set.return_value = True

    httpx_mock.add_exception(
        httpx.ConnectError("connection refused"),
        method="POST",
        url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    )

    with patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis):
        response = client.get(f"/v1/sharepoint/oauth/callback?code=code&state={state}")

    assert response.status_code == 200
    result_call = mock_redis.set.call_args
    result_stored = json.loads(result_call[0][1])
    assert result_stored["status"] == "error"


def test_callback_token_exchange_error_response(httpx_mock):
    state = "ms-error-state"
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = _make_state_value()
    mock_redis.set.return_value = True

    httpx_mock.add_response(
        method="POST",
        url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        json={"error": "invalid_grant", "error_description": "Code expired."},
        status_code=400,
    )

    with patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis):
        response = client.get(f"/v1/sharepoint/oauth/callback?code=bad-code&state={state}")

    assert response.status_code == 200
    result_call = mock_redis.set.call_args
    result_stored = json.loads(result_call[0][1])
    assert result_stored["status"] == "error"
    assert result_stored["message"]


# ---------------------------------------------------------------------------
# GET /v1/sharepoint/oauth/status/{state} tests
# ---------------------------------------------------------------------------


def test_status_pending():
    mock_redis = MagicMock()
    mock_redis.get.return_value = None

    with patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis):
        response = client.get("/v1/sharepoint/oauth/status/some-state")

    assert response.status_code == 202
    assert response.json() == {"status": "pending"}
    mock_redis.delete.assert_not_called()


def test_status_success_consumes_token():
    mock_redis = MagicMock()
    result = {"status": "success", "access_token": "tok-xyz", "username": "user@example.com", "user_id": "test_user_id"}
    mock_redis.get.return_value = json.dumps(result).encode()
    mock_redis.delete.return_value = 1

    with patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis):
        response = client.get("/v1/sharepoint/oauth/status/success-state")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["access_token"] == "tok-xyz"
    assert data["username"] == "user@example.com"
    assert "user_id" not in data
    mock_redis.delete.assert_called_once_with("codemie:sp_pkce:result:success-state")


def test_status_error_consumes():
    mock_redis = MagicMock()
    result = {"status": "error", "message": "Authorization was declined. Please try again.", "user_id": "test_user_id"}
    mock_redis.get.return_value = json.dumps(result).encode()
    mock_redis.delete.return_value = 1

    with patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis):
        response = client.get("/v1/sharepoint/oauth/status/error-state")

    assert response.status_code == 400
    data = response.json()
    assert data["status"] == "error"
    assert data["message"]
    mock_redis.delete.assert_called_once_with("codemie:sp_pkce:result:error-state")


def test_initiate_encrypts_state_in_redis():
    import base64 as _b64
    from codemie.service.encryption.base_encryption_service import Base64EncryptionService

    enc = Base64EncryptionService()
    mock_redis = MagicMock()
    mock_redis.set.return_value = True

    with (
        patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis),
        patch.object(sharepoint_oauth._pkce_service, "_enc", enc),
    ):
        response = client.post("/v1/sharepoint/oauth/initiate", json={})

    assert response.status_code == 200
    stored_value = mock_redis.set.call_args[0][1]
    # With Base64EncryptionService the stored value is base64, not plain JSON
    decoded = _b64.b64decode(stored_value).decode()
    parsed = json.loads(decoded)
    assert "code_verifier" in parsed
    assert "user_id" in parsed


def test_callback_uses_getdel_for_state_key(httpx_mock):
    state = "getdel-test-state"
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = _make_state_value()
    mock_redis.set.return_value = True

    httpx_mock.add_response(
        method="POST",
        url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        json={"access_token": "tok-abc", "token_type": "Bearer"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="GET",
        url="https://graph.microsoft.com/v1.0/me?$select=userPrincipalName",
        json={"userPrincipalName": "user@example.com"},
        status_code=200,
    )

    with patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis):
        response = client.get(f"/v1/sharepoint/oauth/callback?code=auth-code&state={state}")

    assert response.status_code == 200
    mock_redis.getdel.assert_called_once_with(f"codemie:sp_pkce:state:{state}")
    mock_redis.get.assert_not_called()
    mock_redis.delete.assert_not_called()


def test_status_user_mismatch_returns_403():
    mock_redis = MagicMock()
    result = {
        "status": "success",
        "access_token": "tok-xyz",
        "username": "other@example.com",
        "user_id": "other_user_id",
    }
    mock_redis.get.return_value = json.dumps(result).encode()

    with patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis):
        response = client.get("/v1/sharepoint/oauth/status/some-state")

    assert response.status_code == 403


def test_status_redis_failure():
    mock_redis = MagicMock()
    mock_redis.get.side_effect = Exception("Redis down")

    with patch.object(sharepoint_oauth._pkce_service, "_redis", mock_redis):
        response = client.get("/v1/sharepoint/oauth/status/any-state")

    assert response.status_code == 502


# ---------------------------------------------------------------------------
# SHAREPOINT_PKCE_ENABLED=False guardrail tests
# ---------------------------------------------------------------------------


def test_initiate_disabled_returns_503(monkeypatch):
    monkeypatch.setattr("codemie.rest_api.routers.sharepoint_oauth.config.SHAREPOINT_PKCE_ENABLED", False)
    response = client.post("/v1/sharepoint/oauth/initiate", json={})
    assert response.status_code == 503


def test_callback_disabled_returns_html_503(monkeypatch):
    monkeypatch.setattr("codemie.rest_api.routers.sharepoint_oauth.config.SHAREPOINT_PKCE_ENABLED", False)
    response = client.get("/v1/sharepoint/oauth/callback?code=x&state=y")
    assert response.status_code == 503
    assert "disabled" in response.text.lower()


def test_status_disabled_returns_503(monkeypatch):
    monkeypatch.setattr("codemie.rest_api.routers.sharepoint_oauth.config.SHAREPOINT_PKCE_ENABLED", False)
    response = client.get("/v1/sharepoint/oauth/status/any-state")
    assert response.status_code == 503
