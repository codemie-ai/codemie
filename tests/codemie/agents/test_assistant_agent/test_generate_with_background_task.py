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

from langchain_core.tools import BaseTool
from typing import Any, Dict

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.chains.base import GenerationResult
from codemie.core.constants import BackgroundTaskStatus
from codemie.core.models import AssistantChatRequest
from codemie.core.thread import ThreadedGenerator
from codemie.rest_api.security.user import User


class MockTool(BaseTool):
    """Mock tool for testing."""

    name: str = "mock_tool"  # Add type annotation as required by Pydantic
    description: str = "A mock tool for testing purposes"

    def _run(self, *args: Any, **kwargs: Dict[str, Any]) -> str:
        return "Mock tool result"


@pytest.fixture
def mock_thread_generator():
    return MagicMock(spec=ThreadedGenerator)


@pytest.fixture
def mock_request():
    mock_request = MagicMock(spec=AssistantChatRequest)
    mock_request.system_prompt = "example_system_prompt"
    mock_request.conversation_id = "example_conversation_id"
    mock_request.text = "example task text"
    mock_request.file_name = None
    mock_request.history = []
    return mock_request


@pytest.fixture
def mock_user():
    mock_user = MagicMock(spec=User)
    mock_user.id = "example_user_id"
    mock_user.username = "example_user_id"
    mock_user.name = "Test User"
    return mock_user


@pytest.fixture
def mock_background_task_service():
    with patch('codemie.agents.assistant_agent.BackgroundTasksService') as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        yield mock_instance


class TestGenerateWithBackgroundTask:
    @pytest.mark.parametrize(
        "is_pure_chain,agent_response,expected_output",
        [
            (True, "This is the generated response", "This is the generated response"),
            (False, {"output": "This is the agent output"}, "This is the agent output"),
        ],
        ids=["pure_chain", "regular_agent"],
    )
    @patch('codemie.agents.assistant_agent.time')
    @patch('codemie.agents.assistant_agent.calculate_tokens')
    def test_generate_with_background_task_success(
        self,
        mock_calculate_tokens,
        mock_time,
        is_pure_chain,
        agent_response,
        expected_output,
        mock_thread_generator,
        mock_request,
        mock_user,
        mock_background_task_service,
    ):
        # Setup
        background_task_id = "test-task-123"

        # Set up time mock for execution time measurement
        mock_time.side_effect = [100.0, 105.0]  # start_time and end_time for time elapsed calculation
        mock_calculate_tokens.return_value = 42  # Mock token calculation

        # Create agent with appropriate patching to avoid init_agent execution
        with patch.object(AIToolsAgent, 'init_agent', return_value=MagicMock()):
            agent = AIToolsAgent(
                agent_name="TestAgent",
                description="A test agent",
                tools=[] if is_pure_chain else [MockTool()],
                request=mock_request,
                system_prompt="Test system prompt",
                request_uuid="test-uuid-123",
                user=mock_user,
                llm_model="gpt-3.5-turbo",
                thread_generator=mock_thread_generator,
            )

        # Set up mocks based on agent type
        if is_pure_chain:
            agent.is_pure_chain = MagicMock(return_value=True)
            mock_response = MagicMock()
            mock_response.generated = agent_response
            agent.agent_executor.generate = MagicMock(return_value=mock_response)
        else:
            agent.is_pure_chain = MagicMock(return_value=False)
            agent._invoke_agent = MagicMock(return_value=agent_response)
            agent._get_inputs = MagicMock(return_value={})

        # Execute
        result = agent.generate(background_task_id=background_task_id)

        # Verify
        assert isinstance(result, GenerationResult)
        assert result.generated == expected_output
        assert result.time_elapsed == 5.0  # (105.0 - 100.0)
        assert result.tokens_used == 42

        # Verify background task service was updated correctly
        mock_background_task_service.update.assert_called_once_with(
            task_id=background_task_id, status=BackgroundTaskStatus.COMPLETED, final_output=expected_output
        )

    @patch('codemie.agents.assistant_agent.time')
    @patch('codemie.agents.assistant_agent.traceback')
    def test_generate_with_background_task_error(
        self,
        mock_traceback,
        mock_time,
        mock_thread_generator,
        mock_request,
        mock_user,
        mock_background_task_service,
    ):
        # Setup
        background_task_id = "test-task-123"

        # Set up time mock for execution time measurement
        mock_time.side_effect = [100.0, 106.0]  # start_time and end_time for time elapsed calculation
        mock_traceback.format_exc.return_value = "Traceback (most recent call last):\n  File 'example.py', line 123\nValueError: Simulated error in agent execution"

        # Create agent with appropriate patching to avoid init_agent execution
        with patch.object(AIToolsAgent, 'init_agent', return_value=MagicMock()):
            agent = AIToolsAgent(
                agent_name="TestAgent",
                description="A test agent",
                tools=[],
                request=mock_request,
                system_prompt="Test system prompt",
                request_uuid="test-uuid-123",
                user=mock_user,
                llm_model="gpt-3.5-turbo",
                thread_generator=mock_thread_generator,
            )

        # Set up agent to raise exception during execution
        agent.is_pure_chain = MagicMock(return_value=True)
        agent.agent_executor.generate = MagicMock(side_effect=ValueError("Simulated error in agent execution"))

        # Execute
        with patch('codemie.agents.assistant_agent.logger'):
            result = agent.generate(background_task_id=background_task_id)

        # Verify
        assert isinstance(result, GenerationResult)
        assert "AI Agent run failed with error:" in result.generated
        assert "ValueError: Simulated error in agent execution" in result.generated
        assert "Traceback" in result.generated
        assert result.time_elapsed == 6.0  # (106.0 - 100.0)

        # Verify background task service was updated correctly with error status
        mock_background_task_service.update.assert_called_once()
        update_args = mock_background_task_service.update.call_args[1]
        assert update_args["task_id"] == background_task_id
        assert update_args["status"] == BackgroundTaskStatus.FAILED
        assert "AI Agent run failed with error:" in update_args["final_output"]
        assert "ValueError: Simulated error in agent execution" in update_args["final_output"]

    @patch('codemie.agents.assistant_agent.time')
    @patch('codemie.agents.assistant_agent.calculate_tokens')
    def test_generate_without_background_task(
        self,
        mock_calculate_tokens,
        mock_time,
        mock_thread_generator,
        mock_request,
        mock_user,
        mock_background_task_service,
    ):
        # Setup - use empty string as background task ID
        background_task_id = ""

        # Set up time mock for execution time measurement
        mock_time.side_effect = [100.0, 107.0]  # start_time and end_time
        mock_calculate_tokens.return_value = 42

        # Create agent with appropriate patching to avoid init_agent execution
        with patch.object(AIToolsAgent, 'init_agent', return_value=MagicMock()):
            agent = AIToolsAgent(
                agent_name="TestAgent",
                description="A test agent",
                tools=[],
                request=mock_request,
                system_prompt="Test system prompt",
                request_uuid="test-uuid-123",
                user=mock_user,
                llm_model="gpt-3.5-turbo",
                thread_generator=mock_thread_generator,
            )

        # Set up agent to return mock response
        agent.is_pure_chain = MagicMock(return_value=True)
        mock_response = MagicMock()
        mock_response.generated = "This is a response without background task"
        agent.agent_executor.generate = MagicMock(return_value=mock_response)

        # Execute
        result = agent.generate(background_task_id=background_task_id)

        # Verify
        assert isinstance(result, GenerationResult)
        assert result.generated == "This is a response without background task"
        assert result.time_elapsed == 7.0  # (107.0 - 100.0)
        assert result.tokens_used == 42

        # Verify background task service was never updated
        mock_background_task_service.update.assert_not_called()

    @patch('codemie.agents.assistant_agent.time')
    @patch('codemie.agents.assistant_agent.calculate_tokens')
    def test_generate_with_bedrock_style_output(
        self,
        mock_calculate_tokens,
        mock_time,
        mock_thread_generator,
        mock_request,
        mock_user,
        mock_background_task_service,
    ):
        # Setup
        background_task_id = "test-task-123"

        # Set up time mock for execution time measurement
        mock_time.side_effect = [100.0, 108.0]
        mock_calculate_tokens.return_value = 50

        # Create agent with appropriate patching to avoid init_agent execution
        with patch.object(AIToolsAgent, 'init_agent', return_value=MagicMock()):
            agent = AIToolsAgent(
                agent_name="TestAgent",
                description="A test agent",
                tools=[MockTool()],
                request=mock_request,
                system_prompt="Test system prompt",
                request_uuid="test-uuid-123",
                user=mock_user,
                llm_model="anthropic.claude-3-sonnet-20240229-v1:0",  # Bedrock model
                thread_generator=mock_thread_generator,
            )

        # Set up mocks for a Bedrock-style response (list of dictionaries)
        agent.is_pure_chain = MagicMock(return_value=False)
        bedrock_response = {
            "output": [{"text": "This is a Bedrock model response"}, {"other_field": "Should be ignored"}]
        }
        agent._invoke_agent = MagicMock(return_value=bedrock_response)
        agent._get_inputs = MagicMock(return_value={})

        # Execute
        result = agent.generate(background_task_id=background_task_id)

        # Verify
        assert isinstance(result, GenerationResult)
        assert result.generated == "This is a Bedrock model response"
        assert result.time_elapsed == 8.0  # (108.0 - 100.0)
        assert result.tokens_used == 50

        # Verify background task service update
        mock_background_task_service.update.assert_called_once_with(
            task_id=background_task_id,
            status=BackgroundTaskStatus.COMPLETED,
            final_output="This is a Bedrock model response",
        )
