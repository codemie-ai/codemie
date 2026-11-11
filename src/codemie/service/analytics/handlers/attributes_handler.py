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

"""Run attributes analytics handler."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from codemie.core.run_attributes import KEYS_FIELD, PAIRS_FIELD, pair_prefix
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.aggregation_builder import AggregationBuilder
from codemie.service.analytics.handlers.field_constants import METRIC_NAME_KEYWORD_FIELD, USER_ID_KEYWORD_FIELD
from codemie.service.analytics.handlers.request_spend import collect_request_usage, price_requests
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryFilters, AnalyticsQueryPipeline, EnrichedRows
from codemie.service.spend.rendering import apply_spend_cells, render_spend, spend_footer

logger = logging.getLogger(__name__)

CONVERSATION_ID_KEYWORD_FIELD = "attributes.conversation_id.keyword"
EXECUTION_ID_KEYWORD_FIELD = "attributes.execution_id.keyword"
REQUEST_ID_KEYWORD_FIELD = "attributes.request_id.keyword"
COST_COLUMN = "total_cost"
INPUT_TOKENS_FIELD = "attributes.input_tokens"
OUTPUT_TOKENS_FIELD = "attributes.output_tokens"
LUCENE_REGEX_SPECIAL_CHARS = frozenset('.?+*|{}[]()"\\#@&<>~^$-')


def escape_lucene_regex(text: str) -> str:
    return "".join(f"\\{char}" if char in LUCENE_REGEX_SPECIAL_CHARS else char for char in text)


def _scoped_to_key(query: dict[str, Any], attribute_key: str) -> dict[str, Any]:
    return {"bool": {"filter": [query, {"term": {KEYS_FIELD: attribute_key}}]}}


def _metric_filter(metric: MetricName) -> dict[str, Any]:
    return {"term": {METRIC_NAME_KEYWORD_FIELD: metric.value}}


def _usage_counts_aggs() -> dict[str, Any]:
    return {
        "conversations": {
            "filter": _metric_filter(MetricName.CONVERSATION_ASSISTANT_USAGE),
            "aggs": {"unique": {"cardinality": {"field": CONVERSATION_ID_KEYWORD_FIELD}}},
        },
        "workflow_executions": {
            "filter": _metric_filter(MetricName.WORKFLOW_EXECUTION_TOTAL),
            "aggs": {"unique": {"cardinality": {"field": EXECUTION_ID_KEYWORD_FIELD}}},
        },
        "last_used": {"max": {"field": "@timestamp"}},
    }


class AttributesHandler:
    """Handler for run attributes analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize attributes handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_attributes_usage(
        self,
        attribute_key: str,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        client_source: str | None = None,
        attributes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Get usage broken down by the values of one run attribute key."""
        logger.info(f"Requesting attributes-usage analytics: attribute_key={attribute_key}")

        filters = AnalyticsQueryFilters(
            metric_filters=MetricName.to_list_from_group(MetricName.USAGE_METRICS),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
            client_source=client_source,
            attributes=attributes,
        )

        return await self._pipeline.execute_enriched_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_attributes_usage_aggregation(
                query, fetch_size, attribute_key
            ),
            result_parser=lambda result: self._parse_attributes_usage_result(result, attribute_key),
            columns=self._get_attributes_usage_columns(),
            group_by_field=PAIRS_FIELD,
            row_enricher=lambda rows, start_dt, end_dt: self._cost_attribute_values(
                rows, start_dt, end_dt, filters, attribute_key
            ),
            filters=filters,
        )

    async def _cost_attribute_values(
        self,
        rows: list[dict[str, Any]],
        start_dt: datetime,
        end_dt: datetime,
        filters: AnalyticsQueryFilters,
        attribute_key: str,
    ) -> EnrichedRows:
        """Each value's cost: the runs carrying it, priced per execution or request id."""
        priced = {}
        if rows:
            prefix = pair_prefix(attribute_key)
            listed = await collect_request_usage(
                self._pipeline.search_aggregation,
                _scoped_to_key(self._pipeline.secure_query(start_dt, end_dt, filters), attribute_key),
                group_field=PAIRS_FIELD,
                id_fields=(EXECUTION_ID_KEYWORD_FIELD, REQUEST_ID_KEYWORD_FIELD),
                cost_bearing={"exists": {"field": INPUT_TOKENS_FIELD}},
            )
            priced = await price_requests(
                {pair[len(prefix) :]: usages for pair, usages in listed.items() if pair and pair.startswith(prefix)}
            )
        rendered = [priced.get(row["value"], render_spend(None)) for row in rows]
        apply_spend_cells(rows, rendered, COST_COLUMN)
        return EnrichedRows(rows=rows, columns=(COST_COLUMN,), totals=spend_footer(COST_COLUMN, rendered))

    def _build_attributes_usage_aggregation(
        self, query: dict[str, Any], fetch_size: int, attribute_key: str
    ) -> dict[str, Any]:
        sub_aggs = {
            **_usage_counts_aggs(),
            "input_tokens": {"sum": {"field": INPUT_TOKENS_FIELD}},
            "output_tokens": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
            "unique_users": {"cardinality": {"field": USER_ID_KEYWORD_FIELD}},
        }
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=PAIRS_FIELD,
            fetch_size=fetch_size,
            order={"_count": "desc"},
            sub_aggs=sub_aggs,
        )
        terms_agg["terms"]["include"] = f"{escape_lucene_regex(pair_prefix(attribute_key))}.*"
        return {"query": _scoped_to_key(query, attribute_key), "size": 0, "aggs": {"paginated_results": terms_agg}}

    @staticmethod
    def _unique_count(bucket: dict[str, Any], name: str) -> int:
        return int(bucket.get(name, {}).get("unique", {}).get("value", 0) or 0)

    @staticmethod
    def _metric_value(bucket: dict[str, Any], name: str) -> float:
        return bucket.get(name, {}).get("value", 0) or 0

    def _parse_attributes_usage_result(self, result: dict[str, Any], attribute_key: str) -> list[dict[str, Any]]:
        prefix = pair_prefix(attribute_key)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = []
        for bucket in buckets:
            pair = bucket["key"]
            if not pair.startswith(prefix):
                continue
            rows.append(
                {
                    "value": pair[len(prefix) :],
                    "conversations": self._unique_count(bucket, "conversations"),
                    "workflow_executions": self._unique_count(bucket, "workflow_executions"),
                    "total_tokens": int(
                        self._metric_value(bucket, "input_tokens") + self._metric_value(bucket, "output_tokens")
                    ),
                    COST_COLUMN: None,
                    "unique_users": int(self._metric_value(bucket, "unique_users")),
                    "last_used": bucket.get("last_used", {}).get("value_as_string"),
                }
            )
        return rows

    def _get_attributes_usage_columns(self) -> list[dict[str, Any]]:
        return [
            {"id": "value", "label": "Attribute Value", "type": "string"},
            {"id": "conversations", "label": "Conversations", "type": "number"},
            {"id": "workflow_executions", "label": "Workflow Executions", "type": "number"},
            {"id": "total_tokens", "label": "Total Tokens", "type": "number"},
            {"id": COST_COLUMN, "label": "Total Cost ($)", "type": "number", "format": "currency"},
            {"id": "unique_users", "label": "Unique Users", "type": "number"},
            {"id": "last_used", "label": "Last Used", "type": "string", "format": "timestamp"},
        ]
