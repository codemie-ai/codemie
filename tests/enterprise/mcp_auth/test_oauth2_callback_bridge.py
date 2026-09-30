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

import re
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


def test_keep_callback_tab_open_flag_defaults_to_false() -> None:
    from codemie.configs.config import Config

    assert Config.model_fields["MCP_AUTH_CALLBACK_KEEP_TAB_OPEN"].default is False


def test_callback_script_closes_tab_by_default() -> None:
    client = _build_enabled_client()

    script = client.get("/v1/mcp-auth/oauth2/callback-page.js").text

    assert "const CALLBACK_KEEP_TAB_OPEN = false;" in script
    assert "window.close();" in script
    assert "if (!CALLBACK_KEEP_TAB_OPEN)" in script


def test_callback_script_keeps_tab_open_when_flag_enabled(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import _callback_pages

    monkeypatch.setattr(_callback_pages.config, "MCP_AUTH_CALLBACK_KEEP_TAB_OPEN", True)
    client = _build_enabled_client()

    script = client.get("/v1/mcp-auth/oauth2/callback-page.js").text

    assert "const CALLBACK_KEEP_TAB_OPEN = true;" in script
    # close is gated, not unconditional
    assert "if (!CALLBACK_KEEP_TAB_OPEN)" in script
    # success path still notifies the opener before deciding whether to close
    assert "window.opener.postMessage" in script
    # beacon stays truthful about the gated close
    assert "window_should_close: !CALLBACK_KEEP_TAB_OPEN," in script


def _success_branches(script: str) -> tuple[str, str]:
    """Split the success handler into its (no-opener, opener) branches."""
    no_opener_start = script.index("if (!window.opener) {")
    opener_start = script.index("} else if (authConfigId && targetOrigin) {", no_opener_start)
    error_start = script.index("if (main.dataset.callbackResult === 'error')", opener_start)
    return script[no_opener_start:opener_start], script[opener_start:error_start]


def test_callback_script_no_opener_success_branch_reports_then_closes_tab_with_fallback_message() -> None:
    script = _build_enabled_client().get("/v1/mcp-auth/oauth2/callback-page.js").text

    no_opener_branch, _ = _success_branches(script)

    assert "const CALLBACK_KEEP_TAB_OPEN = false;" in script
    # the opener is absent by design, so the beacon must not claim a lost one (that logs WARNING)
    assert (
        "sendDiagnostics({ opener_present: null, window_should_close: !CALLBACK_KEEP_TAB_OPEN });" in no_opener_branch
    )
    assert "window.close();" in no_opener_branch
    # the beacon must fire before the tab goes away
    assert no_opener_branch.index("sendDiagnostics(") < no_opener_branch.index("window.close();")
    # a blocked close still leaves the user a message after the fallback delay
    assert "if (!window.closed)" in no_opener_branch
    assert "updateMessage(CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE);" in no_opener_branch
    assert "CALLBACK_FALLBACK_DELAY_MS" in no_opener_branch
    # the no-opener page has nobody to notify
    assert "postMessage" not in no_opener_branch


def test_callback_script_no_opener_success_branch_does_not_close_when_keep_tab_open_enabled(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import _callback_pages

    monkeypatch.setattr(_callback_pages.config, "MCP_AUTH_CALLBACK_KEEP_TAB_OPEN", True)
    script = _build_enabled_client().get("/v1/mcp-auth/oauth2/callback-page.js").text

    no_opener_branch, _ = _success_branches(script)

    assert "const CALLBACK_KEEP_TAB_OPEN = true;" in script
    # close is reachable only through the gate, so the flag being true keeps the tab open
    gate, close = no_opener_branch.index("if (!CALLBACK_KEEP_TAB_OPEN) {"), no_opener_branch.index("window.close();")
    assert gate < close
    # with the tab kept open the user is told immediately, in the else branch of the gate
    keep_open_branch = no_opener_branch[no_opener_branch.index("} else {", gate) :]
    assert "updateMessage(CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE);" in keep_open_branch
    assert "window.close();" not in keep_open_branch


def test_callback_script_opener_success_branch_still_posts_message_before_closing() -> None:
    script = _build_enabled_client().get("/v1/mcp-auth/oauth2/callback-page.js").text

    _, opener_branch = _success_branches(script)

    assert "window.opener.postMessage" in opener_branch
    assert "status: 'success'" in opener_branch
    assert opener_branch.index("window.opener.postMessage") < opener_branch.index("window.close();")
    assert "updateMessage(CALLBACK_SUCCESS_CLOSE_MESSAGE);" in opener_branch


def test_callback_script_error_branch_without_opener_only_reports_diagnostics() -> None:
    """Accepted limit: with the opener cut, an IdP error code cannot reach the UI.

    The error is posted to an opener and nowhere else, so a window opened without one only
    beacons diagnostics; the UI shows its generic hint / early-close wording instead.
    """
    script = _build_enabled_client().get("/v1/mcp-auth/oauth2/callback-page.js").text

    error_branch = script[script.index("if (main.dataset.callbackResult === 'error')") :]
    posting_branch, no_opener_branch = error_branch.split("} else {", 1)

    assert "if (window.opener && authConfigId && targetOrigin && errorCode) {" in posting_branch
    assert "window.opener.postMessage" in posting_branch
    assert "sendDiagnostics({});" in no_opener_branch
    assert "postMessage" not in no_opener_branch


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
        url=("https://user:secret-sentinel@mcp.example.com/api/mcp?access_token=token-sentinel#fragment-sentinel"),
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
        assert (
            response.headers["content-security-policy"] == "default-src 'none'; script-src 'self'; connect-src 'self'"
        )
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
    assert response.headers["content-security-policy"] == "default-src 'none'; script-src 'self'; connect-src 'self'"
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
        lambda state, signing_key: (
            (_ for _ in ()).throw(
                mcp_auth_dependencies.CallbackPageError(
                    "Authentication session could not be verified. Return to CodeMie and try again."
                )
            )
            if state == "invalid-state"
            else _build_state_payload()
        ),
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


def test_callback_csp_allows_same_origin_connect_for_beacon() -> None:
    from codemie.enterprise.mcp_auth._constants import _CALLBACK_CONTENT_SECURITY_POLICY

    assert "connect-src 'self'" in _CALLBACK_CONTENT_SECURITY_POLICY


def test_callback_diagnostics_logs_and_returns_204(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import _diagnostics

    logged: list[str] = []
    monkeypatch.setattr(_diagnostics.logger, "info", lambda message, *a, **k: logged.append(message))
    monkeypatch.setattr(_diagnostics.logger, "warning", lambda message, *a, **k: logged.append(message))

    client = _build_enabled_client()
    resp = client.post(
        "/v1/mcp-auth/oauth2/callback-diagnostics",
        json={
            "result": "success",
            "auth_config_id": "discovered:abc",
            "opener_present": True,
            "target_origin": "https://app.example.com",
            "post_message_attempted": True,
            "window_should_close": True,
        },
    )

    assert resp.status_code == status.HTTP_204_NO_CONTENT
    text = "\n".join(logged)
    assert "MCP OAuth2 callback client diagnostics" in text
    assert "opener_present=True" in text
    assert "target_origin=https://app.example.com" in text


def test_callback_diagnostics_warns_on_lost_opener_and_returns_204(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import _diagnostics

    warned: list[str] = []
    monkeypatch.setattr(_diagnostics.logger, "warning", lambda message, *a, **k: warned.append(message))

    client = _build_enabled_client()
    resp = client.post(
        "/v1/mcp-auth/oauth2/callback-diagnostics",
        json={"result": "success", "opener_present": False, "target_origin": "https://app.example.com"},
    )

    assert resp.status_code == status.HTTP_204_NO_CONTENT
    assert any("opener_present=False" in message for message in warned)


def test_callback_diagnostics_logs_info_for_intentional_no_opener_success(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import _diagnostics

    warned: list[str] = []
    infos: list[str] = []
    monkeypatch.setattr(_diagnostics.logger, "warning", lambda message, *a, **k: warned.append(message))
    monkeypatch.setattr(_diagnostics.logger, "info", lambda message, *a, **k: infos.append(message))

    client = _build_enabled_client()
    resp = client.post(
        "/v1/mcp-auth/oauth2/callback-diagnostics",
        json={"result": "success", "opener_present": None, "window_should_close": True},
    )

    assert resp.status_code == status.HTTP_204_NO_CONTENT
    assert not warned
    assert len(infos) == 1


def test_callback_diagnostics_warns_with_phase_when_window_closed_before_callback(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import _diagnostics

    warned: list[str] = []
    infos: list[str] = []
    monkeypatch.setattr(_diagnostics.logger, "warning", lambda message, *a, **k: warned.append(message))
    monkeypatch.setattr(_diagnostics.logger, "info", lambda message, *a, **k: infos.append(message))

    client = _build_enabled_client()
    resp = client.post(
        "/v1/mcp-auth/oauth2/callback-diagnostics",
        json={"result": "error", "phase": "window_closed_before_callback", "waited_ms": 1200},
    )

    assert resp.status_code == status.HTTP_204_NO_CONTENT
    assert not infos
    assert len(warned) == 1
    assert "phase=window_closed_before_callback" in warned[0]


def test_callback_diagnostics_rejects_invalid_result() -> None:
    client = _build_enabled_client()
    resp = client.post(
        "/v1/mcp-auth/oauth2/callback-diagnostics",
        json={"result": "bogus", "opener_present": True},
    )
    assert resp.status_code == 422


def test_disabled_callback_diagnostics_returns_503() -> None:
    client = _build_disabled_client()
    resp = client.post("/v1/mcp-auth/oauth2/callback-diagnostics", json={"result": "success", "opener_present": True})
    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_callback_script_includes_diagnostics_beacon() -> None:
    client = _build_enabled_client()

    script = client.get("/v1/mcp-auth/oauth2/callback-page.js").text

    assert "navigator.sendBeacon" in script
    assert "/v1/mcp-auth/oauth2/callback-diagnostics" in script
    assert "post_message_error" in script
    # The beacon must fire on every branch, including the silent error branch
    # (the error page that cannot notify the opener — the case we are hunting).
    assert script.count("sendDiagnostics(") >= 4
    # postMessage is wrapped so a throw is captured, not propagated.
    assert "catch" in script


def test_callback_logs_entry_and_error_page_served(monkeypatch) -> None:
    from codemie.configs.logger import logger as shared_logger

    captured: list[str] = []
    monkeypatch.setattr(shared_logger, "info", lambda message, *a, **k: captured.append(message))
    monkeypatch.setattr(shared_logger, "warning", lambda message, *a, **k: captured.append(message))

    client = _build_enabled_client()
    client.get("/v1/mcp-auth/oauth2/callback")  # no code/state -> error page

    text = "\n".join(captured)
    assert "MCP OAuth2 callback received: has_code=False has_state=False" in text
    assert "MCP auth callback error page served" in text
    # entry log must record presence only, never raw secret values
    assert "has_error=False" in text


def test_callback_logs_success_page_served_with_target_origin(monkeypatch) -> None:
    from codemie.configs.logger import logger as shared_logger

    pkce_store, _, _, _, _ = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state()

    captured: list[str] = []
    monkeypatch.setattr(shared_logger, "info", lambda message, *a, **k: captured.append(message))

    client = _build_enabled_client()
    client.get("/v1/mcp-auth/oauth2/callback", params={"code": "auth-code", "state": "opaque-state"})

    text = "\n".join(captured)
    assert "MCP auth callback success page served" in text
    assert "target_origin=https://frontend.example.com" in text
    # secrets never logged
    assert "auth-code" not in text


def test_callback_diagnostics_timeout_result_logs_distinct_warning_message(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import _diagnostics

    warned: list[str] = []
    monkeypatch.setattr(_diagnostics.logger, "warning", lambda message, *a, **k: warned.append(message))
    monkeypatch.setattr(_diagnostics.logger, "info", lambda message, *a, **k: warned.append(message))

    client = _build_enabled_client()
    resp = client.post(
        "/v1/mcp-auth/oauth2/callback-diagnostics",
        json={
            "result": "timeout",
            "auth_config_id": "auth-config-1",
            "target_origin": "https://app.example.com",
            "waited_ms": 45000,
            "phase": "awaiting_callback",
        },
    )

    assert resp.status_code == status.HTTP_204_NO_CONTENT
    assert len(warned) == 1
    message = warned[0]
    assert "MCP OAuth2 callback never observed by client" in message
    assert "MCP OAuth2 callback client diagnostics" not in message
    assert "waited_ms=45000" in message
    assert "phase=awaiting_callback" in message
    assert (
        "opener_present" not in message
    ), "opener_present describes the bridge page's window; the parent reporting a timeout has none"
    assert "target_origin=https://app.example.com" in message
    assert "auth_config_id=auth-config-1" in message


def test_callback_diagnostics_rejects_waited_ms_over_ceiling() -> None:
    client = _build_enabled_client()
    resp = client.post(
        "/v1/mcp-auth/oauth2/callback-diagnostics",
        json={"result": "timeout", "waited_ms": 4_000_000},
    )

    assert resp.status_code == 422


def test_callback_diagnostics_sanitizes_newlines_in_logged_fields(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import _diagnostics

    logged: list[str] = []
    monkeypatch.setattr(_diagnostics.logger, "warning", lambda message, *a, **k: logged.append(message))
    monkeypatch.setattr(_diagnostics.logger, "info", lambda message, *a, **k: logged.append(message))

    client = _build_enabled_client()
    resp = client.post(
        "/v1/mcp-auth/oauth2/callback-diagnostics",
        json={
            "result": "error",
            "opener_present": True,
            "auth_config_id": "discovered:abc",
            "post_message_error": "boom\nINFO: forged log line",
            "bridge_error_code": "a\r\nb",
        },
    )

    assert resp.status_code == status.HTTP_204_NO_CONTENT
    assert len(logged) == 1
    message = logged[0]
    # CR/LF in client-controlled fields must be neutralized at the source so they
    # cannot forge log lines (CWE-117), independent of the logger's own escaping.
    assert "\n" not in message
    assert "\r" not in message
    assert "boom INFO: forged log line" in message


# ── Task 8: callback entry log and IdP-error log carry the initiate's state prefix (AC5) ──


def _extract_log_field(message: str, key: str) -> str | None:
    """Read a `key=value` field back out of an emitted log line."""
    match = re.search(rf"(?:^|\s){re.escape(key)}=(\S*)", message)
    return match.group(1) if match else None


def test_callback_entry_log_state_prefix_joins_to_initiate(monkeypatch) -> None:
    """The two lines must join on a value each side emitted independently, so the flow is
    traceable even when verification fails before auth_config_id becomes available.

    The comparison is between the two *emitted* log lines - deriving the expected value with
    _log_state_prefix would only prove the helper equals itself.
    """
    from codemie.configs.logger import logger as shared_logger

    pkce_store, _, _, _, _ = _set_default_bridge_state(monkeypatch)
    pkce_store.consume.return_value = _build_pkce_state()

    import codemie_enterprise.mcp_auth as enterprise_mcp_auth

    # Distinct characters throughout: a repeated-character state cannot reveal a wrong slice.
    known_state = "Nq8Wz3Ek5Ty7Ui1Op2As4Df6Gh9Jk0Lx-_ZcVbNm"
    auth_url = (
        "https://idp.example.com/oauth2/authorize"
        f"?client_id=client-1&redirect_uri=https://codemie.example.com/callback&state={known_state}"
    )
    monkeypatch.setattr(enterprise_mcp_auth, "OAuth2AuthConfig", SimpleNamespace(model_validate=lambda raw: raw))
    monkeypatch.setattr(
        enterprise_mcp_auth,
        "build_oauth2_initiate_response",
        lambda **kwargs: SimpleNamespace(
            auth_url=auth_url,
            redirect_uri_hostname=kwargs["redirect_uri_hostname"],
            localhost_warning=kwargs["localhost_warning"],
        ),
    )
    monkeypatch.setattr(enterprise_mcp_auth, "MCPAuthRedisUnavailable", RuntimeError)

    initiate_logs: list[str] = []
    monkeypatch.setattr(shared_logger, "info", lambda message, *a, **k: initiate_logs.append(message))

    # Drive the known state through initiate first, mirroring how a real flow issues it.
    initiate_response = mcp_auth_dependencies.build_oauth2_initiate_response(
        raw_auth_config=_build_auth_config(),
        user=SimpleNamespace(id="user-1", auth_token="Bearer token-123"),
        auth_config_id="auth-config-1",
        mcp_server_url="https://mcp.example.com/",
        mcp_config_id="mcp-config-1",
    )
    assert initiate_response.auth_url == auth_url

    issued = [m for m in initiate_logs if m.startswith("MCP OAuth2 initiate issued:")]
    assert len(issued) == 1
    initiate_state = _extract_log_field(issued[0], "state")
    assert initiate_state and initiate_state != known_state, "initiate must emit a truncated state"

    captured: list[str] = []
    monkeypatch.setattr(shared_logger, "info", lambda message, *a, **k: captured.append(message))

    client = _build_enabled_client()
    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": known_state},
    )

    assert response.status_code == status.HTTP_200_OK
    entry_logs = [message for message in captured if message.startswith("MCP OAuth2 callback received:")]
    assert len(entry_logs) == 1
    callback_state = _extract_log_field(entry_logs[0], "state")

    assert (
        callback_state == initiate_state
    ), "the callback line must join to the initiate line on the value each side actually emitted"
    assert known_state not in entry_logs[0]


def test_callback_idp_error_log_includes_state_prefix(monkeypatch) -> None:
    """The IdP-error branch log must carry the same state prefix as the entry log, so it stays
    joinable to initiate even when auth_config_id cannot be resolved from the state."""
    from codemie.configs.logger import logger as shared_logger
    from codemie.enterprise.mcp_auth._common import _log_state_prefix

    client = _build_enabled_client()
    _set_default_bridge_state(monkeypatch)
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: _build_state_payload(),
    )

    captured: list[str] = []
    monkeypatch.setattr(shared_logger, "warning", lambda message, *a, **k: captured.append(message))

    known_state = "e" * 40
    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"error": "access_denied", "state": known_state},
    )

    assert response.status_code == status.HTTP_200_OK
    idp_error_logs = [message for message in captured if "identity provider error" in message]
    assert len(idp_error_logs) == 1
    assert f"state={_log_state_prefix(known_state)}" in idp_error_logs[0]


# ── Code-review fix: the callback logs `state` before it is verified (AC7, CWE-117) ──


def test_callback_entry_log_neutralizes_control_characters_in_state(monkeypatch) -> None:
    """`state` reaches the entry log straight from an unauthenticated query parameter, before any
    verification. Truncating to 12 characters bounds the injected text but does not neutralize it,
    so a CR/LF in the first 12 characters forged a second log line."""
    from codemie.configs.logger import logger as shared_logger

    _set_default_bridge_state(monkeypatch)

    captured: list[str] = []
    monkeypatch.setattr(shared_logger, "info", lambda message, *a, **k: captured.append(message))

    client = _build_enabled_client()
    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"code": "auth-code", "state": "a\nWARNING: forged line"},
    )

    assert response.status_code == status.HTTP_200_OK
    entry_logs = [message for message in captured if message.startswith("MCP OAuth2 callback received:")]
    assert len(entry_logs) == 1
    message = entry_logs[0]
    assert "\n" not in message
    assert "\r" not in message
    assert "state=a WARNING: " in message


def test_callback_idp_error_log_neutralizes_control_characters_in_state(monkeypatch) -> None:
    """Same exposure on the identity-provider-error branch, which also logs the raw state."""
    from codemie.configs.logger import logger as shared_logger

    client = _build_enabled_client()
    _set_default_bridge_state(monkeypatch)
    monkeypatch.setattr(
        mcp_auth_dependencies,
        "_decode_and_verify_oauth2_callback_state",
        lambda state, signing_key: _build_state_payload(),
    )

    captured: list[str] = []
    monkeypatch.setattr(shared_logger, "warning", lambda message, *a, **k: captured.append(message))

    response = client.get(
        "/v1/mcp-auth/oauth2/callback",
        params={"error": "access_denied", "state": "e\r\nERROR: forged"},
    )

    assert response.status_code == status.HTTP_200_OK
    idp_error_logs = [message for message in captured if "identity provider error" in message]
    assert len(idp_error_logs) == 1
    message = idp_error_logs[0]
    assert "\n" not in message
    assert "\r" not in message
