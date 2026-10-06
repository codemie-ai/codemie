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


_EXCHANGE_DIR = ".codemie_bridge/1700000000-abc123"


def _run_wrapper(wrapper: str, workspace_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        cwd=workspace_root,
        text=True,
        capture_output=True,
        check=False,
    )


def _sdk_config(run_seconds: float | None = 120.0) -> dict[str, object]:
    return {"exchange_dir": _EXCHANGE_DIR, "run_seconds": run_seconds, "max_payload_bytes": 262144}


def _make_workspace(tmp_path: Path, script_source: str) -> Path:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "script.py").write_text(script_source)
    return workspace_root


def test_workspace_wrapper_with_exchange_dir_exposes_configured_sdk(tmp_path: Path) -> None:
    workspace_root = _make_workspace(
        tmp_path,
        "import codemie_runtime_sdk\n"
        "print(codemie_runtime_sdk._exchange_dir)\n"
        "print(codemie_runtime_sdk.PROTOCOL_VERSION)\n",
    )
    wrapper = build_guarded_workspace_script("script.py", workspace_root=str(workspace_root), sdk_config=_sdk_config())

    result = _run_wrapper(wrapper, workspace_root)

    assert result.returncode == 0, result.stderr
    state, version = result.stdout.split()
    assert state == _EXCHANGE_DIR
    assert version == "1"
    assert (workspace_root / _EXCHANGE_DIR).is_dir()
    assert not (workspace_root / _EXCHANGE_DIR / "pid").exists()
    assert not (workspace_root / _EXCHANGE_DIR / "start_time").exists()


def test_workspace_wrapper_passes_the_run_limit_to_the_sdk(tmp_path: Path) -> None:
    workspace_root = _make_workspace(
        tmp_path,
        "import codemie_runtime_sdk as sdk\nprint(sdk._run_seconds)\n",
    )
    wrapper = build_guarded_workspace_script(
        "script.py", workspace_root=str(workspace_root), sdk_config=_sdk_config(run_seconds=180.0)
    )

    result = _run_wrapper(wrapper, workspace_root)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "180.0"


def test_workspace_wrapper_without_a_run_limit_leaves_it_unset(tmp_path: Path) -> None:
    workspace_root = _make_workspace(
        tmp_path,
        "import codemie_runtime_sdk as sdk\nprint(sdk._run_seconds)\n",
    )
    wrapper = build_guarded_workspace_script(
        "script.py", workspace_root=str(workspace_root), sdk_config=_sdk_config(run_seconds=None)
    )

    result = _run_wrapper(wrapper, workspace_root)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "None"


def test_workspace_wrapper_without_exchange_dir_leaves_sdk_unconfigured(tmp_path: Path) -> None:
    workspace_root = _make_workspace(
        tmp_path,
        "import codemie_runtime_sdk as sdk\n"
        "try:\n"
        "    sdk.call('x', {})\n"
        "except sdk.ToolCallError as exc:\n"
        "    print(exc.code)\n",
    )
    wrapper = build_guarded_workspace_script("script.py", workspace_root=str(workspace_root))

    result = _run_wrapper(wrapper, workspace_root)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "unavailable"
    assert not (workspace_root / ".codemie_bridge").exists()


def test_workspace_wrapper_stdout_carries_only_script_output(tmp_path: Path) -> None:
    workspace_root = _make_workspace(tmp_path, "print('only-this')\n")
    wrapper = build_guarded_workspace_script("script.py", workspace_root=str(workspace_root), sdk_config=_sdk_config())

    result = _run_wrapper(wrapper, workspace_root)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "only-this\n"


def test_guarded_python_script_does_not_embed_sdk() -> None:
    script = build_guarded_python_script("print('hello')", workspace_root="/home/codemie/u")

    assert "codemie_runtime_sdk" not in script


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
