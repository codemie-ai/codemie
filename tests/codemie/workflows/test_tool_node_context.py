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
Test Area: Tool Node Context Handling

Tests for ToolNode context handling, argument resolution, execution flow,
and tool-specific logic including MCP server integration.

This module tests the following critical functionality:
- Tool argument resolution from context store
- Tool execution with task dict in iterations
- JSON pointer extraction from results
- MCP server tool execution
- Pydantic argument validation
- Missing argument handling
- Context store update with tool output
- Dynamic value resolution in tool response
- Virtual assistant cleanup
- Output post-processing with Pydantic models
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from pydantic import BaseModel

from codemie.workflows.nodes.tool_node import ToolNode
from codemie.workflows.constants import (
    CONTEXT_STORE_VARIABLE,
    MESSAGES_VARIABLE,
    FIRST_STATE_IN_ITERATION,
    TASK_KEY,
)
from codemie.core.workflow_models import WorkflowNextState, WorkflowState, WorkflowConfig, WorkflowTool
from codemie.rest_api.models.assistant import MCPServerDetails


@pytest.fixture
def mock_workflow_execution_service():
    """Create mock WorkflowExecutionService."""
    service = Mock()
    service.start_state = Mock(return_value="state_123")
    service.finish_state = Mock()
    return service


@pytest.fixture
def mock_thought_queue():
    """Create mock ThoughtQueue."""
    return Mock()


@pytest.fixture
def mock_callbacks():
    """Create mock callbacks."""
    callback = Mock()
    callback.on_node_start = Mock()
    callback.on_node_end = Mock()
    return [callback]


@pytest.fixture
def mock_user():
    """Create mock User."""
    user = Mock()
    user.id = "user_123"
    user.username = "test_user"
    return user


@pytest.fixture
def mock_tool_config():
    """Create mock WorkflowTool configuration."""
    tool_config = Mock(spec=WorkflowTool)
    tool_config.id = "tool_1"
    tool_config.tool = "test_tool"
    tool_config.tool_args = {"arg1": "value1", "arg2": "{dynamic_value}"}
    tool_config.mcp_server = None
    tool_config.tool_result_json_pointer = None
    tool_config.resolve_dynamic_values_in_response = False
    return tool_config


@pytest.fixture
def mock_workflow_config(mock_tool_config):
    """Create mock WorkflowConfig."""
    config = Mock(spec=WorkflowConfig)
    config.project = "test_project"
    config.tools = [mock_tool_config]
    return config


@patch("codemie.workflows.nodes.tool_node.process_values")
def test_tc_tnc_001_tool_argument_resolution_from_context(
    mock_process_values,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
):
    """
    TC_TNC_001: Tool Argument Resolution from Context

    Verify dynamic value resolution in tool arguments from context store.
    """
    # Arrange
    mock_process_values.return_value = {"arg1": "value1", "arg2": "resolved_value"}

    state_schema = {
        CONTEXT_STORE_VARIABLE: {"dynamic_value": "resolved_value"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
    )

    # Act
    result = node._get_tool_args(
        tool_args={"arg1": "value1", "arg2": "{dynamic_value}"},
        state_schema=state_schema,
    )

    # Assert
    assert result == {"arg1": "value1", "arg2": "resolved_value"}
    mock_process_values.assert_called_once()


def test_tc_tnc_002_tool_execution_with_task_dict(
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
):
    """
    TC_TNC_002: Tool Execution with Task Dict

    Test FIRST_STATE_IN_ITERATION with task dict values.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"base_key": "base_value"},
        MESSAGES_VARIABLE: [],
        FIRST_STATE_IN_ITERATION: True,
        TASK_KEY: {"item_name": "item_1", "item_id": 42},
    }

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
    )

    with patch("codemie.workflows.nodes.tool_node.process_values") as mock_process_values:
        mock_process_values.return_value = {"arg1": "item_1", "arg2": "42"}

        # Act
        node._get_tool_args(
            tool_args={"arg1": "{item_name}", "arg2": "{item_id}"},
            state_schema=state_schema,
        )

        # Assert - process_values should receive context with both base and task dict
        call_args = mock_process_values.call_args
        context = call_args[0][1]  # Second argument is the context dict
        assert "base_key" in context
        assert "item_name" in context
        assert "item_id" in context


@patch("codemie.workflows.nodes.tool_node.UnwrappingJsonPointerEvaluator")
def test_tc_tnc_003_tool_with_json_pointer_extraction(
    mock_evaluator,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
    mock_tool_config,
):
    """
    TC_TNC_003: Tool with JSON Pointer Extraction

    Extract specific path from tool result.
    """
    # Arrange
    mock_tool_config.tool_result_json_pointer = "/data/result"
    mock_evaluator.get_node_by_pointer = Mock(return_value="extracted_value")

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
    )

    tool_output = {"data": {"result": "extracted_value", "other": "ignored"}}

    # Act
    result = node.post_process_output(state_schema, "task", tool_output)

    # Assert
    mock_evaluator.get_node_by_pointer.assert_called_once()
    assert result == "extracted_value"


@patch("codemie.workflows.nodes.tool_node.MCPToolkitService")
@patch("codemie.workflows.nodes.tool_node.config")
def test_tc_tnc_004_tool_with_mcp_server(
    mock_config,
    mock_mcp_toolkit_service,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
    mock_tool_config,
):
    """
    TC_TNC_004: Tool with MCP Server

    Test MCP tool execution flow.
    """
    # Arrange
    mock_config.MCP_CONNECT_ENABLED = True

    mcp_server = Mock(spec=MCPServerDetails)
    mcp_server.name = "test_mcp"
    mcp_server.enabled = True
    mcp_server.config = Mock()
    mcp_server.config.single_usage = False

    mock_tool_config.mcp_server = mcp_server

    # Mock MCP tool
    mock_mcp_tool = Mock()
    mock_mcp_tool.name = "test_tool"
    mock_mcp_tool.args_schema = {"properties": {"arg1": {"type": "string"}}}
    mock_mcp_tool.execute = Mock(return_value="MCP tool result")

    mock_mcp_toolkit_service.get_mcp_server_tools = Mock(return_value=[mock_mcp_tool])

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute MCP tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
        tool_args={"arg1": "test"},
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
    )

    # Act
    result = node._execute_mcp_tool(state_schema)

    # Assert
    assert result == "MCP tool result"
    mock_mcp_toolkit_service.get_mcp_server_tools.assert_called_once()
    mock_mcp_tool.execute.assert_called_once()


def test_tc_tnc_005_tool_argument_validation(
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
):
    """
    TC_TNC_005: Tool Argument Validation

    Test Pydantic validation of tool arguments.
    """

    # Arrange
    class ToolArgsModel(BaseModel):
        name: str
        count: int

    mock_tool = Mock()
    mock_tool.args_schema = ToolArgsModel
    mock_tool.execute = Mock(return_value="success")

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
        tool_args={"name": "test", "count": 5},
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
    )

    # Act
    with patch("codemie.workflows.nodes.tool_node.process_values") as mock_process_values:
        mock_process_values.return_value = {"name": "test", "count": 5}
        result = node._execute_tool_with_args(mock_tool, state_schema)

    # Assert
    assert result == "success"
    mock_tool.execute.assert_called_once_with(name="test", count=5)


def test_tc_tnc_006_tool_with_missing_arguments(
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
):
    """
    TC_TNC_006: Tool with Missing Arguments

    Verify None values for missing parameters.
    """

    # Arrange
    class ToolArgsModel(BaseModel):
        required_arg: str
        optional_arg: str | None = None

    mock_tool = Mock()
    mock_tool.args_schema = ToolArgsModel
    mock_tool.execute = Mock(return_value="success")

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
        tool_args=None,  # No args provided
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
    )

    # Act
    with patch("codemie.workflows.nodes.tool_node.process_values") as mock_process_values:
        mock_process_values.return_value = {"required_arg": None, "optional_arg": None}

        # The function should handle None values gracefully
        # Since all values are None, validation is skipped
        result = node._execute_tool_with_args(mock_tool, state_schema)

    # Assert
    # Tool should be executed with empty dict (only non-None values passed)
    assert result == "success"


@patch("codemie.workflows.nodes.tool_node.ToolsService")
@patch("codemie.workflows.nodes.tool_node.ToolkitService")
def test_tc_tnc_007_tool_context_store_update(
    mock_toolkit_service,
    mock_tools_service,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
):
    """
    TC_TNC_007: Tool Context Store Update

    Verify tool output added to context store when store_in_context=True.
    """
    # Arrange
    # Mock the services to avoid database calls
    mock_tools_service.return_value.find_by_id.return_value = None
    mock_toolkit_service.return_value.find_by_project.return_value = []

    state_schema = {
        CONTEXT_STORE_VARIABLE: {"existing": "data"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(
            state_id="next",
            store_in_context=True,
            include_in_llm_history=False,
        ),
        tool_id="tool_1",
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
    )

    # Mock _is_execution_aborted to assume execution is active (not aborted)
    with patch.object(node, "_is_execution_aborted", return_value=False):
        with patch.object(node, "_execute_regular_tool", return_value={"new_key": "new_value"}):
            # Act
            result = node(state_schema)

    # Assert
    # Tool node returns only new values in context store (when store_in_context=True)
    # Existing context is not automatically preserved (done by LangGraph state reducer)
    context_store = result[CONTEXT_STORE_VARIABLE]
    assert "new_key" in context_store
    assert context_store.get("new_key") == "new_value"


@patch("codemie.workflows.nodes.tool_node.process_string")
def test_tc_tnc_008_tool_with_resolve_dynamic_values_in_response(
    mock_process_string,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
    mock_tool_config,
):
    """
    TC_TNC_008: Tool with resolve_dynamic_values_in_response

    Test dynamic value resolution in tool response.
    """
    # Arrange
    mock_tool_config.resolve_dynamic_values_in_response = True
    mock_process_string.return_value = "Response with user_name: Alice"

    state_schema = {
        CONTEXT_STORE_VARIABLE: {"user_name": "Alice"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
    )

    tool_output = "Response with user_name: {user_name}"

    # Act
    result = node.post_process_output(state_schema, "task", tool_output)

    # Assert
    assert result == "Response with user_name: Alice"
    mock_process_string.assert_called_once()


@patch("codemie.workflows.nodes.tool_node.VirtualAssistantService")
@patch("codemie.workflows.nodes.tool_node.ToolkitService")
@patch("codemie.workflows.nodes.tool_node.ToolsService")
def test_tc_tnc_009_tool_virtual_assistant_cleanup(
    mock_tools_service,
    mock_toolkit_service,
    mock_virtual_assistant_service,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
):
    """
    TC_TNC_009: Tool Virtual Assistant Cleanup

    Verify virtual assistant deleted after execution.
    """
    # Arrange
    mock_assistant = Mock()
    mock_assistant.id = "assistant_temp_123"
    mock_virtual_assistant_service.create_from_tool_config = Mock(return_value=mock_assistant)

    mock_tool = Mock()
    mock_tool.args_schema = {}
    mock_tool.execute = Mock(return_value="tool result")
    mock_tools_service.find_tool_from_config = Mock(return_value=mock_tool)

    mock_toolkit_service.get_toolkit_methods = Mock(return_value=[])

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
    )

    with patch("codemie.workflows.nodes.tool_node.process_values", return_value={}):
        # Act
        node._execute_regular_tool(state_schema)

    # Assert
    # Virtual assistant should be deleted after execution
    mock_virtual_assistant_service.delete.assert_called_once_with("assistant_temp_123")


@patch("codemie.workflows.nodes.tool_node.VirtualAssistantService")
@patch("codemie.workflows.nodes.tool_node.ToolkitService")
@patch("codemie.workflows.nodes.tool_node.ToolsService")
@patch("codemie.workflows.nodes.tool_node.build_unique_file_objects_list")
def test_tc_tnc_011_file_name_builds_file_objects_and_passes_to_find_tool(
    mock_build_files,
    mock_tools_service,
    mock_toolkit_service,
    mock_virtual_assistant_service,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
):
    """
    TC_TNC_011: file_name on ToolNode injects file_objects into tool after construction

    When ToolNode has file_name set, _execute_regular_tool must:
    1. Call build_unique_file_objects_list with that file_name and execution_id
    2. Assign file_objects directly to tool.input_files — the only reliable path,
       since CodeExecutorTool.__init__ always calls CodeExecutorConfig.from_env()
       and the toolkit fallback path has no access to file_objects.
    """
    # Arrange
    fake_file_objects = [MagicMock()]
    mock_build_files.return_value = fake_file_objects

    mock_assistant = Mock()
    mock_assistant.id = "assistant_123"
    mock_virtual_assistant_service.create_from_tool_config.return_value = mock_assistant

    mock_tool = Mock()
    mock_tool.args_schema = {}
    mock_tool.execute.return_value = "result"
    mock_tools_service.find_tool_from_config.return_value = mock_tool
    mock_toolkit_service.get_toolkit_methods.return_value = []

    state_schema = {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
        file_name="report.xlsx",
    )

    with patch("codemie.workflows.nodes.tool_node.process_values", return_value={}):
        # Act
        node._execute_regular_tool(state_schema)

    # Assert: file objects built with correct args
    mock_build_files.assert_called_once_with(
        file_names=["report.xlsx"],
        conversation_id="exec_123",
    )
    # Assert: file objects assigned directly to tool
    assert mock_tool.input_files == fake_file_objects


@patch("codemie.workflows.nodes.tool_node.VirtualAssistantService")
@patch("codemie.workflows.nodes.tool_node.ToolkitService")
@patch("codemie.workflows.nodes.tool_node.ToolsService")
@patch("codemie.workflows.nodes.tool_node.build_unique_file_objects_list")
def test_tc_tnc_012_no_file_name_passes_none_file_objects_to_find_tool(
    mock_build_files,
    mock_tools_service,
    mock_toolkit_service,
    mock_virtual_assistant_service,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
):
    """
    TC_TNC_012: No file_name — file_objects=None forwarded, build_unique_file_objects_list not called

    When ToolNode has no file_name, find_tool_from_config receives file_objects=None
    and build_unique_file_objects_list is never invoked.
    """
    # Arrange
    mock_assistant = Mock()
    mock_assistant.id = "assistant_123"
    mock_virtual_assistant_service.create_from_tool_config.return_value = mock_assistant

    mock_tool = Mock()
    mock_tool.args_schema = {}
    mock_tool.execute.return_value = "result"
    mock_tools_service.find_tool_from_config.return_value = mock_tool
    mock_toolkit_service.get_toolkit_methods.return_value = []

    state_schema = {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
        # no file_name
    )

    with patch("codemie.workflows.nodes.tool_node.process_values", return_value={}):
        # Act
        node._execute_regular_tool(state_schema)

    # Assert: builder not called, None forwarded
    mock_build_files.assert_not_called()
    _, kwargs = mock_tools_service.find_tool_from_config.call_args
    assert kwargs.get("file_objects") is None


def test_tc_tnc_010_tool_output_post_processing_pydantic(
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_user,
    mock_workflow_config,
):
    """
    TC_TNC_010: Tool Output Post-Processing

    Test Pydantic model serialization.
    """

    # Arrange
    class ToolOutputModel(BaseModel):
        status: str
        data: dict

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
    )

    tool_output = ToolOutputModel(status="success", data={"key": "value"})

    # Act
    result = node.post_process_output(state_schema, "task", tool_output)

    # Assert
    # Output should be JSON string
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert parsed["data"]["key"] == "value"
