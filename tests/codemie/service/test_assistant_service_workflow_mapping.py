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
Stage 1.5 tests: an assistant node inside a workflow reuses the per-assistant integration
mapping the user configured in the assistant view.

build_agent_for_workflow must merge the saved mapping into request.tools_config (via the shared
_apply_marketplace_tool_mappings) only for nodes backed by a real standalone assistant
(workflow_assistant.assistant_id set). Inline/virtual nodes (no assistant_id) must be skipped.
"""

from unittest.mock import Mock, patch

from codemie.core.workflow_models import WorkflowAssistant
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService


def _build_agent_for_workflow(workflow_assistant, mock_assistant, get_tools_spy=None):
    """Invoke build_agent_for_workflow with all heavy collaborators mocked at the seams.

    Returns the _apply_marketplace_tool_mappings spy so tests can assert whether the
    per-assistant mapping merge ran for the given node. ``get_tools_spy`` (a Mock) replaces the
    patched ``ToolkitService.get_tools`` so a test can inspect how it was called.
    """
    get_tools_mock = get_tools_spy if get_tools_spy is not None else Mock(return_value=[])
    user = Mock(spec=User)
    user.id = "user-123"
    user.name = "Test User"

    agent_class = Mock(name="AgentClass")

    with (
        patch.object(AssistantService, "_load_and_configure_workflow_assistant", return_value=mock_assistant),
        patch.object(AssistantService, "_apply_marketplace_tool_mappings") as mock_apply,
        patch.object(AssistantService, "prepare_tools_config_from_toolkits", return_value=None),
        patch.object(AssistantService, "_prepare_workflow_system_prompt", return_value=("prompt", None)),
        patch.object(AssistantService, "_select_agent_class_for_workflow", return_value=agent_class),
        patch("codemie.service.assistant_service.set_llm_context"),
        patch("codemie.service.assistant_service.set_disable_prompt_cache"),
        patch("codemie.service.assistant_service.build_unique_file_objects_list", return_value={}),
        patch("codemie.service.assistant_service.ToolkitService.get_tools", get_tools_mock),
    ):
        AssistantService.build_agent_for_workflow(
            user_input="hello",
            user=user,
            request_uuid="req-1",
            workflow_assistant=workflow_assistant,
            project_name="proj-a",
            execution_id="exec-1",
        )

    return mock_apply


def _mock_assistant():
    assistant = Mock(spec=Assistant)
    assistant.id = "asst-123"
    assistant.name = "Node Assistant"
    assistant.description = "desc"
    assistant.project = "proj-a"
    assistant.is_global = False
    assistant.shared = True
    assistant.mcp_servers = []
    assistant.llm_model_type = "claude-sonnet-4"
    assistant.is_react = False
    assistant.temperature = 0.5
    assistant.context = None
    return assistant


class TestWorkflowAppliesPerAssistantMapping:
    """Stage 1.5 gate: merge per-assistant mapping only for real standalone-assistant nodes."""

    def test_applies_mapping_for_node_with_real_assistant_id(self):
        """A node backed by a real assistant reuses the saved per-assistant mapping."""
        workflow_assistant = WorkflowAssistant(assistant_id="asst-123")
        assistant = _mock_assistant()

        mock_apply = _build_agent_for_workflow(workflow_assistant, assistant)

        mock_apply.assert_called_once()
        # Called with the loaded standalone assistant and the executing user.
        called_assistant = mock_apply.call_args.args[0]
        assert called_assistant is assistant

    def test_skips_mapping_for_inline_virtual_node(self):
        """A virtual/inline node (no assistant_id) has no per-assistant record; merge is skipped."""
        workflow_assistant = WorkflowAssistant(assistant_id=None)
        assistant = _mock_assistant()

        mock_apply = _build_agent_for_workflow(workflow_assistant, assistant)

        mock_apply.assert_not_called()


class TestWorkflowScopeForScriptToolCalls:
    """A workflow assistant node (stored or inline) runs scripts with its own assistant's tools, never workflow scope."""

    def test_stored_assistant_node_gets_no_workflow_project(self):
        get_tools_spy = Mock(return_value=[])

        _build_agent_for_workflow(WorkflowAssistant(assistant_id="asst-123"), _mock_assistant(), get_tools_spy)

        get_tools_spy.assert_called_once()
        assert "workflow_project" not in get_tools_spy.call_args.kwargs

    def test_inline_node_gets_no_workflow_project(self):
        get_tools_spy = Mock(return_value=[])

        _build_agent_for_workflow(WorkflowAssistant(assistant_id=None), _mock_assistant(), get_tools_spy)

        assert "workflow_project" not in get_tools_spy.call_args.kwargs
