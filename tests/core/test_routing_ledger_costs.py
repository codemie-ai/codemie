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

from dataclasses import replace

import pytest

from codemie.core.routing_ledger_costs import (
    LedgerSettlementStatus,
    RoutingCostEstimate,
    RunLedgerTotals,
    recalculate_routing_costs,
)

ROUTER = "gpt-auto-llm"
PICKED = "gpt-4.1"
CLASSIFIER = "gpt-4.1-mini"


def _litellm_estimate(**overrides) -> RoutingCostEstimate:
    estimate = RoutingCostEstimate(
        routing_family="litellm",
        requested_model=ROUTER,
        routed_model=PICKED,
        classifier_model=CLASSIFIER,
        tier="simple",
        is_routed_cache_hit=False,
        routed_tokens=1000,
        original_cost_usd=0.0102,
        estimated_max_cost_usd=0.05,
        classifier_cost_usd=0.0002,
    )
    return replace(estimate, **overrides)


def _switchyard_estimate(**overrides) -> RoutingCostEstimate:
    estimate = RoutingCostEstimate(
        routing_family="switchyard",
        requested_model="switchyard-auto",
        routed_model=PICKED,
        classifier_model=CLASSIFIER,
        tier="simple",
        is_routed_cache_hit=False,
        routed_tokens=1000,
        original_cost_usd=0.0102,
        estimated_max_cost_usd=0.05,
        classifier_cost_usd=0.0002,
    )
    return replace(estimate, **overrides)


def _totals(model_group, spend, *, status="success", rows=1, tokens=1000) -> RunLedgerTotals:
    return RunLedgerTotals(model_group=model_group, status=status, spend=spend, rows=rows, tokens=tokens)


def test_recalculate_without_ledger_rows_is_pending_no_rows():
    costs = recalculate_routing_costs(_litellm_estimate(), [])

    assert costs.status == LedgerSettlementStatus.PENDING
    assert costs.reason == "no_rows"


def test_recalculate_without_successful_routed_row_is_pending():
    run_totals = [_totals(ROUTER, 0.0, status="failure"), _totals(CLASSIFIER, 0.0002)]

    costs = recalculate_routing_costs(_litellm_estimate(), run_totals)

    assert costs.status == LedgerSettlementStatus.PENDING
    assert costs.reason == "no_routed_success"


def test_recalculate_litellm_with_classifier_cost_but_no_classifier_row_is_pending():
    costs = recalculate_routing_costs(_litellm_estimate(), [_totals(ROUTER, 0.0100)])

    assert costs.status == LedgerSettlementStatus.PENDING
    assert costs.reason == "no_classifier_row"


def test_recalculate_litellm_without_classifier_cost_settles_without_classifier_row():
    costs = recalculate_routing_costs(_litellm_estimate(classifier_cost_usd=0.0), [_totals(ROUTER, 0.0100)])

    assert costs.status == LedgerSettlementStatus.SETTLED
    assert costs.settled_costs.classifier_cost_usd == 0.0


def test_recalculate_with_foreign_row_is_ambiguous():
    run_totals = [_totals(ROUTER, 0.0100), _totals(CLASSIFIER, 0.0002), _totals("other-model", 0.0300)]

    costs = recalculate_routing_costs(_litellm_estimate(), run_totals)

    assert costs.status == LedgerSettlementStatus.AMBIGUOUS
    assert costs.reason is None
    assert costs.ledger_spend_usd == pytest.approx(0.0102)
    assert costs.ledger_rows == 3
    assert costs.settled_costs is None


def test_recalculate_litellm_treats_picked_model_row_as_foreign():
    run_totals = [_totals(ROUTER, 0.0100), _totals(CLASSIFIER, 0.0002), _totals(PICKED, 0.0100)]

    costs = recalculate_routing_costs(_litellm_estimate(), run_totals)

    assert costs.status == LedgerSettlementStatus.AMBIGUOUS


def test_recalculate_switchyard_treats_classifier_model_row_as_foreign():
    run_totals = [_totals(PICKED, 0.0100), _totals(CLASSIFIER, 0.0002)]

    costs = recalculate_routing_costs(_switchyard_estimate(), run_totals)

    assert costs.status == LedgerSettlementStatus.AMBIGUOUS


@pytest.mark.parametrize("missing", ["original_cost_usd", "estimated_max_cost_usd"])
def test_recalculate_without_estimate_is_no_basis(missing):
    run_totals = [_totals(ROUTER, 0.0100), _totals(CLASSIFIER, 0.0002)]

    costs = recalculate_routing_costs(_litellm_estimate(**{missing: None}), run_totals)

    assert costs.status == LedgerSettlementStatus.NO_BASIS
    assert costs.reason == "missing_estimate"
    assert costs.ledger_spend_usd == pytest.approx(0.0102)
    assert costs.ledger_rows == 2


def test_recalculate_cache_hit_is_no_basis():
    run_totals = [_totals(ROUTER, 0.0100), _totals(CLASSIFIER, 0.0002)]

    costs = recalculate_routing_costs(_litellm_estimate(is_routed_cache_hit=True), run_totals)

    assert costs.status == LedgerSettlementStatus.NO_BASIS
    assert costs.reason == "cache_hit"


def test_recalculate_without_routed_tokens_is_no_basis():
    run_totals = [_totals(ROUTER, 0.0100), _totals(CLASSIFIER, 0.0002)]

    costs = recalculate_routing_costs(_litellm_estimate(routed_tokens=0), run_totals)

    assert costs.status == LedgerSettlementStatus.NO_BASIS
    assert costs.reason == "no_routed_tokens"


def test_recalculate_ignores_zero_spend_failures_and_unnamed_rows_but_counts_them():
    run_totals = [
        _totals(ROUTER, 0.0100),
        _totals(CLASSIFIER, 0.0002),
        _totals("other-model", 0.0, status="failure", rows=2),
        _totals(None, 0.0, rows=1),
        _totals("", 0.0, rows=1),
    ]

    costs = recalculate_routing_costs(_litellm_estimate(), run_totals)

    assert costs.status == LedgerSettlementStatus.SETTLED
    assert costs.ledger_rows == 6
    assert costs.ledger_spend_usd == pytest.approx(0.0102)


def test_recalculate_single_paid_attempt_keeps_estimate():
    run_totals = [
        _totals(ROUTER, 0.0100, tokens=1000),
        _totals(ROUTER, 0.0, status="failure", rows=1, tokens=1000),
        _totals(CLASSIFIER, 0.0002),
    ]

    costs = recalculate_routing_costs(_litellm_estimate(), run_totals)

    assert costs.status == LedgerSettlementStatus.SETTLED
    assert costs.settled_costs.ledger_attempts == 1.0
    assert costs.settled_costs.estimated_max_cost_usd == pytest.approx(0.05)


def test_recalculate_retried_call_scales_estimate_by_billed_attempts():
    run_totals = [
        _totals(ROUTER, 0.0300, rows=3, tokens=3000),
        _totals(CLASSIFIER, 0.0006, rows=3, tokens=300),
    ]

    costs = recalculate_routing_costs(_litellm_estimate(), run_totals)

    assert costs.settled_costs.ledger_attempts == pytest.approx(3.0)
    assert costs.settled_costs.estimated_max_cost_usd == pytest.approx(0.15)


def test_recalculate_complex_tier_uses_routed_spend_as_estimate():
    run_totals = [_totals(ROUTER, 0.0300, rows=2, tokens=2000), _totals(CLASSIFIER, 0.0004, rows=2)]

    costs = recalculate_routing_costs(_litellm_estimate(tier="complex"), run_totals)

    assert costs.settled_costs.estimated_max_cost_usd == pytest.approx(0.0300)
    assert costs.settled_costs.original_cost_usd == pytest.approx(0.0304)
    assert costs.settled_costs.potential_savings_usd == pytest.approx(-0.0004)


def test_recalculate_litellm_takes_classifier_from_ledger():
    run_totals = [_totals(ROUTER, 0.0100), _totals(CLASSIFIER, 0.0003)]

    costs = recalculate_routing_costs(_litellm_estimate(classifier_cost_usd=0.0002), run_totals)

    assert costs.settled_costs.classifier_cost_usd == pytest.approx(0.0003)
    assert costs.settled_costs.original_cost_usd == pytest.approx(0.0103)


def test_recalculate_switchyard_takes_classifier_from_document():
    costs = recalculate_routing_costs(_switchyard_estimate(classifier_cost_usd=0.0002), [_totals(PICKED, 0.0100)])

    assert costs.status == LedgerSettlementStatus.SETTLED
    assert costs.settled_costs.classifier_cost_usd == pytest.approx(0.0002)
    assert costs.settled_costs.original_cost_usd == pytest.approx(0.0102)
    assert costs.ledger_spend_usd == pytest.approx(0.0100)
    assert costs.settled_costs.potential_savings_usd == pytest.approx(0.0398)


def test_one_retry_doubles_estimated_max_and_savings():
    run_totals = [
        _totals(ROUTER, 0.0200, rows=2, tokens=2000),
        _totals(CLASSIFIER, 0.0004, rows=2, tokens=200),
    ]

    costs = recalculate_routing_costs(_litellm_estimate(), run_totals)

    assert costs.status == LedgerSettlementStatus.SETTLED
    assert costs.reason is None
    assert costs.ledger_spend_usd == pytest.approx(0.0204)
    assert costs.ledger_rows == 4
    settled = costs.settled_costs
    assert settled.ledger_attempts == pytest.approx(2.0)
    assert settled.original_cost_usd == pytest.approx(0.0204)
    assert settled.estimated_max_cost_usd == pytest.approx(0.1000)
    assert settled.potential_savings_usd == pytest.approx(0.0796)
    assert settled.classifier_cost_usd == pytest.approx(0.0004)
    assert (settled.emitted_original_cost_usd, settled.emitted_estimated_max_cost_usd) == (0.0102, 0.05)


def test_recalculate_returns_float_money_and_int_rows():
    run_totals = [
        _totals(ROUTER, 0.0200, rows=2, tokens=2000),
        _totals(CLASSIFIER, 0.0004, rows=2, tokens=200),
        _totals("other-model", 0.0, status="failure"),
    ]

    costs = recalculate_routing_costs(_litellm_estimate(), run_totals)

    settled = costs.settled_costs
    for value in (
        costs.ledger_spend_usd,
        settled.ledger_attempts,
        settled.original_cost_usd,
        settled.estimated_max_cost_usd,
        settled.potential_savings_usd,
        settled.classifier_cost_usd,
    ):
        assert type(value) is float
    assert type(costs.ledger_rows) is int


def test_recalculate_zero_values_are_floats():
    costs = recalculate_routing_costs(
        _litellm_estimate(classifier_cost_usd=0.0, estimated_max_cost_usd=0),
        [_totals(ROUTER, 0.0, status="success", tokens=0)],
    )

    assert costs.status == LedgerSettlementStatus.SETTLED
    settled = costs.settled_costs
    for value in (
        costs.ledger_spend_usd,
        settled.original_cost_usd,
        settled.estimated_max_cost_usd,
        settled.potential_savings_usd,
        settled.classifier_cost_usd,
    ):
        assert type(value) is float
        assert value == 0.0


def test_recalculate_litellm_shared_model_settles_with_classifier_row_as_classifier():
    estimate = _litellm_estimate(routed_model=CLASSIFIER)
    run_totals = [_totals(ROUTER, 0.0100), _totals(CLASSIFIER, 0.0002)]

    costs = recalculate_routing_costs(estimate, run_totals)

    assert costs.status == LedgerSettlementStatus.SETTLED
    assert costs.settled_costs.classifier_cost_usd == pytest.approx(0.0002)
    assert costs.settled_costs.original_cost_usd == pytest.approx(0.0102)


def test_recalculate_switchyard_shared_model_settles_with_row_as_routed():
    estimate = _switchyard_estimate(routed_model=CLASSIFIER)

    costs = recalculate_routing_costs(estimate, [_totals(CLASSIFIER, 0.0100)])

    assert costs.status == LedgerSettlementStatus.SETTLED
    assert costs.settled_costs.classifier_cost_usd == pytest.approx(0.0002)
    assert costs.settled_costs.original_cost_usd == pytest.approx(0.0102)
    assert costs.ledger_spend_usd == pytest.approx(0.0100)
