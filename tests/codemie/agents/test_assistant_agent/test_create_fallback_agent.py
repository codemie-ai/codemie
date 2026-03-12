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

import uuid
from unittest.mock import patch, MagicMock

import pytest

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.chains.pure_chat_chain import PureChatChain
from codemie.core.models import AssistantChatRequest
from codemie.core.thread import ThreadedGenerator
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_request():
    mock_request = MagicMock(spec=AssistantChatRequest)
    mock_request.system_prompt = "Request system prompt"
    mock_request.conversation_id = str(uuid.uuid4())
    mock_request.file_name = None
    return mock_request


@pytest.fixture
def mock_user():
    mock_user = MagicMock(spec=User)
    mock_user.id = "test_user_id"
    mock_user.username = "test_user_id"
    mock_user.name = "Test User"
    return mock_user


@pytest.fixture
def mock_llm():
    return MagicMock()


@pytest.fixture
def mock_thread_generator():
    return MagicMock(spec=ThreadedGenerator)


class TestCreateFallbackAgent:
    """Test case for the _create_fallback_agent method of AIToolsAgent."""

    def test_create_fallback_agent(self, mock_llm, mock_request, mock_user, mock_thread_generator):
        """Test that _create_fallback_agent correctly initializes a PureChatChain with appropriate parameters."""
        # Initialize agent with empty tools to trigger fallback mechanism
        agent = AIToolsAgent(
            agent_name="TestFallbackAgent",
            description="Test description",
            tools=[],
            request=mock_request,
            system_prompt="Test system prompt",
            request_uuid=str(uuid.uuid4()),
            user=mock_user,
            llm_model="test-model",
            thread_generator=mock_thread_generator,
        )

        # Mock the agent's _get_system_prompt method to verify it's called with correct parameters
        with patch.object(
            agent, '_get_system_prompt', return_value="Processed system prompt"
        ) as mock_get_system_prompt:
            # Call the method under test
            fallback_agent = agent._create_fallback_agent(mock_llm)

            # Verify method was called with correct parameters
            mock_get_system_prompt.assert_called_once_with(from_request=True)

            # Check result is a PureChatChain instance
            assert isinstance(fallback_agent, PureChatChain)

            # Verify the PureChatChain was initialized with correct parameters
            assert fallback_agent.request == mock_request
            assert fallback_agent.system_prompt == "Processed system prompt"
            assert fallback_agent.llm_model == "test-model"
            assert fallback_agent.llm == mock_llm
            assert fallback_agent.thread_generator == mock_thread_generator

    def test_fallback_agent_without_request_system_prompt(self, mock_llm, mock_user, mock_thread_generator):
        """Test fallback agent creation when request.system_prompt is None."""
        # Create request with system_prompt set to None
        request = MagicMock(spec=AssistantChatRequest)
        request.system_prompt = None
        request.conversation_id = str(uuid.uuid4())
        request.file_name = None

        agent_system_prompt = "Agent system prompt"

        # Initialize agent with the request
        agent = AIToolsAgent(
            agent_name="TestFallbackAgent",
            description="Test description",
            tools=[],
            request=request,
            system_prompt=agent_system_prompt,
            request_uuid=str(uuid.uuid4()),
            user=mock_user,
            llm_model="test-model",
            thread_generator=mock_thread_generator,
        )

        # Patch the config.LLM_REQUEST_ADD_MARKDOWN_PROMPT
        with patch('codemie.agents.assistant_agent.config.LLM_REQUEST_ADD_MARKDOWN_PROMPT', False):
            # Create fallback agent
            fallback_agent = agent._create_fallback_agent(mock_llm)

            # Verify that agent.system_prompt is used since request.system_prompt is None
            assert fallback_agent.system_prompt == agent_system_prompt
            assert fallback_agent.request == request

    def test_fallback_agent_with_markdown_prompt(self, mock_llm, mock_request, mock_user, mock_thread_generator):
        """Test fallback agent creation when LLM_REQUEST_ADD_MARKDOWN_PROMPT is True."""
        # Initialize agent
        agent = AIToolsAgent(
            agent_name="TestFallbackAgent",
            description="Test description",
            tools=[],
            request=mock_request,
            system_prompt="Test system prompt",
            request_uuid=str(uuid.uuid4()),
            user=mock_user,
            llm_model="test-model",
            thread_generator=mock_thread_generator,
        )

        # Create patch for config and necessary methods
        with (
            patch('codemie.agents.assistant_agent.config') as mock_config,
            patch('codemie.agents.assistant_agent.markdown_response_prompt', "MARKDOWN_INSTRUCTION"),
        ):
            # Set the mocked attribute
            mock_config.LLM_REQUEST_ADD_MARKDOWN_PROMPT = True
            # Create fallback agent
            fallback_agent = agent._create_fallback_agent(mock_llm)

            # Verify that the system prompt includes the markdown instructions
            assert "MARKDOWN_INSTRUCTION" in fallback_agent.system_prompt

    def test_fallback_agent_logs_debug_message(self, mock_llm, mock_request, mock_user, mock_thread_generator):
        """Test that creating fallback agent logs appropriate debug message."""
        # Initialize agent
        agent = AIToolsAgent(
            agent_name="TestFallbackAgent",
            description="Test description",
            tools=[],
            request=mock_request,
            system_prompt="Test system prompt",
            request_uuid=str(uuid.uuid4()),
            user=mock_user,
            llm_model="test-model",
            thread_generator=mock_thread_generator,
        )

        # Create patch for logger.debug
        with patch('codemie.agents.assistant_agent.logger') as mock_logger:
            # Create fallback agent
            agent._create_fallback_agent(mock_llm)

            # Verify that debug log was called with appropriate message
            mock_logger.debug.assert_called_once_with("LLMChain initialized for TestFallbackAgent as fallback")
