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

"""Authentication service for user authentication flows.

Handles user authentication including:
- Local authentication (email/password)
- IDP authentication (SSO providers)
- Dev header authentication (local development)
- User loading for authentication
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from cachetools import TTLCache

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_repository import user_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_kb_repository import user_kb_repository
from codemie.repository.application_repository import application_repository
from codemie.rest_api.models.user_management import UserDB, CodeMieUserDetail, ProjectInfo
from codemie.rest_api.security import user as security_user
from codemie.service.project.personal_project_service import personal_project_service

_INVALID_EMAIL_OR_PASSWORD = "Invalid email or password"
_ACCOUNT_DEACTIVATED = "Account is deactivated"

# TTL cache for authenticated users: auth_token → User.
# Skips all DB operations for repeated requests with the same token within TTL.
# Complements the request coalescing in PersistentUserProvider which handles
# concurrent (simultaneous) requests; this cache handles sequential ones.
# Uses time.monotonic() by default (immune to clock drift). Expired items are
# evicted automatically; LRU eviction kicks in when maxsize is reached.
_auth_token_cache: TTLCache[str, security_user.User] = TTLCache(
    maxsize=config.AUTH_TOKEN_CACHE_MAX_SIZE, ttl=config.AUTH_TOKEN_CACHE_TTL
)


def clear_auth_token_cache() -> None:
    """Clear the auth token cache. Used by tests to prevent state leaking between test cases."""
    _auth_token_cache.clear()


def invalidate_user_from_cache(user_id: str) -> None:
    """Invalidate all auth token cache entries for a specific user.

    Called after admin user-profile updates to ensure subsequent
    requests reflect updated data instead of stale cached values.
    """
    tokens_to_remove = [token for token, user in list(_auth_token_cache.items()) if user.id == user_id]
    for token in tokens_to_remove:
        _auth_token_cache.pop(token, None)
    if tokens_to_remove:
        logger.debug(f"auth_cache_invalidated: user_id={user_id}, tokens_removed={len(tokens_to_remove)}")


class AuthenticationService:
    """Service for user authentication business logic."""

    # ===========================================
    # Core Authentication
    # ===========================================

    @staticmethod
    async def authenticate_local(session: AsyncSession, email: str, password: str) -> UserDB:
        """Authenticate local user with email and password

        Args:
            session: Async database session
            email: User email
            password: Plain text password

        Returns:
            Authenticated UserDB

        Raises:
            ExtendedHTTPException: 401 if invalid credentials or inactive
        """
        from codemie.service.password_service import password_service

        user = await user_repository.aget_by_email(session, email)

        if not user:
            raise ExtendedHTTPException(code=401, message=_INVALID_EMAIL_OR_PASSWORD)

        if not user.password_hash:
            raise ExtendedHTTPException(code=401, message=_INVALID_EMAIL_OR_PASSWORD)

        if not password_service.verify_password(user.password_hash, password):
            logger.warning(f"Failed login attempt: user_id={user.id}")
            raise ExtendedHTTPException(code=401, message=_INVALID_EMAIL_OR_PASSWORD)

        if not user.is_active or user.deleted_at is not None:
            raise ExtendedHTTPException(code=401, message=_ACCOUNT_DEACTIVATED)

        if not user.email_verified:
            raise ExtendedHTTPException(code=401, message="Email not verified")

        # Check if password needs rehash
        if password_service.needs_rehash(user.password_hash):
            new_hash = password_service.hash_password(password)
            await user_repository.aupdate(session, user.id, password_hash=new_hash)

        # Update last login
        await user_repository.aupdate_last_login(session, user.id)

        logger.info(f"User authenticated: user_id={user.id}, auth_source=local")
        return user

    @staticmethod
    async def load_user_for_auth(session: AsyncSession, user_id: str) -> Optional[security_user.User]:
        """Load user from database and map to security.User

        Args:
            session: Async database session
            user_id: User UUID

        Returns:
            security.User Pydantic model or None
        """
        db_user = await user_repository.aget_active_by_id(session, user_id)
        if not db_user:
            return None

        # Load relationships
        projects = await user_project_repository.aget_by_user_id(session, db_user.id)
        kbs = await user_kb_repository.aget_by_user_id(session, db_user.id)

        # Map to security.User
        return security_user.User(
            id=db_user.id,
            username=db_user.username,
            name=db_user.name or "",
            email=db_user.email,
            picture=db_user.picture or "",
            user_type=db_user.user_type,
            roles=[],  # IDP roles ignored when flag ON
            project_names=[p.project_name for p in projects],
            admin_project_names=[p.project_name for p in projects if p.is_project_admin],
            knowledge_bases=[kb.kb_name for kb in kbs],
            is_admin=db_user.is_admin,
            project_limit=db_user.project_limit,
        )

    # ===========================================
    # IDP User Management
    # ===========================================

    @staticmethod
    async def create_user_from_idp(session: AsyncSession, idp_user: security_user.User) -> UserDB:
        """Create database user from IDP authentication (first login only)

        Args:
            session: Async database session
            idp_user: security.User object from IDP authentication

        Returns:
            Created UserDB
        """
        # Validate user_id is UUID format
        try:
            UUID(idp_user.id)
        except ValueError:
            raise ExtendedHTTPException(code=422, message="User ID must be a valid UUID")

        # Determine if IDP user should be SuperAdmin (legacy logic)
        legacy_is_admin = idp_user.id == config.ADMIN_USER_ID or config.ADMIN_ROLE_NAME in idp_user.roles

        # Create user record
        db_user = UserDB(
            id=idp_user.id,
            email=getattr(idp_user, 'email', '') or idp_user.username,
            username=idp_user.username,
            name=idp_user.name,
            picture=idp_user.picture,
            user_type=idp_user.user_type,
            auth_source=config.IDP_PROVIDER,
            email_verified=True,  # IDP users pre-verified
            is_active=True,
            is_admin=legacy_is_admin,
            project_limit=config.USER_PROJECT_LIMIT,
        )
        db_user = await user_repository.acreate(session, db_user)

        # Bootstrap authorization from idp_user fields (ONE-TIME ONLY)
        for project_name in idp_user.project_names:
            is_project_admin = project_name in idp_user.admin_project_names
            await user_project_repository.aadd_project(session, db_user.id, project_name, is_project_admin)

        # Bootstrap knowledge base access
        for kb_name in idp_user.knowledge_bases:
            await user_kb_repository.aadd_kb(session, db_user.id, kb_name)

        # Story 9: Personal project creation deferred until after commit
        # See authenticate_persistent_user for actual call (after session.commit())

        # Ensure Application records exist for user's projects (first login only)
        # This is done here to avoid per-request overhead in the auth hot-path
        await AuthenticationService._ensure_projects_exist(idp_user.project_names + [db_user.email])

        logger.info(f"user_created: target_user_id={db_user.id}, auth_source={config.IDP_PROVIDER}")
        return db_user

    @staticmethod
    async def _ensure_projects_exist(project_names: list[str]) -> None:
        """Ensure project records exist in applications table

        Called during first login to avoid per-request overhead.

        Args:
            project_names: List of project names
        """
        from codemie.clients.postgres import get_async_session

        async with get_async_session() as session:
            for project_name in project_names:
                try:
                    await application_repository.aget_or_create(session, project_name)
                except Exception as e:
                    logger.warning(f"Failed to ensure project exists: {project_name}, error: {e}", exc_info=True)

            try:
                await session.commit()
            except Exception as e:
                logger.error(f"Failed to commit project creation: {e}", exc_info=True)

    @staticmethod
    async def sync_idp_user_profile(session: AsyncSession, db_user: UserDB, idp_user: security_user.User) -> UserDB:
        """Sync profile fields from IDP (ignore authorization)

        Args:
            session: Async database session
            db_user: Database user record
            idp_user: Fresh security.User from IDP

        Returns:
            Updated UserDB
        """
        updates = {}

        # Sync profile fields only
        idp_email = getattr(idp_user, 'email', '') or idp_user.username
        if db_user.email != idp_email:
            updates["email"] = idp_email
        if db_user.name != idp_user.name:
            updates["name"] = idp_user.name
        if db_user.picture != idp_user.picture:
            updates["picture"] = idp_user.picture

        if updates:
            db_user = await user_repository.aupdate(session, db_user.id, **updates)

        return db_user

    @staticmethod
    async def ensure_project_exists(project_name: str) -> None:
        """Ensure project record exists in applications table

        Used by legacy JWT mode to create personal workspaces.

        Args:
            project_name: Project name to ensure exists
        """
        from codemie.clients.postgres import get_async_session

        try:
            async with get_async_session() as session:
                await application_repository.aget_or_create(session, project_name)
                await session.commit()
                logger.debug(f"Ensured project exists: {project_name}")
        except Exception as e:
            logger.warning(f"Failed to ensure project exists: {project_name}, error: {e}", exc_info=True)

    # ===========================================
    # Complete Authentication Flows
    # ===========================================

    @staticmethod
    def _build_security_user(db_user: UserDB, auth_token: str | None = None) -> security_user.User:
        """Build security.User Pydantic model from DB user (without relationships).

        Captures scalar attributes from the ORM instance so the result
        can safely be used after the session is closed.

        Args:
            db_user: Database user record (must be within an active session)
            auth_token: Authentication token to attach

        Returns:
            security.User with empty relationship lists
        """
        return security_user.User(
            id=db_user.id,
            username=db_user.username,
            name=db_user.name or "",
            email=db_user.email,
            picture=db_user.picture or "",
            user_type=db_user.user_type,
            roles=[],
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
            is_admin=db_user.is_admin,
            project_limit=db_user.project_limit,
            auth_token=auth_token,
        )

    @staticmethod
    async def _finalize_authentication(security_user_ins: security_user.User, auth_source: str) -> security_user.User:
        """Ensure personal project and load relationships after commit.

        Must be called AFTER the main session is committed and closed.
        Opens its own isolated sessions.

        Args:
            security_user_ins: Partially-built security.User (no relationships yet)
            auth_source: Label for logging (e.g. "persistent", "dev_header")

        Returns:
            security.User with relationships populated
        """
        from codemie.clients.postgres import get_async_session

        await personal_project_service.ensure_personal_project_async(security_user_ins.id, security_user_ins.email)

        async with get_async_session() as session:
            projects = await user_project_repository.aget_by_user_id(session, security_user_ins.id)
            kbs = await user_kb_repository.aget_by_user_id(session, security_user_ins.id)

            security_user_ins.project_names = [p.project_name for p in projects]
            security_user_ins.admin_project_names = [p.project_name for p in projects if p.is_project_admin]
            security_user_ins.knowledge_bases = [kb.kb_name for kb in kbs]

        logger.debug(f"User authenticated ({auth_source}): user_id={security_user_ins.id}")
        return security_user_ins

    @staticmethod
    def _validate_user_id_uuid(user_id: str) -> None:
        """Validate user_id is UUID format.

        Args:
            user_id: User ID to validate

        Raises:
            ExtendedHTTPException: 422 if not a valid UUID
        """
        try:
            UUID(user_id)
        except ValueError:
            raise ExtendedHTTPException(code=422, message="User ID must be a valid UUID")

    @staticmethod
    async def _create_first_login_user(session: AsyncSession, idp_user: Optional[security_user.User]) -> UserDB:
        """Create user on first IDP login.

        Args:
            session: Async database session
            idp_user: IDP user object

        Returns:
            Created UserDB

        Raises:
            ExtendedHTTPException: 401 if local provider or missing IDP user
        """
        if config.IDP_PROVIDER == "local":
            raise ExtendedHTTPException(code=401, message="Invalid credentials")

        if not idp_user:
            raise ExtendedHTTPException(code=401, message="Invalid authentication state")

        db_user = await AuthenticationService.create_user_from_idp(session, idp_user)
        logger.info(f"IDP user migrated: user_id={db_user.id}")
        return db_user

    @staticmethod
    async def _sync_existing_user(
        session: AsyncSession, db_user: UserDB, idp_user: Optional[security_user.User]
    ) -> str:
        """Sync existing user profile from IDP and update last login.

        Args:
            session: Async database session
            db_user: Existing database user
            idp_user: IDP user object (may be None)

        Returns:
            Pre-sync email address for reconciliation check
        """
        pre_sync_email = db_user.email

        if config.IDP_PROVIDER != "local" and idp_user:
            await AuthenticationService.sync_idp_user_profile(session, db_user, idp_user)

        if config.IDP_PROVIDER == "local":
            await user_repository.aupdate_last_login(session, db_user.id)

        return pre_sync_email

    @staticmethod
    def _get_cached_user(auth_token: str, user_id: str) -> security_user.User | None:
        """Return cached User for the token, or None on miss."""
        if not auth_token:
            return None
        cached_user = _auth_token_cache.get(auth_token)
        if cached_user is not None:
            logger.debug(f"Auth token cache hit: user_id={user_id}")
            return cached_user.model_copy(deep=True)
        return None

    @staticmethod
    async def _create_or_recover_user(
        session: AsyncSession,
        user_id: str,
        idp_user: Optional[security_user.User],
    ) -> tuple[UserDB, str | None]:
        """Create user on first login with race-condition recovery.

        If a concurrent request already created the user (IntegrityError),
        rolls back and loads the existing record instead.

        Args:
            session: Async database session
            user_id: User UUID
            idp_user: IDP user object

        Returns:
            Tuple of (db_user, pre_sync_email)
        """
        try:
            db_user = await AuthenticationService._create_first_login_user(session, idp_user)
            return db_user, None
        except IntegrityError:
            await session.rollback()
            db_user = await user_repository.aget_by_id(session, user_id)
            if not db_user:
                raise
            logger.info(f"User creation race condition handled: user_id={user_id}")
            pre_sync_email = await AuthenticationService._sync_existing_user(session, db_user, idp_user)
            return db_user, pre_sync_email

    @staticmethod
    async def authenticate_persistent_user(
        user_id: str, idp_user: Optional[security_user.User], auth_token: str
    ) -> security_user.User:
        """Authenticate and load user from database (persistent mode)

        Handles user creation on first login and profile sync.
        Manages database session internally.

        Args:
            user_id: User UUID from token/IDP
            idp_user: IDP user object (None for local JWT)
            auth_token: Authentication token

        Returns:
            security.User loaded from database

        Raises:
            ExtendedHTTPException: On authentication failure
        """
        cached = AuthenticationService._get_cached_user(auth_token, user_id)
        if cached:
            return cached

        from codemie.clients.postgres import get_async_session

        AuthenticationService._validate_user_id_uuid(user_id)

        pre_sync_email = None
        is_new_user = False
        async with get_async_session() as session:
            db_user = await user_repository.aget_by_id(session, user_id)

            if db_user:
                pre_sync_email = await AuthenticationService._sync_existing_user(session, db_user, idp_user)
            else:
                db_user, pre_sync_email = await AuthenticationService._create_or_recover_user(
                    session, user_id, idp_user
                )
                is_new_user = True

            if not db_user.is_active or db_user.deleted_at is not None:
                raise ExtendedHTTPException(code=401, message=_ACCOUNT_DEACTIVATED)

            security_user_ins = AuthenticationService._build_security_user(db_user, auth_token)
            await session.commit()

        if is_new_user:
            import asyncio

            from codemie.enterprise.litellm.dependencies import get_litellm_service_or_none

            litellm_svc = get_litellm_service_or_none()
            if litellm_svc:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, litellm_svc.get_or_create_customer_with_budget, security_user_ins.id
                    )
                    logger.debug(f"LiteLLM default budget assigned for new user: {security_user_ins.id}")
                except Exception as e:
                    logger.warning(
                        f"Failed to assign LiteLLM budget for new user {security_user_ins.id}, "
                        f"will retry on first LLM request: {e}"
                    )

        if pre_sync_email and security_user_ins.email != pre_sync_email:
            await personal_project_service.reconcile_personal_project_on_email_change(
                security_user_ins.id, pre_sync_email, security_user_ins.email
            )

        result = await AuthenticationService._finalize_authentication(security_user_ins, "persistent")

        if auth_token:
            _auth_token_cache[auth_token] = result.model_copy(deep=True)

        return result

    @staticmethod
    async def authenticate_dev_header(user_id: str) -> security_user.User:
        """Authenticate dev header user (ENV='local' only, flag ON)

        Creates minimal profile if user doesn't exist.
        Manages database session internally.

        Args:
            user_id: User ID from dev header

        Returns:
            security.User loaded/created from database

        Raises:
            ExtendedHTTPException: On authentication failure
        """
        from codemie.clients.postgres import get_async_session

        # Validate UUID format
        # try:
        #     UUID(user_id)
        # except ValueError:
        #     raise ExtendedHTTPException(code=422, message="User ID must be a valid UUID")

        async with get_async_session() as session:
            db_user = await user_repository.aget_by_id(session, user_id)

            if not db_user:
                # Create minimal profile for dev header user
                db_user = UserDB(
                    id=user_id,
                    email=user_id,  # Use ID as email placeholder
                    username=user_id,  # Use ID as username placeholder
                    name="Dev User",
                    auth_source="dev_header",
                    email_verified=True,
                    is_active=True,
                    is_admin=True,  # Persist superadmin
                )
                db_user = await user_repository.acreate(session, db_user)
                logger.info(f"Dev header user created: user_id={db_user.id}")

            security_user_ins = AuthenticationService._build_security_user(db_user)

            await user_repository.aupdate_last_login(session, db_user.id)
            await session.commit()

        return await AuthenticationService._finalize_authentication(security_user_ins, "dev_header")

    # ===========================================
    # Router-Facing Flows
    # ===========================================

    @staticmethod
    async def authenticate_and_login(email: str, password: str) -> dict[str, Any]:
        """Authenticate user and generate access token

        Handles complete login flow.
        Manages database session internally.

        Args:
            email: User email
            password: Plain text password

        Returns:
            Dict with "access_token" and "user" (CodeMieUserDetail)

        Raises:
            ExtendedHTTPException: If authentication fails
        """
        from codemie.clients.postgres import get_async_session
        from codemie.rest_api.security.jwt_local import generate_access_token

        async with get_async_session() as session:
            user = await AuthenticationService.authenticate_local(session, email, password)

            # Load user projects for login response (F-09: include project_limit + projects)
            user_projects = await user_project_repository.aget_by_user_id(session, user.id)
            projects_info = [
                ProjectInfo(name=p.project_name, is_project_admin=p.is_project_admin) for p in user_projects
            ]

            # Build response object before commit to avoid expired attribute access
            user_detail = CodeMieUserDetail(
                id=user.id,
                username=user.username,
                email=user.email,
                name=user.name,
                picture=user.picture,
                user_type=user.user_type,
                is_active=user.is_active,
                is_admin=user.is_admin,
                auth_source=user.auth_source,
                email_verified=user.email_verified,
                last_login_at=user.last_login_at,
                projects=projects_info,
                project_limit=user.project_limit,
                date=user.date,
                update_date=user.update_date,
                deleted_at=user.deleted_at,
            )
            user_id = user.id
            user_email = user.email

            await session.commit()

        # Story 9: Ensure personal project exists (local login flow, ISOLATED transaction)
        # FR-7.1: Personal project auto-created on authentication
        # Uses separate session to prevent rollback affecting authentication
        await personal_project_service.ensure_personal_project_async(user_id, user_email)

        access_token = generate_access_token(user_id, user_email, "local")

        logger.info(f"User logged in: user_id={user_id}")

        return {
            "access_token": access_token,
            "user": user_detail,
        }


# Singleton instance
authentication_service = AuthenticationService()
