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

"""Handler for project analytics."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import METRIC_NAME_KEYWORD_FIELD, PROJECT_KEYWORD_FIELD
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline
from codemie.service.analytics.handlers.cli_cost_processor import CLICostAdjustmentMixin
from codemie.service.analytics.time_parser import TimeParser

logger = logging.getLogger(__name__)

# Elasticsearch field constants
MONEY_SPENT_FIELD = "attributes.money_spent"
INPUT_TOKENS_FIELD = "attributes.input_tokens"
OUTPUT_TOKENS_FIELD = "attributes.output_tokens"
USER_EMAIL_KEYWORD_FIELD = "attributes.user_email.keyword"
CLI_REQUEST_FIELD = "attributes.cli_request"


class ProjectHandler(CLICostAdjustmentMixin):
    """Handler for project analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize project handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)
        self.repository = repository

    async def get_projects_spending(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get projects spending analytics.

        Returns project spending data grouped by project with total cost across all resource types
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
            Tabular response with columns: project_name, total_cost_usd
        """
        logger.info("Requesting projects-spending analytics")

        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)

        # Query 1: Get CLI costs per project with ADJUSTED dates (before main query)
        cli_costs_by_project = await self.get_cli_costs_grouped_by(
            start_dt, end_dt, PROJECT_KEYWORD_FIELD, "project", users, projects
        )

        # Query 2: Get all costs (including CLI) with ORIGINAL dates
        # Use closure to capture cli_costs_by_project for result parsing
        def parse_with_cli_adjustment(result: dict) -> list[dict]:
            return self._parse_projects_spending_result(result, cli_costs_by_project)

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_projects_spending_aggregation(query, fetch_size),
            result_parser=parse_with_cli_adjustment,
            columns=self._get_projects_spending_columns(),
            group_by_field=PROJECT_KEYWORD_FIELD,
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

    def _build_projects_spending_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for projects spending with fetch-and-slice."""
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
            group_by_field=PROJECT_KEYWORD_FIELD,
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

    def _parse_projects_spending_result(self, result: dict, cli_costs_by_project: dict[str, float]) -> list[dict]:
        """Parse result for projects spending and merge adjusted CLI costs.

        Args:
            result: Elasticsearch aggregation result
            cli_costs_by_project: Mapping of project_name -> adjusted CLI cost
        """
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = []

        for bucket in buckets:
            project_name = bucket["key"]
            total_cost_original = bucket.get("total_cost", {}).get("value", 0) or 0
            cli_cost_original = bucket.get("cli_cost", {}).get("sum", {}).get("value", 0) or 0

            # Get adjusted CLI cost for this project (default to 0 if not in map)
            cli_cost_adjusted = cli_costs_by_project.get(project_name, 0.0)

            # Calculate adjustment (can be negative if original costs were inflated)
            # Example: cli_cost_adjusted=$10 (Feb 2-3 only) - cli_cost_original=$100 (Jan 1 - Feb 3) = -$90
            cli_cost_adjustment = cli_cost_adjusted - cli_cost_original

            # Update total cost
            # Example: total_cost_original=$500 (web=$400 + CLI=$100) + adjustment=(-$90)
            #          = $410 (web=$400 + CLI_adjusted=$10) ✓
            total_cost = total_cost_original + cli_cost_adjustment

            rows.append(
                {
                    "project_name": project_name,
                    "total_cost_usd": round(total_cost, 2),
                }
            )

        logger.debug(f"Parsed projects-spending result: total_project_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_projects_spending_columns(self) -> list[dict]:
        """Get column definitions for projects spending."""
        return [
            {"id": "project_name", "label": "Project", "type": "string"},
            {"id": "total_cost_usd", "label": "Total Cost ($)", "type": "number", "format": "currency"},
        ]

    async def get_projects_activity(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get projects activity analytics with comprehensive metrics and cost/token breakdown.

        Returns project activity including:
        - Assistant cost (money spent on assistant usage)
        - Workflow cost (money spent on workflow execution)
        - Datasource cost (money spent on datasource processing)
        - CLI cost (money spent on CLI usage)
        - Total cost (sum of all costs)
        - Assistant tokens (input + output tokens from assistant usage)
        - Workflow tokens (input + output tokens from workflow execution)
        - Datasource tokens (input + output tokens from datasource processing)
        - Unique users count
        - Total tokens (sum of all tokens from web + CLI)

        Groups by project.keyword and orders by total cost descending.

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            Tabular response with columns: project_name, assistant_cost, workflow_cost,
            datasource_cost, cli_cost, total_cost, assistant_tokens, workflow_tokens, datasource_tokens,
            unique_users, total_tokens
        """
        logger.info("Requesting projects-activity analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_projects_activity_aggregation(query, fetch_size),
            result_parser=self._parse_projects_activity_result,
            columns=self._get_projects_activity_columns(),
            group_by_field=PROJECT_KEYWORD_FIELD,
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

    def _build_projects_activity_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for projects activity with fetch-and-slice.

        Includes sub-aggregations for:
        - Unique users (all metrics)
        - Total money spent (all metrics)
        - Separate costs by metric type (assistant, workflow, datasource)
        - Separate tokens by metric type (assistant, workflow, datasource)
        - Total input tokens (web + CLI fields)
        - Total output tokens (web + CLI fields)
        """
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (metrics)
        sub_aggs = {
            # Unique users - filtered to exclude empty/missing user_email values
            # (cardinality alone counts empty strings, but terms aggregation in User Activity skips them)
            "2": {
                "filter": {
                    "bool": {
                        "must": [{"exists": {"field": USER_EMAIL_KEYWORD_FIELD}}],
                        "must_not": [{"term": {USER_EMAIL_KEYWORD_FIELD: ""}}],
                    }
                },
                "aggs": {
                    "unique_users": {
                        "cardinality": {
                            "field": USER_EMAIL_KEYWORD_FIELD,
                        }
                    }
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
            group_by_field=PROJECT_KEYWORD_FIELD,
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

    def _parse_projects_activity_result(self, result: dict) -> list[dict]:
        """Parse result for projects activity with metric breakdown (combines web + CLI tokens)."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        rows = []
        for bucket in buckets:
            project_name = bucket["key"]

            unique_users = bucket.get("2", {}).get("unique_users", {}).get("value", 0)
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
                    "project_name": project_name,
                    "unique_users": int(unique_users),
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

        logger.debug(f"Parsed projects-activity result: total_project_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_projects_activity_columns(self) -> list[dict]:
        """Get column definitions for projects activity with metric breakdown."""
        return [
            {"id": "project_name", "label": "Project", "type": "string"},
            {"id": "unique_users", "label": "Unique Users", "type": "number"},
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

    async def get_projects_unique_daily(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get unique projects per day analytics.

        Returns time-series data showing the number of unique active projects for each day,
        based on conversation_assistant_usage metric. Returns all date records without pagination.

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)

        Returns:
            Tabular response with columns: date, unique_projects (all records)
        """
        logger.info("Requesting projects-unique-daily analytics")

        # Type-annotated aggregation builder
        def build_agg(query: dict, fetch_size: int) -> dict:
            return self._build_projects_unique_daily_aggregation(query)

        return await self._pipeline.execute_tabular_query(
            agg_builder=build_agg,
            result_parser=self._parse_projects_unique_daily_result,
            columns=self._get_projects_unique_daily_columns(),
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

    def _build_projects_unique_daily_aggregation(self, query: dict) -> dict:
        """Build date histogram aggregation for unique daily projects with fetch-and-slice.

        Uses date_histogram on time field (daily intervals) with nested cardinality
        aggregation on project. Includes extended_bounds for complete date range coverage.
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
                "unique_projects": {
                    "cardinality": {
                        "field": PROJECT_KEYWORD_FIELD,
                    }
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

    def _parse_projects_unique_daily_result(self, result: dict) -> list[dict]:
        """Parse result for projects unique daily.

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
                    if key_millis is None or key_millis < 0:
                        logger.warning(f"Bucket has invalid key value: {bucket}")
                        continue
                    date_obj = datetime.fromtimestamp(key_millis / 1000, tz=timezone.utc)
                    date_formatted = date_obj.strftime("%Y-%m-%d")

                # Extract unique projects count from cardinality aggregation
                unique_projects = bucket.get("unique_projects", {}).get("value", 0)

                rows.append({"date": date_formatted, "unique_projects": int(unique_projects)})
            except (ValueError, TypeError, OSError) as e:
                logger.warning(f"Failed to parse bucket {bucket}: {e}. Skipping bucket.")
                continue

        logger.debug(f"Parsed projects-unique-daily result: total_date_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_projects_unique_daily_columns(self) -> list[dict]:
        """Get column definitions for projects unique daily."""
        return [
            {"id": "date", "label": "Date", "type": "date"},
            {"id": "unique_projects", "label": "Unique Projects", "type": "number"},
        ]
