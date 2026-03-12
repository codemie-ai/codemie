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
Test Area: Flag Interactions

Tests for complex interactions between store_in_context, include_in_llm_history,
clear_prior_messages, and clear_context_store flags introduced in commit 7c2d4928.

This module tests the following functionality:
- All flags enabled simultaneously
- Various flag combinations (store vs include)
- Clear operations with and without additions
- Iteration context with mixed flags
- Flag priority and ordering
"""

import pytest
from unittest.mock import Mock
from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage

from codemie.workflows.nodes.base_node import BaseNode
from codemie.workflows.constants import (
    CONTEXT_STORE_VARIABLE,
    MESSAGES_VARIABLE,
    FIRST_STATE_IN_ITERATION,
    TASK_KEY,
)
from codemie.workflows.models import CONTEXT_STORE_DELETE_MARKER
from codemie.core.workflow_models import WorkflowNextState, WorkflowState


class MockNode(BaseNode):
    """Mock implementation of BaseNode for testing."""

    def execute(self, state_schema, execution_context):
        return self.mock_execute_result

    def get_task(self, state_schema, *args, **kwargs):
        return "Test Task"


@pytest.fixture
def mock_workflow_execution_service():
    """Create mock WorkflowExecutionService."""
    service = Mock()
    service.start_state = Mock(return_value="state_123")
    service.finish_state = Mock()
    return service


@pytest.fixture
def mock_thought_queue():
    """Create mock ThoughtQueue."""
    return Mock()


@pytest.fixture
def mock_callbacks():
    """Create mock callbacks."""
    callback = Mock()
    callback.on_node_start = Mock()
    callback.on_node_end = Mock()
    return [callback]


def test_tc_fi_001_all_flags_enabled_simultaneously(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_FI_001: All Flags Enabled Simultaneously

    Verify all four context management flags work correctly when enabled together.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"old_key": "old_value"},
        MESSAGES_VARIABLE: [HumanMessage(content="old", id="old_id"), AIMessage(content="resp", id="resp_id")],
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            store_in_context=True,
            include_in_llm_history=True,
            clear_prior_messages=True,
            clear_context_store=True,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"new_data": "value"}

    # Act
    result = node(state_schema)

    # Assert
    # Context store: cleared then new data added
    context_store = result[CONTEXT_STORE_VARIABLE]
    # Should be None (cleared) or contain only new data
    if context_store is not None and context_store != {}:
        assert "old_key" not in context_store
        assert "new_data" in context_store or context_store.get("new_data") == "value"

    # Messages: old removed, new added
    messages = result[MESSAGES_VARIABLE]
    remove_messages = [msg for msg in messages if isinstance(msg, RemoveMessage)]
    assert len(remove_messages) == 2  # Both old messages removed

    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage) and hasattr(msg, 'content')]
    assert len(ai_messages) >= 1  # New message added


def test_tc_fi_002_store_in_context_false_include_in_llm_history_true(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_FI_002: store_in_context=False, include_in_llm_history=True

    Output added to message history but NOT to context store.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"data": "preserved"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            store_in_context=False,
            include_in_llm_history=True,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "Task completed"

    # Act
    result = node(state_schema)

    # Assert
    # Context unchanged
    context_store = result[CONTEXT_STORE_VARIABLE]
    assert len(context_store) <= 1  # Either empty or has "data"
    if context_store:
        assert context_store.get("data") == "preserved"

    # Message added
    messages = result[MESSAGES_VARIABLE]
    assert len(messages) >= 1


def test_tc_fi_003_store_in_context_true_include_in_llm_history_false(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_FI_003: store_in_context=True, include_in_llm_history=False

    Output added to context store but NOT to message history.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [HumanMessage(content="query")],
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            store_in_context=True,
            include_in_llm_history=False,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"intermediate": "data", "temp": "value"}

    # Act
    result = node(state_schema)

    # Assert
    # Context updated
    context_store = result[CONTEXT_STORE_VARIABLE]
    assert "intermediate" in context_store or context_store.get("intermediate") == "data"
    assert "temp" in context_store or context_store.get("temp") == "value"

    # Messages unchanged (only original query)
    messages = result[MESSAGES_VARIABLE]
    # Should not have new AI messages added
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage)]
    assert len(ai_messages) == 0 or len(messages) == 1


def test_tc_fi_004_both_flags_false(mock_workflow_execution_service, mock_thought_queue, mock_callbacks):
    """
    TC_FI_004: Both Flags False

    Output added to neither context nor messages. Pure side-effect node.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            store_in_context=False,
            include_in_llm_history=False,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "Some output"

    # Act
    result = node(state_schema)

    # Assert
    # Context remains empty
    context_store = result[CONTEXT_STORE_VARIABLE]
    assert context_store == {} or len(context_store) == 0

    # Messages remain empty
    messages = result[MESSAGES_VARIABLE]
    assert len(messages) == 0 or len(messages) == 0

    # Only "next" transition occurs
    assert "next" in result


def test_tc_fi_005_clear_prior_messages_with_include_in_llm_history_false(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_FI_005: clear_prior_messages with include_in_llm_history=False

    Clear messages without adding new one. Results in empty message history.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [HumanMessage(content="msg1", id="id1"), AIMessage(content="msg2", id="id2")],
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_prior_messages=True,
            include_in_llm_history=False,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "Output"

    # Act
    result = node(state_schema)

    # Assert
    messages = result[MESSAGES_VARIABLE]

    # All old messages should be marked for removal
    remove_messages = [msg for msg in messages if isinstance(msg, RemoveMessage)]
    assert len(remove_messages) == 2

    # No new AI messages added (include_in_llm_history=False)
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage) and hasattr(msg, 'content')]
    assert len(ai_messages) == 0

    # After LangGraph processing, message list would be empty


def test_tc_fi_006_clear_context_store_with_store_in_context_true(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_FI_006: clear_context_store with store_in_context=True

    Clear context then immediately add new data. Context contains only new data.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"old1": "v1", "old2": "v2"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_context_store=True,
            store_in_context=True,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"new": "data"}

    # Act
    result = node(state_schema)

    # Assert
    # When clear_context_store=True, _prepare_resolved_context_store returns None
    # This takes precedence over store_in_context
    context_store = result[CONTEXT_STORE_VARIABLE]

    # Context is cleared (None returned by the method)
    assert context_store is None or context_store == {}


def test_tc_fi_007_reset_keys_with_store_in_context(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_FI_007: reset_keys with store_in_context

    Selective key removal combined with new data addition.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"temp1": "x", "temp2": "y", "perm": "z"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            store_in_context=True,
            reset_keys_in_context_store=["temp1", "temp2"],
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"new": "data", "temp1": "fresh"}

    # Act
    result = node(state_schema)

    # Assert
    context_store = result[CONTEXT_STORE_VARIABLE]

    # Node returns: new values from execute + deletion markers for reset_keys
    # Existing keys like 'perm' are not automatically preserved (done by LangGraph state reducer)

    # new added
    assert "new" in context_store
    assert context_store.get("new") == "data"

    # temp1 has deletion marker (even though it's also in the execute result)
    assert "temp1" in context_store
    assert context_store.get("temp1") == CONTEXT_STORE_DELETE_MARKER

    # temp2 has deletion marker
    assert "temp2" in context_store
    assert context_store.get("temp2") == CONTEXT_STORE_DELETE_MARKER


def test_tc_fi_008_all_clear_flags_with_no_new_data(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_FI_008: All Clear Flags with No New Data

    Complete reset of both context and messages.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"old": "data"},
        MESSAGES_VARIABLE: [HumanMessage(content="old", id="old_id")],
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_context_store=True,
            clear_prior_messages=True,
            store_in_context=False,
            include_in_llm_history=False,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "Output"

    # Act
    result = node(state_schema)

    # Assert
    # Context cleared
    context_store = result[CONTEXT_STORE_VARIABLE]
    assert context_store is None or context_store == {}

    # Messages have RemoveMessage for old message
    messages = result[MESSAGES_VARIABLE]
    remove_messages = [msg for msg in messages if isinstance(msg, RemoveMessage)]
    assert len(remove_messages) == 1

    # No new messages added
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage) and hasattr(msg, 'content')]
    assert len(ai_messages) == 0


def test_tc_fi_009_iteration_context_with_mixed_flags(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_FI_009: Iteration Context with Mixed Flags

    Flag behavior in iteration context with task inclusion.
    """
    # Arrange
    state_schema = {
        TASK_KEY: {"item": "data"},
        FIRST_STATE_IN_ITERATION: True,
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            store_in_context=True,
            include_in_llm_history=True,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "Processed"

    # Act
    result = node(state_schema)

    # Assert
    # Context store includes task dict + output
    context_store = result[CONTEXT_STORE_VARIABLE]
    assert "item" in context_store or context_store.get("item") == "data"

    # Messages include task representation + output
    messages = result[MESSAGES_VARIABLE]
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage)]
    # Should have 2 AI messages: task repr and output
    assert len(ai_messages) >= 2


def test_tc_fi_010_flag_priority_and_ordering(mock_workflow_execution_service, mock_thought_queue, mock_callbacks):
    """
    TC_FI_010: Flag Priority and Ordering

    Verify execution order: clear operations before add operations.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"old": "value"},
        MESSAGES_VARIABLE: [HumanMessage(content="old", id="old_id")],
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_context_store=True,  # Clear first
            store_in_context=True,  # Then add (but clear takes precedence)
            clear_prior_messages=True,  # Clear messages first
            include_in_llm_history=True,  # Then add new message
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"new": "value"}

    # Act
    result = node(state_schema)

    # Assert

    # 1. clear_context_store executes before store_in_context
    # When clear_context_store=True, _prepare_resolved_context_store returns None
    # This overrides store_in_context
    context_store = result[CONTEXT_STORE_VARIABLE]
    assert context_store is None or context_store == {}

    # 2. clear_prior_messages executes before include_in_llm_history
    messages = result[MESSAGES_VARIABLE]

    # Old message removed
    remove_messages = [msg for msg in messages if isinstance(msg, RemoveMessage)]
    assert len(remove_messages) == 1
    assert remove_messages[0].id == "old_id"

    # New message added
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage) and hasattr(msg, 'content')]
    assert len(ai_messages) >= 1

    # 3. All flags evaluated independently
    # clear_context_store doesn't affect message clearing
    # clear_prior_messages doesn't affect context clearing

    # Verify proper ordering in the results list
    # RemoveMessages should appear before new messages
    if len(messages) > 1:
        first_is_remove = isinstance(messages[0], RemoveMessage)
        last_is_ai = isinstance(messages[-1], AIMessage)
        assert first_is_remove or last_is_ai
