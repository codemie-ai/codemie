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

"""PostgreSQL ingest: one transaction per request, each record stored exactly once.

Per request: insert the records' identities into the ledger (`ingest_dedup`), then, for the
records it accepted only, upsert their resources and session attributes, insert their rows
and queue their (day, session) keys for the rollup refresher. Keys go to the database sorted, and the
statements run in the same order in every request, so concurrent requests carrying the
same records (a retry racing its original, grown batches) lock rows in one global order
and cannot deadlock.

The plugin's proxy retries every non-2xx answer except 401/403, with its whole spool, so:
storage unavailable or saturated -> 503 (retried later, nothing lost while the client
keeps its spool). A record the database rejects for its content is found by splitting the
request, dropped and entered in the ledger (a re-send skips it); the rest is stored and
the answer is 200, with OTLP partial success naming how many records were rejected.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any

import asyncpg
from google.protobuf import json_format
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

from codemie.repository.cli_analytics.ports import (
    IngestResult,
    OtlpSignal,
    TelemetryStorageUnavailableError,
)
from codemie.repository.cli_analytics.postgres.dirty import dirty_keys
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.otlp import (
    JSON_MEDIA_TYPE,
    DecodedBatch,
    HookRow,
    LogRow,
    MetricRow,
    SpanRow,
    decode_hook_events,
    decode_otlp,
    json_text,
)

logger = logging.getLogger(__name__)

_PROTOBUF_RESPONSE_MEDIA_TYPE = "application/x-protobuf"
# Decoding a large body takes long enough to stall other requests; do it in a worker thread.
DECODE_IN_THREAD_ABOVE_BYTES = 64 * 1024
# The same for plugin hook batches, which arrive parsed: roughly 64 KB of NDJSON.
DECODE_IN_THREAD_ABOVE_EVENTS = 200
# Resource ids stored today by this process; cleared when it grows past this (resources are few).
_KNOWN_RESOURCES_LIMIT = 50_000
# Rejected for its content, so a retry fails the same way: a value PostgreSQL cannot store
# (class 22) or one too large for an index row (54000). Not class 23: "no partition for the
# row" means the partitions are missing, which the next maintenance run repairs.
_REJECTED_CONTENT = (asyncpg.DataError, asyncpg.exceptions.ProgramLimitExceededError)
_RECORD_KINDS = ("logs", "hooks", "spans", "metrics")
# Failed attempts a request may spend isolating rejected records. Past them the request answers
# 503: what it stored or rejected stays, and the plugin's re-send carries on from there (the
# ledger skips both). A 2xx would lose the rest, as the plugin deletes its spool on any 2xx.
_ISOLATION_ATTEMPTS = 64
# How an OTLP response names the records it rejected (partial success).
_OTLP_RESPONSES = {
    "logs": (ExportLogsServiceResponse, "rejected_log_records"),
    "traces": (ExportTraceServiceResponse, "rejected_spans"),
    "metrics": (ExportMetricsServiceResponse, "rejected_data_points"),
}
_REJECTED_MESSAGE = "records the analytics storage cannot store were dropped"

# PostgreSQL types of the non-text columns; `attrs` is sent as text and cast to jsonb.
_COLUMN_TYPES: dict[str, str] = {
    "ts": "timestamptz",
    "start_ts": "timestamptz",
    "event_kind": "int2",
    "span_kind": "int2",
    "severity": "int2",
    "status_code": "int2",
    "temporality": "int2",
    "trace_id": "bytea",
    "span_id": "bytea",
    "parent_span_id": "bytea",
    "cost_usd": "float8",
    "value": "float8",
    "input_tokens": "int8",
    "output_tokens": "int8",
    "cache_read_tokens": "int8",
    "cache_creation_tokens": "int8",
    "duration_ns": "int8",
    "resource_id": "int8",
    "is_monotonic": "bool",
    "attrs": "jsonb",
}


def _insert_sql(table: str, row_type: type) -> str:
    """INSERT ... SELECT FROM unnest(one array per column): one round trip per table."""
    columns = row_type._fields[2:]  # every field after the ledger key (day, h)
    arrays = ", ".join(
        f"${i}::{'text' if _COLUMN_TYPES.get(c) == 'jsonb' else _COLUMN_TYPES.get(c, 'text')}[]"
        for i, c in enumerate(columns, start=1)
    )
    select = ", ".join(f"{c}::jsonb" if _COLUMN_TYPES.get(c) == "jsonb" else c for c in columns)
    names = ", ".join(columns)
    return f"INSERT INTO {table} ({names}) SELECT {select} FROM unnest({arrays}) AS u({names})"


_TABLES: tuple[tuple[str, str, type], ...] = (
    ("logs", "log_events", LogRow),
    ("hooks", "hook_events", HookRow),
    ("spans", "spans", SpanRow),
    ("metrics", "metric_points", MetricRow),
)
_INSERT_SQL = {table: _insert_sql(table, row_type) for _, table, row_type in _TABLES}

# Each process stores a resource once a day, which keeps last_seen current for retention
# (a row touched in the last day, by another pod say, is left alone).
_RESOURCES_SQL = """
INSERT INTO otel_resources AS o (resource_id, service_name, attrs)
SELECT r, s, a::jsonb FROM unnest($1::int8[], $2::text[], $3::text[]) AS u(r, s, a)
ORDER BY r
ON CONFLICT (resource_id) DO UPDATE SET last_seen = EXCLUDED.last_seen
WHERE o.last_seen < EXCLUDED.last_seen - interval '1 day'"""

_SESSION_ATTRS_SQL = """
INSERT INTO session_attributes AS sa (session_id, attrs)
SELECT s, a::jsonb FROM unnest($1::text[], $2::text[]) AS u(s, a)
ORDER BY s
ON CONFLICT (session_id) DO UPDATE
    SET attrs = sa.attrs || EXCLUDED.attrs, updated_at = now()
    WHERE NOT (sa.attrs @> EXCLUDED.attrs)"""

_LEDGER_SQL = """
INSERT INTO ingest_dedup (day, h)
SELECT d, h FROM unnest($1::date[], $2::int8[]) AS u(d, h)
ORDER BY d, h
ON CONFLICT DO NOTHING
RETURNING day, h"""

# `version` changes with every mark, which tells the refresher that a key it recomputed
# was marked again meanwhile (see rollups.py).
_DIRTY_SQL = """
INSERT INTO rollup_dirty AS r (day, session_id, kinds)
SELECT d, s, k FROM unnest($1::date[], $2::text[], $3::int4[]) AS u(d, s, k)
ORDER BY d, s
ON CONFLICT (day, session_id) DO UPDATE
    -- marked_at stays: a key keeps its place in the queue however often it is marked again
    SET kinds = r.kinds | EXCLUDED.kinds, version = r.version + 1"""


def _columns(rows: Sequence[tuple]) -> list[list[Any]]:
    """Rows (without the ledger key) turned into one list per column."""
    return [list(column) for column in zip(*(row[2:] for row in rows), strict=True)]


def _otlp_message(signal: OtlpSignal, rejected: int) -> Any:
    response_type, field_name = _OTLP_RESPONSES[signal]
    response = response_type()
    if rejected:
        setattr(response.partial_success, field_name, rejected)
        response.partial_success.error_message = _REJECTED_MESSAGE
    return response


def otlp_response(signal: OtlpSignal, rejected: int) -> bytes:
    """The Export*ServiceResponse, encoded: no bytes at all unless records were rejected."""
    return _otlp_message(signal, rejected).SerializeToString()


def _subset(batch: DecodedBatch, records: list[tuple[str, Any]]) -> DecodedBatch:
    """Those records, with the resources and session attributes they use."""
    part = DecodedBatch()
    for kind, row in records:
        getattr(part, kind).append(row)
        resource_id = getattr(row, "resource_id", None)
        if resource_id in batch.resources:
            part.resources[resource_id] = batch.resources[resource_id]
        if row.session_id in batch.session_attrs:
            part.session_attrs[row.session_id] = batch.session_attrs[row.session_id]
    return part


def _halves(batch: DecodedBatch) -> tuple[DecodedBatch, DecodedBatch]:
    records = [(kind, row) for kind in _RECORD_KINDS for row in getattr(batch, kind)]
    middle = len(records) // 2
    return _subset(batch, records[:middle]), _subset(batch, records[middle:])


def _keep_fresh(batch: DecodedBatch, fresh: set[tuple]) -> DecodedBatch:
    """Only rows the ledger accepted, each identity once (a request can repeat a record), with
    the resources and session attributes they use."""
    taken: set[tuple] = set()
    records = []
    for kind in _RECORD_KINDS:
        for row in getattr(batch, kind):
            key = (row.day, row.h)
            if key in fresh and key not in taken:
                taken.add(key)
                records.append((kind, row))
    return _subset(batch, records)


class PostgresTelemetryIngestor:
    """The PostgreSQL `CliTelemetryIngestor`."""

    def __init__(
        self,
        engine: AnalyticsPgEngine,
        decode_in_thread_above_bytes: int = DECODE_IN_THREAD_ABOVE_BYTES,
        decode_in_thread_above_events: int = DECODE_IN_THREAD_ABOVE_EVENTS,
        clock: Callable[[], date] | None = None,
    ) -> None:
        """`clock` is for tests: today's UTC date."""
        self._engine = engine
        self._decode_in_thread_above = decode_in_thread_above_bytes
        self._decode_in_thread_above_events = decode_in_thread_above_events
        self._today = clock or (lambda: datetime.now(timezone.utc).date())
        self._known_resources: dict[int, date] = {}  # resource id -> the day this process stored it

    async def ingest_otlp(self, signal: OtlpSignal, body: bytes, content_type: str) -> IngestResult:
        batch = await self._decode(lambda: decode_otlp(signal, body, content_type), len(body))
        stored, rejected = await self._store(batch)
        if content_type.split(";", 1)[0].strip().lower() == JSON_MEDIA_TYPE:
            response = json_format.MessageToJson(_otlp_message(signal, rejected), indent=None)
            return IngestResult(body=response.encode(), media_type=JSON_MEDIA_TYPE, accepted=stored)
        return IngestResult(
            body=otlp_response(signal, rejected), media_type=_PROTOBUF_RESPONSE_MEDIA_TYPE, accepted=stored
        )

    async def ingest_hook_events(self, events: list[dict], user_email: str, received_at_ns: int) -> IngestResult:
        decode = lambda: decode_hook_events(events, user_email, received_at_ns)  # noqa: E731
        if len(events) > self._decode_in_thread_above_events:
            batch = await asyncio.to_thread(decode)
        else:
            batch = decode()
        stored, _rejected = await self._store(batch)
        return IngestResult(body=b"{}", media_type=JSON_MEDIA_TYPE, accepted=stored)

    async def _decode(self, decode: Callable[[], DecodedBatch], size: int) -> DecodedBatch:
        if size > self._decode_in_thread_above:
            return await asyncio.to_thread(decode)
        return decode()

    async def _store(self, batch: DecodedBatch, attempts: list[int] | None = None) -> tuple[int, int]:
        """Store the batch; returns how many records were stored and how many were rejected.

        A batch the database rejects for its content is split in halves until the record at
        fault is alone, and that record is rejected (see _ISOLATION_ATTEMPTS).
        """
        if not batch.record_count:
            return 0, 0
        if attempts is not None and attempts[0] <= 0:
            logger.warning(
                "cli_analytics: a request holds more records PostgreSQL rejects than one attempt isolates; "
                "answered 503, its re-send carries on"
            )
            raise TelemetryStorageUnavailableError("Isolating records the storage rejects, retry later")
        try:
            return await self._store_once(batch), 0
        except _REJECTED_CONTENT as exc:
            attempts = attempts or [_ISOLATION_ATTEMPTS]
            attempts[0] -= 1
            if batch.record_count == 1:
                await self._reject(batch, exc)
                return 0, 1
            first, second = _halves(batch)
            stored_first, rejected_first = await self._store(first, attempts)
            stored_second, rejected_second = await self._store(second, attempts)
            return stored_first + stored_second, rejected_first + rejected_second

    async def _reject(self, batch: DecodedBatch, error: asyncpg.PostgresError) -> None:
        """Enter the record in the ledger without storing it: the plugin re-sends its whole spool."""
        (row,) = (row for kind in _RECORD_KINDS for row in getattr(batch, kind))
        async with self._transaction() as conn:
            await conn.fetch(_LEDGER_SQL, [row.day], [row.h])
        logger.warning(
            f"cli_analytics: dropped a telemetry record PostgreSQL rejects ({error.sqlstate}: {error}), "
            f"of session {row.session_id!r} on {row.day}; a re-send of it is skipped"
        )

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """An ingest transaction; failures other than rejected content are reported as 503 (retried)."""
        settings = self._engine.settings
        acquired = False
        try:
            async with (
                self._engine.acquire(timeout=settings.ingest_acquire_timeout_s) as conn,
                conn.transaction(),
            ):
                acquired = True
                await conn.execute(f"SET LOCAL statement_timeout = {int(settings.ingest_statement_timeout_ms)}")
                yield conn
        except TimeoutError as exc:
            if acquired:
                logger.warning("cli_analytics: an ingest statement timed out on the client side")
            else:
                logger.warning("cli_analytics: ingest found no free analytics connection in time")
            raise TelemetryStorageUnavailableError("Analytics storage is busy, retry later") from exc
        except _REJECTED_CONTENT:
            raise
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
            logger.error("cli_analytics: ingest failed: %s: %s", type(exc).__name__, exc)
            raise TelemetryStorageUnavailableError("Analytics storage unavailable") from exc

    async def _store_once(self, batch: DecodedBatch) -> int:
        keys = sorted(
            {(row.day, row.h) for rows in (batch.logs, batch.hooks, batch.spans, batch.metrics) for row in rows}
        )
        today = self._today()
        async with self._transaction() as conn:
            # The ledger first: a re-sent record (stored, or rejected before) brings nothing else.
            fresh_keys = await conn.fetch(_LEDGER_SQL, [k[0] for k in keys], [k[1] for k in keys])
            stored = _keep_fresh(batch, {(r["day"], r["h"]) for r in fresh_keys})
            new_resources = sorted(
                (rid, *v) for rid, v in stored.resources.items() if self._known_resources.get(rid) != today
            )
            if new_resources:
                await conn.execute(_RESOURCES_SQL, *_columns([(None, None, *r) for r in new_resources]))
            session_attrs = sorted(stored.session_attrs.items())
            if session_attrs:
                await conn.execute(
                    _SESSION_ATTRS_SQL, [s for s, _ in session_attrs], [json_text(a) for _, a in session_attrs]
                )
            for attr, table, _ in _TABLES:
                rows = getattr(stored, attr)
                if rows:
                    await conn.execute(_INSERT_SQL[table], *_columns(rows))
            dirty = sorted(dirty_keys(stored).items())
            if dirty:
                await conn.execute(
                    _DIRTY_SQL, [k[0] for k, _ in dirty], [k[1] for k, _ in dirty], [f for _, f in dirty]
                )
        self._remember((rid for rid, *_ in new_resources), today)
        return stored.record_count

    def _remember(self, resource_ids, today: date) -> None:
        if len(self._known_resources) > _KNOWN_RESOURCES_LIMIT:
            self._known_resources.clear()
        self._known_resources.update(dict.fromkeys(resource_ids, today))
