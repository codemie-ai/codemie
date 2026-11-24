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

"""The shared context write rules, used by the executor, the nodes and the save-time contract linker."""

from types import SimpleNamespace

import pytest

from codemie.core.workflow_models.workflow_models import WorkflowNextState, WorkflowState
from codemie.workflows.constants import (
    CONTEXT_STORE_APPEND_MARKER,
    CONTEXT_STORE_DELETE_MARKER,
    CONTEXT_STORE_KEEP_NEW_ONLY_FLAG,
)
from codemie.workflows.context_rules import (
    StateNodeType,
    build_context_update,
    iteration_branch_context,
    output_key_context_value,
    state_node_type,
)
from codemie.workflows.contracts.effects import NodeKind, classify_node_kind
from codemie.workflows.workflow import WorkflowExecutor


def _next(**flags) -> WorkflowNextState:
    return WorkflowNextState(state_id="b", **flags)


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        pytest.param({}, {"a": 1}, id="spread"),
        pytest.param({"clear_context_store": True}, None, id="full-reset"),
        pytest.param(
            {"clear_context_store": True, "output_key": "k"},
            {CONTEXT_STORE_KEEP_NEW_ONLY_FLAG: True},
            id="full-reset-with-output-key",
        ),
        pytest.param({"store_in_context": False}, {}, id="writing-off"),
        pytest.param({"append_to_context": True}, {"a": {CONTEXT_STORE_APPEND_MARKER: [1]}}, id="append"),
        pytest.param(
            {"clear_context_store": "keep_current"}, {"a": 1, CONTEXT_STORE_KEEP_NEW_ONLY_FLAG: True}, id="keep-current"
        ),
        pytest.param(
            {"reset_keys_in_context_store": ["a", "z"]},
            {"a": CONTEXT_STORE_DELETE_MARKER, "z": CONTEXT_STORE_DELETE_MARKER},
            id="reset-wins-over-own-write",
        ),
    ],
)
def test_build_context_update(flags, expected):
    assert build_context_update(_next(**flags), lambda: {"a": 1}) == expected


def test_build_context_update_collects_only_when_the_values_are_kept():
    def collect():
        raise AssertionError("collected")

    assert build_context_update(_next(clear_context_store=True), collect) is None
    assert build_context_update(_next(store_in_context=False), collect) == {}


def test_output_key_context_value_wraps_only_when_accumulating():
    assert output_key_context_value(_next(), "v") == "v"
    assert output_key_context_value(_next(append_to_context=True), "v") == {CONTEXT_STORE_APPEND_MARKER: ["v"]}


def test_iteration_branch_context_matches_the_executor():
    store = {"a": 1, "b": 2}
    state = WorkflowState(id="s", assistant_id="x", next=_next(include_in_iterator_context=["a"]))

    assert WorkflowExecutor._build_parallel_context(store, state, True) is store
    assert WorkflowExecutor._build_parallel_context(store, state, False) == {"a": 1}
    assert iteration_branch_context(store, ["*"], nested=False) == store
    assert iteration_branch_context(store, ["*"], nested=False) is not store


@pytest.mark.parametrize(
    ("fields", "node_type", "kind"),
    [
        ({"assistant_id": "x", "tool_id": "t"}, StateNodeType.AGENT, NodeKind.AGENT),
        ({"tool_id": "t", "workflow_id": "w"}, StateNodeType.TOOL, NodeKind.TOOL),
        ({"workflow_id": "w"}, StateNodeType.SUB_WORKFLOW, NodeKind.SUB_WORKFLOW),
        ({}, None, NodeKind.UNKNOWN),
    ],
)
def test_the_linker_classifies_a_state_as_the_executor_builds_it(fields, node_type, kind):
    state = SimpleNamespace(**fields)

    assert state_node_type(state) is node_type
    assert classify_node_kind(state) is kind
