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

"""Base query CTEs for AI Adoption Framework.

Provides foundation CTEs that aggregate baseline data from database tables.
These CTEs are used across all dimension calculations.
"""

from __future__ import annotations

from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig

# SQL filter constants
_PROJECT_FILTER_GENERIC = (
    "AND (CAST(:projects AS text[]) IS NULL OR array_length(CAST(:projects AS text[]), 1) IS NULL "
    "OR project = ANY(CAST(:projects AS text[])))"
)
_PROJECT_FILTER_WITH_PREFIX = (
    "AND (CAST(:projects AS text[]) IS NULL OR array_length(CAST(:projects AS text[]), 1) IS NULL "
    "OR {prefix}.project = ANY(CAST(:projects AS text[])))"
)
_SQL_WHERE = "WHERE "
_SQL_AND = "AND "


def build_params_cte(config: AIAdoptionConfig) -> str:
    """Generate params CTE with configuration values.

    Args:
        config: AIAdoptionConfig with all parameters

    Returns:
        SQL string for params CTE
    """
    return f"""
params AS (
    SELECT
        -- Activation thresholds
        {config.maturity_activation_threshold}::numeric AS activation_threshold,
        {config.asset_reusability_workflow_activation_threshold}::numeric AS workflow_activation_threshold,
        {config.asset_reusability_workflow_reuse_threshold}::numeric AS workflow_reuse_threshold,
        {config.asset_reusability_team_adopted_threshold}::numeric AS team_adopted_threshold,
        {config.user_engagement_multi_assistant_threshold}::numeric AS multi_assistant_threshold,
        {config.minimum_users_threshold}::numeric AS minimum_users_threshold,

        -- Time windows (days)
        {int(config.user_engagement_active_window_short)} AS active_window_short,
        {int(config.user_engagement_active_window_long)} AS active_window_long,
        {int(config.user_engagement_activation_window)} AS user_engagement_activation_window,
        {int(config.user_engagement_returning_user_window)} AS returning_user_window,

        -- Concentration thresholds
        {config.expertise_distribution_top_user_percentile}::numeric AS top_user_percentile,

        -- Level thresholds
        {config.maturity_level_2_threshold}::numeric AS level_2_threshold,
        {config.maturity_level_3_threshold}::numeric AS level_3_threshold
)
"""


def build_assistant_stats_cte(projects_param: bool = True) -> str:
    """Generate assistant_stats CTE.

    Aggregates assistant counts and datasource enablement per project.

    Args:
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for assistant_stats CTE
    """
    project_filter = ""
    if projects_param:
        project_filter = _PROJECT_FILTER_GENERIC

    return f"""
assistant_stats AS (
    SELECT
        project,
        COUNT(*)::numeric AS total_assistants,
        COUNT(
            CASE WHEN jsonb_array_length(COALESCE(context, '[]'::jsonb)) > 0 THEN 1 END
        )::numeric AS datasource_enabled
    FROM codemie.assistants
    WHERE id NOT LIKE 'Virtual%%'
      {project_filter}
    GROUP BY project
)
"""


def build_workflow_stats_cte(projects_param: bool = True) -> str:
    """Generate workflow_stats CTE.

    Aggregates workflow counts per project.

    Args:
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for workflow_stats CTE
    """
    project_filter = ""
    if projects_param:
        project_filter = _SQL_WHERE + _PROJECT_FILTER_GENERIC.replace(_SQL_AND, "")

    return f"""
workflow_stats AS (
    SELECT
        project,
        COUNT(*)::numeric AS total_workflows
    FROM codemie.workflows
    {project_filter}
    GROUP BY project
)
"""


def build_creator_activity_cte(config: AIAdoptionConfig, projects_param: bool = True) -> str:
    """Generate creator_activity CTE.

    Tracks unique creators and workflow creators for weighted scoring (90-day window).
    Workflow creators get a bonus multiplier in D3 score calculation.

    Args:
        config: AIAdoptionConfig for time window
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for creator_activity CTE
    """
    project_filter_assistants = ""
    project_filter_workflows = ""

    if projects_param:
        project_filter_assistants = _PROJECT_FILTER_GENERIC
        project_filter_workflows = _PROJECT_FILTER_GENERIC

    return f"""
creator_activity AS (
    SELECT
        project,
        -- All unique creators (deduplicated across assistants and workflows)
        COUNT(DISTINCT creator_id)::numeric AS unique_creators,
        -- Workflow creators specifically (for weighted bonus in D3 score)
        COUNT(DISTINCT CASE WHEN is_workflow_creator THEN creator_id END)::numeric AS workflow_creators
    FROM (
        -- Creators from assistants
        SELECT
            project,
            COALESCE(created_by->>'user_id', created_by->>'id') AS creator_id,
            FALSE AS is_workflow_creator
        FROM codemie.assistants
        WHERE id NOT LIKE 'Virtual%%'
          AND created_date >= CURRENT_TIMESTAMP
              - INTERVAL '{int(config.expertise_distribution_creator_activity_window)} days'
          {project_filter_assistants}
        UNION  -- UNION (not UNION ALL) deduplicates creators across both sources
        -- Creators from workflows (marked for weighted bonus)
        SELECT
            project,
            COALESCE(created_by->>'user_id', created_by->>'id') AS creator_id,
            TRUE AS is_workflow_creator
        FROM codemie.workflows
        WHERE date >= CURRENT_TIMESTAMP - INTERVAL '{int(config.expertise_distribution_creator_activity_window)} days'
          {project_filter_workflows}
    ) combined_creators
    GROUP BY project
)
"""


def build_filtered_projects_cte(projects_param: bool = True) -> str:
    """Generate filtered_projects CTE for minimum user threshold filtering.

    Filters projects to only those with >= minimum_users_threshold users.
    This CTE is the single source of truth for project inclusion across all
    adoption framework queries.

    Uses user_stats_all (all users including creators) to count users per project.
    Projects below the threshold are excluded from all adoption calculations.

    Args:
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for filtered_projects CTE

    Depends on:
        - user_stats_all: User aggregation with creators included
        - params: Configuration parameters (minimum_users_threshold)
    """
    project_filter = ""
    if projects_param:
        project_filter = _PROJECT_FILTER_GENERIC

    return f"""
filtered_projects AS (
    SELECT project
    FROM user_stats_all
    CROSS JOIN params p
    WHERE TRUE
      {project_filter}
    GROUP BY project, p.minimum_users_threshold
    HAVING COUNT(DISTINCT user_id)::numeric >= p.minimum_users_threshold
)
"""


def build_user_stats_cte(projects_param: bool = True, include_creators: bool = True) -> str:
    """Generate user_stats CTE.

    Multi-source user aggregation with configurable inclusion of creator activity.
    Each user is counted only once per project, with total usage summed.
    CTE name is automatically determined based on include_creators flag.

    Modes:
    - include_creators=False (D1 - User Engagement): Only conversations → CTE named "user_stats"
    - include_creators=True (D3 - Expertise Distribution): All activity → CTE named "user_stats_all"

    Args:
        projects_param: Whether to include projects parameter filter
        include_creators: Whether to include creator activity (assistants, workflows, index_info creation)

    Returns:
        SQL string for user_stats CTE (name determined by include_creators flag)
    """
    # Determine CTE name based on include_creators flag
    cte_name = "user_stats_all" if include_creators else "user_stats"
    project_filter_conversations = ""
    project_filter_workflow_executions = ""
    project_filter_assistant_creators = ""
    project_filter_workflow_creators = ""
    project_filter_index_info = ""

    if projects_param:
        project_filter_conversations = _PROJECT_FILTER_WITH_PREFIX.format(prefix="c")
        project_filter_workflow_executions = _PROJECT_FILTER_WITH_PREFIX.format(prefix="we")
        project_filter_assistant_creators = _PROJECT_FILTER_GENERIC
        project_filter_workflow_creators = _PROJECT_FILTER_GENERIC
        project_filter_index_info = (
            "AND (CAST(:projects AS text[]) IS NULL OR project_name = ANY(CAST(:projects AS text[])))"
        )

    # Build creator activity UNION ALL clauses (only if include_creators=True)
    creator_unions = ""
    if include_creators:
        creator_unions = f"""
        UNION ALL
        -- Assistant creators (creation counts as usage)
        SELECT
            project,
            COALESCE(created_by->>'user_id', created_by->>'id')::text AS user_id,
            NULLIF(COALESCE(created_by->>'name', created_by->>'username', ''), '') AS user_name,
            1 AS usage_count,
            created_date AS first_used,
            update_date AS last_used
        FROM codemie.assistants
        WHERE id NOT LIKE 'Virtual%%'
          AND created_by IS NOT NULL
          AND COALESCE(created_by->>'user_id', created_by->>'id') IS NOT NULL
          {project_filter_assistant_creators}
        UNION ALL
        -- Workflow creators (creation counts as usage)
        SELECT
            project,
            COALESCE(created_by->>'user_id', created_by->>'id')::text AS user_id,
            NULLIF(COALESCE(created_by->>'name', created_by->>'username', ''), '') AS user_name,
            1 AS usage_count,
            date AS first_used,
            update_date AS last_used
        FROM codemie.workflows
        WHERE created_by IS NOT NULL
          AND COALESCE(created_by->>'user_id', created_by->>'id') IS NOT NULL
          {project_filter_workflow_creators}
        UNION ALL
        -- Index info creators (knowledge base indexing - creation counts as usage)
        SELECT
            project_name AS project,
            COALESCE(created_by->>'user_id', created_by->>'id')::text AS user_id,
            NULLIF(COALESCE(created_by->>'name', created_by->>'username', ''), '') AS user_name,
            1 AS usage_count,
            date AS first_used,
            update_date AS last_used
        FROM codemie.index_info
        WHERE created_by IS NOT NULL
          AND COALESCE(created_by->>'user_id', created_by->>'id') IS NOT NULL
          {project_filter_index_info}
"""

    return f"""
{cte_name} AS (
    SELECT
        combined_users.project,
        combined_users.user_id,
        -- Name resolution priority (conversations.user_name historically stored UUIDs for some records)
        COALESCE(
            MAX(combined_users.user_name) FILTER (WHERE combined_users.user_name ~ '^[A-Z]'),
            MAX(combined_users.user_name) FILTER (WHERE combined_users.user_name
                !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
            MAX(combined_users.user_name)
        ) AS user_name,
        SUM(combined_users.usage_count)::numeric AS total_usage,
        MIN(combined_users.first_used) AS first_used,
        MAX(combined_users.last_used) AS last_used,
        -- Returning user calculation with configurable time window
        -- Window = 0: All-time mode (backward compatible) - any return after first use counts
        -- Window > 0: User must return within N days of first use
        CASE
            WHEN p.returning_user_window = 0 THEN
                CASE WHEN MAX(combined_users.last_used) > MIN(combined_users.first_used) THEN 1 ELSE 0 END
            ELSE
                CASE
                    WHEN MAX(combined_users.last_used) > MIN(combined_users.first_used)
                        AND MAX(combined_users.last_used) <=
                            MIN(combined_users.first_used) + (p.returning_user_window || ' days')::interval
                    THEN 1
                    ELSE 0
                END
        END AS is_returning
    FROM (
        -- Users from assistant conversations (codemie.conversations)
        -- Each conversation counts as multiple interactions based on message pairs
        SELECT
            c.project,
            c.user_id::text AS user_id,
            NULLIF(c.user_name, '') AS user_name,
            -- Count message pairs (user + assistant = 1 interaction)
            -- Divide by 2 since history contains both user and assistant messages
            GREATEST(1, (COALESCE(cm.number_of_messages, 0) + 1) / 2)::int AS usage_count,
            c.date AS first_used,
            c.update_date AS last_used
        FROM codemie.conversations c
        LEFT JOIN codemie.conversation_metrics cm ON cm.conversation_id = c.id
        WHERE (c.is_workflow_conversation = FALSE OR c.is_workflow_conversation IS NULL)
          AND c.user_id IS NOT NULL
          {project_filter_conversations}
        UNION ALL
        -- Users from workflow executions (codemie.workflow_executions)
        -- Each execution is one interaction
        -- FIXED: Uses COALESCE for consistent user_id extraction (UserEntity model)
        SELECT
            we.project,
            COALESCE(we.created_by->>'user_id', we.created_by->>'id')::text AS user_id,
            NULLIF(COALESCE(we.created_by->>'name', we.created_by->>'username', ''), '') AS user_name,
            1 AS usage_count,
            we.date AS first_used,
            we.update_date AS last_used
        FROM codemie.workflow_executions we
        WHERE we.created_by IS NOT NULL
          AND COALESCE(we.created_by->>'user_id', we.created_by->>'id') IS NOT NULL
          {project_filter_workflow_executions}{creator_unions}
    ) combined_users
    CROSS JOIN params p  -- Access configuration parameters
    GROUP BY combined_users.project, combined_users.user_id, p.returning_user_window
    -- Deduplicates users across all sources
)
"""
