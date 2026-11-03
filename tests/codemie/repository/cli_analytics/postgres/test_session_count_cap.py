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

"""The distinct-session cap of one ingest request (EPMCDME-16440, review 2505288).

Every distinct session of a request takes an advisory lock in one transaction, so a request over
MAX_SESSIONS_PER_REQUEST is refused (HTTP 413) at the public ingest entry, before the gate, a connection, a lock,
the ledger or any write. The tests drive the real ingestor over the recording connection.
"""

from __future__ import annotations

import pytest

from codemie.repository.cli_analytics.ports import TelemetryPayloadTooLargeError
from codemie.repository.cli_analytics.postgres.session_binding import MAX_SESSIONS_PER_REQUEST as CAP
from codemie.rest_api.routers.cli_analytics import _ingest_http_error
from tests.codemie.repository.cli_analytics.postgres.test_ingestor import (
    FakeEngine,
    _api_request,
    _ingestor,
    _logs_body,
    _project_events,
)
from tests.codemie.repository.cli_analytics.postgres.test_legacy_bound_destination import (
    OWNER,
    LegacyGate,
    ROUTES,
    body,
)


def _untouched(engine: FakeEngine) -> None:
    assert engine.acquire_timeouts == []  # no connection, hence no transaction, lock or ledger statement
    assert engine.conn.sql("INSERT INTO") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ("logs", "metrics"))  # the traces builder keys spans by one byte
async def test_thousands_of_distinct_sessions_are_refused_before_any_sql(route):
    engine, gate = FakeEngine(), LegacyGate("ProjectA")
    sessions = [f"s{i}" for i in range(3000)]

    with pytest.raises(TelemetryPayloadTooLargeError):
        await _ingestor(engine).ingest_otlp(
            route, body(route, *sessions), "application/x-protobuf", gate, user_email=OWNER
        )

    _untouched(engine)
    assert gate.validated == []


@pytest.mark.asyncio
async def test_thousands_of_distinct_hook_sessions_are_refused_before_any_sql():
    engine = FakeEngine()

    with pytest.raises(TelemetryPayloadTooLargeError):
        await _ingestor(engine).ingest_hook_events(
            _project_events(*[None] * 3000), OWNER, 0, gate=LegacyGate("ProjectA")
        )

    _untouched(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_the_limit_is_accepted_and_one_more_session_is_refused(route):
    ingestor = _ingestor(at := FakeEngine())
    result = await ingestor.ingest_otlp(
        route,
        body(route, *[f"s{i}" for i in range(CAP)]),
        "application/x-protobuf",
        LegacyGate("ProjectA"),
        user_email=OWNER,
    )
    assert result.accepted != 0 or at.conn.sql("INSERT INTO") != []  # stored: the limit itself passes

    over = FakeEngine()
    with pytest.raises(TelemetryPayloadTooLargeError):
        await _ingestor(over).ingest_otlp(
            route,
            body(route, *[f"s{i}" for i in range(CAP + 1)]),
            "application/x-protobuf",
            LegacyGate("ProjectA"),
            user_email=OWNER,
        )
    _untouched(over)


@pytest.mark.asyncio
async def test_hook_limit_and_limit_plus_one():
    ok = FakeEngine()
    await _ingestor(ok).ingest_hook_events(_project_events(*[None] * CAP), OWNER, 0, gate=LegacyGate("ProjectA"))
    assert ok.conn.sql("INSERT INTO") != []

    over = FakeEngine()
    with pytest.raises(TelemetryPayloadTooLargeError):
        await _ingestor(over).ingest_hook_events(
            _project_events(*[None] * (CAP + 1)), OWNER, 0, gate=LegacyGate("ProjectA")
        )
    _untouched(over)


@pytest.mark.asyncio
async def test_duplicate_records_of_a_session_do_not_consume_the_count():
    engine = FakeEngine()
    records = [
        _api_request(f"s{i % CAP}", i) for i in range(CAP * 5)
    ]  # 5x the cap in records, exactly the cap in sessions

    await _ingestor(engine).ingest_otlp("logs", _logs_body(*records), "application/x-protobuf", None, user_email=OWNER)

    assert engine.conn.sql("INSERT INTO") != []


@pytest.mark.asyncio
async def test_an_ordinary_single_session_request_and_the_ungated_path_are_unchanged():
    engine = FakeEngine()

    await _ingestor(engine).ingest_otlp(
        "logs", _logs_body(_api_request("s1", 1)), "application/x-protobuf", None, user_email=OWNER
    )

    assert engine.conn.sql("INSERT INTO") != []


def test_the_refusal_answers_413_not_a_retryable_503():
    exc = _ingest_http_error(TelemetryPayloadTooLargeError("Too many distinct sessions in one request"))
    assert exc.status_code == 413
