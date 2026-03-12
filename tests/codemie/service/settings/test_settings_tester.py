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

from codemie_tools.core.project_management.confluence.tools import GenericConfluenceTool
from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
from codemie_tools.notification.email.tools import EmailTool
from codemie_tools.git.toolkit import GitToolkit
from codemie.service.settings.settings_tester import SettingsTester, SettingsTesterHandlerNotFound
from codemie.rest_api.models.settings import TestSettingRequest, CredentialTypes, CredentialValues


def test_init_with_creds():
    request = TestSettingRequest(
        credential_type=CredentialTypes.JIRA,
        credential_values=[CredentialValues(key='username', value='test'), CredentialValues(key='pwd', value='test')],
    )

    instance = SettingsTester(request)

    assert instance.credential_type == CredentialTypes.JIRA
    assert instance.credential_values['username'] == 'test'
    assert instance.credential_values['pwd'] == 'test'


@patch('codemie.rest_api.models.settings.Settings.get_by_id')
def test_init_with_setting_id(mock_get_setting):
    mock_setting = MagicMock()
    mock_setting.credential_values = [
        CredentialValues(key='username', value='test'),
        CredentialValues(key='pwd', value='test'),
    ]
    mock_get_setting.return_value = mock_setting

    request = TestSettingRequest(credential_type=CredentialTypes.JIRA, setting_id='123')

    instance = SettingsTester(request)

    assert instance.credential_type == CredentialTypes.JIRA
    assert instance.credential_values['username'] == 'test'


@patch('codemie.rest_api.models.settings.Settings.get_by_id')
def test_init_with_setting_id_and_creds(mock_get_setting):
    mock_setting = MagicMock()
    mock_setting.credential_values = [
        CredentialValues(key='username', value='test'),
        CredentialValues(key='pwd', value='test'),
    ]
    mock_get_setting.return_value = mock_setting

    request = TestSettingRequest(
        credential_type=CredentialTypes.JIRA,
        credential_values=[
            CredentialValues(key='username', value='test'),
            CredentialValues(key='pwd', value='**********'),
        ],
        setting_id='123',
    )

    instance = SettingsTester(request)

    assert instance.credential_type == CredentialTypes.JIRA
    assert instance.credential_values['username'] == 'test'
    assert instance.credential_values['pwd'] == 'test'


@patch('codemie.service.settings.settings_tester.SettingsTester._test_jira')
def test_test_handler_found(mock_test_jira):
    mock_test_jira.return_value = (True, "")

    request = TestSettingRequest(
        credential_type=CredentialTypes.JIRA,
        credential_values=[CredentialValues(key='username', value='test'), CredentialValues(key='pwd', value='test')],
    )

    instance = SettingsTester(request)

    instance.test()
    mock_test_jira.assert_called_once()


def test_test_handler_not_found():
    request = TestSettingRequest(credential_type=CredentialTypes.FILE_SYSTEM, credential_values=[])

    instance = SettingsTester(request)

    with pytest.raises(SettingsTesterHandlerNotFound) as e:
        instance.test()

    assert 'Unsupported setting type: CredentialTypes.FILE_SYSTEM' in str(e)


def test_handlers():
    request = TestSettingRequest(
        credential_type=CredentialTypes.JIRA,
        credential_values=[CredentialValues(key='username', value='test'), CredentialValues(key='pwd', value='test')],
    )

    instance = SettingsTester(request)

    assert instance.handlers[CredentialTypes.JIRA] == SettingsTester._test_jira
    assert instance.handlers[CredentialTypes.CONFLUENCE] == SettingsTester._test_confluence


@patch.object(GenericJiraIssueTool, 'healthcheck')
def test_test_jira_success(mock_test_integration):
    mock_test_integration.return_value = True, ""
    request = TestSettingRequest(
        credential_type=CredentialTypes.JIRA,
        credential_values=[CredentialValues(key='url', value='test'), CredentialValues(key='token', value='test')],
    )
    instance = SettingsTester(request)

    result = instance._test_jira()

    assert result == (True, "")
    mock_test_integration.assert_called_once()


@patch.object(GenericJiraIssueTool, 'healthcheck')
def test_test_jira_fail(mock_test_integration):
    mock_test_integration.return_value = False, "Error"
    request = TestSettingRequest(
        credential_type=CredentialTypes.JIRA,
        credential_values=[CredentialValues(key='url', value='test'), CredentialValues(key='token', value='test')],
    )
    instance = SettingsTester(request)

    result = instance._test_jira()

    assert result == (False, "Error")
    mock_test_integration.assert_called_once()


@patch.object(GenericConfluenceTool, 'healthcheck')
def test_test_confluence_success(mock_test_integration):
    mock_test_integration.return_value = True, ""
    request = TestSettingRequest(
        credential_type=CredentialTypes.CONFLUENCE,
        credential_values=[CredentialValues(key='url', value='test'), CredentialValues(key='token', value='test')],
    )
    instance = SettingsTester(request)

    result = instance._test_confluence()

    assert result == (True, '')
    mock_test_integration.assert_called_once()


@patch.object(GenericConfluenceTool, 'healthcheck')
def test_test_confluence_fail(mock_test_integration):
    mock_test_integration.return_value = False, "Error"
    request = TestSettingRequest(
        credential_type=CredentialTypes.CONFLUENCE,
        credential_values=[CredentialValues(key='url', value='test'), CredentialValues(key='token', value='test')],
    )
    instance = SettingsTester(request)

    result = instance._test_confluence()

    assert result == (False, 'Error')
    mock_test_integration.assert_called_once()


@patch.object(EmailTool, 'healthcheck')
def test_test_email_success(mock_test_integration):
    mock_test_integration.return_value = (True, '')
    request = TestSettingRequest(
        credential_type=CredentialTypes.EMAIL,
        credential_values=[
            CredentialValues(key='url', value='test'),
            CredentialValues(key='smtp_username', value='test'),
            CredentialValues(key='smtp_password', value='test'),
        ],
    )
    instance = SettingsTester(request)

    result = instance._test_email()

    assert result == (True, '')
    mock_test_integration.assert_called_once()


@patch.object(EmailTool, 'healthcheck')
def test_test_email_fail(mock_test_integration):
    mock_test_integration.return_value = (False, "Some Error occurred")
    request = TestSettingRequest(
        credential_type=CredentialTypes.EMAIL,
        credential_values=[
            CredentialValues(key='url', value='test'),
            CredentialValues(key='smtp_username', value='test'),
            CredentialValues(key='smtp_password', value='test'),
        ],
    )
    instance = SettingsTester(request)

    result = instance._test_email()

    assert result == (False, "Some Error occurred")
    mock_test_integration.assert_called_once()


@patch('codemie.service.settings.settings_tester.ZephyrGenericTool')
def test_test_zephyr_success(mock_tool_class):
    mock_tool = MagicMock()
    mock_tool.healthcheck.return_value = (True, '')
    mock_tool_class.return_value = mock_tool

    request = TestSettingRequest(
        credential_type=CredentialTypes.ZEPHYR_SCALE,
        credential_values=[
            CredentialValues(key='url', value='url'),
            CredentialValues(key='token', value='token'),
        ],
    )
    instance = SettingsTester(request)

    result = instance._test_zephyr()

    assert result == (True, '')
    mock_tool.healthcheck.assert_called_once()


@patch('codemie.service.settings.settings_tester.ZephyrGenericTool')
def test_test_zephyr_fail(mock_tool_class):
    mock_tool = MagicMock()
    mock_tool.healthcheck.return_value = (False, "Some Error occurred")
    mock_tool_class.return_value = mock_tool

    request = TestSettingRequest(
        credential_type=CredentialTypes.ZEPHYR_SCALE,
        credential_values=[
            CredentialValues(key='url', value='url'),
            CredentialValues(key='token', value='token'),
        ],
    )
    instance = SettingsTester(request)

    result = instance._test_zephyr()

    assert result == (False, "Some Error occurred")
    mock_tool.healthcheck.assert_called_once()


@patch.object(GitToolkit, "git_integration_healthcheck")
def test_test_git_success(mock_test_integration):
    mock_test_integration.return_value = True, ""
    request = TestSettingRequest(
        credential_type=CredentialTypes.GIT,
        credential_values=[
            CredentialValues(key="url", value="https://gitlab.example.com"),
            CredentialValues(key="name", value="tokenName"),
            CredentialValues(key="token", value="token"),
        ],
    )
    instance = SettingsTester(request)

    result = instance._test_git()

    assert result == (True, "")
    mock_test_integration.assert_called_once_with(
        configs={
            "base_branch": "main",
            "repo_type": "gitlab",
            "repo_link": "https://gitlab.example.com",
            "token": "token",
            "token_name": "tokenName",
        }
    )


@patch.object(GitToolkit, "git_integration_healthcheck")
def test_test_git_fail(mock_test_integration):
    mock_test_integration.return_value = False, "Error"
    request = TestSettingRequest(
        credential_type=CredentialTypes.GIT,
        credential_values=[
            CredentialValues(key="url", value="https://gitlab.example.com"),
            CredentialValues(key="name", value="tokenName"),
            CredentialValues(key="token", value="token"),
        ],
    )
    instance = SettingsTester(request)

    result = instance._test_git()

    assert result == (False, "Error")
    mock_test_integration.assert_called_once_with(
        configs={
            "base_branch": "main",
            "repo_type": "gitlab",
            "repo_link": "https://gitlab.example.com",
            "token": "token",
            "token_name": "tokenName",
        }
    )


@patch('codemie.service.settings.settings_tester.SonarTool')
def test_test_sonar_success(mock_tool_class):
    mock_tool = MagicMock()
    mock_tool.healthcheck.return_value = (True, '')
    mock_tool_class.return_value = mock_tool

    request = TestSettingRequest(
        credential_type=CredentialTypes.SONAR,
        credential_values=[
            CredentialValues(key='url', value='url'),
            CredentialValues(key='token', value='token'),
            CredentialValues(key='sonar_project_name', value='sonar_project_name'),
        ],
    )

    instance = SettingsTester(request)

    result = instance._test_sonar()

    assert result == (True, '')
    mock_tool.healthcheck.assert_called_once()


@patch('codemie.service.settings.settings_tester.SonarTool')
def test_test_sonar_fail(mock_tool_class):
    mock_tool = MagicMock()
    mock_tool.healthcheck.return_value = (False, "Some Error occurred")
    mock_tool_class.return_value = mock_tool

    request = TestSettingRequest(
        credential_type=CredentialTypes.SONAR,
        credential_values=[
            CredentialValues(key='url', value='url'),
            CredentialValues(key='token', value='token'),
            CredentialValues(key='sonar_project_name', value='sonar_project_name'),
        ],
    )

    instance = SettingsTester(request)

    result = instance._test_sonar()

    assert result == (False, "Some Error occurred")
    mock_tool.healthcheck.assert_called_once()
