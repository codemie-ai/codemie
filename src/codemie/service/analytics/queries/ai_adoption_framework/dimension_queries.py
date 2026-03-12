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

"""Dimension-specific query CTEs for AI Adoption Framework.

Provides CTE builders for each of the 4 dimensions (D1-D4).
Each function generates a specific metric calculation used in dimension scoring.
"""

from __future__ import annotations

from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig

# SQL filter constants
_PROJECT_FILTER_GENERIC = "AND (CAST(:projects AS text[]) IS NULL OR project = ANY(CAST(:projects AS text[])))"
_PROJECT_FILTER_WITH_PREFIX = (
    "AND (CAST(:projects AS text[]) IS NULL OR {prefix}.project = ANY(CAST(:projects AS text[])))"
)
_SQL_WHERE = "WHERE "
_SQL_AND = "AND "


# =============================================================================
# DIMENSION 1: DAILY ACTIVE USERS (3 CTEs)
# =============================================================================


def build_user_metrics_cte(include_creators: bool = False) -> str:
    """Generate user_metrics CTE.

    Calculates activation, activity windows, and engagement distribution for D1.
    Source CTE is automatically determined based on include_creators flag.

    Modes:
    - include_creators=False: Uses user_stats (conversations only) - for dimension queries
    - include_creators=True: Uses user_stats_all (all users) - for maturity overview

    Args:
        include_creators: Whether to calculate metrics across all users (True) or conversation users only (False)

    Returns:
        SQL string for user_metrics CTE
    """
    # Determine source CTE name based on include_creators flag
    source_cte = "user_stats_all" if include_creators else "user_stats"

    return f"""
user_metrics AS (
    SELECT
        us.project,
        COUNT(*)::numeric AS total_users,
        -- Activation (with optional time window)
        COUNT(CASE
            WHEN us.total_usage >= p.activation_threshold
            AND (p.user_engagement_activation_window = 0 OR us.last_used >= CURRENT_TIMESTAMP - (p.user_engagement_activation_window || ' days')::interval)
            THEN 1
        END)::numeric AS activated_users,
        SUM(us.total_usage)::numeric AS total_interactions,
        -- Active windows
        COUNT(CASE WHEN us.last_used >= CURRENT_TIMESTAMP - INTERVAL '1 day' THEN 1 END)::numeric AS active_1d,
        COUNT(CASE WHEN us.last_used >= CURRENT_TIMESTAMP - (p.active_window_short || ' days')::interval THEN 1 END)::numeric AS active_7d,
        COUNT(CASE WHEN us.last_used >= CURRENT_TIMESTAMP - (p.active_window_long || ' days')::interval THEN 1 END)::numeric AS active_30d,
        -- Return users
        COUNT(CASE WHEN us.is_returning = 1 THEN 1 END)::numeric AS returning_users,
        -- Engagement distribution: log-normalized CV handles power-law usage distributions.
        -- LN(x+1) compresses outliers so stddev/mean stays < 1 for typical adoption patterns.
        STDDEV(LN(us.total_usage + 1))::numeric AS interaction_stddev,
        AVG(LN(us.total_usage + 1))::numeric    AS interaction_mean
    FROM {source_cte} us
    CROSS JOIN params p
    GROUP BY us.project
)
"""


def build_multi_assistant_users_cte(projects_param: bool = True) -> str:
    """Generate multi_assistant_users CTE (intermediate).

    Identifies users with 2+ distinct assistants across ALL their conversations.
    This is an intermediate CTE used by multi_assistant_stats.

    Depends on: conversations

    Args:
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for multi_assistant_users CTE
    """
    project_filter = ""
    if projects_param:
        project_filter = _PROJECT_FILTER_WITH_PREFIX.format(prefix="c")

    return f"""
multi_assistant_users AS (
    SELECT
        c.project,
        c.user_id,
        COUNT(DISTINCT assistant_id) as distinct_assistant_count
    FROM codemie.conversations c
    -- Flatten the assistant_ids JSONB array into individual rows
    CROSS JOIN LATERAL jsonb_array_elements_text(c.assistant_ids) AS assistant_id
    WHERE (c.is_workflow_conversation = FALSE OR c.is_workflow_conversation IS NULL)
      AND c.user_id IS NOT NULL
      AND c.assistant_ids IS NOT NULL
      {project_filter}
    GROUP BY c.project, c.user_id
    HAVING COUNT(DISTINCT assistant_id) >= 2
)
"""


def build_multi_assistant_stats_cte() -> str:
    """Generate multi_assistant_stats CTE.

    Counts users with 2+ distinct assistants across ALL their conversations.
    This handles both cases:
    1. Single conversation with multiple assistants (assistant_ids array has 2+ items)
    2. Multiple conversations with different assistants

    Depends on: multi_assistant_users (must be created first)

    Returns:
        SQL string for multi_assistant_stats CTE
    """
    return """
multi_assistant_stats AS (
    SELECT
        project,
        COUNT(*)::numeric AS multi_user_count
    FROM multi_assistant_users
    GROUP BY project
)
"""


def build_concentration_cte(include_creators: bool = False) -> str:
    """Generate concentration CTE.

    Calculates top 20% user concentration (how much usage is concentrated in top users).
    Uses CEIL to ensure at least 1 user is counted even for small teams.
    Source CTE is automatically determined based on include_creators flag.

    Modes:
    - include_creators=False: Uses user_stats (conversations only) - for D1 User Engagement
    - include_creators=True: Uses user_stats_all (all users) - for D3 Expertise Distribution

    Args:
        include_creators: Whether to calculate concentration across all users (True) or conversation users only (False)

    Returns:
        SQL string for concentration CTE
    """
    # Determine source CTE name based on include_creators flag
    source_cte = "user_stats_all" if include_creators else "user_stats"

    return f"""
concentration AS (
    SELECT
        project,
        (SUM(CASE WHEN user_rank <= CEIL(total_users * p.top_user_percentile) THEN total_usage ELSE 0 END) /
         NULLIF(SUM(total_usage), 0) * 100)::numeric AS top_pct_concentration
    FROM (
        SELECT
            us.project,
            us.user_id,
            us.total_usage,
            ROW_NUMBER() OVER (PARTITION BY us.project ORDER BY us.total_usage DESC) AS user_rank,
            COUNT(*) OVER (PARTITION BY us.project) AS total_users
        FROM {source_cte} us
    ) ranked
    CROSS JOIN params p
    GROUP BY project
)
"""


# =============================================================================
# DIMENSION 2: REUSABILITY (5 CTEs)
# =============================================================================


def build_assistant_usage_cte(projects_param: bool = True, single_project: bool = False) -> str:
    """Generate assistant_usage CTE with project filtering.

    Per-assistant usage aggregation (sum, user count, last used).
    Aggregates from conversations table by flattening assistant_ids array.
    Filters to specified projects for accurate project-specific metrics.

    Args:
        projects_param: Whether to include projects parameter filter (for multi-project queries)
        single_project: Whether to filter conversations by single project early (for drill-down optimization)

    Returns:
        SQL string for assistant_usage CTE
    """
    # Early conversation filtering - applies BEFORE LATERAL join (critical!)
    # This filters conversations BEFORE JSONB array expansion (5-10x faster)
    conversation_filter = ""
    if single_project:
        conversation_filter = "\n          AND c.project = :project"
    elif projects_param:
        conversation_filter = f"\n          {_PROJECT_FILTER_WITH_PREFIX.format(prefix='c')}"

    project_filter = ""
    if projects_param:
        project_filter = f"""
    INNER JOIN codemie.assistants a ON conversation_assistant_usage.assistant_id = a.id
    WHERE a.id NOT LIKE 'Virtual%%'
      {_PROJECT_FILTER_WITH_PREFIX.format(prefix="a")}"""

    return f"""
assistant_usage AS (
    SELECT
        assistant_id,
        SUM(usage_count)::numeric AS usage_sum,
        COUNT(DISTINCT user_id)::numeric AS user_count,
        MAX(last_used) AS last_used
    FROM (
        SELECT
            assistant_id.value AS assistant_id,
            c.user_id,
            -- Count message pairs (user + assistant = 1 interaction)
            GREATEST(1, (COALESCE(cm.number_of_messages, 0) + 1) / 2)::int AS usage_count,
            c.update_date AS last_used
        FROM codemie.conversations c
        LEFT JOIN codemie.conversation_metrics cm ON cm.conversation_id = c.id
        CROSS JOIN LATERAL jsonb_array_elements_text(c.assistant_ids) AS assistant_id(value)
        WHERE (c.is_workflow_conversation = FALSE OR c.is_workflow_conversation IS NULL)
          AND c.user_id IS NOT NULL
          AND c.assistant_ids IS NOT NULL{conversation_filter}
    ) conversation_assistant_usage
    {project_filter}
    GROUP BY assistant_id
)
"""


def build_assistant_adoption_cte(projects_param: bool = True) -> str:
    """Generate assistant_adoption CTE.

    Active and team-adopted assistant classification.
    Depends on: assistants, assistant_usage, params

    Args:
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for assistant_adoption CTE
    """
    project_filter = ""
    if projects_param:
        project_filter = _PROJECT_FILTER_WITH_PREFIX.format(prefix="a")

    return f"""
assistant_adoption AS (
    SELECT
        a.project,
        COUNT(
            DISTINCT CASE WHEN au.last_used >= CURRENT_TIMESTAMP - (p.active_window_long || ' days')::interval THEN a.id END
        )::numeric AS active_assistants,
        COUNT(
            DISTINCT CASE WHEN COALESCE(au.user_count, 0) >= p.team_adopted_threshold THEN a.id END
        )::numeric AS team_adopted_assistants
    FROM codemie.assistants a
    CROSS JOIN params p
    LEFT JOIN assistant_usage au ON a.id = au.assistant_id
    WHERE a.id NOT LIKE 'Virtual%%'
      {project_filter}
    GROUP BY a.project
)
"""


def build_workflow_reuse_stats_cte(config: AIAdoptionConfig, projects_param: bool = True) -> str:
    """Generate workflow_reuse_stats CTE.

    Workflows used by 2+ users (30-day window).
    Depends on: workflows, workflow_executions

    Args:
        config: AIAdoptionConfig for time window
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for workflow_reuse_stats CTE
    """
    project_filter_workflows = ""
    project_filter_executions = ""
    if projects_param:
        project_filter_workflows = _SQL_WHERE + _PROJECT_FILTER_WITH_PREFIX.format(prefix="w").replace(_SQL_AND, "")
        project_filter_executions = _PROJECT_FILTER_GENERIC

    return f"""
workflow_reuse_stats AS (
    SELECT
        w.project,
        COUNT(DISTINCT CASE WHEN user_count >= p.workflow_reuse_threshold THEN w.id END)::numeric AS multi_user_workflows
    FROM codemie.workflows w
    CROSS JOIN params p
    LEFT JOIN (
        SELECT
            workflow_id,
            COUNT(DISTINCT created_by)::numeric AS user_count
        FROM codemie.workflow_executions
        WHERE date >= CURRENT_TIMESTAMP - INTERVAL '{int(config.user_engagement_active_window_long)} days'
          {project_filter_executions}
        GROUP BY workflow_id
    ) we ON w.id = we.workflow_id
    {project_filter_workflows}
    GROUP BY w.project
)
"""


def build_workflow_utilization_stats_cte(config: AIAdoptionConfig, projects_param: bool = True) -> str:
    """Generate workflow_utilization_stats CTE.

    Workflows with 10+ executions in 30-day window.
    Depends on: workflows, workflow_executions, params

    Args:
        config: AIAdoptionConfig for time window
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for workflow_utilization_stats CTE
    """
    project_filter_workflows = ""
    project_filter_executions = ""
    if projects_param:
        project_filter_workflows = _SQL_WHERE + _PROJECT_FILTER_WITH_PREFIX.format(prefix="w").replace(_SQL_AND, "")
        project_filter_executions = _PROJECT_FILTER_GENERIC

    return f"""
workflow_utilization_stats AS (
    SELECT
        w.project,
        COUNT(DISTINCT CASE WHEN COALESCE(we.execution_count, 0) >= p.workflow_activation_threshold THEN w.id END)::numeric AS active_workflows
    FROM codemie.workflows w
    CROSS JOIN params p
    LEFT JOIN (
        SELECT
            workflow_id,
            COUNT(*)::numeric AS execution_count
        FROM codemie.workflow_executions
        WHERE date >= CURRENT_TIMESTAMP - INTERVAL '{int(config.user_engagement_active_window_long)} days'
          {project_filter_executions}
        GROUP BY workflow_id
    ) we ON w.id = we.workflow_id
    {project_filter_workflows}
    GROUP BY w.project
)
"""


def build_datasource_reuse_stats_cte(projects_param: bool = True) -> str:
    """Generate datasource_reuse_stats CTE.

    Datasources shared across 2+ assistants.
    Uses index_info table as the base source of truth for datasources,
    then joins with assistants.context to count usage.

    Args:
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for datasource_reuse_stats CTE
    """
    project_filter_index_info = ""
    project_filter_assistants = ""
    if projects_param:
        project_filter_index_info = (
            "AND (CAST(:projects AS text[]) IS NULL OR ii.project_name = ANY(CAST(:projects AS text[])))"
        )
        project_filter_assistants = _PROJECT_FILTER_WITH_PREFIX.format(prefix="a")

    return f"""
datasource_reuse_stats AS (
    SELECT
        ii.project_name AS project,
        COUNT(DISTINCT ii.id)::numeric AS total_datasources,
        COUNT(DISTINCT CASE WHEN COALESCE(ds_usage.assistant_count, 0) >= 2 THEN ii.id END)::numeric AS shared_datasources
    FROM codemie.index_info ii
    LEFT JOIN (
        -- Count how many assistants use each datasource (from assistants.context field)
        -- Join on repo_name = context.name (not indexId)
        SELECT
            context_item->>'name' AS datasource_name,
            COUNT(DISTINCT a.id)::numeric AS assistant_count
        FROM codemie.assistants a
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(a.context, '[]'::jsonb)) AS context_item
        WHERE a.id NOT LIKE 'Virtual%%'
          AND context_item->>'name' IS NOT NULL
          {project_filter_assistants}
        GROUP BY context_item->>'name'
    ) ds_usage ON ii.repo_name = ds_usage.datasource_name
    WHERE TRUE
      {project_filter_index_info}
    GROUP BY ii.project_name
)
"""


def build_datasource_utilization_stats_cte(projects_param: bool = True) -> str:
    """Generate datasource_utilization_stats CTE.

    Calculates active datasources (used by assistants meeting activation threshold).
    Depends on: index_info, assistants, assistant_usage, params

    Args:
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for datasource_utilization_stats CTE
    """
    project_filter_index_info = ""
    project_filter_assistants = ""
    if projects_param:
        project_filter_index_info = (
            "AND (CAST(:projects AS text[]) IS NULL OR ii.project_name = ANY(CAST(:projects AS text[])))"
        )
        project_filter_assistants = _PROJECT_FILTER_WITH_PREFIX.format(prefix="a")

    return f"""
datasource_utilization_stats AS (
    SELECT
        ii.project_name AS project,
        -- Active datasources (used by activated assistants)
        COUNT(DISTINCT CASE
            WHEN COALESCE(max_usage.max_usage, 0) >= p.activation_threshold
            THEN ii.id
        END)::numeric AS active_datasources
    FROM codemie.index_info ii
    CROSS JOIN params p
    LEFT JOIN (
        -- For each datasource, find max usage across all assistants using it
        SELECT
            context_item->>'name' AS datasource_name,
            MAX(COALESCE(au.usage_sum, 0))::numeric AS max_usage
        FROM codemie.assistants a
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(a.context, '[]'::jsonb)) AS context_item
        LEFT JOIN assistant_usage au ON a.id = au.assistant_id
        WHERE a.id NOT LIKE 'Virtual%%'
          AND context_item->>'name' IS NOT NULL
          {project_filter_assistants}
        GROUP BY context_item->>'name'
    ) max_usage ON ii.repo_name = max_usage.datasource_name
    WHERE TRUE
      {project_filter_index_info}
    GROUP BY ii.project_name
)
"""


# =============================================================================
# DIMENSION 3: AI CHAMPIONS (1 CTE)
# =============================================================================


def build_non_champion_stats_cte(include_creators: bool = True) -> str:
    """Generate non_champion_stats CTE.

    Bottom 50% median activity (non-champion activity level).
    Source CTE is automatically determined based on include_creators flag.

    Modes:
    - include_creators=False: Uses user_stats (conversations only)
    - include_creators=True: Uses user_stats_all (all users) - default for D3 Expertise Distribution

    Args:
        include_creators: Whether to calculate non-champion stats across all users (True) or conversation users only (False)

    Returns:
        SQL string for non_champion_stats CTE
    """
    # Determine source CTE name based on include_creators flag
    source_cte = "user_stats_all" if include_creators else "user_stats"

    return f"""
non_champion_stats AS (
    SELECT
        project,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY total_usage)
            FILTER (WHERE decile <= 5)::numeric AS bottom_50_median
    FROM (
        SELECT
            us.project,
            us.total_usage,
            NTILE(10) OVER (PARTITION BY us.project ORDER BY us.total_usage) AS decile
        FROM {source_cte} us
    ) segmented
    GROUP BY project
)
"""


# =============================================================================
# DIMENSION 4: AI CAPABILITIES (3 CTEs)
# =============================================================================


def build_conversation_depth_cte(config: AIAdoptionConfig, projects_param: bool = True) -> str:
    """Generate conversation_depth CTE.

    Median messages per conversation (30-day window).
    Depends on: conversations, assistants, conversation_metrics (optional)

    Args:
        config: AIAdoptionConfig for time window
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for conversation_depth CTE
    """
    project_filter = ""
    if projects_param:
        project_filter = _PROJECT_FILTER_WITH_PREFIX.format(prefix="a")

    return f"""
conversation_depth AS (
    SELECT
        a.project,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
            COALESCE(cm.number_of_messages, jsonb_array_length(c.history))
        )::numeric AS median_messages
    FROM codemie.conversations c
    JOIN codemie.assistants a ON c.initial_assistant_id = a.id
    LEFT JOIN codemie.conversation_metrics cm ON cm.conversation_id = c.id
    WHERE c.date >= CURRENT_TIMESTAMP - INTERVAL '{int(config.feature_adoption_conversation_depth_window)} days'
      {project_filter}
    GROUP BY a.project
)
"""


def build_feature_stats_cte(projects_param: bool = True) -> str:
    """Generate feature_stats CTE.

    Assistant complexity classification (simple/basic/advanced/complex).
    Based on feature combinations (tools, datasources, MCP servers).
    Depends on: assistants

    Args:
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for feature_stats CTE
    """
    project_filter = ""
    if projects_param:
        project_filter = _PROJECT_FILTER_GENERIC

    return f"""
feature_stats AS (
    SELECT
        project,
        COUNT(*)::numeric AS total_assistants,

        -- Individual feature counts
        COUNT(
            CASE WHEN jsonb_array_length(COALESCE(toolkits, '[]'::jsonb)) > 0 THEN 1 END
        )::numeric AS tool_enabled,
        COUNT(
            CASE WHEN jsonb_array_length(COALESCE(context, '[]'::jsonb)) > 0 THEN 1 END
        )::numeric AS datasource_enabled,
        COUNT(
            CASE WHEN jsonb_array_length(COALESCE(mcp_servers, '[]'::jsonb)) > 0 THEN 1 END
        )::numeric AS mcp_enabled,

        -- Complexity levels (combination of features)
        -- Level 0: No features (simple)
        COUNT(
            CASE WHEN jsonb_array_length(COALESCE(toolkits, '[]'::jsonb)) = 0
                AND jsonb_array_length(COALESCE(context, '[]'::jsonb)) = 0
                AND jsonb_array_length(COALESCE(mcp_servers, '[]'::jsonb)) = 0
            THEN 1 END
        )::numeric AS simple_assistants,

        -- Level 1: Single feature type (basic)
        COUNT(
            CASE WHEN (
                (jsonb_array_length(COALESCE(toolkits, '[]'::jsonb)) > 0 AND
                 jsonb_array_length(COALESCE(context, '[]'::jsonb)) = 0 AND
                 jsonb_array_length(COALESCE(mcp_servers, '[]'::jsonb)) = 0) OR
                (jsonb_array_length(COALESCE(toolkits, '[]'::jsonb)) = 0 AND
                 jsonb_array_length(COALESCE(context, '[]'::jsonb)) > 0 AND
                 jsonb_array_length(COALESCE(mcp_servers, '[]'::jsonb)) = 0) OR
                (jsonb_array_length(COALESCE(toolkits, '[]'::jsonb)) = 0 AND
                 jsonb_array_length(COALESCE(context, '[]'::jsonb)) = 0 AND
                 jsonb_array_length(COALESCE(mcp_servers, '[]'::jsonb)) > 0)
            ) THEN 1 END
        )::numeric AS basic_assistants,

        -- Level 2: Two feature types (advanced)
        COUNT(
            CASE WHEN (
                (jsonb_array_length(COALESCE(toolkits, '[]'::jsonb)) > 0 AND
                 jsonb_array_length(COALESCE(context, '[]'::jsonb)) > 0 AND
                 jsonb_array_length(COALESCE(mcp_servers, '[]'::jsonb)) = 0) OR
                (jsonb_array_length(COALESCE(toolkits, '[]'::jsonb)) > 0 AND
                 jsonb_array_length(COALESCE(context, '[]'::jsonb)) = 0 AND
                 jsonb_array_length(COALESCE(mcp_servers, '[]'::jsonb)) > 0) OR
                (jsonb_array_length(COALESCE(toolkits, '[]'::jsonb)) = 0 AND
                 jsonb_array_length(COALESCE(context, '[]'::jsonb)) > 0 AND
                 jsonb_array_length(COALESCE(mcp_servers, '[]'::jsonb)) > 0)
            ) THEN 1 END
        )::numeric AS advanced_assistants,

        -- Level 3: All three feature types (complex)
        COUNT(
            CASE WHEN jsonb_array_length(COALESCE(toolkits, '[]'::jsonb)) > 0
                AND jsonb_array_length(COALESCE(context, '[]'::jsonb)) > 0
                AND jsonb_array_length(COALESCE(mcp_servers, '[]'::jsonb)) > 0
            THEN 1 END
        )::numeric AS complex_assistants,

        -- Datasource diversity (multiple types)
        COUNT(
            CASE WHEN (
                SELECT COUNT(DISTINCT ds->>'context_type')
                FROM jsonb_array_elements(COALESCE(context, '[]'::jsonb)) AS ds
            ) >= 2 THEN 1 END
        )::numeric AS multi_datasource_types

    FROM codemie.assistants
    WHERE id NOT LIKE 'Virtual%%'
      {project_filter}
    GROUP BY project
)
"""


def build_workflow_complexity_stats_cte(projects_param: bool = True) -> str:
    """Generate workflow_complexity_stats CTE.

    Workflow complexity classification (simple/basic/advanced/complex).
    Based on states count, tools, custom nodes, and assistants.
    Depends on: workflows

    Args:
        projects_param: Whether to include projects parameter filter

    Returns:
        SQL string for workflow_complexity_stats CTE
    """
    project_filter = ""
    if projects_param:
        project_filter = _SQL_WHERE + _PROJECT_FILTER_GENERIC.replace(_SQL_AND, "")

    return f"""
workflow_complexity_stats AS (
    SELECT
        project,
        COUNT(*)::numeric AS total_workflows,

        -- Complexity levels based on states count and features
        -- Level 0: Simple (1-2 states, no tools/custom nodes)
        COUNT(
            CASE WHEN jsonb_array_length(COALESCE(states, '[]'::jsonb)) <= 2
                AND jsonb_array_length(COALESCE(tools, '[]'::jsonb)) = 0
                AND jsonb_array_length(COALESCE(custom_nodes, '[]'::jsonb)) = 0
            THEN 1 END
        )::numeric AS simple_workflows,

        -- Level 1: Basic (3-5 states OR has tools/custom_nodes but <= 5 states)
        COUNT(
            CASE WHEN (
                (jsonb_array_length(COALESCE(states, '[]'::jsonb)) BETWEEN 3 AND 5 AND
                 jsonb_array_length(COALESCE(tools, '[]'::jsonb)) = 0 AND
                 jsonb_array_length(COALESCE(custom_nodes, '[]'::jsonb)) = 0) OR
                (jsonb_array_length(COALESCE(states, '[]'::jsonb)) <= 5 AND
                 (jsonb_array_length(COALESCE(tools, '[]'::jsonb)) > 0 OR
                  jsonb_array_length(COALESCE(custom_nodes, '[]'::jsonb)) > 0))
            ) THEN 1 END
        )::numeric AS basic_workflows,

        -- Level 2: Advanced (6-10 states OR has multiple tools/nodes)
        COUNT(
            CASE WHEN (
                (jsonb_array_length(COALESCE(states, '[]'::jsonb)) BETWEEN 6 AND 10 AND
                 jsonb_array_length(COALESCE(tools, '[]'::jsonb)) +
                 jsonb_array_length(COALESCE(custom_nodes, '[]'::jsonb)) <= 5) OR
                (jsonb_array_length(COALESCE(states, '[]'::jsonb)) <= 10 AND
                 jsonb_array_length(COALESCE(tools, '[]'::jsonb)) +
                 jsonb_array_length(COALESCE(custom_nodes, '[]'::jsonb)) > 5)
            ) THEN 1 END
        )::numeric AS advanced_workflows,

        -- Level 3: Complex (10+ states AND multiple tools/nodes/assistants)
        COUNT(
            CASE WHEN jsonb_array_length(COALESCE(states, '[]'::jsonb)) > 10
                AND (jsonb_array_length(COALESCE(tools, '[]'::jsonb)) +
                     jsonb_array_length(COALESCE(custom_nodes, '[]'::jsonb)) > 3)
            THEN 1 END
        )::numeric AS complex_workflows,

        -- Workflows with multiple assistants (higher complexity)
        COUNT(
            CASE WHEN jsonb_array_length(COALESCE(assistants, '[]'::jsonb)) >= 3
            THEN 1 END
        )::numeric AS multi_assistant_workflows

    FROM codemie.workflows
    {project_filter}
    GROUP BY project
)
"""


# =============================================================================
# USER ENGAGEMENT DRILL-DOWN (1 CTE)
# =============================================================================


def build_user_engagement_users_detail_cte(single_project: bool = False) -> str:
    """Generate user_engagement_users_detail CTE for drill-down.

    Returns detailed user statistics with activity classifications.
    This CTE provides user-level data for a single project.

    Args:
        single_project: Whether to filter conversations by single project early (for drill-down optimization)

    Returns:
        SQL string for user_engagement_users_detail CTE

    Depends on:
        - params: Configuration parameters
        - user_stats_all: User aggregation (conversations + workflow executions + creators)
    """
    # Early conversation filtering for single-project drill-down
    # This filters conversations BEFORE JSONB parsing
    conversation_filter = ""
    if single_project:
        conversation_filter = "\n      AND c.project = :project"

    return f"""
user_assistant_usage AS (
    SELECT
        c.project,
        c.user_id,
        COUNT(DISTINCT assistant_id) AS distinct_assistant_count
    FROM codemie.conversations c
    CROSS JOIN LATERAL jsonb_array_elements_text(c.assistant_ids) AS assistant_id
    WHERE (c.is_workflow_conversation = FALSE OR c.is_workflow_conversation IS NULL)
      AND c.user_id IS NOT NULL{conversation_filter}
    GROUP BY c.project, c.user_id
),

user_engagement_users_detail AS (
    SELECT
        us.project,
        us.user_id,
        -- Use display name from conversations/created_by, fall back to user_id if unavailable
        COALESCE(us.user_name, us.user_id) AS user_name,
        us.total_usage AS total_interactions,
        us.first_used,
        us.last_used,
        us.is_returning,

        -- Activity classifications
        CASE
            WHEN us.total_usage >= p.activation_threshold
                AND (p.user_engagement_activation_window = 0
                     OR us.last_used >= CURRENT_TIMESTAMP - (p.user_engagement_activation_window || ' days')::interval)
            THEN TRUE ELSE FALSE
        END AS is_activated,

        CASE WHEN us.last_used >= CURRENT_TIMESTAMP - INTERVAL '1 day'
            THEN TRUE ELSE FALSE END AS is_daily_active,
        CASE WHEN us.last_used >= CURRENT_TIMESTAMP - (p.active_window_short || ' days')::interval
            THEN TRUE ELSE FALSE END AS is_weekly_active,
        CASE WHEN us.last_used >= CURRENT_TIMESTAMP - (p.active_window_long || ' days')::interval
            THEN TRUE ELSE FALSE END AS is_monthly_active,

        -- Multi-assistant usage
        COALESCE(uau.distinct_assistant_count, 0) AS distinct_assistant_count,
        CASE WHEN COALESCE(uau.distinct_assistant_count, 0) >= 2
            THEN TRUE ELSE FALSE END AS is_multi_assistant_user,

        -- User type classification
        CASE
            WHEN us.total_usage >= p.activation_threshold * 2
                 AND us.last_used >= CURRENT_TIMESTAMP - INTERVAL '7 days' THEN 'power_user'
            WHEN us.total_usage >= p.activation_threshold
                 AND us.is_returning = 1 THEN 'engaged'
            WHEN us.is_returning = 1 THEN 'occasional'
            WHEN us.last_used < CURRENT_TIMESTAMP - (p.user_engagement_activation_window || ' days')::interval
                THEN 'inactive'
            ELSE 'new'
        END AS user_type,

        -- Days since last activity
        EXTRACT(DAY FROM CURRENT_TIMESTAMP - us.last_used)::integer AS days_since_last_activity,

        -- Engagement score (0-100)
        ROUND(
            LEAST(
                (us.total_usage::numeric / NULLIF(p.activation_threshold, 0) * 50) +  -- Usage (50%)
                (CASE WHEN us.is_returning = 1 THEN 25 ELSE 0 END) +                   -- Returning (25%)
                (CASE WHEN COALESCE(uau.distinct_assistant_count, 0) >= 2
                    THEN 25 ELSE 0 END),                                                -- Multi-assistant (25%)
                100
            ),
        1) AS engagement_score

    FROM user_stats_all us
    CROSS JOIN params p
    LEFT JOIN user_assistant_usage uau
        ON us.project = uau.project AND us.user_id = uau.user_id
)
"""


def build_assistant_reusability_detail_cte() -> str:
    """Generate assistant_reusability_detail CTE for drill-down.

    Returns detailed assistant statistics with usage and adoption classifications.
    This CTE provides assistant-level data for a single project.

    Returns:
        SQL string for assistant_reusability_detail CTE

    Depends on:
        - params: Configuration parameters
        - assistant_usage: Assistant usage aggregation (reused from dimension_queries)
    """
    return """
assistant_reusability_detail AS (
    SELECT
        a.id AS assistant_id,
        a.name AS assistant_name,
        a.project,
        a.description,
        a.created_date,
        COALESCE(a.created_by->>'user_id', a.created_by->>'id') AS creator_id,
        COALESCE(a.created_by->>'username', a.created_by->>'user_id', a.created_by->>'id') AS creator_name,

        -- Usage metrics (from assistant_usage CTE)
        COALESCE(au.usage_sum, 0)::integer AS total_usage,
        COALESCE(au.user_count, 0)::integer AS unique_users,
        au.last_used,

        -- Days since last activity
        CASE
            WHEN au.last_used IS NULL THEN NULL
            ELSE EXTRACT(DAY FROM CURRENT_TIMESTAMP - au.last_used)::integer
        END AS days_since_last_used,

        -- Status classifications
        CASE
            WHEN au.last_used >= CURRENT_TIMESTAMP - (p.active_window_long || ' days')::interval THEN 'Active'
            ELSE 'Inactive'
        END AS is_active,

        CASE
            WHEN COALESCE(au.user_count, 0) >= p.team_adopted_threshold THEN TRUE
            ELSE FALSE
        END AS is_team_adopted,

        -- Feature flags
        CASE
            WHEN jsonb_array_length(COALESCE(a.context, '[]'::jsonb)) > 0 THEN TRUE
            ELSE FALSE
        END AS has_datasources,

        CASE
            WHEN jsonb_array_length(COALESCE(a.toolkits, '[]'::jsonb)) > 0 THEN TRUE
            ELSE FALSE
        END AS has_toolkits,

        CASE
            WHEN jsonb_array_length(COALESCE(a.mcp_servers, '[]'::jsonb)) > 0 THEN TRUE
            ELSE FALSE
        END AS has_mcp_servers,

        -- Feature counts
        jsonb_array_length(COALESCE(a.context, '[]'::jsonb))::integer AS datasource_count,
        jsonb_array_length(COALESCE(a.toolkits, '[]'::jsonb))::integer AS toolkit_count,
        jsonb_array_length(COALESCE(a.mcp_servers, '[]'::jsonb))::integer AS mcp_server_count,

        -- Complexity classification (matching feature_stats CTE logic from lines 508-584)
        CASE
            WHEN jsonb_array_length(COALESCE(a.toolkits, '[]'::jsonb)) = 0
                AND jsonb_array_length(COALESCE(a.context, '[]'::jsonb)) = 0
                AND jsonb_array_length(COALESCE(a.mcp_servers, '[]'::jsonb)) = 0
            THEN 'simple'
            WHEN (jsonb_array_length(COALESCE(a.toolkits, '[]'::jsonb)) > 0 AND
                  jsonb_array_length(COALESCE(a.context, '[]'::jsonb)) = 0 AND
                  jsonb_array_length(COALESCE(a.mcp_servers, '[]'::jsonb)) = 0) OR
                 (jsonb_array_length(COALESCE(a.toolkits, '[]'::jsonb)) = 0 AND
                  jsonb_array_length(COALESCE(a.context, '[]'::jsonb)) > 0 AND
                  jsonb_array_length(COALESCE(a.mcp_servers, '[]'::jsonb)) = 0) OR
                 (jsonb_array_length(COALESCE(a.toolkits, '[]'::jsonb)) = 0 AND
                  jsonb_array_length(COALESCE(a.context, '[]'::jsonb)) = 0 AND
                  jsonb_array_length(COALESCE(a.mcp_servers, '[]'::jsonb)) > 0)
            THEN 'basic'
            WHEN jsonb_array_length(COALESCE(a.toolkits, '[]'::jsonb)) > 0
                AND jsonb_array_length(COALESCE(a.context, '[]'::jsonb)) > 0
                AND jsonb_array_length(COALESCE(a.mcp_servers, '[]'::jsonb)) > 0
            THEN 'complex'
            ELSE 'advanced'
        END AS complexity_level

    FROM codemie.assistants a
    CROSS JOIN params p
    LEFT JOIN assistant_usage au ON a.id = au.assistant_id
    WHERE a.id NOT LIKE 'Virtual%%'
      AND a.project = :project
)
"""


def build_workflow_reusability_detail_cte() -> str:
    """Generate workflow_reusability_detail CTE for drill-down.

    Returns detailed workflow statistics with execution and reuse classifications.
    This CTE provides workflow-level data for a single project.

    Returns:
        SQL string for workflow_reusability_detail CTE

    Depends on:
        - params: Configuration parameters
        - workflow_execution_stats: Workflow execution aggregation
    """
    return """
workflow_execution_stats AS (
    SELECT
        workflow_id,
        COUNT(*)::integer AS execution_count,
        COUNT(DISTINCT created_by)::integer AS unique_users,
        MAX(date) AS last_executed
    FROM codemie.workflow_executions
    WHERE date >= CURRENT_TIMESTAMP - (SELECT user_engagement_activation_window FROM params) * INTERVAL '1 day'
    GROUP BY workflow_id
),

workflow_reusability_detail AS (
    SELECT
        w.id AS workflow_id,
        w.name AS workflow_name,
        w.project,
        w.description,
        w.date AS created_date,
        COALESCE(w.created_by->>'user_id', w.created_by->>'id') AS creator_id,
        COALESCE(w.created_by->>'username', w.created_by->>'user_id', w.created_by->>'id') AS creator_name,

        -- Execution metrics
        COALESCE(wes.execution_count, 0)::integer AS execution_count,
        COALESCE(wes.unique_users, 0)::integer AS unique_users,
        wes.last_executed,

        -- Days since last execution
        CASE
            WHEN wes.last_executed IS NULL THEN NULL
            ELSE EXTRACT(DAY FROM CURRENT_TIMESTAMP - wes.last_executed)::integer
        END AS days_since_last_executed,

        -- Status classifications
        CASE
            WHEN COALESCE(wes.execution_count, 0) >= p.workflow_activation_threshold THEN 'Active'
            ELSE 'Inactive'
        END AS is_active,

        CASE
            WHEN COALESCE(wes.unique_users, 0) >= p.workflow_reuse_threshold THEN TRUE
            ELSE FALSE
        END AS is_multi_user,

        -- Component counts
        jsonb_array_length(COALESCE(w.states, '[]'::jsonb))::integer AS state_count,
        jsonb_array_length(COALESCE(w.tools, '[]'::jsonb))::integer AS tool_count,
        jsonb_array_length(COALESCE(w.custom_nodes, '[]'::jsonb))::integer AS custom_node_count,
        jsonb_array_length(COALESCE(w.assistants, '[]'::jsonb))::integer AS assistant_count,

        -- Complexity classification (matching workflow_complexity_stats logic)
        CASE
            WHEN jsonb_array_length(COALESCE(w.states, '[]'::jsonb)) <= 2
                AND jsonb_array_length(COALESCE(w.tools, '[]'::jsonb)) = 0
                AND jsonb_array_length(COALESCE(w.custom_nodes, '[]'::jsonb)) = 0
            THEN 'simple'
            WHEN (jsonb_array_length(COALESCE(w.states, '[]'::jsonb)) BETWEEN 3 AND 5 AND
                  jsonb_array_length(COALESCE(w.tools, '[]'::jsonb)) = 0 AND
                  jsonb_array_length(COALESCE(w.custom_nodes, '[]'::jsonb)) = 0) OR
                 (jsonb_array_length(COALESCE(w.states, '[]'::jsonb)) <= 5 AND
                  (jsonb_array_length(COALESCE(w.tools, '[]'::jsonb)) > 0 OR
                   jsonb_array_length(COALESCE(w.custom_nodes, '[]'::jsonb)) > 0))
            THEN 'basic'
            WHEN jsonb_array_length(COALESCE(w.states, '[]'::jsonb)) > 10
                AND (jsonb_array_length(COALESCE(w.tools, '[]'::jsonb)) +
                     jsonb_array_length(COALESCE(w.custom_nodes, '[]'::jsonb)) > 3)
            THEN 'complex'
            ELSE 'advanced'
        END AS complexity_level

    FROM codemie.workflows w
    CROSS JOIN params p
    LEFT JOIN workflow_execution_stats wes ON w.id = wes.workflow_id
    WHERE w.project = :project
)
"""


def build_datasource_reusability_detail_cte() -> str:
    """Generate datasource_reusability_detail CTE for drill-down.

    Returns detailed datasource statistics with usage and sharing classifications.
    This CTE provides datasource-level data for a single project.

    Returns:
        SQL string for datasource_reusability_detail CTE

    Depends on:
        - params: Configuration parameters
        - assistant_usage: Assistant usage aggregation
    """
    return """
assistant_contexts AS (
    -- OPTIMIZATION: Unpack assistant contexts ONCE (no nested queries)
    -- Filter by project BEFORE unpacking (reduces from 21k to ~200 assistants)
    SELECT
        a.id AS assistant_id,
        ctx->>'name' AS datasource_name,
        COALESCE(au.usage_sum, 0)::integer AS assistant_usage
    FROM codemie.assistants a
    CROSS JOIN LATERAL jsonb_array_elements(COALESCE(a.context, '[]'::jsonb)) AS ctx
    LEFT JOIN assistant_usage au ON a.id = au.assistant_id
    WHERE a.id NOT LIKE 'Virtual%%'
      AND a.project = :project  -- ← CRITICAL: Filter by project BEFORE unpacking
      AND ctx->>'name' IS NOT NULL
),

datasource_assistant_usage AS (
    SELECT
        ii.id AS datasource_id,
        ii.repo_name AS datasource_name,
        ac.assistant_id,
        ac.assistant_usage
    FROM codemie.index_info ii
    JOIN assistant_contexts ac ON ii.repo_name = ac.datasource_name
),

datasource_reusability_detail AS (
    SELECT
        ii.id AS datasource_id,
        ii.repo_name AS datasource_name,
        ii.project_name AS project,
        ii.description,
        ii.index_type AS datasource_type,
        ii.date AS created_date,
        ii.update_date AS last_indexed,
        COALESCE(ii.created_by->>'user_id', ii.created_by->>'id') AS creator_id,
        COALESCE(ii.created_by->>'username', ii.created_by->>'user_id', ii.created_by->>'id') AS creator_name,

        -- Usage metrics
        COALESCE(dau.assistant_count, 0)::integer AS assistant_count,
        COALESCE(dau.max_usage, 0)::integer AS max_usage,

        -- Days since last indexed
        CASE
            WHEN ii.update_date IS NULL THEN NULL
            ELSE EXTRACT(DAY FROM CURRENT_TIMESTAMP - ii.update_date)::integer
        END AS days_since_last_indexed,

        -- Status classifications
        CASE
            WHEN COALESCE(dau.max_usage, 0) >= p.activation_threshold THEN 'Active'
            ELSE 'Inactive'
        END AS is_active,

        CASE
            WHEN COALESCE(dau.assistant_count, 0) >= 2 THEN TRUE
            ELSE FALSE
        END AS is_shared

    FROM codemie.index_info ii
    CROSS JOIN params p
    LEFT JOIN (
        SELECT
            datasource_id,
            COUNT(DISTINCT assistant_id)::integer AS assistant_count,
            MAX(assistant_usage)::integer AS max_usage
        FROM datasource_assistant_usage
        GROUP BY datasource_id
    ) dau ON ii.id = dau.datasource_id
)
"""
