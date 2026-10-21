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
    ProviderToolkitConfigParameter,
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


def _param(name: str, parameter_type=ProviderToolkitConfigParameter.ParameterType.STRING, required: bool = False):
    return ProviderDataSourceTypeSchema.Parameter(
        name=name, description="description", required=required, parameter_type=parameter_type
    )


def _schemas(base_parameters: list, create_parameters: list | None = None) -> ProviderDataSourceSchemas:
    return ProviderDataSourceSchemas(
        id="provider_id",
        toolkit_id="toolkit_id",
        name="name",
        provider_name="provider_name",
        base_schema=ProviderDataSourceTypeSchema(description="description", parameters=base_parameters),
        create_schema=ProviderDataSourceTypeSchema(description="description", parameters=create_parameters or []),
    )


def _run_update(mock_user, schemas, base_params: dict, values: dict, create_params: dict | None = None) -> MagicMock:
    mock_provider = MagicMock()
    mock_provider.provided_toolkits = []

    datasource = _make_datasource()
    datasource.provider_fields.base_params = base_params
    datasource.provider_fields.create_params = create_params or {}
    datasource.update = MagicMock()

    with (
        patch(
            "codemie.service.provider.datasource.provider_datasource_schema_service.ProviderDatasourceSchemaService.schema_for",
            return_value=schemas,
        ),
        patch(
            "codemie.service.provider.datasource.provider_datasource_base_service.ProviderDatasourceBaseService._find_toolkit",
            return_value=MagicMock(get_managed_fields=lambda: {}),
        ),
        patch("codemie.service.provider.util.encryption_service") as mock_encryption,
    ):
        mock_encryption.encrypt.side_effect = lambda value: f"encrypted:{value}"
        service = ProviderDatasourceUpdateService(
            datasource=datasource, provider=mock_provider, values=values, user=mock_user
        )
        assert service.run() is True

    datasource.update.assert_called_once()
    return datasource


def test_run_drops_base_params_removed_from_schema(mock_user):
    datasource = _run_update(
        mock_user,
        _schemas([_param("cql"), _param("intent_recognition_prompt_with_memory")]),
        base_params={
            "cql": "label = old",
            "intent_recognition_prompt_with_memory": "prompt",
            "datasource_ids": "2821378733",
            "recognition_prompt_with_memory": "stale prompt",
        },
        values={"cql": "label = new", "intent_recognition_prompt_with_memory": "prompt"},
    )

    assert datasource.provider_fields.base_params == {
        "cql": "label = new",
        "intent_recognition_prompt_with_memory": "prompt",
    }


def test_run_keeps_stored_value_of_schema_field_missing_from_request(mock_user):
    datasource = _run_update(
        mock_user,
        _schemas([_param("cql"), _param("route_resolution_top_k")]),
        base_params={"cql": "label = old", "route_resolution_top_k": "8"},
        values={"cql": "label = new"},
    )

    assert datasource.provider_fields.base_params == {"cql": "label = new", "route_resolution_top_k": "8"}


def test_run_keeps_autofilled_uuid(mock_user):
    datasource = _run_update(
        mock_user,
        _schemas(
            [_param("index_uid", ProviderToolkitConfigParameter.ParameterType.UUID, required=True), _param("cql")]
        ),
        base_params={"index_uid": "7f3e-uuid", "cql": "label = old"},
        values={"cql": "label = new"},
    )

    assert datasource.provider_fields.base_params["index_uid"] == "7f3e-uuid"


def test_run_does_not_overwrite_masked_secret(mock_user):
    datasource = _run_update(
        mock_user,
        _schemas([_param("url"), _param("token", ProviderToolkitConfigParameter.ParameterType.SECRET, required=True)]),
        base_params={"url": "https://old", "token": "encrypted:stored-secret"},
        values={"url": "https://new", "token": ProviderDatasourceUpdateService.MASKED_VALUE},
    )

    assert datasource.provider_fields.base_params == {"url": "https://new", "token": "encrypted:stored-secret"}


def test_run_keeps_imported_datasource_id(mock_user):
    datasource = _run_update(
        mock_user,
        _schemas([_param("cql")]),
        base_params={"datasource_id": "external-id", "cql": "label = old"},
        values={"cql": "label = new"},
    )

    assert datasource.provider_fields.base_params == {"datasource_id": "external-id", "cql": "label = new"}


def test_run_drops_create_params_removed_from_schema(mock_user):
    datasource = _run_update(
        mock_user,
        _schemas([_param("cql")], create_parameters=[_param("page_limit")]),
        base_params={"cql": "label = old"},
        create_params={"page_limit": "10", "removed_arg": "stale"},
        values={"cql": "label = new", "page_limit": "20"},
    )

    assert datasource.provider_fields.create_params == {"page_limit": "20"}


def test_run_keeps_create_params_when_create_schema_has_no_parameters(mock_user):
    datasource = _run_update(
        mock_user,
        _schemas([_param("cql")]),
        base_params={"cql": "label = old"},
        create_params={"legacy_arg": "kept"},
        values={"cql": "label = new"},
    )

    assert datasource.provider_fields.create_params == {"legacy_arg": "kept"}


def test_run_keeps_base_params_when_base_schema_has_no_parameters(mock_user):
    datasource = _run_update(
        mock_user,
        _schemas([]),
        base_params={"legacy_param": "kept"},
        values={},
    )

    assert datasource.provider_fields.base_params == {"legacy_param": "kept"}


def test_keep_schema_fields_returns_params_unchanged_without_schema():
    params = {"datasource_ids": "2821378733", "cql": "label = old"}

    assert ProviderDatasourceUpdateService._keep_schema_fields(params, schema=None) == params
