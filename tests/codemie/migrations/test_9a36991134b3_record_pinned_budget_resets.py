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

"""Unit tests for the 9a36991134b3 backfill: only a reset still set at its budget's creation is recorded.

That is the one case the existing rows answer exactly; recording a guess for any other row would make
its period worse than the provider's schedule it is otherwise derived from.
"""

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

CREATED = datetime(2026, 10, 9, 15, 5, 4, 121220, tzinfo=timezone.utc)


def _module():
    return importlib.import_module("external.alembic.versions.9a36991134b3_record_pinned_budget_resets")


class _Bind:
    """Stand-in for the migration connection: answers the SELECT and records the UPDATE."""

    def __init__(self, rows):
        self.rows = rows
        self.updates: list[list[dict]] = []

    def execute(self, statement, params=None):
        if params is None:
            return SimpleNamespace(all=lambda: self.rows)
        self.updates.append(params)
        return None


class _Op:
    def __init__(self, bind):
        self.bind = bind
        self.added: list[tuple[str, str]] = []

    def add_column(self, table, column):
        self.added.append((table, column.name))

    def get_bind(self):
        return self.bind


def _upgrade(monkeypatch, rows) -> _Op:
    module = _module()
    op = _Op(_Bind(rows))
    monkeypatch.setattr(module, "op", op)
    module.upgrade()
    return op


@pytest.mark.parametrize(
    ("duration", "reset_at", "expected"),
    [
        pytest.param("30d", "2026-11-09T00:00:00Z", True, id="EPMCDME-15204 reporter's budget"),
        pytest.param("1d", "2026-10-11T00:00:00Z", True, id="a day budget's first midnight after a day"),
        pytest.param("2m", "2026-10-10T00:00:00Z", True, id="a sub-day budget's first reset is a midnight too"),
        pytest.param("1d", "2026-10-12T00:00:00Z", False, id="the provider's next midnight is a day later"),
        pytest.param("2m", "2026-10-10T00:02:00Z", False, id="the provider's clock-aligned reset after it"),
        pytest.param("30d", "2026-11-01T00:00:00Z", False, id="the provider's schedule, before created + 30d"),
        pytest.param("30d", "2026-11-10T00:00:00Z", False, id="more than a day after created + 30d"),
    ],
)
def test_only_a_reset_set_at_creation_is_recognised(duration, reset_at, expected):
    module = _module()
    assert module._is_creation_reset(module._instant(reset_at), CREATED, module._duration(duration)) is expected


def test_a_budget_created_at_midnight_resets_exactly_one_duration_later():
    module = _module()
    created = datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert module._is_creation_reset(module._instant("2026-11-08T00:00:00Z"), created, module._duration("30d"))


def _row(budget_id, duration="30d", reset_at="2026-11-09T00:00:00Z", name=None, description=None):
    return (budget_id, name or f"Budget {budget_id}", description, duration, reset_at, CREATED)


def test_the_columns_are_added_and_only_creation_resets_are_recorded(monkeypatch):
    op = _upgrade(
        monkeypatch,
        [
            _row("pinned"),
            _row("scheduled", reset_at="2026-11-01T00:00:00Z"),
            _row("unreadable-duration", duration="1mo"),
            _row("zero-duration", duration="0d"),
            _row("huge-duration", duration="99999999999d"),
            _row("unreadable-reset", reset_at="next month"),
            # Imported from the provider: created elsewhere, `created_at` is only when it arrived.
            _row("synced", name="synced"),
            _row("backfilled", description="Imported from provider customer backfill"),
        ],
    )

    assert op.added == [("budgets", "pinned_reset_at"), ("budgets", "pinned_period_start")]
    assert op.bind.updates == [
        [
            {
                "budget_id": "pinned",
                "reset_at": datetime(2026, 11, 9, tzinfo=timezone.utc),
                # The provider's period the creation falls in, not the creation: the counter carries on.
                "period_start": datetime(2026, 10, 1, tzinfo=timezone.utc),
            }
        ]
    ]


@pytest.mark.parametrize(
    ("duration", "start"),
    [
        pytest.param("30d", datetime(2026, 10, 1, tzinfo=timezone.utc), id="the 1st"),
        pytest.param("7d", datetime(2026, 10, 5, tzinfo=timezone.utc), id="the Monday"),
        pytest.param("1d", datetime(2026, 10, 9, tzinfo=timezone.utc), id="the midnight"),
        pytest.param("2m", datetime(2026, 10, 9, 15, 4, tzinfo=timezone.utc), id="the clock-aligned boundary"),
    ],
)
def test_the_recorded_start_is_where_the_provider_s_schedule_starts_the_creation_s_period(duration, start):
    assert _module()._scheduled_period_start(CREATED, _module()._duration(duration)) == start


def test_a_budget_named_after_its_id_with_a_description_was_created_by_codemie(monkeypatch):
    op = _upgrade(monkeypatch, [_row("named-after-id", name="named-after-id", description="Team budget")])
    assert [pin["budget_id"] for pin in op.bind.updates[0]] == ["named-after-id"]


def test_nothing_to_record_issues_no_update(monkeypatch):
    op = _upgrade(monkeypatch, [_row("scheduled", reset_at="2026-11-01T00:00:00Z")])
    assert op.bind.updates == []
