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
from typing import Any

from langchain_core.tools import BaseTool

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.core.constants import ChatRole
from codemie.core.models import AssistantChatRequest, ChatMessage
from codemie.rest_api.security.user import User


class MockTool(BaseTool):
    name: str = "mock_tool"
    description: str = "A mock tool for testing"

    def _run(self, input_text: str, **kwargs: Any) -> str:
        return "Mock tool result"


@pytest.fixture
def test_data():
    return {
        "agent_name": "TestAgent",
        "description": "Test agent description",
        "request": AssistantChatRequest(
            text="Test request",
            conversation_id="conv123",
            history=[],
            file_names=None,
            system_prompt="",  # Empty string instead of None to pass validation
        ),
        "system_prompt": "You are a helpful assistant.",
        "request_uuid": "test-uuid-123",
        "user": User(id="user123", name="Test User", auth_token=""),
        "llm_model": "gpt-3.5-turbo",
        "input_text": "User question",
        "history": [
            ChatMessage(role=ChatRole.USER, message="Previous question"),
            ChatMessage(role=ChatRole.ASSISTANT, message="Previous answer"),
        ],
        "mock_traceback": "Traceback (most recent call last):\n  File \"assistant_agent.py\", line 123, in invoke\n    raise ValueError('Test error message')\nValueError: Test error message",
        "exception_to_raise": ValueError("Test error message"),
    }


@pytest.fixture
def mock_tools():
    return [MockTool()]


@pytest.fixture
def agent(test_data, mock_tools):
    with patch.object(AIToolsAgent, 'init_agent', return_value=MagicMock()):
        return AIToolsAgent(
            agent_name=test_data["agent_name"],
            description=test_data["description"],
            tools=mock_tools,
            request=test_data["request"],
            system_prompt=test_data["system_prompt"],
            request_uuid=test_data["request_uuid"],
            user=test_data["user"],
            llm_model=test_data["llm_model"],
        )


def test_invoke_error_in_get_inputs(agent, test_data):
    """
    Test that the invoke method properly handles exceptions raised in _get_inputs.
    """
    with patch.object(agent, '_get_inputs', side_effect=test_data["exception_to_raise"]) as mock_get_inputs:
        with patch.object(agent, '_invoke_agent') as mock_invoke_agent:
            with patch('traceback.format_exc', return_value=test_data["mock_traceback"]) as mock_format_exc:
                with patch('codemie.agents.assistant_agent.logger.error') as mock_logger:
                    # Call the method being tested
                    result = agent.invoke(test_data["input_text"], test_data["history"])

                    # Verify the results
                    mock_get_inputs.assert_called_once_with(test_data["input_text"], test_data["history"])
                    mock_invoke_agent.assert_not_called()
                    mock_format_exc.assert_called_once()
                    mock_logger.assert_called_once()

                    # Verify the returned result
                    expected_result = f"AI Agent run failed with error: {test_data['mock_traceback']}"
                    assert result == expected_result


def test_invoke_error_in_invoke_agent(agent, test_data):
    """
    Test that the invoke method properly handles exceptions raised in _invoke_agent.
    """
    # Prepare mock inputs to return
    mock_inputs = {"input": "Test input", "chat_history": []}

    with patch.object(agent, '_get_inputs', return_value=mock_inputs) as mock_get_inputs:
        with patch.object(agent, '_invoke_agent', side_effect=test_data["exception_to_raise"]) as mock_invoke_agent:
            with patch('traceback.format_exc', return_value=test_data["mock_traceback"]) as mock_format_exc:
                with patch('codemie.agents.assistant_agent.logger.error') as mock_logger:
                    # Call the method being tested
                    result = agent.invoke(test_data["input_text"], test_data["history"])

                    # Verify the results
                    mock_get_inputs.assert_called_once_with(test_data["input_text"], test_data["history"])
                    mock_invoke_agent.assert_called_once_with(mock_inputs)
                    mock_format_exc.assert_called_once()
                    mock_logger.assert_called_once()

                    # Verify the returned result
                    expected_result = f"AI Agent run failed with error: {test_data['mock_traceback']}"
                    assert result == expected_result


@pytest.mark.parametrize(
    "exception",
    [RuntimeError("Runtime error"), TypeError("Type error"), KeyError("Key error")],
    ids=["runtime_error", "type_error", "key_error"],
)
def test_invoke_with_different_exception_types(agent, test_data, exception):
    """
    Test that the invoke method properly handles different types of exceptions.
    """
    with patch.object(agent, '_get_inputs', side_effect=exception):
        with patch('traceback.format_exc', return_value=str(exception)):
            with patch('codemie.agents.assistant_agent.logger.error'):
                # Call the method being tested
                result = agent.invoke(test_data["input_text"], test_data["history"])

                # Verify the returned result contains the exception information
                assert str(exception) in result
                assert "AI Agent run failed with error" in result


def test_invoke_with_empty_inputs(agent):
    """
    Test that the invoke method properly handles cases with empty inputs.
    """
    # Mock _invoke_agent to return an empty response
    with patch.object(agent, '_invoke_agent', return_value={'output': ''}):
        # Call the method with empty inputs
        result = agent.invoke("", [])

        # The method should return empty output without throwing an exception
        assert result == ""
