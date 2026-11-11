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

"""Common query pipeline for analytics operations.

This module provides a reusable pipeline for executing analytics queries,
reducing duplication across handlers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from pydantic.dataclasses import dataclass as pydantic_dataclass

from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.access_filter import AccessFilter
from codemie.service.analytics.aggregation_builder import AggregationBuilder
from codemie.service.analytics.query_builder import SecureQueryBuilder
from codemie.service.analytics.response_formatter import ResponseFormatter
from codemie.service.analytics.time_parser import TimeParser
from codemie.service.analytics.totals_calculator import TotalsCalculator
from codemie.service.spend.ledger import AccessScope
from codemie.service.spend.rendering import RenderedSpend, apply_spend_cells, spend_footer

logger = logging.getLogger(__name__)


@pydantic_dataclass
class AnalyticsQueryFilters:
    """Shared filter/pagination parameters accepted by AnalyticsQueryPipeline methods."""

    metric_filters: list[str] | None = None
    time_period: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    users: list[str] | None = None
    projects: list[str] | None = None
    page: int = 0
    per_page: int = 20
    client_source: str | None = None
    attributes: dict[str, str] | None = None
    assistant_id: str | None = None


#: The most groups one bucket-selector query asks Elasticsearch for. Past it, groups are silently
#: absent from the count, the pages and the footer alike; the warning below is the only signal.
BUCKET_SELECTOR_FETCH_SIZE = 10_000


@dataclass(frozen=True, slots=True)
class EnrichedRows:
    """A candidate row set an enricher has finished with, plus the footer for what it filled.

    ``rows`` is final: already ordered and already filtered, because only the enricher knows which
    column it filled and what an absent value in it means. ``columns`` names the column ids it owns,
    which is how the pipeline keeps exactly one writer per footer key. ``totals`` holds the footer
    entries for those columns, and a key it omits is a key the response does not carry.
    """

    rows: list[dict]
    columns: tuple[str, ...] = ()
    totals: dict[str, Any] = field(default_factory=dict)


#: Fills row values the aggregation could not produce, given the window the query covered.
RowEnricher = Callable[[list[dict], datetime, datetime], Awaitable[EnrichedRows]]

#: Fills row values inside an aggregation snapshot, given its search, the filtered query and the rows.
SnapshotRowEnricher = Callable[
    [Callable[[dict[str, Any]], Awaitable[dict[str, Any]]], dict[str, Any], list[dict[str, Any]]],
    Awaitable[EnrichedRows],
]


def _paginated_buckets(result: dict) -> list[dict]:
    """The grouped buckets of a tabular aggregation result, pre-sorted by Elasticsearch."""
    return result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])


def _every_bucket(result: dict, group_by_field: str, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Every candidate bucket of a bucket-selector query, with the fetch cap made visible when hit."""
    buckets = _paginated_buckets(result)
    if len(buckets) >= BUCKET_SELECTOR_FETCH_SIZE:
        # Elasticsearch returned exactly the cap, so every group past it is missing from the
        # count, the page set and the footer, while the footer still reads exact. Nothing on this
        # path can size the missing part, so the accepted truncation is logged rather than labelled.
        logger.warning(
            f"analytics_bucket_selector_capped group_by_field={group_by_field!r} "
            f"window=({start_dt.isoformat()}, {end_dt.isoformat()}) cap={BUCKET_SELECTOR_FETCH_SIZE}"
        )
    return buckets


def _more_pages(page: int, per_page: int, total_count: int) -> bool:
    return (page + 1) * per_page < total_count


def _pagination(page: int, per_page: int, total_count: int, has_more: bool) -> dict:
    return {"page": page, "per_page": per_page, "total_count": total_count, "has_more": has_more}


def _tabular_response(
    *,
    rows: list[dict],
    columns: list[dict],
    totals: dict,
    filters_applied: dict,
    pagination: dict,
    started_at: float,
) -> dict:
    """The formatted tabular response, with the pagination the fetch that produced the page decided."""
    response = ResponseFormatter.format_tabular_response(
        rows=rows,
        columns=columns,
        filters_applied=filters_applied,
        execution_time_ms=(time.time() - started_at) * 1000,
        totals=totals,
    )
    # Set directly rather than derived by the formatter, so has_more reflects the actual fetch
    response["pagination"] = pagination
    return response


class AnalyticsQueryPipeline:
    """Pipeline for executing analytics queries with common patterns.

    This pipeline encapsulates the standard flow:
    1. Parse time parameters
    2. Build secure query with filters
    3. Execute aggregation
    4. Format response

    **Every `result_parser` callback runs in a worker thread, never on the event loop
    (EPMCDME-15537).** A parser is synchronous and may block: it may walk thousands of buckets, and
    some resolve names through a synchronous directory lookup. The backend serves every request from
    one event loop, so a parser called inline stalls every concurrent request for as long as it runs;
    each `result_parser` call site below therefore goes through `asyncio.to_thread`, the totals pass
    included. `execute_summary_query` calls its `metrics_builder` inline on purpose: it only reads a
    handful of aggregation values and does no I/O.
    """

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize pipeline with user context and repository.

        Args:
            user: Authenticated user for access control
            repository: Elasticsearch repository for metrics
        """
        self._user = user
        self._repository = repository

    @property
    def access_scope(self) -> AccessScope:
        """The caller's permission boundary, in the vocabulary the spend port speaks.

        **The one place a scope is derived from a `User`, and handlers must not derive their own.**
        This pipeline already narrows every Elasticsearch read it builds through
        `SecureQueryBuilder`, which injects the same boundary at construction; a money column that
        moved off that query onto the spend port left the row set narrowed and the *figure*
        unnarrowed, so a plain member's own rows were footed with the whole platform's spend. Deriving
        the scope here is what keeps the two halves of one boundary reading the same access context:
        a handler calling `AccessFilter` for itself would be a second derivation, free to drift from
        the query the very rows came from.

        **An empty context is a caller entitled to nothing, never to everything.** That rule lives in
        `AccessScope` - a restricted scope with no accessible project matches no row, mirroring the
        query builder's `minimum_should_match=0` - and it is not softened here. Admins and auditors
        arrive as `is_admin=True` from the access context and become the unrestricted scope, which is
        the query builder's own early return.
        """
        context = AccessFilter(self._user).get_project_access_context()
        return AccessScope.for_context(
            user_id=context.user_id,
            plain_projects=context.plain_user_projects,
            admin_projects=context.admin_projects,
            is_admin=context.is_admin,
        )

    async def execute_search_rows(
        self,
        result_parser: Callable[[dict], list[dict]],
        columns: list[dict],
        filters: AnalyticsQueryFilters | None = None,
        sort: list[dict[str, Any]] | None = None,
        row_enricher: RowEnricher | None = None,
    ) -> dict:
        """Return paginated raw metric documents using the shared secure filters.

        `row_enricher` runs over the page's rows only; its footer is ignored, since a page of raw
        documents has no total.
        """
        filters = filters or AnalyticsQueryFilters()
        page, per_page = filters.page, filters.per_page
        start_time = time.time()
        start_dt, end_dt = TimeParser.parse(filters.time_period, filters.start_date, filters.end_date)
        query = self._build_query(start_dt, end_dt, filters)
        sort_kwargs = {"sort": sort} if sort is not None else {}
        result = await self._repository.execute_search_query(query, size=per_page, from_=page * per_page, **sort_kwargs)
        rows = await asyncio.to_thread(result_parser, result)
        if row_enricher is not None:
            rows = (await row_enricher(rows, start_dt, end_dt)).rows
        total = result.get("hits", {}).get("total", 0)
        total_count = total.get("value", 0) if isinstance(total, dict) else total

        return ResponseFormatter.format_tabular_response(
            rows=rows,
            columns=columns,
            filters_applied=self._build_filters_applied(filters, start_dt, end_dt),
            execution_time_ms=(time.time() - start_time) * 1000,
            page=page,
            per_page=per_page,
            total_count=int(total_count),
        )

    async def execute_tabular_query(
        self,
        agg_builder: Callable[[dict, int], dict],
        result_parser: Callable[[dict], list[dict]],
        columns: list[dict],
        group_by_field: str,
        filters: AnalyticsQueryFilters | None = None,
        use_bucket_selector: bool = False,
        totals_aggs: dict[str, dict] | None = None,
    ) -> dict:
        """Execute analytics query that returns tabular data with stateless pagination.

        Implements fetch-and-slice pagination:
        1. Calculates fetch_size = (page + 1) * per_page (or larger if use_bucket_selector=True)
        2. Fetches all buckets up to current page from Elasticsearch (pre-sorted)
        3. Slices buckets to get current page data
        4. Uses cardinality aggregation for accurate total_count (unless use_bucket_selector=True)

        A column filled from outside Elasticsearch is paged by `execute_enriched_tabular_query`
        instead, which always fetches every candidate bucket.

        Args:
            agg_builder: Function that builds aggregation body from (query, fetch_size)
            result_parser: Function that parses ES result to list of row dicts
            columns: Column definitions for response
            group_by_field: Field for cardinality aggregation (e.g., "attributes.project.keyword")
            filters: Shared query filters/pagination (metric_filters, time_period, start_date,
                     end_date, users, projects, page, per_page, client_source)
            use_bucket_selector: If True, aggregation uses bucket_selector which requires
                                fetching more buckets and calculating total_count from
                                filtered buckets (not cardinality)
            totals_aggs: Optional mapping of column_id → ES metric agg spec.  When
                         provided the specs are injected as sibling aggs alongside
                         ``paginated_results`` so totals are computed in one document
                         pass by Elasticsearch instead of iterating over all buckets
                         in Python.  For ``use_bucket_selector=False`` handlers this
                         also eliminates the second totals query entirely.

        Returns:
            Tabular response with rows, columns, metadata, and pagination
        """
        filters = filters or AnalyticsQueryFilters()
        page, per_page = filters.page, filters.per_page
        started_at = time.time()

        # 1. Parse time and build base query with filters
        start_dt, end_dt = TimeParser.parse(filters.time_period, filters.start_date, filters.end_date)
        query = self._build_query(start_dt, end_dt, filters)

        # 2. Fetch: every bucket in one query, or the current page beside a totals query
        if use_bucket_selector:
            data_result = await self._fetch_every_bucket(agg_builder, query, totals_aggs)
            totals_result = data_result
            data_buckets = _every_bucket(data_result, group_by_field, start_dt, end_dt)
            # With bucket_selector, we have all buckets, so the count is theirs and so is has_more
            total_count = len(data_buckets)
            has_more = _more_pages(page, per_page, total_count)
        else:
            data_fetch_size = (page + 1) * per_page
            data_result, totals_result = await self._fetch_page_buckets(
                agg_builder, query, group_by_field, data_fetch_size, totals_aggs
            )
            data_buckets = _paginated_buckets(data_result)
            # Extract total_count from cardinality aggregation
            total_count = data_result.get("aggregations", {}).get("total_buckets", {}).get("value", 0)
            has_more = len(data_buckets) == data_fetch_size and _more_pages(page, per_page, total_count)

        # 3. Slice buckets for current page and parse them into rows for display
        page_buckets = AggregationBuilder.slice_buckets_for_page(data_buckets, page, per_page)
        rows = await asyncio.to_thread(
            result_parser, {"aggregations": {"paginated_results": {"buckets": page_buckets}}}
        )

        # 4. Calculate totals: use global metric aggs when provided, else iterate buckets
        if totals_aggs:
            totals = AggregationBuilder.extract_global_totals(data_result, totals_aggs)
        else:
            totals = await asyncio.to_thread(
                self._calculate_totals_from_aggregation_result,
                aggregation_result=totals_result,
                result_parser=result_parser,
                columns=columns,
            )

        return _tabular_response(
            rows=rows,
            columns=columns,
            totals=totals,
            filters_applied=self._build_filters_applied(filters, start_dt, end_dt),
            pagination=_pagination(page, per_page, total_count, has_more),
            started_at=started_at,
        )

    async def execute_enriched_tabular_query(
        self,
        agg_builder: Callable[[dict, int], dict],
        result_parser: Callable[[dict], list[dict]],
        columns: list[dict],
        group_by_field: str,
        row_enricher: RowEnricher,
        filters: AnalyticsQueryFilters | None = None,
    ) -> dict:
        """Execute a tabular query whose rows are filled, ordered and totalled outside Elasticsearch.

        `row_enricher` is an async hook that runs once over **every** candidate row, before the
        slice, and returns the final ordered row set plus the footer for the columns it filled. A
        value Elasticsearch never held cannot be sorted or counted by Elasticsearch, so paging on it
        has to wait for the value to exist: this path therefore always fetches every candidate
        bucket, as the bucket-selector path of `execute_tabular_query` does, and never the paged
        fetch, which would hand the enricher one page to order. Totals are summed over the enriched
        rows, so there is no `totals_aggs` here - a sibling aggregation would be computed and
        discarded, and a money entry in it would race the enricher's own footer.

        The arguments and the response shape are otherwise those of `execute_tabular_query`.
        """
        filters = filters or AnalyticsQueryFilters()
        page, per_page = filters.page, filters.per_page
        started_at = time.time()
        start_dt, end_dt = TimeParser.parse(filters.time_period, filters.start_date, filters.end_date)
        query = self._build_query(start_dt, end_dt, filters)

        data_result = await self._fetch_every_bucket(agg_builder, query, totals_aggs=None)
        rows, total_count, totals = await self._enriched_page(
            buckets=_every_bucket(data_result, group_by_field, start_dt, end_dt),
            result_parser=result_parser,
            columns=columns,
            row_enricher=row_enricher,
            window=(start_dt, end_dt),
            page=page,
            per_page=per_page,
        )
        return _tabular_response(
            rows=rows,
            columns=columns,
            totals=totals,
            filters_applied=self._build_filters_applied(filters, start_dt, end_dt),
            pagination=_pagination(page, per_page, total_count, _more_pages(page, per_page, total_count)),
            started_at=started_at,
        )

    async def _fetch_every_bucket(
        self, agg_builder: Callable[[dict, int], dict], query: dict, totals_aggs: dict[str, dict] | None
    ) -> dict:
        """One query over every candidate bucket, with the global totals folded in when asked for."""
        agg_body = agg_builder(query, BUCKET_SELECTOR_FETCH_SIZE)
        if totals_aggs:
            AggregationBuilder.inject_global_totals(agg_body, totals_aggs)
        return await self._repository.execute_aggregation_query(agg_body)

    async def _fetch_page_buckets(
        self,
        agg_builder: Callable[[dict, int], dict],
        query: dict,
        group_by_field: str,
        data_fetch_size: int,
        totals_aggs: dict[str, dict] | None,
    ) -> tuple[dict, dict]:
        """The buckets up to the current page, and the result the totals are read from.

        With `totals_aggs` the global metric aggs ride along in the data query, so totals are
        computed in the same request and the second query is eliminated; without them a full-size
        totals query runs alongside the page query.
        """
        data_agg_body = AggregationBuilder.add_cardinality_for_total(
            agg_builder(query, data_fetch_size),
            field=group_by_field,
        )
        if totals_aggs:
            AggregationBuilder.inject_global_totals(data_agg_body, totals_aggs)
            data_result = await self._repository.execute_aggregation_query(data_agg_body)
            return data_result, data_result
        totals_agg_body = agg_builder(query, BUCKET_SELECTOR_FETCH_SIZE)
        data_result, totals_result = await asyncio.gather(
            self._repository.execute_aggregation_query(data_agg_body),
            self._repository.execute_aggregation_query(totals_agg_body),
        )
        return data_result, totals_result

    async def execute_exact_tabular_query(
        self,
        selection_builder: Callable[[dict[str, Any], int], dict[str, Any]],
        details_builder: Callable[[dict[str, Any], list[str]], dict[str, Any]],
        result_parser: Callable[[dict[str, Any]], list[dict[str, Any]]],
        columns: list[dict[str, Any]],
        group_by_field: str,
        filters: AnalyticsQueryFilters,
        totals_aggs: dict[str, dict[str, Any]],
        row_enricher: SnapshotRowEnricher | None = None,
    ) -> dict[str, Any]:
        """Select a page of groups, then aggregate all their documents in the same snapshot.

        `row_enricher` runs inside the snapshot with its search, the full filtered query and the page's
        rows; the footer entries it returns join the global totals.
        """
        start_time = time.time()
        start_dt, end_dt = TimeParser.parse(filters.time_period, filters.start_date, filters.end_date)
        query = self._build_query(start_dt, end_dt, filters)
        fetch_size = (filters.page + 1) * filters.per_page
        selection_body = selection_builder(query, fetch_size)
        AggregationBuilder.add_cardinality_for_total(selection_body, field=group_by_field)
        AggregationBuilder.inject_global_totals(selection_body, totals_aggs)

        async with self._repository.aggregation_snapshot() as snapshot:
            selection = await snapshot.search(selection_body)
            aggs = selection.get("aggregations", {})
            buckets = aggs.get("paginated_results", {}).get("buckets", [])
            page_buckets = AggregationBuilder.slice_buckets_for_page(buckets, filters.page, filters.per_page)
            group_ids = [bucket["key"] for bucket in page_buckets]
            complete_buckets = []
            if group_ids:
                details = await snapshot.search(details_builder(query, group_ids))
                by_id = {
                    bucket["key"]: bucket
                    for bucket in details.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
                }
                if any(group_id not in by_id for group_id in group_ids):
                    raise ExtendedHTTPException(
                        code=503,
                        message="Incomplete workflow analytics",
                        details="An execution selected from the snapshot is missing from its complete aggregation.",
                    )
                complete_buckets = [by_id[group_id] for group_id in group_ids]

            total_count = int(aggs.get("total_buckets", {}).get("value", 0))
            rows = result_parser({"aggregations": {"paginated_results": {"buckets": complete_buckets}}})
            totals = AggregationBuilder.extract_global_totals(selection, totals_aggs) if total_count else {}
            if row_enricher is not None and total_count:
                enriched = await row_enricher(snapshot.search, query, rows)
                rows = enriched.rows
                totals = {**totals, **enriched.totals}

        response = ResponseFormatter.format_tabular_response(
            rows=rows,
            columns=columns,
            filters_applied=self._build_filters_applied(filters, start_dt, end_dt),
            execution_time_ms=(time.time() - start_time) * 1000,
            totals=totals,
            page=filters.page,
            per_page=filters.per_page,
            total_count=total_count,
        )
        response["pagination"]["has_more"] = (
            len(buckets) == fetch_size and (filters.page + 1) * filters.per_page < response["pagination"]["total_count"]
        )
        return response

    async def execute_tabular_query_with_flattened_rows(
        self,
        agg_builder: Callable[[dict, int], dict],
        result_parser: Callable[[dict], list[dict]],
        columns: list[dict],
        flattening_multiplier: int = 10,
        sort_keys: list[tuple[str, bool]] | None = None,
        filters: AnalyticsQueryFilters | None = None,
        row_enricher: RowEnricher | None = None,
    ) -> dict:
        """Execute analytics query with row-level pagination for flattened nested aggregations.

        This method is designed for nested aggregations that are flattened into multiple rows
        (e.g., user → MCP servers becomes user-MCP combination rows). Unlike execute_tabular_query()
        which paginates at the bucket level, this method:
        1. Over-fetches buckets to account for flattening
        2. Parses ALL fetched buckets to flatten nested structure
        3. Sorts rows consistently using multi-level sort
        4. Slices final flattened rows to return exactly per_page rows

        Args:
            agg_builder: Function that builds aggregation body from (query, fetch_size)
            result_parser: Function that parses ES result to list of row dicts
            columns: Column definitions for response
            flattening_multiplier: Multiplier for over-fetching buckets (default 10)
            sort_keys: List of (field, reverse) tuples for consistent ordering.
                      Example: [("total_requests", True), ("user_name", False)]
            filters: Shared query filters/pagination (metric_filters, time_period, start_date,
                     end_date, users, projects, page, per_page, client_source)
            row_enricher: Optional hook that fills and totals every sorted row before the slice -
                     the sibling of `execute_esql_query`'s, needing no method of its own for the same
                     reason: this path parses every fetched row before it slices. A money column whose
                     cells can be absent or partial is footed by it rather than by `TotalsCalculator`,
                     which skips an absent cell and would present the remainder as a whole total.

        Returns:
            Tabular response with rows, columns, metadata, and pagination
        """
        filters = filters or AnalyticsQueryFilters()
        page, per_page = filters.page, filters.per_page
        start_time = time.time()

        # 1. Parse time and build base query with filters
        start_dt, end_dt = TimeParser.parse(filters.time_period, filters.start_date, filters.end_date)
        query = self._build_query(start_dt, end_dt, filters)

        # 2. Calculate over-fetch size to account for nested flattening
        # If page=0 and per_page=10 with multiplier=10: fetch_size=100
        # This ensures we fetch enough top-level buckets to get sufficient flattened rows
        fetch_size = (page + 1) * per_page * flattening_multiplier

        # 3. Build aggregation with over-fetched size
        agg_body = agg_builder(query, fetch_size)

        # 4. Execute query
        result = await self._repository.execute_aggregation_query(agg_body)

        # 5. Parse ALL fetched buckets to flatten nested structure
        # This returns the complete flattened list of rows (not paginated yet)
        all_rows = await asyncio.to_thread(result_parser, result)

        # 6. Sort rows consistently using multi-level sort keys
        # This ensures deterministic ordering across pages (no duplicates/skips)
        if sort_keys:
            for field, reverse in reversed(sort_keys):
                all_rows.sort(key=lambda x, f=field: x.get(f, 0), reverse=reverse)

        start_idx = page * per_page
        end_idx = start_idx + per_page
        if row_enricher is not None:
            # 7-8a. The same helper the aggregation and ES|QL paths use, so all three agree on which
            # writer owns each footer key and on footing every surviving row, not the page.
            paginated_rows, total_count, totals = await self._enrich_and_page(
                candidates=all_rows,
                columns=columns,
                row_enricher=row_enricher,
                window=(start_dt, end_dt),
                page=page,
                per_page=per_page,
            )
        else:
            # 7. Calculate total_count from parsed rows
            # Note: This is an approximation if dataset is larger than fetch_size
            # For most use cases with multiplier=10, this is accurate enough
            total_count = len(all_rows)

            # 8. Slice rows for current page
            paginated_rows = all_rows[start_idx:end_idx]

            # 8a. Calculate totals from ALL rows (this works correctly because we fetch ALL data first)
            totals = TotalsCalculator.calculate_totals(columns=columns, rows=all_rows)

        # 9. Calculate has_more
        has_more = end_idx < total_count

        # 10. Debug logging for troubleshooting
        logger.debug(
            f"Flattened pagination: fetch_size={fetch_size}, "
            f"total_rows_parsed={len(all_rows)}, "
            f"page={page}, per_page={per_page}, "
            f"start_idx={start_idx}, end_idx={end_idx}, "
            f"rows_returned={len(paginated_rows)}, "
            f"total_count={total_count}, has_more={has_more}"
        )

        # 11. Format and return response
        execution_time_ms = (time.time() - start_time) * 1000
        filters_applied = self._build_filters_applied(filters, start_dt, end_dt)

        pagination = {
            "page": page,
            "per_page": per_page,
            "total_count": total_count,
            "has_more": has_more,
        }

        response = ResponseFormatter.format_tabular_response(
            rows=paginated_rows,
            columns=columns,
            filters_applied=filters_applied,
            execution_time_ms=execution_time_ms,
            totals=totals,
        )
        response["pagination"] = pagination

        return response

    async def execute_summary_query(
        self,
        agg_builder: Callable[[dict], dict],
        metrics_builder: Callable[[dict], list[dict]],
        filters: AnalyticsQueryFilters | None = None,
        timestamp_field: str = "@timestamp",
    ) -> dict:
        """Execute analytics query that returns summary metrics.

        Args:
            agg_builder: Function that builds aggregation body from query
            metrics_builder: Function that builds metrics list from ES result
            filters: Shared query filters (metric_filters, time_period, start_date,
                     end_date, users, projects, client_source)
            timestamp_field: Elasticsearch timestamp field for time range filter

        Returns:
            Summary response with metrics and metadata
        """
        filters = filters or AnalyticsQueryFilters()
        start_time = time.time()

        # Parse time and build query
        start_dt, end_dt = TimeParser.parse(filters.time_period, filters.start_date, filters.end_date)
        query = self._build_query(start_dt, end_dt, filters, timestamp_field)

        # Build aggregation
        agg_body = agg_builder(query)

        # Execute
        result = await self._repository.execute_aggregation_query(agg_body)

        # Build metrics
        metrics = metrics_builder(result)

        # Format response
        execution_time_ms = (time.time() - start_time) * 1000
        filters_applied = self._build_filters_applied(filters, start_dt, end_dt)

        return ResponseFormatter.format_summary_response(
            metrics=metrics, filters_applied=filters_applied, execution_time_ms=execution_time_ms
        )

    async def execute_composite_query(
        self,
        agg_builder: Callable[[dict], dict],
        result_parser: Callable[[dict, dict], dict],
        filters: AnalyticsQueryFilters | None = None,
    ) -> dict:
        """Execute analytics query with custom aggregation and response format.

        This method provides a flexible pipeline for queries that don't fit
        the tabular or summary patterns (e.g., composite aggregations, custom
        response structures).

        Args:
            agg_builder: Function that builds aggregation body from query
            result_parser: Function that parses ES result and metadata into final response dict
            filters: Shared query filters (metric_filters, time_period, start_date,
                     end_date, users, projects, client_source)

        Returns:
            Custom response dict with data and metadata (format defined by result_parser)
        """
        filters = filters or AnalyticsQueryFilters()
        start_time = time.time()

        # 1. Parse time and build secure query with filters
        start_dt, end_dt = TimeParser.parse(filters.time_period, filters.start_date, filters.end_date)
        query = self._build_query(start_dt, end_dt, filters)

        # 2. Build aggregation body
        agg_body = agg_builder(query)

        # 3. Execute aggregation
        result = await self._repository.execute_aggregation_query(agg_body)

        # 4. Parse result into final response (with metadata)
        execution_time_ms = (time.time() - start_time) * 1000
        filters_applied = self._build_filters_applied(filters, start_dt, end_dt)
        metadata = ResponseFormatter.create_metadata(filters_applied, execution_time_ms)

        # 5. Let result_parser build final response with metadata
        return await asyncio.to_thread(result_parser, result, metadata)

    async def execute_esql_query(
        self,
        esql_query: str,
        result_parser: Callable[[dict], list[dict]],
        columns: list[dict],
        filters: AnalyticsQueryFilters | None = None,
        row_enricher: RowEnricher | None = None,
    ) -> dict:
        """Execute ES|QL query with in-memory pagination.

        Note: ES|QL results are capped at 10,000 rows by Elasticsearch.
        All results are fetched first, then paginated in-memory using array slicing.

        **`row_enricher` is the sibling of `execute_enriched_tabular_query`'s, and it needs no
        method of its own.** That method exists because the paged tabular fetch cannot hand an
        enricher all the candidates; this path *always* fetches every row before it slices, so the
        precondition holds unconditionally and the enricher stays an optional argument here. A
        caller supplying no enricher reaches the original code on the same line as before.

        It exists because a value Elasticsearch never held cannot be ordered, counted or totalled by
        Elasticsearch: once a money column comes from the spend port, the ES|QL `SORT` can no longer
        rank by it, and the ranking has to wait until the value exists.

        Args:
            esql_query: ES|QL query string
            result_parser: Function that parses ES|QL result to list of row dicts
            columns: Column definitions for response
            filters: Shared query filters/pagination (metric_filters, time_period, start_date,
                     end_date, users, projects, page, per_page, client_source)
            row_enricher: Optional hook that fills, orders and totals every row before the slice

        Returns:
            Tabular response with rows, columns, metadata, and pagination
        """
        filters = filters or AnalyticsQueryFilters()
        page, per_page = filters.page, filters.per_page
        start_time = time.time()

        # 1. Parse time and build filter query
        start_dt, end_dt = TimeParser.parse(filters.time_period, filters.start_date, filters.end_date)
        filter_query = self._build_query(start_dt, end_dt, filters)

        # 2. Execute ES|QL query (fetch ALL results, up to 10k limit)
        result = await self._repository.execute_esql_query(esql_query, filter_query=filter_query)

        # 3. Parse ALL results
        all_rows = await asyncio.to_thread(result_parser, result)

        if row_enricher is not None:
            # 4-5a. An enriched column is ordered, counted and totalled only after the enricher has
            # run, so the slice waits for it. Same helper the aggregation path uses, so the two
            # cannot disagree about whether the footer covers the page or every surviving row.
            paginated_rows, total_count, totals = await self._enrich_and_page(
                candidates=all_rows,
                columns=columns,
                row_enricher=row_enricher,
                window=(start_dt, end_dt),
                page=page,
                per_page=per_page,
            )
        else:
            # 4. Calculate total count and pagination indices
            total_count = len(all_rows)
            start_idx = page * per_page
            end_idx = start_idx + per_page

            # 5. Slice rows for current page
            paginated_rows = all_rows[start_idx:end_idx]

            # 5a. Calculate totals from ALL rows (correct because we fetch ALL data first)
            totals = TotalsCalculator.calculate_totals(columns=columns, rows=all_rows)

        # 6. Calculate execution time and build filters
        execution_time_ms = (time.time() - start_time) * 1000
        filters_applied = self._build_filters_applied(filters, start_dt, end_dt)

        # 7. Format and return response
        return ResponseFormatter.format_tabular_response(
            rows=paginated_rows,
            columns=columns,
            filters_applied=filters_applied,
            execution_time_ms=execution_time_ms,
            totals=totals,
            page=page,
            per_page=per_page,
            total_count=total_count,
        )

    async def _enriched_page(
        self,
        *,
        buckets: list[dict],
        result_parser: Callable[[dict], list[dict]],
        columns: list[dict],
        row_enricher: RowEnricher,
        window: tuple[datetime, datetime],
        page: int,
        per_page: int,
    ) -> tuple[list[dict], int, dict]:
        """Page, count and total a row set whose values one caller fills in asynchronously.

        The enricher is awaited **once for every candidate row**, which is what makes a single round
        trip enough and what lets the order it returns decide which rows reach the page.

        Returns the page's rows, the count of every row that survived enrichment, and the footer.
        """
        candidates = await asyncio.to_thread(
            result_parser, {"aggregations": {"paginated_results": {"buckets": buckets}}}
        )
        return await self._enrich_and_page(
            candidates=candidates,
            columns=columns,
            row_enricher=row_enricher,
            window=window,
            page=page,
            per_page=per_page,
        )

    async def _enrich_and_page(
        self,
        *,
        candidates: list[dict],
        columns: list[dict],
        row_enricher: RowEnricher,
        window: tuple[datetime, datetime],
        page: int,
        per_page: int,
    ) -> tuple[list[dict], int, dict]:
        """Enrich, order, count and total a candidate row set, then slice the page from it.

        **Split from `_enriched_page` so the aggregation and ES|QL paths share one implementation of
        this rule rather than two.** Where the candidates come from is the only thing that differs
        between them - buckets parsed into rows, or an ES|QL table already parsed - and everything
        after that point is identical. Two copies would be free to diverge on the part that is easy
        to get subtly wrong: whether the footer is computed before or after the slice, and which
        writer owns each footer key.
        """
        enriched = await row_enricher(candidates, *window)

        # The enricher's own columns are excluded here so that exactly one writer produces each
        # footer key. Two writers is how a footer comes to show a value from a source the rows above
        # it no longer use, with nothing on the response saying which of them it is.
        totals = TotalsCalculator.calculate_totals(
            columns=columns,
            rows=enriched.rows,
            exclude_columns=list(enriched.columns),
        )
        totals.update(enriched.totals)

        page_rows = AggregationBuilder.slice_buckets_for_page(enriched.rows, page, per_page)
        logger.debug(
            f"Enriched page: candidates={len(candidates)} surviving={len(enriched.rows)} "
            f"page_rows={len(page_rows)} enriched_columns={enriched.columns}"
        )
        return page_rows, len(enriched.rows), totals

    def _calculate_totals_from_aggregation_result(
        self,
        aggregation_result: dict,
        result_parser: Callable[[dict], list[dict]],
        columns: list[dict],
    ) -> dict[str, float | int]:
        """Calculate totals from aggregation result.

        Extracts buckets from aggregation result, parses them into rows,
        and calculates totals for numeric columns.

        Args:
            aggregation_result: Elasticsearch aggregation result
            result_parser: Function that parses ES result to list of row dicts
            columns: Column definitions for totals calculation

        Returns:
            Dictionary mapping column IDs to total values
        """
        # Extract all buckets from aggregation result
        totals_buckets = aggregation_result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        # Parse ALL buckets to get all rows for totals calculation
        all_rows_for_totals = result_parser({"aggregations": {"paginated_results": {"buckets": totals_buckets}}})

        # Calculate totals from all rows
        return TotalsCalculator.calculate_totals(columns=columns, rows=all_rows_for_totals)

    def secure_query(self, start_dt: datetime, end_dt: datetime, filters: AnalyticsQueryFilters) -> dict[str, Any]:
        """The access-scoped query every table built from `filters` over this window reads."""
        return self._build_query(start_dt, end_dt, filters)

    async def search_aggregation(self, body: dict[str, Any]) -> dict[str, Any]:
        """One aggregation search against the metrics store."""
        return await self._repository.execute_aggregation_query(body)

    def _build_query(
        self,
        start_dt: datetime,
        end_dt: datetime,
        filters: AnalyticsQueryFilters,
        timestamp_field: str = "@timestamp",
    ) -> dict:
        """Build secure query with all filters."""
        query_builder = SecureQueryBuilder(self._user)
        query_builder.add_time_range(start_dt, end_dt, timestamp_field)

        if filters.metric_filters:
            query_builder.add_metric_filter(filters.metric_filters)
        if filters.users:
            query_builder.add_user_filter(filters.users)
        if filters.projects:
            query_builder.add_project_filter(filters.projects)
        if filters.client_source:
            query_builder.add_client_source_filter(filters.client_source)
        if filters.attributes:
            query_builder.add_attributes_filter(filters.attributes)
        if filters.assistant_id:
            query_builder.add_assistant_filter(filters.assistant_id)

        return query_builder.build()

    def _build_query_without_time_filter(self, filters: AnalyticsQueryFilters) -> dict:
        """Build secure query WITHOUT time filter — for all-time engagement metrics (DAU, MAU, weekly).

        Access control (project/user scoping) is still fully applied.
        """
        query_builder = SecureQueryBuilder(self._user)
        # NOTE: no add_time_range() — intentionally omitted for all-time queries
        if filters.metric_filters:
            query_builder.add_metric_filter(filters.metric_filters)
        if filters.users:
            query_builder.add_user_filter(filters.users)
        if filters.projects:
            query_builder.add_project_filter(filters.projects)
        if filters.client_source:
            query_builder.add_client_source_filter(filters.client_source)

        return query_builder.build()

    def _build_filters_applied(self, filters: AnalyticsQueryFilters, start_dt: datetime, end_dt: datetime) -> dict:
        """Build filters_applied dictionary."""
        return tabular_filters_applied(filters, start_dt, end_dt)


def tabular_filters_applied(filters: AnalyticsQueryFilters, start_dt: datetime, end_dt: datetime) -> dict:
    """The `filters_applied` metadata block every tabular analytics response carries.

    Module-level rather than a method because a surface can now answer **without** an
    Elasticsearch query - the budget limit dashboards compute their rows from the ledger against
    the configured limits - and such a surface still owes the caller the same metadata block. A
    second copy assembled beside it would drift on the one key that is conditional
    (`start_date`/`end_date` are reported only for a custom range), and a client comparing two
    tables would see one report its window and the other not.
    """
    return {
        "time_period": filters.time_period or "custom",
        "start_date": start_dt.isoformat() if not filters.time_period else None,
        "end_date": end_dt.isoformat() if not filters.time_period else None,
        "users": filters.users,
        "projects": filters.projects,
        "client_source": filters.client_source,
        "assistant_id": filters.assistant_id,
        "attributes": filters.attributes,
    }


# Ordering left to Elasticsearch once money is no longer in it: document count is the only activity
# proxy on a terms bucket, and the figure the table is actually sorted by arrives after the port
# answers. A holder with heavy ledger spend but few metric documents can therefore be cut by the
# candidate cap before the money sort ever sees it.
CANDIDATE_ORDER = {"_count": "desc"}


def ordered_spend_rows(rows: list[dict], rendered: list[RenderedSpend], column: str) -> EnrichedRows:
    """Fill, order and total an **activity** table around one money column, keeping every row.

    The counterpart of `enriched_spend_rows` for a table whose rows are activity records rather
    than spend: a workflow that ran, an embedding model that indexed something. **No row is dropped
    for costing nothing**, because the row's reason to exist is the activity it records, and a
    measured zero beside it is a fact about that activity rather than grounds to hide it.

    An absent figure sorts **last** rather than as a zero: the first key separates the two outright,
    so no row nobody has a figure for is ranked among the figures. The footer is summed over every
    row, so the table's total and its rows describe the same set.
    """
    apply_spend_cells(rows, rendered, column)
    ordered = sorted(
        zip(rows, rendered, strict=True),
        key=lambda pair: (pair[1].figure is not None, pair[1].figure or 0.0),
        reverse=True,
    )
    return EnrichedRows(
        rows=[row for row, _ in ordered],
        columns=(column,),
        totals=spend_footer(column, [figure for _, figure in ordered]),
    )


def enriched_spend_rows(rows: list[dict], rendered: list[RenderedSpend], column: str) -> EnrichedRows:
    """Fill, order, filter and total a spending table around one money column.

    A measured zero is dropped, which is what the Elasticsearch ``filter_zero_cost`` selector did and
    the same product rule: a spending table lists what was spent. An **absent** figure is kept and
    labelled instead - dropping it would shorten the table on the strength of a figure nobody has,
    and a row silently missing is as wrong as a row silently zero.

    **Only an *exact* zero is a measured one, and the difference is not a nicety.** A figure of ``0``
    carrying an ``unavailable`` flag says *nothing was spent in the part of the window the ledger
    covers, and the rest is unknown*; one carrying a ``partial`` portion says *the attributed rows
    total zero and an unattributed amount is missing*. Dropped as though they were measured, the row
    vanishes **and its annotation vanishes with it** - and because the footer is computed over the
    kept rows only, the table total then asserts it is exact. That is the truncated-total-presented-
    as-whole failure arriving through the filter rather than through the sum, and the second form
    needs no coverage-floor crossing to reach a live report.

    **And the figure judged here must be the measured one, never a display-rounded one.** A sum of
    $0.004 rounded to two places arrives indistinguishable from a project that spent nothing and is
    dropped as one, which is the same rounded-value-taken-for-a-measured-one mistake arriving through
    the caller. `project_handler._pre_floor_spend` keeps that rounding out of the value it hands here.
    """
    apply_spend_cells(rows, rendered, column)

    kept = [pair for pair in zip(rows, rendered, strict=True) if not (pair[1].is_exact and pair[1].figure == 0)]
    # The ledger figure decides the order, and an absent one sorts last rather than as a zero: the
    # first key separates the two outright, so no absent row is ever ranked among the figures.
    kept.sort(key=lambda pair: (pair[1].figure is not None, pair[1].figure or 0.0), reverse=True)

    return EnrichedRows(
        rows=[row for row, _ in kept],
        columns=(column,),
        totals=spend_footer(column, [spend for _, spend in kept]),
    )
