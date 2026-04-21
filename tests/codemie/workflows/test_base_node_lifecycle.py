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
Test Area: Base Node Lifecycle

Tests for base node execution lifecycle, state finalization, output processing,
and execution flow control.

This module tests the following critical functionality:
- Node execution lifecycle (__call__ method)
- State finalization and update
- before_execution and after_execution hooks
- Output post-processing
- Error handling and recovery
- Execution abortion logic
- append_to_context semantics for output_key and JSON schema output
"""

import pytest
from unittest.mock import Mock, patch
from langgraph.types import Command
from langchain_core.messages import HumanMessage

from codemie.workflows.nodes.base_node import BaseNode
from codemie.workflows.constants import (
    CONTEXT_STORE_VARIABLE,
    MESSAGES_VARIABLE,
    NEXT_KEY,
    ITER_SOURCE,
    ITERATION_NODE_NUMBER_KEY,
    TOTAL_ITERATIONS_KEY,
    GUARDRAIL_CHECKED_FLAG,
    PREVIOUS_EXECUTION_STATE_NAMES,
)
from codemie.workflows.models import CONTEXT_STORE_APPEND_MARKER, CONTEXT_STORE_DELETE_MARKER
from codemie.core.workflow_models import WorkflowNextState, WorkflowState, WorkflowExecutionStatusEnum, WorkflowConfig
from codemie.rest_api.models.assistant import AssistantBase
from codemie.rest_api.models.guardrail import GuardrailEntity, GuardrailSource


class MockNode(BaseNode):
    """Mock implementation of BaseNode for testing."""

    def execute(self, state_schema, execution_context):
        if hasattr(self, 'execute_impl'):
            return self.execute_impl(state_schema, execution_context)
        return self.mock_execute_result

    def get_task(self, state_schema, *args, **kwargs):
        return "Test Task"


@pytest.fixture
def mock_workflow_execution_service():
    """Create mock WorkflowExecutionService."""
    service = Mock()
    service.start_state = Mock(return_value="state_123")
    service.finish_state = Mock()
    service.abort_state = Mock()
    return service


@pytest.fixture
def mock_thought_queue():
    """Create mock ThoughtQueue."""
    return Mock()


@pytest.fixture
def mock_callbacks():
    """Create mock callbacks."""
    callback1 = Mock()
    callback1.on_node_start = Mock()
    callback1.on_node_end = Mock()
    callback1.on_node_fail = Mock()

    callback2 = Mock()
    callback2.on_node_start = Mock()
    callback2.on_node_end = Mock()
    callback2.on_node_fail = Mock()

    return [callback1, callback2]


def test_complete_node_lifecycle_execution(mock_workflow_execution_service, mock_thought_queue, mock_callbacks):
    """
    Complete Node Lifecycle Execution

    Verify complete node execution lifecycle from initialization through finalization
    with all lifecycle methods called in correct order.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    state_schema = {
        MESSAGES_VARIABLE: [],
        CONTEXT_STORE_VARIABLE: {},
        "user_input": "test input",
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"result": "success"}

    # Track method calls
    call_order = []

    original_before = node.before_execution
    original_after = node.after_execution
    original_execute = node.execute

    def track_before(*args, **kwargs):
        call_order.append("before_execution")
        return original_before(*args, **kwargs)

    def track_execute(*args, **kwargs):
        call_order.append("execute")
        return original_execute(*args, **kwargs)

    def track_after(*args, **kwargs):
        call_order.append("after_execution")
        return original_after(*args, **kwargs)

    node.before_execution = track_before
    node.execute = track_execute
    node.after_execution = track_after

    # Act
    result = node(state_schema)

    # Assert
    # Verify lifecycle methods called
    assert "before_execution" in call_order
    assert "execute" in call_order
    assert "after_execution" in call_order

    # Verify order: before -> execute -> after
    before_idx = call_order.index("before_execution")
    execute_idx = call_order.index("execute")
    after_idx = call_order.index("after_execution")
    assert before_idx < execute_idx < after_idx

    # Verify callbacks called
    assert mock_callbacks[0].on_node_start.called
    assert mock_callbacks[1].on_node_start.called
    assert mock_callbacks[0].on_node_end.called
    assert mock_callbacks[1].on_node_end.called

    # Verify workflow service called
    assert mock_workflow_execution_service.start_state.called
    assert mock_workflow_execution_service.finish_state.called

    # Verify final state structure
    assert MESSAGES_VARIABLE in result
    assert CONTEXT_STORE_VARIABLE in result
    assert NEXT_KEY in result


def test_node_execution_with_before_execution_redirect(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Node Execution with before_execution Redirect

    Verify that when before_execution() returns a Command, the node execution
    is redirected without executing the main execute() method.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    state_schema = {
        MESSAGES_VARIABLE: [],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )

    # Mock before_execution to return Command
    redirect_command = Command(goto="SUMMARIZE_MEMORY_NODE")

    def before_with_redirect(*args, **kwargs):
        return redirect_command

    node.before_execution = before_with_redirect

    # Track if execute was called
    execute_called = False

    def track_execute(*args, **kwargs):
        nonlocal execute_called
        execute_called = True
        return {"result": "should not happen"}

    node.execute = track_execute

    # Act
    result = node(state_schema)

    # Assert
    # Verify execute() was NOT called
    assert not execute_called

    # Verify Command returned directly
    assert isinstance(result, Command)
    assert result.goto == "SUMMARIZE_MEMORY_NODE"

    # Verify start_state was NOT called (redirect happens before state tracking)
    assert not mock_workflow_execution_service.start_state.called
    # finish_state should also not be called on redirect
    assert not mock_workflow_execution_service.finish_state.called


def test_node_execution_abortion_during_execute(mock_workflow_execution_service, mock_thought_queue, mock_callbacks):
    """
    Node Execution Abortion During Execute

    Verify that when workflow execution is aborted mid-execution, the node handles
    it gracefully with proper state updates.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    state_schema = {
        MESSAGES_VARIABLE: [],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        execution_id="exec_123",
    )

    # Mock _is_execution_aborted to return True after execute
    def aborted_execute(*args, **kwargs):
        return {"result": "done"}

    node.execute = aborted_execute

    # Mock _is_execution_aborted to simulate abortion after execute
    abort_calls = []

    def is_aborted_mock():
        abort_calls.append(True)
        # First call (line 158 - before execute): False
        # Second call (line 173 - after execute): True to trigger abortion
        return len(abort_calls) > 1

    node._is_execution_aborted = is_aborted_mock

    # Act
    result = node(state_schema)

    # Assert
    # When _is_execution_aborted returns True after execute, status is set to ABORTED
    # and finish_state is called with ABORTED status (not abort_state)
    # abort_state is only called when ExecutionAbortedException is raised
    assert mock_workflow_execution_service.finish_state.called
    # Verify the finish_state was called with ABORTED status
    finish_call_args = mock_workflow_execution_service.finish_state.call_args
    if finish_call_args:
        assert finish_call_args[1].get('status') == WorkflowExecutionStatusEnum.ABORTED

    # Result should be the finalized state with messages and context
    assert MESSAGES_VARIABLE in result
    assert CONTEXT_STORE_VARIABLE in result


def test_node_execution_failure_with_exception_handling(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Node Execution Failure with Exception Handling

    Verify proper exception handling when execute() raises an exception, including
    callback notifications and state recording.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    state_schema = {
        MESSAGES_VARIABLE: [],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )

    # Mock execute to raise exception
    test_exception = ValueError("Invalid input data")

    def failing_execute(*args, **kwargs):
        raise test_exception

    node.execute = failing_execute

    # Track after_execution call
    after_execution_called = False

    original_after = node.after_execution

    def track_after(*args, **kwargs):
        nonlocal after_execution_called
        after_execution_called = True
        return original_after(*args, **kwargs)

    node.after_execution = track_after

    # Act & Assert
    with pytest.raises(ValueError, match="Invalid input data"):
        node(state_schema)

    # Verify finish_state called with FAILED status
    finish_call = mock_workflow_execution_service.finish_state.call_args
    assert finish_call is not None
    # Check if FAILED status was passed
    if finish_call[1]:  # kwargs
        assert finish_call[1].get('status') == WorkflowExecutionStatusEnum.FAILED
    else:  # positional args
        assert WorkflowExecutionStatusEnum.FAILED in finish_call[0]

    # Verify callbacks on_node_fail called
    assert mock_callbacks[0].on_node_fail.called
    assert mock_callbacks[1].on_node_fail.called

    # Verify after_execution was called (finally block)
    assert after_execution_called


def test_state_finalization_with_output_key_configuration(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    State Finalization with Output Key Configuration

    Verify that when output_key is configured, the node output is stored in the
    specified state key.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next", output_key="analysis_result"),
    )

    state_schema = {
        MESSAGES_VARIABLE: [],
        CONTEXT_STORE_VARIABLE: {},
    }

    # Create a mock result - just return a simple string since output_key
    # will extract it and store it in the state
    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )

    # The execute method should return a JSON-serializable result
    node.mock_execute_result = "Analysis complete"

    # Act
    result = node(state_schema)

    # Assert
    # Verify output_key present in final state
    assert "analysis_result" in result
    # The output is JSON-serialized by post_process_output, so it's a JSON string
    assert result["analysis_result"] == '"Analysis complete"'


def test_state_finalization_with_iteration_state(mock_workflow_execution_service, mock_thought_queue, mock_callbacks):
    """
    State Finalization with Iteration State

    Verify proper state finalization when node is executing within an iteration
    context (map-reduce).
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next", iter_key="current_item", override_task=False),
    )

    state_schema = {
        MESSAGES_VARIABLE: [],
        CONTEXT_STORE_VARIABLE: {},
        ITERATION_NODE_NUMBER_KEY: 3,
        TOTAL_ITERATIONS_KEY: 5,
        "task": "item_3",
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        current_task_key="task",
    )

    node.mock_execute_result = "Processed item_3"

    # Act
    result = node(state_schema)

    # Assert
    # Verify ITER_SOURCE present
    assert ITER_SOURCE in result

    # Verify iteration counters preserved
    assert result[ITERATION_NODE_NUMBER_KEY] == 3
    assert result[TOTAL_ITERATIONS_KEY] == 5

    # Verify iter_key populated
    assert "current_item" in result
    # Since override_task=False, should use task from state_schema
    assert result["current_item"] == "item_3"


def test_execution_context_generation_for_agent_node(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Execution Context Generation for Agent Node

    Verify execution context generation includes all necessary components for
    agent execution.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    state_schema = {
        MESSAGES_VARIABLE: [],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        user="test_user",
        execution_id="exec123",
        file_names=["doc.pdf"],
    )

    node.mock_execute_result = "Result"

    # Act
    context = node.generate_execution_context(state_schema)

    # Assert
    # generate_execution_context returns {**self.kwargs}
    # Named parameters like execution_id are stored as self.execution_id, not in kwargs
    # Only **kwargs parameters are included in the context
    assert "user" in context
    assert context["user"] == "test_user"
    assert "file_names" in context
    assert context["file_names"] == ["doc.pdf"]

    # execution_id is a named parameter, so it's in self.execution_id, not in kwargs/context
    assert node.execution_id == "exec123"


def test_post_process_output_with_json_serialization(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Post-Process Output with JSON Serialization

    Verify post_process_output() correctly serializes various output types to JSON string.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )

    state_schema = {}

    # Test Case 1: Dict output
    dict_output = {"key": "value", "count": 42}
    result1 = node.post_process_output(state_schema, "task", dict_output)
    assert isinstance(result1, str)
    assert "key" in result1
    assert "value" in result1

    # Test Case 2: List output
    list_output = [1, 2, 3, "item"]
    result2 = node.post_process_output(state_schema, "task", list_output)
    assert isinstance(result2, str)
    assert "[" in result2

    # Test Case 3: String output
    string_output = "plain string"
    result3 = node.post_process_output(state_schema, "task", string_output)
    assert isinstance(result3, str)


def test_node_name_generation_with_iteration_context(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Node Name Generation with Iteration Context

    Verify node name generation includes iteration information when in map-reduce
    context.

    Note: This test is a placeholder as iteration suffix is implemented in
    AgentNode.get_node_name(), not BaseNode.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        node_name="ProcessItem",
    )

    state_schema = {}

    # Act
    node_name = node.get_node_name(state_schema)

    # Assert
    assert node_name == "ProcessItem"

    # AgentNode would add " X of Y" suffix in iteration context


def test_callback_lifecycle_integration(mock_workflow_execution_service, mock_thought_queue, mock_callbacks):
    """
    Callback Lifecycle Integration

    Verify all callback methods are invoked at correct lifecycle points with
    proper parameters.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    state_schema = {
        MESSAGES_VARIABLE: [],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )

    node.mock_execute_result = {"result": "success"}

    # Act
    node(state_schema)

    # Assert
    # Verify on_node_start called for both callbacks
    assert mock_callbacks[0].on_node_start.called
    assert mock_callbacks[1].on_node_start.called

    # Verify parameters passed to on_node_start
    call_args = mock_callbacks[0].on_node_start.call_args
    assert call_args is not None
    assert "state_id" in call_args[1] or len(call_args[0]) > 0
    assert "node_name" in call_args[1] or len(call_args[0]) > 1
    assert "task" in call_args[1] or len(call_args[0]) > 2
    assert "execution_context" in call_args[1] or len(call_args[0]) > 3

    # Verify on_node_end called for both callbacks
    assert mock_callbacks[0].on_node_end.called
    assert mock_callbacks[1].on_node_end.called

    # Verify parameters passed to on_node_end
    end_call_args = mock_callbacks[0].on_node_end.call_args
    assert end_call_args is not None
    assert "output" in end_call_args[1] or len(end_call_args[0]) > 0
    assert "execution_state_id" in end_call_args[1] or len(end_call_args[0]) > 1
    assert "execution_context" in end_call_args[1] or len(end_call_args[0]) > 2


def test_guardrails_applied_to_node_input(mock_workflow_execution_service, mock_thought_queue, mock_callbacks):
    """
    Guardrails Applied to Node Input

    Verify that guardrails are applied to the most recent message in before_execution
    when workflow_config is set.
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "workflow-123"
    workflow_config.project = "test-project"

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    state_schema = {
        MESSAGES_VARIABLE: [HumanMessage(content="Test message")],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=workflow_config,
    )
    node.mock_execute_result = {"result": "success"}

    # Act & Assert
    with patch("codemie.workflows.nodes.base_node.GuardrailService.apply_guardrails_for_entities") as mock_apply:
        mock_apply.return_value = (["Test message"], None)  # No blocking

        result = node(state_schema)

        # Verify guardrail service was called
        mock_apply.assert_called_once()
        call_kwargs = mock_apply.call_args[1]
        assert call_kwargs["input"] == ["Test message"]
        assert call_kwargs["source"] == GuardrailSource.INPUT

        # Verify message was marked as checked
        assert state_schema[MESSAGES_VARIABLE][0].additional_kwargs["metadata"][GUARDRAIL_CHECKED_FLAG] is True

        # Verify node executed successfully
        assert MESSAGES_VARIABLE in result
        assert NEXT_KEY in result


@patch("codemie.workflows.nodes.base_node.GuardrailService.apply_guardrails_for_entities")
def test_guardrails_skip_already_checked_messages(
    mock_apply_guardrails, mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Guardrails Skip Already Checked Messages

    Verify that messages with GUARDRAIL_CHECKED_FLAG are skipped during guardrail validation.
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "workflow-123"
    workflow_config.project = "test-project"

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    # Create message with guardrail check flag already set
    message = HumanMessage(content="Already checked message")
    message.additional_kwargs = {"metadata": {GUARDRAIL_CHECKED_FLAG: True}}

    state_schema = {
        MESSAGES_VARIABLE: [message],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=workflow_config,
    )
    node.mock_execute_result = {"result": "success"}

    # Act
    result = node(state_schema)

    # Assert
    # Guardrail service should NOT be called
    mock_apply_guardrails.assert_not_called()

    # Verify node executed successfully
    assert MESSAGES_VARIABLE in result
    assert NEXT_KEY in result


@patch("codemie.workflows.nodes.base_node.GuardrailService.apply_guardrails_for_entities")
def test_guardrails_block_raises_value_error(
    mock_apply_guardrails, mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Guardrails Block Raises ValueError

    Verify that blocked content raises ValueError and marks workflow as failed.
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "workflow-123"
    workflow_config.project = "test-project"

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    state_schema = {
        MESSAGES_VARIABLE: [HumanMessage(content="Blocked content")],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=workflow_config,
    )
    node.mock_execute_result = {"result": "success"}

    # Mock blocked response
    blocked_reasons = [{"policy": "contentPolicy", "type": "HATE", "reason": "BLOCKED"}]
    mock_apply_guardrails.return_value = (["BLOCKED"], blocked_reasons)

    # Mock workflow_execution_service.fail
    mock_workflow_execution_service.fail = Mock()

    # Act & Assert
    with pytest.raises(ValueError) as exc_info:
        node(state_schema)

    # Verify error message
    assert "Node input blocked by guardrails" in str(exc_info.value)

    # Verify workflow marked as failed
    mock_workflow_execution_service.fail.assert_called_once()
    fail_call = mock_workflow_execution_service.fail.call_args[1]
    assert fail_call["error_class"] == "GuardrailBlockedException"
    assert "Node input blocked by guardrails" in fail_call["error_message"]


@patch("codemie.workflows.nodes.base_node.GuardrailService.apply_guardrails_for_entities")
def test_guardrails_modify_message_content(
    mock_apply_guardrails, mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Guardrails Modify Message Content

    Verify that modified guardrailed text updates the message content in place.
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "workflow-123"
    workflow_config.project = "test-project"

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    original_content = "Original message content"
    modified_content = "Modified message content"

    state_schema = {
        MESSAGES_VARIABLE: [HumanMessage(content=original_content)],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=workflow_config,
    )
    node.mock_execute_result = {"result": "success"}

    # Mock modified response
    mock_apply_guardrails.return_value = ([modified_content], None)

    # Act
    result = node(state_schema)

    # Assert
    # Verify message content was modified
    assert state_schema[MESSAGES_VARIABLE][0].content == modified_content

    # Verify message was marked as checked
    assert state_schema[MESSAGES_VARIABLE][0].additional_kwargs["metadata"][GUARDRAIL_CHECKED_FLAG] is True

    # Verify node executed successfully
    assert MESSAGES_VARIABLE in result


@patch("codemie.workflows.nodes.base_node.GuardrailService.apply_guardrails_for_entities")
def test_guardrails_with_both_workflow_and_assistant(
    mock_apply_guardrails, mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Guardrails with Both Workflow and Assistant

    Verify that guardrails are applied for both workflow and assistant entities when
    assistant is present in execution context.
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "workflow-123"
    workflow_config.project = "test-project"

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    # Create mock assistant
    mock_assistant = Mock(spec=AssistantBase)
    mock_assistant.id = "assistant-456"
    mock_assistant.project = "test-project"

    # Create mock agent with assistant attribute
    mock_agent = Mock()
    mock_agent.assistant = mock_assistant

    state_schema = {
        MESSAGES_VARIABLE: [HumanMessage(content="Test message")],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=workflow_config,
        assistant=mock_agent,  # Pass as kwarg
    )
    node.mock_execute_result = {"result": "success"}

    # Mock guardrail response
    mock_apply_guardrails.return_value = (["Test message"], None)

    # Act
    result = node(state_schema)

    # Assert
    # Verify guardrail service was called
    mock_apply_guardrails.assert_called_once()
    call_kwargs = mock_apply_guardrails.call_args[1]

    # Verify both workflow and assistant entity configs were passed
    entity_configs = call_kwargs["entity_configs"]
    assert len(entity_configs) == 2

    # Check workflow entity config
    workflow_entity = next(ec for ec in entity_configs if ec.entity_type == GuardrailEntity.WORKFLOW)
    assert workflow_entity.entity_id == "workflow-123"
    assert workflow_entity.project_name == "test-project"

    # Check assistant entity config
    assistant_entity = next(ec for ec in entity_configs if ec.entity_type == GuardrailEntity.ASSISTANT)
    assert assistant_entity.entity_id == "assistant-456"
    assert assistant_entity.project_name == "test-project"

    # Verify node executed successfully
    assert MESSAGES_VARIABLE in result


@patch("codemie.workflows.nodes.base_node.GuardrailService.apply_guardrails_for_entities")
def test_guardrails_no_messages_skips_validation(
    mock_apply_guardrails, mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Guardrails No Messages Skips Validation

    Verify that guardrail validation is skipped when there are no messages in state.
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "workflow-123"
    workflow_config.project = "test-project"

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    state_schema = {
        MESSAGES_VARIABLE: [],  # No messages
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=workflow_config,
    )
    node.mock_execute_result = {"result": "success"}

    # Act
    result = node(state_schema)

    # Assert
    # Guardrail service should NOT be called
    mock_apply_guardrails.assert_not_called()

    # Verify node executed successfully
    assert MESSAGES_VARIABLE in result


@patch("codemie.workflows.nodes.base_node.GuardrailService.apply_guardrails_for_entities")
def test_guardrails_no_workflow_config_skips_validation(
    mock_apply_guardrails, mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Guardrails No Workflow Config Skips Validation

    Verify that guardrail validation is skipped when workflow_config is not set.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    state_schema = {
        MESSAGES_VARIABLE: [HumanMessage(content="Test message")],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=None,  # No workflow config
    )
    node.mock_execute_result = {"result": "success"}

    # Act
    result = node(state_schema)

    # Assert
    # Guardrail service should NOT be called
    mock_apply_guardrails.assert_not_called()

    # Verify node executed successfully
    assert MESSAGES_VARIABLE in result


@patch("codemie.workflows.nodes.base_node.GuardrailService.apply_guardrails_for_entities")
def test_guardrails_with_complex_message_structure(
    mock_apply_guardrails, mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Guardrails with Complex Message Structure

    Verify that guardrails correctly extract and validate text from complex message
    structures with tool calls and additional kwargs.
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "workflow-123"
    workflow_config.project = "test-project"

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    # Create complex message with tool calls
    message = HumanMessage(
        content="Main message content",
        additional_kwargs={"tool_calls": [{"function": {"name": "search", "arguments": '{"query": "test"}'}}]},
    )

    state_schema = {
        MESSAGES_VARIABLE: [message],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=workflow_config,
    )
    node.mock_execute_result = {"result": "success"}

    # Mock guardrail response
    mock_apply_guardrails.return_value = (["Main message content", "search", '{"query": "test"}'], None)

    # Act
    result = node(state_schema)

    # Assert
    # Verify guardrail service was called
    mock_apply_guardrails.assert_called_once()

    # Verify message was marked as checked
    assert message.additional_kwargs["metadata"][GUARDRAIL_CHECKED_FLAG] is True

    # Verify node executed successfully
    assert MESSAGES_VARIABLE in result


@patch("codemie.workflows.nodes.base_node.GuardrailService.apply_guardrails_for_entities")
def test_guardrails_mark_message_even_without_modification(
    mock_apply_guardrails, mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    Guardrails Mark Message Even Without Modification

    Verify that messages are always marked as checked even when content is not modified.
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "workflow-123"
    workflow_config.project = "test-project"

    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )

    original_content = "Unmodified message"

    state_schema = {
        MESSAGES_VARIABLE: [HumanMessage(content=original_content)],
        CONTEXT_STORE_VARIABLE: {},
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=workflow_config,
    )
    node.mock_execute_result = {"result": "success"}

    # Mock unmodified response (same content)
    mock_apply_guardrails.return_value = ([original_content], None)

    # Act
    result = node(state_schema)

    # Assert
    # Verify message content unchanged
    assert state_schema[MESSAGES_VARIABLE][0].content == original_content

    # Verify message was STILL marked as checked
    assert state_schema[MESSAGES_VARIABLE][0].additional_kwargs["metadata"][GUARDRAIL_CHECKED_FLAG] is True

    # Verify node executed successfully
    assert MESSAGES_VARIABLE in result


# ============================================================================
# append_to_context semantics
# ============================================================================


def test_append_to_context_output_key_wraps_with_sentinel(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    append_to_context=True with output_key — Sentinel Wrapping

    When append_to_context is True and output_key is configured, the value
    written to context_store is wrapped with CONTEXT_STORE_APPEND_MARKER so
    the reducer can accumulate results across parallel iterations.

    The direct state key (output_key itself) must NOT be set because consumers
    should only read accumulated results from context_store.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next", output_key="output", append_to_context=True),
    )

    state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "branch_result"

    # Act
    result = node(state_schema)

    # Assert
    # Direct state key must NOT be present — only context_store holds the value
    assert "output" not in result

    context_value = result[CONTEXT_STORE_VARIABLE]["output"]
    assert isinstance(context_value, dict)
    assert CONTEXT_STORE_APPEND_MARKER in context_value
    # Wrapped list contains the JSON-serialised output
    assert context_value[CONTEXT_STORE_APPEND_MARKER] == ['"branch_result"']


def test_append_to_context_false_output_key_sets_direct_key(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    append_to_context=False with output_key — Normal Behaviour Preserved

    When append_to_context is False (default), output_key writes the value both
    to the direct state key and to context_store without any sentinel wrapping.
    This verifies backward compatibility.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next", output_key="output", append_to_context=False),
    )

    state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "normal_result"

    # Act
    result = node(state_schema)

    # Assert
    # Direct state key should be set
    assert result["output"] == '"normal_result"'

    # context_store should also contain the plain (non-wrapped) value
    assert result[CONTEXT_STORE_VARIABLE]["output"] == '"normal_result"'
    assert not isinstance(result[CONTEXT_STORE_VARIABLE]["output"], dict)


def test_append_to_context_wraps_json_schema_keys(mock_workflow_execution_service, mock_thought_queue, mock_callbacks):
    """
    append_to_context=True Without output_key — JSON Schema Keys Wrapped

    When append_to_context is True and the node returns a JSON dict (e.g. via
    output_schema), all extracted keys are wrapped with the append sentinel
    in context_store.  No output_key is required.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next", append_to_context=True, store_in_context=True),
    )

    state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    # Returning a dict causes post_process_output to produce JSON,
    # which _collect_new_values then parses → new_values["output"] = "processed_value"
    node.mock_execute_result = {"output": "processed_value"}

    # Act
    result = node(state_schema)

    # Assert
    context = result[CONTEXT_STORE_VARIABLE]
    assert "output" in context

    context_value = context["output"]
    assert isinstance(context_value, dict)
    assert CONTEXT_STORE_APPEND_MARKER in context_value
    assert context_value[CONTEXT_STORE_APPEND_MARKER] == ["processed_value"]


def test_append_to_context_false_json_schema_keys_not_wrapped(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    append_to_context=False — JSON Schema Keys Not Wrapped

    When append_to_context is False (default), JSON output keys are stored in
    context_store as plain values without any sentinel wrapper.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next", append_to_context=False),
    )

    state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"output": "plain_value"}

    # Act
    result = node(state_schema)

    # Assert
    context = result[CONTEXT_STORE_VARIABLE]
    assert "output" in context
    assert context["output"] == "plain_value"
    assert not isinstance(context["output"], dict)


def test_reset_keys_takes_precedence_over_append_to_context(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    reset_keys_in_context_store overwrites append_to_context for the same key.

    When both reset_keys_in_context_store and append_to_context=True target the
    same key, _apply_deletion_markers runs after the append wrapping and replaces
    the sentinel wrapper with CONTEXT_STORE_DELETE_MARKER.  The reducer will then
    remove the key from the merged context store.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            append_to_context=True,
            store_in_context=True,
            reset_keys_in_context_store=["output"],
        ),
    )

    state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"output": "some_value"}

    # Act
    result = node(state_schema)

    # Assert: delete marker wins — key carries CONTEXT_STORE_DELETE_MARKER, not an
    # append-wrapped dict.  After the LangGraph reducer processes this update the
    # key will be absent from the merged context store.
    context = result[CONTEXT_STORE_VARIABLE]
    assert context.get("output") == CONTEXT_STORE_DELETE_MARKER
    assert not isinstance(context.get("output"), dict)


def test_previous_execution_state_id_set_when_final_state_is_dict(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    PREVIOUS_EXECUTION_STATE_ID Written into Dict Final State

    When finalize_and_update_state returns a plain dict, base_node.__call__
    must set PREVIOUS_EXECUTION_STATE_ID directly on that dict using item
    assignment, so the next node in the workflow can track its predecessor.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )
    state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}
    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "output"
    dict_final_state = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}, NEXT_KEY: ["next"]}
    node.finalize_and_update_state = Mock(return_value=dict_final_state)

    # Act
    result = node(state_schema)

    # Assert
    assert isinstance(result, dict)
    assert PREVIOUS_EXECUTION_STATE_NAMES in result
    assert result[PREVIOUS_EXECUTION_STATE_NAMES] == [""]


def test_previous_execution_state_id_added_to_command_with_no_update(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    PREVIOUS_EXECUTION_STATE_ID Added When Command Has No Update Dict

    When finalize_and_update_state returns Command(goto=END) with update=None
    (e.g. when summarization is skipped on empty message history), base_node
    must reconstruct the Command with a new update dict containing
    PREVIOUS_EXECUTION_STATE_ID rather than crashing on a None dict access.
    """
    from langgraph.constants import END

    # Arrange
    workflow_state = WorkflowState(
        id="test_node",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next"),
    )
    state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}
    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = "output"
    command_no_update = Command(goto=END)
    node.finalize_and_update_state = Mock(return_value=command_no_update)

    # Act
    result = node(state_schema)

    # Assert
    assert isinstance(result, Command)
    assert result.goto == END
    assert result.update is not None
    assert PREVIOUS_EXECUTION_STATE_NAMES in result.update
    assert result.update[PREVIOUS_EXECUTION_STATE_NAMES] == [""]


# ---------------------------------------------------------------------------
# preceding_state_ids propagation
# ---------------------------------------------------------------------------


class TestPrecedingStateName:
    """Tests that preceding_state_ids is passed to start_state and propagated."""

    def _make_node(self, mock_workflow_execution_service, mock_thought_queue, node_name="current_node"):
        workflow_state = WorkflowState(
            id=node_name,
            task="Test",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_id="next"),
        )
        node = MockNode(
            callbacks=[],
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            node_name=node_name,
            workflow_state=workflow_state,
        )
        node.mock_execute_result = "output"
        return node

    def test_passes_preceding_name_from_state_schema(self, mock_workflow_execution_service, mock_thought_queue):
        """Node reads PREVIOUS_EXECUTION_STATE_NAMES from incoming state schema and passes it to start_state."""
        node = self._make_node(mock_workflow_execution_service, mock_thought_queue, node_name="node_b")
        state_schema = {
            MESSAGES_VARIABLE: [],
            CONTEXT_STORE_VARIABLE: {},
            PREVIOUS_EXECUTION_STATE_NAMES: ["node_a"],
        }

        node(state_schema)

        mock_workflow_execution_service.start_state.assert_called_once_with(
            workflow_state_id="node_b",
            task="Test Task",
            preceding_state_ids=["node_a"],
            state_id="node_b",
            iteration_number=None,
        )

    def test_stores_current_node_name_in_final_state(self, mock_workflow_execution_service, mock_thought_queue):
        """After execution, PREVIOUS_EXECUTION_STATE_NAMES in output is a list with the current node name."""
        node = self._make_node(mock_workflow_execution_service, mock_thought_queue, node_name="node_a")
        state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

        result = node(state_schema)

        assert PREVIOUS_EXECUTION_STATE_NAMES in result
        assert result[PREVIOUS_EXECUTION_STATE_NAMES] == ["node_a"]

    def test_stores_current_node_name_in_command_update(self, mock_workflow_execution_service, mock_thought_queue):
        """When finalize_and_update_state returns a Command, the name list is injected into command.update."""
        from unittest.mock import Mock
        from langgraph.graph import END

        node = self._make_node(mock_workflow_execution_service, mock_thought_queue, node_name="node_a")
        state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}
        node.finalize_and_update_state = Mock(return_value=Command(goto=END))

        result = node(state_schema)

        assert isinstance(result, Command)
        assert result.update[PREVIOUS_EXECUTION_STATE_NAMES] == ["node_a"]

    def test_before_execution_redirect_returns_command_unchanged(
        self, mock_workflow_execution_service, mock_thought_queue
    ):
        """When before_execution returns a Command, it is returned as-is without injecting
        PREVIOUS_EXECUTION_STATE_NAMES — no node execution occurs on redirect."""
        node = self._make_node(mock_workflow_execution_service, mock_thought_queue, node_name="agent_node_1")
        state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}
        node.before_execution = lambda *a, **kw: Command(goto="summarize_memory_node")

        result = node(state_schema)

        assert isinstance(result, Command)
        assert result.goto == "summarize_memory_node"
        assert result.update is None

    def test_before_execution_redirect_preserves_existing_update(
        self, mock_workflow_execution_service, mock_thought_queue
    ):
        """When before_execution returns a Command with an existing update dict,
        the Command is returned as-is; PREVIOUS_EXECUTION_STATE_NAMES is NOT added."""
        node = self._make_node(mock_workflow_execution_service, mock_thought_queue, node_name="agent_node_1")
        state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}
        node.before_execution = lambda *a, **kw: Command(
            goto="summarize_memory_node", update={"some_key": "some_value"}
        )

        result = node(state_schema)

        assert isinstance(result, Command)
        assert result.update["some_key"] == "some_value"
        assert PREVIOUS_EXECUTION_STATE_NAMES not in result.update


class TestIterationNumber:
    """Tests that iteration_number is read from state_schema and forwarded to start_state."""

    def _make_node(self, mock_workflow_execution_service, mock_thought_queue, node_name="iter_node"):
        workflow_state = WorkflowState(
            id=node_name,
            task="Test",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_id="next"),
        )
        node = MockNode(
            callbacks=[],
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            node_name=node_name,
            workflow_state=workflow_state,
        )
        node.mock_execute_result = "output"
        return node

    def test_passes_iteration_number_from_state_schema(self, mock_workflow_execution_service, mock_thought_queue):
        """Node reads ITERATION_NODE_NUMBER_KEY from state_schema and forwards it to start_state."""
        from codemie.workflows.constants import ITERATION_NODE_NUMBER_KEY

        node = self._make_node(mock_workflow_execution_service, mock_thought_queue)
        state_schema = {
            MESSAGES_VARIABLE: [],
            CONTEXT_STORE_VARIABLE: {},
            ITERATION_NODE_NUMBER_KEY: 3,
        }

        node(state_schema)

        mock_workflow_execution_service.start_state.assert_called_once_with(
            workflow_state_id=node.node_name,
            task="Test Task",
            preceding_state_ids=None,
            state_id=node.node_name,
            iteration_number=3,
        )

    def test_passes_none_when_no_iteration_number_in_state_schema(
        self, mock_workflow_execution_service, mock_thought_queue
    ):
        """Non-iteration nodes pass iteration_number=None to start_state."""
        from codemie.workflows.constants import ITERATION_NODE_NUMBER_KEY  # noqa: F401

        node = self._make_node(mock_workflow_execution_service, mock_thought_queue)
        state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

        node(state_schema)

        mock_workflow_execution_service.start_state.assert_called_once_with(
            workflow_state_id=node.node_name,
            task="Test Task",
            preceding_state_ids=None,
            state_id=node.node_name,
            iteration_number=None,
        )


class TestStateId:
    """Tests that the raw node name (state_id) is passed to start_state, separate from the display name."""

    def _make_node(self, mock_workflow_execution_service, mock_thought_queue, node_name):
        workflow_state = WorkflowState(
            id=node_name,
            task="Test",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_id="next"),
        )
        node = MockNode(
            callbacks=[],
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            node_name=node_name,
            workflow_state=workflow_state,
        )
        node.mock_execute_result = "output"
        return node

    def test_passes_raw_node_name_as_state_id(self, mock_workflow_execution_service, mock_thought_queue):
        """start_state receives state_id equal to the node_name regardless of display name."""

        node = self._make_node(mock_workflow_execution_service, mock_thought_queue, node_name="assistant_2")
        state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

        node(state_schema)

        _, kwargs = mock_workflow_execution_service.start_state.call_args
        assert kwargs["state_id"] == "assistant_2"

    def test_state_id_is_node_name_even_when_display_name_differs(
        self, mock_workflow_execution_service, mock_thought_queue
    ):
        """state_id stays as the raw node_name even if workflow_state_id (display name) is different."""

        node = self._make_node(mock_workflow_execution_service, mock_thought_queue, node_name="assistant_2")
        # Simulate an override of get_node_name to return a computed display name
        node.get_node_name = lambda _: "assistant_2 1 of 5"
        state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

        node(state_schema)

        _, kwargs = mock_workflow_execution_service.start_state.call_args
        assert kwargs["workflow_state_id"] == "assistant_2 1 of 5"
        assert kwargs["state_id"] == "assistant_2"
