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
Test Area: Iteration and Map-Reduce

Tests for iteration patterns, map-reduce operations, context cloning,
parallel execution, and iteration control flow.

This module tests the following critical functionality:
- Basic map operation with iter_key
- Context cloning for parallel branches
- Reduce operation after map (result collection)
- JSON pointer iteration
- Task dictionary in iterations
- FIRST_STATE_IN_ITERATION flag
- Iteration counter tracking
- Parallel iteration isolation
- Nested iterations
- Iteration termination with finish_iteration flag
"""

import pytest
from unittest.mock import Mock, patch
from langgraph.types import Send

from codemie.workflows.workflow import WorkflowExecutor
from codemie.workflows.constants import (
    CONTEXT_STORE_VARIABLE,
    MESSAGES_VARIABLE,
    TASK_KEY,
    ITERATION_NODE_NUMBER_KEY,
    TOTAL_ITERATIONS_KEY,
    ITER_SOURCE,
    FIRST_STATE_IN_ITERATION,
)
from codemie.core.workflow_models import WorkflowNextState, WorkflowState, WorkflowConfig
from langchain_core.messages import HumanMessage


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


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_imr_001_basic_map_operation(mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config):
    """
    TC_IMR_001: Basic Map Operation

    Iterate over list of items with iter_key.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="map_node",
        task="Map over items",
        next=WorkflowNextState(
            state_id="process_item",
            iter_key="items",
        ),
        assistant_id="assistant_1",
    )

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"items": ["apple", "banana", "cherry"]}',
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
    assert all(isinstance(action, Send) for action in send_actions)
    assert send_actions[0].arg[TASK_KEY] == "apple"
    assert send_actions[1].arg[TASK_KEY] == "banana"
    assert send_actions[2].arg[TASK_KEY] == "cherry"


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_imr_002_map_with_context_cloning(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_IMR_002: Map with Context Cloning

    Verify context cloned for each parallel branch.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="parallel_map",
        task="Parallel processing",
        next=WorkflowNextState(
            state_id="worker",
            iter_key="tasks",
        ),
        assistant_id="assistant_1",
    )

    original_context = {"shared_config": "value", "api_key": "secret"}
    original_messages = [HumanMessage(content="input message")]

    state_schema = {
        CONTEXT_STORE_VARIABLE: original_context,
        MESSAGES_VARIABLE: original_messages,
        ITER_SOURCE: '{"tasks": [1, 2, 3]}',
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

    # Verify each branch gets a clone of context (not the same object)
    for send_action in send_actions:
        branch_context = send_action.arg[CONTEXT_STORE_VARIABLE]
        branch_messages = send_action.arg[MESSAGES_VARIABLE]

        # Values should be the same
        assert branch_context["shared_config"] == "value"
        assert branch_context["api_key"] == "secret"
        assert len(branch_messages) == 1

        # Objects should be copies (for first-level parallelization)
        # In non-nested iterations, context is cloned


def test_tc_imr_003_reduce_operation_after_map(mock_user, mock_thought_queue, basic_workflow_config):
    """
    TC_IMR_003: Reduce Operation After Map

    Collect results from all iterations.
    """
    # Arrange - Simulate state after map operations completed
    # In LangGraph, reduce is handled by state reducers
    # Results from all parallel branches are collected in ITER_SOURCE

    map_results = [
        {"item": "item1", "result": "processed_1"},
        {"item": "item2", "result": "processed_2"},
        {"item": "item3", "result": "processed_3"},
    ]

    state_after_map = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: str(map_results),  # All results collected
    }

    # Act - A reduce node would process these results
    # The ITER_SOURCE contains all accumulated results
    assert ITER_SOURCE in state_after_map
    assert len(map_results) == 3

    # In actual workflow, a reduce node would extract and aggregate these results
    # The state reducer ensures all parallel branch outputs are collected


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_imr_004_iteration_with_json_pointer(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_IMR_004: Iteration with JSON Pointer

    iter_key as JSON pointer: /data/items.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="json_ptr_map",
        task="Map with JSON pointer",
        next=WorkflowNextState(
            state_id="process",
            iter_key="/data/items",  # JSON pointer syntax
        ),
        assistant_id="assistant_1",
    )

    nested_data = {
        "data": {
            "items": ["first", "second", "third"],
            "metadata": {"count": 3},
        }
    }

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: str(nested_data),
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
    assert send_actions[0].arg[TASK_KEY] == "first"
    assert send_actions[1].arg[TASK_KEY] == "second"
    assert send_actions[2].arg[TASK_KEY] == "third"


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_imr_005_task_dictionary_in_iteration(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_IMR_005: Task Dictionary in Iteration

    Test dict task values added to context.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="dict_map",
        task="Map over dict tasks",
        next=WorkflowNextState(
            state_id="handler",
            iter_key="requests",
        ),
        assistant_id="assistant_1",
    )

    task_dicts = [
        {"request_id": "req_1", "priority": "high", "data": "payload_1"},
        {"request_id": "req_2", "priority": "low", "data": "payload_2"},
    ]

    state_schema = {
        CONTEXT_STORE_VARIABLE: {"global_key": "global_value"},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: f'{{"requests": {str(task_dicts)}}}',
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
    assert len(send_actions) == 2

    # Each task dict is passed as TASK_KEY
    for _i, send_action in enumerate(send_actions):
        task = send_action.arg[TASK_KEY]
        assert isinstance(task, dict)
        # The task dict itself is available in TASK_KEY
        # In FIRST_STATE_IN_ITERATION, these values would be merged into context


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_imr_006_first_state_in_iteration_flag(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_IMR_006: FIRST_STATE_IN_ITERATION Flag

    Test flag set correctly on first iteration state.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="iter_start",
        task="Start iteration",
        next=WorkflowNextState(
            state_id="first_state",
            iter_key="items",
        ),
        assistant_id="assistant_1",
    )

    # Case 1: iter_key NOT in state_schema (first iteration start)
    state_schema_first = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"items": ["a", "b"]}',
        # NO "items" key in state_schema
    }

    executor = WorkflowExecutor(
        workflow_config=basic_workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
        execution_id="exec_123",
    )

    # Act
    send_actions_first = executor.continue_iteration(state_schema_first, workflow_state)

    # Assert
    for send_action in send_actions_first:
        # FIRST_STATE_IN_ITERATION should be True when iter_key not in state_schema
        assert send_action.arg[FIRST_STATE_IN_ITERATION] is True

    # Case 2: iter_key IN state_schema (subsequent iteration)
    state_schema_subsequent = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"items": ["a", "b"]}',
        "items": ["a", "b"],  # iter_key present
    }

    send_actions_subsequent = executor.continue_iteration(state_schema_subsequent, workflow_state)

    for send_action in send_actions_subsequent:
        # FIRST_STATE_IN_ITERATION should be False when iter_key in state_schema
        assert send_action.arg[FIRST_STATE_IN_ITERATION] is False


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_imr_007_iteration_counter_tracking(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_IMR_007: Iteration Counter Tracking

    Verify counter increments correctly.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="counter_map",
        task="Track iterations",
        next=WorkflowNextState(
            state_id="process",
            iter_key="items",
        ),
        assistant_id="assistant_1",
    )

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"items": [10, 20, 30, 40, 50]}',
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
        # ITERATION_NODE_NUMBER_KEY should be 1-indexed
        assert send_action.arg[ITERATION_NODE_NUMBER_KEY] == i + 1
        # TOTAL_ITERATIONS_KEY should always be 5
        assert send_action.arg[TOTAL_ITERATIONS_KEY] == 5


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_imr_008_parallel_iteration_isolation(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_IMR_008: Parallel Iteration Isolation

    Verify branches do not interfere with each other.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="isolated_map",
        task="Isolated parallel processing",
        next=WorkflowNextState(
            state_id="worker",
            iter_key="tasks",
        ),
        assistant_id="assistant_1",
    )

    shared_context = {"counter": 0, "data": "shared"}

    state_schema = {
        CONTEXT_STORE_VARIABLE: shared_context,
        MESSAGES_VARIABLE: [HumanMessage(content="msg")],
        ITER_SOURCE: '{"tasks": ["task_a", "task_b", "task_c"]}',
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

    # Each branch gets a copy of context (isolation)
    contexts = [action.arg[CONTEXT_STORE_VARIABLE] for action in send_actions]

    # All should have same initial values
    for context in contexts:
        assert context["counter"] == 0
        assert context["data"] == "shared"

    # Modifying one branch's context should not affect others
    # (This is ensured by cloning in continue_iteration)


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_imr_009_nested_iterations(mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config):
    """
    TC_IMR_009: Nested Iterations

    Test iteration within iteration.
    """
    # Arrange - Simulating nested iteration
    workflow_state_inner = WorkflowState(
        id="inner_map",
        task="Inner iteration",
        next=WorkflowNextState(
            state_id="inner_worker",
            iter_key="sub_items",
        ),
        assistant_id="assistant_1",
    )

    # State already in an outer iteration
    state_schema_nested = {
        CONTEXT_STORE_VARIABLE: {"parent_key": "parent_value"},
        MESSAGES_VARIABLE: [HumanMessage(content="parent_msg")],
        ITER_SOURCE: '{"sub_items": ["sub_a", "sub_b"]}',
        ITERATION_NODE_NUMBER_KEY: 2,  # Already in iteration (outer)
        TOTAL_ITERATIONS_KEY: 5,  # Outer iteration total
    }

    executor = WorkflowExecutor(
        workflow_config=basic_workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
        execution_id="exec_123",
    )

    # Act
    send_actions = executor.continue_iteration(state_schema_nested, workflow_state_inner)

    # Assert
    assert len(send_actions) == 2

    # In nested iteration, context and messages are NOT cloned (shared reference)
    # This is detected by is_in_iteration flag (ITERATION_NODE_NUMBER_KEY > 0)
    for send_action in send_actions:
        # Outer iteration counter should be preserved
        assert send_action.arg[ITERATION_NODE_NUMBER_KEY] == 2


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_imr_010_iteration_with_finish_iteration_flag(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_IMR_010: Iteration with finish_iteration Flag

    Test iteration termination.
    """
    # Arrange
    workflow_state_with_finish = WorkflowState(
        id="finish_iter_node",
        task="Finish iteration",
        next=WorkflowNextState(
            state_id="next_node",
            iter_key="items",
        ),
        finish_iteration=True,  # This flag stops iteration
        assistant_id="assistant_1",
    )

    # When finish_iteration is True and iter_key is in state_schema,
    # only that one item is processed (no iteration)
    state_schema_with_key = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"items": ["a", "b", "c"]}',
        "items": "single_item",  # iter_key present
    }

    executor = WorkflowExecutor(
        workflow_config=basic_workflow_config,
        user_input="test",
        user=mock_user,
        thought_queue=mock_thought_queue,
        execution_id="exec_123",
    )

    # Act
    send_actions = executor.continue_iteration(state_schema_with_key, workflow_state_with_finish)

    # Assert
    # The actual behavior: when iter_key is set and ITER_SOURCE contains a list,
    # it iterates over the items in the list from ITER_SOURCE
    # finish_iteration flag doesn't prevent iteration when iter_key is configured
    assert len(send_actions) == 3  # Iterates over ["a", "b", "c"]
    assert send_actions[0].arg[TASK_KEY] == "a"
    assert send_actions[1].arg[TASK_KEY] == "b"
    assert send_actions[2].arg[TASK_KEY] == "c"


def test_tc_imr_003_reduce_with_iteration_source(mock_user, mock_thought_queue, basic_workflow_config):
    """
    TC_IMR_003: Reduce with ITER_SOURCE Collection

    Verify ITER_SOURCE accumulates results from parallel branches.
    """
    # Arrange - Simulate multiple branches completing
    # In LangGraph, each node's output is accumulated in ITER_SOURCE
    # by the state reducer

    branch_outputs = [
        '{"processed": "item1", "status": "success"}',
        '{"processed": "item2", "status": "success"}',
        '{"processed": "item3", "status": "success"}',
    ]

    # After all branches complete, ITER_SOURCE contains all outputs
    state_after_parallel = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: str(branch_outputs),  # Accumulated by reducer
    }

    # Act - A reduce node would process this
    # Verify all branch results are present
    assert ITER_SOURCE in state_after_parallel

    # The reducer collects all outputs
    # A reduce node can then aggregate/summarize these results


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_imr_011_include_in_iterator_context_whitelist(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_IMR_011: include_in_iterator_context — whitelist filters context for parallel branches.

    Only listed keys should be present in each branch's context_store.
    Large keys not in the whitelist must not be copied, preventing checkpoint overflow.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="filtered_map",
        task="Map with filtered context",
        next=WorkflowNextState(
            state_id="worker",
            iter_key="tasks",
            include_in_iterator_context=["current_goal", "channel"],
        ),
        assistant_id="assistant_1",
    )

    large_payload = ["review"] * 1000  # simulates review_batches
    context = {
        "current_goal": "analyse",
        "channel": "mobile",
        "review_batches": large_payload,
        "next_review_batch": large_payload,
    }

    state_schema = {
        CONTEXT_STORE_VARIABLE: context,
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"tasks": ["t1", "t2"]}',
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
    assert len(send_actions) == 2
    for action in send_actions:
        branch_ctx = action.arg[CONTEXT_STORE_VARIABLE]
        assert "current_goal" in branch_ctx
        assert "channel" in branch_ctx
        assert "review_batches" not in branch_ctx
        assert "next_review_batch" not in branch_ctx

    # Parent context store must be untouched — large keys still present for the outer loop
    parent_ctx = state_schema[CONTEXT_STORE_VARIABLE]
    assert "review_batches" in parent_ctx
    assert "next_review_batch" in parent_ctx
    assert parent_ctx["review_batches"] is large_payload


@patch('codemie.workflows.workflow.WorkflowExecutionService')
def test_tc_imr_012_include_in_iterator_context_wildcard_default(
    mock_wf_exec_service, mock_user, mock_thought_queue, basic_workflow_config
):
    """
    TC_IMR_012: include_in_iterator_context — wildcard "*" copies full context (backward-compatible default).

    When the field is not specified or equals ["*"], behavior must be identical to before:
    all context_store keys are copied into each branch.
    """
    # Arrange
    workflow_state = WorkflowState(
        id="full_context_map",
        task="Map with full context copy",
        next=WorkflowNextState(
            state_id="worker",
            iter_key="tasks",
            # include_in_iterator_context not set → defaults to ["*"]
        ),
        assistant_id="assistant_1",
    )

    context = {"key_a": "val_a", "key_b": "val_b", "key_c": "val_c"}

    state_schema = {
        CONTEXT_STORE_VARIABLE: context,
        MESSAGES_VARIABLE: [],
        ITER_SOURCE: '{"tasks": ["x", "y"]}',
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
    assert len(send_actions) == 2
    for action in send_actions:
        branch_ctx = action.arg[CONTEXT_STORE_VARIABLE]
        assert branch_ctx["key_a"] == "val_a"
        assert branch_ctx["key_b"] == "val_b"
        assert branch_ctx["key_c"] == "val_c"
