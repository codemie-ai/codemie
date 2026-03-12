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

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from sqlalchemy.exc import IntegrityError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.category import Category
from codemie.service.assistant.category_service import DatabaseCategoryService


@pytest.fixture
def category_service():
    return DatabaseCategoryService()


@pytest.fixture
def mock_session():
    """Mock SQLModel Session for database operations."""
    session = MagicMock()
    return session


@pytest.fixture
def mock_category_engine():
    """Mock Category.get_engine() to return a mock engine."""
    with patch('codemie.rest_api.models.category.Category.get_engine') as mock_engine:
        yield mock_engine


@pytest.fixture
def sample_categories():
    """Sample category data for testing."""
    return [
        Category(id="engineering", name="Engineering", description="Engineering category"),
        Category(id="data-analytics", name="Data Analytics", description="Data analytics category"),
        Category(id="business-analysis", name="Business Analysis", description="Business analysis category"),
    ]


class TestGetCategories:
    """Tests for get_categories method."""

    @patch('codemie.service.assistant.category_service.Session')
    def test_get_categories_returns_all(self, mock_session_cls, category_service, sample_categories):
        """Test that get_categories returns all categories sorted by name."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = sample_categories

        result = category_service.get_categories()

        assert len(result) == 3
        assert all(isinstance(cat, Category) for cat in result)

    @patch('codemie.service.assistant.category_service.Session')
    def test_get_categories_empty(self, mock_session_cls, category_service):
        """Test that get_categories handles empty database."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = []

        result = category_service.get_categories()

        assert result == []


class TestValidateCategoryIds:
    """Tests for validate_category_ids method."""

    @patch.object(DatabaseCategoryService, '_get_categories_by_ids')
    def test_validate_all_valid_ids(self, mock_get_cats, category_service, sample_categories):
        """Test validation with all valid category IDs."""
        mock_get_cats.return_value = sample_categories
        category_ids = ["engineering", "data-analytics", "business-analysis"]

        result = category_service.validate_category_ids(category_ids)

        assert result == category_ids

    @patch.object(DatabaseCategoryService, '_get_categories_by_ids')
    def test_validate_with_invalid_ids(self, mock_get_cats, category_service):
        """Test validation raises ValueError for invalid IDs."""
        mock_get_cats.return_value = [Category(id="engineering", name="Engineering", description="Test")]
        category_ids = ["engineering", "invalid-id"]

        with pytest.raises(ValueError) as exc:
            category_service.validate_category_ids(category_ids)

        assert "Invalid category IDs" in str(exc.value)
        assert "invalid-id" in str(exc.value)

    def test_validate_empty_list(self, category_service):
        """Test validation with empty list returns empty list."""
        result = category_service.validate_category_ids([])
        assert result == []


class TestFilterValidCategoryIds:
    """Tests for filter_valid_category_ids method."""

    @patch.object(DatabaseCategoryService, '_get_categories_by_ids')
    def test_filter_all_valid(self, mock_get_cats, category_service, sample_categories):
        """Test filtering with all valid IDs returns all."""
        mock_get_cats.return_value = sample_categories
        category_ids = ["engineering", "data-analytics", "business-analysis"]

        result = category_service.filter_valid_category_ids(category_ids)

        assert result == category_ids

    @patch.object(DatabaseCategoryService, '_get_categories_by_ids')
    def test_filter_with_invalid_ids(self, mock_get_cats, category_service):
        """Test filtering removes invalid IDs."""
        mock_get_cats.return_value = [Category(id="engineering", name="Engineering", description="Test")]
        category_ids = ["engineering", "invalid-id", "another-invalid"]

        result = category_service.filter_valid_category_ids(category_ids)

        assert result == ["engineering"]

    @patch.object(DatabaseCategoryService, '_get_categories_by_ids')
    def test_filter_preserves_order(self, mock_get_cats, category_service):
        """Test that filtering preserves original order."""
        mock_get_cats.return_value = [
            Category(id="data-analytics", name="Data Analytics", description="Test"),
            Category(id="engineering", name="Engineering", description="Test"),
        ]
        category_ids = ["data-analytics", "invalid", "engineering"]

        result = category_service.filter_valid_category_ids(category_ids)

        assert result == ["data-analytics", "engineering"]

    def test_filter_empty_list(self, category_service):
        """Test filtering empty list returns empty list."""
        result = category_service.filter_valid_category_ids([])
        assert result == []

    @patch.object(DatabaseCategoryService, '_get_categories_by_ids')
    def test_filter_handles_exceptions(self, mock_get_cats, category_service):
        """Test that exceptions are caught and empty list returned."""
        mock_get_cats.side_effect = Exception("Database error")

        result = category_service.filter_valid_category_ids(["engineering"])

        assert result == []


class TestEnrichCategories:
    """Tests for enrich_categories method."""

    @patch.object(DatabaseCategoryService, '_get_categories_by_ids')
    def test_enrich_categories_success(self, mock_get_cats, category_service, sample_categories):
        """Test enriching category IDs with full objects."""
        mock_get_cats.return_value = sample_categories
        category_ids = ["engineering", "data-analytics", "business-analysis"]

        result = category_service.enrich_categories(category_ids)

        assert len(result) == 3
        assert all(isinstance(cat, Category) for cat in result)

    @patch.object(DatabaseCategoryService, '_get_categories_by_ids')
    def test_enrich_preserves_order(self, mock_get_cats, category_service):
        """Test that enrichment preserves input order."""
        categories = [
            Category(id="data-analytics", name="Data Analytics", description="Test"),
            Category(id="engineering", name="Engineering", description="Test"),
        ]
        mock_get_cats.return_value = categories
        category_ids = ["data-analytics", "engineering"]

        result = category_service.enrich_categories(category_ids)

        assert [cat.id for cat in result] == category_ids

    @patch.object(DatabaseCategoryService, '_get_categories_by_ids')
    def test_enrich_skips_missing(self, mock_get_cats, category_service):
        """Test that missing categories are skipped gracefully."""
        mock_get_cats.return_value = [Category(id="engineering", name="Engineering", description="Test")]
        category_ids = ["engineering", "nonexistent"]

        result = category_service.enrich_categories(category_ids)

        assert len(result) == 1
        assert result[0].id == "engineering"

    def test_enrich_empty_list(self, category_service):
        """Test enriching empty list returns empty list."""
        result = category_service.enrich_categories([])
        assert result == []

    @patch.object(DatabaseCategoryService, '_get_categories_by_ids')
    def test_enrich_handles_exceptions(self, mock_get_cats, category_service):
        """Test that exceptions are caught and empty list returned."""
        mock_get_cats.side_effect = Exception("Database error")

        result = category_service.enrich_categories(["engineering"])

        assert result == []


class TestCreateCategory:
    """Tests for create_category method."""

    @patch('codemie.rest_api.models.base.Session')
    @patch('codemie.rest_api.models.category.Category.get_engine')
    def test_create_category_success(self, mock_get_engine, mock_session_cls, category_service):
        """Test successful category creation."""
        # Mock the database session to avoid actual database operations
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session_cls.return_value.__exit__.return_value = None
        mock_get_engine.return_value = MagicMock()

        result = category_service.create_category("Engineering", "Test description")

        assert result.name == "Engineering"
        assert result.description == "Test description"
        assert result.id is not None  # ID should be set by save()
        assert isinstance(result.id, str)  # ID should be a string
        # Verify it's a valid UUID format
        try:
            uuid.UUID(result.id)
        except ValueError:
            pytest.fail(f"Expected ID to be a valid UUID, got: {result.id}")
        # Verify session operations were called
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @patch('codemie.rest_api.models.category.Category.save')
    def test_create_category_duplicate_name(self, mock_save, category_service):
        """Test that duplicate name raises 409 Conflict."""
        from psycopg2.errors import UniqueViolation

        # Create a proper psycopg2 UniqueViolation error as orig
        # This mimics what psycopg2 would actually raise
        orig_error = UniqueViolation('duplicate key value violates unique constraint "categories_name_key"')

        # Wrap it in SQLAlchemy's IntegrityError
        integrity_error = IntegrityError(
            statement="INSERT INTO categories (name, description) VALUES (%(name)s, %(description)s)",
            params={'name': 'Engineering', 'description': 'Test description'},
            orig=orig_error,
        )
        mock_save.side_effect = integrity_error

        with pytest.raises(ExtendedHTTPException) as exc:
            category_service.create_category("Engineering", "Test description")

        assert exc.value.code == status.HTTP_409_CONFLICT
        assert "already exists" in exc.value.message


class TestUpdateCategory:
    """Tests for update_category method."""

    @patch('codemie.rest_api.models.category.Category.find_by_id')
    @patch('codemie.rest_api.models.category.Category.update')
    def test_update_category_success(self, mock_update, mock_find, category_service):
        """Test successful category update."""
        existing_category = Category(id="engineering", name="Engineering", description="Old description")
        mock_find.return_value = existing_category

        result = category_service.update_category("engineering", "Engineering Updated", "New description")

        # Verify the category fields were updated
        assert result.name == "Engineering Updated"
        assert result.description == "New description"
        # Verify the same object was returned
        assert result is existing_category
        # Verify update() was called to persist changes
        mock_update.assert_called_once()

    @patch('codemie.rest_api.models.category.Category.find_by_id')
    def test_update_category_not_found(self, mock_find, category_service):
        """Test updating non-existent category raises 404."""
        mock_find.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc:
            category_service.update_category("nonexistent", "Name", "Description")

        assert exc.value.code == status.HTTP_404_NOT_FOUND


class TestDeleteCategory:
    """Tests for delete_category method."""

    @patch('codemie.service.assistant.category_service.Session')
    def test_delete_category_success(self, mock_session_cls, category_service):
        """Test successful category deletion when no assistants assigned."""
        existing_category = Category(id="engineering", name="Engineering", description="Test")

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Mock the category query result (first exec call)
        mock_category_result = MagicMock()
        mock_category_result.first.return_value = existing_category

        # Mock the count query result (second exec call)
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 0  # No assistants

        # Configure session.exec to return different results for different calls
        mock_session.exec.side_effect = [mock_category_result, mock_count_result]

        category_service.delete_category("engineering")

        # Verify session operations were called
        mock_session.delete.assert_called_once_with(existing_category)
        mock_session.commit.assert_called_once()

    @patch('codemie.service.assistant.category_service.Session')
    def test_delete_category_not_found(self, mock_session_cls, category_service):
        """Test deleting non-existent category raises 404."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Mock the category query to return None (category not found)
        mock_category_result = MagicMock()
        mock_category_result.first.return_value = None
        mock_session.exec.return_value = mock_category_result

        with pytest.raises(ExtendedHTTPException) as exc:
            category_service.delete_category("nonexistent")

        assert exc.value.code == status.HTTP_404_NOT_FOUND

    @patch('codemie.service.assistant.category_service.Session')
    def test_delete_category_with_assistants(self, mock_session_cls, category_service):
        """Test deleting category with assigned assistants raises 409."""
        existing_category = Category(id="engineering", name="Engineering", description="Test")

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Mock the category query result (first exec call)
        mock_category_result = MagicMock()
        mock_category_result.first.return_value = existing_category

        # Mock the count query result (second exec call) - 5 assistants assigned
        mock_count_result = MagicMock()
        mock_count_result.one.return_value = 5

        # Configure session.exec to return different results for different calls
        mock_session.exec.side_effect = [mock_category_result, mock_count_result]

        with pytest.raises(ExtendedHTTPException) as exc:
            category_service.delete_category("engineering")

        assert exc.value.code == status.HTTP_409_CONFLICT
        assert "5 assigned assistants" in exc.value.details


class TestGetCategoryStats:
    """Tests for get_category_stats method."""

    @patch('codemie.service.assistant.category_service.Session')
    def test_get_category_stats(self, mock_session_cls, category_service):
        """Test getting category statistics."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = (10, 5)  # (marketplace_count, project_count)

        result = category_service.get_category_stats("engineering")

        assert result["marketplace_assistants_count"] == 10
        assert result["project_assistants_count"] == 5

    @patch('codemie.service.assistant.category_service.Session')
    def test_get_category_stats_no_assistants(self, mock_session_cls, category_service):
        """Test statistics for category with no assistants."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = (0, 0)

        result = category_service.get_category_stats("engineering")

        assert result["marketplace_assistants_count"] == 0
        assert result["project_assistants_count"] == 0
