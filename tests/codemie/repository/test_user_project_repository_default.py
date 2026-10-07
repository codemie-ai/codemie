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

"""Tests for is_default set/clear/get methods on user_project_repository (EPMCDME-15110)."""

from unittest.mock import MagicMock

import pytest

from codemie.repository.user_project_repository import user_project_repository
from codemie.rest_api.models.user_management import UserProject


class TestGetDefaultForUser:
    def test_returns_default_project(self):
        mock_session = MagicMock()
        default_project = UserProject(id="up1", user_id="user-1", project_name="proj-a", is_default=True)
        mock_session.exec.return_value.first.return_value = default_project

        result = user_project_repository.get_default_for_user(mock_session, "user-1")

        assert result is default_project

    def test_returns_none_when_no_default(self):
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        result = user_project_repository.get_default_for_user(mock_session, "user-1")

        assert result is None


class TestSetDefault:
    def test_returns_none_when_membership_missing(self):
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        result = user_project_repository.set_default(mock_session, "user-1", "proj-a")

        assert result is None
        mock_session.add.assert_not_called()

    def test_sets_default_and_unsets_previous(self):
        mock_session = MagicMock()
        target = UserProject(id="up-target", user_id="user-1", project_name="proj-a", is_default=False)
        previous_default = UserProject(id="up-prev", user_id="user-1", project_name="proj-b", is_default=True)

        # First exec() call = lookup target row; second = lookup existing default row.
        mock_session.exec.return_value.first.side_effect = [target, previous_default]

        result = user_project_repository.set_default(mock_session, "user-1", "proj-a")

        assert result is target
        assert target.is_default is True
        assert previous_default.is_default is False
        assert mock_session.add.call_count == 2  # previous_default flip + target flip

    def test_previous_default_is_flushed_before_target_is_set(self):
        """Swap must never have both rows is_default=True in one statement batch: the DB's
        one-default index rejected a plain a->b swap in live testing when the unit of work
        flushed the target's UPDATE first."""
        mock_session = MagicMock()
        target = UserProject(id="up-target", user_id="user-1", project_name="proj-a", is_default=False)
        previous_default = UserProject(id="up-prev", user_id="user-1", project_name="proj-b", is_default=True)
        mock_session.exec.return_value.first.side_effect = [target, previous_default]
        states_at_flush = []
        mock_session.flush.side_effect = lambda: states_at_flush.append(
            (previous_default.is_default, target.is_default)
        )

        user_project_repository.set_default(mock_session, "user-1", "proj-a")

        assert states_at_flush[0] == (False, False)
        assert states_at_flush[-1] == (False, True)

    def test_idempotent_when_already_default(self):
        mock_session = MagicMock()
        target = UserProject(id="up-target", user_id="user-1", project_name="proj-a", is_default=True)
        # First exec() call = lookup target row; second = lookup existing default row (itself).
        mock_session.exec.return_value.first.side_effect = [target, target]

        result = user_project_repository.set_default(mock_session, "user-1", "proj-a")

        assert result is target
        assert target.is_default is True


class TestClearDefault:
    def test_returns_none_when_membership_missing(self):
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        result = user_project_repository.clear_default(mock_session, "user-1", "proj-a")

        assert result is None

    def test_clears_default(self):
        mock_session = MagicMock()
        target = UserProject(id="up-target", user_id="user-1", project_name="proj-a", is_default=True)
        mock_session.exec.return_value.first.return_value = target

        result = user_project_repository.clear_default(mock_session, "user-1", "proj-a")

        assert result is target
        assert target.is_default is False

    def test_noop_when_not_currently_default(self):
        mock_session = MagicMock()
        target = UserProject(id="up-target", user_id="user-1", project_name="proj-a", is_default=False)
        mock_session.exec.return_value.first.return_value = target

        result = user_project_repository.clear_default(mock_session, "user-1", "proj-a")

        assert result is target
        assert target.is_default is False


class TestDefaultClearedOnRemoval:
    """Proves AC3: is_default lives on the row, so both removal paths clear it for free."""

    def test_single_removal_clears_default(self):
        mock_session = MagicMock()
        default_row = UserProject(id="up1", user_id="user-1", project_name="proj-a", is_default=True)
        mock_session.exec.return_value.first.return_value = default_row

        removed = user_project_repository.remove_project(mock_session, "user-1", "proj-a")

        assert removed is True
        mock_session.delete.assert_called_once_with(default_row)  # the whole row, flag included, is gone

    def test_bulk_removal_clears_default(self):
        mock_session = MagicMock()
        default_row = UserProject(id="up1", user_id="user-1", project_name="proj-a", is_default=True)
        other_row = UserProject(id="up2", user_id="user-2", project_name="proj-a", is_default=False)
        mock_session.exec.return_value.all.return_value = [default_row, other_row]

        count = user_project_repository.remove_projects_for_users(mock_session, ["user-1", "user-2"], "proj-a")

        assert count == 2
        assert mock_session.delete.call_count == 2
        mock_session.delete.assert_any_call(default_row)


class TestOneDefaultConstraintDefinition:
    """15110 AC1 DB backstop: the model declares a partial UNIQUE index scoped to default rows.

    Enforcement itself was verified against live Postgres (see post-analysis-fixes.md); this
    guards the definition the migration mirrors from drifting.
    """

    def test_partial_unique_index_ddl(self):
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateIndex

        index = next(i for i in UserProject.__table__.indexes if i.name == "uix_user_projects_one_default")
        ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))

        assert ddl.startswith("CREATE UNIQUE INDEX uix_user_projects_one_default ON user_projects (user_id)")
        assert "WHERE is_default = true" in ddl


class TestResolvedProjectListOrder:
    """15110 story: the user's resolved project list is deterministic, not DB-row order."""

    def test_sync_list_is_ordered_by_project_name(self):
        mock_session = MagicMock()

        user_project_repository.get_by_user_id(mock_session, "user-1")

        statement = mock_session.exec.call_args[0][0]
        assert "ORDER BY user_projects.project_name" in str(statement)

    @pytest.mark.asyncio
    async def test_async_list_is_ordered_by_project_name(self):
        from unittest.mock import AsyncMock

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=MagicMock())

        await user_project_repository.aget_by_user_id(mock_session, "user-1")

        statement = mock_session.execute.call_args[0][0]
        assert "ORDER BY user_projects.project_name" in str(statement)


class TestDefaultMutationsSerializePerUser:
    """R03: two overlapping set/clear requests for one user must not leave zero defaults, so
    both take a row lock on the user before reading any membership flag."""

    @staticmethod
    def _lock_then_reads(mock_session):
        calls = [name for name, _, _ in mock_session.mock_calls if name in ("execute", "exec")]
        return calls

    def test_set_default_locks_user_row_before_reading(self):
        mock_session = MagicMock()
        target = UserProject(id="up-target", user_id="user-1", project_name="proj-a", is_default=False)
        mock_session.exec.return_value.first.side_effect = [target, None]

        user_project_repository.set_default(mock_session, "user-1", "proj-a")

        calls = self._lock_then_reads(mock_session)
        assert calls[0] == "execute", calls
        locking_stmt = str(mock_session.execute.call_args_list[0][0][0]).upper()
        assert "FOR UPDATE" in locking_stmt and "USERS" in locking_stmt

    def test_clear_default_locks_user_row_before_reading(self):
        mock_session = MagicMock()
        target = UserProject(id="up-target", user_id="user-1", project_name="proj-a", is_default=True)
        mock_session.exec.return_value.first.return_value = target

        user_project_repository.clear_default(mock_session, "user-1", "proj-a")

        assert self._lock_then_reads(mock_session)[0] == "execute"
