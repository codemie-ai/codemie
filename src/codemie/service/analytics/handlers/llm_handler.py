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

"""LLM usage analytics handler."""

from __future__ import annotations

import logging
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline

logger = logging.getLogger(__name__)

# Model name mapping: canonical name → list of variants to combine
# This mapping handles cases where the same model is reported with different names
MODEL_NAME_MAPPING: dict[str, list[str]] = {
    "claude-4-5-sonnet": [
        "claude-4-5-sonnet",
        "claude-sonnet-4-5-20250929",
    ],
    "claude-4-5-haiku-20251001": [
        "converse/eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        "converse/global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "converse/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "converse/jp.anthropic.claude-haiku-4-5-20251001-v1:0",
        "claude-haiku-4-5-20251001",
    ],
}


def _combine_model_names(rows: list[dict]) -> list[dict]:
    """
    Combine model name variants in the response rows.

    Takes rows from analytics response and combines variants based on MODEL_NAME_MAPPING.
    For example, if rows contain both "claude-4-5-sonnet" and "claude-sonnet-4-5-20250929",
    they will be combined into a single row under the canonical name "claude-4-5-sonnet"
    with summed total_requests.

    Note: This only combines variants that appear on the same page. Variants on different
    pages will not be combined (per-page combining only).

    Args:
        rows: List of row dicts with 'model_name' and 'total_requests' keys

    Returns:
        List of combined rows, sorted by total_requests descending
    """
    # Build reverse mapping for O(1) lookup: variant_name -> canonical_name
    reverse_mapping: dict[str, str] = {}
    for canonical_name, variants in MODEL_NAME_MAPPING.items():
        for variant in variants:
            reverse_mapping[variant] = canonical_name

    # Combine rows by canonical model name
    combined: dict[str, int] = {}
    for row in rows:
        model_name = row["model_name"]
        canonical_name = reverse_mapping.get(model_name, model_name)  # Use original if not in mapping

        # Sum request counts for all variants of the same canonical name
        combined[canonical_name] = combined.get(canonical_name, 0) + row["total_requests"]

    # Convert back to rows
    result = [{"model_name": name, "total_requests": count} for name, count in combined.items()]

    # Sort by total_requests descending (maintain same sort order as original)
    result.sort(key=lambda x: x["total_requests"], reverse=True)

    logger.debug(
        f"Combined model names: input_rows={len(rows)}, "
        f"output_rows={len(result)}, "
        f"variants_merged={len(rows) - len(result)}"
    )

    return result


class LLMHandler:
    """Handler for LLM usage analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize LLM handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_llms_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get LLMs usage analytics with model name combining."""
        logger.info("Requesting llms-usage analytics with model name aggregation")

        # Get response from pipeline (standard flow)
        response = await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_llms_usage_aggregation(query, fetch_size),
            result_parser=self._parse_llms_usage_result,
            columns=self._get_llms_usage_columns(),
            group_by_field="attributes.llm_model.keyword",
            metric_filters=[
                MetricName.CONVERSATION_ASSISTANT_USAGE.value,
                MetricName.CLI_TOOL_USAGE_TOTAL.value,
                MetricName.CLI_AGENT_USAGE_TOTAL.value,
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

    def _build_llms_usage_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for LLMs usage with fetch-and-slice."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # No sub-aggregations needed - using doc_count for total requests
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

    def _parse_llms_usage_result(self, result: dict) -> list[dict]:
        """Parse result for LLMs usage."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = [{"model_name": bucket["key"], "total_requests": bucket["doc_count"]} for bucket in buckets]
        logger.debug(f"Parsed llms-usage result: total_model_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_llms_usage_columns(self) -> list[dict]:
        """Get column definitions for LLMs usage."""
        return [
            {"id": "model_name", "label": "Model", "type": "string"},
            {"id": "total_requests", "label": "Total Requests", "type": "number"},
        ]
