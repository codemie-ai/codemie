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
from unittest.mock import patch, MagicMock, ANY

from codemie.service.provider.datasource.provider_datasource_adapter import ProviderDatasourceAdapter
from codemie.rest_api.security.user import User
from codemie.rest_api.models.provider import ProviderToolMetadata
from codemie.rest_api.models.index import IndexInfo


@pytest.fixture
def mock_provider_config():
    config = MagicMock()
    config.name = "provider_name"

    return config


@pytest.fixture
def mock_toolkit_config():
    config = MagicMock()
    config.name = "toolkit_name"

    mock_tool = MagicMock()
    mock_metadata = MagicMock()
    mock_metadata.tool_action_type = "modify"
    mock_metadata.tool_purpose = "life_cycle_management"
    mock_tool.tool_metadata = ProviderToolMetadata(
        tool_action_type=ProviderToolMetadata.ActionType.MODIFY,
        tool_purpose=ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT,
    )

    config.provided_tools = [mock_tool]

    return config


@pytest.fixture
def mock_user() -> MagicMock:
    mock_user = MagicMock(spec=User)
    mock_user.id = "example_user_id"
    return mock_user


@pytest.fixture
def mock_index() -> MagicMock:
    mock_index = MagicMock(spec=IndexInfo)
    mock_index.id = "example_user_id"
    return mock_index


def test_init(mock_index):
    adapter = ProviderDatasourceAdapter(
        user="user",
        provider_config="provider_config",
        toolkit_config="toolkit_config",
        project_id="project_id",
        datasource=mock_index(),
    )
    assert adapter.user == "user"
    assert adapter.provider_config == "provider_config"
    assert adapter.toolkit_config == "toolkit_config"
    assert adapter.project_id == "project_id"
    assert adapter.correlation_id is not None
    assert isinstance(adapter.correlation_id, str)


def test_create(mocker, mock_index):
    adapter = ProviderDatasourceAdapter(
        user="user",
        provider_config="provider_config",
        toolkit_config="toolkit_config",
        project_id="project_id",
        datasource=mock_index(),
    )
    adapter._send_request = mocker.MagicMock()
    adapter.create(base_params="base_params", create_params="create_params")
    adapter._send_request.assert_called_once_with(
        base_params="base_params", request_params="create_params", action=ProviderToolMetadata.ActionType.CREATE
    )


def test_delete(mocker, mock_index):
    adapter = ProviderDatasourceAdapter(
        user="user",
        provider_config="provider_config",
        toolkit_config="toolkit_config",
        project_id="project_id",
        datasource=mock_index(),
    )

    adapter._send_request = mocker.MagicMock()
    adapter.delete(base_params="base_params")
    adapter._send_request.assert_called_once_with(
        base_params="base_params", action=ProviderToolMetadata.ActionType.REMOVE
    )


def test_reindex(mocker, mock_index):
    adapter = ProviderDatasourceAdapter(
        user="user",
        provider_config="provider_config",
        toolkit_config="toolkit_config",
        project_id="project_id",
        datasource=mock_index(),
    )

    adapter._send_request = mocker.MagicMock()
    adapter.reindex(base_params="base_params")
    adapter._send_request.assert_called_once_with(
        base_params="base_params", action=ProviderToolMetadata.ActionType.MODIFY
    )


@patch("codemie.service.provider.provider_api_client.ProviderAPIClient.build")
def test_send_request(mock_api_client, mock_provider_config, mock_toolkit_config, mock_user, mock_index):
    mock_invoke = MagicMock()
    mock_api_client.return_value.invoke_tool = mock_invoke

    adapter = ProviderDatasourceAdapter(
        user=mock_user,
        provider_config=mock_provider_config,
        toolkit_config=mock_toolkit_config,
        project_id="project_id",
        datasource=mock_index,
    )

    adapter._send_request(
        action=ProviderToolMetadata.ActionType.MODIFY,
        base_params={"base_param": "value"},
        request_params={"param1": "value1"},
    )

    mock_invoke.assert_called_once_with(
        toolkit_name='toolkit_name',
        tool_name=mock_toolkit_config.provided_tools[0].name,
        x_correlation_id=adapter.correlation_id,
        x_callback_otp=ANY,
        tool_invocation_request={
            'user_id': 'example_user_id',
            'project_id': 'project_id',
            'configuration': {'configuration_type': 'datasource', 'parameters': {'base_param': 'value'}},
            'parameters': {'param1': 'value1'},
            'async': ANY,
            'callback_url': ANY,
        },
    )
