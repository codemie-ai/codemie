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

from collections.abc import Sequence

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from codemie.rest_api.security.user import User
from codemie.service.script_tool_calls import workflow_resolution
from codemie.service.script_tool_calls.authorizer import authorize_tool_call
from codemie.service.script_tool_calls.context import (
    NoScope,
    ProjectScope,
    ScriptRunContext,
    ScriptScopeKind,
    ToolListScope,
)
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.code_executor.tool_call_protocol import ToolCallRefused
from codemie_tools.data_management.workspace.tools_vars import EXECUTE_WORKSPACE_SCRIPT_TOOL


class _Args(BaseModel):
    pass


class _NamedTool(CodeMieTool):
    script_callable = True
    name: str = "named_tool"
    description: str = "named"
    args_schema: type[BaseModel] = _Args

    def execute(self) -> str:
        return "ok"


class _StreamTool(_NamedTool):
    name: str = "stream_tool"
    thread_generator: object = object()


_SCRIPT_TOOL_NAME: str = EXECUTE_WORKSPACE_SCRIPT_TOOL.name


def _user() -> User:
    return User(id="user-1", username="u1")


def _none_context() -> ScriptRunContext:
    return ScriptRunContext(user=_user(), scope=NoScope(), scope_kind=ScriptScopeKind.NONE)


def _assistant_context(tools: Sequence[BaseTool] = ()) -> ScriptRunContext:
    return ScriptRunContext(user=_user(), scope=ToolListScope(tools), scope_kind=ScriptScopeKind.ASSISTANT)


def _workflow_context(resolver: object) -> ScriptRunContext:
    scope = ProjectScope(_user(), "proj", resolver)  # type: ignore[arg-type]
    return ScriptRunContext(user=_user(), scope=scope, scope_kind=ScriptScopeKind.WORKFLOW)


def _refusal(context: ScriptRunContext, name: str) -> ToolCallRefused:
    with pytest.raises(ToolCallRefused) as info:
        authorize_tool_call(context, name)
    return info.value


def _never_resolver(user: User, project: str, name: str) -> BaseTool:
    raise AssertionError("resolver must not be called")


class TestAuthorizeToolCall:
    @pytest.mark.parametrize(
        "context",
        [
            _none_context(),
            _assistant_context([_NamedTool(name=_SCRIPT_TOOL_NAME)]),
            _workflow_context(_never_resolver),
        ],
    )
    def test_script_tool_is_blocked_in_every_scope_before_the_scope_is_asked(self, context: ScriptRunContext) -> None:
        assert _refusal(context, _SCRIPT_TOOL_NAME).code == "tool_blocked"

    def test_no_scope_is_no_context(self) -> None:
        assert _refusal(_none_context(), "named_tool").code == "no_context"

    def test_assistant_returns_tool_from_its_list(self) -> None:
        tool = _NamedTool()

        assert authorize_tool_call(_assistant_context([tool]), "named_tool") is tool

    def test_assistant_unknown_tool_is_unavailable(self) -> None:
        assert _refusal(_assistant_context([_NamedTool()]), "other").code == "tool_unavailable"

    def test_an_excluded_tool_in_the_list_is_unavailable_whichever_scope_produced_it(self) -> None:
        assert _refusal(_assistant_context([_StreamTool()]), "stream_tool").code == "tool_unavailable"

    def test_workflow_returns_resolver_result(self) -> None:
        tool = _NamedTool()
        seen: list[tuple[str, str, str]] = []

        def resolver(user: User, project: str, name: str) -> BaseTool:
            seen.append((user.id, project, name))
            return tool

        assert authorize_tool_call(_workflow_context(resolver), "named_tool") is tool
        assert seen == [("user-1", "proj", "named_tool")]

    def test_workflow_resolver_value_error_is_unavailable(self) -> None:
        def resolver(user: User, project: str, name: str) -> BaseTool:
            raise ValueError("Tool not found: secret details")

        refused = _refusal(_workflow_context(resolver), "missing")

        assert refused.code == "tool_unavailable"
        assert "secret details" not in refused.message

    def test_workflow_excluded_tool_is_unavailable(self) -> None:
        refused = _refusal(_workflow_context(lambda user, project, name: _StreamTool()), "stream_tool")

        assert refused.code == "tool_unavailable"

    def test_message_names_only_the_supplied_tool_name(self) -> None:
        assert "some_tool" in _refusal(_assistant_context(), "some_tool").message

    @pytest.mark.parametrize(
        "module",
        ["codemie.service.script_tool_calls.authorizer", "codemie.service.script_tool_calls.handler"],
    )
    def test_the_policy_modules_do_not_pull_in_the_kubernetes_stack(self, module: str) -> None:
        import subprocess
        import sys

        probe = f"import sys, {module}\nprint('kubernetes' in sys.modules)\n"
        result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)

        assert result.stdout.strip().splitlines()[-1] == "False", "an exception type must not need the sandbox stack"


class TestResolveWorkflowTool:
    def test_resolves_and_always_deletes_virtual_assistant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import codemie.service.assistant as assistant_pkg
        import codemie.service.tools as tools_pkg

        tool = _NamedTool()
        calls: list[tuple[str, object]] = []
        virtual = type("Virtual", (), {"id": "va-1"})()

        def create(name: str, user: User, project: str) -> object:
            calls.append(("create", (name, user.id, project)))
            return virtual

        def find(name: str, toolkits: object, assistant: object, user: User, project: str) -> BaseTool:
            calls.append(("find", (name, assistant is virtual, user.id, project)))
            return tool

        monkeypatch.setattr(assistant_pkg.VirtualAssistantService, "create_from_tool_invocation", create)
        monkeypatch.setattr(assistant_pkg.VirtualAssistantService, "delete", lambda aid: calls.append(("delete", aid)))
        monkeypatch.setattr(tools_pkg.ToolsService, "find_tool_by_invoke_request", find)
        monkeypatch.setattr(tools_pkg.ToolkitService, "get_toolkit_methods", lambda: {})

        assert workflow_resolution.resolve_workflow_tool(_user(), "proj", "named_tool") is tool
        assert calls == [
            ("create", ("named_tool", "user-1", "proj")),
            ("find", ("named_tool", True, "user-1", "proj")),
            ("delete", "va-1"),
        ]

    def test_deletes_virtual_assistant_when_lookup_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import codemie.service.assistant as assistant_pkg
        import codemie.service.tools as tools_pkg

        deleted: list[str] = []
        virtual = type("Virtual", (), {"id": "va-2"})()

        def find(name: str, toolkits: object, assistant: object, user: User, project: str) -> BaseTool:
            raise ValueError("not found")

        monkeypatch.setattr(
            assistant_pkg.VirtualAssistantService, "create_from_tool_invocation", lambda *a, **k: virtual
        )
        monkeypatch.setattr(assistant_pkg.VirtualAssistantService, "delete", lambda aid: deleted.append(aid))
        monkeypatch.setattr(tools_pkg.ToolsService, "find_tool_by_invoke_request", find)
        monkeypatch.setattr(tools_pkg.ToolkitService, "get_toolkit_methods", lambda: {})

        with pytest.raises(ValueError):
            workflow_resolution.resolve_workflow_tool(_user(), "proj", "x")
        assert deleted == ["va-2"]
