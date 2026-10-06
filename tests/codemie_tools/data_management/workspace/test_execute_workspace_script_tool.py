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

from collections.abc import Mapping
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
from codemie.rest_api.security.user import User
from codemie.service.script_tool_calls.context import ScriptScopeKind, ScriptToolRegistry
from codemie_tools.base.file_object import FileObject
from codemie_tools.data_management.code_executor.models import (
    CodeExecutorConfig,
    ExecutionMode,
    SandboxMode,
)
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import BRIDGE_DIR_NAME
from codemie_tools.data_management.code_executor.job_bridge import JobBridgeOptions, new_job_bridge_options
from codemie_tools.data_management.code_executor.tool_call_protocol import ToolCallHandler
from codemie_tools.data_management.code_executor.tool_calling_limits import ToolCallingSettings
from codemie_tools.data_management.workspace.execute_workspace_script_tool import ExecuteWorkspaceScriptTool
from codemie_tools.data_management.workspace.workspace_script_runner import (
    WorkspaceScriptRunner,
    _is_system_output_path,
)

_MODULE = "codemie_tools.data_management.workspace.workspace_script_runner"


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
            "codemie_tools.data_management.workspace.workspace_script_runner.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("script.py", "/workspace")
            _, kwargs = mock_build.call_args
            self.assertEqual(kwargs["max_threads"], 16)

    def test_forwards_max_open_files(self):
        runner = self._runner_with_config(max_threads=16, max_open_files=128)
        with patch(
            "codemie_tools.data_management.workspace.workspace_script_runner.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("script.py", "/workspace")
            _, kwargs = mock_build.call_args
            self.assertEqual(kwargs["max_open_files"], 128)

    def test_forwards_the_sdk_config_when_given(self):
        runner = self._runner_with_config(max_threads=16, max_open_files=128)
        sdk_config = {"exchange_dir": ".codemie_bridge/1-a", "run_seconds": 180.0, "max_payload_bytes": 1000}
        with patch(
            "codemie_tools.data_management.workspace.workspace_script_runner.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("script.py", "/workspace", sdk_config=sdk_config)
            _, kwargs = mock_build.call_args
            self.assertEqual(kwargs["sdk_config"], sdk_config)

    def test_sdk_config_defaults_to_none(self):
        runner = self._runner_with_config(max_threads=16, max_open_files=128)
        with patch(
            "codemie_tools.data_management.workspace.workspace_script_runner.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("script.py", "/workspace")
            _, kwargs = mock_build.call_args
            self.assertIsNone(kwargs["sdk_config"])

    def test_non_default_config_values_are_forwarded(self):
        runner = self._runner_with_config(max_threads=8, max_open_files=256)
        with patch(
            "codemie_tools.data_management.workspace.workspace_script_runner.build_guarded_workspace_script"
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
    bridge: JobBridgeOptions | None = None,
) -> WorkspaceScriptRunner:
    """A runner with a chat-uploaded script and a deterministic executor config."""
    runner = WorkspaceScriptRunner(
        file_repository=MagicMock(name="file_repository"),
        user_id="u",
        input_files=[_script_file()],
        execution_mode=ExecutionMode.SANDBOX,
        conversation_id="conv",
        bridge=bridge,
    )
    runner.config = runner.config.model_copy(
        update={
            "sandbox_mode": sandbox_mode,
            "namespace": "ns-test",
            "creator_env": "codemie",
        }
    )
    return runner


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
    """The pooled call shape: no bridge, no exchange folder, no SDK configuration."""

    def test_session_is_acquired_with_only_the_workdir(self):
        runner = _make_runner()
        with _pooled_run(runner) as run:
            runner._execute_sandbox_script("script.py")
        run.sandbox_session.assert_called_once_with("/home/codemie/u/conv")

    def test_execute_code_sandbox_gets_no_kwargs(self):
        runner = _make_runner()
        with _pooled_run(runner) as run:
            runner._execute_sandbox_script("script.py")
        run.execute_code.assert_called_once_with(run.session, "WRAPPER")

    def test_wrapper_is_built_without_an_sdk_config(self):
        runner = _make_runner()
        with _pooled_run(runner) as run:
            runner._execute_sandbox_script("script.py")
        self.assertIsNone(run.build_guard.call_args.kwargs["sdk_config"])


class TestSharedModeHasNoBridge(unittest.TestCase):
    """The bridge exists only in jobs mode: a pooled run has none and the runner does not know the bridge folder."""

    def test_a_shared_mode_runner_uses_the_plain_pooled_call_shape(self):
        runner = _make_runner(sandbox_mode=SandboxMode.SHARED)
        with _pooled_run(runner) as run:
            runner._execute_sandbox_script("script.py")

        run.sandbox_session.assert_called_once_with("/home/codemie/u/conv")
        run.execute_code.assert_called_once_with(run.session, "WRAPPER")
        self.assertIsNone(run.build_guard.call_args.kwargs["sdk_config"])

    def test_the_pooled_snapshot_code_does_not_name_the_bridge_folder(self):
        runner = _make_runner()
        session = MagicMock()
        session.run.return_value = MagicMock(exit_code=0, stdout="__CODEMIE_FILE_SNAPSHOT__{}\n")

        runner._get_sandbox_file_snapshot(session, "/home/codemie/u/conv")

        self.assertNotIn(BRIDGE_DIR_NAME, session.run.call_args.args[0])

    def test_the_runner_has_no_leftover_of_the_removed_tool_calling_fields(self):
        runner = _make_runner()

        self.assertFalse(hasattr(runner, "tool_calling_timeout"))
        self.assertFalse(hasattr(runner, "tool_call_handlers"))
        self.assertIsNone(runner.bridge)


class TestJobsModeToolCalling(unittest.TestCase):
    def _run_jobs(
        self,
        settings: ToolCallingSettings | None,
        handlers: Mapping[str, ToolCallHandler] | None = None,
    ) -> tuple[MagicMock, MagicMock]:
        runner = _make_runner(
            sandbox_mode=SandboxMode.JOBS,
            bridge=None if settings is None else new_job_bridge_options(settings, handlers),
        )
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
        settings = ToolCallingSettings(run_timeout_seconds=120.0)

        job_runner, build_guard = self._run_jobs(settings)

        bridge = job_runner.return_value.run.call_args.kwargs["bridge"]
        sdk_config = build_guard.call_args.kwargs["sdk_config"]
        self.assertTrue(bridge.exchange_dir.startswith(f"{BRIDGE_DIR_NAME}/"))
        self.assertEqual(sdk_config["exchange_dir"], bridge.exchange_dir)
        self.assertIs(bridge.settings, settings)

    def test_the_sdk_config_is_built_by_the_settings(self):
        settings = ToolCallingSettings(run_timeout_seconds=120.0, max_payload_bytes=1000)

        job_runner, build_guard = self._run_jobs(settings)

        bridge = job_runner.return_value.run.call_args.kwargs["bridge"]
        self.assertEqual(build_guard.call_args.kwargs["sdk_config"], settings.sdk_config(bridge.exchange_dir, 30.0))

    def test_enabled_passes_the_larger_of_the_script_timeout_and_the_limit_as_the_run_seconds(self):
        for limit, expected in ((120.0, 120.0), (10.0, 30.0)):
            with self.subTest(limit=limit):
                _, build_guard = self._run_jobs(ToolCallingSettings(run_timeout_seconds=limit))

                self.assertEqual(build_guard.call_args.kwargs["sdk_config"]["run_seconds"], expected)

    def test_disabled_passes_no_sdk_config_and_no_bridge_options(self):
        job_runner, build_guard = self._run_jobs(None)

        self.assertIsNone(build_guard.call_args.kwargs["sdk_config"])
        self.assertIsNone(job_runner.return_value.run.call_args.kwargs["bridge"])

    def test_enabled_without_handlers_passes_no_handlers_in_the_bridge_options(self):
        job_runner, _ = self._run_jobs(ToolCallingSettings())

        self.assertIsNone(job_runner.return_value.run.call_args.kwargs["bridge"].handlers)

    def test_enabled_passes_the_runner_tool_call_handlers_in_the_bridge_options(self):
        handlers: Mapping[str, ToolCallHandler] = {"tool.call": lambda _params: {"ok": True}}

        job_runner, _ = self._run_jobs(ToolCallingSettings(), handlers)

        self.assertIs(job_runner.return_value.run.call_args.kwargs["bridge"].handlers, handlers)


class _LocalSession:
    """Runs the snapshot script locally, so the pod-side code is exercised for real."""

    def run(self, code: str, timeout: float | None = None) -> SimpleNamespace:
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        return SimpleNamespace(exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


class TestPooledSnapshot(unittest.TestCase):
    def test_snapshot_lists_workspace_files_and_skips_caches(self):
        runner = _make_runner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("hello", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "m.pyc").write_bytes(b"x")

            snapshot = runner._get_sandbox_file_snapshot(_LocalSession(), str(root))

        self.assertEqual(sorted(snapshot), ["a.txt"])

    def test_system_output_paths_are_caches_and_sandbox_scripts_not_the_bridge_folder(self):
        self.assertTrue(_is_system_output_path("m.pyc"))
        self.assertFalse(_is_system_output_path("a.txt"))
        self.assertFalse(_is_system_output_path(f"{BRIDGE_DIR_NAME}/x/req.1.json"))


class TestExecuteToolRunContext(unittest.TestCase):
    def _tool(self, script_registry: ScriptToolRegistry | None) -> tuple[ExecuteWorkspaceScriptTool, MagicMock]:
        service = MagicMock()
        service.execute_workspace_script.return_value = {"message": "ok"}
        tool = ExecuteWorkspaceScriptTool(
            conversation_id="conv-1",
            user=User(id="user-1", auth_token=None),
            workspace_service=service,
            workspace_id="ws-1",
            script_registry=script_registry,
        )
        return tool, service

    def test_passes_the_registry_context_to_the_service(self):
        registry = ScriptToolRegistry(User(id="user-1", auth_token=None))
        fake = MagicMock()
        fake.name = "some_tool"
        registry.fill([fake])
        tool, service = self._tool(registry)

        tool.execute("run.py")

        run_context = service.execute_workspace_script.call_args.kwargs["run_context"]
        self.assertIs(run_context.scope_kind, ScriptScopeKind.ASSISTANT)
        self.assertEqual({t.name for t in run_context.scope.callable_tools()}, {"some_tool"})

    def test_without_a_registry_passes_a_context_that_refuses_every_call(self):
        tool, service = self._tool(None)

        tool.execute("run.py")

        run_context = service.execute_workspace_script.call_args.kwargs["run_context"]
        self.assertIs(run_context.scope_kind, ScriptScopeKind.NONE)
        self.assertEqual(run_context.user.id, "user-1")
        self.assertEqual(run_context.scope.callable_tools(), ())

    def test_passes_the_settings_the_tool_was_built_with_to_the_service(self):
        settings = ToolCallingSettings(run_timeout_seconds=90.0)
        tool, service = self._tool(None)
        tool.tool_calling = settings

        tool.execute("run.py")

        self.assertIs(service.execute_workspace_script.call_args.kwargs["tool_calling"], settings)

    def test_a_tool_built_with_tool_calling_off_passes_none(self):
        tool, service = self._tool(None)

        tool.execute("run.py")

        self.assertIsNone(service.execute_workspace_script.call_args.kwargs["tool_calling"])
