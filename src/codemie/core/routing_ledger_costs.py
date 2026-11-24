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

"""Recalculate a routing metric document's costs from its spend-ledger rows."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from codemie.core.routing_costs import compute_original_cost, is_top_tier

LITELLM_FAMILY = "litellm"


class LedgerSettlementStatus(StrEnum):
    SETTLED = "settled"
    PENDING = "pending"
    AMBIGUOUS = "ambiguous"
    NO_BASIS = "no_basis"


@dataclass(frozen=True)
class RunLedgerTotals:
    """Totals of one run's ledger rows that share a model group and status."""

    model_group: str | None
    status: str | None
    spend: float
    rows: int
    tokens: int


@dataclass(frozen=True)
class RequestLedgerTotals:
    """`run_id` is the smallest of the request's run ids."""

    run_id_count: int
    run_id: str | None
    spend: float
    rows: int


@dataclass(frozen=True)
class RoutingCostEstimate:
    routing_family: str | None
    requested_model: str | None
    routed_model: str | None
    classifier_model: str | None
    tier: str | None
    is_routed_cache_hit: bool
    routed_tokens: int
    original_cost_usd: float | None
    estimated_max_cost_usd: float | None
    classifier_cost_usd: float


@dataclass(frozen=True)
class SettledRoutingCosts:
    ledger_attempts: float
    original_cost_usd: float
    estimated_max_cost_usd: float
    potential_savings_usd: float
    classifier_cost_usd: float
    emitted_original_cost_usd: float
    emitted_estimated_max_cost_usd: float


@dataclass(frozen=True)
class RecalculatedRoutingCosts:
    """Only a settled status carries `settled_costs`; pending carries no ledger totals either."""

    status: LedgerSettlementStatus
    reason: str | None
    ledger_spend_usd: float | None = None
    ledger_rows: int | None = None
    settled_costs: SettledRoutingCosts | None = None


class _LedgerRowRole(StrEnum):
    ROUTED = "routed"
    CLASSIFIER = "classifier"
    FOREIGN = "foreign"


def recalculate_routing_costs(
    estimate: RoutingCostEstimate, run_totals: Sequence[RunLedgerTotals]
) -> RecalculatedRoutingCosts:
    by_role = _group_by_role(estimate, run_totals)
    pending_reason = _find_pending_reason(estimate, run_totals, by_role)
    if pending_reason is not None:
        return RecalculatedRoutingCosts(LedgerSettlementStatus.PENDING, pending_reason)

    routed_spend = _sum_spend(by_role[_LedgerRowRole.ROUTED])
    classifier_spend = _sum_spend(by_role[_LedgerRowRole.CLASSIFIER])
    ledger_spend_usd = routed_spend + classifier_spend
    ledger_rows = sum(totals.rows for totals in run_totals)
    if by_role[_LedgerRowRole.FOREIGN]:
        return RecalculatedRoutingCosts(LedgerSettlementStatus.AMBIGUOUS, None, ledger_spend_usd, ledger_rows)

    no_basis = LedgerSettlementStatus.NO_BASIS
    if estimate.original_cost_usd is None or estimate.estimated_max_cost_usd is None:
        return RecalculatedRoutingCosts(no_basis, "missing_estimate", ledger_spend_usd, ledger_rows)
    if estimate.is_routed_cache_hit:
        return RecalculatedRoutingCosts(no_basis, "cache_hit", ledger_spend_usd, ledger_rows)
    if estimate.routed_tokens <= 0:
        return RecalculatedRoutingCosts(no_basis, "no_routed_tokens", ledger_spend_usd, ledger_rows)

    settled_costs = _settle_costs(
        estimate,
        by_role[_LedgerRowRole.ROUTED],
        routed_spend=routed_spend,
        classifier_spend=classifier_spend,
        emitted_original_cost=estimate.original_cost_usd,
        emitted_estimated_max_cost=estimate.estimated_max_cost_usd,
    )
    return RecalculatedRoutingCosts(
        LedgerSettlementStatus.SETTLED, None, ledger_spend_usd, ledger_rows, settled_costs=settled_costs
    )


def _group_by_role(
    estimate: RoutingCostEstimate, run_totals: Sequence[RunLedgerTotals]
) -> dict[_LedgerRowRole, list[RunLedgerTotals]]:
    by_role: dict[_LedgerRowRole, list[RunLedgerTotals]] = {role: [] for role in _LedgerRowRole}
    for totals in run_totals:
        if not _is_ignorable_zero_spend(totals):
            by_role[_classify_ledger_row(estimate, totals)].append(totals)
    return by_role


def _is_ignorable_zero_spend(totals: RunLedgerTotals) -> bool:
    return totals.spend == 0 and (totals.status != "success" or not totals.model_group)


def _classify_ledger_row(estimate: RoutingCostEstimate, totals: RunLedgerTotals) -> _LedgerRowRole:
    """LiteLLM books the routed call under the router alias and the classifier under its model; other
    families book the picked model and have no classifier row, as their classifier call has no CodeMie ids."""
    if estimate.routing_family == LITELLM_FAMILY:
        if estimate.requested_model and totals.model_group == estimate.requested_model:
            return _LedgerRowRole.ROUTED
        if estimate.classifier_model and totals.model_group == estimate.classifier_model:
            return _LedgerRowRole.CLASSIFIER
        return _LedgerRowRole.FOREIGN
    if estimate.routed_model and totals.model_group == estimate.routed_model:
        return _LedgerRowRole.ROUTED
    return _LedgerRowRole.FOREIGN


def _find_pending_reason(
    estimate: RoutingCostEstimate,
    run_totals: Sequence[RunLedgerTotals],
    by_role: dict[_LedgerRowRole, list[RunLedgerTotals]],
) -> str | None:
    if not run_totals:
        return "no_rows"
    if not any(totals.status == "success" for totals in by_role[_LedgerRowRole.ROUTED]):
        return "no_routed_success"
    is_classifier_row_missing = estimate.classifier_cost_usd > 0 and not by_role[_LedgerRowRole.CLASSIFIER]
    if estimate.routing_family == LITELLM_FAMILY and is_classifier_row_missing:
        return "no_classifier_row"
    return None


def _sum_spend(run_totals: Sequence[RunLedgerTotals]) -> float:
    return sum((totals.spend for totals in run_totals), 0.0)


def _settle_costs(  # noqa: PLR0913 - keyword-only; the emitted costs are the estimate's, narrowed to float
    estimate: RoutingCostEstimate,
    routed_totals: Sequence[RunLedgerTotals],
    *,
    routed_spend: float,
    classifier_spend: float,
    emitted_original_cost: float,
    emitted_estimated_max_cost: float,
) -> SettledRoutingCosts:
    attempts = _count_billed_attempts(estimate.routed_tokens, routed_totals)
    classifier_cost = classifier_spend if estimate.routing_family == LITELLM_FAMILY else estimate.classifier_cost_usd
    original_cost = compute_original_cost(routed_spend, classifier_cost)
    estimated_max_cost = routed_spend if is_top_tier(estimate.tier) else emitted_estimated_max_cost * attempts
    return SettledRoutingCosts(
        ledger_attempts=attempts,
        original_cost_usd=original_cost,
        estimated_max_cost_usd=estimated_max_cost,
        potential_savings_usd=estimated_max_cost - original_cost,
        classifier_cost_usd=classifier_cost,
        emitted_original_cost_usd=emitted_original_cost,
        emitted_estimated_max_cost_usd=emitted_estimated_max_cost,
    )


def _count_billed_attempts(routed_tokens: int, routed_totals: Sequence[RunLedgerTotals]) -> float:
    """Each retry is billed, so the estimated max scales by the billed routed tokens over the document's own."""
    billed_totals = [totals for totals in routed_totals if totals.spend > 0]
    if sum(totals.rows for totals in billed_totals) <= 1:
        return 1.0
    return sum(totals.tokens for totals in billed_totals) / routed_tokens
