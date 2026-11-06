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

"""Guards of the pause lifecycle on the agent side: run start, the gate's items and the step stream."""

from __future__ import annotations

import json
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.agents.langgraph_event_adapter import LangGraphEventAdapter
from codemie.agents.tool_confirmation.confirmation_gate import GateCall
from codemie.agents.tool_confirmation.models import ToolCallPendingEvent, applied_decisions
from codemie.agents.tool_confirmation.stream_steps import ConfirmationStepRouter
from codemie.agents.tool_confirmation.tool_call_confirmation_mixin import ToolCallConfirmationMixin
from codemie.core.thread import ThreadedGenerator

_LG = "codemie.agents.langgraph_agent"


def _bare_agent(**attributes) -> LangGraphAgent:
    agent = object.__new__(LangGraphAgent)
    agent.__dict__.update(
        {
            "_otel_context": None,
            "agent_name": "A",
            "llm_model": "m",
            "request_uuid": "r",
            "conversation_id": "conv-1",
            "request": MagicMock(save_history=True),
            "user": MagicMock(),
            "assistant": MagicMock(),
            "require_tool_confirmation": True,
            "tree_requires_confirmation": True,
            "is_subagent": False,
            "can_resume_pause": True,
            "subagents": [],
            "_supervisor_coordinator": MagicMock(),
            **attributes,
        }
    )
    return agent


# --- CR-001: the run-start checkpoint delete is inside the stream's error handling --------------------------


def test_a_failing_checkpoint_delete_at_run_start_ends_the_stream_with_an_error():
    generator = ThreadedGenerator(conversation_id="conv-1")
    agent = _bare_agent(thread_generator=generator)
    agent._agent_streaming = MagicMock()
    agent._send_error_to_thread = MagicMock()

    with (
        patch(
            "codemie.service.conversation_checkpoint_service.ConversationCheckpointService.delete_checkpoints",
            side_effect=RuntimeError("db down"),
        ),
        patch(f"{_LG}.propagated_span", return_value=nullcontext()),
        patch(f"{_LG}.set_llm_context"),
        patch(f"{_LG}.set_logging_info"),
    ):
        agent.stream()

    agent._agent_streaming.assert_not_called()
    agent._send_error_to_thread.assert_called_once()
    assert generator.is_closed()


def test_a_tree_that_does_not_confirm_deletes_no_checkpoints_at_run_start():
    generator = ThreadedGenerator(conversation_id="conv-1")
    agent = _bare_agent(thread_generator=generator, require_tool_confirmation=False, tree_requires_confirmation=False)
    agent._agent_streaming = MagicMock(return_value="done")
    agent._send_error_to_thread = MagicMock()

    with (
        patch(
            "codemie.service.conversation_checkpoint_service.ConversationCheckpointService.delete_checkpoints"
        ) as delete_checkpoints,
        patch(f"{_LG}.propagated_span", return_value=nullcontext()),
        patch(f"{_LG}.set_llm_context"),
        patch(f"{_LG}.set_logging_info"),
    ):
        agent.stream()

    delete_checkpoints.assert_not_called()
    agent._agent_streaming.assert_called_once()


# --- CR-002: a plain agent's resumed allowed call re-opens its step ------------------------------------------


def test_a_plain_agents_tool_result_replays_the_resumed_tool_start():
    agent = MagicMock()
    adapter = LangGraphEventAdapter(agent)
    result = ToolMessage(content="ok", tool_call_id="call_1", name="write")

    adapter.parse_update_type({"tools": {"messages": [result]}})

    agent._replay_tool_start_on_resume.assert_called_once_with(result)
    agent._parse_tool_message.assert_called_once_with(result)


# --- CR-005: deferred steps are never overwritten or lost ----------------------------------------------------


def _router():
    agent = MagicMock()
    started: list[list[str]] = []
    router = ConfirmationStepRouter(
        agent,
        start_supervisor_steps=MagicMock(),
        start_tool_steps=lambda tool_calls, **kwargs: started.append([call["id"] for call in tool_calls]),
    )
    return router, started


def _ai(message_id: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="", id=message_id, tool_calls=[{"name": "w", "args": {}, "id": call_id, "type": "tool_call"}]
    )


def test_a_second_deferral_in_a_namespace_keeps_the_first():
    router, started = _router()
    router.defer((), _ai("m1", "c1"), None, "", supervisor=False)
    router.defer((), _ai("m2", "c2"), None, "", supervisor=False)

    router.consume_identity_chunk({"post_model_hook": None}, namespace_key="")

    assert started == [["c1"], ["c2"]]


def test_steps_still_deferred_when_the_stream_ends_are_started():
    router, started = _router()
    router.defer((), _ai("m1", "c1"), None, "", supervisor=False)

    router.flush_deferred()

    assert started == [["c1"]]
    router.flush_deferred()
    assert started == [["c1"]], "a flushed step is started once"


# --- CR-006 / CR-007: the gate's items --------------------------------------------------------------------


class _Host(ToolCallConfirmationMixin):
    def __init__(self, thread_generator):
        self.thread_generator = thread_generator
        self.conversation_id = "conv-1"
        self.assistant = MagicMock(id="root")
        self._pending_tool_confirmation = False

    @staticmethod
    def _tool_call_id_to_uuid(value: str):
        return f"uuid-{value}"


def _call(call_id: str, decision=None) -> GateCall:
    return GateCall(
        tool_call_id=call_id,
        location_key="",
        checkpoint_ns="agent:1|post_model_hook:1",
        event=ToolCallPendingEvent(pending_tool_call_id=call_id, tool_name="w", tool_args={}),
        decision=decision,
    )


def test_a_carried_deny_is_never_stored_as_an_allow():
    host = _Host(thread_generator=ThreadedGenerator(conversation_id="conv-1"))

    denied = host._decision_item(_call("c1", decision="deny"))
    allowed = host._decision_item(_call("c2", decision="allow"))
    undecided = host._decision_item(_call("c3"))

    assert (denied.auto_allowed, denied.auto_denied, denied.shown) == (False, True, False)
    assert (allowed.auto_allowed, allowed.shown) == (True, False)
    assert undecided.shown is True
    applied = applied_decisions([denied, allowed, undecided], {undecided.thought_id: "allow"})
    assert [decision.action for decision in applied] == ["deny", "allow", "allow"]


# --- CR-008: a nested child's later chunk merges into it -----------------------------------------------------


def _send(generator: ThreadedGenerator, **thought) -> None:
    generator.send(json.dumps({"thought": thought}))


def test_a_nested_childs_update_merges_into_the_child_at_its_depth():
    generator = ThreadedGenerator()
    _send(generator, id="parent", message="p", in_progress=True)
    _send(generator, id="child", parent_id="parent", message="a", in_progress=True)

    _send(
        generator,
        id="child",
        parent_id="parent",
        message="b",
        error=True,
        aborted=True,
        metadata={"k": "v"},
        output_format="markdown",
        in_progress=False,
        routing={"model": "x"},
    )

    assert [t["id"] for t in generator.thoughts] == ["parent"]
    child = generator.thoughts[0]["children"]
    assert len(child) == 1
    assert child[0]["message"] == "ab"
    assert (child[0]["error"], child[0]["aborted"], child[0]["in_progress"]) == (True, True, False)
    assert child[0]["metadata"] == {"k": "v"} and child[0]["output_format"] == "markdown"
    assert child[0]["routing"] == {"model": "x"}
