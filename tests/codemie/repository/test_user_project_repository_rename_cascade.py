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

"""Unit tests for UserProjectRepository.rename_project_cascade."""

from unittest.mock import MagicMock

from codemie.repository.user_project_repository import user_project_repository


class TestRenameProjectCascade:
    """Tests for UserProjectRepository.rename_project_cascade."""

    def test_returns_rowcount_from_execute(self):
        """rename_project_cascade returns rowcount reported by SQLAlchemy execute."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 5
        mock_session.execute.return_value = mock_result

        result = user_project_repository.rename_project_cascade(mock_session, "old-proj", "new-proj")

        assert result == 5

    def test_returns_zero_when_no_rows_matched(self):
        """rename_project_cascade returns 0 when no user_projects rows match old_name."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute.return_value = mock_result

        result = user_project_repository.rename_project_cascade(mock_session, "nonexistent", "new-proj")

        assert result == 0

    def test_calls_execute_with_update_statement(self):
        """rename_project_cascade calls session.execute (DML UPDATE) not session.exec."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        user_project_repository.rename_project_cascade(mock_session, "old-proj", "new-proj")

        # Must use execute (DML) not exec (SELECT shorthand)
        mock_session.execute.assert_called_once()
        mock_session.exec.assert_not_called()

    def test_calls_flush_after_execute(self):
        """rename_project_cascade flushes after the UPDATE to make changes visible."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 2
        mock_session.execute.return_value = mock_result

        user_project_repository.rename_project_cascade(mock_session, "old-proj", "new-proj")

        mock_session.flush.assert_called_once()

    def test_does_not_commit(self):
        """rename_project_cascade does not commit — caller controls transaction."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        user_project_repository.rename_project_cascade(mock_session, "old-proj", "new-proj")

        mock_session.commit.assert_not_called()

    def test_passes_correct_old_and_new_names(self):
        """rename_project_cascade executes UPDATE with correct WHERE and VALUES."""

        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        mock_session.execute.return_value = mock_result

        user_project_repository.rename_project_cascade(mock_session, "alpha-project", "beta-project")

        # Inspect the compiled UPDATE statement
        stmt = mock_session.execute.call_args[0][0]
        compiled = stmt.compile(compile_kwargs={"literal_binds": True})
        sql_text = str(compiled).lower()

        assert "user_projects" in sql_text
        assert "alpha-project" in sql_text
        assert "beta-project" in sql_text
