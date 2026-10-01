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

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from codemie.repository.cli_analytics.hook_events import safe_str, timestamp_to_ns

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _ns(dt: datetime) -> int:
    delta = dt - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("text", "text"),
        (5, "5"),
        (True, "True"),
        ({"k": "é"}, '{"k": "é"}'),
        (["a", 1], '["a", 1]'),
    ],
)
def test_safe_str_renders_values_like_the_ingest_router(value, expected):
    assert safe_str(value) == expected


def test_timestamp_to_ns_parses_a_z_suffixed_timestamp_with_microseconds():
    event = {"timestamp": "2026-09-23T10:00:00.123456Z"}

    assert timestamp_to_ns(event, now_ns=0) == _ns(datetime(2026, 9, 23, 10, 0, 0, 123456, tzinfo=timezone.utc))


def test_timestamp_to_ns_honours_an_explicit_offset():
    event = {"timestamp": "2026-09-23T12:00:00+02:00"}

    assert timestamp_to_ns(event, now_ns=0) == _ns(datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc))


def test_timestamp_to_ns_treats_a_naive_timestamp_as_utc():
    # The router's helper raised TypeError (aware minus naive) for such events, which
    # failed the whole batch with a 500 that the plugin retries forever.
    event = {"timestamp": "2026-09-23T10:00:00"}

    assert timestamp_to_ns(event, now_ns=0) == _ns(datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc))


@pytest.mark.parametrize("event", [{}, {"timestamp": ""}, {"timestamp": None}, {"timestamp": "yesterday"}])
def test_timestamp_to_ns_falls_back_to_receive_time(event):
    assert timestamp_to_ns(event, now_ns=42) == 42


@pytest.mark.parametrize("timestamp", ["9999-12-31T23:59:59-01:00", "0001-01-01T00:00:00+01:00"])
def test_timestamp_to_ns_falls_back_to_now_outside_the_representable_range(timestamp):
    # Year 10000 or year 0 in UTC: no datetime can hold it, so decoding would fail the batch.
    assert timestamp_to_ns({"timestamp": timestamp}, now_ns=42) == 42
