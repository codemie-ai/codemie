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
Test cases for MCPToolkit class focusing on tool creation methods.
"""

import unittest
from unittest.mock import MagicMock, patch

from pydantic import BaseModel
from typing import Optional

from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import (
    MCPServerConfig,
    MCPToolDefinition,
)
from codemie.service.mcp.toolkit import MCPToolkit, MCPTool


class TestMCPToolkitCreation(unittest.TestCase):
    """Test cases for tool creation methods in MCPToolkit."""

    def setUp(self):
        """Set up test fixtures."""
        # Properly mock the MCPConnectClient
        self.mcp_client = MagicMock(spec=MCPConnectClient)

        self.mcp_server_config = MCPServerConfig(
            command="test_command", args=["arg1", "arg2"], env={"ENV_VAR": "value"}
        )

        # Create a valid tool definition with JSON schema
        self.valid_tool_def = MCPToolDefinition(
            name="test_tool",
            description="Test tool description",
            inputSchema={
                "type": "object",
                "properties": {
                    "param1": {
                        "type": "string",
                        "description": "String parameter",
                    },
                    "param2": {
                        "type": "integer",
                        "description": "Integer parameter",
                        "default": 42,
                    },
                },
            },
        )

    @patch('codemie.service.mcp.toolkit.MCPToolkit._create_tools')
    def test_create_tools(self, mock_create_tools):
        """Test that _create_tools properly creates MCPTool objects based on definitions."""
        # Setup
        tool1 = MagicMock(spec=MCPTool)
        tool2 = MagicMock(spec=MCPTool)
        mock_create_tools.return_value = [tool1, tool2]

        # Create toolkit
        toolkit = MCPToolkit(
            name="Test Toolkit",
            description="Test toolkit description",
            mcp_client=self.mcp_client,
            mcp_server_config=self.mcp_server_config,
            tools_definitions=[self.valid_tool_def, self.valid_tool_def],
        )

        # Assertions
        self.assertEqual(len(toolkit.tools), 2, "Should create two tools")
        mock_create_tools.assert_called_once()

    @patch('codemie.service.mcp.toolkit.logger')
    @patch('codemie.service.mcp.toolkit.MCPTool')
    def test_error_handling_in_tool_creation(self, mock_mcp_tool, mock_logger):
        """Test that _create_tools properly handles errors in individual tool creation."""
        # Setup - Mock MCPTool constructor to succeed for first tool and fail for second
        mock_mcp_tool.side_effect = [
            MagicMock(spec=MCPTool),  # First call succeeds
            Exception("Tool creation error"),  # Second call fails
        ]

        # Create a second tool definition (will fail)
        invalid_tool_def = MCPToolDefinition(
            name="invalid_tool",
            description="Invalid tool",
            inputSchema={"type": "object", "properties": {}},
        )

        # Use the actual _create_tools method with patched json_schema_to_model
        with patch('codemie.service.mcp.toolkit.json_schema_to_model') as mock_create_schema:
            mock_create_schema.return_value = MagicMock(spec=BaseModel)

            toolkit = MCPToolkit(
                name="Test Toolkit",
                description="Test toolkit description",
                mcp_client=self.mcp_client,
                mcp_server_config=self.mcp_server_config,
                tools_definitions=[self.valid_tool_def, invalid_tool_def],
            )

            # Should call json_schema_to_model at least once
            mock_create_schema.assert_called()

            # Assert that the error was logged - check that it was called with a message containing the tool name and error
            assert any(
                "Failed to create tool invalid_tool" in str(call) and "Tool creation error" in str(call)
                for call in mock_logger.error.call_args_list
            ), f"Expected error log not found. Actual calls: {mock_logger.error.call_args_list}"

            # Assert that one tool was still created despite the error
            self.assertEqual(len(toolkit.tools), 1)

    def test_create_args_schema(self):
        """Test schema creation from tool definition input schema."""
        # First, create the toolkit with a patched _create_tools to avoid actual tool creation
        with patch.object(MCPToolkit, '_create_tools', return_value=[]):
            toolkit = MCPToolkit(
                name="Test Toolkit",
                description="Test toolkit description",
                mcp_client=self.mcp_client,
                mcp_server_config=self.mcp_server_config,
                tools_definitions=[],
            )

            # Create a complex schema for testing
            complex_schema = MCPToolDefinition(
                name="complex_tool",
                description="Test complex schema",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "string_param": {
                            "type": "string",
                            "description": "String parameter",
                        },
                        "int_param": {
                            "type": "integer",
                            "description": "Integer parameter",
                            "default": 42,
                        },
                        "bool_param": {
                            "type": "boolean",
                            "description": "Boolean parameter",
                        },
                        "array_param": {
                            "type": "array",
                            "description": "Array parameter",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["string_param", "bool_param", "array_param"],
                },
            )

            # Mock json_schema_to_model to return a model class with expected properties
            with patch('codemie.service.mcp.toolkit.json_schema_to_model') as mock_json_schema_to_model:
                # Create a model class with expected annotations for testing
                class TestModel(BaseModel):
                    string_param: str
                    int_param: Optional[int] = 42
                    bool_param: bool
                    array_param: list[str]

                mock_json_schema_to_model.return_value = TestModel

                # Call the method
                result = toolkit._create_args_schema(complex_schema)

                # Verify json_schema_to_model was called with the correct schema
                mock_json_schema_to_model.assert_called_once_with(complex_schema.inputSchema)

                # Assertions
                self.assertTrue(issubclass(result, BaseModel), "Result should be a Pydantic model")

                # Check field types
                annotations = result.__annotations__
                self.assertEqual(annotations["string_param"], str)
                self.assertEqual(annotations["int_param"], Optional[int])
                self.assertEqual(annotations["bool_param"], bool)
                self.assertEqual(annotations["array_param"], list[str])

                # Create an instance with required values for non-optional fields
                model_instance = result(string_param="test string", bool_param=False, array_param=["test"])
                self.assertEqual(model_instance.int_param, 42)

    def test_get_tools(self):
        """Test that get_tools returns the tools list."""
        # First, create some mock tools
        tool1 = MagicMock(spec=MCPTool)
        tool2 = MagicMock(spec=MCPTool)

        # Create the toolkit with patched _create_tools
        with patch.object(MCPToolkit, '_create_tools', return_value=[tool1, tool2]):
            toolkit = MCPToolkit(
                name="Test Toolkit",
                description="Test toolkit description",
                mcp_client=self.mcp_client,
                mcp_server_config=self.mcp_server_config,
                tools_definitions=[self.valid_tool_def, self.valid_tool_def],
            )

            # Get tools and verify
            tools = toolkit.get_tools()
            self.assertEqual(len(tools), 2)
            self.assertIs(tools[0], tool1)
            self.assertIs(tools[1], tool2)

    def test_get_tool(self):
        """Test that get_tool returns the correct tool by name."""
        # Create mock tools with specific names
        tool1 = MagicMock(spec=MCPTool)
        tool1.name = "tool1"
        tool2 = MagicMock(spec=MCPTool)
        tool2.name = "tool2"

        # Create the toolkit with patched _create_tools
        with patch.object(MCPToolkit, '_create_tools', return_value=[tool1, tool2]):
            toolkit = MCPToolkit(
                name="Test Toolkit",
                description="Test toolkit description",
                mcp_client=self.mcp_client,
                mcp_server_config=self.mcp_server_config,
                tools_definitions=[self.valid_tool_def, self.valid_tool_def],
            )

            # Get tool by name and verify
            found_tool = toolkit.get_tool("tool1")
            self.assertIs(found_tool, tool1)

            # Try getting a non-existent tool
            not_found_tool = toolkit.get_tool("nonexistent")
            self.assertIsNone(not_found_tool)

    def test_get_tools_ui_info(self):
        """Test that get_tools_ui_info returns the expected UI info for each tool."""
        # Create mock tools with specific attributes
        tool1 = MagicMock(spec=MCPTool)
        tool1.name = "tool1"
        tool1.description = "Tool 1 Description"

        # Create a real args_schema for testing
        class Tool1Schema(BaseModel):
            param1: str
            param2: int

        tool1.args_schema = Tool1Schema

        tool2 = MagicMock(spec=MCPTool)
        tool2.name = "tool2"
        tool2.description = "Tool 2 Description"
        tool2.args_schema = None

        # Create the toolkit with patched _create_tools
        with patch.object(MCPToolkit, '_create_tools', return_value=[tool1, tool2]):
            toolkit = MCPToolkit(
                name="Test Toolkit",
                description="Test toolkit description",
                mcp_client=self.mcp_client,
                mcp_server_config=self.mcp_server_config,
                tools_definitions=[self.valid_tool_def, self.valid_tool_def],
            )

            # Get UI info and verify
            ui_info = toolkit.get_tools_ui_info()
            self.assertEqual(len(ui_info), 2)

            # Check first tool info
            self.assertEqual(ui_info[0]["name"], "tool1")
            self.assertEqual(ui_info[0]["description"], "Tool 1 Description")
            self.assertEqual(len(ui_info[0]["args_schema"]), 2)
            self.assertEqual(ui_info[0]["args_schema"]["param1"], str)
            self.assertEqual(ui_info[0]["args_schema"]["param2"], int)

            # Check second tool info
            self.assertEqual(ui_info[1]["name"], "tool2")
            self.assertEqual(ui_info[1]["description"], "Tool 2 Description")
            self.assertEqual(ui_info[1]["args_schema"], {})

    def test_get_toolkit_class_method(self):
        """Test that the get_toolkit class method raises RuntimeError."""
        with self.assertRaises(RuntimeError):
            MCPToolkit.get_toolkit()
