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

"""Composite scoring queries for AI Adoption Framework.

Provides the composite_scores CTE which is the single source of truth
for adoption index calculation. Combines all dimension scores using configured weights.
"""

from __future__ import annotations

from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig
from codemie.service.analytics.queries.ai_adoption_framework.score_expressions import (
    build_user_engagement_score_expression,
    build_asset_reusability_score_expression,
    build_expertise_distribution_score_expression,
    build_feature_adoption_score_expression,
)


def build_composite_scores_cte(config: AIAdoptionConfig) -> str:
    """Generate composite_scores CTE with dynamic weight injection.

    This is the SINGLE SOURCE OF TRUTH for adoption index calculation.
    All dimension scores are combined here using configured weights.

    Args:
        config: AIAdoptionConfig with all weights

    Returns:
        SQL string for composite_scores CTE

    Depends on:
        - assistant_stats, workflow_stats, params
        - user_metrics, multi_assistant_stats, concentration (User Engagement)
        - assistant_adoption, workflow_reuse_stats, workflow_utilization_stats, datasource_reuse_stats (Asset Reusability)
        - creator_activity, non_champion_stats (Expertise Distribution)
        - conversation_depth, feature_stats, workflow_complexity_stats (Feature Adoption)
    """
    # Build dimension score expressions
    user_engagement_expr = build_user_engagement_score_expression(config)
    asset_reusability_expr = build_asset_reusability_score_expression(config)
    expertise_distribution_expr = build_expertise_distribution_score_expression(config)
    feature_adoption_expr = build_feature_adoption_score_expression(config)

    return f"""
composite_scores AS (
    SELECT
        ast.project,
        ROUND(
            LEAST(
                (
                    -- User Engagement ({config.adoption_index_user_engagement_weight * 100:.0f}%)
                    -- activation({config.user_engagement_activation_weight * 100:.0f}%) + DAU({config.user_engagement_dau_weight * 100:.0f}%) + MAU({config.user_engagement_mau_weight * 100:.0f}%) + engagement_distribution({config.user_engagement_engagement_distribution_weight * 100:.0f}%) + multi_assistant({config.user_engagement_multi_assistant_weight * 100:.0f}%)
                    {user_engagement_expr} * {config.adoption_index_user_engagement_weight} +

                    -- Asset Reusability ({config.adoption_index_asset_reusability_weight * 100:.0f}%)
                    -- team_adopted({config.asset_reusability_team_adopted_weight * 100:.0f}%) + active_assistants({config.asset_reusability_active_assistants_weight * 100:.0f}%) +
                    -- wf_reuse({config.asset_reusability_workflow_reuse_weight * 100:.0f}%) + wf_exec({config.asset_reusability_workflow_exec_weight * 100:.0f}%) + ds_reuse({config.asset_reusability_datasource_reuse_weight * 100:.0f}%)
                    {asset_reusability_expr} * {config.adoption_index_asset_reusability_weight} +

                    -- Expertise Distribution ({config.adoption_index_expertise_distribution_weight * 100:.0f}%)
                    -- concentration({config.expertise_distribution_concentration_weight * 100:.0f}%) + non_champion({config.expertise_distribution_non_champion_weight * 100:.0f}%) + creator({config.expertise_distribution_creator_diversity_weight * 100:.0f}%)
                    {expertise_distribution_expr} * {config.adoption_index_expertise_distribution_weight} +

                    -- Feature Adoption ({config.adoption_index_feature_adoption_weight * 100:.0f}%)
                    -- workflow_count({config.feature_adoption_workflow_count_weight * 100:.0f}%) + complexity({config.feature_adoption_complexity_weight * 100:.0f}%) + depth({config.feature_adoption_conversation_depth_weight * 100:.0f}%)
                    {feature_adoption_expr} * {config.adoption_index_feature_adoption_weight}
                ) * 100
            , 100::numeric), 1
        ) AS adoption_index
    FROM assistant_stats ast
    CROSS JOIN params p
    LEFT JOIN workflow_stats ws ON ast.project = ws.project
    LEFT JOIN creator_activity ca ON ast.project = ca.project
    LEFT JOIN user_metrics um ON ast.project = um.project
    LEFT JOIN multi_assistant_stats mas ON ast.project = mas.project
    LEFT JOIN concentration con ON ast.project = con.project
    LEFT JOIN assistant_adoption aa ON ast.project = aa.project
    LEFT JOIN workflow_reuse_stats wrs ON ast.project = wrs.project
    LEFT JOIN workflow_utilization_stats wus ON ast.project = wus.project
    LEFT JOIN datasource_reuse_stats drs ON ast.project = drs.project
    LEFT JOIN non_champion_stats ncs ON ast.project = ncs.project
    LEFT JOIN conversation_depth cd ON ast.project = cd.project
    LEFT JOIN feature_stats fs ON ast.project = fs.project
    LEFT JOIN workflow_complexity_stats wcs ON ast.project = wcs.project
)
"""
