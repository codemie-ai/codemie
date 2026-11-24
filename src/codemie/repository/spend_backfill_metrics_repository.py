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

"""Elasticsearch reads and writes of the spend backfill job: unsettled metric documents and their ledger fields."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

from elastic_transport import TransportError
from elasticsearch import ApiError, AsyncElasticsearch

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs.config import config

logger = logging.getLogger(__name__)

_MONEY_FIELDS = ("original_cost_usd", "estimated_max_cost_usd", "potential_savings_usd", "classifier_cost_usd")

LEDGER_ATTRIBUTES_MAPPING: dict[str, dict[str, Any]] = {
    "ledger_settlement": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
    "ledger_pending_reason": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
    "ledger_settlement_version": {"type": "long"},
    "ledger_rows": {"type": "long"},
    "ledger_settled_at": {"type": "date"},
    "ledger_spend_usd": {"type": "float"},
    "ledger_attempts": {"type": "float"},
    **{field: {"type": "float"} for field in _MONEY_FIELDS},
    **{f"{field}_estimated": {"type": "float"} for field in _MONEY_FIELDS},
}

_SETTLEMENT_VERSION_FIELD = "attributes.ledger_settlement_version"
_SCAN_SORT = [{"@timestamp": "asc"}, {"_shard_doc": "asc"}]
_SCAN_KEEP_ALIVE = "2m"
_MAPPING_REQUEST_TIMEOUT = 60
# Lets a point-in-time still be closed after the scan's timeout is spent.
_CLOSE_PIT_MIN_TIMEOUT = 5.0
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ES_ERRORS = (ApiError, TransportError, asyncio.TimeoutError)


class MetricsIndexUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class MetricDoc:
    index: str
    id: str
    seq_no: int
    primary_term: int
    timestamp: datetime
    sort_values: list[Any]
    attributes: dict[str, Any]


@dataclass(frozen=True)
class UnsettledDocsQuery:
    """A page of `metric_name` documents in the time range, never settled or settled before `settlement_version`."""

    metric_name: str
    start: datetime
    end: datetime
    is_end_inclusive: bool
    projects: Sequence[str]
    settlement_version: int
    source_fields: Sequence[str]
    size: int
    search_after: list[Any] | None


@dataclass(frozen=True)
class VersionCheckedUpdate:
    """A partial `attributes` update that fails with 409 if the document changed since it was read."""

    index: str
    id: str
    seq_no: int
    primary_term: int
    attributes: dict[str, Any]


@dataclass(frozen=True)
class UpdateStatus:
    status: int
    error_type: str | None = None
    reason: str | None = None


class SpendBackfillMetricsRepository:
    def __init__(self) -> None:
        self._index = config.ELASTIC_METRICS_INDEX
        self._client: AsyncElasticsearch | None = (
            ElasticSearchClient.get_async_client() if ElasticSearchClient.is_configured() else None
        )

    @asynccontextmanager
    async def open_point_in_time(self, *, timeout: float) -> AsyncIterator[str]:
        """Yields a point-in-time id; `timeout` covers the whole block, and closing gets what is left of it."""
        deadline = monotonic() + timeout
        pit_id = await self._request_point_in_time(timeout)
        try:
            yield pit_id
        finally:
            await self._close_point_in_time_quietly(pit_id, deadline - monotonic())

    async def find_unsettled_docs(self, query: UnsettledDocsQuery, *, pit_id: str, timeout: float) -> list[MetricDoc]:
        """Returns one page, oldest first, of documents never settled or settled by an older version."""
        body: dict[str, Any] = {
            "query": _build_unsettled_query(query),
            "size": query.size,
            "_source": list(query.source_fields),
            "pit": {"id": pit_id, "keep_alive": _SCAN_KEEP_ALIVE},
            "sort": _SCAN_SORT,
            "seq_no_primary_term": True,
            "track_total_hits": False,
        }
        if query.search_after is not None:
            body["search_after"] = query.search_after
        client = self._require_client()
        try:
            result = await client.search(body=body, allow_partial_search_results=False, request_timeout=timeout)
        except _ES_ERRORS as e:
            raise MetricsIndexUnavailableError(f"search failed: {e}") from e
        _raise_if_incomplete(result)
        return [_parse_doc(hit) for hit in result["hits"]["hits"]]

    async def update_docs(self, updates: Sequence[VersionCheckedUpdate], timeout: float) -> list[UpdateStatus]:
        """Returns the statuses in input order."""
        if not updates:
            return []
        operations: list[dict[str, Any]] = []
        for update in updates:
            operations.append(
                {
                    "update": {
                        "_index": update.index,
                        "_id": update.id,
                        "if_seq_no": update.seq_no,
                        "if_primary_term": update.primary_term,
                    }
                }
            )
            operations.append({"doc": {"attributes": update.attributes}})
        client = self._require_client()
        try:
            result = await client.bulk(operations=operations, refresh=False, request_timeout=timeout)
        except _ES_ERRORS as e:
            raise MetricsIndexUnavailableError(f"bulk update failed: {e}") from e
        return [_parse_update_status(item["update"]) for item in result["items"]]

    async def apply_ledger_mapping(self) -> None:
        client = self._require_client()
        try:
            await client.indices.put_mapping(
                index=self._index,
                properties={"attributes": {"properties": LEDGER_ATTRIBUTES_MAPPING}},
                request_timeout=_MAPPING_REQUEST_TIMEOUT,
            )
        except _ES_ERRORS as e:
            raise MetricsIndexUnavailableError(f"put mapping failed: {e}") from e

    def _require_client(self) -> AsyncElasticsearch:
        if self._client is None:
            raise MetricsIndexUnavailableError("Elasticsearch is not configured")
        return self._client

    async def _request_point_in_time(self, timeout: float) -> str:
        client = self._require_client()
        try:
            result = await client.open_point_in_time(
                index=self._index,
                keep_alive=_SCAN_KEEP_ALIVE,
                allow_partial_search_results=False,
                request_timeout=timeout,
            )
        except _ES_ERRORS as e:
            raise MetricsIndexUnavailableError(f"open point-in-time failed: {e}") from e
        pit_id = result["id"]
        try:
            _raise_if_incomplete(result)
        except MetricsIndexUnavailableError:
            await self._close_point_in_time_quietly(pit_id, timeout)
            raise
        return pit_id

    async def _close_point_in_time_quietly(self, pit_id: str, timeout: float) -> None:
        client = self._require_client()
        try:
            await client.close_point_in_time(id=pit_id, request_timeout=max(timeout, _CLOSE_PIT_MIN_TIMEOUT))
        except _ES_ERRORS:
            logger.warning("Failed to close a metrics point-in-time; it expires on its own", exc_info=True)


def _build_unsettled_query(query: UnsettledDocsQuery) -> dict[str, Any]:
    end_operator = "lte" if query.is_end_inclusive else "lt"
    time_range = {"gte": _format_timestamp(query.start), end_operator: _format_timestamp(query.end)}
    filters: list[dict[str, Any]] = [
        {"term": {"metric_name.keyword": query.metric_name}},
        {"range": {"@timestamp": time_range}},
        {
            "bool": {
                "should": [
                    {"bool": {"must_not": [{"exists": {"field": _SETTLEMENT_VERSION_FIELD}}]}},
                    {"range": {_SETTLEMENT_VERSION_FIELD: {"lt": query.settlement_version}}},
                ],
                "minimum_should_match": 1,
            }
        },
    ]
    if query.projects:
        filters.append({"terms": {"attributes.project.keyword": list(query.projects)}})
    return {"bool": {"filter": filters}}


def _format_timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _raise_if_incomplete(result: dict[str, Any]) -> None:
    if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
        raise MetricsIndexUnavailableError("Elasticsearch returned partial results")


def _parse_doc(hit: dict[str, Any]) -> MetricDoc:
    return MetricDoc(
        index=hit["_index"],
        id=hit["_id"],
        seq_no=hit["_seq_no"],
        primary_term=hit["_primary_term"],
        timestamp=_EPOCH + timedelta(milliseconds=int(hit["sort"][0])),
        sort_values=hit["sort"],
        attributes=hit.get("_source", {}).get("attributes", {}),
    )


def _parse_update_status(update: dict[str, Any]) -> UpdateStatus:
    error = update.get("error") or {}
    return UpdateStatus(update["status"], error.get("type"), error.get("reason"))
