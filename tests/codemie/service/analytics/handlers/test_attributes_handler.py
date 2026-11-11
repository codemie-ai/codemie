# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Unit tests for AttributesHandler (attributes-usage analytics)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs import config
from codemie.core.run_attributes import KEYS_FIELD, PAIRS_FIELD
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.attributes_handler import AttributesHandler, escape_lucene_regex
from codemie.service.analytics.handlers.money_coverage import SETTLED_AGG, UNSETTLED_AGG
from codemie.service.analytics.handlers.request_spend import FIRST_SEEN_AGG, MONEY_SUM_AGG
from codemie.service.analytics.metric_names import MetricName
from codemie.service.spend.coverage import LedgerCoverage
from codemie.service.spend.ledger import RequestCost

WORK_ITEM_KEY = "com.epam.gen_ai.service.work_item.id"
COSTS_PORT = "codemie.service.analytics.handlers.request_spend.spend_service.costs_for_requests"
REGISTERED_COVERAGE = "codemie.service.analytics.handlers.request_spend.ledger_coverage"
FLOOR = datetime(2026, 8, 15, tzinfo=timezone.utc)
SEEN = datetime(2026, 8, 20, tzinfo=timezone.utc)


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.project_names = ["proj-a"]
    user.admin_project_names = []
    user.is_admin = False
    user.is_auditor = False
    user.id = "user-1"
    return user


@pytest.fixture
def mock_repository():
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    return AttributesHandler(mock_user, mock_repository)


def _bucket(key: str, doc_count: int = 1, **aggs) -> dict:
    return {
        "key": key,
        "doc_count": doc_count,
        "conversations": {"unique": {"value": aggs.get("conversations", 0)}},
        "workflow_executions": {"unique": {"value": aggs.get("workflow_executions", 0)}},
        "input_tokens": {"value": aggs.get("input_tokens", 0)},
        "output_tokens": {"value": aggs.get("output_tokens", 0)},
        "unique_users": {"value": aggs.get("unique_users", 0)},
        "last_used": {"value_as_string": aggs.get("last_used")},
    }


def _result(*buckets: dict) -> dict:
    return {"aggregations": {"paginated_results": {"buckets": list(buckets)}}}


def _request(pair: str, execution_id: str | None, request_id: str | None, money: float | None = None) -> dict:
    settled = 0 if money is None else 1
    return {
        "key": {"group": pair, "id_0": execution_id, "id_1": request_id},
        "doc_count": 1,
        FIRST_SEEN_AGG: {"value": SEEN.timestamp() * 1000},
        MONEY_SUM_AGG: {"value": money},
        SETTLED_AGG: {"value": settled},
        UNSETTLED_AGG: {"doc_count": 1 - settled},
    }


def _route(usage: dict, *requests: dict):
    async def search(body: dict) -> dict:
        if "requests" in body["aggs"]:
            return {"aggregations": {"requests": {"buckets": list(requests)}}}
        return usage

    return search


def _bodies(mock_repository) -> list[dict]:
    return [call.args[0] for call in mock_repository.execute_aggregation_query.call_args_list]


def _es_body(mock_repository) -> dict:
    return next(body for body in _bodies(mock_repository) if "paginated_results" in body["aggs"])


def _listing(mock_repository) -> dict:
    return next(body for body in _bodies(mock_repository) if "requests" in body["aggs"])


class TestEscapeLuceneRegex:
    def test_escaped_key_only_matches_itself(self):
        pattern = re.compile(f"{escape_lucene_regex('team.lead-x')}=.*")

        assert escape_lucene_regex("team.lead-x") == "team\\.lead\\-x"
        assert pattern.fullmatch("team.lead-x=alice")
        assert not pattern.fullmatch("teamXlead-x=alice")


class TestGetAttributesUsage:
    @pytest.mark.asyncio
    async def test_aggregation_is_scoped_to_the_key(self, handler, mock_repository):
        mock_repository.execute_aggregation_query.side_effect = _route(_result())

        await handler.get_attributes_usage(WORK_ITEM_KEY, time_period="last_30_days")

        body = _es_body(mock_repository)
        terms = body["aggs"]["paginated_results"]["terms"]
        assert terms["field"] == PAIRS_FIELD
        assert terms["include"] == f"{escape_lucene_regex(WORK_ITEM_KEY)}=.*"
        assert {"term": {KEYS_FIELD: WORK_ITEM_KEY}} in body["query"]["bool"]["filter"]

    @pytest.mark.asyncio
    async def test_counts_only_chat_and_workflow_usage_records(self, handler, mock_repository):
        mock_repository.execute_aggregation_query.side_effect = _route(_result())

        await handler.get_attributes_usage(WORK_ITEM_KEY, time_period="last_30_days")

        assert {
            "terms": {
                "metric_name.keyword": [
                    MetricName.CONVERSATION_ASSISTANT_USAGE.value,
                    MetricName.WORKFLOW_EXECUTION_TOTAL.value,
                ]
            }
        } in _es_body(mock_repository)["query"]["bool"]["filter"][0]["bool"]["filter"]

    @pytest.mark.asyncio
    async def test_attributes_filter_narrows_the_query(self, handler, mock_repository):
        mock_repository.execute_aggregation_query.side_effect = _route(_result())

        await handler.get_attributes_usage(WORK_ITEM_KEY, time_period="last_30_days", attributes={"team": "core"})

        assert {"term": {PAIRS_FIELD: "team=core"}} in _es_body(mock_repository)["query"]["bool"]["filter"][0]["bool"][
            "filter"
        ]

    @pytest.mark.asyncio
    async def test_rows_strip_the_key_and_skip_other_keys(self, handler, mock_repository):
        mock_repository.execute_aggregation_query.side_effect = _route(
            _result(
                _bucket(
                    "team=core=blue",
                    doc_count=3,
                    conversations=2,
                    workflow_executions=1,
                    input_tokens=100,
                    output_tokens=50,
                    unique_users=2,
                    last_used="2026-11-01T10:00:00.000Z",
                ),
                _bucket("team.lead=alice", doc_count=1),
            ),
            _request("team=core=blue", "exec-1", None, money=0.123456),
        )

        result = await handler.get_attributes_usage("team", time_period="last_30_days")

        assert result["data"]["rows"] == [
            {
                "value": "core=blue",
                "conversations": 2,
                "workflow_executions": 1,
                "total_tokens": 150,
                "total_cost": 0.12,
                "unique_users": 2,
                "last_used": "2026-11-01T10:00:00.000Z",
                "spend_unavailable": False,
                "spend_partial": False,
                "spend_missing_amount": None,
            }
        ]


class TestAttributeValueCostComesFromTheLedger:
    @pytest.mark.asyncio
    async def test_the_usage_aggregation_carries_no_money(self, handler, mock_repository):
        mock_repository.execute_aggregation_query.side_effect = _route(_result(_bucket("team=core")))

        await handler.get_attributes_usage("team", time_period="last_30_days")

        assert "total_cost" not in _es_body(mock_repository)["aggs"]["paginated_results"]["aggs"]

    @pytest.mark.asyncio
    async def test_each_value_sums_the_ledger_cost_of_its_executions_and_requests(self, handler, mock_repository):
        mock_repository.execute_aggregation_query.side_effect = _route(
            _result(_bucket("team=core"), _bucket("team=edge")),
            _request("team=core", "exec-1", None),
            _request("team=core", None, "req-1"),
            _request("owner=bob", "exec-1", None),
        )
        costs = {"exec-1": RequestCost.settled(Decimal("2")), "req-1": RequestCost.settled(Decimal("1"))}

        with (
            patch(REGISTERED_COVERAGE, return_value=LedgerCoverage(coverage_floor=FLOOR, ready=True)),
            patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", True),
            patch(COSTS_PORT, new=AsyncMock(return_value=costs)) as port,
        ):
            result = await handler.get_attributes_usage("team", time_period="last_30_days")

        port.assert_awaited_once_with(["exec-1", "req-1"])
        listing = _listing(mock_repository)
        assert {"term": {KEYS_FIELD: "team"}} in listing["query"]["bool"]["filter"]
        assert listing["aggs"]["requests"]["composite"]["sources"] == [
            {"group": {"terms": {"field": PAIRS_FIELD}}},
            {"id_0": {"terms": {"field": "attributes.execution_id.keyword", "missing_bucket": True}}},
            {"id_1": {"terms": {"field": "attributes.request_id.keyword", "missing_bucket": True}}},
        ]
        by_value = {row["value"]: row for row in result["data"]["rows"]}
        assert by_value["core"]["total_cost"] == 3.0
        assert by_value["edge"]["total_cost"] is None
        assert by_value["edge"]["spend_unavailable"] is True
        assert result["data"]["totals"]["total_cost"] == 3.0
        assert result["data"]["totals"]["total_cost_spend_unavailable"] is True
