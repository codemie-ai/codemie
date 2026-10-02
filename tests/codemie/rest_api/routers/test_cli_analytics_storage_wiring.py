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

"""The CLI Analytics router talks to storage only through the configured ports."""

from __future__ import annotations

import dataclasses
import json
import ssl
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.core.exceptions import ExtendedHTTPException
from fastapi.responses import JSONResponse
from codemie.repository.cli_analytics.factory import CliAnalyticsStorage
from codemie.repository.cli_analytics.ports import (
    CliAnalyticsStorageConfigError,
    IngestResult,
    InvalidTelemetryPayloadError,
    TelemetryStorageUnavailableError,
    UnsupportedTelemetryContentTypeError,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.analytics.access_filter import ProjectAccessContext


class FakeIngestor:
    def __init__(self) -> None:
        self.otlp_calls: list[tuple] = []
        self.hook_calls: list[tuple] = []
        self.result = IngestResult(body=b"\x08\x01", media_type="application/x-protobuf", accepted=3)
        self.error: Exception | None = None

    async def ingest_otlp(self, signal, body, content_type):
        self.otlp_calls.append((signal, body, content_type))
        if self.error:
            raise self.error
        return self.result

    async def ingest_hook_events(self, events, user_email, received_at_ns):
        self.hook_calls.append((events, user_email, received_at_ns))
        if self.error:
            raise self.error
        return IngestResult(body=b"{}", media_type="application/json", accepted=len(events))


@pytest.fixture()
def storage():
    reader = MagicMock()
    reader.get_session_detail_meta = AsyncMock(return_value=[{"project_name": "other-project"}])
    fake = CliAnalyticsStorage(reader=reader, ingestor=FakeIngestor())
    with patch.object(router_module, "get_cli_analytics_storage", return_value=fake):
        yield fake


def _authenticate(request: Request) -> User:
    """Stands in for `authenticate`, which also publishes the user on request.state."""
    user = User(id="u1", email="dev@example.com")
    request.state.user = user
    return user


@pytest.fixture()
def client(storage):
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[authenticate] = _authenticate
    with patch.object(router_module, "_ensure_enabled", return_value=None):
        yield TestClient(app)


@pytest.fixture()
def refused_client():
    """The API with a storage that refuses its configuration (a URL it cannot apply, say)."""
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[authenticate] = _authenticate
    app.dependency_overrides[router_module._admin_gate] = lambda: None

    @app.exception_handler(ExtendedHTTPException)
    async def _extended(request, exc: ExtendedHTTPException):  # as the application registers it
        return JSONResponse(status_code=exc.code, content={"message": exc.message})

    refused = MagicMock(side_effect=CliAnalyticsStorageConfigError("the analytics database URL sets channel_binding"))
    with (
        patch.object(router_module, "get_cli_analytics_storage", refused),
        patch.object(router_module, "_ensure_enabled", return_value=None),
    ):
        yield TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "body", "content_type"),
    [
        ("/logs", b"", "application/x-protobuf"),
        ("/event-hooks", b'{"type": "agent.session.start"}', "application/x-ndjson"),
        ("/overview", None, None),
    ],
)
def test_a_storage_refusing_its_configuration_answers_503(refused_client, path, body, content_type):
    # Clients keep retrying a 503 until the configuration is fixed; a 400 would blame the request,
    # and OTLP exporters drop a batch answered 500.
    url = f"/v1/analytics/cli-analytics{path}"
    if body is None:
        resp = refused_client.get(url)
    else:
        resp = refused_client.post(url, content=body, headers={"Content-Type": content_type})

    assert resp.status_code == 503
    assert "channel_binding" not in resp.text  # the reason is in the server log, not the answer


class UnreachableReader:
    """A reader whose database cannot be reached: every query raises `error`."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def __getattr__(self, name: str):
        async def query(*args, **kwargs):
            raise self.error

        return query


@pytest.mark.parametrize(
    "error",
    [
        # Also a ValueError: taken for one, it would answer 400 and blame the request.
        ssl.SSLCertVerificationError(1, "certificate verify failed: certificate is not valid for 'db.internal'"),
        ConnectionRefusedError(61, "Connect call failed ('10.0.0.5', 5432)"),
        TimeoutError(),
    ],
)
def test_a_storage_that_cannot_be_reached_answers_503(error):
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[authenticate] = _authenticate
    app.dependency_overrides[router_module._admin_gate] = lambda: None

    @app.exception_handler(ExtendedHTTPException)
    async def _extended(request, exc: ExtendedHTTPException):  # as the application registers it
        return JSONResponse(status_code=exc.code, content={"message": exc.message, "details": exc.details})

    unreachable = CliAnalyticsStorage(reader=UnreachableReader(error), ingestor=FakeIngestor())
    with (
        patch.object(router_module, "get_cli_analytics_storage", return_value=unreachable),
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "logger"),
    ):
        resp = TestClient(app, raise_server_exceptions=False).get("/v1/analytics/cli-analytics/overview")

    assert resp.status_code == 503
    assert "db.internal" not in resp.text and "10.0.0.5" not in resp.text  # the server log has them


# ── OTLP ingest ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("signal", ["logs", "metrics", "traces"])
def test_otlp_body_is_handed_to_the_storage_ingestor(client, storage, signal):
    resp = client.post(
        f"/v1/analytics/cli-analytics/{signal}",
        content=b"\x01\x02",
        headers={"Content-Type": "application/x-protobuf"},
    )

    assert resp.status_code == 200
    assert resp.content == b"\x08\x01"
    assert resp.headers["content-type"] == "application/x-protobuf"
    assert storage.ingestor.otlp_calls == [(signal, b"\x01\x02", "application/x-protobuf")]


def test_missing_content_type_defaults_to_protobuf(client, storage):
    client.post("/v1/analytics/cli-analytics/logs", content=b"")

    assert storage.ingestor.otlp_calls[0][2] == "application/x-protobuf"


def test_oversized_otlp_body_is_rejected_before_storage(client, storage):
    with patch.object(router_module.config, "ANALYTICS_INGEST_MAX_BODY_BYTES", 4):
        resp = client.post("/v1/analytics/cli-analytics/traces", content=b"12345")

    assert resp.status_code == 413
    assert storage.ingestor.otlp_calls == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (InvalidTelemetryPayloadError("cannot decode"), 400),
        (UnsupportedTelemetryContentTypeError("text/plain"), 415),
        (TelemetryStorageUnavailableError("Analytics storage unavailable"), 503),
    ],
)
def test_ingest_errors_map_to_http_statuses(client, storage, error, status):
    storage.ingestor.error = error

    resp = client.post("/v1/analytics/cli-analytics/metrics", content=b"x")

    assert resp.status_code == status
    assert resp.json()["detail"] == str(error)


# ── hook events ───────────────────────────────────────────────────────────────


def test_hook_events_are_parsed_and_handed_to_the_storage_ingestor(client, storage):
    events = [{"type": "agent.session.start", "session_id": "s1"}, {"type": "agent.tool.start"}]
    before_ns = time.time_ns()

    resp = client.post(
        "/v1/analytics/cli-analytics/event-hooks",
        content=b"\n".join(json.dumps(e).encode() for e in events),
        headers={"Content-Type": "application/x-ndjson"},
    )

    assert resp.status_code == 200
    (sent, email, received_at_ns) = storage.ingestor.hook_calls[0]
    assert sent == events
    assert email == "dev@example.com"
    assert received_at_ns % 1000 == 0
    assert before_ns - 1_000_000_000 < received_at_ns <= time.time_ns()


def test_hook_ingest_errors_map_to_http_statuses(client, storage):
    storage.ingestor.error = TelemetryStorageUnavailableError("Analytics storage unavailable")

    resp = client.post(
        "/v1/analytics/cli-analytics/event-hooks",
        content=b'{"type": "agent.session.start"}',
        headers={"Content-Type": "application/x-ndjson"},
    )

    assert resp.status_code == 503


# ── read side ─────────────────────────────────────────────────────────────────


def test_handler_reads_through_the_storage_reader(storage):
    assert router_module._handler()._repo is storage.reader


def _context(*, is_admin: bool, admin_projects: list[str]) -> ProjectAccessContext:
    return ProjectAccessContext(user_id="u1", plain_user_projects=[], admin_projects=admin_projects, is_admin=is_admin)


def _filter_params(projects: str | None = None) -> router_module.FilterParams:
    # Called outside FastAPI, so every Query() default must be given explicitly.
    return router_module.FilterParams(
        time_period="last_7_days", start_date=None, end_date=None, users=None, projects=projects, repositories=None
    )


@pytest.mark.asyncio
async def test_project_admin_without_projects_gets_a_deny_all_filter_without_nul():
    params = _filter_params()
    with patch.object(router_module, "AccessFilter") as access:
        access.return_value.get_project_access_context.return_value = _context(is_admin=False, admin_projects=[])
        f = await params.resolve(User(id="u1"))

    assert f.deny_all is True
    assert f.projects is None


@pytest.mark.asyncio
async def test_project_admin_filter_is_limited_to_administered_projects():
    params = _filter_params(projects="p1,p2")
    with patch.object(router_module, "AccessFilter") as access:
        access.return_value.get_project_access_context.return_value = _context(is_admin=False, admin_projects=["p2"])
        f = await params.resolve(User(id="u1"))

    assert f.deny_all is False
    assert f.projects == ["p2"]


@pytest.mark.asyncio
async def test_session_detail_project_scoping_reads_through_the_storage_reader(storage):
    handler = MagicMock()
    handler.get_session_detail = AsyncMock(return_value=({"trace_id": "s1", "start_time": ""}, []))
    with (
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "_handler", return_value=handler),
        patch.object(router_module, "AccessFilter") as access,
        pytest.raises(ExtendedHTTPException) as denied,
    ):
        access.return_value.get_project_access_context.return_value = _context(is_admin=False, admin_projects=["mine"])
        await router_module.get_session_detail(user=User(id="u1"), trace_id="s1")

    assert denied.value.code == 404
    storage.reader.get_session_detail_meta.assert_awaited_once_with("s1")


def _window(days: int) -> tuple[router_module.FilterParams, datetime]:
    end = datetime(2026, 9, 23, tzinfo=timezone.utc)
    params = router_module.FilterParams(
        time_period=None,
        start_date=end - timedelta(days=days),
        end_date=end,
        users=None,
        projects=None,
        repositories=None,
    )
    return params, end


async def _resolve_as_admin(params: router_module.FilterParams):
    with patch.object(router_module, "AccessFilter") as access:
        access.return_value.get_project_access_context.return_value = _context(is_admin=True, admin_projects=[])
        return await params.resolve(User(id="u1"))


@pytest.mark.parametrize(("retention_days", "kept_days"), [(30, 30), (90, 60)])
@pytest.mark.asyncio
async def test_windows_are_clamped_to_how_far_back_the_storage_keeps_raw_rows(storage, retention_days, kept_days):
    # Longer windows would mix a year of rollup-backed cost with fewer days of raw-backed facts.
    kept = dataclasses.replace(storage, raw_retention_days=retention_days)
    params, end = _window(60)

    with patch.object(router_module, "get_cli_analytics_storage", return_value=kept):
        f = await _resolve_as_admin(params)

    assert f.start_dt == end - timedelta(days=kept_days)
