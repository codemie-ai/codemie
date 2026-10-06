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

"""The workspace service builds the script runner's bridge from the settings the caller (or the resolver) gives."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from unittest.mock import MagicMock, patch

import pytest

from codemie.service import agent_workspace_service as service_module
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.script_tool_calls.context import ScriptRunContext, ScriptScopeKind, ToolListScope
from codemie_tools.data_management.code_executor.job_bridge import JobBridgeOptions
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import TOOL_CALL_OP
from codemie_tools.data_management.code_executor.tool_call_protocol import ToolCallHandler, ToolCallRefused
from codemie_tools.data_management.code_executor.tool_calling_limits import ToolCallingSettings


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


def _bridge_passed_to_runner(runner_class: MagicMock) -> JobBridgeOptions | None:
    runner_class.assert_called_once()
    bridge: JobBridgeOptions | None = runner_class.call_args.kwargs["bridge"]
    return bridge


def _handlers_of(runner_class: MagicMock) -> Mapping[str, ToolCallHandler]:
    bridge = _bridge_passed_to_runner(runner_class)
    assert bridge is not None and bridge.handlers is not None
    return bridge.handlers


def test_the_settings_the_caller_passes_are_used_and_the_resolver_is_not_asked(
    service: AgentWorkspaceService, runner_class: MagicMock
) -> None:
    settings = ToolCallingSettings(run_timeout_seconds=45.0, max_parallel_calls=2)

    with patch.object(service_module, "resolve_tool_calling_settings") as resolve:
        service.execute_workspace_script("ws-1", "run.py", MagicMock(id="user-1"), tool_calling=settings)

    resolve.assert_not_called()
    bridge = _bridge_passed_to_runner(runner_class)
    assert bridge is not None
    assert bridge.settings is settings
    assert bridge.exchange_dir.startswith(".codemie_bridge/")


def test_a_run_without_settings_resolves_them_itself_as_the_rest_path_does(
    service: AgentWorkspaceService, runner_class: MagicMock
) -> None:
    settings = ToolCallingSettings(run_timeout_seconds=60.0)

    with patch.object(service_module, "resolve_tool_calling_settings", return_value=settings) as resolve:
        service.execute_workspace_script("ws-1", "run.py", MagicMock(id="user-1"))

    resolve.assert_called_once_with()
    bridge = _bridge_passed_to_runner(runner_class)
    assert bridge is not None and bridge.settings is settings


def test_there_is_no_bridge_when_tool_calling_is_off(service: AgentWorkspaceService, runner_class: MagicMock) -> None:
    with patch.object(service_module, "resolve_tool_calling_settings", return_value=None):
        service.execute_workspace_script("ws-1", "run.py", MagicMock(id="user-1"))

    assert _bridge_passed_to_runner(runner_class) is None


def test_handlers_are_built_from_the_run_context(service: AgentWorkspaceService, runner_class: MagicMock) -> None:
    user = MagicMock(id="user-1")
    tool = MagicMock()
    tool.name = "some_tool"
    context = ScriptRunContext(user=user, scope=ToolListScope([tool]), scope_kind=ScriptScopeKind.ASSISTANT)

    service.execute_workspace_script("ws-1", "run.py", user, run_context=context, tool_calling=ToolCallingSettings())

    handlers = _handlers_of(runner_class)
    assert set(handlers) == {TOOL_CALL_OP}
    # The handler is bound to the given context: a tool outside its list is unavailable, not no_context.
    with pytest.raises(ToolCallRefused) as excinfo:
        handlers[TOOL_CALL_OP]({"name": "other_tool", "args": {}})
    assert excinfo.value.code == "tool_unavailable"


def test_no_run_context_answers_no_context(service: AgentWorkspaceService, runner_class: MagicMock) -> None:
    service.execute_workspace_script("ws-1", "run.py", MagicMock(id="user-1"), tool_calling=ToolCallingSettings())

    with pytest.raises(ToolCallRefused) as excinfo:
        _handlers_of(runner_class)[TOOL_CALL_OP]({"name": "some_tool", "args": {}})
    assert excinfo.value.code == "no_context"


def test_no_handlers_are_built_when_tool_calling_is_off(
    service: AgentWorkspaceService, runner_class: MagicMock
) -> None:
    user = MagicMock(id="user-1")

    with patch.object(service_module, "resolve_tool_calling_settings", return_value=None):
        with patch.object(service_module, "build_tool_call_handlers") as build:
            service.execute_workspace_script("ws-1", "run.py", user, run_context=ScriptRunContext.without_context(user))

    build.assert_not_called()


def test_the_payload_cap_of_the_settings_reaches_the_handlers(
    service: AgentWorkspaceService, runner_class: MagicMock
) -> None:
    user = MagicMock(id="user-1")
    settings = ToolCallingSettings(max_payload_bytes=1234)

    with patch.object(service_module, "build_tool_call_handlers", return_value={}) as build:
        service.execute_workspace_script("ws-1", "run.py", user, tool_calling=settings)

    assert build.call_args.kwargs["max_payload_bytes"] == 1234
