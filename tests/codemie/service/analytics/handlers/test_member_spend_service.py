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

"""Unit tests for member_spend_service helpers and MemberSpendService."""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from codemie.service.analytics.handlers.member_spend_service import (
    MemberSpendService,
    _build_row,
    _category_columns,
    _limit_value,
    _spend_cell,
)
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import ProjectMemberHolder

RESET = "2099-09-01T00:00:00+00:00"
#: The calendar month before RESET (a 30d budget resetting on the 1st) - what the port is actually asked about.
PERIOD = TimeRange(datetime(2099, 8, 1, tzinfo=timezone.utc), datetime(2099, 9, 1, tzinfo=timezone.utc))
SUB_FLOOR = TimeRange(datetime(2099, 8, 1, tzinfo=timezone.utc), datetime(2099, 8, 10, tzinfo=timezone.utc))
#: Inside PERIOD, and before it.
CREATED_MID_PERIOD = datetime(2099, 8, 14, 9, 30, tzinfo=timezone.utc)
CREATED_BEFORE_PERIOD = datetime(2099, 1, 1, tzinfo=timezone.utc)
#: A manual spend reset inside PERIOD after CREATED_MID_PERIOD, and one in an earlier period.
RESET_MID_PERIOD = datetime(2099, 8, 20, 14, 15, tzinfo=timezone.utc)
RESET_BEFORE_PERIOD = datetime(2099, 7, 15, tzinfo=timezone.utc)
#: Where an overdue RESET moves once reconciled, and the period that reset closes.
MOVED_RESET = "2099-10-01T00:00:00+00:00"
MOVED_PERIOD = TimeRange(datetime(2099, 9, 1, tzinfo=timezone.utc), datetime(2099, 10, 1, tzinfo=timezone.utc))


def _budget(
    budget_id: str = "b-1",
    reset_at: str | None = RESET,
    duration: str | None = "30d",
    created_at: datetime | None = None,
    spend_reset_at: datetime | None = None,
):
    return SimpleNamespace(
        budget_id=budget_id,
        budget_reset_at=reset_at,
        budget_duration=duration,
        created_at=created_at,
        spend_reset_at=spend_reset_at,
    )


def _allocation(
    *,
    project_name: str = "atlas-core",
    category: str = "cli",
    user_id: str = "u-1",
    max_budget: float = 100.0,
    budget_id: str = "b-1",
):
    return SimpleNamespace(
        project_name=project_name,
        budget_category=category,
        user_id=user_id,
        allocated_max_budget=max_budget,
        project_budget_id=budget_id,
    )


def _figures(**by_category: Decimal | None):
    """A consumed_for_holders stub answering per category; a None entry answers "no figure"."""

    async def _call(holders, period):
        return {
            holder: (
                SpendResult(figure=by_category[holder.category.value])
                if by_category.get(holder.category.value) is not None
                else SpendResult.no_figure(period)
            )
            for holder in holders
        }

    return AsyncMock(side_effect=_call)


def _results(**by_category: SpendResult):
    """A consumed_for_holders stub answering with the `SpendResult` given, annotation included."""

    async def _call(holders, period):
        return {holder: by_category[holder.category.value] for holder in holders}

    return AsyncMock(side_effect=_call)


@pytest.fixture(autouse=True)
def refresh_overdue():
    """The reset reconciliation moves nothing unless a test says otherwise - it is a provider call."""
    with patch(
        "codemie.service.analytics.handlers.member_spend_service.daily_budget_reset_reconciliation_service."
        "refresh_overdue",
        new_callable=AsyncMock,
        return_value=set(),
    ) as refresh:
        yield refresh


class _SessionTracker:
    """Stands in for `get_async_session`: records every session it opens and how many are open now."""

    def __init__(self):
        self.sessions: list[AsyncMock] = []
        self.open = 0

    @contextlib.asynccontextmanager
    async def __call__(self):
        session = AsyncMock()
        self.sessions.append(session)
        self.open += 1
        try:
            yield session
        finally:
            self.open -= 1


@pytest.fixture(autouse=True)
def sessions():
    """The readers open their own short sessions; none reaches a database here."""
    with patch(
        "codemie.service.analytics.handlers.member_spend_service.get_async_session", _SessionTracker()
    ) as tracker:
        yield tracker


def _patched_port(budgets, consumed):
    """Patch the spend port and the budget lookup that supplies the period."""
    stack = contextlib.ExitStack()
    stack.enter_context(
        patch(
            "codemie.service.analytics.handlers.member_spend_service.budget_repository.get_by_ids",
            new=AsyncMock(return_value={b.budget_id: b for b in budgets}),
        )
    )
    stack.enter_context(
        patch(
            "codemie.service.analytics.handlers.member_spend_service.spend_service.consumed_for_holders",
            new=consumed,
        )
    )
    return stack


def _patched_user_reads(memberships, allocations, personal_projects: set[str]):
    stack = contextlib.ExitStack()
    stack.enter_context(
        patch(
            "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
            new=AsyncMock(return_value=memberships),
        )
    )
    stack.enter_context(
        patch(
            "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
            "get_active_by_user",
            new=AsyncMock(return_value=allocations),
        )
    )
    stack.enter_context(
        patch(
            "codemie.repository.application_repository.application_repository.aget_personal_project_names",
            new=AsyncMock(return_value=personal_projects),
        )
    )
    return stack


def _patched_project_reads(members, allocations):
    stack = contextlib.ExitStack()
    stack.enter_context(
        patch(
            "codemie.repository.user_project_repository.user_project_repository.aget_by_project_name",
            new=AsyncMock(return_value=members),
        )
    )
    stack.enter_context(
        patch(
            "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
            "get_active_by_project",
            new=AsyncMock(return_value=allocations),
        )
    )
    return stack


class TestCategoryColumns:
    """Tests for _category_columns."""

    def test_first_column_is_the_key_column(self):
        columns = _category_columns("project_name", "Project")
        assert columns[0] == {
            "id": "project_name",
            "label": "Project",
            "type": "string",
            "format": None,
        }

    def test_emits_one_currency_column_per_budget_category(self):
        columns = _category_columns("user_id", "User")
        ids = [c["id"] for c in columns[1:]]
        assert ids == ["platform", "cli", "premium_models"]
        assert all(c["type"] == "number" for c in columns[1:])
        assert all(c["format"] == "currency" for c in columns[1:])

    def test_no_total_column(self):
        assert "total" not in [c["id"] for c in _category_columns("user_id", "User")]


class TestSpendCell:
    """Both directions of the absent-figure rule."""

    def test_absent_figure_stays_absent(self):
        """0 would read as "spent nothing"; the fact here is "unknown"."""
        assert _spend_cell(None) is None

    def test_measured_zero_renders_as_zero(self):
        assert _spend_cell(SpendResult(figure=Decimal("0"))) == 0.0

    def test_figure_renders_as_a_number(self):
        assert _spend_cell(SpendResult(figure=Decimal("12.50"))) == 12.5


class TestLimitValue:
    """Tests for _limit_value."""

    def test_missing_allocation_is_none_not_zero(self):
        assert _limit_value(None) is None

    def test_reads_allocated_max_budget(self):
        assert _limit_value(SimpleNamespace(allocated_max_budget=500.0)) == 500.0

    def test_zero_limit_is_preserved_as_zero(self):
        assert _limit_value(SimpleNamespace(allocated_max_budget=0.0)) == 0.0


class TestBuildRow:
    """Tests for _build_row."""

    def test_includes_key_values_verbatim(self):
        row = _build_row({"project_name": "atlas-core"}, {}, {})
        assert row["project_name"] == "atlas-core"

    def test_absent_categories_render_as_absent_spend_and_null_limit(self):
        row = _build_row({"user_id": "u-1"}, {}, {})
        assert row["platform"] is None
        assert row["cli"] is None
        assert row["premium_models"] is None
        assert row["platform_limit"] is None
        assert row["cli_limit"] is None
        assert row["premium_models_limit"] is None

    def test_populates_spend_and_limit_per_category(self):
        row = _build_row(
            {"user_id": "u-1"},
            {"cli": SpendResult(figure=Decimal("40.00"))},
            {"cli": SimpleNamespace(allocated_max_budget=100.0)},
        )
        assert row["cli"] == 40.0
        assert row["cli_limit"] == 100.0
        assert row["platform"] is None
        assert row["platform_limit"] is None

    def test_measured_zero_is_not_confused_with_an_absent_figure(self):
        row = _build_row({"user_id": "u-1"}, {"cli": SpendResult(figure=Decimal("0"))}, {})
        assert row["cli"] == 0.0
        assert row["platform"] is None

    def test_no_total_key(self):
        assert "total" not in _build_row({"user_id": "u-1"}, {}, {})


class TestGetUserProjectSpend:
    """Tests for MemberSpendService.get_user_project_spend."""

    @pytest.mark.asyncio
    async def test_returns_empty_rows_when_user_has_no_projects(self):
        service = MemberSpendService()

        with (
            patch(
                "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
                new=AsyncMock(return_value=[]),
            ),
        ):
            columns, rows = await service.get_user_project_spend("u-1")

        assert rows == []
        assert columns[0]["id"] == "project_name"

    @pytest.mark.asyncio
    async def test_project_without_an_allocation_still_returns_a_row(self):
        """Membership drives rows; a project with no member budget must never disappear."""
        service = MemberSpendService()
        consumed = _figures()

        with (
            _patched_user_reads([SimpleNamespace(project_name="atlas-core", user_id="u-1")], [], set()),
            _patched_port([_budget()], consumed),
        ):
            _, rows = await service.get_user_project_spend("u-1")

        assert len(rows) == 1
        assert rows[0]["project_name"] == "atlas-core"
        assert rows[0]["platform"] is None
        assert rows[0]["cli"] is None
        assert rows[0]["platform_limit"] is None
        consumed.assert_not_awaited()
        # Every row key must be described by columns[], which is authoritative.
        assert "display_name" not in rows[0]

    @pytest.mark.asyncio
    async def test_an_overdue_reset_is_reconciled_before_the_period_is_derived(self, refresh_overdue, sessions):
        """The parent budget's stored reset names the period that just closed; it is reconciled and
        re-read first, so the port is asked about the current period. The re-read runs in a new
        session: the metadata one is closed by then, and its detached rows cannot be refreshed
        (EPMCDME-15537)."""
        get_by_ids = AsyncMock(side_effect=[{"b-1": _budget()}, {"b-1": _budget(reset_at=MOVED_RESET)}])
        refresh_overdue.return_value = {"b-1"}
        open_when_read = []

        async def read_ledger(holders, period):
            open_when_read.append(sessions.open)
            return {holder: SpendResult(figure=Decimal("40.00")) for holder in holders}

        consumed = AsyncMock(side_effect=read_ledger)

        with (
            _patched_user_reads([SimpleNamespace(project_name="atlas-core", user_id="u-1")], [_allocation()], set()),
            patch(
                "codemie.service.analytics.handlers.member_spend_service.budget_repository.get_by_ids",
                new=get_by_ids,
            ),
            patch(
                "codemie.service.analytics.handlers.member_spend_service.spend_service.consumed_for_holders",
                new=consumed,
            ),
        ):
            await MemberSpendService().get_user_project_spend("u-1")

        assert get_by_ids.await_args.args == (sessions.sessions[1], ["b-1"])
        assert consumed.await_args.args[1] == MOVED_PERIOD
        assert open_when_read == [0], "the re-read session is closed before the ledger is read"

    @pytest.mark.asyncio
    async def test_joins_spend_and_limits_onto_membership_rows(self):
        service = MemberSpendService()

        with (
            _patched_user_reads(
                [SimpleNamespace(project_name="atlas-core", user_id="u-1")],
                [_allocation()],
                set(),
            ),
            _patched_port([_budget()], _figures(cli=Decimal("40.00"))),
        ):
            _, rows = await service.get_user_project_spend("u-1")

        assert rows[0]["cli"] == 40.0
        assert rows[0]["cli_limit"] == 100.0
        assert rows[0]["platform"] is None
        assert rows[0]["platform_limit"] is None

    @pytest.mark.asyncio
    async def test_asks_the_port_for_the_member_holder_over_the_budget_period(self):
        service = MemberSpendService()
        consumed = _figures(cli=Decimal("40.00"))

        with (
            _patched_user_reads(
                [SimpleNamespace(project_name="atlas-core", user_id="u-1")],
                [_allocation()],
                set(),
            ),
            _patched_port([_budget()], consumed),
        ):
            await service.get_user_project_spend("u-1")

        holders, period = consumed.await_args[0]
        assert holders == [ProjectMemberHolder(project="atlas-core", category=BudgetCategory.CLI, user_id="u-1")]
        # The calendar month before the reset instant, half-open — never a rolling window ending at now.
        assert period == TimeRange(
            datetime(2099, 8, 1, tzinfo=timezone.utc),
            datetime(2099, 9, 1, tzinfo=timezone.utc),
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("created_at", "expected_period"),
        [
            pytest.param(CREATED_MID_PERIOD, TimeRange(CREATED_MID_PERIOD, PERIOD.end), id="created-mid-period"),
            pytest.param(CREATED_BEFORE_PERIOD, PERIOD, id="created-before-period"),
        ],
    )
    async def test_the_period_starts_no_earlier_than_the_project_budget(self, created_at, expected_period):
        """The member figure must cover the same window as its project's figure (EPMCDME-15237 AC3)."""
        consumed = _figures(cli=Decimal("40.00"))

        with (
            _patched_user_reads([SimpleNamespace(project_name="atlas-core", user_id="u-1")], [_allocation()], set()),
            _patched_port([_budget(created_at=created_at)], consumed),
        ):
            await MemberSpendService().get_user_project_spend("u-1")

        assert consumed.await_args.args[1] == expected_period

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("created_at", "spend_reset_at", "expected_period"),
        [
            pytest.param(None, RESET_MID_PERIOD, TimeRange(RESET_MID_PERIOD, PERIOD.end), id="reset-mid-period"),
            pytest.param(
                CREATED_MID_PERIOD, RESET_MID_PERIOD, TimeRange(RESET_MID_PERIOD, PERIOD.end), id="reset-after-creation"
            ),
            pytest.param(
                RESET_MID_PERIOD,
                CREATED_MID_PERIOD,
                TimeRange(RESET_MID_PERIOD, PERIOD.end),
                id="reset-before-creation",
            ),
            pytest.param(None, RESET_BEFORE_PERIOD, PERIOD, id="reset-in-an-earlier-period"),
            pytest.param(
                CREATED_MID_PERIOD,
                RESET_MID_PERIOD.replace(tzinfo=None),
                TimeRange(RESET_MID_PERIOD, PERIOD.end),
                id="naive-reset-is-utc",
            ),
        ],
    )
    async def test_the_period_starts_no_earlier_than_the_project_budget_reset(
        self, created_at, spend_reset_at, expected_period
    ):
        consumed = _figures(cli=Decimal("40.00"))

        with (
            _patched_user_reads([SimpleNamespace(project_name="atlas-core", user_id="u-1")], [_allocation()], set()),
            _patched_port([_budget(created_at=created_at, spend_reset_at=spend_reset_at)], consumed),
        ):
            await MemberSpendService().get_user_project_spend("u-1")

        assert consumed.await_args.args[1] == expected_period

    @pytest.mark.asyncio
    async def test_absent_figure_is_not_turned_into_zero(self):
        """A ledger that cannot answer leaves the cell empty; 0 would claim nothing was spent."""
        service = MemberSpendService()

        with (
            _patched_user_reads(
                [SimpleNamespace(project_name="atlas-core", user_id="u-1")],
                [_allocation()],
                set(),
            ),
            _patched_port([_budget()], _figures(cli=None)),
        ):
            _, rows = await service.get_user_project_spend("u-1")

        assert rows[0]["cli"] is None
        assert rows[0]["cli_limit"] == 100.0

    @pytest.mark.asyncio
    async def test_measured_zero_renders_as_zero(self):
        service = MemberSpendService()

        with (
            _patched_user_reads(
                [SimpleNamespace(project_name="atlas-core", user_id="u-1")],
                [_allocation()],
                set(),
            ),
            _patched_port([_budget()], _figures(cli=Decimal("0"))),
        ):
            _, rows = await service.get_user_project_spend("u-1")

        assert rows[0]["cli"] == 0.0

    @pytest.mark.asyncio
    async def test_budget_without_a_derivable_period_is_not_queried(self):
        """No reset instant means no budget period: report absent rather than guess a window."""
        service = MemberSpendService()
        consumed = _figures(cli=Decimal("40.00"))

        with (
            _patched_user_reads(
                [SimpleNamespace(project_name="atlas-core", user_id="u-1")],
                [_allocation()],
                set(),
            ),
            _patched_port([_budget(reset_at=None)], consumed),
        ):
            _, rows = await service.get_user_project_spend("u-1")

        consumed.assert_not_awaited()
        assert rows[0]["cli"] is None
        assert rows[0]["cli_limit"] == 100.0


class TestGetProjectMemberSpend:
    """Tests for MemberSpendService.get_project_member_spend."""

    @pytest.mark.asyncio
    async def test_rows_are_ordered_by_user_id(self):
        """The endpoint paginates, so rows need a stable order or pages skip and repeat members."""
        members = [SimpleNamespace(user_id=user_id, project_name="atlas-core") for user_id in ("u-3", "u-1", "u-2")]

        with _patched_project_reads(members, []), _patched_port([], _figures()):
            _, rows = await MemberSpendService().get_project_member_spend("atlas-core")

        assert [row["user_id"] for row in rows] == ["u-1", "u-2", "u-3"]

    @pytest.mark.asyncio
    async def test_returns_empty_rows_for_project_with_no_members(self):
        service = MemberSpendService()

        with patch(
            "codemie.repository.user_project_repository.user_project_repository.aget_by_project_name",
            new=AsyncMock(return_value=[]),
        ):
            columns, rows = await service.get_project_member_spend("atlas-core")

        assert rows == []
        assert columns[0]["id"] == "user_id"

    @pytest.mark.asyncio
    async def test_member_with_no_ledger_rows_still_appears_in_the_table(self):
        """A measured zero from the ledger is a real figure and keeps the member visible."""
        service = MemberSpendService()

        with (
            _patched_project_reads(
                [SimpleNamespace(user_id="u-7", project_name="atlas-core")],
                [_allocation(category="premium_models", user_id="u-7", max_budget=50.0)],
            ),
            _patched_port([_budget()], _figures(premium_models=Decimal("0"))),
        ):
            _, rows = await service.get_project_member_spend("atlas-core")

        assert len(rows) == 1
        assert rows[0]["user_id"] == "u-7"
        assert rows[0]["premium_models"] == 0.0
        assert rows[0]["premium_models_limit"] == 50.0

    @pytest.mark.asyncio
    async def test_member_without_an_allocation_still_returns_a_row(self):
        service = MemberSpendService()

        with (
            _patched_project_reads([SimpleNamespace(user_id="u-7", project_name="atlas-core")], []),
            _patched_port([_budget()], _figures()),
        ):
            _, rows = await service.get_project_member_spend("atlas-core")

        assert len(rows) == 1
        assert rows[0]["user_id"] == "u-7"
        assert rows[0]["premium_models"] is None
        assert rows[0]["premium_models_limit"] is None

    @pytest.mark.asyncio
    async def test_holders_are_keyed_on_the_member_and_batched_into_one_call(self):
        """Members sharing a reset schedule share a period, so the port is asked once."""
        service = MemberSpendService()
        consumed = _figures(cli=Decimal("7.50"))
        members = [
            SimpleNamespace(user_id="u-1", project_name="atlas-core"),
            SimpleNamespace(user_id="u-2", project_name="atlas-core"),
        ]
        allocations = [_allocation(user_id="u-1"), _allocation(user_id="u-2")]

        with (
            _patched_project_reads(members, allocations),
            _patched_port([_budget()], consumed),
        ):
            _, rows = await service.get_project_member_spend("atlas-core")

        consumed.assert_awaited_once()
        holders, _period = consumed.await_args[0]
        assert holders == [
            ProjectMemberHolder(project="atlas-core", category=BudgetCategory.CLI, user_id="u-1"),
            ProjectMemberHolder(project="atlas-core", category=BudgetCategory.CLI, user_id="u-2"),
        ]
        assert [r["cli"] for r in rows] == [7.5, 7.5]

    @pytest.mark.asyncio
    async def test_distinct_periods_get_one_call_each(self):
        service = MemberSpendService()
        consumed = _figures(cli=Decimal("1"), platform=Decimal("2"))
        allocations = [
            _allocation(category="cli", user_id="u-1", budget_id="b-cli"),
            _allocation(category="platform", user_id="u-1", budget_id="b-platform"),
        ]

        with (
            _patched_project_reads([SimpleNamespace(user_id="u-1", project_name="atlas-core")], allocations),
            _patched_port([_budget("b-cli"), _budget("b-platform", duration="1d")], consumed),
        ):
            _, rows = await service.get_project_member_spend("atlas-core")

        assert consumed.await_count == 2
        assert rows[0]["cli"] == 1.0
        assert rows[0]["platform"] == 2.0

    @pytest.mark.asyncio
    async def test_every_member_is_read_from_the_project_budget_reset(self):
        service = MemberSpendService()
        consumed = _figures(cli=Decimal("7.50"))
        members = [
            SimpleNamespace(user_id="u-1", project_name="atlas-core"),
            SimpleNamespace(user_id="u-2", project_name="atlas-core"),
        ]
        allocations = [_allocation(user_id="u-1"), _allocation(user_id="u-2")]

        with (
            _patched_project_reads(members, allocations),
            _patched_port([_budget(created_at=CREATED_MID_PERIOD, spend_reset_at=RESET_MID_PERIOD)], consumed),
        ):
            await service.get_project_member_spend("atlas-core")

        consumed.assert_awaited_once()
        holders, period = consumed.await_args[0]
        assert [holder.user_id for holder in holders] == ["u-1", "u-2"]
        assert period == TimeRange(RESET_MID_PERIOD, PERIOD.end)


class TestNoSessionHeldAcrossProviderOrLedger:
    """Both readers close their metadata session before the LiteLLM reconciliation and the ledger
    read: held across them, it kept a second pooled connection idle in transaction per request while
    the ledger read checked out its own (EPMCDME-15537)."""

    @staticmethod
    def _recording(refresh_overdue, sessions) -> tuple[dict, AsyncMock]:
        """Make the reconciliation and the port record how many sessions are open when called."""
        open_when_called = {}

        async def reconcile(budgets):
            open_when_called["refresh_overdue"] = sessions.open
            return set()

        async def read_ledger(holders, period):
            open_when_called["consumed_for_holders"] = sessions.open
            return {holder: SpendResult(figure=Decimal("1")) for holder in holders}

        refresh_overdue.side_effect = reconcile
        return open_when_called, AsyncMock(side_effect=read_ledger)

    @pytest.mark.asyncio
    async def test_user_project_spend(self, refresh_overdue, sessions):
        open_when_called, consumed = self._recording(refresh_overdue, sessions)

        with (
            _patched_user_reads([SimpleNamespace(project_name="atlas-core", user_id="u-1")], [_allocation()], set()),
            _patched_port([_budget()], consumed),
        ):
            await MemberSpendService().get_user_project_spend("u-1")

        assert open_when_called == {"refresh_overdue": 0, "consumed_for_holders": 0}
        assert len(sessions.sessions) == 1, "nothing moved, so nothing is re-read"

    @pytest.mark.asyncio
    async def test_project_member_spend(self, refresh_overdue, sessions):
        open_when_called, consumed = self._recording(refresh_overdue, sessions)

        with (
            _patched_project_reads([SimpleNamespace(user_id="u-1", project_name="atlas-core")], [_allocation()]),
            _patched_port([_budget()], consumed),
        ):
            await MemberSpendService().get_project_member_spend("atlas-core")

        assert open_when_called == {"refresh_overdue": 0, "consumed_for_holders": 0}
        assert len(sessions.sessions) == 1, "nothing moved, so nothing is re-read"


class TestPersonalProjectExclusion:
    """Personal projects are excluded: their spend is metered against the user, not the project."""

    @staticmethod
    def _patched(memberships, personal_projects: set[str]):
        stack = contextlib.ExitStack()
        stack.enter_context(_patched_user_reads(memberships, [], personal_projects))
        stack.enter_context(_patched_port([_budget()], _figures()))
        return stack

    @pytest.mark.asyncio
    async def test_personal_project_is_omitted(self):
        service = MemberSpendService()
        memberships = [
            SimpleNamespace(project_name="atlas-core", user_id="u-1"),
            SimpleNamespace(project_name="me@corp.com", user_id="u-1"),
        ]
        with self._patched(memberships, {"me@corp.com"}):
            _, rows = await service.get_user_project_spend("u-1")

        assert [r["project_name"] for r in rows] == ["atlas-core"]

    @pytest.mark.asyncio
    async def test_only_the_users_projects_are_checked_for_being_personal(self, sessions):
        """Loading every application to find the personal ones read ~45k rows per call (EPMCDME-15537)."""
        memberships = [
            SimpleNamespace(project_name="atlas-core", user_id="u-1"),
            SimpleNamespace(project_name="me@corp.com", user_id="u-1"),
        ]
        personal_lookup = AsyncMock(return_value={"me@corp.com"})

        with (
            self._patched(memberships, set()),
            patch(
                "codemie.repository.application_repository.application_repository.aget_personal_project_names",
                new=personal_lookup,
            ),
        ):
            _, rows = await MemberSpendService().get_user_project_spend("u-1")

        assert [r["project_name"] for r in rows] == ["atlas-core"]
        personal_lookup.assert_awaited_once_with(sessions.sessions[0], ["atlas-core", "me@corp.com"])

    @pytest.mark.asyncio
    async def test_personal_project_spend_is_never_requested(self):
        """The exclusion must reach the port too, not only the rendered rows."""
        service = MemberSpendService()
        consumed = _figures(cli=Decimal("40.00"))
        memberships = [SimpleNamespace(project_name="me@corp.com", user_id="u-1")]

        with (
            _patched_user_reads(memberships, [_allocation(project_name="me@corp.com")], {"me@corp.com"}),
            _patched_port([_budget()], consumed),
        ):
            _, rows = await service.get_user_project_spend("u-1")

        assert rows == []
        consumed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_project_type_field_is_emitted(self):
        """The field existed only to let clients suppress these rows; the backend does it now."""
        service = MemberSpendService()
        memberships = [SimpleNamespace(project_name="atlas-core", user_id="u-1")]

        with self._patched(memberships, set()):
            columns, rows = await service.get_user_project_spend("u-1")

        assert "project_type" not in rows[0]
        assert "project_type" not in [c["id"] for c in columns]

    @pytest.mark.asyncio
    async def test_user_with_only_a_personal_project_gets_empty_rows(self):
        service = MemberSpendService()
        memberships = [SimpleNamespace(project_name="me@corp.com", user_id="u-1")]

        with self._patched(memberships, {"me@corp.com"}):
            columns, rows = await service.get_user_project_spend("u-1")

        assert rows == []
        assert columns[0]["id"] == "project_name"

    @pytest.mark.asyncio
    async def test_unknown_project_is_kept_not_dropped(self):
        """Absence from applications must not silently remove a project the user belongs to."""
        service = MemberSpendService()
        memberships = [SimpleNamespace(project_name="ghost", user_id="u-1")]

        with self._patched(memberships, set()):
            _, rows = await service.get_user_project_spend("u-1")

        assert [r["project_name"] for r in rows] == ["ghost"]


async def _cli_member_row(result: SpendResult) -> dict:
    """One project-member row for a cli allocation, with the port answering `result`."""
    with (
        _patched_project_reads(
            [SimpleNamespace(user_id="u-7", project_name="atlas-core")],
            [_allocation(user_id="u-7")],
        ),
        _patched_port([_budget()], _results(cli=result)),
    ):
        _, rows = await MemberSpendService().get_project_member_spend("atlas-core")
    return rows[0]


class TestSpendAnnotationReachesTheRow:
    """The figure never travels alone: three states must stay tellable apart on the row."""

    @pytest.mark.asyncio
    async def test_an_exact_figure_is_annotated_as_exact(self):
        row = await _cli_member_row(SpendResult(figure=Decimal("40.00")))
        assert row["cli"] == 40.0
        assert row["cli_spend_unavailable"] is False
        assert row["cli_spend_partial"] is False
        assert row["cli_spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_a_no_figure_result_carries_no_number_and_says_unavailable(self):
        row = await _cli_member_row(SpendResult.no_figure(PERIOD))
        assert row["cli"] is None
        assert row["cli_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_truncated_total_keeps_its_number_and_still_says_unavailable(self):
        """The dangerous pair: drop the flag and a subtotal is presented as a total."""
        row = await _cli_member_row(SpendResult(figure=Decimal("40.00"), unavailable=SUB_FLOOR))
        assert row["cli"] == 40.0
        assert row["cli_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_computable_dimension_hole_reports_the_amount_missing(self):
        row = await _cli_member_row(
            SpendResult(
                figure=Decimal("40.00"),
                partial={SpendDimension.WORKFLOW: Portion(spend=Decimal("2.50"), rows=12)},
            )
        )
        assert row["cli"] == 40.0
        assert row["cli_spend_partial"] is True
        assert row["cli_spend_missing_amount"] == 2.5
        assert row["cli_spend_unavailable"] is False, "a dimension hole never makes a figure unavailable"

    @pytest.mark.asyncio
    async def test_a_project_hole_is_flagged_but_not_sized(self):
        """The project hole is sized over the whole project, not this member, so its amount would mislead."""
        row = await _cli_member_row(
            SpendResult(
                figure=Decimal("40.00"),
                partial={SpendDimension.PROJECT: Portion(spend=Decimal("2.50"), rows=12, sub_range=SUB_FLOOR)},
            )
        )
        assert row["cli"] == 40.0
        assert row["cli_spend_partial"] is True
        assert row["cli_spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_an_uncomputable_hole_reports_an_unknown_amount_never_a_zero(self):
        """The identity-bridge portion has neither an amount nor a row count to stand in for one."""
        row = await _cli_member_row(
            SpendResult(
                figure=Decimal("40.00"),
                partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
            )
        )
        assert row["cli_spend_partial"] is True
        assert row["cli_spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_the_annotation_is_per_category(self):
        """One row holds every category's figure, so one unsuffixed set could not say which it qualifies."""
        row = await _cli_member_row(SpendResult(figure=Decimal("40.00")))
        assert row["cli_spend_unavailable"] is False
        assert row["platform"] is None
        assert row["platform_spend_unavailable"] is True

    def test_the_annotation_keys_are_not_advertised_as_columns(self):
        """Per-row metadata, exactly like `<category>_limit` - not three raw booleans as headings."""
        ids = [c["id"] for c in _category_columns("user_id", "User")]
        assert "cli_spend_unavailable" not in ids
        assert "cli_spend_partial" not in ids
        assert "cli_spend_missing_amount" not in ids


class TestGetUserProjectRows:
    """The personal panel's project rows: same figures as the project table, narrowed and dated."""

    ENFORCING = "codemie.service.settings.settings.SettingsService.get_projects_enforcing_member_spend_limits"

    @staticmethod
    def _reads(*project_names: str):
        memberships = [SimpleNamespace(project_name=name) for name in project_names]
        allocations = [_allocation(project_name=name, category="platform") for name in project_names]
        return memberships, allocations

    @pytest.mark.asyncio
    async def test_enforced_only_keeps_the_projects_that_enforce_member_limits(self):
        memberships, allocations = self._reads("atlas-core", "beta")
        with (
            _patched_user_reads(memberships, allocations, set()),
            _patched_port([_budget()], _figures(platform=Decimal("3"))),
            patch(self.ENFORCING, return_value={"beta"}) as enforcing,
        ):
            rows = await MemberSpendService().get_user_project_rows("u-1", enforced_only=True)

        assert [row["project_name"] for row in rows] == ["beta"]
        assert rows[0]["platform"] == 3.0
        assert enforcing.call_args.args[0] == ["atlas-core", "beta"]

    @pytest.mark.asyncio
    async def test_no_enforcing_project_reads_no_spend(self):
        memberships, allocations = self._reads("atlas-core")
        consumed = _figures(platform=Decimal("3"))
        with (
            _patched_user_reads(memberships, allocations, set()),
            _patched_port([_budget()], consumed),
            patch(self.ENFORCING, return_value=set()),
        ):
            rows = await MemberSpendService().get_user_project_rows("u-1", enforced_only=True)

        assert rows == []
        consumed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_reset_dates_each_category(self):
        memberships, allocations = self._reads("atlas-core")
        with (
            _patched_user_reads(memberships, allocations, set()),
            _patched_port([_budget()], _figures(platform=Decimal("3"))),
            patch.object(
                MemberSpendService,
                "_reset_dates",
                new=AsyncMock(return_value={("atlas-core", "platform"): RESET}),
            ),
        ):
            (row,) = await MemberSpendService().get_user_project_rows("u-1", with_reset=True)

        assert row["platform_reset_at"] == RESET
        assert row["cli_reset_at"] is None

    @pytest.mark.asyncio
    async def test_without_reset_no_reset_keys(self):
        memberships, allocations = self._reads("atlas-core")
        with (
            _patched_user_reads(memberships, allocations, set()),
            _patched_port([_budget()], _figures(platform=Decimal("3"))),
        ):
            (row,) = await MemberSpendService().get_user_project_rows("u-1")

        assert "platform_reset_at" not in row


class TestResetDates:
    @staticmethod
    def _alloc(**overrides):
        fields = {
            "project_budget_id": "proj-budget",
            "user_id": "user-1",
            "allocation_mode": "shared",
            "effective_budget_id": None,
            "override_budget_id": None,
            "shared_budget_id": None,
            "budget_reset_at": "allocation-reset",
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    async def _reset_dates(self, alloc, budgets: dict):
        get_by_ids = AsyncMock(return_value=budgets)
        with patch("codemie.service.analytics.handlers.member_spend_service.budget_repository.get_by_ids", get_by_ids):
            result = await MemberSpendService._reset_dates(AsyncMock(), {("proj", "platform"): alloc})
        return result[("proj", "platform")], get_by_ids.await_args.args[1]

    @pytest.mark.asyncio
    async def test_reads_reset_from_explicit_effective_budget(self):
        alloc = self._alloc(effective_budget_id="eff", shared_budget_id="proj-budget:shared")
        reset, looked_up = await self._reset_dates(alloc, {"eff": SimpleNamespace(budget_reset_at="eff-reset")})
        assert looked_up == ["eff"]
        assert reset == "eff-reset"

    @pytest.mark.asyncio
    async def test_derives_shared_child_budget_when_ids_are_empty(self):
        alloc = self._alloc()
        reset, looked_up = await self._reset_dates(
            alloc, {"proj-budget:shared": SimpleNamespace(budget_reset_at="shared-reset")}
        )
        assert looked_up == ["proj-budget:shared"]
        assert reset == "shared-reset"

    @pytest.mark.asyncio
    async def test_derives_override_child_budget_for_fixed_allocation(self):
        alloc = self._alloc(allocation_mode="fixed")
        _, looked_up = await self._reset_dates(alloc, {})
        assert looked_up == ["proj-budget:user:user-1"]

    @pytest.mark.asyncio
    async def test_falls_back_to_allocation_reset_when_budget_missing(self):
        reset, _ = await self._reset_dates(self._alloc(), {})
        assert reset == "allocation-reset"


class TestEffectiveLimits:
    """The personal panel's limits: what the provider actually enforces, per the worked examples."""

    ENFORCING = "codemie.service.settings.settings.SettingsService.get_projects_enforcing_member_spend_limits"
    ACTIVE_PROJECT_BUDGETS = (
        "codemie.repository.project_budget_repository.project_budget_assignment_repository.get_active_for_projects"
    )

    @staticmethod
    def _project_budget(max_budget: float = 100.0, spend_reset_at: datetime | None = None):
        budget = _budget("pb-1", spend_reset_at=spend_reset_at)
        budget.max_budget = max_budget
        return budget

    @staticmethod
    def _fixed(max_budget: float):
        allocation = _allocation(project_name="shared", category="platform", max_budget=max_budget, budget_id="pb-1")
        allocation.allocation_mode = "fixed"
        allocation.budget_reset_at = None
        return allocation

    @staticmethod
    def _ledger(member: str, total: str | None):
        """Member holders read `member`; the project holder reads `total`, or no figure when None."""
        from codemie.service.spend.ledger import ProjectHolder

        async def _call(holders, period):
            return {
                holder: (
                    (SpendResult(figure=Decimal(total)) if total is not None else SpendResult.no_figure(period))
                    if isinstance(holder, ProjectHolder)
                    else SpendResult(figure=Decimal(member))
                )
                for holder in holders
            }

        return AsyncMock(side_effect=_call)

    async def _row(self, allocations, member, total, *, enforcing=frozenset(), active=True, project="shared"):
        assignments = (
            [SimpleNamespace(project_name=project, budget_category="platform", budget_id="pb-1")] if active else []
        )
        with (
            _patched_user_reads([SimpleNamespace(project_name=project)], allocations, set()),
            _patched_port([self._project_budget()], self._ledger(member, total)),
            patch(self.ENFORCING, return_value=set(enforcing)),
            patch(self.ACTIVE_PROJECT_BUDGETS, new=AsyncMock(return_value=assignments)),
        ):
            return await MemberSpendService().get_user_project_rows("u-1", effective_limits=True, with_reset=True)

    @pytest.mark.asyncio
    async def test_non_override_member_is_measured_against_the_project_total(self):
        (row,) = await self._row([], "50", "50")

        assert row["platform"] == 50.0
        assert row["platform_limit"] == 100.0
        assert row["platform_percent_spend"] == 50.0
        assert row["platform_reset_at"] == RESET

    @pytest.mark.asyncio
    async def test_absent_project_total_leaves_the_bar_numerator_absent(self):
        (row,) = await self._row([], "50", None)

        assert row["platform_limit"] == 100.0
        assert row["platform_percent_spend"] is None

    @pytest.mark.asyncio
    async def test_fixed_override_is_capped_by_what_is_left_of_the_project_budget(self):
        (row,) = await self._row([self._fixed(40.0)], "10", "90")

        assert row["platform_limit"] == 20.0
        assert "platform_percent_spend" not in row

    @pytest.mark.asyncio
    async def test_fixed_override_cap_not_binding(self):
        (row,) = await self._row([self._fixed(40.0)], "10", "30")

        assert row["platform_limit"] == 40.0

    @pytest.mark.asyncio
    async def test_fixed_override_over_budget_limit_equals_member_spend(self):
        (row,) = await self._row([self._fixed(40.0)], "10", "120")

        assert row["platform_limit"] == 10.0

    @pytest.mark.asyncio
    async def test_fixed_override_without_a_project_total_keeps_the_override(self):
        (row,) = await self._row([self._fixed(40.0)], "10", None)

        assert row["platform_limit"] == 40.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("spend_reset_at", "expected_period"),
        [
            pytest.param(RESET_MID_PERIOD, TimeRange(RESET_MID_PERIOD, PERIOD.end), id="reset-mid-period"),
            pytest.param(RESET_BEFORE_PERIOD, PERIOD, id="reset-in-an-earlier-period"),
        ],
    )
    async def test_member_and_project_total_are_read_from_the_project_budget_reset(
        self, spend_reset_at, expected_period
    ):
        from codemie.service.spend.ledger import ProjectHolder

        ledger = self._ledger("10", "50")
        assignments = [SimpleNamespace(project_name="shared", budget_category="platform", budget_id="pb-1")]
        with (
            _patched_user_reads([SimpleNamespace(project_name="shared")], [], set()),
            _patched_port([self._project_budget(spend_reset_at=spend_reset_at)], ledger),
            patch(self.ENFORCING, return_value=set()),
            patch(self.ACTIVE_PROJECT_BUDGETS, new=AsyncMock(return_value=assignments)),
        ):
            await MemberSpendService().get_user_project_rows("u-1", effective_limits=True, with_reset=True)

        periods = {type(holder): call.args[1] for call in ledger.await_args_list for holder in call.args[0]}
        assert periods == {ProjectMemberHolder: expected_period, ProjectHolder: expected_period}

    @pytest.mark.asyncio
    async def test_unenforced_project_without_a_budget_is_hidden(self):
        assert await self._row([], "10", "10", active=False) == []

    @pytest.mark.asyncio
    async def test_enforced_project_keeps_the_allocated_limit(self):
        allocation = _allocation(project_name="shared", category="platform", max_budget=50.0, budget_id="pb-1")
        allocation.budget_reset_at = None

        (row,) = await self._row([allocation], "10", "90", enforcing={"shared"}, active=False)

        assert row["platform_limit"] == 50.0
        assert "platform_percent_spend" not in row
