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

"""The workspace toolkit gives the script tool a registry, in workflow scope only for a bare workflow tool step."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from langchain_core.tools import BaseTool

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant, VirtualAssistant
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.script_tool_calls.authorizer import authorize_tool_call
from codemie.service.script_tool_calls.context import (
    ProjectScope,
    ScriptRunContext,
    ScriptScopeKind,
    ScriptToolRegistry,
    ToolListScope,
)
from codemie_tools.data_management.workspace.execute_workspace_script_tool import ExecuteWorkspaceScriptTool
from codemie_tools.data_management.workspace.toolkit import AgentWorkspaceToolkit


@pytest.fixture(autouse=True)
def workspace_service() -> Iterator[MagicMock]:
    with patch("codemie_tools.data_management.workspace.toolkit.AgentWorkspaceService") as service_cls:
        service = object.__new__(AgentWorkspaceService)
        service.get_workspace = MagicMock(return_value=MagicMock(id="workspace-1"))  # type: ignore[method-assign]
        service.create_workspace = MagicMock(return_value=MagicMock(id="workspace-1"))  # type: ignore[method-assign]
        service_cls.return_value = service
        yield service


def _assistant(project: str = "proj") -> Assistant:
    return Assistant(name="assistant", description="d", system_prompt="p", project=project)


def _virtual_assistant(
    execution_id: str | None, project: str = "wf-project", is_tool_step: bool = False
) -> VirtualAssistant:
    return VirtualAssistant(
        name="virtual",
        description="d",
        system_prompt="p",
        project=project,
        execution_id=execution_id,
        is_tool_step=is_tool_step,
    )


def _script_registry(assistant: Assistant | VirtualAssistant | None) -> ScriptToolRegistry:
    toolkit = AgentWorkspaceToolkit(
        conversation_id="conversation-1",
        user=User(id="user-1", auth_token=None),
        assistant=assistant,
        image_generator=MagicMock(),
    )
    script_tools = [tool for tool in toolkit.get_tools() if isinstance(tool, ExecuteWorkspaceScriptTool)]
    assert len(script_tools) == 1
    registry = script_tools[0].script_registry
    assert registry is not None
    return registry


def _filled_context(registry: ScriptToolRegistry) -> ScriptRunContext:
    registry.fill([])
    return registry.context()


def test_registry_is_unfilled_until_the_tool_list_is_final() -> None:
    registry = _script_registry(None)

    assert registry.context().scope_kind is ScriptScopeKind.NONE


@pytest.mark.parametrize("assistant", [None, _assistant()])
def test_no_assistant_or_regular_assistant_is_assistant_scope(assistant: Assistant | None) -> None:
    context = _filled_context(_script_registry(assistant))

    assert context.scope_kind is ScriptScopeKind.ASSISTANT
    assert isinstance(context.scope, ToolListScope)


def test_tool_step_virtual_assistant_is_workflow_scope_in_its_project() -> None:
    assistant = _virtual_assistant("exec-1", is_tool_step=True)

    context = _filled_context(_script_registry(assistant))

    assert context.scope_kind is ScriptScopeKind.WORKFLOW
    assert isinstance(context.scope, ProjectScope)
    assert context.scope.project == "wf-project"


@pytest.mark.parametrize("execution_id", [None, "", "exec-1"])
def test_virtual_assistant_that_is_not_a_tool_step_is_assistant_scope(execution_id: str | None) -> None:
    """An inline workflow assistant node carries the run's execution id but is not a bare tool step."""
    assistant = _virtual_assistant(execution_id)

    context = _filled_context(_script_registry(assistant))

    assert context.scope_kind is ScriptScopeKind.ASSISTANT
    assert isinstance(context.scope, ToolListScope)


def test_tool_step_marker_survives_the_assistant_union_of_the_toolkit() -> None:
    toolkit = AgentWorkspaceToolkit(
        conversation_id="conversation-1",
        user=User(id="user-1", auth_token=None),
        assistant=_virtual_assistant("exec-1", is_tool_step=True),
        image_generator=MagicMock(),
    )

    assert isinstance(toolkit.assistant, VirtualAssistant)
    assert toolkit.assistant.is_tool_step is True


def _bare_step_toolkit_registry(
    assistant: Assistant | VirtualAssistant, request: AssistantChatRequest | None = None
) -> ScriptToolRegistry:
    toolkit = AgentWorkspaceToolkit(
        conversation_id="conversation-1",
        user=User(id="user-1", auth_token=None),
        assistant=assistant,
        request=request,
        image_generator=MagicMock(),
    )
    script_tool = next(tool for tool in toolkit.get_tools() if isinstance(tool, ExecuteWorkspaceScriptTool))
    assert script_tool.script_registry is not None
    return script_tool.script_registry


def test_bare_workflow_step_gets_workflow_scope_without_toolkit_service_binding() -> None:
    """A bare workflow tool step never reaches ToolkitService.get_tools, so its registry is never filled."""
    registry = _bare_step_toolkit_registry(_virtual_assistant("exec-1", is_tool_step=True))

    context = registry.context()

    assert context.scope_kind is ScriptScopeKind.WORKFLOW
    assert isinstance(context.scope, ProjectScope)
    assert context.scope.project == "wf-project"
    assert context.scope.callable_tools() is None, "the whole catalog is resolved by name, never listed"


def test_bare_workflow_step_script_resolves_tools_by_name_in_the_workflow_project() -> None:
    registry = _bare_step_toolkit_registry(_virtual_assistant("exec-1", is_tool_step=True))
    resolved = MagicMock(spec=BaseTool)
    resolved.name = "some_platform_tool"
    resolved.script_callable = True  # a tool that opted in; the exclusion rules read the flag
    resolver = MagicMock(return_value=resolved)
    context = registry.context()
    assert isinstance(context.scope, ProjectScope)
    context.scope._resolver = resolver

    tool = authorize_tool_call(context, "some_platform_tool")

    assert tool is resolved
    resolver.assert_called_once()
    _, project, name = resolver.call_args.args
    assert (project, name) == ("wf-project", "some_platform_tool")


def test_request_supplied_workflow_execution_id_gains_no_workflow_scope() -> None:
    request = AssistantChatRequest(text="hi", workflow_execution_id="exec-spoofed")

    registry = _bare_step_toolkit_registry(_assistant(), request)

    assert registry.context().scope_kind is ScriptScopeKind.NONE
    registry.fill([])
    assert registry.context().scope_kind is ScriptScopeKind.ASSISTANT
