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
Tests for the MCPToolkitService get_toolkit functionality

This module contains tests specifically for the get_toolkit and
get_toolkit_async methods of the MCPToolkitService class.
"""

import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock, call
import pytest

from codemie.service.mcp.models import MCPServerConfig
from codemie.service.mcp.toolkit import MCPToolkit, MCPToolkitFactory
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.mcp.client import MCPConnectClient


class TestMCPToolkitServiceGetToolkit(unittest.TestCase):
    """
    Test class for MCPToolkitService get_toolkit and get_toolkit_async methods
    """

    def setUp(self):
        """Set up common test fixtures"""
        # Create mock client and server config
        self.mock_client = MagicMock(spec=MCPConnectClient)
        self.mock_server_config = MagicMock(spec=MCPServerConfig)

        # Add single_usage attribute to mock server config
        self.mock_server_config.single_usage = False

        # Create service instance for testing
        self.service = MCPToolkitService(self.mock_client)

        # Replace the toolkit factory with a mock
        self.service.toolkit_factory = MagicMock(spec=MCPToolkitFactory)

    def test_get_toolkit_async_with_cache(self):
        """
        Test that get_toolkit_async returns a cached toolkit when available and use_cache is True.

        Steps:
        - Create a mock client and server config
        - Create an instance of MCPToolkitService directly
        - Mock the toolkit_factory's get_toolkit to return a mock toolkit
        - Call get_toolkit_async with use_cache=True
        - Verify that toolkit_factory.get_toolkit was called with the correct server config
        - Verify that toolkit_factory.create_toolkit was not called
        - Verify that the returned toolkit is the one from the cache
        """
        # Create a mock toolkit to be returned from the cache
        mock_cached_toolkit = MagicMock(spec=MCPToolkit)
        self.service.toolkit_factory.get_toolkit.return_value = mock_cached_toolkit

        # Run the coroutine and get the result
        result = asyncio.run(
            self.service.get_toolkit_async(
                server_config=self.mock_server_config,
                toolkit_name="test-toolkit",
                toolkit_description="Test toolkit",
                tools_tokens_size_limit=1000,
                use_cache=True,
            )
        )

        # Verify that get_toolkit was called with the server config
        self.service.toolkit_factory.get_toolkit.assert_called_once_with(self.mock_server_config)

        # Verify that create_toolkit was not called
        self.service.toolkit_factory.create_toolkit.assert_not_called()

        # Verify the returned toolkit is the cached one
        self.assertIs(result, mock_cached_toolkit)

    def test_get_toolkit_async_without_cache(self):
        """
        Test that get_toolkit_async creates a new toolkit when use_cache is False.

        Steps:
        - Create a mock client and server config
        - Create an instance of MCPToolkitService directly
        - Mock the toolkit_factory's create_toolkit to return a mock toolkit
        - Call get_toolkit_async with use_cache=False
        - Verify that toolkit_factory.get_toolkit was not called
        - Verify that toolkit_factory.create_toolkit was called with the correct parameters
        - Verify that the returned toolkit is the newly created one
        """
        # Create a mock toolkit to be returned by create_toolkit
        mock_toolkit = MagicMock(spec=MCPToolkit)
        self.service.toolkit_factory.create_toolkit = AsyncMock(return_value=mock_toolkit)

        # Run the coroutine and get the result
        result = asyncio.run(
            self.service.get_toolkit_async(
                server_config=self.mock_server_config,
                toolkit_name="test-toolkit",
                toolkit_description="Test toolkit",
                tools_tokens_size_limit=1000,
                use_cache=False,
            )
        )

        # Verify that get_toolkit was not called
        self.service.toolkit_factory.get_toolkit.assert_not_called()

        # Verify that create_toolkit was called with the correct parameters
        self.service.toolkit_factory.create_toolkit.assert_called_once_with(
            server_config=self.mock_server_config,
            toolkit_name="test-toolkit",
            toolkit_description="Test toolkit",
            tools_tokens_size_limit=1000,
            use_cache=False,
            execution_context=None,
        )

        # Verify the returned toolkit is the newly created one
        self.assertIs(result, mock_toolkit)

    def test_get_toolkit_async_cache_miss(self):
        """
        Test that get_toolkit_async creates a new toolkit when cache is enabled but no toolkit is cached.

        Steps:
        - Create a mock client and server config
        - Create an instance of MCPToolkitService directly
        - Mock the toolkit_factory's get_toolkit to return None (cache miss)
        - Mock the toolkit_factory's create_toolkit to return a mock toolkit
        - Call get_toolkit_async with use_cache=True
        - Verify that toolkit_factory.get_toolkit was called
        - Verify that toolkit_factory.create_toolkit was called with the correct parameters
        - Verify that the returned toolkit is the newly created one
        """
        # Simulate cache miss
        self.service.toolkit_factory.get_toolkit.return_value = None

        # Create a mock toolkit to be returned by create_toolkit
        mock_toolkit = MagicMock(spec=MCPToolkit)
        self.service.toolkit_factory.create_toolkit = AsyncMock(return_value=mock_toolkit)

        # Run the coroutine and get the result
        result = asyncio.run(
            self.service.get_toolkit_async(
                server_config=self.mock_server_config,
                toolkit_name="test-toolkit",
                toolkit_description="Test toolkit",
                tools_tokens_size_limit=1000,
                use_cache=True,
            )
        )

        # Verify that get_toolkit was called
        self.service.toolkit_factory.get_toolkit.assert_called_once_with(self.mock_server_config)

        # Verify that create_toolkit was called with the correct parameters
        self.service.toolkit_factory.create_toolkit.assert_called_once_with(
            server_config=self.mock_server_config,
            toolkit_name="test-toolkit",
            toolkit_description="Test toolkit",
            tools_tokens_size_limit=1000,
            use_cache=True,
            execution_context=None,
        )

        # Verify the returned toolkit is the newly created one
        self.assertIs(result, mock_toolkit)

    def test_get_toolkit_async_error_handling(self):
        """
        Test that get_toolkit_async properly handles and propagates errors.

        Steps:
        - Create a mock client and server config
        - Create an instance of MCPToolkitService directly
        - Mock the toolkit_factory's create_toolkit to raise an exception
        - Call get_toolkit_async and expect it to raise the same exception
        - Verify that the error is logged
        """
        # Force a cache miss
        self.service.toolkit_factory.get_toolkit.return_value = None

        # Make create_toolkit raise an exception
        test_exception = ValueError("Test error")
        self.service.toolkit_factory.create_toolkit = AsyncMock(side_effect=test_exception)

        # Run the coroutine and expect it to raise the exception
        with pytest.raises(ValueError, match="Test error"):
            asyncio.run(self.service.get_toolkit_async(server_config=self.mock_server_config, use_cache=True))

        # Verify that create_toolkit was called
        self.service.toolkit_factory.create_toolkit.assert_called_once()

    def test_get_toolkit_in_running_loop(self):
        """
        Test that get_toolkit works correctly when called from within a running asyncio loop.

        Steps:
        - Create a mock client and server config
        - Create an instance of MCPToolkitService directly
        - Mock get_toolkit_async to return a specific value
        - Create a test function that calls get_toolkit while already in an asyncio loop
        - Run the test function in an asyncio loop
        - Verify that the returned value matches what get_toolkit_async would return
        - Verify that the ThreadPoolExecutor was used to avoid deadlock
        """
        # Create a mock toolkit that will be returned
        mock_toolkit = MagicMock(spec=MCPToolkit)

        # Define how we want the test to behave
        async def test_coroutine():
            # Set up the mocks
            with patch.object(self.service, 'get_toolkit_async', new_callable=AsyncMock) as mock_get_toolkit_async:
                mock_get_toolkit_async.return_value = mock_toolkit

                # Create a mock for RuntimeError that will be raised when checking for a running loop
                with patch('asyncio.get_running_loop') as mock_get_running_loop:
                    # Make it return a mock to simulate being in a running loop
                    mock_get_running_loop.return_value = MagicMock()  # Return a mock loop object

                    # Patch ThreadPoolExecutor to verify it gets called
                    with patch('codemie.service.mcp.toolkit_service.ThreadPoolExecutor') as mock_executor_class:
                        mock_executor = MagicMock()
                        mock_executor_class.return_value.__enter__.return_value = mock_executor
                        mock_future = MagicMock()
                        mock_executor.submit.return_value = mock_future
                        mock_future.result.return_value = mock_toolkit

                        # Call get_toolkit
                        result = self.service.get_toolkit(
                            server_config=self.mock_server_config,
                            toolkit_name="test-toolkit",
                            toolkit_description="Test toolkit",
                            tools_tokens_size_limit=1000,
                            use_cache=True,
                        )

                        # Verify the ThreadPoolExecutor was used correctly
                        mock_executor_class.assert_called_once_with(max_workers=1)
                        mock_executor.submit.assert_called_once()

                        # Verify we got the expected result
                        assert result is mock_toolkit

        # Run the test coroutine
        asyncio.run(test_coroutine())

    def test_get_toolkit_no_running_loop(self):
        """
        Test that get_toolkit works correctly when called without a running asyncio loop.

        Steps:
        - Create a mock client and server config
        - Create an instance of MCPToolkitService directly
        - Mock get_toolkit_async to return a specific value
        - Call get_toolkit directly (no running loop)
        - Verify that the returned value matches what get_toolkit_async would return
        - Verify that asyncio.run was called to execute the coroutine
        """
        # Create a mock toolkit that will be returned
        mock_toolkit = MagicMock(spec=MCPToolkit)

        # Mock get_toolkit_async to return our mock toolkit
        self.service.get_toolkit_async = AsyncMock(return_value=mock_toolkit)

        # Mock asyncio.get_running_loop to raise RuntimeError (simulating no running loop)
        with patch('asyncio.get_running_loop', side_effect=RuntimeError("No running loop")):
            # Mock asyncio.run to return our mock toolkit
            with patch('asyncio.run', return_value=mock_toolkit) as mock_run:
                # Call get_toolkit
                result = self.service.get_toolkit(
                    server_config=self.mock_server_config,
                    toolkit_name="test-toolkit",
                    toolkit_description="Test toolkit",
                    tools_tokens_size_limit=1000,
                    use_cache=True,
                )

                # Verify asyncio.run was called
                mock_run.assert_called_once()

                # Verify we got the expected result
                self.assertIs(result, mock_toolkit)

    def test_get_toolkit_with_custom_parameters(self):
        """
        Test that get_toolkit correctly passes all optional parameters to the toolkit factory.

        Steps:
        - Create a mock client and server config
        - Create a test instance with the mock client
        - Mock the toolkit_factory's create_toolkit
        - Call get_toolkit with custom toolkit_name, toolkit_description, and tools_tokens_size_limit
        - Verify that create_toolkit was called with all the custom parameters
        """
        # Set up the expected toolkit
        mock_toolkit = MagicMock(spec=MCPToolkit)

        # Mock the get_toolkit_async method to allow verification of parameters
        self.service.get_toolkit_async = AsyncMock(return_value=mock_toolkit)

        # Call get_toolkit with custom parameters
        with patch('asyncio.run', return_value=mock_toolkit):
            result = self.service.get_toolkit(
                server_config=self.mock_server_config,
                toolkit_name="custom-name",
                toolkit_description="Custom description",
                tools_tokens_size_limit=2000,
                use_cache=False,
            )

            # Verify that get_toolkit_async was called with the right parameters
            expected_call = call(
                server_config=self.mock_server_config,
                toolkit_name="custom-name",
                toolkit_description="Custom description",
                tools_tokens_size_limit=2000,
                use_cache=False,
                execution_context=None,
            )
            self.assertIn(expected_call, self.service.get_toolkit_async.mock_calls)

            # Verify we got the expected result
            self.assertIs(result, mock_toolkit)
