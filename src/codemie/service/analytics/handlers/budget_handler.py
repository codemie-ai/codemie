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

"""Handler for budget analytics."""

from __future__ import annotations

import logging
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline

logger = logging.getLogger(__name__)

# Elasticsearch field constants
USER_EMAIL_KEYWORD_FIELD = "attributes.user_email.keyword"


class BudgetHandler:
    """Handler for budget analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize budget handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_budget_soft_limit(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get budget soft limit warnings by user with maximum spending amounts."""
        logger.info("Requesting budget-soft-limit analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_budget_soft_limit_aggregation(query, fetch_size),
            result_parser=self._parse_budget_limit_result,
            columns=self._get_budget_limit_columns(),
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            metric_filters=[MetricName.BUDGET_SOFT_LIMIT_WARNING.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_budget_soft_limit_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for budget soft limit with fetch-and-slice."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (metrics)
        sub_aggs = {
            "max_spent": {"max": {"field": "attributes.spent"}},
        }

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"max_spent": "desc"},
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

    async def get_budget_hard_limit(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get budget hard limit violations by user with maximum spending amounts."""
        logger.info("Requesting budget-hard-limit analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_budget_hard_limit_aggregation(query, fetch_size),
            result_parser=self._parse_budget_limit_result,
            columns=self._get_budget_limit_columns(),
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            metric_filters=[MetricName.BUDGET_HARD_LIMIT_VIOLATION.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_budget_hard_limit_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for budget hard limit with fetch-and-slice."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (metrics)
        sub_aggs = {
            "max_spent": {"max": {"field": "attributes.spent"}},
        }

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"max_spent": "desc"},
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

    def _parse_budget_limit_result(self, result: dict) -> list[dict]:
        """Parse result for budget limit (used by both soft and hard limit)."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = [
            {
                "user_email": bucket["key"],
                "max_spent": round(bucket["max_spent"]["value"] or 0, 2),
            }
            for bucket in buckets
        ]
        logger.debug(
            f"Parsed budget limit result: total_buckets={len(buckets)}, "
            f"rows_parsed={len(rows)}, "
            f"has_aggregations={bool(result.get('aggregations'))}"
        )
        return rows

    def _get_budget_limit_columns(self) -> list[dict]:
        """Get column definitions for budget limit (used by both soft and hard limit)."""
        return [
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "max_spent", "label": "Max Spent ($)", "type": "number", "format": "currency"},
        ]
