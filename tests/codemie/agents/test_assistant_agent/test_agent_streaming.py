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

import json
import pytest
from unittest.mock import MagicMock, Mock, patch

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.security.user import User
from codemie.core.thread import ThreadedGenerator


@pytest.fixture
def test_setup():
    # Create mock request
    mock_request = MagicMock(spec=AssistantChatRequest)
    mock_request.text = "Test request"
    mock_request.file_name = None
    mock_request.conversation_id = "test-conversation-id"
    mock_request.history = []
    mock_request.system_prompt = None  # Add system_prompt attribute
    mock_request.metadata = {}
    mock_request.version = None  # Add version attribute for version tracking

    # Create mock user
    mock_user = MagicMock(spec=User)
    mock_user.id = "test-user-id"
    mock_user.name = "Test User"
    mock_user.username = "Test User"

    # Create mock ThreadedGenerator
    mock_thread_generator = MagicMock(spec=ThreadedGenerator)
    mock_thread_generator.is_closed.return_value = False

    # Setup agent with mocks and patch init_agent to avoid initialization
    with patch.object(AIToolsAgent, 'init_agent', return_value=MagicMock()):
        with patch('codemie.agents.assistant_agent.get_llm_by_credentials', return_value=MagicMock()):
            agent = AIToolsAgent(
                agent_name="test_agent",
                description="Test agent",
                tools=[],  # Empty tools for simplicity
                request=mock_request,
                system_prompt="Test system prompt",
                request_uuid="test-uuid",
                user=mock_user,
                llm_model="gpt-3.5-turbo",
                thread_generator=mock_thread_generator,
            )

    return {
        'agent': agent,
        'mock_request': mock_request,
        'mock_user': mock_user,
        'mock_thread_generator': mock_thread_generator,
    }


@patch('codemie.agents.assistant_agent.logger')
@patch('codemie.agents.assistant_agent.extract_text_from_llm_output', return_value="Final test output")
@patch('codemie.enterprise.langfuse.get_langfuse_callback_handler', return_value=None)
def test_agent_streaming_processes_different_chunks(mock_langfuse_handler, mock_extract_text, mock_logger, test_setup):
    agent = test_setup['agent']

    # Setup mock agent_executor with different chunk types
    mock_action = MagicMock()
    mock_action.tool = "test_tool"
    mock_action.tool_input = {"param": "value"}

    mock_observation = MagicMock()
    mock_observation.observation = "Test observation"

    # Create chunks to be returned by stream
    action_chunk = {"actions": [mock_action]}
    step_chunk = {"steps": [mock_observation]}
    output_chunk = {"output": "Final test output"}

    # Mock agent_executor and its stream method
    agent.agent_executor = MagicMock()
    agent.agent_executor.stream.return_value = [action_chunk, step_chunk, output_chunk]

    # Mock _get_inputs method
    agent._get_inputs = MagicMock(return_value={"input": "test input"})

    # Create chunks collector for testing
    chunks_collector = []

    # Call the method under test
    agent._agent_streaming(chunks_collector)
    config = {}
    # Verify agent_executor.stream was called with expected inputs
    agent.agent_executor.stream.assert_called_once_with({"input": "test input"}, config=config)

    # Check that logger was called appropriately for each chunk type
    mock_logger.info.assert_any_call(f"Calling Tool: {mock_action.tool} with input {mock_action.tool_input}")
    mock_logger.debug.assert_any_call("Tool Result: Test observation")
    mock_logger.debug.assert_any_call("Final result is: Final test output")

    # Verify output was appended to chunks_collector
    assert chunks_collector == ["Final test output"]

    # Verify that extract_text_from_llm_output was called
    mock_extract_text.assert_called_once_with("Final test output")


@patch('codemie.agents.assistant_agent.logger')
def test_agent_streaming_with_empty_chunk(mock_logger, test_setup):
    agent = test_setup['agent']

    # Mock agent_executor with an empty chunk
    agent.agent_executor = MagicMock()
    agent.agent_executor.stream.return_value = [{}]  # Empty chunk
    agent._get_inputs = MagicMock(return_value={"input": "test input"})

    chunks_collector = []
    agent._agent_streaming(chunks_collector)

    # Verify stream was called
    agent.agent_executor.stream.assert_called_once()

    # Verify chunks_collector remains empty since the chunk had no output
    assert chunks_collector == []

    # No logger calls should occur for process_error since we didn't provide output
    mock_logger.error.assert_not_called()


@patch('codemie.agents.assistant_agent.logger')
def test_agent_streaming_with_unknown_chunk_format(mock_logger, test_setup):
    agent = test_setup['agent']

    # Mock agent_executor with an unknown chunk format
    agent.agent_executor = MagicMock()
    agent.agent_executor.stream.return_value = [{"unknown_field": "unknown value"}]
    agent._get_inputs = MagicMock(return_value={"input": "test input"})

    chunks_collector = []
    agent._agent_streaming(chunks_collector)

    # Verify stream was called
    agent.agent_executor.stream.assert_called_once()

    # Verify chunks_collector remains empty since the chunk format was unknown
    assert chunks_collector == []

    # The process_error function is called with None when chunk has no 'output'
    # This reflects the actual implementation behavior
    mock_logger.error.assert_called_once_with("Got tool error: None")


@patch('codemie.agents.assistant_agent.logger')
def test_agent_streaming_breaks_when_generator_closed(mock_logger, test_setup):
    agent = test_setup['agent']
    mock_thread_generator = test_setup['mock_thread_generator']

    # Setup chunks that should be processed
    mock_action = MagicMock()
    mock_action.tool = "test_tool"
    mock_action.tool_input = {"param": "value"}

    action_chunk = {"actions": [mock_action]}
    output_chunk = {"output": "Final test output"}

    # Configure thread_generator to report closed at second check
    # First check: False - allow first chunk processing
    # Second check: True - break before processing second chunk
    mock_thread_generator.is_closed.side_effect = [False, True]

    # Set up agent_executor to return multiple chunks
    agent.agent_executor = MagicMock()
    agent.agent_executor.stream.return_value = [action_chunk, output_chunk]
    agent._get_inputs = MagicMock(return_value={"input": "test input"})

    chunks_collector = []
    agent._agent_streaming(chunks_collector)

    # Verify that is_closed() was checked twice
    assert mock_thread_generator.is_closed.call_count == 2

    # First verify the log for the action was called (first chunk processed)
    mock_logger.info.assert_any_call(f"Calling Tool: {mock_action.tool} with input {mock_action.tool_input}")

    # Then verify the stopping message was logged
    mock_logger.info.assert_any_call("Stopping agent test_agent, user is disconnected")

    # Verify output wasn't appended to chunks_collector (second chunk not processed)
    assert chunks_collector == []


@patch('codemie.agents.assistant_agent.logger')
@patch('codemie.agents.assistant_agent.traceback.format_exc')
def test_stream_method_sets_execution_error_on_exception(mock_format_exc, mock_logger, test_setup):
    """Test that stream method sets execution_error='stacktrace' when an exception occurs."""
    # Setup
    agent = test_setup["agent"]
    mock_thread_generator = test_setup["mock_thread_generator"]

    # Mock the exception and stack trace
    mock_format_exc.return_value = "Mocked stack trace"
    test_exception = Exception("Test exception")

    # Mock _agent_streaming to raise an exception
    agent._agent_streaming = Mock(side_effect=test_exception)

    # Mock time() to return consistent values
    with patch("codemie.agents.assistant_agent.time") as mock_time:
        mock_time.side_effect = [0.0, 1.0]  # execution_start=0.0, current_time=1.0

        agent.stream()

    # Verify that send was called with execution_error="stacktrace"
    assert mock_thread_generator.send.call_count == 1
    call_args = mock_thread_generator.send.call_args[0][0]

    # Parse the JSON to verify execution_error field
    import json

    result_data = json.loads(call_args)
    assert result_data["execution_error"] is None
    assert result_data["last"] is True
    assert result_data["time_elapsed"] == 1.0
    assert "AI Agent run failed with error:" in result_data["generated"]


@patch('codemie.agents.assistant_agent.config.HIDE_AGENT_STREAMING_EXCEPTIONS', True)
@patch('codemie.agents.assistant_agent.config.CUSTOM_STACKTRACE_MESSAGE', 'Custom error message for production')
@patch('codemie.agents.assistant_agent.logger')
@patch('codemie.agents.assistant_agent.traceback.format_exc')
def test_stream_method_hides_stacktrace_when_flag_enabled(mock_format_exc, mock_logger, test_setup):
    """Test that stream method shows custom message when HIDE_AGENT_STREAMING_EXCEPTIONS is True."""
    # Setup
    agent = test_setup["agent"]
    mock_thread_generator = test_setup["mock_thread_generator"]

    # Mock the exception and stack trace
    mock_format_exc.return_value = "Real stack trace with sensitive info"
    test_exception = Exception("Test exception")

    # Mock time() to return consistent values
    with patch("codemie.agents.assistant_agent.time") as mock_time:
        mock_time.side_effect = [0.0, 1.0]  # execution_start=0.0, current_time=1.0

        # Patch _agent_streaming to raise exception
        with patch.object(agent, "_agent_streaming", side_effect=test_exception):
            # Execute
            agent.stream()

    # Verify that send was called with execution_error="stacktrace"
    assert mock_thread_generator.send.call_count == 1
    call_args = mock_thread_generator.send.call_args[0][0]

    # Parse the JSON to verify execution_error field
    result_data = json.loads(call_args)

    # Verify execution_error is set
    assert result_data["execution_error"] == "stacktrace"
    assert result_data["last"] is True
    assert result_data["time_elapsed"] == 1.0

    # Verify the generated content shows custom message, NOT the actual error details
    assert result_data["generated"] == "Custom error message for production"

    # Verify that sensitive information is NOT exposed
    assert "Test exception" not in result_data["generated"]
    assert "Real stack trace with sensitive info" not in result_data["generated"]
    assert "AI Agent run failed with error:" not in result_data["generated"]


@patch('codemie.agents.assistant_agent.config.HIDE_AGENT_STREAMING_EXCEPTIONS', True)
@patch('codemie.agents.assistant_agent.config.CUSTOM_GUARDRAILS_MESSAGE', 'Content prohibited')
@patch('codemie.agents.assistant_agent.logger')
@patch('codemie.agents.assistant_agent.traceback.format_exc')
def test_stream_method_hides_guardrails_when_flag_enabled(mock_format_exc, mock_logger, test_setup):
    """Test that stream method shows custom message when HIDE_AGENT_STREAMING_EXCEPTIONS is True."""
    agent = test_setup["agent"]
    mock_thread_generator = test_setup["mock_thread_generator"]

    # Mock time() to return consistent values
    with patch("codemie.agents.assistant_agent.time") as mock_time:
        mock_time.side_effect = [0.0, 1.0]  # execution_start=0.0, current_time=1.0

        # Patch _agent_streaming to raise exception
        with patch.object(
            agent,
            "_process_chunks",
            return_value=("Content prohibited", "guardrails"),
        ):
            agent.stream()

    # Verify that send was called with execution_error="guardrails"
    assert mock_thread_generator.send.call_count == 1
    call_args = mock_thread_generator.send.call_args[0][0]

    # Parse the JSON to verify execution_error field
    result_data = json.loads(call_args)

    # Verify execution_error is set
    assert result_data["execution_error"] == "guardrails"
    assert result_data["last"] is True
    assert result_data["time_elapsed"] == 1.0

    # Verify the generated content shows custom message, NOT the actual error details
    assert result_data["generated"] == "Content prohibited"


@pytest.mark.parametrize(
    "chunks_collector, guardrails_message, stacktrace_message, expected_generated, expected_execution_error",
    [
        (
            # Guardrails case
            [
                "some intermediate output"
                "{\"error_type\": \"HATE_SPEECH\", \"reason\": \"Hate speech detected (keyword: 'kill all')\", \"guardrail\": \"hate_speech\", \"stage\": \"pre_call\"}",
            ],
            "Content prohibited",
            "Internal error occurred",
            "Content prohibited",
            "guardrails",
        ),
        (
            # Stacktrace case
            [
                "AI Agent run failed with error: Exception: boom",
            ],
            "Content prohibited",
            "Internal error occurred",
            "Internal error occurred",
            "stacktrace",
        ),
    ],
)
def test_process_chunks(
    test_setup,
    chunks_collector,
    guardrails_message,
    stacktrace_message,
    expected_generated,
    expected_execution_error,
):
    agent = test_setup["agent"]

    class DummyConfig:
        CUSTOM_GUARDRAILS_MESSAGE = guardrails_message
        CUSTOM_STACKTRACE_MESSAGE = stacktrace_message
        HIDE_AGENT_STREAMING_EXCEPTIONS = True

    generated, execution_error = agent._process_chunks(
        chunks_collector=chunks_collector,
        config=DummyConfig,
    )

    assert generated == expected_generated
    assert execution_error == expected_execution_error


# ---------------------------------------------------------------------------
# _process_chunks: LLM error code propagation (EPMCDEM-226)
# When an LLM error code is provided it takes precedence over
# HIDE_AGENT_STREAMING_EXCEPTIONS — the friendly message is already safe.
# ---------------------------------------------------------------------------


def test_process_chunks_llm_error_code_takes_precedence_over_hide_flag(test_setup):
    """LLM error code must be returned even when HIDE_AGENT_STREAMING_EXCEPTIONS is True."""
    agent = test_setup["agent"]

    class DummyConfig:
        CUSTOM_GUARDRAILS_MESSAGE = "Custom guardrails"
        CUSTOM_STACKTRACE_MESSAGE = "Custom stacktrace"
        HIDE_AGENT_STREAMING_EXCEPTIONS = True

    universal_msg = "We're experiencing a temporary issue with the AI service."
    chunks = ["partial output", universal_msg]

    generated, execution_error = agent._process_chunks(
        chunks_collector=chunks,
        config=DummyConfig,
        llm_error_code="llm_rate_limit",
    )

    # LLM error code propagated, NOT replaced with "stacktrace"
    assert execution_error == "llm_rate_limit"
    # Universal message preserved, NOT replaced with CUSTOM_STACKTRACE_MESSAGE
    assert generated == f"partial output{universal_msg}"


def test_process_chunks_guardrails_still_work_without_llm_error(test_setup):
    """Guardrails detection must still work when there is no LLM error code."""
    agent = test_setup["agent"]

    class DummyConfig:
        CUSTOM_GUARDRAILS_MESSAGE = "Content prohibited"
        CUSTOM_STACKTRACE_MESSAGE = "Internal error"
        HIDE_AGENT_STREAMING_EXCEPTIONS = True

    chunks = ['{"guardrail": "hate_speech", "stage": "pre_call"}']

    generated, execution_error = agent._process_chunks(
        chunks_collector=chunks,
        config=DummyConfig,
        llm_error_code=None,
    )

    assert execution_error == "guardrails"
    assert generated == "Content prohibited"


def test_process_chunks_no_error_code_no_hide_flag_returns_raw_chunks(test_setup):
    """Without LLM error and without HIDE flag, raw chunks are returned with no error code."""
    agent = test_setup["agent"]

    class DummyConfig:
        CUSTOM_GUARDRAILS_MESSAGE = "Custom guardrails"
        CUSTOM_STACKTRACE_MESSAGE = "Custom stacktrace"
        HIDE_AGENT_STREAMING_EXCEPTIONS = False

    chunks = ["some", " output"]

    generated, execution_error = agent._process_chunks(
        chunks_collector=chunks,
        config=DummyConfig,
    )

    assert generated == "some output"
    assert execution_error is None
