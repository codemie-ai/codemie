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

"""Centralized column metadata for AI Adoption Framework endpoints.

Provides consistent column definitions across all analytics endpoints,
ensuring type safety and documentation alignment.
"""

from __future__ import annotations


# =============================================================================
# BASELINE COLUMNS (Common across all endpoints)
# =============================================================================

BASELINE_COLUMNS = [
    {
        "id": "project",
        "label": "Project",
        "type": "string",
        "description": "Project identifier/name",
    },
]

# =============================================================================
# DIMENSION 1: DAILY ACTIVE USERS
# =============================================================================

# Maturity view columns (6 columns - for maturity endpoint)
USER_ENGAGEMENT_COLUMNS = [
    {
        "id": "user_engagement_score",
        "label": "Score",
        "type": "number",
        "format": "score",
        "description": (
            "User engagement composite score (0-100) measuring how actively users adopt AI. "
            "Calculated from: user activation rate (25%), monthly active users (25%), "
            "returning user rate (20%), engagement distribution (15%), "
            "multi-assistant rate (10%), and daily active users (5%)"
        ),
    },
    {
        "id": "dau_ratio",
        "label": "DAU %",
        "type": "number",
        "format": "percentage",
        "description": (
            "Daily Active Users (DAU) - Percentage of total project users who had at least one conversation "
            "in the last 24 hours. Provides real-time pulse of platform activity"
        ),
    },
    {
        "id": "mau_ratio",
        "label": "MAU %",
        "type": "number",
        "format": "percentage",
        "description": (
            "Monthly Active Users (MAU) - Percentage of total project users who had at least one conversation "
            "in the last 30 days. Indicates consistent usage patterns and recent engagement"
        ),
    },
    {
        "id": "user_activation_rate",
        "label": "User Activation Rate",
        "type": "number",
        "format": "percentage",
        "description": (
            "User Activation Rate - Percentage of users who reached meaningful AI usage by exceeding the activation "
            "threshold (configurable, default: 20 interactions) within the configured time window (default: 90 days). "
            "Indicates users who moved beyond experimentation to regular usage"
        ),
    },
    {
        "id": "engagement_distribution",
        "label": "Engagement Distribution",
        "type": "number",
        "format": "score",
        "description": (
            "Engagement Distribution - Shows whether AI usage is balanced across the team or concentrated among "
            "a few power users. Above 0.7 (healthy) = most team members actively use AI, sustainable adoption. "
            "0.4-0.7 (moderate) = some people use AI heavily while others use it less, monitor for improvement. "
            "Below 0.4 (risky) = only a few people drive most activity, high risk if they leave. "
            "Calculated using log-normalized coefficient of variation to handle power-law usage distributions"
        ),
    },
    {
        "id": "returning_user_rate",
        "label": "Returning User Rate",
        "type": "number",
        "format": "percentage",
        "description": (
            "Returning User Rate - Percentage of users who have come back for multiple sessions within the configured "
            "window after first use. Indicates user retention and habitual adoption. "
            "Higher rates show users are finding ongoing value in AI tools, not just trying them once and abandoning"
        ),
    },
]

# Additional columns for detail view only (2 columns)
_USER_ENGAGEMENT_DETAIL_ONLY = [
    {
        "id": "total_users",
        "label": "Total Users",
        "type": "integer",
        "description": (
            "Total unique users registered in the project, including both active and inactive users. "
            "This is the baseline for calculating all percentage-based engagement metrics"
        ),
    },
    {
        "id": "total_interactions",
        "label": "Total Interactions",
        "type": "integer",
        "description": (
            "Total interaction count across all users in the project (all-time cumulative). Each conversation started "
            "with an assistant or workflow counts as one interaction (individual messages within a conversation "
            "do not count separately)"
        ),
    },
]

# Full columns for detail view (8 columns = 5 base + 3 additional)
USER_ENGAGEMENT_COLUMNS_FULL = USER_ENGAGEMENT_COLUMNS + _USER_ENGAGEMENT_DETAIL_ONLY

# =============================================================================
# DIMENSION 2: REUSABILITY
# =============================================================================

# Maturity view columns (8 columns - for maturity endpoint)
ASSET_REUSABILITY_COLUMNS = [
    {
        "id": "asset_reusability_score",
        "label": "Score",
        "type": "number",
        "format": "score",
        "description": (
            "Asset Reusability Score - Measures how well AI assets (assistants, workflows, datasources) "
            "are shared and reused across the team rather than remaining siloed with individual users (0-100). "
            "Combines assistant reuse rate, assistant utilization rate, workflow reuse rate, "
            "workflow utilization rate, and datasource reuse rate"
        ),
    },
    {
        "id": "total_assistants",
        "label": "Total Assistants",
        "type": "integer",
        "description": (
            "Total Assistants - Total number of assistants created in the project (excludes Virtual assistants). "
            "Assistants are configured AI tools with custom instructions, toolkits, and datasource connections "
            "that users interact with through conversations"
        ),
    },
    {
        "id": "total_workflows",
        "label": "Total Workflows",
        "type": "integer",
        "description": (
            "Total Workflows - Total number of workflows defined in the project. "
            "Workflows are automated multi-step AI processes that orchestrate assistants and tools "
            "to complete complex tasks without human intervention between steps"
        ),
    },
    {
        "id": "total_datasources",
        "label": "Total Datasources",
        "type": "integer",
        "description": (
            "Total Datasources - Total number of datasources indexed in the project. "
            "Datasources are searchable knowledge bases (code repositories, Confluence wikis, "
            "Jira tickets, uploaded files) that assistants can reference to provide informed responses"
        ),
    },
    {
        "id": "assistants_reuse_rate",
        "label": "Assistants Reuse Rate",
        "type": "number",
        "format": "percentage",
        "description": (
            "Assistants Reuse Rate - Percentage of assistants used by 2 or more team members (configurable threshold). "
            "Indicates knowledge sharing and collaboration. High reuse means effective prompts and configurations "
            "are being shared across the team. Low reuse suggests assistants remain personal tools, "
            "missing opportunities for team-wide benefit"
        ),
    },
    {
        "id": "assistant_utilization_rate",
        "label": "Assistant Utilization",
        "type": "number",
        "format": "percentage",
        "description": (
            "Assistant Utilization Rate - Percentage of assistants that are actively used "
            "(meeting the activation threshold, default: 20+ interactions). Low utilization indicates "
            "an 'assistant graveyard' where many tools were created but aren't providing value. "
            "High utilization means assets are well-maintained and relevant to user needs"
        ),
    },
    {
        "id": "workflow_reuse_rate",
        "label": "Workflow Reuse Rate",
        "type": "number",
        "format": "percentage",
        "description": (
            "Workflow Reuse Rate - Percentage of workflows executed by 2 or more users in the last 30 days "
            "(configurable threshold). Indicates whether valuable automation patterns are shared across the team. "
            "High reuse shows workflows have been generalized for team use. "
            "Low reuse suggests workflows remain personal automation that hasn't been shared"
        ),
    },
    {
        "id": "workflow_utilization_rate",
        "label": "Workflow Utilization",
        "type": "number",
        "format": "percentage",
        "description": (
            "Workflow Utilization Rate - Percentage of workflows actively executed "
            "(configurable threshold, default: 5+ executions in last 30 days). "
            "High utilization indicates workflows provide consistent value through regular execution. "
            "Low utilization suggests a 'workflow graveyard' where automation was created but isn't being used, "
            "possibly due to unreliable execution or changing needs"
        ),
    },
]

# Additional columns for detail view only (2 columns)
_ASSET_REUSABILITY_DETAIL_ONLY = [
    {
        "id": "datasource_reuse_rate",
        "label": "Datasource Reuse Rate",
        "type": "number",
        "format": "percentage",
        "description": (
            "Datasource Reuse Rate - Percentage of datasources connected to 2 or more assistants "
            "(configurable threshold). High reuse means knowledge bases are effectively leveraged "
            "across multiple tools. Low reuse suggests potential duplication or missed opportunities "
            "to share indexed knowledge across the team"
        ),
    },
    {
        "id": "datasource_utilization_rate",
        "label": "Datasource Utilization",
        "type": "number",
        "format": "percentage",
        "description": (
            "Datasource Utilization Rate - Percentage of datasources actively used by assistants "
            "that meet the activation threshold (default: 20+ interactions). Distinguishes between datasources "
            "that are indexed but unused versus those actually providing value. "
            "Low utilization indicates datasources were set up but aren't being leveraged by active assistants"
        ),
    },
]

# Full columns for detail view (10 columns = 8 base + 2 additional)
ASSET_REUSABILITY_COLUMNS_FULL = ASSET_REUSABILITY_COLUMNS + _ASSET_REUSABILITY_DETAIL_ONLY

# =============================================================================
# DIMENSION 3: AI CHAMPIONS (4 columns: score + 1 total + 2 metrics)
# =============================================================================

EXPERTISE_DISTRIBUTION_COLUMNS = [
    {
        "id": "expertise_distribution_score",
        "label": "Score",
        "type": "number",
        "format": "score",
        "description": (
            "Expertise Distribution Score - Measures how AI expertise and usage is distributed "
            "across the team (0-100). Identifies whether adoption is sustainable "
            "(many users contribute and use AI regularly) or fragile (few power users dominate, "
            "creating dependency risk). High scores indicate balanced distribution where the team "
            "doesn't rely on just a few individuals. Low scores warn that if key power users leave, "
            "AI adoption could collapse. Combines three factors: champion concentration "
            "(how much top users dominate activity), non-champion activity "
            "(whether non-power users are engaged), and creator diversity (how many people create AI assets)"
        ),
    },
    {
        "id": "total_users",
        "label": "Total Users",
        "type": "integer",
        "description": (
            "Total Users - Total unique users registered in the project, including both active and inactive users. "
            "This is the baseline for calculating all percentage-based engagement metrics"
        ),
    },
    {
        "id": "creator_diversity",
        "label": "Creator Diversity",
        "type": "number",
        "format": "percentage",
        "description": (
            "Creator Diversity - Percentage of users who have created at least one assistant or workflow "
            "in the last 90 days (configurable window). High diversity indicates broad ownership "
            "and experimentation across the team. Low diversity means only a few people create AI tools, "
            "limiting innovation and making the team dependent on those creators for customization. "
            "Example: 5% diversity in a 20-person team means only 1 person creates assistants "
            "while 19 others only consume"
        ),
    },
    {
        "id": "champion_health",
        "label": "Champion Health",
        "type": "string",
        "description": (
            "Champion Health - Assesses usage concentration by measuring what percentage of total activity "
            "comes from the top 20% of users (configurable). Returns status: CRITICAL (>80% concentration) = "
            "severe over-reliance on few users, high risk if they leave; WARNING (60-80%) = unbalanced "
            "adoption, usage concentrated but not critical; HEALTHY (40-60%) = optimal, power users lead "
            "but others contribute significantly; FLAT (<40%) = very even distribution, may indicate "
            "low engagement depth overall. Example: CRITICAL status means 2 people out of 10 generate "
            "over 80% of all AI activity"
        ),
    },
]

# =============================================================================
# DIMENSION 4: AI CAPABILITIES (7 columns: score + 2 totals + 4 metrics)
# =============================================================================

FEATURE_ADOPTION_COLUMNS = [
    {
        "id": "feature_adoption_score",
        "label": "Score",
        "type": "number",
        "format": "score",
        "description": (
            "Feature Adoption Score - Measures the sophistication of AI usage through "
            "complexity-based assessment (0-100). Evaluates how teams progress from simple chat assistants "
            "to advanced capabilities using tools, datasources, workflows, and deep conversations. "
            "High scores indicate mature AI practices with sophisticated automation. "
            "Low scores suggest basic usage patterns. Combines workflow count (presence of automation), "
            "feature utilization rate (complexity of assistants and workflows), "
            "and conversation depth (how deeply users engage)"
        ),
    },
    {
        "id": "feature_utilization_rate",
        "label": "Feature Utilization",
        "type": "number",
        "format": "percentage",
        "description": (
            "Feature Utilization Rate - Complexity-weighted assessment measuring sophistication "
            "of assistants and workflows. Evaluates feature combinations (tools, datasources, MCP servers) "
            "and orchestration complexity. Higher rates indicate teams are using advanced capabilities "
            "beyond basic chat"
        ),
    },
    {
        "id": "total_assistants",
        "label": "Total Assistants",
        "type": "integer",
        "description": (
            "Total Assistants - Total number of assistants created in the project (excludes Virtual assistants). "
            "Assistants are configured AI tools with custom instructions, toolkits, and datasource connections "
            "that users interact with through conversations"
        ),
    },
    {
        "id": "total_workflows",
        "label": "Total Workflows",
        "type": "integer",
        "description": (
            "Total Workflows - Total number of workflows defined in the project. "
            "Workflows are automated multi-step AI processes that orchestrate assistants and tools "
            "to complete complex tasks without human intervention between steps"
        ),
    },
    {
        "id": "median_conversation_depth",
        "label": "Median Conversation Depth",
        "type": "number",
        "description": (
            "Median Conversation Depth - Median number of messages per conversation in the last 30 days. "
            "Short conversations (1-2 messages) indicate simple lookups. "
            "Longer conversations (6+ messages) indicate complex problem-solving where users iterate with AI. "
            "Capped at 10 messages for scoring"
        ),
    },
    {
        "id": "assistant_complexity_score",
        "label": "Assistant Complexity",
        "type": "number",
        "format": "score",
        "description": (
            "Assistant Complexity Score - Measures assistant sophistication (0-100) based on feature combinations. "
            "Simple (0%) = no features, Basic (33%) = one feature type, Advanced (67%) = two feature types, "
            "Complex (100%) = all three features (tools + datasources + MCP) with bonus for multiple datasource types"
        ),
    },
    {
        "id": "workflow_complexity_score",
        "label": "Workflow Complexity",
        "type": "number",
        "format": "score",
        "description": (
            "Workflow Complexity Score - Measures workflow sophistication (0-100) based on orchestration complexity. "
            "Simple (0%) = 1-2 states, Basic (33%) = 3-5 states, Advanced (67%) = 6-10 states, "
            "Complex (100%) = 10+ states with extensive tooling and bonus for coordinating multiple assistants"
        ),
    },
]

# =============================================================================
# COMPOSITE SCORES (2 columns: adoption_index + maturity_level)
# =============================================================================

COMPOSITE_COLUMNS = [
    {
        "id": "adoption_index",
        "label": "Adoption Index",
        "type": "number",
        "format": "score",
        "description": "Overall AI maturity score (0-100, 4-dimension framework)",
    },
    {
        "id": "maturity_level",
        "label": "Maturity Level",
        "type": "string",
        "description": "Classification (L1: ASSISTED / L2: AUGMENTED / L3: AGENTIC)",
    },
]

# =============================================================================
# DIMENSION SCORES (4 columns: d1_score, d2_score, d3_score, d4_score)
# =============================================================================

DIMENSION_SCORE_COLUMNS = [
    {
        "id": "user_engagement_score",
        "label": "User Engagement",
        "type": "number",
        "format": "score",
        "description": (
            "Measures active user participation and engagement depth (0-100). "
            "Calculated from: User Activation Rate (30% weight) - proportion reaching meaningful usage threshold, "
            "DAU Ratio (15%) - daily active participation, MAU Ratio (20%) - monthly engagement consistency, "
            "Engagement Distribution (15%) - usage balance across users, "
            "Multi-Assistant Rate (20%) - platform exploration breadth. "
            "Scores below 33 indicate adoption challenges requiring intervention. "
            "Scores above 67 indicate successful daily integration. Primary indicator for user adoption initiatives"
        ),
    },
    {
        "id": "asset_reusability_score",
        "label": "Asset Reusability",
        "type": "number",
        "format": "score",
        "description": (
            "Evaluates knowledge sharing effectiveness and asset utilization (0-100). "
            "Measures collaborative use of assistants, workflows, and datasources versus siloed individual ownership. "
            "Components: Assistants Reuse Rate (30% weight), Assistant Utilization Rate (25%), "
            "Workflow Reuse Rate (25%), Workflow Utilization Rate (10%), Datasource Reuse Rate (10%). "
            "Low scores indicate inefficient duplication and underutilized assets. "
            "High scores demonstrate effective knowledge transfer and standardized best practices. "
            "Critical for maximizing ROI on AI investments"
        ),
    },
    {
        "id": "expertise_distribution_score",
        "label": "Expertise Distribution",
        "type": "number",
        "format": "score",
        "description": (
            "Assesses sustainability and concentration risk of AI expertise (0-100). "
            "Evaluates three factors: Champion Concentration (35% weight) - activity distribution among top users, "
            "Non-Champion Activity (40%) - engagement levels outside power user group, "
            "Creator Diversity (25%) - breadth of tool creation capability. "
            "CRITICAL status (concentration >80%) indicates severe key-person dependency risk. "
            "HEALTHY status (40-60% concentration) indicates sustainable adoption. "
            "Essential metric for succession planning and capability resilience"
        ),
    },
    {
        "id": "feature_adoption_score",
        "label": "Feature Adoption",
        "type": "number",
        "format": "score",
        "description": (
            "Measures sophistication level of AI implementation (0-100). "
            "Assesses organizational capability through: Workflow Count (30% weight) - automation maturity, "
            "Feature Utilization Rate (50%) - complexity of assistants and workflows using advanced features "
            "(tools, datasources, MCP), Conversation Depth (20%) - engagement complexity indicating "
            "problem-solving versus simple queries. Low scores indicate basic usage limiting productivity gains. "
            "High scores demonstrate advanced automation capabilities and strategic AI leverage. "
            "Key metric for L2 to L3 maturity progression"
        ),
    },
]


# =============================================================================
# HELPER FUNCTIONS FOR ENDPOINT-SPECIFIC COLUMN SETS
# =============================================================================


def get_maturity_metrics() -> list[dict]:
    """Get metrics for /ai-maturity-overview endpoint (SummariesResponse format).

    Returns:
        List of metric definitions (not full column definitions):
        - maturity_level, adoption_index
    """
    return [
        {
            "id": "maturity_level",
            "label": "Maturity Level",
            "format": "string",
            "description": (
                "AI maturity classification indicating organizational adoption stage. "
                "L1 ASSISTED (0-33): Sporadic individual usage requiring structured onboarding initiatives. "
                "L2 AUGMENTED (34-66): Established team adoption with shared assets and designated champions. "
                "L3 AGENTIC (67-100): Comprehensive integration with automated workflows and distributed expertise. "
                "Determines strategic focus areas and resource allocation priorities"
            ),
        },
        {
            "id": "adoption_index",
            "label": "Adoption Index",
            "format": "score",
            "description": (
                "Composite maturity score (0-100) measuring AI adoption effectiveness across four dimensions: "
                "User Engagement (30% weight), Asset Reusability (30%), Expertise Distribution (20%), "
                "and Feature Adoption (20%). Serves as primary KPI for tracking adoption progress and comparing "
                "organizational units. Higher scores indicate stronger AI integration and business value realization"
            ),
        },
    ]


def get_dimensions_columns() -> list[dict]:
    """Get all columns for /adoption-dimensions endpoint (23 total).

    Returns:
        List of column definitions in order:
        - project (1)
        - D1 columns (6)
        - D2 columns (8)
        - D3 columns (3)
        - D4 columns (5)
        - Composite scores (2)
        - Dimension scores (4) - added at the end for compatibility
        Total: 29 columns
    """
    return (
        BASELINE_COLUMNS
        + USER_ENGAGEMENT_COLUMNS
        + ASSET_REUSABILITY_COLUMNS
        + EXPERTISE_DISTRIBUTION_COLUMNS
        + FEATURE_ADOPTION_COLUMNS
        + COMPOSITE_COLUMNS
        + DIMENSION_SCORE_COLUMNS
    )


def get_user_engagement_detail_columns() -> list[dict]:
    """Get columns for /d1-details endpoint (9 total).

    Returns:
        List of column definitions: project + all D1 metrics (8 including total_interactions and returning_user_rate)
        Column order matches UI screenshot (source of truth)
    """
    return BASELINE_COLUMNS + USER_ENGAGEMENT_COLUMNS_FULL


def get_asset_reusability_detail_columns() -> list[dict]:
    """Get columns for /d2-details endpoint (11 total).

    Returns:
        List of column definitions: project + D2 metrics including score (10)
    """
    return BASELINE_COLUMNS + ASSET_REUSABILITY_COLUMNS_FULL


def get_expertise_distribution_detail_columns() -> list[dict]:
    """Get columns for /d3-details endpoint (5 total).

    Returns:
        List of column definitions: project + D3 metrics including score (4)
    """
    return BASELINE_COLUMNS + EXPERTISE_DISTRIBUTION_COLUMNS


def get_feature_adoption_detail_columns() -> list[dict]:
    """Get columns for /d4-details endpoint (8 total).

    Returns:
        List of column definitions: project + D4 metrics including score (7)
    """
    return BASELINE_COLUMNS + FEATURE_ADOPTION_COLUMNS
