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

from unittest.mock import MagicMock

from codemie.agents.assistant_agent import TaskResult
from codemie.core.workflow_models import WorkflowExecutionStatusEnum
from codemie.workflows.nodes.base_node import BaseNode


class _ConcreteNode(BaseNode):
    """Minimal concrete subclass so BaseNode's ABC check does not block instantiation.

    `execute`/`get_task` are abstract on BaseNode; the tests below replace `execute`
    with a MagicMock per-instance, so these bodies are never actually invoked.
    """

    def execute(self, state_schema, execution_context):
        raise NotImplementedError

    def get_task(self, state_schema, *args, **kwargs):
        raise NotImplementedError


def _make_node():
    node = _ConcreteNode.__new__(_ConcreteNode)
    node.workflow_execution_service = MagicMock()
    node.callbacks = []
    node.args = {}
    node.kwargs = {}
    node.node_name = "test_node"
    return node


def test_run_success_path_forwards_truncation_signal_from_task_result():
    node = _make_node()
    task_result = TaskResult(result="cut off output", success=True, truncated=True)
    node.execute = MagicMock(return_value=task_result)
    node.post_process_output = MagicMock(return_value="cut off output")
    node._is_execution_aborted = MagicMock(return_value=False)
    node.finalize_and_update_state = MagicMock(return_value={})

    node._run_success_path(
        state_schema=MagicMock(),
        execution_context={},
        execution_state_id="exec-state-1",
        task="do something",
        current_node_name="agent_node",
    )

    node.workflow_execution_service.finish_state.assert_called_once_with(
        execution_state_id="exec-state-1",
        output="cut off output",
        status=WorkflowExecutionStatusEnum.SUCCEEDED,
        truncated=True,
    )


def test_run_success_path_defaults_signal_for_non_task_result_output():
    node = _make_node()
    node.execute = MagicMock(return_value={"some": "dict-shaped output"})
    node.post_process_output = MagicMock(return_value="processed output")
    node._is_execution_aborted = MagicMock(return_value=False)
    node.finalize_and_update_state = MagicMock(return_value={})

    node._run_success_path(
        state_schema=MagicMock(),
        execution_context={},
        execution_state_id="exec-state-2",
        task="do something",
        current_node_name="other_node",
    )

    node.workflow_execution_service.finish_state.assert_called_once_with(
        execution_state_id="exec-state-2",
        output="processed output",
        status=WorkflowExecutionStatusEnum.SUCCEEDED,
        truncated=False,
    )


def test_run_success_path_forwards_truncation_signal_to_on_node_end_callback():
    node = _make_node()
    callback = MagicMock()
    node.callbacks = [callback]
    task_result = TaskResult(result="cut off output", success=True, truncated=True)
    node.execute = MagicMock(return_value=task_result)
    node.post_process_output = MagicMock(return_value="cut off output")
    node._is_execution_aborted = MagicMock(return_value=False)
    node.finalize_and_update_state = MagicMock(return_value={})

    node._run_success_path(
        state_schema=MagicMock(),
        execution_context={},
        execution_state_id="exec-state-3",
        task="do something",
        current_node_name="agent_node",
    )

    callback.on_node_end.assert_called_once_with(
        output="cut off output",
        execution_state_id="exec-state-3",
        execution_context={},
        truncated=True,
    )


def test_node_attribution_does_not_leak_to_the_next_node():
    """EPMCDME-16625: LangGraph runs a lone task inline, so a node's own billing context must be undone."""
    from codemie.core.dependecies import get_current_project, set_litellm_context
    from codemie.rest_api.models.settings import LiteLLMContext

    node = _make_node()

    def build_node_context(_state):
        set_litellm_context(LiteLLMContext(credentials=None, current_project="node-assistant-project"))
        return {}

    node.generate_execution_context = MagicMock(side_effect=build_node_context)
    node.before_execution = MagicMock(return_value="redirect")
    node.get_node_name = MagicMock(return_value="agent_node")
    set_litellm_context(LiteLLMContext(credentials=None, current_project="workflow-project"))

    assert node(MagicMock()) == "redirect"
    assert get_current_project() == "workflow-project"
