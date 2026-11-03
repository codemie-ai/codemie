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

"""The agent carries the origin from the thread that built it to the thread that runs it.

The assistant executor is a plain thread pool that copies no context, so the models built while the
run is in flight - a tool's own LLM, a compaction summary, a retrieval embedding - see whatever the
agent republishes and nothing else.
"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.chains.base import GenerationResult
from codemie.core.models import AssistantChatRequest
from codemie.core.thread import ThreadedGenerator
from codemie.rest_api.security.user import User
from codemie.service.spend.origin_context import current_origin, spend_origin
from codemie.service.spend.traffic_class import TrafficOrigin


def _run_on_bare_pool(call):
    """Run `call` on a plain thread pool - the shape of the executor that runs assistant streams."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(call).result()


@pytest.fixture
def chat_request():
    request = MagicMock(spec=AssistantChatRequest)
    request.text = "run"
    request.conversation_id = "conversation-1"
    request.history = []
    request.file_names = []
    request.system_prompt = None
    request.metadata = {}
    request.version = None
    return request


@pytest.fixture
def user():
    mock_user = MagicMock(spec=User)
    mock_user.id = "user-1"
    mock_user.username = "user@example.com"
    return mock_user


@pytest.fixture
def thread_generator():
    generator = MagicMock(spec=ThreadedGenerator)
    generator.is_closed.return_value = False
    return generator


def _build_ai_tools_agent(chat_request, user, thread_generator) -> AIToolsAgent:
    with patch.object(AIToolsAgent, "init_agent", return_value=MagicMock()):
        return AIToolsAgent(
            agent_name="test_agent",
            description="Test agent",
            tools=[],
            request=chat_request,
            system_prompt="Test system prompt",
            request_uuid="request-1",
            user=user,
            llm_model="gpt-4o",
            thread_generator=thread_generator,
        )


def _build_langgraph_agent(chat_request, user, thread_generator) -> LangGraphAgent:
    with (
        patch.object(LangGraphAgent, "init_agent", return_value=MagicMock()),
        patch.object(LangGraphAgent, "_is_conversation_replay_v2_enabled", return_value=False),
    ):
        return LangGraphAgent(
            agent_name="test_agent",
            description="Test agent",
            tools=[],
            request=chat_request,
            system_prompt="Test system prompt",
            request_uuid="request-1",
            user=user,
            llm_model="gpt-4o",
            thread_generator=thread_generator,
        )


def test_bare_pool_thread_sees_no_origin():
    """The premise of the whole re-binding: submit() carries nothing across the thread hop."""
    with spend_origin(TrafficOrigin.CONVERSATION):
        assert _run_on_bare_pool(current_origin) is None


class TestAIToolsAgentOrigin:
    def test_stream_republishes_the_construction_origin_on_the_pool_thread(self, chat_request, user, thread_generator):
        with spend_origin(TrafficOrigin.CONVERSATION):
            agent = _build_ai_tools_agent(chat_request, user, thread_generator)

        seen = {}
        with patch.object(
            AIToolsAgent, "_agent_streaming", side_effect=lambda *_: seen.update(origin=current_origin())
        ):
            _run_on_bare_pool(agent.stream)

        assert seen["origin"] is TrafficOrigin.CONVERSATION

    def test_generate_republishes_the_construction_origin_on_the_pool_thread(
        self, chat_request, user, thread_generator
    ):
        with spend_origin(TrafficOrigin.CONVERSATION):
            agent = _build_ai_tools_agent(chat_request, user, thread_generator)

        seen = {}
        with (
            patch.object(
                AIToolsAgent,
                "_invoke_agent",
                side_effect=lambda *_: (seen.update(origin=current_origin()), {"output": "done"})[1],
            ),
            patch.object(AIToolsAgent, "_get_inputs", return_value={}),
            patch.object(AIToolsAgent, "_persist_generated_workspace_files"),
        ):
            _run_on_bare_pool(agent.generate)

        assert seen["origin"] is TrafficOrigin.CONVERSATION

    def test_stream_leaves_the_enclosing_origin_alone_when_none_was_captured(
        self, chat_request, user, thread_generator
    ):
        """A missing origin stays missing: the agent must not publish None over a live value."""
        agent = _build_ai_tools_agent(chat_request, user, thread_generator)
        assert agent._origin is None

        seen = {}
        with patch.object(
            AIToolsAgent, "_agent_streaming", side_effect=lambda *_: seen.update(origin=current_origin())
        ):
            with spend_origin(TrafficOrigin.TOOLS):
                agent.stream()

        assert seen["origin"] is TrafficOrigin.TOOLS

    def test_stream_restores_what_the_thread_carried_before(self, chat_request, user, thread_generator):
        """Pool threads are reused, so the run gives the thread back the way it found it."""
        with spend_origin(TrafficOrigin.CONVERSATION):
            agent = _build_ai_tools_agent(chat_request, user, thread_generator)

        with patch.object(AIToolsAgent, "_agent_streaming"):
            with spend_origin(TrafficOrigin.WORKFLOW):
                agent.stream()
                assert current_origin() is TrafficOrigin.WORKFLOW

        assert current_origin() is None


class TestLangGraphAgentOrigin:
    def test_stream_republishes_the_construction_origin_on_the_pool_thread(self, chat_request, user, thread_generator):
        with spend_origin(TrafficOrigin.CONVERSATION):
            agent = _build_langgraph_agent(chat_request, user, thread_generator)

        seen = {}
        with patch.object(
            LangGraphAgent,
            "_agent_streaming",
            side_effect=lambda *_: (seen.update(origin=current_origin()), "done")[1],
        ):
            _run_on_bare_pool(agent.stream)

        assert seen["origin"] is TrafficOrigin.CONVERSATION

    def test_generate_republishes_the_construction_origin_on_the_pool_thread(
        self, chat_request, user, thread_generator
    ):
        with spend_origin(TrafficOrigin.CONVERSATION):
            agent = _build_langgraph_agent(chat_request, user, thread_generator)

        seen = {}
        generated = GenerationResult(
            generated="done", time_elapsed=0.0, input_tokens_used=None, tokens_used=1, success=True
        )
        with (
            patch.object(
                LangGraphAgent,
                "_invoke_agent",
                side_effect=lambda *_: (seen.update(origin=current_origin()), generated)[1],
            ),
            patch.object(LangGraphAgent, "_get_inputs", return_value={}),
            patch.object(LangGraphAgent, "_persist_generated_workspace_files"),
        ):
            _run_on_bare_pool(agent.generate)

        assert seen["origin"] is TrafficOrigin.CONVERSATION

    def test_workflow_built_agent_carries_the_workflow_origin(self, chat_request, user, thread_generator):
        """A workflow node's agent needs no special case: the workflow published before it was built."""
        with spend_origin(TrafficOrigin.WORKFLOW):
            agent = _build_langgraph_agent(chat_request, user, thread_generator)

        seen = {}
        with patch.object(
            LangGraphAgent,
            "_agent_streaming",
            side_effect=lambda *_: (seen.update(origin=current_origin()), "done")[1],
        ):
            _run_on_bare_pool(agent.stream)

        assert seen["origin"] is TrafficOrigin.WORKFLOW
