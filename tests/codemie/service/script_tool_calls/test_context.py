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

import dataclasses
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from codemie.rest_api.security.user import User
from codemie.service.script_tool_calls.context import (
    NoScope,
    ProjectScope,
    ScriptRunContext,
    ScriptScopeKind,
    ScriptToolRegistry,
    ToolListScope,
)
from codemie.service.script_tool_calls.exclusions import is_excluded_from_script_calls
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.code_executor.tool_call_protocol import ToolCallRefused


class _Args(BaseModel):
    pass


class _PlainTool(CodeMieTool):
    script_callable = True
    name: str = "plain_tool"
    description: str = "plain"
    args_schema: type[BaseModel] = _Args

    def execute(self) -> str:
        return "ok"


class _StreamTool(CodeMieTool):
    script_callable = True
    name: str = "stream_tool"
    description: str = "holds a stream"
    args_schema: type[BaseModel] = _Args
    thread_generator: object = None

    def execute(self) -> str:
        return "ok"


class _NamedTool(_PlainTool):
    name: str = "other_tool"


@pytest.fixture
def user() -> User:
    return User(id="user-1", username="u1")


class TestScopes:
    def test_no_scope_refuses_every_name_with_no_context(self) -> None:
        with pytest.raises(ToolCallRefused) as excinfo:
            NoScope().resolve("anything")

        assert excinfo.value.code == "no_context"

    def test_no_scope_offers_no_tools(self) -> None:
        assert NoScope().callable_tools() == ()

    def test_tool_list_scope_resolves_by_name_and_lists_its_tools(self) -> None:
        plain, other = _PlainTool(), _NamedTool()
        scope = ToolListScope([plain, other], "assistant-1")

        assert scope.resolve("plain_tool") is plain
        assert scope.resolve("other_tool") is other
        assert scope.resolve("missing") is None
        assert list(scope.callable_tools() or []) == [plain, other]
        assert scope.assistant_id == "assistant-1"

    def test_tool_list_scope_is_a_snapshot_of_the_list_it_was_given(self) -> None:
        tools: list[BaseTool] = [_PlainTool()]
        scope = ToolListScope(tools)

        tools.append(_NamedTool())

        assert scope.resolve("other_tool") is None
        assert len(scope.callable_tools() or []) == 1

    def test_project_scope_resolves_by_name_for_the_user_in_the_project(self, user: User) -> None:
        resolver = MagicMock(return_value=_PlainTool())
        scope = ProjectScope(user, "wf-project", resolver)

        tool = scope.resolve("plain_tool")

        assert tool is resolver.return_value
        resolver.assert_called_once_with(user, "wf-project", "plain_tool")

    def test_project_scope_answers_none_when_the_resolver_finds_no_such_tool(self, user: User) -> None:
        scope = ProjectScope(user, "wf-project", MagicMock(side_effect=ValueError("no such tool")))

        assert scope.resolve("nope") is None

    @pytest.mark.parametrize("error", [KeyError("boom"), RuntimeError("boom")])
    def test_project_scope_answers_none_and_warns_when_the_resolver_fails_unexpectedly(
        self, user: User, error: Exception
    ) -> None:
        scope = ProjectScope(user, "wf-project", MagicMock(side_effect=error))

        with patch("codemie.service.script_tool_calls.context.logger") as logger:
            assert scope.resolve("broken_tool") is None

        logger.warning.assert_called_once()
        assert "broken_tool" in str(logger.warning.call_args)

    def test_project_scope_does_not_build_a_known_excluded_tool(self, user: User) -> None:
        resolver = MagicMock()
        scope = ProjectScope(user, "wf-project", resolver)

        assert scope.resolve("code_executor") is None

        resolver.assert_not_called()

    def test_project_scope_still_resolves_an_unknown_name(self, user: User) -> None:
        resolver = MagicMock(return_value=_PlainTool())

        assert ProjectScope(user, "wf-project", resolver).resolve("not_in_the_registry") is resolver.return_value

    def test_project_scope_still_resolves_a_name_with_a_script_callable_class(self, user: User) -> None:
        resolver = MagicMock(return_value=_PlainTool())
        with patch(
            "codemie.service.script_tool_calls.registry_prefilter.is_known_excluded_from_script_calls",
            return_value=False,
        ):
            assert ProjectScope(user, "wf-project", resolver).resolve("mixed") is resolver.return_value

    def test_project_scope_fails_closed_when_the_registry_check_breaks(self, user: User) -> None:
        resolver = MagicMock()
        with patch("codemie.service.script_tool_calls.registry_prefilter.ensure_loaded", side_effect=RuntimeError("x")):
            assert ProjectScope(user, "wf-project", resolver).resolve("anything") is None

        resolver.assert_not_called()

    def test_project_scope_cannot_list_the_catalog(self, user: User) -> None:
        assert ProjectScope(user, "wf-project", MagicMock()).callable_tools() is None


class TestScriptToolRegistry:
    def test_unfilled_registry_has_no_scope(self, user: User) -> None:
        context = ScriptToolRegistry(user).context()

        assert context.scope_kind is ScriptScopeKind.NONE
        assert context.user is user
        assert isinstance(context.scope, NoScope)

    def test_unfilled_registry_with_workflow_project_has_the_project_scope(self, user: User) -> None:
        context = ScriptToolRegistry(user, workflow_project="wf-project").context()

        assert context.scope_kind is ScriptScopeKind.WORKFLOW
        assert isinstance(context.scope, ProjectScope)
        assert context.scope.project == "wf-project"

    def test_filled_registry_has_the_tool_list_scope_of_its_assistant(self, user: User) -> None:
        plain, other = _PlainTool(), _NamedTool()
        registry = ScriptToolRegistry(user)

        registry.fill([plain, other], MagicMock(id="assistant-7"))
        context = registry.context()

        assert context.scope_kind is ScriptScopeKind.ASSISTANT
        assert isinstance(context.scope, ToolListScope)
        assert context.scope.resolve("plain_tool") is plain
        assert context.scope.resolve("other_tool") is other
        assert context.scope.assistant_id == "assistant-7"

    def test_the_assistant_is_optional(self, user: User) -> None:
        registry = ScriptToolRegistry(user)

        registry.fill([_PlainTool()])

        assert isinstance(registry.context().scope, ToolListScope)
        assert registry.context().scope.assistant_id is None  # type: ignore[union-attr]

    def test_fill_with_empty_tools_is_the_assistant_scope(self, user: User) -> None:
        registry = ScriptToolRegistry(user)

        registry.fill([])

        assert registry.context().scope_kind is ScriptScopeKind.ASSISTANT

    def test_workflow_project_from_constructor_wins(self, user: User) -> None:
        registry = ScriptToolRegistry(user, workflow_project="wf-project")

        registry.fill([_PlainTool()])
        context = registry.context()

        assert context.scope_kind is ScriptScopeKind.WORKFLOW
        assert isinstance(context.scope, ProjectScope)

    def test_fill_takes_no_workflow_project(self, user: User) -> None:
        registry = ScriptToolRegistry(user)

        with pytest.raises(TypeError):
            registry.fill([], workflow_project="wf-project")  # type: ignore[call-arg]

    def test_context_is_a_snapshot_of_the_tools(self, user: User) -> None:
        registry = ScriptToolRegistry(user)
        registry.fill([_PlainTool()])
        context = registry.context()

        registry.fill([_NamedTool()])

        assert context.scope.resolve("plain_tool") is not None
        assert context.scope.resolve("other_tool") is None


class TestScriptRunContext:
    def test_without_context_refuses_everything(self, user: User) -> None:
        context = ScriptRunContext.without_context(user)

        assert context.user is user
        assert context.scope_kind is ScriptScopeKind.NONE
        assert isinstance(context.scope, NoScope)

    def test_context_is_frozen(self, user: User) -> None:
        context = ScriptRunContext.without_context(user)

        with pytest.raises(dataclasses.FrozenInstanceError):
            context.scope_kind = ScriptScopeKind.ASSISTANT  # type: ignore[misc]

    def test_scope_kind_values(self) -> None:
        assert [kind.value for kind in ScriptScopeKind] == ["none", "assistant", "workflow"]


class TestExclusions:
    @pytest.mark.parametrize("tool", [_PlainTool(), _NamedTool()])
    def test_ordinary_tool_is_kept(self, tool: BaseTool) -> None:
        assert is_excluded_from_script_calls(tool) is False

    def test_script_tool_is_excluded(self) -> None:
        from codemie_tools.data_management.workspace.execute_workspace_script_tool import (
            ExecuteWorkspaceScriptTool,
        )

        tool = ExecuteWorkspaceScriptTool.model_construct(name="execute_workspace_script")

        assert is_excluded_from_script_calls(tool) is True

    @pytest.mark.parametrize("class_name", ["MCPTool", "ContextAwareMCPTool"])
    def test_mcp_tools_are_excluded(self, class_name: str) -> None:
        from codemie.service.mcp import toolkit

        tool = getattr(toolkit, class_name).model_construct(name="mcp_tool")

        assert is_excluded_from_script_calls(tool) is True

    def test_request_user_input_tool_is_excluded(self) -> None:
        from codemie.agents.tools.interactive.request_user_input import RequestUserInputTool

        tool = RequestUserInputTool.model_construct(name="request_user_input")

        assert is_excluded_from_script_calls(tool) is True

    def test_tool_with_thread_generator_is_excluded(self) -> None:
        assert is_excluded_from_script_calls(_StreamTool(thread_generator=object())) is True

    def test_tool_with_none_thread_generator_is_kept(self) -> None:
        assert is_excluded_from_script_calls(_StreamTool(thread_generator=None)) is False
