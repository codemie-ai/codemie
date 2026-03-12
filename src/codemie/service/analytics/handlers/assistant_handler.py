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

"""Assistant and agent analytics handler."""

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
ASSISTANT_NAME_KEYWORD_FIELD = "attributes.assistant_name.keyword"


class AssistantHandler:
    """Handler for assistant and agent analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize assistant handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_assistants_chats(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get assistants/chats analytics with performance metrics.

        Uses ES|QL for accurate 2-stage aggregation: first by conversation, then by assistant.
        This ensures correct error rate calculation and message counting.

        Args:
            time_period: Predefined time range
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users
            projects: Filter by specific projects
            page: Page number (zero-indexed)
            per_page: Items per page

        Returns:
            Tabular response with assistants metrics including min/median/max statistics
        """
        logger.info("Requesting assistants-chats analytics")

        # Build ES|QL query with 2-stage aggregation for accurate metrics
        esql_query = """
FROM codemie_metrics_logs
| WHERE metric_name.keyword == \"conversation_assistant_usage\"
| STATS
    messages_per_conv = COUNT(*),
    total_cost_per_conv = SUM(attributes.money_spent),
    total_input_tokens_per_conv = SUM(attributes.input_tokens),
    total_output_tokens_per_conv = SUM(attributes.output_tokens),
    errors_per_conv = SUM(CASE(attributes.status != \"success\", 1, 0)),
    success_per_conv = SUM(CASE(attributes.status == \"success\", 1, 0)),
    avg_exec_time_per_conv = AVG(attributes.execution_time),
    user_name = MAX(attributes.user_name.keyword),
    last_error_timestamp = MAX(CASE(attributes.status != \"success\", @timestamp, null))
  BY attributes.assistant_name.keyword, attributes.conversation_id.keyword
| STATS
    total_conversations = COUNT(*),
    total_messages = SUM(messages_per_conv),
    avg_messages_per_chat = AVG(messages_per_conv),
    median_messages_per_chat = PERCENTILE(messages_per_conv, 50),
    min_messages_per_chat = MIN(messages_per_conv),
    max_messages_per_chat = MAX(messages_per_conv),
    failed_conversations = SUM(CASE(errors_per_conv > 0, 1, 0)),
    total_errors = SUM(errors_per_conv),
    successful_operations = SUM(success_per_conv),
    total_cost = SUM(total_cost_per_conv),
    total_input_tokens = SUM(total_input_tokens_per_conv),
    total_output_tokens = SUM(total_output_tokens_per_conv),
    avg_execution_time = AVG(avg_exec_time_per_conv),
    unique_users = COUNT_DISTINCT(user_name),
    last_error_time = MAX(last_error_timestamp)
  BY attributes.assistant_name.keyword
| EVAL
    total_tokens = total_input_tokens + total_output_tokens,
    error_rate_percent = ROUND((TO_DOUBLE(failed_conversations) / TO_DOUBLE(total_conversations)) * 100, 2),
    cost_per_operation = CASE(successful_operations > 0, ROUND(total_cost / successful_operations, 5), 0),
    avg_messages_rounded = ROUND(avg_messages_per_chat, 2),
    median_messages_rounded = ROUND(median_messages_per_chat, 2),
    cost_rounded = ROUND(total_cost, 2),
    avg_execution_rounded = ROUND(avg_execution_time, 2)
| SORT total_messages DESC, attributes.assistant_name.keyword ASC
| LIMIT 500
"""

        return await self._pipeline.execute_esql_query(
            esql_query=esql_query,
            result_parser=self._parse_assistants_chats_result,
            columns=self._get_assistants_chats_columns(),
            metric_filters=[MetricName.CONVERSATION_ASSISTANT_USAGE.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _parse_assistants_chats_result(self, result: dict) -> list[dict]:
        """Parse ES|QL result for assistants/chats analytics."""
        columns_metadata = result.get("columns", [])
        values = result.get("values", [])

        # Create column name to index mapping
        col_map = {col["name"]: idx for idx, col in enumerate(columns_metadata)}

        rows = []
        for row_values in values:
            assistant_name = row_values[col_map["attributes.assistant_name.keyword"]]
            total_chats = int(row_values[col_map["total_conversations"]])
            total_messages = int(row_values[col_map["total_messages"]])
            unique_users = int(row_values[col_map["unique_users"]])
            min_msg_per_chat = int(row_values[col_map["min_messages_per_chat"]])
            median_msg_per_chat = float(row_values[col_map["median_messages_rounded"]])
            avg_msg_per_chat = float(row_values[col_map["avg_messages_rounded"]])
            max_msg_per_chat = int(row_values[col_map["max_messages_per_chat"]])
            success_ops = int(row_values[col_map["successful_operations"]])
            total_errors = int(row_values[col_map["total_errors"]])
            error_rate = float(row_values[col_map["error_rate_percent"]] or 0)
            total_tokens = int(row_values[col_map["total_tokens"]])
            total_cost = float(row_values[col_map["cost_rounded"]])
            cost_per_op = float(row_values[col_map["cost_per_operation"]] or 0)
            avg_time = float(row_values[col_map["avg_execution_rounded"]] or 0)
            last_error_time = row_values[col_map["last_error_time"]] if "last_error_time" in col_map else None

            rows.append(
                {
                    "assistant": assistant_name,
                    "total_chats": total_chats,
                    "total_messages": total_messages,
                    "unique_users": unique_users,
                    "min_msg_per_chat": min_msg_per_chat,
                    "median_msg_per_chat": median_msg_per_chat,
                    "avg_msg_per_chat": avg_msg_per_chat,
                    "max_msg_per_chat": max_msg_per_chat,
                    "success_ops": success_ops,
                    "total_errors": total_errors,
                    "error_rate_percent": error_rate,
                    "total_tokens": total_tokens,
                    "total_cost_usd": total_cost,
                    "cost_per_op_usd": cost_per_op,
                    "avg_time_seconds": avg_time,
                    "last_error_timestamp": last_error_time,
                }
            )

        logger.debug(
            f"Parsed assistants-chats result: total_columns={len(columns_metadata)}, "
            f"total_rows={len(values)}, rows_parsed={len(rows)}, "
            f"column_names={[col['name'] for col in columns_metadata]}"
        )
        return rows

    def _get_assistants_chats_columns(self) -> list[dict]:
        """Get column definitions for assistants/chats response."""
        return [
            {"id": "assistant", "label": "Assistant", "type": "string"},
            {"id": "total_chats", "label": "Total Chats", "type": "number"},
            {"id": "total_messages", "label": "Total Messages", "type": "number"},
            {"id": "unique_users", "label": "Unique Users", "type": "number"},
            {"id": "min_msg_per_chat", "label": "Min Msg/Chat", "type": "number"},
            {"id": "median_msg_per_chat", "label": "Median Msg/Chat", "type": "number"},
            {"id": "avg_msg_per_chat", "label": "Avg Msg/Chat", "type": "number"},
            {"id": "max_msg_per_chat", "label": "Max Msg/Chat", "type": "number"},
            {"id": "success_ops", "label": "Success Ops", "type": "number"},
            {"id": "total_errors", "label": "Total Errors", "type": "number"},
            {"id": "error_rate_percent", "label": "Error Rate (%)", "type": "number", "format": "percentage"},
            {"id": "total_tokens", "label": "Total Tokens", "type": "number"},
            {"id": "total_cost_usd", "label": "Total Cost ($)", "type": "number", "format": "currency"},
            {"id": "cost_per_op_usd", "label": "Cost/Op ($)", "type": "number", "format": "currency"},
            {"id": "avg_time_seconds", "label": "Avg Time (s)", "type": "number"},
            {"id": "last_error_timestamp", "label": "Last Error Time", "type": "string", "format": "timestamp"},
        ]

    async def get_agents_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 30,
    ) -> dict:
        """Get assistant and tool usage analytics with cost, users, and error tracking.

        This endpoint provides comprehensive analytics for assistant usage including:
        - Conversation counts per assistant
        - Total cost (money spent)
        - Unique users per assistant
        - Unique tools used
        - Tool error counts and last error details

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)
            page: Page number for pagination (default: 0)
            per_page: Results per page (default: 30)

        Returns:
            Tabular response with assistant usage metrics
        """
        logger.info("Requesting agents-usage analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=self._build_agents_usage_aggregation,
            result_parser=self._parse_agents_usage_result,
            columns=self._get_agents_usage_columns(),
            group_by_field=ASSISTANT_NAME_KEYWORD_FIELD,
            metric_filters=None,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_agents_usage_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for agents usage with fetch-and-slice."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (nested metrics)
        sub_aggs = {
            "1-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CONVERSATION_ASSISTANT_USAGE.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                }
            },
            "2-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CONVERSATION_ASSISTANT_USAGE.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                },
                "aggs": {"2-metric": {"sum": {"field": "attributes.money_spent"}}},
            },
            "3-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CONVERSATION_ASSISTANT_USAGE.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                },
                "aggs": {"3-metric": {"cardinality": {"field": "user_id.keyword"}}},
            },
            "4-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CODEMIE_TOOLS_USAGE_TOTAL.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                },
                "aggs": {"4-metric": {"cardinality": {"field": "attributes.base_tool_name.keyword"}}},
            },
            "5-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CODEMIE_TOOLS_USAGE_ERRORS.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                }
            },
            "6-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CODEMIE_TOOLS_USAGE_ERRORS.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                },
                "aggs": {
                    "6-metric": {
                        "top_metrics": {
                            "metrics": {"field": "attributes.error_class.keyword"},
                            "size": 1,
                            "sort": {"@timestamp": "desc"},
                        }
                    }
                },
            },
        }

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=ASSISTANT_NAME_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"1-bucket": "desc"},
            sub_aggs=sub_aggs,
        )

        # Construct full aggregation body
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": terms_agg,
            },
        }

    def _parse_agents_usage_result(self, result: dict) -> list[dict]:
        """Parse result for agents usage."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        rows = []
        for bucket in buckets:
            assistant_name = bucket["key"]

            total_conversations = bucket.get("1-bucket", {}).get("doc_count", 0)
            total_cost = bucket.get("2-bucket", {}).get("2-metric", {}).get("value", 0.0)
            unique_users = bucket.get("3-bucket", {}).get("3-metric", {}).get("value", 0)
            unique_tools = bucket.get("4-bucket", {}).get("4-metric", {}).get("value", 0)
            tool_errors = bucket.get("5-bucket", {}).get("doc_count", 0)

            # Extract last error if available
            last_error = None
            top_metrics = bucket.get("6-bucket", {}).get("6-metric", {}).get("top", [])
            if top_metrics and len(top_metrics) > 0:
                metrics_data = top_metrics[0].get("metrics", {})
                last_error = metrics_data.get("attributes.error_class.keyword")

            rows.append(
                {
                    "assistant_name": assistant_name,
                    "total_conversations": total_conversations,
                    "total_cost": round(total_cost, 4),
                    "unique_users": unique_users,
                    "unique_tools_used": unique_tools,
                    "tool_errors": tool_errors,
                    "last_error": last_error or "N/A",
                }
            )

        logger.debug(
            f"Parsed agents-usage result: total_buckets={len(buckets)}, "
            f"rows_parsed={len(rows)}, "
            f"has_aggregations={bool(result.get('aggregations'))}"
        )
        return rows

    def _get_agents_usage_columns(self) -> list[dict]:
        """Get column definitions for agents usage."""
        return [
            {"id": "assistant_name", "label": "Assistant Name", "type": "string"},
            {"id": "total_conversations", "label": "Total Conversations", "type": "number"},
            {"id": "total_cost", "label": "Total Cost ($)", "type": "number", "format": "currency"},
            {"id": "unique_users", "label": "Unique Users", "type": "number"},
            {"id": "unique_tools_used", "label": "Unique Tools Used", "type": "number"},
            {"id": "tool_errors", "label": "Tool Errors", "type": "number"},
            {"id": "last_error", "label": "Last Error", "type": "string"},
        ]
