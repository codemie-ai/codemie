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

"""
Test Area: Utility Functions

Tests for utility functions: exclude_prior_messages, get_context_store_from_state_schema,
parse_from_string_representation, and other workflow utilities.

This module tests the following functionality:
- exclude_prior_messages with RemoveMessage creation
- get_context_store_from_state_schema extraction
- parse_from_string_representation with JSON/dict parsing
- extract_json_content with backticks
- evaluate_next_candidate with condition/switch
"""

from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage

from codemie.workflows.utils import (
    exclude_prior_messages,
    get_context_store_from_state_schema,
    parse_from_string_representation,
    extract_json_content,
    evaluate_next_candidate,
    get_messages_from_state_schema,
)
from codemie.workflows.constants import MESSAGES_VARIABLE, CONTEXT_STORE_VARIABLE
from codemie.core.workflow_models import WorkflowState, WorkflowNextState
from codemie.core.workflow_models.workflow_models import (
    WorkflowStateCondition,
    WorkflowStateSwitch,
    WorkflowStateSwitchCondition,
)


def test_tc_uf_001_exclude_prior_messages_with_messages():
    """
    TC_UF_001: exclude_prior_messages with Messages

    Test RemoveMessage creation for all existing messages.
    """
    # Arrange
    existing_messages = [
        HumanMessage(content="msg1", id="id1"),
        AIMessage(content="resp1", id="id2"),
        HumanMessage(content="msg2", id="id3"),
    ]
    state_schema = {MESSAGES_VARIABLE: existing_messages}
    current_messages = [AIMessage(content="new message")]

    # Act
    result = exclude_prior_messages(state_schema, current_messages)

    # Assert
    # Should have 3 RemoveMessage + 1 new message
    assert len(result) == 4

    # First 3 should be RemoveMessage
    remove_messages = [msg for msg in result if isinstance(msg, RemoveMessage)]
    assert len(remove_messages) == 3

    # Verify IDs
    remove_ids = {msg.id for msg in remove_messages}
    assert remove_ids == {"id1", "id2", "id3"}

    # Last message should be the new message
    assert result[-1].content == "new message"


def test_tc_uf_002_exclude_prior_messages_with_empty_list():
    """
    TC_UF_002: exclude_prior_messages with Empty List

    Test early return when no messages exist.
    """
    # Arrange
    state_schema = {MESSAGES_VARIABLE: []}
    current_messages = [AIMessage(content="first message")]

    # Act
    result = exclude_prior_messages(state_schema, current_messages)

    # Assert
    # Should return only current messages (no RemoveMessage)
    assert len(result) == 1
    assert result[0].content == "first message"
    assert not any(isinstance(msg, RemoveMessage) for msg in result)


def test_tc_uf_003_get_context_store_from_state_schema():
    """
    TC_UF_003: get_context_store_from_state_schema

    Test extraction of context_store from state.
    """
    # Arrange
    context_data = {"key1": "value1", "key2": "value2"}
    state_schema = {CONTEXT_STORE_VARIABLE: context_data}

    # Act
    result = get_context_store_from_state_schema(state_schema)

    # Assert
    assert result == context_data
    assert result["key1"] == "value1"
    assert result["key2"] == "value2"


def test_tc_uf_004_get_context_store_with_missing_key():
    """
    TC_UF_004: get_context_store with Missing Key

    Return empty dict when context_store not in state.
    """
    # Arrange
    state_schema = {MESSAGES_VARIABLE: []}  # No context_store key

    # Act
    result = get_context_store_from_state_schema(state_schema)

    # Assert
    assert result == {}
    assert len(result) == 0


def test_tc_uf_005_parse_from_string_representation_with_json():
    """
    TC_UF_005: parse_from_string_representation with JSON

    Test JSON string parsing to dict.
    """
    # Arrange
    json_string = '{"name": "John", "age": 30, "city": "New York"}'

    # Act
    result = parse_from_string_representation(json_string)

    # Assert
    assert isinstance(result, dict)
    assert result["name"] == "John"
    assert result["age"] == 30
    assert result["city"] == "New York"


def test_tc_uf_006_parse_from_string_representation_with_dict_string():
    """
    TC_UF_006: parse_from_string_representation with Dict String

    Test Python dict string with ast.literal_eval.
    """
    # Arrange
    dict_string = "{'key': 'value', 'number': 42}"

    # Act
    result = parse_from_string_representation(dict_string)

    # Assert
    assert isinstance(result, dict)
    assert result["key"] == "value"
    assert result["number"] == 42


def test_tc_uf_007_parse_from_string_representation_with_plain_string():
    """
    TC_UF_007: parse_from_string_representation with Plain String

    Return string as-is when not JSON.
    """
    # Arrange
    plain_string = "This is just a plain string"

    # Act
    result = parse_from_string_representation(plain_string)

    # Assert
    assert result == plain_string
    assert isinstance(result, str)


def test_tc_uf_008_extract_json_content_with_backticks():
    """
    TC_UF_008: extract_json_content with Backticks

    Test JSON extraction from markdown code blocks.
    """
    # Arrange
    response_with_backticks = '''Here is the result:
```json
{"status": "success", "data": {"count": 5}}
```
Done!'''

    # Act
    result = extract_json_content(response_with_backticks)

    # Assert
    assert isinstance(result, dict)
    assert result["status"] == "success"
    assert result["data"]["count"] == 5


def test_tc_uf_009_evaluate_next_candidate_with_condition():
    """
    TC_UF_009: evaluate_next_candidate with Condition

    Test condition evaluation for next state.
    """
    # Arrange
    execution_result = '{"status": "success", "value": 100}'

    workflow_state = WorkflowState(
        id="test_state",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="default",
            condition=WorkflowStateCondition(
                expression="status == 'success' and value > 50",
                then="success_node",
                otherwise="failure_node",
            ),
        ),
    )
    enable_summarization = False

    # Act
    result = evaluate_next_candidate(execution_result, workflow_state, enable_summarization)

    # Assert
    assert result == "success_node"


def test_tc_uf_010_evaluate_next_candidate_with_switch():
    """
    TC_UF_010: evaluate_next_candidate with Switch

    Test switch evaluation for next state.
    """
    # Arrange
    execution_result = '{"priority": "high", "count": 10}'

    workflow_state = WorkflowState(
        id="test_state",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="default",
            switch=WorkflowStateSwitch(
                cases=[
                    WorkflowStateSwitchCondition(
                        condition="priority == 'high' and count > 5",
                        state_id="high_priority_node",
                    ),
                    WorkflowStateSwitchCondition(
                        condition="priority == 'medium'",
                        state_id="medium_priority_node",
                    ),
                ],
                default="low_priority_node",
            ),
        ),
    )
    enable_summarization = False

    # Act
    result = evaluate_next_candidate(execution_result, workflow_state, enable_summarization)

    # Assert
    assert result == "high_priority_node"


# Additional helper function tests


def test_get_messages_from_state_schema_with_messages():
    """Test get_messages_from_state_schema with existing messages."""
    # Arrange
    messages = [HumanMessage(content="test")]
    state_schema = {MESSAGES_VARIABLE: messages}

    # Act
    result = get_messages_from_state_schema(state_schema)

    # Assert
    assert result == messages
    assert len(result) == 1


def test_get_messages_from_state_schema_without_messages():
    """Test get_messages_from_state_schema without messages key."""
    # Arrange
    state_schema = {}

    # Act
    result = get_messages_from_state_schema(state_schema)

    # Assert
    assert result == []
    assert len(result) == 0


def test_parse_from_string_representation_with_list():
    """Test parsing JSON array string."""
    # Arrange
    list_string = '[1, 2, 3, "four"]'

    # Act
    result = parse_from_string_representation(list_string)

    # Assert
    assert isinstance(result, list)
    assert result == [1, 2, 3, "four"]


def test_extract_json_content_with_nested_objects():
    """Test extracting nested JSON objects."""
    # Arrange
    response = '{"outer": {"inner": {"deep": "value"}}}'

    # Act
    result = extract_json_content(response)

    # Assert
    assert isinstance(result, dict)
    assert result["outer"]["inner"]["deep"] == "value"


def test_evaluate_next_candidate_without_condition_or_switch():
    """Test evaluate_next_candidate with simple state_id."""
    # Arrange
    execution_result = '{"result": "done"}'

    workflow_state = WorkflowState(
        id="test_state",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next_node"),
    )
    enable_summarization = False

    # Act
    result = evaluate_next_candidate(execution_result, workflow_state, enable_summarization)

    # Assert
    assert result == "next_node"


def test_evaluate_next_candidate_with_failed_condition():
    """Test condition evaluation when condition fails."""
    # Arrange
    execution_result = '{"status": "failed", "value": 10}'

    workflow_state = WorkflowState(
        id="test_state",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="default",
            condition=WorkflowStateCondition(
                expression="status == 'success' and value > 50",
                then="success_node",
                otherwise="failure_node",
            ),
        ),
    )
    enable_summarization = False

    # Act
    result = evaluate_next_candidate(execution_result, workflow_state, enable_summarization)

    # Assert
    assert result == "failure_node"


def test_evaluate_next_candidate_with_switch_default():
    """Test switch evaluation falling back to default."""
    # Arrange
    execution_result = '{"priority": "low", "count": 1}'

    workflow_state = WorkflowState(
        id="test_state",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="default",
            switch=WorkflowStateSwitch(
                cases=[
                    WorkflowStateSwitchCondition(
                        condition="priority == 'high'",
                        state_id="high_priority_node",
                    ),
                    WorkflowStateSwitchCondition(
                        condition="priority == 'medium'",
                        state_id="medium_priority_node",
                    ),
                ],
                default="low_priority_node",
            ),
        ),
    )
    enable_summarization = False

    # Act
    result = evaluate_next_candidate(execution_result, workflow_state, enable_summarization)

    # Assert
    assert result == "low_priority_node"
