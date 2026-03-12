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

"""Registration service for user registration and email verification.

Handles user registration including:
- New user registration
- Email verification
- Complete registration flows with session management
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from sqlmodel import Session

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_repository import user_repository
from codemie.repository.email_token_repository import email_token_repository
from codemie.rest_api.models.user_management import UserDB, CodeMieUserDetail, ProjectInfo
from codemie.service.user.personal_project_service import personal_project_service


class RegistrationService:
    """Service for user registration business logic."""

    # ===========================================
    # Core Registration
    # ===========================================

    @staticmethod
    def register_user(session: Session, email: str, username: str, password: str, name: Optional[str] = None) -> UserDB:
        """Register a new local user

        Args:
            session: Database session
            email: User email
            username: Username
            password: Plain text password
            name: Optional display name

        Returns:
            Created UserDB

        Raises:
            ExtendedHTTPException: 409 if email or username exists
        """
        # Import here to avoid circular import
        from codemie.service.password_service import password_service

        # Validate password length
        if len(password) < config.PASSWORD_MIN_LENGTH:
            raise ExtendedHTTPException(
                code=400, message=f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters"
            )

        # Check duplicates
        if user_repository.exists_by_email(session, email):
            raise ExtendedHTTPException(code=409, message="Email already registered")

        if user_repository.exists_by_username(session, username):
            raise ExtendedHTTPException(code=409, message="Username already taken")

        # Create user
        user = UserDB(
            id=str(uuid4()),
            email=email,
            username=username,
            name=name or username,
            password_hash=password_service.hash_password(password),
            auth_source="local",
            email_verified=not config.EMAIL_VERIFICATION_ENABLED,  # Pre-verify if disabled
            is_active=True,
            is_super_admin=False,
            project_limit=config.USER_PROJECT_LIMIT,
        )

        user = user_repository.create(session, user)
        logger.info(f"User registered: user_id={user.id}, auth_source=local")

        return user

    @staticmethod
    def verify_email(session: Session, raw_token: str) -> UserDB:
        """Verify user email with token

        Args:
            session: Database session
            raw_token: Raw token from email link

        Returns:
            Updated UserDB

        Raises:
            ExtendedHTTPException: 400 if token invalid/expired
        """
        token_record = email_token_repository.verify_token(session, raw_token, "email_verification")

        if not token_record:
            raise ExtendedHTTPException(code=400, message="Invalid or expired token")

        # Mark token as used
        email_token_repository.mark_used(session, token_record.id)

        # Update user
        user = user_repository.update(session, token_record.user_id, email_verified=True)

        if not user:
            raise ExtendedHTTPException(code=404, message="User not found")

        logger.info(f"Email verified: user_id={user.id}")
        return user

    # ===========================================
    # Router-Facing Flows
    # ===========================================

    @staticmethod
    async def register_user_with_flow(
        email: str, username: str, password: str, name: Optional[str] = None
    ) -> dict[str, Any]:
        """Register user with email verification flow or instant login

        Handles complete registration flow including email sending and token generation.
        Manages database session internally.

        Args:
            email: User email
            username: Username
            password: Plain text password
            name: Optional display name

        Returns:
            Dict with either:
            - {"type": "message", "message": str} for email verification
            - {"type": "token", "access_token": str, "user": CodeMieUserDetail} for instant login

        Raises:
            ExtendedHTTPException: Various error conditions
        """
        from codemie.clients.postgres import get_session
        from codemie.service.email_service import email_service

        with get_session() as session:
            try:
                # Register user (not committed yet)
                user = RegistrationService.register_user(
                    session, email=email, username=username, password=password, name=name
                )

                # Extract values before commit to avoid expired attribute access
                user_id = user.id
                user_email = user.email

                if config.EMAIL_VERIFICATION_ENABLED:
                    # Create verification token (not committed yet)
                    raw_token, _ = email_token_repository.create_token(
                        session, user_id, user_email, "email_verification"
                    )

                    # Send verification email BEFORE commit (fail-closed)
                    try:
                        await email_service.send_verification_email(user_email, raw_token)
                    except Exception as e:
                        logger.error(
                            f"Failed to send verification email, transaction will rollback: {e}", exc_info=True
                        )
                        raise ExtendedHTTPException(
                            code=500, message="Failed to send verification email. Please try again later."
                        )

                    # Only commit AFTER email is sent successfully
                    session.commit()

                    # Story 9: Create personal project (AFTER commit, ISOLATED transaction)
                    # FR-7.1: Called after commit to avoid FK constraint errors
                    # Uses separate session to prevent rollback affecting registration
                    await personal_project_service.ensure_personal_project_async(user_id, user_email)

                    return {
                        "type": "message",
                        "message": "Registration successful. Please check your email to verify your account.",
                    }

                # Email verification disabled - instant login
                # Set email as verified directly (avoid circular dependency)
                user_repository.update(session, user_id, email_verified=True)

                # Build response object before commit to avoid expired attribute access
                user_detail = CodeMieUserDetail(
                    id=user.id,
                    username=user.username,
                    email=user.email,
                    name=user.name,
                    picture=user.picture,
                    user_type=user.user_type,
                    is_active=user.is_active,
                    is_super_admin=user.is_super_admin,
                    auth_source=user.auth_source,
                    email_verified=True,
                    last_login_at=user.last_login_at,
                    date=user.date,
                    update_date=user.update_date,
                    project_limit=user.project_limit,
                    deleted_at=user.deleted_at,
                )

                session.commit()

                # Story 9: Create personal project (AFTER commit, ISOLATED transaction)
                # FR-7.1: Called after commit to avoid FK constraint errors
                # Uses separate session to prevent rollback affecting registration
                await personal_project_service.ensure_personal_project_async(user_id, user_email)

                # Import here to avoid circular dependency
                from codemie.rest_api.security.jwt_local import generate_access_token

                access_token = generate_access_token(user_id, user_email, "local")

                # Load user projects (including personal project just created)
                from codemie.clients.postgres import get_async_session
                from codemie.repository.user_project_repository import user_project_repository

                async with get_async_session() as async_session:
                    user_projects = await user_project_repository.aget_by_user_id(async_session, user_id)
                    user_detail.projects = [
                        ProjectInfo(name=p.project_name, is_project_admin=p.is_project_admin) for p in user_projects
                    ]

                return {
                    "type": "token",
                    "access_token": access_token,
                    "user": user_detail,
                }

            except ExtendedHTTPException:
                raise
            except Exception as e:
                logger.error(f"Registration failed, transaction will rollback: {e}", exc_info=True)
                raise ExtendedHTTPException(code=500, message="Registration failed. Please try again.")

    @staticmethod
    def verify_email_and_login(token: str) -> dict[str, Any]:
        """Verify email and generate access token

        Handles complete email verification flow.
        Manages database session internally.

        Args:
            token: Verification token from email

        Returns:
            Dict with "message" and "access_token"

        Raises:
            ExtendedHTTPException: If token invalid or expired
        """
        from codemie.clients.postgres import get_session
        from codemie.rest_api.security.jwt_local import generate_access_token

        with get_session() as session:
            user = RegistrationService.verify_email(session, token)

            # Extract values before commit to avoid expired attribute access
            user_id = user.id
            user_email = user.email

            session.commit()

            access_token = generate_access_token(user_id, user_email, "local")

            return {"message": "Email verified successfully", "access_token": access_token}


# Singleton instance
registration_service = RegistrationService()
