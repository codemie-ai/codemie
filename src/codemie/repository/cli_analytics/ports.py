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

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
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
class CostGroup:
    """One row of a grouped cost result: the key columns its grouping uses and the money summed under them. Money is
    `float` already (the reader converts every value), so a zero reads back as 0.0."""

    keys: Mapping[str, str | bool | None]
    cost_usd: float
    cache_read_cost_usd: float
    session_count: int
    unpriced_models: tuple[str, ...]
    cost_incomplete: bool
    # Some usage of the group had a price. None means the source did not supply pricing evidence.
    has_priced: bool | None = None


# The groupings a dashboard request asks for, by the name each carries in the `grouping` column.
G_TOTAL = "total"
G_USER = "user"
G_MODEL = "model"
G_ROUTE = "route"
G_PERSONAL = "personal"
G_USER_ROUTE = "user_route"
G_USER_PERSONAL = "user_personal"
G_MODEL_ROUTE = "model_route"
G_MODEL_PERSONAL = "model_personal"


@dataclass(frozen=True)
class GroupedMoney:
    """The rows of each requested grouping, keyed by the `G_*` names; a grouping with no row has an empty list."""

    groups: Mapping[str, list[CostGroup]]


@dataclass(frozen=True)
class UsersMoneyPage:
    """One page of the Users ranking: the page's developers in rank order, the count of all developers of the window
    (before LIMIT/OFFSET) and the money of the window. `money` holds the window groupings (`G_TOTAL`, `G_ROUTE`,
    `G_PERSONAL`) for every developer and the per-user ones (`G_USER`, `G_USER_ROUTE`, `G_USER_PERSONAL`) for the
    page's developers only; a developer with no priced row has no row in them."""

    developers: list[str]
    total_count: int
    money: GroupedMoney


@dataclass(frozen=True)
class RollupUsers:
    """The Users page's data for a whole window from the per-user start-day rollup: `money_page` ranks and prices every
    developer (`developers` is the whole ranking, no page cut), the lists are the rows the live statements return for
    every developer (`get_users`, `get_developer_session_facts`, `get_lines_by_user`, `get_users_last_active`)."""

    money_page: UsersMoneyPage
    user_rows: list[dict]
    developer_facts: list[dict]
    lines_rows: list[dict]
    last_active_rows: list[dict]


@dataclass(frozen=True)
class RollupCost:
    """/cost's data for a whole window from the per-user start-day rollup: `money` holds the spend total, per developer
    (the sessions with a `cost_daily` row) and per model groupings (`G_TOTAL`, `G_USER`, `G_MODEL`), `developers` the
    developers `get_cost_by_user` lists, and the rest the totals `get_cost_kpis` gives."""

    money: GroupedMoney
    developers: list[str]
    total_sessions: int
    spend_sessions: int
    total_tokens: int


@dataclass(frozen=True)
class RollupOverview:
    """/overview's data for a whole window from the per-user start-day rollup, in the shapes the live statements return
    (each a list of rows with a `part` column): `money_rows` (`get_overview_money`: the spend and the dead sessions; no
    estimate buckets), `fact_rows` (`get_overview_facts`), `model_rows` (`get_overview_models`, in its order, no class
    lists), `kpi_rows` (`get_dashboard_kpis`) and `line_rows` (`get_overview_lines`, no class lists)."""

    money_rows: list[dict]
    fact_rows: list[dict]
    model_rows: list[dict]
    kpi_rows: list[dict]
    line_rows: list[dict]
    total_users: int | None = None  # the distinct developers of the window, when asked for


@dataclass(frozen=True)
class RollupEfficiency:
    """/efficiency's data for a whole window from the start-day rollup, in the shapes the live statements return (each a
    list of rows with a `part` column): `fact_rows` (`get_efficiency_facts`: `worst` with the prompt, title, repository
    and branch of that session, `depth`, `all`, `code`; no class lists), `money_rows` (`get_efficiency_money`: the spend
    and the dead sessions with their cost; no estimate buckets) and `kpi_rows` (`get_dashboard_kpis`)."""

    fact_rows: list[dict]
    money_rows: list[dict]
    kpi_rows: list[dict]


@dataclass(frozen=True)
class RollupTools:
    """The Tools & Models tab's data for a whole window from the start-day rollup, in the row shapes of the live
    statements: `tool_rows` (`get_tool_usage`: `tool_name`, `call_count`, `success_count`; the window's top 20),
    `model_rows` (`get_model_breakdown`: `model_name`, `session_count`, `total_tokens`; every model, the handler keeps
    the ones with tokens) and `invocation_rows` (`get_invocations`: `kind`, `name`, `count`; every name, the handler
    cuts each kind). No class lists."""

    tool_rows: list[dict]
    model_rows: list[dict]
    invocation_rows: list[dict]


@dataclass(frozen=True)
class RollupRanking:
    """The Repositories ranking of a whole window from the start-day rollup, in the row shapes of the live statements:
    `rows` as `get_repository_ranking` returns them (`group` rows of the page, `count`, `total` and the funding
    `bucket` rows of the page's groups; no `cache_read_cost_usd`) and `fact_rows` as `get_repository_facts` returns
    them for those groups."""

    rows: list[dict]
    fact_rows: list[dict]


@dataclass(frozen=True)
class RollupProjects:
    """One page of the project breakdown of a whole window from the start-day rollup: `total` projects, the page's
    `project_names` in order (None = unattributed), the page's groups (`group_rows`, `fact_rows`, `repository_buckets`
    in the shapes of `get_repository_ranking` with branches, `get_repository_facts` and its `bucket` rows), the
    funding buckets per project (`project_buckets`) and of the whole window (`window_buckets`) as `get_project_buckets`
    returns them, and the raw names of the window's unpriced spend models."""

    total: int
    project_names: list[str | None]
    group_rows: list[dict]
    fact_rows: list[dict]
    repository_buckets: list[dict]
    project_buckets: list[dict]
    window_buckets: list[dict]
    unpriced_models: list[str]


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


# The rates of the raw model names given, keyed by name, each resolved exactly as a request resolves the raw names of
# its window (the service layer's `model_rates_for`: raw, unwrapped, normalised and alias names), so a stored cost key
# and today's statement price every raw name alike.
RatesFor = Callable[[Iterable[str]], Mapping[str, ModelRates]]


@dataclass(frozen=True)
class ProjectPage:
    """One page of the project breakdown as the database decided it (`get_project_page`).

    `project_names` is the page in order (None = the unattributed project, last, only without `drilldown`) and
    `session_ids` the matching sessions of those projects. With `drilldown` the unattributed project is not ranked or
    counted (`total` counts named projects only): `unattributed_session_ids` are its sessions, filled only on the last
    page and never under a search. `matched_session_ids` every matching session of the window (None without a search:
    all of them) and `model_flags` the priced-kind flags of the window's spend models (`record_unpriced_from_flags`)."""

    total: int
    project_names: list[str | None]
    session_ids: list[str]
    unattributed_session_ids: list[str]
    matched_session_ids: list[str] | None
    model_flags: list[tuple[str, tuple[bool, ...]]]


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

    def set_rates(self, current_version: Callable[[], str] | None, rates: RatesFor | None = None) -> None:
        """Hand in the callable that answers the current catalog rates version (see `RatesVersionProvider`), which
        stamps and checks the stored session cost keys, and the `RatesFor` callable the refresher and the maintenance
        price the stored keys with. Both are built from the service layer's catalog; the version changes whenever
        anything the rate resolution reads changes. Called before `start`."""
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


class StatementTimeout(TimeoutError):
    """A statement outran the client-side command timeout. A TimeoutError still (other endpoints treat it as before);
    a connect or pool-wait timeout is a plain TimeoutError, so a dashboard can tell a slow query from a dead store."""


class RequestBudgetExceeded(StatementTimeout):
    """A dashboard request outran its whole budget (the wait for a slot and every statement), not one statement."""


class DashboardAcquireTimeout(RequestBudgetExceeded):
    """A dashboard request waited past `CLI_ANALYTICS_DASHBOARD_ACQUIRE_TIMEOUT_S` for its lane's slot or a share
    of the pool: the pod is busy, not the store down. A `RequestBudgetExceeded`, so a route mapping that maps this."""


class LiveMergeNeeded(Exception):
    """A ranking rollup read would run live statements (the sessions resumed from before the window) while the caller
    holds a lane that may not: the caller re-enters the heavy lane and reads again."""


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

    def set_rates(self, current_version: Callable[[], str] | None) -> None:
        """Hand in the callable that answers the current catalog rates version; see `CliAnalyticsRuntime`."""
        ...

    async def get_cost_kpis(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_dashboard_kpis(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_overview_user_count(self, f: LocalAnalyticsFilter) -> int: ...

    async def get_model_breakdown(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_session_model_costs(self, f: LocalAnalyticsFilter) -> Rows: ...

    def bounded(self) -> AbstractAsyncContextManager[CliAnalyticsReader]:
        """A reader whose statements run on the one pool connection one request holds, released at block end."""
        ...

    async def materialize_sel(self, f: LocalAnalyticsFilter, cohort: str | None = None) -> None:
        """On a bounded reader: evaluate the window's `sel` once into a temp table of the held connection, which the
        later statements of the request for the same selection read. Dropped when the bounded block ends. `cohort`
        keeps only the sessions that started in the window (`started`) or before it (`before`); the user rollup reads
        them apart."""
        ...

    async def materialize_files(self, f: LocalAnalyticsFilter) -> None:
        """On a bounded reader, after `materialize_sel`: the changed-file counts of the selected sessions once into a
        temp table of the held connection, dropped with `sel`'s."""
        ...

    def widened(self) -> AbstractAsyncContextManager[CliAnalyticsReader]:
        """This reader with a second pool connection for the block, at most two statements at a time (one connection
        if the pool has no second free soon)."""
        ...

    async def get_window_model_flags(self, f: LocalAnalyticsFilter) -> list[tuple[str, tuple[bool, ...]]]: ...

    async def get_session_funding_costs(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_class_lists(self, f: LocalAnalyticsFilter, by: str = "all") -> Rows: ...

    async def get_cost_money(
        self, f: LocalAnalyticsFilter, rates: Sequence[ModelRates], buckets: bool = True
    ) -> GroupedMoney:
        """The /cost money grouped in SQL from one pricing pass: the `G_*` groupings of the window. Without `buckets`
        only the spend groupings (total, per user, per model) are built; the route and personal-billed ones are
        absent (their lists stay empty)."""
        ...

    async def get_cost_by_user(self, f: LocalAnalyticsFilter, with_classes: bool = True) -> Rows:
        """The developers with a `cost_daily` row (`developer_name`), with their class lists unless `with_classes` is
        false."""
        ...

    async def get_users_money_page(
        self, f: LocalAnalyticsFilter, rates: Sequence[ModelRates], limit: int | None, offset: int
    ) -> UsersMoneyPage:
        """The developers of the window ranked by spend (`round(cost, 9)` descending, then name in code-point order),
        the page `[offset, offset + limit)` of them (all when `limit` is None) and the money of the window."""
        ...

    async def rollup_users(
        self, f: LocalAnalyticsFilter, rate_of: Callable[[str], ModelRates], max_age_s: float
    ) -> RollupUsers | None:
        """The Users page's data for the window of `f` from the per-user start-day rollup, or None when it may not
        answer (another scope or filters, a window it is not made for, a day it lacks or built under other rates, a
        job that has not run lately): the caller then reads the live statements. Same values as they give."""
        ...

    async def rollup_overview(
        self, f: LocalAnalyticsFilter, rate_of: Callable[[str], ModelRates], max_age_s: float, users: bool = False
    ) -> RollupOverview | None:
        """/overview's cards and charts for the window of `f` from the per-user start-day rollup, or None when it may
        not answer (see `rollup_users`): the caller then reads the live statements. Same values as they give; it holds
        no class lists and no estimate buckets. `users` also counts the window's developers (`total_users`, the Users
        page's `total_count`)."""
        ...

    async def rollup_efficiency(
        self, f: LocalAnalyticsFilter, rate_of: Callable[[str], ModelRates], max_age_s: float
    ) -> RollupEfficiency | None:
        """/efficiency's rows for the window of `f` from the per-user start-day rollup, or None when it may not answer
        (see `rollup_users`): the caller then reads the live statements. Same values as they give; it holds no class
        lists and no estimate buckets."""
        ...

    async def rollup_tools(
        self, f: LocalAnalyticsFilter, rate_of: Callable[[str], ModelRates], max_age_s: float
    ) -> RollupTools | None:
        """/tools' rows (the tools, the models with their tokens and the skill, agent and slash-command invocations)
        for the window of `f` from the per-user start-day rollup, or None when it may not answer (see `rollup_users`):
        the caller then reads the live statements. Same values as they give; it holds no class lists."""
        ...

    async def rollup_cost(
        self, f: LocalAnalyticsFilter, rate_of: Callable[[str], ModelRates], max_age_s: float
    ) -> RollupCost | None:
        """/cost's totals, per-developer money and per-model money for the window of `f` from the per-user start-day
        rollup, or None when it may not answer (see `rollup_users`): the caller then reads the live statements. Same
        values as they give; it holds no class lists and no estimate buckets."""
        ...

    async def rollup_repository_ranking(
        self,
        f: LocalAnalyticsFilter,
        include_branches: bool,
        rate_of: Callable[[str], ModelRates],
        max_age_s: float,
        page: int = 0,
        per_page: int | None = None,
        unattributed_last: bool = False,
    ) -> RollupRanking | None:
        """The Repositories ranking of the window of `f` (the page `page` of `per_page`, or every group) from the
        start-day rollup, or None when it may not answer (see `rollup_users`; a search is never asked: it is not in the
        filter): the caller then reads the live statements. Same values and order as `get_repository_ranking` and
        `get_repository_facts` give: sessions, then spend, then names in code-point order. `unattributed_last` (paged,
        without branches): the group without a repository is not ranked or counted and rides along with every page."""
        ...

    async def rollup_project_page(
        self,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        rate_of: Callable[[str], ModelRates],
        max_age_s: float,
        unattributed_last: bool = False,
    ) -> RollupProjects | None:
        """One page of the project breakdown of the window of `f` from the start-day rollup (no search), or None when
        it may not answer (see `rollup_users`): the caller then reads the live statements. Projects by spend, the
        unattributed one last, then by name; the same values as `get_project_page` and the rows read after it. With
        `unattributed_last` (the totals-only page) that project is not ranked or counted and is the last name of the
        last page, outside `per_page`."""
        ...

    async def get_developer_session_facts(self, f: LocalAnalyticsFilter) -> Rows:
        """Per developer (`developer`): `turns`, `tool_calls`, `tool_successes` (sums, NULL as 0), the average
        `avg_session_duration_ms` over `duration_sessions` sessions with a known duration (NULL skipped, 0 kept)."""
        ...

    async def get_avg_session_duration_ms(self, f: LocalAnalyticsFilter) -> int | None:
        """The mean duration of the window's sessions with a known duration, truncated to ms (None if none)."""
        ...

    async def get_users(self, f: LocalAnalyticsFilter) -> Rows:
        """Per developer: `developer_name`, `session_count`, the token sums, `top_model` and the class lists."""
        ...

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

    async def get_tool_usage(self, f: LocalAnalyticsFilter, *, exact: bool = False) -> Rows: ...

    async def get_invocations(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_skill_names_by_session(self, session_ids: list[str], *, scope: Any) -> Rows: ...

    async def get_session_durations(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_overview_facts(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_overview_models(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_overview_lines(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_overview_money(
        self, f: LocalAnalyticsFilter, rates: Sequence[ModelRates] = (), *, exact: bool = False
    ) -> Rows: ...

    async def get_repository_ranking(
        self,
        f: LocalAnalyticsFilter,
        include_branches: bool = False,
        search: str | None = None,
        rates: Sequence[ModelRates] = (),
        page: int = 0,
        per_page: int | None = None,
        unattributed_last: bool = False,
        *,
        exact: bool = False,
    ) -> Rows: ...

    async def get_branch_ranking(
        self,
        f: LocalAnalyticsFilter,
        rates: Sequence[ModelRates] = (),
        page: int = 0,
        per_page: int = 10,
    ) -> Rows: ...

    async def get_project_totals(self, f: LocalAnalyticsFilter, rates: Sequence[ModelRates] = ()) -> Rows:
        """The totals of every project of the sessions selected (the caller restricts `f` to the page's sessions): one
        row per project with its counts, catalog-priced `cost_usd`, `repository_count` and `session_ids`, the
        unattributed project (`project_name` NULL) last."""
        ...

    async def get_repository_buckets(
        self,
        f: LocalAnalyticsFilter,
        search: str | None = None,
        rates: Sequence[ModelRates] = (),
        repositories: Sequence[str] = (),
        unattributed: bool = False,
    ) -> Rows: ...

    async def get_project_buckets(
        self, f: LocalAnalyticsFilter, rates: Sequence[ModelRates] = (), by_project: bool = True
    ) -> Rows:
        """The funding and personal-billed estimates of the selection, per project or (`by_project` False) over all of
        it, as rows of `part` `bucket` (`b_route` / `b_billed`)."""
        ...

    async def get_repository_facts(
        self,
        f: LocalAnalyticsFilter,
        include_branches: bool = False,
        search: str | None = None,
        repositories: Sequence[str] = (),
        unattributed: bool = False,
    ) -> Rows: ...

    async def get_efficiency_facts(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_efficiency_money(self, f: LocalAnalyticsFilter, rates: Sequence[ModelRates] = ()) -> Rows: ...

    async def get_active_ms_by_session(self, f: LocalAnalyticsFilter) -> Rows: ...

    async def get_session_cost_facts(
        self,
        f: LocalAnalyticsFilter,
        search: str | None = None,
        is_unattributed: bool = False,
        include_costless: bool = False,
    ) -> Rows: ...

    async def get_session_page(  # noqa: PLR0913 - port signature mirrors the SQL page statement's parameters
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

    async def get_project_page(  # noqa: PLR0913 - the port signature of the SQL page statement
        self,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        rates: Sequence[ModelRates],
        search: str | None = None,
        drilldown: bool = False,
    ) -> ProjectPage:
        """One spend-ordered page of projects (unattributed last); `search` matches a project or repository name.
        With `drilldown`, `search` matches project names only and the unattributed project is not ranked or counted:
        its sessions are returned apart on the last page (never under a search)."""
        ...

    async def get_framework_page(  # noqa: PLR0913 - the port signature of the SQL page statement
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
