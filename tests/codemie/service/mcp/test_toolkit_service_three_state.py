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

"""
Per-user selection for a non-pinned MCP server (chat):

- Default (no slot entry / empty id): integration_alias resolves as before; without an alias the
  server runs on its clean inline base config.
- Explicit integration (real UUID): alias skipped, the chosen integration is applied by id.
"""

from unittest.mock import patch

from codemie_tools.base.models import CredentialTypes

from codemie.core.models import ToolConfig
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.rest_api.models.settings import SettingsBase
from codemie.service.mcp.models import MCPServerConfig
from codemie.service.mcp.toolkit_service import (
    MCP_TOOL_CONFIG_PREFIX,
    MCPToolkitService,
)


def _slot(server_name: str, integration_id: str) -> ToolConfig:
    return ToolConfig(name=f"{MCP_TOOL_CONFIG_PREFIX}{server_name}", integration_id=integration_id)


# --- slot-state resolution ------------------------------------------------------------------


def test_slot_default_when_no_entry():
    # A default slot is represented by the ABSENCE of a mapping entry (an empty id is never stored —
    # ToolConfig requires a real integration_id and the mapping service deletes emptied slots).
    server = MCPServerDetails(name="srv")
    assert MCPToolkitService._is_explicit_integration_slot(server, None) is False
    assert MCPToolkitService._is_explicit_integration_slot(server, []) is False
    assert MCPToolkitService._is_explicit_integration_slot(server, [_slot("other", "uuid-1")]) is False


def test_slot_explicit_integration():
    server = MCPServerDetails(name="srv")
    assert MCPToolkitService._is_explicit_integration_slot(server, [_slot("srv", "uuid-1")]) is True


def test_slot_pinned_server_is_never_explicit():
    """A pinned server never enters user-selection; a stray slot entry does not change its state."""
    pinned = SettingsBase(project_name="proj-a", credential_type=CredentialTypes.ENVIRONMENT_VARS, user_id="author")
    server = MCPServerDetails(name="srv", settings=pinned)
    assert MCPToolkitService._is_explicit_integration_slot(server, [_slot("srv", "uuid-1")]) is False


# --- alias skip in credential resolution ----------------------------------------------------


def test_default_resolves_integration_alias():
    server = MCPServerDetails(name="srv", integration_alias="jira_alias")
    with patch.object(MCPToolkitService, "_resolve_credentials_by_alias", return_value={"A": "1"}) as by_alias:
        result = MCPToolkitService._resolve_credentials_with_priority(server, "user", "proj")
    by_alias.assert_called_once()
    assert result == {"A": "1"}


def test_default_without_alias_yields_base_config():
    """A non-pinned DEFAULT server with no alias resolves to base config (empty env override)."""
    server = MCPServerDetails(name="srv")
    result = MCPToolkitService._resolve_credentials_with_priority(server, "user", "proj")
    assert result == {}


def test_explicit_choice_skips_integration_alias():
    """ignore_integration_alias=True (explicit integration) -> clean inline base before by-id apply."""
    server = MCPServerDetails(name="srv", integration_alias="jira_alias")
    with patch.object(MCPToolkitService, "_resolve_credentials_by_alias", return_value={"A": "1"}) as by_alias:
        result = MCPToolkitService._resolve_credentials_with_priority(
            server, "user", "proj", ignore_integration_alias=True
        )
    by_alias.assert_not_called()
    assert result == {}


def test_build_config_forwards_ignore_alias_flag():
    server = MCPServerDetails(name="srv", integration_alias="jira_alias", config=MCPServerConfig(command="npx"))
    with (
        patch(
            "codemie.service.mcp.access_control.MCPAccessControlService.resolve_catalog_config",
            return_value=server,
        ),
        patch.object(MCPToolkitService, "_resolve_credentials_with_priority", return_value={}) as resolve,
    ):
        MCPToolkitService._build_mcp_server_config(server, "user", "proj", ignore_integration_alias=True)

    _, kwargs = resolve.call_args
    assert kwargs.get("ignore_integration_alias") is True


# --- override path --------------------------------------------------------------------------


def test_explicit_integration_override_resolves_by_id():
    server_config = MCPServerConfig(command="npx", args=[], env={"BASE": "1"})
    tool_config = _slot("srv", "uuid-1")

    with (
        patch.object(MCPToolkitService, "_current_user_can_use_integration", return_value=True),
        patch.object(MCPToolkitService, "_resolve_credentials_by_id", return_value={"OVERRIDE": "val"}),
    ):
        MCPToolkitService._apply_tool_config_to_mcp_server(server_config, tool_config, "user", "proj")

    assert server_config.env["OVERRIDE"] == "val"
    assert server_config.env["BASE"] == "1"


# --- allow_user_context precedence -----------------------------------------------------------


def test_server_config_sends_user_context_by_default():
    """Both the runtime config and the catalog data model start opted in; opting out is explicit."""
    from codemie.rest_api.models.mcp_config import MCPServerConfigData

    assert MCPServerConfig(command="npx").allow_user_context is True
    assert MCPServerConfigData(command="npx").allow_user_context is True


def test_assistant_override_true_flips_server_default_false():
    server_config = MCPServerConfig(command="npx", allow_user_context=False)
    server = MCPServerDetails(name="srv", config=server_config, use_custom_config=True, allow_user_context=True)

    result = MCPToolkitService._build_mcp_server_config(server, "user", "proj")

    assert result.allow_user_context is True


def test_assistant_override_false_flips_server_default_true():
    server_config = MCPServerConfig(command="npx", allow_user_context=True)
    server = MCPServerDetails(name="srv", config=server_config, use_custom_config=True, allow_user_context=False)

    result = MCPToolkitService._build_mcp_server_config(server, "user", "proj")

    assert result.allow_user_context is False


def test_assistant_override_none_leaves_server_default_true_standing():
    server_config = MCPServerConfig(command="npx", allow_user_context=True)
    server = MCPServerDetails(name="srv", config=server_config, use_custom_config=True, allow_user_context=None)

    result = MCPToolkitService._build_mcp_server_config(server, "user", "proj")

    assert result.allow_user_context is True


def test_assistant_override_none_leaves_server_default_false_standing():
    server_config = MCPServerConfig(command="npx", allow_user_context=False)
    server = MCPServerDetails(name="srv", config=server_config, use_custom_config=True, allow_user_context=None)

    result = MCPToolkitService._build_mcp_server_config(server, "user", "proj")

    assert result.allow_user_context is False


# --- allow_user_context: the catalog value is a ceiling for catalog-referenced servers -------------


def _catalog_ref_server(catalog_value: bool, override: bool | None, use_custom_config: bool = False):
    """A server that references an admin-owned catalog entry whose config carries `catalog_value`."""
    return MCPServerDetails(
        name="srv",
        mcp_config_id="catalog-1",
        use_custom_config=use_custom_config,
        config=MCPServerConfig(command="npx", allow_user_context=catalog_value),
        allow_user_context=override,
    )


def _build_with_resolved(server: MCPServerDetails):
    # resolve_catalog_config would load the catalog entry from the DB; the server above already
    # carries the config it would have resolved.
    with patch(
        "codemie.service.mcp.access_control.MCPAccessControlService.resolve_catalog_config",
        return_value=server,
    ):
        return MCPToolkitService._build_mcp_server_config(server, "user", "proj")


def test_catalog_ref_assistant_cannot_turn_on_what_admin_left_off():
    result = _build_with_resolved(_catalog_ref_server(catalog_value=False, override=True))

    assert result.allow_user_context is False


def test_catalog_ref_assistant_can_turn_off_what_admin_enabled():
    result = _build_with_resolved(_catalog_ref_server(catalog_value=True, override=False))

    assert result.allow_user_context is False


def test_catalog_ref_override_true_keeps_admin_enabled_value():
    result = _build_with_resolved(_catalog_ref_server(catalog_value=True, override=True))

    assert result.allow_user_context is True


def test_catalog_ref_override_none_inherits_catalog_value():
    assert _build_with_resolved(_catalog_ref_server(catalog_value=True, override=None)).allow_user_context is True
    assert _build_with_resolved(_catalog_ref_server(catalog_value=False, override=None)).allow_user_context is False


def test_custom_config_with_catalog_id_is_not_capped_by_catalog():
    """use_custom_config=True means the assistant owner supplies the whole config: no ceiling applies."""
    result = _build_with_resolved(_catalog_ref_server(catalog_value=False, override=True, use_custom_config=True))

    assert result.allow_user_context is True


def test_default_absent_slot_applies_no_override():
    """DEFAULT is an absent slot: _apply_server_tools_config finds no entry and keeps the base env."""
    server = MCPServerDetails(name="srv")
    server_config = MCPServerConfig(command="npx", args=[], env={"BASE": "1"})

    with (
        patch.object(MCPToolkitService, "_current_user_can_use_integration") as access,
        patch.object(MCPToolkitService, "_resolve_credentials_by_id") as resolve,
    ):
        # Mapping carries only an unrelated server's slot, so there is no MCP:srv entry to apply.
        MCPToolkitService._apply_server_tools_config(server_config, server, [_slot("other", "uuid-1")], "user", "proj")

    access.assert_not_called()
    resolve.assert_not_called()
    assert server_config.env == {"BASE": "1"}
