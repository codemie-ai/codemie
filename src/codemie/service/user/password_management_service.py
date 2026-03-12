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

"""Password management service for password operations.

Handles password operations including:
- Password changes (self-service and admin)
- Password reset flows
- Password reset token creation and verification
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_repository import user_repository
from codemie.repository.email_token_repository import email_token_repository


class PasswordManagementService:
    """Service for password management business logic."""

    # ===========================================
    # Core Password Operations
    # ===========================================

    @staticmethod
    def change_password(
        session: Session, user_id: str, new_password: str, current_password: Optional[str] = None
    ) -> bool:
        """Change user password

        Args:
            session: Database session
            user_id: User UUID
            new_password: New plain text password
            current_password: Current password (required for self-change, not admin)

        Returns:
            True if changed

        Raises:
            ExtendedHTTPException: Various error conditions
        """
        from codemie.service.password_service import password_service

        user = user_repository.get_by_id(session, user_id)
        if not user:
            raise ExtendedHTTPException(code=404, message="User not found")

        # Verify current password if provided
        if current_password:
            if not user.password_hash:
                raise ExtendedHTTPException(code=400, message="User has no password set")

            if not password_service.verify_password(user.password_hash, current_password):
                raise ExtendedHTTPException(code=401, message="Current password is incorrect")

        # Validate new password
        if len(new_password) < config.PASSWORD_MIN_LENGTH:
            raise ExtendedHTTPException(
                code=400, message=f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters"
            )

        # Update password
        new_hash = password_service.hash_password(new_password)
        user_repository.update(session, user_id, password_hash=new_hash)

        logger.info(f"password_changed: target_user_id={user_id}")
        return True

    @staticmethod
    def create_reset_token(session: Session, email: str) -> str | None:
        """Create password reset token

        Args:
            session: Database session
            email: User email

        Returns:
            Raw token string if user exists and is eligible, None otherwise.

        Note:
            Router must ALWAYS return 200 with generic message (privacy-safe),
            regardless of whether token was created.
        """
        user = user_repository.get_by_email(session, email)

        if user and user.is_active and user.auth_source == "local":
            # Invalidate previous tokens
            email_token_repository.invalidate_previous_tokens(session, user.id, "password_reset")

            # Create new token
            raw_token, _ = email_token_repository.create_token(
                session, user.id, email, "password_reset", expires_in_hours=24
            )

            # Token is returned for email service to send
            logger.debug(f"Password reset token created: user_id={user.id}")

            # Return token for email sending (caller handles email)
            return raw_token

        # Return None but don't reveal user doesn't exist
        return None

    @staticmethod
    def reset_password(session: Session, raw_token: str, new_password: str) -> bool:
        """Reset password with token

        Args:
            session: Database session
            raw_token: Raw token from email link
            new_password: New plain text password

        Returns:
            True if reset successful

        Raises:
            ExtendedHTTPException: 400 if token invalid
        """
        from codemie.service.password_service import password_service

        token_record = email_token_repository.verify_token(session, raw_token, "password_reset")

        if not token_record:
            raise ExtendedHTTPException(code=400, message="Invalid or expired token")

        # Validate new password
        if len(new_password) < config.PASSWORD_MIN_LENGTH:
            raise ExtendedHTTPException(
                code=400, message=f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters"
            )

        # Mark token as used
        email_token_repository.mark_used(session, token_record.id)

        # Update password
        new_hash = password_service.hash_password(new_password)
        user_repository.update(session, token_record.user_id, password_hash=new_hash)

        logger.info(f"Password reset completed: user_id={token_record.user_id}")
        return True

    # ===========================================
    # Router-Facing Flows
    # ===========================================

    @staticmethod
    async def request_password_reset_flow(email: str) -> dict[str, str]:
        """Request password reset and send email

        Handles complete password reset request flow.
        Manages database session internally.
        Always returns success for privacy (doesn't reveal if email exists).

        Args:
            email: User email

        Returns:
            Dict with "message"
        """
        from codemie.clients.postgres import get_session
        from codemie.service.email_service import email_service

        with get_session() as session:
            raw_token = PasswordManagementService.create_reset_token(session, email)
            session.commit()

            if raw_token:
                # Send reset email (fail-safe)
                await email_service.send_password_reset_email(email, raw_token)

        # Always return success (privacy-safe)
        return {"message": "If the email exists, a password reset link has been sent"}

    @staticmethod
    def reset_password_with_token(token: str, new_password: str) -> dict[str, str]:
        """Reset password using token

        Handles complete password reset flow.
        Manages database session internally.

        Args:
            token: Reset token from email
            new_password: New plain text password

        Returns:
            Dict with "message"

        Raises:
            ExtendedHTTPException: If token invalid or expired
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            PasswordManagementService.reset_password(session, token, new_password)
            session.commit()

        return {"message": "Password reset successfully"}

    @staticmethod
    def change_password_authenticated(user_id: str, current_password: str, new_password: str) -> dict[str, str]:
        """Change password for authenticated user

        Handles complete password change flow.
        Manages database session internally.

        Args:
            user_id: User UUID
            current_password: Current password for verification
            new_password: New plain text password

        Returns:
            Dict with "message"

        Raises:
            ExtendedHTTPException: Various error conditions
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            PasswordManagementService.change_password(session, user_id, new_password, current_password=current_password)
            session.commit()

        return {"message": "Password changed successfully"}

    @staticmethod
    def admin_change_password_flow(user_id: str, new_password: str, actor_user_id: str) -> dict[str, str]:
        """Change user password (admin override)

        Handles complete password change flow with session management.
        No current password required (admin override).

        Args:
            user_id: Target user UUID
            new_password: New plain text password
            actor_user_id: User performing the action

        Returns:
            Dict with "message"

        Raises:
            ExtendedHTTPException: Various error conditions
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            PasswordManagementService.change_password(session, user_id, new_password, current_password=None)
            session.commit()

            logger.info(f"password_changed: actor_user_id={actor_user_id}, target_user_id={user_id}")

            return {"message": "Password changed successfully"}


# Singleton instance
password_management_service = PasswordManagementService()
