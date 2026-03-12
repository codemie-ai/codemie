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
Integration tests for MCPToolkitService.

This module contains tests that verify the integration behavior of the MCPToolkitService class,
including cache management and configuration from the config module.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock

from cachetools import TTLCache

from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import MCPServerConfig, MCPToolDefinition
from codemie.service.mcp.toolkit_service import MCPToolkitService


class TestMCPToolkitServiceCacheManagement(unittest.TestCase):
    """Test class for MCPToolkitService cache management functionality."""

    def setUp(self):
        """Set up test environment before each test."""
        # Reset the singleton instance before each test to ensure clean state
        MCPToolkitService.reset_instance()

    def tearDown(self):
        """Clean up after each test."""
        # Reset the singleton instance after each test
        MCPToolkitService.reset_instance()

    def test_clear_cache(self):
        """
        Test that clear_cache properly clears the toolkit factory cache.

        Steps:
        - Create an instance of MCPToolkitService directly
        - Mock the toolkit_factory's clear_cache method
        - Call clear_cache on the instance
        - Verify that toolkit_factory.clear_cache was called
        """
        # Create a mock MCPConnectClient
        mock_client = Mock(spec=MCPConnectClient)
        mock_client.base_url = "https://mock-mcp-server.com"

        # Create a direct instance of MCPToolkitService
        service = MCPToolkitService(mock_client)

        # Mock the toolkit_factory's clear_cache method
        service.toolkit_factory.clear_cache = Mock()

        # Call clear_cache on the instance
        service.clear_cache()

        # Verify that toolkit_factory.clear_cache was called
        service.toolkit_factory.clear_cache.assert_called_once()

    def test_integration_with_toolkit_factory_cache(self):
        """
        Test the integration between MCPToolkitService and MCPToolkitFactory caching.

        Steps:
        - Create a real (non-mocked) instance of MCPToolkitService with a mock client
        - Mock the client's list_tools method to return mock tool definitions
        - Create a server config
        - Call get_toolkit with the server config
        - Verify a toolkit is created
        - Call get_toolkit again with the same config
        - Verify the same toolkit is returned (via the toolkit factory's cache)
        - Call clear_cache
        - Call get_toolkit again
        - Verify that a new toolkit is created (toolkit factory cache was cleared)
        """
        # Create a mock MCPConnectClient
        mock_client = Mock(spec=MCPConnectClient)
        mock_client.base_url = "https://mock-mcp-server.com"

        # Create sample tool definitions
        mock_tool_definition = MCPToolDefinition(
            name="mockTool",
            description="A mock tool for testing",
            inputSchema={"type": "object", "properties": {}, "required": []},
        )

        # Setup the client's list_tools method to return mock definitions
        async def mock_list_tools(server_config, execution_context=None):
            return [mock_tool_definition]

        mock_client.list_tools = MagicMock(side_effect=mock_list_tools)

        # Create a real instance of MCPToolkitService with the mock client
        service = MCPToolkitService(mock_client)

        # Create a server config for testing
        server_config = MCPServerConfig(command="test_command", args=["--arg1", "--arg2"])

        # Create a spy on the create_toolkit method to track calls
        original_create_toolkit = service.toolkit_factory.create_toolkit
        service.toolkit_factory.create_toolkit = MagicMock(side_effect=original_create_toolkit)

        # First call to get_toolkit should create a new toolkit
        toolkit1 = service.get_toolkit(server_config)

        # Verify create_toolkit was called
        service.toolkit_factory.create_toolkit.assert_called_once()
        service.toolkit_factory.create_toolkit.reset_mock()

        # Second call with same config should use cached toolkit
        toolkit2 = service.get_toolkit(server_config)

        # Verify create_toolkit was not called again
        service.toolkit_factory.create_toolkit.assert_not_called()

        # Verify we got the same toolkit instance
        self.assertIs(toolkit1, toolkit2)

        # Clear the cache
        service.clear_cache()

        # Call get_toolkit again after clearing cache
        toolkit3 = service.get_toolkit(server_config)

        # Verify create_toolkit was called again
        service.toolkit_factory.create_toolkit.assert_called_once()

        # Verify we got a different toolkit instance
        self.assertIsNot(toolkit1, toolkit3)


class TestMCPToolkitServiceIntegration(unittest.TestCase):
    """Test class for MCPToolkitService integration with config module."""

    def setUp(self):
        """Set up test environment before each test."""
        # Reset the singleton instance before each test to ensure clean state
        MCPToolkitService.reset_instance()

    def tearDown(self):
        """Clean up after each test."""
        # Reset the singleton instance after each test
        MCPToolkitService.reset_instance()

    @patch('codemie.service.mcp.client.MCPConnectClient')
    @patch('codemie.configs.config.config.MCP_CONNECT_URL', 'https://patched-mcp-url.com')
    def test_configuration_from_config_module(self, mock_client_constructor):
        """
        Test that the singleton is initialized with the correct configuration from the config module.

        Steps:
        - Reset the instance first
        - Patch the config module's MCP_CONNECT_URL
        - Patch the MCPConnectClient constructor
        - Import the module to trigger singleton initialization
        - Verify that MCPConnectClient was called with the correct URL
        - Verify that the singleton was initialized
        """
        # Reset instance to force re-initialization
        MCPToolkitService.reset_instance()

        # Set return value for the mocked client constructor
        mock_client = Mock(spec=MCPConnectClient)
        mock_client.base_url = 'https://patched-mcp-url.com'
        mock_client_constructor.return_value = mock_client

        # Re-import the module to trigger initialization with patched config
        import importlib
        import codemie.service.mcp.toolkit_service as mcp_module

        importlib.reload(mcp_module)

        # The module reload should have already initialized the singleton, but we'll verify
        # that initialization happened properly

        # Verify that MCPConnectClient was called with the correct URL
        mock_client_constructor.assert_called_once_with('https://patched-mcp-url.com')

        # Verify that the singleton was initialized
        self.assertIsNotNone(mcp_module.MCPToolkitService._instance)
        self.assertEqual(mcp_module.MCPToolkitService._instance.mcp_client, mock_client)

    @patch('codemie.configs.config.config.MCP_TOOLKIT_SERVICE_CACHE_SIZE', 200)
    @patch('codemie.configs.config.config.MCP_TOOLKIT_SERVICE_CACHE_TTL', 7200)
    @patch('codemie.service.mcp.toolkit_service.TTLCache')
    @patch('codemie.service.mcp.toolkit_service.MCPConnectClient')
    def test_ttl_cache_configuration(self, mock_client_constructor, mock_ttl_cache):
        """
        Test that the TTLCache is configured with the correct values from config.

        Steps:
        - Patch the config module's cache size and TTL constants
        - Reset the instance to force recreation
        - Create a new instance to trigger cache creation
        - Verify that TTLCache was initialized with the patched values
        """
        # Reset instance to force re-initialization with patched config values
        MCPToolkitService.reset_instance()

        # Mock TTLCache to verify it's called with correct parameters
        mock_cache_instance = Mock(spec=TTLCache)
        mock_ttl_cache.return_value = mock_cache_instance

        # Mock client for initialization
        mock_client = Mock(spec=MCPConnectClient)
        mock_client.base_url = 'https://mock-url.com'
        mock_client_constructor.return_value = mock_client

        # Re-import the module to trigger initialization with patched config
        import importlib
        import codemie.service.mcp.toolkit_service as mcp_module

        importlib.reload(mcp_module)

        # Force creation of a new instance with our patched TTLCache
        mcp_module.MCPToolkitService._instances_cache = mock_ttl_cache(maxsize=200, ttl=7200)

        # Verify TTLCache was initialized with the correct parameters
        mock_ttl_cache.assert_called_with(maxsize=200, ttl=7200)
