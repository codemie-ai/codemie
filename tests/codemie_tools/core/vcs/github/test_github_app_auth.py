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

"""Tests for GitHub App authentication in core VCS GitHub tools."""

import pytest
from unittest.mock import Mock, patch

from codemie_tools.core.vcs.github.models import GithubConfig
from codemie_tools.core.vcs.github.github_client import GithubClient
from langchain_core.tools import ToolException


# ===== GithubConfig Validation Tests =====


@pytest.mark.parametrize(
    "config_data,should_pass,expected_error",
    [
        # Valid PAT authentication
        ({"token": "ghp_test123"}, True, None),
        # Valid GitHub App authentication
        ({"app_id": 123456, "private_key": "test_key"}, True, None),
        # Valid GitHub App with installation_id
        ({"app_id": 123456, "private_key": "test_key", "installation_id": 12345}, True, None),
        # Invalid: Both PAT and GitHub App
        (
            {"token": "ghp_test", "app_id": 123456, "private_key": "test_key"},
            False,
            "Cannot use both PAT and GitHub App authentication",
        ),
        # Invalid: No authentication
        ({}, False, "Authentication required"),
        # Invalid: Partial GitHub App (only app_id)
        ({"app_id": 123456}, False, "GitHub App authentication requires both 'app_id' and 'private_key'"),
        # Invalid: Partial GitHub App (only private_key)
        ({"private_key": "test_key"}, False, "GitHub App authentication requires both 'app_id' and 'private_key'"),
    ],
)
def test_github_config_validation(config_data, should_pass, expected_error):
    """Test GithubConfig validates authentication methods correctly."""
    if should_pass:
        config = GithubConfig(**config_data)
        assert config is not None
        if "app_id" in config_data:
            assert config.is_github_app is True
        else:
            assert config.is_github_app is False
    else:
        with pytest.raises(ValueError) as exc_info:
            GithubConfig(**config_data)
        assert expected_error in str(exc_info.value)


def test_github_config_is_github_app_property():
    """Test is_github_app property correctly identifies GitHub App auth."""
    # PAT config
    pat_config = GithubConfig(token="ghp_test")
    assert pat_config.is_github_app is False

    # GitHub App config
    app_config = GithubConfig(app_id=123, private_key="key")
    assert app_config.is_github_app is True


# ===== GithubClient Tests =====


def test_github_client_pat_authentication():
    """Test GithubClient returns PAT token directly."""
    config = GithubConfig(token="ghp_test_token")
    client = GithubClient(config)

    token = client.get_auth_token()

    assert token == "ghp_test_token"


@patch('github.GithubIntegration')
def test_github_client_github_app_with_installation_id(mock_integration_class):
    """Test GithubClient generates GitHub App token with provided installation_id."""
    # Setup mocks
    mock_integration = Mock()
    mock_access_token = Mock()
    mock_access_token.token = "ghs_installation_token"
    mock_integration.get_access_token.return_value = mock_access_token
    mock_integration_class.return_value = mock_integration

    # Create config with installation_id
    config = GithubConfig(app_id=123456, private_key="test_private_key", installation_id=12345678)
    client = GithubClient(config)

    # Get token
    token = client.get_auth_token()

    # Verify
    assert token == "ghs_installation_token"
    mock_integration_class.assert_called_once_with(integration_id=123456, private_key="test_private_key")
    mock_integration.get_access_token.assert_called_once_with(12345678)


@patch('github.GithubIntegration')
def test_github_client_github_app_auto_detect_installation(mock_integration_class):
    """Test GithubClient auto-detects installation_id when not provided."""
    # Setup mocks
    mock_installation = Mock()
    mock_installation.id = 99999999

    mock_integration = Mock()
    mock_integration.get_installations.return_value = iter([mock_installation])
    mock_access_token = Mock()
    mock_access_token.token = "ghs_auto_detected_token"
    mock_integration.get_access_token.return_value = mock_access_token

    mock_integration_class.return_value = mock_integration

    # Create config without installation_id
    config = GithubConfig(app_id=123456, private_key="test_private_key")
    client = GithubClient(config)

    # Get token
    token = client.get_auth_token()

    # Verify
    assert token == "ghs_auto_detected_token"
    mock_integration.get_installations.assert_called_once()
    mock_integration.get_access_token.assert_called_once_with(99999999)


@patch('github.GithubIntegration')
def test_github_client_github_app_no_installations(mock_integration_class):
    """Test GithubClient raises error when no installations found."""
    # Setup mocks
    mock_integration = Mock()
    mock_integration.get_installations.return_value = iter([])  # Empty iterator
    mock_integration_class.return_value = mock_integration

    # Create config without installation_id
    config = GithubConfig(app_id=123456, private_key="test_private_key")
    client = GithubClient(config)

    # Should raise ToolException
    with pytest.raises(ToolException) as exc_info:
        client.get_auth_token()

    assert "No GitHub App installations found" in str(exc_info.value)


@patch('github.GithubIntegration')
def test_github_client_token_caching(mock_integration_class):
    """Test GithubClient caches GitHub App token for 1 hour."""
    # Setup mocks
    mock_integration = Mock()
    mock_access_token = Mock()
    mock_access_token.token = "ghs_cached_token"
    mock_integration.get_access_token.return_value = mock_access_token
    mock_integration_class.return_value = mock_integration

    config = GithubConfig(app_id=123456, private_key="test_private_key", installation_id=12345678)
    client = GithubClient(config)

    # First call - should generate token
    token1 = client.get_auth_token()
    assert token1 == "ghs_cached_token"
    assert mock_integration.get_access_token.call_count == 1

    # Second call - should use cached token
    token2 = client.get_auth_token()
    assert token2 == "ghs_cached_token"
    assert mock_integration.get_access_token.call_count == 1  # Not called again

    # Verify cache is set
    assert client._installation_token == "ghs_cached_token"
    assert client._token_expires_at is not None


@patch('github.GithubIntegration')
@patch('codemie_tools.core.vcs.github.github_client.time')
def test_github_client_token_refresh_on_expiration(mock_time, mock_integration_class):
    """Test GithubClient refreshes token when cache expires."""
    # Setup mocks
    mock_integration = Mock()
    mock_access_token1 = Mock()
    mock_access_token1.token = "ghs_token_1"
    mock_access_token2 = Mock()
    mock_access_token2.token = "ghs_token_2"

    mock_integration.get_access_token.side_effect = [mock_access_token1, mock_access_token2]
    mock_integration_class.return_value = mock_integration

    # Mock time to simulate expiration
    mock_time.time.side_effect = [1000.0, 4700.0]  # Second call is after expiration

    config = GithubConfig(app_id=123456, private_key="test_private_key", installation_id=12345678)
    client = GithubClient(config)

    # First call
    token1 = client.get_auth_token()
    assert token1 == "ghs_token_1"

    # Second call after expiration - should generate new token
    token2 = client.get_auth_token()
    assert token2 == "ghs_token_2"
    assert mock_integration.get_access_token.call_count == 2


@patch('codemie_tools.core.vcs.github.github_client.requests')
def test_github_client_make_request_with_pat(mock_requests):
    """Test GithubClient.make_request uses PAT token correctly."""
    # Setup
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": "test"}
    mock_requests.request.return_value = mock_response

    config = GithubConfig(token="ghp_test_token")
    client = GithubClient(config)

    # Make request
    result = client.make_request(
        method="GET", url="https://api.github.com/repos/test/repo", headers={"Accept": "application/json"}
    )

    # Verify
    assert result == {"data": "test"}
    call_args = mock_requests.request.call_args
    assert call_args[1]["headers"]["Authorization"] == "Bearer ghp_test_token"


@patch('github.GithubIntegration')
@patch('codemie_tools.core.vcs.github.github_client.requests')
def test_github_client_make_request_with_github_app(mock_requests, mock_integration_class):
    """Test GithubClient.make_request uses GitHub App token correctly."""
    # Setup GitHub App mocks
    mock_integration = Mock()
    mock_access_token = Mock()
    mock_access_token.token = "ghs_app_token"
    mock_integration.get_access_token.return_value = mock_access_token
    mock_integration_class.return_value = mock_integration

    # Setup request mock
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": "test"}
    mock_requests.request.return_value = mock_response

    config = GithubConfig(app_id=123456, private_key="test_key", installation_id=12345678)
    client = GithubClient(config)

    # Make request
    result = client.make_request(
        method="GET", url="https://api.github.com/repos/test/repo", headers={"Accept": "application/json"}
    )

    # Verify
    assert result == {"data": "test"}
    call_args = mock_requests.request.call_args
    assert call_args[1]["headers"]["Authorization"] == "Bearer ghs_app_token"
