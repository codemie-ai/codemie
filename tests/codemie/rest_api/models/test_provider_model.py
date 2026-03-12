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
from unittest.mock import patch, MagicMock
from fastapi.exceptions import RequestValidationError
from pydantic import HttpUrl
from codemie.rest_api.models.provider import Provider, ProviderToolkit, ProviderToolMetadata, ProviderConfiguration

NAME_FIELD = "name.keyword"
ID_FIELD = "id.keyword"


@pytest.fixture
def mock_provider():
    return Provider(
        name="name",
        service_location_url=HttpUrl("http://test.com"),
        configuration=ProviderConfiguration(auth_type=ProviderConfiguration.AuthType.BEARER),
        provided_toolkits=[],
    )


def build_tool(purpose, action_type):
    return ProviderToolkit.Tool(
        name="Mock Tools",
        description="Mock Description",
        args_schema={},
        tool_metadata=ProviderToolMetadata(tool_purpose=purpose, tool_action_type=action_type),
    )


@patch('codemie.rest_api.models.provider.Session')
def test_check_name_is_unique_positive(mock_session_class):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []

    result = Provider.check_name_is_unique("name")

    expected_where_condition = "WHERE providers.name = :name_1"
    mock_session.exec.assert_called_once()
    actual_statement = mock_session.exec.call_args[0][0]

    assert str(actual_statement).endswith(expected_where_condition)
    assert result is True


@patch('codemie.rest_api.models.provider.Session')
def test_provider_check_name_is_unique_negative(mock_session_class, mock_provider):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_provider]

    result = Provider.check_name_is_unique("name")

    expected_where_condition = "WHERE providers.name = :name_1"
    mock_session.exec.assert_called_once()
    actual_statement = mock_session.exec.call_args[0][0]

    assert str(actual_statement).endswith(expected_where_condition)
    assert result is False


@patch('codemie.rest_api.models.provider.Session')
def test_provider_check_name_is_unique_positive_w_id(mock_session_class):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []
    result = Provider.check_name_is_unique("name", "id")

    expected_where_condition = "WHERE providers.name = :name_1 AND providers.id != :id_1"
    mock_session.exec.assert_called_once()
    actual_statement = mock_session.exec.call_args[0][0]

    assert str(actual_statement).endswith(expected_where_condition)
    assert result is True


@patch('codemie.rest_api.models.provider.Session')
def test_provider_heck_name_is_unique_negative_w_id(mock_session_class, mock_provider):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_provider]
    result = Provider.check_name_is_unique("name", "id")

    expected_where_condition = "WHERE providers.name = :name_1 AND providers.id != :id_1"
    mock_session.exec.assert_called_once()
    actual_statement = mock_session.exec.call_args[0][0]

    assert str(actual_statement).endswith(expected_where_condition)
    assert result is False


def test_toolkit_sets_datasource_definition():
    toolkit = ProviderToolkit(
        name="Test Toolkit",
        description="A toolkit with lifecycle management",
        toolkit_config=ProviderToolkit.ToolkitConfig(),
        provided_tools=[
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.CREATE),
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.MODIFY),
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.REMOVE),
            build_tool(ProviderToolMetadata.Purpose.DATA_RETRIEVAL, None),
        ],
    )

    assert toolkit._has_datasource_definition is True


def test_toolkit_does_not_set_datasource_definition():
    toolkit = ProviderToolkit(
        name="Test Toolkit 2",
        description="A toolkit without lifecycle management 2",
        toolkit_config=ProviderToolkit.ToolkitConfig(),
        provided_tools=[build_tool(None, None)],
    )

    assert toolkit._has_datasource_definition is False


def test_toolkit_validates_retrieval_tools():
    with pytest.raises(RequestValidationError):
        ProviderToolkit(
            name="Test Toolkit 3",
            description="A toolkit with lifecycle management 3",
            toolkit_config=ProviderToolkit.ToolkitConfig(),
            provided_tools=[
                build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.CREATE),
                build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.MODIFY),
                build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.REMOVE),
                build_tool(None, None),
            ],
        )


def test_toolkit_validates_lifecycle_tools():
    with pytest.raises(RequestValidationError):
        ProviderToolkit(
            name="Test Toolkit 4",
            description="A toolkit with lifecycle management 4",
            toolkit_config=ProviderToolkit.ToolkitConfig(),
            provided_tools=[
                build_tool(ProviderToolMetadata.Purpose.DATA_RETRIEVAL, None),
            ],
        )


def test_provider_toolkit_sets_datasource_definition_pos():
    toolkit = ProviderToolkit(
        name="Test Toolkit",
        description="A toolkit with lifecycle management",
        toolkit_config=ProviderToolkit.ToolkitConfig(),
        provided_tools=[
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.CREATE),
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.MODIFY),
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.REMOVE),
            build_tool(ProviderToolMetadata.Purpose.DATA_RETRIEVAL, None),
        ],
    )

    assert toolkit._has_datasource_definition is True


def test_provider_toolkit_does_not_set_datasource_definition_neg():
    toolkit = ProviderToolkit(
        name="Test Toolkit 2",
        description="A toolkit without lifecycle management 2",
        toolkit_config=ProviderToolkit.ToolkitConfig(),
        provided_tools=[build_tool(None, None)],
    )

    assert toolkit._has_datasource_definition is False


def test_provider_toolkit_validates_retrieval_tools_neg():
    with pytest.raises(RequestValidationError):
        ProviderToolkit(
            name="Test Toolkit 3",
            description="A toolkit with lifecycle management 3",
            toolkit_config=ProviderToolkit.ToolkitConfig(),
            provided_tools=[
                build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.CREATE),
                build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.MODIFY),
                build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.REMOVE),
                build_tool(None, None),
            ],
        )


def test_provider_toolkit_validates_lifecycle_tools_pos():
    toolkit = ProviderToolkit(
        name="Test Toolkit 4",
        description="A toolkit with lifecycle management 4",
        toolkit_config=ProviderToolkit.ToolkitConfig(),
        provided_tools=[
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.CREATE),
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.MODIFY),
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.REMOVE),
            build_tool(ProviderToolMetadata.Purpose.DATA_RETRIEVAL, None),
        ],
    )

    assert toolkit


def test_provider_toolkit_validates_lifecycle_actions_neg():
    with pytest.raises(RequestValidationError):
        ProviderToolkit(
            name="Test Toolkit 5",
            description="A toolkit with lifecycle management 5",
            toolkit_config=ProviderToolkit.ToolkitConfig(),
            provided_tools=[
                build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.CREATE),
                build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.MODIFY),
                build_tool(ProviderToolMetadata.Purpose.DATA_RETRIEVAL, None),
            ],
        )


def test_provider_toolkit_validates_lifecycle_actions_pos():
    toolkit = ProviderToolkit(
        name="Test Toolkit 6",
        description="A toolkit with lifecycle management 6",
        toolkit_config=ProviderToolkit.ToolkitConfig(),
        provided_tools=[
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.CREATE),
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.MODIFY),
            build_tool(ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT, ProviderToolMetadata.ActionType.REMOVE),
            build_tool(ProviderToolMetadata.Purpose.DATA_RETRIEVAL, None),
        ],
    )

    assert toolkit
