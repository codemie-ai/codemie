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

"""An `attributes`-filtered table prices only the filtered runs, per request, from the spend ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.run_attributes import PAIRS_FIELD
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.assistant_handler import AssistantHandler
from codemie.service.analytics.handlers.money_coverage import SETTLED_AGG, UNSETTLED_AGG
from codemie.service.analytics.handlers.request_spend import FIRST_SEEN_AGG, MONEY_SUM_AGG, collect_request_usage
from codemie.service.analytics.handlers.workflow_handler import PRE_FLOOR_MONEY_KEY, WorkflowHandler
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryFilters
from codemie.service.spend.coverage import LedgerCoverage
from codemie.service.spend.ledger import RequestCost
from codemie.service.spend.rendering import render_spend

COSTS_PORT = "codemie.service.analytics.handlers.request_spend.spend_service.costs_for_requests"
REGISTERED_COVERAGE = "codemie.service.analytics.handlers.request_spend.ledger_coverage"
WORKFLOWS_DIMENSION_PORT = "codemie.service.analytics.handlers.workflow_handler.spend_service.aggregate_by_dimension"
ASSISTANTS_DIMENSION_PORT = "codemie.service.analytics.handlers.assistant_handler.spend_service.aggregate_by_dimension"

ATTRIBUTES = {"team": "core"}
START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = datetime(2026, 8, 31, tzinfo=timezone.utc)
FLOOR = datetime(2026, 7, 1, tzinfo=timezone.utc)
SEEN = datetime(2026, 8, 10, tzinfo=timezone.utc)


@pytest.fixture
def user():
    user = MagicMock(spec=User)
    user.id = "user-1"
    user.project_names = ["proj-a"]
    user.admin_project_names = []
    user.is_admin = False
    user.is_auditor = False
    user.is_global_user = False
    return user


@pytest.fixture
def repository():
    return MagicMock(spec=MetricsElasticRepository)


def _request(group: str, request_id: str) -> dict:
    return {
        "key": {"group": group, "id_0": request_id},
        "doc_count": 1,
        FIRST_SEEN_AGG: {"value": SEEN.timestamp() * 1000},
        MONEY_SUM_AGG: {"value": None},
        SETTLED_AGG: {"value": 0},
        UNSETTLED_AGG: {"doc_count": 1},
    }


def _requests(*buckets: dict) -> dict:
    return {"aggregations": {"requests": {"buckets": list(buckets)}}}


def _ledger(costs: dict[str, RequestCost]):
    return (
        patch(REGISTERED_COVERAGE, return_value=LedgerCoverage(coverage_floor=FLOOR, ready=True)),
        patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", True),
        patch(COSTS_PORT, new=AsyncMock(return_value=costs)),
    )


def _pair_term_in(query: dict) -> bool:
    return {"term": {PAIRS_FIELD: "team=core"}} in query["bool"]["filter"]


class TestFilteredWorkflowsArePricedPerExecution:
    @pytest.mark.asyncio
    async def test_each_workflow_sums_the_ledger_cost_of_its_filtered_executions(self, user, repository):
        repository.execute_aggregation_query = AsyncMock(
            return_value=_requests(_request("Release", "exec-1"), _request("Release", "exec-2"))
        )
        rows = [
            {"workflow_name": "Release", "total_runs": 2, PRE_FLOOR_MONEY_KEY: render_spend(None)},
            {"workflow_name": "Triage", "total_runs": 1, PRE_FLOOR_MONEY_KEY: render_spend(None)},
        ]
        filters = AnalyticsQueryFilters(
            metric_filters=[MetricName.WORKFLOW_EXECUTION_TOTAL.value], attributes=ATTRIBUTES
        )
        costs = {"exec-1": RequestCost.settled(Decimal("1")), "exec-2": RequestCost.settled(Decimal("0.5"))}
        coverage, legacy, port = _ledger(costs)

        with coverage, legacy, port, patch(WORKFLOWS_DIMENSION_PORT, new=AsyncMock()) as dimension_port:
            enriched = await WorkflowHandler(user, repository)._enrich_workflows_spending(
                rows, START, END, filters=filters
            )

        dimension_port.assert_not_awaited()
        body = repository.execute_aggregation_query.call_args.args[0]
        assert _pair_term_in(body["query"])
        assert body["aggs"]["requests"]["composite"]["sources"][0] == {
            "group": {"terms": {"field": "attributes.workflow_name.keyword"}}
        }
        by_name = {row["workflow_name"]: row for row in enriched.rows}
        assert by_name["Release"]["total_cost_usd"] == 1.5
        assert by_name["Release"]["avg_cost_usd"] == 0.75
        assert by_name["Triage"]["total_cost_usd"] is None
        assert by_name["Triage"]["spend_unavailable"] is True
        assert all(PRE_FLOOR_MONEY_KEY not in row for row in enriched.rows)
        assert enriched.totals["total_cost_usd"] == 1.5


class TestFilteredAssistantsArePricedPerRequest:
    @pytest.mark.asyncio
    async def test_each_assistant_sums_the_ledger_cost_of_its_filtered_requests(self, user, repository):
        repository.execute_aggregation_query = AsyncMock(
            return_value=_requests(_request("asst-1", "req-1"), _request("asst-1", "req-2"))
        )
        rows = [
            {"assistant_id": "asst-1", "assistant": "One", "success_ops": 2},
            {"assistant_id": "asst-2", "assistant": "Two", "success_ops": 1},
        ]
        filters = AnalyticsQueryFilters(
            metric_filters=[MetricName.CONVERSATION_ASSISTANT_USAGE.value], attributes=ATTRIBUTES
        )
        costs = {"req-1": RequestCost.settled(Decimal("3")), "req-2": RequestCost.settled(Decimal("1"))}
        coverage, legacy, port = _ledger(costs)

        with coverage, legacy, port, patch(ASSISTANTS_DIMENSION_PORT, new=AsyncMock()) as dimension_port:
            enriched = await AssistantHandler(user, repository)._enrich_assistants_spending(
                rows, START, END, filters=filters
            )

        dimension_port.assert_not_awaited()
        body = repository.execute_aggregation_query.call_args.args[0]
        assert _pair_term_in(body["query"])
        assert body["aggs"]["requests"]["composite"]["sources"] == [
            {"group": {"terms": {"field": "attributes.assistant_id.keyword"}}},
            {"id_0": {"terms": {"field": "attributes.request_id.keyword", "missing_bucket": True}}},
        ]
        by_name = {row["assistant"]: row for row in enriched.rows}
        assert by_name["One"]["total_cost_usd"] == 4.0
        assert by_name["One"]["cost_per_op_usd"] == 2.0
        assert by_name["Two"]["total_cost_usd"] is None
        assert "assistant_id" not in by_name["One"]
        assert enriched.totals["total_cost_usd"] == 4.0


def _composite_page(*buckets: dict, after_key: dict | None) -> dict:
    page: dict = {"buckets": list(buckets)}
    if after_key is not None:
        page["after_key"] = after_key
    return {"aggregations": {"requests": page}}


class TestCollectRequestUsagePaging:
    @pytest.mark.asyncio
    async def test_pages_until_the_composite_is_exhausted(self):
        search = AsyncMock(
            side_effect=[
                _composite_page(_request("g", "r-1"), after_key={"id_0": "r-1"}),
                _composite_page(_request("g", "r-2"), after_key=None),
            ]
        )

        usage = await collect_request_usage(search, {}, group_field="g", id_fields=("id",), max_pages=2)

        assert search.await_count == 2
        assert [item.request_id for item in usage["g"]] == ["r-1", "r-2"]

    @pytest.mark.asyncio
    async def test_a_range_beyond_the_page_cap_is_refused_with_422(self):
        search = AsyncMock(side_effect=lambda _body: _composite_page(_request("g", "r-1"), after_key={"id_0": "r-1"}))

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await collect_request_usage(search, {}, group_field="g", id_fields=("id",), max_pages=3)

        assert exc_info.value.code == 422
        assert search.await_count == 3


def _esql_result(assistant_id: str) -> dict:
    values = {
        "attributes.assistant_id.keyword": assistant_id,
        "assistant_name": "One",
        "total_conversations": 1,
        "total_messages": 2,
        "unique_users": 1,
        "min_messages_per_chat": 2,
        "median_messages_rounded": 2.0,
        "avg_messages_rounded": 2.0,
        "max_messages_per_chat": 2,
        "successful_operations": 2,
        "total_errors": 0,
        "error_rate_percent": 0.0,
        "total_tokens": 10,
        "avg_execution_rounded": 1.0,
        "last_error_time": None,
    }
    return {"columns": [{"name": name} for name in values], "values": [list(values.values())]}


def _workflow_bucket(name: str) -> dict:
    return {
        "key": name,
        "unique_executions": {"value": 2},
        "successful": {"unique_count": {"value": 2}},
        "failed": {"unique_count": {"value": 0}},
        "aborted": {"unique_count": {"value": 0}},
        "total_tokens": {"value": 10},
        "avg_time": {"value": 1.0},
        "unique_users": {"value": 1},
        "last_run": {"value_as_string": "2026-08-10T00:00:00Z"},
    }


class TestFilteredTablesThroughThePublicMethods:
    @pytest.mark.asyncio
    async def test_get_assistants_chats_prices_only_the_filtered_requests(self, user, repository):
        repository.execute_esql_query = AsyncMock(return_value=_esql_result("asst-1"))
        repository.execute_aggregation_query = AsyncMock(
            return_value=_requests(_request("asst-1", "req-1"), _request("asst-1", "req-2"))
        )
        costs = {"req-1": RequestCost.settled(Decimal("3")), "req-2": RequestCost.settled(Decimal("1"))}
        coverage, legacy, port = _ledger(costs)
        handler = AssistantHandler(user, repository)

        with (
            coverage,
            legacy,
            port,
            patch(ASSISTANTS_DIMENSION_PORT, new=AsyncMock()) as dimension_port,
            patch.object(handler, "_resolve_assistant_names", return_value=({"asst-1": "One"}, False)),
        ):
            response = await handler.get_assistants_chats(
                start_date=START, end_date=END, time_period=None, attributes=ATTRIBUTES
            )

        dimension_port.assert_not_awaited()
        assert _pair_term_in(repository.execute_aggregation_query.call_args.args[0]["query"])
        assert response["data"]["rows"][0]["total_cost_usd"] == 4.0
        assert response["data"]["totals"]["total_cost_usd"] == 4.0

    @pytest.mark.asyncio
    async def test_get_workflows_prices_only_the_filtered_executions(self, user, repository):
        async def search(body):
            if "requests" in body["aggs"]:
                return _requests(_request("Release", "exec-1"), _request("Release", "exec-2"))
            return {"aggregations": {"paginated_results": {"buckets": [_workflow_bucket("Release")]}}}

        repository.execute_aggregation_query = AsyncMock(side_effect=search)
        costs = {"exec-1": RequestCost.settled(Decimal("1")), "exec-2": RequestCost.settled(Decimal("0.5"))}
        coverage, legacy, port = _ledger(costs)

        with coverage, legacy, port, patch(WORKFLOWS_DIMENSION_PORT, new=AsyncMock()) as dimension_port:
            response = await WorkflowHandler(user, repository).get_workflows(
                start_date=START, end_date=END, time_period=None, attributes=ATTRIBUTES
            )

        dimension_port.assert_not_awaited()
        row = response["data"]["rows"][0]
        assert row["workflow_name"] == "Release"
        assert row["total_cost_usd"] == 1.5
        assert row["avg_cost_usd"] == 0.75
        assert response["data"]["totals"]["total_cost_usd"] == 1.5
