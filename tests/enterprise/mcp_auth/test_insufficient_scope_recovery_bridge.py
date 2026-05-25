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
import sys
from types import ModuleType, SimpleNamespace

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


def _confidential_server_config() -> MCPServerConfig:
    config = _server_config()
    config.auth_config = {
        "id": "auth-config-1",
        "auth_type": "oauth2",
        "authorization_url": "https://auth.example.com/oauth2/authorize",
        "token_url": "https://auth.example.com/oauth2/token",
        "client_id": "client-1",
        "client_type": "confidential",
        "scopes": ["read"],
        "token_delivery": {"method": "header", "key": "Authorization"},
    }
    return config


def _challenge() -> str:
    return (
        'Bearer error="insufficient_scope", scope="read write admin", '
        'resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource?tenant=secret"'
    )


def _parse_valid_insufficient_scope_challenge(status_code, www_authenticate_header):
    if status_code == 403 and "insufficient_scope" in (www_authenticate_header or ""):
        return object()
    return None


def _install_fake_enterprise_api(monkeypatch) -> None:
    fake = ModuleType("codemie_enterprise.mcp_auth")

    class RecoveryRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def build_insufficient_scope_recovery(request):
        if request.conversation_id is None and request.workflow_execution_id is None:
            return SimpleNamespace(
                mcp_config_id=request.mcp_config_id,
                mcp_config_name=request.mcp_config_name,
                auth_config_id=request.auth_config_id,
                as_hostname="auth.example.com",
                status="config_error",
                error="scope_escalation_config_error",
                reason="scope_escalation_config_error",
                guidance="Scope escalation cannot be started for 'OneHub'",
                attempts_used=0,
                attempts_remaining=2,
                scope="read write admin",
                required_scopes=("read", "write", "admin"),
                requested_scopes=(),
                resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource",
                resource_metadata_redacted=True,
                error_description=None,
                action=None,
                action_label=None,
                recovery_flow_id=None,
            )
        return SimpleNamespace(
            mcp_config_id=request.mcp_config_id,
            mcp_config_name=request.mcp_config_name,
            auth_config_id=request.auth_config_id,
            as_hostname="auth.example.com",
            status="authentication_required",
            error="insufficient_scope",
            reason="insufficient_scope",
            guidance="OneHub requires additional permissions: read, write, admin",
            attempts_used=0,
            attempts_remaining=2,
            scope="read write admin",
            required_scopes=("read", "write", "admin"),
            requested_scopes=("read", "write", "admin"),
            resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource",
            resource_metadata_redacted=True,
            error_description=None,
            action="reauthenticate",
            action_label="Re-authenticate",
            recovery_flow_id="rf-test",
        )

    fake.RecoveryRequest = RecoveryRequest
    fake.parse_insufficient_scope_challenge = _parse_valid_insufficient_scope_challenge
    fake.build_insufficient_scope_recovery = build_insufficient_scope_recovery
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake)


def test_bridge_maps_assistant_recovery_payload(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    _install_fake_enterprise_api(monkeypatch)

    exc = dependencies.build_mcp_insufficient_scope_auth_exception(
        status_code=403,
        www_authenticate_header=_challenge(),
        server_config=_server_config(),
        execution_context=MCPExecutionContext(
            user_id="user-1",
            conversation_id="conversation-1",
            oauth2_token_data={"access_token": "secret", "scope": "read"},
        ),
    )

    assert exc is not None
    server = exc.payload["servers"][0]
    assert server["status"] == "authentication_required"
    assert server["error"] == "insufficient_scope"
    assert server["action"] == "reauthenticate"
    assert server["action_label"] == "Re-authenticate"
    assert server["scope"] == "read write admin"
    assert server["required_scopes"] == ["read", "write", "admin"]
    assert server["requested_scopes"] == ["read", "write", "admin"]
    assert server["resource_metadata"] == "https://mcp.example.com/.well-known/oauth-protected-resource"
    assert server["resource_metadata_redacted"] is True
    assert server["attempts_used"] == 0
    assert server["attempts_remaining"] == 2
    assert server["recovery_flow_id"]
    assert server["initiate_url"].endswith(f"recovery_flow_id={server['recovery_flow_id']}")


def test_bridge_maps_real_assistant_recovery_payload_snapshot(monkeypatch) -> None:
    import codemie_enterprise.mcp_auth as enterprise_mcp_auth

    enterprise_mcp_auth.reset_recovery_state()
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(
        dependencies,
        "_resolve_insufficient_scope_authorization_server_metadata",
        lambda **kwargs: {
            "issuer": "https://auth.example.com",
            "authorization_endpoint": "https://auth.example.com/oauth2/authorize",
            "token_endpoint": "https://auth.example.com/oauth2/token",
        },
    )
    header = (
        'Bearer error="insufficient_scope", scope="read write admin", '
        'error_description="Cookie: raw-cookie-value; token=raw-token-value; user alice@example.com; '
        'https://user:pass@leak.example.com/token/raw-token-value?client_secret=raw-secret#frag", '
        'resource_metadata="https://user:pass@mcp.example.com/token/tenant-alpha/alice@example.com/resource'
        '?tenant_id=tenant-alpha&token=raw-secret#frag"'
    )

    exc = dependencies.build_mcp_insufficient_scope_auth_exception(
        status_code=403,
        www_authenticate_header=header,
        server_config=_server_config(),
        execution_context=MCPExecutionContext(
            user_id="user-1",
            conversation_id="conversation-1",
            session_binding_hash="binding-hash-1",
            oauth2_token_data={
                "access_token": "stored-access-token-1234567890",
                "scope": "read",
                "issuer": "https://auth.example.com",
                "resource": "https://mcp.example.com/mcp",
            },
        ),
    )

    assert exc is not None
    server = exc.payload["servers"][0]
    assert server["mcp_config_id"] == "mcp-config-1"
    assert server["mcp_config_name"] == "OneHub"
    assert server["mcp_server_name"] == "OneHub"
    assert server["auth_config_id"] == "auth-config-1"
    assert server["auth_type"] == "oauth2"
    assert server["as_hostname"] == "auth.example.com"
    assert server["status"] == "authentication_required"
    assert server["error"] == "insufficient_scope"
    assert server["reason"] == "insufficient_scope"
    assert server["guidance"] == "OneHub requires additional permissions: read, write, admin"
    assert server["scope"] == "read write admin"
    assert server["required_scopes"] == ["read", "write", "admin"]
    assert server["requested_scopes"] == ["read", "write", "admin"]
    assert server["resource_metadata"] == "https://mcp.example.com/[redacted]/[redacted]/[redacted]/resource"
    assert server["resource_metadata_redacted"] is True
    assert server["error_description"]
    assert server["action"] == "reauthenticate"
    assert server["action_label"] == "Re-authenticate"
    assert server["attempts_used"] == 0
    assert server["attempts_remaining"] == 2
    assert server["recovery_flow_id"].startswith("rf_")
    assert server["initiate_url"].endswith(f"recovery_flow_id={server['recovery_flow_id']}")
    payload_text = json.dumps(server, sort_keys=True)
    assert "stored-access-token" not in payload_text
    assert "raw-cookie-value" not in payload_text
    assert "raw-token-value" not in payload_text
    assert "raw-secret" not in payload_text
    assert "alice@example.com" not in payload_text
    assert "user:pass" not in payload_text
    assert "tenant_id=tenant-alpha" not in payload_text
    assert "#frag" not in payload_text


def test_bridge_discovers_metadata_from_selected_insufficient_scope_challenge(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_mcp_auth_trust_policy_service", object())
    monkeypatch.setattr(
        dependencies,
        "read_mcp_auth_discovery_private_network_allowlist_config_sync",
        lambda: (),
    )
    sentinel_static_trust_policy = object()
    monkeypatch.setattr(dependencies, "read_mcp_auth_trusted_as_domains_config_sync", lambda: None)
    monkeypatch.setattr(
        dependencies,
        "build_static_trust_policy_service",
        lambda raw_value: sentinel_static_trust_policy,
    )
    captured: dict[str, object] = {}
    fake = ModuleType("codemie_enterprise.mcp_auth")

    class RecoveryRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def parse_insufficient_scope_challenge(status_code, www_authenticate_header):
        assert status_code == 403
        assert "https://wrong.example.com/prm" in www_authenticate_header
        return SimpleNamespace(
            scope="read admin",
            resource_metadata_url_internal="https://right.example.com/prm?tenant=right",
        )

    async def discover_protected_resource_metadata(**kwargs):
        captured["discovery_header"] = kwargs["www_authenticate_header"]
        return SimpleNamespace(status="discovered", selected_authorization_server="https://auth.example.com")

    async def discover_authorization_server_metadata(selected_as, **kwargs):
        captured["selected_as"] = selected_as
        return SimpleNamespace(
            status="discovered",
            issuer=selected_as,
            authorization_endpoint=f"{selected_as}/oauth2/authorize",
            token_endpoint=f"{selected_as}/oauth2/token",
        )

    def build_insufficient_scope_recovery(request):
        captured["authorization_server_metadata"] = request.authorization_server_metadata
        return SimpleNamespace(
            mcp_config_id=request.mcp_config_id,
            mcp_config_name=request.mcp_config_name,
            auth_config_id=request.auth_config_id,
            as_hostname="auth.example.com",
            status="authentication_required",
            error="insufficient_scope",
            reason="insufficient_scope",
            guidance="OneHub requires additional permissions: read, admin",
            attempts_used=0,
            attempts_remaining=2,
            scope="read admin",
            required_scopes=("read", "admin"),
            requested_scopes=("read", "admin"),
            resource_metadata="https://right.example.com/prm",
            resource_metadata_redacted=True,
            error_description=None,
            action="reauthenticate",
            action_label="Re-authenticate",
            recovery_flow_id="rf-test",
        )

    fake.RecoveryRequest = RecoveryRequest
    fake.parse_insufficient_scope_challenge = parse_insufficient_scope_challenge
    fake.discover_protected_resource_metadata = discover_protected_resource_metadata
    fake.discover_authorization_server_metadata = discover_authorization_server_metadata
    fake.build_insufficient_scope_recovery = build_insufficient_scope_recovery
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake)
    header = (
        'Bearer error="invalid_token", resource_metadata="https://wrong.example.com/prm", '
        'Bearer foo="1", foo="2", error="insufficient_scope", scope="read admin", '
        'resource_metadata="https://right.example.com/prm?tenant=right"'
    )

    exc = dependencies.build_mcp_insufficient_scope_auth_exception(
        status_code=403,
        www_authenticate_header=header,
        server_config=_server_config(),
        execution_context=MCPExecutionContext(
            user_id="user-1",
            conversation_id="conversation-1",
            oauth2_token_data={"access_token": "secret", "scope": "read"},
        ),
    )

    assert exc is not None
    discovery_header = captured["discovery_header"]
    assert isinstance(discovery_header, str)
    assert "https://right.example.com/prm?tenant=right" in discovery_header
    assert "https://wrong.example.com/prm" not in discovery_header
    assert "foo=" not in discovery_header
    assert captured["authorization_server_metadata"] == {
        "issuer": "https://auth.example.com",
        "authorization_endpoint": "https://auth.example.com/oauth2/authorize",
        "token_endpoint": "https://auth.example.com/oauth2/token",
    }


def test_bridge_omits_initiate_url_for_workflow(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    _install_fake_enterprise_api(monkeypatch)

    exc = dependencies.build_mcp_insufficient_scope_auth_exception(
        status_code=403,
        www_authenticate_header=_challenge(),
        server_config=_server_config(),
        execution_context=MCPExecutionContext(
            user_id="user-1",
            workflow_execution_id="workflow-1",
            conversation_id="conversation-ignored",
            oauth2_token_data={"access_token": "secret", "scope": "read"},
        ),
    )

    assert exc is not None
    server = exc.payload["servers"][0]
    assert server["recovery_flow_id"]
    assert "initiate_url" not in server


def test_bridge_skips_discovery_for_unsupported_403_challenge(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    fake = ModuleType("codemie_enterprise.mcp_auth")

    def parse_insufficient_scope_challenge(status_code, www_authenticate_header):
        assert status_code == 403
        assert (
            www_authenticate_header == 'Bearer error="invalid_token", resource_metadata="https://mcp.example.com/meta"'
        )
        return None

    class RecoveryRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def build_insufficient_scope_recovery(request):
        raise AssertionError("unsupported challenge must not reach recovery builder")

    fake.RecoveryRequest = RecoveryRequest
    fake.parse_insufficient_scope_challenge = parse_insufficient_scope_challenge
    fake.build_insufficient_scope_recovery = build_insufficient_scope_recovery
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake)
    monkeypatch.setattr(
        dependencies,
        "_resolve_insufficient_scope_authorization_server_metadata",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("unsupported challenge must not trigger discovery")),
    )

    exc = dependencies.build_mcp_insufficient_scope_auth_exception(
        status_code=403,
        www_authenticate_header='Bearer error="invalid_token", resource_metadata="https://mcp.example.com/meta"',
        server_config=_server_config(),
        execution_context=MCPExecutionContext(user_id="user-1", conversation_id="conversation-1"),
    )

    assert exc is None


def test_bridge_missing_retry_context_fails_closed_without_action(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    _install_fake_enterprise_api(monkeypatch)

    exc = dependencies.build_mcp_insufficient_scope_auth_exception(
        status_code=403,
        www_authenticate_header=_challenge(),
        server_config=_server_config(),
        execution_context=MCPExecutionContext(user_id="user-1"),
    )

    assert exc is not None
    server = exc.payload["servers"][0]
    assert server["status"] == "config_error"
    assert server["error"] == "scope_escalation_config_error"
    assert "action" not in server
    assert "action_label" not in server
    assert "recovery_flow_id" not in server
    assert "initiate_url" not in server


def test_bridge_passes_discovered_recovery_config_and_storage_identity(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    captured_requests: list[object] = []
    fake = ModuleType("codemie_enterprise.mcp_auth")

    class RecoveryRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def build_insufficient_scope_recovery(request):
        captured_requests.append(request)
        return SimpleNamespace(
            mcp_config_id=request.mcp_config_id,
            mcp_config_name=request.mcp_config_name,
            auth_config_id=request.auth_config_id,
            as_hostname="auth.example.com",
            status="authentication_required",
            error="insufficient_scope",
            reason="insufficient_scope",
            guidance="OneHub requires additional permissions: read, write, admin",
            attempts_used=0,
            attempts_remaining=2,
            scope="read write admin",
            required_scopes=("read", "write", "admin"),
            requested_scopes=("read", "write", "admin"),
            resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource",
            resource_metadata_redacted=True,
            error_description=None,
            action="reauthenticate",
            action_label="Re-authenticate",
            recovery_flow_id="rf-test",
        )

    fake.RecoveryRequest = RecoveryRequest
    fake.parse_insufficient_scope_challenge = _parse_valid_insufficient_scope_challenge
    fake.build_insufficient_scope_recovery = build_insufficient_scope_recovery
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake)
    discovered_auth_config = SimpleNamespace(client_id="client-1")

    exc = dependencies.build_mcp_insufficient_scope_auth_exception(
        status_code=403,
        www_authenticate_header=_challenge(),
        server_config=MCPServerConfig(
            url="https://mcp.example.com/mcp",
            type="streamable-http",
            auth_config=None,
            mcp_config_id="mcp-config-1",
            mcp_config_name="OneHub",
        ),
        execution_context=MCPExecutionContext(
            user_id="user-1",
            conversation_id="conversation-1",
            oauth2_token_data={"access_token": "secret", "scope": "read", "flow_source": "discovered"},
            oauth2_auth_config=discovered_auth_config,
            oauth2_auth_config_id="discovered:" + "a" * 64,
        ),
    )

    assert exc is not None
    assert captured_requests[0].auth_config is discovered_auth_config
    assert captured_requests[0].auth_config_id == "discovered:" + "a" * 64
    assert captured_requests[0].token_storage_auth_config_id == "discovered:" + "a" * 64


def test_bridge_validates_persisted_confidential_secret_before_enterprise_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "_load_callback_mcp_config", lambda auth_config_id: object())
    monkeypatch.setattr(
        dependencies,
        "_load_raw_callback_oauth_config",
        lambda mcp_config, auth_config_id: {
            "id": auth_config_id,
            "auth_type": "oauth2",
            "authorization_url": "https://auth.example.com/oauth2/authorize",
            "token_url": "https://auth.example.com/oauth2/token",
            "client_id": "client-1",
            "client_type": "confidential",
            "token_delivery": {"method": "header", "key": "Authorization"},
        },
    )
    monkeypatch.setattr(dependencies, "decrypt_confidential_client_secret", lambda raw_auth_config: None)
    captured_requests: list[object] = []
    fake = ModuleType("codemie_enterprise.mcp_auth")

    class RecoveryRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def build_insufficient_scope_recovery(request):
        captured_requests.append(request)
        assert request.confidential_client_secret_available is False
        return SimpleNamespace(
            mcp_config_id=request.mcp_config_id,
            mcp_config_name=request.mcp_config_name,
            auth_config_id=request.auth_config_id,
            as_hostname="auth.example.com",
            status="config_error",
            error="scope_escalation_config_error",
            reason="scope_escalation_config_error",
            guidance="Scope escalation cannot be started for 'OneHub'",
            attempts_used=0,
            attempts_remaining=2,
            scope="read write admin",
            required_scopes=("read", "write", "admin"),
            requested_scopes=(),
            resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource",
            resource_metadata_redacted=True,
            error_description=None,
            action=None,
            action_label=None,
            recovery_flow_id=None,
        )

    fake.RecoveryRequest = RecoveryRequest
    fake.parse_insufficient_scope_challenge = _parse_valid_insufficient_scope_challenge
    fake.build_insufficient_scope_recovery = build_insufficient_scope_recovery
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake)

    exc = dependencies.build_mcp_insufficient_scope_auth_exception(
        status_code=403,
        www_authenticate_header=_challenge(),
        server_config=_confidential_server_config(),
        execution_context=MCPExecutionContext(
            user_id="user-1",
            conversation_id="conversation-1",
            oauth2_token_data={"access_token": "secret", "scope": "read"},
        ),
    )

    assert exc is not None
    assert captured_requests
    server = exc.payload["servers"][0]
    assert server["status"] == "config_error"
    assert server["error"] == "scope_escalation_config_error"
    assert "recovery_flow_id" not in server
    assert "action" not in server


def test_bridge_omits_discovered_internal_auth_config_id_for_non_limit_config_error(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    fake = ModuleType("codemie_enterprise.mcp_auth")
    discovered_auth_id = "discovered:" + "e" * 64

    class RecoveryRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def build_insufficient_scope_recovery(request):
        return SimpleNamespace(
            mcp_config_id=request.mcp_config_id,
            mcp_config_name=request.mcp_config_name,
            auth_config_id=discovered_auth_id,
            as_hostname="auth.example.com",
            status="config_error",
            error="scope_escalation_config_error",
            reason="scope_escalation_config_error",
            guidance="Scope escalation cannot be started for 'OneHub'",
            attempts_used=0,
            attempts_remaining=2,
            scope="read write admin",
            required_scopes=("read", "write", "admin"),
            requested_scopes=(),
            resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource",
            resource_metadata_redacted=True,
            error_description=None,
            action=None,
            action_label=None,
            recovery_flow_id=None,
        )

    fake.RecoveryRequest = RecoveryRequest
    fake.parse_insufficient_scope_challenge = _parse_valid_insufficient_scope_challenge
    fake.build_insufficient_scope_recovery = build_insufficient_scope_recovery
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake)

    exc = dependencies.build_mcp_insufficient_scope_auth_exception(
        status_code=403,
        www_authenticate_header=_challenge(),
        server_config=MCPServerConfig(
            url="https://mcp.example.com/mcp",
            type="streamable-http",
            auth_config=None,
            mcp_config_id="mcp-config-1",
            mcp_config_name="OneHub",
        ),
        execution_context=MCPExecutionContext(
            user_id="user-1",
            conversation_id="conversation-1",
            oauth2_token_data={"access_token": "secret", "scope": "read", "flow_source": "discovered"},
            oauth2_auth_config=SimpleNamespace(client_type="public"),
            oauth2_auth_config_id=discovered_auth_id,
        ),
    )

    assert exc is not None
    server = exc.payload["servers"][0]
    assert server["status"] == "config_error"
    assert server["error"] == "scope_escalation_config_error"
    assert "auth_config_id" not in server
