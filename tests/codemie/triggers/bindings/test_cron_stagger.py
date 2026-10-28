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

"""Tests for the deterministic per-datasource cron stagger (EPMCDME-16236)."""

from codemie.triggers.bindings.cron_stagger import stagger_cron


def _minute_hour(cron_expression: str) -> tuple[str, str]:
    minute, hour, *_ = cron_expression.split()
    return minute, hour


def test_deterministic_per_id():
    a = stagger_cron("ds-1", "0 0 * * *")
    b = stagger_cron("ds-1", "0 0 * * *")
    assert a == b


def test_distinct_across_ids():
    results = {stagger_cron(f"ds-{i}", "0 0 * * *") for i in range(50)}
    assert len(results) > 1


def test_day_wrap_stays_within_day():
    minute, hour = _minute_hour(stagger_cron("ds-x", "0 0 * * *"))
    assert 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59


def test_wildcard_safety_rule_passes_through_non_wildcard():
    assert stagger_cron("ds-1", "0 0 15 * *") == "0 0 15 * *"
    assert stagger_cron("ds-1", "0 0 * * 1-5") == "0 0 * * 1-5"


def test_non_integer_fields_pass_through():
    assert stagger_cron("ds-1", "*/5 0 * * *") == "*/5 0 * * *"


def test_missing_datasource_id_passes_through():
    assert stagger_cron(None, "0 0 * * *") == "0 0 * * *"
    assert stagger_cron("", "0 0 * * *") == "0 0 * * *"


def test_hourly_wildcard_hour_stays_wildcard_but_minute_staggers():
    minute, hour = _minute_hour(stagger_cron("ds-1", "15 * * * *"))
    assert hour == "*"
    assert 0 <= int(minute) <= 59


def test_hourly_wildcard_hour_is_deterministic_per_id():
    a = stagger_cron("ds-1", "15 * * * *")
    b = stagger_cron("ds-1", "15 * * * *")
    assert a == b


def test_hourly_wildcard_hour_distinct_across_ids():
    results = {stagger_cron(f"ds-{i}", "15 * * * *") for i in range(50)}
    assert len(results) > 1
    assert all(_minute_hour(r)[1] == "*" for r in results)


def test_hourly_wildcard_hour_minute_wraps_mod_60():
    for i in range(50):
        minute, hour = _minute_hour(stagger_cron(f"ds-{i}", "45 * * * *"))
        assert hour == "*"
        assert 0 <= int(minute) <= 59
