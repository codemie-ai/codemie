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

import ipaddress
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from codemie.rest_api.models.dynamic_config import ConfigValueType, DynamicConfig


def _config(value: str, value_type: ConfigValueType = ConfigValueType.STRING) -> DynamicConfig | SimpleNamespace:
    from codemie.enterprise.mcp_auth import dependencies

    if len(value) > dependencies.MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST_MAX_LENGTH:
        return SimpleNamespace(
            key=dependencies.MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST,
            value=value,
            value_type=value_type,
        )
    return DynamicConfig(
        id="config-private-network",
        key=dependencies.MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST,
        value=value,
        value_type=value_type,
        updated_by="admin",
    )


@pytest.mark.asyncio
async def test_private_network_allowlist_reader_returns_tuple_for_valid_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    mock_get = AsyncMock(return_value=_config('["10.0.0.0/8", "192.168.1.10"]'))
    monkeypatch.setattr(dependencies.DynamicConfigService, "aget_by_key", mock_get)

    result = await dependencies.read_mcp_auth_discovery_private_network_allowlist_config()

    assert result == ("10.0.0.0/8", "192.168.1.10")
    assert tuple(str(ipaddress.ip_network(entry, strict=False)) for entry in result) == (
        "10.0.0.0/8",
        "192.168.1.10/32",
    )
    mock_get.assert_awaited_once_with(dependencies.MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST)


@pytest.mark.asyncio
async def test_private_network_allowlist_reader_output_passes_to_enterprise_as_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codemie.enterprise.mcp_auth import dependencies
    from codemie_enterprise.mcp_auth.discovery import discover_authorization_server_metadata

    mock_get = AsyncMock(return_value=_config('["10.0.0.0/8", "192.168.1.10"]'))
    monkeypatch.setattr(dependencies.DynamicConfigService, "aget_by_key", mock_get)
    allowlist = await dependencies.read_mcp_auth_discovery_private_network_allowlist_config()
    fetcher_calls: list[tuple[str, dict[str, Any]]] = []

    async def fetcher(url: str, **kwargs: Any) -> httpx.Response:
        fetcher_calls.append((url, kwargs))
        payload = {
            "issuer": "https://auth.example.com/tenant1",
            "authorization_endpoint": "https://auth.example.com/oauth/authorize",
            "token_endpoint": "https://auth.example.com/oauth/token",
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
        }
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(payload).encode("utf-8"),
        )

    result = await discover_authorization_server_metadata(
        "https://auth.example.com/tenant1",
        fetcher=fetcher,
        allowed_private_networks=allowlist,
        discovery_timeout_seconds=10.0,
    )

    assert result.status == "discovered"
    assert fetcher_calls[0][1]["allowed_private_networks"] == allowlist
    assert isinstance(fetcher_calls[0][1]["allowed_private_networks"], tuple)


@pytest.mark.asyncio
async def test_private_network_allowlist_reader_missing_config_returns_empty_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    monkeypatch.setattr(dependencies.DynamicConfigService, "aget_by_key", AsyncMock(return_value=None))

    assert await dependencies.read_mcp_auth_discovery_private_network_allowlist_config() == ()


@pytest.mark.parametrize(
    "raw_value",
    [
        "{not-json",
        '{"cidr": "10.0.0.0/8"}',
        '["10.0.0.0/8", 123]',
        '["10.0.0.0/8", " "]',
        '["example.com"]',
        '["10.0.0.0/999"]',
        '["999.999.999.999"]',
    ],
)
@pytest.mark.asyncio
async def test_private_network_allowlist_reader_rejects_invalid_config_without_raw_payload_leak(
    raw_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    monkeypatch.setattr(dependencies.DynamicConfigService, "aget_by_key", AsyncMock(return_value=_config(raw_value)))

    with pytest.raises(Exception) as exc_info:
        await dependencies.read_mcp_auth_discovery_private_network_allowlist_config()

    assert dependencies.MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST in str(exc_info.value)
    assert raw_value not in str(exc_info.value)


@pytest.mark.asyncio
async def test_private_network_allowlist_reader_wrong_type_fails_closed_without_raw_payload_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    monkeypatch.setattr(
        dependencies.DynamicConfigService,
        "aget_by_key",
        AsyncMock(return_value=_config('["10.0.0.0/8"]', ConfigValueType.BOOL)),
    )

    with pytest.raises(Exception) as exc_info:
        await dependencies.read_mcp_auth_discovery_private_network_allowlist_config()

    assert dependencies.MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST in str(exc_info.value)
    assert "10.0.0.0/8" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_private_network_allowlist_reader_enforces_raw_length_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    valid_raw = '["10.0.0.0/8"]'.ljust(dependencies.MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST_MAX_LENGTH)
    monkeypatch.setattr(dependencies.DynamicConfigService, "aget_by_key", AsyncMock(return_value=_config(valid_raw)))
    assert await dependencies.read_mcp_auth_discovery_private_network_allowlist_config() == ("10.0.0.0/8",)

    invalid_raw = valid_raw + " "
    monkeypatch.setattr(dependencies.DynamicConfigService, "aget_by_key", AsyncMock(return_value=_config(invalid_raw)))
    with pytest.raises(Exception) as exc_info:
        await dependencies.read_mcp_auth_discovery_private_network_allowlist_config()

    assert dependencies.MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST in str(exc_info.value)
    assert invalid_raw not in str(exc_info.value)


def test_private_network_allowlist_bridge_public_exports_exist() -> None:
    from codemie.enterprise import mcp_auth
    from codemie.enterprise.mcp_auth import dependencies

    for name in (
        "MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST",
        "MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST_MAX_LENGTH",
        "read_mcp_auth_discovery_private_network_allowlist_config",
    ):
        assert name in mcp_auth.__all__
        assert getattr(mcp_auth, name) == getattr(dependencies, name)
