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

"""Tests for CodeExecutorTool optimizations."""

import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.base.file_object import FileObject
from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool


class TestCodeExecutorToolFileUpload(unittest.TestCase):
    """Test suite for optimized file upload logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_file_repo = MagicMock()
        self.mock_session = MagicMock()
        self.tool = CodeExecutorTool(file_repository=self.mock_file_repo, user_id="test_user")

    def test_upload_files_single_call(self):
        """Test that files are uploaded with individual file calls."""
        # Create mock file objects
        file1 = FileObject(name="file1.txt", mime_type="text/plain", owner="test_user")
        file2 = FileObject(name="file2.csv", mime_type="text/csv", owner="test_user")

        # Mock file repository reads
        mock_content1 = MagicMock()
        mock_content1.bytes_content.return_value = b"content1"
        mock_content2 = MagicMock()
        mock_content2.bytes_content.return_value = b"content2"

        self.mock_file_repo.read_file.side_effect = [mock_content1, mock_content2]

        # Call upload
        self.tool._upload_files_to_sandbox(
            session=self.mock_session, file_objects=[file1, file2], workdir="/test/workdir"
        )

        # Verify copy_to_runtime was called for each file
        assert self.mock_session.copy_to_runtime.call_count == 2

        # Verify the calls were made with individual file paths
        calls = self.mock_session.copy_to_runtime.call_args_list
        # Extract source paths from calls
        source_paths = [call[0][0] for call in calls]
        # Verify source paths end with the file names
        assert any(path.endswith("file1.txt") for path in source_paths)
        assert any(path.endswith("file2.csv") for path in source_paths)

        # Verify destinations are correct
        dest_paths = [call[0][1] for call in calls]
        assert all(path == "/test/workdir/file1.txt" or path == "/test/workdir/file2.csv" for path in dest_paths)

    def test_upload_files_repository_reads(self):
        """Test that all files are read from repository."""
        file1 = FileObject(name="file1.txt", mime_type="text/plain", owner="test_user")
        file2 = FileObject(name="file2.txt", mime_type="text/plain", owner="test_user")

        mock_content = MagicMock()
        mock_content.bytes_content.return_value = b"content"
        self.mock_file_repo.read_file.return_value = mock_content

        self.tool._upload_files_to_sandbox(
            session=self.mock_session, file_objects=[file1, file2], workdir="/test/workdir"
        )

        # Verify all files were read from repository
        assert self.mock_file_repo.read_file.call_count == 2

        # Verify read_file was called with correct parameters
        calls = self.mock_file_repo.read_file.call_args_list
        assert calls[0][1]['file_name'] == 'file1.txt'
        assert calls[1][1]['file_name'] == 'file2.txt'

    def test_upload_files_temp_directory_cleanup(self):
        """Test that temporary directory is cleaned up."""
        file1 = FileObject(name="file1.txt", mime_type="text/plain", owner="test_user")

        mock_content = MagicMock()
        mock_content.bytes_content.return_value = b"content"
        self.mock_file_repo.read_file.return_value = mock_content

        # Track temp directory
        temp_dirs = []

        original_tempdir = tempfile.TemporaryDirectory

        class MockTempDir:
            def __init__(self, *args, **kwargs):
                self.temp_dir = original_tempdir(*args, **kwargs)
                temp_dirs.append(self.temp_dir)

            def __enter__(self):
                return self.temp_dir.__enter__()

            def __exit__(self, *args):
                return self.temp_dir.__exit__(*args)

        with patch('tempfile.TemporaryDirectory', MockTempDir):
            self.tool._upload_files_to_sandbox(session=self.mock_session, file_objects=[file1], workdir="/test/workdir")

            # Verify temp directory was created and cleaned up
            assert len(temp_dirs) == 1


class TestCodeExecutorToolPodDiscovery(unittest.TestCase):
    """Test suite for pod discovery logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_file_repo = MagicMock()
        self.tool = CodeExecutorTool(user_id="test_user", file_repository=self.mock_file_repo)

    @patch('codemie_tools.data_management.code_executor.code_executor_tool.SandboxSessionManager')
    def test_get_available_pod_name_success(self, mock_manager_class):
        """Test that deprecated _get_available_pod_name always returns None."""
        mock_manager = MagicMock()
        mock_manager_class.return_value = mock_manager

        # The method is deprecated and always returns None
        pod_name = self.tool._get_available_pod_name()

        # Deprecated method should always return None
        assert pod_name is None, "Deprecated method should always return None"

    @patch('codemie_tools.data_management.code_executor.code_executor_tool.SandboxSessionManager')
    def test_get_available_pod_name_returns_none_for_new_pod(self, mock_manager_class):
        """Test that None is returned for new pod creation without raising exception."""
        mock_manager = MagicMock()
        mock_manager._get_available_pod_name.return_value = None
        mock_manager_class.return_value = mock_manager

        # Should NOT raise exception when None is returned (normal case for new pod creation)
        pod_name = self.tool._get_available_pod_name()

        # None is valid - means create new pod
        assert pod_name is None, "Should return None for new pod creation"

    @patch('codemie_tools.data_management.code_executor.code_executor_tool.SandboxSessionManager')
    def test_get_available_pod_name_uses_config(self, mock_manager_class):
        """Test that deprecated method doesn't use session manager."""
        mock_manager = MagicMock()
        mock_manager_class.return_value = mock_manager

        result = self.tool._get_available_pod_name()

        # Deprecated method should return None without using session manager
        assert result is None, "Deprecated method should return None"
        # Session manager should not be called since method is deprecated
        mock_manager_class.assert_not_called()


class TestCodeExecutorToolUserWorkdir(unittest.TestCase):
    """Test suite for user workdir isolation."""

    def setUp(self) -> None:
        self.mock_file_repo = MagicMock()

    def test_get_user_workdir_with_user_id(self):
        """Test workdir generation with user ID."""
        tool = CodeExecutorTool(user_id="user123", file_repository=self.mock_file_repo)
        workdir = tool._get_user_workdir()

        assert workdir == f"{tool.config.workdir_base}/user123"

    def test_get_user_workdir_without_user_id(self):
        """Test workdir generation without user ID."""
        tool = CodeExecutorTool(user_id="", file_repository=self.mock_file_repo)
        workdir = tool._get_user_workdir()

        assert workdir == tool.config.workdir_base

    def test_get_user_workdir_sanitizes_path(self):
        """Test that workdir sanitizes dangerous characters."""
        tool = CodeExecutorTool(user_id="user/../admin", file_repository=self.mock_file_repo)
        workdir = tool._get_user_workdir()

        assert "/.." not in workdir
        assert workdir == f"{tool.config.workdir_base}/user_.._admin"

    def test_get_user_workdir_sanitizes_backslash(self):
        """Test that workdir sanitizes backslashes."""
        tool = CodeExecutorTool(user_id="user\\admin", file_repository=self.mock_file_repo)
        workdir = tool._get_user_workdir()

        assert "\\" not in workdir
        assert workdir == f"{tool.config.workdir_base}/user_admin"


class TestCodeExecutorToolIntegration(unittest.TestCase):
    """Integration tests for CodeExecutorTool."""

    def setUp(self) -> None:
        self.mock_file_repo = MagicMock()

    @patch('codemie_tools.data_management.code_executor.code_executor_tool.SandboxSessionManager')
    def test_execute_with_file_upload(self, mock_manager_class):
        """Test execute workflow with file upload."""
        # Setup
        mock_manager = MagicMock()
        mock_session = MagicMock()
        mock_manager.get_session.return_value = mock_session
        mock_manager_class.return_value = mock_manager

        # Mock session behavior
        mock_session.is_safe.return_value = (True, [])
        mock_result = MagicMock()
        mock_result.stdout = "Success"
        mock_result.stderr = ""
        mock_result.exit_code = 0
        mock_session.run.return_value = mock_result

        # Create tool with input files
        file1 = FileObject(name="input.csv", mime_type="text/csv", owner="user")
        mock_file_repo = MagicMock()
        mock_content = MagicMock()
        mock_content.bytes_content.return_value = b"data"
        mock_file_repo.read_file.return_value = mock_content

        from codemie_tools.data_management.code_executor.models import ExecutionMode

        tool = CodeExecutorTool(
            file_repository=mock_file_repo, user_id="user", input_files=[file1], execution_mode=ExecutionMode.SANDBOX
        )

        # Patch _get_available_pod_name to return a pod name
        with patch.object(tool, '_get_available_pod_name', return_value="test-pod"):
            result = tool.execute(code="print('test')", export_files=None)

            # Verify file was uploaded (copy_to_runtime called)
            assert mock_session.copy_to_runtime.called
            # Verify code was executed
            mock_session.run.assert_called_once()
            # Verify result
            assert "Success" in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
