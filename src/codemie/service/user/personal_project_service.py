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

"""Personal project service for auto-creation and management.

Handles personal project creation including:
- Automatic creation on user authentication/registration
- Idempotent creation (no duplicates)
- Non-blocking error handling (authentication continues on failure)
- Transaction isolation (separate session to prevent auth rollback)
- Email-based project naming
"""

from __future__ import annotations

from codemie.configs.logger import logger
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.application_repository import application_repository


class PersonalProjectService:
    """Service for personal project management."""

    @staticmethod
    async def ensure_personal_project_async(user_id: str, user_email: str) -> bool:
        """Ensure personal project exists for user (idempotent, non-blocking, isolated transaction)

        Creates personal project automatically after authentication if missing.
        Uses SEPARATE SESSION to ensure failures do not affect parent authentication transaction.

        Follows FR-7.1 Personal Project Rules:
        - Project name = user's email address
        - project_type = 'personal'
        - created_by = user_id
        - User assigned as member with is_project_admin=false
        - Non-blocking: failures are logged but do not prevent authentication

        Args:
            user_id: User UUID
            user_email: User email (used as project name)

        Returns:
            True if personal project exists or was created successfully
            False if creation failed (errors are logged)

        Note:
            This method is idempotent - safe to call multiple times.
            Errors do NOT raise exceptions to ensure authentication flow continues.
            ISOLATED TRANSACTION: Uses separate session to prevent rollback affecting auth.
        """
        from codemie.clients.postgres import get_async_session

        try:
            async with get_async_session() as isolated_session:
                # Check if personal project already exists (both Application AND user_projects)
                if await PersonalProjectService._has_personal_project_complete(isolated_session, user_id, user_email):
                    logger.debug(f"Personal project already exists: user_id={user_id}, project_type=personal")
                    return True

                # Create personal project (transaction-safe)
                await PersonalProjectService._create_personal_project(isolated_session, user_id, user_email)

                # Commit isolated transaction
                await isolated_session.commit()

                logger.info(f"Personal project created: user_id={user_id}, project_type=personal")
                return True

        except Exception as e:
            # NON-BLOCKING: Log error but do not raise (FR-7.1)
            # Security: Do not log email (PII leakage)
            logger.error(
                f"Personal project creation failed (non-blocking): user_id={user_id}, project_type=personal, error={e}",
                exc_info=True,
            )
            return False

    @staticmethod
    async def reconcile_personal_project_on_email_change(user_id: str, old_email: str, new_email: str) -> bool:
        """Reconcile personal project when user email changes (non-blocking).

        Soft-deletes old personal project (named after old email) and ensures
        new personal project exists (named after new email).

        Non-blocking: failures are logged but do not prevent the caller from continuing.
        """
        from codemie.clients.postgres import get_async_session
        from datetime import UTC, datetime

        try:
            async with get_async_session() as session:
                old_app = await application_repository.aget_by_name(session, old_email)
                if old_app and old_app.project_type == "personal" and old_app.created_by == user_id:
                    old_app.deleted_at = datetime.now(UTC).replace(tzinfo=None)
                    session.add(old_app)
                    await user_project_repository.aremove_project(session, user_id, old_email)
                    await session.commit()
                    logger.info(f"Old personal project soft-deleted on email change: user_id={user_id}")

            # Create new personal project
            return await PersonalProjectService.ensure_personal_project_async(user_id, new_email)

        except Exception as e:
            logger.error(
                f"Personal project reconciliation failed (non-blocking): user_id={user_id}, error={e}",
                exc_info=True,
            )
            return False

    @staticmethod
    async def _has_personal_project_complete(session, user_id: str, user_email: str) -> bool:
        """Check if user has COMPLETE personal project (Application + user_projects mapping)

        Validates BOTH:
        1. Application exists with project_type='personal' AND created_by=user_id
        2. user_projects mapping exists for user + project

        This prevents the failure mode where Application exists but user lacks membership access.

        Args:
            session: Async database session
            user_id: User UUID
            user_email: User email (project name)

        Returns:
            True if BOTH Application and user_projects mapping exist, False otherwise
        """
        # Check 1: Application exists with correct type and owner
        applications = await application_repository.aget_by_name(session, user_email)
        if not applications:
            return False

        application = applications
        if application.project_type != "personal" or application.created_by != user_id:
            return False

        # Check 2: user_projects mapping exists
        user_project = await user_project_repository.aget_by_user_and_project(session, user_id, user_email)
        return user_project is not None

    @staticmethod
    async def _create_personal_project(session, user_id: str, user_email: str) -> None:
        """Create personal project and user-project mapping

        FR-7.1 Personal Project Specifications:
        - name: user's email address
        - project_type: 'personal'
        - created_by: user_id
        - User assigned as member with is_project_admin=FALSE (Phase 2 spec)

        Args:
            session: Async database session
            user_id: User UUID
            user_email: User email (project name)

        Raises:
            Exception: Database errors propagate to caller
        """
        # Check if application already exists
        existing_app = await application_repository.aget_by_name(session, user_email)

        if existing_app:
            # Validate safe conversion: Only convert if NO other users have access
            # Check ALL users who have access to this project (not just current user)
            all_project_users = await user_project_repository.aget_by_project_name(session, user_email)

            # Filter out current user to check if OTHER users exist
            other_users_count = len([u for u in all_project_users if u.user_id != user_id])

            if other_users_count > 0:
                # UNSAFE: Other users have access - do not convert to personal
                total_count = len(all_project_users)
                logger.warning(
                    f"Cannot convert shared project to personal (other users exist): "
                    f"user_id={user_id}, project_type={existing_app.project_type}, "
                    f"total_users={total_count}, other_users={other_users_count}"
                )
                raise ValueError(
                    f"Cannot convert shared project to personal (has {total_count} users, {other_users_count} others)"
                )

            # Safe to convert: Update to personal project
            if existing_app.project_type != "personal" or existing_app.created_by != user_id:
                existing_app.project_type = "personal"
                existing_app.created_by = user_id
                existing_app.description = f"Personal Project for {user_email}"
                session.add(existing_app)
                await session.flush()
                logger.debug(f"Converted application to personal: user_id={user_id}, project_type=personal")
        else:
            # Create new application (get_or_create handles race conditions)
            application = await application_repository.aget_or_create(session, user_email)
            application.project_type = "personal"
            application.created_by = user_id
            application.description = f"Personal Project for {user_email}"
            session.add(application)
            await session.flush()
            logger.debug(f"Created personal application: user_id={user_id}, project_type=personal")

        # Ensure user-project mapping exists (idempotent)
        existing_mapping = await user_project_repository.aget_by_user_and_project(session, user_id, user_email)

        if not existing_mapping:
            # Create user-project mapping with is_project_admin=FALSE (Phase 2 spec)
            # Story 9: Personal projects are non-collaborative, no admin permissions needed
            await user_project_repository.aadd_project(session, user_id, user_email, is_project_admin=False)
            logger.debug(f"User-project mapping created: user_id={user_id}, is_project_admin=False")

        # Flush changes (commit handled by caller)
        await session.flush()


# Singleton instance
personal_project_service = PersonalProjectService()
