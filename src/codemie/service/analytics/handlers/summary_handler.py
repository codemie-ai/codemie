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

"""Summary analytics handler."""

from __future__ import annotations

import logging
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.cli_cost_processor import CLICostAdjustmentMixin
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline
from codemie.service.analytics.time_parser import TimeParser

logger = logging.getLogger(__name__)

# Elasticsearch field constants
INPUT_TOKENS_FIELD = "attributes.input_tokens"
OUTPUT_TOKENS_FIELD = "attributes.output_tokens"
CACHED_INPUT_TOKENS_FIELD = "attributes.cache_read_input_tokens"
MONEY_SPENT_FIELD = "attributes.money_spent"
CLI_REQUEST_FIELD = "attributes.cli_request"


class SummaryHandler(CLICostAdjustmentMixin):
    """Handler for summary metrics: tokens, costs, usage statistics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize summary handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)
        self.repository = repository

    async def get_summaries(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get summary metrics: tokens, costs, usage statistics.

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)

        Returns:
            Summary response with metrics and metadata
        """
        logger.info(f"Requesting summaries. Period={time_period}")

        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)

        # Execute separate query for unique_users first (without metric_name filter)
        unique_users_count = await self._get_unique_users_count(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

        # Query 1: Get all metrics with original dates
        main_result = await self._pipeline.execute_summary_query(
            agg_builder=self._build_summaries_aggregation,
            metrics_builder=lambda result: self._build_summaries_metrics(result, unique_users_count),
            metric_filters=MetricName.to_list_from_group(MetricName.SUMMARY_METRICS),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

        # Query 2: Get CLI costs separately with adjusted dates (using mixin)
        # Example: If querying Jan 1 - Feb 3 (cutoff Feb 2), this returns costs for Feb 2 - Feb 3 only
        cli_costs_result = await self.get_cli_costs_with_adjustment(
            start_dt, end_dt, users, projects, include_cache_costs=False
        )
        cli_cost_adjusted = cli_costs_result["total_cost"]

        # Merge: Replace CLI costs and recalculate platform_cost and total_money_spent
        # Note: Query 1 uses NEW CLI metric but includes all dates.
        # Query 2 returns adjusted CLI cost (respecting cutoff date).
        # We replace the original CLI cost with the adjusted one.
        metrics = main_result["data"]["metrics"]
        cli_cost_original = None
        total_money_original = None
        embedding_cost_value = None

        # Find original values from Query 1
        for metric in metrics:
            if metric["id"] == "cli_cost":
                cli_cost_original = metric["value"]
            elif metric["id"] == "total_money_spent":
                total_money_original = metric["value"]
            elif metric["id"] == "embedding_cost":
                embedding_cost_value = metric["value"]

        # Calculate adjustment (can be negative if original costs were inflated)
        # Example: cli_cost_adjusted=$10 (Feb 2-3 only) - cli_cost_original=$100 (Jan 1 - Feb 3) = -$90
        if cli_cost_original is not None:
            cli_cost_adjustment = cli_cost_adjusted - cli_cost_original

            # Calculate original LLM cost (total - embedding)
            # LLM cost includes both platform and CLI costs
            # Note: use `is not None` — embedding_cost_value can legitimately be 0.0 (falsy)
            original_llm_cost = (
                (total_money_original - embedding_cost_value)
                if total_money_original is not None and embedding_cost_value is not None
                else 0
            )

            # Calculate adjusted LLM cost (remove inflated CLI costs)
            adjusted_llm_cost = original_llm_cost + cli_cost_adjustment

            # Update affected metrics
            for metric in metrics:
                if metric["id"] == "cli_cost":
                    metric["value"] = round(cli_cost_adjusted, 2)
                elif metric["id"] == "platform_cost":
                    # Recalculate platform_cost = adjusted_llm_cost - adjusted_cli_cost
                    metric["value"] = round(adjusted_llm_cost - cli_cost_adjusted, 2)
                elif metric["id"] == "total_money_spent":
                    # Adjust total spending based on CLI cost adjustment
                    metric["value"] = round(total_money_original + cli_cost_adjustment, 2)

        return main_result

    def _build_summaries_aggregation(self, query: dict) -> dict:
        """Build aggregation for summaries (web + NEW CLI metric).

        Note: Excludes OLD CLI metric (CLI_COMMAND_EXECUTION_TOTAL) from total_money_spent
        to avoid double-counting. Uses only NEW CLI metric (CLI_LLM_USAGE_TOTAL) for costs.
        """
        # LLM metrics filter (excludes embeddings)
        llm_metrics_filter = {
            "terms": {
                "metric_name.keyword": [
                    MetricName.CONVERSATION_ASSISTANT_USAGE.value,
                    MetricName.WORKFLOW_EXECUTION_TOTAL.value,
                ]
            }
        }

        return {
            "query": query,
            "size": 0,
            "aggs": {
                # Web LLM metrics (filtered to exclude embeddings)
                "web_llm_tokens": {
                    "filter": llm_metrics_filter,
                    "aggs": {
                        "input_tokens": {"sum": {"field": INPUT_TOKENS_FIELD}},
                        "output_tokens": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                        "cached_input_tokens": {"sum": {"field": CACHED_INPUT_TOKENS_FIELD}},
                    },
                },
                # CLI metrics (use LiteLLM proxy metric with cli_request=true filter)
                "cli_tokens": {
                    "filter": {
                        "bool": {
                            "filter": [
                                {"term": {"metric_name.keyword": MetricName.CLI_LLM_USAGE_TOTAL.value}},
                                {"term": {CLI_REQUEST_FIELD: True}},
                            ]
                        }
                    },
                    "aggs": {
                        "cli_input_tokens": {"sum": {"field": INPUT_TOKENS_FIELD}},
                        "cli_output_tokens": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                        "cli_cached_input_tokens": {"sum": {"field": CACHED_INPUT_TOKENS_FIELD}},
                    },
                },
                # Total money spent (exclude OLD CLI metric to avoid double-counting)
                # Uses NEW CLI metric (CLI_LLM_USAGE_TOTAL) for accurate CLI costs
                "total_money_spent": {
                    "filter": {
                        "bool": {
                            "must_not": [
                                {"term": {"metric_name.keyword": MetricName.CLI_COMMAND_EXECUTION_TOTAL.value}}
                            ]
                        }
                    },
                    "aggs": {
                        "sum": {"sum": {"field": MONEY_SPENT_FIELD}},
                    },
                },
                # Unique counts with metric-specific filters
                "unique_assistants": {
                    "filter": {"term": {"metric_name.keyword": "conversation_assistant_usage"}},
                    "aggs": {
                        "count": {
                            "cardinality": {
                                "field": "attributes.assistant_id.keyword",
                                "precision_threshold": 1000,
                            }
                        }
                    },
                },
                "unique_workflows": {
                    "filter": {"term": {"metric_name.keyword": "workflow_execution_total"}},
                    "aggs": {
                        "count": {
                            "cardinality": {
                                "field": "attributes.workflow_name.keyword",
                                "precision_threshold": 1000,
                            }
                        }
                    },
                },
                # Embedding metrics (filtered to datasource_tokens_usage only)
                "embedding_metrics": {
                    "filter": {"term": {"metric_name.keyword": MetricName.DATASOURCE_TOKENS_USAGE.value}},
                    "aggs": {
                        "input_tokens": {"sum": {"field": INPUT_TOKENS_FIELD}},
                        "money_spent": {"sum": {"field": MONEY_SPENT_FIELD}},
                    },
                },
                # LLM cost (filtered to LLM-related metrics, excluding embeddings)
                "llm_cost": {
                    "filter": {
                        "bool": {
                            "should": [
                                {"term": {"metric_name.keyword": MetricName.CONVERSATION_ASSISTANT_USAGE.value}},
                                {"term": {"metric_name.keyword": MetricName.WORKFLOW_EXECUTION_TOTAL.value}},
                                {
                                    "bool": {
                                        "filter": [
                                            {"term": {"metric_name.keyword": MetricName.CLI_LLM_USAGE_TOTAL.value}},
                                            {"term": {CLI_REQUEST_FIELD: True}},
                                        ]
                                    }
                                },
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                    "aggs": {
                        "money_spent": {"sum": {"field": MONEY_SPENT_FIELD}},
                    },
                },
                # CLI-specific cost (for transparency into CLI operations cost)
                "cli_cost": {
                    "filter": {
                        "bool": {
                            "filter": [
                                {"term": {"metric_name.keyword": MetricName.CLI_LLM_USAGE_TOTAL.value}},
                                {"term": {CLI_REQUEST_FIELD: True}},
                            ]
                        }
                    },
                    "aggs": {
                        "money_spent": {"sum": {"field": MONEY_SPENT_FIELD}},
                    },
                },
            },
        }

    async def _get_unique_users_count(
        self,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> int:
        """Get unique users count across ALL metrics (separate query without metric_name filter).

        This query counts users across all activity types: conversations, workflows,
        tools, webhooks, etc., matching the dashboard behavior.

        Args:
            time_period: Predefined time range
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by users
            projects: Filter by projects

        Returns:
            Count of unique users
        """
        result = await self._pipeline.execute_summary_query(
            agg_builder=lambda query: {
                "query": query,
                "size": 0,
                "aggs": {
                    "unique_users": {
                        "cardinality": {
                            "field": "attributes.user_id.keyword",
                            "precision_threshold": 3000,
                        }
                    }
                },
            },
            metrics_builder=lambda result: result,
            metric_filters=None,  # No metric_name filter - count across all metrics
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

        data_metrics = result.get("data", {}).get("metrics", {})
        unique_users = data_metrics.get("aggregations", {}).get("unique_users", {}).get("value", 0)
        logger.info(f"Unique users count (all metrics): {unique_users}")
        return int(unique_users)

    def _build_summaries_metrics(self, result: dict, unique_users_count: int) -> list[dict]:
        """Build metrics list from ES result.

        Combines web + CLI tokens for LLM operations, excludes embeddings.

        Returns 11 metrics:
        - LLM Tokens (3): total_input_tokens, total_cached_input_tokens, total_output_tokens
        - Embedding Tokens (1): embedding_input_tokens
        - Counts (3): unique_active_users, unique_assistants_invoked, unique_workflows_invoked
        - Money (4): platform_cost, cli_cost, embedding_cost, total_money_spent

        Note: platform_cost = llm_cost - cli_cost (conversation + workflow only, excludes CLI)
        """
        aggs = result.get("aggregations", {})

        # Extract web LLM tokens (filtered to exclude embeddings)
        web_llm_aggs = aggs.get("web_llm_tokens", {})
        web_input = int(web_llm_aggs.get("input_tokens", {}).get("value", 0))
        web_output = int(web_llm_aggs.get("output_tokens", {}).get("value", 0))
        web_cached = int(web_llm_aggs.get("cached_input_tokens", {}).get("value", 0))

        # CLI tokens (from nested cli_tokens bucket)
        cli_tokens_aggs = aggs.get("cli_tokens", {})
        cli_input = int(cli_tokens_aggs.get("cli_input_tokens", {}).get("value", 0))
        cli_output = int(cli_tokens_aggs.get("cli_output_tokens", {}).get("value", 0))
        cli_cached = int(cli_tokens_aggs.get("cli_cached_input_tokens", {}).get("value", 0))

        # Sum web + CLI tokens (LLM only)
        input_tokens = web_input + cli_input
        cached_input_tokens = web_cached + cli_cached
        output_tokens = web_output + cli_output

        # Total money spent (excludes OLD CLI metric, uses NEW CLI metric)
        money_spent = float(aggs.get("total_money_spent", {}).get("sum", {}).get("value", 0.0))

        # Extract unique counts
        unique_users = unique_users_count
        unique_assistants = int(aggs.get("unique_assistants", {}).get("count", {}).get("value", 0))
        unique_workflows = int(aggs.get("unique_workflows", {}).get("count", {}).get("value", 0))

        # Extract embedding metrics
        embedding_aggs = aggs.get("embedding_metrics", {})
        embedding_input_tokens = int(embedding_aggs.get("input_tokens", {}).get("value", 0))
        embedding_money_spent = float(embedding_aggs.get("money_spent", {}).get("value", 0.0))

        # Extract CLI-specific cost
        cli_money_spent = float(aggs.get("cli_cost", {}).get("money_spent", {}).get("value", 0.0))

        # Calculate platform cost (total - cli - embedding, never negative)
        # Derived from total_money_spent rather than llm_cost agg to avoid ES aggregation discrepancies
        platform_money_spent = max(0.0, money_spent - cli_money_spent - embedding_money_spent)

        logger.debug(
            f"Parsed summary metrics: "
            f"llm_input_tokens={input_tokens} (web={web_input}, cli={cli_input}), "
            f"llm_cached_tokens={cached_input_tokens} (web={web_cached}, cli={cli_cached}), "
            f"llm_output_tokens={output_tokens} (web={web_output}, cli={cli_output}), "
            f"embedding_input_tokens={embedding_input_tokens}, "
            f"platform_cost=${platform_money_spent:.2f}, "
            f"cli_cost=${cli_money_spent:.2f}, "
            f"embedding_cost=${embedding_money_spent:.4f}, "
            f"total_money_spent=${money_spent:.2f}, "
            f"unique_users={unique_users}, "
            f"unique_assistants={unique_assistants}, "
            f"unique_workflows={unique_workflows}, "
            f"metrics_built=11"
        )

        return [
            {
                "id": "total_money_spent",
                "label": "Total Money Spent",
                "type": "number",
                "value": round(money_spent, 2),
                "format": "currency",
                "description": "Total cost in USD across all usage types",
            },
            {
                "id": "platform_cost",
                "label": "Platform LLM Cost",
                "type": "number",
                "value": round(platform_money_spent, 2),
                "format": "currency",
                "description": "Total cost for platform LLM operations (conversations, workflows - excluding CLI)",
            },
            {
                "id": "cli_cost",
                "label": "CLI Cost",
                "type": "number",
                "value": round(cli_money_spent, 2),
                "format": "currency",
                "description": "Total cost in USD for CLI operations",
            },
            {
                "id": "embedding_cost",
                "label": "Embedding Cost",
                "type": "number",
                "value": round(embedding_money_spent, 4),
                "format": "currency",
                "description": "Total cost in USD for embedding operations",
            },
            {
                "id": "total_input_tokens",
                "label": "LLM Input Tokens",
                "type": "number",
                "value": input_tokens,
                "format": "number",
                "description": "Total input tokens for LLM operations (conversations, workflows, CLI)",
            },
            {
                "id": "total_cached_input_tokens",
                "label": "LLM Cached Input Tokens",
                "type": "number",
                "value": cached_input_tokens,
                "format": "number",
                "description": "Total cached input tokens (prompt caching) for LLM operations",
            },
            {
                "id": "total_output_tokens",
                "label": "LLM Output Tokens",
                "type": "number",
                "value": output_tokens,
                "format": "number",
                "description": "Total output tokens generated by LLM operations",
            },
            {
                "id": "embedding_input_tokens",
                "label": "Embedding Input Tokens",
                "type": "number",
                "value": embedding_input_tokens,
                "format": "number",
                "description": "Total input tokens used for embedding operations",
            },
            {
                "id": "unique_active_users",
                "label": "Unique Active Users",
                "type": "number",
                "value": unique_users,
                "format": "number",
                "description": "Number of distinct users who have interacted with the system",
            },
            {
                "id": "unique_assistants_invoked",
                "label": "Unique Assistants Invoked",
                "type": "number",
                "value": unique_assistants,
                "format": "number",
                "description": "Number of distinct assistants that have been invoked",
            },
            {
                "id": "unique_workflows_invoked",
                "label": "Unique Workflows Invoked",
                "type": "number",
                "value": unique_workflows,
                "format": "number",
                "description": "Number of distinct workflows that have been executed",
            },
        ]
