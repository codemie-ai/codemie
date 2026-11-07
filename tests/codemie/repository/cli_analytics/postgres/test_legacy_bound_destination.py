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

"""A legacy request that meets an already bound session (EPMCDME-16440, review f-c22d973e1400) and the origin of an
adopted legacy project (review f-69413b32be4f), through the ingestor over a recording connection.

The legacy gate accepts a request for the header project and, since this change, validates the bound destination it
writes; the ingestor never binds or reroutes with it. The fake gate answers like the service gate: it hands over its
canonical project and a recording `validate_destination`, and nothing else (no resolver, no binding).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

from codemie.repository.cli_analytics.ports import SessionBinding, TelemetryStorageUnavailableError
from codemie.repository.cli_analytics.postgres.otlp import HookRow
from tests.codemie.repository.cli_analytics.postgres.test_ingestor import (
    FakeEngine,
    _api_request,
    _bound,
    _ingestor,
    _project_events,
)
from tests.codemie.repository.cli_analytics.support import otlp_builders as b

OWNER = "dev@example.com"
T = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
RESOURCE = {"service.name": "claude-code"}
ROUTES = ("logs", "metrics", "traces")


class LegacyGate:
    """The shape of the service's legacy gate: accepts for `canonical_project`, validates a bound destination."""

    def __init__(self, canonical_project: str = "ProjectA", verdict=None) -> None:
        self.canonical_project = canonical_project
        self.verdict = verdict
        self.validated: list[tuple[SessionBinding, bool]] = []
        self.user_hash = None

    async def __call__(self, explicit):
        return None

    async def validate_destination(self, binding: SessionBinding, bound: bool = False) -> str | None:
        self.validated.append((binding, bound))
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


def body(route: str, *sessions: str) -> bytes:
    if route == "logs":
        records = [_api_request(s, 1) for s in sessions]
        return b.logs_request(RESOURCE, records).SerializeToString()
    if route == "metrics":
        points = [b.number_point(T, 0.5, {"session.id": s, "model": "m"}) for s in sessions]
        return b.metrics_request(RESOURCE, [b.sum_metric("claude_code.cost.usage", points)]).SerializeToString()
    spans = [
        b.span(
            "claude_code.interaction",
            trace_id=bytes([i + 1]) * 16,
            span_id=bytes([i + 1]) * 8,
            start=T,
            end=T,
            attrs={"session.id": s},
        )
        for i, s in enumerate(sessions)
    ]
    return b.traces_request(RESOURCE, spans).SerializeToString()


async def send(route: str, engine: FakeEngine, gate: LegacyGate, *sessions: str):
    return await _ingestor(engine).ingest_otlp(
        route, body(route, *sessions), "application/x-protobuf", gate, user_email=OWNER
    )


def reason_of(route: str, result) -> str | None:
    response = {
        "logs": ExportLogsServiceResponse,
        "metrics": ExportMetricsServiceResponse,
        "traces": ExportTraceServiceResponse,
    }[route].FromString(result.body)
    return response.partial_success.error_message or None


# --- the request's project may not smuggle writes to a different bound project ------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_a_request_for_project_a_never_writes_a_session_bound_to_project_b(route):
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s1", owner=OWNER, project="ProjectB")]
    gate = LegacyGate("ProjectA")

    result = await send(route, engine, gate, "s1")

    assert reason_of(route, result) == "session_project_conflict"
    assert engine.conn.sql("INSERT INTO") == []
    assert gate.validated == []  # decided before any validation or write


@pytest.mark.asyncio
async def test_hook_events_for_project_a_on_a_session_bound_to_project_b_are_a_conflict_too():
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s0", owner=OWNER, project="ProjectB")]

    result = await _ingestor(engine).ingest_hook_events(_project_events(None), OWNER, 0, gate=LegacyGate("ProjectA"))

    assert json.loads(result.body)["reason"] == "session_project_conflict"
    assert engine.conn.sql("INSERT INTO") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_one_session_of_another_project_discards_the_mixed_request(route):
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s1", owner=OWNER, project="ProjectA"), _bound("s2", owner=OWNER, project="B")]

    result = await send(route, engine, LegacyGate("ProjectA"), "s1", "s2")

    assert reason_of(route, result) == "session_project_conflict"
    assert engine.conn.sql("INSERT INTO") == []


# --- the bound destination the request does write is validated ----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "verdict", ["session_project_unavailable", "cli_analytics_disabled", "personal_destination_unavailable"]
)
async def test_a_bound_destination_the_gate_rejects_is_a_final_reasoned_discard(route, verdict):
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s1", owner=OWNER, project="ProjectA")]
    gate = LegacyGate("ProjectA", verdict)

    result = await send(route, engine, gate, "s1")

    assert reason_of(route, result) == verdict
    assert engine.conn.sql("INSERT INTO") == []
    ((binding, bound),) = gate.validated
    assert (binding.project_name, bound) == ("ProjectA", True)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_a_failed_lookup_of_the_bound_destination_is_retried_not_discarded(route):
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s1", owner=OWNER, project="ProjectA")]
    gate = LegacyGate("ProjectA", TelemetryStorageUnavailableError("lookup"))

    with pytest.raises(TelemetryStorageUnavailableError):
        await send(route, engine, gate, "s1")

    assert engine.conn.sql("INSERT INTO hook_events") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_the_same_project_in_another_case_is_written_once_validated_and_keeps_its_binding(route):
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s1", owner=OWNER, project="ProjectA")]
    gate = LegacyGate("projecta")  # the gate's canonical name is the stored one; the match is case-insensitive

    result = await send(route, engine, gate, "s1")

    assert reason_of(route, result) is None
    assert len(gate.validated) == 1 and gate.validated[0][1] is True
    assert engine.conn.sql("INSERT INTO session_dims") == []  # nothing is bound, rebound or rerouted
    assert engine.conn.sql("INSERT INTO ingest_dedup") != []  # and the rows were stored


@pytest.mark.asyncio
async def test_hook_events_on_the_bound_project_are_validated_and_stored_under_it():
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s0", owner=OWNER, project="ProjectA")]
    gate = LegacyGate("ProjectA")

    await _ingestor(engine).ingest_hook_events(_project_events(None), OWNER, 0, gate=gate)

    assert [bound for _, bound in gate.validated] == [True]
    (hook_args,) = engine.conn.sql("INSERT INTO hook_events")
    assert dict(zip(HookRow._fields[2:], hook_args, strict=True))["codemie_project_name"] == ["ProjectA"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_a_legacy_only_session_is_neither_validated_nor_bound(route):
    """A validator on the gate does not make this an evidence request: no binding is created for the session."""
    engine = FakeEngine()  # no stored binding for s1
    gate = LegacyGate("ProjectA", "session_project_unavailable")

    result = await send(route, engine, gate, "s1")

    assert reason_of(route, result) is None
    assert gate.validated == []
    assert engine.conn.sql("INSERT INTO session_dims") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_another_owner_is_still_discarded_first(route):
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s1", owner="other@example.com", project="ProjectA")]
    gate = LegacyGate("ProjectA")

    result = await send(route, engine, gate, "s1")

    assert reason_of(route, result) == "session_owner_conflict"
    assert gate.validated == []


@pytest.mark.asyncio
async def test_a_mixed_request_validates_only_the_bound_sessions_and_stores_the_legacy_one_unbound():
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s1", owner=OWNER, project="ProjectA")]
    gate = LegacyGate("ProjectA")

    result = await send("logs", engine, gate, "s1", "s2")

    assert reason_of("logs", result) is None
    assert [b_.project_name for b_, _ in gate.validated] == ["ProjectA"]
    assert engine.conn.sql("INSERT INTO session_dims") == []


# --- evidence regressions: the same ingest with an evidence gate is unchanged -----------------------------


class _Evidence:
    """An evidence gate: resolver and validator, no canonical project."""

    def __init__(self, outcome: SessionBinding, verdict=None) -> None:
        self.outcome, self.verdict, self.binding = outcome, verdict, None
        self.validated: list[tuple[SessionBinding, bool]] = []
        self.user_hash = None

    async def __call__(self, explicit):
        return None

    async def resolve_session(self, bound, legacy_project, hook_repo_remote):
        self.binding = self.outcome
        return self.outcome

    async def validate_destination(self, binding, bound=False):
        self.validated.append((binding, bound))
        return self.verdict


@pytest.mark.asyncio
async def test_an_evidence_request_keeps_its_bound_project_and_is_not_compared_with_a_request_project():
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s0", owner=OWNER, project="ProjectB")]
    evidence = _Evidence(SessionBinding("ProjectB", "corporate", "linked_folder"))

    result = await _ingestor(engine).ingest_hook_events(
        _project_events(None), OWNER, 0, gate=evidence, evidence_session_id="s0"
    )

    assert result.accepted == 1


@pytest.mark.asyncio
async def test_an_evidence_request_whose_bound_destination_is_rejected_is_discarded():
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s0", owner=OWNER, project="ProjectB")]
    evidence = _Evidence(SessionBinding("Candidate", "corporate", "default_project"), "session_project_unavailable")

    result = await _ingestor(engine).ingest_hook_events(
        _project_events(None), OWNER, 0, gate=evidence, evidence_session_id="s0"
    )

    assert json.loads(result.body)["reason"] == "session_project_unavailable"
    assert engine.conn.sql("INSERT INTO hook_events") == []


# --- AC8: the origin of an adopted legacy project is stored for every classification -------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["personal", "corporate", "codemie", "unknown"])
async def test_an_adopted_legacy_project_is_stored_with_the_legacy_project_origin(subscription):
    engine = FakeEngine()
    row = _bound("s0", owner=OWNER, project="Legacy") | {"owner_email": None, "legacy_project": "Legacy"}
    engine.conn.bindings = [row]
    # The candidate names the legacy project too, with a route of its own: the stored origin is still legacy_project.
    evidence = _Evidence(SessionBinding("Legacy", subscription, "profile", "folder_not_linked"))

    await _ingestor(engine).ingest_hook_events(_project_events(None), OWNER, 0, gate=evidence, evidence_session_id="s0")

    (bind_args,) = engine.conn.sql("INSERT INTO session_dims")
    assert bind_args[2:5] == (subscription, "legacy_project", "")  # subscription, origin, route note
    (hook_args,) = engine.conn.sql("INSERT INTO hook_events")
    columns = dict(zip(HookRow._fields[2:], hook_args, strict=True))
    assert (columns["subscription_type"], columns["project_origin"]) == ([subscription], ["legacy_project"])


@pytest.mark.asyncio
async def test_a_consent_binding_keeps_its_canonical_project_and_origin_next_to_legacy_rows():
    engine = FakeEngine()
    row = _bound("s0", owner=OWNER, project="legacy") | {"owner_email": None, "legacy_project": "legacy"}
    engine.conn.bindings = [row]
    evidence = _Evidence(
        SessionBinding("Legacy", "personal", "linked_folder", contract_version="1", work_consent="yes")
    )

    await _ingestor(engine).ingest_hook_events(_project_events(None), OWNER, 0, gate=evidence, evidence_session_id="s0")

    (bind_args,) = engine.conn.sql("INSERT INTO session_dims")
    assert bind_args[2:5] == ("personal", "linked_folder", "")


# --- a legacy request never appends to a session bound by the publication contract ---------------------------

NEW_CONTRACT = [("1", None), ("1", "explicit-folder")]  # N0 (no consent), N1 (explicit folder consent)


def _contract_bound(session: str, contract: tuple[str, str | None], project: str = "ProjectA") -> dict:
    return {
        **_bound(session, owner=OWNER, project=project),
        "contract_version": contract[0],
        "work_consent": contract[1],
        "funding_route": "api_based",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("contract", NEW_CONTRACT)
async def test_a_legacy_request_never_appends_to_a_contract_bound_session(route, contract):
    engine = FakeEngine()
    engine.conn.bindings = [_contract_bound("s1", contract)]
    gate = LegacyGate("ProjectA")

    result = await send(route, engine, gate, "s1")

    assert reason_of(route, result) == "session_contract_conflict"
    assert engine.conn.sql("INSERT INTO") == []  # no rows, ledger, binding or funding write
    assert engine.conn.sql("UPDATE") == []
    assert gate.validated == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_a_mixed_legacy_and_contract_bound_batch_writes_nothing(route):
    engine = FakeEngine()
    engine.conn.bindings = [_bound("s1", owner=OWNER, project="ProjectA"), _contract_bound("s2", NEW_CONTRACT[1])]

    result = await send(route, engine, LegacyGate("ProjectA"), "s1", "s2")

    assert reason_of(route, result) == "session_contract_conflict"
    assert engine.conn.sql("INSERT INTO") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("contract", NEW_CONTRACT)
async def test_legacy_hook_events_never_append_to_a_contract_bound_session(contract):
    engine = FakeEngine()
    engine.conn.bindings = [_contract_bound("s0", contract)]

    result = await _ingestor(engine).ingest_hook_events(_project_events(None), OWNER, 0, gate=LegacyGate("ProjectA"))

    assert json.loads(result.body)["reason"] == "session_contract_conflict"
    assert engine.conn.sql("INSERT INTO") == []
