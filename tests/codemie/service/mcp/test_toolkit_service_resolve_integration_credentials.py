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
from unittest.mock import Mock, patch

from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.settings.settings import SettingsService
from codemie.service.settings.base_settings import SearchFields


@pytest.mark.parametrize(
    "test_name,credential_values,expected_result",
    [
        (
            "test_resolve_integration_credentials_with_valid_settings",
            [
                Mock(key="API_KEY", value="test-api-key"),
                Mock(key="SECRET", value="test-secret"),
                Mock(key="ENV_VAR", value="test-value"),
            ],
            {"API_KEY": "test-api-key", "SECRET": "test-secret", "ENV_VAR": "test-value"},
        ),
        (
            "test_resolve_integration_credentials_with_empty_credentials",
            [],
            {},
        ),
    ],
)
def test_resolve_integration_credentials_with_settings(test_name, credential_values, expected_result):
    """
    Tests the _resolve_credentials_by_id function with different credential value configurations.
    Verifies that environment variables are properly extracted from credential values.
    """
    # Setup
    integration_id = "test-settings-id"
    user_id = "test-user"

    mock_settings = Mock(credential_values=credential_values)

    # Mock the settings service
    with patch.object(SettingsService, 'retrieve_setting', return_value=mock_settings) as mock_retrieve:
        # Execute
        result = MCPToolkitService._resolve_credentials_by_id(integration_id, user_id)

        # Verify
        assert result == expected_result
        mock_retrieve.assert_called_once_with(search_fields={SearchFields.USER_ID: user_id}, setting_id=integration_id)


def test_resolve_integration_credentials_with_missing_settings():
    """
    Tests the _resolve_credentials_by_id function when the settings retrieval returns None.
    Verifies that the function handles this case gracefully by returning an empty dict.
    """
    # Setup
    integration_id = "test-settings-id"
    user_id = "test-user"

    # Mock the settings service to return None
    with patch.object(SettingsService, 'retrieve_setting', return_value=None) as mock_retrieve:
        # Execute
        result = MCPToolkitService._resolve_credentials_by_id(integration_id, user_id)

        # Verify
        assert result == {}
        mock_retrieve.assert_called_once_with(search_fields={SearchFields.USER_ID: user_id}, setting_id=integration_id)


def test_resolve_integration_credentials_without_user_id():
    """
    Tests the _resolve_credentials_by_id function when user_id is None.
    Verifies that the function handles this case properly.
    """
    # Setup
    integration_id = "test-settings-id"
    user_id = None

    mock_settings = Mock(
        credential_values=[
            Mock(key="API_KEY", value="test-api-key"),
            Mock(key="SECRET", value="test-secret"),
        ]
    )

    # Mock the settings service
    with patch.object(SettingsService, 'retrieve_setting', return_value=mock_settings) as mock_retrieve:
        # Execute
        result = MCPToolkitService._resolve_credentials_by_id(integration_id, user_id)

        # Verify
        expected_result = {"API_KEY": "test-api-key", "SECRET": "test-secret"}
        assert result == expected_result
        mock_retrieve.assert_called_once_with(search_fields={}, setting_id=integration_id)


def test_resolve_integration_credentials_handles_exceptions():
    """
    Tests the _resolve_credentials_by_id function when an exception occurs.
    Verifies that the function allows exceptions to propagate as expected.
    """
    # Setup
    integration_id = "test-settings-id"
    user_id = "test-user"

    # Mock the settings service to raise an exception
    with patch.object(SettingsService, 'retrieve_setting', side_effect=Exception("Database error")) as mock_retrieve:
        # Execute and verify exception is raised
        with pytest.raises(Exception, match="Database error"):
            MCPToolkitService._resolve_credentials_by_id(integration_id, user_id)

        # Verify the service was called correctly
        mock_retrieve.assert_called_once_with(search_fields={SearchFields.USER_ID: user_id}, setting_id=integration_id)
