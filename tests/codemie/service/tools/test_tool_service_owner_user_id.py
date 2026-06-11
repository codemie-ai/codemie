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

"""Unit tests for ToolsService.find_setting_for_tool owner_user_id propagation."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.security.user import User
from codemie.service.settings.base_settings import SearchFields
from codemie.service.tools.tool_service import ToolsService


@pytest.fixture
def executor_user() -> User:
    user = MagicMock(spec=User)
    user.id = "executor-user-id"
    return user


@patch("codemie.service.settings.settings.SettingsService.retrieve_setting")
def test_find_setting_uses_owner_user_id_when_provided(
    mock_retrieve: MagicMock,
    executor_user: User,
) -> None:
    """lookup_user_id must be owner_user_id, not executor user.id, when owner_user_id is given."""
    # Arrange
    mock_retrieve.return_value = MagicMock()

    # Act
    result = ToolsService.find_setting_for_tool(
        user=executor_user,
        project_name="test-project",
        integration_alias="my-github",
        owner_user_id="publisher-user-id",
    )

    # Assert
    assert result is mock_retrieve.return_value
    args_passed = mock_retrieve.call_args[0][0]
    assert args_passed[SearchFields.USER_ID] == "publisher-user-id"
    assert args_passed[SearchFields.USER_ID] != executor_user.id


@patch("codemie.service.settings.settings.SettingsService.retrieve_setting")
def test_find_setting_uses_executor_user_id_when_no_owner(
    mock_retrieve: MagicMock,
    executor_user: User,
) -> None:
    """lookup_user_id must fall back to user.id when owner_user_id is None."""
    # Arrange
    mock_retrieve.return_value = MagicMock()

    # Act
    result = ToolsService.find_setting_for_tool(
        user=executor_user,
        project_name="test-project",
        integration_alias="my-github",
        owner_user_id=None,
    )

    # Assert
    assert result is mock_retrieve.return_value
    args_passed = mock_retrieve.call_args[0][0]
    assert args_passed[SearchFields.USER_ID] == executor_user.id


@patch("codemie.service.settings.settings.SettingsService.retrieve_setting")
def test_find_setting_passes_project_and_alias(
    mock_retrieve: MagicMock,
    executor_user: User,
) -> None:
    """project_name and integration_alias must always be forwarded to SettingsService."""
    # Arrange
    mock_retrieve.return_value = MagicMock()

    # Act
    ToolsService.find_setting_for_tool(
        user=executor_user,
        project_name="my-project",
        integration_alias="my-alias",
        owner_user_id=None,
    )

    # Assert
    args_passed = mock_retrieve.call_args[0][0]
    assert args_passed[SearchFields.PROJECT_NAME] == "my-project"
    assert args_passed[SearchFields.ALIAS] == "my-alias"
