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

"""Tools usage analytics handler."""

from __future__ import annotations

import logging
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import METRIC_NAME_KEYWORD_FIELD
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline

logger = logging.getLogger(__name__)

# Elasticsearch field constants
TOOL_TYPE_KEYWORD_FIELD = "attributes.tool_type.keyword"


class ToolsHandler:
    """Handler for tools usage analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize tools handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_tools_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get tools usage analytics with comprehensive metrics.

        Returns tool usage data including:
        - Tool metadata (type, category)
        - Usage statistics (invocations, unique users/assistants)
        - Token consumption metrics
        - Error tracking (total errors, last error class)
        """
        logger.info("Requesting tools-usage analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_tools_usage_aggregation(query, fetch_size),
            result_parser=self._parse_tools_usage_result,
            columns=self._get_tools_usage_columns(),
            group_by_field="attributes.base_tool_name.keyword",
            metric_filters=MetricName.to_list_from_group(MetricName.TOOLS_METRICS),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_tools_usage_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for tools usage with fetch-and-slice."""

        # Helper to create verbose bool filter structure matching Kibana/Elasticsearch format
        def create_filter_bool(filters: list) -> dict:
            """Create verbose bool structure with all clauses."""
            return {
                "bool": {
                    "must": [],
                    "filter": filters,
                    "should": [],
                    "must_not": [],
                }
            }

        def create_term_filter(field: str, value: str) -> dict:
            """Create term filter in verbose format with nested bool."""
            return {
                "bool": {
                    "should": [
                        {
                            "term": {
                                field: {
                                    "value": value,
                                }
                            }
                        }
                    ],
                    "minimum_should_match": 1,
                }
            }

        def create_exists_filter(field: str) -> dict:
            """Create exists filter in verbose format with nested bool."""
            return {
                "bool": {
                    "should": [
                        {
                            "exists": {
                                "field": field,
                            }
                        }
                    ],
                    "minimum_should_match": 1,
                }
            }

        # Define sub-aggregations (metrics) matching the correct query structure
        sub_aggs = {
            "1-bucket": {
                "filter": create_filter_bool(
                    [create_term_filter(METRIC_NAME_KEYWORD_FIELD, MetricName.CODEMIE_TOOLS_USAGE_TOTAL.value)]
                ),
            },
            "2-bucket": {
                "filter": create_filter_bool([create_exists_filter(TOOL_TYPE_KEYWORD_FIELD)]),
                "aggs": {
                    "2-metric": {
                        "top_metrics": {
                            "metrics": {"field": TOOL_TYPE_KEYWORD_FIELD},
                            "size": 1,
                            "sort": {"time": "desc"},
                        },
                    },
                },
            },
            "3-bucket": {
                "filter": create_filter_bool(
                    [create_term_filter(METRIC_NAME_KEYWORD_FIELD, MetricName.CODEMIE_TOOLS_USAGE_TOTAL.value)]
                ),
                "aggs": {
                    "3-metric": {
                        "cardinality": {"field": "attributes.user_id.keyword"},
                    },
                },
            },
            "4-bucket": {
                "filter": create_filter_bool(
                    [create_term_filter(METRIC_NAME_KEYWORD_FIELD, MetricName.CODEMIE_TOOLS_USAGE_TOTAL.value)]
                ),
                "aggs": {
                    "4-metric": {
                        "cardinality": {"field": "attributes.assistant_name.keyword"},
                    },
                },
            },
            "5-bucket": {
                "filter": create_filter_bool(
                    [create_term_filter(METRIC_NAME_KEYWORD_FIELD, MetricName.CODEMIE_TOOLS_USAGE_TOKENS.value)]
                ),
                "aggs": {
                    "5-metric": {
                        "sum": {"field": "attributes.count"},
                    },
                },
            },
            "6-bucket": {
                "filter": create_filter_bool(
                    [create_term_filter(METRIC_NAME_KEYWORD_FIELD, MetricName.CODEMIE_TOOLS_USAGE_ERRORS.value)]
                ),
            },
            "7-bucket": {
                "filter": create_filter_bool(
                    [create_term_filter(METRIC_NAME_KEYWORD_FIELD, MetricName.CODEMIE_TOOLS_USAGE_ERRORS.value)]
                ),
                "aggs": {
                    "7-metric": {
                        "top_metrics": {
                            "metrics": {"field": "attributes.error_class.keyword"},
                            "size": 1,
                            "sort": {"@timestamp": "desc"},
                        },
                    },
                },
            },
        }

        # Build terms aggregation structure matching the correct query
        terms_agg = {
            "terms": {
                "field": "attributes.base_tool_name.keyword",
                "order": {"1-bucket": "desc"},
                "size": fetch_size,
            },
            "aggs": sub_aggs,
        }

        # Construct full aggregation body
        agg_body = {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": terms_agg,
            },
        }

        return agg_body

    def _parse_tools_usage_result(self, result: dict) -> list[dict]:
        """Parse result for tools usage."""
        rows = []
        # Extract buckets from paginated_results (already sliced by pipeline)
        tools_buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        for bucket in tools_buckets:
            tool_name = bucket["key"]
            # 1-bucket: total invocations count
            total_invocations = bucket.get("1-bucket", {}).get("doc_count", 0)

            # 2-bucket: tool type from top_metrics
            tool_type = None
            tool_type_hits = bucket.get("2-bucket", {}).get("2-metric", {}).get("top", [])
            if tool_type_hits:
                tool_type = tool_type_hits[0].get("metrics", {}).get(TOOL_TYPE_KEYWORD_FIELD)

            # 3-bucket: unique users cardinality
            unique_users = bucket.get("3-bucket", {}).get("3-metric", {}).get("value", 0)

            # 4-bucket: unique assistants cardinality
            unique_assistants = bucket.get("4-bucket", {}).get("4-metric", {}).get("value", 0)

            # 5-bucket: total tokens sum
            total_tokens = bucket.get("5-bucket", {}).get("5-metric", {}).get("value", 0)

            # 6-bucket: total errors count
            total_errors = bucket.get("6-bucket", {}).get("doc_count", 0)

            # 7-bucket: last error class from top_metrics
            last_error_class = None
            last_error_hits = bucket.get("7-bucket", {}).get("7-metric", {}).get("top", [])
            if last_error_hits:
                last_error_class = last_error_hits[0].get("metrics", {}).get("attributes.error_class.keyword")

            rows.append(
                {
                    "tool_name": tool_name,
                    "tool_type": tool_type,
                    "total_invocations": int(total_invocations),
                    "unique_users": int(unique_users),
                    "unique_assistants": int(unique_assistants),
                    "total_tokens": int(total_tokens),
                    "total_errors": int(total_errors),
                    "last_error_class": last_error_class,
                }
            )

        logger.debug(
            f"Parsed tools-usage result: total_tool_buckets={len(tools_buckets)}, "
            f"rows_parsed={len(rows)}, "
            f"has_aggregations={bool(result.get('aggregations'))}"
        )
        return rows

    def _get_tools_usage_columns(self) -> list[dict]:
        """Get column definitions for tools usage."""
        return [
            {"id": "tool_name", "label": "Tool Name", "type": "string"},
            {"id": "tool_type", "label": "Tool Type", "type": "string"},
            {"id": "total_invocations", "label": "Total Invocations", "type": "number"},
            {"id": "unique_users", "label": "Unique Users", "type": "number"},
            {"id": "unique_assistants", "label": "Unique Assistants", "type": "number"},
            {"id": "total_tokens", "label": "Total Tokens", "type": "number"},
            {"id": "total_errors", "label": "Total Errors", "type": "number"},
            {"id": "last_error_class", "label": "Last Error", "type": "string"},
        ]
