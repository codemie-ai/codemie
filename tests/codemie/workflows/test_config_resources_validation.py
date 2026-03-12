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
from codemie.workflows.config_resources_validation import (
    _extract_tools_from_assistants,
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


def test_extract_tools_from_assistants_without_tools(mock_workflow_config):
    mock_workflow_config.assistants = [MagicMock(tools=None)]
    tools = _extract_tools_from_assistants(mock_workflow_config)
    assert tools == []


def test_extract_tools_from_assistants_no_assistants(mock_workflow_config):
    mock_workflow_config.assistants = None
    tools = _extract_tools_from_assistants(mock_workflow_config)
    assert tools == []


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


@patch("codemie.workflows.config_resources_validation.MCPToolkitService.get_mcp_server_tools")
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


@patch("codemie.workflows.config_resources_validation.MCPToolkitService.get_mcp_server_tools")
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


@patch("codemie.workflows.config_resources_validation.MCPToolkitService.get_mcp_server_tools")
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


@patch("codemie.workflows.config_resources_validation._is_assistant_available", return_value=False)
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


@patch("codemie.workflows.config_resources_validation._is_assistant_available", return_value=True)
def test_validate_assistants_availability_all_available(mock_is_assistant_available, mock_workflow_config, mock_user):
    mock_workflow_config.assistants = [MagicMock(id="id_1", assistant_id="assistant_1")]
    unavailable = _validate_assistants_availability(mock_workflow_config, mock_user)
    assert unavailable == []


@patch("codemie.workflows.config_resources_validation._is_tool_available", return_value=True)
def test_validate_tools_from_assistants_availability_all_available(
    mock_is_tool_available, mock_workflow_config, mock_user
):
    mock_workflow_config.assistants = [MagicMock(tools=[MagicMock(name="Tool A")])]
    unavailable = _validate_tools_from_assistants_availability(mock_workflow_config, mock_user)
    assert unavailable == []


@patch("codemie.workflows.config_resources_validation._is_tool_available", return_value=False)
def test_validate_tools_availability_some_unavailable(mock_is_tool_available, mock_workflow_config, mock_user):
    mock_workflow_config.tools = [MagicMock(id="tool_1", tool="Tool A"), MagicMock(id="tool_2", tool="Tool B")]
    mock_workflow_config.states = [
        MagicMock(id="state_1", tool_id="tool_1"),
        MagicMock(id="state_2", tool_id="tool_2"),
    ]
    unavailable = _validate_tools_avaiability(mock_workflow_config, mock_user)
    assert unavailable == [("tool_1", "Tool A", "state_1"), ("tool_2", "Tool B", "state_2")]


@patch("codemie.workflows.config_resources_validation._is_tool_available", return_value=True)
def test_validate_tools_availability_all_available(mock_is_tool_available, mock_workflow_config, mock_user):
    mock_workflow_config.tools = [MagicMock(id="tool_1", tool="Tool A")]
    unavailable = _validate_tools_avaiability(mock_workflow_config, mock_user)
    assert unavailable == []


@patch(
    "codemie.workflows.config_resources_validation._is_datasource_available",
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


@patch("codemie.workflows.config_resources_validation._is_datasource_available", return_value=True)
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
    return config


@patch("codemie.workflows.config_resources_validation._is_assistant_available", return_value=True)
@patch("codemie.workflows.config_resources_validation._is_tool_available", return_value=True)
@patch("codemie.workflows.config_resources_validation._is_datasource_available", return_value=True)
def test_validate_workflow_config_resources_availability_all_available(
    mock_is_datasource_available, mock_tool_available, mock_assistant_available, mock_workflow_config, mock_user
):
    validate_workflow_config_resources_availability(mock_workflow_config, mock_user)


@patch("codemie.workflows.config_resources_validation._is_assistant_available", return_value=False)
@patch("codemie.workflows.config_resources_validation._is_tool_available", return_value=False)
@patch("codemie.workflows.config_resources_validation._is_datasource_available", return_value=None)
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
    unavailable_tools_from_assistants = ["tool_from_asst_1", "tool_from_asst_2"]
    unavailable_datasources = [("datasource_1", "asst_ref_1", "state_4"), ("datasource_2", "asst_ref_2", "state_5")]

    exception = WorkflowConfigResourcesValidationError(
        unavailable_assistants,
        unavailable_tools,
        unavailable_tools_from_assistants,
        unavailable_datasources,
    )

    error_dict = exception.to_dict()

    assert error_dict["error_type"] == "resource_validation"
    assert "errors" in error_dict
    assert len(error_dict["errors"]) == 7

    assistant_errors = [e for e in error_dict["errors"] if e["resource_type"] == "assistant"]
    assert len(assistant_errors) == 2
    assert assistant_errors[0]["resource_id"] == "asst_123"
    assert assistant_errors[0]["reference_state"] == "state_1"
    assert "asst_123" in assistant_errors[0]["message"]

    tool_errors = [e for e in error_dict["errors"] if e["resource_type"] == "tool"]
    assert len(tool_errors) == 1
    assert tool_errors[0]["resource_id"] == "tool_123"
    assert tool_errors[0]["reference_state"] == "state_3"

    tool_from_asst_errors = [e for e in error_dict["errors"] if e["resource_type"] == "tool_from_assistant"]
    assert len(tool_from_asst_errors) == 2
    assert "reference_state" not in tool_from_asst_errors[0]

    datasource_errors = [e for e in error_dict["errors"] if e["resource_type"] == "datasource"]
    assert len(datasource_errors) == 2
    # Now datasources have reference_state mapping to the state that uses them
    assert datasource_errors[0]["resource_id"] == "datasource_1"
    assert datasource_errors[0]["reference_state"] == "state_4"
    assert "asst_ref_1" in datasource_errors[0]["message"]
    assert datasource_errors[1]["resource_id"] == "datasource_2"
    assert datasource_errors[1]["reference_state"] == "state_5"
    assert "asst_ref_2" in datasource_errors[1]["message"]
