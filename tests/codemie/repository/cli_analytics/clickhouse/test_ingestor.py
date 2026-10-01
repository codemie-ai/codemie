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

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from codemie.repository.cli_analytics.clickhouse import ingestor as ch_ingestor
from codemie.repository.cli_analytics.clickhouse.ingestor import ClickHouseTelemetryIngestor
from codemie.repository.cli_analytics.ports import (
    CliTelemetryIngestor,
    IngestResult,
    TelemetryStorageUnavailableError,
    TelemetryUpstreamConfigurationError,
)
from tests.codemie.repository.cli_analytics.support.contracts import assert_implements_port

ENDPOINT = "http://collector:4318"


def _collector(*responses):
    """An httpx.AsyncClient stand-in whose post() yields the given responses or raises the given errors."""
    client = AsyncMock()
    effects = []
    for item in responses:
        if isinstance(item, Exception):
            effects.append(item)
        else:
            status, content, content_type = item
            resp = MagicMock(status_code=status, content=content, headers={"content-type": content_type})
            effects.append(resp)
    client.post.side_effect = effects
    cm = AsyncMock()
    cm.__aenter__.return_value = client
    cm.__aexit__.return_value = None
    return cm, client


@pytest.fixture(autouse=True)
def _collector_endpoint():
    with (
        patch.object(ch_ingestor.config, "ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT", ENDPOINT),
        patch.object(ch_ingestor.asyncio, "sleep", new_callable=AsyncMock),
    ):
        yield


def test_ingestor_implements_the_ingest_port():
    assert_implements_port(ClickHouseTelemetryIngestor, CliTelemetryIngestor)


@pytest.mark.parametrize("signal", ["logs", "metrics", "traces"])
@pytest.mark.asyncio
async def test_otlp_body_is_forwarded_unchanged_to_the_signal_path(signal):
    cm, client = _collector((200, b"\x0a\x00", "application/x-protobuf"))

    with patch.object(ch_ingestor.httpx, "AsyncClient", return_value=cm):
        result = await ClickHouseTelemetryIngestor().ingest_otlp(signal, b"\x01\x02", "application/x-protobuf")

    url = client.post.call_args.args[0]
    assert url == f"{ENDPOINT}/v1/{signal}"
    assert client.post.call_args.kwargs["content"] == b"\x01\x02"
    assert client.post.call_args.kwargs["headers"] == {"Content-Type": "application/x-protobuf"}
    assert result == IngestResult(body=b"\x0a\x00", media_type="application/x-protobuf")


@pytest.mark.asyncio
async def test_collector_endpoint_is_read_when_the_request_is_made():
    cm, client = _collector((200, b"", "application/json"))
    ingestor = ClickHouseTelemetryIngestor()

    with (
        patch.object(ch_ingestor.config, "ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT", "http://other:4318"),
        patch.object(ch_ingestor.httpx, "AsyncClient", return_value=cm),
    ):
        await ingestor.ingest_otlp("logs", b"", "application/x-protobuf")

    assert client.post.call_args.args[0] == "http://other:4318/v1/logs"


@pytest.mark.asyncio
async def test_collector_5xx_is_retried_then_reported_unavailable():
    cm, client = _collector(*[(503, b"", "text/plain")] * ch_ingestor._MAX_ATTEMPTS)

    with (
        patch.object(ch_ingestor.httpx, "AsyncClient", return_value=cm),
        pytest.raises(TelemetryStorageUnavailableError),
    ):
        await ClickHouseTelemetryIngestor().ingest_otlp("logs", b"", "application/x-protobuf")

    assert client.post.await_count == ch_ingestor._MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_collector_recovering_within_the_retries_succeeds():
    cm, client = _collector((500, b"", "text/plain"), httpx.ConnectError("refused"), (200, b"ok", "text/plain"))

    with patch.object(ch_ingestor.httpx, "AsyncClient", return_value=cm):
        result = await ClickHouseTelemetryIngestor().ingest_otlp("traces", b"", "application/x-protobuf")

    assert result.body == b"ok"
    assert client.post.await_count == 3


@pytest.mark.asyncio
async def test_collector_4xx_is_a_configuration_error_and_not_retried():
    cm, client = _collector((404, b"not found", "text/plain"))

    with (
        patch.object(ch_ingestor.httpx, "AsyncClient", return_value=cm),
        pytest.raises(TelemetryUpstreamConfigurationError),
    ):
        await ClickHouseTelemetryIngestor().ingest_otlp("logs", b"", "application/x-protobuf")

    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_unexpected_error_stops_retrying_and_reports_unavailable():
    cm, client = _collector(RuntimeError("boom"))

    with (
        patch.object(ch_ingestor.httpx, "AsyncClient", return_value=cm),
        pytest.raises(TelemetryStorageUnavailableError),
    ):
        await ClickHouseTelemetryIngestor().ingest_otlp("logs", b"", "application/x-protobuf")

    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_hook_events_are_sent_as_otlp_json_logs_with_the_sender_email():
    cm, client = _collector((200, b"{}", "application/json"))
    events = [{"type": "agent.session.start", "session_id": "s1", "timestamp": "2026-09-23T10:00:00Z"}]

    with patch.object(ch_ingestor.httpx, "AsyncClient", return_value=cm):
        result = await ClickHouseTelemetryIngestor().ingest_hook_events(events, "dev@example.com", 123)

    assert client.post.call_args.args[0] == f"{ENDPOINT}/v1/logs"
    assert client.post.call_args.kwargs["headers"] == {"Content-Type": "application/json"}
    payload = json.loads(client.post.call_args.kwargs["content"])
    record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    attrs = {a["key"]: a["value"]["stringValue"] for a in record["attributes"]}
    assert attrs["event_type"] == "agent.session.start"
    assert attrs["user.email"] == "dev@example.com"
    assert record["observedTimeUnixNano"] == "123"
    assert result.body == b"{}"


@pytest.mark.asyncio
async def test_hook_events_without_a_sender_email_carry_no_email_attribute():
    cm, client = _collector((200, b"{}", "application/json"))

    with patch.object(ch_ingestor.httpx, "AsyncClient", return_value=cm):
        await ClickHouseTelemetryIngestor().ingest_hook_events([{"type": "agent.session.start"}], "", 1)

    record = json.loads(client.post.call_args.kwargs["content"])["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    assert "user.email" not in {a["key"] for a in record["attributes"]}
