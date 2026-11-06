# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codemie.rest_api.models.assistant import ToolCallDecision, ToolCallResumeRequest


_DECISIONS = [ToolCallDecision(thought_id="t1", action="allow")]


@pytest.mark.parametrize(
    "fields",
    [
        {"decisions": _DECISIONS},
        {"action": "allow"},
        {"action": "deny", "thought_id": "t1"},
    ],
    ids=["decisions", "action", "action-with-thought-id"],
)
def test_a_valid_body_has_no_shape_error(fields):
    request = ToolCallResumeRequest(conversation_id="c1", **fields)

    assert request.shape_error() is None


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"thought_id": "t1"},
        {"decisions": _DECISIONS, "action": "allow"},
        {"decisions": _DECISIONS, "thought_id": "t1"},
    ],
    ids=["neither", "thought-id-alone", "decisions-and-action", "thought-id-next-to-decisions"],
)
def test_a_malformed_body_parses_and_reports_its_shape_error(fields):
    request = ToolCallResumeRequest(conversation_id="c1", **fields)

    assert request.shape_error()


def test_decision_action_must_be_allow_or_deny():
    with pytest.raises(ValidationError):
        ToolCallDecision(thought_id="t1", action="maybe")


def test_action_must_be_allow_or_deny():
    with pytest.raises(ValidationError):
        ToolCallResumeRequest(conversation_id="c1", action="maybe")


def test_other_fields_keep_their_defaults():
    request = ToolCallResumeRequest(conversation_id="c1", action="allow")
    assert request.stream is True
