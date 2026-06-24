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

import asyncio
import hashlib
import math
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from codemie.core.exceptions import MCPAuthenticationRequiredException
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.service.mcp.access_control import MCPAccessControlService
from codemie.service.mcp.client import BUCKET_KEY, MCPConnectClient
from codemie.service.mcp.models import MCPExecutionContext, MCPServerConfig, MCPToolDefinition, MCPToolLoadException
from codemie.rest_api.security.user import User
from codemie.service.mcp.toolkit_service import LegacyTokenResolver, MCPToolkitService


@pytest.fixture(autouse=True)
def _stub_discovery_probe_runtime_config():
    """Bypass DB-backed discovery config resolution for tests in this module.

    MCPToolkitService._resolve_discovery_probe_runtime_config reads dynamic config
    via the synchronous engine before crossing into the discovery bridge. Tests in
    this module patch the bridge directly and never reach the DB, so we stub the
    resolution with safe defaults.
    """
    with patch.object(
        MCPToolkitService,
        "_resolve_discovery_probe_runtime_config",
        return_value=((), object()),
    ):
        yield


def _build_mcp_server(
    *,
    name: str = "auth-enabled-server",
    headers: dict[str, str] | None = None,
    auth_config: dict[str, str] | None = None,
    audience: str | None = None,
    mcp_config_id: str | None = None,
    url: str | None = None,
    transport_type: str | None = None,
) -> MCPServerDetails:
    command = None if url else "uvx"
    args = [] if url else ["example-server"]
    return MCPServerDetails(
        name=name,
        enabled=True,
        mcp_config_id=mcp_config_id,
        config=MCPServerConfig(
            command=command,
            url=url,
            args=args,
            env={},
            headers=headers or {},
            type=transport_type,
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


def _http_status_error(status_code: int, www_authenticate: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://bridge.example.com/bridge")
    headers = {"WWW-Authenticate": www_authenticate} if www_authenticate is not None else {}
    response = httpx.Response(status_code, headers=headers, request=request)
    return httpx.HTTPStatusError("auth challenge", request=request, response=response)


def _http_status_error_with_raw_url() -> httpx.HTTPStatusError:
    request = httpx.Request(
        "POST",
        "https://bridge.example.com/bridge?token=secret-token&user=user@example.com",
    )
    response = httpx.Response(
        401,
        headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.com/.well-known"'},
        request=request,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("response.raise_for_status() must raise HTTPStatusError")


def _wrapped_tool_load_error(
    server_name: str,
    status_code: int,
    www_authenticate: str
    | None = 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"',
) -> MCPToolLoadException:
    http_error = _http_status_error(status_code, www_authenticate)
    tool_error = MCPToolLoadException(server_name, RuntimeError("generic wrapper"))
    tool_error.__cause__ = http_error
    return tool_error


def _wrapped_client_value_error_tool_load_error(server_name: str, status_code: int) -> MCPToolLoadException:
    http_error = _http_status_error(status_code, www_authenticate=None)
    client_error = ValueError("unauthorized")
    client_error.__cause__ = http_error
    tool_error = MCPToolLoadException(server_name, RuntimeError("generic wrapper"))
    tool_error.__cause__ = client_error
    return tool_error


def _build_user() -> User:
    user = MagicMock(spec=User)
    user.id = "user-1"
    user.name = "Test User"
    user.username = "test.user"
    return user


def test_mcp_server_config_repr_redacts_discovery_sensitive_fields() -> None:
    server_config = MCPServerConfig(
        url="https://mcp.example.com/api/mcp?token=secret-token&user=user@example.com",
        type="streamable-http",
        headers={"Authorization": "Bearer secret-token", "Cookie": "sid=session-secret"},
        env={"ACCESS_TOKEN": "secret-token"},
        auth_token="secret-token",
        auth_config={"id": "auth-secret", "auth_type": "oauth2"},
    )

    config_repr = repr(server_config)

    assert "secret-token" not in config_repr
    assert "session-secret" not in config_repr
    assert "user@example.com" not in config_repr
    assert "token=" not in config_repr
    assert "Authorization" not in config_repr
    assert "Cookie" not in config_repr


def test_header_placeholder_processing_logs_sanitized_metadata() -> None:
    server_config = MCPServerConfig(
        url="https://mcp.example.com/api/mcp",
        type="streamable-http",
        headers={
            "Authorization": "Bearer {{ACCESS_TOKEN}}",
            "Cookie": "sid={{SESSION_SECRET}}",
            "X-Trace": "{{TRACE_ID}}",
        },
        env={
            "ACCESS_TOKEN": "secret-token",
            "SESSION_SECRET": "session-secret",
            "TRACE_ID": "trace-123",
        },
    )

    with patch("codemie.service.mcp.toolkit_service.logger.debug") as mock_debug:
        MCPToolkitService._process_headers_placeholders(server_config, None)

    log_text = " ".join(str(call.args[0]) for call in mock_debug.call_args_list)
    assert "secret-token" not in log_text
    assert "session-secret" not in log_text
    assert "trace-123" not in log_text
    assert "Bearer" not in log_text
    assert "sid=" not in log_text


def test_legacy_token_resolver_logs_no_token_or_user_identifier() -> None:
    server_config = MCPServerConfig(
        url="https://mcp.example.com/api/mcp",
        type="streamable-http",
        headers={"Authorization": "Bearer {{user.token}}"},
    )

    current_user = SimpleNamespace(name="User Name", username="user@example.com")
    with patch("codemie.service.mcp.toolkit_service.get_current_user", return_value=current_user):
        with patch(
            "codemie.service.mcp.toolkit_service.token_exchange_service.get_token_for_current_user",
            return_value="secret-token",
        ):
            with patch("codemie.service.mcp.toolkit_service.logger.debug") as mock_debug:
                LegacyTokenResolver().resolve(server_config, user_id="user-1")

    log_text = " ".join(str(call.args[0]) for call in mock_debug.call_args_list)
    assert "secret-token" not in log_text
    assert "user@example.com" not in log_text
    assert "Bearer" not in log_text


def test_token_placeholder_resolution_error_log_omits_exception_details() -> None:
    headers = {"Authorization": "Bearer {{user.token}}"}
    env_vars: dict[str, object] = {}
    current_user = SimpleNamespace(name="User Name", username="user@example.com")

    with patch("codemie.service.mcp.toolkit_service.get_current_user", return_value=current_user):
        with patch(
            "codemie.service.mcp.toolkit_service.token_exchange_service.get_token_for_current_user",
            side_effect=RuntimeError("secret-token"),
        ):
            with patch("codemie.service.mcp.toolkit_service.logger.error") as mock_error:
                MCPToolkitService._add_user_token_if_needed(headers, env_vars, audience=None)

    assert mock_error.call_count == 1
    assert mock_error.call_args.kwargs == {}
    assert "Failed to retrieve token for placeholder resolution" in mock_error.call_args.args[0]
    assert "user@example.com" not in mock_error.call_args.args[0]


@pytest.mark.asyncio
async def test_toolkit_cache_logs_sanitized_server_config() -> None:
    server_config = MCPServerConfig(
        url="https://mcp.example.com/api/mcp?token=secret-token&user=user@example.com",
        type="streamable-http",
        headers={"Authorization": "Bearer secret-token", "Cookie": "sid=session-secret"},
        env={"ACCESS_TOKEN": "secret-token"},
    )
    cached_toolkit = MagicMock(name="cached-toolkit")
    created_toolkit = MagicMock(name="created-toolkit")
    service = MCPToolkitService(MagicMock(spec=MCPConnectClient))
    service.toolkit_factory = MagicMock()
    service.toolkit_factory.get_toolkit.side_effect = [cached_toolkit, None]
    service.toolkit_factory.create_toolkit = AsyncMock(return_value=created_toolkit)

    with patch("codemie.service.mcp.toolkit_service.logger.info") as mock_info:
        assert await service.get_toolkit_async(server_config) is cached_toolkit
        assert await service.get_toolkit_async(server_config) is created_toolkit

    log_text = " ".join(str(call.args[0]) for call in mock_info.call_args_list)
    assert "secret-token" not in log_text
    assert "session-secret" not in log_text
    assert "user@example.com" not in log_text
    assert "token=" not in log_text
    assert "Authorization" not in log_text
    assert "Cookie" not in log_text


def test_process_single_mcp_server_sanitizes_http_status_error_log() -> None:
    http_error = _http_status_error_with_raw_url()
    toolkit_service = MagicMock(spec=MCPToolkitService)
    toolkit_service.get_toolkit.side_effect = http_error
    mcp_server = _build_mcp_server(
        name="challenged-server",
        url="https://mcp.example.com/api/mcp?token=secret-token&user=user@example.com",
        transport_type="streamable-http",
    )

    with patch("codemie.service.mcp.toolkit_service.logger.error") as mock_error:
        with pytest.raises(MCPToolLoadException):
            MCPToolkitService._process_single_mcp_server(
                mcp_server=mcp_server,
                default_toolkit_service=toolkit_service,
            )

    log_text = " ".join(str(call.args[0]) for call in mock_error.call_args_list)
    assert "401 Unauthorized" in log_text
    assert "status_code=401" in log_text
    assert "https://bridge.example.com/..." in log_text


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


def test_prepare_server_config_keeps_conversation_id_out_of_bridge_env() -> None:
    server_config = MCPToolkitService._prepare_server_config(
        mcp_server=_build_mcp_server(),
        conversation_id="conversation-1",
    )

    assert server_config is not None
    assert server_config.env is not None
    assert BUCKET_KEY not in server_config.env
    assert getattr(server_config, "bucket_key", None) == "conversation-1"


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
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with pytest.raises(MCPToolLoadException) as exc_info:
                MCPToolkitService.get_mcp_server_tools(mcp_servers, user_id="user-1")

    assert exc_info.value is tool_load_error


def test_get_mcp_server_tools_batches_initial_401_http_discovery_challenges() -> None:
    mcp_servers = [
        _build_mcp_server(
            name=f"http-{index}",
            mcp_config_id=f"cfg-{index}",
            url=f"https://mcp{index}.example.com/api/mcp",
            transport_type="streamable-http",
        )
        for index in range(5)
    ]

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        raise _wrapped_tool_load_error(mcp_server.name, 401)

    captured_candidates: list[dict[str, str]] = []

    async def _probe(candidates: list[dict[str, str]], **_kwargs: object) -> list[object]:
        captured_candidates.extend(candidates)
        return []

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe", side_effect=_probe):
                with pytest.raises(MCPAuthenticationRequiredException):
                    MCPToolkitService.get_mcp_server_tools(mcp_servers, user_id="user-1")

    assert [candidate["server_name"] for candidate in captured_candidates] == [server.name for server in mcp_servers]
    assert [candidate["mcp_resource_url"] for candidate in captured_candidates] == [
        server.config.url for server in mcp_servers
    ]


def test_get_mcp_server_tools_does_not_discover_auth_configured_or_403_challenges() -> None:
    auth_configured = _build_mcp_server(
        name="auth-configured",
        auth_config={"id": "auth-1", "auth_type": "oauth2"},
        url="https://auth-configured.example.com/api/mcp",
        transport_type="streamable-http",
    )
    insufficient_scope = _build_mcp_server(
        name="insufficient-scope",
        url="https://mcp.example.com/api/mcp",
        transport_type="streamable-http",
    )

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        if mcp_server.name == "auth-configured":
            raise _wrapped_tool_load_error(mcp_server.name, 401)
        raise _wrapped_tool_load_error(mcp_server.name, 403)

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe") as mock_probe:
                with pytest.raises(MCPToolLoadException):
                    MCPToolkitService.get_mcp_server_tools([auth_configured, insufficient_scope], user_id="user-1")

    mock_probe.assert_not_called()


def test_get_mcp_server_tools_does_not_discover_401_without_www_authenticate() -> None:
    challenged = _build_mcp_server(
        name="missing-www-authenticate",
        url="https://mcp.example.com/api/mcp",
        transport_type="streamable-http",
    )

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        raise _wrapped_tool_load_error(mcp_server.name, 401, www_authenticate=None)

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe") as mock_probe:
                with pytest.raises(MCPToolLoadException):
                    MCPToolkitService.get_mcp_server_tools([challenged], user_id="user-1")

    mock_probe.assert_not_called()


def test_get_mcp_server_tools_does_not_discover_url_less_http_metadata() -> None:
    url_less_http = _build_mcp_server(
        name="url-less-http",
        transport_type="streamable-http",
    )

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        raise _wrapped_tool_load_error(mcp_server.name, 401)

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe") as mock_probe:
                with pytest.raises(MCPToolLoadException):
                    MCPToolkitService.get_mcp_server_tools([url_less_http], user_id="user-1")

    mock_probe.assert_not_called()


def test_get_mcp_server_tools_auth_configured_401_is_fail_closed_not_discovery() -> None:
    auth_configured = _build_mcp_server(
        name="auth-configured",
        mcp_config_id="mcp-auth",
        auth_config={"id": "auth-1", "auth_type": "oauth2"},
        url="https://auth-configured.example.com/api/mcp",
        transport_type="streamable-http",
    )

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        raise _wrapped_tool_load_error(mcp_server.name, 401)

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe") as mock_probe:
                with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                    MCPToolkitService.get_mcp_server_tools([auth_configured], user_id="user-1")

    payload = exc_info.value.payload
    assert payload["error"] == "authentication_required"
    assert payload["servers"] == [
        {
            "auth_config_id": "auth-1",
            "mcp_config_id": "mcp-auth",
            "mcp_config_name": "auth-configured",
            "mcp_server_name": "auth-configured",
            "auth_type": "oauth2",
            "as_hostname": None,
            "status": "authentication_required",
            "error_context": "MCP server rejected configured authentication.",
            "initiate_url": "/v1/mcp-auth/oauth2/initiate",
        }
    ]
    assert "warnings" not in payload
    mock_probe.assert_not_called()


def test_get_mcp_server_tools_auth_configured_401_without_www_authenticate_is_fail_closed() -> None:
    auth_configured = _build_mcp_server(
        name="auth-configured",
        mcp_config_id="mcp-auth",
        auth_config={"id": "auth-1", "auth_type": "oauth2"},
        url="https://auth-configured.example.com/api/mcp",
        transport_type="streamable-http",
    )

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        raise _wrapped_client_value_error_tool_load_error(mcp_server.name, 401)

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe") as mock_probe:
                with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                    MCPToolkitService.get_mcp_server_tools([auth_configured], user_id="user-1")

    payload = exc_info.value.payload
    assert payload["servers"][0]["status"] == "authentication_required"
    assert payload["servers"][0]["auth_config_id"] == "auth-1"
    assert "warnings" not in payload
    mock_probe.assert_not_called()


def test_get_mcp_server_tools_empty_auth_config_is_config_error_not_discovery() -> None:
    auth_configured = _build_mcp_server(
        name="empty-auth-config",
        mcp_config_id="mcp-auth-empty",
        auth_config={},
        url="https://auth-configured.example.com/api/mcp",
        transport_type="streamable-http",
    )

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPAccessControlService, "resolve_catalog_config", side_effect=lambda s: s):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe") as mock_probe:
                with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                    MCPToolkitService.get_mcp_server_tools([auth_configured], user_id="user-1")

    payload = exc_info.value.payload
    assert payload["servers"][0]["status"] == "config_error"
    assert payload["servers"][0]["mcp_config_id"] == "mcp-auth-empty"
    assert "warnings" not in payload
    mock_probe.assert_not_called()


def test_get_mcp_server_tools_discards_discovery_batch_when_non_auth_failure_occurs() -> None:
    challenged = _build_mcp_server(
        name="challenged",
        url="https://mcp.example.com/api/mcp",
        transport_type="streamable-http",
    )
    broken = _build_mcp_server(name="broken")
    non_auth_error = MCPToolLoadException("broken", RuntimeError("boom"))

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        if mcp_server.name == "challenged":
            raise _wrapped_tool_load_error(mcp_server.name, 401)
        raise non_auth_error

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe") as mock_probe:
                with pytest.raises(MCPToolLoadException) as exc_info:
                    MCPToolkitService.get_mcp_server_tools([challenged, broken], user_id="user-1")

    assert exc_info.value is non_auth_error
    mock_probe.assert_not_called()


def test_get_mcp_server_tools_preserves_healthy_and_legacy_tools_with_discovery_warnings() -> None:
    healthy_tool = MagicMock(name="healthy-tool")
    legacy_tool = MagicMock(name="legacy-tool")
    challenged = _build_mcp_server(
        name="challenged",
        url="https://mcp.example.com/api/mcp",
        transport_type="streamable-http",
    )
    healthy = _build_mcp_server(name="healthy")
    legacy = _build_mcp_server(name="legacy", headers={"Authorization": "{{user.token}}"})

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        if mcp_server.name == "challenged":
            raise _wrapped_tool_load_error(mcp_server.name, 401)
        if mcp_server.name == "healthy":
            return [healthy_tool]
        return [legacy_tool]

    async def _probe(candidates: list[dict[str, str]], **_kwargs: object) -> list[object]:
        return []

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe", side_effect=_probe):
                with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                    MCPToolkitService.get_mcp_server_tools([challenged, healthy, legacy], user_id="user-1")

    payload = exc_info.value.payload
    assert payload["error"] == "authentication_required"
    assert payload["servers"][0]["status"] == "discovery_failed"


def test_get_mcp_server_tools_records_discovery_failed_warning_for_missing_probe_result() -> None:
    from codemie.service.mcp.auth_warnings import get_mcp_auth_warnings

    healthy_tool = MagicMock(name="healthy-tool")
    challenged = _build_mcp_server(
        name="challenged",
        mcp_config_id="mcp-discovery",
        url="https://mcp.example.com/api/mcp",
        transport_type="streamable-http",
    )
    healthy = _build_mcp_server(name="healthy")

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        if mcp_server.name == "challenged":
            raise _wrapped_tool_load_error(mcp_server.name, 401)
        return [healthy_tool]

    async def _probe(candidates: list[dict[str, str]], **_kwargs: object) -> list[object]:
        del candidates
        return []

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe", side_effect=_probe):
                with pytest.raises(MCPAuthenticationRequiredException):
                    MCPToolkitService.get_mcp_server_tools([challenged, healthy], user_id="user-1")

    warnings = get_mcp_auth_warnings(clear=True)
    assert warnings == [
        {
            "status": "discovery_failed",
            "mcp_config_id": "mcp-discovery",
            "mcp_config_name": "challenged",
            "mcp_server_name": "challenged",
            "error_context": "Discovery failed: missing_probe_result. Configure auth_config manually for this server.",
        }
    ]


def test_get_mcp_server_tools_records_per_candidate_warnings_when_probe_bridge_raises() -> None:
    from codemie.service.mcp.auth_warnings import get_mcp_auth_warnings

    healthy_tool = MagicMock(name="healthy-tool")
    challenged_servers = [
        _build_mcp_server(
            name=f"challenged-{index}",
            mcp_config_id=f"mcp-discovery-{index}",
            url=f"https://mcp{index}.example.com/api/mcp",
            transport_type="streamable-http",
        )
        for index in range(2)
    ]
    healthy = _build_mcp_server(name="healthy")

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        if mcp_server.name.startswith("challenged-"):
            raise _wrapped_tool_load_error(mcp_server.name, 401)
        return [healthy_tool]

    async def _probe(candidates: list[dict[str, str]], **_kwargs: object) -> list[object]:
        del candidates
        raise RuntimeError("Authorization: Bearer secret-token Cookie=sid")

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe", side_effect=_probe):
                with pytest.raises(MCPAuthenticationRequiredException):
                    MCPToolkitService.get_mcp_server_tools(
                        [*challenged_servers, healthy],
                        user_id="user-1",
                    )

    warnings = get_mcp_auth_warnings(clear=True)
    warning_text = str(warnings)
    assert [warning["mcp_config_id"] for warning in warnings] == ["mcp-discovery-0", "mcp-discovery-1"]
    assert [warning["error_context"] for warning in warnings] == [
        "Discovery failed: discovery_bridge_unavailable. Configure auth_config manually for this server.",
        "Discovery failed: discovery_bridge_unavailable. Configure auth_config manually for this server.",
    ]
    for sensitive_text in ("secret-token", "Authorization", "Cookie", "sid"):
        assert sensitive_text not in warning_text


def test_get_mcp_server_tools_batches_401_challenges_under_10s_p95_with_fake_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codemie.configs import config as runtime_config
    from codemie.enterprise.mcp_auth import dependencies

    mcp_servers = [
        _build_mcp_server(
            name=f"http-{index}",
            url=f"https://mcp{index}.example.com/api/mcp",
            transport_type="streamable-http",
        )
        for index in range(5)
    ]
    captured_counts: list[int] = []

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        raise _wrapped_tool_load_error(mcp_server.name, 401)

    class FakeDiscoveryProbeCandidate:
        def __init__(self, **kwargs: object) -> None:
            self.server_name = str(kwargs["server_name"])

    async def _probe(**kwargs: object) -> list[dict[str, str]]:
        candidates = list(kwargs["candidates"])
        captured_counts.append(len(candidates))
        captured_limits.append(kwargs["concurrency_limit"])
        active = 0
        max_active = 0
        semaphore = asyncio.Semaphore(int(kwargs["concurrency_limit"]))

        async def _probe_one(candidate: FakeDiscoveryProbeCandidate) -> dict[str, str]:
            nonlocal active, max_active
            async with semaphore:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.02)
                active -= 1
            return {
                "server_name": candidate.server_name,
                "status": "discovery_failed",
                "failure_reason": "timeout",
            }

        results = await asyncio.gather(*(_probe_one(candidate) for candidate in candidates))
        captured_max_active.append(max_active)
        return results

    durations = []
    sample_count = 8
    captured_limits: list[object] = []
    captured_max_active: list[int] = []
    fake_discovery_module = SimpleNamespace(
        DiscoveryProbeCandidate=FakeDiscoveryProbeCandidate,
        probe_discovery_eligible_servers=_probe,
    )
    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_DISCOVERY_CONCURRENCY_LIMIT", 5)
    monkeypatch.setattr(dependencies, "_mcp_auth_discovery_cache", object())
    monkeypatch.setattr(
        dependencies,
        "_mcp_auth_service",
        SimpleNamespace(
            config=SimpleNamespace(
                discovery_probe_overall_timeout_seconds=30.0,
                enforce_https=True,
                resource_metadata_discovery_timeout_seconds=10.0,
                as_metadata_discovery_timeout_seconds=10.0,
            )
        ),
    )
    monkeypatch.setattr(dependencies, "import_module", lambda _: fake_discovery_module)
    monkeypatch.setattr(
        dependencies,
        "read_mcp_auth_discovery_private_network_allowlist_config",
        AsyncMock(return_value=()),
    )
    for _ in range(sample_count):
        start = time.perf_counter()
        with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
            with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
                with pytest.raises(MCPAuthenticationRequiredException):
                    MCPToolkitService.get_mcp_server_tools(mcp_servers, user_id="user-1")
        durations.append(time.perf_counter() - start)

    p95_index = math.ceil(0.95 * len(durations)) - 1
    p95_duration = sorted(durations)[p95_index]

    assert captured_counts == [5] * sample_count
    assert captured_limits == [5] * sample_count
    assert captured_max_active == [5] * sample_count
    assert p95_duration < 10


def test_get_mcp_server_tools_records_warning_only_discovery_failed_payload() -> None:
    from codemie.service.mcp.auth_warnings import get_mcp_auth_warnings

    healthy_tool = MagicMock(name="healthy-tool")
    legacy_tool = MagicMock(name="legacy-tool")
    challenged = _build_mcp_server(
        name="challenged",
        mcp_config_id="mcp-discovery",
        url="https://mcp.example.com/api/mcp",
        transport_type="streamable-http",
    )
    healthy = _build_mcp_server(name="healthy")
    legacy = _build_mcp_server(name="legacy", headers={"Authorization": "{{user.token}}"})
    discovery_result = SimpleNamespace(
        server_name="challenged",
        status="discovery_failed",
        failure_reason="timeout",
        error_context={
            "source_url": "https://mcp.example.com/api?token=abc",
            "WWW-Authenticate": "Bearer secret-token",
            "Cookie": "session=secret",
            "user": "user@example.com",
        },
    )

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        if mcp_server.name == "challenged":
            raise _wrapped_tool_load_error(
                mcp_server.name,
                401,
                'Bearer resource_metadata="https://mcp.example.com/prm?token=abc"',
            )
        if mcp_server.name == "healthy":
            return [healthy_tool]
        return [legacy_tool]

    async def _probe(candidates: list[dict[str, str]], **_kwargs: object) -> list[object]:
        return [discovery_result]

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe", side_effect=_probe):
                with pytest.raises(MCPAuthenticationRequiredException):
                    MCPToolkitService.get_mcp_server_tools([challenged, healthy, legacy], user_id="user-1")

    warnings = get_mcp_auth_warnings(clear=True)
    warning_text = str(warnings)
    assert warnings == [
        {
            "status": "discovery_failed",
            "mcp_config_id": "mcp-discovery",
            "mcp_config_name": "challenged",
            "mcp_server_name": "challenged",
            "error_context": "Discovery failed: timeout. Configure auth_config manually for this server.",
        }
    ]
    assert "auth_config_id" not in warnings[0]
    assert "initiate_url" not in warnings[0]
    assert "secret-token" not in warning_text
    assert "Cookie" not in warning_text
    assert "token=abc" not in warning_text
    assert "user@example.com" not in warning_text


def test_discovery_failed_warning_for_trust_rejection_includes_allowlist_guidance() -> None:
    payloads = MCPToolkitService._build_discovery_warning_payloads(
        [
            {
                "server_name": "challenged",
                "mcp_server_name": "challenged",
                "mcp_config_name": "challenged",
                "mcp_config_id": "mcp-discovery",
            }
        ],
        [
            SimpleNamespace(
                status="discovery_failed",
                failure_reason="no_trusted_authorization_server",
            )
        ],
    )

    assert payloads == [
        {
            "status": "discovery_failed",
            "mcp_config_id": "mcp-discovery",
            "mcp_config_name": "challenged",
            "mcp_server_name": "challenged",
            "error_context": (
                "Discovery failed: no_trusted_authorization_server. "
                "Add the AS domain to the trust allowlist or configure auth_config manually for this server."
            ),
        }
    ]
    assert "auth_config_id" not in payloads[0]
    assert "initiate_url" not in payloads[0]


def test_get_mcp_server_tools_successful_discovery_becomes_blocking_auth_gate_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenged = _build_mcp_server(
        name="challenged",
        mcp_config_id="mcp-discovery",
        url="https://mcp.example.com/api/mcp",
        transport_type="streamable-http",
    )
    discovery_result = SimpleNamespace(
        server_name="challenged",
        canonical_resource_uri="https://mcp.example.com/api/mcp",
        status="discovered",
        protected_resource_metadata={},
        authorization_server_metadata={"issuer": "https://auth.example.com"},
    )
    expected_session_hash = hashlib.sha256(b"Bearer session-token").hexdigest()
    captured: dict[str, object] = {}

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        raise _wrapped_tool_load_error(
            mcp_server.name,
            401,
            'Bearer resource_metadata="https://mcp.example.com/prm", scope="fresh.read"',
        )

    async def _probe(candidates: list[dict[str, str]], **_kwargs: object) -> list[object]:
        return [discovery_result]

    async def _build_gate_payloads(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return [
            {
                "auth_config_id": "discovered:" + "a" * 64,
                "discovered_flow_id": "flow-1",
                "mcp_config_id": "mcp-discovery",
                "mcp_config_name": "challenged",
                "mcp_server_name": "challenged",
                "auth_type": "oauth2",
                "as_hostname": "auth.example.com",
                "status": "authentication_required",
                "error_context": None,
                "initiate_url": "/v1/mcp-auth/oauth2/initiate?discovered_flow_id=flow-1",
            }
        ]

    monkeypatch.setattr("codemie.service.mcp.toolkit_service.get_current_auth_token", lambda: "Bearer session-token")
    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe", side_effect=_probe):
                with patch(
                    "codemie.service.mcp.toolkit_service.build_mcp_auth_discovered_auth_gate_payloads",
                    side_effect=_build_gate_payloads,
                ):
                    with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                        MCPToolkitService.get_mcp_server_tools([challenged], user_id="user-1")

    payload = exc_info.value.payload
    assert payload["servers"][0]["status"] == "authentication_required"
    assert payload["servers"][0]["discovered_flow_id"] == "flow-1"
    assert payload["servers"][0]["auth_config_id"].startswith("discovered:")
    assert "warnings" not in payload
    assert captured["user_id"] == "user-1"
    assert captured["session_binding_hash"] == expected_session_hash
    assert captured["discovery_results"] == [discovery_result]


@pytest.mark.asyncio
async def test_nfr23_discovered_pipeline_preserves_current_scope_and_invokes_tool_with_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codemie.configs import config as runtime_config
    from codemie.enterprise.mcp_auth import dependencies
    from codemie_enterprise.mcp_auth.discovery import DiscoveryProbeCandidate, probe_discovery_eligible_servers
    from codemie_enterprise.mcp_auth.models import OAuth2TokenData
    from codemie_enterprise.mcp_auth.resolver import MCPAuthResolver
    from codemie_enterprise.mcp_auth.tms_mock import MockTokenManagementSystem

    class InMemoryDiscoveryCache:
        def __init__(self) -> None:
            self.entries: dict[str, object] = {}

        def get(self, resource: str) -> object | None:
            return self.entries.get(resource)

        def set(self, resource: str, entry: object, cache_control_header: str | None = None) -> None:
            _ = cache_control_header
            self.entries[resource] = entry

    class InMemoryDiscoveredFlowStore:
        def __init__(self) -> None:
            self.snapshots: dict[str, object] = {}

        def store(self, snapshot: object) -> None:
            self.snapshots[getattr(snapshot, "discovered_flow_id")] = snapshot

        def get(self, discovered_flow_id: str) -> object | None:
            return self.snapshots.get(discovered_flow_id)

        def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
            for snapshot in self.snapshots.values():
                if (
                    getattr(snapshot, "user_id") == user_id
                    and getattr(snapshot, "session_binding_hash") == session_binding_hash
                    and getattr(snapshot, "mcp_config_id") == mcp_config_id
                ):
                    return snapshot
            return None

    class FakePKCEStore:
        def __init__(self) -> None:
            self.states: dict[str, object] = {}

        def store(self, state: str, data: object) -> None:
            self.states[state] = data

    class TrustAllAuthExample:
        async def is_authorization_server_trusted(self, issuer_url: str, server_name: str) -> bool:
            _ = server_name
            return issuer_url == "https://auth.example.com"

    canonical_resource = "https://mcp.example.com/api/mcp"
    issuer = "https://auth.example.com"
    session_hash = hashlib.sha256(b"Bearer session-token").hexdigest()
    discovery_cache = InMemoryDiscoveryCache()
    flow_store = InMemoryDiscoveredFlowStore()
    pkce_store = FakePKCEStore()
    fetched_urls: list[str] = []

    async def fake_metadata_fetcher(source_url: str, **kwargs: object) -> httpx.Response:
        _ = kwargs
        fetched_urls.append(source_url)
        if source_url == "https://mcp.example.com/prm":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                json={
                    "resource": canonical_resource,
                    "authorization_servers": [issuer],
                    "scopes_supported": ["fallback.read"],
                },
            )
        if source_url == f"{issuer}/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                json={
                    "issuer": issuer,
                    "authorization_endpoint": f"{issuer}/oauth2/authorize",
                    "token_endpoint": f"{issuer}/oauth2/token",
                    "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"],
                    "scopes_supported": ["as.read"],
                    "client_id_metadata_document_supported": True,
                },
            )
        return httpx.Response(404, headers={"Content-Type": "application/json"}, json={})

    discovery_result = (
        await probe_discovery_eligible_servers(
            candidates=[
                DiscoveryProbeCandidate(
                    server_name="Catalog",
                    mcp_resource_url="https://mcp.example.com/api/mcp",
                    www_authenticate_header=(
                        'Bearer resource_metadata="https://mcp.example.com/prm", scope="stale.read"'
                    ),
                )
            ],
            discovery_cache=discovery_cache,
            trust_policy_service=TrustAllAuthExample(),
            overall_timeout_seconds=30.0,
            protected_resource_discovery_kwargs={"fetcher": fake_metadata_fetcher, "discovery_timeout_seconds": 10.0},
            authorization_server_discovery_kwargs={"fetcher": fake_metadata_fetcher, "discovery_timeout_seconds": 10.0},
        )
    )[0]
    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(dependencies.config, "CALLBACK_API_BASE_URL", "https://codemie.example.com")
    monkeypatch.setattr(dependencies, "_mcp_auth_discovered_flow_store", flow_store)
    monkeypatch.setattr(dependencies, "_mcp_auth_dcr_credentials_cache", object())
    monkeypatch.setattr(dependencies, "_pkce_store", pkce_store)
    monkeypatch.setattr(dependencies, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setattr(
        dependencies,
        "_mcp_auth_service",
        SimpleNamespace(
            config=SimpleNamespace(
                allow_local_client_metadata_document_url=False,
                enforce_https=True,
                dcr_registration_timeout_seconds=10.0,
            )
        ),
    )

    gate_payloads = await dependencies.build_mcp_auth_discovered_auth_gate_payloads(
        discovery_candidates=[
            {
                "server_name": "Catalog",
                "mcp_server_name": "Catalog",
                "mcp_config_name": "Catalog",
                "mcp_config_id": "mcp-config-1",
                "www_authenticate_header": (
                    'Bearer resource_metadata="https://mcp.example.com/prm", scope="fresh.read fresh.write"'
                ),
            }
        ],
        discovery_results=[discovery_result],
        user_id="user-1",
        session_binding_hash=session_hash,
        allowed_private_networks=(),
    )
    discovered_flow_id = gate_payloads[0]["discovered_flow_id"]
    snapshot = flow_store.get(discovered_flow_id)

    initiate_response = dependencies.build_discovered_oauth2_initiate_response(
        mcp_config=SimpleNamespace(id="mcp-config-1", config=SimpleNamespace(auth_config=None)),
        user=SimpleNamespace(id="user-1", auth_token="Bearer session-token"),
        discovered_flow_id=discovered_flow_id,
    )
    query = parse_qs(urlsplit(initiate_response.auth_url).query)
    tms = MockTokenManagementSystem()
    tms.store(
        "user-1",
        getattr(snapshot, "discovered_auth_id"),
        OAuth2TokenData(
            access_token="stored-token",
            token_type="Bearer",
            resource=canonical_resource,
            issuer=issuer,
            flow_source="discovered",
        ),
    )
    discovery_cache.entries.clear()
    resolver = MCPAuthResolver(
        tms,
        lambda auth_config_id, **kwargs: RuntimeError(f"auth-required:{auth_config_id}:{kwargs}"),
        discovery_cache=discovery_cache,
        discovered_flow_store=flow_store,
    )
    server_config = MCPServerConfig(
        url="https://MCP.Example.Com:443/api/mcp?ignored=1#fragment",
        type="streamable-http",
        headers={"X-Workspace": "catalog"},
        env={},
        mcp_config_id="mcp-config-1",
    )
    execution_context = MCPExecutionContext(user_id="user-1", auth_headers={}, session_binding_hash=session_hash)
    resolver.resolve(server_config, "user-1", execution_context)
    mock_response = MagicMock()
    mock_response.json.return_value = {"content": [{"type": "text", "text": "ok"}], "isError": False}
    mock_response.raise_for_status = MagicMock()

    with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
        invocation = await MCPConnectClient().invoke_tool(
            server_config,
            "search_catalog",
            {"q": "widget"},
            execution_context,
        )

    post_payload = mock_client.return_value.__aenter__.return_value.post.call_args.kwargs["json"]
    assert gate_payloads[0]["status"] == "authentication_required"
    assert gate_payloads[0]["as_hostname"] == "auth.example.com"
    assert discovery_result.status == "discovered"
    assert discovery_result.from_cache is False
    assert fetched_urls == [
        "https://mcp.example.com/prm",
        "https://auth.example.com/.well-known/oauth-authorization-server",
    ]
    assert getattr(snapshot, "registration_method") == "client_id_metadata_document"
    assert getattr(snapshot, "flow_config").client_id == "https://codemie.example.com/oauth/client-metadata.json"
    assert query["scope"] == ["fresh.read fresh.write"]
    assert query["resource"] == [canonical_resource]
    assert "stale.read" not in initiate_response.auth_url
    assert "fallback.read" not in initiate_response.auth_url
    assert execution_context.auth_headers == {"Authorization": "Bearer stored-token"}
    assert post_payload["mcp_headers"] == {"X-Workspace": "catalog", "Authorization": "Bearer stored-token"}
    assert invocation.content[0].text == "ok"
    assert invocation.isError is False


def test_get_mcp_server_tools_mixed_auth_required_and_discovery_warning_payloads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    auth_required_error = MCPAuthenticationRequiredException(
        {
            "auth_config_id": "auth-1",
            "mcp_server_name": "auth-configured",
            "status": "authentication_required",
            "auth_type": "oauth2",
        }
    )
    auth_configured = _build_mcp_server(
        name="auth-configured",
        mcp_config_id="mcp-auth",
        auth_config={"id": "auth-1", "auth_type": "oauth2"},
    )
    challenged = _build_mcp_server(
        name="challenged",
        mcp_config_id="mcp-discovery",
        url="https://mcp.example.com/api/mcp",
        transport_type="streamable-http",
    )
    healthy_tool = MagicMock(name="healthy-tool")
    healthy = _build_mcp_server(name="healthy")
    sensitive_challenge = (
        'Bearer resource_metadata="https://mcp.example.com/prm?token=secret-token&user=user@example.com", '
        'error_description="Authorization: Bearer secret-token Cookie=sid=secret user_id=user-123"'
    )
    captured_candidates: list[dict[str, str]] = []

    def _process_server(*, mcp_server: MCPServerDetails, **_: object) -> list[MagicMock]:
        if mcp_server.name == "auth-configured":
            raise auth_required_error
        if mcp_server.name == "challenged":
            raise _wrapped_tool_load_error(mcp_server.name, 401, sensitive_challenge)
        return [healthy_tool]

    async def _probe(candidates: list[dict[str, str]], **_kwargs: object) -> list[object]:
        captured_candidates.extend(candidates)
        return [
            SimpleNamespace(
                server_name="challenged",
                status="discovery_failed",
                failure_reason="unexpected_error",
                error_context={
                    "authorization": "Bearer secret-token",
                    "cookie": "sid=secret",
                    "source_url": "https://mcp.example.com?secret=1&user=user@example.com",
                    "user_id": "user-123",
                },
            )
        ]

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process_server):
            with patch("codemie.service.mcp.toolkit_service.run_mcp_auth_parallel_discovery_probe", side_effect=_probe):
                with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                    MCPToolkitService.get_mcp_server_tools([auth_configured, challenged, healthy], user_id="user-1")

    payload = exc_info.value.payload
    assert payload["error"] == "authentication_required"
    assert payload["servers"][0]["mcp_server_name"] == "auth-configured"
    assert payload["servers"][0]["status"] == "authentication_required"
    assert captured_candidates[0]["www_authenticate_header"] == sensitive_challenge
    assert payload["warnings"] == [
        {
            "status": "discovery_failed",
            "mcp_config_id": "mcp-discovery",
            "mcp_config_name": "challenged",
            "mcp_server_name": "challenged",
            "error_context": "Discovery failed: unexpected_error. Configure auth_config manually for this server.",
        }
    ]
    assert "auth_config_id" not in payload["warnings"][0]
    assert "initiate_url" not in payload["warnings"][0]

    warning_text = str(payload["warnings"])
    log_text = caplog.text
    for sensitive_text in (
        "secret-token",
        "Authorization",
        "Cookie",
        "sid=secret",
        "token=",
        "user@example.com",
        "user-123",
    ):
        assert sensitive_text not in warning_text
        assert sensitive_text not in log_text


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


def test_prepare_server_config_falls_back_to_legacy_when_discovered_token_missing() -> None:
    from codemie_enterprise.mcp_auth.models import DiscoveryMetadataCacheEntry
    from codemie_enterprise.mcp_auth.resolver import MCPAuthResolver
    from codemie_enterprise.mcp_auth.tms_mock import MockTokenManagementSystem

    canonical_resource = "https://mcp.example.com/api/mcp"
    discovery_cache = SimpleNamespace(
        get=lambda resource: DiscoveryMetadataCacheEntry(
            protected_resource_metadata={},
            authorization_server_metadata={"issuer": "https://auth.example.com"},
        )
        if resource == canonical_resource
        else None
    )
    resolver = MCPAuthResolver(
        MockTokenManagementSystem(),
        lambda auth_config_id, **kwargs: RuntimeError(f"auth-required:{auth_config_id}:{kwargs}"),
        discovery_cache=discovery_cache,
    )

    with patch.object(MCPToolkitService, "_auth_resolvers", [resolver]):
        with patch.object(MCPAccessControlService, "resolve_catalog_config", side_effect=lambda s: s):
            with patch("codemie.service.mcp.toolkit_service.get_current_user", return_value=_build_user()):
                with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
                    mock_factory.get_token_for_current_user.return_value = "legacy-token"

                    server_config = MCPToolkitService._prepare_server_config(
                        mcp_server=_build_mcp_server(
                            mcp_config_id="mcp-config-1",
                            url="https://MCP.Example.Com:443/api/mcp?v=1#section",
                            transport_type="streamable-http",
                            headers={"Authorization": "Bearer [user.token]"},
                        ),
                        user_id="user-1",
                        execution_context=MCPExecutionContext(user_id="user-1"),
                    )

    mock_factory.get_token_for_current_user.assert_called_once()
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


class _AuthConfigHeaderResolver:
    """OAuth2-style stub resolver: matches only servers with `auth_config`,
    and writes ``execution_context.auth_headers["Authorization"]`` (mimicking
    the enterprise ``MCPAuthResolver`` + ``HeaderTokenDelivery`` combination).
    """

    def __init__(self, token: str = "OAUTH_TOKEN") -> None:
        self._token = token
        self.calls: list[tuple[MCPServerConfig, str | None, MCPExecutionContext | None]] = []

    def can_handle(self, server_config: MCPServerConfig) -> bool:
        return bool(server_config.auth_config)

    def resolve(
        self,
        server_config: MCPServerConfig,
        user_id: str | None,
        execution_context: MCPExecutionContext | None = None,
    ) -> None:
        self.calls.append((server_config, user_id, execution_context))
        if execution_context is None:
            return
        if execution_context.auth_headers is None:
            execution_context.auth_headers = {}
        execution_context.auth_headers["Authorization"] = f"Bearer {self._token}"


def _capture_list_tools_calls() -> tuple[AsyncMock, list[tuple[MCPServerConfig, MCPExecutionContext | None]]]:
    """Build a mocked ``MCPConnectClient.list_tools`` that records each call's
    (server_config snapshot, execution_context snapshot) pair. Snapshots are
    taken at await-time so per-iteration mutations are observable even though
    the same MCPExecutionContext type is shared by the test harness.
    """
    captured: list[tuple[MCPServerConfig, MCPExecutionContext | None]] = []

    async def _record(
        server_config: MCPServerConfig,
        execution_context: MCPExecutionContext | None = None,
    ) -> list[MCPToolDefinition]:
        captured.append(
            (
                server_config.model_copy(deep=True),
                execution_context.model_copy(deep=True) if execution_context is not None else None,
            )
        )
        return [_build_tool_definition()]

    return AsyncMock(side_effect=_record), captured


def _build_oauth_server() -> MCPServerDetails:
    server = _build_mcp_server(
        name="oauth-server",
        mcp_config_id="mcp-oauth",
        headers={},
        auth_config={
            "id": "auth-oauth",
            "auth_type": "oauth2",
            "authorization_url": "https://login.example.com/oauth2/authorize",
        },
    )
    # Distinct command/args/env so the toolkit cache does not collapse the two
    # servers onto the same key (cache key is derived from command/url/args/env).
    server.config.args = ["oauth-server"]
    return server


def _build_legacy_server() -> MCPServerDetails:
    server = _build_mcp_server(
        name="legacy-server",
        mcp_config_id="mcp-legacy",
        headers={"Authorization": "Bearer {{user.token}}"},
        auth_config=None,
    )
    server.config.args = ["legacy-server"]
    return server


@pytest.mark.parametrize(
    "server_order",
    [("oauth-first", "legacy-second"), ("legacy-first", "oauth-second")],
    ids=["oauth_then_legacy", "legacy_then_oauth"],
)
def test_get_mcp_server_tools_isolates_auth_headers_per_server(
    monkeypatch: pytest.MonkeyPatch,
    server_order: tuple[str, str],
) -> None:
    """Regression: a server using the OAuth2 ``HeaderTokenDelivery`` resolver
    must not contaminate another server's ``Authorization`` via the shared
    ``MCPExecutionContext.auth_headers`` dict (see ``_merge_mcp_headers`` —
    ``auth_headers`` wins on collision). Verified independently of order.
    """
    mock_list_tools, captured = _capture_list_tools_calls()
    mock_client = MagicMock(spec=MCPConnectClient)
    mock_client.base_url = "http://mock-mcp-connect"
    mock_client.list_tools = mock_list_tools
    MCPToolkitService.init_singleton(mock_client)

    resolver = _AuthConfigHeaderResolver(token="OAUTH_TOKEN")
    monkeypatch.setattr(MCPToolkitService, "_auth_resolvers", [resolver])

    oauth_server = _build_oauth_server()
    legacy_server = _build_legacy_server()
    servers = [oauth_server, legacy_server] if server_order[0] == "oauth-first" else [legacy_server, oauth_server]

    with patch.object(MCPAccessControlService, "resolve_catalog_config", side_effect=lambda s: s):
        with patch("codemie.service.mcp.toolkit_service.get_current_user", return_value=_build_user()):
            with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
                mock_factory.get_token_for_current_user.return_value = "LEGACY_TOKEN"

                MCPToolkitService.get_mcp_server_tools(servers, user_id="user-1", assistant_id="assistant-1")

    assert mock_list_tools.await_count == 2

    by_name = {
        snapshot[0].auth_config["id"] if snapshot[0].auth_config else "legacy": snapshot for snapshot in captured
    }
    oauth_snapshot = by_name["auth-oauth"]
    legacy_snapshot = by_name["legacy"]

    # OAuth2 server: resolver wrote auth_headers["Authorization"]; static headers untouched.
    assert oauth_snapshot[1] is not None
    assert oauth_snapshot[1].auth_headers == {"Authorization": "Bearer OAUTH_TOKEN"}
    assert oauth_snapshot[0].headers == {}

    # Legacy server: own resolver wrote into server_config.headers; per-server execution
    # context starts with auth_headers=None so the OAuth2 token cannot leak in.
    assert legacy_snapshot[0].headers == {"Authorization": "Bearer LEGACY_TOKEN"}
    assert legacy_snapshot[1] is not None
    assert legacy_snapshot[1].auth_headers in (None, {})


def test_get_mcp_server_tools_does_not_mutate_caller_execution_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request-scoped ``execution_context`` constructed inside
    ``get_mcp_server_tools`` should remain pristine across iterations; per-server
    contexts are clones. This guards against leakage back into shared callers."""
    mock_list_tools, _captured = _capture_list_tools_calls()
    mock_client = MagicMock(spec=MCPConnectClient)
    mock_client.base_url = "http://mock-mcp-connect"
    mock_client.list_tools = mock_list_tools
    MCPToolkitService.init_singleton(mock_client)

    resolver = _AuthConfigHeaderResolver(token="OAUTH_TOKEN")
    monkeypatch.setattr(MCPToolkitService, "_auth_resolvers", [resolver])

    captured_parents: list[MCPExecutionContext] = []
    original_process = MCPToolkitService._process_single_mcp_server.__func__

    def _spy(cls, *, execution_context, **kwargs):
        # The per-server context is a clone, not the parent. Record it for assertions.
        captured_parents.append(execution_context)
        return original_process(cls, execution_context=execution_context, **kwargs)

    with patch("codemie.service.mcp.toolkit_service.get_current_user", return_value=_build_user()):
        with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
            mock_factory.get_token_for_current_user.return_value = "LEGACY_TOKEN"
            with patch.object(MCPToolkitService, "_process_single_mcp_server", classmethod(_spy)):
                MCPToolkitService.get_mcp_server_tools(
                    [_build_oauth_server(), _build_legacy_server()],
                    user_id="user-1",
                    assistant_id="assistant-1",
                )

    # Each server got its own context object (different ids), proving isolation.
    assert len(captured_parents) == 2
    assert captured_parents[0] is not captured_parents[1]
    # Each clone carries the request-scoped fields verbatim from the parent.
    for ctx in captured_parents:
        assert ctx.user_id == "user-1"
        assert ctx.assistant_id == "assistant-1"


def test_get_mcp_server_tools_isolates_auth_required_payload_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When one server raises MCPAuthenticationRequiredException, the per-server
    context handed to ``_build_auth_required_server_payload`` must be the same
    clone the resolver saw — not the shared parent — so workflow_execution_id
    handling stays consistent with the rest of the loop."""
    payload = {
        "auth_config_id": "auth-oauth",
        "status": "authentication_required",
        "auth_type": "oauth2",
    }
    auth_error = MCPAuthenticationRequiredException(payload)

    monkeypatch.setattr(MCPToolkitService, "_auth_resolvers", [])

    def _process(*, mcp_server, execution_context, **_):
        if mcp_server.name == "oauth-server":
            raise auth_error
        return [MagicMock(name="legacy-tool")]

    with patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()):
        with patch.object(MCPToolkitService, "_process_single_mcp_server", side_effect=_process):
            with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                MCPToolkitService.get_mcp_server_tools(
                    [_build_oauth_server(), _build_legacy_server()],
                    user_id="user-1",
                    assistant_id="assistant-1",
                    workflow_execution_id="wf-1",
                )

    # workflow_execution_id is preserved on the per-server clone, so initiate_url
    # is correctly omitted by _build_auth_required_server_payload.
    failures = exc_info.value.payload["servers"]
    assert len(failures) == 1
    assert failures[0]["mcp_server_name"] == "oauth-server"
    assert "initiate_url" not in failures[0]


def test_build_discovery_candidate_includes_allow_issuer_prefix_match_true() -> None:
    server = MCPServerDetails(
        name="atlassian-rovo",
        enabled=True,
        mcp_config_id="mcp-rovo",
        config=MCPServerConfig(
            url="https://api.atlassian.com/ex/rovo/mcp",
            allow_issuer_prefix_match=True,
        ),
    )
    http_error = _http_status_error(
        401,
        www_authenticate='Bearer resource_metadata="https://api.atlassian.com/.well-known/oauth-protected-resource"',
    )
    exc = MCPToolLoadException("atlassian-rovo", http_error)
    exc.__cause__ = http_error

    candidate = MCPToolkitService._build_discovery_candidate_from_challenge(server, exc)

    assert candidate is not None
    assert candidate["allow_issuer_prefix_match"] is True


def test_build_discovery_candidate_defaults_allow_issuer_prefix_match_false() -> None:
    server = MCPServerDetails(
        name="default-server",
        enabled=True,
        mcp_config_id="mcp-default",
        config=MCPServerConfig(url="https://mcp.example.com/api"),
    )
    http_error = _http_status_error(
        401,
        www_authenticate='Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"',
    )
    exc = MCPToolLoadException("default-server", http_error)
    exc.__cause__ = http_error

    candidate = MCPToolkitService._build_discovery_candidate_from_challenge(server, exc)

    assert candidate is not None
    assert candidate["allow_issuer_prefix_match"] is False


# ── Approach C: skip_auth_resolution flag ────────────────────────────────────


class _CallTrackingResolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def can_handle(self, server_config: object) -> bool:
        self.calls.append("can_handle")
        return True

    def resolve(self, server_config: object, user_id: object, execution_context: object) -> bool:
        self.calls.append("resolve")
        return True


def test_resolve_server_auth_skip_bypasses_enterprise_loop_and_legacy_fallback(monkeypatch) -> None:
    from codemie.service.mcp.models import MCPServerConfig
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    server_config = MCPServerConfig(url="https://mcp.example.com/")
    tracker = _CallTrackingResolver()
    legacy_calls: list[str] = []

    monkeypatch.setattr(MCPToolkitService, "_auth_resolvers", [tracker])
    monkeypatch.setattr(
        MCPToolkitService._legacy_token_resolver, "can_handle", lambda _: legacy_calls.append("can_handle") or True
    )
    monkeypatch.setattr(MCPToolkitService._legacy_token_resolver, "resolve", lambda *_: legacy_calls.append("resolve"))

    # Raises TypeError today ("unexpected keyword argument 'skip_auth_resolution'") → RED
    MCPToolkitService._resolve_server_auth(
        server_config, user_id=None, execution_context=None, skip_auth_resolution=True
    )

    assert tracker.calls == [], "skip_auth_resolution=True must bypass enterprise resolver loop"
    assert legacy_calls == [], "skip_auth_resolution=True must bypass legacy fallback"


def test_resolve_server_auth_skip_false_still_calls_resolvers(monkeypatch) -> None:
    from codemie.service.mcp.models import MCPServerConfig
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    server_config = MCPServerConfig(url="https://mcp.example.com/")
    tracker = _CallTrackingResolver()

    monkeypatch.setattr(MCPToolkitService, "_auth_resolvers", [tracker])

    MCPToolkitService._resolve_server_auth(
        server_config, user_id=None, execution_context=None, skip_auth_resolution=False
    )

    assert "can_handle" in tracker.calls, "skip_auth_resolution=False must run resolvers as normal"


# ── Approach C: _mcp_server_from_config ──────────────────────────────────────


def _make_mcp_config_for_c(
    *,
    config_id: str = "mcp-config-1",
    name: str = "test-server",
    url: str = "https://mcp.example.com/",
    allow_issuer_prefix_match: bool = False,
) -> object:
    from types import SimpleNamespace

    config_data = SimpleNamespace(
        url=url,
        type="streamable_http",
        command=None,
        args=[],
        headers={},
        env={},
        auth_config=None,
        auth_token=None,
        single_usage=False,
        tools=None,
        audience=None,
        allow_issuer_prefix_match=allow_issuer_prefix_match,
        bucket_key=None,
    )
    config_data.model_dump = lambda: {
        "url": config_data.url,
        "type": config_data.type,
        "command": config_data.command,
        "args": config_data.args,
        "headers": config_data.headers,
        "env": config_data.env,
        "auth_config": config_data.auth_config,
        "auth_token": config_data.auth_token,
        "single_usage": config_data.single_usage,
        "tools": config_data.tools,
        "audience": config_data.audience,
        "allow_issuer_prefix_match": config_data.allow_issuer_prefix_match,
        "bucket_key": config_data.bucket_key,
    }
    return SimpleNamespace(id=config_id, name=name, config=config_data)


def test_mcp_server_from_config_returns_mcp_server_details() -> None:
    from codemie.rest_api.models.assistant import MCPServerDetails
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config_for_c(config_id="cfg-1", name="my-server", url="https://mcp.example.com/")

    # AttributeError today: 'MCPToolkitService' has no attribute '_mcp_server_from_config' → RED
    result = MCPToolkitService._mcp_server_from_config(mcp_config)

    assert isinstance(result, MCPServerDetails)
    assert result.name == "my-server"
    assert result.mcp_config_id == "cfg-1"
    assert result.config.url == "https://mcp.example.com/"
    assert result.config.auth_config is None


def test_mcp_server_from_config_allow_issuer_prefix_match_round_trip() -> None:
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    # Atlassian-Rovo-style server that requires prefix matching
    mcp_config = _make_mcp_config_for_c(
        config_id="rovo-cfg",
        name="atlassian-rovo",
        url="https://rovo.atlassian.net/sse",
        allow_issuer_prefix_match=True,
    )

    result = MCPToolkitService._mcp_server_from_config(mcp_config)

    assert result.config.allow_issuer_prefix_match is True, (
        "allow_issuer_prefix_match must survive MCPServerConfigData → MCPServerConfig mapping "
        "so _build_discovery_candidate_from_challenge carries it to _resolve_discovered_candidate_payload"
    )


def test_mcp_server_from_config_candidate_fields_are_reachable() -> None:
    """_build_discovery_candidate_from_challenge reads mcp_config_id from outer MCPServerDetails
    and allow_issuer_prefix_match from inner MCPServerConfig — both must be set correctly."""
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config_for_c(config_id="cfg-2", name="srv", url="https://srv.example.com/")
    result = MCPToolkitService._mcp_server_from_config(mcp_config)

    assert result.mcp_config_id == "cfg-2", "outer mcp_config_id used by _build_discovery_candidate_from_challenge"
    assert result.name == "srv"
    assert result.config.url == "https://srv.example.com/"
    assert result.config.auth_config is None, "auth_config must be None for discovery candidate guard to pass"
    assert (
        result.config is not None
    ), "inline config must be set so _build_mcp_server_config does not do a catalog DB lookup"


# ── Approach C: ensure_discovered_snapshot_for_server ────────────────────────


def _make_401_load_exception(www_authenticate: str = 'Bearer realm="test"') -> object:
    from codemie.service.mcp.models import MCPToolLoadException

    response = httpx.Response(
        status_code=401,
        headers={"WWW-Authenticate": www_authenticate},
        request=httpx.Request("GET", "https://mcp.example.com/"),
    )
    http_error = httpx.HTTPStatusError("401 Unauthorized", request=response.request, response=response)
    exc = MCPToolLoadException("test-server", http_error)
    exc.__cause__ = http_error
    return exc


def test_ensure_discovered_snapshot_returns_flow_id_on_challenge(monkeypatch) -> None:
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config_for_c(config_id="cfg-discover", name="disco", url="https://mcp.example.com/")
    exc = _make_401_load_exception()

    monkeypatch.setattr(
        MCPToolkitService,
        "_process_single_mcp_server",
        classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(exc)),
    )
    monkeypatch.setattr(
        MCPToolkitService,
        "_run_discovery_probe_and_collect_failures",
        classmethod(lambda cls, **kwargs: ([{"discovered_flow_id": "flow-healed-1"}], [])),
    )

    # AttributeError today: 'MCPToolkitService' has no attribute 'ensure_discovered_snapshot_for_server' → RED
    result = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="user-1",
        session_binding_hash="binding-hash-1",
    )

    assert result == "flow-healed-1"


def test_ensure_discovered_snapshot_returns_none_when_no_401(monkeypatch) -> None:
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config_for_c(config_id="cfg-open", name="open-server", url="https://mcp.example.com/")

    monkeypatch.setattr(
        MCPToolkitService,
        "_process_single_mcp_server",
        classmethod(lambda cls, **kwargs: []),
    )

    result = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="user-1",
        session_binding_hash="binding-hash-1",
    )

    assert result is None


def test_ensure_discovered_snapshot_returns_none_when_401_has_no_www_authenticate(monkeypatch) -> None:
    from codemie.service.mcp.models import MCPToolLoadException
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config_for_c(config_id="cfg-bare401", name="bare", url="https://mcp.example.com/")

    response = httpx.Response(
        status_code=401,
        headers={},
        request=httpx.Request("GET", "https://mcp.example.com/"),
    )
    http_error = httpx.HTTPStatusError("401", request=response.request, response=response)
    exc = MCPToolLoadException("bare", http_error)
    exc.__cause__ = http_error

    monkeypatch.setattr(
        MCPToolkitService,
        "_process_single_mcp_server",
        classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(exc)),
    )

    result = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="user-1",
        session_binding_hash="binding-hash-1",
    )

    assert result is None


def test_ensure_discovered_snapshot_returns_none_on_mcp_auth_required(monkeypatch) -> None:
    from codemie.core.exceptions import MCPAuthenticationRequiredException
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config_for_c(config_id="cfg-auth", name="auth-server", url="https://mcp.example.com/")

    monkeypatch.setattr(
        MCPToolkitService,
        "_process_single_mcp_server",
        classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(MCPAuthenticationRequiredException({}))),
    )

    result = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="user-1",
        session_binding_hash="binding-hash-1",
    )

    assert result is None


def test_ensure_discovered_snapshot_user_id_asymmetry(monkeypatch) -> None:
    """Connect uses user_id=None (credential-less); probe uses real user_id so binding stores correctly."""
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config_for_c(config_id="cfg-uid", name="uid-server", url="https://mcp.example.com/")
    exc = _make_401_load_exception()

    connect_user_ids: list[object] = []
    probe_kwargs: dict = {}

    def fake_process(**kwargs):
        connect_user_ids.append(kwargs.get("user_id", "NOT_SET"))
        raise exc

    def fake_probe(**kwargs):
        probe_kwargs.update(kwargs)
        return [{"discovered_flow_id": "flow-uid-1"}], []

    monkeypatch.setattr(
        MCPToolkitService, "_process_single_mcp_server", classmethod(lambda cls, **kw: fake_process(**kw))
    )
    monkeypatch.setattr(
        MCPToolkitService, "_run_discovery_probe_and_collect_failures", classmethod(lambda cls, **kw: fake_probe(**kw))
    )

    MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="real-user-id",
        session_binding_hash="binding-hash-abc",
    )

    assert connect_user_ids == [None], "credential-less connect must pass user_id=None"
    assert probe_kwargs.get("user_id") == "real-user-id", "probe must use real user_id for correct binding storage"
    assert probe_kwargs.get("session_binding_hash") == "binding-hash-abc"


def test_ensure_discovered_snapshot_ssrf_path_via_probe(monkeypatch) -> None:
    """Heal always calls _run_discovery_probe_and_collect_failures (SSRF-gated), never a raw HTTP client."""
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config_for_c(config_id="cfg-ssrf", name="ssrf-server", url="https://mcp.example.com/")
    exc = _make_401_load_exception()
    probe_calls: list[dict] = []

    monkeypatch.setattr(
        MCPToolkitService,
        "_process_single_mcp_server",
        classmethod(lambda cls, **kw: (_ for _ in ()).throw(exc)),
    )
    monkeypatch.setattr(
        MCPToolkitService,
        "_run_discovery_probe_and_collect_failures",
        classmethod(lambda cls, **kw: probe_calls.append(kw) or ([{"discovered_flow_id": "flow-ssrf"}], [])),
    )

    result = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="user-1",
        session_binding_hash="binding-1",
    )

    assert result == "flow-ssrf"
    assert len(probe_calls) == 1, "SSRF-gated probe must be called exactly once"
