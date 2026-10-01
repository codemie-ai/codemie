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

"""Ports of the CLI Analytics storage: what the API and the handler need from any engine.

Reader row contract, identical for every adapter because `LocalAnalyticsHandler` and the
response models are shared: timestamps are naive UTC `datetime`, days are `date`, counts
are `int`, money is `float`, arrays are `list[str]`, and a missing string is `''` or
`None` exactly where ClickHouse returns one. Rows are plain dicts keyed by the column
aliases each method documents in the ClickHouse adapter.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter

Rows = list[dict[str, Any]]
OtlpSignal = Literal["logs", "metrics", "traces"]


@dataclass(frozen=True)
class ScheduledJob:
    """Background work an adapter needs, run by the service layer's scheduler."""

    job_id: str
    interval_seconds: int
    run: Callable[[], Awaitable[None]]


@runtime_checkable
class CliAnalyticsRuntime(Protocol):
    """Lifecycle of an adapter that owns resources or background work."""

    async def start(self) -> None:
        """Prepare storage (schema, partitions). Must not raise: failures are logged and retried by the jobs."""
        ...

    def jobs(self) -> list[ScheduledJob]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class IngestResult:
    """What the ingest endpoint answers once the telemetry is accepted.

    `accepted` is the number of records newly stored, or None when the adapter cannot
    tell (the ClickHouse adapter hands the body to the OTel Collector).
    """

    body: bytes = b""
    media_type: str | None = None
    accepted: int | None = None


class CliAnalyticsStorageConfigError(Exception):
    """The configured storage refuses its configuration; only fixing the configuration helps.

    Its message names settings or parameters, never values from them.
    """


class TelemetryIngestError(Exception):
    """Base class for ingest failures the API turns into an HTTP status."""


class InvalidTelemetryPayloadError(TelemetryIngestError):
    """The body cannot be decoded or stored as sent; retrying the same body will not help."""


class UnsupportedTelemetryContentTypeError(TelemetryIngestError):
    """The body's media type is not one the adapter can decode."""


class TelemetryStorageUnavailableError(TelemetryIngestError):
    """Storage is temporarily unavailable or saturated; the client should retry later."""


class TelemetryUpstreamConfigurationError(TelemetryIngestError):
    """The upstream collector rejected the request, which points to a misconfiguration."""


@runtime_checkable
class CliTelemetryIngestor(Protocol):
    async def ingest_otlp(self, signal: OtlpSignal, body: bytes, content_type: str) -> IngestResult:
        """Store one OTLP/HTTP export request (`application/x-protobuf` or `application/json`)."""
        ...

    async def ingest_hook_events(self, events: list[dict], user_email: str, received_at_ns: int) -> IngestResult:
        """Store plugin hook events parsed from `/event-hooks` NDJSON.

        `user_email` is the authenticated sender; `received_at_ns` stands in for a missing
        or unparseable event timestamp.
        """
        ...


@runtime_checkable
class CliAnalyticsReader(Protocol):
    """The fact queries behind the CLI Analytics endpoints."""

    async def get_cost_kpis(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_model_breakdown(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_cost_by_user(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_users(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_users_daily_activity(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_users_last_active(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_lines_totals(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_lines_daily(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_lines_by_user(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_lines_by_session(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_turns_by_session(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_file_facts_by_session(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_tool_success_by_session(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_tool_usage(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_invocations(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_skill_names_by_session(self, session_ids: list[str]) -> Rows: ...

    async def get_session_durations(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_active_ms_by_session(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_session_cost_facts(
        self,
        f: LocalAnalyticsFilter,
        search: str | None = None,
        is_unattributed: bool = False,
    ) -> Rows: ...

    async def get_session_start_times(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_repository_sessions(self, f: LocalAnalyticsFilter, search: str | None = None) -> Rows: ...

    async def get_session_detail_meta(self, session_id: str) -> Rows: ...

    async def get_session_detail_cost(self, session_id: str) -> Rows: ...

    async def get_session_detail_scalars(self, session_id: str) -> Rows: ...

    async def get_session_detail_tools(self, session_id: str) -> Rows: ...

    async def get_session_detail_events(self, session_id: str) -> Rows: ...

    async def get_session_detail_dispatches(self, session_id: str) -> Rows: ...
