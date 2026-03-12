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
Tests for MCP server functionality in workflow contexts.
"""

import pytest
from unittest.mock import Mock, patch

from codemie.core.workflow_models.workflow_models import WorkflowAssistant
from codemie.rest_api.models.assistant import MCPServerDetails, VirtualAssistant
from codemie.rest_api.security.user import User
from codemie.service.assistant.virtual_assistant_service import VirtualAssistantService
from codemie.service.mcp.models import MCPServerConfig


class TestWorkflowMCPServers:
    """Test MCP server integration with WorkflowAssistant"""

    def setup_method(self):
        """Set up test fixtures"""
        self.mock_user = Mock(spec=User)
        self.mock_user.id = "test-user-id"
        self.mock_user.name = "Test User"
        self.mock_user.full_name = "Test User Full"

        # Clear virtual assistants before each test
        VirtualAssistantService.assistants.clear()

    def test_workflow_assistant_mcp_server_validation_invalid(self):
        """Test MCP server validation fails for invalid configurations"""
        mcp_server = MCPServerDetails(
            name="test-server",
            enabled=True,
            # Missing config, mcp_connect_url, and command
        )

        with pytest.raises(ValueError, match="MCP server 'test-server' is enabled but missing configuration"):
            WorkflowAssistant(name="Test Assistant", mcp_servers=[mcp_server])

    def test_workflow_assistant_mcp_server_disabled_no_validation(self):
        """Test disabled MCP servers don't trigger validation"""
        mcp_server = MCPServerDetails(
            name="test-server",
            enabled=False,
            # Missing configuration, but disabled
        )

        workflow_assistant = WorkflowAssistant(name="Test Assistant", mcp_servers=[mcp_server])

        assert len(workflow_assistant.mcp_servers) == 1
        assert workflow_assistant.mcp_servers[0].enabled is False

    @patch('codemie.service.tools.tool_service.ToolsService.get_toolkits_from_assistant_tool_config')
    def test_virtual_assistant_service_create_from_workflow_config(self, mock_get_toolkits):
        """Test VirtualAssistantService creates assistant with MCP servers from WorkflowAssistant"""
        mock_get_toolkits.return_value = []

        mcp_server = MCPServerDetails(
            name="test-mcp-server",
            description="Test MCP Server",
            enabled=True,
            config=MCPServerConfig(command="echo", args=["test"]),
        )

        workflow_assistant = WorkflowAssistant(name="Test Workflow Assistant", mcp_servers=[mcp_server])

        virtual_assistant = VirtualAssistantService.create_from_virtual_asst_config(
            config=workflow_assistant,
            user=self.mock_user,
            project_name="test-project",
            execution_id="test-execution-id",
        )

        assert isinstance(virtual_assistant, VirtualAssistant)
        assert len(virtual_assistant.mcp_servers) == 1
        assert virtual_assistant.mcp_servers[0].name == "test-mcp-server"
        assert virtual_assistant.mcp_servers[0].enabled is True

    @patch('codemie.service.tools.tool_service.ToolsService.get_toolkits_from_assistant_tool_config')
    def test_virtual_assistant_service_create_with_multiple_mcp_servers(self, mock_get_toolkits):
        """Test VirtualAssistantService handles multiple MCP servers"""
        mock_get_toolkits.return_value = []

        mcp_servers = [
            MCPServerDetails(name="server-1", enabled=True, config=MCPServerConfig(command="echo", args=["server1"])),
            MCPServerDetails(name="server-2", enabled=False, config=MCPServerConfig(command="echo", args=["server2"])),
            MCPServerDetails(
                name="server-3",
                enabled=True,
                config=MCPServerConfig(command="test-command"),  # Required for validation
                mcp_connect_url="http://localhost:8080",
            ),
        ]

        workflow_assistant = WorkflowAssistant(name="Multi-MCP Assistant", mcp_servers=mcp_servers)

        virtual_assistant = VirtualAssistantService.create_from_virtual_asst_config(
            config=workflow_assistant,
            user=self.mock_user,
            project_name="test-project",
            execution_id="test-execution-id",
        )

        assert len(virtual_assistant.mcp_servers) == 3
        server_names = [server.name for server in virtual_assistant.mcp_servers]
        assert "server-1" in server_names
        assert "server-2" in server_names
        assert "server-3" in server_names

    def test_virtual_assistant_service_create_without_mcp_servers(self):
        """Test VirtualAssistantService create method without MCP servers"""
        virtual_assistant = VirtualAssistantService.create(
            toolkits=[], project="test-project", name="Test Assistant", execution_id="test-execution-id"
        )

        assert virtual_assistant.mcp_servers == []

    def test_virtual_assistant_service_create_with_mcp_servers(self):
        """Test VirtualAssistantService create method with MCP servers"""
        mcp_servers = [
            MCPServerDetails(name="direct-server", enabled=True, config=MCPServerConfig(command="test-command"))
        ]

        virtual_assistant = VirtualAssistantService.create(
            toolkits=[],
            project="test-project",
            name="Test Assistant",
            execution_id="test-execution-id",
            mcp_servers=mcp_servers,
        )

        assert len(virtual_assistant.mcp_servers) == 1
        assert virtual_assistant.mcp_servers[0].name == "direct-server"

    def teardown_method(self):
        """Clean up after each test"""
        VirtualAssistantService.assistants.clear()
