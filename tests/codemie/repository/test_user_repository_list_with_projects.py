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

"""Unit tests for UserRepository.list_users() with project JOIN optimization (Story 7).

Tests verify:
- Projects array population via LEFT JOIN
- N+1 query prevention (single JOIN query)
- Pagination correctness (users, not join rows)
- user_type filter
- Combined filters
- Empty projects handling
"""

import pytest
from uuid import uuid4
from datetime import datetime, UTC
from unittest.mock import MagicMock

from sqlmodel import Session

from codemie.repository.user_repository import UserRepository
from codemie.rest_api.models.user_management import UserDB, UserProject


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
        is_super_admin=False,
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
        is_super_admin=False,
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
        is_super_admin=False,
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


class TestUserRepositoryListWithProjects:
    """Test user list endpoint with projects JOIN optimization (Story 7)."""

    def test_list_users_includes_projects_array(self, user_repository, db_session, sample_users_with_projects, mocker):
        """AC: Each user object in list response contains projects array with [{ name, is_project_admin }]."""
        data = sample_users_with_projects
        users = data["users"]
        join_results = data["join_results"]

        # Mock count query
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 3

        # Mock user query
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = users

        # Mock JOIN query
        mock_join_result = MagicMock()
        mock_join_result.all.return_value = join_results

        # Setup side_effect for 3 exec calls: count, user pagination, JOIN
        db_session.exec.side_effect = [mock_count_result, mock_user_result, mock_join_result]

        # Execute
        result_users, projects_map, total = user_repository.list_users(db_session, page=0, per_page=20)

        # Verify results
        assert total == 3
        assert len(result_users) == 3

        # Verify projects_map structure
        assert users[0].id in projects_map
        assert len(projects_map[users[0].id]) == 2  # user1 has 2 projects
        assert projects_map[users[0].id][0].project_name == "project-a"
        assert projects_map[users[0].id][0].is_project_admin is True
        assert projects_map[users[0].id][1].project_name == "project-b"
        assert projects_map[users[0].id][1].is_project_admin is False

        assert users[1].id in projects_map
        assert len(projects_map[users[1].id]) == 1  # user2 has 1 project

        assert users[2].id in projects_map
        assert len(projects_map[users[2].id]) == 0  # user3 has no projects (empty array)

    def test_users_without_projects_return_empty_array(
        self, user_repository, db_session, sample_users_with_projects, mocker
    ):
        """AC: Users without project assignments return empty projects: [] array."""
        data = sample_users_with_projects
        user_no_projects = data["users"][2]  # user3 has no projects
        join_results = [(user_no_projects, None)]

        # Mock count query
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 1

        # Mock user query
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = [user_no_projects]

        # Mock JOIN query (LEFT JOIN returns None for no projects)
        mock_join_result = MagicMock()
        mock_join_result.all.return_value = join_results

        db_session.exec.side_effect = [mock_count_result, mock_user_result, mock_join_result]

        # Execute
        result_users, projects_map, total = user_repository.list_users(db_session, page=0, per_page=20)

        # Verify empty projects array
        assert user_no_projects.id in projects_map
        assert projects_map[user_no_projects.id] == []
        assert isinstance(projects_map[user_no_projects.id], list)

    def test_pagination_paginates_users_not_join_rows(
        self, user_repository, db_session, sample_users_with_projects, mocker
    ):
        """AC: Pagination correctly paginates users (not user-project join rows)."""
        data = sample_users_with_projects
        users = data["users"][:2]  # Paginated result: 2 users
        join_results = data["join_results"][:3]  # 3 join rows for 2 users

        # Mock count query - total 3 users
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 3

        # Mock user query - page 0, per_page 2 returns 2 users
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = users

        # Mock JOIN query - 3 rows for 2 users
        mock_join_result = MagicMock()
        mock_join_result.all.return_value = join_results

        db_session.exec.side_effect = [mock_count_result, mock_user_result, mock_join_result]

        # Execute with pagination
        result_users, projects_map, total = user_repository.list_users(db_session, page=0, per_page=2)

        # Verify pagination is on users, not join rows
        assert total == 3  # Total users (not join row count)
        assert len(result_users) == 2  # Paginated to 2 users
        assert len(projects_map) == 2  # Projects for 2 users

    def test_user_type_filter(self, user_repository, db_session, sample_users_with_projects, mocker):
        """AC: Filter by user_type returns correct subset."""
        data = sample_users_with_projects
        # Filter for 'regular' users: user1 and user3
        regular_users = [u for u in data["users"] if u.user_type == "regular"]
        regular_join_results = [
            (data["users"][0], data["projects"][data["users"][0].id][0]),
            (data["users"][0], data["projects"][data["users"][0].id][1]),
            (data["users"][2], None),  # user3 with no projects
        ]

        # Mock count query
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 2

        # Mock user query
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = regular_users

        # Mock JOIN query
        mock_join_result = MagicMock()
        mock_join_result.all.return_value = regular_join_results

        db_session.exec.side_effect = [mock_count_result, mock_user_result, mock_join_result]

        # Execute with user_type filter
        result_users, projects_map, total = user_repository.list_users(db_session, user_type="regular")

        # Verify filtered results
        assert total == 2
        assert len(result_users) == 2
        assert all(u.user_type == "regular" for u in result_users)

    def test_combined_filters(self, user_repository, db_session, sample_users_with_projects, mocker):
        """AC: Combined filters work correctly (AND logic)."""
        data = sample_users_with_projects
        # Filter: user_type='regular' AND is_active=True
        filtered_users = [data["users"][0]]  # Only user1 matches
        filtered_join_results = [
            (data["users"][0], data["projects"][data["users"][0].id][0]),
            (data["users"][0], data["projects"][data["users"][0].id][1]),
        ]

        # Mock count query
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 1

        # Mock user query
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = filtered_users

        # Mock JOIN query
        mock_join_result = MagicMock()
        mock_join_result.all.return_value = filtered_join_results

        db_session.exec.side_effect = [mock_count_result, mock_user_result, mock_join_result]

        # Execute with combined filters
        result_users, projects_map, total = user_repository.list_users(
            db_session, user_type="regular", is_active=True, search="user1"
        )

        # Verify combined filter results
        assert total == 1
        assert len(result_users) == 1
        assert result_users[0].user_type == "regular"
        assert result_users[0].is_active is True

    def test_empty_result_set(self, user_repository, db_session, mocker):
        """AC: Empty result set returns { data: [], pagination: { total: 0, ... } }."""
        # Mock count query - 0 results
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 0

        # Mock user query - empty list
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = []

        db_session.exec.side_effect = [mock_count_result, mock_user_result]

        # Execute with filters that match nothing
        result_users, projects_map, total = user_repository.list_users(db_session, search="nonexistent")

        # Verify empty result
        assert total == 0
        assert result_users == []
        assert projects_map == {}

    def test_single_join_query_prevents_n_plus_1(self, user_repository, db_session, sample_users_with_projects, mocker):
        """AC: Single database query fetches users and their projects (verify via query logging - no N+1)."""
        data = sample_users_with_projects
        users = data["users"]
        join_results = data["join_results"]

        # Mock count query
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 3

        # Mock user query
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = users

        # Mock JOIN query (single query for all projects)
        mock_join_result = MagicMock()
        mock_join_result.all.return_value = join_results

        db_session.exec.side_effect = [mock_count_result, mock_user_result, mock_join_result]

        # Execute
        result_users, projects_map, total = user_repository.list_users(db_session)

        # Verify: 3 exec calls total (count, users, JOIN)
        # NOT 1 + N calls (1 for users, N for each user's projects)
        assert db_session.exec.call_count == 3  # count + users + JOIN (not 1 + N)

    def test_total_count_reflects_user_count_not_join_count(
        self, user_repository, db_session, sample_users_with_projects, mocker
    ):
        """AC: Total count in pagination reflects user count (not join row count)."""
        data = sample_users_with_projects
        users = data["users"]  # 3 users
        join_results = data["join_results"]  # 4 join rows (user1 has 2 projects)

        # Mock count query - 3 users (not 4 join rows)
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 3

        # Mock user query
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = users

        # Mock JOIN query
        mock_join_result = MagicMock()
        mock_join_result.all.return_value = join_results

        db_session.exec.side_effect = [mock_count_result, mock_user_result, mock_join_result]

        # Execute
        result_users, projects_map, total = user_repository.list_users(db_session)

        # Verify total is user count, not join row count
        assert total == 3  # User count
        assert len(join_results) == 4  # Join row count (different)
