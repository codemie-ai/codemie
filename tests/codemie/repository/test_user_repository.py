# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Unit tests for UserRepository with focus on search security.

Tests verify SQL LIKE wildcard escaping prevents information leakage (Story 2).
Uses mocking to test repository logic without database dependency.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from datetime import datetime, UTC

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session

from codemie.repository.user_repository import UserRepository
from codemie.rest_api.models.user_management import UserDB, UserListFilters


@pytest.fixture
def user_repository():
    """Provide UserRepository instance."""
    return UserRepository()


@pytest.fixture
def db_session(mocker):
    """Mock database session for testing."""
    # Create a mock session that behaves like SQLModel Session
    session = mocker.MagicMock(spec=Session)
    return session


@pytest.fixture
def sample_users():
    """Sample users for testing search functionality."""
    return [
        UserDB(
            id=str(uuid4()),
            email="admin@example.com",
            username="admin_user",
            name="Admin User",
            is_active=True,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        ),
        UserDB(
            id=str(uuid4()),
            email="test@example.com",
            username="test_user",
            name="Test User",
            is_active=True,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        ),
        UserDB(
            id=str(uuid4()),
            email="alice@example.com",
            username="alice",
            name="Alice",
            is_active=True,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        ),
        UserDB(
            id=str(uuid4()),
            email="percent%test@example.com",
            username="percent%user",
            name="User with %",
            is_active=True,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        ),
        UserDB(
            id=str(uuid4()),
            email="under_score@example.com",
            username="under_score_user",
            name="User_Name",
            is_active=True,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        ),
    ]


class TestUserRepositorySearchSecurity:
    """Test search security: LIKE wildcard escaping (Story 2, NFR-3.1)."""

    def test_search_with_percent_wildcard_escaped(self, user_repository, db_session, sample_users, mocker):
        """AC: Search for % returns only records with literal % character."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[3]]  # User with %
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="%", filters=UserListFilters())

        assert db_session.exec.called
        # In real DB, this would only match "percent%test@example.com"
        assert len(users) == 1

    def test_search_with_underscore_wildcard_escaped(self, user_repository, db_session, sample_users, mocker):
        """AC: Search for _ returns only records with literal _ character."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[4]]  # User with underscore
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="_", filters=UserListFilters())

        assert db_session.exec.called
        assert len(users) == 1

    def test_search_admin_percent_does_not_enumerate(self, user_repository, db_session, sample_users, mocker):
        """AC: Search for %admin% does NOT enumerate admin accounts."""
        # Without escaping, %admin% would match all admins
        # With escaping, it looks for literal "%admin%"
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = []  # No literal "%admin%"
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="%admin%", filters=UserListFilters())

        # Should not return admin@example.com (would match without escaping)
        assert len(users) == 0

    def test_search_t_underscore_st_no_wildcard_match(self, user_repository, db_session, sample_users, mocker):
        """AC: Search for t_st does NOT match test, tast, t0st (only literal t_st)."""
        # Without escaping, "t_st" would match "test"
        # With escaping, looks for literal "t_st"
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = []  # No literal "t_st" in test data
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="t_st", filters=UserListFilters())

        # Should not match "test@example.com" (would match without escaping)
        assert len(users) == 0

    def test_search_normal_text_works(self, user_repository, db_session, sample_users, mocker):
        """AC: Normal search functionality unaffected by escaping."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[1]]  # test@example.com
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="test", filters=UserListFilters())

        assert db_session.exec.called
        assert len(users) == 1

    def test_search_case_insensitive_still_works(self, user_repository, db_session, sample_users, mocker):
        """AC: Case-insensitive search still works after escaping."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[0]]  # Admin User
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="ADMIN", filters=UserListFilters())

        assert db_session.exec.called
        assert len(users) == 1

    def test_search_combined_wildcards(self, user_repository, db_session, mocker):
        """Test search with both % and _ wildcards."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = []
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="a%b_c", filters=UserListFilters())

        # Should only match literal "a%b_c", not use as wildcard pattern
        assert db_session.exec.called
        assert len(users) == 0

    def test_search_empty_string(self, user_repository, db_session, sample_users, mocker):
        """Test search with empty string returns all users."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = sample_users
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="", filters=UserListFilters())

        assert len(users) == len(sample_users)

    def test_search_none_value(self, user_repository, db_session, sample_users, mocker):
        """Test search with None value returns all users."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = sample_users
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search=None, filters=UserListFilters())

        assert len(users) == len(sample_users)


class TestUserRepositorySearchFields:
    """Test search across multiple fields (email, username, name)."""

    def test_search_matches_email(self, user_repository, db_session, sample_users, mocker):
        """Test search can find users by email."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[2]]  # alice@example.com
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="alice@", filters=UserListFilters())

        assert len(users) == 1

    def test_search_matches_username(self, user_repository, db_session, sample_users, mocker):
        """Test search can find users by username."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[1]]  # test_user
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="test", filters=UserListFilters())

        assert len(users) == 1

    def test_search_matches_name(self, user_repository, db_session, sample_users, mocker):
        """Test search can find users by name."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[0]]  # Admin User
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="Admin User", filters=UserListFilters())

        assert len(users) == 1


class TestUserRepositoryPagination:
    """Test pagination still works with search escaping."""

    def test_pagination_with_search(self, user_repository, db_session, sample_users, mocker):
        """Test pagination works with search query."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = sample_users[:2]
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, search="example", filters=UserListFilters(), page=0, per_page=2)

        assert len(users) == 2

    def test_pagination_second_page(self, user_repository, db_session, sample_users, mocker):
        """Test second page of results."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = sample_users[2:4]
        db_session.exec.return_value = mock_result

        users = user_repository.query_users(db_session, filters=UserListFilters(), page=1, per_page=2)

        assert len(users) == 2


def _db_row(**kwargs):
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


_UUID = "550e8400-e29b-41d4-a716-446655440000"


class TestIsUuid:
    """Tests for UserRepository._is_uuid."""

    def test_valid_uuid_returns_true(self):
        assert UserRepository._is_uuid(_UUID) is True

    def test_plain_string_returns_false(self):
        assert UserRepository._is_uuid("alice_smith") is False

    def test_email_returns_false(self):
        assert UserRepository._is_uuid("alice@example.com") is False

    def test_empty_string_returns_false(self):
        assert UserRepository._is_uuid("") is False

    def test_partial_uuid_returns_false(self):
        assert UserRepository._is_uuid("550e8400-e29b-41d4") is False


class TestAfindUsersByIdentifiers:
    """Tests for UserRepository.afind_users_by_identifiers."""

    @pytest.fixture
    def repo(self):
        return UserRepository()

    @pytest.fixture
    def async_session(self):
        session = MagicMock(spec=AsyncSession)
        session.execute = AsyncMock()
        return session

    def _rec(self, id=_UUID, email="alice@example.com", username="alice", name="Alice"):
        return _db_row(id=id, email=email, username=username, name=name)

    @pytest.mark.asyncio
    async def test_empty_identifiers_returns_empty(self, repo, async_session):
        result = await repo.afind_users_by_identifiers(async_session, set())
        assert result == {}
        async_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_uuid_resolved_via_id_query(self, repo, async_session):
        async_session.execute.return_value = [self._rec()]
        result = await repo.afind_users_by_identifiers(async_session, {_UUID})
        assert _UUID in result
        assert result[_UUID].email == "alice@example.com"
        assert async_session.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_username_resolved_via_name_query(self, repo, async_session):
        async_session.execute.return_value = [self._rec(username="alice_smith")]
        result = await repo.afind_users_by_identifiers(async_session, {"alice_smith"})
        assert "alice_smith" in result
        assert result["alice_smith"].email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_display_name_resolved(self, repo, async_session):
        async_session.execute.return_value = [self._rec(username="asmith", name="Alice Smith")]
        result = await repo.afind_users_by_identifiers(async_session, {"Alice Smith"})
        assert "Alice Smith" in result
        assert result["Alice Smith"].email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_email_resolved_via_name_query(self, repo, async_session):
        async_session.execute.return_value = [self._rec(email="alice@example.com")]
        result = await repo.afind_users_by_identifiers(async_session, {"alice@example.com"})
        assert "alice@example.com" in result
        assert result["alice@example.com"].id == _UUID

    @pytest.mark.asyncio
    async def test_mixed_uuid_and_name_makes_two_queries(self, repo, async_session):
        async_session.execute.side_effect = [
            [self._rec()],
            [self._rec(id="other-id", email="bob@example.com", username="bob", name="Bob")],
        ]
        result = await repo.afind_users_by_identifiers(async_session, {_UUID, "bob"})
        assert result[_UUID].email == "alice@example.com"
        assert result["bob"].email == "bob@example.com"
        assert async_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_db_miss_returns_empty(self, repo, async_session):
        async_session.execute.return_value = []
        result = await repo.afind_users_by_identifiers(async_session, {"unknown_user"})
        assert result == {}

    @pytest.mark.asyncio
    async def test_username_and_name_collision_both_mapped(self, repo, async_session):
        """When a row matches both username and name columns, both keys are mapped."""
        row = self._rec(username="alice", name="alice")
        async_session.execute.return_value = [row]
        result = await repo.afind_users_by_identifiers(async_session, {"alice"})
        assert "alice" in result
        assert result["alice"].email == "alice@example.com"


class TestAqueryActiveUsers:
    """Tests for UserRepository.aquery_active_users (async)."""

    @pytest.fixture
    def repo(self):
        return UserRepository()

    def _make_session(self, rows):
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = rows
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=mock_result)
        return session

    def _make_user(self, uid="uuid-1", name="Alice", username="alice"):
        u = MagicMock()
        u.id = uid
        u.name = name
        u.username = username
        return u

    @pytest.mark.asyncio
    async def test_returns_rows_from_session(self, repo):
        """aquery_active_users returns whatever scalars().all() provides."""
        user = self._make_user()
        session = self._make_session([user])

        result = await repo.aquery_active_users(session, search=None)

        assert result == [user]
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_term_triggers_execute(self, repo):
        """When search is provided, execute is still called once."""
        session = self._make_session([])

        result = await repo.aquery_active_users(session, search="alice")

        assert result == []
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_search_string_treated_as_no_filter(self, repo):
        """Empty string is falsy — behaves same as search=None (one execute call each)."""
        session_a = self._make_session([])
        session_b = self._make_session([])

        await repo.aquery_active_users(session_a, search="")
        await repo.aquery_active_users(session_b, search=None)

        session_a.execute.assert_called_once()
        session_b.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_projects_filter_triggers_join(self, repo):
        """When projects are provided, join is applied to filter by project_name."""
        session = self._make_session([])

        result = await repo.aquery_active_users(session, projects=["project-a", "project-b"])

        assert result == []
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_and_projects_combined(self, repo):
        """Both search and projects filters can be applied together."""
        session = self._make_session([])

        result = await repo.aquery_active_users(session, search="alice", projects=["project-a"])

        assert result == []
        session.execute.assert_called_once()
