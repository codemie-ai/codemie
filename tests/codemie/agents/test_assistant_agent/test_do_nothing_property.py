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
from unittest.mock import Mock

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.core.models import AssistantChatRequest


@pytest.fixture
def agent():
    """Create a minimal AIToolsAgent instance for testing."""
    # Create mock objects for required parameters
    mock_request = Mock(spec=AssistantChatRequest)
    mock_request.text = "Test request"
    mock_request.file_name = None
    mock_request.history = []
    mock_request.conversation_id = "test-conversation-id"
    mock_request.system_prompt = None

    mock_user = Mock()
    mock_user.id = "test-user-id"
    mock_user.name = "Test User"

    # Instantiate the AIToolsAgent with minimal required parameters
    return AIToolsAgent(
        agent_name="test-agent",
        description="Test agent description",
        tools=[],  # Empty tools list for minimal setup
        request=mock_request,
        system_prompt="Test system prompt",
        request_uuid="test-uuid",
        user=mock_user,
        llm_model="test-model",
    )


def test_do_nothing_with_dict(agent):
    """Test that _do_nothing preserves dictionary input unchanged."""
    # Create a test dictionary with nested structure
    dict_input = {"key": "value", "nested": {"item": 1}}

    # Get the _do_nothing function
    do_nothing_func = agent._do_nothing

    # Apply the function to the input
    result = do_nothing_func(dict_input)

    # Verify the result is identical to the input
    assert result == dict_input
    # Verify object identity - should be the exact same object
    assert result is dict_input


def test_do_nothing_with_string(agent):
    """Test that _do_nothing preserves string input unchanged."""
    # Create a test string
    string_input = "test string input"

    # Get the _do_nothing function
    do_nothing_func = agent._do_nothing

    # Apply the function to the input
    result = do_nothing_func(string_input)

    # Verify the result is identical to the input
    assert result == string_input
    # Verify object identity - should be the exact same object
    assert result is string_input


def test_do_nothing_with_list(agent):
    """Test that _do_nothing preserves list input unchanged."""
    # Create a test list with mixed content
    list_input = [1, 2, {"key": "value"}]

    # Get the _do_nothing function
    do_nothing_func = agent._do_nothing

    # Apply the function to the input
    result = do_nothing_func(list_input)

    # Verify the result is identical to the input
    assert result == list_input
    # Verify object identity - should be the exact same object
    assert result is list_input


def test_do_nothing_with_integer(agent):
    """Test that _do_nothing preserves integer input unchanged."""
    # Create a test integer
    int_input = 42

    # Get the _do_nothing function
    do_nothing_func = agent._do_nothing

    # Apply the function to the input
    result = do_nothing_func(int_input)

    # Verify the result is identical to the input
    assert result == int_input


def test_do_nothing_with_none(agent):
    """Test that _do_nothing preserves None input unchanged."""
    # Use None as input
    none_input = None

    # Get the _do_nothing function
    do_nothing_func = agent._do_nothing

    # Apply the function to the input
    result = do_nothing_func(none_input)

    # Verify the result is identical to the input
    assert result is None


def test_do_nothing_with_empty_collections(agent):
    """Test that _do_nothing preserves empty collections unchanged."""
    # Create empty collections
    empty_dict = {}
    empty_list = []
    empty_string = ""

    # Get the _do_nothing function
    do_nothing_func = agent._do_nothing

    # Apply the function to each input and verify
    assert do_nothing_func(empty_dict) == empty_dict
    assert do_nothing_func(empty_dict) is empty_dict

    assert do_nothing_func(empty_list) == empty_list
    assert do_nothing_func(empty_list) is empty_list

    assert do_nothing_func(empty_string) == empty_string
    assert do_nothing_func(empty_string) is empty_string


def test_do_nothing_with_complex_nested_structure(agent):
    """Test that _do_nothing preserves complex nested structures unchanged."""
    # Create a complex nested structure
    complex_input = {
        "level1": {"level2": [1, 2, 3, {"level3": {"value": "test"}}], "another_key": (1, 2, 3)},
        "list_data": [{"item1": "value1"}, {"item2": [4, 5, 6]}, None],
    }

    # Get the _do_nothing function
    do_nothing_func = agent._do_nothing

    # Apply the function to the input
    result = do_nothing_func(complex_input)

    # Verify the result is identical to the input (deep comparison)
    assert result == complex_input
    # Verify object identity - should be the exact same object
    assert result is complex_input
