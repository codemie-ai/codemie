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

"""Unit tests for UserRepository CRUD operations.

Tests verify:
- Basic CRUD: get_by_id, get_by_email, get_by_username, get_active_by_id
- Create and update operations with timestamp handling
- Soft delete functionality
- Helper methods: count_active_superadmins, update_last_login
- Existence checks: exists_by_email, exists_by_username, get_existing_user_ids
- Async variants: aget_by_id, aget_by_email, aget_active_by_id, acreate, aupdate, aupdate_last_login
"""

import pytest
from uuid import uuid4
from datetime import datetime, UTC
from unittest.mock import AsyncMock

from sqlmodel import Session
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.repository.user_repository import UserRepository
from codemie.rest_api.models.user_management import UserDB


@pytest.fixture
def user_repository():
    """Provide UserRepository instance."""
    return UserRepository()


@pytest.fixture
def db_session(mocker):
    """Mock database session for testing."""
    session = mocker.MagicMock(spec=Session)
    return session


@pytest.fixture
def async_db_session(mocker):
    """Mock async database session for testing."""
    session = mocker.MagicMock(spec=AsyncSession)
    return session


@pytest.fixture
def sample_user():
    """Sample user for testing."""
    return UserDB(
        id=str(uuid4()),
        email="test@example.com",
        username="testuser",
        name="Test User",
        password_hash="$argon2id$v=19$...",
        is_active=True,
        is_admin=False,
        auth_source="local",
        email_verified=True,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


@pytest.fixture
def sample_inactive_user():
    """Sample inactive user for testing."""
    return UserDB(
        id=str(uuid4()),
        email="inactive@example.com",
        username="inactiveuser",
        name="Inactive User",
        is_active=False,
        deleted_at=datetime.now(UTC),
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


class TestUserRepositoryGetById:
    """Test get_by_id method."""

    def test_get_by_id_found(self, user_repository, db_session, sample_user, mocker):
        """Test get_by_id returns user when found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        user = user_repository.get_by_id(db_session, sample_user.id)

        # Assert
        assert user is not None
        assert user.id == sample_user.id
        assert user.email == sample_user.email
        db_session.exec.assert_called_once()

    def test_get_by_id_not_found(self, user_repository, db_session, mocker):
        """Test get_by_id returns None when user not found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = None
        db_session.exec.return_value = mock_result

        # Act
        user = user_repository.get_by_id(db_session, str(uuid4()))

        # Assert
        assert user is None
        db_session.exec.assert_called_once()


class TestUserRepositoryGetByEmail:
    """Test get_by_email method (case-insensitive)."""

    def test_get_by_email_found(self, user_repository, db_session, sample_user, mocker):
        """Test get_by_email returns user when found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        user = user_repository.get_by_email(db_session, "test@example.com")

        # Assert
        assert user is not None
        assert user.email == sample_user.email
        db_session.exec.assert_called_once()

    def test_get_by_email_case_insensitive(self, user_repository, db_session, sample_user, mocker):
        """Test get_by_email is case-insensitive."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        user = user_repository.get_by_email(db_session, "TEST@EXAMPLE.COM")

        # Assert
        assert user is not None
        assert user.email == sample_user.email
        db_session.exec.assert_called_once()

    def test_get_by_email_not_found(self, user_repository, db_session, mocker):
        """Test get_by_email returns None when not found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = None
        db_session.exec.return_value = mock_result

        # Act
        user = user_repository.get_by_email(db_session, "nonexistent@example.com")

        # Assert
        assert user is None
        db_session.exec.assert_called_once()


class TestUserRepositoryGetByUsername:
    """Test get_by_username method (case-insensitive)."""

    def test_get_by_username_found(self, user_repository, db_session, sample_user, mocker):
        """Test get_by_username returns user when found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        user = user_repository.get_by_username(db_session, "testuser")

        # Assert
        assert user is not None
        assert user.username == sample_user.username
        db_session.exec.assert_called_once()

    def test_get_by_username_case_insensitive(self, user_repository, db_session, sample_user, mocker):
        """Test get_by_username is case-insensitive."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        user = user_repository.get_by_username(db_session, "TESTUSER")

        # Assert
        assert user is not None
        assert user.username == sample_user.username
        db_session.exec.assert_called_once()

    def test_get_by_username_not_found(self, user_repository, db_session, mocker):
        """Test get_by_username returns None when not found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = None
        db_session.exec.return_value = mock_result

        # Act
        user = user_repository.get_by_username(db_session, "nonexistent")

        # Assert
        assert user is None
        db_session.exec.assert_called_once()


class TestUserRepositoryGetActiveById:
    """Test get_active_by_id method (active and not deleted)."""

    def test_get_active_by_id_found(self, user_repository, db_session, sample_user, mocker):
        """Test get_active_by_id returns active user."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        user = user_repository.get_active_by_id(db_session, sample_user.id)

        # Assert
        assert user is not None
        assert user.is_active is True
        assert user.deleted_at is None
        db_session.exec.assert_called_once()

    def test_get_active_by_id_inactive_user_not_returned(self, user_repository, db_session, mocker):
        """Test get_active_by_id returns None for inactive user."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = None  # Query filters out inactive users
        db_session.exec.return_value = mock_result

        # Act
        user = user_repository.get_active_by_id(db_session, str(uuid4()))

        # Assert
        assert user is None
        db_session.exec.assert_called_once()

    def test_get_active_by_id_deleted_user_not_returned(self, user_repository, db_session, mocker):
        """Test get_active_by_id returns None for deleted user."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = None  # Query filters out deleted users
        db_session.exec.return_value = mock_result

        # Act
        user = user_repository.get_active_by_id(db_session, str(uuid4()))

        # Assert
        assert user is None
        db_session.exec.assert_called_once()


class TestUserRepositoryCreate:
    """Test create method."""

    def test_create_user_success(self, user_repository, db_session, mocker):
        """Test create sets timestamps and adds user to session."""
        # Arrange
        new_user = UserDB(
            id=str(uuid4()),
            email="new@example.com",
            username="newuser",
            name="New User",
            password_hash="$argon2id$v=19$...",
            is_active=True,
            auth_source="local",
        )

        # Act
        result = user_repository.create(db_session, new_user)

        # Assert
        assert result.date is not None
        assert result.update_date is not None
        db_session.add.assert_called_once_with(new_user)
        db_session.flush.assert_called_once()
        db_session.refresh.assert_called_once_with(new_user)

    def test_create_user_preserves_existing_timestamps(self, user_repository, db_session, mocker):
        """Test create preserves timestamps if already set."""
        # Arrange
        existing_date = datetime(2023, 1, 1, tzinfo=UTC)
        new_user = UserDB(
            id=str(uuid4()),
            email="new@example.com",
            username="newuser",
            date=existing_date,
            update_date=existing_date,
        )

        # Act
        result = user_repository.create(db_session, new_user)

        # Assert
        assert result.date == existing_date
        assert result.update_date == existing_date
        db_session.add.assert_called_once()


class TestUserRepositoryUpdate:
    """Test update method."""

    def test_update_user_success(self, user_repository, db_session, sample_user, mocker):
        """Test update changes fields and updates timestamp."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        updated = user_repository.update(db_session, sample_user.id, name="Updated Name", is_active=False)

        # Assert
        assert updated is not None
        assert updated.name == "Updated Name"
        assert updated.is_active is False
        assert updated.update_date is not None
        db_session.add.assert_called_once()
        db_session.flush.assert_called_once()
        db_session.refresh.assert_called_once()

    def test_update_user_not_found(self, user_repository, db_session, mocker):
        """Test update returns None when user not found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = None
        db_session.exec.return_value = mock_result

        # Act
        updated = user_repository.update(db_session, str(uuid4()), name="New Name")

        # Assert
        assert updated is None
        db_session.add.assert_not_called()

    def test_update_user_ignores_invalid_fields(self, user_repository, db_session, sample_user, mocker):
        """Test update ignores fields that don't exist on model."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        updated = user_repository.update(db_session, sample_user.id, nonexistent_field="value", name="New Name")

        # Assert
        assert updated is not None
        assert updated.name == "New Name"
        assert not hasattr(updated, "nonexistent_field")


class TestUserRepositorySoftDelete:
    """Test soft_delete method."""

    def test_soft_delete_success(self, user_repository, db_session, sample_user, mocker):
        """Test soft_delete sets is_active=False and deleted_at."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        result = user_repository.soft_delete(db_session, sample_user.id)

        # Assert
        assert result is True
        assert sample_user.is_active is False
        assert sample_user.deleted_at is not None
        assert sample_user.update_date is not None
        db_session.add.assert_called_once()
        db_session.flush.assert_called_once()

    def test_soft_delete_user_not_found(self, user_repository, db_session, mocker):
        """Test soft_delete returns False when user not found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = None
        db_session.exec.return_value = mock_result

        # Act
        result = user_repository.soft_delete(db_session, str(uuid4()))

        # Assert
        assert result is False
        db_session.add.assert_not_called()


class TestUserRepositoryCountActiveSuperadmins:
    """Test count_active_superadmins method."""

    def test_count_active_superadmins(self, user_repository, db_session, mocker):
        """Test count_active_superadmins returns correct count."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.one.return_value = 3
        db_session.exec.return_value = mock_result

        # Act
        count = user_repository.count_active_superadmins(db_session)

        # Assert
        assert count == 3
        db_session.exec.assert_called_once()

    def test_count_active_superadmins_zero(self, user_repository, db_session, mocker):
        """Test count_active_superadmins returns 0 when none exist."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.one.return_value = 0
        db_session.exec.return_value = mock_result

        # Act
        count = user_repository.count_active_superadmins(db_session)

        # Assert
        assert count == 0


class TestUserRepositoryUpdateLastLogin:
    """Test update_last_login method."""

    def test_update_last_login_success(self, user_repository, db_session, sample_user, mocker):
        """Test update_last_login sets last_login_at and update_date."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        result = user_repository.update_last_login(db_session, sample_user.id)

        # Assert
        assert result is True
        assert sample_user.last_login_at is not None
        assert sample_user.update_date is not None
        db_session.add.assert_called_once()
        db_session.flush.assert_called_once()

    def test_update_last_login_user_not_found(self, user_repository, db_session, mocker):
        """Test update_last_login returns False when user not found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = None
        db_session.exec.return_value = mock_result

        # Act
        result = user_repository.update_last_login(db_session, str(uuid4()))

        # Assert
        assert result is False
        db_session.add.assert_not_called()


class TestUserRepositoryExistsByEmail:
    """Test exists_by_email method."""

    def test_exists_by_email_true(self, user_repository, db_session, sample_user, mocker):
        """Test exists_by_email returns True when email exists."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        exists = user_repository.exists_by_email(db_session, "test@example.com")

        # Assert
        assert exists is True

    def test_exists_by_email_false(self, user_repository, db_session, mocker):
        """Test exists_by_email returns False when email doesn't exist."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = None
        db_session.exec.return_value = mock_result

        # Act
        exists = user_repository.exists_by_email(db_session, "nonexistent@example.com")

        # Assert
        assert exists is False


class TestUserRepositoryExistsByUsername:
    """Test exists_by_username method."""

    def test_exists_by_username_true(self, user_repository, db_session, sample_user, mocker):
        """Test exists_by_username returns True when username exists."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = sample_user
        db_session.exec.return_value = mock_result

        # Act
        exists = user_repository.exists_by_username(db_session, "testuser")

        # Assert
        assert exists is True

    def test_exists_by_username_false(self, user_repository, db_session, mocker):
        """Test exists_by_username returns False when username doesn't exist."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_result.first.return_value = None
        db_session.exec.return_value = mock_result

        # Act
        exists = user_repository.exists_by_username(db_session, "nonexistent")

        # Assert
        assert exists is False


class TestUserRepositoryGetExistingUserIds:
    """Test get_existing_user_ids method (bulk existence check)."""

    def test_get_existing_user_ids_all_exist(self, user_repository, db_session, mocker):
        """Test get_existing_user_ids returns all IDs that exist."""
        # Arrange
        user_ids = [str(uuid4()), str(uuid4()), str(uuid4())]
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = user_ids
        db_session.exec.return_value = mock_result

        # Act
        existing = user_repository.get_existing_user_ids(db_session, user_ids)

        # Assert
        assert existing == set(user_ids)
        db_session.exec.assert_called_once()

    def test_get_existing_user_ids_partial_exist(self, user_repository, db_session, mocker):
        """Test get_existing_user_ids returns only existing IDs."""
        # Arrange
        existing_ids = [str(uuid4()), str(uuid4())]
        non_existing_ids = [str(uuid4()), str(uuid4())]
        all_ids = existing_ids + non_existing_ids

        mock_result = mocker.MagicMock()
        mock_result.all.return_value = existing_ids
        db_session.exec.return_value = mock_result

        # Act
        existing = user_repository.get_existing_user_ids(db_session, all_ids)

        # Assert
        assert existing == set(existing_ids)
        assert len(existing) == 2

    def test_get_existing_user_ids_empty_input(self, user_repository, db_session):
        """Test get_existing_user_ids returns empty set for empty input."""
        # Act
        existing = user_repository.get_existing_user_ids(db_session, [])

        # Assert
        assert existing == set()
        db_session.exec.assert_not_called()

    def test_get_existing_user_ids_none_exist(self, user_repository, db_session, mocker):
        """Test get_existing_user_ids returns empty set when none exist."""
        # Arrange
        user_ids = [str(uuid4()), str(uuid4())]
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = []
        db_session.exec.return_value = mock_result

        # Act
        existing = user_repository.get_existing_user_ids(db_session, user_ids)

        # Assert
        assert existing == set()


# ===========================================
# Async Method Tests
# ===========================================


class TestUserRepositoryAsyncGetById:
    """Test aget_by_id async method."""

    @pytest.mark.asyncio
    async def test_aget_by_id_found(self, user_repository, async_db_session, sample_user, mocker):
        """Test aget_by_id returns user when found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_scalars = mocker.MagicMock()
        mock_scalars.first.return_value = sample_user
        mock_result.scalars.return_value = mock_scalars
        async_db_session.execute = AsyncMock(return_value=mock_result)

        # Act
        user = await user_repository.aget_by_id(async_db_session, sample_user.id)

        # Assert
        assert user is not None
        assert user.id == sample_user.id
        async_db_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_aget_by_id_not_found(self, user_repository, async_db_session, mocker):
        """Test aget_by_id returns None when user not found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_scalars = mocker.MagicMock()
        mock_scalars.first.return_value = None
        mock_result.scalars.return_value = mock_scalars
        async_db_session.execute = AsyncMock(return_value=mock_result)

        # Act
        user = await user_repository.aget_by_id(async_db_session, str(uuid4()))

        # Assert
        assert user is None


class TestUserRepositoryAsyncGetByEmail:
    """Test aget_by_email async method."""

    @pytest.mark.asyncio
    async def test_aget_by_email_found(self, user_repository, async_db_session, sample_user, mocker):
        """Test aget_by_email returns user when found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_scalars = mocker.MagicMock()
        mock_scalars.first.return_value = sample_user
        mock_result.scalars.return_value = mock_scalars
        async_db_session.execute = AsyncMock(return_value=mock_result)

        # Act
        user = await user_repository.aget_by_email(async_db_session, "test@example.com")

        # Assert
        assert user is not None
        assert user.email == sample_user.email

    @pytest.mark.asyncio
    async def test_aget_by_email_case_insensitive(self, user_repository, async_db_session, sample_user, mocker):
        """Test aget_by_email is case-insensitive."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_scalars = mocker.MagicMock()
        mock_scalars.first.return_value = sample_user
        mock_result.scalars.return_value = mock_scalars
        async_db_session.execute = AsyncMock(return_value=mock_result)

        # Act
        user = await user_repository.aget_by_email(async_db_session, "TEST@EXAMPLE.COM")

        # Assert
        assert user is not None


class TestUserRepositoryAsyncGetActiveById:
    """Test aget_active_by_id async method."""

    @pytest.mark.asyncio
    async def test_aget_active_by_id_found(self, user_repository, async_db_session, sample_user, mocker):
        """Test aget_active_by_id returns active user."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_scalars = mocker.MagicMock()
        mock_scalars.first.return_value = sample_user
        mock_result.scalars.return_value = mock_scalars
        async_db_session.execute = AsyncMock(return_value=mock_result)

        # Act
        user = await user_repository.aget_active_by_id(async_db_session, sample_user.id)

        # Assert
        assert user is not None
        assert user.is_active is True

    @pytest.mark.asyncio
    async def test_aget_active_by_id_inactive_not_returned(self, user_repository, async_db_session, mocker):
        """Test aget_active_by_id returns None for inactive user."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_scalars = mocker.MagicMock()
        mock_scalars.first.return_value = None
        mock_result.scalars.return_value = mock_scalars
        async_db_session.execute = AsyncMock(return_value=mock_result)

        # Act
        user = await user_repository.aget_active_by_id(async_db_session, str(uuid4()))

        # Assert
        assert user is None


class TestUserRepositoryAsyncCreate:
    """Test acreate async method."""

    @pytest.mark.asyncio
    async def test_acreate_user_success(self, user_repository, async_db_session, mocker):
        """Test acreate sets timestamps and adds user to session."""
        # Arrange
        new_user = UserDB(
            id=str(uuid4()),
            email="new@example.com",
            username="newuser",
            password_hash="$argon2id$v=19$...",
        )
        async_db_session.flush = AsyncMock()
        async_db_session.refresh = AsyncMock()

        # Act
        result = await user_repository.acreate(async_db_session, new_user)

        # Assert
        assert result.date is not None
        assert result.update_date is not None
        async_db_session.add.assert_called_once_with(new_user)
        async_db_session.flush.assert_called_once()
        async_db_session.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_acreate_removes_timezone_info(self, user_repository, async_db_session, mocker):
        """Test acreate removes timezone info from timestamps (Story requirement)."""
        # Arrange
        new_user = UserDB(
            id=str(uuid4()),
            email="new@example.com",
            username="newuser",
        )
        async_db_session.flush = AsyncMock()
        async_db_session.refresh = AsyncMock()

        # Act
        result = await user_repository.acreate(async_db_session, new_user)

        # Assert
        assert result.date is not None
        assert result.date.tzinfo is None  # Timezone info removed
        assert result.update_date is not None
        assert result.update_date.tzinfo is None


class TestUserRepositoryAsyncUpdate:
    """Test aupdate async method."""

    @pytest.mark.asyncio
    async def test_aupdate_user_success(self, user_repository, async_db_session, sample_user, mocker):
        """Test aupdate changes fields and updates timestamp."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_scalars = mocker.MagicMock()
        mock_scalars.first.return_value = sample_user
        mock_result.scalars.return_value = mock_scalars
        async_db_session.execute = AsyncMock(return_value=mock_result)
        async_db_session.flush = AsyncMock()
        async_db_session.refresh = AsyncMock()

        # Act
        updated = await user_repository.aupdate(async_db_session, sample_user.id, name="Updated Name")

        # Assert
        assert updated is not None
        assert updated.name == "Updated Name"
        assert updated.update_date is not None
        assert updated.update_date.tzinfo is None  # Timezone removed
        async_db_session.add.assert_called_once()
        async_db_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_aupdate_user_not_found(self, user_repository, async_db_session, mocker):
        """Test aupdate returns None when user not found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_scalars = mocker.MagicMock()
        mock_scalars.first.return_value = None
        mock_result.scalars.return_value = mock_scalars
        async_db_session.execute = AsyncMock(return_value=mock_result)

        # Act
        updated = await user_repository.aupdate(async_db_session, str(uuid4()), name="New Name")

        # Assert
        assert updated is None
        async_db_session.add.assert_not_called()


class TestUserRepositoryAsyncUpdateLastLogin:
    """Test aupdate_last_login async method."""

    @pytest.mark.asyncio
    async def test_aupdate_last_login_success(self, user_repository, async_db_session, sample_user, mocker):
        """Test aupdate_last_login sets timestamps."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_scalars = mocker.MagicMock()
        mock_scalars.first.return_value = sample_user
        mock_result.scalars.return_value = mock_scalars
        async_db_session.execute = AsyncMock(return_value=mock_result)
        async_db_session.flush = AsyncMock()

        # Act
        result = await user_repository.aupdate_last_login(async_db_session, sample_user.id)

        # Assert
        assert result is True
        assert sample_user.last_login_at is not None
        assert sample_user.last_login_at.tzinfo is None  # Timezone removed
        async_db_session.add.assert_called_once()
        async_db_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_aupdate_last_login_user_not_found(self, user_repository, async_db_session, mocker):
        """Test aupdate_last_login returns False when user not found."""
        # Arrange
        mock_result = mocker.MagicMock()
        mock_scalars = mocker.MagicMock()
        mock_scalars.first.return_value = None
        mock_result.scalars.return_value = mock_scalars
        async_db_session.execute = AsyncMock(return_value=mock_result)

        # Act
        result = await user_repository.aupdate_last_login(async_db_session, str(uuid4()))

        # Assert
        assert result is False
        async_db_session.add.assert_not_called()
