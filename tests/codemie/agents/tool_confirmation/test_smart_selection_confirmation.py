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

"""Tool-call confirmation under smart tool selection, at the root and in a depth-1 sub-assistant.

Real ``LangGraphAgent`` graphs (scripted models, counted tools) run over a ``ConversationCheckpointSaver``
whose in-memory service round-trips every stored value through JSON. The smart selector is replaced by a
fixed, counted selection; every resume runs on a rebuilt agent with a new saver, as the resume request does.
"""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

import pytest

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.agents.smart_tool_selector import SmartToolSelector
from codemie.core.tool_step_states import UNAVAILABLE_MESSAGE
from codemie.agents.tool_confirmation.models import (
    AppliedDecision,
    PendingCallLocation,
    StoredPendingToolCall,
    applied_decisions,
)
from codemie.configs import config
from codemie.core.models import ToolCallPolicy
from codemie.core import tool_step_states
from codemie.core.thread import ThreadedGenerator

from tests.codemie.agents.tool_confirmation.support import (
    BOUND,
    CALLS,
    CONV_ID,
    FakeCheckpointService,
    build_agent,
    flat as _flat,
    sent as _sent,
)

_ASK = ToolCallPolicy.ASK_FOR_APPROVAL
_AUTO = ToolCallPolicy.AUTO_APPROVE
_APPROVE_FOR_ME = ToolCallPolicy.APPROVE_FOR_ME
# The fixed selection: two of the four tools (registry ids are "<name>_<toolkit or ''>").
_SELECTED_IDS = ["read_tool_", "write_tool_"]
_SELECTED_NAMES = ["read_tool", "write_tool"]


def _fixed_selection(self, query, limit=None, history=None):
    CALLS["selection"] += 1
    return list(_SELECTED_IDS), [self.tool_registry[tool_id] for tool_id in _SELECTED_IDS]


@pytest.fixture(autouse=True)
def _smart_selection():
    CALLS.clear()
    BOUND.clear()
    with (
        patch.object(config, "TOOL_SELECTION_THRESHOLD", 2),
        patch.object(SmartToolSelector, "select_tools", _fixed_selection),
    ):
        yield


def _agent(*args, smart: bool = True, **kwargs) -> LangGraphAgent:
    """A confirming-tree agent with smart tool selection on unless told otherwise."""
    return build_agent(*args, smart=smart, **kwargs)


def _root(policy=_ASK, calls=(("write_tool", "call_w"),), require=None) -> LangGraphAgent:
    return _agent("root", list(calls), ThreadedGenerator(conversation_id=CONV_ID), policy=policy, require=require)


def _o_over_s(s_policy=_ASK, s_calls=(("write_tool", "s_w"),), s_require=None) -> LangGraphAgent:
    """O (auto-approve, no smart selection) delegating to a smart-selection S."""
    generator = ThreadedGenerator(conversation_id=CONV_ID)
    sub = _agent("s", list(s_calls), generator, policy=s_policy, require=s_require, subagent=True)
    return _agent("o", [("transfer_to_s", "o_s")], generator, smart=False, subs=[sub])


def _step(step_key: str) -> str:
    return str(LangGraphAgent._tool_call_id_to_uuid(step_key))


def _applied(record: StoredPendingToolCall, decisions: dict[str, str]) -> list[AppliedDecision]:
    """``decisions`` is keyed by tool call id; the request is keyed by the step's thought id."""
    by_thought = {
        item.thought_id: decisions[item.tool_call_id] for item in record.items if item.tool_call_id in decisions
    }
    return applied_decisions(record.items, by_thought)


def _run_resume(agent: LangGraphAgent, record: StoredPendingToolCall, decisions: dict[str, str]) -> None:
    """What the resume branch does before the run (Task 6): seed the generator from the row's snapshot."""
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


def _resume(agent: LangGraphAgent, service: FakeCheckpointService, decisions: dict[str, str]) -> None:
    _run_resume(agent, service.record(), decisions)


def _final(sent: list[dict]) -> str:
    return next(item["generated"] for item in reversed(sent) if item.get("last") and item.get("generated"))


def _bound_only_the_selection(role: str) -> bool:
    """Every tool binding of ``role``'s model (since the last clear) is exactly the selected tool set."""
    return bool(BOUND.get(role)) and all(names == _SELECTED_NAMES for names in BOUND[role])


def _tool_runs() -> int:
    return sum(count for key, count in CALLS.items() if key.startswith("tool:"))


# --- AC23: a single smart-selection assistant pauses on its write call -------------------------------


def test_single_smart_assistant_pauses_on_its_write_call(service):
    agent = _root()

    agent._agent_streaming([])

    (item,) = service.record().items
    assert item.tool_call_id == "call_w"
    assert item.event.tool_name == "write_tool"
    assert item.event.location is None
    assert item.thought_id == _step("call_w")
    waiting = [t for t in _flat(agent.thread_generator.thoughts) if t.get("interrupted")]
    assert [t["id"] for t in waiting] == [_step("call_w")]
    assert agent._pending_tool_confirmation is True
    assert _tool_runs() == 0
    assert _bound_only_the_selection("root")


def test_smart_assistant_without_confirmation_has_the_identity_hook_and_runs_the_call():
    agent = _agent(
        "root", [("write_tool", "call_w")], ThreadedGenerator(conversation_id=CONV_ID), tree_requires_confirmation=False
    )
    assert callable(agent._confirmation_hook(agent.tools))

    agent._agent_streaming([])

    assert CALLS["tool:write_tool"] == 1
    assert agent._pending_tool_confirmation is False


# --- AC13: resume with the same selected tool set ----------------------------------------------------


def test_root_smart_resume_binds_the_same_selected_tools_and_runs_the_call_once(service):
    _root()._agent_streaming([])
    BOUND.clear()

    resumed = _root()
    _resume(resumed, service, {"call_w": "allow"})

    assert CALLS["tool:write_tool"] == 1
    assert CALLS["selection"] == 1, "the stored selection is reused, not re-run"
    assert _bound_only_the_selection("root")
    assert CALLS["llm:root"] == 2
    assert "write_tool EXECUTED" in _final(_sent(resumed.thread_generator))
    assert resumed._pending_tool_confirmation is False


def test_depth_one_smart_sub_assistant_pauses_under_its_delegation_and_resumes_with_the_same_tools(service):
    paused = _o_over_s()
    paused._agent_streaming([])

    (item,) = service.record().items
    assert item.event.location == PendingCallLocation(parent_thought_id=_step("o_s"))
    thoughts = _flat(paused.thread_generator.thoughts)
    container = next(t for t in thoughts if t["id"] == _step("o_s"))
    waiting = [t for t in thoughts if t.get("interrupted")]
    assert [t["id"] for t in waiting] == [item.thought_id]
    assert waiting[0] in container["children"]
    sent_waiting = [s["thought"] for s in _sent(paused.thread_generator) if (s.get("thought") or {}).get("interrupted")]
    assert sent_waiting[-1]["parent_id"] == _step("o_s")
    assert CALLS["tool:write_tool"] == 0

    assert _bound_only_the_selection("s")
    BOUND.clear()

    resumed = _o_over_s()
    _resume(resumed, service, {"s_w": "allow"})

    assert CALLS["tool:write_tool"] == 1
    assert CALLS["selection"] == 1
    assert _bound_only_the_selection("s")
    assert CALLS["llm:s"] == 2
    assert CALLS["llm:o"] == 2
    assert "o done" in _final(_sent(resumed.thread_generator))
    step = next(t for t in _flat(resumed.thread_generator.thoughts) if t["id"] == item.thought_id)
    assert step["interrupted"] is False
    assert "write_tool EXECUTED" in step["message"]


# --- AC5: a mixed batch under smart selection runs each call once ------------------------------------


_MIXED = (("read_tool", "call_r"), ("write_tool", "call_w"))


@pytest.mark.parametrize("depth", ["root", "depth_one"])
def test_smart_mixed_batch_waits_for_the_unsafe_call_and_runs_each_once(service, depth):
    prefix = "call" if depth == "root" else "s"
    mixed = (("read_tool", f"{prefix}_r"), ("write_tool", f"{prefix}_w"))
    build = (lambda: _root(_APPROVE_FOR_ME, mixed)) if depth == "root" else (lambda: _o_over_s(_APPROVE_FOR_ME, mixed))
    build()._agent_streaming([])

    record = service.record()
    assert [(item.tool_call_id, item.auto_allowed) for item in record.items] == [
        (f"{prefix}_r", True),
        (f"{prefix}_w", False),
    ]
    assert _tool_runs() == 0

    _resume(build(), service, {f"{prefix}_w": "allow"})

    assert CALLS["tool:read_tool"] == 1
    assert CALLS["tool:write_tool"] == 1
    assert CALLS["llm:root" if depth == "root" else "llm:s"] == 2
    if depth == "depth_one":
        assert CALLS["llm:o"] == 2


def test_single_smart_assistant_starts_the_waiting_calls_step_from_the_gate_identity(service):
    agent = _root()

    agent._agent_streaming([])

    (item,) = service.record().items
    started = [
        s["thought"]
        for s in _sent(agent.thread_generator)
        if (s.get("thought") or {}).get("id") == _step("call_w") and not s["thought"].get("interrupted")
    ]
    assert started, "the waiting call's step is started before it is shown as waiting"
    assert item.event.display_name


# --- AC14: a run that cannot be resumed answers a confirming call as unavailable ---------------------


def _unresumable(variant: str) -> LangGraphAgent:
    """A tree built for a run that cannot be resumed (sync, background, history off): no agent may pause."""
    generator = ThreadedGenerator(conversation_id=CONV_ID)
    if variant == "nested":
        sub = _agent(
            "s", list(_MIXED), generator, policy=_APPROVE_FOR_ME, smart=False, subagent=True, can_resume_pause=False
        )
        return _agent("o", [("transfer_to_s", "o_s")], generator, smart=False, subs=[sub], can_resume_pause=False)
    return _agent(
        "root", list(_MIXED), generator, policy=_APPROVE_FOR_ME, smart=variant == "smart", can_resume_pause=False
    )


@pytest.mark.parametrize("variant", ["root", "nested", "smart"])
def test_a_run_that_cannot_be_resumed_answers_a_confirming_call_as_unavailable(service, variant):
    agent = _unresumable(variant)

    answer = agent._agent_streaming([])

    assert agent._pending_tool_confirmation is False
    assert service.records == {}
    assert CALLS["tool:write_tool"] == 0
    assert CALLS["tool:read_tool"] == 1
    (step,) = [t for t in _flat(agent.thread_generator.thoughts) if t.get("author_name") == "Write Tool"]
    assert step == {**step, **tool_step_states.unavailable(step)}
    assert step["message"] == UNAVAILABLE_MESSAGE
    assert UNAVAILABLE_MESSAGE in answer  # the model got the explanation and the turn finished


def _sync(variant: str) -> LangGraphAgent:
    """A tree built for a sync request: no generator, so the steps are the invoke callback's thoughts."""
    if variant == "nested":
        sub = _agent("s", list(_MIXED), None, policy=_APPROVE_FOR_ME, smart=False, subagent=True)
        return _agent("o", [("transfer_to_s", "o_s")], None, smart=False, subs=[sub])
    return _agent("root", list(_MIXED), None, policy=_APPROVE_FOR_ME, smart=False)


@pytest.mark.parametrize("variant", ["root", "nested"])
def test_a_sync_run_saves_the_confirming_calls_step_as_unavailable(service, variant):
    agent = _sync(variant)

    result = agent._invoke_agent(agent._get_inputs())

    assert result.success
    assert service.records == {}
    assert CALLS["tool:write_tool"] == 0
    (step,) = [t for t in _flat(agent.get_thoughts_from_callback()) if t.get("author_name") == "Write Tool"]
    assert step == {**step, **tool_step_states.unavailable(step)}
