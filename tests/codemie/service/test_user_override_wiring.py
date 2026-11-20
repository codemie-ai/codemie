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


"""Regression tests: the user model override receives the user id on every assistant execution path."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.routers.assistant.catalog import _resolve_effective_llm_model
from codemie.service.assistant_service import AssistantService

RESOLVER = "codemie.service.llm.model_availability_service.ModelAvailabilityService.resolve_user_override_model"
POLICY_RESOLVER = "codemie.service.assistant_service.resolve_assistant_model"
SERVICE_MOD = "codemie.service.assistant_service"
EXECUTION_MOD = "codemie.rest_api.routers.assistant.execution"


class _Stop(Exception):
    pass


def _assistant(**kwargs):
    assistant = MagicMock()
    assistant.id = "a1"
    assistant.project = "proj"
    assistant.is_global = False
    assistant.llm_model_type = "gpt-4"
    assistant.tool_permissions = None
    for key, value in kwargs.items():
        setattr(assistant, key, value)
    return assistant


def test_build_agent_override_replaces_project_enforcement_and_receives_user_id():
    assistant = _assistant()
    user = MagicMock(id="user-1")
    request = AssistantChatRequest(text="hi", conversation_id="c1")

    with (
        patch(RESOLVER, return_value="override-model") as resolver,
        patch(POLICY_RESOLVER) as policy_resolver,
        patch(f"{SERVICE_MOD}.BedrockOrchestratorService.is_bedrock_assistant", return_value=False),
        patch(f"{SERVICE_MOD}.build_unique_file_objects", return_value={}),
        patch(f"{SERVICE_MOD}.set_llm_context") as set_context,
        patch.object(AssistantService, "_apply_conversation_runtime_overrides", return_value=None),
        patch.object(AssistantService, "drop_missing_context", return_value=[]),
        patch(f"{SERVICE_MOD}.llm_service") as llm,
    ):
        llm.resolve_disabled_model.side_effect = lambda model, *args, **kwargs: model
        llm.get_react_llms.side_effect = _Stop
        with pytest.raises(_Stop):
            AssistantService.build_agent(assistant, request, user, "uuid")

    resolver.assert_called_once_with("gpt-4", "user-1", "proj", is_global=False, report=True)
    policy_resolver.assert_not_called()
    assert set_context.call_args.args[3] == "override-model"


def test_build_agent_without_override_falls_back_to_project_enforcement():
    assistant = _assistant()
    request = AssistantChatRequest(text="hi", conversation_id="c1")

    with (
        patch(RESOLVER, return_value=None),
        patch(POLICY_RESOLVER, return_value="project-model") as policy_resolver,
        patch(f"{SERVICE_MOD}.BedrockOrchestratorService.is_bedrock_assistant", return_value=False),
        patch(f"{SERVICE_MOD}.build_unique_file_objects", return_value={}),
        patch(f"{SERVICE_MOD}.set_llm_context") as set_context,
        patch.object(AssistantService, "_apply_conversation_runtime_overrides", return_value=None),
        patch.object(AssistantService, "drop_missing_context", return_value=[]),
        patch(f"{SERVICE_MOD}.llm_service") as llm,
    ):
        llm.resolve_disabled_model.side_effect = lambda model, *args, **kwargs: model
        llm.get_react_llms.side_effect = _Stop
        with pytest.raises(_Stop):
            AssistantService.build_agent(assistant, request, MagicMock(id="user-1"), "uuid")

    policy_resolver.assert_called_once()
    assert set_context.call_args.args[3] == "project-model"


def test_workflow_assistant_builder_applies_override_with_user_id():
    assistant = _assistant()
    user = MagicMock(id="user-2")

    with (
        patch.object(AssistantService, "_load_and_configure_workflow_assistant", return_value=assistant),
        patch(RESOLVER, return_value="override-model") as resolver,
        patch(f"{SERVICE_MOD}.logger") as log,
    ):
        log.info.side_effect = _Stop
        with pytest.raises(_Stop):
            AssistantService.build_agent_for_workflow(
                user_input="x",
                user=user,
                request_uuid="uuid",
                workflow_assistant=MagicMock(),
                project_name="proj",
                is_global=False,
            )

    resolver.assert_called_once_with("gpt-4", "user-2", "proj", is_global=False)
    assert assistant.llm_model_type == "override-model"


def _agent(user, llm_model="gpt-4"):
    agent = AIToolsAgent.__new__(AIToolsAgent)
    agent.assistant = _assistant()
    agent.llm_model = llm_model
    agent.user = user
    agent._resolved_llm_model = None
    agent.thread_generator = None  # real __init__ always sets this (default None)
    return agent


def test_agent_resolved_model_prefers_user_override():
    agent = _agent(MagicMock(id="user-3"))
    with (
        patch(RESOLVER, return_value="override-model") as resolver,
        patch("codemie.service.llm.model_availability_service.ProjectModelPolicy.for_project") as project_resolver,
    ):
        assert agent._resolve_llm_model() == "override-model"

    resolver.assert_called_once_with(requested_model="gpt-4", user_id="user-3", project_name="proj", is_global=False)
    project_resolver.assert_not_called()


def test_agent_resolved_model_uses_project_rules_without_override():
    agent = _agent(MagicMock(id="user-3"))
    with (
        patch(RESOLVER, return_value=None),
        patch(
            "codemie.service.llm.model_availability_service.ProjectModelPolicy.for_project",
            return_value=MagicMock(choose=MagicMock(return_value="project-model")),
        ),
    ):
        assert agent._resolve_llm_model() == "project-model"


def test_effective_llm_model_passes_user_id():
    assistant = _assistant()
    with patch(RESOLVER, return_value="override-model") as resolver:
        model, source = _resolve_effective_llm_model(assistant, {}, MagicMock(id="user-5"))

    assert (model, source) == ("override-model", "project_default")
    # Display-only: the detail view does not log or count a substitution.
    resolver.assert_called_once_with("gpt-4", "user-5", "proj", is_global=False, report=False)
