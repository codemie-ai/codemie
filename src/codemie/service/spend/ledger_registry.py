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

"""Runtime selection of the active ledger - the single provider branch.

**The branch exists exactly once, here.** No call site branches on provider identity, and a
consumer cannot tell which adapter answered it. Without that, the guarantee degrades from a
structural property into an audit of ~110 sites each testing a flag - and into the subtler
failure of the branch being in the right *place* while asking the wrong *question*.

**Selection is per call class, not per deployment.** One configuration is genuinely mixed - chat
through LiteLLM, embeddings native - and a single boolean adapter choice is wrong for one of the
two classes whichever way it lands. A holder total is the sum across classes, and **each class is
served by exactly one ledger**, which is what stops a row being counted twice or dropped. That
mixed configuration runs nowhere today and is one environment variable away, so it can be
validated only by test, never by observation.

**An unregistered ledger answers `unavailable`, never zero.** That falls out of the noop below
rather than being a special case: before activation there is nothing to serve, and a fabricated
zero is a positive claim that nothing was spent.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime

from codemie.service.spend.coverage import LedgerCoverage, SpendDimension, SpendResult, TimeRange
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
from codemie.service.spend.routing import LLMCallClass, uses_litellm
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.traffic_class import TrafficClass

_NOOP_LEDGER_NAME = "noop"

#: Registered once at startup by enterprise code. Core never imports the adapter.
_active_spend_logs_ledger: SpendLedger | None = None

#: Registered once at startup by core, and the answer for every class LiteLLM does not serve.
_active_default_ledger: SpendLedger | None = None

#: The coverage values, read **once** at the composition root and registered here.
#:
#: **Held beside the ledgers rather than inside one, because two of the four values are not a
#: ledger's business at all.** `emission_lag` (`L`) and `max_elasticsearch_ids` (`N`) bound the
#: *metric store* window an id resolver reads, and that resolver is driven by `SpendService` above
#: every ledger - in a deployment with no LiteLLM ledger registered as much as in one with. Without a
#: route to them there, `SpendService` silently fell back to `DEFAULT_EMISSION_LAG` however the
#: deployment was configured, and an under-widened window drops a document into the residue (AD-29:
#: "under-widening is a defect and over-widening is not").
_active_coverage: LedgerCoverage | None = None


def register_spend_logs_ledger(ledger: SpendLedger) -> None:
    """Register the LiteLLM-backed ledger. Called once at startup by enterprise code."""
    global _active_spend_logs_ledger
    _active_spend_logs_ledger = ledger


def register_default_ledger(ledger: SpendLedger) -> None:
    """Register the ledger that preserves today's behaviour for non-LiteLLM traffic."""
    global _active_default_ledger
    _active_default_ledger = ledger


def register_ledger_coverage(coverage: LedgerCoverage) -> None:
    """Publish the coverage values every spend read shares. Called once at startup.

    Registered independently of any provider branch: `L` and `N` bound a metric-store window that
    belongs to the deployment rather than to whichever adapter answers a figure.
    """
    global _active_coverage
    _active_coverage = coverage


def ledger_coverage() -> LedgerCoverage:
    """The registered coverage, or the documented defaults where nothing has been registered.

    **The unregistered answer is deliberately the type's own defaults and not a config read.** No
    value here is ever re-read deep in a reader - that single read at the composition root is what
    makes activation atomic with respect to readers, so a floor becomes visible to every consumer at
    the same instant. Unregistered therefore means *the composition root has not run* - a test, a
    script - and in that state the defaults leave `coverage_floor` unset, which is `activated=False`
    and reports every figure unavailable rather than serving one against a floor nobody published.
    """
    return _active_coverage if _active_coverage is not None else LedgerCoverage()


def get_spend_ledger(call_class: LLMCallClass) -> SpendLedger:
    """The one ledger that owns this call class in this deployment.

    The predicate is the **routing** predicate, per class - not a global "LiteLLM enabled" flag,
    which is strictly broader than routing requires and true in deployments whose traffic never
    reaches LiteLLM. Selecting the SpendLogs ledger there would point every consumer at a table
    no request writes to: empty totals, every figure unavailable.
    """
    if uses_litellm(call_class):
        # **No fall-through to the default ledger here.** The default ledger is constructed with
        # the classes it owns and scopes every query to the metric names those classes emit, so
        # handing it a class it was not built for would answer a chat question with an embeddings
        # total - a smaller number that looks like a real one. An unregistered ledger reports
        # unavailable instead, which is the same state as before a first activation and needs no
        # special case at any call site.
        return _active_spend_logs_ledger if _active_spend_logs_ledger is not None else _NOOP_LEDGER
    return _active_default_ledger if _active_default_ledger is not None else _NOOP_LEDGER


def ledgers_by_call_class() -> dict[LLMCallClass, SpendLedger]:
    """Which ledger owns each call class - the composition a holder total is built from.

    Returned as a mapping rather than a single ledger because a holder's spend spans classes and
    a caller must sum across them. Deriving it here is what keeps each class owned by exactly one
    ledger; a caller that picked a ledger per class itself would be the branch this module exists
    to hold.
    """
    return {call_class: get_spend_ledger(call_class) for call_class in LLMCallClass}


def reset_ledgers() -> None:
    """Clear every registration, coverage included. For tests, so one test's registry cannot leak
    into the next - a stale `L` would silently widen or narrow the next test's resolver window."""
    global _active_spend_logs_ledger, _active_default_ledger, _active_coverage
    _active_spend_logs_ledger = None
    _active_default_ledger = None
    _active_coverage = None


class _NoopSpendLedger:
    """The answer when no ledger is registered, and it is the honest one.

    Every read reports **unavailable** - never zero, never a partial total. This is the same
    state as before a first activation, and it needs no special case at any call site: a
    consumer that renders the unavailable state correctly already handles a deployment with no
    ledger at all.

    The methods below name every argument they ignore in a comment, bare and unquoted. That
    wording is load-bearing: the arguments are required by the `SpendLedger` protocol and cannot
    be renamed or dropped, and Sonar's unused-parameter rule (python:S1172) stays quiet only for a
    name it finds delimited by whitespace in a comment or docstring - not in backticks, not with
    punctuation attached. Rewording those comments reopens 24 findings.
    """

    ledger_name: str = _NOOP_LEDGER_NAME

    async def consumed_for_holder(
        self, holder: BudgetHolder, period: TimeRange, access: AccessScope | None = None
    ) -> SpendResult:
        # Neither holder nor access is applied to answer this way, exactly as neither is in
        # `aggregate_by_dimension`: an unavailable state discloses nothing, so narrowing it could
        # only turn it into a zero - and a zero is the one thing this ledger must never return.
        return SpendResult.no_figure(period)

    async def consumed_for_holders(
        self,
        holders: Sequence[BudgetHolder],
        period: TimeRange,
        access: AccessScope | None = None,
        *,
        report_filter: ReportFilter | None = None,
    ) -> dict[BudgetHolder, SpendResult]:
        # Every holder asked about gets an entry. An absent key would render as a blank rather
        # than as the unavailable state, which is the fabricated-nothing this ledger exists to
        # avoid. Neither access nor report_filter is applied, for the reason the single-holder read gives.
        return {holder: SpendResult.no_figure(period) for holder in holders}

    async def aggregate(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        # Neither scope nor resolved_ids is applied - see consumed_for_holder.
        return SpendResult.no_figure(period)

    async def aggregate_by_time_bucket(
        self,
        period: TimeRange,
        width: str,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[tuple[datetime, TrafficClass], SpendResult]:
        # Every bucket gets an entry for every class, for the same reason every holder does: an
        # absent key renders as a blank rather than as the unavailable state. None of resolved_ids
        # or access or report_filter is applied - see consumed_for_holder.
        return {
            (bucket, traffic_class): SpendResult.no_figure(period)
            for bucket in bucket_starts(period, width)
            for traffic_class in TrafficClass
        }

    async def aggregate_by_traffic_class(
        self,
        period: TimeRange,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> dict[TrafficClass, SpendResult]:
        # Every class gets an entry, for the same reason every bucket does: an absent key renders
        # as a blank rather than as the unavailable state. The resolved_ids argument is unapplied -
        # see consumed_for_holder.
        return {traffic_class: SpendResult.no_figure(period) for traffic_class in TrafficClass}

    async def aggregate_by_spend_group(
        self,
        period: TimeRange,
        *,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[SpendGroup, SpendResult]:
        # Every group gets an entry, for the same reason every class does: an absent key renders as
        # a blank rather than as the unavailable state. Neither access nor report_filter is applied -
        # see consumed_for_holder.
        return {group: SpendResult.no_figure(period) for group in SpendGroup}

    async def aggregate_by_spend_group_per_filter(
        self,
        period: TimeRange,
        *,
        access: AccessScope | None = None,
        common_filter: ReportFilter | None,
        report_filters: Collection[ReportFilter],
    ) -> dict[ReportFilter, dict[SpendGroup, SpendResult]]:
        # Every filter gets an entry for every group, for the same reason every group does: an absent
        # key renders as a blank rather than as the unavailable state. Neither access nor common_filter
        # is applied - see consumed_for_holder.
        return {
            report_filter: {group: SpendResult.no_figure(period) for group in SpendGroup}
            for report_filter in dict.fromkeys(report_filters)
        }

    async def aggregate_by_dimension(
        self,
        period: TimeRange,
        dimension: SpendDimension,  # NOSONAR: S1172 - Unused method parameter
        values: Sequence[str],
        ids_by_value: dict[str, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[str, SpendResult]:
        # Every requested value gets an entry, for the same reason every class does: an absent key
        # renders as a blank rather than as the unavailable state, and here it would drop a whole row
        # from the caller's table. The access boundary needs no application to answer this way: an
        # unavailable state discloses nothing, and narrowing it could only turn it into a zero.
        # Neither dimension, ids_by_value nor report_filter is applied either.
        return {value: SpendResult.no_figure(period) for value in dict.fromkeys(values)}

    async def aggregate_composed(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        traffic_class_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        # None of scope or dimension_ids or traffic_class_ids is applied - see consumed_for_holder.
        return SpendResult.no_figure(period)

    async def aggregate_composed_by_traffic_class(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        ids_by_class: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> dict[TrafficClass, SpendResult]:
        # Every class gets an entry, for the same reason every holder does: an absent key renders as
        # a blank rather than as the unavailable state. None of scope or dimension_ids or
        # ids_by_class is applied - see consumed_for_holder.
        return {traffic_class: SpendResult.no_figure(period) for traffic_class in TrafficClass}

    async def cost_for_request(self, *, run_id: str | None = None, request_id: str | None = None) -> RequestCost:
        # Not `pending`: pending promises an answer shortly and drives a retry, and there is no
        # ledger here that will ever respond to one - so neither run_id nor request_id is looked up.
        return RequestCost.unavailable()

    async def request_count(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> int:
        # None of period or scope or resolved_ids is applied: with no ledger there is nothing to count.
        return 0


_NOOP_LEDGER: SpendLedger = _NoopSpendLedger()  # type: ignore[assignment]
