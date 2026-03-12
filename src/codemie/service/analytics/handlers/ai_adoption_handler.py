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

"""Handler for project AI adoption analytics."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.clients.postgres import PostgresClient
from codemie.rest_api.security.user import User
from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig

logger = logging.getLogger(__name__)

COLUMN_LABEL_CREATED_BY = "Created By"
COLUMN_DESC_CREATOR_USERNAME = "Creator username"
COLUMN_DESC_CREATION_TIMESTAMP = "Creation timestamp"


class AIAdoptionHandler:
    """Handler for project AI adoption analytics using PostgreSQL."""

    def __init__(self, user: User):
        """Initialize adoption handler with user context."""
        self._user = user

    async def get_ai_adoption_overview(
        self,
        projects: list[str] | None = None,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI adoption overview metrics for dashboard widgets.

        Returns aggregate counts across all accessible projects that meet
        the minimum_users_threshold requirement:
        - Total Projects (filtered by minimum_users_threshold)
        - Total Users (from filtered projects only)
        - Total Assistants (from filtered projects only)
        - Total Workflows (from filtered projects only)
        - Total Datasources (from filtered projects only)

        IMPORTANT: All counts respect the filtered_projects CTE, ensuring
        consistency with the maturity endpoint. Projects with fewer users than
        minimum_users_threshold are excluded from all calculations.

        Access Control:
        - Admin: All projects or filtered projects
        - Non-Admin: Only their accessible projects

        Args:
            projects: Filter by specific projects (admin only for cross-project filtering)
            config: Optional custom configuration (uses default if None)

        Returns:
            SummariesResponse with 5 metrics, filtered by minimum_users_threshold
        """
        logger.info(f"Requesting overview metrics with projects={projects}")

        # Use provided config or create default
        config = config or AIAdoptionConfig()

        # Apply access control using existing helper
        target_projects = self._get_accessible_projects(projects)

        # Build query using query builder (no query building logic in handler)
        from codemie.service.analytics.queries.ai_adoption_framework import query_builder

        query, params = query_builder.build_overview_query(config, target_projects)

        async with AsyncSession(PostgresClient.get_async_engine()) as session:
            result = await session.execute(query, params)
            row = result.first()

            # Extract counts from row
            counts = self._extract_overview_counts(row)

        # Build metrics and response
        metrics = self._build_overview_metrics(counts)

        return {
            "data": {"metrics": metrics},
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data_as_of": datetime.now(timezone.utc).isoformat(),
                "filters_applied": {"projects": target_projects},
                "execution_time_ms": 0,
            },
        }

    def _extract_overview_counts(self, row) -> dict:
        """Extract count values from database row.

        Args:
            row: Database result row or None

        Returns:
            Dict with count values (0 if no data)
        """
        if not row:
            return {
                "total_projects": 0,
                "total_users": 0,
                "total_assistants": 0,
                "total_workflows": 0,
                "total_datasources": 0,
            }

        return {
            "total_projects": row.total_projects or 0,
            "total_users": row.total_users or 0,
            "total_assistants": row.total_assistants or 0,
            "total_workflows": row.total_workflows or 0,
            "total_datasources": row.total_datasources or 0,
        }

    def _build_overview_metrics(self, counts: dict) -> list[dict]:
        """Build overview metrics list from counts.

        Args:
            counts: Dict with count values

        Returns:
            List of metric dictionaries
        """
        return [
            {
                "id": "total_projects",
                "label": "Total Projects",
                "type": "number",
                "value": counts["total_projects"],
                "format": "integer",
                "description": "Number of projects being tracked",
            },
            {
                "id": "total_users",
                "label": "Total Users",
                "type": "number",
                "value": counts["total_users"],
                "format": "integer",
                "description": "Total unique users across all projects",
            },
            {
                "id": "total_assistants",
                "label": "Total Assistants",
                "type": "number",
                "value": counts["total_assistants"],
                "format": "integer",
                "description": "Total assistants created",
            },
            {
                "id": "total_workflows",
                "label": "Total Workflows",
                "type": "number",
                "value": counts["total_workflows"],
                "format": "integer",
                "description": "Total workflows created",
            },
            {
                "id": "total_datasources",
                "label": "Total Datasources",
                "type": "number",
                "value": counts["total_datasources"],
                "format": "integer",
                "description": "Total datasources configured",
            },
        ]

    # =========================================================================
    # NEW ENDPOINTS - MODULAR ADOPTION FRAMEWORK
    # =========================================================================

    async def get_ai_adoption_maturity(
        self,
        projects: list[str] | None = None,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI Adoption Maturity with hierarchical structure.

        Returns SummariesResponse with 6 top-level metrics:
        - adoption_index (overview, no secondary metrics)
        - maturity_level (overview, no secondary metrics)
        - user_engagement_score (with nested secondary_metrics)
        - asset_reusability_score (with nested secondary_metrics)
        - expertise_distribution_score (with nested secondary_metrics)
        - feature_adoption_score (with nested secondary_metrics)

        Args:
            projects: Filter by specific projects (admin only for cross-project)
            config: Optional custom configuration

        Returns:
            SummariesResponse dict with hierarchical metric structure
        """

        # Access control
        target_projects = self._get_accessible_projects(projects)

        # Build query using new modular framework
        from codemie.service.analytics.queries.ai_adoption_framework import AIAdoptionConfig, query_builder

        # Use provided config or create default
        config = config or AIAdoptionConfig()

        query, params = query_builder.build_maturity_query(config, target_projects)

        # Execute query
        async with AsyncSession(PostgresClient.get_async_engine()) as session:
            result = await session.execute(query, params)
            row = result.first()

            if not row:
                # Return empty response if no data
                return self._empty_maturity_response(target_projects)

        # Build metrics from row data
        metrics = self._build_metrics_from_row(row)

        return self._build_maturity_response(metrics, target_projects)

    def _build_metrics_from_row(self, row) -> list[dict]:
        """Build metrics list from a data row (real or mock).

        This method is used by both get_ai_adoption_maturity and _empty_maturity_response
        to avoid code duplication.

        Args:
            row: Database row or mock row with metric values

        Returns:
            List of metric dictionaries with hierarchical structure
        """
        from codemie.service.analytics.queries.ai_adoption_framework.column_definitions import (
            USER_ENGAGEMENT_COLUMNS,
            ASSET_REUSABILITY_COLUMNS,
            EXPERTISE_DISTRIBUTION_COLUMNS,
            FEATURE_ADOPTION_COLUMNS,
            DIMENSION_SCORE_COLUMNS,
            get_maturity_metrics,
        )

        metrics = []
        maturity_cols = get_maturity_metrics()

        # 1. Add overview metrics (standalone, no secondary_metrics)
        for col_def in maturity_cols:
            metrics.append(self._build_metric(col_def, row))

        # 2-5. Add dimension metrics with secondary metrics
        for score_id, dim_cols in [
            ('user_engagement_score', USER_ENGAGEMENT_COLUMNS),
            ('asset_reusability_score', ASSET_REUSABILITY_COLUMNS),
            ('expertise_distribution_score', EXPERTISE_DISTRIBUTION_COLUMNS),
            ('feature_adoption_score', FEATURE_ADOPTION_COLUMNS),
        ]:
            label_def = next((c for c in DIMENSION_SCORE_COLUMNS if c['id'] == score_id), None)
            dim_metric = self._build_dimension_metric(score_id, dim_cols, row, label_def)
            metrics.append(dim_metric)

        return metrics

    def _build_maturity_response(self, metrics: list[dict], target_projects: list[str] | None) -> dict:
        """Build standard maturity response structure.

        Args:
            metrics: List of metric dictionaries
            target_projects: List of project filters applied

        Returns:
            Response dict with data and metadata
        """
        return {
            'data': {'metrics': metrics},
            'metadata': {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'data_as_of': datetime.now(timezone.utc).isoformat(),
                'filters_applied': {'projects': target_projects},
                'execution_time_ms': 0,
            },
        }

    def _build_metric(self, col_def: dict, row) -> dict:
        """Build single metric object from column definition and row data."""
        value = getattr(row, col_def['id'], None)
        return {
            'id': col_def['id'],
            'label': col_def['label'],
            'type': 'number' if col_def.get('format') == 'score' else 'string',
            'value': float(value) if value is not None and col_def.get('format') == 'score' else value,
            'format': col_def.get('format'),
            'description': col_def.get('description'),
        }

    def _build_dimension_metric(self, score_id: str, columns: list[dict], row, label_def: dict | None = None) -> dict:
        """Build dimension metric with nested secondary_metrics.

        Args:
            score_id: ID of the main score metric (e.g., 'user_engagement_score')
            columns: List of column definitions for this dimension
            row: Database row with all metric values
            label_def: Optional dimension label definition from DIMENSION_SCORE_COLUMNS (overrides columns label)

        Returns:
            Metric dict with nested secondary_metrics array
        """
        # Find score column definition
        score_col = next((c for c in columns if c['id'] == score_id), None)
        if not score_col:
            raise ValueError(f'Score column {score_id} not found in column definitions')

        # Build main metric, using label_def if provided for correct dimension label
        if label_def:
            # Use the label and description from DIMENSION_SCORE_COLUMNS
            score_col_with_label = score_col.copy()
            score_col_with_label['label'] = label_def['label']
            score_col_with_label['description'] = label_def['description']
            main_metric = self._build_metric(score_col_with_label, row)
        else:
            main_metric = self._build_metric(score_col, row)

        # Build secondary metrics from columns that exist in the row
        # Query only returns needed columns, so we include all available columns
        secondary_metrics = []
        for col_def in columns:
            if col_def['id'] != score_id:
                # Check if column exists in row (query may not include all columns)
                if not hasattr(row, col_def['id']):
                    continue

                value = getattr(row, col_def['id'], None)
                metric_type = col_def.get('type', 'number')

                secondary_metrics.append(
                    {
                        'id': col_def['id'],
                        'label': col_def['label'],
                        'type': metric_type,
                        'value': self._format_metric_value(value, metric_type),
                        'format': col_def.get('format'),
                        'description': col_def.get('description'),
                    }
                )

        # Add secondary_metrics to main metric
        main_metric['secondary_metrics'] = secondary_metrics

        return main_metric

    def _format_metric_value(self, value, metric_type: str):
        """Format metric value based on type."""
        if value is None:
            return 0 if metric_type in ('number', 'integer') else None

        if metric_type == 'integer':
            return int(value)
        elif metric_type == 'number':
            return float(value)
        else:
            return value

    def _empty_maturity_response(self, target_projects: list[str]) -> dict:
        """Return empty maturity response when no data available."""

        # Create a mock row with zero values for the query columns
        # This matches what the optimized SQL query returns
        class MockRow:
            # Overview
            adoption_index = 0.0
            maturity_level = 'N/A'
            # User Engagement
            user_engagement_score = 0.0
            user_activation_rate = 0.0
            dau_ratio = 0.0
            mau_ratio = 0.0
            engagement_distribution = 0.0
            total_users = 0
            # Asset Reusability
            asset_reusability_score = 0.0
            assistants_reuse_rate = 0.0
            assistant_utilization_rate = 0.0
            workflow_reuse_rate = 0.0
            workflow_utilization_rate = 0.0
            # Expertise Distribution
            expertise_distribution_score = 0.0
            creator_diversity = 0.0
            champion_health = 'N/A'
            # Feature Adoption
            feature_adoption_score = 0.0
            median_conversation_depth = 0.0
            feature_utilization_rate = 0.0

        mock_row = MockRow()

        # Build metrics using the same logic as normal response
        metrics = self._build_metrics_from_row(mock_row)

        return self._build_maturity_response(metrics, target_projects)

    async def get_ai_adoption_user_engagement(
        self,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI Adoption User Engagement project-level metrics.

        Returns TabularResponse with User Engagement columns only:
        - project
        - user_activation_rate, dau_ratio, mau_ratio
        - engagement_distribution
        - total_users, total_interactions
        - returning_user_rate

        Args:
            projects: Filter by specific projects (admin only for cross-project)
            page: Page number (0-indexed)
            per_page: Results per page
            config: Optional custom configuration

        Returns:
            TabularResponse dict with User Engagement metrics
        """
        from codemie.service.analytics.queries.ai_adoption_framework.column_definitions import (
            get_user_engagement_detail_columns,
        )

        def map_user_engagement_row(row) -> dict:
            """Map database row to User Engagement result dict."""
            return {
                "project": row.project,
                "user_engagement_score": float(row.user_engagement_score) if row.user_engagement_score else 0.0,
                "dau_ratio": float(row.dau_ratio) if row.dau_ratio else 0.0,
                "total_users": row.total_users,
                "total_interactions": row.total_interactions,
                "user_activation_rate": float(row.user_activation_rate) if row.user_activation_rate else 0.0,
                "mau_ratio": float(row.mau_ratio) if row.mau_ratio else 0.0,
                "engagement_distribution": float(row.engagement_distribution) if row.engagement_distribution else 0.0,
                "returning_user_rate": float(row.returning_user_rate) if row.returning_user_rate else 0.0,
            }

        return await self._get_dimension_metrics_generic(
            projects=projects,
            page=page,
            per_page=per_page,
            query_builder_method="build_user_engagement_metrics_query",
            columns_getter=get_user_engagement_detail_columns,
            row_mapper=map_user_engagement_row,
            config=config,
        )

    async def get_user_engagement_users(
        self,
        project: str,
        page: int = 0,
        per_page: int = 20,
        user_type: str | None = None,
        activity_level: str | None = None,
        multi_assistant_only: bool | None = None,
        sort_by: str = 'engagement_score',
        sort_order: str = 'desc',
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get user-level drill-down for User Engagement dimension.

        Returns TabularResponse with user-level data for a single project.
        """
        from codemie.service.analytics.queries.ai_adoption_framework import query_builder
        from datetime import datetime, timezone

        # Use default config if none provided
        config = config or AIAdoptionConfig()

        logger.info(f"Fetching user engagement users for project={project}")

        # Build main query
        query, params = query_builder.build_user_engagement_users_query(
            config=config,
            project=project,
            page=page,
            per_page=per_page,
            user_type_filter=user_type,
            activity_level_filter=activity_level,
            multi_assistant_filter=multi_assistant_only,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        # Build count query
        count_query, count_params = query_builder.build_user_engagement_users_count_query(
            config=config,
            project=project,
            user_type_filter=user_type,
            activity_level_filter=activity_level,
            multi_assistant_filter=multi_assistant_only,
        )

        # Execute queries
        rows = []
        total_count = 0

        async with AsyncSession(PostgresClient.get_async_engine()) as session:
            # Get total count first
            count_result = await session.execute(count_query, count_params)
            total_count = count_result.scalar() or 0

            # Execute main query
            result = await session.execute(query, params)
            for row in result:
                # Map row to dict with proper typing
                rows.append(
                    {
                        "user_name": row.user_name,
                        "total_interactions": int(row.total_interactions),
                        "first_used": row.first_used.isoformat() if row.first_used else None,
                        "last_used": row.last_used.isoformat() if row.last_used else None,
                        "days_since_last_activity": int(row.days_since_last_activity),
                        "is_activated": bool(row.is_activated),
                        "is_returning": bool(row.is_returning),
                        "is_daily_active": bool(row.is_daily_active),
                        "is_weekly_active": bool(row.is_weekly_active),
                        "is_monthly_active": bool(row.is_monthly_active),
                        "is_multi_assistant_user": bool(row.is_multi_assistant_user),
                        "distinct_assistant_count": int(row.distinct_assistant_count),
                        "user_type": row.user_type,
                        "engagement_score": float(row.engagement_score) if row.engagement_score else 0.0,
                    }
                )

        # Build response with column definitions
        columns = self._get_user_engagement_users_columns()

        # Calculate total pages
        total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 0

        response = {
            "data": {
                "columns": columns,
                "rows": rows,
                "totals": None,  # No totals row for user drill-down
            },
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data_as_of": datetime.now(timezone.utc).isoformat(),
                "filters_applied": {
                    "project": project,
                    "user_type": user_type,
                    "activity_level": activity_level,
                    "multi_assistant_only": multi_assistant_only,
                    "sort_by": sort_by,
                    "sort_order": sort_order,
                },
                "execution_time_ms": 0,
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_count": total_count,
                "has_more": page < total_pages - 1 if total_pages > 0 else False,
            },
        }

        return response

    def _get_user_engagement_users_columns(self) -> list[dict]:
        """Column definitions for user-level drill-down table."""
        return [
            {"id": "user_name", "label": "User", "type": "string", "format": None, "description": "User display name"},
            {
                "id": "user_type",
                "label": "Type",
                "type": "string",
                "format": "badge",
                "description": "User classification",
            },
            {
                "id": "engagement_score",
                "label": "Engagement Score",
                "type": "number",
                "format": "score",
                "description": "Composite engagement score 0-100",
            },
            {
                "id": "total_interactions",
                "label": "Interactions",
                "type": "integer",
                "format": "number",
                "description": "Total interaction count",
            },
            {
                "id": "distinct_assistant_count",
                "label": "Assistants Used",
                "type": "integer",
                "format": "number",
                "description": "Number of unique assistants used",
            },
            {
                "id": "days_since_last_activity",
                "label": "Days Inactive",
                "type": "integer",
                "format": "number",
                "description": "Days since last activity",
            },
            {
                "id": "first_used",
                "label": "First Seen",
                "type": "date",
                "format": "date",
                "description": "First interaction timestamp",
            },
            {
                "id": "last_used",
                "label": "Last Active",
                "type": "date",
                "format": "relative",
                "description": "Last interaction timestamp",
            },
            {
                "id": "is_daily_active",
                "label": "Daily",
                "type": "boolean",
                "format": "badge",
                "description": "Active within last 24 hours",
            },
            {
                "id": "is_weekly_active",
                "label": "Weekly",
                "type": "boolean",
                "format": "badge",
                "description": "Active within last 7 days",
            },
            {
                "id": "is_monthly_active",
                "label": "Monthly",
                "type": "boolean",
                "format": "badge",
                "description": "Active within last 30 days",
            },
            {
                "id": "is_multi_assistant_user",
                "label": "Multi-Assistant",
                "type": "boolean",
                "format": "badge",
                "description": "Uses 2+ distinct assistants",
            },
        ]

    async def get_assistant_reusability_detail(
        self,
        project: str,
        page: int = 0,
        per_page: int = 20,
        status: str | None = None,
        adoption: str | None = None,
        sort_by: str = 'total_usage',
        sort_order: str = 'desc',
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get assistant-level drill-down for Asset Reusability dimension.

        Returns TabularResponse with assistant-level data for a single project.
        """
        from codemie.service.analytics.queries.ai_adoption_framework import query_builder
        from datetime import datetime, timezone

        # Use default config if none provided
        config = config or AIAdoptionConfig()

        logger.info(f"Fetching assistant reusability detail for project={project}")

        # Build main query
        query, params = query_builder.build_assistant_reusability_detail_query(
            config=config,
            project=project,
            page=page,
            per_page=per_page,
            status_filter=status,
            adoption_filter=adoption,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        # Build count query
        count_query, count_params = query_builder.build_assistant_reusability_detail_count_query(
            config=config,
            project=project,
            status_filter=status,
            adoption_filter=adoption,
        )

        # Execute queries
        rows = []
        total_count = 0

        async with AsyncSession(PostgresClient.get_async_engine()) as session:
            # Get total count first
            count_result = await session.execute(count_query, count_params)
            total_count = count_result.scalar() or 0

            # Execute main query
            result = await session.execute(query, params)
            for row in result:
                rows.append(
                    {
                        "assistant_id": row.assistant_id,
                        "assistant_name": row.assistant_name,
                        "project": row.project,
                        "description": row.description,
                        "total_usage": int(row.total_usage),
                        "unique_users": int(row.unique_users),
                        "last_used": row.last_used.isoformat() if row.last_used else None,
                        "days_since_last_used": int(row.days_since_last_used)
                        if row.days_since_last_used is not None
                        else None,
                        "is_active": row.is_active,
                        "is_team_adopted": bool(row.is_team_adopted),
                        "datasource_count": int(row.datasource_count),
                        "toolkit_count": int(row.toolkit_count),
                        "mcp_server_count": int(row.mcp_server_count),
                        "creator_id": row.creator_id,
                        "creator_name": row.creator_name,
                        "created_date": row.created_date.isoformat() if row.created_date else None,
                    }
                )

        # Build response with column definitions
        columns = self._get_assistant_reusability_detail_columns()

        response = {
            "data": {
                "columns": columns,
                "rows": rows,
                "totals": None,  # No totals row for asset drill-down
            },
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data_as_of": datetime.now(timezone.utc).isoformat(),
                "filters_applied": {
                    "project": project,
                    "status": status,
                    "adoption": adoption,
                    "sort_by": sort_by,
                    "sort_order": sort_order,
                },
                "execution_time_ms": 0,
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_count": total_count,
                "has_more": (page + 1) * per_page < total_count,
            },
        }

        return response

    def _get_assistant_reusability_detail_columns(self) -> list[dict]:
        """Column definitions for assistant-level drill-down table."""
        return [
            {
                "id": "assistant_name",
                "label": "Assistant Name",
                "type": "string",
                "format": None,
                "description": "Assistant display name",
            },
            {
                "id": "is_active",
                "label": "Status",
                "type": "string",
                "format": "badge",
                "description": "Active (≥20 interactions) or Inactive",
            },
            {
                "id": "is_team_adopted",
                "label": "Adoption",
                "type": "boolean",
                "format": "badge",
                "description": "Team-Adopted (≥2 users) or Single-User",
            },
            {
                "id": "total_usage",
                "label": "Total Usage",
                "type": "integer",
                "format": "number",
                "description": "Total interaction count",
            },
            {
                "id": "unique_users",
                "label": "Unique Users",
                "type": "integer",
                "format": "number",
                "description": "Number of unique users",
            },
            {
                "id": "days_since_last_used",
                "label": "Days Idle",
                "type": "integer",
                "format": "number",
                "description": "Days since last usage",
            },
            {
                "id": "last_used",
                "label": "Last Used",
                "type": "date",
                "format": "relative",
                "description": "Last usage timestamp",
            },
            {
                "id": "datasource_count",
                "label": "# Datasources",
                "type": "integer",
                "format": "number",
                "description": "Number of datasources",
            },
            {
                "id": "toolkit_count",
                "label": "# Toolkits",
                "type": "integer",
                "format": "number",
                "description": "Number of toolkits",
            },
            {
                "id": "mcp_server_count",
                "label": "# MCP Servers",
                "type": "integer",
                "format": "number",
                "description": "Number of MCP servers",
            },
            {
                "id": "creator_name",
                "label": COLUMN_LABEL_CREATED_BY,
                "type": "string",
                "format": None,
                "description": COLUMN_DESC_CREATOR_USERNAME,
            },
            {
                "id": "created_date",
                "label": "Created",
                "type": "date",
                "format": "date",
                "description": COLUMN_DESC_CREATION_TIMESTAMP,
            },
        ]

    async def get_workflow_reusability_detail(
        self,
        project: str,
        page: int = 0,
        per_page: int = 20,
        status: str | None = None,
        reuse: str | None = None,
        sort_by: str = 'execution_count',
        sort_order: str = 'desc',
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get workflow-level drill-down for Asset Reusability dimension.

        Returns TabularResponse with workflow-level data for a single project.
        """
        from codemie.service.analytics.queries.ai_adoption_framework import query_builder
        from datetime import datetime, timezone

        # Use default config if none provided
        config = config or AIAdoptionConfig()

        logger.info(f"Fetching workflow reusability detail for project={project}")

        # Build main query
        query, params = query_builder.build_workflow_reusability_detail_query(
            config=config,
            project=project,
            page=page,
            per_page=per_page,
            status_filter=status,
            reuse_filter=reuse,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        # Build count query
        count_query, count_params = query_builder.build_workflow_reusability_detail_count_query(
            config=config,
            project=project,
            status_filter=status,
            reuse_filter=reuse,
        )

        # Execute queries
        rows = []
        total_count = 0

        async with AsyncSession(PostgresClient.get_async_engine()) as session:
            # Get total count first
            count_result = await session.execute(count_query, count_params)
            total_count = count_result.scalar() or 0

            # Execute main query
            result = await session.execute(query, params)
            for row in result:
                rows.append(
                    {
                        "workflow_id": row.workflow_id,
                        "workflow_name": row.workflow_name,
                        "project": row.project,
                        "description": row.description,
                        "execution_count": int(row.execution_count),
                        "unique_users": int(row.unique_users),
                        "last_executed": row.last_executed.isoformat() if row.last_executed else None,
                        "days_since_last_executed": int(row.days_since_last_executed)
                        if row.days_since_last_executed is not None
                        else None,
                        "is_active": row.is_active,
                        "is_multi_user": bool(row.is_multi_user),
                        "state_count": int(row.state_count),
                        "tool_count": int(row.tool_count),
                        "custom_node_count": int(row.custom_node_count),
                        "assistant_count": int(row.assistant_count),
                        "creator_id": row.creator_id,
                        "creator_name": row.creator_name,
                        "created_date": row.created_date.isoformat() if row.created_date else None,
                    }
                )

        # Build response with column definitions
        columns = self._get_workflow_reusability_detail_columns()

        response = {
            "data": {
                "columns": columns,
                "rows": rows,
                "totals": None,  # No totals row for asset drill-down
            },
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data_as_of": datetime.now(timezone.utc).isoformat(),
                "filters_applied": {
                    "project": project,
                    "status": status,
                    "reuse": reuse,
                    "sort_by": sort_by,
                    "sort_order": sort_order,
                },
                "execution_time_ms": 0,
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_count": total_count,
                "has_more": (page + 1) * per_page < total_count,
            },
        }

        return response

    def _get_workflow_reusability_detail_columns(self) -> list[dict]:
        """Column definitions for workflow-level drill-down table."""
        return [
            {
                "id": "workflow_name",
                "label": "Workflow Name",
                "type": "string",
                "format": None,
                "description": "Workflow display name",
            },
            {
                "id": "is_active",
                "label": "Status",
                "type": "string",
                "format": "badge",
                "description": "Active (≥5 executions) or Inactive",
            },
            {
                "id": "is_multi_user",
                "label": "Reuse",
                "type": "boolean",
                "format": "badge",
                "description": "Multi-User (≥2 users) or Single-User",
            },
            {
                "id": "execution_count",
                "label": "Executions",
                "type": "integer",
                "format": "number",
                "description": "Total execution count",
            },
            {
                "id": "unique_users",
                "label": "Unique Users",
                "type": "integer",
                "format": "number",
                "description": "Number of unique executors",
            },
            {
                "id": "days_since_last_executed",
                "label": "Days Idle",
                "type": "integer",
                "format": "number",
                "description": "Days since last execution",
            },
            {
                "id": "last_executed",
                "label": "Last Executed",
                "type": "date",
                "format": "relative",
                "description": "Last execution timestamp",
            },
            {
                "id": "state_count",
                "label": "# States",
                "type": "integer",
                "format": "number",
                "description": "Number of workflow states",
            },
            {
                "id": "tool_count",
                "label": "# Tools",
                "type": "integer",
                "format": "number",
                "description": "Number of tools",
            },
            {
                "id": "custom_node_count",
                "label": "# Custom Nodes",
                "type": "integer",
                "format": "number",
                "description": "Number of custom nodes",
            },
            {
                "id": "assistant_count",
                "label": "# Assistants",
                "type": "integer",
                "format": "number",
                "description": "Number of assistants used",
            },
            {
                "id": "creator_name",
                "label": COLUMN_LABEL_CREATED_BY,
                "type": "string",
                "format": None,
                "description": COLUMN_DESC_CREATOR_USERNAME,
            },
            {
                "id": "created_date",
                "label": "Created",
                "type": "date",
                "format": "date",
                "description": COLUMN_DESC_CREATION_TIMESTAMP,
            },
        ]

    async def get_datasource_reusability_detail(
        self,
        project: str,
        page: int = 0,
        per_page: int = 20,
        status: str | None = None,
        shared: str | None = None,
        type: str | None = None,
        sort_by: str = 'assistant_count',
        sort_order: str = 'desc',
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get datasource-level drill-down for Asset Reusability dimension.

        Returns TabularResponse with datasource-level data for a single project.
        """
        from codemie.service.analytics.queries.ai_adoption_framework import query_builder
        from datetime import datetime, timezone

        # Use default config if none provided
        config = config or AIAdoptionConfig()

        logger.info(f"Fetching datasource reusability detail for project={project}")

        # Build main query
        query, params = query_builder.build_datasource_reusability_detail_query(
            config=config,
            project=project,
            page=page,
            per_page=per_page,
            status_filter=status,
            shared_filter=shared,
            type_filter=type,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        # Build count query
        count_query, count_params = query_builder.build_datasource_reusability_detail_count_query(
            config=config,
            project=project,
            status_filter=status,
            shared_filter=shared,
            type_filter=type,
        )

        # Execute queries
        rows = []
        total_count = 0

        async with AsyncSession(PostgresClient.get_async_engine()) as session:
            # Get total count first
            count_result = await session.execute(count_query, count_params)
            total_count = count_result.scalar() or 0

            # Execute main query
            result = await session.execute(query, params)
            for row in result:
                rows.append(
                    {
                        "datasource_id": row.datasource_id,
                        "datasource_name": row.datasource_name,
                        "project": row.project,
                        "description": row.description,
                        "datasource_type": row.datasource_type,
                        "assistant_count": int(row.assistant_count),
                        "max_usage": int(row.max_usage),
                        "last_indexed": row.last_indexed.isoformat() if row.last_indexed else None,
                        "days_since_last_indexed": int(row.days_since_last_indexed)
                        if row.days_since_last_indexed is not None
                        else None,
                        "is_active": row.is_active,
                        "is_shared": bool(row.is_shared),
                        "creator_id": row.creator_id,
                        "creator_name": row.creator_name,
                        "created_date": row.created_date.isoformat() if row.created_date else None,
                    }
                )

        # Build response with column definitions
        columns = self._get_datasource_reusability_detail_columns()

        response = {
            "data": {
                "columns": columns,
                "rows": rows,
                "totals": None,  # No totals row for asset drill-down
            },
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data_as_of": datetime.now(timezone.utc).isoformat(),
                "filters_applied": {
                    "project": project,
                    "status": status,
                    "shared": shared,
                    "type": type,
                    "sort_by": sort_by,
                    "sort_order": sort_order,
                },
                "execution_time_ms": 0,
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_count": total_count,
                "has_more": (page + 1) * per_page < total_count,
            },
        }

        return response

    def _get_datasource_reusability_detail_columns(self) -> list[dict]:
        """Column definitions for datasource-level drill-down table."""
        return [
            {
                "id": "datasource_name",
                "label": "Datasource Name",
                "type": "string",
                "format": None,
                "description": "Datasource display name",
            },
            {
                "id": "datasource_type",
                "label": "Type",
                "type": "string",
                "format": "badge",
                "description": "Datasource type (git, confluence, jira, etc)",
            },
            {
                "id": "is_active",
                "label": "Status",
                "type": "string",
                "format": "badge",
                "description": "Active (used by active assistant) or Inactive",
            },
            {
                "id": "is_shared",
                "label": "Sharing",
                "type": "boolean",
                "format": "badge",
                "description": "Shared (≥2 assistants) or Single-Assistant",
            },
            {
                "id": "assistant_count",
                "label": "# Assistants",
                "type": "integer",
                "format": "number",
                "description": "Number of assistants using this datasource",
            },
            {
                "id": "max_usage",
                "label": "Max Usage",
                "type": "integer",
                "format": "number",
                "description": "Highest usage among assistants",
            },
            {
                "id": "days_since_last_indexed",
                "label": "Days Since Index",
                "type": "integer",
                "format": "number",
                "description": "Days since last indexing",
            },
            {
                "id": "last_indexed",
                "label": "Last Indexed",
                "type": "date",
                "format": "relative",
                "description": "Last indexing timestamp",
            },
            {
                "id": "creator_name",
                "label": COLUMN_LABEL_CREATED_BY,
                "type": "string",
                "format": None,
                "description": COLUMN_DESC_CREATOR_USERNAME,
            },
            {
                "id": "created_date",
                "label": "Created",
                "type": "date",
                "format": "date",
                "description": COLUMN_DESC_CREATION_TIMESTAMP,
            },
        ]

    async def get_ai_adoption_asset_reusability(
        self,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI Adoption Asset Reusability project-level metrics.

        Returns TabularResponse with Asset Reusability columns only:
        - project
        - total_assistants, total_workflows, total_datasources
        - assistants_reuse_rate, assistant_utilization_rate
        - workflow_reuse_rate, workflow_utilization_rate
        - datasource_reuse_rate, datasource_utilization_rate

        Args:
            projects: Filter by specific projects (admin only for cross-project)
            page: Page number (0-indexed)
            per_page: Results per page
            config: Optional custom configuration

        Returns:
            TabularResponse dict with Asset Reusability metrics
        """
        from codemie.service.analytics.queries.ai_adoption_framework.column_definitions import (
            get_asset_reusability_detail_columns,
        )

        def map_asset_reusability_row(row) -> dict:
            """Map database row to Asset Reusability result dict."""
            return {
                "project": row.project,
                "asset_reusability_score": float(row.asset_reusability_score) if row.asset_reusability_score else 0.0,
                "total_assistants": row.total_assistants,
                "total_workflows": row.total_workflows,
                "total_datasources": row.total_datasources,
                "assistants_reuse_rate": float(row.assistants_reuse_rate) if row.assistants_reuse_rate else 0.0,
                "assistant_utilization_rate": float(row.assistant_utilization_rate)
                if row.assistant_utilization_rate
                else 0.0,
                "workflow_reuse_rate": float(row.workflow_reuse_rate) if row.workflow_reuse_rate else 0.0,
                "workflow_utilization_rate": float(row.workflow_utilization_rate)
                if row.workflow_utilization_rate
                else 0.0,
                "datasource_reuse_rate": float(row.datasource_reuse_rate) if row.datasource_reuse_rate else 0.0,
                "datasource_utilization_rate": float(row.datasource_utilization_rate)
                if row.datasource_utilization_rate
                else 0.0,
            }

        return await self._get_dimension_metrics_generic(
            projects=projects,
            page=page,
            per_page=per_page,
            query_builder_method="build_asset_reusability_metrics_query",
            columns_getter=get_asset_reusability_detail_columns,
            row_mapper=map_asset_reusability_row,
            config=config,
        )

    async def get_ai_adoption_expertise_distribution(
        self,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI Adoption Expertise Distribution project-level metrics.

        Returns TabularResponse with Expertise Distribution columns only:
        - project
        - total_users
        - creator_diversity
        - champion_health

        Args:
            projects: Filter by specific projects (admin only for cross-project)
            page: Page number (0-indexed)
            per_page: Results per page
            config: Optional custom configuration

        Returns:
            TabularResponse dict with Expertise Distribution metrics
        """
        from codemie.service.analytics.queries.ai_adoption_framework.column_definitions import (
            get_expertise_distribution_detail_columns,
        )

        def map_expertise_distribution_row(row) -> dict:
            """Map database row to Expertise Distribution result dict."""
            return {
                "project": row.project,
                "expertise_distribution_score": float(row.expertise_distribution_score)
                if row.expertise_distribution_score
                else 0.0,
                "total_users": row.total_users,
                "creator_diversity": float(row.creator_diversity) if row.creator_diversity else 0.0,
                "champion_health": row.champion_health,
            }

        return await self._get_dimension_metrics_generic(
            projects=projects,
            page=page,
            per_page=per_page,
            query_builder_method="build_expertise_distribution_metrics_query",
            columns_getter=get_expertise_distribution_detail_columns,
            row_mapper=map_expertise_distribution_row,
            config=config,
        )

    async def get_ai_adoption_feature_adoption(
        self,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI Adoption Feature Adoption project-level metrics.

        Returns TabularResponse with Feature Adoption columns only:
        - project
        - feature_adoption_score
        - feature_utilization_rate
        - total_assistants, total_workflows
        - median_conversation_depth
        - assistant_complexity_score
        - workflow_complexity_score

        Args:
            projects: Filter by specific projects (admin only for cross-project)
            page: Page number (0-indexed)
            per_page: Results per page
            config: Optional custom configuration

        Returns:
            TabularResponse dict with Feature Adoption metrics
        """
        from codemie.service.analytics.queries.ai_adoption_framework.column_definitions import (
            get_feature_adoption_detail_columns,
        )

        def map_feature_adoption_row(row) -> dict:
            """Map database row to Feature Adoption result dict."""
            return {
                "project": row.project,
                "feature_adoption_score": float(row.feature_adoption_score) if row.feature_adoption_score else 0.0,
                "feature_utilization_rate": float(row.feature_utilization_rate)
                if row.feature_utilization_rate
                else 0.0,
                "total_assistants": row.total_assistants,
                "total_workflows": row.total_workflows,
                "median_conversation_depth": float(row.median_conversation_depth)
                if row.median_conversation_depth
                else 0.0,
                "assistant_complexity_score": float(row.assistant_complexity_score)
                if row.assistant_complexity_score
                else 0.0,
                "workflow_complexity_score": float(row.workflow_complexity_score)
                if row.workflow_complexity_score
                else 0.0,
            }

        return await self._get_dimension_metrics_generic(
            projects=projects,
            page=page,
            per_page=per_page,
            query_builder_method="build_feature_adoption_metrics_query",
            columns_getter=get_feature_adoption_detail_columns,
            row_mapper=map_feature_adoption_row,
            config=config,
        )

    async def _get_dimension_metrics_generic(
        self,
        projects: list[str] | None,
        page: int,
        per_page: int,
        query_builder_method: str,
        columns_getter: callable,
        row_mapper: callable,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Generic method for fetching dimension metrics with pagination.

        This method eliminates code duplication across all 4 dimension endpoints
        by providing a common pattern for querying and building responses.

        Args:
            projects: Filter by specific projects
            page: Page number (0-indexed)
            per_page: Results per page
            query_builder_method: Name of the query builder method to call
            columns_getter: Function to get column definitions
            row_mapper: Function to map database row to result dict
            config: Optional custom configuration

        Returns:
            TabularResponse dict with dimension metrics
        """
        logger.info(f"Requesting dimension metrics using {query_builder_method}. Config={config}")

        # Access control
        target_projects = self._get_accessible_projects(projects)

        # Build dimension-specific query
        from codemie.service.analytics.queries.ai_adoption_framework import query_builder, AIAdoptionConfig

        # Use provided config or create default
        config = config or AIAdoptionConfig()

        # Get the query function dynamically
        query_func = getattr(query_builder, query_builder_method)
        query, params = query_func(
            config,
            projects=target_projects,
            page=page,
            per_page=per_page,
        )

        # Execute query and get total count
        rows = []
        total_count = 0
        async with AsyncSession(PostgresClient.get_async_engine()) as session:
            # First, get total count (without pagination) - respects minimum_users_threshold
            count_query, count_params = query_builder.build_project_count_query(config, target_projects)
            count_result = await session.execute(count_query, count_params)
            total_count = count_result.scalar() or 0

            # Then execute main query with pagination
            result = await session.execute(query, params)
            for row in result:
                rows.append(row_mapper(row))

        # Build TabularResponse
        columns = columns_getter()

        response = {
            "data": {
                "columns": columns,
                "rows": rows,
            },
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data_as_of": datetime.now(timezone.utc).isoformat(),
                "filters_applied": {"projects": target_projects},
                "execution_time_ms": 0,
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_count": total_count,
                "has_more": (page + 1) * per_page < total_count,
            },
        }

        return response

    def _get_accessible_projects(self, projects: list[str] | None) -> list[str] | None:
        """Apply access control based on user role.

        Args:
            projects: Requested project filter

        Returns:
            List of accessible projects or None (all accessible)
        """
        if self._user.is_admin:
            return projects  # Admin can see all or specified projects

        # Non-admin: can only see their accessible projects
        user_projects = self._user.project_names or []
        if projects:
            # Filter to intersection of requested and accessible
            return [p for p in projects if p in user_projects]
        return user_projects

    async def get_ai_adoption_config(self) -> dict:
        """Get AI Adoption Framework configuration parameters.

        Returns all weights, thresholds, and parameters used in adoption scoring.
        No access control required - config is public information.

        Returns:
            Dict with framework configuration organized by dimension
        """
        logger.info("Requesting AI Adoption Framework configuration")

        from codemie.service.analytics.queries.ai_adoption_framework import AIAdoptionConfig

        config = AIAdoptionConfig()

        response = {
            "data": config.to_dict(),
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": "1.0",
                "description": "AI Adoption Framework calculation parameters (weights, thresholds, scoring rules)",
            },
        }

        return response
