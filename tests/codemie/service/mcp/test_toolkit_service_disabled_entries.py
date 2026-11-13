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

"""Characterization tests: disabled MCP entries at runtime.

Agent/assistant nodes reach ``MCPToolkitService.get_mcp_server_tools`` (via
``ToolkitService.get_tools``), where a disabled entry is skipped silently. A workflow tool node
bound to a disabled MCP server still fails loudly.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from codemie.core.workflow_models import WorkflowConfig, WorkflowNextState, WorkflowState, WorkflowTool
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.service.mcp.models import MCPServerConfig
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE, MESSAGES_VARIABLE
from codemie.workflows.nodes.tool_node import ToolNode


def _disabled_server(name: str = "disabled-server") -> MCPServerDetails:
    return MCPServerDetails(
        name=name,
        enabled=False,
        config=MCPServerConfig(command="uvx", args=["example-server"], env={}),
    )


def test_disabled_mcp_entry_in_assistant_node_yields_no_tools_and_no_error() -> None:
    with (
        patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()),
        patch.object(MCPToolkitService, "_process_single_mcp_server") as mock_process,
    ):
        tools = MCPToolkitService.get_mcp_server_tools([_disabled_server()], user_id="user-1")

    assert tools == []
    mock_process.assert_not_called()


def test_disabled_mcp_entry_in_workflow_agent_node_is_skipped() -> None:
    """The agent node's virtual assistant carries the workflow assistant's MCP servers into the toolkit lookup."""
    from codemie.core.workflow_models import WorkflowAssistant
    from codemie.service.assistant.virtual_assistant_service import VirtualAssistantService
    from codemie.service.tools.toolkit_service import ToolkitService

    config = WorkflowAssistant(id="agent_1", mcp_servers=[_disabled_server("agent-server")])
    with patch(
        "codemie.service.assistant.virtual_assistant_service.ToolsService.get_toolkits_from_assistant_tool_config",
        return_value=[],
    ):
        assistant = VirtualAssistantService.create_from_virtual_asst_config(
            config, user=Mock(), project_name="proj", execution_id="exec-1"
        )

    merged = ToolkitService._merge_skill_mcp_servers(assistant)
    assert [s.name for s in merged] == ["agent-server"]

    with (
        patch.object(MCPToolkitService, "get_instance", return_value=MagicMock()),
        patch.object(MCPToolkitService, "_process_single_mcp_server") as mock_process,
    ):
        tools = MCPToolkitService.get_mcp_server_tools(merged, user_id="user-1")

    assert tools == []
    mock_process.assert_not_called()
    VirtualAssistantService.delete(assistant.id)


def test_disabled_mcp_tool_node_still_raises() -> None:
    mcp_server = _disabled_server("tool-node-server")
    tool_config = Mock(spec=WorkflowTool)
    tool_config.id = "tool_1"
    tool_config.tool = "test_tool"
    tool_config.tool_args = {}
    tool_config.mcp_server = mcp_server
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.project = "test_project"
    workflow_config.tools = [tool_config]
    workflow_config.is_global = False
    workflow_config.created_by = None
    user = Mock()
    user.id = "user_123"

    node = ToolNode(
        callbacks=[Mock()],
        workflow_execution_service=Mock(),
        thought_queue=Mock(),
        workflow_state=WorkflowState(
            id="tool_node",
            task="Execute MCP tool",
            next=WorkflowNextState(state_id="next"),
            tool_id="tool_1",
            tool_args={},
        ),
        workflow_config=workflow_config,
        user=user,
        execution_id="exec_123",
    )

    with (
        patch("codemie.workflows.nodes.tool_node.config") as mock_config,
        patch("codemie.workflows.nodes.tool_node.MCPToolkitService") as mock_service,
    ):
        mock_config.MCP_CONNECT_ENABLED = True
        with pytest.raises(ValueError, match="MCP server 'tool-node-server' is disabled"):
            node._execute_mcp_tool({CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []})

    mock_service.get_mcp_server_tools.assert_not_called()
