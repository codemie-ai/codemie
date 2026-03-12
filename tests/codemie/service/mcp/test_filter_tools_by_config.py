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
Tests for MCPToolkitService._filter_tools_by_config functionality.
"""

from unittest.mock import MagicMock


class TestFilterToolsByConfig:
    """Test cases for MCPToolkitService._filter_tools_by_config method."""

    def setup_method(self):
        """Set up test fixtures for each test method."""
        # Import here to avoid circular import at module level
        from codemie.service.mcp.toolkit import MCPTool

        # Create mock tools with different names
        self.tool1 = MagicMock(spec=MCPTool)
        self.tool1.name = "search"
        self.tool1.description = "Search tool"

        self.tool2 = MagicMock(spec=MCPTool)
        self.tool2.name = "write_file"
        self.tool2.description = "Write file tool"

        self.tool3 = MagicMock(spec=MCPTool)
        self.tool3.name = "read_file"
        self.tool3.description = "Read file tool"

        self.all_tools = [self.tool1, self.tool2, self.tool3]

    def test_no_filtering_when_tools_is_none(self):
        """
        Test that all tools are returned when tools field is None.

        When neither mcp_server.tools nor mcp_server.config.tools is set,
        all tools should be returned without filtering.
        """
        # Import here to avoid circular import
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with no tools filter
        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=None,  # No filtering
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify all tools are returned
        assert len(result) == 3
        assert result == self.all_tools

    def test_no_filtering_when_tools_is_empty_list(self):
        """
        Test that all tools are returned when tools field is an empty list.

        An empty list should be treated as "no filtering" and return all tools.
        """
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with empty tools list
        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=[],  # Empty list - no filtering
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify all tools are returned
        assert len(result) == 3
        assert result == self.all_tools

    def test_filter_with_single_tool(self):
        """
        Test filtering with a single tool name specified.

        Only the tool with the specified name should be returned.
        """
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with single tool filter
        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=["search"],
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify only the search tool is returned
        assert len(result) == 1
        assert result[0] == self.tool1
        assert result[0].name == "search"

    def test_filter_with_multiple_tools(self):
        """
        Test filtering with multiple tool names specified.

        Only the tools with the specified names should be returned.
        """
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with multiple tool filters
        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=["search", "read_file"],
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify only the specified tools are returned
        assert len(result) == 2
        tool_names = {tool.name for tool in result}
        assert tool_names == {"search", "read_file"}
        assert self.tool1 in result
        assert self.tool3 in result
        assert self.tool2 not in result

    def test_filter_with_nonexistent_tools(self):
        """
        Test filtering with tool names that don't exist.

        When specified tool names don't match any available tools,
        an empty list should be returned.
        """
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with non-existent tool names
        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=["nonexistent_tool", "another_missing_tool"],
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify no tools are returned
        assert len(result) == 0
        assert result == []

    def test_filter_with_mixed_valid_and_invalid_tools(self):
        """
        Test filtering with a mix of valid and invalid tool names.

        Valid tools should be returned, invalid tools should be ignored.
        """
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with mix of valid and invalid tool names
        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=["search", "nonexistent_tool", "write_file"],
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify only valid tools are returned
        assert len(result) == 2
        tool_names = {tool.name for tool in result}
        assert tool_names == {"search", "write_file"}

    def test_filter_from_config_tools_field(self):
        """
        Test filtering using the tools field from mcp_server.config.

        When mcp_server.tools is None but mcp_server.config.tools is set,
        the config.tools should be used for filtering.
        """
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.models import MCPServerConfig
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with config that has tools filter
        server_config = MCPServerConfig(
            command="test-command",
            tools=["read_file", "write_file"],
        )

        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=None,  # Not set at this level
            config=server_config,
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify only tools from config.tools are returned
        assert len(result) == 2
        tool_names = {tool.name for tool in result}
        assert tool_names == {"read_file", "write_file"}

    def test_mcp_server_tools_takes_priority_over_config_tools(self):
        """
        Test that mcp_server.tools takes priority over mcp_server.config.tools.

        When both mcp_server.tools and mcp_server.config.tools are set,
        mcp_server.tools should be used for filtering.
        """
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.models import MCPServerConfig
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with both tools fields set
        server_config = MCPServerConfig(
            command="test-command",
            tools=["read_file"],  # This should be ignored
        )

        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=["search", "write_file"],  # This should take priority
            config=server_config,
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify only tools from mcp_server.tools are returned (not from config.tools)
        assert len(result) == 2
        tool_names = {tool.name for tool in result}
        assert tool_names == {"search", "write_file"}
        # read_file should not be included (it's only in config.tools)
        assert "read_file" not in tool_names

    def test_filter_with_no_config(self):
        """
        Test filtering when mcp_server.config is None.

        Should handle the case gracefully and use mcp_server.tools if available.
        """
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with no config
        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=["search"],
            config=None,  # No config
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify only the specified tool is returned
        assert len(result) == 1
        assert result[0].name == "search"

    def test_filter_preserves_tool_order(self):
        """
        Test that filtering preserves the original order of tools.

        The filtered tools should maintain their original order from the input list.
        """
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with tools in different order
        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=["read_file", "search"],  # Different order than original
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify tools are in original order (search before read_file in all_tools)
        assert len(result) == 2
        assert result[0].name == "search"  # Comes first in all_tools
        assert result[1].name == "read_file"  # Comes second in all_tools

    def test_filter_with_empty_tools_list_returns_all(self):
        """
        Test that an empty tools list is treated as "use all tools".

        This is consistent with the specification that empty or None means use all.
        """
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with explicitly empty list
        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=[],
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify all tools are returned
        assert len(result) == 3
        assert result == self.all_tools

    def test_filter_all_tools_specified(self):
        """
        Test filtering when all available tools are specified.

        Should return all tools (same as no filtering).
        """
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.service.mcp.toolkit_service import MCPToolkitService

        # Create MCP server with all tool names
        mcp_server = MCPServerDetails(
            name="TestServer",
            description="Test MCP server",
            enabled=True,
            tools=["search", "write_file", "read_file"],
        )

        # Call the filter method
        result = MCPToolkitService._filter_tools_by_config(self.all_tools, mcp_server)

        # Verify all tools are returned
        assert len(result) == 3
        tool_names = {tool.name for tool in result}
        assert tool_names == {"search", "write_file", "read_file"}
