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

"""The inner-calls savings estimate is emitted when a workspace script run ends, whatever its ending."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from codemie.service import agent_workspace_service as service_module
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.script_tool_calls.context import ScriptRunContext, ScriptScopeKind, ToolListScope
from codemie.service.script_tool_calls.observer import ScriptRunObserver
from codemie_tools.data_management.code_executor.tool_calling_limits import ToolCallingSettings

SCRIPT = "scripts/run.py"


@pytest.fixture
def runner_class() -> Iterator[MagicMock]:
    with patch.object(service_module, "WorkspaceScriptRunner") as runner_cls:
        runner_cls.return_value.execute_script.return_value = "ok"
        runner_cls.return_value.last_execution_files = []
        yield runner_cls


@pytest.fixture
def service() -> AgentWorkspaceService:
    svc = AgentWorkspaceService.__new__(AgentWorkspaceService)
    svc.repository = MagicMock()
    svc.file_repository = MagicMock()
    workspace = MagicMock()
    workspace.id = "ws-1"
    workspace.conversation_id = "conv-1"
    svc.get_workspace = MagicMock(return_value=workspace)  # type: ignore[method-assign]
    svc.get_workspace_input_files = MagicMock(return_value=[])  # type: ignore[method-assign]
    svc._sync_execution_files = MagicMock(return_value=[])  # type: ignore[method-assign]
    return svc


def _user() -> MagicMock:
    user = MagicMock(id="u1")
    user.name = "User"
    return user


def _context() -> ScriptRunContext:
    return ScriptRunContext(
        user=_user(),
        scope=ToolListScope([]),
        scope_kind=ScriptScopeKind.ASSISTANT,
        assistant_name="asst",
        llm_model="gpt",
        project="proj",
        user_id="u1",
    )


def _run(service: AgentWorkspaceService, script: str = SCRIPT, **kwargs: object) -> None:
    service.execute_workspace_script(
        "ws-1",
        script,
        MagicMock(id="u1"),
        tool_calling=ToolCallingSettings(),
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.fixture
def estimate() -> Iterator[MagicMock]:
    """The sender of every metric of the run: the estimate is written by the script tool, never by the service."""
    from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService

    with patch.object(BaseMonitoringService, "send_count_metric") as sender:
        yield sender


def test_the_service_does_not_write_the_savings_estimate(
    service: AgentWorkspaceService, runner_class: MagicMock, estimate: MagicMock
) -> None:
    """Caught whatever the service calls: any record of the estimate metric during a run is a violation."""
    _run(service, run_context=_context(), observer=ScriptRunObserver())

    assert [c for c in estimate.call_args_list if c.kwargs.get("name") == "codemie_script_tokens_estimated"] == []


def test_the_response_cap_is_the_assistants_tokens_limit(
    service: AgentWorkspaceService, runner_class: MagicMock, estimate: MagicMock
) -> None:
    observer = ScriptRunObserver()
    context = dataclasses.replace(_context(), tools_tokens_size_limit=100)

    _run(service, run_context=context, observer=observer)

    assert observer._response_cap_tokens == 100


def test_the_response_cap_falls_back_to_the_payload_bound_in_tokens(
    service: AgentWorkspaceService, runner_class: MagicMock, estimate: MagicMock
) -> None:
    observer = ScriptRunObserver()

    _run(service, run_context=_context(), observer=observer)

    assert observer._response_cap_tokens == ToolCallingSettings().max_payload_bytes // 4


@pytest.fixture
def run_metrics() -> Iterator[MagicMock]:
    """The run reaches the metrics through the sandbox-run sinks, as in the application."""
    from codemie.service.monitoring.code_execution_monitoring_service import CodeExecutionMonitoringService
    from codemie_tools.base.execution_timing import sandbox_run_sinks

    sandbox_run_sinks.clear()
    CodeExecutionMonitoringService.register()
    try:
        with patch.object(CodeExecutionMonitoringService, "send_run") as send_run:
            yield send_run
    finally:
        sandbox_run_sinks.clear()


def test_the_capacity_facts_of_the_run_are_reported_as_metrics(
    service: AgentWorkspaceService, runner_class: MagicMock, estimate: MagicMock, run_metrics: MagicMock
) -> None:
    runner_class.return_value.config.sandbox_mode.value = "sandbox-jobs"

    _run(service, run_context=_context())

    sandbox_run = runner_class.call_args.kwargs["sandbox_run"]
    run_metrics.assert_called_once()
    kwargs = run_metrics.call_args.kwargs
    assert kwargs["sandbox_run"] is sandbox_run
    assert kwargs["tool_name"] == "execute_workspace_script"
    assert kwargs["sandbox_mode"] == "sandbox-jobs"
    assert kwargs["dimensions"]["project"] == "proj"
    assert kwargs["dimensions"]["assistant_name"] == "asst"


def test_the_capacity_facts_are_reported_when_the_run_fails(
    service: AgentWorkspaceService, runner_class: MagicMock, estimate: MagicMock, run_metrics: MagicMock
) -> None:
    runner_class.return_value.config.sandbox_mode.value = "sandbox-jobs"
    runner_class.return_value.execute_script.side_effect = RuntimeError("at capacity")

    with pytest.raises(RuntimeError):
        _run(service, run_context=_context())

    run_metrics.assert_called_once()


def test_a_run_whose_runner_could_not_be_built_is_still_reported_with_an_unknown_mode(
    service: AgentWorkspaceService, runner_class: MagicMock, estimate: MagicMock, run_metrics: MagicMock
) -> None:
    runner_class.side_effect = RuntimeError("no config")

    with pytest.raises(RuntimeError):
        _run(service, run_context=_context())

    run_metrics.assert_called_once()
    assert run_metrics.call_args.kwargs["sandbox_mode"] == "unknown"
    assert run_metrics.call_args.kwargs["sandbox_run"].outcome.value == "error"


def test_the_run_is_reported_with_its_duration_and_what_the_script_did_through_the_bridge(
    service: AgentWorkspaceService, runner_class: MagicMock, estimate: MagicMock, run_metrics: MagicMock
) -> None:
    runner_class.return_value.config.sandbox_mode.value = "sandbox-jobs"
    observer = ScriptRunObserver()

    _run(service, run_context=_context(), observer=observer)

    kwargs = run_metrics.call_args.kwargs
    assert kwargs["elapsed_seconds"] >= 0
    assert kwargs["calls"] == observer.close()


def test_a_run_that_fails_says_how_it_ended(
    service: AgentWorkspaceService, runner_class: MagicMock, estimate: MagicMock, run_metrics: MagicMock
) -> None:
    from codemie_tools.data_management.code_executor.run_observer import RunOutcome, SandboxFailure

    runner_class.return_value.config.sandbox_mode.value = "sandbox-jobs"
    runner_class.return_value.execute_script.side_effect = SandboxFailure("pod never ran", RunOutcome.TIMEOUT)

    with pytest.raises(SandboxFailure):
        _run(service, run_context=_context())

    assert run_metrics.call_args.kwargs["sandbox_run"].outcome is RunOutcome.TIMEOUT


def test_a_cancelled_run_is_reported_as_an_error_too(
    service: AgentWorkspaceService, runner_class: MagicMock, estimate: MagicMock, run_metrics: MagicMock
) -> None:
    """A cancellation is a BaseException; the run's outcome must still say it did not finish."""
    runner_class.return_value.execute_script.side_effect = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        _run(service, run_context=_context())

    assert run_metrics.call_args.kwargs["sandbox_run"].outcome.value == "error"
