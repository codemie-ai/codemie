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

"""Unit tests for AI Adoption Framework score expression builders."""

from __future__ import annotations


from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig
from codemie.service.analytics.queries.ai_adoption_framework.score_expressions import (
    build_assistant_complexity_expression,
    build_asset_reusability_score_expression,
    build_expertise_distribution_score_expression,
    build_feature_adoption_score_expression,
    build_user_engagement_score_expression,
    build_workflow_complexity_expression,
)


class TestDimensionScoreExpressions:
    """Tests for D1-D4 score expression builders."""

    def test_d1_expression_includes_all_components(self):
        """Verify D1 expression includes 5 weighted components."""
        config = AIAdoptionConfig()
        expr = build_user_engagement_score_expression(config)

        # Assert structure
        assert "LEAST(" in expr
        assert "COALESCE(" in expr
        assert f"{config.user_engagement_activation_weight}" in expr
        assert f"{config.user_engagement_dau_weight}" in expr
        assert f"{config.user_engagement_mau_weight}" in expr
        assert f"{config.user_engagement_engagement_distribution_weight}" in expr
        assert f"{config.user_engagement_multi_assistant_weight}" in expr

        # Assert CTE references
        assert "um.activated_users" in expr
        assert "um.active_1d" in expr
        assert "um.active_30d" in expr
        assert "um.interaction_stddev" in expr
        assert "mas.multi_user_count" in expr

    def test_d1_expression_respects_custom_weights(self):
        """Verify D1 expression uses config weights correctly."""
        config = AIAdoptionConfig(
            user_engagement_activation_weight=0.40,
            user_engagement_dau_weight=0.10,
            user_engagement_mau_weight=0.20,
            user_engagement_engagement_distribution_weight=0.15,
            user_engagement_multi_assistant_weight=0.15,
            user_engagement_returning_user_weight=0.0,
        )
        expr = build_user_engagement_score_expression(config)

        # Check weights are injected (allow for float formatting)
        assert "0.4" in expr or "0.40" in expr
        assert "0.1" in expr or "0.10" in expr

    def test_d1_expression_has_bounds(self):
        """Verify D1 expression has LEAST wrapper to cap at 1.0."""
        config = AIAdoptionConfig()
        expr = build_user_engagement_score_expression(config)

        # Must have outer LEAST to cap score at 1.0
        assert expr.startswith("LEAST(")
        assert "1.0::numeric)" in expr

    def test_d2_expression_handles_zero_resources(self):
        """Verify D2 expression has zero-resource CASE statement."""
        config = AIAdoptionConfig()
        expr = build_asset_reusability_score_expression(config)

        # Must handle case when no assistants, workflows, or datasources
        assert "WHEN COALESCE(ast.total_assistants, 0) = 0" in expr
        assert "AND COALESCE(ws.total_workflows, 0) = 0" in expr
        assert "AND COALESCE(drs.total_datasources, 0) = 0" in expr
        assert "THEN 0.0::numeric" in expr

    def test_d2_expression_includes_all_components(self):
        """Verify D2 expression includes 5 weighted components."""
        config = AIAdoptionConfig()
        expr = build_asset_reusability_score_expression(config)

        # Assert weights
        assert f"{config.asset_reusability_team_adopted_weight}" in expr
        assert f"{config.asset_reusability_active_assistants_weight}" in expr
        assert f"{config.asset_reusability_workflow_reuse_weight}" in expr
        assert f"{config.asset_reusability_workflow_exec_weight}" in expr
        assert f"{config.asset_reusability_datasource_reuse_weight}" in expr

        # Assert CTE references
        assert "aa.team_adopted_assistants" in expr
        assert "aa.active_assistants" in expr
        assert "wrs.multi_user_workflows" in expr
        assert "wus.active_workflows" in expr
        assert "drs.shared_datasources" in expr

    def test_d3_expression_includes_all_case_branches(self):
        """Verify D3 expression includes concentration thresholds."""
        config = AIAdoptionConfig()
        expr = build_expertise_distribution_score_expression(config)

        # Concentration thresholds
        assert f"{config.expertise_distribution_concentration_critical_threshold}" in expr
        assert f"{config.expertise_distribution_concentration_warning_threshold}" in expr
        assert f"{config.expertise_distribution_concentration_healthy_lower}" in expr
        assert f"{config.expertise_distribution_concentration_healthy_upper}" in expr
        assert f"{config.expertise_distribution_concentration_flat_lower}" in expr
        assert f"{config.expertise_distribution_concentration_flat_upper}" in expr

        # Concentration scores
        assert f"{config.expertise_distribution_concentration_critical_score}" in expr
        assert f"{config.expertise_distribution_concentration_warning_score}" in expr
        assert f"{config.expertise_distribution_concentration_healthy_score}" in expr
        assert f"{config.expertise_distribution_concentration_flat_score}" in expr
        assert f"{config.expertise_distribution_concentration_low_score}" in expr

        # Non-champion multipliers
        assert f"{config.expertise_distribution_non_champion_high_multiplier}" in expr
        assert f"{config.expertise_distribution_non_champion_medium_multiplier}" in expr
        assert f"{config.expertise_distribution_non_champion_low_multiplier}" in expr

        # Non-champion scores
        assert f"{config.expertise_distribution_non_champion_high_score}" in expr
        assert f"{config.expertise_distribution_non_champion_medium_score}" in expr
        assert f"{config.expertise_distribution_non_champion_low_score}" in expr
        assert f"{config.expertise_distribution_non_champion_minimal_score}" in expr

        # Creator diversity thresholds
        assert f"{config.expertise_distribution_creator_diversity_high_threshold}" in expr
        assert f"{config.expertise_distribution_creator_diversity_medium_threshold}" in expr

        # Creator diversity scores
        assert f"{config.expertise_distribution_creator_diversity_high_score}" in expr
        assert f"{config.expertise_distribution_creator_diversity_medium_score}" in expr
        assert f"{config.expertise_distribution_creator_diversity_low_score}" in expr

    def test_d3_expression_uses_workflow_creator_bonus(self):
        """Verify D3 expression includes workflow creator bonus."""
        config = AIAdoptionConfig()
        expr = build_expertise_distribution_score_expression(config)

        # Must include bonus multiplier
        assert f"{config.expertise_distribution_workflow_creator_bonus}" in expr
        assert "ca.workflow_creators" in expr

    def test_d3_expression_references_params_cte(self):
        """Verify D3 expression references params.activation_threshold."""
        config = AIAdoptionConfig()
        expr = build_expertise_distribution_score_expression(config)

        # Must reference params CTE for activation threshold
        assert "p.activation_threshold" in expr

    def test_d4_expression_uses_complexity_helpers(self):
        """Verify D4 expression calls complexity sub-expressions."""
        config = AIAdoptionConfig()
        expr = build_feature_adoption_score_expression(config)

        # Should include complexity expressions (D4 calls the helper functions internally)
        # Verify structure
        assert "LEAST(" in expr
        assert f"{config.feature_adoption_workflow_count_weight}" in expr
        assert f"{config.feature_adoption_complexity_weight}" in expr
        assert f"{config.feature_adoption_conversation_depth_weight}" in expr
        assert f"{config.feature_adoption_assistant_complexity_weight}" in expr
        assert f"{config.feature_adoption_workflow_complexity_weight}" in expr

    def test_d4_expression_includes_workflow_count_tiers(self):
        """Verify D4 expression includes all workflow count thresholds."""
        config = AIAdoptionConfig()
        expr = build_feature_adoption_score_expression(config)

        # Workflow count thresholds
        assert f"{config.feature_adoption_workflow_count_low_threshold}" in expr
        assert f"{config.feature_adoption_workflow_count_medium_threshold}" in expr
        assert f"{config.feature_adoption_workflow_count_high_threshold}" in expr

        # Workflow count scores
        assert f"{config.feature_adoption_workflow_count_none_score}" in expr
        assert f"{config.feature_adoption_workflow_count_low_score}" in expr
        assert f"{config.feature_adoption_workflow_count_medium_score}" in expr
        assert f"{config.feature_adoption_workflow_count_high_score}" in expr
        assert f"{config.feature_adoption_workflow_count_very_high_score}" in expr

    def test_d4_expression_includes_conversation_depth(self):
        """Verify D4 expression includes conversation depth calculation."""
        config = AIAdoptionConfig()
        expr = build_feature_adoption_score_expression(config)

        # Must include conversation depth normalizer
        assert f"{config.feature_adoption_conversation_depth_normalizer}" in expr
        assert "cd.median_messages" in expr


class TestComplexityExpressions:
    """Tests for assistant/workflow complexity expressions."""

    def test_assistant_complexity_includes_all_levels(self):
        """Verify assistant complexity has 4 levels + bonus."""
        config = AIAdoptionConfig()
        expr = build_assistant_complexity_expression(config)

        # Complexity level weights
        assert f"{config.feature_adoption_complexity_simple}" in expr
        assert f"{config.feature_adoption_complexity_basic}" in expr
        assert f"{config.feature_adoption_complexity_advanced}" in expr
        assert f"{config.feature_adoption_complexity_complex}" in expr
        assert f"{config.feature_adoption_complexity_multi_feature_bonus}" in expr

        # Assert CTE references
        assert "fs.simple_assistants" in expr
        assert "fs.basic_assistants" in expr
        assert "fs.advanced_assistants" in expr
        assert "fs.complex_assistants" in expr
        assert "fs.multi_datasource_types" in expr
        assert "fs.total_assistants" in expr

    def test_workflow_complexity_includes_all_levels(self):
        """Verify workflow complexity has 4 levels + bonus."""
        config = AIAdoptionConfig()
        expr = build_workflow_complexity_expression(config)

        # Complexity level weights
        assert f"{config.feature_adoption_complexity_simple}" in expr
        assert f"{config.feature_adoption_complexity_basic}" in expr
        assert f"{config.feature_adoption_complexity_advanced}" in expr
        assert f"{config.feature_adoption_complexity_complex}" in expr
        assert f"{config.feature_adoption_complexity_multi_feature_bonus}" in expr

        # Assert CTE references
        assert "wcs.simple_workflows" in expr
        assert "wcs.basic_workflows" in expr
        assert "wcs.advanced_workflows" in expr
        assert "wcs.complex_workflows" in expr
        assert "wcs.multi_assistant_workflows" in expr
        assert "wcs.total_workflows" in expr

    def test_complexity_expressions_use_same_weights(self):
        """Verify both complexity expressions use same level weights."""
        config = AIAdoptionConfig()
        ast_expr = build_assistant_complexity_expression(config)
        wf_expr = build_workflow_complexity_expression(config)

        # Both should use same complexity weights
        for weight in [
            config.feature_adoption_complexity_simple,
            config.feature_adoption_complexity_basic,
            config.feature_adoption_complexity_advanced,
            config.feature_adoption_complexity_complex,
        ]:
            assert str(weight) in ast_expr
            assert str(weight) in wf_expr

    def test_complexity_expressions_have_nullif_protection(self):
        """Verify complexity expressions protect against division by zero."""
        config = AIAdoptionConfig()
        ast_expr = build_assistant_complexity_expression(config)
        wf_expr = build_workflow_complexity_expression(config)

        # Both should use NULLIF to prevent division by zero
        assert "NULLIF(fs.total_assistants, 0)" in ast_expr
        assert "NULLIF(wcs.total_workflows, 0)" in wf_expr


class TestExpressionConsistency:
    """Tests to ensure expressions produce consistent SQL."""

    def test_all_expressions_return_strings(self):
        """Verify all builders return string expressions."""
        config = AIAdoptionConfig()

        assert isinstance(build_user_engagement_score_expression(config), str)
        assert isinstance(build_asset_reusability_score_expression(config), str)
        assert isinstance(build_expertise_distribution_score_expression(config), str)
        assert isinstance(build_feature_adoption_score_expression(config), str)
        assert isinstance(build_assistant_complexity_expression(config), str)
        assert isinstance(build_workflow_complexity_expression(config), str)

    def test_expressions_not_empty(self):
        """Verify expressions are non-empty."""
        config = AIAdoptionConfig()

        expressions = [
            build_user_engagement_score_expression(config),
            build_asset_reusability_score_expression(config),
            build_expertise_distribution_score_expression(config),
            build_feature_adoption_score_expression(config),
            build_assistant_complexity_expression(config),
            build_workflow_complexity_expression(config),
        ]

        for expr in expressions:
            assert len(expr) > 0
            assert expr.strip() != ""

    def test_expressions_contain_no_placeholders(self):
        """Verify expressions don't contain TODO or placeholder text."""
        config = AIAdoptionConfig()

        expressions = [
            build_user_engagement_score_expression(config),
            build_asset_reusability_score_expression(config),
            build_expertise_distribution_score_expression(config),
            build_feature_adoption_score_expression(config),
            build_assistant_complexity_expression(config),
            build_workflow_complexity_expression(config),
        ]

        for expr in expressions:
            assert "TODO" not in expr
            assert "FIXME" not in expr
            assert "XXX" not in expr
            assert "placeholder" not in expr.lower()

    def test_dimension_expressions_have_proper_numeric_casts(self):
        """Verify dimension expressions cast scores to numeric type."""
        config = AIAdoptionConfig()

        expressions = [
            build_user_engagement_score_expression(config),
            build_asset_reusability_score_expression(config),
            build_expertise_distribution_score_expression(config),
            build_feature_adoption_score_expression(config),
        ]

        for expr in expressions:
            # Should have numeric casts for PostgreSQL type safety
            assert "::numeric" in expr

    def test_expressions_use_coalesce_for_nulls(self):
        """Verify expressions use COALESCE to handle NULL values."""
        config = AIAdoptionConfig()

        expressions = [
            build_user_engagement_score_expression(config),
            build_asset_reusability_score_expression(config),
            build_expertise_distribution_score_expression(config),
            build_feature_adoption_score_expression(config),
            build_assistant_complexity_expression(config),
            build_workflow_complexity_expression(config),
        ]

        for expr in expressions:
            # All expressions should use COALESCE for NULL safety
            assert "COALESCE(" in expr


class TestConfigWeightValidation:
    """Tests to ensure expressions respect config validation."""

    def test_expressions_work_with_validated_config(self):
        """Verify expressions work with validated config."""
        config = AIAdoptionConfig()

        # All expressions should build successfully
        build_user_engagement_score_expression(config)
        build_asset_reusability_score_expression(config)
        build_expertise_distribution_score_expression(config)
        build_feature_adoption_score_expression(config)
        build_assistant_complexity_expression(config)
        build_workflow_complexity_expression(config)

    def test_expressions_inject_custom_weights(self):
        """Verify expressions inject custom config weights."""
        config = AIAdoptionConfig(
            user_engagement_activation_weight=0.40,
            user_engagement_dau_weight=0.10,
            user_engagement_mau_weight=0.25,
            user_engagement_engagement_distribution_weight=0.10,
            user_engagement_multi_assistant_weight=0.15,
            user_engagement_returning_user_weight=0.0,
        )

        expr = build_user_engagement_score_expression(config)

        # Custom weights should be present
        assert "0.4" in expr or "0.40" in expr
        assert "0.25" in expr
        assert "0.15" in expr


class TestExpressionFormatting:
    """Tests for SQL expression formatting and style."""

    def test_dimension_expressions_have_consistent_wrapping(self):
        """Verify dimension expressions use consistent LEAST/COALESCE wrapping."""
        config = AIAdoptionConfig()

        # D1, D3, D4 should start with LEAST
        for expr in [
            build_user_engagement_score_expression(config),
            build_feature_adoption_score_expression(config),
        ]:
            assert expr.strip().startswith("LEAST(")

        # D2 should start with CASE (due to zero-resource check)
        d2_expr = build_asset_reusability_score_expression(config)
        assert d2_expr.strip().startswith("CASE")

        # D3 should start with CASE (due to zero-user check)
        d3_expr = build_expertise_distribution_score_expression(config)
        assert d3_expr.strip().startswith("CASE")

    def test_complexity_expressions_have_parentheses(self):
        """Verify complexity expressions are wrapped in parentheses."""
        config = AIAdoptionConfig()

        for expr in [
            build_assistant_complexity_expression(config),
            build_workflow_complexity_expression(config),
        ]:
            # Should be wrapped in parentheses for safe embedding
            assert expr.strip().startswith("(")
            assert expr.strip().endswith(")")
