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

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.base.models import CredentialTypes

from codemie.core.models import ToolConfig
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.rest_api.models.settings import CredentialValues, SettingsBase
from codemie.service.mcp.models import MCPExecutionContext, MCPMissingCredentialsError, MCPServerConfig
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


# --- MCP-Connect export switch is admin-only ------------------------------------------------


def test_tool_creds_cannot_switch_export_on():
    server_config = MCPServerConfig(command="npx", args=[], env={"BASE": "1"})
    tool_config = ToolConfig(
        name=f"{MCP_TOOL_CONFIG_PREFIX}srv", tool_creds={"CODEMIE_EXPORT_FILES": "true", "TOKEN": "t"}
    )

    MCPToolkitService._apply_tool_config_to_mcp_server(server_config, tool_config, "user", "proj")

    assert "CODEMIE_EXPORT_FILES" not in server_config.env
    assert server_config.env == {"BASE": "1", "TOKEN": "t"}


def test_integration_env_cannot_switch_export_on():
    server_config = MCPServerConfig(command="npx", args=[], env={"BASE": "1"})
    tool_config = _slot("srv", "uuid-1")

    with (
        patch.object(MCPToolkitService, "_current_user_can_use_integration", return_value=True),
        patch.object(
            MCPToolkitService,
            "_resolve_credentials_by_id",
            return_value={"CODEMIE_EXPORT_FILES": "true", "TOKEN": "t"},
        ),
    ):
        MCPToolkitService._apply_tool_config_to_mcp_server(server_config, tool_config, "user", "proj")

    assert "CODEMIE_EXPORT_FILES" not in server_config.env
    assert server_config.env == {"BASE": "1", "TOKEN": "t"}


@pytest.mark.parametrize("base_export", [None, "true"])
def test_resolved_credentials_cannot_switch_export_on(base_export):
    # Covers alias, pinned and inline resolution: they all merge through _build_mcp_server_config.
    base_env = {"BASE": "1"} | ({"CODEMIE_EXPORT_FILES": base_export} if base_export else {})
    server = MCPServerDetails(name="srv", config=MCPServerConfig(command="npx", args=[], env=base_env))

    with patch.object(
        MCPToolkitService,
        "_resolve_credentials_with_priority",
        return_value={"CODEMIE_EXPORT_FILES": "false" if base_export else "true", "TOKEN": "t"},
    ):
        config = MCPToolkitService._build_mcp_server_config(server, "user", "proj")

    # The server config's own value stands; the credential's value is ignored.
    assert config.env.get("CODEMIE_EXPORT_FILES") == base_export
    assert config.env["TOKEN"] == "t"


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


# --- inline credentials (test / tool-fetch) --------------------------------------------------

_FIND_BY_ID = "codemie.rest_api.models.mcp_config.MCPConfig.find_by_id"
_SECRET = "s3cr3t-value"


def _inline_server(values, *, settings_id=None, config_id="cfg", custom=False, alias=None) -> MCPServerDetails:
    settings = SettingsBase(
        id=settings_id,
        project_name="proj-a",
        credential_type=CredentialTypes.ENVIRONMENT_VARS,
        user_id="author",
        credential_values=[CredentialValues(key=k, value=v) for k, v in values.items()],
    )
    return MCPServerDetails(
        name="srv",
        settings=settings,
        mcp_config_id=config_id,
        use_custom_config=custom,
        integration_alias=alias,
    )


def _entry(*names: str):
    return SimpleNamespace(required_env_vars=[SimpleNamespace(name=n) for n in names])


def _resolve_inline(server, entry, **kwargs):
    with (
        patch(_FIND_BY_ID, return_value=entry),
        patch.object(MCPToolkitService, "_resolve_credentials_by_id", return_value={"BYID": "x"}) as by_id,
        patch("codemie.service.settings.settings.SettingsService.retrieve_setting") as retrieve,
    ):
        result = MCPToolkitService._resolve_credentials_with_priority(
            server, "user", "proj", allow_inline_credentials=True, **kwargs
        )
    return result, by_id, retrieve


def test_inline_returns_only_declared_names():
    server = _inline_server({"A": "a", "B": "b"})
    result, by_id, retrieve = _resolve_inline(server, _entry("A"))
    assert result == {"A": "a"}
    by_id.assert_not_called()
    retrieve.assert_not_called()


def test_inline_allowed_names_match_case_sensitively():
    server = _inline_server({"a": "lower", "A": "upper"})
    result, _, _ = _resolve_inline(server, _entry("A"))
    assert result == {"A": "upper"}


def test_inline_ignored_for_custom_config():
    result, by_id, _ = _resolve_inline(_inline_server({"A": "a"}, custom=True), _entry("A"))
    assert result == {}
    by_id.assert_not_called()


def test_inline_ignored_without_config_id():
    result, by_id, _ = _resolve_inline(_inline_server({"A": "a"}, config_id=None), _entry("A"))
    assert result == {}
    by_id.assert_not_called()


def test_inline_ignored_when_entry_has_no_required_env_vars():
    result, by_id, _ = _resolve_inline(_inline_server({"A": "a"}), _entry())
    assert result == {}
    by_id.assert_not_called()


def test_inline_ignored_when_entry_missing():
    result, by_id, _ = _resolve_inline(_inline_server({"A": "a"}), None)
    assert result == {}
    by_id.assert_not_called()


def test_inline_empty_values_return_empty_without_by_id():
    result, by_id, _ = _resolve_inline(_inline_server({}), _entry("A"))
    assert result == {}
    by_id.assert_not_called()


def test_settings_id_wins_over_inline():
    server = _inline_server({"A": "a"}, settings_id="set-1")
    result, by_id, _ = _resolve_inline(server, _entry("A"))
    by_id.assert_called_once()
    assert by_id.call_args.kwargs["integration_id"] == "set-1"
    assert result == {"BYID": "x"}


def test_flag_false_keeps_current_behaviour():
    server = _inline_server({"A": "a"})
    with (
        patch(_FIND_BY_ID, return_value=_entry("A")),
        patch.object(MCPToolkitService, "_resolve_credentials_by_id", return_value={"BYID": "x"}) as by_id,
    ):
        result = MCPToolkitService._resolve_credentials_with_priority(server, "user", "proj")
    by_id.assert_called_once()
    assert result == {"BYID": "x"}


def test_alias_wins_over_inline():
    server = _inline_server({"A": "a"}, alias="jira_alias")
    with (
        patch.object(MCPToolkitService, "_resolve_credentials_by_alias", return_value={"AL": "1"}) as by_alias,
        patch(_FIND_BY_ID, return_value=_entry("A")),
    ):
        result = MCPToolkitService._resolve_credentials_with_priority(
            server, "user", "proj", allow_inline_credentials=True
        )
    by_alias.assert_called_once()
    assert result == {"AL": "1"}


def test_inline_passes_any_value_type_untouched_and_does_not_log_or_mutate(caplog):
    caplog.set_level(logging.DEBUG)
    server = _inline_server({"A": _SECRET, "B": "other"})
    before = [(c.key, c.value) for c in server.settings.credential_values]

    result, _, _ = _resolve_inline(server, _entry("A"))

    assert result == {"A": _SECRET}
    assert _SECRET not in caplog.text
    assert [(c.key, c.value) for c in server.settings.credential_values] == before


# --- inline flag threading -------------------------------------------------------------------


def test_build_config_forwards_inline_flag():
    server = MCPServerDetails(name="srv", config=MCPServerConfig(command="npx"))
    with (
        patch(
            "codemie.service.mcp.access_control.MCPAccessControlService.resolve_catalog_config",
            return_value=server,
        ),
        patch.object(MCPToolkitService, "_resolve_credentials_with_priority", return_value={}) as resolve,
    ):
        MCPToolkitService._build_mcp_server_config(server, "user", "proj", allow_inline_credentials=True)
        MCPToolkitService._build_mcp_server_config(server, "user", "proj")

    assert resolve.call_args_list[0].kwargs["allow_inline_credentials"] is True
    assert resolve.call_args_list[1].kwargs["allow_inline_credentials"] is False


def test_prepare_server_config_forwards_inline_flag():
    server = MCPServerDetails(name="srv", config=MCPServerConfig(command="npx"))
    with patch.object(MCPToolkitService, "_build_mcp_server_config", return_value=None) as build:
        MCPToolkitService._prepare_server_config(server, "user", "proj", allow_inline_credentials=True)

    assert build.call_args.kwargs["allow_inline_credentials"] is True


def test_get_mcp_server_tools_forwards_inline_flag():
    server = MCPServerDetails(name="srv", config=MCPServerConfig(command="npx"))
    with (
        patch(
            "codemie.service.mcp.access_control.MCPAccessControlService.filter_for_runtime",
            return_value=[server],
        ),
        patch.object(MCPToolkitService, "get_instance"),
        patch.object(MCPToolkitService, "_collect_server_results", return_value=([], [], [])) as collect,
    ):
        MCPToolkitService.get_mcp_server_tools([server], user_id="user", allow_inline_credentials=True)

    assert collect.call_args.kwargs["allow_inline_credentials"] is True


def test_collect_server_results_forwards_inline_flag():
    server = MCPServerDetails(name="srv", config=MCPServerConfig(command="npx"))
    with patch.object(MCPToolkitService, "_process_single_server_for_tools", return_value=([], None, None)) as process:
        for flag in (True, False):
            MCPToolkitService._collect_server_results(
                mcp_servers=[server],
                execution_context=MCPExecutionContext(user_id="user"),
                default_toolkit_service=MagicMock(),
                user_id="user",
                project_name="proj",
                conversation_id=None,
                tools_config=None,
                mcp_server_args_preprocessor=None,
                mcp_server_single_usage=None,
                allow_inline_credentials=flag,
            )

    assert process.call_args_list[0].kwargs["allow_inline_credentials"] is True
    assert process.call_args_list[1].kwargs["allow_inline_credentials"] is False


def test_process_single_server_for_tools_forwards_inline_flag():
    server = MCPServerDetails(name="srv", config=MCPServerConfig(command="npx"))
    with patch.object(MCPToolkitService, "_process_single_mcp_server", return_value=[]) as process:
        for flag in (True, False):
            MCPToolkitService._process_single_server_for_tools(
                mcp_server=server,
                per_server_context=MCPExecutionContext(user_id="user"),
                default_toolkit_service=MagicMock(),
                user_id="user",
                project_name="proj",
                conversation_id=None,
                tools_config=None,
                mcp_server_args_preprocessor=None,
                mcp_server_single_usage=None,
                allow_inline_credentials=flag,
            )

    assert process.call_args_list[0].kwargs["allow_inline_credentials"] is True
    assert process.call_args_list[1].kwargs["allow_inline_credentials"] is False


def test_process_single_mcp_server_forwards_inline_flag():
    server = MCPServerDetails(name="srv", config=MCPServerConfig(command="npx"))
    with (
        patch.object(MCPToolkitService, "_get_toolkit_service_for_server", return_value=MagicMock()),
        patch.object(MCPToolkitService, "_prepare_server_config", return_value=None) as prepare,
    ):
        for flag in (True, False):
            MCPToolkitService._process_single_mcp_server(
                mcp_server=server,
                default_toolkit_service=MagicMock(),
                user_id="user",
                allow_inline_credentials=flag,
            )

    assert prepare.call_args_list[0].kwargs["allow_inline_credentials"] is True
    assert prepare.call_args_list[1].kwargs["allow_inline_credentials"] is False


# --- required env var check (test and tool-fetch paths) --------------------------------------


def _required_entry(*names: str, optional: tuple[str, ...] = ()):
    return SimpleNamespace(
        required_env_vars=[SimpleNamespace(name=n, required=True) for n in names]
        + [SimpleNamespace(name=n, required=False) for n in optional]
    )


def _build_with_entry(server, entry, *, catalog_env=None, by_id=None, **kwargs):
    resolved = server.model_copy(update={"config": MCPServerConfig(command="npx", env=dict(catalog_env or {}))})
    if server.use_custom_config:
        resolved = server
    with (
        patch(
            "codemie.service.mcp.access_control.MCPAccessControlService.resolve_catalog_config",
            return_value=resolved,
        ),
        patch(_FIND_BY_ID, return_value=entry),
        patch.object(MCPToolkitService, "_resolve_credentials_by_id", return_value=by_id or {}),
    ):
        return MCPToolkitService._build_mcp_server_config(server, "user", "proj", **kwargs)


def test_required_check_names_blank_inline_value_without_leaking_values():
    server = _inline_server({"A": "   ", "B": _SECRET})

    with pytest.raises(MCPMissingCredentialsError) as exc_info:
        _build_with_entry(server, _required_entry("A", "B"), allow_inline_credentials=True)

    assert exc_info.value.missing == ["A"]
    assert str(exc_info.value) == "Missing required credentials: A"
    assert _SECRET not in str(exc_info.value)


def test_required_check_names_var_when_no_settings_sent():
    server = MCPServerDetails(name="srv", mcp_config_id="cfg")

    with pytest.raises(MCPMissingCredentialsError) as exc_info:
        _build_with_entry(server, _required_entry("A"), allow_inline_credentials=True)

    assert exc_info.value.missing == ["A"]


def test_required_check_passes_when_inline_value_provided():
    config = _build_with_entry(_inline_server({"A": "a"}), _required_entry("A"), allow_inline_credentials=True)

    assert config.env == {"A": "a"}


def test_required_check_accepts_catalog_env_literal():
    server = MCPServerDetails(name="srv", mcp_config_id="cfg")

    config = _build_with_entry(server, _required_entry("A"), catalog_env={"A": "lit"}, allow_inline_credentials=True)

    assert config.env == {"A": "lit"}


def test_required_check_ignores_optional_vars():
    server = _inline_server({"A": "a"})

    config = _build_with_entry(server, _required_entry("A", optional=("B",)), allow_inline_credentials=True)

    assert config.env == {"A": "a"}


def test_required_check_names_var_missing_from_existing_setting():
    server = _inline_server({}, settings_id="set-1")

    with pytest.raises(MCPMissingCredentialsError) as exc_info:
        _build_with_entry(server, _required_entry("A", "B"), by_id={"A": "a"}, allow_inline_credentials=True)

    assert exc_info.value.missing == ["B"]


def test_required_check_skipped_for_custom_config():
    server = MCPServerDetails(
        name="srv", mcp_config_id="cfg", use_custom_config=True, config=MCPServerConfig(command="npx", env={})
    )

    with patch(_FIND_BY_ID) as find:
        config = _build_with_entry(server, _required_entry("A"), allow_inline_credentials=True)

    assert config.env == {}
    find.assert_not_called()


def test_required_check_off_without_inline_flag():
    server = MCPServerDetails(name="srv", mcp_config_id="cfg")

    config = _build_with_entry(server, _required_entry("A"))

    assert config.env == {}


def test_missing_credentials_error_is_not_wrapped_as_tool_load_failure():
    server = MCPServerDetails(name="srv", mcp_config_id="cfg")
    error = MCPMissingCredentialsError("srv", ["A"])
    with (
        patch.object(MCPToolkitService, "_get_toolkit_service_for_server", return_value=MagicMock()),
        patch.object(MCPToolkitService, "_prepare_server_config", side_effect=error),
    ):
        with pytest.raises(MCPMissingCredentialsError) as exc_info:
            MCPToolkitService._process_single_mcp_server(
                mcp_server=server,
                default_toolkit_service=MagicMock(),
                user_id="user",
                allow_inline_credentials=True,
            )

    assert exc_info.value is error
