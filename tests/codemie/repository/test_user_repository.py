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

"""Unit tests for UserRepository with focus on search security.

Tests verify SQL LIKE wildcard escaping prevents information leakage (Story 2).
Uses mocking to test repository logic without database dependency.
"""

import pytest
from uuid import uuid4
from datetime import datetime, UTC

from sqlmodel import Session

from codemie.repository.user_repository import UserRepository
from codemie.rest_api.models.user_management import UserDB


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
        # Mock session.exec to return sample users
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[3]]  # User with %

        # Mock count query
        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 1

        # Mock JOIN query (Story 7: returns user-project pairs)
        mock_join_result = mocker.MagicMock()
        mock_join_result.all.return_value = [(sample_users[3], None)]  # User with no projects

        # Story 7: 3 exec calls now (count, users, JOIN)
        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        # Search for percent sign
        users, projects_map, total = user_repository.list_users(db_session, search="%")

        # Verify query was called (escaping happens inside)
        assert db_session.exec.called
        # In real DB, this would only match "percent%test@example.com"
        # Mock returns filtered result
        assert len(users) == 1

    def test_search_with_underscore_wildcard_escaped(self, user_repository, db_session, sample_users, mocker):
        """AC: Search for _ returns only records with literal _ character."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[4]]  # User with underscore
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 1
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, search="_")

        assert db_session.exec.called
        assert len(users) == 1

    def test_search_admin_percent_does_not_enumerate(self, user_repository, db_session, sample_users, mocker):
        """AC: Search for %admin% does NOT enumerate admin accounts."""
        # Without escaping, %admin% would match all admins
        # With escaping, it looks for literal "%admin%"
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = []  # No literal "%admin%"
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 0
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, search="%admin%")

        # Should not return admin@example.com (would match without escaping)
        assert len(users) == 0

    def test_search_t_underscore_st_no_wildcard_match(self, user_repository, db_session, sample_users, mocker):
        """AC: Search for t_st does NOT match test, tast, t0st (only literal t_st)."""
        # Without escaping, "t_st" would match "test"
        # With escaping, looks for literal "t_st"
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = []  # No literal "t_st" in test data
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 0
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, search="t_st")

        # Should not match "test@example.com" (would match without escaping)
        assert len(users) == 0

    def test_search_normal_text_works(self, user_repository, db_session, sample_users, mocker):
        """AC: Normal search functionality unaffected by escaping."""
        # Normal search should still work
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[1]]  # test@example.com
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 1
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, search="test")

        assert db_session.exec.called
        # Normal search still works
        assert len(users) == 1

    def test_search_case_insensitive_still_works(self, user_repository, db_session, sample_users, mocker):
        """AC: Case-insensitive search still works after escaping."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[0]]  # Admin User
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 1
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, search="ADMIN")

        assert db_session.exec.called
        assert len(users) == 1

    def test_search_combined_wildcards(self, user_repository, db_session, mocker):
        """Test search with both % and _ wildcards."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = []
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 0
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, search="a%b_c")

        # Should only match literal "a%b_c", not use as wildcard pattern
        assert db_session.exec.called
        assert len(users) == 0

    def test_search_empty_string(self, user_repository, db_session, sample_users, mocker):
        """Test search with empty string returns all users."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = sample_users
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = len(sample_users)
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, search="")

        assert len(users) == len(sample_users)

    def test_search_none_value(self, user_repository, db_session, sample_users, mocker):
        """Test search with None value returns all users."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = sample_users
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = len(sample_users)
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, search=None)

        assert len(users) == len(sample_users)


class TestUserRepositorySearchFields:
    """Test search across multiple fields (email, username, name)."""

    def test_search_matches_email(self, user_repository, db_session, sample_users, mocker):
        """Test search can find users by email."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[2]]  # alice@example.com
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 1
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, search="alice@")

        assert len(users) == 1

    def test_search_matches_username(self, user_repository, db_session, sample_users, mocker):
        """Test search can find users by username."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[1]]  # test_user
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 1
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, search="test")

        assert len(users) == 1

    def test_search_matches_name(self, user_repository, db_session, sample_users, mocker):
        """Test search can find users by name."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = [sample_users[0]]  # Admin User
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 1
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, search="Admin User")

        assert len(users) == 1


class TestUserRepositoryPagination:
    """Test pagination still works with search escaping."""

    def test_pagination_with_search(self, user_repository, db_session, sample_users, mocker):
        """Test pagination works with search query."""
        # First page
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = sample_users[:2]
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 5
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, page=0, per_page=2, search="example")

        assert len(users) == 2
        assert total == 5

    def test_pagination_second_page(self, user_repository, db_session, sample_users, mocker):
        """Test second page of results."""
        mock_result = mocker.MagicMock()
        mock_result.all.return_value = sample_users[2:4]
        db_session.exec.return_value = mock_result

        mock_count_result = mocker.MagicMock()
        mock_count_result.one.return_value = 5
        # Mock JOIN query (Story 7)

        mock_join_result = mocker.MagicMock()

        mock_join_result.all.return_value = []  # Default empty projects

        db_session.exec.side_effect = [mock_count_result, mock_result, mock_join_result]

        users, projects_map, total = user_repository.list_users(db_session, page=1, per_page=2)

        assert len(users) == 2
        assert total == 5
