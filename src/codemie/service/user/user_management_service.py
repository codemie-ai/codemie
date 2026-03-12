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

"""User management service for CRUD and admin operations.

Handles user management including:
- User CRUD operations (create, read, update, deactivate)
- User listing with pagination and filters
- SuperAdmin bootstrap
- Admin-level user operations
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Optional
from uuid import uuid4

from sqlmodel import Session

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_repository import user_repository
from codemie.rest_api.models.user_management import (
    UserDB,
    CodeMieUserDetail,
    AdminUserListItem,
    PaginatedUserListResponse,
    PaginationInfo,
    ProjectInfo,
)


_USER_NOT_FOUND = "User not found"


class UserManagementService:
    """Service for user management business logic."""

    # ===========================================
    # User CRUD (Core Operations)
    # ===========================================

    @staticmethod
    def create_local_user(
        session: Session,
        email: str,
        username: str,
        password: str,
        name: Optional[str] = None,
        is_super_admin: bool = False,
    ) -> UserDB:
        """Create a local user (SuperAdmin only)

        Args:
            session: Database session
            email: User email
            username: Username
            password: Plain text password
            name: Display name
            is_super_admin: Grant SuperAdmin status

        Returns:
            Created UserDB
        """
        from codemie.service.password_service import password_service

        # Validate password
        if len(password) < config.PASSWORD_MIN_LENGTH:
            raise ExtendedHTTPException(
                code=400, message=f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters"
            )

        # Check duplicates
        if user_repository.exists_by_email(session, email):
            raise ExtendedHTTPException(code=409, message="Email already registered")

        if user_repository.exists_by_username(session, username):
            raise ExtendedHTTPException(code=409, message="Username already taken")

        user = UserDB(
            id=str(uuid4()),
            email=email,
            username=username,
            name=name or username,
            password_hash=password_service.hash_password(password),
            auth_source="local",
            email_verified=True,  # Admin-created users are pre-verified
            is_active=True,
            is_super_admin=is_super_admin,
            project_limit=None
            if is_super_admin
            else config.USER_PROJECT_LIMIT,  # Super admins always have unlimited (NULL)
        )

        user = user_repository.create(session, user)
        logger.info(f"user_created: target_user_id={user.id}, auth_source=local, is_super_admin={is_super_admin}")

        return user

    @staticmethod
    def get_user_by_id(session: Session, user_id: str) -> Optional[UserDB]:
        """Get user by ID"""
        return user_repository.get_by_id(session, user_id)

    @staticmethod
    def get_user_by_email(session: Session, email: str) -> Optional[UserDB]:
        """Get user by email"""
        return user_repository.get_by_email(session, email)

    @staticmethod
    def get_user_with_relationships(
        session: Session, user_id: str, requesting_user_id: str, is_super_admin: bool, is_project_admin: bool = False
    ) -> Optional[CodeMieUserDetail]:
        """Get user with full details for admin view

        Story 10: Filters personal projects based on visibility rules.
        Story 18: Filters projects for project admins (only show projects where admin is member).

        Args:
            session: Database session
            user_id: Target user ID
            requesting_user_id: User requesting the detail (for visibility filtering)
            is_super_admin: Whether requesting user is super admin
            is_project_admin: Whether requesting user is project admin (Story 18)

        Returns:
            CodeMieUserDetail or None
        """
        from codemie.repository.user_project_repository import user_project_repository

        user = user_repository.get_by_id(session, user_id)
        if not user:
            return None

        # Story 18: Different filtering for project admins vs super admins
        if is_super_admin:
            # Super admins see all projects (Story 10 visibility filtering)
            visible_projects = user_project_repository.get_visible_projects_for_user(
                session, user_id, requesting_user_id, is_super_admin
            )
        elif is_project_admin:
            # Project admins see only projects where they are members (Story 18)
            visible_projects = user_project_repository.get_admin_visible_projects_for_user(
                session, user_id, requesting_user_id
            )
        else:
            # Regular users should not reach here (caught at API layer)
            visible_projects = []

        projects = [ProjectInfo(name=up.project_name, is_project_admin=up.is_project_admin) for up in visible_projects]

        # Fetch user's knowledge bases (no filtering - shown in full)
        knowledge_bases = user_repository.get_user_knowledge_bases(session, user_id)

        return CodeMieUserDetail(
            id=user.id,
            username=user.username,
            email=user.email,
            name=user.name,
            picture=user.picture,
            user_type=user.user_type,
            is_active=user.is_active,
            is_super_admin=user.is_super_admin,
            auth_source=user.auth_source,
            email_verified=user.email_verified,
            last_login_at=user.last_login_at,
            projects=projects,
            project_limit=user.project_limit,
            knowledge_bases=knowledge_bases,
            date=user.date,
            update_date=user.update_date,
            deleted_at=user.deleted_at,
        )

    @staticmethod
    def update_user(session: Session, user_id: str, actor_user_id: str, **fields) -> Optional[UserDB]:
        """Update user fields (admin action)

        Args:
            session: Database session
            user_id: Target user UUID
            actor_user_id: User performing the action (for audit)
            **fields: Fields to update

        Returns:
            Updated UserDB or None
        """
        user = user_repository.update(session, user_id, **fields)

        if user:
            logger.info(f"user_updated: actor_user_id={actor_user_id}, target_user_id={user_id}")

        return user

    @staticmethod
    def deactivate_user(session: Session, user_id: str, actor_user_id: str) -> UserDB:
        """Deactivate (soft delete) user

        Args:
            session: Database session
            user_id: Target user UUID
            actor_user_id: User performing the action

        Returns:
            Deactivated UserDB

        Raises:
            ExtendedHTTPException: 404 if not found, 403 if last SuperAdmin
        """
        user = user_repository.get_by_id(session, user_id)
        if not user:
            raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)

        # Last SuperAdmin protection
        if user.is_super_admin:
            count = user_repository.count_active_superadmins(session)
            if count <= 1:
                msg = (
                    f"blocked_last_super_admin_deactivation: actor_user_id={actor_user_id}, "
                    f"target_user_id={user_id}, action=deactivate, timestamp={datetime.now(UTC)}"
                )
                logger.warning(msg)
                raise ExtendedHTTPException(
                    code=403, message="Cannot deactivate last super admin - system must have at least one super admin"
                )

        # Soft delete
        user_repository.soft_delete(session, user_id)

        logger.info(f"user_deactivated: actor_user_id={actor_user_id}, target_user_id={user_id}")

        # Refresh user
        return user_repository.get_by_id(session, user_id)

    @staticmethod
    def list_users(
        session: Session,
        requesting_user_id: str,
        is_super_admin: bool,
        page: int = 0,
        per_page: int = 20,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        project_name: Optional[str] = None,
        user_type: Optional[str] = None,
    ) -> PaginatedUserListResponse:
        """List users with pagination and filters

        Story 10: Filters personal projects based on visibility rules.

        Args:
            session: Database session
            requesting_user_id: User requesting the list (for visibility filtering)
            is_super_admin: Whether requesting user is super admin
            page: Page number (0-indexed)
            per_page: Items per page
            search: Search term
            is_active: Active status filter
            project_name: Project access filter
            user_type: User type filter ('regular' or 'external')

        Returns:
            PaginatedUserListResponse
        """
        from codemie.repository.user_project_repository import user_project_repository

        # Story 7: Repository now returns projects_map from JOIN query (N+1 prevention)
        users, projects_map, total = user_repository.list_users(
            session, page, per_page, search, is_active, project_name, user_type
        )

        # Story 10 Code Review R2: Filter pre-fetched projects_map instead of re-querying per user
        # This prevents per-user query amplification while applying visibility rules
        filtered_projects_map = user_project_repository.filter_visible_projects_from_map(
            session, projects_map, requesting_user_id, is_super_admin
        )

        items = [
            AdminUserListItem(
                id=u.id,
                username=u.username,
                email=u.email,
                name=u.name,
                user_type=u.user_type,
                is_active=u.is_active,
                is_super_admin=u.is_super_admin,
                auth_source=u.auth_source,
                last_login_at=u.last_login_at,
                projects=[
                    ProjectInfo(name=up.project_name, is_project_admin=up.is_project_admin)
                    for up in filtered_projects_map.get(u.id, [])
                ],
                date=u.date,
            )
            for u in users
        ]

        return PaginatedUserListResponse(
            data=items, pagination=PaginationInfo(total=total, page=page, per_page=per_page)
        )

    # ===========================================
    # Bootstrap
    # ===========================================

    @staticmethod
    def bootstrap_superadmin(session: Session, email: str, password: str) -> Optional[UserDB]:
        """Bootstrap SuperAdmin if none exists

        Args:
            session: Database session
            email: SuperAdmin email
            password: SuperAdmin password

        Returns:
            Created UserDB or None if already exists
        """
        count = user_repository.count_active_superadmins(session)
        if count > 0:
            return None

        return UserManagementService.create_local_user(
            session, email=email, username="admin", password=password, name="System Administrator", is_super_admin=True
        )

    @staticmethod
    def bootstrap_superadmin_startup(email: str, password: str) -> Optional[UserDB]:
        """Bootstrap SuperAdmin during application startup

        Manages database session internally.
        Used by application startup code, not routers.

        Args:
            email: SuperAdmin email
            password: SuperAdmin password

        Returns:
            Created UserDB or None if already exists

        Raises:
            Exception: Propagates any errors to caller (caller decides how to handle)
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            superadmin = UserManagementService.bootstrap_superadmin(session, email, password)

            if superadmin:
                superadmin_id = superadmin.id
                # Expunge before commit to preserve loaded attributes for the caller
                # It is legitimate, because flush/refresh is called inside bootstrap_superadmin...create() method
                session.expunge(superadmin)
                session.commit()
                logger.info(f"SuperAdmin bootstrapped: user_id={superadmin_id}")
                return superadmin
            else:
                logger.info("SuperAdmin already exists, skipping bootstrap")
                return None

    @staticmethod
    def count_active_superadmins(session: Session) -> int:
        """Count active SuperAdmins"""
        return user_repository.count_active_superadmins(session)

    @staticmethod
    def update_last_login(session: Session, user_id: str) -> bool:
        """Update user's last login timestamp"""
        return user_repository.update_last_login(session, user_id)

    # ===========================================
    # Router-Facing Flows (Admin User Management)
    # ===========================================

    @staticmethod
    def list_users_with_flow(
        requesting_user_id: str,
        is_super_admin: bool,
        page: int = 0,
        per_page: int = 20,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        project_name: Optional[str] = None,
        user_type: Optional[str] = None,
    ) -> PaginatedUserListResponse:
        """List users with pagination and filters

        Handles complete flow with session management.

        Story 10: Requires user context for visibility filtering.

        Args:
            requesting_user_id: User requesting the list (for visibility filtering)
            is_super_admin: Whether requesting user is super admin
            page: Page number (0-indexed)
            per_page: Items per page
            search: Search term
            is_active: Active status filter
            project_name: Project access filter
            user_type: User type filter ('regular' or 'external')

        Returns:
            PaginatedUserListResponse
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            return UserManagementService.list_users(
                session,
                requesting_user_id=requesting_user_id,
                is_super_admin=is_super_admin,
                page=page,
                per_page=per_page,
                search=search,
                is_active=is_active,
                project_name=project_name,
                user_type=user_type,
            )

    @staticmethod
    def get_user_detail(
        user_id: str, requesting_user_id: str, is_super_admin: bool, is_project_admin: bool = False
    ) -> CodeMieUserDetail:
        """Get user detail by ID

        Handles complete flow with session management.

        Story 10: Requires user context for visibility filtering.
        Story 18: Project admin can view users in projects they admin (with filtered response).

        Args:
            user_id: Target user UUID
            requesting_user_id: User requesting the detail (for visibility filtering)
            is_super_admin: Whether requesting user is super admin
            is_project_admin: Whether requesting user is project admin (Story 18)

        Returns:
            CodeMieUserDetail

        Raises:
            ExtendedHTTPException: If user not found
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            user_detail = UserManagementService.get_user_with_relationships(
                session, user_id, requesting_user_id, is_super_admin, is_project_admin
            )

            if not user_detail:
                raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)

            return user_detail

    @staticmethod
    def create_local_user_with_flow(
        email: str,
        username: str,
        password: str,
        name: Optional[str] = None,
        is_super_admin: bool = False,
        actor_user_id: str = "system",
    ) -> CodeMieUserDetail:
        """Create local user (admin action)

        Handles complete user creation flow with session management.
        Manages database session internally.

        Args:
            email: User email
            username: Username
            password: Plain text password
            name: Display name
            is_super_admin: Grant SuperAdmin status
            actor_user_id: User performing the action

        Returns:
            CodeMieUserDetail with full user information

        Raises:
            ExtendedHTTPException: Various error conditions
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            new_user = UserManagementService.create_local_user(
                session, email=email, username=username, password=password, name=name, is_super_admin=is_super_admin
            )

            # Extract values before commit to avoid expired attribute access
            new_user_id = new_user.id

            session.commit()

            logger.info(f"user_created: actor_user_id={actor_user_id}, target_user_id={new_user_id}")

            # Story 10: Get actor's super admin status for visibility filtering
            actor = user_repository.get_by_id(session, actor_user_id)
            actor_is_super_admin = actor.is_super_admin if actor else False

            return UserManagementService.get_user_with_relationships(
                session, new_user_id, actor_user_id, actor_is_super_admin
            )

    # ===========================================
    # Private Helper Methods (Complexity Reduction)
    # ===========================================

    @staticmethod
    def _validate_super_admin_revocation(
        session: Session, user: UserDB, user_id: str, actor_user_id: str, is_super_admin: Optional[bool]
    ) -> None:
        """Validate super admin revocation attempt (Story 5).

        Args:
            session: Database session (needed for count_active_superadmins)
            user: Current user object (already fetched)
            user_id: Target user UUID
            actor_user_id: User performing the action
            is_super_admin: New super admin status

        Raises:
            ExtendedHTTPException: 403 if self-revocation or last admin revocation
        """
        if is_super_admin is not None and not is_super_admin and user.is_super_admin:
            # Attempting to revoke super admin status from current super admin
            # Rule 1: Self-revocation blocked
            if actor_user_id == user_id:
                msg = (
                    f"blocked_self_revocation: actor_user_id={actor_user_id}, "
                    f"target_user_id={user_id}, action=revoke_self, timestamp={datetime.now(UTC)}"
                )
                logger.warning(msg)
                raise ExtendedHTTPException(code=403, message="Cannot revoke own super admin status")

            # Rule 2: Last admin protection
            count = user_repository.count_active_superadmins(session)
            if count <= 1:
                msg = (
                    f"blocked_last_super_admin_revocation: actor_user_id={actor_user_id}, "
                    f"target_user_id={user_id}, action=revoke_last, timestamp={datetime.now(UTC)}"
                )
                logger.warning(msg)
                raise ExtendedHTTPException(
                    code=403,
                    message="Cannot revoke last super admin - system must have at least one super admin",
                )

    @staticmethod
    def _auto_manage_project_limit(
        user: UserDB, user_id: str, is_super_admin: Optional[bool]
    ) -> tuple[bool, Optional[int]]:
        """Auto-manage project_limit on role changes (Story 6).

        Args:
            user: Current user object (already fetched)
            user_id: Target user UUID
            is_super_admin: New super admin status (None if not changing)

        Returns:
            Tuple of (auto_set_limit: bool, auto_limit_value: Optional[int])
        """
        auto_set_limit = False
        auto_limit_value = None

        if is_super_admin is not None and user.is_super_admin != is_super_admin:
            # Role is changing
            auto_set_limit = True
            if is_super_admin:
                # Promotion: set to NULL (unlimited)
                auto_limit_value = None
                logger.info(f"project_limit_auto_management: user_id={user_id}, action=promotion, limit=NULL")
            else:
                # Demotion: set to default (3)
                auto_limit_value = 3
                logger.info(f"project_limit_auto_management: user_id={user_id}, action=demotion, limit=3")

        return auto_set_limit, auto_limit_value

    @staticmethod
    def _build_updates_dict(
        name: Optional[str],
        picture: Optional[str],
        email: Optional[str],
        user_type: Optional[str],
        is_super_admin: Optional[bool],
        project_limit: Optional[int],
        auto_set_limit: bool,
        auto_limit_value: Optional[int],
        user_id: str,
        project_limit_provided: bool = False,
    ) -> dict:
        """Build update dictionary with project_limit and user_type handling (Story 6, 8, F-15).

        Args:
            name: New name
            picture: New picture URL
            email: New email
            user_type: New user type (Story 8)
            is_super_admin: New super admin status
            project_limit: Explicit project_limit value
            auto_set_limit: Whether auto-management triggered
            auto_limit_value: Auto-managed limit value
            user_id: Target user UUID (for logging)
            project_limit_provided: Whether project_limit was explicitly in request body (F-15)

        Returns:
            Dict of fields to update
        """
        updates = {}
        if name is not None:
            updates["name"] = name
        if picture is not None:
            updates["picture"] = picture
        if email is not None:
            updates["email"] = email
        if user_type is not None:
            updates["user_type"] = user_type
        if is_super_admin is not None:
            updates["is_super_admin"] = is_super_admin

        # Story 6 + F-15: Apply project_limit changes
        # INVARIANT: Super admins MUST have project_limit=NULL (unlimited)
        # Priority: auto-management for super admin promotion > explicit > auto-management for demotion
        if auto_set_limit and auto_limit_value is None:
            # Promotion to super admin: FORCE project_limit=None (invariant enforcement)
            updates["project_limit"] = None
            if project_limit is not None:
                logger.warning(f"project_limit_override_ignored: user={user_id}, value={project_limit}")
        elif project_limit is not None:
            # Explicit non-None project_limit provided (validated above)
            updates["project_limit"] = project_limit
        elif project_limit_provided:
            # F-15: Explicit null sent (validated above — only super admins allowed)
            updates["project_limit"] = None
        elif auto_set_limit:
            # Auto-managed demotion (project_limit=3)
            updates["project_limit"] = auto_limit_value

        return updates

    @staticmethod
    def _validate_project_limit(
        user: UserDB,
        user_id: str,
        actor_user_id: str,
        project_limit: Optional[int],
        project_limit_provided: bool = False,
    ) -> None:
        """Validate manual project_limit modification (Story 6, F-15).

        Args:
            user: Current user object (already fetched)
            user_id: Target user UUID
            actor_user_id: User performing the action
            project_limit: New project_limit value
            project_limit_provided: Whether project_limit was explicitly in request body

        Raises:
            ExtendedHTTPException: 403 if super admin modifying own limit
            ExtendedHTTPException: 400 if negative value or invalid null for non-super-admin
        """
        if project_limit is not None:
            # Rule 1: Super admin cannot modify own project_limit
            if actor_user_id == user_id and user.is_super_admin:
                raise ExtendedHTTPException(
                    code=403, message="Super admins cannot modify their own project limit (always unlimited)"
                )

            # Rule 2: Validate negative values
            if project_limit < 0:
                raise ExtendedHTTPException(
                    code=400, message="Invalid project_limit: must be non-negative integer or NULL"
                )
        elif project_limit_provided:
            # F-15: Explicit null sent — only super admins may have unlimited
            if not user.is_super_admin:
                raise ExtendedHTTPException(
                    code=400, message="Only super admins can have unlimited project_limit (NULL)"
                )

    @staticmethod
    def _resolve_actor_super_admin_status(session: Session, actor_user_id: str, user_type: Optional[str]) -> bool:
        """Resolve actor's super admin status for user_type validation (Story 8).

        Args:
            session: Database session
            actor_user_id: Actor user UUID
            user_type: New user_type value (triggers actor lookup if not None)

        Returns:
            True if actor is super admin, False otherwise

        Raises:
            ExtendedHTTPException: 403 if actor not found when user_type change requested
        """
        if user_type is None:
            return False
        actor_user = user_repository.get_by_id(session, actor_user_id)
        if not actor_user:
            raise ExtendedHTTPException(
                code=403,
                message="Access denied",
                details="Actor user not found in database",
            )
        return actor_user.is_super_admin

    @staticmethod
    def _validate_user_and_auto_manage(
        session: Session,
        user_id: str,
        actor_user_id: str,
        is_super_admin: Optional[bool],
        project_limit: Optional[int],
        project_limit_provided: bool,
    ) -> tuple[bool, Optional[int]]:
        """Validate user changes and auto-manage project limits (Stories 5, 6, F-15).

        Args:
            session: Database session
            user_id: Target user UUID
            actor_user_id: Actor user UUID
            is_super_admin: New super admin status
            project_limit: Explicit project_limit value
            project_limit_provided: Whether project_limit was in request body

        Returns:
            Tuple of (auto_set_limit, auto_limit_value)

        Raises:
            ExtendedHTTPException: 404 if user not found, 403 on validation failures
        """
        user = user_repository.get_by_id(session, user_id)
        if not user:
            raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)

        UserManagementService._validate_super_admin_revocation(session, user, user_id, actor_user_id, is_super_admin)

        if project_limit is not None or project_limit_provided:
            UserManagementService._validate_project_limit(
                user, user_id, actor_user_id, project_limit, project_limit_provided
            )

        auto_set_limit, auto_limit_value = (False, None)
        if is_super_admin is not None:
            auto_set_limit, auto_limit_value = UserManagementService._auto_manage_project_limit(
                user, user_id, is_super_admin
            )

        return auto_set_limit, auto_limit_value

    @staticmethod
    def _validate_conditional_field_editability(
        username: Optional[str], email: Optional[str], user_type: Optional[str], actor_is_super_admin: bool
    ) -> None:
        """Validate conditional field editability based on system auth mode and actor role (Story 8).

        Args:
            username: New username value (should always be None)
            email: New email value (conditional: local mode only)
            user_type: New user_type value (conditional: super admin in local mode only)
            actor_is_super_admin: Whether the actor performing the action is a super admin

        Raises:
            ExtendedHTTPException: 400 if field cannot be edited, 403 if insufficient permissions
        """
        # Rule 1: username is immutable - cannot be changed by anyone (Story 8)
        if username is not None:
            raise ExtendedHTTPException(
                code=400,
                message="Username cannot be changed",
                details="Username is an immutable identifier and cannot be modified",
            )

        # Rule 2: email is conditional - editable only in local auth mode (Story 8)
        if email is not None and config.IDP_PROVIDER != "local":
            raise ExtendedHTTPException(
                code=400,
                message="Email cannot be changed in IDP mode",
                details=f"Email is managed by identity provider ({config.IDP_PROVIDER}). "
                "Only local auth mode allows email changes.",
            )

        # Rule 3: user_type is conditional - editable by super admin in local mode only (Story 8)
        if user_type is not None:
            if config.IDP_PROVIDER != "local":
                raise ExtendedHTTPException(
                    code=400,
                    message="User type cannot be changed in IDP mode",
                    details=f"User type is managed by identity provider ({config.IDP_PROVIDER}). "
                    "Only local auth mode allows user type changes.",
                )
            if not actor_is_super_admin:
                raise ExtendedHTTPException(
                    code=403,
                    message="Insufficient permissions to change user type",
                    details="Only super admins can modify user_type field",
                    help="Contact a super admin to request user type changes",
                )

    @staticmethod
    def _validate_user_type(user_type: Optional[str]) -> Optional[str]:
        """Validate and normalize user_type value (Story 8).

        Args:
            user_type: User type value to validate

        Returns:
            Normalized user_type (lowercase) or None

        Raises:
            ExtendedHTTPException: 400 if invalid user_type
        """
        if user_type is None:
            return None

        # Normalize to lowercase
        normalized = user_type.lower().strip()

        # Validate allowed values
        if normalized not in ["regular", "external"]:
            raise ExtendedHTTPException(
                code=400,
                message="Invalid user_type",
                details="user_type must be either 'regular' or 'external'",
            )

        return normalized

    @staticmethod
    def _handle_deactivation_flow(session: Session, user_id: str, actor_user_id: str) -> CodeMieUserDetail:
        """Handle user deactivation as separate flow (complexity reduction).

        Args:
            session: Database session
            user_id: Target user UUID
            actor_user_id: User performing the action

        Returns:
            CodeMieUserDetail after deactivation

        Raises:
            ExtendedHTTPException: 404, 403 from deactivate_user
        """
        UserManagementService.deactivate_user(session, user_id, actor_user_id)
        session.commit()

        # Story 10: Get actor's super admin status for visibility filtering
        actor = user_repository.get_by_id(session, actor_user_id)
        actor_is_super_admin = actor.is_super_admin if actor else False

        return UserManagementService.get_user_with_relationships(session, user_id, actor_user_id, actor_is_super_admin)

    @staticmethod
    def update_user_fields(
        user_id: str,
        actor_user_id: str,
        name: Optional[str] = None,
        picture: Optional[str] = None,
        email: Optional[str] = None,
        username: Optional[str] = None,
        user_type: Optional[str] = None,
        is_super_admin: Optional[bool] = None,
        is_active: Optional[bool] = None,
        project_limit: Optional[int] = None,
        project_limit_provided: bool = False,
    ) -> CodeMieUserDetail:
        """Update user fields (admin action)

        Handles complete user update flow with session management.
        Special handling for is_active: deactivation only (one-way).

        Super Admin Protection:
        - Self-revocation blocked: super admin cannot revoke own status
        - Last admin protection: cannot revoke last super admin's status

        Conditional Field Editability (Story 8):
        - username: Immutable - cannot be changed by anyone
        - email: Editable only in local auth mode (IDP mode: error)
        - user_type: Editable only in local auth mode (IDP mode: error)

        Args:
            user_id: Target user UUID
            actor_user_id: User performing the action
            name: New name
            picture: New picture URL
            email: New email (Story 8: local mode only)
            username: Username cannot be changed (Story 8: always rejected)
            user_type: New user type (Story 8: local mode only, 'regular' or 'external')
            is_super_admin: New SuperAdmin status
            is_active: Deactivation only (False allowed, True raises error)
            project_limit: Max shared projects (Story 6). Auto-managed on role changes.
                NULL/unlimited for super admins, non-negative integers for regular users

        Returns:
            CodeMieUserDetail with updated information

        Raises:
            ExtendedHTTPException: Various error conditions (400, 403, 404)
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            # Handle deactivation as separate flow (early exit reduces complexity)
            if is_active is not None:
                if is_active:
                    raise ExtendedHTTPException(
                        code=400, message="Cannot reactivate user. Reactivation is not supported."
                    )
                return UserManagementService._handle_deactivation_flow(session, user_id, actor_user_id)

            # Story 8: Resolve actor's super admin status for user_type validation
            actor_is_super_admin = UserManagementService._resolve_actor_super_admin_status(
                session, actor_user_id, user_type
            )

            # Story 8: Validate conditional field editability (auth mode and role dependent)
            UserManagementService._validate_conditional_field_editability(
                username, email, user_type, actor_is_super_admin
            )

            # Story 8: Validate and normalize user_type
            normalized_user_type = UserManagementService._validate_user_type(user_type)

            # Run validations and auto-management (Stories 5, 6, F-15)
            auto_set_limit, auto_limit_value = (False, None)
            if is_super_admin is not None or project_limit is not None or project_limit_provided:
                auto_set_limit, auto_limit_value = UserManagementService._validate_user_and_auto_manage(
                    session, user_id, actor_user_id, is_super_admin, project_limit, project_limit_provided
                )

            # Build and apply updates
            updates = UserManagementService._build_updates_dict(
                name=name,
                picture=picture,
                email=email,
                user_type=normalized_user_type,
                is_super_admin=is_super_admin,
                project_limit=project_limit,
                auto_set_limit=auto_set_limit,
                auto_limit_value=auto_limit_value,
                user_id=user_id,
                project_limit_provided=project_limit_provided,
            )

            if not updates:
                raise ExtendedHTTPException(
                    code=400,
                    message="No fields to update",
                    details="At least one field must be provided for update operation",
                    help="Provide one or more fields: name, picture, email, user_type, is_super_admin, project_limit",
                )

            updated_user = UserManagementService.update_user(session, user_id, actor_user_id, **updates)

            if not updated_user:
                raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)

            session.commit()

            # Story 10: Get actor's super admin status for visibility filtering
            actor = user_repository.get_by_id(session, actor_user_id)
            actor_is_super_admin = actor.is_super_admin if actor else False

            return UserManagementService.get_user_with_relationships(
                session, user_id, actor_user_id, actor_is_super_admin
            )

    @staticmethod
    def deactivate_user_flow(user_id: str, actor_user_id: str) -> dict[str, str]:
        """Deactivate user (admin action)

        Handles complete user deactivation flow with session management.

        Args:
            user_id: Target user UUID
            actor_user_id: User performing the action

        Returns:
            Dict with "message"

        Raises:
            ExtendedHTTPException: Various error conditions
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            UserManagementService.deactivate_user(session, user_id, actor_user_id)
            session.commit()

            return {"message": "User deactivated successfully"}


# Singleton instance
user_management_service = UserManagementService()
