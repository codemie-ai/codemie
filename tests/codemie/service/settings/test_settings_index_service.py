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

from codemie.rest_api.security.user import User
from codemie.service.settings.settings_index_service import SettingsIndexService
from codemie.rest_api.models.settings import Settings, SettingType


@pytest.fixture
def mock_settings():
    return Settings(
        id="test-id",
        project_name="test_integration",
        credential_type="Jira",
        credential_values=[{"key": "token", "value": "http://test.com"}],
        setting_type=SettingType.USER,
    )


@patch("codemie.service.settings.settings_index_service.Session")
def test_run_with_user_settings(mock_session_class, mock_settings):
    # Given
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_settings]
    mock_session.exec.return_value.one.return_value = 1
    user = User(id='user_id')
    # When
    result = SettingsIndexService.run(
        user=user,
        settings_type=SettingType.USER,
        page=3,
        per_page=20,
        filters={"search": "test_integration", "type": "Jira"},
    )

    # Then
    assert len(result["data"]) == 1
    assert result["data"][0].project_name == "test_integration"
    assert result["data"][0].credential_values[0].value == "**********"
    assert result["pagination"] == {"page": 3, "per_page": 20, "total": 1, "pages": 1}
    mock_session_class.assert_called_once_with(Settings.get_engine())


@patch("codemie.service.settings.settings_index_service.Session")
def test_run_with_project_settings(mock_session_class, mock_settings):
    # Given
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_settings]
    mock_session.exec.return_value.one.return_value = 1
    user = User(id='user_id')
    # When
    result = SettingsIndexService.run(
        user=user,
        settings_type=SettingType.PROJECT,
        page=3,
        per_page=20,
        filters={"search": "test_integration", "type": "Jira"},
    )

    # Then
    assert len(result["data"]) == 1
    assert result["data"][0].project_name == "test_integration"
    assert result["data"][0].credential_values[0].value == "**********"
    assert result["pagination"] == {"page": 3, "per_page": 20, "total": 1, "pages": 1}
    mock_session_class.assert_called_once_with(Settings.get_engine())


@patch("codemie.service.settings.settings_index_service.Session")
def test_get_users_for_project_settings_admin(mock_session_class):
    """Test get_users() returns all users for project settings when user is admin"""
    from codemie.core.models import CreatedByUser

    # Given
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    # Mock CreatedByUser objects (as they would be returned from the database)
    mock_users = [
        CreatedByUser(id="user1", username="user1@example.com", name="User One"),
        CreatedByUser(id="user2", username="user2@example.com", name="User Two"),
        CreatedByUser(id="user3", username="user3@example.com", name="User Three"),
    ]
    mock_session.exec.return_value.all.return_value = mock_users

    admin_user = User(id="admin", roles=["admin"])

    # When
    result = SettingsIndexService.get_users(user=admin_user, settings_type=SettingType.PROJECT)

    # Then
    assert len(result) == 3
    assert all(isinstance(user, CreatedByUser) for user in result)
    assert result[0].id == "user1"
    assert result[0].name == "User One"
    assert result[0].username == "user1@example.com"

    # Verify query was called
    mock_session.exec.assert_called_once()
    actual_query = str(mock_session.exec.call_args[0][0])
    assert "SELECT DISTINCT settings.created_by" in actual_query
    assert "settings.setting_type = :setting_type_1" in actual_query


@patch("codemie.service.settings.settings_index_service.Session")
def test_get_users_for_project_settings_app_admin(mock_session_class):
    """Test get_users() filters by applications_admin for non-admin users"""
    from codemie.core.models import CreatedByUser

    # Given
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    # Mock CreatedByUser objects
    mock_users = [
        CreatedByUser(id="user1", username="user1@example.com", name="User One"),
        CreatedByUser(id="user2", username="user2@example.com", name="User Two"),
    ]
    mock_session.exec.return_value.all.return_value = mock_users

    from codemie.configs import config as _cfg

    with patch.object(_cfg, 'ENV', 'dev'), patch.object(_cfg, 'ENABLE_USER_MANAGEMENT', True):
        regular_user = User(id="user", roles=["user"], admin_project_names=["demo", "project1"], is_admin=False)

    # When
    result = SettingsIndexService.get_users(user=regular_user, settings_type=SettingType.PROJECT)

    # Then
    assert len(result) == 2
    assert all(isinstance(user, CreatedByUser) for user in result)

    # Verify query includes project filter for non-admin
    actual_query = str(mock_session.exec.call_args[0][0])
    assert "settings.project_name" in actual_query


@patch("codemie.service.settings.settings_index_service.Session")
def test_get_users_for_user_settings(mock_session_class):
    """Test get_users() returns only current user for USER settings"""
    from codemie.core.models import CreatedByUser

    # Given
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    # Mock CreatedByUser object
    mock_user = CreatedByUser(id="current_user", username="current@example.com", name="Current User")
    mock_session.exec.return_value.all.return_value = [mock_user]

    user = User(id="current_user", roles=["user"])

    # When
    result = SettingsIndexService.get_users(user=user, settings_type=SettingType.USER)

    # Then
    assert len(result) == 1
    assert result[0].id == "current_user"

    # Verify query filters by current user
    actual_query = str(mock_session.exec.call_args[0][0])
    # Check that the query includes the user_id filter (parameter name may vary)
    assert "settings.user_id = :user_id_" in actual_query or "settings.user_id = :param_" in actual_query


@patch("codemie.service.settings.settings_index_service.Session")
def test_get_users_filters_out_none_values(mock_session_class):
    """Test get_users() filters out None and users with empty names"""
    from codemie.core.models import CreatedByUser

    # Given
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    # Mock response with None and users with empty names
    mock_users = [
        CreatedByUser(id="user1", username="user1@example.com", name="User One"),
        None,  # Should be filtered out
        CreatedByUser(id="user2", username="user2@example.com", name="User Two"),
        CreatedByUser(id="user3", username="user3@example.com", name=""),  # Empty name - should be filtered out
        CreatedByUser(id="user4", username="user4@example.com", name="User Four"),
    ]
    mock_session.exec.return_value.all.return_value = mock_users

    admin_user = User(id="admin", roles=["admin"])

    # When
    result = SettingsIndexService.get_users(user=admin_user, settings_type=SettingType.PROJECT)

    # Then
    # Should only have user1, user2, user4 (None and empty name filtered out)
    assert len(result) == 3
    assert all(user.id and user.name for user in result)  # All should have non-empty ids and names


@patch("codemie.service.settings.settings_index_service.Session")
def test_get_users_sorted_by_name(mock_session_class):
    """Test get_users() returns users sorted by name"""
    from codemie.core.models import CreatedByUser

    # Given
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    # Return users in non-alphabetical order
    mock_users = [
        CreatedByUser(id="user3", username="zebra@example.com", name="Zebra User"),
        CreatedByUser(id="user1", username="apple@example.com", name="Apple User"),
        CreatedByUser(id="user2", username="banana@example.com", name="Banana User"),
    ]
    mock_session.exec.return_value.all.return_value = mock_users

    admin_user = User(id="admin", roles=["admin"])

    # When
    result = SettingsIndexService.get_users(user=admin_user, settings_type=SettingType.PROJECT)

    # Then
    # Should be sorted alphabetically by name
    assert len(result) == 3
    assert result[0].name == "Apple User"
    assert result[1].name == "Banana User"
    assert result[2].name == "Zebra User"
