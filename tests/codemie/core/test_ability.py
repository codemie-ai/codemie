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

import pytest
from unittest.mock import MagicMock, patch

from codemie.core.ability import Ability, Action
from codemie.rest_api.models.index import IndexInfo
from codemie.core.models import CreatedByUser


def test_ability_init():
    user = MagicMock(name="test-user")
    ability = Ability(user=user)

    assert ability.user == user


@pytest.fixture
def mock_index_info():
    return IndexInfo(
        project_name="project",
        repo_name="repo",
        embeddings_model="model",
        current_state=10,
        complete_state=10,
        index_type="code",
        created_by=CreatedByUser(username="test_user", id="123"),
    )


def test_ability_can_creator(mock_index_info):
    user = MagicMock(name="test_user", is_admin=True)
    user.is_maintainer = False
    user.is_admin_or_maintainer = True
    ability = Ability(user=user)

    assert ability.can(Action.READ, mock_index_info) is True
    assert ability.can(Action.WRITE, mock_index_info) is True
    assert ability.can(Action.DELETE, mock_index_info) is True


def test_ability_can_admin(mock_index_info):
    user = MagicMock(name="admin-user", is_admin=True)
    user.is_maintainer = False
    user.is_admin_or_maintainer = True
    ability = Ability(user=user)

    assert ability.can(Action.READ, mock_index_info) is True
    assert ability.can(Action.WRITE, mock_index_info) is True
    assert ability.can(Action.DELETE, mock_index_info) is True


def test_ability_can_non_creator(mock_index_info):
    user = MagicMock(name="non-creator-user", is_admin=False)
    user.is_maintainer = False
    user.is_admin_or_maintainer = False
    ability = Ability(user=user)

    assert ability.can(Action.READ, mock_index_info) is False
    assert ability.can(Action.WRITE, mock_index_info) is False
    assert ability.can(Action.DELETE, mock_index_info) is False


@patch("codemie.rest_api.models.permission.Permission.exists_for", return_value=True)
def test_ability_with_permissions(_mock_exists, mock_index_info):
    user = MagicMock(name="non-creator-user", is_admin=False)
    user.is_maintainer = False
    user.is_admin_or_maintainer = False
    ability = Ability(user=user)

    assert ability.can(Action.READ, mock_index_info, check_resource_permissions=True) is True
    assert ability.can(Action.WRITE, mock_index_info, check_resource_permissions=True) is True
    assert ability.can(Action.DELETE, mock_index_info, check_resource_permissions=True) is True


def test_ability_wo_permissions(mock_index_info):
    user = MagicMock(name="non-creator-user", is_admin=False)
    user.is_maintainer = False
    user.is_admin_or_maintainer = False
    ability = Ability(user=user)

    assert ability.can(Action.READ, mock_index_info) is False
    assert ability.can(Action.WRITE, mock_index_info) is False
    assert ability.can(Action.DELETE, mock_index_info) is False


def test_ability_list(mock_index_info):
    user = MagicMock(name="test_user", is_admin=True)
    user.is_maintainer = False
    user.is_admin_or_maintainer = True
    ability = Ability(user=user)

    abilities = ability.list(mock_index_info)

    assert abilities == [Action.READ, Action.WRITE, Action.DELETE]


def _make_assistant(project="proj", shared=False, is_global=False):
    from codemie.rest_api.models.assistant import Assistant

    return Assistant(
        id="asst-1",
        name="test",
        description="test",
        system_prompt="test",
        system_prompt_history=[],
        project=project,
        toolkits=[],
        shared=shared,
        is_react=False,
        is_global=is_global,
        created_date="2022-01-02T00:00:00.000Z",
        agent_mode="general",
        creator="owner-1",
        update_date="2022-01-02T00:00:00.000Z",
    )


def test_is_shared_with_per_user_grant():
    assistant = _make_assistant(project="proj", shared=False)
    user = MagicMock()
    user.is_external_user = False
    user.project_names = []
    user.admin_project_names = []
    user.id = "user-1"

    with patch("codemie.rest_api.models.assistant.Session") as mock_session_cls:
        session = mock_session_cls.return_value.__enter__.return_value
        session.exec.return_value.first.return_value = "asst-1"
        assert assistant.is_shared_with(user) is True
    sql = str(session.exec.call_args[0][0]).lower()
    assert "permissions" in sql and "principal_id" in sql


def test_is_shared_with_no_grant_returns_false():
    assistant = _make_assistant(project="proj", shared=False)
    user = MagicMock()
    user.is_external_user = False
    user.project_names = []
    user.admin_project_names = []
    user.id = "user-1"

    with patch("codemie.rest_api.models.assistant.Session") as mock_session_cls:
        mock_session_cls.return_value.__enter__.return_value.exec.return_value.first.return_value = None
        assert assistant.is_shared_with(user) is False


def test_is_shared_with_project_shared_still_works():
    assistant = _make_assistant(project="proj", shared=True)
    user = MagicMock()
    user.is_external_user = False
    user.project_names = ["proj"]
    user.id = "user-1"

    # Should NOT need to query Permission at all
    with patch("codemie.rest_api.models.assistant.Session") as mock_get:
        result = assistant.is_shared_with(user)
    mock_get.assert_not_called()
    assert result is True


def _grant_user(external=False):
    user = MagicMock()
    user.is_external_user = external
    user.is_admin_or_maintainer = False
    user.project_names = []
    user.admin_project_names = []
    user.id = "user-1"
    return user


@pytest.fixture(autouse=True)
def _user_management_enabled(monkeypatch):
    monkeypatch.setattr("codemie.rest_api.models.permission.config.ENABLE_USER_MANAGEMENT", True)


def test_named_grant_resource_ids_none_when_user_management_disabled(monkeypatch):
    from codemie.rest_api.models.permission import Permission

    monkeypatch.setattr("codemie.rest_api.models.permission.config.ENABLE_USER_MANAGEMENT", False)
    assert Permission.named_grant_resource_ids(_grant_user()) is None


def test_is_shared_with_ignores_grants_when_user_management_disabled(monkeypatch):
    monkeypatch.setattr("codemie.rest_api.models.permission.config.ENABLE_USER_MANAGEMENT", False)
    assistant = _make_assistant(project="proj", shared=False)
    with patch("codemie.rest_api.models.assistant.Session") as mock_session_cls:
        assert assistant.is_shared_with(_grant_user()) is False
    mock_session_cls.assert_not_called()


def test_named_grant_resource_ids_none_for_external_user():
    from codemie.rest_api.models.permission import Permission

    assert Permission.named_grant_resource_ids(_grant_user(external=True)) is None


def test_named_grant_resource_ids_for_internal_user():
    from codemie.rest_api.models.permission import Permission

    sql = str(Permission.named_grant_resource_ids(_grant_user())).lower()
    assert "cast(permissions.resource_id as text)" in sql
    assert "principal_id" in sql and "resource_type" in sql


def test_is_shared_with_external_user_with_grant_returns_false():
    assistant = _make_assistant(project="proj", shared=False)
    with patch("codemie.rest_api.models.assistant.Session") as mock_session_cls:
        mock_session_cls.return_value.__enter__.return_value.exec.return_value.first.return_value = "asst-1"
        assert assistant.is_shared_with(_grant_user(external=True)) is False
    mock_session_cls.assert_not_called()


def test_is_shared_with_external_user_with_preloaded_grant_returns_false():
    assistant = _make_assistant(project="proj", shared=False)
    assert assistant.is_shared_with(_grant_user(external=True), granted_ids={"asst-1"}) is False


def test_is_shared_with_uses_granted_ids_without_db():
    assistant = _make_assistant(project="proj", shared=False)
    with patch("codemie.rest_api.models.assistant.Session") as mock_session_cls:
        assert assistant.is_shared_with(_grant_user(), granted_ids={"asst-1"}) is True
        assert assistant.is_shared_with(_grant_user(), granted_ids=set()) is False
    mock_session_cls.assert_not_called()


def test_ability_with_granted_ids_lists_read_without_db():
    assistant = _make_assistant(project="proj", shared=False)
    with patch("codemie.rest_api.models.assistant.Session") as mock_session_cls:
        abilities = Ability(_grant_user(), granted_ids={"asst-1"}).list(assistant)
    mock_session_cls.assert_not_called()
    assert Action.READ in abilities
    assert Action.WRITE not in abilities


def test_is_shared_with_global_assistant_without_id_still_true():
    assistant = _make_assistant(project="proj", is_global=True)
    assistant.id = None
    assert assistant.is_shared_with(_grant_user()) is True


def test_is_shared_with_unsaved_assistant_has_no_grant():
    assistant = _make_assistant(project="proj", shared=False)
    assistant.id = None
    with patch("codemie.rest_api.models.assistant.Session") as mock_session_cls:
        assert assistant.is_shared_with(_grant_user()) is False
    mock_session_cls.assert_not_called()


def test_named_grant_predicate_binds_caller_assistant_and_user_principal():
    from sqlalchemy.dialects import postgresql

    from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType

    compiled = Permission.named_grant_resource_ids(_grant_user()).compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    assert "permissions.resource_type = %(resource_type_1)s" in sql
    assert "permissions.principal_type = %(principal_type_1)s" in sql
    assert "permissions.principal_id = %(principal_id_1)s" in sql
    params = compiled.params
    assert params["principal_id_1"] == "user-1"
    assert params["resource_type_1"] in (ResourceType.ASSISTANT, ResourceType.ASSISTANT.name)
    assert params["principal_type_1"] in (PrincipalType.USER, PrincipalType.USER.name)
