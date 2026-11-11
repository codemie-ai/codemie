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

"""Unit tests for pricing Elasticsearch-listed requests from the spend ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from codemie.configs import config
from codemie.service.analytics.handlers.money_coverage import SETTLED_AGG, UNSETTLED_AGG
from codemie.service.analytics.handlers.request_spend import (
    FIRST_SEEN_AGG,
    MONEY_SUM_AGG,
    RequestUsage,
    collect_request_usage,
    price_requests,
)
from codemie.service.spend.coverage import LedgerCoverage
from codemie.service.spend.ledger import RequestCost
from codemie.service.spend.rendering import RenderedSpend

COSTS_PORT = "codemie.service.analytics.handlers.request_spend.spend_service.costs_for_requests"
REGISTERED_COVERAGE = "codemie.service.analytics.handlers.request_spend.ledger_coverage"

FLOOR = datetime(2026, 8, 15, tzinfo=timezone.utc)
BEFORE_FLOOR = datetime(2026, 8, 1, tzinfo=timezone.utc)
AFTER_FLOOR = datetime(2026, 8, 20, tzinfo=timezone.utc)

EXACT = RenderedSpend(figure=1.5, unavailable=False, partial=False, missing_amount=None)
NOTHING_SETTLED = RenderedSpend(figure=None, unavailable=True, partial=False, missing_amount=None)


def _bucket(key: dict, first_seen: datetime, money: float | None = 1.5, settled: int = 1, unsettled: int = 0) -> dict:
    return {
        "key": key,
        "doc_count": settled + unsettled,
        FIRST_SEEN_AGG: {"value": first_seen.timestamp() * 1000},
        MONEY_SUM_AGG: {"value": money},
        SETTLED_AGG: {"value": settled},
        UNSETTLED_AGG: {"doc_count": unsettled},
    }


def _page(*buckets: dict, after_key: dict | None = None) -> dict:
    composite: dict = {"buckets": list(buckets)}
    if after_key is not None:
        composite["after_key"] = after_key
    return {"aggregations": {"requests": composite}}


class TestCollectRequestUsage:
    @pytest.mark.asyncio
    async def test_pages_the_composite_until_exhausted_under_the_given_query(self):
        search = AsyncMock(
            side_effect=[
                _page(_bucket({"group": "a", "id_0": "r-1"}, AFTER_FLOOR), after_key={"group": "a", "id_0": "r-1"}),
                _page(_bucket({"group": "b", "id_0": "r-2"}, AFTER_FLOOR), after_key={"group": "b", "id_0": "r-2"}),
                _page(),
            ]
        )
        query = {"bool": {"filter": [{"term": {"x": "y"}}]}}

        usage = await collect_request_usage(
            search, query, group_field="attributes.workflow_name.keyword", id_fields=("attributes.request_id.keyword",)
        )

        assert set(usage) == {"a", "b"}
        first, second, third = [call.args[0] for call in search.call_args_list]
        assert first["query"] == query
        assert first["size"] == 0
        composite = first["aggs"]["requests"]["composite"]
        assert composite["sources"] == [
            {"group": {"terms": {"field": "attributes.workflow_name.keyword"}}},
            {"id_0": {"terms": {"field": "attributes.request_id.keyword", "missing_bucket": True}}},
        ]
        assert "after" not in composite
        assert second["aggs"]["requests"]["composite"]["after"] == {"group": "a", "id_0": "r-1"}
        assert third["aggs"]["requests"]["composite"]["after"] == {"group": "b", "id_0": "r-2"}

    @pytest.mark.asyncio
    async def test_reads_first_seen_and_the_coverage_qualified_stored_money(self):
        search = AsyncMock(
            return_value=_page(_bucket({"group": "a", "id_0": "r-1"}, AFTER_FLOOR, money=0.5, settled=1, unsettled=1))
        )

        usage = await collect_request_usage(search, {}, group_field="g", id_fields=("i",))

        assert usage == {
            "a": [
                RequestUsage(
                    request_id="r-1",
                    first_seen=AFTER_FLOOR,
                    stored=RenderedSpend(figure=0.5, unavailable=False, partial=True, missing_amount=None),
                )
            ]
        }

    @pytest.mark.asyncio
    async def test_the_first_present_id_field_names_the_request_and_its_buckets_merge(self):
        search = AsyncMock(
            return_value=_page(
                _bucket({"group": "a", "id_0": "exec-1", "id_1": None}, AFTER_FLOOR, money=1.0),
                _bucket({"group": "a", "id_0": "exec-1", "id_1": "req-9"}, BEFORE_FLOOR, money=2.0),
                _bucket({"group": "a", "id_0": None, "id_1": "req-2"}, AFTER_FLOOR, money=3.0),
            )
        )

        usage = await collect_request_usage(search, {}, group_field="g", id_fields=("exec", "req"))

        by_id = {item.request_id: item for item in usage["a"]}
        assert set(by_id) == {"exec-1", "req-2"}
        assert by_id["exec-1"].first_seen == BEFORE_FLOOR
        assert by_id["exec-1"].stored.figure == 3.0

    @pytest.mark.asyncio
    async def test_without_a_group_field_everything_is_one_group(self):
        search = AsyncMock(return_value=_page(_bucket({"id_0": "r-1"}, AFTER_FLOOR)))

        usage = await collect_request_usage(search, {}, group_field=None, id_fields=("i",))

        assert list(usage) == [None]
        assert search.call_args.args[0]["aggs"]["requests"]["composite"]["sources"] == [
            {"id_0": {"terms": {"field": "i", "missing_bucket": True}}}
        ]


def _coverage(activated: bool = True):
    coverage = LedgerCoverage(coverage_floor=FLOOR, ready=activated)
    return (
        patch(REGISTERED_COVERAGE, return_value=coverage),
        patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", True),
    )


class TestPriceRequests:
    @pytest.mark.asyncio
    async def test_requests_from_the_floor_on_are_priced_by_the_ledger_and_summed_per_group(self):
        coverage, legacy = _coverage()
        costs = {"r-1": RequestCost.settled(Decimal("2")), "r-2": RequestCost.settled(Decimal("0.25"))}
        with coverage, legacy, patch(COSTS_PORT, new=AsyncMock(return_value=costs)) as port:
            priced = await price_requests(
                {
                    "a": [
                        RequestUsage("r-1", AFTER_FLOOR, NOTHING_SETTLED),
                        RequestUsage("r-2", AFTER_FLOOR, NOTHING_SETTLED),
                    ]
                }
            )

        port.assert_awaited_once_with(["r-1", "r-2"])
        assert priced["a"] == RenderedSpend(figure=2.25, unavailable=False, partial=False, missing_amount=None)

    @pytest.mark.asyncio
    async def test_requests_before_the_floor_and_without_an_id_keep_their_stored_money(self):
        coverage, legacy = _coverage()
        with coverage, legacy, patch(COSTS_PORT, new=AsyncMock(return_value={})) as port:
            priced = await price_requests(
                {"a": [RequestUsage("r-1", BEFORE_FLOOR, EXACT), RequestUsage(None, AFTER_FLOOR, EXACT)]}
            )

        port.assert_not_awaited()
        assert priced["a"] == RenderedSpend(figure=3.0, unavailable=False, partial=False, missing_amount=None)

    @pytest.mark.asyncio
    async def test_a_pending_request_has_no_figure_and_marks_its_group(self):
        coverage, legacy = _coverage()
        costs = {"r-1": RequestCost.pending(), "r-2": RequestCost.settled(Decimal("1"))}
        with coverage, legacy, patch(COSTS_PORT, new=AsyncMock(return_value=costs)):
            priced = await price_requests(
                {"a": [RequestUsage("r-1", AFTER_FLOOR, EXACT)], "b": [RequestUsage("r-2", AFTER_FLOOR, EXACT)]}
            )

        assert priced["a"].figure is None
        assert priced["a"].unavailable is True
        assert priced["b"] == RenderedSpend(figure=1.0, unavailable=False, partial=False, missing_amount=None)

    @pytest.mark.asyncio
    async def test_a_request_the_ledger_cannot_price_keeps_its_stored_money_flagged(self):
        coverage, legacy = _coverage()
        with coverage, legacy, patch(COSTS_PORT, new=AsyncMock(return_value={"r-1": RequestCost.unavailable()})):
            priced = await price_requests({"a": [RequestUsage("r-1", AFTER_FLOOR, EXACT)]})

        assert priced["a"].figure == 1.5
        assert priced["a"].unavailable is True

    @pytest.mark.asyncio
    async def test_without_an_activated_ledger_the_stored_money_is_served_unasked(self):
        coverage, legacy = _coverage(activated=False)
        with coverage, legacy, patch(COSTS_PORT, new=AsyncMock(return_value={})) as port:
            priced = await price_requests({"a": [RequestUsage("r-1", AFTER_FLOOR, EXACT)]})

        port.assert_not_awaited()
        assert priced["a"] == EXACT

    @pytest.mark.asyncio
    async def test_a_group_with_no_requests_has_no_figure(self):
        coverage, legacy = _coverage()
        with coverage, legacy, patch(COSTS_PORT, new=AsyncMock(return_value={})):
            priced = await price_requests({"a": []})

        assert priced["a"].figure is None
        assert priced["a"].unavailable is True
