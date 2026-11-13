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

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ValidationException
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.rest_api.models.mcp_config import MCPConfig, MCPServerConfigData
from codemie.service.mcp.access_control import (
    CatalogLookupError,
    MCPAccessControlService,
    NodeAddress,
    is_enabled,
    is_link,
    unavailable_reason,
)
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

    def test_disabled_server_with_dead_catalog_id_saves(self):
        servers = [_server("s1", mcp_config_id="missing", enabled=False)]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[]):
                MCPAccessControlService.validate_on_save(servers)

    def test_disabled_and_enabled_entry_with_same_id_save(self):
        entry = _catalog_entry("cat-1")
        servers = [_server("off", mcp_config_id="cat-1", enabled=False), _server("on", mcp_config_id="cat-1")]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                MCPAccessControlService.validate_on_save(servers)

    def test_two_enabled_links_with_same_id_refused(self):
        entry = _catalog_entry("cat-1")
        servers = [_server("a", mcp_config_id="cat-1"), _server("b", mcp_config_id="cat-1")]
        with _open_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                with pytest.raises(ValidationException, match="added more than once"):
                    MCPAccessControlService.validate_on_save(servers)

    def test_re_enabled_dead_entry_refused(self):
        servers = [_server("s1", mcp_config_id="missing", enabled=True)]
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

    def test_disabled_custom_server_saves_in_restricted_mode(self):
        servers = [_server("s1", command="npx", enabled=False)]
        with _restricted_mode():
            MCPAccessControlService.validate_on_save(servers)

    def test_same_custom_server_enabled_refused_in_restricted_mode(self):
        servers = [_server("s1", command="npx", enabled=True)]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="custom server"):
                MCPAccessControlService.validate_on_save(servers)

    def test_enabled_custom_server_with_dead_id_still_refused_in_restricted_mode(self):
        servers = [_server("s1", mcp_config_id="missing", use_custom_config=True)]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[]):
                with pytest.raises(ValidationException, match="custom configuration"):
                    MCPAccessControlService.validate_on_save(servers)

    def test_disabled_link_with_dead_id_saves_in_restricted_mode(self):
        servers = [_server("s1", mcp_config_id="missing", enabled=False)]
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[]):
                MCPAccessControlService.validate_on_save(servers)

    def test_restore_ignores_disabled_custom_server_in_restricted_mode(self):
        with _restricted_mode():
            MCPAccessControlService.validate_on_restore([_server("s1", command="npx", enabled=False)])

    def test_use_custom_config_true_raises_in_restricted_mode(self):
        # AC3: declaring custom mode on a catalog-ref server is itself an override.
        servers = [_server("s1", mcp_config_id="cat-1", use_custom_config=True)]
        with _restricted_mode():
            with pytest.raises(ValidationException, match="custom configuration"):
                MCPAccessControlService.validate_on_save(servers)

    def test_catalog_ref_with_allow_user_context_override_passes_in_restricted_mode(self):
        """allow_user_context is an assistant-level override, not connection-level config —
        it must be allowed on a catalog-referenced server under restricted mode (EPMCDME-14984)."""
        entry = _catalog_entry("cat-1")
        server = MCPServerDetails(name="s1", mcp_config_id="cat-1", allow_user_context=True)
        with _restricted_mode():
            with patch(_GET_BY_IDS, return_value=[entry]):
                MCPAccessControlService.validate_on_save([server])


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

    def test_catalog_allow_user_context_true_carries_onto_runtime_config(self):
        """MCPServerConfigData.allow_user_context=True survives the model_dump()-based
        MCPServerConfig(**entry.config.model_dump()) crossover in resolve_catalog_config()."""
        catalog_config = MCPServerConfigData(command="uvx", allow_user_context=True)
        entry = _catalog_entry("cat-1", config=catalog_config)
        server = _server("s1", mcp_config_id="cat-1")
        with patch(_FIND_BY_ID, return_value=entry):
            result = MCPAccessControlService.resolve_catalog_config(server)
        assert result.config.allow_user_context is True

    def test_catalog_allow_user_context_defaults_true_on_runtime_config(self):
        catalog_config = MCPServerConfigData(command="uvx")
        entry = _catalog_entry("cat-1", config=catalog_config)
        server = _server("s1", mcp_config_id="cat-1")
        with patch(_FIND_BY_ID, return_value=entry):
            result = MCPAccessControlService.resolve_catalog_config(server)
        assert result.config.allow_user_context is True


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


class TestPredicates:
    def test_is_enabled_defaults_true_when_attribute_missing(self):
        assert is_enabled(MagicMock(spec=[])) is True

    def test_is_enabled_false_for_disabled(self):
        assert is_enabled(_server("s", enabled=False)) is False

    def test_link_is_enabled_catalog_ref_without_custom_config(self):
        assert is_link(_server("s", mcp_config_id="c")) is True

    def test_disabled_ref_is_not_a_link(self):
        assert is_link(_server("s", mcp_config_id="c", enabled=False)) is False

    def test_custom_config_ref_is_not_a_link(self):
        assert is_link(_server("s", mcp_config_id="c", use_custom_config=True)) is False

    def test_entry_without_id_is_not_a_link(self):
        assert is_link(_server("s", command="npx")) is False

    def test_link_with_missing_enabled_attribute_counts_as_enabled(self):
        srv = MagicMock(spec=["mcp_config_id", "use_custom_config"])
        srv.mcp_config_id, srv.use_custom_config = "c", False
        assert is_link(srv) is True

    @pytest.mark.parametrize(
        ("entry", "reason"),
        [
            (None, "missing"),
            (_catalog_entry(is_active=False), "inactive"),
            (_catalog_entry(is_public=False), "not_public"),
            (_catalog_entry(), None),
        ],
    )
    def test_unavailable_reason(self, entry, reason):
        assert unavailable_reason(entry) == reason


# ── Workflow node-scoped validation ──────────────────────────────────


def _workflow(assistant_servers: list[list[MCPServerDetails]], tool_servers: list[MCPServerDetails | None] = ()):
    return SimpleNamespace(
        assistants=[SimpleNamespace(mcp_servers=s) for s in assistant_servers],
        tools=[SimpleNamespace(mcp_server=s) for s in tool_servers],
    )


class TestWorkflowMcpValidation:
    def test_node_address_render(self):
        assert NodeAddress("assistant", 1, 2).render() == "assistants[1].mcp_servers[2]"
        assert NodeAddress("tool", 3).render() == "tools[3].mcp_server"

    def test_collect_nodes_addresses_and_flat_view(self):
        a, b, c = _server("a", mcp_config_id="x"), _server("b", mcp_config_id="y"), _server("c", mcp_config_id="x")
        wf = _workflow([[a], [b]], [None, c])
        nodes = MCPAccessControlService.collect_workflow_mcp_nodes(wf)
        assert [(n.render(), s) for n, s in nodes] == [
            ("assistants[0].mcp_servers[0]", a),
            ("assistants[1].mcp_servers[0]", b),
            ("tools[1].mcp_server", c),
        ]
        assert MCPAccessControlService.collect_workflow_mcp_servers(wf) == [a, b, c]

    def test_same_catalog_id_across_nodes_saves(self):
        wf = _workflow(
            [[_server("a", mcp_config_id="cat-1")], [_server("b", mcp_config_id="cat-1")]],
            [_server("t", mcp_config_id="cat-1")],
        )
        with _open_mode(), patch(_GET_BY_IDS, return_value=[_catalog_entry("cat-1")]) as get:
            MCPAccessControlService.validate_workflow_mcp(wf)
        get.assert_called_once()

    def test_same_catalog_id_twice_in_one_node_refused(self):
        wf = _workflow([[_server("a", mcp_config_id="cat-1"), _server("b", mcp_config_id="cat-1")]])
        with _open_mode(), patch(_GET_BY_IDS, return_value=[_catalog_entry("cat-1")]):
            with pytest.raises(ValidationException, match="more than once"):
                MCPAccessControlService.validate_workflow_mcp(wf)

    def test_disabled_duplicate_in_one_node_saves(self):
        wf = _workflow([[_server("a", mcp_config_id="cat-1"), _server("b", mcp_config_id="cat-1", enabled=False)]])
        with _open_mode(), patch(_GET_BY_IDS, return_value=[_catalog_entry("cat-1")]):
            MCPAccessControlService.validate_workflow_mcp(wf)

    def test_restricted_runs_over_all_nodes(self):
        wf = _workflow([[_server("a", mcp_config_id="cat-1")]], [_server("t", command="npx")])
        with _restricted_mode(), patch(_GET_BY_IDS, return_value=[_catalog_entry("cat-1")]):
            with pytest.raises(ValidationException, match="custom server"):
                MCPAccessControlService.validate_workflow_mcp(wf)

    def test_catalog_runs_over_all_nodes(self):
        wf = _workflow([[_server("a", mcp_config_id="cat-1")], [_server("b", mcp_config_id="cat-2")]])
        with _open_mode(), patch(_GET_BY_IDS, return_value=[_catalog_entry("cat-1")]):
            with pytest.raises(ValidationException, match="not available in the catalogue"):
                MCPAccessControlService.validate_workflow_mcp(wf)

    def test_empty_workflow_is_valid(self):
        MCPAccessControlService.validate_workflow_mcp(_workflow([], []))


class TestRestoreMcpServers:
    def test_dead_links_disabled_in_copy_and_reported_with_reasons(self):
        servers = [
            _server("a", mcp_config_id="gone"),
            _server("b", mcp_config_id="off"),
            _server("c", mcp_config_id="priv"),
            _server("d", mcp_config_id="ok"),
        ]
        entries = [
            _catalog_entry("off", is_active=False),
            _catalog_entry("priv", is_public=False),
            _catalog_entry("ok"),
        ]
        with patch(_GET_BY_IDS, return_value=entries) as get:
            new, report = MCPAccessControlService.restore_mcp_servers(servers, ["x0", "x1", "x2", "x3"])
        assert get.call_count == 1
        assert [s.enabled for s in new] == [False, False, False, True]
        assert [(r.name, r.mcp_config_id, r.reason, r.address) for r in report] == [
            ("a", "gone", "missing", "x0"),
            ("b", "off", "inactive", "x1"),
            ("c", "priv", "not_public", "x2"),
        ]

    def test_inputs_not_mutated(self):
        servers = [_server("a", mcp_config_id="gone")]
        with patch(_GET_BY_IDS, return_value=[]):
            new, _ = MCPAccessControlService.restore_mcp_servers(servers)
        assert servers[0].enabled is True
        assert new[0] is not servers[0]

    def test_non_links_untouched_and_address_defaults_to_none(self):
        custom = _server("c", mcp_config_id="gone", use_custom_config=True)
        off = _server("o", mcp_config_id="gone", enabled=False)
        plain = _server("p", command="uvx")
        dead = _server("d", mcp_config_id="gone")
        with patch(_GET_BY_IDS, return_value=[]):
            new, report = MCPAccessControlService.restore_mcp_servers([custom, off, plain, dead])
        assert new[0] is custom and new[1] is off and new[2] is plain
        assert [r.name for r in report] == ["d"]
        assert report[0].address is None

    def test_no_links_skips_lookup(self):
        with patch(_GET_BY_IDS) as get:
            new, report = MCPAccessControlService.restore_mcp_servers([_server("p", command="uvx")])
        get.assert_not_called()
        assert report == [] and len(new) == 1

    def test_strict_disables_missing_link_on_partial_miss(self):
        servers = [_server("a", mcp_config_id="gone"), _server("b", mcp_config_id="ok")]
        with patch(_GET_BY_IDS, return_value=[_catalog_entry("ok")]):
            new, report = MCPAccessControlService.restore_mcp_servers(servers, strict_lookup=True)
        assert [r.mcp_config_id for r in report] == ["gone"]
        assert new[0].enabled is False and new[1].enabled is not False

    def test_strict_raises_when_no_rows_for_non_empty_ids(self):
        with patch(_GET_BY_IDS, return_value=[]):
            with pytest.raises(CatalogLookupError):
                MCPAccessControlService.restore_mcp_servers([_server("a", mcp_config_id="x")], strict_lookup=True)

    def test_strict_raises_when_lookup_fails(self):
        with patch(_GET_BY_IDS, side_effect=RuntimeError("db down")):
            with pytest.raises(CatalogLookupError):
                MCPAccessControlService.restore_mcp_servers([_server("a", mcp_config_id="x")], strict_lookup=True)

    def test_strict_ok_when_all_found(self):
        with patch(_GET_BY_IDS, return_value=[_catalog_entry("ok", is_active=False)]):
            new, report = MCPAccessControlService.restore_mcp_servers(
                [_server("a", mcp_config_id="ok")], strict_lookup=True
            )
        assert report[0].reason == "inactive" and new[0].enabled is False
