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

"""User model override resolution in state processor, summarize and result finalizer nodes."""

from unittest.mock import Mock, patch

import pytest

from codemie.workflows.nodes.result_finalizer_node import ResultFinalizerNode
from codemie.workflows.nodes.state_processor_node import StateProcessorNode
from codemie.workflows.nodes.summarize_conversation_node import (
    SummarizeConversationCommandNode,
    SummarizeConversationNode,
)

SERVICE = "codemie.workflows.nodes.base_node.ModelAvailabilityService"


def _node(cls, is_global=False, user_id="user-1"):
    node = object.__new__(cls)
    node.workflow_config = Mock(project="demo", is_global=is_global, default_model="cfg-model")
    node.kwargs = {"user": Mock(id=user_id)} if user_id else {}
    node.execution_id = "exec"
    node.node_name = "n"
    node.thought_queue = Mock()
    return node


def test_user_override_model_passes_user_project_and_global_flag():
    node = _node(StateProcessorNode, is_global=True)
    with patch(SERVICE) as service:
        service.resolve_user_override_model.return_value = "m"
        assert node.resolve_user_override_model("req") == "m"
    service.resolve_user_override_model.assert_called_once_with(
        requested_model="req", user_id="user-1", project_name="demo", is_global=True
    )


def test_user_override_model_is_none_without_workflow_config():
    node = _node(StateProcessorNode)
    node.workflow_config = None
    assert node.resolve_user_override_model("req") is None


def test_state_processor_uses_user_override_model():
    node = _node(StateProcessorNode)
    with patch(SERVICE) as service:
        service.resolve_user_override_model.return_value = "user-model"
        assert node._resolve_actual_model(Mock(model="gpt-4")) == "user-model"
        service.resolve_model_for_execution.assert_not_called()


def test_state_processor_falls_back_to_project_logic_without_override():
    node = _node(StateProcessorNode)
    with patch(SERVICE) as service:
        service.resolve_user_override_model.return_value = None
        service.resolve_model_for_execution.return_value = ("project-model", True)
        assert node._resolve_actual_model(Mock(model="gpt-4")) == "project-model"


def test_finalizer_uses_user_override_model():
    node = _node(ResultFinalizerNode)
    node.node_name = "result_finalizer"
    node.request_id = "req"
    with (
        patch(SERVICE) as service,
        patch("codemie.workflows.nodes.result_finalizer_node.get_messages_from_state_schema", return_value=[]),
        patch("codemie.workflows.nodes.result_finalizer_node.get_llm_by_credentials") as get_llm,
    ):
        service.resolve_user_override_model.return_value = "user-model"
        get_llm.return_value.invoke.return_value = Mock(content="done")
        node.execute({}, {})
    assert get_llm.call_args.kwargs["llm_model"] == "user-model"
    service.resolve_user_override_model.assert_called_once_with(
        requested_model="cfg-model", user_id="user-1", project_name="demo", is_global=False
    )
    service.resolve_model_for_execution.assert_not_called()


def test_finalizer_without_override_uses_project_resolution():
    node = _node(ResultFinalizerNode)
    node.node_name = "result_finalizer"
    node.request_id = "req"
    with (
        patch(SERVICE) as service,
        patch("codemie.workflows.nodes.result_finalizer_node.get_messages_from_state_schema", return_value=[]),
        patch("codemie.workflows.nodes.result_finalizer_node.get_llm_by_credentials") as get_llm,
    ):
        service.resolve_user_override_model.return_value = None
        service.resolve_model_for_execution.return_value = ("project-model", False)
        get_llm.return_value.invoke.return_value = Mock(content="done")
        node.execute({}, {})
    assert get_llm.call_args.kwargs["llm_model"] == "project-model"


@pytest.mark.parametrize("override_model,expected", [("user-model", "user-model"), (None, "project-model")])
def test_summarize_resolution_prefers_user_override_model(override_model, expected):
    node = _node(SummarizeConversationCommandNode)
    with patch(SERVICE) as service:
        service.resolve_user_override_model.return_value = override_model
        service.resolve_model_for_execution.return_value = ("project-model", False)
        assert node._resolve_model_for_summarization("requested") == expected
    service.resolve_user_override_model.assert_called_once_with(
        requested_model="requested", user_id="user-1", project_name="demo", is_global=False
    )


def _execute_summarize_conversation_node(node):
    with (
        patch("codemie.workflows.nodes.summarize_conversation_node.get_messages_from_state_schema", return_value=[]),
        patch("codemie.workflows.nodes.summarize_conversation_node.get_llm_by_credentials") as get_llm,
    ):
        get_llm.return_value.invoke.return_value = Mock(content="summary")
        node.execute({}, {})
    return get_llm.call_args.kwargs["llm_model"]


def test_summarize_conversation_node_uses_user_override_model_over_project_resolution():
    node = _node(SummarizeConversationNode)
    node.request_id = "req"
    with patch(SERVICE) as service:
        service.resolve_user_override_model.return_value = "user-model"
        llm_model = _execute_summarize_conversation_node(node)

    assert llm_model == "user-model"
    service.resolve_user_override_model.assert_called_once_with(
        requested_model="cfg-model", user_id="user-1", project_name="demo", is_global=False
    )
    service.resolve_model_for_execution.assert_not_called()


def test_summarize_conversation_node_without_override_uses_project_resolution():
    node = _node(SummarizeConversationNode)
    node.request_id = "req"
    with patch(SERVICE) as service:
        service.resolve_user_override_model.return_value = None
        service.resolve_model_for_execution.return_value = ("project-model", False)
        llm_model = _execute_summarize_conversation_node(node)

    assert llm_model == "project-model"
    assert service.resolve_model_for_execution.call_args.kwargs["requested_model"] == "cfg-model"
