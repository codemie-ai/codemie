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


def _grants(*assistant_ids):
    from codemie.core.ability import PreloadedGrants

    return PreloadedGrants("Assistant", {assistant_id: Action.READ for assistant_id in assistant_ids})


def _grant_user(external=False):
    user = MagicMock()
    user.is_external_user = external
    user.is_admin_or_maintainer = False
    user.project_names = []
    user.admin_project_names = []
    user.id = "user-1"
    return user


def test_is_granted_to_reads_the_preloaded_grant():
    assistant = _make_assistant(project="proj", shared=False)
    assert assistant.is_granted_to(_grant_user(), Action.READ, _grants("asst-1")) is True
    assert assistant.is_granted_to(_grant_user(), Action.READ, _grants("other")) is False
    assert assistant.is_granted_to(_grant_user(), Action.WRITE, _grants("asst-1")) is False


def test_is_shared_with_has_no_grant_term():
    import inspect

    from codemie.rest_api.models.assistant import Assistant

    assert "grants" not in inspect.signature(Assistant.is_shared_with).parameters


def test_assistant_read_registers_granted_and_write_delete_do_not():
    from codemie.core.ability import Role

    roles = Ability.PERMISSIONS["Assistant"]
    assert Role.GRANTED in roles[Action.READ]
    assert Role.GRANTED not in roles[Action.WRITE] and Role.GRANTED not in roles[Action.DELETE]


def test_can_passes_the_action_to_the_granted_role():
    from codemie.core.ability import PreloadedGrants

    assistant = _make_assistant(project="proj", shared=False)
    calls = []
    original = type(assistant).is_granted_to

    def spy(self, user, action, grants):
        calls.append(action)
        return original(self, user, action, grants)

    with patch.object(type(assistant), "is_granted_to", spy):
        assert Ability(_grant_user(), grants=PreloadedGrants("Assistant", {"asst-1": Action.READ})).can(
            Action.READ, assistant
        )
    assert calls == [Action.READ]


def test_is_shared_with_fails_closed_without_a_preload(monkeypatch):
    """No preload means no grant: the assistant does no per-object lookup (shared named-grant rule)."""
    monkeypatch.setattr("codemie.rest_api.models.permission.config.ENABLE_USER_MANAGEMENT", True)
    assistant = _make_assistant(project="proj", shared=False)
    with (
        patch("codemie.rest_api.models.assistant.Session") as assistant_session,
        patch("codemie.rest_api.models.permission.Session") as permission_session,
    ):
        assert assistant.is_shared_with(_grant_user()) is False
    assistant_session.assert_not_called()
    permission_session.assert_not_called()


def test_is_shared_with_project_shared_still_works():
    assistant = _make_assistant(project="proj", shared=True)
    user = _grant_user()
    user.project_names = ["proj"]
    assert assistant.is_shared_with(user) is True


def test_is_shared_with_external_user_with_preloaded_grant_returns_false():
    assistant = _make_assistant(project="proj", shared=False)
    assert assistant.is_granted_to(_grant_user(external=True), Action.READ, _grants("asst-1")) is False
    assert Ability(_grant_user(external=True), grants=_grants("asst-1")).list(assistant) == []


def test_ability_with_preloaded_assistant_grants_lists_read_only():
    assistant = _make_assistant(project="proj", shared=False)
    abilities = Ability(_grant_user(), grants=_grants("asst-1")).list(assistant)
    assert Action.READ in abilities
    assert Action.WRITE not in abilities


def test_ability_without_a_grant_row_gives_a_grantee_nothing():
    from codemie.core.ability import PreloadedGrants

    assistant = _make_assistant(project="proj", shared=False)
    with patch("codemie.core.ability.load_class_grants", return_value=PreloadedGrants("Assistant", {})) as load:
        assert Ability(_grant_user()).list(assistant) == []
    load.assert_called_once()


def test_ability_loads_its_own_grants_for_a_class_it_was_not_given():
    from codemie.core.ability import PreloadedGrants

    assistant = _make_assistant(project="proj", shared=False)
    hosted = PreloadedGrants("HostedApplications", {"asst-1": Action.READ})
    with patch("codemie.core.ability.load_class_grants", return_value=_grants("asst-1")) as load:
        assert Ability(_grant_user(), grants=hosted).list(assistant) == [Action.READ]
    assert load.call_args.args[1] == "Assistant"


def test_granted_is_the_last_role_so_other_roles_never_load_grants():
    from codemie.core.ability import Role

    for permissions in Ability.PERMISSIONS.values():
        for roles in permissions.values():
            assert Role.GRANTED not in roles or roles[-1] is Role.GRANTED


def test_a_class_without_named_grants_has_no_grant_source():
    from codemie.service.permission.named_grant_service import load_class_grants

    assert load_class_grants(_grant_user(), "WorkflowConfig") is None


def test_ability_has_one_grant_keyword():
    import inspect

    assert list(inspect.signature(Ability.__init__).parameters) == ["self", "user", "grants"]


def test_is_shared_with_global_assistant_without_id_still_true():
    assistant = _make_assistant(project="proj", is_global=True)
    assistant.id = None
    assert assistant.is_shared_with(_grant_user()) is True


def test_is_shared_with_unsaved_assistant_has_no_grant():
    assistant = _make_assistant(project="proj", shared=False)
    assistant.id = None
    assert assistant.is_granted_to(_grant_user(), Action.READ, _grants("asst-1")) is False
