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

"""Characterization tests for the project spending helpers: access, reset text, selection, aggregation, widget, IO."""

import inspect
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from codemie.core.models import Application
from codemie.repository.project_spend_tracking_repository import (
    ProjectSpendTrackingRepository,
    project_spend_tracking_repository,
)
from codemie.rest_api.models.project import ProjectDetailResponse
from codemie.service.project import project_spending_service

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
# Non-zero days, hours and remainder: every divisor and floor in the reset text is observable with this offset.
TWO_DAYS_FIVE_HOURS = timedelta(days=2, hours=5, minutes=30)
FIVE_HOURS = timedelta(hours=5, minutes=30)
LATER = datetime(2026, 3, 9, 8, 0, tzinfo=timezone.utc)
EARLIER = datetime(2026, 3, 8, 8, 0, tzinfo=timezone.utc)
PERSONAL = Application.ProjectType.PERSONAL

WIDGET_COLUMNS = [
    {"id": "budget_id", "label": "Budget", "type": "string", "format": None, "description": ""},
    {
        "id": "current_spending",
        "label": "Budget Period Spend ($)",
        "type": "number",
        "format": "currency",
        "description": "Total amount spent in current budget period",
    },
    {
        "id": "budget_reset_at",
        "label": "Budget Reset Date",
        "type": "string",
        "format": "timestamp",
        "description": "Timestamp when budget will reset",
    },
    {"id": "time_until_reset", "label": "Time Until Reset", "type": "string", "format": None, "description": ""},
    {
        "id": "budget_limit",
        "label": "Budget Limit ($)",
        "type": "number",
        "format": "currency",
        "description": "Soft budget limit (warning threshold)",
    },
    {"id": "total", "label": "Total", "type": "number", "format": "percentage", "description": ""},
]


# --- builders -------------------------------------------------------------------------------------------------------


def _iso(offset: timedelta) -> str:
    return (NOW + offset).isoformat()


def _budget(max_budget: float, reset_offset: timedelta | None = None, *, deleted: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        max_budget=max_budget,
        budget_reset_at=None if reset_offset is None else _iso(reset_offset),
        deleted_at=NOW if deleted else None,
    )


def _assigned(budget_id: str, category: str, max_budget: float, reset_offset: timedelta | None = None):
    return SimpleNamespace(
        budget_id=budget_id,
        budget_category=category,
        max_budget=max_budget,
        budget_reset_at=None if reset_offset is None else _iso(reset_offset),
    )


def _row(project: str, budget_id: str | None, spend: str, *, category: str | None = None, date: datetime = LATER):
    return SimpleNamespace(
        project_name=project,
        budget_id=budget_id,
        budget_category=category,
        budget_period_spend=Decimal(spend),
        spend_date=date,
    )


def _widget_row(**fields) -> dict:
    return {"budget_category": None, "is_assigned": True, **fields}


def _response() -> ProjectDetailResponse:
    return ProjectDetailResponse(name="p1", project_type="shared", user_count=1, admin_count=1, members=[])


def _same_objects(actual: list, expected: list) -> bool:
    return len(actual) == len(expected) and all(left is right for left, right in zip(actual, expected, strict=True))


def _same_pairs(actual: list[tuple], expected: list[tuple]) -> bool:
    return len(actual) == len(expected) and all(
        left_row is right_row and left_assigned is right_assigned
        for (left_row, left_assigned), (right_row, right_assigned) in zip(actual, expected, strict=True)
    )


# --- fixtures -------------------------------------------------------------------------------------------------------


@pytest.fixture
def events() -> list[str]:
    return []


@pytest.fixture(autouse=True)
def frozen_clock(events):
    """The module's ``datetime`` is a subclass whose ``now`` is NOW and records each read in ``events``."""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            events.append("now")
            return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)

    with patch.object(project_spending_service, "datetime", FrozenDatetime):
        yield


@pytest.fixture
def repos(events):
    """Doubles for everything the two IO functions read; the session double records enter and exit in ``events``."""
    session = MagicMock(name="session")

    @asynccontextmanager
    async def session_scope():
        events.append("enter")
        try:
            yield session
        finally:
            events.append("exit")

    doubles = SimpleNamespace(
        session=session,
        get_async_session=MagicMock(side_effect=session_scope),
        spend=SimpleNamespace(
            get_latest_spending_by_project=AsyncMock(return_value=[]),
            get_latest_project_budget_rows_by_category=AsyncMock(return_value=[]),
            get_latest_key_spending_for_project=AsyncMock(return_value=None),
            get_latest_budget_rows_for_project=AsyncMock(return_value=[]),
            get_lifetime_spend=AsyncMock(return_value=0.0),
        ),
        budget_repository=SimpleNamespace(get_all_keyed_by_id=AsyncMock(return_value={})),
        assignment_repository=SimpleNamespace(get_assigned_budget_summaries_for_projects=AsyncMock(return_value={})),
    )
    with (
        patch.object(project_spending_service, "get_async_session", doubles.get_async_session),
        patch.object(project_spending_service, "spend_repo", doubles.spend),
        patch.object(project_spending_service, "budget_repository", doubles.budget_repository),
        patch.object(project_spending_service, "project_budget_assignment_repository", doubles.assignment_repository),
    ):
        yield doubles


# --- access control -------------------------------------------------------------------------------------------------

ORDINARY = SimpleNamespace(is_admin=False, is_auditor=False)
WITHOUT_AUDITOR_FLAG = SimpleNamespace(is_admin=False)
PROJECT_LIST = [
    {"name": "zeta-shared", "project_type": "shared"},
    {"name": "mid-admin", "project_type": "shared", "is_project_admin": True},
    {"name": "alpha-personal", "project_type": "personal"},
    {"name": "beta-personal-enum", "project_type": PERSONAL},
    {"name": "omega-flag-off", "project_type": "shared", "is_project_admin": False},
]
ALL_NAMES = ["zeta-shared", "mid-admin", "alpha-personal", "beta-personal-enum", "omega-flag-off"]
OWN_NAMES = ["mid-admin", "alpha-personal", "beta-personal-enum"]


class TestAccessControl:
    @pytest.mark.parametrize(
        ("user", "project_detail", "visible"),
        [
            pytest.param(SimpleNamespace(is_admin=True), {"project_type": "shared"}, True, id="admin"),
            pytest.param(
                SimpleNamespace(is_admin=False, is_auditor=True), {"project_type": "shared"}, True, id="auditor"
            ),
            pytest.param(ORDINARY, {"project_type": "shared", "is_project_admin": 1}, True, id="project-admin-flag"),
            pytest.param(ORDINARY, {"project_type": "personal"}, True, id="personal-string"),
            pytest.param(ORDINARY, {"project_type": PERSONAL}, True, id="personal-enum"),
            pytest.param(ORDINARY, {"project_type": "shared"}, False, id="other-user-shared-project"),
            pytest.param(WITHOUT_AUDITOR_FLAG, {"project_type": "shared"}, False, id="no-auditor-attribute"),
            pytest.param(ORDINARY, {}, False, id="empty-detail"),
        ],
    )
    def test_can_see_project_spending_returns_a_real_bool(self, user, project_detail, visible):
        assert project_spending_service.can_see_project_spending(user, project_detail) is visible

    @pytest.mark.parametrize(
        ("user", "expected"),
        [
            pytest.param(SimpleNamespace(is_admin=True), ALL_NAMES, id="admin"),
            pytest.param(SimpleNamespace(is_admin=False, is_auditor=True), ALL_NAMES, id="auditor"),
            pytest.param(ORDINARY, OWN_NAMES, id="other-user"),
            pytest.param(WITHOUT_AUDITOR_FLAG, OWN_NAMES, id="no-auditor-attribute"),
        ],
    )
    def test_manageable_project_names_keeps_input_order_per_user_kind(self, user, expected):
        assert project_spending_service.manageable_project_names(PROJECT_LIST, user) == expected

    def test_manageable_project_names_is_empty_for_an_empty_list(self):
        assert project_spending_service.manageable_project_names([], ORDINARY) == []


# --- reset text and parsing -----------------------------------------------------------------------------------------


class TestFormatTimeUntilReset:
    def test_none_gives_none(self):
        assert project_spending_service.format_time_until_reset(None) is None

    @pytest.mark.parametrize(
        ("offset", "naive", "expected"),
        [
            pytest.param(TWO_DAYS_FIVE_HOURS, True, "2 days", id="naive-datetime-is-read-as-utc"),
            pytest.param(TWO_DAYS_FIVE_HOURS, False, "2 days", id="aware-two-days"),
            pytest.param(timedelta(days=1, hours=2), False, "1 day", id="one-day-is-singular"),
            pytest.param(FIVE_HOURS, False, "5 hours", id="hours-under-a-day"),
            pytest.param(timedelta(hours=1), False, "1 hour", id="one-hour-is-singular"),
            pytest.param(timedelta(minutes=30), False, "0 hours", id="under-an-hour"),
            pytest.param(timedelta(0), False, "0 days", id="exactly-now"),
            pytest.param(timedelta(hours=-1), False, "0 days", id="in-the-past"),
        ],
    )
    def test_text(self, offset, naive, expected):
        reset_at = (NOW + offset).replace(tzinfo=None) if naive else NOW + offset

        assert project_spending_service.format_time_until_reset(reset_at) == expected


class TestParseBudgetResetAt:
    @pytest.mark.parametrize("value", [None, ""])
    def test_missing_value_gives_none(self, value):
        assert project_spending_service.parse_budget_reset_at(value) is None

    def test_z_suffix_reads_as_utc_offset(self):
        parsed = project_spending_service.parse_budget_reset_at("2026-03-15T12:00:00Z")

        assert parsed == project_spending_service.parse_budget_reset_at("2026-03-15T12:00:00+00:00")
        assert parsed == datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)

    def test_malformed_value_raises_value_error_pinned_quirk(self):
        with pytest.raises(ValueError):
            project_spending_service.parse_budget_reset_at("not-a-date")


class TestIsBudgetPeriodOpen:
    @pytest.mark.parametrize(
        ("budget", "expected"),
        [
            pytest.param(None, False, id="no-budget"),
            pytest.param(SimpleNamespace(budget_reset_at=None), False, id="no-reset"),
            pytest.param(SimpleNamespace(budget_reset_at=""), False, id="empty-reset"),
            pytest.param(SimpleNamespace(budget_reset_at=_iso(timedelta(0))), False, id="reset-is-now"),
            pytest.param(
                SimpleNamespace(budget_reset_at=_iso(timedelta(seconds=1))), True, id="reset-one-second-later"
            ),
            pytest.param(SimpleNamespace(budget_reset_at=_iso(timedelta(days=-1))), False, id="reset-in-the-past"),
            pytest.param(SimpleNamespace(budget_reset_at="2026-03-10T12:00:01Z"), True, id="z-suffix"),
            pytest.param(
                SimpleNamespace(budget_reset_at=(NOW + timedelta(hours=1)).replace(tzinfo=None).isoformat()),
                True,
                id="naive-future-is-utc",
            ),
            pytest.param(
                SimpleNamespace(budget_reset_at=(NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat()),
                False,
                id="naive-past-is-utc",
            ),
        ],
    )
    def test_open_only_for_a_reset_strictly_after_now(self, budget, expected):
        assert project_spending_service.is_budget_period_open(budget, NOW) is expected


class TestBudgetInfo:
    def test_missing_id_gives_no_info(self):
        assert project_spending_service.budget_info({"b1": _budget(3.0)}, None) == (None, None)

    def test_unknown_id_gives_no_info(self):
        assert project_spending_service.budget_info({"b1": _budget(3.0)}, "other") == (None, None)

    def test_known_id_gives_limit_and_parsed_reset(self):
        info = project_spending_service.budget_info({"b1": _budget(7.5, timedelta(days=3))}, "b1")

        assert info == (7.5, NOW + timedelta(days=3))


# --- selection and aggregation --------------------------------------------------------------------------------------


class TestSelectCategoryRows:
    def _select(self, rows, assigned, budgets):
        return project_spending_service.select_category_rows(rows, assigned, budgets, NOW)

    def test_each_branch_keeps_or_drops_its_row(self):
        cli, premium = _assigned("b-cli", "cli", 5.0), _assigned("b-premium", "premium", 8.0)
        budgets = {
            "b-cli": _budget(5.0, timedelta(days=-1)),
            "b-old-open": _budget(1.0, timedelta(days=2)),
            "b-old-closed": _budget(1.0, timedelta(days=-2)),
            "b-premium": _budget(8.0, timedelta(days=-3)),
            "b-free-open": _budget(1.0, timedelta(days=4)),
            "b-free-closed": _budget(1.0, timedelta(days=-4)),
        }
        same_budget_closed = _row("p", "b-cli", "2.0", category="cli")
        other_budget_open = _row("p", "b-old-open", "2.0", category="cli")
        other_budget_closed = _row("p", "b-old-closed", "2.0", category="cli")
        second_category = _row("p", "b-premium", "3.0", category="premium")
        unassigned_open = _row("p", "b-free-open", "4.0", category="free")
        unassigned_open_no_spend = _row("p", "b-free-open", "0", category="free")
        unassigned_closed = _row("p", "b-free-closed", "4.0", category="free")
        unassigned_unknown_budget = _row("p", "b-gone", "4.0", category="free")
        rows = [
            same_budget_closed,
            other_budget_open,
            other_budget_closed,
            second_category,
            unassigned_open,
            unassigned_open_no_spend,
            unassigned_closed,
            unassigned_unknown_budget,
        ]

        selected = self._select(rows, [cli, premium], budgets)

        assert _same_pairs(
            selected,
            [
                (same_budget_closed, cli),
                (other_budget_open, cli),
                (second_category, premium),
                (unassigned_open, None),
            ],
        )

    def test_no_rows_select_nothing(self):
        assert self._select([], [_assigned("b", "cli", 1.0)], {}) == []


class TestAggregateBudgetWindow:
    def test_assigned_budgets_sum_their_limits_and_take_the_earliest_reset(self):
        assigned = [
            _assigned("a", "cli", 10.5, timedelta(days=5)),
            _assigned("b", "premium", 4.25, timedelta(days=2)),
            _assigned("c", "platform", 1.0),
        ]

        window = project_spending_service.aggregate_budget_window(assigned, [], {})

        assert window == (15.75, NOW + timedelta(days=2))

    def test_assigned_budgets_without_any_reset_give_no_reset(self):
        window = project_spending_service.aggregate_budget_window([_assigned("a", "cli", 1.0)], [], {})

        assert window == (1.0, None)

    def test_without_assignments_each_resolvable_budget_counts_once(self):
        budgets = {
            "b1": _budget(3.0, timedelta(days=4)),
            "b2": _budget(2.5, timedelta(days=3)),
            "gone": _budget(9.0, timedelta(days=1), deleted=True),
            "": _budget(99.0, timedelta(hours=1)),
        }
        rows = [
            None,
            SimpleNamespace(budget_id=""),
            SimpleNamespace(budget_id="b1"),
            SimpleNamespace(budget_id="b1"),
            SimpleNamespace(budget_id="missing"),
            SimpleNamespace(budget_id="gone"),
            SimpleNamespace(budget_id="b2"),
        ]

        window = project_spending_service.aggregate_budget_window([], rows, budgets)

        assert window == (5.5, NOW + timedelta(days=3))

    def test_without_assignments_a_budget_without_reset_gives_no_reset(self):
        rows = [SimpleNamespace(budget_id="b1")]

        window = project_spending_service.aggregate_budget_window([], rows, {"b1": _budget(3.0)})

        assert window == (3.0, None)

    def test_nothing_resolving_gives_no_window(self):
        rows = [None, SimpleNamespace(budget_id="missing"), SimpleNamespace(budget_id="gone")]
        budgets = {"gone": _budget(9.0, timedelta(days=1), deleted=True)}

        assert project_spending_service.aggregate_budget_window([], rows, budgets) == (None, None)


# --- widget rows ----------------------------------------------------------------------------------------------------


class TestBuildWidgetRows:
    BUDGETS = {"b1": _budget(3.0, TWO_DAYS_FIVE_HOURS), "zero": _budget(0.0, timedelta(days=1))}

    def _build(self, rows, budgets=BUDGETS):
        built = project_spending_service.build_widget_rows(rows, budgets_by_id=budgets)
        return [row.model_dump() for row in built]

    def test_row_with_a_budget_carries_limit_percent_and_reset(self):
        assert self._build([_row("p1", "b1", "1.0")]) == [
            _widget_row(
                budget_id="b1",
                current_spending=1.0,
                budget_reset_at=NOW + TWO_DAYS_FIVE_HOURS,
                time_until_reset="2 days",
                budget_limit=3.0,
                total=33.33,
            )
        ]

    def test_zero_limit_gives_zero_percent(self):
        built = self._build([_row("p1", "zero", "1.0")])

        assert (built[0]["budget_limit"], built[0]["total"]) == (0.0, 0.0)

    def test_row_without_budget_info_has_no_limit_and_no_reset(self):
        built = self._build([_row("p1", "unknown", "2.5")])

        assert built == [
            _widget_row(
                budget_id="unknown",
                current_spending=2.5,
                budget_reset_at=None,
                time_until_reset=None,
                budget_limit=None,
                total=0.0,
            )
        ]

    def test_empty_budget_id_is_called_default(self):
        built = self._build([_row("p1", "", "1.0")])

        assert built[0]["budget_id"] == "Default"

    def test_budgets_default_to_none_and_rows_keep_their_order(self):
        rows = [_row("p1", "b2", "1.0"), _row("p1", "b1", "2.0")]

        built = project_spending_service.build_widget_rows(rows)

        assert [(row.budget_id, row.budget_limit) for row in built] == [("b2", None), ("b1", None)]


class TestBuildCategoryWidgetRows:
    def test_assigned_rows_use_the_assignment(self):
        assigned = _assigned("b-assigned", "cli", 3.0, TWO_DAYS_FIVE_HOURS)
        zero = _assigned("b-zero", "premium", 0, timedelta(days=1))
        selected = [
            (_row("p1", "b-other", "1.0", category="cli"), assigned),
            (_row("p1", "b-other", "1.0", category="premium"), zero),
        ]

        built = project_spending_service.build_category_widget_rows(selected, {})

        assert [row.model_dump() for row in built] == [
            {
                "budget_id": "b-assigned",
                "budget_category": "cli",
                "is_assigned": True,
                "current_spending": 1.0,
                "budget_reset_at": NOW + TWO_DAYS_FIVE_HOURS,
                "time_until_reset": "2 days",
                "budget_limit": 3.0,
                "total": 33.33,
            },
            {
                "budget_id": "b-zero",
                "budget_category": "premium",
                "is_assigned": True,
                "current_spending": 1.0,
                "budget_reset_at": NOW + timedelta(days=1),
                "time_until_reset": "1 day",
                "budget_limit": 0.0,
                "total": 0.0,
            },
        ]

    def test_unassigned_row_has_no_limit_and_reads_its_reset_from_the_budgets(self):
        selected = [(_row("p1", "b-free", "2.0", category="free"), None)]

        built = project_spending_service.build_category_widget_rows(selected, {"b-free": _budget(9.0, FIVE_HOURS)})

        assert [row.model_dump() for row in built] == [
            {
                "budget_id": "b-free",
                "budget_category": "free",
                "is_assigned": False,
                "current_spending": 2.0,
                "budget_reset_at": NOW + FIVE_HOURS,
                "time_until_reset": "5 hours",
                "budget_limit": None,
                "total": 0.0,
            }
        ]


class TestKeyRowToWidget:
    def test_key_row_is_called_key_and_takes_limit_and_reset_from_the_budgets(self):
        row = _row("p1", "b1", "1.0")

        built = project_spending_service.key_row_to_widget(row, {"b1": _budget(3.0, TWO_DAYS_FIVE_HOURS)})

        assert built.model_dump() == _widget_row(
            budget_id="Key",
            current_spending=1.0,
            budget_reset_at=NOW + TWO_DAYS_FIVE_HOURS,
            time_until_reset="2 days",
            budget_limit=3.0,
            total=33.33,
        )

    def test_key_row_with_a_zero_limit_gives_zero_percent(self):
        built = project_spending_service.key_row_to_widget(_row("p1", "zero", "1.0"), {"zero": _budget(0.0)})

        assert (built.budget_limit, built.total) == (0.0, 0.0)

    def test_key_row_whose_budget_is_not_known_has_no_limit(self):
        built = project_spending_service.key_row_to_widget(_row("p1", "gone", "1.0"), {"b1": _budget(3.0)})

        assert (built.budget_limit, built.total) == (None, 0.0)

    def test_budgets_default_to_none(self):
        built = project_spending_service.key_row_to_widget(_row("p1", "b1", "1.0"))

        assert (built.budget_id, built.budget_limit, built.total) == ("Key", None, 0.0)


# --- groupers and summary -------------------------------------------------------------------------------------------


class TestLatestKeyRowsByProject:
    def test_latest_row_wins_only_on_a_strictly_later_date(self):
        first, newer = _row("p1", None, "1", date=LATER), _row("p1", None, "2", date=NOW)
        older = _row("p1", None, "3", date=EARLIER)
        tie_first, tie_second = _row("p2", None, "4", date=LATER), _row("p2", None, "5", date=LATER)

        latest = project_spending_service.latest_key_rows_by_project([first, newer, older, tie_first, tie_second])

        assert list(latest) == ["p1", "p2"]
        assert latest["p1"] is newer
        assert latest["p2"] is tie_first

    def test_empty_input_gives_empty_mapping(self):
        assert project_spending_service.latest_key_rows_by_project([]) == {}


class TestBudgetRowsGroupedByProject:
    def test_keeps_the_latest_row_per_project_and_budget(self):
        old_b1, new_b1 = _row("p1", "b1", "1", date=EARLIER), _row("p1", "b1", "2", date=LATER)
        other_b2 = _row("p1", "b2", "3", date=EARLIER)
        tie_first, tie_second = _row("p2", "b1", "4", date=LATER), _row("p2", "b1", "5", date=LATER)

        grouped = project_spending_service.budget_rows_grouped_by_project(
            [old_b1, other_b2, new_b1, tie_first, tie_second]
        )

        assert list(grouped) == ["p1", "p2"]
        assert _same_objects(grouped["p1"], [new_b1, other_b2])
        assert _same_objects(grouped["p2"], [tie_first])

    def test_an_older_row_listed_later_does_not_replace_the_latest(self):
        newer, older = _row("p1", "b1", "2", date=LATER), _row("p1", "b1", "1", date=EARLIER)

        grouped = project_spending_service.budget_rows_grouped_by_project([newer, older])

        assert _same_objects(grouped["p1"], [newer])


class TestProjectSpendingSummary:
    def _summary(self, key_row, budget_rows, budgets=None, assigned=None):
        summary = project_spending_service.project_spending_summary(key_row, budget_rows, budgets or {}, assigned)
        return None if summary is None else summary.model_dump()

    def test_neither_key_nor_budget_rows_gives_no_summary(self):
        assert self._summary(None, []) is None

    def test_key_only_takes_its_limit_from_the_budgets(self):
        key_row = _row("p1", "bk", "1.0")

        summary = self._summary(key_row, [], {"bk": _budget(3.0)})

        assert summary == {"current_spending": 1.0, "budget_limit": 3.0, "total_percent": 33.33}

    def test_budget_rows_only_without_any_limit(self):
        rows = [_row("p1", "x", "2.0"), _row("p1", "y", "1.5")]

        summary = self._summary(None, rows)

        assert summary == {"current_spending": 3.5, "budget_limit": None, "total_percent": 0.0}

    def test_assigned_limit_of_zero_is_kept_as_zero(self):
        summary = self._summary(_row("p1", "bk", "1.0"), [], assigned=[_assigned("a", "cli", 0)])

        assert summary == {"current_spending": 1.0, "budget_limit": 0.0, "total_percent": 0.0}

    def test_values_are_rounded_to_two_decimals(self):
        key_row = _row("p1", "bk", "0.1234")
        rows = [_row("p1", "x", "0.2222"), _row("p1", "y", "0.1111")]
        assigned = [
            _assigned("a", "cli", 1.14159, timedelta(days=5)),
            _assigned("b", "premium", 2.0, timedelta(days=2)),
        ]

        summary = self._summary(key_row, rows, assigned=assigned)

        assert summary == {"current_spending": 0.46, "budget_limit": 3.14, "total_percent": 14.54}


# --- IO: list ---------------------------------------------------------------------------------------------------


def _by_subject_type(key_rows, budget_rows):
    rows = {"key": key_rows, "budget": budget_rows}

    async def read(_session, names, *, spend_subject_type):
        return [row for row in rows[spend_subject_type] if row.project_name in names]

    return read


@pytest.mark.anyio
class TestAttachProjectSpendingSummaries:
    SENTINEL = object()

    def _items(self, *names):
        return [SimpleNamespace(name=name, spending=self.SENTINEL) for name in names]

    async def test_nothing_manageable_returns_before_opening_a_session(self, repos, events):
        items = self._items("p3")

        await project_spending_service.attach_project_spending_summaries(items, [{"name": "p3"}], ORDINARY)

        repos.get_async_session.assert_not_called()
        assert events == []
        assert items[0].spending is self.SENTINEL

    async def test_reads_only_manageable_names_and_summarizes_every_item(self, repos, events):
        cli = _assigned("b-cli", "cli", 5.0, timedelta(days=10))
        key_rows = [_row("p2", None, "0.25"), _row("p3", None, "9.0")]
        budget_rows = [_row("p1", "b-ignored", "10.0"), _row("p2", "b-p2", "1.5"), _row("p3", "b-p3", "9.0")]
        category_rows = [
            _row("p1", "b-cli", "2.0", category="cli"),
            _row("p1", "b-x", "4.0", category="x"),
            _row("p3", "b-p3", "9.0", category="cli"),
        ]
        repos.spend.get_latest_spending_by_project.side_effect = _by_subject_type(key_rows, budget_rows)
        repos.spend.get_latest_project_budget_rows_by_category.side_effect = lambda _session, names: [
            row for row in category_rows if row.project_name in names
        ]
        repos.budget_repository.get_all_keyed_by_id.return_value = {
            "b-cli": _budget(7.0, timedelta(days=10)),
            "b-x": _budget(9.0, timedelta(days=-1)),
        }
        repos.assignment_repository.get_assigned_budget_summaries_for_projects.return_value = {"p1": [cli]}
        enriched = [
            {"name": "p1", "is_project_admin": True},
            {"name": "p2", "project_type": "personal"},
            {"name": "p3"},
        ]
        items = self._items("p1", "p2", "p3")

        await project_spending_service.attach_project_spending_summaries(items, enriched, ORDINARY)

        manageable = ["p1", "p2"]
        assert repos.spend.get_latest_spending_by_project.await_args_list == [
            call(repos.session, manageable, spend_subject_type="key"),
            call(repos.session, manageable, spend_subject_type="budget"),
        ]
        repos.spend.get_latest_project_budget_rows_by_category.assert_awaited_once_with(repos.session, manageable)
        repos.budget_repository.get_all_keyed_by_id.assert_awaited_once_with(repos.session)
        repos.assignment_repository.get_assigned_budget_summaries_for_projects.assert_awaited_once_with(
            repos.session, manageable
        )
        assert [None if item.spending is None else item.spending.model_dump() for item in items] == [
            {"current_spending": 2.0, "budget_limit": 5.0, "total_percent": 40.0},
            {"current_spending": 1.75, "budget_limit": None, "total_percent": 0.0},
            None,
        ]

    async def test_one_session_and_the_clock_is_read_after_it_closes(self, repos, events):
        enriched = [{"name": "p1", "is_project_admin": True}]

        await project_spending_service.attach_project_spending_summaries(self._items("p1"), enriched, ORDINARY)

        repos.get_async_session.assert_called_once_with()
        assert events == ["enter", "exit", "now"]


# --- IO: detail -------------------------------------------------------------------------------------------------


@pytest.mark.anyio
class TestAttachProjectDetailSpending:
    CLI = _assigned("b-cli", "cli", 5.0, timedelta(days=10))

    async def _attach(
        self, repos, *, key_row=None, category_rows=(), budget_rows=(), budgets=None, assigned=(), lifetime=3.5
    ):
        repos.spend.get_latest_key_spending_for_project.return_value = key_row
        repos.spend.get_latest_project_budget_rows_by_category.return_value = list(category_rows)
        repos.spend.get_latest_budget_rows_for_project.return_value = list(budget_rows)
        repos.spend.get_lifetime_spend.return_value = lifetime
        repos.budget_repository.get_all_keyed_by_id.return_value = budgets or {}
        repos.assignment_repository.get_assigned_budget_summaries_for_projects.return_value = {"p1": list(assigned)}
        response = _response()
        await project_spending_service.attach_project_detail_spending(
            response=response, project_name="p1", spending_rows_limit=7
        )
        return response

    def _lifetime_subject_type(self, repos) -> str:
        return repos.spend.get_lifetime_spend.await_args.kwargs["spend_subject_type"]

    async def test_project_budget_rows_win_and_the_budget_rows_are_not_read(self, repos):
        response = await self._attach(
            repos,
            category_rows=[_row("p1", "b-cli", "2.0", category="cli")],
            budget_rows=[_row("p1", "b-budget", "8.0")],
            budgets={"b-cli": _budget(7.0, timedelta(days=10))},
            assigned=[self.CLI],
            lifetime=12.3456,
        )

        repos.spend.get_latest_budget_rows_for_project.assert_not_awaited()
        repos.spend.get_latest_project_budget_rows_by_category.assert_awaited_once_with(repos.session, ["p1"])
        repos.spend.get_lifetime_spend.assert_awaited_once_with(
            repos.session, "p1", spend_subject_type="project_budget"
        )
        assert response.spending.model_dump() == {
            "current_spending": 2.0,
            "cumulative_spend": 12.35,
            "budget_reset_at": NOW + timedelta(days=10),
            "time_until_reset": "10 days",
            "budget_limit": 5.0,
            "total": 40.0,
        }

    async def test_category_path_filters_through_the_selection_and_the_widget_follows_it(self, repos):
        response = await self._attach(
            repos,
            category_rows=[
                _row("p1", "b-cli", "2.0", category="cli"),
                _row("p1", "b-x", "4.0", category="x"),
                _row("p1", "b-free", "1.0", category="free"),
            ],
            budgets={
                "b-cli": _budget(5.0, timedelta(days=10)),
                "b-x": _budget(9.0, timedelta(days=-1)),
                "b-free": _budget(9.0, FIVE_HOURS),
            },
            assigned=[self.CLI],
        )

        assert (response.spending.current_spending, response.spending.total) == (3.0, 60.0)
        assert [row.model_dump() for row in response.spending_widget.data.rows] == [
            {
                "budget_id": "b-cli",
                "budget_category": "cli",
                "is_assigned": True,
                "current_spending": 2.0,
                "budget_reset_at": NOW + timedelta(days=10),
                "time_until_reset": "10 days",
                "budget_limit": 5.0,
                "total": 40.0,
            },
            {
                "budget_id": "b-free",
                "budget_category": "free",
                "is_assigned": False,
                "current_spending": 1.0,
                "budget_reset_at": NOW + FIVE_HOURS,
                "time_until_reset": "5 hours",
                "budget_limit": None,
                "total": 0.0,
            },
        ]

    async def test_one_session_serves_every_read_and_the_clock_is_read_after_it_closes(self, repos, events):
        await self._attach(
            repos,
            category_rows=[_row("p1", "b-cli", "2.0", category="cli")],
            budgets={"b-cli": _budget(5.0, timedelta(days=10))},
            assigned=[self.CLI],
        )

        repos.get_async_session.assert_called_once_with()
        assert events[:3] == ["enter", "exit", "now"]
        repos.spend.get_latest_key_spending_for_project.assert_awaited_once_with(repos.session, "p1")
        repos.budget_repository.get_all_keyed_by_id.assert_awaited_once_with(repos.session)
        repos.assignment_repository.get_assigned_budget_summaries_for_projects.assert_awaited_once_with(
            repos.session, ["p1"]
        )

    async def test_budget_rows_are_read_with_the_rows_limit_when_no_category_rows_exist(self, repos):
        response = await self._attach(
            repos,
            budget_rows=[_row("p1", "b1", "1.0")],
            budgets={"b1": _budget(3.0, TWO_DAYS_FIVE_HOURS)},
        )

        repos.spend.get_latest_budget_rows_for_project.assert_awaited_once_with(repos.session, "p1", rows_limit=7)
        assert self._lifetime_subject_type(repos) == "budget"
        assert response.spending.model_dump() == {
            "current_spending": 1.0,
            "cumulative_spend": 3.5,
            "budget_reset_at": NOW + TWO_DAYS_FIVE_HOURS,
            "time_until_reset": "2 days",
            "budget_limit": 3.0,
            "total": 33.33,
        }
        assert [row.model_dump() for row in response.spending_widget.data.rows] == [
            _widget_row(
                budget_id="b1",
                current_spending=1.0,
                budget_reset_at=NOW + TWO_DAYS_FIVE_HOURS,
                time_until_reset="2 days",
                budget_limit=3.0,
                total=33.33,
            )
        ]

    async def test_key_row_is_dropped_once_a_budget_row_exists_pinned_quirk(self, repos):
        response = await self._attach(
            repos,
            key_row=_row("p1", "bk", "7.0"),
            budget_rows=[_row("p1", "b1", "1.0")],
            budgets={"b1": _budget(4.0, timedelta(days=3)), "bk": _budget(100.0)},
        )

        assert self._lifetime_subject_type(repos) == "budget"
        assert response.spending.current_spending == 1.0
        assert [row.budget_id for row in response.spending_widget.data.rows] == ["b1"]

    async def test_key_row_alone_reads_the_key_lifetime_spend_and_is_the_only_widget_row(self, repos):
        response = await self._attach(
            repos,
            key_row=_row("p1", "bk", "1.0"),
            budgets={"bk": _budget(3.0, TWO_DAYS_FIVE_HOURS)},
            lifetime=2.0,
        )

        assert self._lifetime_subject_type(repos) == "key"
        assert response.spending.model_dump() == {
            "current_spending": 1.0,
            "cumulative_spend": 2.0,
            "budget_reset_at": NOW + TWO_DAYS_FIVE_HOURS,
            "time_until_reset": "2 days",
            "budget_limit": 3.0,
            "total": 33.33,
        }
        assert [row.model_dump() for row in response.spending_widget.data.rows] == [
            _widget_row(
                budget_id="Key",
                current_spending=1.0,
                budget_reset_at=NOW + TWO_DAYS_FIVE_HOURS,
                time_until_reset="2 days",
                budget_limit=3.0,
                total=33.33,
            )
        ]

    async def test_no_rows_at_all_leaves_the_response_untouched_and_reads_the_budget_lifetime(self, repos):
        response = await self._attach(repos)

        assert self._lifetime_subject_type(repos) == "budget"
        assert response.spending is None
        assert response.spending_widget is None


# --- populate -------------------------------------------------------------------------------------------------------


class TestPopulateProjectSpendingSummary:
    def _populate(self, key_row, budget_rows, budgets=None, assigned=None, lifetime=12.3456):
        response = _response()
        project_spending_service.populate_project_spending_summary(
            response=response,
            key_row=key_row,
            budget_rows=budget_rows,
            budgets_map=budgets or {},
            lifetime_spend=lifetime,
            assigned_budgets=assigned,
        )
        return response

    def test_neither_key_nor_budget_rows_leaves_the_response_untouched(self):
        assert self._populate(None, []).spending is None

    def test_assigned_budgets_give_rounded_sums_and_the_earliest_reset(self):
        key_row = _row("p1", "bk", "0.1234")
        rows = [_row("p1", "x", "0.2222"), _row("p1", "y", "0.1111")]
        assigned = [
            _assigned("a", "cli", 1.14159, timedelta(days=5)),
            _assigned("b", "premium", 2.0, timedelta(days=2)),
        ]

        response = self._populate(key_row, rows, assigned=assigned)

        assert response.spending.model_dump() == {
            "current_spending": 0.46,
            "cumulative_spend": 12.35,
            "budget_reset_at": NOW + timedelta(days=2),
            "time_until_reset": "2 days",
            "budget_limit": 3.14,
            "total": 14.54,
        }

    def test_key_only_without_assignment_takes_the_limit_of_the_key_budget(self):
        response = self._populate(
            _row("p1", "bk", "1.0"), [], budgets={"bk": _budget(3.0, TWO_DAYS_FIVE_HOURS)}, lifetime=2.0
        )

        assert response.spending.model_dump() == {
            "current_spending": 1.0,
            "cumulative_spend": 2.0,
            "budget_reset_at": NOW + TWO_DAYS_FIVE_HOURS,
            "time_until_reset": "2 days",
            "budget_limit": 3.0,
            "total": 33.33,
        }

    def test_assigned_limit_of_zero_is_kept_as_zero(self):
        response = self._populate(_row("p1", "bk", "1.0"), [], assigned=[_assigned("a", "cli", 0)])

        assert (response.spending.budget_limit, response.spending.total) == (0.0, 0.0)

    def test_budget_rows_without_key_or_limit_give_zero_percent_and_no_reset(self):
        rows = [_row("p1", "x", "2.0"), _row("p1", "y", "1.5")]

        response = self._populate(None, rows, lifetime=0.0)

        assert response.spending.model_dump() == {
            "current_spending": 3.5,
            "cumulative_spend": 0.0,
            "budget_reset_at": None,
            "time_until_reset": None,
            "budget_limit": None,
            "total": 0.0,
        }


class TestPopulateProjectSpendingWidget:
    def _populate(self, widget_rows, key_row=None, budgets=None):
        response = _response()
        project_spending_service.populate_project_spending_widget(
            response=response,
            widget_rows=widget_rows,
            key_row=key_row,
            budgets_map=budgets or {},
        )
        return response

    def _rows(self):
        return project_spending_service.build_widget_rows([_row("p1", "b1", "1.0"), _row("p1", "b2", "2.0")])

    def test_no_rows_and_no_key_row_leave_no_widget(self):
        assert self._populate([]).spending_widget is None

    def test_rows_without_key_row_become_the_widget_rows(self):
        rows = self._rows()

        widget = self._populate(rows).spending_widget

        assert widget.data.rows == rows

    def test_key_row_is_appended_last_and_the_given_list_is_not_changed(self):
        rows = self._rows()

        widget = self._populate(rows, key_row=_row("p1", "bk", "1.0"), budgets={"bk": _budget(3.0)}).spending_widget

        assert [row.budget_id for row in widget.data.rows] == ["b1", "b2", "Key"]
        assert widget.data.rows[-1].budget_limit == 3.0
        assert len(rows) == 2

    def test_key_row_alone_builds_the_widget(self):
        widget = self._populate([], key_row=_row("p1", "bk", "1.0")).spending_widget

        assert [row.budget_id for row in widget.data.rows] == ["Key"]

    def test_widget_has_exactly_the_six_columns(self):
        widget = self._populate(self._rows()).spending_widget

        assert [column.model_dump() for column in widget.data.columns] == WIDGET_COLUMNS


class TestSpendRepository:
    def test_spend_repo_is_a_module_level_instance_beside_the_singleton(self):
        assert isinstance(project_spending_service.spend_repo, ProjectSpendTrackingRepository)
        assert project_spending_service.spend_repo is not project_spend_tracking_repository


# --- public signatures ----------------------------------------------------------------------------------------------

BARE_GENERIC = re.compile(r"\b(list|dict)\b(?!\[)")
TYPED_SIGNATURES = [
    "aggregate_budget_window",
    "build_widget_rows",
    "key_row_to_widget",
    "manageable_project_names",
    "project_spending_summary",
    "attach_project_spending_summaries",
    "can_see_project_spending",
    "populate_project_spending_summary",
    "populate_project_spending_widget",
]


class TestPublicSignatures:
    """Every parameter and the return carry an annotation, and no generic is left bare.

    The annotation-only imports sit under TYPE_CHECKING, so ``get_type_hints`` would raise NameError: the annotation
    strings are read instead.
    """

    @pytest.mark.parametrize("name", TYPED_SIGNATURES)
    def test_every_annotation_is_present_and_parameterized(self, name):
        signature = inspect.signature(getattr(project_spending_service, name))

        missing = [
            parameter.name for parameter in signature.parameters.values() if parameter.annotation is parameter.empty
        ]
        if signature.return_annotation is signature.empty:
            missing.append("return")
        bare = [
            f"{parameter.name}: {parameter.annotation}"
            for parameter in signature.parameters.values()
            if parameter.annotation is not parameter.empty and BARE_GENERIC.search(parameter.annotation)
        ]
        if signature.return_annotation is not signature.empty and BARE_GENERIC.search(signature.return_annotation):
            bare.append(f"return: {signature.return_annotation}")

        assert (missing, bare) == ([], [])
