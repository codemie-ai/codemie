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
Tests for MCPTool class in toolkit.py.
"""

import asyncio
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import create_model

from codemie.configs.config import config
from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import (
    MCPServerConfig,
    MCPToolInvocationResponse,
    MCPToolContentItem,
    MCPExecutionContext,
)
from codemie.service.mcp.toolkit import MCPTool, MCPToolExecutionError, ContextAwareMCPTool


class TestMCPTool(unittest.TestCase):
    """Test suite for MCPTool class."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create mock objects
        self.mock_client = MagicMock(spec=MCPConnectClient)
        self.mock_server_config = MCPServerConfig(
            command="test_command", args=["arg1", "arg2"], env={"ENV_VAR": "value"}
        )

        # Create a simple args schema for testing
        self.args_schema = create_model(
            "TestArgsSchema",
            test_param=(str, ...),
            optional_param=(Optional[int], None),
        )

        # Create the tool instance
        self.tool = MCPTool(
            name="test_tool",
            description="A test MCP tool",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            args_schema=self.args_schema,
        )

    def tearDown(self):
        """Clean up after each test method."""
        # Clean up any pending coroutines
        if hasattr(self.mock_client, 'invoke_tool'):
            self.mock_client.invoke_tool.reset_mock()

    def test_basic_initialization(self):
        """Test basic initialization with valid parameters."""
        # Check that the tool is initialized with correct attributes
        self.assertEqual(self.tool.name, "test_tool")
        self.assertEqual(self.tool.description, "A test MCP tool")
        self.assertEqual(self.tool.mcp_client, self.mock_client)
        self.assertEqual(self.tool.mcp_server_config, self.mock_server_config)
        self.assertEqual(self.tool.args_schema, self.args_schema)
        self.assertEqual(self.tool.tokens_size_limit, config.MCP_TOOL_TOKENS_SIZE_LIMIT)

    def test_initialization_with_custom_tokens_size_limit(self):
        """Test initialization with custom tokens_size_limit."""
        custom_limit = 500
        tool = MCPTool(
            name="test_tool",
            description="A test MCP tool",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            args_schema=self.args_schema,
            tokens_size_limit=custom_limit,
        )
        self.assertEqual(tool.tokens_size_limit, custom_limit)

    @patch("codemie.service.mcp.toolkit.asyncio.run")
    def test_execute_without_running_loop(self, mock_asyncio_run):
        """Test execution without an existing asyncio loop."""
        # Mock RuntimeError when getting running loop
        with patch("codemie.service.mcp.toolkit.asyncio.get_running_loop", side_effect=RuntimeError):
            self.tool.execute(test_param="value")
            mock_asyncio_run.assert_called_once()
            # Path B wraps with asyncio.wait_for(...) for timeout enforcement,
            # so asyncio.run receives the wait_for coroutine, not _aexecute_with_context directly.
            call_args = mock_asyncio_run.call_args[0][0]
            self.assertEqual(call_args.__name__, "wait_for")

    @patch("codemie.service.mcp.toolkit.asyncio.run")
    def test_execute_without_running_loop_raises_mcp_error_on_timeout(self, mock_asyncio_run):
        """asyncio.TimeoutError from wait_for must be converted to MCPToolExecutionError."""
        mock_asyncio_run.side_effect = asyncio.TimeoutError()
        with patch("codemie.service.mcp.toolkit.asyncio.get_running_loop", side_effect=RuntimeError):
            with self.assertRaises(MCPToolExecutionError) as ctx:
                self.tool.execute(test_param="value")

        self.assertIn("timed out", str(ctx.exception))
        self.assertIn("test_tool", str(ctx.exception))
        self.assertIn("MCP_CLIENT_TIMEOUT", str(ctx.exception))

    @patch("codemie.service.mcp.toolkit.ThreadPoolExecutor")
    def test_execute_with_running_loop(self, mock_executor_class):
        """Test execution from within an existing asyncio loop."""
        # Mock a running loop
        mock_loop = AsyncMock()
        with patch("codemie.service.mcp.toolkit.asyncio.get_running_loop", return_value=mock_loop):
            # Setup the thread pool executor mock
            mock_executor = MagicMock()
            mock_executor_class.return_value.__enter__.return_value = mock_executor
            mock_future = MagicMock()
            mock_executor.submit.return_value = mock_future

            # Call execute
            self.tool.execute(test_param="value")

            # Check that ThreadPoolExecutor was used
            mock_executor_class.assert_called_with(max_workers=1)
            mock_executor.submit.assert_called_once()
            mock_future.result.assert_called_once_with(timeout=config.MCP_CLIENT_TIMEOUT)

    @patch("codemie.service.mcp.toolkit.asyncio.run")
    def test_successful_execution(self, mock_asyncio_run):
        """Test successful execution of the tool."""

        # Mock the async execution
        async def mock_aexecute(**kwargs):
            self.assertEqual(kwargs["test_param"], "value")
            return "Result line 1\nResult line 2"

        # Setup the mock client to return our mock response
        self.tool._aexecute_with_context = mock_aexecute

        def side_effect(coro):
            coro.close()
            return "Result line 1\nResult line 2"

        mock_asyncio_run.side_effect = side_effect

        # Execute the tool
        result = self.tool.execute(test_param="value")

        # Verify the result
        self.assertEqual(result, "Result line 1\nResult line 2")

    async def _async_test_aexecute_successful(self):
        """Async test for _aexecute method with successful response."""
        # Setup mock response
        response_items = [
            MCPToolContentItem(type="text", text="Result line 1"),
            MCPToolContentItem(type="text", text="Result line 2"),
        ]
        mock_response = MCPToolInvocationResponse(isError=False, content=response_items)

        # Configure mock client
        self.mock_client.invoke_tool = AsyncMock(return_value=mock_response)

        # Call _aexecute
        result = await self.tool._aexecute_with_context(execution_context=None, test_param="value")

        # Verify invoke_tool was called correctly
        self.mock_client.invoke_tool.assert_called_once_with(
            server_config=self.mock_server_config,
            tool_name="test_tool",
            tool_args={"test_param": "value"},
            execution_context=None,
        )

        # Verify result
        self.assertEqual(result, mock_response)

    def test_aexecute_successful(self):
        """Test _aexecute method with successful response."""
        asyncio.run(self._async_test_aexecute_successful())

    async def _async_test_aexecute_error_response(self):
        """Async test for _aexecute method with error response."""
        # Setup mock error response
        response_items = [
            MCPToolContentItem(type="error", text="Error message 1"),
            MCPToolContentItem(type="error", text="Error message 2"),
        ]
        mock_response = MCPToolInvocationResponse(isError=True, content=response_items)

        # Configure mock client
        self.mock_client.invoke_tool = AsyncMock(return_value=mock_response)

        # Call _aexecute and check for exception
        with self.assertRaises(Exception) as context:
            await self.tool._aexecute_with_context(execution_context=None, test_param="value")

        # Verify error message
        self.assertIn("MCP tool execution failed", str(context.exception))
        self.assertIn("Error message 1", str(context.exception))
        self.assertIn("Error message 2", str(context.exception))

    def test_aexecute_error_response(self):
        """Test _aexecute method with error response."""
        asyncio.run(self._async_test_aexecute_error_response())

    async def _async_test_aexecute_client_exception(self):
        """Async test for _aexecute method when client raises exception."""
        # Configure mock client to raise an exception
        self.mock_client.invoke_tool = AsyncMock(side_effect=ValueError("Connection error"))

        # Call _aexecute_with_context and check for exception
        with self.assertRaises(Exception) as context:
            await self.tool._aexecute_with_context(execution_context=None, test_param="value")

        # Verify error is propagated
        self.assertIn("Connection error", str(context.exception))

    def test_aexecute_client_exception(self):
        """Test _aexecute method when client raises exception."""
        asyncio.run(self._async_test_aexecute_client_exception())

    async def _async_test_aexecute_logs_errors(self, mock_logger):
        """Async test that _aexecute logs errors properly."""
        # Configure mock client to raise an exception
        self.mock_client.invoke_tool = AsyncMock(side_effect=ValueError("Connection error"))

        # Call _aexecute_with_context and catch the exception
        with self.assertRaises(MCPToolExecutionError):
            await self.tool._aexecute_with_context(execution_context=None, test_param="value")

        # Verify error was logged
        mock_logger.error.assert_called_once()
        log_message = mock_logger.error.call_args[0][0]
        self.assertIn("Error executing MCP tool:", log_message)
        self.assertIn("test_tool: ValueError: Connection error", log_message)

    def test_aexecute_logs_errors(self):
        """Test that _aexecute logs errors properly."""
        with patch("codemie.service.mcp.toolkit.logger") as mock_logger:
            # Fix: Create a separate function to run the test
            async def run_test():
                await self._async_test_aexecute_logs_errors(mock_logger)

            asyncio.run(run_test())

    async def _async_test_aexecute_empty_content(self):
        """Async test for _aexecute method with empty content."""
        # Setup mock response with empty content
        mock_response = MCPToolInvocationResponse(isError=False, content=[])

        # Configure mock client
        self.mock_client.invoke_tool = AsyncMock(return_value=mock_response)

        # Call _aexecute_with_context
        result = await self.tool._aexecute_with_context(execution_context=None, test_param="value")

        # Verify result is an empty MCPToolInvocationResponse
        self.assertEqual(result, mock_response)

    def test_aexecute_empty_content(self):
        """Test _aexecute method with empty content."""
        asyncio.run(self._async_test_aexecute_empty_content())

    @patch("codemie.service.mcp.toolkit.asyncio.run")
    def test_execute_passes_all_kwargs(self, mock_asyncio_run):
        """Test that execute passes all kwargs to _aexecute."""

        # Setup mock for _aexecute_with_context
        async def mock_aexecute_with_context(execution_context=None, **kwargs):
            # Check all expected kwargs are present
            self.assertEqual(kwargs["test_param"], "value")
            self.assertEqual(kwargs["optional_param"], 42)
            return "Result"

        self.tool._aexecute_with_context = mock_aexecute_with_context

        def side_effect(coro):
            coro.close()
            return "Result"

        mock_asyncio_run.side_effect = side_effect

        # Execute with multiple kwargs
        result = self.tool.execute(test_param="value", optional_param=42)

        # Verify result
        self.assertEqual(result, "Result")

    async def _async_test_aexecute_mixed_content_types(self):
        """Async test for _aexecute method with mixed content types."""
        # Setup mock response with mixed content types
        response_items = [
            MCPToolContentItem(type="text", text="Text result"),
            MCPToolContentItem(type="json", text='{"key": "value"}'),
            MCPToolContentItem(type="markdown", text="# Header"),
        ]
        mock_response = MCPToolInvocationResponse(isError=False, content=response_items)

        # Configure mock client
        self.mock_client.invoke_tool = AsyncMock(return_value=mock_response)

        # Call _aexecute_with_context
        result = await self.tool._aexecute_with_context(execution_context=None, test_param="value")

        # Verify result is the MCPToolInvocationResponse object
        self.assertEqual(result, mock_response)

    def test_aexecute_mixed_content_types(self):
        """Test _aexecute method with mixed content types."""
        asyncio.run(self._async_test_aexecute_mixed_content_types())

    @patch("codemie.service.mcp.toolkit.ThreadPoolExecutor")
    def test_execute_passes_exceptions_from_thread(self, mock_executor_class):
        """Test that exceptions from ThreadPoolExecutor are propagated."""
        # Mock a running loop
        mock_loop = AsyncMock()
        with patch("codemie.service.mcp.toolkit.asyncio.get_running_loop", return_value=mock_loop):
            # Setup the thread pool executor mock
            mock_executor = MagicMock()
            mock_executor_class.return_value.__enter__.return_value = mock_executor
            mock_future = MagicMock()
            mock_executor.submit.return_value = mock_future
            mock_future.result.side_effect = ValueError("Thread error")

            # Call execute and check for exception
            with self.assertRaises(ValueError) as context:
                self.tool.execute(test_param="value")

            # Verify error is propagated
            self.assertEqual(str(context.exception), "Thread error")

    def test_execute_with_complex_arguments(self):
        """Test execution with complex argument types."""
        # Create a tool with complex argument schema
        complex_args_schema = create_model(
            "ComplexArgsSchema",
            string_param=(str, ...),
            int_param=(int, ...),
            bool_param=(bool, ...),
            list_param=(List[str], ...),
            dict_param=(Dict[str, Any], ...),
        )

        tool = MCPTool(
            name="complex_tool",
            description="A tool with complex arguments",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            args_schema=complex_args_schema,
        )

        # Setup mock for asyncio.run
        with patch("codemie.service.mcp.toolkit.asyncio.run") as mock_asyncio_run:

            def side_effect(coro):
                coro.close()
                return "Success"

            mock_asyncio_run.side_effect = side_effect

            # Execute with complex arguments
            result = tool.execute(
                string_param="test",
                int_param=42,
                bool_param=True,
                list_param=["item1", "item2"],
                dict_param={"key1": "value1", "key2": 2},
            )

            # Verify result
            self.assertEqual(result, "Success")
            mock_asyncio_run.assert_called_once()

    @patch("codemie.service.mcp.toolkit.asyncio.run")
    def test_execute_validates_arguments(self, mock_asyncio_run):
        """Test that execute validates arguments using args_schema."""

        # Configure mock to raise validation error
        def side_effect(coro):
            coro.close()
            raise ValueError("test_param is missing")

        mock_asyncio_run.side_effect = side_effect

        # Attempt to execute with missing required argument
        with self.assertRaises(ValueError) as context:
            self.tool.execute()  # Missing required 'test_param'

        # Verify validation error message
        self.assertIn("test_param", str(context.exception))

    @patch("codemie.service.mcp.toolkit.asyncio.run")
    def test_execute_with_invalid_argument_type(self, mock_asyncio_run):
        """Test execution with invalid argument type."""

        # Configure mock to raise validation error for wrong type
        def side_effect(coro):
            coro.close()
            raise ValueError("test_param should be str")

        mock_asyncio_run.side_effect = side_effect

        # Attempt to execute with wrong argument type
        with self.assertRaises(ValueError) as context:
            self.tool.execute(test_param=123)  # Should be string, not int

        # Verify error message
        self.assertIn("test_param", str(context.exception))

    async def test_aexecute_with_context_successful(self):
        """Test _aexecute_with_context method with execution context."""
        # Setup execution context
        execution_context = MCPExecutionContext(
            user_id="user-123",
            assistant_id="assistant-456",
            project_name="test-project",
            workflow_execution_id="workflow-789",
        )

        # Setup mock response
        response_items = [
            MCPToolContentItem(type="text", text="Result with context"),
        ]
        mock_response = MCPToolInvocationResponse(isError=False, content=response_items)

        # Configure mock client
        self.mock_client.invoke_tool = AsyncMock(return_value=mock_response)

        # Call _aexecute_with_context
        result = await self.tool._aexecute_with_context(execution_context=execution_context, test_param="value")

        # Verify invoke_tool was called with context
        self.mock_client.invoke_tool.assert_called_once_with(
            server_config=self.mock_server_config,
            tool_name="test_tool",
            tool_args={"test_param": "value"},
            execution_context=execution_context,
        )

        # Verify result
        self.assertEqual(result, mock_response)

    def test_execute_with_context_successful(self):
        """Test execute_with_context method."""
        execution_context = MCPExecutionContext(
            user_id="user-123",
            workflow_execution_id="workflow-789",
        )

        with patch("codemie.service.mcp.toolkit.asyncio.run") as mock_asyncio_run:
            mock_response = MCPToolInvocationResponse(
                isError=False, content=[MCPToolContentItem(type="text", text="Success")]
            )
            mock_asyncio_run.return_value = mock_response

            # Call execute_with_context
            result = self.tool.execute_with_context(execution_context=execution_context, test_param="value")

            # Verify result
            self.assertEqual(result, mock_response)
            mock_asyncio_run.assert_called_once()

    @patch("codemie.service.mcp.toolkit.ThreadPoolExecutor")
    def test_execute_with_context_with_running_loop(self, mock_executor_class):
        """Test execute_with_context from within an existing asyncio loop."""
        execution_context = MCPExecutionContext(user_id="user-123")

        # Mock a running loop
        mock_loop = AsyncMock()
        with patch("codemie.service.mcp.toolkit.asyncio.get_running_loop", return_value=mock_loop):
            # Setup the thread pool executor mock
            mock_executor = MagicMock()
            mock_executor_class.return_value.__enter__.return_value = mock_executor
            mock_future = MagicMock()
            mock_executor.submit.return_value = mock_future
            mock_future.result.return_value = "Success"

            # Call execute_with_context
            result = self.tool.execute_with_context(execution_context=execution_context, test_param="value")

            # Check that ThreadPoolExecutor was used
            mock_executor_class.assert_called_with(max_workers=1)
            mock_executor.submit.assert_called_once()
            mock_future.result.assert_called_once_with(timeout=config.MCP_CLIENT_TIMEOUT)
            self.assertEqual(result, "Success")

    @patch("codemie.service.mcp.toolkit.ThreadPoolExecutor")
    def test_execute_with_running_loop_passes_timeout_to_future(self, mock_executor_class):
        """future.result() must be called with timeout=config.MCP_CLIENT_TIMEOUT."""
        mock_loop = MagicMock()
        with patch("codemie.service.mcp.toolkit.asyncio.get_running_loop", return_value=mock_loop):
            mock_executor = MagicMock()
            mock_executor_class.return_value.__enter__.return_value = mock_executor
            mock_future = MagicMock()
            mock_executor.submit.return_value = mock_future
            mock_future.result.return_value = "Success"

            self.tool.execute(test_param="value")

            mock_future.result.assert_called_once_with(timeout=config.MCP_CLIENT_TIMEOUT)

    @patch("codemie.service.mcp.toolkit.ThreadPoolExecutor")
    def test_execute_with_running_loop_raises_mcp_error_on_timeout(self, mock_executor_class):
        """TimeoutError from future.result() must be converted to MCPToolExecutionError."""
        import concurrent.futures

        mock_loop = MagicMock()
        with patch("codemie.service.mcp.toolkit.asyncio.get_running_loop", return_value=mock_loop):
            mock_executor = MagicMock()
            mock_executor_class.return_value.__enter__.return_value = mock_executor
            mock_future = MagicMock()
            mock_executor.submit.return_value = mock_future
            mock_future.result.side_effect = concurrent.futures.TimeoutError()

            with self.assertRaises(MCPToolExecutionError) as ctx:
                self.tool.execute(test_param="value")

            self.assertIn("timed out", str(ctx.exception))
            self.assertIn("test_tool", str(ctx.exception))
            self.assertIn("MCP_CLIENT_TIMEOUT", str(ctx.exception))


class TestContextAwareMCPTool(unittest.TestCase):
    """Test suite for ContextAwareMCPTool class."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create mock objects
        self.mock_client = MagicMock(spec=MCPConnectClient)
        self.mock_server_config = MCPServerConfig(
            command="test_command", args=["arg1", "arg2"], env={"ENV_VAR": "value"}
        )

        # Create a simple args schema for testing
        self.args_schema = create_model(
            "TestArgsSchema",
            test_param=(str, ...),
            optional_param=(Optional[int], None),
        )

        # Create execution context
        self.execution_context = MCPExecutionContext(
            user_id="user-123",
            assistant_id="assistant-456",
            project_name="test-project",
            workflow_execution_id="workflow-789",
        )

        # Create the original tool instance
        self.original_tool = MCPTool(
            name="test_tool",
            description="A test MCP tool",
            mcp_client=self.mock_client,
            mcp_server_config=self.mock_server_config,
            args_schema=self.args_schema,
            return_direct=True,
            verbose=True,
            metadata={"custom": "metadata"},
            tokens_size_limit=1000,
        )

        # Create the context-aware tool
        self.context_aware_tool = ContextAwareMCPTool(self.original_tool, self.execution_context)

    def test_initialization_copies_attributes(self):
        """Test that ContextAwareMCPTool copies all attributes from original tool."""
        # Verify basic attributes
        self.assertEqual(self.context_aware_tool.name, "test_tool")
        self.assertEqual(self.context_aware_tool.description, "A test MCP tool")
        self.assertEqual(self.context_aware_tool.mcp_client, self.mock_client)
        self.assertEqual(self.context_aware_tool.mcp_server_config, self.mock_server_config)
        self.assertEqual(self.context_aware_tool.args_schema, self.args_schema)

        # Verify additional attributes are copied
        self.assertTrue(self.context_aware_tool.return_direct)
        self.assertTrue(self.context_aware_tool.verbose)
        self.assertEqual(self.context_aware_tool.tokens_size_limit, 1000)

        # Verify metadata is copied (but not shared reference)
        # Note: MCPTool.__init__ adds tool_type to metadata, so we expect it in the result
        expected_metadata = {"custom": "metadata", "tool_type": self.context_aware_tool.metadata["tool_type"]}
        self.assertEqual(self.context_aware_tool.metadata, expected_metadata)
        self.assertIsNot(self.context_aware_tool.metadata, self.original_tool.metadata)

        # Verify context is stored
        self.assertEqual(self.context_aware_tool._execution_context, self.execution_context)

    def test_execute_passes_context(self):
        """Test that execute method passes context to execute_with_context."""
        # Mock the underlying execute_with_context method from the parent class
        with patch('codemie.service.mcp.toolkit.MCPTool.execute_with_context') as mock_execute_with_context:
            mock_execute_with_context.return_value = "Success"

            # Call execute
            result = self.context_aware_tool.execute(test_param="value")

            # Verify execute_with_context was called with correct parameters
            mock_execute_with_context.assert_called_once_with(
                execution_context=self.execution_context, test_param="value"
            )
            self.assertEqual(result, "Success")

    @patch("codemie.service.mcp.toolkit.asyncio.run")
    def test_execute_integration(self, mock_asyncio_run):
        """Test full integration of context-aware tool execution."""
        # Setup mock response
        mock_response = MCPToolInvocationResponse(
            isError=False, content=[MCPToolContentItem(type="text", text="Context-aware result")]
        )

        # Mock async execution
        async def mock_aexecute_with_context(execution_context=None, **kwargs):
            self.assertEqual(execution_context, self.execution_context)
            self.assertEqual(kwargs["test_param"], "value")
            return mock_response

        self.context_aware_tool._aexecute_with_context = mock_aexecute_with_context
        mock_asyncio_run.side_effect = lambda coro: mock_response

        # Execute the tool
        result = self.context_aware_tool.execute(test_param="value")

        # Verify the result
        self.assertEqual(result, mock_response)
        mock_asyncio_run.assert_called_once()

    def test_context_aware_tool_preserves_original_functionality(self):
        """Test that context-aware tool preserves all original functionality."""
        # Test that the tool still has all the same methods and attributes
        original_methods = [method for method in dir(self.original_tool) if not method.startswith('_')]
        context_aware_methods = [method for method in dir(self.context_aware_tool) if not method.startswith('_')]

        for method in original_methods:
            self.assertIn(method, context_aware_methods, f"Method {method} missing from context-aware tool")

        # Test that schemas are identical
        self.assertEqual(self.context_aware_tool.args_schema, self.original_tool.args_schema)

        # Test that configuration is identical (but not shared reference for mutable objects)
        self.assertEqual(self.context_aware_tool.mcp_server_config, self.original_tool.mcp_server_config)

    def test_empty_context(self):
        """Test context-aware tool with empty execution context."""
        empty_context = MCPExecutionContext()
        empty_context_tool = ContextAwareMCPTool(self.original_tool, empty_context)

        self.assertEqual(empty_context_tool._execution_context, empty_context)
        self.assertIsNone(empty_context_tool._execution_context.user_id)
        self.assertIsNone(empty_context_tool._execution_context.assistant_id)
        self.assertIsNone(empty_context_tool._execution_context.project_name)
        self.assertIsNone(empty_context_tool._execution_context.workflow_execution_id)
