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

import pytest
from unittest.mock import patch, MagicMock, Mock

from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.thread import ThreadedGenerator
from codemie.service.mcp.models import MCPToolInvocationResponse, MCPToolContentItem
from codemie.chains.base import Thought


@pytest.fixture
def real_thought_queue() -> ThoughtQueue:
    return ThoughtQueue()


@pytest.fixture
def real_thread_generator() -> ThreadedGenerator:
    return ThreadedGenerator()


def test_agent_streaming_callback_with_thought_queue(real_thought_queue: ThoughtQueue) -> None:
    callback = AgentStreamingCallback(gen=real_thought_queue)

    assert callback.gen == real_thought_queue, "ThoughtQueue was not properly assigned to the callback"
    assert isinstance(callback.gen, ThoughtQueue), "gen is not an instance of ThoughtQueue"


def test_agent_streaming_callback_with_threaded_generator(real_thread_generator: ThreadedGenerator) -> None:
    callback = AgentStreamingCallback(gen=real_thread_generator)

    assert callback.gen == real_thread_generator, "ThreadedGenerator was not properly assigned to the callback"
    assert isinstance(callback.gen, ThreadedGenerator), "gen is not an instance of ThreadedGenerator"


def test_agent_streaming_callback_debug_with_thought_queue(real_thought_queue: ThoughtQueue) -> None:
    callback = AgentStreamingCallback(gen=real_thought_queue)
    with patch('codemie.agents.callbacks.agent_streaming_callback.logger') as mock_logger_debug:
        callback._debug("Test debug message")

        mock_logger_debug.debug.assert_called_once_with("Test debug message")


def test_agent_streaming_callback_debug_with_threaded_generator(real_thread_generator: ThreadedGenerator) -> None:
    callback = AgentStreamingCallback(gen=real_thread_generator)
    with patch('codemie.agents.callbacks.agent_streaming_callback.logger') as mock_logger_debug:
        callback._debug("Test debug message")

    mock_logger_debug.debug.assert_called_once_with("Test debug message")


def test_tool_result_preprocessing_with_string_input():
    """Test that string inputs are returned as-is by the _tool_result_preprocessing method."""
    # Setup
    callback = AgentStreamingCallback(gen=ThoughtQueue())
    test_string = "Test string result"

    # Execute
    result = callback._tool_result_preprocessing(test_string)

    # Verify
    assert result == test_string, "String input should be returned unmodified"


def test_tool_result_preprocessing_with_mcp_tool_invocation_response():
    """Test that MCPToolInvocationResponse objects are properly converted to strings."""
    # Setup
    callback = AgentStreamingCallback(gen=ThoughtQueue())

    # Create test data
    content_items = [
        MCPToolContentItem(type="text", text="Text content item"),
        MCPToolContentItem(type="image", data="base64_data", mimeType="image/png"),
        MCPToolContentItem(type="text", text="Another text content"),
    ]
    response = MCPToolInvocationResponse(content=content_items, isError=False)

    # Execute
    result = callback._tool_result_preprocessing(response)

    # Verify
    expected_result = "\n".join(
        ["Text content item", "![Screenshot](data:image/png;base64,base64_data)", "Another text content"]
    )
    assert result == expected_result, "MCPToolInvocationResponse should be converted to string with proper formatting"


def test_tool_result_preprocessing_with_other_object_types():
    """Test that objects other than strings and MCPToolInvocationResponse are returned as-is."""
    # Setup
    callback = AgentStreamingCallback(gen=ThoughtQueue())

    # Test data
    test_list = [1, 2, 3]
    test_dict = {"key": "value"}
    test_int = 42

    # Execute and verify
    assert callback._tool_result_preprocessing(test_list) == test_list, "Lists should be returned unmodified"
    assert callback._tool_result_preprocessing(test_dict) == test_dict, "Dictionaries should be returned unmodified"
    assert callback._tool_result_preprocessing(test_int) == test_int, "Integers should be returned unmodified"


@patch('codemie.agents.callbacks.agent_streaming_callback.StreamedGenerationResult')
def test_on_tool_end_with_string_input(mock_streamed_result):
    """Test the on_tool_end method with string input."""
    # Setup
    mock_queue = MagicMock()

    # Create a proper Thought mock instead of a simple MagicMock
    mock_thought = Mock(spec=Thought)

    callback = AgentStreamingCallback(gen=mock_queue)
    # Replace the _current_thought property with our mock_thought
    callback._current_thought = mock_thought

    # Test data
    test_output = "Sample tool output"

    # Configure the mock_streamed_result to return a mock with model_dump_json method
    mock_result_instance = Mock()
    mock_result_instance.model_dump_json.return_value = '{"mocked": "json"}'
    mock_streamed_result.return_value = mock_result_instance

    # Execute
    callback.on_tool_end(test_output)

    # Verify
    # Check that message was set on the thought
    expected_message = f"{test_output} \n\n"
    assert hasattr(mock_thought, 'message'), "message attribute should have been set on the thought"
    assert (
        mock_thought.message == expected_message
    ), f"Expected message: {expected_message}, got: {mock_thought.message}"

    # Check that in_progress was set to False
    assert mock_thought.in_progress is False, "in_progress should have been set to False"

    # Check that gen.send was called with the expected JSON
    mock_queue.send.assert_called_once_with('{"mocked": "json"}')

    # Verify StreamedGenerationResult was constructed correctly
    mock_streamed_result.assert_called_once()
    args, kwargs = mock_streamed_result.call_args
    assert kwargs['thought'] is mock_thought


@patch('codemie.agents.callbacks.agent_streaming_callback.StreamedGenerationResult')
def test_on_tool_end_with_mcp_tool_invocation_response(mock_streamed_result):
    """Test the on_tool_end method with MCPToolInvocationResponse input."""
    # Setup
    mock_queue = MagicMock()

    # Create a proper Thought mock
    mock_thought = Mock(spec=Thought)

    callback = AgentStreamingCallback(gen=mock_queue)
    callback._current_thought = mock_thought

    # Test data
    content_items = [
        MCPToolContentItem(type="text", text="Text content item"),
        MCPToolContentItem(type="text", text="Another text content"),
    ]
    test_output = MCPToolInvocationResponse(content=content_items, isError=False)

    # Configure the mock_streamed_result
    mock_result_instance = Mock()
    mock_result_instance.model_dump_json.return_value = '{"mocked": "json"}'
    mock_streamed_result.return_value = mock_result_instance

    # Execute
    callback.on_tool_end(test_output)

    # Verify
    # Check that message was set on the thought
    expected_content = "Text content item\nAnother text content"
    expected_message = f"{expected_content} \n\n"
    assert hasattr(mock_thought, 'message'), "message attribute should have been set on the thought"
    assert (
        mock_thought.message == expected_message
    ), f"Expected message: {expected_message}, got: {mock_thought.message}"

    # Check that in_progress was set to False
    assert mock_thought.in_progress is False, "in_progress should have been set to False"

    # Check that gen.send was called with the expected JSON
    mock_queue.send.assert_called_once_with('{"mocked": "json"}')


def test_mcp_tool_invocation_response_with_error_flag():
    """Test that the _tool_result_preprocessing method handles MCPToolInvocationResponse with error flag correctly."""
    # Setup
    callback = AgentStreamingCallback(gen=ThoughtQueue())

    # Create test data
    content_items = [
        MCPToolContentItem(type="text", text="Error: Something went wrong"),
        MCPToolContentItem(type="text", text="Additional error details"),
    ]
    response = MCPToolInvocationResponse(content=content_items, isError=True)

    # Execute
    result = callback._tool_result_preprocessing(response)

    # Verify
    expected_result = "Error: Something went wrong\nAdditional error details"
    assert result == expected_result, "Error content from MCPToolInvocationResponse should be properly formatted"


def test_empty_mcp_tool_invocation_response():
    """Test that the _tool_result_preprocessing method handles empty MCPToolInvocationResponse correctly."""
    # Setup
    callback = AgentStreamingCallback(gen=ThoughtQueue())

    # Create test data
    empty_response = MCPToolInvocationResponse(content=[], isError=False)

    # Execute
    result = callback._tool_result_preprocessing(empty_response)

    # Verify
    assert result == "", "Empty content list should result in an empty string"


@patch('codemie.agents.callbacks.agent_streaming_callback.StreamedGenerationResult')
def test_on_llm_error_with_execution_error_field(mock_streamed_result):
    """Test the on_llm_error method includes execution_error field."""
    # Setup
    mock_queue = MagicMock()
    mock_thought = Mock(spec=Thought)

    callback = AgentStreamingCallback(gen=mock_queue)
    callback._current_thought = mock_thought

    # Test data
    test_error = Exception("LLM generation failed")

    # Configure the mock_streamed_result
    mock_result_instance = Mock()
    mock_result_instance.model_dump_json.return_value = '{"mocked": "json"}'
    mock_streamed_result.return_value = mock_result_instance

    # Execute
    callback.on_llm_error(test_error)

    # Verify
    # Check that message was set on the thought
    assert hasattr(mock_thought, "message"), "message attribute should have been set"
    assert mock_thought.message == "LLM generation failed"

    # Check that error and in_progress flags were set correctly
    assert mock_thought.error is True, "error should have been set to True"
    assert mock_thought.in_progress is False, "in_progress should have been set to False"

    # Check that gen.send was called
    mock_queue.send.assert_called_once_with('{"mocked": "json"}')

    # Verify StreamedGenerationResult was constructed with execution_error="stacktrace"
    mock_streamed_result.assert_called_once()
    args, kwargs = mock_streamed_result.call_args
    assert kwargs["thought"] is mock_thought
    assert kwargs["context"] == callback.context
    assert kwargs["execution_error"] == "stacktrace"
