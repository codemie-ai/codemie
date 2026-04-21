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

"""Unit tests for UserHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.user_handler import UserHandler
from codemie.service.analytics.metric_names import MetricName


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin_or_maintainer = False
    user.is_admin = False
    user.is_maintainer = False
    user.id = "test-user-id"
    return user


@pytest.fixture
def mock_repository():
    """Create mock repository."""
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    """Create handler with mocked dependencies."""
    return UserHandler(mock_user, mock_repository)


class TestGetUsersSpending:
    """Tests for get_users_spending method."""

    def test_get_users_spending_aggregates_by_user_email(self, handler):
        """Verify users are grouped by email."""
        # Arrange
        query = {"bool": {"filter": []}}

        # Act
        agg_body = handler._build_users_spending_aggregation(query, fetch_size=20)

        # Assert
        assert agg_body["query"] == query
        assert agg_body["size"] == 0

        # Verify paginated_results structure
        users_agg = agg_body["aggs"]["paginated_results"]
        assert users_agg["terms"]["field"] == "attributes.user_email.keyword"
        assert users_agg["terms"]["size"] == 20
        # Verify ordered by total_cost descending
        assert users_agg["terms"]["order"] == {"total_cost": "desc"}

    def test_parse_users_spending_result_rounds_cost(self, handler):
        """Verify cost is rounded to 2 decimal places."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "total_cost": {"value": 123.456789},
                        },
                        {
                            "key": "user2@example.com",
                            "total_cost": {"value": 45.999},
                        },
                    ]
                },
                "total_buckets": {"value": 2},
            }
        }

        # Act
        rows = handler._parse_users_spending_result(result, cli_costs_by_user={})

        # Assert
        assert len(rows) == 2
        assert rows[0]["user_email"] == "user1@example.com"
        assert rows[0]["total_cost_usd"] == 123.46  # Rounded
        assert rows[1]["user_email"] == "user2@example.com"
        assert rows[1]["total_cost_usd"] == 46.0  # Rounded

    def test_parse_users_spending_result_handles_none_cost(self, handler):
        """Verify None cost is handled."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "total_cost": {"value": None},
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_users_spending_result(result, cli_costs_by_user={})

        # Assert
        assert len(rows) == 1
        assert rows[0]["total_cost_usd"] == 0

    def test_parse_users_spending_result_filters_empty_emails(self, handler):
        """Verify empty user emails are filtered out."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "total_cost": {"value": 10.50},
                        },
                        {
                            "key": "",  # Empty email should be filtered
                            "total_cost": {"value": 0.01},
                        },
                        {
                            "key": "user2@example.com",
                            "total_cost": {"value": 5.25},
                        },
                    ]
                },
                "total_buckets": {"value": 3},
            }
        }

        # Act
        rows = handler._parse_users_spending_result(result, cli_costs_by_user={})

        # Assert
        assert len(rows) == 2  # Empty email filtered out
        assert rows[0]["user_email"] == "user1@example.com"
        assert rows[0]["total_cost_usd"] == 10.50
        assert rows[1]["user_email"] == "user2@example.com"
        assert rows[1]["total_cost_usd"] == 5.25


class TestGetUsersActivity:
    """Tests for get_users_activity method."""

    def test_get_users_activity_includes_unique_projects(self, handler):
        """Verify unique projects count is extracted via cardinality."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "2": {"value": 3},  # cardinality of unique projects
                            "1-bucket": {"1-metric": {"value": 100.0}},
                            "assistant_cost_bucket": {"assistant_cost": {"value": 30.0}},
                            "workflow_cost_bucket": {"workflow_cost": {"value": 40.0}},
                            "datasource_cost_bucket": {"datasource_cost": {"value": 30.0}},
                            "assistant_tokens_bucket": {"input": {"value": 1000}, "output": {"value": 500}},
                            "workflow_tokens_bucket": {"input": {"value": 2000}, "output": {"value": 1000}},
                            "datasource_tokens_bucket": {"input": {"value": 500}, "output": {"value": 250}},
                            "3-bucket": {"3-metric": {"value": 3500}},
                            "4-bucket": {"4-metric": {"value": 1750}},
                            "5-bucket": {"5-metric": {"value": 100}},
                            "6-bucket": {"6-metric": {"value": 50}},
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_users_activity_result(result)

        # Assert
        assert len(rows) == 1
        row = rows[0]
        assert row["unique_projects"] == 3

    def test_get_users_activity_defaults_to_zero_when_no_projects(self, handler):
        """Verify unique_projects defaults to 0 when no project data."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "2": {"value": 0},  # no unique projects
                            "1-bucket": {"1-metric": {"value": 100.0}},
                            "assistant_cost_bucket": {"assistant_cost": {"value": 100.0}},
                            "workflow_cost_bucket": {"workflow_cost": {"value": 0}},
                            "datasource_cost_bucket": {"datasource_cost": {"value": 0}},
                            "assistant_tokens_bucket": {"input": {"value": 5000}, "output": {"value": 3000}},
                            "workflow_tokens_bucket": {"input": {"value": 0}, "output": {"value": 0}},
                            "datasource_tokens_bucket": {"input": {"value": 0}, "output": {"value": 0}},
                            "3-bucket": {"3-metric": {"value": 5000}},
                            "4-bucket": {"4-metric": {"value": 3000}},
                            "5-bucket": {"5-metric": {"value": 0}},
                            "6-bucket": {"6-metric": {"value": 0}},
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_users_activity_result(result)

        # Assert
        assert len(rows) == 1
        assert rows[0]["unique_projects"] == 0

    def test_get_users_activity_calculates_llm_tokens(self, handler):
        """Verify LLM tokens (input + output from web + CLI) are calculated correctly."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "2": {"value": 1},
                            "1-bucket": {"1-metric": {"value": 100.0}},
                            "assistant_cost_bucket": {"assistant_cost": {"value": 100.0}},
                            "workflow_cost_bucket": {"workflow_cost": {"value": 0}},
                            "datasource_cost_bucket": {"datasource_cost": {"value": 0}},
                            "assistant_tokens_bucket": {"input": {"value": 80}, "output": {"value": 40}},
                            "workflow_tokens_bucket": {"input": {"value": 0}, "output": {"value": 0}},
                            "datasource_tokens_bucket": {"input": {"value": 0}, "output": {"value": 0}},
                            "3-bucket": {"3-metric": {"value": 100}},  # web input_tokens
                            "4-bucket": {"4-metric": {"value": 50}},  # web output_tokens
                            "5-bucket": {"5-metric": {"value": 20}},  # CLI input_tokens
                            "6-bucket": {"6-metric": {"value": 10}},  # CLI output_tokens
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_users_activity_result(result)

        # Assert
        assert len(rows) == 1
        assert rows[0]["llm_tokens"] == 180  # 100 + 50 + 20 + 10

    def test_get_users_activity_aggregation_structure(self, handler):
        """Verify aggregation structure for users activity."""
        # Arrange
        query = {"bool": {"filter": []}}

        # Act
        agg_body = handler._build_users_activity_aggregation(query, fetch_size=20)

        # Assert
        assert agg_body["query"] == query
        assert agg_body["size"] == 0

        # Verify paginated_results structure
        users_agg = agg_body["aggs"]["paginated_results"]
        assert users_agg["terms"]["field"] == "attributes.user_email.keyword"
        assert users_agg["terms"]["size"] == 20
        assert users_agg["terms"]["order"] == {"1-bucket>1-metric": "desc"}

        # Verify sub-aggregations exist
        assert "2" in users_agg["aggs"]  # unique_projects (cardinality)
        assert "1-bucket" in users_agg["aggs"]  # total money_spent
        assert "assistant_cost_bucket" in users_agg["aggs"]  # assistant costs
        assert "workflow_cost_bucket" in users_agg["aggs"]  # workflow costs
        assert "datasource_cost_bucket" in users_agg["aggs"]  # datasource costs
        assert "assistant_tokens_bucket" in users_agg["aggs"]  # assistant tokens
        assert "workflow_tokens_bucket" in users_agg["aggs"]  # workflow tokens
        assert "datasource_tokens_bucket" in users_agg["aggs"]  # datasource tokens
        assert "3-bucket" in users_agg["aggs"]  # web input_tokens
        assert "4-bucket" in users_agg["aggs"]  # web output_tokens
        assert "5-bucket" in users_agg["aggs"]  # CLI input_tokens
        assert "6-bucket" in users_agg["aggs"]  # CLI output_tokens
        assert "filter_zero_tokens" in users_agg["aggs"]  # bucket_selector filter

        # Verify "2" is cardinality aggregation for unique projects
        assert "cardinality" in users_agg["aggs"]["2"]
        assert users_agg["aggs"]["2"]["cardinality"]["field"] == "attributes.project.keyword"

        # Verify 1-bucket filters by all metric types using "should" (not "filter")
        should_filters = users_agg["aggs"]["1-bucket"]["filter"]["bool"]["should"]
        # Extract metric names from should filters
        metric_names = set()
        for filter_item in should_filters:
            if "term" in filter_item:
                metric_names.add(filter_item["term"]["metric_name.keyword"])
            elif "bool" in filter_item and "filter" in filter_item["bool"]:
                # Handle nested bool filter for CLI (with cli_request=true)
                for nested_filter in filter_item["bool"]["filter"]:
                    if "term" in nested_filter and "metric_name.keyword" in nested_filter["term"]:
                        metric_names.add(nested_filter["term"]["metric_name.keyword"])

        assert metric_names == {
            MetricName.CONVERSATION_ASSISTANT_USAGE.value,
            MetricName.CLI_LLM_USAGE_TOTAL.value,  # Changed from CLI_COMMAND_EXECUTION_TOTAL
            MetricName.WORKFLOW_EXECUTION_TOTAL.value,
            MetricName.DATASOURCE_TOKENS_USAGE.value,
        }

        # Verify 3-bucket and 4-bucket filter by LLM metrics only (excludes embeddings)
        assert set(users_agg["aggs"]["3-bucket"]["filter"]["bool"]["filter"][0]["terms"]["metric_name.keyword"]) == {
            MetricName.CONVERSATION_ASSISTANT_USAGE.value,
            MetricName.WORKFLOW_EXECUTION_TOTAL.value,
        }
        assert set(users_agg["aggs"]["4-bucket"]["filter"]["bool"]["filter"][0]["terms"]["metric_name.keyword"]) == {
            MetricName.CONVERSATION_ASSISTANT_USAGE.value,
            MetricName.WORKFLOW_EXECUTION_TOTAL.value,
        }

        # Verify bucket_selector filter exists
        assert "bucket_selector" in users_agg["aggs"]["filter_zero_tokens"]
        bucket_selector = users_agg["aggs"]["filter_zero_tokens"]["bucket_selector"]
        assert "buckets_path" in bucket_selector
        assert "script" in bucket_selector
        assert (
            bucket_selector["script"] == "(params.webInput + params.webOutput + params.cliInput + params.cliOutput) > 0"
        )


def _make_es_page(user_ids: list[str], after_key: dict | None = None) -> dict:
    """Build a fake ES composite aggregation response page."""
    buckets = [{"key": {"user_id": uid, "user_name": f"User {uid}"}, "doc_count": 1} for uid in user_ids]
    agg: dict = {"buckets": buckets}
    if after_key is not None:
        agg["after_key"] = after_key
    return {"aggregations": {"unique_users": agg}}


class TestGetUsersList:
    """Tests for get_users_list / _fetch_all_users_with_after_key."""

    @pytest.mark.asyncio
    async def test_single_page_returns_all_users(self, handler, mock_repository):
        """When ES returns fewer than page_size buckets with no after_key, stops after 1 call."""
        mock_repository.execute_aggregation_query = AsyncMock(return_value=_make_es_page(["u1", "u2", "u3"]))

        query = {"bool": {"filter": []}}
        buckets = await handler._fetch_all_users_with_after_key(query)

        assert len(buckets) == 3
        assert buckets[0]["key"]["user_id"] == "u1"
        mock_repository.execute_aggregation_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_page_collects_all_users(self, handler, mock_repository):
        """When first page is full (10000 buckets) ES returns after_key; loop continues until partial page."""
        page1_ids = [f"u{i}" for i in range(10000)]
        page2_ids = ["u10000", "u10001", "u10002"]

        mock_repository.execute_aggregation_query = AsyncMock(
            side_effect=[
                _make_es_page(page1_ids, after_key={"user_id": "u9999", "user_name": "User u9999"}),
                _make_es_page(page2_ids),
            ]
        )

        query = {"bool": {"filter": []}}
        buckets = await handler._fetch_all_users_with_after_key(query)

        assert len(buckets) == 10003
        assert mock_repository.execute_aggregation_query.call_count == 2

    @pytest.mark.asyncio
    async def test_second_call_includes_after_key(self, handler, mock_repository):
        """Verifies the after_key from page 1 is passed as 'after' in the page 2 request."""
        page1_ids = [f"u{i}" for i in range(10000)]
        after = {"user_id": "u9999", "user_name": "User u9999"}

        mock_repository.execute_aggregation_query = AsyncMock(
            side_effect=[
                _make_es_page(page1_ids, after_key=after),
                _make_es_page(["u10000"]),
            ]
        )

        query = {"bool": {"filter": []}}
        await handler._fetch_all_users_with_after_key(query)

        _, second_call_kwargs = mock_repository.execute_aggregation_query.call_args_list[1]
        second_body = mock_repository.execute_aggregation_query.call_args_list[1][0][0]
        assert second_body["aggs"]["unique_users"]["composite"]["after"] == after

    @pytest.mark.asyncio
    async def test_three_pages_stops_at_partial_last_page(self, handler, mock_repository):
        """Loops correctly across three pages."""
        full_page = [f"u{i}" for i in range(10000)]
        after1 = {"user_id": "u9999", "user_name": "User u9999"}
        after2 = {"user_id": "u19999", "user_name": "User u19999"}

        mock_repository.execute_aggregation_query = AsyncMock(
            side_effect=[
                _make_es_page(full_page, after_key=after1),
                _make_es_page(full_page, after_key=after2),
                _make_es_page(["u20000", "u20001"]),
            ]
        )

        query = {"bool": {"filter": []}}
        buckets = await handler._fetch_all_users_with_after_key(query)

        assert len(buckets) == 20002
        assert mock_repository.execute_aggregation_query.call_count == 3

    @pytest.mark.asyncio
    async def test_partial_page_with_after_key_continues_pagination(self, handler, mock_repository):
        """Partial page (<10000 buckets) with after_key must NOT stop — ES multi-shard scenario."""
        after = {"user_id": "u6999", "user_name": "User u6999"}

        mock_repository.execute_aggregation_query = AsyncMock(
            side_effect=[
                _make_es_page([f"u{i}" for i in range(7000)], after_key=after),
                _make_es_page([f"u{i}" for i in range(7000, 10000)]),
            ]
        )

        query = {"bool": {"filter": []}}
        buckets = await handler._fetch_all_users_with_after_key(query)

        assert len(buckets) == 10000
        assert mock_repository.execute_aggregation_query.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self, handler, mock_repository):
        """ES returning zero buckets produces an empty list with one call."""
        mock_repository.execute_aggregation_query = AsyncMock(return_value=_make_es_page([]))

        query = {"bool": {"filter": []}}
        buckets = await handler._fetch_all_users_with_after_key(query)

        assert buckets == []
        mock_repository.execute_aggregation_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_users_list_non_admin_returns_correct_response_shape(self, handler, mock_repository, mock_user):
        """Non-admin get_users_list uses single-page query and returns correct response shape."""
        mock_user.is_admin_or_maintainer = False
        mock_repository.execute_aggregation_query = AsyncMock(return_value=_make_es_page(["alice", "bob"]))

        response = await handler.get_users_list(time_period="last_24_hours")

        data = response["data"]
        assert data["total_count"] == 2
        assert {"id": "alice", "name": "User alice"} in data["users"]
        assert {"id": "bob", "name": "User bob"} in data["users"]
        assert "metadata" in response
        mock_repository.execute_aggregation_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_users_list_superadmin_uses_after_key_pagination(self, handler, mock_repository, mock_user):
        """Superadmin get_users_list uses after_key pagination to fetch beyond 10,000 users."""
        mock_user.is_admin_or_maintainer = True
        page1_ids = [f"u{i}" for i in range(10000)]
        page2_ids = ["u10000", "u10001"]
        mock_repository.execute_aggregation_query = AsyncMock(
            side_effect=[
                _make_es_page(page1_ids, after_key={"user_id": "u9999", "user_name": "User u9999"}),
                _make_es_page(page2_ids),
            ]
        )

        response = await handler.get_users_list(time_period="last_24_hours")

        data = response["data"]
        assert data["total_count"] == 10002
        assert mock_repository.execute_aggregation_query.call_count == 2

    @pytest.mark.asyncio
    async def test_get_users_list_non_admin_uses_single_query(self, handler, mock_repository, mock_user):
        """Non-admin get_users_list executes exactly one ES query (no pagination loop)."""
        mock_user.is_admin_or_maintainer = False
        mock_repository.execute_aggregation_query = AsyncMock(
            return_value=_make_es_page([f"u{i}" for i in range(10000)])
        )

        await handler.get_users_list(time_period="last_24_hours")

        mock_repository.execute_aggregation_query.assert_called_once()


class TestGetUsersUniqueDaily:
    """Tests for get_users_unique_daily method."""

    def test_build_users_unique_daily_aggregation_structure(self, handler):
        """Verify date_histogram aggregation structure with cardinality."""
        # Arrange
        query = {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": "2025-12-01T00:00:00.000Z", "lte": "2025-12-31T23:59:59.999Z"}}}
                ]
            }
        }

        # Act
        agg_body = handler._build_users_unique_daily_aggregation(query)

        # Assert
        assert agg_body["query"] == query
        assert agg_body["size"] == 0

        # Verify paginated_results structure uses date_histogram
        date_hist_agg = agg_body["aggs"]["paginated_results"]
        assert "date_histogram" in date_hist_agg
        assert date_hist_agg["date_histogram"]["field"] == "time"
        assert date_hist_agg["date_histogram"]["calendar_interval"] == "1d"
        assert date_hist_agg["date_histogram"]["time_zone"] == "UTC"
        assert date_hist_agg["date_histogram"]["order"] == {"_key": "asc"}

        # Verify extended_bounds is present
        assert "extended_bounds" in date_hist_agg["date_histogram"]
        extended_bounds = date_hist_agg["date_histogram"]["extended_bounds"]
        assert "min" in extended_bounds
        assert "max" in extended_bounds

        # Verify nested filtered_users aggregation with cardinality on user_id
        assert "aggs" in date_hist_agg
        assert "filtered_users" in date_hist_agg["aggs"]
        filtered_users_agg = date_hist_agg["aggs"]["filtered_users"]
        # Verify filter excludes null/empty user_ids
        assert "filter" in filtered_users_agg
        assert "bool" in filtered_users_agg["filter"]
        # Verify nested cardinality aggregation
        assert "aggs" in filtered_users_agg
        assert "unique_users" in filtered_users_agg["aggs"]
        assert "cardinality" in filtered_users_agg["aggs"]["unique_users"]
        assert filtered_users_agg["aggs"]["unique_users"]["cardinality"]["field"] == "attributes.user_id.keyword"

    def test_parse_users_unique_daily_result_formats_dates(self, handler):
        """Verify date formatting and cardinality extraction."""
        # Arrange - Mock ES result with date buckets (nested filtered_users structure)
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": 1766448000000,  # epoch millis
                            "key_as_string": "2025-12-22T00:00:00.000Z",
                            "filtered_users": {"unique_users": {"value": 42}},
                        },
                        {
                            "key": 1766534400000,
                            "key_as_string": "2025-12-23T00:00:00.000Z",
                            "filtered_users": {"unique_users": {"value": 58}},
                        },
                        {
                            "key": 1766620800000,
                            "key_as_string": "2025-12-24T00:00:00.000Z",
                            "filtered_users": {"unique_users": {"value": 35}},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_users_unique_daily_result(result)

        # Assert
        assert len(rows) == 3
        # Verify first row
        assert rows[0]["date"] == "2025-12-22"
        assert rows[0]["unique_users"] == 42
        # Verify second row
        assert rows[1]["date"] == "2025-12-23"
        assert rows[1]["unique_users"] == 58
        # Verify third row
        assert rows[2]["date"] == "2025-12-24"
        assert rows[2]["unique_users"] == 35

    def test_parse_users_unique_daily_result_handles_empty_buckets(self, handler):
        """Verify empty buckets handling."""
        # Arrange
        result = {"aggregations": {"paginated_results": {"buckets": []}}}

        # Act
        rows = handler._parse_users_unique_daily_result(result)

        # Assert
        assert rows == []

    def test_get_users_unique_daily_columns(self, handler):
        """Verify column definitions."""
        # Act
        columns = handler._get_users_unique_daily_columns()

        # Assert
        assert len(columns) == 2
        # Verify date column
        assert columns[0]["id"] == "date"
        assert columns[0]["label"] == "Date"
        assert columns[0]["type"] == "date"
        # Verify unique_users column
        assert columns[1]["id"] == "unique_users"
        assert columns[1]["label"] == "Unique Users"
        assert columns[1]["type"] == "number"
