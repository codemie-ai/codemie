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

import pytest
import yaml
from unittest.mock import patch, MagicMock

from codemie.core.workflow_models import WorkflowTool
from codemie.rest_api.models.assistant import Assistant
from codemie.service.tools.tool_service import ToolsService
from codemie.workflows.validation.resources import (
    _extract_datasources,
    _is_assistant_available,
    _is_tool_available,
    _find_states_referencing_assistant,
    _find_states_referencing_tool,
    _validate_assistants_availability,
    _validate_tools_from_assistants_availability,
    _validate_tools_avaiability,
    _validate_datasources_availability,
    _validate_sub_workflow_availability,
    validate_workflow_config_resources_availability,
    WorkflowConfigResourcesValidationError,
)


@pytest.fixture
def mock_user():
    return MagicMock()


def test_find_states_referencing_assistant(mock_workflow_config):
    mock_workflow_config.states = [
        MagicMock(id="state_1", assistant_id="asst_ref_1"),
        MagicMock(id="state_2", assistant_id="asst_ref_2"),
        MagicMock(id="state_3", assistant_id="asst_ref_1"),
    ]
    states = _find_states_referencing_assistant(mock_workflow_config, "asst_ref_1")
    assert states == ["state_1", "state_3"]


def test_find_states_referencing_assistant_no_match(mock_workflow_config):
    mock_workflow_config.states = [MagicMock(id="state_1", assistant_id="asst_ref_1")]
    states = _find_states_referencing_assistant(mock_workflow_config, "nonexistent")
    assert states == []


def test_find_states_referencing_tool(mock_workflow_config):
    mock_workflow_config.states = [
        MagicMock(id="state_1", tool_id="tool_1"),
        MagicMock(id="state_2", tool_id="tool_2"),
        MagicMock(id="state_3", tool_id="tool_1"),
    ]
    states = _find_states_referencing_tool(mock_workflow_config, "tool_1")
    assert states == ["state_1", "state_3"]


def test_find_states_referencing_tool_no_match(mock_workflow_config):
    mock_workflow_config.states = [MagicMock(id="state_1", tool_id="tool_1")]
    states = _find_states_referencing_tool(mock_workflow_config, "nonexistent")
    assert states == []


def test_extract_datasources_with_ids(mock_workflow_config):
    mock_workflow_config.assistants = [
        MagicMock(datasource_ids=["ds_1", "ds_2"]),
        MagicMock(datasource_ids=["ds_2", "ds_3"]),
    ]

    datasources = _extract_datasources(mock_workflow_config)
    # Returns dict mapping datasource_id -> list of assistant refs
    assert "ds_1" in datasources
    assert "ds_2" in datasources
    assert "ds_3" in datasources


def test_extract_datasources_no_datasource_ids(mock_workflow_config):
    mock_workflow_config.assistants = [MagicMock(datasource_ids=None)]
    datasources = _extract_datasources(mock_workflow_config)
    assert datasources == {}


def test_extract_datasources_no_assistants(mock_workflow_config):
    mock_workflow_config.assistants = None
    datasources = _extract_datasources(mock_workflow_config)
    assert datasources == {}


@patch.object(Assistant, "get_by_ids")
def test_is_assistant_available_available(mock_get_by_ids, mock_user):
    mock_get_by_ids.return_value = ["assistant_1"]
    assert _is_assistant_available("assistant_1", mock_user) is True


@patch.object(Assistant, "get_by_ids")
def test_is_assistant_available_not_available(mock_get_by_ids, mock_user):
    mock_get_by_ids.return_value = []
    assert _is_assistant_available("assistant_1", mock_user) is False


@patch.object(ToolsService, "find_toolkit_for_tool")
def test_is_tool_available_available(find_toolkit_for_tool, mock_user, mock_workflow_config):
    find_toolkit_for_tool.return_value = MagicMock()  # Return a toolkit object (non-None)
    mock_tool = MagicMock()
    mock_tool.mcp_server = None  # Regular tool, not MCP server
    mock_tool.tool = "tool_1"
    assert _is_tool_available(mock_workflow_config, mock_tool, mock_user) is True


@patch.object(ToolsService, "find_toolkit_for_tool")
def test_is_tool_available_not_available(find_toolkit_for_tool, mock_user, mock_workflow_config):
    find_toolkit_for_tool.return_value = None
    mock_tool = MagicMock()
    mock_tool.mcp_server = None  # Regular tool, not MCP server
    mock_tool.tool = "tool_1"
    assert _is_tool_available(mock_workflow_config, mock_tool, mock_user) is False


@patch("codemie.workflows.validation.resources.MCPToolkitService.get_mcp_server_tools")
def test_is_tool_available_mcp_server_available(mock_get_mcp_server_tools, mock_user, mock_workflow_config):
    mock_mcp_server = MagicMock()
    mock_mcp_server.resolve_dynamic_values_in_arguments = False  # Ensure static validation runs
    mock_tool = MagicMock(spec=WorkflowTool)
    mock_tool.mcp_server = mock_mcp_server
    mock_tool.tool = "mcp_tool_1"

    # Mock the MCP service to return a tool
    mock_mcp_tool = MagicMock()
    mock_mcp_tool.name = "mcp_tool_1"
    mock_get_mcp_server_tools.return_value = [mock_mcp_tool]

    assert _is_tool_available(mock_workflow_config, mock_tool, mock_user) is True


@patch("codemie.workflows.validation.resources.MCPToolkitService.get_mcp_server_tools")
def test_is_tool_available_mcp_server_not_available(mock_get_mcp_server_tools, mock_user, mock_workflow_config):
    mock_mcp_server = MagicMock()
    mock_mcp_server.resolve_dynamic_values_in_arguments = False  # Ensure static validation runs
    mock_tool = MagicMock(spec=WorkflowTool)
    mock_tool.mcp_server = mock_mcp_server
    mock_tool.tool = "mcp_tool_1"

    # Mock the MCP service to return empty list (no tools found)
    mock_get_mcp_server_tools.return_value = []

    assert _is_tool_available(mock_workflow_config, mock_tool, mock_user) is False


def test_is_tool_available_mcp_server_dynamic_validation_skip(mock_user, mock_workflow_config):
    """Test that dynamic MCP servers skip validation."""
    mock_mcp_server = MagicMock()
    mock_mcp_server.resolve_dynamic_values_in_arguments = True
    mock_tool = MagicMock(spec=WorkflowTool)
    mock_tool.mcp_server = mock_mcp_server
    mock_tool.tool = "dynamic_mcp_tool"

    # Should return True without calling MCP service
    assert _is_tool_available(mock_workflow_config, mock_tool, mock_user) is True


@patch("codemie.workflows.validation.resources.MCPToolkitService.get_mcp_server_tools")
def test_is_tool_available_mcp_server_static_validation_runs(
    mock_get_mcp_server_tools, mock_user, mock_workflow_config
):
    """Test that static MCP servers still run validation."""
    mock_mcp_server = MagicMock()
    mock_mcp_server.resolve_dynamic_values_in_arguments = False
    mock_tool = MagicMock(spec=WorkflowTool)
    mock_tool.mcp_server = mock_mcp_server
    mock_tool.tool = "static_mcp_tool"

    # Mock the MCP service to return a tool
    mock_mcp_tool = MagicMock()
    mock_mcp_tool.name = "static_mcp_tool"
    mock_get_mcp_server_tools.return_value = [mock_mcp_tool]

    # Should call MCP service for validation
    assert _is_tool_available(mock_workflow_config, mock_tool, mock_user) is True
    mock_get_mcp_server_tools.assert_called_once()


@patch("codemie.workflows.validation.resources._is_assistant_available", return_value=False)
def test_validate_assistants_availability_some_unavailable(
    mock_is_assistant_available, mock_workflow_config, mock_user
):
    mock_workflow_config.assistants = [
        MagicMock(id="id_1", assistant_id="assistant_1"),
        MagicMock(id="id_2", assistant_id="assistant_2"),
    ]
    mock_workflow_config.states = [
        MagicMock(id="state_1", assistant_id="id_1"),
        MagicMock(id="state_2", assistant_id="id_2"),
    ]
    unavailable = _validate_assistants_availability(mock_workflow_config, mock_user)
    assert unavailable == [("id_1", "assistant_1", "state_1"), ("id_2", "assistant_2", "state_2")]


@patch("codemie.workflows.validation.resources._is_assistant_available", return_value=True)
def test_validate_assistants_availability_all_available(mock_is_assistant_available, mock_workflow_config, mock_user):
    mock_workflow_config.assistants = [MagicMock(id="id_1", assistant_id="assistant_1")]
    unavailable = _validate_assistants_availability(mock_workflow_config, mock_user)
    assert unavailable == []


@patch("codemie.workflows.validation.resources._is_tool_available", return_value=True)
@patch("codemie.workflows.validation.resources._tool_requires_missing_integration", return_value=(False, None))
def test_validate_tools_from_assistants_availability_all_available(
    mock_requires_missing, mock_is_tool_available, mock_workflow_config, mock_user
):
    mock_workflow_config.assistants = [MagicMock(tools=[MagicMock(name="Tool A", integration_alias=None)])]
    unavailable, invalid_integration, missing_integration = _validate_tools_from_assistants_availability(
        mock_workflow_config, mock_user
    )
    assert unavailable == []
    assert invalid_integration == []
    assert missing_integration == []


@patch("codemie.workflows.validation.resources._is_tool_available", return_value=False)
def test_validate_tools_availability_some_unavailable(mock_is_tool_available, mock_workflow_config, mock_user):
    mock_workflow_config.tools = [MagicMock(id="tool_1", tool="Tool A"), MagicMock(id="tool_2", tool="Tool B")]
    mock_workflow_config.states = [
        MagicMock(id="state_1", tool_id="tool_1"),
        MagicMock(id="state_2", tool_id="tool_2"),
    ]
    unavailable = _validate_tools_avaiability(mock_workflow_config, mock_user)
    assert unavailable == [("tool_1", "Tool A", "state_1"), ("tool_2", "Tool B", "state_2")]


@patch("codemie.workflows.validation.resources._is_tool_available", return_value=True)
def test_validate_tools_availability_all_available(mock_is_tool_available, mock_workflow_config, mock_user):
    mock_workflow_config.tools = [MagicMock(id="tool_1", tool="Tool A")]
    unavailable = _validate_tools_avaiability(mock_workflow_config, mock_user)
    assert unavailable == []


@patch(
    "codemie.workflows.validation.resources._is_datasource_available",
    side_effect=lambda ds: ds if ds != "unavailable_ds" else None,
)
def test_validate_datasources_availability_some_unavailable(mock_is_datasource_available, mock_workflow_config):
    mock_assistant = MagicMock(id="asst_ref_1", datasource_ids=["ds_1", "unavailable_ds", "ds_2"])
    mock_workflow_config.assistants = [mock_assistant]
    mock_workflow_config.states = [MagicMock(id="state_1", assistant_id="asst_ref_1")]
    unavailable = _validate_datasources_availability(mock_workflow_config)
    # Now returns list of tuples (datasource_id, assistant_ref, state_id)
    assert len(unavailable) == 1
    assert unavailable[0] == ("unavailable_ds", "asst_ref_1", "state_1")


@patch("codemie.workflows.validation.resources._is_datasource_available", return_value=True)
def test_validate_datasources_availability_all_available(mock_is_datasource_available, mock_workflow_config):
    mock_workflow_config.assistants = [MagicMock(datasource_ids=["ds_1", "ds_2"])]
    unavailable = _validate_datasources_availability(mock_workflow_config)
    assert unavailable == []


@pytest.fixture
def mock_workflow_config():
    config = MagicMock()
    config.assistants = [MagicMock(id="asst_ref_1", assistant_id="assistant_1", datasource_ids=["ds_1"])]
    config.tools = [MagicMock(id="tool_1", tool="Tool A")]
    config.states = [MagicMock(id="state_1", assistant_id="asst_ref_1", tool_id="tool_1")]
    config.yaml_config = None  # Prevent yaml.compose() from receiving MagicMock
    return config


@patch("codemie.workflows.validation.resources._is_assistant_available", return_value=True)
@patch("codemie.workflows.validation.resources._is_tool_available", return_value=True)
@patch("codemie.workflows.validation.resources._is_datasource_available", return_value=True)
@patch("codemie.workflows.validation.resources._validate_referenced_assistants_integrations", return_value=[])
@patch("codemie.workflows.validation.resources._validate_sub_workflow_availability", return_value=[])
def test_validate_workflow_config_resources_availability_all_available(
    mock_sub_wf_availability,
    mock_ref_integrations,
    mock_is_datasource_available,
    mock_tool_available,
    mock_assistant_available,
    mock_workflow_config,
    mock_user,
):
    validate_workflow_config_resources_availability(mock_workflow_config, mock_user)


@patch("codemie.workflows.validation.resources._is_assistant_available", return_value=False)
@patch("codemie.workflows.validation.resources._is_tool_available", return_value=False)
@patch("codemie.workflows.validation.resources._is_datasource_available", return_value=None)
@patch("codemie.workflows.validation.resources._validate_referenced_assistants_integrations", return_value=[])
@patch("codemie.workflows.validation.resources._validate_sub_workflow_availability", return_value=[])
def test_validate_workflow_config_resources_availability_multiple_unavailable_resources(
    mock_sub_wf_availability,
    mock_ref_integrations,
    mock_is_datasource_available,
    mock_tool_available,
    mock_assistant_available,
    mock_workflow_config,
    mock_user,
):
    with pytest.raises(WorkflowConfigResourcesValidationError) as exc_info:
        validate_workflow_config_resources_availability(mock_workflow_config, mock_user)
    error_message = str(exc_info.value)
    assert "Assistants do not exist" in error_message
    assert "Tools do not exist" in error_message
    assert "Data sources (referenced in assistant definitions) do not exist" in error_message


def test_workflow_config_resources_validation_error_to_dict():
    unavailable_assistants = [("ref_1", "asst_123", "state_1"), ("ref_2", "asst_456", "state_2")]
    unavailable_tools = [("tool_ref_1", "tool_123", "state_3")]
    unavailable_tools_from_asst_integrations = [
        ("tool_from_asst_1", "asst_ref_1", "state_4"),
        ("tool_from_asst_2", "asst_ref_2", "state_5"),
    ]
    unavailable_datasources = [("datasource_1", "asst_ref_1", "state_6"), ("datasource_2", "asst_ref_2", "state_7")]

    exception = WorkflowConfigResourcesValidationError(
        unavailable_assistants,
        unavailable_tools,
        unavailable_tools_from_asst_integrations,
        unavailable_datasources,
    )

    error_dict = exception.to_dict()

    assert error_dict["error_type"] == "resource_validation"
    assert "errors" in error_dict
    assert len(error_dict["errors"]) == 7

    # Check assistant errors
    assistant_errors = [e for e in error_dict["errors"] if e["path"] == "assistant_id"]
    assert len(assistant_errors) == 2
    assert assistant_errors[0]["details"] == "Assistant 'asst_123' does not exist"
    assert assistant_errors[0]["state_id"] == "state_1"

    # Check tool errors
    tool_errors = [e for e in error_dict["errors"] if e["path"] == "tool_id"]
    assert len(tool_errors) == 1
    assert tool_errors[0]["details"] == "Tool 'tool_123' does not exist"
    assert tool_errors[0]["state_id"] == "state_3"

    # Check tool from assistant errors (no state_id - these are assistant-level errors)
    tool_from_asst_errors = [e for e in error_dict["errors"] if e["path"] == "tools"]
    assert len(tool_from_asst_errors) == 2
    assert "tool_from_asst_1" in tool_from_asst_errors[0]["details"]
    assert "asst_ref_1" in tool_from_asst_errors[0]["details"]
    assert "state_id" not in tool_from_asst_errors[0]

    # Check datasource errors
    datasource_errors = [e for e in error_dict["errors"] if e["path"] == "datasource_ids"]
    assert len(datasource_errors) == 2
    assert (
        datasource_errors[0]["details"] == "Datasource 'datasource_1' (used by assistant 'asst_ref_1') does not exist"
    )
    assert datasource_errors[0]["state_id"] == "state_6"
    assert (
        datasource_errors[1]["details"] == "Datasource 'datasource_2' (used by assistant 'asst_ref_2') does not exist"
    )
    assert datasource_errors[1]["state_id"] == "state_7"


def test_workflow_config_resources_validation_error_to_dict_empty_tool_field():
    """When a tool definition has an empty 'tool' field the error must point to the
    tool definition itself (path='tool'), including the state_id of the referencing state."""
    unavailable_tools = [("tool_ref_1", "", "state_1")]

    exception = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[],
        unavailable_tools=unavailable_tools,
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
    )

    error_dict = exception.to_dict()

    assert error_dict["error_type"] == "resource_validation"
    errors = error_dict["errors"]
    assert len(errors) == 1

    error = errors[0]
    assert error["path"] == "tool"
    assert error["message"] == "Tool is required"
    assert error["details"] == "Tool '' does not exist"
    assert error["state_id"] == "state_1"


@patch.object(ToolsService, "find_toolkit_for_tool")
def test_is_tool_available_raises_value_error(find_toolkit_for_tool, mock_user, mock_workflow_config):
    """ValueError from ToolsService is treated as tool unavailable."""
    find_toolkit_for_tool.side_effect = ValueError("unexpected error")
    mock_tool = MagicMock()
    mock_tool.mcp_server = None
    mock_tool.tool = "tool_1"
    assert _is_tool_available(mock_workflow_config, mock_tool, mock_user) is False


@patch("codemie.workflows.validation.resources.ToolsService.find_setting_for_tool")
def test_is_integration_alias_valid_valid(mock_find_setting, mock_user):
    """Valid integration alias (no exception) returns True."""
    from codemie.workflows.validation.resources import _is_integration_alias_valid

    mock_find_setting.return_value = MagicMock()
    assert _is_integration_alias_valid(mock_user, "project", "alias") is True


@patch("codemie.workflows.validation.resources.ToolsService.find_setting_for_tool")
def test_is_integration_alias_valid_invalid(mock_find_setting, mock_user):
    """ValueError from find_setting_for_tool means alias is invalid, returns False."""
    from codemie.workflows.validation.resources import _is_integration_alias_valid

    mock_find_setting.side_effect = ValueError("not found")
    assert _is_integration_alias_valid(mock_user, "project", "alias") is False


@patch("codemie.workflows.validation.resources._is_assistant_available")
def test_validate_assistants_availability_skips_none_assistant_id(
    mock_is_assistant_available, mock_workflow_config, mock_user
):
    """Assistants with None assistant_id are skipped without availability check."""
    mock_workflow_config.assistants = [MagicMock(id="asst_ref_1", assistant_id=None)]
    unavailable = _validate_assistants_availability(mock_workflow_config, mock_user)
    assert unavailable == []
    mock_is_assistant_available.assert_not_called()


@patch("codemie.workflows.validation.resources._is_tool_available", return_value=False)
def test_validate_tools_from_assistants_availability_some_unavailable(
    mock_is_tool_available, mock_workflow_config, mock_user
):
    """Tools that don't exist appear in the unavailable list."""
    tool_mock = MagicMock()
    tool_mock.name = "tool_a"
    tool_mock.integration_alias = None
    mock_workflow_config.assistants = [MagicMock(id="asst_ref_1", tools=[tool_mock])]
    mock_workflow_config.states = [MagicMock(id="state_1", assistant_id="asst_ref_1")]
    unavailable, invalid_integration, missing_integration = _validate_tools_from_assistants_availability(
        mock_workflow_config, mock_user
    )
    assert ("tool_a", "asst_ref_1", "state_1") in unavailable
    assert invalid_integration == []
    assert missing_integration == []


@patch("codemie.workflows.validation.resources._is_tool_available", return_value=True)
@patch("codemie.workflows.validation.resources._is_integration_alias_valid", return_value=False)
def test_validate_tools_from_assistants_availability_invalid_integration(
    mock_is_integration_alias, mock_is_tool_available, mock_workflow_config, mock_user
):
    """Tools with invalid integration_alias appear in the invalid_integration list."""
    tool_mock = MagicMock()
    tool_mock.name = "tool_a"
    tool_mock.integration_alias = "my_alias"
    mock_workflow_config.assistants = [MagicMock(id="asst_ref_1", tools=[tool_mock])]
    mock_workflow_config.states = [MagicMock(id="state_1", assistant_id="asst_ref_1")]
    unavailable, invalid_integration, missing_integration = _validate_tools_from_assistants_availability(
        mock_workflow_config, mock_user
    )
    assert unavailable == []
    assert ("tool_a", "asst_ref_1", "state_1") in invalid_integration
    assert missing_integration == []


@patch("codemie.workflows.validation.resources._is_tool_available", return_value=True)
@patch(
    "codemie.workflows.validation.resources._tool_requires_missing_integration",
    return_value=(True, "Jira"),
)
def test_validate_tools_from_assistants_availability_missing_integration(
    mock_requires_missing, mock_is_tool_available, mock_workflow_config, mock_user
):
    """Tools requiring an integration that is not configured appear in missing_integration list."""
    tool_mock = MagicMock()
    tool_mock.name = "tool_a"
    tool_mock.integration_alias = None
    # Inline assistant (no assistant_id) — missing integration check applies.
    mock_workflow_config.assistants = [MagicMock(id="asst_ref_1", assistant_id=None, tools=[tool_mock])]
    mock_workflow_config.states = [MagicMock(id="state_1", assistant_id="asst_ref_1")]
    unavailable, invalid_integration, missing_integration = _validate_tools_from_assistants_availability(
        mock_workflow_config, mock_user
    )
    assert unavailable == []
    assert invalid_integration == []
    assert ("tool_a", "asst_ref_1", "state_1", "Jira") in missing_integration


@patch("codemie.workflows.validation.resources._is_tool_available", return_value=True)
@patch(
    "codemie.workflows.validation.resources._tool_requires_missing_integration",
    return_value=(True, "Jira"),
)
def test_validate_tools_from_assistants_skips_missing_check_for_referenced_assistants(
    mock_requires_missing, mock_is_tool_available, mock_workflow_config, mock_user
):
    """Referenced assistants (with assistant_id) are not double-reported through inline tools path.

    When a referenced assistant carries inline tools (the model does not forbid it),
    missing-integration for those tools must be checked only via the referenced-assistant
    path, not via the inline-tools path - otherwise the same issue surfaces twice.
    """
    tool_mock = MagicMock()
    tool_mock.name = "tool_a"
    tool_mock.integration_alias = None
    # Referenced assistant (has assistant_id) + inline tools - missing check must skip.
    mock_workflow_config.assistants = [MagicMock(id="asst_ref_1", assistant_id="assistant_1", tools=[tool_mock])]
    mock_workflow_config.states = [MagicMock(id="state_1", assistant_id="asst_ref_1")]
    unavailable, invalid_integration, missing_integration = _validate_tools_from_assistants_availability(
        mock_workflow_config, mock_user
    )
    assert missing_integration == []
    # _tool_requires_missing_integration must not be invoked for the referenced assistant
    # because the referenced-assistant validator handles it.
    mock_requires_missing.assert_not_called()


def test_workflow_config_resources_validation_error_to_dict_includes_message_key():
    """to_dict result includes a top-level 'message' key."""
    exc = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[("ref_1", "asst_1", "state_1")],
        unavailable_tools=[],
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
    )
    error_dict = exc.to_dict()
    assert error_dict["message"] == "Configuration references unavailable resources"


def test_workflow_config_resources_validation_error_to_dict_empty():
    """to_dict returns empty errors list when all resource lists are empty."""
    exc = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[],
        unavailable_tools=[],
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
    )
    error_dict = exc.to_dict()
    assert error_dict["error_type"] == "resource_validation"
    assert error_dict["errors"] == []


def test_workflow_config_resources_validation_error_to_dict_with_invalid_integration():
    """to_dict includes errors for invalid integration tools with correct path and message."""
    invalid_integration_tools = [("tool_a", "asst_ref_1", "state_1")]
    exc = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[],
        unavailable_tools=[],
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
        invalid_integration_tools=invalid_integration_tools,
    )
    error_dict = exc.to_dict()
    errors = error_dict["errors"]
    assert len(errors) == 1
    assert errors[0]["path"] == "tools"
    assert "tool_a" in errors[0]["details"]
    assert errors[0]["message"] == "Invalid integration settings"
    assert "state_id" not in errors[0]


def test_find_tool_meta_found():
    """_find_tool_meta returns correct ToolMeta for a matching tool in toolkits_metadata."""
    toolkits_metadata = [{"toolkit": "my_toolkit", "is_external": False, "tools": [{"name": "tool_a"}]}]
    exc = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[("ref", "asst_id", "state_1")],
        unavailable_tools=[],
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
        toolkits_metadata=toolkits_metadata,
    )
    meta = exc._find_tool_meta("tool_a")
    assert meta is not None
    assert meta.toolkit_name == "my_toolkit"
    assert meta.tool_name == "tool_a"
    assert meta.toolkit_type == "tools"


def test_find_tool_meta_external_toolkit():
    """_find_tool_meta sets toolkit_type to 'external-tools' for external toolkits."""
    toolkits_metadata = [{"toolkit": "ext_toolkit", "is_external": True, "tools": [{"name": "tool_b"}]}]
    exc = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[("ref", "asst_id", "state_1")],
        unavailable_tools=[],
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
        toolkits_metadata=toolkits_metadata,
    )
    meta = exc._find_tool_meta("tool_b")
    assert meta is not None
    assert meta.toolkit_type == "external-tools"


def test_find_tool_meta_not_found():
    """_find_tool_meta returns None when the tool is absent from toolkits_metadata."""
    exc = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[],
        unavailable_tools=[],
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
        toolkits_metadata=[{"toolkit": "tk", "is_external": False, "tools": [{"name": "other_tool"}]}],
    )
    assert exc._find_tool_meta("nonexistent_tool") is None


def test_find_tool_meta_empty_toolkits_metadata():
    """_find_tool_meta returns None when toolkits_metadata is empty."""
    exc = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[],
        unavailable_tools=[],
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
        toolkits_metadata=[],
    )
    assert exc._find_tool_meta("some_tool") is None


def test_workflow_config_resources_validation_error_creates_yaml_line_finder():
    """YamlLineFinder is used when workflow_config_dict and line_number_map are provided."""
    workflow_config_dict = {"states": [{"id": "state_1", "assistant_id": "asst_ref_1"}]}
    line_number_map = {"states[0].assistant_id": 3}
    exc = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[("asst_ref_1", "asst_1", "state_1")],
        unavailable_tools=[],
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
        workflow_config_dict=workflow_config_dict,
        line_number_map=line_number_map,
    )
    config_line = exc.line_finder.find_line_for_state_field("state_1", "assistant_id")
    assert config_line == 3


def test_workflow_config_resources_validation_error_null_line_finder_when_no_config():
    """NullYamlLineFinder is used when no workflow_config_dict is provided."""
    exc = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[("asst_ref_1", "asst_1", "state_1")],
        unavailable_tools=[],
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
    )
    assert exc.line_finder.find_line_for_state_field("state_1", "assistant_id") is None
    assert exc.line_finder.find_line_for_assistant_field("asst_1", "tools") is None


@patch("codemie.workflows.validation.resources._is_assistant_available", return_value=False)
@patch("codemie.workflows.validation.resources._is_tool_available", return_value=True)
@patch("codemie.workflows.validation.resources._is_datasource_available", return_value=True)
@patch("codemie.workflows.validation.resources._validate_referenced_assistants_integrations", return_value=[])
def test_validate_workflow_config_resources_availability_with_yaml_config(
    mock_ref_integrations,
    mock_is_datasource_available,
    mock_tool_available,
    mock_assistant_available,
    mock_workflow_config,
    mock_user,
):
    """When yaml_config is a valid YAML string, line numbers are extracted into the exception."""
    mock_workflow_config.yaml_config = (
        "states:\n"
        "  - id: state_1\n"
        "    assistant_id: asst_ref_1\n"
        "assistants:\n"
        "  - id: asst_ref_1\n"
        "    assistant_id: assistant_1\n"
    )
    with pytest.raises(WorkflowConfigResourcesValidationError) as exc_info:
        validate_workflow_config_resources_availability(mock_workflow_config, mock_user)
    assert exc_info.value.workflow_config_dict is not None
    assert exc_info.value.line_number_map is not None


@patch("codemie.workflows.validation.resources._is_assistant_available", return_value=True)
@patch("codemie.workflows.validation.resources._is_tool_available", return_value=True)
@patch("codemie.workflows.validation.resources._is_datasource_available", return_value=True)
@patch("codemie.workflows.validation.resources._is_integration_alias_valid", return_value=False)
@patch("codemie.workflows.validation.resources._validate_referenced_assistants_integrations", return_value=[])
def test_validate_workflow_config_resources_availability_raises_on_invalid_integration(
    mock_ref_integrations,
    mock_is_integration_alias,
    mock_is_datasource,
    mock_tool_available,
    mock_assistant_available,
    mock_user,
):
    """validate raises WorkflowConfigResourcesValidationError when integration alias is invalid."""
    config = MagicMock()
    config.tools = []
    config.assistants = [
        MagicMock(
            id="asst_ref_1",
            assistant_id="assistant_1",
            datasource_ids=[],
            tools=[MagicMock(name="tool_a", integration_alias="bad_alias")],
        )
    ]
    config.states = [MagicMock(id="state_1", assistant_id="asst_ref_1")]
    config.yaml_config = None
    config.project = "test_project"
    with pytest.raises(WorkflowConfigResourcesValidationError) as exc_info:
        validate_workflow_config_resources_availability(config, mock_user)
    assert len(exc_info.value.invalid_integration_tools) > 0


@patch("codemie.workflows.validation.resources.ToolsService.find_toolkit_for_tool")
def test_tool_requires_missing_integration_true(mock_find_toolkit, mock_user):
    """Tool whose credentials cannot be resolved at all returns (True, credential_type)."""
    from codemie.workflows.validation.resources import _tool_requires_missing_integration

    mock_find_toolkit.return_value = {"toolkit": "Jira"}
    with patch(
        "codemie.service.assistant.credential_validator.CredentialValidator.validate_tool_credentials"
    ) as mock_validate:
        mock_validate.return_value = MagicMock(is_valid=False, credential_type="Jira")
        is_missing, credential_type = _tool_requires_missing_integration(mock_user, "project", "jira_search", None)
        assert is_missing is True
        assert credential_type == "Jira"


@patch("codemie.workflows.validation.resources.ToolsService.find_toolkit_for_tool")
def test_tool_requires_missing_integration_false_when_auto_lookup_resolves(mock_find_toolkit, mock_user):
    """Automatic Credentials Lookup mode (no alias) is not flagged when credentials resolve."""
    from codemie.workflows.validation.resources import _tool_requires_missing_integration

    mock_find_toolkit.return_value = {"toolkit": "Jira"}
    with patch(
        "codemie.service.assistant.credential_validator.CredentialValidator.validate_tool_credentials"
    ) as mock_validate:
        mock_validate.return_value = MagicMock(is_valid=True, credential_type="Jira")
        is_missing, _ = _tool_requires_missing_integration(mock_user, "project", "jira_search", None)
        assert is_missing is False


@patch("codemie.workflows.validation.resources.ToolsService.find_setting_for_tool")
@patch("codemie.workflows.validation.resources.ToolsService.find_toolkit_for_tool")
def test_tool_requires_missing_integration_false_when_alias_configured(mock_find_toolkit, mock_find_setting, mock_user):
    """Tool with an explicit alias and satisfied credentials is not flagged."""
    from codemie.workflows.validation.resources import _tool_requires_missing_integration

    mock_find_toolkit.return_value = {"toolkit": "Jira"}
    mock_find_setting.return_value = MagicMock()
    with patch(
        "codemie.service.assistant.credential_validator.CredentialValidator.validate_tool_credentials"
    ) as mock_validate:
        mock_validate.return_value = MagicMock(is_valid=True, credential_type="Jira")
        is_missing, _ = _tool_requires_missing_integration(mock_user, "project", "jira_search", "my_alias")
        assert is_missing is False


@patch(
    "codemie.workflows.validation.resources.ToolsService.find_toolkit_for_tool",
    side_effect=ValueError("not found"),
)
def test_tool_requires_missing_integration_false_when_tool_not_found(mock_find_toolkit, mock_user):
    """Non-existent tool returns (False, None) (reported separately by availability validation)."""
    from codemie.workflows.validation.resources import _tool_requires_missing_integration

    assert _tool_requires_missing_integration(mock_user, "project", "ghost_tool", None) == (False, None)


@patch("codemie.workflows.validation.resources.ToolsService.find_toolkit_for_tool")
@patch(
    "codemie.workflows.validation.resources.ToolsService.find_setting_for_tool",
    side_effect=ValueError("bad alias"),
)
def test_tool_requires_missing_integration_false_when_alias_invalid(mock_find_setting, mock_find_toolkit, mock_user):
    """Invalid alias returns (False, None) (reported separately by _is_integration_alias_valid)."""
    from codemie.workflows.validation.resources import _tool_requires_missing_integration

    mock_find_toolkit.return_value = {"toolkit": "Jira"}
    assert _tool_requires_missing_integration(mock_user, "project", "jira_search", "bad_alias") == (False, None)


@patch.object(Assistant, "get_by_ids")
def test_validate_referenced_assistants_integrations_missing(mock_get_by_ids, mock_workflow_config, mock_user):
    """Referenced assistant with a toolkit missing its integration yields missing integration tuples."""
    from codemie.workflows.validation.resources import _validate_referenced_assistants_integrations

    mock_get_by_ids.return_value = [MagicMock(id="assistant_1", toolkits=[MagicMock()])]
    mock_workflow_config.assistants = [MagicMock(id="asst_ref_1", assistant_id="assistant_1")]
    mock_workflow_config.states = [MagicMock(id="state_1", assistant_id="asst_ref_1")]

    with patch(
        "codemie.service.assistant.assistant_integration_validator."
        "AssistantIntegrationValidator.collect_missing_integrations"
    ) as mock_collect:
        mock_collect.return_value = [MagicMock(tool="jira_search", credential_type="Jira")]
        result = _validate_referenced_assistants_integrations(mock_workflow_config, mock_user)

    assert ("jira_search", "asst_ref_1", "state_1", "Jira") in result


@patch.object(Assistant, "get_by_ids")
def test_validate_referenced_assistants_integrations_skips_inline(mock_get_by_ids, mock_workflow_config, mock_user):
    """Inline (virtual) assistants without assistant_id are skipped."""
    from codemie.workflows.validation.resources import _validate_referenced_assistants_integrations

    mock_workflow_config.assistants = [MagicMock(id="asst_ref_1", assistant_id=None)]
    result = _validate_referenced_assistants_integrations(mock_workflow_config, mock_user)
    assert result == []
    mock_get_by_ids.assert_not_called()


@patch.object(Assistant, "get_by_ids", return_value=[])
def test_validate_referenced_assistants_integrations_skips_missing_assistant(
    mock_get_by_ids, mock_workflow_config, mock_user
):
    """Referenced assistant that does not exist is skipped (reported by availability validation)."""
    from codemie.workflows.validation.resources import _validate_referenced_assistants_integrations

    mock_workflow_config.assistants = [MagicMock(id="asst_ref_1", assistant_id="assistant_1")]
    result = _validate_referenced_assistants_integrations(mock_workflow_config, mock_user)
    assert result == []


def test_workflow_config_resources_validation_error_to_dict_with_missing_integration():
    """to_dict groups tools sharing the same integration into a single missing-integration error."""
    exc = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[],
        unavailable_tools=[],
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
        missing_integration_tools=[
            ("jira_search", "asst_ref_1", "state_1", "Jira"),
            ("jira_create_issue", "asst_ref_1", "state_1", "Jira"),
        ],
    )
    error_dict = exc.to_dict()
    errors = error_dict["errors"]

    assert error_dict["error_type"] == "resource_validation"
    assert len(errors) == 1
    assert errors[0]["path"] == "tools"
    assert errors[0]["message"] == "Missing required integration"
    assert "Jira" in errors[0]["details"]
    assert "jira_search" in errors[0]["details"]
    assert "jira_create_issue" in errors[0]["details"]
    assert "asst_ref_1" in errors[0]["details"]
    assert "state_id" not in errors[0]


def test_workflow_config_resources_validation_error_to_dict_missing_integration_separate_per_type():
    """Tools requiring different integrations produce separate missing-integration errors."""
    exc = WorkflowConfigResourcesValidationError(
        unavailable_assistants=[],
        unavailable_tools=[],
        unavailable_tools_from_asst_integrations=[],
        unavailable_datasources=[],
        missing_integration_tools=[
            ("jira_search", "asst_ref_1", "state_1", "Jira"),
            ("github_search", "asst_ref_1", "state_1", "GitHub"),
        ],
    )
    errors = exc.to_dict()["errors"]

    assert len(errors) == 2
    details = sorted(e["details"] for e in errors)
    assert "GitHub" in details[0]
    assert "Jira" in details[1]


@pytest.fixture
def mock_workflow_config_with_id():
    wf = MagicMock()
    wf.id = "parent-wf-id"
    return wf


def test_validate_sub_workflow_availability_returns_empty_when_flag_disabled(mock_workflow_config_with_id, mock_user):
    mock_workflow_config_with_id.states = [
        MagicMock(id="s1", workflow_id="child-wf-id"),
    ]
    with patch("codemie.workflows.validation.resources.customer_config") as mock_cfg:
        mock_cfg.is_feature_enabled.return_value = False
        result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)
    assert result == []


def test_validate_sub_workflow_availability_no_workflow_id_states(mock_workflow_config_with_id, mock_user):
    mock_workflow_config_with_id.states = [
        MagicMock(id="s1", workflow_id=None, assistant_id="asst-1"),
    ]
    result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)
    assert result == []


def test_validate_sub_workflow_availability_found(mock_workflow_config_with_id, mock_user):
    mock_workflow_config_with_id.states = [
        MagicMock(id="s1", workflow_id="child-wf-id"),
    ]
    with patch("codemie.workflows.validation.resources.WorkflowService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_workflow.return_value = MagicMock()
        result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)
    assert result == []


def test_validate_sub_workflow_availability_not_found(mock_workflow_config_with_id, mock_user):
    mock_workflow_config_with_id.states = [
        MagicMock(id="s1", workflow_id="missing-wf"),
    ]
    with (
        patch("codemie.workflows.validation.resources.customer_config") as mock_cfg,
        patch("codemie.workflows.validation.resources.WorkflowService") as mock_service_cls,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_service = mock_service_cls.return_value
        mock_service.get_workflow.return_value = None
        result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)
    assert len(result) == 1
    state_id, wf_id, reason = result[0]
    assert state_id == "s1"
    assert wf_id == "missing-wf"
    assert "not found" in reason


def test_validate_sub_workflow_availability_self_reference(mock_workflow_config_with_id, mock_user):
    mock_workflow_config_with_id.states = [
        MagicMock(id="s1", workflow_id="parent-wf-id"),
    ]
    with patch("codemie.workflows.validation.resources.customer_config") as mock_cfg:
        mock_cfg.is_feature_enabled.return_value = True
        result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)
    assert len(result) == 1
    state_id, wf_id, reason = result[0]
    assert state_id == "s1"
    assert "self-reference" in reason


def test_validate_sub_workflow_availability_access_denied(mock_workflow_config_with_id, mock_user):
    mock_workflow_config_with_id.states = [
        MagicMock(id="s1", workflow_id="restricted-wf"),
    ]
    with (
        patch("codemie.workflows.validation.resources.customer_config") as mock_cfg,
        patch("codemie.workflows.validation.resources.WorkflowService") as mock_service_cls,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_service = mock_service_cls.return_value
        mock_service.get_workflow.side_effect = Exception("Access denied")
        result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)
    assert len(result) == 1
    assert "not found or access denied" in result[0][2]


# ---- tool_access save-time validation ----

_RES = "codemie.workflows.validation.resources"
_SCRIPT = "codemie.workflows.validation.tool_access"


def _script_config(access, tool_name="execute_workspace_script", created_by="creator-1"):
    from codemie.core.workflow_models.workflow_models import WorkflowAssistantTool

    config = MagicMock()
    config.project = "proj"
    config.created_by = MagicMock(user_id=created_by) if created_by else None
    config.tools = [
        WorkflowTool(
            id="script_tool",
            tool=tool_name,
            tool_access=[WorkflowAssistantTool(name=n, integration_alias=a) for n, a in access],
        )
    ]
    config.states = [MagicMock(id="step_1", tool_id="script_tool")]
    return config


@pytest.fixture
def script_env():
    from codemie_tools.base.models import CredentialTypes

    setting = MagicMock()
    setting.credential_type = CredentialTypes.JIRA
    with (
        patch(f"{_RES}.ToolsService.find_toolkit_for_tool", return_value={"toolkit": "Jira"}) as toolkit,
        patch(f"{_SCRIPT}.can_be_listed_for", return_value=True) as callable_tool,
        patch(f"{_RES}.ToolsService.find_setting_for_tool", return_value=setting) as find_setting,
        patch(f"{_SCRIPT}.ToolMetadataService.requires_credentials", return_value=True),
        patch(f"{_SCRIPT}.ToolMetadataService.get_config_class", return_value=object),
        patch(f"{_SCRIPT}.ToolMetadataService.get_credential_type", return_value="Jira"),
    ):
        yield MagicMock(toolkit=toolkit, callable_tool=callable_tool, find_setting=find_setting, setting=setting)


def test_tool_access_valid_map_passes(script_env, mock_user):
    from codemie.workflows.validation.tool_access import validate_tool_access

    config = _script_config([("jira_tool", "a1")])
    assert validate_tool_access(config, mock_user) == []
    script_env.find_setting.assert_called_once_with(
        user=mock_user, project_name="proj", integration_alias="a1", owner_user_id="creator-1"
    )


def test_tool_access_on_other_tool_is_refused_naming_step(script_env, mock_user):
    from codemie.workflows.validation.tool_access import validate_tool_access

    config = _script_config([("jira_tool", "a1")], tool_name="other_tool")
    errors = validate_tool_access(config, mock_user)
    assert len(errors) == 1
    assert "step_1" in errors[0].message
    assert "other_tool" in errors[0].message


def test_tool_access_unknown_tool_refused(script_env, mock_user):
    from codemie.workflows.validation.tool_access import validate_tool_access

    script_env.toolkit.side_effect = ValueError("nope")
    errors = validate_tool_access(_script_config([("ghost", None)]), mock_user)
    assert len(errors) == 1 and "ghost" in errors[0].message


def test_tool_access_non_callable_tool_refused(script_env, mock_user):
    from codemie.workflows.validation.tool_access import validate_tool_access

    script_env.callable_tool.return_value = False
    errors = validate_tool_access(_script_config([("jira_tool", None)]), mock_user)
    assert len(errors) == 1 and "jira_tool" in errors[0].message


def test_tool_access_missing_alias_refused(script_env, mock_user):
    from codemie.workflows.validation.tool_access import validate_tool_access

    script_env.find_setting.side_effect = ValueError("not found")
    errors = validate_tool_access(_script_config([("jira_tool", "gone")]), mock_user)
    assert len(errors) == 1
    assert "jira_tool" in errors[0].message and "gone" in errors[0].message


def test_tool_access_mismatched_credential_type_refused(script_env, mock_user):
    from codemie.workflows.validation.tool_access import validate_tool_access
    from codemie_tools.base.models import CredentialTypes

    script_env.setting.credential_type = CredentialTypes.CONFLUENCE
    errors = validate_tool_access(_script_config([("jira_tool", "a1")]), mock_user)
    assert len(errors) == 1
    assert "jira_tool" in errors[0].message and "a1" in errors[0].message


def test_tool_access_credentialless_tool_with_alias_refused(script_env, mock_user):
    from codemie.workflows.validation.tool_access import validate_tool_access

    with patch(f"{_SCRIPT}.ToolMetadataService.requires_credentials", return_value=False):
        errors = validate_tool_access(_script_config([("jira_tool", "a1")]), mock_user)
    assert len(errors) == 1
    assert "jira_tool" in errors[0].message and "a1" in errors[0].message


def test_tool_access_undeterminable_type_skips_with_warning(script_env, mock_user):
    from codemie.workflows.validation.tool_access import validate_tool_access

    with (
        patch(f"{_SCRIPT}.ToolMetadataService.get_credential_type", return_value=None),
        patch(f"{_SCRIPT}.logger") as log,
    ):
        errors = validate_tool_access(_script_config([("jira_tool", "a1")]), mock_user)
    assert errors == []
    log.warning.assert_called_once()


def test_tool_access_errors_refuse_the_save(script_env, mock_user):
    script_env.find_setting.side_effect = ValueError("not found")
    config = _script_config([("jira_tool", "gone")])
    config.assistants = []
    config.yaml_config = None
    with (
        patch(f"{_RES}._validate_assistants_availability", return_value=[]),
        patch(f"{_RES}._validate_tools_avaiability", return_value=[]),
        patch(f"{_RES}._validate_tools_from_assistants_availability", return_value=([], [], [])),
        patch(f"{_RES}._validate_referenced_assistants_integrations", return_value=[]),
        patch(f"{_RES}._validate_datasources_availability", return_value=[]),
        patch(f"{_RES}._validate_sub_workflow_availability", return_value=[]),
        pytest.raises(WorkflowConfigResourcesValidationError) as exc,
    ):
        validate_workflow_config_resources_availability(config, mock_user)
    assert "gone" in str(exc.value)
    details = [e["details"] for e in exc.value.to_dict()["errors"]]
    assert any("gone" in d and "jira_tool" in d for d in details)


def test_tool_access_errors_carry_the_row_path(script_env, mock_user):
    from codemie.workflows.validation.tool_access import validate_tool_access

    script_env.find_setting.side_effect = [script_env.setting, ValueError("not found")]
    config = _script_config([("a_tool", "ok"), ("jira_tool", "gone")])
    [error] = validate_tool_access(config, mock_user)
    assert error.path == "tool_access.1.integration_alias"
    assert error.state_id == "step_1"
    assert error.tool_id == "script_tool"


def test_tool_access_tool_level_problem_points_at_the_name(script_env, mock_user):
    from codemie.workflows.validation.tool_access import validate_tool_access

    script_env.callable_tool.side_effect = [True, False]
    [error] = validate_tool_access(_script_config([("a_tool", None), ("tavily", None)]), mock_user)
    assert error.path == "tool_access.1.name"
    assert "is not allowed in tool_access" in error.message


def test_tool_access_duplicate_name_is_refused_in_the_common_format(script_env, mock_user):
    from codemie.workflows.validation.tool_access import validate_tool_access

    [error] = validate_tool_access(_script_config([("jira_tool", None), ("jira_tool", "a1")]), mock_user)
    assert error.path == "tool_access.1.name"
    assert "more than once" in error.message and "jira_tool" in error.message
    assert error.state_id == "step_1"


def test_tool_access_error_line_points_at_the_entry_in_the_tools_section(mock_user):
    from codemie.workflows.validation.resources import (
        ToolAccessProblem,
        WorkflowConfigResourcesValidationError,
    )
    from codemie.workflows.validation.line_lookup import extract_line_numbers

    yaml_text = (
        "states:\n"
        "  - id: step_1\n"
        "    tool_id: script_tool\n"
        "tools:\n"
        "  - id: script_tool\n"
        "    tool: execute_workspace_script\n"
        "    tool_access:\n"
        "      - name: a_tool\n"
        "      - name: dup\n"
        "        integration_alias: one\n"
    )
    error = WorkflowConfigResourcesValidationError(
        [],
        [],
        [],
        [],
        yaml.safe_load(yaml_text),
        extract_line_numbers(yaml_text),
        [],
        invalid_tool_access=[
            ToolAccessProblem("script_tool", "m", "step_1", "tool_access.1.integration_alias"),
            ToolAccessProblem("script_tool", "n", "step_1", "tool_access.0.name"),
        ],
    )
    lines = [e["config_line"] for e in error.to_dict()["errors"]]
    assert lines == [10, 8]


def test_resource_error_lines_follow_the_source_yaml_not_the_rewritten_one(mock_user):
    from codemie.workflows.validation.resources import validate_workflow_config_resources_availability

    source = "tools:\n- id: script_tool\n  tool: execute_workspace_script\n  tool_access:\n  - name: ghost\n"
    config = _script_config([("ghost", None)])
    config.assistants = []
    config._source_yaml_config = source
    config.yaml_config = "x: 1\n" * 30 + source  # the rewritten text puts the entry 30 lines further down
    with (
        patch(f"{_RES}._validate_assistants_availability", return_value=[]),
        patch(f"{_RES}._validate_tools_avaiability", return_value=[]),
        patch(f"{_RES}._validate_tools_from_assistants_availability", return_value=([], [], [])),
        patch(f"{_RES}._validate_referenced_assistants_integrations", return_value=[]),
        patch(f"{_RES}._validate_datasources_availability", return_value=[]),
        patch(f"{_RES}._validate_sub_workflow_availability", return_value=[]),
        patch(f"{_RES}.ToolsService.find_toolkit_for_tool", side_effect=ValueError("nope")),
        pytest.raises(WorkflowConfigResourcesValidationError) as exc,
    ):
        validate_workflow_config_resources_availability(config, mock_user)
    [error] = exc.value.to_dict()["errors"]
    assert error["path"] == "tool_access.0.name"
    assert error["config_line"] == 5
