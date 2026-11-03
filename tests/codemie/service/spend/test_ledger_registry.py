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

"""The provider branch exists exactly once, and it is per call class.

The mixed configuration these tests pin down runs nowhere in the field, so it can be validated
only by test - which is precisely why it is tested rather than observed.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from codemie.configs import config
from codemie.service.spend.coverage import SpendResult, TimeRange
from codemie.service.spend.ledger import (
    AccessScope,
    CostState,
    PersonTotalHolder,
    ReportFilter,
    RequestCost,
    SpendLedger,
    UserTerm,
    bucket_starts,
)
from codemie.service.spend.ledger_registry import (
    _NoopSpendLedger,
    get_spend_ledger,
    ledgers_by_call_class,
    register_default_ledger,
    register_spend_logs_ledger,
    reset_ledgers,
)
from codemie.service.spend.routing import LLMCallClass
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.traffic_class import TrafficClass

PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))


class _StubLedger:
    """A stand-in that only has to be distinguishable and satisfy the protocol."""

    def __init__(self, name: str) -> None:
        self.ledger_name = name

    async def consumed_for_holder(self, holder, period, access=None):
        return SpendResult(figure=Decimal("1"))

    async def aggregate(self, period, scope=None):
        return SpendResult(figure=Decimal("1"))

    async def cost_for_request(self, *, run_id=None, request_id=None):
        return RequestCost.settled(Decimal("1"))

    async def request_count(self, period, scope=None):
        return 1


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_ledgers()
    yield
    reset_ledgers()


@pytest.fixture
def registered():
    spend_logs, default = _StubLedger("spend_logs"), _StubLedger("default")
    register_spend_logs_ledger(spend_logs)
    register_default_ledger(default)
    return spend_logs, default


@contextmanager
def routing(mode: str, *, embeddings_disabled: bool = False, enabled: bool = True, has_litellm: bool = True):
    """Pin the four flags that decide where a call class routes."""
    with (
        patch("codemie.enterprise.HAS_LITELLM", has_litellm),
        patch.object(config, "LLM_PROXY_ENABLED", enabled),
        patch.object(config, "LLM_PROXY_MODE", mode),
        patch.object(config, "LLM_PROXY_EMBEDDINGS_DISABLED", embeddings_disabled),
    ):
        yield


class TestSelectionFollowsRouting:
    def test_a_fully_litellm_deployment_serves_both_classes_from_the_ledger(self, registered):
        spend_logs, _ = registered
        with routing("lite_llm"):
            assert get_spend_ledger(LLMCallClass.CHAT) is spend_logs
            assert get_spend_ledger(LLMCallClass.EMBEDDINGS) is spend_logs

    def test_internal_mode_serves_neither_class_from_the_ledger(self, registered):
        """`LLM_PROXY_ENABLED=true` with the default `internal` mode sends nothing to LiteLLM.

        Treating it as a LiteLLM deployment points every consumer at a table no request writes
        to. It is a wholly non-LiteLLM deployment and needs no composition.
        """
        _, default = registered
        with routing("internal"):
            assert get_spend_ledger(LLMCallClass.CHAT) is default
            assert get_spend_ledger(LLMCallClass.EMBEDDINGS) is default

    def test_the_mixed_configuration_splits_the_two_classes(self, registered):
        """Chat through LiteLLM, embeddings native - the case a single boolean cannot express.

        Each class is served by exactly one ledger, so no row is counted twice and none dropped.
        """
        spend_logs, default = registered
        with routing("lite_llm", embeddings_disabled=True):
            assert get_spend_ledger(LLMCallClass.CHAT) is spend_logs
            assert get_spend_ledger(LLMCallClass.EMBEDDINGS) is default

    def test_every_class_is_owned_by_exactly_one_ledger(self, registered):
        with routing("lite_llm", embeddings_disabled=True):
            mapping = ledgers_by_call_class()

        assert set(mapping) == set(LLMCallClass), "every class must be owned"
        assert len({id(ledger) for ledger in mapping.values()}) == 2


class TestTheUnregisteredCase:
    @pytest.mark.asyncio
    async def test_with_nothing_registered_a_ranged_read_is_unavailable_not_zero(self):
        ledger = get_spend_ledger(LLMCallClass.CHAT)
        result = await ledger.aggregate(PERIOD)

        assert result.figure is None, "a fabricated zero claims nothing was spent"
        assert result.unavailable == PERIOD

    @pytest.mark.asyncio
    async def test_with_nothing_registered_an_id_read_is_unavailable_not_pending(self):
        """Pending promises an answer shortly and drives a retry that cannot succeed."""
        ledger = get_spend_ledger(LLMCallClass.CHAT)
        cost = await ledger.cost_for_request(run_id="run-1")

        assert cost.state is CostState.UNAVAILABLE
        assert cost.figure is None

    def test_the_noop_satisfies_the_port(self):
        assert isinstance(get_spend_ledger(LLMCallClass.CHAT), SpendLedger)

    def test_registering_only_the_litellm_ledger_still_answers_for_a_native_class(self):
        """A half-configured registry must not fall through to nothing."""
        spend_logs = _StubLedger("spend_logs")
        register_spend_logs_ledger(spend_logs)

        with routing("lite_llm", embeddings_disabled=True):
            assert get_spend_ledger(LLMCallClass.CHAT) is spend_logs
            assert get_spend_ledger(LLMCallClass.EMBEDDINGS).ledger_name == "noop"


class TestAClassIsNeverServedByALedgerNotBuiltForIt:
    def test_registering_only_the_default_ledger_leaves_a_litellm_class_unavailable(self):
        """The mirror of the half-configured case above, and the one that could report a WRONG number.

        The default ledger scopes every query to the metric names of the classes it was constructed
        with. Handed a chat question in a deployment where chat routes to LiteLLM, it would answer
        with an embeddings total - smaller, and indistinguishable from a real figure. Unavailable is
        the honest answer.
        """
        default = _StubLedger("default")
        register_default_ledger(default)

        with routing("lite_llm", embeddings_disabled=True):
            assert get_spend_ledger(LLMCallClass.CHAT).ledger_name == "noop"
            assert get_spend_ledger(LLMCallClass.EMBEDDINGS) is default


class TestTheNoopAnswersTheWholeWindowReadsWithoutFabricatingAZero:
    """The two summary reads, on the deployment with no ledger at all - and the answer is *unavailable*.

    A zero here is the whole defect this ledger exists to avoid, and on these two reads it is the most
    tempting one: both are keyed mappings whose keys are known up front, so filling them with
    `Decimal(0)` produces a chart and a set of cards that render perfectly and claim nothing was spent.
    Every key is present - an absent one renders as a blank rather than as the state it is in - and
    every value carries no figure.

    **The boundary and the filter are accepted and applied to nothing**, deliberately: an unavailable
    state discloses nothing, so narrowing it could only turn it into a zero.
    """

    ALICE = AccessScope(unrestricted=False, user_id="alice", plain_projects=("A",))
    FILTER = ReportFilter(users=(UserTerm(user_id="alice", aliases=frozenset()),), projects=("A",))

    @pytest.mark.asyncio
    async def test_every_spend_group_is_answered_unavailable(self) -> None:
        answers = await _NoopSpendLedger().aggregate_by_spend_group(PERIOD)

        assert set(answers) == set(SpendGroup), "an omitted group would read as traffic this ledger cannot carry"
        assert all(answer.figure is None for answer in answers.values())
        assert all(answer.unavailable == PERIOD for answer in answers.values())

    @pytest.mark.asyncio
    async def test_the_group_read_takes_the_boundary_and_the_filter_and_still_says_unavailable(self) -> None:
        answers = await _NoopSpendLedger().aggregate_by_spend_group(
            PERIOD, access=self.ALICE, report_filter=self.FILTER
        )

        assert all(answer.figure is None for answer in answers.values())

    @pytest.mark.asyncio
    async def test_the_holder_batch_takes_the_boundary_and_the_filter_and_still_says_unavailable(self) -> None:
        """The users- and projects-spending tables hand the batch both; neither may turn it into a zero."""
        holder = PersonTotalHolder(user_id="alice", aliases=frozenset())

        answers = await _NoopSpendLedger().consumed_for_holders([holder], PERIOD, self.ALICE, report_filter=self.FILTER)

        assert set(answers) == {holder}
        assert answers[holder].figure is None
        assert answers[holder].unavailable == PERIOD

    @pytest.mark.asyncio
    async def test_every_walked_bucket_is_answered_unavailable_for_every_class(self) -> None:
        answers = await _NoopSpendLedger().aggregate_by_time_bucket(PERIOD, "week")

        expected = {(start, traffic_class) for start in bucket_starts(PERIOD, "week") for traffic_class in TrafficClass}
        assert set(answers) == expected, "a chart walked these buckets and a missing key draws a gap"
        assert all(answer.figure is None for answer in answers.values())

    @pytest.mark.asyncio
    async def test_the_bucket_read_takes_the_boundary_and_the_filter_positionally_as_the_port_spells_them(self) -> None:
        """The port's own argument order, so a positional call from the cutover cannot land them wrong."""
        answers = await _NoopSpendLedger().aggregate_by_time_bucket(PERIOD, "week", None, self.ALICE, self.FILTER)

        assert set(answers) == {
            (start, traffic_class) for start in bucket_starts(PERIOD, "week") for traffic_class in TrafficClass
        }
        assert all(answer.figure is None for answer in answers.values())
