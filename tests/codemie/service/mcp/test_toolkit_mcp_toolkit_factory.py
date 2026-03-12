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
Tests for MCPToolkitFactory class in toolkit.py.
"""

import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio

from cachetools import TTLCache

from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import MCPServerConfig, MCPToolDefinition
from codemie.service.mcp.toolkit import MCPToolkit, MCPToolkitFactory
from codemie.configs.config import config


class TestMCPToolkitFactory(unittest.TestCase):
    """Test suite for MCPToolkitFactory class."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create mock objects
        self.mock_client = MagicMock(spec=MCPConnectClient)

        # Create a mock server config
        self.mock_server_config = MagicMock(spec=MCPServerConfig)
        self.mock_server_config.url = None  # No URL, will use command
        self.mock_server_config.command = "test_command"
        self.mock_server_config.args = ["arg1", "arg2"]
        self.mock_server_config.env = {"ENV_VAR": "value"}

        # Create a different mock server config for cache testing
        self.mock_server_config2 = MagicMock(spec=MCPServerConfig)
        self.mock_server_config2.url = None  # No URL, will use command
        self.mock_server_config2.command = "another_command"
        self.mock_server_config2.args = ["argA", "argB"]
        self.mock_server_config2.env = {"DIFFERENT_ENV": "different_value"}

        # Create mock tool definitions
        self.mock_tool_definition1 = MagicMock(spec=MCPToolDefinition)
        self.mock_tool_definition1.name = "tool1"
        self.mock_tool_definition1.description = "Tool 1 description"
        self.mock_tool_definition1.inputSchema = {
            "properties": {"param1": {"type": "string", "description": "String param"}}
        }

        self.mock_tool_definition2 = MagicMock(spec=MCPToolDefinition)
        self.mock_tool_definition2.name = "tool2"
        self.mock_tool_definition2.description = "Tool 2 description"
        self.mock_tool_definition2.inputSchema = {
            "properties": {
                "param1": {"type": "integer", "description": "Integer param"},
                "param2": {"type": "boolean", "description": "Boolean param", "default": False},
            }
        }

        self.tools_definitions = [self.mock_tool_definition1, self.mock_tool_definition2]

    def test_factory_initialization_default_params(self):
        """Test that the MCPToolkitFactory can be properly initialized with default parameters."""
        # Initialize factory with defaults
        factory = MCPToolkitFactory(mcp_client=self.mock_client)

        # Verify that the instance is created successfully
        self.assertIsInstance(factory, MCPToolkitFactory)

        # Check that its attributes match expected parameters
        self.assertEqual(factory.mcp_client, self.mock_client)
        self.assertIsInstance(factory._toolkit_cache, TTLCache)

        # Check default cache parameters from config
        self.assertEqual(factory._toolkit_cache.maxsize, config.MCP_TOOLKIT_FACTORY_CACHE_SIZE)
        self.assertEqual(factory._toolkit_cache.ttl, config.MCP_TOOLKIT_FACTORY_CACHE_TTL)

    def test_factory_initialization_custom_params(self):
        """Test that the MCPToolkitFactory can be properly initialized with custom parameters."""
        # Initialize factory with custom cache parameters
        custom_cache_size = 100
        custom_cache_ttl = 3600

        factory = MCPToolkitFactory(
            mcp_client=self.mock_client, cache_size=custom_cache_size, cache_expiry_seconds=custom_cache_ttl
        )

        # Verify that the instance is created successfully
        self.assertIsInstance(factory, MCPToolkitFactory)

        # Check that its attributes match expected parameters
        self.assertEqual(factory.mcp_client, self.mock_client)
        self.assertIsInstance(factory._toolkit_cache, TTLCache)

        # Check custom cache parameters
        self.assertEqual(factory._toolkit_cache.maxsize, custom_cache_size)
        self.assertEqual(factory._toolkit_cache.ttl, custom_cache_ttl)

    @patch('codemie.service.mcp.toolkit.MCPToolkit')
    async def test_factory_create_toolkit_method(self, mock_toolkit_class):
        """Test toolkit creation through the factory."""
        # Configure mocks
        self.mock_client.list_tools = AsyncMock(return_value=self.tools_definitions)
        mock_toolkit_instance = MagicMock(spec=MCPToolkit)
        mock_toolkit_class.return_value = mock_toolkit_instance

        # Initialize factory
        factory = MCPToolkitFactory(mcp_client=self.mock_client)

        # Call create_toolkit
        toolkit = await factory.create_toolkit(server_config=self.mock_server_config)

        # Verify the mock client was called
        self.mock_client.list_tools.assert_called_once_with(self.mock_server_config)

        # Verify MCPToolkit constructor was called with correct parameters
        mock_toolkit_class.assert_called_once_with(
            name=f"MCP Toolkit ({self.mock_server_config.command})",
            description=f"Tools provided by MCP server: {self.mock_server_config.command} {' '.join(self.mock_server_config.args)}",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            tools_definitions=self.tools_definitions,
        )

        # Verify the returned toolkit is the expected mock instance
        self.assertEqual(toolkit, mock_toolkit_instance)

    def test_factory_create_toolkit_method_wrapper(self):
        """Test toolkit creation through the factory (wrapper for the async method)."""

        async def run_test():
            # Configure mocks
            self.mock_client.list_tools = AsyncMock(return_value=self.tools_definitions)
            mock_toolkit_instance = MagicMock(spec=MCPToolkit)

            with patch('codemie.service.mcp.toolkit.MCPToolkit', return_value=mock_toolkit_instance):
                # Initialize factory
                factory = MCPToolkitFactory(mcp_client=self.mock_client)

                # Call create_toolkit
                toolkit = await factory.create_toolkit(server_config=self.mock_server_config)

                # Verify the mock client was called with server_config and None execution_context
                self.mock_client.list_tools.assert_called_once_with(self.mock_server_config, None)

                # Verify the returned toolkit is the expected mock instance
                self.assertEqual(toolkit, mock_toolkit_instance)

        # Run the async test
        asyncio.run(run_test())

    async def test_factory_caching_behavior(self):
        """Test that toolkits are properly cached and retrieved."""
        # Configure mocks
        self.mock_client.list_tools = AsyncMock(return_value=self.tools_definitions)

        # Create mock toolkit instances - each with a unique id to verify caching
        mock_toolkit1 = MagicMock(spec=MCPToolkit)
        mock_toolkit1.id = "toolkit1"
        mock_toolkit2 = MagicMock(spec=MCPToolkit)
        mock_toolkit2.id = "toolkit2"

        # Initialize factory
        factory = MCPToolkitFactory(mcp_client=self.mock_client)

        # Patch MCPToolkit constructor to return different instances for different configs
        with patch('codemie.service.mcp.toolkit.MCPToolkit', side_effect=[mock_toolkit1, mock_toolkit2]):
            # Call create_toolkit for the first server config
            toolkit1_first_call = await factory.create_toolkit(server_config=self.mock_server_config)

            # Call create_toolkit again with same config - should return cached instance
            toolkit1_second_call = await factory.create_toolkit(server_config=self.mock_server_config)

            # Call create_toolkit with different config - should create a new instance
            toolkit2 = await factory.create_toolkit(server_config=self.mock_server_config2)

        # Verify list_tools was called exactly twice (once for each unique config)
        self.assertEqual(self.mock_client.list_tools.call_count, 2)

        # Verify both calls with the same config return the same instance (cached)
        self.assertIs(toolkit1_first_call, toolkit1_second_call)
        self.assertEqual(toolkit1_first_call.id, "toolkit1")

        # Verify the call with different config returns a different instance
        self.assertIsNot(toolkit1_first_call, toolkit2)
        self.assertEqual(toolkit2.id, "toolkit2")

    def test_cache_key_generation(self):
        """Test that the cache key is generated correctly and consistently."""
        # Initialize factory
        factory = MCPToolkitFactory(mcp_client=self.mock_client)

        # Generate cache keys for the two different configs
        key1 = factory._generate_cache_key(self.mock_server_config)
        key2 = factory._generate_cache_key(self.mock_server_config2)

        # Verify keys are strings and not empty
        self.assertIsInstance(key1, str)
        self.assertTrue(len(key1) > 0)

        # Verify different configs produce different keys
        self.assertNotEqual(key1, key2)

        # Generate key again for the first config
        key1_again = factory._generate_cache_key(self.mock_server_config)

        # Verify identical configs produce identical keys (deterministic)
        self.assertEqual(key1, key1_again)

        # Create a new server config with same values but different object identity
        same_as_config1 = MagicMock(spec=MCPServerConfig)
        same_as_config1.url = self.mock_server_config.url
        same_as_config1.command = self.mock_server_config.command
        same_as_config1.args = self.mock_server_config.args.copy()
        same_as_config1.env = self.mock_server_config.env.copy()

        # Generate key for the new but equivalent config
        key_same_as_1 = factory._generate_cache_key(same_as_config1)

        # Verify equivalent configs produce the same key
        self.assertEqual(key1, key_same_as_1)

    async def test_cache_clearing_and_removal(self):
        """Test that cache clearing and specific toolkit removal works."""
        # Configure mocks
        self.mock_client.list_tools = AsyncMock(return_value=self.tools_definitions)

        # Create two distinct mock toolkit instances
        mock_toolkit1 = MagicMock(spec=MCPToolkit)
        mock_toolkit1.id = "toolkit1"
        mock_toolkit2 = MagicMock(spec=MCPToolkit)
        mock_toolkit2.id = "toolkit2"

        # Initialize factory
        factory = MCPToolkitFactory(mcp_client=self.mock_client)

        # Patch MCPToolkit constructor to return our mock instances
        with patch('codemie.service.mcp.toolkit.MCPToolkit', side_effect=[mock_toolkit1, mock_toolkit2]):
            # Populate cache with both configs
            await factory.create_toolkit(server_config=self.mock_server_config)
            await factory.create_toolkit(server_config=self.mock_server_config2)

            # Verify both toolkits are in the cache
            self.assertEqual(len(factory._toolkit_cache), 2)

            # Test remove_toolkit_from_cache
            factory.remove_toolkit_from_cache(self.mock_server_config)

            # Verify first toolkit is removed but second remains
            self.assertEqual(len(factory._toolkit_cache), 1)

            # Creating the first toolkit again should call list_tools
            self.mock_client.list_tools.reset_mock()
            await factory.create_toolkit(server_config=self.mock_server_config)
            self.mock_client.list_tools.assert_called_once()

            # Verify both toolkits are in the cache again
            self.assertEqual(len(factory._toolkit_cache), 2)

            # Test clear_cache
            factory.clear_cache()

            # Verify cache is empty
            self.assertEqual(len(factory._toolkit_cache), 0)

    def test_get_toolkit(self):
        """Test getting a toolkit from cache."""
        # Initialize factory
        factory = MCPToolkitFactory(mcp_client=self.mock_client)

        # Create a mock toolkit
        mock_toolkit = MagicMock(spec=MCPToolkit)

        # Add toolkit to cache manually
        cache_key = factory._generate_cache_key(self.mock_server_config)
        factory._toolkit_cache[cache_key] = mock_toolkit

        # Get toolkit from cache
        retrieved_toolkit = factory.get_toolkit(self.mock_server_config)

        # Verify retrieved toolkit is the same as the one we added
        self.assertIs(retrieved_toolkit, mock_toolkit)

        # Try to get a toolkit that's not in the cache
        nonexistent_toolkit = factory.get_toolkit(self.mock_server_config2)

        # Verify None is returned for non-existent toolkit
        self.assertIsNone(nonexistent_toolkit)
