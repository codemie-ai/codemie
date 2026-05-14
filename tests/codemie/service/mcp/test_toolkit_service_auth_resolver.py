# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import MCPAuthenticationRequiredException
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.service.mcp.access_control import MCPAccessControlService
from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import MCPExecutionContext, MCPServerConfig, MCPToolDefinition, MCPToolLoadException
from codemie.rest_api.security.user import User
from codemie.service.mcp.toolkit_service import LegacyTokenResolver, MCPToolkitService


def _build_mcp_server(
    *,
    name: str = "auth-enabled-server",
    headers: dict[str, str] | None = None,
    auth_config: dict[str, str] | None = None,
    audience: str | None = None,
    mcp_config_id: str | None = None,
) -> MCPServerDetails:
    return MCPServerDetails(
        name=name,
        enabled=True,
        mcp_config_id=mcp_config_id,
        config=MCPServerConfig(
            command="uvx",
            args=["example-server"],
            env={},
            headers=headers or {},
            auth_config=auth_config,
            audience=audience,
        ),
    )


def _build_tool_definition() -> MCPToolDefinition:
    return MCPToolDefinition(
        name="example_tool",
        description="Example tool",
        inputSchema={"type": "object", "properties": {}, "required": []},
    )


def _build_user() -> User:
    user = MagicMock(spec=User)
    user.id = "user-1"
    user.name = "Test User"
    user.username = "test.user"
    return user


class _RecordingResolver:
    def __init__(
        self,
        *,
        name: str,
        can_handle: bool = True,
        raise_error: Exception | None = None,
        inject_env: bool = False,
        inject_header: bool = False,
    ) -> None:
        self.name = name
        self._can_handle = can_handle
        self._raise_error = raise_error
        self._inject_env = inject_env
        self._inject_header = inject_header
        self.calls: list[tuple[MCPServerConfig, str | None, MCPExecutionContext | None]] = []

    def can_handle(self, server_config: MCPServerConfig) -> bool:
        del server_config
        return self._can_handle

    def resolve(
        self,
        server_config: MCPServerConfig,
        user_id: str | None,
        execution_context: MCPExecutionContext | None = None,
    ) -> None:
        self.calls.append((server_config, user_id, execution_context))
        if self._raise_error is not None:
            raise self._raise_error
        if self._inject_env and user_id:
            server_config.env["ACCESS_TOKEN"] = f"Bearer {user_id}"
        if self._inject_header and user_id and execution_context is not None:
            if execution_context.auth_headers is None:
                execution_context.auth_headers = {}
            execution_context.auth_headers["Authorization"] = f"Bearer {user_id}"


class _ExpiringSAMLResolver:
    def __init__(self) -> None:
        self.expired = True

    def can_handle(self, server_config: MCPServerConfig) -> bool:
        del server_config
        return True

    def resolve(
        self,
        server_config: MCPServerConfig,
        user_id: str | None,
        execution_context: MCPExecutionContext | None = None,
    ) -> None:
        del user_id, execution_context
        if self.expired:
            raise MCPAuthenticationRequiredException(
                {
                    "auth_config_id": server_config.auth_config["id"],
                    "status": "session_expired",
                    "auth_type": "saml",
                    "error_context": "SAML session expired",
                }
            )
        server_config.env["ACCESS_TOKEN"] = "user@example.com"


@pytest.fixture(autouse=True)
def _reset_toolkit_service(monkeypatch: pytest.MonkeyPatch) -> None:
    MCPToolkitService.reset_instance()
    monkeypatch.setattr(MCPToolkitService, "_auth_resolvers", [])
    yield
    MCPToolkitService.reset_instance()


def test_process_single_mcp_server_passes_execution_context_to_prepare_server_config() -> None:
    mcp_server = _build_mcp_server()
    default_toolkit_service = MagicMock()
    default_toolkit_service.get_toolkit.return_value.get_tools.return_value = []
    execution_context = MCPExecutionContext(user_id="user-1", auth_headers={"Authorization": "Bearer token"})
    server_config = MCPServerConfig(command="uvx", args=["example-server"], env={})

    with patch.object(MCPToolkitService, "_get_toolkit_service_for_server", return_value=default_toolkit_service):
        with patch.object(MCPToolkitService, "_prepare_server_config", return_value=server_config) as mock_prepare:
            MCPToolkitService._process_single_mcp_server(
                mcp_server=mcp_server,
                default_toolkit_service=default_toolkit_service,
                user_id="user-1",
                execution_context=execution_context,
            )

    assert mock_prepare.call_args.kwargs["execution_context"] is execution_context


def test_prepare_server_config_invokes_only_first_matching_auth_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_resolver = _RecordingResolver(name="first", can_handle=False)
    second_resolver = _RecordingResolver(name="second", inject_env=True)
    third_resolver = _RecordingResolver(name="third", inject_env=True)
    monkeypatch.setattr(MCPToolkitService, "_auth_resolvers", [first_resolver, second_resolver, third_resolver])

    execution_context = MCPExecutionContext(user_id="user-1")
    server_config = MCPToolkitService._prepare_server_config(
        mcp_server=_build_mcp_server(),
        user_id="user-1",
        execution_context=execution_context,
    )

    assert first_resolver.calls == []
    assert len(second_resolver.calls) == 1
    assert third_resolver.calls == []
    _, resolved_user_id, resolved_context = second_resolver.calls[0]
    assert resolved_user_id == "user-1"
    assert resolved_context is execution_context
    assert server_config.env["ACCESS_TOKEN"] == "Bearer user-1"


def test_process_single_mcp_server_reraises_mcp_authentication_required_exception() -> None:
    auth_error = MCPAuthenticationRequiredException({"auth_config_id": "cfg-1", "status": "authentication_required"})
    default_toolkit_service = MagicMock()

    with patch.object(MCPToolkitService, "_get_toolkit_service_for_server", return_value=default_toolkit_service):
        with patch.object(MCPToolkitService, "_prepare_server_config", side_effect=auth_error):
            with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                MCPToolkitService._process_single_mcp_server(
                    mcp_server=_build_mcp_server(),
                    default_toolkit_service=default_toolkit_service,
                    user_id="user-1",
                    execution_context=MCPExecutionContext(user_id="user-1"),
                )

    assert exc_info.value is auth_error


def test_get_mcp_server_tools_aggregates_authentication_required_servers_in_input_order() -> None:
    auth_required_error = MCPAuthenticationRequiredException(
        {
            "auth_config_id": "auth-1",
            "mcp_server_name": "auth-required-server",
            "status": "authentication_required",
            "auth_type": "oauth2",
        }
    )
    session_expired_error = MCPAuthenticationRequiredException(
        {
            "auth_config_id": "auth-2",
            "mcp_server_name": "session-expired-server",
            "status": "session_expired",
            "auth_type": "saml",
        }
    )
    mcp_servers = [
        _build_mcp_server(name="healthy-server"),
        _build_mcp_server(name="auth-required-server"),
        _build_mcp_server(name="session-expired-server"),
    ]

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        if mcp_server.name == "healthy-server":
            return [MagicMock(name="healthy-tool")]
        if mcp_server.name == "auth-required-server":
            raise auth_required_error
        raise session_expired_error

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server) as mock_process:
            with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                MCPToolkitService.get_mcp_server_tools(mcp_servers, user_id="user-1")

    assert mock_process.call_count == 3
    assert exc_info.value.payload["error"] == "authentication_required"
    assert [server["mcp_config_name"] for server in exc_info.value.payload["servers"]] == [
        "auth-required-server",
        "session-expired-server",
    ]
    assert [server["status"] for server in exc_info.value.payload["servers"]] == [
        "authentication_required",
        "session_expired",
    ]


def test_get_mcp_server_tools_discards_auth_accumulator_when_non_auth_failure_occurs() -> None:
    auth_required_error = MCPAuthenticationRequiredException(
        {
            "auth_config_id": "auth-1",
            "mcp_server_name": "auth-required-server",
            "status": "authentication_required",
            "auth_type": "oauth2",
        }
    )
    tool_load_error = MCPToolLoadException("broken-server", RuntimeError("boom"))
    mcp_servers = [
        _build_mcp_server(name="auth-required-server"),
        _build_mcp_server(name="broken-server"),
        _build_mcp_server(name="unreached-server"),
    ]

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        if mcp_server.name == "auth-required-server":
            raise auth_required_error
        if mcp_server.name == "broken-server":
            raise tool_load_error
        return [MagicMock(name="unreached-tool")]

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server) as mock_process:
            with pytest.raises(MCPToolLoadException) as exc_info:
                MCPToolkitService.get_mcp_server_tools(mcp_servers, user_id="user-1")

    assert exc_info.value is tool_load_error
    assert mock_process.call_count == 2


def test_get_mcp_server_tools_aggregates_all_auth_blocked_servers_in_input_order() -> None:
    mcp_servers = [
        _build_mcp_server(
            name="oauth-server",
            mcp_config_id="mcp-1",
            auth_config={
                "id": "auth-1",
                "auth_type": "oauth2",
                "authorization_url": "https://login.example.com/oauth2/authorize",
            },
        ),
        _build_mcp_server(
            name="saml-server",
            mcp_config_id="mcp-2",
            auth_config={
                "id": "auth-2",
                "auth_type": "saml",
                "entity_id": "idp.example.com",
            },
        ),
    ]

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        if mcp_server.name == "oauth-server":
            raise MCPAuthenticationRequiredException({"status": "authentication_required", "auth_type": "oauth2"})
        raise MCPAuthenticationRequiredException({"status": "config_error", "auth_type": "saml"})

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                MCPToolkitService.get_mcp_server_tools(mcp_servers, user_id="user-1")

    assert exc_info.value.payload["error"] == "authentication_required"
    assert [server["mcp_config_name"] for server in exc_info.value.payload["servers"]] == [
        "oauth-server",
        "saml-server",
    ]
    assert [server["status"] for server in exc_info.value.payload["servers"]] == [
        "authentication_required",
        "config_error",
    ]


def test_get_mcp_server_tools_aggregate_payload_excludes_legacy_and_no_auth_servers() -> None:
    mcp_servers = [
        _build_mcp_server(
            name="auth-server",
            mcp_config_id="mcp-1",
            auth_config={
                "id": "auth-1",
                "auth_type": "oauth2",
                "authorization_url": "https://login.example.com/oauth2/authorize",
            },
        ),
        _build_mcp_server(
            name="legacy-server",
            headers={"Authorization": "Bearer [user.token]"},
        ),
        _build_mcp_server(
            name="no-auth-server",
            headers={"X-Static": "ok"},
        ),
    ]

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        if mcp_server.name == "auth-server":
            raise MCPAuthenticationRequiredException({"status": "authentication_required", "auth_type": "oauth2"})
        return [MagicMock(name=f"{mcp_server.name}-tool")]

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                MCPToolkitService.get_mcp_server_tools(mcp_servers, user_id="user-1")

    assert exc_info.value.payload["servers"] == [
        {
            "auth_config_id": "auth-1",
            "mcp_config_id": "mcp-1",
            "mcp_config_name": "auth-server",
            "mcp_server_name": "auth-server",
            "auth_type": "oauth2",
            "as_hostname": "login.example.com",
            "status": "authentication_required",
            "error_context": None,
            "initiate_url": "/v1/mcp-auth/oauth2/initiate",
        }
    ]


def test_build_auth_required_server_payload_for_assistant_uses_live_core_fallbacks() -> None:
    payload = MCPToolkitService._build_auth_required_server_payload(
        caught_payload={
            "status": "authentication_required",
            "error_context": "Administrator action required.",
        },
        mcp_server=_build_mcp_server(
            name="catalog-server",
            mcp_config_id="mcp-1",
            auth_config={
                "id": "auth-1",
                "auth_type": "oauth2",
                "authorization_url": "https://login.example.com/oauth2/authorize",
            },
        ),
        execution_context=MCPExecutionContext(user_id="user-1", assistant_id="assistant-1"),
    )

    assert payload == {
        "auth_config_id": "auth-1",
        "mcp_config_id": "mcp-1",
        "mcp_config_name": "catalog-server",
        "mcp_server_name": "catalog-server",
        "auth_type": "oauth2",
        "as_hostname": "login.example.com",
        "status": "authentication_required",
        "error_context": "Administrator action required.",
        "initiate_url": "/v1/mcp-auth/oauth2/initiate",
    }


def test_build_auth_required_server_payload_for_assistant_preserves_saml_session_expired_context() -> None:
    payload = MCPToolkitService._build_auth_required_server_payload(
        caught_payload={
            "status": "session_expired",
            "auth_type": "saml",
            "error_context": "SAML session expired",
        },
        mcp_server=_build_mcp_server(
            name="saml-assistant-server",
            mcp_config_id="mcp-3",
            auth_config={
                "id": "auth-3",
                "auth_type": "saml",
                "sso_url": "https://idp.example.com/sso",
                "entity_id": "https://idp.example.com/metadata",
            },
        ),
        execution_context=MCPExecutionContext(user_id="user-1", assistant_id="assistant-1"),
    )

    assert payload["error_context"] == "SAML session expired"
    assert payload["status"] == "session_expired"
    assert payload["auth_type"] == "saml"
    assert payload["initiate_url"] == "/v1/mcp-auth/saml/initiate"
    assert payload["as_hostname"] == "idp.example.com"


def test_build_auth_required_server_payload_for_workflow_omits_initiate_url() -> None:
    payload = MCPToolkitService._build_auth_required_server_payload(
        caught_payload={
            "status": "session_expired",
            "mcp_server_name": "resolver-name",
            "auth_type": "saml",
            "error_context": "SAML session expired",
        },
        mcp_server=_build_mcp_server(
            name="fallback-name",
            mcp_config_id="mcp-2",
            auth_config={
                "id": "auth-2",
                "auth_type": "saml",
                "entity_id": "https://idp.example.com/metadata",
            },
        ),
        execution_context=MCPExecutionContext(user_id="user-1", workflow_execution_id="wf-1"),
    )

    assert payload == {
        "auth_config_id": "auth-2",
        "mcp_config_id": "mcp-2",
        "mcp_config_name": "resolver-name",
        "mcp_server_name": "resolver-name",
        "auth_type": "saml",
        "as_hostname": "idp.example.com",
        "status": "session_expired",
        "error_context": "SAML session expired",
    }


def test_get_mcp_server_tools_stops_listing_saml_server_after_reauth() -> None:
    resolver = _ExpiringSAMLResolver()
    saml_server = _build_mcp_server(
        name="saml-server",
        mcp_config_id="mcp-2",
        auth_config={
            "id": "auth-2",
            "auth_type": "saml",
            "sso_url": "https://idp.example.com/sso",
            "entity_id": "https://idp.example.com/metadata",
            "idp_entity_id": "https://idp.example.com/metadata",
            "idp_x509cert": "CERTDATA",
            "saml_credential_attribute": "mail",
            "saml_session_ttl": 3600,
            "token_delivery": {"method": "env", "key": "ACCESS_TOKEN"},
        },
    )
    default_toolkit_service = MagicMock()
    default_toolkit_service.get_toolkit.return_value.get_tools.return_value = [MagicMock(name="healthy-tool")]

    with patch.object(MCPToolkitService, "get_instance", return_value=default_toolkit_service):
        with patch.object(MCPToolkitService, "_auth_resolvers", [resolver]):
            with patch.object(MCPToolkitService, "_create_context_aware_tools", side_effect=lambda tools, _: tools):
                with patch.object(MCPAccessControlService, "resolve_catalog_config", side_effect=lambda s: s):
                    with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                        MCPToolkitService.get_mcp_server_tools(
                            [saml_server], user_id="user-1", assistant_id="assistant-1"
                        )

                    assert exc_info.value.payload["servers"] == [
                        {
                            "auth_config_id": "auth-2",
                            "mcp_config_id": "mcp-2",
                            "mcp_config_name": "saml-server",
                            "mcp_server_name": "saml-server",
                            "auth_type": "saml",
                            "as_hostname": "idp.example.com",
                            "status": "session_expired",
                            "error_context": "SAML session expired",
                            "initiate_url": "/v1/mcp-auth/saml/initiate",
                        }
                    ]

                    resolver.expired = False

                    tools = MCPToolkitService.get_mcp_server_tools(
                        [saml_server],
                        user_id="user-1",
                        assistant_id="assistant-1",
                    )

    assert len(tools) == 1


def test_build_auth_required_server_payload_for_assistant_falls_back_to_saml_entity_id_hostname() -> None:
    payload = MCPToolkitService._build_auth_required_server_payload(
        caught_payload={"status": "session_expired"},
        mcp_server=_build_mcp_server(
            name="assistant-saml-server",
            auth_config={
                "id": "auth-4",
                "auth_type": "saml",
                "entity_id": "idp.example.com",
            },
        ),
        execution_context=MCPExecutionContext(user_id="user-1", assistant_id="assistant-1"),
    )

    assert payload["as_hostname"] == "idp.example.com"
    assert payload["initiate_url"] == "/v1/mcp-auth/saml/initiate"


def test_build_auth_required_server_payload_for_assistant_falls_back_to_entity_id_when_sso_url_has_no_hostname() -> (
    None
):
    payload = MCPToolkitService._build_auth_required_server_payload(
        caught_payload={"status": "session_expired"},
        mcp_server=_build_mcp_server(
            name="assistant-saml-server",
            auth_config={
                "id": "auth-6",
                "auth_type": "saml",
                "sso_url": "",
                "entity_id": "idp.example.com",
            },
        ),
        execution_context=MCPExecutionContext(user_id="user-1", assistant_id="assistant-1"),
    )

    assert payload["as_hostname"] == "idp.example.com"
    assert payload["initiate_url"] == "/v1/mcp-auth/saml/initiate"


def test_build_auth_required_server_payload_for_assistant_keeps_saml_hostname_none_without_metadata() -> None:
    payload = MCPToolkitService._build_auth_required_server_payload(
        caught_payload={"status": "authentication_required"},
        mcp_server=_build_mcp_server(
            name="assistant-saml-server",
            auth_config={
                "id": "auth-5",
                "auth_type": "saml",
            },
        ),
        execution_context=MCPExecutionContext(user_id="user-1", assistant_id="assistant-1"),
    )

    assert payload["as_hostname"] is None
    assert payload["initiate_url"] == "/v1/mcp-auth/saml/initiate"


def test_build_auth_required_server_payload_ignores_non_hostname_saml_entity_id() -> None:
    payload = MCPToolkitService._build_auth_required_server_payload(
        caught_payload={"status": "config_error"},
        mcp_server=_build_mcp_server(
            name="saml-server",
            auth_config={
                "id": "auth-3",
                "auth_type": "saml",
                "entity_id": "urn:idp:example",
            },
        ),
        execution_context=MCPExecutionContext(user_id="user-1", assistant_id="assistant-1"),
    )

    assert payload["as_hostname"] is None
    assert payload["initiate_url"] == "/v1/mcp-auth/saml/initiate"


def test_get_mcp_server_tools_uses_env_injection_to_isolate_cache_per_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_client = MagicMock(spec=MCPConnectClient)
    mock_client.base_url = "http://mock-mcp-connect"
    mock_client.list_tools = AsyncMock(return_value=[_build_tool_definition()])
    MCPToolkitService.init_singleton(mock_client)

    resolver = _RecordingResolver(name="env", inject_env=True)
    monkeypatch.setattr(MCPToolkitService, "_auth_resolvers", [resolver])

    mcp_server = _build_mcp_server()
    MCPToolkitService.get_mcp_server_tools([mcp_server], user_id="user-a")
    MCPToolkitService.get_mcp_server_tools([mcp_server], user_id="user-b")

    assert mock_client.list_tools.await_count == 2
    assert len(MCPToolkitService.get_instance().toolkit_factory._toolkit_cache) == 2


def test_get_mcp_server_tools_checks_header_auth_before_reusing_cached_toolkit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_client = MagicMock(spec=MCPConnectClient)
    mock_client.base_url = "http://mock-mcp-connect"
    mock_client.list_tools = AsyncMock(return_value=[_build_tool_definition()])
    MCPToolkitService.init_singleton(mock_client)

    auth_error = MCPAuthenticationRequiredException({"auth_config_id": "cfg-1", "status": "authentication_required"})

    class _HeaderResolver(_RecordingResolver):
        def resolve(
            self,
            server_config: MCPServerConfig,
            user_id: str | None,
            execution_context: MCPExecutionContext | None = None,
        ) -> None:
            if user_id == "user-b":
                raise auth_error
            super().resolve(server_config, user_id, execution_context)

    resolver = _HeaderResolver(name="header", inject_header=True)
    monkeypatch.setattr(MCPToolkitService, "_auth_resolvers", [resolver])

    mcp_server = _build_mcp_server(headers={"Authorization": "Bearer stale"})
    MCPToolkitService.get_mcp_server_tools([mcp_server], user_id="user-a")

    with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
        MCPToolkitService.get_mcp_server_tools([mcp_server], user_id="user-b")

    assert exc_info.value.payload["error"] == "authentication_required"
    assert exc_info.value.payload["servers"] == [
        {
            "auth_config_id": "cfg-1",
            "mcp_config_id": None,
            "mcp_config_name": "auth-enabled-server",
            "mcp_server_name": "auth-enabled-server",
            "auth_type": None,
            "as_hostname": None,
            "status": "authentication_required",
            "error_context": None,
            "initiate_url": None,
        }
    ]
    assert mock_client.list_tools.await_count == 1
    assert len(MCPToolkitService.get_instance().toolkit_factory._toolkit_cache) == 1


@pytest.mark.parametrize(
    ("server_config", "expected"),
    [
        (MCPServerConfig(command="uvx", headers=None), False),
        (MCPServerConfig(command="uvx", headers={}), False),
        (MCPServerConfig(command="uvx", headers={"Authorization": "Bearer static"}), False),
        (
            MCPServerConfig(
                command="uvx",
                headers={"Authorization": "Bearer {{user.token}}"},
                auth_config={"id": "cfg-1"},
            ),
            False,
        ),
        (MCPServerConfig(command="uvx", headers={"Authorization": "Bearer {{user.token}}"}), True),
    ],
)
def test_legacy_token_resolver_can_handle_only_placeholder_only_servers(
    server_config: MCPServerConfig,
    expected: bool,
) -> None:
    resolver = LegacyTokenResolver()

    assert resolver.can_handle(server_config) is expected


def test_process_server_url_and_command_preserves_legacy_placeholder_for_resolver_chain() -> None:
    server_config = MCPServerConfig(
        command="uvx",
        args=["example-server"],
        env={},
        headers={
            "Authorization": "Bearer [user.token]",
            "X-User": "{{user.name}}",
        },
    )

    with patch("codemie.service.mcp.toolkit_service.get_current_user", return_value=_build_user()):
        with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
            MCPToolkitService._process_server_url_and_command(server_config, None)

    mock_factory.get_token_for_current_user.assert_not_called()
    assert server_config.headers == {
        "Authorization": "Bearer {{user.token}}",
        "X-User": "Test User",
    }


def test_prepare_server_config_falls_back_to_legacy_after_registered_resolvers_decline() -> None:
    resolver = _RecordingResolver(name="enterprise", can_handle=False)

    with patch.object(MCPToolkitService, "_auth_resolvers", [resolver]):
        with patch("codemie.service.mcp.toolkit_service.get_current_user", return_value=_build_user()):
            with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
                mock_factory.get_token_for_current_user.return_value = "legacy-token"

                server_config = MCPToolkitService._prepare_server_config(
                    mcp_server=_build_mcp_server(headers={"Authorization": "Bearer [user.token]"}),
                    user_id="user-1",
                    execution_context=MCPExecutionContext(user_id="user-1"),
                )

    mock_factory.get_token_for_current_user.assert_called_once()
    assert resolver.calls == []
    assert server_config.headers == {"Authorization": "Bearer legacy-token"}


def test_prepare_server_config_prefers_enterprise_resolver_and_skips_legacy_services() -> None:
    resolver = _RecordingResolver(name="enterprise", inject_header=True)
    execution_context = MCPExecutionContext(user_id="user-1")

    with patch.object(MCPToolkitService, "_auth_resolvers", [resolver]):
        with patch("codemie.service.mcp.toolkit_service.get_current_user", return_value=_build_user()):
            with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
                with patch(
                    "codemie.service.security.oidc_token_exchange_service.oidc_token_exchange_service"
                ) as mock_oidc:
                    server_config = MCPToolkitService._prepare_server_config(
                        mcp_server=_build_mcp_server(
                            headers={
                                "Authorization": "Bearer [user.token]",
                                "X-User": "{{user.name}}",
                            },
                            auth_config={"id": "cfg-1"},
                            audience="aud-1",
                        ),
                        user_id="user-1",
                        execution_context=execution_context,
                    )

    assert len(resolver.calls) == 1
    mock_factory.get_token_for_current_user.assert_not_called()
    mock_oidc.get_exchanged_token.assert_not_called()
    assert execution_context.auth_headers == {"Authorization": "Bearer user-1"}
    assert server_config.headers == {"X-User": "Test User"}


def test_prepare_server_config_restores_legacy_behavior_after_auth_config_removal() -> None:
    mcp_server = _build_mcp_server(
        headers={"Authorization": "Bearer [user.token]"},
        auth_config={"id": "cfg-1"},
    )

    with patch("codemie.service.mcp.toolkit_service.get_current_user", return_value=_build_user()):
        with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
            first_config = MCPToolkitService._prepare_server_config(
                mcp_server=mcp_server,
                user_id="user-1",
                execution_context=MCPExecutionContext(user_id="user-1"),
            )

            mcp_server.config.auth_config = None
            mock_factory.get_token_for_current_user.return_value = "legacy-token"
            second_config = MCPToolkitService._prepare_server_config(
                mcp_server=mcp_server,
                user_id="user-1",
                execution_context=MCPExecutionContext(user_id="user-1"),
            )

    assert first_config.headers == {}
    assert second_config.headers == {"Authorization": "Bearer legacy-token"}


def test_prepare_server_config_mixed_fleet_regression() -> None:
    class _AuthConfigResolver(_RecordingResolver):
        def can_handle(self, server_config: MCPServerConfig) -> bool:
            return bool(server_config.auth_config)

    resolver = _AuthConfigResolver(name="enterprise", inject_header=True)
    auth_only_context = MCPExecutionContext(user_id="user-1")
    both_context = MCPExecutionContext(user_id="user-1")

    with patch.object(MCPToolkitService, "_auth_resolvers", [resolver]):
        with patch("codemie.service.mcp.toolkit_service.get_current_user", return_value=_build_user()):
            with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
                with patch(
                    "codemie.service.security.oidc_token_exchange_service.oidc_token_exchange_service"
                ) as mock_oidc:
                    mock_factory.get_token_for_current_user.return_value = "legacy-token"

                    auth_only_config = MCPToolkitService._prepare_server_config(
                        mcp_server=_build_mcp_server(
                            headers={"X-User": "{{user.name}}"},
                            auth_config={"id": "cfg-auth-only"},
                        ),
                        user_id="user-1",
                        execution_context=auth_only_context,
                    )
                    legacy_only_config = MCPToolkitService._prepare_server_config(
                        mcp_server=_build_mcp_server(headers={"Authorization": "Bearer [user.token]"}),
                        user_id="user-1",
                        execution_context=MCPExecutionContext(user_id="user-1"),
                    )
                    both_config = MCPToolkitService._prepare_server_config(
                        mcp_server=_build_mcp_server(
                            headers={
                                "Authorization": "Bearer [user.token]",
                                "X-Static": "ok",
                            },
                            auth_config={"id": "cfg-both"},
                            audience="aud-1",
                        ),
                        user_id="user-1",
                        execution_context=both_context,
                    )
                    neither_config = MCPToolkitService._prepare_server_config(
                        mcp_server=_build_mcp_server(headers={"X-Static": "ok"}),
                        user_id="user-1",
                        execution_context=MCPExecutionContext(user_id="user-1"),
                    )

    assert auth_only_config.headers == {"X-User": "Test User"}
    assert auth_only_context.auth_headers == {"Authorization": "Bearer user-1"}
    assert legacy_only_config.headers == {"Authorization": "Bearer legacy-token"}
    assert both_config.headers == {"X-Static": "ok"}
    assert both_context.auth_headers == {"Authorization": "Bearer user-1"}
    assert neither_config.headers == {"X-Static": "ok"}
    assert mock_factory.get_token_for_current_user.call_count == 1
    mock_oidc.get_exchanged_token.assert_not_called()
    assert all("{{user.token}}" not in value for value in both_config.headers.values())


def test_prepare_server_config_placeholder_only_servers_loop() -> None:
    with patch("codemie.service.mcp.toolkit_service.get_current_user", return_value=_build_user()):
        with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
            mock_factory.get_token_for_current_user.return_value = "legacy-token"

            for index in range(8):
                try:
                    server_config = MCPToolkitService._prepare_server_config(
                        mcp_server=MCPServerDetails(
                            name=f"legacy-server-{index}",
                            enabled=True,
                            config=MCPServerConfig(
                                command="uvx",
                                args=["example-server"],
                                env={},
                                headers={"Authorization": "Bearer [user.token]"},
                            ),
                        ),
                        user_id="user-1",
                        execution_context=MCPExecutionContext(user_id="user-1"),
                    )
                except MCPAuthenticationRequiredException as exc:
                    pytest.fail(f"Unexpected MCPAuthenticationRequiredException for legacy-server-{index}: {exc}")

                assert server_config.headers == {"Authorization": "Bearer legacy-token"}
                assert mock_factory.get_token_for_current_user.call_count == index + 1
