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

"""ClickHouse ingest: telemetry is handed to the OTel Collector, whose exporter writes ClickHouse.

OTLP bodies are forwarded byte for byte; plugin hook events become OTLP/JSON log records
carrying an `event_type` attribute, which `mv_hook_events` routes into the hook table.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from codemie.configs.config import config
from codemie.repository.cli_analytics.hook_events import safe_str, timestamp_to_ns
from codemie.repository.cli_analytics.ports import (
    IngestResult,
    OtlpSignal,
    TelemetryStorageUnavailableError,
    TelemetryUpstreamConfigurationError,
)
from codemie.repository.cli_analytics.vocabulary import HOOK_ATTRIBUTE_KEYS

logger = logging.getLogger(__name__)

EVENT_SEVERITY: dict[str, tuple[str, int]] = {
    "agent.tool.error": ("ERROR", 17),
    "agent.turn.error": ("ERROR", 17),
    "agent.tool.denied": ("WARN", 13),
}
DEFAULT_SEVERITY: tuple[str, int] = ("INFO", 9)

_RETRY_DELAYS = (1.0, 2.0)
_MAX_ATTEMPTS = len(_RETRY_DELAYS) + 1
_HTTPX_TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=5.0, pool=1.0)


async def _forward(url: str, body: bytes, content_type: str) -> IngestResult:
    """POST to the collector, retrying transport errors and 5xx answers."""
    last_exc: Exception | None = None
    last_status: int | None = None
    async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
        for attempt in range(_MAX_ATTEMPTS):
            if attempt > 0:
                await asyncio.sleep(_RETRY_DELAYS[attempt - 1])
            try:
                resp = await client.post(url, content=body, headers={"Content-Type": content_type})
                if 200 <= resp.status_code < 300:
                    return IngestResult(body=resp.content, media_type=resp.headers.get("content-type"))
                if resp.status_code < 500:
                    logger.error("OTel Collector returned unexpected %d — check routing config", resp.status_code)
                    raise TelemetryUpstreamConfigurationError("Analytics collector configuration error")
                last_status = resp.status_code
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning("OTel Collector unreachable at %s: %s", url, exc)
            except TelemetryUpstreamConfigurationError:
                raise
            except Exception as exc:
                last_exc = exc
                # An f-string: with exc_info set, the project's log formatter replaces the message
                # and %-style arguments would be lost.
                logger.exception(f"Unexpected error forwarding to {url}")
                break
    reason = f"upstream {last_status}" if last_status else str(last_exc)
    logger.error("Analytics collector unavailable after %d attempts: %s", _MAX_ATTEMPTS, reason)
    raise TelemetryStorageUnavailableError("Analytics collector unavailable")


def _event_to_log_record(event: dict, now_ns: int, user_email: str = "") -> dict:
    severity_text, severity_number = EVENT_SEVERITY.get(event.get("type", ""), DEFAULT_SEVERITY)
    ts_ns = timestamp_to_ns(event, now_ns)
    attributes = [
        {"key": "event_type", "value": {"stringValue": safe_str(event.get("type"))}},
        *({"key": key, "value": {"stringValue": safe_str(event.get(key))}} for key in HOOK_ATTRIBUTE_KEYS[1:]),
    ]
    if user_email:
        attributes.append({"key": "user.email", "value": {"stringValue": user_email}})
    return {
        "timeUnixNano": str(ts_ns),
        "observedTimeUnixNano": str(now_ns),
        "severityNumber": severity_number,
        "severityText": severity_text,
        "body": {"stringValue": ""},
        "attributes": attributes,
    }


def _build_otlp_logs_payload(records: list[dict]) -> bytes:
    return json.dumps(
        {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [{"key": "service.name", "value": {"stringValue": "codemie-agent-hooks"}}]
                    },
                    "scopeLogs": [{"scope": {}, "logRecords": records}],
                }
            ]
        }
    ).encode()


class ClickHouseTelemetryIngestor:
    """The ClickHouse `CliTelemetryIngestor`: forwards to the OTel Collector."""

    async def ingest_otlp(self, signal: OtlpSignal, body: bytes, content_type: str) -> IngestResult:
        # Read per request: the endpoint is deployment configuration, not state of this object.
        return await _forward(f"{config.ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT}/v1/{signal}", body, content_type)

    async def ingest_hook_events(self, events: list[dict], user_email: str, received_at_ns: int) -> IngestResult:
        records = [_event_to_log_record(e, received_at_ns, user_email=user_email) for e in events]
        return await _forward(
            f"{config.ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT}/v1/logs",
            _build_otlp_logs_payload(records),
            "application/json",
        )
