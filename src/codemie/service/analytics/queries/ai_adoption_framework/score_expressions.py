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

"""Reusable SQL expression builders for AI Adoption Framework scoring.

Provides helper functions that generate SQL expressions for:
- D1-D4 dimension score calculations
- Assistant/workflow complexity scoring
- Engagement distribution metrics

These expressions are used across multiple query builder methods to ensure
consistency and eliminate duplication.
"""

from __future__ import annotations

from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig


# =============================================================================
# DIMENSION SCORE EXPRESSIONS
# =============================================================================


def build_user_engagement_score_expression(config: AIAdoptionConfig) -> str:
    """Generate User Engagement score SQL expression.

    Combines 6 components with configured weights (must sum to 1.0):
    - User Activation Rate (25%) — users reaching meaningful usage threshold
    - DAU Ratio (5%) — point-in-time daily pulse, minimal weight due to volatility
    - MAU Ratio (25%) — stable monthly engagement breadth
    - Engagement Distribution (15%) — log-normalized CV, evenness of usage across users
    - Multi-Assistant Rate (10%) — exploration breadth
    - Returning User Rate (20%) — retention signal, users who came back after first use

    Engagement Distribution Metric Details:
    ----------------------------------------
    Formula: 1 - (stddev(LN(usage+1)) / mean(LN(usage+1)))

    Log-normalized inverse coefficient of variation. LN transformation compresses
    power-law distributions so the metric produces meaningful differentiation.

    Edge Cases:
    - 0 users: Returns 0.0 (no engagement to measure)
    - mean = 0: Returns 0.5 (neutral score, no variation possible)
    - All users equal usage: Returns 1.0 (perfect distribution, stddev=0)

    Args:
        config: AIAdoptionConfig with component weights

    Returns:
        SQL expression string calculating User Engagement score (0-1 scale)

    Example:
        >>> config = AIAdoptionConfig()
        >>> expr = build_user_engagement_score_expression(config)
        >>> query = f"SELECT {expr} * 100 AS user_engagement_score FROM ..."
    """
    return f"""LEAST(
        COALESCE(
            COALESCE(um.activated_users, 0) / NULLIF(um.total_users, 0) * {config.user_engagement_activation_weight} +
            COALESCE(um.active_1d, 0) / NULLIF(um.total_users, 0) * {config.user_engagement_dau_weight} +
            COALESCE(um.active_30d, 0) / NULLIF(um.total_users, 0) * {config.user_engagement_mau_weight} +
            -- Engagement Distribution: Log-normalized inverse CV
            -- LN(x+1) transformation handles power-law usage distributions
            -- Higher score = more even distribution = healthier adoption
            (CASE
                WHEN COALESCE(um.total_users, 0) = 0 THEN 0.0::numeric  -- No users
                WHEN COALESCE(um.interaction_mean, 0) > 0 THEN
                    GREATEST(0::numeric, LEAST(1::numeric,
                        1::numeric - (COALESCE(um.interaction_stddev, 0) / um.interaction_mean)
                    ))
                ELSE 0.5::numeric  -- Mean=0, neutral score
            END) * {config.user_engagement_engagement_distribution_weight} +
            COALESCE(mas.multi_user_count, 0) / NULLIF(um.total_users, 0) * {config.user_engagement_multi_assistant_weight} +
            -- Returning User Rate: users who returned within the configured window after first use
            COALESCE(um.returning_users, 0) / NULLIF(um.total_users, 0) * {config.user_engagement_returning_user_weight}
        , 0)
    , 1.0::numeric)"""


def build_asset_reusability_score_expression(config: AIAdoptionConfig) -> str:
    """Generate Asset Reusability score SQL expression.

    Combines 5 components with configured weights:
    - Team Adopted Assistants (30%)
    - Active Assistants (25%)
    - Workflow Reuse (25%)
    - Workflow Execution (10%)
    - Datasource Reuse (10%)

    Args:
        config: AIAdoptionConfig with component weights

    Returns:
        SQL expression string calculating Asset Reusability score (0-1 scale)

    Example:
        >>> config = AIAdoptionConfig()
        >>> expr = build_asset_reusability_score_expression(config)
        >>> query = f"SELECT {expr} * 100 AS asset_reusability_score FROM ..."
    """
    return f"""CASE
        WHEN COALESCE(ast.total_assistants, 0) = 0
            AND COALESCE(ws.total_workflows, 0) = 0
            AND COALESCE(drs.total_datasources, 0) = 0 THEN 0.0::numeric
        ELSE LEAST(
            COALESCE(COALESCE(aa.team_adopted_assistants, 0) / NULLIF(ast.total_assistants, 0), 0) * {config.asset_reusability_team_adopted_weight} +
            COALESCE(COALESCE(aa.active_assistants, 0) / NULLIF(ast.total_assistants, 0), 0) * {config.asset_reusability_active_assistants_weight} +
            COALESCE(COALESCE(wrs.multi_user_workflows, 0) / NULLIF(ws.total_workflows, 0), 0) * {config.asset_reusability_workflow_reuse_weight} +
            COALESCE(COALESCE(wus.active_workflows, 0) / NULLIF(ws.total_workflows, 0), 0) * {config.asset_reusability_workflow_exec_weight} +
            COALESCE(COALESCE(drs.shared_datasources, 0) / NULLIF(drs.total_datasources, 0), 0) * {config.asset_reusability_datasource_reuse_weight}
        , 1.0::numeric)
    END"""


def build_expertise_distribution_score_expression(config: AIAdoptionConfig) -> str:
    """Generate Expertise Distribution score SQL expression.

    Combines 3 components with configured weights:
    - Concentration (35%)
    - Non-Champion Activity (40%)
    - Creator Diversity (25%)

    Args:
        config: AIAdoptionConfig with component weights and thresholds

    Returns:
        SQL expression string calculating Expertise Distribution score (0-1 scale)

    Example:
        >>> config = AIAdoptionConfig()
        >>> expr = build_expertise_distribution_score_expression(config)
        >>> query = f"SELECT {expr} * 100 AS expertise_distribution_score FROM ..."
    """
    return f"""CASE
        WHEN COALESCE(um.total_users, 0) = 0 THEN 0.0::numeric
        ELSE LEAST(
            COALESCE(
                CASE
                    WHEN COALESCE(con.top_pct_concentration, 0) > {float(config.expertise_distribution_concentration_critical_threshold)} THEN {config.expertise_distribution_concentration_critical_score}::numeric
                    WHEN COALESCE(con.top_pct_concentration, 0) > {float(config.expertise_distribution_concentration_warning_threshold)} THEN {config.expertise_distribution_concentration_warning_score}::numeric
                    WHEN COALESCE(con.top_pct_concentration, 0) BETWEEN {float(config.expertise_distribution_concentration_healthy_lower)} AND {float(config.expertise_distribution_concentration_healthy_upper)} THEN {config.expertise_distribution_concentration_healthy_score}::numeric
                    WHEN COALESCE(con.top_pct_concentration, 0) BETWEEN {float(config.expertise_distribution_concentration_flat_lower)} AND {float(config.expertise_distribution_concentration_flat_upper)} THEN {config.expertise_distribution_concentration_flat_score}::numeric
                    ELSE {config.expertise_distribution_concentration_low_score}::numeric
                END * {config.expertise_distribution_concentration_weight} +
                CASE
                    WHEN COALESCE(ncs.bottom_50_median, 0) >= p.activation_threshold * {float(config.expertise_distribution_non_champion_high_multiplier)} THEN {config.expertise_distribution_non_champion_high_score}::numeric
                    WHEN COALESCE(ncs.bottom_50_median, 0) >= p.activation_threshold * {float(config.expertise_distribution_non_champion_medium_multiplier)} THEN {config.expertise_distribution_non_champion_medium_score}::numeric
                    WHEN COALESCE(ncs.bottom_50_median, 0) >= p.activation_threshold * {float(config.expertise_distribution_non_champion_low_multiplier)} THEN {config.expertise_distribution_non_champion_low_score}::numeric
                    ELSE {config.expertise_distribution_non_champion_minimal_score}::numeric
                END * {config.expertise_distribution_non_champion_weight} +
                CASE
                    WHEN (ca.unique_creators + ca.workflow_creators * {float(config.expertise_distribution_workflow_creator_bonus)}) / NULLIF(um.total_users, 1) >= {float(config.expertise_distribution_creator_diversity_high_threshold)} THEN {config.expertise_distribution_creator_diversity_high_score}::numeric
                    WHEN (ca.unique_creators + ca.workflow_creators * {float(config.expertise_distribution_workflow_creator_bonus)}) / NULLIF(um.total_users, 1) >= {float(config.expertise_distribution_creator_diversity_medium_threshold)} THEN {config.expertise_distribution_creator_diversity_medium_score}::numeric
                    ELSE {config.expertise_distribution_creator_diversity_low_score}::numeric
                END * {config.expertise_distribution_creator_diversity_weight}
            , 0)
        , 1.0::numeric)
    END"""


def build_feature_adoption_score_expression(config: AIAdoptionConfig) -> str:
    """Generate Feature Adoption score SQL expression.

    Combines 3 components with configured weights:
    - Workflow Count (30%)
    - Complexity (50%)
    - Conversation Depth (20%)

    Args:
        config: AIAdoptionConfig with component weights

    Returns:
        SQL expression string calculating Feature Adoption score (0-1 scale)

    Example:
        >>> config = AIAdoptionConfig()
        >>> expr = build_feature_adoption_score_expression(config)
        >>> query = f"SELECT {expr} * 100 AS feature_adoption_score FROM ..."
    """
    assistant_complexity = build_assistant_complexity_expression(config)
    workflow_complexity = build_workflow_complexity_expression(config)

    return f"""LEAST(
        COALESCE(
            CASE
                WHEN COALESCE(ws.total_workflows, 0) = 0 THEN {config.feature_adoption_workflow_count_none_score}::numeric
                WHEN COALESCE(ws.total_workflows, 0) <= {int(config.feature_adoption_workflow_count_low_threshold)} THEN {config.feature_adoption_workflow_count_low_score}::numeric
                WHEN COALESCE(ws.total_workflows, 0) <= {int(config.feature_adoption_workflow_count_medium_threshold)} THEN {config.feature_adoption_workflow_count_medium_score}::numeric
                WHEN COALESCE(ws.total_workflows, 0) <= {int(config.feature_adoption_workflow_count_high_threshold)} THEN {config.feature_adoption_workflow_count_high_score}::numeric
                ELSE {config.feature_adoption_workflow_count_very_high_score}::numeric
            END * {config.feature_adoption_workflow_count_weight} +
            (
                {assistant_complexity} * {config.feature_adoption_assistant_complexity_weight} +
                {workflow_complexity} * {config.feature_adoption_workflow_complexity_weight}
            ) * {config.feature_adoption_complexity_weight} +
            LEAST(COALESCE(cd.median_messages, 0) / {config.feature_adoption_conversation_depth_normalizer}::numeric, 1.0::numeric) * {config.feature_adoption_conversation_depth_weight}
        , 0)
    , 1.0::numeric)"""


# =============================================================================
# COMPLEXITY SCORING SUB-EXPRESSIONS
# =============================================================================


def build_assistant_complexity_expression(config: AIAdoptionConfig) -> str:
    """Generate assistant complexity scoring expression.

    Scores assistants based on feature usage (tools, datasources, MCP):
    - Simple: No features (0.0)
    - Basic: 1 feature type (0.33)
    - Advanced: 2 feature types (0.67)
    - Complex: All 3 feature types (1.0)
    - Bonus: Multi-datasource types (+0.15)

    Args:
        config: AIAdoptionConfig with complexity weights

    Returns:
        SQL expression string calculating assistant complexity score (0-1 scale)

    Example:
        >>> config = AIAdoptionConfig()
        >>> expr = build_assistant_complexity_expression(config)
        >>> query = f"SELECT {expr} * 100 AS assistant_complexity FROM ..."
    """
    return f"""(
        COALESCE(COALESCE(fs.simple_assistants, 0) / NULLIF(fs.total_assistants, 0), 0) * {config.feature_adoption_complexity_simple} +
        COALESCE(COALESCE(fs.basic_assistants, 0) / NULLIF(fs.total_assistants, 0), 0) * {config.feature_adoption_complexity_basic} +
        COALESCE(COALESCE(fs.advanced_assistants, 0) / NULLIF(fs.total_assistants, 0), 0) * {config.feature_adoption_complexity_advanced} +
        COALESCE(COALESCE(fs.complex_assistants, 0) / NULLIF(fs.total_assistants, 0), 0) * {config.feature_adoption_complexity_complex} +
        COALESCE(COALESCE(fs.multi_datasource_types, 0) / NULLIF(fs.total_assistants, 0), 0) * {config.feature_adoption_complexity_multi_feature_bonus}
    )"""


def build_workflow_complexity_expression(config: AIAdoptionConfig) -> str:
    """Generate workflow complexity scoring expression.

    Scores workflows based on states, tools, and assistants:
    - Simple: 1-2 states, no tools (0.0)
    - Basic: 3-5 states OR has tools (0.33)
    - Advanced: 6-10 states OR multiple tools (0.67)
    - Complex: 10+ states AND multiple tools (1.0)
    - Bonus: Multi-assistant workflows (+0.15)

    Args:
        config: AIAdoptionConfig with complexity weights

    Returns:
        SQL expression string calculating workflow complexity score (0-1 scale)

    Example:
        >>> config = AIAdoptionConfig()
        >>> expr = build_workflow_complexity_expression(config)
        >>> query = f"SELECT {expr} * 100 AS workflow_complexity FROM ..."
    """
    return f"""(
        COALESCE(COALESCE(wcs.simple_workflows, 0) / NULLIF(wcs.total_workflows, 0), 0) * {config.feature_adoption_complexity_simple} +
        COALESCE(COALESCE(wcs.basic_workflows, 0) / NULLIF(wcs.total_workflows, 0), 0) * {config.feature_adoption_complexity_basic} +
        COALESCE(COALESCE(wcs.advanced_workflows, 0) / NULLIF(wcs.total_workflows, 0), 0) * {config.feature_adoption_complexity_advanced} +
        COALESCE(COALESCE(wcs.complex_workflows, 0) / NULLIF(wcs.total_workflows, 0), 0) * {config.feature_adoption_complexity_complex} +
        COALESCE(COALESCE(wcs.multi_assistant_workflows, 0) / NULLIF(wcs.total_workflows, 0), 0) * {config.feature_adoption_complexity_multi_feature_bonus}
    )"""
