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
Test Area: Context Store Reducers

Tests for add_or_replace_context_store reducer, deletion markers, and merge logic
introduced in commit 7c2d4928.

This module tests the following critical functionality:
- Context store merge logic
- Value override behavior
- Context store clearing with None
- Deletion marker filtering
- LangGraph integration
"""

from codemie.workflows.models import add_or_replace_context_store, CONTEXT_STORE_DELETE_MARKER


def test_tc_csr_001_basic_context_store_merge():
    """
    TC_CSR_001: Basic Context Store Merge

    Verify basic merge of two dicts in add_or_replace_context_store reducer.
    """
    # Arrange
    left = {"a": "1", "b": "2"}
    right = {"c": "3"}

    # Act
    result = add_or_replace_context_store(left, right)

    # Assert
    assert result == {"a": "1", "b": "2", "c": "3"}
    assert len(result) == 3


def test_tc_csr_002_context_store_value_override():
    """
    TC_CSR_002: Context Store Value Override

    Verify that right dict values override left dict values for same keys.
    """
    # Arrange
    left = {"key": "old_value"}
    right = {"key": "new_value"}

    # Act
    result = add_or_replace_context_store(left, right)

    # Assert
    assert result == {"key": "new_value"}
    assert result["key"] == "new_value"


def test_tc_csr_003_context_store_clear_with_none():
    """
    TC_CSR_003: Context Store Clear with None

    Verify that passing None as right value clears the context store.
    """
    # Arrange
    left = {"a": "1", "b": "2"}
    right = None

    # Act
    result = add_or_replace_context_store(left, right)

    # Assert
    assert result == {}
    assert len(result) == 0


def test_tc_csr_004_deletion_marker_filtering():
    """
    TC_CSR_004: Deletion Marker Filtering

    Verify keys with __DELETE_KEY__ marker are filtered out from result.
    """
    # Arrange
    left = {"a": "1", "b": "2", "c": "3"}
    right = {"b": CONTEXT_STORE_DELETE_MARKER, "d": "4"}

    # Act
    result = add_or_replace_context_store(left, right)

    # Assert
    assert "a" in result
    assert result["a"] == "1"

    assert "b" not in result  # Deleted

    assert "c" in result
    assert result["c"] == "3"

    assert "d" in result
    assert result["d"] == "4"

    assert len(result) == 3  # a, c, d (b is deleted)


def test_tc_csr_005_multiple_deletion_markers():
    """
    TC_CSR_005: Multiple Deletion Markers

    Test multiple keys with deletion markers in single update.
    """
    # Arrange
    left = {"key1": "val1", "key2": "val2", "key3": "val3", "key4": "val4"}
    right = {
        "key1": CONTEXT_STORE_DELETE_MARKER,
        "key2": CONTEXT_STORE_DELETE_MARKER,
        "key5": "val5",
    }

    # Act
    result = add_or_replace_context_store(left, right)

    # Assert
    assert "key1" not in result
    assert "key2" not in result
    assert "key3" in result
    assert result["key3"] == "val3"
    assert "key4" in result
    assert result["key4"] == "val4"
    assert "key5" in result
    assert result["key5"] == "val5"

    assert len(result) == 3  # key3, key4, key5


def test_tc_csr_006_empty_dict_merge():
    """
    TC_CSR_006: Empty Dict Merge

    Verify merging empty dict with populated dict works correctly.
    """
    # Arrange
    left = {}
    right = {"key": "value"}

    # Act
    result = add_or_replace_context_store(left, right)

    # Assert
    assert result == {"key": "value"}
    assert len(result) == 1


def test_tc_csr_007_deletion_marker_on_nonexistent_key():
    """
    TC_CSR_007: Deletion Marker on Non-Existent Key

    Verify no error when deletion marker applied to non-existent key.
    """
    # Arrange
    left = {"existing": "value"}
    right = {"nonexistent": CONTEXT_STORE_DELETE_MARKER, "new": "data"}

    # Act
    result = add_or_replace_context_store(left, right)

    # Assert
    assert "existing" in result
    assert result["existing"] == "value"

    assert "nonexistent" not in result  # Never existed, marker filtered out

    assert "new" in result
    assert result["new"] == "data"

    assert len(result) == 2


def test_tc_csr_008_complex_nested_object_merge():
    """
    TC_CSR_008: Complex Nested Object Merge

    Test nested objects are replaced, not deep-merged.
    """
    # Arrange
    left = {"config": {"db": {"host": "old_host", "port": 5432}}}
    right = {"config": {"db": {"host": "new_host"}}}

    # Act
    result = add_or_replace_context_store(left, right)

    # Assert
    # Right side completely replaces left side for the same key
    assert "config" in result
    config = result["config"]

    # The entire config object is replaced, not deep-merged
    assert config == {"db": {"host": "new_host"}}

    # port is not preserved because shallow merge
    assert "port" not in config.get("db", {})


def test_tc_csr_009_langgraph_integration():
    """
    TC_CSR_009: LangGraph Integration

    Test reducer with LangGraph state updates (simulating sequential merges).
    """
    # Arrange - Simulate LangGraph state updates
    initial_state = {}

    # Update 1
    update1 = {"step1": "data1"}
    state_after_1 = add_or_replace_context_store(initial_state, update1)

    # Update 2
    update2 = {"step2": "data2"}
    state_after_2 = add_or_replace_context_store(state_after_1, update2)

    # Update 3 with deletion
    update3 = {"step1": CONTEXT_STORE_DELETE_MARKER, "step3": "data3"}
    state_after_3 = add_or_replace_context_store(state_after_2, update3)

    # Assert final state
    assert "step1" not in state_after_3  # Deleted
    assert state_after_3["step2"] == "data2"  # Preserved
    assert state_after_3["step3"] == "data3"  # Added

    assert len(state_after_3) == 2


def test_tc_csr_010_concurrent_merge_operations():
    """
    TC_CSR_010: Concurrent Merge Operations

    Test reducer behavior in parallel branches (simulating map-reduce).
    """
    # Arrange - Initial state
    initial_state = {"global": "config"}

    # Simulate parallel branches
    branch1_update = {"branch": "1", "result": "A"}
    branch2_update = {"branch": "2", "result": "B"}
    branch3_update = {"branch": "3", "result": "C"}

    # Each branch merges independently with initial state
    branch1_result = add_or_replace_context_store(initial_state, branch1_update)
    branch2_result = add_or_replace_context_store(initial_state, branch2_update)
    branch3_result = add_or_replace_context_store(initial_state, branch3_update)

    # Assert each branch has isolated updates
    assert branch1_result == {"global": "config", "branch": "1", "result": "A"}
    assert branch2_result == {"global": "config", "branch": "2", "result": "B"}
    assert branch3_result == {"global": "config", "branch": "3", "result": "C"}

    # Simulate merge of parallel results (last one wins for conflicting keys)
    merged_12 = add_or_replace_context_store(branch1_result, branch2_update)
    final_merged = add_or_replace_context_store(merged_12, branch3_update)

    # Assert final merge has last branch's values
    assert final_merged["global"] == "config"
    assert final_merged["branch"] == "3"  # Last merge wins
    assert final_merged["result"] == "C"  # Last merge wins
