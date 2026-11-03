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

"""Tests for the project spending helpers that outlived the move to the spend ledger: access and reset text."""

import inspect
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from codemie.core.models import Application
from codemie.repository.project_budget_repository import ProjectAssignedBudgetSummaryRow
from codemie.service.project import project_spending_service
from codemie.service.spend.coverage import TimeRange

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
# Non-zero days, hours and remainder: every divisor and floor in the reset text is observable with this offset.
TWO_DAYS_FIVE_HOURS = timedelta(days=2, hours=5, minutes=30)
FIVE_HOURS = timedelta(hours=5, minutes=30)
PERSONAL = Application.ProjectType.PERSONAL


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


# --- public signatures ----------------------------------------------------------------------------------------------

BARE_GENERIC = re.compile(r"\b(list|dict)\b(?!\[)")
TYPED_SIGNATURES = [
    "aggregate_budget_window",
    "build_widget_rows",
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


# --- budget period --------------------------------------------------------------------------------------------------

PERIOD_RESET = "2099-09-01T00:00:00+00:00"
PERIOD_START = datetime(2099, 8, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2099, 9, 1, tzinfo=timezone.utc)
CREATED_MID_PERIOD = datetime(2099, 8, 5, tzinfo=timezone.utc)
RESET_MID_PERIOD = datetime(2099, 8, 20, 14, 15, tzinfo=timezone.utc)


def _summary_row(
    *,
    created_at: datetime | None = None,
    spend_reset_at: datetime | None = None,
    reset_at: str = PERIOD_RESET,
) -> ProjectAssignedBudgetSummaryRow:
    return ProjectAssignedBudgetSummaryRow(
        project_name="atlas-core",
        budget_id="b-1",
        name="atlas-core cli",
        budget_category="cli",
        soft_budget=80.0,
        max_budget=100.0,
        budget_duration="30d",
        budget_reset_at=reset_at,
        provider_sync_status=None,
        member_count=0,
        allocated_member_budget_total=0.0,
        budget_created_at=created_at,
        budget_spend_reset_at=spend_reset_at,
    )


class TestBudgetPeriodOf:
    """The period starts no earlier than the budget's manual spend reset, when there is one."""

    @pytest.mark.parametrize(
        ("created_at", "spend_reset_at", "expected_start"),
        [
            pytest.param(None, None, PERIOD_START, id="no-creation-no-reset"),
            pytest.param(CREATED_MID_PERIOD, None, CREATED_MID_PERIOD, id="no-reset-keeps-creation"),
            pytest.param(None, RESET_MID_PERIOD, RESET_MID_PERIOD, id="reset-without-creation"),
            pytest.param(CREATED_MID_PERIOD, RESET_MID_PERIOD, RESET_MID_PERIOD, id="reset-after-creation"),
            pytest.param(RESET_MID_PERIOD, CREATED_MID_PERIOD, RESET_MID_PERIOD, id="reset-before-creation"),
            pytest.param(
                None, datetime(2099, 7, 15, tzinfo=timezone.utc), PERIOD_START, id="reset-in-an-earlier-period"
            ),
            pytest.param(
                CREATED_MID_PERIOD, RESET_MID_PERIOD.replace(tzinfo=None), RESET_MID_PERIOD, id="naive-reset-is-utc"
            ),
        ],
    )
    def test_starts_at_the_later_of_counting_since_and_the_reset(self, created_at, spend_reset_at, expected_start):
        row = _summary_row(created_at=created_at, spend_reset_at=spend_reset_at)

        assert project_spending_service.budget_period_of(row, []) == TimeRange(expected_start, PERIOD_END)

    def test_a_reset_before_a_removed_budget_began_counting_keeps_its_start(self):
        """A removed budget of the category sharing the reset keeps the earlier start (EPMCDME-15048)."""
        removed = _summary_row(created_at=CREATED_MID_PERIOD)
        row = _summary_row(
            created_at=datetime(2099, 8, 25, tzinfo=timezone.utc),
            spend_reset_at=datetime(2099, 8, 3, tzinfo=timezone.utc),
        )

        assert project_spending_service.budget_period_of(row, [removed]) == TimeRange(CREATED_MID_PERIOD, PERIOD_END)

    def test_a_reset_after_a_removed_budget_began_counting_moves_the_start(self):
        removed = _summary_row(created_at=CREATED_MID_PERIOD)
        row = _summary_row(created_at=datetime(2099, 8, 25, tzinfo=timezone.utc), spend_reset_at=RESET_MID_PERIOD)

        assert project_spending_service.budget_period_of(row, [removed]) == TimeRange(RESET_MID_PERIOD, PERIOD_END)
