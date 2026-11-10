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

"""Context reset through a compiled LangGraph, wired with the workflow engine's own conditional-edge routing.

A conditional edge makes LangGraph apply a node's update twice: to a temporary state for routing, then to the real
state. Calling the reducer directly cannot reproduce that, so these tests run real nodes through a real graph.
"""

import json
from typing import Annotated, Any
from unittest.mock import Mock

from langgraph.graph import END, START, StateGraph

from codemie.core.workflow_models import WorkflowNextState, WorkflowState
from codemie.workflows.constants import (
    CONTEXT_STORE_KEEP_NEW_ONLY_FLAG,
    CONTEXT_STORE_VARIABLE,
    ITER_SOURCE,
    MESSAGES_VARIABLE,
)
from codemie.workflows.models import AgentMessages, add_or_replace_context_store
from codemie.workflows.nodes.base_node import BaseNode
from codemie.workflows.workflow import WorkflowExecutor

PRIOR_STORE = {"old1": "v1", "old2": "v2"}
ITEMS = ["x", "y"]


class _Schema(AgentMessages):
    """AgentMessages plus the extra channels the nodes and Send payloads write to."""

    iteration_source: str
    task: Any
    first_state_in_iteration: bool
    prev_state_names_to_merge: Annotated[list, lambda left, right: right]
    # Top-level output keys; branches write distinct keys, or the same key through a last-wins reducer
    out_a: str
    out_b: str
    result: Annotated[Any, lambda left, right: right]


class _StepNode(BaseNode):
    """Real BaseNode (so finalize_and_update_state builds the real update) with a canned execution result."""

    def execute(self, state_schema, execution_context):
        return {"new": "data"}

    def get_task(self, state_schema, *args, **kwargs):
        return "Test Task"


def _node(state: WorkflowState) -> _StepNode:
    service = Mock()
    service.start_state = Mock(return_value="state_123")
    return _StepNode(
        callbacks=[Mock()],
        workflow_execution_service=service,
        thought_queue=Mock(),
        workflow_state=state,
    )


def _state(node_id: str, **next_kwargs) -> WorkflowState:
    return WorkflowState(id=node_id, task="Test", assistant_id="assistant_1", next=WorkflowNextState(**next_kwargs))


def _splitter_node(_state_schema):
    """Hands the items to the iterator, leaving the context store untouched."""
    return {ITER_SOURCE: json.dumps(ITEMS)}


def _passthrough_node(_state_schema):
    """Changes nothing, so the store a test reads is exactly what the node under test left behind."""
    return {}


def _run_graph(states: list[WorkflowState], entry: str, plain_nodes: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compile the states with the workflow engine's own edge builders and run them from a pre-filled store.

    States named in `plain_nodes` run the given callable; every other state runs a real node.
    """
    plain_nodes = plain_nodes or {}
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    graph = StateGraph(_Schema)
    for state in states:
        graph.add_node(state.id, plain_nodes.get(state.id) or _node(state))
    graph.add_edge(START, entry)
    for state in states:
        executor._process_transition(graph, state, enable_summarization_node=False)
    return graph.compile().invoke(
        {CONTEXT_STORE_VARIABLE: dict(PRIOR_STORE), MESSAGES_VARIABLE: []},
        {"recursion_limit": 25},
    )[CONTEXT_STORE_VARIABLE]


def _sequential(**next_kwargs) -> dict[str, Any]:
    return _run_graph(
        [_state("first", state_id="second", **next_kwargs), _state("second", state_id=END)],
        entry="first",
        plain_nodes={"second": _passthrough_node},
    )


# --- the reducer ---


def test_reducer_does_not_mutate_its_update():
    update = {CONTEXT_STORE_KEEP_NEW_ONLY_FLAG: True, "k": "v"}

    first = add_or_replace_context_store({"old": 1}, update)
    second = add_or_replace_context_store({"old": 1}, update)

    assert update == {CONTEXT_STORE_KEEP_NEW_ONLY_FLAG: True, "k": "v"}
    assert first == second == {"k": "v"}


# --- sequential node, through the conditional edge ---


def test_sequential_full_reset_with_output_key_leaves_only_the_output_key():
    store = _sequential(clear_context_store=True, output_key="out_a")

    assert set(store) == {"out_a"}


def test_sequential_full_reset_without_output_key_clears_the_store():
    assert _sequential(clear_context_store=True) == {}


def test_sequential_output_key_without_reset_keeps_prior_context():
    store = _sequential(output_key="out_a")

    assert set(store) == {*PRIOR_STORE, "new", "out_a"}


def test_sequential_keep_current_drops_prior_context():
    store = _sequential(clear_context_store="keep_current", output_key="out_a", store_in_context=True)

    assert set(store) == {"new", "out_a"}


def test_sequential_keep_current_with_append_starts_a_fresh_list():
    store = _sequential(
        clear_context_store="keep_current", output_key="out_a", store_in_context=True, append_to_context=True
    )

    assert set(store) == {"new", "out_a"}
    assert isinstance(store["out_a"], list) and len(store["out_a"]) == 1


# --- parallel fan-out through next.state_ids ---


def _state_ids_fan_out(branch_a: dict, branch_b: dict) -> dict[str, Any]:
    return _run_graph(
        [
            _state("start", state_ids=["branch_a", "branch_b"]),
            _state("branch_a", state_id=END, **branch_a),
            _state("branch_b", state_id=END, **branch_b),
        ],
        entry="start",
        plain_nodes={"start": _passthrough_node},
    )


def test_state_ids_fan_out_without_resets_merges_both_output_keys():
    """Control: parallel branches that only add their own key keep the prior context and both results."""
    store = _state_ids_fan_out({"output_key": "out_a"}, {"output_key": "out_b"})

    assert set(store) == {*PRIOR_STORE, "new", "out_a", "out_b"}


# --- parallel fan-out through iter_key ---


def _iterator_fan_out(**worker_next: Any) -> dict[str, Any]:
    return _run_graph(
        [
            _state("splitter", state_id="worker", iter_key="items"),
            _state("worker", state_id=END, **worker_next),
        ],
        entry="splitter",
        plain_nodes={"splitter": _splitter_node},
    )


def test_iterator_fan_out_without_resets_accumulates_every_item():
    """Control: appending branches keep the prior context and one entry per item."""
    store = _iterator_fan_out(output_key="result", append_to_context=True)

    assert {k: store[k] for k in PRIOR_STORE} == PRIOR_STORE
    assert len(store["result"]) == len(ITEMS)


def test_iterator_fan_out_with_full_reset_without_output_key_clears_the_store():
    assert _iterator_fan_out(clear_context_store=True) == {}
