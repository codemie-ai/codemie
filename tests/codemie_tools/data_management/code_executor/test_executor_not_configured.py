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


from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import ToolException

from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool
from codemie_tools.data_management.code_executor.run_observer import RunOutcome, SandboxFailure
from codemie_tools.data_management.workspace.workspace_script_runner import WorkspaceScriptRunner

MOD = "codemie_tools.data_management.code_executor.code_executor_tool"


@pytest.fixture
def standalone_unconfigured(monkeypatch):
    monkeypatch.setenv("STANDALONE", "true")
    monkeypatch.delenv("CODE_EXECUTOR_ENABLED", raising=False)
    monkeypatch.delenv("CODE_EXECUTOR_KUBECONFIG_PATH", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)


def test_code_executor_not_configured_never_touches_cluster(standalone_unconfigured):
    tool = CodeExecutorTool(file_repository=MagicMock(), user_id="u")
    with patch(f"{MOD}.run_via_jobs") as jobs, patch(f"{MOD}.SandboxSessionManager") as mgr:
        with pytest.raises(ToolException, match="not configured"):
            tool.execute("print(1)")
    jobs.assert_not_called()
    mgr.assert_not_called()


def test_script_runner_not_configured(standalone_unconfigured):
    runner = WorkspaceScriptRunner(file_repository=MagicMock(), user_id="u", conversation_id="c")
    with patch("codemie_tools.data_management.workspace.workspace_script_runner.BatchJobRunner") as jobs:
        with pytest.raises(ToolException, match="not configured"):
            runner.execute_script("a.py")
    jobs.assert_not_called()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("STANDALONE", "true")
    monkeypatch.setenv("CODE_EXECUTOR_ENABLED", "true")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")


def _run_with(configured_tool_error):
    tool = CodeExecutorTool(file_repository=MagicMock(), user_id="u")
    with (
        patch.object(tool, "_get_user_workdir", return_value="/w"),
        patch.object(tool, "_validate_export_paths"),
        patch(f"{MOD}.build_guarded_python_script", return_value="g"),
        patch(f"{MOD}.run_via_jobs", side_effect=configured_tool_error),
        patch.object(tool.config, "sandbox_mode", MagicMock(value="jobs")),
    ):
        from codemie_tools.data_management.code_executor.models import SandboxMode

        tool.config.sandbox_mode = SandboxMode.JOBS
        with patch.object(tool, "_validate_code_security_policy"):
            with pytest.raises(ToolException) as exc:
                tool.execute("print(1)")
    return str(exc.value)


def test_three_distinct_messages(configured):
    sandbox = _run_with(SandboxFailure("Failed to create Job: boom", RunOutcome.SANDBOX_ERROR))
    conn = _run_with(ConnectionError("no route"))
    code = _run_with(ToolException("Code execution failed.\n\nSTDERR:\nZeroDivisionError"))
    assert "unavailable" in sandbox and "boom" in sandbox
    assert "unavailable" in conn and "no route" in conn
    assert "ZeroDivisionError" in code and "unavailable" not in code
    assert "not configured" not in sandbox + conn + code


def test_script_runner_flag_off_not_configured(configured):
    from codemie.configs.customer_config import CustomerConfig

    runner = WorkspaceScriptRunner(file_repository=MagicMock(), user_id="u", conversation_id="c")
    with (
        patch.object(CustomerConfig, "configured_feature_enabled", return_value=False),
        patch("codemie_tools.data_management.workspace.workspace_script_runner.BatchJobRunner") as jobs,
    ):
        with pytest.raises(ToolException, match="not configured"):
            runner.execute_script("a.py")
    jobs.assert_not_called()
