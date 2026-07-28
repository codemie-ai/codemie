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

import base64
import os
import unittest
from unittest.mock import patch, mock_open, MagicMock, Mock

import pytest
from git import Blob, Submodule
from langchain_core.documents import Document

from codemie.core.constants import CodeIndexType
from codemie.core.models import GitRepo
from codemie.core.utils import check_file_type
from codemie.datasource.exceptions import ConnectionException
from codemie.datasource.loader.git_loader import GitBatchLoader, _build_clone_url, _has_null_bytes
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
        self.assertFalse(GitBatchLoader._is_unsupported_mime_type(image_path))

    def test_is_video(self):
        video_path = "example.mp4"
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type(video_path))

    def test_is_audio(self):
        audio_path = "example.mp3"
        self.assertFalse(GitBatchLoader._is_unsupported_mime_type(audio_path))

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

        documents = loader._process_file(item, file_path)

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].page_content, "decoded content")
        self.assertEqual(documents[0].metadata["file_name"], "example.txt")
        self.assertEqual(documents[0].metadata["file_path"], "example.txt")

    @patch("builtins.open", new_callable=mock_open)
    @patch.object(GitBatchLoader, '_decode_content', return_value=None)
    def test_process_file_decode_failed(self, mock_decode_content, mock_open_file):
        item = MagicMock()
        item.name = "example.txt"
        file_path = "/path/to/repo/example.txt"

        documents = self.loader._process_file(item, file_path)

        self.assertEqual(documents, [])

    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_process_file_file_not_found(self, mock_open_file):
        item = MagicMock()
        item.name = "example.txt"
        file_path = "/path/to/repo/example.txt"

        documents = self.loader._process_file(item, file_path)

        self.assertEqual(documents, [])

    @patch("builtins.open", side_effect=IsADirectoryError)
    def test_process_file_is_a_directory_error(self, mock_open_file):
        item = MagicMock()
        item.name = "example.txt"
        file_path = "/path/to/repo/example.txt"

        documents = self.loader._process_file(item, file_path)

        self.assertEqual(documents, [])

    # --- MIME prefix wildcard tests ---

    def test_is_unsupported_returns_true_for_woff(self):
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type("font.woff"))

    def test_is_unsupported_returns_true_for_woff2(self):
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type("font.woff2"))

    @patch("mimetypes.guess_type", return_value=("model/gltf-binary", None))
    def test_is_unsupported_returns_true_for_gltf_binary(self, _mock_mime):
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type("/repo/assets/model.bin"))

    # --- _has_null_bytes tests ---

    @patch("builtins.open", new_callable=mock_open, read_data=b"hello\x00world")
    def test_has_null_bytes_returns_true_when_null_in_chunk(self, _mock_file):
        self.assertTrue(_has_null_bytes("/some/binary.bin"))

    @patch("builtins.open", new_callable=mock_open, read_data=b"#!/usr/bin/env python\nprint('hello')")
    def test_has_null_bytes_returns_false_for_clean_text(self, _mock_file):
        self.assertFalse(_has_null_bytes("/some/script.py"))

    @patch("builtins.open", side_effect=OSError)
    def test_has_null_bytes_returns_false_on_oserror(self, _mock_file):
        self.assertFalse(_has_null_bytes("/nonexistent/file"))

    @patch("builtins.open", new_callable=mock_open, read_data=b"data")
    def test_has_null_bytes_reads_at_most_8192_bytes(self, mock_file):
        _has_null_bytes("/some/file")
        mock_file().read.assert_called_once_with(8192)

    # --- MIME=None + null-byte heuristic integration ---

    @patch("codemie.datasource.loader.git_loader._has_null_bytes", return_value=True)
    @patch("mimetypes.guess_type", return_value=(None, None))
    def test_is_unsupported_mime_none_with_null_bytes(self, _mock_mime, _mock_null):
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type("/repo/data/unknown_binary"))

    @patch("codemie.datasource.loader.git_loader._has_null_bytes", return_value=False)
    @patch("mimetypes.guess_type", return_value=(None, None))
    def test_is_unsupported_mime_none_without_null_bytes(self, _mock_mime, _mock_null):
        self.assertFalse(GitBatchLoader._is_unsupported_mime_type("/repo/data/unknown_text"))

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
        mock_is_unsupported_mime_type.assert_called_once_with(os.path.join(self.repo_path, item.path))

    # --- _is_unsupported_mime_type: binary-extractable files are always allowed ---

    def test_is_unsupported_mime_type_returns_false_for_pdf(self):
        self.assertFalse(GitBatchLoader._is_unsupported_mime_type("document.pdf"))

    def test_is_unsupported_mime_type_returns_false_for_docx(self):
        self.assertFalse(GitBatchLoader._is_unsupported_mime_type("document.docx"))

    def test_is_unsupported_mime_type_returns_false_for_jpg(self):
        self.assertFalse(GitBatchLoader._is_unsupported_mime_type("photo.jpg"))

    def test_is_unsupported_mime_type_returns_false_for_jpeg(self):
        self.assertFalse(GitBatchLoader._is_unsupported_mime_type("photo.jpeg"))

    def test_is_unsupported_mime_type_returns_false_for_png(self):
        self.assertFalse(GitBatchLoader._is_unsupported_mime_type("screenshot.png"))

    def test_is_unsupported_mime_type_returns_true_for_rtf(self):
        # .exe/.dll MIME types vary by platform (application/x-msdownload on macOS,
        # application/x-dosexec on Linux), so we test .rtf which is reliably
        # application/rtf across all platforms. Note: .exe/.dll are blocked earlier
        # by excluded_extensions and never reach this function in practice.
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type("document.rtf"))

    def test_is_unsupported_mime_type_returns_true_for_tar(self):
        # .tar → application/x-tar, reliably detected on all platforms
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type("archive.tar"))

    def test_is_unsupported_mime_type_returns_true_for_rar(self):
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type("archive.rar"))

    def test_is_unsupported_mime_type_returns_true_for_mp4(self):
        self.assertTrue(GitBatchLoader._is_unsupported_mime_type("video.mp4"))

    # --- _process_file: binary files are routed to _process_binary_file ---

    @patch("builtins.open", new_callable=mock_open, read_data=b"%PDF binary content")
    @patch.object(GitBatchLoader, '_process_binary_file')
    def test_process_file_routes_pdf_to_process_binary_file(self, mock_binary, mock_open_file):
        # Arrange
        loader = GitBatchLoader(repo_path="/path/to/repo")
        item = MagicMock()
        item.name = "report.pdf"
        file_path = "/path/to/repo/report.pdf"
        expected_docs = [Document(page_content="extracted pdf text", metadata={})]
        mock_binary.return_value = expected_docs

        # Act
        result = loader._process_file(item, file_path)

        # Assert
        mock_binary.assert_called_once_with(b"%PDF binary content", "report.pdf", "report.pdf")
        self.assertEqual(result, expected_docs)

    @patch("builtins.open", new_callable=mock_open, read_data=b"text content")
    @patch.object(GitBatchLoader, '_decode_content', return_value="text content")
    def test_process_file_returns_list_for_text_file(self, mock_decode, mock_open_file):
        # Arrange
        loader = GitBatchLoader(repo_path="/path/to/repo")
        item = MagicMock()
        item.name = "script.py"
        file_path = "/path/to/repo/script.py"

        # Act
        result = loader._process_file(item, file_path)

        # Assert — result is always a list
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].page_content, "text content")

    # --- _process_binary_file: metadata is set correctly ---

    @patch("codemie.datasource.loader.git_loader.extract_documents_from_bytes")
    def test_process_binary_file_sets_correct_metadata_for_pdf(self, mock_extract):
        # Arrange
        loader = GitBatchLoader(repo_path="/path/to/repo")
        raw_doc = Document(page_content="pdf page 1", metadata={"source": "/tmp/tmpXXX.pdf"})
        mock_extract.return_value = [raw_doc]

        # Act
        result = loader._process_binary_file(b"%PDF-1.4", "docs/report.pdf", "report.pdf")

        # Assert
        self.assertEqual(len(result), 1)
        doc = result[0]
        self.assertEqual(doc.metadata["source"], "docs/report.pdf")
        self.assertEqual(doc.metadata["file_path"], "docs/report.pdf")
        self.assertEqual(doc.metadata["file_name"], "report.pdf")
        self.assertEqual(doc.metadata["file_type"], ".pdf")

    @patch("codemie.datasource.loader.git_loader.extract_documents_from_bytes")
    def test_process_binary_file_sets_correct_metadata_for_png(self, mock_extract):
        # Arrange
        loader = GitBatchLoader(repo_path="/path/to/repo")
        raw_doc = Document(page_content="image description", metadata={"source": "/tmp/tmpXXX.png"})
        mock_extract.return_value = [raw_doc]

        # Act
        result = loader._process_binary_file(b"\x89PNG", "assets/screenshot.png", "screenshot.png")

        # Assert
        doc = result[0]
        self.assertEqual(doc.metadata["source"], "assets/screenshot.png")
        self.assertEqual(doc.metadata["file_path"], "assets/screenshot.png")
        self.assertEqual(doc.metadata["file_name"], "screenshot.png")
        self.assertEqual(doc.metadata["file_type"], ".png")

    @patch("codemie.datasource.loader.git_loader.extract_documents_from_bytes")
    def test_process_binary_file_returns_empty_list_when_extractor_returns_nothing(self, mock_extract):
        # Arrange
        loader = GitBatchLoader(repo_path="/path/to/repo")
        mock_extract.return_value = []

        # Act
        result = loader._process_binary_file(b"bytes", "docs/empty.pdf", "empty.pdf")

        # Assert
        self.assertEqual(result, [])

    @patch("codemie.datasource.loader.git_loader.extract_documents_from_bytes")
    def test_process_binary_file_passes_file_name_to_extractor(self, mock_extract):
        # Arrange
        loader = GitBatchLoader(repo_path="/path/to/repo")
        mock_extract.return_value = []

        # Act
        loader._process_binary_file(b"content", "sub/dir/doc.docx", "doc.docx")

        # Assert
        mock_extract.assert_called_once_with(
            file_bytes=b"content",
            file_name="doc.docx",
            request_uuid=None,
            datasource_id="",
        )

    @patch('codemie.datasource.loader.git_loader.git_cmd.Git')
    def test_test_public_access_success(self, mock_git_cls):
        """test_public_access returns None when ls-remote succeeds."""
        mock_git_cls.return_value.execute.return_value = "abc123\tHEAD"
        # Should not raise
        GitBatchLoader.test_public_access("https://github.com/owner/public-repo")

    @patch('codemie.datasource.loader.git_loader.git_cmd.Git')
    def test_test_public_access_git_command_error(self, mock_git_cls):
        """test_public_access raises ConnectionException on GitCommandError."""
        from git.exc import GitCommandError

        mock_git_cls.return_value.execute.side_effect = GitCommandError("ls-remote", 128)
        with pytest.raises(ConnectionException):
            GitBatchLoader.test_public_access("https://github.com/owner/private-repo")

    @patch('codemie.datasource.loader.git_loader.git_cmd.Git')
    def test_test_public_access_generic_exception(self, mock_git_cls):
        """test_public_access raises ConnectionException on any other exception (e.g. timeout)."""
        mock_git_cls.return_value.execute.side_effect = Exception("timed out")
        with pytest.raises(ConnectionException):
            GitBatchLoader.test_public_access("https://github.com/owner/slow-repo")

    @patch('codemie.datasource.loader.git_loader.git_cmd.Git')
    def test_test_connection_uses_auth_url(self, mock_git_cls):
        """test_connection builds an auth URL from creds and runs ls-remote on it."""
        mock_execute = mock_git_cls.return_value.execute
        mock_execute.return_value = "abc123\tHEAD"

        creds = Credentials(
            url="https://github.com",
            token="mytoken",
            token_name="oauth2",
            auth_type="pat",
        )
        GitBatchLoader.test_connection("https://github.com/owner/repo", creds)

        # The execute call must receive the auth-embedded URL, not the plain one
        cmd_list = mock_execute.call_args[0][0]  # first positional arg is the command list
        auth_url = cmd_list[cmd_list.index("--quiet") + 1]
        assert "mytoken" in auth_url, f"Expected auth URL after --quiet, got: {cmd_list}"
        assert "-c" not in cmd_list, f"No extraHeader expected without header auth, got: {cmd_list}"

    @patch('codemie.datasource.loader.git_loader.git_cmd.Git')
    def test_test_connection_uses_header_auth(self, mock_git_cls):
        """test_connection adds the http.extraHeader option when creds use header auth."""
        mock_execute = mock_git_cls.return_value.execute
        mock_execute.return_value = "abc123\tHEAD"

        creds = Credentials(
            url="https://onprem.example.com",
            token="mytoken",
            token_name="oauth2",
            auth_type="pat",
            use_header_auth=True,
        )
        GitBatchLoader.test_connection("https://onprem.example.com/owner/repo", creds)

        # Must mirror create_loader: -c http.extraHeader=... placed before the ls-remote subcommand
        cmd_list = mock_execute.call_args[0][0]
        assert "-c" in cmd_list, f"Expected header auth option, got: {cmd_list}"
        header_option = cmd_list[cmd_list.index("-c") + 1]
        expected_token = base64.b64encode(b":mytoken").decode()
        assert header_option == f"http.extraHeader=Authorization: Basic {expected_token}"
        assert cmd_list.index("-c") < cmd_list.index("ls-remote")

    @patch('codemie.datasource.loader.git_loader.git_cmd.Git')
    def test_test_connection_raises_on_failure(self, mock_git_cls):
        """test_connection raises ConnectionException when ls-remote fails."""
        from git.exc import GitCommandError

        mock_git_cls.return_value.execute.side_effect = GitCommandError("ls-remote", 128)

        creds = Credentials(
            url="https://github.com",
            token="mytoken",
            token_name="oauth2",
            auth_type="pat",
        )
        with pytest.raises(ConnectionException):
            GitBatchLoader.test_connection("https://github.com/owner/private-repo", creds)


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
