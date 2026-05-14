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

"""Unit tests for MCPAccessControlService."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ValidationException
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.rest_api.models.mcp_config import MCPConfig, MCPServerConfigData
from codemie.service.mcp.access_control import MCPAccessControlService
from codemie.service.mcp.models import MCPServerConfig

_CC = "codemie.service.mcp.access_control.customer_config"
_GET_BY_IDS = "codemie.service.mcp.access_control.MCPConfig.get_by_ids"
_FIND_BY_ID = "codemie.service.mcp.access_control.MCPConfig.find_by_id"


def _open_mode():
    m = MagicMock()
    m.is_component_enabled.return_value = False  # mcpCustomServersDisabled=False → custom servers allowed
    return patch(_CC, m)


def _restricted_mode():
    m = MagicMock()
    m.is_component_enabled.return_value = True  # mcpCustomServersDisabled=True → restricted
    return patch(_CC, m)


# ── Helpers ──────────────────────────────────────────────────────────


def _server(
    name: str = "srv",
    *,
    mcp_config_id: str | None = None,
    config: MCPServerConfig | None = None,
    command: str | None = None,
    arguments: str | None = None,
    mcp_connect_url: str | None = None,
    enabled: bool = True,
) -> MCPServerDetails:
    return MCPServerDetails(
        name=name,
        enabled=enabled,
        mcp_config_id=mcp_config_id,
        config=config,
        command=command,
        arguments=arguments,
        mcp_connect_url=mcp_connect_url,
    )


def _catalog_entry(
    id_: str = "cat-1",
    *,
    is_active: bool = True,
    is_public: bool = True,
    config: MCPServerConfigData | None = None,
) -> MCPConfig:
    entry = MagicMock(spec=MCPConfig)
    entry.id = id_
    entry.is_active = is_active
    entry.is_public = is_public
    entry.config = config or MCPServerConfigData(command="uvx")
    return entry


# ── validate_on_save — open mode ─────────────────────────────────────


class TestValidateOnSaveOpenMode:
    def test_empty_list_passes(self):
        with _open_mode():
            MCPAccessControlService.validate_on_save([])

    def test_inline_server_passes_in_open_mode(self):
        servers = [_server("s1", command="npx")]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[]):
                MCPAccessControlService.validate_on_save(servers)

    def test_valid_catalog_ref_passes(self):
        entry = _catalog_entry("cat-1")
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                MCPAccessControlService.validate_on_save(servers)

    def test_missing_catalog_entry_raises(self):
        servers = [_server("s1", mcp_config_id="missing")]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[]):
                with pytest.raises(ValidationException, match="not found"):
                    MCPAccessControlService.validate_on_save(servers)

    def test_inactive_catalog_entry_raises(self):
        entry = _catalog_entry("cat-1", is_active=False)
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                with pytest.raises(ValidationException, match="inactive"):
                    MCPAccessControlService.validate_on_save(servers)

    def test_non_public_catalog_entry_raises(self):
        entry = _catalog_entry("cat-1", is_public=False)
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                with pytest.raises(ValidationException, match="not public"):
                    MCPAccessControlService.validate_on_save(servers)

    def test_disabled_server_with_invalid_catalog_still_raises(self):
        # disabled servers are still validated
        servers = [_server("s1", mcp_config_id="missing", enabled=False)]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[]):
                with pytest.raises(ValidationException, match="not found"):
                    MCPAccessControlService.validate_on_save(servers)


# ── validate_on_save — restricted mode ───────────────────────────────


class TestValidateOnSaveRestrictedMode:
    def test_inline_server_raises(self):
        servers = [_server("s1", command="npx")]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="Custom MCP servers are not allowed"):
                MCPAccessControlService.validate_on_save(servers)

    def test_forbidden_field_config_raises(self):
        inline_cfg = MCPServerConfig(command="uvx")
        servers = [_server("s1", mcp_config_id="cat-1", config=inline_cfg)]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="not allowed when mcp_config_id is set"):
                MCPAccessControlService.validate_on_save(servers)

    def test_forbidden_field_command_raises(self):
        servers = [_server("s1", mcp_config_id="cat-1", command="uvx")]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="not allowed when mcp_config_id is set"):
                MCPAccessControlService.validate_on_save(servers)

    def test_forbidden_field_arguments_raises(self):
        servers = [_server("s1", mcp_config_id="cat-1", arguments="--arg")]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="not allowed when mcp_config_id is set"):
                MCPAccessControlService.validate_on_save(servers)

    def test_forbidden_field_mcp_connect_url_raises(self):
        servers = [_server("s1", mcp_config_id="cat-1", mcp_connect_url="http://x")]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="not allowed when mcp_config_id is set"):
                MCPAccessControlService.validate_on_save(servers)

    def test_inactive_catalog_raises_in_restricted_mode(self):
        entry = _catalog_entry("cat-1", is_active=False)
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                with pytest.raises(ValidationException, match="inactive"):
                    MCPAccessControlService.validate_on_save(servers)

    def test_non_public_catalog_raises_in_restricted_mode(self):
        entry = _catalog_entry("cat-1", is_public=False)
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                with pytest.raises(ValidationException, match="not public"):
                    MCPAccessControlService.validate_on_save(servers)

    def test_valid_catalog_ref_passes_in_restricted_mode(self):
        entry = _catalog_entry("cat-1")
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                MCPAccessControlService.validate_on_save(servers)

    def test_disabled_inline_server_raises_in_restricted_mode(self):
        # All servers are checked regardless of enabled flag
        servers = [_server("s1", command="npx", enabled=False)]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="Custom MCP servers are not allowed"):
                MCPAccessControlService.validate_on_save(servers)


# ── strip_inline_config ───────────────────────────────────────────────


class TestStripInlineConfig:
    def test_strips_fields_when_mcp_config_id_present(self):
        server = _server("s1", mcp_config_id="cat-1", command="uvx", mcp_connect_url="http://x")
        result = MCPAccessControlService.strip_inline_config([server])
        assert len(result) == 1
        assert result[0].command is None
        assert result[0].mcp_connect_url is None
        assert result[0].config is None
        assert result[0].arguments is None

    def test_preserves_fields_when_no_mcp_config_id(self):
        server = _server("s1", command="uvx")
        result = MCPAccessControlService.strip_inline_config([server])
        assert result[0].command == "uvx"

    def test_preserves_mcp_config_id_after_strip(self):
        server = _server("s1", mcp_config_id="cat-1", command="uvx")
        result = MCPAccessControlService.strip_inline_config([server])
        assert result[0].mcp_config_id == "cat-1"

    def test_mixed_servers_handled_correctly(self):
        s1 = _server("s1", mcp_config_id="cat-1", command="uvx")
        s2 = _server("s2", command="npx")
        result = MCPAccessControlService.strip_inline_config([s1, s2])
        assert result[0].command is None
        assert result[1].command == "npx"


# ── filter_for_runtime ────────────────────────────────────────────────


class TestFilterForRuntime:
    def test_returns_all_in_open_mode(self):
        servers = [_server("s1", command="uvx"), _server("s2", mcp_config_id="cat-1")]
        with _open_mode():
            result = MCPAccessControlService.filter_for_runtime(servers)
        assert result == servers

    def test_drops_inline_server_in_restricted_mode(self):
        servers = [_server("s1", command="uvx")]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[]):
                result = MCPAccessControlService.filter_for_runtime(servers)
        assert result == []

    def test_keeps_valid_catalog_server_in_restricted_mode(self):
        entry = _catalog_entry("cat-1")
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                result = MCPAccessControlService.filter_for_runtime(servers)
        assert len(result) == 1
        assert result[0].name == "s1"

    def test_drops_inactive_catalog_server_in_restricted_mode(self):
        entry = _catalog_entry("cat-1", is_active=False)
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                result = MCPAccessControlService.filter_for_runtime(servers)
        assert result == []

    def test_drops_non_public_catalog_server_in_restricted_mode(self):
        entry = _catalog_entry("cat-1", is_public=False)
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                result = MCPAccessControlService.filter_for_runtime(servers)
        assert result == []

    def test_mixed_servers_filtered_correctly(self):
        valid_entry = _catalog_entry("cat-1")
        inactive_entry = _catalog_entry("cat-2", is_active=False)
        servers = [
            _server("s1", command="uvx"),
            _server("s2", mcp_config_id="cat-1"),
            _server("s3", mcp_config_id="cat-2"),
        ]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[valid_entry, inactive_entry]):
                result = MCPAccessControlService.filter_for_runtime(servers)
        assert len(result) == 1
        assert result[0].name == "s2"


# ── resolve_catalog_config ────────────────────────────────────────────


class TestResolveCatalogConfig:
    def test_returns_unchanged_when_no_mcp_config_id(self):
        server = _server("s1", command="uvx")
        result = MCPAccessControlService.resolve_catalog_config(server)
        assert result is server

    def test_returns_none_when_catalog_entry_not_found(self):
        server = _server("s1", mcp_config_id="cat-1")
        with patch(_FIND_BY_ID, return_value=None):
            result = MCPAccessControlService.resolve_catalog_config(server)
        assert result is None

    def test_returns_none_when_catalog_entry_has_no_config(self):
        entry = MagicMock(spec=MCPConfig)
        entry.config = None
        server = _server("s1", mcp_config_id="cat-1")
        with patch(_FIND_BY_ID, return_value=entry):
            result = MCPAccessControlService.resolve_catalog_config(server)
        assert result is None

    def test_resolves_config_from_catalog(self):
        catalog_config = MCPServerConfigData(command="uvx", args=["mcp-server"])
        entry = _catalog_entry("cat-1", config=catalog_config)
        server = _server("s1", mcp_config_id="cat-1")
        with patch(_FIND_BY_ID, return_value=entry):
            result = MCPAccessControlService.resolve_catalog_config(server)
        assert result is not server
        assert result.config is not None
        assert result.config.command == "uvx"

    def test_returns_none_when_config_conversion_fails(self):
        catalog_config = MagicMock()
        catalog_config.model_dump.side_effect = RuntimeError("boom")
        entry = MagicMock(spec=MCPConfig)
        entry.config = catalog_config
        server = _server("s1", mcp_config_id="cat-1")
        with patch(_FIND_BY_ID, return_value=entry):
            result = MCPAccessControlService.resolve_catalog_config(server)
        assert result is None
