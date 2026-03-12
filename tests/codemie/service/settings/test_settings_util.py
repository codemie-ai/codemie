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
from codemie.service.settings.settings_util import search_assistant, get_assistant_settings_id


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
