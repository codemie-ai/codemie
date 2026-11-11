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

"""The pre-floor engine, held to the one thing it must never do: write down a figure nobody measured.

**`figure is None` and `figure == Decimal("0")` are the whole subject of this file.** Below the floor
the metric store records no budget category, so a category-keyed holder has nothing to be measured
by, and the only honest answer is *no figure*. An earlier implementation answered those holders by
reading the identity's whole Elasticsearch total, placing it on the lowest-sorted category and
handing every other category a fabricated `Decimal("0")` - a device that made a consumer summing a
project's categories come out at the pre-cutover row total. It was removed because four surfaces
render **one** category-keyed holder on its own (`service/project/project_spending_service.py` builds one
`ProjectHolder(project, category)` per budget row and prints that single result as
`current_spending`), and on all four the zero publishes real spend as an exact, measured `$0.00`.

**So the assertions here are written to fail on that device, not merely to describe the new
behaviour.** A test asserting *"the categories add up to the identity total"* passes on the
implementation that lies; a test asserting `results[holder].figure is None` - and asserting
explicitly that it is **not** `Decimal("0")` - is the one that cannot. Every category-keyed case
below is given rows the removed device would have found, so a re-introduction of it turns these red
rather than leaving them vacuously green.

**The third state this file pins is a figure the store *can* measure and used not to report.** The
budget category is absent from every emission; the CLI traffic class is not - `attributes.cli_request`
is a flag the proxy emitter writes on every proxied call, and the predicate the pre-cutover surfaces
summed their CLI money on. Reporting *that* absent is the mirror of the fabricated zero: it withholds
a measurement rather than inventing one, and on the leaderboard it printed a person's total beside a
blank cell. `TestTheCliClassIsTheOneClassTheStoreHasAWordFor` holds the difference.

**The rows belong to the fake, not to the assertions.** `_RowSetMetrics` - reused from the
default-ledger tests rather than restated - answers every aggregation by applying the query's own
filters to one row set, so the one read this module still makes (the person total) can only come out
right if it asked the right question of the right documents. A stub answering a fixed total per call
would let a read scoped to the wrong identity, or to a metric name this adapter must not touch, pass
without a murmur. It **raises** on a clause shape it cannot evaluate, which is what makes it a fake
rather than a yes-man: a clause silently ignored widens every bucket that carries it, and a per-
identity assertion would then pass on a read that selected the whole index.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from codemie.service.analytics.metric_names import MetricName
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import SpendDimension, SpendResult
from codemie.service.spend.default_ledger import (
    _CLI_REQUEST_FIELD,
    _MONEY_METRICS,
    _PROJECT_FIELD,
    _USER_ID_FIELD,
    MONEY_FIELD,
    DefaultSpendLedger,
)
from codemie.service.spend.ledger import (
    AccessScope,
    ComposedScope,
    PersonalHolder,
    PersonTotalHolder,
    ProjectGroupHolder,
    ProjectHolder,
    ProjectMemberHolder,
    ReportFilter,
    RequestCost,
    UserTerm,
)
from codemie.service.spend.legacy_ledger import LEGACY_MONEY_METRICS, LegacySpendLedger, legacy_metric_names
from codemie.service.spend.routing import LLMCallClass
from codemie.service.spend.traffic_class import TrafficClass
from tests.codemie.service.spend.periods import PERIOD
from tests.codemie.service.spend.test_default_ledger import MID_PERIOD, _RowSetMetrics

USER = "aaaaaaaa-1111-1111-1111-111111111111"
OTHER_USER = "bbbbbbbb-2222-2222-2222-222222222222"
PROJECT = "atlas"
OTHER_PROJECT = "borealis"

#: One holder of each category-keyed shape, all four naming the identity the row set below carries
#: money for. They are the four the store cannot answer, and the four the removed device would have
#: answered with the identity's whole total plus fabricated zeros.
CATEGORY_KEYED_HOLDERS = (
    ProjectHolder(project=PROJECT, category=BudgetCategory.PLATFORM),
    PersonalHolder(user_id=USER, category=BudgetCategory.CLI, aliases=frozenset()),
    ProjectMemberHolder(project=PROJECT, category=BudgetCategory.PREMIUM_MODELS, user_id=USER),
    ProjectGroupHolder(projects=frozenset({PROJECT, OTHER_PROJECT}), category=BudgetCategory.PLATFORM),
)

#: The fifth holder: the only one without a category, and therefore the only one the metric store
#: answers natively.
PERSON_TOTAL = PersonTotalHolder(user_id=USER, aliases=frozenset())


class _RecordingAdapter:
    """A `DefaultSpendLedger` that remembers **which holders** it was asked about.

    The query bodies alone cannot express the assertion this exists for. "A `PersonalHolder` was
    answered with the person-total predicate" is a statement about the holder handed down, and a body
    carrying a user-id `should` looks identical whether it came from a `PersonTotalHolder` the caller
    really asked about or from one this module manufactured out of a personal budget - which is
    exactly the substitution the removed device made.
    """

    def __init__(self, adapter: DefaultSpendLedger) -> None:
        self._adapter = adapter
        self.asked: list[list] = []
        self.filters: list = []

    async def consumed_for_holders(self, holders, period, access=None, *, report_filter=None):
        self.asked.append(list(holders))
        self.filters.append(report_filter)
        return await self._adapter.consumed_for_holders(holders, period, access, report_filter=report_filter)


def money_row(
    money: str,
    *,
    metric: str = MetricName.CONVERSATION_ASSISTANT_USAGE.value,
    project: str | None = None,
    user: str | None = None,
) -> dict:
    """One settled money-bearing document, carrying only the attributes it is given.

    Attributes are omitted rather than defaulted to a placeholder, because "arrived without a project
    attribute" is a real and common shape here - the shared emitter writes none of its own - and a
    fake that stamped every row with a project would hide the very rows a project-scoped sum misses.
    """
    row = {"metric_name.keyword": metric, "@timestamp": MID_PERIOD, MONEY_FIELD: Decimal(money)}
    if project is not None:
        row[_PROJECT_FIELD] = project
    if user is not None:
        row[_USER_ID_FIELD] = user
    return row


def legacy_ledger(
    rows: list[dict], call_classes: frozenset[LLMCallClass] = frozenset(LLMCallClass)
) -> tuple[LegacySpendLedger, _RowSetMetrics]:
    """The adapter under test over a row set, wired exactly as its own constructor wires it.

    The class scope and `legacy_metric_names` are not this helper's choices - they are what
    `LegacySpendLedger.__init__` builds when no adapter is injected, restated here only because the
    injection point exists to put a fake repository underneath.
    `TestTheCallClassScopeNarrowsWhatIsRead` pins that the two agree for every scope, so a change to
    the real construction cannot leave these tests measuring a ledger the platform never builds.
    """
    repo = _RowSetMetrics(rows)
    adapter = DefaultSpendLedger(call_classes, repo, metric_names=legacy_metric_names(call_classes))
    return LegacySpendLedger(adapter), repo


def rows_for_every_identity() -> list[dict]:
    """Money the removed device would have found for each of the four category-keyed holders.

    Every row names both the project and the user, so a project-scoped read, a member-scoped read, a
    group-scoped read and a person-scoped read all select it. That is what makes the assertions below
    hostile rather than accidental: *no* filter this module could plausibly build comes back empty
    against this row set, so a figure of `None` can only mean the read was never made.
    """
    return [money_row("7.25", project=PROJECT, user=USER), money_row("5.25", project=PROJECT, user=USER)]


class TestACategoryKeyedHolderHasNoFigure:
    """The rule: below the floor the budget-category dimension does not exist, so there is no figure.

    Not *zero*. A zero is a positive claim that nothing was spent, and four surfaces render one of
    these holders on its own - so a fabricated zero there is real money published as an exact
    `$0.00`, with no `unavailable` and no `partial` on it to say otherwise.
    """

    @pytest.mark.parametrize("holder", CATEGORY_KEYED_HOLDERS, ids=lambda holder: type(holder).__name__)
    def test_the_figure_is_absent_and_specifically_not_a_fabricated_zero(self, holder):
        """`is None`, asserted **beside** `!= Decimal("0")` - the distinction is the whole change.

        Both halves are spelled out because they fail differently: an implementation that reports a
        zero satisfies "there is no money here" to a careless reader and still publishes a
        measurement, and a renderer keyed on `figure is not None` prints `$0.00` for a project that
        may have spent a fortune.
        """
        ledger, _repo = legacy_ledger(rows_for_every_identity())

        result = asyncio.run(ledger.consumed_for_holders([holder], PERIOD))[holder]

        assert result.figure is None
        assert result.figure != Decimal("0"), "a zero here is a claim that nothing was spent"

    @pytest.mark.parametrize("holder", CATEGORY_KEYED_HOLDERS, ids=lambda holder: type(holder).__name__)
    def test_the_absence_covers_the_whole_requested_period(self, holder):
        """The flag a qualified budget row is rendered from, and the reason the figure is not exact.

        `SpendResult` requires an absent figure to name the time it covers, so this is also the check
        that the module builds its answer through `no_figure` rather than assembling a bare `None`.
        """
        ledger, _repo = legacy_ledger(rows_for_every_identity())

        result = asyncio.run(ledger.consumed_for_holders([holder], PERIOD))[holder]

        assert result.unavailable == PERIOD
        assert not result.is_exact

    def test_the_store_is_not_read_for_them_at_all(self):
        """No query, because there is no question: nothing in the store carries a budget category.

        A read whose answer is discarded is how the removed device would come back - first as a
        harmless total, then as something to do with it - so the absence of the round trip is pinned
        rather than only the absence of the figure.
        """
        ledger, repo = legacy_ledger(rows_for_every_identity())

        asyncio.run(ledger.consumed_for_holders(list(CATEGORY_KEYED_HOLDERS), PERIOD))

        assert repo.bodies == []

    def test_categories_of_one_identity_do_not_add_up_to_its_elasticsearch_total(self):
        """The property the removed device existed to provide, asserted as **gone**.

        This is the test that documents the owner decision: a pre-floor budget row is rendered
        qualified, and the sum a consumer used to get back is not recovered by fabricating parts of
        it. A future change restoring the spread makes this fail, which is the point.
        """
        holders = [ProjectHolder(project=PROJECT, category=category) for category in BudgetCategory]
        ledger, _repo = legacy_ledger(rows_for_every_identity())

        results = asyncio.run(ledger.consumed_for_holders(holders, PERIOD))

        assert all(results[holder].figure is None for holder in holders)


class TestEveryHolderAskedAboutIsAnswered:
    def test_no_holder_is_silently_dropped_from_the_mapping(self):
        """The port's contract, and not a convenience: a missing key renders as a blank, not a state.

        A consumer indexes the mapping by the holder it asked about. A holder shape this module
        forgot to classify - the fifth holder, or a new one - would not raise here; it would simply
        not come back, and the surface would render an empty cell that no state, flag or log accounts
        for. So all five shapes are asked at once and the key set is compared whole.
        """
        holders = [*CATEGORY_KEYED_HOLDERS, PERSON_TOTAL]
        ledger, _repo = legacy_ledger(rows_for_every_identity())

        results = asyncio.run(ledger.consumed_for_holders(holders, PERIOD))

        assert set(results) == set(holders)
        assert all(isinstance(result, SpendResult) for result in results.values())


class TestThePersonTotalPassesThrough:
    """The fifth holder is the one without a category, and therefore the one still answered.

    The metric store records the person, the adapter already answers it exactly - including the
    `identity_bridge` qualification an unresolved alias set carries - so turning it into `no_figure`
    alongside the other four would blank a figure that was genuinely measured. The rule is about the
    category dimension, not about the floor.
    """

    def test_it_is_the_adapters_own_answer_unchanged(self):
        """Compared against the adapter over the same rows, rather than against a hand-written figure.

        A hand-written expectation would still pass if this class quietly re-answered the person from
        its own arithmetic; agreeing with the adapter is the property that says it did not.
        """
        rows = [money_row("2.5", user=USER), money_row("1.5", user=USER), money_row("9", user=OTHER_USER)]
        holder = PersonTotalHolder(user_id=USER, aliases=frozenset({"alice@example.com"}))
        ledger, _repo = legacy_ledger(rows)
        adapter = DefaultSpendLedger(frozenset(LLMCallClass), _RowSetMetrics(rows), metric_names=LEGACY_MONEY_METRICS)

        through_legacy = asyncio.run(ledger.consumed_for_holders([holder], PERIOD))
        through_adapter = asyncio.run(adapter.consumed_for_holders([holder], PERIOD))

        assert through_legacy == through_adapter
        assert through_legacy[holder].figure == Decimal("4")

    def test_it_is_not_swept_into_no_figure_by_the_category_rule(self):
        """Asked beside a category-keyed holder for the same person, and it keeps its own figure.

        The two holders name one human and are answered differently on purpose: the person total is a
        measurement the store can make, the personal budget's category is not. A rule applied by
        "below the floor, report nothing" instead of "the category dimension does not exist" would
        blank both.
        """
        personal = PersonalHolder(user_id=USER, category=BudgetCategory.PLATFORM, aliases=frozenset())
        ledger, _repo = legacy_ledger([money_row("6", user=USER)])

        results = asyncio.run(ledger.consumed_for_holders([PERSON_TOTAL, personal], PERIOD))

        assert results[PERSON_TOTAL].figure == Decimal("6")
        assert results[personal].figure is None

    def test_it_reaches_the_adapter_with_the_dashboards_filter(self):
        """Below the floor the users-spending table's money is the adapter's batch, so the filter its
        rows were narrowed by must arrive there - dropped here, a person filtered to one project would be
        shown their pre-cutover spend in every project."""
        adapter = _RecordingAdapter(
            DefaultSpendLedger(
                frozenset(LLMCallClass),
                _RowSetMetrics(rows_for_every_identity()),
                metric_names=LEGACY_MONEY_METRICS,
            )
        )
        asked = ReportFilter(projects=(PROJECT,))

        asyncio.run(LegacySpendLedger(adapter).consumed_for_holders([PERSON_TOTAL], PERIOD, report_filter=asked))

        assert adapter.filters == [asked]


class TestAPersonalHolderIsNeverProbedWithAPersonTotal:
    """The substitution the removed device made, pinned so it cannot come back quietly.

    `PersonTotalHolder` spans the personal **and** project pots by design; every other holder keeps
    them apart, because a call made with a project in context lands in the project pot only. Probing
    a `PersonalHolder` with a person total therefore swaps the personal-pot predicate for the
    both-pots one: the personal budget's figure absorbs every project call the person made, **and**
    those same rows are counted again in that project's own `ProjectHolder` figure - the same row in
    two holders the port keeps disjoint above the floor.
    """

    def test_the_adapter_is_asked_about_nothing_when_only_category_holders_are(self):
        """Asserted on what the adapter was handed, because a query body cannot tell the two apart.

        A `bool.should` over the user id looks the same whether the caller really asked for a person
        total or this module manufactured one from a personal budget.
        """
        adapter = _RecordingAdapter(
            DefaultSpendLedger(
                frozenset(LLMCallClass),
                _RowSetMetrics(rows_for_every_identity()),
                metric_names=LEGACY_MONEY_METRICS,
            )
        )
        ledger = LegacySpendLedger(adapter)

        asyncio.run(ledger.consumed_for_holders(list(CATEGORY_KEYED_HOLDERS), PERIOD))

        assert adapter.asked == []

    def test_only_the_person_totals_the_caller_named_reach_the_adapter(self):
        """A person total and a personal budget for the same user: exactly one of them goes down."""
        personal = PersonalHolder(user_id=USER, category=BudgetCategory.CLI, aliases=frozenset())
        adapter = _RecordingAdapter(
            DefaultSpendLedger(
                frozenset(LLMCallClass),
                _RowSetMetrics(rows_for_every_identity()),
                metric_names=LEGACY_MONEY_METRICS,
            )
        )
        ledger = LegacySpendLedger(adapter)

        asyncio.run(ledger.consumed_for_holders([PERSON_TOTAL, personal], PERIOD))

        assert adapter.asked == [[PERSON_TOTAL]]

    def test_a_personal_budget_never_reaches_the_store_under_a_project_bearing_row(self):
        """The consequence on the figure: the personal pot cannot absorb this project's money.

        The row set is one project call by this user. The personal budget must not report it - and
        neither may it report it as a zero, which the second assertion is here to separate from the
        first.
        """
        personal = PersonalHolder(user_id=USER, category=BudgetCategory.CLI, aliases=frozenset())
        ledger, repo = legacy_ledger([money_row("11", project=PROJECT, user=USER)])

        result = asyncio.run(ledger.consumed_for_holder(personal, PERIOD))

        assert result.figure is None
        assert result.figure != Decimal("0")
        assert repo.bodies == []


class TestTheSingularReadAgreesWithTheBatch:
    """Two figures for one holder is the defect the port exists to remove, so the two paths are compared.

    `consumed_for_holder` delegates to the batch precisely so it cannot hold a second copy of the
    rule; this asserts the delegation rather than trusting it, for all five holder shapes.
    """

    @pytest.mark.parametrize(
        "holder", [*CATEGORY_KEYED_HOLDERS, PERSON_TOTAL], ids=lambda holder: type(holder).__name__
    )
    def test_one_holder_reads_the_same_either_way(self, holder):
        ledger, _repo = legacy_ledger(rows_for_every_identity())

        singular = asyncio.run(ledger.consumed_for_holder(holder, PERIOD))
        batched = asyncio.run(ledger.consumed_for_holders([holder], PERIOD))[holder]

        assert singular == batched

    def test_the_two_paths_agree_on_which_holders_have_a_figure(self):
        """Term for term, not merely equal object by object: the person is measured, the four are not."""
        ledger, _repo = legacy_ledger(rows_for_every_identity())

        for holder in CATEGORY_KEYED_HOLDERS:
            assert asyncio.run(ledger.consumed_for_holder(holder, PERIOD)).figure is None
        assert asyncio.run(ledger.consumed_for_holder(PERSON_TOTAL, PERIOD)).figure == Decimal("12.5")


class TestTheNameSetIsWhatTheSurfacesSummed:
    """Below the floor the adapter must sum exactly the names the pre-cutover aggregations summed.

    The requirement for a pre-floor period is parity with the number production prints today, and
    today's number comes from an Elasticsearch aggregation filtered on
    `MetricName.to_list_from_group(MetricName.SPENDING_METRICS)`. Reading a *narrower* set does not
    fail loudly: it returns a short figure as an **exact** `SpendResult` - no `unavailable`, no
    `partial` - and on a project whose pre-floor money rides only on the missing names it returns a
    measured `$0.00` that `enriched_spend_rows` then drops from the table. That is the fabricated
    zero this module already had to remove once, arriving through the metric-name set instead of
    through the holder read.

    So the containment is asserted against the live group rather than against a transcribed list: a
    tenth spending metric added later turns this red instead of quietly shrinking every pre-October
    figure on the platform.
    """

    def test_every_metric_the_surfaces_filtered_on_is_read(self):
        """The regression test that cannot rot - it re-expands the group, it does not restate it."""
        surface_names = frozenset(MetricName.to_list_from_group(MetricName.SPENDING_METRICS))

        assert (
            surface_names <= LEGACY_MONEY_METRICS
        ), "a pre-floor figure that omits a name the surface summed is short and looks exact"

    def test_the_ports_own_money_table_is_read_too(self):
        """The union, not a replacement: nothing a ledger prices above the floor may be lost here."""
        table = {name for names in _MONEY_METRICS.values() for name in names}

        assert table <= LEGACY_MONEY_METRICS

    def test_the_set_is_exactly_that_union_and_nothing_further(self):
        """Widening past the two derived sources would sweep in documents no surface ever counted."""
        table = {name for names in _MONEY_METRICS.values() for name in names}
        surface_names = frozenset(MetricName.to_list_from_group(MetricName.SPENDING_METRICS))

        assert table | surface_names == LEGACY_MONEY_METRICS

    def test_money_on_a_cli_document_is_counted(self):
        """`DefaultSpendLedger` excludes `CLI_LLM_USAGE_TOTAL` as its double-count guard.

        Below the floor there are no spend-logs rows at all, so the exclusion guards nothing and
        instead drops every pre-cutoff CLI cost. Asserted on a figure as well as on a set.
        """
        ledger, _repo = legacy_ledger(
            [
                money_row("3", metric=MetricName.CLI_LLM_USAGE_TOTAL.value, user=USER),
                money_row("1", user=USER),
            ]
        )

        result = asyncio.run(ledger.consumed_for_holder(PERSON_TOTAL, PERIOD))

        assert result.figure == Decimal("4")

    def test_money_on_a_metric_the_ports_table_omits_reaches_a_pre_floor_total(self):
        """`CODEMIE_TOOLS_USAGE_TOKENS` - one of the six the port's money table never named.

        Asserted on the window total rather than on a holder because the failure this pins is not an
        absence: with the tool-token row unread the figure comes back as an exact `Decimal("0")`,
        indistinguishable from a window in which nothing was spent at all.
        """
        ledger, _repo = legacy_ledger(
            [money_row("5", metric=MetricName.CODEMIE_TOOLS_USAGE_TOKENS.value, project=PROJECT, user=USER)]
        )

        result = asyncio.run(ledger.aggregate(PERIOD))

        assert result.figure == Decimal("5")

    @pytest.mark.parametrize(
        "metric",
        [
            MetricName.ASSISTANT_GENERATOR_TOTAL,
            MetricName.PROMPT_GENERATOR_TOTAL,
            MetricName.SKILL_GENERATOR_TOTAL,
            MetricName.WORKFLOW_OUTPUT_CHANGE_TOTAL,
            MetricName.CODEMIE_TOOLS_USAGE_TOKENS,
            MetricName.MARKETPLACE_ASSISTANT_VALIDATION_TOTAL,
        ],
        ids=lambda metric: metric.name.lower(),
    )
    def test_each_previously_omitted_name_reaches_a_persons_figure(self, metric):
        """All six, one at a time: each was money the platform showed and this adapter did not read."""
        ledger, _repo = legacy_ledger([money_row("2", metric=metric.value, user=USER)])

        result = asyncio.run(ledger.consumed_for_holder(PERSON_TOTAL, PERIOD))

        assert result.figure == Decimal("2")


def cli_row(money: str, *, user: str = USER, project: str | None = None, cli_request: bool = True) -> dict:
    """One proxy-metric document, carrying the flag the pre-cutover CLI aggregations filtered on.

    `cli_request=False` is a real and common shape rather than a contrived one: every non-CLI proxied
    call writes the same metric with the flag false, and a read that dropped the flag term would sum
    those too - reporting a person's whole proxy spend as their CLI spend.
    """
    row = money_row(money, metric=MetricName.CLI_LLM_USAGE_TOTAL.value, project=project, user=user)
    row[_CLI_REQUEST_FIELD] = cli_request
    return row


#: One person's pre-floor money, split the four ways that can each be got wrong.
#:
#: The **project-scoped** CLI row is the whole point of the composed read: `PersonalHolder(user, CLI)`
#: cannot see it when a project budget was charged, while the aggregation the leaderboard actually
#: ran grouped by user and filtered on the flag, never on a budget - so that row was in production's
#: number and must be in this one.
def cli_and_other_rows() -> list[dict]:
    return [
        cli_row("3"),
        cli_row("4", project=PROJECT),
        cli_row("5", cli_request=False),
        money_row("6", user=USER),
        cli_row("99", user=OTHER_USER),
    ]


def pre_cutover_cli_spend(rows: list[dict], user: str = USER) -> Decimal:
    """What the pre-cutover leaderboard's own `cli_spend` bucket summed for one person.

    Re-run over the row set rather than hand-totalled, and spelled as its three terms - the metric
    name, the flag, the user - so that a read which dropped any one of them fails against the number
    that aggregation produced rather than against a constant somebody could quietly adjust.
    """
    return sum(
        (
            row[MONEY_FIELD]
            for row in rows
            if row["metric_name.keyword"] == MetricName.CLI_LLM_USAGE_TOTAL.value
            and row.get(_CLI_REQUEST_FIELD) is True
            and row.get(_USER_ID_FIELD) == user
        ),
        Decimal("0"),
    )


def leaderboard_reads(rows: list[dict], call_classes: frozenset[LLMCallClass] = frozenset(LLMCallClass)):
    """The two reads a leaderboard row is built from, made exactly as `collector.py` makes them.

    `PersonTotalHolder` for the total and a **user-only `ComposedScope`** for the CLI share - not
    `PersonalHolder(user, CLI)`, which the collector's own docstring calls the trap that compiles.
    Both are asked here so the assertions can compare them, which is the only place a share that
    exceeds its total, or a total that grew by its own share, shows up as a number.
    """
    ledger, _repo = legacy_ledger(rows, call_classes)
    total = asyncio.run(ledger.consumed_for_holder(PERSON_TOTAL, PERIOD))
    split = asyncio.run(
        ledger.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=UserTerm(USER, frozenset())))
    )
    return total, split


class TestTheCliClassIsTheOneClassTheStoreHasAWordFor:
    """The pre-floor leaderboard's CLI column, restored to the figure production prints.

    A leaderboard row carries two money figures: the total, which `PersonTotalHolder` is answered
    natively for here, and the CLI share, which goes through `aggregate_composed_by_traffic_class`.
    While the class split reported absent for every class, a pre-floor row showed a measured total
    beside a **blank** CLI cell - a cell no flag, log or annotation accounts for, on a page whose
    pre-cutover version printed both numbers out of one Elasticsearch pass.

    **The class is expressible here, which is what makes absent the wrong answer rather than a
    cautious one.** The absence rule this module lives by is about dimensions the store never
    recorded; `attributes.cli_request` is recorded on every proxied call, and is the predicate the
    pre-cutover aggregation itself used.
    """

    def test_the_cli_figure_is_the_sum_the_pre_cutover_aggregation_produced(self):
        """Parity with today's number, asserted against that aggregation re-run over the rows."""
        rows = cli_and_other_rows()
        _total, split = leaderboard_reads(rows)

        assert split[TrafficClass.CLI].figure == pre_cutover_cli_spend(rows)
        assert split[TrafficClass.CLI].figure == Decimal("7"), "the principal CLI row and the project-scoped one"

    def test_the_figure_is_exact_rather_than_qualified(self):
        """The governing rule for a pre-floor period: a number production prints clean stays clean.

        A figure carrying `unavailable` renders with an *at least* on it. Nothing here is short - the
        engine read every document the surface read - so acquiring that suffix would be a new
        qualification on an old number, which is its own kind of wrong answer.
        """
        _total, split = leaderboard_reads(cli_and_other_rows())

        assert split[TrafficClass.CLI].is_exact
        assert not split[TrafficClass.CLI].partial

    def test_a_project_scoped_cli_call_is_counted(self):
        """The row `PersonalHolder(user, CLI)` cannot see, on its own so the failure is unambiguous.

        Through that holder this reads as no figure; through the own-customer predicate it reads as
        zero. Both are wrong by the whole of the person's project CLI spend, and neither is marked.
        """
        _total, split = leaderboard_reads([cli_row("4", project=PROJECT)])

        assert split[TrafficClass.CLI].figure == Decimal("4")

    def test_a_non_cli_proxy_call_is_not_in_the_cli_figure(self):
        """Dropping the flag term reports a person's whole proxy spend as their CLI spend."""
        _total, split = leaderboard_reads([cli_row("3"), cli_row("5", cli_request=False)])

        assert split[TrafficClass.CLI].figure == Decimal("3")

    def test_the_flag_is_only_read_on_the_metric_that_defines_it(self):
        """The metric-name term of the two-clause filter, and the reason it is not decorative.

        Only the proxy emitter writes this flag today, so a filter resting on it alone gives the same
        answer - until a second emitter adopts the attribute, at which point that read starts summing
        documents the pre-cutover aggregation never saw and the CLI column silently grows. The
        aggregation named the metric as well; so does this.
        """
        adopted_elsewhere = money_row("6", user=USER)
        adopted_elsewhere[_CLI_REQUEST_FIELD] = True

        _total, split = leaderboard_reads([cli_row("3"), adopted_elsewhere])

        assert split[TrafficClass.CLI].figure == Decimal("3")

    def test_another_persons_cli_money_is_not_in_this_persons_figure(self):
        """The user term of the conjunction, which is the term a class split makes it easy to lose."""
        _total, split = leaderboard_reads([cli_row("3"), cli_row("99", user=OTHER_USER)])

        assert split[TrafficClass.CLI].figure == Decimal("3")

    def test_a_person_who_made_no_cli_calls_reads_a_measured_zero(self):
        """Zero here is a **measurement**, and the one case in this file where that is the right answer.

        The engine read the person's documents and found no CLI call among them - which is what the
        pre-cutover aggregation returned for that person too, and what the leaderboard printed. The
        absence rule is about a question the store cannot answer, not about an answer of nothing.
        """
        _total, split = leaderboard_reads([money_row("6", user=USER)])

        assert split[TrafficClass.CLI].figure == Decimal("0")
        assert split[TrafficClass.CLI].unavailable is None

    @pytest.mark.parametrize(
        "traffic_class",
        [TrafficClass.WORKFLOW, TrafficClass.ASSISTANT, TrafficClass.DATASOURCE],
        ids=lambda traffic_class: traffic_class.value,
    )
    def test_the_other_three_classes_are_absent_and_specifically_not_zero(self, traffic_class):
        """The store has no word for them, and a zero would say the person did none of that work.

        Given rows a metric-name grouping would happily have bucketed, so a substitute selector
        introduced later turns this red rather than leaving it vacuously green.
        """
        _total, split = leaderboard_reads(cli_and_other_rows())

        assert split[traffic_class].figure is None
        assert split[traffic_class].figure != Decimal("0"), "a zero here is a claim about work nobody measured"
        assert split[traffic_class].unavailable == PERIOD

    def test_every_class_is_still_present_in_the_mapping(self):
        """A class silently absent reads downstream as a missing series rather than an empty one."""
        _total, split = leaderboard_reads(cli_and_other_rows())

        assert set(split) == set(TrafficClass)


class TestTheCliShareAndThePersonTotalCannotDoubleCount:
    """One pass over one row set answers both, so the share is **inside** the total, never beside it.

    That is how the pre-cutover collector produced them - `total_spend` over a person's bucket and
    `cli_spend` as a filtered aggregation *within* that bucket - and it is what the leaderboard
    renders: an amount and a part of it, in two columns, added by nobody.
    """

    def test_the_total_is_unchanged_by_the_class_read_existing(self):
        """The property that says the CLI figure was not paid for out of the total.

        Compared against the same holder answered by the adapter over the same rows rather than
        against a constant: agreeing with the adapter is what says this engine still delegates the
        person read whole, rather than having learned to adjust it.
        """
        rows = cli_and_other_rows()
        total, _split = leaderboard_reads(rows)
        adapter = DefaultSpendLedger(frozenset(LLMCallClass), _RowSetMetrics(rows), metric_names=LEGACY_MONEY_METRICS)

        assert total == asyncio.run(adapter.consumed_for_holder(PERSON_TOTAL, PERIOD))
        assert total.figure == Decimal("18"), "every money row of this person, the non-CLI proxy call included"

    def test_the_cli_share_is_a_part_of_the_total_and_not_an_addition_to_it(self):
        """A surface adding the two columns would report 25 for a person who spent 18.

        Pinned as an inequality rather than as two constants, because the failure it guards against is
        an implementation that answers the CLI class from a *second* source - which shows up first as
        a share that no longer fits inside its own total.
        """
        total, split = leaderboard_reads(cli_and_other_rows())

        assert Decimal("0") < split[TrafficClass.CLI].figure < total.figure

    def test_no_class_figure_is_counted_twice_across_the_split(self):
        """The classes that carry a figure sum to no more than the total they are drawn from."""
        total, split = leaderboard_reads(cli_and_other_rows())
        measured = [entry.figure for entry in split.values() if entry.figure is not None]

        assert sum(measured, Decimal("0")) <= total.figure


class TestTheClassIsOnlyReadWhereTheMoneyIsThisEnginesToReport:
    """The gate, which is the double-count guard asked the traffic-class question.

    The CLI flag rides on `CLI_LLM_USAGE_TOTAL`, a name the port's own money table withholds because
    that traffic is LiteLLM-routed and the spend-logs ledger reports it. An adapter without that name
    is not looking at an empty CLI bucket; it is looking **away** from documents in the very store it
    reads, so a sum over its own rows returns `0.0` for money one filter away.
    """

    def test_an_adapter_that_may_not_read_the_cli_metric_reports_no_figure_and_never_a_zero(self):
        """The same rows, the same store, and the answer turns on the name set alone.

        This is the above-floor adapter: its class-derived table excludes the CLI metric, so the money
        below is real, present, and not its to report - and a measured `$0.00` over it would be a
        fabricated zero arriving through the class split instead of through the holder read.
        """
        rows = cli_and_other_rows()
        above_floor = DefaultSpendLedger(frozenset(LLMCallClass), _RowSetMetrics(rows))

        split = asyncio.run(
            above_floor.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=UserTerm(USER, frozenset())))
        )

        assert split[TrafficClass.CLI].figure is None
        assert split[TrafficClass.CLI].figure != Decimal("0")
        assert pre_cutover_cli_spend(rows) > 0, "the rows the refusal is about are really there"

    def test_an_embeddings_only_pre_floor_engine_reports_no_figure_rather_than_its_own_zero(self):
        """The mixed configuration: two engines, and CLI traffic belongs to exactly one of them.

        The embeddings engine's name set holds the datasource metric alone, so it has no CLI rows to
        find. It says *no figure* rather than a zero, and `combine_results` then carries that flag onto
        the deployment's CLI figure - the amount stays the chat engine's and stays right, and the
        qualification is what declining to fabricate the other engine's zero costs.
        """
        _total, split = leaderboard_reads(cli_and_other_rows(), frozenset({LLMCallClass.EMBEDDINGS}))

        assert split[TrafficClass.CLI].figure is None
        assert split[TrafficClass.CLI].figure != Decimal("0")

    def test_a_chat_scoped_pre_floor_engine_is_the_one_that_answers(self):
        """The other half of the partition: CLI traffic is chat traffic, so the chat engine has it all."""
        rows = cli_and_other_rows()
        _total, split = leaderboard_reads(rows, frozenset({LLMCallClass.CHAT}))

        assert split[TrafficClass.CLI].figure == pre_cutover_cli_spend(rows)


class TestTheCallClassScopeNarrowsWhatIsRead:
    """`call_classes` must change the query, not merely label the instance.

    It exists for the mixed configuration - chat through LiteLLM, embeddings native - where both
    registry slots hold a ledger, both get a cutover wrapper, and therefore two pre-floor engines
    answer for one deployment. Scoped to every class, each of them sums every pre-floor row, so the
    deployment counts its own money twice while both figures look entirely plausible alone. The
    parameter is only a fix if the two engines read **disjoint** metric names.
    """

    def test_an_embeddings_only_engine_reads_only_the_embedding_metric(self):
        assert legacy_metric_names([LLMCallClass.EMBEDDINGS]) == frozenset({MetricName.DATASOURCE_TOKENS_USAGE.value})

    def test_the_cli_proxy_metric_belongs_to_chat_alone(self):
        """It is chat traffic - the CLI proxies nothing else - so exactly one engine may read it.

        Added to every class's set instead, it would be the one name both engines in a mixed
        deployment shared, and every pre-cutoff CLI cost would be counted twice.
        """
        assert MetricName.CLI_LLM_USAGE_TOTAL.value in legacy_metric_names([LLMCallClass.CHAT])
        assert MetricName.CLI_LLM_USAGE_TOTAL.value not in legacy_metric_names([LLMCallClass.EMBEDDINGS])

    def test_the_surface_only_names_belong_to_chat_alone(self):
        """The generator, tool-token, marketplace-validation and output-change metrics bill chat calls.

        Each one prices a completion - a model asked to write an assistant, a prompt or a skill, or
        the chat call a tool made - so chat is where they belong rather than merely where a set
        subtraction leaves them. `DATASOURCE_TOKENS_USAGE` is the one embedding-priced name the
        surfaces sum, and it stays with the embeddings engine.
        """
        chat = legacy_metric_names([LLMCallClass.CHAT])
        embeddings = legacy_metric_names([LLMCallClass.EMBEDDINGS])

        for metric in (
            MetricName.ASSISTANT_GENERATOR_TOTAL,
            MetricName.PROMPT_GENERATOR_TOTAL,
            MetricName.SKILL_GENERATOR_TOTAL,
            MetricName.WORKFLOW_OUTPUT_CHANGE_TOTAL,
            MetricName.CODEMIE_TOOLS_USAGE_TOKENS,
            MetricName.MARKETPLACE_ASSISTANT_VALIDATION_TOTAL,
        ):
            assert metric.value in chat
            assert metric.value not in embeddings

    def test_the_per_class_sets_partition_the_unscoped_one(self):
        """Disjoint and complete: nothing is read twice, and nothing is dropped on the way."""
        chat = legacy_metric_names([LLMCallClass.CHAT])
        embeddings = legacy_metric_names([LLMCallClass.EMBEDDINGS])

        assert chat & embeddings == frozenset()
        assert chat | embeddings == LEGACY_MONEY_METRICS

    def test_the_narrowing_reaches_the_figure_and_not_only_the_name_set(self):
        """A chat document, and an engine scoped to embeddings: the money must not be summed.

        Asserted on a read rather than on a set, because the name set is only a guard if it is the
        set the query body carries.
        """
        ledger, _repo = legacy_ledger(
            [
                money_row("4", metric=MetricName.CONVERSATION_ASSISTANT_USAGE.value, user=USER),
                money_row("2", metric=MetricName.DATASOURCE_TOKENS_USAGE.value, user=USER),
            ],
            frozenset({LLMCallClass.EMBEDDINGS}),
        )

        result = asyncio.run(ledger.consumed_for_holder(PERSON_TOTAL, PERIOD))

        assert result.figure == Decimal("2")

    @pytest.mark.parametrize(
        "call_classes",
        [frozenset(LLMCallClass), frozenset({LLMCallClass.CHAT}), frozenset({LLMCallClass.EMBEDDINGS})],
        ids=["every_class", "chat_only", "embeddings_only"],
    )
    def test_the_real_construction_is_the_one_these_tests_exercise(self, call_classes):
        """What the platform builds must be what the fake sits underneath, for every scope.

        Every other test here injects an adapter, so all of them would keep passing if the real
        constructor started building a *different* one - a narrower name set, a class scope it
        ignored - and the pre-cutoff figures would quietly change with no test to notice. The
        default-argument case is the one composition uses today.
        """
        injected, _repo = legacy_ledger([], call_classes)

        assert LegacySpendLedger(call_classes=call_classes)._es._metric_names() == injected._es._metric_names()

    def test_the_default_scope_is_every_call_class(self):
        """Below the floor there is normally one engine, so it answers for the whole deployment."""
        assert LegacySpendLedger()._es._metric_names() == sorted(LEGACY_MONEY_METRICS)


class _RecordingReadAdapter:
    """A `DefaultSpendLedger` stand-in that remembers **how** a whole-window read reached it.

    These two reads are pure pass-throughs, so a figure cannot pin them: an argument dropped on the
    way down reads back as a chart drawn without its access boundary or a card built without the
    filter the dashboard asked for, and both come out as plausible money. What arrives is the whole
    of the assertion, so what arrives is what is recorded.
    """

    def __init__(self) -> None:
        self.bucket_calls: list[tuple] = []
        self.group_calls: list[tuple] = []
        self.group_batch_calls: list[tuple] = []
        self.dimension_calls: list[tuple] = []

    async def aggregate_by_time_bucket(self, period, width, resolved_ids=None, access=None, report_filter=None):
        self.bucket_calls.append((period, width, resolved_ids, access, report_filter))
        return {}

    async def aggregate_by_spend_group(self, period, *, access=None, report_filter=None):
        self.group_calls.append((period, {"access": access, "report_filter": report_filter}))
        return {}

    async def aggregate_by_spend_group_per_filter(self, period, *, access=None, common_filter, report_filters):
        self.group_batch_calls.append((period, access, common_filter, report_filters))
        return {}

    async def aggregate_by_dimension(
        self, period, dimension, values, ids_by_value=None, access=None, report_filter=None
    ):
        self.dimension_calls.append((period, dimension, tuple(values), ids_by_value, access, report_filter))
        return {}


class TestTheWholeWindowReadsPassThroughUnadjusted:
    """The bucketed split and the summary cards: this engine widens the name set and nothing else."""

    @staticmethod
    def _caller() -> tuple[AccessScope, ReportFilter]:
        """One boundary and one filter, distinct from each other so a swap cannot pass."""
        return (
            AccessScope(unrestricted=False, user_id=USER, plain_projects=(PROJECT,)),
            ReportFilter(users=(UserTerm(user_id=OTHER_USER, aliases=frozenset()),), projects=(OTHER_PROJECT,)),
        )

    def test_the_bucketed_read_forwards_every_argument_unchanged(self):
        adapter = _RecordingReadAdapter()
        access, report_filter = self._caller()
        resolved_ids = {TrafficClass.CLI: ("req-1",)}

        asyncio.run(
            LegacySpendLedger(adapter).aggregate_by_time_bucket(PERIOD, "week", resolved_ids, access, report_filter)
        )

        assert adapter.bucket_calls == [(PERIOD, "week", resolved_ids, access, report_filter)]

    def test_the_group_read_forwards_the_boundary_and_the_filter_by_keyword(self):
        """Both are keyword-only on the adapter, so a positional hand-off would not arrive at all."""
        adapter = _RecordingReadAdapter()
        access, report_filter = self._caller()

        asyncio.run(
            LegacySpendLedger(adapter).aggregate_by_spend_group(PERIOD, access=access, report_filter=report_filter)
        )

        assert adapter.group_calls == [(PERIOD, {"access": access, "report_filter": report_filter})]

    def test_the_per_filter_group_read_forwards_every_argument_unchanged(self):
        adapter = _RecordingReadAdapter()
        access, report_filter = self._caller()
        common_filter = ReportFilter(projects=(PROJECT,))
        report_filters = [report_filter, common_filter]

        asyncio.run(
            LegacySpendLedger(adapter).aggregate_by_spend_group_per_filter(
                PERIOD, access=access, common_filter=common_filter, report_filters=report_filters
            )
        )

        assert adapter.group_batch_calls == [(PERIOD, access, common_filter, report_filters)]

    def test_the_dimension_read_forwards_every_argument_unchanged(self):
        """The assistant, workflow and model tables' pre-floor money: a filter dropped here shows a
        filtered table each value's spend in every project (RD-D1)."""
        adapter = _RecordingReadAdapter()
        access, report_filter = self._caller()

        asyncio.run(
            LegacySpendLedger(adapter).aggregate_by_dimension(
                PERIOD, SpendDimension.ASSISTANT, ["asst-1"], None, access, report_filter
            )
        )

        assert adapter.dimension_calls == [(PERIOD, SpendDimension.ASSISTANT, ("asst-1",), None, access, report_filter)]


class TestAPageOfRequestsHasNoIdLookupHere:
    def test_every_request_is_unavailable(self):
        """The cost was computed locally at call time and is already on the record."""
        ledger, _repo = legacy_ledger([money_row("1.00")])

        costs = asyncio.run(ledger.costs_for_requests(["q-1"]))

        assert costs == {"q-1": RequestCost.unavailable()}
