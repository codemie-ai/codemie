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

"""MCP servers analytics handler."""

from __future__ import annotations

import logging
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import METRIC_NAME_KEYWORD_FIELD, USER_NAME_KEYWORD_FIELD
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline

logger = logging.getLogger(__name__)

# Elasticsearch field constants
MCP_NAME_KEYWORD_FIELD = "attributes.mcp_name.keyword"


class MCPHandler:
    """Handler for MCP servers analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize MCP handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    def _build_metric_filter_clauses(self) -> list[dict]:
        """Build metric name filter clauses for MCP metrics.

        Returns:
            List of Elasticsearch term filter clauses for MCP metric names
        """
        metric_names = MetricName.to_list_from_group(MetricName.MCP_METRICS)
        return [{"term": {METRIC_NAME_KEYWORD_FIELD: {"value": name}}} for name in metric_names]

    async def get_mcp_servers(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get MCP servers usage analytics."""
        logger.info("Requesting mcp-servers analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_mcp_servers_aggregation(query, fetch_size),
            result_parser=self._parse_mcp_servers_result,
            columns=self._get_mcp_servers_columns(),
            group_by_field=MCP_NAME_KEYWORD_FIELD,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_mcp_servers_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for MCP servers with fetch-and-slice."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Build metric name filter clauses
        metric_should_clauses = self._build_metric_filter_clauses()

        # Define sub-aggregations with metric filtering
        sub_aggs = {
            "filtered_requests": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": metric_should_clauses,
                                    "minimum_should_match": 1,
                                }
                            },
                            {"exists": {"field": MCP_NAME_KEYWORD_FIELD}},
                        ],
                    }
                }
            }
        }

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=MCP_NAME_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"filtered_requests": "desc"},
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

    def _parse_mcp_servers_result(self, result: dict) -> list[dict]:
        """Parse result for MCP servers."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = [
            {
                "mcp_name": bucket["key"],
                "total_requests": bucket.get("filtered_requests", {}).get("doc_count", 0),
            }
            for bucket in buckets
        ]
        logger.debug(f"Parsed mcp-servers result: total_mcp_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_mcp_servers_columns(self) -> list[dict]:
        """Get column definitions for MCP servers."""
        return [
            {"id": "mcp_name", "label": "MCP Server", "type": "string"},
            {"id": "total_requests", "label": "Total Requests", "type": "number"},
        ]

    async def get_mcp_servers_by_users(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get MCP servers usage by users analytics with accurate row-level pagination."""
        logger.info("Requesting mcp-servers-by-users analytics")

        # Use specialized method for nested/flattened aggregations to ensure accurate row-level pagination
        return await self._pipeline.execute_tabular_query_with_flattened_rows(
            agg_builder=lambda query, fetch_size: self._build_mcp_servers_by_users_aggregation(query, fetch_size),
            result_parser=self._parse_mcp_servers_by_users_result,
            columns=self._get_mcp_servers_by_users_columns(),
            flattening_multiplier=10,  # Assume avg 10 MCP servers per user
            sort_keys=[
                ("total_requests", True),  # Primary: Most active first (DESC)
                ("user_name", False),  # Secondary: Alphabetical (ASC)
                ("mcp_name", False),  # Tertiary: Alphabetical (ASC)
            ],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_mcp_servers_by_users_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build nested terms aggregation for users and MCP servers with fetch-and-slice."""
        # Build metric name filter clauses
        metric_should_clauses = self._build_metric_filter_clauses()

        # Enhance base query to filter for MCP metrics BEFORE aggregation
        # This ensures only users with MCP activity are counted and paginated
        enhanced_query = {
            "bool": {
                "must": [
                    query,  # Original query (time, user, project filters)
                    {
                        "bool": {
                            "should": metric_should_clauses,
                            "minimum_should_match": 1,
                        }
                    },
                ],
                "filter": [{"exists": {"field": MCP_NAME_KEYWORD_FIELD}}],
            }
        }

        # Build nested aggregation: user_name -> mcp_name
        # Fetch more buckets to ensure proper pagination after flattening
        # Multiply fetch_size to account for multiple MCP servers per user
        user_fetch_size = max(fetch_size * 5, 100)
        mcp_fetch_size = 50  # Assume max 50 different MCPs per user

        # Construct full aggregation body with nested terms
        agg_body = {
            "query": enhanced_query,
            "size": 0,
            "aggs": {
                "paginated_results": {
                    "terms": {
                        "field": USER_NAME_KEYWORD_FIELD,
                        "size": user_fetch_size,
                    },
                    "aggs": {
                        "mcp_servers": {
                            "terms": {
                                "field": MCP_NAME_KEYWORD_FIELD,
                                "size": mcp_fetch_size,
                                "order": {"_count": "desc"},
                            }
                        }
                    },
                }
            },
        }

        return agg_body

    def _parse_mcp_servers_by_users_result(self, result: dict) -> list[dict]:
        """Parse nested result and flatten to user-MCP combinations.

        Note: Sorting is now handled by query_pipeline for consistent pagination.
        """
        # Extract user buckets from paginated_results
        user_buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        rows = []
        for user_bucket in user_buckets:
            user_name = user_bucket["key"]
            # Extract nested MCP server buckets
            mcp_buckets = user_bucket.get("mcp_servers", {}).get("buckets", [])

            for mcp_bucket in mcp_buckets:
                rows.append(
                    {
                        "user_name": user_name,
                        "mcp_name": mcp_bucket["key"],
                        "total_requests": mcp_bucket["doc_count"],
                    }
                )

        # Sorting removed - now handled by pipeline for consistent pagination across pages

        logger.debug(
            f"Parsed mcp-servers-by-users result: "
            f"total_user_buckets={len(user_buckets)}, "
            f"total_combinations={len(rows)}"
        )
        return rows

    def _get_mcp_servers_by_users_columns(self) -> list[dict]:
        """Get column definitions for MCP servers by users."""
        return [
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "mcp_name", "label": "MCP Server", "type": "string"},
            {"id": "total_requests", "label": "Total Requests", "type": "number"},
        ]
