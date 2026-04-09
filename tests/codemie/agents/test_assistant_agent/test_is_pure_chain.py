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
from unittest.mock import Mock, patch

from langchain_classic.agents import AgentExecutor

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.chains.pure_chat_chain import PureChatChain
from codemie.core.models import AssistantChatRequest
from codemie.core.thread import ThreadedGenerator


@pytest.fixture
def common_request_fixture():
    mock_request = Mock(spec=AssistantChatRequest)
    mock_request.conversation_id = "test-conversation-id"
    mock_request.history = []
    mock_request.text = "Test request text"
    mock_request.file_name = None
    mock_request.system_prompt = None
    return mock_request


@pytest.fixture
def common_user_fixture():
    mock_user = Mock()
    mock_user.id = "test-user-id"
    mock_user.name = "Test User"
    return mock_user


@pytest.fixture
def common_thread_generator_fixture():
    return Mock(spec=ThreadedGenerator)


@pytest.fixture
def common_params_fixture(common_request_fixture, common_user_fixture, common_thread_generator_fixture):
    return {
        "agent_name": "test_agent",
        "description": "Test agent for unit testing",
        "system_prompt": "You are a helpful assistant",
        "request_uuid": "test-uuid-1234",
        "user": common_user_fixture,
        "llm_model": "gpt-3.5-turbo",
        "request": common_request_fixture,
        "thread_generator": common_thread_generator_fixture,
        "verbose": False,
    }


@patch('codemie.agents.assistant_agent.get_llm_by_credentials')
def test_is_pure_chain_with_empty_tools(mock_get_llm, common_params_fixture):
    """Test that is_pure_chain returns True when agent_executor is PureChatChain (no tools)."""
    # Setup
    mock_llm = Mock()
    mock_get_llm.return_value = mock_llm

    # Create agent with empty tools list
    agent = AIToolsAgent(**common_params_fixture, tools=[])

    # Verify agent_executor is an instance of PureChatChain
    assert isinstance(agent.agent_executor, PureChatChain)

    # Test is_pure_chain method
    assert agent.is_pure_chain() is True


@patch('codemie.agents.assistant_agent.get_llm_by_credentials')
@patch('codemie.agents.assistant_agent.create_tool_calling_agent')
def test_is_pure_chain_with_tools(mock_create_tool_agent, mock_get_llm, common_params_fixture):
    """Test that is_pure_chain returns False when agent_executor is not PureChatChain (has tools)."""
    # Setup
    mock_llm = Mock()
    mock_get_llm.return_value = mock_llm

    mock_tool1 = Mock()
    mock_tool1.name = "tool1"
    mock_tool2 = Mock()
    mock_tool2.name = "tool2"

    # Mock agent creation
    mock_agent = Mock()
    mock_create_tool_agent.return_value = mock_agent

    # Create mock executor to be returned by init_agent
    mock_executor = Mock(spec=AgentExecutor)

    # Create agent with tools
    with patch.object(AIToolsAgent, 'init_agent', return_value=mock_executor):
        agent = AIToolsAgent(**common_params_fixture, tools=[mock_tool1, mock_tool2])

        # Verify agent_executor is not an instance of PureChatChain
        assert not isinstance(agent.agent_executor, PureChatChain)
        assert isinstance(agent.agent_executor, AgentExecutor)

        # Test is_pure_chain method
        assert agent.is_pure_chain() is False


@patch('codemie.agents.assistant_agent.get_llm_by_credentials')
def test_is_pure_chain_with_none_executor(mock_get_llm, common_params_fixture):
    """Test is_pure_chain when agent_executor is None."""
    # Setup
    mock_llm = Mock()
    mock_get_llm.return_value = mock_llm

    # Create agent with empty tools but override agent_executor to None
    with patch.object(AIToolsAgent, 'init_agent', return_value=None):
        agent = AIToolsAgent(**common_params_fixture, tools=[])

        # Verify agent_executor is None
        assert agent.agent_executor is None

        # Test is_pure_chain method
        # It should return False because None is not an instance of PureChatChain
        assert agent.is_pure_chain() is False


@patch('codemie.agents.assistant_agent.get_llm_by_credentials')
def test_is_pure_chain_with_manual_executor(mock_get_llm, common_params_fixture):
    """Test is_pure_chain with manually set agent_executor of different types."""
    # Setup
    mock_llm = Mock()
    mock_get_llm.return_value = mock_llm

    # Create base agent
    agent = AIToolsAgent(**common_params_fixture, tools=[])

    # Test with PureChatChain instance
    agent.agent_executor = Mock(spec=PureChatChain)
    assert agent.is_pure_chain() is True

    # Test with AgentExecutor instance
    agent.agent_executor = Mock(spec=AgentExecutor)
    assert agent.is_pure_chain() is False

    # Test with some other object type
    agent.agent_executor = "not a chain or executor"
    assert agent.is_pure_chain() is False
