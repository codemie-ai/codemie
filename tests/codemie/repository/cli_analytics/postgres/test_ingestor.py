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

"""PostgreSQL ingest against a recording connection: statements, ordering, dedup, errors."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch

import asyncpg
import pytest
from google.protobuf import json_format
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

from codemie.repository.cli_analytics.ports import (
    CliTelemetryIngestor,
    IngestResult,
    TelemetryStorageUnavailableError,
)
from codemie.repository.cli_analytics.postgres import ingestor as ingestor_module
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from tests.codemie.repository.cli_analytics.postgres.test_engine import SETTINGS
from tests.codemie.repository.cli_analytics.support import hook_event_builders as hb
from tests.codemie.repository.cli_analytics.support import otlp_builders as b
from tests.codemie.repository.cli_analytics.support.contracts import assert_implements_port

T = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
RESOURCE = {"service.name": "claude-code", "host.arch": "arm64"}


class RecordingConnection:
    """Records statements; the ledger answers like `ON CONFLICT DO NOTHING RETURNING`."""

    def __init__(self, error: Exception | None = None) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self.ledger: set[tuple] = set()
        self.error = error
        self.transactions = 0

    @asynccontextmanager
    async def _transaction(self):
        self.transactions += 1
        self.statements.append(("BEGIN", ()))
        yield
        self.statements.append(("COMMIT", ()))

    def transaction(self):
        return self._transaction()

    async def execute(self, sql: str, *args):
        if self.error:
            raise self.error
        self.statements.append((sql, args))
        return "INSERT 0 1"

    async def fetch(self, sql: str, *args):
        if self.error:
            raise self.error
        self.statements.append((sql, args))
        fresh = [k for k in zip(*args, strict=True) if k not in self.ledger]
        self.ledger.update(fresh)
        return [{"day": d, "h": h} for d, h in fresh]

    def sql(self, needle: str) -> list[tuple]:
        return [args for sql, args in self.statements if needle in sql]


class FakeEngine:
    def __init__(self, conn: RecordingConnection | None = None, acquire_error: Exception | None = None) -> None:
        self.settings = SETTINGS
        self.conn = conn or RecordingConnection()
        self.acquire_error = acquire_error
        self.acquire_timeouts: list[float | None] = []

    @asynccontextmanager
    async def acquire(self, timeout=None):
        self.acquire_timeouts.append(timeout)
        if self.acquire_error:
            raise self.acquire_error
        yield self.conn


def _api_request(session: str, seq: int, **extra) -> object:
    attrs = {"event.name": "api_request", "session.id": session, "event.sequence": seq, "cost_usd": 0.5}
    attrs.update(extra)
    return b.log_record(T, attrs)


def _logs_body(*records, resource=RESOURCE) -> bytes:
    return b.logs_request(resource, list(records)).SerializeToString()


def _ingestor(engine: FakeEngine) -> PostgresTelemetryIngestor:
    return PostgresTelemetryIngestor(engine)  # type: ignore[arg-type]


def test_ingestor_implements_the_ingest_port():
    assert_implements_port(PostgresTelemetryIngestor, CliTelemetryIngestor)


@pytest.mark.asyncio
async def test_a_request_is_one_transaction_in_a_fixed_statement_order():
    engine = FakeEngine()
    body = _logs_body(_api_request("s1", 1, **{"user.id": "u"}))

    result = await _ingestor(engine).ingest_otlp("logs", body, "application/x-protobuf")

    assert engine.conn.transactions == 1
    order = [m.group(1) for sql, _ in engine.conn.statements if (m := re.search(r"INSERT INTO (\w+)", sql))]
    # The ledger first: only records it accepts bring their resource and session attributes.
    assert order == ["ingest_dedup", "otel_resources", "session_attributes", "log_events", "rollup_dirty"]
    assert engine.conn.statements[0] == ("BEGIN", ()) and engine.conn.statements[-1] == ("COMMIT", ())
    # Its own limit, not the 30 s one sized for dashboards: a request stuck behind a lock
    # frees its connection instead of holding it while the plugin re-sends.
    assert engine.conn.statements[1] == ("SET LOCAL statement_timeout = 10000", ())
    assert result == IngestResult(body=b"", media_type="application/x-protobuf", accepted=1)


@pytest.mark.asyncio
async def test_records_already_in_the_ledger_are_not_stored_again():
    engine = FakeEngine()
    ingestor = _ingestor(engine)
    await ingestor.ingest_otlp("logs", _logs_body(_api_request("s1", 1)), "application/x-protobuf")

    # The plugin re-sends a grown batch: the old record plus a new one.
    result = await ingestor.ingest_otlp(
        "logs", _logs_body(_api_request("s1", 1), _api_request("s1", 2)), "application/x-protobuf"
    )

    assert result.accepted == 1
    (inserted,) = engine.conn.sql("INSERT INTO log_events")[-1:]
    assert len(inserted[0]) == 1  # one ts value: only the new record


@pytest.mark.asyncio
async def test_a_record_repeated_inside_one_request_is_stored_once():
    engine = FakeEngine()

    result = await _ingestor(engine).ingest_otlp(
        "logs", _logs_body(_api_request("s1", 1)) + _logs_body(_api_request("s1", 1)), "application/x-protobuf"
    )

    (ledger_args,) = engine.conn.sql("INSERT INTO ingest_dedup")
    assert len(ledger_args[0]) == 1
    assert result.accepted == 1


@pytest.mark.asyncio
async def test_keyed_statements_send_their_keys_in_sorted_order():
    engine = FakeEngine()
    body = _logs_body(*(_api_request(f"s{n}", n, **{"user.id": f"u{n}"}) for n in (3, 1, 2)))

    await _ingestor(engine).ingest_otlp("logs", body, "application/x-protobuf")

    (ledger_days, ledger_hashes) = engine.conn.sql("INSERT INTO ingest_dedup")[0]
    assert list(zip(ledger_days, ledger_hashes, strict=True)) == sorted(zip(ledger_days, ledger_hashes, strict=True))
    (sessions, _attrs) = engine.conn.sql("INSERT INTO session_attributes")[0]
    assert list(sessions) == ["s1", "s2", "s3"]
    (_days, dirty_sessions, _kinds) = engine.conn.sql("INSERT INTO rollup_dirty")[0]
    assert list(dirty_sessions) == ["s1", "s2", "s3"]


@pytest.mark.asyncio
async def test_resources_already_stored_are_not_sent_again():
    engine = FakeEngine()
    ingestor = _ingestor(engine)

    await ingestor.ingest_otlp("logs", _logs_body(_api_request("s1", 1)), "application/x-protobuf")
    await ingestor.ingest_otlp("logs", _logs_body(_api_request("s1", 2)), "application/x-protobuf")

    assert len(engine.conn.sql("INSERT INTO otel_resources")) == 1


@pytest.mark.asyncio
async def test_a_resource_is_written_again_once_a_day_so_retention_sees_it_in_use():
    engine, today = FakeEngine(), [date(2026, 9, 23)]
    ingestor = PostgresTelemetryIngestor(engine, clock=lambda: today[0])  # type: ignore[arg-type]

    await ingestor.ingest_otlp("logs", _logs_body(_api_request("s1", 1)), "application/x-protobuf")
    await ingestor.ingest_otlp("logs", _logs_body(_api_request("s1", 2)), "application/x-protobuf")
    today[0] = date(2026, 9, 24)
    await ingestor.ingest_otlp("logs", _logs_body(_api_request("s1", 3)), "application/x-protobuf")

    assert len(engine.conn.sql("INSERT INTO otel_resources")) == 2


@pytest.mark.asyncio
async def test_an_empty_request_touches_no_database():
    engine = FakeEngine()

    result = await _ingestor(engine).ingest_otlp("traces", b"", "application/x-protobuf")

    assert result.accepted == 0
    assert engine.acquire_timeouts == []


@pytest.mark.asyncio
async def test_a_saturated_pool_is_reported_as_unavailable_quickly():
    engine = FakeEngine(acquire_error=asyncio.TimeoutError())

    with patch.object(ingestor_module, "logger") as logger, pytest.raises(TelemetryStorageUnavailableError):
        await _ingestor(engine).ingest_otlp("logs", _logs_body(_api_request("s1", 1)), "application/x-protobuf")

    assert engine.acquire_timeouts == [SETTINGS.ingest_acquire_timeout_s]
    assert "no free analytics connection" in logger.warning.call_args.args[0]


@pytest.mark.asyncio
async def test_a_statement_timing_out_is_reported_apart_from_a_busy_pool():
    engine = FakeEngine(RecordingConnection(error=asyncio.TimeoutError()))  # asyncpg's client-side limit

    with patch.object(ingestor_module, "logger") as logger, pytest.raises(TelemetryStorageUnavailableError):
        await _ingestor(engine).ingest_otlp("logs", _logs_body(_api_request("s1", 1)), "application/x-protobuf")

    assert "timed out" in logger.warning.call_args.args[0]


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.exceptions.UndefinedTableError("relation does not exist"),
        asyncpg.exceptions.QueryCanceledError("statement timeout"),
        asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed"),
        ConnectionRefusedError("refused"),
    ],
)
@pytest.mark.asyncio
async def test_database_failures_are_reported_as_unavailable(error):
    engine = FakeEngine(RecordingConnection(error=error))

    with pytest.raises(TelemetryStorageUnavailableError):
        await _ingestor(engine).ingest_otlp("logs", _logs_body(_api_request("s1", 1)), "application/x-protobuf")


class RejectingConnection(RecordingConnection):
    """Rejects any insert of log rows of the `poison` session, as PostgreSQL rejects a value
    it cannot store; a failed transaction leaves nothing behind."""

    def __init__(self, poison: str, error: Exception | None = None, table: str = "log_events") -> None:
        super().__init__()
        self.poison = poison
        self.rejection = error or asyncpg.exceptions.InvalidParameterValueError("unstorable value")
        self.table = table  # where the unstorable value is: the records, or their session attributes
        self.pending: list[str] = []
        self.stored: list[str] = []  # sessions of committed log rows
        self.failed_transactions = 0

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        ledger, self.pending = set(self.ledger), []
        self.transactions += 1
        try:
            yield
        except BaseException:
            self.ledger = ledger
            raise
        self.stored += self.pending

    async def execute(self, sql: str, *args):
        if f"INSERT INTO {self.table}" in sql:
            sessions = args[1] if self.table == "log_events" else args[0]  # log rows: after ts
            prefix = self.poison.removesuffix("*")
            poisoned = (
                any(s.startswith(prefix) for s in sessions) if self.poison.endswith("*") else self.poison in sessions
            )
            if poisoned:
                self.failed_transactions += 1
                raise self.rejection
        if "INSERT INTO log_events" in sql:
            self.pending += args[1]
        return await super().execute(sql, *args)


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.exceptions.InvalidParameterValueError("unstorable value"),
        # 54000: a value too large for an index row.
        asyncpg.exceptions.ProgramLimitExceededError("index row size exceeds btree maximum"),
    ],
)
@pytest.mark.asyncio
async def test_a_record_the_database_rejects_is_dropped_and_the_others_are_stored(error):
    # The plugin re-sends every non-2xx answer with its whole spool: failing the request
    # would block that client's telemetry for good.
    conn = RejectingConnection(poison="s-bad", error=error)
    body = _logs_body(*(_api_request(s, 1) for s in ("s1", "s-bad", "s2", "s3")))

    with patch.object(ingestor_module, "logger") as logger:
        result = await _ingestor(FakeEngine(conn)).ingest_otlp("logs", body, "application/x-protobuf")

    assert sorted(conn.stored) == ["s1", "s2", "s3"]
    assert result.accepted == 3
    assert ExportLogsServiceResponse.FromString(result.body).partial_success.rejected_log_records == 1
    assert "s-bad" in logger.warning.call_args.args[0]


@pytest.mark.asyncio
async def test_a_rejected_record_is_remembered_so_a_resend_skips_it():
    conn = RejectingConnection(poison="s-bad")
    ingestor = _ingestor(FakeEngine(conn))
    body = _logs_body(_api_request("s1", 1), _api_request("s-bad", 1))

    with patch.object(ingestor_module, "logger"):
        await ingestor.ingest_otlp("logs", body, "application/x-protobuf")
        transactions = conn.transactions
        result = await ingestor.ingest_otlp("logs", body, "application/x-protobuf")  # the plugin's re-send

    assert conn.transactions == transactions + 1  # nothing left to store, nor to search for
    assert result == IngestResult(body=b"", media_type="application/x-protobuf", accepted=0)


@pytest.mark.asyncio
async def test_isolating_rejected_records_costs_a_bounded_number_of_transactions_per_request():
    # Otherwise a request of N unstorable records would take about 3N transactions.
    conn = RejectingConnection(poison="*")
    body = _logs_body(*(_api_request(f"s{i}", 1) for i in range(500)))

    with patch.object(ingestor_module, "logger"), pytest.raises(TelemetryStorageUnavailableError):
        await _ingestor(FakeEngine(conn)).ingest_otlp("logs", body, "application/x-protobuf")

    assert conn.failed_transactions <= 64 and conn.transactions <= 200


@pytest.mark.asyncio
async def test_the_plugins_resends_finish_a_request_its_budget_did_not():
    # Answered 200 after running out of attempts, the rest of the request would be lost: the plugin
    # deletes its spool on any 2xx. With a 503 it re-sends, and the ledger carries each attempt on.
    conn = RejectingConnection(poison="bad*")
    sessions = [f"bad{i}" if i % 2 else f"ok{i}" for i in range(256)]  # every failing part mixes both
    body = _logs_body(*(_api_request(s, 1) for s in sessions))
    ingestor = _ingestor(FakeEngine(conn))

    attempts = 0
    with patch.object(ingestor_module, "logger"):
        while attempts < 20:  # the plugin re-sends until it gets a 2xx
            attempts += 1
            try:
                await ingestor.ingest_otlp("logs", body, "application/x-protobuf")
                break
            except TelemetryStorageUnavailableError:
                continue

    assert sorted(conn.stored) == sorted(s for s in sessions if s.startswith("ok"))  # none lost
    assert len(conn.ledger) == len(sessions) and attempts <= 10


@pytest.mark.asyncio
async def test_a_record_whose_session_attributes_are_rejected_is_skipped_on_a_resend():
    conn = RejectingConnection(poison="s-bad", table="session_attributes")
    body = _logs_body(_api_request("s1", 1, **{"user.id": "u"}), _api_request("s-bad", 1, **{"user.id": "u"}))
    ingestor = _ingestor(FakeEngine(conn))

    with patch.object(ingestor_module, "logger"):
        first = await ingestor.ingest_otlp("logs", body, "application/x-protobuf")
        failed = conn.failed_transactions
        await ingestor.ingest_otlp("logs", body, "application/x-protobuf")

    assert (first.accepted, conn.stored) == (1, ["s1"])
    assert conn.failed_transactions == failed  # the re-send is not rejected all over again


@pytest.mark.asyncio
async def test_json_requests_learn_how_many_records_were_rejected():
    conn = RejectingConnection(poison="s-bad")
    body = json_format.MessageToJson(b.logs_request(RESOURCE, [_api_request("s-bad", 1)])).encode()

    with patch.object(ingestor_module, "logger"):
        result = await _ingestor(FakeEngine(conn)).ingest_otlp("logs", body, "application/json")

    assert json.loads(result.body)["partialSuccess"]["rejectedLogRecords"] == "1"


@pytest.mark.parametrize(
    ("signal", "response_type", "field"),
    [
        ("logs", ExportLogsServiceResponse, "rejected_log_records"),
        ("traces", ExportTraceServiceResponse, "rejected_spans"),
        ("metrics", ExportMetricsServiceResponse, "rejected_data_points"),
    ],
)
def test_partial_success_names_what_each_signal_rejected(signal, response_type, field):
    response = response_type.FromString(ingestor_module.otlp_response(signal, rejected=2))

    assert getattr(response.partial_success, field) == 2
    assert response.partial_success.error_message
    assert ingestor_module.otlp_response(signal, rejected=0) == b""


@pytest.mark.asyncio
async def test_json_requests_get_a_json_response():
    engine = FakeEngine()
    body = b"{}"

    result = await _ingestor(engine).ingest_otlp("metrics", body, "application/json")

    assert (result.body, result.media_type) == (b"{}", "application/json")


@pytest.mark.asyncio
async def test_hook_events_are_stored_with_the_sender_and_answered_in_json():
    engine = FakeEngine()
    events = [{"type": "agent.session.start", "session_id": "s1", "timestamp": T.isoformat()}]

    result = await _ingestor(engine).ingest_hook_events(events, "dev@example.com", 0)

    (hook_args,) = engine.conn.sql("INSERT INTO hook_events")
    assert hook_args[4] == ["dev@example.com"]  # user_email column
    assert (result.body, result.media_type, result.accepted) == (b"{}", "application/json", 1)
    (dirty_args,) = engine.conn.sql("INSERT INTO rollup_dirty")
    assert list(dirty_args[2]) == [34]  # DIMENSIONS 2 + SESSION 32


@pytest.mark.asyncio
async def test_large_bodies_are_decoded_off_the_event_loop():
    engine = FakeEngine()
    ingestor = PostgresTelemetryIngestor(engine, decode_in_thread_above_bytes=10)  # type: ignore[arg-type]
    calls = []

    async def to_thread(fn, *args):
        calls.append(fn)
        return fn(*args)

    with patch.object(ingestor_module.asyncio, "to_thread", side_effect=to_thread):
        await ingestor.ingest_otlp("logs", _logs_body(_api_request("s1", 1)), "application/x-protobuf")

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_large_hook_batches_are_decoded_off_the_event_loop():
    engine = FakeEngine()
    ingestor = PostgresTelemetryIngestor(engine, decode_in_thread_above_events=2)  # type: ignore[arg-type]
    events = [{"type": "agent.tool.start", "session_id": "s1", "n": i} for i in range(3)]
    calls = []

    async def to_thread(fn, *args):
        calls.append(fn)
        return fn(*args)

    with patch.object(ingestor_module.asyncio, "to_thread", side_effect=to_thread):
        await ingestor.ingest_hook_events(events[:2], "dev@example.com", 0)  # at the limit: on the loop
        await ingestor.ingest_hook_events(events, "dev@example.com", 0)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_jsonb_columns_are_sent_as_json_text():
    engine = FakeEngine()

    await _ingestor(engine).ingest_otlp(
        "logs", _logs_body(_api_request("s1", 1, extra_attr="x")), "application/x-protobuf"
    )

    (log_args,) = engine.conn.sql("INSERT INTO log_events")
    assert json.loads(log_args[-1][0]) == {"event.sequence": 1, "extra_attr": "x"}


def _usage_events(*request_ids: str) -> list[dict[str, object]]:
    return [hb.usage_request(request_id=r, event_id=f"usage:s:{r}", session_id=r) for r in request_ids]


@pytest.mark.asyncio
async def test_a_usage_request_is_stored_in_its_own_table_and_the_ledger() -> None:
    engine = FakeEngine()

    result = await _ingestor(engine).ingest_hook_events([hb.usage_request()], "dev@example.com", 0)

    order = [m.group(1) for sql, _ in engine.conn.statements if (m := re.search(r"INSERT INTO (\w+)", sql))]
    assert order == ["ingest_dedup", "usage_requests", "rollup_dirty"]
    assert result.accepted == 1
    (ledger_args,) = engine.conn.sql("INSERT INTO ingest_dedup")
    assert list(ledger_args[0]) == [date(2026, 9, 29)] and len(ledger_args[1]) == 1
    (usage_args,) = engine.conn.sql("INSERT INTO usage_requests")
    assert usage_args[1] == ["3f6c0a52"]  # session_id column, after ts


@pytest.mark.asyncio
async def test_usage_columns_are_sent_with_their_sql_types() -> None:
    engine = FakeEngine()

    await _ingestor(engine).ingest_hook_events([hb.usage_request()], "dev@example.com", 0)

    (sql,) = [s for s, _ in engine.conn.statements if "INSERT INTO usage_requests" in s]
    unnest = sql.split("FROM unnest(")[1]
    types = dict(zip(re.findall(r"\) AS u\(([^)]*)\)", unnest)[0].split(", "), re.findall(r"::(\w+)\[\]", unnest)))
    assert types["web_search_requests"] == types["web_fetch_requests"] == "int4"
    assert types["is_api_error"] == "bool"
    assert types["thinking_tokens"] == types["cache_creation_5m_tokens"] == types["cache_creation_1h_tokens"] == "int8"
    # Existing names keep their types.
    assert (types["input_tokens"], types["output_tokens"], types["cache_read_tokens"]) == ("int8",) * 3
    assert types["ts"] == "timestamptz" and types["session_id"] == "text"
    assert "$" + str(list(types).index("attrs") + 1) + "::text[]" in sql and "attrs::jsonb" in sql


@pytest.mark.asyncio
async def test_usage_requests_touch_no_rollup_or_session_table() -> None:
    engine = FakeEngine()

    await _ingestor(engine).ingest_hook_events([hb.usage_request()], "dev@example.com", 0)

    targets = {m.group(1) for sql, _ in engine.conn.statements if (m := re.search(r"INSERT INTO (\w+)", sql))}
    assert not {t for t in targets if t.startswith("rollup_") and t != "rollup_dirty"}
    assert "session_attributes" not in targets and "sessions" not in targets


class RejectingUsageConnection(RecordingConnection):
    """Rejects any insert of usage rows of the `poison` session (class 22), as PostgreSQL would."""

    def __init__(self, poison: str) -> None:
        super().__init__()
        self.poison = poison
        self.pending: list[str] = []
        self.stored: list[str] = []

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        ledger, self.pending = set(self.ledger), []
        self.transactions += 1
        try:
            yield
        except BaseException:
            self.ledger = ledger
            raise
        self.stored += self.pending

    async def execute(self, sql: str, *args: object) -> str:
        if "INSERT INTO usage_requests" in sql:
            if self.poison in args[1]:  # session_id column, after ts
                raise asyncpg.exceptions.InvalidParameterValueError("unstorable value")
            self.pending += args[1]
        return await super().execute(sql, *args)


@pytest.mark.asyncio
async def test_a_usage_row_the_database_rejects_is_dropped_and_the_others_are_stored() -> None:
    conn = RejectingUsageConnection(poison="s-bad")
    events = _usage_events("s1", "s-bad", "s2", "s3")

    with patch.object(ingestor_module, "logger") as logger:
        result = await _ingestor(FakeEngine(conn)).ingest_hook_events(events, "dev@example.com", 0)

    assert sorted(conn.stored) == ["s1", "s2", "s3"]
    assert result.accepted == 3
    assert "s-bad" in logger.warning.call_args.args[0]
