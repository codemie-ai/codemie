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

"""Skill workspace notices travel as an argument from the preparation step to both prompt builders."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import Mock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.core.workflow_models import WorkflowAssistant
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService

HEADER = "**Workspace setup notices (skill files):**"


def _assistant(skill_ids=()):
    assistant = Mock(spec=Assistant)
    assistant.skill_ids = list(skill_ids)
    return assistant


def _user():
    user = Mock(spec=User)
    user.id = "user-123"
    user.name = "Test User"
    user.username = "test@email.com"
    user.full_name = "Test User"
    return user


@pytest.fixture
def system_prompt():
    with patch.object(AssistantService, "get_system_prompt", return_value="Base prompt"):
        yield


class TestChatPrompt:
    def test_notices_are_appended_as_a_block(self, system_prompt):
        prompt = AssistantService._prepare_system_prompt(
            _assistant(), _user(), AssistantChatRequest(text="t", file_names=[]), workspace_notices=["a", "b"]
        )

        assert prompt == f"Base prompt\n\n{HEADER}\n- a\n- b"

    @pytest.mark.parametrize("notices", [None, []])
    def test_no_block_without_notices(self, system_prompt, notices):
        prompt = AssistantService._prepare_system_prompt(
            _assistant(), _user(), AssistantChatRequest(text="t", file_names=[]), workspace_notices=notices
        )

        assert prompt == "Base prompt"


class TestWorkflowPrompt:
    def test_notices_are_appended_as_a_block(self, system_prompt):
        prompt, schema = AssistantService._prepare_workflow_system_prompt(
            WorkflowAssistant(assistant_id="a1"), _assistant(), _user(), None, None, workspace_notices=["a"]
        )

        assert f"{HEADER}\n- a" in prompt
        assert schema is None

    @pytest.mark.parametrize("notices", [None, []])
    def test_no_block_without_notices(self, system_prompt, notices):
        prompt, _ = AssistantService._prepare_workflow_system_prompt(
            WorkflowAssistant(assistant_id="a1"), _assistant(), _user(), None, None, workspace_notices=notices
        )

        assert HEADER not in prompt


def _workflow_assistant_mock():
    assistant = Mock(spec=Assistant)
    assistant.id = "asst-123"
    assistant.name = "Node Assistant"
    assistant.description = "desc"
    assistant.project = "proj-a"
    assistant.is_global = False
    assistant.skill_ids = ["sk1"]
    assistant.llm_model_type = "gpt-4"
    assistant.is_react = False
    assistant.system_prompt = "p"
    assistant.toolkits = []
    assistant.mcp_servers = []
    return assistant


def test_workflow_builder_prepares_before_building_tools_and_passes_the_notices_to_the_prompt():
    parent = Mock()
    prompt_mock = Mock(return_value=("prompt", None))
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                AssistantService, "_load_and_configure_workflow_assistant", return_value=_workflow_assistant_mock()
            )
        )
        stack.enter_context(patch.object(AssistantService, "_apply_marketplace_tool_mappings"))
        stack.enter_context(patch.object(AssistantService, "prepare_tools_config_from_toolkits", return_value=None))
        stack.enter_context(patch.object(AssistantService, "_prepare_workflow_system_prompt", prompt_mock))
        stack.enter_context(patch.object(AssistantService, "_select_agent_class_for_workflow", return_value=Mock()))
        stack.enter_context(patch("codemie.service.assistant_service.set_llm_context"))
        stack.enter_context(patch("codemie.service.assistant_service.set_disable_prompt_cache"))
        stack.enter_context(patch("codemie.service.assistant_service.build_unique_file_objects_list", return_value={}))
        prepare = stack.enter_context(
            patch("codemie.service.assistant_service.prepare_skill_workspace", return_value=["n1"])
        )
        get_tools = stack.enter_context(
            patch("codemie.service.assistant_service.ToolkitService.get_tools", return_value=[])
        )
        parent.attach_mock(prepare, "prepare")
        parent.attach_mock(get_tools, "get_tools")

        AssistantService.build_agent_for_workflow(
            user_input="hello",
            user=_user(),
            request_uuid="req-1",
            workflow_assistant=WorkflowAssistant(assistant_id="asst-123"),
            project_name="proj-a",
            execution_id="exec-1",
        )

    assert [call[0] for call in parent.mock_calls] == ["prepare", "get_tools"]
    assert prompt_mock.call_args.kwargs["workspace_notices"] == ["n1"]
    assert parent.prepare.call_args.args[1].conversation_id == "exec-1"


def test_chat_builder_prepares_before_building_tools_and_passes_the_notices_to_the_prompt():
    parent = Mock()
    prompt_mock = Mock(return_value="prompt")
    assistant = _workflow_assistant_mock()
    assistant.context = []
    assistant.temperature = 0.7
    assistant.top_p = 0.9
    assistant.bedrock = None
    assistant.smart_tool_selection_enabled = False
    assistant.prompt_variables = []
    assistant.type = "regular"
    assistant.llm_model_type = "claude-sonnet-4"
    request = AssistantChatRequest(text="Hello", file_names=[], conversation_id="conv-1")

    with ExitStack() as stack:
        stack.enter_context(patch("codemie.service.assistant_service.Application.get_by_id", return_value=None))
        stack.enter_context(patch("codemie.service.assistant_service.Conversation.find_by_id", return_value=None))
        stack.enter_context(patch("codemie.service.assistant_service.AIToolsAgent", return_value=Mock()))
        stack.enter_context(patch("codemie.service.assistant_service.LangGraphAgent"))
        config = stack.enter_context(patch("codemie.service.assistant_service.config"))
        config.ENABLE_LANGGRAPH_AITOOLS_AGENT = False
        llm = stack.enter_context(patch("codemie.service.assistant_service.llm_service"))
        llm.get_react_llms.return_value = []
        stack.enter_context(patch("codemie.service.assistant_service.set_llm_context"))
        stack.enter_context(patch("codemie.service.assistant_service.build_unique_file_objects", return_value={}))
        stack.enter_context(
            patch("codemie.service.assistant_service.AssistantService.drop_missing_context", return_value=[])
        )
        stack.enter_context(
            patch(
                "codemie.service.assistant_service.BedrockOrchestratorService.is_bedrock_assistant", return_value=False
            )
        )
        stack.enter_context(patch.object(AssistantService, "_prepare_system_prompt", prompt_mock))
        prepare = stack.enter_context(
            patch("codemie.service.assistant_service.prepare_skill_workspace", return_value=["n1"])
        )
        get_tools = stack.enter_context(
            patch("codemie.service.assistant_service.ToolkitService.get_tools", return_value=[])
        )
        parent.attach_mock(prepare, "prepare")
        parent.attach_mock(get_tools, "get_tools")

        AssistantService.build_agent(assistant=assistant, request=request, user=_user(), request_uuid="req-1")

    assert [call[0] for call in parent.mock_calls] == ["prepare", "get_tools"]
    assert prompt_mock.call_args.args[-1] == ["n1"]
    assert prepare.call_args.args[1].conversation_id == "conv-1"
