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

"""Structured output (``output_schema``) on the one graph shape: every agent carries the ``post_model_hook``."""

from __future__ import annotations

import json
from copy import deepcopy

from codemie.agents.tool_confirmation.models import applied_decisions
from codemie.core import tool_step_states
from codemie.core.models import ToolCallPolicy
from codemie.core.thread import ThreadedGenerator

from tests.codemie.agents.tool_confirmation.support import CALLS, CONV_ID, build_agent, flat, sent


# A request's ``output_schema`` is a JSON schema; its structured response is a dict.
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def _agent(policy: ToolCallPolicy):
    return build_agent(
        "root",
        [("write_tool", "call_w")],
        ThreadedGenerator(conversation_id=CONV_ID),
        policy=policy,
        tree_requires_confirmation=policy != ToolCallPolicy.AUTO_APPROVE,
        output_schema=dict(SCHEMA),
    )


def test_a_non_confirming_agent_with_a_tool_call_returns_its_structured_response():
    agent = _agent(ToolCallPolicy.AUTO_APPROVE)

    result = agent._agent_streaming([])

    assert CALLS["tool:write_tool"] == 1
    assert result == {"answer": "root structured: write_tool EXECUTED"}


def test_a_confirming_agent_pauses_and_after_allow_returns_its_structured_response(service):
    agent = _agent(ToolCallPolicy.ASK_FOR_APPROVAL)
    agent._agent_streaming([])
    assert agent._pending_tool_confirmation is True
    assert CALLS["tool:write_tool"] == 0

    record = service.record()
    applied = applied_decisions(record.items, {item.thought_id: "allow" for item in record.items})
    resumed = _agent(ToolCallPolicy.ASK_FOR_APPROVAL)
    thoughts = deepcopy(record.thoughts)
    for step in flat(thoughts):
        if step["id"] in {item.thought_id for item in record.items}:
            step.update(tool_step_states.allowed(step))
    resumed.thread_generator.seed(thoughts, {item.thought_id for item in record.items})
    resumed.resume_from_interrupt(applied)

    assert CALLS["tool:write_tool"] == 1
    assert resumed._pending_tool_confirmation is False
    final = next(item for item in reversed(sent(resumed.thread_generator)) if item.get("last"))
    assert json.loads(final["generated"]) == {"answer": "root structured: write_tool EXECUTED"}
