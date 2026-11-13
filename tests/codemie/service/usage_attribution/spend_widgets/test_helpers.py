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

"""The helpers several spend widgets, or the handler, share."""

import pytest

from codemie.service.analytics.handlers.ledger_group_spend import TableFilters
from codemie.service.spend.coverage import SpendResult, TimeRange
from codemie.service.spend.ledger import ReportFilter, UserTerm
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.usage_attribution.spend_widgets import SpendFigures, helpers

from .conftest import END, FLOOR, SCOPE, groups, unavailable, user_filters

WINDOW = TimeRange(FLOOR, END)


def test_round_cents_keeps_no_figure_as_none():
    assert helpers.round_cents(None) is None
    assert helpers.round_cents(1.234) == 1.23


class TestRendered:
    def test_a_full_answer_is_complete_bare_figures(self):
        assert helpers.rendered(groups()) == SpendFigures(total=10.0, platform=4.0, cli=6.0, embedding=1.0)
        assert helpers.rendered(groups()).complete

    def test_the_figures_are_in_cents(self):
        assert helpers.rendered(groups(total=10.126, platform=4.004)).total == 10.13
        assert helpers.rendered(groups(total=10.126, platform=4.004)).platform == 4.0

    def test_an_unavailable_answer_is_none_and_incomplete(self):
        figures = helpers.rendered(unavailable(WINDOW))
        assert figures == SpendFigures() and not figures.complete

    def test_one_unavailable_group_makes_the_answer_incomplete(self):
        answer = groups()
        answer[SpendGroup.EMBEDDING] = SpendResult.no_figure(WINDOW)
        figures = helpers.rendered(answer)
        assert figures.embedding is None and figures.total == 10.0 and not figures.complete


@pytest.mark.asyncio
class TestReadPerRow:
    async def test_many_rows_are_read_in_batches(self, port):
        uids = [f"u{i}" for i in range(150)]
        table = await helpers.read_per_row(WINDOW, SCOPE, uids, user_filters(uids))
        assert port.aggregate_by_spend_group_per_filter.await_count == 2
        assert set(table.rows) == set(uids)
        assert all(table.of(u).total is not None for u in uids)

    async def test_a_row_naming_nobody_has_no_figure_and_keeps_the_table_complete(self, port):
        filters = TableFilters(ReportFilter(), [ReportFilter(users=(UserTerm("a", frozenset()),)), None])
        table = await helpers.read_per_row(WINDOW, SCOPE, ["a", ""], filters)
        assert table.of("") == SpendFigures() and table.of("a").total == 1.0
        assert table.complete

    async def test_an_unavailable_row_makes_the_table_incomplete(self, port):
        async def failing(period, *, access, common_filter, report_filters):
            return {f: unavailable(WINDOW) for f in report_filters}

        port.aggregate_by_spend_group_per_filter.side_effect = failing
        table = await helpers.read_per_row(WINDOW, SCOPE, ["a"], user_filters(["a"]))
        assert table.of("a") == SpendFigures() and not table.complete
