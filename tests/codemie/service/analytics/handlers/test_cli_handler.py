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

from unittest.mock import MagicMock

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

    def test_build_cli_summary_aggregation_structure(self, handler):
        """Verify CLI summary aggregation has metrics with correct filters.

        Note: Cost metrics (total_cost, cache_read_cost, cache_creation_cost) are now
        handled separately with cutoff date adjustments, so they're not in this aggregation.
        """
        query = {"bool": {"filter": []}}
        agg_body = handler._build_cli_summary_aggregation(query)

        assert agg_body["query"] == query
        assert agg_body["size"] == 0

        # Verify all metrics exist (excluding cost metrics which are handled separately)
        expected_metrics = [
            "unique_users",
            "unique_sessions",
            "unique_repos",
            "input_tokens",
            "output_tokens",
            "cached_tokens_read",
            "cached_creation_tokens",  # Added this instead of cache_creation_cost
            "cli_invoked",
            "cli_avg_session",
            "cli_max_session_duration",
            "proxy_requests_count",
            "proxy_errors_count",
            "proxy_failed_calls",
            "total_lines_added",
            "total_lines_removed",
            "total_created_files",
            "total_prompts",
            "total_deleted_lines",
            "total_deleted_files",
            "total_modified_files",
        ]
        for metric in expected_metrics:
            assert metric in agg_body["aggs"], f"Missing metric: {metric}"

        # Verify metric filters (now using 'terms' for dual-metric support)
        cli_filter = agg_body["aggs"]["unique_users"]["filter"]["bool"]["filter"][0]
        assert "terms" in cli_filter
        assert "codemie_cli_tool_usage_total" in cli_filter["terms"]["metric_name.keyword"]
        assert "codemie_cli_usage_total" in cli_filter["terms"]["metric_name.keyword"]
        assert (
            agg_body["aggs"]["proxy_requests_count"]["filter"]["bool"]["filter"][0]["term"]["metric_name.keyword"]
            == "llm_proxy_requests_total"
        )
        assert (
            agg_body["aggs"]["proxy_errors_count"]["filter"]["bool"]["filter"][0]["term"]["metric_name.keyword"]
            == "llm_proxy_errors_total"
        )

    def test_parse_cli_summary_result_all_17_metrics(self, handler):
        """Verify all 18 metrics are parsed correctly.

        Note: Cost metrics (total_cost, cache_read_cost, cache_creation_cost) are now
        handled separately with cutoff date adjustments, so they're not in this result.
        """
        result = {
            "aggregations": {
                "unique_users": {"count": {"value": 25}},
                "unique_sessions": {"count": {"value": 50}},
                "unique_repos": {"count": {"value": 10}},
                "input_tokens": {"total": {"value": 100000}},
                "output_tokens": {"total": {"value": 50000}},
                "cached_tokens_read": {"total": {"value": 20000}},
                "cached_creation_tokens": {"total": {"value": 10000}},
                # Cost metrics removed - handled separately with cutoff adjustments
                "cli_invoked": {"count": {"value": 500}},
                "cli_avg_session": {"avg_duration": {"value": 45000.5}},
                "cli_max_session_duration": {"max_duration": {"value": 120000}},
                "proxy_requests_count": {"doc_count": 1000},
                "proxy_errors_count": {"doc_count": 50},
                "proxy_failed_calls": {"count": {"value": 50}},
                "total_lines_added": {"total": {"value": 5000}},
                "total_lines_removed": {"total": {"value": 2000}},
                "total_created_files": {"total": {"value": 30}},
                "total_prompts": {"total": {"value": 200}},
                "total_deleted_lines": {"total": {"value": 2000}},
                "total_deleted_files": {"total": {"value": 5}},
                "total_modified_files": {"total": {"value": 80}},
            }
        }

        metrics = handler._parse_cli_summary_result(result)

        assert len(metrics) == 18  # Changed from 21 to 18 (removed 3 cost metrics)
        # Verify key metrics (excluding cost metrics which are now handled separately)
        assert next(m for m in metrics if m["id"] == "unique_users")["value"] == 25
        assert next(m for m in metrics if m["id"] == "input_tokens")["value"] == 100000
        assert next(m for m in metrics if m["id"] == "cli_avg_session")["value"] == 45000
        assert next(m for m in metrics if m["id"] == "total_created_files")["value"] == 30

    def test_parse_cli_summary_proxy_success_rate_calculation(self, handler):
        """Verify proxy success rate is calculated as (requests - errors) / requests * 100."""
        result = {
            "aggregations": {
                "unique_users": {"count": {"value": 0}},
                "unique_sessions": {"count": {"value": 0}},
                "unique_repos": {"count": {"value": 0}},
                "input_tokens": {"total": {"value": 0}},
                "output_tokens": {"total": {"value": 0}},
                "cached_tokens_read": {"total": {"value": 0}},
                "total_cost": {"total": {"value": 0}},
                "cache_read_cost": {"total": {"value": 0}},
                "cache_creation_cost": {"total": {"value": 0}},
                "cli_invoked": {"count": {"value": 0}},
                "cli_avg_session": {"avg_duration": {}},
                "cli_max_session_duration": {"max_duration": {}},
                "proxy_requests_count": {"doc_count": 1000},
                "proxy_errors_count": {"doc_count": 50},
                "proxy_failed_calls": {"count": {"value": 0}},
                "total_lines_added": {"total": {"value": 0}},
                "total_lines_removed": {"total": {"value": 0}},
                "total_created_files": {"total": {"value": 0}},
                "total_prompts": {"total": {"value": 0}},
                "total_deleted_lines": {"total": {"value": 0}},
                "total_deleted_files": {"total": {"value": 0}},
                "total_modified_files": {"total": {"value": 0}},
            }
        }

        metrics = handler._parse_cli_summary_result(result)

        proxy_success_rate = next(m for m in metrics if m["id"] == "proxy_success_rate")
        # (1000 - 50) / 1000 * 100 = 95.0%
        assert proxy_success_rate["value"] == 95.0

    def test_parse_cli_summary_proxy_success_rate_zero_requests(self, handler):
        """Verify proxy success rate is N/A when no requests exist (avoid division by zero)."""
        result = {
            "aggregations": {
                "unique_users": {"count": {"value": 0}},
                "unique_sessions": {"count": {"value": 0}},
                "unique_repos": {"count": {"value": 0}},
                "input_tokens": {"total": {"value": 0}},
                "output_tokens": {"total": {"value": 0}},
                "cached_tokens_read": {"total": {"value": 0}},
                "total_cost": {"total": {"value": 0}},
                "cache_read_cost": {"total": {"value": 0}},
                "cache_creation_cost": {"total": {"value": 0}},
                "cli_invoked": {"count": {"value": 0}},
                "cli_avg_session": {"avg_duration": {}},
                "cli_max_session_duration": {"max_duration": {}},
                "proxy_requests_count": {"doc_count": 0},
                "proxy_errors_count": {"doc_count": 0},
                "proxy_failed_calls": {"count": {"value": 0}},
                "total_lines_added": {"total": {"value": 0}},
                "total_lines_removed": {"total": {"value": 0}},
                "total_created_files": {"total": {"value": 0}},
                "total_prompts": {"total": {"value": 0}},
                "total_deleted_lines": {"total": {"value": 0}},
                "total_deleted_files": {"total": {"value": 0}},
                "total_modified_files": {"total": {"value": 0}},
            }
        }

        metrics = handler._parse_cli_summary_result(result)

        proxy_success_rate = next(m for m in metrics if m["id"] == "proxy_success_rate")
        assert proxy_success_rate["value"] == "N/A"

    def test_parse_cli_summary_net_new_lines_calculation(self, handler):
        """Verify net new lines is calculated as lines_added - lines_removed."""
        result = {
            "aggregations": {
                "unique_users": {"count": {"value": 0}},
                "unique_sessions": {"count": {"value": 0}},
                "unique_repos": {"count": {"value": 0}},
                "input_tokens": {"total": {"value": 0}},
                "output_tokens": {"total": {"value": 0}},
                "cached_tokens_read": {"total": {"value": 0}},
                "total_cost": {"total": {"value": 0}},
                "cache_read_cost": {"total": {"value": 0}},
                "cache_creation_cost": {"total": {"value": 0}},
                "cli_invoked": {"count": {"value": 0}},
                "cli_avg_session": {"avg_duration": {}},
                "cli_max_session_duration": {"max_duration": {}},
                "proxy_requests_count": {"doc_count": 0},
                "proxy_errors_count": {"doc_count": 0},
                "proxy_failed_calls": {"count": {"value": 0}},
                "total_lines_added": {"total": {"value": 5000}},
                "total_lines_removed": {"total": {"value": 2000}},
                "total_created_files": {"total": {"value": 0}},
                "total_prompts": {"total": {"value": 0}},
                "total_deleted_lines": {"total": {"value": 2000}},
                "total_deleted_files": {"total": {"value": 0}},
                "total_modified_files": {"total": {"value": 0}},
            }
        }

        metrics = handler._parse_cli_summary_result(result)

        net_new_lines = next(m for m in metrics if m["id"] == "net_new_lines")
        # 5000 - 2000 = 3000
        assert net_new_lines["value"] == 3000


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
