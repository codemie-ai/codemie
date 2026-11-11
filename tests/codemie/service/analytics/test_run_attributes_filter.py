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

"""The `attributes` analytics filter reaches every Elasticsearch query behind the filtered endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.core.run_attributes import PAIRS_FIELD
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.assistant_handler import AssistantHandler
from codemie.service.analytics.handlers.workflow_handler import WorkflowHandler
from codemie.service.analytics.query_builder import SecureQueryBuilder
from codemie.service.analytics.query_pipeline import AnalyticsQueryFilters, AnalyticsQueryPipeline

ATTRIBUTES = {"team": "core", "com.epam.gen_ai.service.work_item.id": "ABC-123"}
PAIR_TERMS = [
    {"term": {PAIRS_FIELD: "team=core"}},
    {"term": {PAIRS_FIELD: "com.epam.gen_ai.service.work_item.id=ABC-123"}},
]


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


def _filters(query: dict) -> list[dict]:
    return query["bool"]["filter"]


class TestSecureQueryBuilder:
    def test_adds_one_term_per_pair(self, user):
        query = SecureQueryBuilder(user).add_attributes_filter(ATTRIBUTES).build()

        assert all(term in _filters(query) for term in PAIR_TERMS)

    @pytest.mark.parametrize("attributes", [None, {}])
    def test_adds_nothing_without_attributes(self, user, attributes):
        query = SecureQueryBuilder(user).add_attributes_filter(attributes).build()

        assert PAIRS_FIELD not in str(query)


def test_all_time_query_ignores_the_attributes_filter(user, repository):
    filters = AnalyticsQueryFilters(metric_filters=["conversation_assistant_usage"], attributes=ATTRIBUTES)

    query = AnalyticsQueryPipeline(user, repository)._build_query_without_time_filter(filters)

    assert PAIRS_FIELD not in str(query)


@pytest.mark.asyncio
async def test_assistants_chats_esql_filter_carries_the_pairs(user, repository):
    repository.execute_esql_query.return_value = {"columns": [], "values": []}

    await AssistantHandler(user, repository).get_assistants_chats(time_period="last_30_days", attributes=ATTRIBUTES)

    filter_query = repository.execute_esql_query.call_args.kwargs["filter_query"]
    assert all(term in _filters(filter_query) for term in PAIR_TERMS)


@pytest.mark.asyncio
async def test_workflows_aggregation_carries_the_pairs(user, repository):
    repository.execute_aggregation_query.return_value = {"aggregations": {"paginated_results": {"buckets": []}}}

    await WorkflowHandler(user, repository).get_workflows(time_period="last_30_days", attributes=ATTRIBUTES)

    query = repository.execute_aggregation_query.call_args.args[0]["query"]
    assert all(term in _filters(query) for term in PAIR_TERMS)


@pytest.mark.asyncio
async def test_workflows_executions_aggregation_carries_the_pairs(user, repository):
    snapshot = repository.aggregation_snapshot.return_value.__aenter__.return_value
    snapshot.search = AsyncMock(return_value={"aggregations": {"paginated_results": {"buckets": []}}})

    await WorkflowHandler(user, repository).get_workflows_executions(time_period="last_30_days", attributes=ATTRIBUTES)

    query = snapshot.search.call_args.args[0]["query"]
    assert all(term in _filters(query) for term in PAIR_TERMS)
