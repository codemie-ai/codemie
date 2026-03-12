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

"""Tests for CategoryRepository."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from codemie.repository.category_repository import CategoryRepository
from codemie.rest_api.models.category import Category


@pytest.fixture
def mock_categories():
    """Mock category data."""
    return [
        Category(
            id="engineering",
            name="Engineering",
            description="Software engineering",
            date=datetime(2024, 1, 1, tzinfo=UTC),
            update_date=datetime(2024, 1, 1, tzinfo=UTC),
        ),
        Category(
            id="data-analytics",
            name="Data Analytics",
            description="Data analysis",
            date=datetime(2024, 1, 2, tzinfo=UTC),
            update_date=datetime(2024, 1, 2, tzinfo=UTC),
        ),
        Category(
            id="business-analysis",
            name="Business Analysis",
            description="Business analysis",
            date=datetime(2024, 1, 3, tzinfo=UTC),
            update_date=datetime(2024, 1, 3, tzinfo=UTC),
        ),
    ]


class TestCategoryRepositoryHelperMethods:
    """Tests for helper methods that build queries."""

    def test_build_counts_subquery_structure(self):
        """Test that _build_counts_subquery creates correct query structure."""
        from codemie.rest_api.models.assistant import Assistant

        subquery_statement = CategoryRepository._build_counts_subquery(Assistant)

        # Verify it's a select statement
        assert hasattr(subquery_statement, "selected_columns")

        # Convert to SQL string to inspect
        sql_str = str(subquery_statement.compile(compile_kwargs={"literal_binds": True}))

        # Verify it selects from assistants table
        assert "assistants" in sql_str.lower()

        # Verify it uses jsonb_array_elements_text
        assert "jsonb_array_elements_text" in sql_str.lower()

        # Verify it selects categories column
        assert "categories" in sql_str.lower()

        # Verify it selects is_global column
        assert "is_global" in sql_str.lower()

    def test_aggregate_counts_structure(self):
        """Test that _aggregate_counts creates correct aggregation query."""
        from codemie.rest_api.models.assistant import Assistant

        # Create a subquery first
        subquery_statement = CategoryRepository._build_counts_subquery(Assistant)
        subquery = subquery_statement.subquery()

        # Build aggregation query
        agg_query = CategoryRepository._aggregate_counts(subquery)

        # Convert to SQL string
        sql_str = str(agg_query.compile(compile_kwargs={"literal_binds": True}))

        # Verify it has SUM functions
        assert sql_str.lower().count("sum") >= 2  # At least 2 SUMs

        # Verify it has CASE statements for conditional counting
        assert "case" in sql_str.lower()

        # Verify it groups by category_id
        assert "group by" in sql_str.lower()
        assert "category_id" in sql_str.lower()


class TestCategoryRepositoryNameSort:
    """Tests for name-based sorting query logic."""

    @patch("codemie.repository.category_repository.Session")
    def test_name_sort_executes_two_queries(self, mock_session_cls, mock_categories):
        """Test that name sort executes pagination query then counts query."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Setup mocks
        mock_session.exec.return_value.one.return_value = 3
        paginated_cats = [mock_categories[0], mock_categories[1]]
        counts_results = [("engineering", 2, 1), ("data-analytics", 1, 0)]
        mock_session.exec.return_value.all.side_effect = [paginated_cats, counts_results]

        # Execute
        CategoryRepository.query(page=0, per_page=2)

        # Verify session.exec was called 3 times (1 for count, 2 for data)
        assert mock_session.exec.call_count == 3

    @patch("codemie.repository.category_repository.Session")
    def test_name_sort_pagination_applied(self, mock_session_cls, mock_categories):
        """Test that OFFSET and LIMIT are correctly applied in name sort."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Setup mocks
        mock_session.exec.return_value.one.return_value = 10
        mock_session.exec.return_value.all.side_effect = [[mock_categories[0]], [("engineering", 2, 1)]]

        # Execute with specific pagination
        CategoryRepository.query(page=2, per_page=5)

        # Get the second call (first query after count) - the pagination query
        pagination_call = mock_session.exec.call_args_list[1]
        query = pagination_call[0][0]
        sql_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        # Verify OFFSET and LIMIT
        assert "limit" in sql_str.lower() or "5" in sql_str  # LIMIT 5
        assert "offset" in sql_str.lower() or "10" in sql_str  # OFFSET 10 (page 2 * per_page 5)

    @patch("codemie.repository.category_repository.Session")
    def test_name_sort_filters_counts_by_category_ids(self, mock_session_cls, mock_categories):
        """Test that counts query filters by the paginated category IDs."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Setup mocks
        mock_session.exec.return_value.one.return_value = 3
        paginated_cats = [mock_categories[0], mock_categories[1]]  # engineering, data-analytics
        mock_session.exec.return_value.all.side_effect = [paginated_cats, []]

        # Execute
        CategoryRepository.query(page=0, per_page=2)

        # Get the third call - the counts query
        counts_call = mock_session.exec.call_args_list[2]
        query = counts_call[0][0]
        sql_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        # Verify WHERE clause with IN
        assert "where" in sql_str.lower()
        assert "in" in sql_str.lower()

    @patch("codemie.repository.category_repository.Session")
    def test_name_sort_combines_categories_with_counts(self, mock_session_cls, mock_categories):
        """Test that name sort correctly combines categories with their counts."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Setup mocks
        mock_session.exec.return_value.one.return_value = 3
        paginated_cats = [mock_categories[0], mock_categories[1]]
        counts_results = [("engineering", 5, 3), ("data-analytics", 2, 1)]
        mock_session.exec.return_value.all.side_effect = [paginated_cats, counts_results]

        # Execute
        result = CategoryRepository.query(page=0, per_page=2)

        # Verify data combination
        assert len(result["categories"]) == 2
        assert result["categories"][0]["id"] == "engineering"
        assert result["categories"][0]["marketplace_assistants_count"] == 5
        assert result["categories"][0]["project_assistants_count"] == 3
        assert result["categories"][1]["id"] == "data-analytics"
        assert result["categories"][1]["marketplace_assistants_count"] == 2
        assert result["categories"][1]["project_assistants_count"] == 1

    @patch("codemie.repository.category_repository.Session")
    def test_name_sort_defaults_missing_counts_to_zero(self, mock_session_cls, mock_categories):
        """Test that categories without counts get 0 for both marketplace and project."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Setup mocks
        mock_session.exec.return_value.one.return_value = 2
        paginated_cats = [mock_categories[0], mock_categories[1]]
        # Only engineering has counts
        counts_results = [("engineering", 5, 3)]
        mock_session.exec.return_value.all.side_effect = [paginated_cats, counts_results]

        # Execute
        result = CategoryRepository.query(page=0, per_page=2)

        # Verify data-analytics gets 0 counts
        assert result["categories"][1]["id"] == "data-analytics"
        assert result["categories"][1]["marketplace_assistants_count"] == 0
        assert result["categories"][1]["project_assistants_count"] == 0


class TestCategoryRepositoryPaginationLogic:
    """Tests for pagination calculation and metadata."""

    @pytest.mark.parametrize(
        "total,per_page,expected_pages",
        [
            (0, 10, 0),  # No items
            (5, 10, 1),  # Less than one page
            (10, 10, 1),  # Exactly one page
            (11, 10, 2),  # Just over one page
            (100, 10, 10),  # Exact multiple
            (105, 10, 11),  # Not exact multiple
            (5, 0, 1),  # Division by zero edge case
        ],
    )
    @patch("codemie.repository.category_repository.Session")
    def test_pages_calculation(self, mock_session_cls, total, per_page, expected_pages):
        """Test that pages are calculated correctly for different scenarios."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Setup mocks
        mock_session.exec.return_value.one.return_value = total
        mock_session.exec.return_value.all.return_value = []

        # Execute
        result = CategoryRepository.query(page=0, per_page=per_page)

        # Verify pages calculation
        assert result["pages"] == expected_pages
        assert result["total"] == total
        assert result["per_page"] == per_page

    @pytest.mark.parametrize("page", [0, 1, 5, 100])
    @patch("codemie.repository.category_repository.Session")
    def test_page_number_preserved_in_result(self, mock_session_cls, page):
        """Test that requested page number is preserved in result metadata."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Setup mocks
        mock_session.exec.return_value.one.return_value = 10
        mock_session.exec.return_value.all.return_value = []

        # Execute
        result = CategoryRepository.query(page=page, per_page=10)

        # Verify page is preserved
        assert result["page"] == page


class TestCategoryRepositoryDefaultBehavior:
    """Tests for default parameter behavior."""

    @patch("codemie.repository.category_repository.Session")
    def test_default_order_by_is_name(self, mock_session_cls, mock_categories):
        """Test that when order_by is None, it defaults to NAME sorting."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Setup mocks
        mock_session.exec.return_value.one.return_value = 1
        mock_session.exec.return_value.all.side_effect = [[mock_categories[0]], [("engineering", 2, 1)]]

        # Execute
        CategoryRepository.query(page=0, per_page=10)

        # Verify it executed multiple queries (name sort pattern: count + categories + counts)
        assert mock_session.exec.call_count == 3  # Name sort executes 3 queries


class TestCategoryRepositoryEmptyResults:
    """Tests for empty result handling."""

    @patch("codemie.repository.category_repository.Session")
    def test_empty_database_returns_empty_list(self, mock_session_cls):
        """Test that empty database returns empty results correctly."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Setup mocks for empty database
        mock_session.exec.return_value.one.return_value = 0
        mock_session.exec.return_value.all.return_value = []

        # Execute
        result = CategoryRepository.query(page=0, per_page=10)

        # Verify empty results
        assert result["categories"] == []
        assert result["total"] == 0
        assert result["pages"] == 0

    @patch("codemie.repository.category_repository.Session")
    def test_name_sort_empty_page_returns_empty_list(self, mock_session_cls):
        """Test that requesting page beyond available data returns empty list."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Setup mocks
        mock_session.exec.return_value.one.return_value = 5  # 5 total items
        mock_session.exec.return_value.all.return_value = []  # But page 100 has no items

        # Execute
        result = CategoryRepository.query(page=100, per_page=10)

        # Verify empty results but correct metadata
        assert result["categories"] == []
        assert result["total"] == 5
        assert result["page"] == 100
