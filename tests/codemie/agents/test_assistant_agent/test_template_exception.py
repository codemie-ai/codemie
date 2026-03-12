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

from langchain_core.prompts import SystemMessagePromptTemplate, ChatPromptTemplate

from codemie.agents.assistant_agent import AIToolsAgent, InvalidPromptTemplate
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_request():
    mock_request = MagicMock(spec=AssistantChatRequest)
    mock_request.system_prompt = None
    mock_request.conversation_id = "test-conversation-id"
    mock_request.file_names = None
    mock_request.text = "Test request"
    mock_request.history = []
    return mock_request


@pytest.fixture
def mock_user():
    mock_user = MagicMock(spec=User)
    mock_user.id = "test-user-id"
    mock_user.name = "Test User"
    mock_user.username = "Test User"
    return mock_user


def test_valid_prompt_template(mock_request, mock_user):
    """Test that no exception is raised with correct template syntax."""
    # Arrange
    agent = AIToolsAgent(
        agent_name="test-agent",
        description="Test agent description",
        tools=[],  # Empty list of tools
        system_prompt="System prompt with {{ valid.jinja.syntax }}",  # Valid syntax
        request=mock_request,
        request_uuid="test-uuid-12345",
        user=mock_user,
        llm_model="gpt-3.5-turbo",
    )

    # Create a valid mock prompt that won't cause issues
    mock_prompt = MagicMock(spec=ChatPromptTemplate)

    # Act & Assert - should not raise exception
    with patch(
        'langchain_core.prompts.chat.SystemMessagePromptTemplate.from_template',
        return_value=MagicMock(spec=SystemMessagePromptTemplate),
    ):
        with patch('langchain_core.prompts.chat.ChatPromptTemplate.from_messages', return_value=mock_prompt):
            try:
                result = agent.get_prompt_template()
                assert result is mock_prompt
            except InvalidPromptTemplate:
                pytest.fail("Unexpected InvalidPromptTemplate exception raised with valid template")


@pytest.fixture
def agent_with_invalid_syntax(mock_request, mock_user):
    return AIToolsAgent(
        agent_name="test-agent",
        description="Test agent description",
        tools=[],  # Empty list of tools
        system_prompt="System prompt with {{ unclosed.jinja.syntax",  # Invalid Jinja2 syntax
        request=mock_request,
        request_uuid="test-uuid-12345",
        user=mock_user,
        llm_model="gpt-3.5-turbo",
    )


def test_other_exceptions_not_converted(agent_with_invalid_syntax):
    """Test that non-Jinja2 exceptions are not converted to InvalidPromptTemplate."""
    # Arrange - Set up a different type of exception
    with patch(
        'langchain_core.prompts.chat.SystemMessagePromptTemplate.from_template',
        side_effect=ValueError("Different error"),
    ):
        # Act & Assert - Should raise the original exception, not InvalidPromptTemplate
        with pytest.raises(ValueError):
            agent_with_invalid_syntax.get_prompt_template()
