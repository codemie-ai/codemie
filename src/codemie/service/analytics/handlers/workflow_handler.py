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

"""Workflow analytics handler."""

from __future__ import annotations

import logging
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import USER_EMAIL_KEYWORD_FIELD, USER_NAME_KEYWORD_FIELD
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline

logger = logging.getLogger(__name__)

# Elasticsearch field constants
STATUS_FIELD = "attributes.status.keyword"
EXECUTION_ID_KEYWORD_FIELD = "attributes.execution_id.keyword"
WORKFLOW_NAME_KEYWORD_FIELD = "attributes.workflow_name.keyword"


class WorkflowHandler:
    """Handler for workflow execution analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize workflow handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_workflows(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get workflow execution analytics.

        Returns metrics for workflow runs, success rates, costs, and performance.
        """
        logger.info("Requesting workflows analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_workflows_aggregation(query, fetch_size),
            result_parser=self._parse_workflows_result,
            columns=self._get_workflows_columns(),
            group_by_field=WORKFLOW_NAME_KEYWORD_FIELD,
            metric_filters=[MetricName.WORKFLOW_EXECUTION_TOTAL.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
            use_bucket_selector=True,
        )

    def _build_workflows_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for workflows with fetch-and-slice.

        Note: Uses Elasticsearch cardinality aggregations for unique execution counts.
        Cardinality uses HyperLogLog++ algorithm which provides approximate counts
        (not exact) with ~3% error rate for large datasets. Default precision_threshold
        is 3000 - counts below this are exact, counts above are approximate.
        """
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (metrics)
        sub_aggs = {
            "unique_executions": {"cardinality": {"field": EXECUTION_ID_KEYWORD_FIELD}},
            "successful": {
                "filter": {"term": {STATUS_FIELD: "SUCCEEDED"}},
                "aggs": {"unique_count": {"cardinality": {"field": EXECUTION_ID_KEYWORD_FIELD}}},
            },
            "failed": {
                "filter": {"term": {STATUS_FIELD: "FAILED"}},
                "aggs": {"unique_count": {"cardinality": {"field": EXECUTION_ID_KEYWORD_FIELD}}},
            },
            "aborted": {
                "filter": {"term": {STATUS_FIELD: "ABORTED"}},
                "aggs": {"unique_count": {"cardinality": {"field": EXECUTION_ID_KEYWORD_FIELD}}},
            },
            "total_cost": {"sum": {"field": "attributes.money_spent"}},
            "total_tokens": {
                "sum": {
                    "script": {
                        "source": (
                            "(doc.containsKey('attributes.input_tokens') && "
                            "!doc['attributes.input_tokens'].empty ? "
                            "doc['attributes.input_tokens'].value : 0) + "
                            "(doc.containsKey('attributes.output_tokens') && "
                            "!doc['attributes.output_tokens'].empty ? "
                            "doc['attributes.output_tokens'].value : 0)"
                        )
                    }
                }
            },
            "avg_time": {"avg": {"field": "attributes.execution_time"}},
            "unique_users": {"cardinality": {"field": USER_NAME_KEYWORD_FIELD}},
            "last_run": {"max": {"field": "@timestamp"}},
        }

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=WORKFLOW_NAME_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"_count": "desc"},
            sub_aggs=sub_aggs,
        )

        # Construct full aggregation body
        agg_body = {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": terms_agg,
            },
        }

        return agg_body

    def _parse_workflows_result(self, result: dict) -> list[dict]:
        """Parse result for workflows."""
        rows = []
        # Extract buckets from paginated_results (already sliced by pipeline)
        workflows_buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        for bucket in workflows_buckets:
            try:
                workflow_name = bucket["key"]
                total_runs = bucket["unique_executions"]["value"]
                successful = bucket["successful"]["unique_count"]["value"]
                failed = bucket["failed"]["unique_count"]["value"]
                aborted = bucket["aborted"]["unique_count"]["value"]
                total_cost = bucket["total_cost"]["value"]
                total_tokens = bucket["total_tokens"]["value"]
                avg_time = bucket["avg_time"]["value"] or 0
                unique_users = bucket["unique_users"]["value"]
                last_run = bucket["last_run"]["value_as_string"]
            except KeyError as e:
                logger.error(
                    f"Missing expected aggregation field in Elasticsearch response: {e}, "
                    f"available bucket keys: {bucket.keys()}"
                )
                continue

            success_rate = (successful / total_runs * 100) if total_runs > 0 else 0
            avg_cost = (total_cost / total_runs) if total_runs > 0 else 0

            rows.append(
                {
                    "workflow_name": workflow_name,
                    "total_runs": int(total_runs),
                    "success": int(successful),
                    "failed": int(failed),
                    "aborted": int(aborted),
                    "success_rate_percent": round(success_rate, 2),
                    "total_cost_usd": round(total_cost, 4),
                    "avg_cost_usd": round(avg_cost, 5),
                    "total_tokens": int(total_tokens),
                    "avg_time_seconds": round(avg_time, 2),
                    "users": int(unique_users),
                    "last_run": last_run,
                }
            )

        logger.debug(
            f"Parsed workflows result: total_workflow_buckets={len(workflows_buckets)}, "
            f"rows_parsed={len(rows)}, "
            f"successful_workflows={sum(1 for row in rows if row['success'] > 0)}, "
            f"failed_workflows={sum(1 for row in rows if row['failed'] > 0)}, "
            f"aborted_workflows={sum(1 for row in rows if row['aborted'] > 0)}"
        )
        return rows

    def _get_workflows_columns(self) -> list[dict]:
        """Get column definitions for workflows."""
        return [
            {"id": "workflow_name", "label": "Workflow Name", "type": "string"},
            {"id": "total_runs", "label": "Total Runs", "type": "number"},
            {"id": "success", "label": "Success", "type": "number"},
            {"id": "failed", "label": "Failed", "type": "number"},
            {"id": "aborted", "label": "Aborted", "type": "number"},
            {"id": "success_rate_percent", "label": "Success Rate (%)", "type": "number", "format": "percentage"},
            {"id": "total_cost_usd", "label": "Total Cost ($)", "type": "number", "format": "currency"},
            {"id": "avg_cost_usd", "label": "Avg Cost ($)", "type": "number", "format": "currency"},
            {"id": "total_tokens", "label": "Total Tokens", "type": "number"},
            {"id": "avg_time_seconds", "label": "Avg Time (s)", "type": "number"},
            {"id": "users", "label": "Users", "type": "number"},
            {"id": "last_run", "label": "Last Run", "type": "string", "format": "timestamp"},
        ]

    async def get_top_workflow_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get top workflow usage: invocations, cost, unique users, and most recent user per workflow."""
        from codemie.service.analytics.handlers.field_constants import METRIC_NAME_KEYWORD_FIELD

        logger.info("Requesting top-workflow-usage analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_top_workflow_usage_aggregation(
                query, fetch_size, METRIC_NAME_KEYWORD_FIELD
            ),
            result_parser=self._parse_top_workflow_usage_result,
            columns=self._get_top_workflow_usage_columns(),
            group_by_field=WORKFLOW_NAME_KEYWORD_FIELD,
            metric_filters=None,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
            use_bucket_selector=True,
        )

    def _build_top_workflow_usage_aggregation(self, query: dict, fetch_size: int, metric_name_field: str) -> dict:
        """Build terms aggregation for top workflow usage."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        def _workflow_filter() -> dict:
            return {
                "bool": {
                    "filter": [
                        {
                            "bool": {
                                "should": [
                                    {"term": {metric_name_field: {"value": MetricName.WORKFLOW_EXECUTION_TOTAL.value}}}
                                ],
                                "minimum_should_match": 1,
                            }
                        }
                    ]
                }
            }

        sub_aggs = {
            "1-bucket": {"filter": _workflow_filter()},
            "2-bucket": {
                "filter": _workflow_filter(),
                "aggs": {"2-metric": {"sum": {"field": "attributes.money_spent"}}},
            },
            "3-bucket": {
                "filter": _workflow_filter(),
                "aggs": {"3-metric": {"cardinality": {"field": "attributes.user_id.keyword"}}},
            },
            "4-bucket": {
                "filter": {"exists": {"field": USER_EMAIL_KEYWORD_FIELD}},
                "aggs": {
                    "4-metric": {
                        "top_metrics": {
                            "metrics": {"field": USER_EMAIL_KEYWORD_FIELD},
                            "size": 1,
                            "sort": {"@timestamp": "desc"},
                        }
                    }
                },
            },
        }

        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=WORKFLOW_NAME_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"1-bucket": "desc"},
            sub_aggs=sub_aggs,
        )

        return {
            "query": query,
            "size": 0,
            "aggs": {"paginated_results": terms_agg},
        }

    def _parse_top_workflow_usage_result(self, result: dict) -> list[dict]:
        """Parse result for top workflow usage."""
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = []
        for bucket in buckets:
            workflow_name = bucket["key"]
            invocations = bucket.get("1-bucket", {}).get("doc_count", 0)
            money_spent = bucket.get("2-bucket", {}).get("2-metric", {}).get("value", 0.0)
            unique_users = bucket.get("3-bucket", {}).get("3-metric", {}).get("value", 0)

            recent_user = None
            top = bucket.get("4-bucket", {}).get("4-metric", {}).get("top", [])
            if top:
                recent_user = top[0].get("metrics", {}).get(USER_EMAIL_KEYWORD_FIELD)

            rows.append(
                {
                    "workflow_name": workflow_name,
                    "invocations": invocations,
                    "money_spent": round(money_spent or 0, 4),
                    "unique_users": unique_users,
                    "recent_user": recent_user or "N/A",
                }
            )
        logger.debug(f"Parsed top-workflow-usage result: total_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_top_workflow_usage_columns(self) -> list[dict]:
        """Get column definitions for top workflow usage."""
        return [
            {"id": "workflow_name", "label": "Workflow Name", "type": "string"},
            {"id": "invocations", "label": "Invocations", "type": "number"},
            {"id": "money_spent", "label": "Money Spent ($)", "type": "number", "format": "currency"},
            {"id": "unique_users", "label": "Unique Users", "type": "number"},
            {"id": "recent_user", "label": "Recent User", "type": "string"},
        ]
