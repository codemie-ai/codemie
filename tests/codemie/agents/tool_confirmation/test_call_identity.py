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

"""The single call-identity function: unique, provider-safe, deterministic wire ids."""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from codemie.agents.tool_confirmation.call_identity import (
    CallIdentity,
    assign_call_identities,
    location_key_from_namespace,
)
from codemie.agents.tool_confirmation.models import (
    DecisionItem,
    PendingCallLocation,
    StoredPendingToolCall,
    ToolCallPendingEvent,
)
from codemie.rest_api.models.assistant import ToolCallResumeRequest

WIRE_ID = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")


def _ai(*ids: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "t", "args": {}, "id": tool_call_id, "type": "tool_call"} for tool_call_id in ids],
    )


def _wire_ids(identities: list[CallIdentity]) -> list[str]:
    return [identity.wire_id for identity in identities]


def test_repeated_ids_in_one_message_get_unique_provider_safe_wire_ids():
    ai = _ai("call_0", "call_0", "call_0")

    identities = assign_call_identities([HumanMessage("hi"), ai], ai, "")

    wire_ids = _wire_ids(identities)
    assert wire_ids[0] == "call_0"
    assert len(set(wire_ids)) == 3
    assert all(WIRE_ID.match(wire_id) for wire_id in wire_ids)


def test_answered_id_is_rewritten_and_later_rounds_do_not_collide():
    round_1 = _ai("call_0")
    round_2 = _ai("call_0")
    messages = [HumanMessage("hi"), round_1, ToolMessage(content="ok", tool_call_id="call_0")]
    rewritten_2 = assign_call_identities([*messages, round_2], round_2, "")[0].wire_id
    messages += [_ai(rewritten_2), ToolMessage(content="ok", tool_call_id=rewritten_2)]
    round_3 = _ai("call_0")

    rewritten_3 = assign_call_identities([*messages, round_3], round_3, "")[0].wire_id

    assert rewritten_2 != "call_0"
    assert rewritten_3 not in {"call_0", rewritten_2}
    assert WIRE_ID.match(rewritten_3)


def test_non_repeated_root_id_is_unchanged():
    ai = _ai("toolu_01ABC", "toolu_02DEF")

    identities = assign_call_identities([HumanMessage("hi"), ai], ai, "")

    assert identities == [CallIdentity("toolu_01ABC", ""), CallIdentity("toolu_02DEF", "")]


def test_rewrite_is_deterministic_and_handles_empty_and_unsafe_ids():
    ai = _ai("", "", "a.very/long:id with spaces and more than forty characters total")

    first = _wire_ids(assign_call_identities([ai], ai, "k"))
    second = _wire_ids(assign_call_identities([ai], ai, "k"))

    assert first == second
    assert len(set(first)) == 3
    assert all(WIRE_ID.match(wire_id) for wire_id in first)


def test_already_rewritten_message_is_left_as_is():
    ai = _ai("call_0", "call_0")
    wire_ids = _wire_ids(assign_call_identities([ai], ai, ""))
    rewritten = _ai(*wire_ids)

    assert _wire_ids(assign_call_identities([rewritten], rewritten, "")) == wire_ids


def test_location_key_is_carried_beside_the_wire_id():
    ai = _ai("call_0")

    identity = assign_call_identities([ai], ai, "agent_s:abcd1234")[0]

    assert identity == CallIdentity("call_0", "agent_s:abcd1234")
    assert identity.carrier_key == "agent_s:abcd1234/call_0"


def test_location_key_drops_post_model_hook_and_hashes_task_ids():
    key = location_key_from_namespace("supervisor:task-a|agent_s:task-b|post_model_hook:task-c")

    assert "post_model_hook" not in key
    assert "task-b" not in key
    assert key.startswith("supervisor:")
    assert "|agent_s:" in key
    assert key == location_key_from_namespace("supervisor:task-a|agent_s:task-b|post_model_hook:task-other")


def test_location_key_differs_for_parallel_instances_of_one_agent():
    one = location_key_from_namespace("supervisor:t0|agent_s:instance-1|post_model_hook:x")
    two = location_key_from_namespace("supervisor:t0|agent_s:instance-2|post_model_hook:y")

    assert one != two


def test_root_namespace_gives_empty_location_key():
    assert location_key_from_namespace("") == ""
    assert location_key_from_namespace("post_model_hook:abc") == ""


def test_pending_event_and_resume_request_stay_backward_compatible():
    event = ToolCallPendingEvent(pending_tool_call_id="call_0", tool_name="t", tool_args={})
    assert event.location is None
    assert "location" in event.model_dump()
    assert ToolCallPendingEvent.model_validate({"pending_tool_call_id": "c", "tool_name": "t", "tool_args": {}})
    assert ToolCallResumeRequest(conversation_id="c", action="allow").thought_id is None


def test_stored_pending_tool_call_round_trips():
    location = PendingCallLocation(parent_thought_id="th-1")
    event = ToolCallPendingEvent(pending_tool_call_id="call_0", tool_name="t", tool_args={}, location=location)
    item = DecisionItem(
        thought_id="call_0",
        tool_call_id="call_0",
        location_key="s:abc",
        checkpoint_ns="s:1|post_model_hook:2",
        event=event,
    )
    record = StoredPendingToolCall(
        items=[item],
        root_assistant_id="a-1",
        history_index=3,
        original_user_message="hi",
        thoughts=[{"id": "call_0", "interrupted": True}],
        payload={"carried_ids": []},
    )

    restored = StoredPendingToolCall.model_validate(record.model_dump(mode="json"))

    assert restored == record
    assert (restored.history_index, restored.original_user_message) == (3, "hi")
    assert not hasattr(restored, "run_id") and not hasattr(restored, "saved")
    assert not hasattr(restored, "format") and not hasattr(restored.items[0], "decision")


def test_a_row_stored_with_the_removed_fields_still_parses():
    """Rows written before Task 27 carry ``producer`` and the location's agent path; they are ignored."""
    stored = {
        "producer": "langgraph",
        "items": [
            {
                "thought_id": "t",
                "tool_call_id": "call_0",
                "location_key": "s",
                "checkpoint_ns": "s:1|post_model_hook:1",
                "producer": "langgraph",
                "event": {
                    "pending_tool_call_id": "call_0",
                    "tool_name": "write",
                    "tool_args": {},
                    "location": {"agent_path": ["root", "s"], "agent_names": ["Root", "S"], "parent_thought_id": "p"},
                },
            }
        ],
        "root_assistant_id": "a-1",
        "history_index": 3,
        "original_user_message": "hi",
        "thoughts": [],
        "payload": {"interrupt_ids": ["i"], "carried_ids": []},
    }

    record = StoredPendingToolCall.model_validate(stored)

    assert record.items[0].event.location.parent_thought_id == "p"
    assert record.shown_items()[0].thought_id == "t"
