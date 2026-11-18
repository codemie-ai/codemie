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

"""Who Pays: ledger money per request, in the bucket the classification gives it, footing on the cards."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.usage_attribution.spend_widgets import WhoPaysFigures, WhoPaysSpend, WhoPaysSplit, widgets

from .conftest import END, FLOOR, SCOPE, START, FakeCache, context, coverage, groups, unavailable

CLASSIFICATION = {
    "cli": {"c-cb": ("cli", "charged_back"), "c-sh": ("cli", "shared_no_budget"), "c-pr": ("cli", "personal"),
            "c-nc": ("cli", "no_customer")},
    "web": {"w-cb": ("web", "charged_back"), "w-sh": ("web", "shared_no_budget"), "w-pr": ("web", "personal")},
}  # fmt: skip
#: The ledger's money per request id; None is money without a request id.
LEDGER = {
    "cli": {"c-cb": "1.0", "c-sh": "2.0", "c-pr": "0.5", "c-nc": "0.25", "c-unseen": "0.75", None: "0.5"},
    "web": {"w-cb": "0.5", "w-sh": "1.0", "w-pr": "0.25"},
}


def session():
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.fixture
def ledger():
    async def by_request_id(_session, _window, *, cli, access):
        return {k: Decimal(v) for k, v in LEDGER["cli" if cli else "web"].items()}

    read = AsyncMock(side_effect=by_request_id)
    with (
        patch.object(widgets.spend_logs_repository, "sum_by_request_id", read),
        patch.object(widgets, "get_ledger_async_session", side_effect=lambda: session()),
    ):
        yield read


def classifier():
    async def classify(_window, channel):
        return CLASSIFICATION[channel]

    return AsyncMock(side_effect=classify)


@pytest.mark.asyncio
class TestWhoPaysSpend:
    async def test_each_channel_foots_on_its_card_with_the_rest_unclassified(self, port, ledger):
        with coverage():
            out = await WhoPaysSpend(context(), classify=classifier()).read(START, END, "k")
        # Cards: platform 4.0, CLI 6.0 (conftest.groups).
        assert out.cli == WhoPaysSplit(charged=1.0, shared=2.0, personal=0.5, unclassified=2.5, no_customer=0.25)
        assert out.web == WhoPaysSplit(charged=0.5, shared=1.0, personal=0.25, unclassified=2.25, no_customer=0.0)

    async def test_the_ledger_is_read_over_the_cards_window_under_the_callers_scope(self, port, ledger):
        classify = classifier()
        with coverage():
            await WhoPaysSpend(context(), classify=classify).read(START, END, "k")
        window = TimeRange(FLOOR, END)
        assert [c.kwargs for c in ledger.await_args_list] == [
            {"cli": False, "access": SCOPE},
            {"cli": True, "access": SCOPE},
        ]
        assert all(c.args[1] == window for c in ledger.await_args_list)
        assert [c.args for c in classify.await_args_list] == [(window, "web"), (window, "cli")]

    async def test_a_cached_answer_does_not_classify_again(self, port, ledger):
        cache, classify = FakeCache(), classifier()
        with coverage():
            first = await WhoPaysSpend(context(cache), classify=classify).read(START, END, "k")
            second = await WhoPaysSpend(context(cache), classify=classify).read(START, END, "k")
        assert first == second
        assert classify.await_count == 2  # web and CLI, once

    async def test_a_window_without_a_ledger_has_no_figures_and_no_classification(self, port, ledger):
        classify = classifier()
        with coverage(floor=END):
            out = await WhoPaysSpend(context(), classify=classify).read(START, END, "k")
        assert out == WhoPaysFigures()
        classify.assert_not_awaited()
        ledger.assert_not_awaited()

    async def test_a_card_without_a_figure_has_no_split_and_is_not_cached(self, port, ledger):
        answer = groups()
        answer[SpendGroup.CLI] = unavailable(TimeRange(FLOOR, END))[SpendGroup.CLI]
        port.aggregate_by_spend_group.return_value = answer
        cache = FakeCache()
        with coverage():
            out = await WhoPaysSpend(context(cache), classify=classifier()).read(START, END, "k")
        assert out.cli == WhoPaysSplit() and out.web.charged == 0.5
        assert not any(":who_pays:" in key for key in cache.store)
