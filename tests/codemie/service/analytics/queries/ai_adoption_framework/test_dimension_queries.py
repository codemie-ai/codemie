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

"""Tests for dimension_queries CTE builder functions."""

from __future__ import annotations

import pytest


class TestDimensionCTEBuilders:
    """Smoke tests for dimension CTE builder functions."""

    @pytest.mark.parametrize(
        "function_name,kwargs",
        [
            ("build_user_metrics_cte", {"include_creators": False}),
            ("build_user_metrics_cte", {"include_creators": True}),
            ("build_multi_assistant_users_cte", {}),
            ("build_multi_assistant_stats_cte", {}),
            ("build_concentration_cte", {"include_creators": False}),
            ("build_concentration_cte", {"include_creators": True}),
            ("build_assistant_usage_cte", {"projects_param": True, "single_project": False}),
            ("build_assistant_usage_cte", {"projects_param": True, "single_project": True}),
            ("build_assistant_usage_cte", {"projects_param": False, "single_project": False}),
            ("build_assistant_adoption_cte", {}),
            ("build_user_engagement_users_detail_cte", {"single_project": False}),
            ("build_user_engagement_users_detail_cte", {"single_project": True}),
        ],
    )
    def test_cte_builder_returns_valid_sql(self, function_name, kwargs):
        """Verify CTE builders return non-empty SQL strings with proper structure."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework import dimension_queries

        func = getattr(dimension_queries, function_name)

        # Act
        sql = func(**kwargs)

        # Assert
        assert isinstance(sql, str)
        assert len(sql) > 0
        assert "SELECT" in sql.upper()
        assert "AS (" in sql or "AS(" in sql

    @pytest.mark.parametrize(
        "function_name,expected_cte_name",
        [
            ("build_user_metrics_cte", "user_metrics"),
            ("build_multi_assistant_users_cte", "multi_assistant_users"),
            ("build_multi_assistant_stats_cte", "multi_assistant_stats"),
            ("build_concentration_cte", "concentration"),
            ("build_assistant_usage_cte", "assistant_usage"),
            ("build_assistant_adoption_cte", "assistant_adoption"),
            ("build_user_engagement_users_detail_cte", "user_engagement_users"),
        ],
    )
    def test_cte_builder_contains_expected_cte_name(self, function_name, expected_cte_name):
        """Verify CTE builders include the expected CTE name."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework import dimension_queries

        func = getattr(dimension_queries, function_name)

        # Act - Call with default parameters
        if function_name == "build_assistant_usage_cte":
            sql = func(projects_param=True, single_project=False)
        elif function_name == "build_user_engagement_users_detail_cte":
            sql = func(single_project=False)
        elif function_name in ["build_user_metrics_cte", "build_concentration_cte"]:
            sql = func(include_creators=False)
        else:
            sql = func()

        # Assert
        assert expected_cte_name in sql.lower()

    @pytest.mark.parametrize(
        "function_name,include_creators,expected_source",
        [
            ("build_user_metrics_cte", False, "user_stats"),
            ("build_user_metrics_cte", True, "user_stats_all"),
            ("build_concentration_cte", False, "user_stats"),
            ("build_concentration_cte", True, "user_stats_all"),
        ],
    )
    def test_include_creators_flag_uses_correct_source_cte(self, function_name, include_creators, expected_source):
        """Verify include_creators flag selects correct source CTE."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework import dimension_queries

        func = getattr(dimension_queries, function_name)

        # Act
        sql = func(include_creators=include_creators)

        # Assert
        assert expected_source in sql.lower()

    def test_assistant_usage_cte_single_project_mode(self):
        """Verify assistant_usage_cte handles single_project mode correctly."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.dimension_queries import (
            build_assistant_usage_cte,
        )

        # Act
        sql_single = build_assistant_usage_cte(projects_param=True, single_project=True)
        sql_multi = build_assistant_usage_cte(projects_param=True, single_project=False)

        # Assert
        assert isinstance(sql_single, str)
        assert isinstance(sql_multi, str)
        assert len(sql_single) > 0
        assert len(sql_multi) > 0
        # Single project mode should have simpler filtering
        assert "project = :project" in sql_single.lower() or ":project" in sql_single.lower()

    def test_user_engagement_users_detail_cte_structure(self):
        """Verify user_engagement_users_detail_cte has expected columns."""
        # Arrange
        from codemie.service.analytics.queries.ai_adoption_framework.dimension_queries import (
            build_user_engagement_users_detail_cte,
        )

        # Act
        sql = build_user_engagement_users_detail_cte(single_project=True)

        # Assert
        # Check for expected column names
        sql_lower = sql.lower()
        assert "user_id" in sql_lower
        assert "user_name" in sql_lower or "username" in sql_lower
        assert "engagement_score" in sql_lower or "score" in sql_lower
        assert "total_interactions" in sql_lower or "interaction" in sql_lower
