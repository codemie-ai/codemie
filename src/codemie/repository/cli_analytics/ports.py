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
`None` exactly where the adapter documents one. Rows are plain dicts keyed by the column
aliases each method documents in the adapter.
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

    def set_classifier(self, classify: Callable[[list[str]], str] | None) -> None:
        """Hand in the delivery-framework classifier (skill names -> label) the adapter's background work
        uses. It belongs to the service layer, so an adapter never imports it. Called before `start`."""
        ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class IngestResult:
    """What the ingest endpoint answers once the telemetry is accepted.

    `accepted` is the number of records newly stored, or None when the adapter cannot
    tell.
    """

    body: bytes = b""
    media_type: str | None = None
    accepted: int | None = None


# Decides, before anything is stored, whether a request is discarded. It receives every explicit
# project value the request carries and answers the discard reason, or None to store the request.
# It lives in the service layer (policy, access), which the adapter never imports; it may raise
# TelemetryStorageUnavailableError when it cannot decide.
#
# A gate that accepts a request may also name the project it accepted it for, as the stored
# (canonical) name, in a `canonical_project` attribute set when it answers None; read it with
# `accepted_project`. The request may spell the project in another case than the stored name, and
# attribution must follow the project that was found, not the spelling that was sent.
IngestGate = Callable[[frozenset[str]], Awaitable[str | None]]


def accepted_project(gate: IngestGate | None) -> str | None:
    """The stored name of the project the gate accepted the request for, None when it names none."""
    name = getattr(gate, "canonical_project", None)
    return name if isinstance(name, str) and name else None


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


@runtime_checkable
class CliTelemetryIngestor(Protocol):
    async def ingest_otlp(
        self, signal: OtlpSignal, body: bytes, content_type: str, gate: IngestGate | None = None
    ) -> IngestResult:
        """Store one OTLP/HTTP export request (`application/x-protobuf` or `application/json`).

        A `gate` that answers a reason discards the request: nothing is stored and the answer is
        the OTLP success response whose partial_success reports the records sent and the reason.
        """
        ...

    async def ingest_hook_events(
        self, events: list[dict], user_email: str, received_at_ns: int, gate: IngestGate | None = None
    ) -> IngestResult:
        """Store plugin hook events parsed from `/event-hooks` NDJSON.

        `user_email` is the authenticated sender; `received_at_ns` stands in for a missing
        or unparseable event timestamp. A `gate` that answers a reason discards the events and the
        answer is `{"accepted": 0, "dropped": N, "reason": ...}`.
        """
        ...


@runtime_checkable
class CliAnalyticsReader(Protocol):
    """The fact queries behind the CLI Analytics endpoints."""

    async def get_cost_kpis(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_model_breakdown(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_session_model_costs(self, f: LocalAnalyticsFilter) -> Rows: ...

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

    async def get_session_detail_model_costs(self, session_id: str) -> Rows: ...

    async def get_session_detail_scalars(self, session_id: str) -> Rows: ...

    async def get_session_detail_tools(self, session_id: str) -> Rows: ...

    async def get_session_detail_events(self, session_id: str) -> Rows: ...

    async def get_session_detail_dispatches(self, session_id: str) -> Rows: ...
