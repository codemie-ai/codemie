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

from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter

Rows = list[dict[str, Any]]
OtlpSignal = Literal["logs", "metrics", "traces"]
# The session-list sorts the database decides: `get_session_page` (`start_time`, `ctx_per_call`), `get_cost_page`
# (`cost_usd`, which needs the catalog `ModelRates`) and `get_framework_page` (any sort, for the framework filter).
SESSION_PAGE_SORTS = ("start_time", "cost_usd", "ctx_per_call")


@dataclass(frozen=True)
class ModelRates:
    """Per-token catalog rates of one raw model name, resolved by the service layer (the repository never
    imports it). A rate is None when the model has no catalog entry or the catalog lacks that kind.
    `cache_write` prices the 5-minute and unsplit cache writes, `cache_write_1h` the one-hour writes."""

    model: str
    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None
    cache_write_1h: float | None = None


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
    # True when the request was discarded (a gate or session guard reason): the answer carries no publication echo.
    discarded: bool = False


# Decides, before anything is stored, whether a request is discarded. It receives every explicit
# project value the request carries and answers the discard reason, or None to store the request.
# It lives in the service layer (policy, access), which the adapter never imports; it may raise
# TelemetryStorageUnavailableError when it cannot decide.
#
# A gate that accepts a request may also name the project it accepted it for, as the stored
# (canonical) name, in a `canonical_project` attribute set when it answers None; read it with
# `accepted_project`. The request may spell the project in another case than the stored name, and
# attribution must follow the project that was found, not the spelling that was sent.
#
# `user_hash` is a short keyed hash of the sender for the discard log line, None when none is available.
# It is read only when a discard is recorded, so a gate computes it lazily.
class IngestGate(Protocol):
    async def __call__(self, explicit: frozenset[str]) -> str | None: ...

    @property
    def user_hash(self) -> str | None: ...


@dataclass(frozen=True)
class SessionBinding:
    """The resolved destination of an evidence request (EPMCDME-16440), handed to the ingestor.

    The owner is the authenticated sender (`user_email` of the ingest call). Under the per-session lock
    the ingestor binds a session that has none to these values, or keeps an existing binding; the hook
    rows of the request are stored under the effective binding. `subscription_type` is the billing class and
    `project_origin == "personal"` the private scope: a Personal-paid binding with that origin is the owner's
    personal project, which a session bound elsewhere never joins. A Personal-paid binding of a user type
    without a personal project (external, service account) has a shared origin and a work project.
    """

    project_name: str
    subscription_type: str
    project_origin: str = ""
    route_note: str = ""
    client_subscription_type: str = ""
    client_policy_version: str = ""
    # The publication contract the session was bound under; None = bound before it (legacy).
    contract_version: str | None = None
    work_consent: str | None = None
    funding_route: str | None = None


# A gate that accepts an evidence request also hands over, in attributes set when it answers None, the
# destination it chose (`binding`, a SessionBinding) and `validate_destination`: an async callable the
# ingestor awaits under the session lock with the EFFECTIVE binding of each session whose project is not
# the one the gate resolved (a bound or legacy project replaces the candidate). It answers a final discard
# reason or None, and raises TelemetryStorageUnavailableError when the destination must be retried later. The
# second argument is True for a session that is already bound: its project confirmed gone or inaccessible is a
# final discard (`session_project_unavailable`), where an unbound session's is retried.
DestinationValidator = Callable[["SessionBinding", bool], Awaitable[str | None]]


# A gate that also has `resolve_session` lets the ingestor resolve the destination UNDER the session lock, once
# the session's stored state is known: `(bound, legacy_project, hook_repo_remote)` -> the SessionBinding to
# store under, or a final discard reason (a str). `bound` is the session's existing binding, else None;
# `legacy_project` is the project of an unbound session's legacy rows. It raises
# TelemetryStorageUnavailableError when the request must be retried. A bound session is validated against its
# bound destination only: an unrelated candidate failure never discards it, and nothing is provisioned for a
# request the session guard would discard.
SessionResolver = Callable[["SessionBinding | None", "str | None", "str | None"], Awaitable["SessionBinding | str"]]

# Session guard reasons (also the discard reasons of the ingest answer).
REASON_OWNER_CONFLICT = "session_owner_conflict"
REASON_PROJECT_CONFLICT = "session_project_conflict"
REASON_SESSION_BOUNDARY = "evidence_session_conflict"
REASON_CONTRACT_CONFLICT = "session_contract_conflict"


def gate_resolver(gate: IngestGate | None) -> SessionResolver | None:
    resolver = getattr(gate, "resolve_session", None)
    return resolver if callable(resolver) else None


def gate_binding(gate: IngestGate | None) -> "SessionBinding | None":
    binding = getattr(gate, "binding", None)
    return binding if isinstance(binding, SessionBinding) else None


def gate_validator(gate: IngestGate | None) -> DestinationValidator | None:
    validator = getattr(gate, "validate_destination", None)
    return validator if callable(validator) else None


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


class TelemetryPayloadTooLargeError(TelemetryIngestError):
    """The request carries more distinct sessions than one request may lock (HTTP 413); nothing was stored."""


class UnsupportedTelemetryContentTypeError(TelemetryIngestError):
    """The body's media type is not one the adapter can decode."""


class StalePolicyError(TelemetryIngestError):
    """The evidence request's classification policy is out of date (HTTP 409); nothing was stored."""

    def __init__(self, policy_version: str) -> None:
        super().__init__("stale_policy")
        self.policy_version = policy_version


class PublicationRejectedError(TelemetryIngestError):
    """A contract request refused with `{"reason", "disposition"}`: 422 is final, 503 a hold (nothing is stored)."""

    def __init__(self, status_code: int, reason: str) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason
        self.disposition = "final" if status_code == 422 else "hold"


class TelemetryStorageUnavailableError(TelemetryIngestError):
    """Storage is temporarily unavailable or saturated; the client should retry later."""


@runtime_checkable
class CliTelemetryIngestor(Protocol):
    async def ingest_otlp(
        self,
        signal: OtlpSignal,
        body: bytes,
        content_type: str,
        gate: IngestGate | None = None,
        *,
        user_email: str = "",
        binding: SessionBinding | None = None,
        evidence_session_id: str | None = None,
        stale_policy_version: str | None = None,
    ) -> IngestResult:
        """Store one OTLP/HTTP export request (`application/x-protobuf` or `application/json`).

        A `gate` that answers a reason discards the request: nothing is stored and the answer is
        the OTLP success response whose partial_success reports the records sent and the reason.
        `user_email` is the authenticated sender, checked against a bound session's owner (empty: not
        checked); `binding` makes it an evidence request (see `SessionBinding`, requires `user_email`).
        A session guard discard (`session_owner_conflict`, `session_project_conflict`) answers the same way.
        `evidence_session_id` (evidence request, never blank: the route answers 400 for a missing id) is the one
        session the request may carry: any other session id discards it as `evidence_session_conflict` before the
        gate runs. The gate may supply the
        `binding` and a destination validator itself (see `gate_binding`, `gate_validator`), or resolve it under
        the session lock (`gate_resolver`). `stale_policy_version` set (the current version) answers
        `StalePolicyError` after the session boundary check and before anything else (spec 4 order).
        An evidence request without an authenticated sender email is discarded `session_owner_conflict`.
        """
        ...

    async def ingest_hook_events(
        self,
        events: list[dict],
        user_email: str,
        received_at_ns: int,
        gate: IngestGate | None = None,
        *,
        binding: SessionBinding | None = None,
        evidence_session_id: str | None = None,
        stale_policy_version: str | None = None,
    ) -> IngestResult:
        """Store plugin hook events parsed from `/event-hooks` NDJSON.

        `user_email` is the authenticated sender; `received_at_ns` stands in for a missing
        or unparseable event timestamp. A `gate` that answers a reason discards the events and the
        answer is `{"accepted": 0, "dropped": N, "reason": ...}`; a session guard discard adds
        `other_sessions_dropped`. `binding` as for `ingest_otlp`.
        """
        ...


@runtime_checkable
class CliAnalyticsReader(Protocol):
    """The fact queries behind the CLI Analytics endpoints."""

    async def get_cost_kpis(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_model_breakdown(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_session_model_costs(self, f: LocalAnalyticsFilter, *, spend_only: bool = False) -> Rows: ...

    def bounded(self) -> AbstractAsyncContextManager[CliAnalyticsReader]:
        """A reader whose statements run on the one pool connection one request holds, released at block end."""
        ...

    def widened(self) -> AbstractAsyncContextManager[CliAnalyticsReader]:
        """This reader with a second pool connection for the block, at most two statements at a time (one connection
        if the pool has no second free soon)."""
        ...

    async def get_window_model_flags(self, f: LocalAnalyticsFilter) -> list[tuple[str, tuple[bool, ...]]]: ...

    async def get_session_funding_costs(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_class_lists(self, f: LocalAnalyticsFilter, by: str = "all") -> Rows: ...

    async def get_cost_by_user(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_users(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_users_daily_activity(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_users_last_active(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_lines_totals(self, f: LocalAnalyticsFilter) -> Rows:
        """Summed lines; NULL sums when no selected session has a lines row (unknown)."""
        ...

    async def get_lines_daily(self, f: LocalAnalyticsFilter) -> Rows:
        """Net lines per day with a lines row, plus a `nonzero` flag per day."""
        ...

    async def get_lines_by_user(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_lines_by_session(self, f: LocalAnalyticsFilter) -> Rows:
        """A row per session with a lines row on any day (zero-sum rows report 0); unknown sessions absent."""
        ...

    async def get_turns_by_session(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_file_facts_by_session(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_tool_success_by_session(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_tool_usage(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_invocations(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_skill_names_by_session(self, session_ids: list[str], *, scope: Any) -> Rows: ...

    async def get_session_durations(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_active_ms_by_session(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_session_cost_facts(
        self,
        f: LocalAnalyticsFilter,
        search: str | None = None,
        is_unattributed: bool = False,
        include_costless: bool = False,
    ) -> Rows: ...

    async def get_session_page(
        self,
        f: LocalAnalyticsFilter,
        sort_by: str,
        page: int,
        per_page: int,
        search: str | None = None,
        is_unattributed: bool = False,
        rates: Sequence[ModelRates] | None = None,
    ) -> tuple[int, list[str]]: ...

    async def get_cost_models(self, f: LocalAnalyticsFilter) -> list[str]:
        """The raw model names token rows carry in the filtered window and viewer scope: the models whose catalog
        rates the cost sort resolves."""
        ...

    async def get_cost_page(
        self,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        rates: Sequence[ModelRates],
        search: str | None = None,
        is_unattributed: bool = False,
    ) -> tuple[int, list[str], list[tuple[str, tuple[bool, ...]]]]:
        """One cost-ordered page `(total, session_ids, model_flags)`; the flags cover the whole filtered window."""
        ...

    async def get_framework_page(
        self,
        f: LocalAnalyticsFilter,
        sort_by: str,
        page: int,
        per_page: int,
        matching: Callable[[list[list[str]]], list[list[str]]],
        rates: Sequence[ModelRates] | None = None,
        search: str | None = None,
        is_unattributed: bool = False,
    ) -> tuple[int, list[str], list[tuple[str, tuple[bool, ...]]]]:
        """One page of the sessions whose classifier name set `matching` accepts (the framework filter, a Python
        rule): `(total, session_ids, model_flags)`, in the order of the other pages. Runs on one connection."""
        ...

    async def get_session_start_times(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_repository_sessions(self, f: LocalAnalyticsFilter, search: str | None = None) -> Rows: ...

    async def get_framework_sessions(
        self, start: datetime, end: datetime, framework_labels: Sequence[str]
    ) -> list[tuple[str, str, str, int]]: ...

    async def authorize_session(self, session_id: str, scope: Any) -> bool: ...

    async def get_session_detail_meta(self, session_id: str, *, scope: Any) -> Rows: ...

    async def get_session_detail_cost(self, session_id: str, *, scope: Any) -> Rows: ...

    async def get_session_detail_model_costs(self, session_id: str, *, scope: Any) -> Rows: ...

    async def get_session_detail_scalars(self, session_id: str, *, scope: Any) -> Rows: ...

    async def get_session_detail_tools(self, session_id: str, *, scope: Any) -> Rows: ...

    async def get_session_detail_events(self, session_id: str, *, scope: Any) -> Rows: ...

    async def get_session_detail_dispatches(self, session_id: str, *, scope: Any) -> Rows: ...
