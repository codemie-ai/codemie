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

"""CLI analytics handler (standard tabular and summary widgets)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import (
    METRIC_NAME_KEYWORD_FIELD,
    PROJECT_KEYWORD_FIELD,
    USER_EMAIL_KEYWORD_FIELD,
)
from codemie.service.analytics.handlers.llm_handler import _combine_model_names
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.time_parser import TimeParser

from .base_handler import CLIBaseHandler
from .constants import (
    CACHE_CREATION_TOKENS_FIELD,
    CACHE_READ_INPUT_TOKENS_FIELD,
    CLI_REQUEST_FIELD,
    INPUT_TOKENS_FIELD,
    OUTPUT_TOKENS_FIELD,
    REPOSITORY_KEYWORD_FIELD,
    RESPONSE_STATUS_FIELD,
    SESSION_DURATION_MS_FIELD,
    SESSION_ID_KEYWORD_FIELD,
    TIMESTAMP_FIELD,
    TOOL_NAMES_FIELD,
    TOTAL_COST_LABEL,
    TOTAL_LINES_ADDED_FIELD,
    USAGE_COUNT_LABEL,
    USER_ID_KEYWORD_FIELD,
)

logger = logging.getLogger(__name__)


class CLIHandler(CLIBaseHandler):
    """Handler for CLI analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository) -> None:
        """Initialize cli handler."""
        super().__init__(user, repository)

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
