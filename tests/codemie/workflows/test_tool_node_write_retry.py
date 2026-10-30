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

"""Exercises the REAL retry wiring for Exchange write errors with an unknown or not-applied outcome.

``WorkflowRetryPolicy.custom_retry_on`` (``codemie.core.workflow_models.workflow_models``) honours a duck-typed
``RETRYABLE is False`` marker that write errors with a known outcome carry. That marker only reaches the policy
because ``BaseNode.__call__`` re-raises the raw tool error unchanged (see ``_handle_execution_exception``'s bare
``raise``). Unit-testing ``custom_retry_on`` as a bare function proves the predicate is correct but not that the
predicate is actually consulted: if a future change wrapped the tool error before it left ``ToolNode``/``BaseNode``,
or if ``WorkflowConfigBase.get_effective_retry_policy`` stopped wiring ``custom_retry_on`` into the real
``langgraph`` ``RetryPolicy``, every unit test above would stay green while an "unknown outcome" write got
resubmitted up to ``max_attempts`` times in production.

These tests close that gap: a real ``ToolNode`` is added to a real ``langgraph`` ``StateGraph`` with the
``RetryPolicy`` that production code (``WorkflowConfigBase.get_effective_retry_policy``) builds, and the graph is
actually invoked. Only sleep/backoff timing is zeroed for test speed; the retry decision itself is the production
``WorkflowRetryPolicy.custom_retry_on`` callable running inside langgraph's own retry loop.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from langgraph.graph import END, START, StateGraph

from codemie.core.workflow_models import (
    WorkflowConfig,
    WorkflowNextState,
    WorkflowRetryPolicy,
    WorkflowState,
    WorkflowTool,
)
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE, MESSAGES_VARIABLE
from codemie.workflows.nodes.tool_node import ToolNode
from codemie_tools.ms_exchange.errors import PreMutationReadFailedError, UnreachableError, WriteUnknownError


class _CountingTool:
    """A fake toolkit tool whose ``execute`` raises a configured error and counts invocations."""

    args_schema: dict = {}

    def __init__(self, error_cls: type[Exception]):
        self._error_cls = error_cls
        self.call_count = 0

    def execute(self):
        self.call_count += 1
        raise self._error_cls()


def _build_tool_node(
    tool_id: str, retry_policy_cfg: WorkflowRetryPolicy
) -> tuple[ToolNode, WorkflowState, WorkflowConfig]:
    """A ToolNode wired to a real (non-mocked) WorkflowConfig, so get_effective_retry_policy runs for real."""
    tool_config = WorkflowTool(id=tool_id, tool="fake_write_tool")
    state = WorkflowState(
        id="tool_node",
        task="Run tool",
        next=WorkflowNextState(state_id="next"),
        tool_id=tool_id,
        retry_policy=retry_policy_cfg,
    )
    workflow_config = WorkflowConfig(
        id="wf-write-retry",
        name="Write Retry Workflow",
        description="Exercises the real retry wiring for Exchange write errors.",
        tools=[tool_config],
        states=[state],
    )
    node = ToolNode(
        callbacks=[MagicMock()],
        workflow_execution_service=MagicMock(),
        thought_queue=MagicMock(),
        workflow_state=state,
        workflow_config=workflow_config,
        user=MagicMock(),
        execution_id="exec-write-retry",
    )
    return node, state, workflow_config


def _compiled_graph_with_production_retry_policy(node: ToolNode, state: WorkflowState, workflow_config: WorkflowConfig):
    """Build a minimal StateGraph the same way ``WorkflowExecutor.initialize_node`` does in production:
    ``workflow.add_node(state.id, node, retry=workflow_config.get_effective_retry_policy(state=state))``.

    Only the timing fields are zeroed afterwards (no sleep/jitter in the test); ``retry_on`` stays the real
    ``WorkflowRetryPolicy.custom_retry_on`` and ``max_attempts`` stays whatever the policy under test configured.
    """
    retry_policy = workflow_config.get_effective_retry_policy(state=state)
    retry_policy = retry_policy._replace(initial_interval=0.0, max_interval=0.0, jitter=False)

    graph = StateGraph(dict)
    graph.add_node(state.id, node, retry=retry_policy)
    graph.add_edge(START, state.id)
    graph.add_edge(state.id, END)
    return graph.compile(), retry_policy


@contextmanager
def _tool_resolves_to(node: ToolNode, tool: _CountingTool):
    """Make ToolNode._execute_regular_tool resolve to ``tool`` without touching the real toolkit/virtual-assistant
    machinery, while keeping BaseNode.__call__ / ToolNode.execute / the real retry wiring untouched."""
    with (
        patch("codemie.workflows.nodes.tool_node.VirtualAssistantService") as mock_vas,
        patch("codemie.workflows.nodes.tool_node.ToolkitService") as mock_tks,
        patch("codemie.workflows.nodes.tool_node.ToolsService") as mock_ts,
        patch.object(node, "_is_execution_aborted", return_value=False),
    ):
        mock_vas.create_from_tool_config.return_value = MagicMock(id="virtual-assistant-1")
        mock_tks.get_toolkit_methods.return_value = []
        mock_ts.find_tool_from_config.return_value = tool
        yield


@pytest.mark.parametrize(
    "error_cls",
    [WriteUnknownError, PreMutationReadFailedError],
    ids=["unknown-outcome", "not-applied"],
)
def test_non_retryable_write_error_is_attempted_exactly_once_through_real_retry_wiring(error_cls):
    """A write whose outcome is unknown or proven not-applied must never be resubmitted, even though the
    configured policy would otherwise allow many attempts -- going through the real ToolNode -> BaseNode.__call__
    -> langgraph RetryPolicy(retry_on=WorkflowRetryPolicy.custom_retry_on) path, not a bare-function call."""
    tool = _CountingTool(error_cls)
    node, state, workflow_config = _build_tool_node(
        "write_tool",
        WorkflowRetryPolicy(initial_interval=0.0, backoff_factor=1.0, max_interval=0.0, max_attempts=10),
    )
    compiled, retry_policy = _compiled_graph_with_production_retry_policy(node, state, workflow_config)
    assert retry_policy.max_attempts == 10  # the policy really would allow many attempts if retry_on said yes

    with _tool_resolves_to(node, tool):
        with pytest.raises(error_cls):
            compiled.invoke({CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []})

    assert tool.call_count == 1


def test_retryable_error_is_attempted_more_than_once_through_real_retry_wiring():
    """Control case: a plain retryable error (a read error, RETRYABLE defaults True) really is retried by the
    harness -- proving the exactly-once assertions above come from custom_retry_on's marker check, not from a
    retry mechanism that never retries anything."""
    tool = _CountingTool(UnreachableError)
    node, state, workflow_config = _build_tool_node(
        "read_tool",
        WorkflowRetryPolicy(initial_interval=0.0, backoff_factor=1.0, max_interval=0.0, max_attempts=3),
    )
    compiled, retry_policy = _compiled_graph_with_production_retry_policy(node, state, workflow_config)
    assert retry_policy.max_attempts == 3

    with _tool_resolves_to(node, tool):
        with pytest.raises(UnreachableError):
            compiled.invoke({CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []})

    assert tool.call_count == 3
