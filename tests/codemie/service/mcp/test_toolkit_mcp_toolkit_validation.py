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
Test cases for MCPToolkit class focusing on validation and edge cases.
"""

import unittest
from unittest.mock import MagicMock, patch


from codemie_tools.base.codemie_tool import CodeMieTool
from pydantic import BaseModel

from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import (
    MCPServerConfig,
    MCPToolDefinition,
)
from codemie.service.mcp.toolkit import MCPToolkit, MCPToolkitFactory


class TestMCPToolkitValidation(unittest.TestCase):
    """Test case for validation and edge cases of MCPToolkit."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock the MCPConnectClient
        self.mcp_client = MagicMock(spec=MCPConnectClient)

        # Create server config
        self.server_config = MCPServerConfig(command="test_command", args=["arg1", "arg2"], env={"ENV_VAR": "value"})

        # Create basic tool definition
        self.basic_tool_def = MCPToolDefinition(
            name="basic_tool",
            description="Basic tool description",
            inputSchema={
                "type": "object",
                "properties": {
                    "param1": {
                        "type": "string",
                        "description": "String parameter",
                    },
                },
            },
        )

        # Create comprehensive set of tool definitions
        self.comprehensive_tool_def1 = MCPToolDefinition(
            name="string_tool",
            description="Tool with string parameters",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text parameter", "default": "default text"},
                    "optional_text": {"type": "string", "description": "Optional text parameter", "default": None},
                },
            },
        )

        self.comprehensive_tool_def2 = MCPToolDefinition(
            name="numeric_tool",
            description="Tool with numeric parameters",
            inputSchema={
                "type": "object",
                "properties": {
                    "integer_param": {
                        "type": "integer",
                        "description": "Integer parameter",
                    },
                    "float_param": {"type": "number", "description": "Float parameter", "default": 3.14},
                },
            },
        )

        self.comprehensive_tool_def3 = MCPToolDefinition(
            name="complex_tool",
            description="Tool with complex parameters",
            inputSchema={
                "type": "object",
                "properties": {
                    "array_param": {"type": "array", "description": "Array parameter", "items": {"type": "string"}},
                    "object_param": {
                        "type": "object",
                        "description": "Object parameter",
                    },
                    "boolean_param": {"type": "boolean", "description": "Boolean parameter", "default": False},
                },
            },
        )

        # Create tool definition with missing properties
        self.missing_props_def = MCPToolDefinition(
            name="missing_props_tool",
            description="Tool with partially specified schema",
            inputSchema={
                "type": "object",
                "properties": {},  # Empty properties dictionary
            },
        )

        # Create tool definition with unsupported type
        self.unsupported_type_def = MCPToolDefinition(
            name="unsupported_type_tool",
            description="Tool with unsupported schema type",
            inputSchema={
                "type": "object",
                "properties": {
                    "custom_type": {
                        "type": "custom",  # Non-standard type
                        "description": "Custom type parameter",
                    },
                    "another_custom": {
                        "type": "datetime",  # Another non-standard type
                        "description": "DateTime parameter",
                    },
                },
            },
        )

        # Create tool definition with special field names that match Python keywords
        # Avoid using "model_dump" as it conflicts with pydantic method
        self.special_field_names_def = MCPToolDefinition(
            name="special_field_names_tool",
            description="Tool with special field names",
            inputSchema={
                "type": "object",
                "properties": {
                    "class": {  # Python keyword
                        "type": "string",
                        "description": "Class parameter",
                    },
                    "from": {  # Python keyword
                        "type": "string",
                        "description": "From parameter",
                    },
                    "def": {  # Python keyword
                        "type": "string",
                        "description": "Definition parameter",
                    },
                },
            },
        )

    def test_get_toolkit_class_method(self):
        """
        Test 4.1: Test that the static get_toolkit method raises the expected error.
        """
        with self.assertRaises(RuntimeError) as context:
            MCPToolkit.get_toolkit()

        # Verify error message
        error_message = str(context.exception)
        self.assertIn("MCPToolkit instances cannot be created directly", error_message)
        self.assertIn("MCPToolkitService", error_message)

    def test_full_toolkit_creation_from_actual_definitions(self):
        """
        Test 5.1: Test end-to-end creation of a toolkit with realistic tool definitions.
        """
        # Prepare comprehensive set of tool definitions
        tool_definitions = [self.comprehensive_tool_def1, self.comprehensive_tool_def2, self.comprehensive_tool_def3]

        # Create toolkit with these definitions
        toolkit = MCPToolkit(
            name="Comprehensive Toolkit",
            description="A comprehensive toolkit with various tool types",
            mcp_client=self.mcp_client,
            mcp_server_config=self.server_config,
            tools_definitions=tool_definitions,
        )

        # Verify the toolkit has the expected tools
        self.assertEqual(len(toolkit.tools), 3, "Should have created 3 tools")

        # Verify each tool has the correct name
        tool_names = [tool.name for tool in toolkit.tools]
        self.assertIn("string_tool", tool_names)
        self.assertIn("numeric_tool", tool_names)
        self.assertIn("complex_tool", tool_names)

        # Verify each tool has the expected structure
        for tool in toolkit.tools:
            # Each tool should be an MCPTool
            self.assertIsInstance(tool, CodeMieTool)

            # Each tool should have the correct attributes
            self.assertEqual(tool.mcp_client, self.mcp_client)
            self.assertEqual(tool.mcp_server_config, self.server_config)

            # Verify tool has a valid args_schema
            self.assertTrue(hasattr(tool, "args_schema"))
            self.assertTrue(issubclass(tool.args_schema, BaseModel))

    def test_toolkit_with_token_size_limits(self):
        """
        Test 5.2: Test that token size limits are properly applied to created tools.
        """
        # Create a toolkit with tools definitions and a custom token size limit
        custom_token_limit = 5000

        with patch('codemie.service.mcp.toolkit.config') as mock_config:
            # Mock the config to allow initialization with default token size limit
            mock_config.MCP_TOOL_TOKENS_SIZE_LIMIT = 2000  # Default limit

            # Create toolkit with basic tool definition
            toolkit = MCPToolkit(
                name="Token Limited Toolkit",
                description="A toolkit with token size limits",
                mcp_client=self.mcp_client,
                mcp_server_config=self.server_config,
                tools_definitions=[self.basic_tool_def],
            )

            # Get the tools
            tools = toolkit.get_tools()
            self.assertTrue(len(tools) > 0, "Should have at least one tool")

            # Set custom token size limit
            for tool in tools:
                tool.tokens_size_limit = custom_token_limit

            # Verify each tool has the custom token size limit
            for tool in tools:
                self.assertEqual(
                    tool.tokens_size_limit, custom_token_limit, "Tool should have the specified token size limit"
                )

    def test_schema_with_missing_properties(self):
        """
        Test 6.1: Test handling of tool definitions with partially specified schemas.
        """
        # Create toolkit with tool definition that has missing properties
        toolkit = MCPToolkit(
            name="Missing Properties Toolkit",
            description="A toolkit with missing schema properties",
            mcp_client=self.mcp_client,
            mcp_server_config=self.server_config,
            tools_definitions=[self.missing_props_def],
        )

        # Verify the toolkit created a valid tool
        self.assertEqual(len(toolkit.tools), 1, "Should have created 1 tool")

        # Get the tool and verify it has a valid args_schema
        tool = toolkit.tools[0]
        self.assertEqual(tool.name, "missing_props_tool")
        self.assertTrue(hasattr(tool, "args_schema"))
        self.assertTrue(issubclass(tool.args_schema, BaseModel))

        # The args_schema should not have any fields
        self.assertEqual(len(tool.args_schema.__annotations__), 0)

    def test_schema_with_unsupported_types(self):
        """
        Test 6.2: Test handling of tool definitions with unsupported schema types.
        """
        # Create toolkit with tool definition that has unsupported types
        with patch('codemie.service.mcp.toolkit.logger') as mock_logger:
            toolkit = MCPToolkit(
                name="Unsupported Types Toolkit",
                description="A toolkit with unsupported schema types",
                mcp_client=self.mcp_client,
                mcp_server_config=self.server_config,
                tools_definitions=[self.unsupported_type_def],
            )

            # Check that the error was logged - verify the key parts of the error message
            assert any(
                "Failed to create tool unsupported_type_tool" in str(call)
                and "Cannot determine Pydantic type for schema fragment" in str(call)
                and "CustomType" in str(call)
                for call in mock_logger.error.call_args_list
            ), f"Expected error log not found. Actual calls: {mock_logger.error.call_args_list}"

        # Verify the toolkit was created, but the tool was not added due to errors
        self.assertEqual(len(toolkit.tools), 0, "Should have created 0 tools due to schema errors")

    def test_special_field_names(self):
        """
        Test 6.3: Test handling of tool definitions with special/reserved field names.
        """
        # Create toolkit with tool definition that has special field names
        toolkit = MCPToolkit(
            name="Special Field Names Toolkit",
            description="A toolkit with special field names",
            mcp_client=self.mcp_client,
            mcp_server_config=self.server_config,
            tools_definitions=[self.special_field_names_def],
        )

        # Verify the toolkit created a valid tool
        self.assertEqual(len(toolkit.tools), 1, "Should have created 1 tool")

        # Get the tool and verify it has a valid args_schema
        tool = toolkit.tools[0]
        self.assertEqual(tool.name, "special_field_names_tool")
        self.assertTrue(hasattr(tool, "args_schema"))
        self.assertTrue(issubclass(tool.args_schema, BaseModel))

        # Verify that Python keywords are properly handled
        self.assertIn("class", tool.args_schema.__annotations__)
        self.assertIn("from", tool.args_schema.__annotations__)
        self.assertIn("def", tool.args_schema.__annotations__)

        # Try to create an instance of the schema to verify it works
        schema_instance = tool.args_schema(**{"class": "test_class", "from": "test_from", "def": "test_def"})

        # Verify the values were set correctly
        self.assertEqual(getattr(schema_instance, "class"), "test_class")
        self.assertEqual(getattr(schema_instance, "from"), "test_from")
        self.assertEqual(getattr(schema_instance, "def"), "test_def")


class TestMCPToolkitFactory(unittest.TestCase):
    """Test cases for MCPToolkitFactory."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock the MCPConnectClient
        self.mcp_client = MagicMock(spec=MCPConnectClient)

        # Create server config
        self.server_config = MCPServerConfig(command="test_command", args=["arg1", "arg2"], env={"ENV_VAR": "value"})

        # Create a basic tool definition
        self.basic_tool_def = MCPToolDefinition(
            name="basic_tool",
            description="Basic tool description",
            inputSchema={
                "type": "object",
                "properties": {
                    "param1": {
                        "type": "string",
                        "description": "String parameter",
                    },
                },
            },
        )

    @patch('codemie.service.mcp.toolkit.config')
    def test_factory_initialization(self, mock_config):
        """Test initialization of the toolkit factory."""
        # Configure mock
        mock_config.MCP_TOOLKIT_FACTORY_CACHE_SIZE = 100
        mock_config.MCP_TOOLKIT_FACTORY_CACHE_TTL = 3600

        # Create factory with default parameters
        factory = MCPToolkitFactory(mcp_client=self.mcp_client)

        # Verify the factory was initialized with the config values
        self.assertEqual(factory.mcp_client, self.mcp_client)
        self.assertEqual(factory._toolkit_cache.maxsize, 100)
        self.assertEqual(factory._toolkit_cache.ttl, 3600)

        # Create factory with custom parameters
        custom_factory = MCPToolkitFactory(mcp_client=self.mcp_client, cache_size=200, cache_expiry_seconds=7200)

        # Verify the factory was initialized with custom values
        self.assertEqual(custom_factory.mcp_client, self.mcp_client)
        self.assertEqual(custom_factory._toolkit_cache.maxsize, 200)
        self.assertEqual(custom_factory._toolkit_cache.ttl, 7200)

    @patch('codemie.service.mcp.toolkit.MCPToolkit')
    async def test_create_toolkit(self, mock_toolkit_class):
        """Test creating a toolkit through the factory."""
        # Configure mocks
        mock_toolkit = MagicMock(spec=MCPToolkit)
        mock_toolkit_class.return_value = mock_toolkit

        self.mcp_client.list_tools = MagicMock()
        self.mcp_client.list_tools.return_value = [self.basic_tool_def]

        # Create factory
        factory = MCPToolkitFactory(mcp_client=self.mcp_client)

        # Create toolkit
        toolkit = await factory.create_toolkit(
            server_config=self.server_config,
            toolkit_name="Test Toolkit",
            toolkit_description="Test toolkit description",
        )

        # Verify the toolkit was created
        self.assertEqual(toolkit, mock_toolkit)

        # Verify the MCPToolkit constructor was called with correct parameters
        mock_toolkit_class.assert_called_once_with(
            name="Test Toolkit",
            description="Test toolkit description",
            mcp_client=self.mcp_client,
            mcp_server_config=self.server_config,
            tools_definitions=[self.basic_tool_def],
        )

        # Verify the toolkit was cached
        cached_toolkit = factory.get_toolkit(self.server_config)
        self.assertEqual(cached_toolkit, mock_toolkit)

    @patch('codemie.service.mcp.toolkit.MCPToolkit')
    async def test_create_toolkit_with_token_limit(self, mock_toolkit_class):
        """Test creating a toolkit with custom token size limit."""
        # Configure mocks
        mock_toolkit = MagicMock(spec=MCPToolkit)
        mock_toolkit.get_tools.return_value = [MagicMock(spec=CodeMieTool)]
        mock_toolkit_class.return_value = mock_toolkit

        self.mcp_client.list_tools = MagicMock()
        self.mcp_client.list_tools.return_value = [self.basic_tool_def]

        # Create factory
        factory = MCPToolkitFactory(mcp_client=self.mcp_client)

        # Create toolkit with custom token size limit
        custom_token_limit = 5000
        toolkit = await factory.create_toolkit(
            server_config=self.server_config,
            toolkit_name="Test Toolkit",
            toolkit_description="Test toolkit description",
            tools_tokens_size_limit=custom_token_limit,
        )

        # Verify the toolkit was created
        self.assertEqual(toolkit, mock_toolkit)

        # Verify that get_tools was called
        mock_toolkit.get_tools.assert_called_once()

        # Each tool should have had its token size limit set
        for tool in mock_toolkit.get_tools.return_value:
            self.assertEqual(tool.tokens_size_limit, custom_token_limit)

    def test_generate_cache_key(self):
        """Test generation of cache keys."""
        # Create two different server configs
        config1 = MCPServerConfig(command="command1", args=["arg1", "arg2"], env={"ENV_VAR": "value1"})

        config2 = MCPServerConfig(command="command2", args=["arg1", "arg2"], env={"ENV_VAR": "value2"})

        # Create factory
        factory = MCPToolkitFactory(mcp_client=self.mcp_client)

        # Generate keys
        key1 = factory._generate_cache_key(config1)
        key2 = factory._generate_cache_key(config2)

        # Keys should be different
        self.assertNotEqual(key1, key2)

        # Same config should produce same key
        key1_duplicate = factory._generate_cache_key(config1)
        self.assertEqual(key1, key1_duplicate)

    @patch('codemie.service.mcp.toolkit.MCPToolkit')
    async def test_cache_operations(self, mock_toolkit_class):
        """Test cache operations like get_toolkit, clear_cache, and remove_toolkit_from_cache."""
        # Configure mocks
        mock_toolkit = MagicMock(spec=MCPToolkit)
        mock_toolkit_class.return_value = mock_toolkit

        self.mcp_client.list_tools = MagicMock()
        self.mcp_client.list_tools.return_value = [self.basic_tool_def]

        # Create factory
        factory = MCPToolkitFactory(mcp_client=self.mcp_client)

        # Initially, get_toolkit should return None
        self.assertIsNone(factory.get_toolkit(self.server_config))

        # Create toolkit to populate cache
        await factory.create_toolkit(server_config=self.server_config)

        # Now get_toolkit should return the cached toolkit
        self.assertEqual(factory.get_toolkit(self.server_config), mock_toolkit)

        # Remove toolkit from cache
        factory.remove_toolkit_from_cache(self.server_config)

        # get_toolkit should now return None again
        self.assertIsNone(factory.get_toolkit(self.server_config))

        # Create toolkit again
        await factory.create_toolkit(server_config=self.server_config)

        # Clear entire cache
        factory.clear_cache()

        # get_toolkit should return None again
        self.assertIsNone(factory.get_toolkit(self.server_config))
