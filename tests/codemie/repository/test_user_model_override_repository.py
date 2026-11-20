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

from unittest.mock import MagicMock
from uuid import uuid4


from codemie.rest_api.models.user_model_override import UserModelOverride
from codemie.repository.user_model_override_repository import UserModelOverrideRepository


def _make_session() -> MagicMock:
    """AsyncSession stand-in: execute/flush/delete are awaitable, add is sync."""
    session = MagicMock()
    session.add = MagicMock()
    return session


def _set_scalars(session: MagicMock, *, first=None, all_=None) -> None:
    """Configure `(session.execute(stmt)).scalars().first()/all()`."""
    result = MagicMock()
    result.scalars.return_value.first.return_value = first
    result.scalars.return_value.all.return_value = all_ if all_ is not None else []
    session.execute.return_value = result


def test_get_by_user_and_project_found():
    """Verify get_by_user_and_project returns override when found."""
    session = _make_session()
    mock_override = MagicMock(spec=UserModelOverride)
    _set_scalars(session, first=mock_override)

    result = UserModelOverrideRepository.get_by_user_and_project(session, str(uuid4()), "test-project")

    assert result == mock_override
    session.execute.assert_called_once()


def test_get_by_user_and_project_not_found():
    """Verify get_by_user_and_project returns None when not found."""
    session = _make_session()
    _set_scalars(session, first=None)

    result = UserModelOverrideRepository.get_by_user_and_project(session, str(uuid4()), "test-project")

    assert result is None


def test_create_new_override():
    """Verify create adds, flushes and returns new override."""
    session = _make_session()
    user_id = str(uuid4())
    allowed_models = ["gpt-4", "claude-opus"]

    result = UserModelOverrideRepository.create(session, user_id, "test-project", allowed_models, default_model="gpt-4")

    assert result.user_id == user_id
    assert result.project_name == "test-project"
    assert result.allowed_models == allowed_models
    assert result.default_model == "gpt-4"
    assert result.date is not None
    assert result.update_date is not None
    session.add.assert_called_once_with(result)
    session.flush.assert_called_once()


def test_create_defaults_default_model_to_none():
    """Verify create leaves default_model unset when not provided."""
    session = _make_session()

    result = UserModelOverrideRepository.create(session, str(uuid4()), "test-project", ["gpt-4"])

    assert result.default_model is None


def test_delete_removes_override():
    """Verify delete removes override and flushes."""
    session = _make_session()
    mock_override = MagicMock(spec=UserModelOverride)
    _set_scalars(session, first=mock_override)

    UserModelOverrideRepository.delete(session, str(uuid4()), "test-project")

    session.delete.assert_called_once_with(mock_override)
    session.flush.assert_called_once()


def test_delete_missing_override_is_noop():
    """Verify delete does nothing when override does not exist."""
    session = _make_session()
    _set_scalars(session, first=None)

    UserModelOverrideRepository.delete(session, str(uuid4()), "test-project")

    session.delete.assert_not_called()
    session.flush.assert_not_called()


def test_get_by_project_pagination():
    """Verify get_by_project returns the page of overrides and applies offset/limit."""
    session = _make_session()
    mock_overrides = [MagicMock(spec=UserModelOverride) for _ in range(3)]
    _set_scalars(session, all_=mock_overrides)

    result = UserModelOverrideRepository.get_by_project(session, "test-project", skip=10, limit=20)

    assert result == mock_overrides
    statement = session.execute.call_args.args[0]
    assert statement._offset_clause.value == 10
    assert statement._limit_clause.value == 20


def test_update_existing_override():
    """Verify update modifies allowed_models and default_model."""
    session = _make_session()
    new_models = ["claude-opus", "gpt-4"]
    mock_override = MagicMock(spec=UserModelOverride)
    _set_scalars(session, first=mock_override)

    result = UserModelOverrideRepository.update(
        session, str(uuid4()), "test-project", new_models, default_model="claude-opus"
    )

    assert result is mock_override
    assert mock_override.allowed_models == new_models
    assert mock_override.default_model == "claude-opus"
    session.flush.assert_called_once()


def test_update_missing_override_returns_none():
    """Verify update returns None and does not flush when override does not exist."""
    session = _make_session()
    _set_scalars(session, first=None)

    result = UserModelOverrideRepository.update(session, str(uuid4()), "test-project", ["gpt-4"])

    assert result is None
    session.flush.assert_not_called()


def test_delete_all_by_project():
    """Verify delete_all_by_project deletes every project override and flushes once."""
    session = _make_session()
    mock_overrides = [MagicMock(spec=UserModelOverride) for _ in range(2)]
    _set_scalars(session, all_=mock_overrides)

    UserModelOverrideRepository.delete_all_by_project(session, "test-project")

    assert session.delete.call_count == 2
    session.flush.assert_called_once()


def test_get_by_users_and_project_keys_the_overrides_by_user():
    """One query answers for every user; users without an override are simply absent."""
    session = _make_session()
    first = MagicMock(spec=UserModelOverride, user_id="u1")
    second = MagicMock(spec=UserModelOverride, user_id="u3")
    _set_scalars(session, all_=[first, second])

    result = UserModelOverrideRepository.get_by_users_and_project(session, ["u1", "u2", "u3"], "test-project")

    assert result == {"u1": first, "u3": second}
    session.execute.assert_called_once()


def test_upsert_many_updates_existing_rows_creates_the_rest_and_flushes_once():
    session = _make_session()
    existing = UserModelOverride(
        user_id="u2", project_name="test-project", allowed_models=["claude-opus"], default_model="claude-opus"
    )
    models = ["gpt-4"]

    result = UserModelOverrideRepository.upsert_many(
        session, "test-project", ["u1", "u2"], models, "gpt-4", {"u2": existing}
    )

    assert [o.user_id for o in result] == ["u1", "u2"]
    assert result[1] is existing
    assert existing.allowed_models == ["gpt-4"] and existing.default_model == "gpt-4"
    created = result[0]
    assert (created.project_name, created.allowed_models, created.default_model) == ("test-project", ["gpt-4"], "gpt-4")
    session.add.assert_called_once_with(created)
    session.flush.assert_called_once()


def test_upsert_many_gives_every_user_their_own_copy_of_the_model_list():
    session = _make_session()
    models = ["gpt-4"]

    result = UserModelOverrideRepository.upsert_many(session, "test-project", ["u1", "u2"], models, None, {})
    result[0].allowed_models.append("claude-opus")

    assert result[1].allowed_models == ["gpt-4"]
    assert models == ["gpt-4"]


def test_delete_many_deletes_each_row_and_flushes_once():
    session = _make_session()
    rows = [MagicMock(spec=UserModelOverride), MagicMock(spec=UserModelOverride)]

    UserModelOverrideRepository.delete_many(session, rows)

    assert [c.args[0] for c in session.delete.call_args_list] == rows
    session.flush.assert_called_once()
