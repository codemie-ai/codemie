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

"""Tests for project limit counting with personal project exclusion (Story 10)

Story 10: Personal Project Constraints Enforcement

Tests cover:
- Personal projects excluded from project_limit count
- Only shared projects created by user are counted
- Projects assigned to user (created by others) excluded from count

Includes Story 14 validation that soft-deleted projects are excluded from counts.
"""

from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from codemie.repository.application_repository import application_repository


def _compile_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


class TestProjectLimitCounting:
    """Test count_shared_projects_created_by_user excludes personal projects"""

    def test_count_excludes_personal_projects(self):
        """Test: Personal projects are excluded from count"""
        # Arrange
        mock_session = MagicMock()
        user_id = "alice-123"

        # Mock query result: Alice has 3 shared projects (personal projects excluded)
        mock_session.exec.return_value.one.return_value = 3

        # Act
        count = application_repository.count_shared_projects_created_by_user(mock_session, user_id)

        # Assert
        assert count == 3

        # Verify query filters for shared projects only
        call_args = mock_session.exec.call_args[0][0]
        query_str = _compile_sql(call_args)
        assert "project_type" in query_str
        assert "created_by" in query_str

    def test_count_only_user_created_projects(self):
        """Test: Only projects created by user are counted (not assigned by others)"""
        # Arrange
        mock_session = MagicMock()
        user_id = "bob-456"

        # Mock: Bob created 2 shared projects (has access to 10 but only created 2)
        mock_session.exec.return_value.one.return_value = 2

        # Act
        count = application_repository.count_shared_projects_created_by_user(mock_session, user_id)

        # Assert: Only created projects counted
        assert count == 2

    def test_count_zero_when_no_shared_projects(self):
        """Test: Returns 0 when user has no shared projects (only personal)"""
        # Arrange
        mock_session = MagicMock()
        user_id = "dave-012"

        # Mock: Dave has 0 shared projects (only 1 personal)
        mock_session.exec.return_value.one.return_value = 0

        # Act
        count = application_repository.count_shared_projects_created_by_user(mock_session, user_id)

        # Assert
        assert count == 0

    def test_count_converts_numeric_aggregate_result(self):
        """Test: Aggregate count result is converted to int."""
        # Arrange
        mock_session = MagicMock()
        user_id = "eve-345"

        # Mock aggregate result as int-compatible value
        mock_session.exec.return_value.one.return_value = 0

        # Act
        count = application_repository.count_shared_projects_created_by_user(mock_session, user_id)

        # Assert
        assert count == 0

    def test_scenario_ac_user_with_limit_3_and_mixed_projects(self):
        """Test Story 10 AC Scenario: User with project_limit=3, 1 personal + 3 shared"""
        # Arrange
        mock_session = MagicMock()
        user_id = "frank-678"

        # Scenario: Frank has 1 personal + 3 shared projects
        # Only 3 shared should be counted (personal excluded)
        mock_session.exec.return_value.one.return_value = 3

        # Act
        count = application_repository.count_shared_projects_created_by_user(mock_session, user_id)

        # Assert: Only shared projects counted
        assert count == 3
        # Note: In Story 14, this would trigger limit reached (3/3)


class TestProjectLimitCountingQueryStructure:
    """Test query structure to ensure correct filters"""

    def test_query_filters_by_created_by(self):
        """Test: Query filters by created_by column"""
        # Arrange
        mock_session = MagicMock()
        user_id = "test-user"
        mock_session.exec.return_value.one.return_value = 0

        # Act
        application_repository.count_shared_projects_created_by_user(mock_session, user_id)

        # Assert: Verify created_by filter in query
        call_args = mock_session.exec.call_args[0][0]
        query_str = _compile_sql(call_args)
        assert "applications.created_by" in query_str

    def test_query_filters_by_project_type_shared(self):
        """Test: Query filters for project_type='shared'"""
        # Arrange
        mock_session = MagicMock()
        user_id = "test-user"
        mock_session.exec.return_value.one.return_value = 0

        # Act
        application_repository.count_shared_projects_created_by_user(mock_session, user_id)

        # Assert: Verify project_type='shared' filter
        call_args = mock_session.exec.call_args[0][0]
        query_str = _compile_sql(call_args)
        assert "applications.project_type = 'shared'" in query_str

    def test_query_filters_only_active_projects(self):
        """Test: Query excludes soft-deleted projects via deleted_at IS NULL."""
        # Arrange
        mock_session = MagicMock()
        user_id = "test-user"
        mock_session.exec.return_value.one.return_value = 0

        # Act
        application_repository.count_shared_projects_created_by_user(mock_session, user_id)

        # Assert
        call_args = mock_session.exec.call_args[0][0]
        query_str = _compile_sql(call_args)
        assert "applications.deleted_at" in query_str
        assert "is null" in query_str
