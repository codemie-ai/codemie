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

"""Headroom pre-model hook wiring in LangGraphAgent (single-agent and supervisor chains)."""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.core.models import AssistantChatRequest
from codemie.service.headroom.models import HeadroomStrategy, ResolvedHeadroomConfig

BUILDER_PATH = "codemie.agents.langgraph_agent.build_headroom_pre_model_hook"


@pytest.fixture
def resolved_config():
    return ResolvedHeadroomConfig(
        compress_user_messages=True, compress_tool_outputs=True, strategy=HeadroomStrategy.BALANCED
    )


@pytest.fixture
def base_config():
    user = MagicMock()
    user.id = "test_user_id"
    user.name = "Test User"
    user.username = "testuser"

    request = MagicMock(spec=AssistantChatRequest)
    request.conversation_id = "test_conv_id"
    request.text = "Test request"
    request.history = []
    request.system_prompt = None
    request.file_names = None
    request.metadata = {}

    tool = MagicMock(spec=BaseTool)
    tool.name = "regular_tool"
    tool.description = "A regular tool"
    tool.metadata = {}

    assistant = MagicMock()
    assistant.project = "test"
    return {
        "agent_name": "Agent",
        "description": "desc",
        "tools": [tool],
        "request": request,
        "system_prompt": "You are an assistant.",
        "request_uuid": "test_uuid",
        "user": user,
        "llm_model": "gpt-4",
        "assistant": assistant,
    }


def _make_sentinel(calls: list):
    def sentinel(state):
        calls.append(state)
        return {"llm_input_messages": [HumanMessage(content="compressed")]}

    return sentinel


def _build_single(config: dict, builder_patch):
    with (
        patch("codemie.agents.langgraph_agent.create_supervisor"),
        patch("codemie.agents.langgraph_agent.create_smart_react_agent") as create_react,
        patch("codemie.core.dependecies.get_llm_by_credentials"),
        builder_patch as builder,
    ):
        agent = LangGraphAgent(**config)
    return agent, builder, create_react.call_args.kwargs["pre_model_hook"]


def _build_supervisor(config: dict, builder_patch):
    config = {**config, "subagents": [MagicMock()]}
    with (
        patch("codemie.agents.langgraph_agent.create_supervisor") as create_supervisor,
        patch("codemie.agents.langgraph_agent.create_smart_react_agent"),
        patch("codemie.core.dependecies.get_llm_by_credentials"),
        builder_patch as builder,
    ):
        create_supervisor.return_value.compile.return_value = MagicMock()
        agent = LangGraphAgent(**config)
    return agent, builder, create_supervisor.call_args.kwargs["pre_model_hook"]


def _state():
    return {"messages": [HumanMessage(content="original")]}


class TestHeadroomHookSingleAgent:
    def test_configured_agent_runs_headroom_hook_last(self, base_config, resolved_config):
        calls: list = []
        sentinel = _make_sentinel(calls)

        agent, builder, composed = _build_single(
            {**base_config, "headroom_config": resolved_config}, patch(BUILDER_PATH, return_value=sentinel)
        )

        builder.assert_called_once_with(resolved_config, "gpt-4", "test_uuid")
        assert agent.headroom_pre_model_hook is sentinel
        result = composed(_state())
        assert len(calls) == 1
        assert result["llm_input_messages"][0].content == "compressed"

    def test_headroom_hook_runs_after_earlier_hooks(self, base_config, resolved_config):
        order: list[str] = []

        def earlier(state):
            order.append("earlier")
            return {"llm_input_messages": [HumanMessage(content="from-earlier")]}

        def sentinel(state):
            order.append("headroom")
            assert state["messages"][0].content == "from-earlier"
            return {}

        with (
            patch("codemie.agents.langgraph_agent._image_artifact_pre_model_hook", earlier),
        ):
            _, _, composed = _build_single(
                {**base_config, "headroom_config": resolved_config}, patch(BUILDER_PATH, return_value=sentinel)
            )
            composed(_state())

        assert order == ["earlier", "headroom"]

    def test_unconfigured_agent_chain_is_unchanged(self, base_config):
        agent, builder, composed = _build_single(base_config, patch(BUILDER_PATH, return_value=None))

        builder.assert_called_once_with(None, "gpt-4", "test_uuid")
        assert agent.headroom_pre_model_hook is None
        result = composed(_state())
        assert result["llm_input_messages"][0].content == "original"


class TestHeadroomHookSupervisor:
    def test_supervisor_runs_headroom_hook_after_strip_hooks(self, base_config, resolved_config):
        order: list[str] = []

        def strip_handoff(state):
            order.append("strip_handoff")
            return {}

        def strip_task(state):
            order.append("strip_task")
            return {}

        def sentinel(state):
            order.append("headroom")
            return {}

        with (
            patch("codemie.agents.langgraph_agent._strip_handoff_back_messages_pre_model_hook", strip_handoff),
            patch("codemie.agents.langgraph_agent._strip_subagent_task_messages_pre_model_hook", strip_task),
        ):
            _, builder, composed = _build_supervisor(
                {**base_config, "headroom_config": resolved_config}, patch(BUILDER_PATH, return_value=sentinel)
            )
            composed(_state())

        builder.assert_called_once_with(resolved_config, "gpt-4", "test_uuid")
        assert order == ["strip_handoff", "strip_task", "headroom"]

    def test_unconfigured_supervisor_builder_gets_none(self, base_config):
        agent, builder, composed = _build_supervisor(base_config, patch(BUILDER_PATH, return_value=None))

        builder.assert_called_once_with(None, "gpt-4", "test_uuid")
        assert agent.headroom_pre_model_hook is None
        assert callable(composed)
