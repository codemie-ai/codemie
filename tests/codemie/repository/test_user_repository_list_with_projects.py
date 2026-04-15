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

"""Unit tests for UserRepository primitives: count_users, query_users, fetch_projects_map.

Tests verify:
- fetch_projects_map: Projects array population via LEFT JOIN (Story 7)
- fetch_projects_map: N+1 prevention (single JOIN query for all users)
- count_users / query_users: Pagination correctness
- _apply_filters: user_type, platform_role, projects, search filters
- _apply_platform_role_filter: ADMIN, PLATFORM_ADMIN, USER branches
"""

import pytest
from uuid import uuid4
from datetime import datetime, UTC
from unittest.mock import MagicMock, patch

from sqlmodel import Session

from codemie.repository.user_repository import UserRepository
from codemie.rest_api.models.user_management import UserDB, UserProject, UserListFilters, PlatformRole


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
def sample_users_with_projects():
    """Sample users and projects for testing JOIN logic."""
    user1 = UserDB(
        id=str(uuid4()),
        email="user1@example.com",
        username="user1",
        name="User One",
        user_type="regular",
        is_active=True,
        is_admin=False,
        auth_source="local",
        email_verified=True,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )
    user2 = UserDB(
        id=str(uuid4()),
        email="user2@example.com",
        username="user2",
        name="User Two",
        user_type="external",
        is_active=True,
        is_admin=False,
        auth_source="keycloak",
        email_verified=True,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )
    user3 = UserDB(
        id=str(uuid4()),
        email="user3@example.com",
        username="user3",
        name="User Three",
        user_type="regular",
        is_active=True,
        is_admin=False,
        auth_source="local",
        email_verified=True,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )

    # Projects for users
    project1_user1 = UserProject(
        id=str(uuid4()), user_id=user1.id, project_name="project-a", is_project_admin=True, date=datetime.now(UTC)
    )
    project2_user1 = UserProject(
        id=str(uuid4()), user_id=user1.id, project_name="project-b", is_project_admin=False, date=datetime.now(UTC)
    )
    project1_user2 = UserProject(
        id=str(uuid4()), user_id=user2.id, project_name="project-a", is_project_admin=False, date=datetime.now(UTC)
    )
    # user3 has no projects

    return {
        "users": [user1, user2, user3],
        "projects": {
            user1.id: [project1_user1, project2_user1],
            user2.id: [project1_user2],
            user3.id: [],
        },
        "join_results": [
            (user1, project1_user1),
            (user1, project2_user1),
            (user2, project1_user2),
            (user3, None),  # LEFT JOIN returns None for users without projects
        ],
    }


# ===========================================
# fetch_projects_map tests
# ===========================================


class TestFetchProjectsMap:
    """Test UserRepository.fetch_projects_map() — LEFT JOIN optimization (Story 7)."""

    def test_builds_projects_map_from_join_rows(self, user_repository, db_session, sample_users_with_projects):
        """fetch_projects_map correctly groups projects by user_id from JOIN rows."""
        data = sample_users_with_projects
        join_results = data["join_results"]

        mock_join_result = MagicMock()
        mock_join_result.all.return_value = join_results
        db_session.exec.return_value = mock_join_result

        user_ids = [u.id for u in data["users"]]
        projects_map = user_repository.fetch_projects_map(db_session, user_ids)

        assert len(projects_map[data["users"][0].id]) == 2
        assert projects_map[data["users"][0].id][0].project_name == "project-a"
        assert projects_map[data["users"][0].id][0].is_project_admin is True
        assert projects_map[data["users"][0].id][1].project_name == "project-b"
        assert projects_map[data["users"][0].id][1].is_project_admin is False

        assert len(projects_map[data["users"][1].id]) == 1
        assert projects_map[data["users"][1].id][0].project_name == "project-a"

    def test_users_without_projects_get_empty_list(self, user_repository, db_session, sample_users_with_projects):
        """Users with no project assignments appear in map with empty list."""
        data = sample_users_with_projects
        user_no_projects = data["users"][2]  # user3 has no projects

        mock_join_result = MagicMock()
        mock_join_result.all.return_value = [(user_no_projects, None)]
        db_session.exec.return_value = mock_join_result

        projects_map = user_repository.fetch_projects_map(db_session, [user_no_projects.id])

        assert user_no_projects.id in projects_map
        assert projects_map[user_no_projects.id] == []
        assert isinstance(projects_map[user_no_projects.id], list)

    def test_single_query_for_all_users_no_n_plus_1(self, user_repository, db_session, sample_users_with_projects):
        """Single exec call fetches projects for all users (no N+1 queries)."""
        data = sample_users_with_projects

        mock_join_result = MagicMock()
        mock_join_result.all.return_value = data["join_results"]
        db_session.exec.return_value = mock_join_result

        user_ids = [u.id for u in data["users"]]
        user_repository.fetch_projects_map(db_session, user_ids)

        # Single exec call — NOT 1 + N calls (one per user)
        assert db_session.exec.call_count == 1

    def test_empty_user_ids_returns_empty_map(self, user_repository, db_session):
        """fetch_projects_map with empty user_ids returns {} without querying DB."""
        projects_map = user_repository.fetch_projects_map(db_session, [])

        assert projects_map == {}
        db_session.exec.assert_not_called()

    def test_mixed_users_projects_and_none(self, user_repository, db_session, sample_users_with_projects):
        """Handles mixed JOIN rows: some with projects, some with None."""
        data = sample_users_with_projects
        join_results = data["join_results"]  # includes user3 → None

        mock_join_result = MagicMock()
        mock_join_result.all.return_value = join_results
        db_session.exec.return_value = mock_join_result

        user_ids = [u.id for u in data["users"]]
        projects_map = user_repository.fetch_projects_map(db_session, user_ids)

        # user1: 2 projects, user2: 1 project, user3: 0 projects
        assert len(projects_map[data["users"][0].id]) == 2
        assert len(projects_map[data["users"][1].id]) == 1
        assert len(projects_map[data["users"][2].id]) == 0


# ===========================================
# count_users tests
# ===========================================


class TestCountUsers:
    """Test UserRepository.count_users()."""

    def test_returns_total_count(self, user_repository, db_session):
        """count_users returns COUNT(*) from single exec call."""
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 42
        db_session.exec.return_value = mock_count_result

        total = user_repository.count_users(db_session)

        assert total == 42
        assert db_session.exec.call_count == 1

    def test_returns_zero_for_empty_table(self, user_repository, db_session):
        """count_users returns 0 when no users match."""
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 0
        db_session.exec.return_value = mock_count_result

        total = user_repository.count_users(db_session, search="nonexistent")

        assert total == 0

    def test_count_reflects_users_not_join_rows(self, user_repository, db_session):
        """count_users counts UserDB rows, not join rows (important for N users with multiple projects)."""
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 3  # 3 users, even if they have 5 total projects
        db_session.exec.return_value = mock_count_result

        total = user_repository.count_users(db_session)

        assert total == 3


# ===========================================
# query_users tests
# ===========================================


class TestQueryUsers:
    """Test UserRepository.query_users()."""

    def test_returns_paginated_users(self, user_repository, db_session, sample_users_with_projects):
        """query_users returns list of UserDB objects."""
        data = sample_users_with_projects
        users = data["users"][:2]

        mock_user_result = MagicMock()
        mock_user_result.all.return_value = users
        db_session.exec.return_value = mock_user_result

        result = user_repository.query_users(db_session, filters=UserListFilters(), page=0, per_page=2)

        assert len(result) == 2
        assert result == users

    def test_returns_empty_list_when_no_matches(self, user_repository, db_session):
        """query_users returns empty list when no users match filters."""
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = []
        db_session.exec.return_value = mock_user_result

        result = user_repository.query_users(db_session, search="no-match", filters=UserListFilters())

        assert result == []

    def test_single_exec_call(self, user_repository, db_session, sample_users_with_projects):
        """query_users makes exactly 1 exec call (no N+1)."""
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = sample_users_with_projects["users"]
        db_session.exec.return_value = mock_user_result

        user_repository.query_users(db_session, filters=UserListFilters())

        assert db_session.exec.call_count == 1


# ===========================================
# _apply_platform_role_filter tests
# ===========================================


class TestApplyPlatformRoleFilter:
    """Test UserRepository._apply_platform_role_filter static method — all 3 branches."""

    def _make_mock_query(self):
        """Return a mock query object that records .where() calls."""
        query = MagicMock()
        query.where.return_value = query
        return query

    def test_admin_filters_by_is_admin_column(self):
        """ADMIN branch: WHERE is_admin = true."""
        query = self._make_mock_query()

        result = UserRepository._apply_platform_role_filter(query, PlatformRole.ADMIN)

        # Should call query.where with UserDB.is_admin
        query.where.assert_called_once()
        assert result is query

    def test_platform_admin_filters_not_admin_and_has_project_admin(self):
        """PLATFORM_ADMIN branch: WHERE NOT admin AND EXISTS project admin membership."""
        query = self._make_mock_query()

        result = UserRepository._apply_platform_role_filter(query, PlatformRole.PLATFORM_ADMIN)

        # Should call query.where with ~is_admin + EXISTS condition
        query.where.assert_called_once()
        args = query.where.call_args[0]
        assert len(args) == 2  # Two conditions: ~admin, EXISTS
        assert result is query

    def test_user_filters_not_admin_and_no_project_admin(self):
        """USER branch: WHERE NOT admin AND NOT EXISTS project admin membership."""
        query = self._make_mock_query()

        result = UserRepository._apply_platform_role_filter(query, PlatformRole.USER)

        # Should call query.where with ~is_admin + ~EXISTS condition
        query.where.assert_called_once()
        args = query.where.call_args[0]
        assert len(args) == 2  # Two conditions: ~admin, ~EXISTS
        assert result is query

    def test_platform_admin_and_user_receive_different_where_clauses(self):
        """PLATFORM_ADMIN and USER receive different WHERE clauses (EXISTS vs ~EXISTS)."""
        query_admin = self._make_mock_query()
        query_user = self._make_mock_query()

        UserRepository._apply_platform_role_filter(query_admin, PlatformRole.PLATFORM_ADMIN)
        UserRepository._apply_platform_role_filter(query_user, PlatformRole.USER)

        args_admin = query_admin.where.call_args[0]
        args_user = query_user.where.call_args[0]

        # Both have 2 args, but 2nd arg differs (EXISTS vs ~EXISTS)
        assert len(args_admin) == 2
        assert len(args_user) == 2
        # The first arg (~is_admin) is the same; second arg is different
        assert str(args_admin[1]) != str(args_user[1])


# ===========================================
# _apply_filters tests
# ===========================================


class TestApplyFilters:
    """Test UserRepository._apply_filters() — search text and structured filters."""

    def _make_mock_query(self):
        query = MagicMock()
        query.where.return_value = query
        return query

    def test_no_filters_returns_query_unchanged(self):
        """With empty filters and no search, only the base deleted_at filter is applied."""
        query = self._make_mock_query()

        result = UserRepository._apply_filters(query, search=None, filters=UserListFilters())

        # Base filter: WHERE deleted_at IS NULL always added
        query.where.assert_called_once()
        assert result is query

    def test_search_adds_ilike_where_clause(self):
        """Non-empty search term adds OR ilike WHERE clause (plus base deleted_at filter)."""
        query = self._make_mock_query()

        result = UserRepository._apply_filters(query, search="alice", filters=UserListFilters())

        # Base deleted_at filter + search ilike filter = 2 calls
        assert query.where.call_count == 2
        assert result is query

    def test_user_type_filter_adds_where_clause(self):
        """user_type filter adds WHERE user_type = value (plus base deleted_at filter)."""
        query = self._make_mock_query()

        result = UserRepository._apply_filters(query, search=None, filters=UserListFilters(user_type="external"))

        # Base deleted_at filter + user_type filter = 2 calls
        assert query.where.call_count == 2
        assert result is query

    def test_projects_filter_adds_exists_where_clause(self):
        """projects filter adds WHERE EXISTS user_projects clause (plus base deleted_at filter)."""
        query = self._make_mock_query()

        result = UserRepository._apply_filters(
            query, search=None, filters=UserListFilters(projects=["proj-a", "proj-b"])
        )

        # Base deleted_at filter + projects exists filter = 2 calls
        assert query.where.call_count == 2
        assert result is query

    @patch.object(UserRepository, "_apply_platform_role_filter")
    def test_platform_role_delegates_to_helper(self, mock_role_filter):
        """platform_role filter delegates to _apply_platform_role_filter."""
        mock_role_filter.return_value = MagicMock()
        query = self._make_mock_query()

        UserRepository._apply_filters(query, search=None, filters=UserListFilters(platform_role=PlatformRole.ADMIN))

        mock_role_filter.assert_called_once_with(query, PlatformRole.ADMIN)

    def test_multiple_filters_chain_where_calls(self):
        """Multiple filters each add a WHERE clause (chained), plus base deleted_at filter."""
        query = self._make_mock_query()

        UserRepository._apply_filters(
            query,
            search="test",
            filters=UserListFilters(user_type="regular", projects=["proj-x"]),
        )

        # Base deleted_at + search + user_type + projects = 4 where calls
        assert query.where.call_count == 4

    def test_empty_search_string_skips_ilike(self):
        """Empty string search is falsy — no ilike clause added, only base deleted_at filter."""
        query = self._make_mock_query()

        UserRepository._apply_filters(query, search="", filters=UserListFilters())

        # Base filter only: WHERE deleted_at IS NULL
        query.where.assert_called_once()

    @patch.object(UserRepository, "_apply_platform_admin_project_filter")
    def test_platform_admin_with_projects_delegates_to_combined_filter(self, mock_combined_filter):
        """platform_admin + projects delegates to _apply_platform_admin_project_filter."""
        mock_combined_filter.return_value = MagicMock()
        query = self._make_mock_query()

        UserRepository._apply_filters(
            query, search=None, filters=UserListFilters(platform_role=PlatformRole.PLATFORM_ADMIN, projects=["proj-a"])
        )

        mock_combined_filter.assert_called_once_with(query, ["proj-a"])

    @patch.object(UserRepository, "_apply_platform_role_filter")
    @patch.object(UserRepository, "_apply_platform_admin_project_filter")
    def test_platform_admin_with_projects_skips_role_filter(self, mock_combined_filter, mock_role_filter):
        """platform_admin + projects does NOT call _apply_platform_role_filter."""
        mock_combined_filter.return_value = MagicMock()
        query = self._make_mock_query()

        UserRepository._apply_filters(
            query, search=None, filters=UserListFilters(platform_role=PlatformRole.PLATFORM_ADMIN, projects=["proj-a"])
        )

        mock_role_filter.assert_not_called()

    @patch.object(UserRepository, "_apply_platform_admin_project_filter")
    @patch.object(UserRepository, "_apply_platform_role_filter")
    def test_platform_admin_without_projects_uses_role_filter(self, mock_role_filter, mock_combined_filter):
        """platform_admin without projects uses _apply_platform_role_filter, not combined filter."""
        mock_role_filter.return_value = MagicMock()
        query = self._make_mock_query()

        UserRepository._apply_filters(
            query, search=None, filters=UserListFilters(platform_role=PlatformRole.PLATFORM_ADMIN)
        )

        mock_role_filter.assert_called_once_with(query, PlatformRole.PLATFORM_ADMIN)
        mock_combined_filter.assert_not_called()


# ===========================================
# _apply_platform_admin_project_filter tests
# ===========================================


class TestApplyPlatformAdminProjectFilter:
    """Test UserRepository._apply_platform_admin_project_filter static method."""

    def _make_mock_query(self):
        query = MagicMock()
        query.where.return_value = query
        return query

    def test_adds_single_where_clause_with_two_conditions(self):
        """Combined filter adds a single WHERE clause with one EXISTS condition (project-scoped)."""
        query = self._make_mock_query()

        result = UserRepository._apply_platform_admin_project_filter(query, ["proj-a", "proj-b"])

        query.where.assert_called_once()
        args = query.where.call_args[0]
        assert len(args) == 1  # EXISTS(is_project_admin AND project_name IN ...)
        assert result is query

    def test_exists_subquery_differs_from_plain_platform_admin_filter(self):
        """Combined filter EXISTS differs from plain _apply_platform_role_filter EXISTS (no project scope)."""
        query_combined = self._make_mock_query()
        query_plain = self._make_mock_query()

        UserRepository._apply_platform_admin_project_filter(query_combined, ["proj-a"])
        UserRepository._apply_platform_role_filter(query_plain, PlatformRole.PLATFORM_ADMIN)

        args_combined = query_combined.where.call_args[0]
        args_plain = query_plain.where.call_args[0]

        assert len(args_combined) == 1  # project-scoped EXISTS only
        assert len(args_plain) == 2  # ~is_admin + EXISTS without project scope
        # The EXISTS subqueries differ: combined is project-scoped, plain is not
        assert str(args_combined[0]) != str(args_plain[1])
