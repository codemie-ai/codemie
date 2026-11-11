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

"""The `SpendLedger` port - the only vocabulary the rest of the system speaks about spend.

**Every read of "what was spent" comes through here.** No service, router, handler, scheduler or
agent tool computes, fetches or caches a spend figure by any other means. That is the whole
mechanism: five disagreeing routes existed because each consumer was free to find its own
source, and one port is what removes the freedom rather than fixing the disagreements one at a
time. Adding a spend read outside this port is a review rejection, not a style note.

**A consumer cannot tell which adapter answered it**, and none of them branches on provider
identity - the registry does that once.

**This port reads. It never enforces.** Its figures drive tracking, reporting and what a person
is shown, and nothing else: no code path evaluates a threshold on the request path to gate,
raise, alarm or emit a violation signal. A report answers a question when asked; a check takes
an action. Computing consumption and comparing it to a configured limit *for display* is the
point of this work; the same comparison on the request path is prohibited.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import SpendDimension, SpendResult, TimeRange
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.traffic_class import UNION_CLASSES, TrafficClass

# ---------------------------------------------------------------------------
# Budget holders
# ---------------------------------------------------------------------------
# A holder is passed to the port; the predicate that defines its spend is resolved in exactly
# one place. Two units could each centralise their logic, each filter on a legitimate `attr_*`
# column, and still return different totals for the same person - so the predicate is not a
# consumer's to write.


@dataclass(frozen=True, slots=True)
class PersonalHolder:
    """A personal or global budget: one user, one category.

    `aliases` is the fallback half. A person is **not one identifier** on rows the parser
    attributed: some paths emit `users.username` and some `users.email` for the same human, so a
    reader keyed on one under-reports. Above the identity floor the canonical `users.id` is
    enough, but pre-rollout rows and foreign traffic do not stop arriving at activation.

    `aliases is None` means the bridge could not be resolved - a deleted user, an unmatched
    principal. That is **not** an empty alias set: an empty set is a resolved person with no
    extra identifiers, while `None` means the person's fallback rows cannot be *identified* at
    all, so the figure carries an `identity_bridge` entry whose portion is not computable. The
    two must not be collapsed, which is why this is `None` rather than `frozenset()`.
    """

    user_id: str
    category: BudgetCategory
    aliases: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class ProjectHolder:
    """A project budget: one project, one category."""

    project: str
    category: BudgetCategory


@dataclass(frozen=True, slots=True)
class ProjectMemberHolder:
    """A project-member budget: one project, one category, one user.

    Keyed on project **and** user id, and a pre-rollout principal row carries neither - so
    resolving the principal supplies neither key this holder needs. A figure for this holder
    over a range reaching below the identity floor is a mixed quantity and carries the label.
    """

    project: str
    category: BudgetCategory
    user_id: str


@dataclass(frozen=True, slots=True)
class ProjectGroupHolder:
    """A project budget group: several projects, one category.

    Composes from the project predicate. Cost centres do too - they are a chargeback grouping
    over projects with no budget of their own - which is why the holder list is complete rather
    than merely representative.
    """

    projects: frozenset[str]
    category: BudgetCategory


@dataclass(frozen=True, slots=True)
class PersonTotalHolder:
    """A person's total across everything, for analytics - both pots, deliberately.

    This is the one holder that spans the personal and project pots. Every other holder keeps
    them apart, because a call made with a project in context lands in the project pot only and
    does not also consume the caller's personal budget.
    """

    user_id: str
    aliases: frozenset[str] | None


#: Every holder the platform has. Enumerated rather than left open: a sixth kind would need a
#: predicate, and a predicate is what this port exists to keep in one place.
BudgetHolder = PersonalHolder | ProjectHolder | ProjectMemberHolder | ProjectGroupHolder | PersonTotalHolder


#: How many holders one batched read carries before it is split into several.
#:
#: **A property of the port, not of an adapter**, because both adapters build one filtered
#: aggregate per holder - `SUM(spend) FILTER (…)` against the ledger table, a named `filters`
#: bucket against the metric store - so in both the *statement* grows with the holder count. The
#: bound is what makes a caller who passes a whole tenant degrade into several queries instead of
#: one the server declines to plan. A list screen's page is far below it, so no ordinary read is
#: split at all.
HOLDER_BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# Per-request cost
# ---------------------------------------------------------------------------


class CostState(str, Enum):
    """Three states, not two, and the third is not a nicety.

    `PENDING` means *this cost will exist shortly, ask again*. `UNAVAILABLE` means *this ledger
    cannot answer, do not retry*. Collapsing them is forbidden in both directions: marking an
    unavailable figure pending promises a human an answer that never arrives and drives a retry
    that cannot succeed, while marking either settled presents a subtotal as a total.

    The discriminator is ledger readiness, decided once, not guessed per read.
    """

    SETTLED = "settled"
    PENDING = "pending"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RequestCost:
    """What one CodeMie unit of work cost.

    **Zero is never pending and never unavailable.** A failure row costs zero - a real, measured
    zero. An unsettled row costs *unknown*. Collapsing those two is the defect this type exists
    to prevent, so `figure` is `None` in both unsettled states and a `Decimal` only when settled.

    Finding **several** rows is normal, not an error: a retried call writes one row per upstream
    attempt and each attempt that reached a provider may have cost money, so a lookup sums the
    rows it finds. Finding **none** is pending once the ledger is activated, and unavailable
    before that.
    """

    figure: Decimal | None
    state: CostState

    def __post_init__(self) -> None:
        if self.state is CostState.SETTLED and self.figure is None:
            raise ValueError("a settled cost must carry a figure")
        if self.state is not CostState.SETTLED and self.figure is not None:
            raise ValueError(f"an unsettled cost carries no figure, got {self.figure!r} for {self.state.value}")

    @classmethod
    def settled(cls, figure: Decimal) -> RequestCost:
        return cls(figure=figure, state=CostState.SETTLED)

    @classmethod
    def pending(cls) -> RequestCost:
        """The row has not landed yet. Normal for streamed chat, not an edge case."""
        return cls(figure=None, state=CostState.PENDING)

    @classmethod
    def unavailable(cls) -> RequestCost:
        """The ledger cannot answer. The caller must stop asking."""
        return cls(figure=None, state=CostState.UNAVAILABLE)

    # The three constructors above are `classmethod`s, so calling one on an *instance* builds a new
    # cost instead of asking about that one - and the result is always truthy. `if cost.pending():`
    # therefore reads correctly and is always true. These predicates exist so the readable form is
    # also the correct one; compare `state` directly if you prefer, but never call a constructor to
    # ask a question.

    @property
    def is_settled(self) -> bool:
        return self.state is CostState.SETTLED

    @property
    def is_pending(self) -> bool:
        """A figure is coming - re-read later."""
        return self.state is CostState.PENDING

    @property
    def is_unavailable(self) -> bool:
        """No figure is coming for this record; hydration must be suppressed."""
        return self.state is CostState.UNAVAILABLE


# ---------------------------------------------------------------------------
# Report scopes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DimensionScope:
    """A report grouped or filtered by a dimension.

    `dimension` says which of the five it is; `value` is the id being asked about, or None for a
    grouped report over all of them. Only the workflow dimension has a ledger column - the rest
    resolve wholly through the correlation-id set the metric store returns for the same scope.
    """

    dimension: SpendDimension
    value: str | None = None


@dataclass(frozen=True, slots=True)
class UserTerm:
    """The person a composed scope names: their canonical id **and** every alias for them.

    Two identifiers rather than one, for the reason `PersonalHolder` carries both - a person is
    **not one identifier** on rows the fallback parser attributed, because some code paths emit
    `users.username` and some `users.email` for the same human, so a reader keyed on one
    under-reports.

    `aliases is None` means the bridge could not be resolved - a deleted user, an unmatched
    principal - and is **not** an empty set. An empty set is a resolved person who simply has no
    extra identifiers; `None` means the person's fallback rows cannot be *identified* at all, so
    the figure carries an `identity_bridge` portion whose amount is not computable. Collapsing the
    two turns *"an unknown amount is missing"* into *"nothing is missing"*, which is why this is
    `None` rather than `frozenset()`.
    """

    user_id: str
    aliases: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class ReportFilter:
    """What a dashboard asked about on a whole-window read: its users and projects filter.

    **A filter, and emphatically not an `AccessScope`.** A boundary says what the caller may see; a
    filter says what they asked for. They are kept apart because their empty states mean opposite
    things - a restricted scope with no project matches *nothing*, a filter with no term matches
    *everything* - and a merge would have to pick one. The two whole-window reads (`aggregate_by_time_bucket`,
    `aggregate_by_spend_group`) take both, and so do the holder batch behind the users- and
    projects-spending tables (`consumed_for_holders`) and the dimension tables' read
    (`aggregate_by_dimension`): `SecureQueryBuilder` already applied both to the Elasticsearch query a
    chart's or a table's rows came from, so a port figure carrying only the boundary would silently
    un-filter a filtered dashboard.

    `users` are `UserTerm`s rather than ids, for `PersonTotalHolder`'s reason: a person is not one
    identifier on the rows the fallback parser attributed, so the SQL side matches the id and every
    alias. The metric store matches the id alone, as its query builder always has.

    `personal_project_owners` are the `users.id`s owning the personal projects among `projects`, for
    the SQL side alone. A personal project's calls reach the ledger in principal form with **no
    project at all** - `AccessScope` says why - so its name matches none of its own rows, and a
    dashboard filtered to it read a measured `$0.00` (EPMCDME-13429 pass H, UD-H-D2). The metric store
    matches the name, which its documents do carry. `None` means nobody knows which of `projects` are
    personal, or whose - a failed lookup, or a personal project with no recorded owner - the same
    convention as an alias set of `None`; the ledger labels the figure rather than reading the names
    alone as complete.
    """

    users: tuple[UserTerm, ...] = ()
    projects: tuple[str, ...] = ()
    personal_project_owners: tuple[str, ...] | None = ()

    @property
    def is_empty(self) -> bool:
        return not self.users and not self.projects

    def elasticsearch_filters(self, *, user_id_field: str, project_field: str) -> list[dict]:
        """The filter as `terms` clauses for a `bool.filter` list - none when there is nothing asked."""
        clauses: list[dict] = []
        if self.users:
            clauses.append({"terms": {user_id_field: [term.user_id for term in self.users]}})
        if self.projects:
            clauses.append({"terms": {project_field: list(self.projects)}})
        return clauses


@dataclass(frozen=True, slots=True)
class ComposedScope:
    """Several report filters ANDed - *"this person, in this project, on this workflow"*.

    **A FILTER, and emphatically not a sixth holder.** AD-26 closes the holder list because *"a
    sixth kind would need a predicate"*, and this type adds none: every term below resolves through
    a predicate that already exists. The two answer different questions. A holder answers *"what
    may this budget spend"* - it is a thing an operator configures a limit against, and the port
    compares consumption to that limit for display. A scope answers *"what was spent matching these
    terms"* - nothing is ever configured against it and no limit is ever set from it. Reading this
    as a holder is what would make `ComposedScope(user=..., traffic_class=CLI)` look like a CLI
    *budget*, which is item 15's trap (`PersonalHolder(user, CLI)`) arriving from the other side.

    **Every term is optional, and an absent term means *unconstrained*** - never "matches nothing".
    A term that is present and resolves to no rows is a different thing entirely, and the
    repository keeps them apart.

    **An all-absent scope is refused rather than answered, and the refusal is the point of the
    type.** The whole period is what `aggregate(period, None)` already returns, so nothing is lost
    by rejecting it here: a caller that means the platform total says so with that method. What *is*
    gained is the case this type exists to prevent - a consumer building a scope out of optional,
    agent-supplied arguments (`platform_tool` passes four `Optional[str]`s) and omitting all of
    them would otherwise receive the **platform total** in place of one person's spend, silently,
    with an assistant quoting it to that person. That is the largest available over-report on the
    highest-consequence surface, and it is checkable at construction where the caller's own frame is
    on the error.

    **`dimension` rather than a bare workflow id**, because the two consumers need two dimensions -
    `platform_tool` passes `assistant_id` two lines above `workflow_id` - and the asymmetry between
    them is already solved exactly once: only `workflow` has a ledger column, and `DimensionScope`
    plus the repository's union machinery is what knows that. A second, workflow-shaped term here
    would be a second place that knows it.
    """

    user: UserTerm | None = None
    project: str | None = None
    dimension: DimensionScope | None = None
    traffic_class: TrafficClass | None = None
    #: The `users.id`s owning `project` when it is a personal project, whose calls carry no project in
    #: the ledger - see `ReportFilter.personal_project_owners`. They qualify the project term; not a term.
    project_owners: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if all(term is None for term in (self.user, self.project, self.dimension, self.traffic_class)):
            raise ValueError(
                "a composed scope needs at least one term - an unconstrained one is the whole "
                "period, which aggregate(period, None) already answers"
            )
        if self.project_owners and self.project is None:
            raise ValueError("project owners qualify the project term, so a scope naming no project cannot carry them")
        if self.dimension is None or self.traffic_class is None:
            return
        # A figure's `partial` holds **one portion per dimension**, so two of a read's residue
        # sources may never name the same dimension - one would overwrite the other and the result
        # would carry a qualification it cannot honour. That happens exactly here: the two union
        # classes resolve through the correlation-id set of the assistant and datasource dimensions,
        # so a class term naming one of them beside a dimension term naming the same one asks for
        # two residues over two different id sets in one slot. The pair adds no constraint either -
        # a scope's id set is a subset of its class's - so it is refused rather than merged by a
        # rule nobody could check. The classes with a column of their own collide with nothing and
        # compose freely: `traffic_class=WORKFLOW` with `dimension=assistant` is the useful case.
        if UNION_CLASSES.get(self.traffic_class) is self.dimension.dimension:
            raise ValueError(
                f"the {self.traffic_class.value} traffic class and the {self.dimension.dimension.value} "
                "dimension resolve through the same correlation-id set, so a scope naming both would "
                "need two residues in one partial slot - name one of them"
            )


@dataclass(frozen=True, slots=True)
class AccessScope:
    """Which rows the **caller is permitted to see** - the analytics access boundary, carried into
    the port so a ledger read cannot answer wider than the table it feeds.

    **A permission, and neither a report filter nor a holder.** `ComposedScope` answers *"what was
    spent matching these terms"* - a question the caller chose. This answers *"whose rows may this
    caller be shown at all"* - a question the caller does not get to choose, and the difference is
    why it travels as its own argument rather than as another `ComposedScope` term. Merging the two
    would let a consumer that forgets a term widen a **boundary** rather than merely widen a filter.

    **Why it exists.** Every Elasticsearch-backed analytics read is narrowed at construction by
    `SecureQueryBuilder`, which injects `(user_id = caller AND project IN plain) OR (project IN
    admin)` for every caller who is not an admin or an auditor. When a money column moved from that
    query to a ledger read, the row set stayed narrowed and the **figure stopped being** - so a plain
    member's own row set was footed with the whole platform's spend on the same assistant or
    workflow. This type is what carries the boundary across that seam.

    **The two renderings must agree, and that is pinned by a test rather than by intent.**
    `elasticsearch_filter` below reproduces `SecureQueryBuilder._build_role_based_filter` exactly,
    and `access_predicate` in the spend-logs repository is its SQL twin; a test asserts the ES half
    is byte-identical to the query builder's own output over a matrix of contexts, so the day one
    side is changed the other fails rather than quietly diverging.

    **The personal pot is where the two row shapes differ, and the twin follows the calls, not the
    shapes.** A call made in the caller's personal project reaches Elasticsearch carrying that
    project's name - one of the caller's own projects, so the injected filter admits it - and reaches
    the ledger in principal form, carrying no project at all. A literal SQL copy of the project term
    would therefore hide from callers their own CLI, personal-project and global-assistant spend that
    Elasticsearch shows them. So the SQL plain clause also admits the caller's own principal-form rows,
    by `user_id` alone. Not by alias: a legacy parsed row carries only a principal, and wherever one
    person's username is another's email an alias match would hand the first the second's rows - so
    those rows stay outside, below the identity floor, as they do for every holder without a user id.

    **An unrestricted scope carries no terms at all**, mirroring the query builder's own
    admin/auditor path, which appends no filter whatsoever. A **restricted** scope with no projects
    of either kind matches **nothing** - the query builder's `minimum_should_match=0`, which returns
    zero results by design and is deliberately not the same as "unconstrained".
    """

    unrestricted: bool
    user_id: str | None = None
    plain_projects: tuple[str, ...] = ()
    admin_projects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.unrestricted:
            if self.user_id is not None or self.plain_projects or self.admin_projects:
                raise ValueError(
                    "an unrestricted access scope carries no terms - a scope holding both an "
                    "unrestricted flag and a narrowing term has two readings, and the widest one wins"
                )
            return
        if not self.user_id:
            # The plain-user clause pins the caller's own id, so a restricted scope without one
            # cannot be rendered at all. Failing here beats rendering the admin half alone, which
            # would show a plain member every other member's rows in their own projects.
            raise ValueError("a restricted access scope needs the caller's user id for its plain-user clause")

    @classmethod
    def everything(cls) -> AccessScope:
        """The admin and auditor scope: no narrowing, matching the query builder's early return."""
        return cls(unrestricted=True)

    @classmethod
    def for_context(
        cls,
        *,
        user_id: str,
        plain_projects: Sequence[str],
        admin_projects: Sequence[str],
        is_admin: bool,
    ) -> AccessScope:
        """Build from an analytics `ProjectAccessContext`'s four fields, in its own vocabulary.

        Named for the context rather than taking one, so the port keeps no import of the analytics
        layer that owns that type - the caller passes the four values it already holds.
        """
        if is_admin:
            return cls.everything()
        return cls(
            unrestricted=False,
            user_id=user_id,
            plain_projects=tuple(plain_projects),
            admin_projects=tuple(admin_projects),
        )

    def elasticsearch_filter(self, *, user_id_field: str, project_field: str) -> dict | None:
        """The boundary as an Elasticsearch `bool`, or `None` when there is nothing to narrow.

        Field names are **arguments** rather than constants here: the analytics indices spell them
        `attributes.user_id.keyword` / `attributes.project.keyword`, and a ledger reading a different
        index must be able to say so without a second copy of the clause shape.
        """
        if self.unrestricted:
            return None
        should: list[dict] = []
        if self.plain_projects:
            should.append(
                {
                    "bool": {
                        "must": [
                            {"term": {user_id_field: self.user_id}},
                            {"terms": {project_field: list(self.plain_projects)}},
                        ]
                    }
                }
            )
        if self.admin_projects:
            should.append({"terms": {project_field: list(self.admin_projects)}})
        # `minimum_should_match=0` over an empty `should` returns nothing, which is the query
        # builder's own answer for a caller with no accessible projects - not "everything".
        return {"bool": {"should": should, "minimum_should_match": 1 if should else 0}}


# ---------------------------------------------------------------------------
# Time buckets
# ---------------------------------------------------------------------------


#: The three-hour width, spelled as the interval it is.
#:
#: A three-hour bucket - the grain the split spend chart draws - has no `date_trunc` unit at all, which
#: is what first put `date_bin` on this path; every other width has since followed it, for the reason
#: the bin table below argues. That needs **PostgreSQL 14+**, where `date_trunc` needs nothing; the
#: floor is taken as safe because PostgreSQL 13 reached end of life in 2025, and a 12/13 deployment
#: would have to give bucketed spend up rather than approximate it.
BUCKET_WIDTH_3_HOURS = "3 hours"

#: The instant hour, day and three-hour buckets are tiled from - **one value, read by the ledger's
#: grouping and by the walk below, and derived from the arguments of neither.**
#:
#: A bucket start the walk computes from one origin while the ledger groups by another matches none of
#: the ledger's keys, so **every figure reads as a measured zero while real money is silently dropped**
#: - the one failure on this path that nothing raises on. One shared instant is what makes that
#: divergence unrepresentable.
#:
#: **Deriving it from the range is the plausible mistake, and it misaligns exactly where it matters:**
#: the walk is handed the *requested* range and the ledger's read the *admitted* one,
#: `[max(start, F), end)`, so two range-derived origins part company precisely when a range crosses
#: the coverage floor. A fixed instant cannot part company at all. The epoch is a midnight and each of
#: these strides divides a day evenly, so binning from it puts every bucket on the boundary a
#: midnight-anchored walk would have chosen anyway.
BUCKET_ORIGIN = datetime(1970, 1, 1, tzinfo=timezone.utc)

#: The instant week buckets are tiled from: 5 January 1970, the **first Monday of the epoch**.
#:
#: 1 January 1970 was a Thursday, so a week binned from `BUCKET_ORIGIN` would start every bucket on a
#: Thursday. Four days on puts each week bucket on the ISO Monday the calendar names and every chart
#: drawn so far already shows - the same instant a Monday-anchored walk produces - so moving this width
#: onto a fixed stride re-anchors nothing.
WEEK_ORIGIN = BUCKET_ORIGIN + timedelta(days=4)

#: The widths a bucket walk can step, and the (stride, origin) each one tiles from. **Calendar widths -
#: month and up - are absent rather than approximated**: no fixed stride tiles months, so an
#: approximated one misaligns every bucket after the first, and a chart drawn on misaligned buckets
#: shows real money in the wrong period instead of failing.
#:
#: **Every width is a fixed stride from an absolute origin, and the SQL grouping bins from this same
#: table** - instant arithmetic on both sides, computed identically, so the walk and the grouping
#: cannot disagree and no session `TimeZone` can move a boundary. `date_trunc('day', ...)` over a
#: `timestamptz` truncates in the session's own zone instead, which would file a non-UTC session's rows
#: under keys no walk ever emits: the silent-zero failure above, reached by configuration alone.
_BUCKET_BINS: Mapping[str, tuple[timedelta, datetime]] = MappingProxyType(
    {
        BUCKET_WIDTH_3_HOURS: (timedelta(hours=3), BUCKET_ORIGIN),
        "hour": (timedelta(hours=1), BUCKET_ORIGIN),
        "day": (timedelta(days=1), BUCKET_ORIGIN),
        "week": (timedelta(weeks=1), WEEK_ORIGIN),
    }
)


def _bin_of(width: str) -> tuple[timedelta, datetime]:
    """The stride and origin `width` tiles from, or a raise for a width no walk can tile."""
    binning = _BUCKET_BINS.get(width)
    if binning is None:
        raise ValueError(f"a bucket walk cannot step {width!r}; the walkable widths are {sorted(_BUCKET_BINS)}")
    return binning


def bucket_step(width: str) -> timedelta:
    """The fixed step one bucket of `width` spans, or a raise for a width no walk can tile.

    Public because the ledger's `date_bin` grouping needs the very same stride the walk steps: read
    from two places, a stride and a step could disagree by a factor and the buckets would tile a
    different chart from the one the walk drew.
    """
    return _bin_of(width)[0]


def bucket_origin(width: str) -> datetime:
    """The absolute instant `width`'s buckets are tiled from, or a raise for a width no walk can tile.

    Public for the reason `bucket_step` is: the ledger binds this origin into `date_bin` and the walk
    floors against it, and two origins that part company share no bucket key at all - so every figure
    reads as a measured zero while real money is dropped, and nothing raises.
    """
    return _bin_of(width)[1]


def _align(moment: datetime, width: str) -> datetime:
    """One instant floored to its bucket, aligned exactly as the ledger's own grouping aligns it.

    **One formula for every width**, because every width is a fixed stride from an absolute origin:
    whole strides from that origin, floored - which is exactly what `date_bin(stride, ts, origin)`
    computes server-side, off the same two values. The same arithmetic over the same inputs cannot
    produce two answers, and neither side consults a `TimeZone`, so a session set to one cannot move a
    boundary here. That matters because a start computed even a minute off matches none of the keys the
    ledger returns, and every bucket then reads as a measured zero while the money is quietly dropped -
    the one failure here that nothing raises on.

    UTC throughout, like every instant in this design, so no local-midnight or DST case arises.
    """
    step, origin = _bin_of(width)
    return origin + (moment - origin) // step * step


def bucket_starts(period: TimeRange, width: str) -> tuple[datetime, ...]:
    """Every bucket start a range covers - **walked from the range, never read off the rows.**

    A property of the port rather than of an adapter, because it is what makes "a bucket the table
    holds no rows for is a measured zero" executable. Buckets inferred from the returned rows cannot
    express the empty one at all: a chart built from them draws a gap where it must draw a zero, and
    a gap reads as *no data* where the truth is *no spend*.

    The first start may lie **before** `period.start`, and that is required rather than tolerated:
    a week bin files a Wednesday's rows under that week's Monday, and a three-hour bin files 07:12
    under 06:00, so a walk beginning at the requested instant would key every bucket a partial step off the
    ledger's own keys and match none of them.
    """
    step = bucket_step(width)

    starts: list[datetime] = []
    moment = _align(period.start, width)
    while moment < period.end:
        starts.append(moment)
        moment += step
    return tuple(starts)


# ---------------------------------------------------------------------------
# The port
# ---------------------------------------------------------------------------


@runtime_checkable
class SpendLedger(Protocol):
    """The contract every spend consumer speaks.

    All three range-scoped methods return the one `SpendResult` shape, so a caller writes one
    branch for the no-figure case rather than three. A consumer that renders only `figure` has
    dropped an annotation rather than simplified a call.

    Failures raise; the **reading** path catches and reports unavailable, counting the failure.
    No request has ever depended on a spend read, so a ledger outage degrades a *figure* and
    never a request. Returning a bare `None` on error is prohibited - that is how an outage
    becomes indistinguishable from "nothing was spent".
    """

    @property
    def ledger_name(self) -> str: ...

    async def consumed_for_holder(
        self, holder: BudgetHolder, period: TimeRange, access: AccessScope | None = None
    ) -> SpendResult:
        """What a holder has consumed over a period.

        Equal to a direct `SUM(spend)` over that holder's attributed rows, and recomputing later
        from the same rows reproduces it.

        **This one read serves two questions, and only one of them narrows.** A *report* asks "what
        should I show this caller", and a figure computed without the caller's boundary foots a row
        set `SecureQueryBuilder` already narrowed with the whole platform's spend - the disclosure
        `access` closes. A *budget* read asks "what has this holder consumed", and its answer is a
        property of the holder rather than of whoever is looking: narrowing it would let a person's
        spend escape their own limit whenever the reader could not see the project it landed in.

        **So `access` defaults to `None` - no caller, narrow nothing - and that default is the
        budget answer.** A report call site has to name its scope, and it takes it from
        `AnalyticsQueryPipeline.access_scope` rather than deriving one. `None` is spelled
        differently from `AccessScope.everything()`, which is a *caller* entitled to everything;
        both narrow nothing, and the two spellings are what keeps a read that merely forgot its
        boundary distinguishable from one that has none.
        """
        ...

    async def consumed_for_holders(
        self,
        holders: Sequence[BudgetHolder],
        period: TimeRange,
        access: AccessScope | None = None,
        *,
        report_filter: ReportFilter | None = None,
    ) -> Mapping[BudgetHolder, SpendResult]:
        """The same question as `consumed_for_holder`, asked about many holders at once.

        **On the port rather than left to a loop at the call site, because the loop is what makes
        a list screen slow enough to be worked around - and a worked-around spend read is a spend
        read outside the port.** A project list showing forty projects across four categories asks
        one hundred and sixty of these questions to paint one screen; answered one round trip at a
        time, the surface becomes slow enough that somebody reintroduces the join this port
        replaced.

        **Every figure here is what `consumed_for_holder` would return for the same holder and the
        same period.** The batch changes how many round trips are made and nothing else: an
        adapter that re-expressed a holder's predicate to make the grouping cheaper would be free
        to disagree with the single-holder answer for the same holder, and two figures for one
        holder is the defect this whole port exists to remove.

        **Every holder asked about appears in the result**, carrying `no_figure` where no figure
        exists. An absent key is not a way of saying "nothing" - a caller reading a mapping would
        render that as a blank rather than as the unavailable state.

        `access` is the caller's permission boundary and means here exactly what it means on
        `consumed_for_holder`, including its default: one boundary for the whole batch, because the
        boundary is a property of the caller and not of a holder. **A batch narrowed for some holders
        and not others would be an equality the batch contract does not permit** - every figure here
        must be what the single-holder read returns for the same holder, the same period *and the
        same boundary*.

        `report_filter` is what a dashboard asked about - its users and projects - and narrows every
        holder's figure exactly as it narrows `aggregate_by_spend_group`. **It exists for the two report
        tables only**, users-spending and projects-spending, whose rows Elasticsearch already narrowed by
        that filter: a figure carrying the boundary and not the filter showed a person filtered to one
        project their spend across every project. **It defaults to `None` - the whole window - and that
        default is the budget answer, for the reason `access` defaults to `None`, only more so.** A
        boundary is at least a property of whoever is reading; a dashboard filter is merely what one
        screen happened to ask, so a budget read narrowed by one would report a limit's consumption as
        whatever share of it the filter selected. The single-holder read takes no filter because no
        report asks about one holder; the batch equality above is therefore stated for an unfiltered
        batch, and a filtered one is the same predicate with the filter's conjunct added.
        """
        ...

    async def aggregate(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        """A report's total for a period, optionally within one dimension's scope.

        A dimension-scoped figure is a **union summed once**: rows the ledger can bucket itself,
        plus rows it cannot that the metric store resolved for the same scope. `resolved_ids` is
        that second set. The caller fetches it, because only the caller knows the scope in the
        metric store's vocabulary; the ledger turns it into money, because **money never comes
        from the metric store** - the branch a row arrives by decides only how its bucket is
        identified.

        Exactly one dimension has a ledger column. For the other three the ledger can bucket
        nothing, so the id set is the whole scope, and an empty one yields a **measured zero**
        whose missing money is named in `partial` rather than left unaccounted.
        """
        ...

    async def aggregate_by_time_bucket(
        self,
        period: TimeRange,
        width: str,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> Mapping[tuple[datetime, TrafficClass], SpendResult]:
        """Money per (time bucket, traffic class) - the read a split spend chart makes.

        **Every bucket in the range appears for every class, and a bucket holding no rows carries a
        measured zero.** An adapter returns only the buckets it found something in; filling the gaps
        is part of this contract, because a chart missing a week draws a gap where it must draw a
        zero and a gap reads as *no data* rather than as *no spend*. The starts come from walking the
        range at `width` (`bucket_starts`), never from the rows that came back.

        **The floors are applied once for the whole range, exactly as `aggregate` applies them.** A
        range no figure exists for yields `no_figure` for every bucket; otherwise the range's own
        unavailable flag rides on every result. Applying a floor per bucket instead would relabel a
        whole-range absence as a per-week one and turn one honest statement into fifty-two.

        `resolved_ids` carries the metric store's answer **per class**, for the one class
        `UNION_CLASSES` names, fetched once for the whole range. The caller owns the fetch because
        only it knows a class's scope in the metric store's vocabulary; the ledger owns turning ids
        into money, because money never comes from the metric store. **The residue is the rows no
        class claims** (`unclassified_predicate`), read once for the range and carried by every class
        key under `SpendDimension.TRAFFIC_CLASS` - never the assistant class's unresolved rows, which
        would name the other three series as money missing from it.

        `access` is the caller's boundary, applied to the scan and the residue alike, exactly as
        `aggregate_by_dimension` applies it; `report_filter` is what the caller asked about - the
        users and projects of a dashboard filter - and narrows the same two reads. Neither is the
        other: a boundary with no project matches nothing, a filter with no term matches everything.
        """
        ...

    async def aggregate_by_traffic_class(
        self,
        period: TimeRange,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> Mapping[TrafficClass, SpendResult]:
        """One figure per traffic class over the whole range - the read a split summary makes.

        **On the port rather than left to a caller summing `aggregate_by_time_bucket`'s buckets.**
        That sum is a second combining rule for one class's money, written at the call site, and a
        window total would then be free to disagree with the chart printed beside it - the
        divergence this port exists to remove. The two reads share one filter list, so they are the
        same question asked at two grains.

        **Every class in `TrafficClass` appears**, and one the table holds nothing for carries a
        **measured zero**. A class silently absent from the mapping reads downstream as a missing
        series rather than an empty one, which is the same fabricated-nothing an absent holder key
        would be.

        **The floors are applied once for the whole range, never per class.** A range no figure
        exists for yields `no_figure` for every class; a range crossing `F` yields the admitted
        portion's figure *plus* the range's own unavailable flag on every class, because that flag
        is the only thing that stops a zero reading as measured.

        `resolved_ids` carries the metric store's answer **per class**, for the classes
        `UNION_CLASSES` names. **Every class carries the one residue of the split** under
        `TRAFFIC_CLASS`: the rows no class claims, read once for the range against the union class's
        id set. It is a portion of the split rather than of any one class - those rows belong to no
        series - so a summary can render a partial label rather than silently showing a short figure.
        """
        ...

    async def aggregate_by_spend_group(
        self,
        period: TimeRange,
        *,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> Mapping[SpendGroup, SpendResult]:
        """One figure per coarse summary group over the whole range - the read the summary cards make.

        **Every group the adapter answers is floored once for the range and read in one scan**, so
        the total and the cards cut from it foot against each other by construction; `spend_group`
        says why the four are not a partition and why the platform figure is never the total less
        the CLI figure computed by a caller. No residue and no project label: the groups are native
        columns, as CLI and workflow are for the class split, and the read is not project-keyed.

        **An adapter answers a group only over the traffic its call classes can carry.** A group its
        classes cannot carry at all is *omitted* from the mapping - the facade combines what the other
        adapters said - never answered absent, which would flag a whole card as incomplete over
        traffic that was never this adapter's to report. A group the adapter's classes do carry but
        it cannot read is absent, with the flag naming the range.

        `access` and `report_filter` mean what they mean on `aggregate_by_time_bucket`.
        """
        ...

    async def aggregate_by_spend_group_per_filter(
        self,
        period: TimeRange,
        *,
        access: AccessScope | None = None,
        common_filter: ReportFilter | None,
        report_filters: Collection[ReportFilter],
    ) -> Mapping[ReportFilter, Mapping[SpendGroup, SpendResult]]:
        """`aggregate_by_spend_group` for each of several filters in one read - the read a per-row table makes.

        **Each answer is the figure `aggregate_by_spend_group` gives for that filter**, group for group:
        an adapter may batch the scan, never change what a row is told. A table whose rows were read one
        at a time and one read in a batch would otherwise disagree about the same money.

        **Every requested filter is answered.** A key missing from the mapping is a defect, not a row
        with nothing to show: it would read downstream as a blank rather than as the unavailable state,
        which is the fabricated-nothing every other read here refuses. A filter the adapter cannot read
        is answered `no_figure` per group.

        `common_filter` is the term every filter in `report_filters` already carries - the dashboard's
        own filter. It is given so an adapter that reads one shared scan can put it in the scan's
        `WHERE`; an adapter that answers filter by filter may ignore it, since each filter carries it.
        `access` means what it means on `aggregate_by_time_bucket`.
        """
        ...

    async def aggregate_by_dimension(
        self,
        period: TimeRange,
        dimension: SpendDimension,
        values: Sequence[str],
        ids_by_value: Mapping[str, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> Mapping[str, SpendResult]:
        """One figure per dimension value, for a **named set** of values - the read a table makes.

        **On the port rather than left to a loop over `aggregate`.** A scope answers one key per
        call, so a thousand-row assistant table is a thousand round trips; a surface that slow
        acquires a cache, and a cache is a second source of spend truth. The values are named by the
        caller rather than discovered here because the caller has already chosen its rows - a page, a
        `LIMIT` - and a dimension with no ledger column cannot be grouped over unboundedly at all.

        **Every requested value appears in the result, and one the table holds nothing for carries a
        measured zero.** A value silently absent from the mapping makes a caller's table row vanish
        rather than read zero, which is the same fabricated-nothing an absent holder key would be.

        **The floors are applied once for the whole range, never per value.** A range no figure
        exists for yields `no_figure` for every value; a range crossing `F` yields the admitted
        portion's figure *plus* the range's own unavailable flag on every value, because that flag is
        the only thing that stops a zero reading as measured. A value is not a time interval, so a
        per-value flag would name an absence no floor describes.

        `ids_by_value` carries the metric store's answer **per value**, fetched once for the whole
        range. The caller owns the fetch because only it knows a value's scope in the metric store's
        vocabulary; the ledger owns turning ids into money, because money never comes from the metric
        store. A value **absent** from the mapping is asked about with **no ids** - never with no
        filter, which would select every row for it.

        **The residue is read once for the whole grouped read and attached to every value's
        `partial`.** It is a property of the range and the dimension rather than of one value: the
        rows the union could not bucket are missing from *every* value's figure, and any of them
        might belong to any of them, so no value may claim it alone.

        **An empty `values` asks nothing**, issues no query, and returns an empty mapping.

        `access` is the **caller's permission boundary**, and it narrows the figure rather than the
        report. Every consumer of this read feeds a table Elasticsearch already narrowed at
        construction, and a figure computed without the boundary foots that narrowed row set with the
        whole platform's spend - the disclosure this argument exists to close. `None` means the read
        has no caller at all (a scheduled job), which is a different absence from
        `AccessScope.everything()`; both narrow nothing, and they are spelled differently so a read
        that merely forgot its scope is findable.

        `report_filter` is what the caller asked about - a dashboard's users and projects - and narrows
        every value's figure and the residue, as it does on `aggregate_by_time_bucket`. A value's ids
        and a column-keyed value select its rows in every project and for every person, so a filtered
        table read without it foots its rows with the whole window's spend on the same value.
        """
        ...

    async def aggregate_composed(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        traffic_class_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        """One total for several filter terms at once - *"this person, in this project, on this workflow"*.

        **On the port because a caller cannot compose it.** `aggregate` answers one `DimensionScope`
        and `consumed_for_holder` one holder; a conjunction is neither, and a consumer assembling one
        would be writing an attribution predicate outside `spend_logs_repository` - the prohibition
        that is the whole reason one figure means one thing.

        **One scan over the conjunction, never two scoped figures added.** Two terms' row sets
        overlap by construction, so adding a project total to a workflow total counts every row both
        claim twice: *"sum over the resolved row set - never aggregate per bucket and add."*

        **A scope carrying exactly one term answers exactly what that term's own read answers**, and
        that equality is what makes this an extension rather than a second source of truth. The user
        term *is* the person-total predicate - both pots, deliberately, because a person's spend
        includes the calls they made with a project in context. The dimension term *is* `aggregate`'s
        union. The class term *is* the closed table's predicate. None of the three is re-derived.

        **The floors are applied once for the whole range**, exactly as `aggregate` applies them, and
        `partial` carries **every** term's qualification rather than the first one found: a project
        term below the identity floor, an unresolvable alias set, and a dimension the union could not
        fully bucket are three different absences, and a figure can be short in all three at once.

        **Two id sets, because they answer two different questions.** `dimension_ids` is what the
        metric store resolved for *this* dimension scope; `traffic_class_ids` is what it resolved for
        the class *as a whole*. One argument serving both would silently narrow one of them, and
        narrowing a selector is how two surfaces come to answer differently for one window. Both are
        the caller's to fetch and the ledger's to turn into money, because money never comes from the
        metric store.
        """
        ...

    async def aggregate_composed_by_traffic_class(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        ids_by_class: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> Mapping[TrafficClass, SpendResult]:
        """`aggregate_composed` split per traffic class - the read a per-person leaderboard makes.

        **On the port rather than composed from the two reads that exist**, because neither answers
        it: `aggregate_by_traffic_class` is platform-wide - its only scoping argument is the period -
        and `consumed_for_holders` takes holders but no class. `PersonalHolder(user, CLI)` is the
        trap that looks like the answer: it is what the person's own customer was charged, so it
        silently drops every CLI call the person made against a project budget.

        **Every class in `TrafficClass` appears**, and one the scope holds nothing for carries a
        **measured zero** - a class silently absent reads downstream as a missing series rather than
        an empty one.

        **The floors are applied once for the whole range, never per class**, exactly as
        `aggregate_by_traffic_class` applies them: a sub-`F` range yields zeros *plus* the range's
        own unavailable flag, because that flag is the only thing that stops a zero reading as
        measured.

        **The scope's own qualifications ride on every class**, since they qualify the row set every
        class is drawn from, and the class split's one residue - the rows no class claims, under
        `TRAFFIC_CLASS` - rides beside them on every class, exactly as `aggregate_by_traffic_class`
        carries it.

        `ids_by_class` carries the metric store's answer **per class**, for the classes
        `UNION_CLASSES` names, fetched once for the whole range - the same shape the platform-wide
        class split takes; the residue is read against the union class's set.
        """
        ...

    async def cost_for_request(self, *, run_id: str | None = None, request_id: str | None = None) -> RequestCost:
        """What one unit of work cost, keyed on a correlation id and **no time bound**.

        Exactly one of the two ids is given, and which one is the *caller's* choice because only
        the caller knows the grain of the record being hydrated: one LLM call resolves by
        `run_id`, an outer request by `request_id`. Hydrating a single call by the request id
        writes the whole outer request's cost into every pending call in it, and a ten-call agent
        loop then reports roughly ten times the true cost with nothing failing.

        **Inventing a time bound here is a defect, not a safeguard.** The bound exists elsewhere
        to stop an unbounded scan, and an id is already a bounded selector. The obvious
        invention - the request's own start day - silently drops a retry attempt that landed on
        the other side of a period boundary, so the windowed lookup under-reports a cost the
        id-only lookup returns correctly.
        """
        ...

    async def costs_for_requests(self, request_ids: Sequence[str]) -> dict[str, RequestCost]:
        """`cost_for_request(request_id=...)` for every id of a page, in one read.

        Each answer is the one `cost_for_request` gives for that id. An id left out of the mapping is
        read by the caller as this ledger having no figure for it.
        """
        ...

    async def request_count(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> int:
        """How many requests a scope saw, failures included.

        Counted as **distinct calls, not rows**: a proxy-side retry writes several rows under one
        call and every user-visible notion of a request counts it once. Spend still sums every
        row - counting and summing treat rows differently on purpose, and that asymmetry belongs
        in code rather than being rediscovered. A row carrying no correlation id counts as one:
        absent the id there is no evidence of duplication.

        Failed requests are requests. They contribute zero spend, and a failure count is a
        subset of this number rather than a sibling to be added to it.
        """
        ...
