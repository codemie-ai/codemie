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

import os
import unittest
from unittest.mock import patch

import pytest

from codemie_tools.data_management.file_system.tools import (
    ReadFileTool,
    ListDirectoryTool,
    WriteFileTool,
    CommandLineTool,
)


# Updated tests to use relative paths within the permissible directory
@pytest.mark.parametrize(
    "file_path, expected",
    [('test_file.txt', 'New file content'), ('invalid/path.txt', 'Error: no such file or directory: invalid/path.txt')],
)
def test_read_file_tool(file_path, expected):
    tool = ReadFileTool(root_dir='tests')
    result = tool.execute(file_path=file_path)
    assert result == expected


class TestFileSystemTools(unittest.TestCase):
    def setUp(self):
        self.command_line_tool = CommandLineTool(root_dir='.')

    def test_list_directory_tool(self):
        tool = ListDirectoryTool(root_dir='tests')
        result = tool.execute(dir_path='.')
        assert result != 'Error: '
        assert 'test_file.txt' in result

    def test_write_file_tool(self):
        tool = WriteFileTool(root_dir='tests')
        file_path = 'test_file.txt'
        content = 'New file content'
        result = tool.execute(file_path=file_path, text=content)
        assert 'File written successfully to test_file.txt' in result

    def test_command_line_tool(self):
        tool = CommandLineTool()
        command = 'echo "Hello, World!"'
        stdout, stderr, returncode, start_time = tool.execute(command=command)
        assert stdout.strip() == 'Hello, World!'
        assert stderr == ''
        assert returncode == 0

    def test_sanitize_command_allows_safe_commands(self):
        safe_commands = [
            "ls -l",
            "mkdir test_dir",
            "touch test_file.txt",
            "echo 'Hello, World!' > test_file.txt",
            "cp test_file.txt backup.txt",
            "mv test_file.txt renamed.txt",
        ]
        for command in safe_commands:
            with self.subTest(command=command):
                self.command_line_tool.sanitize_command(command)

    def test_sanitize_command_blocks_rm_rf(self):
        dangerous_command = "rm -rf /"
        with self.assertRaisesRegex(Exception, "Use of 'rm -rf' command is not allowed."):
            self.command_line_tool.sanitize_command(dangerous_command)

    def test_sanitize_command_blocks_mv_to_dev_null(self):
        dangerous_command = "mv test_file.txt /dev/null"
        with self.assertRaisesRegex(Exception, "Moving files to /dev/null is not allowed."):
            self.command_line_tool.sanitize_command(dangerous_command)

    def test_sanitize_command_blocks_dd(self):
        dangerous_command = "dd if=/dev/zero of=/dev/sda"
        with self.assertRaisesRegex(Exception, "Use of 'dd' command is not allowed."):
            self.command_line_tool.sanitize_command(dangerous_command)

    def test_sanitize_command_blocks_overwriting_disk_blocks(self):
        dangerous_command = "echo 'test' > /dev/sda1"
        with self.assertRaisesRegex(Exception, "Overwriting disk blocks directly is not allowed."):
            self.command_line_tool.sanitize_command(dangerous_command)

    def test_sanitize_command_blocks_fork_bombs(self):
        dangerous_command = ":(){ :|:& };:"
        with self.assertRaisesRegex(Exception, "Fork bombs are not allowed."):
            self.command_line_tool.sanitize_command(dangerous_command)

    @patch('codemie_tools.data_management.file_system.tools.logger.error')
    def test_sanitize_command_logs_error(self, mock_logger):
        from langchain_core.tools import ToolException

        dangerous_command = "rm -rf /"
        with self.assertRaises(ToolException):
            self.command_line_tool.sanitize_command(dangerous_command)
        mock_logger.assert_called_with(
            "Potentially dangerous command detected: Use of 'rm -rf' command is not allowed."
        )


@pytest.fixture
def temp_file_exist():
    # Create the file
    file_path = "temp_test_file.txt"
    with open(file_path, "w") as f:
        f.write("Test content")

    yield file_path  # This provides the file path to the test

    # Clean up after the test
    if os.path.exists(file_path):
        os.remove(file_path)


@pytest.fixture
def temp_file_new():
    # Create the file
    file_path = "temp_test_new.txt"

    yield file_path  # This provides the file path to the test

    # Clean up after the test
    if os.path.exists(file_path):
        os.remove(file_path)
