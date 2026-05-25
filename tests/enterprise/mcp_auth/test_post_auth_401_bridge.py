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
from types import SimpleNamespace

import pytest

from codemie.enterprise.mcp_auth import dependencies
from codemie.service.mcp.models import MCPExecutionContext, MCPServerConfig


def _server_config() -> MCPServerConfig:
    return MCPServerConfig(
        url="https://mcp.example.com/mcp",
        type="streamable-http",
        auth_config={
            "id": "auth-config-1",
            "auth_type": "oauth2",
            "authorization_url": "https://auth.example.com/oauth2/authorize",
            "token_url": "https://auth.example.com/oauth2/token",
            "client_id": "client-1",
            "client_type": "public",
            "scopes": ["read"],
            "token_delivery": {"method": "header", "key": "Authorization"},
        },
        mcp_config_id="mcp-config-1",
        mcp_config_name="OneHub",
    )


def _context(**overrides: object) -> MCPExecutionContext:
    data = {"user_id": "user-1", "conversation_id": "conversation-1"}
    data.update(overrides)
    return MCPExecutionContext(**data)


class FakeTMS:
    def __init__(self, result: object | None = None, exc: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._result = result or SimpleNamespace(access_token="fresh-access", token_type="Bearer")
        self._exc = exc

    def force_refresh(self, user_id: str, auth_config_id: str) -> object:
        self.calls.append((user_id, auth_config_id))
        if self._exc is not None:
            raise self._exc
        return self._result


class FakeDiscoveredFlowStore:
    def __init__(self, snapshot: object | None = None) -> None:
        self.snapshot = snapshot
        self.stored: list[object] = []

    def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
        del user_id, session_binding_hash, mcp_config_id
        return self.snapshot

    def store(self, snapshot: object) -> None:
        self.stored.append(snapshot)
        self.snapshot = snapshot


def test_post_auth_401_bridge_returns_retry_header_after_successful_force_refresh(monkeypatch) -> None:
    tms = FakeTMS()
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", tms)

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=_server_config(),
        execution_context=_context(auth_headers={"Authorization": "Bearer old-access"}),
    )

    assert result is not None
    assert result.retry_auth_headers == {"Authorization": "Bearer fresh-access"}
    assert result.auth_exception is None
    assert tms.calls == [("user-1", "auth-config-1")]


def test_post_auth_401_bridge_logs_retry_401_and_does_not_force_refresh_twice(monkeypatch) -> None:
    tms = FakeTMS()
    warnings: list[str] = []
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", tms)
    monkeypatch.setattr(dependencies.logger, "warning", lambda message: warnings.append(message))
    server_config = _server_config()
    execution_context = _context(auth_headers={"Authorization": "Bearer old-access"})

    first_result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=server_config,
        execution_context=execution_context,
    )
    second_result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=server_config,
        execution_context=execution_context,
        refresh_allowed=False,
    )

    assert first_result.retry_auth_headers == {"Authorization": "Bearer fresh-access"}
    assert second_result.retry_auth_headers is None
    assert second_result.auth_exception.payload["servers"][0]["reason"] == "retry_401_after_refresh"
    assert tms.calls == [("user-1", "auth-config-1")]
    captured_log = "\n".join(warnings)
    assert "retry returned 401 after OAuth2 refresh" in captured_log
    assert "mcp_config_id=mcp-config-1" in captured_log
    assert "auth_config_id=auth-config-1" in captured_log
    assert "fresh-access" not in captured_log
    assert "old-access" not in captured_log


@pytest.mark.parametrize(
    ("exception_factory", "reason"),
    [
        (lambda auth: auth.TokenNotFound("token_not_found"), "token_not_found"),
        (lambda auth: auth.ReAuthenticationRequired("refresh_invalid_grant"), "reauth_required"),
        (lambda auth: auth.TMSUnavailable("refresh_timeout"), "tms_unavailable"),
        (lambda auth: auth.TMSPersistenceError("db_persistence_error"), "tms_persistence_error"),
        (lambda auth: auth.TokenRefreshError("refresh_unexpected_response"), "token_refresh_error"),
        (lambda auth: auth.TMSCryptoError("invalid_token_payload"), "reauth_required"),
        (lambda auth: auth.TMSAuditError("audit_unavailable"), "tms_persistence_error"),
    ],
)
def test_post_auth_401_bridge_maps_force_refresh_failures(monkeypatch, exception_factory, reason: str) -> None:
    import codemie_enterprise.mcp_auth as enterprise_auth

    tms = FakeTMS(exc=exception_factory(enterprise_auth))
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", tms)

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=_server_config(),
        execution_context=_context(),
    )

    assert result is not None
    assert result.retry_auth_headers is None
    assert result.auth_exception is not None
    server = result.auth_exception.payload["servers"][0]
    assert server["status"] == "session_expired"
    assert server["reason"] == reason
    assert server["action"] == "reauthenticate"
    assert server["action_label"] == "Re-authenticate"


@pytest.mark.parametrize(
    ("exception_factory", "expected_level", "expected_reason"),
    [
        (lambda auth: auth.TokenNotFound("token_not_found"), "warning", "token_not_found"),
        (lambda auth: auth.ReAuthenticationRequired("refresh_invalid_grant"), "warning", "reauth_required"),
        (lambda auth: auth.TMSUnavailable("refresh_timeout"), "error", "tms_unavailable"),
        (lambda auth: auth.TMSPersistenceError("db_persistence_error"), "error", "tms_persistence_error"),
        (lambda auth: auth.TokenRefreshError("refresh_unexpected_response"), "error", "token_refresh_error"),
        (lambda auth: auth.TMSCryptoError("invalid_token_payload"), "warning", "reauth_required"),
        (lambda auth: auth.TMSAuditError("audit_unavailable"), "error", "tms_persistence_error"),
    ],
)
def test_post_auth_401_bridge_logs_force_refresh_failures(
    monkeypatch,
    exception_factory,
    expected_level: str,
    expected_reason: str,
) -> None:
    import codemie_enterprise.mcp_auth as enterprise_auth

    events: list[tuple[str, str]] = []
    tms = FakeTMS(exc=exception_factory(enterprise_auth))
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", tms)
    monkeypatch.setattr(dependencies.logger, "warning", lambda message: events.append(("warning", message)))
    monkeypatch.setattr(dependencies.logger, "info", lambda message: events.append(("info", message)))
    monkeypatch.setattr(dependencies.logger, "error", lambda message: events.append(("error", message)))

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=_server_config(),
        execution_context=_context(auth_headers={"Authorization": "Bearer old-access"}),
    )

    assert result.retry_auth_headers is None
    assert result.auth_exception.payload["servers"][0]["reason"] == expected_reason
    assert events == [
        (
            expected_level,
            (
                "MCP post-auth OAuth2 refresh failed: "
                f"mcp_config_id=mcp-config-1, auth_config_id=auth-config-1, reason={expected_reason}, "
                f"failure_type={type(tms._exc).__name__}"
            ),
        )
    ]
    assert "old-access" not in events[0][1]
    assert "refresh_invalid_grant" not in events[0][1]


@pytest.mark.parametrize(
    ("header", "reason"),
    [
        (None, "missing_www_authenticate"),
        ('Bearer error="invalid_request"', "unsupported_bearer_error"),
    ],
)
def test_post_auth_401_bridge_prompts_without_calling_tms_for_no_refresh_decisions(
    monkeypatch,
    header: str | None,
    reason: str,
) -> None:
    tms = FakeTMS()
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", tms)

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header=header,
        server_config=_server_config(),
        execution_context=_context(),
    )

    assert result is not None
    assert result.retry_auth_headers is None
    assert tms.calls == []
    server = result.auth_exception.payload["servers"][0]
    assert server["reason"] == reason
    assert server["initiate_url"] == "/v1/mcp-auth/oauth2/initiate"


def test_post_auth_401_bridge_detects_configured_discovered_identity_conflict(monkeypatch) -> None:
    tms = FakeTMS()
    warnings: list[str] = []
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", tms)
    monkeypatch.setattr(dependencies.logger, "warning", lambda message: warnings.append(message))

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=_server_config(),
        execution_context=_context(oauth2_auth_config_id="discovered:mcp-config-1:binding"),
    )

    assert result is not None
    assert result.retry_auth_headers is None
    assert tms.calls == []
    server = result.auth_exception.payload["servers"][0]
    assert server["reason"] == "auth_identity_conflict"
    assert "auth_config_id" not in server
    assert "initiate_url" not in server
    assert warnings == [
        (
            "MCP post-auth OAuth2 identity conflict: mcp_config_id=mcp-config-1, "
            "configured_auth_config_id=auth-config-1, "
            "discovered_auth_config_id=discovered:mcp-config-1:binding"
        )
    ]


def test_post_auth_401_bridge_prompts_non_oauth_config_without_oauth_payload(monkeypatch) -> None:
    tms = FakeTMS()
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", tms)
    server_config = _server_config()
    server_config.auth_config = {"id": "saml-auth-1", "auth_type": "saml"}

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_request"',
        server_config=server_config,
        execution_context=_context(),
    )

    server = result.auth_exception.payload["servers"][0]
    assert tms.calls == []
    assert server["auth_config_id"] == "saml-auth-1"
    assert server["auth_type"] == "saml"
    assert "initiate_url" not in server


@pytest.mark.parametrize(
    ("token_type", "expected_retry_header", "expected_reason"),
    [
        ("bearer", "Bearer fresh-access", None),
        ("Basic", None, "refresh_result_invalid"),
    ],
)
def test_post_auth_401_bridge_normalizes_or_rejects_refreshed_token_type(
    monkeypatch,
    token_type: str,
    expected_retry_header: str | None,
    expected_reason: str | None,
) -> None:
    tms = FakeTMS(result=SimpleNamespace(access_token="fresh-access", token_type=token_type))
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", tms)

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=_server_config(),
        execution_context=_context(),
    )

    if expected_retry_header is not None:
        assert result.retry_auth_headers == {"Authorization": expected_retry_header}
        assert result.auth_exception is None
    else:
        assert result.retry_auth_headers is None
        assert result.auth_exception.payload["servers"][0]["reason"] == expected_reason


@pytest.mark.parametrize(
    ("execution_context", "expected_reason"),
    [
        (_context(user_id=None), "missing_user_id"),
        (_context(oauth2_auth_config_id=None), "missing_auth_config_id"),
    ],
)
def test_post_auth_401_bridge_prompts_when_oauth2_identity_is_incomplete(
    monkeypatch,
    execution_context: MCPExecutionContext,
    expected_reason: str,
) -> None:
    tms = FakeTMS()
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", tms)
    server_config = MCPServerConfig(
        url="https://mcp.example.com/mcp",
        type="streamable-http",
        mcp_config_id="mcp-config-1",
        mcp_config_name="OneHub",
    )

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=server_config,
        execution_context=execution_context,
    )

    assert result.retry_auth_headers is None
    assert tms.calls == []
    server = result.auth_exception.payload["servers"][0]
    assert server["reason"] == expected_reason


def test_post_auth_401_bridge_uses_discovered_oauth2_identity_when_no_configured_id(monkeypatch) -> None:
    tms = FakeTMS()
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", tms)
    server_config = MCPServerConfig(
        url="https://mcp.example.com/mcp",
        type="streamable-http",
        mcp_config_id="mcp-config-1",
        mcp_config_name="OneHub",
    )

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=server_config,
        execution_context=_context(oauth2_auth_config_id="discovered:mcp-config-1:binding"),
    )

    assert result is not None
    assert result.retry_auth_headers == {"Authorization": "Bearer fresh-access"}
    assert tms.calls == [("user-1", "discovered:mcp-config-1:binding")]


def test_post_auth_401_bridge_rejects_env_delivered_oauth2_refresh(monkeypatch) -> None:
    tms = FakeTMS()
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", tms)
    server_config = _server_config()
    server_config.auth_config["token_delivery"] = {"method": "env", "key": "ACCESS_TOKEN"}

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=server_config,
        execution_context=_context(),
    )

    assert result.retry_auth_headers is None
    assert tms.calls == []
    server = result.auth_exception.payload["servers"][0]
    assert server["reason"] == "env_token_delivery_unsupported"
    assert server["error_context"] == {"reason": "OAuth2 env-delivered credentials cannot be force-refreshed."}


def test_post_auth_401_bridge_omits_initiate_url_for_workflow_payload(monkeypatch) -> None:
    import codemie_enterprise.mcp_auth as enterprise_auth

    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", FakeTMS(exc=enterprise_auth.TokenNotFound("token_not_found")))

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=_server_config(),
        execution_context=_context(workflow_execution_id="workflow-1"),
    )

    server = result.auth_exception.payload["servers"][0]
    assert "initiate_url" not in server
    assert "fresh-access" not in json.dumps(server)


def test_post_auth_401_bridge_uses_live_discovered_snapshot_for_reauth_initiate_url(monkeypatch) -> None:
    import codemie_enterprise.mcp_auth as enterprise_auth

    auth_config_id = "discovered:mcp-config-1:binding"
    store = FakeDiscoveredFlowStore(
        SimpleNamespace(discovered_auth_id=auth_config_id, discovered_flow_id="df-live-snapshot")
    )
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", FakeTMS(exc=enterprise_auth.TokenNotFound("token_not_found")))
    monkeypatch.setattr(dependencies, "_mcp_auth_discovered_flow_store", store)

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=MCPServerConfig(
            url="https://mcp.example.com/mcp",
            type="streamable-http",
            mcp_config_id="mcp-config-1",
            mcp_config_name="OneHub",
        ),
        execution_context=_context(
            oauth2_auth_config_id=auth_config_id,
            session_binding_hash="binding-hash-1",
        ),
    )

    server = result.auth_exception.payload["servers"][0]
    assert server["initiate_url"].endswith("discovered_flow_id=df-live-snapshot")
    assert store.stored == []


def test_post_auth_401_bridge_rebuilds_discovered_snapshot_from_exact_flow_config(monkeypatch) -> None:
    import codemie_enterprise.mcp_auth as enterprise_auth

    auth_config_id = "discovered:mcp-config-1:binding"
    store = FakeDiscoveredFlowStore()
    flow_config = enterprise_auth.DiscoveredOAuth2FlowConfig(
        authorization_url="https://login.example.net/custom/oauth2/authorize",
        token_url="https://issuer.example.com/oauth2/token",
        client_id="client-1",
        client_type="public",
        client_auth_method="none",
        issuer="https://issuer.example.com",
        resource="https://mcp.example.com/mcp",
        scopes=("read",),
    )
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", FakeTMS(exc=enterprise_auth.TokenNotFound("token_not_found")))
    monkeypatch.setattr(dependencies, "_mcp_auth_discovered_flow_store", store)
    monkeypatch.setattr(
        dependencies,
        "build_redirect_uri",
        lambda: ("https://codemie.example.com/v1/mcp-auth/oauth2/callback", "codemie.example.com", False),
    )

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=MCPServerConfig(
            url="https://mcp.example.com/mcp",
            type="streamable-http",
            mcp_config_id="mcp-config-1",
            mcp_config_name="OneHub",
        ),
        execution_context=_context(
            oauth2_auth_config_id=auth_config_id,
            oauth2_auth_config=flow_config,
            session_binding_hash="binding-hash-1",
        ),
    )

    server = result.auth_exception.payload["servers"][0]
    assert server["initiate_url"].startswith("/v1/mcp-auth/oauth2/initiate?discovered_flow_id=df_")
    assert store.stored
    snapshot = store.stored[0]
    assert snapshot.discovered_auth_id == auth_config_id
    assert snapshot.flow_config.authorization_url == "https://login.example.net/custom/oauth2/authorize"
    assert "issuer.example.com/authorize" not in json.dumps(snapshot.model_dump(mode="json"))


def test_post_auth_401_bridge_discovered_snapshot_missing_without_exact_config_is_non_resumable(
    monkeypatch,
) -> None:
    import codemie_enterprise.mcp_auth as enterprise_auth

    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_tms", FakeTMS(exc=enterprise_auth.TokenNotFound("token_not_found")))
    monkeypatch.setattr(dependencies, "_mcp_auth_discovered_flow_store", FakeDiscoveredFlowStore())

    result = dependencies.build_mcp_post_auth_401_result(
        status_code=401,
        www_authenticate_header='Bearer error="invalid_token"',
        server_config=MCPServerConfig(
            url="https://mcp.example.com/mcp",
            type="streamable-http",
            mcp_config_id="mcp-config-1",
            mcp_config_name="OneHub",
        ),
        execution_context=_context(
            oauth2_auth_config_id="discovered:mcp-config-1:binding",
            session_binding_hash="binding-hash-1",
        ),
    )

    server = result.auth_exception.payload["servers"][0]
    assert "initiate_url" not in server
