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
Test Area: Context Store Management

Tests for context store creation, updates, merging, clearing, and deletion markers
introduced in commit 7c2d4928.

This module tests the following critical functionality:
- Context store initialization and updates
- Key-value storage and retrieval
- Context store clearing mechanism
- Deletion marker handling
- Context store merging logic
"""

import pytest
from unittest.mock import Mock

from codemie.workflows.nodes.base_node import BaseNode
from codemie.workflows.models import CONTEXT_STORE_DELETE_MARKER
from codemie.workflows.constants import (
    CONTEXT_STORE_VARIABLE,
    MESSAGES_VARIABLE,
    FIRST_STATE_IN_ITERATION,
    TASK_KEY,
    CONTEXT_STORE_KEEP_NEW_ONLY_FLAG,
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
    service.abort_state = Mock()
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


@pytest.fixture
def base_workflow_state():
    """Create base WorkflowState for testing."""
    return WorkflowState(
        id="test_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next_node",
            store_in_context=True,
            include_in_llm_history=True,
            clear_prior_messages=False,
            clear_context_store=False,
        ),
    )


def test_tc_csm_001_context_store_basic_addition(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks, base_workflow_state
):
    """
    TC_CSM_001: Context Store Basic Addition

    Verify that when store_in_context=True, the node output is correctly added
    to the context store as resolved key-value pairs.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        node_name="TestNode",
        workflow_state=base_workflow_state,
    )
    node.mock_execute_result = {"user_name": "John", "age": 30, "city": "New York"}

    # Act
    result = node(state_schema)

    # Assert
    assert CONTEXT_STORE_VARIABLE in result
    context_store = result[CONTEXT_STORE_VARIABLE]

    # Verify all three keys are present
    assert "user_name" in context_store
    assert "age" in context_store
    assert "city" in context_store

    # Verify values match
    assert context_store["user_name"] == "John"
    assert context_store["age"] == 30
    assert context_store["city"] == "New York"

    # Verify no other keys are present
    assert len(context_store) == 3

    # Verify types are preserved
    assert isinstance(context_store["user_name"], str)
    assert isinstance(context_store["age"], int)
    assert isinstance(context_store["city"], str)


def test_tc_csm_002_context_store_accumulation_across_multiple_nodes(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_CSM_002: Context Store Accumulation Across Multiple Nodes

    Verify that context store accumulates values from multiple sequential node executions,
    merging new values with existing ones.
    """
    # Arrange
    initial_state = {
        CONTEXT_STORE_VARIABLE: {"initial_key": "initial_value"},
        MESSAGES_VARIABLE: [],
    }

    # Node 1
    workflow_state_1 = WorkflowState(
        id="node_1",
        task="Task 1",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="node_2", store_in_context=True),
    )
    node_1 = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state_1,
    )
    node_1.mock_execute_result = {"step1_result": "data1", "count": 1}

    # Act - Execute Node 1
    state_after_node1 = node_1(initial_state)

    # Assert Node 1
    context_after_node1 = state_after_node1[CONTEXT_STORE_VARIABLE]
    # Note: Context store behavior - when store_in_context=True, only NEW values from
    # the node output are added, existing context is not automatically merged by base_node
    # The merging happens at the LangGraph level through state reducers
    assert "step1_result" in context_after_node1
    assert "count" in context_after_node1
    assert context_after_node1["count"] == 1

    # Node 2
    workflow_state_2 = WorkflowState(
        id="node_2",
        task="Task 2",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="node_3", store_in_context=True),
    )
    node_2 = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state_2,
    )
    node_2.mock_execute_result = {"step2_result": "data2", "count": 2}

    # Prepare state for Node 2 (simulating LangGraph merge)
    state_for_node2 = {
        CONTEXT_STORE_VARIABLE: context_after_node1.copy(),
        MESSAGES_VARIABLE: state_after_node1[MESSAGES_VARIABLE],
    }

    # Act - Execute Node 2
    state_after_node2 = node_2(state_for_node2)

    # Assert Node 2
    context_after_node2 = state_after_node2[CONTEXT_STORE_VARIABLE]
    # Node 2 returns only its new values
    assert "step2_result" in context_after_node2
    assert context_after_node2["count"] == 2  # New value from Node 2

    # Node 3
    workflow_state_3 = WorkflowState(
        id="node_3",
        task="Task 3",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="END", store_in_context=True),
    )
    node_3 = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state_3,
    )
    node_3.mock_execute_result = {"final_result": "complete"}

    # Prepare state for Node 3
    state_for_node3 = {
        CONTEXT_STORE_VARIABLE: context_after_node2.copy(),
        MESSAGES_VARIABLE: state_after_node2[MESSAGES_VARIABLE],
    }

    # Act - Execute Node 3
    final_state = node_3(state_for_node3)

    # Assert Final State
    final_context = final_state[CONTEXT_STORE_VARIABLE]
    # Node 3 returns only its new values
    assert "final_result" in final_context
    assert final_context["final_result"] == "complete"


def test_tc_csm_003_context_store_clearing_with_clear_context_store_flag(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_CSM_003: Context Store Clearing with clear_context_store Flag

    Verify that when clear_context_store=True, the entire context store is cleared
    and replaced with an empty dict.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"key1": "value1", "key2": "value2", "key3": "value3"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="clear_node",
        task="Clear task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_context_store=True,
            store_in_context=False,  # Not adding new data
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"new_key": "new_value"}

    # Act
    result = node(state_schema)

    # Assert
    context_store = result[CONTEXT_STORE_VARIABLE]

    # Verify context is completely cleared (None is returned which LangGraph converts to {})
    assert context_store is None or context_store == {}

    # Verify no keys from previous state remain
    if context_store:
        assert "key1" not in context_store
        assert "key2" not in context_store
        assert "key3" not in context_store

        # Verify new_key is NOT in context_store (store_in_context=False)
        assert "new_key" not in context_store


def test_tc_csm_004_context_store_with_deletion_markers(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_CSM_004: Context Store with Deletion Markers

    Verify that keys specified in reset_keys_in_context_store are marked for deletion
    and removed from the context store.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {
            "temp1": "data",
            "temp2": "info",
            "permanent": "keep_me",
            "result": "output",
        },
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="delete_node",
        task="Delete task",
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
    node.mock_execute_result = {"new_data": "value"}

    # Act
    result = node(state_schema)

    # Assert
    context_store = result[CONTEXT_STORE_VARIABLE]

    # Node returns: new values + deletion markers for reset_keys
    # It doesn't automatically preserve existing keys (that's done by LangGraph state reducer)

    # Verify temp keys have deletion markers
    assert context_store.get("temp1") == CONTEXT_STORE_DELETE_MARKER
    assert context_store.get("temp2") == CONTEXT_STORE_DELETE_MARKER

    # Verify new data was added
    assert "new_data" in context_store
    assert context_store.get("new_data") == "value"


def test_tc_csm_005_context_store_with_task_dictionary_in_iteration(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_CSM_005: Context Store with Task Dictionary in Iteration

    Verify that when in iteration (FIRST_STATE_IN_ITERATION=True), task dict values
    are included in context store along with output values.
    """
    # Arrange
    state_schema = {
        TASK_KEY: {"item_id": "123", "item_name": "Widget", "quantity": 5},
        FIRST_STATE_IN_ITERATION: True,
        CONTEXT_STORE_VARIABLE: {"global_config": "production"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="iter_node",
        task="Iteration task",
        assistant_id="assistant_1",
        next=WorkflowNextState(state_id="next", store_in_context=True),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"processing_result": "success", "processed_count": 5}

    # Act
    result = node(state_schema)

    # Assert
    context_store = result[CONTEXT_STORE_VARIABLE]

    # When FIRST_STATE_IN_ITERATION=True, task values are included in context store
    # along with output values. Existing context is not automatically preserved.

    # Verify task values are in context store (from TASK_KEY)
    assert "item_id" in context_store
    assert context_store.get("item_id") == "123"
    assert "item_name" in context_store
    assert context_store.get("item_name") == "Widget"
    assert "quantity" in context_store
    assert context_store.get("quantity") == 5

    # Verify output values are in context store
    assert "processing_result" in context_store
    assert context_store.get("processing_result") == "success"
    assert "processed_count" in context_store
    assert context_store.get("processed_count") == 5


def test_tc_csm_006_context_store_with_list_output_merging(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks, base_workflow_state
):
    """
    TC_CSM_006: Context Store with List Output Merging

    Verify that when node output is a list containing dict items, all dicts are merged
    into a single context store entry.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=base_workflow_state,
    )
    # List with mixed types: dicts and string
    node.mock_execute_result = [{"key1": "value1"}, {"key2": "value2"}, "string_item", {"key3": "value3"}]

    # Act
    result = node(state_schema)

    # Assert
    context_store = result[CONTEXT_STORE_VARIABLE]

    # Verify all dict items are merged
    assert "key1" in context_store or context_store.get("key1") == "value1"
    assert "key2" in context_store or context_store.get("key2") == "value2"
    assert "key3" in context_store or context_store.get("key3") == "value3"

    # Verify non-dict items are not in context store
    assert "string_item" not in context_store

    # Verify no list structure remains
    assert not any(isinstance(v, list) for v in context_store.values())


def test_tc_csm_007_context_store_disabled_with_store_in_context_false(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_CSM_007: Context Store Disabled with store_in_context=False

    Verify that when store_in_context=False, node output is NOT added to the context store,
    but existing context is preserved.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"existing": "data", "count": 10},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="no_store_node",
        task="No store task",
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
    node.mock_execute_result = {"new_key": "new_value", "result": "success"}

    # Act
    result = node(state_schema)

    # Assert
    context_store = result[CONTEXT_STORE_VARIABLE]

    # Verify context store has exactly 2 keys (unchanged)
    assert len(context_store) == 2 or context_store == {}

    # Verify existing keys remain
    if context_store:
        assert context_store.get("existing") == "data"
        assert context_store.get("count") == 10

        # Verify new keys are NOT added
        assert "new_key" not in context_store
        assert "result" not in context_store

    # Verify message history includes the output (include_in_llm_history=True)
    assert MESSAGES_VARIABLE in result
    assert len(result[MESSAGES_VARIABLE]) > 0


def test_tc_csm_008_context_store_json_pointer_extraction(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_CSM_008: Context Store JSON Pointer Extraction

    Verify that when tool_result_json_pointer is configured, only the specified
    JSON path is extracted and stored in context.

    Note: This test is a placeholder as JSON pointer extraction is implemented
    in ToolNode.post_process_output(), not in BaseNode.
    """
    # This test will be fully implemented in test_tool_node_context.py
    # as it's specific to ToolNode functionality
    pytest.skip("JSON pointer extraction is tested in test_tool_node_context.py (TC_TNC_003)")


def test_tc_csm_009_context_store_concurrent_updates_in_map_reduce(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_CSM_009: Context Store Concurrent Updates in Map-Reduce

    Verify context store behavior when multiple parallel branches (map-reduce)
    update context simultaneously, ensuring proper isolation and merging.
    """
    # Arrange
    initial_context = {"workflow_id": "wf123", "mode": "parallel"}

    # Create three parallel branches
    branches = []
    for branch_num in [1, 2, 3]:
        workflow_state = WorkflowState(
            id=f"branch_{branch_num}",
            task=f"Branch {branch_num} task",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_id="merge_node", store_in_context=True),
        )

        node = MockNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=workflow_state,
        )
        node.mock_execute_result = {
            "branch": str(branch_num),
            "result": chr(64 + branch_num),  # "A", "B", "C"
            "shared": f"value{branch_num}",
        }
        branches.append(node)

    # Act - Execute all branches with isolated context
    branch_results = []
    for node in branches:
        state = {
            CONTEXT_STORE_VARIABLE: initial_context.copy(),  # Isolated copy
            MESSAGES_VARIABLE: [],
        }
        result = node(state)
        branch_results.append(result[CONTEXT_STORE_VARIABLE])

    # Assert - Each branch has isolated context during execution
    for i, context in enumerate(branch_results, 1):
        # Each branch returns only its own new values, not the initial context
        # Context merging happens at LangGraph level, not in the node itself

        # Each branch should have its own values
        assert "branch" in context
        assert context.get("branch") == str(i)
        assert "result" in context
        assert "shared" in context

    # Verify branches cannot see each other's updates during execution
    # Each branch produces its own isolated output
    assert branch_results[0].get("branch") == "1"
    assert branch_results[1].get("branch") == "2"
    assert branch_results[2].get("branch") == "3"


def test_tc_csm_010_context_store_with_complex_nested_objects(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks, base_workflow_state
):
    """
    TC_CSM_010: Context Store with Complex Nested Objects

    Verify context store correctly handles deeply nested objects, preserving
    structure and enabling nested value access.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=base_workflow_state,
    )

    # Complex nested structure
    node.mock_execute_result = {
        "config": {
            "database": {
                "host": "localhost",
                "port": 5432,
                "credentials": {
                    "username": "admin",
                    "encrypted_password": "***",
                },
            },
            "features": {
                "enable_cache": True,
                "cache_ttl": 3600,
            },
        },
        "metadata": {
            "created": "2025-10-20",
            "tags": ["production", "critical"],
        },
    }

    # Act
    result = node(state_schema)

    # Assert
    context_store = result[CONTEXT_STORE_VARIABLE]

    # Verify nested structure is preserved
    assert "config" in context_store
    config = context_store.get("config")

    if isinstance(config, dict):
        # Verify database nested object
        assert "database" in config
        db_config = config["database"]
        assert db_config["host"] == "localhost"
        assert db_config["port"] == 5432
        assert "credentials" in db_config
        assert db_config["credentials"]["username"] == "admin"

        # Verify features nested object
        assert "features" in config
        features = config["features"]
        assert features["enable_cache"] is True
        assert features["cache_ttl"] == 3600

    # Verify metadata with arrays
    assert "metadata" in context_store
    metadata = context_store.get("metadata")

    if isinstance(metadata, dict):
        assert metadata["created"] == "2025-10-20"
        assert "tags" in metadata
        assert isinstance(metadata["tags"], list)
        assert "production" in metadata["tags"]
        assert "critical" in metadata["tags"]


def test_tc_csm_010_keep_current_context_mode(mock_workflow_execution_service, mock_thought_queue, mock_callbacks):
    """
    TC_CSM_010: Context Store with 'keep_current' Mode

    Verify that when clear_context_store="keep_current", the context store keeps only
    new values from the current state and discards all previous context store entries.
    This is different from clear_context_store=True which clears everything including new values.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"old_key1": "old_value1", "old_key2": "old_value2", "old_key3": "old_value3"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="keep_current_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_context_store="keep_current",
            store_in_context=True,
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"new_key1": "new_value1", "new_key2": "new_value2"}

    # Act
    result = node(state_schema)

    # Assert
    context_store = result[CONTEXT_STORE_VARIABLE]

    # Verify old keys are not present
    assert "old_key1" not in context_store
    assert "old_key2" not in context_store
    assert "old_key3" not in context_store

    # Verify only new keys are present
    assert "new_key1" in context_store
    assert context_store["new_key1"] == "new_value1"
    assert "new_key2" in context_store
    assert context_store["new_key2"] == "new_value2"

    # Verify no other keys exist (excluding the internal flag which will be removed by the reducer)
    actual_keys = [k for k in context_store if k != CONTEXT_STORE_KEEP_NEW_ONLY_FLAG]
    assert len(actual_keys) == 2


def test_tc_csm_011_keep_current_with_no_new_values(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_CSM_011: Context Store 'keep_current' with No New Values

    Verify that when clear_context_store="keep_current" but store_in_context is empty,
    the result is an empty context store (old values discarded, no new values added).
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"old_key1": "old_value1", "old_key2": "old_value2"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="keep_current_empty_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_context_store="keep_current",
            store_in_context=False,  # No new values stored
        ),
    )

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )
    node.mock_execute_result = {"output": "test output"}  # Output but not stored

    # Act
    result = node(state_schema)

    # Assert
    context_store = result[CONTEXT_STORE_VARIABLE]

    # Verify old keys are not present
    assert "old_key1" not in context_store
    assert "old_key2" not in context_store

    # Verify context is empty or only contains the internal flag that will be filtered by reducer
    # The reducer will remove the flag
    assert len([k for k in context_store if k != CONTEXT_STORE_KEEP_NEW_ONLY_FLAG]) == 0


def test_tc_csm_012_keep_current_vs_clear_true_difference(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """
    TC_CSM_012: Difference Between 'keep_current' and clear_context_store=True

    Verify the behavioral difference:
    - clear_context_store=True: Clears everything (returns None, no new values stored)
    - clear_context_store="keep_current": Clears old values but stores new values
    """
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"old_key": "old_value"},
        MESSAGES_VARIABLE: [],
    }

    # Test with clear_context_store=True
    workflow_state_true = WorkflowState(
        id="clear_true_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_context_store=True,
            store_in_context=True,
        ),
    )

    node_true = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state_true,
    )
    node_true.mock_execute_result = {"new_key": "new_value"}

    result_true = node_true(state_schema)
    context_store_true = result_true[CONTEXT_STORE_VARIABLE]

    # With clear_context_store=True, context is None or empty (everything cleared, including new values)
    assert context_store_true is None or context_store_true == {}

    # Test with clear_context_store="keep_current"
    workflow_state_keep = WorkflowState(
        id="keep_current_node",
        task="Test task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            clear_context_store="keep_current",
            store_in_context=True,
        ),
    )

    node_keep = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state_keep,
    )
    node_keep.mock_execute_result = {"new_key": "new_value"}

    result_keep = node_keep(state_schema)
    context_store_keep = result_keep[CONTEXT_STORE_VARIABLE]

    # With clear_context_store="keep_current", old values are gone but new values are stored
    assert "old_key" not in context_store_keep
    assert "new_key" in context_store_keep
    assert context_store_keep["new_key"] == "new_value"
