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

"""Secure Elasticsearch query builder with mandatory access control.

This module provides a fail-safe query builder that automatically injects
project access filters, preventing data leaks by design.

Super admins (user.is_admin=True) bypass all project filtering and receive
unrestricted access to all analytics data across all projects.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from codemie.rest_api.security.user import User
from codemie.service.analytics.access_filter import AccessFilter
from codemie.service.analytics.metric_names import MetricName

logger = logging.getLogger(__name__)

# Elasticsearch field name constants
PROJECT_FIELD = "attributes.project.keyword"
USER_ID_FIELD = "attributes.user_id.keyword"


class SecureQueryBuilder:
    """Builds Elasticsearch queries with mandatory access control filters.

    This class ensures that project access filters are ALWAYS applied by injecting
    them in the constructor. Developers cannot build a query without going through
    the secure builder.

    Exception: Super admins (user.is_admin=True) bypass project filtering entirely,
    granting unrestricted access to all analytics data across all projects.
    """

    def __init__(self, user: User):
        """Initialize query builder with automatic project filter injection.

        Super admins receive unrestricted access (no project filtering applied).

        Args:
            user: Authenticated user for access control
        """
        self._user = user
        self._accessible_projects = AccessFilter(user).get_accessible_projects()
        self._query: dict = {"bool": {"must": [], "filter": []}}
        self._inject_project_filter()  # CRITICAL: Auto-inject at construction

    def _inject_project_filter(self) -> None:
        """Inject role-based project access filter - ALWAYS called in constructor.

        Builds query with two conditions:
        1. Plain user projects: require user_id match + project match
        2. Admin projects: only project match (see all users' data)
        3. Super admins: NO filter applied (unrestricted access to all data)

        The base security filter is ALWAYS applied and cannot be bypassed, except for
        super admins who have unrestricted access by design.
        """
        from codemie.service.analytics.access_filter import AccessFilter

        # Get role-based access context
        access_ctx = AccessFilter(self._user).get_project_access_context()

        # Super admins bypass project filtering entirely (unrestricted access)
        if access_ctx.is_admin:
            logger.info(
                f"Super admin {self._user.id}: skipping project access filter "
                f"(unrestricted access to all analytics data)"
            )
            return  # No filter = access to all data

        # Log warning if no access for regular users
        if not access_ctx.plain_user_projects and not access_ctx.admin_projects:
            logger.warning(
                f"User {self._user.id} (user_id={access_ctx.user_id}) has no accessible projects for analytics"
            )

        # Build role-based filter for regular users
        filter_query = self._build_role_based_filter(
            access_ctx.plain_user_projects, access_ctx.admin_projects, access_ctx.user_id
        )

        # Inject into query (use 'must' clause for proper filtering semantics)
        self._query["bool"]["must"].append(filter_query)

        logger.debug(
            f"Injected role-based project filter: "
            f"plain_projects_count={len(access_ctx.plain_user_projects)}, "
            f"admin_projects_count={len(access_ctx.admin_projects)}, "
            f"query_structure={json.dumps(filter_query, indent=2)}"
        )

    def _build_role_based_filter(self, plain_projects: list[str], admin_projects: list[str], user_id: str) -> dict:
        """Build role-based project filter with OR logic.

        Constructs an Elasticsearch bool query with should clauses for different
        role-based access patterns:
        - Plain user access: user_id filter + project filter (AND logic)
        - Admin access: project filter only (no user_id restriction)

        Args:
            plain_projects: Projects where user sees only own data
            admin_projects: Projects where user sees all users' data
            user_id: User's ID for user_id filtering

        Returns:
            Elasticsearch bool query dict with should clauses

        Example:
            >>> filter = self._build_role_based_filter(
            ...     plain_projects=["proj-a"],
            ...     admin_projects=["proj-c"],
            ...     user_id="user@example.com"
            ... )
            >>> # Returns OR query: (user_id=X AND proj=proj-a) OR (proj=proj-c)
        """
        should_clauses = []

        # Plain user projects: user_id filter + project filter
        if plain_projects:
            should_clauses.append(
                {
                    "bool": {
                        "must": [
                            {"term": {USER_ID_FIELD: user_id}},
                            {"terms": {PROJECT_FIELD: plain_projects}},
                        ]
                    }
                }
            )
            logger.debug(
                f"Plain user filter: user_id={user_id}, "
                f"projects_count={len(plain_projects)}, "
                f"projects={plain_projects}"
            )

        # Admin projects: only project filter (no user_id restriction)
        if admin_projects:
            should_clauses.append({"terms": {PROJECT_FIELD: admin_projects}})
            logger.debug(f"Admin filter: projects_count={len(admin_projects)}, projects={admin_projects}")

        # Return OR query
        # Note: If should_clauses is empty, minimum_should_match=0 returns 0 results (safe)
        return {"bool": {"should": should_clauses, "minimum_should_match": 1 if should_clauses else 0}}

    def add_time_range(self, start: datetime, end: datetime, timestamp_field: str = "@timestamp") -> SecureQueryBuilder:
        """Add time range filter to query.

        Args:
            start: Start datetime for range filter
            end: End datetime for range filter
            timestamp_field: Elasticsearch field to filter on (default: "@timestamp")

        Returns:
            Self for method chaining
        """
        self._query["bool"]["filter"].append(
            {
                "range": {
                    timestamp_field: {
                        "gte": start.isoformat(),
                        "lte": end.isoformat(),
                        "format": "strict_date_optional_time",
                    }
                }
            }
        )
        return self

    def add_metric_filter(self, metric_names: list[str]) -> SecureQueryBuilder:
        """Add metric name filter to query.

        Args:
            metric_names: List of metric names to filter on (should be valid MetricName enum values)

        Returns:
            Self for method chaining

        Note:
            Validates metric names against MetricName enum. Invalid names are logged as warnings.
        """
        if metric_names:
            # Validate metric names against enum
            valid_metric_values = {metric.value for metric in MetricName}
            invalid_names = [name for name in metric_names if name not in valid_metric_values]
            if invalid_names:
                logger.warning(
                    f"Invalid metric names provided: {invalid_names}. Valid names: {list(valid_metric_values)}"
                )
            # Only use valid metric names
            valid_names = [name for name in metric_names if name in valid_metric_values]
            if valid_names:
                logger.debug(
                    f"Adding metric filter: valid_count={len(valid_names)}, "
                    f"invalid_count={len(invalid_names)}, "
                    f"valid_metrics={valid_names}"
                )
                # Use a single terms query for efficiency and to prevent DoS with large lists
                self._query["bool"]["filter"].append({"terms": {"metric_name.keyword": valid_names}})
        return self

    def add_user_filter(self, users: list[str]) -> SecureQueryBuilder:
        """Add user filter to query.

        Args:
            users: List of user IDs to filter on

        Returns:
            Self for method chaining
        """
        if users:
            self._query["bool"]["filter"].append({"terms": {USER_ID_FIELD: users}})
        return self

    def add_project_filter(self, projects: list[str]) -> SecureQueryBuilder:
        """Add project restriction (append-only, no filter removal).

        This method validates requested projects against user's accessible projects
        and appends a restriction filter. The base security filter is NEVER removed,
        ensuring security rules always apply.

        Super admins can filter by any project without validation (unrestricted access).

        Append-only strategy (from review):
        - Old: Remove auto-injected filter, rebuild (~20 lines, mutation bugs)
        - New: Just append restriction (ES optimizes automatically)

        Args:
            projects: List of project names to filter on (must be accessible to user,
                     unless user is super admin)

        Returns:
            Self for method chaining

        Example:
            >>> builder = SecureQueryBuilder(user)
            >>> builder.add_project_filter(["proj-a", "proj-c"]).build()
        """
        if not projects:
            return self  # No restriction needed

        from codemie.service.analytics.access_filter import AccessFilter

        # Get accessible projects
        access_ctx = AccessFilter(self._user).get_project_access_context()

        # Super admins can filter by any project (skip validation)
        if access_ctx.is_admin:
            self._query["bool"]["must"].append({"terms": {PROJECT_FIELD: projects}})
            logger.debug(f"Super admin project filter applied: project_count={len(projects)}, projects={projects}")
            return self

        # Regular users: validate against accessible projects
        all_accessible = set(access_ctx.plain_user_projects) | set(access_ctx.admin_projects)

        # Validate: only allow accessible projects
        allowed_projects = [p for p in projects if p in all_accessible]

        if allowed_projects != projects:
            logger.warning(
                f"User {self._user.id} attempted to filter by inaccessible projects. "
                f"Requested: {projects}, Allowed: {allowed_projects}"
            )

        # APPEND restriction (don't remove base filter)
        # Elasticsearch optimizes: (BaseSecurityFilter) AND (ProjectRestriction)
        if allowed_projects:
            self._query["bool"]["must"].append({"terms": {PROJECT_FIELD: allowed_projects}})

            logger.debug(
                f"Project filter restriction appended: "
                f"requested_count={len(projects)}, "
                f"allowed_count={len(allowed_projects)}, "
                f"allowed_projects={allowed_projects}"
            )
        else:
            logger.warning(
                f"User {self._user.id} requested projects but none are accessible. Query will return 0 results."
            )

        return self

    def build(self) -> dict:
        """Build final Elasticsearch query with all filters applied.

        Returns:
            Complete Elasticsearch bool query dict
        """
        logger.debug(
            f"Built Elasticsearch query: filter_count={len(self._query['bool']['filter'])}, "
            f"must_count={len(self._query['bool']['must'])}, "
            f"query_structure={json.dumps(self._query, indent=2)}"
        )
        return self._query
