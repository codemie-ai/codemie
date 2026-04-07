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
from codemie.rest_api.models.provider import (
    ProviderBase,
    ProviderDataSourceSchemas,
    ProviderToolkit,
    ProviderToolkitConfigParameter,
)
from codemie.rest_api.security.user import User
from codemie.service.provider.datasource.provider_datasource_schema_service import ProviderDatasourceSchemaService


@pytest.fixture
def mock_user():
    return MagicMock(spec=User)


@pytest.fixture
def mock_provider_toolkit():
    toolkit = MagicMock(spec=ProviderToolkit)
    toolkit.toolkit_id = "TestID"
    toolkit.name = "TestToolkit"
    toolkit.description = "Descriptipn"
    toolkit.toolkit_config = MagicMock(
        description="TestDescription",
        parameters={
            "test_param": ProviderToolkitConfigParameter(
                description="test_param",
                parameter_type=ProviderToolkitConfigParameter.ParameterType.STRING,
                required=False,
            )
        },
    )
    toolkit.provided_tools = []
    toolkit._has_datasource_definition = True

    return toolkit


@pytest.fixture
def mock_provider(mock_provider_toolkit):
    provider = MagicMock(spec=ProviderBase)
    provider.id = "provider-db-id"
    provider.name = "Mock Provider"
    provider.provided_toolkits = [mock_provider_toolkit]
    return provider


@patch('codemie.rest_api.models.provider.Provider.get_all')
def test_get_all(mock_get_all, mock_user, mock_provider):
    mock_get_all.return_value = [mock_provider]
    result = ProviderDatasourceSchemaService.get_all(mock_user)

    assert len(result) == 1
    assert isinstance(result[0], ProviderDataSourceSchemas)
    assert result[0].base_schema.description == "TestDescription"


def test_schema_for_success(mock_provider, mock_user):
    instance = ProviderDatasourceSchemaService(provider=mock_provider, user=mock_user)

    result = instance.schema_for(toolkit_id="TestID", include_autofilled=False)

    assert result.schema_id == 'provider-db-id'
    assert result.toolkit_id == 'TestID'
    assert result.provider_name == 'Mock Provider'
    assert result.name == 'Mock Provider - TestToolkit'
    assert result.base_schema.parameters[0].name == 'test_param'
    assert result.base_schema.parameters[0].description == 'test_param'
    assert result.base_schema.parameters[0].parameter_type.value == 'String'


def test_schema_for_not_ds(mock_provider, mock_user):
    mock_provider.provided_toolkits[0]._has_datasource_definition = False
    instance = ProviderDatasourceSchemaService(provider=mock_provider, user=mock_user)

    with pytest.raises(ValueError):
        instance.schema_for(toolkit_id="TestID", include_autofilled=False)
