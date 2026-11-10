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

"""A full context reset (clear_context_store=True) combined with output_key keeps only the output key."""

from unittest.mock import Mock

import pytest

from codemie.core.workflow_models import WorkflowNextState, WorkflowState
from codemie.workflows.constants import (
    CONTEXT_STORE_KEEP_NEW_ONLY_FLAG,
    CONTEXT_STORE_VARIABLE,
    MESSAGES_VARIABLE,
)
from codemie.workflows.models import CONTEXT_STORE_APPEND_MARKER, add_or_replace_context_store
from codemie.workflows.nodes.base_node import BaseNode

OUTPUT_KEY = "k"
PRIOR_STORE = {"old1": "v1"}


class MockNode(BaseNode):
    """Mock implementation of BaseNode for testing."""

    def execute(self, state_schema, execution_context):
        return self.mock_execute_result

    def get_task(self, state_schema, *args, **kwargs):
        return "Test Task"


@pytest.fixture
def mock_workflow_execution_service():
    service = Mock()
    service.start_state = Mock(return_value="state_123")
    service.finish_state = Mock()
    return service


@pytest.fixture
def mock_thought_queue():
    return Mock()


@pytest.fixture
def mock_callbacks():
    callback = Mock()
    callback.on_node_start = Mock()
    callback.on_node_end = Mock()
    return [callback]


@pytest.fixture
def run_node(mock_workflow_execution_service, mock_thought_queue, mock_callbacks):
    def _run(**next_kwargs):
        workflow_state = WorkflowState(
            id="test_node",
            task="Test",
            assistant_id="assistant_1",
            next=WorkflowNextState(state_id="next", **next_kwargs),
        )
        node = MockNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=workflow_state,
        )
        node.mock_execute_result = {"new": "data"}
        state_schema = {CONTEXT_STORE_VARIABLE: dict(PRIOR_STORE), MESSAGES_VARIABLE: []}
        return node(state_schema)

    return _run


def test_full_reset_with_output_key_keeps_only_output_key(run_node):
    result = run_node(clear_context_store=True, output_key=OUTPUT_KEY)

    output_value = result[OUTPUT_KEY]
    reduced = add_or_replace_context_store(dict(PRIOR_STORE), result[CONTEXT_STORE_VARIABLE])

    assert set(reduced) == {OUTPUT_KEY}
    assert reduced[OUTPUT_KEY] == output_value


def test_full_reset_with_output_key_and_append_stores_single_item_list(run_node):
    expected_value = run_node(clear_context_store=True, output_key=OUTPUT_KEY, append_to_context=False)[OUTPUT_KEY]

    result = run_node(clear_context_store=True, output_key=OUTPUT_KEY, append_to_context=True)
    reduced = add_or_replace_context_store(dict(PRIOR_STORE), result[CONTEXT_STORE_VARIABLE])

    assert reduced == {OUTPUT_KEY: [expected_value]}


def test_full_reset_wins_over_store_in_context_and_reset_keys(run_node):
    result = run_node(
        clear_context_store=True,
        output_key=OUTPUT_KEY,
        store_in_context=True,
        reset_keys_in_context_store=["old1"],
    )

    reduced = add_or_replace_context_store(dict(PRIOR_STORE), result[CONTEXT_STORE_VARIABLE])

    assert set(reduced) == {OUTPUT_KEY}


def test_full_reset_without_output_key_clears_store(run_node):
    result = run_node(clear_context_store=True)

    assert result[CONTEXT_STORE_VARIABLE] is None
    assert add_or_replace_context_store(dict(PRIOR_STORE), result[CONTEXT_STORE_VARIABLE]) == {}


def test_keep_current_with_output_key_keeps_parsed_keys_and_output_key(run_node):
    result = run_node(clear_context_store="keep_current", output_key=OUTPUT_KEY, store_in_context=True)

    reduced = add_or_replace_context_store(dict(PRIOR_STORE), result[CONTEXT_STORE_VARIABLE])

    assert set(reduced) == {"new", OUTPUT_KEY}


def test_keep_current_with_output_key_and_append_stores_single_item_list(run_node):
    """The reducer unwraps append markers on every keep-new-only path, not only on a full reset."""
    expected_value = run_node(clear_context_store="keep_current", output_key=OUTPUT_KEY)[OUTPUT_KEY]

    result = run_node(clear_context_store="keep_current", output_key=OUTPUT_KEY, append_to_context=True)
    reduced = add_or_replace_context_store(dict(PRIOR_STORE), result[CONTEXT_STORE_VARIABLE])

    assert reduced[OUTPUT_KEY] == [expected_value]


def test_keep_new_only_reducer_starts_a_fresh_list_for_append_markers():
    right = {
        CONTEXT_STORE_KEEP_NEW_ONLY_FLAG: True,
        "out": {CONTEXT_STORE_APPEND_MARKER: ["a"]},
        "plain": "x",
    }

    reduced = add_or_replace_context_store({"out": ["old"], "old1": "v1"}, right)

    assert reduced == {"out": ["a"], "plain": "x"}
