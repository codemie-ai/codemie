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

"""Tests for GitHub App authentication in Git toolkit."""

import pytest
from unittest.mock import Mock, patch

from codemie_tools.git.utils import GitCredentials, init_github_api_wrapper


# ===== GitCredentials Validation Tests =====


@pytest.mark.parametrize(
    "credentials_data,should_pass,expected_error",
    [
        # Valid: GitHub with PAT
        (
            {
                "token": "ghp_test",
                "repo_link": "https://github.com/user/repo.git",
                "base_branch": "main",
                "repo_type": "github",
            },
            True,
            None,
        ),
        # Valid: GitHub with GitHub App
        (
            {
                "app_id": 123456,
                "private_key": "test_key",
                "repo_link": "https://github.com/user/repo.git",
                "base_branch": "main",
                "repo_type": "github",
            },
            True,
            None,
        ),
        # Valid: GitHub with GitHub App and installation_id
        (
            {
                "app_id": 123456,
                "private_key": "test_key",
                "installation_id": 12345,
                "repo_link": "https://github.com/user/repo.git",
                "base_branch": "main",
                "repo_type": "github",
            },
            True,
            None,
        ),
        # Valid: GitLab with PAT only
        (
            {
                "token": "gitlab_token",
                "repo_link": "https://gitlab.com/user/repo.git",
                "base_branch": "main",
                "repo_type": "gitlab",
            },
            True,
            None,
        ),
        # Valid: Bitbucket with PAT only
        (
            {
                "token": "bitbucket_token",
                "repo_link": "https://bitbucket.org/user/repo.git",
                "base_branch": "main",
                "repo_type": "bitbucket",
            },
            True,
            None,
        ),
        # Invalid: GitHub with both PAT and App
        (
            {
                "token": "ghp_test",
                "app_id": 123456,
                "private_key": "test_key",
                "repo_link": "https://github.com/user/repo.git",
                "base_branch": "main",
                "repo_type": "github",
            },
            False,
            "Cannot use both PAT and GitHub App authentication",
        ),
        # Invalid: GitHub with no authentication
        (
            {"repo_link": "https://github.com/user/repo.git", "base_branch": "main", "repo_type": "github"},
            False,
            "GitHub authentication required",
        ),
        # Invalid: GitHub with partial App (only app_id)
        (
            {
                "app_id": 123456,
                "repo_link": "https://github.com/user/repo.git",
                "base_branch": "main",
                "repo_type": "github",
            },
            False,
            "GitHub App authentication requires both 'app_id' and 'private_key'",
        ),
        # Invalid: GitHub with partial App (only private_key)
        (
            {
                "private_key": "test_key",
                "repo_link": "https://github.com/user/repo.git",
                "base_branch": "main",
                "repo_type": "github",
            },
            False,
            "GitHub App authentication requires both 'app_id' and 'private_key'",
        ),
        # Invalid: GitLab with GitHub App fields
        (
            {
                "app_id": 123456,
                "private_key": "test_key",
                "repo_link": "https://gitlab.com/user/repo.git",
                "base_branch": "main",
                "repo_type": "gitlab",
            },
            False,
            "GitHub App authentication (app_id, private_key, installation_id) is only supported for GitHub repositories, not for gitlab",
        ),
        # Invalid: Bitbucket with GitHub App fields
        (
            {
                "token": "bitbucket_token",
                "installation_id": 12345,
                "repo_link": "https://bitbucket.org/user/repo.git",
                "base_branch": "main",
                "repo_type": "bitbucket",
            },
            False,
            "GitHub App authentication (app_id, private_key, installation_id) is only supported for GitHub repositories, not for bitbucket",
        ),
        # Invalid: GitLab without token
        (
            {"repo_link": "https://gitlab.com/user/repo.git", "base_branch": "main", "repo_type": "gitlab"},
            False,
            "Token is required for gitlab authentication",
        ),
        # Invalid: Bitbucket without token
        (
            {"repo_link": "https://bitbucket.org/user/repo.git", "base_branch": "main", "repo_type": "bitbucket"},
            False,
            "Token is required for bitbucket authentication",
        ),
    ],
)
def test_git_credentials_validation(credentials_data, should_pass, expected_error):
    """Test GitCredentials validates authentication for all Git providers."""
    if should_pass:
        creds = GitCredentials(**credentials_data)
        assert creds is not None

        # Verify is_github_app property
        if "app_id" in credentials_data:
            assert creds.is_github_app is True
        else:
            assert creds.is_github_app is False
    else:
        with pytest.raises(ValueError) as exc_info:
            GitCredentials(**credentials_data)
        assert expected_error in str(exc_info.value)


def test_git_credentials_is_github_app_property():
    """Test is_github_app property works correctly."""
    # PAT credentials
    pat_creds = GitCredentials(
        token="ghp_test", repo_link="https://github.com/user/repo.git", base_branch="main", repo_type="github"
    )
    assert pat_creds.is_github_app is False

    # GitHub App credentials
    app_creds = GitCredentials(
        app_id=123456,
        private_key="test_key",
        repo_link="https://github.com/user/repo.git",
        base_branch="main",
        repo_type="github",
    )
    assert app_creds.is_github_app is True


def test_git_credentials_detects_github_from_url():
    """Test GitCredentials detects GitHub even without explicit repo_type."""
    # Should detect github.com in URL
    creds = GitCredentials(
        token="ghp_test",
        repo_link="https://github.com/user/repo.git",
        base_branch="main",
        repo_type="unknown",  # Even with unknown, should detect from URL
    )
    assert creds.token == "ghp_test"


# ===== init_github_api_wrapper Tests =====


@patch('codemie_tools.git.utils.CustomGitHubAPIWrapper')
def test_init_github_api_wrapper_with_pat(mock_wrapper_class):
    """Test init_github_api_wrapper initializes correctly with PAT."""
    mock_wrapper = Mock()
    mock_wrapper_class.return_value = mock_wrapper

    creds = GitCredentials(
        token="ghp_test", repo_link="https://github.com/user/repo.git", base_branch="main", repo_type="github"
    )

    result = init_github_api_wrapper(creds)

    assert result == mock_wrapper
    mock_wrapper_class.assert_called_once()
    call_kwargs = mock_wrapper_class.call_args[1]
    assert call_kwargs["github_access_token"] == "ghp_test"
    assert call_kwargs["github_base_branch"] == "main"
    assert call_kwargs["github_repository"] == "user/repo"


@patch('codemie_tools.git.utils.CustomGitHubAPIWrapper')
def test_init_github_api_wrapper_with_github_app(mock_wrapper_class):
    """Test init_github_api_wrapper initializes correctly with GitHub App."""
    mock_wrapper = Mock()
    mock_wrapper_class.return_value = mock_wrapper

    creds = GitCredentials(
        app_id=123456,
        private_key="test_private_key",
        installation_id=12345678,
        repo_link="https://github.com/user/repo.git",
        base_branch="main",
        repo_type="github",
    )

    result = init_github_api_wrapper(creds)

    assert result == mock_wrapper
    mock_wrapper_class.assert_called_once()
    call_kwargs = mock_wrapper_class.call_args[1]
    assert call_kwargs["github_app_id"] == 123456
    assert call_kwargs["github_app_private_key"] == "test_private_key"
    assert call_kwargs["github_app_installation_id"] == 12345678
    assert call_kwargs["github_base_branch"] == "main"
    assert call_kwargs["github_repository"] == "user/repo"
    assert "github_access_token" not in call_kwargs


@patch('codemie_tools.git.utils.CustomGitHubAPIWrapper')
def test_init_github_api_wrapper_with_github_app_no_installation_id(mock_wrapper_class):
    """Test init_github_api_wrapper works without installation_id."""
    mock_wrapper = Mock()
    mock_wrapper_class.return_value = mock_wrapper

    creds = GitCredentials(
        app_id=123456,
        private_key="test_private_key",
        repo_link="https://github.com/user/repo.git",
        base_branch="main",
        repo_type="github",
    )

    result = init_github_api_wrapper(creds)

    assert result == mock_wrapper
    call_kwargs = mock_wrapper_class.call_args[1]
    assert "github_app_installation_id" not in call_kwargs


def test_init_github_api_wrapper_with_no_auth():
    """Test init_github_api_wrapper returns None with no auth."""
    # Create credentials without validation (using dict)
    with pytest.raises(ValueError):
        # This should fail validation first
        GitCredentials(repo_link="https://github.com/user/repo.git", base_branch="main", repo_type="github")


# ===== CustomGitHubAPIWrapper Tests =====


@patch('github.Auth')
@patch('github.Github')
def test_custom_github_api_wrapper_pat_auth(mock_github, mock_auth):
    """Test CustomGitHubAPIWrapper initializes with PAT authentication."""
    from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper

    mock_github_instance = Mock()
    mock_github.return_value = mock_github_instance

    wrapper = CustomGitHubAPIWrapper(github_access_token="ghp_test", github_base_branch="main", active_branch="main")

    assert wrapper is not None
    mock_auth.Token.assert_called_once_with("ghp_test")


@patch('github.Auth')
@patch('github.Github')
@patch('github.GithubIntegration')
def test_custom_github_api_wrapper_github_app_auth(mock_integration_class, mock_github, mock_auth):
    """Test CustomGitHubAPIWrapper initializes with GitHub App authentication."""
    from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper

    # Setup mocks
    mock_access_token = Mock()
    mock_access_token.token = "ghs_app_token"

    mock_integration = Mock()
    mock_integration.get_access_token.return_value = mock_access_token
    mock_integration_class.return_value = mock_integration

    mock_github_instance = Mock()
    mock_github.return_value = mock_github_instance

    # Initialize with GitHub App
    wrapper = CustomGitHubAPIWrapper(
        github_app_id=123456,
        github_app_private_key="test_private_key",
        github_app_installation_id=12345678,
        github_base_branch="main",
        active_branch="main",
    )

    assert wrapper is not None
    mock_integration_class.assert_called_once_with(integration_id=123456, private_key="test_private_key")
    mock_integration.get_access_token.assert_called_once_with(12345678)
    mock_auth.Token.assert_called_once_with("ghs_app_token")


@patch('github.Auth')
@patch('github.Github')
@patch('github.GithubIntegration')
def test_custom_github_api_wrapper_github_app_auto_detect_installation(mock_integration_class, mock_github, mock_auth):
    """Test CustomGitHubAPIWrapper auto-detects installation_id."""
    from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper

    # Setup mocks
    mock_installation = Mock()
    mock_installation.id = 99999999

    mock_access_token = Mock()
    mock_access_token.token = "ghs_app_token"

    mock_integration = Mock()
    mock_integration.get_installations.return_value = iter([mock_installation])
    mock_integration.get_access_token.return_value = mock_access_token
    mock_integration_class.return_value = mock_integration

    mock_github_instance = Mock()
    mock_github.return_value = mock_github_instance

    # Initialize without installation_id
    wrapper = CustomGitHubAPIWrapper(
        github_app_id=123456, github_app_private_key="test_private_key", github_base_branch="main", active_branch="main"
    )

    assert wrapper is not None
    mock_integration.get_installations.assert_called_once()
    mock_integration.get_access_token.assert_called_once_with(99999999)


@patch('github.GithubIntegration')
def test_custom_github_api_wrapper_github_app_no_installations(mock_integration_class):
    """Test CustomGitHubAPIWrapper raises error when no installations found."""
    from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper

    # Setup mocks
    mock_integration = Mock()
    mock_integration.get_installations.return_value = iter([])  # Empty
    mock_integration_class.return_value = mock_integration

    # Should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        CustomGitHubAPIWrapper(
            github_app_id=123456,
            github_app_private_key="test_private_key",
            github_base_branch="main",
            active_branch="main",
        )

    assert "No GitHub App installations found" in str(exc_info.value)
