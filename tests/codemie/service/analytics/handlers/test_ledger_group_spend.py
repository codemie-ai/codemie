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

"""Unit tests for the per-row ledger reads behind the CLI Insights and by-user spending tables."""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.analytics.handlers import ledger_group_spend
from codemie.service.analytics.handlers.ledger_group_spend import (
    dashboard_group_spend,
    project_filters_within,
    project_report_filters,
    TableFilters,
    read_group_spend,
    user_report_filters,
)
from codemie.service.spend.coverage import SpendResult, TimeRange
from codemie.service.spend.ledger import AccessScope, ReportFilter, UserTerm
from codemie.service.spend.spend_group import SpendGroup

PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 8, 31, tzinfo=timezone.utc))
ACCESS = AccessScope(unrestricted=False, user_id="caller")
_MODULE = "codemie.service.analytics.handlers.ledger_group_spend"


def _term(identifier: str) -> UserTerm:
    return UserTerm(user_id=f"id-{identifier}", aliases=frozenset({identifier}))


def _fake_report_filter_for(owners: tuple[str, ...] | None = ()) -> AsyncMock:
    """`report_filter_for` without the identity lookup: one term per distinct non-empty identifier."""

    async def _build(users, projects):
        terms = tuple(_term(identifier) for identifier in dict.fromkeys(user for user in users or () if user))
        return ReportFilter(users=terms, projects=tuple(projects or ()), personal_project_owners=owners)

    return AsyncMock(side_effect=_build)


@contextlib.asynccontextmanager
async def _fake_session():
    yield MagicMock()


class TestUserReportFilters:
    @pytest.mark.asyncio
    async def test_each_identifier_gets_a_filter_naming_only_that_person_in_order(self):
        with patch(f"{_MODULE}.report_filter_for", new=_fake_report_filter_for()):
            table = await user_report_filters(["ada", "bob", "ada"], ["alpha"])

        assert table == TableFilters(
            common=ReportFilter(projects=("alpha",), personal_project_owners=()),
            rows=[
                ReportFilter(users=(_term("ada"),), projects=("alpha",), personal_project_owners=()),
                ReportFilter(users=(_term("bob"),), projects=("alpha",), personal_project_owners=()),
                ReportFilter(users=(_term("ada"),), projects=("alpha",), personal_project_owners=()),
            ],
        )

    @pytest.mark.asyncio
    async def test_an_empty_identifier_gets_no_filter_because_a_filter_naming_nobody_means_everyone(self):
        with patch(f"{_MODULE}.report_filter_for", new=_fake_report_filter_for()):
            table = await user_report_filters(["ada", ""], None)

        assert table.rows == [ReportFilter(users=(_term("ada"),)), None]

    @pytest.mark.asyncio
    async def test_nobody_named_resolves_nothing(self):
        build = _fake_report_filter_for()

        with patch(f"{_MODULE}.report_filter_for", new=build):
            table = await user_report_filters(["", ""], ["alpha"])

        assert table == TableFilters(common=ReportFilter(), rows=[None, None])
        build.assert_not_awaited()


class TestProjectReportFilters:
    @staticmethod
    def _owners(found: dict[str, set[str | None]]):
        return patch.object(
            ledger_group_spend.application_repository,
            "aget_personal_project_owners_by_name",
            new=AsyncMock(return_value=found),
        )

    @pytest.mark.asyncio
    async def test_each_filter_carries_its_own_projects_owners_and_the_dashboards_users(self):
        found = {"ada@example.com": {"u-ada"}, "orphan@example.com": {None}}

        with (
            patch(f"{_MODULE}.report_filter_for", new=_fake_report_filter_for()),
            patch(f"{_MODULE}.get_async_session", _fake_session),
            self._owners(found),
        ):
            table = await project_report_filters(["team", "ada@example.com", "orphan@example.com", ""], ["bob"])

        users = (_term("bob"),)
        assert table == TableFilters(
            common=ReportFilter(users=users),
            rows=[
                # Not a personal project: matched by its name alone.
                ReportFilter(users=users, projects=("team",), personal_project_owners=()),
                # A personal project: its calls carry no project, so the port matches it through its owner.
                ReportFilter(users=users, projects=("ada@example.com",), personal_project_owners=("u-ada",)),
                # A personal project with no recorded owner: nobody can say, which the port labels.
                ReportFilter(users=users, projects=("orphan@example.com",), personal_project_owners=None),
                None,
            ],
        )

    @pytest.mark.asyncio
    async def test_a_failed_owner_lookup_leaves_every_project_labelled_rather_than_failing_the_table(self):
        failing = patch.object(
            ledger_group_spend.application_repository,
            "aget_personal_project_owners_by_name",
            new=AsyncMock(side_effect=RuntimeError("database is down")),
        )

        with (
            patch(f"{_MODULE}.report_filter_for", new=_fake_report_filter_for()),
            patch(f"{_MODULE}.get_async_session", _fake_session),
            failing,
        ):
            table = await project_report_filters(["team"], None)

        assert table.rows == [ReportFilter(projects=("team",), personal_project_owners=None)]


class TestProjectFiltersWithin:
    @pytest.mark.asyncio
    async def test_a_filter_built_by_the_caller_is_reused_with_its_users_kept_and_its_projects_replaced(self):
        base = ReportFilter(users=(_term("bob"),), projects=("dashboard-project",), personal_project_owners=("u-x",))

        with (
            patch(f"{_MODULE}.report_filter_for", new=AsyncMock(side_effect=AssertionError("built a second filter"))),
            patch(f"{_MODULE}.get_async_session", _fake_session),
            patch.object(
                ledger_group_spend.application_repository,
                "aget_personal_project_owners_by_name",
                new=AsyncMock(return_value={"ada@example.com": {"u-ada"}}),
            ),
        ):
            table = await project_filters_within(base, ["team", "ada@example.com"])

        assert table == TableFilters(
            common=ReportFilter(users=(_term("bob"),)),
            rows=[
                ReportFilter(users=(_term("bob"),), projects=("team",), personal_project_owners=()),
                ReportFilter(users=(_term("bob"),), projects=("ada@example.com",), personal_project_owners=("u-ada",)),
            ],
        )

    @pytest.mark.asyncio
    async def test_an_empty_name_gets_no_filter_and_nothing_is_looked_up(self):
        lookup = AsyncMock()

        with patch.object(
            ledger_group_spend.application_repository, "aget_personal_project_owners_by_name", new=lookup
        ):
            table = await project_filters_within(ReportFilter(), ["", ""])

        assert table == TableFilters(common=ReportFilter(), rows=[None, None])
        lookup.assert_not_awaited()


ALPHA = ReportFilter(projects=("alpha",))
ADA_IN_ALPHA = ReportFilter(users=(_term("ada"),), projects=("alpha",))
BOB_IN_ALPHA = ReportFilter(users=(_term("bob"),), projects=("alpha",))


class TestReadGroupSpend:
    @staticmethod
    def _port(per_filter: dict[ReportFilter, dict[SpendGroup, SpendResult]]) -> AsyncMock:
        async def _call(period, *, access, common_filter, report_filters):
            return {
                report_filter: {group: SpendResult.no_figure(period) for group in SpendGroup}
                | per_filter.get(report_filter, {})
                for report_filter in report_filters
            }

        return AsyncMock(side_effect=_call)

    @staticmethod
    def _patched(port: AsyncMock):
        return patch(f"{_MODULE}.spend_service.aggregate_by_spend_group_per_filter", new=port)

    @pytest.mark.asyncio
    async def test_one_read_answers_every_row_with_its_own_figure_in_row_order(self):
        port = self._port(
            {
                ADA_IN_ALPHA: {
                    SpendGroup.CLI: SpendResult(figure=Decimal("1.5")),
                    SpendGroup.PLATFORM: SpendResult(figure=Decimal("9")),
                },
                BOB_IN_ALPHA: {SpendGroup.CLI: SpendResult(figure=Decimal("2.5"))},
            }
        )

        with self._patched(port):
            figures = await read_group_spend(PERIOD, ACCESS, ALPHA, [BOB_IN_ALPHA, ADA_IN_ALPHA], SpendGroup.CLI)

        assert [figure.figure for figure in figures] == [2.5, 1.5]
        port.assert_awaited_once_with(
            PERIOD, access=ACCESS, common_filter=ALPHA, report_filters=[BOB_IN_ALPHA, ADA_IN_ALPHA]
        )

    @pytest.mark.asyncio
    async def test_a_repeated_filter_is_asked_once_and_every_row_shows_its_figure(self):
        port = self._port({ADA_IN_ALPHA: {SpendGroup.CLI: SpendResult(figure=Decimal("1.5"))}})

        with self._patched(port):
            figures = await read_group_spend(PERIOD, ACCESS, ALPHA, [ADA_IN_ALPHA, ADA_IN_ALPHA], SpendGroup.CLI)

        assert [figure.figure for figure in figures] == [1.5, 1.5]
        assert port.await_args.kwargs["report_filters"] == [ADA_IN_ALPHA]

    @pytest.mark.asyncio
    async def test_a_row_naming_nobody_is_no_figure_and_is_left_out_of_the_read(self):
        port = self._port({ADA_IN_ALPHA: {SpendGroup.CLI: SpendResult(figure=Decimal("1.5"))}})

        with self._patched(port):
            figures = await read_group_spend(PERIOD, ACCESS, ALPHA, [None, ADA_IN_ALPHA], SpendGroup.CLI)

        assert [(figure.figure, figure.unavailable) for figure in figures] == [(None, True), (1.5, False)]
        assert port.await_args.kwargs["report_filters"] == [ADA_IN_ALPHA]

    @pytest.mark.asyncio
    async def test_rows_naming_nobody_ask_the_port_nothing(self):
        port = self._port({})

        with self._patched(port):
            figures = await read_group_spend(PERIOD, ACCESS, ALPHA, [None, None], SpendGroup.CLI)

        port.assert_not_awaited()
        assert [(figure.figure, figure.unavailable) for figure in figures] == [(None, True), (None, True)]

    @pytest.mark.asyncio
    async def test_an_unreadable_window_asks_the_port_nothing(self):
        port = self._port({})

        with self._patched(port):
            figures = await read_group_spend(None, ACCESS, ALPHA, [ADA_IN_ALPHA], SpendGroup.CLI)

        port.assert_not_awaited()
        assert [(figure.figure, figure.unavailable) for figure in figures] == [(None, True)]

    @pytest.mark.asyncio
    async def test_a_port_with_no_answer_for_a_group_is_no_figure_never_zero(self):
        ada = ReportFilter(users=(_term("ada"),))

        with self._patched(self._port({ada: {}})):
            figures = await read_group_spend(PERIOD, ACCESS, ReportFilter(), [ada], SpendGroup.CLI)

        assert [(figure.figure, figure.unavailable) for figure in figures] == [(None, True)]

    @pytest.mark.parametrize(
        ("common", "stray"),
        [
            pytest.param(ALPHA, ReportFilter(users=(_term("ada"),), projects=("beta",)), id="other-projects"),
            pytest.param(
                ALPHA,
                ReportFilter(users=(_term("ada"),), projects=("alpha",), personal_project_owners=None),
                id="other-owners",
            ),
            pytest.param(
                ReportFilter(users=(_term("bob"),)),
                ReportFilter(users=(_term("ada"),), projects=("team",)),
                id="other-users",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_a_filter_not_carrying_the_common_term_raises_before_any_read(self, common, stray):
        port = self._port({})

        with self._patched(port), pytest.raises(ValueError):
            await read_group_spend(PERIOD, ACCESS, common, [stray], SpendGroup.CLI)

        port.assert_not_awaited()


class TestDashboardGroupSpend:
    @pytest.mark.asyncio
    async def test_reads_the_group_for_the_dashboards_own_filter_under_the_callers_boundary(self):
        dashboard = ReportFilter(users=(_term("ada"),), projects=("alpha",))
        port = AsyncMock(
            return_value={group: SpendResult.no_figure(PERIOD) for group in SpendGroup}
            | {SpendGroup.CLI: SpendResult(figure=Decimal("4.25"))}
        )

        with (
            patch(f"{_MODULE}.report_filter_for", new=AsyncMock(return_value=dashboard)),
            patch(f"{_MODULE}.spend_service.aggregate_by_spend_group", new=port),
        ):
            figure = await dashboard_group_spend(PERIOD, ACCESS, ["ada"], ["alpha"], SpendGroup.CLI)

        assert figure.figure == 4.25
        port.assert_awaited_once_with(PERIOD, access=ACCESS, report_filter=dashboard)

    @pytest.mark.asyncio
    async def test_a_window_the_port_cannot_accept_is_no_figure_and_nothing_is_resolved(self):
        build = AsyncMock()
        port = AsyncMock()

        with (
            patch(f"{_MODULE}.report_filter_for", new=build),
            patch(f"{_MODULE}.spend_service.aggregate_by_spend_group", new=port),
        ):
            figure = await dashboard_group_spend(None, ACCESS, None, None, SpendGroup.CLI)

        assert figure.figure is None
        assert figure.unavailable is True
        build.assert_not_awaited()
        port.assert_not_awaited()
