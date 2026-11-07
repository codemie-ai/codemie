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
import logging
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
    SessionBinding,
    StalePolicyError,
    TelemetryStorageUnavailableError,
)
from codemie.repository.cli_analytics.postgres import ingestor as ingestor_module
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.otlp import HookRow
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
        self.fork_days: list[dict] = []  # what the fork-day lookup answers
        self.later_start_days: list[dict] = []  # what the later-start lookup answers
        self.bindings: list[dict] = []  # what the session binding lookup answers

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
        if "LEFT JOIN session_dims" in sql:
            return self.bindings
        if "AND f.ts > u.t" in sql:
            return self.later_start_days
        if "JOIN hook_events f" in sql:
            return self.fork_days
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

    def lock_key(self, name: str) -> int:
        return self.settings.lock_key(name)

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
    body = _logs_body(*(_api_request(f"s{i % 100}", i) for i in range(500)))  # 100 sessions: the per-request cap

    with patch.object(ingestor_module, "logger"), pytest.raises(TelemetryStorageUnavailableError):
        await _ingestor(FakeEngine(conn)).ingest_otlp("logs", body, "application/x-protobuf")

    assert conn.failed_transactions <= 64 and conn.transactions <= 200


@pytest.mark.asyncio
async def test_the_plugins_resends_finish_a_request_its_budget_did_not():
    # Answered 200 after running out of attempts, the rest of the request would be lost: the plugin
    # deletes its spool on any 2xx. With a 503 it re-sends, and the ledger carries each attempt on.
    conn = RejectingConnection(poison="bad*")
    names = [f"bad{i}" if i % 2 else f"ok{i}" for i in range(100)]  # 100 sessions: the per-request cap
    sessions = [names[i % 100] for i in range(256)]  # every failing part mixes both
    body = _logs_body(*(_api_request(s, i) for i, s in enumerate(sessions)))
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
async def test_a_skill_call_end_queues_its_forks_own_day():
    """A Skill agent.tool.end naming its fork looks the fork's day up by (session_id, agent_id) in the same
    transaction and queues it with SPAN_FACTS beside the end's own keys, in one rollup_dirty insert."""
    engine = FakeEngine()
    fork_day = date(2026, 1, 1)
    engine.conn.fork_days = [{"day": fork_day, "session_id": "s1"}]
    events = [
        {
            "type": "agent.tool.end",
            "session_id": "s1",
            "timestamp": T.isoformat(),
            "tool_name": "Skill",
            "tool_use_id": "toolu_1",
            "fork_agent_id": "a861e999c42f7f90c",
        }
    ]

    await _ingestor(engine).ingest_hook_events(events, "dev@example.com", 0)

    (lookup_args,) = engine.conn.sql("JOIN hook_events f")
    assert lookup_args == (["s1"], ["a861e999c42f7f90c"])
    (dirty_args,) = engine.conn.sql("INSERT INTO rollup_dirty")
    marks = dict(zip(zip(dirty_args[0], dirty_args[1], strict=True), dirty_args[2], strict=True))
    assert marks[(fork_day, "s1")] == 4  # SPAN_FACTS
    assert marks[(T.date(), "s1")] & 4


@pytest.mark.asyncio
async def test_a_batch_without_a_skill_or_agent_call_end_makes_no_fork_day_lookup():
    engine = FakeEngine()
    events = [{"type": "agent.tool.end", "session_id": "s1", "timestamp": T.isoformat(), "tool_name": "Read"}]

    await _ingestor(engine).ingest_hook_events(events, "dev@example.com", 0)

    assert engine.conn.sql("JOIN hook_events f") == []


@pytest.mark.asyncio
async def test_a_batch_locks_every_session_first_in_key_order():
    """A Skill end naming a fork (session s1) and a keyed agent.subagent.start (session s2): one statement takes
    the transaction-scoped lock of each session of the batch, keys sorted, before the binding lookup and the
    ledger (EPMCDME-16440), so a concurrent batch of the same session commits before anything here reads."""
    engine = FakeEngine()
    events = [
        {
            "type": "agent.tool.end",
            "session_id": "s1",
            "timestamp": T.isoformat(),
            "tool_name": "Skill",
            "tool_use_id": "toolu_1",
            "fork_agent_id": "a861e999c42f7f90c",
        },
        {"type": "agent.subagent.start", "session_id": "s2", "timestamp": T.isoformat(), "agent_id": "a1"},
    ]

    await _ingestor(engine).ingest_hook_events(events, "dev@example.com", 0)

    statements = [sql for sql, _ in engine.conn.statements]
    (lock,) = [i for i, sql in enumerate(statements) if "pg_advisory_xact_lock" in sql]
    assert engine.conn.statements[lock][1] == ([-3786935906701147602, 4325205642299247865],)  # s2's key, s1's
    lookup = next(i for i, sql in enumerate(statements) if "LEFT JOIN session_dims" in sql)
    ledger = next(i for i, sql in enumerate(statements) if "INSERT INTO ingest_dedup" in sql)
    assert lock < lookup < ledger
    assert engine.conn.statements[lookup][1] == (["s1", "s2"], False)  # legacy: no legacy-project probe


def _bound(session: str, owner: str = "dev@example.com", project: str = "proj") -> dict:
    return {
        "session_id": session,
        "owner_email": owner,
        "project_name": project,
        "subscription_type": "corporate",
        "project_origin": "default_project",
        "route_note": "folder_not_linked",
        "legacy_project": None,
    }


@pytest.mark.asyncio
async def test_a_session_guard_discard_answers_hook_events_with_reason_and_other_sessions_and_writes_nothing():
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s0", owner="other@example.com")]

    result = await _ingestor(engine).ingest_hook_events(_project_events(None, None), "dev@example.com", 0)

    assert json.loads(result.body) == {
        "accepted": 0,
        "dropped": 2,
        "reason": "session_owner_conflict",
        "other_sessions_dropped": 1,
    }
    assert engine.conn.sql("INSERT INTO") == []


@pytest.mark.asyncio
async def test_a_session_guard_discard_answers_otlp_with_partial_success():
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s1", project="proj")]
    event = _project_events("other-project")[0] | {"session_id": "s1"}
    body = b.logs_request(RESOURCE, [hb.as_otlp_log(event, T)]).SerializeToString()

    result = await _ingestor(engine).ingest_otlp("logs", body, "application/x-protobuf", user_email="dev@example.com")

    response = ExportLogsServiceResponse.FromString(result.body)
    assert response.partial_success.rejected_log_records == 1
    assert response.partial_success.error_message == "session_project_conflict"
    assert engine.conn.sql("INSERT INTO") == []


@pytest.mark.asyncio
async def test_an_evidence_request_binds_a_new_session_and_stamps_its_hook_rows():
    engine = FakeEngine()
    binding = SessionBinding("dest", "corporate", "linked_folder", "", "corporate", "v1")

    await _ingestor(engine).ingest_hook_events(_project_events("sent"), "dev@example.com", 0, binding=binding)

    (bind_args,) = engine.conn.sql("INSERT INTO session_dims")
    assert bind_args == (["s0"], "dev@example.com", "corporate", "linked_folder", "", ["dest"], None, None, None)
    (hook_args,) = engine.conn.sql("INSERT INTO hook_events")
    columns = dict(zip(HookRow._fields[2:], hook_args, strict=True))
    assert columns["codemie_project_name"] == ["dest"] and columns["client_policy_version"] == ["v1"]


@pytest.mark.asyncio
async def test_an_evidence_request_without_the_sender_is_a_final_discard_not_an_error():
    engine = FakeEngine()
    body = _logs_body(_api_request("s1", 1))

    result = await _ingestor(engine).ingest_otlp(
        "logs", body, "application/x-protobuf", binding=SessionBinding("p", "x"), evidence_session_id="s1"
    )

    assert ExportLogsServiceResponse.FromString(result.body).partial_success.error_message == "session_owner_conflict"
    assert engine.conn.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", ["", "  "])
async def test_a_missing_sender_never_skips_the_owner_check_of_a_bound_session(sender):
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s1", owner="dev@example.com")]

    result = await _ingestor(engine).ingest_otlp(
        "logs", _logs_body(_api_request("s1", 1)), "application/x-protobuf", user_email=sender
    )

    assert ExportLogsServiceResponse.FromString(result.body).partial_success.error_message == "session_owner_conflict"
    assert engine.conn.sql("INSERT INTO") == []


@pytest.mark.asyncio
async def test_the_owner_is_compared_as_an_email_not_as_a_case_sensitive_string():
    """The user lookups compare lower(email) (user_repository), so a differently cased sender is the owner."""
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s1", owner="Dev@Example.com")]

    result = await _ingestor(engine).ingest_otlp(
        "logs", _logs_body(_api_request("s1", 1)), "application/x-protobuf", user_email="dev@example.COM"
    )

    assert result.accepted == 1
    assert engine.conn.sql("INSERT INTO log_events")


@pytest.mark.asyncio
async def test_a_keyed_subagent_start_queues_the_days_of_the_later_starts_of_its_agent_id():
    """A keyed agent.subagent.start looks up the later starts of its (session_id, agent_id) after its ts and
    queues each one's day with SPAN_FACTS."""
    engine = FakeEngine()
    later_day = date(2026, 9, 25)
    engine.conn.later_start_days = [{"day": later_day, "session_id": "s1"}]
    events = [{"type": "agent.subagent.start", "session_id": "s1", "timestamp": T.isoformat(), "agent_id": "a1"}]

    await _ingestor(engine).ingest_hook_events(events, "dev@example.com", 0)

    (lookup_args,) = engine.conn.sql("AND f.ts > u.t")
    assert lookup_args == (["s1"], ["a1"], [T])
    (dirty_args,) = engine.conn.sql("INSERT INTO rollup_dirty")
    marks = dict(zip(zip(dirty_args[0], dirty_args[1], strict=True), dirty_args[2], strict=True))
    assert marks[(later_day, "s1")] == 4  # SPAN_FACTS


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


# ── project gate (EPMCDME-16025) ──────────────────────────────────────────────


def _gate(reason: str | None, canonical_project: str | None = None):
    seen: list[frozenset[str]] = []

    async def gate(projects: frozenset[str]) -> str | None:
        seen.append(projects)
        return reason

    gate.seen = seen  # type: ignore[attr-defined]
    gate.user_hash = None  # type: ignore[attr-defined]
    if canonical_project is not None:
        gate.canonical_project = canonical_project  # type: ignore[attr-defined]
    return gate


DISCARD_LOGGER = "codemie.repository.cli_analytics.gate_discards"


@pytest.fixture
def discard_lines(caplog: pytest.LogCaptureFixture):
    """The platform logger does not propagate to the root, so attach caplog's handler to the discard logger."""
    logger = logging.getLogger(DISCARD_LOGGER)
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger=DISCARD_LOGGER)
    yield lambda: [r.getMessage() for r in caplog.records if r.name == DISCARD_LOGGER]
    logger.removeHandler(caplog.handler)


def _discard_requests():
    """(signal, request, wire records, response type, rejected field)"""
    points = [b.number_point(T, 1, {}), b.number_point(T, float("nan"), {})]
    metrics = [
        b.sum_metric("a", points),
        b.histogram_metric("h"),
        b.summary_metric("s"),
        b.gauge_metric("g", points[:1]),
    ]
    sp = b.span("claude_code.tool", trace_id=bytes(16), span_id=bytes(8), start=T, end=T, attrs={})
    return [
        (
            "logs",
            b.logs_request(RESOURCE, [_api_request("s1", 1), _api_request("s1", 2)]),
            2,
            ExportLogsServiceResponse,
            "rejected_log_records",
        ),
        ("traces", b.traces_request(RESOURCE, [sp, sp, sp]), 3, ExportTraceServiceResponse, "rejected_spans"),
        ("metrics", b.metrics_request(RESOURCE, metrics), 5, ExportMetricsServiceResponse, "rejected_data_points"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("as_json", [False, True], ids=["protobuf", "json"])
@pytest.mark.parametrize("case", range(3), ids=["logs", "traces", "metrics"])
async def test_a_discarding_gate_writes_nothing_and_reports_the_wire_count(case, as_json, discard_lines):
    signal, request, wire, response_type, field = _discard_requests()[case]
    engine = FakeEngine()
    gate = _gate("cli_analytics_disabled")
    content_type = "application/json" if as_json else "application/x-protobuf"
    body = json.dumps(json_format.MessageToDict(request)).encode() if as_json else request.SerializeToString()

    result = await _ingestor(engine).ingest_otlp(signal, body, content_type, gate=gate)

    assert engine.conn.statements == []
    assert engine.acquire_timeouts == []
    assert result.accepted == 0
    response = json_format.Parse(result.body, response_type()) if as_json else response_type.FromString(result.body)
    assert getattr(response.partial_success, field) == wire
    assert response.partial_success.error_message == "cli_analytics_disabled"
    assert result.media_type == content_type.split(";")[0]

    (line,) = discard_lines()
    if signal == "logs":
        assert "session_count=1 session_ids=s1" in line
    else:
        assert "session_count=0" in line
        assert "session_ids" not in line


@pytest.mark.asyncio
async def test_an_empty_otlp_request_that_is_discarded_is_the_plain_success_response():
    engine = FakeEngine()

    result = await _ingestor(engine).ingest_otlp(
        "logs", b.logs_request(RESOURCE, []).SerializeToString(), "application/x-protobuf", gate=_gate("x")
    )

    assert result == IngestResult(body=b"", media_type="application/x-protobuf", accepted=0, discarded=True)


@pytest.mark.asyncio
async def test_a_discarding_gate_answers_hook_events_with_the_ndjson_body(discard_lines):
    engine = FakeEngine()
    events = [hb.minimal("agent.tool.end", "s1", "2026-09-29T10:59:41.512Z") for _ in range(3)]

    result = await _ingestor(engine).ingest_hook_events(
        events, "dev@example.com", 1, gate=_gate("project_context_required")
    )

    assert engine.conn.statements == []
    assert json.loads(result.body) == {"accepted": 0, "dropped": 3, "reason": "project_context_required"}
    assert result.media_type == "application/json"
    assert result.accepted == 0
    (line,) = discard_lines()
    assert "dropped=3 session_count=1 session_ids=s1" in line


@pytest.mark.asyncio
async def test_a_passing_gate_stores_as_before_and_sees_every_explicit_project():
    engine = FakeEngine()
    gate = _gate(None)
    body = b.logs_request(
        {**RESOURCE, "codemie_project_name": "p1"}, [_api_request("s1", 1, codemie_project_name="p2")]
    )

    result = await _ingestor(engine).ingest_otlp("logs", body.SerializeToString(), "application/x-protobuf", gate=gate)

    assert result.accepted == 1
    assert gate.seen == [frozenset({"p1", "p2"})]
    assert engine.conn.sql("INSERT INTO log_events")


@pytest.mark.asyncio
async def test_hook_events_pass_their_projects_to_the_gate():
    engine = FakeEngine()
    gate = _gate(None)
    events = [{**hb.minimal("agent.tool.end", "s1", "2026-09-29T10:59:41.512Z"), "codemie_project_name": "p1"}]

    result = await _ingestor(engine).ingest_hook_events(events, "dev@example.com", 1, gate=gate)

    assert gate.seen == [frozenset({"p1"})]
    assert result.accepted == 1


@pytest.mark.asyncio
async def test_a_failing_gate_propagates_and_nothing_is_written():
    engine = FakeEngine()

    async def gate(_projects):
        raise TelemetryStorageUnavailableError("policy lookup failed")

    with pytest.raises(TelemetryStorageUnavailableError):
        await _ingestor(engine).ingest_otlp(
            "logs", _logs_body(_api_request("s1", 1)), "application/x-protobuf", gate=gate
        )

    assert engine.conn.statements == []


# ── persisted project attribution (EPMCDME-16077) ─────────────────────────────

PROJECT_COLUMN = (
    6  # hook_events column list (the ledger key excluded): ts, session_id, type, prompt_id, user, dev, project
)


def _project_events(*names: str | None) -> list[dict]:
    events = []
    for index, name in enumerate(names):
        event = hb.minimal("agent.tool.end", f"s{index}", "2026-09-29T10:59:41.512Z")
        if name is not None:
            event["codemie_project_name"] = name
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_hook_events_are_stored_under_the_project_the_gate_accepted_them_for():
    engine = FakeEngine()
    events = _project_events("QA_CASE_CHECK", "qa_case_check", "Qa_Case_Check", None)

    result = await _ingestor(engine).ingest_hook_events(
        events, "dev@example.com", 1, gate=_gate(None, canonical_project="qa_case_check")
    )

    (hook_args,) = engine.conn.sql("INSERT INTO hook_events")
    assert hook_args[PROJECT_COLUMN] == ["qa_case_check"] * 4
    assert result.accepted == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", [None, "", "   "])
async def test_hook_events_without_a_project_are_stored_under_the_accepted_project(sent):
    engine = FakeEngine()
    events = _project_events(None)
    if sent is not None:
        events[0]["codemie_project_name"] = sent

    await _ingestor(engine).ingest_hook_events(
        events, "dev@example.com", 1, gate=_gate(None, canonical_project="qa_case_check")
    )

    (hook_args,) = engine.conn.sql("INSERT INTO hook_events")
    assert hook_args[PROJECT_COLUMN] == ["qa_case_check"]


@pytest.mark.asyncio
async def test_hook_events_without_a_project_delivered_as_otlp_logs_get_the_accepted_project():
    engine = FakeEngine()
    record = hb.as_otlp_log(_project_events(None)[0], T)
    body = b.logs_request(RESOURCE, [record]).SerializeToString()

    await _ingestor(engine).ingest_otlp(
        "logs", body, "application/x-protobuf", gate=_gate(None, canonical_project="qa_case_check")
    )

    (hook_args,) = engine.conn.sql("INSERT INTO hook_events")
    assert hook_args[PROJECT_COLUMN] == ["qa_case_check"]


@pytest.mark.asyncio
async def test_the_stored_project_is_the_canonical_name_for_hook_events_delivered_as_otlp_logs():
    engine = FakeEngine()
    record = hb.as_otlp_log(_project_events("QA_CASE_CHECK")[0], T)
    body = b.logs_request(RESOURCE, [record]).SerializeToString()

    result = await _ingestor(engine).ingest_otlp(
        "logs", body, "application/x-protobuf", gate=_gate(None, canonical_project="qa_case_check")
    )

    (hook_args,) = engine.conn.sql("INSERT INTO hook_events")
    assert hook_args[PROJECT_COLUMN] == ["qa_case_check"]
    assert result.accepted == 1


@pytest.mark.asyncio
async def test_event_identities_do_not_depend_on_the_canonical_name():
    # No event_id: the identity is a hash of the event as sent, which must not change with the stored name.
    events = _project_events("QA_CASE_CHECK", "qa_case_check")
    plain, canonical = FakeEngine(), FakeEngine()

    await _ingestor(plain).ingest_hook_events(events, "dev@example.com", 1, gate=_gate(None))
    await _ingestor(canonical).ingest_hook_events(
        events, "dev@example.com", 1, gate=_gate(None, canonical_project="qa_case_check")
    )

    assert canonical.conn.sql("INSERT INTO ingest_dedup") == plain.conn.sql("INSERT INTO ingest_dedup")
    assert canonical.conn.sql("INSERT INTO hook_events")[0][PROJECT_COLUMN] == ["qa_case_check"] * 2
    assert plain.conn.sql("INSERT INTO hook_events")[0][PROJECT_COLUMN] == ["QA_CASE_CHECK", "qa_case_check"]


@pytest.mark.asyncio
async def test_a_gate_without_a_canonical_project_leaves_the_stored_name_as_sent():
    with_gate, without_gate = FakeEngine(), FakeEngine()

    await _ingestor(with_gate).ingest_hook_events(
        _project_events("QA_CASE_CHECK"), "dev@example.com", 1, gate=_gate(None)
    )
    await _ingestor(without_gate).ingest_hook_events(_project_events("QA_CASE_CHECK"), "dev@example.com", 2)

    for engine in (with_gate, without_gate):
        (hook_args,) = engine.conn.sql("INSERT INTO hook_events")
        assert hook_args[PROJECT_COLUMN] == ["QA_CASE_CHECK"]


@pytest.mark.asyncio
async def test_other_log_records_keep_their_sent_attributes_untouched():
    engine = FakeEngine()
    body = _logs_body(_api_request("s1", 1, codemie_project_name="QA_CASE_CHECK"))

    await _ingestor(engine).ingest_otlp(
        "logs", body, "application/x-protobuf", gate=_gate(None, canonical_project="qa_case_check")
    )

    (log_args,) = engine.conn.sql("INSERT INTO log_events")
    assert any("QA_CASE_CHECK" in (attrs or "") for attrs in log_args[-1])


# ── destination resolved under the session lock; boundary, stale and sender order (review CR-001..006) ────


class _Resolving:
    """An evidence gate that defers the destination to the session lock (`resolve_session`)."""

    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple] = []
        self.binding = None

    async def __call__(self, explicit):
        return None

    async def resolve_session(self, bound, legacy_project, hook_repo_remote):
        self.calls.append((bound, legacy_project, hook_repo_remote))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.asyncio
async def test_the_destination_is_resolved_after_the_lock_with_the_stored_binding_and_the_hook_remote():
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s0", project="bound-proj")]
    gate = _Resolving(SessionBinding("bound-proj", "corporate", "default_project", "folder_not_linked"))
    event = _project_events(None)[0] | {"repo_remote": "git@h:o/r"}

    await _ingestor(engine).ingest_hook_events([event], "dev@example.com", 0, gate=gate, evidence_session_id="s0")

    ((bound, legacy, remote),) = gate.calls
    assert (bound.project_name, bound.subscription_type, legacy, remote) == (
        "bound-proj",
        "corporate",
        None,
        "git@h:o/r",
    )
    order = [sql for sql, _ in engine.conn.statements]
    assert next(i for i, s in enumerate(order) if "pg_advisory_xact_lock" in s) < next(
        i for i, s in enumerate(order) if "INSERT INTO hook_events" in s
    )
    (hook_args,) = engine.conn.sql("INSERT INTO hook_events")
    assert dict(zip(HookRow._fields[2:], hook_args, strict=True))["codemie_project_name"] == ["bound-proj"]


@pytest.mark.asyncio
async def test_a_resolver_discard_writes_nothing_and_counts_the_reason():
    engine = FakeEngine()
    before = ingestor_module.SESSION_DISCARDS["session_project_conflict"]

    result = await _ingestor(engine).ingest_hook_events(
        _project_events(None),
        "dev@example.com",
        0,
        gate=_Resolving("session_project_conflict"),
        evidence_session_id="s0",
    )

    assert json.loads(result.body)["reason"] == "session_project_conflict"
    assert engine.conn.sql("INSERT INTO") == []
    assert ingestor_module.SESSION_DISCARDS["session_project_conflict"] == before + 1


@pytest.mark.asyncio
async def test_another_owner_is_discarded_before_the_destination_is_resolved():
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s0", owner="other@example.com")]
    gate = _Resolving(SessionBinding("p", "corporate"))

    result = await _ingestor(engine).ingest_hook_events(
        _project_events(None), "dev@example.com", 0, gate=gate, evidence_session_id="s0"
    )

    assert json.loads(result.body)["reason"] == "session_owner_conflict"
    assert gate.calls == []


@pytest.mark.asyncio
async def test_a_retryable_resolution_rolls_back_and_surfaces_as_unavailable():
    engine = FakeEngine()
    gate = _Resolving(TelemetryStorageUnavailableError("default_project_unavailable"))

    with pytest.raises(TelemetryStorageUnavailableError, match="default_project_unavailable"):
        await _ingestor(engine).ingest_hook_events(
            _project_events(None), "dev@example.com", 0, gate=gate, evidence_session_id="s0"
        )

    assert engine.conn.sql("INSERT INTO") == []


@pytest.mark.asyncio
async def test_a_stale_policy_loses_to_the_session_boundary_and_wins_over_everything_after_it():
    """Spec 4: boundary, classification, stale, binding."""
    engine = FakeEngine()
    ingestor = _ingestor(engine)
    gate = _Resolving(SessionBinding("p", "corporate"))

    both = await ingestor.ingest_hook_events(
        _project_events(None), "dev@example.com", 0, gate=gate, evidence_session_id="other", stale_policy_version="v9"
    )
    assert json.loads(both.body)["reason"] == "evidence_session_conflict"

    with pytest.raises(StalePolicyError) as stale:
        await ingestor.ingest_hook_events(
            _project_events(None), "dev@example.com", 0, gate=gate, evidence_session_id="s0", stale_policy_version="v9"
        )
    assert stale.value.policy_version == "v9"
    assert gate.calls == [] and engine.conn.statements == []


@pytest.mark.asyncio
async def test_a_declared_session_with_session_less_rows_is_a_boundary_discard_not_a_bypass():
    """Rows without a session id carry '' and fail the boundary; a request without rows stores nothing."""
    engine = FakeEngine()
    ingestor = _ingestor(engine)
    sessionless = hb.minimal("agent.tool.end", "", "2026-09-29T10:59:41.512Z")

    result = await ingestor.ingest_hook_events([sessionless], "dev@example.com", 0, evidence_session_id="s0")
    empty = await ingestor.ingest_otlp(
        "metrics",
        b.metrics_request(RESOURCE, []).SerializeToString(),
        "application/x-protobuf",
        evidence_session_id="s0",
    )

    assert json.loads(result.body)["reason"] == "evidence_session_conflict"
    assert empty.accepted == 0
    assert engine.conn.statements == []
