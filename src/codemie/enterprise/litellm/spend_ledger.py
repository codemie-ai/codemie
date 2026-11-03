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

"""The LiteLLM-backed ledger: `litellm_spend_logs` is the sole authority for LiteLLM spend.

Not one input among several, not a figure to reconcile against a second ledger. Cost is **read
from the recorded row**, never re-derived from token counts and never re-priced - a sampled row
bills 149,444 of its 149,854 prompt tokens from cache, so re-pricing overstates it by roughly
100x. The reported cost is exactly the recorded `spend`: nothing derived, rounded or reconciled.

**This is where the two floors are applied, in the fixed order, once.** `F` decides whether a
figure exists over the whole requested range; `E` then labels only the portion `F` admitted. No
consumer splits a range, and no consumer names a source.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs import config, logger
from codemie.repository.spend_logs_repository import SpendLogsRepository, spend_logs_repository
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import SPEND_LEDGER_CONNECTION_WAIT_METRIC
from codemie.service.spend.coverage import (
    LedgerCoverage,
    Portion,
    SpendDimension,
    SpendResult,
    TimeRange,
)
from codemie.service.spend.identity_bridge import identity_bridge_portion
from codemie.service.spend.ledger import (
    AccessScope,
    BudgetHolder,
    ComposedScope,
    DimensionScope,
    PersonalHolder,
    PersonTotalHolder,
    ProjectGroupHolder,
    ProjectHolder,
    ProjectMemberHolder,
    ReportFilter,
    RequestCost,
    bucket_starts,
)
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.traffic_class import TrafficClass

LEDGER_NAME = "litellm_spend_logs"

#: Holders whose figure depends on the project dimension, and therefore carry the label below the
#: identity floor. **Deliberately excludes the personal, global and person-total holders**:
#: degrading those on `E`'s account would be its own defect, because above `F` they are complete
#: below `E` as well - the fallback parser resolves the principal, the bridge maps it to a user,
#: and the category is assigned. Their money is exact.
_PROJECT_KEYED_HOLDERS = (ProjectHolder, ProjectMemberHolder, ProjectGroupHolder)

#: Holders that can carry an `identity_bridge` entry. `E`-freedom is not label-freedom: an
#: unresolvable alias set reaches personal, global and per-user figures, which `E` never touches.
_BRIDGE_DEPENDENT_HOLDERS = (PersonalHolder, PersonTotalHolder)


def _report_filter_label(report_filter: ReportFilter | None) -> dict[SpendDimension, Portion]:
    """The hole a **filter** on an unbridged person leaves - the one no SQL can close.

    `report_filter_predicate` renders a term whose alias set is `None` as its explicit half alone,
    and that is the only honest rendering: the bridge could not find out which principal-form rows
    belong to that person, so there is no fallback conjunct to write. Those rows are keyed on a
    username or an e-mail nobody can map back, which means a figure filtered to that person is short
    by an amount nobody can *compute* - the one state `identity_bridge_portion` exists to name.

    The composed read already labels exactly this for its own user term; this is the same label for
    the grouped reads, which carry the filter without carrying a `ComposedScope`.

    Personal-project owners nobody could look up (`None`) leave the same kind of hole: the projects are
    matched by names their rows do not carry, so the figure may be short by their whole pots, by an
    amount nobody can compute either.
    """
    if report_filter is None:
        return {}
    if report_filter.personal_project_owners is None or any(term.aliases is None for term in report_filter.users):
        return {SpendDimension.IDENTITY_BRIDGE: identity_bridge_portion()}
    return {}


def _money_portion(spend: Decimal, rows: int, *, sub_range: TimeRange | None = None) -> Portion | None:
    """A residue as a portion of a money figure, or None where it takes no money away from it.

    **A residue that carries no money is not a hole in a money figure.** The rows exist - a failed
    call writes a row with no model and a spend of zero, an unattributed row may cost nothing - but
    the figure beside them is short by nothing, and a surface that renders *partial, missing $0.00*
    has told its reader to distrust a whole number. The row count stays a diagnostic of the
    repository read, never the thing a money label is raised on.
    """
    if not rows or spend == 0:
        return None
    return Portion(spend=spend, rows=rows, sub_range=sub_range)


class SpendLogsLedger:
    """Reads the ledger table and annotates what it returns.

    Every method is given the coverage values that were read **once** at the composition root.
    They are never re-read here and never derived from a query - in particular `F` is never a live
    `MIN(start_time)`, which over a table still being filled reports coverage the table does not
    have.
    """

    ledger_name: str = LEDGER_NAME

    def __init__(
        self,
        coverage: LedgerCoverage,
        *,
        session_factory,
        repository: SpendLogsRepository = spend_logs_repository,
    ) -> None:
        self._coverage = coverage
        self._session_factory = session_factory
        self._repository = repository

    async def consumed_for_holder(
        self, holder: BudgetHolder, period: TimeRange, access: AccessScope | None = None
    ) -> SpendResult:
        """A holder's consumed amount, with both annotations applied.

        **Budget consumption is not an exception to the split.** A consumption figure built on a
        silently truncated total understates spend, and everything downstream inherits it: the
        holder's own view of what remains, and the limits an operator sets from it.

        **`access` narrows the figure and its label alike, and `None` - the default - narrows
        neither.** The two questions this read serves want opposite answers: a *report* must be
        narrowed to what its caller may see, because the row set beside the figure already is; a
        *budget* read must not, because consumption is a property of the holder and a narrowed one
        would let spend escape its own limit whenever the reader could not see where it landed. The
        boundary is never re-spelled here - the clause shape lives in `AccessScope` and in the
        repository's `access_predicate`.
        """
        admitted = self._coverage.admitted(period)
        if admitted is None:
            return SpendResult.no_figure(period)

        async with self._session_factory() as session:
            figure = await self._repository.sum_for_holder(session, holder, admitted, access)
            partial = await self._label(session, holder, admitted, access=access)

        return SpendResult(
            figure=figure,
            unavailable=self._coverage.unavailable_flag(period),
            partial=partial,
        )

    async def consumed_for_holders(
        self,
        holders: Sequence[BudgetHolder],
        period: TimeRange,
        access: AccessScope | None = None,
        *,
        report_filter: ReportFilter | None = None,
    ) -> dict[BudgetHolder, SpendResult]:
        """Many holders' consumed amounts, annotated exactly as one holder's would be.

        **Two things are read once here that a caller's loop would read once per holder**, and the
        second is the reason this belongs in the ledger rather than above it. The figures come from
        one filtered-aggregate scan. The project-dimension portion is a property of the *period*,
        not of a holder - every project-keyed holder over the same period carries the same portion -
        so it is one residue query for the whole batch instead of one per row of a list screen.

        **`access` reaches both of those reads, and the residue is the one it is easiest to forget.**
        A portion is money *and* a row count published beside a figure, so an unnarrowed one tells a
        restricted caller the magnitude of the spend they are forbidden to see - and over-labels their
        own figure with a hole none of their rows is in. Narrowed, the portion is empty for a
        restricted caller by construction: it counts rows carrying no project at all, and every clause
        of the boundary pins one. `None` - the default - narrows neither, which is the budget answer
        `consumed_for_holder` explains.

        **`report_filter` narrows the figures and labels them, exactly as it does the summary cards**:
        one conjunct in the scan's `WHERE`, and the bridge hole `_report_filter_label` names when a
        filtered person could not be resolved. Its `None` default is the budget answer the port
        explains. **The project portion is left unfiltered on purpose.** It names rows carrying no
        project at all, so a projects filter would empty it while the hole it names is still in the
        filtered project's figure; reported whole, it can only overstate a hole, never hide one.
        """
        admitted = self._coverage.admitted(period)
        if admitted is None:
            return {holder: SpendResult.no_figure(period) for holder in holders}

        async with self._session_factory() as session:
            totals = await self._repository.sum_for_holders(session, holders, admitted, access, report_filter)
            project_portion = (
                await self._project_portion(session, admitted, access=access)
                if any(isinstance(holder, _PROJECT_KEYED_HOLDERS) for holder in holders)
                else None
            )

        unavailable = self._coverage.unavailable_flag(period)
        filter_label = _report_filter_label(report_filter)
        return {
            holder: SpendResult(
                figure=totals[holder],
                unavailable=unavailable,
                partial={**self._batch_label(holder, project_portion), **filter_label},
            )
            for holder in holders
        }

    async def aggregate(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        """A report's total for a period, optionally within one dimension's scope.

        `resolved_ids` is the correlation-id set the **metric store** returned for the same scope,
        over a window widened by the emission-lag allowance. The caller owns fetching it, because
        only the caller knows the scope in the metric store's own vocabulary; this method owns
        turning it into money, because money never comes from the metric store.

        An empty id set is not an error and not a default to be filled in later: for a dimension
        that *has* a ledger column it simply means the pre-column rows contributed nothing the
        metric store could bucket, and for a dimension that has none it means the whole scope
        resolved to no rows - a measured zero whose missing money is then named in the label.
        """
        admitted = self._coverage.admitted(period)
        if admitted is None:
            return SpendResult.no_figure(period)

        async with self._session_factory() as session:
            if scope is None:
                figure = await self._repository.sum_for_period(session, admitted)
                partial: dict[SpendDimension, Portion] = {}
            else:
                figure = await self._repository.sum_for_dimension_scope(session, admitted, scope, resolved_ids)
                partial = await self._dimension_label(session, admitted, scope, resolved_ids)

        return SpendResult(
            figure=figure,
            unavailable=self._coverage.unavailable_flag(period),
            partial=partial,
        )

    async def aggregate_by_time_bucket(
        self,
        period: TimeRange,
        width: str,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[tuple[datetime, TrafficClass], SpendResult]:
        """Money per (time bucket, traffic class), with every bucket in the range present.

        **The repository returns only the buckets it found rows in; the gaps are filled here with a
        measured zero.** Below the floor there is no figure and the whole mapping says so, but above
        it an empty bucket means the table holds no spend for that week - a fact, not an absence -
        and a chart handed a gap instead draws a break in a line that should touch the axis.

        **The floors are applied once for the whole range**, exactly as `aggregate` applies them, so
        a bucket lying below `F` carries a zero *and* the range's unavailable flag rather than a bare
        zero. Splitting the floor per bucket would restate one whole-range absence as fifty-two
        per-week ones, and the two do not mean the same thing.

        **One residue for the whole chart, and it names the rows no class claims.** It is read once
        for the range - a property of the range and of the assistant class's id set, exactly as the
        project portion is a property of the period in `consumed_for_holders` - and the *same* mapping
        rides on every key, because rows belonging to no series are not more missing from one series
        than from another. Two queries answer a whole chart; two per bucket would be a hundred for a
        year of weeks.

        `access` and `report_filter` narrow the scan and the residue alike: the boundary because the
        chart sits beside a table `SecureQueryBuilder` already narrowed, the filter because a figure
        carrying only the boundary would silently un-filter a filtered dashboard. A filter naming a
        person the bridge could not resolve additionally *labels* every key - see
        `_report_filter_label`, which names the half of that person's rows the scan cannot reach.
        """
        buckets = bucket_starts(period, width)
        admitted = self._coverage.admitted(period)
        if admitted is None:
            return {(bucket, cls): SpendResult.no_figure(period) for bucket in buckets for cls in TrafficClass}

        ids = resolved_ids or {}
        async with self._session_factory() as session:
            totals = await self._repository.sum_by_time_bucket(session, admitted, width, ids, access, report_filter)
            residue = await self._class_split_label(
                session, admitted, ids.get(TrafficClass.ASSISTANT, ()), access, report_filter
            )

        # A key the walk did not produce is money this mapping cannot place, and the walk's own
        # alignment is the only thing that can put one there - most plausibly a width whose stride the
        # walk and the grouping read from different tables, which they no longer do. Said out loud,
        # because dropping it is otherwise silent and reads downstream as a bucket that held nothing.
        unplaceable = {bucket for bucket, _ in totals if bucket not in set(buckets)}
        if unplaceable:
            logger.warning(
                f"spend_logs_ledger: {len(unplaceable)} {width} bucket(s) the ledger grouped do not align with the "
                f"walked range and their money is not reported: {sorted(unplaceable)}"
            )

        unavailable = self._coverage.unavailable_flag(period)
        # Composed once for the whole chart, because both halves are properties of the range: one
        # residue naming the rows no class claims, and the bridge hole the filter itself leaves.
        partial = {**residue, **_report_filter_label(report_filter)}
        return {
            (bucket, cls): SpendResult(
                figure=totals.get((bucket, cls), Decimal("0")),
                unavailable=unavailable,
                # One residue means one `Portion` **value** on every key, and the combiner counts a
                # hole once by value however many buckets a chart happens to have. The object being
                # reused is a consequence of reading once, not something identity is relied on for.
                partial=partial,
            )
            for bucket in buckets
            for cls in TrafficClass
        }

    async def aggregate_by_traffic_class(
        self,
        period: TimeRange,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> dict[TrafficClass, SpendResult]:
        """One figure per traffic class over the whole range, with every class present.

        **The bucketed read without the buckets, and deliberately not built by summing it.** Adding
        that method's buckets up here would be a second combining rule for one class's money, and
        the two surfaces would then be free to disagree about one window. The repository answers
        both from the same filter list instead, so they cannot.

        **The floors are applied once for the whole range**, exactly as `aggregate` applies them: a
        class over a sub-`F` range carries a zero *and* the range's unavailable flag rather than a
        bare zero, and a range below the floor entirely is withheld from every class at once.

        **One residue for the whole split**, read once for the range and carried by every class: it
        names the rows no class claims, which belong to no series and are therefore not missing from
        one series more than from another. Two queries answer the whole split.
        """
        admitted = self._coverage.admitted(period)
        if admitted is None:
            return {cls: SpendResult.no_figure(period) for cls in TrafficClass}

        ids = resolved_ids or {}
        async with self._session_factory() as session:
            totals = await self._repository.sum_for_traffic_classes(session, admitted, ids)
            # No boundary and no filter: this whole-window class read has no caller to be narrowed
            # by today, and its scan carries none either - narrowing one and not the other is what
            # would label a figure with a hole none of its rows is in.
            residue = await self._class_split_label(session, admitted, ids.get(TrafficClass.ASSISTANT, ()))

        unavailable = self._coverage.unavailable_flag(period)
        return {
            # Indexed rather than `.get`-with-a-zero, like `consumed_for_holders`: this read is one
            # filtered aggregate per class over one row, so unlike a bucket the walk had to fill,
            # a missing class is a repository defect. Substituting a zero for it would fabricate
            # the one figure this design never fabricates.
            cls: SpendResult(
                figure=totals[cls],
                unavailable=unavailable,
                partial=residue,
            )
            for cls in TrafficClass
        }

    async def aggregate_by_spend_group(
        self,
        period: TimeRange,
        *,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[SpendGroup, SpendResult]:
        """One figure per coarse summary group over the whole range - the read the summary cards make.

        **Every group is answered, and none is omitted**, because this table holds every LiteLLM-billed
        row: a group whose traffic a deployment does not route through LiteLLM sums to a **measured
        zero** here, which is a fact about this ledger and not an absence. The facade adds the other
        adapter's figure to it. Omission is for an adapter whose call classes cannot carry a group at
        all; answering absent instead would flag a whole card as incomplete over traffic that was never
        this adapter's to report.

        **One scan for the four**, so the total and the cards cut from it foot against each other -
        `spend_group` says why they are not a partition, and why the platform card is never the total
        less the CLI figure computed by a caller.

        **No residue and no project label**, exactly as `aggregate(period, None)` carries none: the four
        selectors are native columns, so there is no unresolved remainder for a label to name, and the
        read is not project-keyed.

        **The one label it can carry comes from the filter itself.** A dashboard filtered to a person
        the bridge could not resolve is answered over that person's canonical id alone, so every card
        is short by their unreachable fallback rows - see `_report_filter_label`.
        """
        admitted = self._coverage.admitted(period)
        if admitted is None:
            return {group: SpendResult.no_figure(period) for group in SpendGroup}

        async with self._session_factory() as session:
            totals = await self._repository.sum_for_spend_groups(session, admitted, access, report_filter)

        unavailable = self._coverage.unavailable_flag(period)
        label = _report_filter_label(report_filter)
        return {
            # Indexed rather than `.get`-with-a-zero, like the class split: one row of aggregates
            # cannot omit a group, so a missing key is a repository defect and a substituted zero
            # would fabricate the one figure this design never fabricates.
            group: SpendResult(figure=totals[group], unavailable=unavailable, partial=label)
            for group in SpendGroup
        }

    async def aggregate_by_dimension(
        self,
        period: TimeRange,
        dimension: SpendDimension,
        values: Sequence[str],
        ids_by_value: dict[str, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[str, SpendResult]:
        """One figure per named dimension value, with every requested value present.

        **The `GROUP BY` a builder reaches for here is wrong for three of the five dimensions.**
        Assistant, conversation and datasource have no ledger column at all, so grouping on one
        answers `workflow` correctly and returns nothing whatsoever for them. The repository asks each
        value the same union question `aggregate` asks about one instead, so a grouped figure equals
        the single-scope figure value for value.

        **The floors are applied once for the whole range**, exactly as `aggregate` applies them: a
        value over a sub-`F` range carries a zero *and* the range's unavailable flag rather than a
        bare zero, and a range below the floor entirely is withheld from every value at once.

        **The residue is read once for the whole read, from the union of every value's ids, and
        attached to every value.** It is a property of the range and the dimension - the same partial
        entry `aggregate` produces for the same dimension - because the rows the union could not
        bucket are missing from every value's figure and any of them might belong to any of them. One
        query answers a whole table; one per value would be a thousand.

        **`access` narrows the figure and the residue alike, and it is passed to both reads rather
        than to the first.** A residue is money and a row count published beside a figure, so leaving
        it unnarrowed would report the magnitude of spend the caller may not see and over-label their
        own figure with a hole none of their rows is in. The boundary is never re-spelled here: the
        clause shape lives in `AccessScope` and in the repository's `access_predicate`.

        `report_filter` - the dashboard's users and projects - narrows both reads the same way and for
        the time-bucket chart's reason: a value's ids and a workflow's or a model's own column select
        its rows everywhere, so a filtered table's figures and its residue would otherwise be the whole
        window's. A filter naming a person the bridge could not resolve also labels every value - see
        `_report_filter_label`. A projects filter reaches no row that carries no project - a parsed row
        below the identity floor - and nothing labels that gap, exactly as on the summary cards and the
        time-bucket chart, which this read now agrees with.
        """
        # An empty request asks nothing, so nothing is read - not the floors, not the residue. Checked
        # before the floor so a caller passing no values gets an empty mapping rather than a mapping
        # of nothing to the unavailable state.
        requested = list(dict.fromkeys(values))
        if not requested:
            return {}

        admitted = self._coverage.admitted(period)
        if admitted is None:
            return {value: SpendResult.no_figure(period) for value in requested}

        supplied = ids_by_value or {}
        # A value the metric store returned nothing for is asked about with **no ids**, never with no
        # filter: the second would select every row in the range for it. Normalised here, once, so
        # the repository's key set is exactly the requested set.
        per_value = {value: supplied.get(value, ()) for value in requested}

        async with self._session_factory() as session:
            totals = await self._repository.sum_for_dimension_values(
                session, admitted, dimension, per_value, access, report_filter
            )
            # Deduped on insertion order: two values' id sets can overlap, and a repeated id would
            # only bloat the `NOT IN` list the residue is built from.
            union = tuple(dict.fromkeys(one_id for ids in per_value.values() for one_id in ids))
            residue = await self._dimension_label(
                session, admitted, DimensionScope(dimension, None), union, access=access, report_filter=report_filter
            )

        unavailable = self._coverage.unavailable_flag(period)
        partial = {**residue, **_report_filter_label(report_filter)}
        return {
            # Indexed rather than `.get`-with-a-zero, like `consumed_for_holders` and the class split:
            # the repository answers for exactly the keys it was given, so a missing value is a
            # repository defect, and substituting a zero for it would fabricate the one figure this
            # design never fabricates.
            value: SpendResult(figure=totals[value], unavailable=unavailable, partial=partial)
            for value in requested
        }

    async def aggregate_composed(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        traffic_class_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        """A total for several filter terms at once, annotated in every dimension it depends on.

        **The floors are applied once for the whole range**, exactly as `aggregate` applies them, so a
        scope over a sub-`F` range carries a zero *and* the range's unavailable flag rather than a bare
        zero, and a range below the floor entirely is withheld outright.

        **The label is assembled per term, and no term's qualification is dropped because another
        fired.** A composed figure can be short in three ways at once - a project term below the
        identity floor, an alias set the bridge could not resolve, a dimension the union could not
        fully bucket - and they are three different absences over three different row sets. This is
        the shape `SpendResult.partial` is a *set* for.

        `dimension_ids` is the dimension term's own resolved id set. `traffic_class_ids` is the
        **union class's** - the assistant's - and it is handed down for *any* class term: the
        assistant selector consumes it when the class is `ASSISTANT`, and the class split's residue
        consumes it for every class, because a row the metric store resolved is not an unclassified
        one whichever class the scope names.
        """
        admitted = self._coverage.admitted(period)
        if admitted is None:
            return SpendResult.no_figure(period)

        async with self._session_factory() as session:
            figure = await self._repository.sum_for_composed_scope(
                session,
                admitted,
                scope,
                dimension_ids=dimension_ids,
                traffic_class_ids=traffic_class_ids,
            )
            partial = await self._composed_label(
                session,
                admitted,
                scope,
                dimension_ids=dimension_ids,
                traffic_class_ids=traffic_class_ids,
            )

        return SpendResult(
            figure=figure,
            unavailable=self._coverage.unavailable_flag(period),
            partial=partial,
        )

    async def aggregate_composed_by_traffic_class(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        ids_by_class: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> dict[TrafficClass, SpendResult]:
        """A composed scope split per traffic class, with every class present.

        **The scope's own label rides on every class, and so does the split's one residue.** The first
        qualifies the row set every class is drawn from - the same project hole and the same
        unresolvable alias set apply whichever class a row landed in - and the second names the rows
        *no* class claims, which is why it is not per class either: rows belonging to no series are
        not more missing from one series than from another. Merging the two into one entry would let
        a residue claim the scope was complete, or a scope label claim the split was.

        **The residue is read once for the whole split**, against the assistant class's id set,
        exactly as `aggregate_by_traffic_class` reads it - and, like the dimension residue
        `_composed_label` reads, it is **not narrowed by the scope's other terms**. It names the
        platform's unclassified rows within the admitted range, so it is the same magnitude on every
        row of a leaderboard rather than that row's own hole. A narrowed residue is a different read:
        it would need the composed predicate beside it in the residue's own `WHERE`.

        **The floors are applied once for the whole range, never per class**, exactly as
        `aggregate_by_traffic_class` applies them.
        """
        admitted = self._coverage.admitted(period)
        if admitted is None:
            return {cls: SpendResult.no_figure(period) for cls in TrafficClass}

        ids = ids_by_class or {}
        async with self._session_factory() as session:
            totals = await self._repository.sum_for_composed_scope_by_traffic_class(
                session, admitted, scope, dimension_ids=dimension_ids, ids_by_class=ids
            )
            # No class term reaches here - the repository refuses one - so the scope label carries no
            # class residue and the split's own residue below is the only source of one.
            scope_partial = await self._composed_label(session, admitted, scope, dimension_ids=dimension_ids)
            residue = await self._class_split_label(session, admitted, ids.get(TrafficClass.ASSISTANT, ()))

        unavailable = self._coverage.unavailable_flag(period)
        return {
            # Indexed rather than `.get`-with-a-zero, like the platform-wide class split: one row of
            # filtered aggregates cannot omit a class, so a missing key is a repository defect and
            # substituting a zero would fabricate the one figure this design never fabricates.
            cls: SpendResult(
                figure=totals[cls],
                unavailable=unavailable,
                partial={**scope_partial, **residue},
            )
            for cls in TrafficClass
        }

    async def cost_for_request(self, *, run_id: str | None = None, request_id: str | None = None) -> RequestCost:
        """What one unit of work cost, keyed on an id and carrying no time bound.

        **The state of an empty result is decided by readiness, not by the emptiness.** Before
        activation - or while readiness stands invalidated - the ledger cannot answer at all, so
        the answer is `unavailable` and the caller must stop asking. Once activated, finding no row
        means the row has not landed yet, which is `pending`. Calling the first state `pending`
        would schedule an indefinite retry against a table that will not respond to it.

        Finding **several** rows is normal: a retried call writes one per upstream attempt, and
        each attempt that reached a provider may have cost money. They are summed.
        """
        if not self._coverage.activated:
            return RequestCost.unavailable()

        async with self._session_factory() as session:
            total = await self._repository.sum_for_correlation_id(session, run_id=run_id, request_id=request_id)

        return RequestCost.pending() if total is None else RequestCost.settled(total)

    async def request_count(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> int:
        """Distinct calls in a period, failures included.

        Returns 0 for a period no figure exists for. That is not a claim that nothing happened -
        the accompanying `SpendResult` carries the unavailable state, and a count is not the
        surface a coverage question is answered on.
        """
        admitted = self._coverage.admitted(period)
        if admitted is None:
            return 0

        async with self._session_factory() as session:
            if scope is not None and scope.dimension is SpendDimension.EXECUTION:
                return await self._repository.count_executions_for_scope(session, admitted, scope, resolved_ids)
            return await self._repository.count_requests(session, admitted)

    async def _dimension_label(
        self,
        session: AsyncSession,
        admitted: TimeRange,
        scope: DimensionScope,
        resolved_ids: tuple[str, ...],
        *,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[SpendDimension, Portion]:
        """The residue that makes a dimension-grouped answer partial, computed **with** the figure.

        It ships here rather than in a later pass because "naming the portion" is otherwise not
        executable for a dimension, and a builder handed the label without the predicate invents a
        definition - most plausibly "rows below the rollout instant", which is the dimension
        coverage floor this design refuses.

        **A portion is only added where in-scope rows are actually missing the dimension.** A row
        the metric store resolved is not a hole, and labelling a period that holds none would
        report a wholly pre-rollout period as 100% partial while nothing was missing.

        No `sub_range`: a dimension hole is scattered rows inside a fully covered period, and no
        interval can express it. Only a *published* floor scopes a portion as a time interval.

        `access` is **keyword-only and defaults to no boundary**, because only the caller-facing
        grouped read carries one: the holder and traffic-class reads above it answer budget and
        platform questions that have no caller to be narrowed by. A residue narrowed by a boundary
        names only the caller's *own* unbucketable money, which is what keeps it from publishing the
        size of spend they may not see. `report_filter` narrows it for the figure's reason: a residue
        sized outside the dashboard's filter names a hole none of the filtered rows is in.
        """
        spend, rows = await self._repository.dimension_residue(
            session, admitted, scope, resolved_ids, access, report_filter
        )
        portion = _money_portion(spend, rows)
        return {} if portion is None else {scope.dimension: portion}

    async def _class_split_label(
        self,
        session: AsyncSession,
        admitted: TimeRange,
        resolved_assistant_ids: tuple[str, ...],
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[SpendDimension, Portion]:
        """The residue that makes a **class split** partial - the rows no class claims.

        `_dimension_label`'s shape over a different question. That one names the rows one dimension
        could not bucket and files them under that dimension; this one names the rows *no* traffic
        class claims and files them under `TRAFFIC_CLASS`, which is a portion of the split rather than
        a floor on it. Filing the assistant class's unresolved rows here instead - the reading the
        name most invites - would count every CLI, workflow and embedding row as money missing from
        one series.

        **A period holding no unclassified rows is not labelled**, for the reason `_dimension_label`
        gives: a covered window reported as partial is a warning nobody can act on.
        """
        spend, rows = await self._repository.unclassified_residue(
            session, admitted, resolved_assistant_ids, access, report_filter
        )
        portion = _money_portion(spend, rows)
        return {} if portion is None else {SpendDimension.TRAFFIC_CLASS: portion}

    async def _composed_label(
        self,
        session: AsyncSession,
        admitted: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        traffic_class_ids: tuple[str, ...] = (),
    ) -> dict[SpendDimension, Portion]:
        """Which dimensions a composed figure is incomplete in - one entry per term that can be short.

        **Assembled term by term, and each term's portion is the very one that term's own read
        produces**: the dimension term's is `aggregate`'s residue, the class term's is the class
        split's residue, and the user term's is the holder read's bridge hole. That is what makes a
        one-term composed scope carry the same label as the read it mirrors; a label assembled by any
        other rule would let two figures for one question differ in their annotation while agreeing
        in their money, which reads to a surface as the more complete of the two.

        `traffic_class_ids` is the **union class's** - the assistant's - resolved id set, handed here
        for any class term: the assistant selector consumes it when the class is `ASSISTANT`, and the
        residue consumes it for every class, because a resolved row is not an unclassified one.

        A term that cannot be short contributes nothing: a resolved alias set leaves no bridge hole,
        and a range holding no unclassified or unbucketable rows is not labelled at all.
        """
        partial: dict[SpendDimension, Portion] = {}

        if scope.dimension is not None:
            partial |= await self._dimension_label(session, admitted, scope.dimension, dimension_ids)
        if scope.traffic_class is not None:
            # Every class, native ones included: the rows no class claims are the split's own residue
            # rather than one series' unresolved remainder, so a term naming any series carries it.
            partial |= await self._class_split_label(session, admitted, traffic_class_ids)
        if scope.project is not None:
            # A project term makes this figure project-keyed, so it carries `E`'s label for the same
            # reason the three project holders do: below the identity floor a principal-form row
            # carries no project at all, so it is absent from a project-filtered figure and no
            # identity narrows the hole.
            project_portion = await self._project_portion(session, admitted)
            if project_portion is not None:
                partial[SpendDimension.PROJECT] = project_portion
        if scope.user is not None and scope.user.aliases is None:
            # The one portion that is not computable: an unresolvable alias set means the person's
            # fallback rows cannot be *identified*, so there is no row set to sum or count.
            partial[SpendDimension.IDENTITY_BRIDGE] = identity_bridge_portion()

        return partial

    async def _project_portion(
        self, session: AsyncSession, admitted: TimeRange, *, access: AccessScope | None = None
    ) -> Portion | None:
        """The project-dimension portion for a period, or None when the period has no hole.

        **A property of the period and not of a holder**, which is why a batch reads it once. Two
        different project holders over the same period are incomplete by the same amount: the hole
        is the set of rows below the identity floor that carry no project at all, and no holder's
        identity narrows it.

        **`access` is keyword-only and defaults to no boundary**, exactly as it does on
        `_dimension_label`: a caller's boundary is *not your money* and never *missing money*, so a
        portion it excludes must not be reported as a hole. It is emphatically not the holder's
        identity narrowing the period's portion - it is the reader's permission deciding whether the
        portion is theirs to be told about at all.
        """
        sub_range = self._coverage.project_dimension_portion(admitted)
        if sub_range is None:
            return None
        spend, rows = await self._repository.project_dimension_residue(session, sub_range, access)
        # Only a period that actually holds project-less money is incomplete. Labelling one that
        # holds none would report a covered period as partial while nothing is missing.
        return _money_portion(spend, rows, sub_range=sub_range)

    @staticmethod
    def _batch_label(holder: BudgetHolder, project_portion: Portion | None) -> dict[SpendDimension, Portion]:
        """Assemble one holder's annotations from portions already read for the whole batch.

        Kept beside `_label` and deliberately not merged with it: this one performs no I/O, which
        is the property that lets the batch label a hundred holders without a hundred queries.
        """
        partial: dict[SpendDimension, Portion] = {}
        if project_portion is not None and isinstance(holder, _PROJECT_KEYED_HOLDERS):
            partial[SpendDimension.PROJECT] = project_portion
        if isinstance(holder, _BRIDGE_DEPENDENT_HOLDERS) and holder.aliases is None:
            # The one portion that is not computable: an unresolvable alias set means the person's
            # fallback rows cannot be *identified*, so there is no row set to sum or count. A
            # surface renders exactly that - never a zero, never a blank, and never the row count
            # standing in for the money.
            partial[SpendDimension.IDENTITY_BRIDGE] = Portion(spend=None, rows=None)
        return partial

    async def _label(
        self,
        session: AsyncSession,
        holder: BudgetHolder,
        admitted: TimeRange,
        *,
        access: AccessScope | None = None,
    ) -> dict[SpendDimension, Portion]:
        """Which dimensions this holder's figure is incomplete in, and by how much.

        A figure can be incomplete in two dimensions at once with two different portions, which is
        why this returns a mapping rather than a single slot.

        `access` narrows the portion it reads, for the reason `_project_portion` gives, so that the
        single-holder label is the label the batch would produce for the same holder, period **and
        boundary** - the batch equality the port's contract requires.
        """
        project_portion = (
            await self._project_portion(session, admitted, access=access)
            if isinstance(holder, _PROJECT_KEYED_HOLDERS)
            else None
        )
        return self._batch_label(holder, project_portion)


@asynccontextmanager
async def _ledger_session() -> AsyncIterator[AsyncSession]:
    """A ledger read session whose connection wait is measured before any read runs on it.

    EPMCDME-15537: a slow figure is either a slow query or a long queue for a connection, and only
    this wait tells the two apart - which decides whether the ledger needs a pool of its own. The
    wait includes the `pool_pre_ping` round trip a checkout makes, so even an idle pool reports one
    round trip rather than zero.

    A failed checkout is timed too, under its own outcome: a pool timeout is the clearest sign of a
    queue there is. A cancelled wait is timed as `cancelled` and re-raised, as `SpendService._ask`
    times a cancelled read, so abandoned waits never pose as failed checkouts.

    The eager checkout moves the connection's acquisition earlier rather than adding one: every
    ledger session issues at least one statement, since each early return in `SpendLogsLedger`
    happens before its session opens.
    """
    from codemie.clients.postgres import get_ledger_async_session

    async with get_ledger_async_session() as session:
        started = time.monotonic()
        outcome = "failed"
        try:
            await session.connection()
            outcome = "ok"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            # A fresh dict per call: `record_duration_metric` mutates what it is handed.
            BaseMonitoringService.record_duration_metric(
                SPEND_LEDGER_CONNECTION_WAIT_METRIC,
                time.monotonic() - started,
                attributes={"pool": "dedicated" if config.SPEND_LEDGER_POOL_SIZE > 0 else "shared", "outcome": outcome},
                per_user=False,
            )
        yield session


def build_spend_logs_ledger(coverage: LedgerCoverage) -> SpendLogsLedger:
    """Compose the ledger against the ledger's timed async session factory.

    The composition root, and the only place the four coverage values are read - and the one place
    `SPEND_LEDGER_PROJECTION_ENABLED` decides whether reads may be answered from the hourly projection.
    """
    repository = SpendLogsRepository(projection=config.SPEND_LEDGER_PROJECTION_ENABLED)
    return SpendLogsLedger(coverage, session_factory=_ledger_session, repository=repository)


__all__ = ["LEDGER_NAME", "SpendLogsLedger", "build_spend_logs_ledger"]
