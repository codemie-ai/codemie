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

"""Response formatting for analytics API.

This module transforms Elasticsearch results into structured API responses
with consistent metadata and pagination information.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TypedDict


class Metadata(TypedDict):
    """Metadata structure for analytics responses."""

    timestamp: str
    data_as_of: str
    filters_applied: dict[str, Any]
    execution_time_ms: float


class Pagination(TypedDict):
    """Pagination structure for analytics responses."""

    page: int
    per_page: int
    total_count: int
    has_more: bool


class Metric(TypedDict, total=False):
    """Metric structure for summary responses."""

    id: str
    label: str
    type: str
    value: int | float | str
    format: str
    description: str


class Column(TypedDict, total=False):
    """Column definition for tabular responses."""

    id: str
    label: str
    type: str
    format: str
    description: str


class ResponseFormatter:
    """Formats Elasticsearch results into API response models."""

    @staticmethod
    def create_metadata(filters_applied: dict[str, Any], execution_time_ms: float) -> Metadata:
        """Create standard metadata for analytics responses.

        Args:
            filters_applied: Dict of filters that were applied to the query
            execution_time_ms: Query execution time in milliseconds

        Returns:
            Metadata dict with timestamp, data_as_of, filters_applied, execution_time_ms
        """
        now = datetime.now(timezone.utc)
        return Metadata(
            timestamp=now.isoformat(),
            data_as_of=now.isoformat(),
            filters_applied=filters_applied,
            execution_time_ms=round(execution_time_ms, 2),
        )

    @staticmethod
    def create_pagination(page: int, per_page: int, total_count: int) -> Pagination:
        """Create pagination metadata.

        Args:
            page: Current page number (zero-indexed)
            per_page: Items per page
            total_count: Total number of items

        Returns:
            Pagination dict with page, per_page, total_count, has_more
        """
        has_more = (page + 1) * per_page < total_count
        return Pagination(page=page, per_page=per_page, total_count=total_count, has_more=has_more)

    @staticmethod
    def format_summary_response(
        metrics: list[dict[str, Any]], filters_applied: dict[str, Any], execution_time_ms: float
    ) -> dict[str, Any]:
        """Format summary metrics response.

        Args:
            metrics: List of metric dicts conforming to Metric structure (id, label, type, value, etc.)
            filters_applied: Filters applied to query
            execution_time_ms: Query execution time

        Returns:
            Complete summary response with data and metadata
        """
        return {
            "data": {"metrics": metrics},
            "metadata": ResponseFormatter.create_metadata(filters_applied, execution_time_ms),
        }

    @staticmethod
    def format_tabular_response(
        columns: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        filters_applied: dict[str, Any],
        execution_time_ms: float,
        totals: dict[str, Any] | None = None,
        page: int | None = None,
        per_page: int | None = None,
        total_count: int | None = None,
    ) -> dict[str, Any]:
        """Format tabular data response.

        Args:
            columns: Column definitions conforming to Column structure (id, label, type, format, description)
            rows: Data rows
            filters_applied: Filters applied to query
            execution_time_ms: Query execution time
            totals: Optional totals/summary row (default: None)
            page: Current page number (for paginated responses)
            per_page: Items per page (for paginated responses)
            total_count: Total items (for paginated responses)

        Returns:
            Complete tabular response with data, metadata, and optional pagination
        """
        response: dict[str, Any] = {
            "data": {"columns": columns, "rows": rows},
            "metadata": ResponseFormatter.create_metadata(filters_applied, execution_time_ms),
        }

        # Add totals if provided
        if totals is not None:
            response["data"]["totals"] = totals

        # Add pagination if all pagination params provided
        if page is not None and per_page is not None and total_count is not None:
            response["pagination"] = ResponseFormatter.create_pagination(page, per_page, total_count)

        return response
