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

"""Unit tests for CLIHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.cli_handler import CLIHandler


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
    return CLIHandler(mock_user, mock_repository)


class TestCLISummary:
    """Tests for CLI summary metrics."""

    @pytest.mark.asyncio
    async def test_get_cli_summary_returns_overview_metrics_with_dau_and_mau(self, handler):
        """Verify cli-summary returns the overview cards plus CLI DAU/MAU."""
        handler._pipeline.execute_summary_query = AsyncMock(
            side_effect=[
                {
                    "data": {
                        "metrics": [
                            {"id": "input_tokens", "label": "Input Tokens", "type": "number", "value": 100},
                            {
                                "id": "cached_creation_tokens",
                                "label": "Cache Creation Tokens",
                                "type": "number",
                                "value": 10,
                            },
                            {"id": "cached_tokens_read", "label": "Cache Read Tokens", "type": "number", "value": 20},
                            {"id": "output_tokens", "label": "Output Tokens", "type": "number", "value": 30},
                            {"id": "unique_users", "label": "Unique Users", "type": "number", "value": 5},
                            {"id": "unique_projects", "label": "Total Projects", "type": "number", "value": 4},
                            {"id": "unique_sessions", "label": "Unique Sessions", "type": "number", "value": 9},
                            {"id": "unique_repos", "label": "Unique Repositories", "type": "number", "value": 7},
                        ]
                    }
                },
                {
                    "data": {
                        "metrics": [
                            {
                                "id": "dau",
                                "label": "DAU",
                                "type": "number",
                                "value": 2,
                                "format": "number",
                                "description": "Distinct CLI proxy users active in last 1 day",
                                "fixed_timeframe": "Last 1 day",
                            }
                        ]
                    }
                },
                {
                    "data": {
                        "metrics": [
                            {
                                "id": "mau",
                                "label": "MAU",
                                "type": "number",
                                "value": 11,
                                "format": "number",
                                "description": "Distinct CLI proxy users active in last 1 month",
                                "fixed_timeframe": "Last 1 month",
                            }
                        ]
                    }
                },
            ]
        )
        handler.get_cli_costs_with_adjustment = AsyncMock(return_value={"total_cost": 123.45})

        response = await handler.get_cli_summary(time_period="last_30_days")

        assert [metric["id"] for metric in response["data"]["metrics"]] == [
            "unique_users",
            "dau",
            "mau",
            "unique_sessions",
            "cli_cost",
            "total_tokens",
            "unique_projects",
            "unique_repos",
        ]
        assert [metric["label"] for metric in response["data"]["metrics"]] == [
            "Total Users",
            "DAU",
            "MAU",
            "Total Sessions",
            "Total Cost",
            "Total Tokens",
            "Total Projects",
            "Repositories",
        ]
        assert next(metric for metric in response["data"]["metrics"] if metric["id"] == "cli_cost")["value"] == 123.45
        assert (
            next(metric for metric in response["data"]["metrics"] if metric["id"] == "unique_users")["description"]
            == "Distinct CLI users"
        )
        assert (
            next(metric for metric in response["data"]["metrics"] if metric["id"] == "cli_cost")["description"]
            == "Total CLI proxy cost"
        )
        assert next(metric for metric in response["data"]["metrics"] if metric["id"] == "total_tokens")["value"] == 160
        assert (
            next(metric for metric in response["data"]["metrics"] if metric["id"] == "total_tokens")["description"]
            == "Total CLI proxy tokens"
        )
        assert (
            next(metric for metric in response["data"]["metrics"] if metric["id"] == "dau")["fixed_timeframe"]
            == "Last 1 day"
        )
        assert (
            next(metric for metric in response["data"]["metrics"] if metric["id"] == "mau")["fixed_timeframe"]
            == "Last 1 month"
        )

    def test_build_cli_summary_aggregation_structure(self, handler):
        """Verify CLI summary aggregation only includes overview metrics and token inputs."""
        query = {"bool": {"filter": []}}
        agg_body = handler._build_cli_summary_aggregation(query)

        assert agg_body["query"] == query
        assert agg_body["size"] == 0

        expected_metrics = [
            "unique_users",
            "unique_sessions",
            "unique_projects",
            "unique_repos",
            "input_tokens",
            "output_tokens",
            "cached_tokens_read",
            "cached_creation_tokens",
        ]
        for metric in expected_metrics:
            assert metric in agg_body["aggs"], f"Missing metric: {metric}"

        assert set(agg_body["aggs"]) == set(expected_metrics)

        cli_filter = agg_body["aggs"]["unique_users"]["filter"]["bool"]["filter"][0]
        assert cli_filter == {"term": {"metric_name.keyword": "codemie_cli_tool_usage_total"}}

        session_filter = agg_body["aggs"]["unique_sessions"]["filter"]["bool"]["filter"][0]
        assert session_filter == {"term": {"metric_name.keyword": "codemie_cli_session_total"}}

    def test_build_cli_active_users_aggregation_uses_proxy_metrics_only(self, handler):
        """Verify CLI DAU/MAU use proxy usage records only."""
        query = {"bool": {"filter": []}}

        agg_body = handler._build_cli_active_users_aggregation(query)

        filters = agg_body["aggs"]["unique_users"]["filter"]["bool"]["filter"]
        assert {"term": {"metric_name.keyword": "codemie_litellm_proxy_usage"}} in filters
        assert {"term": {"attributes.cli_request": True}} in filters
        assert agg_body["aggs"]["unique_users"]["aggs"]["count"]["cardinality"]["field"] == "attributes.user_id.keyword"

    def test_parse_cli_active_users_result_builds_fixed_window_metric(self, handler):
        """Verify CLI active users parser returns a fixed-timeframe metric."""
        result = {"aggregations": {"unique_users": {"count": {"value": 17}}}}

        metrics = handler._parse_cli_active_users_result(
            result,
            metric_id="dau",
            label="DAU",
            fixed_timeframe="Last 1 day",
        )

        assert metrics == [
            {
                "id": "dau",
                "label": "DAU",
                "type": "number",
                "value": 17,
                "format": "number",
                "description": "Distinct CLI proxy users active in last 1 day",
                "fixed_timeframe": "Last 1 day",
            }
        ]

    def test_parse_cli_summary_result_builds_only_underlying_overview_metrics(self, handler):
        """Verify only the base metrics needed by the overview are parsed."""
        result = {
            "aggregations": {
                "unique_users": {"count": {"value": 25}},
                "unique_sessions": {"count": {"value": 50}},
                "unique_projects": {"count": {"value": 12}},
                "unique_repos": {"count": {"value": 10}},
                "input_tokens": {"total": {"value": 100000}},
                "output_tokens": {"total": {"value": 50000}},
                "cached_tokens_read": {"total": {"value": 20000}},
                "cached_creation_tokens": {"total": {"value": 10000}},
            }
        }

        metrics = handler._parse_cli_summary_result(result)

        assert len(metrics) == 8
        assert [metric["id"] for metric in metrics] == [
            "input_tokens",
            "cached_creation_tokens",
            "cached_tokens_read",
            "output_tokens",
            "unique_users",
            "unique_projects",
            "unique_sessions",
            "unique_repos",
        ]
        assert next(m for m in metrics if m["id"] == "unique_users")["value"] == 25
        assert next(m for m in metrics if m["id"] == "unique_users")["description"] == "Distinct CLI users"
        assert next(m for m in metrics if m["id"] == "unique_projects")["value"] == 12
        assert next(m for m in metrics if m["id"] == "unique_projects")["description"] == "Distinct CLI projects"
        assert next(m for m in metrics if m["id"] == "input_tokens")["value"] == 100000
        assert next(m for m in metrics if m["id"] == "unique_repos")["value"] == 10


class TestCLIAgents:
    """Tests for CLI agents (clients) analytics."""

    def test_build_cli_agents_aggregation(self, handler):
        """Verify CLI agents aggregation groups by codemie_client field."""
        query = {"bool": {"filter": []}}
        agg_body = handler._build_cli_agents_aggregation(query, fetch_size=20)

        assert agg_body["aggs"]["paginated_results"]["terms"]["field"] == "attributes.codemie_client.keyword"
        assert agg_body["aggs"]["paginated_results"]["terms"]["size"] == 20

    def test_parse_cli_agents_result(self, handler):
        """Verify CLI agents result parsing with client_name and total_usage fields."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {"key": "claude-code", "doc_count": 500},
                        {"key": "vscode-extension", "doc_count": 300},
                    ]
                }
            }
        }

        rows = handler._parse_cli_agents_result(result)

        assert len(rows) == 2
        assert rows[0]["client_name"] == "claude-code"
        assert rows[0]["total_usage"] == 500
        assert rows[1]["client_name"] == "vscode-extension"
        assert rows[1]["total_usage"] == 300


class TestCLIErrors:
    """Tests for proxy errors analytics."""

    def test_build_cli_errors_aggregation_structure(self, handler):
        """Verify proxy errors aggregation groups by response_status field."""
        query = {"bool": {"filter": []}}
        agg_body = handler._build_cli_errors_aggregation(query, fetch_size=20)

        # Updated to response_status field (not error_code)
        assert agg_body["aggs"]["paginated_results"]["terms"]["field"] == "attributes.response_status"
        assert agg_body["aggs"]["paginated_results"]["terms"]["size"] == 20

    def test_parse_cli_errors_result(self, handler):
        """Verify proxy errors result parsing with response_status and occurrences."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {"key": "500", "doc_count": 45},
                        {"key": "503", "doc_count": 30},
                        {"key": "429", "doc_count": 15},
                    ]
                }
            }
        }

        rows = handler._parse_cli_errors_result(result)

        assert len(rows) == 3
        assert rows[0]["response_status"] == "500"
        assert rows[0]["total_occurrences"] == 45
        assert rows[1]["response_status"] == "503"
        assert rows[1]["total_occurrences"] == 30


class TestCLIUsers:
    """Tests for CLI users analytics."""

    def test_build_cli_users_aggregation_structure(self, handler):
        """Verify CLI users aggregation includes top_metrics for last project/repository."""
        query = {"bool": {"filter": []}}
        agg_body = handler._build_cli_users_aggregation(query, fetch_size=20)

        # Verify top_metrics sub-aggregations exist
        user_agg = agg_body["aggs"]["paginated_results"]
        assert "last_project" in user_agg["aggs"]
        assert "last_repository" in user_agg["aggs"]

        # Verify top_metrics structure for last_project
        assert (
            user_agg["aggs"]["last_project"]["aggs"]["top_project"]["top_metrics"]["metrics"]["field"]
            == "attributes.project.keyword"
        )
        assert user_agg["aggs"]["last_project"]["aggs"]["top_project"]["top_metrics"]["sort"] == {"@timestamp": "desc"}

    def test_parse_cli_users_result_with_top_metrics(self, handler):
        """Verify CLI users result parsing extracts last_project and last_repository from top_metrics."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "doc_count": 100,
                            "last_project": {
                                "top_project": {"top": [{"metrics": {"attributes.project.keyword": "project-alpha"}}]}
                            },
                            "last_repository": {
                                "top_repository": {"top": [{"metrics": {"attributes.repository.keyword": "repo-main"}}]}
                            },
                        },
                        {
                            "key": "user2@example.com",
                            "doc_count": 75,
                            "last_project": {"top_project": {"top": []}},
                            "last_repository": {"top_repository": {"top": []}},
                        },
                    ]
                }
            }
        }

        rows = handler._parse_cli_users_result(result)

        assert len(rows) == 2
        # User with top_metrics data
        assert rows[0]["user_name"] == "user1@example.com"
        assert rows[0]["total_commands"] == 100
        assert rows[0]["last_project"] == "project-alpha"
        assert rows[0]["last_repository"] == "repo-main"
        # User without top_metrics data
        assert rows[1]["user_name"] == "user2@example.com"
        assert rows[1]["total_commands"] == 75
        assert rows[1]["last_project"] is None
        assert rows[1]["last_repository"] is None


class TestCLIRepositories:
    """Tests for CLI repositories analytics with 3-level nested aggregation."""

    def test_build_cli_repositories_aggregation_structure(self, handler):
        """Verify 3-level nested aggregation structure (repo→branch→user)."""
        query = {"bool": {"filter": []}}
        agg_body = handler._build_cli_repositories_aggregation(query, fetch_size=20)

        # Level 1: Repository
        repo_agg = agg_body["aggs"]["paginated_results"]
        assert repo_agg["terms"]["field"] == "attributes.repository.keyword"
        assert repo_agg["terms"]["size"] == 20

        # Level 2: Branch (nested in repository)
        branch_agg = repo_agg["aggs"]["branches"]
        assert branch_agg["terms"]["field"] == "attributes.branch.keyword"
        assert branch_agg["terms"]["size"] == 10  # Capped at 10 branches

        # Level 3: User (nested in branch)
        user_agg = branch_agg["aggs"]["users"]
        assert user_agg["terms"]["field"] == "attributes.user_email.keyword"
        assert user_agg["terms"]["size"] == 20  # Capped at 20 users

        # Verify metrics are organized in two buckets: token_data and session_data
        assert "token_data" in user_agg["aggs"]
        assert "session_data" in user_agg["aggs"]

        # Verify token_data contains 4 token metrics
        token_data_aggs = user_agg["aggs"]["token_data"]["aggs"]
        assert "input_tokens" in token_data_aggs
        assert "cache_creation_tokens" in token_data_aggs
        assert "cache_read_tokens" in token_data_aggs
        assert "output_tokens" in token_data_aggs

        # Verify session_data contains 2 session metrics
        session_data_aggs = user_agg["aggs"]["session_data"]["aggs"]
        assert "session_duration" in session_data_aggs
        assert "total_lines_added" in session_data_aggs

    def test_parse_cli_repositories_result_flattens_3_level_nesting(self, handler):
        """Verify 3-level nested result is correctly flattened into tabular rows."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "repo-alpha",
                            "branches": {
                                "buckets": [
                                    {
                                        "key": "main",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "user1@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 10000},
                                                        "cache_creation_tokens": {"value": 2000},
                                                        "cache_read_tokens": {"value": 5000},
                                                        "output_tokens": {"value": 8000},
                                                    },
                                                    "session_data": {
                                                        "session_duration": {"value": 60000},
                                                        "total_lines_added": {"value": 500},
                                                    },
                                                },
                                                {
                                                    "key": "user2@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 5000},
                                                        "cache_creation_tokens": {"value": 1000},
                                                        "cache_read_tokens": {"value": 2000},
                                                        "output_tokens": {"value": 4000},
                                                    },
                                                    "session_data": {
                                                        "session_duration": {"value": 30000},
                                                        "total_lines_added": {"value": 200},
                                                    },
                                                },
                                            ]
                                        },
                                    },
                                    {
                                        "key": "dev",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "user1@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 3000},
                                                        "cache_creation_tokens": {"value": 500},
                                                        "cache_read_tokens": {"value": 1000},
                                                        "output_tokens": {"value": 2000},
                                                    },
                                                    "session_data": {
                                                        "session_duration": {"value": 15000},
                                                        "total_lines_added": {"value": 100},
                                                    },
                                                }
                                            ]
                                        },
                                    },
                                ]
                            },
                        },
                        {
                            "key": "repo-beta",
                            "branches": {
                                "buckets": [
                                    {
                                        "key": "main",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "user3@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 15000},
                                                        "cache_creation_tokens": {"value": 3000},
                                                        "cache_read_tokens": {"value": 7000},
                                                        "output_tokens": {"value": 10000},
                                                    },
                                                    "session_data": {
                                                        "session_duration": {"value": 90000},
                                                        "total_lines_added": {"value": 800},
                                                    },
                                                }
                                            ]
                                        },
                                    }
                                ]
                            },
                        },
                    ]
                }
            }
        }

        rows = handler._parse_cli_repositories_result(result)

        # Should flatten to 4 rows: repo-alpha/main/user1, repo-alpha/main/user2, repo-alpha/dev/user1, repo-beta/main/user3
        assert len(rows) == 4

        # Row 1: repo-alpha/main/user1
        assert rows[0]["repository"] == "repo-alpha"
        assert rows[0]["branch"] == "main"
        assert rows[0]["user_name"] == "user1@example.com"
        assert rows[0]["input_tokens"] == 10000
        assert rows[0]["cache_creation_tokens"] == 2000
        assert rows[0]["total_lines_added"] == 500

        # Row 2: repo-alpha/main/user2
        assert rows[1]["repository"] == "repo-alpha"
        assert rows[1]["branch"] == "main"
        assert rows[1]["user_name"] == "user2@example.com"
        assert rows[1]["input_tokens"] == 5000

        # Row 3: repo-alpha/dev/user1
        assert rows[2]["repository"] == "repo-alpha"
        assert rows[2]["branch"] == "dev"
        assert rows[2]["user_name"] == "user1@example.com"
        assert rows[2]["input_tokens"] == 3000

        # Row 4: repo-beta/main/user3
        assert rows[3]["repository"] == "repo-beta"
        assert rows[3]["branch"] == "main"
        assert rows[3]["user_name"] == "user3@example.com"
        assert rows[3]["input_tokens"] == 15000

    def test_parse_cli_repositories_result_handles_empty_branches_users(self, handler):
        """Verify handling when repositories have empty branches or users."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "repo-empty",
                            "branches": {"buckets": []},
                        },
                        {
                            "key": "repo-with-empty-branch",
                            "branches": {
                                "buckets": [
                                    {
                                        "key": "main",
                                        "users": {"buckets": []},
                                    }
                                ]
                            },
                        },
                    ]
                }
            }
        }

        rows = handler._parse_cli_repositories_result(result)

        # Should return empty rows since no users exist
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_cli_repositories_pagination_accuracy(self, handler, mock_repository):
        """Verify accurate pagination with flattened 3-level nested results.

        Scenario: 2 repos × multiple branches × multiple users = 12 total rows
        Request: page=0, per_page=5
        Expected: Exactly 5 rows returned
        """
        # Arrange: Mock Elasticsearch response with 2 repositories
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "repo-alpha",
                            "branches": {
                                "buckets": [
                                    {
                                        "key": "main",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "alice@example.com",
                                                    "input_tokens": {"value": 1000},
                                                    "cache_creation_tokens": {"value": 100},
                                                    "cache_read_tokens": {"value": 200},
                                                    "output_tokens": {"value": 500},
                                                    "session_duration": {"value": 10000},
                                                    "total_lines_added": {"value": 50},
                                                },
                                                {
                                                    "key": "bob@example.com",
                                                    "input_tokens": {"value": 2000},
                                                    "cache_creation_tokens": {"value": 200},
                                                    "cache_read_tokens": {"value": 400},
                                                    "output_tokens": {"value": 1000},
                                                    "session_duration": {"value": 20000},
                                                    "total_lines_added": {"value": 100},
                                                },
                                                {
                                                    "key": "charlie@example.com",
                                                    "input_tokens": {"value": 1500},
                                                    "cache_creation_tokens": {"value": 150},
                                                    "cache_read_tokens": {"value": 300},
                                                    "output_tokens": {"value": 750},
                                                    "session_duration": {"value": 15000},
                                                    "total_lines_added": {"value": 75},
                                                },
                                            ]
                                        },
                                    },
                                    {
                                        "key": "dev",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "alice@example.com",
                                                    "input_tokens": {"value": 500},
                                                    "cache_creation_tokens": {"value": 50},
                                                    "cache_read_tokens": {"value": 100},
                                                    "output_tokens": {"value": 250},
                                                    "session_duration": {"value": 5000},
                                                    "total_lines_added": {"value": 25},
                                                },
                                                {
                                                    "key": "bob@example.com",
                                                    "input_tokens": {"value": 800},
                                                    "cache_creation_tokens": {"value": 80},
                                                    "cache_read_tokens": {"value": 160},
                                                    "output_tokens": {"value": 400},
                                                    "session_duration": {"value": 8000},
                                                    "total_lines_added": {"value": 40},
                                                },
                                            ]
                                        },
                                    },
                                    {
                                        "key": "feature",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "alice@example.com",
                                                    "input_tokens": {"value": 300},
                                                    "cache_creation_tokens": {"value": 30},
                                                    "cache_read_tokens": {"value": 60},
                                                    "output_tokens": {"value": 150},
                                                    "session_duration": {"value": 3000},
                                                    "total_lines_added": {"value": 15},
                                                }
                                            ]
                                        },
                                    },
                                ]
                            },
                        },
                        {
                            "key": "repo-beta",
                            "branches": {
                                "buckets": [
                                    {
                                        "key": "main",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "alice@example.com",
                                                    "input_tokens": {"value": 3000},
                                                    "cache_creation_tokens": {"value": 300},
                                                    "cache_read_tokens": {"value": 600},
                                                    "output_tokens": {"value": 1500},
                                                    "session_duration": {"value": 30000},
                                                    "total_lines_added": {"value": 150},
                                                },
                                                {
                                                    "key": "bob@example.com",
                                                    "input_tokens": {"value": 2500},
                                                    "cache_creation_tokens": {"value": 250},
                                                    "cache_read_tokens": {"value": 500},
                                                    "output_tokens": {"value": 1250},
                                                    "session_duration": {"value": 25000},
                                                    "total_lines_added": {"value": 125},
                                                },
                                            ]
                                        },
                                    },
                                    {
                                        "key": "dev",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "charlie@example.com",
                                                    "input_tokens": {"value": 1200},
                                                    "cache_creation_tokens": {"value": 120},
                                                    "cache_read_tokens": {"value": 240},
                                                    "output_tokens": {"value": 600},
                                                    "session_duration": {"value": 12000},
                                                    "total_lines_added": {"value": 60},
                                                }
                                            ]
                                        },
                                    },
                                    {
                                        "key": "feature",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "alice@example.com",
                                                    "input_tokens": {"value": 900},
                                                    "cache_creation_tokens": {"value": 90},
                                                    "cache_read_tokens": {"value": 180},
                                                    "output_tokens": {"value": 450},
                                                    "session_duration": {"value": 9000},
                                                    "total_lines_added": {"value": 45},
                                                },
                                                {
                                                    "key": "bob@example.com",
                                                    "input_tokens": {"value": 700},
                                                    "cache_creation_tokens": {"value": 70},
                                                    "cache_read_tokens": {"value": 140},
                                                    "output_tokens": {"value": 350},
                                                    "session_duration": {"value": 7000},
                                                    "total_lines_added": {"value": 35},
                                                },
                                                {
                                                    "key": "charlie@example.com",
                                                    "input_tokens": {"value": 600},
                                                    "cache_creation_tokens": {"value": 60},
                                                    "cache_read_tokens": {"value": 120},
                                                    "output_tokens": {"value": 300},
                                                    "session_duration": {"value": 6000},
                                                    "total_lines_added": {"value": 30},
                                                },
                                            ]
                                        },
                                    },
                                ]
                            },
                        },
                    ]
                }
            }
        }

        # Act: Request page 0 with per_page=5
        result = await handler.get_cli_repositories(time_period="last_30_days", page=0, per_page=5)

        # Assert: Exactly 5 rows returned (not 12)
        assert len(result["data"]["rows"]) == 5
        assert result["pagination"]["total_count"] == 12  # 2 repos × (3+2+1 branches) × users = 12 rows total
        assert result["pagination"]["has_more"] is True
        assert result["pagination"]["page"] == 0
        assert result["pagination"]["per_page"] == 5

        # Verify alphabetical sorting (repository → branch → user_name)
        rows = result["data"]["rows"]
        assert rows[0]["repository"] == "repo-alpha"
        assert rows[0]["branch"] == "dev"
        assert rows[0]["user_name"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_cli_repositories_last_page(self, handler, mock_repository):
        """Verify last page returns partial rows correctly."""
        # Arrange: Mock 9 total rows (3 repos × 3 rows each)
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": f"repo-{i}",
                            "branches": {
                                "buckets": [
                                    {
                                        "key": "main",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": f"user-{j}@example.com",
                                                    "input_tokens": {"value": 1000},
                                                    "cache_creation_tokens": {"value": 100},
                                                    "cache_read_tokens": {"value": 200},
                                                    "output_tokens": {"value": 500},
                                                    "session_duration": {"value": 10000},
                                                    "total_lines_added": {"value": 50},
                                                }
                                                for j in range(3)
                                            ]
                                        },
                                    }
                                ]
                            },
                        }
                        for i in range(3)
                    ]
                }
            }
        }

        # Act: Request page 2 (third page) with per_page=4
        result = await handler.get_cli_repositories(time_period="last_30_days", page=2, per_page=4)

        # Assert: Last page has only 1 row (rows 0-3, 4-7, 8 = 9 total)
        assert len(result["data"]["rows"]) == 1
        assert result["pagination"]["total_count"] == 9
        assert result["pagination"]["has_more"] is False
        assert result["pagination"]["page"] == 2

    @pytest.mark.asyncio
    async def test_cli_repositories_sorting_consistency(self, handler, mock_repository):
        """Verify sorting is consistent across pages (alphabetical by repo→branch→user)."""
        # Arrange: Mock data with multiple combinations
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "repo-zebra",
                            "branches": {
                                "buckets": [
                                    {
                                        "key": "main",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "alice@example.com",
                                                    "input_tokens": {"value": 1000},
                                                    "cache_creation_tokens": {"value": 100},
                                                    "cache_read_tokens": {"value": 200},
                                                    "output_tokens": {"value": 500},
                                                    "session_duration": {"value": 10000},
                                                    "total_lines_added": {"value": 50},
                                                }
                                            ]
                                        },
                                    }
                                ]
                            },
                        },
                        {
                            "key": "repo-alpha",
                            "branches": {
                                "buckets": [
                                    {
                                        "key": "dev",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "bob@example.com",
                                                    "input_tokens": {"value": 2000},
                                                    "cache_creation_tokens": {"value": 200},
                                                    "cache_read_tokens": {"value": 400},
                                                    "output_tokens": {"value": 1000},
                                                    "session_duration": {"value": 20000},
                                                    "total_lines_added": {"value": 100},
                                                }
                                            ]
                                        },
                                    },
                                    {
                                        "key": "main",
                                        "users": {
                                            "buckets": [
                                                {
                                                    "key": "alice@example.com",
                                                    "input_tokens": {"value": 1500},
                                                    "cache_creation_tokens": {"value": 150},
                                                    "cache_read_tokens": {"value": 300},
                                                    "output_tokens": {"value": 750},
                                                    "session_duration": {"value": 15000},
                                                    "total_lines_added": {"value": 75},
                                                }
                                            ]
                                        },
                                    },
                                ]
                            },
                        },
                    ]
                }
            }
        }

        # Act
        result = await handler.get_cli_repositories(time_period="last_30_days", page=0, per_page=10)

        # Assert: Alphabetical ordering applied (repo → branch → user)
        rows = result["data"]["rows"]
        assert len(rows) == 3

        # Sorted: repo-alpha/dev/bob, repo-alpha/main/alice, repo-zebra/main/alice
        assert rows[0]["repository"] == "repo-alpha"
        assert rows[0]["branch"] == "dev"
        assert rows[0]["user_name"] == "bob@example.com"

        assert rows[1]["repository"] == "repo-alpha"
        assert rows[1]["branch"] == "main"
        assert rows[1]["user_name"] == "alice@example.com"

        assert rows[2]["repository"] == "repo-zebra"
        assert rows[2]["branch"] == "main"
        assert rows[2]["user_name"] == "alice@example.com"


class TestCLILLMs:
    """Tests for CLI LLMs analytics."""

    def test_build_cli_llms_aggregation(self, handler):
        """Verify CLI LLMs aggregation groups by llm_model field."""
        query = {"bool": {"filter": []}}
        agg_body = handler._build_cli_llms_aggregation(query, fetch_size=20)

        assert agg_body["aggs"]["paginated_results"]["terms"]["field"] == "attributes.llm_model.keyword"

    def test_parse_cli_llms_result(self, handler):
        """Verify CLI LLMs result parsing with model_name and total_requests fields."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {"key": "claude-3-5-sonnet-20241022", "doc_count": 500},
                        {"key": "gpt-4o", "doc_count": 300},
                    ]
                }
            }
        }

        rows = handler._parse_cli_llms_result(result)

        assert len(rows) == 2
        assert rows[0]["model_name"] == "claude-3-5-sonnet-20241022"
        assert rows[0]["total_requests"] == 500
        assert rows[1]["model_name"] == "gpt-4o"
        assert rows[1]["total_requests"] == 300


class TestCLITopPerformers:
    """Tests for CLI top performers analytics."""

    def test_build_cli_top_performers_aggregation(self, handler):
        """Verify top performers aggregation orders by total_lines_added."""
        query = {"bool": {"filter": []}}
        agg_body = handler._build_cli_top_performers_aggregation(query, fetch_size=20)

        # Verify ordering by total_lines_added
        assert agg_body["aggs"]["paginated_results"]["terms"]["order"] == {"total_lines_added": "desc"}
        # Verify sub-aggregations
        assert "total_lines_added" in agg_body["aggs"]["paginated_results"]["aggs"]
        assert "last_project" in agg_body["aggs"]["paginated_results"]["aggs"]

    def test_parse_cli_top_performers_result_with_top_metrics(self, handler):
        """Verify top performers result parsing extracts lines_added and last_project."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "doc_count": 100,
                            "total_lines_added": {"value": 5000},
                            "last_project": {
                                "top_project": {"top": [{"metrics": {"attributes.project.keyword": "project-alpha"}}]}
                            },
                        },
                        {
                            "key": "user2@example.com",
                            "doc_count": 75,
                            "total_lines_added": {"value": 3000},
                            "last_project": {"top_project": {"top": []}},
                        },
                    ]
                }
            }
        }

        rows = handler._parse_cli_top_performers_result(result)

        assert len(rows) == 2
        assert rows[0]["user_name"] == "user1@example.com"
        assert rows[0]["total_lines_added"] == 5000
        assert rows[0]["last_project"] == "project-alpha"
        assert rows[1]["user_name"] == "user2@example.com"
        assert rows[1]["total_lines_added"] == 3000
        assert rows[1]["last_project"] is None


class TestCLITopVersions:
    """Tests for CLI top versions analytics."""

    def test_build_cli_top_versions_aggregation(self, handler):
        """Verify top versions aggregation groups by codemie_cli field."""
        query = {"bool": {"filter": []}}
        agg_body = handler._build_cli_top_versions_aggregation(query, fetch_size=20)

        assert agg_body["aggs"]["paginated_results"]["terms"]["field"] == "attributes.codemie_cli.keyword"

    def test_parse_cli_top_versions_result(self, handler):
        """Verify top versions result parsing with version and usage_count fields."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {"key": "v1.2.3", "doc_count": 1500},
                        {"key": "v1.2.2", "doc_count": 800},
                        {"key": "v1.1.0", "doc_count": 200},
                    ]
                }
            }
        }

        rows = handler._parse_cli_top_versions_result(result)

        assert len(rows) == 3
        assert rows[0]["version"] == "v1.2.3"
        assert rows[0]["usage_count"] == 1500
        assert rows[1]["version"] == "v1.2.2"
        assert rows[1]["usage_count"] == 800


class TestCLITopProxyEndpoints:
    """Tests for CLI top proxy endpoints analytics."""

    def test_build_cli_top_proxy_endpoints_aggregation(self, handler):
        """Verify top proxy endpoints aggregation groups by endpoint field."""
        query = {"bool": {"filter": []}}
        agg_body = handler._build_cli_top_proxy_endpoints_aggregation(query, fetch_size=20)

        assert agg_body["aggs"]["paginated_results"]["terms"]["field"] == "attributes.endpoint.keyword"

    def test_parse_cli_top_proxy_endpoints_result(self, handler):
        """Verify top proxy endpoints result parsing with endpoint and request_count fields."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {"key": "/v1/chat/completions", "doc_count": 5000},
                        {"key": "/v1/embeddings", "doc_count": 2000},
                        {"key": "/v1/models", "doc_count": 500},
                    ]
                }
            }
        }

        rows = handler._parse_cli_top_proxy_endpoints_result(result)

        assert len(rows) == 3
        assert rows[0]["endpoint"] == "/v1/chat/completions"
        assert rows[0]["request_count"] == 5000
        assert rows[1]["endpoint"] == "/v1/embeddings"
        assert rows[1]["request_count"] == 2000


class TestCLIToolsUsage:
    """Tests for CLI tools usage analytics."""

    def test_build_cli_tools_usage_aggregation(self, handler):
        """Verify CLI tools usage aggregation groups by tool_names field."""
        query = {"bool": {"filter": []}}
        agg_body = handler._build_cli_tools_usage_aggregation(query, fetch_size=20)

        assert agg_body["query"] == query
        assert agg_body["size"] == 0
        assert "paginated_results" in agg_body["aggs"]
        assert "terms" in agg_body["aggs"]["paginated_results"]
        assert agg_body["aggs"]["paginated_results"]["terms"]["field"] == "attributes.tool_names.keyword"

    def test_parse_cli_tools_usage_result(self, handler):
        """Verify CLI tools usage result parsing with tool_name and session_count fields."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {"key": "Read", "doc_count": 1500},
                        {"key": "Write", "doc_count": 1200},
                        {"key": "Edit", "doc_count": 800},
                        {"key": "Bash", "doc_count": 600},
                    ]
                }
            }
        }

        rows = handler._parse_cli_tools_usage_result(result)

        assert len(rows) == 4
        assert rows[0]["tool_name"] == "Read"
        assert rows[0]["session_count"] == 1500
        assert rows[1]["tool_name"] == "Write"
        assert rows[1]["session_count"] == 1200
        assert rows[2]["tool_name"] == "Edit"
        assert rows[2]["session_count"] == 800
        assert rows[3]["tool_name"] == "Bash"
        assert rows[3]["session_count"] == 600


class TestCLIInsightsHelpers:
    """Tests for CLI Insights helper transformations."""

    @pytest.mark.asyncio
    async def test_get_cli_time_pattern_rows_uses_sunday_first_weekday_order(self, handler, mock_repository):
        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "hourly_buckets": {
                        "buckets": [
                            {"key_as_string": "2026-03-22T10:00:00.000Z", "doc_count": 5},
                            {"key_as_string": "2026-03-23T10:00:00.000Z", "doc_count": 7},
                        ]
                    }
                }
            }
        )

        rows = await handler._get_cli_time_pattern_rows("last_7_days", None, None, None, None)

        assert rows["weekday"]["Sun"]["weekday_index"] == 0
        assert rows["weekday"]["Sun"]["activity_count"] == 5
        assert rows["weekday"]["Mon"]["weekday_index"] == 1
        assert rows["weekday"]["Mon"]["activity_count"] == 7

    @pytest.mark.asyncio
    async def test_get_cli_insights_project_rows_skips_blank_project_name(self, handler, mock_repository):
        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "projects": {
                        "buckets": [
                            {
                                "key": "",
                                "cost_bucket": {"total_cost": {"value": 9334.5}},
                                "repositories": {"buckets": {"buckets": []}},
                                "branches": {"buckets": {"buckets": []}},
                            },
                            {
                                "key": "epm-cdme",
                                "cost_bucket": {"total_cost": {"value": 120.0}},
                                "repositories": {"buckets": {"buckets": [{"key": "JnJ/payment-service/backend"}]}},
                                "branches": {"buckets": {"buckets": [{"key": "feature/ABC-123-auth"}]}},
                            },
                        ]
                    }
                }
            }
        )

        rows = await handler._get_cli_insights_project_rows("last_30_days", None, None, None, None)

        assert len(rows) == 1
        assert rows[0]["project_name"] == "epm-cdme"
        assert rows[0]["total_cost"] == 120.0

    @pytest.mark.asyncio
    async def test_get_cli_insights_top_spenders(self, handler):
        handler._get_cli_insights_user_rows = AsyncMock(
            return_value=[
                {
                    "user_id": "u-2",
                    "user_name": "Bob",
                    "user_email": "bob@example.com",
                    "classification": "learning",
                    "total_sessions": 3,
                    "net_lines": 12,
                    "total_cost": 40.0,
                    "total_lines_added": 20,
                },
                {
                    "user_id": "u-1",
                    "user_name": "Alice",
                    "user_email": "alice@example.com",
                    "classification": "production",
                    "total_sessions": 8,
                    "net_lines": 140,
                    "total_cost": 120.0,
                    "total_lines_added": 200,
                },
            ]
        )

        response = await handler.get_cli_insights_top_spenders(time_period="last_30_days")

        assert [row["rank"] for row in response["data"]["rows"]] == [1, 2]
        assert response["data"]["rows"][0]["user_name"] == "Alice"
        assert response["data"]["rows"][0]["classification"] == "production"
        assert response["data"]["rows"][0]["total_sessions"] == 8
        assert response["data"]["rows"][0]["net_lines"] == 140
        assert response["data"]["rows"][0]["total_cost"] == 120.0
        assert response["data"]["columns"][0]["id"] == "rank"
        assert response["data"]["columns"][1]["id"] == "user_name"
        assert response["data"]["columns"][5]["id"] == "total_cost"

    @pytest.mark.asyncio
    async def test_get_cli_insights_user_detail(self, handler, mock_repository):
        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "user_name": {"top": [{"metrics": {"attributes.user_name.keyword": "Pavlo Chaikivskyi"}}]},
                    "user_email": {
                        "top": [{"metrics": {"attributes.user_email.keyword": "pavlo_chaikivskyi@epam.com"}}]
                    },
                    "tool_usage": {
                        "total_prompts": {"value": 18097},
                        "total_commands": {"value": 520},
                        "total_lines_added": {"value": 9349},
                        "total_lines_removed": {"value": 2530},
                        "files_created": {"value": 62},
                        "files_modified": {"value": 563},
                        "files_deleted": {"value": 0},
                        "unique_repositories": {"value": 12},
                        "projects": {"buckets": [{"key": "epm-cdme"}]},
                        "branches": {"buckets": [{"key": "main"}, {"key": "aws/pc-codemie"}]},
                    },
                    "session_usage": {
                        "total_sessions": {"value": 52},
                        "active_days": {"buckets": [{"key_as_string": "2026-03-01"}, {"key_as_string": "2026-03-02"}]},
                    },
                    "completed_sessions": {"avg_duration_ms": {"value": 9480000}},
                    "proxy_usage": {
                        "total_cost": {"value": 1210.894},
                        "models": {"buckets": [{"key": "claude-sonnet-4-6", "doc_count": 3583}]},
                    },
                    "repositories": {
                        "buckets": [
                            {
                                "key": "infra/codemie-terraform-gcp-platform",
                                "usage": {
                                    "lines_added": {"value": 5685},
                                    "lines_removed": {"value": 788},
                                    "branches": {"buckets": [{"key": "epmcdme-9952"}, {"key": "main"}]},
                                    "projects": {"buckets": [{"key": "epm-cdme"}]},
                                },
                                "sessions": {"count": {"value": 23}},
                                "proxy": {"total_cost": {"value": 74.87}},
                            },
                            {
                                "key": "home/pavlo_chaikivskyi",
                                "usage": {
                                    "lines_added": {"value": 152},
                                    "lines_removed": {"value": 24},
                                    "branches": {"buckets": [{"key": "HEAD"}]},
                                    "projects": {"buckets": [{"key": "epm-cdme"}]},
                                },
                                "sessions": {"count": {"value": 3}},
                                "proxy": {"total_cost": {"value": 11.81}},
                            },
                        ]
                    },
                }
            }
        )
        mock_repository.execute_search_query = AsyncMock(
            return_value={
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "attributes": {
                                    "tool_names": ["Bash", "Edit", "Read"],
                                    "tool_counts": [12, 5, 8],
                                }
                            }
                        },
                        {
                            "_source": {
                                "attributes": {
                                    "tool_names": ["Bash", "Write"],
                                    "tool_counts": [3, 2],
                                }
                            }
                        },
                    ]
                }
            }
        )

        response = await handler.get_cli_insights_user_detail(
            user_name="Pavlo Chaikivskyi",
            time_period="last_30_days",
        )

        assert response["data"]["user_name"] == "Pavlo Chaikivskyi"
        assert response["data"]["user_email"] == "pavlo_chaikivskyi@epam.com"
        assert response["data"]["total_sessions"] == 52
        assert response["data"]["total_prompts"] == 18097
        assert response["data"]["net_lines"] == 6819
        assert response["data"]["files_modified"] == 563
        assert response["data"]["unique_projects"] == ["epm-cdme"]
        assert response["data"]["branches_used"] == ["main", "aws/pc-codemie"]
        assert set(response["data"]) >= {
            "user_name",
            "user_email",
            "classification",
            "primary_category",
            "total_sessions",
            "total_commands",
            "unique_repositories",
            "total_cost",
            "total_prompts",
            "net_lines",
            "files_created",
            "files_deleted",
            "files_modified",
            "active_days",
            "avg_session_duration_min",
            "prompts_per_session",
            "est_monthly_20d",
            "is_multi_category",
            "category_diversity_score",
            "rule_reasons",
            "unique_projects",
            "branches_used",
            "category_breakdown",
            "repository_classifications",
            "tools",
            "models",
            "tool_profile",
            "key_metrics",
            "tools_chart",
            "models_chart",
            "workflow_intent_metrics",
            "classification_metrics",
            "category_breakdown_chart",
            "repositories_table",
        }
        assert set(response["data"]["repository_classifications"][0]) == {
            "repository",
            "sessions",
            "cost",
            "classification",
            "net_lines",
            "branches",
        }
        assert response["data"]["tools"][0] == {"tool_name": "Bash", "usage_count": 15}
        assert response["data"]["models"][0] == {"model_name": "claude-sonnet-4-6", "count": 3583}
        assert response["data"]["tool_profile"]["primary_intent_label"]
        assert response["data"]["category_breakdown"]
        assert response["data"]["key_metrics"]["data"]["metrics"][0]["label"] == "Total Cost"
        assert response["data"]["tools_chart"]["data"]["columns"][0]["id"] == "tool_name"
        assert response["data"]["models_chart"]["data"]["columns"][0]["id"] == "model_name"
        assert response["data"]["workflow_intent_metrics"]["data"]["metrics"][0]["id"] == "primary_intent"
        assert response["data"]["classification_metrics"]["data"]["metrics"][0]["id"] == "primary_category"
        assert response["data"]["category_breakdown_chart"]["data"]["columns"][1]["id"] == "percentage"
        assert response["data"]["repositories_table"]["data"]["columns"][0]["id"] == "repository"
        assert response["data"]["repositories_table"]["data"]["rows"][0]["branches"] == ["epmcdme-9952", "main"]

    def test_build_cli_insights_user_aggregation_uses_session_total_for_unique_sessions(self, handler):
        aggregation = handler._build_cli_insights_user_aggregation({"bool": {"filter": []}})
        usage_filter = aggregation["aggs"]["users"]["aggs"]["projects"]["filter"]
        session_filter = aggregation["aggs"]["users"]["aggs"]["total_sessions"]["filter"]
        lines_added_filter = aggregation["aggs"]["users"]["aggs"]["total_lines_added"]["filter"]
        lines_removed_filter = aggregation["aggs"]["users"]["aggs"]["total_lines_removed"]["filter"]
        cost_filter = aggregation["aggs"]["users"]["aggs"]["cost_bucket"]["filter"]

        assert usage_filter == {"term": {"metric_name.keyword": "codemie_cli_tool_usage_total"}}
        assert session_filter == {"term": {"metric_name.keyword": "codemie_cli_session_total"}}
        assert lines_added_filter == {"term": {"metric_name.keyword": "codemie_cli_tool_usage_total"}}
        assert lines_removed_filter == {"term": {"metric_name.keyword": "codemie_cli_tool_usage_total"}}
        assert cost_filter == {"term": {"metric_name.keyword": "codemie_litellm_proxy_usage"}}

    def test_build_cli_insights_project_aggregation_uses_cli_tool_usage_metric(self, handler):
        aggregation = handler._build_cli_insights_project_aggregation({"bool": {"filter": []}})
        usage_filter = aggregation["aggs"]["projects"]["aggs"]["repositories"]["filter"]
        cost_filter = aggregation["aggs"]["projects"]["aggs"]["cost_bucket"]["filter"]

        assert usage_filter == {"term": {"metric_name.keyword": "codemie_cli_tool_usage_total"}}
        assert aggregation["aggs"]["projects"]["aggs"]["branches"]["filter"] == usage_filter
        assert cost_filter == {"term": {"metric_name.keyword": "codemie_litellm_proxy_usage"}}

    def test_build_cli_insights_user_detail_aggregation_uses_current_cli_metrics(self, handler):
        aggregation = handler._build_cli_insights_user_detail_aggregation({"bool": {"filter": []}})

        assert aggregation["aggs"]["tool_usage"]["filter"] == {
            "term": {"metric_name.keyword": "codemie_cli_tool_usage_total"}
        }
        assert aggregation["aggs"]["session_usage"]["filter"] == {
            "term": {"metric_name.keyword": "codemie_cli_session_total"}
        }
        assert aggregation["aggs"]["proxy_usage"]["filter"] == {
            "term": {"metric_name.keyword": "codemie_litellm_proxy_usage"}
        }
        status_filters = aggregation["aggs"]["completed_sessions"]["filter"]["bool"]["filter"]
        assert {"term": {"metric_name.keyword": "codemie_cli_session_total"}} in status_filters
        assert {"terms": {"attributes.status.keyword": ["completed", "failed", "interrupted"]}} in status_filters

    def test_extract_cli_tool_counts_supports_dict_tool_counts(self, handler):
        result = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "attributes": {
                                "tool_names": ["Bash", "Read", "Edit"],
                                "tool_counts": {"Bash": 12, "Read": 7, "Edit": 3},
                            }
                        }
                    }
                ]
            }
        }

        tool_counts = handler._extract_cli_tool_counts(result)

        assert tool_counts == [("Bash", 12), ("Read", 7), ("Edit", 3)]

    def test_classify_cli_entity_learning(self, handler):
        classification, confidence = handler._classify_cli_entity(
            repositories=["tutorials/react-course"],
            branches=["main"],
            project_name="demo@epam.com",
            total_cost=2.0,
        )

        assert classification in {"learning", "pet_project", "experimental"}
        assert 0 <= confidence <= 1

    def test_classify_cli_entity_production(self, handler):
        classification, confidence = handler._classify_cli_entity(
            repositories=["JnJ/payment-service/backend"],
            branches=["feature/ABC-123-auth"],
            project_name="team-project",
            total_cost=200.0,
        )

        assert classification == "production"
        assert confidence > 0
