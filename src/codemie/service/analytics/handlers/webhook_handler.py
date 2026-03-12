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

"""Webhook analytics handler."""

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
USER_ID_KEYWORD_FIELD = "attributes.user_id.keyword"
WEBHOOK_ALIAS_KEYWORD_FIELD = "attributes.webhook_alias.keyword"
WEBHOOK_RESOURCE_TYPE_KEYWORD_FIELD = "attributes.resource_type.keyword"

# Aggregation name constants
AGG_PAGINATED_RESULTS = "paginated_results"
AGG_WEBHOOK_FILTER = "1-bucket"
AGG_ALIAS_FILTER = "2-bucket"
AGG_ALIAS_METRIC = "2-metric"


class WebhookHandler:
    """Handler for webhook invocation analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize webhook handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_webhooks_invocation(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get webhooks invocation analytics with accurate row-level pagination.

        Returns webhook invocation data as (user, webhook_alias) combinations.
        Each row represents a unique user-webhook pair with its invocation count.
        """
        logger.info("Requesting webhooks-invocation analytics")

        # Use specialized method for nested/flattened aggregations to ensure accurate row-level pagination
        return await self._pipeline.execute_tabular_query_with_flattened_rows(
            agg_builder=lambda query, fetch_size: self._build_webhooks_invocation_aggregation(query, fetch_size),
            result_parser=self._parse_webhooks_invocation_result,
            columns=self._get_webhooks_invocation_columns(),
            flattening_multiplier=20,  # Higher multiplier for many webhook-user combinations
            sort_keys=[
                ("total_invocations", True),  # Primary: Most active first (DESC)
                ("user_id", False),  # Secondary: Alphabetical (ASC)
                ("webhook_alias", False),  # Tertiary: Alphabetical (ASC)
            ],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_webhooks_invocation_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build nested terms aggregation for user and webhook combinations.

        Creates a nested aggregation: user_id -> webhook_alias to get accurate
        invocation counts per (user, webhook) pair. This prevents overcounting
        when a user has invoked multiple different webhooks.
        """
        # Enhance base query to filter for webhook metrics BEFORE aggregation
        # This ensures only users with webhook activity are counted and paginated
        enhanced_query = {
            "bool": {
                "must": [
                    query,  # Original query (time, user, project filters)
                    {
                        "bool": {
                            "should": [
                                {
                                    "term": {
                                        METRIC_NAME_KEYWORD_FIELD: {"value": MetricName.WEBHOOK_INVOCATION_TOTAL.value}
                                    }
                                }
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                ],
                "filter": [{"exists": {"field": WEBHOOK_ALIAS_KEYWORD_FIELD}}],
                "must_not": [{"term": {WEBHOOK_RESOURCE_TYPE_KEYWORD_FIELD: {"value": "verification"}}}],
            }
        }

        # Build nested aggregation: user_id -> webhook_alias
        # Fetch more user buckets to ensure proper pagination after flattening
        # Use larger multipliers to handle many webhook-user combinations
        user_fetch_size = max(fetch_size * 3, 200)
        # Set webhook_fetch_size large enough to capture all webhooks per user
        # For large datasets, this ensures we don't truncate results
        webhook_fetch_size = max(fetch_size * 2, 500)

        # Construct full aggregation body with nested terms
        agg_body = {
            "query": enhanced_query,
            "size": 0,
            "aggs": {
                AGG_PAGINATED_RESULTS: {
                    "terms": {
                        "field": USER_ID_KEYWORD_FIELD,
                        "size": user_fetch_size,
                    },
                    "aggs": {
                        "webhooks": {
                            "terms": {
                                "field": WEBHOOK_ALIAS_KEYWORD_FIELD,
                                "size": webhook_fetch_size,
                                "order": {"_count": "desc"},
                            }
                        }
                    },
                }
            },
        }

        return agg_body

    def _parse_webhooks_invocation_result(self, result: dict) -> list[dict]:
        """Parse nested result and flatten to user-webhook combinations.

        Extracts (user_id, webhook_alias, invocation_count) tuples from the
        nested aggregation structure. Sorting is handled by query_pipeline
        for consistent pagination.
        """
        # Extract user buckets from paginated_results
        user_buckets = result.get("aggregations", {}).get(AGG_PAGINATED_RESULTS, {}).get("buckets", [])

        rows = []
        for user_bucket in user_buckets:
            user_id = user_bucket["key"]
            # Extract nested webhook buckets
            webhook_buckets = user_bucket.get("webhooks", {}).get("buckets", [])

            for webhook_bucket in webhook_buckets:
                rows.append(
                    {
                        "user_id": user_id,
                        "webhook_alias": webhook_bucket["key"],
                        "total_invocations": webhook_bucket["doc_count"],
                    }
                )

        logger.debug(
            f"Parsed webhooks-invocation result: "
            f"total_user_buckets={len(user_buckets)}, "
            f"total_combinations={len(rows)}"
        )
        return rows

    def _get_webhooks_invocation_columns(self) -> list[dict]:
        """Get column definitions for webhooks invocation."""
        return [
            {"id": "user_id", "label": "User", "type": "string"},
            {"id": "total_invocations", "label": "Total Invocations", "type": "number"},
            {"id": "webhook_alias", "label": "Webhook Alias", "type": "string"},
        ]
