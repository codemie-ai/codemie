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

"""Which engine answers a spend read, decided by where the requested period falls against `F`.

The spend-logs ledger serves every money figure from `litellm_spend_logs`, and it serves them
honestly: below the published coverage floor `F` that table holds nothing, so a sub-`F` read reports
`unavailable` rather than a zero. That is the correct statement about the *table* and the wrong thing
to put on a screen that showed a figure yesterday - production will carry ledger rows only from the
rollout instant, while the legacy Elasticsearch engine already holds every period before it. So a
period below `F` is served by the legacy engine, a period at or above `F` by the ledger, and a period
crossing `F` by both.

**The cutover narrows the period before it delegates, and that one move is the reason nothing
downstream changes.** `LedgerCoverage.unavailable_flag` returns None exactly when
`requested.start >= F`, so a ledger handed the pre-clipped `[max(start, F), end)` never raises a flag
about time it was not asked about. The alternative - delegate the whole range and subtract the
spurious flag afterwards - would re-derive a floor decision `coverage` already owns, in a second
place, which is how two surfaces come to sit on opposite sides of one floor. `coverage.py`,
`spend_service.py` and every existing adapter stay untouched precisely because this unit clips
instead.

**The seam is composition, not a branch.** This is a `SpendLedger` holding two `SpendLedger`s, so no
consumer, response model or screen can tell it from a single one; the fourteen consumers of
`SpendService` are unaware that a cutover exists at all, exactly as they are unaware which provider
answered them.

**No floor is read in this module.** The floor arrives already resolved from the composition root,
which is the one place that reads it. A reader fetching its own floor is the thing the coverage design
forbids by name, and it fails silently: two surfaces built a minute apart would answer one week from
two different engines and neither number would look wrong.

**The two halves are cut on one instant and then measured on two different clocks, and this unit
cannot close that gap.** The legacy half filters Elasticsearch on `@timestamp`, which is the instant a
metric document was *emitted*; the ledger half filters `litellm_spend_logs` on `start_time`, which is
the instant the call *started* - `default_ledger._query`'s range clause and the repository's
`start_time >= period.start AND start_time < period.end` predicate respectively. So `[start, F)`
beside `[F, end)` is disjoint and exhaustive over a single time axis, and there is no single axis
here: the two halves tile the requested period as *instants* while the two stores partition their
rows on quantities that differ, per call, by however long that call's metric took to be emitted.

Worked, with production's floor. `F = 2026-10-01T00:00:00Z`. A chat call starts `2026-09-30T23:50Z`
and its metric document is emitted `2026-10-01T00:05Z` carrying `money_spent = 0.50`. Asked for
`[1 Sep, 31 Oct)`, the cutover makes the two calls it always makes - legacy over `[1 Sep, 1 Oct)`,
ledger over `[1 Oct, 31 Oct)` - and **neither one claims the $0.50**: the document sits above the
legacy half's `@timestamp` bound and the row sits below the ledger half's `start_time` bound. The
combined figure is short by it and **reads as exact** - no unavailable flag, no partial portion,
nothing on any surface separating that week from a week in which the money was never spent. The
mirror case is the same defect with its sign flipped: a row whose `start_time` is at or above `F`
while its document was emitted below it - which the two writers produce whenever their clocks
disagree about the current instant, since one stamps the ledger row and the other the metric
document - is claimed by **both** halves and **double counted**, just as silently.

**The affected band is `L` wide, not one row.** `L` is the metric emission lag - config
`SPEND_LEDGER_EMISSION_LAG_HOURS`, **24 hours** by default and unchanged in production - and it is
this design's own published estimate of how far an emission can trail the work it describes: the same
`L` that `TimeRange.widened` pushes an Elasticsearch scope window out by, for this exact mismatch,
because "a metric document's timestamp is *emission* time and never assigns a row to a period". So
the exposure is every call started in `[F - L, F)` whose document landed at or above `F`, which at
the default is a **day** of traffic rather than the one row logged at `F` that a boundary defect is
usually worth. The config comment calls `L` an allowance that "errs high on purpose"; that makes it
an upper bound on the band, not a measurement of it.

**Documented rather than repaired, because no query here can repair it.** A metric document carries
no `start_time` at all, so nothing asked of the metric store can partition on the ledger's clock, and
moving the boundary only moves the band. Three mitigations exist, each with a cost and a residual
error, and none of them is a reader's to choose: place `F` at a low-traffic hour so the band is
nearly empty; overlap the halves by `L` and de-duplicate on a correlation id, if one is reachable on
both sides; or emit `start_time` onto the metric documents so the two stores share one clock. What
belongs in this module is that the exposure is known, bounded by `L`, and silent -
`tests/codemie/service/spend/test_cutover.py` pins both directions of it so it cannot change without
a test changing with it.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from datetime import datetime, timedelta
from typing import TypeVar

from codemie.service.spend.combining import combine_results
from codemie.service.spend.coverage import SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import (
    AccessScope,
    BudgetHolder,
    ComposedScope,
    DimensionScope,
    ReportFilter,
    RequestCost,
    SpendLedger,
    bucket_starts,
)
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.traffic_class import TrafficClass

__all__ = ["CutoverSpendLedger"]

_Key = TypeVar("_Key")

#: One side of a split read: the ledger that answers it, and the narrowed period it is asked about.
_Leg = tuple[SpendLedger, TimeRange]


def _canonical_start(grid: tuple[datetime, ...], moment: datetime) -> datetime:
    """The greatest canonical bucket start at or before `moment`.

    The *greatest at or before*, never the nearest: a bucket start names the instant its bucket opens,
    so a key that fell between two canonical starts belongs to the earlier one. Rounding to the nearer
    start would move a bucket's money forward in time whenever it landed in the second half of a
    stride, which is a chart that is wrong by one bar and foots perfectly.
    """
    position = bisect_right(grid, moment)
    return grid[position - 1] if position else grid[0]


def _on_canonical_grid(
    halves: Sequence[Mapping[tuple[datetime, TrafficClass], SpendResult]],
    period: TimeRange,
    width: str,
) -> dict[tuple[datetime, TrafficClass], SpendResult]:
    """Both halves' bucketed answers, re-keyed onto the grid the *caller's* period walks.

    Every canonical bucket appears for every `TrafficClass`, including one neither half returned:
    that one carries the unavailable state `combine_results` produces from no answers, because an
    absent key is not a way of saying nothing - a chart reading this mapping renders a missing key as
    a blank rather than as the state it is in.
    """
    grid = bucket_starts(period, width)

    contributions: dict[tuple[datetime, TrafficClass], list[SpendResult]] = {}
    for half in halves:
        for (start, traffic_class), result in half.items():
            contributions.setdefault((_canonical_start(grid, start), traffic_class), []).append(result)

    return {
        (start, traffic_class): combine_results(contributions.get((start, traffic_class), []), period)
        for start in grid
        for traffic_class in TrafficClass
    }


class CutoverSpendLedger:
    """One `SpendLedger` that is two, split at an instant rather than at a call class.

    **What travels between the two halves is `combining.combine_results`, imported and not
    rewritten.** The figures sum, the unavailable flag survives if either half raised it, and the
    partial entries merge per dimension - three one-line rules, each one wrong line away from a
    plausible mistake, and a second copy of them would not fail but drift. The requested range handed
    to the combiner is always the **original** period rather than a half, so a flag names the range
    the caller actually asked about instead of an interval the caller never mentioned.

    **A period lying wholly on one side is one delegation and nothing else.** That is not an
    optimisation but the steady state: a month after the rollout every live request is entirely above
    `F`, and every one of them would otherwise pay for a transition that is over. A one-element
    combine returns the same numbers, so nothing would fail - it would merely re-wrap, forever, a
    result whose figure and flags were already exactly right.
    """

    def __init__(self, legacy: SpendLedger, current: SpendLedger, floor: datetime) -> None:
        """Compose the two engines around an already-resolved floor.

        **`floor` is required, and a None raises rather than degrading.** Whether a cutover is wanted
        at all is the composition root's decision - it holds the config and it makes that decision
        once - so a None arriving here is a wiring defect and not a state. The degraded behaviour it
        would imply is the silent kind: delegating everything to one side flips either every
        pre-rollout figure to `unavailable` or every post-rollout figure to the legacy engine's, and
        both read as a data problem rather than as the construction bug they are.

        A naive or offset floor raises for the reason `TimeRange` gives. Comparing an aware period
        against a naive floor raises deep inside a comparison, far from the wiring that caused it; an
        offset one does something worse than raise, silently shifting the boundary by that offset and
        serving a whole day's rows from the wrong engine.
        """
        if floor is None:
            raise ValueError("CutoverSpendLedger requires a resolved coverage floor; the composition root decides")
        if floor.tzinfo is None or floor.utcoffset() != timedelta(0):
            raise ValueError(f"CutoverSpendLedger floor must be UTC-aware, got {floor!r}")

        self._legacy = legacy
        self._current = current
        self._floor = floor

    @property
    def ledger_name(self) -> str:
        """Both halves named, in the order they are consulted.

        A composed name rather than a fixed `"cutover"`: this string reaches logs and diagnostics, and
        the question asked of it is always *which engines answered*, which a constant cannot answer.
        """
        return f"cutover({self._legacy.ledger_name}|{self._current.ledger_name})"

    def _split(self, period: TimeRange) -> tuple[TimeRange | None, TimeRange | None]:
        """(legacy_half, current_half) - either may be None, never both.

        **The instant `F` itself belongs to the current ledger.** `F` is the first instant the ledger
        table covers, and every interval in this design is half-open (`start <= t < end`), so the
        legacy half ends *at* `F` and the current half begins *at* `F`. Adjacent half-open ranges tile
        exactly: the row logged at exactly `F` is claimed by one side and by one side only. The
        plausible mistake is a closed legacy upper bound - `[start, F]` beside `[F, end)` - which is
        the `BETWEEN` inclusivity that lands a boundary row in both totals; its mirror, `[start, F)`
        beside `(F, end)`, drops that row from both. Neither fails, and both are wrong by one row.

        Never both None, because a `TimeRange` is non-empty by construction: a period that ends after
        it starts is wholly below `F`, wholly at or above it, or crossing it.

        **What these comparisons do not buy, said where they are made.** They tile the requested
        period exactly as *instants*, and the two halves are then evaluated against two different
        clocks - emission time below `F`, start time at and above it - so the tiling the operators
        express is not the tiling the two stores perform. Money whose call started below `F` and whose
        metric document was emitted at or above it is dropped by both halves; money whose timestamps
        straddle the other way is taken by both. The module docstring works the case through with
        dates and names the bound: the band is the emission lag `L` wide -
        `SPEND_LEDGER_EMISSION_LAG_HOURS`, 24 hours by default - and no choice of boundary here
        narrows it, because a metric document has no `start_time` to be split on.
        """
        if period.end <= self._floor:
            return (period, None)
        if period.start >= self._floor:
            return (None, period)
        return (TimeRange(period.start, self._floor), TimeRange(self._floor, period.end))

    def _legs(self, period: TimeRange) -> tuple[_Leg, ...]:
        """The one or two (ledger, narrowed period) pairs this read has to make.

        One leg means the fast path - the whole call is that ledger's to answer and its result is
        returned untouched. Two legs means the period crosses `F` and the answers combine.
        """
        legacy_half, current_half = self._split(period)
        legs: list[_Leg] = []
        if legacy_half is not None:
            legs.append((self._legacy, legacy_half))
        if current_half is not None:
            legs.append((self._current, current_half))
        return tuple(legs)

    async def _one_figure(
        self,
        period: TimeRange,
        ask: Callable[[SpendLedger, TimeRange], Awaitable[SpendResult]],
    ) -> SpendResult:
        """Route a single-figure read: delegate whole, or ask both halves and combine their answers.

        `ask` carries the caller's own arguments unchanged and receives only the narrowed period, so
        no method here can accidentally pass a half's range where a scope, a holder or an id set
        belongs.
        """
        legs = self._legs(period)
        if len(legs) == 1:
            ledger, half = legs[0]
            return await ask(ledger, half)
        return combine_results([await ask(ledger, half) for ledger, half in legs], period)

    async def _per_key(
        self,
        period: TimeRange,
        ask: Callable[[SpendLedger, TimeRange], Awaitable[Mapping[_Key, SpendResult]]],
    ) -> Mapping[_Key, SpendResult]:
        """Route a mapping read, combining **per key over the union of both halves' keys**.

        The union rather than an intersection, and the distinction is the whole reason this helper is
        written once. A key only one half returned - a holder, a dimension value, a traffic class the
        other engine records nothing for - survives with that half's answer; intersecting would drop
        it entirely, and a caller's table row would then vanish rather than read as the state it is
        in. That is the same fabricated silence an absent holder key would be, arriving through the
        merge instead of through an adapter.
        """
        legs = self._legs(period)
        if len(legs) == 1:
            ledger, half = legs[0]
            return await ask(ledger, half)

        halves = [await ask(ledger, half) for ledger, half in legs]
        # Insertion-ordered rather than a set, so keys come back in the order the halves produced
        # them - legacy first, which is a chart's own left-to-right order.
        keys = dict.fromkeys(key for half in halves for key in half)
        return {key: combine_results([half[key] for half in halves if key in half], period) for key in keys}

    async def consumed_for_holder(
        self, holder: BudgetHolder, period: TimeRange, access: AccessScope | None = None
    ) -> SpendResult:
        """What a holder consumed, added across whichever engines cover the period.

        `access` is passed through untouched to both halves, because it is a property of the caller
        and not of a period: narrowing one half and not the other would make the total depend on
        where the range sat relative to `F`.
        """
        return await self._one_figure(period, lambda ledger, half: ledger.consumed_for_holder(holder, half, access))

    async def consumed_for_holders(
        self,
        holders: Sequence[BudgetHolder],
        period: TimeRange,
        access: AccessScope | None = None,
        *,
        report_filter: ReportFilter | None = None,
    ) -> Mapping[BudgetHolder, SpendResult]:
        """The batch form, combined per holder.

        Each half is asked about the **whole** holder list, so every holder still appears; the union
        merge is what guarantees it even if one engine answered about fewer than it was asked.

        `access` and `report_filter` reach both halves untouched, for the reason `consumed_for_holder`
        gives for the boundary: a filter honoured on one side of `F` only would make a filtered row's
        figure depend on where the window sat relative to the floor.
        """
        return await self._per_key(
            period,
            lambda ledger, half: ledger.consumed_for_holders(holders, half, access, report_filter=report_filter),
        )

    async def aggregate(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        """A report's total, added across whichever engines cover the period.

        `resolved_ids` is the caller's one fetch for the whole range and both halves get it whole. It
        is a superset selector rather than a period predicate - each engine's own time bound is what
        assigns a row to a half - so splitting it per half would narrow one side's selector for no
        reason and drop money nothing would flag.
        """
        return await self._one_figure(period, lambda ledger, half: ledger.aggregate(half, scope, resolved_ids))

    async def aggregate_by_time_bucket(
        self,
        period: TimeRange,
        width: str,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> Mapping[tuple[datetime, TrafficClass], SpendResult]:
        """Money per (time bucket, traffic class), on **one** grid rather than on each half's own.

        **The trap here is a drawing bug rather than an arithmetic one, which is why it needs saying.**
        Each half walks the range *it* was handed: the current half's bucket starts come from
        `_align(F, width)` and the legacy half's from `_align(period.start, width)`. Every width the
        walk can step today aligns to an absolute grid - a whole stride from the width's own origin,
        the epoch or its first Monday - so the two walks happen to agree on the bucket that straddles `F`, and a
        plain per-key merge would add its two contributions into one full bar. **That agreement is a
        property of `_align`'s table and not a promise of this method.** A width aligned to the range
        it is given - a rolling window is the plausible next entry - returns the straddling bucket
        under two keys, and the chart then draws two half-height bars where one belongs while every
        total printed beside it still foots. Nothing raises on either the arithmetic or the drawing.

        **So the grid is the caller's, computed once from the original period** with
        `bucket_starts(period, width)`, and every key either half returned is filed under the greatest
        canonical start at or before it. The keys read back are then exactly the keys an uncut ledger
        would have returned, which is the standard the whole cutover is held to.

        **Every canonical bucket appears for every `TrafficClass`**, carrying the unavailable state
        where neither half answered, because an absent key renders as a blank instead of as that
        state.
        """
        legs = self._legs(period)
        if len(legs) == 1:
            ledger, half = legs[0]
            return await ledger.aggregate_by_time_bucket(half, width, resolved_ids, access, report_filter)

        halves = [
            await ledger.aggregate_by_time_bucket(half, width, resolved_ids, access, report_filter)
            for ledger, half in legs
        ]
        return _on_canonical_grid(halves, period, width)

    async def aggregate_by_traffic_class(
        self,
        period: TimeRange,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> Mapping[TrafficClass, SpendResult]:
        """The window total behind the split chart, combined per class.

        Combined per class rather than by adding the bucketed read's buckets up, for the reason the
        port gives: that sum would be a second combining rule for one class's money, free to disagree
        with the chart printed beside it.
        """
        return await self._per_key(period, lambda ledger, half: ledger.aggregate_by_traffic_class(half, resolved_ids))

    async def aggregate_by_spend_group(
        self,
        period: TimeRange,
        *,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> Mapping[SpendGroup, SpendResult]:
        """The summary cards, combined per group across the two halves.

        Per key rather than by re-deriving one group from another: the platform figure is a read of
        its own on both sides, never the total less the CLI figure, so nothing here has to know that
        the two halves foot differently (the pre-floor side reproduces today's cards, the ledger side
        partitions).
        """
        return await self._per_key(
            period,
            lambda ledger, half: ledger.aggregate_by_spend_group(half, access=access, report_filter=report_filter),
        )

    async def aggregate_by_spend_group_per_filter(
        self,
        period: TimeRange,
        *,
        access: AccessScope | None = None,
        common_filter: ReportFilter | None,
        report_filters: Collection[ReportFilter],
    ) -> Mapping[ReportFilter, Mapping[SpendGroup, SpendResult]]:
        """Each row's cards, combined per (filter, group) - so each is what `aggregate_by_spend_group` gives.

        **Flattened onto `(filter, group)` keys so `_per_key` is the merge**, rather than a second
        merge written for nested mappings: the union over those keys is exactly the per-group union
        the single read takes for each filter, a group one half omitted included, so a row cannot read
        one figure in a batch and another alone.

        **Re-nested over the requested filters rather than over the merged keys**, because a filter no
        half answered a group for leaves no key behind to re-nest - and an absent filter is the defect
        the port names, a row rendered blank instead of as the state it is in.
        """

        async def batch(
            ledger: SpendLedger, half: TimeRange
        ) -> Mapping[ReportFilter, Mapping[SpendGroup, SpendResult]]:
            return await ledger.aggregate_by_spend_group_per_filter(
                half, access=access, common_filter=common_filter, report_filters=report_filters
            )

        legs = self._legs(period)
        if len(legs) == 1:
            return await batch(*legs[0])

        async def flattened(
            ledger: SpendLedger, half: TimeRange
        ) -> Mapping[tuple[ReportFilter, SpendGroup], SpendResult]:
            answers = await batch(ledger, half)
            return {
                (report_filter, group): result
                for report_filter, groups in answers.items()
                for group, result in groups.items()
            }

        by_filter: dict[ReportFilter, dict[SpendGroup, SpendResult]] = {
            report_filter: {} for report_filter in report_filters
        }
        for (report_filter, group), result in (await self._per_key(period, flattened)).items():
            by_filter[report_filter][group] = result
        return by_filter

    async def aggregate_by_dimension(
        self,
        period: TimeRange,
        dimension: SpendDimension,
        values: Sequence[str],
        ids_by_value: Mapping[str, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> Mapping[str, SpendResult]:
        """One figure per named dimension value, combined per value.

        A value is not a time interval, so nothing here is split by value: both halves are asked about
        the whole named set and the merge is what puts the two periods' money into one row. Both get
        `report_filter` too, for `consumed_for_holders`' reason.
        """
        return await self._per_key(
            period,
            lambda ledger, half: ledger.aggregate_by_dimension(
                half, dimension, values, ids_by_value, access, report_filter
            ),
        )

    async def aggregate_composed(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        traffic_class_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        """A conjunction's total, added across whichever engines cover the period.

        **Splitting by time is the one split this conjunction tolerates**, because the halves cover
        disjoint *rows*: no row is claimed by both, so adding them is not the overlapping-term
        addition the port prohibits. The conjunction itself is never taken apart - each half evaluates
        the whole scope over its own range, exactly as one scan would.
        """
        return await self._one_figure(
            period,
            lambda ledger, half: ledger.aggregate_composed(
                half, scope, dimension_ids=dimension_ids, traffic_class_ids=traffic_class_ids
            ),
        )

    async def aggregate_composed_by_traffic_class(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        ids_by_class: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> Mapping[TrafficClass, SpendResult]:
        """The same conjunction split per class, combined per class.

        The scope's own qualifications ride on every class from each half, and the per-dimension merge
        inside `combine_results` is what keeps a qualification raised by only one half from being
        dropped because the other half's figure looked complete.
        """
        return await self._per_key(
            period,
            lambda ledger, half: ledger.aggregate_composed_by_traffic_class(
                half, scope, dimension_ids=dimension_ids, ids_by_class=ids_by_class
            ),
        )

    async def cost_for_request(self, *, run_id: str | None = None, request_id: str | None = None) -> RequestCost:
        """What one unit of work cost - **the current ledger's answer alone, and that is complete.**

        There is no period to route on, and inventing one is the defect the port names outright: an id
        is already a bounded selector, and the obvious invented bound - the request's own day - drops
        a retry that landed on the other side of a boundary. So this read cannot be split by date, and
        the only question left is what a caller loses by the legacy half never being asked.

        **Nothing, and structurally rather than by luck.** The legacy engine's own `cost_for_request`
        returns `RequestCost.unavailable()` by design - deliberately not `pending`, which would promise
        a figure and drive a re-read - because on that path the cost was computed locally at the time
        of the call and is already on the record. No record reaches this method needing to be filled
        in from there, so a legacy lookup has nothing to add and would only hand the caller a second
        `unavailable` to reconcile against a settled figure.
        """
        return await self._current.cost_for_request(run_id=run_id, request_id=request_id)

    async def request_count(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> int:
        """How many requests a scope saw, summed over the halves.

        Two integers add. There is no flag and no portion to carry, so `combine_results` has nothing
        to do here and is not reached for - a count has no unavailable state to preserve, and wrapping
        one in a money shape to reuse the combiner would invent one.

        **Each half counts by its own rule**, the ledger deduping a retry's attempts onto one call and
        the legacy engine counting one document per call. That asymmetry is what either engine already
        returns on its own and is not introduced here. The one edge the split does own: a call whose
        attempts straddle `F` is deduped within each half and so counts once on each - at most one
        extra per call, and only for calls retried across a single instant.
        """
        legs = self._legs(period)
        if len(legs) == 1:
            ledger, half = legs[0]
            return await ledger.request_count(half, scope, resolved_ids)
        return sum([await ledger.request_count(half, scope, resolved_ids) for ledger, half in legs])
