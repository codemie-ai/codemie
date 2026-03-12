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

"""Access control filtering for analytics queries.

This module determines which projects a user can access based on their roles,
ensuring data isolation and security in analytics queries.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

from cachetools import TTLCache

from codemie.rest_api.security.user import User

logger = logging.getLogger(__name__)


@dataclass
class ProjectAccessContext:
    """Encapsulates user's role-based project access for analytics queries.

    This dataclass replaces the flat project list model with structured
    access information that distinguishes between plain user and admin roles.

    Attributes:
        user_id: User's ID for user_id filtering in plain user projects.
                 MUST NOT be None or empty (validate in AccessFilter).
        plain_user_projects: Projects where user is a plain user (sees only own data).
                            Query filter: user_id.keyword = user_id AND project IN list
        admin_projects: Projects where user is an admin (sees all users' data).
                       Query filter: project IN list (no user_id restriction)
        is_super_admin: If True, user has unrestricted access to ALL data (no project filtering).
                       When True, plain_user_projects and admin_projects are ignored.

    Note:
        Projects may appear in BOTH lists if user has overlapping roles (Union strategy).
        This is intentional and ensures no privilege loss.

    Example:
        >>> ctx = ProjectAccessContext(
        ...     user_id="user-123",
        ...     plain_user_projects=["proj-a", "proj-b"],
        ...     admin_projects=["proj-c"],
        ...     is_super_admin=False
        ... )
        >>> # User sees own data in proj-a/proj-b, all data in proj-c

        >>> super_ctx = ProjectAccessContext(
        ...     user_id="admin-456",
        ...     plain_user_projects=[],
        ...     admin_projects=[],
        ...     is_super_admin=True
        ... )
        >>> # Super admin sees ALL data across ALL projects (no filtering)
    """

    user_id: str
    plain_user_projects: list[str]
    admin_projects: list[str]
    is_super_admin: bool = False

    def __post_init__(self) -> None:
        """Validate dataclass fields after initialization."""
        if not self.user_id:
            raise ValueError("user_id cannot be empty")


class AccessFilter:
    """Determines which projects a user can access for analytics queries."""

    # Class-level cache: 5min TTL, max 1000 users
    _context_cache: TTLCache = TTLCache(maxsize=1000, ttl=300)

    def __init__(self, user: User):
        """Initialize access filter with user context.

        Args:
            user: Authenticated user object with role and project information
        """
        self._user = user

    def get_project_access_context(self) -> ProjectAccessContext:
        """Get role-based project access (cached for 5 minutes).

        This method uses an in-memory TTL cache to avoid repeated lookups.
        Cache key is user_id, with automatic expiration after 5 minutes.

        Super admins (user.is_admin=True) receive unrestricted access to all data,
        regardless of application lists. This ensures admins can access analytics
        across all projects without explicit project assignments.

        Returns:
            ProjectAccessContext with role-based project segmentation (cached)

        Raises:
            ValueError: If user ID is missing or empty
        """
        user_id = self._user.id

        # Check cache
        if user_id in self._context_cache:
            logger.debug(f"Cache HIT: user_id={user_id}")
            return self._context_cache[user_id]

        logger.debug(f"Cache MISS: user_id={user_id}")

        # Validate user ID (required for filtering)
        if not user_id:
            logger.error("User missing ID for analytics access control")
            raise ValueError("User ID is required for analytics access control")

        # Check if user is super admin (unrestricted access)
        if self._user.is_admin:
            logger.info(f"Super admin detected: user_id={user_id}, granting unrestricted access to all analytics data")
            context = ProjectAccessContext(
                user_id=user_id, plain_user_projects=[], admin_projects=[], is_super_admin=True
            )
            # Store in cache
            self._context_cache[user_id] = context
            logger.debug(f"Cache STORED (super admin): user_id={user_id}, cache_size={len(self._context_cache)}")
            return context

        # Extract project lists for regular users (handle None gracefully)
        plain_user_projects = list(self._user.project_names or [])
        admin_projects = list(self._user.admin_project_names or [])

        logger.debug(
            f"Access context computed: user_id={user_id}, "
            f"plain_projects_count={len(plain_user_projects)}, "
            f"admin_projects_count={len(admin_projects)}, "
            f"plain_projects={plain_user_projects}, "
            f"admin_projects={admin_projects}"
        )

        # Create context for regular users
        context = ProjectAccessContext(
            user_id=user_id,
            plain_user_projects=plain_user_projects,
            admin_projects=admin_projects,
            is_super_admin=False,
        )

        # Store in cache
        self._context_cache[user_id] = context
        logger.debug(f"Cache STORED: user_id={user_id}, cache_size={len(self._context_cache)}")

        return context

    def get_accessible_projects(self) -> list[str]:
        """Get flat list of accessible projects (DEPRECATED).

        DEPRECATED: Use get_project_access_context() for role-aware filtering.
        This method returns a merged list without role information and will be
        removed in a future version.

        Access rules:
        - Plain user: projects in user.project_names
        - Project admin: projects in user.project_names + user.admin_project_names (unique)

        Returns:
            List of project names/IDs user can query
        """
        logger.warning(
            f"get_accessible_projects() is deprecated for user {self._user.id}. "
            "Use get_project_access_context() for role-based filtering."
        )

        accessible = set(self._user.project_names or [])

        # Project admins see their admin projects too
        if self._user.admin_project_names:
            accessible.update(self._user.admin_project_names)

        logger.debug(
            f"Access filter computed: user_id={self._user.id}, "
            f"user_apps_count={len(self._user.project_names or [])}, "
            f"admin_apps_count={len(self._user.admin_project_names or [])}, "
            f"accessible_projects_count={len(accessible)}, "
            f"accessible_projects={list(accessible)}"
        )
        return list(accessible)
