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

"""Tests for LangGraphAssistantBuilder.create_subagent_executors model policy and failure logging."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

from langgraph.graph.state import CompiledStateGraph

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_engine_builder import LangGraphAssistantBuilder
from codemie.service.llm.model_availability_service import ProjectModelConfig
from codemie.service.subagents.builtin_subagents import BuiltinSubagent

BUILDER = "codemie.service.assistant.assistant_engine_builder"


def _parent_assistant() -> Assistant:
    return Assistant(
        id="parent-id",
        name="Parent",
        description="Parent assistant",
        system_prompt="You are a parent",
        project="proj",
        llm_model_type="assistant-model",
    )


def _project(allowed_models: list[str] | None = None, default_model: str | None = None) -> Mock:
    project = Mock()
    project.id = "proj"
    project.allowed_models = allowed_models
    project.default_model = default_model
    return project


def _user() -> Mock:
    user = Mock(spec=User)
    user.id = "user-id"
    user.name = "User"
    return user


def _create(parent: Assistant) -> tuple[list[CompiledStateGraph[Any, Any, Any, Any]], set[str]]:
    return LangGraphAssistantBuilder.create_subagent_executors(
        assistant=parent,
        user=_user(),
        request=AssistantChatRequest(text="hi", llm_model="gpt-4.1"),
        request_uuid="uuid",
        thread_generator=Mock(),
        llm_model="gpt-4.1",
    )


def test_builtin_subagent_built_with_unconfigured_whitelist():
    agent = Mock()
    with (
        patch(
            f"{BUILDER}.LangGraphAssistantBuilder._merge_skill_builtin_subagents",
            return_value=[BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT],
        ),
        patch("codemie.service.tools.assistant_factory.create_assistant_executors", return_value=[]),
        patch(f"{BUILDER}.Assistant.get_by_ids", return_value=[]),
        patch(f"{BUILDER}.BuiltinSubagentsRegistry.get_system_prompt", return_value="builtin prompt"),
        patch("codemie.service.assistant_service.AssistantService.build_agent", return_value=agent),
        patch("codemie.core.models.Application.get_by_id", return_value=_project(allowed_models=None)),
        patch("codemie.configs.customer_config.CustomerConfig.is_feature_enabled", return_value=True),
        patch(
            "codemie.service.llm.model_availability_service.ModelAvailabilityService.get_project_models",
            return_value=ProjectModelConfig(allowed_models=[], default_model=""),
        ),
    ):
        result = _create(_parent_assistant())

    assert result == ([agent.agent_executor], {"__builtin__general_purpose_subagent"})


def test_builtin_failure_log_is_informative():
    from codemie.core.exceptions import ModelNotWhitelistedException

    error = ModelNotWhitelistedException("x", "proj", details="d")
    with (
        patch(
            f"{BUILDER}.LangGraphAssistantBuilder._merge_skill_builtin_subagents",
            return_value=[BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT],
        ),
        patch("codemie.service.tools.assistant_factory.create_assistant_executors", return_value=[]),
        patch(f"{BUILDER}.Assistant.get_by_ids", return_value=[]),
        patch(f"{BUILDER}.LangGraphAssistantBuilder._build_builtin_subagent_executor", side_effect=error),
        patch(f"{BUILDER}.logger") as mock_logger,
    ):
        result = _create(_parent_assistant())

    assert result == ([], set())
    logged = " ".join(str(c.args[0]) for c in mock_logger.error.call_args_list)
    assert BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT.value in logged
    assert "ModelNotWhitelistedException" in logged
    assert error.message in logged


def test_unbuilt_sub_assistant_is_absent_from_supervisor_descriptions():
    """AC4: a sub-assistant whose build failed gets neither an executor nor a description (handoff tool)."""
    from types import SimpleNamespace

    alpha = SimpleNamespace(name="Alpha", description="alpha desc")
    beta = SimpleNamespace(name="Beta", description="beta desc")
    alpha_executor = SimpleNamespace(name="alpha")  # Beta's build raised, so only Alpha has an executor
    parent = _parent_assistant()
    parent.assistant_ids = ["a", "b"]

    customer_config_mock = Mock()
    customer_config_mock.is_feature_enabled.return_value = True
    customer_config_mock.get_feature_setting.return_value = None
    agent_kwargs: dict[str, Any] = {}
    with (
        patch("codemie.service.tools.assistant_factory.create_assistant_executors", return_value=[alpha_executor]),
        patch(f"{BUILDER}.Assistant.get_by_ids", return_value=[alpha, beta]),
        patch("codemie.service.tool_permissions_service.customer_config", customer_config_mock),
    ):
        LangGraphAssistantBuilder.configure_agent_kwargs(
            agent_kwargs=agent_kwargs,
            assistant=parent,
            user=_user(),
            request=AssistantChatRequest(text="hi", llm_model="gpt-4.1"),
            request_uuid="uuid",
            thread_generator=Mock(),
            llm_model="gpt-4.1",
            smart_tool_selection_enabled=False,
            create_subagent_executors=LangGraphAssistantBuilder.create_subagent_executors,
            get_subagent_descriptions=LangGraphAssistantBuilder.get_subagent_descriptions_with_contributions,
        )

    assert agent_kwargs["subagents"] == [alpha_executor]
    assert agent_kwargs["subagent_descriptions"] == {"Alpha": "alpha desc"}


class _CloneStopped(Exception):
    """Raised at the clone's react check; args[0] is the model the clone's build_agent resolved."""


class _StopAtReactCheck:
    def __contains__(self, item):
        raise _CloneStopped(item)


def test_builtin_clone_runs_on_explicit_resolved_model():
    """The clone runs on the resolved model the parent passes in, not on its copied llm_model_type."""
    builtin = BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT
    with (
        patch(f"{BUILDER}.BuiltinSubagentsRegistry.get_system_prompt", return_value="builtin prompt"),
        patch("codemie.service.assistant_service.set_llm_context"),
        patch("codemie.service.assistant_service.build_unique_file_objects", return_value={}),
        patch(
            "codemie.service.assistant_service.BedrockOrchestratorService.is_bedrock_assistant",
            return_value=False,
        ),
        patch("codemie.service.assistant_service.AssistantService.drop_missing_context", return_value=[]),
        patch("codemie.service.assistant_service.Conversation.find_by_id", return_value=None),
        patch(
            "codemie.service.assistant_service.llm_service.get_react_llms",
            return_value=_StopAtReactCheck(),
        ),
        patch("codemie.configs.customer_config.CustomerConfig.is_feature_enabled", return_value=False),
    ):
        parent = _parent_assistant()
        try:
            LangGraphAssistantBuilder._build_builtin_subagent_executor(
                parent=parent,
                builtin=builtin,
                user=_user(),
                request=AssistantChatRequest(text="hi"),
                request_uuid="uuid",
                thread_generator=Mock(),
                resolved_model="resolved-model",
                agent_name="__builtin__clone",
            )
            outcome = None
        except _CloneStopped as e:
            outcome = e

    assert isinstance(outcome, _CloneStopped)
    assert outcome.args[0] == "resolved-model"
    assert parent.llm_model_type == "assistant-model"
