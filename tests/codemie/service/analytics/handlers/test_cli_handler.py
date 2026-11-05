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

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.cli.classification_engine import CLIClassificationEngine
from codemie.service.analytics.handlers.cli.insights_handler import CLIInsightsHandler
from codemie.service.analytics.handlers.cli_handler import CLIHandler
from codemie.service.analytics.time_parser import TimeParser
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.rendering import RenderedSpend, combine_rendered_spend
from codemie.service.spend.spend_group import SpendGroup


def _exact(figure: float) -> RenderedSpend:
    """A whole figure: every cost-bearing document in the window settled."""
    return RenderedSpend(figure=figure, unavailable=False, partial=False, missing_amount=None)


def _partial(figure: float) -> RenderedSpend:
    """A subtotal: the settled documents total this, and an unknown amount is unsettled."""
    return RenderedSpend(figure=figure, unavailable=False, partial=True, missing_amount=None)


def _absent() -> RenderedSpend:
    """No figure at all: nothing in the window settled a cost, which is not the same as zero."""
    return RenderedSpend(figure=None, unavailable=True, partial=False, missing_amount=None)


def _patched_cli_cost(spend: RenderedSpend) -> AsyncMock:
    """Stand in for the ledger read behind the summary's cost tile and return the stub to assert on."""
    return patch(
        "codemie.service.analytics.handlers.cli.handler.dashboard_group_spend",
        new=AsyncMock(return_value=spend),
    )


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin = False
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


def _repo_user_bucket(user: str, tokens: tuple[int, int, int, int], session_docs: int, duration: int = 0) -> dict:
    """ES user bucket for cli-repositories; tokens = (input, output, cache_creation, cache_read)."""
    input_tokens, output_tokens, cache_creation, cache_read = tokens
    return {
        "key": user,
        "token_data": {
            "input_tokens": {"value": input_tokens},
            "output_tokens": {"value": output_tokens},
            "cache_creation_tokens": {"value": cache_creation},
            "cache_read_tokens": {"value": cache_read},
        },
        "session_data": {
            "doc_count": session_docs,
            "session_duration": {"value": duration},
            "total_lines_added": {"value": 0},
        },
    }


def _repo_result(repository: str, branches: dict[str, list[dict]]) -> dict:
    return {
        "aggregations": {
            "paginated_results": {
                "buckets": [
                    {
                        "key": repository,
                        "branches": {
                            "buckets": [
                                {"key": branch, "users": {"buckets": users}} for branch, users in branches.items()
                            ]
                        },
                    }
                ]
            }
        }
    }


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

        with _patched_cli_cost(_exact(123.45)):
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

    @pytest.mark.asyncio
    async def test_get_cli_summary_cli_cost_reads_the_cli_group_for_the_requested_range(self, handler):
        """cli_cost is the spend port's CLI group over the requested range (no cutoff clamping), as the caller may see it."""
        handler._pipeline.execute_summary_query = AsyncMock(
            side_effect=[{"data": {"metrics": []}}, {"data": {"metrics": []}}, {"data": {"metrics": []}}]
        )
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 31, tzinfo=timezone.utc)

        with _patched_cli_cost(_exact(42.13)) as ledger_read:
            response = await handler.get_cli_summary(start_date=start, end_date=end, users=["ada"], projects=["alpha"])

        cli_cost_metric = next(m for m in response["data"]["metrics"] if m["id"] == "cli_cost")
        assert cli_cost_metric["value"] == 42.13
        expected_start, expected_end = TimeParser.parse(None, start, end)
        ledger_read.assert_awaited_once_with(
            TimeRange(expected_start, expected_end),
            handler._pipeline.access_scope,
            ["ada"],
            ["alpha"],
            SpendGroup.CLI,
        )

    @staticmethod
    def _cli_cost_card(response: dict) -> dict:
        return next(metric for metric in response["data"]["metrics"] if metric["id"] == "cli_cost")

    @pytest.mark.asyncio
    async def test_cli_cost_is_unavailable_when_the_ledger_has_no_figure(self, handler):
        """No figure from the ledger is no figure on the card. A zero here would claim the window cost nothing."""
        handler._pipeline.execute_summary_query = AsyncMock(
            side_effect=[{"data": {"metrics": []}}, {"data": {"metrics": []}}, {"data": {"metrics": []}}]
        )

        with _patched_cli_cost(_absent()):
            card = self._cli_cost_card(await handler.get_cli_summary(time_period="last_30_days"))

        assert card["value"] is None
        assert card["spend_unavailable"] is True
        assert card["spend_partial"] is False

    @pytest.mark.asyncio
    async def test_cli_cost_is_partial_when_the_ledger_figure_is_short(self, handler):
        """A short ledger figure is published as a subtotal, with the figure the ledger did produce."""
        handler._pipeline.execute_summary_query = AsyncMock(
            side_effect=[{"data": {"metrics": []}}, {"data": {"metrics": []}}, {"data": {"metrics": []}}]
        )

        with _patched_cli_cost(_partial(42.13)):
            card = self._cli_cost_card(await handler.get_cli_summary(time_period="last_30_days"))

        assert card["value"] == 42.13
        assert card["spend_partial"] is True
        assert card["spend_unavailable"] is False
        # The size of the hole is unknown - a state, not a gap, and never a row count standing in for money.
        assert card["spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_cli_cost_is_exact_when_the_ledger_figure_is_whole(self, handler):
        """The only state a currency card may render bare, including a measured zero."""
        handler._pipeline.execute_summary_query = AsyncMock(
            side_effect=[{"data": {"metrics": []}}, {"data": {"metrics": []}}, {"data": {"metrics": []}}]
        )

        with _patched_cli_cost(_exact(42.13)):
            card = self._cli_cost_card(await handler.get_cli_summary(time_period="last_30_days"))

        assert card["value"] == 42.13
        assert (card["spend_unavailable"], card["spend_partial"], card["spend_missing_amount"]) == (False, False, None)

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
        assert user_agg["terms"]["field"] == "attributes.user_id.keyword"
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
                                                        "doc_count": 1,
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
                                                        "doc_count": 1,
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
                                                        "doc_count": 1,
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
                                                        "doc_count": 1,
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

    def test_parse_cli_repositories_keeps_unbranched_tokens_as_own_row(self, handler):
        """EPMCDME-16530: unborn-HEAD repo — proxy tokens have branch "", the session has "master"."""
        result = _repo_result(
            "qa-h1/tok-nocommit",
            {
                "": [_repo_user_bucket("u1", (2, 4, 28222, 0), session_docs=0)],
                "master": [_repo_user_bucket("u1", (0, 0, 0, 0), session_docs=1, duration=9947)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert rows == [
            {
                "repository": "qa-h1/tok-nocommit",
                "branch": "master",
                "user_name": "u1",
                "input_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 0,
                "session_duration": 9947,
                "total_lines_added": 0,
            },
            {
                "repository": "qa-h1/tok-nocommit",
                "branch": "",
                "user_name": "u1",
                "input_tokens": 2,
                "cache_creation_tokens": 28222,
                "cache_read_tokens": 0,
                "output_tokens": 4,
                "session_duration": 0,
                "total_lines_added": 0,
            },
        ]

    def test_parse_cli_repositories_does_not_inflate_session_row_with_unbranched_tokens(self, handler):
        """Repository/user equality does not prove ownership: a session row keeps only its own tokens."""
        result = _repo_result(
            "org/repo",
            {
                "main": [_repo_user_bucket("u1", (5, 0, 0, 0), session_docs=1, duration=10)],
                "": [_repo_user_bucket("u1", (100, 0, 0, 0), session_docs=0)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert [(r["branch"], r["input_tokens"]) for r in rows] == [("main", 5), ("", 100)]

    def test_parse_cli_repositories_keeps_unbranched_row_next_to_several_session_rows(self, handler):
        """Two session branches for the user: the branch-less usage stays on its own row, not guessed."""
        result = _repo_result(
            "org/repo",
            {
                "main": [_repo_user_bucket("u1", (5, 5, 0, 0), session_docs=1, duration=10)],
                "dev": [_repo_user_bucket("u1", (7, 7, 0, 0), session_docs=1, duration=20)],
                "": [_repo_user_bucket("u1", (1, 2, 3, 4), session_docs=0)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert [(r["branch"], r["input_tokens"]) for r in rows] == [("main", 5), ("dev", 7), ("", 1)]
        unbranched = rows[2]
        assert (unbranched["session_duration"], unbranched["total_lines_added"]) == (0, 0)
        assert (unbranched["output_tokens"], unbranched["cache_creation_tokens"], unbranched["cache_read_tokens"]) == (
            2,
            3,
            4,
        )

    def test_parse_cli_repositories_skips_zero_token_unbranched_bucket(self, handler):
        """An all-zero "" bucket (e.g. proxy pings) must not add a noise row next to a session row."""
        result = _repo_result(
            "org/repo",
            {
                "main": [_repo_user_bucket("u1", (5, 5, 0, 0), session_docs=1, duration=10)],
                "": [_repo_user_bucket("u1", (0, 0, 0, 0), session_docs=0)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert [(r["branch"], r["input_tokens"]) for r in rows] == [("main", 5)]

    def test_parse_cli_repositories_merges_detached_head_branches_into_one_row(self, handler):
        """Claude Code "HEAD" and Copilot CLI "detached@<sha>" are the same thing: no branch."""
        result = _repo_result(
            "org/repo",
            {
                "HEAD": [_repo_user_bucket("u1", (1, 1, 0, 0), session_docs=1, duration=10)],
                "detached@4d432ed": [_repo_user_bucket("u1", (2, 2, 0, 0), session_docs=1, duration=20)],
                "detached@9af01bc": [_repo_user_bucket("u1", (4, 4, 0, 0), session_docs=1, duration=30)],
                "main": [_repo_user_bucket("u1", (8, 8, 0, 0), session_docs=1, duration=40)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert [(r["branch"], r["input_tokens"], r["session_duration"]) for r in rows] == [
            ("", 7, 60),
            ("main", 8, 40),
        ]

    def test_parse_cli_repositories_treats_token_only_detached_head_as_unbranched(self, handler):
        """Detached-HEAD proxy tokens without a session doc land on the "" row, like branch-less ones."""
        result = _repo_result(
            "org/repo",
            {
                "": [_repo_user_bucket("u1", (0, 0, 0, 0), session_docs=1, duration=10)],
                "HEAD": [_repo_user_bucket("u1", (3, 3, 0, 0), session_docs=0)],
                "detached@4d432ed": [_repo_user_bucket("u1", (4, 4, 0, 0), session_docs=0)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert [(r["branch"], r["input_tokens"], r["session_duration"]) for r in rows] == [("", 7, 10)]

    def test_parse_cli_repositories_drops_unbranched_tokens_without_any_session(self, handler):
        """EPMCDME-12687 noise filter: proxy-only usage (e.g. Claude Desktop pings) stays hidden."""
        result = _repo_result("org/repo", {"": [_repo_user_bucket("u1", (9, 9, 9, 9), session_docs=0)]})

        assert handler._parse_cli_repositories_result(result) == []

    def test_parse_cli_repositories_does_not_attach_unbranched_tokens_across_users(self, handler):
        """u2's unbranched tokens have no u2 session in the repository, so they stay dropped."""
        result = _repo_result(
            "org/repo",
            {
                "main": [_repo_user_bucket("u1", (0, 0, 0, 0), session_docs=1)],
                "": [_repo_user_bucket("u2", (9, 9, 9, 9), session_docs=0)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert [(r["user_name"], r["input_tokens"]) for r in rows] == [("u1", 0)]

    def test_parse_cli_repositories_still_drops_token_only_bucket_on_real_branch(self, handler):
        """Only branch "" is treated as missing attribution; a real branch without sessions is unchanged."""
        result = _repo_result(
            "org/repo",
            {
                "main": [_repo_user_bucket("u1", (1, 1, 0, 0), session_docs=1)],
                "feature": [_repo_user_bucket("u1", (50, 50, 0, 0), session_docs=0)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert [(r["branch"], r["input_tokens"]) for r in rows] == [("main", 1)]

    @pytest.mark.asyncio
    @patch(
        "codemie.service.analytics.handlers.user_identity_resolver.UserIdentityResolver.resolve_rows",
        new_callable=AsyncMock,
    )
    async def test_cli_repositories_pagination_accuracy(self, mock_resolve_rows, handler, mock_repository):
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
                                                    "token_data": {
                                                        "input_tokens": {"value": 1000},
                                                        "cache_creation_tokens": {"value": 100},
                                                        "cache_read_tokens": {"value": 200},
                                                        "output_tokens": {"value": 500},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 10000},
                                                        "total_lines_added": {"value": 50},
                                                    },
                                                },
                                                {
                                                    "key": "bob@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 2000},
                                                        "cache_creation_tokens": {"value": 200},
                                                        "cache_read_tokens": {"value": 400},
                                                        "output_tokens": {"value": 1000},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 20000},
                                                        "total_lines_added": {"value": 100},
                                                    },
                                                },
                                                {
                                                    "key": "charlie@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 1500},
                                                        "cache_creation_tokens": {"value": 150},
                                                        "cache_read_tokens": {"value": 300},
                                                        "output_tokens": {"value": 750},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 15000},
                                                        "total_lines_added": {"value": 75},
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
                                                    "key": "alice@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 500},
                                                        "cache_creation_tokens": {"value": 50},
                                                        "cache_read_tokens": {"value": 100},
                                                        "output_tokens": {"value": 250},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 5000},
                                                        "total_lines_added": {"value": 25},
                                                    },
                                                },
                                                {
                                                    "key": "bob@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 800},
                                                        "cache_creation_tokens": {"value": 80},
                                                        "cache_read_tokens": {"value": 160},
                                                        "output_tokens": {"value": 400},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 8000},
                                                        "total_lines_added": {"value": 40},
                                                    },
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
                                                    "token_data": {
                                                        "input_tokens": {"value": 300},
                                                        "cache_creation_tokens": {"value": 30},
                                                        "cache_read_tokens": {"value": 60},
                                                        "output_tokens": {"value": 150},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 3000},
                                                        "total_lines_added": {"value": 15},
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
                                                    "key": "alice@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 3000},
                                                        "cache_creation_tokens": {"value": 300},
                                                        "cache_read_tokens": {"value": 600},
                                                        "output_tokens": {"value": 1500},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 30000},
                                                        "total_lines_added": {"value": 150},
                                                    },
                                                },
                                                {
                                                    "key": "bob@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 2500},
                                                        "cache_creation_tokens": {"value": 250},
                                                        "cache_read_tokens": {"value": 500},
                                                        "output_tokens": {"value": 1250},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 25000},
                                                        "total_lines_added": {"value": 125},
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
                                                    "key": "charlie@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 1200},
                                                        "cache_creation_tokens": {"value": 120},
                                                        "cache_read_tokens": {"value": 240},
                                                        "output_tokens": {"value": 600},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 12000},
                                                        "total_lines_added": {"value": 60},
                                                    },
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
                                                    "token_data": {
                                                        "input_tokens": {"value": 900},
                                                        "cache_creation_tokens": {"value": 90},
                                                        "cache_read_tokens": {"value": 180},
                                                        "output_tokens": {"value": 450},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 9000},
                                                        "total_lines_added": {"value": 45},
                                                    },
                                                },
                                                {
                                                    "key": "bob@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 700},
                                                        "cache_creation_tokens": {"value": 70},
                                                        "cache_read_tokens": {"value": 140},
                                                        "output_tokens": {"value": 350},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 7000},
                                                        "total_lines_added": {"value": 35},
                                                    },
                                                },
                                                {
                                                    "key": "charlie@example.com",
                                                    "token_data": {
                                                        "input_tokens": {"value": 600},
                                                        "cache_creation_tokens": {"value": 60},
                                                        "cache_read_tokens": {"value": 120},
                                                        "output_tokens": {"value": 300},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 6000},
                                                        "total_lines_added": {"value": 30},
                                                    },
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
    @patch(
        "codemie.service.analytics.handlers.user_identity_resolver.UserIdentityResolver.resolve_rows",
        new_callable=AsyncMock,
    )
    async def test_cli_repositories_last_page(self, mock_resolve_rows, handler, mock_repository):
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
                                                    "token_data": {
                                                        "input_tokens": {"value": 1000},
                                                        "cache_creation_tokens": {"value": 100},
                                                        "cache_read_tokens": {"value": 200},
                                                        "output_tokens": {"value": 500},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 10000},
                                                        "total_lines_added": {"value": 50},
                                                    },
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
    @patch(
        "codemie.service.analytics.handlers.user_identity_resolver.UserIdentityResolver.resolve_rows",
        new_callable=AsyncMock,
    )
    async def test_cli_repositories_sorting_consistency(self, mock_resolve_rows, handler, mock_repository):
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
                                                    "token_data": {
                                                        "input_tokens": {"value": 1000},
                                                        "cache_creation_tokens": {"value": 100},
                                                        "cache_read_tokens": {"value": 200},
                                                        "output_tokens": {"value": 500},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 10000},
                                                        "total_lines_added": {"value": 50},
                                                    },
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
                                                    "token_data": {
                                                        "input_tokens": {"value": 2000},
                                                        "cache_creation_tokens": {"value": 200},
                                                        "cache_read_tokens": {"value": 400},
                                                        "output_tokens": {"value": 1000},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 20000},
                                                        "total_lines_added": {"value": 100},
                                                    },
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
                                                    "token_data": {
                                                        "input_tokens": {"value": 1500},
                                                        "cache_creation_tokens": {"value": 150},
                                                        "cache_read_tokens": {"value": 300},
                                                        "output_tokens": {"value": 750},
                                                    },
                                                    "session_data": {
                                                        "doc_count": 1,
                                                        "session_duration": {"value": 15000},
                                                        "total_lines_added": {"value": 75},
                                                    },
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

    @pytest.fixture
    def handler(self, mock_user, mock_repository):
        """Override module-level fixture to return CLIInsightsHandler."""
        return CLIInsightsHandler(mock_user, mock_repository)

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
                                "repositories": {"buckets": {"buckets": []}},
                                "branches": {"buckets": {"buckets": []}},
                            },
                            {
                                "key": "epm-cdme",
                                "repositories": {"buckets": {"buckets": [{"key": "JnJ/payment-service/backend"}]}},
                                "branches": {"buckets": {"buckets": [{"key": "feature/ABC-123-auth"}]}},
                            },
                        ]
                    }
                }
            }
        )
        handler._cli_spend_by_project = AsyncMock(return_value=[_exact(120.0)])

        rows, rendered = await handler._get_cli_insights_project_rows("last_30_days", None, None, ["ada"], None)

        assert len(rows) == 1
        assert rows[0]["project_name"] == "epm-cdme"
        assert rows[0]["total_cost"] == 120.0
        assert rendered[0].is_exact
        # The blank key is never asked about: a filter naming no project would read as everything.
        names, _start, _end, users = handler._cli_spend_by_project.await_args.args
        assert names == ["epm-cdme"]
        assert users == ["ada"]

    @pytest.mark.asyncio
    async def test_get_cli_insights_top_spenders(self, handler):
        handler._get_cli_insights_user_rows = AsyncMock(
            return_value=(
                [
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
                ],
                [_exact(40.0), _exact(120.0)],
            )
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
    @patch(
        "codemie.service.analytics.handlers.cli.insights_handler.UserIdentityResolver.resolve_rows",
        new_callable=AsyncMock,
    )
    @patch(
        "codemie.service.analytics.handlers.cli.insights_handler.UserIdentityResolver.resolve",
        new_callable=AsyncMock,
        return_value="",
    )
    async def test_get_cli_insights_user_detail(self, mock_resolve, mock_resolve_rows, handler, mock_repository):
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
                        "money_settled": {"value": 3583},
                        "money_unsettled": {"doc_count": 0},
                        "models": {"buckets": [{"key": "claude-sonnet-4-6", "doc_count": 3583}]},
                    },
                    "repositories_data": {
                        "repositories": {
                            "buckets": [
                                {
                                    "key": "infra/codemie-terraform-gcp-platform",
                                    "branches": {
                                        "buckets": [
                                            {
                                                "key": "main",
                                                "clients": {
                                                    "buckets": [
                                                        {
                                                            "key": "CLI",
                                                            "usage": {
                                                                "lines_added": {"value": 5685},
                                                                "lines_removed": {"value": 788},
                                                                "projects": {"buckets": [{"key": "epm-cdme"}]},
                                                            },
                                                            "sessions": {"count": {"value": 23}},
                                                            "proxy": {
                                                                "total_cost": {"value": 74.87},
                                                                "money_settled": {"value": 200},
                                                                "money_unsettled": {"doc_count": 0},
                                                            },
                                                        }
                                                    ]
                                                },
                                            }
                                        ]
                                    },
                                },
                                {
                                    "key": "home/pavlo_chaikivskyi",
                                    "branches": {
                                        "buckets": [
                                            {
                                                "key": "HEAD",
                                                "clients": {
                                                    "buckets": [
                                                        {
                                                            "key": "CLI",
                                                            "usage": {
                                                                "lines_added": {"value": 152},
                                                                "lines_removed": {"value": 24},
                                                                "projects": {"buckets": [{"key": "epm-cdme"}]},
                                                            },
                                                            "sessions": {"count": {"value": 3}},
                                                            "proxy": {
                                                                "total_cost": {"value": 11.81},
                                                                "money_settled": {"value": 30},
                                                                "money_unsettled": {"doc_count": 0},
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
            "branch",
            "client",
            "sessions",
            "cost",
            "classification",
            "net_lines",
            # The money cell never travels without the three values that qualify it.
            "spend_unavailable",
            "spend_partial",
            "spend_missing_amount",
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
        assert response["data"]["repositories_table"]["data"]["rows"][0]["branch"] == "main"

    def test_build_cli_insights_user_aggregation_uses_session_total_for_unique_sessions(self, handler):
        aggregation = handler._build_cli_insights_user_aggregation({"bool": {"filter": []}})
        usage_filter = aggregation["aggs"]["users"]["aggs"]["projects"]["filter"]
        session_filter = aggregation["aggs"]["users"]["aggs"]["total_sessions"]["filter"]
        lines_added_filter = aggregation["aggs"]["users"]["aggs"]["total_lines_added"]["filter"]
        lines_removed_filter = aggregation["aggs"]["users"]["aggs"]["total_lines_removed"]["filter"]

        assert usage_filter == {"term": {"metric_name.keyword": "codemie_cli_tool_usage_total"}}
        assert session_filter == {"term": {"metric_name.keyword": "codemie_cli_session_total"}}
        assert lines_added_filter == {"term": {"metric_name.keyword": "codemie_cli_tool_usage_total"}}
        assert lines_removed_filter == {"term": {"metric_name.keyword": "codemie_cli_tool_usage_total"}}
        assert "cost_bucket" not in aggregation["aggs"]["users"]["aggs"]

    def test_build_cli_insights_project_aggregation_uses_cli_tool_usage_metric(self, handler):
        aggregation = handler._build_cli_insights_project_aggregation({"bool": {"filter": []}})
        usage_filter = aggregation["aggs"]["projects"]["aggs"]["repositories"]["filter"]

        assert usage_filter == {"term": {"metric_name.keyword": "codemie_cli_tool_usage_total"}}
        assert aggregation["aggs"]["projects"]["aggs"]["branches"]["filter"] == usage_filter
        assert "cost_bucket" not in aggregation["aggs"]["projects"]["aggs"]

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

        tool_counts = CLIClassificationEngine._extract_cli_tool_counts(result)

        assert tool_counts == [("Bash", 12), ("Read", 7), ("Edit", 3)]

    def test_classify_cli_entity_learning(self, handler):
        classification, confidence = CLIClassificationEngine._classify_cli_entity(
            repositories=["tutorials/react-course"],
            branches=["main"],
            project_name="demo@epam.com",
            spend=_exact(2.0),
        )

        assert classification in {"learning", "pet_project", "experimental"}
        assert 0 <= confidence <= 1

    def test_classify_cli_entity_production(self, handler):
        classification, confidence = CLIClassificationEngine._classify_cli_entity(
            repositories=["JnJ/payment-service/backend"],
            branches=["feature/ABC-123-auth"],
            project_name="team-project",
            spend=_exact(200.0),
        )

        assert classification == "production"
        assert confidence > 0


# ---------------------------------------------------------------------------
# `attributes.money_spent` is omitted from a metric document whenever the cost has not settled
# (`llm_proxy_monitoring_service`), and for streaming responses there is no cost header to settle
# from at all. Every `sum` over it therefore returns the **settled remainder**, and `... or 0` used
# to publish "nothing settled" as "nothing was spent". These tests pin the three states the read
# side now distinguishes, and specifically the three places where a fabricated zero did more damage
# than mis-stating one cell: a rank, a forecast, and a person's label.
# ---------------------------------------------------------------------------


def _cost_bucket(total: float | None, settled: int, unsettled: int) -> dict:
    """One `cost_bucket` shaped exactly as the aggregation now asks for it."""
    return {
        "total_cost": {"value": total},
        "money_settled": {"value": settled},
        "money_unsettled": {"doc_count": unsettled},
    }


NOTHING_SETTLED = _cost_bucket(0.0, 0, 812)
SOME_UNSETTLED = _cost_bucket(42.5, 300, 12)
ALL_SETTLED = _cost_bucket(42.5, 312, 0)
MEASURED_ZERO = _cost_bucket(0.0, 312, 0)

#: Repository signals worth production 1.5 and experimental 1.5 — a deliberate tie, so that the
#: half-point the old `< 5` branch added on a fabricated zero is exactly enough to flip the label.
TIED_REPOSITORIES = ["Acme/payments", "tmp"]


class TestTheCostRuleNeverFiresOnAFigureNobodyHas:
    """`classification_engine` turns money into a bare string, so the qualification is spent inside."""

    def test_an_absent_cost_leaves_the_label_to_the_repository_signals(self):
        """The consequence that has no format hint to warn a reader: a label decided by a phantom zero."""
        classification, _confidence = CLIClassificationEngine._classify_cli_entity(
            repositories=TIED_REPOSITORIES,
            branches=[],
            project_name="team-project",
            spend=_absent(),
        )

        assert classification == "production"

    def test_a_measured_zero_still_earns_the_low_cost_signal(self):
        """A document carrying `money_spent: 0.0` is a measurement, and this is the branch it means.

        It is also the exact boundary the defect crossed: with the fabricated zero restored, an
        *absent* figure reaches this same branch and the entity above is published `experimental`.
        """
        classification, _confidence = CLIClassificationEngine._classify_cli_entity(
            repositories=TIED_REPOSITORIES,
            branches=[],
            project_name="team-project",
            spend=_exact(0.0),
        )

        assert classification == "experimental"

    def test_a_subtotal_under_five_fires_no_low_cost_branch(self):
        """A subtotal is a lower bound, and `< 5` needs an upper one: a partial $3 may really be $300."""
        classification, _confidence = CLIClassificationEngine._classify_cli_entity(
            repositories=TIED_REPOSITORIES,
            branches=[],
            project_name="team-project",
            spend=_partial(3.0),
        )

        assert classification == "production"

    def test_a_subtotal_over_one_hundred_still_proves_the_production_branch(self):
        """What a lower bound *does* prove: settled >= 100 means the whole window is >= 100."""
        scores = dict.fromkeys(("production", "learning", "testing", "experimental", "pet_project"), 0.0)

        CLIClassificationEngine._score_cli_cost(scores, _partial(150.0))

        assert scores["production"] == 1.5

    def test_no_figure_contributes_no_component_at_all(self):
        """Neither branch — the score is left standing on the signals that were measured."""
        scores = dict.fromkeys(("production", "learning", "testing", "experimental", "pet_project"), 0.0)

        CLIClassificationEngine._score_cli_cost(scores, _absent())

        assert set(scores.values()) == {0.0}

    def test_an_exact_figure_keeps_every_threshold_it_had(self):
        """The fix removes no signal from a whole figure."""
        heavy = dict.fromkeys(("production", "learning", "testing", "experimental", "pet_project"), 0.0)
        middling = dict(heavy)
        light = dict(heavy)

        CLIClassificationEngine._score_cli_cost(heavy, _exact(120.0))
        CLIClassificationEngine._score_cli_cost(middling, _exact(20.0))
        CLIClassificationEngine._score_cli_cost(light, _exact(1.0))

        assert heavy["production"] == 1.5
        assert (middling["production"], middling["learning"]) == (0.5, 0.5)
        assert light["experimental"] == 0.5

    def test_a_rule_reason_says_the_cost_is_unknown_rather_than_quoting_a_figure(self):
        """The reason list is prose with no annotation slot, so the qualification goes in the sentence."""
        reasons = CLIClassificationEngine._build_cli_rule_reasons(
            repositories=["Acme/payments"],
            branches=["release/1.0"],
            spend=_absent(),
            total_sessions=4,
            active_days=2,
            net_lines=90,
        )

        assert any("cost: unknown" in reason for reason in reasons)
        assert not any("$" in reason for reason in reasons)

    def test_a_rule_reason_marks_a_subtotal_as_one(self):
        reasons = CLIClassificationEngine._build_cli_rule_reasons(
            repositories=["Acme/payments"],
            branches=["release/1.0"],
            spend=_partial(3.0),
            total_sessions=4,
            active_days=2,
            net_lines=90,
        )

        assert "cost: $3.00 (subtotal)" in reasons

    def test_a_rule_reason_states_a_whole_figure_bare(self):
        reasons = CLIClassificationEngine._build_cli_rule_reasons(
            repositories=["Acme/payments"],
            branches=["release/1.0"],
            spend=_exact(3.0),
            total_sessions=4,
            active_days=2,
            net_lines=90,
        )

        assert "cost: $3.00" in reasons


class TestForecastCardIsNeverAProjectionOfNothing:
    """`est_monthly_20d` is a card, so an absent input must not reach it as `$0.00`."""

    @pytest.fixture
    def handler(self, mock_user, mock_repository):
        return CLIInsightsHandler(mock_user, mock_repository)

    @staticmethod
    def _core_metrics(handler, proxy_bucket: dict) -> dict:
        # A ten-day window, so the 20-working-day projection is exactly twice the window's spend.
        return handler._extract_cli_user_detail_core_metrics(
            user_name="Ada",
            aggs={"proxy_usage": {**proxy_bucket, "models": {"buckets": []}}},
            start_dt=datetime(2026, 3, 1, tzinfo=timezone.utc),
            end_dt=datetime(2026, 3, 11, tzinfo=timezone.utc),
        )

    def test_nothing_settled_projects_to_no_figure(self, handler):
        metrics = self._core_metrics(handler, NOTHING_SETTLED)

        assert metrics["spend"].figure is None
        assert metrics["est_monthly_spend"].figure is None
        assert metrics["est_monthly_spend"].unavailable is True

    def test_a_subtotal_projects_to_a_subtotal(self, handler):
        metrics = self._core_metrics(handler, SOME_UNSETTLED)

        assert metrics["est_monthly_spend"].figure == 85.0
        assert metrics["est_monthly_spend"].partial is True

    def test_a_whole_figure_projects_to_a_whole_figure(self, handler):
        metrics = self._core_metrics(handler, ALL_SETTLED)

        assert metrics["est_monthly_spend"].figure == 85.0
        assert metrics["est_monthly_spend"].is_exact

    def test_a_measured_zero_projects_to_an_unqualified_zero(self, handler):
        metrics = self._core_metrics(handler, MEASURED_ZERO)

        assert metrics["est_monthly_spend"].figure == 0.0
        assert metrics["est_monthly_spend"].is_exact

    def test_both_money_cards_carry_the_annotation_beside_the_number(self, handler):
        cards = handler._build_cli_user_detail_key_metrics(
            spend=_absent(),
            est_monthly_spend=_absent(),
            total_sessions=3,
            total_prompts=10,
            net_lines=5,
            files_modified=1,
            active_days=2,
            avg_session_duration_min=1.0,
        )["data"]["metrics"]
        by_id = {card["id"]: card for card in cards}

        for card_id in ("total_cost", "est_monthly_20d"):
            assert by_id[card_id]["value"] is None
            assert by_id[card_id]["spend_unavailable"] is True
            assert by_id[card_id]["spend_partial"] is False


def _user_bucket(user_id: str, email: str, *, repositories: list[str], branches: list[str]) -> dict:
    """One `users` terms bucket for the user-rows and classification aggregations (money comes from the ledger)."""
    return {
        "key": user_id,
        "user_name": {"top": [{"metrics": {"attributes.user_name.keyword": email}}]},
        "user_email": {"top": [{"metrics": {"attributes.user_email.keyword": email}}]},
        "projects": {"buckets": {"buckets": [{"key": "epm-cdme"}]}},
        "repositories": {"buckets": {"buckets": [{"key": repo} for repo in repositories]}},
        "branches": {"buckets": {"buckets": [{"key": branch} for branch in branches]}},
        "total_lines_added": {"total": {"value": 100}},
        "total_lines_removed": {"total": {"value": 10}},
        "total_sessions": {"count": {"value": 4}},
    }


class TestAnAbsentFigureIsNotRankedAmongTheFigures:
    """Top Spenders publishes a rank, so a ledger figure decides a position as well as an amount."""

    @pytest.fixture
    def handler(self, mock_user, mock_repository):
        return CLIInsightsHandler(mock_user, mock_repository)

    @staticmethod
    def _answer_users(mock_repository, handler, users: list[tuple[str, str]], spends: list[RenderedSpend]) -> None:
        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "users": {
                        "buckets": [
                            _user_bucket(user_id, email, repositories=["Acme/payments"], branches=["release/1.0"])
                            for user_id, email in users
                        ]
                    }
                }
            }
        )
        handler._cli_spend_by_user = AsyncMock(return_value=spends)

    @pytest.mark.asyncio
    @patch(
        "codemie.service.analytics.handlers.cli.insights_handler.UserIdentityResolver.resolve_rows",
        new_callable=AsyncMock,
    )
    async def test_the_user_without_a_ledger_figure_ranks_last_with_no_figure(
        self, _mock_resolve_rows, handler, mock_repository
    ):
        self._answer_users(
            mock_repository,
            handler,
            [("u-absent", "absent@example.com"), ("u-small", "small@example.com"), ("u-big", "big@example.com")],
            [_absent(), _exact(1.0), _exact(500.0)],
        )

        rows = (await handler.get_cli_insights_top_spenders(time_period="last_30_days"))["data"]["rows"]

        assert [row["user_id"] for row in rows] == ["u-big", "u-small", "u-absent"]
        assert [row["rank"] for row in rows] == [1, 2, 3]
        # Last by rank, and explicitly no figure - never the $0.00 that put it there before.
        assert rows[-1]["total_cost"] is None
        assert rows[-1]["spend_unavailable"] is True
        assert rows[1]["total_cost"] == 1.0
        # The ledger is asked about the buckets' own users, in the order Elasticsearch listed them.
        assert handler._cli_spend_by_user.await_args.args[0] == ["u-absent", "u-small", "u-big"]

    @pytest.mark.asyncio
    @patch(
        "codemie.service.analytics.handlers.cli.insights_handler.UserIdentityResolver.resolve_rows",
        new_callable=AsyncMock,
    )
    async def test_a_subtotal_is_ranked_by_what_the_ledger_has_and_says_it_is_a_subtotal(
        self, _mock_resolve_rows, handler, mock_repository
    ):
        self._answer_users(
            mock_repository,
            handler,
            [("u-partial", "partial@example.com"), ("u-exact", "exact@example.com")],
            [_partial(42.5), _exact(10.0)],
        )

        rows = (await handler.get_cli_insights_all_users(time_period="last_30_days"))["data"]["rows"]
        by_id = {row["user_id"]: row for row in rows}

        assert by_id["u-partial"]["total_cost"] == 42.5
        assert by_id["u-partial"]["spend_partial"] is True
        assert by_id["u-partial"]["spend_missing_amount"] is None
        assert by_id["u-exact"]["spend_partial"] is False
        # Line and session counts are measurements and keep their zeros and their exactness.
        assert by_id["u-partial"]["net_lines"] == 90
        assert by_id["u-partial"]["total_sessions"] == 4

    @pytest.mark.asyncio
    @patch(
        "codemie.service.analytics.handlers.cli.insights_handler.UserIdentityResolver.resolve_rows",
        new_callable=AsyncMock,
    )
    async def test_top_users_by_cost_ranks_by_the_ledger_figure_and_puts_the_absent_one_last(
        self, _mock_resolve_rows, handler, mock_repository
    ):
        self._answer_users(
            mock_repository,
            handler,
            [("u-absent", "absent@example.com"), ("u-big", "big@example.com")],
            [_absent(), _exact(500.0)],
        )

        rows = (await handler.get_cli_insights_top_users_by_cost(time_period="last_30_days"))["data"]["rows"]

        assert [row["user_id"] for row in rows] == ["u-big", "u-absent"]
        assert rows[0]["total_cost"] == 500.0
        assert rows[1]["total_cost"] is None


class TestProjectRowsPublishTheThreeStates:
    """The plain money cell, one ledger figure per state."""

    @pytest.fixture
    def handler(self, mock_user, mock_repository):
        return CLIInsightsHandler(mock_user, mock_repository)

    @staticmethod
    def _answer_projects(mock_repository, handler, spends: dict[str, RenderedSpend]) -> None:
        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "projects": {
                        "buckets": [
                            {
                                "key": name,
                                "repositories": {"buckets": {"buckets": [{"key": "Acme/payments"}]}},
                                "branches": {"buckets": {"buckets": [{"key": "release/1.0"}]}},
                            }
                            for name in spends
                        ]
                    }
                }
            }
        )
        handler._cli_spend_by_project = AsyncMock(return_value=list(spends.values()))

    @pytest.mark.asyncio
    async def test_each_state_reaches_the_cell_as_itself(self, handler, mock_repository):
        self._answer_projects(
            mock_repository,
            handler,
            {
                "none-settled": _absent(),
                "some-unsettled": _partial(42.5),
                "all-settled": _exact(42.5),
                "measured-zero": _exact(0.0),
            },
        )

        rows, _rendered = await handler._get_cli_insights_project_rows("last_30_days", None, None, None, None)
        by_name = {row["project_name"]: row for row in rows}

        assert by_name["none-settled"]["total_cost"] is None
        assert by_name["none-settled"]["spend_unavailable"] is True
        assert by_name["some-unsettled"]["total_cost"] == 42.5
        assert by_name["some-unsettled"]["spend_partial"] is True
        assert by_name["all-settled"]["total_cost"] == 42.5
        assert (by_name["all-settled"]["spend_unavailable"], by_name["all-settled"]["spend_partial"]) == (False, False)
        # A measured zero is a figure and stays one, unqualified.
        assert by_name["measured-zero"]["total_cost"] == 0.0
        assert (
            by_name["measured-zero"]["spend_unavailable"],
            by_name["measured-zero"]["spend_partial"],
        ) == (False, False)

    @pytest.mark.asyncio
    async def test_a_project_whose_cost_is_unknown_is_ranked_last_not_at_zero(self, handler, mock_repository):
        self._answer_projects(mock_repository, handler, {"none-settled": _absent(), "cheap": _exact(0.5)})

        rows = (await handler.get_cli_insights_top_projects_by_cost(time_period="last_30_days"))["data"]["rows"]

        assert [row["project_name"] for row in rows] == ["cheap", "none-settled"]


class TestGroupTotalsAndAveragesCarryTheirQualification:
    """A group total over ledger figures, and an average whose numerator is a subtotal."""

    @pytest.fixture
    def handler(self, mock_user, mock_repository):
        return CLIInsightsHandler(mock_user, mock_repository)

    @staticmethod
    def _answer_classification(mock_repository, handler, spends: list[RenderedSpend]) -> None:
        # Identical repository/branch signals, so every user lands in one classification group and
        # the assertion is about the group's money rather than about how it was grouped.
        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "users": {
                        "buckets": [
                            _user_bucket(
                                f"u-{index}",
                                f"u{index}@example.com",
                                repositories=["Acme/payments"],
                                branches=["release/1.0"],
                            )
                            for index, _ in enumerate(spends)
                        ]
                    }
                }
            }
        )
        handler._cli_spend_by_user = AsyncMock(return_value=spends)

    @pytest.mark.asyncio
    async def test_one_member_with_no_figure_makes_the_group_total_and_average_say_so(self, handler, mock_repository):
        self._answer_classification(mock_repository, handler, [_absent(), _exact(100.0)])

        rows = (await handler.get_cli_insights_user_classification(time_period="last_30_days"))["data"]["rows"]

        assert len(rows) == 1
        assert rows[0]["user_count"] == 2
        assert rows[0]["total_cost"] == 100.0
        # The total is short by one member's whole spend, so it is not whole; the average divides
        # that subtotal by the full head count and inherits the same qualification.
        assert rows[0]["spend_unavailable"] is True
        assert rows[0]["spend_partial"] is True
        assert rows[0]["avg_cost"] == 50.0

    @pytest.mark.asyncio
    async def test_a_group_in_which_nothing_settled_has_neither_a_total_nor_an_average(self, handler, mock_repository):
        self._answer_classification(mock_repository, handler, [_absent(), _absent()])

        rows = (await handler.get_cli_insights_user_classification(time_period="last_30_days"))["data"]["rows"]

        assert rows[0]["total_cost"] is None
        assert rows[0]["avg_cost"] is None
        assert rows[0]["user_count"] == 2

    @pytest.mark.asyncio
    async def test_a_group_of_whole_figures_totals_and_averages_exactly(self, handler, mock_repository):
        self._answer_classification(mock_repository, handler, [_exact(30.0), _exact(10.0)])

        rows = (await handler.get_cli_insights_user_classification(time_period="last_30_days"))["data"]["rows"]

        assert rows[0]["total_cost"] == 40.0
        assert rows[0]["avg_cost"] == 20.0
        assert (rows[0]["spend_unavailable"], rows[0]["spend_partial"]) == (False, False)

    @pytest.mark.asyncio
    async def test_project_classification_groups_combine_rather_than_add_their_cells(self, handler, mock_repository):
        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "projects": {
                        "buckets": [
                            {
                                "key": name,
                                "repositories": {"buckets": {"buckets": [{"key": "Acme/payments"}]}},
                                "branches": {"buckets": {"buckets": [{"key": "release/1.0"}]}},
                            }
                            for name in ("alpha", "beta")
                        ]
                    }
                }
            }
        )
        handler._cli_spend_by_project = AsyncMock(return_value=[_absent(), _exact(60.0)])

        rows = (await handler.get_cli_insights_project_classification(time_period="last_30_days"))["data"]["rows"]

        assert len(rows) == 1
        assert rows[0]["project_count"] == 2
        assert rows[0]["total_cost"] == 60.0
        assert rows[0]["spend_unavailable"] is True
        assert rows[0]["spend_partial"] is True


class TestCliSpendIsReadFromTheLedgerPerRow:
    """The two reads every CLI Insights money cell goes through hand the ledger the caller's boundary and the CLI group."""

    START = datetime(2026, 8, 1, tzinfo=timezone.utc)
    END = datetime(2026, 8, 31, tzinfo=timezone.utc)

    @pytest.fixture
    def handler(self, mock_user, mock_repository):
        return CLIInsightsHandler(mock_user, mock_repository)

    @pytest.mark.asyncio
    async def test_user_spend_is_one_cli_group_read_per_user_under_the_callers_boundary(self, handler):
        module = "codemie.service.analytics.handlers.cli.insights_handler"
        with (
            patch(f"{module}.user_report_filters", new=AsyncMock(return_value=["f-ada", "f-bob"])) as filters,
            patch(f"{module}.read_group_spend", new=AsyncMock(return_value=[_exact(1.0), _exact(2.0)])) as read,
        ):
            spends = await handler._cli_spend_by_user(["u-ada", "u-bob"], self.START, self.END, ["alpha"])

        assert [spend.figure for spend in spends] == [1.0, 2.0]
        filters.assert_awaited_once_with(["u-ada", "u-bob"], ["alpha"])
        read.assert_awaited_once_with(
            TimeRange(self.START, self.END), handler._pipeline.access_scope, ["f-ada", "f-bob"], SpendGroup.CLI
        )

    @pytest.mark.asyncio
    async def test_project_spend_is_one_cli_group_read_per_project_under_the_callers_boundary(self, handler):
        module = "codemie.service.analytics.handlers.cli.insights_handler"
        with (
            patch(f"{module}.project_report_filters", new=AsyncMock(return_value=["f-alpha"])) as filters,
            patch(f"{module}.read_group_spend", new=AsyncMock(return_value=[_exact(3.0)])) as read,
        ):
            spends = await handler._cli_spend_by_project(["alpha"], self.START, self.END, ["ada"])

        assert [spend.figure for spend in spends] == [3.0]
        filters.assert_awaited_once_with(["alpha"], ["ada"])
        read.assert_awaited_once_with(
            TimeRange(self.START, self.END), handler._pipeline.access_scope, ["f-alpha"], SpendGroup.CLI
        )

    @pytest.mark.asyncio
    async def test_a_window_the_port_cannot_accept_is_passed_on_as_no_period(self, handler):
        module = "codemie.service.analytics.handlers.cli.insights_handler"
        with (
            patch(f"{module}.user_report_filters", new=AsyncMock(return_value=["f-ada"])),
            patch(f"{module}.read_group_spend", new=AsyncMock(return_value=[_absent()])) as read,
        ):
            await handler._cli_spend_by_user(["u-ada"], self.END, self.START, None)

        assert read.await_args.args[0] is None


class TestRepositoryAndCategoryMoneyInTheDetailModal:
    """The per-repository rows and the category rollup built from them."""

    @staticmethod
    def _client_bucket(cost_bucket: dict, sessions: int) -> dict:
        return {
            "key": "CLI",
            "usage": {
                "lines_added": {"value": 100},
                "lines_removed": {"value": 10},
                "projects": {"buckets": [{"key": "epm-cdme"}]},
            },
            "sessions": {"count": {"value": sessions}},
            "proxy": cost_bucket,
        }

    @classmethod
    def _repository_buckets(cls, entries: list[tuple[str, dict, int]]) -> list[dict]:
        return [
            {
                "key": repository,
                "branches": {
                    "buckets": [{"key": "main", "clients": {"buckets": [cls._client_bucket(cost, sessions)]}}]
                },
            }
            for repository, cost, sessions in entries
        ]

    def test_a_repository_whose_cost_never_settled_shows_no_figure_and_sorts_last(self):
        rows, rendered = CLIClassificationEngine._build_cli_repository_classifications(
            self._repository_buckets(
                [
                    ("Acme/unknown", NOTHING_SETTLED, 5),
                    ("Acme/cheap", _cost_bucket(2.0, 3, 0), 5),
                ]
            )
        )

        assert [row["repository"] for row in rows] == ["Acme/cheap", "Acme/unknown"]
        assert rows[-1]["cost"] is None
        assert rows[-1]["spend_unavailable"] is True
        assert rendered[-1].figure is None

    def test_two_buckets_of_one_row_combine_their_figures_rather_than_adding_one_to_nothing(self):
        """The same logical row arrives twice; one half unsettled must not be added away silently."""
        buckets = [
            {
                "key": "Acme/payments",
                "branches": {
                    "buckets": [
                        # 'HEAD' and '' both normalise to no tracked branch, which is exactly how one
                        # logical row arrives as two buckets: proxy cost events carry no branch.
                        {"key": "HEAD", "clients": {"buckets": [self._client_bucket(_cost_bucket(20.0, 8, 0), 3)]}},
                        {"key": "", "clients": {"buckets": [self._client_bucket(NOTHING_SETTLED, 2)]}},
                    ]
                },
            }
        ]

        rows, _rendered = CLIClassificationEngine._build_cli_repository_classifications(buckets)

        assert len(rows) == 1
        assert rows[0]["sessions"] == 5
        assert rows[0]["cost"] == 20.0
        assert rows[0]["spend_unavailable"] is True
        assert rows[0]["spend_partial"] is True

    def test_a_category_of_nothing_but_unknown_costs_has_no_cost(self):
        rows, rendered = CLIClassificationEngine._build_cli_repository_classifications(
            self._repository_buckets([("Acme/unknown", NOTHING_SETTLED, 5)])
        )

        breakdown, _breakdown_spend = CLIClassificationEngine._build_cli_category_breakdown(rows, rendered)

        assert len(breakdown) == 1
        assert breakdown[0]["cost"] is None
        assert breakdown[0]["spend_unavailable"] is True
        # Sessions and the share derived from them are counts and stay exact.
        assert breakdown[0]["sessions"] == 5
        assert breakdown[0]["percentage"] == 100.0

    def test_a_repository_that_settled_money_but_opened_no_session_keeps_its_row(self):
        """Proxy cost events carry no session, so a repository can settle real spend and open none.

        The row used to be dropped for having no session, which took its money out of this table and
        out of the category rollup built from it while the modal's Total Cost card above still counted
        it - $500 in the card, in no row, and nothing saying the table was short.
        """
        rows, rendered = CLIClassificationEngine._build_cli_repository_classifications(
            self._repository_buckets(
                [
                    ("Org/web", _cost_bucket(25.0, 40, 0), 25),
                    ("Org/batch", _cost_bucket(500.0, 12, 0), 0),
                ]
            )
        )

        assert [(row["repository"], row["sessions"], row["cost"]) for row in rows] == [
            ("Org/batch", 0, 500.0),
            ("Org/web", 25, 25.0),
        ]
        # Nothing about the kept row is qualified: its figure settled in full.
        assert rows[0]["spend_unavailable"] is False
        assert rows[0]["spend_partial"] is False
        # And the table now adds up to what the card over it shows.
        assert combine_rendered_spend(rendered).figure == 525.0
        assert combine_rendered_spend(rendered).is_exact is True

    def test_the_category_rollup_regains_the_session_less_repositorys_money(self):
        rows, rendered = CLIClassificationEngine._build_cli_repository_classifications(
            self._repository_buckets(
                [
                    ("Org/web", _cost_bucket(25.0, 40, 0), 25),
                    ("Org/batch", _cost_bucket(500.0, 12, 0), 0),
                ]
            )
        )

        breakdown, _breakdown_spend = CLIClassificationEngine._build_cli_category_breakdown(rows, rendered)

        assert sum(row["cost"] for row in breakdown) == 525.0
        # Sessions are counts and the session-less row contributes a real zero to them.
        assert sum(row["sessions"] for row in breakdown) == 25

    def test_a_session_less_repository_with_a_measured_zero_keeps_its_row_as_a_zero(self):
        rows, _rendered = CLIClassificationEngine._build_cli_repository_classifications(
            self._repository_buckets([("Org/free", MEASURED_ZERO, 0)])
        )

        # A measured zero is a figure: the row says the work was free rather than omitting it.
        assert [(row["repository"], row["cost"], row["spend_unavailable"]) for row in rows] == [
            ("Org/free", 0.0, False)
        ]

    def test_a_session_less_repository_with_no_figure_at_all_is_still_dropped(self):
        """Nothing settled means nothing to lose: the money the card counts is not in this bucket."""
        rows, rendered = CLIClassificationEngine._build_cli_repository_classifications(
            self._repository_buckets(
                [
                    ("Org/web", _cost_bucket(25.0, 40, 0), 25),
                    ("Org/idle", NOTHING_SETTLED, 0),
                ]
            )
        )

        assert [row["repository"] for row in rows] == ["Org/web"]
        assert combine_rendered_spend(rendered).figure == 25.0
