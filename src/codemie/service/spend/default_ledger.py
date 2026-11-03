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

"""Today's non-LiteLLM behaviour, moved behind the port rather than rewritten.

This adapter exists so the rest of the platform can speak one spend vocabulary without knowing which
provider answered. It changes no non-LiteLLM behaviour; what it does is stop callers from having to
know there *is* a non-LiteLLM case.

**The double-count trap, and it is the only place in this design where double counting is reachable
at all.** The metric store carries `money_spent` for LiteLLM-routed traffic **as well as** native
traffic. A deployment can run chat through LiteLLM while embeddings go to native providers, and there
this adapter serves embeddings only. If it summed the metric store unfiltered it would report every
chat call a second time - once from the spend-logs ledger, once from here - and both figures would
look right in isolation. So the adapter is **constructed with the call classes the registry assigned
it** and every query it makes is scoped to the metric names those classes emit. That configuration is
one environment variable away and, per the architecture, the rule *"cannot be validated by
observation, only by test"* - hence the explicit double-count test beside this module.

**What this adapter can and cannot answer, and why the difference is structural.** The metric store
records `user_id`, `user_email` and `project`, and **no budget category at all** - no monitoring
emission writes one. Four of the five budget holders are keyed on category, so for those there is
genuinely no figure here. That is reported as *no figure*, never as zero: a holder whose consumption
cannot be determined and a holder who spent nothing are different facts, and a non-LiteLLM
deployment has no LiteLLM budget enforcement for the first to be compared against anyway.

**There is no coverage floor here.** `F` and `E` are properties of `litellm_spend_logs`; today's
locally-priced answer has neither, so this adapter never withholds a period for a floor reason.

**The one absence that is *not* a floor: an unsettled cost, and it is a guard rather than a fix.**
The monitoring emitters omit `attributes.money_spent` entirely while a cost is unsettled - AD-28, and
`base_monitoring_service` names this adapter as the reason - and an Elasticsearch `sum` silently skips
exactly those documents, so an unguarded sum returns the **settled remainder** of the window. Nothing
in the shape of the number says so. `_money_figure` therefore measures coverage in the same
aggregation and reports *no figure* where no document settled a cost, rather than the zero a bare sum
returns.

**That state is unreachable in every current configuration, and the guard is here for when it stops
being.** Each source of an unsettled cost is gated on `uses_litellm` for its own call class -
`tokens_callback._calculate_cost` on `CHAT`, `core.utils` and `datasource_monitoring_callback` on
`EMBEDDINGS` - while `composition.register_spend_ledgers` gives this adapter only the classes
`uses_litellm` is **False** for, and `money_spent_unavailable` is set nowhere in the source at all.
So for whatever class this adapter serves, every document it reads carries money today, the unsettled
count is zero, and every figure is exactly the figure it was before. The hole opens the moment
`money_spent_unavailable` is wired to a readiness read - `request_summary_manager` has it as an open
question - or an emitter gains a `None` cost path not gated on routing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from decimal import Decimal

from codemie.configs import logger
from codemie.service.analytics.metric_names import MetricName
from codemie.service.monitoring.metrics_constants import MetricsAttributes
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.identity_bridge import identity_bridge_portion
from codemie.service.spend.ledger import (
    BUCKET_ORIGIN,
    HOLDER_BATCH_SIZE,
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
    bucket_origin,
    bucket_starts,
    bucket_step,
)
from codemie.service.spend.routing import LLMCallClass
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.traffic_class import TrafficClass

DEFAULT_LEDGER_NAME = "default"

MONEY_FIELD = f"attributes.{MetricsAttributes.MONEY_SPENT}"
_USER_ID_FIELD = f"attributes.{MetricsAttributes.USER_ID}.keyword"
_USER_EMAIL_FIELD = f"attributes.{MetricsAttributes.USER_EMAIL}.keyword"

#: The metric-name field every read here is bounded by, named rather than repeated because a second
#: spelling of it cannot be noticed: the CLI class filter narrows the very field `_query` bounds on,
#: and two strings that differ AND into nothing and report a measured `$0.00` for money sitting there.
_METRIC_NAME_FIELD = "metric_name.keyword"

#: What selects `TrafficClass.CLI` **in the metric store** - the one traffic class it has a word for.
#:
#: `traffic_class.py` selects that class in the ledger by `attr_category = 'cli'`; this is the same
#: distinction as the *store* records it - the flag `llm_proxy_monitoring_service` writes on the proxy
#: metric from the CLI header, on every proxied call. **It is also the exact predicate every
#: pre-cutover CLI surface filtered on** - the analytics user, project, summary, engagement and CLI
#: handlers, and the leaderboard's own `cli_spend` aggregation - so a pre-floor figure built from it
#: reproduces the number production prints instead of deriving a second one beside it.
#:
#: A literal because the emitter writes a literal key: `MetricsAttributes` has no member for it, and
#: the six surfaces above each spell this same string for that reason.
_CLI_REQUEST_FIELD = "attributes.cli_request"

#: The money sum and the two counts that qualify it, named once so a builder and its reader cannot
#: drift. **The strings are the ones `analytics.handlers.money_coverage` already uses**, so one
#: Elasticsearch response reads the same whichever surface asked for it.
#:
#: **Spelled here rather than imported from there, exactly as `_PROJECT_FIELD` below is**: that module
#: sits in the analytics *handler* package, whose `__init__` imports `spend_service` - this port's own
#: consumer - so importing it would invert the dependency the port keeps out. A test asserts the two
#: spellings agree so they cannot drift.
_MONEY_AGG = "money"
_SETTLED_AGG = "money_settled"
_UNSETTLED_AGG = "money_unsettled"

#: What the bucketed read names its date histogram in the body. Named for what a reader debugging a
#: query body needs to see, exactly as `_sum_money_per_bucket`'s `group` argument is.
_TIME_BUCKET_AGG = "time_buckets"

#: The project attribute the access boundary is rendered against.
#:
#: **These two are the same strings `query_builder.PROJECT_FIELD` and `USER_ID_FIELD` spell, and they
#: are derived here rather than imported from there deliberately.** The port keeps no import of the
#: analytics layer - `AccessScope.for_context` is named for a `ProjectAccessContext` precisely so it
#: need not import one - and `query_builder` reaches into `rest_api.security` for a `User`, which
#: would invert the dependency between a consumer and the port it reads through. `MetricsAttributes`
#: is the one source both spellings come from, and a test asserts the two agree so they cannot drift.
_PROJECT_FIELD = f"attributes.{MetricsAttributes.PROJECT}.keyword"

#: Which metric names carry each call class's money. **This mapping is the double-count guard**, so
#: it is one table read in one place rather than a filter assembled per query.
#:
#: `CLI_LLM_USAGE_TOTAL` ("codemie_litellm_proxy_usage") is deliberately **absent**: it is
#: LiteLLM-routed traffic by definition and belongs to the spend-logs ledger. Summing it here is
#: precisely the double count this module's docstring describes.
_MONEY_METRICS: dict[LLMCallClass, frozenset[str]] = {
    LLMCallClass.CHAT: frozenset(
        {
            MetricName.CONVERSATION_ASSISTANT_USAGE.value,
            MetricName.WORKFLOW_EXECUTION_TOTAL.value,
            MetricName.AGENT_EXECUTION_TOTAL.value,
        }
    ),
    LLMCallClass.EMBEDDINGS: frozenset({MetricName.DATASOURCE_TOKENS_USAGE.value}),
}

#: The metric-store attribute that identifies each id-keyed dimension, where one exists. Absent
#: entries have no attribute to filter on, so a scope naming one cannot be answered here.
#:
#: **`SpendDimension.MODEL` is deliberately not an entry**: no single field identifies it, and the
#: partition that does is `_model_filter` below.
#:
#: **`project` stays out although `_PROJECT_FIELD` above exists.** That attribute is the access
#: boundary's, not a dimension key: it reaches a document only when the emitting call site's
#: `base_attributes` carried one, so a project-keyed sum built on it comes back silently narrower
#: than the question asked - `_composed_filters` works the same trap through for the term in its
#: other spelling. The missing emitter guarantee is the whole of the reason: a grouped scope reaches
#: no wider figure either way, because `_dimension_filter` narrows a valueless scope by `exists`.
_DIMENSION_FIELD: dict[SpendDimension, str] = {
    SpendDimension.ASSISTANT: f"attributes.{MetricsAttributes.ASSISTANT_ID}.keyword",
    SpendDimension.CONVERSATION: f"attributes.{MetricsAttributes.CONVERSATION_ID}.keyword",
    SpendDimension.WORKFLOW: f"attributes.{MetricsAttributes.WORKFLOW_ID}.keyword",
    SpendDimension.EXECUTION: f"attributes.{MetricsAttributes.EXECUTION_ID}.keyword",
}

#: The two attributes `SpendDimension.MODEL` is partitioned across.
#:
#: The dimension means *the model a call was billed under* - the ledger's `model_group`, one key per
#: row - and the store spells that same vocabulary here: `codemie-text-embedding-ada-002` reads the
#: same on both sides, not a qualified spelling against an alias, so a per-model figure read here is
#: the dimension the ledger groups on rather than a second one wearing its name.
#:
#: **`embeddings_model` alone fabricated a zero for every chat model.** Only datasource documents
#: carry it, while every money-bearing document carries `llm_model` - so a chat model name asked of
#: this adapter matched nothing and came back as an exact, measured `$0.00` over money sitting one
#: field away, which is the one thing this design never writes down.
#:
#: **A plain OR across the two double-counts, which is why this is a partition.** The per-model
#: datasource emitter writes both on one document - `llm_model` for the summarising LLM,
#: `embeddings_model` for the index model - so an OR keys that document under two model names and a
#: table summing its rows reports its money twice. A document is therefore keyed by `llm_model`
#: wherever it carries one, and by `embeddings_model` only where it carries none, which is the
#: aggregate datasource emission and nothing else. The two datasource emitters are alternatives -
#: per-model where the indexing captured LLM runs, aggregate otherwise - so no indexing writes both
#: shapes and no document falls outside the partition.
_LLM_MODEL_FIELD = f"attributes.{MetricsAttributes.LLM_MODEL}.keyword"
_EMBEDDINGS_MODEL_FIELD = f"attributes.{MetricsAttributes.EMBEDDINGS_MODEL}.keyword"


def _model_filter(value: str | None) -> dict:
    """One model name - or any model at all - as the partition described beside `_LLM_MODEL_FIELD`.

    A single clause rather than a list, so it composes into a `filter` `AND` exactly as an id term
    does: its `should` stays parenthesised inside the conjunction, which is the one thing a
    hand-flattened copy gets wrong.
    """
    if value is None:
        return {
            "bool": {
                "should": [
                    {"exists": {"field": _LLM_MODEL_FIELD}},
                    {"exists": {"field": _EMBEDDINGS_MODEL_FIELD}},
                ],
                "minimum_should_match": 1,
            }
        }
    return {
        "bool": {
            "should": [
                {"term": {_LLM_MODEL_FIELD: value}},
                {
                    "bool": {
                        "must_not": [{"exists": {"field": _LLM_MODEL_FIELD}}],
                        "filter": [{"term": {_EMBEDDINGS_MODEL_FIELD: value}}],
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    }


def _dimension_filter(dimension: SpendDimension, value: str | None) -> dict | None:
    """A dimension's scope as one query clause, or `None` where no attribute identifies it.

    **A grouped scope - no value - narrows by `exists`, never by nothing.** An empty filter list
    would answer *group by X, no particular X* with this adapter's whole total, presented as exact;
    `dimension_scope._scope_filter` makes the same choice for the id resolver's own grouped lookup,
    and it is the Elasticsearch mirror of the ledger branch's bare `column.is_not(None)`.

    One builder for the four reads that ask - a single-scope total, a count, a grouped table and a
    composed term - so none of them can come to filter the same dimension differently.
    """
    if dimension is SpendDimension.MODEL:
        return _model_filter(value)
    field = _DIMENSION_FIELD.get(dimension)
    if field is None:
        return None
    return {"exists": {"field": field}} if value is None else {"term": {field: value}}


def _money_aggs() -> dict:
    """The money sum and the two counts that let it say whether it is whole.

    `value_count` counts the documents that **carry** the attribute, which is the whole of the
    measurement: it separates *no document recorded a cost* from *documents recorded a cost of `0.0`*,
    and a free call really is a measured zero. `sum == 0` cannot tell those apart and is never the test.

    The unsettled bucket counts the documents in scope that carry **no** money attribute. It needs no
    `cost_bearing` narrowing - which is what `money_coverage_aggs` takes one for - because `_query`
    already restricts every read here to `_metric_names()`: the session, webhook and tool documents
    that never carry money are outside the query, so they cannot be counted as unsettled and brand
    every window a subtotal.
    """
    return {
        _MONEY_AGG: {"sum": {"field": MONEY_FIELD}},
        _SETTLED_AGG: {"value_count": {"field": MONEY_FIELD}},
        _UNSETTLED_AGG: {"filter": {"bool": {"must_not": [{"exists": {"field": MONEY_FIELD}}]}}},
    }


def _money_figure(bucket: dict) -> Decimal | None:
    """One money bucket's sum, or `None` when nothing in it settled a cost.

    Three readings, and only the middle one is new:

    - **no unsettled document** - the sum is whole. A bucket the store holds no documents for is a
      **measured zero** on this footing: there is no coverage floor here, no call was recorded, and
      nothing was spent. So is a bucket whose documents all recorded `0.0`.
    - **unsettled documents and none settled** - *no figure*. The `sum` skipped every document, so the
      `0.0` it returns is not a measurement of anything; published it would claim nothing was spent.
    - **both** - a **subtotal**. Returned as a figure, because the settled money is real and
      withholding it would be the larger error - but it reads as exact, and `SpendResult` has no
      vocabulary for "short by an unknown amount in no dimension" to say otherwise. Unreachable in
      every current configuration (see the module docstring); recorded here rather than left silent.
    """
    # Through str(): binding a float straight into Decimal would carry the float's own representation
    # error into a money figure.
    total = Decimal(str(bucket.get(_MONEY_AGG, {}).get("value") or 0))
    unsettled = bucket.get(_UNSETTLED_AGG, {}).get("doc_count") or 0
    settled = bucket.get(_SETTLED_AGG, {}).get("value") or 0
    if unsettled and not settled:
        return None
    return total


def _epoch_millis(moment: datetime) -> int:
    """One instant as Elasticsearch bounds a date histogram: milliseconds since the epoch."""
    return int(moment.timestamp() * 1000)


def _bucket_start(bucket: dict) -> datetime:
    """A histogram bucket's key as the UTC instant the walk names it by.

    Read from `key` - epoch milliseconds - and never from `key_as_string`, whose spelling depends on
    the field's own date format: a key parsed from a format the mapping changed would match none of
    the walked starts, and every bucket would then read as a measured zero while its money is dropped.
    """
    return datetime.fromtimestamp(bucket["key"] / 1000, tz=timezone.utc)


def _money_result(
    figure: Decimal | None, period: TimeRange, partial: dict[SpendDimension, Portion] | None = None
) -> SpendResult:
    """A money read's answer in the one shape `SpendResult`'s own validator allows for it.

    An absent figure admits no portion - that is the type's rule, not a convenience here - so a label
    the caller assembled for a figure that turned out not to exist is dropped rather than raised on:
    there is nothing left for the identity bridge to qualify, and the whole period is already reported
    absent.
    """
    if figure is None:
        return SpendResult.no_figure(period)
    return SpendResult(figure=figure, partial=partial or {})


class DefaultSpendLedger:
    """The spend ledger for every call class not routed through LiteLLM.

    **`metric_names` overrides the class-derived name set, and the one caller allowed to reach for it
    is the pre-floor legacy adapter.** The class-derived set is the double-count guard: it excludes
    `CLI_LLM_USAGE_TOTAL` because that traffic is LiteLLM-routed and the spend-logs ledger already
    reports it, so summing it here would report every CLI call twice. That reasoning holds only where
    both ledgers answer for the same window. **Below the coverage floor `F` there are no
    `litellm_spend_logs` rows at all** - that is what the floor *means* - so nothing else reports that
    traffic, the exclusion drops every pre-cutoff CLI cost instead of guarding anything, and an
    instance restricted to the composed period `[start, F)` can widen its name set with nothing left to
    double count.

    **So the override is not a softening of the guard; it is the guard evaluated against a window the
    other ledger does not cover.** Its precondition is that window, not a preference: an instance
    constructed with an override and then asked about a period reaching at or above `F` double-counts
    exactly as the unscoped instance the `ValueError` below refuses would. A caller reaching for it
    must own the clipping that keeps its period wholly below `F`; `composition` does, and is the reason
    this parameter exists. Anything else wanting a wider set wants a different ledger.
    """

    ledger_name: str = DEFAULT_LEDGER_NAME

    def __init__(
        self,
        call_classes: Iterable[LLMCallClass],
        repository=None,
        *,
        metric_names: frozenset[str] | None = None,
    ) -> None:
        """
        Args:
            call_classes: the classes the registry assigned this adapter. **Required, and the
                double-count guard**: every query is restricted to the metric names these emit, so
                an adapter serving embeddings in a mixed deployment cannot reach chat money.
            repository: metric-store access; the shared instance when omitted.
            metric_names: the exact metric names to read, **replacing** the class-derived set rather
                than adding to it. Omitted - the default - is every existing caller, and for them the
                guard is unchanged down to the query body. Passed only by the pre-floor legacy
                adapter, on the precondition the class docstring states: its period lies wholly below
                `F`, where no spend-logs row exists for the extra names to be counted a second time.
                `call_classes` stays required and stays validated even so, because it is what the
                registry routes on - the override changes what is read, never who reads.
        """
        self._call_classes = frozenset(call_classes)
        self._metric_names_override = metric_names
        if not self._call_classes:
            raise ValueError(
                "DefaultSpendLedger needs the call classes it serves: an unscoped instance would sum "
                "LiteLLM-routed traffic as well as native traffic and double-count every call"
            )
        if repository is None:
            from codemie.repository.metrics_elastic_repository import MetricsElasticRepository

            repository = MetricsElasticRepository()
        self._repository = repository

    # -- the port ---------------------------------------------------------------

    async def consumed_for_holder(
        self, holder: BudgetHolder, period: TimeRange, access: AccessScope | None = None
    ) -> SpendResult:
        """What a holder consumed, for the holders this store can identify.

        **A category-keyed holder gets no figure, and that is a fact about the store rather than a
        limitation of this code**: no emission writes a budget category, so there is nothing to
        filter on. Returning a sum ignoring the category would report the person's *whole* spend as
        their spend in one category - a wrong number rather than an absent one.

        **`access` goes into the query, because this adapter has no other place for it to reach.**
        It is the very `bool` `SecureQueryBuilder` injects into the analytics query beside this one,
        rendered by `AccessScope` rather than re-spelled here. It reaches only the branch that
        actually queries: a category-keyed holder is answered without one, and an absent figure
        discloses nothing to narrow.

        **`None` - the default - is a read with no caller and narrows nothing**, which is what a
        budget or scheduled path passes. A budget's consumption may not be narrowed by whoever is
        looking, or a person's spend escapes their own limit as soon as the reader cannot see the
        project it landed in.
        """
        match holder:
            case PersonTotalHolder():
                figure = await self._sum_money(
                    period, self._person_filters(holder.user_id, holder.aliases), scoped_by=access
                )
                partial: dict[SpendDimension, Portion] = {}
                if holder.aliases is None:
                    # The person's fallback rows cannot be identified, so the missing amount is not
                    # computable. A partial entry, never an unavailable flag: the period is covered.
                    partial[SpendDimension.IDENTITY_BRIDGE] = identity_bridge_portion()
                return _money_result(figure, period, partial)

            case PersonalHolder() | ProjectHolder() | ProjectMemberHolder() | ProjectGroupHolder():
                logger.debug(
                    f"spend_default_ledger: {type(holder).__name__} is keyed on a budget category, "
                    "which the metric store does not record; reporting no figure rather than a "
                    "category-blind total"
                )
                return SpendResult.no_figure(period)

        raise TypeError(f"unknown budget holder: {type(holder).__name__}")

    async def consumed_for_holders(
        self,
        holders: Sequence[BudgetHolder],
        period: TimeRange,
        access: AccessScope | None = None,
        *,
        report_filter: ReportFilter | None = None,
    ) -> dict[BudgetHolder, SpendResult]:
        """Per-holder figures in one metric-store round trip.

        **The store's `filters` aggregation is the exact analogue of SQL's `FILTER`**: one named
        bucket per holder, each carrying that holder's own query, all summed in a single pass. So
        the batched figure is built from the *same* predicate as the single-holder figure - the one
        `_person_filters` returns - and cannot drift from it. Re-shaping it as a `terms` aggregation
        on the user-id field would have been cheaper and wrong: a person is matched by canonical id
        **or** by any alias, and a terms bucket keyed on one field cannot express that union, so
        every figure for a person whose rows carry the email would silently read zero.

        The category-keyed holders are answered without a query at all, for the reason
        `consumed_for_holder` states: this store records no budget category.

        **`access` narrows the outer query and therefore every bucket at once**, which is the only
        shape a caller's boundary can take here: it is a property of the caller and not of a holder,
        so one clause in the shared filter cannot be applied to some holders and forgotten on others.
        `None` - the default - is a read with no caller, for the reason `consumed_for_holder` gives.
        `report_filter` narrows the same outer query through `_report_filters`, exactly as the summary
        cards are narrowed, and its `None` default is the budget answer the port explains.
        """
        results: dict[BudgetHolder, SpendResult] = {}
        answerable: list[PersonTotalHolder] = []
        for holder in holders:
            if isinstance(holder, PersonTotalHolder):
                answerable.append(holder)
            else:
                results[holder] = SpendResult.no_figure(period)

        for start in range(0, len(answerable), HOLDER_BATCH_SIZE):
            chunk = answerable[start : start + HOLDER_BATCH_SIZE]
            money_by_key = await self._sum_money_per_bucket(
                period,
                {
                    f"holder_{index}": self._person_filters(holder.user_id, holder.aliases)
                    for index, holder in enumerate(chunk)
                },
                group="holders",
                scoped_by=access,
                filters=self._report_filters(report_filter),
            )
            for index, holder in enumerate(chunk):
                partial: dict[SpendDimension, Portion] = {}
                if holder.aliases is None:
                    partial[SpendDimension.IDENTITY_BRIDGE] = identity_bridge_portion()
                results[holder] = _money_result(money_by_key[f"holder_{index}"], period, partial)

        return results

    async def aggregate(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        """A report's total, optionally within one dimension's scope.

        The resolved_ids argument is accepted for protocol compatibility and **not used**: it exists so a
        ledger holding a native dimension column can be told which of its column-less rows the
        metric store resolved. Here every row *is* a metric-store row, so the dimension is filtered
        directly on its own attribute and there is no second branch to reconcile. (The bare, unquoted
        spelling of the name above is what keeps Sonar's python:S1172 quiet for a protocol-required
        argument - see `_NoopSpendLedger`.)
        """
        if scope is None:
            return _money_result(await self._sum_money(period, []), period)

        dimension_filter = _dimension_filter(scope.dimension, scope.value)
        if dimension_filter is None:
            # The datasource dimension has no id attribute on any emission, and the project and
            # identity-bridge dimensions are not scopes this store can be asked about.
            logger.debug(
                f"spend_default_ledger: no metric-store attribute identifies the "
                f"{scope.dimension.value} dimension; reporting no figure"
            )
            return SpendResult.no_figure(period)

        return _money_result(await self._sum_money(period, [dimension_filter]), period)

    async def aggregate_by_time_bucket(
        self,
        period: TimeRange,
        width: str,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[tuple[datetime, TrafficClass], SpendResult]:
        """Money per (time bucket, traffic class) - **the CLI series, and honest silence elsewhere.**

        **Three of the four classes have no word in this store, and every bucket of theirs is
        absent.** The plausible substitute - grouping by metric name - answers a *different*
        question, because the names a chart happens to draw are not the predicates the ledger
        attributes rows by, and it would answer it confidently with figures disagreeing with every
        other surface for the same week.

        **CLI is the exception, and a consumer now makes this split.** `_CLI_REQUEST_FIELD` is the
        one class word this store does have - the flag the proxy emitter writes, and the very
        predicate every pre-cutover CLI surface filtered its money on - so below the coverage floor
        the weekly chart draws the CLI series from the aggregation those surfaces already performed
        and leaves the other three flat. A short line with three absences beside it is what this
        store can support; four lines derived from metric names would be four confident wrong ones.

        **One date histogram answers every bucket, binned from the stride and origin the walk
        steps.** `fixed_interval` and `offset` come from `bucket_step` and `bucket_origin` rather
        than being spelled here, for the same reason the SQL side binds `date_bin` to those two
        values: an origin computed twice can part company, two origins share no bucket key at all,
        and every figure would then read as a measured zero while real money is silently dropped -
        the one failure on this path that nothing raises on. `min_doc_count: 0` over
        `extended_bounds` spanning the **walk** makes the store return the empty buckets too, so a
        week nothing was spent in arrives as the measured zero it is rather than as a gap, which a
        chart draws as *no data* where the truth is *no spend*.

        **Where the CLI money is not this adapter's to report, nothing is read at all.**
        `_cli_class_filters` refuses when the proxy metric is outside the name set, and the whole
        mapping is absent rather than carrying a measured `$0.00` over documents this adapter looks
        away from.

        `access` and `report_filter` both narrow the outer query, once for every bucket: the
        boundary as the parenthesised `bool` `AccessScope` renders, the filter as the `terms` clauses
        the dashboard asked for. `resolved_ids` is accepted for protocol compatibility and **not
        used** - every row here is a metric-store row, and the class those ids resolve is not one
        this store can express at any grain.
        """
        _ = resolved_ids
        buckets = bucket_starts(period, width)
        cli_filters = self._cli_class_filters()
        if cli_filters is None:
            # Logged once for the whole read, like the holder branch above: one reason per call, not
            # one per bucket, or a year of weeks writes fifty-two identical lines.
            logger.debug(
                "spend_default_ledger: the metric store records no traffic class, so a bucketed split "
                "cannot be built here; reporting no figure for every bucket rather than a metric-name "
                "grouping that answers a different question - and the one class it does have a word "
                "for, CLI, rides on a metric outside this adapter's name set"
            )
            return {
                (bucket, traffic_class): SpendResult.no_figure(period)
                for bucket in buckets
                for traffic_class in TrafficClass
            }

        money = await self._sum_money_per_time_bucket(
            period,
            width,
            [*cli_filters, *self._report_filters(report_filter)],
            buckets=buckets,
            scoped_by=access,
        )

        # A key the histogram returned that the walk did not produce is money this mapping cannot
        # place, and only a stride or origin read from two tables can put one there - which they no
        # longer are. Said out loud, because dropping it is otherwise silent and reads downstream as
        # a bucket that held nothing.
        unplaceable = set(money) - set(buckets)
        if unplaceable:
            logger.warning(
                f"spend_default_ledger: {len(unplaceable)} {width} bucket(s) the metric store grouped do not "
                f"align with the walked range and their money is not reported: {sorted(unplaceable)}"
            )

        return {
            (bucket, traffic_class): (
                # A bucket the histogram did not return is a **measured zero**: `extended_bounds` is
                # the walk's own first and last start, so the store was asked for every one of these
                # keys and answered with the ones it found documents in. A key it returned holding
                # only unsettled documents is the different case and is already `None` here.
                _money_result(money.get(bucket, Decimal("0")), period)
                if traffic_class is TrafficClass.CLI
                else SpendResult.no_figure(period)
            )
            for bucket in buckets
            for traffic_class in TrafficClass
        }

    async def aggregate_by_spend_group(
        self,
        period: TimeRange,
        *,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[SpendGroup, SpendResult]:
        """The four summary figures over the whole range - **each card's own aggregation, reproduced.**

        The class split this store cannot express is not the split the cards make: theirs *is* the
        metric name, which is the one vocabulary it has. So each group here is the pre-cutover card's
        Elasticsearch filter performed again rather than a figure derived beside it - the total over
        this adapter's name set, platform over `PLATFORM_METRICS`, CLI over the proxy metric with the
        CLI flag, embedding over the datasource metric - and a window below the floor prints what
        production printed. `spend_group` says why these four are not a partition and why no consumer
        may subtract one from another.

        **The total is every document in this adapter's name set, and emphatically not a `match_all`
        over the index.** That name set is the double-count guard: a total spanning the index would
        sum the LiteLLM-routed traffic the spend-logs ledger already reports, which is this module's
        whole reason for existing. Today's card is a `match_all`, so what this total leaves out
        beside it is the three cost-bearing metrics that are in `MetricName` not at all -
        `codemie_workflow_generator_total`, `codemie_workflow_ai_refine_total` and
        `codemie_conversation_analysis_total`, each emitted with money the card counts and no name
        set can name. Left out deliberately: the guard is spelled as a name set, and a metric with no
        name in it is a metric this adapter does not read.

        **The four figures do not foot below the floor, and they never did.** The pre-floor name set
        is `legacy_metric_names` - the surfaces' own `SPENDING_METRICS` unioned with the port's money
        table - and that union carries `agent_execution_total`, a money metric in no metric group at
        all. So `TOTAL` exceeds `PLATFORM` plus `CLI` by that metric's money - and by the proxy
        metric's non-CLI documents, which the name set carries and the CLI card's flag excludes - and
        the remainder is a residue rather than an error: `spend_group` says why these four are not a
        partition and why no consumer may subtract one from another.

        **A group this adapter's call classes cannot carry is omitted from the mapping, never
        answered absent.** An embeddings-only adapter reports no CLI key whatsoever: CLI traffic is
        chat traffic, it was never this adapter's to report, and flagging the card incomplete over it
        would put a qualification on a figure the chat adapter answered in full. **A group its
        classes do carry but it cannot read is a different fact and is answered absent** - CLI where
        the proxy metric is outside the name set, embedding where the datasource metric is - which is
        precisely the fabricated `$0.00` `_cli_class_filters` and `_embedding_class_filters` exist to
        refuse.

        One round trip answers all of them, so the cards on one screen foot against each other by
        construction. `access` and `report_filter` mean what they mean on `aggregate_by_time_bucket`
        and narrow the outer query once for every group at once: a boundary is a property of the
        caller and a filter of the question, neither of one card.
        """
        cli_filters = self._cli_class_filters()
        embedding_filters = self._embedding_class_filters()
        carries_cli = LLMCallClass.CHAT in self._call_classes
        carries_embeddings = LLMCallClass.EMBEDDINGS in self._call_classes

        selectors: dict[SpendGroup, list[dict]] = {
            # No clause at all: `_query` already bounds every read here by the name set, and that
            # bound is the guard - see the docstring on why this is not a `match_all`.
            SpendGroup.TOTAL: [],
            SpendGroup.PLATFORM: [
                {"terms": {_METRIC_NAME_FIELD: MetricName.to_list_from_group(MetricName.PLATFORM_METRICS)}}
            ],
        }
        if carries_cli and cli_filters is not None:
            selectors[SpendGroup.CLI] = cli_filters
        if carries_embeddings and embedding_filters is not None:
            selectors[SpendGroup.EMBEDDING] = embedding_filters

        money = await self._sum_money_per_bucket(
            period,
            {group.value: terms for group, terms in selectors.items()},
            group="spend_groups",
            scoped_by=access,
            filters=self._report_filters(report_filter),
        )
        # Indexed rather than `.get`-with-a-zero, like every read here: the helper answers for
        # exactly the keys it was given, so a missing one is a defect there.
        answered = {group: _money_result(money[group.value], period) for group in selectors}
        if carries_cli and cli_filters is None:
            logger.debug(
                "spend_default_ledger: the CLI proxy metric is outside this adapter's name set, so the "
                "CLI card cannot be read here; reporting no figure rather than a zero over documents "
                "this adapter is not allowed to see"
            )
            answered[SpendGroup.CLI] = SpendResult.no_figure(period)
        if carries_embeddings and embedding_filters is None:
            logger.debug(
                "spend_default_ledger: the datasource metric is outside this adapter's name set, so the "
                "embedding card cannot be read here; reporting no figure rather than a zero over documents "
                "this adapter is not allowed to see"
            )
            answered[SpendGroup.EMBEDDING] = SpendResult.no_figure(period)
        # Enumerated in the declared order so the mapping reads as the screen does, and so an omitted
        # group is visibly omitted rather than reordered.
        return {group: answered[group] for group in SpendGroup if group in answered}

    async def aggregate_by_traffic_class(
        self,
        period: TimeRange,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> dict[TrafficClass, SpendResult]:
        """Money per traffic class - **which this store cannot split either.**

        Dropping the time grouping changes nothing about why: the class is the part this store has no
        vocabulary for, not the grain. Grouping by metric name is the plausible substitute here as
        well, and it answers a different question with figures that disagree with every other
        surface for the same window. The CLI series `aggregate_by_time_bucket` draws is not read here
        either: no consumer makes this platform-wide split below the floor, and a series with no
        chart to draw it is a query paid for and discarded.
        """
        _ = resolved_ids
        logger.debug(
            "spend_default_ledger: the metric store records no traffic class, so a class split "
            "cannot be built here; reporting no figure for every class rather than a metric-name "
            "grouping that answers a different question"
        )
        return {traffic_class: SpendResult.no_figure(period) for traffic_class in TrafficClass}

    async def aggregate_by_dimension(
        self,
        period: TimeRange,
        dimension: SpendDimension,
        values: Sequence[str],
        ids_by_value: dict[str, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[str, SpendResult]:
        """One figure per named dimension value, in one round trip.

        **`aggregate`'s money rule asked once per value, never a second rule.** Each value gets its own
        named bucket in the `filters` aggregation `consumed_for_holders` already uses, over the one
        class-scoped, half-open query every read here shares - so a grouped figure is built from the
        very `_dimension_filter` clause a single-scope figure is built from and cannot drift from it. One
        query answers a whole table; one per value would be a thousand round trips, and a surface that
        slow acquires a cache, which is a second source of spend truth.

        **A dimension with no attribute gets no figure per value, and never a zero**, for the reason
        `aggregate` gives about the same absence: a zero would be a positive claim that each of those
        values cost nothing. That is `datasource`, whose id no emission carries at all, `project` and
        `identity_bridge`, which are not scopes this store can be asked about, and `traffic_class`,
        which this store has no word for at any grain. `model` is **not** among them although
        `_DIMENSION_FIELD` names no field for it: it is answered through the two-attribute partition
        `_dimension_filter` builds.

        `ids_by_value` is accepted for protocol compatibility and **not used**, exactly as `aggregate`
        does not use `resolved_ids`: those ids were resolved *from this very store*, and every row here
        is one of its rows, so the dimension is filtered directly on its own attribute and there is no
        column-less branch for an id set to reconcile. Said out loud because an unused argument
        otherwise reads as one that was forgotten.

        Nothing reaches `partial`: with one branch there is no unresolved remainder for a residue to
        name, which is why `aggregate` produces none for a scope either.

        **`access` is applied in the query, and this adapter has no SQL for it to reach any other
        way.** It is the very `bool` `SecureQueryBuilder` injects into the analytics query beside
        this one, rendered by `AccessScope` rather than re-spelled here, and it goes into the shared
        outer filter list - an `AND` - so the boundary's own `OR` cannot leak past its parentheses.
        A **restricted** scope with no accessible project matches nothing rather than everything,
        which is `AccessScope`'s rule and not this adapter's to soften.

        **No grouping field here is the field the boundary is rendered against**, which is why the
        two never need reconciling: the boundary is one clause of the outer query and each value's
        term is one clause of its own bucket, so they meet as an `AND` across two levels with
        neither applied twice nor dropped. `_DIMENSION_FIELD` keeps `_PROJECT_FIELD` out for reasons
        of its own; that it also keeps this property is worth knowing before an entry is added back.

        `report_filter` joins the same outer filter list through `_report_filters`, exactly as the
        holder batch applies it: what a dashboard asked about qualifies every value's bucket at once.
        """
        _ = ids_by_value
        # An empty request asks nothing, so nothing is read - checked before the attribute lookup so a
        # caller passing no values gets an empty mapping rather than a mapping of nothing to a state.
        requested = list(dict.fromkeys(values))
        if not requested:
            return {}

        # Probed with no value, because whether an attribute identifies a dimension is a property of
        # the dimension and never of the value asked about: one check answers the whole table.
        if _dimension_filter(dimension, None) is None:
            # Logged once for the whole read, like the holder and class branches above: one reason per
            # call, not one per value, or a thousand-row table writes a thousand identical lines.
            logger.debug(
                f"spend_default_ledger: no metric-store attribute identifies the {dimension.value} "
                f"dimension, so none of the {len(requested)} requested value(s) can be filtered on; "
                "reporting no figure for each rather than a zero"
            )
            return {value: SpendResult.no_figure(period) for value in requested}

        # Keyed by position rather than by the value itself, like the holder batch: a value is
        # caller-supplied text, and a bucket name it chose is a caller-chosen key in a query body.
        bucket_key = {value: f"value_{index}" for index, value in enumerate(requested)}
        money_by_key = await self._sum_money_per_bucket(
            period,
            {key: [_dimension_filter(dimension, value)] for value, key in bucket_key.items()},
            group="values",
            scoped_by=access,
            filters=self._report_filters(report_filter),
        )
        return {
            # Indexed rather than `.get`-with-a-zero, like `consumed_for_holders`: the helper answers
            # for exactly the keys it was given, so a missing one is a defect there, and substituting
            # a zero for it would fabricate the one figure this design never fabricates.
            value: _money_result(money_by_key[key], period)
            for value, key in bucket_key.items()
        }

    async def aggregate_composed(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        traffic_class_ids: tuple[str, ...] = (),
    ) -> SpendResult:
        """A total for several filter terms at once, **only where every term is expressible here.**

        **A conjunction answered with the subset of its terms this store can express is not a
        narrower figure - it is a wider one**, and that is the whole reason for the all-or-nothing
        rule below. Dropping the class term from *"this person's CLI spend"* reports the person's
        **entire** spend as their CLI spend: a number that looks right, is far too large, and nothing
        marks. That is item 15's `PersonalHolder(user, CLI)` trap reproduced inside an adapter, so a
        term this store cannot express means *no figure* - never a zero, and never an answer to a
        different question.

        Which terms are expressible is the same per-term judgement `aggregate` and
        `aggregate_by_dimension` already make about a dimension; `_composed_filters` holds it.

        `dimension_ids` and `traffic_class_ids` are accepted for protocol compatibility and **not
        used**, exactly as `resolved_ids` is not: those ids were resolved *from this very store*, and
        every row here is one of its rows, so a term is filtered directly on its own attribute and
        there is no column-less branch for an id set to reconcile.
        """
        _ = (dimension_ids, traffic_class_ids)
        filters = self._composed_filters(scope)
        if filters is None:
            return SpendResult.no_figure(period)

        return _money_result(await self._sum_money(period, filters), period, self._scope_partial(scope))

    async def aggregate_composed_by_traffic_class(
        self,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        ids_by_class: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> dict[TrafficClass, SpendResult]:
        """A composed scope split per traffic class - **CLI, where this adapter may read it.**

        **CLI is the one class the metric store has a word for**, and `_CLI_REQUEST_FIELD` is that
        word: the flag the proxy emitter writes, and the very predicate every pre-cutover CLI surface
        filtered its money on. So this read is not a new derivation of the class - it is the
        aggregation those surfaces already performed, asked through the port. Below the floor that
        matters concretely: the leaderboard renders a person's total beside their CLI share, and an
        absent CLI figure there is a blank cell next to a figure on a page production prints as two
        numbers.

        **The other three classes stay absent, and not for want of a plausible substitute.** Grouping
        by metric name is the substitute, and it answers a different question - the names a chart
        happens to draw are not the predicates the ledger attributes rows by - with figures that
        disagree with every other surface for the same window. `exists workflow_id` is that same trap
        wearing an attribute: the ledger selects that class by a spend-logs column, and a metric-store
        re-derivation of it would be a second definition of one class.

        **Both refusals are all-or-nothing and neither degrades into a number.** `_cli_class_filters`
        refuses where the CLI money is not this adapter's to report, so nothing here publishes a
        measured `$0.00` over documents its name set looks *away* from; `_composed_filters` refuses a
        scope carrying a term this store cannot express, and it takes the CLI figure with it rather
        than answering the person's whole spend as their CLI spend - item 15's trap, which is the
        reason a conjunction is never answered with the subset of its terms.

        **The CLI figure is a share of the scope's total, never an addend to it.** It reads the rows
        `aggregate_composed` reads with two more conjuncts, so a surface showing a total beside a CLI
        figure - which is what the leaderboard does - shows an amount and a part of it. Adding the two
        would count that part twice, and the class split exists to be read the other way.

        **Where two engines split the call classes the figure is qualified rather than wrong.** The
        engine carrying embeddings has no CLI name in its set, so it reports no figure and
        `combine_results` carries that flag onto the combined answer. The amount is the CLI-carrying
        engine's and is right; the flag is what not fabricating the other engine's zero costs, and it
        is the direction of error this design chooses every time.

        `dimension_ids` and `ids_by_class` are accepted for protocol compatibility and **not used**,
        exactly as `aggregate_composed`'s id sets are: those ids were resolved *from this very store*,
        so a term is filtered directly on its own attribute and there is no column-less branch to
        reconcile.
        """
        _ = (dimension_ids, ids_by_class)
        # Every class present from the start, so a class this store cannot express is reported absent
        # rather than being missing from the mapping - a missing key renders as a blank that no state,
        # flag or log accounts for.
        split = {traffic_class: SpendResult.no_figure(period) for traffic_class in TrafficClass}

        # Asked before the gate so `_composed_filters` logs its own reason for refusing a term, which
        # is a different absence from the one below and reads as one in the log.
        scope_filters = self._composed_filters(scope)
        cli_filters = self._cli_class_filters()
        if cli_filters is None:
            # Logged once for the whole read, like every reason this adapter reports: one line per
            # call rather than one per class.
            logger.debug(
                "spend_default_ledger: the CLI proxy metric is outside this adapter's name set, so "
                "its traffic class cannot be read here; reporting no figure rather than a zero over "
                "documents this adapter is not allowed to see"
            )
        if scope_filters is None or cli_filters is None:
            return split

        money = await self._sum_money(period, [*scope_filters, *cli_filters])
        split[TrafficClass.CLI] = _money_result(money, period, self._scope_partial(scope))
        return split

    async def cost_for_request(self, *, run_id: str | None = None, request_id: str | None = None) -> RequestCost:
        """What one unit of work cost - **which this store cannot answer by id.**

        Not `pending`, which promises a figure shortly and drives a re-read: there is no store here
        that will ever answer an id-keyed lookup, so `unavailable` is the truthful state and it
        correctly suppresses hydration. Nothing is lost by that: on this path the cost was computed
        locally at the time of the call and is already on the record, so no record reaches this
        method needing to be filled in.
        """
        _ = (run_id, request_id)
        return RequestCost.unavailable()

    async def request_count(
        self,
        period: TimeRange,
        scope: DimensionScope | None = None,
        resolved_ids: tuple[str, ...] = (),
    ) -> int:
        """How many money-bearing metric documents the scope saw.

        A document per call, so no distinct-id dedupe applies here: unlike the spend-logs table this
        store holds no per-attempt rows for a retry to multiply.
        """
        _ = resolved_ids
        filters: list[dict] = []
        if scope is not None:
            dimension_filter = _dimension_filter(scope.dimension, scope.value)
            if dimension_filter is None:
                return 0
            filters.append(dimension_filter)

        body = {
            "size": 0,
            "query": self._query(period, filters),
            "aggs": {"calls": {"value_count": {"field": MONEY_FIELD}}},
        }
        result = await self._repository.execute_aggregation_query(body)
        return int(result.get("aggregations", {}).get("calls", {}).get("value", 0) or 0)

    # -- internals --------------------------------------------------------------

    def _metric_names(self) -> list[str]:
        """The metric names this adapter is allowed to read. The double-count guard, applied.

        An explicit set passed at construction is returned **instead of** the class-derived one, never
        unioned with it: a union would make the override unable to *narrow*, and the pre-floor adapter
        that carries one asks for a deliberate, fully spelled list. Sorted either way, so the query
        body a test reads back is stable rather than set-iteration order.

        Without an override this is exactly the mapping read it has always been, which is the property
        that matters here: every existing construction takes the same branch as before, so the guard
        cannot be weakened by the mere existence of the parameter - only by a caller passing one, under
        the precondition the class docstring names.
        """
        if self._metric_names_override is not None:
            return sorted(self._metric_names_override)
        names: set[str] = set()
        for call_class in self._call_classes:
            names |= _MONEY_METRICS[call_class]
        return sorted(names)

    def _query(self, period: TimeRange, filters: list[dict], *, scoped_by: AccessScope | None = None) -> dict:
        """The bounded, class-scoped query every read here shares.

        The time bound is **half-open**, like every interval in this design: a closed upper bound
        lands a boundary document in two adjacent period totals.

        `scoped_by` appends the caller's access boundary as one more conjunct of this `filter` list.
        It belongs here rather than at a call site because a `filter` list is an `AND`: nesting the
        boundary's `should` inside it keeps its `OR` parenthesised, which is the one thing a hand-
        assembled copy of the clause gets wrong. An unrestricted scope renders no clause at all -
        `elasticsearch_filter` returns `None` - matching the query builder's own admin path.
        """
        boundary = (
            scoped_by.elasticsearch_filter(user_id_field=_USER_ID_FIELD, project_field=_PROJECT_FIELD)
            if scoped_by is not None
            else None
        )
        return {
            "bool": {
                "filter": [
                    {"terms": {_METRIC_NAME_FIELD: self._metric_names()}},
                    {
                        "range": {
                            "@timestamp": {
                                "gte": period.start.isoformat(),
                                "lt": period.end.isoformat(),
                            }
                        }
                    },
                    *filters,
                    *([boundary] if boundary is not None else []),
                ]
            }
        }

    def _composed_filters(self, scope: ComposedScope) -> list[dict] | None:
        """Every term of a composed scope as a query filter, or `None` if any term cannot be one.

        **`None` for the whole scope rather than a filter list missing a term**, because a missing
        conjunct widens the answer rather than narrowing it - see `aggregate_composed`.

        | Term | Expressible here? |
        | --- | --- |
        | user | **yes** - the very `_person_filters` a person-total holder is answered by |
        | dimension | **where `_dimension_filter` builds one**, the same rule `aggregate` applies |
        | project | **no** - see below |
        | traffic class | **no** - this store has no traffic-class vocabulary at all |

        **The project term is refused although the store does record a project**, and the reason is
        that recording it is not a guarantee. The money-bearing documents this adapter reads come from
        several emitters, and the shared one - `emit_llm_token_metric`, which writes `money_spent` for
        the agent-execution metric - adds no project attribute of its own: whether one arrives depends
        on each call site's `base_attributes`. A project-scoped sum built on that silently omits
        whatever money arrived without one, which is a **narrower** figure presented as the asked-for
        one. Both directions of wrongness are silent, so the rule is the same: no figure.

        **Both spellings of the term land on that rule, and they do so through one table rather than
        two rules.** `_DIMENSION_FIELD` names no project attribute, so `dimension=DimensionScope(
        PROJECT, ...)` is refused by the dimension row below exactly as `project=` is refused here. A
        project entry in that table would part them - one scope answering two ways, absent as a term
        and a figure as a dimension - which is why the table says so where the entry would go.
        """
        filters: list[dict] = []

        if scope.traffic_class is not None:
            logger.debug(
                f"spend_default_ledger: the metric store records no traffic class, so the "
                f"{scope.traffic_class.value} term cannot be expressed; reporting no figure"
            )
            return None
        if scope.project is not None:
            logger.debug(
                "spend_default_ledger: a project attribute is written by each emitter rather than "
                "guaranteed on every money-bearing document, so a project term cannot be relied on; "
                "reporting no figure"
            )
            return None
        if scope.user is not None:
            filters.extend(self._person_filters(scope.user.user_id, scope.user.aliases))
        if scope.dimension is not None:
            dimension_filter = _dimension_filter(scope.dimension.dimension, scope.dimension.value)
            if dimension_filter is None:
                logger.debug(
                    f"spend_default_ledger: no metric-store attribute identifies the "
                    f"{scope.dimension.dimension.value} dimension; reporting no figure"
                )
                return None
            # Whether the term narrows by `term` or by `exists` is `_dimension_filter`'s to decide,
            # and it matters most here: in a conjunction an omitted grouped term would answer *"this
            # person's assistant spend"* with this person's whole spend.
            filters.append(dimension_filter)

        return filters

    @staticmethod
    def _person_filters(user_id: str, aliases: frozenset[str] | None) -> list[dict]:
        """Match a person by canonical id or by any alias the composers can emit for them."""
        should: list[dict] = [{"term": {_USER_ID_FIELD: user_id}}]
        if aliases:
            should.append({"terms": {_USER_EMAIL_FIELD: sorted(aliases)}})
        return [{"bool": {"should": should, "minimum_should_match": 1}}]

    def _cli_class_filters(self) -> list[dict] | None:
        """`TrafficClass.CLI` as query filters, or `None` where this adapter may not read that class.

        **The gate is the double-count guard asked the class question, and it is what keeps the honest
        answer from being a zero.** The CLI flag rides on `CLI_LLM_USAGE_TOTAL`, which `_MONEY_METRICS`
        withholds from every class because that traffic is LiteLLM-routed and the spend-logs ledger
        reports it. An adapter without that name is therefore not looking at an empty CLI bucket, it is
        looking **away** from documents sitting in the very store it reads - so a sum over its own rows
        would come back `0.0` for money one filter away and publish it as an exact, measured `$0.00`.
        That is the fabricated zero this design never writes down, so the class is reported absent.

        **The pre-floor engine is the adapter this opens for, on the precondition that lets it widen
        its name set at all**: below `F` there are no spend-logs rows, nothing else reports that
        traffic, and `legacy_metric_names` puts the CLI metric back. The class is expressible exactly
        where the money is this adapter's to report, which is why the condition is read off the name
        set rather than passed in as a flag - a flag could be set where the guard is not satisfied.

        The metric-name term **narrows rather than decorates**: it is ANDed with the name set `_query`
        already bounds every read by, so the sum reads the CLI metric alone - the two-clause filter the
        pre-cutover surfaces used - rather than every money name that might one day carry the flag.
        """
        if MetricName.CLI_LLM_USAGE_TOTAL.value not in self._metric_names():
            return None
        return [
            {"term": {_METRIC_NAME_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
            {"term": {_CLI_REQUEST_FIELD: True}},
        ]

    def _embedding_class_filters(self) -> list[dict] | None:
        """The embedding card as query filters, or `None` where this adapter may not read that money.

        `_cli_class_filters`' gate asked of the other card whose name is not guaranteed to be in the
        set, and refused for its reason: an adapter whose name set lacks the datasource metric is not
        looking at an empty embedding bucket, it is looking **away** from documents in the very store
        it reads, so a sum over its own rows comes back `0.0` for money one filter away and publishes
        it as an exact, measured `$0.00`.

        Read off the name set rather than off the call classes, because those are two different
        questions: `LLMCallClass.EMBEDDINGS` says the money is this adapter's to report, and the name
        set says whether it can be reached. The first alone decides whether the card is **omitted**;
        the second decides whether an owned card is **absent**.
        """
        if MetricName.DATASOURCE_TOKENS_USAGE.value not in self._metric_names():
            return None
        return [{"term": {_METRIC_NAME_FIELD: MetricName.DATASOURCE_TOKENS_USAGE.value}}]

    @staticmethod
    def _report_filters(report_filter: ReportFilter | None) -> list[dict]:
        """What the dashboard asked about, as query clauses - none where it asked for everything.

        Rendered by `ReportFilter` rather than re-spelled here, for the reason `AccessScope` renders
        its own boundary: one spelling of a `terms` clause per field, so the two whole-window reads
        cannot come to filter on different attributes. **Absent means unconstrained** - the opposite
        of a restricted boundary with no project, which matches nothing - so `None` adds no clause
        rather than an empty `terms` that would match none.
        """
        if report_filter is None:
            return []
        return report_filter.elasticsearch_filters(user_id_field=_USER_ID_FIELD, project_field=_PROJECT_FIELD)

    @staticmethod
    def _scope_partial(scope: ComposedScope) -> dict[SpendDimension, Portion]:
        """The qualification the scope itself carries, wherever the scope is answered.

        Shared by the total and the class split rather than written twice, because it qualifies the
        row set both are drawn from: an unresolvable alias set is the same hole whichever class a row
        landed in. A second copy would be one edit away from labelling only one of the two, and a
        figure that lost its label reads as an exact one.

        A partial entry and never an unavailable flag: the period is covered, it is the person's
        fallback rows that cannot be identified, and their amount is not computable.
        """
        if scope.user is not None and scope.user.aliases is None:
            return {SpendDimension.IDENTITY_BRIDGE: identity_bridge_portion()}
        return {}

    async def _sum_money_per_time_bucket(
        self,
        period: TimeRange,
        width: str,
        filters: list[dict],
        *,
        buckets: tuple[datetime, ...],
        scoped_by: AccessScope | None = None,
    ) -> dict[datetime, Decimal | None]:
        """Sum the money field once per time bucket, in one query - the money a split chart draws.

        **Binned from `bucket_step` and `bucket_origin`, never from a width string handed to
        Elasticsearch.** A calendar interval would let the store choose its own boundaries, and a
        boundary the walk did not choose produces keys the walk cannot place: the money is then
        dropped and every bucket reads as a measured zero, which nothing raises on. The offset is the
        origin's own distance from the epoch, so a week lands on the first Monday of the epoch exactly
        as the walk floors it and as `date_bin` bins it on the other side of the port.

        **`min_doc_count: 0` and `extended_bounds` are what make an empty bucket answerable, and the
        bounds are the walk's own ends rather than the requested range.** Without them the store
        returns only the buckets it found documents in, and the caller cannot tell a week nothing was
        spent in from a week outside the read - so the gap-filling above would be guessing rather
        than reading. The first walked start can lie **before** `period.start` - a week bins a
        Saturday to its Monday - so a range-bounded histogram leaves that bucket outside the bounds
        and the caller's substituted zero is a guess again; it holds today only because the store
        rounds a bound down into the bucket containing it, which is a property of the store rather
        than of this read.

        **`buckets` is the caller's own walk and not a second one computed here**, for the reason the
        interval and the offset are read off `bucket_step` and `bucket_origin`: two walks can part
        company, and the bounds would then be drawn around keys the caller never asks about.

        A bucket holding documents **none of which settled a cost** is `None` - no figure - for
        `_money_figure`'s reason, exactly as in `_sum_money_per_bucket`.
        """
        body = {
            "size": 0,
            "query": self._query(period, filters, scoped_by=scoped_by),
            "aggs": {
                _TIME_BUCKET_AGG: {
                    "date_histogram": {
                        # The very field `_query` bounds the range on: a histogram grouping on a
                        # second date field would bucket by one instant and filter by another.
                        "field": "@timestamp",
                        "fixed_interval": f"{int(bucket_step(width).total_seconds())}s",
                        "offset": f"{int((bucket_origin(width) - BUCKET_ORIGIN).total_seconds())}s",
                        "min_doc_count": 0,
                        "extended_bounds": {
                            "min": _epoch_millis(buckets[0]),
                            "max": _epoch_millis(buckets[-1]),
                        },
                    },
                    "aggs": _money_aggs(),
                }
            },
        }
        result = await self._repository.execute_aggregation_query(body)
        found = result.get("aggregations", {}).get(_TIME_BUCKET_AGG, {}).get("buckets", []) or []
        return {_bucket_start(bucket): _money_figure(bucket) for bucket in found}

    async def _sum_money_per_bucket(
        self,
        period: TimeRange,
        buckets: dict[str, list[dict]],
        *,
        group: str,
        scoped_by: AccessScope | None = None,
        filters: list[dict] | None = None,
    ) -> dict[str, Decimal | None]:
        """Sum the money field once per named bucket, in one query.

        A bucket the store holds no documents for sums to a **measured zero**, on the same footing as
        `_sum_money`'s empty result: there is no coverage floor here, so *no document at all* means
        nothing was recorded. A bucket holding documents **none of which settled a cost** is the
        different case, and it is `None` - no figure - for `_money_figure`'s reason: the sum skipped
        every one of them, so its `0.0` measures nothing.

        `group` names the aggregation in the body and is the caller's to choose, because two
        different reads share this one shape: a dimension table's query labelled `holders` reads back
        as the holder batch to whoever is debugging it, which is the one thing a query body is for.

        `scoped_by` narrows **every** bucket at once, in the outer query rather than per bucket: the
        boundary is a property of the caller and not of a bucket, and one clause in the shared filter
        cannot be applied to some buckets and forgotten on others. `None` is a read with no caller -
        the holder batch - and narrows nothing.

        `filters` narrows every bucket at once for the same reason and in the same place: what a
        dashboard asked about qualifies the whole question, not one of its cards, and a clause applied
        per bucket is one a later bucket can be added without.
        """
        if not buckets:
            return {}
        body = {
            "size": 0,
            "query": self._query(period, filters or [], scoped_by=scoped_by),
            "aggs": {
                group: {
                    "filters": {"filters": {key: {"bool": {"filter": terms}} for key, terms in buckets.items()}},
                    "aggs": _money_aggs(),
                }
            },
        }
        result = await self._repository.execute_aggregation_query(body)
        found = result.get("aggregations", {}).get(group, {}).get("buckets", {}) or {}
        return {key: _money_figure(found.get(key, {})) for key in buckets}

    async def _sum_money(
        self, period: TimeRange, filters: list[dict], *, scoped_by: AccessScope | None = None
    ) -> Decimal | None:
        """Sum the money field over a scope, or `None` where nothing in it settled a cost.

        A scope the store holds **no documents** for sums to a *measured zero*, which is the truthful
        answer here: unlike the spend-logs table there is no coverage floor below which a figure does
        not exist, so no document at all means nothing was recorded. **A scope whose documents all
        arrived with their cost unsettled is a different fact** and is reported as no figure, because
        the `sum` skipped every one of them - `_money_figure` holds that rule and the whole of the
        distinction between the two.

        `scoped_by` is the caller's access boundary and defaults to none, exactly as it does on
        `_sum_money_per_bucket`: a read that does not name one has no caller to be narrowed by, and
        the boundary is added by `_query` so its `OR` stays parenthesised inside the filter `AND`.
        """
        body = {
            "size": 0,
            "query": self._query(period, filters, scoped_by=scoped_by),
            "aggs": _money_aggs(),
        }
        result = await self._repository.execute_aggregation_query(body)
        return _money_figure(result.get("aggregations", {}))
