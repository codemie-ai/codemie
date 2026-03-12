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

"""Unit tests for AIAdoptionHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.ai_adoption_handler import AIAdoptionHandler
from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig


@pytest.fixture
def mock_admin_user():
    """Create mock admin user."""
    user = MagicMock(spec=User)
    user.is_admin = True
    user.project_names = ["project1", "project2"]
    user.id = "admin-user-id"
    return user


@pytest.fixture
def mock_non_admin_user():
    """Create mock non-admin user."""
    user = MagicMock(spec=User)
    user.is_admin = False
    user.project_names = ["project1"]
    user.id = "regular-user-id"
    return user


@pytest.fixture
def handler_admin(mock_admin_user):
    """Create handler with admin user."""
    return AIAdoptionHandler(mock_admin_user)


@pytest.fixture
def handler_non_admin(mock_non_admin_user):
    """Create handler with non-admin user."""
    return AIAdoptionHandler(mock_non_admin_user)


@pytest.fixture
def mock_config():
    """Create mock AIAdoptionConfig."""
    return AIAdoptionConfig()


class TestInitialization:
    """Tests for handler initialization."""

    def test_init_stores_user(self, mock_admin_user):
        """Verify handler stores user reference."""
        # Act
        handler = AIAdoptionHandler(mock_admin_user)

        # Assert
        assert handler._user == mock_admin_user

    def test_init_with_non_admin_user(self, mock_non_admin_user):
        """Verify handler can be initialized with non-admin user."""
        # Act
        handler = AIAdoptionHandler(mock_non_admin_user)

        # Assert
        assert handler._user == mock_non_admin_user
        assert handler._user.is_admin is False


class TestGetAccessibleProjects:
    """Tests for _get_accessible_projects helper method."""

    def test_admin_user_can_access_all_projects(self, handler_admin):
        """Verify admin can access all projects when None specified."""
        # Act
        result = handler_admin._get_accessible_projects(None)

        # Assert
        assert result is None  # None means all projects

    def test_admin_user_can_filter_specific_projects(self, handler_admin):
        """Verify admin can filter to specific projects."""
        # Arrange
        requested_projects = ["project1", "project3"]

        # Act
        result = handler_admin._get_accessible_projects(requested_projects)

        # Assert
        assert result == requested_projects

    def test_non_admin_user_limited_to_their_projects(self, handler_non_admin):
        """Verify non-admin can only access their assigned projects."""
        # Act
        result = handler_non_admin._get_accessible_projects(None)

        # Assert
        assert result == ["project1"]  # Only their accessible projects

    def test_non_admin_user_intersection_with_requested_projects(self, handler_non_admin):
        """Verify non-admin gets intersection of requested and accessible projects."""
        # Arrange
        requested_projects = ["project1", "project2", "project3"]

        # Act
        result = handler_non_admin._get_accessible_projects(requested_projects)

        # Assert
        assert result == ["project1"]  # Only accessible project from request


class TestExtractOverviewCounts:
    """Tests for _extract_overview_counts helper method."""

    def test_extract_counts_from_valid_row(self, handler_admin):
        """Verify counts extracted correctly from database row."""
        # Arrange
        mock_row = MagicMock()
        mock_row.total_projects = 5
        mock_row.total_users = 100
        mock_row.total_assistants = 50
        mock_row.total_workflows = 25
        mock_row.total_datasources = 10

        # Act
        counts = handler_admin._extract_overview_counts(mock_row)

        # Assert
        assert counts["total_projects"] == 5
        assert counts["total_users"] == 100
        assert counts["total_assistants"] == 50
        assert counts["total_workflows"] == 25
        assert counts["total_datasources"] == 10

    def test_extract_counts_handles_none_row(self, handler_admin):
        """Verify defaults when row is None."""
        # Act
        counts = handler_admin._extract_overview_counts(None)

        # Assert
        assert counts["total_projects"] == 0
        assert counts["total_users"] == 0
        assert counts["total_assistants"] == 0
        assert counts["total_workflows"] == 0
        assert counts["total_datasources"] == 0

    def test_extract_counts_handles_none_values(self, handler_admin):
        """Verify None values default to 0."""
        # Arrange
        mock_row = MagicMock()
        mock_row.total_projects = None
        mock_row.total_users = None
        mock_row.total_assistants = 10
        mock_row.total_workflows = None
        mock_row.total_datasources = 5

        # Act
        counts = handler_admin._extract_overview_counts(mock_row)

        # Assert
        assert counts["total_projects"] == 0
        assert counts["total_users"] == 0
        assert counts["total_assistants"] == 10
        assert counts["total_workflows"] == 0
        assert counts["total_datasources"] == 5


class TestBuildOverviewMetrics:
    """Tests for _build_overview_metrics helper method."""

    def test_build_metrics_structure(self, handler_admin):
        """Verify metrics structure matches expected format."""
        # Arrange
        counts = {
            "total_projects": 5,
            "total_users": 100,
            "total_assistants": 50,
            "total_workflows": 25,
            "total_datasources": 10,
        }

        # Act
        metrics = handler_admin._build_overview_metrics(counts)

        # Assert
        assert len(metrics) == 5
        assert all("id" in m for m in metrics)
        assert all("label" in m for m in metrics)
        assert all("type" in m for m in metrics)
        assert all("value" in m for m in metrics)
        assert all("format" in m for m in metrics)
        assert all("description" in m for m in metrics)

    def test_build_metrics_values_match_counts(self, handler_admin):
        """Verify metric values match input counts."""
        # Arrange
        counts = {
            "total_projects": 3,
            "total_users": 42,
            "total_assistants": 15,
            "total_workflows": 8,
            "total_datasources": 5,
        }

        # Act
        metrics = handler_admin._build_overview_metrics(counts)

        # Assert
        projects_metric = next(m for m in metrics if m["id"] == "total_projects")
        assert projects_metric["value"] == 3

        users_metric = next(m for m in metrics if m["id"] == "total_users")
        assert users_metric["value"] == 42

        assistants_metric = next(m for m in metrics if m["id"] == "total_assistants")
        assert assistants_metric["value"] == 15

        workflows_metric = next(m for m in metrics if m["id"] == "total_workflows")
        assert workflows_metric["value"] == 8

        datasources_metric = next(m for m in metrics if m["id"] == "total_datasources")
        assert datasources_metric["value"] == 5


class TestBuildMetric:
    """Tests for _build_metric helper method."""

    def test_build_metric_with_score_format(self, handler_admin):
        """Verify metric built correctly with score format."""
        # Arrange
        col_def = {
            "id": "test_score",
            "label": "Test Score",
            "format": "score",
            "description": "Test description",
        }
        mock_row = MagicMock()
        mock_row.test_score = 0.85

        # Act
        metric = handler_admin._build_metric(col_def, mock_row)

        # Assert
        assert metric["id"] == "test_score"
        assert metric["label"] == "Test Score"
        assert metric["type"] == "number"
        assert metric["value"] == pytest.approx(0.85)
        assert metric["format"] == "score"
        assert metric["description"] == "Test description"

    def test_build_metric_with_string_type(self, handler_admin):
        """Verify metric built correctly with string value."""
        # Arrange
        col_def = {
            "id": "maturity_level",
            "label": "Maturity Level",
            "format": "text",
            "description": "Current maturity level",
        }
        mock_row = MagicMock()
        mock_row.maturity_level = "Advanced"

        # Act
        metric = handler_admin._build_metric(col_def, mock_row)

        # Assert
        assert metric["id"] == "maturity_level"
        assert metric["value"] == "Advanced"
        assert metric["type"] == "string"

    def test_build_metric_handles_none_value(self, handler_admin):
        """Verify None value handled correctly."""
        # Arrange
        col_def = {
            "id": "test_metric",
            "label": "Test",
            "format": "score",
            "description": "Test",
        }
        mock_row = MagicMock()
        mock_row.test_metric = None

        # Act
        metric = handler_admin._build_metric(col_def, mock_row)

        # Assert
        assert metric["value"] is None


class TestFormatMetricValue:
    """Tests for _format_metric_value helper method."""

    def test_format_integer_type(self, handler_admin):
        """Verify integer formatting."""
        # Act
        result = handler_admin._format_metric_value(42.7, "integer")

        # Assert
        assert result == 42
        assert isinstance(result, int)

    def test_format_number_type(self, handler_admin):
        """Verify number formatting."""
        # Act
        result = handler_admin._format_metric_value(42.7, "number")

        # Assert
        assert result == pytest.approx(42.7)
        assert isinstance(result, float)

    def test_format_string_type(self, handler_admin):
        """Verify string values pass through."""
        # Act
        result = handler_admin._format_metric_value("test", "string")

        # Assert
        assert result == "test"

    def test_format_none_with_number_type(self, handler_admin):
        """Verify None becomes 0 for number types."""
        # Act
        result_int = handler_admin._format_metric_value(None, "integer")
        result_num = handler_admin._format_metric_value(None, "number")

        # Assert
        assert result_int == 0
        assert result_num == 0

    def test_format_none_with_string_type(self, handler_admin):
        """Verify None stays None for non-number types."""
        # Act
        result = handler_admin._format_metric_value(None, "string")

        # Assert
        assert result is None


class TestBuildMaturityResponse:
    """Tests for _build_maturity_response helper method."""

    def test_response_structure(self, handler_admin):
        """Verify response has correct structure."""
        # Arrange
        metrics = [
            {"id": "metric1", "value": 1.0},
            {"id": "metric2", "value": 2.0},
        ]
        target_projects = ["project1"]

        # Act
        response = handler_admin._build_maturity_response(metrics, target_projects)

        # Assert
        assert "data" in response
        assert "metadata" in response
        assert "metrics" in response["data"]
        assert response["data"]["metrics"] == metrics
        assert "timestamp" in response["metadata"]
        assert "data_as_of" in response["metadata"]
        assert "filters_applied" in response["metadata"]
        assert response["metadata"]["filters_applied"]["projects"] == target_projects

    def test_response_with_no_projects_filter(self, handler_admin):
        """Verify response when no project filter applied."""
        # Arrange
        metrics = []

        # Act
        response = handler_admin._build_maturity_response(metrics, None)

        # Assert
        assert response["metadata"]["filters_applied"]["projects"] is None


class TestGetAiAdoptionOverview:
    """Tests for get_ai_adoption_overview async method."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    async def test_overview_returns_expected_structure(self, mock_postgres_client, mock_session_class, handler_admin):
        """Verify overview returns correct response structure."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.total_projects = 5
        mock_row.total_users = 100
        mock_row.total_assistants = 50
        mock_row.total_workflows = 25
        mock_row.total_datasources = 10
        mock_result.first.return_value = mock_row

        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_ai_adoption_overview()

        # Assert
        assert "data" in result
        assert "metadata" in result
        assert "metrics" in result["data"]
        assert len(result["data"]["metrics"]) == 5
        assert result["metadata"]["filters_applied"]["projects"] is None

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    async def test_overview_with_project_filter(self, mock_postgres_client, mock_session_class, handler_admin):
        """Verify overview with project filter."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.total_projects = 2
        mock_row.total_users = 20
        mock_row.total_assistants = 10
        mock_row.total_workflows = 5
        mock_row.total_datasources = 3
        mock_result.first.return_value = mock_row

        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_ai_adoption_overview(projects=["project1"])

        # Assert
        assert result["metadata"]["filters_applied"]["projects"] == ["project1"]
        assert result["data"]["metrics"][0]["value"] == 2  # total_projects

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    async def test_overview_handles_empty_result(self, mock_postgres_client, mock_session_class, handler_admin):
        """Verify overview handles empty database result."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.first.return_value = None

        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_ai_adoption_overview()

        # Assert
        assert "data" in result
        assert len(result["data"]["metrics"]) == 5
        # All metrics should default to 0
        assert all(m["value"] == 0 for m in result["data"]["metrics"])

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    async def test_overview_non_admin_access_control(self, mock_postgres_client, mock_session_class, handler_non_admin):
        """Verify non-admin user limited to their projects."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.total_projects = 1
        mock_row.total_users = 10
        mock_row.total_assistants = 5
        mock_row.total_workflows = 2
        mock_row.total_datasources = 1
        mock_result.first.return_value = mock_row

        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_non_admin.get_ai_adoption_overview()

        # Assert
        # Non-admin should only see their accessible projects
        assert result["metadata"]["filters_applied"]["projects"] == ["project1"]


class TestGetAiAdoptionMaturity:
    """Tests for get_ai_adoption_maturity async method."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_maturity_returns_hierarchical_structure(
        self, mock_query_builder, mock_postgres_client, mock_session_class, handler_admin, mock_config
    ):
        """Verify maturity returns hierarchical metrics."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_maturity_query.return_value = (MagicMock(), {})

        mock_result = MagicMock()
        mock_row = MagicMock()
        # Overview metrics
        mock_row.adoption_index = 0.75
        mock_row.maturity_level = "Advanced"
        # User Engagement
        mock_row.user_engagement_score = 0.80
        mock_row.user_activation_rate = 0.70
        mock_row.dau_ratio = 0.60
        mock_row.mau_ratio = 0.85
        mock_row.engagement_distribution = 0.75
        mock_row.total_users = 100
        # Asset Reusability
        mock_row.asset_reusability_score = 0.65
        mock_row.assistants_reuse_rate = 0.60
        mock_row.assistant_utilization_rate = 0.70
        mock_row.workflow_reuse_rate = 0.50
        mock_row.workflow_utilization_rate = 0.80
        # Expertise Distribution
        mock_row.expertise_distribution_score = 0.70
        mock_row.creator_diversity = 0.65
        mock_row.champion_health = "Healthy"
        # Feature Adoption
        mock_row.feature_adoption_score = 0.85
        mock_row.median_conversation_depth = 15.0
        mock_row.feature_utilization_rate = 0.90

        mock_result.first.return_value = mock_row

        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_ai_adoption_maturity(config=mock_config)

        # Assert
        assert "data" in result
        assert "metadata" in result
        metrics = result["data"]["metrics"]
        assert len(metrics) == 6  # 2 overview + 4 dimensions

        # Verify dimension metrics have secondary_metrics
        user_engagement = next(m for m in metrics if m["id"] == "user_engagement_score")
        assert "secondary_metrics" in user_engagement
        assert len(user_engagement["secondary_metrics"]) > 0

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_maturity_uses_default_config_when_none(
        self, mock_query_builder, mock_postgres_client, mock_session_class, handler_admin
    ):
        """Verify default config used when none provided."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_maturity_query.return_value = (MagicMock(), {})

        mock_result = MagicMock()
        mock_result.first.return_value = None

        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_ai_adoption_maturity()

        # Assert
        # Should call build_maturity_query with default config
        mock_query_builder.build_maturity_query.assert_called_once()
        # Verify empty response structure
        assert "data" in result
        assert "metrics" in result["data"]


class TestGetAiAdoptionConfig:
    """Tests for get_ai_adoption_config async method."""

    @pytest.mark.asyncio
    async def test_config_returns_structure(self, handler_admin):
        """Verify config returns expected structure."""
        # Act
        result = await handler_admin.get_ai_adoption_config()

        # Assert
        assert "data" in result
        assert "metadata" in result
        assert "timestamp" in result["metadata"]
        assert "version" in result["metadata"]
        assert "description" in result["metadata"]
        assert isinstance(result["data"], dict)


class TestGetDimensionMetricsGeneric:
    """Tests for _get_dimension_metrics_generic async method."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_generic_method_returns_tabular_response(
        self, mock_query_builder, mock_postgres_client, mock_session_class, handler_admin
    ):
        """Verify generic method returns tabular response structure."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_user_engagement_metrics_query.return_value = (MagicMock(), {})
        mock_query_builder.build_project_count_query.return_value = (MagicMock(), {})

        # Mock count query
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 5

        # Mock main query
        mock_result = MagicMock()
        mock_row1 = MagicMock()
        mock_row1.project = "project1"
        mock_row1.user_engagement_score = 0.75
        mock_row1.dau_ratio = 0.60
        mock_row1.total_users = 50
        mock_row1.total_interactions = 1000
        mock_row1.user_activation_rate = 0.70
        mock_row1.mau_ratio = 0.80
        mock_row1.engagement_distribution = 0.65
        mock_row1.returning_user_rate = 0.55

        mock_result.__iter__.return_value = [mock_row1]

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        def mock_columns_getter():
            return [{"id": "project", "label": "Project", "type": "string"}]

        def mock_row_mapper(row):
            return {
                "project": row.project,
                "user_engagement_score": float(row.user_engagement_score),
                "dau_ratio": float(row.dau_ratio),
                "total_users": row.total_users,
                "total_interactions": row.total_interactions,
                "user_activation_rate": float(row.user_activation_rate),
                "mau_ratio": float(row.mau_ratio),
                "engagement_distribution": float(row.engagement_distribution),
                "returning_user_rate": float(row.returning_user_rate),
            }

        # Act
        result = await handler_admin._get_dimension_metrics_generic(
            projects=None,
            page=0,
            per_page=20,
            query_builder_method="build_user_engagement_metrics_query",
            columns_getter=mock_columns_getter,
            row_mapper=mock_row_mapper,
        )

        # Assert
        assert "data" in result
        assert "metadata" in result
        assert "pagination" in result
        assert "columns" in result["data"]
        assert "rows" in result["data"]
        assert len(result["data"]["rows"]) == 1
        assert result["pagination"]["total_count"] == 5
        assert result["pagination"]["page"] == 0
        assert result["pagination"]["per_page"] == 20

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_generic_method_calculates_has_more(
        self, mock_query_builder, mock_postgres_client, mock_session_class, handler_admin
    ):
        """Verify has_more pagination flag calculated correctly."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_user_engagement_metrics_query.return_value = (MagicMock(), {})
        mock_query_builder.build_project_count_query.return_value = (MagicMock(), {})

        # Mock count query - 50 total
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 50

        # Mock main query - empty for this test
        mock_result = MagicMock()
        mock_result.__iter__.return_value = []

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act - page 1 with 20 per page (covers items 20-39)
        result = await handler_admin._get_dimension_metrics_generic(
            projects=None,
            page=1,
            per_page=20,
            query_builder_method="build_user_engagement_metrics_query",
            columns_getter=lambda: [],
            row_mapper=lambda r: {},
        )

        # Assert
        assert result["pagination"]["total_count"] == 50
        assert result["pagination"]["has_more"] is True


class TestGetAiAdoptionUserEngagement:
    """Tests for get_ai_adoption_user_engagement async method."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_user_engagement_delegates_to_generic(
        self, mock_query_builder, mock_postgres_client, mock_session_class, handler_admin
    ):
        """Verify user engagement delegates to generic method."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_user_engagement_metrics_query.return_value = (MagicMock(), {})
        mock_query_builder.build_project_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0

        mock_result = MagicMock()
        mock_result.__iter__.return_value = []

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_ai_adoption_user_engagement()

        # Assert
        assert "data" in result
        assert "pagination" in result
        # Verify correct query builder method was called
        mock_query_builder.build_user_engagement_metrics_query.assert_called_once()


class TestGetAiAdoptionAssetReusability:
    """Tests for get_ai_adoption_asset_reusability async method."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_asset_reusability_delegates_to_generic(
        self, mock_query_builder, mock_postgres_client, mock_session_class, handler_admin
    ):
        """Verify asset reusability delegates to generic method."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_asset_reusability_metrics_query.return_value = (MagicMock(), {})
        mock_query_builder.build_project_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0

        mock_result = MagicMock()
        mock_result.__iter__.return_value = []

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_ai_adoption_asset_reusability()

        # Assert
        assert "data" in result
        assert "pagination" in result
        # Verify correct query builder method was called
        mock_query_builder.build_asset_reusability_metrics_query.assert_called_once()


class TestGetAiAdoptionExpertiseDistribution:
    """Tests for get_ai_adoption_expertise_distribution async method."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_expertise_distribution_delegates_to_generic(
        self, mock_query_builder, mock_postgres_client, mock_session_class, handler_admin
    ):
        """Verify expertise distribution delegates to generic method."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_expertise_distribution_metrics_query.return_value = (MagicMock(), {})
        mock_query_builder.build_project_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0

        mock_result = MagicMock()
        mock_result.__iter__.return_value = []

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_ai_adoption_expertise_distribution()

        # Assert
        assert "data" in result
        assert "pagination" in result
        # Verify correct query builder method was called
        mock_query_builder.build_expertise_distribution_metrics_query.assert_called_once()


class TestGetAiAdoptionFeatureAdoption:
    """Tests for get_ai_adoption_feature_adoption async method."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_feature_adoption_delegates_to_generic(
        self, mock_query_builder, mock_postgres_client, mock_session_class, handler_admin
    ):
        """Verify feature adoption delegates to generic method."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_feature_adoption_metrics_query.return_value = (MagicMock(), {})
        mock_query_builder.build_project_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0

        mock_result = MagicMock()
        mock_result.__iter__.return_value = []

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_ai_adoption_feature_adoption()

        # Assert
        assert "data" in result
        assert "pagination" in result
        # Verify correct query builder method was called
        mock_query_builder.build_feature_adoption_metrics_query.assert_called_once()


class TestEmptyMaturityResponse:
    """Tests for _empty_maturity_response helper method."""

    def test_empty_response_structure(self, handler_admin):
        """Verify empty response has correct structure."""
        # Act
        result = handler_admin._empty_maturity_response(["project1"])

        # Assert
        assert "data" in result
        assert "metadata" in result
        assert "metrics" in result["data"]
        assert len(result["data"]["metrics"]) == 6  # 2 overview + 4 dimensions

    def test_empty_response_all_zeros(self, handler_admin):
        """Verify all metric values are zero."""
        # Act
        result = handler_admin._empty_maturity_response([])

        # Assert
        metrics = result["data"]["metrics"]
        # Check overview metrics
        adoption_index = next(m for m in metrics if m["id"] == "adoption_index")
        assert adoption_index["value"] == pytest.approx(0.0)

        # Check dimension scores are zero
        for metric_id in [
            "user_engagement_score",
            "asset_reusability_score",
            "expertise_distribution_score",
            "feature_adoption_score",
        ]:
            metric = next(m for m in metrics if m["id"] == metric_id)
            assert metric["value"] == pytest.approx(0.0)


class TestBuildDimensionMetric:
    """Tests for _build_dimension_metric helper method."""

    def test_dimension_metric_with_secondary_metrics(self, handler_admin):
        """Verify dimension metric includes secondary metrics."""
        # Arrange
        columns = [
            {"id": "test_score", "label": "Test Score", "format": "score", "type": "number"},
            {"id": "metric1", "label": "Metric 1", "format": "percent", "type": "number"},
            {"id": "metric2", "label": "Metric 2", "format": "integer", "type": "integer"},
        ]
        mock_row = MagicMock()
        mock_row.test_score = 0.75
        mock_row.metric1 = 0.60
        mock_row.metric2 = 100

        # Act
        result = handler_admin._build_dimension_metric("test_score", columns, mock_row)

        # Assert
        assert result["id"] == "test_score"
        assert result["value"] == pytest.approx(0.75)
        assert "secondary_metrics" in result
        assert len(result["secondary_metrics"]) == 2
        assert result["secondary_metrics"][0]["id"] == "metric1"
        assert result["secondary_metrics"][1]["id"] == "metric2"

    def test_dimension_metric_skips_missing_columns(self, handler_admin):
        """Verify secondary metrics skipped when column not in row."""
        # Arrange
        columns = [
            {"id": "test_score", "label": "Test Score", "format": "score", "type": "number"},
            {"id": "metric1", "label": "Metric 1", "format": "percent", "type": "number"},
            {"id": "metric_not_in_row", "label": "Missing", "format": "integer", "type": "integer"},
        ]

        # Create a mock row where hasattr returns False for metric_not_in_row
        class MockRow:
            test_score = 0.75
            metric1 = 0.60
            # metric_not_in_row is intentionally not defined

        mock_row = MockRow()

        # Act
        result = handler_admin._build_dimension_metric("test_score", columns, mock_row)

        # Assert
        assert len(result["secondary_metrics"]) == 1  # Only metric1
        assert result["secondary_metrics"][0]["id"] == "metric1"


class TestGetUserEngagementUsers:
    """Tests for get_user_engagement_users drill-down method."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_user_engagement_users_returns_tabular_response(
        self, mock_query_builder, mock_session_class, mock_postgres_client, handler_admin, mock_config
    ):
        """Verify user engagement users returns tabular response with user data."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_user_engagement_users_query.return_value = (MagicMock(), {})
        mock_query_builder.build_user_engagement_users_count_query.return_value = (MagicMock(), {})

        # Mock count query
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 25

        # Mock main query
        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.user_id = "user-123"
        mock_row.user_name = "John Doe"
        mock_row.total_interactions = 100
        mock_row.first_used = MagicMock()
        mock_row.first_used.isoformat.return_value = "2024-01-01T00:00:00"
        mock_row.last_used = MagicMock()
        mock_row.last_used.isoformat.return_value = "2024-02-01T00:00:00"
        mock_row.days_since_last_activity = 5
        mock_row.is_activated = True
        mock_row.is_returning = True
        mock_row.is_daily_active = False
        mock_row.is_weekly_active = True
        mock_row.is_monthly_active = True
        mock_row.is_multi_assistant_user = True
        mock_row.distinct_assistant_count = 3
        mock_row.user_type = "Champion"
        mock_row.engagement_score = 0.85

        mock_result.__iter__.return_value = [mock_row]

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_user_engagement_users(
            project="project1", page=0, per_page=20, config=mock_config
        )

        # Assert
        assert "data" in result
        assert "metadata" in result
        assert "pagination" in result
        assert "columns" in result["data"]
        assert "rows" in result["data"]
        assert len(result["data"]["rows"]) == 1
        assert result["data"]["rows"][0]["user_name"] == "John Doe"
        assert result["data"]["rows"][0]["total_interactions"] == 100
        assert result["pagination"]["total_count"] == 25

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_user_engagement_users_with_filters(
        self, mock_query_builder, mock_session_class, mock_postgres_client, handler_admin
    ):
        """Verify user engagement users respects filter parameters."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_user_engagement_users_query.return_value = (MagicMock(), {})
        mock_query_builder.build_user_engagement_users_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 5

        mock_result = MagicMock()
        mock_result.__iter__.return_value = []

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_user_engagement_users(
            project="project1",
            user_type="Champion",
            activity_level="daily",
            multi_assistant_only=True,
            sort_by="engagement_score",
            sort_order="asc",
        )

        # Assert
        assert result["metadata"]["filters_applied"]["user_type"] == "Champion"
        assert result["metadata"]["filters_applied"]["activity_level"] == "daily"
        assert result["metadata"]["filters_applied"]["multi_assistant_only"] is True
        assert result["metadata"]["filters_applied"]["sort_by"] == "engagement_score"
        assert result["metadata"]["filters_applied"]["sort_order"] == "asc"

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_user_engagement_users_pagination(
        self, mock_query_builder, mock_session_class, mock_postgres_client, handler_admin
    ):
        """Verify pagination works correctly."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_user_engagement_users_query.return_value = (MagicMock(), {})
        mock_query_builder.build_user_engagement_users_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 50

        mock_result = MagicMock()
        mock_result.__iter__.return_value = []

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_user_engagement_users(project="project1", page=1, per_page=20)

        # Assert
        assert result["pagination"]["page"] == 1
        assert result["pagination"]["per_page"] == 20
        assert result["pagination"]["total_count"] == 50
        assert result["pagination"]["has_more"] is True


class TestGetUserEngagementUsersColumns:
    """Tests for _get_user_engagement_users_columns helper method."""

    def test_columns_structure(self, handler_admin):
        """Verify column structure is correct."""
        # Act
        columns = handler_admin._get_user_engagement_users_columns()

        # Assert
        assert len(columns) > 0
        assert all("id" in col for col in columns)
        assert all("label" in col for col in columns)
        assert all("type" in col for col in columns)
        assert all("format" in col for col in columns)
        assert all("description" in col for col in columns)

    def test_columns_include_expected_fields(self, handler_admin):
        """Verify all expected columns are present."""
        # Act
        columns = handler_admin._get_user_engagement_users_columns()

        # Assert
        column_ids = [col["id"] for col in columns]
        assert "user_name" in column_ids
        assert "user_type" in column_ids
        assert "engagement_score" in column_ids
        assert "total_interactions" in column_ids
        assert "distinct_assistant_count" in column_ids


class TestGetAssistantReusabilityDetail:
    """Tests for get_assistant_reusability_detail drill-down method."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_assistant_reusability_detail_returns_tabular_response(
        self, mock_query_builder, mock_session_class, mock_postgres_client, handler_admin, mock_config
    ):
        """Verify assistant reusability detail returns tabular response."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_assistant_reusability_detail_query.return_value = (MagicMock(), {})
        mock_query_builder.build_assistant_reusability_detail_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 10

        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.assistant_id = "asst-123"
        mock_row.assistant_name = "Test Assistant"
        mock_row.project = "project1"
        mock_row.description = "Test description"
        mock_row.total_usage = 500
        mock_row.unique_users = 25
        mock_row.last_used = MagicMock()
        mock_row.last_used.isoformat.return_value = "2024-02-01T00:00:00"
        mock_row.days_since_last_used = 3
        mock_row.is_active = "Active"
        mock_row.is_team_adopted = True
        mock_row.datasource_count = 2
        mock_row.toolkit_count = 1
        mock_row.mcp_server_count = 0
        mock_row.creator_id = "user-1"
        mock_row.creator_name = "Creator"
        mock_row.created_date = MagicMock()
        mock_row.created_date.isoformat.return_value = "2024-01-01T00:00:00"

        mock_result.__iter__.return_value = [mock_row]

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_assistant_reusability_detail(
            project="project1", page=0, per_page=20, config=mock_config
        )

        # Assert
        assert "data" in result
        assert "metadata" in result
        assert "pagination" in result
        assert len(result["data"]["rows"]) == 1
        assert result["data"]["rows"][0]["assistant_name"] == "Test Assistant"
        assert result["pagination"]["total_count"] == 10

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_assistant_reusability_detail_with_filters(
        self, mock_query_builder, mock_session_class, mock_postgres_client, handler_admin
    ):
        """Verify assistant detail respects filter parameters."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_assistant_reusability_detail_query.return_value = (MagicMock(), {})
        mock_query_builder.build_assistant_reusability_detail_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0

        mock_result = MagicMock()
        mock_result.__iter__.return_value = []

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_assistant_reusability_detail(
            project="project1", status="active", adoption="team", sort_by="unique_users", sort_order="desc"
        )

        # Assert
        assert result["metadata"]["filters_applied"]["status"] == "active"
        assert result["metadata"]["filters_applied"]["adoption"] == "team"
        assert result["metadata"]["filters_applied"]["sort_by"] == "unique_users"


class TestGetAssistantReusabilityDetailColumns:
    """Tests for _get_assistant_reusability_detail_columns helper method."""

    def test_columns_structure(self, handler_admin):
        """Verify column structure is correct."""
        # Act
        columns = handler_admin._get_assistant_reusability_detail_columns()

        # Assert
        assert len(columns) > 0
        assert all("id" in col for col in columns)
        assert all("label" in col for col in columns)
        assert all("type" in col for col in columns)

    def test_columns_include_expected_fields(self, handler_admin):
        """Verify all expected columns are present."""
        # Act
        columns = handler_admin._get_assistant_reusability_detail_columns()

        # Assert
        column_ids = [col["id"] for col in columns]
        assert "assistant_name" in column_ids
        assert "is_active" in column_ids
        assert "is_team_adopted" in column_ids
        assert "total_usage" in column_ids


class TestGetWorkflowReusabilityDetail:
    """Tests for get_workflow_reusability_detail drill-down method."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_workflow_reusability_detail_returns_tabular_response(
        self, mock_query_builder, mock_session_class, mock_postgres_client, handler_admin, mock_config
    ):
        """Verify workflow reusability detail returns tabular response."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_workflow_reusability_detail_query.return_value = (MagicMock(), {})
        mock_query_builder.build_workflow_reusability_detail_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 15

        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.workflow_id = "wf-123"
        mock_row.workflow_name = "Test Workflow"
        mock_row.project = "project1"
        mock_row.description = "Test workflow description"
        mock_row.execution_count = 100
        mock_row.unique_users = 10
        mock_row.last_executed = MagicMock()
        mock_row.last_executed.isoformat.return_value = "2024-02-01T00:00:00"
        mock_row.days_since_last_executed = 2
        mock_row.is_active = "Active"
        mock_row.is_multi_user = True
        mock_row.state_count = 5
        mock_row.tool_count = 3
        mock_row.custom_node_count = 1
        mock_row.assistant_count = 2
        mock_row.creator_id = "user-1"
        mock_row.creator_name = "Creator"
        mock_row.created_date = MagicMock()
        mock_row.created_date.isoformat.return_value = "2024-01-01T00:00:00"

        mock_result.__iter__.return_value = [mock_row]

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_workflow_reusability_detail(
            project="project1", page=0, per_page=20, config=mock_config
        )

        # Assert
        assert "data" in result
        assert "metadata" in result
        assert "pagination" in result
        assert len(result["data"]["rows"]) == 1
        assert result["data"]["rows"][0]["workflow_name"] == "Test Workflow"
        assert result["pagination"]["total_count"] == 15

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.PostgresClient")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_workflow_reusability_detail_with_filters(
        self, mock_query_builder, mock_session_class, mock_postgres_client, handler_admin
    ):
        """Verify workflow detail respects filter parameters."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_workflow_reusability_detail_query.return_value = (MagicMock(), {})
        mock_query_builder.build_workflow_reusability_detail_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0

        mock_result = MagicMock()
        mock_result.__iter__.return_value = []

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_postgres_client.get_async_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_workflow_reusability_detail(
            project="project1", status="active", reuse="multi_user", sort_by="execution_count", sort_order="desc"
        )

        # Assert
        assert result["metadata"]["filters_applied"]["status"] == "active"
        assert result["metadata"]["filters_applied"]["reuse"] == "multi_user"
        assert result["metadata"]["filters_applied"]["sort_by"] == "execution_count"


class TestGetWorkflowReusabilityDetailColumns:
    """Tests for _get_workflow_reusability_detail_columns helper method."""

    def test_columns_structure(self, handler_admin):
        """Verify column structure is correct."""
        # Act
        columns = handler_admin._get_workflow_reusability_detail_columns()

        # Assert
        assert len(columns) > 0
        assert all("id" in col for col in columns)
        assert all("label" in col for col in columns)

    def test_columns_include_expected_fields(self, handler_admin):
        """Verify all expected columns are present."""
        # Act
        columns = handler_admin._get_workflow_reusability_detail_columns()

        # Assert
        column_ids = [col["id"] for col in columns]
        assert "workflow_name" in column_ids
        assert "is_active" in column_ids
        assert "execution_count" in column_ids


class TestGetDatasourceReusabilityDetail:
    """Tests for get_datasource_reusability_detail drill-down method."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.models.index.IndexInfo")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_datasource_reusability_detail_returns_tabular_response(
        self, mock_query_builder, mock_session_class, mock_index_info, handler_admin, mock_config
    ):
        """Verify datasource reusability detail returns tabular response."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_datasource_reusability_detail_query.return_value = (MagicMock(), {})
        mock_query_builder.build_datasource_reusability_detail_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 8

        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.datasource_id = "ds-123"
        mock_row.datasource_name = "Test Datasource"
        mock_row.project = "project1"
        mock_row.description = "Test datasource"
        mock_row.datasource_type = "git"
        mock_row.assistant_count = 5
        mock_row.max_usage = 200
        mock_row.last_indexed = MagicMock()
        mock_row.last_indexed.isoformat.return_value = "2024-02-01T00:00:00"
        mock_row.days_since_last_indexed = 1
        mock_row.is_active = "Active"
        mock_row.is_shared = True
        mock_row.creator_id = "user-1"
        mock_row.creator_name = "Creator"
        mock_row.created_date = MagicMock()
        mock_row.created_date.isoformat.return_value = "2024-01-01T00:00:00"

        mock_result.__iter__.return_value = [mock_row]

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_index_info.get_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_datasource_reusability_detail(
            project="project1", page=0, per_page=20, config=mock_config
        )

        # Assert
        assert "data" in result
        assert "metadata" in result
        assert "pagination" in result
        assert len(result["data"]["rows"]) == 1
        assert result["data"]["rows"][0]["datasource_name"] == "Test Datasource"
        assert result["pagination"]["total_count"] == 8

    @pytest.mark.asyncio
    @patch("codemie.rest_api.models.index.IndexInfo")
    @patch("codemie.service.analytics.handlers.ai_adoption_handler.AsyncSession")
    @patch("codemie.service.analytics.queries.ai_adoption_framework.query_builder")
    async def test_datasource_reusability_detail_with_filters(
        self, mock_query_builder, mock_session_class, mock_index_info, handler_admin
    ):
        """Verify datasource detail respects filter parameters."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        mock_query_builder.build_datasource_reusability_detail_query.return_value = (MagicMock(), {})
        mock_query_builder.build_datasource_reusability_detail_count_query.return_value = (MagicMock(), {})

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0

        mock_result = MagicMock()
        mock_result.__iter__.return_value = []

        mock_session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])
        mock_index_info.get_engine.return_value = MagicMock()

        # Act
        result = await handler_admin.get_datasource_reusability_detail(
            project="project1", status="active", shared="shared", type="git", sort_by="assistant_count"
        )

        # Assert
        assert result["metadata"]["filters_applied"]["status"] == "active"
        assert result["metadata"]["filters_applied"]["shared"] == "shared"
        assert result["metadata"]["filters_applied"]["type"] == "git"


class TestGetDatasourceReusabilityDetailColumns:
    """Tests for _get_datasource_reusability_detail_columns helper method."""

    def test_columns_structure(self, handler_admin):
        """Verify column structure is correct."""
        # Act
        columns = handler_admin._get_datasource_reusability_detail_columns()

        # Assert
        assert len(columns) > 0
        assert all("id" in col for col in columns)
        assert all("label" in col for col in columns)

    def test_columns_include_expected_fields(self, handler_admin):
        """Verify all expected columns are present."""
        # Act
        columns = handler_admin._get_datasource_reusability_detail_columns()

        # Assert
        column_ids = [col["id"] for col in columns]
        assert "datasource_name" in column_ids
        assert "datasource_type" in column_ids
        assert "is_active" in column_ids
        assert "assistant_count" in column_ids


class TestBuildMetricsFromRow:
    """Tests for _build_metrics_from_row helper method."""

    @patch("codemie.service.analytics.queries.ai_adoption_framework.column_definitions")
    def test_build_metrics_from_row_with_full_data(self, mock_column_defs, handler_admin):
        """Verify metrics built correctly from full data row."""
        # Arrange
        mock_column_defs.get_maturity_metrics.return_value = [
            {"id": "adoption_index", "label": "Adoption Index", "format": "score"},
            {"id": "maturity_level", "label": "Maturity Level", "format": "text"},
        ]
        mock_column_defs.USER_ENGAGEMENT_COLUMNS = [{"id": "user_engagement_score", "format": "score"}]
        mock_column_defs.ASSET_REUSABILITY_COLUMNS = [{"id": "asset_reusability_score", "format": "score"}]
        mock_column_defs.EXPERTISE_DISTRIBUTION_COLUMNS = [{"id": "expertise_distribution_score", "format": "score"}]
        mock_column_defs.FEATURE_ADOPTION_COLUMNS = [{"id": "feature_adoption_score", "format": "score"}]
        mock_column_defs.DIMENSION_SCORE_COLUMNS = [
            {"id": "user_engagement_score", "label": "User Engagement", "description": "Test"},
            {"id": "asset_reusability_score", "label": "Asset Reusability", "description": "Test"},
            {"id": "expertise_distribution_score", "label": "Expertise Distribution", "description": "Test"},
            {"id": "feature_adoption_score", "label": "Feature Adoption", "description": "Test"},
        ]

        mock_row = MagicMock()
        mock_row.adoption_index = 0.75
        mock_row.maturity_level = "Advanced"
        mock_row.user_engagement_score = 0.80
        mock_row.asset_reusability_score = 0.65
        mock_row.expertise_distribution_score = 0.70
        mock_row.feature_adoption_score = 0.85

        # Act
        metrics = handler_admin._build_metrics_from_row(mock_row)

        # Assert
        assert len(metrics) == 6  # 2 overview + 4 dimensions
        # Check that overview metrics are present (order may vary)
        metric_ids = [m["id"] for m in metrics]
        assert "adoption_index" in metric_ids
        assert "maturity_level" in metric_ids

    def test_build_metrics_from_row_with_mock_row(self, handler_admin):
        """Verify metrics built correctly from empty MockRow."""

        # Arrange - Create mock row similar to what _empty_maturity_response creates
        class MockRow:
            adoption_index = 0.0
            maturity_level = "N/A"
            user_engagement_score = 0.0
            asset_reusability_score = 0.0
            expertise_distribution_score = 0.0
            feature_adoption_score = 0.0

        mock_row = MockRow()

        # Act
        metrics = handler_admin._build_metrics_from_row(mock_row)

        # Assert
        assert len(metrics) == 6  # 2 overview + 4 dimensions
        adoption_index = next(m for m in metrics if m["id"] == "adoption_index")
        assert adoption_index["value"] == pytest.approx(0.0)


class TestConfigurationEnhancements:
    """Tests for new configuration features: returning user window parameter."""

    def test_returning_user_window_default_is_14_days(self):
        """Verify returning user window defaults to 14 days."""
        # Arrange & Act
        config = AIAdoptionConfig()

        # Assert
        assert config.user_engagement_returning_user_window == 14

    def test_returning_user_window_accepts_zero_for_all_time(self):
        """Verify returning user window can be set to 0 for all-time mode."""
        # Arrange & Act
        config = AIAdoptionConfig(user_engagement_returning_user_window=0)

        # Assert
        assert config.user_engagement_returning_user_window == 0

    def test_returning_user_window_accepts_custom_value(self):
        """Verify returning user window accepts custom values within valid range."""
        # Arrange & Act
        config = AIAdoptionConfig(user_engagement_returning_user_window=30)

        # Assert
        assert config.user_engagement_returning_user_window == 30

    def test_returning_user_window_rejects_negative(self):
        """Verify returning user window rejects negative values."""
        # Act & Assert
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AIAdoptionConfig(user_engagement_returning_user_window=-1)

    def test_returning_user_window_rejects_too_large(self):
        """Verify returning user window rejects values > 365."""
        # Act & Assert
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AIAdoptionConfig(user_engagement_returning_user_window=366)

    def test_config_to_dict_includes_returning_user_window(self):
        """Verify to_dict() exports returning_user_window parameter."""
        # Arrange
        config = AIAdoptionConfig(user_engagement_returning_user_window=21)

        # Act
        config_dict = config.to_dict()

        # Assert
        assert "user_engagement" in config_dict
        assert "parameters" in config_dict["user_engagement"]
        assert "returning_user_window_days" in config_dict["user_engagement"]["parameters"]
        assert config_dict["user_engagement"]["parameters"]["returning_user_window_days"]["value"] == 21
        assert "description" in config_dict["user_engagement"]["parameters"]["returning_user_window_days"]

    def test_config_from_dict_parses_returning_user_window(self):
        """Verify from_dict() correctly parses returning_user_window."""
        # Arrange
        config_dict = {
            "user_engagement": {
                "parameters": {"returning_user_window_days": {"value": 28, "description": "Test window"}}
            }
        }

        # Act
        config = AIAdoptionConfig.from_dict(config_dict)

        # Assert
        assert config.user_engagement_returning_user_window == 28

    def test_config_roundtrip_preserves_returning_user_window(self):
        """Verify to_dict() → from_dict() roundtrip preserves returning_user_window field."""
        # Arrange
        original = AIAdoptionConfig(user_engagement_returning_user_window=45)

        # Act
        dict_form = original.to_dict()
        reconstructed = AIAdoptionConfig.from_dict(dict_form)

        # Assert
        assert reconstructed.user_engagement_returning_user_window == 45
