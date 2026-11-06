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

"""The one home of the step states a tool-confirmation pause leaves in saved thoughts (rule 7).

Each builder returns a new step dict with the state's fields and keeps every other field of the step.
"""

from __future__ import annotations

from typing import Any

WAITING_MESSAGE = "Tool call is waiting for user approval."
DENIED_MESSAGE = "Tool call was denied by the user."
CANCELLED_MESSAGE = "Tool call was cancelled without a decision."
UNAVAILABLE_MESSAGE = "Tool call needs user confirmation, which is not available in this channel. The tool was not run."


def waiting(step: dict[str, Any]) -> dict[str, Any]:
    return {**step, "interrupted": True, "in_progress": False, "message": WAITING_MESSAGE}


def allowed(step: dict[str, Any]) -> dict[str, Any]:
    """The waiting message is cleared; the normal tool run fills the step."""
    return {**step, "interrupted": False, "in_progress": True, "message": ""}


def _stopped(step: dict[str, Any], message: str) -> dict[str, Any]:
    return {**step, "interrupted": False, "aborted": True, "in_progress": False, "message": message}


def denied(step: dict[str, Any]) -> dict[str, Any]:
    return _stopped(step, DENIED_MESSAGE)


def cancelled(step: dict[str, Any]) -> dict[str, Any]:
    return _stopped(step, CANCELLED_MESSAGE)


def unavailable(step: dict[str, Any]) -> dict[str, Any]:
    """A call needing confirmation in a run that cannot be resumed: the gate refused it without a pause."""
    return _stopped(step, UNAVAILABLE_MESSAGE)


def cancel_waiting_steps(thoughts: list[dict[str, Any]]) -> bool:
    """Every step still waiting (``interrupted``), at any depth of the thought tree, becomes Cancelled.

    A step with a result is never waiting, so it is left as it is. Returns whether any step changed.
    """
    changed = False
    for thought in thoughts:
        if not isinstance(thought, dict):
            continue
        if thought.get("interrupted") is True:
            thought.update(cancelled(thought))
            changed = True
        children = thought.get("children")
        if isinstance(children, list):
            changed = cancel_waiting_steps(children) or changed
    return changed
