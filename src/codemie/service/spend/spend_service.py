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

"""The one entry point every spend consumer calls.

A consumer asks *what was spent*; it does not choose a ledger, sum across call classes, fetch a
correlation-id set, or decide what an absent figure means. Each of those is a rule with exactly one
right answer, and each was independently rediscovered by a different consumer before this port
existed - which is how five routes to one number came about.

**Why this sits above the ledgers rather than inside one.** A holder's spend spans call classes, and
in a partly-LiteLLM deployment those classes are served by *different* ledgers. Somebody has to add
the pieces up. Left to callers, forty sites would each add them up slightly differently; here it is
one function with one trap written down in it.

**The trap, and it is easy to write the wrong way.** The obvious loop is "for each call class, ask its
ledger, add the answers". That double-counts every figure in a deployment where **one** ledger serves
both classes - the ordinary case - because the same ledger is asked twice and each answer already
covers everything it holds. So the classes are grouped **by ledger** and each ledger is asked exactly
once.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from decimal import Decimal

from codemie.configs import config, logger
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import (
    SPEND_LEDGER_READ_DURATION_METRIC,
    SPEND_LEDGER_READ_FAILURES_METRIC,
)
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
from codemie.service.spend.ledger_registry import ledger_coverage, ledgers_by_call_class
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.traffic_class import UNION_CLASSES, TrafficClass

__all__ = ["SpendService", "spend_service"]


def _distinct_ledgers() -> list[SpendLedger]:
    """The ledgers serving this deployment, each appearing **once**.

    De-duplicated on object identity, which is what stops the double count described in this
    module's docstring: a ledger serving both call classes answers for both, so asking it per class
    and adding would report everything it holds twice.
    """
    seen: list[SpendLedger] = []
    for ledger in ledgers_by_call_class().values():
        if not any(ledger is already for already in seen):
            seen.append(ledger)
    return seen


class SpendService:
    """The port's public face. Consumers depend on this, never on a ledger."""

    async def consumed_for_holder(
        self, holder: BudgetHolder, period: TimeRange, *, access: AccessScope | None = None
    ) -> SpendResult:
        """What a holder consumed over a period, across every call class.

        The sum spans classes because a budget does: a person's platform budget covers their chat
        *and* their embedding work, and in a partly-LiteLLM deployment those are recorded in two
        different places. **Each class is served by exactly one ledger**, so no row is counted twice
        and none is dropped.

        **`access` is keyword-only, and it defaults to no boundary because a holder read is mostly
        not a report.** A budget's consumption is a property of the budget rather than of whoever is
        looking: narrowing it would let a person's spend escape their own limit whenever the reader
        could not see the project it landed in. So the default is the budget answer, and a *report*
        call site - a table of users' or projects' spend, whose rows Elasticsearch already narrowed -
        names its scope explicitly, taking it from `AnalyticsQueryPipeline.access_scope` rather than
        deriving one of its own.

        **That is the opposite default from `aggregate_by_dimension`, deliberately.** Every consumer
        of *that* read is a caller-facing table, so a missing argument there can only be a forgotten
        boundary and is a `TypeError`. Here both kinds of consumer are legitimate, so the argument
        cannot be made mandatory without writing `access=None` at every budget path - which would
        make the boundary a ritual rather than a decision.
        """
        results = [
            await self._ask(
                ledger,
                "consumed_for_holder",
                period,
                ledger.consumed_for_holder(holder, period, access),
                SpendResult.no_figure(period),
            )
            for ledger in _distinct_ledgers()
        ]
        return combine_results(results, period)

    async def consumed_for_holders(
        self,
        holders: Sequence[BudgetHolder],
        period: TimeRange,
        *,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[BudgetHolder, SpendResult]:
        """`consumed_for_holder` for many holders - the read every list screen makes.

        Each ledger is asked **once for the whole holder set**, which is the same de-duplication
        rule as the single-holder path and for the same reason: a ledger serving both call classes
        answers for both, so asking it per class and adding would report everything it holds twice.
        Then each holder's answers are combined across ledgers exactly as one holder's are, so a
        batched figure equals the single-holder figure term for term.

        **One boundary reaches every ledger, and every holder inside each.** `access` means exactly
        what it means on `consumed_for_holder`, default included: the boundary belongs to the caller,
        not to a holder, so there is nothing per-holder about it to get wrong - and a boundary
        honoured by one adapter and dropped by the other is not a boundary at all.

        **`report_filter` reaches every ledger the same way, and defaults to `None` for the same reason
        `access` does.** It is the dashboard's users and projects filter, which a users- or
        projects-spending table must carry because its rows were narrowed by it; a budget, a list
        screen or a member's spend must never be narrowed by what one dashboard happened to ask, so
        none of them names it. The port explains the default at length on `SpendLedger.consumed_for_holders`.
        """
        unique = list(dict.fromkeys(holders))
        per_ledger = [
            await self._ask(
                ledger,
                "consumed_for_holders",
                period,
                ledger.consumed_for_holders(unique, period, access, report_filter=report_filter),
                lambda: {holder: SpendResult.no_figure(period) for holder in unique},
            )
            for ledger in _distinct_ledgers()
        ]
        return {
            holder: combine_results([answers[holder] for answers in per_ledger if holder in answers], period)
            for holder in unique
        }

    async def aggregate(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        *,
        resolved_workflow_name: str | None = None,
    ) -> SpendResult:
        """A report's total, optionally within one dimension's scope.

        **The correlation-id set is fetched here, once, and handed to every ledger.** A ledger holding
        a native column for the dimension needs it for its column-less rows; a ledger whose money is
        already in the metric store ignores it. Fetching it here means the widened window and the
        paging rule live in one place rather than in each ledger.
        """
        resolved_ids: tuple[str, ...] = ()
        if scope is not None:
            resolved_ids = await self._metric_store_ids(scope, period, resolved_workflow_name)

        results = [
            await self._ask(
                ledger,
                "aggregate",
                period,
                ledger.aggregate(period, scope, resolved_ids),
                SpendResult.no_figure(period),
            )
            for ledger in _distinct_ledgers()
        ]
        return combine_results(results, period)

    async def aggregate_by_time_bucket(
        self,
        period: TimeRange,
        width: str,
        *,
        access: AccessScope | None,
        report_filter: ReportFilter | None = None,
    ) -> dict[tuple[datetime, TrafficClass], SpendResult]:
        """Money per (time bucket, traffic class), across every call class.

        **`access` is keyword-only and has no default, for `aggregate_by_dimension`'s reason:** the
        chart this feeds sits on a dashboard `SecureQueryBuilder` narrowed to the caller, so a figure
        computed without the boundary draws the whole platform's spend for a plain member. `None` has
        to be written out to mean *no caller*. `report_filter` is the dashboard's own users and
        projects filter, and it narrows the read the way the query builder narrowed the rows.

        **The two union classes' correlation-id sets are fetched here, once for the whole range.**
        Only this layer knows a class's scope in the metric store's vocabulary, the same division of
        labour as `aggregate`; and once for the range rather than once per bucket because the window
        the resolver widens is already a superset for every bucket inside it - the ledger's own
        `start_time` predicate is what assigns each row to one.

        **No `resolved_ids` parameter, deliberately, exactly as `aggregate` has none.** A consumer
        able to hand its own id set in could narrow a class's selector, and two surfaces would then
        answer differently for one week - the freedom this port exists to remove.

        Each distinct ledger is asked **once** and the answers are combined per key with the same
        `combine_results` a holder total uses, for the reason this module's docstring gives: a ledger serving
        both call classes answers for both, so asking it per class and adding double-counts it.
        """
        resolved_ids = {
            traffic_class: await self._metric_store_ids(DimensionScope(dimension, None), period, None)
            for traffic_class, dimension in UNION_CLASSES.items()
        }

        # Walked before any ledger is asked, so a width nothing can be stepped by still raises at the
        # caller: an unsteppable width is a wiring defect rather than an outage, and degrading it
        # would answer a chart nobody can key.
        grid = [(bucket, traffic_class) for bucket in bucket_starts(period, width) for traffic_class in TrafficClass]

        per_ledger: list[tuple[SpendLedger, dict[tuple[datetime, TrafficClass], SpendResult]]] = []
        for ledger in _distinct_ledgers():
            answers = await self._ask(
                ledger,
                "aggregate_by_time_bucket",
                period,
                ledger.aggregate_by_time_bucket(period, width, resolved_ids, access, report_filter),
                lambda: {key: SpendResult.no_figure(period) for key in grid},
            )
            per_ledger.append((ledger, answers))

        # Each adapter already says out loud when a key of its own does not land on its own walk, and
        # this is not that warning repeated: here is where two adapters' keys meet the *one* grid the
        # chart is drawn on, and a stride or an origin computed twice - once per adapter, once here -
        # is the only thing that can put a key beside it. Said out loud per ledger, because a key the
        # walk did not produce is dropped below and reads downstream as a bucket that held nothing.
        # Never raised: a chart missing one stray key is a smaller loss than no chart at all (AD-11).
        on_grid = set(grid)
        for ledger, answers in per_ledger:
            stray = sorted(key for key in answers if key not in on_grid)
            if stray:
                logger.warning(
                    f"spend_service: {len(stray)} (bucket, class) key(s) ledger {ledger.ledger_name} answered "
                    f"are off the walked {width} grid and their money is not reported: {stray}"
                )

        # Keyed on the closed grid - every walked bucket for every class - rather than on what the
        # ledgers returned, as the class split keys on the enum: a key no ledger answered is still
        # reported, as the unavailable state `combine_results` produces from no answers, never by
        # being absent from a chart that walked it. The walk's order is the chart's left-to-right.
        return {
            key: combine_results([answers[key] for _, answers in per_ledger if key in answers], period) for key in grid
        }

    async def aggregate_by_traffic_class(self, period: TimeRange) -> dict[TrafficClass, SpendResult]:
        """One figure per traffic class over a whole window, across every call class.

        The window total for the split `aggregate_by_time_bucket` charts. **On the port rather than
        left to a consumer adding that method's buckets up**, which would be a second combining rule
        for one class's money and would let a total disagree with the chart printed beside it.

        **No `resolved_ids` parameter, deliberately, exactly as `aggregate` has none.** A consumer
        able to hand its own id set in could narrow a class's selector, and two surfaces would then
        answer differently for one window - the freedom this port exists to remove. The two union
        classes' sets are fetched here instead, once for the whole range, because only this layer
        knows a class's scope in the metric store's vocabulary.

        Each distinct ledger is asked **once** and the answers are combined per class with the same
        `combine_results` a holder total uses, for the reason this module's docstring gives.
        """
        resolved_ids = {
            traffic_class: await self._metric_store_ids(DimensionScope(dimension, None), period, None)
            for traffic_class, dimension in UNION_CLASSES.items()
        }

        per_ledger = [
            await self._ask(
                ledger,
                "aggregate_by_traffic_class",
                period,
                ledger.aggregate_by_traffic_class(period, resolved_ids),
                lambda: {traffic_class: SpendResult.no_figure(period) for traffic_class in TrafficClass},
            )
            for ledger in _distinct_ledgers()
        ]
        # Keyed on the closed enum rather than on what the ledgers returned, unlike the bucketed
        # read whose keys depend on the range: every class is known up front, so a class no ledger
        # answered for is still reported - as the unavailable state `combine_results` produces from no
        # answers, never by being absent.
        return {
            traffic_class: combine_results(
                [answers[traffic_class] for answers in per_ledger if traffic_class in answers], period
            )
            for traffic_class in TrafficClass
        }

    async def aggregate_by_spend_group(
        self,
        period: TimeRange,
        *,
        access: AccessScope | None,
        report_filter: ReportFilter | None = None,
    ) -> dict[SpendGroup, SpendResult]:
        """One figure per coarse summary group, across every call class - the read the summary cards make.

        **`access` is keyword-only and has no default**, for the reason `aggregate_by_dimension` gives:
        the cards sit beside tables `SecureQueryBuilder` narrowed to the caller, so a figure computed
        without the boundary foots a member's rows with the platform's spend. `report_filter` carries
        the dashboard's own users and projects filter.

        Each distinct ledger is asked **once**. A group an adapter omitted - traffic its call classes
        cannot carry - is combined from the adapters that answered it; a group no adapter answered is
        the unavailable state `combine_results` produces from no answers, never an absent key.
        """
        per_ledger = [
            await self._ask(
                ledger,
                "aggregate_by_spend_group",
                period,
                ledger.aggregate_by_spend_group(period, access=access, report_filter=report_filter),
                lambda: {group: SpendResult.no_figure(period) for group in SpendGroup},
            )
            for ledger in _distinct_ledgers()
        ]
        return {
            group: combine_results([answers[group] for answers in per_ledger if group in answers], period)
            for group in SpendGroup
        }

    async def aggregate_by_dimension(
        self,
        period: TimeRange,
        dimension: SpendDimension,
        values: Sequence[str],
        *,
        access: AccessScope | None,
        report_filter: ReportFilter | None = None,
    ) -> dict[str, SpendResult]:
        """One figure per named dimension value, across every call class - the read a table makes.

        **No `ids_by_value` parameter, deliberately, exactly as `aggregate` has none.** A consumer able
        to hand its own id set in could narrow a value's selector, and two surfaces would then answer
        differently for one window - the freedom this port exists to remove. The mapping is resolved
        here instead, in one query for the whole value set, because only this layer knows a value's
        scope in the metric store's vocabulary.

        **`access` is keyword-only and has no default, and that is the whole point of it.** Every
        consumer of this read feeds a table `SecureQueryBuilder` already narrowed to the caller's own
        rows, so a figure computed without the boundary foots a narrowed row set with the whole
        platform's spend - which is exactly the disclosure that reached production once. A consumer
        that forgets the argument must get a `TypeError`, never a silent platform-wide answer, so
        `None` has to be **written out** to mean *this read has no caller*: a scheduled job, a budget
        path. The scope itself is derived in one place - `AnalyticsQueryPipeline.access_scope` - and a
        handler never builds one from a `User` for itself.

        `report_filter` carries the dashboard's own users and projects filter to every ledger, as on
        `aggregate_by_spend_group`; `None` is an unfiltered table.

        Each distinct ledger is asked **once** and the answers are combined per value with the same
        `combine_results` a holder total uses, for the reason this module's docstring gives.
        """
        requested = list(dict.fromkeys(values))
        if not requested:
            # Nothing is asked, so nothing is resolved and no ledger is disturbed.
            return {}

        ids_by_value = await self._metric_store_ids_by_value(dimension, requested, period)
        per_ledger = [
            await self._ask(
                ledger,
                "aggregate_by_dimension",
                period,
                ledger.aggregate_by_dimension(period, dimension, requested, ids_by_value, access, report_filter),
                lambda: {value: SpendResult.no_figure(period) for value in requested},
            )
            for ledger in _distinct_ledgers()
        ]
        # Keyed on the **requested** values rather than on what the ledgers returned: the caller named
        # them, so a value no ledger answered for is still reported - as the unavailable state
        # `combine_results` produces from no answers, never by being absent from a table it asked for a row of.
        return {
            value: combine_results([answers[value] for answers in per_ledger if value in answers], period)
            for value in requested
        }

    async def aggregate_composed(self, period: TimeRange, scope: ComposedScope) -> SpendResult:
        """One total for several filter terms at once, across every call class.

        The read *"spend for this person, in this project, on this workflow, since this date"* makes -
        four filters that are neither one `DimensionScope` nor one holder, and that a consumer may not
        assemble for itself, because assembling one is writing an attribution predicate outside
        `spend_logs_repository`.

        **No id parameters, deliberately, exactly as `aggregate` has none.** A consumer able to hand
        its own id set in could narrow a term's selector, and two surfaces would then answer
        differently for one window - the freedom this port exists to remove. Both sets are resolved
        here instead, because only this layer knows a term's scope in the metric store's vocabulary.

        Each distinct ledger is asked **once** and the answers are combined with the same `combine_results` a
        holder total uses, for the reason this module's docstring gives.
        """
        dimension_ids = await self._composed_dimension_ids(scope, period)
        traffic_class_ids = await self._class_ids(scope.traffic_class, period)

        results = [
            await self._ask(
                ledger,
                "aggregate_composed",
                period,
                ledger.aggregate_composed(
                    period, scope, dimension_ids=dimension_ids, traffic_class_ids=traffic_class_ids
                ),
                SpendResult.no_figure(period),
            )
            for ledger in _distinct_ledgers()
        ]
        return combine_results(results, period)

    async def aggregate_composed_by_traffic_class(
        self, period: TimeRange, scope: ComposedScope
    ) -> dict[TrafficClass, SpendResult]:
        """A composed scope split per traffic class, across every call class.

        The per-person, per-class figure a leaderboard needs, and the one shape neither existing read
        has: the platform-wide class split takes no scope, and the holder reads take no class.

        **No id parameters, deliberately**, for the reason `aggregate_composed` gives. The two union
        classes' sets are fetched here once for the whole range, exactly as the platform-wide class
        split fetches them.

        Keyed on the closed `TrafficClass` enum rather than on what the ledgers returned, like the
        platform-wide split: every class is known up front, so a class no ledger answered for is still
        reported - as the unavailable state `combine_results` produces from no answers, never by being absent.
        """
        dimension_ids = await self._composed_dimension_ids(scope, period)
        ids_by_class = {
            traffic_class: await self._metric_store_ids(DimensionScope(dimension, None), period, None)
            for traffic_class, dimension in UNION_CLASSES.items()
        }

        per_ledger = [
            await self._ask(
                ledger,
                "aggregate_composed_by_traffic_class",
                period,
                ledger.aggregate_composed_by_traffic_class(
                    period, scope, dimension_ids=dimension_ids, ids_by_class=ids_by_class
                ),
                lambda: {traffic_class: SpendResult.no_figure(period) for traffic_class in TrafficClass},
            )
            for ledger in _distinct_ledgers()
        ]
        return {
            traffic_class: combine_results(
                [answers[traffic_class] for answers in per_ledger if traffic_class in answers], period
            )
            for traffic_class in TrafficClass
        }

    async def cost_for_request(
        self,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
    ) -> RequestCost:
        """What one unit of work cost, keyed on **the id matching the record's own grain**.

        The caller chooses which id, and that choice is not a detail: an individual LLM call resolves
        by `run_id` and an outer request by `request_id`. Hydrating a single call by the request id
        writes the whole outer request's cost into *every* pending call in it, and a ten-call agent
        loop then reports roughly ten times the true cost with nothing failing.

        **A partly-settled total is reported as pending, not as settled.** An outer request can span
        call classes - a retrieval and a chat turn - so one ledger may have landed its rows while
        another has not. Returning the part that landed as though it were final is the fabricated
        total this design exists to prevent; `pending` says truthfully that more is coming. The
        settled subtotal is not carried alongside it because the type forbids a figure on an
        unsettled cost (see the open question in the implementation notes).

        **A ledger that could not be read at all makes the whole cost unavailable**, for the same
        reason: what that ledger holds is unknown, and an unknown part cannot be settled.
        """
        if (run_id is None) == (request_id is None):
            raise ValueError("exactly one of run_id or request_id identifies the grain being asked about")

        # The read names the grain rather than a period, there being none: the two grains are the
        # only thing that distinguishes one of these reads from another.
        read = f"cost_for_request by {'run_id' if run_id is not None else 'request_id'}"
        costs: list[RequestCost | None] = [
            await self._ask(ledger, read, None, ledger.cost_for_request(run_id=run_id, request_id=request_id), None)
            for ledger in _distinct_ledgers()
        ]

        # A read that **failed** is unknown, which is weaker than the "no figure is coming for this
        # record" a ledger states by answering unavailable - so it cannot be one of the fallbacks
        # combined below, and is marked absent instead. An unknown part cannot be settled: reporting
        # the half that landed as final is the same fabricated total the pending rule prevents.
        if any(cost is None for cost in costs):
            return RequestCost.unavailable()

        settled = [cost.figure for cost in costs if cost.is_settled and cost.figure is not None]

        if any(cost.is_pending for cost in costs):
            return RequestCost.pending()
        if settled:
            return RequestCost.settled(sum(settled, Decimal("0")))
        return RequestCost.unavailable()

    async def request_count(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        *,
        resolved_workflow_name: str | None = None,
    ) -> int:
        """How many requests a scope saw, failures included, across every call class."""
        resolved_ids: tuple[str, ...] = ()
        if scope is not None:
            resolved_ids = await self._metric_store_ids(scope, period, resolved_workflow_name)

        total = 0
        for ledger in _distinct_ledgers():
            # A count has no state to carry an outage in, so a failed read contributes nothing and
            # the total comes out short - the same unadmitted answer a ledger gives for rows it
            # cannot count. The `SpendResult` reported beside it is where the loss is named.
            total += await self._ask(
                ledger, "request_count", period, ledger.request_count(period, scope, resolved_ids), 0
            )
        return total

    async def _ask[Answer](
        self,
        ledger: SpendLedger,
        read: str,
        period: TimeRange | None,
        answer: Awaitable[Answer],
        fallback: Answer | Callable[[], Answer],
    ) -> Answer:
        """One ledger read, whose failure costs a **figure** and never the request.

        No request has ever depended on a spend read - a budget check, a dashboard tile, a hydrated
        cost - so a ledger that raises must leave the surface standing with the money it *can* still
        report, marked incomplete. The `SpendLedger` contract states this from the other side: an
        adapter raises and the reading path catches, reports unavailable, and counts the failure.

        **The fallback is the whole key set the read was going to answer, not an empty mapping.**
        Every combining rule in this class merges per key, and a key nobody contributed to would be filled
        from the healthy ledger alone - rendering that ledger's half of a mixed deployment as an
        exact figure. A `no_figure` per key is what makes the healthy contribution come out *flagged*,
        which is the "incomplete data" the reader is owed. It is built lazily so a healthy read pays
        nothing for the grid it never needs.

        **A failure inside one half of a `CutoverSpendLedger` degrades that whole cutover ledger,
        under its one name and one metric.** Its halves are its own composition and not this
        facade's, so splitting the credit here would name an adapter this layer does not know it has.

        **ERROR rather than the WARNING the metric-store lookup degrades with**, because the two
        losses differ in kind: a missing id set narrows a union to its ledger branch and the residue
        label still names the money, whereas a ledger that raises is an outage of the only place the
        money lives.

        **Every read is timed, whatever its outcome** (EPMCDME-15537): a figure that arrives late
        degrades nothing and so leaves no trace of its own, yet a slow ledger read is what holds a
        dashboard for tens of seconds. The duration and the slow-read WARNING name which read it was.
        A cancelled read is timed as `cancelled` and re-raised: nothing failed, so no failure is
        counted, and its duration must not inflate the failed series either.
        """
        over = f" over {period}" if period is not None else ""
        started = time.monotonic()
        outcome = "failed"
        try:
            result = await answer
            outcome = "ok"
            return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as error:
            logger.error(
                f"spend_service: ledger {ledger.ledger_name} failed {read}{over}: {error}",
                exc_info=True,
            )
            # A fresh dict per call: `send_count_metric` mutates what it is handed, adding the
            # calling user and the environment, so a shared literal would accumulate them.
            BaseMonitoringService.send_count_metric(
                name=SPEND_LEDGER_READ_FAILURES_METRIC,
                attributes={"ledger": ledger.ledger_name, "read": read},
            )
            return fallback() if callable(fallback) else fallback
        finally:
            elapsed = time.monotonic() - started
            # A fresh dict per call, for the reason the failure count above gives.
            BaseMonitoringService.record_duration_metric(
                SPEND_LEDGER_READ_DURATION_METRIC,
                elapsed,
                attributes={"ledger": ledger.ledger_name, "read": read, "outcome": outcome},
                per_user=False,
            )
            if elapsed >= config.SPEND_LEDGER_SLOW_READ_SECONDS:
                logger.warning(f"spend_service: ledger {ledger.ledger_name} slow {read}{over}: {elapsed:.2f}s")

    async def _composed_dimension_ids(self, scope: ComposedScope, period: TimeRange) -> tuple[str, ...]:
        """The metric store's ids for a composed scope's dimension term, or none if it has no term.

        **No `resolved_workflow_name` is passed, and the omission is the same choice the grouped
        dimension read makes.** That second, name-based lookup applies only where a caller has already
        disambiguated a *single* live workflow name against a directory this layer does not hold, and
        the consumers of a composed scope carry a workflow **id** (decision C: analytics key on the id,
        never the mutable display name). So a composed workflow term covers the id-emitting population
        plus the residue, and a pre-id-emission row lands in the residue rather than in a name-matched
        bucket - never in a wrong or duplicated one.
        """
        if scope.dimension is None:
            return ()
        return await self._metric_store_ids(scope.dimension, period, None)

    async def _class_ids(self, traffic_class: TrafficClass | None, period: TimeRange) -> tuple[str, ...]:
        """The union classes' id sets for a class term, whichever class the term names.

        **Not only for the class the metric store resolves.** A class term selects its own class *and*
        the residue that class split leaves - the rows no class claims, which are recognised by not
        being in the union class's id set - so a term naming a ledger-native class consumes the set
        just as much as one naming the union class itself. Returned empty for a native class, the
        residue would read against nothing and name every union-class row as claimed by no class: the
        term's figure is untouched, but the partial label beside it overstates the missing money by
        the whole assistant series.

        Fetched once for the whole window, the same division of labour the other reads keep: only
        this layer knows a class's scope in the metric store's vocabulary.
        """
        if traffic_class is None:
            return ()

        ids: tuple[str, ...] = ()
        for dimension in UNION_CLASSES.values():
            ids += await self._metric_store_ids(DimensionScope(dimension, None), period, None)
        return ids

    @staticmethod
    async def _metric_store_ids(
        scope: DimensionScope,
        period: TimeRange,
        resolved_workflow_name: str | None,
    ) -> tuple[str, ...]:
        """The metric store's answer for this scope, or an empty set if it cannot be reached.

        **A failed metric-store read degrades a report; it never fails one.** Losing the id set
        narrows the union to its ledger branch, and the rows that branch cannot bucket then land in
        the residue, where the partial label names them - so the loss is *reported* rather than
        silently subtracted from the total.

        **`L` and `N` come from the registered coverage, and omitting them was a defect rather than a
        default.** The resolver documents them as "the two config-derived values a composition root
        reads once and passes down"; unpassed, every window here silently used
        `DEFAULT_EMISSION_LAG` - 24 h - however `SPEND_LEDGER_EMISSION_LAG_HOURS` was set. A
        deployment configuring 72 h then under-widened an August report to 2 September, missed a
        metric emitted on the 3rd, and dropped that row into the residue: AD-29 is explicit that
        "under-widening is a defect and over-widening is not", because the ledger's own `start_time`
        predicate discards whatever the wider window over-collects.
        """
        from codemie.clients.elasticsearch import ElasticSearchClient
        from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
        from codemie.service.spend.dimension_scope import resolve_metric_store_ids

        if not ElasticSearchClient.is_configured():
            # No metric store means no recorded ids: the true answer, not a failed read to count.
            return ()
        coverage = ledger_coverage()
        try:
            return await resolve_metric_store_ids(
                MetricsElasticRepository(),
                scope,
                period,
                emission_lag=coverage.emission_lag,
                page_size=coverage.max_elasticsearch_ids,
                resolved_workflow_name=resolved_workflow_name,
            )
        except Exception as error:
            # WARNING where a ledger failure is an ERROR, and the difference is the size of the loss:
            # the union merely narrows to its ledger branch here and the residue label still names
            # the money, whereas a ledger that raises takes out the only place the money lives.
            logger.warning(
                f"spend_service: could not resolve metric-store ids for the {scope.dimension.value} "
                f"dimension; the union falls back to its ledger branch and the remainder is labelled: {error}"
            )
            # Counted on the same metric as a ledger failure, under the honest name of the source
            # that failed: this degradation was invisible until it showed up as a report nobody
            # could explain. A fresh dict because `send_count_metric` mutates what it is handed.
            BaseMonitoringService.send_count_metric(
                name=SPEND_LEDGER_READ_FAILURES_METRIC,
                attributes={"ledger": "metric_store", "read": "metric_store_ids"},
            )
            return ()

    @staticmethod
    async def _metric_store_ids_by_value(
        dimension: SpendDimension,
        values: Sequence[str],
        period: TimeRange,
    ) -> dict[str, tuple[str, ...]]:
        """The metric store's answer per value, or an empty mapping if it cannot be reached.

        **A failed metric-store read degrades a report; it never fails one** - the same rule
        `_metric_store_ids` implements, and for the same reason. Losing the mapping narrows every
        value's union to its ledger branch, and the rows that branch cannot bucket then land in the
        residue where the partial label names them, so the loss is *reported* rather than silently
        subtracted from each figure.

        `L` and `N` come from the registered coverage for the reason `_metric_store_ids` gives, and
        the grouped read needs them at least as much: `N` is this resolver's **paging** threshold, so
        a deployment that raised it to page a large value set in fewer round trips was still paged at
        the 50 000 default.
        """
        from codemie.clients.elasticsearch import ElasticSearchClient
        from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
        from codemie.service.spend.dimension_scope import resolve_metric_store_ids_by_value

        if not ElasticSearchClient.is_configured():
            # The same answer `_metric_store_ids` gives without a metric store.
            return {}
        coverage = ledger_coverage()
        try:
            return await resolve_metric_store_ids_by_value(
                MetricsElasticRepository(),
                dimension,
                values,
                period,
                emission_lag=coverage.emission_lag,
                page_size=coverage.max_elasticsearch_ids,
            )
        except Exception as error:
            # WARNING rather than ERROR for the reason `_metric_store_ids` gives.
            logger.warning(
                f"spend_service: could not resolve metric-store ids for {len(values)} {dimension.value} "
                f"value(s); every union falls back to its ledger branch and the remainder is labelled: {error}"
            )
            BaseMonitoringService.send_count_metric(
                name=SPEND_LEDGER_READ_FAILURES_METRIC,
                attributes={"ledger": "metric_store", "read": "metric_store_ids_by_value"},
            )
            return {}


#: The shared instance. A module-level singleton, matching the repositories it reads through.
spend_service = SpendService()
