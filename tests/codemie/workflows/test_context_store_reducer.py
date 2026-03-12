# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
Test Area: Context Store Reducer — append_to_context semantics

Tests for add_or_replace_context_store reducer, focusing on:
- Accumulation behaviour via CONTEXT_STORE_APPEND_MARKER sentinel
- Backward-compatible overwrite behaviour (no marker)
- Interaction with existing markers (DELETE, KEEP_NEW_ONLY)
"""

from codemie.workflows.models import (
    add_or_replace_context_store,
    CONTEXT_STORE_APPEND_MARKER,
    CONTEXT_STORE_DELETE_MARKER,
)
from codemie.workflows.constants import CONTEXT_STORE_KEEP_NEW_ONLY_FLAG


# ---------------------------------------------------------------------------
# CONTEXT_STORE_APPEND_MARKER — accumulation semantics
# ---------------------------------------------------------------------------


def test_reducer_appends_to_empty_context():
    """
    Append to Empty Context

    When the key does not yet exist in left, the sentinel value starts a new list.
    """
    left = {}
    right = {"output": {CONTEXT_STORE_APPEND_MARKER: ["result_1"]}}

    result = add_or_replace_context_store(left, right)

    assert result["output"] == ["result_1"]


def test_reducer_appends_to_existing_list():
    """
    Append to Existing List

    When left already holds a list for the key, the new value is appended.
    """
    left = {"output": ["result_1"]}
    right = {"output": {CONTEXT_STORE_APPEND_MARKER: ["result_2"]}}

    result = add_or_replace_context_store(left, right)

    assert result["output"] == ["result_1", "result_2"]


def test_reducer_converts_scalar_to_list_when_appending():
    """
    Convert Scalar to List on First Append

    When left holds a plain scalar and right carries a sentinel, the scalar is
    wrapped into a list and the new value is appended.
    """
    left = {"output": "existing_scalar"}
    right = {"output": {CONTEXT_STORE_APPEND_MARKER: ["new_value"]}}

    result = add_or_replace_context_store(left, right)

    assert result["output"] == ["existing_scalar", "new_value"]


def test_reducer_three_branches_simulate_parallel_iteration():
    """
    Three Parallel Branches Accumulate Into One List

    Simulates three parallel iteration branches each writing their result with
    the append sentinel. After all three reductions the list contains all values
    in insertion order.
    """
    state = add_or_replace_context_store({}, {"output": {CONTEXT_STORE_APPEND_MARKER: ["branch_1"]}})
    state = add_or_replace_context_store(state, {"output": {CONTEXT_STORE_APPEND_MARKER: ["branch_2"]}})
    state = add_or_replace_context_store(state, {"output": {CONTEXT_STORE_APPEND_MARKER: ["branch_3"]}})

    assert state["output"] == ["branch_1", "branch_2", "branch_3"]


def test_reducer_mixed_append_and_overwrite_keys():
    """
    Mixed Append and Overwrite in Same Update

    Keys carrying the sentinel are accumulated; other keys are overwritten as
    usual. Both behaviours coexist in the same reducer call.
    """
    left = {"output": ["existing"], "status": "old"}
    right = {
        "output": {CONTEXT_STORE_APPEND_MARKER: ["new_item"]},
        "status": "updated",
    }

    result = add_or_replace_context_store(left, right)

    assert result["output"] == ["existing", "new_item"]
    assert result["status"] == "updated"


def test_reducer_unrelated_keys_preserved_during_append():
    """
    Unrelated Keys Preserved During Append

    Keys that are neither in left's append target nor in right are untouched.
    """
    left = {"output": ["x"], "config": "keep_me"}
    right = {"output": {CONTEXT_STORE_APPEND_MARKER: ["y"]}}

    result = add_or_replace_context_store(left, right)

    assert result["output"] == ["x", "y"]
    assert result["config"] == "keep_me"


# ---------------------------------------------------------------------------
# Backward-compatible behaviour (no CONTEXT_STORE_APPEND_MARKER)
# ---------------------------------------------------------------------------


def test_reducer_normal_overwrite_without_marker():
    """
    Normal Overwrite Without Marker

    Standard behaviour: right value replaces left value when no sentinel present.
    """
    left = {"output": "old_value", "other": "keep_me"}
    right = {"output": "new_value"}

    result = add_or_replace_context_store(left, right)

    assert result["output"] == "new_value"
    assert result["other"] == "keep_me"


def test_reducer_clear_context_when_right_is_none():
    """
    Clear Context Store When right Is None

    Existing behaviour preserved: passing None as right returns an empty dict.
    """
    left = {"output": ["a", "b"], "status": "done"}

    result = add_or_replace_context_store(left, None)

    assert result == {}


def test_reducer_delete_marker_removes_key():
    """
    DELETE Marker Still Removes Key

    Existing DELETE_MARKER behaviour is unaffected by the new APPEND_MARKER logic.
    """
    left = {"output": ["a", "b"], "temp": "remove_me"}
    right = {"temp": CONTEXT_STORE_DELETE_MARKER}

    result = add_or_replace_context_store(left, right)

    assert "temp" not in result
    assert result["output"] == ["a", "b"]


def test_reducer_keep_new_only_flag_discards_left():
    """
    KEEP_NEW_ONLY Flag Discards Left Context

    Existing KEEP_NEW_ONLY behaviour is unaffected: right values replace the
    entire context store, dropping all left entries.
    """
    left = {"old_key": "old_value"}
    right = {CONTEXT_STORE_KEEP_NEW_ONLY_FLAG: True, "new_key": "new_value"}

    result = add_or_replace_context_store(left, right)

    assert "old_key" not in result
    assert result["new_key"] == "new_value"
    assert CONTEXT_STORE_KEEP_NEW_ONLY_FLAG not in result
