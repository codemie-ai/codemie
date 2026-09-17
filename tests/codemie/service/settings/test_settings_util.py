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

from unittest.mock import patch, MagicMock, PropertyMock

from codemie.rest_api.models.settings import SettingType
from codemie.service.settings.settings_util import (
    search_assistant,
    get_assistant_settings_id,
    user_can_access_setting,
)


def _make_user(user_id="user-1", project_names=None, is_admin=False):
    user = MagicMock()
    user.id = user_id
    user.project_names = project_names if project_names is not None else []
    user.has_access_to_application.side_effect = lambda project: is_admin or project in user.project_names
    return user


def _make_setting(setting_type, user_id=None, project_name="proj-a"):
    setting = MagicMock()
    setting.setting_type = setting_type
    setting.user_id = user_id
    setting.project_name = project_name
    return setting


def test_user_can_access_setting_none_fails_closed():
    user = _make_user()
    assert user_can_access_setting(None, user, "proj-a") is False


def test_user_can_access_own_user_setting():
    user = _make_user(user_id="user-1")
    setting = _make_setting(SettingType.USER, user_id="user-1")
    assert user_can_access_setting(setting, user, "proj-a") is True


def test_user_cannot_access_other_users_user_setting():
    user = _make_user(user_id="user-1")
    setting = _make_setting(SettingType.USER, user_id="user-2")
    assert user_can_access_setting(setting, user, "proj-a") is False


def test_user_can_access_project_setting_of_assistant_project():
    user = _make_user(user_id="user-1", project_names=["proj-a"])
    setting = _make_setting(SettingType.PROJECT, project_name="proj-a")
    assert user_can_access_setting(setting, user, assistant_project="proj-a") is True


def test_user_cannot_access_project_setting_of_other_membership_project():
    # User is a member of proj-b too, but the assistant lives in proj-a: strict scoping rejects it.
    user = _make_user(user_id="user-1", project_names=["proj-a", "proj-b"])
    setting = _make_setting(SettingType.PROJECT, project_name="proj-b")
    assert user_can_access_setting(setting, user, assistant_project="proj-a") is False


def test_user_cannot_access_project_setting_of_non_member_project():
    user = _make_user(user_id="user-1", project_names=["proj-a"])
    setting = _make_setting(SettingType.PROJECT, project_name="proj-b")
    assert user_can_access_setting(setting, user, assistant_project="proj-b") is False


def test_user_cannot_access_assistant_project_setting_without_membership():
    # Setting matches the assistant project, but the user is not a member of it.
    user = _make_user(user_id="user-1", project_names=["proj-a"])
    setting = _make_setting(SettingType.PROJECT, project_name="proj-c")
    assert user_can_access_setting(setting, user, assistant_project="proj-c") is False


def test_marketplace_allows_project_setting_of_other_membership_project():
    # Marketplace assistants relax project scoping to allow a PROJECT setting outside the
    # assistant's own project, but only when the user has access to that setting's project.
    user = _make_user(user_id="user-1", project_names=["proj-a", "proj-b"])
    setting = _make_setting(SettingType.PROJECT, project_name="proj-b")
    assert user_can_access_setting(setting, user, assistant_project="proj-a", marketplace=True) is True


def test_marketplace_rejects_project_setting_of_non_member_project():
    # The cross-project relaxation never opens a project the user cannot otherwise access.
    user = _make_user(user_id="user-1", project_names=["proj-a"])
    setting = _make_setting(SettingType.PROJECT, project_name="proj-b")
    assert user_can_access_setting(setting, user, assistant_project="proj-a", marketplace=True) is False


def test_marketplace_rejects_project_setting_without_assistant_project_when_not_member():
    user = _make_user(user_id="user-1", project_names=[])
    setting = _make_setting(SettingType.PROJECT, project_name="proj-b")
    assert user_can_access_setting(setting, user, assistant_project=None, marketplace=True) is False


def test_marketplace_still_rejects_other_users_user_setting():
    # The USER owner-only invariant holds even for marketplace: another user's personal
    # integration is never accessible.
    user = _make_user(user_id="user-1")
    setting = _make_setting(SettingType.USER, user_id="user-2")
    assert user_can_access_setting(setting, user, assistant_project="proj-a", marketplace=True) is False


def test_marketplace_allows_own_user_setting():
    user = _make_user(user_id="user-1")
    setting = _make_setting(SettingType.USER, user_id="user-1", project_name="proj-b")
    assert user_can_access_setting(setting, user, assistant_project="proj-a", marketplace=True) is True


def test_marketplace_none_still_fails_closed():
    user = _make_user()
    assert user_can_access_setting(None, user, "proj-a", marketplace=True) is False


@patch("codemie.rest_api.models.assistant.Assistant.get_by_id")
def test_search_assistant_actuall(mock_get_assistant):
    search_assistant(assistant_id="12345")

    mock_get_assistant.assert_called_once_with("12345")


@patch("codemie.service.assistant.VirtualAssistantService.get")
def test_search_assistant_virtual(mock_get_v_assistant):
    search_assistant(assistant_id="Virtual_blahblah")

    mock_get_v_assistant.assert_called_once_with("Virtual_blahblah")


def test_get_assistant_settings_id():
    mock_tool = MagicMock()
    mock_tool.name = 'aws_create_ec2'
    mock_tool.settings = PropertyMock(id='tool_setting')

    mock_assistant = MagicMock(
        toolkits=[
            MagicMock(
                toolkit='aws',
                tools=[mock_tool],
                settings=PropertyMock(id='toolkit_setting', credential_type='aws'),
            )
        ]
    )

    result = get_assistant_settings_id(assistant=mock_assistant, credential_type='aws')

    assert result == 'toolkit_setting'

    result = get_assistant_settings_id(assistant=mock_assistant, credential_type=PropertyMock(value='aws_create_ec2'))

    assert result == 'tool_setting'

    result = get_assistant_settings_id(assistant=mock_assistant, credential_type=PropertyMock(value='other'))

    assert result is None
