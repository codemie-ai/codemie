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

"""Tests for the analytics ingest endpoints (EPMCDME-13556).

Covers /logs, /metrics, and /event-hooks under
/v1/analytics/cli-analytics/: what the router accepts, rejects and hands to the
storage ingestor. How the storage decodes and stores it is tested with the adapter.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as ingest_router
from codemie.repository.cli_analytics.factory import CliAnalyticsStorage
from codemie.repository.cli_analytics.ports import (
    CliAnalyticsStorageConfigError,
    IngestResult,
    InvalidTelemetryPayloadError,
    OtlpSignal,
    TelemetryStorageUnavailableError,
)
from codemie.rest_api.routers.cli_analytics import _parse_ndjson
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class RecordingIngestor:
    """Stands in for the storage ingestor and records what the router hands it.

    With `error` set, every call is recorded and then raises it, as the storage would.
    """

    def __init__(self) -> None:
        self.otlp_calls: list[tuple[str, bytes, str]] = []
        self.hook_calls: list[tuple[list[dict[str, Any]], str, int]] = []
        self.error: Exception | None = None

    async def ingest_otlp(self, signal: OtlpSignal, body: bytes, content_type: str) -> IngestResult:
        self.otlp_calls.append((signal, body, content_type))
        if self.error is not None:
            raise self.error
        return IngestResult()

    async def ingest_hook_events(
        self, events: list[dict[str, Any]], user_email: str, received_at_ns: int
    ) -> IngestResult:
        self.hook_calls.append((events, user_email, received_at_ns))
        if self.error is not None:
            raise self.error
        return IngestResult(body=b"{}", media_type="application/json", accepted=len(events))

    @property
    def events(self) -> list[dict[str, Any]]:
        """The events of the only hook call."""
        (call,) = self.hook_calls
        return call[0]


def _authenticate(request: Request) -> User:
    """Stands in for `authenticate`, which also publishes the user on request.state."""
    user = User(id="test-user", email="dev@example.com")
    request.state.user = user
    return user


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ingest_router.router)
    app.dependency_overrides[authenticate] = _authenticate
    return app


def _make_app_no_auth() -> FastAPI:
    def _raise_401():
        raise HTTPException(status_code=401, detail="Unauthorized")

    app = FastAPI()
    app.include_router(ingest_router.router)
    app.dependency_overrides[authenticate] = _raise_401
    return app


@pytest.fixture(autouse=True)
def ingestor() -> RecordingIngestor:
    recording = RecordingIngestor()
    storage = CliAnalyticsStorage(reader=None, ingestor=recording)
    with patch.object(ingest_router, "get_cli_analytics_storage", return_value=storage):
        yield recording


@pytest.fixture()
def client() -> TestClient:
    with patch.object(ingest_router, "_ensure_enabled", return_value=None):
        yield TestClient(_make_app())


@pytest.fixture()
def client_no_auth() -> TestClient:
    with patch.object(ingest_router, "_ensure_enabled", return_value=None):
        yield TestClient(_make_app_no_auth())


# ---------------------------------------------------------------------------
# /logs
# ---------------------------------------------------------------------------


class TestIngestLogs:
    def test_returns_200_once_stored(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/analytics/cli-analytics/logs",
            content=b"\x00\x01\x02",
            headers={"Content-Type": "application/x-protobuf"},
        )
        assert resp.status_code == 200

    def test_requires_auth(self, client_no_auth: TestClient, ingestor: RecordingIngestor) -> None:
        resp = client_no_auth.post(
            "/v1/analytics/cli-analytics/logs",
            content=b"\x00",
        )
        assert resp.status_code == 401
        assert ingestor.otlp_calls == []

    def test_hands_the_logs_signal_to_the_ingestor(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        client.post("/v1/analytics/cli-analytics/logs", content=b"\x00")

        assert [signal for signal, _, _ in ingestor.otlp_calls] == ["logs"]

    def test_hands_the_content_type_to_the_ingestor(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        client.post(
            "/v1/analytics/cli-analytics/logs",
            content=b"\x00\x01",
            headers={"Content-Type": "application/json"},
        )

        assert ingestor.otlp_calls[0][2] == "application/json"

    def test_hands_the_raw_body_unchanged(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        raw = b"\xde\xad\xbe\xef"
        client.post(
            "/v1/analytics/cli-analytics/logs",
            content=raw,
            headers={"Content-Type": "application/x-protobuf"},
        )

        assert ingestor.otlp_calls[0][1] == raw

    def test_empty_body_is_handed_on_not_rejected(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        resp = client.post(
            "/v1/analytics/cli-analytics/logs",
            content=b"",
            headers={"Content-Type": "application/x-protobuf"},
        )

        assert resp.status_code == 200
        assert ingestor.otlp_calls == [("logs", b"", "application/x-protobuf")]

    def test_body_too_large_returns_413(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        with patch("codemie.rest_api.routers.cli_analytics.config.ANALYTICS_INGEST_MAX_BODY_BYTES", 10):
            resp = client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"x" * 11,
                headers={"Content-Type": "application/x-protobuf"},
            )
        assert resp.status_code == 413
        assert ingestor.otlp_calls == []


# ---------------------------------------------------------------------------
# /metrics
# ---------------------------------------------------------------------------


class TestIngestMetrics:
    def test_returns_200_once_stored(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/analytics/cli-analytics/metrics",
            content=b"\x00\x01",
            headers={"Content-Type": "application/x-protobuf"},
        )
        assert resp.status_code == 200

    def test_requires_auth(self, client_no_auth):
        resp = client_no_auth.post(
            "/v1/analytics/cli-analytics/metrics",
            content=b"\x00",
        )
        assert resp.status_code == 401

    def test_hands_the_metrics_signal_to_the_ingestor(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        client.post("/v1/analytics/cli-analytics/metrics", content=b"\x00")

        assert [signal for signal, _, _ in ingestor.otlp_calls] == ["metrics"]

    def test_body_too_large_returns_413(self, client):
        with patch("codemie.rest_api.routers.cli_analytics.config.ANALYTICS_INGEST_MAX_BODY_BYTES", 10):
            resp = client.post(
                "/v1/analytics/cli-analytics/metrics",
                content=b"x" * 11,
                headers={"Content-Type": "application/x-protobuf"},
            )
        assert resp.status_code == 413


# ---------------------------------------------------------------------------
# /event-hooks
# ---------------------------------------------------------------------------


def _session_start_event(**overrides) -> dict:
    base = {
        "type": "agent.session.start",
        "session_id": "sess-abc",
        "developer_name": "dev@example.com",
        "timestamp": "2026-07-23T10:00:00.000Z",
        "cwd": "/home/dev/project",
        "git_branch": "main",
        "permission_mode": "default",
    }
    base.update(overrides)
    return base


def _tool_start_event(**overrides) -> dict:
    base = {
        "type": "agent.tool.start",
        "session_id": "sess-abc",
        "timestamp": "2026-07-23T10:00:05.000Z",
        "tool_name": "Bash",
        "tool_use_id": "toolu_01X",
        "tool_input": "git status",
    }
    base.update(overrides)
    return base


def _ndjson(*events: dict) -> bytes:
    return b"\n".join(json.dumps(e).encode() for e in events)


class TestIngestEventHooks:
    # Happy path
    def test_returns_200_single_event(self, client):
        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=_ndjson(_session_start_event()),
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert resp.status_code == 200

    def test_hands_every_event_of_the_batch_to_the_ingestor(
        self, client: TestClient, ingestor: RecordingIngestor
    ) -> None:
        events = [_tool_start_event(tool_use_id=f"toolu_{i}") for i in range(10)]

        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=_ndjson(*events),
            headers={"Content-Type": "application/x-ndjson"},
        )

        assert resp.status_code == 200
        assert ingestor.events == events

    def test_hands_the_authenticated_sender_to_the_ingestor(
        self, client: TestClient, ingestor: RecordingIngestor
    ) -> None:
        client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=_ndjson(_session_start_event(developer_name="someone@example.com")),
        )

        (_, user_email, received_at_ns) = ingestor.hook_calls[0]
        assert user_email == "dev@example.com"
        assert received_at_ns % 1000 == 0  # microsecond precision
        assert received_at_ns > 1_700_000_000_000_000_000

    @pytest.mark.parametrize(
        "event",
        [
            _session_start_event(),
            _session_start_event(type="agent.prompt.submit", prompt_body="fix the login bug"),
            {"type": "agent.tool.error", "session_id": "s1", "tool_name": "Bash", "error_message": "not found"},
            {
                "type": "agent.skill.dispatch",
                "session_id": "sess-abc",
                "prompt_id": "p-1",
                "skill_name": "sdlc-factory:sdlc-standard",
                "command_source": "plugin",
                "command_args": "arg1 arg2",
            },
            {"type": "agent.session.start", "session_id": "s1", "cwd": "epm-cdme/my-repo"},  # the plugin normalises
            {"type": "agent.tool.start", "session_id": "s1", "tool_input": "x" * 500},  # not truncated by the API
            {"type": "agent.tool.start", "tool_input": {"command": "git status"}, "tool_output": ["a", "b"]},
            {"type": "custom.event", "session_id": "s1"},
            {"type": "agent.session.start", "timestamp": "not-a-date"},
        ],
    )
    def test_events_are_handed_on_unchanged(
        self, client: TestClient, ingestor: RecordingIngestor, event: dict[str, Any]
    ) -> None:
        resp = client.post("/v1/analytics/cli-analytics/event-hooks", content=_ndjson(event))

        assert resp.status_code == 200
        assert ingestor.events == [event]

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def test_empty_body_returns_400(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=b"",
        )
        assert resp.status_code == 400
        assert ingestor.hook_calls == []

    def test_whitespace_only_body_returns_400(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=b"   \n  \n  ",
        )
        assert resp.status_code == 400
        assert ingestor.hook_calls == []

    def test_all_lines_invalid_json_returns_400(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=b"not-json\nalso-not-json",
        )
        assert resp.status_code == 400
        assert ingestor.hook_calls == []

    def test_empty_lines_skipped_valid_events_processed(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        body = b"\n" + _ndjson(_session_start_event()) + b"\n\n"

        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=body,
        )

        assert resp.status_code == 200
        assert ingestor.events == [_session_start_event()]

    def test_mixed_valid_invalid_lines_processes_valid(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        body = json.dumps(_session_start_event()).encode() + b"\nnot-json\n" + json.dumps(_tool_start_event()).encode()

        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=body,
        )

        assert resp.status_code == 200
        assert ingestor.events == [_session_start_event(), _tool_start_event()]

    def test_body_too_large_returns_413(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        with patch("codemie.rest_api.routers.cli_analytics.config.ANALYTICS_INGEST_MAX_BODY_BYTES", 10):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=b"x" * 11,
            )
        assert resp.status_code == 413
        assert ingestor.hook_calls == []

    def test_requires_auth(self, client_no_auth: TestClient, ingestor: RecordingIngestor) -> None:
        resp = client_no_auth.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=_ndjson(_session_start_event()),
        )
        assert resp.status_code == 401
        assert ingestor.hook_calls == []

    def test_wrong_content_type_returns_415(self, client: TestClient, ingestor: RecordingIngestor) -> None:
        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=b"data",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 415
        assert ingestor.hook_calls == []


# ---------------------------------------------------------------------------
# Storage errors -> HTTP status (every ingest endpoint)
# ---------------------------------------------------------------------------

_INGEST_REQUESTS = [
    ("/v1/analytics/cli-analytics/logs", b"\x00", "application/x-protobuf"),
    ("/v1/analytics/cli-analytics/metrics", b"\x00", "application/x-protobuf"),
    ("/v1/analytics/cli-analytics/traces", b"\x00", "application/x-protobuf"),
    ("/v1/analytics/cli-analytics/event-hooks", b'{"type": "agent.session.start"}', "application/x-ndjson"),
]


class TestIngestStorageErrors:
    @pytest.mark.parametrize(("path", "body", "content_type"), _INGEST_REQUESTS)
    def test_storage_unavailable_returns_503(
        self, client: TestClient, ingestor: RecordingIngestor, path: str, body: bytes, content_type: str
    ) -> None:
        # 503 tells the CLI to retry later instead of dropping the telemetry.
        ingestor.error = TelemetryStorageUnavailableError("Analytics storage unavailable")

        resp = client.post(path, content=body, headers={"Content-Type": content_type})

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Analytics storage unavailable"

    @pytest.mark.parametrize(("path", "body", "content_type"), _INGEST_REQUESTS)
    def test_invalid_body_returns_400(
        self, client: TestClient, ingestor: RecordingIngestor, path: str, body: bytes, content_type: str
    ) -> None:
        # The storage cannot decode or store the body as sent; resending it will not help.
        ingestor.error = InvalidTelemetryPayloadError("cannot decode")

        resp = client.post(path, content=body, headers={"Content-Type": content_type})

        assert resp.status_code == 400
        assert resp.json()["detail"] == "cannot decode"

    @pytest.mark.parametrize(("path", "body", "content_type"), _INGEST_REQUESTS)
    def test_storage_misconfigured_returns_503_without_its_message(
        self, client: TestClient, ingestor: RecordingIngestor, path: str, body: bytes, content_type: str
    ) -> None:
        # Retried by the client until the configuration is fixed; the settings named in the message stay in the log.
        ingestor.error = CliAnalyticsStorageConfigError("CLI_ANALYTICS_PG_URL is not a PostgreSQL URL")

        resp = client.post(path, content=body, headers={"Content-Type": content_type})

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Analytics storage is not available"


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestParseNdjson:
    def test_single_valid_line(self):
        result = _parse_ndjson(b'{"a": 1}')
        assert result == [{"a": 1}]

    def test_multiple_lines(self):
        body = b'{"a": 1}\n{"b": 2}'
        result = _parse_ndjson(body)
        assert len(result) == 2

    def test_skips_empty_lines(self):
        body = b'{"a": 1}\n\n{"b": 2}\n'
        result = _parse_ndjson(body)
        assert len(result) == 2

    def test_skips_malformed_lines(self):
        body = b'{"a": 1}\nnot-json\n{"b": 2}'
        result = _parse_ndjson(body)
        assert len(result) == 2

    def test_all_malformed_returns_empty(self):
        result = _parse_ndjson(b"bad\nalso-bad")
        assert result == []

    def test_empty_body_returns_empty(self):
        assert _parse_ndjson(b"") == []

    def test_skips_lines_that_are_not_objects(self):
        # Only an object is a hook event; anything else would fail.
        body = b'[1, 2]\n"text"\n5\nnull\n{"a": 1}'
        assert _parse_ndjson(body) == [{"a": 1}]

    def test_skips_lines_nested_beyond_the_recursion_limit(self):
        body = b"[" * 100_000 + b"]" * 100_000 + b'\n{"a": 1}'
        assert _parse_ndjson(body) == [{"a": 1}]

    def test_skips_lines_with_integers_too_long_to_convert(self):
        # json.loads raises a plain ValueError past 4,300 digits; the batch must not fail with a 500.
        body = b'{"n": ' + b"9" * 5000 + b'}\n{"a": 1}'
        assert _parse_ndjson(body) == [{"a": 1}]
