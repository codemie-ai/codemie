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
from unittest.mock import patch, MagicMock

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.service.llm_service.llm_service import LLMService


@pytest.fixture
def test_params():
    # Set up common test parameters
    return {
        "llm_model": LLMService.BASE_NAME_GPT_41,
        "temperature": 0.7,
        "top_p": 0.9,
        "request_uuid": "test-request-uuid",
    }


@pytest.fixture
def mock_request():
    # Create a mock request object
    request = MagicMock()
    request.system_prompt = "Test system prompt"
    request.conversation_id = "test-conversation-id"
    request.file_name = None
    request.history = []
    return request


@pytest.fixture
def mock_user():
    # Create a mock user object
    user = MagicMock()
    user.id = "test-user-id"
    user.email = "test@example.com"
    user.name = "Test User"
    return user


@pytest.fixture
def system_prompt():
    return "Test system prompt"


@pytest.fixture
def agent_factory(mock_request, mock_user, system_prompt, test_params):
    """Factory fixture to create AIToolsAgent instances with different configurations"""

    def _create_agent(**kwargs):
        # Create a patched AIToolsAgent with provided parameters
        with patch.object(AIToolsAgent, 'init_agent', return_value=MagicMock()):
            return AIToolsAgent(
                agent_name="TestAgent",
                description="A test agent",
                tools=[],
                request=mock_request,
                system_prompt=system_prompt,
                request_uuid=test_params["request_uuid"],
                user=mock_user,
                **kwargs,
            )

    return _create_agent


@patch("codemie.agents.assistant_agent.get_llm_by_credentials")
def test_initialize_llm(mock_get_llm_by_credentials, agent_factory, test_params):
    # Setup
    mock_llm = MagicMock()
    mock_get_llm_by_credentials.return_value = mock_llm

    # Create agent with all parameters
    agent = agent_factory(
        llm_model=test_params["llm_model"], temperature=test_params["temperature"], top_p=test_params["top_p"]
    )

    # Call the method to test
    result = agent._initialize_llm()

    # Assert that get_llm_by_credentials was called with correct params
    mock_get_llm_by_credentials.assert_called_once_with(
        llm_model=test_params["llm_model"],
        temperature=test_params["temperature"],
        top_p=test_params["top_p"],
        request_id=test_params["request_uuid"],
    )

    # Assert that the method returns the expected LLM instance
    assert result == mock_llm


@patch("codemie.agents.assistant_agent.get_llm_by_credentials")
def test_initialize_llm_without_temperature(mock_get_llm_by_credentials, agent_factory, test_params):
    # Setup
    mock_llm = MagicMock()
    mock_get_llm_by_credentials.return_value = mock_llm

    # Create agent without temperature parameter
    agent = agent_factory(llm_model=test_params["llm_model"], top_p=test_params["top_p"])

    # Call the method to test
    result = agent._initialize_llm()

    # Assert get_llm_by_credentials was called with temperature=None
    mock_get_llm_by_credentials.assert_called_once_with(
        llm_model=test_params["llm_model"],
        temperature=None,
        top_p=test_params["top_p"],
        request_id=test_params["request_uuid"],
    )

    # Assert that the method returns the expected LLM instance
    assert result == mock_llm


@patch("codemie.agents.assistant_agent.get_llm_by_credentials")
def test_initialize_llm_without_top_p(mock_get_llm_by_credentials, agent_factory, test_params):
    # Setup
    mock_llm = MagicMock()
    mock_get_llm_by_credentials.return_value = mock_llm

    # Create agent without top_p parameter
    agent = agent_factory(llm_model=test_params["llm_model"], temperature=test_params["temperature"])

    # Call the method to test
    result = agent._initialize_llm()

    # Assert get_llm_by_credentials was called with top_p=None
    mock_get_llm_by_credentials.assert_called_once_with(
        llm_model=test_params["llm_model"],
        temperature=test_params["temperature"],
        top_p=None,
        request_id=test_params["request_uuid"],
    )

    # Assert that the method returns the expected LLM instance
    assert result == mock_llm


@patch("codemie.agents.assistant_agent.get_llm_by_credentials")
def test_initialize_llm_with_different_models(mock_get_llm_by_credentials, agent_factory, test_params):
    # Setup
    mock_llm = MagicMock()
    mock_get_llm_by_credentials.return_value = mock_llm

    different_models = ["gpt-3.5-turbo", "claude-2", "anthropic-claude-instant-v1"]

    for model in different_models:
        # Reset mock
        mock_get_llm_by_credentials.reset_mock()

        # Create agent with different model
        agent = agent_factory(llm_model=model, temperature=test_params["temperature"], top_p=test_params["top_p"])

        # Call the method to test
        result = agent._initialize_llm()

        # Assert that get_llm_by_credentials was called with the correct model
        mock_get_llm_by_credentials.assert_called_once_with(
            llm_model=model,
            temperature=test_params["temperature"],
            top_p=test_params["top_p"],
            request_id=test_params["request_uuid"],
        )

        # Assert that the method returns the expected LLM instance
        assert result == mock_llm
