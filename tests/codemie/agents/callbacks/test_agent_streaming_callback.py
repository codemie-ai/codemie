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
import uuid

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
    mock_thought.metadata = None

    callback = AgentStreamingCallback(gen=mock_queue)
    # Register the mock thought in thoughts_storage so on_tool_end can find it
    run_id = uuid.uuid4()
    callback.thoughts_storage[str(run_id)] = mock_thought

    # Test data
    test_output = "Sample tool output"

    # Configure the mock_streamed_result to return a mock with model_dump_json method
    mock_result_instance = Mock()
    mock_result_instance.model_dump_json.return_value = '{"mocked": "json"}'
    mock_streamed_result.return_value = mock_result_instance

    # Execute
    callback.on_tool_end(test_output, run_id=run_id)

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
    mock_thought.metadata = None

    callback = AgentStreamingCallback(gen=mock_queue)
    # Register the mock thought in thoughts_storage so on_tool_end can find it
    run_id = uuid.uuid4()
    callback.thoughts_storage[str(run_id)] = mock_thought

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
    callback.on_tool_end(test_output, run_id=run_id)

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


@patch('codemie.agents.callbacks.agent_streaming_callback.set_logging_info')
@patch('codemie.agents.callbacks.agent_streaming_callback.StreamedGenerationResult')
def test_on_llm_error_with_execution_error_field(mock_streamed_result, mock_logging):
    """Test the on_llm_error method includes execution_error field."""
    # Setup
    mock_queue = MagicMock()
    mock_thought = Mock(spec=Thought)

    callback = AgentStreamingCallback(gen=mock_queue)
    run_id = uuid.uuid4()
    callback.thoughts_storage[str(run_id)] = mock_thought

    # Test data
    test_error = Exception("LLM generation failed")

    # Configure the mock_streamed_result
    mock_result_instance = Mock()
    mock_result_instance.model_dump_json.return_value = '{"mocked": "json"}'
    mock_streamed_result.return_value = mock_result_instance

    # Execute
    callback.on_llm_error(test_error, run_id=run_id)

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


# ---------------------------------------------------------------------------
# Tool lifecycle tests (immediate-send architecture)
# ---------------------------------------------------------------------------


@patch('codemie.agents.callbacks.agent_streaming_callback.StreamedGenerationResult')
def test_on_tool_start_sends_immediately(mock_streamed_result) -> None:
    """on_tool_start always sends a thought immediately."""
    mock_queue = MagicMock()
    mock_result_instance = Mock()
    mock_result_instance.model_dump_json.return_value = '{"mocked": "json"}'
    mock_streamed_result.return_value = mock_result_instance

    callback = AgentStreamingCallback(gen=mock_queue)
    run_id = uuid.uuid4()

    callback.on_tool_start({"name": "some_tool"}, "input text", run_id=run_id)

    mock_queue.send.assert_called_once_with('{"mocked": "json"}')
    assert str(run_id) in callback.thoughts_storage


@patch('codemie.agents.callbacks.agent_streaming_callback.StreamedGenerationResult')
def test_on_tool_end_sends_immediately(mock_streamed_result) -> None:
    """on_tool_end sends the thought immediately and removes it from storage."""
    mock_queue = MagicMock()
    mock_result_instance = Mock()
    mock_result_instance.model_dump_json.return_value = '{"mocked": "json"}'
    mock_streamed_result.return_value = mock_result_instance

    callback = AgentStreamingCallback(gen=mock_queue)
    run_id = uuid.uuid4()
    mock_thought = Mock(spec=Thought)
    mock_thought.metadata = None
    callback.thoughts_storage[str(run_id)] = mock_thought

    callback.on_tool_end("result", run_id=run_id)

    mock_queue.send.assert_called_once_with('{"mocked": "json"}')
    assert mock_thought.in_progress is False
    assert str(run_id) not in callback.thoughts_storage


def test_on_tool_end_unknown_run_id_is_noop() -> None:
    mock_queue = MagicMock()
    callback = AgentStreamingCallback(gen=mock_queue)

    callback.on_tool_end("some output", run_id=uuid.uuid4())

    mock_queue.send.assert_not_called()


@patch('codemie.agents.callbacks.agent_streaming_callback.set_logging_info')
@patch('codemie.agents.callbacks.agent_streaming_callback.StreamedGenerationResult')
def test_on_tool_error_sends_with_error_flag(mock_streamed_result, mock_logging) -> None:
    """on_tool_error sends the thought with error=True immediately."""
    mock_queue = MagicMock()
    mock_result_instance = Mock()
    mock_result_instance.model_dump_json.return_value = '{"mocked": "json"}'
    mock_streamed_result.return_value = mock_result_instance

    callback = AgentStreamingCallback(gen=mock_queue)
    run_id = uuid.uuid4()
    mock_thought = Mock(spec=Thought)
    mock_thought.metadata = None
    callback.thoughts_storage[str(run_id)] = mock_thought

    callback.on_tool_error(Exception("tool failed"), run_id=run_id)

    mock_queue.send.assert_called_once_with('{"mocked": "json"}')
    assert mock_thought.error is True
    assert mock_thought.in_progress is False
    assert str(run_id) not in callback.thoughts_storage


@patch('codemie.agents.callbacks.agent_streaming_callback.set_logging_info')
def test_on_tool_error_unknown_run_id_is_noop(mock_logging) -> None:
    mock_queue = MagicMock()
    callback = AgentStreamingCallback(gen=mock_queue)

    callback.on_tool_error(Exception("error"), run_id=uuid.uuid4())

    mock_queue.send.assert_not_called()


# ---------------------------------------------------------------------------
# set_context tests
# ---------------------------------------------------------------------------


def test_set_context_updates_default_storage() -> None:
    """set_context with author=None updates top-level context and parent_id."""
    callback = AgentStreamingCallback(gen=MagicMock())
    ctx = {"key": "value"}
    parent_id = str(uuid.uuid4())

    callback.set_context(ctx, parent_thought_id=parent_id)

    assert callback.context == ctx
    assert callback.parent_id == parent_id


def test_set_context_with_author_creates_storage() -> None:
    """set_context with a new author creates per-author storage without touching defaults."""
    callback = AgentStreamingCallback(gen=MagicMock())
    parent_id = str(uuid.uuid4())

    callback.set_context({}, parent_thought_id=parent_id, author="agent_x")

    assert "agent_x" in callback._storages
    assert callback._storages["agent_x"].parent_id == parent_id
    assert callback.context is None


def test_set_context_with_author_updates_existing_storage() -> None:
    """set_context with an existing author updates parent_id in-place, preserving thoughts."""
    callback = AgentStreamingCallback(gen=MagicMock())
    run_id = uuid.uuid4()
    callback._storages["agent_x"] = callback._get_storage("agent_x")
    callback._storages["agent_x"].create_thought(run_id=run_id, tool_name="some_tool")

    new_parent_id = str(uuid.uuid4())
    callback.set_context({}, parent_thought_id=new_parent_id, author="agent_x")

    assert callback._storages["agent_x"].parent_id == new_parent_id
    assert str(run_id) in callback._storages["agent_x"]
