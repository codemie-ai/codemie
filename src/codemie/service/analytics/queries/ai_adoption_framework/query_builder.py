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

"""Query composition orchestrator for AI Adoption Framework.

Provides pure functions that compose CTEs for different endpoint types.
Each function builds a complete query by selecting and ordering the appropriate CTEs.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from codemie.service.analytics.queries.ai_adoption_framework import base_queries, composite_queries, dimension_queries
from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig
from codemie.service.analytics.queries.ai_adoption_framework.score_expressions import (
    build_assistant_complexity_expression,
    build_user_engagement_score_expression,
    build_asset_reusability_score_expression,
    build_expertise_distribution_score_expression,
    build_feature_adoption_score_expression,
    build_workflow_complexity_expression,
)

SQL_PROJECT_FILTER = "project = :project"
SQL_AND = " AND "
SQL_IS_ACTIVE_TRUE = "is_active = TRUE"
SQL_IS_ACTIVE_FALSE = "is_active = FALSE"


def build_params_cte(config: AIAdoptionConfig) -> str:
    """Generate params CTE with configuration values.

    This is a convenience wrapper around base_queries.build_params_cte()
    to match the naming convention used in ctes list.

    Args:
        config: AIAdoptionConfig with all parameters

    Returns:
        SQL string for params CTE
    """
    return base_queries.build_params_cte(config)


def build_maturity_query(
    config: AIAdoptionConfig,
    projects: list[str] | None = None,
) -> tuple[text, dict]:
    """Build query for /ai-maturity-overview endpoint.

    Returns single aggregated row with adoption_index, maturity_level, and dimension scores.
    Aggregates across all accessible projects for dashboard cards.

    Args:
        config: AIAdoptionConfig with all parameters
        projects: Filter by specific projects (None for all accessible)

    Returns:
        Tuple of (sqlalchemy.text query, params dict)
    """
    # Build CTEs (only what's needed for scoring)
    # NOTE: We need TWO user_stats CTEs for different purposes:
    # - user_stats: Conversations only (for D1 MAU/DAU/activation calculations)
    # - user_stats_all: All activity including creators (for total_users count and D3)
    ctes = [
        build_params_cte(config),
        base_queries.build_assistant_stats_cte(),
        base_queries.build_workflow_stats_cte(),
        base_queries.build_user_stats_cte(include_creators=False),  # D1: Conversations only → CTE named "user_stats"
        base_queries.build_user_stats_cte(include_creators=True),  # All users → CTE named "user_stats_all"
        base_queries.build_filtered_projects_cte(),  # Project filtering by minimum users
        base_queries.build_creator_activity_cte(config),
        # User Engagement CTEs (calculate across ALL users for maturity overview)
        dimension_queries.build_user_metrics_cte(include_creators=True),
        dimension_queries.build_multi_assistant_users_cte(),
        dimension_queries.build_multi_assistant_stats_cte(),
        dimension_queries.build_concentration_cte(include_creators=True),
        # Asset Reusability CTEs
        dimension_queries.build_assistant_usage_cte(),
        dimension_queries.build_assistant_adoption_cte(),
        dimension_queries.build_workflow_reuse_stats_cte(config),
        dimension_queries.build_workflow_utilization_stats_cte(config),
        dimension_queries.build_datasource_reuse_stats_cte(),
        dimension_queries.build_datasource_utilization_stats_cte(),
        # Expertise Distribution CTEs (use all users)
        dimension_queries.build_non_champion_stats_cte(include_creators=True),
        # Feature Adoption CTEs
        dimension_queries.build_conversation_depth_cte(config),
        dimension_queries.build_feature_stats_cte(),
        dimension_queries.build_workflow_complexity_stats_cte(),
        # Composite scoring
        composite_queries.build_composite_scores_cte(config),
    ]

    # Build dimension score expressions
    user_engagement_expr = build_user_engagement_score_expression(config)
    asset_reusability_expr = build_asset_reusability_score_expression(config)
    expertise_distribution_expr = build_expertise_distribution_score_expression(config)
    feature_adoption_expr = build_feature_adoption_score_expression(config)

    # Build complexity expressions for Feature Adoption metrics
    assistant_complexity_expr = build_assistant_complexity_expression(config)
    workflow_complexity_expr = build_workflow_complexity_expression(config)

    query_sql = f"""
WITH
{',\n'.join(ctes)}

-- Aggregated metrics for maturity endpoint (only columns used in API response)
-- Column names match exactly with column_definitions.py IDs
--
-- NOTE: For user counts, we use DISTINCT counts from user_stats to avoid double-counting
-- users who exist in multiple projects (organization-wide unique users)
SELECT
    -- Overview metrics
    COUNT(DISTINCT fp.project)::integer AS total_projects,
    AVG(cs.adoption_index)::numeric AS adoption_index,
    CASE
        WHEN AVG(cs.adoption_index) >= {int(config.maturity_level_3_threshold)} THEN 'L3: AGENTIC'
        WHEN AVG(cs.adoption_index) >= {int(config.maturity_level_2_threshold)} THEN 'L2: AUGMENTED'
        ELSE 'L1: ASSISTED'
    END AS maturity_level,

    -- User Engagement (score + 7 secondary metrics)
    AVG(ROUND({user_engagement_expr} * 100, 1))::numeric AS user_engagement_score,
    -- Activation: conversation users with ≥20 interactions / ALL users (filtered projects only)
    ROUND(
        (SELECT COUNT(DISTINCT user_id) FROM user_stats WHERE project IN (SELECT project FROM filtered_projects) AND total_usage >= (SELECT activation_threshold FROM params LIMIT 1))::numeric
        * 100.0 / NULLIF((SELECT COUNT(DISTINCT user_id) FROM user_stats_all WHERE project IN (SELECT project FROM filtered_projects))::numeric, 0),
    1)::numeric AS user_activation_rate,
    -- DAU: conversation users active in 1 day / ALL users (filtered projects only)
    ROUND(
        (SELECT COUNT(DISTINCT user_id) FROM user_stats WHERE project IN (SELECT project FROM filtered_projects) AND last_used >= CURRENT_TIMESTAMP - INTERVAL '1 day')::numeric
        * 100.0 / NULLIF((SELECT COUNT(DISTINCT user_id) FROM user_stats_all WHERE project IN (SELECT project FROM filtered_projects))::numeric, 0),
    1)::numeric AS dau_ratio,
    -- MAU: conversation users active in 30 days / ALL users (filtered projects only)
    ROUND(
        (SELECT COUNT(DISTINCT user_id) FROM user_stats WHERE project IN (SELECT project FROM filtered_projects) AND last_used >= CURRENT_TIMESTAMP - (SELECT active_window_long || ' days' FROM params LIMIT 1)::interval)::numeric
        * 100.0 / NULLIF((SELECT COUNT(DISTINCT user_id) FROM user_stats_all WHERE project IN (SELECT project FROM filtered_projects))::numeric, 0),
    1)::numeric AS mau_ratio,
    AVG(ROUND(
        CASE
            WHEN COALESCE(um.total_users, 0) = 0 THEN 0.0
            WHEN COALESCE(um.interaction_mean, 0) > 0 THEN
                GREATEST(0, LEAST(1, 1 - (COALESCE(um.interaction_stddev, 0) / um.interaction_mean)))
            ELSE 0.5
        END,
    2))::numeric AS engagement_distribution,
    -- Total users: Use user_stats_all (all users including creators, filtered projects only)
    (SELECT COUNT(DISTINCT user_id) FROM user_stats_all WHERE project IN (SELECT project FROM filtered_projects))::integer AS total_users,
    -- Total interactions: Sum from user_stats (filtered projects only)
    (SELECT SUM(total_usage) FROM user_stats WHERE project IN (SELECT project FROM filtered_projects))::integer AS total_interactions,
    -- Returning User Rate (filtered projects only)
    ROUND(
        (SELECT COUNT(DISTINCT user_id) FROM user_stats WHERE project IN (SELECT project FROM filtered_projects) AND is_returning = 1)::numeric
        * 100.0 / NULLIF((SELECT COUNT(DISTINCT user_id) FROM user_stats_all WHERE project IN (SELECT project FROM filtered_projects))::numeric, 0),
    1)::numeric AS returning_user_rate,

    -- Asset Reusability (score + 6 secondary metrics)
    AVG(ROUND({asset_reusability_expr} * 100, 1))::numeric AS asset_reusability_score,
    ROUND(SUM(COALESCE(aa.team_adopted_assistants, 0)) * 100.0 / NULLIF(SUM(COALESCE(ast.total_assistants, 0)), 0), 1)::numeric AS assistants_reuse_rate,
    ROUND(SUM(COALESCE(aa.active_assistants, 0)) * 100.0 / NULLIF(SUM(COALESCE(ast.total_assistants, 0)), 0), 1)::numeric AS assistant_utilization_rate,
    ROUND(SUM(COALESCE(wrs.multi_user_workflows, 0)) * 100.0 / NULLIF(SUM(COALESCE(ws.total_workflows, 0)), 0), 1)::numeric AS workflow_reuse_rate,
    ROUND(SUM(COALESCE(wus.active_workflows, 0)) * 100.0 / NULLIF(SUM(COALESCE(ws.total_workflows, 0)), 0), 1)::numeric AS workflow_utilization_rate,
    ROUND(SUM(COALESCE(drs.shared_datasources, 0)) * 100.0 / NULLIF(SUM(COALESCE(drs.total_datasources, 0)), 0), 1)::numeric AS datasource_reuse_rate,
    ROUND(SUM(COALESCE(dus.active_datasources, 0)) * 100.0 / NULLIF(SUM(COALESCE(drs.total_datasources, 0)), 0), 1)::numeric AS datasource_utilization_rate,

    -- Expertise Distribution (score + 2 secondary metrics, total_users reused from User Engagement)
    AVG(ROUND({expertise_distribution_expr} * 100, 1))::numeric AS expertise_distribution_score,
    -- Count distinct creators across filtered projects only
    ROUND(
        (SELECT COUNT(DISTINCT creator_id) FROM (
            SELECT created_by->>'id' AS creator_id
            FROM codemie.assistants
            WHERE id NOT LIKE 'Virtual%%'
              AND project IN (SELECT project FROM filtered_projects)
              AND created_date >= CURRENT_TIMESTAMP - INTERVAL '{int(config.expertise_distribution_creator_activity_window)} days'
            UNION
            SELECT created_by->>'user_id' AS creator_id
            FROM codemie.workflows
            WHERE project IN (SELECT project FROM filtered_projects)
              AND date >= CURRENT_TIMESTAMP - INTERVAL '{int(config.expertise_distribution_creator_activity_window)} days'
        ) all_creators)::numeric
        * 100.0 / NULLIF((SELECT COUNT(DISTINCT user_id) FROM user_stats_all WHERE project IN (SELECT project FROM filtered_projects))::numeric, 0),
    1)::numeric AS creator_diversity,
    -- Champion health must consider filtered projects only
    MODE() WITHIN GROUP (ORDER BY
        CASE
            -- First check: if no creators in filtered projects, return NO_CREATORS
            WHEN (SELECT COUNT(DISTINCT creator_id) FROM (
                SELECT created_by->>'id' AS creator_id
                FROM codemie.assistants
                WHERE id NOT LIKE 'Virtual%%'
                  AND project IN (SELECT project FROM filtered_projects)
                  AND created_date >= CURRENT_TIMESTAMP - INTERVAL '{int(config.expertise_distribution_creator_activity_window)} days'
                UNION
                SELECT created_by->>'user_id' AS creator_id
                FROM codemie.workflows
                WHERE project IN (SELECT project FROM filtered_projects)
                  AND date >= CURRENT_TIMESTAMP - INTERVAL '{int(config.expertise_distribution_creator_activity_window)} days'
            ) all_creators) = 0 THEN 'NO_CREATORS'
            -- Otherwise check per-project concentration (top 20% usage)
            WHEN COALESCE(con.top_pct_concentration, 0) > {float(config.expertise_distribution_concentration_critical_threshold)} THEN 'CRITICAL'
            WHEN COALESCE(con.top_pct_concentration, 0) > {float(config.expertise_distribution_concentration_warning_threshold)} THEN 'WARNING'
            WHEN COALESCE(con.top_pct_concentration, 0) BETWEEN {float(config.expertise_distribution_concentration_healthy_lower)} AND {float(config.expertise_distribution_concentration_healthy_upper)} THEN 'HEALTHY'
            ELSE 'FLAT'
        END
    ) AS champion_health,

    -- Feature Adoption (score + 2 secondary metrics)
    AVG(ROUND({feature_adoption_expr} * 100, 1))::numeric AS feature_adoption_score,
    AVG(ROUND(COALESCE(cd.median_messages, 0), 1))::numeric AS median_conversation_depth,
    AVG(ROUND(
        (
            {assistant_complexity_expr} * {config.feature_adoption_assistant_complexity_weight} +
            {workflow_complexity_expr} * {config.feature_adoption_workflow_complexity_weight}
        ) * 100.0,
    1))::numeric AS feature_utilization_rate

FROM filtered_projects fp
CROSS JOIN params p
LEFT JOIN assistant_stats ast ON fp.project = ast.project
LEFT JOIN workflow_stats ws ON fp.project = ws.project
LEFT JOIN creator_activity ca ON fp.project = ca.project
LEFT JOIN user_metrics um ON fp.project = um.project
LEFT JOIN multi_assistant_stats mas ON fp.project = mas.project
LEFT JOIN concentration con ON fp.project = con.project
LEFT JOIN assistant_adoption aa ON fp.project = aa.project
LEFT JOIN workflow_reuse_stats wrs ON fp.project = wrs.project
LEFT JOIN workflow_utilization_stats wus ON fp.project = wus.project
LEFT JOIN datasource_reuse_stats drs ON fp.project = drs.project
LEFT JOIN datasource_utilization_stats dus ON fp.project = dus.project
LEFT JOIN non_champion_stats ncs ON fp.project = ncs.project
LEFT JOIN conversation_depth cd ON fp.project = cd.project
LEFT JOIN feature_stats fs ON fp.project = fs.project
LEFT JOIN workflow_complexity_stats wcs ON fp.project = wcs.project
LEFT JOIN composite_scores cs ON fp.project = cs.project
"""

    params = {"projects": projects if projects else None}
    return text(query_sql), params


def build_dimensions_query(
    config: AIAdoptionConfig,
    projects: list[str] | None = None,
    page: int = 0,
    per_page: int = 20,
) -> tuple[text, dict]:
    """Build query for /adoption-dimensions endpoint (all 23+ columns).

    Returns comprehensive view with all metrics + calculation parameters in metadata.
    Uses temporary tables instead of CTEs for better performance with complex queries.

    Args:
        config: AIAdoptionConfig with all parameters
        projects: Filter by specific projects (None for all accessible)
        page: Page number (0-indexed)
        per_page: Results per page

    Returns:
        Tuple of (sqlalchemy.text query, params dict)
    """

    def cte_to_temp_table(cte_sql: str) -> str:
        """Convert 'cte_name AS (SELECT ...)' to temp table."""

        cte_name = cte_sql.split(' AS (')[0].strip()
        select_part = cte_sql[cte_sql.index(' AS (') + 5 :].rstrip()
        if select_part.endswith(')'):
            select_part = select_part[:-1]
        return f"DROP TABLE IF EXISTS {cte_name};\nCREATE TEMP TABLE {cte_name} AS {select_part}"

    def create_temp_index(table_name: str, column: str) -> str:
        """Generate index creation SQL for temp table."""
        return f"CREATE INDEX idx_{table_name}_{column} ON {table_name}({column})"

    ctes = [
        base_queries.build_params_cte(config),
        base_queries.build_assistant_stats_cte(),
        base_queries.build_workflow_stats_cte(),
        base_queries.build_user_stats_cte(include_creators=False),  # D1: Conversations only → table named "user_stats"
        base_queries.build_user_stats_cte(include_creators=True),  # D3: All users → table named "user_stats_all"
        base_queries.build_filtered_projects_cte(),  # Project filtering by minimum users
        base_queries.build_creator_activity_cte(config),
        # User Engagement CTEs (use conversation users only)
        dimension_queries.build_user_metrics_cte(include_creators=False),
        dimension_queries.build_multi_assistant_users_cte(),  # Intermediate CTE
        dimension_queries.build_multi_assistant_stats_cte(),  # Depends on multi_assistant_users
        dimension_queries.build_concentration_cte(include_creators=False),
        # Asset Reusability CTEs
        dimension_queries.build_assistant_usage_cte(),
        dimension_queries.build_assistant_adoption_cte(),
        dimension_queries.build_workflow_reuse_stats_cte(config),
        dimension_queries.build_workflow_utilization_stats_cte(config),
        dimension_queries.build_datasource_reuse_stats_cte(),
        dimension_queries.build_datasource_utilization_stats_cte(),
        # Expertise Distribution CTEs (use all users)
        dimension_queries.build_non_champion_stats_cte(include_creators=True),
        # Feature Adoption CTEs
        dimension_queries.build_conversation_depth_cte(config),
        dimension_queries.build_feature_stats_cte(),
        dimension_queries.build_workflow_complexity_stats_cte(),
        # Composite scoring
        composite_queries.build_composite_scores_cte(config),
    ]

    # Convert CTEs to CREATE TEMP TABLE statements
    temp_table_statements = [cte_to_temp_table(cte) for cte in ctes]

    # Define indexes for key tables (on join columns)
    indexes = [
        create_temp_index("filtered_projects", "project"),
        create_temp_index("assistant_stats", "project"),
        create_temp_index("workflow_stats", "project"),
        create_temp_index("user_metrics", "project"),
        create_temp_index("assistant_adoption", "project"),
        create_temp_index("composite_scores", "project"),
    ]

    # Build complexity expressions
    assistant_complexity_expr = build_assistant_complexity_expression(config)
    workflow_complexity_expr = build_workflow_complexity_expression(config)

    query_sql = f"""
-- Create temporary tables with DROP IF EXISTS for idempotency
{';\n'.join(temp_table_statements)};

-- Create indexes on key join columns
{';\n'.join(indexes)};

-- Full dimension details with all metrics
SELECT
    fp.project,

    -- Baseline counts
    COALESCE(um.total_users, 0)::integer AS total_users,
    COALESCE(ast.total_assistants, 0)::integer AS total_assistants,
    COALESCE(ws.total_workflows, 0)::integer AS total_workflows,
    COALESCE(drs.total_datasources, 0)::integer AS total_datasources,

    -- D1: Daily Active Users metrics
    ROUND(COALESCE(um.activated_users, 0) * 100.0 / NULLIF(um.total_users, 0), 1) AS user_activation_rate,
    ROUND(COALESCE(um.active_30d, 0) * 100.0 / NULLIF(um.total_users, 0), 1) AS mau_ratio,
    ROUND(COALESCE(um.active_1d, 0) * 100.0 / NULLIF(um.total_users, 0), 1) AS dau_ratio,
    ROUND(
        CASE
            WHEN COALESCE(um.total_users, 0) = 0 THEN 0.0
            WHEN COALESCE(um.interaction_mean, 0) > 0 THEN
                GREATEST(0, LEAST(1, 1 - (COALESCE(um.interaction_stddev, 0) / um.interaction_mean)))
            ELSE 0.5
        END,
    2) AS engagement_distribution,
    ROUND(COALESCE(mas.multi_user_count, 0) * 100.0 / NULLIF(um.total_users, 0), 1) AS multi_assistant_rate,

    -- D2: Reusability metrics
    ROUND(COALESCE(aa.team_adopted_assistants, 0) * 100.0 / NULLIF(COALESCE(ast.total_assistants, 0), 0), 1) AS assistants_reuse_rate,
    ROUND(COALESCE(aa.active_assistants, 0) * 100.0 / NULLIF(COALESCE(ast.total_assistants, 0), 0), 1) AS assistant_utilization_rate,
    ROUND(COALESCE(wrs.multi_user_workflows, 0) * 100.0 / NULLIF(ws.total_workflows, 0), 1) AS workflow_reuse_rate,
    ROUND(COALESCE(wus.active_workflows, 0) * 100.0 / NULLIF(ws.total_workflows, 0), 1) AS workflow_utilization_rate,
    ROUND(COALESCE(drs.shared_datasources, 0) * 100.0 / NULLIF(drs.total_datasources, 0), 1) AS datasource_reuse_rate,
    ROUND(COALESCE(dus.active_datasources, 0) * 100.0 / NULLIF(drs.total_datasources, 0), 1) AS datasource_utilization_rate,

    -- D3: AI Champions metrics
    ROUND(ca.unique_creators * 100.0 / NULLIF(um.total_users, 0), 1) AS creator_diversity,
    CASE
        -- If no creators in last 90 days, champion health cannot be assessed
        WHEN COALESCE(ca.unique_creators, 0) = 0 THEN 'NO_CREATORS'
        -- Otherwise check concentration (top 20% usage)
        WHEN COALESCE(con.top_pct_concentration, 0) > {float(config.expertise_distribution_concentration_critical_threshold)} THEN 'CRITICAL'
        WHEN COALESCE(con.top_pct_concentration, 0) > {float(config.expertise_distribution_concentration_warning_threshold)} THEN 'WARNING'
        WHEN COALESCE(con.top_pct_concentration, 0) BETWEEN {float(config.expertise_distribution_concentration_healthy_lower)} AND {float(config.expertise_distribution_concentration_healthy_upper)} THEN 'HEALTHY'
        ELSE 'FLAT'
    END AS champion_health,

    -- D4: AI Capabilities metrics
    ROUND(COALESCE(cd.median_messages, 0), 1) AS median_conversation_depth,
    ROUND(
        (
            {assistant_complexity_expr} * {config.feature_adoption_assistant_complexity_weight} +
            {workflow_complexity_expr} * {config.feature_adoption_workflow_complexity_weight}
        ) * 100.0,
    1) AS feature_utilization_rate,

    -- Composite scores
    cs.adoption_index,
    CASE
        WHEN cs.adoption_index >= {float(config.maturity_level_3_threshold)} THEN 'L3: AGENTIC'
        WHEN cs.adoption_index >= {float(config.maturity_level_2_threshold)} THEN 'L2: AUGMENTED'
        ELSE 'L1: ASSISTED'
    END AS maturity_level

FROM filtered_projects fp
CROSS JOIN params p
LEFT JOIN assistant_stats ast ON fp.project = ast.project
LEFT JOIN workflow_stats ws ON fp.project = ws.project
LEFT JOIN creator_activity ca ON fp.project = ca.project
LEFT JOIN user_metrics um ON fp.project = um.project
LEFT JOIN multi_assistant_stats mas ON fp.project = mas.project
LEFT JOIN concentration con ON fp.project = con.project
LEFT JOIN assistant_adoption aa ON fp.project = aa.project
LEFT JOIN workflow_reuse_stats wrs ON fp.project = wrs.project
LEFT JOIN workflow_utilization_stats wus ON fp.project = wus.project
LEFT JOIN datasource_reuse_stats drs ON fp.project = drs.project
LEFT JOIN datasource_utilization_stats dus ON fp.project = dus.project
LEFT JOIN non_champion_stats ncs ON fp.project = ncs.project
LEFT JOIN conversation_depth cd ON fp.project = cd.project
LEFT JOIN feature_stats fs ON fp.project = fs.project
LEFT JOIN workflow_complexity_stats wcs ON fp.project = wcs.project
LEFT JOIN composite_scores cs ON fp.project = cs.project
WHERE fp.project IS NOT NULL
ORDER BY cs.adoption_index DESC NULLS LAST
LIMIT :limit OFFSET :offset
"""

    params = {
        "projects": projects if projects else None,
        "limit": per_page,
        "offset": page * per_page,
    }
    return text(query_sql), params


def build_user_engagement_metrics_query(
    config: AIAdoptionConfig,
    projects: list[str] | None = None,
    page: int = 0,
    per_page: int = 20,
) -> tuple[text, dict]:
    """Build query for User Engagement metrics only.

    Returns project-level User Engagement metrics in UI order (source of truth):
    - project
    - user_engagement_score (PRIMARY METRIC - composite score 0-100)
    - dau_ratio (DAU %)
    - total_users
    - total_interactions
    - user_activation_rate
    - mau_ratio (MAU %)
    - engagement_distribution
    - returning_user_rate

    Args:
        config: AIAdoptionConfig with all parameters
        projects: Optional project filter
        page: Page number (0-indexed)
        per_page: Results per page

    Returns:
        Tuple of (SQLAlchemy text object, params dict)
    """
    # Assemble CTEs needed for User Engagement
    ctes = [
        build_params_cte(config),
        base_queries.build_assistant_stats_cte(projects_param=True),
        base_queries.build_user_stats_cte(
            projects_param=True, include_creators=False
        ),  # Conversations only (for engagement metrics)
        base_queries.build_user_stats_cte(
            projects_param=True, include_creators=True
        ),  # All users (for total_users count)
        base_queries.build_filtered_projects_cte(projects_param=True),  # Project filtering by minimum users
        dimension_queries.build_user_metrics_cte(include_creators=False),
        dimension_queries.build_multi_assistant_users_cte(projects_param=True),
        dimension_queries.build_multi_assistant_stats_cte(),
    ]

    # Build dimension score expression
    user_engagement_expr = build_user_engagement_score_expression(config)

    # Build final SELECT with User Engagement columns only (matching UI screenshot order)
    query_sql = f"""
WITH
{',\n\n'.join(ctes)},

-- Count ALL users (including creators) for total_users column (filtered projects only)
all_users_count AS (
    SELECT
        project,
        COUNT(*)::numeric AS total_users_all
    FROM user_stats_all
    WHERE project IN (SELECT project FROM filtered_projects)
    GROUP BY project
)

SELECT
    fp.project,
    ROUND({user_engagement_expr} * 100, 1) AS user_engagement_score,
    ROUND(COALESCE(um.active_1d, 0) * 100.0 / NULLIF(um.total_users, 0), 1) AS dau_ratio,
    COALESCE(auc.total_users_all, 0)::integer AS total_users,
    COALESCE(um.total_interactions, 0)::integer AS total_interactions,
    ROUND(COALESCE(um.activated_users, 0) * 100.0 / NULLIF(um.total_users, 0), 1) AS user_activation_rate,
    ROUND(COALESCE(um.active_30d, 0) * 100.0 / NULLIF(um.total_users, 0), 1) AS mau_ratio,
    ROUND(
        CASE
            WHEN COALESCE(um.total_users, 0) = 0 THEN 0.0
            WHEN COALESCE(um.interaction_mean, 0) > 0 THEN
                GREATEST(0, LEAST(1, 1 - (COALESCE(um.interaction_stddev, 0) / um.interaction_mean)))
            ELSE 0.5
        END,
    2) AS engagement_distribution,
    ROUND(COALESCE(um.returning_users, 0) * 100.0 / NULLIF(um.total_users, 0), 1) AS returning_user_rate
FROM filtered_projects fp
CROSS JOIN params p
LEFT JOIN assistant_stats ast ON fp.project = ast.project
LEFT JOIN user_metrics um ON fp.project = um.project
LEFT JOIN multi_assistant_stats mas ON fp.project = mas.project
LEFT JOIN all_users_count auc ON fp.project = auc.project
WHERE fp.project IS NOT NULL
ORDER BY user_engagement_score DESC NULLS LAST, fp.project
LIMIT :limit OFFSET :offset
"""

    params: dict[str, Any] = {"limit": per_page, "offset": page * per_page}
    if projects:
        params["projects"] = projects
    else:
        params["projects"] = None

    return text(query_sql), params


def build_asset_reusability_metrics_query(
    config: AIAdoptionConfig,
    projects: list[str] | None = None,
    page: int = 0,
    per_page: int = 20,
) -> tuple[text, dict]:
    """Build query for Asset Reusability metrics only.

    Returns project-level Asset Reusability metrics:
    - project
    - assistants_reuse_rate, assistant_utilization_rate
    - workflow_reuse_rate, workflow_utilization_rate
    - datasource_reuse_rate, datasource_utilization_rate
    - total_assistants, total_workflows, total_datasources

    Args:
        config: AIAdoptionConfig with all parameters
        projects: Optional project filter
        page: Page number (0-indexed)
        per_page: Results per page

    Returns:
        Tuple of (SQLAlchemy text object, params dict)
    """
    # Assemble CTEs needed for Asset Reusability
    ctes = [
        build_params_cte(config),
        base_queries.build_assistant_stats_cte(projects_param=True),
        base_queries.build_workflow_stats_cte(projects_param=True),
        base_queries.build_user_stats_cte(projects_param=True, include_creators=True),  # Needed for filtered_projects
        base_queries.build_filtered_projects_cte(projects_param=True),  # Project filtering by minimum users
        dimension_queries.build_assistant_usage_cte(projects_param=True),
        dimension_queries.build_assistant_adoption_cte(projects_param=True),
        dimension_queries.build_workflow_reuse_stats_cte(config, projects_param=True),
        dimension_queries.build_workflow_utilization_stats_cte(config, projects_param=True),
        dimension_queries.build_datasource_reuse_stats_cte(projects_param=True),
        dimension_queries.build_datasource_utilization_stats_cte(projects_param=True),
    ]

    # Build dimension score expression
    asset_reusability_expr = build_asset_reusability_score_expression(config)

    # Build final SELECT with Asset Reusability columns only
    query_sql = f"""
WITH
{',\n\n'.join(ctes)}

SELECT
    fp.project,
    ROUND({asset_reusability_expr} * 100, 1) AS asset_reusability_score,
    COALESCE(ast.total_assistants, 0)::integer AS total_assistants,
    COALESCE(ws.total_workflows, 0)::integer AS total_workflows,
    COALESCE(drs.total_datasources, 0)::integer AS total_datasources,
    ROUND(COALESCE(aa.team_adopted_assistants, 0) * 100.0 / NULLIF(COALESCE(ast.total_assistants, 0), 0), 1) AS assistants_reuse_rate,
    ROUND(COALESCE(aa.active_assistants, 0) * 100.0 / NULLIF(COALESCE(ast.total_assistants, 0), 0), 1) AS assistant_utilization_rate,
    ROUND(COALESCE(wrs.multi_user_workflows, 0) * 100.0 / NULLIF(ws.total_workflows, 0), 1) AS workflow_reuse_rate,
    ROUND(COALESCE(wus.active_workflows, 0) * 100.0 / NULLIF(ws.total_workflows, 0), 1) AS workflow_utilization_rate,
    ROUND(COALESCE(drs.shared_datasources, 0) * 100.0 / NULLIF(drs.total_datasources, 0), 1) AS datasource_reuse_rate,
    ROUND(COALESCE(dus.active_datasources, 0) * 100.0 / NULLIF(drs.total_datasources, 0), 1) AS datasource_utilization_rate
FROM filtered_projects fp
CROSS JOIN params p
LEFT JOIN assistant_stats ast ON fp.project = ast.project
LEFT JOIN workflow_stats ws ON fp.project = ws.project
LEFT JOIN assistant_adoption aa ON fp.project = aa.project
LEFT JOIN workflow_reuse_stats wrs ON fp.project = wrs.project
LEFT JOIN workflow_utilization_stats wus ON fp.project = wus.project
LEFT JOIN datasource_reuse_stats drs ON fp.project = drs.project
LEFT JOIN datasource_utilization_stats dus ON fp.project = dus.project
WHERE fp.project IS NOT NULL
ORDER BY asset_reusability_score DESC NULLS LAST, fp.project
LIMIT :limit OFFSET :offset
"""

    params: dict[str, Any] = {"limit": per_page, "offset": page * per_page}
    if projects:
        params["projects"] = projects
    else:
        params["projects"] = None

    return text(query_sql), params


def build_expertise_distribution_metrics_query(
    config: AIAdoptionConfig,
    projects: list[str] | None = None,
    page: int = 0,
    per_page: int = 20,
) -> tuple[text, dict]:
    """Build query for Expertise Distribution metrics only.

    Returns project-level Expertise Distribution metrics:
    - project
    - creator_diversity
    - champion_health
    - total_users

    Args:
        config: AIAdoptionConfig with all parameters
        projects: Optional project filter
        page: Page number (0-indexed)
        per_page: Results per page

    Returns:
        Tuple of (SQLAlchemy text object, params dict)
    """
    # Assemble CTEs needed for Expertise Distribution
    ctes = [
        build_params_cte(config),
        base_queries.build_assistant_stats_cte(projects_param=True),
        base_queries.build_user_stats_cte(
            projects_param=True, include_creators=True
        ),  # D3: All activity including creators → CTE named "user_stats_all"
        base_queries.build_filtered_projects_cte(projects_param=True),  # Project filtering by minimum users
        base_queries.build_creator_activity_cte(config, projects_param=True),
        dimension_queries.build_user_metrics_cte(include_creators=True),  # Must match user_stats_all from line above
        dimension_queries.build_concentration_cte(include_creators=True),
        dimension_queries.build_non_champion_stats_cte(include_creators=True),
    ]

    # Build dimension score expression
    expertise_distribution_expr = build_expertise_distribution_score_expression(config)

    # Build final SELECT with Expertise Distribution columns only
    query_sql = f"""
WITH
{',\n\n'.join(ctes)}

SELECT
    fp.project,
    ROUND({expertise_distribution_expr} * 100, 1) AS expertise_distribution_score,
    COALESCE(um.total_users, 0)::integer AS total_users,
    ROUND(COALESCE(ca.unique_creators, 0) * 100.0 / NULLIF(um.total_users, 0), 1) AS creator_diversity,
    CASE
        -- If no creators in last 90 days, champion health cannot be assessed
        WHEN COALESCE(ca.unique_creators, 0) = 0 THEN 'NO_CREATORS'
        -- Otherwise check concentration (top 20% usage)
        WHEN COALESCE(con.top_pct_concentration, 0) > {float(config.expertise_distribution_concentration_critical_threshold)} THEN 'CRITICAL'
        WHEN COALESCE(con.top_pct_concentration, 0) > {float(config.expertise_distribution_concentration_warning_threshold)} THEN 'WARNING'
        WHEN COALESCE(con.top_pct_concentration, 0) BETWEEN {float(config.expertise_distribution_concentration_healthy_lower)} AND {float(config.expertise_distribution_concentration_healthy_upper)} THEN 'HEALTHY'
        ELSE 'FLAT'
    END AS champion_health
FROM filtered_projects fp
CROSS JOIN params p
LEFT JOIN assistant_stats ast ON fp.project = ast.project
LEFT JOIN user_metrics um ON fp.project = um.project
LEFT JOIN creator_activity ca ON fp.project = ca.project
LEFT JOIN concentration con ON fp.project = con.project
LEFT JOIN non_champion_stats ncs ON fp.project = ncs.project
WHERE fp.project IS NOT NULL
ORDER BY expertise_distribution_score DESC NULLS LAST, fp.project
LIMIT :limit OFFSET :offset
"""

    params: dict[str, Any] = {"limit": per_page, "offset": page * per_page}
    if projects:
        params["projects"] = projects
    else:
        params["projects"] = None

    return text(query_sql), params


def build_feature_adoption_metrics_query(
    config: AIAdoptionConfig,
    projects: list[str] | None = None,
    page: int = 0,
    per_page: int = 20,
) -> tuple[text, dict]:
    """Build query for Feature Adoption metrics only.

    Returns project-level Feature Adoption metrics:
    - project
    - feature_adoption_score
    - feature_utilization_rate
    - total_assistants
    - total_workflows
    - median_conversation_depth
    - assistant_complexity_score
    - workflow_complexity_score

    Args:
        config: AIAdoptionConfig with all parameters
        projects: Optional project filter
        page: Page number (0-indexed)
        per_page: Results per page

    Returns:
        Tuple of (SQLAlchemy text object, params dict)
    """
    # Assemble CTEs needed for Feature Adoption
    ctes = [
        build_params_cte(config),
        base_queries.build_assistant_stats_cte(projects_param=True),
        base_queries.build_workflow_stats_cte(projects_param=True),
        base_queries.build_user_stats_cte(projects_param=True, include_creators=True),  # Needed for filtered_projects
        base_queries.build_filtered_projects_cte(projects_param=True),  # Project filtering by minimum users
        dimension_queries.build_conversation_depth_cte(config, projects_param=True),
        dimension_queries.build_feature_stats_cte(projects_param=True),
        dimension_queries.build_workflow_complexity_stats_cte(projects_param=True),
    ]

    # Build dimension score and complexity expressions
    feature_adoption_expr = build_feature_adoption_score_expression(config)
    assistant_complexity_expr = build_assistant_complexity_expression(config)
    workflow_complexity_expr = build_workflow_complexity_expression(config)

    # Build final SELECT with Feature Adoption columns only
    query_sql = f"""
WITH
{',\n\n'.join(ctes)}

SELECT
    fp.project,
    ROUND({feature_adoption_expr} * 100, 1) AS feature_adoption_score,
    ROUND(
        (
            -- Assistant complexity (60%)
            {assistant_complexity_expr} * {config.feature_adoption_assistant_complexity_weight} +
            -- Workflow complexity (40%)
            {workflow_complexity_expr} * {config.feature_adoption_workflow_complexity_weight}
        ) * 100.0,
    1) AS feature_utilization_rate,
    COALESCE(ast.total_assistants, 0)::integer AS total_assistants,
    COALESCE(ws.total_workflows, 0)::integer AS total_workflows,
    ROUND(COALESCE(cd.median_messages, 0), 1) AS median_conversation_depth,
    ROUND({assistant_complexity_expr} * 100.0, 1) AS assistant_complexity_score,
    ROUND({workflow_complexity_expr} * 100.0, 1) AS workflow_complexity_score
FROM filtered_projects fp
CROSS JOIN params p
LEFT JOIN assistant_stats ast ON fp.project = ast.project
LEFT JOIN workflow_stats ws ON fp.project = ws.project
LEFT JOIN conversation_depth cd ON fp.project = cd.project
LEFT JOIN feature_stats fs ON fp.project = fs.project
LEFT JOIN workflow_complexity_stats wcs ON fp.project = wcs.project
WHERE fp.project IS NOT NULL
ORDER BY feature_adoption_score DESC NULLS LAST, fp.project
LIMIT :limit OFFSET :offset
"""

    params: dict[str, Any] = {"limit": per_page, "offset": page * per_page}
    if projects:
        params["projects"] = projects
    else:
        params["projects"] = None

    return text(query_sql), params


def build_overview_query(
    config: AIAdoptionConfig,
    projects: list[str] | None = None,
) -> tuple[text, dict]:
    """Build query for /ai-adoption-overview endpoint.

    Returns single aggregated row with total counts:
    - total_projects (filtered by minimum_users_threshold)
    - total_users (from filtered projects only)
    - total_assistants (from filtered projects only)
    - total_workflows (from filtered projects only)
    - total_datasources (from filtered projects only)

    All counts respect the filtered_projects CTE for consistency with maturity endpoint.

    Args:
        config: AIAdoptionConfig with all parameters
        projects: Filter by specific projects (None for all accessible)

    Returns:
        Tuple of (sqlalchemy.text query, params dict)
    """
    # Build CTEs (minimal set needed for overview counts)
    ctes = [
        build_params_cte(config),
        base_queries.build_user_stats_cte(projects_param=True, include_creators=True),  # All users for filtering
        base_queries.build_filtered_projects_cte(projects_param=True),  # Project filtering
    ]

    query_sql = f"""
WITH
{',\n'.join(ctes)},
-- Projects count (filtered by minimum_users_threshold)
projects_count AS (
    SELECT COUNT(DISTINCT project)::integer AS total_projects
    FROM filtered_projects
),
-- Users count: Only from filtered projects
users_count AS (
    SELECT COUNT(DISTINCT user_id)::integer AS total_users
    FROM user_stats_all
    WHERE project IN (SELECT project FROM filtered_projects)
),
-- Assistants count
assistants_count AS (
    SELECT COUNT(*)::integer AS total_assistants
    FROM codemie.assistants a
    WHERE id NOT LIKE 'Virtual%%'
      AND a.project IN (SELECT project FROM filtered_projects)
),
-- Workflows count
workflows_count AS (
    SELECT COUNT(*)::integer AS total_workflows
    FROM codemie.workflows w
    WHERE w.project IN (SELECT project FROM filtered_projects)
),
-- Datasources count (from index_info table, matches dimensions query)
datasources_count AS (
    SELECT COUNT(DISTINCT id)::integer AS total_datasources
    FROM codemie.index_info
    WHERE project_name IN (SELECT project FROM filtered_projects)
)
SELECT
    pc.total_projects,
    uc.total_users,
    ac.total_assistants,
    wc.total_workflows,
    dc.total_datasources
FROM projects_count pc
CROSS JOIN users_count uc
CROSS JOIN assistants_count ac
CROSS JOIN workflows_count wc
CROSS JOIN datasources_count dc
"""

    params = {"projects": projects if projects else None}
    return text(query_sql), params


def build_project_count_query(
    config: AIAdoptionConfig,
    projects: list[str] | None = None,
) -> tuple[text, dict]:
    """Build query to count projects respecting minimum_users_threshold filter.

    Used for pagination total_count in dimension endpoints.
    Reuses base_queries CTEs for consistency with other endpoints.

    Args:
        config: AIAdoptionConfig with all parameters
        projects: Filter by specific projects (None for all accessible)

    Returns:
        Tuple of (sqlalchemy.text query, params dict)
    """
    # Reuse existing CTEs (DRY principle - single source of truth)
    ctes = [
        build_params_cte(config),
        base_queries.build_user_stats_cte(projects_param=True, include_creators=True),
        base_queries.build_filtered_projects_cte(projects_param=True),
    ]

    query_sql = f"""
WITH
{',\n'.join(ctes)}

-- Count filtered projects
SELECT COUNT(DISTINCT project)::integer AS total
FROM filtered_projects
"""

    params = {"projects": projects if projects else None}
    return text(query_sql), params


def build_user_engagement_users_query(
    config: AIAdoptionConfig,
    project: str,
    page: int = 0,
    per_page: int = 20,
    user_type_filter: str | None = None,
    activity_level_filter: str | None = None,
    multi_assistant_filter: bool | None = None,
    sort_by: str = 'engagement_score',
    sort_order: str = 'desc',
) -> tuple[text, dict]:
    """Build user-level drill-down query for User Engagement dimension.

    Args:
        config: AI Adoption configuration
        project: Single project identifier
        page: Page number (zero-indexed)
        per_page: Items per page
        user_type_filter: Filter by user type
        activity_level_filter: Filter by activity level
        multi_assistant_filter: Filter by multi-assistant usage
        sort_by: Sort column
        sort_order: Sort direction

    Returns:
        Tuple of (SQL query string, parameters dict)
    """
    # Build CTEs (no project parameter filtering - single project only)
    # Use include_creators=True to match main table's total_users count (includes creators)
    ctes = [
        base_queries.build_params_cte(config),
        base_queries.build_user_stats_cte(projects_param=False, include_creators=True),
        dimension_queries.build_user_engagement_users_detail_cte(single_project=True),
    ]

    cte_sql = ",\n\n".join(ctes)

    # Build filter conditions
    filter_conditions = [SQL_PROJECT_FILTER]  # Single project filter
    if user_type_filter:
        filter_conditions.append("user_type = :user_type_filter")
    if activity_level_filter:
        if activity_level_filter == 'daily':
            filter_conditions.append("is_daily_active = TRUE")
        elif activity_level_filter == 'weekly':
            filter_conditions.append("is_weekly_active = TRUE")
        elif activity_level_filter == 'monthly':
            filter_conditions.append("is_monthly_active = TRUE")
        elif activity_level_filter == 'inactive':
            filter_conditions.append("is_monthly_active = FALSE")
    if multi_assistant_filter is not None:
        filter_conditions.append("is_multi_assistant_user = :multi_assistant_filter")

    filter_sql = SQL_AND.join(filter_conditions)

    # Build sort clause
    sort_direction = "DESC" if sort_order == 'desc' else "ASC"
    sort_mappings = {
        'engagement_score': f"engagement_score {sort_direction}",
        'total_interactions': f"total_interactions {sort_direction}",
        'last_used': f"last_used {sort_direction}",
        'user_name': f"user_name {sort_direction}",
    }
    sort_clause = sort_mappings.get(sort_by, f"engagement_score {sort_direction}")

    # Build final query
    query_sql = f"""
WITH
{cte_sql}

SELECT
    user_name,
    total_interactions,
    first_used,
    last_used,
    days_since_last_activity,
    is_activated,
    is_returning,
    is_daily_active,
    is_weekly_active,
    is_monthly_active,
    is_multi_assistant_user,
    distinct_assistant_count,
    user_type,
    engagement_score
FROM user_engagement_users_detail
WHERE {filter_sql}
ORDER BY {sort_clause}, last_used DESC
LIMIT :limit OFFSET :offset
"""

    # Build parameters
    params = {
        "project": project,
        "user_type_filter": user_type_filter,
        "multi_assistant_filter": multi_assistant_filter,
        "limit": per_page,
        "offset": page * per_page,
    }

    return text(query_sql), params


def build_user_engagement_users_count_query(
    config: AIAdoptionConfig,
    project: str,
    user_type_filter: str | None = None,
    activity_level_filter: str | None = None,
    multi_assistant_filter: bool | None = None,
) -> tuple[text, dict]:
    """Build count query for user engagement users (for pagination).

    Returns total count matching the filters without LIMIT/OFFSET.
    """
    # Reuse same CTEs and filters - use include_creators=True to match main query
    ctes = [
        base_queries.build_params_cte(config),
        base_queries.build_user_stats_cte(projects_param=False, include_creators=True),
        dimension_queries.build_user_engagement_users_detail_cte(single_project=True),
    ]

    cte_sql = ",\n\n".join(ctes)

    # Same filter logic as main query
    filter_conditions = [SQL_PROJECT_FILTER]
    if user_type_filter:
        filter_conditions.append("user_type = :user_type_filter")
    if activity_level_filter:
        if activity_level_filter == 'daily':
            filter_conditions.append("is_daily_active = TRUE")
        elif activity_level_filter == 'weekly':
            filter_conditions.append("is_weekly_active = TRUE")
        elif activity_level_filter == 'monthly':
            filter_conditions.append("is_monthly_active = TRUE")
        elif activity_level_filter == 'inactive':
            filter_conditions.append("is_monthly_active = FALSE")
    if multi_assistant_filter is not None:
        filter_conditions.append("is_multi_assistant_user = :multi_assistant_filter")

    filter_sql = SQL_AND.join(filter_conditions)

    query_sql = f"""
WITH
{cte_sql}

SELECT COUNT(*) AS total_count
FROM user_engagement_users_detail
WHERE {filter_sql}
"""

    params = {
        "project": project,
        "user_type_filter": user_type_filter,
        "multi_assistant_filter": multi_assistant_filter,
    }

    return text(query_sql), params


def build_assistant_reusability_detail_query(
    config: AIAdoptionConfig,
    project: str,
    page: int = 0,
    per_page: int = 20,
    status_filter: str | None = None,  # 'active' | 'inactive'
    adoption_filter: str | None = None,  # 'team_adopted' | 'single_user'
    sort_by: str = 'total_usage',
    sort_order: str = 'desc',
) -> tuple[text, dict]:
    """Build assistant-level drill-down query for Asset Reusability dimension.

    Args:
        config: AI Adoption configuration
        project: Single project identifier
        page: Page number (zero-indexed)
        per_page: Items per page
        status_filter: Filter by active/inactive status
        adoption_filter: Filter by team-adopted/single-user
        sort_by: Sort column
        sort_order: Sort direction

    Returns:
        Tuple of (SQL query string, parameters dict)
    """
    # Build CTEs (no project parameter filtering - single project only)
    ctes = [
        base_queries.build_params_cte(config),
        dimension_queries.build_assistant_usage_cte(projects_param=False, single_project=True),
        dimension_queries.build_assistant_reusability_detail_cte(),
    ]

    cte_sql = ",\n\n".join(ctes)

    # Build filter conditions
    filter_conditions = [SQL_PROJECT_FILTER]  # Single project filter

    if status_filter:
        if status_filter == 'active':
            filter_conditions.append(SQL_IS_ACTIVE_TRUE)
        elif status_filter == 'inactive':
            filter_conditions.append(SQL_IS_ACTIVE_FALSE)

    if adoption_filter:
        if adoption_filter == 'team_adopted':
            filter_conditions.append("is_team_adopted = TRUE")
        elif adoption_filter == 'single_user':
            filter_conditions.append("is_team_adopted = FALSE AND unique_users > 0")

    filter_sql = SQL_AND.join(filter_conditions)

    # Build sort clause
    sort_direction = "DESC" if sort_order == 'desc' else "ASC"
    sort_mappings = {
        'total_usage': f"total_usage {sort_direction}",
        'unique_users': f"unique_users {sort_direction}",
        'last_used': f"last_used {sort_direction} NULLS LAST",
        'assistant_name': f"assistant_name {sort_direction}",
        'created_date': f"created_date {sort_direction}",
    }
    sort_clause = sort_mappings.get(sort_by, f"total_usage {sort_direction}")

    # Build final query
    query_sql = f"""
WITH
{cte_sql}

SELECT
    assistant_id,
    assistant_name,
    project,
    description,
    total_usage,
    unique_users,
    last_used,
    days_since_last_used,
    is_active,
    is_team_adopted,
    datasource_count,
    toolkit_count,
    mcp_server_count,
    creator_id,
    creator_name,
    created_date
FROM assistant_reusability_detail
WHERE {filter_sql}
ORDER BY {sort_clause}, created_date DESC
LIMIT :limit OFFSET :offset
"""

    # Build parameters
    params = {
        "project": project,
        "limit": per_page,
        "offset": page * per_page,
    }

    return text(query_sql), params


def build_assistant_reusability_detail_count_query(
    config: AIAdoptionConfig,
    project: str,
    status_filter: str | None = None,
    adoption_filter: str | None = None,
) -> tuple[text, dict]:
    """Build count query for assistant reusability detail (for pagination).

    Returns total count matching the filters without LIMIT/OFFSET.
    """
    # Reuse same CTEs and filters
    ctes = [
        base_queries.build_params_cte(config),
        dimension_queries.build_assistant_usage_cte(projects_param=False, single_project=True),
        dimension_queries.build_assistant_reusability_detail_cte(),
    ]

    cte_sql = ",\n\n".join(ctes)

    # Same filter logic as main query
    filter_conditions = [SQL_PROJECT_FILTER]

    if status_filter:
        if status_filter == 'active':
            filter_conditions.append(SQL_IS_ACTIVE_TRUE)
        elif status_filter == 'inactive':
            filter_conditions.append(SQL_IS_ACTIVE_FALSE)

    if adoption_filter:
        if adoption_filter == 'team_adopted':
            filter_conditions.append("is_team_adopted = TRUE")
        elif adoption_filter == 'single_user':
            filter_conditions.append("is_team_adopted = FALSE AND unique_users > 0")

    filter_sql = SQL_AND.join(filter_conditions)

    query_sql = f"""
WITH
{cte_sql}

SELECT COUNT(*) AS total_count
FROM assistant_reusability_detail
WHERE {filter_sql}
"""

    params = {
        "project": project,
    }

    return text(query_sql), params


def build_workflow_reusability_detail_query(
    config: AIAdoptionConfig,
    project: str,
    page: int = 0,
    per_page: int = 20,
    status_filter: str | None = None,  # 'active' | 'inactive'
    reuse_filter: str | None = None,  # 'multi_user' | 'single_user'
    sort_by: str = 'execution_count',
    sort_order: str = 'desc',
) -> tuple[text, dict]:
    """Build workflow-level drill-down query for Asset Reusability dimension.

    Args:
        config: AI Adoption configuration
        project: Single project identifier
        page: Page number (zero-indexed)
        per_page: Items per page
        status_filter: Filter by active/inactive status
        reuse_filter: Filter by multi-user/single-user
        sort_by: Sort column
        sort_order: Sort direction

    Returns:
        Tuple of (SQL query string, parameters dict)
    """
    # Build CTEs
    ctes = [
        base_queries.build_params_cte(config),
        dimension_queries.build_workflow_reusability_detail_cte(),
    ]

    cte_sql = ",\n\n".join(ctes)

    # Build filter conditions
    filter_conditions = [SQL_PROJECT_FILTER]  # Single project filter

    if status_filter:
        if status_filter == 'active':
            filter_conditions.append(SQL_IS_ACTIVE_TRUE)
        elif status_filter == 'inactive':
            filter_conditions.append(SQL_IS_ACTIVE_FALSE)

    if reuse_filter:
        if reuse_filter == 'multi_user':
            filter_conditions.append("is_multi_user = TRUE")
        elif reuse_filter == 'single_user':
            filter_conditions.append("is_multi_user = FALSE AND unique_users > 0")

    filter_sql = SQL_AND.join(filter_conditions)

    # Build sort clause
    sort_direction = "DESC" if sort_order == 'desc' else "ASC"
    sort_mappings = {
        'execution_count': f"execution_count {sort_direction}",
        'unique_users': f"unique_users {sort_direction}",
        'last_executed': f"last_executed {sort_direction} NULLS LAST",
        'workflow_name': f"workflow_name {sort_direction}",
        'created_date': f"created_date {sort_direction}",
    }
    sort_clause = sort_mappings.get(sort_by, f"execution_count {sort_direction}")

    # Build final query
    query_sql = f"""
WITH
{cte_sql}

SELECT
    workflow_id,
    workflow_name,
    project,
    description,
    execution_count,
    unique_users,
    last_executed,
    days_since_last_executed,
    is_active,
    is_multi_user,
    state_count,
    tool_count,
    custom_node_count,
    assistant_count,
    creator_id,
    creator_name,
    created_date
FROM workflow_reusability_detail
WHERE {filter_sql}
ORDER BY {sort_clause}, created_date DESC
LIMIT :limit OFFSET :offset
"""

    # Build parameters
    params = {
        "project": project,
        "limit": per_page,
        "offset": page * per_page,
    }

    return text(query_sql), params


def build_workflow_reusability_detail_count_query(
    config: AIAdoptionConfig,
    project: str,
    status_filter: str | None = None,
    reuse_filter: str | None = None,
) -> tuple[text, dict]:
    """Build count query for workflow reusability detail (for pagination).

    Returns total count matching the filters without LIMIT/OFFSET.
    """
    # Reuse same CTEs and filters
    ctes = [
        base_queries.build_params_cte(config),
        dimension_queries.build_workflow_reusability_detail_cte(),
    ]

    cte_sql = ",\n\n".join(ctes)

    # Same filter logic as main query
    filter_conditions = [SQL_PROJECT_FILTER]

    if status_filter:
        if status_filter == 'active':
            filter_conditions.append(SQL_IS_ACTIVE_TRUE)
        elif status_filter == 'inactive':
            filter_conditions.append(SQL_IS_ACTIVE_FALSE)

    if reuse_filter:
        if reuse_filter == 'multi_user':
            filter_conditions.append("is_multi_user = TRUE")
        elif reuse_filter == 'single_user':
            filter_conditions.append("is_multi_user = FALSE AND unique_users > 0")

    filter_sql = SQL_AND.join(filter_conditions)

    query_sql = f"""
WITH
{cte_sql}

SELECT COUNT(*) AS total_count
FROM workflow_reusability_detail
WHERE {filter_sql}
"""

    params = {
        "project": project,
    }

    return text(query_sql), params


def build_datasource_reusability_detail_query(
    config: AIAdoptionConfig,
    project: str,
    page: int = 0,
    per_page: int = 20,
    status_filter: str | None = None,  # 'active' | 'inactive'
    shared_filter: str | None = None,  # 'shared' | 'single'
    type_filter: str | None = None,  # datasource type
    sort_by: str = 'assistant_count',
    sort_order: str = 'desc',
) -> tuple[text, dict]:
    """Build datasource-level drill-down query for Asset Reusability dimension.

    Args:
        config: AI Adoption configuration
        project: Single project identifier
        page: Page number (zero-indexed)
        per_page: Items per page
        status_filter: Filter by active/inactive status
        shared_filter: Filter by shared/single
        type_filter: Filter by datasource type
        sort_by: Sort column
        sort_order: Sort direction

    Returns:
        Tuple of (SQL query string, parameters dict)
    """
    # Build CTEs
    ctes = [
        base_queries.build_params_cte(config),
        dimension_queries.build_assistant_usage_cte(projects_param=False, single_project=True),
        dimension_queries.build_datasource_reusability_detail_cte(),
    ]

    cte_sql = ",\n\n".join(ctes)

    # Build filter conditions
    filter_conditions = [SQL_PROJECT_FILTER]  # Single project filter

    if status_filter:
        if status_filter == 'active':
            filter_conditions.append(SQL_IS_ACTIVE_TRUE)
        elif status_filter == 'inactive':
            filter_conditions.append(SQL_IS_ACTIVE_FALSE)

    if shared_filter:
        if shared_filter == 'shared':
            filter_conditions.append("is_shared = TRUE")
        elif shared_filter == 'single':
            filter_conditions.append("is_shared = FALSE AND assistant_count > 0")

    if type_filter:
        filter_conditions.append("datasource_type = :type_filter")

    filter_sql = SQL_AND.join(filter_conditions)

    # Build sort clause
    sort_direction = "DESC" if sort_order == 'desc' else "ASC"
    sort_mappings = {
        'assistant_count': f"assistant_count {sort_direction}",
        'max_usage': f"max_usage {sort_direction}",
        'last_indexed': f"last_indexed {sort_direction} NULLS LAST",
        'datasource_name': f"datasource_name {sort_direction}",
        'created_date': f"created_date {sort_direction}",
    }
    sort_clause = sort_mappings.get(sort_by, f"assistant_count {sort_direction}")

    # Build final query
    query_sql = f"""
WITH
{cte_sql}

SELECT
    datasource_id,
    datasource_name,
    project,
    description,
    datasource_type,
    assistant_count,
    max_usage,
    last_indexed,
    days_since_last_indexed,
    is_active,
    is_shared,
    creator_id,
    creator_name,
    created_date
FROM datasource_reusability_detail
WHERE {filter_sql}
ORDER BY {sort_clause}, created_date DESC
LIMIT :limit OFFSET :offset
"""

    # Build parameters
    params = {
        "project": project,
        "type_filter": type_filter,
        "limit": per_page,
        "offset": page * per_page,
    }

    return text(query_sql), params


def build_datasource_reusability_detail_count_query(
    config: AIAdoptionConfig,
    project: str,
    status_filter: str | None = None,
    shared_filter: str | None = None,
    type_filter: str | None = None,
) -> tuple[text, dict]:
    """Build count query for datasource reusability detail (for pagination).

    Returns total count matching the filters without LIMIT/OFFSET.
    """
    # Reuse same CTEs and filters
    ctes = [
        base_queries.build_params_cte(config),
        dimension_queries.build_assistant_usage_cte(projects_param=False, single_project=True),
        dimension_queries.build_datasource_reusability_detail_cte(),
    ]

    cte_sql = ",\n\n".join(ctes)

    # Same filter logic as main query
    filter_conditions = [SQL_PROJECT_FILTER]

    if status_filter:
        if status_filter == 'active':
            filter_conditions.append(SQL_IS_ACTIVE_TRUE)
        elif status_filter == 'inactive':
            filter_conditions.append(SQL_IS_ACTIVE_FALSE)

    if shared_filter:
        if shared_filter == 'shared':
            filter_conditions.append("is_shared = TRUE")
        elif shared_filter == 'single':
            filter_conditions.append("is_shared = FALSE AND assistant_count > 0")

    if type_filter:
        filter_conditions.append("datasource_type = :type_filter")

    filter_sql = SQL_AND.join(filter_conditions)

    query_sql = f"""
WITH
{cte_sql}

SELECT COUNT(*) AS total_count
FROM datasource_reusability_detail
WHERE {filter_sql}
"""

    params = {
        "project": project,
        "type_filter": type_filter,
    }

    return text(query_sql), params
