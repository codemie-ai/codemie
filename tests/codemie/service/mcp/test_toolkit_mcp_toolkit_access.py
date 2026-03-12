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
Tests for MCPToolkit class focusing on tool access methods.

This module contains test cases for the tool access methods of the MCPToolkit class,
including get_tools, get_tool, and get_tools_ui_info.
"""

import unittest
from unittest.mock import MagicMock

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import (
    MCPServerConfig,
    MCPToolDefinition,
)
from codemie.service.mcp.toolkit import MCPToolkit


class TestMCPToolkitAccess(unittest.TestCase):
    """Test cases for tool access methods in MCPToolkit."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create mock objects
        self.mock_client = MagicMock(spec=MCPConnectClient)

        # Create server config
        self.mock_server_config = MCPServerConfig(
            command="test_command", args=["arg1", "arg2"], env={"ENV_VAR": "value"}
        )

        # Create tool definitions with different schema complexity
        # Tool 1 with simple schema
        self.tool_def1 = MCPToolDefinition(
            name="simple_tool",
            description="A simple tool",
            inputSchema={
                "type": "object",
                "properties": {
                    "text_param": {
                        "type": "string",
                        "description": "Text parameter",
                    },
                },
            },
        )

        # Tool 2 with more complex schema
        self.tool_def2 = MCPToolDefinition(
            name="complex_tool",
            description="A more complex tool",
            inputSchema={
                "type": "object",
                "properties": {
                    "text_param": {
                        "type": "string",
                        "description": "Text parameter",
                    },
                    "number_param": {
                        "type": "integer",
                        "description": "Number parameter",
                        "default": 42,
                    },
                    "boolean_param": {
                        "type": "boolean",
                        "description": "Boolean parameter",
                    },
                    "array_param": {"type": "array", "description": "Array parameter", "items": {"type": "string"}},
                },
            },
        )

        # Create toolkit with the tool definitions
        self.toolkit = MCPToolkit(
            name="Test Toolkit",
            description="A test toolkit",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            tools_definitions=[self.tool_def1, self.tool_def2],
        )

    def test_get_tools(self):
        """
        Test retrieval of all tools from the toolkit.

        Description: Test retrieval of all tools from the toolkit.
        Setup: Create a toolkit with known tools.
        Action: Call get_tools().
        Assertions: Verify the returned list matches the expected tools.
        """
        # Get tools from the toolkit
        tools = self.toolkit.get_tools()

        # Assertions
        self.assertIsInstance(tools, list)
        self.assertEqual(len(tools), 2, "Should return 2 tools")

        # Verify tool names
        tool_names = [tool.name for tool in tools]
        expected_names = ["simple_tool", "complex_tool"]
        self.assertEqual(tool_names, expected_names, "Tool names should match expected names")

        # Verify that each item is a CodeMieTool
        for tool in tools:
            self.assertIsInstance(tool, CodeMieTool, "Each item should be a CodeMieTool")

        # Verify that the list is the same as the tools attribute
        self.assertIs(tools, self.toolkit.tools, "get_tools should return the tools attribute")

    def test_get_tool_existing(self):
        """
        Test retrieval of a specific tool by name.

        Description: Test retrieval of a specific tool by name.
        Setup: Create a toolkit with known tools.
        Action: Call get_tool("known_tool_name").
        Assertions: Verify the returned tool is the expected one.
        """
        # Get existing tool by name
        tool = self.toolkit.get_tool("simple_tool")

        # Assertions
        self.assertIsNotNone(tool, "Should return a tool")
        self.assertIsInstance(tool, CodeMieTool, "Should return a CodeMieTool")
        self.assertEqual(tool.name, "simple_tool", "Tool name should match")
        self.assertEqual(tool.description, "A simple tool", "Tool description should match")

        # Verify that the tool is part of the toolkit's tools
        self.assertIn(tool, self.toolkit.tools, "Returned tool should be in the toolkit's tools")

    def test_get_tool_non_existing(self):
        """
        Test retrieval of a non-existent tool.

        Description: Test retrieval of a non-existent tool.
        Setup: Create a toolkit with known tools.
        Action: Call get_tool("unknown_tool_name").
        Assertions: Verify that None is returned.
        """
        # Get non-existing tool by name
        tool = self.toolkit.get_tool("unknown_tool")

        # Assertions
        self.assertIsNone(tool, "Should return None for unknown tool name")

    def test_get_tools_ui_info(self):
        """
        Test retrieval of UI information for tools.

        Description: Test retrieval of UI information for tools.
        Setup: Create a toolkit with tools having various schemas.
        Action: Call get_tools_ui_info().
        Assertions:
        - Verify the returned list has the same length as the tools list
        - Verify each item has the expected structure (name, description, args_schema)
        - Verify args_schema is correctly derived from each tool's schema
        """
        # Get UI info
        ui_info = self.toolkit.get_tools_ui_info()

        # Assertions
        self.assertIsInstance(ui_info, list)
        self.assertEqual(len(ui_info), 2, "Should return info for 2 tools")

        # Verify structure
        for info in ui_info:
            self.assertIsInstance(info, dict)
            self.assertIn("name", info)
            self.assertIn("description", info)
            self.assertIn("args_schema", info)

        # Verify simple tool info
        simple_tool_info = next(info for info in ui_info if info["name"] == "simple_tool")
        self.assertEqual(simple_tool_info["description"], "A simple tool")
        self.assertIsInstance(simple_tool_info["args_schema"], dict)
        self.assertIn("text_param", simple_tool_info["args_schema"])

        # Verify complex tool info
        complex_tool_info = next(info for info in ui_info if info["name"] == "complex_tool")
        self.assertEqual(complex_tool_info["description"], "A more complex tool")
        self.assertIsInstance(complex_tool_info["args_schema"], dict)
        self.assertIn("text_param", complex_tool_info["args_schema"])
        self.assertIn("number_param", complex_tool_info["args_schema"])
        self.assertIn("boolean_param", complex_tool_info["args_schema"])
        self.assertIn("array_param", complex_tool_info["args_schema"])
