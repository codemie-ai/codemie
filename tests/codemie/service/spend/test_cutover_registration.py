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

"""Registering the pre-floor cutover: the kill switch, what gets wrapped, and the double count.

**The defect these tests exist for is arithmetic, not structural, so the central one asserts a
figure.** In the mixed configuration - chat through LiteLLM, embeddings native, one environment
variable away - both registry slots hold a ledger, so `register_spend_cutover` builds two wrappers.
`SpendService._distinct_ledgers` de-duplicates on object identity, and two wrappers are two objects,
so both are asked about the same sub-`F` period and their answers are **added**. A pre-floor engine
scoped to every call class under each wrapper therefore reports every sub-`F` row twice, in two
figures that are individually plausible. An assertion on the object graph - *both slots hold a
cutover* - passes on exactly that implementation; only the total tells them apart.

**So the money comes from a scripted row set rather than from a stub returning a fixed number.**
`_RowSetMetrics` - reused from the default-ledger tests rather than restated - applies each query's
own filters to one row set, so the two pre-floor engines can only come out at the right total if
they asked for disjoint metric names. A stub answering per call would agree with itself whichever
names the engines were scoped to, which is the whole subject here.

**The kill switch is proved rather than assumed.** `SPEND_LEDGER_LEGACY_BELOW_FLOOR` off must leave
the registry holding the very objects it held before, so the assertion is on object identity: a
cutover constructed with a disabled legacy half would still replace the object in the slot, still
change `ledger_name`, and would then have to be reasoned about instead of simply not being there.

**The gap the double-count fix opened is asserted as money too, and for the same reason.** Scoping
each engine to its slot's classes made the wrap depend on the slot being occupied, so a deployment
whose LiteLLM ledger failed to construct - a swallowed startup error in `rest_api/main.py`, not a
misconfiguration - kept the noop in the chat slot and lost every pre-floor chat and CLI-proxy figure.
The structural assertion (*the chat slot holds a cutover*) is here, but the one that would survive a
re-implementation is the total: the same three rows, the same 9, with only one of the two slots
actually holding a ledger.

**The configured floor is deliberately not the registered one.** `routing(...)` pins
`SPEND_LEDGER_COVERAGE_FLOOR` to an instant far below the coverage the tests register, so a second
config read inside the registration - the failure this package's composition root exists to prevent -
splits the period at the wrong instant and one test's ledger half gets consulted where the legacy
half belongs.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from codemie.configs import config
from codemie.service.analytics.metric_names import MetricName
from codemie.service.spend.composition import register_spend_cutover, register_spend_ledgers
from codemie.service.spend.coverage import LedgerCoverage, SpendResult
from codemie.service.spend.default_ledger import MONEY_FIELD
from codemie.service.spend.ledger_registry import (
    get_spend_ledger,
    ledgers_by_call_class,
    register_ledger_coverage,
    register_spend_logs_ledger,
    reset_ledgers,
)
from codemie.service.spend.legacy_ledger import LEGACY_MONEY_METRICS
from codemie.service.spend.routing import LLMCallClass
from codemie.service.spend.spend_service import spend_service
from tests.codemie.service.spend.periods import PERIOD
from tests.codemie.service.spend.test_default_ledger import MID_PERIOD, _RowSetMetrics

#: The floor the composition root publishes, and the only one a registration may split at. `PERIOD`
#: (August 2026) lies wholly below it, so every read here is answered by the legacy half alone.
REGISTERED_FLOOR = datetime(2026, 10, 1, tzinfo=timezone.utc)

#: What `SPEND_LEDGER_COVERAGE_FLOOR` says while these tests run - **below** `PERIOD` rather than
#: above it, so a registration re-reading config would split the period the other way round and be
#: caught by the figure rather than by an assertion about where the value came from.
CONFIGURED_FLOOR = datetime(2026, 1, 1, tzinfo=timezone.utc)


def money_row(metric: str, money: str) -> dict:
    """One settled money document, timestamped inside `PERIOD` and so below the registered floor."""
    return {"metric_name.keyword": metric, "@timestamp": MID_PERIOD, MONEY_FIELD: Decimal(money)}


#: Three rows the two pre-floor engines must partition between them: the chat metric and the CLI
#: proxy metric belong to the chat engine, the datasource metric to the embeddings engine. Their
#: total is 9, and an engine per slot scoped to every class answers 18.
CHAT_MONEY = money_row(MetricName.CONVERSATION_ASSISTANT_USAGE.value, "3")
CLI_MONEY = money_row(MetricName.CLI_LLM_USAGE_TOTAL.value, "5")
EMBEDDINGS_MONEY = money_row(MetricName.DATASOURCE_TOKENS_USAGE.value, "1")
ALL_MONEY = [CHAT_MONEY, CLI_MONEY, EMBEDDINGS_MONEY]


class _StubLedger:
    """A stand-in for the enterprise spend-logs ledger that records having been asked.

    Its figure is deliberately large and unlike any row total below: a period below the floor must
    reach the legacy half only, so a consult shows up as a wrong number as well as a recorded period.
    """

    ledger_name = "spend_logs"

    def __init__(self) -> None:
        self.periods: list = []

    async def aggregate(self, period, scope=None, resolved_ids=()):
        self.periods.append(period)
        return SpendResult(figure=Decimal("100"))


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_ledgers()
    yield
    reset_ledgers()


@contextmanager
def routing(
    mode: str,
    *,
    embeddings_disabled: bool = False,
    has_litellm: bool = True,
    enabled: bool = True,
    legacy_below_floor: bool = True,
):
    """Patch the routing predicate's inputs, the cutover's kill switch, and the configured floor.

    The configured floor is pinned here rather than per test because *no* test wants a registration
    to read it: it is the value the composition root already read once, and reading it again is the
    mid-change split this package is built to avoid.
    """
    with (
        patch.object(config, "LLM_PROXY_MODE", mode),
        patch.object(config, "LLM_PROXY_ENABLED", enabled),
        patch.object(config, "LLM_PROXY_EMBEDDINGS_DISABLED", embeddings_disabled),
        patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", legacy_below_floor),
        patch.object(config, "SPEND_LEDGER_COVERAGE_FLOOR", CONFIGURED_FLOOR.isoformat()),
        patch("codemie.enterprise.HAS_LITELLM", has_litellm),
    ):
        yield


@contextmanager
def metric_store(rows: list[dict]):
    """Every `DefaultSpendLedger` built while this is open reads one scripted row set.

    Patched at the repository class the adapter reaches for when none is injected, because the
    pre-floor engines are constructed *inside* the registration under test - there is no seam to hand
    a fake through, and inventing one would test a wiring no deployment uses.
    """
    repo = _RowSetMetrics(list(rows))
    with patch("codemie.repository.metrics_elastic_repository.MetricsElasticRepository", lambda: repo):
        yield repo


def activate(floor: datetime = REGISTERED_FLOOR) -> None:
    """Publish an activated coverage, which is what a cutover has a boundary to split at."""
    register_ledger_coverage(LedgerCoverage(coverage_floor=floor, ready=True))


def metric_names_queried(repo: _RowSetMetrics) -> list[frozenset[str]]:
    """The metric-name set each issued query was restricted to - the double-count guard, as executed.

    Read off the query body rather than off the engines' attributes: the guard is what the store was
    actually asked for, and an engine holding the right scope while querying a wider one is precisely
    the failure that would not show up in the object graph.
    """
    return [frozenset(body["query"]["bool"]["filter"][0]["terms"]["metric_name.keyword"]) for body in repo.bodies]


class TestTheKillSwitch:
    def test_the_flag_off_leaves_the_registry_holding_the_very_objects_it_held(self):
        """Off means untouched, asserted on identity: a disabled wrapper is still a wrapper."""
        with routing("lite_llm", embeddings_disabled=True, legacy_below_floor=False), metric_store([]):
            register_spend_ledgers()
            register_spend_logs_ledger(_StubLedger())
            activate()
            before = ledgers_by_call_class()

            register_spend_cutover()

            after = ledgers_by_call_class()

        assert all(after[call_class] is before[call_class] for call_class in LLMCallClass)
        assert sorted(ledger.ledger_name for ledger in after.values()) == ["default", "spend_logs"]

    def test_the_flag_off_leaves_an_empty_slot_empty_rather_than_holding_a_legacy_engine(self):
        """The switch has to cover the slot the wrap now reaches that it did not reach before.

        Wrapping the empty slot is what installs a ledger where the registry had none, so it is the
        one place an opt-out could be read as "no ledger to leave alone, therefore nothing to skip".
        """
        with routing("lite_llm", embeddings_disabled=True, legacy_below_floor=False), metric_store([]):
            register_spend_ledgers()  # a default ledger for embeddings; nothing registers chat
            activate()

            register_spend_cutover()

            assert get_spend_ledger(LLMCallClass.CHAT).ledger_name == "noop"
            assert get_spend_ledger(LLMCallClass.EMBEDDINGS).ledger_name == "default"

    def test_the_flag_off_reads_nothing_and_therefore_needs_no_coverage(self):
        """The switch returns before the registry is read, so an unactivated ledger is not a branch."""
        with routing("internal", legacy_below_floor=False), metric_store([]):
            register_spend_ledgers()
            register_spend_cutover()

            assert get_spend_ledger(LLMCallClass.CHAT).ledger_name == "default"


class TestAnUnactivatedCoverageIsNotSplitAtAll:
    """There is no `F` to split a period at, so there is no boundary to serve two engines around."""

    @pytest.mark.parametrize(
        "coverage",
        [
            None,
            LedgerCoverage(),
            LedgerCoverage(coverage_floor=REGISTERED_FLOOR, ready=False),
            LedgerCoverage(coverage_floor=None, ready=True),
        ],
        ids=["nothing registered", "defaults", "a floor but not ready", "ready but no floor"],
    )
    def test_no_wrapper_is_installed(self, coverage):
        with routing("internal"), metric_store([]):
            register_spend_ledgers()
            if coverage is not None:
                register_ledger_coverage(coverage)

            register_spend_cutover()

            for call_class in LLMCallClass:
                assert get_spend_ledger(call_class).ledger_name == "default"


class TestWhichSlotsAreWrapped:
    def test_an_empty_slot_is_wrapped_too_so_its_classes_keep_a_pre_floor_engine(self):
        """`legacy + noop` is the right object: Elasticsearch below `F`, unavailable at and above it.

        Whether two engines can both count a row is decided by their class scope, never by whether the
        slot above them is occupied - so an empty slot costs nothing to wrap and costs its classes'
        whole pre-floor money to skip.
        """
        with routing("lite_llm", embeddings_disabled=True), metric_store([]):
            register_spend_ledgers()  # a default ledger for embeddings; nothing registers chat
            activate()

            register_spend_cutover()

            assert get_spend_ledger(LLMCallClass.EMBEDDINGS).ledger_name == "cutover(legacy|default)"
            assert get_spend_ledger(LLMCallClass.CHAT).ledger_name == "cutover(legacy|noop)"

    def test_a_registry_holding_nothing_at_all_is_left_exactly_as_it_was(self):
        """No spend port in the deployment is a different state from a port with a hole in it.

        This step composes *under* a ledger a deployment registered; it does not become that
        deployment's only ledger. Asserted on identity, because that is what "left alone" means.
        """
        with routing("lite_llm"), metric_store([]):
            register_spend_ledgers()  # every class routes to LiteLLM, so no default ledger
            activate()
            before = ledgers_by_call_class()

            register_spend_cutover()

            after = ledgers_by_call_class()

        assert all(after[call_class] is before[call_class] for call_class in LLMCallClass)
        assert {ledger.ledger_name for ledger in after.values()} == {"noop"}

    def test_both_slots_are_wrapped_when_both_are_occupied(self):
        with routing("lite_llm", embeddings_disabled=True), metric_store([]):
            register_spend_ledgers()
            register_spend_logs_ledger(_StubLedger())
            activate()

            register_spend_cutover()

            chat = get_spend_ledger(LLMCallClass.CHAT)
            embeddings = get_spend_ledger(LLMCallClass.EMBEDDINGS)

        assert chat.ledger_name == "cutover(legacy|spend_logs)"
        assert embeddings.ledger_name == "cutover(legacy|default)"
        assert chat is not embeddings, "two slots, two ledgers, two wrappers"

    def test_one_ledger_serving_both_classes_is_wrapped_exactly_once(self):
        """The slots are grouped on object identity, which is what `_distinct_ledgers` counts on."""
        with routing("internal"), metric_store([]):
            register_spend_ledgers()  # one default ledger, both classes
            activate()

            register_spend_cutover()

            assert get_spend_ledger(LLMCallClass.CHAT) is get_spend_ledger(LLMCallClass.EMBEDDINGS)

    def test_the_floor_comes_from_the_registered_coverage_and_not_from_a_second_config_read(self):
        """`PERIOD` is below the registered floor and above the configured one - the two disagree.

        Split at the registered floor the whole period is the legacy engine's, so the ledger half is
        never asked; split at the configured one it would all be the ledger's, and the figure would be
        that stub's 100 instead of the row set's 3.
        """
        stub = _StubLedger()
        with routing("lite_llm"), metric_store([CHAT_MONEY]):
            register_spend_logs_ledger(stub)
            activate()
            register_spend_cutover()

            result = asyncio.run(get_spend_ledger(LLMCallClass.CHAT).aggregate(PERIOD))

        assert stub.periods == [], "a period wholly below the registered floor is the legacy half's alone"
        assert result.figure == Decimal("3")


class TestTheMixedConfigurationCountsEveryPreFloorRowOnce:
    """Chat through LiteLLM, embeddings native: two wrappers, and both are consulted for one period."""

    def test_a_sub_floor_total_is_counted_once_rather_than_once_per_wrapper(self):
        """3 (chat) + 5 (CLI proxy) + 1 (datasource) = 9. Unscoped engines answer 9 each, so 18.

        The read goes through `SpendService`, not through one wrapper, because the doubling happens
        in the sum across wrappers rather than inside either of them - each is right on its own.
        """
        with routing("lite_llm", embeddings_disabled=True), metric_store(ALL_MONEY):
            register_spend_ledgers()
            register_spend_logs_ledger(_StubLedger())
            activate()
            register_spend_cutover()

            total = asyncio.run(spend_service.aggregate(PERIOD))

        assert total.figure == Decimal("9")

    def test_the_two_pre_floor_engines_read_disjoint_metric_names_covering_the_whole_table(self):
        """Why the total above is right: the names are partitioned, not shared and not dropped.

        Disjoint alone would be satisfied by an engine that lost a name; covering the legacy money
        table alone would be satisfied by two engines that both read everything. Together they are
        the partition, which is the same guard the two above-floor ledgers apply to their own classes.
        """
        with routing("lite_llm", embeddings_disabled=True), metric_store(ALL_MONEY) as repo:
            register_spend_ledgers()
            register_spend_logs_ledger(_StubLedger())
            activate()
            register_spend_cutover()

            asyncio.run(spend_service.aggregate(PERIOD))

        queried = metric_names_queried(repo)
        assert len(queried) == 2, "one query per pre-floor engine"
        assert not queried[0] & queried[1]
        assert queried[0] | queried[1] == LEGACY_MONEY_METRICS

    def test_the_cli_proxy_metric_lands_in_exactly_one_engine(self):
        """It is chat traffic, and it is the one name the pre-floor engines add to the guard's table.

        Added to both engines it would be summed twice; added to neither, every pre-cutoff CLI cost
        would vanish from a figure production prints today.
        """
        with routing("lite_llm", embeddings_disabled=True), metric_store(ALL_MONEY) as repo:
            register_spend_ledgers()
            register_spend_logs_ledger(_StubLedger())
            activate()
            register_spend_cutover()

            asyncio.run(spend_service.aggregate(PERIOD))

        carrying_cli = [names for names in metric_names_queried(repo) if MetricName.CLI_LLM_USAGE_TOTAL.value in names]
        assert len(carrying_cli) == 1
        assert MetricName.CONVERSATION_ASSISTANT_USAGE.value in carrying_cli[0], "the CLI proxies chat calls"


class TestASlotWhoseLedgerNeverRegistered:
    """The mixed configuration again, with the chat slot left empty - the gap, asserted as money.

    Reachable through an ordinary swallowed startup error rather than a misconfiguration:
    `rest_api/main.py` logs a LiteLLM spend-ledger construction failure and carries on, so the chat
    slot keeps the noop while `uses_litellm(CHAT)` is still true and `register_spend_ledgers` has put
    a default ledger scoped to embeddings in the other slot.
    """

    def test_the_pre_floor_money_of_the_empty_slot_is_reported_rather_than_dropped(self):
        """Skipping that slot dropped 3 (chat) + 5 (CLI proxy) and reported the datasource row alone.

        Both assertions are needed. The per-slot figure says the empty slot's own classes are being
        read; the deployment total says reading them did not also re-count the occupied slot's row -
        the same 9 the two-occupied-slots case reaches, from a differently shaped registry.
        """
        with routing("lite_llm", embeddings_disabled=True), metric_store(ALL_MONEY):
            register_spend_ledgers()  # a default ledger for embeddings; nothing registers chat
            activate()
            register_spend_cutover()

            chat = asyncio.run(get_spend_ledger(LLMCallClass.CHAT).aggregate(PERIOD))
            total = asyncio.run(spend_service.aggregate(PERIOD))

        assert chat.figure == Decimal("8"), "the chat slot's own classes: the chat row and the CLI row"
        assert total.figure == Decimal("9"), "and no row is counted twice because one slot filled late"

    def test_the_two_engines_still_partition_the_metric_names(self):
        """The empty slot's engine is scoped like any other, which is what makes wrapping it safe."""
        with routing("lite_llm", embeddings_disabled=True), metric_store(ALL_MONEY) as repo:
            register_spend_ledgers()
            activate()
            register_spend_cutover()

            asyncio.run(spend_service.aggregate(PERIOD))

        queried = metric_names_queried(repo)
        assert len(queried) == 2, "one query per pre-floor engine, empty slot included"
        assert not queried[0] & queried[1]
        assert queried[0] | queried[1] == LEGACY_MONEY_METRICS
