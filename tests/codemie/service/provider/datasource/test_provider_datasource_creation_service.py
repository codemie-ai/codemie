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

from codemie.service.provider.datasource import ProviderDatasourceCreationService
from codemie.rest_api.models.provider import (
    ProviderDataSourceSchemas,
    ProviderDataSourceTypeSchema,
    ProviderToolArgument,
)
from codemie.rest_api.security.user import User


@pytest.fixture
def values():
    return {
        "name": "name",
        "project_name": "project_name",
        "description": "description",
        "project_space_visible": True,
    }


@pytest.fixture
def mock_user() -> MagicMock:
    mock_user = MagicMock(spec=User)
    mock_user.id = "example_user_id"
    mock_user.username = "example_username"
    mock_user.name = "example_name"
    return mock_user


def schema_mock():
    return ProviderDataSourceSchemas(
        id="toolidkit_id",
        toolkit_id="toolkit_id",
        name="name",
        provider_name="provider_name",
        base_schema=ProviderDataSourceTypeSchema(
            description="description",
            parameters=[
                ProviderDataSourceTypeSchema.Parameter(
                    name="arg1",
                    description="description",
                    required=True,
                    parameter_type=ProviderToolArgument.ArgType.STRING,
                )
            ],
        ),
        create_schema=ProviderDataSourceTypeSchema(
            description="description",
            parameters=[
                ProviderDataSourceTypeSchema.Parameter(
                    name="arg2",
                    description="description",
                    required=True,
                    parameter_type=ProviderToolArgument.ArgType.STRING,
                )
            ],
        ),
        update_schema=ProviderDataSourceTypeSchema(
            description="description",
            parameters=[
                ProviderDataSourceTypeSchema.Parameter(
                    name="arg3",
                    description="description",
                    required=True,
                    parameter_type=ProviderToolArgument.ArgType.STRING,
                )
            ],
        ),
    )


@pytest.fixture
@patch(
    "codemie.service.provider.datasource.provider_datasource_schema_service.ProviderDatasourceSchemaService.schema_for"
)
def mock_instance(mock_schema, mock_user):
    mock_schema.return_value = schema_mock()
    mock_provider = MagicMock()
    mock_provider.id = "provider_id"

    return ProviderDatasourceCreationService(
        provider=mock_provider,
        toolkit_id="toolkit_id",
        values={
            "name": "name",
            "project_name": "project_name",
            "description": "description",
            "project_space_visible": True,
            "arg1": "arg1",
            "arg2": "arg2",
        },
        user=mock_user,
    )


@patch("codemie.service.provider.datasource.provider_datasource_creation_service.ensure_application_exists")
@patch("codemie.rest_api.models.index.IndexInfo.save")
@patch("codemie.rest_api.models.index.IndexInfo.complete_progress")
@patch("codemie.service.provider.datasource.provider_datasource_adapter.ProviderDatasourceAdapter.create")
@patch(
    "codemie.service.provider.datasource.provider_datasource_base_service.ProviderDatasourceBaseService._find_toolkit"
)
def test_run(mock_find_tookit, mock_create, mock_complete_progress, mock_save_index, mock_ensure_app, mock_instance):
    mock_find_tookit.return_value = MagicMock()
    mock_adapter_result = MagicMock()
    mock_adapter_result.errors = None
    mock_adapter_result.status = "Completed"
    mock_create.return_value = mock_adapter_result

    mock_instance.run()

    mock_complete_progress.assert_called_once()
    mock_create.assert_called_once_with(base_params={"arg1": "arg1"}, create_params={"arg2": "arg2"})
    mock_ensure_app.assert_called_once_with("project_name")
