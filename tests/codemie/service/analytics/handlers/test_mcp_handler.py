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

"""Unit tests for MCPHandler."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.mcp_handler import MCPHandler


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
    return MCPHandler(mock_user, mock_repository)


class TestGetMCPServers:
    """Tests for get_mcp_servers method."""

    @pytest.mark.asyncio
    async def test_get_mcp_servers_uses_mcp_metrics(self, handler, mock_repository):
        """Verify MCP handlers use MCP-specific metrics."""
        # Arrange
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {"key": "mcp-server-1", "doc_count": 100},
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        result = await handler.get_mcp_servers(time_period="last_30_days")

        # Assert
        # Verify the pipeline was called TWICE (parallel queries: data + totals)
        assert mock_repository.execute_aggregation_query.call_count == 2

        # Verify result structure
        assert "data" in result
        assert isinstance(result["data"], dict)
        assert "columns" in result["data"]
        assert "rows" in result["data"]

    def test_build_mcp_servers_aggregation(self, handler):
        """Verify MCP servers aggregation structure."""
        # Arrange
        query = {"bool": {"filter": []}}

        # Act
        agg_body = handler._build_mcp_servers_aggregation(query, fetch_size=20)

        # Assert
        assert agg_body["query"] == query
        assert agg_body["size"] == 0

        # Verify paginated_results structure
        assert "paginated_results" in agg_body["aggs"]
        assert agg_body["aggs"]["paginated_results"]["terms"]["field"] == "attributes.mcp_name.keyword"
        assert agg_body["aggs"]["paginated_results"]["terms"]["size"] == 20

    def test_parse_mcp_servers_result(self, handler):
        """Verify MCP servers result parsing."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {"key": "mcp-server-1", "filtered_requests": {"doc_count": 100}},
                        {"key": "mcp-server-2", "filtered_requests": {"doc_count": 50}},
                    ]
                },
                "total_buckets": {"value": 2},
            }
        }

        # Act
        rows = handler._parse_mcp_servers_result(result)

        # Assert
        assert len(rows) == 2
        assert rows[0]["mcp_name"] == "mcp-server-1"
        assert rows[0]["total_requests"] == 100
        assert rows[1]["mcp_name"] == "mcp-server-2"
        assert rows[1]["total_requests"] == 50


class TestGetMCPServersByUsers:
    """Tests for get_mcp_servers_by_users method."""

    def test_build_mcp_servers_by_users_aggregation_structure(self, handler):
        """Verify MCP servers by users aggregation structure."""
        # Arrange
        query = {"bool": {"filter": []}}

        # Act
        agg_body = handler._build_mcp_servers_by_users_aggregation(query, fetch_size=20)

        # Assert
        assert agg_body["size"] == 0

        # Verify query is enhanced with MCP filters
        enhanced_query = agg_body["query"]
        assert "bool" in enhanced_query
        assert "must" in enhanced_query["bool"]
        assert len(enhanced_query["bool"]["must"]) == 2
        assert enhanced_query["bool"]["must"][0] == query  # Original query
        # Second must clause should have MCP metric filters
        assert "bool" in enhanced_query["bool"]["must"][1]
        assert "should" in enhanced_query["bool"]["must"][1]["bool"]
        # Filter clause should check for mcp_name existence
        assert "filter" in enhanced_query["bool"]
        assert {"exists": {"field": "attributes.mcp_name.keyword"}} in enhanced_query["bool"]["filter"]

        # Verify paginated_results structure (nested terms aggregation)
        users_agg = agg_body["aggs"]["paginated_results"]
        assert users_agg["terms"]["field"] == "attributes.user_name.keyword"
        # Size is multiplied by 5 with minimum 100
        assert users_agg["terms"]["size"] == 100

        # Verify nested mcp_servers aggregation
        assert "mcp_servers" in users_agg["aggs"]
        mcp_servers_agg = users_agg["aggs"]["mcp_servers"]
        assert "terms" in mcp_servers_agg
        assert mcp_servers_agg["terms"]["field"] == "attributes.mcp_name.keyword"
        assert mcp_servers_agg["terms"]["size"] == 50
        assert mcp_servers_agg["terms"]["order"] == {"_count": "desc"}

    def test_parse_mcp_servers_by_users_result(self, handler):
        """Verify MCP servers by users result parsing."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "doc_count": 150,
                            "mcp_servers": {
                                "buckets": [
                                    {"key": "mcp-server-1", "doc_count": 150},
                                ]
                            },
                        },
                        {
                            "key": "user2@example.com",
                            "doc_count": 100,
                            "mcp_servers": {
                                "buckets": [
                                    {"key": "mcp-server-2", "doc_count": 100},
                                ]
                            },
                        },
                    ]
                },
                "total_buckets": {"value": 2},
            }
        }

        # Act
        rows = handler._parse_mcp_servers_by_users_result(result)

        # Assert
        assert len(rows) == 2
        assert rows[0]["user_name"] == "user1@example.com"
        assert rows[0]["total_requests"] == 150
        assert rows[0]["mcp_name"] == "mcp-server-1"
        assert rows[1]["user_name"] == "user2@example.com"
        assert rows[1]["total_requests"] == 100
        assert rows[1]["mcp_name"] == "mcp-server-2"

    def test_parse_mcp_servers_by_users_result_handles_no_mcp_servers(self, handler):
        """Verify handling when a user has no MCP servers in nested buckets."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "doc_count": 150,
                            "mcp_servers": {"buckets": []},  # No MCP servers for this user
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_mcp_servers_by_users_result(result)

        # Assert
        # Users with no MCP servers should not appear in results
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_mcp_servers_by_users_pagination_accuracy(self, handler, mock_repository):
        """Verify accurate pagination with flattened nested results.

        Scenario: 3 users with varying MCP servers (3 + 2 + 5 = 10 total rows)
        Request: page=0, per_page=3
        Expected: Exactly 3 rows returned
        """
        # Arrange: Mock Elasticsearch response with 3 users
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "mcp_servers": {
                                "buckets": [
                                    {"key": "mcp-filesystem", "doc_count": 100},
                                    {"key": "mcp-git", "doc_count": 50},
                                    {"key": "mcp-github", "doc_count": 25},
                                ]
                            },
                        },
                        {
                            "key": "user2@example.com",
                            "mcp_servers": {
                                "buckets": [
                                    {"key": "mcp-filesystem", "doc_count": 75},
                                    {"key": "mcp-slack", "doc_count": 30},
                                ]
                            },
                        },
                        {
                            "key": "user3@example.com",
                            "mcp_servers": {
                                "buckets": [
                                    {"key": "mcp-git", "doc_count": 90},
                                    {"key": "mcp-jira", "doc_count": 40},
                                    {"key": "mcp-confluence", "doc_count": 35},
                                    {"key": "mcp-slack", "doc_count": 20},
                                    {"key": "mcp-github", "doc_count": 10},
                                ]
                            },
                        },
                    ]
                }
            }
        }

        # Act: Request page 0 with per_page=3
        result = await handler.get_mcp_servers_by_users(time_period="last_30_days", page=0, per_page=3)

        # Assert: Exactly 3 rows returned
        assert len(result["data"]["rows"]) == 3
        assert result["pagination"]["total_count"] == 10
        assert result["pagination"]["has_more"] is True
        assert result["pagination"]["page"] == 0
        assert result["pagination"]["per_page"] == 3

        # Verify rows are sorted by total_requests DESC
        rows = result["data"]["rows"]
        assert rows[0]["total_requests"] == 100  # user1, mcp-filesystem
        assert rows[1]["total_requests"] == 90  # user3, mcp-git
        assert rows[2]["total_requests"] == 75  # user2, mcp-filesystem

        # Verify consistent ordering (alphabetical tie-breaker)
        assert rows[0]["user_name"] == "user1@example.com"
        assert rows[0]["mcp_name"] == "mcp-filesystem"

    @pytest.mark.asyncio
    async def test_mcp_servers_by_users_last_page(self, handler, mock_repository):
        """Verify last page returns partial rows correctly."""
        # Arrange: Mock 7 total rows
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user@example.com",
                            "mcp_servers": {"buckets": [{"key": f"mcp-{i}", "doc_count": 10 - i} for i in range(7)]},
                        }
                    ]
                }
            }
        }

        # Act: Request page 2 (third page) with per_page=3
        result = await handler.get_mcp_servers_by_users(time_period="last_30_days", page=2, per_page=3)

        # Assert: Last page has only 1 row (rows 6-7, but only 7 total)
        assert len(result["data"]["rows"]) == 1
        assert result["pagination"]["total_count"] == 7
        assert result["pagination"]["has_more"] is False
        assert result["pagination"]["page"] == 2

    @pytest.mark.asyncio
    async def test_mcp_servers_by_users_sorting_consistency(self, handler, mock_repository):
        """Verify sorting is consistent for items with same total_requests."""
        # Arrange: Mock data with tied request counts
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "bob@example.com",
                            "mcp_servers": {
                                "buckets": [
                                    {"key": "mcp-git", "doc_count": 50},
                                    {"key": "mcp-filesystem", "doc_count": 50},
                                ]
                            },
                        },
                        {
                            "key": "alice@example.com",
                            "mcp_servers": {"buckets": [{"key": "mcp-slack", "doc_count": 50}]},
                        },
                    ]
                }
            }
        }

        # Act
        result = await handler.get_mcp_servers_by_users(time_period="last_30_days", page=0, per_page=10)

        # Assert: Alphabetical tie-breaker applied
        rows = result["data"]["rows"]
        assert len(rows) == 3

        # All have same total_requests, so sorted by user_name, then mcp_name
        assert rows[0]["user_name"] == "alice@example.com"
        assert rows[0]["mcp_name"] == "mcp-slack"

        assert rows[1]["user_name"] == "bob@example.com"
        assert rows[1]["mcp_name"] == "mcp-filesystem"

        assert rows[2]["user_name"] == "bob@example.com"
        assert rows[2]["mcp_name"] == "mcp-git"
