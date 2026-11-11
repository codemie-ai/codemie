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

"""Unit tests for WorkflowHandler per-execution analytics (workflows-executions)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs import config
from codemie.core.run_attributes import PAIRS_FIELD
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.workflow_handler import EXECUTION_ID_KEYWORD_FIELD, WorkflowHandler
from codemie.service.analytics.handlers.money_coverage import SETTLED_AGG, UNSETTLED_AGG
from codemie.service.analytics.handlers.request_spend import FIRST_SEEN_AGG, MONEY_SUM_AGG
from codemie.service.analytics.metric_names import MetricName
from codemie.service.spend.coverage import LedgerCoverage
from codemie.service.spend.ledger import RequestCost

COSTS_PORT = "codemie.service.analytics.handlers.request_spend.spend_service.costs_for_requests"
REGISTERED_COVERAGE = "codemie.service.analytics.handlers.request_spend.ledger_coverage"
FLOOR = datetime(2026, 8, 15, tzinfo=timezone.utc)
AFTER_FLOOR = datetime(2026, 8, 20, tzinfo=timezone.utc)
SPEND_KEYS = ["spend_unavailable", "spend_partial", "spend_missing_amount"]


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
    repository = MagicMock(spec=MetricsElasticRepository)
    snapshot = repository.aggregation_snapshot.return_value.__aenter__.return_value
    snapshot.search = AsyncMock()
    return repository


@pytest.fixture
def handler(mock_user, mock_repository):
    return WorkflowHandler(mock_user, mock_repository)


def _bucket(execution_id: str, latest: dict | None, **aggs) -> dict:
    hits = [{"_source": {"attributes": latest}}] if latest is not None else []
    return {
        "key": execution_id,
        "doc_count": aggs.get("doc_count", 1),
        "input_tokens": {"value": aggs.get("input_tokens", 0)},
        "output_tokens": {"value": aggs.get("output_tokens", 0)},
        "execution_time": {"value": aggs.get("execution_time")},
        "last_activity": {"value_as_string": aggs.get("last_activity")},
        "latest": {"hits": {"hits": hits}},
    }


def _result(*buckets: dict) -> dict:
    return {"aggregations": {"paginated_results": {"buckets": list(buckets)}, "total_buckets": {"value": len(buckets)}}}


def _request(execution_id: str | None, money: float | None = None, first_seen: datetime = AFTER_FLOOR) -> dict:
    settled = 0 if money is None else 1
    return {
        "key": {"id_0": execution_id},
        "doc_count": 1,
        FIRST_SEEN_AGG: {"value": first_seen.timestamp() * 1000},
        MONEY_SUM_AGG: {"value": money},
        SETTLED_AGG: {"value": settled},
        UNSETTLED_AGG: {"doc_count": 1 - settled},
    }


def _requests(*buckets: dict) -> dict:
    return {"aggregations": {"requests": {"buckets": list(buckets)}}}


def _route(selection: dict, details: dict | None = None, requests: dict | None = None):
    async def search(body: dict) -> dict:
        if "requests" in body["aggs"]:
            return requests if requests is not None else _requests()
        if body["aggs"]["paginated_results"]["terms"]["order"] == {"_key": "asc"}:
            return details if details is not None else selection
        return selection

    return search


def _es_body(mock_repository) -> dict:
    return _searches(mock_repository)[0]


def _searches(mock_repository) -> list[dict]:
    return [call.args[0] for call in _snapshot(mock_repository).search.call_args_list]


def _snapshot(repository):
    return repository.aggregation_snapshot.return_value.__aenter__.return_value


def _ledger(costs: dict[str, RequestCost]):
    return (
        patch(REGISTERED_COVERAGE, return_value=LedgerCoverage(coverage_floor=FLOOR, ready=True)),
        patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", True),
        patch(COSTS_PORT, new=AsyncMock(return_value=costs)),
    )


class TestGetWorkflowsExecutions:
    @pytest.mark.asyncio
    async def test_groups_workflow_usage_records_by_execution_newest_first(self, handler, mock_repository):
        _snapshot(mock_repository).search.side_effect = _route(_result())

        await handler.get_workflows_executions(time_period="last_30_days")

        body = _es_body(mock_repository)
        terms = body["aggs"]["paginated_results"]["terms"]
        assert terms["field"] == EXECUTION_ID_KEYWORD_FIELD
        assert terms["order"] == {"last_activity": "desc"}
        assert {"terms": {"metric_name.keyword": [MetricName.WORKFLOW_EXECUTION_TOTAL.value]}} in body["query"]["bool"][
            "filter"
        ]

    @pytest.mark.asyncio
    async def test_reads_run_fields_from_the_newest_record(self, handler, mock_repository):
        _snapshot(mock_repository).search.side_effect = _route(_result(_bucket("exec-1", None)))

        await handler.get_workflows_executions(time_period="last_30_days")

        latest = _searches(mock_repository)[1]["aggs"]["paginated_results"]["aggs"]["latest"]["top_hits"]
        assert latest["size"] == 1
        assert latest["sort"] == [{"@timestamp": {"order": "desc"}}]
        assert "attributes.run_attribute_pairs" in latest["_source"]["includes"]
        assert "attributes.status" in latest["_source"]["includes"]

    @pytest.mark.asyncio
    async def test_attributes_filter_narrows_the_query(self, handler, mock_repository):
        _snapshot(mock_repository).search.side_effect = _route(_result())

        await handler.get_workflows_executions(time_period="last_30_days", attributes={"team": "core"})

        assert {"term": {PAIRS_FIELD: "team=core"}} in _es_body(mock_repository)["query"]["bool"]["filter"]

    @pytest.mark.asyncio
    async def test_token_totals_come_from_global_aggregations_in_the_selection_query(self, handler, mock_repository):
        _snapshot(mock_repository).search.side_effect = _route(_result())

        await handler.get_workflows_executions(time_period="last_30_days")

        aggs = _es_body(mock_repository)["aggs"]
        assert "money_spent" not in aggs
        assert aggs["input_tokens"] == {"sum": {"field": "attributes.input_tokens"}}
        assert aggs["output_tokens"] == {"sum": {"field": "attributes.output_tokens"}}

    @pytest.mark.asyncio
    async def test_one_row_per_execution_with_summed_usage_and_latest_fields(self, handler, mock_repository):
        selection = _result(
            _bucket(
                "exec-1",
                {
                    "workflow_name": "Release notes",
                    "status": "SUCCEEDED",
                    "user_id": "user-1",
                    "user_name": "Alice",
                    "user_email": "alice@example.com",
                    "project": "proj-a",
                    "run_attribute_pairs": ["team=core=blue", "work_item=ABC-123"],
                },
                doc_count=3,
                input_tokens=100,
                output_tokens=50,
                execution_time=12.5,
                last_activity="2026-11-01T10:00:00.000Z",
            ),
            _bucket("exec-2", {"workflow_name": "Triage", "status": "IN_PROGRESS"}),
        )
        _snapshot(mock_repository).search.side_effect = _route(
            selection, requests=_requests(_request("exec-1", money=0.123456), _request("exec-2"))
        )

        result = await handler.get_workflows_executions(time_period="last_30_days")

        assert result["data"]["rows"] == [
            {
                "execution_id": "exec-1",
                "last_activity": "2026-11-01T10:00:00.000Z",
                "workflow_name": "Release notes",
                "status": "SUCCEEDED",
                "user_id": "user-1",
                "user_name": "Alice",
                "user_email": "alice@example.com",
                "project": "proj-a",
                "usage_records": 3,
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "money_spent": 0.12,
                "execution_time": 12.5,
                "attributes": {"team": "core=blue", "work_item": "ABC-123"},
                "spend_unavailable": False,
                "spend_partial": False,
                "spend_missing_amount": None,
            },
            {
                "execution_id": "exec-2",
                "last_activity": None,
                "workflow_name": "Triage",
                "status": "IN_PROGRESS",
                "user_id": None,
                "user_name": None,
                "user_email": None,
                "project": None,
                "usage_records": 1,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "money_spent": None,
                "execution_time": None,
                "attributes": None,
                "spend_unavailable": True,
                "spend_partial": False,
                "spend_missing_amount": None,
            },
        ]

    @pytest.mark.asyncio
    async def test_every_row_has_every_column(self, handler, mock_repository):
        _snapshot(mock_repository).search.side_effect = _route(_result(_bucket("exec-1", None)))

        result = await handler.get_workflows_executions(time_period="last_30_days")

        column_ids = [column["id"] for column in result["data"]["columns"]]
        assert list(result["data"]["rows"][0]) == column_ids + SPEND_KEYS
        assert {"id": "attributes", "label": "Attributes", "type": "json"} in result["data"]["columns"]


class TestExecutionCostComesFromTheLedger:
    @pytest.mark.asyncio
    async def test_every_matching_execution_is_listed_under_the_same_query_inside_the_snapshot(
        self, handler, mock_repository
    ):
        _snapshot(mock_repository).search.side_effect = _route(
            _result(_bucket("exec-1", None)),
            requests=_requests(_request("exec-1"), _request("exec-off-page")),
        )

        await handler.get_workflows_executions(time_period="last_30_days")

        selection, _, listing = _searches(mock_repository)
        assert listing["query"] == selection["query"]
        assert listing["aggs"]["requests"]["composite"]["sources"] == [
            {"id_0": {"terms": {"field": EXECUTION_ID_KEYWORD_FIELD, "missing_bucket": True}}}
        ]
        mock_repository.aggregation_snapshot.return_value.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rows_carry_the_ledger_cost_and_totals_sum_every_execution_in_range(self, handler, mock_repository):
        _snapshot(mock_repository).search.side_effect = _route(
            _result(_bucket("exec-1", None)),
            requests=_requests(_request("exec-1", money=9.0), _request("exec-off-page", money=9.0)),
        )
        costs = {"exec-1": RequestCost.settled(Decimal("1.25")), "exec-off-page": RequestCost.settled(Decimal("2"))}
        coverage, legacy, port = _ledger(costs)

        with coverage, legacy, port as costs_port:
            result = await handler.get_workflows_executions(time_period="last_30_days", per_page=1)

        costs_port.assert_awaited_once_with(["exec-1", "exec-off-page"])
        row = result["data"]["rows"][0]
        assert row["money_spent"] == 1.25
        assert row["spend_unavailable"] is False
        assert result["data"]["totals"]["money_spent"] == 3.25
        assert result["data"]["totals"]["money_spent_spend_unavailable"] is False

    @pytest.mark.asyncio
    async def test_a_pending_execution_has_no_cost_and_flags_the_total(self, handler, mock_repository):
        _snapshot(mock_repository).search.side_effect = _route(
            _result(_bucket("exec-1", None)),
            requests=_requests(_request("exec-1"), _request("exec-2")),
        )
        costs = {"exec-1": RequestCost.pending(), "exec-2": RequestCost.settled(Decimal("2"))}
        coverage, legacy, port = _ledger(costs)

        with coverage, legacy, port:
            result = await handler.get_workflows_executions(time_period="last_30_days", per_page=1)

        row = result["data"]["rows"][0]
        assert row["money_spent"] is None
        assert row["spend_unavailable"] is True
        assert result["data"]["totals"]["money_spent"] == 2.0
        assert result["data"]["totals"]["money_spent_spend_unavailable"] is True


class TestExactExecutionAggregation:
    @pytest.mark.asyncio
    async def test_missing_details_fail_closed_and_close_snapshot(self, handler, mock_repository):
        _snapshot(mock_repository).search.side_effect = _route(_result(_bucket("selected", None)), details=_result())
        with pytest.raises(ExtendedHTTPException) as exc:
            await handler.get_workflows_executions(time_period="last_30_days")
        assert exc.value.code == 503
        mock_repository.aggregation_snapshot.return_value.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_selection_is_lightweight_and_details_keep_page_order(self, handler, mock_repository):
        selection = _result(_bucket("newest", None), _bucket("older", None))
        selection["aggregations"]["total_buckets"] = {"value": 50}
        _snapshot(mock_repository).search.side_effect = _route(
            selection,
            details=_result(_bucket("older", None, input_tokens=30), _bucket("newest", None, input_tokens=70)),
            requests=_requests(_request("newest", money=1.0), _request("older", money=2.0)),
        )

        result = await handler.get_workflows_executions(time_period="last_30_days", per_page=2)

        first, second, _ = _searches(mock_repository)
        assert set(first["aggs"]["paginated_results"]["aggs"]) == {"last_activity"}
        assert second["query"] == {
            "bool": {"filter": [first["query"], {"terms": {EXECUTION_ID_KEYWORD_FIELD: ["newest", "older"]}}]}
        }
        assert second["aggs"]["paginated_results"]["terms"]["order"] == {"_key": "asc"}
        assert second["aggs"]["paginated_results"]["terms"]["size"] == 2
        assert "money_spent" not in second["aggs"]["paginated_results"]["aggs"]
        assert [row["execution_id"] for row in result["data"]["rows"]] == ["newest", "older"]
        assert [row["input_tokens"] for row in result["data"]["rows"]] == [70, 30]
        assert [row["money_spent"] for row in result["data"]["rows"]] == [1.0, 2.0]
        assert result["data"]["totals"]["money_spent"] == 3.0
        assert result["pagination"] == {"page": 0, "per_page": 2, "total_count": 50, "has_more": True}
        mock_repository.aggregation_snapshot.return_value.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_only_selected_page_is_reaggregated(self, handler, mock_repository):
        _snapshot(mock_repository).search.side_effect = _route(
            _result(_bucket("first", None), _bucket("second", None)), details=_result(_bucket("second", None))
        )
        result = await handler.get_workflows_executions(time_period="last_30_days", page=1, per_page=1)
        detail_query = _searches(mock_repository)[1]["query"]
        assert detail_query["bool"]["filter"][1] == {"terms": {EXECUTION_ID_KEYWORD_FIELD: ["second"]}}
        assert result["data"]["rows"][0]["execution_id"] == "second"

    @pytest.mark.asyncio
    async def test_empty_page_keeps_totals_without_a_details_search(self, handler, mock_repository):
        selection = _result()
        selection["aggregations"]["input_tokens"] = {"value": 900}
        selection["aggregations"]["total_buckets"] = {"value": 1}
        _snapshot(mock_repository).search.side_effect = _route(selection, requests=_requests(_request("x", money=4.0)))
        result = await handler.get_workflows_executions(time_period="last_30_days", page=2, per_page=1)
        searches = _searches(mock_repository)
        assert len(searches) == 2
        assert "requests" in searches[1]["aggs"]
        assert result["data"]["rows"] == []
        assert result["data"]["totals"]["input_tokens"] == 900
        assert result["data"]["totals"]["money_spent"] == 4.0

    @pytest.mark.asyncio
    async def test_a_range_without_executions_has_an_empty_footer_like_every_other_table(
        self, handler, mock_repository
    ):
        selection = _result()
        selection["aggregations"]["input_tokens"] = {"value": 0}
        selection["aggregations"]["output_tokens"] = {"value": 0}
        _snapshot(mock_repository).search.side_effect = _route(selection)

        result = await handler.get_workflows_executions(time_period="last_30_days")

        assert result["data"]["rows"] == []
        assert result["data"]["totals"] == {}
        assert len(_searches(mock_repository)) == 1
