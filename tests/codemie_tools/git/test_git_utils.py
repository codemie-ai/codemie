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

from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.git.utils import (
    GitCredentials,
    init_bitbucket_api_wrapper,
    init_github_api_wrapper,
    init_gitlab_api_wrapper,
    validate_gitlab_wrapper,
)


def test_validate_gitlab_wrapper_present():
    mock_wrapper = MagicMock()
    result = validate_gitlab_wrapper(mock_wrapper, None)
    assert result is None


def test_validate_gitlab_wrapper_missing_creds():
    from codemie_tools.base.errors import InvalidCredentialsError

    mock_creds = MagicMock(token=None)
    with pytest.raises(InvalidCredentialsError):
        validate_gitlab_wrapper(None, mock_creds)


@patch('codemie_tools.git.utils.init_gitlab_api_wrapper')
def test_validate_gitlab_wrapper_exception(mock_init_function):
    from codemie_tools.base.errors import InvalidCredentialsError

    mock_init_function.side_effect = Exception('Error')
    creds = GitCredentials(
        token='gitlab_token', repo_link='repo_link', base_branch='base_branch', token_name='gitlab_token_name'
    )

    with pytest.raises(InvalidCredentialsError):
        validate_gitlab_wrapper(None, creds)


@pytest.fixture
def git_creds():
    return GitCredentials(
        repo_link="https://example.com/username/repo.git",
        base_branch="main",
        token="test_token",
        token_name="test_user",
    )


class TestInitGitLabAPIWrapper:
    def test_no_token(self, git_creds):
        git_creds.token = None
        result = init_gitlab_api_wrapper(git_creds)
        assert result is None

    def test_exception(self, git_creds):
        with patch(
            'codemie_tools.git.gitlab.custom_gitlab_api_wrapper.CustomGitLabAPIWrapper',
            side_effect=Exception("Test error"),
        ):
            result = init_gitlab_api_wrapper(git_creds)
            assert result is None


class TestInitGitHubAPIWrapper:
    def test_no_token(self, git_creds):
        git_creds.token = None
        result = init_github_api_wrapper(git_creds)
        assert result is None

    def test_exception(self, git_creds):
        with patch(
            'codemie_tools.git.github.custom_github_api_wrapper.CustomGitHubAPIWrapper',
            side_effect=Exception("Test error"),
        ):
            result = init_github_api_wrapper(git_creds)
            assert result is None


class TestInitBitbucketAPIWrapper:
    def test_no_token(self, git_creds):
        git_creds.token = None
        result = init_bitbucket_api_wrapper(git_creds)
        assert result is None

    def test_exception(self, git_creds):
        with patch(
            'codemie_tools.git.bitbucket.custom_bitbucket_wrapper.CustomBitbucketApiWrapper',
            side_effect=Exception("Test error"),
        ):
            result = init_bitbucket_api_wrapper(git_creds)
            assert result is None
