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

import builtins
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException
from codemie.enterprise.mcp_auth.router import authenticate as router_authenticate
from codemie.rest_api.main import extended_http_exception_handler, mcp_auth_required_handler
from codemie.rest_api.security.user import User


def _build_app():
    from codemie.enterprise.mcp_auth.router import enabled_router

    app = FastAPI()
    app.include_router(enabled_router)
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    app.add_exception_handler(MCPAuthenticationRequiredException, mcp_auth_required_handler)
    return app, TestClient(app)


def _build_user(**overrides: object) -> User:
    payload = {
        "id": "user-1",
        "name": "Test User",
        "auth_token": "Bearer token-123",
    }
    payload.update(overrides)
    user = User(**payload)
    user.is_admin = bool(payload.get("is_admin", False))
    user.is_maintainer = bool(payload.get("is_maintainer", False))
    return user


def _build_mcp_config(*, owner_id: str = "user-1", is_public: bool = False, url: str = "https://mcp.example.com/"):
    return SimpleNamespace(
        id="mcp-config-1",
        user_id=owner_id,
        is_public=is_public,
        config=SimpleNamespace(
            url=url,
            auth_config={
                "id": "auth-config-1",
                "auth_type": "oauth2",
                "authorization_url": "https://idp.example.com/oauth2/authorize",
                "token_url": "https://idp.example.com/oauth2/token",
                "client_id": "client-1",
                "client_type": "public",
                "scopes": ["openid", "profile"],
                "token_delivery": {"method": "header"},
            },
        ),
    )


class _RecordingPKCEStore:
    def __init__(self) -> None:
        self.records: list[tuple[str, object]] = []

    def store(self, state: str, pkce_state: object) -> None:
        self.records.append((state, pkce_state))


def _record_recovery_oauth2_initiate_attempt(enterprise_mcp_auth, *, decision, user, mcp_config) -> None:
    pkce_store = _RecordingPKCEStore()
    response = enterprise_mcp_auth.build_recovery_oauth2_initiate_response(
        recovery_flow_id=decision.recovery_flow_id,
        mcp_config_id=mcp_config.id,
        pkce_store=pkce_store,
        signing_key=b"s" * 32,
        user_id=user.id,
        session_binding_hash="binding-hash-1",
        redirect_uri="https://api.example.com/v1/mcp-auth/oauth2/callback",
        redirect_uri_hostname="api.example.com",
        localhost_warning=False,
    )
    assert response.auth_url.startswith("https://idp.example.com/oauth2/authorize?")
    assert len(pkce_store.records) == 1


@pytest.fixture
def app_client():
    return _build_app()


def test_initiate_route_authenticates_loads_config_and_rejects_client_auth_config_id(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config()
    captured: dict[str, object] = {}

    def fake_build_oauth2_initiate_response(**kwargs):
        captured.update(kwargs)
        data = {
            "auth_url": "https://idp.example.com/oauth2/authorize?state=abc",
            "redirect_uri_hostname": "localhost:8080",
            "localhost_warning": True,
        }
        result = SimpleNamespace(**data)
        result.model_dump = lambda: data
        return result

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config if config_id == mcp_config.id else None
    )
    monkeypatch.setattr(mcp_auth_router, "build_oauth2_initiate_response", fake_build_oauth2_initiate_response)

    response = client.post(
        "/v1/mcp-auth/oauth2/initiate",
        json={"mcp_config_id": mcp_config.id, "auth_config_id": "client-controlled-value"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "auth_url": "https://idp.example.com/oauth2/authorize?state=abc",
        "redirect_uri_hostname": "localhost:8080",
        "localhost_warning": True,
    }
    assert captured["user"] == user
    assert captured["auth_config_id"] == "auth-config-1"
    assert captured["mcp_server_url"] == "https://mcp.example.com/"


def test_initiate_route_accepts_discovered_flow_id_for_no_auth_config(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config()
    mcp_config.config.auth_config = None
    captured: dict[str, object] = {}

    def fake_build_discovered_oauth2_initiate_response(**kwargs):
        captured.update(kwargs)
        data = {
            "auth_url": "https://idp.example.com/oauth2/authorize?state=abc",
            "redirect_uri_hostname": "localhost:8080",
            "localhost_warning": True,
        }
        result = SimpleNamespace(**data)
        result.model_dump = lambda: data
        return result

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config if config_id == mcp_config.id else None
    )
    monkeypatch.setattr(
        mcp_auth_router,
        "build_discovered_oauth2_initiate_response",
        fake_build_discovered_oauth2_initiate_response,
    )

    response = client.post(
        "/v1/mcp-auth/oauth2/initiate",
        json={"mcp_config_id": mcp_config.id, "discovered_flow_id": "flow-1"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "auth_url": "https://idp.example.com/oauth2/authorize?state=abc",
        "redirect_uri_hostname": "localhost:8080",
        "localhost_warning": True,
    }
    assert captured["user"] == user
    assert captured["mcp_config"] == mcp_config
    assert captured["discovered_flow_id"] == "flow-1"


def test_initiate_route_accepts_discovered_flow_id_from_emitted_query_url(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config()
    mcp_config.config.auth_config = None
    captured: dict[str, object] = {}

    def fake_build_discovered_oauth2_initiate_response(**kwargs):
        captured.update(kwargs)
        data = {
            "auth_url": "https://idp.example.com/oauth2/authorize?state=query",
            "redirect_uri_hostname": "localhost:8080",
            "localhost_warning": True,
        }
        result = SimpleNamespace(**data)
        result.model_dump = lambda: data
        return result

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config if config_id == mcp_config.id else None
    )
    monkeypatch.setattr(
        mcp_auth_router,
        "build_discovered_oauth2_initiate_response",
        fake_build_discovered_oauth2_initiate_response,
    )

    response = client.post(
        "/v1/mcp-auth/oauth2/initiate?discovered_flow_id=flow-from-url",
        json={"mcp_config_id": mcp_config.id},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["auth_url"].endswith("state=query")
    assert response.json()["redirect_uri_hostname"] == "localhost:8080"
    assert response.json()["localhost_warning"] is True
    assert captured["discovered_flow_id"] == "flow-from-url"


def test_initiate_route_accepts_recovery_flow_id_from_body_and_query(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config()
    captured: dict[str, object] = {}

    def fake_build_recovery_oauth2_initiate_response(**kwargs):
        captured.update(kwargs)
        data = {
            "auth_url": "https://idp.example.com/oauth2/authorize?state=recovery",
            "redirect_uri_hostname": "localhost:8080",
            "localhost_warning": True,
        }
        result = SimpleNamespace(**data)
        result.model_dump = lambda: data
        return result

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config if config_id == mcp_config.id else None
    )
    monkeypatch.setattr(
        mcp_auth_router,
        "build_recovery_oauth2_initiate_response",
        fake_build_recovery_oauth2_initiate_response,
    )

    body_response = client.post(
        "/v1/mcp-auth/oauth2/initiate",
        json={"mcp_config_id": mcp_config.id, "recovery_flow_id": "rf-body"},
    )
    query_response = client.post(
        "/v1/mcp-auth/oauth2/initiate?recovery_flow_id=rf-query",
        json={"mcp_config_id": mcp_config.id},
    )

    assert body_response.status_code == status.HTTP_200_OK
    assert query_response.status_code == status.HTTP_200_OK
    assert body_response.json()["redirect_uri_hostname"] == "localhost:8080"
    assert body_response.json()["localhost_warning"] is True
    assert query_response.json()["redirect_uri_hostname"] == "localhost:8080"
    assert query_response.json()["localhost_warning"] is True
    assert captured["recovery_flow_id"] == "rf-query"


def test_initiate_route_rejects_recovery_flow_conflicts(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config()

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config if config_id == mcp_config.id else None
    )

    mismatch = client.post(
        "/v1/mcp-auth/oauth2/initiate?recovery_flow_id=rf-query",
        json={"mcp_config_id": mcp_config.id, "recovery_flow_id": "rf-body"},
    )
    ambiguous = client.post(
        "/v1/mcp-auth/oauth2/initiate",
        json={"mcp_config_id": mcp_config.id, "recovery_flow_id": "rf-1", "discovered_flow_id": "df-1"},
    )

    assert mismatch.status_code == status.HTTP_400_BAD_REQUEST
    assert ambiguous.status_code == status.HTTP_400_BAD_REQUEST


def test_initiate_route_recovery_attempt_three_returns_auth_required_envelope(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies
    import codemie_enterprise.mcp_auth as enterprise_mcp_auth

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config()

    enterprise_mcp_auth.reset_recovery_state()
    decision = enterprise_mcp_auth.build_insufficient_scope_recovery(
        enterprise_mcp_auth.RecoveryRequest.model_validate(
            {
                "status_code": 403,
                "www_authenticate_header": (
                    'Bearer error="insufficient_scope", scope="openid admin", '
                    'resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"'
                ),
                "user_id": user.id,
                "mcp_config_id": mcp_config.id,
                "mcp_config_name": "OneHub",
                "auth_config_id": "auth-config-1",
                "token_storage_auth_config_id": "auth-config-1",
                "conversation_id": "conversation-1",
                "server_url": "https://mcp.example.com/",
                "auth_config": mcp_config.config.auth_config,
                "token_data": enterprise_mcp_auth.OAuth2TokenData(
                    access_token="token-123",
                    scope="openid",
                    issuer="https://idp.example.com",
                    resource="https://mcp.example.com/",
                ),
                "authorization_server_metadata": {
                    "issuer": "https://idp.example.com",
                    "authorization_endpoint": "https://idp.example.com/oauth2/authorize",
                    "token_endpoint": "https://idp.example.com/oauth2/token",
                },
                "authorization_server_metadata_validated": True,
            }
        )
    )
    assert decision is not None
    assert decision.attempts_used == 0
    _record_recovery_oauth2_initiate_attempt(
        enterprise_mcp_auth,
        decision=decision,
        user=user,
        mcp_config=mcp_config,
    )
    second = enterprise_mcp_auth.build_insufficient_scope_recovery(
        enterprise_mcp_auth.RecoveryRequest.model_validate(
            {
                "status_code": 403,
                "www_authenticate_header": (
                    'Bearer error="insufficient_scope", scope="openid admin", '
                    'resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"'
                ),
                "user_id": user.id,
                "mcp_config_id": mcp_config.id,
                "mcp_config_name": "OneHub",
                "auth_config_id": "auth-config-1",
                "token_storage_auth_config_id": "auth-config-1",
                "conversation_id": "conversation-1",
                "server_url": "https://mcp.example.com/",
                "auth_config": mcp_config.config.auth_config,
                "token_data": enterprise_mcp_auth.OAuth2TokenData(
                    access_token="token-123",
                    scope="openid",
                    issuer="https://idp.example.com",
                    resource="https://mcp.example.com/",
                ),
                "authorization_server_metadata": {
                    "issuer": "https://idp.example.com",
                    "authorization_endpoint": "https://idp.example.com/oauth2/authorize",
                    "token_endpoint": "https://idp.example.com/oauth2/token",
                },
                "authorization_server_metadata_validated": True,
            }
        )
    )
    assert second is not None
    assert second.attempts_used == 1
    _record_recovery_oauth2_initiate_attempt(
        enterprise_mcp_auth,
        decision=second,
        user=user,
        mcp_config=mcp_config,
    )
    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config if config_id == mcp_config.id else None
    )
    monkeypatch.setattr(mcp_auth_dependencies, "build_redirect_uri", lambda: pytest.fail("auth URL setup was called"))
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_require_initialized_mcp_auth_components",
        lambda: pytest.fail("PKCE setup was called"),
    )

    response = client.post(
        "/v1/mcp-auth/oauth2/initiate",
        json={"mcp_config_id": mcp_config.id, "recovery_flow_id": second.recovery_flow_id},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    body = response.json()
    assert body["error"] == "authentication_required"
    assert isinstance(body.get("servers"), list) and len(body["servers"]) == 1
    server = body["servers"][0]
    assert server["status"] == "config_error"
    assert server["error"] == "scope_escalation_failed"
    assert server["reason"] == "scope_escalation_failed"
    assert server["guidance"] == (
        "Scope escalation failed after 2 attempts for 'OneHub'. "
        "Contact your administrator to review scope configuration on the Authorization Server."
    )
    assert server["attempts_used"] == 2
    assert server["attempts_remaining"] == 0
    for forbidden in ("action", "action_label", "initiate_url", "recovery_flow_id", "auth_url"):
        assert forbidden not in server
    snapshot_after = enterprise_mcp_auth.get_recovery_snapshot(second.recovery_flow_id)
    assert snapshot_after is not None
    assert snapshot_after.recovery_flow_id == second.recovery_flow_id


def test_build_recovery_initiate_passes_mcp_config_id_to_enterprise_builder(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    captured: dict[str, object] = {}

    def fake_build_recovery_oauth2_initiate_response(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            auth_url="https://idp.example.com/oauth2/authorize?state=recovery",
            redirect_uri_hostname=kwargs["redirect_uri_hostname"],
            localhost_warning=kwargs["localhost_warning"],
        )

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "https://api.example.com")
    monkeypatch.setattr(mcp_auth_dependencies, "_pkce_store", SimpleNamespace())
    monkeypatch.setattr(mcp_auth_dependencies, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setitem(
        sys.modules,
        "codemie_enterprise.mcp_auth",
        SimpleNamespace(
            MCPAuthRedisUnavailable=RuntimeError,
            RecoveryAttemptsExhausted=RuntimeError,
            get_recovery_oauth2_initiate_exhausted_decision=lambda **kwargs: None,
            build_recovery_oauth2_initiate_response=fake_build_recovery_oauth2_initiate_response,
        ),
    )

    response = mcp_auth_dependencies.build_recovery_oauth2_initiate_response(
        mcp_config=_build_mcp_config(),
        user=_build_user(),
        recovery_flow_id="rf-1",
    )

    assert response.auth_url.endswith("state=recovery")
    assert captured["mcp_config_id"] == "mcp-config-1"


def test_initiate_route_uses_discovered_binding_fallback_for_no_auth_config(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config()
    mcp_config.config.auth_config = None
    captured: dict[str, object] = {}

    def fake_build_discovered_oauth2_initiate_response(**kwargs):
        captured.update(kwargs)
        data = {
            "auth_url": "https://idp.example.com/oauth2/authorize?state=binding",
            "redirect_uri_hostname": "localhost:8080",
            "localhost_warning": True,
        }
        result = SimpleNamespace(**data)
        result.model_dump = lambda: data
        return result

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config if config_id == mcp_config.id else None
    )
    monkeypatch.setattr(
        mcp_auth_router,
        "build_discovered_oauth2_initiate_response",
        fake_build_discovered_oauth2_initiate_response,
    )

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": mcp_config.id})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["auth_url"].endswith("state=binding")
    assert captured["user"] == user
    assert captured["mcp_config"] == mcp_config
    assert captured["discovered_flow_id"] is None


def test_initiate_route_rejects_private_non_owned_config(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user(id="other-user", auth_token="Bearer token-123")
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: _build_mcp_config(owner_id="owner-user")
    )

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": "mcp-config-1"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error"]["message"] == "Access denied"


def test_initiate_route_returns_not_found_for_missing_config(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user()
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: None)

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": "missing-config"})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["message"] == "MCP configuration not found"


def test_initiate_route_fails_closed_when_auth_token_missing(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user(auth_token=None)
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: _build_mcp_config())
    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "http://localhost:8080")

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": "mcp-config-1"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        response.json()["error"]["details"]
        == "Authenticated MCP auth initiation requires a bearer token for session binding."
    )


def test_initiate_route_returns_redirect_hostname_and_localhost_warning(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user()
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: _build_mcp_config())
    monkeypatch.setattr(mcp_auth_dependencies, "_pkce_store", SimpleNamespace())
    monkeypatch.setattr(mcp_auth_dependencies, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))

    captured: dict[str, object] = {}

    def fake_build_oauth2_initiate_response(**kwargs):
        captured.update(kwargs)
        data = {
            "auth_url": "https://idp.example.com/oauth2/authorize?state=abc",
            "redirect_uri_hostname": kwargs["redirect_uri_hostname"],
            "localhost_warning": kwargs["localhost_warning"],
        }
        result = SimpleNamespace(**data)
        result.model_dump = lambda: data
        return result

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "http://localhost:8080")
    monkeypatch.setitem(
        sys.modules,
        "codemie_enterprise.mcp_auth",
        SimpleNamespace(
            MCPAuthRedisUnavailable=RuntimeError,
            OAuth2AuthConfig=SimpleNamespace(model_validate=lambda raw: raw),
            build_oauth2_initiate_response=fake_build_oauth2_initiate_response,
        ),
    )

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": "mcp-config-1"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["redirect_uri_hostname"] == "localhost:8080"
    assert response.json()["localhost_warning"] is True
    assert captured["redirect_uri"] == "http://localhost:8080/v1/mcp-auth/oauth2/callback"


def test_build_oauth2_initiate_response_passes_canonical_resource_to_enterprise_builder(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies
    import codemie_enterprise.mcp_auth as enterprise_mcp_auth

    captured: dict[str, object] = {}

    def fake_build_oauth2_initiate_response(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            auth_url="https://idp.example.com/oauth2/authorize?state=abc",
            redirect_uri_hostname=kwargs["redirect_uri_hostname"],
            localhost_warning=kwargs["localhost_warning"],
        )

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "https://api.example.com")
    monkeypatch.setattr(mcp_auth_dependencies, "_pkce_store", SimpleNamespace())
    monkeypatch.setattr(mcp_auth_dependencies, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setattr(enterprise_mcp_auth, "OAuth2AuthConfig", SimpleNamespace(model_validate=lambda raw: raw))
    monkeypatch.setattr(enterprise_mcp_auth, "build_oauth2_initiate_response", fake_build_oauth2_initiate_response)
    monkeypatch.setattr(enterprise_mcp_auth, "MCPAuthRedisUnavailable", RuntimeError)

    response = mcp_auth_dependencies.build_oauth2_initiate_response(
        raw_auth_config=_build_mcp_config().config.auth_config,
        user=_build_user(),
        auth_config_id="auth-config-1",
        mcp_server_url="https://MCP.Example.Com:443/api/mcp?v=1#section",
    )

    assert response.auth_url == "https://idp.example.com/oauth2/authorize?state=abc"
    assert captured["resource"] == "https://mcp.example.com/api/mcp"


def test_build_redirect_uri_returns_full_netloc_for_non_default_https_port(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "https://api.example.com:9443")

    redirect_uri, redirect_uri_hostname, localhost_warning = mcp_auth_dependencies.build_redirect_uri()

    assert redirect_uri == "https://api.example.com:9443/v1/mcp-auth/oauth2/callback"
    assert redirect_uri_hostname == "api.example.com:9443"
    assert localhost_warning is False


def test_build_redirect_uri_marks_normal_https_as_not_localhost(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "https://api.example.com")

    redirect_uri, redirect_uri_hostname, localhost_warning = mcp_auth_dependencies.build_redirect_uri()

    assert redirect_uri == "https://api.example.com/v1/mcp-auth/oauth2/callback"
    assert redirect_uri_hostname == "api.example.com"
    assert localhost_warning is False


@pytest.mark.parametrize(
    ("callback_base_url", "expected_hostname"),
    [
        ("http://127.0.0.1:8080", "127.0.0.1:8080"),
        ("http://[::1]:8080", "[::1]:8080"),
    ],
)
def test_build_redirect_uri_marks_localhost_family_hosts(
    monkeypatch, callback_base_url: str, expected_hostname: str
) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", callback_base_url)

    redirect_uri, redirect_uri_hostname, localhost_warning = mcp_auth_dependencies.build_redirect_uri()

    assert redirect_uri.endswith("/v1/mcp-auth/oauth2/callback")
    assert redirect_uri_hostname == expected_hostname
    assert localhost_warning is True


def test_build_redirect_uri_rejects_http_non_localhost(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "http://api.example.com:8080")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        mcp_auth_dependencies.build_redirect_uri()

    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "Redirect URI must use HTTPS" in exc_info.value.details


def test_disabled_router_still_returns_story_1_4_payload(app_client) -> None:
    from codemie.enterprise.mcp_auth.router import router as disabled_router

    app = FastAPI()
    app.include_router(disabled_router)
    disabled_client = TestClient(app)

    response = disabled_client.post("/v1/mcp-auth/oauth2/initiate")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {
        "feature": "MCP Authorization",
        "state": "inactive — enterprise package not installed or MCP_AUTH_ENABLED not set",
        "action": "Enable MCP_AUTH_ENABLED and install the enterprise package",
    }


def test_derive_resource_uri_normalizes_default_port_root_slash_and_query_fragment() -> None:
    from codemie.enterprise.mcp_auth.dependencies import derive_resource_uri

    assert derive_resource_uri("https://mcp.example.com:443/?q=1#frag") == "https://mcp.example.com"
    assert derive_resource_uri("https://MCP.Example.Com/path?q=1#frag") == "https://mcp.example.com/path"
    assert derive_resource_uri("https://MCP.Example.Com:443/api/mcp?v=1#section") == "https://mcp.example.com/api/mcp"
    assert derive_resource_uri("https://mcp.example.com/api/") == "https://mcp.example.com/api/"
    assert derive_resource_uri("https://mcp.example.com:8443/") == "https://mcp.example.com:8443"


def test_derive_resource_uri_uses_core_fallback_when_enterprise_discovery_import_fails(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    original_import = builtins.__import__
    captured: dict[str, str] = {}

    def fail_enterprise_discovery_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "codemie_enterprise.mcp_auth.discovery" and "derive_canonical_mcp_resource_uri" in fromlist:
            raise ImportError("enterprise discovery unavailable")
        return original_import(name, globals, locals, fromlist, level)

    def fake_fallback(server_url: str) -> str:
        captured["server_url"] = server_url
        return "fallback-resource"

    monkeypatch.setattr(builtins, "__import__", fail_enterprise_discovery_import)
    monkeypatch.setattr(mcp_auth_dependencies, "_derive_resource_uri_without_enterprise", fake_fallback)

    assert (
        mcp_auth_dependencies.derive_resource_uri("https://MCP.Example.Com:443/api/mcp?v=1#section")
        == "fallback-resource"
    )
    assert captured["server_url"] == "https://MCP.Example.Com:443/api/mcp?v=1#section"


@pytest.mark.parametrize(
    "server_url",
    [
        "http://mcp.example.com/api?token=secret#frag",
        "https://user:secret@mcp.example.com/api",
        "https://127.0.0.1/api",
        "https://bad..host/api?access_token=secret",
    ],
)
def test_derive_resource_uri_rejects_invalid_or_tainted_url_without_echo(server_url: str) -> None:
    from codemie.enterprise.mcp_auth.dependencies import derive_resource_uri

    with pytest.raises(ExtendedHTTPException) as exc_info:
        derive_resource_uri(server_url)

    rendered = f"{exc_info.value.message} {exc_info.value.details}"
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "secret" not in rendered
    assert "token" not in rendered
    assert "frag" not in rendered
    assert "user:" not in rendered


# ── Approach C: inline discovery heal path ────────────────────────────────────


def _build_discovered_mcp_config(
    *,
    owner_id: str = "user-1",
    is_public: bool = False,
    url: str = "https://mcp.example.com/",
    allow_issuer_prefix_match: bool = False,
) -> object:
    """MCPConfig with auth_config=None — a discovered OAuth2 server."""
    return SimpleNamespace(
        id="mcp-config-disc-1",
        name="discovered-server",
        user_id=owner_id,
        is_public=is_public,
        config=SimpleNamespace(
            url=url,
            auth_config=None,
            allow_issuer_prefix_match=allow_issuer_prefix_match,
        ),
    )


def _build_discovered_snapshot(
    *,
    discovered_flow_id: str = "flow-healed-1",
    mcp_config_id: str = "mcp-config-disc-1",
    user_id: str = "user-1",
) -> object:
    return SimpleNamespace(
        discovered_flow_id=discovered_flow_id,
        discovered_auth_id="auth-id-healed",
        mcp_config_id=mcp_config_id,
        user_id=user_id,
        session_binding_hash="binding-hash-token123",
        status="authentication_required",
        flow_config=SimpleNamespace(
            auth_type="oauth2",
            authorization_url="https://idp.example.com/oauth2/authorize",
            token_url="https://idp.example.com/oauth2/token",
            client_id="client-healed",
            client_type="public",
            scopes=["openid"],
            token_delivery={"method": "header"},
        ),
        canonical_resource="https://mcp.example.com/",
        as_hostname="idp.example.com",
        redirect_uri="https://api.example.com/v1/mcp-auth/oauth2/callback",
    )


def _stub_enterprise_for_heal(monkeypatch, *, snapshot: object, auth_url: str) -> None:
    """Patch all deps needed for build_discovered_oauth2_initiate_response to complete."""
    import sys
    from codemie.enterprise.mcp_auth import dependencies as deps

    monkeypatch.setattr(
        deps,
        "_mcp_auth_discovered_flow_store",
        SimpleNamespace(
            get=lambda flow_id: snapshot if flow_id == getattr(snapshot, "discovered_flow_id", None) else None,
            get_for_binding=lambda user_id, binding, cfg_id: snapshot,
        ),
    )
    monkeypatch.setattr(deps, "_pkce_store", SimpleNamespace(store=lambda state, pkce: None))
    monkeypatch.setattr(deps, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setattr(deps, "_validate_discovered_snapshot_context", lambda *a, **kw: None)

    monkeypatch.setitem(
        sys.modules,
        "codemie_enterprise.mcp_auth",
        SimpleNamespace(
            build_oauth2_initiate_response=lambda **kw: SimpleNamespace(
                auth_url=auth_url,
                redirect_uri_hostname="api.example.com",
                localhost_warning=False,
                model_dump=lambda: {
                    "auth_url": auth_url,
                    "redirect_uri_hostname": "api.example.com",
                    "localhost_warning": False,
                },
            ),
            MCPAuthRedisUnavailable=Exception,
        ),
    )


def test_initiate_heals_binding_miss_via_inline_discovery(monkeypatch, app_client) -> None:
    """By-binding miss: store returns None → ensure_... heals → re-read by binding → 200."""
    import sys
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as deps
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    app, client = app_client
    user = _build_user()
    mcp_config = _build_discovered_mcp_config()
    snapshot = _build_discovered_snapshot()
    auth_url = "https://idp.example.com/oauth2/authorize?state=healed"

    # Store: first get_for_binding returns None (miss), second returns snapshot (after heal)
    get_for_binding_calls: list[int] = [0]

    class _HealableStore:
        def get(self, flow_id: str) -> object | None:
            return None

        def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
            get_for_binding_calls[0] += 1
            return None if get_for_binding_calls[0] == 1 else snapshot

    monkeypatch.setattr(deps, "_mcp_auth_discovered_flow_store", _HealableStore())
    monkeypatch.setattr(deps, "_pkce_store", SimpleNamespace(store=lambda state, pkce: None))
    monkeypatch.setattr(deps, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setattr(deps, "_validate_discovered_snapshot_context", lambda *a, **kw: None)
    monkeypatch.setattr(
        MCPToolkitService,
        "ensure_discovered_snapshot_for_server",
        classmethod(lambda cls, **kw: "flow-healed-1"),
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "codemie_enterprise.mcp_auth",
        SimpleNamespace(
            build_oauth2_initiate_response=lambda **kw: SimpleNamespace(
                auth_url=auth_url,
                redirect_uri_hostname="api.example.com",
                localhost_warning=False,
                model_dump=lambda: {
                    "auth_url": auth_url,
                    "redirect_uri_hostname": "api.example.com",
                    "localhost_warning": False,
                },
            ),
            MCPAuthRedisUnavailable=Exception,
        ),
    )

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    # Without heal wiring in build_discovered_oauth2_initiate_response → 400 → RED
    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": mcp_config.id})

    assert response.status_code == 200, f"Expected 200 after heal, got {response.status_code}: {response.text}"
    assert response.json()["auth_url"] == auth_url
    assert get_for_binding_calls[0] == 2, "store.get_for_binding must be called twice: probe then re-read"


def test_initiate_heals_by_id_expired_via_inline_discovery(monkeypatch, app_client) -> None:
    """By-id-expired: store.get(flow_id) returns None → heal → re-read by binding → 200.
    Re-read must use binding (new snapshot stored by binding, not under stale id)."""
    import sys
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as deps
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    app, client = app_client
    user = _build_user()
    mcp_config = _build_discovered_mcp_config()
    snapshot = _build_discovered_snapshot(discovered_flow_id="new-flow-after-heal")
    auth_url = "https://idp.example.com/oauth2/authorize?state=healed-id"

    class _ExpiredIdStore:
        def get(self, flow_id: str) -> object | None:
            return None  # stale id — expired from Redis

        def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
            return snapshot  # heal stored it by binding

    monkeypatch.setattr(deps, "_mcp_auth_discovered_flow_store", _ExpiredIdStore())
    monkeypatch.setattr(deps, "_pkce_store", SimpleNamespace(store=lambda state, pkce: None))
    monkeypatch.setattr(deps, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setattr(deps, "_validate_discovered_snapshot_context", lambda *a, **kw: None)
    monkeypatch.setattr(
        MCPToolkitService,
        "ensure_discovered_snapshot_for_server",
        classmethod(lambda cls, **kw: "new-flow-after-heal"),
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "codemie_enterprise.mcp_auth",
        SimpleNamespace(
            build_oauth2_initiate_response=lambda **kw: SimpleNamespace(
                auth_url=auth_url,
                redirect_uri_hostname="api.example.com",
                localhost_warning=False,
                model_dump=lambda: {
                    "auth_url": auth_url,
                    "redirect_uri_hostname": "api.example.com",
                    "localhost_warning": False,
                },
            ),
            MCPAuthRedisUnavailable=Exception,
        ),
    )

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    # With stale discovered_flow_id: without heal wiring → 400 → RED
    response = client.post(
        "/v1/mcp-auth/oauth2/initiate",
        json={"mcp_config_id": mcp_config.id, "discovered_flow_id": "stale-flow-id"},
    )

    assert (
        response.status_code == 200
    ), f"Expected 200 after heal of expired id, got {response.status_code}: {response.text}"
    assert response.json()["auth_url"] == auth_url


def test_initiate_returns_400_not_500_when_discovery_fails(monkeypatch, app_client) -> None:
    """If ensure_... returns None (discovery failed), the original 400 is raised — never a 500."""
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as deps
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    app, client = app_client
    user = _build_user()
    mcp_config = _build_discovered_mcp_config()

    class _MissStore:
        def get(self, flow_id: str) -> object | None:
            return None

        def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
            return None

    monkeypatch.setattr(deps, "_mcp_auth_discovered_flow_store", _MissStore())
    monkeypatch.setattr(
        MCPToolkitService, "ensure_discovered_snapshot_for_server", classmethod(lambda cls, **kw: None), raising=False
    )

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": mcp_config.id})

    assert response.status_code == 400, f"Discovery failure must return 400, got {response.status_code}"
    assert response.status_code != 500, "Discovery failure must never surface as 500"


def test_initiate_valid_snapshot_does_not_trigger_heal(monkeypatch, app_client) -> None:
    """If the snapshot exists already, ensure_... must NOT be called (no unnecessary probe)."""
    import sys
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as deps
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    app, client = app_client
    user = _build_user()
    mcp_config = _build_discovered_mcp_config()
    snapshot = _build_discovered_snapshot()
    auth_url = "https://idp.example.com/oauth2/authorize?state=existing"

    monkeypatch.setattr(
        deps,
        "_mcp_auth_discovered_flow_store",
        SimpleNamespace(
            get=lambda flow_id: snapshot,
            get_for_binding=lambda user_id, binding, cfg_id: snapshot,
        ),
    )
    monkeypatch.setattr(deps, "_pkce_store", SimpleNamespace(store=lambda state, pkce: None))
    monkeypatch.setattr(deps, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setattr(deps, "_validate_discovered_snapshot_context", lambda *a, **kw: None)

    ensure_calls: list[int] = [0]
    monkeypatch.setattr(
        MCPToolkitService,
        "ensure_discovered_snapshot_for_server",
        classmethod(lambda cls, **kw: ensure_calls.__setitem__(0, ensure_calls[0] + 1) or "flow"),
        raising=False,
    )

    monkeypatch.setitem(
        sys.modules,
        "codemie_enterprise.mcp_auth",
        SimpleNamespace(
            build_oauth2_initiate_response=lambda **kw: SimpleNamespace(
                auth_url=auth_url,
                redirect_uri_hostname="api.example.com",
                localhost_warning=False,
                model_dump=lambda: {
                    "auth_url": auth_url,
                    "redirect_uri_hostname": "api.example.com",
                    "localhost_warning": False,
                },
            ),
            MCPAuthRedisUnavailable=Exception,
        ),
    )

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": mcp_config.id})

    assert ensure_calls[0] == 0, "ensure_... must NOT be called when a valid snapshot already exists"
    assert response.status_code == 200


def test_initiate_auth_rejection_precedes_discovery(monkeypatch, app_client) -> None:
    """_check_mcp_config_access raises 403 before ensure_... is ever called."""
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    app, client = app_client
    # other-user does not own the config and it is not public
    other_user = _build_user(id="other-user", auth_token="Bearer other-token")
    mcp_config = _build_discovered_mcp_config(owner_id="owner-user")

    ensure_calls: list[int] = [0]
    monkeypatch.setattr(
        MCPToolkitService,
        "ensure_discovered_snapshot_for_server",
        classmethod(lambda cls, **kw: ensure_calls.__setitem__(0, ensure_calls[0] + 1) or "flow"),
        raising=False,
    )

    app.dependency_overrides[router_authenticate] = lambda: other_user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": mcp_config.id})

    assert response.status_code == 403, "Non-owner must be rejected before any discovery attempt"
    assert ensure_calls[0] == 0, "ensure_... must never run after a 403 access rejection"


def test_initiate_redis_outage_on_probe_propagates_as_503(monkeypatch, app_client) -> None:
    """Redis outage on the non-raising probe must NOT silently trigger heal — it must propagate as 503."""
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as deps
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    app, client = app_client
    user = _build_user()
    mcp_config = _build_discovered_mcp_config()

    class _RedisDownStore:
        def get(self, flow_id: str) -> object | None:
            raise ExtendedHTTPException(
                code=status.HTTP_503_SERVICE_UNAVAILABLE,
                message="MCP auth temporarily unavailable",
                details="Redis is down",
                help="Try again later.",
            )

        def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
            raise ExtendedHTTPException(
                code=status.HTTP_503_SERVICE_UNAVAILABLE,
                message="MCP auth temporarily unavailable",
                details="Redis is down",
                help="Try again later.",
            )

    monkeypatch.setattr(deps, "_mcp_auth_discovered_flow_store", _RedisDownStore())

    ensure_calls: list[int] = [0]
    monkeypatch.setattr(
        MCPToolkitService,
        "ensure_discovered_snapshot_for_server",
        classmethod(lambda cls, **kw: ensure_calls.__setitem__(0, ensure_calls[0] + 1) or "flow"),
        raising=False,
    )

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": mcp_config.id})

    assert response.status_code == 503, f"Redis outage must propagate as 503, got {response.status_code}"
    assert ensure_calls[0] == 0, "ensure_... must NOT run on infra error — only on genuine snapshot absence"
