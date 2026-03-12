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
Tests for MCPToolkitService get_instance functionality.

This module contains tests that verify the singleton and caching behavior
of the MCPToolkitService get_instance method.
"""

import pytest
from unittest.mock import MagicMock, patch

from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.toolkit_service import MCPToolkitService


class TestMCPToolkitServiceGetInstance:
    """Test class for MCPToolkitService get_instance functionality."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        # Reset the singleton instance before each test
        MCPToolkitService.reset_instance()

    def teardown_method(self):
        """Tear down test fixtures after each test method."""
        # Clean up after each test
        MCPToolkitService.reset_instance()

    def test_get_instance_singleton_error(self):
        """Test that get_instance raises an error when singleton is not initialized and no client is provided."""
        # Reset the instance first (already done in setup_method, but being explicit for clarity)
        MCPToolkitService.reset_instance()

        # Call get_instance with no arguments and verify that a RuntimeError is raised
        with pytest.raises(RuntimeError) as excinfo:
            MCPToolkitService.get_instance()

        # Verify the error message
        assert "MCPToolkitService singleton not initialized" in str(excinfo.value)
        assert "Call init_singleton first" in str(excinfo.value)

    def test_get_instance_with_singleton(self):
        """Test that get_instance returns the singleton when no client is provided and the singleton is initialized."""
        # Create a mock client
        mock_client = MagicMock(spec=MCPConnectClient)
        mock_client.base_url = "http://mock-singleton-url.com"

        # Initialize the singleton with the mock client
        MCPToolkitService.init_singleton(mock_client)

        # Call get_instance with no arguments
        instance = MCPToolkitService.get_instance()

        # Verify that the returned instance is the singleton instance
        assert instance is MCPToolkitService._instance
        assert instance.mcp_client is mock_client

    def test_get_instance_with_client(self):
        """Test that get_instance returns a cached instance for a given client if it exists."""
        # Create a mock client
        mock_client = MagicMock(spec=MCPConnectClient)
        mock_client.base_url = "http://mock-client-url.com"

        # Mock the MCPToolkitService constructor to track calls
        with patch.object(MCPToolkitService, '__init__', return_value=None) as mock_init:
            # First call should create a new instance
            instance1 = MCPToolkitService.get_instance(mock_client)
            assert mock_init.call_count == 1

            # Second call with the same client should return the cached instance
            instance2 = MCPToolkitService.get_instance(mock_client)
            # Constructor should not be called again
            assert mock_init.call_count == 1

            # Verify that both calls return the same instance
            assert instance1 is instance2

    def test_get_instance_cache_behavior(self):
        """Test that get_instance properly caches instances by client base_url."""
        # Create two mock clients with different base_urls
        mock_client1 = MagicMock(spec=MCPConnectClient)
        mock_client1.base_url = "http://mock-url-1.com"

        mock_client2 = MagicMock(spec=MCPConnectClient)
        mock_client2.base_url = "http://mock-url-2.com"

        # Get instances for each client
        instance1 = MCPToolkitService.get_instance(mock_client1)
        instance2 = MCPToolkitService.get_instance(mock_client2)

        # Verify that different instances are created for each client
        assert instance1 is not instance2

        # Verify that each instance is cached with the correct base_url as key
        assert MCPToolkitService._instances_cache[mock_client1.base_url] is instance1
        assert MCPToolkitService._instances_cache[mock_client2.base_url] is instance2

        # Call get_instance again for client1 and verify cached instance is returned
        instance1_cached = MCPToolkitService.get_instance(mock_client1)
        assert instance1_cached is instance1

        # Change one client's base_url and call get_instance again
        mock_client1.base_url = "http://mock-url-3.com"
        instance3 = MCPToolkitService.get_instance(mock_client1)

        # Verify that a new instance is created for the changed URL
        assert instance3 is not instance1
        assert instance3 is not instance2
        assert MCPToolkitService._instances_cache["http://mock-url-3.com"] is instance3
