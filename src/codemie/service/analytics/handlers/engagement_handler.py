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

Weekly spending uses the normal time filter and shows money spent in 3h
intervals broken down by source (Assistants, Workflows, Datasources, CLI).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline
from codemie.service.analytics.response_formatter import ResponseFormatter

logger = logging.getLogger(__name__)

# Elasticsearch field constants
USER_ID_KEYWORD_FIELD = "attributes.user_id.keyword"
METRIC_NAME_KEYWORD_FIELD = "metric_name.keyword"
CLI_REQUEST_FIELD = "attributes.cli_request"
MONEY_SPENT_FIELD = "attributes.money_spent"


class EngagementHandler:
    """Handler for engagement metrics: DAU, MAU, and weekly usage histogram.

    DAU and MAU ignore the dashboard time filter: they delegate to
    ``AnalyticsQueryPipeline.execute_summary_query`` with fixed
    ``time_period`` literals ("last_24_hours" / "last_30_days").
    Weekly histogram uses ``execute_composite_query`` with the
    caller-supplied time filter.
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
            metric_filters=None,
            time_period="last_24_hours",
            users=users,
            projects=projects,
        )

    def _build_dau_aggregation(self, query: dict) -> dict:
        """Build cardinality aggregation for DAU — distinct users over the query window."""
        return {
            "query": query,
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
            metric_filters=None,
            time_period="last_30_days",
            users=users,
            projects=projects,
        )

    def _build_mau_aggregation(self, query: dict) -> dict:
        """Build cardinality aggregation for MAU — distinct users over last 30 days."""
        return {
            "query": query,
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
    # Weekly spending histogram (respects dashboard time filter)
    # ------------------------------------------------------------------

    async def get_weekly_spending(
        self,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get weekly spending histogram broken down by source.

        Always covers the last 7 days regardless of the dashboard time filter,
        matching the Kibana behaviour for this widget.

        Args:
            users: Optional user-scope filter (access-control).
            projects: Optional project-scope filter (access-control).

        Returns:
            TabularResponse with columns: time, assistants_spent, workflows_spent,
            datasources_spent, cli_spent.
        """
        logger.info("Requesting weekly spending histogram (last 7 days, ignores time filter)")

        columns = self._get_weekly_spending_columns()

        def agg_builder(query: dict) -> dict:
            return self._build_weekly_spending_aggregation(query)

        def parse_result(result: dict, metadata: dict) -> dict:
            rows = self._parse_weekly_spending_result(result)
            response = ResponseFormatter.format_tabular_response(
                rows=rows,
                columns=columns,
                filters_applied=metadata.get("filters_applied", {}),
                execution_time_ms=metadata.get("execution_time_ms", 0),
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

        return await self._pipeline.execute_composite_query(
            agg_builder=agg_builder,
            result_parser=parse_result,
            metric_filters=None,
            time_period="last_7_days",
            users=users,
            projects=projects,
        )

    @staticmethod
    def _extract_time_bounds(query: dict) -> tuple[int | None, int | None]:
        """Extract min/max epoch-ms bounds from a bool-filter query for extended_bounds."""
        for clause in query.get("bool", {}).get("filter", []):
            range_filter = clause.get("range", {})
            for field in ("@timestamp", "time"):
                if field not in range_filter:
                    continue
                tf = range_filter[field]
                try:
                    gte = tf.get("gte")
                    lte = tf.get("lte")
                    if isinstance(gte, str):
                        gte = int(datetime.fromisoformat(gte.replace("Z", "+00:00")).timestamp() * 1000)
                    if isinstance(lte, str):
                        lte = int(datetime.fromisoformat(lte.replace("Z", "+00:00")).timestamp() * 1000)
                    return gte, lte
                except (ValueError, AttributeError) as exc:
                    logger.warning(f"Could not parse time bounds for extended_bounds: {exc}")
                    return None, None
        return None, None

    def _build_weekly_spending_aggregation(self, query: dict) -> dict:
        """Build 3h date-histogram aggregation with 4 spending sub-aggregations."""
        min_time, max_time = self._extract_time_bounds(query)

        date_histogram: dict = {
            "field": "time",
            "fixed_interval": "3h",
            "order": {"_key": "asc"},
        }
        if min_time is not None and max_time is not None:
            date_histogram["extended_bounds"] = {"min": min_time, "max": max_time}

        return {
            "query": query,
            "size": 0,
            "aggs": {
                "time_buckets": {
                    "date_histogram": date_histogram,
                    "aggs": {
                        "assistants_spent": {
                            "filter": {
                                "term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CONVERSATION_ASSISTANT_USAGE.value}
                            },
                            "aggs": {"sum": {"sum": {"field": MONEY_SPENT_FIELD}}},
                        },
                        "workflows_spent": {
                            "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.WORKFLOW_EXECUTION_TOTAL.value}},
                            "aggs": {"sum": {"sum": {"field": MONEY_SPENT_FIELD}}},
                        },
                        "datasources_spent": {
                            "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.DATASOURCE_TOKENS_USAGE.value}},
                            "aggs": {"sum": {"sum": {"field": MONEY_SPENT_FIELD}}},
                        },
                        "cli_spent": {
                            "filter": {
                                "bool": {
                                    "filter": [
                                        {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                                        {"term": {CLI_REQUEST_FIELD: True}},
                                    ]
                                }
                            },
                            "aggs": {"sum": {"sum": {"field": MONEY_SPENT_FIELD}}},
                        },
                    },
                }
            },
        }

    def _parse_weekly_spending_result(self, result: dict) -> list[dict]:
        """Parse 3h spending buckets into tabular rows."""
        buckets = result.get("aggregations", {}).get("time_buckets", {}).get("buckets", [])

        rows = []
        for bucket in buckets:
            try:
                key_millis = bucket.get("key")
                if key_millis is None:
                    logger.warning(f"Spending bucket missing key: {bucket}")
                    continue

                ts_iso = datetime.fromtimestamp(key_millis / 1000, tz=timezone.utc).isoformat()

                assistants = round(bucket.get("assistants_spent", {}).get("sum", {}).get("value", 0) or 0, 2)
                workflows = round(bucket.get("workflows_spent", {}).get("sum", {}).get("value", 0) or 0, 2)
                datasources = round(bucket.get("datasources_spent", {}).get("sum", {}).get("value", 0) or 0, 2)
                cli = round(bucket.get("cli_spent", {}).get("sum", {}).get("value", 0) or 0, 2)

                rows.append(
                    {
                        "time": ts_iso,
                        "assistants_spent": assistants,
                        "workflows_spent": workflows,
                        "datasources_spent": datasources,
                        "cli_spent": cli,
                    }
                )
            except (ValueError, TypeError, OSError) as e:
                logger.warning(f"Failed to parse spending bucket {bucket}: {e}. Skipping.")
                continue

        logger.debug(f"Weekly spending histogram: total_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

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
