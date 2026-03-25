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

"""Handler for CLI analytics."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.cli_cost_processor import CLICostAdjustmentMixin
from codemie.service.analytics.handlers.field_constants import (
    METRIC_NAME_KEYWORD_FIELD,
    PROJECT_KEYWORD_FIELD,
    USER_EMAIL_KEYWORD_FIELD,
)
from codemie.service.analytics.handlers.llm_handler import _combine_model_names
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline
from codemie.service.analytics.response_formatter import ResponseFormatter
from codemie.service.analytics.time_parser import TimeParser

logger = logging.getLogger(__name__)

# Elasticsearch field constants
TIMESTAMP_FIELD = "@timestamp"
REPOSITORY_KEYWORD_FIELD = "attributes.repository.keyword"
SESSION_DURATION_MS_FIELD = "attributes.session_duration_ms"
RESPONSE_STATUS_FIELD = "attributes.response_status"
TOTAL_LINES_ADDED_FIELD = "attributes.total_lines_added"
TOTAL_TOOL_CALLS_FIELD = "attributes.total_tool_calls"
TOTAL_USER_PROMPTS_FIELD = "attributes.total_user_prompts"
TOTAL_LINES_REMOVED_FIELD = "attributes.total_lines_removed"
FILES_CREATED_FIELD = "attributes.files_created"
FILES_MODIFIED_FIELD = "attributes.files_modified"
FILES_DELETED_FIELD = "attributes.files_deleted"
SESSION_ID_KEYWORD_FIELD = "attributes.session_id.keyword"
SESSION_STATUS_KEYWORD_FIELD = "attributes.status.keyword"
LLM_MODEL_KEYWORD_FIELD = "attributes.llm_model.keyword"
CLI_REQUEST_FIELD = "attributes.cli_request"
INPUT_TOKENS_FIELD = "attributes.input_tokens"
OUTPUT_TOKENS_FIELD = "attributes.output_tokens"
CACHE_READ_INPUT_TOKENS_FIELD = "attributes.cache_read_input_tokens"
CACHE_CREATION_TOKENS_FIELD = "attributes.cache_creation_tokens"
MONEY_SPENT_FIELD = "attributes.money_spent"

# Tool usage fields
TOOL_NAMES_FIELD = "attributes.tool_names"
TOOL_COUNTS_FIELD = "attributes.tool_counts"
TOOL_NAMES_ATTR_KEY = "tool_names"
TOOL_COUNTS_ATTR_KEY = "tool_counts"
USER_ID_KEYWORD_FIELD = "attributes.user_id.keyword"
USER_NAME_KEYWORD_FIELD = "attributes.user_name.keyword"
USER_EMAIL_RAW_FIELD = "attributes.user_email"
BRANCH_KEYWORD_FIELD = "attributes.branch.keyword"

# Special values
N_A_VALUE = "N/A"
PROJECT_TYPE_PERSONAL = "personal"
PROJECT_TYPE_TEAM = "team"
LEARNING_REPO_PATTERNS = [r"tutorial", r"learn", r"course", r"training", r"workshop", r"sample", r"example"]
TESTING_REPO_PATTERNS = [r"test", r"spec", r"qa", r"mock", r"fixture"]
EXPERIMENTAL_REPO_PATTERNS = [r"demo", r"poc", r"spike", r"experiment", r"playground", r"sandbox", r"scratch"]
PET_PROJECT_REPO_PATTERNS = [r"personal", r"my[-_]?project", r"side[-_]?project"]
LOCAL_PATH_PATTERNS = [
    r"^/Users/",
    r"^/home/",
    r"^[A-Z]:[/\\\\]Users[/\\\\]",
    r"Downloads",
    r"Desktop",
    r"tmp",
    r"temp",
]
PRODUCTION_BRANCH_PATTERNS = [r"[A-Z]+-\d+", r"^(feature|feat|fix|bugfix|hotfix|release)[-_/]"]
NON_PRODUCTION_BRANCH_PATTERNS = [r"^(test|tmp|temp|sandbox|playground|experiment)[-_/]"]
PERSONAL_PROJECT_DOMAINS = ("@epam.com", "@epamneoris.com", "@firstderivative.com")
TERMINAL_TOOL_MATCHERS = ("bash", "run_shell_command")
READ_SEARCH_TOOL_MATCHERS = ("read", "grep", "glob", "find", "search", "webfetch", "websearch")
CODE_CHANGE_TOOL_MATCHERS = ("edit", "write", "notebookedit", "replace")
PLANNING_TOOL_MATCHERS = ("task", "askuserquestion", "ask_user", "enterplanmode", "exitplanmode")
AGENT_TOOL_MATCHERS = ("agent", "skill")
SESSION_COMPLETED_STATUSES = ("completed", "failed", "interrupted")
TOTAL_COST_LABEL = "Total Cost"
USAGE_COUNT_LABEL = "Usage Count"
NET_LINES_LABEL = "Net Lines"


class CLIHandler(CLICostAdjustmentMixin):
    """Handler for CLI analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize cli handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)
        self.repository = repository

    async def get_cli_summary(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get CLI summary metrics.

        This endpoint only returns the six overview metrics used by the UI:
        total users, total projects, total sessions, total cost, total tokens, and repositories.
        """
        logger.info("Requesting cli-summary analytics")

        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)

        response, cli_costs, dau_metrics, mau_metrics = await asyncio.gather(
            self._pipeline.execute_summary_query(
                agg_builder=self._build_cli_summary_aggregation,
                metrics_builder=self._parse_cli_summary_result,
                metric_filters=None,
                time_period=time_period,
                start_date=start_date,
                end_date=end_date,
                users=users,
                projects=projects,
            ),
            self.get_cli_costs_with_adjustment(
                start_date=start_dt,
                end_date=end_dt,
                users=users,
                projects=projects,
                include_cache_costs=False,
            ),
            self._get_cli_active_users_metric("last_24_hours", "dau", "DAU", "Last 1 day", users, projects),
            self._get_cli_active_users_metric("last_30_days", "mau", "MAU", "Last 1 month", users, projects),
        )

        metrics = response["data"]["metrics"]
        metric_map = {metric["id"]: metric for metric in metrics}
        total_tokens = (
            int(metric_map.get("input_tokens", {}).get("value", 0))
            + int(metric_map.get("cached_creation_tokens", {}).get("value", 0))
            + int(metric_map.get("cached_tokens_read", {}).get("value", 0))
            + int(metric_map.get("output_tokens", {}).get("value", 0))
        )

        metrics.extend(
            [
                {
                    "id": "cli_cost",
                    "label": TOTAL_COST_LABEL,
                    "type": "number",
                    "value": cli_costs["total_cost"],
                    "format": "currency",
                    "description": "Total CLI proxy cost",
                },
                {
                    "id": "total_tokens",
                    "label": "Total Tokens",
                    "type": "number",
                    "value": total_tokens,
                    "format": "number",
                    "description": "Total CLI proxy tokens",
                },
            ]
        )

        overview_metric_ids = [
            "unique_users",
            "dau",
            "mau",
            "unique_sessions",
            "cli_cost",
            "total_tokens",
            "unique_projects",
            "unique_repos",
        ]
        overview_labels = {
            "unique_users": "Total Users",
            "unique_projects": "Total Projects",
            "unique_sessions": "Total Sessions",
            "dau": "DAU",
            "mau": "MAU",
            "cli_cost": TOTAL_COST_LABEL,
            "total_tokens": "Total Tokens",
            "unique_repos": "Repositories",
        }
        overview_descriptions = {
            "unique_users": "Distinct CLI users",
            "dau": "Distinct CLI proxy users active in last 1 day",
            "mau": "Distinct CLI proxy users active in last 1 month",
            "unique_projects": "Distinct CLI projects",
            "unique_sessions": "Distinct CLI sessions",
            "cli_cost": "Total CLI proxy cost",
            "total_tokens": "Total CLI proxy tokens",
            "unique_repos": "Distinct repositories used in CLI activity",
        }
        metrics.extend(dau_metrics + mau_metrics)
        response["data"]["metrics"] = [
            {
                **metric,
                "label": overview_labels.get(metric["id"], metric["label"]),
                "description": overview_descriptions.get(metric["id"], metric.get("description")),
            }
            for metric_id in overview_metric_ids
            for metric in metrics
            if metric["id"] == metric_id
        ]

        return response

    def _build_cli_summary_aggregation(self, query: dict) -> dict:
        """Build aggregation for the six-card CLI overview.

        Note: We do NOT add metric filter here since each aggregation needs different metric filters.
        Uses two different CLI metrics:
        - cli_usage_filter: session/activity data for users, sessions, projects, repositories
        - cli_llm_filter: LiteLLM proxy metric for token data
        """
        # Session/activity metrics come from the non-legacy CLI tool usage stream.
        cli_usage_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}}
        cli_session_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_SESSION_TOTAL.value}}

        # Token/cost metrics (new metric: codemie_litellm_proxy_usage with cli_request=true)
        cli_llm_filter = {
            "bool": {
                "filter": [
                    {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                    {"term": {CLI_REQUEST_FIELD: True}},
                ]
            }
        }
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "unique_users": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"count": {"cardinality": {"field": USER_ID_KEYWORD_FIELD}}},
                },
                "unique_sessions": {
                    "filter": {"bool": {"filter": [cli_session_filter]}},
                    "aggs": {"count": {"cardinality": {"field": SESSION_ID_KEYWORD_FIELD}}},
                },
                "unique_projects": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"count": {"cardinality": {"field": PROJECT_KEYWORD_FIELD}}},
                },
                "unique_repos": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"count": {"cardinality": {"field": REPOSITORY_KEYWORD_FIELD}}},
                },
                "input_tokens": {
                    "filter": {"bool": {"filter": [cli_llm_filter]}},
                    "aggs": {"total": {"sum": {"field": INPUT_TOKENS_FIELD}}},
                },
                "output_tokens": {
                    "filter": {"bool": {"filter": [cli_llm_filter]}},
                    "aggs": {"total": {"sum": {"field": OUTPUT_TOKENS_FIELD}}},
                },
                "cached_tokens_read": {
                    "filter": {"bool": {"filter": [cli_llm_filter]}},
                    "aggs": {"total": {"sum": {"field": CACHE_READ_INPUT_TOKENS_FIELD}}},
                },
                "cached_creation_tokens": {
                    "filter": {"bool": {"filter": [cli_llm_filter]}},
                    "aggs": {"total": {"sum": {"field": CACHE_CREATION_TOKENS_FIELD}}},
                },
            },
        }

    def _parse_cli_summary_result(self, result: dict) -> list[dict]:
        """Parse result for CLI summary metrics used by the overview cards."""
        aggs = result.get("aggregations", {})

        unique_users = int(aggs.get("unique_users", {}).get("count", {}).get("value", 0))
        unique_sessions = int(aggs.get("unique_sessions", {}).get("count", {}).get("value", 0))
        unique_projects = int(aggs.get("unique_projects", {}).get("count", {}).get("value", 0))
        unique_repos = int(aggs.get("unique_repos", {}).get("count", {}).get("value", 0))

        input_tokens = int(aggs.get("input_tokens", {}).get("total", {}).get("value", 0))
        output_tokens = int(aggs.get("output_tokens", {}).get("total", {}).get("value", 0))
        cached_tokens_read = int(aggs.get("cached_tokens_read", {}).get("total", {}).get("value", 0))
        cached_creation_tokens = int(aggs.get("cached_creation_tokens", {}).get("total", {}).get("value", 0))

        metrics = [
            {"id": "input_tokens", "label": "Input Tokens", "type": "number", "value": input_tokens},
            {
                "id": "cached_creation_tokens",
                "label": "Cache Creation Tokens",
                "type": "number",
                "value": cached_creation_tokens,
            },
            {"id": "cached_tokens_read", "label": "Cache Read Tokens", "type": "number", "value": cached_tokens_read},
            {"id": "output_tokens", "label": "Output Tokens", "type": "number", "value": output_tokens},
            {
                "id": "unique_users",
                "label": "Unique Users",
                "type": "number",
                "value": unique_users,
                "format": "number",
                "description": "Distinct CLI users",
            },
            {
                "id": "unique_projects",
                "label": "Total Projects",
                "type": "number",
                "value": unique_projects,
                "format": "number",
                "description": "Distinct CLI projects",
            },
            {
                "id": "unique_sessions",
                "label": "Unique Sessions",
                "type": "number",
                "value": unique_sessions,
                "format": "number",
                "description": "Distinct CLI sessions",
            },
            {
                "id": "unique_repos",
                "label": "Unique Repositories",
                "type": "number",
                "value": unique_repos,
                "format": "number",
                "description": "Distinct repositories used in CLI activity",
            },
        ]

        logger.debug(
            f"Parsed cli-summary result: aggregation_keys={list(aggs.keys())}, "
            f"unique_users={unique_users}, unique_projects={unique_projects}, "
            f"unique_sessions={unique_sessions}, unique_repos={unique_repos}, "
            f"cached_creation_tokens={cached_creation_tokens}, "
            f"metrics_built={len(metrics)}"
        )
        return metrics

    async def _get_cli_active_users_metric(
        self,
        time_period: str,
        metric_id: str,
        label: str,
        fixed_timeframe: str,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> list[dict]:
        """Get fixed-window active CLI users from proxy usage only."""
        response = await self._pipeline.execute_summary_query(
            agg_builder=self._build_cli_active_users_aggregation,
            metrics_builder=lambda result: self._parse_cli_active_users_result(
                result,
                metric_id=metric_id,
                label=label,
                fixed_timeframe=fixed_timeframe,
            ),
            metric_filters=None,
            time_period=time_period,
            users=users,
            projects=projects,
        )
        return response["data"]["metrics"]

    def _build_cli_active_users_aggregation(self, query: dict) -> dict:
        """Build cardinality aggregation for CLI proxy active users."""
        cli_proxy_filter = {
            "bool": {
                "filter": [
                    {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                    {"term": {CLI_REQUEST_FIELD: True}},
                ]
            }
        }

        return {
            "query": query,
            "size": 0,
            "aggs": {
                "unique_users": {
                    "filter": cli_proxy_filter,
                    "aggs": {
                        "count": {
                            "cardinality": {
                                "field": USER_ID_KEYWORD_FIELD,
                                "precision_threshold": 3000,
                            }
                        }
                    },
                }
            },
        }

    def _parse_cli_active_users_result(
        self,
        result: dict,
        metric_id: str,
        label: str,
        fixed_timeframe: str,
    ) -> list[dict]:
        """Parse cardinality result for CLI fixed-window active users."""
        value = int(result.get("aggregations", {}).get("unique_users", {}).get("count", {}).get("value", 0))
        return [
            {
                "id": metric_id,
                "label": label,
                "type": "number",
                "value": value,
                "format": "number",
                "description": f"Distinct CLI proxy users active in {fixed_timeframe.lower()}",
                "fixed_timeframe": fixed_timeframe,
            }
        ]

    async def get_cli_agents(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get top CLI agents (clients) usage analytics."""
        logger.info("Requesting cli-agents analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_cli_agents_aggregation(query, fetch_size),
            result_parser=self._parse_cli_agents_result,
            columns=self._get_cli_agents_columns(),
            group_by_field="attributes.codemie_client.keyword",
            metric_filters=[MetricName.CLI_TOOL_USAGE_TOTAL.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_cli_agents_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for CLI agents (clients) usage with fetch-and-slice."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (none needed, just counting doc_count)
        sub_aggs = {}

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field="attributes.codemie_client.keyword",
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

    def _parse_cli_agents_result(self, result: dict) -> list[dict]:
        """Parse result for CLI agents (clients) usage."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = [{"client_name": bucket["key"], "total_usage": bucket["doc_count"]} for bucket in buckets]
        logger.debug(f"Parsed cli-agents result: total_client_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_cli_agents_columns(self) -> list[dict]:
        """Get column definitions for CLI agents (clients) usage."""
        return [
            {"id": "client_name", "label": "Client", "type": "string"},
            {"id": "total_usage", "label": USAGE_COUNT_LABEL, "type": "number"},
        ]

    async def get_cli_llms(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get top CLI LLMs usage analytics with model name combining."""
        logger.info("Requesting cli-llms analytics with model name aggregation")

        # Get response from pipeline (standard flow)
        response = await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_cli_llms_aggregation(query, fetch_size),
            result_parser=self._parse_cli_llms_result,
            columns=self._get_cli_llms_columns(),
            group_by_field="attributes.llm_model.keyword",
            metric_filters=[
                MetricName.CLI_TOOL_USAGE_TOTAL.value,
                MetricName.LLM_PROXY_REQUESTS_TOTAL.value,
            ],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

        # Apply model name combining to rows before returning to frontend
        response["data"]["rows"] = _combine_model_names(response["data"]["rows"])

        return response

    def _build_cli_llms_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for CLI LLMs usage with fetch-and-slice."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (none needed, just counting doc_count)
        sub_aggs = {}

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field="attributes.llm_model.keyword",
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

    def _parse_cli_llms_result(self, result: dict) -> list[dict]:
        """Parse result for CLI LLMs usage."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = [{"model_name": bucket["key"], "total_requests": bucket["doc_count"]} for bucket in buckets]
        logger.debug(f"Parsed cli-llms result: total_model_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_cli_llms_columns(self) -> list[dict]:
        """Get column definitions for CLI LLMs usage."""
        return [
            {"id": "model_name", "label": "Model", "type": "string"},
            {"id": "total_requests", "label": "Requests", "type": "number"},
        ]

    async def get_cli_users(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI users activity analytics."""
        logger.info("Requesting cli-users analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_cli_users_aggregation(query, fetch_size),
            result_parser=self._parse_cli_users_result,
            columns=self._get_cli_users_columns(),
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            metric_filters=[MetricName.CLI_TOOL_USAGE_TOTAL.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_cli_users_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for CLI users activity with top_metrics for last project/repo."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations for last project and last repository using top_metrics
        sub_aggs = {
            "last_project": {
                "filter": {"bool": {"filter": [{"exists": {"field": PROJECT_KEYWORD_FIELD}}]}},
                "aggs": {
                    "top_project": {
                        "top_metrics": {
                            "metrics": {"field": PROJECT_KEYWORD_FIELD},
                            "size": 1,
                            "sort": {TIMESTAMP_FIELD: "desc"},
                        }
                    }
                },
            },
            "last_repository": {
                "filter": {"bool": {"filter": [{"exists": {"field": REPOSITORY_KEYWORD_FIELD}}]}},
                "aggs": {
                    "top_repository": {
                        "top_metrics": {
                            "metrics": {"field": REPOSITORY_KEYWORD_FIELD},
                            "size": 1,
                            "sort": {TIMESTAMP_FIELD: "desc"},
                        }
                    }
                },
            },
        }

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
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

    def _parse_cli_users_result(self, result: dict) -> list[dict]:
        """Parse result for CLI users activity with last project/repository."""
        rows = []
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        for bucket in buckets:
            user_name = bucket["key"]
            total_commands = bucket["doc_count"]

            # Extract last_project from top_metrics
            last_project = None
            last_project_top = bucket.get("last_project", {}).get("top_project", {}).get("top", [])
            if last_project_top:
                last_project = last_project_top[0].get("metrics", {}).get(PROJECT_KEYWORD_FIELD)

            # Extract last_repository from top_metrics
            last_repository = None
            last_repository_top = bucket.get("last_repository", {}).get("top_repository", {}).get("top", [])
            if last_repository_top:
                last_repository = last_repository_top[0].get("metrics", {}).get(REPOSITORY_KEYWORD_FIELD)

            rows.append(
                {
                    "user_name": user_name,
                    "total_commands": total_commands,
                    "last_project": last_project,
                    "last_repository": last_repository,
                }
            )

        logger.debug(f"Parsed cli-users result: total_user_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_cli_users_columns(self) -> list[dict]:
        """Get column definitions for CLI users activity."""
        return [
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "total_commands", "label": "Commands", "type": "number"},
            {"id": "last_project", "label": "Last Project", "type": "string"},
            {"id": "last_repository", "label": "Last Repository", "type": "string"},
        ]

    async def get_cli_errors(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get top proxy errors analytics."""
        logger.info("Requesting cli-errors analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_cli_errors_aggregation(query, fetch_size),
            result_parser=self._parse_cli_errors_result,
            columns=self._get_cli_errors_columns(),
            group_by_field=RESPONSE_STATUS_FIELD,
            metric_filters=[MetricName.LLM_PROXY_ERRORS_TOTAL.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_cli_errors_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for proxy errors with fetch-and-slice."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (none needed for proxy errors)
        sub_aggs = {}

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=RESPONSE_STATUS_FIELD,
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

    def _parse_cli_errors_result(self, result: dict) -> list[dict]:
        """Parse result for proxy errors."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = [{"response_status": bucket["key"], "total_occurrences": bucket["doc_count"]} for bucket in buckets]
        logger.debug(f"Parsed cli-errors result: total_error_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_cli_errors_columns(self) -> list[dict]:
        """Get column definitions for proxy errors."""
        return [
            {"id": "response_status", "label": "Response Status", "type": "string"},
            {"id": "total_occurrences", "label": "Occurrences", "type": "number"},
        ]

    async def get_cli_repositories(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI repositories stats with accurate row-level pagination.

        Uses 3-level nested aggregation (repo→branch→user) with flattening.
        """
        logger.info("Requesting cli-repositories analytics")

        # Use specialized method for nested/flattened aggregations to ensure accurate row-level pagination
        return await self._pipeline.execute_tabular_query_with_flattened_rows(
            agg_builder=lambda query, fetch_size: self._build_cli_repositories_aggregation(query, fetch_size),
            result_parser=self._parse_cli_repositories_result,
            columns=self._get_cli_repositories_columns(),
            flattening_multiplier=15,  # Conservative: avg 3 branches × 5 users per repo = 15x multiplier
            sort_keys=[
                ("repository", False),  # Primary: Alphabetical (ASC)
                ("branch", False),  # Secondary: Alphabetical (ASC)
                ("user_name", False),  # Tertiary: Alphabetical (ASC)
            ],
            metric_filters=[
                MetricName.CLI_TOOL_USAGE_TOTAL.value,  # New CLI session metric
                MetricName.CLI_LLM_USAGE_TOTAL.value,  # For token data
            ],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_cli_repositories_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build 3-level nested terms aggregation (repo→branch→user) with 6 metrics per user.

        Note: Uses over-fetching strategy with flattening_multiplier to ensure accurate row-level pagination.
        The pipeline handles fetching extra repositories to account for flattening, then slices final rows.
        MERGES data from TWO metrics:
        - Token data from CLI_LLM_USAGE_TOTAL (new metric, accurate server-side tracking)
        - Session/file data from CLI_TOOL_USAGE_TOTAL
        """
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Level 3 (User) sub-aggregations - split by metric source
        user_metrics = {
            # Token data from NEW metric (LiteLLM proxy with cli_request=true)
            "token_data": {
                "filter": {
                    "bool": {
                        "filter": [
                            {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            {"term": {CLI_REQUEST_FIELD: True}},
                        ]
                    }
                },
                "aggs": {
                    "input_tokens": {"sum": {"field": INPUT_TOKENS_FIELD}},
                    "output_tokens": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                    "cache_read_tokens": {"sum": {"field": CACHE_READ_INPUT_TOKENS_FIELD}},
                    "cache_creation_tokens": {"sum": {"field": CACHE_CREATION_TOKENS_FIELD}},
                },
            },
            # Session data from the non-legacy CLI tool usage metric
            "session_data": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}},
                "aggs": {
                    "session_duration": {"sum": {"field": SESSION_DURATION_MS_FIELD}},
                    "total_lines_added": {"sum": {"field": TOTAL_LINES_ADDED_FIELD}},
                },
            },
        }

        # Level 3: Build user aggregation using AggregationBuilder (optimize size)
        user_agg = AggregationBuilder.build_terms_agg(
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            fetch_size=20,  # Reasonable cap: max 20 users per branch (not fetch_size which could be 100+)
            order={"_count": "desc"},
            sub_aggs=user_metrics,
        )

        # Level 2: Build branch aggregation using AggregationBuilder (optimize size)
        branch_agg = AggregationBuilder.build_terms_agg(
            group_by_field="attributes.branch.keyword",
            fetch_size=10,  # Reasonable cap: max 10 branches per repo (not fetch_size)
            order={"_count": "desc"},
            sub_aggs={"users": user_agg},
        )

        # Level 1: Build repository aggregation using AggregationBuilder (use fetch_size for pagination)
        repository_agg = AggregationBuilder.build_terms_agg(
            group_by_field=REPOSITORY_KEYWORD_FIELD,
            fetch_size=fetch_size,  # Use fetch_size for top-level pagination
            order={"_count": "desc"},
            sub_aggs={"branches": branch_agg},
        )

        # Construct full aggregation body
        agg_body = {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": repository_agg,
            },
        }

        return agg_body

    def _parse_cli_repositories_result(self, result: dict) -> list[dict]:
        """Parse 3-level nested result and flatten into tabular rows.

        Note: Sorting is now handled by query_pipeline for consistent pagination.
        Extracts token data from CLI_LLM_USAGE_TOTAL and session data from CLI_TOOL_USAGE_TOTAL.
        """
        rows = []
        repo_buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        for repo_bucket in repo_buckets:
            repo_name = repo_bucket["key"]
            branch_buckets = repo_bucket.get("branches", {}).get("buckets", [])

            for branch_bucket in branch_buckets:
                branch_name = branch_bucket["key"]
                user_buckets = branch_bucket.get("users", {}).get("buckets", [])

                for user_bucket in user_buckets:
                    user_name = user_bucket["key"]

                    # Extract token data from NEW metric (filtered aggregation)
                    token_data = user_bucket.get("token_data", {})
                    input_tokens = int(token_data.get("input_tokens", {}).get("value", 0))
                    output_tokens = int(token_data.get("output_tokens", {}).get("value", 0))
                    cache_read_tokens = int(token_data.get("cache_read_tokens", {}).get("value", 0))
                    cache_creation_tokens = int(token_data.get("cache_creation_tokens", {}).get("value", 0))

                    # Extract session data from OLD metric (filtered aggregation)
                    session_data = user_bucket.get("session_data", {})
                    session_duration = int(session_data.get("session_duration", {}).get("value", 0))
                    total_lines_added = int(session_data.get("total_lines_added", {}).get("value", 0))

                    row = {
                        "repository": repo_name,
                        "branch": branch_name,
                        "user_name": user_name,
                        "input_tokens": input_tokens,
                        "cache_creation_tokens": cache_creation_tokens,
                        "cache_read_tokens": cache_read_tokens,
                        "output_tokens": output_tokens,
                        "session_duration": session_duration,
                        "total_lines_added": total_lines_added,
                    }
                    rows.append(row)

        # Sorting removed - now handled by pipeline for consistent pagination across pages

        logger.debug(
            f"Parsed cli-repositories result: total_repo_buckets={len(repo_buckets)}, "
            f"total_rows_flattened={len(rows)}"
        )
        return rows

    def _get_cli_repositories_columns(self) -> list[dict]:
        """Get column definitions for CLI repositories stats."""
        return [
            {"id": "repository", "label": "Repository", "type": "string"},
            {"id": "branch", "label": "Branch", "type": "string"},
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "input_tokens", "label": "Input Tokens", "type": "number"},
            {"id": "cache_creation_tokens", "label": "Cache Creation Tokens", "type": "number"},
            {"id": "cache_read_tokens", "label": "Cache Read Tokens", "type": "number"},
            {"id": "output_tokens", "label": "Output Tokens", "type": "number"},
            {"id": "session_duration", "label": "Session Duration (ms)", "type": "number"},
            {"id": "total_lines_added", "label": "Total Lines Added", "type": "number"},
        ]

    async def get_cli_top_performers(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get top CLI performers ranked by total lines added."""
        logger.info("Requesting cli-top-performers analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_cli_top_performers_aggregation(query, fetch_size),
            result_parser=self._parse_cli_top_performers_result,
            columns=self._get_cli_top_performers_columns(),
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            metric_filters=[MetricName.CLI_TOOL_USAGE_TOTAL.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_cli_top_performers_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for top performers with total lines added."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Sub-aggregations: sum of lines_added and top_metrics for last_project
        sub_aggs = {
            "total_lines_added": {"sum": {"field": TOTAL_LINES_ADDED_FIELD}},
            "last_project": {
                "filter": {"bool": {"filter": [{"exists": {"field": PROJECT_KEYWORD_FIELD}}]}},
                "aggs": {
                    "top_project": {
                        "top_metrics": {
                            "metrics": {"field": PROJECT_KEYWORD_FIELD},
                            "size": 1,
                            "sort": {TIMESTAMP_FIELD: "desc"},
                        }
                    }
                },
            },
        }

        # Build terms aggregation using helper, ordered by total_lines_added
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=USER_EMAIL_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"total_lines_added": "desc"},
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

    def _parse_cli_top_performers_result(self, result: dict) -> list[dict]:
        """Parse result for top performers."""
        rows = []
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        for bucket in buckets:
            user_name = bucket["key"]
            total_lines_added = int(bucket.get("total_lines_added", {}).get("value", 0))

            # Extract last_project from top_metrics
            last_project = None
            last_project_top = bucket.get("last_project", {}).get("top_project", {}).get("top", [])
            if last_project_top:
                last_project = last_project_top[0].get("metrics", {}).get(PROJECT_KEYWORD_FIELD)

            rows.append({"user_name": user_name, "total_lines_added": total_lines_added, "last_project": last_project})

        logger.debug(f"Parsed cli-top-performers result: total_user_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_cli_top_performers_columns(self) -> list[dict]:
        """Get column definitions for top performers."""
        return [
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "total_lines_added", "label": "Total Lines Added", "type": "number"},
            {"id": "last_project", "label": "Last Project", "type": "string"},
        ]

    async def get_cli_top_versions(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get top CLI versions ranked by usage count."""
        logger.info("Requesting cli-top-versions analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_cli_top_versions_aggregation(query, fetch_size),
            result_parser=self._parse_cli_top_versions_result,
            columns=self._get_cli_top_versions_columns(),
            group_by_field="attributes.codemie_cli.keyword",
            metric_filters=[MetricName.CLI_TOOL_USAGE_TOTAL.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_cli_top_versions_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for top CLI versions."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Sub-aggregations: none needed, just counting doc_count
        sub_aggs = {}

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field="attributes.codemie_cli.keyword",
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

    def _parse_cli_top_versions_result(self, result: dict) -> list[dict]:
        """Parse result for top CLI versions."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = [{"version": bucket["key"], "usage_count": bucket["doc_count"]} for bucket in buckets]
        logger.debug(f"Parsed cli-top-versions result: total_version_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_cli_top_versions_columns(self) -> list[dict]:
        """Get column definitions for top CLI versions."""
        return [
            {"id": "version", "label": "Version", "type": "string"},
            {"id": "usage_count", "label": USAGE_COUNT_LABEL, "type": "number"},
        ]

    async def get_cli_top_proxy_endpoints(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get top proxy endpoints ranked by request count."""
        logger.info("Requesting cli-top-proxy-endpoints analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_cli_top_proxy_endpoints_aggregation(query, fetch_size),
            result_parser=self._parse_cli_top_proxy_endpoints_result,
            columns=self._get_cli_top_proxy_endpoints_columns(),
            group_by_field="attributes.endpoint.keyword",
            metric_filters=[MetricName.LLM_PROXY_REQUESTS_TOTAL.value],  # Only count requests for traffic volume
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_cli_top_proxy_endpoints_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for top proxy endpoints."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Sub-aggregations: none needed, just counting doc_count
        sub_aggs = {}

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field="attributes.endpoint.keyword",
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

    def _parse_cli_top_proxy_endpoints_result(self, result: dict) -> list[dict]:
        """Parse result for top proxy endpoints."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = [{"endpoint": bucket["key"], "request_count": bucket["doc_count"]} for bucket in buckets]
        logger.debug(
            f"Parsed cli-top-proxy-endpoints result: total_endpoint_buckets={len(buckets)}, rows_parsed={len(rows)}"
        )
        return rows

    def _get_cli_top_proxy_endpoints_columns(self) -> list[dict]:
        """Get column definitions for top proxy endpoints."""
        return [
            {"id": "endpoint", "label": "Endpoint", "type": "string"},
            {"id": "request_count", "label": "Request Count", "type": "number"},
        ]

    async def get_cli_tools_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI tool usage analytics showing which tools are used most frequently."""
        logger.info("Requesting cli-tools-usage analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_cli_tools_usage_aggregation(query, fetch_size),
            result_parser=self._parse_cli_tools_usage_result,
            columns=self._get_cli_tools_usage_columns(),
            group_by_field=f"{TOOL_NAMES_FIELD}.keyword",
            metric_filters=[MetricName.CLI_TOOL_USAGE_TOTAL.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_cli_tools_usage_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for tool names (array field flattened by ES)."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=f"{TOOL_NAMES_FIELD}.keyword",
            fetch_size=fetch_size,
            order={"_count": "desc"},
            sub_aggs={},
        )

        return {"query": query, "size": 0, "aggs": {"paginated_results": terms_agg}}

    def _parse_cli_tools_usage_result(self, result: dict) -> list[dict]:
        """Parse CLI tool usage results."""
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = [{"tool_name": bucket["key"], "session_count": bucket["doc_count"]} for bucket in buckets]
        logger.debug(f"Parsed cli-tools-usage: {len(rows)} tools")
        return rows

    def _get_cli_tools_usage_columns(self) -> list[dict]:
        """Column definitions for tool usage."""
        return [
            {"id": "tool_name", "label": "Tool Name", "type": "string"},
            {"id": "session_count", "label": "Sessions Using Tool", "type": "number"},
        ]

    async def get_cli_insights_weekday_pattern(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get weekday activity distribution for CLI insights."""
        patterns = await self._get_cli_time_pattern_rows(time_period, start_date, end_date, users, projects)
        rows = sorted(patterns["weekday"].values(), key=lambda row: row["weekday_index"])
        for row in rows:
            row.pop("weekday_index", None)
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_weekday_pattern_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_hourly_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 24,
    ) -> dict:
        """Get hourly activity distribution for CLI insights."""
        patterns = await self._get_cli_time_pattern_rows(time_period, start_date, end_date, users, projects)
        rows = sorted(patterns["hour"].values(), key=lambda row: row["hour"])
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_hourly_usage_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_session_depth(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get prompts-per-session distribution for CLI insights."""
        rows = await self._get_cli_session_depth_rows(time_period, start_date, end_date, users, projects)
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_session_depth_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_user_classification(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get aggregated user classification metrics for CLI insights."""
        rows = await self._get_cli_insights_user_rows(time_period, start_date, end_date, users, projects)
        grouped: dict[str, dict] = defaultdict(
            lambda: {"classification": "", "user_count": 0, "total_cost": 0.0, "avg_cost": 0.0}
        )
        for row in rows:
            bucket = grouped[row["classification"]]
            bucket["classification"] = row["classification"]
            bucket["user_count"] += 1
            bucket["total_cost"] += float(row["total_cost"])
        result_rows = list(grouped.values())
        for row in result_rows:
            row["total_cost"] = round(row["total_cost"], 2)
            row["avg_cost"] = round(row["total_cost"] / max(row["user_count"], 1), 2)
        result_rows.sort(key=lambda row: row["total_cost"], reverse=True)
        return self._format_custom_tabular_response(
            rows=result_rows,
            columns=self._get_cli_insights_user_classification_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_top_users_by_cost(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get top CLI users ranked by cost."""
        rows = await self._get_cli_insights_user_rows(time_period, start_date, end_date, users, projects)
        rows.sort(key=lambda row: row["total_cost"], reverse=True)
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_top_users_by_cost_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_top_spenders(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get Top Spenders table data for CLI insights."""
        rows = await self._get_cli_insights_user_rows(time_period, start_date, end_date, users, projects)
        rows.sort(key=lambda row: row["total_cost"], reverse=True)
        ranked_rows = [{"rank": index + 1, **row} for index, row in enumerate(rows)]
        return self._format_custom_tabular_response(
            rows=ranked_rows,
            columns=self._get_cli_insights_top_spenders_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_all_users(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get all CLI users table data for CLI insights."""
        rows = await self._get_cli_insights_user_rows(time_period, start_date, end_date, users, projects)
        rows.sort(key=lambda row: row["total_cost"], reverse=True)
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_all_users_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_user_detail(
        self,
        user_name: str,
        user_id: str | None = None,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get drilldown detail for a single CLI user."""
        detail = await self._get_cli_insights_user_detail_payload(
            user_name=user_name,
            user_id=user_id,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )
        return self._format_custom_detail_response(
            detail=detail,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

    async def get_cli_insights_user_key_metrics(self, **kwargs) -> dict:
        """Get key metrics widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        return self._format_custom_summary_response(
            metrics=detail["key_metrics"]["data"]["metrics"],
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
        )

    async def get_cli_insights_user_tools(self, **kwargs) -> dict:
        """Get tools donut widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        tools_chart = detail["tools_chart"]
        rows = tools_chart["data"]["rows"]
        columns = tools_chart["data"]["columns"]
        return self._format_custom_tabular_response(
            rows=rows,
            columns=columns,
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
            page=0,
            per_page=max(len(rows), 1),
        )

    async def get_cli_insights_user_models(self, **kwargs) -> dict:
        """Get models donut widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        models_chart = detail["models_chart"]
        rows = models_chart["data"]["rows"]
        columns = models_chart["data"]["columns"]
        return self._format_custom_tabular_response(
            rows=rows,
            columns=columns,
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
            page=0,
            per_page=max(len(rows), 1),
        )

    async def get_cli_insights_user_workflow_intent(self, **kwargs) -> dict:
        """Get workflow intent widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        return self._format_custom_summary_response(
            metrics=detail["workflow_intent_metrics"]["data"]["metrics"],
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
        )

    async def get_cli_insights_user_classification_detail(self, **kwargs) -> dict:
        """Get classification widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        return self._format_custom_summary_response(
            metrics=detail["classification_metrics"]["data"]["metrics"],
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
        )

    async def get_cli_insights_user_category_breakdown(self, **kwargs) -> dict:
        """Get category breakdown widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        category_breakdown_chart = detail["category_breakdown_chart"]
        rows = category_breakdown_chart["data"]["rows"]
        columns = category_breakdown_chart["data"]["columns"]
        return self._format_custom_tabular_response(
            rows=rows,
            columns=columns,
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
            page=0,
            per_page=max(len(rows), 1),
        )

    async def get_cli_insights_user_repositories(self, **kwargs) -> dict:
        """Get repositories table widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        repositories_table = detail["repositories_table"]
        rows = repositories_table["data"]["rows"]
        columns = repositories_table["data"]["columns"]
        return self._format_custom_tabular_response(
            rows=rows,
            columns=columns,
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
            page=0,
            per_page=max(len(rows), 1),
        )

    async def _get_cli_insights_user_detail_payload(
        self,
        user_name: str,
        user_id: str | None = None,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Build the full CLI user detail payload used by user detail widgets."""
        if not user_id:
            user_id = await self._resolve_cli_insights_user_id(
                entity_name=user_name,
                time_period=time_period,
                start_date=start_date,
                end_date=end_date,
                users=users,
                projects=projects,
            )
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        detail_query = self._build_cli_insights_entity_query(
            start_dt,
            end_dt,
            users,
            projects,
            entity_name=user_name,
            entity_id=user_id,
        )
        aggregation_result, tool_docs_result = await asyncio.gather(
            self.repository.execute_aggregation_query(self._build_cli_insights_user_detail_aggregation(detail_query)),
            self.repository.execute_search_query(
                self._build_cli_insights_tool_docs_query(detail_query),
                size=10000,
            ),
        )
        detail = self._parse_cli_insights_user_detail_result(
            user_name=user_name,
            result=aggregation_result,
            tool_docs_result=tool_docs_result,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        return detail

    async def _resolve_cli_insights_user_id(
        self,
        entity_name: str,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> str | None:
        """Resolve user_id from existing CLI insights user rows when only label/email is provided."""
        normalized_entity_name = entity_name.strip().lower()
        if not normalized_entity_name:
            return None

        rows = await self._get_cli_insights_user_rows(time_period, start_date, end_date, users, projects)
        for row in rows:
            row_user_name = str(row.get("user_name", "")).strip().lower()
            row_user_email = str(row.get("user_email", "")).strip().lower()
            if normalized_entity_name in {row_user_name, row_user_email}:
                resolved_user_id = row.get("user_id")
                if resolved_user_id:
                    return str(resolved_user_id)
        return None

    async def get_cli_insights_project_classification(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get aggregated project classification metrics for CLI insights."""
        rows = await self._get_cli_insights_project_rows(time_period, start_date, end_date, users, projects)
        grouped: dict[str, dict] = defaultdict(lambda: {"classification": "", "project_count": 0, "total_cost": 0.0})
        for row in rows:
            bucket = grouped[row["classification"]]
            bucket["classification"] = row["classification"]
            bucket["project_count"] += 1
            bucket["total_cost"] += float(row["total_cost"])
        result_rows = list(grouped.values())
        for row in result_rows:
            row["total_cost"] = round(row["total_cost"], 2)
        result_rows.sort(key=lambda row: row["total_cost"], reverse=True)
        return self._format_custom_tabular_response(
            rows=result_rows,
            columns=self._get_cli_insights_project_classification_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_top_projects_by_cost(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get top CLI projects ranked by cost."""
        rows = await self._get_cli_insights_project_rows(time_period, start_date, end_date, users, projects)
        rows.sort(key=lambda row: row["total_cost"], reverse=True)
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_top_projects_by_cost_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def _get_cli_time_pattern_rows(
        self,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> dict[str, dict]:
        """Aggregate hourly histogram into weekday and hour counts."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(
            start_dt,
            end_dt,
            users,
            projects,
            [MetricName.CLI_TOOL_USAGE_TOTAL.value],
        )
        result = await self.repository.execute_aggregation_query(
            {
                "query": query,
                "size": 0,
                "aggs": {
                    "hourly_buckets": {
                        "date_histogram": {
                            "field": TIMESTAMP_FIELD,
                            "calendar_interval": "hour",
                            "min_doc_count": 1,
                        }
                    }
                },
            }
        )

        weekday_rows: dict[str, dict] = {}
        hour_rows: dict[int, dict] = {}
        weekday_order = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

        for bucket in result.get("aggregations", {}).get("hourly_buckets", {}).get("buckets", []):
            key_as_string = bucket.get("key_as_string")
            if not key_as_string:
                continue
            bucket_dt = datetime.fromisoformat(key_as_string.replace("Z", "+00:00"))
            weekday_index = (bucket_dt.weekday() + 1) % 7
            weekday_label = weekday_order[weekday_index]
            weekday_rows.setdefault(
                weekday_label,
                {"weekday": weekday_label, "weekday_index": weekday_index, "activity_count": 0},
            )
            weekday_rows[weekday_label]["activity_count"] += bucket["doc_count"]
            hour_rows.setdefault(bucket_dt.hour, {"hour": bucket_dt.hour, "activity_count": 0})
            hour_rows[bucket_dt.hour]["activity_count"] += bucket["doc_count"]

        return {"weekday": weekday_rows, "hour": hour_rows}

    async def _get_cli_session_depth_rows(
        self,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> list[dict]:
        """Build session depth distribution from prompts-per-session counts."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(
            start_dt,
            end_dt,
            users,
            projects,
            [MetricName.CLI_TOOL_USAGE_TOTAL.value],
        )
        result = await self.repository.execute_aggregation_query(
            {
                "query": query,
                "size": 0,
                "aggs": {
                    "sessions": {
                        "terms": {"field": SESSION_ID_KEYWORD_FIELD, "size": 10000},
                        "aggs": {"total_prompts": {"sum": {"field": "attributes.total_user_prompts"}}},
                    }
                },
            }
        )

        bucket_ranges = [
            ("1", lambda value: value <= 1),
            ("2-5", lambda value: 2 <= value <= 5),
            ("6-10", lambda value: 6 <= value <= 10),
            ("11-20", lambda value: 11 <= value <= 20),
            ("21-50", lambda value: 21 <= value <= 50),
            ("51-100", lambda value: 51 <= value <= 100),
            ("100+", lambda value: value > 100),
        ]
        counts = {label: 0 for label, _predicate in bucket_ranges}

        for bucket in result.get("aggregations", {}).get("sessions", {}).get("buckets", []):
            prompts = int(bucket.get("total_prompts", {}).get("value", 0) or 0)
            for label, predicate in bucket_ranges:
                if predicate(prompts):
                    counts[label] += 1
                    break

        return [{"range": label, "count": counts[label]} for label, _predicate in bucket_ranges]

    async def _get_cli_insights_user_rows(
        self,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> list[dict]:
        """Build user-level CLI insight rows used by multiple widgets."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(start_dt, end_dt, users, projects, None)
        result = await self.repository.execute_aggregation_query(self._build_cli_insights_user_aggregation(query))
        rows = []
        for bucket in result.get("aggregations", {}).get("users", {}).get("buckets", []):
            user_id = bucket["key"]
            user_name = self._extract_top_metric(bucket, "user_name", USER_NAME_KEYWORD_FIELD) or user_id
            user_email = self._extract_top_metric(bucket, "user_email", USER_EMAIL_KEYWORD_FIELD) or user_name
            total_cost = round(bucket.get("cost_bucket", {}).get("total_cost", {}).get("value", 0) or 0, 2)
            repositories = [
                repo["key"] for repo in bucket.get("repositories", {}).get("buckets", {}).get("buckets", [])
            ]
            branches = [branch["key"] for branch in bucket.get("branches", {}).get("buckets", {}).get("buckets", [])]
            projects_used = [
                project["key"] for project in bucket.get("projects", {}).get("buckets", {}).get("buckets", [])
            ]
            classification, _confidence = self._classify_cli_entity(
                repositories=repositories,
                branches=branches,
                project_name=projects_used[0] if projects_used else None,
                total_cost=total_cost,
            )
            total_lines_added = int(bucket.get("total_lines_added", {}).get("total", {}).get("value", 0) or 0)
            total_lines_removed = int(bucket.get("total_lines_removed", {}).get("total", {}).get("value", 0) or 0)
            total_sessions = int(bucket.get("total_sessions", {}).get("count", {}).get("value", 0) or 0)
            rows.append(
                {
                    "user_id": user_id,
                    "user_name": user_name,
                    "user_email": user_email,
                    "classification": classification,
                    "total_sessions": total_sessions,
                    "total_lines_added": total_lines_added,
                    "total_lines_removed": total_lines_removed,
                    "net_lines": total_lines_added - total_lines_removed,
                    "total_cost": total_cost,
                }
            )
        return rows

    async def _get_cli_insights_project_rows(
        self,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> list[dict]:
        """Build project-level CLI insight rows used by multiple widgets."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(start_dt, end_dt, users, projects, None)
        result = await self.repository.execute_aggregation_query(self._build_cli_insights_project_aggregation(query))
        rows = []
        for bucket in result.get("aggregations", {}).get("projects", {}).get("buckets", []):
            project_name = str(bucket["key"]).strip()
            if not project_name:
                continue
            total_cost = round(bucket.get("cost_bucket", {}).get("total_cost", {}).get("value", 0) or 0, 2)
            repositories = [
                repo["key"] for repo in bucket.get("repositories", {}).get("buckets", {}).get("buckets", [])
            ]
            branches = [branch["key"] for branch in bucket.get("branches", {}).get("buckets", {}).get("buckets", [])]
            classification, _confidence = self._classify_cli_entity(
                repositories=repositories,
                branches=branches,
                project_name=project_name,
                total_cost=total_cost,
            )
            rows.append(
                {
                    "project_name": project_name,
                    "project_type": self._infer_project_type(project_name),
                    "classification": classification,
                    "total_cost": total_cost,
                }
            )
        return rows

    def _build_cli_insights_user_aggregation(self, query: dict) -> dict:
        """Build user aggregation for CLI insights."""
        usage_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}}
        lines_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}}
        session_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_SESSION_TOTAL.value}}
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "users": {
                    "terms": {"field": USER_ID_KEYWORD_FIELD, "size": 10000},
                    "aggs": {
                        "user_name": {
                            "top_metrics": {
                                "metrics": {"field": USER_NAME_KEYWORD_FIELD},
                                "size": 1,
                                "sort": {TIMESTAMP_FIELD: "desc"},
                            }
                        },
                        "user_email": {
                            "top_metrics": {
                                "metrics": {"field": USER_EMAIL_KEYWORD_FIELD},
                                "size": 1,
                                "sort": {TIMESTAMP_FIELD: "desc"},
                            }
                        },
                        "projects": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": PROJECT_KEYWORD_FIELD, "size": 10}}},
                        },
                        "repositories": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": REPOSITORY_KEYWORD_FIELD, "size": 20}}},
                        },
                        "branches": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": BRANCH_KEYWORD_FIELD, "size": 20}}},
                        },
                        "cost_bucket": {
                            "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            "aggs": {"total_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
                        },
                        "total_lines_added": {
                            "filter": lines_filter,
                            "aggs": {"total": {"sum": {"field": TOTAL_LINES_ADDED_FIELD}}},
                        },
                        "total_lines_removed": {
                            "filter": lines_filter,
                            "aggs": {"total": {"sum": {"field": "attributes.total_lines_removed"}}},
                        },
                        "total_sessions": {
                            "filter": session_filter,
                            "aggs": {"count": {"cardinality": {"field": SESSION_ID_KEYWORD_FIELD}}},
                        },
                    },
                }
            },
        }

    def _build_cli_insights_project_aggregation(self, query: dict) -> dict:
        """Build project aggregation for CLI insights."""
        usage_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}}
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "projects": {
                    "terms": {"field": PROJECT_KEYWORD_FIELD, "size": 10000, "exclude": ""},
                    "aggs": {
                        "repositories": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": REPOSITORY_KEYWORD_FIELD, "size": 20}}},
                        },
                        "branches": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": BRANCH_KEYWORD_FIELD, "size": 20}}},
                        },
                        "cost_bucket": {
                            "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            "aggs": {"total_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
                        },
                    },
                }
            },
        }

    def _build_cli_insights_entity_query(
        self,
        start_dt: datetime,
        end_dt: datetime,
        users: list[str] | None,
        projects: list[str] | None,
        entity_name: str,
        entity_id: str | None = None,
    ) -> dict:
        """Build base query scoped to one CLI user by name or email."""
        query = self._pipeline._build_query(start_dt, end_dt, users, projects, None)
        should_filters = [
            {"term": {USER_NAME_KEYWORD_FIELD: entity_name}},
            {"term": {USER_EMAIL_KEYWORD_FIELD: entity_name}},
        ]
        if entity_id:
            should_filters.append({"term": {USER_ID_KEYWORD_FIELD: entity_id}})
        entity_filter = {"bool": {"should": should_filters, "minimum_should_match": 1}}
        query.setdefault("bool", {}).setdefault("filter", []).append(entity_filter)
        return query

    def _build_cli_insights_tool_docs_query(self, query: dict) -> dict:
        """Build search query for raw CLI tool usage docs."""
        return {
            "bool": {
                "filter": [
                    query,
                    {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}},
                ]
            }
        }

    def _build_cli_insights_user_detail_aggregation(self, query: dict) -> dict:
        """Build detail aggregation for one CLI user."""
        usage_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}}
        session_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_SESSION_TOTAL.value}}
        proxy_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}}
        completed_session_filter = {
            "bool": {
                "filter": [
                    session_filter,
                    {"terms": {SESSION_STATUS_KEYWORD_FIELD: list(SESSION_COMPLETED_STATUSES)}},
                ]
            }
        }
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "user_name": {
                    "top_metrics": {
                        "metrics": {"field": USER_NAME_KEYWORD_FIELD},
                        "size": 1,
                        "sort": {TIMESTAMP_FIELD: "desc"},
                    }
                },
                "user_email": {
                    "top_metrics": {
                        "metrics": {"field": USER_EMAIL_KEYWORD_FIELD},
                        "size": 1,
                        "sort": {TIMESTAMP_FIELD: "desc"},
                    }
                },
                "tool_usage": {
                    "filter": usage_filter,
                    "aggs": {
                        "total_prompts": {"sum": {"field": TOTAL_USER_PROMPTS_FIELD}},
                        "total_commands": {"sum": {"field": TOTAL_TOOL_CALLS_FIELD}},
                        "total_lines_added": {"sum": {"field": TOTAL_LINES_ADDED_FIELD}},
                        "total_lines_removed": {"sum": {"field": TOTAL_LINES_REMOVED_FIELD}},
                        "files_created": {"sum": {"field": FILES_CREATED_FIELD}},
                        "files_modified": {"sum": {"field": FILES_MODIFIED_FIELD}},
                        "files_deleted": {"sum": {"field": FILES_DELETED_FIELD}},
                        "unique_repositories": {"cardinality": {"field": REPOSITORY_KEYWORD_FIELD}},
                        "projects": {"terms": {"field": PROJECT_KEYWORD_FIELD, "size": 100}},
                        "branches": {"terms": {"field": BRANCH_KEYWORD_FIELD, "size": 100}},
                    },
                },
                "session_usage": {
                    "filter": session_filter,
                    "aggs": {
                        "total_sessions": {"cardinality": {"field": SESSION_ID_KEYWORD_FIELD}},
                        "active_days": {
                            "date_histogram": {
                                "field": TIMESTAMP_FIELD,
                                "calendar_interval": "day",
                                "min_doc_count": 1,
                            }
                        },
                    },
                },
                "completed_sessions": {
                    "filter": completed_session_filter,
                    "aggs": {
                        "avg_duration_ms": {"avg": {"field": SESSION_DURATION_MS_FIELD}},
                    },
                },
                "proxy_usage": {
                    "filter": proxy_filter,
                    "aggs": {
                        "total_cost": {"sum": {"field": MONEY_SPENT_FIELD}},
                        "input_tokens": {"sum": {"field": INPUT_TOKENS_FIELD}},
                        "output_tokens": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                        "cache_read_tokens": {"sum": {"field": CACHE_READ_INPUT_TOKENS_FIELD}},
                        "cache_creation_tokens": {"sum": {"field": CACHE_CREATION_TOKENS_FIELD}},
                        "models": {"terms": {"field": LLM_MODEL_KEYWORD_FIELD, "size": 20}},
                    },
                },
                "repositories": {
                    "terms": {"field": REPOSITORY_KEYWORD_FIELD, "size": 1000},
                    "aggs": {
                        "usage": {
                            "filter": usage_filter,
                            "aggs": {
                                "lines_added": {"sum": {"field": TOTAL_LINES_ADDED_FIELD}},
                                "lines_removed": {"sum": {"field": TOTAL_LINES_REMOVED_FIELD}},
                                "branches": {"terms": {"field": BRANCH_KEYWORD_FIELD, "size": 20}},
                                "projects": {"terms": {"field": PROJECT_KEYWORD_FIELD, "size": 10}},
                            },
                        },
                        "sessions": {
                            "filter": session_filter,
                            "aggs": {
                                "count": {"cardinality": {"field": SESSION_ID_KEYWORD_FIELD}},
                            },
                        },
                        "proxy": {
                            "filter": proxy_filter,
                            "aggs": {
                                "total_cost": {"sum": {"field": MONEY_SPENT_FIELD}},
                            },
                        },
                    },
                },
            },
        }

    def _extract_cli_user_detail_core_metrics(
        self,
        *,
        user_name: str,
        aggs: dict,
        start_dt: datetime,
        end_dt: datetime,
    ) -> dict:
        """Extract scalar user detail metrics from aggregation buckets."""
        usage_aggs = aggs.get("tool_usage", {})
        session_aggs = aggs.get("session_usage", {})
        completed_session_aggs = aggs.get("completed_sessions", {})
        proxy_aggs = aggs.get("proxy_usage", {})
        total_sessions = int(session_aggs.get("total_sessions", {}).get("value", 0) or 0)
        total_cost = round(proxy_aggs.get("total_cost", {}).get("value", 0) or 0, 2)
        total_prompts = int(usage_aggs.get("total_prompts", {}).get("value", 0) or 0)
        total_lines_added = int(usage_aggs.get("total_lines_added", {}).get("value", 0) or 0)
        total_lines_removed = int(usage_aggs.get("total_lines_removed", {}).get("value", 0) or 0)
        active_days = len(session_aggs.get("active_days", {}).get("buckets", []))
        avg_session_duration_min = round(
            float(completed_session_aggs.get("avg_duration_ms", {}).get("value", 0) or 0) / 60000,
            2,
        )
        analysis_days = max((end_dt - start_dt).days or 0, 1)
        resolved_user_name = self._extract_top_metric(aggs, "user_name", USER_NAME_KEYWORD_FIELD) or user_name
        return {
            "resolved_user_name": resolved_user_name,
            "user_email": self._extract_top_metric(aggs, "user_email", USER_EMAIL_KEYWORD_FIELD) or resolved_user_name,
            "total_sessions": total_sessions,
            "total_cost": total_cost,
            "total_prompts": total_prompts,
            "total_commands": int(usage_aggs.get("total_commands", {}).get("value", 0) or 0),
            "net_lines": total_lines_added - total_lines_removed,
            "files_created": int(usage_aggs.get("files_created", {}).get("value", 0) or 0),
            "files_modified": int(usage_aggs.get("files_modified", {}).get("value", 0) or 0),
            "files_deleted": int(usage_aggs.get("files_deleted", {}).get("value", 0) or 0),
            "active_days": active_days,
            "avg_session_duration_min": avg_session_duration_min,
            "prompts_per_session": round(total_prompts / total_sessions, 2) if total_sessions else 0.0,
            "est_monthly_20d": round((total_cost / analysis_days) * 20, 2),
            "unique_repositories": int(usage_aggs.get("unique_repositories", {}).get("value", 0) or 0),
            "proxy_aggs": proxy_aggs,
        }

    def _extract_cli_user_detail_collections(self, aggs: dict) -> dict:
        """Extract collection fields used in CLI user detail."""
        usage_aggs = aggs.get("tool_usage", {})
        return {
            "unique_projects": [
                bucket["key"] for bucket in usage_aggs.get("projects", {}).get("buckets", []) if bucket["key"]
            ],
            "branches_used": [
                bucket["key"] for bucket in usage_aggs.get("branches", {}).get("buckets", []) if bucket["key"]
            ],
            "repositories": [
                repo_bucket["key"]
                for repo_bucket in aggs.get("repositories", {}).get("buckets", [])
                if str(repo_bucket.get("key", "")).strip()
            ],
        }

    def _build_cli_user_detail_widgets(
        self,
        *,
        metrics: dict,
        classification: str,
        category_breakdown: list[dict],
        repository_classifications: list[dict],
        tools: list[dict],
        models: list[dict],
        tool_profile: dict,
    ) -> dict:
        """Build widget-ready sections for the CLI user detail modal."""
        return {
            "key_metrics": self._build_cli_user_detail_key_metrics(
                total_cost=metrics["total_cost"],
                est_monthly_20d=metrics["est_monthly_20d"],
                total_sessions=metrics["total_sessions"],
                total_prompts=metrics["total_prompts"],
                net_lines=metrics["net_lines"],
                files_modified=metrics["files_modified"],
                active_days=metrics["active_days"],
                avg_session_duration_min=metrics["avg_session_duration_min"],
            ),
            "tools_chart": self._build_cli_user_detail_tools_chart(tools),
            "models_chart": self._build_cli_user_detail_models_chart(models),
            "workflow_intent_metrics": self._build_cli_user_detail_workflow_metrics(
                primary_intent_label=tool_profile.get("primary_intent_label") or "Unknown",
                intent_scores=tool_profile.get("intent_scores") or {},
            ),
            "classification_metrics": self._build_cli_user_detail_classification_metrics(
                primary_category=classification,
                is_multi_category=len(category_breakdown) > 1,
                category_diversity_score=self._calculate_cli_category_diversity_score(category_breakdown),
                unique_repositories=metrics["unique_repositories"],
            ),
            "category_breakdown_chart": self._build_cli_user_detail_category_breakdown_chart(category_breakdown),
            "repositories_table": self._build_cli_user_detail_repositories_table(repository_classifications),
        }

    def _parse_cli_insights_user_detail_result(
        self,
        user_name: str,
        result: dict,
        tool_docs_result: dict,
        start_dt: datetime,
        end_dt: datetime,
    ) -> dict:
        """Parse CLI user detail aggregation into modal payload."""
        aggs = result.get("aggregations", {})
        metrics = self._extract_cli_user_detail_core_metrics(
            user_name=user_name,
            aggs=aggs,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        collections = self._extract_cli_user_detail_collections(aggs)

        classification, _confidence = self._classify_cli_entity(
            repositories=collections["repositories"],
            branches=collections["branches_used"],
            project_name=collections["unique_projects"][0] if collections["unique_projects"] else None,
            total_cost=metrics["total_cost"],
        )
        repository_classifications = self._build_cli_repository_classifications(
            aggs.get("repositories", {}).get("buckets", []),
        )
        category_breakdown = self._build_cli_category_breakdown(repository_classifications)
        tool_counts = self._extract_cli_tool_counts(tool_docs_result)
        tools = [{"tool_name": name, "usage_count": count} for name, count in tool_counts]
        models = [
            {"model_name": bucket["key"], "count": bucket["doc_count"]}
            for bucket in metrics["proxy_aggs"].get("models", {}).get("buckets", [])
            if bucket["key"]
        ]
        tool_profile = self._build_cli_tool_profile(tool_counts)
        widgets = self._build_cli_user_detail_widgets(
            metrics=metrics,
            classification=classification,
            category_breakdown=category_breakdown,
            repository_classifications=repository_classifications,
            tools=tools,
            models=models,
            tool_profile=tool_profile,
        )

        return {
            "user_name": metrics["resolved_user_name"],
            "user_email": metrics["user_email"],
            "classification": classification,
            "primary_category": classification,
            "total_sessions": metrics["total_sessions"],
            "total_commands": metrics["total_commands"],
            "unique_repositories": metrics["unique_repositories"],
            "total_cost": metrics["total_cost"],
            "total_prompts": metrics["total_prompts"],
            "net_lines": metrics["net_lines"],
            "files_created": metrics["files_created"],
            "files_deleted": metrics["files_deleted"],
            "files_modified": metrics["files_modified"],
            "active_days": metrics["active_days"],
            "avg_session_duration_min": metrics["avg_session_duration_min"],
            "prompts_per_session": metrics["prompts_per_session"],
            "est_monthly_20d": metrics["est_monthly_20d"],
            "is_multi_category": len(category_breakdown) > 1,
            "category_diversity_score": self._calculate_cli_category_diversity_score(category_breakdown),
            "rule_reasons": self._build_cli_rule_reasons(
                repositories=collections["repositories"],
                branches=collections["branches_used"],
                total_cost=metrics["total_cost"],
                total_sessions=metrics["total_sessions"],
                active_days=metrics["active_days"],
                net_lines=metrics["net_lines"],
            ),
            "unique_projects": collections["unique_projects"],
            "branches_used": collections["branches_used"],
            "category_breakdown": category_breakdown,
            "repository_classifications": repository_classifications,
            "tools": tools,
            "models": models,
            "tool_profile": tool_profile,
            **widgets,
        }

    def _build_cli_user_detail_key_metrics(
        self,
        *,
        total_cost: float,
        est_monthly_20d: float,
        total_sessions: int,
        total_prompts: int,
        net_lines: int,
        files_modified: int,
        active_days: int,
        avg_session_duration_min: float,
    ) -> dict:
        """Build widget-ready key metrics section for the CLI user detail modal."""
        return ResponseFormatter.format_summary_response(
            metrics=[
                {
                    "id": "total_cost",
                    "label": TOTAL_COST_LABEL,
                    "type": "number",
                    "value": total_cost,
                    "format": "currency",
                },
                {
                    "id": "est_monthly_20d",
                    "label": "Est. Monthly (20 days)",
                    "type": "number",
                    "value": est_monthly_20d,
                    "format": "currency",
                },
                {
                    "id": "total_sessions",
                    "label": "Sessions",
                    "type": "number",
                    "value": total_sessions,
                    "format": "number",
                },
                {
                    "id": "total_prompts",
                    "label": "Total Prompts",
                    "type": "number",
                    "value": total_prompts,
                    "format": "number",
                },
                {
                    "id": "net_lines",
                    "label": "Net Lines Added",
                    "type": "number",
                    "value": net_lines,
                    "format": "number",
                },
                {
                    "id": "files_modified",
                    "label": "Files Modified",
                    "type": "number",
                    "value": files_modified,
                    "format": "number",
                },
                {
                    "id": "active_days",
                    "label": "Active Days",
                    "type": "number",
                    "value": active_days,
                    "format": "number",
                },
                {
                    "id": "avg_session_duration_min",
                    "label": "Avg Session",
                    "type": "number",
                    "value": round(avg_session_duration_min * 60, 2),
                    "format": "duration",
                },
            ],
            filters_applied={},
            execution_time_ms=0.0,
        )

    def _build_cli_user_detail_workflow_metrics(
        self,
        *,
        primary_intent_label: str,
        intent_scores: dict[str, float],
    ) -> dict:
        """Build widget-ready workflow intent metrics section."""
        scores = list(intent_scores.values())
        total = sum(scores)
        strongest = max(scores) if scores else 0
        signal_strength = round(strongest / total, 4) if total > 0 else 0.0
        return ResponseFormatter.format_summary_response(
            metrics=[
                {
                    "id": "primary_intent",
                    "label": "Primary Intent",
                    "type": "string",
                    "value": primary_intent_label,
                },
                {
                    "id": "signal_strength",
                    "label": "Signal Strength",
                    "type": "number",
                    "value": signal_strength,
                    "format": "percentage",
                },
            ],
            filters_applied={},
            execution_time_ms=0.0,
        )

    def _build_cli_user_detail_classification_metrics(
        self,
        *,
        primary_category: str,
        is_multi_category: bool,
        category_diversity_score: float,
        unique_repositories: int,
    ) -> dict:
        """Build widget-ready classification metrics section."""
        return ResponseFormatter.format_summary_response(
            metrics=[
                {
                    "id": "primary_category",
                    "label": "Primary Category",
                    "type": "string",
                    "value": primary_category,
                },
                {
                    "id": "is_multi_category",
                    "label": "Multi-Category",
                    "type": "string",
                    "value": "Yes" if is_multi_category else "No",
                },
                {
                    "id": "category_diversity_score",
                    "label": "Diversity Score",
                    "type": "number",
                    "value": category_diversity_score,
                    "format": "percentage",
                },
                {
                    "id": "unique_repositories",
                    "label": "Repositories",
                    "type": "number",
                    "value": unique_repositories,
                    "format": "number",
                },
            ],
            filters_applied={},
            execution_time_ms=0.0,
        )

    def _build_cli_user_detail_tools_chart(self, tools: list[dict]) -> dict:
        """Build widget-ready tools chart section."""
        return ResponseFormatter.format_tabular_response(
            columns=[
                {"id": "tool_name", "label": "Tool", "type": "string"},
                {"id": "usage_count", "label": USAGE_COUNT_LABEL, "type": "number", "format": "number"},
            ],
            rows=tools[:8],
            filters_applied={},
            execution_time_ms=0.0,
            page=0,
            per_page=max(len(tools[:8]), 1),
            total_count=len(tools[:8]),
        )

    def _build_cli_user_detail_models_chart(self, models: list[dict]) -> dict:
        """Build widget-ready models chart section."""
        return ResponseFormatter.format_tabular_response(
            columns=[
                {"id": "model_name", "label": "Model", "type": "string"},
                {"id": "count", "label": "Requests", "type": "number", "format": "number"},
            ],
            rows=models[:8],
            filters_applied={},
            execution_time_ms=0.0,
            page=0,
            per_page=max(len(models[:8]), 1),
            total_count=len(models[:8]),
        )

    def _build_cli_user_detail_category_breakdown_chart(self, category_breakdown: list[dict]) -> dict:
        """Build widget-ready category breakdown chart section."""
        rows = [
            {
                "category": item["category"],
                "percentage": item["percentage"] / 100,
                "cost": item["cost"],
                "sessions": item["sessions"],
            }
            for item in category_breakdown
        ]
        return ResponseFormatter.format_tabular_response(
            columns=[
                {"id": "category", "label": "Category", "type": "string"},
                {"id": "percentage", "label": "Share", "type": "number", "format": "percentage"},
                {"id": "cost", "label": "Cost", "type": "number", "format": "currency"},
                {"id": "sessions", "label": "Sessions", "type": "number", "format": "number"},
            ],
            rows=rows,
            filters_applied={},
            execution_time_ms=0.0,
            page=0,
            per_page=max(len(rows), 1),
            total_count=len(rows),
        )

    def _build_cli_user_detail_repositories_table(self, repository_rows: list[dict]) -> dict:
        """Build widget-ready repositories table section."""
        rows = [
            {
                "repository": row["repository"],
                "classification": row["classification"],
                "cost": row["cost"],
                "sessions": row["sessions"],
                "net_lines": row["net_lines"],
                "branches": row.get("branches", []),
            }
            for row in repository_rows
        ]
        return ResponseFormatter.format_tabular_response(
            columns=[
                {"id": "repository", "label": "Repository", "type": "string"},
                {"id": "classification", "label": "Category", "type": "string"},
                {"id": "cost", "label": "Cost", "type": "number", "format": "currency"},
                {"id": "sessions", "label": "Sessions", "type": "number", "format": "number"},
                {"id": "net_lines", "label": NET_LINES_LABEL, "type": "number", "format": "number"},
            ],
            rows=rows,
            filters_applied={},
            execution_time_ms=0.0,
            page=0,
            per_page=max(len(rows), 1),
            total_count=len(rows),
        )

    def _build_cli_repository_classifications(self, repository_buckets: list[dict]) -> list[dict]:
        """Build repository rows for CLI user detail."""
        rows = []
        for bucket in repository_buckets:
            repository = str(bucket.get("key", "")).strip()
            if not repository:
                continue
            usage_aggs = bucket.get("usage", {})
            branch_buckets = usage_aggs.get("branches", {}).get("buckets", [])
            branches = [branch["key"] for branch in branch_buckets if branch["key"]]
            project_buckets = usage_aggs.get("projects", {}).get("buckets", [])
            project_name = project_buckets[0]["key"] if project_buckets else None
            repository_cost = round(bucket.get("proxy", {}).get("total_cost", {}).get("value", 0) or 0, 2)
            total_lines_added = int(usage_aggs.get("lines_added", {}).get("value", 0) or 0)
            total_lines_removed = int(usage_aggs.get("lines_removed", {}).get("value", 0) or 0)
            classification, _confidence = self._classify_cli_entity(
                repositories=[repository],
                branches=branches,
                project_name=project_name,
                total_cost=repository_cost,
            )
            rows.append(
                {
                    "repository": repository,
                    "sessions": int(bucket.get("sessions", {}).get("count", {}).get("value", 0) or 0),
                    "cost": repository_cost,
                    "classification": classification,
                    "net_lines": total_lines_added - total_lines_removed,
                    "branches": branches,
                }
            )
        rows.sort(key=lambda row: (row["cost"], row["sessions"]), reverse=True)
        return rows

    def _build_cli_category_breakdown(self, repository_rows: list[dict]) -> list[dict]:
        """Build category breakdown from classified repositories."""
        grouped: dict[str, dict] = defaultdict(
            lambda: {"category": "", "sessions": 0, "cost": 0.0, "repositories": 0, "percentage": 0.0}
        )
        total_sessions = sum(int(row["sessions"]) for row in repository_rows)
        for row in repository_rows:
            bucket = grouped[row["classification"]]
            bucket["category"] = row["classification"]
            bucket["sessions"] += int(row["sessions"])
            bucket["cost"] += float(row["cost"])
            bucket["repositories"] += 1
        rows = list(grouped.values())
        for row in rows:
            row["cost"] = round(row["cost"], 2)
            row["percentage"] = round((row["sessions"] / total_sessions) * 100, 1) if total_sessions else 0.0
        rows.sort(key=lambda row: (row["sessions"], row["cost"]), reverse=True)
        return rows

    def _calculate_cli_category_diversity_score(self, category_breakdown: list[dict]) -> float:
        """Calculate a simple diversity score from category percentages."""
        if not category_breakdown:
            return 0.0
        total_share = sum((item["percentage"] / 100) ** 2 for item in category_breakdown)
        if total_share <= 0:
            return 0.0
        return round(1 - total_share, 2)

    def _extract_cli_tool_counts(self, tool_docs_result: dict) -> list[tuple[str, int]]:
        """Aggregate tool counts from raw CLI tool usage documents."""
        counts: dict[str, int] = defaultdict(int)
        for hit in tool_docs_result.get("hits", {}).get("hits", []):
            self._merge_cli_tool_counts_from_hit(counts, hit)
        return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))

    def _merge_cli_tool_counts_from_hit(self, counts: dict[str, int], hit: dict) -> None:
        """Merge tool counts from a single raw tool usage hit."""
        attributes = hit.get("_source", {}).get("attributes", {})
        tool_names = attributes.get(TOOL_NAMES_ATTR_KEY) or []
        tool_counts = attributes.get(TOOL_COUNTS_ATTR_KEY) or []

        if isinstance(tool_counts, dict):
            self._merge_cli_dict_tool_counts(counts, tool_names, tool_counts)
            return

        for index, tool_name in enumerate(tool_names):
            normalized_tool_name = self._normalize_cli_tool_name(tool_name)
            if not normalized_tool_name:
                continue
            counts[normalized_tool_name] += self._resolve_cli_tool_count(tool_counts, index)

    def _merge_cli_dict_tool_counts(
        self,
        counts: dict[str, int],
        tool_names: list,
        tool_counts: dict,
    ) -> None:
        """Merge tool counts when a hit stores counts as a dict keyed by tool name."""
        for tool_name in tool_names:
            normalized_tool_name = self._normalize_cli_tool_name(tool_name)
            if not normalized_tool_name:
                continue
            counts[normalized_tool_name] += int(tool_counts.get(normalized_tool_name, 0) or 0)

    def _normalize_cli_tool_name(self, tool_name: str | None) -> str | None:
        """Normalize a tool name for aggregation."""
        if not tool_name:
            return None
        return str(tool_name)

    def _resolve_cli_tool_count(self, tool_counts: dict | list | int | float, index: int) -> int:
        """Resolve tool count from list/scalar fallback formats."""
        if isinstance(tool_counts, list):
            return int((tool_counts[index] if index < len(tool_counts) else 1) or 0)
        if isinstance(tool_counts, int | float):
            return int(tool_counts or 0)
        return 1

    def _build_cli_tool_profile(self, tool_counts: list[tuple[str, int]]) -> dict:
        """Build lightweight tool profile for user detail modal."""
        if not tool_counts:
            return {
                "primary_intent_label": "Unknown",
                "rationale": "No scoped tool usage available for this entity.",
                "top_tools": [],
                "intent_scores": {},
            }

        category_totals = {
            "terminal": 0,
            "read_search": 0,
            "code_changes": 0,
            "planning": 0,
            "agents": 0,
            "other": 0,
        }
        top_tools = [{"name": name, "count": count} for name, count in tool_counts[:8]]

        for tool_name, count in tool_counts:
            normalized = tool_name.strip().lower()
            if any(matcher in normalized for matcher in CODE_CHANGE_TOOL_MATCHERS):
                category_totals["code_changes"] += count
            elif any(matcher in normalized for matcher in TERMINAL_TOOL_MATCHERS):
                category_totals["terminal"] += count
            elif any(matcher in normalized for matcher in READ_SEARCH_TOOL_MATCHERS):
                category_totals["read_search"] += count
            elif any(matcher in normalized for matcher in PLANNING_TOOL_MATCHERS):
                category_totals["planning"] += count
            elif any(matcher in normalized for matcher in AGENT_TOOL_MATCHERS):
                category_totals["agents"] += count
            else:
                category_totals["other"] += count

        intent_scores = {
            "active_development": round(category_totals["code_changes"] * 1.4 + category_totals["terminal"] * 0.8, 2),
            "code_exploration": round(category_totals["read_search"] * 1.2 + category_totals["other"] * 0.2, 2),
            "planning_architecture": round(category_totals["planning"] * 1.5 + category_totals["agents"] * 0.5, 2),
            "advanced_integrations": round(category_totals["agents"] * 1.3 + category_totals["terminal"] * 0.3, 2),
            "debugging_loops": round(category_totals["terminal"] * 0.9 + category_totals["read_search"] * 0.6, 2),
        }
        primary_intent = max(intent_scores, key=intent_scores.get)

        rationale_parts = []
        if category_totals["code_changes"]:
            rationale_parts.append("code changes are present")
        if category_totals["terminal"]:
            rationale_parts.append("terminal activity is substantial")
        if category_totals["read_search"]:
            rationale_parts.append("read/search activity is frequent")
        if category_totals["planning"]:
            rationale_parts.append("planning/task tools are used")
        if category_totals["agents"]:
            rationale_parts.append("agent/skill tools are used")
        rationale = (
            f"Primary signal suggests {primary_intent.replace('_', ' ')} because " + ", ".join(rationale_parts) + "."
            if rationale_parts
            else "Scoped tool usage is limited, so the intent remains uncertain."
        )

        return {
            "primary_intent_label": primary_intent.replace("_", " ").title(),
            "rationale": rationale,
            "top_tools": top_tools,
            "intent_scores": intent_scores,
        }

    def _build_cli_rule_reasons(
        self,
        repositories: list[str],
        branches: list[str],
        total_cost: float,
        total_sessions: int,
        active_days: int,
        net_lines: int,
    ) -> list[str]:
        """Build human-readable deterministic signals for the detail modal."""
        reasons = []
        production_branch_count = sum(
            1
            for branch in branches
            if any(re.search(pattern, branch, re.IGNORECASE) for pattern in PRODUCTION_BRANCH_PATTERNS)
        )
        if total_sessions and active_days:
            reasons.append(f"frequency: {round(total_sessions / max(active_days, 1), 2)} sessions/day")
        if production_branch_count:
            reasons.append(f"production_branches: {production_branch_count}")
        if any("/" in repository for repository in repositories):
            reasons.append(f"multi_repo: {len(repositories)} repos")
        if net_lines > 0:
            reasons.append(f"productivity: +{net_lines} lines")
        if total_cost > 0:
            reasons.append(f"cost: ${total_cost:.2f}")
        return reasons

    def _extract_top_metric(self, bucket: dict, agg_name: str, metric_field: str) -> str | None:
        """Extract top_metrics string value."""
        top_values = bucket.get(agg_name, {}).get("top", [])
        if not top_values:
            return None
        return top_values[0].get("metrics", {}).get(metric_field)

    def _classify_cli_entity(
        self,
        repositories: list[str],
        branches: list[str],
        project_name: str | None,
        total_cost: float,
    ) -> tuple[str, float]:
        """Apply a lightweight deterministic classification for CLI insight widgets."""
        scores = {
            "production": 0.0,
            "learning": 0.0,
            "testing": 0.0,
            "experimental": 0.0,
            "pet_project": 0.0,
        }
        self._score_cli_repositories(scores, repositories)
        self._score_cli_branches(scores, branches)
        self._score_cli_project(scores, project_name)
        self._score_cli_cost(scores, total_cost)
        if not repositories and not branches:
            scores["experimental"] += 1.0

        classification = max(scores, key=scores.get)
        total_score = sum(scores.values())
        confidence = round(scores[classification] / total_score, 2) if total_score else 0.0
        return classification, confidence

    def _score_cli_repositories(self, scores: dict[str, float], repositories: list[str]) -> None:
        """Apply repository-name based classification signals."""
        for repository in repositories:
            repo_lower = repository.lower()
            if any(re.search(pattern, repo_lower) for pattern in LEARNING_REPO_PATTERNS):
                scores["learning"] += 2.0
            if any(re.search(pattern, repo_lower) for pattern in TESTING_REPO_PATTERNS):
                scores["testing"] += 2.0
            if any(re.search(pattern, repo_lower) for pattern in EXPERIMENTAL_REPO_PATTERNS):
                scores["experimental"] += 2.0
            if any(re.search(pattern, repo_lower) for pattern in PET_PROJECT_REPO_PATTERNS):
                scores["pet_project"] += 1.5
            if any(re.search(pattern, repository) for pattern in LOCAL_PATH_PATTERNS):
                scores["experimental"] += 1.5
            if repository.count("/") >= 2 or re.search(r"[A-Z][a-z]+/[a-z0-9._-]+", repository):
                scores["production"] += 1.5

    def _score_cli_branches(self, scores: dict[str, float], branches: list[str]) -> None:
        """Apply branch-name based classification signals."""
        for branch in branches:
            if any(re.search(pattern, branch, re.IGNORECASE) for pattern in PRODUCTION_BRANCH_PATTERNS):
                scores["production"] += 2.0
                continue
            if any(re.search(pattern, branch, re.IGNORECASE) for pattern in NON_PRODUCTION_BRANCH_PATTERNS):
                scores["testing"] += 1.5
                scores["experimental"] += 0.5
                continue
            if branch in {"main", "master", "develop"}:
                scores["production"] += 0.5

    def _score_cli_project(self, scores: dict[str, float], project_name: str | None) -> None:
        """Apply project-name based classification signals."""
        if project_name and self._infer_project_type(project_name) == PROJECT_TYPE_PERSONAL:
            scores["pet_project"] += 1.0

    def _score_cli_cost(self, scores: dict[str, float], total_cost: float) -> None:
        """Apply spend-based classification signals."""
        if total_cost >= 100:
            scores["production"] += 1.5
        elif total_cost >= 20:
            scores["production"] += 0.5
            scores["learning"] += 0.5
        elif total_cost < 5:
            scores["experimental"] += 0.5

    def _infer_project_type(self, project_name: str) -> str:
        """Infer personal vs team project type."""
        if project_name.lower().endswith(PERSONAL_PROJECT_DOMAINS) or "@" in project_name:
            return PROJECT_TYPE_PERSONAL
        return PROJECT_TYPE_TEAM

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

    def _get_cli_insights_weekday_pattern_columns(self) -> list[dict]:
        return [
            {"id": "weekday", "label": "Weekday", "type": "string"},
            {"id": "activity_count", "label": "Activity Count", "type": "number"},
        ]

    def _get_cli_insights_hourly_usage_columns(self) -> list[dict]:
        return [
            {"id": "hour", "label": "Hour", "type": "number"},
            {"id": "activity_count", "label": "Activity Count", "type": "number"},
        ]

    def _get_cli_insights_session_depth_columns(self) -> list[dict]:
        return [
            {"id": "range", "label": "Range", "type": "string"},
            {"id": "count", "label": "Count", "type": "number"},
        ]

    def _get_cli_insights_user_classification_columns(self) -> list[dict]:
        return [
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "user_count", "label": "User Count", "type": "number"},
            {"id": "total_cost", "label": TOTAL_COST_LABEL, "type": "number", "format": "currency"},
            {"id": "avg_cost", "label": "Avg Cost", "type": "number", "format": "currency"},
        ]

    def _get_cli_insights_top_users_by_cost_columns(self) -> list[dict]:
        return [
            {"id": "user_id", "label": "User ID", "type": "string"},
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "user_email", "label": "Email", "type": "string"},
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "total_cost", "label": TOTAL_COST_LABEL, "type": "number", "format": "currency"},
        ]

    def _get_cli_insights_top_spenders_columns(self) -> list[dict]:
        return [
            {"id": "rank", "label": "#", "type": "number"},
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "total_sessions", "label": "Sessions", "type": "number"},
            {"id": "net_lines", "label": NET_LINES_LABEL, "type": "number"},
            {"id": "total_cost", "label": "Cost", "type": "number", "format": "currency"},
        ]

    def _get_cli_insights_all_users_columns(self) -> list[dict]:
        return [
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "total_sessions", "label": "Sessions", "type": "number"},
            {"id": "total_lines_added", "label": "Lines Added", "type": "number"},
            {"id": "total_lines_removed", "label": "Lines Removed", "type": "number"},
            {"id": "net_lines", "label": NET_LINES_LABEL, "type": "number"},
            {"id": "total_cost", "label": "Cost", "type": "number", "format": "currency"},
        ]

    def _get_cli_insights_project_classification_columns(self) -> list[dict]:
        return [
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "project_count", "label": "Project Count", "type": "number"},
            {"id": "total_cost", "label": TOTAL_COST_LABEL, "type": "number", "format": "currency"},
        ]

    def _get_cli_insights_top_projects_by_cost_columns(self) -> list[dict]:
        return [
            {"id": "project_name", "label": "Project", "type": "string"},
            {"id": "project_type", "label": "Project Type", "type": "string"},
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "total_cost", "label": TOTAL_COST_LABEL, "type": "number", "format": "currency"},
        ]
