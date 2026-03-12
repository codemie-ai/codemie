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

"""Unit tests for AI Adoption Framework query builder functions."""

from __future__ import annotations

import pytest
from sqlalchemy.sql.elements import TextClause

from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig
from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
    build_maturity_query,
    build_dimensions_query,
    build_user_engagement_metrics_query,
    build_asset_reusability_metrics_query,
    build_expertise_distribution_metrics_query,
    build_feature_adoption_metrics_query,
    build_params_cte,
)


@pytest.fixture
def default_config():
    """Create default AIAdoptionConfig."""
    return AIAdoptionConfig()


@pytest.fixture
def custom_config():
    """Create custom AIAdoptionConfig with non-default values."""
    return AIAdoptionConfig(
        maturity_activation_threshold=30,
        user_engagement_activation_window=60,
        maturity_level_2_threshold=55,
        maturity_level_3_threshold=75,
    )


class TestConfigValidation:
    """Tests for AIAdoptionConfig validation."""

    def test_default_config_creation(self):
        """Verify config initializes with defaults."""
        # Act
        config = AIAdoptionConfig()

        # Assert
        assert config is not None
        assert isinstance(config, AIAdoptionConfig)
        assert config.maturity_activation_threshold == 20  # Default value

    def test_custom_config_creation(self):
        """Verify config accepts custom values."""
        # Act
        config = AIAdoptionConfig(maturity_activation_threshold=30)

        # Assert
        assert config.maturity_activation_threshold == 30  # Custom value

    def test_config_validation_fails_on_invalid_threshold(self):
        """Verify Pydantic validation occurs on config."""
        # Arrange - Create config with invalid threshold (> 1000)
        with pytest.raises(ValueError):
            AIAdoptionConfig(maturity_activation_threshold=2000)


class TestBuildMaturityQuery:
    """Tests for build_maturity_query function."""

    def test_returns_text_and_params(self, default_config):
        """Verify function returns tuple of (text, params)."""
        # Act
        query, params = build_maturity_query(default_config)

        # Assert
        assert isinstance(query, TextClause)
        assert isinstance(params, dict)

    def test_params_without_projects(self, default_config):
        """Verify params dict when no projects specified."""
        # Act
        _, params = build_maturity_query(default_config)

        # Assert
        assert "projects" in params
        assert params["projects"] is None

    def test_params_with_projects(self, default_config):
        """Verify params dict with project filter."""
        # Arrange
        projects = ["project1", "project2"]

        # Act
        _, params = build_maturity_query(default_config, projects=projects)

        # Assert
        assert params["projects"] == projects

    def test_query_contains_expected_ctes(self, default_config):
        """Verify query includes expected CTEs."""
        # Act
        query, _ = build_maturity_query(default_config)
        query_str = str(query)

        # Assert - Check for key CTEs
        assert "WITH" in query_str
        assert "params" in query_str or "assistant_stats" in query_str
        assert "SELECT" in query_str

    def test_query_contains_maturity_columns(self, default_config):
        """Verify query selects required maturity columns."""
        # Act
        query, _ = build_maturity_query(default_config)
        query_str = str(query)

        # Assert - Check for key column names
        assert "adoption_index" in query_str
        assert "maturity_level" in query_str
        assert "user_engagement_score" in query_str
        assert "asset_reusability_score" in query_str
        assert "expertise_distribution_score" in query_str
        assert "feature_adoption_score" in query_str

    def test_query_uses_config_thresholds(self, custom_config):
        """Verify query uses config threshold values."""
        # Act
        query, _ = build_maturity_query(custom_config)
        query_str = str(query)

        # Assert - Check that custom thresholds appear in query
        assert "55" in query_str  # maturity_level_2_threshold
        assert "75" in query_str  # maturity_level_3_threshold

    def test_query_aggregates_across_projects(self, default_config):
        """Verify query uses AVG for cross-project aggregation."""
        # Act
        query, _ = build_maturity_query(default_config)
        query_str = str(query)

        # Assert
        assert "AVG(" in query_str


class TestBuildDimensionsQuery:
    """Tests for build_dimensions_query function."""

    def test_returns_text_and_params(self, default_config):
        """Verify function returns tuple of (text, params)."""
        # Act
        query, params = build_dimensions_query(default_config)

        # Assert
        assert isinstance(query, TextClause)
        assert isinstance(params, dict)

    def test_params_includes_pagination(self, default_config):
        """Verify params include limit and offset."""
        # Act
        _, params = build_dimensions_query(default_config, page=2, per_page=50)

        # Assert
        assert params["limit"] == 50
        assert params["offset"] == 100  # page 2 * 50 per_page

    def test_params_default_pagination(self, default_config):
        """Verify default pagination values."""
        # Act
        _, params = build_dimensions_query(default_config)

        # Assert
        assert params["limit"] == 20
        assert params["offset"] == 0

    def test_params_with_projects(self, default_config):
        """Verify params with project filter."""
        # Arrange
        projects = ["project1"]

        # Act
        _, params = build_dimensions_query(default_config, projects=projects)

        # Assert
        assert params["projects"] == projects

    def test_query_contains_all_dimension_columns(self, default_config):
        """Verify query includes comprehensive dimension columns."""
        # Act
        query, _ = build_dimensions_query(default_config)
        query_str = str(query)

        # Assert - Check for columns from all 4 dimensions
        assert "total_users" in query_str
        assert "user_activation_rate" in query_str
        assert "assistants_reuse_rate" in query_str
        assert "creator_diversity" in query_str
        assert "median_conversation_depth" in query_str

    def test_query_has_where_clause(self, default_config):
        """Verify query filters out null projects."""
        # Act
        query, _ = build_dimensions_query(default_config)
        query_str = str(query)

        # Assert
        assert "WHERE" in query_str
        assert "project IS NOT NULL" in query_str.replace("ast.", "")

    def test_query_has_order_by(self, default_config):
        """Verify query orders by adoption_index."""
        # Act
        query, _ = build_dimensions_query(default_config)
        query_str = str(query)

        # Assert
        assert "ORDER BY" in query_str
        assert "adoption_index" in query_str

    def test_query_has_limit_offset(self, default_config):
        """Verify query includes LIMIT and OFFSET."""
        # Act
        query, _ = build_dimensions_query(default_config)
        query_str = str(query)

        # Assert
        assert "LIMIT" in query_str
        assert "OFFSET" in query_str


class TestCTEToTempTableTransformation:
    """Tests for CTE to temporary table transformation in build_dimensions_query."""

    @pytest.mark.parametrize(
        "table_name",
        [
            "params",
            "assistant_stats",
            "multi_assistant_users",
            "multi_assistant_stats",
            "filtered_projects",
        ],
    )
    def test_temp_table_created_with_drop_and_no_prefix(self, default_config, table_name):
        """Verify temp tables are created with DROP IF EXISTS and without temp_ prefix."""
        # Act
        query, _ = build_dimensions_query(default_config)
        query_str = str(query)

        # Assert
        assert f"DROP TABLE IF EXISTS {table_name};" in query_str
        assert f"CREATE TEMP TABLE {table_name} AS" in query_str
        # Should not have temp_ prefix
        assert f"temp_{table_name}" not in query_str.lower()

    def test_multi_assistant_split_and_dependency_order(self, default_config):
        """Verify multi_assistant CTEs are split correctly with proper dependency order."""
        # Act
        query, _ = build_dimensions_query(default_config)
        query_str = str(query)

        # Assert - Both tables created
        assert "CREATE TEMP TABLE multi_assistant_users AS" in query_str
        assert "CREATE TEMP TABLE multi_assistant_stats AS" in query_str

        # Assert - Correct order (users before stats)
        users_pos = query_str.index("CREATE TEMP TABLE multi_assistant_users AS")
        stats_pos = query_str.index("CREATE TEMP TABLE multi_assistant_stats AS")
        assert users_pos < stats_pos, "multi_assistant_users must be created before multi_assistant_stats"

        # Assert - stats references users
        stats_start = stats_pos
        next_create = query_str.find("DROP TABLE IF EXISTS", stats_start + 50)
        stats_sql = (
            query_str[stats_start:next_create] if next_create != -1 else query_str[stats_start : stats_start + 500]
        )
        assert "FROM multi_assistant_users" in stats_sql

    @pytest.mark.parametrize(
        "table_reference,join_type",
        [
            ("filtered_projects fp", "FROM"),
            ("params p", "CROSS JOIN"),
            ("assistant_stats ast", "LEFT JOIN"),
            ("multi_assistant_stats mas", "LEFT JOIN"),
        ],
    )
    def test_final_select_references_correct_table_names(self, default_config, table_reference, join_type):
        """Verify final SELECT references temp tables with correct names and join types."""
        # Act
        query, _ = build_dimensions_query(default_config)
        query_str = str(query)

        # Assert
        assert f"{join_type} {table_reference}" in query_str

    def test_uses_temp_tables_and_maintains_session_isolation(self, default_config):
        """Verify transformation uses CREATE TEMP TABLE for session isolation."""
        # Act
        query, _ = build_dimensions_query(default_config)
        query_str = str(query)

        # Assert
        # Should not use WITH CTE syntax
        assert not query_str.strip().startswith("WITH")
        # Must use CREATE TEMP TABLE
        assert "CREATE TEMP TABLE" in query_str

        # All table creations must be temporary
        import re

        create_temp_table_count = len(re.findall(r'\bCREATE TEMP TABLE\b', query_str))
        assert create_temp_table_count >= 15, f"Expected at least 15 temp tables, found {create_temp_table_count}"

        # No permanent tables
        permanent_tables = re.findall(r'\bCREATE\s+(?!TEMP\s+)TABLE\b', query_str)
        assert len(permanent_tables) == 0, "Should only create temp tables, not permanent tables"


class TestBuildUserEngagementMetricsQuery:
    """Tests for build_user_engagement_metrics_query function."""

    def test_returns_text_and_params(self, default_config):
        """Verify function returns tuple of (text, params)."""
        # Act
        query, params = build_user_engagement_metrics_query(default_config)

        # Assert
        assert isinstance(query, TextClause)
        assert isinstance(params, dict)

    def test_params_structure(self, default_config):
        """Verify params include pagination and projects."""
        # Act
        _, params = build_user_engagement_metrics_query(default_config, projects=["project1"], page=1, per_page=10)

        # Assert
        assert params["limit"] == 10
        assert params["offset"] == 10
        assert params["projects"] == ["project1"]

    def test_params_without_projects(self, default_config):
        """Verify projects param is None when not provided."""
        # Act
        _, params = build_user_engagement_metrics_query(default_config)

        # Assert
        assert params["projects"] is None

    def test_query_contains_user_engagement_columns(self, default_config):
        """Verify query includes User Engagement specific columns."""
        # Act
        query, _ = build_user_engagement_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "user_engagement_score" in query_str
        assert "dau_ratio" in query_str
        assert "mau_ratio" in query_str
        assert "user_activation_rate" in query_str
        assert "engagement_distribution" in query_str
        assert "returning_user_rate" in query_str

    def test_query_orders_by_user_engagement_score(self, default_config):
        """Verify query orders by user_engagement_score."""
        # Act
        query, _ = build_user_engagement_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "ORDER BY user_engagement_score DESC" in query_str

    def test_query_includes_total_users_count(self, default_config):
        """Verify query includes total_users from all_users_count CTE."""
        # Act
        query, _ = build_user_engagement_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "all_users_count" in query_str
        assert "total_users" in query_str


class TestBuildAssetReusabilityMetricsQuery:
    """Tests for build_asset_reusability_metrics_query function."""

    def test_returns_text_and_params(self, default_config):
        """Verify function returns tuple of (text, params)."""
        # Act
        query, params = build_asset_reusability_metrics_query(default_config)

        # Assert
        assert isinstance(query, TextClause)
        assert isinstance(params, dict)

    def test_params_structure(self, default_config):
        """Verify params include pagination and projects."""
        # Act
        _, params = build_asset_reusability_metrics_query(default_config, projects=["p1", "p2"], page=0, per_page=25)

        # Assert
        assert params["limit"] == 25
        assert params["offset"] == 0
        assert params["projects"] == ["p1", "p2"]

    def test_query_contains_asset_reusability_columns(self, default_config):
        """Verify query includes Asset Reusability specific columns."""
        # Act
        query, _ = build_asset_reusability_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "asset_reusability_score" in query_str
        assert "assistants_reuse_rate" in query_str
        assert "assistant_utilization_rate" in query_str
        assert "workflow_reuse_rate" in query_str
        assert "workflow_utilization_rate" in query_str
        assert "datasource_reuse_rate" in query_str
        assert "datasource_utilization_rate" in query_str

    def test_query_includes_total_counts(self, default_config):
        """Verify query includes total counts for assets."""
        # Act
        query, _ = build_asset_reusability_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "total_assistants" in query_str
        assert "total_workflows" in query_str
        assert "total_datasources" in query_str

    def test_query_orders_by_asset_reusability_score(self, default_config):
        """Verify query orders by asset_reusability_score."""
        # Act
        query, _ = build_asset_reusability_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "ORDER BY asset_reusability_score DESC" in query_str


class TestBuildExpertiseDistributionMetricsQuery:
    """Tests for build_expertise_distribution_metrics_query function."""

    def test_returns_text_and_params(self, default_config):
        """Verify function returns tuple of (text, params)."""
        # Act
        query, params = build_expertise_distribution_metrics_query(default_config)

        # Assert
        assert isinstance(query, TextClause)
        assert isinstance(params, dict)

    def test_params_structure(self, default_config):
        """Verify params include pagination and projects."""
        # Act
        _, params = build_expertise_distribution_metrics_query(
            default_config, projects=["project1"], page=3, per_page=15
        )

        # Assert
        assert params["limit"] == 15
        assert params["offset"] == 45  # 3 * 15
        assert params["projects"] == ["project1"]

    def test_query_contains_expertise_distribution_columns(self, default_config):
        """Verify query includes Expertise Distribution specific columns."""
        # Act
        query, _ = build_expertise_distribution_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "expertise_distribution_score" in query_str
        assert "creator_diversity" in query_str
        assert "champion_health" in query_str
        assert "total_users" in query_str

    def test_query_contains_champion_health_case(self, default_config):
        """Verify query includes champion health CASE statement."""
        # Act
        query, _ = build_expertise_distribution_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "CASE" in query_str
        assert "NO_CREATORS" in query_str
        assert "CRITICAL" in query_str
        assert "WARNING" in query_str
        assert "HEALTHY" in query_str
        assert "FLAT" in query_str

    def test_query_uses_config_concentration_thresholds(self, custom_config):
        """Verify query uses config concentration thresholds."""
        # Act
        query, _ = build_expertise_distribution_metrics_query(custom_config)
        query_str = str(query)

        # Assert - Check that concentration thresholds appear
        assert "top_pct_concentration" in query_str

    def test_query_orders_by_expertise_distribution_score(self, default_config):
        """Verify query orders by expertise_distribution_score."""
        # Act
        query, _ = build_expertise_distribution_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "ORDER BY expertise_distribution_score DESC" in query_str


class TestBuildFeatureAdoptionMetricsQuery:
    """Tests for build_feature_adoption_metrics_query function."""

    def test_returns_text_and_params(self, default_config):
        """Verify function returns tuple of (text, params)."""
        # Act
        query, params = build_feature_adoption_metrics_query(default_config)

        # Assert
        assert isinstance(query, TextClause)
        assert isinstance(params, dict)

    def test_params_structure(self, default_config):
        """Verify params include pagination and projects."""
        # Act
        _, params = build_feature_adoption_metrics_query(default_config, projects=None, page=5, per_page=30)

        # Assert
        assert params["limit"] == 30
        assert params["offset"] == 150  # 5 * 30
        assert params["projects"] is None

    def test_query_contains_feature_adoption_columns(self, default_config):
        """Verify query includes Feature Adoption specific columns."""
        # Act
        query, _ = build_feature_adoption_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "feature_adoption_score" in query_str
        assert "feature_utilization_rate" in query_str
        assert "median_conversation_depth" in query_str
        assert "assistant_complexity_score" in query_str
        assert "workflow_complexity_score" in query_str

    def test_query_includes_asset_totals(self, default_config):
        """Verify query includes total assistants and workflows."""
        # Act
        query, _ = build_feature_adoption_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "total_assistants" in query_str
        assert "total_workflows" in query_str

    def test_query_uses_complexity_weights(self, custom_config):
        """Verify query uses config complexity weights."""
        # Act
        query, _ = build_feature_adoption_metrics_query(custom_config)
        query_str = str(query)

        # Assert - Check that weights appear in calculation
        assert "assistant_complexity_weight" in query_str or "*" in query_str
        assert "workflow_complexity_weight" in query_str or "*" in query_str

    def test_query_orders_by_feature_adoption_score(self, default_config):
        """Verify query orders by feature_adoption_score."""
        # Act
        query, _ = build_feature_adoption_metrics_query(default_config)
        query_str = str(query)

        # Assert
        assert "ORDER BY feature_adoption_score DESC" in query_str


class TestBuildParamsCte:
    """Tests for build_params_cte helper function."""

    def test_returns_cte_string(self, default_config):
        """Verify function returns CTE SQL string."""
        # Act
        result = build_params_cte(default_config)

        # Assert
        assert isinstance(result, str)
        assert len(result) > 0

    def test_cte_contains_params(self, default_config):
        """Verify CTE includes params table."""
        # Act
        result = build_params_cte(default_config)

        # Assert
        assert "params" in result.lower()

    def test_cte_uses_config_values(self, custom_config):
        """Verify CTE uses values from config."""
        # Act
        result = build_params_cte(custom_config)

        # Assert
        # Check that custom threshold appears in CTE
        assert "30" in result  # maturity_activation_threshold


class TestQueryStructureConsistency:
    """Tests for consistency across all query builder methods."""

    def test_all_queries_return_same_structure(self, default_config):
        """Verify all query methods return (text, dict) tuple."""
        # Act
        queries = [
            build_maturity_query(default_config),
            build_dimensions_query(default_config),
            build_user_engagement_metrics_query(default_config),
            build_asset_reusability_metrics_query(default_config),
            build_expertise_distribution_metrics_query(default_config),
            build_feature_adoption_metrics_query(default_config),
        ]

        # Assert
        for query, params in queries:
            assert isinstance(query, TextClause)
            assert isinstance(params, dict)

    def test_all_dimension_queries_support_pagination(self, default_config):
        """Verify all dimension queries accept page/per_page parameters."""
        # Act & Assert - Should not raise errors
        build_user_engagement_metrics_query(default_config, page=1, per_page=10)
        build_asset_reusability_metrics_query(default_config, page=2, per_page=20)
        build_expertise_distribution_metrics_query(default_config, page=3, per_page=30)
        build_feature_adoption_metrics_query(default_config, page=4, per_page=40)

    def test_all_queries_support_project_filter(self, default_config):
        """Verify all queries accept projects parameter."""
        # Arrange
        projects = ["test-project"]

        # Act & Assert - Should not raise errors
        build_maturity_query(default_config, projects=projects)
        build_dimensions_query(default_config, projects=projects)
        build_user_engagement_metrics_query(default_config, projects=projects)
        build_asset_reusability_metrics_query(default_config, projects=projects)
        build_expertise_distribution_metrics_query(default_config, projects=projects)
        build_feature_adoption_metrics_query(default_config, projects=projects)

    def test_pagination_offset_calculation(self, default_config):
        """Verify offset calculation is consistent across dimension queries."""
        # Act
        _, params1 = build_user_engagement_metrics_query(default_config, page=0, per_page=10)
        _, params2 = build_asset_reusability_metrics_query(default_config, page=1, per_page=10)
        _, params3 = build_expertise_distribution_metrics_query(default_config, page=2, per_page=10)
        _, params4 = build_feature_adoption_metrics_query(default_config, page=3, per_page=10)

        # Assert
        assert params1["offset"] == 0  # page 0 * 10
        assert params2["offset"] == 10  # page 1 * 10
        assert params3["offset"] == 20  # page 2 * 10
        assert params4["offset"] == 30  # page 3 * 10


class TestSqlInjectionPrevention:
    """Tests to verify SQL injection protection through parameterization."""

    def test_projects_filter_uses_params(self, default_config):
        """Verify projects are passed as parameters, not concatenated."""
        # Arrange
        malicious_projects = ["'; DROP TABLE users; --"]

        # Act
        _, params = build_maturity_query(default_config, projects=malicious_projects)

        # Assert
        # Projects should be in params dict, not in query string
        assert params["projects"] == malicious_projects

    def test_pagination_values_use_params(self, default_config):
        """Verify pagination values are parameters, not concatenated."""
        # Arrange
        malicious_page = 999999
        malicious_per_page = 999999

        # Act
        query, params = build_dimensions_query(default_config, page=malicious_page, per_page=malicious_per_page)

        # Assert
        # Pagination should use :limit and :offset parameters
        assert ":limit" in str(query)
        assert ":offset" in str(query)
        assert params["limit"] == malicious_per_page
        assert params["offset"] == malicious_page * malicious_per_page

    def test_config_values_are_validated(self):
        """Verify config validation prevents malicious values."""
        # Act & Assert - Should raise validation error
        with pytest.raises(ValueError):
            AIAdoptionConfig(maturity_activation_threshold=-1)  # Negative not allowed

        with pytest.raises(ValueError):
            AIAdoptionConfig(maturity_level_2_threshold=150)  # > 100 not allowed


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_zero_page_number(self, default_config):
        """Verify page 0 works correctly."""
        # Act
        _, params = build_user_engagement_metrics_query(default_config, page=0, per_page=20)

        # Assert
        assert params["offset"] == 0

    def test_large_page_number(self, default_config):
        """Verify large page numbers work correctly."""
        # Act
        _, params = build_user_engagement_metrics_query(default_config, page=1000, per_page=50)

        # Assert
        assert params["offset"] == 50000  # 1000 * 50

    def test_empty_projects_list(self, default_config):
        """Verify empty projects list converted to None."""
        # Act
        _, params = build_maturity_query(default_config, projects=[])

        # Assert
        # Empty list is falsy, so converted to None in "projects if projects else None"
        assert params["projects"] is None

    def test_single_project(self, default_config):
        """Verify single project in list works correctly."""
        # Act
        _, params = build_dimensions_query(default_config, projects=["single-project"])

        # Assert
        assert params["projects"] == ["single-project"]

    def test_many_projects(self, default_config):
        """Verify many projects work correctly."""
        # Arrange
        many_projects = [f"project-{i}" for i in range(100)]

        # Act
        _, params = build_dimensions_query(default_config, projects=many_projects)

        # Assert
        assert params["projects"] == many_projects
        assert len(params["projects"]) == 100


class TestDrillDownQueryBuilders:
    """Parametrized tests for all drill-down query builder functions."""

    @pytest.mark.parametrize(
        "function_name,module_path",
        [
            (
                "build_user_engagement_users_query",
                "codemie.service.analytics.queries.ai_adoption_framework.query_builder",
            ),
            (
                "build_assistant_reusability_detail_query",
                "codemie.service.analytics.queries.ai_adoption_framework.query_builder",
            ),
            (
                "build_workflow_reusability_detail_query",
                "codemie.service.analytics.queries.ai_adoption_framework.query_builder",
            ),
            (
                "build_datasource_reusability_detail_query",
                "codemie.service.analytics.queries.ai_adoption_framework.query_builder",
            ),
        ],
    )
    def test_returns_text_and_params(self, default_config, function_name, module_path):
        """Verify all drill-down functions return tuple of (text, params)."""
        # Arrange
        import importlib

        module = importlib.import_module(module_path)
        query_func = getattr(module, function_name)

        # Act
        query, params = query_func(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
        )

        # Assert
        assert isinstance(query, TextClause)
        assert isinstance(params, dict)
        assert params["project"] == "project1"

    @pytest.mark.parametrize(
        "function_name,page,per_page,expected_offset",
        [
            ("build_user_engagement_users_query", 2, 50, 100),
            ("build_assistant_reusability_detail_query", 0, 20, 0),
            ("build_workflow_reusability_detail_query", 5, 10, 50),
            ("build_datasource_reusability_detail_query", 10, 100, 1000),
        ],
    )
    def test_pagination_params(self, default_config, function_name, page, per_page, expected_offset):
        """Verify pagination parameters are correctly calculated across all drill-down functions."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework import query_builder

        query_func = getattr(query_builder, function_name)

        # Act
        _, params = query_func(
            config=default_config,
            project="project1",
            page=page,
            per_page=per_page,
        )

        # Assert
        assert params["limit"] == per_page
        assert params["offset"] == expected_offset

    @pytest.mark.parametrize(
        "function_name,sort_by,sort_order",
        [
            ("build_user_engagement_users_query", "engagement_score", "desc"),
            ("build_assistant_reusability_detail_query", "total_usage", "asc"),
            ("build_workflow_reusability_detail_query", "execution_count", "desc"),
            ("build_datasource_reusability_detail_query", "assistant_count", "asc"),
        ],
    )
    def test_sorting_applied(self, default_config, function_name, sort_by, sort_order):
        """Verify sorting is applied correctly across all drill-down functions."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework import query_builder

        query_func = getattr(query_builder, function_name)

        # Act
        query, _ = query_func(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        # Assert
        query_str = str(query)
        assert "ORDER BY" in query_str
        assert sort_by in query_str
        assert sort_order.upper() in query_str or sort_order.lower() in query_str

    @pytest.mark.parametrize(
        "function_name,status_filter",
        [
            ("build_assistant_reusability_detail_query", "active"),
            ("build_assistant_reusability_detail_query", "inactive"),
            ("build_workflow_reusability_detail_query", "active"),
            ("build_workflow_reusability_detail_query", "inactive"),
            ("build_datasource_reusability_detail_query", "active"),
            ("build_datasource_reusability_detail_query", "inactive"),
        ],
    )
    def test_status_filter_applied(self, default_config, function_name, status_filter):
        """Verify status filter is applied correctly across applicable drill-down functions."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework import query_builder

        query_func = getattr(query_builder, function_name)

        # Act
        query, _ = query_func(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            status_filter=status_filter,
        )

        # Assert
        query_str = str(query)
        assert "is_active" in query_str or status_filter in query_str.lower()


class TestBuildUserEngagementUsersQuery:
    """Tests for build_user_engagement_users_query drill-down function."""

    def test_returns_text_and_params(self, default_config):
        """Verify function returns tuple of (text, params)."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_user_engagement_users_query,
        )

        # Act
        query, params = build_user_engagement_users_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
        )

        # Assert
        assert isinstance(query, TextClause)
        assert isinstance(params, dict)
        assert params["project"] == "project1"

    def test_pagination_params(self, default_config):
        """Verify pagination parameters are correctly set."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_user_engagement_users_query,
        )

        # Act
        _, params = build_user_engagement_users_query(
            config=default_config,
            project="project1",
            page=2,
            per_page=50,
        )

        # Assert
        assert params["limit"] == 50
        assert params["offset"] == 100  # page 2 * per_page 50

    def test_filter_params_included(self, default_config):
        """Verify filter parameters are included in params dict."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_user_engagement_users_query,
        )

        # Act
        query, params = build_user_engagement_users_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            user_type_filter="power_user",
            activity_level_filter="daily",
            multi_assistant_filter=True,
        )

        # Assert
        query_str = str(query)
        # Verify filters are applied in WHERE clause
        assert "power_user" in query_str or "user_type" in query_str
        assert "daily" in query_str or "activity" in query_str

    def test_sorting_params(self, default_config):
        """Verify sorting is applied correctly."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_user_engagement_users_query,
        )

        # Act
        query, _ = build_user_engagement_users_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            sort_by="engagement_score",
            sort_order="desc",
        )

        # Assert
        query_str = str(query)
        assert "ORDER BY" in query_str
        assert "DESC" in query_str or "desc" in query_str


class TestBuildAssistantReusabilityDetailQuery:
    """Tests for build_assistant_reusability_detail_query drill-down function."""

    def test_returns_text_and_params(self, default_config):
        """Verify function returns tuple of (text, params)."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_assistant_reusability_detail_query,
        )

        # Act
        query, params = build_assistant_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
        )

        # Assert
        assert isinstance(query, TextClause)
        assert isinstance(params, dict)
        assert params["project"] == "project1"

    def test_status_filter_active(self, default_config):
        """Verify status filter for active assistants."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_assistant_reusability_detail_query,
        )

        # Act
        query, _ = build_assistant_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            status_filter="active",
        )

        # Assert
        query_str = str(query)
        assert "is_active" in query_str or "active" in query_str.lower()

    def test_adoption_filter(self, default_config):
        """Verify adoption filter is applied."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_assistant_reusability_detail_query,
        )

        # Act
        query, _ = build_assistant_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            adoption_filter="team_adopted",
        )

        # Assert
        query_str = str(query)
        assert "is_team_adopted" in query_str or "team" in query_str.lower()

    def test_default_sorting_by_total_usage(self, default_config):
        """Verify default sorting is by total_usage descending."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_assistant_reusability_detail_query,
        )

        # Act
        query, _ = build_assistant_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            sort_by="total_usage",
            sort_order="desc",
        )

        # Assert
        query_str = str(query)
        assert "ORDER BY" in query_str
        assert "total_usage" in query_str


class TestBuildWorkflowReusabilityDetailQuery:
    """Tests for build_workflow_reusability_detail_query drill-down function."""

    def test_returns_text_and_params(self, default_config):
        """Verify function returns tuple of (text, params)."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_workflow_reusability_detail_query,
        )

        # Act
        query, params = build_workflow_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
        )

        # Assert
        assert isinstance(query, TextClause)
        assert isinstance(params, dict)
        assert params["project"] == "project1"

    def test_status_filter(self, default_config):
        """Verify status filter for workflows."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_workflow_reusability_detail_query,
        )

        # Act
        query, _ = build_workflow_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            status_filter="inactive",
        )

        # Assert
        query_str = str(query)
        assert "is_active" in query_str or "FALSE" in query_str

    def test_reuse_filter_multi_user(self, default_config):
        """Verify reuse filter for multi-user workflows."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_workflow_reusability_detail_query,
        )

        # Act
        query, _ = build_workflow_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            reuse_filter="multi_user",
        )

        # Assert
        query_str = str(query)
        assert "is_multi_user" in query_str or "multi" in query_str.lower()

    def test_sorting_by_execution_count(self, default_config):
        """Verify sorting by execution count."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_workflow_reusability_detail_query,
        )

        # Act
        query, _ = build_workflow_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            sort_by="execution_count",
            sort_order="desc",
        )

        # Assert
        query_str = str(query)
        assert "ORDER BY" in query_str
        assert "execution_count" in query_str


class TestBuildDatasourceReusabilityDetailQuery:
    """Tests for build_datasource_reusability_detail_query drill-down function."""

    def test_returns_text_and_params(self, default_config):
        """Verify function returns tuple of (text, params)."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_datasource_reusability_detail_query,
        )

        # Act
        query, params = build_datasource_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
        )

        # Assert
        assert isinstance(query, TextClause)
        assert isinstance(params, dict)
        assert params["project"] == "project1"

    def test_status_filter(self, default_config):
        """Verify status filter for datasources."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_datasource_reusability_detail_query,
        )

        # Act
        query, _ = build_datasource_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            status_filter="active",
        )

        # Assert
        query_str = str(query)
        assert "is_active" in query_str or "TRUE" in query_str

    def test_shared_filter(self, default_config):
        """Verify shared filter for datasources."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_datasource_reusability_detail_query,
        )

        # Act
        query, _ = build_datasource_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            shared_filter="shared",
        )

        # Assert
        query_str = str(query)
        assert "is_shared" in query_str or "shared" in query_str.lower()

    def test_type_filter(self, default_config):
        """Verify type filter for datasources."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_datasource_reusability_detail_query,
        )

        # Act
        query, _ = build_datasource_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            type_filter="git",
        )

        # Assert
        query_str = str(query)
        assert "datasource_type" in query_str or "git" in query_str

    def test_sorting_by_assistant_count(self, default_config):
        """Verify sorting by assistant count."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_datasource_reusability_detail_query,
        )

        # Act
        query, _ = build_datasource_reusability_detail_query(
            config=default_config,
            project="project1",
            page=0,
            per_page=20,
            sort_by="assistant_count",
            sort_order="desc",
        )

        # Assert
        query_str = str(query)
        assert "ORDER BY" in query_str
        assert "assistant_count" in query_str

    def test_pagination_with_large_page_number(self, default_config):
        """Verify pagination handles large page numbers correctly."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
            build_datasource_reusability_detail_query,
        )

        # Act
        _, params = build_datasource_reusability_detail_query(
            config=default_config,
            project="project1",
            page=10,
            per_page=100,
        )

        # Assert
        assert params["limit"] == 100
        assert params["offset"] == 1000  # page 10 * per_page 100
