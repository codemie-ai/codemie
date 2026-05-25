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
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies


def _build_enabled_client() -> TestClient:
    from codemie.enterprise.mcp_auth.router import enabled_router

    app = FastAPI()
    app.include_router(enabled_router)
    return TestClient(app)


def _build_disabled_client() -> TestClient:
    from codemie.enterprise.mcp_auth.router import router as disabled_router

    app = FastAPI()
    app.include_router(disabled_router)
    return TestClient(app)


def _build_auth_config(*, client_type: str = "public") -> dict[str, object]:
    return {
        "id": "auth-config-1",
        "auth_type": "oauth2",
        "authorization_url": "https://idp.example.com/oauth2/authorize",
        "token_url": "https://idp.example.com/oauth2/token",
        "client_id": "client-1",
        "client_type": client_type,
        "client_secret": "encrypted-secret",
        "scopes": ["openid", "profile"],
        "token_delivery": {"method": "header"},
    }


def _build_mcp_config(
    *,
    name: str = "Demo MCP Server",
    auth_config: dict[str, object] | None = None,
    url: str = "https://mcp.example.com/server",
) -> SimpleNamespace:
    return SimpleNamespace(
        id="mcp-config-1",
        name=name,
        config=SimpleNamespace(
            url=url,
            auth_config=auth_config or _build_auth_config(),
        ),
    )


def _build_state_payload(*, ts: int = 4_102_444_800) -> SimpleNamespace:
    return SimpleNamespace(
        auth_config_id="auth-config-1",
        user_id="user-1",
        session_binding_hash="a" * 64,
        ts=ts,
    )


def _build_pkce_state(**overrides: object) -> SimpleNamespace:
    payload = {
        "code_verifier": "verifier-123",
        "user_id": "user-1",
        "auth_config_id": "auth-config-1",
        "session_binding_hash": "a" * 64,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _set_default_bridge_state(monkeypatch):
    pkce_store = MagicMock()
    tms = MagicMock()
    exchange = MagicMock(return_value=SimpleNamespace(access_token="access-token", token_type="Bearer"))
    decrypt = MagicMock(return_value="plain-secret")
    reverse_lookup = MagicMock(return_value=_build_mcp_config())

    monkeypatch.setattr(mcp_auth_dependencies, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setattr(mcp_auth_dependencies, "_pkce_store", pkce_store)
    monkeypatch.setattr(mcp_auth_dependencies, "_tms", tms)
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: _build_state_payload(),
    )
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_validate_callback_auth_config",
        lambda raw_auth_config, server_name, auth_config_id: SimpleNamespace(**raw_auth_config),
    )
    monkeypatch.setattr(mcp_auth_dependencies, "decrypt_confidential_client_secret", decrypt)
    monkeypatch.setattr(mcp_auth_dependencies, "_exchange_callback_code", exchange)
    monkeypatch.setattr(mcp_auth_dependencies, "_load_callback_mcp_config", reverse_lookup)
    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "https://codemie.example.com")
    monkeypatch.setattr(mcp_auth_dependencies.config, "FRONTEND_URL", "https://frontend.example.com/app")

    return pkce_store, tms, exchange, decrypt, reverse_lookup


def _assert_has_callback_script(response_text: str) -> None:
    assert '<script src="/v1/mcp-auth/oauth2/callback-page.js"></script>' in response_text
    assert '<script>' not in response_text


def test_disabled_callback_path_preserves_story_1_4_payload() -> None:
    client = _build_disabled_client()

    response = client.get("/v1/mcp-auth/oauth2/callback")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {
        "feature": "MCP Authorization",
        "state": "inactive — enterprise package not installed or MCP_AUTH_ENABLED not set",
        "action": "Enable MCP_AUTH_ENABLED and install the enterprise package",
    }


def test_enabled_callback_keeps_query_params_optional() -> None:
    client = _build_enabled_client()

    response = client.get("/v1/mcp-auth/oauth2/callback")

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("text/html")
    assert "Authentication session could not be verified. Return to CodeMie and try again." in response.text
    _assert_has_callback_script(response.text)


def test_enabled_callback_script_route_returns_first_party_javascript() -> None:
    client = _build_enabled_client()

    response = client.get("/v1/mcp-auth/oauth2/callback-page.js")

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("application/javascript")
    assert "window.opener.postMessage" in response.text
    assert "mcp_auth_callback" in response.text
    assert 'status: \'success\'' in response.text
    assert 'status: \'error\'' in response.text
    assert 'Authentication successful! You can close this tab.' in response.text
    assert 'Authentication successful! Open CodeMie to continue.' in response.text


def test_disabled_callback_script_route_is_absent() -> None:
    client = _build_disabled_client()

    response = client.get("/v1/mcp-auth/oauth2/callback-page.js")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_enabled_callback_renders_idp_error_and_skips_consume_exchange_store(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, tms, exchange, _, _ = _set_default_bridge_state(monkeypatch)

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={
            "error": "access_denied",
            "error_description": "User denied access",
            "error_uri": "https://idp.example.com/error-info",
            "code": "ignored-code",
            "state": "opaque-state",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert "access_denied" in response.text
    assert "User denied access" in response.text
    assert "https://idp.example.com/error-info" in response.text
    assert "Demo MCP Server" in response.text
    assert 'data-callback-result="error"' in response.text
    assert pkce_store.consume.call_count == 0
    assert exchange.call_count == 0
    assert tms.store.call_count == 0


def test_enabled_callback_idp_error_can_render_server_name_without_tms(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, _, exchange, _, reverse_lookup = _set_default_bridge_state(monkeypatch)
    monkeypatch.setattr(mcp_auth_dependencies, "_tms", None)
    reverse_lookup.return_value = _build_mcp_config(name="Demo MCP Server")

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={
            "error": "access_denied",
            "error_description": "User denied access",
            "state": "opaque-state",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Demo MCP Server" in response.text
    assert pkce_store.consume.call_count == 0
    assert exchange.call_count == 0


def test_enabled_callback_invalid_state_returns_secure_error_without_server_name(monkeypatch) -> None:
    client = _build_enabled_client()
    _set_default_bridge_state(monkeypatch)
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: (_ for _ in ()).throw(
            mcp_auth_dependencies.CallbackPageError(
                "Authentication session could not be verified. Return to CodeMie and try again."
            )
        ),
    )

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "bad-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Authentication session could not be verified. Return to CodeMie and try again." in response.text
    assert "MCP server:" not in response.text


def test_enabled_callback_expired_or_consumed_state_returns_expired_message(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, _, exchange, _, _ = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state()
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: _build_state_payload(ts=0),
    )

    expired_response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "expired-state"},
    )

    assert pkce_store.consume.call_count == 0

    pkce_store.consume.return_value = None
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: _build_state_payload(),
    )
    consumed_response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "consumed-state"},
    )

    assert expired_response.status_code == status.HTTP_200_OK
    assert "Authentication session expired. Return to CodeMie and try again." in expired_response.text
    assert consumed_response.status_code == status.HTTP_200_OK
    assert "Authentication session expired. Return to CodeMie and try again." in consumed_response.text
    assert exchange.call_count == 0


def test_enabled_callback_redis_unavailable_returns_service_message(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, _, exchange, _, _ = _set_default_bridge_state(monkeypatch)
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_consume_callback_pkce_state",
        lambda pkce_store, state, auth_config_id: (_ for _ in ()).throw(
            mcp_auth_dependencies.CallbackPageError(
                "Authentication session could not be verified. Return to CodeMie and try again when the service is available.",
                auth_config_id=auth_config_id,
                bridge_error_code="runtime_error",
            )
        ),
    )

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "try again when the service is available" in response.text
    assert 'data-auth-config-id="auth-config-1"' in response.text
    assert 'data-bridge-error-code="runtime_error"' in response.text
    assert exchange.call_count == 0


def test_enabled_callback_enforces_state_pkce_equality_before_token_exchange(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, _, exchange, _, _ = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state(user_id="other-user")

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Authentication session could not be verified. Return to CodeMie and try again." in response.text
    assert exchange.call_count == 0


def test_enabled_callback_uses_reverse_lookup_and_core_secret_decryption(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, tms, exchange, decrypt, reverse_lookup = _set_default_bridge_state(monkeypatch)
    reverse_lookup.return_value = _build_mcp_config(auth_config=_build_auth_config(client_type="confidential"))
    pkce_store.consume.return_value = _build_pkce_state()

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Completing authentication..." in response.text
    assert "Authentication complete. Return to CodeMie to continue using the MCP server." in response.text
    assert "auth-code" not in response.text
    assert "plain-secret" not in response.text
    assert "access-token" not in response.text
    assert 'data-callback-result="success"' in response.text
    assert 'data-auth-config-id="auth-config-1"' in response.text
    assert 'data-target-origin="https://frontend.example.com"' in response.text
    assert 'data-idp-error-code=' not in response.text
    assert 'data-bridge-error-code=' not in response.text
    _assert_has_callback_script(response.text)
    reverse_lookup.assert_called_once_with("auth-config-1")
    decrypt.assert_called_once()
    assert exchange.call_args.kwargs["client_secret"] == "plain-secret"
    assert exchange.call_args.kwargs["auth_config_id"] == "auth-config-1"
    tms.store.assert_called_once()


def test_enabled_callback_uses_discovered_snapshot_without_mcp_config_reverse_lookup(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, tms, exchange, decrypt, reverse_lookup = _set_default_bridge_state(monkeypatch)
    discovered_auth_id = "discovered:" + "a" * 64
    flow_config = SimpleNamespace(
        authorization_url="https://auth.example.com/oauth2/authorize",
        token_url="https://auth.example.com/oauth2/token",
        client_id="client-1",
        client_type="public",
        client_auth_method="none",
        client_secret=None,
        issuer="https://auth.example.com",
        resource="https://mcp.example.com/api/mcp",
        scopes=("read",),
    )
    snapshot = SimpleNamespace(
        status="authentication_required",
        discovered_flow_id="flow-1",
        discovered_auth_id=discovered_auth_id,
        mcp_config_id="mcp-config-1",
        mcp_config_name="Discovered MCP",
        user_id="user-1",
        session_binding_hash="a" * 64,
        canonical_resource="https://mcp.example.com/api/mcp",
        redirect_uri="https://codemie.example.com/v1/mcp-auth/oauth2/callback",
        flow_config=flow_config,
    )
    pkce_store.consume.return_value = _build_pkce_state(auth_config_id=discovered_auth_id, discovered_flow_id="flow-1")
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: SimpleNamespace(
            auth_config_id=discovered_auth_id,
            user_id="user-1",
            session_binding_hash="a" * 64,
            ts=4_102_444_800,
            discovered_flow_id="flow-1",
        ),
    )
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_load_discovered_flow_snapshot_or_error",
        MagicMock(return_value=snapshot),
    )
    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "https://changed.example.com")
    reverse_lookup.side_effect = AssertionError("persisted auth_config lookup must not run for discovered callback")

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Discovered MCP" in response.text
    assert exchange.call_args.kwargs["auth_config"] == flow_config
    assert exchange.call_args.kwargs["auth_config_id"] == discovered_auth_id
    assert exchange.call_args.kwargs["redirect_uri"] == "https://codemie.example.com/v1/mcp-auth/oauth2/callback"
    assert exchange.call_args.kwargs["resource"] == "https://mcp.example.com/api/mcp"
    assert exchange.call_args.kwargs["client_secret"] is None
    assert decrypt.call_count == 0
    tms.store.assert_called_once()
    assert tms.store.call_args.args[1] == discovered_auth_id


def test_enabled_callback_uses_recovery_snapshot_before_persisted_fallback(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, tms, exchange, decrypt, reverse_lookup = _set_default_bridge_state(monkeypatch)
    recovery_auth_config = SimpleNamespace(
        authorization_url="https://auth.example.com/oauth2/authorize",
        token_url="https://auth.example.com/oauth2/token",
        client_id="client-1",
        client_type="public",
        scopes=("read", "write", "admin"),
    )
    snapshot = SimpleNamespace(
        recovery_flow_id="rf-1",
        mcp_config_id="mcp-config-1",
        mcp_config_name="Recovered MCP",
        user_id="user-1",
        session_binding_hash="a" * 64,
        auth_config_id="auth-config-1",
        token_storage_auth_config_id="auth-config-1",
        auth_config=recovery_auth_config,
        resource=None,
        resource_metadata_url_internal="https://mcp.example.com/.well-known/oauth-protected-resource?tenant=secret",
    )
    pkce_store.consume.return_value = _build_pkce_state(recovery_flow_id="rf-1")
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: SimpleNamespace(
            auth_config_id="auth-config-1",
            user_id="user-1",
            session_binding_hash="a" * 64,
            ts=4_102_444_800,
            recovery_flow_id="rf-1",
        ),
    )
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_load_recovery_snapshot_or_error",
        MagicMock(return_value=snapshot),
    )
    reverse_lookup.side_effect = AssertionError("persisted auth_config lookup must not run for recovery callback")

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Recovered MCP" in response.text
    assert exchange.call_args.kwargs["auth_config"] == recovery_auth_config
    assert exchange.call_args.kwargs["auth_config_id"] == "auth-config-1"
    assert exchange.call_args.kwargs["resource"] == (
        "https://mcp.example.com/.well-known/oauth-protected-resource?tenant=secret"
    )
    assert decrypt.call_count == 0
    tms.store.assert_called_once()
    assert tms.store.call_args.args[1] == "auth-config-1"


@pytest.mark.parametrize(
    ("state_recovery_flow_id", "pkce_recovery_flow_id"),
    [
        ("rf-1", None),
        (None, "rf-1"),
        ("rf-1", "rf-other"),
    ],
)
def test_recovery_callback_rejects_missing_or_mismatched_flow_binding(
    monkeypatch,
    state_recovery_flow_id: str | None,
    pkce_recovery_flow_id: str | None,
) -> None:
    client = _build_enabled_client()
    pkce_store, tms, exchange, _, _ = _set_default_bridge_state(monkeypatch)
    recovery_auth_config = SimpleNamespace(
        authorization_url="https://auth.example.com/oauth2/authorize",
        token_url="https://auth.example.com/oauth2/token",
        client_id="client-1",
        client_type="public",
        scopes=("read", "write", "admin"),
    )
    snapshot = SimpleNamespace(
        recovery_flow_id="rf-1",
        mcp_config_id="mcp-config-1",
        mcp_config_name="Recovered MCP",
        user_id="user-1",
        session_binding_hash="a" * 64,
        auth_config_id="auth-config-1",
        token_storage_auth_config_id="auth-config-1",
        auth_config=recovery_auth_config,
        resource="https://mcp.example.com/mcp",
        resource_metadata_url_internal=None,
    )
    pkce_overrides = {}
    if pkce_recovery_flow_id is not None:
        pkce_overrides["recovery_flow_id"] = pkce_recovery_flow_id
    pkce_store.consume.return_value = _build_pkce_state(**pkce_overrides)

    def decode_state(state, signing_key):
        payload = SimpleNamespace(
            auth_config_id="auth-config-1",
            user_id="user-1",
            session_binding_hash="a" * 64,
            ts=4_102_444_800,
        )
        if state_recovery_flow_id is not None:
            payload.recovery_flow_id = state_recovery_flow_id
        return payload

    monkeypatch.setattr(mcp_auth_dependencies, "_decode_and_verify_oauth2_callback_state", decode_state)
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_load_recovery_snapshot_or_error",
        MagicMock(return_value=snapshot),
    )

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Authentication session could not be verified. Return to CodeMie and try again." in response.text
    assert 'data-callback-result="error"' in response.text
    assert exchange.call_count == 0
    assert tms.store.call_count == 0


def test_recovery_callback_uses_discovered_confidential_snapshot_secret_without_persisted_lookup(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, tms, exchange, decrypt, reverse_lookup = _set_default_bridge_state(monkeypatch)
    discovered_auth_id = "discovered:" + "f" * 64
    recovery_auth_config = SimpleNamespace(
        authorization_url="https://auth.example.com/oauth2/authorize",
        token_url="https://auth.example.com/oauth2/token",
        client_id="client-1",
        client_type="confidential",
        client_auth_method="client_secret_basic",
        client_secret="inline-secret",
        scopes=("read", "write", "admin"),
    )
    snapshot = SimpleNamespace(
        recovery_flow_id="rf-discovered-confidential",
        mcp_config_id="mcp-config-1",
        mcp_config_name="Recovered Discovered MCP",
        user_id="user-1",
        session_binding_hash="a" * 64,
        auth_config_id=discovered_auth_id,
        token_storage_auth_config_id=discovered_auth_id,
        auth_config=recovery_auth_config,
        resource="https://mcp.example.com/mcp",
        resource_metadata_url_internal="https://mcp.example.com/.well-known/oauth-protected-resource?tenant=secret",
    )
    pkce_store.consume.return_value = _build_pkce_state(
        auth_config_id=discovered_auth_id,
        recovery_flow_id="rf-discovered-confidential",
    )
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: SimpleNamespace(
            auth_config_id=discovered_auth_id,
            user_id="user-1",
            session_binding_hash="a" * 64,
            ts=4_102_444_800,
            recovery_flow_id="rf-discovered-confidential",
        ),
    )
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_load_recovery_snapshot_or_error",
        MagicMock(return_value=snapshot),
    )
    reverse_lookup.side_effect = AssertionError("persisted auth_config lookup must not run for discovered recovery")
    assert (
        mcp_auth_dependencies._recovery_callback_client_secret(
            recovery_auth_config,
            discovered_auth_id,
            "Recovered Discovered MCP",
        )
        is None
    )

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Recovered Discovered MCP" in response.text
    assert exchange.call_args.kwargs["auth_config"] == recovery_auth_config
    assert exchange.call_args.kwargs["auth_config_id"] == discovered_auth_id
    assert exchange.call_args.kwargs["client_secret"] is None
    assert decrypt.call_count == 0
    tms.store.assert_called_once()
    assert tms.store.call_args.args[1] == discovered_auth_id


def test_enabled_callback_skips_core_secret_decryption_for_public_clients(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, tms, exchange, decrypt, reverse_lookup = _set_default_bridge_state(monkeypatch)
    reverse_lookup.return_value = _build_mcp_config(auth_config=_build_auth_config(client_type="public"))
    pkce_store.consume.return_value = _build_pkce_state()

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Completing authentication..." in response.text
    reverse_lookup.assert_called_once_with("auth-config-1")
    assert decrypt.call_count == 0
    assert exchange.call_args.kwargs["client_secret"] is None
    tms.store.assert_called_once()


def test_enabled_callback_passes_canonical_resource_to_exchange(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, _, exchange, _, reverse_lookup = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state()
    reverse_lookup.return_value = _build_mcp_config(
        url="https://MCP.Example.Com:443/api/mcp?v=1#section",
    )

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert exchange.call_args.kwargs["resource"] == "https://mcp.example.com/api/mcp"


def test_enabled_callback_invalid_resource_url_does_not_render_or_log_tainted_values(monkeypatch, caplog) -> None:
    client = _build_enabled_client()
    pkce_store, _, exchange, _, reverse_lookup = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state()
    reverse_lookup.return_value = _build_mcp_config(
        url=("https://user:secret-sentinel@mcp.example.com/api/mcp" "?access_token=token-sentinel#fragment-sentinel"),
    )

    with caplog.at_level("WARNING"):
        response = client.get(
            "/v1/mcp-auth/oauth2/callback",
            params={"code": "auth-code", "state": "opaque-state"},
        )

    rendered = f"{response.text} {caplog.text}"
    assert response.status_code == status.HTTP_200_OK
    assert "Authentication could not be completed because the MCP server configuration is invalid." in response.text
    assert exchange.call_count == 0
    assert "secret-sentinel" not in rendered
    assert "token-sentinel" not in rendered
    assert "fragment-sentinel" not in rendered
    assert "access_token" not in rendered
    assert "user:" not in rendered


def test_enabled_callback_uses_frontend_url_origin_for_target_origin(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, _, _, _, _ = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state()
    monkeypatch.setattr(mcp_auth_dependencies.config, "FRONTEND_URL", "https://frontend.example.com/nested/path")
    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "https://api.example.com")

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert 'data-target-origin="https://frontend.example.com"' in response.text
    assert 'data-target-origin="https://api.example.com"' not in response.text
    assert '"*"' not in response.text


def test_enabled_callback_adds_security_headers_on_success_and_failure(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, _, _, _, _ = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state()

    success_response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )
    pkce_store.consume.return_value = None
    failure_response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "missing-state"},
    )

    for response in (success_response, failure_response):
        assert response.status_code == status.HTTP_200_OK
        assert response.headers["content-security-policy"] == "default-src 'none'; script-src 'self'"
        assert response.headers["x-frame-options"] == "DENY"


def test_enabled_callback_tms_store_failure_returns_exact_message(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, tms, _, _, _ = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state()
    tms.store.side_effect = RuntimeError("database down")

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-security-policy"] == "default-src 'none'; script-src 'self'"
    assert response.headers["x-frame-options"] == "DENY"
    assert (
        "Authentication succeeded but credentials could not be saved. Return to CodeMie and try again." in response.text
    )
    assert "Authentication complete. Return to CodeMie to continue using the MCP server." not in response.text
    assert "Demo MCP Server" in response.text
    assert 'data-auth-config-id="auth-config-1"' in response.text
    assert 'data-bridge-error-code="credentials_store_failed"' in response.text


def test_post_verification_error_pages_include_server_name_pre_verification_do_not(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, _, exchange, _, reverse_lookup = _set_default_bridge_state(monkeypatch)
    reverse_lookup.return_value = _build_mcp_config(name="Named MCP")
    pkce_store.consume.return_value = _build_pkce_state()
    exchange.side_effect = mcp_auth_dependencies.CallbackPageError(
        "Authentication could not be completed. Return to CodeMie and try again.",
        server_name="Named MCP",
    )
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: (_ for _ in ()).throw(
            mcp_auth_dependencies.CallbackPageError(
                "Authentication session could not be verified. Return to CodeMie and try again."
            )
        )
        if state == "invalid-state"
        else _build_state_payload(),
    )

    post_verification_response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )
    pre_verification_response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "invalid-state"},
    )

    assert "Named MCP" in post_verification_response.text
    assert 'data-auth-config-id=' not in pre_verification_response.text
    assert "MCP server:" not in pre_verification_response.text


def test_trusted_error_bootstrap_uses_idp_error_when_present(monkeypatch) -> None:
    client = _build_enabled_client()
    _set_default_bridge_state(monkeypatch)
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: _build_state_payload(),
    )

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"error": "access_denied", "state": "opaque-state"},
    )

    assert 'data-auth-config-id="auth-config-1"' in response.text
    assert 'data-idp-error-code="access_denied"' in response.text
    assert 'data-bridge-error-code=' not in response.text


def test_pre_verification_errors_omit_callback_bootstrap(monkeypatch) -> None:
    client = _build_enabled_client()
    monkeypatch.setattr(mcp_auth_dependencies, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setattr(mcp_auth_dependencies, "_pkce_store", MagicMock())
    monkeypatch.setattr(mcp_auth_dependencies, "_tms", MagicMock())
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: (_ for _ in ()).throw(
            mcp_auth_dependencies.CallbackPageError(
                "Authentication session could not be verified. Return to CodeMie and try again."
            )
        ),
    )
    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "https://codemie.example.com")
    monkeypatch.setattr(mcp_auth_dependencies.config, "FRONTEND_URL", "https://frontend.example.com/app")

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "invalid-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert 'data-auth-config-id=' not in response.text
    assert 'data-target-origin=' not in response.text
    assert 'data-idp-error-code=' not in response.text
    assert 'data-bridge-error-code=' not in response.text


def test_enabled_callback_db_lookup_failure_preserves_trusted_runtime_bootstrap(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, _, exchange, _, _ = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state()
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_load_callback_mcp_config",
        lambda auth_config_id: (_ for _ in ()).throw(
            mcp_auth_dependencies.CallbackPageError(
                "Authentication could not be completed. Return to CodeMie and try again.",
                auth_config_id=auth_config_id,
                bridge_error_code="runtime_error",
            )
        ),
    )

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert 'data-auth-config-id="auth-config-1"' in response.text
    assert 'data-bridge-error-code="runtime_error"' in response.text
    assert exchange.call_count == 0


def test_enabled_callback_config_validation_import_failure_preserves_trusted_runtime_bootstrap(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, _, exchange, _, _ = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state()
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_validate_callback_auth_config",
        lambda raw_auth_config, server_name, auth_config_id: (_ for _ in ()).throw(
            mcp_auth_dependencies.CallbackPageError(
                "Authentication could not be completed. Return to CodeMie and try again.",
                auth_config_id=auth_config_id,
                bridge_error_code="runtime_error",
                server_name=server_name,
            )
        ),
    )

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Demo MCP Server" in response.text
    assert 'data-auth-config-id="auth-config-1"' in response.text
    assert 'data-bridge-error-code="runtime_error"' in response.text
    assert exchange.call_count == 0


def test_enabled_callback_invalid_resource_uri_preserves_trusted_configuration_bootstrap(monkeypatch) -> None:
    client = _build_enabled_client()
    pkce_store, _, exchange, _, _ = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state()
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "derive_resource_uri",
        lambda server_url: (_ for _ in ()).throw(
            ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid MCP server URL",
                details="bad url",
            )
        ),
    )

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "opaque-state"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Demo MCP Server" in response.text
    assert 'data-auth-config-id="auth-config-1"' in response.text
    assert 'data-bridge-error-code="configuration_error"' in response.text
    assert exchange.call_count == 0
