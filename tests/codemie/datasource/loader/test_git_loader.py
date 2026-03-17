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

import os
import unittest
from unittest.mock import patch, mock_open, MagicMock, Mock

import pytest
from git import Blob, Submodule

from codemie.core.constants import CodeIndexType
from codemie.core.models import GitRepo
from codemie.core.utils import check_file_type
from codemie.datasource.loader.git_loader import GitBatchLoader, _build_clone_url
from codemie.rest_api.models.settings import Credentials, GitAuthType


class TestGitBatchLoader(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo(
            name="test_repo",
            branch="main-test",
            indexType=CodeIndexType.CODE,
            appId="app_id",
            link="https://example.com",
            description="anything",
        )
        self.repo_path = '/some/repo/path'
        self.file_filter = Mock()
        self.loader = GitBatchLoader(self.repo_path, self.file_filter)

    def test_is_image(self):
        image_path = "example.jpg"
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type(image_path))

    def test_is_video(self):
        video_path = "example.mp4"
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type(video_path))

    def test_is_audio(self):
        audio_path = "example.mp3"
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type(audio_path))

    def test_is_not_image_or_video(self):
        text_path = "example.txt"
        self.assertFalse(GitBatchLoader._is_unsupported_mime_type(text_path))

    def test_no_mime_type(self):
        unknown_path = "example.unknown"
        self.assertFalse(GitBatchLoader._is_unsupported_mime_type(unknown_path))

    @patch("builtins.open", new_callable=mock_open, read_data=b"mock file content")
    @patch.object(GitBatchLoader, '_decode_content', return_value="decoded content")
    def test_process_file_success(self, mock_decode_content, mock_open_file):
        loader = GitBatchLoader(repo_path="/path/to/repo")
        item = MagicMock()
        item.name = "example.txt"
        file_path = "/path/to/repo/example.txt"

        document = loader._process_file(item, file_path)

        self.assertIsNotNone(document)
        self.assertEqual(document.page_content, "decoded content")
        self.assertEqual(document.metadata["file_name"], "example.txt")
        self.assertEqual(document.metadata["file_path"], "example.txt")

    @patch("builtins.open", new_callable=mock_open)
    @patch.object(GitBatchLoader, '_decode_content', return_value=None)
    def test_process_file_decode_failed(self, mock_decode_content, mock_open_file):
        item = MagicMock()
        item.name = "example.txt"
        file_path = "/path/to/repo/example.txt"

        document = self.loader._process_file(item, file_path)

        self.assertIsNone(document)

    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_process_file_file_not_found(self, mock_open_file):
        item = MagicMock()
        item.name = "example.txt"
        file_path = "/path/to/repo/example.txt"

        document = self.loader._process_file(item, file_path)

        self.assertIsNone(document)

    @patch("builtins.open", side_effect=IsADirectoryError)
    def test_process_file_is_a_directory_error(self, mock_open_file):
        item = MagicMock()
        item.name = "example.txt"
        file_path = "/path/to/repo/example.txt"

        document = self.loader._process_file(item, file_path)

        self.assertIsNone(document)

    def test_build_clone_url_with_creds_and_at(self):
        creds = Credentials(token_name="username", token="password", url="url")
        self.repo.link = "https://example.com@repo.git"

        result = _build_clone_url(creds, self.repo)
        self.assertEqual(result, "https://username:password@repo.git")

    def test_build_clone_url_with_creds_and_at_and_spaces(self):
        creds = Credentials(token_name="user name", token="pass word", url="url")
        self.repo.link = "https://example.com@repo.git"

        result = _build_clone_url(creds, self.repo)
        self.assertEqual(result, "https://user%20name:pass%20word@repo.git")

    def test_build_clone_url_with_creds_without_at(self):
        creds = Credentials(token_name="username", token="password", url="url")
        self.repo.link = "https://example.com/repo.git"

        result = _build_clone_url(creds, self.repo)
        self.assertEqual(result, "https://username:password@example.com/repo.git")

    def test_build_clone_url_with_creds_without_at_and_spaces(self):
        creds = Credentials(token_name="user name", token="pass word", url="url")
        self.repo.link = "https://example.com/repo.git"

        result = _build_clone_url(creds, self.repo)
        self.assertEqual(result, "https://user%20name:pass%20word@example.com/repo.git")

    def test_build_clone_url_without_creds(self):
        creds = None
        self.repo.link = "https://example.com/repo.git"

        result = _build_clone_url(creds, self.repo)
        self.assertEqual(result, "https://example.com/repo.git")

    def test_build_clone_url_with_token_only(self):
        creds = Credentials(token_name="", token="password", url="url")
        self.repo.link = "https://example.com/repo.git"

        result = _build_clone_url(creds, self.repo)
        self.assertEqual(result, "https://oauth2:password@example.com/repo.git")

    def test_check_file_type_excluded_file(self):
        result = check_file_type(
            file_name="/path/to/repo/example.txt",
            files_filter=".py",
            repo_local_path="/path/to/repo",
            excluded_files=[".txt"],
        )
        self.assertFalse(result)

    def test_check_file_type_no_file_filter_specified(self):
        result = check_file_type(
            file_name="/path/to/repo/example.py", files_filter="", repo_local_path="/path/to/repo", excluded_files=[]
        )
        self.assertTrue(result)

    def test_check_file_type_gitignore_syntax(self):
        files_filter = """
        *.py
        """
        result = check_file_type(
            file_name="/path/to/repo/example.py",
            files_filter=files_filter,
            repo_local_path="/path/to/repo",
            excluded_files=[],
        )
        self.assertTrue(result)

    def test_check_file_type_syntax_exclusion(self):
        files_filter = """
        *.py
        !example.py
        """
        result = check_file_type(
            file_name="/path/to/repo/example.py",
            files_filter=files_filter,
            repo_local_path="/path/to/repo",
            excluded_files=[],
        )
        self.assertFalse(result)

    def test_check_file_type_multiline(self):
        files_filter = """
        # Include all .txt files
        *.txt
        # But ignore example_file.txt specifically
        !example_folder/example_file.txt
        """
        result = check_file_type(
            file_name="/path/to/repo/example_folder/example_file.txt",
            files_filter=files_filter,
            repo_local_path="/path/to/repo",
            excluded_files=[],
        )
        self.assertFalse(result)

        result = check_file_type(
            file_name="/path/to/repo/example_folder/another_file.txt",
            files_filter=files_filter,
            repo_local_path="/path/to/repo",
            excluded_files=[],
        )
        self.assertTrue(result)

    def test_check_file_type_excluded_files(self):
        files_filter = """
        *.log
        """
        result = check_file_type(
            file_name="/path/to/repo/example.log",
            files_filter=files_filter,
            repo_local_path="/path/to/repo",
            excluded_files=['.log'],
        )
        self.assertFalse(result)

    @patch('os.path.islink')
    def test_should_skip_submodule(self, mock_islink):
        item = Mock(spec=Submodule)
        self.assertTrue(self.loader._should_skip_item(item))

    @patch('os.path.islink')
    def test_should_skip_symlink(self, mock_islink):
        item = Mock(spec=Blob)
        item.path = 'some_path'
        mock_islink.return_value = True
        self.assertTrue(self.loader._should_skip_item(item))
        mock_islink.assert_called_once_with(os.path.join(self.repo_path, item.path))

    def test_should_skip_non_blob(self):
        item = Mock()
        item.path = 'some_path'
        self.assertTrue(self.loader._should_skip_item(item))

    @patch('codemie.datasource.loader.git_loader.GitBatchLoader._is_unsupported_mime_type')
    def test_should_skip_unsupported_mime_type(self, mock_is_unsupported_mime_type):
        item = Mock(spec=Blob)
        item.path = 'some_path'
        mock_is_unsupported_mime_type.return_value = True
        self.assertTrue(self.loader._should_skip_item(item))
        mock_is_unsupported_mime_type.assert_called_once_with(item.path)


@pytest.fixture
def github_repo():
    """Create a test GitRepo for GitHub."""
    return GitRepo(
        name="test_repo",
        branch="main",
        indexType=CodeIndexType.CODE,
        appId="app_id",
        link="https://github.com/org/repo.git",
        description="Test repository",
    )


@pytest.fixture
def github_app_credentials():
    """Create credentials with GitHub App authentication."""
    return Credentials(
        url="https://github.com/org/repo",
        auth_type=GitAuthType.GITHUB_APP,
        app_id=123456,
        private_key="-----BEGIN KEY-----\ntest_key\n-----END KEY-----",
        installation_id=789012,
    )


@pytest.fixture
def pat_credentials():
    """Create credentials with PAT authentication."""
    return Credentials(
        url="https://github.com/org/repo", auth_type=GitAuthType.PAT, token="ghp_test_token", token_name="oauth2"
    )


@pytest.mark.parametrize(
    "installation_id,expected_token",
    [
        (789012, "ghs_installation_token_12345"),
        (None, "ghs_auto_detected_token"),
    ],
)
@patch('codemie.datasource.loader.git_loader.get_github_app_token')
def test_build_clone_url_with_github_app(mock_get_token, github_repo, installation_id, expected_token):
    """Test clone URL generation with GitHub App credentials."""
    # Arrange
    creds = Credentials(
        url="https://github.com/org/repo",
        auth_type=GitAuthType.GITHUB_APP,
        app_id=123456,
        private_key="-----BEGIN RSA PRIVATE KEY-----\ntest_key\n-----END RSA PRIVATE KEY-----",
        installation_id=installation_id,
    )
    mock_get_token.return_value = expected_token

    # Act
    result = _build_clone_url(creds, github_repo)

    # Assert
    assert result == f"https://x-access-token:{expected_token}@github.com/org/repo.git"
    mock_get_token.assert_called_once_with(creds.app_id, creds.private_key, installation_id)


@patch('codemie.datasource.loader.git_loader.get_github_app_token')
def test_build_clone_url_github_app_token_generation_fails(mock_get_token, github_repo, github_app_credentials):
    """Test error handling when GitHub App token generation fails."""
    # Arrange
    mock_get_token.side_effect = ValueError("GitHub App authentication failed: API error")

    # Act & Assert
    with pytest.raises(ValueError, match="GitHub App authentication failed"):
        _build_clone_url(github_app_credentials, github_repo)


def test_build_clone_url_pat_still_works(github_repo, pat_credentials):
    """Test that PAT authentication still works (backward compatibility)."""
    # Act
    result = _build_clone_url(pat_credentials, github_repo)

    # Assert
    assert result == "https://oauth2:ghp_test_token@github.com/org/repo.git"


@pytest.mark.parametrize(
    "creds,expected_url",
    [
        (None, "https://github.com/org/repo.git"),
        (
            Credentials(url="https://github.com/org/repo", auth_type=GitAuthType.PAT, token=None, token_name=None),
            "https://github.com/org/repo.git",
        ),
    ],
)
def test_build_clone_url_no_auth(github_repo, creds, expected_url):
    """Test clone URL generation without credentials or with empty credentials."""
    # Act
    result = _build_clone_url(creds, github_repo)

    # Assert
    assert result == expected_url


@pytest.mark.parametrize(
    "repo_link,expected_result",
    [
        ("https://github.com/org/repo.git", "https://x-access-token:ghs_token@github.com/org/repo.git"),
        ("https://github.company.com/org/repo.git", "https://x-access-token:ghs_token@github.company.com/org/repo.git"),
    ],
)
@patch('codemie.datasource.loader.git_loader.get_github_app_token')
def test_build_clone_url_github_app_various_urls(mock_get_token, github_app_credentials, repo_link, expected_result):
    """Test clone URL generation with GitHub App for various repository URLs."""
    # Arrange
    repo = GitRepo(
        name="test_repo",
        branch="main",
        indexType=CodeIndexType.CODE,
        appId="app_id",
        link=repo_link,
        description="Test repository",
    )
    mock_get_token.return_value = "ghs_token"

    # Act
    result = _build_clone_url(github_app_credentials, repo)

    # Assert
    assert result == expected_result


@patch('codemie.datasource.loader.git_loader.get_github_app_token')
def test_build_clone_url_github_app_special_characters_in_token(mock_get_token, github_repo, github_app_credentials):
    """Test URL encoding of tokens with special characters."""
    # Arrange
    mock_get_token.return_value = "ghs_token/with+special=chars"

    # Act
    result = _build_clone_url(github_app_credentials, github_repo)

    # Assert
    # Token should be URL-encoded (note: forward slash / is NOT encoded by quote())
    # This is correct behavior as / is safe in passwords for HTTP basic auth
    assert "x-access-token:ghs_token/with%2Bspecial%3Dchars@" in result
    assert result.endswith("github.com/org/repo.git")
