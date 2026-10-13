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
from codemie_tools.data_management.code_executor.tools_vars import CODE_EXECUTOR_TOOL
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
    @pytest.fixture(autouse=True)
    def default_gating_env(self, monkeypatch):
        # A developer's local .env may enable these; tests assume the defaults unless they set them
        monkeypatch.setenv("FILE_SYSTEM_TOOLS_ENABLED", "false")
        monkeypatch.setenv("CODE_EXECUTOR_ENABLED", "false")

    @pytest.fixture
    def toolkit(self):
        return FileSystemToolkit.get_toolkit(configs={})

    def test_get_tools_ui_info_admin_without_env_var(self, toolkit):
        # Admin without env vars should only see the safe tool (GenerateImage) in UI
        result = toolkit.get_tools_ui_info(is_admin=True)
        assert 'tools' in result, "UI info does not contain 'tools'"
        assert len(result['tools']) == 1, "Admin without env vars should see only 1 tool in UI"

    def test_get_tools_ui_info_admin_with_env_var(self, toolkit, monkeypatch):
        # Admin with file system env var should see file system tools (7 without executor)
        monkeypatch.setenv("FILE_SYSTEM_TOOLS_ENABLED", "true")
        result = toolkit.get_tools_ui_info(is_admin=True)
        assert 'tools' in result, "UI info does not contain 'tools'"
        assert len(result['tools']) == 7, "Admin with env var should see 7 tools in UI (executor gated off)"

    def test_get_tools_ui_info_non_admin(self, toolkit):
        # Non-admin should only see the safe tool in UI
        result = toolkit.get_tools_ui_info(is_admin=False)
        assert 'tools' in result, "UI info does not contain 'tools'"
        assert len(result['tools']) == 1, "Non-admin should see only 1 tool in UI"

    def test_get_tools_ui_info_non_admin_with_env_var(self, toolkit, monkeypatch):
        # Non-admin with env var should still only see the safe tool in UI
        monkeypatch.setenv("FILE_SYSTEM_TOOLS_ENABLED", "true")
        result = toolkit.get_tools_ui_info(is_admin=False)
        assert 'tools' in result, "UI info does not contain 'tools'"
        assert len(result['tools']) == 1, "Non-admin should see only 1 tool in UI even with env var"

    def test_get_tools_non_admin(self, toolkit):
        # Non-admin users should only get the safe tool (executor gated off by default)
        tools = toolkit.get_tools()
        assert len(tools) == 1, "Non-admin should only get 1 safe tool"
        assert any(isinstance(tool, GenerateImageTool) for tool in tools), "GenerateImageTool missing"
        assert not any(isinstance(tool, CodeExecutorTool) for tool in tools), "CodeExecutor should be gated off"
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
        # Without env vars should only get the safe tool
        toolkit = FileSystemToolkit.get_toolkit(configs={})
        tools = toolkit.get_tools()
        assert len(tools) == 1, "Without env vars should only get 1 safe tool"
        assert any(isinstance(tool, GenerateImageTool) for tool in tools), "GenerateImageTool missing"
        assert not any(isinstance(tool, CodeExecutorTool) for tool in tools), "CodeExecutor should be gated off"
        # Verify file system tools are NOT present
        assert not any(isinstance(tool, ReadFileTool) for tool in tools), "ReadFileTool should not be present"

    def test_get_tools_with_env_var(self, monkeypatch):
        # With file system env var set should get file system tools (7 without executor)
        monkeypatch.setenv("FILE_SYSTEM_TOOLS_ENABLED", "true")
        toolkit = FileSystemToolkit.get_toolkit(configs={})
        tools = toolkit.get_tools()
        assert len(tools) == 7, "With env var should get 7 tools (executor gated off)"
        assert any(isinstance(tool, ReadFileTool) for tool in tools), "ReadFileTool missing"
        assert any(isinstance(tool, ListDirectoryTool) for tool in tools), "ListDirectoryTool missing"
        assert any(isinstance(tool, WriteFileTool) for tool in tools), "WriteFileTool missing"
        assert any(isinstance(tool, CommandLineTool) for tool in tools), "CommandLineTool missing"
        assert any(isinstance(tool, DiffUpdateFileTool) for tool in tools), "DiffUpdateFileTool missing"
        assert any(isinstance(tool, GenerateImageTool) for tool in tools), "GenerateImageTool missing"
        assert any(isinstance(tool, ReplaceStringTool) for tool in tools), "ReplaceStringTool missing"
        assert not any(isinstance(tool, CodeExecutorTool) for tool in tools), "CodeExecutor should be gated off"

    def test_get_tools_with_root_directory(self, monkeypatch):
        # Test with env var enabled to verify root_dir is set correctly
        monkeypatch.setenv("FILE_SYSTEM_TOOLS_ENABLED", "true")
        root_dir = "/test/directory"
        toolkit = FileSystemToolkit.get_toolkit(configs={"root_directory": root_dir})
        tools = toolkit.get_tools()
        for tool in tools:
            if hasattr(tool, 'root_dir'):
                assert tool.root_dir == root_dir, f"Root directory not set correctly for {tool.__class__.__name__}"

    def test_get_tools_code_executor_enabled(self, monkeypatch):
        # With CODE_EXECUTOR_ENABLED=true the executor is instantiated
        monkeypatch.setenv("CODE_EXECUTOR_ENABLED", "true")
        toolkit = FileSystemToolkit.get_toolkit(configs={})
        tools = toolkit.get_tools()
        assert any(isinstance(tool, GenerateImageTool) for tool in tools), "GenerateImageTool missing"
        assert any(isinstance(tool, CodeExecutorTool) for tool in tools), "CodeExecutor should be present when enabled"

    def test_get_tools_ui_info_code_executor_enabled_non_admin(self, monkeypatch):
        # With CODE_EXECUTOR_ENABLED=true non-admins see the executor in the UI catalog
        monkeypatch.setenv("CODE_EXECUTOR_ENABLED", "true")
        toolkit = FileSystemToolkit.get_toolkit(configs={})
        result = toolkit.get_tools_ui_info(is_admin=False)
        names = [tool['name'] for tool in result['tools']]
        assert CODE_EXECUTOR_TOOL.name in names, "CodeExecutor should be listed in UI when enabled"

    def test_get_tools_ui_info_code_executor_enabled_admin(self, monkeypatch):
        # With both env vars enabled admins see the executor alongside file system tools
        monkeypatch.setenv("CODE_EXECUTOR_ENABLED", "true")
        monkeypatch.setenv("FILE_SYSTEM_TOOLS_ENABLED", "true")
        toolkit = FileSystemToolkit.get_toolkit(configs={})
        result = toolkit.get_tools_ui_info(is_admin=True)
        names = [tool['name'] for tool in result['tools']]
        assert CODE_EXECUTOR_TOOL.name in names, "CodeExecutor should be listed for admins when enabled"
        assert len(result['tools']) == 8, "Admin with both env vars should see all 8 tools in UI"

    def test_get_tools_ui_info_code_executor_disabled_by_default(self, toolkit):
        # By default the executor must not appear in any UI catalog
        non_admin = toolkit.get_tools_ui_info(is_admin=False)
        assert CODE_EXECUTOR_TOOL.name not in [t['name'] for t in non_admin['tools']]

    def test_get_tools_ui_info_code_executor_disabled_admin_with_fs(self, toolkit, monkeypatch):
        # Admin with file system tools on but executor off must not see the executor
        monkeypatch.setenv("FILE_SYSTEM_TOOLS_ENABLED", "true")
        result = toolkit.get_tools_ui_info(is_admin=True)
        assert CODE_EXECUTOR_TOOL.name not in [t['name'] for t in result['tools']]
