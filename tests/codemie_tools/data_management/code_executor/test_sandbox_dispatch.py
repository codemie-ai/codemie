# Copyright 2026 EPAM Systems, Inc. ("EPAM")
# Licensed under the Apache License, Version 2.0
"""Dispatch tests for CodeExecutorTool._sandbox_session."""

import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool
from codemie_tools.data_management.code_executor.models import (
    ExecutionMode,
    SandboxMode,
)


def _make_tool(sandbox_mode: SandboxMode) -> CodeExecutorTool:
    file_repo = MagicMock(name="file_repository")
    tool = CodeExecutorTool(
        file_repository=file_repo,
        execution_mode=ExecutionMode.SANDBOX,
    )
    tool.config = tool.config.model_copy(update={"sandbox_mode": sandbox_mode})
    return tool


class TestSandboxSessionDispatch(unittest.TestCase):
    def test_shared_mode_routes_through_session_manager(self):
        tool = _make_tool(SandboxMode.SHARED)
        fake_session = MagicMock(name="session")

        with patch.object(tool, "_acquire_session", return_value=(fake_session, 0.1)) as acquire:
            with tool._sandbox_session("/home/codemie/u") as session:
                assert session is fake_session

            acquire.assert_called_once_with("/home/codemie/u")


class TestExecuteSandboxIntegration(unittest.TestCase):
    """Verify _execute_sandbox uses the _sandbox_session context manager in SHARED mode."""

    def test_execute_sandbox_uses_session_helper_in_shared_mode(self):
        tool = _make_tool(SandboxMode.SHARED)
        fake_session = MagicMock(name="session")
        fake_session.is_safe.return_value = (True, [])
        fake_result = MagicMock(exit_code=0, stdout="hello\n", stderr="", plots=[])
        fake_session.run.return_value = fake_result

        @contextmanager
        def fake_sandbox_session(workdir):
            yield fake_session

        with (
            patch.object(tool, "_sandbox_session", side_effect=fake_sandbox_session) as sb,
            patch.object(tool, "_get_user_workdir", return_value="/home/codemie/u"),
            patch.object(tool, "_export_files_from_execution", return_value=[]),
            patch.object(tool, "_format_execution_result", return_value="hello"),
        ):
            out = tool.execute("print('hello')")

        sb.assert_called_once_with("/home/codemie/u")
        assert "hello" in out


class TestExecuteSandboxJobsMode(unittest.TestCase):
    """Verify _execute_sandbox bypasses _sandbox_session in JOBS mode."""

    def test_jobs_mode_calls_batch_job_runner(self):
        tool = _make_tool(SandboxMode.JOBS)
        fake_result = MagicMock(stdout="hi\n", stderr="", exit_code=0, exported_files={})

        with (
            patch("codemie_tools.data_management.code_executor.batch_job_runner.BatchJobRunner") as runner_cls,
            patch.object(tool, "_format_execution_result", return_value="hi") as fmt,
            patch.object(tool, "_sandbox_session") as sb,
        ):
            runner_cls.return_value.run.return_value = fake_result
            out = tool.execute("print('hi')")

        runner_cls.assert_called_once_with(tool.config)
        runner_cls.return_value.run.assert_called_once_with(
            "print('hi')",
            input_files={},
            export_files=None,
            workdir=tool._get_user_workdir(),
        )
        fmt.assert_called_once_with(fake_result)
        sb.assert_not_called()
        assert out == "hi"

    def test_jobs_mode_passes_input_file_bytes_to_runner(self):
        tool = _make_tool(SandboxMode.JOBS)
        fake_file = MagicMock(name="data.csv", owner="u", mime_type="text/csv")
        fake_file.name = "data.csv"
        tool.input_files = [fake_file]
        fake_result = MagicMock(stdout="", stderr="", exit_code=0, exported_files={})

        with (
            patch("codemie_tools.data_management.code_executor.batch_job_runner.BatchJobRunner") as runner_cls,
            patch.object(tool, "_format_execution_result", return_value=""),
            patch.object(tool, "_read_input_file_bytes", return_value={"data.csv": b"a,b\n1,2\n"}) as reader,
        ):
            runner_cls.return_value.run.return_value = fake_result
            tool.execute("print('hi')")

        reader.assert_called_once_with([fake_file])
        runner_cls.return_value.run.assert_called_once_with(
            "print('hi')",
            input_files={"data.csv": b"a,b\n1,2\n"},
            export_files=None,
            workdir=tool._get_user_workdir(),
        )

    def test_jobs_mode_stores_exported_files_via_export_service(self):
        tool = _make_tool(SandboxMode.JOBS)
        fake_result = MagicMock(
            stdout="",
            stderr="",
            exit_code=0,
            exported_files={"out.txt": b"contents"},
        )

        with (
            patch("codemie_tools.data_management.code_executor.batch_job_runner.BatchJobRunner") as runner_cls,
            patch.object(tool, "_format_execution_result", return_value="ok"),
            patch.object(
                tool,
                "_store_exported_bytes",
                return_value=[" File 'out.txt', URL `sandbox:/v1/files/abc`"],
            ) as store,
        ):
            runner_cls.return_value.run.return_value = fake_result
            out = tool.execute("print('hi')", export_files=["out.txt"])

        runner_cls.return_value.run.assert_called_once_with(
            "print('hi')",
            input_files={},
            export_files=["out.txt"],
            workdir=tool._get_user_workdir(),
        )
        store.assert_called_once_with({"out.txt": b"contents"})
        assert "sandbox:/v1/files/abc" in out


class TestWorkspaceScriptRunnerJobsMode(unittest.TestCase):
    """Workspace script runner routes to BatchJobRunner under sandbox-jobs mode."""

    def test_workspace_script_jobs_mode_calls_batch_job_runner(self):
        from codemie_tools.data_management.code_executor.models import CodeExecutorConfig
        from codemie_tools.data_management.workspace.execute_workspace_script_tool import (
            WorkspaceScriptRunner,
        )

        config = CodeExecutorConfig(
            execution_mode=ExecutionMode.SANDBOX,
            sandbox_mode=SandboxMode.JOBS,
        )
        runner = WorkspaceScriptRunner.__new__(WorkspaceScriptRunner)
        object.__setattr__(runner, "__pydantic_fields_set__", set())
        object.__setattr__(runner, "__pydantic_extra__", None)
        object.__setattr__(runner, "__pydantic_private__", {"_custom_pod_manifest": None})
        object.__setattr__(runner, "config", config)
        object.__setattr__(runner, "input_files", [])
        object.__setattr__(runner, "security_policy", MagicMock())
        object.__setattr__(runner, "last_execution_files", [])
        object.__setattr__(runner, "file_repository", MagicMock())
        object.__setattr__(runner, "user_id", "u")
        object.__setattr__(runner, "conversation_id", "conv")

        fake_result = MagicMock(stdout="ok\n", stderr="", exit_code=0, exported_files={}, changed_files={})

        with (
            patch("codemie_tools.data_management.workspace.execute_workspace_script_tool.BatchJobRunner") as runner_cls,
            patch.object(WorkspaceScriptRunner, "_get_user_workdir", return_value="/home/codemie/conv"),
            patch.object(WorkspaceScriptRunner, "_get_script_content", return_value="print('x')"),
            patch.object(WorkspaceScriptRunner, "_validate_code_security_policy", return_value=None),
            patch.object(WorkspaceScriptRunner, "_build_script_wrapper", return_value="print('x')"),
            patch.object(WorkspaceScriptRunner, "_get_input_file_hashes", return_value={}),
            patch.object(WorkspaceScriptRunner, "_read_input_file_bytes", return_value={}),
            patch.object(WorkspaceScriptRunner, "_format_execution_result", return_value="ok"),
            patch.object(WorkspaceScriptRunner, "_store_exported_bytes", return_value=[]),
            patch.object(WorkspaceScriptRunner, "_log_execution_timing", return_value=None),
            patch.object(WorkspaceScriptRunner, "_sandbox_session") as sb,
        ):
            runner_cls.return_value.run.return_value = fake_result
            out = runner._execute_sandbox_script("script.py")

        runner_cls.assert_called_once_with(config)
        runner_cls.return_value.run.assert_called_once_with(
            "print('x')",
            input_files={},
            export_files=None,
            workdir="/home/codemie/conv",
            baseline_hashes={},
        )
        sb.assert_not_called()
        assert out == "ok"

    def test_workspace_script_shared_mode_still_uses_sandbox_session(self):
        from codemie_tools.data_management.code_executor.models import CodeExecutorConfig
        from codemie_tools.data_management.workspace.execute_workspace_script_tool import (
            WorkspaceScriptRunner,
        )

        config = CodeExecutorConfig(
            execution_mode=ExecutionMode.SANDBOX,
            sandbox_mode=SandboxMode.SHARED,
        )
        runner = WorkspaceScriptRunner.__new__(WorkspaceScriptRunner)
        object.__setattr__(runner, "__pydantic_fields_set__", set())
        object.__setattr__(runner, "__pydantic_extra__", None)
        object.__setattr__(runner, "__pydantic_private__", {"_custom_pod_manifest": None})
        object.__setattr__(runner, "config", config)
        object.__setattr__(runner, "input_files", [])
        object.__setattr__(runner, "security_policy", MagicMock())
        object.__setattr__(runner, "last_execution_files", [])

        fake_session = MagicMock(name="session")
        fake_session.is_safe.return_value = (True, [])
        fake_session.run.return_value = MagicMock(exit_code=0, stdout="", stderr="", plots=[])

        @contextmanager
        def fake_sandbox_session(workdir):
            yield fake_session

        with (
            patch.object(WorkspaceScriptRunner, "_sandbox_session", side_effect=fake_sandbox_session) as sb,
            patch.object(WorkspaceScriptRunner, "_get_user_workdir", return_value="/home/codemie/conv"),
            patch.object(WorkspaceScriptRunner, "_get_input_file_hashes", return_value={}),
            patch.object(WorkspaceScriptRunner, "_get_script_content", return_value="print('x')"),
            patch.object(WorkspaceScriptRunner, "_build_script_wrapper", return_value="print('x')"),
            patch.object(WorkspaceScriptRunner, "_collect_sandbox_changed_files", return_value=[]),
            patch.object(WorkspaceScriptRunner, "_export_files_from_execution", return_value=[]),
            patch.object(WorkspaceScriptRunner, "_format_execution_result", return_value="ok"),
        ):
            out = runner._execute_sandbox_script("script.py")

        sb.assert_called_once_with("/home/codemie/conv")
        assert out == "ok"
