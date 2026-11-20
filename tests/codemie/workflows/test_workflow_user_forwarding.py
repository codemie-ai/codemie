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

"""The executor must forward its user to nodes that resolve the user's model override."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from codemie.core.workflow_models import WorkflowConfig
from codemie.workflows.constants import RESULT_FINALIZER_NODE, SUMMARIZE_MEMORY_NODE
from codemie.workflows.nodes.state_processor_node import StateProcessorNode
from codemie.workflows.workflow import WorkflowExecutor

WF = "codemie.workflows.workflow"


@pytest.fixture
def user():
    return Mock(id="user_1", username="u")


@pytest.fixture
def executor(user):
    config = Mock(spec=WorkflowConfig)
    config.id = "wf"
    config.name = "wf"
    config.project = "proj"
    config.states = []
    config.assistants = []
    config.tools = []
    config.custom_nodes = []
    config.enable_summarization_node = True
    config.is_global = False
    with patch(f"{WF}.WorkflowExecutionService"):
        return WorkflowExecutor(
            workflow_config=config, user_input="x", user=user, thought_queue=Mock(), execution_id="exec"
        )


def test_initialize_nodes_forwards_user_to_summarize_and_finalizer(executor, user):
    with (
        patch(f"{WF}.SummarizeConversationCommandNode") as summarize,
        patch(f"{WF}.ResultFinalizerNode") as finalizer,
    ):
        executor.initialize_nodes(MagicMock(), executor.workflow_config)

    assert summarize.call_args.kwargs["user"] is user
    assert finalizer.call_args.kwargs["user"] is user


def test_inject_user_context_forwards_user_to_summarize_and_finalizer(executor, user):
    graph = MagicMock()
    graph._node_slots = {SUMMARIZE_MEMORY_NODE: MagicMock(), RESULT_FINALIZER_NODE: MagicMock()}
    with (
        patch(f"{WF}.SummarizeConversationCommandNode") as summarize,
        patch(f"{WF}.ResultFinalizerNode") as finalizer,
    ):
        executor._inject_user_context(graph)

    assert summarize.call_args.kwargs["user"] is user
    assert finalizer.call_args.kwargs["user"] is user


def test_init_custom_node_forwards_user_to_state_processor(executor, user):
    state = Mock(id="s1", custom_node_id="state_processor_node")
    custom = Mock(custom_node_id="state_processor_node")
    with (
        patch(f"{WF}.find_custom_node_by_id", return_value=custom),
        patch.object(StateProcessorNode, "__init__", return_value=None) as init,
    ):
        node = executor.init_custom_node(state)

    assert isinstance(node, StateProcessorNode)
    assert init.call_args.kwargs["user"] is user
