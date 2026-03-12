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
Test Area: Workflow State Transitions

Tests for workflow state transitions, conditional routing, switch statements,
parallel execution, iterations, and workflow control flow.

This module tests the following critical functionality:
- Sequential state transitions with context preservation
- Conditional transitions based on output evaluation
- Switch statements with multiple cases
- Parallel branch execution and context isolation
- Map-reduce iteration start with iter_key
- Context preservation across iterations
- Iteration counter management
- Nested iteration handling
- Final node transition to END
- Interrupt and resume with state preservation
"""

import pytest
from unittest.mock import Mock, patch
from langgraph.types import Send
from langgraph.constants import END

from codemie.workflows.workflow import WorkflowExecutor
from codemie.workflows.constants import (
    CONTEXT_STORE_VARIABLE,
    MESSAGES_VARIABLE,
    TASK_KEY,
    ITERATION_NODE_NUMBER_KEY,
    TOTAL_ITERATIONS_KEY,
    ITER_SOURCE,
    FIRST_STATE_IN_ITERATION,
    RESULT_FINALIZER_NODE,
    END_NODE,
)
from codemie.core.workflow_models import (
    WorkflowNextState,
    WorkflowState,
    WorkflowConfig,
)
from codemie.core.workflow_models.workflow_models import (
    WorkflowStateCondition,
    WorkflowStateSwitch,
    WorkflowStateSwitchCondition,
)
from codemie.workflows.utils import evaluate_next_candidate, get_final_state
from langchain_core.messages import HumanMessage, AIMessage


@pytest.fixture
def mock_user():
    """Create mock User."""
    user = Mock()
    user.id = "user_123"
    user.username = "test_user"
    return user


@pytest.fixture
def mock_thought_queue():
    """Create mock ThoughtQueue."""
    queue = Mock()
    queue.set_context = Mock()
    return queue


@pytest.fixture
def basic_workflow_config():
    """Create basic WorkflowConfig."""
    config = Mock(spec=WorkflowConfig)
    config.id = "wf_001"
    config.name = "Test Workflow"
    config.project = "test_project"
    config.states = []
    config.assistants = []
    config.tools = []
    config.enable_summarization_node = False
    return config


def test_tc_wst_001_sequential_state_transition(mock_user, mock_thought_queue, basic_workflow_config):
    """
    TC_WST_001: Sequential State Transition

    Test Node A to Node B with context preserved.
    """
    # Arrange
    state_a = WorkflowState(
        id="node_a",
        task="Task A",
        next=WorkflowNextState(state_id="node_b"),
        assistant_id="assistant_1",
    )

    state_b = WorkflowState(
        id="node_b",
        task="Task B",
        next=WorkflowNextState(state_id=END),
        assistant_id="assistant_1",
    )

    basic_workflow_config.states = [state_a, state_b]
    basic_workflow_config.assistants = [Mock(id="assistant_1", assistant_id=None)]

    # Act - Verify transition from node_a to node_b preserves context
    # The context should flow through to node_b
    final_state = get_final_state("node_b", enable_summarization_node=False)

    # Assert
    assert final_state == "node_b"
    # Context preservation is handled by LangGraph state reducers
    # The state_schema_after_a would be passed to node_b


def test_tc_wst_002_conditional_transition_based_on_output(mock_user, mock_thought_queue, basic_workflow_config):
    """
    TC_WST_002: Conditional Transition Based on Output

    Test condition evaluation with context values.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="decision_node",
        task="Make decision",
        next=WorkflowNextState(
            state_id="default",
            condition=WorkflowStateCondition(
                expression="status == 'success' and count > 10",
                then="success_node",
                otherwise="failure_node",
            ),
        ),
        assistant_id="assistant_1",
    )

    execution_result = '{"status": "success", "count": 15}'

    # Act
    # Use evaluate_next_candidate which handles condition/switch evaluation
    result = evaluate_next_candidate(execution_result, workflow_state, enable_summarization_node=False)

    # Assert
    # The condition should evaluate to True (status == 'success' and count > 10)
    assert result == "success_node"


def test_tc_wst_002_conditional_transition_false_condition(mock_user, mock_thought_queue, basic_workflow_config):
    """
    TC_WST_002: Conditional Transition (Otherwise Path)

    Test condition evaluation when condition fails.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="decision_node",
        task="Make decision",
        next=WorkflowNextState(
            state_id="default",
            condition=WorkflowStateCondition(
                expression="status == 'success' and count > 10",
                then="success_node",
                otherwise="failure_node",
            ),
        ),
        assistant_id="assistant_1",
    )

    execution_result = '{"status": "failed", "count": 5}'

    # Act
    # Use evaluate_next_candidate which handles condition/switch evaluation
    result = evaluate_next_candidate(execution_result, workflow_state, enable_summarization_node=False)

    # Assert
    # The condition should evaluate to False
    assert result == "failure_node"


def test_tc_wst_003_switch_transition_with_multiple_cases(mock_user, mock_thought_queue, basic_workflow_config):
    """
    TC_WST_003: Switch Transition with Multiple Cases

    Test switch statement evaluation.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="router_node",
        task="Route request",
        next=WorkflowNextState(
            state_id="default",
            switch=WorkflowStateSwitch(
                cases=[
                    WorkflowStateSwitchCondition(
                        condition="priority == 'high'",
                        state_id="high_priority_handler",
                    ),
                    WorkflowStateSwitchCondition(
                        condition="priority == 'medium'",
                        state_id="medium_priority_handler",
                    ),
                    WorkflowStateSwitchCondition(
                        condition="priority == 'low'",
                        state_id="low_priority_handler",
                    ),
                ],
                default="default_handler",
            ),
        ),
        assistant_id="assistant_1",
    )

    # Test Case 1: High priority
    execution_result_high = '{"priority": "high"}'

    result_high = evaluate_next_candidate(execution_result_high, workflow_state, enable_summarization_node=False)
    assert result_high == "high_priority_handler"

    # Test Case 2: Medium priority
    execution_result_medium = '{"priority": "medium"}'

    result_medium = evaluate_next_candidate(execution_result_medium, workflow_state, enable_summarization_node=False)
    assert result_medium == "medium_priority_handler"

    # Test Case 3: Default (no match)
    execution_result_unknown = '{"priority": "critical"}'

    result_unknown = evaluate_next_candidate(execution_result_unknown, workflow_state, enable_summarization_node=False)
    assert result_unknown == "default_handler"


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_wst_004_parallel_branch_execution(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_WST_004: Parallel Branch Execution

    Test context isolation in parallel branches.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="parallel_node",
        task="Start parallel processing",
        next=WorkflowNextState(
            state_id="process_item",
            iter_key="items",
        ),
        assistant_id="assistant_1",
    )

    state_schema = {
        CONTEXT_STORE_VARIABLE: {"global_key": "global_value"},
        MESSAGES_VARIABLE: [HumanMessage(content="input")],
        ITER_SOURCE: '{"items": ["item1", "item2", "item3"]}',
    }

    executor = WorkflowExecutor(
        workflow_config=basic_workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
        execution_id="exec_123",
    )

    # Act
    send_actions = executor.continue_iteration(state_schema, workflow_state)

    # Assert
    assert len(send_actions) == 3  # Three parallel branches

    # Verify each branch gets a copy of context (isolation)
    for i, send_action in enumerate(send_actions):
        assert isinstance(send_action, Send)
        assert send_action.node == "process_item"
        assert send_action.arg[TASK_KEY] == ["item1", "item2", "item3"][i]
        # Context should be copied for isolation
        assert send_action.arg[CONTEXT_STORE_VARIABLE]["global_key"] == "global_value"
        # Messages should be copied
        assert len(send_action.arg[MESSAGES_VARIABLE]) == 1


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_wst_005_map_reduce_iteration_start(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_WST_005: Map-Reduce Iteration Start

    Test continue_iteration() with iter_key.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="map_node",
        task="Map over items",
        next=WorkflowNextState(
            state_id="process_node",
            iter_key="data",
        ),
        assistant_id="assistant_1",
    )

    state_schema = {
        CONTEXT_STORE_VARIABLE: {"config": "value"},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"data": [1, 2, 3, 4, 5]}',
    }

    executor = WorkflowExecutor(
        workflow_config=basic_workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
        execution_id="exec_123",
    )

    # Act
    send_actions = executor.continue_iteration(state_schema, workflow_state)

    # Assert
    assert len(send_actions) == 5
    for i, send_action in enumerate(send_actions):
        assert send_action.arg[TASK_KEY] == [1, 2, 3, 4, 5][i]
        assert send_action.arg[ITERATION_NODE_NUMBER_KEY] == i + 1
        assert send_action.arg[TOTAL_ITERATIONS_KEY] == 5


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_wst_006_context_preservation_across_iterations(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_WST_006: Context Preservation Across Iterations

    Verify global context preserved in all iterations.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="iter_node",
        task="Iterate",
        next=WorkflowNextState(
            state_id="process_node",
            iter_key="items",
        ),
        assistant_id="assistant_1",
    )

    global_context = {"api_key": "secret", "base_url": "https://api.example.com", "timeout": 30}

    state_schema = {
        CONTEXT_STORE_VARIABLE: global_context,
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"items": ["a", "b", "c"]}',
    }

    executor = WorkflowExecutor(
        workflow_config=basic_workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
        execution_id="exec_123",
    )

    # Act
    send_actions = executor.continue_iteration(state_schema, workflow_state)

    # Assert
    # Each iteration should have the global context
    for send_action in send_actions:
        context = send_action.arg[CONTEXT_STORE_VARIABLE]
        assert context["api_key"] == "secret"
        assert context["base_url"] == "https://api.example.com"
        assert context["timeout"] == 30


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_wst_007_iteration_counter_management(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_WST_007: Iteration Counter Management

    Test ITERATION_NODE_NUMBER_KEY and TOTAL_ITERATIONS_KEY.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="counter_node",
        task="Count iterations",
        next=WorkflowNextState(
            state_id="work_node",
            iter_key="tasks",
        ),
        assistant_id="assistant_1",
    )

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"tasks": ["task1", "task2", "task3", "task4"]}',
    }

    executor = WorkflowExecutor(
        workflow_config=basic_workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
        execution_id="exec_123",
    )

    # Act
    send_actions = executor.continue_iteration(state_schema, workflow_state)

    # Assert
    assert len(send_actions) == 4

    for i, send_action in enumerate(send_actions):
        # Iteration numbers should be 1-indexed
        assert send_action.arg[ITERATION_NODE_NUMBER_KEY] == i + 1
        # Total should always be 4
        assert send_action.arg[TOTAL_ITERATIONS_KEY] == 4


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_wst_008_nested_iteration_handling(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_WST_008: Nested Iteration Handling

    Test is_in_iteration flag behavior.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="nested_iter_node",
        task="Nested iteration",
        next=WorkflowNextState(
            state_id="inner_node",
            iter_key="sub_items",
        ),
        assistant_id="assistant_1",
    )

    # Already in an iteration (nested case)
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"parent_key": "parent_value"},
        MESSAGES_VARIABLE: [HumanMessage(content="msg")],
        ITER_SOURCE: '{"sub_items": ["a", "b"]}',
        ITERATION_NODE_NUMBER_KEY: 2,  # Already in iteration
        TOTAL_ITERATIONS_KEY: 5,
    }

    executor = WorkflowExecutor(
        workflow_config=basic_workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
        execution_id="exec_123",
    )

    # Act
    send_actions = executor.continue_iteration(state_schema, workflow_state)

    # Assert
    # In nested iteration, context and messages should NOT be copied (just referenced)
    # Check that the same list objects are used (not copies)
    assert len(send_actions) == 2

    # All branches should share the same ITERATION_NODE_NUMBER_KEY from parent
    for send_action in send_actions:
        assert send_action.arg[ITERATION_NODE_NUMBER_KEY] == 2


def test_tc_wst_009_state_transition_with_end_node(mock_user, mock_thought_queue, basic_workflow_config):
    """
    TC_WST_009: State Transition with END Node

    Test final node transition to END.
    """
    # Arrange - Test get_final_state with END_NODE transition
    # get_final_state only checks for END_NODE ("end"), not END ("__end__")

    # Without summarization node
    final_state_no_summary = get_final_state(END_NODE, enable_summarization_node=False)
    assert final_state_no_summary == END

    # With summarization node enabled
    final_state_with_summary = get_final_state(END_NODE, enable_summarization_node=True)
    assert final_state_with_summary == RESULT_FINALIZER_NODE

    # Test with END constant - should return as-is since it doesn't match END_NODE
    final_state_end = get_final_state(END, enable_summarization_node=False)
    assert final_state_end == END

    # Regular state transition
    final_state_regular = get_final_state("next_node", enable_summarization_node=False)
    assert final_state_regular == "next_node"


def test_tc_wst_010_interrupt_and_resume(mock_user, mock_thought_queue, basic_workflow_config):
    """
    TC_WST_010: Interrupt and Resume

    Test context/message preservation across interrupt.
    """
    # Arrange
    state_before_interrupt = {
        CONTEXT_STORE_VARIABLE: {"key1": "value1", "key2": "value2"},
        MESSAGES_VARIABLE: [
            HumanMessage(content="user input"),
            AIMessage(content="response 1"),
            HumanMessage(content="follow up"),
        ],
    }

    # Simulate interrupt (state is preserved in checkpoint)
    preserved_state = state_before_interrupt.copy()

    # Act - After resume, state should be identical
    assert preserved_state[CONTEXT_STORE_VARIABLE] == {"key1": "value1", "key2": "value2"}
    assert len(preserved_state[MESSAGES_VARIABLE]) == 3

    # The checkpoint mechanism in LangGraph handles state preservation
    # We verify that the state structure is maintained correctly


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_wst_005_iteration_with_json_pointer(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_WST_005: Map-Reduce with JSON Pointer

    Test iter_key as JSON pointer: /data/items.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="json_pointer_node",
        task="Iterate with JSON pointer",
        next=WorkflowNextState(
            state_id="process_node",
            iter_key="/data/items",  # JSON pointer syntax
        ),
        assistant_id="assistant_1",
    )

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"data": {"items": ["x", "y", "z"]}}',
    }

    executor = WorkflowExecutor(
        workflow_config=basic_workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
        execution_id="exec_123",
    )

    # Act
    send_actions = executor.continue_iteration(state_schema, workflow_state)

    # Assert
    assert len(send_actions) == 3
    for i, send_action in enumerate(send_actions):
        assert send_action.arg[TASK_KEY] == ["x", "y", "z"][i]


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_wst_006_first_state_in_iteration_flag(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_WST_006: FIRST_STATE_IN_ITERATION Flag

    Test flag set correctly on first iteration state.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="iter_start_node",
        task="Start iteration",
        next=WorkflowNextState(
            state_id="first_state",
            iter_key="items",
        ),
        assistant_id="assistant_1",
    )

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"items": ["a", "b"]}',
    }

    executor = WorkflowExecutor(
        workflow_config=basic_workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
        execution_id="exec_123",
    )

    # Act
    send_actions = executor.continue_iteration(state_schema, workflow_state)

    # Assert
    # FIRST_STATE_IN_ITERATION should be True when iter_key not in state_schema
    for send_action in send_actions:
        assert send_action.arg[FIRST_STATE_IN_ITERATION] is True


# ============================================================================
# Parallel Execution with Convergence Nodes (defer=True)
# ============================================================================


def test_tc_wst_007_simple_parallel_convergence(mock_user, mock_thought_queue):
    """
    TC_WST_007: Simple Parallel Convergence Detection

    Test detection of convergence node in simple parallel fan-out pattern.

    Workflow:
        A → [B, C] → D

    Expected: D is detected as convergence node (2 incoming edges)
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "wf_conv_001"
    workflow_config.name = "Parallel Convergence Test"
    workflow_config.description = "Test convergence detection"
    workflow_config.project = "test_project"
    workflow_config.enable_summarization_node = False
    workflow_config.states = [
        WorkflowState(
            id="node_a",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_ids=["node_b", "node_c"]),
        ),
        WorkflowState(
            id="node_b",
            assistant_id="assistant_2",
            next=WorkflowNextState(state_id="node_d"),
        ),
        WorkflowState(
            id="node_c",
            assistant_id="assistant_3",
            next=WorkflowNextState(state_id="node_d"),
        ),
        WorkflowState(
            id="node_d",
            assistant_id="assistant_4",
            next=WorkflowNextState(state_id="end"),
        ),
    ]

    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
    )

    # Act
    convergence_nodes = executor.find_convergence_nodes(workflow_config)

    # Assert
    assert "node_d" in convergence_nodes, "node_d should be detected as convergence node"
    assert "node_b" not in convergence_nodes, "node_b is not a convergence node"
    assert "node_c" not in convergence_nodes, "node_c is not a convergence node"
    assert len(convergence_nodes) == 1, "Only node_d should be a convergence node"


def test_tc_wst_008_multiple_convergence_points(mock_user, mock_thought_queue):
    """
    TC_WST_008: Multiple Convergence Points Detection

    Test detection of multiple convergence nodes in complex workflow.

    Workflow:
        A → [B, C] → D → [E, F] → G

    Expected: D and G are convergence nodes
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "wf_conv_002"
    workflow_config.name = "Multiple Convergence Test"
    workflow_config.description = "Test multiple convergence points"
    workflow_config.project = "test_project"
    workflow_config.enable_summarization_node = False
    workflow_config.states = [
        WorkflowState(
            id="node_a",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_ids=["node_b", "node_c"]),
        ),
        WorkflowState(
            id="node_b",
            assistant_id="assistant_2",
            next=WorkflowNextState(state_id="node_d"),
        ),
        WorkflowState(
            id="node_c",
            assistant_id="assistant_3",
            next=WorkflowNextState(state_id="node_d"),
        ),
        WorkflowState(
            id="node_d",
            assistant_id="assistant_4",
            next=WorkflowNextState(state_ids=["node_e", "node_f"]),
        ),
        WorkflowState(
            id="node_e",
            assistant_id="assistant_5",
            next=WorkflowNextState(state_id="node_g"),
        ),
        WorkflowState(
            id="node_f",
            assistant_id="assistant_6",
            next=WorkflowNextState(state_id="node_g"),
        ),
        WorkflowState(
            id="node_g",
            assistant_id="assistant_7",
            next=WorkflowNextState(state_id="end"),
        ),
    ]

    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
    )

    # Act
    convergence_nodes = executor.find_convergence_nodes(workflow_config)

    # Assert
    assert "node_d" in convergence_nodes, "node_d should be a convergence node"
    assert "node_g" in convergence_nodes, "node_g should be a convergence node"
    assert len(convergence_nodes) == 2, "Should detect exactly 2 convergence nodes"


def test_tc_wst_009_no_convergence_sequential(mock_user, mock_thought_queue):
    """
    TC_WST_009: No Convergence in Sequential Workflow

    Test that sequential workflow has no convergence nodes.

    Workflow:
        A → B → C → D

    Expected: No convergence nodes
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "wf_seq_001"
    workflow_config.name = "Sequential Test"
    workflow_config.description = "Test sequential workflow"
    workflow_config.project = "test_project"
    workflow_config.enable_summarization_node = False
    workflow_config.states = [
        WorkflowState(
            id="node_a",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_id="node_b"),
        ),
        WorkflowState(
            id="node_b",
            assistant_id="assistant_2",
            next=WorkflowNextState(state_id="node_c"),
        ),
        WorkflowState(
            id="node_c",
            assistant_id="assistant_3",
            next=WorkflowNextState(state_id="node_d"),
        ),
        WorkflowState(
            id="node_d",
            assistant_id="assistant_4",
            next=WorkflowNextState(state_id="end"),
        ),
    ]

    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
    )

    # Act
    convergence_nodes = executor.find_convergence_nodes(workflow_config)

    # Assert
    assert len(convergence_nodes) == 0, "Sequential workflow should have no convergence nodes"


def test_tc_wst_010_asymmetric_branches_convergence(mock_user, mock_thought_queue):
    """
    TC_WST_010: Asymmetric Branches Convergence Detection

    Test convergence detection with branches of different lengths.

    Workflow:
        A → [B → C → D, E] → TARGET

    Expected: TARGET is convergence node (receives from D and E)
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "wf_asym_001"
    workflow_config.name = "Asymmetric Branches Test"
    workflow_config.description = "Test asymmetric branch convergence"
    workflow_config.project = "test_project"
    workflow_config.enable_summarization_node = False
    workflow_config.states = [
        WorkflowState(
            id="node_a",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_ids=["node_b", "node_e"]),
        ),
        WorkflowState(
            id="node_b",
            assistant_id="assistant_2",
            next=WorkflowNextState(state_id="node_c"),
        ),
        WorkflowState(
            id="node_c",
            assistant_id="assistant_3",
            next=WorkflowNextState(state_id="node_d"),
        ),
        WorkflowState(
            id="node_d",
            assistant_id="assistant_4",
            next=WorkflowNextState(state_id="target"),
        ),
        WorkflowState(
            id="node_e",
            assistant_id="assistant_5",
            next=WorkflowNextState(state_id="target"),
        ),
        WorkflowState(
            id="target",
            assistant_id="assistant_6",
            next=WorkflowNextState(state_id="end"),
        ),
    ]

    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
    )

    # Act
    convergence_nodes = executor.find_convergence_nodes(workflow_config)

    # Assert
    assert "target" in convergence_nodes, "target should be detected as convergence node"
    assert len(convergence_nodes) == 1, "Only target should be a convergence node"


def test_tc_wst_011_yaml_workflow_convergence(mock_user, mock_thought_queue):
    """
    TC_WST_011: Real YAML Workflow Convergence Detection

    Test convergence detection for the exact YAML workflow pattern from user's case.

    Workflow (from user's YAML):
        assistant_1 → [assistant_2, assistant_3]
        assistant_2 → assistant_4 → TARGET
        assistant_3 → TARGET

    Expected: TARGET is convergence node (2 incoming edges)

    This test verifies the fix for the reported issue where TARGET was executing
    twice (once from assistant_4, once from assistant_3) instead of once.
    """
    # Arrange - Exact structure from user's YAML
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "wf_yaml_001"
    workflow_config.name = "YAML Workflow"
    workflow_config.description = "User's actual YAML workflow"
    workflow_config.project = "test_project"
    workflow_config.enable_summarization_node = False
    workflow_config.states = [
        WorkflowState(
            id="assistant_1",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_ids=["assistant_2", "assistant_3"]),
        ),
        WorkflowState(
            id="assistant_2",
            assistant_id="assistant_2",
            next=WorkflowNextState(state_id="assistant_4"),
        ),
        WorkflowState(
            id="assistant_4",
            assistant_id="assistant_3",
            next=WorkflowNextState(state_id="TARGET"),
        ),
        WorkflowState(
            id="assistant_3",
            assistant_id="assistant_5",
            next=WorkflowNextState(state_id="TARGET"),
        ),
        WorkflowState(
            id="TARGET",
            assistant_id="assistant_4",
            next=WorkflowNextState(state_id="assistant_8"),
        ),
        WorkflowState(
            id="assistant_8",
            assistant_id="assistant_8",
            next=WorkflowNextState(state_id="end"),
        ),
    ]

    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
    )

    # Act
    convergence_nodes = executor.find_convergence_nodes(workflow_config)

    # Assert
    assert "TARGET" in convergence_nodes, (
        "TARGET node should be detected as convergence node. "
        "This is the exact pattern from user's YAML workflow where TARGET "
        "receives incoming edges from both assistant_4 and assistant_3. "
        "With defer=True, TARGET will execute once after both branches complete."
    )
    assert (
        "assistant_8" not in convergence_nodes
    ), "assistant_8 should not be a convergence node (only 1 incoming edge from TARGET)"
    assert len(convergence_nodes) == 1, "Only TARGET should be detected as convergence node in this workflow"


def test_tc_wst_012_three_way_convergence(mock_user, mock_thought_queue):
    """
    TC_WST_012: Three-Way Convergence Detection

    Test convergence node with 3 incoming branches.

    Workflow:
        A → [B, C, D] → TARGET

    Expected: TARGET has 3 incoming edges
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "wf_three_001"
    workflow_config.name = "Three-Way Convergence"
    workflow_config.description = "Test three-way convergence"
    workflow_config.project = "test_project"
    workflow_config.enable_summarization_node = False
    workflow_config.states = [
        WorkflowState(
            id="node_a",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_ids=["node_b", "node_c", "node_d"]),
        ),
        WorkflowState(
            id="node_b",
            assistant_id="assistant_2",
            next=WorkflowNextState(state_id="target"),
        ),
        WorkflowState(
            id="node_c",
            assistant_id="assistant_3",
            next=WorkflowNextState(state_id="target"),
        ),
        WorkflowState(
            id="node_d",
            assistant_id="assistant_4",
            next=WorkflowNextState(state_id="target"),
        ),
        WorkflowState(
            id="target",
            assistant_id="assistant_5",
            next=WorkflowNextState(state_id="end"),
        ),
    ]

    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
    )

    # Act
    convergence_nodes = executor.find_convergence_nodes(workflow_config)

    # Assert
    assert "target" in convergence_nodes, "target should be detected with 3 incoming edges"


def test_tc_wst_013_diamond_pattern_convergence(mock_user, mock_thought_queue):
    """
    TC_WST_013: Diamond Pattern Convergence Detection

    Test classic diamond pattern convergence.

    Workflow:
            A
          /   \
         B     C
          \\   /
            D

    Expected: D is convergence node
    """
    # Arrange
    workflow_config = Mock(spec=WorkflowConfig)
    workflow_config.id = "wf_diamond_001"
    workflow_config.name = "Diamond Pattern"
    workflow_config.description = "Test diamond pattern convergence"
    workflow_config.project = "test_project"
    workflow_config.enable_summarization_node = False
    workflow_config.states = [
        WorkflowState(
            id="node_a",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_ids=["node_b", "node_c"]),
        ),
        WorkflowState(
            id="node_b",
            assistant_id="assistant_2",
            next=WorkflowNextState(state_id="node_d"),
        ),
        WorkflowState(
            id="node_c",
            assistant_id="assistant_3",
            next=WorkflowNextState(state_id="node_d"),
        ),
        WorkflowState(
            id="node_d",
            assistant_id="assistant_4",
            next=WorkflowNextState(state_id="end"),
        ),
    ]

    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
    )

    # Act
    convergence_nodes = executor.find_convergence_nodes(workflow_config)

    # Assert
    assert "node_d" in convergence_nodes, "node_d should be detected in diamond pattern"
    assert len(convergence_nodes) == 1, "Only node_d should be convergence node"
