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

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from elasticsearch.serializer import JSONSerializer

from codemie.core.routing_ledger_costs import RequestLedgerTotals, RunLedgerTotals
from codemie.repository.spend_backfill_metrics_repository import LEDGER_ATTRIBUTES_MAPPING, MetricDoc
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend_backfill.routing_call_usage import RoutingCallUsage

NOW = datetime(2026, 11, 20, 12, 30, tzinfo=UTC)
PERIOD = TimeRange(datetime(2026, 11, 19, tzinfo=UTC), datetime(2026, 11, 20, 12, tzinfo=UTC))

LITELLM = {
    "routing_family": "litellm",
    "requested_model": "gpt-auto-llm",
    "routed_model": "gpt-4.1",
    "classifier_model": "gpt-4.1-mini",
    "tier": "medium",
    "routed_cache_hit": False,
    "routed_input_tokens": 800,
    "routed_output_tokens": 200,
    "original_cost_usd": 0.0102,
    "estimated_max_cost_usd": 0.05,
    "potential_savings_usd": 0.0398,
    "classifier_cost_usd": 0.0002,
}

SWITCHYARD = {
    **LITELLM,
    "routing_family": "switchyard",
    "requested_model": "switchyard-auto",
    "classifier_model": "gpt-4.1-nano",
}

RETRIED_LITELLM_TOTALS = [
    RunLedgerTotals("gpt-auto-llm", "success", 0.0200, 2, 2000),
    RunLedgerTotals("gpt-4.1-mini", "success", 0.0004, 2, 300),
]

MONEY = {
    "ledger_spend_usd",
    "ledger_attempts",
    "original_cost_usd",
    "estimated_max_cost_usd",
    "potential_savings_usd",
    "classifier_cost_usd",
    "original_cost_usd_estimated",
    "estimated_max_cost_usd_estimated",
    "potential_savings_usd_estimated",
    "classifier_cost_usd_estimated",
}
COUNTS = {"ledger_rows", "ledger_settlement_version"}


def make_doc(doc_id: str, **attributes) -> MetricDoc:
    return MetricDoc(
        index="codemie_metrics_logs-2026-q4",
        id=doc_id,
        seq_no=3,
        primary_term=1,
        timestamp=datetime(2026, 11, 20, 12, tzinfo=UTC),
        sort_values=[1_763_640_000_000, 7],
        attributes=attributes,
    )


async def build_updates(doc_type, docs, timeout: float = 10.0):
    return await doc_type.build_updates(docs, PERIOD, NOW, timeout)


@pytest.fixture
def session():
    session = MagicMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture(autouse=True)
def ledger_session(session):
    @asynccontextmanager
    async def fake_session():
        yield session

    with patch(
        "codemie.service.spend_backfill.routing_call_usage.get_ledger_async_session", side_effect=fake_session
    ) as factory:
        yield factory


@pytest.fixture
def repository():
    repository = MagicMock()
    repository.sum_ledger_rows_by_request_id = AsyncMock(return_value={})
    repository.sum_ledger_rows_by_run_id = AsyncMock(return_value={})
    return repository


@pytest.fixture
def doc_type(repository):
    return RoutingCallUsage(repository=repository)


def test_doc_type_selects_routing_usage_documents_with_their_ledger_keys_and_emitted_copies():
    assert RoutingCallUsage.metric_name == "routing_call_usage"
    assert RoutingCallUsage.settlement_version == 1
    assert "attributes.llm_run_id" in RoutingCallUsage.source_fields
    assert "attributes.original_cost_usd_estimated" in RoutingCallUsage.source_fields
    assert "attributes.ledger_settlement" in RoutingCallUsage.source_fields
    assert "attributes.ledger_pending_reason" in RoutingCallUsage.source_fields


@pytest.mark.asyncio
async def test_retried_litellm_call_settles_with_ledger_costs_and_keeps_emitted_copies(doc_type, repository):
    repository.sum_ledger_rows_by_run_id.return_value = {"run-1": RETRIED_LITELLM_TOTALS}

    [update] = await build_updates(doc_type, [make_doc("d1", llm_run_id="run-1", **LITELLM)])

    assert update.status == "settled"
    assert update.reason is None
    repository.sum_ledger_rows_by_request_id.assert_not_called()
    assert repository.sum_ledger_rows_by_run_id.await_args.args[1:] == (["run-1"], PERIOD)
    attributes = update.attributes
    assert attributes["ledger_settlement"] == "settled"
    assert attributes["ledger_settlement_version"] == 1
    assert datetime.fromisoformat(attributes["ledger_settled_at"]).utcoffset() == timedelta(0)
    assert attributes["ledger_spend_usd"] == pytest.approx(0.0204)
    assert attributes["ledger_rows"] == 4
    assert attributes["ledger_attempts"] == pytest.approx(2.0)
    assert attributes["original_cost_usd"] == pytest.approx(0.0204)
    assert attributes["estimated_max_cost_usd"] == pytest.approx(0.1)
    assert attributes["potential_savings_usd"] == pytest.approx(0.0796)
    assert attributes["classifier_cost_usd"] == pytest.approx(0.0004)
    assert attributes["original_cost_usd_estimated"] == pytest.approx(0.0102)
    assert attributes["estimated_max_cost_usd_estimated"] == pytest.approx(0.05)
    assert attributes["potential_savings_usd_estimated"] == pytest.approx(0.0398)
    assert attributes["classifier_cost_usd_estimated"] == pytest.approx(0.0002)


@pytest.mark.asyncio
async def test_settled_update_reports_cost_sums_and_largest_relative_change(doc_type, repository):
    repository.sum_ledger_rows_by_run_id.return_value = {"run-1": RETRIED_LITELLM_TOTALS}

    [update] = await build_updates(doc_type, [make_doc("d1", llm_run_id="run-1", **LITELLM)])

    assert update.log_sums == pytest.approx(
        {
            "original_cost_before": 0.0102,
            "original_cost_after": 0.0204,
            "savings_before": 0.0398,
            "savings_after": 0.0796,
        }
    )
    assert update.log_maxima == pytest.approx({"max_relative_change": 1.0})


@pytest.mark.asyncio
async def test_each_ledger_read_opens_its_own_session_and_gets_the_time_left(
    doc_type, repository, session, ledger_session
):
    repository.sum_ledger_rows_by_request_id.return_value = {"req-1": RequestLedgerTotals(1, "run-9", 0.0204, 4)}
    repository.sum_ledger_rows_by_run_id.return_value = {"run-9": RETRIED_LITELLM_TOTALS}
    doc = make_doc("d1", endpoint="/v1/chat/completions", request_id="req-1", **LITELLM)

    await build_updates(doc_type, [doc], 5.0)

    assert ledger_session.call_count == 2
    for read in (repository.sum_ledger_rows_by_request_id, repository.sum_ledger_rows_by_run_id):
        assert read.await_args.args[0] is session
        assert read.await_args.kwargs["timeout"] == 5.0


@pytest.mark.asyncio
async def test_request_grain_with_one_run_id_continues_as_run_grain(doc_type, repository):
    repository.sum_ledger_rows_by_request_id.return_value = {"req-1": RequestLedgerTotals(1, "run-9", 0.0204, 4)}
    repository.sum_ledger_rows_by_run_id.return_value = {"run-9": RETRIED_LITELLM_TOTALS}
    doc = make_doc("d1", endpoint="/v1/chat/completions", request_id="req-1", **LITELLM)

    [update] = await build_updates(doc_type, [doc])

    assert repository.sum_ledger_rows_by_request_id.await_args.args[1:] == (["req-1"], PERIOD)
    assert repository.sum_ledger_rows_by_run_id.await_args.args[1:] == (["run-9"], PERIOD)
    assert update.status == "settled"


@pytest.mark.asyncio
async def test_request_grain_with_several_run_ids_is_ambiguous(doc_type, repository):
    repository.sum_ledger_rows_by_request_id.return_value = {"req-1": RequestLedgerTotals(2, "run-1", 0.0304, 5)}
    doc = make_doc("d1", endpoint="/v1/chat/completions", request_id="req-1", **LITELLM)

    [update] = await build_updates(doc_type, [doc])

    assert (update.status, update.reason) == ("ambiguous", "several_run_ids")
    assert update.attributes["ledger_settlement"] == "ambiguous"
    assert update.attributes["ledger_settlement_version"] == 1
    assert type(update.attributes["ledger_spend_usd"]) is float
    assert update.attributes["ledger_spend_usd"] == pytest.approx(0.0304)
    assert type(update.attributes["ledger_rows"]) is int
    assert update.attributes["ledger_rows"] == 5
    assert "original_cost_usd" not in update.attributes
    repository.sum_ledger_rows_by_run_id.assert_not_called()


REQUEST_KEYS = {"endpoint": "/v1/chat/completions", "request_id": "req-1"}
PENDING_PATHS = {
    "request_grain_without_rows": (REQUEST_KEYS, {}, {}, "no_rows"),
    "request_grain_rows_without_run_id": (
        REQUEST_KEYS,
        {"req-1": RequestLedgerTotals(0, None, 0.0003, 2)},
        {},
        "no_run_id_on_rows",
    ),
    "resolved_run_without_groups": (REQUEST_KEYS, {"req-1": RequestLedgerTotals(1, "run-9", 0.0204, 4)}, {}, "no_rows"),
    "run_grain_without_routed_success": (
        {"llm_run_id": "run-1"},
        {},
        {"run-1": [RunLedgerTotals("gpt-4.1-mini", "success", 0.0004, 1, 300)]},
        "no_routed_success",
    ),
}


def pending_marker(reason: str) -> dict:
    return {"ledger_settlement": "pending", "ledger_pending_reason": reason, "ledger_settled_at": NOW.isoformat()}


def set_ledger(repository, request_totals, run_totals) -> None:
    repository.sum_ledger_rows_by_request_id.return_value = request_totals
    repository.sum_ledger_rows_by_run_id.return_value = run_totals


@pytest.mark.asyncio
@pytest.mark.parametrize(("keys", "request_totals", "run_totals", "reason"), PENDING_PATHS.values(), ids=PENDING_PATHS)
async def test_pending_document_is_marked_without_a_version_or_money(
    doc_type, repository, keys, request_totals, run_totals, reason
):
    set_ledger(repository, request_totals, run_totals)

    [update] = await build_updates(doc_type, [make_doc("d1", **keys, **LITELLM)])

    assert (update.status, update.reason) == ("pending", reason)
    assert update.attributes == pending_marker(reason)
    assert not set(update.attributes) & (MONEY | COUNTS)
    assert set(update.attributes) <= set(LEDGER_ATTRIBUTES_MAPPING)


@pytest.mark.asyncio
@pytest.mark.parametrize(("keys", "request_totals", "run_totals", "reason"), PENDING_PATHS.values(), ids=PENDING_PATHS)
async def test_pending_document_with_the_same_reason_is_not_rewritten(
    doc_type, repository, keys, request_totals, run_totals, reason
):
    set_ledger(repository, request_totals, run_totals)
    marked = make_doc("d1", **keys, **LITELLM, ledger_settlement="pending", ledger_pending_reason=reason)

    [update] = await build_updates(doc_type, [marked])

    assert (update.status, update.reason, update.attributes) == ("pending", reason, None)


@pytest.mark.asyncio
async def test_pending_document_whose_reason_changes_is_rewritten(doc_type, repository):
    set_ledger(repository, {"req-1": RequestLedgerTotals(0, None, 0.0003, 2)}, {})
    marked = make_doc("d1", **REQUEST_KEYS, **LITELLM, ledger_settlement="pending", ledger_pending_reason="no_rows")

    [update] = await build_updates(doc_type, [marked])

    assert update.attributes == pending_marker("no_run_id_on_rows")


@pytest.mark.asyncio
async def test_pending_document_later_settles_and_clears_its_reason(doc_type, repository):
    set_ledger(repository, {}, {"run-1": RETRIED_LITELLM_TOTALS})
    marked = make_doc("d1", llm_run_id="run-1", **LITELLM, ledger_settlement="pending", ledger_pending_reason="no_rows")

    [update] = await build_updates(doc_type, [marked])

    assert update.status == "settled"
    assert update.attributes["ledger_settlement"] == "settled"
    assert update.attributes["ledger_settlement_version"] == 1
    assert update.attributes["ledger_pending_reason"] is None
    assert update.attributes["original_cost_usd"] == pytest.approx(0.0204)
    assert update.attributes["original_cost_usd_estimated"] == pytest.approx(0.0102)


@pytest.mark.asyncio
async def test_document_without_either_grain_is_no_basis(doc_type, repository, ledger_session):
    [update] = await build_updates(doc_type, [make_doc("d1", request_id="turn-1", **LITELLM)])

    assert update.status == "no_basis"
    assert update.attributes["ledger_settlement"] == "no_basis"
    assert update.attributes["ledger_settlement_version"] == 1
    assert "ledger_spend_usd" not in update.attributes
    assert "ledger_rows" not in update.attributes
    ledger_session.assert_not_called()


@pytest.mark.asyncio
async def test_ambiguous_groups_record_ledger_spend_and_rows(doc_type, repository):
    repository.sum_ledger_rows_by_run_id.return_value = {
        "run-1": [*RETRIED_LITELLM_TOTALS, RunLedgerTotals("other-model", "success", 0.5, 1, 10)]
    }

    [update] = await build_updates(doc_type, [make_doc("d1", llm_run_id="run-1", **LITELLM)])

    assert update.status == "ambiguous"
    assert update.attributes["ledger_spend_usd"] == pytest.approx(0.0204)
    assert update.attributes["ledger_rows"] == 5
    assert "original_cost_usd" not in update.attributes


@pytest.mark.asyncio
async def test_updates_follow_doc_order(doc_type, repository):
    repository.sum_ledger_rows_by_request_id.return_value = {"req-2": RequestLedgerTotals(1, "run-2", 0.0204, 4)}
    repository.sum_ledger_rows_by_run_id.return_value = {"run-1": RETRIED_LITELLM_TOTALS}
    docs = [
        make_doc("d1", llm_run_id="run-1", **LITELLM),
        make_doc("d2", endpoint="/v1/chat/completions", request_id="req-2", **LITELLM),
        make_doc("d3", **LITELLM),
    ]

    updates = await build_updates(doc_type, docs)

    assert [(update.status, update.reason) for update in updates] == [
        ("settled", None),
        ("pending", "no_rows"),
        ("no_basis", updates[2].reason),
    ]
    assert sorted(repository.sum_ledger_rows_by_run_id.await_args.args[1]) == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_switchyard_settles_without_classifier_fields(doc_type, repository):
    repository.sum_ledger_rows_by_run_id.return_value = {
        "run-1": [RunLedgerTotals("gpt-4.1", "success", 0.01, 1, 1000)]
    }

    [update] = await build_updates(doc_type, [make_doc("d1", llm_run_id="run-1", **SWITCHYARD)])

    assert update.status == "settled"
    assert "classifier_cost_usd" not in update.attributes
    assert "classifier_cost_usd_estimated" not in update.attributes
    assert update.attributes["original_cost_usd"] == pytest.approx(0.0102)


@pytest.mark.asyncio
async def test_estimated_copies_read_and_not_rewritten(doc_type, repository):
    repository.sum_ledger_rows_by_run_id.return_value = {"run-1": RETRIED_LITELLM_TOTALS}
    resettled = {
        **LITELLM,
        "original_cost_usd": 0.0204,
        "estimated_max_cost_usd": 0.1,
        "potential_savings_usd": 0.0796,
        "classifier_cost_usd": 0.0004,
        "original_cost_usd_estimated": 0.0102,
        "estimated_max_cost_usd_estimated": 0.05,
        "potential_savings_usd_estimated": 0.0398,
        "classifier_cost_usd_estimated": 0.0002,
    }

    [update] = await build_updates(doc_type, [make_doc("d1", llm_run_id="run-1", **resettled)])

    assert update.status == "settled"
    assert update.attributes["estimated_max_cost_usd"] == pytest.approx(0.1)
    assert not [name for name in update.attributes if name.endswith("_estimated")]
    assert update.log_sums["original_cost_before"] == pytest.approx(0.0102)
    assert update.log_sums["savings_before"] == pytest.approx(0.0398)


@pytest.mark.asyncio
async def test_every_written_field_is_in_the_shared_mapping(doc_type, repository):
    repository.sum_ledger_rows_by_run_id.return_value = {"run-1": RETRIED_LITELLM_TOTALS}

    [update] = await build_updates(doc_type, [make_doc("d1", llm_run_id="run-1", **LITELLM)])

    assert set(update.attributes) <= set(LEDGER_ATTRIBUTES_MAPPING)
    assert set(update.attributes) == set(LEDGER_ATTRIBUTES_MAPPING)


@pytest.mark.asyncio
async def test_serialized_update_has_json_floats_for_money_and_ints_for_counts(doc_type, repository):
    zero_classifier = {**LITELLM, "classifier_cost_usd": 0, "original_cost_usd": 0, "potential_savings_usd": 0}
    repository.sum_ledger_rows_by_run_id.return_value = {
        "run-1": [
            RunLedgerTotals("gpt-auto-llm", "success", 0.01, 1, 1000),
            RunLedgerTotals("gpt-4.1-mini", "success", 0.0, 1, 0),
        ]
    }

    [update] = await build_updates(doc_type, [make_doc("d1", llm_run_id="run-1", **zero_classifier)])

    attributes = json.loads(JSONSerializer().dumps({"doc": {"attributes": update.attributes}}))["doc"]["attributes"]
    for name in MONEY:
        assert type(attributes[name]) is float, name
    for name in COUNTS:
        assert type(attributes[name]) is int, name
    assert attributes["classifier_cost_usd"] == 0.0
    assert attributes["original_cost_usd_estimated"] == 0.0
