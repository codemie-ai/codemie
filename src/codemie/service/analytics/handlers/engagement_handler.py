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

"""Engagement analytics handler: DAU, MAU, and weekly spending histogram.

DAU: distinct users active in the **last 24 hours** (fixed window, ignores
     the dashboard time filter) — matches Kibana cardinality widget.
MAU: distinct users active in the **last 30 days** (rolling window, ignores
     the dashboard time filter) — matches Kibana "Last 1 month" cardinality widget.

Weekly spending covers the **last 7 days** (fixed window, ignores the dashboard
time filter) in 3h buckets, split by traffic class (Assistants, Workflows,
Datasources, CLI). Its money is read from the spend port, not from this
handler's own Elasticsearch aggregation, so a series means here exactly what it
means on every other money surface.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import PLACEHOLDER_USER_IDS
from codemie.service.analytics.handlers.report_filter import report_filter_for
from codemie.service.analytics.query_pipeline import (
    AnalyticsQueryFilters,
    AnalyticsQueryPipeline,
    tabular_filters_applied,
)
from codemie.service.analytics.response_formatter import ResponseFormatter
from codemie.service.analytics.time_parser import TimeParser
from codemie.service.spend.coverage import SpendResult
from codemie.service.spend.ledger import BUCKET_WIDTH_3_HOURS, bucket_starts
from codemie.service.spend.rendering import render_spend, report_period_or_none, spend_fields, time_range_or_none
from codemie.service.spend.spend_service import spend_service
from codemie.service.spend.traffic_class import TrafficClass

logger = logging.getLogger(__name__)

# Elasticsearch field constants
USER_ID_KEYWORD_FIELD = "attributes.user_id.keyword"

#: The window the chart draws, whatever the dashboard's time filter says - the widget is labelled
#: "Last 1 week" and has always ignored that filter.
WEEKLY_TIME_PERIOD = "last_7_days"

#: Which traffic class each series of the chart is. **The mapping is the whole definition of a
#: series**: the port owns what "workflow spend" selects, and a predicate assembled beside it here
#: would be a second definition free to disagree with every other surface for the same week.
_SERIES_CLASSES: Mapping[str, TrafficClass] = {
    "assistants_spent": TrafficClass.ASSISTANT,
    "workflows_spent": TrafficClass.WORKFLOW,
    "datasources_spent": TrafficClass.DATASOURCE,
    "cli_spent": TrafficClass.CLI,
}


def _series_cells(series: str, result: SpendResult | None) -> dict[str, object]:
    """One series' value on a bucket, with the three keys that qualify it.

    The qualification keys are prefixed with the series id exactly as `spend_footer` prefixes a
    column's, because one row carries four independent figures: an unprefixed triple would say
    *something on this bucket is short* without naming which line of the chart it is short on.

    A measured zero stays `0.0` and an absent figure is `None`, never `0`. The widget draws a `None`
    as a gap and a number as a bar, so the distinction survives all the way to the screen - which is
    the whole point, since a fabricated zero there reads as *nothing was spent*.
    """
    rendered = render_spend(result)
    cells: dict[str, object] = {series: None if rendered.figure is None else round(rendered.figure, 2)}
    cells.update({f"{series}_{key}": value for key, value in spend_fields(rendered).items()})
    return cells


class EngagementHandler:
    """Handler for engagement metrics: DAU, MAU, and weekly usage histogram.

    DAU and MAU ignore the dashboard time filter: they delegate to
    ``AnalyticsQueryPipeline.execute_summary_query`` with fixed
    ``time_period`` literals ("last_24_hours" / "last_30_days").
    The weekly histogram issues no Elasticsearch query at all: it reads the
    spend port over its own fixed window and uses the pipeline only for the
    access boundary the port must be narrowed by.
    """

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize engagement handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    # ------------------------------------------------------------------
    # DAU  (Daily Active Users — distinct users in the last 24 hours)
    # ------------------------------------------------------------------

    async def get_dau(
        self,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get Daily Active Users (DAU) metric.

        Returns the count of distinct users active in the last 24 hours,
        matching the Kibana cardinality-over-1d widget.  The dashboard time
        filter is intentionally ignored — DAU always covers "last 24 hours".

        Args:
            users: Optional user-scope filter (access-control).
            projects: Optional project-scope filter (access-control).

        Returns:
            SummariesResponse with a single ``dau`` metric.
        """
        logger.info("Requesting DAU engagement metric (last 24 hours)")

        return await self._pipeline.execute_summary_query(
            agg_builder=self._build_dau_aggregation,
            metrics_builder=self._parse_dau_result,
            filters=AnalyticsQueryFilters(
                metric_filters=None,
                time_period="last_24_hours",
                users=users,
                projects=projects,
            ),
        )

    def _build_dau_aggregation(self, query: dict) -> dict:
        """Build cardinality aggregation for DAU — distinct users over the query window."""
        return {
            "query": {
                "bool": {
                    "must": [query],
                    "must_not": [{"terms": {USER_ID_KEYWORD_FIELD: PLACEHOLDER_USER_IDS}}],
                }
            },
            "size": 0,
            "aggs": {
                "unique_users": {
                    "cardinality": {
                        "field": USER_ID_KEYWORD_FIELD,
                        "precision_threshold": 3000,
                    }
                }
            },
        }

    def _parse_dau_result(self, result: dict) -> list[dict]:
        """Extract DAU count from cardinality aggregation."""
        dau_value = int(result.get("aggregations", {}).get("unique_users", {}).get("value", 0))
        logger.debug(f"DAU: unique_users={dau_value}")
        return [self._dau_metric(dau_value)]

    @staticmethod
    def _dau_metric(value: int) -> dict:
        return {
            "id": "dau",
            "label": "DAU",
            "type": "number",
            "value": value,
            "format": "number",
            "description": "Distinct users active in the last 24 hours",
            "fixed_timeframe": "Last 1 day",
        }

    # ------------------------------------------------------------------
    # MAU  (Monthly Active Users — distinct users in the last 30 days)
    # ------------------------------------------------------------------

    async def get_mau(
        self,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get Monthly Active Users (MAU) — distinct users in the last 30 days.

        Uses a rolling 30-day window (matching Kibana's "Last 1 month" widget)
        instead of a calendar-month bucket, so partial months are not under-counted.
        The dashboard time filter is intentionally ignored.

        Args:
            users: Optional user-scope filter (access-control).
            projects: Optional project-scope filter (access-control).

        Returns:
            SummariesResponse with a single ``mau`` metric.
        """
        logger.info("Requesting MAU engagement metric (last 30 days)")

        return await self._pipeline.execute_summary_query(
            agg_builder=self._build_mau_aggregation,
            metrics_builder=self._parse_mau_result,
            filters=AnalyticsQueryFilters(
                metric_filters=None,
                time_period="last_30_days",
                users=users,
                projects=projects,
            ),
        )

    def _build_mau_aggregation(self, query: dict) -> dict:
        """Build cardinality aggregation for MAU — distinct users over last 30 days."""
        return {
            "query": {
                "bool": {
                    "must": [query],
                    "must_not": [{"terms": {USER_ID_KEYWORD_FIELD: PLACEHOLDER_USER_IDS}}],
                }
            },
            "size": 0,
            "aggs": {
                "unique_users": {
                    "cardinality": {
                        "field": USER_ID_KEYWORD_FIELD,
                        "precision_threshold": 3000,
                    }
                }
            },
        }

    def _parse_mau_result(self, result: dict) -> list[dict]:
        """Extract MAU count from cardinality aggregation."""
        mau_value = int(result.get("aggregations", {}).get("unique_users", {}).get("value", 0))
        logger.debug(f"MAU: unique_users={mau_value}")
        return [self._mau_metric(mau_value)]

    @staticmethod
    def _mau_metric(value: int) -> dict:
        return {
            "id": "mau",
            "label": "MAU",
            "type": "number",
            "value": value,
            "format": "number",
            "description": "Distinct users active in the last 30 days",
            "fixed_timeframe": "Last 1 month",
        }

    # ------------------------------------------------------------------
    # Weekly spending histogram (always the last 7 days)
    # ------------------------------------------------------------------

    async def get_weekly_spending(
        self,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        client_source: str | None = None,
    ) -> dict:
        """Get weekly spending histogram broken down by source.

        **Money comes from the spend port and no Elasticsearch query is issued.** The chart used to
        split spend by groups of metric names, which answered a different question from every other
        money surface: the same week could be drawn here as one figure and reported elsewhere as
        another, with neither wrong by its own definition. `TrafficClass` is the one definition of a
        series, and it lives with the predicates that select it.

        **Below the coverage floor, and in a deployment with no LiteLLM ledger, the three non-CLI
        series are absent rather than zero.** The metric store has a word for CLI traffic and none
        for the other three, so the pre-floor adapter draws that one line and leaves the rest
        unanswered; the widget renders those as gaps. Reported as zeros they would claim no
        assistant, workflow or datasource money was spent that week, which is the fabrication this
        whole port exists to stop - the owner accepted the gap over the false zero.

        **The rows are the port's own bucket walk, not the requested window.** A three-hour bin files
        07:12 under 06:00, so the first bucket may start before the window does; walking from the
        requested instant instead would key every row a partial step off the ledger's own keys and
        match none of them. Buckets nobody spent in are still walked, so an idle stretch is a row of
        measured zeros rather than a hole in the chart.

        Args:
            users: Optional user-scope filter (access-control).
            projects: Optional project-scope filter (access-control).
            client_source: Optional client-source filter; the port cannot apply it, so every series
                is then absent.

        Returns:
            TabularResponse with columns: time, assistants_spent, workflows_spent,
            datasources_spent, cli_spent.
        """
        started = time.time()
        logger.info("Requesting weekly spending histogram (last 7 days, ignores time filter)")

        start_dt, end_dt = TimeParser.parse(WEEKLY_TIME_PERIOD, None, None)
        period = time_range_or_none(start_dt, end_dt)
        # The week is walked even when the port is not asked - a client-source filter it cannot apply
        # - so the chart keeps its buckets and every series on them reads absent, never zero.
        asked = report_period_or_none(start_dt, end_dt, client_source)
        # The figure is narrowed by the boundary the dashboard's own queries are built under, taken
        # from the pipeline so that one access context answers for the rows and for the money.
        split = (
            await spend_service.aggregate_by_time_bucket(
                asked,
                BUCKET_WIDTH_3_HOURS,
                access=self._pipeline.access_scope,
                report_filter=await report_filter_for(users, projects),
            )
            if asked is not None
            else {}
        )

        buckets = bucket_starts(period, BUCKET_WIDTH_3_HOURS) if period is not None else ()
        rows = [self._bucket_row(bucket, split) for bucket in buckets]

        response = ResponseFormatter.format_tabular_response(
            rows=rows,
            columns=self._get_weekly_spending_columns(),
            filters_applied=tabular_filters_applied(
                AnalyticsQueryFilters(
                    time_period=WEEKLY_TIME_PERIOD, users=users, projects=projects, client_source=client_source
                ),
                start_dt,
                end_dt,
            ),
            execution_time_ms=(time.time() - started) * 1000,
            totals=None,
        )
        response["pagination"] = {
            "page": 0,
            "per_page": len(rows),
            "total_count": len(rows),
            "has_more": False,
        }
        response["fixed_timeframe"] = "Last 1 week"
        return response

    @staticmethod
    def _bucket_row(bucket: datetime, split: Mapping[tuple[datetime, TrafficClass], SpendResult]) -> dict:
        """One bucket of the chart: its start, and the four series measured over it."""
        # ISO-8601 carrying the UTC offset, as the date-histogram keys were rendered before: the
        # widget builds a `Date` from this string, and an offset-less one is read in the browser's
        # own zone, which shifts every bar of the chart for a reader outside UTC.
        row: dict = {"time": bucket.isoformat()}
        for series, traffic_class in _SERIES_CLASSES.items():
            row.update(_series_cells(series, split.get((bucket, traffic_class))))
        return row

    @staticmethod
    def _get_weekly_spending_columns() -> list[dict]:
        """Get column definitions for weekly spending histogram."""
        return [
            {"id": "time", "label": "Time", "type": "string", "format": "timestamp"},
            {"id": "assistants_spent", "label": "Assistants ($)", "type": "number", "format": "currency"},
            {"id": "workflows_spent", "label": "Workflows ($)", "type": "number", "format": "currency"},
            {"id": "datasources_spent", "label": "Datasources ($)", "type": "number", "format": "currency"},
            {"id": "cli_spent", "label": "CLI ($)", "type": "number", "format": "currency"},
        ]
