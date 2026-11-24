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

"""Tests for HeadroomMonitoringService (headroom_compaction_usage)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.headroom_savings import HeadroomSavings
from codemie.rest_api.security.user import User
from codemie.service.analytics.metric_names import MetricName
from codemie.service.monitoring.headroom_monitoring_service import HeadroomMonitoringService


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user-123"
    user.email = "test@example.com"
    return user


def _savings(original=1000, compacted=400) -> HeadroomSavings:
    return HeadroomSavings(
        tokens_original=original,
        tokens_compacted=compacted,
        tokens_saved=original - compacted,
        mode="sidecar",
        strategy="balanced",
        calls=2,
    )


def _send(user, savings):
    HeadroomMonitoringService.send_compaction_metric(
        user=user, savings=savings, conversation_id="c1", assistant_id="a1", project="p1", request_id="r1"
    )


def test_metric_name():
    assert MetricName.HEADROOM_COMPACTION_USAGE.value == "headroom_compaction_usage"


@pytest.mark.parametrize("savings", [None, _savings(100, 100), _savings(100, 150)])
def test_noop_when_nothing_was_saved(mock_user, savings):
    with patch.object(HeadroomMonitoringService, "send_count_metric") as send:
        _send(mock_user, savings)

    send.assert_not_called()


def test_emits_one_event_with_all_fields_when_saved(mock_user):
    with patch.object(HeadroomMonitoringService, "send_count_metric") as send:
        _send(mock_user, _savings())

    send.assert_called_once()
    assert send.call_args.kwargs["name"] == "headroom_compaction_usage"
    attributes = send.call_args.kwargs["attributes"]
    assert attributes == {
        "user_id": "user-123",
        "user_email": "test@example.com",
        "project": "p1",
        "conversation_id": "c1",
        "assistant_id": "a1",
        "request_id": "r1",
        "tokens_original": 1000,
        "tokens_compacted": 400,
        "tokens_saved": 600,
        "mode": "sidecar",
        "strategy": "balanced",
        "calls": 2,
    }
    assert all(type(attributes[key]) is int for key in ("tokens_original", "tokens_compacted", "tokens_saved"))
