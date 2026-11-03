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

"""The engine that answers the periods below the coverage floor - today's number, kept working.

**Why a second Elasticsearch-backed ledger exists at all.** `F` - the published coverage floor - is
the instant `litellm_spend_logs` starts holding rows, and production's is 1 Oct 2026. Below it the
spend ledger is *right* to report absent: it genuinely has no rows there. But the surfaces asking
are the surfaces that render money today, and today they render an Elasticsearch sum over
`attributes.money_spent`. Serving them `unavailable` for every period the platform has ever had is
not a truthful improvement, it is a regression with a correct-looking justification. So the
composition seam narrows a crossing period to `[max(start, F), end)` for the ledger and hands the
sub-`F` remainder **here**, and here is the aggregation those surfaces were already reading.

**This adapter is a configuration of `DefaultSpendLedger`, not a second implementation of it.** The
money rule, the half-open bound, the settled/unsettled distinction and the access boundary all stay
in the one place that already owns them; what this module adds is a call-class scope and a
metric-name set. A second Elasticsearch money query written here would be exactly the "two units
each centralising their logic and still disagreeing" that the port exists to remove.

**THE RULE FOR A CATEGORY-KEYED HOLDER: below `F` the budget-category dimension does not exist in
the metric store, so the holder has no figure and says so.** No emission has ever written a budget
category - not before the cutover and not after it - so there is nothing here to filter a
`PersonalHolder`, `ProjectHolder`, `ProjectMemberHolder` or `ProjectGroupHolder` on. This class
therefore answers all four exactly as `DefaultSpendLedger` answers them, with
`SpendResult.no_figure(period)`, and that is what makes a pre-floor budget figure render as
**qualified** - a limit shown with no consumption beside it - instead of as a measurement. The
holder read is not a place where the pre-cutover number can be recovered; it is a place where the
dimension the caller asked about was never recorded.

**A traffic class is not a budget category, and the CLI one is the exception worth naming.** The
holder rule above is about a dimension no emission has ever written; `attributes.cli_request` is the
opposite case - a flag `llm_proxy_monitoring_service` writes on every proxied call, and the exact
predicate the pre-cutover leaderboard and five analytics handlers filtered their CLI money on. So the
class split answers `TrafficClass.CLI` below `F` and leaves the other three absent, and it is
`DefaultSpendLedger` that answers it, gated on the CLI proxy metric being in the reading adapter's own
name set - true here because `legacy_metric_names` puts it back, false above the floor where the
spend-logs ledger owns that traffic. Without it a pre-floor leaderboard row printed a person's total
beside a **blank** CLI cell, on a page production prints as two numbers.

**No category is given a zero to make a sum come out right.** The metric store never recorded budget
categories, so an identity's total cannot be split across them, and a zero is a positive claim that
nothing was spent. Surfaces read one category-keyed holder at a time and render it as a whole figure
(`service/project/project_spending_service.py` per budget row; `project_handler.py`,
`budget_handler.py`, `budget_usage_service.py`, `member_spend_service.py`), so a zero there would
publish real money as an exact `$0.00` - and in a period crossing `F` it would be added to the
above-floor figures, making the whole row read as complete.

**Where an analytics project total comes from instead.** `project_handler` reads the `PROJECT`
dimension for its pre-floor money rather than adding up a project's assigned budget categories - a
dimension the metric store *does* carry an attribute for, and the one the pre-cutover
projects-spending aggregation itself grouped on. That read reproduces the pre-cutover number
honestly, because it asks the question the store can answer; the category-keyed holder asks one it
cannot.

**Widening the double-count guard is safe here for one reason, and it is a precondition rather than
a preference.** `DefaultSpendLedger` excludes `CLI_LLM_USAGE_TOTAL` because that traffic is
LiteLLM-routed and the spend-logs ledger already reports it. Below `F` there are no spend-logs rows
at all - that is what the floor *means* - so nothing else reports it, the exclusion drops every
pre-cutoff CLI cost instead of guarding anything, and `legacy_metric_names` puts it back. The same
argument covers every other name this module adds past the port's table: the ledger prices that
traffic too, so above `F` reading it here would duplicate a priced call, and below `F` not reading it
drops money the surface printed. **An instance of this class asked about a period reaching at or
above `F` double-counts every call carried on an added name**, silently and plausibly. The cutover
owns the clipping that makes the precondition true; this class cannot check it, because a period is
an argument and the floor is not.

**Below `F` this adapter sums exactly what the surface summed, and that is the rule rather than a
preference.** What a pre-floor period owes its reader is *parity with the number production prints
today*, so the basis for `LEGACY_MONEY_METRICS` is the metric-name list the pre-cutover aggregations
themselves filtered on - `MetricName.SPENDING_METRICS`, expanded here through the very
`to_list_from_group` call `project_handler` makes - unioned with the port's own money table so
nothing a ledger prices above the floor is lost either. Both halves are *derived*, never restated,
because a hand-copied list drifts from the names that carry money. `SPENDING_METRICS` holds money-bearing
names beyond the port's four money names and the CLI proxy metric - the three generator metrics, the
workflow-output-change metric, the tool-token metric and the marketplace-validation metric, every one
emitted through `emit_llm_token_metric` with `attributes.money_spent` and `attributes.project` on the
document. A pre-floor read missing any of them returns a **short figure as exact**, carrying neither
`unavailable` nor a `partial` portion, and a project whose pre-floor money rides only on those names
reads as a measured `$0.00`, which `enriched_spend_rows` then drops from the table altogether.

**The port's narrower money table is the right basis above the floor and the wrong one below it.**
Above `F` it says which Elasticsearch documents would duplicate money the ledger has already priced,
which is a statement about the ledger; below `F` there is no ledger to agree with and nothing to
reproduce except the sum the surface performed. Deriving from the surface's list is what makes the
two sets unable to drift apart: a tenth spending metric added later joins this set on the same
commit, and a test asserts the containment so it cannot be added on one side only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from codemie.configs import logger
from codemie.service.analytics.metric_names import MetricName
from codemie.service.spend.coverage import SpendDimension, SpendResult, TimeRange
from codemie.service.spend.default_ledger import _MONEY_METRICS, DefaultSpendLedger
from codemie.service.spend.ledger import (
    AccessScope,
    BudgetHolder,
    ComposedScope,
    DimensionScope,
    PersonTotalHolder,
    ReportFilter,
    RequestCost,
)
from codemie.service.spend.routing import LLMCallClass
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.traffic_class import TrafficClass

LEGACY_LEDGER_NAME = "legacy"

#: What the pre-cutover surfaces themselves summed, expanded exactly as `project_handler` expands it.
#:
#: Read through `to_list_from_group` rather than transcribed, so a spending metric added to the group
#: later cannot be money this adapter silently stops counting. Below `F` this list - not the port's
#: money table - is what decides the figure, for the reason the module docstring gives.
_SURFACE_MONEY_METRICS: frozenset[str] = frozenset(MetricName.to_list_from_group(MetricName.SPENDING_METRICS))

#: The port's money table flattened across every call class: what a ledger prices above the floor.
_PORT_MONEY_METRICS: frozenset[str] = frozenset(name for names in _MONEY_METRICS.values() for name in names)

#: The pre-cutoff money names each call class adds on top of the guard's own table.
#:
#: **Derived by subtracting the whole port table, not merely the chat half**, which is what keeps the
#: per-class sets disjoint: a name the port already assigns to embeddings stays there and is never
#: re-added under chat. Disjointness only shows up as a bug in the mixed configuration - two legacy
#: engines, one per registry slot, must partition the metric names between them or the name they
#: share is summed twice for one deployment - and it is asserted by a test for that reason.
#:
#: **Chat is where these names belong, not merely where the subtraction leaves them.**
#: `CLI_LLM_USAGE_TOTAL` is chat traffic: the CLI proxies chat completions and nothing else. The three
#: generator metrics, the workflow-output-change metric, the marketplace-validation metric and
#: `CODEMIE_TOOLS_USAGE_TOKENS` each bill a completion made on the chat path - a generator asks a
#: model to write an assistant, a prompt or a skill; a tool's token cost is the chat call the tool
#: made. The one embedding-priced name in the surface's list, `DATASOURCE_TOKENS_USAGE`, is already
#: the port's `EMBEDDINGS` entry, so it stays with the engine that owns embeddings.
_LEGACY_ONLY_METRICS: dict[LLMCallClass, frozenset[str]] = {
    LLMCallClass.CHAT: _SURFACE_MONEY_METRICS - _PORT_MONEY_METRICS,
}


def legacy_metric_names(call_classes: Iterable[LLMCallClass]) -> frozenset[str]:
    """Every metric name a pre-cutoff money read may sum for these classes.

    Built from `_MONEY_METRICS` **and** `MetricName.SPENDING_METRICS`, both read rather than
    restated, so neither the guard's table nor the surfaces' own list can drift from this set by a
    name added on one side only. The per-class union is what makes the scope **narrowing** rather
    than decorative: an engine handed `{EMBEDDINGS}` reads the datasource metric and cannot reach a
    chat document, which is the same guarantee `DefaultSpendLedger`'s own class scope gives above the
    floor and is needed for the same reason - a mixed deployment runs two engines and neither may
    count the other's rows.

    The partition still covers the union: every surface name outside the port's table is chat's, so
    `legacy_metric_names(LLMCallClass)` is the whole of `LEGACY_MONEY_METRICS` and no name is
    reachable from two classes at once.
    """
    names: set[str] = set()
    for call_class in call_classes:
        names |= _MONEY_METRICS[call_class]
        names |= _LEGACY_ONLY_METRICS.get(call_class, frozenset())
    return frozenset(names)


#: What an unscoped pre-floor engine reads: everything the pre-cutover surfaces summed, **plus** the
#: port's own money table so nothing a ledger prices above the floor is dropped either.
#:
#: A test asserts this is a superset of `MetricName.SPENDING_METRICS`, and it is a regression test
#: rather than a description: under-reading here does not fail loudly, it returns a short figure as
#: an **exact** one, and on a project whose pre-floor money rides only on the missing names it
#: returns a measured `$0.00` that the row enricher then drops. The additions past the port's table
#: are also what the clipping precondition above pays for - an instance asked about a period reaching
#: at or above `F` double-counts every one of them.
LEGACY_MONEY_METRICS: frozenset[str] = legacy_metric_names(LLMCallClass)


class LegacySpendLedger:
    """The pre-floor spend ledger: `DefaultSpendLedger` over a widened metric-name set.

    **Every read is delegated**, because for every one of them the pre-floor answer *is* the metric
    store's answer and this class knows nothing the adapter does not. The module docstring explains
    why a category-keyed holder is answered absent and never with a zero.

    **Scoped to every call class unless a caller says otherwise.** Below the floor there is usually no
    other engine to divide the classes with - the division exists so two adapters serving one
    deployment cannot both count a call - so serving all of them is what "this is the only engine
    here" is spelled as. The mixed configuration is the exception `call_classes` exists for.
    """

    ledger_name: str = LEGACY_LEDGER_NAME

    def __init__(
        self,
        es_ledger: DefaultSpendLedger | None = None,
        call_classes: Iterable[LLMCallClass] | None = None,
    ) -> None:
        """
        Args:
            es_ledger: the metric-store adapter to read through; built over `call_classes` and their
                `legacy_metric_names` when omitted, which is what composition wants. Injected only by
                tests, and injected rather than reached for so the holder rule can be exercised
                against a scripted store without an Elasticsearch round trip. **An injected adapter
                carries its own scope**, so `call_classes` is not applied on top of one - narrowing a
                caller's own adapter behind its back would give it a name set it never asked for.
            call_classes: the classes this engine answers for, defaulting to all of them. It reaches
                the wrapped adapter *and* `legacy_metric_names`, so it genuinely narrows which metric
                names are read rather than merely labelling the instance. That is what the mixed
                configuration needs: chat through LiteLLM and embeddings native means two slots, two
                cutover wrappers and therefore two pre-floor engines, and an engine per slot scoped to
                every class would sum every pre-floor row **twice** - once under each wrapper - with
                both figures looking entirely plausible on their own.
        """
        classes = frozenset(LLMCallClass if call_classes is None else call_classes)
        if es_ledger is None:
            es_ledger = DefaultSpendLedger(classes, metric_names=legacy_metric_names(classes))
        self._es = es_ledger

    # -- the port: the holder reads ---------------------------------------------

    async def consumed_for_holder(
        self, holder: BudgetHolder, period: TimeRange, access: AccessScope | None = None
    ) -> SpendResult:
        """What one holder consumed below the floor.

        Delegated to the batch rather than deciding again here, because a single-holder path with its
        own copy of the rule is free to disagree with the batch about the same holder, and two figures
        for one holder is the defect the port exists to remove. The two paths must agree term for
        term, and a test asserts it for all five holder shapes.
        """
        results = await self.consumed_for_holders([holder], period, access)
        return results[holder]

    async def consumed_for_holders(
        self,
        holders: Sequence[BudgetHolder],
        period: TimeRange,
        access: AccessScope | None = None,
        *,
        report_filter: ReportFilter | None = None,
    ) -> dict[BudgetHolder, SpendResult]:
        """Per-holder figures below the floor: the person's total, and no figure for a category.

        **`PersonTotalHolder` passes straight through to the adapter, unchanged.** It is not keyed on
        a category, the metric store records the person, and the adapter already answers it exactly -
        including the `identity_bridge` qualification an unresolved alias set carries. It is handed to
        the adapter's own batch so a page asking about many people still costs one round trip.

        **The other four get `no_figure`, and it is the same answer the adapter gives above the
        floor** - for the same reason, which is why this method delegates the person and decides
        nothing else. The category dimension is absent from the store in every period, so a
        category-blind total presented as one category's spend is a *wrong* number rather than an
        absent one, and a zero is a *claim* rather than a silence. The module docstring names the
        surfaces that render a single category-keyed holder alone, and why a sum over categories is not
        this method's to fix.

        **A category-keyed holder is never probed with a `PersonTotalHolder`.** `PersonTotalHolder`
        spans the personal *and* project pots by design, so a `PersonalHolder` probed with it would
        swap the personal-pot predicate for the both-pots one: a personal budget's pre-floor figure
        would include every call made with a project in context, and the same rows would be counted
        again in that project's own `ProjectHolder` figure, in two holders the port keeps apart.

        Every holder asked about appears in the result, which is the port's contract and not a
        convenience: an absent key renders as a blank rather than as a state, and a blank is a cell no
        flag, log or annotation accounts for.

        `access` narrows the delegated read exactly as it narrows the adapter's own, and its `None`
        default means the same thing it means there - a read with no caller, which is what a budget or
        scheduled path passes and which must not be narrowed by whoever is looking. The category-keyed
        answers are computed without a query and so disclose nothing for it to narrow. `report_filter`
        is delegated the same way and for the same reason: the adapter's own batch is the pre-cutover
        answer, filter included.
        """
        results: dict[BudgetHolder, SpendResult] = {}
        person_totals: list[PersonTotalHolder] = []

        for holder in holders:
            if isinstance(holder, PersonTotalHolder):
                person_totals.append(holder)
            else:
                # Every non-person holder, not only the four named ones: a sixth holder shape added
                # later is answered "no figure" rather than raising, because refusing to render a
                # money row is a smaller failure than a page that 500s - and it is still the honest
                # answer until the store learns that holder's dimension.
                results[holder] = SpendResult.no_figure(period)

        if results:
            # One line per call rather than one per holder, like every reason the adapter logs: a
            # project table asking about forty projects across three categories writes one line.
            logger.debug(
                f"spend_legacy_ledger: {len(results)} category-keyed holder(s) reported with no "
                "figure; no pre-floor emission carries a budget category, and a zero there would be "
                "a claim that nothing was spent"
            )

        if person_totals:
            results.update(
                await self._es.consumed_for_holders(person_totals, period, access, report_filter=report_filter)
            )

        return results

    # -- the port: delegated unchanged -------------------------------------------
    #
    # Below the floor the metric store is the whole of the truth, and for every read here the
    # adapter's answer already *is* the pre-cutover answer. Adding a rule on top would be inventing a
    # pre-cutover behaviour rather than reproducing one.

    async def aggregate(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        """A pre-floor total, optionally within one dimension's scope - the adapter's own answer."""
        return await self._es.aggregate(period, scope, resolved_ids)

    async def aggregate_by_dimension(
        self,
        period: TimeRange,
        dimension: SpendDimension,
        values: Sequence[str],
        ids_by_value: Mapping[str, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> Mapping[str, SpendResult]:
        """One pre-floor figure per dimension value - the adapter's own answer, adjusted by nothing.

        **A pre-floor project total does not come through here, and deliberately not.** The obvious
        route - group on the project attribute the metric store writes - is refused in
        `_DIMENSION_FIELD`, which spells out why: that attribute is the access boundary's rather than
        a dimension key, it reaches a document only when the emitting call site carried one, and a
        parallel read over it re-derives a normalisation, a request filter and a metric set the
        surface already applied. `project_handler` takes its pre-October money from its own candidate
        aggregation instead, so nothing here has to reproduce those four decisions a second time.

        Nothing is filled in, spread or substituted on the way through: a value the adapter cannot
        express is reported absent, because every alternative is an attribute-blind sum presented as
        the value that was asked for.
        """
        return await self._es.aggregate_by_dimension(period, dimension, values, ids_by_value, access, report_filter)

    async def aggregate_by_time_bucket(
        self,
        period: TimeRange,
        width: str,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> Mapping[tuple[datetime, TrafficClass], SpendResult]:
        """Money per (bucket, traffic class) below the floor - **the CLI series, three absences.**

        `attributes.cli_request` is the one traffic class the metric store has a word for, and below
        `F` the proxy metric it rides on is inside this engine's name set - so the adapter draws the
        weekly CLI line from the very aggregation the pre-cutover surfaces performed, and this class
        neither adds to it nor adjusts it.

        The other three classes have no word here, and clipping a period below `F` does not give them
        one. The plausible substitute - grouping by metric name - answers a different question with
        figures that disagree with every other surface for the same week, which is why the adapter
        leaves those buckets absent and why this class does not derive them.
        """
        return await self._es.aggregate_by_time_bucket(period, width, resolved_ids, access, report_filter)

    async def aggregate_by_traffic_class(
        self,
        period: TimeRange,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> Mapping[TrafficClass, SpendResult]:
        """Money per traffic class below the floor - absent, the adapter's own answer.

        The store has a word for one class only, and the adapter reads it solely for the bucketed
        chart: with no consumer of this whole-window split below the floor, every class is left
        absent here rather than one drawn and three not.
        """
        return await self._es.aggregate_by_traffic_class(period, resolved_ids)

    async def aggregate_by_spend_group(
        self,
        period: TimeRange,
        *,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> Mapping[SpendGroup, SpendResult]:
        """The summary cards below the floor - each the card's own aggregation, reproduced verbatim.

        The one class split the metric store can express: its vocabulary *is* the metric name, and
        each summary card was always a metric-name group. The adapter answers exactly what the cards
        summed before the ledger - which is the whole requirement below `F`, and why the pre-floor
        side need not foot as a partition even though the ledger side does.
        """
        return await self._es.aggregate_by_spend_group(period, access=access, report_filter=report_filter)

    async def aggregate_composed(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        traffic_class_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        """A pre-floor total over several ANDed terms, where every term is expressible.

        The adapter's all-or-nothing rule stands unchanged, and it is the rule that matters most on
        this path: a conjunction answered with the subset of its terms the store can express is a
        **wider** figure, not a narrower one, so dropping a term would report a person's entire spend
        as their spend in one project.
        """
        return await self._es.aggregate_composed(
            period, scope, dimension_ids=dimension_ids, traffic_class_ids=traffic_class_ids
        )

    async def aggregate_composed_by_traffic_class(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        ids_by_class: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> Mapping[TrafficClass, SpendResult]:
        """A composed scope split per class below the floor - **the CLI figure the surface printed.**

        Delegated like every other read, and the one read here that comes back with a figure rather
        than an absence: the store's own CLI flag is what the pre-cutover surfaces filtered on, this
        engine's name set carries the metric that flag rides on, so the adapter can express the class
        and does. The other three stay absent - the store has no word for them, and a metric-name
        grouping answers a different question with figures that disagree with every other surface.

        **The figure is a share of the person's total and never an addition to it.** It reads the same
        pre-floor documents `consumed_for_holders` reads for a `PersonTotalHolder`, narrowed by two
        more conjuncts, which is precisely how the pre-cutover leaderboard produced both numbers - one
        pass over these documents, the CLI amount a filtered bucket inside the total. A consumer
        adding them would count that money twice.
        """
        return await self._es.aggregate_composed_by_traffic_class(
            period, scope, dimension_ids=dimension_ids, ids_by_class=ids_by_class
        )

    async def cost_for_request(self, *, run_id: str | None = None, request_id: str | None = None) -> RequestCost:
        """What one pre-floor unit of work cost - `unavailable`, and never `pending`.

        There is no store here that will ever answer an id-keyed lookup, so a pending state would
        promise a figure that never arrives and drive a re-read that cannot succeed. Nothing is lost:
        on this path the cost was computed locally at the time of the call and is already on the
        record.
        """
        return await self._es.cost_for_request(run_id=run_id, request_id=request_id)

    async def request_count(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> int:
        """How many money-bearing documents a pre-floor scope saw - the adapter's own count."""
        return await self._es.request_count(period, scope, resolved_ids)


__all__ = ["LEGACY_LEDGER_NAME", "LEGACY_MONEY_METRICS", "LegacySpendLedger", "legacy_metric_names"]
