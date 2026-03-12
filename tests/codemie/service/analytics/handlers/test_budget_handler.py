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

"""Unit tests for BudgetHandler."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.budget_handler import BudgetHandler


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.id = "test-user-id"
    return user


@pytest.fixture
def mock_repository():
    """Create mock repository."""
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    """Create handler with mocked dependencies."""
    return BudgetHandler(mock_user, mock_repository)


class TestBudgetSoftLimit:
    """Tests for budget soft limit."""

    @pytest.mark.asyncio
    async def test_get_budget_soft_limit_filters_by_soft_limit_metric(self, handler, mock_repository):
        """Verify soft limit queries use correct metric."""
        # Arrange
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "max_spent": {"value": 100.0},
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        result = await handler.get_budget_soft_limit(time_period="last_30_days")

        # Assert
        # The pipeline would be called with metric_filters containing BUDGET_SOFT_LIMIT_WARNING
        # We verify the repository was called TWICE (parallel queries: data + totals)
        assert mock_repository.execute_aggregation_query.call_count == 2

        # Verify result structure
        assert "data" in result
        assert isinstance(result["data"], dict)
        assert "columns" in result["data"]
        assert "rows" in result["data"]

    def test_build_budget_soft_limit_aggregation(self, handler):
        """Verify soft limit aggregation structure."""
        # Arrange
        query = {"bool": {"filter": []}}

        # Act
        agg_body = handler._build_budget_soft_limit_aggregation(query, fetch_size=20)

        # Assert
        assert agg_body["query"] == query
        assert agg_body["size"] == 0

        # Verify paginated_results structure
        users_agg = agg_body["aggs"]["paginated_results"]
        assert users_agg["terms"]["field"] == "attributes.user_email.keyword"
        assert users_agg["terms"]["size"] == 20
        assert users_agg["terms"]["order"] == {"max_spent": "desc"}

        # Verify max aggregation on spent field
        assert "max_spent" in users_agg["aggs"]
        assert users_agg["aggs"]["max_spent"]["max"]["field"] == "attributes.spent"


class TestBudgetHardLimit:
    """Tests for budget hard limit."""

    @pytest.mark.asyncio
    async def test_get_budget_hard_limit_filters_by_hard_limit_metric(self, handler, mock_repository):
        """Verify hard limit queries use correct metric."""
        # Arrange
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "max_spent": {"value": 200.0},
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        result = await handler.get_budget_hard_limit(time_period="last_30_days")

        # Assert
        # The pipeline would be called with metric_filters containing BUDGET_HARD_LIMIT_VIOLATION
        # We verify the repository was called TWICE (parallel queries: data + totals)
        assert mock_repository.execute_aggregation_query.call_count == 2

        # Verify result structure
        assert "data" in result
        assert isinstance(result["data"], dict)
        assert "columns" in result["data"]
        assert "rows" in result["data"]

    def test_build_budget_hard_limit_aggregation(self, handler):
        """Verify hard limit aggregation structure."""
        # Arrange
        query = {"bool": {"filter": []}}

        # Act
        agg_body = handler._build_budget_hard_limit_aggregation(query, fetch_size=20)

        # Assert
        assert agg_body["query"] == query
        assert agg_body["size"] == 0

        # Verify paginated_results structure
        users_agg = agg_body["aggs"]["paginated_results"]
        assert users_agg["terms"]["field"] == "attributes.user_email.keyword"
        assert users_agg["terms"]["size"] == 20
        assert users_agg["terms"]["order"] == {"max_spent": "desc"}

        # Verify max aggregation on spent field
        assert "max_spent" in users_agg["aggs"]
        assert users_agg["aggs"]["max_spent"]["max"]["field"] == "attributes.spent"


class TestBudgetLimitParser:
    """Tests for budget limit parser (shared by soft and hard limit)."""

    def test_parse_budget_limit_result_rounds_cost(self, handler):
        """Verify max_spent is rounded to 2 decimal places."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "max_spent": {"value": 123.456789},
                        },
                        {
                            "key": "user2@example.com",
                            "max_spent": {"value": 99.999},
                        },
                    ]
                },
                "total_buckets": {"value": 2},
            }
        }

        # Act
        rows = handler._parse_budget_limit_result(result)

        # Assert
        assert len(rows) == 2
        assert rows[0]["user_email"] == "user1@example.com"
        assert rows[0]["max_spent"] == 123.46  # Rounded
        assert rows[1]["user_email"] == "user2@example.com"
        assert rows[1]["max_spent"] == 100.0  # Rounded

    def test_parse_budget_limit_result_handles_none(self, handler):
        """Verify None values are handled."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "max_spent": {"value": None},
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_budget_limit_result(result)

        # Assert
        assert len(rows) == 1
        assert rows[0]["max_spent"] == 0

    def test_get_budget_limit_columns(self, handler):
        """Verify column definitions."""
        # Act
        columns = handler._get_budget_limit_columns()

        # Assert
        assert len(columns) == 2
        assert columns[0]["id"] == "user_name"
        assert columns[0]["type"] == "string"
        assert columns[1]["id"] == "max_spent"
        assert columns[1]["type"] == "number"
        assert columns[1]["format"] == "currency"
