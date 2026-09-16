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
from codemie.service.analytics.handlers.field_constants import (
    METRIC_NAME_KEYWORD_FIELD,
    USER_EMAIL_KEYWORD_FIELD,
    USER_NAME_KEYWORD_FIELD,
)
from codemie.service.analytics.handlers.user_identity_resolver import UserIdentityResolver
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline

logger = logging.getLogger(__name__)

# Elasticsearch field constants
USER_ID_KEYWORD_FIELD = "attributes.user_id.keyword"
WEBHOOK_ALIAS_KEYWORD_FIELD = "attributes.webhook_alias.keyword"
WEBHOOK_RESOURCE_TYPE_KEYWORD_FIELD = "attributes.resource_type.keyword"
TIMESTAMP_FIELD = "@timestamp"

UNKNOWN_USER_ID = "unknown"

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
        """Get webhooks invocation analytics grouped by user.

        Returns one row per user with total invocations, most recent project, and most recent webhook alias.
        """
        logger.info("Requesting webhooks-invocation analytics")

        result = await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_webhooks_invocation_aggregation(query, fetch_size),
            result_parser=self._parse_webhooks_invocation_result,
            columns=self._get_webhooks_invocation_columns(),
            group_by_field=USER_ID_KEYWORD_FIELD,
            metric_filters=[MetricName.WEBHOOK_INVOCATION_TOTAL.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )
        rows = result.get("data", {}).get("rows", [])
        for row in rows:
            row["user_id"] = (
                row.pop("raw_user_email", None) or row.pop("raw_user_name", None) or row.pop("raw_user_id", None)
            )
        await UserIdentityResolver.resolve_rows(rows, "user_id")
        return result

    def _build_webhooks_invocation_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for webhooks invocation grouped by user."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        sub_aggs = {
            "user_email_top": {
                "top_metrics": {
                    "metrics": {"field": USER_EMAIL_KEYWORD_FIELD},
                    "size": 1,
                    "sort": {TIMESTAMP_FIELD: "desc"},
                }
            },
            "user_name_top": {
                "top_metrics": {
                    "metrics": {"field": USER_NAME_KEYWORD_FIELD},
                    "size": 1,
                    "sort": {TIMESTAMP_FIELD: "desc"},
                }
            },
            AGG_WEBHOOK_FILTER: {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.WEBHOOK_INVOCATION_TOTAL.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                },
                "aggs": {
                    "1-metric": {
                        "top_metrics": {
                            "metrics": {"field": "attributes.project.keyword"},
                            "size": 1,
                            "sort": {TIMESTAMP_FIELD: "desc"},
                        }
                    }
                },
            },
            AGG_ALIAS_FILTER: {
                "filter": {"exists": {"field": WEBHOOK_ALIAS_KEYWORD_FIELD}},
                "aggs": {
                    AGG_ALIAS_METRIC: {
                        "top_metrics": {
                            "metrics": {"field": WEBHOOK_ALIAS_KEYWORD_FIELD},
                            "size": 1,
                            "sort": {TIMESTAMP_FIELD: "desc"},
                        }
                    }
                },
            },
            "3-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.WEBHOOK_INVOCATION_TOTAL.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                },
            },
        }

        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=USER_ID_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"3-bucket": "desc"},
            sub_aggs=sub_aggs,
        )

        return {
            "query": query,
            "size": 0,
            "aggs": {AGG_PAGINATED_RESULTS: terms_agg},
        }

    def _parse_webhooks_invocation_result(self, result: dict) -> list[dict]:
        """Parse result for webhooks invocation (one row per user)."""
        buckets = result.get("aggregations", {}).get(AGG_PAGINATED_RESULTS, {}).get("buckets", [])

        rows = []
        for bucket in buckets:
            raw_user_id = bucket["key"]
            user_email_top = bucket.get("user_email_top", {}).get("top", [])
            raw_user_email = (
                user_email_top[0].get("metrics", {}).get(USER_EMAIL_KEYWORD_FIELD) if user_email_top else None
            )
            user_name_top = bucket.get("user_name_top", {}).get("top", [])
            raw_user_name = user_name_top[0].get("metrics", {}).get(USER_NAME_KEYWORD_FIELD) if user_name_top else None
            if raw_user_id == UNKNOWN_USER_ID:
                continue
            total_invocations = bucket.get("3-bucket", {}).get("doc_count", 0)

            project = None
            top1 = bucket.get(AGG_WEBHOOK_FILTER, {}).get("1-metric", {}).get("top", [])
            if top1:
                project = top1[0].get("metrics", {}).get("attributes.project.keyword")

            webhook_alias = None
            top2 = bucket.get(AGG_ALIAS_FILTER, {}).get(AGG_ALIAS_METRIC, {}).get("top", [])
            if top2:
                webhook_alias = top2[0].get("metrics", {}).get(WEBHOOK_ALIAS_KEYWORD_FIELD)

            rows.append(
                {
                    "raw_user_email": raw_user_email,
                    "raw_user_name": raw_user_name,
                    "raw_user_id": raw_user_id,
                    "project": project or "N/A",
                    "webhook_alias": webhook_alias or "N/A",
                    "total_invocations": total_invocations,
                }
            )

        logger.debug(f"Parsed webhooks-invocation result: total_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_webhooks_invocation_columns(self) -> list[dict]:
        """Get column definitions for webhooks invocation."""
        return [
            {"id": "user_id", "label": "User Email", "type": "string"},
            {"id": "project", "label": "Project", "type": "string"},
            {"id": "webhook_alias", "label": "Webhook Alias", "type": "string"},
            {"id": "total_invocations", "label": "Total Invocations", "type": "number"},
        ]
