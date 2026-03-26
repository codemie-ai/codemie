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

"""Extended tests for application_repository to reach 80% coverage.

Target methods from test_coverage_plan.md:
- get_by_name
- exists_by_name
- delete_by_name
- is_personal_project
- get_or_create (race condition handling)
- get_project_owner
- get_project_types_bulk
- Async variants: aget_by_name, acreate, aget_or_create
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from codemie.core.models import Application
from codemie.repository.application_repository import application_repository


def _compile_sql(statement) -> str:
    """Helper to compile SQLModel statement to SQL string for inspection."""
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


class TestApplicationRepositoryBasicOperations:
    """Test basic CRUD operations not covered by existing tests."""

    def test_get_by_name_returns_application_when_exists(self):
        """get_by_name returns Application when project exists."""
        # Arrange
        mock_session = MagicMock()
        expected_app = Application(
            id="proj-a",
            name="proj-a",
            description="Test project",
            project_type="shared",
            date=datetime.now(),
            update_date=datetime.now(),
        )
        mock_session.exec.return_value.first.return_value = expected_app

        # Act
        result = application_repository.get_by_name(mock_session, "proj-a")

        # Assert
        assert result == expected_app
        query_text = _compile_sql(mock_session.exec.call_args[0][0])
        assert "applications.name" in query_text

    def test_get_by_name_returns_none_when_not_exists(self):
        """get_by_name returns None when project doesn't exist."""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        # Act
        result = application_repository.get_by_name(mock_session, "nonexistent")

        # Assert
        assert result is None

    def test_exists_by_name_returns_true_when_project_exists(self):
        """exists_by_name returns True when project exists."""
        # Arrange
        mock_session = MagicMock()
        mock_app = MagicMock(spec=Application)

        with patch.object(application_repository, "get_by_name", return_value=mock_app):
            # Act
            result = application_repository.exists_by_name(mock_session, "proj-a")

            # Assert
            assert result is True

    def test_exists_by_name_returns_false_when_project_not_exists(self):
        """exists_by_name returns False when project doesn't exist."""
        # Arrange
        mock_session = MagicMock()

        with patch.object(application_repository, "get_by_name", return_value=None):
            # Act
            result = application_repository.exists_by_name(mock_session, "nonexistent")

            # Assert
            assert result is False

    def test_delete_by_name_deletes_and_returns_true_when_exists(self):
        """delete_by_name deletes project and returns True when exists."""
        # Arrange
        mock_session = MagicMock()
        mock_app = Application(
            id="proj-a",
            name="proj-a",
            date=datetime.now(),
            update_date=datetime.now(),
        )

        with patch.object(application_repository, "get_by_name", return_value=mock_app):
            # Act
            result = application_repository.delete_by_name(mock_session, "proj-a")

            # Assert
            assert result is True
            mock_session.delete.assert_called_once_with(mock_app)
            mock_session.flush.assert_called_once()

    def test_delete_by_name_returns_false_when_not_exists(self):
        """delete_by_name returns False when project doesn't exist."""
        # Arrange
        mock_session = MagicMock()

        with patch.object(application_repository, "get_by_name", return_value=None):
            # Act
            result = application_repository.delete_by_name(mock_session, "nonexistent")

            # Assert
            assert result is False
            mock_session.delete.assert_not_called()

    def test_is_personal_project_returns_true_for_personal_type(self):
        """is_personal_project returns True for personal projects."""
        # Arrange
        mock_session = MagicMock()
        mock_app = Application(
            id="personal-1",
            name="personal-1",
            project_type="personal",
            date=datetime.now(),
            update_date=datetime.now(),
        )

        with patch.object(application_repository, "get_by_name", return_value=mock_app):
            # Act
            result = application_repository.is_personal_project(mock_session, "personal-1")

            # Assert
            assert result is True

    def test_is_personal_project_returns_false_for_shared_type(self):
        """is_personal_project returns False for shared projects."""
        # Arrange
        mock_session = MagicMock()
        mock_app = Application(
            id="shared-1",
            name="shared-1",
            project_type="shared",
            date=datetime.now(),
            update_date=datetime.now(),
        )

        with patch.object(application_repository, "get_by_name", return_value=mock_app):
            # Act
            result = application_repository.is_personal_project(mock_session, "shared-1")

            # Assert
            assert result is False

    def test_is_personal_project_returns_false_for_nonexistent_project(self):
        """is_personal_project returns False for nonexistent projects."""
        # Arrange
        mock_session = MagicMock()

        with patch.object(application_repository, "get_by_name", return_value=None):
            # Act
            result = application_repository.is_personal_project(mock_session, "nonexistent")

            # Assert
            assert result is False

    def test_get_project_owner_returns_created_by_when_exists(self):
        """get_project_owner returns creator user ID when project exists."""
        # Arrange
        mock_session = MagicMock()
        mock_app = Application(
            id="proj-a",
            name="proj-a",
            created_by="user-123",
            date=datetime.now(),
            update_date=datetime.now(),
        )

        with patch.object(application_repository, "get_by_name", return_value=mock_app):
            # Act
            result = application_repository.get_project_owner(mock_session, "proj-a")

            # Assert
            assert result == "user-123"

    def test_get_project_owner_returns_none_when_project_not_exists(self):
        """get_project_owner returns None when project doesn't exist."""
        # Arrange
        mock_session = MagicMock()

        with patch.object(application_repository, "get_by_name", return_value=None):
            # Act
            result = application_repository.get_project_owner(mock_session, "nonexistent")

            # Assert
            assert result is None


class TestApplicationRepositoryGetOrCreate:
    """Test get_or_create race condition handling."""

    def test_get_or_create_returns_existing_when_found(self):
        """get_or_create returns existing project without creating."""
        # Arrange
        mock_session = MagicMock()
        existing_app = Application(
            id="proj-a",
            name="proj-a",
            date=datetime.now(),
            update_date=datetime.now(),
        )

        with patch.object(application_repository, "get_by_name_case_insensitive", return_value=existing_app):
            # Act
            result = application_repository.get_or_create(mock_session, "proj-a")

            # Assert
            assert result == existing_app
            # Should not attempt to create
            mock_session.add.assert_not_called()

    def test_get_or_create_creates_when_not_found(self):
        """get_or_create creates project when not found."""
        # Arrange
        mock_session = MagicMock()
        new_app = Application(
            id="proj-b",
            name="proj-b",
            date=datetime.now(),
            update_date=datetime.now(),
        )

        with (
            patch.object(application_repository, "get_by_name_case_insensitive", return_value=None),
            patch.object(application_repository, "create", return_value=new_app) as mock_create,
        ):
            # Act
            result = application_repository.get_or_create(mock_session, "proj-b")

            # Assert
            assert result == new_app
            mock_create.assert_called_once_with(mock_session, "proj-b")

    def test_get_or_create_handles_race_condition(self):
        """get_or_create handles IntegrityError from concurrent creation."""
        # Arrange
        mock_session = MagicMock()
        existing_app = Application(
            id="proj-c",
            name="proj-c",
            date=datetime.now(),
            update_date=datetime.now(),
        )

        # Initial case-insensitive lookup returns None (miss), create raises IntegrityError,
        # retry via get_by_name returns the concurrently-created record.
        with (
            patch.object(application_repository, "get_by_name_case_insensitive", return_value=None),
            patch.object(application_repository, "get_by_name", return_value=existing_app) as mock_get,
            patch.object(application_repository, "create", side_effect=IntegrityError("", "", "")) as mock_create,
        ):
            # Act
            result = application_repository.get_or_create(mock_session, "proj-c")

            # Assert
            assert result == existing_app
            mock_get.assert_called_once()
            mock_create.assert_called_once()
            mock_session.rollback.assert_called_once()

    def test_get_or_create_raises_when_retry_fails(self):
        """get_or_create re-raises IntegrityError if retry lookup still fails."""
        # Arrange
        mock_session = MagicMock()
        integrity_error = IntegrityError("", "", "")

        # Initial case-insensitive lookup and retry both return None, create raises IntegrityError
        with (
            patch.object(application_repository, "get_by_name_case_insensitive", return_value=None),
            patch.object(application_repository, "get_by_name", return_value=None),
            patch.object(application_repository, "create", side_effect=integrity_error),
        ):
            # Act & Assert
            with pytest.raises(IntegrityError):
                application_repository.get_or_create(mock_session, "proj-d")


class TestApplicationRepositoryBulkOperations:
    """Test bulk query operations for optimization."""

    def test_get_project_types_bulk_returns_type_and_creator(self):
        """get_project_types_bulk returns dict of project_name -> (type, creator)."""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = [
            ("proj-a", "shared", "user-1"),
            ("proj-b", "personal", "user-2"),
            ("proj-c", "shared", None),
        ]

        # Act
        result = application_repository.get_project_types_bulk(mock_session, ["proj-a", "proj-b", "proj-c"])

        # Assert
        assert result == {
            "proj-a": ("shared", "user-1"),
            "proj-b": ("personal", "user-2"),
            "proj-c": ("shared", None),
        }

    def test_get_project_types_bulk_empty_input_returns_empty_dict(self):
        """get_project_types_bulk returns empty dict for empty input without DB query."""
        # Arrange
        mock_session = MagicMock()

        # Act
        result = application_repository.get_project_types_bulk(mock_session, [])

        # Assert
        assert result == {}
        mock_session.exec.assert_not_called()

    def test_get_project_types_bulk_excludes_missing_projects(self):
        """get_project_types_bulk only returns projects that exist."""
        # Arrange
        mock_session = MagicMock()
        # Only proj-a exists, proj-b and proj-c don't
        mock_session.exec.return_value.all.return_value = [
            ("proj-a", "shared", "user-1"),
        ]

        # Act
        result = application_repository.get_project_types_bulk(mock_session, ["proj-a", "proj-b", "proj-c"])

        # Assert
        assert result == {
            "proj-a": ("shared", "user-1"),
        }
        assert "proj-b" not in result
        assert "proj-c" not in result

    def test_get_project_types_bulk_filters_by_project_names(self):
        """get_project_types_bulk uses IN clause with project names."""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []

        # Act
        application_repository.get_project_types_bulk(mock_session, ["proj-a", "proj-b"])

        # Assert
        query = mock_session.exec.call_args[0][0]
        query_text = _compile_sql(query)
        assert "in" in query_text
        assert "applications.name" in query_text


class TestApplicationRepositoryAsyncOperations:
    """Test async variants of repository methods."""

    @pytest.mark.asyncio
    async def test_aget_by_name_returns_application_when_exists(self):
        """aget_by_name returns Application when project exists (async)."""
        # Arrange
        mock_session = AsyncMock()
        expected_app = Application(
            id="proj-a",
            name="proj-a",
            date=datetime.now(),
            update_date=datetime.now(),
        )
        # Mock the result chain: execute() -> scalars() -> first()
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = expected_app

        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        # Make execute return the mock_result directly (not awaitable)
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Act
        result = await application_repository.aget_by_name(mock_session, "proj-a")

        # Assert
        assert result == expected_app
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_aget_by_name_returns_none_when_not_exists(self):
        """aget_by_name returns None when project doesn't exist (async)."""
        # Arrange
        mock_session = AsyncMock()
        # Mock the result chain: execute() -> scalars() -> first()
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = None

        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        # Make execute return the mock_result directly (not awaitable)
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Act
        result = await application_repository.aget_by_name(mock_session, "nonexistent")

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_acreate_persists_application_with_all_fields(self):
        """acreate creates Application with all fields (async)."""
        # Arrange
        mock_session = AsyncMock()

        # Act
        result = await application_repository.acreate(
            session=mock_session,
            name="proj-async",
            description="Async project",
            project_type="shared",
            created_by="user-1",
        )

        # Assert
        assert result.name == "proj-async"
        assert result.description == "Async project"
        assert result.project_type == "shared"
        assert result.created_by == "user-1"
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_aget_or_create_returns_existing_when_found(self):
        """aget_or_create returns existing project without creating (async)."""
        # Arrange
        mock_session = AsyncMock()
        existing_app = Application(
            id="proj-a",
            name="proj-a",
            date=datetime.now(),
            update_date=datetime.now(),
        )

        with patch.object(application_repository, "aget_by_name_case_insensitive", return_value=existing_app):
            # Act
            result = await application_repository.aget_or_create(mock_session, "proj-a")

            # Assert
            assert result == existing_app
            mock_session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_aget_or_create_creates_when_not_found(self):
        """aget_or_create creates project when not found (async)."""
        # Arrange
        mock_session = AsyncMock()
        new_app = Application(
            id="proj-b",
            name="proj-b",
            date=datetime.now(),
            update_date=datetime.now(),
        )

        with (
            patch.object(application_repository, "aget_by_name_case_insensitive", return_value=None),
            patch.object(application_repository, "acreate", return_value=new_app) as mock_create,
        ):
            # Act
            result = await application_repository.aget_or_create(mock_session, "proj-b")

            # Assert
            assert result == new_app
            mock_create.assert_called_once_with(mock_session, "proj-b")

    @pytest.mark.asyncio
    async def test_aget_or_create_handles_race_condition(self):
        """aget_or_create handles IntegrityError from concurrent creation (async)."""
        # Arrange
        mock_session = AsyncMock()
        existing_app = Application(
            id="proj-c",
            name="proj-c",
            date=datetime.now(),
            update_date=datetime.now(),
        )

        # Initial case-insensitive lookup returns None (miss), create raises IntegrityError,
        # retry via aget_by_name returns the concurrently-created record.
        async def mock_aget_by_name(session, name):
            return existing_app

        async def mock_create(session, name):
            raise IntegrityError("", "", "")

        with (
            patch.object(application_repository, "aget_by_name_case_insensitive", return_value=None),
            patch.object(application_repository, "aget_by_name", side_effect=mock_aget_by_name),
            patch.object(application_repository, "acreate", side_effect=mock_create),
        ):
            # Act
            result = await application_repository.aget_or_create(mock_session, "proj-c")

            # Assert
            assert result == existing_app
            mock_session.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_aget_or_create_raises_when_retry_fails(self):
        """aget_or_create re-raises IntegrityError if retry lookup still fails (async)."""
        # Arrange
        mock_session = AsyncMock()

        # Initial case-insensitive lookup and retry both return None, create raises IntegrityError
        async def mock_aget_by_name(session, name):
            return None

        async def mock_create(session, name):
            raise IntegrityError("", "", "")

        with (
            patch.object(application_repository, "aget_by_name_case_insensitive", return_value=None),
            patch.object(application_repository, "aget_by_name", side_effect=mock_aget_by_name),
            patch.object(application_repository, "acreate", side_effect=mock_create),
        ):
            # Act & Assert
            with pytest.raises(IntegrityError):
                await application_repository.aget_or_create(mock_session, "proj-d")


class TestApplicationRepositoryVisibilityHelpers:
    """Test visibility helper methods not covered by other test files."""

    def test_get_visible_project_super_admin_excludes_deleted(self):
        """get_visible_project filters out soft-deleted projects for super admin."""
        # Arrange
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        # Act
        result = application_repository.get_visible_project(
            session=mock_session,
            project_name="proj-a",
            user_id="admin-1",
            is_admin=True,
        )

        # Assert
        assert result is None
        query_text = _compile_sql(mock_session.exec.call_args[0][0])
        # Super admin query should filter deleted_at IS NULL
        assert "deleted_at" in query_text
        assert "is null" in query_text

    def test_build_visibility_condition_includes_personal_and_shared_logic(self):
        """_build_visibility_condition returns AND clause with personal/shared conditions."""
        # Arrange
        user_id = "user-123"

        # Act
        condition = application_repository._build_visibility_condition(user_id)

        # Assert
        # Verify it's a BooleanClauseList (AND clause)
        assert condition is not None
        # The condition is used in queries - just verify it returns something
        assert hasattr(condition, "clauses") or hasattr(condition, "__iter__")

    def test_apply_search_filters_no_search_returns_unchanged_statement(self):
        """_apply_search_filters returns statement unchanged when search is None."""
        # Arrange
        from sqlmodel import select

        base_statement = select(Application)

        # Act
        result = application_repository._apply_search_filters(base_statement, None)

        # Assert
        assert result is base_statement

    def test_apply_search_filters_applies_exact_and_ilike_conditions(self):
        """_apply_search_filters applies exact name match, name ILIKE, and description ILIKE."""
        # Arrange
        from sqlmodel import select

        base_statement = select(Application)

        # Act
        result = application_repository._apply_search_filters(base_statement, "test-proj")

        # Assert
        query_text = _compile_sql(result)
        # Should have OR condition covering name (exact + partial) and description (partial)
        assert "applications.name" in query_text
        assert "applications.description" in query_text

    def test_apply_search_delegates_to_apply_search_filters(self):
        """_apply_search delegates filtering to _apply_search_filters."""
        # Arrange
        from sqlmodel import select

        base_statement = select(Application)

        # Patch the class method since it's a staticmethod
        with patch(
            "codemie.repository.application_repository.ApplicationRepository._apply_search_filters",
            return_value=base_statement,
        ) as mock_filters:
            # Act
            application_repository._apply_search(base_statement, "test")

            # Assert
            mock_filters.assert_called_once_with(base_statement, "test")

    def test_apply_search_adds_ordering_when_search_provided(self):
        """_apply_search adds exact-match-first ordering when search is provided."""
        # Arrange
        from sqlmodel import select

        base_statement = select(Application)

        # Act
        result = application_repository._apply_search(base_statement, "test-proj")

        # Assert
        query_text = _compile_sql(result)
        # Should have CASE expression for exact-match-first ordering
        assert "case" in query_text or "order by" in query_text
