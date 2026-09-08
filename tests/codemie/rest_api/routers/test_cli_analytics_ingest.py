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
/v1/analytics/cli-analytics/.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as ingest_router
from codemie.rest_api.routers.cli_analytics import (
    _build_otlp_logs_payload,
    _event_to_log_record,
    _parse_ndjson,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_user() -> User:
    return User(id="test-user", email="dev@example.com")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ingest_router.router)
    app.dependency_overrides[authenticate] = lambda: _make_user()
    return app


def _make_app_no_auth() -> FastAPI:
    def _raise_401():
        raise HTTPException(status_code=401, detail="Unauthorized")

    app = FastAPI()
    app.include_router(ingest_router.router)
    app.dependency_overrides[authenticate] = _raise_401
    return app


@pytest.fixture()
def client() -> TestClient:
    with patch.object(ingest_router, "_ensure_enabled", return_value=None):
        yield TestClient(_make_app())


@pytest.fixture()
def client_no_auth() -> TestClient:
    with patch.object(ingest_router, "_ensure_enabled", return_value=None):
        yield TestClient(_make_app_no_auth())


def _mock_httpx_ok(status_code: int = 200):
    """Return a context-manager-compatible httpx mock that yields a success response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.content = b""
    mock_resp.headers = {}
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_client
    cm.__aexit__.return_value = None
    return cm, mock_client


def _mock_httpx_5xx():
    """Return a mock that always returns 500."""
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.content = b"error"
    mock_resp.headers = {}
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_client
    cm.__aexit__.return_value = None
    return cm, mock_client


def _mock_httpx_4xx(status_code: int = 404):
    """Return a mock that returns a 4xx response (misconfigured collector)."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.content = b"not found"
    mock_resp.headers = {}
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_client
    cm.__aexit__.return_value = None
    return cm, mock_client


# ---------------------------------------------------------------------------
# /logs — happy path
# ---------------------------------------------------------------------------


class TestIngestLogs:
    def test_returns_200_on_collector_ok(self, client):
        cm, _ = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            resp = client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"\x00\x01\x02",
                headers={"Content-Type": "application/x-protobuf"},
            )
        assert resp.status_code == 200

    def test_requires_auth(self, client_no_auth):
        resp = client_no_auth.post(
            "/v1/analytics/cli-analytics/logs",
            content=b"\x00",
        )
        assert resp.status_code == 401

    def test_forwards_content_type_to_collector(self, client):
        cm, mock_client = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"\x00\x01",
                headers={"Content-Type": "application/x-protobuf"},
            )
        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Content-Type"] == "application/x-protobuf"

    def test_forwards_raw_body_unchanged(self, client):
        cm, mock_client = _mock_httpx_ok()
        raw = b"\xde\xad\xbe\xef"
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/logs",
                content=raw,
                headers={"Content-Type": "application/x-protobuf"},
            )
        _, kwargs = mock_client.post.call_args
        assert kwargs["content"] == raw

    def test_empty_body_forwarded_not_rejected(self, client):
        cm, _ = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            resp = client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"",
                headers={"Content-Type": "application/x-protobuf"},
            )
        assert resp.status_code == 200

    def test_forwards_to_otlp_logs_path(self, client):
        cm, mock_client = _mock_httpx_ok()
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch(
                "codemie.rest_api.routers.cli_analytics.config.ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT",
                "http://collector:4318",
            ),
        ):
            client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"\x00",
            )
        url, _ = mock_client.post.call_args
        assert url[0].endswith("/v1/logs")

    # ------------------------------------------------------------------
    # Collector errors
    # ------------------------------------------------------------------

    def test_collector_unreachable_returns_503(self, client):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("refused")
        cm = AsyncMock()
        cm.__aenter__.return_value = mock_client
        cm.__aexit__.return_value = None
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch("codemie.rest_api.routers.cli_analytics.asyncio.sleep", new_callable=AsyncMock),
        ):
            resp = client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"\x00",
            )
        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    def test_collector_timeout_returns_503(self, client):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ReadTimeout("timed out")
        cm = AsyncMock()
        cm.__aenter__.return_value = mock_client
        cm.__aexit__.return_value = None
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch("codemie.rest_api.routers.cli_analytics.asyncio.sleep", new_callable=AsyncMock),
        ):
            resp = client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"\x00",
            )
        assert resp.status_code == 503

    def test_collector_5xx_retries_and_returns_503(self, client):
        cm, mock_client = _mock_httpx_5xx()
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch("codemie.rest_api.routers.cli_analytics.asyncio.sleep", new_callable=AsyncMock),
        ):
            resp = client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"\x00",
            )
        assert resp.status_code == 503
        assert mock_client.post.call_count == ingest_router._MAX_ATTEMPTS

    def test_collector_5xx_then_ok_returns_200(self, client):
        mock_500 = MagicMock(status_code=500, content=b"", headers={})
        mock_200 = MagicMock(status_code=200, content=b"", headers={})
        mock_client = AsyncMock()
        mock_client.post.side_effect = [mock_500, mock_500, mock_200]
        cm = AsyncMock()
        cm.__aenter__.return_value = mock_client
        cm.__aexit__.return_value = None
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch("codemie.rest_api.routers.cli_analytics.asyncio.sleep", new_callable=AsyncMock),
        ):
            resp = client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"\x00",
            )
        assert resp.status_code == 200
        assert mock_client.post.call_count == 3

    def test_collector_4xx_returns_502_not_retried(self, client):
        cm, mock_client = _mock_httpx_4xx(404)
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch("codemie.rest_api.routers.cli_analytics.asyncio.sleep", new_callable=AsyncMock),
        ):
            resp = client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"\x00",
            )
        assert resp.status_code == 502
        assert mock_client.post.call_count == 1

    def test_unexpected_exception_returns_503(self, client):
        mock_client = AsyncMock()
        mock_client.post.side_effect = RuntimeError("unexpected")
        cm = AsyncMock()
        cm.__aenter__.return_value = mock_client
        cm.__aexit__.return_value = None
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            resp = client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"\x00",
            )
        assert resp.status_code == 503
        assert mock_client.post.call_count == 1

    def test_body_too_large_returns_413(self, client):
        with patch("codemie.rest_api.routers.cli_analytics.config.ANALYTICS_INGEST_MAX_BODY_BYTES", 10):
            resp = client.post(
                "/v1/analytics/cli-analytics/logs",
                content=b"x" * 11,
                headers={"Content-Type": "application/x-protobuf"},
            )
        assert resp.status_code == 413


# ---------------------------------------------------------------------------
# /metrics — happy path
# ---------------------------------------------------------------------------


class TestIngestMetrics:
    def test_returns_200_on_collector_ok(self, client):
        cm, _ = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
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

    def test_forwards_to_otlp_metrics_path(self, client):
        cm, mock_client = _mock_httpx_ok()
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch(
                "codemie.rest_api.routers.cli_analytics.config.ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT",
                "http://collector:4318",
            ),
        ):
            client.post(
                "/v1/analytics/cli-analytics/metrics",
                content=b"\x00",
            )
        url, _ = mock_client.post.call_args
        assert url[0].endswith("/v1/metrics")

    def test_body_too_large_returns_413(self, client):
        with patch("codemie.rest_api.routers.cli_analytics.config.ANALYTICS_INGEST_MAX_BODY_BYTES", 10):
            resp = client.post(
                "/v1/analytics/cli-analytics/metrics",
                content=b"x" * 11,
                headers={"Content-Type": "application/x-protobuf"},
            )
        assert resp.status_code == 413


# ---------------------------------------------------------------------------
# /event-hooks — happy path
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
        cm, _ = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event()),
                headers={"Content-Type": "application/x-ndjson"},
            )
        assert resp.status_code == 200

    def test_returns_200_multi_event(self, client):
        cm, _ = _mock_httpx_ok()
        events = [_tool_start_event() for _ in range(10)]
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(*events),
                headers={"Content-Type": "application/x-ndjson"},
            )
        assert resp.status_code == 200

    def test_event_type_passed_through_unchanged(self, client):
        cm, mock_client = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event()),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        attrs = {
            a["key"]: a["value"]["stringValue"]
            for a in body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
        }
        assert attrs["event_type"] == "agent.session.start"

    def test_severity_info_for_session_start(self, client):
        cm, mock_client = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event()),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        record = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        assert record["severityText"] == "INFO"
        assert record["severityNumber"] == 9

    def test_severity_error_for_tool_error(self, client):
        cm, mock_client = _mock_httpx_ok()
        event = {
            "type": "agent.tool.error",
            "session_id": "s1",
            "timestamp": "2026-07-23T10:00:10.000Z",
            "tool_name": "Bash",
            "tool_use_id": "toolu_02",
            "error_message": "Command not found",
        }
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(event),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        record = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        assert record["severityText"] == "ERROR"
        assert record["severityNumber"] == 17

    def test_timestamp_converted_to_nanoseconds(self, client):
        cm, mock_client = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event(timestamp="2026-07-23T10:00:00.000Z")),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        ts_ns = int(body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["timeUnixNano"])
        assert ts_ns > 0
        # 2026-07-23 timestamp in nanoseconds should be roughly 1.75e18
        assert 1_700_000_000_000_000_000 < ts_ns < 1_800_000_000_000_000_000

    def test_all_log_attribute_fields_present(self, client):
        cm, mock_client = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event()),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        attrs_keys = {a["key"] for a in body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]}
        # Exact whitelist: mirrors _EVENT_ATTRIBUTE_KEYS in ingest_router.py
        expected = {
            "event_type",
            "session_id",
            "prompt_id",
            "developer_name",
            "codemie_project_name",
            "cwd",
            "git_branch",
            "repo_remote",
            "permission_mode",
            "source",
            "effort",
            "tool_name",
            "tool_use_id",
            "tool_input",
            "tool_output",
            "error_message",
            "error_type",
            "reason",
            "agent_id",
            "agent_type",
            "trigger",
            "denial_reason",
            "notification_type",
            "prompt_body",
            "skill_name",
        }
        assert expected == attrs_keys

    def test_skill_dispatch_skill_name_forwarded(self, client):
        cm, mock_client = _mock_httpx_ok()
        event = {
            "type": "agent.skill.dispatch",
            "session_id": "sess-abc",
            "prompt_id": "p-1",
            "timestamp": "2026-07-23T10:00:05.000Z",
            "skill_name": "sdlc-factory:sdlc-standard",
        }
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(event),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        attrs = {
            a["key"]: a["value"]["stringValue"]
            for a in body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
        }
        assert attrs["event_type"] == "agent.skill.dispatch"
        assert attrs["skill_name"] == "sdlc-factory:sdlc-standard"

    def test_skill_dispatch_command_source_forwarded(self, client):
        cm, mock_client = _mock_httpx_ok()
        event = {
            "type": "agent.skill.dispatch",
            "session_id": "sess-abc",
            "prompt_id": "p-1",
            "timestamp": "2026-07-23T10:00:05.000Z",
            "skill_name": "sdlc-factory:sdlc-standard",
            "command_source": "plugin",
            "command_args": "arg1 arg2",
        }
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(event),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        attrs = {
            a["key"]: a["value"]["stringValue"]
            for a in body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
        }
        assert attrs["event_type"] == "agent.skill.dispatch"
        assert attrs["skill_name"] == "sdlc-factory:sdlc-standard"

    def test_prompt_submit_prompt_body_forwarded(self, client):
        # prompt_body must survive the hook -> OTel LogAttributes translation, or
        # get_session_detail's argMinIf(prompt_body, ...) has nothing to read no
        # matter how well analytics-hook captures the prompt client-side.
        cm, mock_client = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event(type="agent.prompt.submit", prompt_body="fix the login bug")),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        attrs = {
            a["key"]: a["value"]["stringValue"]
            for a in body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
        }
        assert attrs["prompt_body"] == "fix the login bug"

    def test_resource_attributes_service_name(self, client):
        cm, mock_client = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event()),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        resource_attrs = {
            a["key"]: a["value"]["stringValue"] for a in body["resourceLogs"][0]["resource"]["attributes"]
        }
        assert resource_attrs["service.name"] == "codemie-agent-hooks"

    def test_posts_to_otlp_logs_path(self, client):
        cm, mock_client = _mock_httpx_ok()
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch(
                "codemie.rest_api.routers.cli_analytics.config.ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT",
                "http://collector:4318",
            ),
        ):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event()),
            )
        url, _ = mock_client.post.call_args
        assert url[0] == "http://collector:4318/v1/logs"

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def test_empty_body_returns_400(self, client):
        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=b"",
        )
        assert resp.status_code == 400

    def test_whitespace_only_body_returns_400(self, client):
        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=b"   \n  \n  ",
        )
        assert resp.status_code == 400

    def test_all_lines_invalid_json_returns_400(self, client):
        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=b"not-json\nalso-not-json",
        )
        assert resp.status_code == 400

    def test_empty_lines_skipped_valid_events_processed(self, client):
        cm, _ = _mock_httpx_ok()
        body = b"\n" + _ndjson(_session_start_event()) + b"\n\n"
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=body,
            )
        assert resp.status_code == 200

    def test_mixed_valid_invalid_lines_processes_valid(self, client):
        cm, mock_client = _mock_httpx_ok()
        body = json.dumps(_session_start_event()).encode() + b"\nnot-json\n" + json.dumps(_tool_start_event()).encode()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=body,
            )
        assert resp.status_code == 200
        _, kwargs = mock_client.post.call_args
        parsed = json.loads(kwargs["content"])
        assert len(parsed["resourceLogs"][0]["scopeLogs"][0]["logRecords"]) == 2

    def test_body_too_large_returns_413(self, client):
        with patch("codemie.rest_api.routers.cli_analytics.config.ANALYTICS_INGEST_MAX_BODY_BYTES", 10):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=b"x" * 11,
            )
        assert resp.status_code == 413

    def test_requires_auth(self, client_no_auth):
        resp = client_no_auth.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=_ndjson(_session_start_event()),
        )
        assert resp.status_code == 401

    # ------------------------------------------------------------------
    # Missing / malformed fields
    # ------------------------------------------------------------------

    def test_missing_session_id_defaults_to_empty(self, client):
        cm, mock_client = _mock_httpx_ok()
        event = {"type": "agent.session.start", "timestamp": "2026-07-23T10:00:00Z"}
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(event),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        attrs = {
            a["key"]: a["value"]["stringValue"]
            for a in body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
        }
        assert attrs["session_id"] == ""

    def test_missing_developer_name_defaults_to_empty(self, client):
        cm, mock_client = _mock_httpx_ok()
        event = {"type": "agent.session.start", "session_id": "s1"}
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(event),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        attrs = {
            a["key"]: a["value"]["stringValue"]
            for a in body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
        }
        assert attrs["developer_name"] == ""

    def test_missing_timestamp_uses_current_time(self, client):
        cm, mock_client = _mock_httpx_ok()
        event = {"type": "agent.session.start", "session_id": "s1"}
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(event),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        ts_ns = int(body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["timeUnixNano"])
        assert ts_ns > 0

    def test_invalid_timestamp_uses_current_time(self, client):
        cm, mock_client = _mock_httpx_ok()
        event = {"type": "agent.session.start", "session_id": "s1", "timestamp": "not-a-date"}
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(event),
            )
        assert resp.status_code == 200
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        ts_ns = int(body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["timeUnixNano"])
        assert ts_ns > 0

    def test_cwd_passed_through_unchanged(self, client):
        """cwd is stored as-is; the plugin is the single normalization site."""
        cm, mock_client = _mock_httpx_ok()
        event = {"type": "agent.session.start", "session_id": "s1", "cwd": "epm-cdme/my-repo"}
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(event),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        attrs = {
            a["key"]: a["value"]["stringValue"]
            for a in body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
        }
        assert attrs["cwd"] == "epm-cdme/my-repo"

    def test_unknown_event_type_passes_through_as_info(self, client):
        cm, mock_client = _mock_httpx_ok()
        event = {"type": "custom.event", "session_id": "s1"}
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(event),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        record = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        attrs = {a["key"]: a["value"]["stringValue"] for a in record["attributes"]}
        assert attrs["event_type"] == "custom.event"
        assert record["severityText"] == "INFO"

    def test_tool_input_not_truncated_by_api(self, client):
        cm, mock_client = _mock_httpx_ok()
        long_input = "x" * 500
        event = {"type": "agent.tool.start", "session_id": "s1", "tool_input": long_input}
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(event),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        attrs = {
            a["key"]: a["value"]["stringValue"]
            for a in body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
        }
        assert attrs["tool_input"] == long_input

    # ------------------------------------------------------------------
    # Collector errors
    # ------------------------------------------------------------------

    def test_collector_unreachable_returns_503(self, client):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("refused")
        cm = AsyncMock()
        cm.__aenter__.return_value = mock_client
        cm.__aexit__.return_value = None
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch("codemie.rest_api.routers.cli_analytics.asyncio.sleep", new_callable=AsyncMock),
        ):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event()),
            )
        assert resp.status_code == 503

    def test_collector_timeout_returns_503(self, client):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ReadTimeout("timed out")
        cm = AsyncMock()
        cm.__aenter__.return_value = mock_client
        cm.__aexit__.return_value = None
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch("codemie.rest_api.routers.cli_analytics.asyncio.sleep", new_callable=AsyncMock),
        ):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event()),
            )
        assert resp.status_code == 503

    def test_collector_5xx_retries_and_returns_503(self, client):
        cm, mock_client = _mock_httpx_5xx()
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch("codemie.rest_api.routers.cli_analytics.asyncio.sleep", new_callable=AsyncMock),
        ):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event()),
            )
        assert resp.status_code == 503
        assert mock_client.post.call_count == ingest_router._MAX_ATTEMPTS

    def test_collector_5xx_then_ok_returns_200(self, client):
        mock_500 = MagicMock(status_code=500, content=b"", headers={})
        mock_200 = MagicMock(status_code=200, content=b"", headers={})
        mock_client = AsyncMock()
        mock_client.post.side_effect = [mock_500, mock_500, mock_200]
        cm = AsyncMock()
        cm.__aenter__.return_value = mock_client
        cm.__aexit__.return_value = None
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch("codemie.rest_api.routers.cli_analytics.asyncio.sleep", new_callable=AsyncMock),
        ):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event()),
            )
        assert resp.status_code == 200
        assert mock_client.post.call_count == 3

    def test_collector_4xx_returns_502_not_retried(self, client):
        cm, mock_client = _mock_httpx_4xx(400)
        with (
            patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm),
            patch("codemie.rest_api.routers.cli_analytics.asyncio.sleep", new_callable=AsyncMock),
        ):
            resp = client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson(_session_start_event()),
            )
        assert resp.status_code == 502
        assert mock_client.post.call_count == 1

    def test_wrong_content_type_returns_415(self, client):
        resp = client.post(
            "/v1/analytics/cli-analytics/event-hooks",
            content=b"data",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 415

    def test_now_ns_precision_integer_arithmetic(self, client):
        cm, mock_client = _mock_httpx_ok()
        with patch("codemie.rest_api.routers.cli_analytics.httpx.AsyncClient", return_value=cm):
            client.post(
                "/v1/analytics/cli-analytics/event-hooks",
                content=_ndjson({"type": "agent.session.start", "session_id": "s1"}),
            )
        _, kwargs = mock_client.post.call_args
        body = json.loads(kwargs["content"])
        record = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        obs_ns = int(record["observedTimeUnixNano"])
        # observedTimeUnixNano must be a multiple of 1000 (microsecond precision)
        # and within a reasonable range of now
        assert obs_ns % 1000 == 0
        assert obs_ns > 1_700_000_000_000_000_000


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


class TestEventToLogRecord:
    def _attrs(self, record: dict) -> dict:
        return {a["key"]: a["value"]["stringValue"] for a in record["attributes"]}

    def test_maps_type_to_event_type_attribute(self):
        record = _event_to_log_record({"type": "agent.session.start"}, 0)
        assert self._attrs(record)["event_type"] == "agent.session.start"

    def test_info_severity_default(self):
        record = _event_to_log_record({"type": "agent.session.start"}, 0)
        assert record["severityText"] == "INFO"
        assert record["severityNumber"] == 9

    def test_error_severity_for_tool_error(self):
        record = _event_to_log_record({"type": "agent.tool.error"}, 0)
        assert record["severityText"] == "ERROR"
        assert record["severityNumber"] == 17

    def test_uses_now_ns_when_no_timestamp(self):
        record = _event_to_log_record({}, 999_000_000_000)
        assert record["timeUnixNano"] == "999000000000"

    def test_parses_iso8601_timestamp(self):
        record = _event_to_log_record({"timestamp": "2026-07-23T10:00:00.000Z"}, 0)
        ts = int(record["timeUnixNano"])
        assert ts > 0

    def test_invalid_timestamp_falls_back_to_now_ns(self):
        record = _event_to_log_record({"timestamp": "bad"}, 42)
        assert record["timeUnixNano"] == "42"

    def test_cwd_passed_through_unchanged_unit(self):
        """cwd is not modified at ingest; the plugin normalises it before sending."""
        record = _event_to_log_record({"cwd": "epm-cdme/codemie-ui"}, 0)
        assert self._attrs(record)["cwd"] == "epm-cdme/codemie-ui"

    def test_cwd_group_slash_repo_preserved(self):
        record = _event_to_log_record({"cwd": "my-group/my-repo"}, 0)
        assert self._attrs(record)["cwd"] == "my-group/my-repo"

    def test_none_fields_default_to_empty_string(self):
        record = _event_to_log_record({}, 0)
        attrs = self._attrs(record)
        for key in ("session_id", "developer_name", "cwd", "git_branch", "tool_name"):
            assert attrs[key] == "", f"expected empty string for {key}"

    def test_observed_time_unix_nano_equals_now_ns(self):
        record = _event_to_log_record({"timestamp": "2026-07-23T10:00:00.000Z"}, 999_000_000_000)
        assert record["observedTimeUnixNano"] == "999000000000"

    def test_observed_time_unix_nano_present_when_no_timestamp(self):
        record = _event_to_log_record({}, 42)
        assert "observedTimeUnixNano" in record
        assert record["observedTimeUnixNano"] == "42"

    def test_dict_tool_input_serialized_as_json_string(self):
        tool_input = {"command": "git status", "cwd": "/repo"}
        record = _event_to_log_record({"type": "agent.tool.start", "tool_input": tool_input}, 0)
        raw = self._attrs(record)["tool_input"]
        assert json.loads(raw) == tool_input

    def test_list_tool_output_serialized_as_json_string(self):
        tool_output = ["line1", "line2"]
        record = _event_to_log_record({"tool_output": tool_output}, 0)
        raw = self._attrs(record)["tool_output"]
        assert json.loads(raw) == tool_output


class TestBuildOtlpLogsPayload:
    def test_produces_valid_json(self):
        records = [_event_to_log_record({"type": "agent.session.start"}, 0)]
        payload = _build_otlp_logs_payload(records)
        parsed = json.loads(payload)
        assert "resourceLogs" in parsed

    def test_service_name_in_resource_attributes(self):
        payload = json.loads(_build_otlp_logs_payload([]))
        attrs = {a["key"]: a["value"]["stringValue"] for a in payload["resourceLogs"][0]["resource"]["attributes"]}
        assert attrs["service.name"] == "codemie-agent-hooks"

    def test_records_in_scope_logs(self):
        records = [_event_to_log_record({"type": "agent.session.start"}, 0)] * 3
        payload = json.loads(_build_otlp_logs_payload(records))
        assert len(payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]) == 3
