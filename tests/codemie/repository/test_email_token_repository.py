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

"""Unit tests for EmailTokenRepository.

Tests verify token creation, verification, and lifecycle management for email
verification and password reset flows (EPMCDME-10160).
"""

import hashlib
import pytest
from datetime import datetime, timedelta, UTC
from unittest.mock import MagicMock
from uuid import uuid4

from sqlmodel import Session

from codemie.repository.email_token_repository import EmailTokenRepository
from codemie.rest_api.models.user_management import EmailVerificationToken


@pytest.fixture
def repository():
    """Provide EmailTokenRepository instance."""
    return EmailTokenRepository()


@pytest.fixture
def mock_session(mocker):
    """Mock database session for testing."""
    session = mocker.MagicMock(spec=Session)
    # Mock exec() to return a mock result that has first() method
    mock_result = MagicMock()
    session.exec.return_value = mock_result
    return session


@pytest.fixture
def sample_user_id():
    """Sample user ID for testing."""
    return str(uuid4())


@pytest.fixture
def sample_email():
    """Sample email for testing."""
    return "test@example.com"


class TestCreateToken:
    """Test cases for create_token method."""

    def test_create_token_returns_raw_and_record(self, repository, mock_session, sample_user_id, sample_email):
        """Test that create_token returns tuple of (raw_token, EmailVerificationToken).

        AC: create_token should return both the raw token (for email) and the token record
        """
        # Arrange - no specific setup needed

        # Act
        raw_token, token_record = repository.create_token(
            mock_session,
            user_id=sample_user_id,
            email=sample_email,
            token_type="email_verification",
            expires_in_hours=24,
        )

        # Assert
        assert isinstance(raw_token, str)
        assert len(raw_token) > 0
        assert isinstance(token_record, EmailVerificationToken)
        assert token_record.user_id == sample_user_id
        assert token_record.email == sample_email
        assert token_record.token_type == "email_verification"
        mock_session.add.assert_called_once_with(token_record)
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once_with(token_record)

    def test_create_token_stores_hash_not_raw(self, repository, mock_session, sample_user_id, sample_email):
        """Test that create_token stores SHA256 hash, not raw token.

        AC: Only hash is stored in database for security (token_hash != raw_token)
        """
        # Arrange - no specific setup needed

        # Act
        raw_token, token_record = repository.create_token(
            mock_session, user_id=sample_user_id, email=sample_email, token_type="password_reset", expires_in_hours=2
        )

        # Assert - verify hash matches but is not the raw token
        expected_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        assert token_record.token_hash == expected_hash
        assert token_record.token_hash != raw_token
        assert len(token_record.token_hash) == 64  # SHA256 hex length

    def test_create_token_sets_expiration(self, repository, mock_session, sample_user_id, sample_email):
        """Test that create_token sets correct expiration time."""
        # Arrange
        expires_in_hours = 48
        before_create = datetime.now(UTC)

        # Act
        raw_token, token_record = repository.create_token(
            mock_session,
            user_id=sample_user_id,
            email=sample_email,
            token_type="email_verification",
            expires_in_hours=expires_in_hours,
        )

        # Assert - expiration should be approximately expires_in_hours from now
        after_create = datetime.now(UTC)
        expected_expiration = before_create + timedelta(hours=expires_in_hours)
        # Allow 1 second tolerance for test execution time
        assert abs((token_record.expires_at - expected_expiration).total_seconds()) < 1
        assert token_record.expires_at > after_create

    def test_create_token_default_expiration(self, repository, mock_session, sample_user_id, sample_email):
        """Test that create_token uses default 24-hour expiration."""
        # Arrange
        before_create = datetime.now(UTC)

        # Act - omit expires_in_hours to use default
        raw_token, token_record = repository.create_token(
            mock_session, user_id=sample_user_id, email=sample_email, token_type="email_verification"
        )

        # Assert - should default to 24 hours
        expected_expiration = before_create + timedelta(hours=24)
        assert abs((token_record.expires_at - expected_expiration).total_seconds()) < 1


class TestVerifyToken:
    """Test cases for verify_token method."""

    def test_verify_token_valid(self, repository, mock_session, sample_user_id, sample_email):
        """Test that verify_token returns token record for valid token.

        AC: Token is valid if hash matches, type matches, not expired, not used
        """
        # Arrange - create a token first to get raw token and expected hash
        raw_token = "test_raw_token_urlsafe_string"
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        valid_token = EmailVerificationToken(
            id=str(uuid4()),
            user_id=sample_user_id,
            token_hash=token_hash,
            email=sample_email,
            token_type="email_verification",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            used_at=None,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        )

        # Mock session.exec to return the valid token
        mock_result = MagicMock()
        mock_result.first.return_value = valid_token
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.verify_token(mock_session, raw_token, "email_verification")

        # Assert
        assert result == valid_token
        mock_session.exec.assert_called_once()

    def test_verify_token_expired(self, repository, mock_session):
        """Test that verify_token returns None for expired token.

        AC: Token is invalid if expires_at <= now
        """
        # Arrange - token expired 1 hour ago
        raw_token = "expired_token"

        # Mock session.exec to return None (query filters out expired)
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.verify_token(mock_session, raw_token, "email_verification")

        # Assert
        assert result is None

    def test_verify_token_wrong_type(self, repository, mock_session):
        """Test that verify_token returns None for wrong token type.

        AC: Token must match expected token_type
        """
        # Arrange
        raw_token = "valid_token"

        # Mock session.exec to return None (query filters by type)
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        # Act - looking for email_verification but token is password_reset
        result = repository.verify_token(mock_session, raw_token, "email_verification")

        # Assert
        assert result is None

    def test_verify_token_already_used(self, repository, mock_session):
        """Test that verify_token returns None for already used token.

        AC: Token is invalid if used_at is not None
        """
        # Arrange
        raw_token = "used_token"

        # Mock session.exec to return None (query filters out used tokens)
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.verify_token(mock_session, raw_token, "password_reset")

        # Assert
        assert result is None

    def test_verify_token_invalid_hash(self, repository, mock_session):
        """Test that verify_token returns None when hash doesn't match any token."""
        # Arrange
        raw_token = "nonexistent_token"

        # Mock session.exec to return None
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.verify_token(mock_session, raw_token, "email_verification")

        # Assert
        assert result is None


class TestGetByHash:
    """Test cases for get_by_hash method."""

    def test_get_by_hash_found(self, repository, mock_session, sample_user_id, sample_email):
        """Test that get_by_hash returns token when hash matches."""
        # Arrange
        token_hash = "abc123def456"
        expected_token = EmailVerificationToken(
            id=str(uuid4()),
            user_id=sample_user_id,
            token_hash=token_hash,
            email=sample_email,
            token_type="email_verification",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        )

        mock_result = MagicMock()
        mock_result.first.return_value = expected_token
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_by_hash(mock_session, token_hash)

        # Assert
        assert result == expected_token

    def test_get_by_hash_not_found(self, repository, mock_session):
        """Test that get_by_hash returns None when hash doesn't match."""
        # Arrange
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_by_hash(mock_session, "nonexistent_hash")

        # Assert
        assert result is None


class TestMarkUsed:
    """Test cases for mark_used method."""

    def test_mark_used_sets_used_at(self, repository, mock_session, sample_user_id, sample_email):
        """Test that mark_used sets used_at timestamp.

        AC: mark_used should update used_at and update_date fields
        """
        # Arrange
        token_id = str(uuid4())
        token = EmailVerificationToken(
            id=token_id,
            user_id=sample_user_id,
            token_hash="hash123",
            email=sample_email,
            token_type="email_verification",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            used_at=None,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        )

        mock_result = MagicMock()
        mock_result.first.return_value = token
        mock_session.exec.return_value = mock_result

        before_mark = datetime.now(UTC)

        # Act
        result = repository.mark_used(mock_session, token_id)

        # Assert
        assert result is True
        assert token.used_at is not None
        assert token.used_at >= before_mark
        assert token.update_date >= before_mark
        mock_session.add.assert_called_once_with(token)
        mock_session.flush.assert_called_once()

    def test_mark_used_token_not_found(self, repository, mock_session):
        """Test that mark_used returns False when token not found."""
        # Arrange
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.mark_used(mock_session, str(uuid4()))

        # Assert
        assert result is False
        mock_session.add.assert_not_called()


class TestDeleteExpiredTokens:
    """Test cases for delete_expired_tokens method."""

    def test_delete_expired_tokens_removes_expired(self, repository, mock_session, sample_user_id, sample_email):
        """Test that delete_expired_tokens removes all expired tokens.

        AC: Should delete tokens where expires_at < now
        """
        # Arrange - create 3 expired tokens
        expired_tokens = [
            EmailVerificationToken(
                id=str(uuid4()),
                user_id=sample_user_id,
                token_hash=f"hash{i}",
                email=sample_email,
                token_type="email_verification",
                expires_at=datetime.now(UTC) - timedelta(hours=1),
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            )
            for i in range(3)
        ]

        mock_result = MagicMock()
        mock_result.all.return_value = expired_tokens
        mock_session.exec.return_value = mock_result

        # Act
        count = repository.delete_expired_tokens(mock_session)

        # Assert
        assert count == 3
        assert mock_session.delete.call_count == 3
        mock_session.flush.assert_called_once()

    def test_delete_expired_tokens_no_expired(self, repository, mock_session):
        """Test that delete_expired_tokens returns 0 when no expired tokens."""
        # Arrange
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result

        # Act
        count = repository.delete_expired_tokens(mock_session)

        # Assert
        assert count == 0
        mock_session.delete.assert_not_called()


class TestInvalidatePreviousTokens:
    """Test cases for invalidate_previous_tokens method."""

    def test_invalidate_previous_tokens_deletes_matching(self, repository, mock_session, sample_user_id, sample_email):
        """Test that invalidate_previous_tokens deletes all tokens of specified type for user.

        AC: Should delete all email_verification OR password_reset tokens for user
        """
        # Arrange - create 2 tokens of same type for user
        tokens = [
            EmailVerificationToken(
                id=str(uuid4()),
                user_id=sample_user_id,
                token_hash=f"hash{i}",
                email=sample_email,
                token_type="email_verification",
                expires_at=datetime.now(UTC) + timedelta(hours=24),
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            )
            for i in range(2)
        ]

        mock_result = MagicMock()
        mock_result.all.return_value = tokens
        mock_session.exec.return_value = mock_result

        # Act
        count = repository.invalidate_previous_tokens(mock_session, sample_user_id, "email_verification")

        # Assert
        assert count == 2
        assert mock_session.delete.call_count == 2
        mock_session.flush.assert_called_once()

    def test_invalidate_previous_tokens_no_matching(self, repository, mock_session, sample_user_id):
        """Test that invalidate_previous_tokens returns 0 when no tokens exist."""
        # Arrange
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result

        # Act
        count = repository.invalidate_previous_tokens(mock_session, sample_user_id, "password_reset")

        # Assert
        assert count == 0
        mock_session.delete.assert_not_called()


class TestDeleteTokensForUser:
    """Test cases for delete_tokens_for_user method."""

    def test_delete_tokens_for_user_all_types(self, repository, mock_session, sample_user_id, sample_email):
        """Test that delete_tokens_for_user deletes all tokens when no type specified."""
        # Arrange - create tokens of different types
        tokens = [
            EmailVerificationToken(
                id=str(uuid4()),
                user_id=sample_user_id,
                token_hash="hash1",
                email=sample_email,
                token_type="email_verification",
                expires_at=datetime.now(UTC) + timedelta(hours=24),
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            ),
            EmailVerificationToken(
                id=str(uuid4()),
                user_id=sample_user_id,
                token_hash="hash2",
                email=sample_email,
                token_type="password_reset",
                expires_at=datetime.now(UTC) + timedelta(hours=2),
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            ),
        ]

        mock_result = MagicMock()
        mock_result.all.return_value = tokens
        mock_session.exec.return_value = mock_result

        # Act
        count = repository.delete_tokens_for_user(mock_session, sample_user_id)

        # Assert
        assert count == 2
        assert mock_session.delete.call_count == 2

    def test_delete_tokens_for_user_specific_type(self, repository, mock_session, sample_user_id, sample_email):
        """Test that delete_tokens_for_user filters by type when specified."""
        # Arrange - only password_reset tokens
        tokens = [
            EmailVerificationToken(
                id=str(uuid4()),
                user_id=sample_user_id,
                token_hash="hash1",
                email=sample_email,
                token_type="password_reset",
                expires_at=datetime.now(UTC) + timedelta(hours=2),
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            )
        ]

        mock_result = MagicMock()
        mock_result.all.return_value = tokens
        mock_session.exec.return_value = mock_result

        # Act
        count = repository.delete_tokens_for_user(mock_session, sample_user_id, token_type="password_reset")

        # Assert
        assert count == 1
        mock_session.delete.assert_called_once()


class TestGetActiveTokenForUser:
    """Test cases for get_active_token_for_user method."""

    def test_get_active_token_for_user_found(self, repository, mock_session, sample_user_id, sample_email):
        """Test that get_active_token_for_user returns active token."""
        # Arrange
        active_token = EmailVerificationToken(
            id=str(uuid4()),
            user_id=sample_user_id,
            token_hash="hash123",
            email=sample_email,
            token_type="email_verification",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            used_at=None,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        )

        mock_result = MagicMock()
        mock_result.first.return_value = active_token
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_active_token_for_user(mock_session, sample_user_id, "email_verification")

        # Assert
        assert result == active_token

    def test_get_active_token_for_user_none_active(self, repository, mock_session, sample_user_id):
        """Test that get_active_token_for_user returns None when no active token exists."""
        # Arrange
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_active_token_for_user(mock_session, sample_user_id, "password_reset")

        # Assert
        assert result is None
