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

"""The spend widgets: clipped to the coverage floor, read under the caller's boundary, one engine each."""

from unittest.mock import AsyncMock, patch

import pytest

from codemie.service.analytics.handlers.ledger_group_spend import TableFilters
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.ledger import ReportFilter, UserTerm
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.usage_attribution.constants import CACHE_NAMESPACE
from codemie.service.usage_attribution.spend_widgets import (
    PeopleSpend,
    PlatformSpend,
    ProjectSpend,
    SpendFigures,
    SpendTable,
    widgets,
)

from .conftest import END, FLOOR, LATER, OTHER_SCOPE, SCOPE, START, FakeCache, context, coverage, groups
from .conftest import project_filters, unavailable, user_filters


@pytest.mark.asyncio
class TestPlatformSpend:
    async def test_a_window_crossing_the_floor_is_read_from_the_floor_under_the_callers_scope(self, port):
        with coverage():
            out = await PlatformSpend(context()).read(START, END)
        assert out == SpendFigures(total=10.0, platform=4.0, cli=6.0, embedding=1.0)
        port.aggregate_by_spend_group.assert_awaited_once_with(TimeRange(FLOOR, END), access=SCOPE, report_filter=None)

    @pytest.mark.parametrize("kwargs", [{"floor": END}, {"ready": False}])
    async def test_a_window_without_a_ledger_has_no_figure_and_no_port_call(self, port, kwargs):
        with coverage(**kwargs):
            out = await PlatformSpend(context()).read(START, END)
        assert out == SpendFigures()
        port.aggregate_by_spend_group.assert_not_awaited()

    async def test_an_unavailable_answer_is_none_never_zero(self, port):
        port.aggregate_by_spend_group.return_value = unavailable(TimeRange(FLOOR, END))
        with coverage():
            out = await PlatformSpend(context()).read(START, END)
        assert out == SpendFigures() and not out.complete

    async def test_one_unavailable_group_keeps_the_answer_out_of_the_cache(self, port):
        cache = FakeCache()
        answer = groups()
        answer[SpendGroup.EMBEDDING] = unavailable(TimeRange(FLOOR, END))[SpendGroup.EMBEDDING]
        port.aggregate_by_spend_group.return_value = answer
        with coverage():
            out = await PlatformSpend(context(cache)).read(START, END)
        assert out.embedding is None and out.total == 10.0
        assert cache.store == {}

    async def test_a_second_read_is_served_from_the_cache(self, port):
        cache = FakeCache()
        with coverage():
            first = await PlatformSpend(context(cache)).read(START, END)
            second = await PlatformSpend(context(cache)).read(START, END)
        assert first == second == SpendFigures(total=10.0, platform=4.0, cli=6.0, embedding=1.0)
        assert port.aggregate_by_spend_group.await_count == 1
        (key,) = cache.store
        assert key.startswith(f"{CACHE_NAMESPACE}:ledger:")

    async def test_another_access_scope_is_read_under_its_own_boundary(self, port):
        cache = FakeCache()
        with coverage():
            await PlatformSpend(context(cache, SCOPE)).read(START, END)
            await PlatformSpend(context(cache, OTHER_SCOPE)).read(START, END)
        assert port.aggregate_by_spend_group.await_count == 2
        assert port.aggregate_by_spend_group.await_args.kwargs["access"] == OTHER_SCOPE

    async def test_another_window_is_read_on_its_own(self, port):
        cache = FakeCache()
        with coverage():
            await PlatformSpend(context(cache)).read(START, END)
            await PlatformSpend(context(cache)).read(START, LATER)
        assert port.aggregate_by_spend_group.await_count == 2


@pytest.mark.asyncio
class TestPeopleSpend:
    async def test_many_people_share_one_grouped_read(self, port):
        uids = [f"u{i}" for i in range(250)]
        filters = user_filters(uids)
        with coverage(), patch.object(widgets, "user_report_filters", AsyncMock(return_value=filters)):
            out = await PeopleSpend(context()).read(START, END, uids)

        port.aggregate_by_spend_group_per_filter.assert_awaited_once_with(
            TimeRange(FLOOR, END), access=SCOPE, common_filter=filters.common, report_filters=filters.rows
        )
        assert len(out.rows) == 250
        assert out.of(uids[-1]).total == 250.0

    async def test_people_over_a_window_crossing_the_floor(self, port):
        with (
            coverage(),
            patch.object(widgets, "user_report_filters", AsyncMock(return_value=user_filters(["a", "b"]))) as built,
        ):
            out = await PeopleSpend(context()).read(START, END, ["a", "b"])
        built.assert_awaited_once_with(["a", "b"], None)
        assert isinstance(out, SpendTable)
        assert out.of("a").total == 1.0 and out.of("b").total == 2.0
        assert isinstance(out.of("a").cli, float)
        call = port.aggregate_by_spend_group_per_filter.await_args
        assert call.args[0] == TimeRange(FLOOR, END)
        assert call.kwargs["access"] == SCOPE

    async def test_people_wholly_below_the_floor_have_no_figure_and_no_port_call(self, port):
        built = AsyncMock()
        with coverage(floor=END), patch.object(widgets, "user_report_filters", built):
            out = await PeopleSpend(context()).read(START, END, ["a"])
        assert out.rows == {"a": SpendFigures()}
        port.aggregate_by_spend_group_per_filter.assert_not_awaited()
        built.assert_not_awaited()

    async def test_nobody_is_not_read(self, port):
        built = AsyncMock()
        with coverage(), patch.object(widgets, "user_report_filters", built):
            out = await PeopleSpend(context()).read(START, END, [])
        assert out.rows == {}
        built.assert_not_awaited()

    async def test_a_row_naming_nobody_has_no_figure_and_does_not_keep_the_answer_out_of_the_cache(self, port):
        cache = FakeCache()
        filters = TableFilters(ReportFilter(), [ReportFilter(users=(UserTerm("a", frozenset()),)), None])
        with coverage(), patch.object(widgets, "user_report_filters", AsyncMock(return_value=filters)):
            out = await PeopleSpend(context(cache)).read(START, END, ["a", ""])
        assert out.of("") == SpendFigures() and out.of("a").total == 1.0
        assert len(cache.store) == 1

    async def test_an_unavailable_row_keeps_the_answer_out_of_the_cache(self, port):
        cache = FakeCache()

        async def failing(period, *, access, common_filter, report_filters):
            return {f: unavailable(period) for f in report_filters}

        port.aggregate_by_spend_group_per_filter.side_effect = failing
        with coverage(), patch.object(widgets, "user_report_filters", AsyncMock(return_value=user_filters(["a"]))):
            out = await PeopleSpend(context(cache)).read(START, END, ["a"])
        assert out.of("a") == SpendFigures()
        assert cache.store == {}

    async def test_other_names_and_scopes_are_cached_on_their_own(self, port):
        cache = FakeCache()
        with (
            coverage(),
            patch.object(widgets, "user_report_filters", AsyncMock(side_effect=lambda keys, _p: user_filters(keys))),
        ):
            await PeopleSpend(context(cache)).read(START, END, ["a"])
            await PeopleSpend(context(cache)).read(START, END, ["a"])
            out = await PeopleSpend(context(cache)).read(START, END, ["b"])
            await PeopleSpend(context(cache, OTHER_SCOPE)).read(START, END, ["a"])
        assert set(out.rows) == {"b"}
        assert port.aggregate_by_spend_group_per_filter.await_count == 3
        assert len(cache.store) == 3


@pytest.mark.asyncio
class TestProjectSpend:
    async def test_projects_read_each_project(self, port):
        built = AsyncMock(return_value=project_filters(["p1", "p2"]))
        with coverage(), patch.object(widgets, "project_report_filters", built):
            out = await ProjectSpend(context()).read(START, END, ["p1", "p2"])
        built.assert_awaited_once_with(["p1", "p2"], None)
        assert out.of("p1").total == 1.0 and out.of("p2").total == 2.0

    async def test_projects_without_a_window_have_no_figure_and_no_port_call(self, port):
        with coverage(floor=END):
            out = await ProjectSpend(context()).read(START, END, ["p1"])
        assert out.rows == {"p1": SpendFigures()}
        port.aggregate_by_spend_group_per_filter.assert_not_awaited()

    async def test_a_second_read_is_served_from_the_cache_under_the_projects_view(self, port):
        cache = FakeCache()
        built = AsyncMock(side_effect=lambda keys, _p: project_filters(keys))
        with coverage(), patch.object(widgets, "project_report_filters", built):
            first = await ProjectSpend(context(cache)).read(START, END, ["a"])
            second = await ProjectSpend(context(cache)).read(START, END, ["a"])
        assert first == second
        assert port.aggregate_by_spend_group_per_filter.await_count == 1
        (key,) = cache.store
        assert ":projects:" in key
