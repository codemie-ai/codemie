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

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_engine_builder import (
    LangGraphAssistantBuilder,
    SubagentBuildContext,
    SubagentHandle,
)
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


def _create(parent: Assistant) -> list[SubagentHandle]:
    return LangGraphAssistantBuilder.create_subagent_executors(
        assistant=parent,
        user=_user(),
        request=AssistantChatRequest(text="hi", llm_model="gpt-4.1"),
        request_uuid="uuid",
        thread_generator=Mock(),
        llm_model="gpt-4.1",
        build_context=SubagentBuildContext(),
    )


def test_builtin_subagent_built_with_unconfigured_whitelist():
    handle = Mock()
    with (
        patch(
            f"{BUILDER}.LangGraphAssistantBuilder._merge_skill_builtin_subagents",
            return_value=[BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT],
        ),
        patch("codemie.service.tools.assistant_factory.create_assistant_executors", return_value=[]),
        patch(f"{BUILDER}.Assistant.get_by_ids", return_value=[]),
        patch(f"{BUILDER}.BuiltinSubagentsRegistry.get_system_prompt", return_value="builtin prompt"),
        patch("codemie.service.assistant_service.AssistantService.build_agent_handle", return_value=handle),
        patch("codemie.core.models.Application.get_by_id", return_value=_project(allowed_models=None)),
        patch("codemie.configs.customer_config.CustomerConfig.is_feature_enabled", return_value=True),
    ):
        result = _create(_parent_assistant())

    assert result == [handle]


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

    assert result == []
    logged = " ".join(str(c.args[0]) for c in mock_logger.error.call_args_list)
    assert BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT.value in logged
    assert "ModelNotWhitelistedException" in logged
    assert error.message in logged


def test_fatal_custom_failure_logs_already_built_builtin_handle():
    """CR-002 regression: a fatal custom failure must not silently drop an already-built builtin handle.

    Custom and builtin jobs share one pool, so the builtin job can finish before the fatal custom
    outcome is detected. The already-built handle must be surfaced in a log, not discarded silently.
    """
    from codemie.service.mcp.models import MCPToolLoadException

    builtin_done = threading.Event()
    custom_assistant = SimpleNamespace(name="custom", id="c-id", description="d")
    builtin_handle = SubagentHandle(agent=Mock(), executor=Mock(), tree_requires_confirmation=False, name="b")

    def build_custom():
        assert builtin_done.wait(timeout=2), "builtin job never finished"
        raise MCPToolLoadException("srv", ValueError("boom"))

    def build_builtin(**_kwargs):
        builtin_done.set()
        return builtin_handle

    with (
        patch(
            f"{BUILDER}.LangGraphAssistantBuilder._merge_skill_builtin_subagents",
            return_value=[BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT],
        ),
        patch("codemie.service.tools.assistant_factory.prepare_assistants", return_value=[custom_assistant]),
        patch("codemie.service.tools.assistant_factory.AssistantFactory", return_value=Mock(build=build_custom)),
        patch(f"{BUILDER}.LangGraphAssistantBuilder._build_builtin_subagent_executor", side_effect=build_builtin),
        patch(f"{BUILDER}.logger") as mock_logger,
        pytest.raises(MCPToolLoadException),
    ):
        _create(_parent_assistant())

    logged = " ".join(str(c.args[0]) for c in mock_logger.warning.call_args_list)
    assert "1" in logged
    assert "builtin" in logged.lower()


def test_unbuilt_sub_assistant_is_absent_from_supervisor_descriptions():
    """AC4: a sub-assistant whose build failed gets neither an executor nor a description (handoff tool)."""
    from types import SimpleNamespace

    alpha = SimpleNamespace(name="Alpha", description="alpha desc", id="a")
    beta = SimpleNamespace(name="Beta", description="beta desc", id="b")
    alpha_executor = SimpleNamespace(name="alpha")  # Beta's build raised, so only Alpha has an executor
    alpha_handle = SubagentHandle(
        agent=Mock(), executor=alpha_executor, tree_requires_confirmation=False, name="Alpha", description="alpha desc"
    )
    parent = _parent_assistant()
    parent.assistant_ids = ["a", "b"]

    def factory_side_effect(assistant, **_kwargs):
        if assistant is alpha:
            return Mock(build=lambda: alpha_handle)

        def _raise():
            raise ValueError("beta build failed")

        return Mock(build=_raise)

    customer_config_mock = Mock()
    customer_config_mock.is_feature_enabled.return_value = True
    customer_config_mock.get_feature_setting.return_value = None
    agent_kwargs: dict[str, Any] = {}
    with (
        patch("codemie.service.tools.assistant_factory.prepare_assistants", return_value=[alpha, beta]),
        patch("codemie.service.tools.assistant_factory.AssistantFactory", side_effect=factory_side_effect),
        patch("codemie.service.tool_permissions_service.customer_config", customer_config_mock),
    ):
        request = AssistantChatRequest(text="hi", llm_model="gpt-4.1")
        LangGraphAssistantBuilder.configure_agent_kwargs(
            agent_kwargs=agent_kwargs,
            assistant=parent,
            user=_user(),
            request=request,
            request_uuid="uuid",
            thread_generator=Mock(),
            llm_model="gpt-4.1",
            smart_tool_selection_enabled=False,
            resolved_policy=LangGraphAssistantBuilder.resolve_tool_policy(
                parent, request, allow_tool_confirmation=True
            ),
            build_context=SubagentBuildContext(),
            is_root=True,
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
                build_context=SubagentBuildContext(),
            )
            outcome = None
        except _CloneStopped as e:
            outcome = e

    assert isinstance(outcome, _CloneStopped)
    assert outcome.args[0] == "resolved-model"
    assert parent.llm_model_type == "assistant-model"


def _admin_free_user() -> User:
    return User(id="user-id", username="user", name="User", project_names=["proj"])


class _StopAfterPrompt(Exception):
    """Raised by the patched system-prompt step, after file objects and skill seeding have run."""


def test_concurrent_children_get_attachments_and_one_skill_seed_without_touching_the_parent_request():
    """Real child builds in the pool: every child sees the attachment, the skill is seeded once, the parent is intact."""
    from codemie_tools.base.file_object import FileObject
    from codemie.repository.agent_workspace_repository import SeedOutcome
    from codemie.rest_api.models.skill import Skill
    from codemie.service.tools.assistant_factory import AssistantFactory, run_builds

    children = 3
    overlap = threading.Barrier(children, timeout=5)
    claim_lock = threading.Lock()
    claimed: set[str] = set()
    outcomes_by_skill: list[SeedOutcome] = []
    seen: list[dict[str, Any]] = []

    class Store:
        def seed_references(self, workspace_id, source_kind, source_id, folder, files):
            with claim_lock:
                outcome = SeedOutcome.ALREADY_SEEDED if folder in claimed else SeedOutcome.SEEDED
                claimed.add(folder)
                outcomes_by_skill.append(outcome)
            return outcome

    class Workspaces:
        def create_workspace(self, *_a, **_k):
            overlap.wait()  # all children are inside the seeding phase at the same time
            return SimpleNamespace(id="ws-1")

    skill = Skill(
        id="sk1",
        name="foo",
        description="d",
        content="c" * 200,
        project="p",
        companion_files=[
            {
                "path": "a.py",
                "mime_type": "text/plain",
                "encoding": "text",
                "size_bytes": 3,
                "storage_key": "ab12/a.py",
                "checksum": "ff",
            }
        ],
    )

    def capture_prompt(_assistant, _user, request, _gen, workspace_notices, **_kw):
        seen.append({"request": request, "notices": workspace_notices})
        raise _StopAfterPrompt

    parent_request = AssistantChatRequest(text="hi", llm_model="gpt-4.1", conversation_id="conv-1", file_names=["f1"])
    children_assistants = [
        Assistant(
            id=f"c{i}",
            name=f"child{i}",
            description="d",
            system_prompt="p",
            project="proj",
            llm_model_type="assistant-model",
            skill_ids=["sk1"],
        )
        for i in range(children)
    ]
    jobs = [
        (
            a.name,
            "custom",
            AssistantFactory(a, _admin_free_user(), parent_request, "uuid", Mock(), SubagentBuildContext()).build,
        )
        for a in children_assistants
    ]
    prep = "codemie.service.skill_workspace_preparation"
    svc = "codemie.service.assistant_service"

    with (
        patch(f"{prep}._gate_is_open", return_value=True),
        patch(f"{prep}.AgentWorkspaceService", return_value=Workspaces()),
        patch(f"{prep}.AgentWorkspaceRepository", return_value=Store()),
        patch(f"{prep}.SkillService.get_skills_by_ids", return_value=[skill]),
        patch(f"{svc}.set_llm_context"),
        patch(f"{svc}.BedrockOrchestratorService.is_bedrock_assistant", return_value=False),
        patch(f"{svc}.AssistantService.drop_missing_context", return_value=[]),
        patch(f"{svc}.Conversation.find_by_id", return_value=None),
        patch(f"{svc}.llm_service.get_react_llms", return_value=[]),
        patch(f"{svc}.SkillContributionsResolver.resolve_for_assistant", return_value=None),
        patch(f"{svc}.AssistantService._apply_marketplace_tool_mappings"),
        patch(
            "codemie.service.file_service.file_service.FileService.get_file_object",
            side_effect=lambda name: FileObject(name=name, mime_type="text/plain", owner="u", content=b"x"),
        ),
        patch(f"{svc}.AssistantService._prepare_system_prompt", side_effect=capture_prompt),
        patch("codemie.configs.customer_config.CustomerConfig.is_feature_enabled", return_value=False),
    ):
        results = run_builds(jobs)

    assert [type(err) for _, err in results] == [_StopAfterPrompt] * children
    assert len(seen) == children
    assert all(s["notices"] == [] for s in seen)
    assert all(s["request"].file_names == ["f1"] for s in seen)
    assert len({id(s["request"]) for s in seen}) == children and all(s["request"] is not parent_request for s in seen)
    assert sorted(o.value for o in outcomes_by_skill) == sorted(
        [SeedOutcome.SEEDED.value] + [SeedOutcome.ALREADY_SEEDED.value] * (children - 1)
    )
    assert parent_request.file_names == ["f1"]
