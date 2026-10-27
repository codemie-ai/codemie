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

"""Publish and approve refuse a CRITICAL gate finding with one and the same body."""

from __future__ import annotations

from codemie.service.marketplace_management.gate_errors import critical_gate_blocked_error

_FINDINGS = [
    {"rule": "name_length", "severity": "CRITICAL", "message": "Name too short"},
    {"rule": "description_length", "severity": "OPTIONAL", "message": "Description short"},
]


def test_body_carries_the_failing_rules_and_every_finding() -> None:
    error = critical_gate_blocked_error("a-1", _FINDINGS, _FINDINGS[:1])

    assert error.code == 422
    assert "name_length: Name too short" in error.message
    assert error.details == {
        "assistant_id": "a-1",
        "requires_confirmation": False,
        "failed_rules": ["name_length"],
        "gate_findings": _FINDINGS,
    }


def test_only_the_help_text_may_differ_between_callers() -> None:
    publish = critical_gate_blocked_error("a-1", _FINDINGS, _FINDINGS[:1])
    approve = critical_gate_blocked_error("a-1", _FINDINGS, _FINDINGS[:1], help="use force-publish")

    assert publish.help != approve.help
    assert (publish.code, publish.message, publish.details) == (approve.code, approve.message, approve.details)
