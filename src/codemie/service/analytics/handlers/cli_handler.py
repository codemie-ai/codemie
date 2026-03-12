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

import logging
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
from codemie.service.analytics.time_parser import TimeParser

logger = logging.getLogger(__name__)

# Elasticsearch field constants
TIMESTAMP_FIELD = "@timestamp"
REPOSITORY_KEYWORD_FIELD = "attributes.repository.keyword"
SESSION_DURATION_MS_FIELD = "attributes.session_duration_ms"
RESPONSE_STATUS_FIELD = "attributes.response_status"
TOTAL_LINES_ADDED_FIELD = "attributes.total_lines_added"

# Tool usage fields
TOOL_NAMES_FIELD = "attributes.tool_names"
TOOL_COUNTS_FIELD = "attributes.tool_counts"

# Special values
N_A_VALUE = "N/A"


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

        This endpoint provides 18 comprehensive metrics in this order:
        - Token consumption (input, cache creation, cache read, output)
        - User activity (unique users, sessions, repos)
        - Session analytics (invoked, avg/max duration)
        - Proxy health (success rate, failed calls)
        - File changes (created, deleted, modified, net new lines)
        - User prompts

        Note: Cost metrics are excluded from this summary (handled separately with cutoff adjustments).
        """
        logger.info("Requesting cli-summary analytics")

        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)

        # Get all metrics (cost metrics excluded, handled separately in other endpoints)
        return await self._pipeline.execute_summary_query(
            agg_builder=self._build_cli_summary_aggregation,
            metrics_builder=self._parse_cli_summary_result,
            metric_filters=None,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

    def _build_cli_summary_aggregation(self, query: dict) -> dict:
        """Build aggregation for CLI summary metrics with 18 comprehensive metrics.

        Note: We do NOT add metric filter here since each aggregation needs different metric filters.
        Uses two different CLI metrics:
        - cli_usage_filter: Old metric for session/activity data (files, duration, repos, etc.)
        - cli_llm_filter: New LiteLLM proxy metric for token/cost data
        """
        # Session/activity metrics (both old and new metrics for backward compatibility)
        cli_usage_filter = {
            "terms": {
                METRIC_NAME_KEYWORD_FIELD: [
                    MetricName.CLI_TOOL_USAGE_TOTAL.value,
                    MetricName.CLI_COMMAND_EXECUTION_TOTAL.value,
                ]
            }
        }

        # Token/cost metrics (new metric: codemie_litellm_proxy_usage with cli_request=true)
        cli_llm_filter = {
            "bool": {
                "filter": [
                    {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                    {"term": {"attributes.cli_request": True}},
                ]
            }
        }

        proxy_requests_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.LLM_PROXY_REQUESTS_TOTAL.value}}
        proxy_errors_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.LLM_PROXY_ERRORS_TOTAL.value}}

        return {
            "query": query,
            "size": 0,
            "aggs": {
                # 1. Unique Users
                "unique_users": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"count": {"cardinality": {"field": "attributes.user_id.keyword"}}},
                },
                # 2. Unique Sessions
                "unique_sessions": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"count": {"cardinality": {"field": "attributes.session_id.keyword"}}},
                },
                # 3. Unique Repos
                "unique_repos": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"count": {"cardinality": {"field": REPOSITORY_KEYWORD_FIELD}}},
                },
                # 4. Input Tokens (use LiteLLM proxy metric)
                "input_tokens": {
                    "filter": {"bool": {"filter": [cli_llm_filter]}},
                    "aggs": {"total": {"sum": {"field": "attributes.input_tokens"}}},
                },
                # 5. Output Tokens (use LiteLLM proxy metric)
                "output_tokens": {
                    "filter": {"bool": {"filter": [cli_llm_filter]}},
                    "aggs": {"total": {"sum": {"field": "attributes.output_tokens"}}},
                },
                # 6. Cached Tokens Read (use LiteLLM proxy metric)
                "cached_tokens_read": {
                    "filter": {"bool": {"filter": [cli_llm_filter]}},
                    "aggs": {"total": {"sum": {"field": "attributes.cache_read_input_tokens"}}},
                },
                # 6a. Cache Creation Tokens (use LiteLLM proxy metric)
                "cached_creation_tokens": {
                    "filter": {"bool": {"filter": [cli_llm_filter]}},
                    "aggs": {"total": {"sum": {"field": "attributes.cache_creation_tokens"}}},
                },
                # Cost aggregations removed - handled separately with cutoff date adjustments
                # 7. CLI Invoked
                "cli_invoked": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"count": {"value_count": {"field": "attributes.agent.keyword"}}},
                },
                # 11. CLI Avg Session
                "cli_avg_session": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"avg_duration": {"avg": {"field": SESSION_DURATION_MS_FIELD}}},
                },
                # 12. CLI Max Session Duration
                "cli_max_session_duration": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"max_duration": {"max": {"field": SESSION_DURATION_MS_FIELD}}},
                },
                # 13. Proxy Success Rate - Requests count
                "proxy_requests_count": {
                    "filter": {"bool": {"filter": [proxy_requests_filter]}},
                },
                # 13. Proxy Success Rate - Errors count
                "proxy_errors_count": {
                    "filter": {"bool": {"filter": [proxy_errors_filter]}},
                },
                # 14. Proxy Failed Calls
                "proxy_failed_calls": {
                    "filter": {"bool": {"filter": [proxy_errors_filter]}},
                    "aggs": {"count": {"value_count": {"field": RESPONSE_STATUS_FIELD}}},
                },
                # 15. Net New Lines - Lines Added
                "total_lines_added": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"total": {"sum": {"field": TOTAL_LINES_ADDED_FIELD}}},
                },
                # 15. Net New Lines - Lines Removed
                "total_lines_removed": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"total": {"sum": {"field": "attributes.total_lines_removed"}}},
                },
                # 16. Total Created Files
                "total_created_files": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"total": {"sum": {"field": "attributes.files_created"}}},
                },
                # 17. Total Prompts
                "total_prompts": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"total": {"sum": {"field": "attributes.total_user_prompts"}}},
                },
                # 18. Total Deleted Lines (same as lines removed)
                "total_deleted_lines": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"total": {"sum": {"field": "attributes.total_lines_removed"}}},
                },
                # 19. Total Deleted Files
                "total_deleted_files": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"total": {"sum": {"field": "attributes.files_deleted"}}},
                },
                # 20. Total Modified Files
                "total_modified_files": {
                    "filter": {"bool": {"filter": [cli_usage_filter]}},
                    "aggs": {"total": {"sum": {"field": "attributes.files_modified"}}},
                },
            },
        }

    def _parse_cli_summary_result(self, result: dict) -> list[dict]:
        """Parse result for CLI summary metrics with 18 comprehensive metrics."""
        aggs = result.get("aggregations", {})

        # Extract base metrics
        unique_users = int(aggs.get("unique_users", {}).get("count", {}).get("value", 0))
        unique_sessions = int(aggs.get("unique_sessions", {}).get("count", {}).get("value", 0))
        unique_repos = int(aggs.get("unique_repos", {}).get("count", {}).get("value", 0))

        # Extract token metrics
        input_tokens = int(aggs.get("input_tokens", {}).get("total", {}).get("value", 0))
        output_tokens = int(aggs.get("output_tokens", {}).get("total", {}).get("value", 0))
        cached_tokens_read = int(aggs.get("cached_tokens_read", {}).get("total", {}).get("value", 0))
        cached_creation_tokens = int(aggs.get("cached_creation_tokens", {}).get("total", {}).get("value", 0))

        # Cost metrics removed from this summary (handled separately with cutoff adjustments)

        cli_invoked = int(aggs.get("cli_invoked", {}).get("count", {}).get("value", 0))
        cli_avg_session = aggs.get("cli_avg_session", {}).get("avg_duration", {}).get("value", N_A_VALUE)
        cli_max_session = aggs.get("cli_max_session_duration", {}).get("max_duration", {}).get("value", N_A_VALUE)
        proxy_failed_calls = int(aggs.get("proxy_failed_calls", {}).get("count", {}).get("value", 0))
        total_created_files = int(aggs.get("total_created_files", {}).get("total", {}).get("value", 0))
        total_prompts = int(aggs.get("total_prompts", {}).get("total", {}).get("value", 0))
        total_deleted_lines = int(aggs.get("total_deleted_lines", {}).get("total", {}).get("value", 0))
        total_deleted_files = int(aggs.get("total_deleted_files", {}).get("total", {}).get("value", 0))
        total_modified_files = int(aggs.get("total_modified_files", {}).get("total", {}).get("value", 0))

        # Calculate proxy success rate: (total_requests - total_errors) / total_requests
        proxy_requests_count = aggs.get("proxy_requests_count", {}).get("doc_count", 0)
        proxy_errors_count = aggs.get("proxy_errors_count", {}).get("doc_count", 0)
        if proxy_requests_count > 0:
            proxy_success_rate = ((proxy_requests_count - proxy_errors_count) / proxy_requests_count) * 100
        else:
            proxy_success_rate = N_A_VALUE

        # Calculate net new lines: total_lines_added - total_lines_removed
        total_lines_added = int(aggs.get("total_lines_added", {}).get("total", {}).get("value", 0))
        total_lines_removed = int(aggs.get("total_lines_removed", {}).get("total", {}).get("value", 0))
        net_new_lines = total_lines_added - total_lines_removed

        # Convert values and determine types
        cli_avg_session_value = int(cli_avg_session) if cli_avg_session not in (None, N_A_VALUE) else N_A_VALUE
        cli_max_session_value = int(cli_max_session) if cli_max_session not in (None, N_A_VALUE) else N_A_VALUE
        proxy_success_rate_value = (
            round(proxy_success_rate, 2) if proxy_success_rate not in (None, N_A_VALUE) else N_A_VALUE
        )

        metrics = [
            # Token metrics first (in logical order: input → cache creation → cache read → output)
            {"id": "input_tokens", "label": "Input Tokens", "type": "number", "value": input_tokens},
            {
                "id": "cached_creation_tokens",
                "label": "Cache Creation Tokens",
                "type": "number",
                "value": cached_creation_tokens,
            },
            {"id": "cached_tokens_read", "label": "Cache Read Tokens", "type": "number", "value": cached_tokens_read},
            {"id": "output_tokens", "label": "Output Tokens", "type": "number", "value": output_tokens},
            # User activity metrics
            {"id": "unique_users", "label": "Unique Users", "type": "number", "value": unique_users},
            {"id": "unique_sessions", "label": "Unique Sessions", "type": "number", "value": unique_sessions},
            {"id": "unique_repos", "label": "Unique Repositories", "type": "number", "value": unique_repos},
            # Cost metrics removed - handled separately with cutoff date adjustments
            # Session metrics
            {"id": "cli_invoked", "label": "CLI Invoked", "type": "number", "value": cli_invoked},
            {
                "id": "cli_avg_session",
                "label": "CLI Avg Session (ms)",
                "type": "string" if cli_avg_session_value == N_A_VALUE else "number",
                "value": cli_avg_session_value,
            },
            {
                "id": "cli_max_session_duration",
                "label": "CLI Max Session Duration (ms)",
                "type": "string" if cli_max_session_value == N_A_VALUE else "number",
                "value": cli_max_session_value,
            },
            {
                "id": "proxy_success_rate",
                "label": "Proxy Success Rate (%)",
                "type": "string" if proxy_success_rate_value == N_A_VALUE else "number",
                "value": proxy_success_rate_value,
            },
            {"id": "proxy_failed_calls", "label": "Proxy Failed Calls", "type": "number", "value": proxy_failed_calls},
            {"id": "net_new_lines", "label": "Net New Lines", "type": "number", "value": net_new_lines},
            {
                "id": "total_created_files",
                "label": "Total Created Files",
                "type": "number",
                "value": total_created_files,
            },
            {"id": "total_prompts", "label": "Total Prompts", "type": "number", "value": total_prompts},
            {
                "id": "total_deleted_lines",
                "label": "Total Deleted Lines",
                "type": "number",
                "value": total_deleted_lines,
            },
            {
                "id": "total_deleted_files",
                "label": "Total Deleted Files",
                "type": "number",
                "value": total_deleted_files,
            },
            {
                "id": "total_modified_files",
                "label": "Total Modified Files",
                "type": "number",
                "value": total_modified_files,
            },
        ]

        logger.debug(
            f"Parsed cli-summary result: aggregation_keys={list(aggs.keys())}, "
            f"unique_users={unique_users}, unique_sessions={unique_sessions}, unique_repos={unique_repos}, "
            f"cached_creation_tokens={cached_creation_tokens}, "
            f"proxy_success_rate={proxy_success_rate}, net_new_lines={net_new_lines}, "
            f"metrics_built={len(metrics)}"
        )
        return metrics

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
            metric_filters=[
                MetricName.CLI_TOOL_USAGE_TOTAL.value,
                MetricName.CLI_COMMAND_EXECUTION_TOTAL.value,
            ],
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
            {"id": "total_usage", "label": "Usage Count", "type": "number"},
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
                MetricName.CLI_COMMAND_EXECUTION_TOTAL.value,
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
            metric_filters=[
                MetricName.CLI_TOOL_USAGE_TOTAL.value,
                MetricName.CLI_COMMAND_EXECUTION_TOTAL.value,
            ],
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
                MetricName.CLI_COMMAND_EXECUTION_TOTAL.value,  # Legacy CLI session metric
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
        - Session data from CLI_COMMAND_EXECUTION_TOTAL (old metric, has session_duration/lines)
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
                            {"term": {"attributes.cli_request": True}},
                        ]
                    }
                },
                "aggs": {
                    "input_tokens": {"sum": {"field": "attributes.input_tokens"}},
                    "output_tokens": {"sum": {"field": "attributes.output_tokens"}},
                    "cache_read_tokens": {"sum": {"field": "attributes.cache_read_input_tokens"}},
                    "cache_creation_tokens": {"sum": {"field": "attributes.cache_creation_tokens"}},
                },
            },
            # Session data from OLD and NEW metrics (both for backward compatibility)
            "session_data": {
                "filter": {
                    "terms": {
                        METRIC_NAME_KEYWORD_FIELD: [
                            MetricName.CLI_TOOL_USAGE_TOTAL.value,
                            MetricName.CLI_COMMAND_EXECUTION_TOTAL.value,
                        ]
                    }
                },
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
        Extracts token data from CLI_LLM_USAGE_TOTAL and session data from CLI_COMMAND_EXECUTION_TOTAL.
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
            metric_filters=[
                MetricName.CLI_TOOL_USAGE_TOTAL.value,
                MetricName.CLI_COMMAND_EXECUTION_TOTAL.value,
            ],
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
            metric_filters=[
                MetricName.CLI_TOOL_USAGE_TOTAL.value,
                MetricName.CLI_COMMAND_EXECUTION_TOTAL.value,
            ],
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
            {"id": "usage_count", "label": "Usage Count", "type": "number"},
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
            metric_filters=[
                MetricName.CLI_TOOL_USAGE_TOTAL.value,
                MetricName.CLI_COMMAND_EXECUTION_TOTAL.value,
            ],
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
