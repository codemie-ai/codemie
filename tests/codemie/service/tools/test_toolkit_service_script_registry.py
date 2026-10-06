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

"""ToolkitService.get_tools fills the script tool's registry with the run's final tool list."""

from __future__ import annotations

from collections.abc import Sequence
import inspect
from typing import ClassVar
from unittest.mock import MagicMock, Mock, patch

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.script_tool_calls.binding import bind_script_registries
from codemie.service.script_tool_calls.context import (
    ProjectScope,
    ScriptRunContext,
    ScriptScopeKind,
    ScriptToolRegistry,
    ToolListScope,
)
from codemie.service.tools.toolkit_service import ToolkitService
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.workspace.execute_workspace_script_tool import ExecuteWorkspaceScriptTool


class _Args(BaseModel):
    pass


class _NamedTool(CodeMieTool):
    script_callable = True
    name: str = "named_tool"
    description: str = "a tool"
    args_schema: type[BaseModel] = _Args

    def execute(self) -> str:
        return "ok"


class _NoScriptTool(_NamedTool):
    name: str = "no_script_tool"
    script_callable: ClassVar[bool] = False


class _StreamTool(_NamedTool):
    name: str = "request_user_input"
    thread_generator: object = None


def _tool(name: str) -> _NamedTool:
    return _NamedTool(name=name)


def _user() -> User:
    return User(id="user-1", auth_token=None)


def _script_tool(registry: ScriptToolRegistry | None) -> ExecuteWorkspaceScriptTool:
    return ExecuteWorkspaceScriptTool(
        conversation_id="conv-1",
        user=_user(),
        workspace_service=MagicMock(),
        workspace_id="ws-1",
        script_registry=registry,
    )


def _context(tool: ExecuteWorkspaceScriptTool) -> ScriptRunContext:
    assert tool.script_registry is not None
    return tool.script_registry.context()


def _names(context: ScriptRunContext) -> list[str]:
    """The names of the tools the context's scope offers."""
    tools = context.scope.callable_tools()
    assert tools is not None
    return [tool.name for tool in tools]


class TestBindScriptRegistries:
    def test_fills_registry_with_every_tool_except_excluded_ones(self) -> None:
        script = _script_tool(ScriptToolRegistry(_user()))
        stream = _StreamTool(thread_generator=MagicMock())
        tools: Sequence[BaseTool] = [_tool("a"), script, _tool("b"), stream]

        bind_script_registries(tools)

        context = _context(script)
        assert context.scope_kind is ScriptScopeKind.ASSISTANT
        assert set(_names(context)) == {"a", "b"}

    def test_de_duplicates_by_name(self) -> None:
        script = _script_tool(ScriptToolRegistry(_user()))
        first, second = _tool("dup"), _tool("dup")

        bind_script_registries([first, second, script])

        assert _names(_context(script)) == ["dup"]

    def test_registry_built_for_a_tool_step_keeps_workflow_scope_when_bound(self) -> None:
        script = _script_tool(ScriptToolRegistry(_user(), workflow_project="proj-1"))

        bind_script_registries([_tool("a"), script])

        context = _context(script)
        assert context.scope_kind is ScriptScopeKind.WORKFLOW
        assert isinstance(context.scope, ProjectScope)
        assert context.scope.project == "proj-1"

    def test_tools_flagged_not_script_callable_are_left_out(self) -> None:
        script = _script_tool(ScriptToolRegistry(_user()))

        bind_script_registries([_tool("a"), _NoScriptTool(), script])

        assert set(_names(_context(script))) == {"a"}

    def test_the_assistant_the_tools_belong_to_is_recorded_in_the_scope(self) -> None:
        script = _script_tool(ScriptToolRegistry(_user()))

        bind_script_registries([_tool("a"), script], MagicMock(id="assistant-9"))

        scope = _context(script).scope
        assert isinstance(scope, ToolListScope)
        assert scope.assistant_id == "assistant-9"

    def test_the_binding_names_no_tool_class_it_finds_registries_by_attribute(self) -> None:
        class _OtherHolder(_NamedTool):
            name: str = "other_holder"
            script_registry: ScriptToolRegistry | None = None

        holder = _OtherHolder(script_registry=ScriptToolRegistry(_user()))

        bind_script_registries([_tool("a"), holder])

        assert _names(holder.script_registry.context()) == ["a", "other_holder"]  # type: ignore[union-attr]

    def test_script_tool_without_registry_is_skipped(self) -> None:
        script = _script_tool(None)

        bind_script_registries([_tool("a"), script])

        assert script.script_registry is None

    def test_no_script_tool_changes_nothing(self) -> None:
        tools: list[BaseTool] = [_tool("a"), _tool("b")]

        bind_script_registries(tools)

        assert [t.name for t in tools] == ["a", "b"]

    def test_unfilled_registry_stays_closed_without_a_script_tool_binding(self) -> None:
        script = _script_tool(ScriptToolRegistry(_user()))

        assert _context(script).scope_kind is ScriptScopeKind.NONE


@pytest.fixture
def assistant() -> Mock:
    mock = Mock(spec=Assistant)
    mock.id = "assistant-id"
    mock.name = "Assistant"
    mock.project = "assistant-project"
    mock.toolkits = []
    mock.context = []
    mock.assistant_ids = []
    mock.skill_ids = []
    mock.mcp_servers = []
    mock.enable_image_generation = False
    mock.created_by = None
    mock.interactive_enabled = False
    return mock


@pytest.fixture
def request_model() -> AssistantChatRequest:
    return AssistantChatRequest(text="hi", conversation_id="conv-1")


def _get_tools(
    assistant: Mock,
    request: AssistantChatRequest,
    *,
    core: Sequence[BaseTool] = (),
    others: Sequence[BaseTool] = (),
    appended_last: Sequence[BaseTool] = (),
    **kwargs: str | None,
) -> list[BaseTool]:
    def append_last(tools: list[BaseTool], *args: object, **kw: object) -> list[BaseTool]:
        tools.extend(appended_last)
        return tools

    user = Mock(spec=User)
    user.id = "user-1"
    user.is_admin = False
    with (
        patch.object(ToolkitService, "get_core_tools", return_value=list(core)),
        patch.object(ToolkitService, "add_context_tools", return_value=[]),
        patch.object(ToolkitService, "_get_tools", return_value=list(others)),
        patch.object(ToolkitService, "_append_request_user_input_tool_if_enabled", side_effect=append_last),
    ):
        return ToolkitService.get_tools(
            assistant=assistant,
            request=request,
            user=user,
            llm_model="gpt-4",
            request_uuid="uuid",
            **kwargs,
        )


class TestGetToolsFillsScriptRegistry:
    def test_registry_holds_final_list_minus_excluded_tools(
        self, assistant: Mock, request_model: AssistantChatRequest
    ) -> None:
        script = _script_tool(ScriptToolRegistry(_user()))
        stream = _StreamTool(thread_generator=MagicMock())

        result = _get_tools(
            assistant,
            request_model,
            core=[_tool("core_tool"), _tool("skill_tool")],
            others=[script, _tool("core_tool")],
            appended_last=[stream],
        )

        context = _context(script)
        assert script in result and stream in result
        assert set(_names(context)) == {"core_tool", "skill_tool"}

    def test_chat_request_with_workflow_execution_id_stays_assistant_scope(
        self, assistant: Mock, request_model: AssistantChatRequest
    ) -> None:
        request_model.workflow_execution_id = "exec-1"
        script = _script_tool(ScriptToolRegistry(_user()))

        _get_tools(assistant, request_model, others=[script, _tool("a")])

        assert _context(script).scope_kind is ScriptScopeKind.ASSISTANT

    def test_get_tools_takes_no_workflow_project_so_assistant_nodes_stay_assistant_scope(
        self, assistant: Mock, request_model: AssistantChatRequest
    ) -> None:
        assert "workflow_project" not in inspect.signature(ToolkitService.get_tools).parameters
        script = _script_tool(ScriptToolRegistry(_user()))

        _get_tools(assistant, request_model, others=[script, _tool("a")])

        context = _context(script)
        assert context.scope_kind is ScriptScopeKind.ASSISTANT
        assert isinstance(context.scope, ToolListScope)

    def test_get_tools_hands_the_assistant_to_the_binding(
        self, assistant: Mock, request_model: AssistantChatRequest
    ) -> None:
        script = _script_tool(ScriptToolRegistry(_user()))

        _get_tools(assistant, request_model, others=[script, _tool("a")])

        scope = _context(script).scope
        assert isinstance(scope, ToolListScope)
        assert scope.assistant_id == "assistant-id"

    def test_assistant_without_the_script_tool_yields_no_script_tool(
        self, assistant: Mock, request_model: AssistantChatRequest
    ) -> None:
        result = _get_tools(assistant, request_model, others=[_tool("a")])

        assert [t.name for t in result] == ["a"]
