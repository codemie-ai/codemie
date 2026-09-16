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

"""Base handler for CLI analytics."""

from __future__ import annotations

from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.cli_cost_processor import CLICostAdjustmentMixin
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline
from codemie.service.analytics.response_formatter import ResponseFormatter
from codemie.service.analytics.time_parser import TimeParser


class CLIBaseHandler(CLICostAdjustmentMixin):
    """Base handler for CLI analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository) -> None:
        """Initialize cli base handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)
        self.repository = repository

    def _extract_top_metric(self, bucket: dict, agg_name: str, metric_field: str) -> str | None:
        """Extract top_metrics string value."""
        top_values = bucket.get(agg_name, {}).get("top", [])
        if not top_values:
            return None
        return top_values[0].get("metrics", {}).get(metric_field)

    def _build_filters_applied(
        self,
        time_period: str | None,
        start_dt: datetime,
        end_dt: datetime,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> dict:
        """Proxy shared pipeline filter formatting."""
        return self._pipeline._build_filters_applied(time_period, start_dt, end_dt, users, projects)

    def _format_custom_tabular_response(
        self,
        rows: list[dict],
        columns: list[dict],
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
        page: int,
        per_page: int,
    ) -> dict:
        """Format custom tabular rows with pagination and metadata."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        total_count = len(rows)
        paginated_rows = rows[page * per_page : (page + 1) * per_page]
        filters_applied = self._build_filters_applied(time_period, start_dt, end_dt, users, projects)
        return ResponseFormatter.format_tabular_response(
            rows=paginated_rows,
            columns=columns,
            filters_applied=filters_applied,
            execution_time_ms=0.0,
            page=page,
            per_page=per_page,
            total_count=total_count,
        )

    def _format_custom_summary_response(
        self,
        metrics: list[dict],
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> dict:
        """Format custom summary metrics with standard analytics metadata."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        filters_applied = self._build_filters_applied(time_period, start_dt, end_dt, users, projects)
        return ResponseFormatter.format_summary_response(
            metrics=metrics,
            filters_applied=filters_applied,
            execution_time_ms=0.0,
        )

    def _format_custom_detail_response(
        self,
        detail: dict,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> dict:
        """Format custom detail payload with standard analytics metadata."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        filters_applied = self._build_filters_applied(time_period, start_dt, end_dt, users, projects)
        return {
            "data": detail,
            "metadata": ResponseFormatter.create_metadata(filters_applied, 0.0),
        }
