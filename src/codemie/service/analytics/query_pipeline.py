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
from datetime import datetime
from typing import Callable

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.aggregation_builder import AggregationBuilder
from codemie.service.analytics.query_builder import SecureQueryBuilder
from codemie.service.analytics.response_formatter import ResponseFormatter
from codemie.service.analytics.time_parser import TimeParser
from codemie.service.analytics.totals_calculator import TotalsCalculator

logger = logging.getLogger(__name__)


class AnalyticsQueryPipeline:
    """Pipeline for executing analytics queries with common patterns.

    This pipeline encapsulates the standard flow:
    1. Parse time parameters
    2. Build secure query with filters
    3. Execute aggregation
    4. Format response
    """

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize pipeline with user context and repository.

        Args:
            user: Authenticated user for access control
            repository: Elasticsearch repository for metrics
        """
        self._user = user
        self._repository = repository

    async def execute_tabular_query(
        self,
        agg_builder: Callable[[dict, int], dict],
        result_parser: Callable[[dict], list[dict]],
        columns: list[dict],
        group_by_field: str,
        metric_filters: list[str] | None = None,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        use_bucket_selector: bool = False,
    ) -> dict:
        """Execute analytics query that returns tabular data with stateless pagination.

        Implements fetch-and-slice pagination:
        1. Calculates fetch_size = (page + 1) * per_page (or larger if use_bucket_selector=True)
        2. Fetches all buckets up to current page from Elasticsearch (pre-sorted)
        3. Slices buckets to get current page data
        4. Uses cardinality aggregation for accurate total_count (unless use_bucket_selector=True)

        Args:
            agg_builder: Function that builds aggregation body from (query, fetch_size)
            result_parser: Function that parses ES result to list of row dicts
            columns: Column definitions for response
            group_by_field: Field for cardinality aggregation (e.g., "attributes.project.keyword")
            metric_filters: Optional metric name filters
            time_period: Predefined time range
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by users
            projects: Filter by projects
            page: Page number (0-indexed)
            per_page: Items per page
            use_bucket_selector: If True, aggregation uses bucket_selector which requires
                                fetching more buckets and calculating total_count from
                                filtered buckets (not cardinality)

        Returns:
            Tabular response with rows, columns, metadata, and pagination
        """
        start_time = time.time()

        # 1. Parse time and build base query with filters
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._build_query(start_dt, end_dt, users, projects, metric_filters)

        # 2. Calculate fetch sizes for hybrid approach
        # Data query: fetch only buckets needed for current page (efficient pagination)
        # Totals query: fetch all buckets for accurate totals calculation
        data_fetch_size = 10000 if use_bucket_selector else (page + 1) * per_page
        totals_fetch_size = 10000  # Always fetch all buckets for accurate totals

        # 3. Build TWO aggregation queries
        data_agg_body = agg_builder(query, data_fetch_size)
        totals_agg_body = agg_builder(query, totals_fetch_size)

        # 4. Add sibling cardinality aggregation for accurate total_count to data query
        # (only if not using bucket_selector)
        if not use_bucket_selector:
            data_agg_body = AggregationBuilder.add_cardinality_for_total(
                data_agg_body,
                field=group_by_field,
            )

        # 5. Execute BOTH queries in parallel for optimal performance
        data_result, totals_result = await asyncio.gather(
            self._repository.execute_aggregation_query(data_agg_body),
            self._repository.execute_aggregation_query(totals_agg_body),
        )

        # 6. Extract buckets from data query (pre-sorted by Elasticsearch)
        data_buckets = data_result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        # 7. Calculate total_count from data query
        if use_bucket_selector:
            # When bucket_selector is used, count actual filtered buckets
            total_count = len(data_buckets)
        else:
            # Extract total_count from cardinality aggregation
            total_count = data_result.get("aggregations", {}).get("total_buckets", {}).get("value", 0)

        # 8. Slice buckets for current page from data query
        page_buckets = AggregationBuilder.slice_buckets_for_page(data_buckets, page, per_page)

        # 9. Parse sliced buckets into rows for display
        rows = result_parser({"aggregations": {"paginated_results": {"buckets": page_buckets}}})

        # 9a. Calculate totals from ALL buckets (totals query) for accuracy
        totals = self._calculate_totals_from_aggregation_result(
            aggregation_result=totals_result,
            result_parser=result_parser,
            columns=columns,
        )

        # 10. Determine if more pages exist
        if use_bucket_selector:
            # With bucket_selector, we have all buckets, so just check if there are more
            has_more = (page + 1) * per_page < total_count
        else:
            # Standard pagination check
            has_more = len(data_buckets) == data_fetch_size and (page + 1) * per_page < total_count

        # 11. Format and return response
        execution_time_ms = (time.time() - start_time) * 1000
        filters_applied = self._build_filters_applied(time_period, start_dt, end_dt, users, projects)

        # Build pagination dict directly to override has_more calculation
        pagination = {
            "page": page,
            "per_page": per_page,
            "total_count": total_count,
            "has_more": has_more,
        }

        response = ResponseFormatter.format_tabular_response(
            rows=rows,
            columns=columns,
            filters_applied=filters_applied,
            execution_time_ms=execution_time_ms,
            totals=totals,
        )
        response["pagination"] = pagination

        return response

    async def execute_tabular_query_with_flattened_rows(
        self,
        agg_builder: Callable[[dict, int], dict],
        result_parser: Callable[[dict], list[dict]],
        columns: list[dict],
        flattening_multiplier: int = 10,
        sort_keys: list[tuple[str, bool]] | None = None,
        metric_filters: list[str] | None = None,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
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
            metric_filters: Optional metric name filters
            time_period: Predefined time range
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by users
            projects: Filter by projects
            page: Page number (0-indexed)
            per_page: Items per page

        Returns:
            Tabular response with rows, columns, metadata, and pagination
        """
        start_time = time.time()

        # 1. Parse time and build base query with filters
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._build_query(start_dt, end_dt, users, projects, metric_filters)

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
        all_rows = result_parser(result)

        # 6. Sort rows consistently using multi-level sort keys
        # This ensures deterministic ordering across pages (no duplicates/skips)
        if sort_keys:
            for field, reverse in reversed(sort_keys):
                all_rows.sort(key=lambda x, f=field: x.get(f, 0), reverse=reverse)

        # 7. Calculate total_count from parsed rows
        # Note: This is an approximation if dataset is larger than fetch_size
        # For most use cases with multiplier=10, this is accurate enough
        total_count = len(all_rows)

        # 8. Slice rows for current page
        start_idx = page * per_page
        end_idx = start_idx + per_page
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
        filters_applied = self._build_filters_applied(time_period, start_dt, end_dt, users, projects)

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
        metric_filters: list[str] | None = None,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        timestamp_field: str = "@timestamp",
    ) -> dict:
        """Execute analytics query that returns summary metrics.

        Args:
            agg_builder: Function that builds aggregation body from query
            metrics_builder: Function that builds metrics list from ES result
            metric_filters: Optional metric name filters
            time_period: Predefined time range
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by users
            projects: Filter by projects
            timestamp_field: Elasticsearch timestamp field for time range filter

        Returns:
            Summary response with metrics and metadata
        """
        start_time = time.time()

        # Parse time and build query
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._build_query(start_dt, end_dt, users, projects, metric_filters, timestamp_field)

        # Build aggregation
        agg_body = agg_builder(query)

        # Execute
        result = await self._repository.execute_aggregation_query(agg_body)

        # Build metrics
        metrics = metrics_builder(result)

        # Format response
        execution_time_ms = (time.time() - start_time) * 1000
        filters_applied = self._build_filters_applied(time_period, start_dt, end_dt, users, projects)

        return ResponseFormatter.format_summary_response(
            metrics=metrics, filters_applied=filters_applied, execution_time_ms=execution_time_ms
        )

    async def execute_composite_query(
        self,
        agg_builder: Callable[[dict], dict],
        result_parser: Callable[[dict, dict], dict],
        metric_filters: list[str] | None = None,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Execute analytics query with custom aggregation and response format.

        This method provides a flexible pipeline for queries that don't fit
        the tabular or summary patterns (e.g., composite aggregations, custom
        response structures).

        Args:
            agg_builder: Function that builds aggregation body from query
            result_parser: Function that parses ES result and metadata into final response dict
            metric_filters: Optional metric name filters
            time_period: Predefined time range
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by users
            projects: Filter by projects

        Returns:
            Custom response dict with data and metadata (format defined by result_parser)
        """
        start_time = time.time()

        # 1. Parse time and build secure query with filters
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._build_query(start_dt, end_dt, users, projects, metric_filters)

        # 2. Build aggregation body
        agg_body = agg_builder(query)

        # 3. Execute aggregation
        result = await self._repository.execute_aggregation_query(agg_body)

        # 4. Parse result into final response (with metadata)
        execution_time_ms = (time.time() - start_time) * 1000
        filters_applied = self._build_filters_applied(time_period, start_dt, end_dt, users, projects)
        metadata = ResponseFormatter.create_metadata(filters_applied, execution_time_ms)

        # 5. Let result_parser build final response with metadata
        return result_parser(result, metadata)

    async def execute_esql_query(
        self,
        esql_query: str,
        result_parser: Callable[[dict], list[dict]],
        columns: list[dict],
        metric_filters: list[str] | None = None,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Execute ES|QL query with in-memory pagination.

        Note: ES|QL results are capped at 10,000 rows by Elasticsearch.
        All results are fetched first, then paginated in-memory using array slicing.

        Args:
            esql_query: ES|QL query string
            result_parser: Function that parses ES|QL result to list of row dicts
            columns: Column definitions for response
            metric_filters: Optional metric name filters
            time_period: Predefined time range
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by users
            projects: Filter by projects
            page: Page number (0-indexed)
            per_page: Items per page

        Returns:
            Tabular response with rows, columns, metadata, and pagination
        """
        start_time = time.time()

        # 1. Parse time and build filter query
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        filter_query = self._build_query(start_dt, end_dt, users, projects, metric_filters)

        # 2. Execute ES|QL query (fetch ALL results, up to 10k limit)
        result = await self._repository.execute_esql_query(esql_query, filter_query=filter_query)

        # 3. Parse ALL results
        all_rows = result_parser(result)

        # 4. Calculate total count and pagination indices
        total_count = len(all_rows)
        start_idx = page * per_page
        end_idx = start_idx + per_page

        # 5. Slice rows for current page
        paginated_rows = all_rows[start_idx:end_idx]

        # 5a. Calculate totals from ALL rows (this works correctly because we fetch ALL data first)
        totals = TotalsCalculator.calculate_totals(columns=columns, rows=all_rows)

        # 6. Calculate execution time and build filters
        execution_time_ms = (time.time() - start_time) * 1000
        filters_applied = self._build_filters_applied(time_period, start_dt, end_dt, users, projects)

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

    def _build_query(
        self,
        start_dt: datetime,
        end_dt: datetime,
        users: list[str] | None,
        projects: list[str] | None,
        metric_filters: list[str] | None,
        timestamp_field: str = "@timestamp",
    ) -> dict:
        """Build secure query with all filters."""
        query_builder = SecureQueryBuilder(self._user)
        query_builder.add_time_range(start_dt, end_dt, timestamp_field)

        if metric_filters:
            query_builder.add_metric_filter(metric_filters)
        if users:
            query_builder.add_user_filter(users)
        if projects:
            query_builder.add_project_filter(projects)

        return query_builder.build()

    def _build_query_without_time_filter(
        self,
        users: list[str] | None,
        projects: list[str] | None,
        metric_filters: list[str] | None,
    ) -> dict:
        """Build secure query WITHOUT time filter — for all-time engagement metrics (DAU, MAU, weekly).

        Access control (project/user scoping) is still fully applied.
        """
        query_builder = SecureQueryBuilder(self._user)
        # NOTE: no add_time_range() — intentionally omitted for all-time queries
        if metric_filters:
            query_builder.add_metric_filter(metric_filters)
        if users:
            query_builder.add_user_filter(users)
        if projects:
            query_builder.add_project_filter(projects)

        return query_builder.build()

    def _build_filters_applied(
        self,
        time_period: str | None,
        start_dt: datetime,
        end_dt: datetime,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> dict:
        """Build filters_applied dictionary."""
        return {
            "time_period": time_period or "custom",
            "start_date": start_dt.isoformat() if not time_period else None,
            "end_date": end_dt.isoformat() if not time_period else None,
            "users": users,
            "projects": projects,
        }
