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

"""Proves _build_truncation_abort_pre_model_hook stops the supervisor/sub-agent
recursion-limit loop through a REAL, unmocked create_supervisor(...).compile() graph
(EPMCDME-16233). The existing supervisor fixtures across this test package patch
`create_supervisor` away, so none of them exercise the actual LangGraph node wiring the
hook depends on - these do.
"""

from unittest.mock import Mock, patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool as langchain_tool
from langgraph.prebuilt import create_react_agent
from langgraph_supervisor import create_supervisor
from pydantic import Field

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.agents.supervisor.pre_model_hooks import _build_truncation_abort_pre_model_hook
from codemie.core.exceptions import StreamAbortedException, TokenLimitExceededException


class RecordingFakeChatModel(FakeMessagesListChatModel):
    """Fake chat model that counts how many times it was actually invoked."""

    call_count: int = Field(default=0)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.call_count += 1
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _fake_agent(agent_name: str, *, stream_closed: bool = False) -> LangGraphAgent:
    """A minimal LangGraphAgent the hook can close over: just enough attributes for
    `_safe_check_for_truncation` (llm_model, and the _callback_bridge that flags the failed
    thought before the exception is raised) and the abort check (thread_generator).
    """
    agent = object.__new__(LangGraphAgent)
    agent.agent_name = agent_name
    agent.llm_model = "gpt-4.1"
    agent._callback_bridge = Mock()
    agent.thread_generator = Mock(
        is_closed=Mock(return_value=stream_closed),
        is_consumer_aborted=Mock(return_value=False),
        cancellation_reason=None,
    )
    return agent


@langchain_tool("regular_tool")
def regular_tool(param: str) -> str:
    """A trivial tool the supervisor can call directly."""
    return f"did {param}"


@langchain_tool("child_tool")
def child_tool(param: str) -> str:
    """A trivial tool the sub-agent can call directly."""
    return f"did {param}"


def _truncated_tool_call_message(tool_name: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": {"param": "value"}, "id": "call_1"}],
        response_metadata={"finish_reason": "length"},
    )


class TestSupervisorTruncationStopsLoop:
    def test_supervisor_node_truncation_stops_further_llm_calls(self):
        fake_agent = _fake_agent("Supervisor")
        child_model = RecordingFakeChatModel(responses=[AIMessage(content="child final")])
        child = create_react_agent(model=child_model, tools=[], name="analyst")

        trap_message = AIMessage(content="the supervisor should never reach this")
        supervisor_model = RecordingFakeChatModel(
            responses=[_truncated_tool_call_message("regular_tool"), trap_message]
        )

        app = create_supervisor(
            model=supervisor_model,
            agents=[child],
            tools=[regular_tool],
            add_handoff_back_messages=False,
            output_mode="last_message",
            pre_model_hook=_build_truncation_abort_pre_model_hook(fake_agent),
        ).compile()

        try:
            app.invoke({"messages": [HumanMessage(content="hi")]})
            raised = None
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below
            raised = exc

        assert isinstance(raised, TokenLimitExceededException)
        # The supervisor's node ran the tool (whose result the hook's next pass catches
        # by scanning back to the truncated AIMessage), but never asked the model again.
        assert supervisor_model.call_count == 1

    def test_subagent_node_truncation_stops_further_llm_calls(self):
        fake_child_agent = _fake_agent("analyst")
        child_trap_message = AIMessage(content="the sub-agent should never reach this")
        child_model = RecordingFakeChatModel(responses=[_truncated_tool_call_message("child_tool"), child_trap_message])
        child = create_react_agent(
            model=child_model,
            tools=[child_tool],
            name="analyst",
            pre_model_hook=_build_truncation_abort_pre_model_hook(fake_child_agent),
        )

        handoff_tool = LangGraphAgent._create_custom_handoff_tool(
            agent_name="analyst",
            name="transfer_to_analyst",
            description="Sub-assistant: Analyst",
        )
        supervisor_model = RecordingFakeChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "transfer_to_analyst",
                            "args": {"task": "do the thing"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="supervisor final"),
            ]
        )

        app = create_supervisor(
            model=supervisor_model,
            agents=[child],
            tools=[handoff_tool],
            add_handoff_back_messages=False,
            output_mode="last_message",
        ).compile()

        try:
            app.invoke({"messages": [HumanMessage(content="hi")]})
            raised = None
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below
            raised = exc

        assert isinstance(raised, TokenLimitExceededException)
        # The supervisor handed off once; the failure is scoped to the sub-agent's own
        # node, which also only ever reached the model once.
        assert supervisor_model.call_count == 1
        assert child_model.call_count == 1

    def test_aborted_stream_stops_next_model_call(self):
        fake_agent = _fake_agent("Supervisor", stream_closed=True)
        child_model = RecordingFakeChatModel(responses=[AIMessage(content="child final")])
        child = create_react_agent(model=child_model, tools=[], name="analyst")
        supervisor_model = RecordingFakeChatModel(responses=[AIMessage(content="should never be called")])

        app = create_supervisor(
            model=supervisor_model,
            agents=[child],
            tools=[],
            add_handoff_back_messages=False,
            output_mode="last_message",
            pre_model_hook=_build_truncation_abort_pre_model_hook(fake_agent),
        ).compile()

        try:
            app.invoke({"messages": [HumanMessage(content="hi")]})
            raised = None
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below
            raised = exc

        assert isinstance(raised, StreamAbortedException)
        # The abort is checked before any model call for the turn, not after one.
        assert supervisor_model.call_count == 0

    def test_normal_tool_calling_run_completes_unaffected(self):
        fake_agent = _fake_agent("Supervisor")
        child_model = RecordingFakeChatModel(responses=[AIMessage(content="child final")])
        child = create_react_agent(model=child_model, tools=[], name="analyst")

        normal_tool_call = AIMessage(
            content="",
            tool_calls=[{"name": "regular_tool", "args": {"param": "value"}, "id": "call_1"}],
            response_metadata={"finish_reason": "tool_calls"},
        )
        final_answer = AIMessage(content="all done", response_metadata={"finish_reason": "stop"})
        supervisor_model = RecordingFakeChatModel(responses=[normal_tool_call, final_answer])

        app = create_supervisor(
            model=supervisor_model,
            agents=[child],
            tools=[regular_tool],
            add_handoff_back_messages=False,
            output_mode="last_message",
            pre_model_hook=_build_truncation_abort_pre_model_hook(fake_agent),
        ).compile()

        result = app.invoke({"messages": [HumanMessage(content="hi")]})

        assert result["messages"][-1].content == "all done"
        # Unaffected: the tool call, then the final answer - exactly as today.
        assert supervisor_model.call_count == 2


def _mock_user() -> Mock:
    user = Mock()
    user.id = "test_user_id"
    user.name = "Test User"
    user.username = "testuser"
    return user


def _mock_request() -> Mock:
    request = Mock()
    request.conversation_id = "test_conv_id"
    request.text = "hi"
    request.history = []
    request.system_prompt = None
    request.file_names = None
    request.metadata = {}
    return request


class TestRealAgentConstructionWiresTruncationHook:
    """CR-001: _build_supervisor_agent/_build_single_agent must wire the hook for real.

    The tests above hand-pass `_build_truncation_abort_pre_model_hook(fake_agent)` straight
    into a hand-built `create_supervisor`/`create_react_agent` call, never through
    `_build_supervisor_agent`/`_build_single_agent` themselves. These go through
    LangGraphAgent's real `__init__`/`init_agent()` instead, with `create_supervisor` and
    `create_smart_react_agent` left unmocked, so a dropped hook in either builder would make
    one of these fail.
    """

    def test_build_supervisor_agent_wiring_stops_loop_through_real_init(self):
        child_model = RecordingFakeChatModel(responses=[AIMessage(content="child final")])
        child = create_react_agent(model=child_model, tools=[], name="analyst")

        trap_message = AIMessage(content="the supervisor should never reach this")
        supervisor_model = RecordingFakeChatModel(
            responses=[_truncated_tool_call_message("regular_tool"), trap_message]
        )

        assistant = Mock()
        assistant.project = "test"

        with patch.object(LangGraphAgent, "_initialize_llm", return_value=supervisor_model):
            agent = LangGraphAgent(
                agent_name="Supervisor",
                description="Supervisor agent",
                tools=[regular_tool],
                request=_mock_request(),
                system_prompt="You are a supervisor.",
                request_uuid="test_uuid",
                user=_mock_user(),
                llm_model="gpt-4.1",
                assistant=assistant,
                subagents=[child],
                thread_generator=Mock(
                    is_closed=Mock(return_value=False),
                    is_consumer_aborted=Mock(return_value=False),
                    cancellation_reason=None,
                ),
            )

        try:
            agent.agent_executor.invoke({"messages": [HumanMessage(content="hi")]})
            raised = None
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below
            raised = exc

        assert isinstance(raised, TokenLimitExceededException)
        assert supervisor_model.call_count == 1

    def test_build_single_agent_wiring_stops_on_closed_stream_through_real_init(self):
        model = RecordingFakeChatModel(responses=[AIMessage(content="should never be called")])

        assistant = Mock()
        assistant.project = "test"

        with patch.object(LangGraphAgent, "_initialize_llm", return_value=model):
            agent = LangGraphAgent(
                agent_name="Worker",
                description="Single agent, no subagents",
                tools=[regular_tool],
                request=_mock_request(),
                system_prompt="You are a worker.",
                request_uuid="test_uuid",
                user=_mock_user(),
                llm_model="gpt-4.1",
                assistant=assistant,
                thread_generator=Mock(
                    is_closed=Mock(return_value=True),
                    is_consumer_aborted=Mock(return_value=False),
                    cancellation_reason=None,
                ),
            )

        try:
            agent.agent_executor.invoke({"messages": [HumanMessage(content="hi")]})
            raised = None
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below
            raised = exc

        assert isinstance(raised, StreamAbortedException)
        assert model.call_count == 0
