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

import pytest
from unittest.mock import MagicMock, patch

from codemie.service.provider.datasource.provider_datasource_update_service import (
    ProviderDatasourceUpdateService,
)
from codemie.rest_api.models.provider import (
    ProviderDataSourceSchemas,
    ProviderDataSourceTypeSchema,
    ProviderToolArgument,
)
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user() -> MagicMock:
    mock_user = MagicMock(spec=User)
    mock_user.id = "example_user_id"
    return mock_user


def schema_mock():
    return ProviderDataSourceSchemas(
        id="provider_id",
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
            parameters=[],
        ),
    )


def _make_datasource(project_name: str = "project_name") -> MagicMock:
    datasource = MagicMock()
    datasource.project_name = project_name
    datasource.repo_name = "repo_name"
    datasource.provider_fields.toolkit_id = "toolkit_id"
    datasource.provider_fields.base_params = {}
    datasource.provider_fields.create_params = {}
    return datasource


@pytest.fixture
@patch(
    "codemie.service.provider.datasource.provider_datasource_schema_service.ProviderDatasourceSchemaService.schema_for"
)
def mock_instance(mock_schema, mock_user):
    mock_schema.return_value = schema_mock()
    mock_provider = MagicMock()
    mock_provider.id = "provider_id"
    mock_provider.name = "provider_name"
    mock_provider.provided_toolkits = []

    return ProviderDatasourceUpdateService(
        datasource=_make_datasource(),
        provider=mock_provider,
        values={
            "name": "new_name",
            "project_name": "project_name",
            "description": "new_description",
            "project_space_visible": True,
            "arg1": "value1",
        },
        user=mock_user,
    )


@patch("codemie.service.provider.datasource.provider_datasource_update_service.ensure_application_exists")
@patch(
    "codemie.service.provider.datasource.provider_datasource_base_service.ProviderDatasourceBaseService._find_toolkit"
)
def test_run_updates_base_fields(mock_find_toolkit, mock_ensure_app, mock_instance):
    mock_find_toolkit.return_value = MagicMock(get_managed_fields=lambda: {})
    mock_instance.datasource.update = MagicMock()

    result = mock_instance.run()

    assert result is True
    assert mock_instance.datasource.repo_name == "new_name"
    assert mock_instance.datasource.description == "new_description"
    mock_ensure_app.assert_not_called()
    mock_instance.datasource.update.assert_called_once()


@patch("codemie.service.provider.datasource.provider_datasource_update_service.ensure_application_exists")
@patch(
    "codemie.service.provider.datasource.provider_datasource_base_service.ProviderDatasourceBaseService._find_toolkit"
)
def test_run_respects_new_project_name(mock_find_toolkit, mock_ensure_app, mock_user):
    with patch(
        "codemie.service.provider.datasource.provider_datasource_schema_service.ProviderDatasourceSchemaService.schema_for",
        return_value=schema_mock(),
    ):
        mock_provider = MagicMock()
        mock_provider.id = "provider_id"
        mock_provider.name = "provider_name"
        mock_provider.provided_toolkits = []

        datasource = _make_datasource(project_name="old_project")
        datasource.update = MagicMock()

        service = ProviderDatasourceUpdateService(
            datasource=datasource,
            provider=mock_provider,
            values={
                "name": "name",
                "project_name": "old_project",
                "new_project_name": "new_project",
                "description": "desc",
                "project_space_visible": False,
                "arg1": "value1",
            },
            user=mock_user,
        )

    mock_find_toolkit.return_value = MagicMock(get_managed_fields=lambda: {})

    result = service.run()

    assert result is True
    assert datasource.project_name == "new_project"
    mock_ensure_app.assert_called_once_with("new_project")
    datasource.update.assert_called_once()


@patch("codemie.service.provider.datasource.provider_datasource_update_service.ensure_application_exists")
@patch(
    "codemie.service.provider.datasource.provider_datasource_base_service.ProviderDatasourceBaseService._find_toolkit"
)
def test_run_without_new_project_name_keeps_existing(mock_find_toolkit, mock_ensure_app, mock_user):
    with patch(
        "codemie.service.provider.datasource.provider_datasource_schema_service.ProviderDatasourceSchemaService.schema_for",
        return_value=schema_mock(),
    ):
        mock_provider = MagicMock()
        mock_provider.id = "provider_id"
        mock_provider.name = "provider_name"
        mock_provider.provided_toolkits = []

        datasource = _make_datasource(project_name="original_project")
        datasource.update = MagicMock()

        service = ProviderDatasourceUpdateService(
            datasource=datasource,
            provider=mock_provider,
            values={
                "name": "name",
                "project_name": "original_project",
                "description": "desc",
                "project_space_visible": False,
                "arg1": "value1",
            },
            user=mock_user,
        )

    mock_find_toolkit.return_value = MagicMock(get_managed_fields=lambda: {})

    result = service.run()

    assert result is True
    mock_ensure_app.assert_not_called()
    datasource.update.assert_called_once()
