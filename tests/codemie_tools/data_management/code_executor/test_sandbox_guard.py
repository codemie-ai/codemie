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

from unittest.mock import MagicMock

import subprocess
import sys
from pathlib import Path

import pytest
from langchain_core.tools import ToolException

from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool
from codemie_tools.data_management.code_executor.filesystem_policy import DENIAL_MARKER
from codemie_tools.data_management.code_executor.sandbox_guard import (
    build_guarded_python_script,
    build_guarded_workspace_script,
    extract_denial_events,
)


def test_build_guarded_python_script_embeds_customer_code_and_workspace() -> None:
    script = build_guarded_python_script("print('hello')", workspace_root="/home/codemie/u")

    assert "print('hello')" in script
    assert "/home/codemie/u" in script
    assert DENIAL_MARKER in script


def test_build_guarded_workspace_script_uses_runpy_launcher() -> None:
    script = build_guarded_workspace_script("scripts/run_me.py", workspace_root="/home/codemie/u")

    assert "runpy.run_path" in script
    assert "scripts/run_me.py" in script


def test_extract_denial_events_parses_marker_lines() -> None:
    stderr = (
        f'{DENIAL_MARKER}{{"operation":"open","path":"../x","reason":"outside_workspace"}}\n'
        "Filesystem access denied: path is outside the execution workspace\n"
    )

    assert extract_denial_events(stderr) == [{"operation": "open", "path": "../x", "reason": "outside_workspace"}]


def test_extract_denial_events_ignores_malformed_marker_lines() -> None:
    stderr = (
        f"{DENIAL_MARKER}not-json\n"
        f'{DENIAL_MARKER}{{"operation":"open","path":"../x","reason":"outside_workspace"}}\n'
    )

    assert extract_denial_events(stderr) == [{"operation": "open", "path": "../x", "reason": "outside_workspace"}]


def test_build_guarded_workspace_script_runs_workspace_script(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "script.py").write_text("print('workspace-script-ok')\n")
    script = build_guarded_workspace_script("script.py", workspace_root=str(workspace_root))

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=workspace_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "workspace-script-ok"


def test_format_execution_result_hides_denial_markers_from_stderr() -> None:
    tool = CodeExecutorTool(file_repository=MagicMock(), user_id="test_user")
    result = MagicMock(
        stdout="",
        stderr=(
            f'{DENIAL_MARKER}{{"operation":"open","path":"../x","reason":"outside_workspace"}}\n'
            "Filesystem access denied: path is outside the execution workspace\n"
        ),
        exit_code=1,
        plots=[],
    )

    with pytest.raises(ToolException) as exc_info:
        tool._format_execution_result(result)

    assert "Filesystem access denied" in str(exc_info.value)
    assert DENIAL_MARKER not in str(exc_info.value)
