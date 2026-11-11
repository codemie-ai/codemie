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

"""Money for groups of requests listed by Elasticsearch, priced per request from the spend ledger.

A request first seen at or after the ledger's coverage floor is priced by the ledger under its
correlation id; an older request, or one with no id, keeps its own coverage-qualified Elasticsearch
money. Groups combine their requests' figures with every qualification kept.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.analytics.handlers.money_coverage import (
    money_coverage_aggs,
    pre_floor_boundary,
    read_money_coverage,
)
from codemie.service.spend.ledger import RequestCost
from codemie.service.spend.ledger_registry import ledger_coverage
from codemie.service.spend.rendering import RenderedSpend, combine_rendered_spend, render_spend
from codemie.service.spend.spend_service import spend_service

if TYPE_CHECKING:
    from codemie.service.analytics.query_pipeline import AnalyticsQueryFilters, AnalyticsQueryPipeline

FIRST_SEEN_AGG = "first_seen"
MONEY_SUM_AGG = "money_spent"
REQUEST_USAGE_PAGE_SIZE = 10_000
MAX_REQUEST_USAGE_PAGES = 50
TIMESTAMP_FIELD = "@timestamp"
MONEY_SPENT_FIELD = "attributes.money_spent"

GroupKey = TypeVar("GroupKey", bound=Hashable)
Search = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RequestUsage:
    """One request as Elasticsearch saw it: its correlation id, when it first appeared, its own money."""

    request_id: str | None
    first_seen: datetime | None
    stored: RenderedSpend


def request_usage_aggs(cost_bearing: dict[str, Any] | None = None) -> dict[str, Any]:
    """The per-request sub-aggregations `read_request_usage` reads."""
    return {
        FIRST_SEEN_AGG: {"min": {"field": TIMESTAMP_FIELD}},
        MONEY_SUM_AGG: {"sum": {"field": MONEY_SPENT_FIELD}},
        **money_coverage_aggs(MONEY_SPENT_FIELD, cost_bearing=cost_bearing),
    }


def read_request_usage(bucket: dict[str, Any], request_id: str | None) -> RequestUsage:
    first_seen = bucket.get(FIRST_SEEN_AGG, {}).get("value")
    return RequestUsage(
        request_id=request_id,
        first_seen=None if first_seen is None else datetime.fromtimestamp(first_seen / 1000, tz=timezone.utc),
        stored=read_money_coverage(bucket, MONEY_SUM_AGG),
    )


def _merged(usages: list[RequestUsage]) -> RequestUsage:
    if len(usages) == 1:
        return usages[0]
    seen = [usage.first_seen for usage in usages if usage.first_seen is not None]
    return RequestUsage(
        request_id=usages[0].request_id,
        first_seen=min(seen) if seen else None,
        stored=combine_rendered_spend([usage.stored for usage in usages]),
    )


async def collect_request_usage(
    search: Search,
    query: dict[str, Any],
    group_field: str | None,
    id_fields: Sequence[str],
    cost_bearing: dict[str, Any] | None = None,
    page_size: int = REQUEST_USAGE_PAGE_SIZE,
    max_pages: int = MAX_REQUEST_USAGE_PAGES,
) -> dict[Any, list[RequestUsage]]:
    """Every request matching `query`, grouped by `group_field`, paged through a composite aggregation.

    The request id is the first of `id_fields` a document carries; buckets naming the same request
    within a group are merged so the ledger is never asked twice for one request. A range holding more
    than `max_pages` pages of requests is refused with a 422 asking for a narrower one.
    """
    id_keys = [f"id_{index}" for index in range(len(id_fields))]
    sources: list[dict[str, Any]] = [{"group": {"terms": {"field": group_field}}}] if group_field else []
    sources += [
        {key: {"terms": {"field": field, "missing_bucket": True}}}
        for key, field in zip(id_keys, id_fields, strict=True)
    ]

    collected: dict[Any, dict[str | None, list[RequestUsage]]] = {}
    after: dict[str, Any] | None = None
    pages = 0
    while True:
        if pages >= max_pages:
            raise ExtendedHTTPException(
                code=422,
                message="Analytics range too large to price",
                details="The selected range holds too many requests to price; narrow the time range or the filters.",
            )
        pages += 1
        composite: dict[str, Any] = {"size": page_size, "sources": sources}
        if after is not None:
            composite["after"] = after
        result = await search(
            {
                "query": query,
                "size": 0,
                "aggs": {"requests": {"composite": composite, "aggs": request_usage_aggs(cost_bearing)}},
            }
        )
        requests = result.get("aggregations", {}).get("requests", {})
        buckets = requests.get("buckets", [])
        for bucket in buckets:
            key = bucket["key"]
            request_id = next((key[id_key] for id_key in id_keys if key.get(id_key) is not None), None)
            group = collected.setdefault(key.get("group"), {})
            group.setdefault(request_id, []).append(read_request_usage(bucket, request_id))
        after = requests.get("after_key")
        if not buckets or after is None:
            break

    return {group: [_merged(usages) for usages in by_id.values()] for group, by_id in collected.items()}


def _before_floor(first_seen: datetime | None, floor: datetime | None) -> bool:
    return floor is not None and first_seen is not None and first_seen < floor


def _request_spend(usage: RequestUsage, costs: Mapping[str, RequestCost]) -> RenderedSpend:
    cost = costs.get(usage.request_id) if usage.request_id else None
    if cost is not None and cost.is_settled and cost.figure is not None:
        return RenderedSpend(figure=float(cost.figure), unavailable=False, partial=False, missing_amount=None)
    if cost is not None and cost.is_pending:
        return RenderedSpend(figure=None, unavailable=True, partial=False, missing_amount=None)
    if cost is None:
        return usage.stored
    return RenderedSpend(
        figure=usage.stored.figure,
        unavailable=True,
        partial=usage.stored.partial,
        missing_amount=usage.stored.missing_amount,
    )


async def price_filtered_requests(
    pipeline: AnalyticsQueryPipeline,
    filters: AnalyticsQueryFilters,
    start_dt: datetime,
    end_dt: datetime,
    group_field: str,
    id_fields: Sequence[str],
    cost_bearing: dict[str, Any] | None = None,
) -> dict[Any, RenderedSpend]:
    """Each group's money over the requests the pipeline's secured query selects for `filters`."""
    usage = await collect_request_usage(
        pipeline.search_aggregation,
        pipeline.secure_query(start_dt, end_dt, filters),
        group_field=group_field,
        id_fields=id_fields,
        cost_bearing=cost_bearing,
    )
    return await price_requests(usage)


async def price_requests(
    usages_by_group: Mapping[GroupKey, Sequence[RequestUsage]],
) -> dict[GroupKey, RenderedSpend]:
    """Each group's money: its requests from the floor on priced by the ledger, the rest as stored."""
    coverage = ledger_coverage()
    floor = pre_floor_boundary(coverage)
    asked = list(
        dict.fromkeys(
            usage.request_id
            for usages in usages_by_group.values()
            for usage in usages
            if coverage.activated and usage.request_id and not _before_floor(usage.first_seen, floor)
        )
    )
    costs = await spend_service.costs_for_requests(asked) if asked else {}
    return {
        group: combine_rendered_spend([_request_spend(usage, costs) for usage in usages])
        if usages
        else render_spend(None)
        for group, usages in usages_by_group.items()
    }
