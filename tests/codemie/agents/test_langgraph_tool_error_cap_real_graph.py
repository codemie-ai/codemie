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

"""Integration coverage for AC6: the consecutive-tool-error cap must be
isolated per parallel Send() branch of the same subagent, not shared across
branches. Unlike test_langgraph_tool_error_cap.py (all-mocked, no real
graph), this drives an actual compiled LangGraph graph that dispatches two
parallel Send() branches to the same subagent node name, and feeds the real
streamed chunks through the real event-adapter -> agent callback stack.
"""

import operator
from typing import Annotated, TypedDict
from unittest.mock import Mock

from langchain_core.messages import ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.agents.langgraph_event_adapter import LangGraphEventAdapter
from codemie.agents.supervisor.coordinator import _SupervisorChunkContext
from codemie.configs import config
from codemie.core.exceptions import MaxConsecutiveToolErrorsExceededException

CAP = config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS


class _OuterState(TypedDict):
    # The outer graph's schema only shares "messages" with the worker subgraph, so
    # LangGraph does not merge the subgraph-only remaining_errors/call_index keys at the
    # parent level -- avoids a concurrent-write conflict when both parallel Send branches
    # finish and would otherwise both write the same outer channel.
    messages: Annotated[list, operator.add]


class _WorkerState(TypedDict):
    messages: Annotated[list, operator.add]
    remaining_errors: int
    call_index: int


def _tools_node(state: _WorkerState) -> dict:
    remaining = state.get("remaining_errors", 0)
    call_index = state.get("call_index", 0)
    status = "error" if remaining > 0 else "success"
    message = ToolMessage(
        content="boom" if status == "error" else "ok",
        name="some_tool",
        tool_call_id=f"call-{call_index}",
        status=status,
    )
    return {
        "messages": [message],
        "remaining_errors": max(remaining - 1, 0),
        "call_index": call_index + 1,
    }


def _loop_or_stop(state: _WorkerState) -> str:
    return "tools" if state.get("remaining_errors", 0) > 0 else END


def _build_graph():
    inner = StateGraph(_WorkerState)
    inner.add_node("tools", _tools_node)
    inner.add_edge(START, "tools")
    inner.add_conditional_edges("tools", _loop_or_stop, ["tools", END])
    worker_subgraph = inner.compile()

    def supervisor(state: _OuterState) -> list[Send]:
        return [
            Send("worker", {"messages": [], "remaining_errors": CAP, "call_index": 0}),
            Send("worker", {"messages": [], "remaining_errors": 1, "call_index": 0}),
        ]

    outer = StateGraph(_OuterState)
    outer.add_node("worker", worker_subgraph)
    outer.add_conditional_edges(START, supervisor, ["worker"])
    outer.add_edge("worker", END)
    return outer.compile()


def _make_supervisor_agent():
    agent = object.__new__(LangGraphAgent)
    agent.subagents = ["worker"]
    agent.subagent_descriptions = {"worker": "Worker subagent"}
    agent._consecutive_tool_error_count = {}
    agent._inflight_tool_run_ids = {}
    agent._max_consecutive_tool_errors_cache = None
    agent._supervisor_coordinator = Mock()
    agent._supervisor_coordinator.binding_for.return_value = None
    agent._pending_handoffs = {}
    agent._callback_bridge = Mock()
    agent._event_adapter = LangGraphEventAdapter(agent)
    agent._supervisor_chunk_context_type = _SupervisorChunkContext
    return agent


def test_consecutive_tool_error_cap_is_isolated_per_parallel_send_branch():
    graph = _build_graph()
    chunks = list(
        graph.stream(
            {"messages": []},
            stream_mode=["updates", "messages"],
            subgraphs=True,
        )
    )

    agent = _make_supervisor_agent()
    raised = []
    for chunk in chunks:
        try:
            agent.process_chunk(chunk, [])
        except MaxConsecutiveToolErrorsExceededException as exc:
            raised.append(exc)

    assert len(raised) == 1, "exactly the branch that hit the cap should raise, once"

    counts = agent._consecutive_tool_error_count
    assert len(counts) == 2, "each parallel Send branch must get its own counter key"
    capped = [v for v in counts.values() if v >= CAP]
    uncapped = [v for v in counts.values() if v < CAP]
    assert capped == [CAP]
    assert uncapped == [1], "the other branch's single error must not be affected by the capped branch"
