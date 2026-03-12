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

"""Embedding model usage analytics handler."""

from __future__ import annotations

import logging
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline

logger = logging.getLogger(__name__)

# Elasticsearch field for embedding model name
EMBEDDINGS_MODEL_KEYWORD_FIELD = "attributes.embeddings_model.keyword"


class EmbeddingsHandler:
    """Handler for embedding model usage analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize embeddings handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_embeddings_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get embedding model usage analytics.

        Returns embedding model usage data grouped by model name with:
        - Input tokens used
        - Total cost in USD
        - Invocation count

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            Tabular response with embedding model usage data
        """
        logger.info("Requesting embeddings-usage analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_embeddings_usage_aggregation(query, fetch_size),
            result_parser=self._parse_embeddings_usage_result,
            columns=self._get_embeddings_usage_columns(),
            group_by_field=EMBEDDINGS_MODEL_KEYWORD_FIELD,
            metric_filters=[MetricName.DATASOURCE_TOKENS_USAGE.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_embeddings_usage_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for embeddings usage with fetch-and-slice."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations for embedding metrics (embeddings only have input tokens)
        sub_aggs = {
            "total_input_tokens": {"sum": {"field": "attributes.input_tokens"}},
            "total_cost": {"sum": {"field": "attributes.money_spent"}},
        }

        # Build terms aggregation using helper
        # Order by total cost descending (most expensive embedding models first)
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=EMBEDDINGS_MODEL_KEYWORD_FIELD,
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

    def _parse_embeddings_usage_result(self, result: dict) -> list[dict]:
        """Parse result for embeddings usage."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        rows = []
        for bucket in buckets:
            model_name = bucket["key"]
            invocation_count = bucket.get("doc_count", 0)
            total_input_tokens = int(bucket.get("total_input_tokens", {}).get("value", 0) or 0)
            total_cost = float(bucket.get("total_cost", {}).get("value", 0.0) or 0.0)

            rows.append(
                {
                    "model_name": model_name,
                    "total_input_tokens": total_input_tokens,
                    "total_cost_usd": round(total_cost, 4),
                    "invocation_count": invocation_count,
                }
            )

        logger.debug(f"Parsed embeddings-usage result: total_model_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_embeddings_usage_columns(self) -> list[dict]:
        """Get column definitions for embeddings usage."""
        return [
            {"id": "model_name", "label": "Embedding Model", "type": "string"},
            {"id": "total_input_tokens", "label": "Input Tokens", "type": "number"},
            {"id": "total_cost_usd", "label": "Total Cost ($)", "type": "number", "format": "currency"},
            {"id": "invocation_count", "label": "Invocations", "type": "number"},
        ]
