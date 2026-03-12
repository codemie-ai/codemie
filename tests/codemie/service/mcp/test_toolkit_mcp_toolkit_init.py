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
Tests for MCPToolkit class in toolkit.py.
"""

import unittest
from unittest.mock import MagicMock, patch

from codemie_tools.base.codemie_tool import CodeMieTool
from pydantic import BaseModel

from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import MCPServerConfig, MCPToolDefinition
from codemie.service.mcp.toolkit import MCPToolkit


class TestMCPToolkit(unittest.TestCase):
    """Test suite for MCPToolkit class."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create mock objects
        self.mock_client = MagicMock(spec=MCPConnectClient)

        self.mock_server_config = MagicMock(spec=MCPServerConfig)
        self.mock_server_config.command = "test_command"
        self.mock_server_config.url = None
        self.mock_server_config.args = ["arg1", "arg2"]
        self.mock_server_config.env = {"ENV_VAR": "value"}

        # Create mock tool definitions with JSON schema
        self.mock_tool_definition1 = MagicMock(spec=MCPToolDefinition)
        self.mock_tool_definition1.name = "tool1"
        self.mock_tool_definition1.description = "Tool 1 description"
        self.mock_tool_definition1.inputSchema = {
            "type": "object",
            "properties": {"param1": {"type": "string", "description": "String param"}},
        }

        self.mock_tool_definition2 = MagicMock(spec=MCPToolDefinition)
        self.mock_tool_definition2.name = "tool2"
        self.mock_tool_definition2.description = "Tool 2 description"
        self.mock_tool_definition2.inputSchema = {
            "type": "object",
            "properties": {
                "param1": {"type": "integer", "description": "Integer param"},
                "param2": {"type": "boolean", "description": "Boolean param", "default": False},
            },
        }

        self.tools_definitions = [self.mock_tool_definition1, self.mock_tool_definition2]

    def test_basic_initialization(self):
        """Test that the MCPToolkit can be properly initialized with valid parameters."""
        # Initialize toolkit
        toolkit = MCPToolkit(
            name="Test Toolkit",
            description="A test toolkit",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            tools_definitions=self.tools_definitions,
        )

        # Verify that the instance is created successfully
        self.assertIsInstance(toolkit, MCPToolkit)

        # Check that its attributes match input parameters
        self.assertEqual(toolkit.name, "Test Toolkit")
        self.assertEqual(toolkit.description, "A test toolkit")
        self.assertEqual(toolkit.mcp_client, self.mock_client)
        self.assertEqual(toolkit.mcp_server_config, self.mock_server_config)
        self.assertEqual(toolkit.tools_definitions, self.tools_definitions)

        # Verify that tools were created
        self.assertEqual(len(toolkit.tools), 2)
        self.assertEqual(toolkit.tools[0].name, "tool1")
        self.assertEqual(toolkit.tools[1].name, "tool2")

    def test_initialization_with_empty_tools_list(self):
        """Test toolkit initialization with empty tool definitions."""
        # Initialize toolkit with empty tools list
        toolkit = MCPToolkit(
            name="Empty Toolkit",
            description="A toolkit with no tools",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            tools_definitions=[],
        )

        # Verify that tools attribute is an empty list
        self.assertEqual(toolkit.tools, [])

    @patch("codemie.service.mcp.toolkit.MCPToolkit._create_tools")
    def test_initialization_error_handling(self, mock_create_tools):
        """Test that initialization handles errors in tool creation gracefully."""
        # Configure mock to raise an exception
        mock_create_tools.side_effect = Exception("Tool creation error")

        # Initialize toolkit with mocked _create_tools
        with patch("codemie.service.mcp.toolkit.logger"):
            with self.assertRaises(Exception) as context:
                MCPToolkit(
                    name="Error Toolkit",
                    description="A toolkit that will fail to initialize",
                    mcp_client=self.mock_client,
                    mcp_server_config=self.mock_server_config,
                    tools_definitions=self.tools_definitions,
                )

            # Verify appropriate error handling and logging
            self.assertEqual(str(context.exception), "Tool creation error")
            mock_create_tools.assert_called_once()

    def test_get_tools(self):
        """Test the get_tools method returns all tools."""
        # Initialize toolkit
        toolkit = MCPToolkit(
            name="Test Toolkit",
            description="A test toolkit",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            tools_definitions=self.tools_definitions,
        )

        # Call get_tools and verify result
        tools = toolkit.get_tools()
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0].name, "tool1")
        self.assertEqual(tools[1].name, "tool2")

        # Verify that the returned list is the same as the tools attribute
        self.assertEqual(tools, toolkit.tools)

    def test_get_tool(self):
        """Test the get_tool method returns a specific tool by name."""
        # Initialize toolkit
        toolkit = MCPToolkit(
            name="Test Toolkit",
            description="A test toolkit",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            tools_definitions=self.tools_definitions,
        )

        # Get a tool that exists
        tool = toolkit.get_tool("tool2")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, "tool2")

        # Get a tool that doesn't exist
        tool = toolkit.get_tool("non_existent_tool")
        self.assertIsNone(tool)

    def test_get_tools_ui_info(self):
        """Test the get_tools_ui_info method returns UI information for tools."""
        # Initialize toolkit
        toolkit = MCPToolkit(
            name="Test Toolkit",
            description="A test toolkit",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            tools_definitions=self.tools_definitions,
        )

        # Get UI info
        ui_info = toolkit.get_tools_ui_info()

        # Verify result
        self.assertEqual(len(ui_info), 2)
        self.assertEqual(ui_info[0]["name"], "tool1")
        self.assertEqual(ui_info[0]["description"], "Tool 1 description")
        self.assertEqual(ui_info[1]["name"], "tool2")
        self.assertEqual(ui_info[1]["description"], "Tool 2 description")

        # Check that args_schema is included
        self.assertIn("args_schema", ui_info[0])
        self.assertIn("args_schema", ui_info[1])

    def test_get_toolkit_class_method(self):
        """Test that the get_toolkit class method raises the expected exception."""
        with self.assertRaises(RuntimeError) as context:
            MCPToolkit.get_toolkit()

        # Verify error message
        self.assertIn("MCPToolkit instances cannot be created directly", str(context.exception))
        self.assertIn("MCPToolkitService", str(context.exception))

    @patch("codemie.service.mcp.toolkit.json_schema_to_model")
    def test_create_args_schema(self, mock_json_schema_to_model):
        """Test the _create_args_schema method creates a valid Pydantic model."""
        # Mock the json_schema_to_model function
        mock_model = MagicMock(spec=BaseModel)
        mock_json_schema_to_model.return_value = mock_model

        # Using a test instance of MCPToolkit to avoid initialization side effects
        # that would call _create_tools which calls _create_args_schema multiple times
        mock_json_schema_to_model.reset_mock()

        # Create a manual instance without __init__ using dict to set attributes
        toolkit = MCPToolkit.__new__(MCPToolkit)
        # Set required attributes manually for the test
        toolkit.__dict__["mcp_client"] = self.mock_client
        toolkit.__dict__["mcp_server_config"] = self.mock_server_config
        toolkit.__dict__["tools_definitions"] = self.tools_definitions

        # Call _create_args_schema directly
        args_schema = toolkit._create_args_schema(self.mock_tool_definition1)

        # Verify json_schema_to_model was called with the right parameters
        mock_json_schema_to_model.assert_called_once_with(self.mock_tool_definition1.inputSchema)

        # Verify the result is what was returned by json_schema_to_model
        self.assertEqual(args_schema, mock_model)

    @patch("codemie.service.mcp.toolkit.MCPTool")
    def test_create_tools(self, mock_mcp_tool_class):
        """Test the _create_tools method."""
        # Configure mock to return different mock objects for each invocation
        mock_tool1 = MagicMock(spec=CodeMieTool)
        mock_tool1.name = "tool1"
        mock_tool2 = MagicMock(spec=CodeMieTool)
        mock_tool2.name = "tool2"
        mock_mcp_tool_class.side_effect = [mock_tool1, mock_tool2]

        # Initialize toolkit with our mocked MCPTool class
        toolkit = MCPToolkit(
            name="Test Toolkit",
            description="A test toolkit",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            tools_definitions=self.tools_definitions,
        )

        # Verify tools were created correctly
        self.assertEqual(len(toolkit.tools), 2)
        self.assertEqual(toolkit.tools[0], mock_tool1)
        self.assertEqual(toolkit.tools[1], mock_tool2)

        # Verify MCPTool constructor was called with correct parameters
        mock_mcp_tool_class.assert_any_call(
            name="tool1",
            description="Tool 1 description",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            args_schema=unittest.mock.ANY,  # Can't easily compare the dynamically created model
        )
        mock_mcp_tool_class.assert_any_call(
            name="tool2",
            description="Tool 2 description",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            args_schema=unittest.mock.ANY,  # Can't easily compare the dynamically created model
        )

    @patch("codemie.service.mcp.toolkit.logger")
    def test_create_tools_handles_errors(self, mock_logger):
        """Test that _create_tools handles errors gracefully."""
        # Create a tool definition that will cause an error
        bad_tool_def = MagicMock(spec=MCPToolDefinition)
        bad_tool_def.name = "bad_tool"
        bad_tool_def.description = "A tool that will cause an error"
        # Set inputSchema to None to cause an AttributeError
        bad_tool_def.inputSchema = None

        # Mock the MCPTool class to control its behavior
        with patch("codemie.service.mcp.toolkit.MCPTool") as mock_mcp_tool:
            # Configure mock to return tool instances for valid tools and raise exception for bad tool
            mock_tool1 = MagicMock(spec=CodeMieTool)
            mock_tool1.name = "tool1"
            mock_tool2 = MagicMock(spec=CodeMieTool)
            mock_tool2.name = "tool2"

            # Set up side effect to succeed for valid tools and fail for bad tool
            def tool_side_effect(*args, **kwargs):
                if kwargs.get("name") == "bad_tool":
                    raise ValueError("Bad tool definition")
                elif kwargs.get("name") == "tool1":
                    return mock_tool1
                elif kwargs.get("name") == "tool2":
                    return mock_tool2

            mock_mcp_tool.side_effect = tool_side_effect

            # Initialize toolkit with the problematic tool definition
            toolkit = MCPToolkit(
                name="Error Handling Toolkit",
                description="A toolkit that tests error handling",
                mcp_client=self.mock_client,
                mcp_server_config=self.mock_server_config,
                tools_definitions=[self.mock_tool_definition1, bad_tool_def, self.mock_tool_definition2],
            )

            # Verify that only the valid tools were created
            self.assertEqual(len(toolkit.tools), 2)
            self.assertEqual(toolkit.tools[0].name, "tool1")
            self.assertEqual(toolkit.tools[1].name, "tool2")

            # Verify that an error was logged - check for the tool name and error message
            assert any(
                "Failed to create tool bad_tool" in str(call) and "Bad tool definition" in str(call)
                for call in mock_logger.error.call_args_list
            ), f"Expected error log not found. Actual calls: {mock_logger.error.call_args_list}"
