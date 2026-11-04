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

"""Parent -> child model resolution through the real AssistantService.build_agent (AC3, AC6, AC7, AC8).

The parent build runs to its subagents; each child build stops at the get_react_llms membership check,
which records the model that child resolved.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import Mock, patch

from codemie.core.models import AssistantChatRequest, ToolCallPolicy
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService
from codemie.service.subagents.builtin_subagents import BuiltinSubagent

SERVICE = "codemie.service.assistant_service"


class _ChildStopped(Exception):
    """Raised by the child build at its react check; args[0] is the model the child resolved."""


class _ReactCheckRecorder:
    """Stands in for get_react_llms(): records each resolved model; lets the root through, stops every child."""

    def __init__(self):
        self.seen: list[str | None] = []

    def __contains__(self, item):
        self.seen.append(item)
        if len(self.seen) > 1:
            raise _ChildStopped(item)
        return False


class _RecordingAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.agent_name = kwargs["agent_name"]
        self.llm_model = kwargs["llm_model"]
        self.temperature = kwargs["temperature"]
        self.top_p = kwargs["top_p"]
        self.recursion_limit = None


PROJECTS = {
    "proj": Mock(id="proj", allowed_models=["D", "M"], default_model="D"),
    "child-proj": Mock(id="child-proj", allowed_models=["X"], default_model="X"),
    # Whitelist as the admin API stores it: deployment names for chat models.
    "deployment-proj": Mock(
        id="deployment-proj", allowed_models=["codemie-gpt-4.1-2025-04-14", "D"], default_model="D"
    ),
}


def _assistant(assistant_id: str, own_model: str, project: str = "proj", **kwargs) -> Assistant:
    return Assistant(
        id=assistant_id,
        name=assistant_id,
        description=assistant_id,
        system_prompt="prompt",
        project=project,
        llm_model_type=own_model,
        **kwargs,
    )


def _user() -> Mock:
    user = Mock(spec=User)
    user.id = "user-id"
    user.name = "User"
    return user


def _conversation(llm_model: str) -> Mock:
    return Mock(llm_model=llm_model, enable_image_generation=None, image_generation_model=None, tool_call_policy=None)


def _run(
    parent: Assistant,
    children: list[Assistant],
    request: AssistantChatRequest,
    conversation=None,
    pinned=None,
    set_llm_context: Mock | None = None,
):
    recorder = _ReactCheckRecorder()
    permissions = Mock(tool_call_policy=ToolCallPolicy.AUTO_APPROVE)
    patches = [
        patch(f"{SERVICE}.set_llm_context", set_llm_context or Mock()),
        patch(f"{SERVICE}.build_unique_file_objects", return_value={}),
        patch(f"{SERVICE}.BedrockOrchestratorService.is_bedrock_assistant", return_value=False),
        patch(f"{SERVICE}.AssistantService.drop_missing_context", return_value=[]),
        patch(f"{SERVICE}.Conversation.find_by_id", return_value=conversation),
        patch(
            "codemie.service.llm.model_availability_service.Application.get_by_id",
            side_effect=lambda name: PROJECTS[name],
        ),
        patch("codemie.configs.customer_config.CustomerConfig.is_feature_enabled", return_value=True),
        patch(f"{SERVICE}.llm_service.get_react_llms", return_value=recorder),
        patch(f"{SERVICE}.AssistantService._apply_marketplace_tool_mappings"),
        patch(f"{SERVICE}.SkillContributionsResolver.resolve_for_assistant"),
        patch(f"{SERVICE}.prepare_skill_workspace", return_value=[]),
        patch(f"{SERVICE}.ToolkitService.get_tools", return_value=[]),
        patch("codemie.service.tools.oauth_connect_gate.enforce_oauth_connected"),
        patch(f"{SERVICE}.AssistantService._prepare_system_prompt", return_value=""),
        patch(f"{SERVICE}.config.ENABLE_LANGGRAPH_AITOOLS_AGENT", True),
        patch(
            "codemie.service.tool_permissions_service.ToolPermissionsService.get_effective_permissions",
            return_value=permissions,
        ),
        patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=children),
        patch("codemie.service.tools.assistant_factory._validate_remote_entity_exists_and_cleanup", return_value=None),
        patch(
            "codemie.service.assistant.assistant_version_service.AssistantVersionService.apply_version_to_assistant",
            side_effect=lambda assistant, version: pinned,
        ),
        patch(
            "codemie.service.assistant.assistant_engine_builder.BuiltinSubagentsRegistry.get_system_prompt",
            return_value="builtin prompt",
        ),
        patch(f"{SERVICE}.LangGraphAgent", _RecordingAgent),
        patch(f"{SERVICE}.AIToolsAgent", _RecordingAgent),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        agent = AssistantService.build_agent(assistant=parent, request=request, user=_user(), request_uuid="uuid")
    return agent, recorder.seen


def test_child_with_own_allowed_model_runs_on_it():
    """AC6: whitelist [D, M], default D, child configured with M, user picks X -> the child runs on M."""
    child = _assistant("child", own_model="M")
    parent = _assistant("parent", own_model="D", assistant_ids=["child"])

    agent, seen = _run(parent, [child], AssistantChatRequest(text="hi", llm_model="X"))

    assert agent.llm_model == "D"
    assert seen == ["D", "M"]


def test_child_in_project_allowing_pick_runs_on_pick_while_parent_substitutes():
    """AC7: the child's project allows X -> the child runs on X although the parent substituted."""
    child = _assistant("child", own_model="C", project="child-proj")
    parent = _assistant("parent", own_model="M", assistant_ids=["child"])

    agent, seen = _run(parent, [child], AssistantChatRequest(text="hi", llm_model="X"))

    assert agent.llm_model == "M"
    assert seen == ["M", "X"]


def test_child_gets_conversation_model_when_request_has_none():
    """AC8: no llm_model in the request, the conversation stores M -> M is the pick for root and child."""
    child = _assistant("child", own_model="D")
    parent = _assistant("parent", own_model="D", assistant_ids=["child"])
    request = AssistantChatRequest(text="hi", conversation_id="conv-1")

    agent, seen = _run(parent, [child], request, conversation=_conversation("M"))

    assert seen == ["M", "M"]
    assert request.llm_model is None
    assert "llm_model" not in request.model_fields_set


def test_version_pinned_child_resolves_with_pinned_model():
    """A pinned child version resolves with the pinned llm_model_type, not the stored one."""
    stored = _assistant("child", own_model="Z")
    pinned = _assistant("child", own_model="M")
    parent = _assistant("parent", own_model="D", assistant_ids=["child"])
    request = AssistantChatRequest(text="hi", llm_model="X", sub_assistants_versions={"child": 3})

    _, seen = _run(parent, [stored], request, pinned=pinned)

    assert seen == ["D", "M"]


def test_builtin_clone_runs_on_parent_resolved_model():
    """AC3: a built-in clone runs on the parent's resolved model."""
    parent = _assistant("parent", own_model="M", enabled_builtin_subagents=[BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT])

    agent, seen = _run(parent, [], AssistantChatRequest(text="hi", llm_model="X"))

    assert agent.llm_model == "M"
    assert seen == ["M", "M"]
    assert parent.llm_model_type == "M"


def test_builtin_clone_does_not_report_the_parents_fallback_again():
    """The parent reports X -> M once; the clone runs on M and does not count the same substitution again."""
    parent = _assistant("parent", own_model="M", enabled_builtin_subagents=[BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT])

    with patch("codemie.service.llm.model_availability_service.ProjectModelPolicy._report_fallback") as report:
        _, seen = _run(parent, [], AssistantChatRequest(text="hi", llm_model="X"))

    assert seen == ["M", "M"]
    report.assert_called_once_with("X", "M", "assistant_execution")


def test_assistant_without_pydantic_private_storage_builds():
    """A DB-loaded assistant can have __pydantic_private__ = None; reading the clone marker must not crash."""
    parent = _assistant("parent", own_model="M")
    object.__setattr__(parent, "__pydantic_private__", None)

    agent, seen = _run(parent, [], AssistantChatRequest(text="hi", llm_model="X"))

    assert agent.llm_model == "M"
    assert seen == ["M"]


def test_sub_assistant_reports_its_own_fallback():
    """A sub-assistant resolves under its own project, so its substitution is reported separately."""
    child = _assistant("child", own_model="M")
    parent = _assistant("parent", own_model="D", assistant_ids=["child"])

    with patch("codemie.service.llm.model_availability_service.ProjectModelPolicy._report_fallback") as report:
        _run(parent, [child], AssistantChatRequest(text="hi", llm_model="X"))

    assert [c.args for c in report.call_args_list] == [
        ("X", "D", "assistant_execution"),
        ("X", "M", "assistant_execution"),
    ]


def test_base_name_pick_runs_when_its_deployment_name_is_whitelisted():
    """The user picks the base name the UI lists; the whitelist holds its deployment name -> no substitution."""
    parent = _assistant("parent", own_model="D", project="deployment-proj")
    catalog = [Mock(base_name="gpt-4.1", deployment_name="codemie-gpt-4.1-2025-04-14")]

    with patch("codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info", return_value=catalog):
        agent, seen = _run(parent, [], AssistantChatRequest(text="hi", llm_model="gpt-4.1"))

    assert agent.llm_model == "gpt-4.1"
    assert seen == ["gpt-4.1"]


def test_llm_context_uses_the_resolved_model():
    """The budget context is set for the model the agent runs on, not the assistant's raw model."""
    parent = _assistant("parent", own_model="X")
    set_ctx = Mock()

    agent, _ = _run(parent, [], AssistantChatRequest(text="hi", llm_model="Y"), set_llm_context=set_ctx)

    assert agent.llm_model == "D"
    assert set_ctx.call_count == 1
    assert set_ctx.call_args.args[0] is parent
    assert set_ctx.call_args.args[1] is None
    assert set_ctx.call_args.args[3] == "D"
