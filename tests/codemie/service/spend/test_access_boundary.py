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

"""The access boundary **threaded** through the port, and the two config values that never arrived.

Two independent defects, both of the shape where nothing raises and a plausible number is returned.

**One.** Every Elasticsearch analytics read is narrowed at construction by `SecureQueryBuilder`. A
refactor moved money columns onto the spend port; the row set stayed narrowed and the figure stopped
being, so a plain member's own rows were footed with the whole platform's spend. The figures
themselves are asserted over real rows in `tests/codemie/repository/test_spend_logs_access_boundary.py`
and the clause's two renderings are pinned against each other in `test_access_scope.py`. What is
asserted **here** is that the boundary actually travels: through the facade, into both adapters, and
into both of the SQL reads a grouped figure is built from.

**The same defect reached the holder reads, and there the fix has to be half-hearted on purpose.**
`consumed_for_holder(s)` serves two questions with one statement: a *report* - the users-spending and
projects-spending tables - must be narrowed to what its caller may see, because the row set beside the
figure already is; a *budget* read must not be, because consumption is a property of the holder and a
narrowed one would let a person's spend escape their own limit whenever the reader could not see the
project it landed in. So the boundary there is optional, `None` by default, and the classes at the
bottom of this file pin both halves: that a report's boundary arrives everywhere, and that a budget's
absence of one survives all the way into the SQL.

**Two.** `SpendService` never handed the configured emission-lag allowance `L` or paging threshold `N`
to the id resolver, so every metric-store window used the 24 h default however the deployment was
configured. Under-widening drops a document into the residue silently - AD-29: "under-widening is a
defect and over-widening is not" - and the scenario is worked below with the numbers that reproduce it.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from codemie.enterprise.litellm.spend_ledger import SpendLogsLedger
from codemie.service.spend.coverage import LedgerCoverage, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.cutover import CutoverSpendLedger
from codemie.service.spend.default_ledger import DefaultSpendLedger
from codemie.service.spend.identity_bridge import identity_bridge_portion
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.ledger import (
    AccessScope,
    DimensionScope,
    PersonTotalHolder,
    ProjectHolder,
    ReportFilter,
    SpendLedger,
    UserTerm,
)
from codemie.service.spend.legacy_ledger import LegacySpendLedger
from codemie.service.spend.ledger_registry import _NoopSpendLedger, register_ledger_coverage, reset_ledgers
from codemie.service.spend.routing import LLMCallClass
from codemie.service.spend.spend_service import SpendService

PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))
SERVICE = SpendService()

ALICE_SCOPE = AccessScope(unrestricted=False, user_id="alice", plain_projects=("A",))
NO_PROJECTS_SCOPE = AccessScope(unrestricted=False, user_id="carol")

#: The holder a users-spending row is footed with - a person's total across everything, both pots.
PERSON = PersonTotalHolder(user_id="bob", aliases=frozenset({"bob@example.com"}))

#: A project-keyed holder, which is the kind that carries the project-dimension residue.
PROJECT = ProjectHolder(project="A", category=BudgetCategory.PLATFORM)


#: Distinguishes "the argument never arrived" from "the argument arrived as `None`". The second is a
#: read with no caller and narrows nothing on purpose; the first is a read that forgot its boundary.
_UNSET = object()


#: Every implementation of the port, the `Protocol` included, so no adapter is reachable without the
#: boundary. The two composing ledgers are here for the reason the leaf adapters are: a wrapper that
#: dropped an argument on its way through would leave the leaves conformant and the deployment wide
#: open, and the wrapper is the only object a consumer actually holds once a cutover is registered.
_IMPLEMENTATIONS = [
    pytest.param(SpendLedger, id="protocol"),
    pytest.param(DefaultSpendLedger, id="default"),
    pytest.param(SpendLogsLedger, id="spend_logs"),
    pytest.param(_NoopSpendLedger, id="noop"),
    pytest.param(LegacySpendLedger, id="legacy"),
    pytest.param(CutoverSpendLedger, id="cutover"),
]


class _StubMetrics:
    """Records the query bodies it is asked for and answers with a fixed money total."""

    def __init__(self, money: float = 0.0) -> None:
        self.bodies: list[dict] = []
        self._money = money

    async def execute_aggregation_query(self, body: dict, **_kwargs) -> dict:
        self.bodies.append(body)
        buckets = {key: {"money": {"value": self._money}} for key in self._bucket_keys(body)}
        return {"aggregations": {"values": {"buckets": buckets}, "money": {"value": self._money}}}

    @staticmethod
    def _bucket_keys(body: dict) -> list[str]:
        values = body.get("aggs", {}).get("values", {})
        return list(values.get("filters", {}).get("filters", {}))

    def filter_clauses(self) -> list[dict]:
        return self.bodies[0]["query"]["bool"]["filter"]


class _StubSpendLogsRepository:
    """Records the boundary each of the two grouped SQL reads was given.

    `_UNSET` rather than `None`, because `None` is a real value the port passes to mean *no caller*: a
    double unable to tell the two apart would pass a test that never threaded the argument at all.
    """

    def __init__(self) -> None:
        self.figure_access: object = _UNSET
        self.residue_access: object = _UNSET
        self.holder_access: object = _UNSET
        self.holder_batch_access: object = _UNSET
        self.holder_batch_filter: object = _UNSET
        self.project_residue_access: object = _UNSET

    async def sum_for_dimension_values(
        self, session, period, dimension, ids_by_value, access=_UNSET, report_filter=_UNSET
    ):
        self.figure_access = access
        return {value: Decimal("1") for value in ids_by_value}

    async def dimension_residue(self, session, period, scope, resolved_ids, access=_UNSET, report_filter=_UNSET):
        self.residue_access = access
        # A non-zero row count so the portion is actually built, which is what reads the argument.
        return Decimal("2"), 3

    async def sum_for_holder(self, session, holder, period, access=_UNSET):
        self.holder_access = access
        return Decimal("1")

    async def sum_for_holders(self, session, holders, period, access=_UNSET, report_filter=_UNSET):
        self.holder_batch_access = access
        self.holder_batch_filter = report_filter
        return {holder: Decimal("1") for holder in holders}

    async def project_dimension_residue(self, session, sub_range, access=_UNSET):
        self.project_residue_access = access
        # A non-zero row count so the portion is actually built, which is what reads the argument.
        return Decimal("2"), 3


class _StubSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _spend_logs_ledger() -> tuple[SpendLogsLedger, _StubSpendLogsRepository]:
    repository = _StubSpendLogsRepository()
    coverage = LedgerCoverage(coverage_floor=datetime(2026, 1, 1, tzinfo=timezone.utc), ready=True)
    return SpendLogsLedger(coverage, session_factory=_StubSession, repository=repository), repository


class _StubLedger:
    """A ledger that records the boundary the facade handed it."""

    ledger_name = "stub"

    def __init__(self) -> None:
        self.access_seen: list = []
        self.holder_access_seen: list = []
        self.holder_filter_seen: list = []

    async def aggregate_by_dimension(
        self, period, dimension, values, ids_by_value=None, access=_UNSET, report_filter=_UNSET
    ):
        self.access_seen.append(access)
        return {value: SpendResult(figure=Decimal("1")) for value in values}

    async def consumed_for_holder(self, holder, period, access=_UNSET):
        self.holder_access_seen.append(access)
        return SpendResult(figure=Decimal("1"))

    async def consumed_for_holders(self, holders, period, access=_UNSET, *, report_filter=_UNSET):
        self.holder_access_seen.append(access)
        self.holder_filter_seen.append(report_filter)
        return {holder: SpendResult(figure=Decimal("1")) for holder in holders}


def _registry(mapping):
    return patch("codemie.service.spend.spend_service.ledgers_by_call_class", return_value=mapping)


class TestTheBoundaryCannotBeForgottenAtTheFacade:
    """A missing argument must be a `TypeError`, never a platform-wide answer."""

    def test_the_facade_requires_the_boundary_and_takes_it_by_keyword(self) -> None:
        parameter = inspect.signature(SpendService.aggregate_by_dimension).parameters["access"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty, (
            "a default would let a consumer that forgot the boundary be answered platform-wide, "
            "silently, which is the disclosure itself"
        )

    def test_omitting_it_raises_rather_than_answering(self) -> None:
        led = _StubLedger()
        with _registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with pytest.raises(TypeError):
                asyncio.run(SERVICE.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["a-1"]))

    def test_no_caller_has_to_be_written_out_as_none(self) -> None:
        """The absence is expressible - a scheduled job has no caller - but only deliberately."""
        led = _StubLedger()
        with _registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch.object(SpendService, "_metric_store_ids_by_value", return_value={}):
                asyncio.run(SERVICE.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["a-1"], access=None))
        assert led.access_seen == [None]

    def test_the_scope_reaches_every_distinct_ledger(self) -> None:
        chat, embeddings = _StubLedger(), _StubLedger()
        with _registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids_by_value", return_value={}):
                asyncio.run(
                    SERVICE.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["a-1"], access=ALICE_SCOPE)
                )
        assert chat.access_seen == [ALICE_SCOPE]
        assert embeddings.access_seen == [ALICE_SCOPE], "a boundary honoured by one adapter is not a boundary"


class TestEveryImplementationOfThePortAcceptsTheBoundary:
    """The `Protocol` and every implementation of it, so no adapter is reachable without it."""

    @pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
    def test_the_signature_carries_access(self, implementation) -> None:
        assert "access" in inspect.signature(implementation.aggregate_by_dimension).parameters

    def test_the_noop_ledger_still_reports_unavailable_rather_than_a_narrowed_zero(self) -> None:
        """An unavailable state discloses nothing, so narrowing it could only turn it into a zero."""
        answers = asyncio.run(
            _NoopSpendLedger().aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["a-1"], None, ALICE_SCOPE)
        )
        assert answers["a-1"].figure is None


class TestTheSpendLogsLedgerNarrowsBothReads:
    """Narrowing only the figure leaves the residue publishing the size of spend the caller may not
    see; narrowing only the residue leaves the leak. Both, or neither is worth anything."""

    def test_the_figure_read_is_given_the_boundary(self) -> None:
        led, repository = _spend_logs_ledger()
        asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["a-1"], None, ALICE_SCOPE))
        assert repository.figure_access == ALICE_SCOPE

    def test_the_residue_read_is_given_the_same_boundary(self) -> None:
        led, repository = _spend_logs_ledger()
        asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["a-1"], None, ALICE_SCOPE))
        assert repository.residue_access == ALICE_SCOPE

    def test_a_read_with_no_caller_passes_none_rather_than_omitting_the_argument(self) -> None:
        led, repository = _spend_logs_ledger()
        asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["a-1"]))
        assert repository.figure_access is None
        assert repository.residue_access is None


class TestTheDefaultLedgerNarrowsItsElasticsearchQuery:
    """This adapter has no SQL, so the boundary must reach its query or it does not exist at all."""

    @staticmethod
    def _read(access) -> _StubMetrics:
        repository = _StubMetrics(money=1.0)
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repository)
        asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["a-1"], None, access))
        return repository

    def test_the_boundary_is_the_clause_access_scope_renders_and_not_a_second_copy_of_it(self) -> None:
        """Re-spelling the clause here is how the two halves come to disagree, so what lands in the
        query must be exactly what the scope itself produces."""
        clauses = self._read(ALICE_SCOPE).filter_clauses()
        expected = ALICE_SCOPE.elasticsearch_filter(
            user_id_field="attributes.user_id.keyword", project_field="attributes.project.keyword"
        )
        assert expected in clauses

    def test_it_is_anded_with_the_rest_so_its_own_or_stays_parenthesised(self) -> None:
        """A `filter` list is an `AND` and the boundary is one nested `bool` inside it. Flattened into
        the list, its two `should` clauses would each admit rows on their own."""
        clauses = self._read(ALICE_SCOPE).filter_clauses()
        boundary = [clause for clause in clauses if "bool" in clause]
        assert len(boundary) == 1
        assert set(boundary[0]["bool"]) == {"should", "minimum_should_match"}
        # The metric-name and time bounds are still there beside it.
        assert any("terms" in clause and "metric_name.keyword" in clause["terms"] for clause in clauses)
        assert any("range" in clause for clause in clauses)

    def test_an_unrestricted_caller_adds_no_clause_at_all(self) -> None:
        clauses = self._read(AccessScope.everything()).filter_clauses()
        assert not [clause for clause in clauses if "bool" in clause]

    def test_a_read_with_no_caller_adds_no_clause_either(self) -> None:
        assert not [clause for clause in self._read(None).filter_clauses() if "bool" in clause]

    def test_a_caller_with_no_accessible_project_matches_nothing_rather_than_everything(self) -> None:
        clauses = self._read(NO_PROJECTS_SCOPE).filter_clauses()
        boundary = next(clause for clause in clauses if "bool" in clause)
        assert boundary["bool"]["should"] == []
        assert boundary["bool"]["minimum_should_match"] == 0

    def test_the_holder_batch_beside_it_is_not_narrowed_by_a_leaked_default(self) -> None:
        """The two reads share one helper, and a boundary defaulted on in it would narrow a budget
        figure by whichever caller last asked for a table."""
        repository = _StubMetrics(money=1.0)
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repository)
        asyncio.run(led.aggregate(PERIOD))
        assert not [clause for clause in repository.filter_clauses() if "bool" in clause]


class TestTheConfiguredWindowReachesTheIdResolver:
    """Task 2, the second defect: `L` and `N` were read at the composition root and never passed on."""

    @pytest.fixture(autouse=True)
    def _registered_coverage(self):
        """72 h and 7 - both different from the defaults, so a test cannot pass on a fallback."""
        register_ledger_coverage(
            LedgerCoverage(
                coverage_floor=datetime(2026, 1, 1, tzinfo=timezone.utc),
                emission_lag=timedelta(hours=72),
                max_elasticsearch_ids=7,
                ready=True,
            )
        )
        yield
        reset_ledgers()

    def test_the_single_value_resolver_is_given_both(self) -> None:
        resolver = AsyncMock(return_value=())
        with patch("codemie.service.spend.dimension_scope.resolve_metric_store_ids", resolver):
            asyncio.run(SERVICE._metric_store_ids(DimensionScope(SpendDimension.ASSISTANT, "a-1"), PERIOD, None))

        assert resolver.await_args.kwargs["emission_lag"] == timedelta(hours=72)
        assert resolver.await_args.kwargs["page_size"] == 7

    def test_the_grouped_resolver_is_given_both(self) -> None:
        resolver = AsyncMock(return_value={})
        with patch("codemie.service.spend.dimension_scope.resolve_metric_store_ids_by_value", resolver):
            asyncio.run(SERVICE._metric_store_ids_by_value(SpendDimension.ASSISTANT, ["a-1"], PERIOD))

        assert resolver.await_args.kwargs["emission_lag"] == timedelta(hours=72)
        assert resolver.await_args.kwargs["page_size"] == 7

    def test_the_window_actually_widens_by_the_configured_allowance(self) -> None:
        """The failing scenario, end to end and with its numbers: a run starting 31 August emits its
        metric on 3 September, so an August report must widen to **4 September**. At the hard-coded
        24 h default it widened to the 2nd, missed the document, and dropped the row into the residue.
        """
        metrics = _StubMetrics()
        with patch("codemie.repository.metrics_elastic_repository.MetricsElasticRepository", return_value=metrics):
            asyncio.run(SERVICE._metric_store_ids(DimensionScope(SpendDimension.ASSISTANT, "a-1"), PERIOD, None))

        window = next(
            clause["range"]["@timestamp"]
            for clause in metrics.bodies[0]["query"]["bool"]["filter"]
            if "range" in clause
        )
        assert window["lt"] == datetime(2026, 9, 4, tzinfo=timezone.utc).isoformat()
        assert window["gte"] == datetime(2026, 7, 29, tzinfo=timezone.utc).isoformat()

    def test_the_configured_paging_threshold_sizes_the_composite(self) -> None:
        """`N` is a paging threshold and never a truncation point, so the size it must carry is the
        configured one - a deployment that raised it to page in fewer round trips was still at 50 000.
        """
        metrics = _StubMetrics()
        with patch("codemie.repository.metrics_elastic_repository.MetricsElasticRepository", return_value=metrics):
            asyncio.run(SERVICE._metric_store_ids(DimensionScope(SpendDimension.ASSISTANT, "a-1"), PERIOD, None))

        assert metrics.bodies[0]["aggs"]["ids"]["composite"]["size"] == 7


class TestAnUnregisteredCoverageIsNotAWidenedGuess:
    """With no composition root run, the defaults stand - and they are the type's own, never a
    lazily re-read config value: a floor re-read deep in a reader is how two surfaces come to sit on
    opposite sides of one activation instant."""

    def test_nothing_registered_falls_back_to_the_documented_defaults(self) -> None:
        from codemie.service.spend.coverage import DEFAULT_EMISSION_LAG, DEFAULT_MAX_ELASTICSEARCH_IDS
        from codemie.service.spend.ledger_registry import ledger_coverage

        reset_ledgers()
        coverage = ledger_coverage()
        assert coverage.emission_lag == DEFAULT_EMISSION_LAG
        assert coverage.max_elasticsearch_ids == DEFAULT_MAX_ELASTICSEARCH_IDS
        # And no figure is served against a floor nobody published.
        assert not coverage.activated

    def test_a_reset_clears_the_coverage_so_one_test_cannot_leak_into_the_next(self) -> None:
        from codemie.service.spend.ledger_registry import ledger_coverage

        register_ledger_coverage(LedgerCoverage(emission_lag=timedelta(hours=99)))
        reset_ledgers()
        assert ledger_coverage().emission_lag != timedelta(hours=99)


# ---------------------------------------------------------------------------
# The holder reads: one statement, two questions, and only one of them narrows
# ---------------------------------------------------------------------------


class TestTheHolderReadTakesTheBoundaryWithoutDemandingIt:
    """The facade's contract, which is deliberately **not** `aggregate_by_dimension`'s.

    There the argument is mandatory because every consumer is a caller-facing table, so a missing one
    can only be a forgotten boundary. Here a budget path is an equally legitimate consumer and must
    not be made to write `access=None` a dozen times to say so - a boundary written as a ritual stops
    being read as a decision.
    """

    def test_the_boundary_is_keyword_only_so_a_call_site_names_it(self) -> None:
        for method in (SpendService.consumed_for_holder, SpendService.consumed_for_holders):
            parameter = inspect.signature(method).parameters["access"]
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    def test_omitting_it_is_a_budget_read_rather_than_a_type_error(self) -> None:
        """The enforcement half of the contract: a budget's consumption is a property of the budget,
        so a read with no caller answers unnarrowed rather than refusing to answer."""
        led = _StubLedger()
        with _registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            asyncio.run(SERVICE.consumed_for_holders([PERSON], PERIOD))
        assert led.holder_access_seen == [None]

    def test_a_report_that_names_its_boundary_hands_it_to_every_distinct_ledger(self) -> None:
        chat, embeddings = _StubLedger(), _StubLedger()
        with _registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            asyncio.run(SERVICE.consumed_for_holders([PERSON], PERIOD, access=ALICE_SCOPE))
        assert chat.holder_access_seen == [ALICE_SCOPE]
        assert embeddings.holder_access_seen == [ALICE_SCOPE], "a boundary honoured by one adapter is not a boundary"

    def test_the_single_holder_path_threads_it_the_same_way(self) -> None:
        """The batch must equal the single-holder read for the same holder, period **and** boundary,
        so a boundary reaching only one of the two would break the port's own equality."""
        led = _StubLedger()
        with _registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            asyncio.run(SERVICE.consumed_for_holder(PERSON, PERIOD, access=ALICE_SCOPE))
        assert led.holder_access_seen == [ALICE_SCOPE]


class TestEveryImplementationOfThePortAcceptsTheBoundaryOnAHolderRead:
    """The `Protocol` and every implementation of it, so no adapter is reachable without it."""

    @pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
    @pytest.mark.parametrize("method", ["consumed_for_holder", "consumed_for_holders"])
    def test_the_signature_carries_access(self, implementation, method) -> None:
        assert "access" in inspect.signature(getattr(implementation, method)).parameters

    @pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
    def test_the_batch_takes_the_dashboards_filter_by_keyword_and_defaults_it_to_no_filter(
        self, implementation
    ) -> None:
        """Keyword-only, so it cannot land in the boundary's position, and defaulted to `None`, so the
        budget paths that never name it keep reading a holder's whole consumption."""
        parameter = inspect.signature(implementation.consumed_for_holders).parameters["report_filter"]

        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None

    def test_the_noop_ledger_still_reports_unavailable_rather_than_a_narrowed_zero(self) -> None:
        """An unavailable state discloses nothing, so narrowing it could only turn it into a zero."""
        answers = asyncio.run(_NoopSpendLedger().consumed_for_holders([PERSON], PERIOD, ALICE_SCOPE))
        assert answers[PERSON].figure is None
        assert asyncio.run(_NoopSpendLedger().consumed_for_holder(PERSON, PERIOD, ALICE_SCOPE)).figure is None


class TestTheSpendLogsLedgerNarrowsTheHolderFigureAndItsResidue:
    """Narrowing only the figure leaves the residue publishing the size of spend the caller may not
    see; narrowing only the residue leaves the leak. Both, or neither is worth anything."""

    def test_the_batched_figure_read_is_given_the_boundary(self) -> None:
        led, repository = _spend_logs_ledger()
        asyncio.run(led.consumed_for_holders([PERSON], PERIOD, ALICE_SCOPE))
        assert repository.holder_batch_access == ALICE_SCOPE

    def test_the_single_holder_figure_read_is_given_it_too(self) -> None:
        led, repository = _spend_logs_ledger()
        asyncio.run(led.consumed_for_holder(PERSON, PERIOD, ALICE_SCOPE))
        assert repository.holder_access == ALICE_SCOPE

    def test_the_project_residue_beside_a_batched_figure_is_given_the_same_boundary(self) -> None:
        """A portion is money *and* a row count printed beside the figure, so an unnarrowed one names
        a hole none of the caller's rows is in and states its size while doing so."""
        led, repository = _spend_logs_ledger()
        asyncio.run(led.consumed_for_holders([PROJECT], PERIOD, ALICE_SCOPE))
        assert repository.project_residue_access == ALICE_SCOPE

    def test_the_project_residue_beside_a_single_figure_is_given_it_as_well(self) -> None:
        led, repository = _spend_logs_ledger()
        asyncio.run(led.consumed_for_holder(PROJECT, PERIOD, ALICE_SCOPE))
        assert repository.project_residue_access == ALICE_SCOPE

    def test_a_budget_read_passes_none_all_the_way_into_the_sql(self) -> None:
        """**The enforcement invariant, and the one this whole parameter is defaulted for.** A budget's
        consumption may not be narrowed by whoever is looking, or spend escapes its own limit as soon
        as the reader cannot see the project it landed in. `_UNSET` here would mean the argument never
        arrived at all, which is a different failure and would hide this one."""
        led, repository = _spend_logs_ledger()
        asyncio.run(led.consumed_for_holders([PROJECT], PERIOD))
        assert repository.holder_batch_access is None
        assert repository.project_residue_access is None


class TestTheSpendLogsLedgerCarriesTheDashboardsFilterIntoTheHolderBatch:
    """The users- and projects-spending tables' money, filtered as their rows were.

    An admin filtering the dashboard to one project was shown a person's spend across every project,
    because the batch carried the boundary and not the filter. `_UNSET` keeps an argument that never
    arrived apart from the `None` a budget read passes on purpose.
    """

    ASKED = ReportFilter(projects=("A",))

    def test_the_batched_figure_read_is_given_the_filter(self) -> None:
        led, repository = _spend_logs_ledger()
        asyncio.run(led.consumed_for_holders([PERSON], PERIOD, ALICE_SCOPE, report_filter=self.ASKED))
        assert repository.holder_batch_filter == self.ASKED

    def test_a_filter_on_an_unbridged_person_labels_every_holder_figure(self) -> None:
        """The scan matches that person's canonical id alone, so each figure is short by the fallback
        rows nobody can key back to them - by an amount nobody can compute."""
        led, _ = _spend_logs_ledger()
        asked = ReportFilter(users=(UserTerm("bob", None),))

        results = asyncio.run(led.consumed_for_holders([PERSON, PROJECT], PERIOD, report_filter=asked))

        for holder, result in results.items():
            assert result.partial[SpendDimension.IDENTITY_BRIDGE] == identity_bridge_portion(), holder

    def test_a_budget_read_passes_no_filter_into_the_sql(self) -> None:
        """A budget's consumption is not whatever share of it one dashboard's filter selected."""
        led, repository = _spend_logs_ledger()
        asyncio.run(led.consumed_for_holders([PROJECT], PERIOD))
        assert repository.holder_batch_filter is None


class TestTheDefaultLedgerNarrowsItsHolderQueries:
    """This adapter has no SQL, so the boundary must reach its query or it does not exist at all."""

    @staticmethod
    def _batch(access, holder=PERSON) -> _StubMetrics:
        repository = _StubMetrics(money=1.0)
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repository)
        asyncio.run(led.consumed_for_holders([holder], PERIOD, access))
        return repository

    @staticmethod
    def _single(access, holder=PERSON) -> _StubMetrics:
        repository = _StubMetrics(money=1.0)
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repository)
        asyncio.run(led.consumed_for_holder(holder, PERIOD, access))
        return repository

    def test_the_batched_query_carries_the_clause_access_scope_renders(self) -> None:
        """Re-spelling the clause here is how the two halves come to disagree, so what lands in the
        query must be exactly what the scope itself produces."""
        expected = ALICE_SCOPE.elasticsearch_filter(
            user_id_field="attributes.user_id.keyword", project_field="attributes.project.keyword"
        )
        assert expected in self._batch(ALICE_SCOPE).filter_clauses()

    def test_the_single_holder_query_carries_the_same_clause(self) -> None:
        expected = ALICE_SCOPE.elasticsearch_filter(
            user_id_field="attributes.user_id.keyword", project_field="attributes.project.keyword"
        )
        assert expected in self._single(ALICE_SCOPE).filter_clauses()

    def test_it_narrows_the_outer_query_rather_than_one_holders_bucket(self) -> None:
        """The boundary belongs to the caller and not to a holder, so one clause in the shared filter
        cannot be applied to some buckets and forgotten on others."""
        body = self._batch(ALICE_SCOPE).bodies[0]
        assert any("bool" in clause for clause in body["query"]["bool"]["filter"])
        buckets = body["aggs"]["holders"]["filters"]["filters"]
        assert "alice" not in repr(buckets), "the boundary must not be copied into each bucket"

    def test_an_unrestricted_caller_adds_no_clause_at_all(self) -> None:
        assert not [c for c in self._batch(AccessScope.everything()).filter_clauses() if "bool" in c]

    def test_a_budget_read_with_no_caller_adds_no_clause_either(self) -> None:
        """`None` and `everything()` narrow the same amount - nothing - and are spelled differently so
        a read that merely forgot its boundary stays findable."""
        assert not [c for c in self._batch(None).filter_clauses() if "bool" in c]

    def test_a_caller_with_no_accessible_project_matches_nothing_rather_than_everything(self) -> None:
        boundary = next(c for c in self._batch(NO_PROJECTS_SCOPE).filter_clauses() if "bool" in c)
        assert boundary["bool"]["should"] == []
        assert boundary["bool"]["minimum_should_match"] == 0


# ---------------------------------------------------------------------------
# The two whole-window reads: a boundary *and* a filter, on every implementation
# ---------------------------------------------------------------------------


class TestEveryImplementationOfThePortAcceptsTheBoundaryAndTheFilterOnAWholeWindowRead:
    """The split chart and the summary cards, on every object a consumer can be holding.

    Both reads sit on a dashboard `SecureQueryBuilder` narrowed at construction *and* filtered by the
    user's own choice of people and projects. An adapter missing either argument answers wider than
    the rows printed beside it - the boundary case is a disclosure, the filter case a figure that
    silently ignores what the dashboard was asked - and both render as an ordinary number.

    **The composing ledgers are parametrised alongside the leaves deliberately.** Once a cutover is
    registered it is the only object a consumer holds, so a wrapper that dropped an argument on its
    way through would leave every leaf adapter conformant and the deployment wide open.
    """

    @pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
    def test_the_bucketed_read_ends_with_the_boundary_then_the_filter(self, implementation) -> None:
        """The **order** as well as the presence: the cutover passes both positionally to its halves,
        so an adapter declaring them the other way round would receive a filter as a boundary and
        narrow a dashboard by a permission scope. Nothing raises - both are typed `| None`."""
        parameters = list(inspect.signature(implementation.aggregate_by_time_bucket).parameters)

        assert parameters[-2:] == ["access", "report_filter"]

    @pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
    def test_the_spend_group_read_takes_both_by_keyword_only(self, implementation) -> None:
        """Keyword-only on this one, because it has no `resolved_ids` to sit behind: two same-typed
        optional arguments in a row are the pair a call site transposes, and a name cannot be."""
        parameters = inspect.signature(implementation.aggregate_by_spend_group).parameters

        for name in ("access", "report_filter"):
            assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY

    @pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
    def test_the_batched_spend_group_read_takes_its_arguments_by_keyword_only(self, implementation) -> None:
        """The same reason, sharper: `common_filter` and each of `report_filters` are both filters, and a
        transposed pair would narrow every row by the dashboard's filter alone."""
        parameters = inspect.signature(implementation.aggregate_by_spend_group_per_filter).parameters

        for name in ("access", "common_filter", "report_filters"):
            assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
