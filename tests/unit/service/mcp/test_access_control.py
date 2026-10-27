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
    use_custom_config: bool = False,
) -> MCPServerDetails:
    return MCPServerDetails(
        name=name,
        enabled=enabled,
        mcp_config_id=mcp_config_id,
        config=config,
        command=command,
        arguments=arguments,
        mcp_connect_url=mcp_connect_url,
        use_custom_config=use_custom_config,
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
                with pytest.raises(ValidationException, match="not available in the catalogue"):
                    MCPAccessControlService.validate_on_save(servers)

    def test_inactive_catalog_entry_raises(self):
        entry = _catalog_entry("cat-1", is_active=False)
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                with pytest.raises(ValidationException, match="not available in the catalogue"):
                    MCPAccessControlService.validate_on_save(servers)

    def test_non_public_catalog_entry_raises(self):
        entry = _catalog_entry("cat-1", is_public=False)
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                with pytest.raises(ValidationException, match="not available in the catalogue"):
                    MCPAccessControlService.validate_on_save(servers)

    def test_disabled_server_with_invalid_catalog_still_raises(self):
        # disabled servers are still validated
        servers = [_server("s1", mcp_config_id="missing", enabled=False)]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[]):
                with pytest.raises(ValidationException, match="not available in the catalogue"):
                    MCPAccessControlService.validate_on_save(servers)


# ── validate_on_save — restricted mode ───────────────────────────────


class TestValidateOnSaveRestrictedMode:
    def test_inline_server_raises(self):
        servers = [_server("s1", command="npx")]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="custom server"):
                MCPAccessControlService.validate_on_save(servers)

    def test_forbidden_field_config_raises(self):
        inline_cfg = MCPServerConfig(command="uvx")
        servers = [_server("s1", mcp_config_id="cat-1", config=inline_cfg)]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="custom configuration"):
                MCPAccessControlService.validate_on_save(servers)

    def test_forbidden_field_command_raises(self):
        servers = [_server("s1", mcp_config_id="cat-1", command="uvx")]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="custom configuration"):
                MCPAccessControlService.validate_on_save(servers)

    def test_forbidden_field_arguments_raises(self):
        servers = [_server("s1", mcp_config_id="cat-1", arguments="--arg")]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="custom configuration"):
                MCPAccessControlService.validate_on_save(servers)

    def test_forbidden_field_mcp_connect_url_raises(self):
        servers = [_server("s1", mcp_config_id="cat-1", mcp_connect_url="http://x")]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="custom configuration"):
                MCPAccessControlService.validate_on_save(servers)

    def test_inactive_catalog_raises_in_restricted_mode(self):
        entry = _catalog_entry("cat-1", is_active=False)
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                with pytest.raises(ValidationException, match="not available in the catalogue"):
                    MCPAccessControlService.validate_on_save(servers)

    def test_non_public_catalog_raises_in_restricted_mode(self):
        entry = _catalog_entry("cat-1", is_public=False)
        servers = [_server("s1", mcp_config_id="cat-1")]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                with pytest.raises(ValidationException, match="not available in the catalogue"):
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
            with pytest.raises(ValidationException, match="custom server"):
                MCPAccessControlService.validate_on_save(servers)

    def test_use_custom_config_true_raises_in_restricted_mode(self):
        # AC3: declaring custom mode on a catalog-ref server is itself an override.
        servers = [_server("s1", mcp_config_id="cat-1", use_custom_config=True)]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="custom configuration"):
                MCPAccessControlService.validate_on_save(servers)


# ── strip_inline_config ───────────────────────────────────────────────


class TestStripInlineConfig:
    def test_preserves_inline_fields_when_mcp_config_id_present(self):
        # Inline overrides are kept and win over the catalog at runtime.
        inline_cfg = MCPServerConfig(command="uvx")
        server = _server(
            "s1",
            mcp_config_id="cat-1",
            config=inline_cfg,
            command="uvx",
            mcp_connect_url="http://x",
        )
        result = MCPAccessControlService.strip_inline_config([server])
        assert len(result) == 1
        assert result[0].command == "uvx"
        assert result[0].mcp_connect_url == "http://x"
        assert result[0].config is inline_cfg
        assert result[0].mcp_config_id == "cat-1"

    def test_preserves_fields_when_no_mcp_config_id(self):
        server = _server("s1", command="uvx")
        result = MCPAccessControlService.strip_inline_config([server])
        assert result[0].command == "uvx"

    def test_mixed_servers_handled_correctly(self):
        s1 = _server("s1", mcp_config_id="cat-1", command="uvx")
        s2 = _server("s2", command="npx")
        result = MCPAccessControlService.strip_inline_config([s1, s2])
        assert result[0].command == "uvx"
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

    def test_inline_config_wins_over_catalog(self):
        # use_custom_config=True means: use inline config, skip catalog entirely
        inline_cfg = MCPServerConfig(command="npx")
        server = _server("s1", mcp_config_id="cat-1", config=inline_cfg, use_custom_config=True)
        with patch(_FIND_BY_ID) as find_by_id:
            result = MCPAccessControlService.resolve_catalog_config(server)
        assert result is server
        assert result.config is inline_cfg
        find_by_id.assert_not_called()

    def test_custom_config_true_with_config_returns_server_unchanged(self):
        # use_custom_config=True + inline config → server returned as-is, catalog not touched
        inline_cfg = MCPServerConfig(command="uvx")
        server = _server("s1", config=inline_cfg, use_custom_config=True)
        with patch(_FIND_BY_ID) as find_by_id:
            result = MCPAccessControlService.resolve_catalog_config(server)
        assert result is server
        assert result.config is inline_cfg
        find_by_id.assert_not_called()

    def test_custom_config_true_with_no_config_returns_none(self):
        # use_custom_config=True but no inline config → warning and None
        server = _server("s1", use_custom_config=True)
        with patch(_FIND_BY_ID) as find_by_id:
            result = MCPAccessControlService.resolve_catalog_config(server)
        assert result is None
        find_by_id.assert_not_called()

    def test_global_mode_fetches_catalog_overwriting_inline(self):
        # use_custom_config=False + mcp_config_id + inline config → catalog config wins
        catalog_config = MCPServerConfigData(command="uvx", args=["mcp-server"])
        entry = _catalog_entry("cat-1", config=catalog_config)
        inline_cfg = MCPServerConfig(command="npx")
        server = _server("s1", mcp_config_id="cat-1", config=inline_cfg, use_custom_config=False)
        with patch(_FIND_BY_ID, return_value=entry):
            result = MCPAccessControlService.resolve_catalog_config(server)
        assert result is not server
        assert result is not None
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


class TestValidateOnRestore:
    """Restoring stored servers (version rollback): restricted mode only, no catalog availability re-check."""

    def test_restricted_mode_refuses_inline_server(self):
        with _restricted_mode():
            with pytest.raises(ValidationException, match="rogue"):
                MCPAccessControlService.validate_on_restore([_server("rogue", command="npx")])

    def test_restricted_mode_allows_clean_catalog_ref_without_catalog_lookup(self):
        with _restricted_mode(), patch(_GET_BY_IDS) as get_by_ids:
            MCPAccessControlService.validate_on_restore([_server("cat", mcp_config_id="cfg-1")])
        get_by_ids.assert_not_called()

    @pytest.mark.parametrize("servers", [None, []])
    def test_restricted_mode_ignores_missing_servers(self, servers):
        with _restricted_mode():
            MCPAccessControlService.validate_on_restore(servers)

    def test_open_mode_allows_inline_server(self):
        with _open_mode():
            MCPAccessControlService.validate_on_restore([_server("ok", command="npx")])


# ── user-facing wording of the restricted-mode refusals ───────────────


class TestRestrictedModeMessages:
    """The messages end up in a toast: no internal field names, every offending server named once, safe name text."""

    def test_names_every_custom_server_in_one_message(self):
        servers = [_server("one", command="npx"), _server("two", command="uvx")]
        with _restricted_mode():
            with pytest.raises(ValidationException) as exc:
                MCPAccessControlService.validate_on_save(servers)
        assert "'one', 'two'" in str(exc.value)
        assert "Remove them" in str(exc.value) and "are custom servers" in str(exc.value)

    @pytest.mark.parametrize(
        "server",
        [
            _server("x", command="npx"),
            _server("x", mcp_config_id="cat-1", use_custom_config=True),
            _server("x", mcp_config_id="cat-1", config=MCPServerConfig(command="uvx")),
        ],
    )
    def test_message_has_no_internal_field_names(self, server):
        with _restricted_mode():
            with pytest.raises(ValidationException) as exc:
                MCPAccessControlService.validate_on_save([server])
        for internal in ("mcp_config_id", "use_custom_config", "'config'"):
            assert internal not in str(exc.value)
        assert "catalogue" in str(exc.value)

    def test_server_name_is_stripped_of_markup_and_bounded(self):
        servers = [_server("<img src=x onerror=alert(1)>" + "a" * 200, command="npx")]
        with _restricted_mode():
            with pytest.raises(ValidationException) as exc:
                MCPAccessControlService.validate_on_save(servers)
        message = str(exc.value)
        assert "<" not in message and ">" not in message
        assert "a" * 61 not in message

    def test_duplicate_catalog_entries_message_has_no_ids(self):
        servers = [_server("a", mcp_config_id="cat-secret-1"), _server("b", mcp_config_id="cat-secret-1")]
        with _open_mode():
            with pytest.raises(ValidationException) as exc:
                MCPAccessControlService.validate_on_save(servers)
        assert "added more than once" in str(exc.value)
        assert "cat-secret-1" not in str(exc.value)

    def test_restore_message_says_it_is_a_restore_and_gives_an_action_that_is_possible(self):
        with _restricted_mode():
            with pytest.raises(ValidationException, match="Can't restore this version") as exc:
                MCPAccessControlService.validate_on_restore([_server("rogue", command="npx")])
        assert "'rogue'" in str(exc.value)
        assert "Choose a version without them" in str(exc.value)
        assert "Remove it" not in str(exc.value)  # nothing can be removed from a stored version

    def test_restore_refuses_catalog_server_with_custom_configuration(self):
        """A catalogue server stored with a custom override must block a restore as well, not only custom servers."""
        with _restricted_mode():
            with pytest.raises(ValidationException, match="Can't restore this version") as exc:
                MCPAccessControlService.validate_on_restore(
                    [_server("cat", mcp_config_id="cat-1", use_custom_config=True)]
                )
        assert "'cat'" in str(exc.value)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("", "'unnamed'"),
            ("   ", "'unnamed'"),
            ("a\nb\r\tc", "'a b c'"),
            ("it's \"x\" &amp; `y`", "'it s x amp; y'"),
            ("\x00\x1bhidden", "'hidden'"),
            ("a\x85b", "'a b'"),  # C1 control (NEL)
            ("x\u202ey", "'x y'"),  # bidi override
            ("ze\u200bro\ufeff", "'ze ro'"),  # zero-width space, byte-order mark
            ("l1\u2028l2\u2029l3", "'l1 l2 l3'"),  # line and paragraph separators
            ("caf\u00e9 \u540d", "'caf\u00e9 \u540d'"),  # ordinary non-ASCII letters are kept
        ],
    )
    def test_server_name_is_cleaned(self, name, expected):
        from codemie.service.mcp.access_control import _clean_name

        assert f"'{_clean_name(name)}'" == expected

    def test_many_servers_are_summarised(self):
        servers = [_server(f"s{i}", command="npx") for i in range(8)]
        with _restricted_mode():
            with pytest.raises(ValidationException) as exc:
                MCPAccessControlService.validate_on_save(servers)
        assert "'s0', 's1', 's2', 's3', 's4' and 3 more" in str(exc.value)
        assert "s7" not in str(exc.value)

    def test_logged_server_name_cannot_forge_log_lines(self):
        servers = [_server("x\nERROR forged line", mcp_config_id="cat-1")]
        with (
            _open_mode(),
            patch(_GET_BY_IDS, return_value=[]),
            patch("codemie.service.mcp.access_control.logger") as log,
        ):
            with pytest.raises(ValidationException):
                MCPAccessControlService.validate_on_save(servers)
        logged = " ".join(str(c.args[0]) for c in log.warning.call_args_list)
        assert "\n" not in logged
        assert "\\n" in logged

    @pytest.mark.parametrize(
        ("entry", "reason"),
        [
            (None, "not found"),
            (_catalog_entry("cat-secret-id", is_active=False), "inactive"),
            (_catalog_entry("cat-secret-id", is_public=False), "not public"),
        ],
    )
    def test_unavailable_catalog_entry_message_hides_id_and_reason(self, entry, reason):
        """One identical message whatever the cause, so a user cannot probe entries; the cause goes to the log."""
        servers = [_server("s1", mcp_config_id="cat-secret-id")]
        with _open_mode(), patch(_GET_BY_IDS, return_value=[entry] if entry else []):
            with patch("codemie.service.mcp.access_control.logger") as log:
                with pytest.raises(ValidationException) as exc:
                    MCPAccessControlService.validate_on_save(servers)
        message = str(exc.value)
        assert (
            message
            == "MCP server not available in the catalogue: 's1'. Choose another MCP server or ask your administrator."
        )
        assert "cat-secret-id" not in message
        assert reason not in message
        logged = " ".join(str(c.args[0]) for c in log.warning.call_args_list)
        assert reason in logged and "cat-secret-id" in logged
