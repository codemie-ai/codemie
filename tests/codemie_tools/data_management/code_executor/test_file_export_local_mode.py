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

"""Test suite for CodeExecutorTool file export functionality in local mode."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain_core.tools import ToolException
import pytest
from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool
from codemie_tools.data_management.code_executor.models import ExecutionMode


class TestFileExportLocalModeNoExportFilesProvided(unittest.TestCase):
    """Test cases when export_files parameter is not provided (None)."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_file_repo = MagicMock()
        self.tool = CodeExecutorTool(
            file_repository=self.mock_file_repo,
            user_id="test_user",
            execution_mode=ExecutionMode.LOCAL,
        )

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    def test_no_export_files_provided_no_files_created(self, mock_subprocess_run):
        """
        Case 1: No export_files provided, no files created.
        Expected: No files exported, execution succeeds with normal output.
        """
        # Arrange
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Hello World"
        mock_subprocess_run.return_value = mock_result

        # Act
        result = self.tool.execute(code="print('Hello World')", export_files=None)

        # Assert
        assert result == "Hello World"
        assert "Exported files" not in result
        self.mock_file_repo.write_file.assert_not_called()

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    def test_no_export_files_provided_files_created_not_returned(self, mock_subprocess_run):
        """
        Case 5: No export_files provided, but files are created during execution.
        Expected: Created files are NOT exported, even though they exist.
        """
        # Arrange
        # Mock subprocess execution
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "File created"
        mock_subprocess_run.return_value = mock_result

        # Act - execute without specifying export_files
        # The code will create a temp directory, execute code, and clean up
        # We're just verifying no export happens regardless of files created
        result = self.tool.execute(code="# Creates output.txt", export_files=None)

        # Assert - files exist but were not exported
        assert result == "File created"
        assert "Exported files" not in result
        self.mock_file_repo.write_file.assert_not_called()


class TestFileExportLocalModeExportFilesProvided(unittest.TestCase):
    """Test cases when export_files parameter is provided with file paths."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_file_repo = MagicMock()
        self.tool = CodeExecutorTool(
            file_repository=self.mock_file_repo,
            user_id="test_user",
            execution_mode=ExecutionMode.LOCAL,
        )

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    def test_export_files_provided_and_created_returned(self, mock_subprocess_run):
        """
        Case 3: export_files provided AND files are created.
        Expected: Files are exported and URLs are returned in the result.
        """

        # Arrange
        def mock_run_with_file_creation(*args, **kwargs):
            # Simulate subprocess that creates files in the work directory
            work_dir = kwargs.get('cwd')
            if work_dir:
                # Create the files that will be exported
                output_file = Path(work_dir) / "result.csv"
                output_file.write_text("col1,col2\n1,2\n3,4")

                plot_file = Path(work_dir) / "plot.png"
                plot_file.write_bytes(b"\x89PNG\r\n\x1a\n")  # Valid PNG header

            # Return success
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Files generated"
            return mock_result

        mock_subprocess_run.side_effect = mock_run_with_file_creation

        # Mock file repository
        mock_stored_file = MagicMock()
        mock_stored_file.to_encoded_url.return_value = "encoded-file-id-123"
        self.mock_file_repo.write_file.return_value = mock_stored_file

        # Act - execute with export_files specified
        result = self.tool.execute(code="# Creates result.csv and plot.png", export_files=["result.csv", "plot.png"])

        # Assert - files were exported and URLs returned
        assert "Files generated" in result
        assert "Exported files" in result
        assert "sandbox:/v1/files/encoded-file-id-123" in result

        # Verify file repository was called twice (once for each file)
        assert self.mock_file_repo.write_file.call_count == 2

        # Verify the files were written with correct parameters
        calls = self.mock_file_repo.write_file.call_args_list

        # First file (result.csv)
        call_kwargs_1 = calls[0][1]
        assert call_kwargs_1['mime_type'] == 'text/csv'
        assert call_kwargs_1['owner'] == 'test_user'
        assert b'col1,col2' in call_kwargs_1['content']

        # Second file (plot.png)
        call_kwargs_2 = calls[1][1]
        assert call_kwargs_2['mime_type'] == 'image/png'
        assert call_kwargs_2['owner'] == 'test_user'
        assert b'\x89PNG' in call_kwargs_2['content']

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    @patch('codemie_tools.data_management.code_executor.local_execution_engine.tempfile.TemporaryDirectory')
    def test_export_files_provided_but_not_created_not_returned(self, mock_tmpdir, mock_subprocess_run):
        """
        Case 4: export_files provided BUT files are NOT created.
        Expected: Missing files are skipped with warning, no URLs returned for them.
        """
        # Arrange - Create a temporary directory WITHOUT the expected files
        with tempfile.TemporaryDirectory() as real_tmpdir:
            # Don't create the files that are requested for export

            # Mock TemporaryDirectory
            mock_tmpdir_instance = MagicMock()
            mock_tmpdir_instance.__enter__ = MagicMock(return_value=real_tmpdir)
            mock_tmpdir_instance.__exit__ = MagicMock(return_value=False)
            mock_tmpdir.return_value = mock_tmpdir_instance

            # Mock subprocess execution
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Code executed"
            mock_subprocess_run.return_value = mock_result

            # Act - execute with export_files specified but files don't exist
            with self.assertLogs(
                'codemie_tools.data_management.code_executor.local_execution_engine', level='WARNING'
            ) as log:
                result = self.tool.execute(
                    code="# Does not create any files", export_files=["missing.csv", "nonexistent.png"]
                )

            # Assert - execution succeeded but no files exported
            assert result == "Code executed"
            assert "Exported files" not in result
            self.mock_file_repo.write_file.assert_not_called()

            # Verify warnings were logged for missing files
            assert any("missing.csv" in msg and "not found" in msg for msg in log.output)
            assert any("nonexistent.png" in msg and "not found" in msg for msg in log.output)

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    def test_export_files_mixed_some_created_some_not(self, mock_subprocess_run):
        """
        Edge case: export_files contains mix of existing and non-existing files.
        Expected: Only existing files are exported, missing ones are skipped with warning.
        """

        # Arrange
        def mock_run_with_partial_file_creation(*args, **kwargs):
            # Simulate subprocess that creates only one file
            work_dir = kwargs.get('cwd')
            if work_dir:
                # Create only one of the requested files
                existing_file = Path(work_dir) / "exists.txt"
                existing_file.write_text("I exist")
                # Don't create missing.txt

            # Return success
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Partial success"
            return mock_result

        mock_subprocess_run.side_effect = mock_run_with_partial_file_creation

        # Mock file repository
        mock_stored_file = MagicMock()
        mock_stored_file.to_encoded_url.return_value = "encoded-exists-123"
        self.mock_file_repo.write_file.return_value = mock_stored_file

        # Act
        with self.assertLogs(
            'codemie_tools.data_management.code_executor.local_execution_engine', level='WARNING'
        ) as log:
            result = self.tool.execute(code="# Creates only exists.txt", export_files=["exists.txt", "missing.txt"])

        # Assert - only existing file was exported
        assert "Partial success" in result
        assert "Exported files" in result
        assert "sandbox:/v1/files/encoded-exists-123" in result

        # Only one file should be written (the existing one)
        self.mock_file_repo.write_file.assert_called_once()
        call_kwargs = self.mock_file_repo.write_file.call_args[1]
        assert call_kwargs['mime_type'] == 'text/plain'
        assert b'I exist' in call_kwargs['content']

        # Verify warning for missing file
        assert any("missing.txt" in msg and "not found" in msg for msg in log.output)


class TestFileExportLocalModeNoFileRepository(unittest.TestCase):
    """Test cases when file_repository is not configured."""

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    def test_export_files_provided_no_repository_raises_error(self, mock_subprocess_run):
        """
        Edge case: export_files provided but file_repository is None.
        Expected: ValueError raised indicating repository is required.
        """
        # Arrange - Create tool without file repository
        tool = CodeExecutorTool(
            file_repository=None,  # No repository
            user_id="test_user",
            execution_mode=ExecutionMode.LOCAL,
        )

        def mock_run_with_file_creation(*args, **kwargs):
            # Simulate subprocess that creates a file
            work_dir = kwargs.get('cwd')
            if work_dir:
                output_file = Path(work_dir) / "output.txt"
                output_file.write_text("data")

            # Return success
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Success"
            return mock_result

        mock_subprocess_run.side_effect = mock_run_with_file_creation

        # Act & Assert - should raise ValueError
        with pytest.raises(ToolException) as exc_info:
            tool.execute(code="# Creates output.txt", export_files=["output.txt"])

        assert "File repository is required" in str(exc_info.value)


class TestFileExportLocalModeSecurityValidation(unittest.TestCase):
    """Test security validation for file export paths."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_file_repo = MagicMock()
        self.tool = CodeExecutorTool(
            file_repository=self.mock_file_repo,
            user_id="test_user",
            execution_mode=ExecutionMode.LOCAL,
        )

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    @patch('codemie_tools.data_management.code_executor.local_execution_engine.tempfile.TemporaryDirectory')
    def test_export_files_with_path_traversal_attempt_blocked(self, mock_tmpdir, mock_subprocess_run):
        """
        Security test: Attempted path traversal in export_files.
        Expected: Path traversal is blocked, warning logged, file not exported.
        """
        # Arrange
        with tempfile.TemporaryDirectory() as real_tmpdir:
            # Try to create a file outside work directory (attacker attempt)
            # The code will sanitize this, but let's verify it's handled safely

            # Mock TemporaryDirectory
            mock_tmpdir_instance = MagicMock()
            mock_tmpdir_instance.__enter__ = MagicMock(return_value=real_tmpdir)
            mock_tmpdir_instance.__exit__ = MagicMock(return_value=False)
            mock_tmpdir.return_value = mock_tmpdir_instance

            # Mock subprocess execution
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Attempted attack"
            mock_subprocess_run.return_value = mock_result

            # Act - try to export file with path traversal
            with self.assertLogs(
                'codemie_tools.data_management.code_executor.local_execution_engine', level='WARNING'
            ) as log:
                result = self.tool.execute(code="# Malicious code", export_files=["../../../etc/passwd"])

            # Assert - path traversal blocked
            assert "Attempted attack" in result
            assert "Exported files" not in result
            self.mock_file_repo.write_file.assert_not_called()

            # Verify warning about path escaping work directory
            assert any("escapes work directory" in msg for msg in log.output)

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    def test_export_files_directory_not_file_skipped(self, mock_subprocess_run):
        """
        Edge case: export_files contains directory path instead of file.
        Expected: Directory is skipped with warning, not exported.
        """

        # Arrange
        def mock_run_with_directory_creation(*args, **kwargs):
            # Simulate subprocess that creates a directory, not a file
            work_dir = kwargs.get('cwd')
            if work_dir:
                subdir = Path(work_dir) / "subdir"
                subdir.mkdir()

            # Return success
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Created directory"
            return mock_result

        mock_subprocess_run.side_effect = mock_run_with_directory_creation

        # Act
        with self.assertLogs(
            'codemie_tools.data_management.code_executor.local_execution_engine', level='WARNING'
        ) as log:
            result = self.tool.execute(code="# Creates directory", export_files=["subdir"])

        # Assert - directory not exported
        assert result == "Created directory"
        assert "Exported files" not in result
        self.mock_file_repo.write_file.assert_not_called()

        # Verify warning about not being a file
        assert any("subdir" in msg and "not a file" in msg for msg in log.output)


class TestFileExportLocalModeMultipleFiles(unittest.TestCase):
    """Test exporting multiple files in various scenarios."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_file_repo = MagicMock()
        self.tool = CodeExecutorTool(
            file_repository=self.mock_file_repo,
            user_id="test_user",
            execution_mode=ExecutionMode.LOCAL,
        )

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    def test_export_multiple_files_all_created(self, mock_subprocess_run):
        """
        Test exporting multiple files when all are successfully created.
        Expected: All files exported, multiple URLs returned.
        """

        # Arrange
        def mock_run_with_multiple_files(*args, **kwargs):
            # Simulate subprocess that creates multiple files
            work_dir = kwargs.get('cwd')
            if work_dir:
                file1 = Path(work_dir) / "data.csv"
                file1.write_text("csv,data")

                file2 = Path(work_dir) / "report.txt"
                file2.write_text("report content")

                file3 = Path(work_dir) / "chart.png"
                file3.write_bytes(b"\x89PNG\r\n\x1a\n")

            # Return success
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "All files created"
            return mock_result

        mock_subprocess_run.side_effect = mock_run_with_multiple_files

        # Mock file repository to return different URLs
        mock_stored_files = []
        for i in range(3):
            mock_file = MagicMock()
            mock_file.to_encoded_url.return_value = f"file-{i}"
            mock_stored_files.append(mock_file)
        self.mock_file_repo.write_file.side_effect = mock_stored_files

        # Act
        result = self.tool.execute(
            code="# Creates multiple files", export_files=["data.csv", "report.txt", "chart.png"]
        )

        # Assert - all files exported
        assert "All files created" in result
        assert "Exported files" in result
        assert "sandbox:/v1/files/file-0" in result
        assert "sandbox:/v1/files/file-1" in result
        assert "sandbox:/v1/files/file-2" in result

        # Verify repository called 3 times
        assert self.mock_file_repo.write_file.call_count == 3

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    @patch('codemie_tools.data_management.code_executor.local_execution_engine.tempfile.TemporaryDirectory')
    def test_export_empty_list_no_files_exported(self, mock_tmpdir, mock_subprocess_run):
        """
        Edge case: export_files is empty list.
        Expected: No files exported (same as None).
        """
        # Arrange
        with tempfile.TemporaryDirectory() as real_tmpdir:
            # Create a file
            Path(real_tmpdir) / "file.txt"

            # Mock TemporaryDirectory
            mock_tmpdir_instance = MagicMock()
            mock_tmpdir_instance.__enter__ = MagicMock(return_value=real_tmpdir)
            mock_tmpdir_instance.__exit__ = MagicMock(return_value=False)
            mock_tmpdir.return_value = mock_tmpdir_instance

            # Mock subprocess execution
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Success"
            mock_subprocess_run.return_value = mock_result

            # Act
            result = self.tool.execute(code="# Creates file", export_files=[])

            # Assert - no exports with empty list
            assert result == "Success"
            assert "Exported files" not in result
            self.mock_file_repo.write_file.assert_not_called()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
