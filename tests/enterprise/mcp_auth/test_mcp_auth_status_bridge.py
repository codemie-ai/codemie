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

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.enterprise.mcp_auth.router import authenticate as router_authenticate
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.security.user import User
from codemie.service.mcp.models import MCPExecutionContext
from codemie.service.mcp.toolkit_service import MCPToolkitService

_UNSET = object()


def _build_enabled_app_client() -> tuple[FastAPI, TestClient]:
    from codemie.enterprise.mcp_auth.router import enabled_router

    app = FastAPI()
    app.include_router(enabled_router)
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    return app, TestClient(app)


def _build_disabled_client() -> TestClient:
    from codemie.enterprise.mcp_auth.router import router as disabled_router

    app = FastAPI()
    app.include_router(disabled_router)
    return TestClient(app)


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


def _build_auth_config(auth_type: str = "oauth2", **overrides: object) -> dict[str, object]:
    payload: dict[str, object]
    if auth_type == "saml":
        payload = {
            "id": "auth-config-1",
            "auth_type": "saml",
            "sso_url": "https://idp.example.com/sso",
            "entity_id": "https://idp.example.com/metadata",
            "idp_entity_id": "https://idp.example.com/metadata",
            "idp_x509cert": "CERTDATA",
            "saml_credential_attribute": "mail",
            "saml_session_ttl": 3600,
            "token_delivery": {"method": "env", "key": "ACCESS_TOKEN"},
        }
    else:
        payload = {
            "id": "auth-config-1",
            "auth_type": "oauth2",
            "authorization_url": "https://login.example.com/oauth2/authorize",
            "token_url": "https://login.example.com/oauth2/token",
            "client_id": "client-1",
            "client_type": "public",
            "scopes": ["openid", "profile"],
            "token_delivery": {"method": "header"},
        }
    payload.update(overrides)
    return payload


def _build_mcp_config(
    *,
    config_id: str = "mcp-config-1",
    name: str = "Catalog Server",
    owner_id: str = "user-1",
    is_public: bool = False,
    auth_config: dict[str, object] | None | object = _UNSET,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=config_id,
        name=name,
        user_id=owner_id,
        is_public=is_public,
        config=SimpleNamespace(
            url="https://mcp.example.com/server",
            auth_config=_build_auth_config() if auth_config is _UNSET else auth_config,
        ),
    )


@pytest.fixture
def app_client() -> tuple[FastAPI, TestClient]:
    return _build_enabled_app_client()


def test_status_route_returns_authenticated_payload_with_required_fields(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config()
    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)
    monkeypatch.setattr(mcp_auth_router, "_require_initialized_tms", lambda: object())
    monkeypatch.setattr(
        mcp_auth_router,
        "_evaluate_auth_status",
        lambda **kwargs: ("authenticated", kwargs["raw_auth_config"]["auth_type"], None),
    )

    response = client.get("/v1/mcp-auth/status", params={"mcp_config_id": mcp_config.id})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "mcp_config_id": "mcp-config-1",
        "mcp_config_name": "Catalog Server",
        "mcp_server_name": "Catalog Server",
        "auth_config_id": "auth-config-1",
        "auth_type": "oauth2",
        "as_hostname": "login.example.com",
        "status": "authenticated",
        "error_context": None,
        "initiate_url": "/v1/mcp-auth/oauth2/initiate",
    }


def test_status_route_resolves_discovered_server_without_persisted_auth_config(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config(auth_config=None)
    captured: dict[str, object] = {}

    def fake_build_discovered_auth_status_response(**kwargs):
        captured.update(kwargs)
        return mcp_auth_router.MCPAuthStatusResponse(
            mcp_config_id="mcp-config-1",
            mcp_config_name="Catalog Server",
            mcp_server_name="Catalog Server",
            auth_config_id="discovered:" + "a" * 64,
            auth_type="oauth2",
            as_hostname="auth.example.com",
            status="authentication_required",
            error_context=None,
            initiate_url="/v1/mcp-auth/oauth2/initiate?discovered_flow_id=flow-1",
        )

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)
    monkeypatch.setattr(
        mcp_auth_router,
        "build_discovered_auth_status_response",
        fake_build_discovered_auth_status_response,
    )

    response = client.get("/v1/mcp-auth/status", params={"mcp_config_id": mcp_config.id})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["auth_config_id"].startswith("discovered:")
    assert response.json()["status"] == "authentication_required"
    assert captured["mcp_config"] == mcp_config
    assert captured["user"] == user


class _InMemoryDiscoveredFlowStore:
    def __init__(self, snapshot: object) -> None:
        self.snapshot = snapshot

    def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
        if (
            getattr(self.snapshot, "user_id") == user_id
            and getattr(self.snapshot, "session_binding_hash") == session_binding_hash
            and getattr(self.snapshot, "mcp_config_id") == mcp_config_id
        ):
            return self.snapshot
        return None


def _build_discovered_snapshot(
    *,
    status_value: str = "authentication_required",
    session_binding_hash: str,
    auth_config_id: str = "discovered:" + "a" * 64,
    error_context: dict[str, object] | None = None,
) -> object:
    from codemie_enterprise.mcp_auth.discovered_flow import DiscoveredOAuth2FlowSnapshot
    from codemie_enterprise.mcp_auth.models import DiscoveredOAuth2FlowConfig

    flow_config = None
    if status_value == "authentication_required":
        flow_config = DiscoveredOAuth2FlowConfig(
            authorization_url="https://auth.example.com/oauth2/authorize",
            token_url="https://auth.example.com/oauth2/token",
            client_id="https://codemie.example.com/oauth/client-metadata.json",
            client_type="public",
            client_auth_method="none",
            issuer="https://auth.example.com",
            resource="https://mcp.example.com/server",
            scopes=("catalog.read",),
        )
    return DiscoveredOAuth2FlowSnapshot(
        status=status_value,
        discovered_flow_id="flow-1",
        discovered_auth_id=auth_config_id if status_value == "authentication_required" else None,
        mcp_config_id="mcp-config-1",
        mcp_config_name="Catalog Server",
        user_id="user-1",
        session_binding_hash=session_binding_hash,
        canonical_resource="https://mcp.example.com/server",
        redirect_uri="https://codemie.example.com/v1/mcp-auth/oauth2/callback",
        issuer="https://auth.example.com",
        selected_authorization_server="https://auth.example.com",
        as_hostname="auth.example.com",
        registration_method="client_id_metadata_document",
        registration_reason_code="cimd_supported",
        registration_profile_fingerprint="f" * 64,
        current_challenge_scope="catalog.read",
        flow_config=flow_config,
        error_context=error_context or {},
    )


@pytest.mark.parametrize(
    ("store_token", "expected_status"),
    [
        (False, "authentication_required"),
        (True, "authenticated"),
    ],
)
def test_build_discovered_auth_status_response_uses_flow_store_and_tms(
    monkeypatch,
    store_token: bool,
    expected_status: str,
) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies
    from codemie_enterprise.mcp_auth.models import OAuth2TokenData
    from codemie_enterprise.mcp_auth.tms_mock import MockTokenManagementSystem

    user = _build_user()
    session_binding_hash = mcp_auth_dependencies._get_authenticated_bearer_token_hash(user)
    auth_config_id = "discovered:" + "a" * 64
    snapshot = _build_discovered_snapshot(session_binding_hash=session_binding_hash, auth_config_id=auth_config_id)
    tms = MockTokenManagementSystem()
    if store_token:
        tms.store(
            user.id,
            auth_config_id,
            OAuth2TokenData(
                access_token="stored-token",
                token_type="Bearer",
                resource="https://mcp.example.com/server",
                issuer="https://auth.example.com",
                flow_source="discovered",
            ),
        )

    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_mcp_auth_discovered_flow_store",
        _InMemoryDiscoveredFlowStore(snapshot),
    )
    monkeypatch.setattr(mcp_auth_dependencies, "_tms", tms)

    response = mcp_auth_dependencies.build_discovered_auth_status_response(
        mcp_config=_build_mcp_config(auth_config=None),
        user=user,
    )

    assert response["auth_config_id"] == auth_config_id
    assert response["status"] == expected_status
    assert response["auth_type"] == "oauth2"
    assert response["as_hostname"] == "auth.example.com"
    assert response["initiate_url"] == "/v1/mcp-auth/oauth2/initiate?discovered_flow_id=flow-1"


def test_build_discovered_auth_status_response_returns_config_error_snapshot(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    user = _build_user()
    session_binding_hash = mcp_auth_dependencies._get_authenticated_bearer_token_hash(user)
    snapshot = _build_discovered_snapshot(
        status_value="config_error",
        session_binding_hash=session_binding_hash,
        error_context={
            "server_name": "Catalog Server",
            "failure_reasons": ("dcr_timeout",),
            "action": "Configure auth_config with pre-registered credentials for this server",
        },
    )
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_mcp_auth_discovered_flow_store",
        _InMemoryDiscoveredFlowStore(snapshot),
    )
    monkeypatch.setattr(mcp_auth_dependencies, "_tms", object())

    response = mcp_auth_dependencies.build_discovered_auth_status_response(
        mcp_config=_build_mcp_config(auth_config=None),
        user=user,
    )

    assert response["auth_config_id"] is None
    assert response["status"] == "config_error"
    assert response["as_hostname"] == "auth.example.com"
    assert response["error_context"]["failure_reasons"] == ("dcr_timeout",)
    assert response["initiate_url"] is None


@pytest.mark.parametrize(
    ("status_value", "error_context"),
    [
        ("authentication_required", None),
        ("session_expired", None),
        ("config_error", "Authentication configuration is invalid. Contact your administrator."),
    ],
)
def test_status_route_maps_non_authenticated_statuses(
    monkeypatch,
    app_client,
    status_value: str,
    error_context: str | None,
) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config(auth_config=_build_auth_config("saml"))
    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)
    monkeypatch.setattr(mcp_auth_router, "_require_initialized_tms", lambda: object())
    monkeypatch.setattr(
        mcp_auth_router,
        "_evaluate_auth_status",
        lambda **kwargs: (status_value, kwargs["raw_auth_config"]["auth_type"], error_context),
    )

    response = client.get("/v1/mcp-auth/status", params={"mcp_config_id": mcp_config.id})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == status_value
    assert response.json()["error_context"] == error_context
    assert response.json()["auth_type"] == "saml"
    assert response.json()["as_hostname"] == "idp.example.com"
    assert response.json()["initiate_url"] == "/v1/mcp-auth/saml/initiate"


def test_status_route_returns_503_when_tms_is_uninitialized(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user()
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: _build_mcp_config())

    def _raise_uninitialized() -> object:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="MCP auth temporarily unavailable",
            details="MCP auth service is not initialized",
            help="Try again after the MCP auth service finishes initializing.",
        )

    monkeypatch.setattr(mcp_auth_router, "_require_initialized_tms", _raise_uninitialized)

    response = client.get("/v1/mcp-auth/status", params={"mcp_config_id": "mcp-config-1"})

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["error"]["details"] == "MCP auth service is not initialized"


def test_status_route_requires_non_empty_mcp_config_id_query_param(app_client) -> None:
    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user()

    missing_response = client.get("/v1/mcp-auth/status")
    empty_response = client.get("/v1/mcp-auth/status", params={"mcp_config_id": ""})

    assert missing_response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert empty_response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_status_route_returns_not_found_for_missing_config(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user()
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: None)

    response = client.get("/v1/mcp-auth/status", params={"mcp_config_id": "missing-config"})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["message"] == "MCP configuration not found"


def test_status_route_rejects_private_non_owned_config(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user(id="other-user")
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig,
        "find_by_id",
        lambda config_id: _build_mcp_config(owner_id="owner-user"),
    )

    response = client.get("/v1/mcp-auth/status", params={"mcp_config_id": "mcp-config-1"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error"]["message"] == "Access denied"


@pytest.mark.parametrize(
    "auth_config",
    [
        None,
        {"id": "auth-config-1", "auth_type": "custom"},
    ],
)
def test_status_route_rejects_invalid_or_unsupported_auth_configs(
    monkeypatch,
    app_client,
    auth_config: dict[str, object] | None,
) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user()
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig,
        "find_by_id",
        lambda config_id: _build_mcp_config(auth_config=auth_config),
    )

    response = client.get("/v1/mcp-auth/status", params={"mcp_config_id": "mcp-config-1"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["message"] == "Invalid MCP auth configuration"


def test_disabled_status_route_preserves_story_1_4_payload() -> None:
    client = _build_disabled_client()

    response = client.get("/v1/mcp-auth/status")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {
        "feature": "MCP Authorization",
        "state": "inactive — enterprise package not installed or MCP_AUTH_ENABLED not set",
        "action": "Enable MCP_AUTH_ENABLED and install the enterprise package",
    }


def test_status_route_and_toolkit_payload_share_hostname_and_initiate_url(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    auth_config = _build_auth_config("saml")
    mcp_config = _build_mcp_config(name="Shared Server", auth_config=auth_config)
    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)
    monkeypatch.setattr(mcp_auth_router, "_require_initialized_tms", lambda: object())
    monkeypatch.setattr(
        mcp_auth_router,
        "_evaluate_auth_status",
        lambda **kwargs: ("session_expired", kwargs["raw_auth_config"]["auth_type"], None),
    )

    route_response = client.get("/v1/mcp-auth/status", params={"mcp_config_id": mcp_config.id})
    toolkit_payload = MCPToolkitService._build_auth_required_server_payload(
        caught_payload={"status": "session_expired", "auth_type": "saml"},
        mcp_server=SimpleNamespace(
            name=mcp_config.name,
            mcp_config_id=mcp_config.id,
            config=SimpleNamespace(auth_config=auth_config),
        ),
        execution_context=MCPExecutionContext(user_id=user.id, assistant_id="assistant-1"),
    )

    assert route_response.status_code == status.HTTP_200_OK
    assert route_response.json()["as_hostname"] == toolkit_payload["as_hostname"]
    assert route_response.json()["initiate_url"] == toolkit_payload["initiate_url"]


def test_status_route_returns_authenticated_after_saml_reauth_replaces_expired_credentials(
    monkeypatch,
    app_client,
) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config(auth_config=_build_auth_config("saml"))
    tms = SimpleNamespace(status="session_expired", error_context="SAML session expired")

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)
    monkeypatch.setattr(mcp_auth_router, "_require_initialized_tms", lambda: tms)
    monkeypatch.setattr(
        mcp_auth_router,
        "_evaluate_auth_status",
        lambda **kwargs: (kwargs["tms"].status, kwargs["raw_auth_config"]["auth_type"], kwargs["tms"].error_context),
    )

    expired_response = client.get("/v1/mcp-auth/status", params={"mcp_config_id": mcp_config.id})

    tms.status = "authenticated"
    tms.error_context = None
    authenticated_response = client.get("/v1/mcp-auth/status", params={"mcp_config_id": mcp_config.id})

    assert expired_response.status_code == status.HTTP_200_OK
    assert expired_response.json()["status"] == "session_expired"
    assert expired_response.json()["auth_type"] == "saml"
    assert expired_response.json()["error_context"] == "SAML session expired"
    assert authenticated_response.status_code == status.HTTP_200_OK
    assert authenticated_response.json()["status"] == "authenticated"
    assert authenticated_response.json()["auth_type"] == "saml"
    assert authenticated_response.json()["error_context"] is None
