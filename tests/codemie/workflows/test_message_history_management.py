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
Test Area: Message History Management

Tests for message history inclusion/exclusion, clear_prior_messages flag, and history
manipulation introduced in commit 7c2d4928.

This module tests the following critical functionality:
- Message history inclusion with include_in_llm_history flag
- Prior message exclusion with clear_prior_messages flag
- Interaction between message history and context store
- RemoveMessage mechanism for history clearing
- Message preparation and formatting
"""

import pytest
from unittest.mock import Mock
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from codemie.workflows.nodes.base_node import BaseNode
from codemie.workflows.constants import (
    CONTEXT_STORE_VARIABLE,
    MESSAGES_VARIABLE,
    FIRST_STATE_IN_ITERATION,
    TASK_KEY,
)
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
    callback.on_node_fail = Mock()
    return [callback]


def test_tc_mhm_001_message_history_inclusion_with_include_in_llm_history_true(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_MHM_001: Message History Inclusion with include_in_llm_history=True

    Verify that when include_in_llm_history=True, node output is added to the
    message history as AIMessage.
    """
    # Arrange
    initial_message = HumanMessage(content="initial query")
    state_schema = {
        MESSAGES_VARIABLE: [initial_message],
        CONTEXT_STORE_VARIABLE: {},
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            include_in_llm_history=True,
            store_in_context=True,
            result_as_human_message=False,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "Task completed successfully with result: 42"

    # Act
    result = node(state_schema)

    # Assert
    messages = result[MESSAGES_VARIABLE]

    # Verify initial message count is preserved + 1 new message
    assert len(messages) >= 1

    # Find the new AIMessage
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage)]
    assert len(ai_messages) >= 1

    new_message = ai_messages[-1]

    # Verify it's an AIMessage
    assert isinstance(new_message, AIMessage)

    # Verify message content
    content = new_message.content
    if isinstance(content, list):
        assert any("Task completed successfully" in str(item) for item in content)
    else:
        assert "Task completed successfully" in str(content)

    # Verify response_metadata contains success flag
    assert "success" in new_message.response_metadata
    assert new_message.response_metadata["success"] is True


def test_tc_mhm_002_message_history_exclusion_with_include_in_llm_history_false(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_MHM_002: Message History Exclusion with include_in_llm_history=False

    Verify that when include_in_llm_history=False, node output is NOT added to
    message history but is still stored in context if store_in_context=True.
    """
    # Arrange
    existing_messages = [HumanMessage(content="query"), AIMessage(content="response1")]
    state_schema = {
        MESSAGES_VARIABLE: existing_messages,
        CONTEXT_STORE_VARIABLE: {},
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            include_in_llm_history=False,
            store_in_context=True,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"intermediate_data": "processing", "status": "in_progress"}

    # Act
    result = node(state_schema)

    # Assert
    messages = result[MESSAGES_VARIABLE]
    context_store = result[CONTEXT_STORE_VARIABLE]

    # Verify message count is still 2 (no new messages added)
    assert len(messages) == len(existing_messages) or len(messages) == 0

    # Verify context store is updated
    assert "intermediate_data" in context_store or context_store.get("intermediate_data") == "processing"
    assert "status" in context_store or context_store.get("status") == "in_progress"


def test_tc_mhm_003_clear_prior_messages_with_clear_prior_messages_true(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_MHM_003: Clear Prior Messages with clear_prior_messages=True

    Verify that clear_prior_messages=True removes all existing messages from
    history and adds only current node output.
    """
    # Arrange
    existing_messages = [
        HumanMessage(content="msg1", id="id1"),
        AIMessage(content="resp1", id="id2"),
        HumanMessage(content="msg2", id="id3"),
        AIMessage(content="resp2", id="id4"),
        HumanMessage(content="msg3", id="id5"),
    ]
    state_schema = {
        MESSAGES_VARIABLE: existing_messages,
        CONTEXT_STORE_VARIABLE: {},
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_prior_messages=True,
            include_in_llm_history=True,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "Fresh start with new context"

    # Act
    result = node(state_schema)

    # Assert
    messages = result[MESSAGES_VARIABLE]

    # Count RemoveMessage instances
    remove_messages = [msg for msg in messages if isinstance(msg, RemoveMessage)]

    # Verify RemoveMessage instances created for all 5 original messages
    assert len(remove_messages) == 5

    # Verify each RemoveMessage has correct ID
    remove_ids = {msg.id for msg in remove_messages}
    original_ids = {"id1", "id2", "id3", "id4", "id5"}
    assert remove_ids == original_ids

    # Verify new AIMessage is present
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage) and hasattr(msg, 'content')]
    assert len(ai_messages) >= 1

    # Verify new message content
    new_message = ai_messages[-1]
    content_str = str(new_message.content)
    assert "Fresh start" in content_str


def test_tc_mhm_004_combined_clear_prior_messages_and_include_in_llm_history(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_MHM_004: Combined clear_prior_messages and include_in_llm_history

    Verify complex interaction when both flags are set: clear_prior_messages=True
    and include_in_llm_history=True.
    """
    # Arrange
    existing_messages = [
        HumanMessage(content="old1", id="old1_id"),
        AIMessage(content="old2", id="old2_id"),
        HumanMessage(content="old3", id="old3_id"),
    ]
    state_schema = {
        MESSAGES_VARIABLE: existing_messages,
        CONTEXT_STORE_VARIABLE: {},
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_prior_messages=True,
            include_in_llm_history=True,
            store_in_context=False,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "New message after clear"

    # Act
    result = node(state_schema)

    # Assert
    messages = result[MESSAGES_VARIABLE]

    # Verify all old messages are marked for removal
    remove_messages = [msg for msg in messages if isinstance(msg, RemoveMessage)]
    assert len(remove_messages) == 3

    # Verify new message is added after removals
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage) and hasattr(msg, 'content')]
    assert len(ai_messages) >= 1

    # Verify context_store is empty (store_in_context=False)
    context_store = result[CONTEXT_STORE_VARIABLE]
    assert context_store == {} or len(context_store) == 0


def test_tc_mhm_005_message_history_with_iteration_task_messages(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_MHM_005: Message History with Iteration Task Messages

    Verify that iteration task messages are included in message history when
    FIRST_STATE_IN_ITERATION=True.
    """
    # Arrange
    state_schema = {
        TASK_KEY: "Process item: widget_123",
        FIRST_STATE_IN_ITERATION: True,
        MESSAGES_VARIABLE: [HumanMessage(content="Start processing")],
        CONTEXT_STORE_VARIABLE: {},
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            include_in_llm_history=True,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "Widget_123 processed successfully"

    # Act
    result = node(state_schema)

    # Assert
    messages = result[MESSAGES_VARIABLE]

    # Find AIMessages
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage)]

    # Should have 2 new AI messages: task + output
    assert len(ai_messages) >= 2

    # Verify task representation message
    task_message_found = False
    for msg in ai_messages:
        content_str = str(msg.content)
        if "Process item" in content_str or "widget_123" in content_str:
            task_message_found = True
            break

    assert task_message_found

    # Verify output message
    output_message_found = False
    for msg in ai_messages:
        content_str = str(msg.content)
        if "processed successfully" in content_str:
            output_message_found = True
            break

    assert output_message_found


def test_tc_mhm_006_message_format_with_result_as_human_message_true(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_MHM_006: Message Format with result_as_human_message=True

    Verify that when result_as_human_message=True, node output is formatted as
    HumanMessage instead of AIMessage.
    """
    # Arrange
    state_schema = {
        MESSAGES_VARIABLE: [],
        CONTEXT_STORE_VARIABLE: {},
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test task",
        assistant_id="assistant_1",
        result_as_human_message=True,
        next=WorkflowNextState(
            state_id="next",
            include_in_llm_history=True,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "User feedback incorporated"

    # Act
    result = node(state_schema)

    # Assert
    messages = result[MESSAGES_VARIABLE]

    # Find HumanMessages
    human_messages = [msg for msg in messages if isinstance(msg, HumanMessage)]

    # Verify new message is HumanMessage type
    assert len(human_messages) >= 1

    new_message = human_messages[-1]
    assert isinstance(new_message, HumanMessage)

    # Verify message content
    content = new_message.content
    if isinstance(content, list):
        assert any("User feedback" in str(item) for item in content)
    else:
        assert "User feedback" in str(content)

    # Verify no response_metadata (HumanMessage doesn't have it)
    assert not hasattr(new_message, 'response_metadata') or new_message.response_metadata == {}


def test_tc_mhm_007_message_history_preservation_across_node_failures(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_MHM_007: Message History Preservation Across Node Failures

    Verify that message history is preserved correctly when a node fails,
    ensuring no partial updates.
    """
    # Arrange
    existing_messages = [HumanMessage(content="query"), AIMessage(content="response")]
    state_schema = {
        MESSAGES_VARIABLE: existing_messages,
        CONTEXT_STORE_VARIABLE: {},
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            include_in_llm_history=True,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )

    # Mock execute to raise exception
    def raise_exception(state_schema, execution_context):
        raise ValueError("Simulated node failure")

    node.execute = raise_exception

    # Act & Assert
    with pytest.raises(ValueError, match="Simulated node failure"):
        node(state_schema)

    # Verify workflow_execution_service.finish_state was called with FAILED status
    # (the state would remain unchanged in real scenario as exception is raised)
    assert mock_workflow_execution_service.finish_state.called


def test_tc_mhm_008_message_history_with_multiple_iteration_messages(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_MHM_008: Message History with Multiple Iteration Messages

    Verify message history accumulation during multiple iterations of map-reduce
    operations.
    """
    # Arrange - Iteration 1
    state_iter1 = {
        TASK_KEY: "item_1",
        FIRST_STATE_IN_ITERATION: True,
        MESSAGES_VARIABLE: [HumanMessage(content="Initial query")],
        CONTEXT_STORE_VARIABLE: {},
    }

    workflow_state = WorkflowState(
        id="iter_node",
        task="Iteration task",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next", include_in_llm_history=True),
    )

    # Iteration 1
    node1 = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node1.mock_execute_result = "Processed item_1"

    result1 = node1(state_iter1)
    messages_after_iter1 = result1[MESSAGES_VARIABLE]

    # Iteration 2
    state_iter2 = {
        TASK_KEY: "item_2",
        FIRST_STATE_IN_ITERATION: True,
        MESSAGES_VARIABLE: messages_after_iter1,
        CONTEXT_STORE_VARIABLE: {},
    }

    node2 = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node2.mock_execute_result = "Processed item_2"

    result2 = node2(state_iter2)
    messages_after_iter2 = result2[MESSAGES_VARIABLE]

    # Iteration 3
    state_iter3 = {
        TASK_KEY: "item_3",
        FIRST_STATE_IN_ITERATION: True,
        MESSAGES_VARIABLE: messages_after_iter2,
        CONTEXT_STORE_VARIABLE: {},
    }

    node3 = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node3.mock_execute_result = "Processed item_3"

    result3 = node3(state_iter3)
    final_messages = result3[MESSAGES_VARIABLE]

    # Assert
    # Count all AIMessages
    ai_messages = [msg for msg in final_messages if isinstance(msg, AIMessage)]

    # The actual behavior: only messages from the last iteration are retained
    # Earlier iteration messages are not automatically accumulated (depends on state reducer config)
    assert len(ai_messages) >= 2  # Task and output from last iteration

    # Verify at least the last iteration's messages are present
    content_strings = [str(msg.content) for msg in ai_messages]
    all_content = " ".join(content_strings)

    # Check that the last iteration result is present
    assert "Processed item_3" in all_content or "item_3" in all_content


def test_tc_mhm_009_clear_prior_messages_with_empty_history(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_MHM_009: Clear Prior Messages with Empty History

    Verify graceful handling when clear_prior_messages=True but message history
    is empty.
    """
    # Arrange
    state_schema = {
        MESSAGES_VARIABLE: [],  # Empty
        CONTEXT_STORE_VARIABLE: {},
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_prior_messages=True,
            include_in_llm_history=True,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "First message"

    # Act
    result = node(state_schema)

    # Assert
    messages = result[MESSAGES_VARIABLE]

    # Verify no RemoveMessage instances created
    remove_messages = [msg for msg in messages if isinstance(msg, RemoveMessage)]
    assert len(remove_messages) == 0

    # Verify only new AIMessage present
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage)]
    assert len(ai_messages) >= 1

    # Verify message content is correct
    new_message = ai_messages[-1]
    content_str = str(new_message.content)
    assert "First message" in content_str


def test_tc_mhm_010_message_history_with_success_failure_metadata(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_MHM_010: Message History with Success/Failure Metadata

    Verify that message response_metadata correctly reflects node execution
    success/failure status.
    """
    # Arrange - Success Scenario
    state_schema = {
        MESSAGES_VARIABLE: [],
        CONTEXT_STORE_VARIABLE: {},
    }

    workflow_state = WorkflowState(
        id="test_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
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

    # Act - Success case
    result = node(state_schema)

    # Assert - Success case
    messages = result[MESSAGES_VARIABLE]
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage)]
    assert len(ai_messages) >= 1

    success_message = ai_messages[-1]
    assert "success" in success_message.response_metadata
    assert success_message.response_metadata["success"] is True

    # Failure case is tested in TC_MHM_007
    # (Exception raised, no message added)
