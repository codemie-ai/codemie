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

"""User profile service for self-service profile operations.

Handles profile management including:
- Profile updates (name, picture, email)
- Email verification on profile changes
- Profile validation
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_repository import user_repository
from codemie.repository.email_token_repository import email_token_repository
from codemie.rest_api.models.user_management import UserDB


class UserProfileService:
    """Service for user profile management business logic."""

    # ===========================================
    # Helper Methods
    # ===========================================

    @staticmethod
    def _build_profile_updates(
        session: Session,
        user_id: str,
        db_user: UserDB,
        name: Optional[str],
        picture: Optional[str],
        email: Optional[str],
    ) -> tuple[dict, bool, Optional[str]]:
        """Build profile update dict and detect email changes

        Args:
            session: Database session
            user_id: User UUID
            db_user: Current user from database
            name: New name (optional)
            picture: New picture URL (optional)
            email: New email (optional)

        Returns:
            Tuple of (updates_dict, email_changed, new_email)

        Raises:
            ExtendedHTTPException: If email already in use or no fields to update
        """
        updates = {}

        if name is not None:
            updates["name"] = name
        if picture is not None:
            updates["picture"] = picture

        # Handle email change
        email_changed = False
        new_email = None

        if email is not None and db_user.email != email:
            UserProfileService._validate_email_uniqueness(session, email, user_id)
            updates["email"] = email
            new_email = email
            email_changed = True

            # Mark as unverified if email verification is enabled
            if config.EMAIL_VERIFICATION_ENABLED:
                updates["email_verified"] = False

        if not updates:
            raise ExtendedHTTPException(code=400, message="No fields to update")

        return updates, email_changed, new_email

    @staticmethod
    def _validate_email_uniqueness(session: Session, email: str, user_id: str) -> None:
        """Validate that email is not already in use by another user

        Args:
            session: Database session
            email: Email to check
            user_id: Current user ID (to exclude from check)

        Raises:
            ExtendedHTTPException: If email is already in use
        """
        existing = user_repository.get_by_email(session, email)
        if existing and existing.id != user_id:
            raise ExtendedHTTPException(code=409, message="Email already in use")

    @staticmethod
    async def _send_verification_email_safe(email: str, token: str) -> None:
        """Send verification email with fail-safe error handling

        Args:
            email: Email address to send to
            token: Verification token

        Note:
            Uses fail-safe pattern: logs warning on failure, doesn't raise exception
        """
        from codemie.service.email_service import email_service

        try:
            await email_service.send_verification_email(email, token)
        except Exception as e:
            logger.warning(f"Failed to send verification email for profile update: {e}")
            # Don't fail the request - user can request resend later

    # ===========================================
    # Core Profile Operations
    # ===========================================

    @staticmethod
    async def update_profile(
        user_id: str, name: Optional[str] = None, picture: Optional[str] = None, email: Optional[str] = None
    ) -> UserDB:
        """Update user's own profile (local auth mode only)

        Handles complete profile update flow including email sending:
        - Validates email uniqueness (if changing)
        - Marks email as unverified if changed (when verification enabled)
        - Creates verification token and sends email (if needed)
        - Uses fail-safe pattern: commits first, then sends email

        Args:
            user_id: User UUID
            name: New name (optional)
            picture: New picture URL (optional)
            email: New email (optional)

        Returns:
            Updated UserDB

        Raises:
            ExtendedHTTPException: Various error conditions
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            # Get current user
            db_user = user_repository.get_by_id(session, user_id)
            if not db_user:
                raise ExtendedHTTPException(code=404, message="User not found")

            # Build updates and detect email change
            updates, email_changed, new_email = UserProfileService._build_profile_updates(
                session, user_id, db_user, name, picture, email
            )

            # Capture old email BEFORE update for personal project reconciliation
            old_email = db_user.email

            # Update user (direct repository call to avoid circular dependency)
            updated_user = user_repository.update(session, user_id, **updates)
            if not updated_user:
                raise ExtendedHTTPException(code=404, message="User not found")

            # Create verification token if email changed and verification enabled
            verification_token = None
            if email_changed and config.EMAIL_VERIFICATION_ENABLED:
                verification_token, _ = email_token_repository.create_token(
                    session, updated_user.id, new_email, "email_verification"
                )

            # Expunge before commit to preserve loaded attributes for the caller
            # It is legitimate, because flush/refresh is called inside user_repository.update() method
            session.expunge(updated_user)

            # Commit BEFORE sending email (fail-safe pattern)
            session.commit()

            # Send verification email (fail-safe: won't fail request on error)
            if verification_token and new_email:
                await UserProfileService._send_verification_email_safe(new_email, verification_token)

            # Reconcile personal project on email change (FR-7.1)
            if email_changed and new_email:
                from codemie.service.user.personal_project_service import personal_project_service

                await personal_project_service.reconcile_personal_project_on_email_change(user_id, old_email, new_email)

            logger.info(f"Profile updated: user_id={user_id}")
            return updated_user


# Singleton instance
user_profile_service = UserProfileService()
