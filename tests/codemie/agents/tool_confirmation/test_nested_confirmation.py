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

"""Nested pauses: a sub-assistant's tool call pauses the whole run and resumes it, at any depth.

Every turn runs real ``LangGraphAgent`` graphs (scripted models, counted tools) over a
``ConversationCheckpointSaver`` whose in-memory service round-trips each stored value through JSON, as the
JSONB column does. Every resume runs on a rebuilt tree with a new saver, as the resume request does.
"""

from __future__ import annotations

from types import SimpleNamespace
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.agents.tool_confirmation.models import (
    AppliedDecision,
    DecisionItem,
    PendingCallLocation,
    StoredPendingToolCall,
    applied_decisions,
)
from codemie.agents.tool_confirmation.tool_call_confirmation_mixin import ToolCallConfirmationMixin
from codemie.core import tool_step_states
from codemie.core.models import ToolCallPolicy
from codemie.core.thread import ThreadedGenerator

from tests.codemie.agents.tool_confirmation.support import (
    CALLS,
    CONV_ID,
    MIXIN_MODULE as _MIXIN_MODULE,
    SEEN,
    FakeCheckpointService,
    ScriptedModel,
    build_agent as _agent,
    flat as _flat,
    make_request as _request,
    make_assistant as _assistant,
    make_tools as _tools,
    make_user as _user,
    sent as _sent,
)

_ASK = ToolCallPolicy.ASK_FOR_APPROVAL
_AUTO = ToolCallPolicy.AUTO_APPROVE


def _o_over_s(
    generator=None, *, s_policy=_ASK, s_calls=(("write_tool", "s_w"),), o_calls=None, s_rounds=1, s_round_ids=None
):
    """O (auto-approve) delegating to S; ``s_round_ids`` gives each of S's rounds its own call ids."""
    generator = generator if generator is not None else ThreadedGenerator(conversation_id=CONV_ID)
    sub = _agent("s", list(s_calls), generator, policy=s_policy, subagent=True, rounds=s_rounds, round_ids=s_round_ids)
    return _agent("o", o_calls or [("transfer_to_s", "o_s")], generator, subs=[sub])


def _step(step_key: str) -> str:
    return str(LangGraphAgent._tool_call_id_to_uuid(step_key))


# --- AC1: S pauses under O -> S --------------------------------------------------------------------


def test_sub_assistant_call_pauses_the_run_under_its_delegation_step(service):
    orchestrator = _o_over_s()

    orchestrator._agent_streaming([])

    record = service.record()
    assert len(record.items) == 1
    item = record.items[0]
    assert item.event.tool_name == "write_tool"
    assert item.event.location == PendingCallLocation(parent_thought_id=_step("o_s"))
    assert CALLS["tool:write_tool"] == 0
    assert orchestrator._pending_tool_confirmation is True

    thoughts = orchestrator.thread_generator.thoughts
    container = next(t for t in _flat(thoughts) if t["id"] == _step("o_s"))
    waiting = [t for t in _flat(thoughts) if t.get("interrupted")]
    assert [t["id"] for t in waiting] == [item.thought_id]
    assert waiting[0] in container["children"]
    sent_waiting = [
        s["thought"] for s in _sent(orchestrator.thread_generator) if (s.get("thought") or {}).get("interrupted")
    ]
    assert sent_waiting[-1]["parent_id"] == _step("o_s")


# --- AC2 / AC3: Allow and Deny on a rebuilt tree -----------------------------------------------------


def _applied(record: StoredPendingToolCall, decisions: dict[str, str]) -> list[AppliedDecision]:
    """``decisions`` is keyed by tool call id; the request is keyed by the step's thought id."""
    by_thought = {
        item.thought_id: decisions[item.tool_call_id] for item in record.items if item.tool_call_id in decisions
    }
    return applied_decisions(record.items, by_thought)


def _seed_from_row(agent: LangGraphAgent, record: StoredPendingToolCall, applied: list[AppliedDecision]) -> None:
    """What the resume branch does before the run (Task 6): seed the generator from the row's snapshot.

    Allowed steps get the Allowed state, denied ones the Denied state, carried ones ``in_progress``; the first
    chunk replaces the text of the allowed and carried steps only.
    """
    thoughts = deepcopy(record.thoughts)
    carried = set(record.payload.get("carried_ids") or [])
    states = {
        d.item.thought_id: tool_step_states.allowed if d.action == "allow" else tool_step_states.denied for d in applied
    }
    for step in _flat(thoughts):
        if step["id"] in carried:
            step["in_progress"] = True
        if step["id"] in states:
            step.update(states[step["id"]](step))
    agent.thread_generator.seed(thoughts, {d.item.thought_id for d in applied if d.action == "allow"} | carried)


def _run_resume(agent: LangGraphAgent, record: StoredPendingToolCall, decisions: dict[str, str]) -> None:
    applied = _applied(record, decisions)
    _seed_from_row(agent, record, applied)
    agent.resume_from_interrupt(applied)


def _resume(agent: LangGraphAgent, service: FakeCheckpointService, decisions: dict[str, str]) -> None:
    _run_resume(agent, service.record(), decisions)


def _final(sent: list[dict]) -> str:
    return next(item["generated"] for item in reversed(sent) if item.get("last") and item.get("generated"))


def test_allow_runs_the_sub_assistant_call_once_and_its_step_stays_under_the_container(service):
    paused = _o_over_s()
    paused._agent_streaming([])
    thought_id = service.record().items[0].thought_id

    resumed = _o_over_s()
    _resume(resumed, service, {"s_w": "allow"})

    assert CALLS["tool:write_tool"] == 1
    assert CALLS["llm:s"] == 2
    assert CALLS["llm:o"] == 2
    assert resumed._pending_tool_confirmation is False
    sent = _sent(resumed.thread_generator)
    assert "o done" in _final(sent)

    container = next(t for t in _flat(resumed.thread_generator.thoughts) if t["id"] == _step("o_s"))
    step = next(t for t in container["children"] if t["id"] == thought_id)
    assert step["interrupted"] is False
    assert "write_tool EXECUTED" in step["message"]
    assert not [t for t in resumed.thread_generator.thoughts if t["id"] == thought_id], "no top-level copy"


def test_deny_never_runs_the_sub_assistant_call_and_both_agents_finish(service):
    _o_over_s()._agent_streaming([])
    thought_id = service.record().items[0].thought_id

    resumed = _o_over_s()
    _resume(resumed, service, {"s_w": "deny"})

    assert CALLS["tool:write_tool"] == 0
    assert CALLS["llm:s"] == 2
    assert CALLS["llm:o"] == 2
    sent = _sent(resumed.thread_generator)
    assert not [s for s in sent if (s.get("thought") or {}).get("id") == thought_id], "a denied step gets no chunk"
    container = _thought(resumed.thread_generator, _step("o_s"))
    (step,) = [t for t in container["children"] if t["id"] == thought_id]
    assert step["message"] == tool_step_states.DENIED_MESSAGE, "the seed's Denied text appears once"
    assert (step["aborted"], step["interrupted"], step["in_progress"]) == (True, False, False)
    assert "o done" in _final(sent)


# --- AC21: O -> O2 -> S ---------------------------------------------------------------------------------


def _chain(generator=None, *, s_policy=_ASK, o2_policy=_AUTO, o2_calls=None, subs=("s",)):
    """O (auto) -> O2 -> S (one or more S, each with its own write call)."""
    generator = generator if generator is not None else ThreadedGenerator(conversation_id=CONV_ID)
    leaves = [_agent(name, [("write_tool", f"{name}_w")], generator, policy=s_policy, subagent=True) for name in subs]
    o2_calls = o2_calls or [(f"transfer_to_{name}", f"o2_{name}") for name in subs]
    o2 = _agent("o2", o2_calls, generator, policy=o2_policy, subagent=True, subs=leaves)
    return _agent("o", [("transfer_to_o2", "o_o2")], generator, subs=[o2])


def _thought(generator: ThreadedGenerator, thought_id: str) -> dict:
    return next(t for t in _flat(generator.thoughts) if t["id"] == thought_id)


def test_depth_two_pause_goes_under_the_o2_to_s_step_which_is_under_o_to_o2(service):
    orchestrator = _chain()

    orchestrator._agent_streaming([])

    (item,) = service.record().items
    location = item.event.location
    generator = orchestrator.thread_generator
    o_to_o2 = _thought(generator, _step("o_o2"))
    o2_to_s = _thought(generator, location.parent_thought_id)
    assert o2_to_s in o_to_o2["children"]
    waiting = _thought(generator, item.thought_id)
    assert waiting["interrupted"] is True
    assert waiting in o2_to_s["children"]
    assert CALLS["tool:write_tool"] == 0


def test_o2_own_call_goes_under_o_to_o2(service):
    orchestrator = _chain(o2_policy=_ASK, o2_calls=[("write2_tool", "o2_w")], subs=())

    orchestrator._agent_streaming([])

    (item,) = service.record().items
    assert item.event.location == PendingCallLocation(parent_thought_id=_step("o_o2"))
    waiting = _thought(orchestrator.thread_generator, item.thought_id)
    assert waiting in _thought(orchestrator.thread_generator, _step("o_o2"))["children"]


def _o_o1_o2():
    """O -> O1 -> O2, where O2 manages S but pauses its own call: O2's own agent is its internal supervisor."""
    generator = ThreadedGenerator(conversation_id=CONV_ID)
    s = _agent("s", [("write_tool", "s_w")], generator, subagent=True)
    o2 = _agent("o2", [("write2_tool", "o2_w")], generator, policy=_ASK, subagent=True, subs=[s])
    o1 = _agent("o1", [("transfer_to_o2", "o1_o2")], generator, subagent=True, subs=[o2])
    return _agent("o", [("transfer_to_o1", "o_o1")], generator, subs=[o1])


@pytest.mark.parametrize(("decision", "runs"), [("allow", 1), ("deny", 0)])
def test_a_nested_orchestrators_own_call_resumes_inside_its_own_delegation_step(service, decision, runs):
    _o_o1_o2()._agent_streaming([])
    (item,) = service.record().items
    assert item.checkpoint_ns.split("|")[2].startswith("supervisor:"), "O2's own agent is its internal supervisor"

    resumed = _o_o1_o2()
    _resume(resumed, service, {"o2_w": decision})

    assert CALLS["tool:write2_tool"] == runs
    assert resumed._pending_tool_confirmation is False
    generator = resumed.thread_generator
    o1_to_o2 = _thought(generator, item.event.location.parent_thought_id)
    assert o1_to_o2 in _thought(generator, _step("o_o1"))["children"]
    assert _thought(generator, item.thought_id) in o1_to_o2["children"]
    (o2_answer,) = [t for t in _flat(generator.thoughts) if (t.get("message") or "").startswith("o2 done")]
    assert o2_answer in o1_to_o2["children"], "O2's answer after the resume stays in O1 -> O2, not in O -> O1"


# --- AC6 / AC20: the build path gives S its policy, its location and its gate ------------------------


def _built_s_kwargs(*, assistant_policy, request_policy=None, floor=None, name="s") -> dict:
    """S's LangGraph kwargs as the build path fills them under a root that can resume S's pause."""
    from codemie.rest_api.models.assistant import ToolPermissionsConfig
    from codemie.service.assistant.assistant_engine_builder import LangGraphAssistantBuilder, SubagentBuildContext

    assistant = Mock()
    assistant.name = name
    assistant.tool_permissions = ToolPermissionsConfig(tool_call_policy=assistant_policy)
    request = Mock()
    request.tool_call_policy = request_policy
    customer = Mock()
    customer.is_feature_enabled.return_value = True
    customer.get_feature_setting.return_value = floor
    context = SubagentBuildContext(can_resume_pause=True).child()
    agent_kwargs: dict = {}
    with patch("codemie.service.tool_permissions_service.customer_config", customer):
        LangGraphAssistantBuilder.configure_agent_kwargs(
            agent_kwargs=agent_kwargs,
            assistant=assistant,
            user=Mock(),
            request=request,
            request_uuid="req-1",
            thread_generator=Mock(),
            llm_model="gpt-4.1",
            smart_tool_selection_enabled=False,
            resolved_policy=LangGraphAssistantBuilder.resolve_tool_policy(
                assistant, request, allow_tool_confirmation=True
            ),
            build_context=context,
            is_root=False,
            create_subagent_executors=lambda **_: [],
            get_subagent_descriptions=lambda a, u: {},
        )
    return agent_kwargs


def _o_over_built_s(s_kwargs: dict, name: str = "s") -> LangGraphAgent:
    generator = ThreadedGenerator(conversation_id=CONV_ID)
    model = ScriptedModel(role=name, calls=[("write_tool", "s_w")])
    with patch.object(LangGraphAgent, "_initialize_llm", return_value=model):
        sub = LangGraphAgent(
            agent_name=name,
            description=f"assistant {name}",
            tools=_tools(),
            request=_request(),
            system_prompt="You are s.",
            request_uuid="req-1",
            user=_user(),
            llm_model="gpt-4.1",
            assistant=_assistant(f"asst-{name}"),
            thread_generator=generator,
            is_subagent=True,
            **s_kwargs,
        )
    return _agent("o", [(f"transfer_to_{name}", "o_s")], generator, subs=[sub])


@pytest.mark.parametrize(
    ("assistant_policy", "request_policy", "floor"),
    [
        (_AUTO, None, "ask_for_approval"),  # the customer minimum
        (_AUTO, _ASK, None),  # the request override
    ],
)
def test_customer_minimum_and_override_apply_to_the_sub_assistant(service, assistant_policy, request_policy, floor):
    s_kwargs = _built_s_kwargs(assistant_policy=assistant_policy, request_policy=request_policy, floor=floor)
    assert s_kwargs["tool_call_policy"] == _ASK
    assert s_kwargs["can_resume_pause"] is True

    _o_over_built_s(s_kwargs)._agent_streaming([])

    (item,) = service.record().items
    assert CALLS["tool:write_tool"] == 0


def test_builtin_sub_assistant_pauses_and_resumes(service):
    name = "general"
    s_kwargs = _built_s_kwargs(assistant_policy=_ASK, name=name)

    _o_over_built_s(s_kwargs, name=name)._agent_streaming([])
    (item,) = service.record().items
    assert item.event.location == PendingCallLocation(parent_thought_id=_step("o_s"))

    _resume(_o_over_built_s(s_kwargs, name=name), service, {"s_w": "allow"})
    assert CALLS["tool:write_tool"] == 1


# --- AC4: reload --------------------------------------------------------------------------------------


def _pending_ids(agent) -> list[str]:
    return sorted(interrupt_id for interrupt_id, _ in agent._pending_gate_interrupts(agent._get_run_config()))


def test_a_rebuilt_tree_over_the_stored_checkpoints_finds_the_same_interrupts(service):
    paused = _chain(subs=("s1", "s2"))
    paused._agent_streaming([])
    paused_ids = _pending_ids(paused)
    assert len(paused_ids) == 2

    assert _pending_ids(_chain(subs=("s1", "s2"))) == paused_ids


@pytest.mark.parametrize(("decision", "runs"), [("allow", 1), ("deny", 0)])
def test_depth_two_allow_and_deny_after_a_reload_and_the_resumed_step_stays_nested(service, decision, runs):
    paused = _chain()
    paused._agent_streaming([])
    (item,) = service.record().items

    resumed = _chain()
    _resume(resumed, service, {"s_w": decision})

    assert CALLS["tool:write_tool"] == runs
    assert CALLS["llm:s"] == CALLS["llm:o2"] == CALLS["llm:o"] == 2
    assert resumed._pending_tool_confirmation is False
    generator = resumed.thread_generator
    step = _thought(generator, item.thought_id)
    assert step["interrupted"] is False
    assert step in _thought(generator, item.event.location.parent_thought_id)["children"]
    assert _thought(generator, item.event.location.parent_thought_id) in _thought(generator, _step("o_o2"))["children"]


# --- AC21: parallel pauses at depth two -----------------------------------------------------------------


def test_parallel_pauses_at_depth_two_are_decided_together_and_each_call_runs_once(service):
    paused = _chain(subs=("s1", "s2"))
    paused._agent_streaming([])

    record = service.record()
    assert sorted(item.tool_call_id for item in record.items) == ["s1_w", "s2_w"]
    containers = {item.event.location.parent_thought_id for item in record.items}
    assert len(containers) == 2
    generator = paused.thread_generator
    o_to_o2 = _thought(generator, _step("o_o2"))
    for item in record.items:
        container = _thought(generator, item.event.location.parent_thought_id)
        assert container in o_to_o2["children"]
    assert CALLS["tool:write_tool"] == 0

    resumed = _chain(subs=("s1", "s2"))
    _resume(resumed, service, {"s1_w": "allow", "s2_w": "deny"})

    assert CALLS["tool:write_tool"] == 1
    assert CALLS["llm:s1"] == CALLS["llm:s2"] == 2
    assert CALLS["llm:o2"] == CALLS["llm:o"] == 2
    for item in record.items:
        step = _thought(resumed.thread_generator, item.thought_id)
        assert step in _thought(resumed.thread_generator, item.event.location.parent_thought_id)["children"]


# --- AC15 (runtime): two guarded calls of S in one AI message ---------------------------------------


def test_two_calls_of_one_sub_assistant_pause_once_and_resume_once(service):
    calls = (("write_tool", "s_w"), ("write2_tool", "s_w2"))
    paused = _o_over_s(s_calls=calls)
    paused._agent_streaming([])

    record = service.record()
    assert [item.tool_call_id for item in record.items] == ["s_w", "s_w2"]
    assert len(_pending_ids(paused)) == 1
    waiting = [t for t in _flat(paused.thread_generator.thoughts) if t.get("interrupted")]
    assert sorted(t["id"] for t in waiting) == sorted(item.thought_id for item in record.items)

    _resume(_o_over_s(s_calls=calls), service, {"s_w": "allow", "s_w2": "allow"})

    assert CALLS["tool:write_tool"] == CALLS["tool:write2_tool"] == 1


def test_a_resume_without_a_decision_for_a_paused_call_denies_it(service):
    """Rule 5 Run (AR-12): every pending gate interrupt is resumed with no stored ids; an undecided call fails closed."""
    calls = (("write_tool", "s_w"), ("write2_tool", "s_w2"))
    _o_over_s(s_calls=calls)._agent_streaming([])

    record = service.record()
    applied = [d for d in _applied(record, {"s_w": "allow", "s_w2": "allow"}) if d.item.tool_call_id == "s_w"]
    resumed = _o_over_s(s_calls=calls)
    _seed_from_row(resumed, record, applied)
    resumed.resume_from_interrupt(applied)

    assert CALLS["tool:write_tool"] == 1
    assert CALLS["tool:write2_tool"] == 0
    assert resumed._pending_tool_confirmation is False
    assert CALLS["llm:s"] == 2


# --- O hands off to the same S twice in parallel ------------------------------------------------------


def test_two_parallel_handoffs_to_one_sub_assistant_pause_on_one_id_and_resume_at_the_same_location(service):
    handoffs = [("transfer_to_s", "o_s1"), ("transfer_to_s", "o_s2")]
    paused = _o_over_s(o_calls=handoffs)
    paused._agent_streaming([])

    record = service.record()
    assert [item.tool_call_id for item in record.items] == ["s_w", "s_w"]
    assert len({item.location_key for item in record.items}) == 2
    assert len({item.thought_id for item in record.items}) == 2
    assert {item.event.location.parent_thought_id for item in record.items} == {_step("o_s1"), _step("o_s2")}

    rebuilt = _o_over_s(o_calls=handoffs)
    pending = rebuilt._pending_gate_interrupts(rebuilt._get_run_config())
    resumed_locations = sorted((call.location_key, call.checkpoint_ns) for _, gate in pending for call in gate.calls)
    assert resumed_locations == sorted((item.location_key, item.checkpoint_ns) for item in record.items)

    _resume(rebuilt, service, {"s_w": "allow"})

    assert CALLS["tool:write_tool"] == 2
    assert CALLS["llm:s"] == 4


# --- provider conversion of a resumed nested two-round call -----------------------------------------


def test_nested_two_round_call_converts_for_every_provider_family(service):
    import re

    def two_rounds():
        """S asks for one write in each of two rounds, with a new call id per round, as a provider does."""
        return _o_over_s(s_rounds=2, s_round_ids=[["s_w"], ["s_w_r2"]])

    two_rounds()._agent_streaming([])
    _resume(two_rounds(), service, {"s_w": "allow"})
    assert service.record().items[0].tool_call_id == "s_w_r2"
    _resume(two_rounds(), service, {"s_w_r2": "allow"})
    assert CALLS["tool:write_tool"] == 2

    messages = SEEN["s"]
    call_ids = [call["id"] for m in messages if isinstance(m, AIMessage) for call in m.tool_calls]
    result_ids = [m.tool_call_id for m in messages if isinstance(m, ToolMessage)]
    assert call_ids == result_ids == ["s_w", "s_w_r2"]
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", wire_id) for wire_id in call_ids)

    from langchain_anthropic.chat_models import _format_messages
    from langchain_aws.chat_models.bedrock_converse import _messages_to_bedrock
    from langchain_google_vertexai._image_utils import ImageBytesLoader
    from langchain_google_vertexai.chat_models import _parse_chat_history_gemini
    from langchain_openai.chat_models.base import _convert_message_to_dict

    _, anthropic = _format_messages(messages)
    anthropic_ids = [
        b["id"] for m in anthropic for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    assert anthropic_ids == call_ids
    bedrock, _ = _messages_to_bedrock(messages)
    bedrock_ids = [b["toolUse"]["toolUseId"] for m in bedrock for b in m["content"] if "toolUse" in b]
    assert bedrock_ids == call_ids
    openai = [_convert_message_to_dict(m) for m in messages]
    assert [c["id"] for m in openai for c in m.get("tool_calls") or []] == call_ids
    assert [m["tool_call_id"] for m in openai if m["role"] == "tool"] == call_ids
    _, gemini = _parse_chat_history_gemini(messages, ImageBytesLoader())
    assert [p.function_call.name for c in gemini for p in c.parts if p.function_call] == ["write_tool", "write_tool"]


def test_a_paused_stream_keeps_its_checkpoint_and_record(service):
    paused = _o_over_s()
    with patch("codemie.service.conversation_checkpoint_service.ConversationCheckpointService", return_value=service):
        paused.stream()

    assert paused._pending_tool_confirmation is True
    assert service.checkpoints[CONV_ID]["namespaces"]
    assert service.record().items


def test_a_run_that_is_not_a_resume_deletes_leftover_checkpoint_rows_at_its_start(service):
    service.checkpoints[CONV_ID] = {"namespaces": {"leftover": {}}}
    service.records[CONV_ID] = {"left": "alone"}
    paused = _o_over_s()
    with (
        patch("codemie.service.conversation_checkpoint_service.ConversationCheckpointService", return_value=service),
        patch.object(paused, "_agent_streaming", side_effect=RuntimeError("stop here")),
    ):
        paused.stream()

    assert service.checkpoint_deletes == 1
    assert "leftover" not in (service.checkpoints.get(CONV_ID) or {}).get("namespaces", {})
    assert service.records[CONV_ID] == {"left": "alone"}


_SERVICE_CLASS = "codemie.service.conversation_checkpoint_service.ConversationCheckpointService"


def _ends_with_success(service):
    agent = _o_over_s(s_policy=_AUTO)
    with patch(_SERVICE_CLASS, return_value=service):
        agent.stream()
    assert CALLS["tool:write_tool"] == 1
    return agent


def _ends_with_error(service, error):
    agent = _o_over_s()
    with (
        patch(_SERVICE_CLASS, return_value=service),
        patch.object(agent, "_agent_streaming", side_effect=_writes_rows_then(service, error)),
    ):
        agent.stream()
    return agent


def _ends_resumed(service):
    _o_over_s()._agent_streaming([])
    assert service.checkpoints[CONV_ID]
    record = service.record()
    service.records.pop(CONV_ID)  # the consume deleted the row
    resumed = _o_over_s()
    _run_resume(resumed, record, {"s_w": "allow"})
    return resumed


def _writes_rows_then(service: FakeCheckpointService, error: BaseException):
    def run(_chunks):
        service.put_checkpoint(CONV_ID, "", {"checkpoint_id": "1"})
        raise error

    return run


@pytest.mark.parametrize("scenario", ["success", "error", "abort", "resumed"])
def test_a_run_that_ends_without_a_pause_leaves_no_checkpoint_rows(service, scenario):
    from codemie.core.exceptions import StreamAbortedException

    if scenario == "success":
        agent = _ends_with_success(service)
    elif scenario == "error":
        agent = _ends_with_error(service, RuntimeError("boom"))
    elif scenario == "abort":
        agent = _ends_with_error(service, StreamAbortedException("closed"))
    else:
        agent = _ends_resumed(service)

    assert agent._pending_tool_confirmation is False
    assert CONV_ID not in service.checkpoints
    if scenario != "resumed":
        assert agent.thread_generator.is_closed()


def test_a_failing_end_of_run_checkpoint_delete_is_logged_and_the_stream_still_closes(service):
    agent = _o_over_s(s_policy=_AUTO)
    with (
        patch(_SERVICE_CLASS, return_value=service),
        patch.object(service, "delete_checkpoints_if_no_pending", side_effect=RuntimeError("db down")),
    ):
        agent.stream()

    assert agent.thread_generator.is_closed()
    assert "o done" in _final(_sent(agent.thread_generator))


# --- rule 10: the resume binds every path agent, at any depth ----------------------------------------------


class _Binder(ToolCallConfirmationMixin):
    """The mixin's path binding over a fixed step tree, recording each agent bound to its container."""

    _tool_call_id_to_uuid = staticmethod(LangGraphAgent._tool_call_id_to_uuid)

    def __init__(self, thoughts: list[dict]):
        self.thread_generator = SimpleNamespace(thoughts=thoughts)
        self.bound: list[tuple[str, str]] = []
        coordinator = Mock()
        coordinator.binding_for.return_value = None
        coordinator.tracker.activate.side_effect = lambda raw, author, ctx: self.bound.append((author, str(ctx[0])))
        self._supervisor_coordinator = coordinator
        self.set_thread_context = Mock()

    def bind(self, item: DecisionItem) -> None:
        self._bind_resumed_authors([AppliedDecision(item=item, action="allow")])

    def find(self, thought_id: str):
        return self._find_thought(thought_id)


def _nested_item(parent_thought_id: str) -> DecisionItem:
    from codemie.agents.tool_confirmation.models import ToolCallPendingEvent

    return DecisionItem(
        thought_id="t",
        tool_call_id="s_w",
        location_key="k",
        checkpoint_ns="o2:1|s:2|post_model_hook:3",
        event=ToolCallPendingEvent(
            pending_tool_call_id="s_w",
            tool_name="write_tool",
            tool_args={},
            location=PendingCallLocation(parent_thought_id=parent_thought_id),
        ),
    )


def test_a_nested_orchestrators_own_call_binds_the_orchestrator_not_its_internal_supervisor():
    o_o2 = _step("o_o2")
    binder = _Binder([{"id": o_o2, "parent_id": None, "children": []}])
    item = _nested_item(o_o2).model_copy(update={"checkpoint_ns": "o2:1|supervisor:2|post_model_hook:3"})

    binder.bind(item)

    assert binder.bound == [("o2:1", o_o2)]


def test_a_container_that_is_not_a_step_id_is_not_bound_and_does_not_abort_the_resume():
    o2_s = _step("o2_s")
    thoughts = [{"id": o2_s, "parent_id": "latest", "children": []}]
    binder = _Binder(thoughts)

    binder.bind(_nested_item(o2_s))

    assert binder.bound == [("s:2", o2_s)]


# --- AC4 / AC9 on Postgres: the real saver SQL, a fresh build per resume ------------------------------------
# Live only (TEST_LOCAL_PG_URL); every test runs on a throwaway conversations table, dropped afterwards.

# --- rule 5: parallel decisions on one pause, a single {action}, a re-pause inside a delegation ------------------


def _root_with_two_calls(generator=None) -> LangGraphAgent:
    """A root asking to run two confirming tools in one AI message."""
    generator = generator if generator is not None else ThreadedGenerator(conversation_id=CONV_ID)
    return _agent("o", [("write_tool", "w1"), ("write2_tool", "w2")], generator, policy=_ASK)


@pytest.mark.parametrize(
    ("first", "second"), [("allow", "deny"), ("allow", "allow"), ("deny", "deny")], ids=["mixed", "allow", "deny"]
)
def test_two_parallel_calls_decided_in_one_resume_run_the_allowed_ones_once_and_finish(service, first, second):
    paused = _root_with_two_calls()
    paused._agent_streaming([])
    assert sorted(item.tool_call_id for item in service.record().items) == ["w1", "w2"]

    resumed = _root_with_two_calls()
    _resume(resumed, service, {"w1": first, "w2": second})

    assert (CALLS["tool:write_tool"], CALLS["tool:write2_tool"]) == (int(first == "allow"), int(second == "allow"))
    assert CALLS["llm:o"] == 2
    answered = {m.tool_call_id for m in SEEN["o"] if isinstance(m, ToolMessage)}
    assert answered == {"w1", "w2"}, "every call of the AI message has its tool message (no INVALID_CHAT_HISTORY)"
    assert resumed._pending_tool_confirmation is False
    assert "o done" in _final(_sent(resumed.thread_generator))


def test_a_single_call_resume_streams_only_its_step_and_the_answer(service):
    paused = _o_over_s()
    paused._agent_streaming([])
    thought_id = service.record().items[0].thought_id

    resumed = _o_over_s()
    _resume(resumed, service, {"s_w": "allow"})

    sent = _sent(resumed.thread_generator)
    thoughts = [item["thought"] for item in sent if item.get("thought")]
    assert not [t for t in thoughts if t.get("interrupted") or t.get("aborted")], "no waiting or denied chunk"
    assert {t["id"] for t in thoughts if t["id"] == thought_id} == {thought_id}
    assert "o done" in _final(sent)


def _delegating_pair(generator=None) -> LangGraphAgent:
    """O asks to confirm its delegation to S; S asks to confirm its own call."""
    generator = generator if generator is not None else ThreadedGenerator(conversation_id=CONV_ID)
    sub = _agent("s", [("write_tool", "s_w")], generator, policy=_ASK, subagent=True)
    return _agent("o", [("transfer_to_s", "o_s")], generator, policy=_ASK, subs=[sub])


def test_a_re_pause_inside_an_allowed_delegation_carries_its_step_and_ends_with_only_its_result(service):
    _delegating_pair()._agent_streaming([])
    (delegation,) = service.record().items

    repaused = _delegating_pair()
    _resume(repaused, service, {"o_s": "allow"})

    record = service.record()
    assert [item.tool_call_id for item in record.items] == ["s_w"]
    assert record.payload["carried_ids"] == [delegation.thought_id]
    step = next(t for t in _flat(record.thoughts) if t["id"] == delegation.thought_id)
    assert step["in_progress"] is True and step["interrupted"] is False

    final = _delegating_pair()
    _resume(final, service, {"s_w": "allow"})

    assert CALLS["tool:write_tool"] == 1
    # The handoff tool returning is streamed by the agent callbacks (not part of this harness): its result replaces
    # the carried step's earlier text. The run has closed the generator here and a closed generator drops sends, so
    # the thought is applied the way send() applies it while the run is open.
    generator = final.thread_generator
    generator._process_thought(
        {"id": delegation.thought_id, "message": "s done", "in_progress": False},
        generator.thoughts,
        generator.seeded_ids,
    )
    result = _thought(final.thread_generator, delegation.thought_id)
    assert result["message"] == "s done" and result["in_progress"] is False
    assert "o done" in _final(_sent(final.thread_generator))


def _plain_beside_delegation(generator=None) -> LangGraphAgent:
    """O asks to confirm a plain call and its delegation to S in one AI message; S asks to confirm its own call."""
    generator = generator if generator is not None else ThreadedGenerator(conversation_id=CONV_ID)
    sub = _agent("s", [("write_tool", "s_w")], generator, policy=_ASK, subagent=True)
    return _agent("o", [("write2_tool", "o_w2"), ("transfer_to_s", "o_s")], generator, policy=_ASK, subs=[sub])


def test_a_re_pause_after_allowing_a_plain_call_and_a_delegation_carries_the_running_delegation(service):
    _plain_beside_delegation()._agent_streaming([])
    ids = {item.tool_call_id: item.thought_id for item in service.record().items}
    assert sorted(ids) == ["o_s", "o_w2"]

    _resume(_plain_beside_delegation(), service, {"o_w2": "allow", "o_s": "allow"})

    record = service.record()
    assert [item.tool_call_id for item in record.items] == ["s_w"]
    assert CALLS["tool:write2_tool"] == 1
    assert ids["o_s"] in record.payload["carried_ids"]
    delegation = next(t for t in _flat(record.thoughts) if t["id"] == ids["o_s"])
    assert delegation["in_progress"] is True and delegation["interrupted"] is False


def test_denying_a_plain_call_beside_an_allowed_delegation_still_runs_the_delegation(service):
    """The gate's denial result lands before the allowed handoff runs; the handoff must still find its AI message."""
    _plain_beside_delegation()._agent_streaming([])
    ids = {item.tool_call_id: item.thought_id for item in service.record().items}
    assert sorted(ids) == ["o_s", "o_w2"]

    _resume(_plain_beside_delegation(), service, {"o_w2": "deny", "o_s": "allow"})

    record = service.record()
    assert record is not None, "the allowed delegation did not reach S's own confirmation"
    assert [item.tool_call_id for item in record.items] == ["s_w"]
    assert CALLS["tool:write2_tool"] == 0
    assert ids["o_s"] in record.payload["carried_ids"]


def _two_delegations(generator=None) -> LangGraphAgent:
    """O asks to confirm two delegations in one AI message: to S1 (reads) and S2 (writes); neither S asks."""
    generator = generator if generator is not None else ThreadedGenerator(conversation_id=CONV_ID)
    s1 = _agent("s1", [("read_tool", "s1_r")], generator, policy=_AUTO, subagent=True)
    s2 = _agent("s2", [("write_tool", "s2_w")], generator, policy=_AUTO, subagent=True)
    return _agent("o", [("transfer_to_s1", "o_s1"), ("transfer_to_s2", "o_s2")], generator, policy=_ASK, subs=[s1, s2])


def test_a_denied_delegation_is_not_dispatched_by_its_allowed_sibling(service):
    _two_delegations()._agent_streaming([])
    assert sorted(item.tool_call_id for item in service.record().items) == ["o_s1", "o_s2"]

    _resume(_two_delegations(), service, {"o_s1": "allow", "o_s2": "deny"})

    assert (CALLS["tool:read_tool"], CALLS["tool:write_tool"]) == (1, 0)


from tests.codemie.service.pg_conversations import PgCheckpointService, pg_conversations  # noqa: E402


@pytest.fixture
def pg():
    with pg_conversations() as db:
        db.insert(CONV_ID)
        service = PgCheckpointService()
        with patch(f"{_MIXIN_MODULE}.ConversationCheckpointService", return_value=service):
            yield SimpleNamespace(db=db, service=service)
