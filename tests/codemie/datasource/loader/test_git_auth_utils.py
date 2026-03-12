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

"""Tests for git authentication utilities."""

import pytest
from unittest.mock import MagicMock, patch

from codemie.datasource.loader.git_auth_utils import get_github_app_token


@pytest.mark.parametrize(
    "app_id,private_key,installation_id,expected_token",
    [
        (
            123456,
            "-----BEGIN RSA PRIVATE KEY-----\ntest_key\n-----END RSA PRIVATE KEY-----",
            789012,
            "ghs_test_token_12345",
        ),
        (
            654321,
            "-----BEGIN RSA PRIVATE KEY-----\ntest_key_2\n-----END RSA PRIVATE KEY-----",
            111222,
            "ghs_another_token",
        ),
        (
            999,
            """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0Z3VS...
MIIEpAIBAAKCAQEA0Z3VS...
-----END RSA PRIVATE KEY-----""",
            456,
            "ghs_multiline_key_token",
        ),
    ],
)
@patch('github.GithubIntegration')
def test_get_github_app_token_with_installation_id(
    mock_integration_class, app_id, private_key, installation_id, expected_token
):
    """Test token generation with provided installation_id."""
    # Arrange
    mock_integration = MagicMock()
    mock_integration_class.return_value = mock_integration

    mock_access_token = MagicMock()
    mock_access_token.token = expected_token
    mock_integration.get_access_token.return_value = mock_access_token

    # Act
    token = get_github_app_token(app_id, private_key, installation_id)

    # Assert
    assert token == expected_token
    mock_integration_class.assert_called_once_with(integration_id=app_id, private_key=private_key)
    mock_integration.get_access_token.assert_called_once_with(installation_id)


@patch('github.GithubIntegration')
def test_get_github_app_token_auto_detect_installation_id(mock_integration_class):
    """Test token generation with auto-detected installation_id."""
    # Arrange
    mock_integration = MagicMock()
    mock_integration_class.return_value = mock_integration

    # Mock installations list
    mock_installation = MagicMock()
    mock_installation.id = 999888
    mock_integration.get_installations.return_value = iter([mock_installation])

    mock_access_token = MagicMock()
    mock_access_token.token = "ghs_auto_detected_token"
    mock_integration.get_access_token.return_value = mock_access_token

    app_id = 654321
    private_key = "-----BEGIN RSA PRIVATE KEY-----\ntest_key_2\n-----END RSA PRIVATE KEY-----"

    # Act
    token = get_github_app_token(app_id, private_key, installation_id=None)

    # Assert
    assert token == "ghs_auto_detected_token"
    mock_integration.get_installations.assert_called_once()
    mock_integration.get_access_token.assert_called_once_with(999888)


@patch('github.GithubIntegration')
def test_get_github_app_token_no_installations_found(mock_integration_class):
    """Test error handling when no installations are found."""
    # Arrange
    mock_integration = MagicMock()
    mock_integration_class.return_value = mock_integration

    # Mock empty installations list
    mock_integration.get_installations.return_value = iter([])

    app_id = 111222
    private_key = "-----BEGIN RSA PRIVATE KEY-----\ntest_key_3\n-----END RSA PRIVATE KEY-----"

    # Act & Assert
    with pytest.raises(ValueError, match="GitHub App authentication failed"):
        get_github_app_token(app_id, private_key, installation_id=None)


@patch('github.GithubIntegration')
def test_get_github_app_token_api_failure(mock_integration_class):
    """Test error handling when GitHub API call fails."""
    # Arrange
    mock_integration = MagicMock()
    mock_integration_class.return_value = mock_integration

    mock_integration.get_access_token.side_effect = Exception("API rate limit exceeded")

    app_id = 333444
    private_key = "-----BEGIN RSA PRIVATE KEY-----\ntest_key_4\n-----END RSA PRIVATE KEY-----"
    installation_id = 555666

    # Act & Assert
    with pytest.raises(ValueError, match="GitHub App authentication failed.*API rate limit exceeded"):
        get_github_app_token(app_id, private_key, installation_id)


def test_get_github_app_token_missing_pygithub():
    """Test error handling when PyGithub is not installed."""
    # Arrange
    app_id = 777888
    private_key = "-----BEGIN RSA PRIVATE KEY-----\ntest_key_5\n-----END RSA PRIVATE KEY-----"
    installation_id = 999000

    # Act & Assert
    # Mock the import to raise ImportError
    import sys

    github_module = sys.modules.get('github')
    if github_module:
        # Temporarily remove github module to simulate it not being installed
        with patch.dict(sys.modules, {'github': None}):
            with pytest.raises(ImportError, match="PyGithub is required for GitHub App authentication"):
                get_github_app_token(app_id, private_key, installation_id)
    else:
        # If github is not imported yet, just verify the function handles ImportError
        pytest.skip("Cannot test ImportError without github module loaded")
