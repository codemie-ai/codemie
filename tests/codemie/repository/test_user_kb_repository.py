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

"""Unit tests for UserKnowledgeBaseRepository.

Tests verify CRUD operations for user-knowledge-base access management
(EPMCDME-10160).
"""

import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session

from codemie.repository.user_kb_repository import UserKnowledgeBaseRepository
from codemie.rest_api.models.user_management import UserKnowledgeBase


@pytest.fixture
def repository():
    """Provide UserKnowledgeBaseRepository instance."""
    return UserKnowledgeBaseRepository()


@pytest.fixture
def mock_session(mocker):
    """Mock database session for sync testing."""
    session = mocker.MagicMock(spec=Session)
    # Mock exec() to return a mock result that has first() and all() methods
    mock_result = MagicMock()
    session.exec.return_value = mock_result
    return session


@pytest.fixture
def sample_user_id():
    """Sample user ID for testing."""
    return str(uuid4())


@pytest.fixture
def sample_kb_name():
    """Sample knowledge base name for testing."""
    return "test_kb_name"


@pytest.fixture
def sample_user_kb(sample_user_id, sample_kb_name):
    """Sample UserKnowledgeBase record."""
    return UserKnowledgeBase(
        id=str(uuid4()),
        user_id=sample_user_id,
        kb_name=sample_kb_name,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


class TestGetByUserId:
    """Test cases for get_by_user_id method."""

    def test_get_by_user_id_returns_list(self, repository, mock_session, sample_user_id):
        """Test that get_by_user_id returns list of UserKnowledgeBase records.

        AC: Should return all KB access records for the specified user
        """
        # Arrange
        kb_records = [
            UserKnowledgeBase(
                id=str(uuid4()),
                user_id=sample_user_id,
                kb_name=f"kb_{i}",
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            )
            for i in range(3)
        ]

        mock_result = MagicMock()
        mock_result.all.return_value = kb_records
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_by_user_id(mock_session, sample_user_id)

        # Assert
        assert len(result) == 3
        assert all(isinstance(kb, UserKnowledgeBase) for kb in result)
        assert all(kb.user_id == sample_user_id for kb in result)
        mock_session.exec.assert_called_once()

    def test_get_by_user_id_empty_list(self, repository, mock_session, sample_user_id):
        """Test that get_by_user_id returns empty list when user has no KB access."""
        # Arrange
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_by_user_id(mock_session, sample_user_id)

        # Assert
        assert result == []
        assert isinstance(result, list)


class TestGetById:
    """Test cases for get_by_id method."""

    def test_get_by_id_found(self, repository, mock_session, sample_user_kb):
        """Test that get_by_id returns record when found."""
        # Arrange
        kb_id = sample_user_kb.id
        mock_result = MagicMock()
        mock_result.first.return_value = sample_user_kb
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_by_id(mock_session, kb_id)

        # Assert
        assert result == sample_user_kb
        assert result.id == kb_id

    def test_get_by_id_not_found(self, repository, mock_session):
        """Test that get_by_id returns None when record doesn't exist."""
        # Arrange
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_by_id(mock_session, str(uuid4()))

        # Assert
        assert result is None


class TestGetByUserAndKb:
    """Test cases for get_by_user_and_kb method."""

    def test_get_by_user_and_kb_found(self, repository, mock_session, sample_user_id, sample_kb_name):
        """Test that get_by_user_and_kb returns record when user has access to KB.

        AC: Should find existing access record by user_id and kb_name combination
        """
        # Arrange
        expected_record = UserKnowledgeBase(
            id=str(uuid4()),
            user_id=sample_user_id,
            kb_name=sample_kb_name,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        )

        mock_result = MagicMock()
        mock_result.first.return_value = expected_record
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_by_user_and_kb(mock_session, sample_user_id, sample_kb_name)

        # Assert
        assert result == expected_record
        assert result.user_id == sample_user_id
        assert result.kb_name == sample_kb_name

    def test_get_by_user_and_kb_not_found(self, repository, mock_session, sample_user_id):
        """Test that get_by_user_and_kb returns None when access doesn't exist."""
        # Arrange
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_by_user_and_kb(mock_session, sample_user_id, "nonexistent_kb")

        # Assert
        assert result is None


class TestAddKb:
    """Test cases for add_kb method."""

    def test_add_kb_creates_record(self, repository, mock_session, sample_user_id, sample_kb_name):
        """Test that add_kb creates new UserKnowledgeBase record.

        AC: Should create access record with user_id, kb_name, and timestamps
        """
        # Arrange - no specific setup needed

        # Act
        before_add = datetime.now(UTC)
        result = repository.add_kb(mock_session, sample_user_id, sample_kb_name)
        after_add = datetime.now(UTC)

        # Assert
        assert isinstance(result, UserKnowledgeBase)
        assert result.user_id == sample_user_id
        assert result.kb_name == sample_kb_name
        assert result.date is not None
        assert result.update_date is not None
        # Timestamps should be within test execution window
        assert before_add <= result.date <= after_add
        assert before_add <= result.update_date <= after_add
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once_with(result)

    def test_add_kb_sets_timestamps(self, repository, mock_session, sample_user_id, sample_kb_name):
        """Test that add_kb sets both date and update_date to current UTC time."""
        # Arrange - no specific setup needed

        # Act
        result = repository.add_kb(mock_session, sample_user_id, sample_kb_name)

        # Assert - date and update_date should be equal at creation
        assert result.date == result.update_date
        # Verify timezone-aware (UTC) - sync method stores with timezone
        assert result.date.tzinfo == UTC


class TestRemoveKb:
    """Test cases for remove_kb method."""

    def test_remove_kb_success(self, repository, mock_session, sample_user_id, sample_kb_name):
        """Test that remove_kb deletes access record when it exists.

        AC: Should delete record and return True
        """
        # Arrange
        existing_record = UserKnowledgeBase(
            id=str(uuid4()),
            user_id=sample_user_id,
            kb_name=sample_kb_name,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        )

        mock_result = MagicMock()
        mock_result.first.return_value = existing_record
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.remove_kb(mock_session, sample_user_id, sample_kb_name)

        # Assert
        assert result is True
        mock_session.delete.assert_called_once_with(existing_record)
        mock_session.flush.assert_called_once()

    def test_remove_kb_not_found(self, repository, mock_session, sample_user_id):
        """Test that remove_kb returns False when access doesn't exist.

        AC: Should not delete anything and return False
        """
        # Arrange
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.remove_kb(mock_session, sample_user_id, "nonexistent_kb")

        # Assert
        assert result is False
        mock_session.delete.assert_not_called()
        mock_session.flush.assert_not_called()


class TestHasAccess:
    """Test cases for has_access method."""

    def test_has_access_true(self, repository, mock_session, sample_user_id, sample_kb_name):
        """Test that has_access returns True when user has access to KB."""
        # Arrange
        existing_record = UserKnowledgeBase(
            id=str(uuid4()),
            user_id=sample_user_id,
            kb_name=sample_kb_name,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        )

        mock_result = MagicMock()
        mock_result.first.return_value = existing_record
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.has_access(mock_session, sample_user_id, sample_kb_name)

        # Assert
        assert result is True

    def test_has_access_false(self, repository, mock_session, sample_user_id):
        """Test that has_access returns False when user doesn't have access."""
        # Arrange
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.has_access(mock_session, sample_user_id, "nonexistent_kb")

        # Assert
        assert result is False


class TestGetKbNames:
    """Test cases for get_kb_names method."""

    def test_get_kb_names_returns_names_only(self, repository, mock_session, sample_user_id):
        """Test that get_kb_names returns list of KB names without full records."""
        # Arrange
        kb_records = [
            UserKnowledgeBase(
                id=str(uuid4()),
                user_id=sample_user_id,
                kb_name=f"kb_{i}",
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            )
            for i in range(3)
        ]

        mock_result = MagicMock()
        mock_result.all.return_value = kb_records
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_kb_names(mock_session, sample_user_id)

        # Assert
        assert result == ["kb_0", "kb_1", "kb_2"]
        assert all(isinstance(name, str) for name in result)

    def test_get_kb_names_empty_list(self, repository, mock_session, sample_user_id):
        """Test that get_kb_names returns empty list when user has no KB access."""
        # Arrange
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_kb_names(mock_session, sample_user_id)

        # Assert
        assert result == []


class TestDeleteAllForUser:
    """Test cases for delete_all_for_user method."""

    def test_delete_all_for_user_removes_all(self, repository, mock_session, sample_user_id):
        """Test that delete_all_for_user removes all KB access for a user.

        AC: Should delete all records and return count
        """
        # Arrange - create 3 KB access records for user
        kb_records = [
            UserKnowledgeBase(
                id=str(uuid4()),
                user_id=sample_user_id,
                kb_name=f"kb_{i}",
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            )
            for i in range(3)
        ]

        mock_result = MagicMock()
        mock_result.all.return_value = kb_records
        mock_session.exec.return_value = mock_result

        # Act
        count = repository.delete_all_for_user(mock_session, sample_user_id)

        # Assert
        assert count == 3
        assert mock_session.delete.call_count == 3
        mock_session.flush.assert_called_once()

    def test_delete_all_for_user_no_records(self, repository, mock_session, sample_user_id):
        """Test that delete_all_for_user returns 0 when user has no KB access."""
        # Arrange
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result

        # Act
        count = repository.delete_all_for_user(mock_session, sample_user_id)

        # Assert
        assert count == 0
        mock_session.delete.assert_not_called()


# ===========================================
# Async Method Tests
# ===========================================


class TestAsyncGetByUserId:
    """Test cases for aget_by_user_id async method."""

    @pytest.mark.asyncio
    async def test_aget_by_user_id_returns_list(self, repository, sample_user_id):
        """Test that aget_by_user_id returns list of UserKnowledgeBase records (async).

        AC: Should return all KB access records for the specified user
        """
        # Arrange
        kb_records = [
            UserKnowledgeBase(
                id=str(uuid4()),
                user_id=sample_user_id,
                kb_name=f"kb_{i}",
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            )
            for i in range(3)
        ]

        mock_session = AsyncMock(spec=AsyncSession)
        # Mock the async execute chain properly
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = kb_records
        mock_result = MagicMock()  # Use MagicMock not AsyncMock for result
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        # Act
        result = await repository.aget_by_user_id(mock_session, sample_user_id)

        # Assert
        assert len(result) == 3
        assert all(isinstance(kb, UserKnowledgeBase) for kb in result)
        assert all(kb.user_id == sample_user_id for kb in result)
        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aget_by_user_id_empty_list(self, repository, sample_user_id):
        """Test that aget_by_user_id returns empty list when user has no KB access (async)."""
        # Arrange
        mock_session = AsyncMock(spec=AsyncSession)
        # Mock the async execute chain properly
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()  # Use MagicMock not AsyncMock for result
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        # Act
        result = await repository.aget_by_user_id(mock_session, sample_user_id)

        # Assert
        assert result == []
        assert isinstance(result, list)


class TestAsyncAddKb:
    """Test cases for aadd_kb async method."""

    @pytest.mark.asyncio
    async def test_aadd_kb_creates_record(self, repository, sample_user_id, sample_kb_name):
        """Test that aadd_kb creates new UserKnowledgeBase record (async).

        AC: Should create access record with user_id, kb_name, and timestamps
        """
        # Arrange
        mock_session = AsyncMock(spec=AsyncSession)

        # Act
        before_add = datetime.now(UTC)
        result = await repository.aadd_kb(mock_session, sample_user_id, sample_kb_name)
        after_add = datetime.now(UTC)

        # Assert
        assert isinstance(result, UserKnowledgeBase)
        assert result.user_id == sample_user_id
        assert result.kb_name == sample_kb_name
        assert result.date is not None
        assert result.update_date is not None
        # Timestamps should be within test execution window
        assert before_add <= result.date.replace(tzinfo=UTC) <= after_add
        assert before_add <= result.update_date.replace(tzinfo=UTC) <= after_add
        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited_once()
        mock_session.refresh.assert_awaited_once_with(result)

    @pytest.mark.asyncio
    async def test_aadd_kb_sets_timestamps_naive_utc(self, repository, sample_user_id, sample_kb_name):
        """Test that aadd_kb sets timestamps as naive UTC (tzinfo=None) per implementation.

        AC: Repository implementation uses datetime.now(UTC).replace(tzinfo=None)
        """
        # Arrange
        mock_session = AsyncMock(spec=AsyncSession)

        # Act
        result = await repository.aadd_kb(mock_session, sample_user_id, sample_kb_name)

        # Assert
        # Verify naive datetime (no timezone info)
        assert result.date.tzinfo is None
        assert result.update_date.tzinfo is None
        # Verify date and update_date are equal at creation
        assert result.date == result.update_date


# ===========================================
# Edge Cases and Integration Tests
# ===========================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_get_by_user_and_kb_with_special_characters(self, repository, mock_session):
        """Test that get_by_user_and_kb handles KB names with special characters."""
        # Arrange
        user_id = str(uuid4())
        kb_name_with_special_chars = "kb-name_with.special@chars"
        expected_record = UserKnowledgeBase(
            id=str(uuid4()),
            user_id=user_id,
            kb_name=kb_name_with_special_chars,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        )

        mock_result = MagicMock()
        mock_result.first.return_value = expected_record
        mock_session.exec.return_value = mock_result

        # Act
        result = repository.get_by_user_and_kb(mock_session, user_id, kb_name_with_special_chars)

        # Assert
        assert result == expected_record
        assert result.kb_name == kb_name_with_special_chars

    def test_add_kb_with_long_kb_name(self, repository, mock_session):
        """Test that add_kb handles long KB names (boundary testing)."""
        # Arrange
        user_id = str(uuid4())
        long_kb_name = "kb_" + "a" * 250  # Very long KB name

        # Act
        result = repository.add_kb(mock_session, user_id, long_kb_name)

        # Assert
        assert result.kb_name == long_kb_name
        assert len(result.kb_name) == 253

    def test_delete_all_for_user_preserves_other_users_data(self, repository, mock_session):
        """Test that delete_all_for_user only deletes records for specified user."""
        # Arrange
        user_a_id = str(uuid4())

        # Only user_a's records
        user_a_records = [
            UserKnowledgeBase(
                id=str(uuid4()),
                user_id=user_a_id,
                kb_name=f"kb_{i}",
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            )
            for i in range(2)
        ]

        mock_result = MagicMock()
        mock_result.all.return_value = user_a_records
        mock_session.exec.return_value = mock_result

        # Act
        count = repository.delete_all_for_user(mock_session, user_a_id)

        # Assert
        assert count == 2  # Only user_a's records deleted
        # Verify delete was called only for user_a's records
        assert mock_session.delete.call_count == 2
        for call_args in mock_session.delete.call_args_list:
            deleted_record = call_args[0][0]
            assert deleted_record.user_id == user_a_id  # Only user_a's records


class TestSingletonInstance:
    """Test singleton pattern implementation."""

    def test_singleton_instance_exists(self):
        """Test that user_kb_repository singleton instance is accessible."""
        # Import the singleton instance
        from codemie.repository.user_kb_repository import user_kb_repository

        # Assert
        assert user_kb_repository is not None
        assert isinstance(user_kb_repository, UserKnowledgeBaseRepository)
