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

"""The applied decisions of a pause and what the resume derives from them (rules 1, 2, 4)."""

from __future__ import annotations

import pytest

from codemie.agents.tool_confirmation.call_identity import CallIdentity
from codemie.agents.tool_confirmation.confirmation_gate import build_decision_carrier
from codemie.agents.tool_confirmation.models import (
    AppliedDecision,
    DecisionItem,
    ToolCallPendingEvent,
    applied_decisions,
)
from codemie.agents.tool_confirmation.tool_call_confirmation_mixin import tool_pending_events


def _item(call_id: str, auto: bool = False) -> DecisionItem:
    return DecisionItem(
        thought_id=f"thought-{call_id}",
        tool_call_id=call_id,
        location_key="",
        checkpoint_ns="agent:1|post_model_hook:1",
        event=ToolCallPendingEvent(pending_tool_call_id=call_id, tool_name="write_tool", tool_args={"q": call_id}),
        auto_allowed=auto,
    )


def test_shown_items_take_the_requested_action_and_auto_allowed_ones_allow():
    items = [_item("a"), _item("b"), _item("c", auto=True)]

    applied = applied_decisions(items, {"thought-a": "deny", "thought-b": "allow"})

    assert [(a.item.tool_call_id, a.action) for a in applied] == [("a", "deny"), ("b", "allow"), ("c", "allow")]


def test_a_shown_item_missing_from_the_request_is_an_error_not_an_invented_decision():
    with pytest.raises(ValueError, match="thought-b"):
        applied_decisions([_item("a"), _item("b")], {"thought-a": "allow"})


def test_an_auto_allowed_item_ignores_a_request_entry():
    applied = applied_decisions([_item("c", auto=True)], {"thought-c": "deny"})

    assert [a.action for a in applied] == ["allow"]


def test_the_carrier_denies_exactly_the_deny_entries_and_auto_allowed_counts_as_allow():
    applied = applied_decisions(
        [_item("a"), _item("b"), _item("c", auto=True)], {"thought-a": "deny", "thought-b": "allow"}
    )

    carrier = build_decision_carrier(applied)

    key = lambda call_id: CallIdentity(wire_id=call_id, location_key="").carrier_key  # noqa: E731
    assert carrier == {
        key("a"): {"action": "deny", "ns": "agent:1|post_model_hook:1"},
        key("b"): {"action": "allow", "ns": "agent:1|post_model_hook:1"},
        key("c"): {"action": "allow", "ns": "agent:1|post_model_hook:1"},
    }


def test_tool_pending_events_hold_only_the_allowed_calls():
    applied = applied_decisions([_item("a"), _item("b")], {"thought-a": "deny", "thought-b": "allow"})

    events = tool_pending_events(applied)

    assert list(events) == [CallIdentity(wire_id="b", location_key="").step_key]
    assert events[CallIdentity(wire_id="b", location_key="").step_key].tool_args == {"q": "b"}


def test_applied_decision_is_a_frozen_pair():
    pair = AppliedDecision(item=_item("a"), action="allow")

    with pytest.raises(AttributeError):
        pair.action = "deny"  # type: ignore[misc]
