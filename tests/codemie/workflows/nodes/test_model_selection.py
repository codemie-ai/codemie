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

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from codemie.workflows.constants import MESSAGES_VARIABLE
from codemie.workflows.nodes.result_finalizer_node import ResultFinalizerNode
from codemie.workflows.nodes.state_processor_node import StateProcessorNode
from codemie.workflows.nodes.summarize_conversation_node import SummarizeConversationNode


@pytest.mark.parametrize("node_class", [ResultFinalizerNode, StateProcessorNode, SummarizeConversationNode])
@pytest.mark.parametrize(
    "requested, is_global, allowed, default, expected, fallback",
    [
        (None, False, ["project-default", "allowed"], "project-default", "project-default", False),
        ("", False, ["project-default"], "project-default", "project-default", False),
        ("allowed", False, ["project-default", "allowed"], "project-default", "allowed", False),
        ("blocked", False, ["project-default"], "project-default", "project-default", True),
        (None, False, None, "project-default", "project-default", False),
        (None, False, None, None, None, False),
        (None, True, ["project-default"], "project-default", None, False),
    ],
)
def test_workflow_node_passes_resolved_model_to_llm(
    node_class, requested, is_global, allowed, default, expected, fallback
):
    module = node_class.__module__
    config = SimpleNamespace(project="project", is_global=is_global, default_model=requested)
    node = node_class(
        callbacks=[],
        workflow_execution_service=MagicMock(workflow_execution_id="execution"),
        thought_queue=MagicMock(),
        workflow_config=config,
        workflow_state=MagicMock(task="Summarize"),
    )
    legacy_model = "gpt-3.5-turbo" if node_class is SummarizeConversationNode else "global-default"
    custom_node = SimpleNamespace(model=requested, config={"output_template": "{{ items }}"})
    with (
        patch(
            "codemie.service.llm.model_availability_service.customer_config",
            is_feature_enabled=MagicMock(return_value=True),
        ),
        patch(
            "codemie.service.llm.model_availability_service.Application.get_by_id",
            return_value=SimpleNamespace(allowed_models=allowed, default_model=default),
        ),
        patch(f"{module}.get_llm_by_credentials") as get_llm,
    ):
        if node_class is not SummarizeConversationNode:
            with patch(f"{module}.llm_service") as llm_service:
                llm_service.default_llm_model = legacy_model
                if node_class is StateProcessorNode:
                    with patch.object(node, "_fetch_states", return_value=[]):
                        node.execute({MESSAGES_VARIABLE: []}, {"custom_node": custom_node})
                else:
                    node.execute({MESSAGES_VARIABLE: []}, {})
        else:
            node.execute({MESSAGES_VARIABLE: [HumanMessage(content="Conversation")]}, {})
        get_llm.assert_called_once_with(request_id="execution", llm_model=expected or legacy_model)


def test_summarize_blocked_model_falls_back_and_completes():
    node = SummarizeConversationNode(
        callbacks=[],
        workflow_execution_service=MagicMock(workflow_execution_id="execution"),
        thought_queue=MagicMock(),
        workflow_config=SimpleNamespace(project="project", is_global=False, default_model="blocked"),
        execution_id="execution",
    )
    with (
        patch(
            "codemie.workflows.nodes.base_node.ModelAvailabilityService.resolve_model_for_execution",
            return_value=("project-default", True),
        ),
        patch("codemie.workflows.nodes.summarize_conversation_node.get_llm_by_credentials") as get_llm,
    ):
        get_llm.return_value.invoke.return_value.content = "Summary"
        assert node.execute({MESSAGES_VARIABLE: [HumanMessage(content="Conversation")]}, {}) == "Summary"
        get_llm.assert_called_once_with(request_id="execution", llm_model="project-default")
