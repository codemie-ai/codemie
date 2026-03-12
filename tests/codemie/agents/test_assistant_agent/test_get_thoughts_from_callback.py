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
from unittest.mock import Mock  # Mock is often still useful directly

# Assume these imports are correct based on your project structure
from codemie.agents.assistant_agent import AIToolsAgent
from codemie.chains.pure_chat_chain import PureChatChain
from codemie.core.models import AssistantChatRequest

# --- Pytest Fixtures ---


@pytest.fixture
def mock_request():
    """Provides a mock AssistantChatRequest."""
    request = Mock(spec=AssistantChatRequest)
    request.conversation_id = "test-conversation-id"
    request.history = []
    request.text = "Test request text"
    request.file_name = None
    request.system_prompt = None
    return request


@pytest.fixture
def mock_user():
    """Provides a mock User object."""
    user = Mock()
    user.id = "test-user-id"
    user.name = "Test User"
    return user


@pytest.fixture
def mock_thread_generator():
    """Provides a mock ThreadGenerator."""
    return Mock()


@pytest.fixture
def mock_thoughts():
    """Provides sample thoughts list."""
    return ["Thought 1: I need to understand the request", "Thought 2: Let me search for information"]


@pytest.fixture
def common_agent_params(mock_request, mock_user, mock_thread_generator):
    """Provides common parameters for AIToolsAgent initialization."""
    return {
        "agent_name": "test_agent",
        "description": "Test agent for unit testing",
        "system_prompt": "You are a helpful assistant",
        "request_uuid": "test-uuid-1234",
        "user": mock_user,
        "llm_model": "gpt-3.5-turbo",
        "request": mock_request,
        "thread_generator": mock_thread_generator,
        "verbose": False,
    }


# --- Test Class ---


class TestGetThoughtsFromCallback:
    """Tests for the get_thoughts_from_callback method of AIToolsAgent."""

    def test_get_thoughts_from_pure_chain(self, mocker, common_agent_params):
        """Test returns empty list when agent_executor is PureChatChain."""
        # Arrange
        mock_llm = Mock()
        mocker.patch('codemie.agents.assistant_agent.get_llm_by_credentials', return_value=mock_llm)
        # No need to mock create_tool_calling_agent as tools=[] leads to PureChatChain

        # Act
        # Create agent with empty tools list (which results in a PureChatChain)
        agent = AIToolsAgent(**common_agent_params, tools=[])
        thoughts = agent.get_thoughts_from_callback()

        # Assert
        assert isinstance(agent.agent_executor, PureChatChain)
        assert thoughts == []

    def test_get_thoughts_with_callback_containing_thoughts(self, mocker, common_agent_params, mock_thoughts):
        """Test returns thoughts from a callback that has a thoughts attribute."""
        # Arrange
        mock_llm = Mock()
        mocker.patch('codemie.agents.assistant_agent.get_llm_by_credentials', return_value=mock_llm)

        mock_tool = Mock()
        mock_tool.name = "test_tool"

        # Mock agent creation within init_agent
        mock_created_agent = Mock()
        mocker.patch('codemie.agents.assistant_agent.create_tool_calling_agent', return_value=mock_created_agent)

        # Create mock executor with callbacks that have thoughts
        mock_executor = Mock()
        mock_callback_with_thoughts = Mock()
        mock_callback_with_thoughts.thoughts = mock_thoughts  # Use the fixture
        mock_callback_without_thoughts = Mock()
        # Ensure the second mock doesn't accidentally have 'thoughts' if not intended
        # One way is to explicitly set it to None or use spec
        mock_callback_without_thoughts.thoughts = None
        # Or: mock_callback_without_thoughts = Mock(spec=['some_other_method'])

        mock_executor.callbacks = [mock_callback_with_thoughts, mock_callback_without_thoughts]

        # Mock the init_agent method to return our controlled executor
        mocker.patch.object(AIToolsAgent, 'init_agent', return_value=mock_executor)

        # Act
        agent = AIToolsAgent(**common_agent_params, tools=[mock_tool])
        # Mock is_pure_chain to return False to enter the callback logic
        mocker.patch.object(agent, 'is_pure_chain', return_value=False)
        thoughts = agent.get_thoughts_from_callback()

        # Assert
        assert thoughts == mock_thoughts

    def test_get_thoughts_without_thoughts_in_callbacks(self, mocker, common_agent_params):
        """Test returns empty list when no callback has thoughts attribute."""
        # Arrange
        mock_llm = Mock()
        mocker.patch('codemie.agents.assistant_agent.get_llm_by_credentials', return_value=mock_llm)

        mock_tool = Mock()
        mock_tool.name = "test_tool"

        mock_created_agent = Mock()
        mocker.patch('codemie.agents.assistant_agent.create_tool_calling_agent', return_value=mock_created_agent)

        mock_executor = Mock()
        # Create callbacks ensuring they don't have 'thoughts'
        mock_callback1 = Mock(spec=[])  # spec=[] helps prevent accidental attribute creation
        mock_callback2 = Mock(spec=['another_method'])  # Or specify known attributes/methods

        mock_executor.callbacks = [mock_callback1, mock_callback2]

        mocker.patch.object(AIToolsAgent, 'init_agent', return_value=mock_executor)

        # Act
        agent = AIToolsAgent(**common_agent_params, tools=[mock_tool])
        mocker.patch.object(agent, 'is_pure_chain', return_value=False)
        thoughts = agent.get_thoughts_from_callback()

        # Assert
        assert thoughts == []

    def test_get_thoughts_with_empty_thoughts_in_callback(self, mocker, common_agent_params):
        """Test returns empty list when callback's thoughts attribute is empty."""
        # Arrange
        mock_llm = Mock()
        mocker.patch('codemie.agents.assistant_agent.get_llm_by_credentials', return_value=mock_llm)

        mock_tool = Mock()
        mock_tool.name = "test_tool"

        mock_created_agent = Mock()
        mocker.patch('codemie.agents.assistant_agent.create_tool_calling_agent', return_value=mock_created_agent)

        mock_executor = Mock()
        mock_callback_with_empty_thoughts = Mock()
        mock_callback_with_empty_thoughts.thoughts = []  # Explicitly empty list

        mock_executor.callbacks = [mock_callback_with_empty_thoughts]

        mocker.patch.object(AIToolsAgent, 'init_agent', return_value=mock_executor)

        # Act
        agent = AIToolsAgent(**common_agent_params, tools=[mock_tool])
        mocker.patch.object(agent, 'is_pure_chain', return_value=False)
        thoughts = agent.get_thoughts_from_callback()

        # Assert
        assert thoughts == []

    def test_get_thoughts_from_multiple_callbacks_with_thoughts(self, mocker, common_agent_params, mock_thoughts):
        """Test returns thoughts from the *first* callback that has them."""
        # Arrange
        mock_llm = Mock()
        mocker.patch('codemie.agents.assistant_agent.get_llm_by_credentials', return_value=mock_llm)

        mock_tool = Mock()
        mock_tool.name = "test_tool"

        mock_created_agent = Mock()
        mocker.patch('codemie.agents.assistant_agent.create_tool_calling_agent', return_value=mock_created_agent)

        mock_executor = Mock()
        mock_callback1 = Mock()
        mock_callback1.thoughts = mock_thoughts  # Use fixture for first callback
        mock_callback2 = Mock()
        mock_callback2.thoughts = ["Different thought 1", "Different thought 2"]

        # Order matters here: callback1 comes first
        mock_executor.callbacks = [mock_callback1, mock_callback2]

        mocker.patch.object(AIToolsAgent, 'init_agent', return_value=mock_executor)

        # Act
        agent = AIToolsAgent(**common_agent_params, tools=[mock_tool])
        mocker.patch.object(agent, 'is_pure_chain', return_value=False)
        thoughts = agent.get_thoughts_from_callback()

        # Assert
        # Should return thoughts from the first callback
        assert thoughts == mock_thoughts

    def test_get_thoughts_with_none_callbacks_attribute(self, mocker, common_agent_params):
        """Test handles agent_executor having callbacks=None gracefully."""
        # Arrange
        mock_llm = Mock()
        mocker.patch('codemie.agents.assistant_agent.get_llm_by_credentials', return_value=mock_llm)

        mock_tool = Mock()
        mock_tool.name = "test_tool"

        mock_created_agent = Mock()
        mocker.patch('codemie.agents.assistant_agent.create_tool_calling_agent', return_value=mock_created_agent)

        mock_executor = Mock()
        mock_executor.callbacks = []

        mocker.patch.object(AIToolsAgent, 'init_agent', return_value=mock_executor)

        # Act
        agent = AIToolsAgent(**common_agent_params, tools=[mock_tool])
        mocker.patch.object(agent, 'is_pure_chain', return_value=False)
        # Call the actual method - it should handle callbacks=None internally
        thoughts = agent.get_thoughts_from_callback()

        # Assert
        assert thoughts == []

    def test_get_thoughts_with_missing_callbacks_attribute(self, mocker, common_agent_params):
        """Test handles agent_executor missing the callbacks attribute gracefully."""
        # Arrange
        mock_llm = Mock()
        mocker.patch('codemie.agents.assistant_agent.get_llm_by_credentials', return_value=mock_llm)

        mock_tool = Mock()
        mock_tool.name = "test_tool"

        mock_created_agent = Mock()
        mocker.patch('codemie.agents.assistant_agent.create_tool_calling_agent', return_value=mock_created_agent)

        # Create a mock executor *without* the 'callbacks' attribute
        mock_executor = Mock(spec=[])  # spec=[] prevents default attributes

        mock_callback_with_thoughts = Mock()
        mock_callback_with_thoughts.thoughts = []
        mock_callback_without_thoughts = Mock()
        mock_callback_without_thoughts.thoughts = None
        mock_executor.callbacks = [mock_callback_with_thoughts, mock_callback_without_thoughts]

        mocker.patch.object(AIToolsAgent, 'init_agent', return_value=mock_executor)

        # Act
        agent = AIToolsAgent(**common_agent_params, tools=[mock_tool])
        mocker.patch.object(agent, 'is_pure_chain', return_value=False)
        # Call the actual method - it should handle missing 'callbacks' (e.g., via hasattr or try/except)
        thoughts = agent.get_thoughts_from_callback()

        # Assert
        assert thoughts == []
