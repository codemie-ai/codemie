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

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import MagicMock, patch
import json
import subprocess
import sys
import tempfile
import unittest

from codemie.rest_api.models.agent_workspace import (
    ExecuteWorkspaceScriptResponse,
    WorkspaceFileItemResponse,
)
from codemie_tools.base.file_object import FileObject
from codemie_tools.data_management.code_executor.models import (
    CodeExecutorConfig,
    ExecutionMode,
    SandboxMode,
)
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import BRIDGE_DIR_NAME
from codemie_tools.data_management.code_executor.tool_calling_limits import MAX_TOOL_CALLING_SECONDS
from codemie_tools.data_management.workspace.execute_workspace_script_tool import (
    ExecuteWorkspaceScriptTool,
    WorkspaceScriptRunner,
    _is_system_output_path,
)

_MODULE = "codemie_tools.data_management.workspace.execute_workspace_script_tool"


def _make_file_item() -> WorkspaceFileItemResponse:
    return WorkspaceFileItemResponse(
        path="src/foo.py",
        mime_type="text/x-python",
        checksum="abc123",
        size=42,
        version=1,
        update_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class TestExecuteWorkspaceScriptToolDumpJson(unittest.TestCase):
    def _make_tool(self) -> ExecuteWorkspaceScriptTool:
        return ExecuteWorkspaceScriptTool.__new__(ExecuteWorkspaceScriptTool)

    def test_checksum_excluded_from_workspace_files_in_response(self):
        tool = self._make_tool()
        response = ExecuteWorkspaceScriptResponse(
            message="ok",
            output="done",
            workspace_files=[_make_file_item()],
        )
        result = json.loads(tool._dump_json(response))
        self.assertNotIn("checksum", result["workspace_files"][0])

    def test_output_field_still_present(self):
        tool = self._make_tool()
        response = ExecuteWorkspaceScriptResponse(
            message="ok",
            output="script output",
            workspace_files=[],
        )
        result = json.loads(tool._dump_json(response))
        self.assertEqual(result["output"], "script output")

    def test_path_and_size_present_in_workspace_files(self):
        tool = self._make_tool()
        response = ExecuteWorkspaceScriptResponse(
            message="ok",
            output="",
            workspace_files=[_make_file_item()],
        )
        result = json.loads(tool._dump_json(response))
        file_entry = result["workspace_files"][0]
        self.assertEqual(file_entry["path"], "src/foo.py")
        self.assertEqual(file_entry["size"], 42)


class TestBuildScriptWrapperForwardsResourceLimits(unittest.TestCase):
    """_build_script_wrapper must pass config-controlled limits to the guard builder."""

    def _runner_with_config(self, max_threads: int, max_open_files: int) -> WorkspaceScriptRunner:
        runner = MagicMock(spec=WorkspaceScriptRunner)
        runner.config = CodeExecutorConfig(max_threads=max_threads, max_open_files=max_open_files)
        runner._build_script_wrapper = WorkspaceScriptRunner._build_script_wrapper.__get__(runner)
        return runner

    def test_forwards_max_threads(self):
        runner = self._runner_with_config(max_threads=16, max_open_files=128)
        with patch(
            "codemie_tools.data_management.workspace.execute_workspace_script_tool.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("script.py", "/workspace")
            _, kwargs = mock_build.call_args
            self.assertEqual(kwargs["max_threads"], 16)

    def test_forwards_max_open_files(self):
        runner = self._runner_with_config(max_threads=16, max_open_files=128)
        with patch(
            "codemie_tools.data_management.workspace.execute_workspace_script_tool.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("script.py", "/workspace")
            _, kwargs = mock_build.call_args
            self.assertEqual(kwargs["max_open_files"], 128)

    def test_non_default_config_values_are_forwarded(self):
        runner = self._runner_with_config(max_threads=8, max_open_files=256)
        with patch(
            "codemie_tools.data_management.workspace.execute_workspace_script_tool.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("run.py", "/sandbox")
            _, kwargs = mock_build.call_args
            self.assertEqual(kwargs["max_threads"], 8)
            self.assertEqual(kwargs["max_open_files"], 256)


def _script_file() -> FileObject:
    return FileObject(
        name="script.py",
        path="script.py",
        mime_type="text/x-python",
        owner="u",
        content="print('x')",
    )


def _make_runner(
    *,
    sandbox_mode: SandboxMode = SandboxMode.SHARED,
    tool_calling_timeout: float | None = None,
) -> WorkspaceScriptRunner:
    """A runner with a chat-uploaded script and a deterministic executor config."""
    runner = WorkspaceScriptRunner(
        file_repository=MagicMock(name="file_repository"),
        user_id="u",
        input_files=[_script_file()],
        execution_mode=ExecutionMode.SANDBOX,
        conversation_id="conv",
        tool_calling_timeout=tool_calling_timeout,
    )
    runner.config = runner.config.model_copy(
        update={
            "sandbox_mode": sandbox_mode,
            "namespace": "ns-test",
            "creator_env": "codemie",
        }
    )
    return runner


class TestResolveToolCallingLimit(unittest.TestCase):
    """The runner's limit follows the clamp: off by default, never raising."""

    def test_none_means_tool_calling_is_off(self):
        runner = _make_runner(tool_calling_timeout=None)
        self.assertIsNone(runner._resolve_tool_calling_limit())

    def test_jobs_mode_returns_a_set_value_unchanged(self):
        runner = _make_runner(sandbox_mode=SandboxMode.JOBS, tool_calling_timeout=120.0)
        self.assertEqual(runner._resolve_tool_calling_limit(), 120.0)

    def test_non_positive_value_turns_tool_calling_off(self):
        runner = _make_runner(sandbox_mode=SandboxMode.JOBS, tool_calling_timeout=0.0)
        self.assertIsNone(runner._resolve_tool_calling_limit())

    def test_value_above_the_maximum_is_clamped(self):
        runner = _make_runner(sandbox_mode=SandboxMode.JOBS, tool_calling_timeout=1e12)
        self.assertEqual(runner._resolve_tool_calling_limit(), MAX_TOOL_CALLING_SECONDS)


class _PooledRun:
    """Outcome of one patched pooled `_execute_sandbox_script` call."""

    def __init__(self) -> None:
        self.session: MagicMock = MagicMock(name="session")
        self.session.is_safe.return_value = (True, [])
        self.session.run.return_value = MagicMock(exit_code=0, stdout="", stderr="", plots=[])
        self.sandbox_session: MagicMock | None = None
        self.execute_code: MagicMock | None = None
        self.build_guard: MagicMock | None = None


@contextmanager
def _pooled_run(runner: WorkspaceScriptRunner) -> Iterator[_PooledRun]:
    state = _PooledRun()

    @contextmanager
    def fake_sandbox_session(*args: object, **kwargs: object) -> Iterator[MagicMock]:
        yield state.session

    with (
        patch.object(WorkspaceScriptRunner, "_sandbox_session", side_effect=fake_sandbox_session) as sb,
        patch.object(WorkspaceScriptRunner, "_get_user_workdir", return_value="/home/codemie/u/conv"),
        patch.object(WorkspaceScriptRunner, "_upload_files_to_sandbox", return_value=None),
        patch.object(WorkspaceScriptRunner, "_collect_sandbox_changed_files", return_value=[]),
        patch.object(WorkspaceScriptRunner, "_export_files_from_execution", return_value=[]),
        patch.object(WorkspaceScriptRunner, "_format_execution_result", return_value="ok"),
        patch.object(
            WorkspaceScriptRunner,
            "_execute_code_sandbox",
            return_value=(MagicMock(exit_code=0, stdout="", stderr=""), 0.1),
        ) as execute_code,
        patch(f"{_MODULE}.build_guarded_workspace_script", return_value="WRAPPER") as build_guard,
    ):
        state.sandbox_session = sb
        state.execute_code = execute_code
        state.build_guard = build_guard
        yield state


class TestPooledRunWithoutToolCalling(unittest.TestCase):
    """With tool calling off the pooled call shape is unchanged."""

    def test_session_is_acquired_with_only_the_workdir(self):
        runner = _make_runner(tool_calling_timeout=None)
        with _pooled_run(runner) as run:
            runner._execute_sandbox_script("script.py")
        run.sandbox_session.assert_called_once_with("/home/codemie/u/conv")

    def test_execute_code_sandbox_gets_no_kwargs(self):
        runner = _make_runner(tool_calling_timeout=None)
        with _pooled_run(runner) as run:
            runner._execute_sandbox_script("script.py")
        run.execute_code.assert_called_once_with(run.session, "WRAPPER")

    def test_wrapper_is_built_without_an_exchange_dir(self):
        runner = _make_runner(tool_calling_timeout=None)
        with _pooled_run(runner) as run:
            runner._execute_sandbox_script("script.py")
        self.assertIsNone(run.build_guard.call_args.kwargs["exchange_dir"])


class TestSharedModeIgnoresToolCallingTimeout(unittest.TestCase):
    """The tool-calling bridge only runs in jobs mode; a shared-mode runner keeps the plain pooled call shape."""

    def test_shared_mode_runner_with_a_timeout_uses_the_plain_pooled_call_shape(self):
        runner = _make_runner(sandbox_mode=SandboxMode.SHARED, tool_calling_timeout=120.0)
        with _pooled_run(runner) as run:
            runner._execute_sandbox_script("script.py")

        run.sandbox_session.assert_called_once_with("/home/codemie/u/conv")
        run.execute_code.assert_called_once_with(run.session, "WRAPPER")
        self.assertIsNone(run.build_guard.call_args.kwargs["exchange_dir"])


class TestJobsModeToolCalling(unittest.TestCase):
    def _run_jobs(self, tool_calling_timeout: float | None) -> tuple[MagicMock, MagicMock]:
        runner = _make_runner(sandbox_mode=SandboxMode.JOBS, tool_calling_timeout=tool_calling_timeout)
        fake_result = MagicMock(stdout="ok\n", stderr="", exit_code=0, exported_files={}, changed_files={})
        with (
            patch(f"{_MODULE}.BatchJobRunner") as job_runner,
            patch(f"{_MODULE}.build_guarded_workspace_script", return_value="WRAPPER") as build_guard,
            patch.object(WorkspaceScriptRunner, "_get_user_workdir", return_value="/home/codemie/u/conv"),
            patch.object(WorkspaceScriptRunner, "_validate_code_security_policy", return_value=None),
            patch.object(WorkspaceScriptRunner, "_store_exported_bytes", return_value=[]),
            patch.object(WorkspaceScriptRunner, "_format_execution_result", return_value="ok"),
        ):
            job_runner.return_value.run.return_value = fake_result
            runner._execute_sandbox_script("script.py")
        return job_runner, build_guard

    def test_enabled_passes_the_bridge_options_to_the_job_runner(self):
        job_runner, build_guard = self._run_jobs(120.0)

        exchange_dir = build_guard.call_args.kwargs["exchange_dir"]
        self.assertTrue(exchange_dir.startswith(f"{BRIDGE_DIR_NAME}/"))
        bridge = job_runner.return_value.run.call_args.kwargs["bridge"]
        self.assertEqual(bridge.exchange_dir, exchange_dir)
        self.assertEqual(bridge.tool_calling_timeout, 120.0)

    def test_disabled_passes_no_bridge_options(self):
        for value in (None, 0.0):
            with self.subTest(tool_calling_timeout=value):
                job_runner, build_guard = self._run_jobs(value)

                self.assertIsNone(build_guard.call_args.kwargs["exchange_dir"])
                self.assertIsNone(job_runner.return_value.run.call_args.kwargs["bridge"])


class _LocalSession:
    """Runs the snapshot script locally, so the pod-side code is exercised for real."""

    def run(self, code: str, timeout: float | None = None) -> SimpleNamespace:
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        return SimpleNamespace(exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


class TestSnapshotPrunesBridgeFolder(unittest.TestCase):
    def test_snapshot_skips_the_bridge_folder_but_keeps_workspace_files(self):
        runner = _make_runner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("hello", encoding="utf-8")
            leftover = root / BRIDGE_DIR_NAME / "x"
            leftover.mkdir(parents=True)
            (leftover / "req.1.json").write_text("{}", encoding="utf-8")

            snapshot = runner._get_sandbox_file_snapshot(_LocalSession(), str(root))

        self.assertIn("a.txt", snapshot)
        self.assertEqual([key for key in snapshot if key.startswith(BRIDGE_DIR_NAME)], [])

    def test_is_system_output_path_excludes_bridge_files_as_a_backstop(self):
        self.assertTrue(_is_system_output_path(f"{BRIDGE_DIR_NAME}/x/req.1.json"))
        self.assertFalse(_is_system_output_path("a.txt"))

    def test_leftover_bridge_file_never_reaches_the_export_service(self):
        runner = _make_runner()
        snapshot = {"a.txt": "h1", f"{BRIDGE_DIR_NAME}/x/req.1.json": "h2"}
        with (
            patch.object(WorkspaceScriptRunner, "_get_sandbox_file_snapshot", return_value=snapshot),
            patch(f"{_MODULE}.FileExportService") as export_service,
        ):
            export_service.return_value.collect_files_from_execution.return_value = []
            runner._collect_sandbox_changed_files(MagicMock(), "/home/codemie/u/conv", {})

        changed_paths = export_service.return_value.collect_files_from_execution.call_args.args[1]
        self.assertEqual(changed_paths, ["a.txt"])
