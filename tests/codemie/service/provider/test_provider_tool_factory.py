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
from unittest.mock import MagicMock, patch, ANY
from urllib3.exceptions import MaxRetryError
from typing import Optional

from codemie.service.provider.provider_tool_factory import (
    ProviderToolFactory,
    ProviderToolBase,
    ProviderConnectionError,
)
from codemie.rest_api.models.provider import Provider, ProviderConfiguration, ProviderToolkit, ProviderToolArgument
from codemie.rest_api.security.user import User
from codemie.configs import config


@pytest.fixture
def provider_config():
    mock = MagicMock(
        spec=Provider,
        configuration=MagicMock(
            spec=ProviderConfiguration, auth_type=ProviderConfiguration.AuthType.BEARER.value, auth_secret="test_secret"
        ),
        service_location_url="http://test.com",
    )

    return mock


@pytest.fixture
def toolkit_config():
    mock = MagicMock(spec=ProviderToolkit)
    mock.name = "test"
    mock.toolkit_id = "test_id"
    return mock


@pytest.fixture
def tool_config():
    tool_config = MagicMock(spec=ProviderToolkit.Tool)
    tool_config.name = "test"
    tool_config.description = "A test tool"
    tool_config.args_schema = {
        "param1": ProviderToolArgument(arg_type="String", required=False, description=""),
        "param2": ProviderToolArgument(arg_type="Number", required=True, description=""),
    }

    return tool_config


@pytest.fixture
def tool_factory(provider_config, toolkit_config, tool_config):
    return ProviderToolFactory(provider_config, toolkit_config, tool_config)


@pytest.fixture
def tool_params():
    return {
        "user": User(id="tool_user", auth_token="123"),
        "project_id": "tool_project",
        "request_uuid": "tool_request",
    }


def test_build_creates_tool_class(tool_factory, tool_params):
    tool_class = tool_factory.build()
    instance = tool_class(**tool_params)

    assert tool_class.__name__ == "TestTool"
    assert issubclass(tool_class, ProviderToolBase)

    assert instance.name == "test"
    assert instance.description == "A test tool"
    assert hasattr(instance, "args_schema")
    assert hasattr(instance, "execute")


def test_build_creates_tool_class_incorrect_auth_type(tool_factory, tool_params):
    config.ENV = "test"
    tool_factory.provider_config.configuration.auth_type = "Basic"

    with pytest.raises(ValueError):
        klass = tool_factory.build()
        klass(**tool_params).execute()


@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
@patch("codemie.service.provider.datasource.ProviderDatasourceSchemaService.schema_for")
@patch("codemie.service.provider.util.decrypt_datasource_provider_fields", return_value={})
def test_built_execute_method(
    _mock_decrypt, _mock_schema_get, mock_invoke_tool, mock_api_client, mock_api_config, tool_factory, tool_params
):
    mock_result = MagicMock()
    mock_result.result = "test_result"
    mock_invoke_tool.return_value = mock_result

    tool_class = tool_factory.build()
    tool_instance = tool_class(**tool_params)

    result = tool_instance.execute(param1="test_param1", param2=42)

    assert result == "test_result"
    mock_invoke_tool.assert_called_once_with(
        toolkit_name='test',
        tool_name='test',
        x_correlation_id=ANY,
        tool_invocation_request={
            "user_id": "tool_user",
            "project_id": "tool_project",
            "configuration": {"configuration_type": "tool_invocation", "parameters": {}},
            "parameters": {
                "param1": "test_param1",
                "param2": 42,
            },
            "async": False,
        },
    )


@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
@patch("codemie.service.provider.datasource.ProviderDatasourceSchemaService.schema_for")
@patch("codemie.service.provider.util.decrypt_datasource_provider_fields", return_value={})
def test_built_execute_method_conn_error(
    _mock_decrypt, _mock_schema_get, mock_invoke_tool, mock_api_client, mock_api_config, tool_factory, tool_params
):
    mock_invoke_tool.side_effect = MaxRetryError(url="test", pool=MagicMock())

    tool_class = tool_factory.build()
    tool_instance = tool_class(**tool_params)

    with pytest.raises(ProviderConnectionError):
        tool_instance.execute(param1="test_param1", param2=42)


def test_build_tool_class_args_schema(tool_factory, tool_params):
    tool_class = tool_factory.build()
    args_schema = tool_class(**tool_params).args_schema

    assert args_schema.__name__ == "ArgsSchema"
    assert "param1" in args_schema.__annotations__
    assert "param2" in args_schema.__annotations__
    assert args_schema.__annotations__["param1"] is Optional[str]
    assert args_schema.__annotations__["param2"] is int
