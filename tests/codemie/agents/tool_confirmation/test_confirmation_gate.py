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

"""Graph tests of the post_model_hook confirmation gate (spike "Decision carrier and batch rule", Q1-Q3).

Every resume runs on a rebuilt graph over the same checkpointer, the way CodeMie rebuilds the agent for
the resume request. Counters show any re-run of a model or a tool.
"""

from __future__ import annotations

import uuid

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command
from langgraph_supervisor import create_supervisor

from codemie.agents.tool_confirmation.call_identity import assign_call_identities
from codemie.agents.tool_confirmation.models import DecisionItem, applied_decisions
from codemie.agents.tool_confirmation.confirmation_gate import (
    DECISIONS_CONFIG_KEY,
    GateInterrupt,
    _with_wire_ids,
    build_confirmation_hook,
    build_decision_carrier,
)
from codemie.core.models import ToolCallPolicy
from codemie.core.tool_step_states import DENIED_MESSAGE

from tests.codemie.agents.tool_confirmation.support import (
    CALLS,
    CountedTool,
    ScriptedModel,
)


def _tools() -> list[BaseTool]:
    return [
        CountedTool(name="read_tool", description="read", safe=True),
        CountedTool(name="write_tool", description="write"),
        CountedTool(name="write2_tool", description="write 2"),
        CountedTool(name="write3_tool", description="write 3"),
    ]


class SupervisorModel(BaseChatModel):
    """Hands off to agent_a and agent_b in parallel, then finishes once a sub-agent answered."""

    @property
    def _llm_type(self) -> str:
        return "scripted-supervisor"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        CALLS["llm:supervisor"] += 1
        answers = [
            m for m in messages if isinstance(m, AIMessage) and getattr(m, "name", None) in ("agent_a", "agent_b")
        ]
        if answers:
            message = AIMessage(content="FINAL: " + " | ".join(str(m.content) for m in answers))
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {"name": "transfer_to_agent_a", "args": {"task": "a"}, "id": "sup_a", "type": "tool_call"},
                    {"name": "transfer_to_agent_b", "args": {"task": "b"}, "id": "sup_b", "type": "tool_call"},
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.fixture(autouse=True)
def _reset_counters():
    CALLS.clear()


def _root(policy: ToolCallPolicy, calls: list[tuple[str, str]], saver, pauses_own_calls: bool = True):
    tools = _tools()
    hook = build_confirmation_hook(policy, tools, is_subagent=False, pauses_own_calls=pauses_own_calls, resumable=True)
    model = ScriptedModel(role="root", calls=calls)
    return create_react_agent(model, tools, name="root", post_model_hook=hook, checkpointer=saver)


def _supervisor(policy: ToolCallPolicy, a_calls, b_calls, saver):
    from codemie.agents.langgraph_agent import LangGraphAgent

    agents = []
    for name, calls in (("agent_a", a_calls), ("agent_b", b_calls)):
        tools = _tools()
        hook = build_confirmation_hook(policy, tools, is_subagent=True, pauses_own_calls=True, resumable=True)
        agents.append(create_react_agent(ScriptedModel(role=name, calls=calls), tools, name=name, post_model_hook=hook))
    handoffs = [
        LangGraphAgent._create_custom_handoff_tool(agent_name=n, name=f"transfer_to_{n}", description=n)
        for n in ("agent_a", "agent_b")
    ]
    return create_supervisor(
        model=SupervisorModel(),
        agents=agents,
        tools=handoffs,
        prompt="sup",
        add_handoff_back_messages=False,
        output_mode="last_message",
    ).compile(checkpointer=saver)


def _cfg(thread_id: str, carrier: dict | None = None) -> dict:
    configurable = {"thread_id": thread_id}
    if carrier is not None:
        configurable[DECISIONS_CONFIG_KEY] = carrier
    return {"configurable": configurable, "recursion_limit": 60}


def _run(graph, inputs, cfg) -> None:
    for _ in graph.stream(inputs, cfg, stream_mode="updates", subgraphs=True):
        pass


def _pending(graph, thread_id: str):
    state = graph.get_state(_cfg(thread_id))
    return [i for task in state.tasks if task.result is None for i in task.interrupts]


def _gate_calls(interrupts) -> list:
    return [call for i in interrupts for call in GateInterrupt.model_validate(i.value).calls]


def _carrier(calls, action_for) -> dict:
    """The carrier of the pause: ``action_for`` decides each shown call, an auto-allowed one allows."""
    items = [
        DecisionItem(
            thought_id=f"thought-{call.tool_call_id}-{call.location_key}",
            tool_call_id=call.tool_call_id,
            location_key=call.location_key,
            checkpoint_ns=call.checkpoint_ns,
            event=call.event,
            auto_allowed=call.decision is not None,
        )
        for call in calls
    ]
    requested = {
        item.thought_id: action_for(call) for item, call in zip(items, calls, strict=True) if not item.auto_allowed
    }
    return build_decision_carrier(applied_decisions(items, requested))


def _tool_counts() -> dict:
    return {k: v for k, v in CALLS.items() if k.startswith("tool:")}


def _messages(graph, thread_id: str) -> list:
    return graph.get_state(_cfg(thread_id)).values["messages"]


def test_mixed_batch_under_approve_for_me_pauses_before_any_tool_and_runs_each_once_after_allow():
    saver, thread = InMemorySaver(), str(uuid.uuid4())
    calls = [("read_tool", "r1"), ("write_tool", "w1")]
    graph = _root(ToolCallPolicy.APPROVE_FOR_ME, calls, saver)

    _run(graph, {"messages": [HumanMessage("go")]}, _cfg(thread))

    pending = _pending(graph, thread)
    assert len(pending) == 1
    gate_calls = _gate_calls(pending)
    shown = [c for c in gate_calls if c.decision is None]
    assert [c.event.tool_name for c in shown] == ["write_tool"]
    assert [(c.tool_call_id, c.decision) for c in gate_calls if c.decision] == [("r1", "allow")]
    assert _tool_counts() == {}

    resumed = _root(ToolCallPolicy.APPROVE_FOR_ME, calls, saver)
    _run(resumed, Command(resume={i.id: "ack" for i in pending}), _cfg(thread, _carrier(gate_calls, lambda c: "allow")))

    assert _tool_counts() == {"tool:read_tool": 1, "tool:write_tool": 1}
    assert resumed.get_state(_cfg(thread)).next == ()


def test_root_pending_event_keeps_the_model_id_and_has_no_location():
    saver, thread = InMemorySaver(), str(uuid.uuid4())
    graph = _root(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "w1")], saver)

    _run(graph, {"messages": [HumanMessage("go")]}, _cfg(thread))

    (call,) = _gate_calls(_pending(graph, thread))
    assert call.tool_call_id == "w1"
    assert call.location_key == ""
    assert "post_model_hook" in call.checkpoint_ns
    assert call.event.pending_tool_call_id == "w1"
    assert call.event.tool_args == {"q": "1"}
    assert call.event.all_tool_call_ids == ["w1"]
    assert call.event.location is None


@pytest.mark.parametrize("new_policy", [ToolCallPolicy.APPROVE_FOR_ME, ToolCallPolicy.AUTO_APPROVE])
def test_deny_never_runs_the_call_even_when_the_policy_is_relaxed(new_policy):
    saver, thread = InMemorySaver(), str(uuid.uuid4())
    calls = [("write_tool", "w1")]
    graph = _root(ToolCallPolicy.ASK_FOR_APPROVAL, calls, saver)
    _run(graph, {"messages": [HumanMessage("go")]}, _cfg(thread))
    pending = _pending(graph, thread)
    gate_calls = _gate_calls(pending)

    resumed = _root(new_policy, calls, saver)
    _run(resumed, Command(resume={i.id: "ack" for i in pending}), _cfg(thread, _carrier(gate_calls, lambda c: "deny")))

    assert _tool_counts() == {}
    final = _messages(resumed, thread)[-1]
    assert DENIED_MESSAGE in str(final.content)


def test_carrier_entry_with_a_foreign_namespace_is_ignored():
    saver, thread = InMemorySaver(), str(uuid.uuid4())
    graph = _root(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "w1")], saver)
    foreign = {"/w1": {"action": "allow", "ns": "post_model_hook:someone-else"}}

    _run(graph, {"messages": [HumanMessage("go")]}, _cfg(thread, foreign))

    assert len(_pending(graph, thread)) == 1
    assert _tool_counts() == {}


def test_call_still_undecided_after_resume_is_denied():
    saver, thread = InMemorySaver(), str(uuid.uuid4())
    calls = [("write_tool", "w1")]
    graph = _root(ToolCallPolicy.ASK_FOR_APPROVAL, calls, saver)
    _run(graph, {"messages": [HumanMessage("go")]}, _cfg(thread))
    pending = _pending(graph, thread)

    resumed = _root(ToolCallPolicy.ASK_FOR_APPROVAL, calls, saver)
    _run(resumed, Command(resume={i.id: "ack" for i in pending}), _cfg(thread, {}))

    assert _tool_counts() == {}
    assert DENIED_MESSAGE in str(_messages(resumed, thread)[-1].content)


def test_all_denied_routes_back_to_the_model():
    saver, thread = InMemorySaver(), str(uuid.uuid4())
    calls = [("write_tool", "w1"), ("write2_tool", "w2")]
    graph = _root(ToolCallPolicy.ASK_FOR_APPROVAL, calls, saver)
    _run(graph, {"messages": [HumanMessage("go")]}, _cfg(thread))
    pending = _pending(graph, thread)
    gate_calls = _gate_calls(pending)
    assert len(gate_calls) == 2

    resumed = _root(ToolCallPolicy.ASK_FOR_APPROVAL, calls, saver)
    _run(resumed, Command(resume={i.id: "ack" for i in pending}), _cfg(thread, _carrier(gate_calls, lambda c: "deny")))

    assert _tool_counts() == {}
    assert CALLS["llm:root"] == 2
    messages = _messages(resumed, thread)
    denials = [m for m in messages if isinstance(m, ToolMessage)]
    assert [(m.tool_call_id, m.content) for m in denials] == [
        ("w1", DENIED_MESSAGE),
        ("w2", DENIED_MESSAGE),
    ]
    assert isinstance(messages[-1], AIMessage) and not messages[-1].tool_calls


def test_two_agents_batch_and_one_combined_resume_runs_each_tool_once():
    saver, thread = InMemorySaver(), str(uuid.uuid4())
    a_calls, b_calls = [("write_tool", "w1"), ("write2_tool", "w2")], [("write3_tool", "w3")]
    graph = _supervisor(ToolCallPolicy.ASK_FOR_APPROVAL, a_calls, b_calls, saver)
    _run(graph, {"messages": [HumanMessage("go")]}, _cfg(thread))

    pending = _pending(graph, thread)
    assert len(pending) == 2
    gate_calls = _gate_calls(pending)
    assert sorted(c.tool_call_id for c in gate_calls) == ["w1", "w2", "w3"]
    assert all(c.location_key for c in gate_calls)
    assert len({c.location_key for c in gate_calls}) == 2
    assert _tool_counts() == {}

    resumed = _supervisor(ToolCallPolicy.ASK_FOR_APPROVAL, a_calls, b_calls, saver)
    _run(resumed, Command(resume={i.id: "ack" for i in pending}), _cfg(thread, _carrier(gate_calls, lambda c: "allow")))

    assert _tool_counts() == {"tool:write_tool": 1, "tool:write2_tool": 1, "tool:write3_tool": 1}
    assert resumed.get_state(_cfg(thread)).next == ()
    assert CALLS["llm:supervisor"] == 2


def test_nested_pending_event_carries_the_agent_location_and_its_handoff_container():
    saver, thread = InMemorySaver(), str(uuid.uuid4())
    graph = _supervisor(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "w1")], [("write3_tool", "w3")], saver)
    _run(graph, {"messages": [HumanMessage("go")]}, _cfg(thread))

    by_id = {c.tool_call_id: c for c in _gate_calls(_pending(graph, thread))}
    location = by_id["w1"].event.location
    assert location.parent_thought_id == str(uuid.uuid5(uuid.NAMESPACE_URL, "sup_a"))
    assert by_id["w3"].event.location.parent_thought_id == str(uuid.uuid5(uuid.NAMESPACE_URL, "sup_b"))


def test_identity_only_hook_rewrites_repeated_ids_in_place_and_never_pauses():
    saver, thread = InMemorySaver(), str(uuid.uuid4())
    graph = _root(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "c0"), ("write2_tool", "c0")], saver, False)

    _run(graph, {"messages": [HumanMessage("go")]}, _cfg(thread))

    assert _pending(graph, thread) == []
    assert _tool_counts() == {"tool:write_tool": 1, "tool:write2_tool": 1}
    messages = _messages(graph, thread)
    ai_messages = [m for m in messages if isinstance(m, AIMessage) and m.tool_calls]
    assert len(ai_messages) == 1
    expected = [i.wire_id for i in assign_call_identities([], ai_messages[0], "")]
    wire_ids = [c["id"] for c in ai_messages[0].tool_calls]
    assert wire_ids == expected
    assert wire_ids[0] == "c0" and wire_ids[1] != "c0"
    assert sorted(m.tool_call_id for m in messages if isinstance(m, ToolMessage)) == sorted(wire_ids)


def test_pausing_gate_shows_rewritten_ids_and_resume_runs_each_once():
    saver, thread = InMemorySaver(), str(uuid.uuid4())
    calls = [("write_tool", "c0"), ("write2_tool", "c0")]
    graph = _root(ToolCallPolicy.ASK_FOR_APPROVAL, calls, saver)
    _run(graph, {"messages": [HumanMessage("go")]}, _cfg(thread))
    pending = _pending(graph, thread)
    gate_calls = _gate_calls(pending)
    ids = [c.tool_call_id for c in gate_calls]
    assert ids[0] == "c0" and ids[1] != "c0"
    assert gate_calls[0].event.all_tool_call_ids == ids

    resumed = _root(ToolCallPolicy.ASK_FOR_APPROVAL, calls, saver)
    carrier = _carrier(gate_calls, lambda c: "allow" if c.tool_call_id == "c0" else "deny")
    _run(resumed, Command(resume={i.id: "ack" for i in pending}), _cfg(thread, carrier))

    assert _tool_counts() == {"tool:write_tool": 1}


def test_replayed_handoff_puts_the_pause_under_the_latest_delegation_step():
    """AC26: a sub-agent handed off to twice waits under the latest delegation step, at any depth."""
    from unittest.mock import patch

    from codemie.agents.supervisor.constants import (
        METADATA_KEY_HANDOFF_DESTINATION,
        METADATA_KEY_HANDOFF_LOCATION,
        METADATA_KEY_SUBAGENT_TASK,
    )
    from codemie.agents.tool_confirmation.models import DecisionItem
    from codemie.agents.tool_confirmation.tool_call_confirmation_mixin import _container_id

    def handoff(call_id: str) -> ToolMessage:
        return ToolMessage(
            content="do it",
            tool_call_id=call_id,
            name="transfer_to_s",
            response_metadata={METADATA_KEY_HANDOFF_DESTINATION: "s"},
            additional_kwargs={METADATA_KEY_SUBAGENT_TASK: True, METADATA_KEY_HANDOFF_LOCATION: "o2:ab12"},
        )

    messages = [
        HumanMessage("go"),
        handoff("h_old"),
        AIMessage(content="s done"),
        handoff("h_new"),
        AIMessage(content="", tool_calls=[{"name": "write_tool", "args": {"q": "1"}, "id": "w1", "type": "tool_call"}]),
    ]
    hook = build_confirmation_hook(
        ToolCallPolicy.ASK_FOR_APPROVAL, _tools(), is_subagent=True, pauses_own_calls=True, resumable=True
    )
    captured: list = []
    ns = "o2:1|s:2|post_model_hook:3"
    with patch(
        "codemie.agents.tool_confirmation.confirmation_gate.interrupt",
        side_effect=lambda value: captured.append(value) or "ack",
    ):
        hook({"messages": messages}, {"configurable": {"checkpoint_ns": ns}})

    (call,) = GateInterrupt.model_validate(captured[0]).calls
    latest = str(uuid.uuid5(uuid.NAMESPACE_URL, "o2:ab12/h_new"))
    assert call.event.location.parent_thought_id == latest
    assert latest != str(uuid.uuid5(uuid.NAMESPACE_URL, "o2:ab12/h_old"))
    item = DecisionItem(
        thought_id="t",
        tool_call_id=call.tool_call_id,
        location_key=call.location_key,
        checkpoint_ns=ns,
        event=call.event,
    )
    assert _container_id(item) == latest  # the waiting step's parent_id


def _use(block_id: str) -> dict:
    return {"type": "tool_use", "id": block_id, "name": "t", "input": {}}


def _call(call_id: str) -> dict:
    return {"name": "t", "args": {}, "id": call_id, "type": "tool_call"}


def test_tool_use_blocks_are_rewritten_by_id_and_unrelated_blocks_stay():
    unrelated = _use("other")
    ai = AIMessage(
        content=[{"type": "text", "text": "x"}, _use("a"), unrelated, _use("b")],
        tool_calls=[_call("a"), _call("b")],
    )

    result = _with_wire_ids(ai, ["w1", "w2"])

    assert [c["id"] for c in result.tool_calls] == ["w1", "w2"]
    assert [b.get("id") for b in result.content[1:]] == ["w1", "other", "w2"]
    assert result.content[2] == unrelated


def test_tool_use_blocks_in_another_order_are_matched_by_id_not_position():
    ai = AIMessage(content=[_use("b"), _use("a")], tool_calls=[_call("a"), _call("b")])

    result = _with_wire_ids(ai, ["w1", "w2"])

    assert [b["id"] for b in result.content] == ["w2", "w1"]


def test_tool_use_blocks_sharing_one_raw_id_take_the_wire_ids_of_their_calls_in_order():
    ai = AIMessage(content=[_use("a"), _use("a")], tool_calls=[_call("a"), _call("a")])

    result = _with_wire_ids(ai, ["w1", "w2"])

    assert [c["id"] for c in result.tool_calls] == ["w1", "w2"]
    assert [b["id"] for b in result.content] == ["w1", "w2"]
