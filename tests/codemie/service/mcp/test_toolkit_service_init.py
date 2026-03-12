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
Tests for MCPToolkitService initialization behavior.

This module contains tests that verify the initialization behavior of the MCPToolkitService class,
including singleton pattern implementation, constructor usage, and instance reset functionality.
"""

import unittest
from unittest.mock import Mock

from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.mcp.toolkit import MCPToolkitFactory


class TestMCPToolkitServiceInitialization(unittest.TestCase):
    """Test class for MCPToolkitService initialization behavior."""

    def setUp(self):
        """Set up test environment before each test."""
        # Reset the singleton instance before each test to ensure clean state
        MCPToolkitService.reset_instance()

    def tearDown(self):
        """Clean up after each test."""
        # Reset the singleton instance after each test
        MCPToolkitService.reset_instance()

    def test_singleton_initialization(self):
        """
        Test that init_singleton properly initializes the singleton instance.

        Steps:
        - Reset the instance first to ensure clean state
        - Create a mock MCPConnectClient
        - Call init_singleton with the mock client
        - Verify that _instance is set to a non-None value
        - Verify that _instance.mcp_client is the mock client
        - Verify the instance is cached in _instances_cache with the client's base_url as key
        """
        # Create a mock MCPConnectClient
        mock_client = Mock(spec=MCPConnectClient)
        mock_client.base_url = "https://mock-mcp-server.com"

        # Call init_singleton with the mock client
        MCPToolkitService.init_singleton(mock_client)

        # Verify that _instance is set to a non-None value
        self.assertIsNotNone(MCPToolkitService._instance)

        # Verify that _instance.mcp_client is the mock client
        self.assertEqual(MCPToolkitService._instance.mcp_client, mock_client)

        # Verify the instance is cached in _instances_cache with the client's base_url as key
        self.assertIn(mock_client.base_url, MCPToolkitService._instances_cache)
        self.assertEqual(MCPToolkitService._instances_cache[mock_client.base_url], MCPToolkitService._instance)

    def test_constructor_usage(self):
        """
        Test that direct constructor usage works properly.

        Steps:
        - Create a mock MCPConnectClient
        - Create an instance of MCPToolkitService directly
        - Verify the instance has the correct mcp_client
        - Verify the instance has a properly initialized toolkit_factory
        """
        # Create a mock MCPConnectClient
        mock_client = Mock(spec=MCPConnectClient)
        mock_client.base_url = "https://mock-mcp-server.com"

        # Create an instance of MCPToolkitService directly
        service = MCPToolkitService(mock_client)

        # Verify the instance has the correct mcp_client
        self.assertEqual(service.mcp_client, mock_client)

        # Verify the instance has a properly initialized toolkit_factory
        self.assertIsInstance(service.toolkit_factory, MCPToolkitFactory)
        self.assertEqual(service.toolkit_factory.mcp_client, mock_client)

    def test_reset_instance(self):
        """
        Test that reset_instance properly clears the singleton and cache.

        Steps:
        - Create and initialize the singleton instance
        - Add some entries to the _instances_cache
        - Call reset_instance
        - Verify that _instance is None
        - Verify that _instances_cache is empty
        """
        # Create and initialize the singleton instance
        mock_client = Mock(spec=MCPConnectClient)
        mock_client.base_url = "https://mock-mcp-server.com"

        MCPToolkitService.init_singleton(mock_client)

        # Add another entry to the _instances_cache
        mock_client2 = Mock(spec=MCPConnectClient)
        mock_client2.base_url = "https://another-mock-server.com"
        MCPToolkitService._instances_cache[mock_client2.base_url] = MCPToolkitService(mock_client2)

        # Verify that we have entries in the cache
        self.assertEqual(len(MCPToolkitService._instances_cache), 2)

        # Call reset_instance
        MCPToolkitService.reset_instance()

        # Verify that _instance is None
        self.assertIsNone(MCPToolkitService._instance)

        # Verify that _instances_cache is empty
        self.assertEqual(len(MCPToolkitService._instances_cache), 0)
