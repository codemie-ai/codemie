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

"""One user's, or one project's, CLI cost is the same figure on the Analytics activity tables and on CLI Insights.

Both screens are driven through their real handlers, with only the spend port and Elasticsearch faked, so
the two differ in nothing but the code that turns the same ledger answer into a cell.
"""

from __future__ import annotations

import contextlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.cli.insights_handler import CLIInsightsHandler
from codemie.service.analytics.handlers.project_handler import ProjectHandler
from codemie.service.analytics.handlers.user_handler import UserHandler
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import ReportFilter, UserTerm
from codemie.service.spend.spend_group import SpendGroup

_LEDGER_MODULE = "codemie.service.analytics.handlers.ledger_group_spend"
START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = datetime(2026, 8, 31, tzinfo=timezone.utc)

#: The three figures a row can be handed: whole, short in a dimension, and cut at the coverage floor.
FIGURES = {
    "exact": SpendResult(figure=Decimal("12.34")),
    "partial": SpendResult(
        figure=Decimal("1.25"), partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)}
    ),
    "truncated": SpendResult(
        figure=Decimal("7.5"), unavailable=TimeRange(START, datetime(2026, 8, 2, tzinfo=timezone.utc))
    ),
}


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin = False
    user.id = "test-user-id"
    return user


def _repository(answer: dict) -> MagicMock:
    repository = MagicMock(spec=MetricsElasticRepository)
    repository.execute_aggregation_query = AsyncMock(return_value=answer)
    return repository


@asynccontextmanager
async def _fake_session():
    yield MagicMock()


def _ledger_answering(cli: SpendResult) -> contextlib.ExitStack:
    """The spend port answering every one-person and every one-project filter with `cli` for the CLI group."""

    async def _report_filter_for(users, projects):
        terms = tuple(UserTerm(user_id=u, aliases=frozenset({u})) for u in dict.fromkeys(u for u in users or () if u))
        return ReportFilter(users=terms, projects=tuple(projects or ()), personal_project_owners=())

    async def _per_filter(period, *, access, common_filter, report_filters):
        no_figure = {group: SpendResult.no_figure(period) for group in SpendGroup}
        return {report_filter: no_figure | {SpendGroup.CLI: cli} for report_filter in report_filters}

    stack = contextlib.ExitStack()
    stack.enter_context(patch(f"{_LEDGER_MODULE}.report_filter_for", new=AsyncMock(side_effect=_report_filter_for)))
    stack.enter_context(patch(f"{_LEDGER_MODULE}.get_async_session", _fake_session))
    stack.enter_context(
        patch(
            f"{_LEDGER_MODULE}.application_repository.aget_personal_project_owners_by_name",
            new=AsyncMock(return_value={}),
        )
    )
    stack.enter_context(
        patch(
            f"{_LEDGER_MODULE}.spend_service.aggregate_by_spend_group_per_filter",
            new=AsyncMock(side_effect=_per_filter),
        )
    )
    for module in ("user_handler", "cli.insights_handler"):
        stack.enter_context(
            patch(f"codemie.service.analytics.handlers.{module}.UserIdentityResolver.resolve_rows", new=AsyncMock())
        )
    return stack


def _cli_insights_bucket(key: str) -> dict:
    return {
        "key": key,
        "user_name": {"top": [{"metrics": {"attributes.user_name.keyword": "ada@example.com"}}]},
        "user_email": {"top": [{"metrics": {"attributes.user_email.keyword": "ada@example.com"}}]},
        "projects": {"buckets": {"buckets": [{"key": "alpha"}]}},
        "repositories": {"buckets": {"buckets": []}},
        "branches": {"buckets": {"buckets": []}},
    }


def _activity_bucket(key: str) -> dict:
    return {
        "key": key,
        "doc_count": 5,
        "1-bucket": {"1-metric": {"value": 120.0}},
        "assistant_cost_bucket": {"assistant_cost": {"value": 3.0}},
    }


@pytest.mark.parametrize("state", sorted(FIGURES))
class TestOneCliCostOnBothScreens:
    WINDOW = {"start_date": START, "end_date": END}

    @pytest.mark.asyncio
    async def test_a_users_activity_row_shows_the_cli_insights_figure_for_that_user(self, mock_user, state):
        activity = UserHandler(
            mock_user, _repository({"aggregations": {"paginated_results": {"buckets": [_activity_bucket("u-ada")]}}})
        )
        insights = CLIInsightsHandler(
            mock_user, _repository({"aggregations": {"users": {"buckets": [_cli_insights_bucket("u-ada")]}}})
        )

        with _ledger_answering(FIGURES[state]):
            (activity_row,) = (await activity.get_users_activity(projects=["alpha"], **self.WINDOW))["data"]["rows"]
            (insights_row,) = (await insights.get_cli_insights_all_users(projects=["alpha"], **self.WINDOW))["data"][
                "rows"
            ]

        assert activity_row["cli_cost"] == insights_row["total_cost"]

    @pytest.mark.asyncio
    async def test_a_projects_activity_row_shows_the_cli_insights_figure_for_that_project(self, mock_user, state):
        activity = ProjectHandler(
            mock_user, _repository({"aggregations": {"paginated_results": {"buckets": [_activity_bucket("alpha")]}}})
        )
        insights = CLIInsightsHandler(
            mock_user, _repository({"aggregations": {"projects": {"buckets": [_cli_insights_bucket("alpha")]}}})
        )

        with _ledger_answering(FIGURES[state]):
            (activity_row,) = (await activity.get_projects_activity(users=["u-ada"], **self.WINDOW))["data"]["rows"]
            (insights_row,) = (await insights.get_cli_insights_top_projects_by_cost(users=["u-ada"], **self.WINDOW))[
                "data"
            ]["rows"]

        assert activity_row["cli_cost"] == insights_row["total_cost"]
