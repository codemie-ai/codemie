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

from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import BaseModel


class PendingCallLocation(BaseModel):
    """Where a waiting call of a sub-assistant sits: the step of the delegation into it."""

    parent_thought_id: str


class ToolCallPendingEvent(BaseModel):
    pending_tool_call_id: str
    tool_name: str
    tool_args: dict
    display_name: Optional[str] = None
    history_index: Optional[int] = None
    original_user_message: str = ""
    all_tool_call_ids: list[str] = []
    location: PendingCallLocation | None = None


class DecisionItem(BaseModel):
    """One call of a pause."""

    thought_id: str
    tool_call_id: str
    location_key: str
    checkpoint_ns: str
    event: ToolCallPendingEvent
    # Allowed by the policy when the pause was written (approve-for-me on a safe call): never shown to the user.
    auto_allowed: bool = False
    # Denied by a decision the gate carried from an earlier pause: never shown, and resumed as a deny.
    auto_denied: bool = False

    @property
    def shown(self) -> bool:
        """The user decides this call; an auto-allowed or auto-denied one is never shown."""
        return not (self.auto_allowed or self.auto_denied)


class StoredPendingToolCall(BaseModel):
    """The pending row of a pause.

    ``thoughts`` is the snapshot of the generator's thoughts, with every shown call's waiting step.
    ``payload`` carries ``carried_ids``: the allowed steps of an earlier pause still running at this one.
    """

    items: list[DecisionItem]
    root_assistant_id: str
    history_index: int | None = None
    original_user_message: str
    thoughts: list[dict[str, Any]]
    payload: dict[str, Any]

    def shown_items(self) -> list[DecisionItem]:
        return [item for item in self.items if item.shown]


@dataclass(frozen=True)
class AppliedDecision:
    """One call of a pause with the action the resume applies to it (an auto-allowed call counts as ``allow``)."""

    item: DecisionItem
    action: Literal["allow", "deny"]


def applied_decisions(items: list[DecisionItem], requested: dict[str, str]) -> list[AppliedDecision]:
    """Pair every item of a pause with its action.

    ``requested`` maps a shown item's ``thought_id`` to ``allow`` or ``deny``; an auto-allowed item is not in it
    and allows. The set of ids is the caller's to validate; a shown item without an entry is never given a
    decision here.

    Raises:
        ValueError: a shown item has no entry in ``requested``
    """
    applied: list[AppliedDecision] = []
    for item in items:
        if item.auto_allowed:
            applied.append(AppliedDecision(item, "allow"))
            continue
        if item.auto_denied:
            applied.append(AppliedDecision(item, "deny"))
            continue
        if item.thought_id not in requested:
            raise ValueError(f"No decision requested for the shown call {item.thought_id}.")
        applied.append(AppliedDecision(item, "allow" if requested[item.thought_id] == "allow" else "deny"))
    return applied
