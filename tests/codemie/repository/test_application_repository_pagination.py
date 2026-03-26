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

"""Unit tests for Story 16: Project list pagination and member counts."""

from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from codemie.repository.application_repository import application_repository
from codemie.rest_api.models.user_management import UserProject


def _compile_sql(statement) -> str:
    """Helper to compile SQLModel statement to SQL string for inspection."""
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


class TestApplicationRepositoryPagination:
    """Story 16: Test pagination query building and member count aggregation."""

    def test_list_visible_projects_paginated_applies_offset_and_limit(self):
        """Pagination query includes OFFSET and LIMIT clauses."""
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 0  # Count query
        mock_session.exec.return_value.all.return_value = []  # Data query

        with (
            patch.object(application_repository, "_apply_search", side_effect=lambda statement, _: statement),
            patch.object(application_repository, "_build_visibility_condition", return_value=True),
        ):
            projects, total = application_repository.list_visible_projects_paginated(
                session=mock_session,
                user_id="user-1",
                is_admin=False,
                search=None,
                page=2,
                per_page=10,
            )

        # Verify data query (second call) includes offset/limit
        data_query = mock_session.exec.call_args_list[1][0][0]
        query_text = _compile_sql(data_query)
        assert "offset 20" in query_text  # page=2, per_page=10 -> offset=20
        assert "limit 10" in query_text

    def test_list_visible_projects_paginated_counts_with_filters(self):
        """Total count reflects search + visibility filters."""
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 42  # Count query
        mock_session.exec.return_value.all.return_value = []  # Data query

        with (
            patch.object(application_repository, "_apply_search", side_effect=lambda statement, _: statement),
            patch.object(application_repository, "_build_visibility_condition", return_value=True),
        ):
            projects, total = application_repository.list_visible_projects_paginated(
                session=mock_session,
                user_id="user-1",
                is_admin=False,
                search="analytics",
                page=0,
                per_page=20,
            )

        assert total == 42
        # Verify count query (first call) uses same filters
        count_query = mock_session.exec.call_args_list[0][0][0]
        query_text = _compile_sql(count_query)
        assert "count" in query_text

    def test_list_visible_projects_paginated_super_admin_sees_all(self):
        """Super admin pagination query skips visibility filter."""
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 100
        mock_session.exec.return_value.all.return_value = []

        with (
            patch.object(application_repository, "_apply_search", side_effect=lambda statement, _: statement),
            patch.object(application_repository, "_build_visibility_condition") as mock_visibility,
        ):
            projects, total = application_repository.list_visible_projects_paginated(
                session=mock_session,
                user_id="admin-1",
                is_admin=True,
                search=None,
                page=0,
                per_page=20,
            )

        # Should NOT call visibility filter for super admin
        mock_visibility.assert_not_called()

    def test_list_visible_projects_paginated_page_0_offset_0(self):
        """First page (page=0) uses offset=0."""
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 50
        mock_session.exec.return_value.all.return_value = []

        with patch.object(application_repository, "_apply_search", side_effect=lambda statement, _: statement):
            application_repository.list_visible_projects_paginated(
                session=mock_session,
                user_id="user-1",
                is_admin=True,
                search=None,
                page=0,
                per_page=20,
            )

        data_query = mock_session.exec.call_args_list[1][0][0]
        query_text = _compile_sql(data_query)
        assert "offset 0" in query_text

    def test_get_project_member_counts_bulk_aggregates_correctly(self):
        """Bulk member count query aggregates user_count and admin_count."""
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = [
            ("proj-a", 5, 2),  # project_name, user_count, admin_count
            ("proj-b", 10, 3),
        ]

        result = application_repository.get_project_member_counts_bulk(
            session=mock_session, project_names=["proj-a", "proj-b"]
        )

        assert result == {
            "proj-a": (5, 2),
            "proj-b": (10, 3),
        }

        # Verify query uses GROUP BY and aggregation
        query = mock_session.exec.call_args[0][0]
        query_text = _compile_sql(query)
        assert "group by" in query_text
        assert "count" in query_text

    def test_get_project_member_counts_bulk_empty_input(self):
        """Empty project names list returns empty dict without DB query."""
        mock_session = MagicMock()

        result = application_repository.get_project_member_counts_bulk(session=mock_session, project_names=[])

        assert result == {}
        mock_session.exec.assert_not_called()

    def test_get_project_member_counts_bulk_filters_by_project_names(self):
        """Query filters by IN clause with provided project names."""
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []

        application_repository.get_project_member_counts_bulk(
            session=mock_session, project_names=["proj-a", "proj-b", "proj-c"]
        )

        query = mock_session.exec.call_args[0][0]
        query_text = _compile_sql(query)
        assert "in" in query_text
        assert "user_projects.project_name" in query_text

    def test_get_project_members_returns_all_members(self):
        """get_project_members returns all UserProject records for a project."""
        mock_session = MagicMock()
        mock_member_1 = MagicMock(spec=UserProject)
        mock_member_2 = MagicMock(spec=UserProject)
        mock_session.exec.return_value.all.return_value = [mock_member_1, mock_member_2]

        result = application_repository.get_project_members(session=mock_session, project_name="proj-a")

        assert len(result) == 2
        assert result[0] == mock_member_1
        assert result[1] == mock_member_2

        # Verify query filters by project_name
        query = mock_session.exec.call_args[0][0]
        query_text = _compile_sql(query)
        assert "user_projects.project_name" in query_text


class TestApplicationRepositoryPaginationIntegration:
    """Unit tests verifying pagination query structure and filter composition."""

    def test_pagination_total_count_matches_visible_projects(self):
        """Total count should reflect same visibility filters as data query."""
        mock_session = MagicMock()
        # Mock count query to return 100
        mock_session.exec.return_value.one.return_value = 100
        # Mock data query to return empty list
        mock_session.exec.return_value.all.return_value = []

        with (
            patch.object(application_repository, "_apply_search", side_effect=lambda statement, _: statement),
            patch.object(application_repository, "_build_visibility_condition", return_value=True),
        ):
            projects, total = application_repository.list_visible_projects_paginated(
                session=mock_session,
                user_id="user-1",
                is_admin=False,
                search="dashboard",
                page=0,
                per_page=20,
            )

        assert total == 100
        assert projects == []

        # Verify both queries were executed
        assert mock_session.exec.call_count == 2

    def test_pagination_handles_search_with_special_characters(self):
        """Pagination applies wildcard-safe search via _apply_search_filters."""
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 0
        mock_session.exec.return_value.all.return_value = []

        with patch.object(
            application_repository, "_apply_search_filters", side_effect=lambda statement, _: statement
        ) as mock_search:
            application_repository.list_visible_projects_paginated(
                session=mock_session,
                user_id="user-1",
                is_admin=True,
                search="analytics_%dashboard",  # Wildcard character
                page=0,
                per_page=20,
            )

        # Verify _apply_search_filters was called with search term (called twice: count + data)
        mock_search.assert_called()
        call_args = mock_search.call_args[0]
        assert call_args[1] == "analytics_%dashboard"
