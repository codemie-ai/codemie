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

"""End-to-end project gate of the four CLI analytics ingest endpoints (EPMCDME-16025).

Real router, real gate and real PostgreSQL ingestor over a recording connection; only the project
policy lookup is faked, with an isolated enabled project, a disabled one and a soft-deleted one.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from google.protobuf import json_format
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

import codemie.rest_api.routers.cli_analytics as ingest_router
from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.cli_analytics.factory import CliAnalyticsStorage
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from tests.codemie.repository.cli_analytics.postgres.test_ingestor import FakeEngine
from tests.codemie.repository.cli_analytics.support import hook_event_builders as hb
from tests.codemie.repository.cli_analytics.support import otlp_builders as b

ORIGINAL_ENSURE_ENABLED = ingest_router._ensure_enabled  # the fixture replaces it with a no-op
GATE = "codemie.service.analytics.cli_analytics_project_gate"
BASE = "/v1/analytics/cli-analytics"
T = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
KEY = "codemie_project_name"
ENABLED, DISABLED, DELETED = "iso-enabled", "iso-disabled", "iso-deleted"
PROTOBUF, JSON, NDJSON = "application/x-protobuf", "application/json", "application/x-ndjson"
RESPONSES = {
    "logs": (ExportLogsServiceResponse, "rejected_log_records", "log_events"),
    "traces": (ExportTraceServiceResponse, "rejected_spans", "spans"),
    "metrics": (ExportMetricsServiceResponse, "rejected_data_points", "metric_points"),
}


def make_user(projects: list[str]) -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="u-1", email="dev@example.com", project_names=projects)


@dataclass
class Env:
    client: TestClient
    engine: FakeEngine
    apps: dict[str, Application]
    lookup: AsyncMock
    users: dict


@pytest.fixture
def env():
    apps = {
        ENABLED: Application(id=ENABLED, name=ENABLED, cli_analytics_enabled=True),
        DISABLED: Application(id=DISABLED, name=DISABLED, cli_analytics_enabled=False),
        DELETED: Application(id=DELETED, name=DELETED, cli_analytics_enabled=True, deleted_at=T),
    }
    engine = FakeEngine()
    storage = CliAnalyticsStorage(reader=None, ingestor=PostgresTelemetryIngestor(engine))  # type: ignore[arg-type]
    users = {"user": make_user([ENABLED, DISABLED, DELETED])}

    def authenticate_as(request: Request) -> User:
        request.state.user = users["user"]
        return users["user"]

    @asynccontextmanager
    async def session():
        yield object()

    app = FastAPI()
    app.include_router(ingest_router.router)
    app.dependency_overrides[authenticate] = authenticate_as
    lookup = AsyncMock(side_effect=lambda _session, name: apps.get(name.lower()))
    with (
        patch.object(ingest_router, "get_cli_analytics_storage", return_value=storage),
        patch.object(ingest_router, "_ensure_enabled", return_value=None),
        patch(f"{GATE}.get_async_session", session),
        patch(f"{GATE}.application_repository.aget_active_by_name_case_insensitive", lookup),
    ):
        yield Env(TestClient(app), engine, apps, lookup, users)


# ── request builders ──────────────────────────────────────────────────────────


@dataclass
class Sent:
    path: str
    body: bytes
    content_type: str
    wire: int
    signal: str  # "logs" | "traces" | "metrics" | "hooks"

    def post(self, env: Env, project: str | None = ENABLED):
        headers = {"Content-Type": self.content_type}
        if project is not None:
            headers["X-CodeMie-Project"] = project
        return env.client.post(self.path, content=self.body, headers=headers)


def _otlp_body(request, content_type: str) -> bytes:
    if content_type == JSON:
        return json.dumps(json_format.MessageToDict(request)).encode()
    return request.SerializeToString()


def _resource(project: str | None) -> dict:
    return {"service.name": "claude-code", **({KEY: project} if project else {})}


def _log(i: int, project: str | None = None):
    attrs = {"event.name": "api_request", "session.id": "s1", "event.sequence": i, "cost_usd": 0.5}
    return b.log_record(T, {**attrs, **({KEY: project} if project else {})})


def _span(i: int, project: str | None = None):
    return b.span(
        "claude_code.tool",
        trace_id=bytes([i]) * 16,
        span_id=bytes([i]) * 8,
        start=T,
        end=T,
        attrs={"session.id": "s1", **({KEY: project} if project else {})},
    )


def _point(value: float = 1.0, project: str | None = None):
    return b.number_point(T, value, {"session.id": "s1", **({KEY: project} if project else {})})


def logs(content_type: str, *, resource: str | None = None, records=None) -> Sent:
    records = records if records is not None else [_log(1), _log(2)]
    request = b.logs_request(_resource(resource), records)
    return Sent(f"{BASE}/logs", _otlp_body(request, content_type), content_type, len(records), "logs")


def traces(content_type: str, *, resource: str | None = None, spans=None) -> Sent:
    spans = spans if spans is not None else [_span(1), _span(2)]
    request = b.traces_request(_resource(resource), spans)
    return Sent(f"{BASE}/traces", _otlp_body(request, content_type), content_type, len(spans), "traces")


def metrics(content_type: str, *, resource: str | None = None, metric_list=None, wire: int = 3) -> Sent:
    metric_list = (
        metric_list
        if metric_list is not None
        else [b.sum_metric("m", [_point(), _point(2.0)]), b.gauge_metric("g", [_point()])]
    )
    request = b.metrics_request(_resource(resource), metric_list)
    return Sent(f"{BASE}/metrics", _otlp_body(request, content_type), content_type, wire, "metrics")


def hooks(events: list[dict] | None = None, raw: bytes | None = None, wire: int | None = None) -> Sent:
    events = (
        events
        if events is not None
        else [hb.minimal("agent.tool.end", f"s{i}", "2026-09-29T10:59:41.512Z") for i in range(3)]
    )
    body = raw if raw is not None else "\n".join(json.dumps(e) for e in events).encode()
    return Sent(f"{BASE}/event-hooks", body, NDJSON, wire if wire is not None else len(events), "hooks")


def with_project(event: dict, project: str) -> dict:
    return {**event, KEY: project}


# One positive and one negative-capable payload per endpoint and wire format.
FORMATS = [
    pytest.param(lambda: logs(PROTOBUF), id="logs-protobuf"),
    pytest.param(lambda: logs(JSON), id="logs-json"),
    pytest.param(lambda: traces(PROTOBUF), id="traces-protobuf"),
    pytest.param(lambda: traces(JSON), id="traces-json"),
    pytest.param(lambda: metrics(PROTOBUF), id="metrics-protobuf"),
    pytest.param(lambda: metrics(JSON), id="metrics-json"),
    pytest.param(lambda: hooks(), id="hooks-ndjson"),
]


def assert_discard(response, sent: Sent, reason: str) -> None:
    assert response.status_code == 200, response.text
    if sent.signal == "hooks":
        assert response.json() == {"accepted": 0, "dropped": sent.wire, "reason": reason}
        return
    response_type, field, _ = RESPONSES[sent.signal]
    if sent.content_type == JSON:
        message = json_format.Parse(response.content, response_type())
    else:
        message = response_type.FromString(response.content)
    assert getattr(message.partial_success, field) == sent.wire
    assert message.partial_success.error_message == reason


def stored_tables(env: Env) -> list[str]:
    return [sql for sql, _ in env.engine.conn.statements if sql.startswith("INSERT INTO") and "ingest_dedup" not in sql]


def assert_nothing_written(env: Env) -> None:
    assert env.engine.conn.statements == []
    assert env.engine.acquire_timeouts == []


# ── enabled / disabled / missing context ──────────────────────────────────────


@pytest.mark.parametrize("make", FORMATS)
def test_enabled_project_is_stored_normally(env, make):
    sent = make()

    response = sent.post(env, ENABLED)

    assert response.status_code == 200
    assert stored_tables(env), "an enabled project's data must reach the storage"
    if sent.signal == "hooks":
        assert response.json() == {}


@pytest.mark.parametrize("make", FORMATS)
def test_disabled_project_is_discarded_with_the_wire_count(env, make):
    sent = make()

    assert_discard(sent.post(env, DISABLED), sent, "cli_analytics_disabled")

    assert_nothing_written(env)


@pytest.mark.parametrize("make", FORMATS)
@pytest.mark.parametrize(
    "header", [None, "", "no-such-project", DELETED], ids=["missing", "blank", "unknown", "deleted"]
)
def test_missing_unknown_or_deleted_project_is_context_required(env, make, header):
    sent = make()

    assert_discard(sent.post(env, header), sent, "project_context_required")

    assert_nothing_written(env)


@pytest.mark.parametrize("make", FORMATS)
def test_a_sender_without_access_to_the_project_is_context_required(env, make):
    env.users["user"] = make_user(["some-other-project"])
    sent = make()

    assert_discard(sent.post(env, ENABLED), sent, "project_context_required")

    assert_nothing_written(env)


@pytest.mark.parametrize("make", FORMATS)
def test_header_project_is_matched_case_insensitively(env, make):
    assert make().post(env, ENABLED.upper()).status_code == 200
    assert stored_tables(env)


# ── conflicting explicit project values ───────────────────────────────────────

OTHER = DISABLED
CONFLICTS = [
    pytest.param(lambda ct: logs(ct, resource=OTHER), id="logs-resource"),
    pytest.param(lambda ct: logs(ct, records=[_log(1), _log(2, OTHER)]), id="logs-record-attribute"),
    pytest.param(
        lambda ct: logs(ct, records=[hb.as_otlp_log({**hb.usage_request(), KEY: OTHER}, T)]), id="logs-usage-event"
    ),
    pytest.param(lambda ct: traces(ct, resource=OTHER), id="traces-resource"),
    pytest.param(lambda ct: traces(ct, spans=[_span(1), _span(2, OTHER)]), id="traces-span-attribute"),
    pytest.param(lambda ct: metrics(ct, resource=OTHER), id="metrics-resource"),
    pytest.param(
        lambda ct: metrics(ct, metric_list=[b.sum_metric("m", [_point(project=OTHER)])], wire=1), id="metrics-sum-point"
    ),
    pytest.param(
        lambda ct: metrics(ct, metric_list=[b.histogram_metric("h", {KEY: OTHER})], wire=1),
        id="metrics-skipped-histogram",
    ),
    pytest.param(
        lambda ct: metrics(ct, metric_list=[b.sum_metric("m", [_point(float("nan"), OTHER)])], wire=1),
        id="metrics-nan-point",
    ),
]


@pytest.mark.parametrize("content_type", [PROTOBUF, JSON])
@pytest.mark.parametrize("build", CONFLICTS)
def test_a_conflicting_project_anywhere_in_the_batch_discards_all_of_it(env, build, content_type):
    sent = build(content_type)

    assert_discard(sent.post(env, ENABLED), sent, "project_context_conflict")

    assert_nothing_written(env)


@pytest.mark.parametrize("content_type", [PROTOBUF, JSON])
def test_a_batch_mixing_two_projects_is_a_conflict_even_when_one_is_the_header_project(env, content_type):
    sent = logs(content_type, records=[_log(1, ENABLED), _log(2, DISABLED)])

    assert_discard(sent.post(env, ENABLED), sent, "project_context_conflict")

    assert_nothing_written(env)


@pytest.mark.parametrize("content_type", [PROTOBUF, JSON])
def test_explicit_values_equal_to_the_header_project_are_stored(env, content_type):
    sent = logs(content_type, resource=ENABLED.upper(), records=[_log(1, ENABLED), _log(2, ENABLED.title())])

    assert sent.post(env, ENABLED).status_code == 200
    assert stored_tables(env)


@pytest.mark.parametrize("content_type", [PROTOBUF, JSON])
def test_explicit_value_differing_from_the_header_project_by_whitespace_is_a_conflict(env, content_type):
    sent = logs(content_type, records=[_log(1, f" {ENABLED} ")])

    assert_discard(sent.post(env, ENABLED), sent, "project_context_conflict")

    assert_nothing_written(env)


@pytest.mark.parametrize(
    "events",
    [
        [with_project(hb.minimal("agent.tool.end", "s1", "2026-09-29T10:59:41.512Z"), OTHER)],
        [with_project(hb.usage_request(), OTHER)],
        [
            with_project(hb.minimal("agent.tool.end", "s1", "2026-09-29T10:59:41.512Z"), ENABLED),
            with_project(hb.minimal("agent.tool.end", "s2", "2026-09-29T10:59:41.512Z"), OTHER),
        ],
    ],
    ids=["hook-event", "usage-event", "mixed"],
)
def test_ndjson_events_with_a_conflicting_project_discard_the_batch(env, events):
    sent = hooks(events)

    assert_discard(sent.post(env, ENABLED), sent, "project_context_conflict")

    assert_nothing_written(env)


# ── counts and parsing ────────────────────────────────────────────────────────


@pytest.mark.parametrize("content_type", [PROTOBUF, JSON])
def test_a_metrics_batch_of_only_unstorable_points_still_reports_a_nonzero_rejected_count(env, content_type):
    metric_list = [
        b.histogram_metric("h"),
        b.summary_metric("s"),
        b.exponential_histogram_metric("e"),
        b.sum_metric("m", [_point(float("nan"))]),
    ]
    sent = metrics(content_type, metric_list=metric_list, wire=4)

    assert_discard(sent.post(env, DISABLED), sent, "cli_analytics_disabled")

    assert_nothing_written(env)


def test_a_partly_malformed_ndjson_body_discards_with_the_parseable_line_count(env):
    raw = b'{"type": "agent.tool.end", "session_id": "s1"}\nnot json\n[1, 2]\n{"type": "agent.tool.end", "session_id": "s2"}\n'
    sent = hooks(raw=raw, wire=2)

    assert_discard(sent.post(env, DISABLED), sent, "cli_analytics_disabled")

    assert_nothing_written(env)


@pytest.mark.parametrize("raw", [b"not json\n{broken", b"   \n"])
def test_a_wholly_malformed_or_empty_ndjson_body_is_still_a_400(env, raw):
    response = hooks(raw=raw, wire=0).post(env, DISABLED)

    assert response.status_code == 400
    assert_nothing_written(env)


@pytest.mark.parametrize("content_type", [PROTOBUF, JSON])
def test_a_malformed_otlp_body_is_a_400_not_a_discard(env, content_type):
    garbage = b"\xff\xff\xff\x00garbage" if content_type == PROTOBUF else b"{not json"

    response = env.client.post(
        f"{BASE}/logs", content=garbage, headers={"Content-Type": content_type, "X-CodeMie-Project": DISABLED}
    )

    assert response.status_code == 400
    assert_nothing_written(env)


# ── failures, instance flag, isolation, timing ────────────────────────────────


@pytest.mark.parametrize("make", FORMATS)
def test_a_policy_lookup_failure_is_a_503_with_no_writes(env, make):
    env.lookup.side_effect = RuntimeError("db down")

    response = make().post(env, ENABLED)

    assert response.status_code == 503
    assert_nothing_written(env)


@pytest.mark.parametrize("make", FORMATS)
def test_the_instance_flag_off_still_answers_404_before_any_lookup(env, make):
    with (
        patch.object(ingest_router, "_ensure_enabled", ORIGINAL_ENSURE_ENABLED),
        patch.object(ingest_router, "customer_config", MagicMock(is_feature_enabled=lambda _flag: False)),
        pytest.raises(ExtendedHTTPException) as raised,
    ):
        make().post(env, ENABLED)

    assert raised.value.code == 404
    env.lookup.assert_not_called()
    assert_nothing_written(env)


def test_two_projects_with_different_settings_are_isolated(env):
    first = logs(PROTOBUF)
    second = logs(PROTOBUF, records=[_log(7)])

    assert first.post(env, ENABLED).status_code == 200
    stored_after_first = len(env.engine.conn.statements)
    discarded = second.post(env, DISABLED)
    assert_discard(discarded, second, "cli_analytics_disabled")

    assert stored_after_first > 0
    assert len(env.engine.conn.statements) == stored_after_first, "the disabled project's batch wrote something"


def test_flipping_the_flag_applies_to_the_next_request_and_history_is_untouched(env):
    sent = logs(PROTOBUF)
    assert sent.post(env, ENABLED).status_code == 200
    written = list(env.engine.conn.statements)

    env.apps[ENABLED].cli_analytics_enabled = False
    assert_discard(sent.post(env, ENABLED), sent, "cli_analytics_disabled")
    assert env.engine.conn.statements == written  # nothing added, nothing removed

    env.apps[ENABLED].cli_analytics_enabled = True
    again = logs(PROTOBUF, records=[_log(99)])
    assert again.post(env, ENABLED).status_code == 200
    assert len(env.engine.conn.statements) > len(written)
