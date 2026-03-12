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

from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool
from codemie_tools.data_management.code_executor.local_code_executor_tool import LocalCodeExecutorTool
from codemie_tools.data_management.file_system.generate_image_tool import GenerateImageTool
from codemie_tools.data_management.file_system.toolkit import FileSystemToolkit
from codemie_tools.data_management.file_system.tools import (
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
    CommandLineTool,
    DiffUpdateFileTool,
    ReplaceStringTool,
)


class TestFileSystemToolkit:
    @pytest.fixture
    def toolkit(self):
        return FileSystemToolkit.get_toolkit(configs={})

    def test_get_tools_ui_info_admin_without_env_var(self, toolkit):
        # Admin without env var should only see 3 tools in UI
        result = toolkit.get_tools_ui_info(is_admin=True)
        assert 'tools' in result, "UI info does not contain 'tools'"
        assert len(result['tools']) == 3, "Admin without env var should see only 3 tools in UI"

    def test_get_tools_ui_info_admin_with_env_var(self, toolkit, monkeypatch):
        # Admin with env var should see all 9 tools in UI
        monkeypatch.setenv("FILE_SYSTEM_TOOLS_ENABLED", "true")
        result = toolkit.get_tools_ui_info(is_admin=True)
        assert 'tools' in result, "UI info does not contain 'tools'"
        assert len(result['tools']) == 9, "Admin with env var should see all 9 tools in UI"

    def test_get_tools_ui_info_non_admin(self, toolkit):
        # Non-admin should only see 3 tools in UI
        result = toolkit.get_tools_ui_info(is_admin=False)
        assert 'tools' in result, "UI info does not contain 'tools'"
        assert len(result['tools']) == 3, "Non-admin should see only 3 tools in UI"

    def test_get_tools_ui_info_non_admin_with_env_var(self, toolkit, monkeypatch):
        # Non-admin with env var should still only see 3 tools in UI
        monkeypatch.setenv("FILE_SYSTEM_TOOLS_ENABLED", "true")
        result = toolkit.get_tools_ui_info(is_admin=False)
        assert 'tools' in result, "UI info does not contain 'tools'"
        assert len(result['tools']) == 3, "Non-admin should see only 3 tools in UI even with env var"

    def test_get_tools_non_admin(self, toolkit):
        # Non-admin users should only get safe tools (3 tools)
        tools = toolkit.get_tools()
        assert len(tools) == 3, "Non-admin should only get 3 safe tools"
        assert any(isinstance(tool, LocalCodeExecutorTool) for tool in tools), "LocalCodeExecutor missing"
        assert any(isinstance(tool, GenerateImageTool) for tool in tools), "GenerateImageTool missing"
        assert any(isinstance(tool, CodeExecutorTool) for tool in tools), "CodeExecutor missing"
        # Verify admin tools are NOT present
        assert not any(isinstance(tool, ReadFileTool) for tool in tools), "ReadFileTool should not be present"
        assert not any(isinstance(tool, ListDirectoryTool) for tool in tools), "ListDirectoryTool should not be present"
        assert not any(isinstance(tool, WriteFileTool) for tool in tools), "WriteFileTool should not be present"
        assert not any(isinstance(tool, CommandLineTool) for tool in tools), "CommandLineTool should not be present"
        assert not any(
            isinstance(tool, DiffUpdateFileTool) for tool in tools
        ), "DiffUpdateFileTool should not be present"
        assert not any(isinstance(tool, ReplaceStringTool) for tool in tools), "ReplaceStringTool should not be present"

    def test_get_tools_without_env_var(self):
        # Without env var should only get safe tools
        toolkit = FileSystemToolkit.get_toolkit(configs={})
        tools = toolkit.get_tools()
        assert len(tools) == 3, "Without env var should only get 3 safe tools"
        assert any(isinstance(tool, LocalCodeExecutorTool) for tool in tools), "LocalCodeExecutor missing"
        assert any(isinstance(tool, GenerateImageTool) for tool in tools), "GenerateImageTool missing"
        assert any(isinstance(tool, CodeExecutorTool) for tool in tools), "CodeExecutor missing"
        # Verify file system tools are NOT present
        assert not any(isinstance(tool, ReadFileTool) for tool in tools), "ReadFileTool should not be present"

    def test_get_tools_with_env_var(self, monkeypatch):
        # With env var set should get all 9 tools
        monkeypatch.setenv("FILE_SYSTEM_TOOLS_ENABLED", "true")
        toolkit = FileSystemToolkit.get_toolkit(configs={})
        tools = toolkit.get_tools()
        assert len(tools) == 9, "With env var should get all 9 tools"
        assert any(isinstance(tool, ReadFileTool) for tool in tools), "ReadFileTool missing"
        assert any(isinstance(tool, ListDirectoryTool) for tool in tools), "ListDirectoryTool missing"
        assert any(isinstance(tool, WriteFileTool) for tool in tools), "WriteFileTool missing"
        assert any(isinstance(tool, CommandLineTool) for tool in tools), "CommandLineTool missing"
        assert any(isinstance(tool, LocalCodeExecutorTool) for tool in tools), "LocalCodeExecutor missing"
        assert any(isinstance(tool, DiffUpdateFileTool) for tool in tools), "DiffUpdateFileTool missing"
        assert any(isinstance(tool, GenerateImageTool) for tool in tools), "GenerateImageTool missing"
        assert any(isinstance(tool, ReplaceStringTool) for tool in tools), "ReplaceStringTool missing"
        assert any(isinstance(tool, CodeExecutorTool) for tool in tools), "CodeExecutor missing"

    def test_get_tools_with_root_directory(self, monkeypatch):
        # Test with env var enabled to verify root_dir is set correctly
        monkeypatch.setenv("FILE_SYSTEM_TOOLS_ENABLED", "true")
        root_dir = "/test/directory"
        toolkit = FileSystemToolkit.get_toolkit(configs={"root_directory": root_dir})
        tools = toolkit.get_tools()
        for tool in tools:
            if hasattr(tool, 'root_dir'):
                assert tool.root_dir == root_dir, f"Root directory not set correctly for {tool.__class__.__name__}"
