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

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import ToolException

from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool
from codemie_tools.data_management.code_executor.models import SandboxMode
from codemie_tools.data_management.workspace.execute_workspace_script_tool import WorkspaceScriptRunner

WORKDIR = "/home/codemie/test_user"


@pytest.mark.parametrize("export_files", [None, []])
def test_validate_export_paths_falsy_input_passes(export_files):
    CodeExecutorTool._validate_export_paths(export_files, WORKDIR)


@pytest.mark.parametrize("path", ["output.csv", "subdir/result.png"])
def test_validate_export_paths_allowed_relative_passes(path):
    CodeExecutorTool._validate_export_paths([path], WORKDIR)


def test_validate_export_paths_normalizes_to_inside_workdir():
    # subdir/../output.csv resolves to /home/codemie/test_user/output.csv — still inside workdir
    CodeExecutorTool._validate_export_paths(["subdir/../output.csv"], WORKDIR)


@pytest.mark.parametrize(
    "path",
    [
        "/proc/1/environ",
        "/etc/passwd",
        "/sys/class/net",
        "/var/log/syslog",
        "/proc/net/tcp",
    ],
)
def test_validate_export_paths_absolute_path_raises(path):
    with pytest.raises(ToolException):
        CodeExecutorTool._validate_export_paths([path], WORKDIR)


@pytest.mark.parametrize(
    "path",
    [
        "../../etc/passwd",
        "../other_user/data.csv",
        "..",
        "subdir/..",
    ],
)
def test_validate_export_paths_traversal_raises(path):
    with pytest.raises(ToolException):
        CodeExecutorTool._validate_export_paths([path], WORKDIR)


def test_execute_sandbox_blocks_bad_export_path_before_io():
    tool = CodeExecutorTool(file_repository=MagicMock(), user_id="test_user")
    with (
        patch.object(tool, "_sandbox_session", side_effect=AssertionError("should not reach session")),
        patch(
            "codemie_tools.data_management.code_executor.code_executor_tool.run_via_jobs",
            side_effect=AssertionError("should not reach jobs I/O"),
        ),
    ):
        with pytest.raises(ToolException, match="working directory"):
            tool._execute_sandbox("print('hi')", export_files=["../../etc/passwd"])


def test_execute_sandbox_keeps_export_validation_before_guard_build():
    tool = CodeExecutorTool(file_repository=MagicMock(), user_id="test_user")

    with patch(
        "codemie_tools.data_management.code_executor.code_executor_tool.build_guarded_python_script",
        side_effect=AssertionError("guard should not build for invalid export paths"),
    ):
        with pytest.raises(ToolException, match="working directory"):
            tool._execute_sandbox("print('hi')", export_files=["../../etc/passwd"])


def test_execute_sandbox_script_blocks_bad_export_path_before_io():
    tool = WorkspaceScriptRunner(file_repository=MagicMock(), user_id="test_user")
    with (
        patch.object(tool, "_sandbox_session", side_effect=AssertionError("should not reach session")),
        patch.object(tool, "_get_script_content", return_value="print('hi')"),
        patch.object(tool, "_validate_code_security"),
        patch.object(tool, "_execute_code_sandbox", side_effect=AssertionError("should not reach execution")),
    ):
        with pytest.raises(ToolException, match="working directory"):
            tool._execute_sandbox_script("/workspace/script.py", export_files=["../other_user/secret.txt"])


def test_execute_sandbox_blocks_bad_export_path_jobs_mode():
    tool = CodeExecutorTool(file_repository=MagicMock(), user_id="test_user")
    tool.config = tool.config.model_copy(update={"sandbox_mode": SandboxMode.JOBS})
    with patch(
        "codemie_tools.data_management.code_executor.code_executor_tool.run_via_jobs",
        side_effect=AssertionError("should not reach jobs runner"),
    ):
        with pytest.raises(ToolException, match="working directory"):
            tool._execute_sandbox("print('hi')", export_files=["../../etc/passwd"])


def test_execute_sandbox_script_blocks_bad_export_path_jobs_mode():
    tool = WorkspaceScriptRunner(file_repository=MagicMock(), user_id="test_user")
    tool.config = tool.config.model_copy(update={"sandbox_mode": SandboxMode.JOBS})
    with patch.object(tool, "_execute_sandbox_script_jobs", side_effect=AssertionError("should not reach jobs runner")):
        with pytest.raises(ToolException, match="working directory"):
            tool._execute_sandbox_script("/workspace/script.py", export_files=["../other_user/secret.txt"])
