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

"""Root and supervisor pauses through the ``post_model_hook`` gate of a real ``LangGraphAgent``.

Each turn runs the agent's real graph (scripted model, counted tools) over a ``ConversationCheckpointSaver``
backed by an in-memory service. Every resume runs on a rebuilt agent, as the resume request does. Counters
show any re-run of a model or a tool.
"""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from codemie.core import tool_step_states
from codemie.core.tool_step_states import DENIED_MESSAGE
from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.agents.tool_confirmation.models import (
    AppliedDecision,
    StoredPendingToolCall,
    ToolCallPendingEvent,
    applied_decisions,
)
from codemie.core.models import ToolCallPolicy
from codemie.core.thread import ThreadedGenerator

from tests.codemie.agents.tool_confirmation.support import (
    CALLS,
    CONV_ID,
    FakeCheckpointService,
    ScriptedModel,
    build_agent,
    flat as _flat,
    make_assistant as _assistant,
    patched_storage,
    make_tools as _tools,
    make_user as _user,
    make_request as _request,
    sent as _sent,
)


def _root_agent(policy: ToolCallPolicy, calls: list[tuple[str, str]], *, require: bool = True) -> LangGraphAgent:
    return build_agent("root", calls, ThreadedGenerator(conversation_id=CONV_ID), policy=policy, require=require)


def _thoughts(generator: ThreadedGenerator) -> list[dict]:
    return [item["thought"] for item in _sent(generator) if item.get("thought")]


def _applied(record: StoredPendingToolCall, decisions: dict[str, str]) -> list[AppliedDecision]:
    """``decisions`` is keyed by tool call id; the request is keyed by the step's thought id."""
    by_thought = {
        item.thought_id: decisions[item.tool_call_id] for item in record.items if item.tool_call_id in decisions
    }
    return applied_decisions(record.items, by_thought)


def _resume(agent: LangGraphAgent, service: FakeCheckpointService, decisions: dict[str, str]) -> None:
    """What the resume branch does (Task 6): seed the generator from the row's snapshot, then resume the graph."""
    record = service.record()
    applied = _applied(record, decisions)
    states = {
        d.item.thought_id: tool_step_states.allowed if d.action == "allow" else tool_step_states.denied for d in applied
    }
    thoughts = deepcopy(record.thoughts)
    for step in _flat(thoughts):
        if step["id"] in states:
            step.update(states[step["id"]](step))
    agent.thread_generator.seed(thoughts, {d.item.thought_id for d in applied if d.action == "allow"})
    agent.resume_from_interrupt(applied)


# --- AC11: a single top-level call ---------------------------------------------------------------


def test_single_root_call_pauses_with_main_event_and_step_id(service):
    agent = _root_agent(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "call_w")])

    agent._agent_streaming([])

    step_id = str(LangGraphAgent._tool_call_id_to_uuid("call_w"))
    thoughts = _thoughts(agent.thread_generator)
    started = [t for t in thoughts if t["id"] == step_id and t.get("in_progress")]
    waiting = [t for t in thoughts if t.get("interrupted")]
    assert started, "the tool step is started from the AI message"
    assert [t["id"] for t in waiting] == [step_id]
    assert thoughts.index(started[0]) < thoughts.index(waiting[0])

    record = service.record()
    assert len(record.items) == 1
    item = record.items[0]
    assert item.thought_id == step_id
    assert item.auto_allowed is False
    assert item.event == ToolCallPendingEvent(
        pending_tool_call_id="call_w",
        tool_name="write_tool",
        tool_args={"q": "1"},
        display_name=started[0]["author_name"],
        all_tool_call_ids=["call_w"],
        location=None,
    )
    assert record.root_assistant_id == "asst-root"
    assert agent._pending_tool_confirmation is True
    assert CALLS["tool:write_tool"] == 0


def _final(generator: ThreadedGenerator) -> str:
    return next(item["generated"] for item in reversed(_sent(generator)) if item.get("last") and item.get("generated"))


def test_allow_runs_the_single_call_once_on_a_rebuilt_agent(service):
    _root_agent(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "call_w")])._agent_streaming([])

    resumed = _root_agent(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "call_w")])
    _resume(resumed, service, {"call_w": "allow"})

    assert CALLS["tool:write_tool"] == 1
    assert CALLS["llm:root"] == 2
    assert "write_tool EXECUTED" in _final(resumed.thread_generator)
    assert resumed._pending_tool_confirmation is False


def test_deny_never_runs_the_call_and_closes_its_step(service):
    _root_agent(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "call_w")])._agent_streaming([])

    resumed = _root_agent(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "call_w")])
    _resume(resumed, service, {"call_w": "deny"})

    step_id = str(LangGraphAgent._tool_call_id_to_uuid("call_w"))
    sent = _sent(resumed.thread_generator)
    assert not [
        item for item in sent if (item.get("thought") or {}).get("id") == step_id
    ], "a denied step gets no chunk"
    (step,) = [t for t in resumed.thread_generator.thoughts if t["id"] == step_id]
    assert step["message"] == DENIED_MESSAGE, "the seed's Denied text appears once"
    assert CALLS["tool:write_tool"] == 0
    final = next(item["generated"] for item in reversed(sent) if item.get("generated"))
    assert DENIED_MESSAGE in final


# --- policies -------------------------------------------------------------------------------------


def test_approve_for_me_safe_call_runs_without_a_pause(service):
    agent = _root_agent(ToolCallPolicy.APPROVE_FOR_ME, [("read_tool", "call_r")])

    agent._agent_streaming([])

    assert CALLS["tool:read_tool"] == 1
    assert service.records == {}
    assert agent._pending_tool_confirmation is False


@pytest.mark.parametrize(
    ("policy", "tool_name"),
    [
        (ToolCallPolicy.APPROVE_FOR_ME, "write_tool"),
        (ToolCallPolicy.APPROVE_FOR_ME, "unknown_tool"),
        (ToolCallPolicy.ASK_FOR_APPROVAL, "read_tool"),
    ],
)
def test_call_needing_confirmation_pauses_before_it_runs(service, policy, tool_name):
    agent = _root_agent(policy, [(tool_name, "call_1")])

    agent._agent_streaming([])

    assert [item.event.tool_name for item in service.record().items] == [tool_name]
    assert agent._pending_tool_confirmation is True
    assert sum(count for key, count in CALLS.items() if key.startswith("tool:")) == 0


def test_answer_without_tool_calls_does_not_pause(service):
    agent = _root_agent(ToolCallPolicy.ASK_FOR_APPROVAL, [])

    agent._agent_streaming([])

    assert service.records == {}
    assert agent._pending_tool_confirmation is False


# --- the supervisor's own calls ------------------------------------------------------------------


def _sub_agent(name: str, tool_name: str, generator) -> LangGraphAgent:
    model = ScriptedModel(role=name, calls=[(tool_name, f"{name}_call")])
    with patch.object(LangGraphAgent, "_initialize_llm", return_value=model):
        return LangGraphAgent(
            agent_name=name,
            description=f"sub-assistant {name}",
            tools=_tools(),
            request=_request(),
            system_prompt="You are a sub-assistant.",
            request_uuid="req-1",
            user=_user(),
            llm_model="gpt-4.1",
            assistant=_assistant(),
            thread_generator=generator,
            is_subagent=True,
        )


def _orchestrator(policy: ToolCallPolicy, calls: list[tuple[str, str]], generator=None) -> LangGraphAgent:
    generator = generator if generator is not None else ThreadedGenerator(conversation_id=CONV_ID)
    subs = [_sub_agent("s1", "write_tool", generator), _sub_agent("s2", "write2_tool", generator)]
    model = ScriptedModel(role="orchestrator", calls=calls)
    with patch.object(LangGraphAgent, "_initialize_llm", return_value=model):
        return LangGraphAgent(
            agent_name="Orchestrator",
            description="orchestrator",
            tools=_tools(),
            request=_request(),
            system_prompt="You are an orchestrator.",
            request_uuid="req-1",
            user=_user(),
            llm_model="gpt-4.1",
            assistant=_assistant(),
            thread_generator=generator,
            subagents=[sub.agent_executor for sub in subs],
            subagent_descriptions={"s1": "first", "s2": "second"},
            require_tool_confirmation=True,
            tool_call_policy=policy,
            can_resume_pause=True,
        )


_PARALLEL_HANDOFFS = [("transfer_to_s1", "o_s1"), ("transfer_to_s2", "o_s2")]


def test_orchestrator_parallel_handoffs_are_both_shown_and_each_sub_assistant_runs_once(service):
    from langgraph.prebuilt.tool_node import ToolNode

    orchestrator = _orchestrator(ToolCallPolicy.ASK_FOR_APPROVAL, _PARALLEL_HANDOFFS)
    orchestrator._agent_streaming([])

    record = service.record()
    assert [(item.tool_call_id, item.auto_allowed) for item in record.items] == [("o_s1", False), ("o_s2", False)]
    assert all(item.event.location is None for item in record.items)
    assert CALLS["llm:s1"] == CALLS["llm:s2"] == 0

    resumed = _orchestrator(ToolCallPolicy.ASK_FOR_APPROVAL, _PARALLEL_HANDOFFS)
    _resume(resumed, service, {"o_s1": "allow", "o_s2": "allow"})

    # bootstrap.py's parallel-Send patch is what merges the two handoff commands.
    assert ToolNode._combine_tool_outputs.__name__ == "_patched_toolnode_combine_tool_outputs"
    assert CALLS["llm:s1"] == CALLS["llm:s2"] == 2
    assert CALLS["tool:write_tool"] == CALLS["tool:write2_tool"] == 1
    assert CALLS["llm:orchestrator"] == 2
    assert resumed._pending_tool_confirmation is False


def test_orchestrator_tool_step_starts_before_its_waiting_step_with_subgraphs(service):
    """Chunk order on the pinned langgraph with ``subgraphs=True``: start, then the waiting step."""
    orchestrator = _orchestrator(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "o_w")])
    with patch.object(orchestrator.agent_executor, "stream", wraps=orchestrator.agent_executor.stream) as stream:
        orchestrator._agent_streaming([])
    assert stream.call_args.kwargs["subgraphs"] is True

    step_id = str(LangGraphAgent._tool_call_id_to_uuid("o_w"))
    thoughts = _thoughts(orchestrator.thread_generator)
    positions = [index for index, thought in enumerate(thoughts) if thought["id"] == step_id]
    assert positions, "the orchestrator's tool step is streamed"
    assert thoughts[positions[0]].get("in_progress") and not thoughts[positions[0]].get("interrupted")
    assert thoughts[positions[-1]].get("interrupted")
    assert service.record().items[0].thought_id == step_id
    assert CALLS["tool:write_tool"] == 0


# --- rule 2 and 5: every shown call waits, and the pause flag is set with the pending flag -------------------


def test_two_shown_calls_get_two_waiting_steps_streamed_and_both_saved(service):
    agent = _root_agent(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "call_w"), ("write2_tool", "call_w2")])

    agent._agent_streaming([])

    ids = [str(LangGraphAgent._tool_call_id_to_uuid(call_id)) for call_id in ("call_w", "call_w2")]
    waiting = [t for t in _thoughts(agent.thread_generator) if t.get("interrupted")]
    assert sorted(t["id"] for t in waiting) == sorted(ids)
    assert sorted(item.thought_id for item in service.record().items) == sorted(ids)


class _OrderedService(FakeCheckpointService):
    """Records which chunks the generator had sent when the pause was written."""

    def __init__(self, generator: ThreadedGenerator):
        super().__init__()
        self.generator = generator
        self.sent_at_write: list[dict] = []

    def write_pause(self, conversation_id, record: StoredPendingToolCall) -> None:
        self.sent_at_write = [json.loads(chunk) for chunk in self.generator._history_chunks]
        super().write_pause(conversation_id, record)


def test_the_row_is_written_before_the_first_waiting_chunk_with_the_snapshot_and_the_request_fields():
    calls = [("read_tool", "call_r"), ("write_tool", "call_w"), ("write2_tool", "call_w2")]
    agent = _root_agent(ToolCallPolicy.APPROVE_FOR_ME, calls)
    service = _OrderedService(agent.thread_generator)

    with patched_storage(service):
        agent._agent_streaming([])

    assert not [c for c in service.sent_at_write if (c.get("thought") or {}).get("interrupted")]
    record = service.record()
    assert record.history_index == 4
    assert record.original_user_message == "hi"
    shown = {item.thought_id for item in record.items if item.shown}
    assert len(shown) == 2 and len(record.items) == 3
    waiting = [t for t in _flat(record.thoughts) if t.get("interrupted")]
    assert sorted(t["id"] for t in waiting) == sorted(shown), "one interrupted step per shown call"
    assert "interrupt_ids" not in record.payload
    assert record.payload["carried_ids"] == []
    assert "hide_tool_outputs" not in record.payload
    streamed = [t for t in _thoughts(agent.thread_generator) if t.get("interrupted")]
    assert sorted(t["id"] for t in streamed) == sorted(shown)


def test_the_run_stays_paused_when_streaming_a_waiting_step_fails_after_the_write(service):
    agent = _root_agent(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "call_w")])
    send = agent.thread_generator.send

    def failing_send(data):
        if '"interrupted":true' in data:
            raise RuntimeError("stream broke")
        send(data)

    with (
        patch.object(agent.thread_generator, "send", side_effect=failing_send),
        pytest.raises(RuntimeError, match="stream broke"),
    ):
        agent._agent_streaming([])

    assert service.records, "the pause was written before the failure"
    assert agent._pending_tool_confirmation is True


def test_a_pause_carries_the_allowed_steps_that_still_run(service):
    agent = _root_agent(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "call_w")])
    running = {"id": "running-step", "in_progress": True}
    finished = {"id": "finished-step", "in_progress": False}
    agent.thread_generator.seed([running, finished], ["running-step", "finished-step"])

    agent._agent_streaming([])

    assert service.record().payload["carried_ids"] == ["running-step"]


def test_a_first_pause_carries_nothing(service):
    agent = _root_agent(ToolCallPolicy.ASK_FOR_APPROVAL, [("write_tool", "call_w")])

    agent._agent_streaming([])

    assert service.record().payload["carried_ids"] == []
