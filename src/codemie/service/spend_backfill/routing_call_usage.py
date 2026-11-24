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

"""Backfill of `routing_call_usage` documents: their ledger rows and recalculated routing costs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from codemie.clients.postgres import get_ledger_async_session
from codemie.core.routing_ledger_costs import (
    LITELLM_FAMILY,
    LedgerSettlementStatus,
    RecalculatedRoutingCosts,
    RequestLedgerTotals,
    RoutingCostEstimate,
    RunLedgerTotals,
    SettledRoutingCosts,
    recalculate_routing_costs,
)
from codemie.repository.spend_backfill_metrics_repository import MetricDoc
from codemie.repository.spend_logs_repository import SpendLogsRepository, spend_logs_repository
from codemie.service.analytics.metric_names import MetricName
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend_backfill.spend_doc_type import DocUpdate, SpendDocType

_SETTLEMENT_VERSION = 1
_COST_FIELDS = ("original_cost_usd", "estimated_max_cost_usd", "potential_savings_usd")
_CLASSIFIER_COST_FIELD = "classifier_cost_usd"
_SETTLEMENT_FIELD = "ledger_settlement"
_PENDING_REASON_FIELD = "ledger_pending_reason"
_SOURCE_ATTRIBUTES = (
    _SETTLEMENT_FIELD,
    _PENDING_REASON_FIELD,
    "llm_run_id",
    "request_id",
    "endpoint",
    "requested_model",
    "routed_model",
    "classifier_model",
    "routing_family",
    "tier",
    "routed_cache_hit",
    "routed_input_tokens",
    "routed_output_tokens",
    *_COST_FIELDS,
    _CLASSIFIER_COST_FIELD,
    *(f"{name}_estimated" for name in (*_COST_FIELDS, _CLASSIFIER_COST_FIELD)),
)


class RoutingCallUsage(SpendDocType):
    """Routing call documents, matched by run id, or by request id resolved to one run id for old proxy documents."""

    metric_name = MetricName.ROUTING_CALL_USAGE.value
    settlement_version = _SETTLEMENT_VERSION
    source_fields = tuple(f"attributes.{name}" for name in _SOURCE_ATTRIBUTES)

    def __init__(self, repository: SpendLogsRepository = spend_logs_repository) -> None:
        self._repository = repository

    async def build_updates(
        self, docs: Sequence[MetricDoc], ledger_period: TimeRange, now: datetime, timeout: float
    ) -> list[DocUpdate]:
        run_id_or_updates = await self._find_run_ids(docs, ledger_period, now, timeout)
        run_ids = [value for value in run_id_or_updates if isinstance(value, str)]
        totals_by_run_id = await self._sum_ledger_by_run_id(run_ids, ledger_period, timeout)
        return [
            value
            if isinstance(value, DocUpdate)
            else _build_update_from_run_totals(doc.attributes, totals_by_run_id.get(value, []), now)
            for doc, value in zip(docs, run_id_or_updates, strict=True)
        ]

    async def _find_run_ids(
        self, docs: Sequence[MetricDoc], ledger_period: TimeRange, now: datetime, timeout: float
    ) -> list[str | DocUpdate]:
        """Returns one run id per document, or its update when it has no run id to settle by."""
        request_ids = [doc.attributes["request_id"] for doc in docs if _is_keyed_by_request_id(doc.attributes)]
        totals_by_request_id = await self._sum_ledger_by_request_id(request_ids, ledger_period, timeout)
        run_id_or_updates: list[str | DocUpdate] = []
        for doc in docs:
            attributes = doc.attributes
            if attributes.get("llm_run_id"):
                run_id_or_updates.append(attributes["llm_run_id"])
            elif _is_keyed_by_request_id(attributes):
                totals = totals_by_request_id.get(attributes["request_id"])
                run_id_or_updates.append(_find_run_id_of_request(attributes, totals, now))
            else:
                no_basis = LedgerSettlementStatus.NO_BASIS
                run_id_or_updates.append(
                    DocUpdate(no_basis, "no_ledger_key", _build_ledger_status_fields(no_basis, now))
                )
        return run_id_or_updates

    async def _sum_ledger_by_request_id(
        self, request_ids: Sequence[str], ledger_period: TimeRange, timeout: float
    ) -> dict[str, RequestLedgerTotals]:
        if not request_ids:
            return {}
        async with get_ledger_async_session() as session:
            return await self._repository.sum_ledger_rows_by_request_id(
                session, list(dict.fromkeys(request_ids)), ledger_period, timeout=timeout
            )

    async def _sum_ledger_by_run_id(
        self, run_ids: Sequence[str], ledger_period: TimeRange, timeout: float
    ) -> dict[str, list[RunLedgerTotals]]:
        if not run_ids:
            return {}
        async with get_ledger_async_session() as session:
            return await self._repository.sum_ledger_rows_by_run_id(
                session, list(dict.fromkeys(run_ids)), ledger_period, timeout=timeout
            )


def _is_keyed_by_request_id(attributes: dict[str, Any]) -> bool:
    return not attributes.get("llm_run_id") and bool(attributes.get("endpoint") and attributes.get("request_id"))


def _find_run_id_of_request(
    attributes: dict[str, Any], totals: RequestLedgerTotals | None, now: datetime
) -> str | DocUpdate:
    if totals is None:
        return _build_pending_update(attributes, "no_rows", now)
    if totals.run_id_count > 1:
        ambiguous = LedgerSettlementStatus.AMBIGUOUS
        attributes_update = _build_ledger_status_fields(ambiguous, now, totals.spend, totals.rows)
        return DocUpdate(ambiguous, "several_run_ids", attributes_update)
    if totals.run_id_count == 0 or not totals.run_id:
        return _build_pending_update(attributes, "no_run_id_on_rows", now)
    return totals.run_id


def _build_pending_update(attributes: dict[str, Any], reason: str | None, now: datetime) -> DocUpdate:
    """Marks the document pending without a settlement version, so it stays selected and is retried every run."""
    pending = LedgerSettlementStatus.PENDING
    if attributes.get(_SETTLEMENT_FIELD) == pending.value and attributes.get(_PENDING_REASON_FIELD) == reason:
        return DocUpdate(pending, reason, None)
    attributes_update = {
        _SETTLEMENT_FIELD: pending.value,
        _PENDING_REASON_FIELD: reason,
        "ledger_settled_at": now.isoformat(),
    }
    return DocUpdate(pending, reason, attributes_update)


def _build_update_from_run_totals(
    attributes: dict[str, Any], run_totals: Sequence[RunLedgerTotals], now: datetime
) -> DocUpdate:
    estimate = _read_cost_estimate(attributes)
    costs = recalculate_routing_costs(estimate, run_totals)
    if costs.settled_costs is not None:
        return _build_settled_update(attributes, estimate, costs, costs.settled_costs, now)
    if costs.status == LedgerSettlementStatus.PENDING:
        return _build_pending_update(attributes, costs.reason, now)
    attributes_update = _build_ledger_status_fields(costs.status, now, costs.ledger_spend_usd, costs.ledger_rows)
    return DocUpdate(costs.status, costs.reason, attributes_update)


def _read_cost_estimate(attributes: dict[str, Any]) -> RoutingCostEstimate:
    return RoutingCostEstimate(
        routing_family=attributes.get("routing_family"),
        requested_model=attributes.get("requested_model"),
        routed_model=attributes.get("routed_model"),
        classifier_model=attributes.get("classifier_model"),
        tier=attributes.get("tier"),
        is_routed_cache_hit=bool(attributes.get("routed_cache_hit")),
        routed_tokens=int(attributes.get("routed_input_tokens") or 0)
        + int(attributes.get("routed_output_tokens") or 0),
        original_cost_usd=_read_emitted_value(attributes, "original_cost_usd"),
        estimated_max_cost_usd=_read_emitted_value(attributes, "estimated_max_cost_usd"),
        classifier_cost_usd=_read_emitted_value(attributes, _CLASSIFIER_COST_FIELD) or 0.0,
    )


def _read_emitted_value(attributes: dict[str, Any], name: str) -> float | None:
    """A settled document keeps its emission-time value in the `*_estimated` copy."""
    value = attributes.get(f"{name}_estimated")
    if value is None:
        value = attributes.get(name)
    return None if value is None else float(value)


def _build_settled_update(
    attributes: dict[str, Any],
    estimate: RoutingCostEstimate,
    costs: RecalculatedRoutingCosts,
    settled_costs: SettledRoutingCosts,
    now: datetime,
) -> DocUpdate:
    emitted_savings = _read_emitted_value(attributes, "potential_savings_usd")
    if emitted_savings is None:
        emitted_savings = settled_costs.emitted_estimated_max_cost_usd - settled_costs.emitted_original_cost_usd
    emitted_values = {
        "original_cost_usd": settled_costs.emitted_original_cost_usd,
        "estimated_max_cost_usd": settled_costs.emitted_estimated_max_cost_usd,
        "potential_savings_usd": emitted_savings,
    }
    settled_values = {
        "original_cost_usd": settled_costs.original_cost_usd,
        "estimated_max_cost_usd": settled_costs.estimated_max_cost_usd,
        "potential_savings_usd": settled_costs.potential_savings_usd,
    }
    if estimate.routing_family == LITELLM_FAMILY:
        emitted_values[_CLASSIFIER_COST_FIELD] = estimate.classifier_cost_usd
        settled_values[_CLASSIFIER_COST_FIELD] = settled_costs.classifier_cost_usd

    attributes_update = _build_ledger_status_fields(costs.status, now, costs.ledger_spend_usd, costs.ledger_rows)
    attributes_update["ledger_attempts"] = settled_costs.ledger_attempts
    attributes_update.update(settled_values)
    attributes_update.update(
        {
            f"{name}_estimated": value
            for name, value in emitted_values.items()
            if attributes.get(f"{name}_estimated") is None
        }
    )

    original_cost_before = settled_costs.emitted_original_cost_usd
    original_cost_after = settled_costs.original_cost_usd
    log_maxima: dict[str, float] = {}
    if original_cost_before:
        log_maxima["max_relative_change"] = abs(original_cost_after - original_cost_before) / abs(original_cost_before)
    return DocUpdate(
        costs.status,
        None,
        attributes_update,
        log_sums={
            "original_cost_before": original_cost_before,
            "original_cost_after": original_cost_after,
            "savings_before": emitted_savings,
            "savings_after": settled_costs.potential_savings_usd,
        },
        log_maxima=log_maxima,
    )


def _build_ledger_status_fields(
    status: LedgerSettlementStatus,
    now: datetime,
    ledger_spend_usd: float | None = None,
    ledger_rows: int | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        _SETTLEMENT_FIELD: status.value,
        _PENDING_REASON_FIELD: None,
        "ledger_settlement_version": _SETTLEMENT_VERSION,
        "ledger_settled_at": now.isoformat(),
    }
    if ledger_spend_usd is not None and ledger_rows is not None:
        fields["ledger_spend_usd"] = ledger_spend_usd
        fields["ledger_rows"] = ledger_rows
    return fields
