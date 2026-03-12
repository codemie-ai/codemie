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

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, UTC
from typing import Optional, Literal

from sqlmodel import Session, select

from codemie.rest_api.models.user_management import EmailVerificationToken


TokenType = Literal["email_verification", "password_reset"]


class EmailTokenRepository:
    """Repository for email verification and password reset tokens (sync SQLModel)"""

    def create_token(
        self, session: Session, user_id: str, email: str, token_type: TokenType, expires_in_hours: int = 24
    ) -> tuple[str, EmailVerificationToken]:
        """Create a new verification/reset token

        Args:
            session: Database session
            user_id: User UUID
            email: Email address
            token_type: 'email_verification' or 'password_reset'
            expires_in_hours: Token expiration time in hours

        Returns:
            Tuple of (raw_token, EmailVerificationToken record)

        Note:
            The raw_token is returned for sending via email.
            Only the hash is stored in the database.
        """
        # Generate secure token
        raw_token = secrets.token_urlsafe(32)  # 256-bit
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        now = datetime.now(UTC)
        token_record = EmailVerificationToken(
            user_id=user_id,
            token_hash=token_hash,
            email=email,
            token_type=token_type,
            expires_at=now + timedelta(hours=expires_in_hours),
            date=now,
            update_date=now,
        )

        session.add(token_record)
        session.flush()
        session.refresh(token_record)

        return raw_token, token_record

    def verify_token(self, session: Session, raw_token: str, token_type: TokenType) -> Optional[EmailVerificationToken]:
        """Verify a token and return the record if valid

        Args:
            session: Database session
            raw_token: The raw token string (from email link)
            token_type: Expected token type

        Returns:
            EmailVerificationToken if valid, None otherwise

        Note:
            Token is valid if:
            - Hash matches
            - Token type matches
            - Not expired
            - Not already used
        """
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        statement = select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == token_hash,
            EmailVerificationToken.token_type == token_type,
            EmailVerificationToken.expires_at > datetime.now(UTC),
            EmailVerificationToken.used_at.is_(None),
        )

        return session.exec(statement).first()

    def get_by_hash(self, session: Session, token_hash: str) -> Optional[EmailVerificationToken]:
        """Get token record by hash

        Args:
            session: Database session
            token_hash: SHA256 hash of token

        Returns:
            EmailVerificationToken or None
        """
        statement = select(EmailVerificationToken).where(EmailVerificationToken.token_hash == token_hash)
        return session.exec(statement).first()

    def mark_used(self, session: Session, token_id: str) -> bool:
        """Mark a token as used

        Args:
            session: Database session
            token_id: Token record UUID

        Returns:
            True if marked, False if not found
        """
        statement = select(EmailVerificationToken).where(EmailVerificationToken.id == token_id)
        token = session.exec(statement).first()

        if not token:
            return False

        now = datetime.now(UTC)
        token.used_at = now
        token.update_date = now
        session.add(token)
        session.flush()
        return True

    def delete_expired_tokens(self, session: Session) -> int:
        """Delete all expired tokens (cleanup)

        Args:
            session: Database session

        Returns:
            Number of tokens deleted
        """
        statement = select(EmailVerificationToken).where(EmailVerificationToken.expires_at < datetime.now(UTC))
        expired_tokens = session.exec(statement).all()

        count = len(expired_tokens)
        for token in expired_tokens:
            session.delete(token)

        session.flush()
        return count

    def delete_tokens_for_user(self, session: Session, user_id: str, token_type: Optional[TokenType] = None) -> int:
        """Delete all tokens for a user (optionally filtered by type)

        Args:
            session: Database session
            user_id: User UUID
            token_type: Optional token type filter

        Returns:
            Number of tokens deleted
        """
        statement = select(EmailVerificationToken).where(EmailVerificationToken.user_id == user_id)

        if token_type:
            statement = statement.where(EmailVerificationToken.token_type == token_type)

        tokens = session.exec(statement).all()

        count = len(tokens)
        for token in tokens:
            session.delete(token)

        session.flush()
        return count

    def get_active_token_for_user(
        self, session: Session, user_id: str, token_type: TokenType
    ) -> Optional[EmailVerificationToken]:
        """Get active (unused, not expired) token for user

        Args:
            session: Database session
            user_id: User UUID
            token_type: Token type

        Returns:
            EmailVerificationToken or None
        """
        statement = select(EmailVerificationToken).where(
            EmailVerificationToken.user_id == user_id,
            EmailVerificationToken.token_type == token_type,
            EmailVerificationToken.expires_at > datetime.now(UTC),
            EmailVerificationToken.used_at.is_(None),
        )
        return session.exec(statement).first()

    def invalidate_previous_tokens(self, session: Session, user_id: str, token_type: TokenType) -> int:
        """Invalidate (delete) all previous tokens of a type for user

        Used when generating a new token to ensure only one active token exists.

        Args:
            session: Database session
            user_id: User UUID
            token_type: Token type to invalidate

        Returns:
            Number of tokens invalidated
        """
        return self.delete_tokens_for_user(session, user_id, token_type)


# Singleton instance
email_token_repository = EmailTokenRepository()
