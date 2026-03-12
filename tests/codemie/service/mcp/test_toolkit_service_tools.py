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
Tests for MCPToolkitService.get_tools functionality.
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import BaseTool

from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import MCPServerConfig
from codemie.service.mcp.toolkit import MCPToolkit
from codemie.service.mcp.toolkit_service import MCPToolkitService


class TestMCPToolkitServiceGetTools:
    """Test cases for MCPToolkitService.get_tools method."""

    def setup_method(self):
        """Set up test fixtures for each test method."""
        # Create a mock client
        self.mock_client = MagicMock(spec=MCPConnectClient)
        self.mock_client.base_url = "http://mock-mcp-server.com"

        # Create a server config
        self.server_config = MCPServerConfig(
            command="test-command",  # Adding required command field
            args=["--test-arg"],
            env={"TEST_ENV": "test-value"},
        )

        # Create the service instance directly
        self.toolkit_service = MCPToolkitService(self.mock_client)

    def test_get_tools(self):
        """
        Test that get_tools correctly retrieves tools from a toolkit.

        Steps:
        - Create a mock client and server config
        - Create an instance of MCPToolkitService directly
        - Create a mock toolkit with mock tools
        - Mock the get_toolkit method to return the mock toolkit
        - Call get_tools with the server config
        - Verify that get_toolkit was called with the correct parameters
        - Verify that toolkit.get_tools was called
        - Verify that the returned tools match the toolkit's tools
        """
        # Create mock tools and toolkit
        mock_tool1 = MagicMock(spec=BaseTool)
        mock_tool2 = MagicMock(spec=BaseTool)
        mock_tools = [mock_tool1, mock_tool2]

        mock_toolkit = MagicMock(spec=MCPToolkit)
        mock_toolkit.get_tools.return_value = mock_tools

        # Mock the get_toolkit method to return our mock toolkit
        with patch.object(self.toolkit_service, 'get_toolkit', return_value=mock_toolkit) as mock_get_toolkit:
            # Call get_tools
            result = self.toolkit_service.get_tools(self.server_config)

            # Verify get_toolkit was called with correct parameters
            mock_get_toolkit.assert_called_once_with(self.server_config, use_cache=True)

            # Verify toolkit.get_tools was called
            mock_toolkit.get_tools.assert_called_once()

            # Verify the result matches the toolkit's tools
            assert result == mock_tools

    def test_get_tools_with_cache_parameter(self):
        """
        Test that get_tools correctly passes the cache parameter to get_toolkit.

        Steps:
        - Create a mock client and server config
        - Create an instance of MCPToolkitService directly
        - Mock the get_toolkit method
        - Call get_tools with use_cache=False
        - Verify that get_toolkit was called with use_cache=False
        - Call get_tools with use_cache=True
        - Verify that get_toolkit was called with use_cache=True
        """
        # Create a mock toolkit
        mock_toolkit = MagicMock(spec=MCPToolkit)
        mock_toolkit.get_tools.return_value = []

        # Mock the get_toolkit method to return our mock toolkit
        with patch.object(self.toolkit_service, 'get_toolkit', return_value=mock_toolkit) as mock_get_toolkit:
            # Call get_tools with use_cache=False
            self.toolkit_service.get_tools(self.server_config, use_cache=False)

            # Verify get_toolkit was called with use_cache=False
            mock_get_toolkit.assert_called_once_with(self.server_config, use_cache=False)

            # Reset the mock and call again with use_cache=True
            mock_get_toolkit.reset_mock()
            self.toolkit_service.get_tools(self.server_config, use_cache=True)

            # Verify get_toolkit was called with use_cache=True
            mock_get_toolkit.assert_called_once_with(self.server_config, use_cache=True)

    def test_get_tools_error_handling(self):
        """
        Test that get_tools properly handles and propagates errors.

        Steps:
        - Create a mock client and server config
        - Create an instance of MCPToolkitService directly
        - Mock the get_toolkit method to raise an exception
        - Call get_tools and expect it to raise the same exception
        """
        # Define a custom exception for testing
        test_exception = ValueError("Test toolkit error")

        # Mock the get_toolkit method to raise our test exception
        with patch.object(self.toolkit_service, 'get_toolkit', side_effect=test_exception):
            # Call get_tools and expect it to raise the same exception
            with pytest.raises(ValueError) as excinfo:
                self.toolkit_service.get_tools(self.server_config)

            # Verify the exception is the same one we raised
            assert str(excinfo.value) == "Test toolkit error"
