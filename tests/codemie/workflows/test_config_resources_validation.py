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
def test_validate_tools_from_assistants_availability_all_available(
    mock_is_tool_available, mock_workflow_config, mock_user
):
    mock_workflow_config.assistants = [MagicMock(tools=[MagicMock(name="Tool A", integration_alias=None)])]
    unavailable, invalid_integration = _validate_tools_from_assistants_availability(mock_workflow_config, mock_user)
    assert unavailable == []
    assert invalid_integration == []


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
def test_validate_workflow_config_resources_availability_all_available(
    mock_is_datasource_available, mock_tool_available, mock_assistant_available, mock_workflow_config, mock_user
):
    validate_workflow_config_resources_availability(mock_workflow_config, mock_user)


@patch("codemie.workflows.validation.resources._is_assistant_available", return_value=False)
@patch("codemie.workflows.validation.resources._is_tool_available", return_value=False)
@patch("codemie.workflows.validation.resources._is_datasource_available", return_value=None)
def test_validate_workflow_config_resources_availability_multiple_unavailable_resources(
    mock_is_datasource_available, mock_tool_available, mock_assistant_available, mock_workflow_config, mock_user
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
    unavailable, invalid_integration = _validate_tools_from_assistants_availability(mock_workflow_config, mock_user)
    assert ("tool_a", "asst_ref_1", "state_1") in unavailable
    assert invalid_integration == []


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
    unavailable, invalid_integration = _validate_tools_from_assistants_availability(mock_workflow_config, mock_user)
    assert unavailable == []
    assert ("tool_a", "asst_ref_1", "state_1") in invalid_integration


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
def test_validate_workflow_config_resources_availability_with_yaml_config(
    mock_is_datasource_available, mock_tool_available, mock_assistant_available, mock_workflow_config, mock_user
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
def test_validate_workflow_config_resources_availability_raises_on_invalid_integration(
    mock_is_integration_alias, mock_is_datasource, mock_tool_available, mock_assistant_available, mock_user
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
