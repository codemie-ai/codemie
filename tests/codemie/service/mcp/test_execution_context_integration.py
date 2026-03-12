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
Tests for MCP execution context integration and propagation.

This module contains tests for the execution context functionality added to
the MCP system, including context creation, propagation, and tool wrapping.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from codemie.service.mcp.models import (
    MCPExecutionContext,
    MCPServerConfig,
    MCPToolInvocationResponse,
    MCPToolContentItem,
)
from codemie.service.mcp.toolkit import MCPTool, ContextAwareMCPTool
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.mcp.client import MCPConnectClient
from codemie.rest_api.models.assistant import MCPServerDetails


class TestMCPExecutionContextIntegration:
    """Tests for execution context integration across MCP components."""

    @pytest.fixture
    def sample_execution_context(self):
        """Create a sample execution context for testing."""
        return MCPExecutionContext(
            user_id="user-123",
            assistant_id="assistant-456",
            project_name="test-project",
            workflow_execution_id="workflow-789",
        )

    @pytest.fixture
    def sample_mcp_server_details(self):
        """Create sample MCP server details for testing."""
        server_details = MagicMock(spec=MCPServerDetails)
        server_details.name = "test-server"
        server_details.command = "npx"
        server_details.args = ["-y", "test-mcp-server"]
        server_details.env = {"TEST_TOKEN": "token123"}
        server_details.enabled = True
        server_details.tools_tokens_size_limit = None
        server_details.description = "Test MCP server description"
        server_details.tools = None
        server_details.config = None
        return server_details

    @pytest.fixture
    def sample_mcp_tool(self):
        """Create a sample MCP tool for testing."""
        mock_client = MagicMock(spec=MCPConnectClient)
        mock_server_config = MCPServerConfig(
            command="test_command", args=["arg1", "arg2"], env={"TEST_VAR": "test_value"}
        )

        from pydantic import create_model

        args_schema = create_model("TestSchema", test_param=(str, ...))

        return MCPTool(
            name="test_tool",
            description="A test MCP tool",
            mcp_client=mock_client,
            mcp_server_config=mock_server_config,
            args_schema=args_schema,
        )

    def test_create_context_aware_tools(self, sample_execution_context, sample_mcp_tool):
        """Test creation of context-aware tools from regular MCP tools."""
        tools = [sample_mcp_tool]

        context_aware_tools = MCPToolkitService._create_context_aware_tools(tools, sample_execution_context)

        assert len(context_aware_tools) == 1
        assert isinstance(context_aware_tools[0], ContextAwareMCPTool)
        assert context_aware_tools[0].name == "test_tool"
        assert context_aware_tools[0]._execution_context == sample_execution_context

    def test_create_context_aware_tools_preserves_attributes(self, sample_execution_context):
        """Test that context-aware tool creation preserves all original tool attributes."""
        mock_client = MagicMock(spec=MCPConnectClient)
        mock_server_config = MCPServerConfig(
            command="test_command", args=["arg1", "arg2"], env={"TEST_VAR": "test_value"}
        )

        from pydantic import create_model

        args_schema = create_model("TestSchema", test_param=(str, ...))

        original_tool = MCPTool(
            name="test_tool",
            description="A test MCP tool",
            mcp_client=mock_client,
            mcp_server_config=mock_server_config,
            args_schema=args_schema,
            return_direct=True,
            verbose=True,
            metadata={"custom": "metadata", "test": "value"},
            tokens_size_limit=2000,
        )

        context_aware_tools = MCPToolkitService._create_context_aware_tools([original_tool], sample_execution_context)
        context_aware_tool = context_aware_tools[0]

        # Verify all attributes are preserved
        assert context_aware_tool.name == original_tool.name
        assert context_aware_tool.description == original_tool.description
        assert context_aware_tool.mcp_client == original_tool.mcp_client
        assert context_aware_tool.mcp_server_config == original_tool.mcp_server_config
        assert context_aware_tool.args_schema == original_tool.args_schema
        assert context_aware_tool.return_direct == original_tool.return_direct
        assert context_aware_tool.verbose == original_tool.verbose
        assert context_aware_tool.tokens_size_limit == original_tool.tokens_size_limit

        # Verify metadata is copied but not shared
        assert context_aware_tool.metadata == original_tool.metadata
        assert context_aware_tool.metadata is not original_tool.metadata

    def test_create_context_aware_tools_empty_list(self, sample_execution_context):
        """Test creation of context-aware tools with empty tool list."""
        tools = []

        context_aware_tools = MCPToolkitService._create_context_aware_tools(tools, sample_execution_context)

        assert len(context_aware_tools) == 0

    @patch('codemie.service.mcp.toolkit_service.MCPToolkitService._get_toolkit_service_for_server')
    @patch('codemie.service.mcp.toolkit_service.MCPToolkitService._prepare_server_config')
    def test_process_single_mcp_server_with_context(
        self,
        mock_prepare_server_config,
        mock_get_toolkit_service,
        sample_mcp_server_details,
        sample_execution_context,
        sample_mcp_tool,
    ):
        """Test processing of single MCP server with execution context."""
        # Setup mocks
        mock_server_config = MCPServerConfig(
            command="npx", args=["-y", "test-mcp-server"], env={"TEST_TOKEN": "token123"}
        )
        mock_prepare_server_config.return_value = mock_server_config

        mock_toolkit_service = MagicMock()
        mock_toolkit = MagicMock()
        mock_toolkit.get_tools.return_value = [sample_mcp_tool]
        mock_toolkit_service.get_toolkit.return_value = mock_toolkit
        mock_get_toolkit_service.return_value = mock_toolkit_service

        # Call the method
        result = MCPToolkitService._process_single_mcp_server(
            sample_mcp_server_details,
            mock_toolkit_service,
            user_id="user-123",
            project_name="test-project",
            execution_context=sample_execution_context,
        )

        # Verify context-aware tools are returned
        assert len(result) == 1
        assert isinstance(result[0], ContextAwareMCPTool)
        assert result[0]._execution_context == sample_execution_context

    @patch('codemie.service.mcp.toolkit_service.MCPToolkitService._get_toolkit_service_for_server')
    @patch('codemie.service.mcp.toolkit_service.MCPToolkitService._prepare_server_config')
    def test_process_single_mcp_server_without_context(
        self,
        mock_prepare_server_config,
        mock_get_toolkit_service,
        sample_mcp_server_details,
        sample_mcp_tool,
    ):
        """Test processing of single MCP server without execution context."""
        # Setup mocks
        mock_server_config = MCPServerConfig(
            command="npx", args=["-y", "test-mcp-server"], env={"TEST_TOKEN": "token123"}
        )
        mock_prepare_server_config.return_value = mock_server_config

        mock_toolkit_service = MagicMock()
        mock_toolkit = MagicMock()
        mock_toolkit.get_tools.return_value = [sample_mcp_tool]
        mock_toolkit_service.get_toolkit.return_value = mock_toolkit
        mock_get_toolkit_service.return_value = mock_toolkit_service

        # Call the method without execution context
        result = MCPToolkitService._process_single_mcp_server(
            sample_mcp_server_details,
            mock_toolkit_service,
            user_id="user-123",
            project_name="test-project",
            execution_context=None,
        )

        # Verify regular tools are returned (not context-aware)
        assert len(result) == 1
        assert isinstance(result[0], MCPTool)
        assert not isinstance(result[0], ContextAwareMCPTool)

    @patch('codemie.service.mcp.toolkit_service.MCPToolkitService._process_single_mcp_server')
    @patch('codemie.service.mcp.toolkit_service.MCPToolkitService.get_instance')
    def test_get_mcp_server_tools_with_context(
        self,
        mock_get_instance,
        mock_process_single_server,
        sample_mcp_server_details,
        sample_execution_context,
        sample_mcp_tool,
    ):
        """Test get_mcp_server_tools with execution context parameters."""
        # Setup mocks
        mock_toolkit_service = MagicMock()
        mock_get_instance.return_value = mock_toolkit_service

        context_aware_tool = ContextAwareMCPTool(sample_mcp_tool, sample_execution_context)
        mock_process_single_server.return_value = [context_aware_tool]

        # Call the method with context parameters
        result = MCPToolkitService.get_mcp_server_tools(
            mcp_servers=[sample_mcp_server_details],
            user_id="user-123",
            project_name="test-project",
            assistant_id="assistant-456",
            workflow_execution_id="workflow-789",
        )

        # Verify execution context was created and passed
        mock_process_single_server.assert_called_once()
        call_kwargs = mock_process_single_server.call_args[1]

        assert 'execution_context' in call_kwargs
        execution_context = call_kwargs['execution_context']
        assert execution_context.user_id == "user-123"
        assert execution_context.assistant_id == "assistant-456"
        assert execution_context.project_name == "test-project"
        assert execution_context.workflow_execution_id == "workflow-789"

        # Verify context-aware tools are returned
        assert len(result) == 1
        assert isinstance(result[0], ContextAwareMCPTool)

    @patch('codemie.service.mcp.toolkit_service.MCPToolkitService._process_single_mcp_server')
    @patch('codemie.service.mcp.toolkit_service.MCPToolkitService.get_instance')
    def test_get_mcp_server_tools_without_context_params(
        self,
        mock_get_instance,
        mock_process_single_server,
        sample_mcp_server_details,
        sample_mcp_tool,
    ):
        """Test get_mcp_server_tools without context parameters (backward compatibility)."""
        # Setup mocks
        mock_toolkit_service = MagicMock()
        mock_get_instance.return_value = mock_toolkit_service
        mock_process_single_server.return_value = [sample_mcp_tool]

        # Call the method without context parameters
        result = MCPToolkitService.get_mcp_server_tools(
            mcp_servers=[sample_mcp_server_details],
            user_id="user-123",
            project_name="test-project",
        )

        # Verify execution context was still created (with None values for missing params)
        mock_process_single_server.assert_called_once()
        call_kwargs = mock_process_single_server.call_args[1]

        assert 'execution_context' in call_kwargs
        execution_context = call_kwargs['execution_context']
        assert execution_context.user_id == "user-123"
        assert execution_context.assistant_id is None
        assert execution_context.project_name == "test-project"
        assert execution_context.workflow_execution_id is None

        # Verify regular tools are returned
        assert len(result) == 1
        assert isinstance(result[0], MCPTool)

    def test_context_aware_tool_execution_integration(self, sample_execution_context):
        """Test full integration of context-aware tool execution."""
        # Create mock client and server config
        mock_client = MagicMock(spec=MCPConnectClient)
        mock_server_config = MCPServerConfig(
            command="test_command", args=["arg1", "arg2"], env={"TEST_VAR": "test_value"}
        )

        # Setup mock response
        mock_response = MCPToolInvocationResponse(
            isError=False, content=[MCPToolContentItem(type="text", text="Context-aware execution result")]
        )
        mock_client.invoke_tool = AsyncMock(return_value=mock_response)

        from pydantic import create_model

        args_schema = create_model("TestSchema", test_param=(str, ...))

        # Create original tool
        original_tool = MCPTool(
            name="integration_test_tool",
            description="Tool for integration testing",
            mcp_client=mock_client,
            mcp_server_config=mock_server_config,
            args_schema=args_schema,
        )

        # Create context-aware tool
        context_aware_tool = ContextAwareMCPTool(original_tool, sample_execution_context)

        # Execute the tool
        with patch('codemie.service.mcp.toolkit.asyncio.run') as mock_asyncio_run:
            mock_asyncio_run.return_value = mock_response

            result = context_aware_tool.execute(test_param="integration_test_value")

            # Verify execution was triggered
            mock_asyncio_run.assert_called_once()
            assert result == mock_response

    def test_context_propagates_through_tool_chain(self, sample_execution_context):
        """Test that execution context propagates correctly through the entire tool chain."""
        # Create mock client and server config
        mock_client = MagicMock(spec=MCPConnectClient)
        mock_server_config = MCPServerConfig(
            command="test_command", args=["arg1", "arg2"], env={"TEST_VAR": "test_value"}
        )

        # Setup client invoke_tool to verify context is passed
        async def mock_invoke_tool(server_config, tool_name, tool_args, execution_context=None):
            # Verify context is passed correctly
            assert execution_context is not None
            assert execution_context.user_id == "user-123"
            assert execution_context.assistant_id == "assistant-456"
            assert execution_context.project_name == "test-project"
            assert execution_context.workflow_execution_id == "workflow-789"

            return MCPToolInvocationResponse(
                isError=False, content=[MCPToolContentItem(type="text", text="Context verified")]
            )

        mock_client.invoke_tool = mock_invoke_tool

        from pydantic import create_model

        args_schema = create_model("TestSchema", test_param=(str, ...))

        # Create original tool
        original_tool = MCPTool(
            name="chain_test_tool",
            description="Tool for chain testing",
            mcp_client=mock_client,
            mcp_server_config=mock_server_config,
            args_schema=args_schema,
        )

        # Create context-aware tool
        context_aware_tool = ContextAwareMCPTool(original_tool, sample_execution_context)

        # Execute the tool (this will run the async chain)
        import asyncio

        result = asyncio.run(
            context_aware_tool._aexecute_with_context(
                execution_context=sample_execution_context, test_param="chain_test_value"
            )
        )

        # Verify execution completed successfully
        assert result.isError is False
        assert result.content[0].text == "Context verified"
