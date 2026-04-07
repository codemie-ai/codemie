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
from unittest.mock import MagicMock, patch

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
