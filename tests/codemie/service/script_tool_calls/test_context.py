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


class TestScriptToolRegistry:
    def test_unfilled_registry_has_no_scope(self, user: User) -> None:
        context = ScriptToolRegistry(user).context()

        assert context.scope_kind is ScriptScopeKind.NONE
        assert context.user is user
        assert isinstance(context.scope, NoScope)

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
        assert [kind.value for kind in ScriptScopeKind] == ["none", "assistant"]


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


class TestRunAttribution:
    """The fields the polling thread cannot read from request contextvars are captured when the context is built."""

    METADATA = {
        "agent_name": "my-assistant",
        "llm_model": "gpt-x",
        "project": "assistant-project",
        "user_name": "U One",
    }

    def test_assistant_context_takes_name_model_and_project_from_the_tool_metadata(self, user: User) -> None:
        registry = ScriptToolRegistry(user)
        registry.fill([_PlainTool()])

        context = registry.context(self.METADATA)

        assert (context.assistant_name, context.llm_model, context.project, context.user_id) == (
            "my-assistant",
            "gpt-x",
            "assistant-project",
            "user-1",
        )

    def test_project_is_the_request_thread_billing_project_when_there_is_one(self, user: User) -> None:
        registry = ScriptToolRegistry(user)
        registry.fill([_PlainTool()])

        with patch("codemie.core.dependecies.get_current_project") as current:
            current.side_effect = lambda fallback=None: "billing@example.com" if fallback else ""
            context = registry.context(self.METADATA)

        assert context.project == "billing@example.com"
        current.assert_called_once_with(fallback="assistant-project")

    def test_captured_values_survive_the_end_of_the_request_context(self, user: User) -> None:
        registry = ScriptToolRegistry(user)
        registry.fill([_PlainTool()])
        with patch("codemie.core.dependecies.get_current_project", return_value="billing-project"):
            context = registry.context(self.METADATA)

        assert context.project == "billing-project"
        assert context.user_id == "user-1"

    def test_missing_metadata_gives_empty_fields_and_the_user_id(self, user: User) -> None:
        context = ScriptToolRegistry(user).context()

        assert (context.assistant_name, context.llm_model, context.project) == ("", "", "")
        assert context.user_id == "user-1"

    def test_non_text_model_is_stored_as_text(self, user: User) -> None:
        registry = ScriptToolRegistry(user)
        registry.fill([_PlainTool()])

        assert registry.context({"llm_model": 42}).llm_model == "42"

    def test_tool_step_has_no_assistant_and_no_model_but_the_project_of_its_assistant(self, user: User) -> None:
        """A tool step never runs the assistant agent's ``_configure_tools``, so its tool carries no metadata: the
        project comes from the step's virtual assistant."""
        registry = ScriptToolRegistry(user, project="wf-project", is_tool_step=True)
        registry.fill([_PlainTool()])

        context = registry.context()

        assert context.scope_kind is ScriptScopeKind.ASSISTANT
        assert context.is_tool_step is True
        assert context.assistant_name == ""
        assert context.llm_model == ""
        assert context.project == "wf-project"
        assert context.user_id == "user-1"

    def test_without_context_still_carries_the_user_id(self, user: User) -> None:
        assert ScriptRunContext.without_context(user).user_id == "user-1"


class TestRunIds:
    """The assistant id and workflow id given to the registry reach every kind of context it builds."""

    def test_assistant_scope_context_carries_both_ids(self, user: User) -> None:
        registry = ScriptToolRegistry(user, assistant_id="asst-1", workflow_id="wf-1")
        registry.fill([_PlainTool()])

        context = registry.context()

        assert context.scope_kind is ScriptScopeKind.ASSISTANT
        assert (context.assistant_id, context.workflow_id) == ("asst-1", "wf-1")

    def test_workflow_tool_step_context_carries_the_workflow_id(self, user: User) -> None:
        registry = ScriptToolRegistry(user, workflow_id="wf-1", is_tool_step=True)
        registry.fill([_PlainTool()])

        context = registry.context()

        assert context.workflow_id == "wf-1"
        assert context.is_tool_step is True

    def test_an_unfilled_registry_still_carries_the_ids(self, user: User) -> None:
        context = ScriptToolRegistry(user, assistant_id="asst-1", workflow_id="wf-1").context()

        assert context.scope_kind is ScriptScopeKind.NONE
        assert (context.assistant_id, context.workflow_id) == ("asst-1", "wf-1")

    def test_ids_default_to_empty(self, user: User) -> None:
        registry = ScriptToolRegistry(user)
        registry.fill([_PlainTool()])

        context = registry.context()

        assert (context.assistant_id, context.workflow_id) == ("", "")
