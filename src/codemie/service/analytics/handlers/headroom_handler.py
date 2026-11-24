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

"""Headroom analytics handler: aggregations over headroom_compaction_usage events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryFilters, AnalyticsQueryPipeline
from codemie.service.analytics.response_formatter import ResponseFormatter

HEADROOM_METRIC = MetricName.HEADROOM_COMPACTION_USAGE.value
TIMESTAMP_FIELD = "@timestamp"

MODE_KEYWORD = "attributes.mode.keyword"
STRATEGY_KEYWORD = "attributes.strategy.keyword"
MIXED_BUCKET = "mixed"  # label for events merged from several strategies or modes
MODES_AGG_SIZE = 5
CONVERSATION_ID_KEYWORD = "attributes.conversation_id.keyword"
REQUEST_ID_KEYWORD = "attributes.request_id.keyword"

LABEL_TOKENS_ORIGINAL = "Original Tokens"
LABEL_TOKENS_COMPACTED = "Compacted Tokens"
LABEL_TOKENS_SAVED = "Tokens Saved"

_TOKEN_SUMS = {
    "tokens_original": "attributes.tokens_original",
    "tokens_compacted": "attributes.tokens_compacted",
    "tokens_saved": "attributes.tokens_saved",
}


def _token_sum_aggs() -> dict[str, Any]:
    return {name: {"sum": {"field": field}} for name, field in _TOKEN_SUMS.items()}


def _saved_percent(saved: int, original: int) -> float:
    """tokens_saved as a percentage of tokens_original, two decimals; 0 when nothing was original."""
    return round(saved * 100 / original, 2) if original else 0.0


def _int(aggs: dict[str, Any], name: str) -> int:
    return int((aggs.get(name) or {}).get("value") or 0)


class HeadroomHandler:
    """Handler for Headroom analytics: tokens saved overall, by strategy and over time."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    @staticmethod
    def _filters(  # noqa: PLR0913 - same filter and paging arguments as the sibling analytics handlers
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
        page: int = 0,
        per_page: int = 20,
    ) -> AnalyticsQueryFilters:
        return AnalyticsQueryFilters(
            metric_filters=[HEADROOM_METRIC],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_headroom_summary(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return requests, sessions, total tokens, savings rate and the active mode."""
        return await self._pipeline.execute_summary_query(
            agg_builder=self._build_summary_agg,
            metrics_builder=self._parse_summary,
            filters=self._filters(time_period, start_date, end_date, users, projects),
        )

    @staticmethod
    def _build_summary_agg(query: dict[str, Any]) -> dict[str, Any]:
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "session_count": {"cardinality": {"field": CONVERSATION_ID_KEYWORD}},
                "request_count": {"value_count": {"field": REQUEST_ID_KEYWORD}},
                # a deployment runs one mode; a few buckets are enough to tell "one" from "several"
                "modes": {"terms": {"field": MODE_KEYWORD, "size": MODES_AGG_SIZE, "missing": MIXED_BUCKET}},
                **_token_sum_aggs(),
            },
        }

    @staticmethod
    def _parse_summary(result: dict[str, Any]) -> list[dict[str, Any]]:
        aggs = result.get("aggregations", {})
        original = _int(aggs, "tokens_original")
        saved = _int(aggs, "tokens_saved")
        metrics = [
            {"id": "tokens_original", "label": LABEL_TOKENS_ORIGINAL, "type": "number", "value": original},
            {
                "id": "tokens_compacted",
                "label": LABEL_TOKENS_COMPACTED,
                "type": "number",
                "value": _int(aggs, "tokens_compacted"),
            },
            {"id": "tokens_saved", "label": LABEL_TOKENS_SAVED, "type": "number", "value": saved},
            {
                "id": "saved_percent",
                "label": "Saved Share (%)",
                "type": "number",
                "format": "percentage",
                "value": _saved_percent(saved, original),
            },
            {
                "id": "request_count",
                "label": "Compacted Requests",
                "type": "number",
                "value": _int(aggs, "request_count"),
            },
            {"id": "session_count", "label": "Unique Sessions", "type": "number", "value": _int(aggs, "session_count")},
        ]
        mode = HeadroomHandler._active_mode(aggs)
        if mode:
            metrics.append({"id": "mode", "label": "Mode", "type": "string", "value": mode})
        return metrics

    @staticmethod
    def _active_mode(aggs: dict[str, Any]) -> str | None:
        """The one mode seen in the period, `mixed` when several appear (e.g. sdk -> sidecar), None without events."""
        modes = {bucket["key"] for bucket in (aggs.get("modes") or {}).get("buckets", [])}
        if not modes:
            return None
        return modes.pop() if len(modes) == 1 else MIXED_BUCKET

    async def get_savings_by_strategy(  # noqa: PLR0913 - same filter and paging arguments as the sibling analytics handlers
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Return savings grouped by compression strategy (lossless, balanced, aggressive, mixed)."""
        return await self._pipeline.execute_tabular_query(
            agg_builder=self._build_terms_agg,
            result_parser=self._parse_terms_result,
            columns=[
                {"id": "strategy", "label": "Strategy", "type": "string"},
                {"id": "request_count", "label": "Requests", "type": "number"},
                {"id": "tokens_original", "label": LABEL_TOKENS_ORIGINAL, "type": "number"},
                {"id": "tokens_compacted", "label": LABEL_TOKENS_COMPACTED, "type": "number"},
                {"id": "tokens_saved", "label": LABEL_TOKENS_SAVED, "type": "number"},
                {"id": "saved_percent", "label": "Saved Share (%)", "type": "number", "format": "percentage"},
            ],
            group_by_field=STRATEGY_KEYWORD,
            filters=self._filters(time_period, start_date, end_date, users, projects, page, per_page),
        )

    @staticmethod
    def _build_terms_agg(query: dict[str, Any], fetch_size: int) -> dict[str, Any]:
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": {
                    "terms": {
                        "field": STRATEGY_KEYWORD,
                        "size": fetch_size,
                        "order": {"tokens_saved": "desc"},
                        # merged events of differing strategies carry no label: keep them in the report
                        "missing": MIXED_BUCKET,
                    },
                    "aggs": _token_sum_aggs(),
                }
            },
        }

    @staticmethod
    def _parse_terms_result(result: dict[str, Any]) -> list[dict[str, Any]]:
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = []
        for bucket in buckets:
            tokens = {name: _int(bucket, name) for name in _TOKEN_SUMS}
            rows.append(
                {
                    "strategy": bucket["key"],
                    "request_count": bucket["doc_count"],
                    **tokens,
                    "saved_percent": _saved_percent(tokens["tokens_saved"], tokens["tokens_original"]),
                }
            )
        return rows

    async def get_savings_timeseries(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return tokens original, compacted and saved over time."""
        columns = [
            {"id": "time", "label": "Time", "type": "date"},
            {"id": "request_count", "label": "Requests", "type": "number"},
            {"id": "tokens_original", "label": LABEL_TOKENS_ORIGINAL, "type": "number"},
            {"id": "tokens_compacted", "label": LABEL_TOKENS_COMPACTED, "type": "number"},
            {"id": "tokens_saved", "label": LABEL_TOKENS_SAVED, "type": "number"},
        ]

        def result_parser(result: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
            rows = self._parse_timeseries(result)
            response = ResponseFormatter.format_tabular_response(
                rows=rows,
                columns=columns,
                filters_applied=metadata.get("filters_applied", {}),
                execution_time_ms=metadata.get("execution_time_ms", 0),
                totals=None,
            )
            response["pagination"] = {"page": 0, "per_page": len(rows), "total_count": len(rows), "has_more": False}
            return response

        return await self._pipeline.execute_composite_query(
            agg_builder=self._build_timeseries_agg,
            result_parser=result_parser,
            filters=self._filters(time_period, start_date, end_date, users, projects),
        )

    @staticmethod
    def _build_timeseries_agg(query: dict[str, Any]) -> dict[str, Any]:
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
                    "aggs": _token_sum_aggs(),
                }
            },
        }

    @staticmethod
    def _parse_timeseries(result: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for bucket in result.get("aggregations", {}).get("time_buckets", {}).get("buckets", []):
            timestamp = bucket.get("key_as_string")
            if timestamp is None and bucket.get("key") is not None:
                timestamp = datetime.fromtimestamp(bucket["key"] / 1000, tz=UTC).isoformat()
            rows.append(
                {
                    "time": timestamp,
                    "request_count": bucket.get("doc_count", 0),
                    **{name: _int(bucket, name) for name in _TOKEN_SUMS},
                }
            )
        return rows
