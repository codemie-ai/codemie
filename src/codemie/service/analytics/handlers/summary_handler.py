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

import json
import logging
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline

logger = logging.getLogger(__name__)

# Elasticsearch field constants
INPUT_TOKENS_FIELD = "attributes.input_tokens"
OUTPUT_TOKENS_FIELD = "attributes.output_tokens"
CACHED_INPUT_TOKENS_FIELD = "attributes.cache_read_input_tokens"
MONEY_SPENT_FIELD = "attributes.money_spent"
CLI_REQUEST_FIELD = "attributes.cli_request"
METRIC_NAME_KEYWORD_FIELD = "metric_name.keyword"


class SummaryHandler:
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

        # Execute separate query for unique_users first (without metric_name filter)
        unique_users_count = await self._get_unique_users_count(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

        return await self._pipeline.execute_summary_query(
            agg_builder=self._build_summaries_aggregation,
            metrics_builder=lambda result: self._build_summaries_metrics(result, unique_users_count),
            metric_filters=None,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            timestamp_field="time",
        )

    def _build_summaries_aggregation(self, query: dict) -> dict:
        """Build aggregation for summaries (web + NEW CLI metric).

        Note: Excludes OLD CLI metric (CLI_COMMAND_EXECUTION_TOTAL) from total_money_spent
        to avoid double-counting. Uses only NEW CLI metric (CLI_LLM_USAGE_TOTAL) for costs.
        """
        agg_body = {
            "query": query,
            "size": 0,
            "aggs": {
                # All tokens except legacy CLI metric (matches Kibana scope)
                "total_tokens_agg": {
                    "filter": {
                        "bool": {
                            "must_not": [
                                {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_COMMAND_EXECUTION_TOTAL.value}}
                            ]
                        }
                    },
                    "aggs": {
                        "input_tokens": {"sum": {"field": INPUT_TOKENS_FIELD}},
                        "output_tokens": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                        "cache_read_input_tokens": {"sum": {"field": CACHED_INPUT_TOKENS_FIELD}},
                        "cache_creation_tokens": {"sum": {"field": "attributes.cache_creation_tokens"}},
                    },
                },
                # Total money spent (exclude OLD CLI metric to avoid double-counting)
                # Uses NEW CLI metric (CLI_LLM_USAGE_TOTAL) for accurate CLI costs
                "total_money_spent": {
                    "filter": {
                        "bool": {
                            "must_not": [
                                {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_COMMAND_EXECUTION_TOTAL.value}}
                            ]
                        }
                    },
                    "aggs": {
                        "sum": {"sum": {"field": MONEY_SPENT_FIELD}},
                    },
                },
                # Platform LLM cost (conversation + workflow + datasource, matches Kibana formula)
                "platform_llm_cost": {
                    "filter": {
                        "terms": {
                            METRIC_NAME_KEYWORD_FIELD: [
                                MetricName.CONVERSATION_ASSISTANT_USAGE.value,
                                MetricName.WORKFLOW_EXECUTION_TOTAL.value,
                                MetricName.DATASOURCE_TOKENS_USAGE.value,
                            ]
                        }
                    },
                    "aggs": {
                        "money_spent": {"sum": {"field": MONEY_SPENT_FIELD}},
                    },
                },
                # Unique counts with metric-specific filters
                "unique_assistants": {
                    "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: "conversation_assistant_usage"}},
                    "aggs": {"count": {"cardinality": {"field": "attributes.assistant_id.keyword"}}},
                },
                "unique_workflows": {
                    "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: "workflow_execution_total"}},
                    "aggs": {"count": {"cardinality": {"field": "attributes.workflow_name.keyword"}}},
                },
                # Embedding metrics (filtered to datasource_tokens_usage only)
                "embedding_metrics": {
                    "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.DATASOURCE_TOKENS_USAGE.value}},
                    "aggs": {
                        "input_tokens": {"sum": {"field": INPUT_TOKENS_FIELD}},
                        "money_spent": {"sum": {"field": MONEY_SPENT_FIELD}},
                    },
                },
                # CLI-specific cost (for transparency into CLI operations cost)
                "cli_cost": {
                    "filter": {
                        "bool": {
                            "filter": [
                                {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                                {"term": {CLI_REQUEST_FIELD: True}},
                            ]
                        }
                    },
                    "aggs": {
                        "money_spent": {"sum": {"field": MONEY_SPENT_FIELD}},
                    },
                },
                # CLI invocations count (matches Kibana: doc_count of llm_proxy_*_total docs)
                "cli_invoked": {
                    "filter": {
                        "bool": {
                            "filter": [
                                {
                                    "bool": {
                                        "should": [
                                            {"query_string": {"fields": ["metric_name"], "query": "llm_proxy_*_total"}}
                                        ],
                                        "minimum_should_match": 1,
                                    }
                                }
                            ]
                        }
                    }
                },
                # MCPs invoked (matches Kibana: cardinality(mcp_name) for docs with mcp_name field)
                "mcps_invoked": {
                    "filter": {
                        "bool": {
                            "should": [{"exists": {"field": "attributes.mcp_name.keyword"}}],
                            "minimum_should_match": 1,
                        }
                    },
                    "aggs": {"count": {"cardinality": {"field": "attributes.mcp_name.keyword"}}},
                },
                # Webhooks invoked (matches Kibana: value_count(webhook_id) across all docs)
                "webhooks_invoked": {
                    "filter": {"match_all": {}},
                    "aggs": {"count": {"value_count": {"field": "attributes.webhook_id.keyword"}}},
                },
                # Skills invoked (matches Kibana: cardinality(skill_id) across all docs)
                "skills_invoked": {
                    "filter": {"match_all": {}},
                    "aggs": {"count": {"cardinality": {"field": "attributes.skill_id.keyword"}}},
                },
            },
        }
        logger.info(f"Summaries ES query body:\n{json.dumps(agg_body, indent=2, default=str)}")
        return agg_body

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
            timestamp_field="time",
        )

        data_metrics = result.get("data", {}).get("metrics", {})
        unique_users = data_metrics.get("aggregations", {}).get("unique_users", {}).get("value", 0)
        logger.info(f"Unique users count (all metrics): {unique_users}")
        return int(unique_users)

    def _build_summaries_metrics(self, result: dict, unique_users_count: int) -> list[dict]:
        """Build metrics list from ES result.

        Combines web + CLI tokens for LLM operations, excludes embeddings.

        Returns 17 metrics:
        - LLM Tokens (5): total_tokens, total_input_tokens, total_cache_creation_tokens,
                          total_cached_input_tokens, total_output_tokens
        - Embedding Tokens (1): embedding_input_tokens
        - Counts (3): unique_active_users, unique_assistants_invoked, unique_workflows_invoked
        - Money (4): platform_cost, cli_cost, embedding_cost, total_money_spent
        - CLI (1): cli_invoked
        - Integrations (3): mcps_invoked, webhooks_invoked, skills_invoked

        Note: platform_cost = llm_cost - cli_cost (conversation + workflow only, excludes CLI)
        """
        aggs = result.get("aggregations", {})

        # All tokens except legacy CLI metric (matches Kibana scope)
        total_tokens_data = aggs.get("total_tokens_agg", {})
        input_tokens = int(total_tokens_data.get("input_tokens", {}).get("value", 0))
        output_tokens = int(total_tokens_data.get("output_tokens", {}).get("value", 0))
        cached_input_tokens = int(total_tokens_data.get("cache_read_input_tokens", {}).get("value", 0))
        total_cache_creation_tokens = int(total_tokens_data.get("cache_creation_tokens", {}).get("value", 0))
        total_tokens = input_tokens + output_tokens + cached_input_tokens + total_cache_creation_tokens

        # Total money spent (excludes legacy CLI metric, matches Kibana formula)
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

        # Extract new usage counts
        cli_invoked_count = int(aggs.get("cli_invoked", {}).get("doc_count", 0))
        mcps_invoked_count = int(aggs.get("mcps_invoked", {}).get("count", {}).get("value", 0))
        webhooks_invoked_count = int(aggs.get("webhooks_invoked", {}).get("count", {}).get("value", 0))
        skills_invoked_count = int(aggs.get("skills_invoked", {}).get("count", {}).get("value", 0))

        # Platform LLM cost (conversation + workflow + datasource, direct from dedicated agg)
        platform_money_spent = float(aggs.get("platform_llm_cost", {}).get("money_spent", {}).get("value", 0.0))

        logger.debug(
            f"Parsed summary metrics: "
            f"input_tokens={input_tokens}, "
            f"cached_input_tokens={cached_input_tokens}, "
            f"output_tokens={output_tokens}, "
            f"cache_creation_tokens={total_cache_creation_tokens}, "
            f"total_tokens={total_tokens}, "
            f"embedding_input_tokens={embedding_input_tokens}, "
            f"platform_cost=${platform_money_spent:.2f}, "
            f"cli_cost=${cli_money_spent:.2f}, "
            f"embedding_cost=${embedding_money_spent:.4f}, "
            f"total_money_spent=${money_spent:.2f}, "
            f"unique_users={unique_users}, "
            f"unique_assistants={unique_assistants}, "
            f"unique_workflows={unique_workflows}, "
            f"cli_invoked={cli_invoked_count}, "
            f"mcps_invoked={mcps_invoked_count}, "
            f"webhooks_invoked={webhooks_invoked_count}, "
            f"skills_invoked={skills_invoked_count}"
        )

        return [
            {
                "id": "total_money_spent",
                "label": "Total Money Spent",
                "type": "number",
                "value": round(money_spent, 2),
                "format": "currency",
                "description": "Total cost across all usage types",
            },
            {
                "id": "platform_cost",
                "label": "Platform LLM Cost",
                "type": "number",
                "value": round(platform_money_spent, 2),
                "format": "currency",
                "description": "Platform LLM cost (conversations, workflows)",
            },
            {
                "id": "cli_cost",
                "label": "CLI Cost",
                "type": "number",
                "value": round(cli_money_spent, 2),
                "format": "currency",
                "description": "CLI operations cost",
            },
            {
                "id": "total_tokens",
                "label": "Total Tokens",
                "type": "number",
                "value": total_tokens,
                "format": "number",
                "description": "Total LLM tokens",
            },
            {
                "id": "total_input_tokens",
                "label": "LLM Input Tokens",
                "type": "number",
                "value": input_tokens,
                "format": "number",
                "description": "LLM input tokens",
            },
            {
                "id": "total_cached_input_tokens",
                "label": "Total Cache Read Tokens",
                "type": "number",
                "value": cached_input_tokens,
                "format": "number",
                "description": "Cached input tokens (prompt caching)",
            },
            {
                "id": "total_cache_creation_tokens",
                "label": "Total Cache Creation Tokens",
                "type": "number",
                "value": total_cache_creation_tokens,
                "format": "number",
                "description": "Cache creation tokens",
            },
            {
                "id": "total_output_tokens",
                "label": "LLM Output Tokens",
                "type": "number",
                "value": output_tokens,
                "format": "number",
                "description": "LLM output tokens",
            },
            {
                "id": "unique_assistants_invoked",
                "label": "Unique Assistants Invoked",
                "type": "number",
                "value": unique_assistants,
                "format": "number",
                "description": "Distinct assistants invoked",
            },
            {
                "id": "unique_workflows_invoked",
                "label": "Total Workflow Invocations",
                "type": "number",
                "value": unique_workflows,
                "format": "number",
                "description": "Workflow executions",
            },
            {
                "id": "cli_invoked",
                "label": "CLI Invoked",
                "type": "number",
                "value": cli_invoked_count,
                "format": "number",
                "description": "CLI invocations",
            },
            {
                "id": "mcps_invoked",
                "label": "MCPs Invoked",
                "type": "number",
                "value": mcps_invoked_count,
                "format": "number",
                "description": "MCP invocations",
            },
            {
                "id": "webhooks_invoked",
                "label": "Webhooks Invoked",
                "type": "number",
                "value": webhooks_invoked_count,
                "format": "number",
                "description": "Webhook invocations",
            },
            {
                "id": "skills_invoked",
                "label": "Skills Invoked",
                "type": "number",
                "value": skills_invoked_count,
                "format": "number",
                "description": "Skill invocations",
            },
        ]
