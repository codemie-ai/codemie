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

The rules under test are the ones a wrong implementation satisfies silently: adding one ledger's
answer twice, dropping an unavailable flag off a partial total, and presenting a partly-settled cost
as final.
"""

import asyncio
import inspect
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from codemie.service.monitoring.metrics_constants import (
    SPEND_LEDGER_READ_DURATION_METRIC,
    SPEND_LEDGER_READ_FAILURES_METRIC,
)
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import (
    AccessScope,
    ComposedScope,
    PersonTotalHolder,
    ReportFilter,
    RequestCost,
    UserTerm,
    bucket_starts,
)
from codemie.service.spend.routing import LLMCallClass
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend import spend_service
from codemie.service.spend.spend_service import SpendService
from codemie.service.spend.traffic_class import TrafficClass
from tests.codemie.service.spend.periods import PERIOD

USER = "aaaaaaaa-1111-1111-1111-111111111111"
SERVICE = SpendService()

#: Three days at a day's width - nine keys short of a month's chart, and the same walk.
SHORT_PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 8, 4, tzinfo=timezone.utc))
WIDTH = "day"

ALICE_SCOPE = AccessScope(unrestricted=False, user_id="alice", plain_projects=("A",))
DASHBOARD_FILTER = ReportFilter(users=(UserTerm(user_id="alice", aliases=frozenset({"alice@example.com"})),))

#: A projects-filtered users table: the dashboard's term, and each row's filter carrying it unchanged.
COMMON_FILTER = ReportFilter(projects=("A",))
ALICE_ROW = ReportFilter(users=(UserTerm(user_id="alice", aliases=frozenset({"alice@example.com"})),), projects=("A",))
BOB_ROW = ReportFilter(users=(UserTerm(user_id="bob", aliases=frozenset({"bob@example.com"})),), projects=("A",))

#: Distinguishes "the argument never arrived" from "the argument arrived as `None`". The second is a
#: read with no caller and no filter and narrows nothing on purpose; the first is a ledger the facade
#: never threaded the argument to, which a `None` assertion would pass over.
_UNSET = object()


class _StubLedger:
    def __init__(
        self,
        name,
        result=None,
        cost=None,
        count=0,
        *,
        buckets=None,
        groups=None,
        groups_by_filter=None,
        request_costs=None,
    ):
        self.ledger_name = name
        self._result = result if result is not None else SpendResult(figure=Decimal("10"))
        self._cost = cost or RequestCost.unavailable()
        self._request_costs = dict(request_costs or {})
        self.request_ids_seen: list[list[str]] = []
        self._count = count
        self._buckets = dict(buckets or {})
        self._groups = dict(groups or {})
        self._groups_by_filter = dict(groups_by_filter or {})
        self.holder_calls = 0
        self.aggregate_calls = 0
        self.bucket_calls = 0
        self.group_calls = 0
        self.composed_calls = 0
        self.resolved_ids_seen: list[tuple] = []
        self.dimension_ids_seen: list[tuple] = []
        self.class_ids_seen: list[tuple] = []
        self.access_seen: list = []
        self.filter_seen: list = []
        self.report_filters_seen: list = []

    async def consumed_for_holder(self, holder, period, access=None):
        self.holder_calls += 1
        return self._result

    async def consumed_for_holders(self, holders, period, access=_UNSET, *, report_filter=_UNSET):
        self.holder_calls += 1
        self.access_seen.append(access)
        self.filter_seen.append(report_filter)
        return {holder: self._result for holder in holders}

    async def aggregate_by_time_bucket(self, period, width, resolved_ids=None, access=_UNSET, report_filter=_UNSET):
        self.bucket_calls += 1
        self.resolved_ids_seen.append(resolved_ids)
        self.access_seen.append(access)
        self.filter_seen.append(report_filter)
        return dict(self._buckets)

    async def aggregate_by_spend_group(self, period, *, access=_UNSET, report_filter=_UNSET):
        self.group_calls += 1
        self.access_seen.append(access)
        self.filter_seen.append(report_filter)
        return dict(self._groups)

    async def aggregate_by_spend_group_per_filter(self, period, *, access=_UNSET, common_filter=_UNSET, report_filters):
        self.group_calls += 1
        self.access_seen.append(access)
        self.filter_seen.append(common_filter)
        self.report_filters_seen.append(list(report_filters))
        return {
            report_filter: dict(self._groups_by_filter[report_filter])
            for report_filter in report_filters
            if report_filter in self._groups_by_filter
        }

    async def aggregate(self, period, scope=None, resolved_ids=()):
        self.aggregate_calls += 1
        self.resolved_ids_seen.append(resolved_ids)
        return self._result

    async def aggregate_composed(self, period, scope, *, dimension_ids=(), traffic_class_ids=()):
        self.composed_calls += 1
        self.dimension_ids_seen.append(dimension_ids)
        self.class_ids_seen.append(traffic_class_ids)
        return self._result

    async def cost_for_request(self, *, run_id=None, request_id=None):
        return self._cost

    async def costs_for_requests(self, request_ids):
        self.request_ids_seen.append(list(request_ids))
        return {
            request_id: self._request_costs[request_id]
            for request_id in request_ids
            if request_id in self._request_costs
        }

    async def request_count(self, period, scope=None, resolved_ids=()):
        return self._count


def registry(mapping):
    return patch("codemie.service.spend.spend_service.ledgers_by_call_class", return_value=mapping)


HOLDER = PersonTotalHolder(user_id=USER, aliases=frozenset({"alice"}))


class TestOneLedgerServingBothClassesIsAskedOnce:
    """The trap. Asking per class and adding reports everything that ledger holds TWICE."""

    def test_a_single_ledger_deployment_is_not_double_counted(self):
        only = _StubLedger("spend_logs", SpendResult(figure=Decimal("10")))
        with registry({LLMCallClass.CHAT: only, LLMCallClass.EMBEDDINGS: only}):
            result = asyncio.run(SERVICE.consumed_for_holder(HOLDER, PERIOD))

        assert only.holder_calls == 1, "the same ledger must not be asked once per call class"
        assert result.figure == Decimal("10"), "10 + 10 would be the double count this guards"

    def test_two_ledgers_are_both_asked_and_summed(self):
        """The mixed deployment: a holder's budget spans classes, so the figures DO add."""
        chat = _StubLedger("spend_logs", SpendResult(figure=Decimal("7")))
        embeddings = _StubLedger("default", SpendResult(figure=Decimal("3")))
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            result = asyncio.run(SERVICE.consumed_for_holder(HOLDER, PERIOD))

        assert chat.holder_calls == 1 and embeddings.holder_calls == 1
        assert result.figure == Decimal("10")

    def test_the_same_dedupe_applies_to_counts(self):
        only = _StubLedger("spend_logs", count=5)
        with registry({LLMCallClass.CHAT: only, LLMCallClass.EMBEDDINGS: only}):
            assert asyncio.run(SERVICE.request_count(PERIOD)) == 5


class TestNothingAnyLedgerSaidIsDropped:
    def test_an_unavailable_flag_survives_onto_a_summed_figure(self):
        """A total missing one ledger's contribution is partial; unflagged it is a truncated total."""
        covered = _StubLedger("a", SpendResult(figure=Decimal("4")))
        crossing = _StubLedger("b", SpendResult(figure=Decimal("1"), unavailable=PERIOD))
        with registry({LLMCallClass.CHAT: covered, LLMCallClass.EMBEDDINGS: crossing}):
            result = asyncio.run(SERVICE.consumed_for_holder(HOLDER, PERIOD))

        assert result.figure == Decimal("5")
        assert result.unavailable == PERIOD

    def test_a_ledger_with_no_figure_contributes_nothing_rather_than_a_zero(self):
        """It did not report zero, so it must not pull the total down as if it had."""
        has = _StubLedger("a", SpendResult(figure=Decimal("4")))
        has_not = _StubLedger("b", SpendResult.no_figure(PERIOD))
        with registry({LLMCallClass.CHAT: has, LLMCallClass.EMBEDDINGS: has_not}):
            result = asyncio.run(SERVICE.consumed_for_holder(HOLDER, PERIOD))

        assert result.figure == Decimal("4")
        assert result.unavailable == PERIOD, "the ledger that could not answer must still be named"

    def test_no_ledger_answering_yields_no_figure_and_never_a_zero(self):
        blank = _StubLedger("a", SpendResult.no_figure(PERIOD))
        with registry({LLMCallClass.CHAT: blank, LLMCallClass.EMBEDDINGS: blank}):
            result = asyncio.run(SERVICE.consumed_for_holder(HOLDER, PERIOD))

        assert result.figure is None
        assert result.unavailable == PERIOD

    def test_partial_entries_merge_per_dimension(self):
        left = _StubLedger(
            "a",
            SpendResult(figure=Decimal("1"), partial={SpendDimension.WORKFLOW: Portion(spend=Decimal("2"), rows=3)}),
        )
        right = _StubLedger(
            "b",
            SpendResult(figure=Decimal("1"), partial={SpendDimension.WORKFLOW: Portion(spend=Decimal("5"), rows=4)}),
        )
        with registry({LLMCallClass.CHAT: left, LLMCallClass.EMBEDDINGS: right}):
            result = asyncio.run(SERVICE.consumed_for_holder(HOLDER, PERIOD))

        portion = result.partial[SpendDimension.WORKFLOW]
        assert portion.spend == Decimal("7")
        assert portion.rows == 7

    def test_an_uncomputable_portion_wins_over_a_computable_one(self):
        """Rendering only the half that WAS computable understates a hole of unknown size."""
        known = _StubLedger(
            "a",
            SpendResult(
                figure=Decimal("1"),
                partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=Decimal("2"), rows=1)},
            ),
        )
        unknown = _StubLedger(
            "b",
            SpendResult(figure=Decimal("1"), partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)}),
        )
        with registry({LLMCallClass.CHAT: known, LLMCallClass.EMBEDDINGS: unknown}):
            result = asyncio.run(SERVICE.consumed_for_holder(HOLDER, PERIOD))

        portion = result.partial[SpendDimension.IDENTITY_BRIDGE]
        assert portion.spend is None and portion.rows is None


class TestTheCorrelationIdSetIsFetchedOnceAndSharedNoScope:
    def test_no_scope_fetches_nothing_and_passes_nothing(self):
        led = _StubLedger("a")
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch.object(SpendService, "_metric_store_ids") as fetch:
                asyncio.run(SERVICE.aggregate(PERIOD))
        fetch.assert_not_called()
        assert led.resolved_ids_seen == [()]

    def test_a_scoped_report_fetches_once_and_hands_the_same_set_to_every_ledger(self):
        from codemie.service.spend.ledger import DimensionScope

        chat = _StubLedger("a")
        embeddings = _StubLedger("b")
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids", return_value=("req-1",)) as fetch:
                asyncio.run(SERVICE.aggregate(PERIOD, DimensionScope(SpendDimension.ASSISTANT, "a-1")))

        assert fetch.call_count == 1, "the widened window and the paging rule live in one place"
        assert chat.resolved_ids_seen == [("req-1",)]
        assert embeddings.resolved_ids_seen == [("req-1",)]

    def test_the_union_class_ids_are_fetched_once_for_the_range_and_shared_on_the_bucket_read(self):
        """Once for the whole range, not once per bucket: the widened window a resolver reads is
        already a superset of every bucket inside it, and the ledger's own start predicate is what
        assigns a row to one."""
        chat = _StubLedger("a")
        embeddings = _StubLedger("b")
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids", return_value=("req-1",)) as fetch:
                asyncio.run(SERVICE.aggregate_by_time_bucket(SHORT_PERIOD, WIDTH, access=None))

        assert fetch.call_count == 1, "a fetch per bucket per ledger is the same answer paid for many times"
        assert chat.resolved_ids_seen == [{TrafficClass.ASSISTANT: ("req-1",)}]
        assert embeddings.resolved_ids_seen == [{TrafficClass.ASSISTANT: ("req-1",)}]

    def test_a_metric_store_failure_degrades_the_report_and_never_raises(self):
        """The loss is REPORTED through the residue label, not silently subtracted."""
        from codemie.service.spend.ledger import DimensionScope

        led = _StubLedger("a")
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch(
                "codemie.service.spend.dimension_scope.resolve_metric_store_ids",
                side_effect=RuntimeError("elasticsearch is down"),
            ):
                result = asyncio.run(SERVICE.aggregate(PERIOD, DimensionScope(SpendDimension.ASSISTANT, "a-1")))

        assert result.figure == Decimal("10")
        assert led.resolved_ids_seen == [()]

    def test_no_metric_store_means_no_ids_and_no_failure(self):
        """Without Elasticsearch there are no recorded ids to resolve, so nothing failed and nothing is counted."""
        from codemie.service.spend.ledger import DimensionScope

        led = _StubLedger("a")
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with (
                patch("codemie.clients.elasticsearch.ElasticSearchClient.is_configured", return_value=False),
                patch("codemie.service.spend.dimension_scope.resolve_metric_store_ids") as resolve,
                patch("codemie.service.spend.spend_service.BaseMonitoringService") as monitoring,
            ):
                result = asyncio.run(SERVICE.aggregate(PERIOD, DimensionScope(SpendDimension.ASSISTANT, "a-1")))

        assert result.figure == Decimal("10")
        assert led.resolved_ids_seen == [()]
        resolve.assert_not_called()
        monitoring.send_count_metric.assert_not_called()


class TestAClassTermCarriesTheUnionClassIdsWhateverClassItNames:
    """A class term selects its class *and* the residue the split leaves, and the residue is what
    needs the ids even when the class itself has a column.

    The residue is the rows no class claims, recognised by not being in the union class's id set. A
    term naming a ledger-native class handed an empty set therefore reads its residue against
    nothing and names every union-class row as unclassified: the figure is right, and the partial
    label beside it overstates the missing money by the whole assistant series.
    """

    def _composed(self, scope, ledgers):
        with registry(ledgers):
            with patch.object(SpendService, "_metric_store_ids", return_value=("req-1",)) as fetch:
                asyncio.run(SERVICE.aggregate_composed(PERIOD, scope))
        return fetch

    def test_a_native_class_term_fetches_the_union_set_once_and_every_ledger_receives_it(self):
        chat = _StubLedger("spend_logs")
        embeddings = _StubLedger("default")

        fetch = self._composed(
            ComposedScope(traffic_class=TrafficClass.CLI),
            {LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings},
        )

        assert fetch.call_count == 1, "one set for the window, fetched where the vocabulary is known"
        assert chat.class_ids_seen == [("req-1",)]
        assert embeddings.class_ids_seen == [("req-1",)]

    def test_the_union_class_term_receives_it_too(self):
        led = _StubLedger("a")

        self._composed(
            ComposedScope(traffic_class=TrafficClass.ASSISTANT),
            {LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led},
        )

        assert led.class_ids_seen == [("req-1",)]

    def test_a_scope_with_no_class_term_resolves_nothing(self):
        """An absent term is unconstrained, so there is no selector to narrow and no residue to
        recognise - fetching a set for it would be paid for and then ignored."""
        led = _StubLedger("a")

        fetch = self._composed(ComposedScope(project="proj-a"), {LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led})

        fetch.assert_not_called()
        assert led.class_ids_seen == [()]


class TestTheGrainOfAnIdLookupIsTheCallersChoice:
    @pytest.mark.parametrize("kwargs", [{}, {"run_id": "r", "request_id": "q"}])
    def test_exactly_one_id_is_required(self, kwargs):
        """Hydrating a single call by the request id multiplies the money by the loop length."""
        with pytest.raises(ValueError):
            asyncio.run(SERVICE.cost_for_request(**kwargs))

    def test_settled_figures_across_ledgers_are_summed(self):
        a = _StubLedger("a", cost=RequestCost.settled(Decimal("2")))
        b = _StubLedger("b", cost=RequestCost.settled(Decimal("3")))
        with registry({LLMCallClass.CHAT: a, LLMCallClass.EMBEDDINGS: b}):
            cost = asyncio.run(SERVICE.cost_for_request(request_id="q-1"))
        assert cost.is_settled
        assert cost.figure == Decimal("5")

    def test_a_partly_settled_total_is_pending_and_never_settled(self):
        """Returning the landed part as final is the fabricated total this design prevents."""
        landed = _StubLedger("a", cost=RequestCost.settled(Decimal("2")))
        waiting = _StubLedger("b", cost=RequestCost.pending())
        with registry({LLMCallClass.CHAT: landed, LLMCallClass.EMBEDDINGS: waiting}):
            cost = asyncio.run(SERVICE.cost_for_request(request_id="q-1"))
        assert cost.is_pending
        assert not cost.is_settled

    def test_no_ledger_able_to_answer_is_unavailable_not_pending(self):
        blank = _StubLedger("a", cost=RequestCost.unavailable())
        with registry({LLMCallClass.CHAT: blank, LLMCallClass.EMBEDDINGS: blank}):
            cost = asyncio.run(SERVICE.cost_for_request(run_id="r-1"))
        assert cost.is_unavailable

    def test_a_single_ledger_is_asked_once_for_a_cost_too(self):
        only = _StubLedger("a", cost=RequestCost.settled(Decimal("4")))
        with registry({LLMCallClass.CHAT: only, LLMCallClass.EMBEDDINGS: only}):
            cost = asyncio.run(SERVICE.cost_for_request(run_id="r-1"))
        assert cost.figure == Decimal("4"), "8 would mean the ledger was asked twice"


class TestAPageOfRequestsIsCostedInOneReadPerLedger:
    """`costs_for_requests` answers each request exactly as `cost_for_request` would, in one read per ledger."""

    def test_each_ledger_is_asked_once_with_every_distinct_id(self):
        a = _StubLedger("a", request_costs={"q-1": RequestCost.settled(Decimal("2"))})
        b = _StubLedger("b", request_costs={"q-2": RequestCost.settled(Decimal("1"))})
        with registry({LLMCallClass.CHAT: a, LLMCallClass.EMBEDDINGS: b}):
            asyncio.run(SERVICE.costs_for_requests(["q-1", "q-2", "q-1"]))

        assert a.request_ids_seen == [["q-1", "q-2"]]
        assert b.request_ids_seen == [["q-1", "q-2"]]

    def test_settled_figures_across_ledgers_are_summed_per_request(self):
        a = _StubLedger("a", request_costs={"q-1": RequestCost.settled(Decimal("2"))})
        b = _StubLedger("b", request_costs={"q-1": RequestCost.settled(Decimal("3"))})
        with registry({LLMCallClass.CHAT: a, LLMCallClass.EMBEDDINGS: b}):
            costs = asyncio.run(SERVICE.costs_for_requests(["q-1"]))

        assert costs["q-1"] == RequestCost.settled(Decimal("5"))

    def test_a_request_one_ledger_did_not_answer_for_takes_the_others_figure(self):
        a = _StubLedger("a", request_costs={"q-1": RequestCost.settled(Decimal("2"))})
        b = _StubLedger("b")
        with registry({LLMCallClass.CHAT: a, LLMCallClass.EMBEDDINGS: b}):
            costs = asyncio.run(SERVICE.costs_for_requests(["q-1"]))

        assert costs["q-1"] == RequestCost.settled(Decimal("2"))

    def test_a_partly_settled_request_is_pending(self):
        landed = _StubLedger("a", request_costs={"q-1": RequestCost.settled(Decimal("2"))})
        waiting = _StubLedger("b", request_costs={"q-1": RequestCost.pending()})
        with registry({LLMCallClass.CHAT: landed, LLMCallClass.EMBEDDINGS: waiting}):
            costs = asyncio.run(SERVICE.costs_for_requests(["q-1"]))

        assert costs["q-1"] == RequestCost.pending()

    def test_a_request_no_ledger_answered_for_is_unavailable(self):
        blank = _StubLedger("a")
        with registry({LLMCallClass.CHAT: blank, LLMCallClass.EMBEDDINGS: blank}):
            costs = asyncio.run(SERVICE.costs_for_requests(["q-1"]))

        assert costs == {"q-1": RequestCost.unavailable()}

    def test_a_single_ledger_serving_both_classes_is_asked_once(self):
        only = _StubLedger("a", request_costs={"q-1": RequestCost.settled(Decimal("4"))})
        with registry({LLMCallClass.CHAT: only, LLMCallClass.EMBEDDINGS: only}):
            costs = asyncio.run(SERVICE.costs_for_requests(["q-1"]))

        assert costs["q-1"] == RequestCost.settled(Decimal("4"))
        assert only.request_ids_seen == [["q-1"]]

    def test_no_ids_asks_no_ledger(self):
        only = _StubLedger("a")
        with registry({LLMCallClass.CHAT: only, LLMCallClass.EMBEDDINGS: only}):
            costs = asyncio.run(SERVICE.costs_for_requests([]))

        assert costs == {}
        assert only.request_ids_seen == []


def _no_metric_store_ids():
    """The union class's id set, stubbed - these tests are about composition, not about resolution."""
    return patch.object(SpendService, "_metric_store_ids", return_value=())


class TestTheWholeWindowReadsCannotBeAskedWithoutABoundary:
    """A forgotten boundary must be a `TypeError`, never a platform-wide chart.

    Both reads feed a dashboard `SecureQueryBuilder` already narrowed to its caller, so a figure
    computed without the scope foots a plain member's rows with the whole platform's spend. A default
    would make that the answer to a call that merely forgot to say - which is the disclosure itself,
    arriving silently and rendering perfectly.
    """

    @pytest.mark.parametrize(
        "method_name",
        ["aggregate_by_time_bucket", "aggregate_by_spend_group", "aggregate_by_spend_group_per_filter"],
    )
    def test_the_boundary_is_keyword_only_and_has_no_default(self, method_name):
        parameter = inspect.signature(getattr(SpendService, method_name)).parameters["access"]

        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, "a positional boundary can be passed by accident"
        assert parameter.default is inspect.Parameter.empty

    @pytest.mark.parametrize(
        "call",
        [
            lambda: SERVICE.aggregate_by_time_bucket(SHORT_PERIOD, WIDTH),
            lambda: SERVICE.aggregate_by_spend_group(SHORT_PERIOD),
            lambda: SERVICE.aggregate_by_spend_group_per_filter(
                SHORT_PERIOD, common_filter=COMMON_FILTER, report_filters=[ALICE_ROW]
            ),
        ],
        ids=["aggregate_by_time_bucket", "aggregate_by_spend_group", "aggregate_by_spend_group_per_filter"],
    )
    def test_omitting_it_raises_rather_than_answering(self, call):
        led = _StubLedger("a")
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}), _no_metric_store_ids():
            with pytest.raises(TypeError):
                asyncio.run(call())

    @pytest.mark.parametrize("report_filter", [None, DASHBOARD_FILTER], ids=["unfiltered", "filtered"])
    def test_the_filter_is_optional_because_an_unfiltered_dashboard_is_a_real_state(self, report_filter):
        """The opposite default from the boundary, and deliberately so: an absent filter means the
        dashboard asked about everything, which is a thing a caller genuinely means."""
        led = _StubLedger("a")
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            asyncio.run(SERVICE.aggregate_by_spend_group(SHORT_PERIOD, access=None, report_filter=report_filter))

        assert led.filter_seen == [report_filter]


class TestTheBucketedReadIsKeyedOnTheWalkedGrid:
    """Every walked bucket for every class, and a key no ledger answered is a *state* rather than a gap.

    The keys come from `bucket_starts`, never from what came back: a chart built off the returned rows
    cannot express the empty bucket at all, and draws a gap where the truth is either a measured zero
    or an outage. The two are different answers and both must be renderable.
    """

    def _read(self, ledger):
        with registry({LLMCallClass.CHAT: ledger, LLMCallClass.EMBEDDINGS: ledger}), _no_metric_store_ids():
            return asyncio.run(SERVICE.aggregate_by_time_bucket(SHORT_PERIOD, WIDTH, access=None))

    def test_every_walked_bucket_appears_for_every_class(self):
        result = self._read(_StubLedger("a"))

        assert set(result) == {
            (start, traffic_class) for start in bucket_starts(SHORT_PERIOD, WIDTH) for traffic_class in TrafficClass
        }

    def test_the_walk_order_is_the_charts_left_to_right(self):
        """Bucket outer, class inner - the nesting a chart reads a series out of by slicing.

        Both halves are needed to pin it: the bucket starts appear in walk order under *either*
        nesting, so only the first assertion - one bucket carrying every class before the next
        bucket begins - fails when the two loops are transposed.
        """
        keys = list(self._read(_StubLedger("a")))

        assert [traffic_class for _, traffic_class in keys[: len(TrafficClass)]] == list(TrafficClass)
        assert [bucket for bucket, _ in keys[:: len(TrafficClass)]] == list(bucket_starts(SHORT_PERIOD, WIDTH))

    def test_a_key_no_ledger_answered_is_unavailable_rather_than_absent(self):
        answered = (bucket_starts(SHORT_PERIOD, WIDTH)[0], TrafficClass.CLI)
        result = self._read(_StubLedger("a", buckets={answered: SpendResult(figure=Decimal("6"))}))

        assert result[answered].figure == Decimal("6")
        silent = result[(bucket_starts(SHORT_PERIOD, WIDTH)[1], TrafficClass.WORKFLOW)]
        assert silent.figure is None, "a fabricated zero claims nothing was spent in that bucket"
        assert silent.unavailable == SHORT_PERIOD, "the flag names the period the caller asked about"

    def test_one_ledger_serving_both_call_classes_is_asked_once(self):
        """Asking per class and combining per key would add everything that ledger holds twice."""
        key = (bucket_starts(SHORT_PERIOD, WIDTH)[0], TrafficClass.CLI)
        only = _StubLedger("a", buckets={key: SpendResult(figure=Decimal("10"))})

        result = self._read(only)

        assert only.bucket_calls == 1
        assert result[key].figure == Decimal("10"), "20 would be the double count this guards"

    def test_an_off_grid_key_is_warned_about_and_never_reported(self):
        """A key beside the grid is money this mapping cannot place, and dropping it silently reads
        downstream as a bucket that held nothing. The facade is where two adapters' keys meet the one
        grid, so it is where the mismatch is named."""
        walked = bucket_starts(SHORT_PERIOD, WIDTH)
        beside_the_grid = (walked[0] + timedelta(minutes=1), TrafficClass.CLI)
        on_the_grid = (walked[0], TrafficClass.CLI)
        answering = _StubLedger(
            "spend_logs",
            buckets={
                on_the_grid: SpendResult(figure=Decimal("6")),
                beside_the_grid: SpendResult(figure=Decimal("99")),
            },
        )

        with patch("codemie.service.spend.spend_service.logger") as spend_logger:
            result = self._read(answering)

        assert beside_the_grid not in result, "a key the walk never produced must not reach the chart"
        assert set(result) == {(bucket, klass) for bucket in walked for klass in TrafficClass}
        assert result[on_the_grid].figure == Decimal("6")
        spend_logger.warning.assert_called_once()
        assert "spend_logs" in spend_logger.warning.call_args.args[0], "the warning has to name the ledger"

    def test_a_ledger_whose_keys_all_land_on_the_grid_is_not_warned_about(self):
        with patch("codemie.service.spend.spend_service.logger") as spend_logger:
            self._read(
                _StubLedger(
                    "a",
                    buckets={
                        (bucket_starts(SHORT_PERIOD, WIDTH)[0], TrafficClass.CLI): SpendResult(figure=Decimal("6"))
                    },
                )
            )

        spend_logger.warning.assert_not_called()


class TestTheSpendGroupReadIsKeyedOnTheClosedEnum:
    """Four keys, always, because every group is known before the read - unlike a bucket's grid.

    A card missing from the mapping renders as a blank, which reads as *no spend* where the state is
    *nobody could answer*. And the omission rule is the one an adapter is allowed: a group its call
    classes cannot carry at all is left out and combined from the adapters that can, never answered
    absent - which would flag a whole card as incomplete over traffic that was never its to report.
    """

    def _read(self, mapping, access=None, report_filter=None):
        with registry(mapping):
            return asyncio.run(SERVICE.aggregate_by_spend_group(PERIOD, access=access, report_filter=report_filter))

    def test_every_group_appears(self):
        led = _StubLedger("a", groups={SpendGroup.TOTAL: SpendResult(figure=Decimal("9"))})

        result = self._read({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led})

        assert set(result) == set(SpendGroup)

    def test_a_group_no_adapter_answered_is_unavailable_rather_than_zero(self):
        led = _StubLedger("a", groups={SpendGroup.TOTAL: SpendResult(figure=Decimal("9"))})

        result = self._read({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led})

        assert result[SpendGroup.CLI].figure is None
        assert result[SpendGroup.CLI].unavailable == PERIOD

    def test_a_group_one_adapter_omitted_is_combined_from_the_adapters_that_answered_it(self):
        """The mixed deployment: the embeddings adapter carries no CLI traffic and says so by omission.

        Answering `no_figure` there instead would flag the CLI card as incomplete on every deployment
        that splits the two call classes - a permanent warning about traffic that adapter never held.
        """
        chat = _StubLedger(
            "spend_logs",
            groups={
                SpendGroup.TOTAL: SpendResult(figure=Decimal("7")),
                SpendGroup.CLI: SpendResult(figure=Decimal("2")),
            },
        )
        embeddings = _StubLedger(
            "default",
            groups={
                SpendGroup.TOTAL: SpendResult(figure=Decimal("3")),
                SpendGroup.EMBEDDING: SpendResult(figure=Decimal("3")),
            },
        )

        result = self._read({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings})

        assert result[SpendGroup.TOTAL].figure == Decimal("10"), "both adapters answered the total"
        assert result[SpendGroup.CLI].figure == Decimal("2"), "the omitting adapter must not drag it to unavailable"
        assert result[SpendGroup.CLI].is_exact, "an omission is not an absence and raises no flag"
        assert result[SpendGroup.EMBEDDING].figure == Decimal("3")

    def test_one_ledger_serving_both_call_classes_is_asked_once(self):
        """Asking per class and combining per group reports everything that ledger holds twice."""
        only = _StubLedger("a", groups={SpendGroup.TOTAL: SpendResult(figure=Decimal("10"))})

        result = self._read({LLMCallClass.CHAT: only, LLMCallClass.EMBEDDINGS: only})

        assert only.group_calls == 1
        assert result[SpendGroup.TOTAL].figure == Decimal("10"), "20 would be the double count this guards"


class TestThePerFilterSpendGroupReadIsOneReadPerLedger:
    """A per-row table's groups in one read per ledger, each row's figure combined as if read alone.

    Every filter the table asked about is keyed with every group, for the closed-enum reason
    `aggregate_by_spend_group` gives: a missing row or card renders as a blank where the state is
    *nobody could answer*. And a ledger staying silent about a filter is the omission an adapter is
    allowed for a group, so it contributes nothing rather than dragging the row to unavailable.
    """

    def _read(self, mapping, report_filters):
        with registry(mapping):
            return asyncio.run(
                SERVICE.aggregate_by_spend_group_per_filter(
                    PERIOD, access=None, common_filter=COMMON_FILTER, report_filters=report_filters
                )
            )

    def test_one_ledger_serving_both_call_classes_is_asked_once_for_the_deduplicated_filters(self):
        only = _StubLedger(
            "a",
            groups_by_filter={
                ALICE_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("10"))},
                BOB_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("4"))},
            },
        )

        result = self._read({LLMCallClass.CHAT: only, LLMCallClass.EMBEDDINGS: only}, [ALICE_ROW, BOB_ROW, ALICE_ROW])

        assert only.group_calls == 1
        assert only.report_filters_seen == [[ALICE_ROW, BOB_ROW]]
        assert result[ALICE_ROW][SpendGroup.TOTAL].figure == Decimal("10"), "20 would be the double count"
        assert result[BOB_ROW][SpendGroup.TOTAL].figure == Decimal("4")

    def test_every_requested_filter_is_keyed_with_every_group(self):
        led = _StubLedger("a", groups_by_filter={ALICE_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("9"))}})

        result = self._read({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}, [ALICE_ROW, BOB_ROW])

        assert list(result) == [ALICE_ROW, BOB_ROW]
        assert set(result[ALICE_ROW]) == set(SpendGroup) and set(result[BOB_ROW]) == set(SpendGroup)

    def test_two_ledgers_answers_are_summed_per_filter_and_group(self):
        chat = _StubLedger(
            "spend_logs",
            groups_by_filter={
                ALICE_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("7"))},
                BOB_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("1"))},
            },
        )
        embeddings = _StubLedger(
            "default",
            groups_by_filter={
                ALICE_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("3"))},
                BOB_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("2"))},
            },
        )

        result = self._read({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}, [ALICE_ROW, BOB_ROW])

        assert chat.group_calls == 1 and embeddings.group_calls == 1
        assert result[ALICE_ROW][SpendGroup.TOTAL].figure == Decimal("10")
        assert result[BOB_ROW][SpendGroup.TOTAL].figure == Decimal("3")

    def test_a_group_one_ledger_omitted_is_combined_from_the_ledger_that_answered_it(self):
        chat = _StubLedger(
            "spend_logs",
            groups_by_filter={
                ALICE_ROW: {
                    SpendGroup.TOTAL: SpendResult(figure=Decimal("7")),
                    SpendGroup.CLI: SpendResult(figure=Decimal("2")),
                }
            },
        )
        embeddings = _StubLedger(
            "default", groups_by_filter={ALICE_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("3"))}}
        )

        result = self._read({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}, [ALICE_ROW])

        assert result[ALICE_ROW][SpendGroup.CLI].figure == Decimal("2")
        assert result[ALICE_ROW][SpendGroup.CLI].is_exact, "an omission is not an absence and raises no flag"

    def test_a_filter_one_ledger_did_not_answer_is_combined_from_the_ledger_that_did(self):
        chat = _StubLedger(
            "spend_logs",
            groups_by_filter={
                ALICE_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("7"))},
                BOB_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("5"))},
            },
        )
        embeddings = _StubLedger(
            "default", groups_by_filter={ALICE_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("3"))}}
        )

        result = self._read({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}, [ALICE_ROW, BOB_ROW])

        assert result[BOB_ROW][SpendGroup.TOTAL].figure == Decimal("5")
        assert result[BOB_ROW][SpendGroup.TOTAL].is_exact

    def test_a_group_no_ledger_answered_is_unavailable_rather_than_zero(self):
        led = _StubLedger("a", groups_by_filter={ALICE_ROW: {SpendGroup.TOTAL: SpendResult(figure=Decimal("9"))}})

        result = self._read({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}, [ALICE_ROW, BOB_ROW])

        assert result[ALICE_ROW][SpendGroup.CLI].figure is None
        assert result[ALICE_ROW][SpendGroup.CLI].unavailable == PERIOD
        assert result[BOB_ROW][SpendGroup.TOTAL].figure is None
        assert result[BOB_ROW][SpendGroup.TOTAL].unavailable == PERIOD

    def test_no_filters_answers_nothing_and_asks_no_ledger(self):
        led = _StubLedger("a")

        result = self._read({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}, [])

        assert result == {}
        assert led.group_calls == 0


class TestTheBoundaryAndTheFilterReachEveryDistinctLedger:
    """A boundary honoured by one adapter is not a boundary, and the same is true of the filter.

    In a split deployment the un-narrowed adapter's answer is *summed into* the narrowed one's, so the
    disclosure arrives inside a figure that looks right. `_UNSET` rather than `None` is what makes
    these assertions worth anything: `None` is a real value on both arguments, so an adapter that was
    never handed either would satisfy a `is None` check for the wrong reason.
    """

    def _both(self):
        return _StubLedger("spend_logs"), _StubLedger("default")

    def test_a_bucketed_read_hands_them_to_every_ledger_unchanged(self):
        chat, embeddings = self._both()
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}), _no_metric_store_ids():
            asyncio.run(
                SERVICE.aggregate_by_time_bucket(
                    SHORT_PERIOD, WIDTH, access=ALICE_SCOPE, report_filter=DASHBOARD_FILTER
                )
            )

        assert chat.access_seen == [ALICE_SCOPE] and embeddings.access_seen == [ALICE_SCOPE]
        assert chat.filter_seen == [DASHBOARD_FILTER] and embeddings.filter_seen == [DASHBOARD_FILTER]

    def test_a_spend_group_read_hands_them_to_every_ledger_unchanged(self):
        chat, embeddings = self._both()
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            asyncio.run(SERVICE.aggregate_by_spend_group(PERIOD, access=ALICE_SCOPE, report_filter=DASHBOARD_FILTER))

        assert chat.access_seen == [ALICE_SCOPE] and embeddings.access_seen == [ALICE_SCOPE]
        assert chat.filter_seen == [DASHBOARD_FILTER] and embeddings.filter_seen == [DASHBOARD_FILTER]

    def test_a_per_filter_spend_group_read_hands_them_to_every_ledger_unchanged(self):
        chat, embeddings = self._both()
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            asyncio.run(
                SERVICE.aggregate_by_spend_group_per_filter(
                    PERIOD, access=ALICE_SCOPE, common_filter=COMMON_FILTER, report_filters=[ALICE_ROW]
                )
            )

        assert chat.access_seen == [ALICE_SCOPE] and embeddings.access_seen == [ALICE_SCOPE]
        assert chat.filter_seen == [COMMON_FILTER] and embeddings.filter_seen == [COMMON_FILTER]

    def test_no_caller_is_written_out_as_none_rather_than_omitted(self):
        """The absence is expressible - a scheduled job has no caller - but only deliberately."""
        led = _StubLedger("a")
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            asyncio.run(SERVICE.aggregate_by_spend_group(PERIOD, access=None))

        assert led.access_seen == [None]
        assert led.filter_seen == [None]

    def test_a_holder_batch_hands_them_to_every_ledger_unchanged(self):
        """The users- and projects-spending tables read their rows' money through this batch, so a
        filter dropped here shows a person filtered to one project their spend across every project."""
        chat, embeddings = self._both()
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            asyncio.run(
                SERVICE.consumed_for_holders([HOLDER], PERIOD, access=ALICE_SCOPE, report_filter=DASHBOARD_FILTER)
            )

        assert chat.access_seen == [ALICE_SCOPE] and embeddings.access_seen == [ALICE_SCOPE]
        assert chat.filter_seen == [DASHBOARD_FILTER] and embeddings.filter_seen == [DASHBOARD_FILTER]

    def test_a_budget_holder_batch_hands_every_ledger_no_filter(self):
        """A budget's consumption is not whatever share of it one dashboard's filter selected, so a
        caller naming no filter reaches every ledger with `None`, never with something narrower."""
        chat, embeddings = self._both()
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            asyncio.run(SERVICE.consumed_for_holders([HOLDER], PERIOD))

        assert chat.filter_seen == [None] and embeddings.filter_seen == [None]


HEALTHY_FIGURE = Decimal("10")


class _HealthyLedger:
    """Answers every key of every read with the same figure.

    A stub that answers everything is what makes the outage tests below say something: the only
    thing that can change what comes back is the ledger beside it failing.
    """

    ledger_name = "healthy"

    async def consumed_for_holder(self, holder, period, access=None):
        return SpendResult(figure=HEALTHY_FIGURE)

    async def consumed_for_holders(self, holders, period, access=None, *, report_filter=None):
        return {holder: SpendResult(figure=HEALTHY_FIGURE) for holder in holders}

    async def aggregate(self, period, scope=None, resolved_ids=()):
        return SpendResult(figure=HEALTHY_FIGURE)

    async def aggregate_by_time_bucket(self, period, width, resolved_ids=None, access=None, report_filter=None):
        return {
            (bucket, traffic_class): SpendResult(figure=HEALTHY_FIGURE)
            for bucket in bucket_starts(period, width)
            for traffic_class in TrafficClass
        }

    async def aggregate_by_traffic_class(self, period, resolved_ids=None):
        return {traffic_class: SpendResult(figure=HEALTHY_FIGURE) for traffic_class in TrafficClass}

    async def aggregate_by_spend_group(self, period, *, access=None, report_filter=None):
        return {group: SpendResult(figure=HEALTHY_FIGURE) for group in SpendGroup}

    async def aggregate_by_spend_group_per_filter(self, period, *, access=None, common_filter=None, report_filters):
        return {
            report_filter: {group: SpendResult(figure=HEALTHY_FIGURE) for group in SpendGroup}
            for report_filter in report_filters
        }

    async def aggregate_by_dimension(
        self, period, dimension, values, ids_by_value=None, access=None, report_filter=None
    ):
        return {value: SpendResult(figure=HEALTHY_FIGURE) for value in values}

    async def aggregate_composed(self, period, scope, *, dimension_ids=(), traffic_class_ids=()):
        return SpendResult(figure=HEALTHY_FIGURE)

    async def aggregate_composed_by_traffic_class(self, period, scope, *, dimension_ids=(), ids_by_class=None):
        return {traffic_class: SpendResult(figure=HEALTHY_FIGURE) for traffic_class in TrafficClass}

    async def cost_for_request(self, *, run_id=None, request_id=None):
        return RequestCost.settled(Decimal("2"))

    async def costs_for_requests(self, request_ids):
        return {request_id: RequestCost.settled(Decimal("2")) for request_id in request_ids}

    async def request_count(self, period, scope=None, resolved_ids=()):
        return 5


class _BrokenLedger:
    """A ledger in outage: every read raises, whichever read it is.

    Written through `__getattr__` rather than as a dozen raising methods so that a read added to the
    port later cannot quietly acquire a working stub here and stop being covered.
    """

    def __init__(self, name="broken"):
        self.ledger_name = name

    def __getattr__(self, read):
        async def _outage(*args, **kwargs):
            raise RuntimeError("ledger down")

        return _outage


@contextmanager
def _no_metric_store_reads():
    """Both metric-store lookups stubbed - these tests are about ledger outages, not resolution."""
    with patch.object(SpendService, "_metric_store_ids", return_value=()):
        with patch.object(SpendService, "_metric_store_ids_by_value", return_value={}):
            yield


#: Every read whose figure an outage must degrade, with the period it is asked over. A composed
#: scope with no dimension and no class term keeps the composed reads off the metric store.
DEGRADING_READS = {
    "consumed_for_holder": (lambda: SERVICE.consumed_for_holder(HOLDER, PERIOD), PERIOD),
    "consumed_for_holders": (lambda: SERVICE.consumed_for_holders([HOLDER], PERIOD), PERIOD),
    "aggregate": (lambda: SERVICE.aggregate(PERIOD), PERIOD),
    "aggregate_by_time_bucket": (
        lambda: SERVICE.aggregate_by_time_bucket(SHORT_PERIOD, WIDTH, access=None),
        SHORT_PERIOD,
    ),
    "aggregate_by_traffic_class": (lambda: SERVICE.aggregate_by_traffic_class(PERIOD), PERIOD),
    "aggregate_by_spend_group": (lambda: SERVICE.aggregate_by_spend_group(PERIOD, access=None), PERIOD),
    "aggregate_by_spend_group_per_filter": (
        lambda: SERVICE.aggregate_by_spend_group_per_filter(
            PERIOD, access=None, common_filter=COMMON_FILTER, report_filters=[ALICE_ROW, BOB_ROW]
        ),
        PERIOD,
    ),
    "aggregate_by_dimension": (
        lambda: SERVICE.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["a-1", "a-2"], access=None),
        PERIOD,
    ),
    "aggregate_composed": (lambda: SERVICE.aggregate_composed(PERIOD, ComposedScope(project="proj-a")), PERIOD),
    "aggregate_composed_by_traffic_class": (
        lambda: SERVICE.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(project="proj-a")),
        PERIOD,
    ),
}


def _results_of(answer):
    """The `SpendResult`s a read produced - one, or one per key at any depth - so one assertion fits every shape."""
    if isinstance(answer, dict):
        return [result for nested in answer.values() for result in _results_of(nested)]
    return [answer]


def _run(read, ledgers):
    with registry(ledgers), _no_metric_store_reads():
        return _results_of(asyncio.run(DEGRADING_READS[read][0]()))


class TestALedgerOutageDegradesTheFigureAndNeverTheRequest:
    """A ledger that raises costs a figure, never the request that asked for it.

    Nothing on any surface depends on a spend read, so the loss is reported *inside* the answer: the
    healthy ledger's money still arrives, carrying the flag that renders it as incomplete data.
    """

    @pytest.mark.parametrize("read", sorted(DEGRADING_READS))
    def test_a_healthy_ledgers_figure_survives_beside_a_failing_one_and_is_flagged(self, read):
        """Every key the caller asked about is answered, and none of them silently exact.

        The flag is the load-bearing half. A figure short of one ledger's contribution rendered
        unflagged is the truncated total the port exists to prevent, and it renders perfectly.
        """
        _, period = DEGRADING_READS[read]

        results = _run(read, {LLMCallClass.CHAT: _HealthyLedger(), LLMCallClass.EMBEDDINGS: _BrokenLedger()})

        assert results, "the read must still answer every key it was asked about"
        assert all(result.figure == HEALTHY_FIGURE for result in results)
        assert all(
            result.unavailable == period for result in results
        ), "a partial mapping would let the surviving ledger's answer render as exact"

    @pytest.mark.parametrize("read", sorted(DEGRADING_READS))
    def test_every_ledger_failing_yields_no_figure_and_never_a_zero(self, read):
        """Nobody could answer, and `$0.00` would claim that nothing was spent."""
        _, period = DEGRADING_READS[read]

        results = _run(read, {LLMCallClass.CHAT: _BrokenLedger("a"), LLMCallClass.EMBEDDINGS: _BrokenLedger("b")})

        assert results
        assert all(result.figure is None for result in results)
        assert all(result.unavailable == period for result in results)


class TestAFailedLedgerReadIsBothLoggedAndCounted:
    """An outage that degrades silently is an outage nobody is paged for.

    A figure that renders as incomplete looks the same to a reader whatever went wrong, so the log
    line and the counter are the only places the *cause* survives.
    """

    def test_the_log_names_the_ledger_the_read_and_the_period_and_carries_the_traceback(self):
        with registry({LLMCallClass.CHAT: _BrokenLedger("spend_logs"), LLMCallClass.EMBEDDINGS: _HealthyLedger()}):
            with patch("codemie.service.spend.read_failures.logger") as spend_logger:
                asyncio.run(SERVICE.aggregate(PERIOD))

        spend_logger.error.assert_called_once()
        message = spend_logger.error.call_args.args[0]
        assert "spend_logs" in message, "an outage nobody can attribute to a ledger is not actionable"
        assert "aggregate" in message
        assert str(PERIOD) in message
        assert isinstance(spend_logger.error.call_args.kwargs["exc_info"], RuntimeError)

    def test_a_ledger_outage_is_an_error_where_a_lost_id_set_is_a_warning(self):
        """The two losses differ in kind: the residue label still names the money a lost id set costs."""
        with registry({LLMCallClass.CHAT: _BrokenLedger(), LLMCallClass.EMBEDDINGS: _HealthyLedger()}):
            with patch("codemie.service.spend.spend_service.logger") as spend_logger:
                asyncio.run(SERVICE.aggregate(PERIOD))

        spend_logger.warning.assert_not_called()

    def test_one_line_per_failed_read_rather_than_one_per_request(self):
        with registry({LLMCallClass.CHAT: _BrokenLedger("a"), LLMCallClass.EMBEDDINGS: _BrokenLedger("b")}):
            with patch("codemie.service.spend.read_failures.logger") as spend_logger:
                asyncio.run(SERVICE.aggregate(PERIOD))

        assert spend_logger.error.call_count == 2
        assert {call.args[0].split()[2] for call in spend_logger.error.call_args_list} == {"a", "b"}

    def test_each_failed_read_counts_the_failure_metric_naming_the_ledger_and_the_read(self):
        with registry({LLMCallClass.CHAT: _BrokenLedger("spend_logs"), LLMCallClass.EMBEDDINGS: _HealthyLedger()}):
            with patch("codemie.service.spend.read_failures.BaseMonitoringService") as monitoring:
                asyncio.run(SERVICE.aggregate(PERIOD))

        monitoring.send_count_metric.assert_called_once_with(
            name=SPEND_LEDGER_READ_FAILURES_METRIC,
            attributes={"ledger": "spend_logs", "read": "aggregate"},
            count=1,
        )

    def test_every_count_is_handed_its_own_attribute_dict(self):
        """`send_count_metric` mutates what it is handed, adding the calling user and the environment.

        A dict shared between counts would carry the first call's user into the second, and a
        module-level literal would accumulate them for the life of the process.
        """
        with registry({LLMCallClass.CHAT: _BrokenLedger("a"), LLMCallClass.EMBEDDINGS: _BrokenLedger("b")}):
            with patch("codemie.service.spend.read_failures.BaseMonitoringService") as monitoring:
                asyncio.run(SERVICE.aggregate(PERIOD))

        first, second = (call.kwargs["attributes"] for call in monitoring.send_count_metric.call_args_list)
        assert first is not second
        assert (first["ledger"], second["ledger"]) == ("a", "b")


class TestEveryLedgerReadIsTimed:
    """EPMCDME-15537: a figure that arrives late degrades nothing, so its duration is its only trace."""

    #: The slow-read threshold the warning tests pin, so they do not ride on the shipped default.
    SLOW_READ_SECONDS = 5.0

    def test_each_read_records_its_duration_naming_the_ledger_the_read_and_the_outcome(self):
        with registry({LLMCallClass.CHAT: _HealthyLedger(), LLMCallClass.EMBEDDINGS: _BrokenLedger("spend_logs")}):
            with patch("codemie.service.spend.spend_service.BaseMonitoringService") as monitoring:
                asyncio.run(SERVICE.aggregate(PERIOD))

        recorded = [
            (call.args[0], call.kwargs["attributes"]) for call in monitoring.record_duration_metric.call_args_list
        ]
        assert sorted(recorded, key=lambda metric: metric[1]["ledger"]) == [
            (SPEND_LEDGER_READ_DURATION_METRIC, {"ledger": "healthy", "read": "aggregate", "outcome": "ok"}),
            (SPEND_LEDGER_READ_DURATION_METRIC, {"ledger": "spend_logs", "read": "aggregate", "outcome": "failed"}),
        ], "a failed read is timed too, under its own outcome"
        assert all(
            call.kwargs["per_user"] is False for call in monitoring.record_duration_metric.call_args_list
        ), "a ledger read's duration is the ledger's, not the calling user's"

    def test_a_cancelled_read_is_timed_as_cancelled_and_still_cancels(self):
        """Nothing failed, so no failure is counted - and the duration stays out of the failed series."""

        async def _cancelled_read():
            raise asyncio.CancelledError

        with patch("codemie.service.spend.spend_service.BaseMonitoringService") as monitoring:
            with patch("codemie.service.spend.read_failures.BaseMonitoringService") as failures:
                with pytest.raises(asyncio.CancelledError):
                    asyncio.run(SERVICE._ask(_HealthyLedger(), "aggregate", PERIOD, _cancelled_read(), None))

        failures.send_count_metric.assert_not_called()
        assert monitoring.record_duration_metric.call_args.kwargs["attributes"] == {
            "ledger": "healthy",
            "read": "aggregate",
            "outcome": "cancelled",
        }

    @pytest.mark.parametrize(("elapsed", "warned"), [(4.99, False), (SLOW_READ_SECONDS, True), (30.0, True)])
    def test_a_read_at_or_above_the_threshold_is_named_in_a_warning(self, elapsed, warned):
        ledger = _HealthyLedger()
        clock = MagicMock()
        clock.monotonic.side_effect = [100.0, 100.0 + elapsed]
        with registry({LLMCallClass.CHAT: ledger, LLMCallClass.EMBEDDINGS: ledger}):
            with (
                patch.object(spend_service.config, "SPEND_LEDGER_SLOW_READ_SECONDS", self.SLOW_READ_SECONDS),
                patch.object(spend_service, "time", clock),
                patch.object(spend_service, "logger") as spend_logger,
            ):
                asyncio.run(SERVICE.aggregate(PERIOD))

        if not warned:
            spend_logger.warning.assert_not_called()
            return
        message = spend_logger.warning.call_args.args[0]
        assert "healthy" in message and "aggregate" in message and str(PERIOD) in message
        assert f"{elapsed:.2f}s" in message


class TestAWiringDefectStillRaisesRatherThanDegrading:
    def test_an_unsteppable_width_raises_even_with_a_ledger_in_outage(self):
        """A width no walk can tile is a defect in the caller, not an outage in a ledger.

        Degraded, it would answer a chart whose keys nobody can compute - so the walk happens before
        any ledger is asked, which is also what gives the outage fallback a grid to fill.
        """
        ledgers = {LLMCallClass.CHAT: _BrokenLedger(), LLMCallClass.EMBEDDINGS: _BrokenLedger()}
        with registry(ledgers), _no_metric_store_reads():
            with pytest.raises(ValueError):
                asyncio.run(SERVICE.aggregate_by_time_bucket(SHORT_PERIOD, "fortnight", access=None))


class TestAnUnreadableLedgerLeavesNoCostSettled:
    """What a failed ledger holds is *unknown*, and an unknown part cannot be settled.

    This is the pending rule one step further out: reporting the half that landed as final is the
    fabricated total either way, and here nothing even says a figure is coming.
    """

    def test_a_settled_ledger_beside_a_failing_one_is_unavailable_rather_than_settled(self):
        with registry({LLMCallClass.CHAT: _HealthyLedger(), LLMCallClass.EMBEDDINGS: _BrokenLedger()}):
            cost = asyncio.run(SERVICE.cost_for_request(request_id="q-1"))

        assert cost.is_unavailable
        assert not cost.is_settled
        assert cost.figure is None

    def test_the_failure_is_logged_and_counted_under_the_grain_that_was_asked_about(self):
        with registry({LLMCallClass.CHAT: _HealthyLedger(), LLMCallClass.EMBEDDINGS: _BrokenLedger()}):
            with patch("codemie.service.spend.read_failures.logger") as spend_logger:
                with patch("codemie.service.spend.read_failures.BaseMonitoringService") as monitoring:
                    asyncio.run(SERVICE.cost_for_request(run_id="r-1"))

        assert "run_id" in spend_logger.error.call_args.args[0], "with no period, the grain is what names the read"
        assert monitoring.send_count_metric.call_args.kwargs["attributes"]["read"] == "cost_for_request by run_id"

    def test_a_failing_ledger_leaves_every_request_on_the_page_unavailable(self):
        with registry({LLMCallClass.CHAT: _HealthyLedger(), LLMCallClass.EMBEDDINGS: _BrokenLedger()}):
            costs = asyncio.run(SERVICE.costs_for_requests(["q-1", "q-2"]))

        assert costs == {"q-1": RequestCost.unavailable(), "q-2": RequestCost.unavailable()}

    def test_the_grain_check_still_raises_with_a_ledger_in_outage(self):
        with registry({LLMCallClass.CHAT: _BrokenLedger(), LLMCallClass.EMBEDDINGS: _BrokenLedger()}):
            with pytest.raises(ValueError):
                asyncio.run(SERVICE.cost_for_request(run_id="r-1", request_id="q-1"))


class TestAFailedCountComesOutShortRatherThanRaising:
    """A count carries no state to say it is incomplete - the `SpendResult` beside it does that."""

    def test_a_failing_ledger_contributes_nothing_to_the_total(self):
        with registry({LLMCallClass.CHAT: _HealthyLedger(), LLMCallClass.EMBEDDINGS: _BrokenLedger()}):
            assert asyncio.run(SERVICE.request_count(PERIOD)) == 5

    def test_every_ledger_failing_counts_nothing_and_still_answers(self):
        with registry({LLMCallClass.CHAT: _BrokenLedger(), LLMCallClass.EMBEDDINGS: _BrokenLedger()}):
            with patch("codemie.service.spend.read_failures.logger") as spend_logger:
                with patch("codemie.service.spend.read_failures.BaseMonitoringService") as monitoring:
                    assert asyncio.run(SERVICE.request_count(PERIOD)) == 0

        assert spend_logger.error.call_count == 2
        assert {call.kwargs["attributes"]["read"] for call in monitoring.send_count_metric.call_args_list} == {
            "request_count"
        }


class TestAMetricStoreFailureIsCountedUnderItsOwnName:
    """The degradation that was already survivable, and until now invisible.

    Counted on the same metric because the question is the same one - which read is failing, and how
    often - and named honestly for the source that failed rather than for a ledger it is not.
    """

    def test_a_failed_id_lookup_counts_under_the_metric_store(self):
        from codemie.service.spend.ledger import DimensionScope

        led = _StubLedger("a")
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch(
                "codemie.service.spend.dimension_scope.resolve_metric_store_ids",
                side_effect=RuntimeError("elasticsearch is down"),
            ):
                with patch("codemie.service.spend.spend_service.BaseMonitoringService") as monitoring:
                    result = asyncio.run(SERVICE.aggregate(PERIOD, DimensionScope(SpendDimension.ASSISTANT, "a-1")))

        assert result.figure == Decimal("10"), "the report still stands"
        monitoring.send_count_metric.assert_called_once_with(
            name=SPEND_LEDGER_READ_FAILURES_METRIC,
            attributes={"ledger": "metric_store", "read": "metric_store_ids"},
        )

    def test_a_failed_per_value_lookup_counts_under_its_own_read(self):
        with registry({LLMCallClass.CHAT: _HealthyLedger(), LLMCallClass.EMBEDDINGS: _HealthyLedger()}):
            with patch(
                "codemie.service.spend.dimension_scope.resolve_metric_store_ids_by_value",
                side_effect=RuntimeError("elasticsearch is down"),
            ):
                with patch("codemie.service.spend.spend_service.BaseMonitoringService") as monitoring:
                    result = asyncio.run(
                        SERVICE.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["a-1"], access=None)
                    )

        assert result["a-1"].figure == Decimal("20"), "both ledgers still answered"
        monitoring.send_count_metric.assert_called_once_with(
            name=SPEND_LEDGER_READ_FAILURES_METRIC,
            attributes={"ledger": "metric_store", "read": "metric_store_ids_by_value"},
        )
