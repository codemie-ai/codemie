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

"""Routing analytics handler — aggregations over routing_call_usage events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from codemie.core.routing_info import normalize_decision_source
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline
from codemie.service.analytics.response_formatter import ResponseFormatter

logger = logging.getLogger(__name__)

ROUTING_METRIC = MetricName.ROUTING_CALL_USAGE.value
TIMESTAMP_FIELD = "@timestamp"
ROUTED_MODEL_LABEL = "Routed Model"

ROUTED_MODEL_KEYWORD = "attributes.routed_model.keyword"
REQUESTED_MODEL_KEYWORD = "attributes.requested_model.keyword"
TIER_KEYWORD = "attributes.tier.keyword"
DECISION_SOURCE_KEYWORD = "attributes.decision_source.keyword"
CONVERSATION_ID_KEYWORD = "attributes.conversation_id.keyword"
REQUEST_ID_KEYWORD = "attributes.request_id.keyword"


class RoutingHandler:
    """Handler for routing analytics — model switches, tier distribution, and classifier overhead."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_decision_timeline(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 50,
    ) -> dict[str, Any]:
        """Return individual routing decisions for the routing timeline chart."""
        return await self._pipeline.execute_search_rows(
            result_parser=self._parse_decision_timeline,
            columns=[
                {"id": "timestamp", "label": "Date", "type": "date"},
                {"id": "routed_model", "label": ROUTED_MODEL_LABEL, "type": "string"},
                {"id": "tier", "label": "Tier", "type": "string"},
                {"id": "decision_source", "label": "Decision Source", "type": "string"},
                {"id": "confidence", "label": "Confidence", "type": "number"},
                {"id": "requested_model", "label": "Requested Model", "type": "string"},
                {"id": "conversation_id", "label": "Conversation", "type": "string"},
                {"id": "request_id", "label": "Request", "type": "string"},
                {"id": "rule", "label": "Rule", "type": "string"},
                {"id": "boundary", "label": "Boundary", "type": "string"},
                {"id": "crux", "label": "Crux", "type": "string"},
            ],
            metric_filters=[ROUTING_METRIC],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    @staticmethod
    def _parse_decision_timeline(result: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for hit in result.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            attributes = source.get("attributes", {})
            row = {
                "timestamp": source.get(TIMESTAMP_FIELD),
                "routed_model": attributes.get("routed_model"),
                "tier": attributes.get("tier"),
                "decision_source": normalize_decision_source(attributes.get("decision_source")),
                "confidence": attributes.get("confidence"),
            }
            for field in ("requested_model", "conversation_id", "request_id", "rule", "boundary", "crux"):
                if attributes.get(field) is not None:
                    row[field] = attributes[field]
            rows.append(row)
        return rows

    async def get_routing_summary(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return session count, request count, and total classifier cost."""
        return await self._pipeline.execute_summary_query(
            agg_builder=self._build_routing_summary_agg,
            metrics_builder=self._parse_routing_summary,
            metric_filters=[ROUTING_METRIC],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

    async def get_routing_activity(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return routing request volume over time, stacked by routing tier."""
        columns = [
            {"id": "time", "label": "Time", "type": "date"},
            {"id": "simple_requests", "label": "Simple", "type": "number"},
            {"id": "medium_requests", "label": "Medium", "type": "number"},
            {"id": "complex_requests", "label": "Complex", "type": "number"},
            {"id": "reasoning_requests", "label": "Reasoning", "type": "number"},
        ]

        def result_parser(result: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
            rows = self._parse_routing_activity(result)
            response = ResponseFormatter.format_tabular_response(
                rows=rows,
                columns=columns,
                filters_applied=metadata.get("filters_applied", {}),
                execution_time_ms=metadata.get("execution_time_ms", 0),
                totals=None,
            )
            response["pagination"] = {
                "page": 0,
                "per_page": len(rows),
                "total_count": len(rows),
                "has_more": False,
            }
            return response

        return await self._pipeline.execute_composite_query(
            agg_builder=self._build_routing_activity_agg,
            result_parser=result_parser,
            metric_filters=[ROUTING_METRIC],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

    def _build_routing_activity_agg(self, query: dict[str, Any]) -> dict[str, Any]:
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "time_buckets": {
                    "date_histogram": {
                        "field": TIMESTAMP_FIELD,
                        "fixed_interval": "3h",
                        "order": {"_key": "asc"},
                    },
                    "aggs": {
                        "tiers": {
                            "terms": {"field": TIER_KEYWORD, "size": 20},
                        }
                    },
                }
            },
        }

    @staticmethod
    def _parse_routing_activity(result: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for bucket in result.get("aggregations", {}).get("time_buckets", {}).get("buckets", []):
            timestamp = bucket.get("key_as_string")
            if timestamp is None and bucket.get("key") is not None:
                timestamp = datetime.fromtimestamp(bucket["key"] / 1000, tz=timezone.utc).isoformat()
            row = {
                "time": timestamp,
                "simple_requests": 0,
                "medium_requests": 0,
                "reasoning_requests": 0,
                "complex_requests": 0,
            }
            for tier_bucket in bucket.get("tiers", {}).get("buckets", []):
                tier = tier_bucket.get("key")
                if tier not in {"simple", "medium", "reasoning", "complex"}:
                    continue
                field = f"{tier}_requests"
                row[field] += tier_bucket.get("doc_count", 0)
            rows.append(row)
        return rows

    async def get_routing_paths(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Return grouped router, routed-model, and tier paths."""
        return await self._pipeline.execute_tabular_query_with_flattened_rows(
            agg_builder=self._build_routing_paths_agg,
            result_parser=self._parse_routing_paths,
            columns=[
                {"id": "router", "label": "Router", "type": "string"},
                {"id": "routed_model", "label": ROUTED_MODEL_LABEL, "type": "string"},
                {"id": "tier", "label": "Tier", "type": "string"},
                {"id": "request_count", "label": "Requests", "type": "number"},
                {"id": "actual_cost_usd", "label": "Actual Cost ($)", "type": "number", "format": "currency"},
                {
                    "id": "estimated_max_cost_usd",
                    "label": "Estimated Max Cost ($)",
                    "type": "number",
                    "format": "currency",
                },
                {
                    "id": "potential_savings_usd",
                    "label": "Potential Savings ($)",
                    "type": "number",
                    "format": "currency",
                },
            ],
            flattening_multiplier=10,
            sort_keys=[("request_count", True), ("router", False), ("routed_model", False)],
            metric_filters=[ROUTING_METRIC],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    @staticmethod
    def _build_routing_paths_agg(query: dict[str, Any], fetch_size: int) -> dict[str, Any]:
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "routers": {
                    "terms": {"field": REQUESTED_MODEL_KEYWORD, "size": fetch_size, "order": {"_count": "desc"}},
                    "aggs": {
                        "models": {
                            "terms": {"field": ROUTED_MODEL_KEYWORD, "size": fetch_size, "order": {"_count": "desc"}},
                            "aggs": {
                                "tiers": {
                                    "terms": {"field": TIER_KEYWORD, "size": fetch_size, "order": {"_count": "desc"}},
                                    "aggs": {
                                        "actual_cost": {"sum": {"field": "attributes.original_cost_usd"}},
                                        "estimated_max_cost": {"sum": {"field": "attributes.estimated_max_cost_usd"}},
                                        "potential_savings": {"sum": {"field": "attributes.potential_savings_usd"}},
                                    },
                                }
                            },
                        }
                    },
                }
            },
        }

    @staticmethod
    def _parse_routing_paths(result: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for router_bucket in result.get("aggregations", {}).get("routers", {}).get("buckets", []):
            for model_bucket in router_bucket.get("models", {}).get("buckets", []):
                for tier_bucket in model_bucket.get("tiers", {}).get("buckets", []):
                    rows.append(
                        {
                            "router": router_bucket.get("key"),
                            "routed_model": model_bucket.get("key"),
                            "tier": tier_bucket.get("key"),
                            "request_count": tier_bucket.get("doc_count", 0),
                            "actual_cost_usd": RoutingHandler._normalize_cost(
                                tier_bucket.get("actual_cost", {}).get("value")
                            ),
                            "estimated_max_cost_usd": RoutingHandler._normalize_cost(
                                tier_bucket.get("estimated_max_cost", {}).get("value")
                            ),
                            "potential_savings_usd": RoutingHandler._normalize_cost(
                                tier_bucket.get("potential_savings", {}).get("value")
                            ),
                        }
                    )
        return rows

    @staticmethod
    def _normalize_cost(value: Any) -> float:
        numeric_value = float(value or 0.0)
        return 0.0 if round(numeric_value, 2) == 0 else numeric_value

    def _build_routing_summary_agg(self, query: dict[str, Any]) -> dict[str, Any]:
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "session_count": {"cardinality": {"field": CONVERSATION_ID_KEYWORD}},
                "request_count": {"value_count": {"field": REQUEST_ID_KEYWORD}},
                "total_classifier_cost": {"sum": {"field": "attributes.classifier_cost_usd"}},
                "total_original_cost": {"sum": {"field": "attributes.original_cost_usd"}},
                "total_estimated_max_cost": {"sum": {"field": "attributes.estimated_max_cost_usd"}},
                "total_potential_savings": {"sum": {"field": "attributes.potential_savings_usd"}},
                "decision_source_counts": {"terms": {"field": DECISION_SOURCE_KEYWORD, "size": 10}},
            },
        }

    def _parse_routing_summary(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        aggs = result.get("aggregations", {})
        decision_source_counts: dict[str, int] = {}
        for bucket in aggs.get("decision_source_counts", {}).get("buckets", []):
            source = normalize_decision_source(str(bucket.get("key", ""))) or ""
            source_key = source.replace("-", "_")
            if source_key == "heuristic_scorer":
                source_key = "heuristic"
            decision_source_counts[source_key] = decision_source_counts.get(source_key, 0) + int(
                bucket.get("doc_count", 0)
            )
        return [
            {
                "id": "total_original_cost",
                "label": "Total Original Cost ($)",
                "type": "number",
                "format": "currency",
                "value": aggs.get("total_original_cost", {}).get("value") or 0.0,
            },
            {
                "id": "total_classifier_cost",
                "label": "Total Classifier Cost ($)",
                "type": "number",
                "format": "currency",
                "value": aggs.get("total_classifier_cost", {}).get("value") or 0.0,
            },
            {
                "id": "total_estimated_max_cost",
                "label": "Total Estimated Max Cost ($)",
                "type": "number",
                "format": "currency",
                "value": aggs.get("total_estimated_max_cost", {}).get("value") or 0.0,
            },
            {
                "id": "total_potential_savings",
                "label": "Total Potential Savings ($)",
                "type": "number",
                "format": "currency",
                "value": aggs.get("total_potential_savings", {}).get("value") or 0.0,
            },
            {
                "id": "request_count",
                "label": "Total Requests",
                "type": "number",
                "value": int(aggs.get("request_count", {}).get("value") or 0),
            },
            {
                "id": "session_count",
                "label": "Unique Sessions",
                "type": "number",
                "value": aggs.get("session_count", {}).get("value", 0),
            },
            {
                "id": "llm_classifier_count",
                "label": "LLM Classifier Decisions",
                "type": "number",
                "value": decision_source_counts.get("llm_classifier", 0),
            },
            {
                "id": "heuristic_count",
                "label": "Heuristic Decisions",
                "type": "number",
                "value": decision_source_counts.get("heuristic", 0),
            },
        ]

    async def get_model_switches(
        self,
        session_id: str | None = None,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Return ordered model-switch timeline (optionally filtered by session_id)."""
        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_model_switches_agg(query, fetch_size, session_id),
            result_parser=self._parse_model_switches,
            columns=[
                {"id": "timestamp", "label": "Date", "type": "date"},
                {"id": "routed_model", "label": ROUTED_MODEL_LABEL, "type": "string"},
                {"id": "request_count", "label": "Requests", "type": "number"},
            ],
            group_by_field=ROUTED_MODEL_KEYWORD,
            metric_filters=[ROUTING_METRIC],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_model_switches_agg(
        self, query: dict[str, Any], fetch_size: int, session_id: str | None
    ) -> dict[str, Any]:
        effective_query = query
        if session_id is not None:
            effective_query = {"bool": {"must": [query, {"term": {CONVERSATION_ID_KEYWORD: session_id}}]}}
        return {
            "query": effective_query,
            "size": 0,
            "aggs": {
                "paginated_results": {
                    "date_histogram": {
                        "field": TIMESTAMP_FIELD,
                        "calendar_interval": "day",
                        "min_doc_count": 1,
                        "order": {"_key": "asc"},
                    },
                    "aggs": {
                        "models": {
                            "terms": {"field": ROUTED_MODEL_KEYWORD, "size": fetch_size},
                        }
                    },
                }
            },
        }

    def _parse_model_switches(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        timeline_buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows: list[dict[str, Any]] = []
        for bucket in timeline_buckets:
            date_str = bucket.get("key_as_string", str(bucket.get("key", "")))
            for model_bucket in bucket.get("models", {}).get("buckets", []):
                rows.append(
                    {
                        "timestamp": date_str,
                        "routed_model": model_bucket["key"],
                        "request_count": model_bucket["doc_count"],
                    }
                )
        return rows

    async def get_tier_distribution(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Return request count by routing tier."""
        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_terms_agg(query, fetch_size, TIER_KEYWORD),
            result_parser=lambda result: self._parse_terms_result(result, "tier"),
            columns=[
                {"id": "tier", "label": "Tier", "type": "string"},
                {"id": "request_count", "label": "Requests", "type": "number"},
            ],
            group_by_field=TIER_KEYWORD,
            metric_filters=[ROUTING_METRIC],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_routed_model_distribution(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Return request count by concrete routed model."""
        return await self._get_distribution(
            field=ROUTED_MODEL_KEYWORD,
            key_name="routed_model",
            label=ROUTED_MODEL_LABEL,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_requested_model_distribution(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Return request count by requested router alias or direct model."""
        return await self._get_distribution(
            field=REQUESTED_MODEL_KEYWORD,
            key_name="requested_model",
            label="Router / Requested Model",
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def _get_distribution(
        self,
        *,
        field: str,
        key_name: str,
        label: str,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
        page: int,
        per_page: int,
    ) -> dict[str, Any]:
        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_terms_agg(query, fetch_size, field),
            result_parser=lambda result: self._parse_terms_result(result, key_name),
            columns=[
                {"id": key_name, "label": label, "type": "string"},
                {"id": "request_count", "label": "Requests", "type": "number"},
            ],
            group_by_field=field,
            metric_filters=[ROUTING_METRIC],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_decision_source_distribution(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Return request count by decision source (classifier, heuristic, fallback, etc.)."""
        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_terms_agg(query, fetch_size, DECISION_SOURCE_KEYWORD),
            result_parser=lambda result: self._parse_terms_result(result, "decision_source"),
            columns=[
                {"id": "decision_source", "label": "Decision Source", "type": "string"},
                {"id": "request_count", "label": "Requests", "type": "number"},
            ],
            group_by_field=DECISION_SOURCE_KEYWORD,
            metric_filters=[ROUTING_METRIC],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    def _build_terms_agg(self, query: dict[str, Any], fetch_size: int, field: str) -> dict[str, Any]:
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": {
                    "terms": {"field": field, "size": fetch_size},
                }
            },
        }

    def _parse_terms_result(self, result: dict[str, Any], key_name: str) -> list[dict[str, Any]]:
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        if key_name not in {"tier", "decision_source"}:
            return [{key_name: b["key"], "request_count": b["doc_count"]} for b in buckets]

        totals: dict[str, int] = {}
        for bucket in buckets:
            raw_key = str(bucket["key"])
            key = raw_key if key_name == "tier" else normalize_decision_source(raw_key)
            if key is not None:
                totals[key] = totals.get(key, 0) + int(bucket["doc_count"])
        return [{key_name: key, "request_count": count} for key, count in totals.items()]

    async def get_classifier_overhead(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return total classifier cost and token totals."""
        return await self._pipeline.execute_summary_query(
            agg_builder=self._build_classifier_overhead_agg,
            metrics_builder=self._parse_classifier_overhead,
            metric_filters=[ROUTING_METRIC],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

    def _build_classifier_overhead_agg(self, query: dict[str, Any]) -> dict[str, Any]:
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "total_classifier_cost": {"sum": {"field": "attributes.classifier_cost_usd"}},
                "total_input_tokens": {"sum": {"field": "attributes.classifier_input_tokens"}},
                "total_output_tokens": {"sum": {"field": "attributes.classifier_output_tokens"}},
                "total_cached_tokens": {"sum": {"field": "attributes.classifier_cached_tokens"}},
            },
        }

    def _parse_classifier_overhead(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        aggs = result.get("aggregations", {})
        return [
            {
                "id": "total_classifier_cost",
                "label": "Classifier Cost",
                "type": "currency",
                "value": aggs.get("total_classifier_cost", {}).get("value") or 0.0,
            },
            {
                "id": "total_input_tokens",
                "label": "Classifier Input Tokens",
                "type": "number",
                "value": int(aggs.get("total_input_tokens", {}).get("value") or 0),
            },
            {
                "id": "total_output_tokens",
                "label": "Classifier Output Tokens",
                "type": "number",
                "value": int(aggs.get("total_output_tokens", {}).get("value") or 0),
            },
            {
                "id": "total_cached_tokens",
                "label": "Classifier Cached Tokens",
                "type": "number",
                "value": int(aggs.get("total_cached_tokens", {}).get("value") or 0),
            },
        ]
