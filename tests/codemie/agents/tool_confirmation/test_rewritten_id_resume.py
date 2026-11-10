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

"""Resume of a call whose wire id differs from the raw id the model sent (a Gemini-via-LiteLLM id).

The AI message keeps the raw id, the waiting step is keyed by the wire id. On resume the gate re-runs over the same
checkpointed state, so it assigns the same wire id the pause stored; the stream resolves the tool result's raw id to
that wire id, so the waiting step is the one restarted and finished. Every resume runs on a rebuilt tree.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch

import pytest

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.agents.smart_tool_selector import SmartToolSelector
from codemie.agents.tool_confirmation.call_identity import CallIdentity
from codemie.agents.tool_confirmation.models import StoredPendingToolCall, applied_decisions
from codemie.configs import config
from codemie.core import tool_step_states
from codemie.core.models import ToolCallPolicy
from codemie.core.thread import ThreadedGenerator

from tests.codemie.agents.tool_confirmation.support import CALLS, CONV_ID, build_agent, flat

_ASK = ToolCallPolicy.ASK_FOR_APPROVAL
# A LiteLLM id carrying a Gemini thought signature: over 40 chars and outside ``^[a-zA-Z0-9_-]{1,40}$``.
GEMINI_ID = "call_3f2a9c1e7b4d4e8a9c2f1d6b5a8e0f11__thought__CiQB/vH+9kQ2Zm8xR3p0Tg=="


def _root(generator, smart=False):
    return build_agent("o", [("write_tool", GEMINI_ID)], generator, policy=_ASK, smart=smart)


def _supervisor_own_call(generator, smart=False):
    sub = build_agent("s", [], generator, subagent=True)
    return build_agent("o", [("write_tool", GEMINI_ID)], generator, policy=_ASK, subs=[sub])


def _nested(generator, smart=False):
    sub = build_agent("s", [("write_tool", GEMINI_ID)], generator, policy=_ASK, subagent=True, smart=smart)
    return build_agent("o", [("transfer_to_s", "o_s")], generator, subs=[sub])


def _nested_orchestrator_own_call(generator, smart=False):
    """O -> O1 -> O2, where O2 manages S but pauses its own call: O2's own agent is its internal supervisor."""
    s = build_agent("s", [], generator, subagent=True)
    o2 = build_agent("o2", [("write_tool", GEMINI_ID)], generator, policy=_ASK, subagent=True, subs=[s])
    o1 = build_agent("o1", [("transfer_to_o2", "o1_o2")], generator, subagent=True, subs=[o2])
    return build_agent("o", [("transfer_to_o1", "o_o1")], generator, subs=[o1])


_SHAPES = {
    "root": _root,
    "supervisor": _supervisor_own_call,
    "nested": _nested,
    "nested-orchestrator": _nested_orchestrator_own_call,
}
_SMART_SHAPES = {"smart-root": _root, "smart-nested": _nested}


@contextmanager
def _smart_selection(enabled: bool):
    """Smart selection with a fixed two-tool selection, the way the smart-selection tests set it up."""
    if not enabled:
        yield
        return
    ids = ["read_tool_", "write_tool_"]

    def select(self, query, limit=None, history=None):
        return list(ids), [self.tool_registry[tool_id] for tool_id in ids]

    with patch.object(config, "TOOL_SELECTION_THRESHOLD", 2), patch.object(SmartToolSelector, "select_tools", select):
        yield


def _build(shape: str):
    builder = {**_SHAPES, **_SMART_SHAPES}[shape]
    return builder(ThreadedGenerator(conversation_id=CONV_ID), smart=shape.startswith("smart"))


def _resume(agent: LangGraphAgent, record: StoredPendingToolCall, action: str) -> None:
    """What the resume branch does before the run: seed the generator from the row's snapshot, then resume."""
    applied = applied_decisions(record.items, {item.thought_id: action for item in record.items})
    states = {
        d.item.thought_id: tool_step_states.allowed if action == "allow" else tool_step_states.denied for d in applied
    }
    thoughts = deepcopy(record.thoughts)
    for step in flat(thoughts):
        if step["id"] in states:
            step.update(states[step["id"]](step))
    agent.thread_generator.seed(thoughts, {d.item.thought_id for d in applied if d.action == "allow"})
    agent.resume_from_interrupt(applied)


def _raw_step_ids(location_key: str) -> set[str]:
    """Every step id a thought would carry if it were keyed by the raw id instead of the wire id."""
    keys = {GEMINI_ID, CallIdentity(wire_id=GEMINI_ID, location_key=location_key).step_key}
    return {str(LangGraphAgent._tool_call_id_to_uuid(key)) for key in keys}


def _pause(shape: str, service):
    _build(shape)._agent_streaming([])
    (item,) = service.record().items
    assert item.tool_call_id != GEMINI_ID, "the gate gives an unsafe raw id its own wire id"
    assert item.event.tool_name == "write_tool"
    return item


# A smart-selection root is not covered here: its gate and tools run inside the wrapper's ``agent`` node, whose inner
# updates never stream, so its resumed tool step gets no result whatever its id (the raw id kept or not).
@pytest.mark.parametrize("shape", [*_SHAPES, "smart-nested"])
def test_an_allowed_call_with_a_rewritten_id_finishes_its_own_waiting_step_on_resume(service, shape):
    with _smart_selection(shape.startswith("smart")):
        item = _pause(shape, service)
        record = service.record()

        resumed = _build(shape)
        _resume(resumed, record, "allow")

    assert CALLS["tool:write_tool"] == 1
    assert resumed._pending_tool_confirmation is False
    thoughts = flat(resumed.thread_generator.thoughts)
    waiting = [t for t in thoughts if t["id"] == item.thought_id]
    assert waiting, "the waiting step is still in the tree"
    assert "write_tool EXECUTED" in waiting[-1]["message"]
    assert (waiting[-1]["interrupted"], waiting[-1]["in_progress"]) == (False, False)
    assert not [t for t in thoughts if t["id"] in _raw_step_ids(item.location_key)], "no step keyed by the raw id"


@pytest.mark.parametrize("shape", [*_SHAPES, *_SMART_SHAPES])
def test_a_denied_call_with_a_rewritten_id_never_runs_and_keeps_its_denied_step(service, shape):
    with _smart_selection(shape.startswith("smart")):
        item = _pause(shape, service)
        record = service.record()

        resumed = _build(shape)
        _resume(resumed, record, "deny")

    assert CALLS["tool:write_tool"] == 0
    assert resumed._pending_tool_confirmation is False
    thoughts = flat(resumed.thread_generator.thoughts)
    (step,) = [t for t in thoughts if t["id"] == item.thought_id]
    assert step["message"] == tool_step_states.DENIED_MESSAGE
    assert (step["aborted"], step["interrupted"], step["in_progress"]) == (True, False, False)
    assert not [t for t in thoughts if t["id"] in _raw_step_ids(item.location_key)], "no step keyed by the raw id"
