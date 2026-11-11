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

from unittest.mock import MagicMock, patch

import pytest


from codemie.core.models import ToolCallPolicy
from codemie.rest_api.models.assistant import ToolPermissionsConfig
from codemie.service.assistant.assistant_engine_builder import (
    LangGraphAssistantBuilder,
    SubagentBuildContext,
)


def _run_configure(*, request_policy, assistant_policy):
    agent_kwargs = {}
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(tool_call_policy=assistant_policy)
    request = MagicMock()
    request.tool_call_policy = request_policy

    customer_config_mock = MagicMock()
    customer_config_mock.is_feature_enabled.return_value = True
    customer_config_mock.get_feature_setting.return_value = None
    with (
        patch("codemie.service.tool_permissions_service.customer_config", customer_config_mock),
    ):
        LangGraphAssistantBuilder.configure_agent_kwargs(
            agent_kwargs=agent_kwargs,
            assistant=assistant,
            user=MagicMock(),
            request=request,
            request_uuid="uuid-1",
            thread_generator=MagicMock(),
            llm_model="gpt-4",
            smart_tool_selection_enabled=False,
            resolved_policy=LangGraphAssistantBuilder.resolve_tool_policy(
                assistant, request, allow_tool_confirmation=True
            ),
            build_context=SubagentBuildContext(),
            is_root=True,
            create_subagent_executors=lambda **_: [],
            get_subagent_descriptions=lambda a, u: {},
        )
    return agent_kwargs["require_tool_confirmation"]


def test_request_ask_for_approval_activates_confirmation():
    assert (
        _run_configure(
            request_policy=ToolCallPolicy.ASK_FOR_APPROVAL,
            assistant_policy=ToolCallPolicy.AUTO_APPROVE,
        )
        is True
    )


def test_assistant_ask_for_approval_wins_when_no_request_override():
    assert (
        _run_configure(
            request_policy=None,
            assistant_policy=ToolCallPolicy.ASK_FOR_APPROVAL,
        )
        is True
    )


def test_auto_approve_leaves_confirmation_off():
    assert (
        _run_configure(
            request_policy=ToolCallPolicy.AUTO_APPROVE,
            assistant_policy=ToolCallPolicy.AUTO_APPROVE,
        )
        is False
    )


def test_no_request_policy_auto_approve_assistant_leaves_confirmation_off():
    assert (
        _run_configure(
            request_policy=None,
            assistant_policy=ToolCallPolicy.AUTO_APPROVE,
        )
        is False
    )


def _run_configure_full(*, request_policy, assistant_policy):
    """Return the full agent_kwargs dict (not just require_tool_confirmation)."""
    agent_kwargs = {}
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(tool_call_policy=assistant_policy)
    request = MagicMock()
    request.tool_call_policy = request_policy

    customer_config_mock = MagicMock()
    customer_config_mock.is_feature_enabled.return_value = True
    customer_config_mock.get_feature_setting.return_value = None
    with (
        patch("codemie.service.tool_permissions_service.customer_config", customer_config_mock),
    ):
        LangGraphAssistantBuilder.configure_agent_kwargs(
            agent_kwargs=agent_kwargs,
            assistant=assistant,
            user=MagicMock(),
            request=request,
            request_uuid="uuid-1",
            thread_generator=MagicMock(),
            llm_model="gpt-4",
            smart_tool_selection_enabled=False,
            resolved_policy=LangGraphAssistantBuilder.resolve_tool_policy(
                assistant, request, allow_tool_confirmation=True
            ),
            build_context=SubagentBuildContext(),
            is_root=True,
            create_subagent_executors=lambda **_: [],
            get_subagent_descriptions=lambda a, u: {},
        )
    return agent_kwargs


def test_tool_call_policy_propagated_for_ask_for_approval():
    kwargs = _run_configure_full(
        request_policy=ToolCallPolicy.ASK_FOR_APPROVAL,
        assistant_policy=ToolCallPolicy.AUTO_APPROVE,
    )
    assert kwargs["tool_call_policy"] == ToolCallPolicy.ASK_FOR_APPROVAL


def test_tool_call_policy_propagated_for_approve_for_me():
    kwargs = _run_configure_full(
        request_policy=ToolCallPolicy.APPROVE_FOR_ME,
        assistant_policy=ToolCallPolicy.AUTO_APPROVE,
    )
    assert kwargs["tool_call_policy"] == ToolCallPolicy.APPROVE_FOR_ME


def test_tool_call_policy_propagated_for_auto_approve():
    kwargs = _run_configure_full(
        request_policy=ToolCallPolicy.AUTO_APPROVE,
        assistant_policy=ToolCallPolicy.AUTO_APPROVE,
    )
    assert kwargs["tool_call_policy"] == ToolCallPolicy.AUTO_APPROVE


def test_tool_call_policy_uses_assistant_policy_when_no_request_override():
    kwargs = _run_configure_full(
        request_policy=None,
        assistant_policy=ToolCallPolicy.APPROVE_FOR_ME,
    )
    assert kwargs["tool_call_policy"] == ToolCallPolicy.APPROVE_FOR_ME


def _configure_with(*, resolved, is_root, children):
    agent_kwargs = {}
    tree = LangGraphAssistantBuilder.configure_agent_kwargs(
        agent_kwargs=agent_kwargs,
        assistant=MagicMock(),
        user=MagicMock(),
        request=MagicMock(),
        request_uuid="uuid-1",
        thread_generator=MagicMock(),
        llm_model="gpt-4",
        smart_tool_selection_enabled=False,
        resolved_policy=resolved,
        build_context=SubagentBuildContext(),
        is_root=is_root,
        create_subagent_executors=lambda **_: list(children),
        get_subagent_descriptions=lambda a, u: {"S": "s"},
    )
    return tree, agent_kwargs


def _handle(*, tree):
    from codemie.service.assistant.assistant_engine_builder import SubagentHandle

    return SubagentHandle(agent=MagicMock(), executor=MagicMock(), tree_requires_confirmation=tree)


def test_tree_flag_is_own_or_any_child_and_subagents_are_the_executors():
    from codemie.service.assistant.assistant_engine_builder import ResolvedToolPolicy

    auto = ResolvedToolPolicy(policy=ToolCallPolicy.AUTO_APPROVE, requires_confirmation=False)
    quiet, loud = _handle(tree=False), _handle(tree=True)

    tree, kwargs = _configure_with(resolved=auto, is_root=True, children=[quiet, loud])

    assert tree is True
    assert kwargs["require_tool_confirmation"] is False
    assert kwargs["subagents"] == [quiet.executor, loud.executor]
    # The built agents travel beside their graphs, so the status report can ask their tools what they wrote
    assert kwargs["subagent_agents"] == [quiet.agent, loud.agent]

    tree, _ = _configure_with(resolved=auto, is_root=True, children=[quiet])
    assert tree is False


@pytest.mark.parametrize("can_resume_pause", [False, True], ids=["not-resumable", "resumable"])
def test_building_a_confirming_tree_creates_no_conversation_row(can_resume_pause):
    """Rule 6 (AR-13): the placeholder save creates the row before any run that uses the conversation saver."""
    from codemie.service.assistant.assistant_engine_builder import ResolvedToolPolicy

    asks = ResolvedToolPolicy(policy=ToolCallPolicy.ASK_FOR_APPROVAL, requires_confirmation=True)

    with patch("codemie.service.conversation_service.ConversationService._find_or_create_conversation") as create:
        tree = LangGraphAssistantBuilder.configure_agent_kwargs(
            agent_kwargs={},
            assistant=MagicMock(),
            user=MagicMock(),
            request=MagicMock(),
            request_uuid="uuid-1",
            thread_generator=MagicMock(),
            llm_model="gpt-4",
            smart_tool_selection_enabled=False,
            resolved_policy=asks,
            build_context=SubagentBuildContext(can_resume_pause=can_resume_pause),
            is_root=True,
            create_subagent_executors=lambda **_: [_handle(tree=True)],
            get_subagent_descriptions=lambda a, u: {},
        )

    assert tree is True
    create.assert_not_called()


# --- EPMCDME-16463 Task 21: the build raises tree_requires_confirmation from the built children --------------

from codemie.service.assistant.assistant_engine_builder import (  # noqa: E402
    ResolvedToolPolicy,
    SubagentHandle,
)


def _built_handle(name: str, tree: bool = False) -> SubagentHandle:
    return SubagentHandle(
        agent=MagicMock(),
        executor=f"{name}-graph",
        tree_requires_confirmation=tree,
    )


def _configure(resolved: ResolvedToolPolicy, handles):
    agent_kwargs = {}
    tree = LangGraphAssistantBuilder.configure_agent_kwargs(
        agent_kwargs=agent_kwargs,
        assistant=MagicMock(),
        user=MagicMock(),
        request=MagicMock(),
        request_uuid="uuid-1",
        thread_generator=MagicMock(),
        llm_model="gpt-4",
        smart_tool_selection_enabled=False,
        resolved_policy=resolved,
        build_context=SubagentBuildContext(),
        is_root=False,
        create_subagent_executors=lambda **_: list(handles),
        get_subagent_descriptions=lambda a, u: {},
    )
    return tree, agent_kwargs


def test_a_pausing_agent_requires_confirmation_for_its_tree():
    tree, agent_kwargs = _configure(ResolvedToolPolicy(ToolCallPolicy.ASK_FOR_APPROVAL, True), [])

    assert tree is True
    assert agent_kwargs["tree_requires_confirmation"] is True


def test_a_tree_without_a_confirming_agent_does_not_require_confirmation():
    tree, agent_kwargs = _configure(ResolvedToolPolicy(ToolCallPolicy.AUTO_APPROVE, False), [_built_handle("s1")])

    assert tree is False
    assert agent_kwargs["tree_requires_confirmation"] is False
    assert agent_kwargs["subagents"] == ["s1-graph"]


def test_a_confirming_child_raises_the_flag_and_its_siblings_are_not_rebuilt():
    tree, agent_kwargs = _configure(
        ResolvedToolPolicy(ToolCallPolicy.AUTO_APPROVE, False),
        [_built_handle("s1"), _built_handle("s2", tree=True)],
    )

    assert tree is True
    assert agent_kwargs["tree_requires_confirmation"] is True
    assert agent_kwargs["subagents"] == ["s1-graph", "s2-graph"]
