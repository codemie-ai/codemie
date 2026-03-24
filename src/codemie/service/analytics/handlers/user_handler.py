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

"""Handler for user analytics."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import (
    METRIC_NAME_KEYWORD_FIELD,
    PROJECT_KEYWORD_FIELD,
    USER_NAME_KEYWORD_FIELD,
)
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline
from codemie.service.analytics.time_parser import TimeParser
from codemie.service.analytics.handlers.cli_cost_processor import CLICostAdjustmentMixin

logger = logging.getLogger(__name__)

# Elasticsearch field constants
USER_EMAIL_KEYWORD_FIELD = "attributes.user_email.keyword"
USER_ID_KEYWORD_FIELD = "attributes.user_id.keyword"
MONEY_SPENT_FIELD = "attributes.money_spent"
INPUT_TOKENS_FIELD = "attributes.input_tokens"
OUTPUT_TOKENS_FIELD = "attributes.output_tokens"
CLI_REQUEST_FIELD = "attributes.cli_request"


class UserHandler(CLICostAdjustmentMixin):
    """Handler for user analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize user handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)
        self.repository = repository

    async def get_users_list(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get list of unique users from metrics logs.

        Returns unique users with activity in the specified time range,
        respecting access control (admin sees all users from admin projects,
        plain user sees only themselves).

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)

        Returns:
            Response with users list, total count, and metadata
        """
        logger.info("Requesting users-list analytics")

        return await self._pipeline.execute_composite_query(
            agg_builder=self._build_users_list_aggregation,
            result_parser=self._parse_users_list_result,
            metric_filters=None,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

    def _build_users_list_aggregation(self, query: dict) -> dict:
        """Build composite aggregation for unique (user_id, user_name) pairs."""
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "unique_users": {
                    "composite": {
                        "size": 10000,
                        "sources": [
                            {"user_id": {"terms": {"field": USER_ID_KEYWORD_FIELD}}},
                            {"user_name": {"terms": {"field": USER_NAME_KEYWORD_FIELD}}},
                        ],
                    }
                }
            },
        }

    def _parse_users_list_result(self, result: dict, metadata: dict) -> dict:
        """Parse ES result into users list response."""
        # Extract buckets from composite aggregation
        buckets = result.get("aggregations", {}).get("unique_users", {}).get("buckets", [])
        users_list = [{"id": bucket["key"]["user_id"], "name": bucket["key"]["user_name"]} for bucket in buckets]
        total_count = len(users_list)

        logger.debug(f"Parsed users-list result: total_users={total_count}")

        return {"data": {"users": users_list, "total_count": total_count}, "metadata": metadata}

    async def get_users_spending(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get users spending analytics.

        Returns user spending data grouped by user email with total cost across all resource types
        (assistants, workflows, datasources, CLI). CLI costs are adjusted for cutoff date.

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            Tabular response with columns: user_email, total_cost_usd
        """
        logger.info("Requesting users-spending analytics")

        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)

        # Query 1: Get CLI costs per user with ADJUSTED dates (before main query)
        cli_costs_by_user = await self.get_cli_costs_grouped_by(
            start_dt, end_dt, USER_EMAIL_KEYWORD_FIELD, "user", users, projects
        )

        # Query 2: Get all costs (including CLI) with ORIGINAL dates
        # Use closure to capture cli_costs_by_user for result parsing
        def parse_with_cli_adjustment(result: dict) -> list[dict]:
            return self._parse_users_spending_result(result, cli_costs_by_user)

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_users_spending_aggregation(query, fetch_size),
            result_parser=parse_with_cli_adjustment,
            columns=self._get_users_spending_columns(),
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            metric_filters=MetricName.to_list_from_group(MetricName.SPENDING_METRICS),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
            use_bucket_selector=True,
        )

    def _build_users_spending_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for users spending with fetch-and-slice."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (metrics)
        sub_aggs = {
            "total_cost": {"sum": {"field": MONEY_SPENT_FIELD}},
            # Add CLI-specific cost aggregation for cutoff handling (use LiteLLM proxy metric)
            "cli_cost": {
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
            **AggregationBuilder.build_zero_token_filter_aggs(),
        }

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"total_cost": "desc"},
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

    def _parse_users_spending_result(self, result: dict, cli_costs_by_user: dict[str, float]) -> list[dict]:
        """Parse result for users spending, filtering out empty user emails, and merge adjusted CLI costs.

        Args:
            result: Elasticsearch aggregation result
            cli_costs_by_user: Mapping of user_email -> adjusted CLI cost
        """
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = []

        for bucket in buckets:
            if not bucket["key"]:  # Filter out empty or null user emails
                continue

            user_email = bucket["key"]
            total_cost_original = bucket.get("total_cost", {}).get("value", 0) or 0
            cli_cost_original = bucket.get("cli_cost", {}).get("sum", {}).get("value", 0) or 0

            # Get adjusted CLI cost for this user (default to 0 if not in map)
            cli_cost_adjusted = cli_costs_by_user.get(user_email, 0.0)

            # Calculate adjustment (can be negative if original costs were inflated)
            # Example: cli_cost_adjusted=$10 (Feb 2-3 only) - cli_cost_original=$100 (Jan 1 - Feb 3) = -$90
            cli_cost_adjustment = cli_cost_adjusted - cli_cost_original

            # Update total cost
            # Example: total_cost_original=$500 (web=$400 + CLI=$100) + adjustment=(-$90)
            #          = $410 (web=$400 + CLI_adjusted=$10) ✓
            total_cost = total_cost_original + cli_cost_adjustment

            rows.append(
                {
                    "user_email": user_email,
                    "total_cost_usd": round(total_cost, 2),
                }
            )

        logger.debug(
            f"Parsed users-spending result: total_user_buckets={len(buckets)}, "
            f"rows_parsed={len(rows)}, filtered_out={len(buckets) - len(rows)}"
        )
        return rows

    def _get_users_spending_columns(self) -> list[dict]:
        """Get column definitions for users spending."""
        return [
            {"id": "user_email", "label": "User Email", "type": "string"},
            {"id": "total_cost_usd", "label": "Total Cost ($)", "type": "number", "format": "currency"},
        ]

    async def get_users_platform_spending(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get platform spending per user (Assistants + Workflows + Datasources, no CLI).

        Groups by user_email and sums money_spent for platform metrics only.

        Args:
            time_period: Predefined time range
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            Tabular response with columns: user_email, total_cost_usd
        """
        logger.info("Requesting users-platform-spending analytics")

        platform_metrics = MetricName.to_list(
            MetricName.CONVERSATION_ASSISTANT_USAGE,
            MetricName.WORKFLOW_EXECUTION_TOTAL,
            MetricName.DATASOURCE_TOKENS_USAGE,
        )

        def parse_platform(result: dict) -> list[dict]:
            return self._parse_simple_spending_result(result, "user_email")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_simple_spending_aggregation(
                query, fetch_size, USER_EMAIL_KEYWORD_FIELD
            ),
            result_parser=parse_platform,
            columns=self._get_users_spending_columns(),
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            metric_filters=platform_metrics,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
            use_bucket_selector=False,
        )

    async def get_users_cli_spending(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI-only spending per user (grouped by user_name).

        Filters to codemie_litellm_proxy_usage with cli_request=true
        and groups by attributes.user_name.keyword.

        Args:
            time_period: Predefined time range
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            Tabular response with columns: user_name, total_cost_usd
        """
        logger.info("Requesting users-cli-spending analytics")

        from codemie.service.analytics.handlers.field_constants import USER_NAME_KEYWORD_FIELD

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_cli_spending_aggregation(query, fetch_size),
            result_parser=self._parse_cli_spending_result,
            columns=self._get_cli_spending_columns(),
            group_by_field=USER_NAME_KEYWORD_FIELD,
            metric_filters=MetricName.to_list(MetricName.CLI_LLM_USAGE_TOTAL),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
            use_bucket_selector=True,
        )

    def _build_simple_spending_aggregation(self, query: dict, fetch_size: int, group_by_field: str) -> dict:
        """Build terms aggregation for simple spending (sum money_spent, no CLI adjustment)."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        sub_aggs = {
            "total_cost": {"sum": {"field": MONEY_SPENT_FIELD}},
        }

        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=group_by_field,
            fetch_size=fetch_size,
            order={"total_cost": "desc"},
            sub_aggs=sub_aggs,
        )

        return {
            "query": query,
            "size": 0,
            "aggs": {"paginated_results": terms_agg},
        }

    def _parse_simple_spending_result(self, result: dict, field_name: str) -> list[dict]:
        """Parse simple spending result (no CLI adjustment)."""
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = []

        for bucket in buckets:
            key = bucket.get("key", "")
            if not key:
                continue
            total_cost = bucket.get("total_cost", {}).get("value", 0) or 0
            rows.append({field_name: key, "total_cost_usd": round(total_cost, 2)})

        logger.debug(f"Parsed simple-spending result: total_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _build_cli_spending_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for CLI spending, filtered to cli_request=true."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder
        from codemie.service.analytics.handlers.field_constants import USER_NAME_KEYWORD_FIELD

        sub_aggs = {
            "cli_request_filter": {
                "filter": {"term": {CLI_REQUEST_FIELD: True}},
                "aggs": {"total_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
            },
            "has_cli_request": {
                "bucket_selector": {
                    "buckets_path": {"cost": "cli_request_filter>total_cost"},
                    "script": "params.cost > 0",
                }
            },
        }

        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=USER_NAME_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"cli_request_filter>total_cost": "desc"},
            sub_aggs=sub_aggs,
        )

        return {
            "query": query,
            "size": 0,
            "aggs": {"paginated_results": terms_agg},
        }

    def _parse_cli_spending_result(self, result: dict) -> list[dict]:
        """Parse CLI spending result grouped by user_name."""
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = []

        for bucket in buckets:
            user_name = bucket.get("key", "")
            if not user_name:
                continue
            total_cost = bucket.get("cli_request_filter", {}).get("total_cost", {}).get("value", 0) or 0
            rows.append({"user_name": user_name, "total_cost_usd": round(total_cost, 2)})

        logger.debug(f"Parsed cli-spending result: total_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_cli_spending_columns(self) -> list[dict]:
        """Get column definitions for CLI spending."""
        return [
            {"id": "user_name", "label": "User Name", "type": "string"},
            {"id": "total_cost_usd", "label": "CLI Cost ($)", "type": "number", "format": "currency"},
        ]

    async def get_users_activity(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get users activity analytics with comprehensive metrics and cost/token breakdown.

        Returns user activity including:
        - Assistant cost (money spent on assistant usage)
        - Workflow cost (money spent on workflow execution)
        - Datasource cost (money spent on datasource processing)
        - CLI cost (money spent on CLI usage)
        - Total cost (sum of all costs)
        - Assistant tokens (input + output tokens from assistant usage)
        - Workflow tokens (input + output tokens from workflow execution)
        - Datasource tokens (input + output tokens from datasource processing)
        - Unique projects count
        - Total tokens (sum of all tokens from web + CLI)

        Groups by user_email.keyword and orders by total cost descending.

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            Tabular response with columns: user_email, unique_projects, assistant_cost, workflow_cost,
            datasource_cost, cli_cost, total_cost, assistant_tokens, workflow_tokens, datasource_tokens, total_tokens
        """
        logger.info("Requesting users-activity analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_users_activity_aggregation(query, fetch_size),
            result_parser=self._parse_users_activity_result,
            columns=self._get_users_activity_columns(),
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            metric_filters=MetricName.to_list_from_group(MetricName.ACTIVITY_METRICS),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
            use_bucket_selector=True,
        )

    def _build_users_activity_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for users activity with fetch-and-slice.

        Includes sub-aggregations for:
        - Unique projects (all metrics)
        - Total money spent (all metrics)
        - Separate costs by metric type (assistant, workflow, datasource)
        - Separate tokens by metric type (assistant, workflow, datasource)
        - Total input tokens (web + CLI fields)
        - Total output tokens (web + CLI fields)
        """
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (metrics)
        sub_aggs = {
            "2": {
                "cardinality": {
                    "field": PROJECT_KEYWORD_FIELD,
                },
            },
            # Assistant costs
            "assistant_cost_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CONVERSATION_ASSISTANT_USAGE.value}},
                "aggs": {"assistant_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
            },
            # Workflow costs
            "workflow_cost_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.WORKFLOW_EXECUTION_TOTAL.value}},
                "aggs": {"workflow_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
            },
            # Datasource costs
            "datasource_cost_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.DATASOURCE_TOKENS_USAGE.value}},
                "aggs": {"datasource_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
            },
            # CLI costs (use NEW LiteLLM proxy metric)
            "cli_cost_bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            {"term": {CLI_REQUEST_FIELD: True}},
                        ]
                    }
                },
                "aggs": {"cli_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
            },
            # Assistant tokens (input + output)
            "assistant_tokens_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CONVERSATION_ASSISTANT_USAGE.value}},
                "aggs": {
                    "input": {"sum": {"field": INPUT_TOKENS_FIELD}},
                    "output": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                },
            },
            # Workflow tokens (input + output)
            "workflow_tokens_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.WORKFLOW_EXECUTION_TOTAL.value}},
                "aggs": {
                    "input": {"sum": {"field": INPUT_TOKENS_FIELD}},
                    "output": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                },
            },
            # Datasource tokens (input + output)
            "datasource_tokens_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.DATASOURCE_TOKENS_USAGE.value}},
                "aggs": {
                    "input": {"sum": {"field": INPUT_TOKENS_FIELD}},
                    "output": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                },
            },
            # Total cost aggregation (sum of Assistant + Workflow + Datasource + CLI)
            # Note: CLI uses NEW LiteLLM proxy metric for accurate server-side tracking
            "1-bucket": {
                "filter": {
                    "bool": {
                        "should": [
                            # Assistant costs
                            {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CONVERSATION_ASSISTANT_USAGE.value}},
                            # Workflow costs
                            {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.WORKFLOW_EXECUTION_TOTAL.value}},
                            # Datasource costs
                            {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.DATASOURCE_TOKENS_USAGE.value}},
                            # CLI costs (NEW metric with cli_request filter)
                            {
                                "bool": {
                                    "filter": [
                                        {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                                        {"term": {CLI_REQUEST_FIELD: True}},
                                    ]
                                }
                            },
                        ],
                        "minimum_should_match": 1,
                    }
                },
                "aggs": {
                    "1-metric": {
                        "sum": {"field": MONEY_SPENT_FIELD},
                    },
                },
            },
            **AggregationBuilder.build_zero_token_filter_aggs(),
        }

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"1-bucket>1-metric": "desc"},
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

    def _parse_users_activity_result(self, result: dict) -> list[dict]:
        """Parse result for users activity with metric breakdown (combines web + CLI tokens)."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        rows = []
        for bucket in buckets:
            user_email = bucket["key"]

            # Skip empty or null user emails to match User Spending Distribution
            if not user_email:
                continue

            unique_projects = bucket.get("2", {}).get("value", 0)
            total_cost = bucket.get("1-bucket", {}).get("1-metric", {}).get("value", 0.0)

            # Extract separate costs for each metric type (calculated in ES)
            assistant_cost = bucket.get("assistant_cost_bucket", {}).get("assistant_cost", {}).get("value", 0) or 0
            workflow_cost = bucket.get("workflow_cost_bucket", {}).get("workflow_cost", {}).get("value", 0) or 0
            datasource_cost = bucket.get("datasource_cost_bucket", {}).get("datasource_cost", {}).get("value", 0) or 0
            cli_cost = bucket.get("cli_cost_bucket", {}).get("cli_cost", {}).get("value", 0) or 0

            # Calculate separate tokens for each metric type (sum input + output)
            assistant_input = bucket.get("assistant_tokens_bucket", {}).get("input", {}).get("value", 0) or 0
            assistant_output = bucket.get("assistant_tokens_bucket", {}).get("output", {}).get("value", 0) or 0
            assistant_tokens = int(assistant_input + assistant_output)

            workflow_input = bucket.get("workflow_tokens_bucket", {}).get("input", {}).get("value", 0) or 0
            workflow_output = bucket.get("workflow_tokens_bucket", {}).get("output", {}).get("value", 0) or 0
            workflow_tokens = int(workflow_input + workflow_output)

            datasource_input = bucket.get("datasource_tokens_bucket", {}).get("input", {}).get("value", 0) or 0
            datasource_output = bucket.get("datasource_tokens_bucket", {}).get("output", {}).get("value", 0) or 0
            datasource_tokens = int(datasource_input + datasource_output)

            # LLM tokens (web + CLI, excludes embeddings)
            web_input = bucket.get("3-bucket", {}).get("3-metric", {}).get("value", 0) or 0
            web_output = bucket.get("4-bucket", {}).get("4-metric", {}).get("value", 0) or 0
            cli_input = bucket.get("5-bucket", {}).get("5-metric", {}).get("value", 0) or 0
            cli_output = bucket.get("6-bucket", {}).get("6-metric", {}).get("value", 0) or 0
            llm_tokens = int(web_input + web_output + cli_input + cli_output)

            rows.append(
                {
                    "user_email": user_email,
                    "unique_projects": int(unique_projects),
                    "assistant_cost": round(assistant_cost, 2),
                    "workflow_cost": round(workflow_cost, 2),
                    "datasource_cost": round(datasource_cost, 2),
                    "cli_cost": round(cli_cost, 2),
                    "total_cost": round(total_cost, 2),
                    "assistant_tokens": assistant_tokens,
                    "workflow_tokens": workflow_tokens,
                    "datasource_tokens": datasource_tokens,
                    "llm_tokens": llm_tokens,
                }
            )

        logger.debug(
            f"Parsed users-activity result: total_user_buckets={len(buckets)}, "
            f"rows_parsed={len(rows)}, filtered_out={len(buckets) - len(rows)}"
        )
        return rows

    def _get_users_activity_columns(self) -> list[dict]:
        """Get column definitions for users activity with metric breakdown."""
        return [
            {"id": "user_email", "label": "User", "type": "string"},
            {"id": "unique_projects", "label": "Unique Projects", "type": "number"},
            {"id": "assistant_cost", "label": "Assistant Cost ($)", "type": "number", "format": "currency"},
            {"id": "workflow_cost", "label": "Workflow Cost ($)", "type": "number", "format": "currency"},
            {"id": "datasource_cost", "label": "Datasource Cost ($)", "type": "number", "format": "currency"},
            {"id": "cli_cost", "label": "CLI Cost ($)", "type": "number", "format": "currency"},
            {"id": "total_cost", "label": "Total Cost ($)", "type": "number", "format": "currency"},
            {"id": "assistant_tokens", "label": "Assistant Tokens", "type": "number"},
            {"id": "workflow_tokens", "label": "Workflow Tokens", "type": "number"},
            {"id": "datasource_tokens", "label": "Datasource Tokens", "type": "number"},
            {"id": "llm_tokens", "label": "LLM Tokens", "type": "number"},
        ]

    async def get_users_unique_daily(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get unique users per day analytics.

        Returns time-series data showing the number of unique active users for each day,
        based on conversation_assistant_usage metric. Returns all date records without pagination.

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)

        Returns:
            Tabular response with columns: date, unique_users (all records)
        """
        logger.info("Requesting users-unique-daily analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_users_unique_daily_aggregation(query),
            result_parser=self._parse_users_unique_daily_result,
            columns=self._get_users_unique_daily_columns(),
            group_by_field="time",
            metric_filters=MetricName.to_list_from_group(MetricName.ACTIVITY_METRICS),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=0,
            per_page=10000,
        )

    def _build_users_unique_daily_aggregation(self, query: dict) -> dict:
        """Build date histogram aggregation for unique daily users with fetch-and-slice.

        Uses date_histogram on time field (daily intervals) with nested cardinality
        aggregation on user_id. Includes extended_bounds for complete date range coverage.
        """
        # Extract time range from query for extended_bounds
        time_range = query.get("bool", {}).get("filter", [])
        min_time = None
        max_time = None
        for filter_clause in time_range:
            if "range" in filter_clause and "@timestamp" in filter_clause["range"]:
                time_filter = filter_clause["range"]["@timestamp"]
                min_time = time_filter.get("gte")
                max_time = time_filter.get("lte")
                break

        # Build date_histogram aggregation
        date_histogram_base = {
            "date_histogram": {
                "field": "time",
                "calendar_interval": "1d",
                "time_zone": "UTC",
                "order": {"_key": "asc"},
            },
            "aggs": {
                "filtered_users": {
                    "filter": {
                        "bool": {
                            "must": [{"exists": {"field": USER_ID_KEYWORD_FIELD}}],
                            "must_not": [{"term": {USER_ID_KEYWORD_FIELD: ""}}],
                        }
                    },
                    "aggs": {
                        "unique_users": {
                            "cardinality": {
                                "field": USER_ID_KEYWORD_FIELD,
                            }
                        }
                    },
                }
            },
        }

        # Add extended_bounds if time range is available
        if min_time and max_time:
            try:
                # Convert ISO strings to epoch millis if needed
                if isinstance(min_time, str):
                    min_dt = datetime.fromisoformat(min_time.replace("Z", "+00:00"))
                    min_time = int(min_dt.timestamp() * 1000)
                if isinstance(max_time, str):
                    max_dt = datetime.fromisoformat(max_time.replace("Z", "+00:00"))
                    max_time = int(max_dt.timestamp() * 1000)

                date_histogram_base["date_histogram"]["extended_bounds"] = {"min": min_time, "max": max_time}
            except (ValueError, AttributeError) as e:
                logger.warning(f"Failed to parse time bounds for extended_bounds: {e}. Proceeding without.")
        else:
            logger.warning("Time range not found in query filter. Extended bounds will not be set.")

        # Construct full aggregation body
        agg_body = {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": date_histogram_base,
            },
        }

        return agg_body

    def _parse_users_unique_daily_result(self, result: dict) -> list[dict]:
        """Parse result for users unique daily.

        Extracts date buckets and formats dates as ISO date strings (YYYY-MM-DD).
        """
        # Extract buckets from paginated_results (date_histogram buckets)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        rows = []
        for bucket in buckets:
            try:
                # Parse date from bucket key
                # ES returns key_as_string (ISO format) or key (epoch millis)
                date_str = bucket.get("key_as_string")
                if date_str:
                    # Validate ISO format and extract date portion
                    if "T" not in date_str:
                        logger.warning(f"Unexpected date format in key_as_string: {date_str}. Expected ISO format.")
                        continue
                    # Format as YYYY-MM-DD (remove time portion)
                    date_formatted = date_str.split("T")[0]
                else:
                    # Fallback: parse epoch millis
                    key_millis = bucket.get("key")
                    if key_millis is None:
                        logger.warning(f"Bucket missing both key_as_string and key: {bucket}")
                        continue
                    date_obj = datetime.fromtimestamp(key_millis / 1000, tz=timezone.utc)
                    date_formatted = date_obj.strftime("%Y-%m-%d")

                # Extract unique users count from nested filtered aggregation
                unique_users = bucket.get("filtered_users", {}).get("unique_users", {}).get("value", 0)

                rows.append({"date": date_formatted, "unique_users": int(unique_users)})
            except (ValueError, TypeError, OSError) as e:
                logger.warning(f"Failed to parse bucket {bucket}: {e}. Skipping bucket.")
                continue

        logger.debug(f"Parsed users-unique-daily result: total_date_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_users_unique_daily_columns(self) -> list[dict]:
        """Get column definitions for users unique daily."""
        return [
            {"id": "date", "label": "Date", "type": "date"},
            {"id": "unique_users", "label": "Unique Users", "type": "number"},
        ]
