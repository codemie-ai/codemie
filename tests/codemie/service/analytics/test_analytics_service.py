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

"""Unit tests for AnalyticsService.

This module tests the main analytics service facade that delegates to domain-specific handlers.
Testing priority: LOW - This is primarily a delegation layer with minimal logic.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.analytics.analytics_service import AnalyticsService


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    user = MagicMock(name="test-user")
    user.user_id = "test-user-id"
    user.username = "test-username"
    return user


@pytest.fixture
def mock_repository():
    """Create a mock MetricsElasticRepository."""
    repository = MagicMock()
    repository.name = "MetricsElasticRepository"
    return repository


class TestAnalyticsServiceInitialization:
    """Test class for AnalyticsService initialization."""

    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    def test_init_uses_lazy_initialization(self, mock_repository, mock_user):
        """Verify handlers use lazy initialization pattern.

        Tests that:
        - Repository is initialized immediately
        - All 11 handler instances are None after initialization (not created yet)
        - Handlers are only created on first access (lazy loading)
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        # Act
        service = AnalyticsService(mock_user)

        # Assert - Repository initialized immediately
        mock_repository.assert_called_once()

        # Assert - All handler instances are None (not initialized yet)
        assert service._summary_handler_instance is None
        assert service._assistant_handler_instance is None
        assert service._workflow_handler_instance is None
        assert service._tools_handler_instance is None
        assert service._user_handler_instance is None
        assert service._project_handler_instance is None
        assert service._cli_handler_instance is None
        assert service._budget_handler_instance is None
        assert service._webhook_handler_instance is None
        assert service._mcp_handler_instance is None
        assert service._llm_handler_instance is None

    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    @patch("codemie.service.analytics.analytics_service.SummaryHandler")
    def test_handler_lazy_loading_on_first_access(self, mock_summary_handler_class, mock_repository, mock_user):
        """Verify handler is created only on first access (lazy loading).

        Tests that:
        - Handler is not created during service initialization
        - Handler is created on first property access
        - Same instance is returned on subsequent accesses
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        mock_handler_instance = MagicMock()
        mock_summary_handler_class.return_value = mock_handler_instance

        service = AnalyticsService(mock_user)

        # Assert - Handler not created yet
        assert service._summary_handler_instance is None
        mock_summary_handler_class.assert_not_called()

        # Act - Access handler property (first time)
        handler1 = service._summary_handler

        # Assert - Handler created with correct parameters
        mock_summary_handler_class.assert_called_once_with(mock_user, mock_repo_instance)
        assert handler1 == mock_handler_instance
        assert service._summary_handler_instance is not None

        # Act - Access handler property again (second time)
        handler2 = service._summary_handler

        # Assert - Same instance returned, no additional creation
        mock_summary_handler_class.assert_called_once()  # Still only called once
        assert handler2 == handler1  # Same instance returned

    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    def test_init_stores_user_and_repository(self, mock_repository, mock_user):
        """Verify user and repository are stored as instance attributes.

        Tests that:
        - service._user equals the provided user
        - service._repository is an instance of MetricsElasticRepository
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        # Act
        service = AnalyticsService(mock_user)

        # Assert
        assert service._user == mock_user
        assert service._repository == mock_repo_instance


class TestAnalyticsServiceDelegation:
    """Test class for AnalyticsService delegation methods.

    Note: Service methods are pure delegation - extensive testing not needed.
    Testing 2-3 representative methods from different handlers.
    """

    @patch("codemie.service.analytics.analytics_service.EngagementHandler")
    @patch("codemie.service.analytics.analytics_service.SummaryHandler")
    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    @pytest.mark.asyncio
    async def test_get_summaries_delegates_to_summary_handler(
        self, mock_repository, mock_summary_handler_class, mock_engagement_handler_class, mock_user
    ):
        """Verify get_summaries delegates correctly to SummaryHandler.

        Tests that:
        - summary_handler.get_summaries is called once with correct parameters
        - result contains base metrics plus DAU and MAU
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        mock_handler_instance = MagicMock()
        mock_summary_handler_class.return_value = mock_handler_instance

        base_result = {
            "data": {"metrics": [{"id": "total_tokens", "value": 1000}]},
            "metadata": {},
        }
        mock_handler_instance.get_summaries = AsyncMock(return_value=base_result)

        mock_engagement_instance = MagicMock()
        mock_engagement_handler_class.return_value = mock_engagement_instance
        mock_engagement_instance.get_dau = AsyncMock(return_value={"data": {"metrics": [{"id": "dau", "value": 5}]}})
        mock_engagement_instance.get_mau = AsyncMock(return_value={"data": {"metrics": [{"id": "mau", "value": 50}]}})

        service = AnalyticsService(mock_user)

        # Act
        result = await service.get_summaries(
            time_period="last_30_days",
            users=["user1", "user2"],
            projects=["project1"],
        )

        # Assert - summary_handler called with correct params
        mock_handler_instance.get_summaries.assert_called_once_with(
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=["user1", "user2"],
            projects=["project1"],
        )
        # Result contains base metrics + DAU + MAU
        assert "data" in result
        assert "metrics" in result["data"]
        metric_ids = [m["id"] for m in result["data"]["metrics"]]
        assert "total_tokens" in metric_ids
        assert "dau" in metric_ids
        assert "mau" in metric_ids

    @patch("codemie.service.analytics.analytics_service.AssistantHandler")
    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    @pytest.mark.asyncio
    async def test_get_assistants_chats_delegates_with_pagination(
        self, mock_repository, mock_assistant_handler_class, mock_user
    ):
        """Verify delegation with pagination parameters to AssistantHandler.

        Tests that:
        - assistant_handler.get_assistants_chats is called with all parameters including pagination
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        mock_handler_instance = MagicMock()
        mock_assistant_handler_class.return_value = mock_handler_instance

        expected_result = {"chats": [], "total": 0, "page": 2, "per_page": 50}
        mock_handler_instance.get_assistants_chats = AsyncMock(return_value=expected_result)

        service = AnalyticsService(mock_user)

        # Act
        result = await service.get_assistants_chats(
            time_period="last_7_days",
            page=2,
            per_page=50,
        )

        # Assert
        mock_handler_instance.get_assistants_chats.assert_called_once_with(
            time_period="last_7_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            page=2,
            per_page=50,
        )
        assert result == expected_result

    @patch("codemie.service.analytics.analytics_service.AssistantHandler")
    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    @pytest.mark.asyncio
    async def test_get_agents_usage_delegates_to_assistant_handler(
        self, mock_repository, mock_assistant_handler_class, mock_user
    ):
        """Verify multiple methods delegate to same handler (AssistantHandler).

        Tests that:
        - assistant_handler.get_agents_usage is called
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        mock_handler_instance = MagicMock()
        mock_assistant_handler_class.return_value = mock_handler_instance

        expected_result = {"agents": [], "total": 0}
        mock_handler_instance.get_agents_usage = AsyncMock(return_value=expected_result)

        service = AnalyticsService(mock_user)

        # Act
        result = await service.get_agents_usage(
            time_period="last_30_days",
            users=["user1"],
        )

        # Assert
        mock_handler_instance.get_agents_usage.assert_called_once_with(
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=["user1"],
            projects=None,
            page=0,
            per_page=30,
        )
        assert result == expected_result

    @patch("codemie.service.analytics.analytics_service.ProjectHandler")
    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    @pytest.mark.asyncio
    async def test_get_projects_unique_daily_delegates_to_project_handler(
        self, mock_repository, mock_project_handler_class, mock_user
    ):
        """Verify get_projects_unique_daily delegates correctly to ProjectHandler.

        Tests that:
        - project_handler.get_projects_unique_daily is called once with correct parameters
        - result equals mocked return value
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        mock_handler_instance = MagicMock()
        mock_project_handler_class.return_value = mock_handler_instance

        expected_result = {
            "data": {
                "columns": [
                    {"id": "date", "label": "Date", "type": "date"},
                    {"id": "unique_projects", "label": "Unique Projects", "type": "number"},
                ],
                "rows": [
                    {"date": "2026-01-01", "unique_projects": 5},
                    {"date": "2026-01-02", "unique_projects": 8},
                ],
            },
            "metadata": {},
            "pagination": {"page": 0, "per_page": 20, "total_count": 2, "has_more": False},
        }
        mock_handler_instance.get_projects_unique_daily = AsyncMock(return_value=expected_result)

        service = AnalyticsService(mock_user)

        # Act
        result = await service.get_projects_unique_daily(
            time_period="last_30_days",
            users=["user1@example.com"],
            projects=["project1"],
        )

        # Assert
        mock_handler_instance.get_projects_unique_daily.assert_called_once_with(
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=["user1@example.com"],
            projects=["project1"],
        )
        assert result == expected_result

    @patch("codemie.service.analytics.analytics_service.UserHandler")
    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    @pytest.mark.asyncio
    async def test_get_users_spending_delegates_to_user_handler(
        self, mock_repository, mock_user_handler_class, mock_user
    ):
        """Verify user analytics delegation to UserHandler.

        Tests that:
        - user_handler.get_users_spending is called
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        mock_handler_instance = MagicMock()
        mock_user_handler_class.return_value = mock_handler_instance

        expected_result = {"users": [], "total": 0}
        mock_handler_instance.get_users_spending = AsyncMock(return_value=expected_result)

        service = AnalyticsService(mock_user)

        # Act
        result = await service.get_users_spending(
            time_period="last_7_days",
            page=1,
            per_page=10,
        )

        # Assert
        mock_handler_instance.get_users_spending.assert_called_once_with(
            time_period="last_7_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            page=1,
            per_page=10,
        )
        assert result == expected_result

    @patch("codemie.service.analytics.analytics_service.CLIHandler")
    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    @pytest.mark.asyncio
    async def test_cli_methods_delegate_to_cli_handler(self, mock_repository, mock_cli_handler_class, mock_user):
        """Verify all CLI methods delegate to CLIHandler.

        Tests delegation for: get_cli_summary, get_cli_agents, get_cli_llms,
        get_cli_users, get_cli_errors, get_cli_repositories
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        mock_handler_instance = MagicMock()
        mock_cli_handler_class.return_value = mock_handler_instance

        # Setup return values as AsyncMock
        mock_handler_instance.get_cli_summary = AsyncMock(return_value={"summary": "data"})
        mock_handler_instance.get_cli_agents = AsyncMock(return_value={"agents": []})
        mock_handler_instance.get_cli_llms = AsyncMock(return_value={"llms": []})
        mock_handler_instance.get_cli_users = AsyncMock(return_value={"users": []})
        mock_handler_instance.get_cli_errors = AsyncMock(return_value={"errors": []})
        mock_handler_instance.get_cli_repositories = AsyncMock(return_value={"repositories": []})

        service = AnalyticsService(mock_user)

        # Act & Assert - get_cli_summary
        result = await service.get_cli_summary(time_period="last_30_days")
        mock_handler_instance.get_cli_summary.assert_called_once_with(
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
        )
        assert result == {"summary": "data"}

        # Act & Assert - get_cli_agents
        result = await service.get_cli_agents(time_period="last_7_days", page=1, per_page=30)
        mock_handler_instance.get_cli_agents.assert_called_once_with(
            time_period="last_7_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            page=1,
            per_page=30,
        )
        assert result == {"agents": []}

        # Act & Assert - get_cli_llms
        result = await service.get_cli_llms(users=["user1"])
        mock_handler_instance.get_cli_llms.assert_called_once_with(
            time_period=None,
            start_date=None,
            end_date=None,
            users=["user1"],
            projects=None,
            page=0,
            per_page=20,
        )
        assert result == {"llms": []}

        # Act & Assert - get_cli_users
        result = await service.get_cli_users(projects=["project1"])
        mock_handler_instance.get_cli_users.assert_called_once_with(
            time_period=None,
            start_date=None,
            end_date=None,
            users=None,
            projects=["project1"],
            page=0,
            per_page=20,
        )
        assert result == {"users": []}

        # Act & Assert - get_cli_errors
        result = await service.get_cli_errors(page=2, per_page=50)
        mock_handler_instance.get_cli_errors.assert_called_once_with(
            time_period=None,
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            page=2,
            per_page=50,
        )
        assert result == {"errors": []}

        # Act & Assert - get_cli_repositories
        result = await service.get_cli_repositories()
        mock_handler_instance.get_cli_repositories.assert_called_once_with(
            time_period=None,
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            page=0,
            per_page=20,
        )
        assert result == {"repositories": []}

    @patch("codemie.service.analytics.analytics_service.WorkflowHandler")
    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    @pytest.mark.asyncio
    async def test_get_workflows_delegates_with_date_range(
        self, mock_repository, mock_workflow_handler_class, mock_user
    ):
        """Verify workflow delegation with custom date range.

        Tests that:
        - workflow_handler.get_workflows is called with start_date and end_date
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        mock_handler_instance = MagicMock()
        mock_workflow_handler_class.return_value = mock_handler_instance

        expected_result = {"workflows": [], "total": 0}
        mock_handler_instance.get_workflows = AsyncMock(return_value=expected_result)

        service = AnalyticsService(mock_user)

        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 12, 31)

        # Act
        result = await service.get_workflows(
            start_date=start_date,
            end_date=end_date,
            page=0,
            per_page=20,
        )

        # Assert
        mock_handler_instance.get_workflows.assert_called_once_with(
            time_period=None,
            start_date=start_date,
            end_date=end_date,
            users=None,
            projects=None,
            page=0,
            per_page=20,
        )
        assert result == expected_result

    @patch("codemie.service.analytics.analytics_service.BudgetHandler")
    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    @pytest.mark.asyncio
    async def test_get_budget_soft_limit_delegates_to_budget_handler(
        self, mock_repository, mock_budget_handler_class, mock_user
    ):
        """Verify budget soft limit delegation to BudgetHandler.

        Tests that:
        - budget_handler.get_budget_soft_limit is called
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        mock_handler_instance = MagicMock()
        mock_budget_handler_class.return_value = mock_handler_instance

        expected_result = {"warnings": [], "total": 0}
        mock_handler_instance.get_budget_soft_limit = AsyncMock(return_value=expected_result)

        service = AnalyticsService(mock_user)

        # Act
        result = await service.get_budget_soft_limit(time_period="last_30_days")

        # Assert
        mock_handler_instance.get_budget_soft_limit.assert_called_once_with(
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            page=0,
            per_page=20,
        )
        assert result == expected_result

    @patch("codemie.service.analytics.analytics_service.MCPHandler")
    @patch("codemie.service.analytics.analytics_service.MetricsElasticRepository")
    @pytest.mark.asyncio
    async def test_get_mcp_servers_delegates_to_mcp_handler(self, mock_repository, mock_mcp_handler_class, mock_user):
        """Verify MCP servers delegation to MCPHandler.

        Tests that:
        - mcp_handler.get_mcp_servers is called
        """
        # Arrange
        mock_repo_instance = MagicMock()
        mock_repository.return_value = mock_repo_instance

        mock_handler_instance = MagicMock()
        mock_mcp_handler_class.return_value = mock_handler_instance

        expected_result = {"servers": [], "total": 0}
        mock_handler_instance.get_mcp_servers = AsyncMock(return_value=expected_result)

        service = AnalyticsService(mock_user)

        # Act
        result = await service.get_mcp_servers(users=["user1", "user2"])

        # Assert
        mock_handler_instance.get_mcp_servers.assert_called_once_with(
            time_period=None,
            start_date=None,
            end_date=None,
            users=["user1", "user2"],
            projects=None,
            page=0,
            per_page=20,
        )
        assert result == expected_result


class TestAIAdoptionDrillDownDelegation:
    """Tests for AI Adoption drill-down delegation methods."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.analytics_service.AIAdoptionHandler")
    async def test_get_user_engagement_users_delegates_to_handler(
        self, mock_adoption_handler_class, mock_repository, mock_user
    ):
        """Verify get_user_engagement_users delegates to adoption handler."""
        # Arrange
        service = AnalyticsService(mock_user)

        # Mock handler response
        expected_response = {
            "data": {"columns": [], "rows": []},
            "metadata": {},
            "pagination": {"total_count": 0},
        }
        service._adoption_handler.get_user_engagement_users = AsyncMock(return_value=expected_response)

        # Act
        result = await service.get_user_engagement_users(
            project="project1",
            page=1,
            per_page=50,
            user_type="power_user",
            activity_level="daily",
            multi_assistant_only=True,
            sort_by="engagement_score",
            sort_order="desc",
        )

        # Assert
        service._adoption_handler.get_user_engagement_users.assert_called_once_with(
            project="project1",
            page=1,
            per_page=50,
            user_type="power_user",
            activity_level="daily",
            multi_assistant_only=True,
            sort_by="engagement_score",
            sort_order="desc",
            config=None,
        )
        assert result == expected_response

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.analytics_service.AIAdoptionHandler")
    async def test_get_assistant_reusability_detail_delegates_to_handler(
        self, mock_adoption_handler_class, mock_repository, mock_user
    ):
        """Verify get_assistant_reusability_detail delegates to adoption handler."""
        # Arrange
        service = AnalyticsService(mock_user)

        # Mock handler response
        expected_response = {
            "data": {"columns": [], "rows": []},
            "metadata": {},
            "pagination": {"total_count": 0},
        }
        service._adoption_handler.get_assistant_reusability_detail = AsyncMock(return_value=expected_response)

        # Act
        result = await service.get_assistant_reusability_detail(
            project="project1",
            page=0,
            per_page=20,
            status="active",
            adoption="team_adopted",
            sort_by="total_usage",
            sort_order="asc",
        )

        # Assert
        service._adoption_handler.get_assistant_reusability_detail.assert_called_once_with(
            project="project1",
            page=0,
            per_page=20,
            status="active",
            adoption="team_adopted",
            sort_by="total_usage",
            sort_order="asc",
            config=None,
        )
        assert result == expected_response

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.analytics_service.AIAdoptionHandler")
    async def test_get_workflow_reusability_detail_delegates_to_handler(
        self, mock_adoption_handler_class, mock_repository, mock_user
    ):
        """Verify get_workflow_reusability_detail delegates to adoption handler."""
        # Arrange
        service = AnalyticsService(mock_user)

        # Mock handler response
        expected_response = {
            "data": {"columns": [], "rows": []},
            "metadata": {},
            "pagination": {"total_count": 0},
        }
        service._adoption_handler.get_workflow_reusability_detail = AsyncMock(return_value=expected_response)

        # Act
        result = await service.get_workflow_reusability_detail(
            project="project1",
            page=2,
            per_page=100,
            status="inactive",
            reuse="single_user",
            sort_by="execution_count",
            sort_order="desc",
        )

        # Assert
        service._adoption_handler.get_workflow_reusability_detail.assert_called_once_with(
            project="project1",
            page=2,
            per_page=100,
            status="inactive",
            reuse="single_user",
            sort_by="execution_count",
            sort_order="desc",
            config=None,
        )
        assert result == expected_response

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.analytics_service.AIAdoptionHandler")
    async def test_get_datasource_reusability_detail_delegates_to_handler(
        self, mock_adoption_handler_class, mock_repository, mock_user
    ):
        """Verify get_datasource_reusability_detail delegates to adoption handler."""
        # Arrange
        service = AnalyticsService(mock_user)

        # Mock handler response
        expected_response = {
            "data": {"columns": [], "rows": []},
            "metadata": {},
            "pagination": {"total_count": 0},
        }
        service._adoption_handler.get_datasource_reusability_detail = AsyncMock(return_value=expected_response)

        # Act
        result = await service.get_datasource_reusability_detail(
            project="project1",
            page=0,
            per_page=50,
            status="active",
            shared="shared",
            type="git",
            sort_by="assistant_count",
            sort_order="desc",
        )

        # Assert
        service._adoption_handler.get_datasource_reusability_detail.assert_called_once_with(
            project="project1",
            page=0,
            per_page=50,
            status="active",
            shared="shared",
            type="git",
            sort_by="assistant_count",
            sort_order="desc",
            config=None,
        )
        assert result == expected_response

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.analytics_service.AIAdoptionHandler")
    async def test_drill_down_methods_pass_config_parameter(
        self, mock_adoption_handler_class, mock_repository, mock_user
    ):
        """Verify drill-down methods pass optional config parameter to handler."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig

        service = AnalyticsService(mock_user)
        custom_config = AIAdoptionConfig(maturity_activation_threshold=30)

        # Mock handler response
        expected_response = {"data": {}, "metadata": {}, "pagination": {}}
        service._adoption_handler.get_user_engagement_users = AsyncMock(return_value=expected_response)

        # Act
        await service.get_user_engagement_users(project="project1", config=custom_config)

        # Assert
        call_kwargs = service._adoption_handler.get_user_engagement_users.call_args[1]
        assert call_kwargs["config"] == custom_config
        assert call_kwargs["config"].maturity_activation_threshold == 30
