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

"""Which tools a script may call from a workflow tool step and from a workflow assistant step.

A bare tool step resolves any catalog tool by name for the running user in the workflow project (``ProjectScope``);
an assistant step is limited to its own assistant's tool list, and a request field never widens either scope.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.script_tool_calls.authorizer import authorize_tool_call
from codemie.service.script_tool_calls.binding import bind_script_registries
from codemie.service.script_tool_calls.context import (
    ProjectScope,
    ScriptRunContext,
    ScriptScopeKind,
    ScriptToolRegistry,
    ToolListScope,
)
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    CODE_TOOL_UNAVAILABLE,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import ToolCallRefused
from codemie_tools.data_management.workspace.execute_workspace_script_tool import ExecuteWorkspaceScriptTool

PROJECT = "wf-project"
RUNNING_USER = User(id="running-user", username="runner")


class _NoArgs(BaseModel):
    pass


class _CatalogTool(CodeMieTool):
    script_callable = True

    name: str = "catalog_tool"
    description: str = "a catalog tool that opted in to script calls"
    args_schema: type[BaseModel] = _NoArgs

    def execute(self, **kwargs: object) -> str:
        return "ok"


class _ExcludedTool(_CatalogTool):
    """A catalog tool that never opted in (``script_callable`` stays ``False``, as on the base class)."""

    script_callable = False

    name: str = "excluded_tool"


class _OtherTool(_CatalogTool):
    name: str = "other_tool"


@pytest.fixture
def catalog() -> Iterator[dict[str, MagicMock]]:
    """The real ``resolve_workflow_tool`` over a fake catalog: the services it imports lazily are replaced."""
    virtual_assistant_service = MagicMock(name="VirtualAssistantService")
    virtual_assistant_service.create_from_tool_invocation.return_value = MagicMock(id="virtual-1")
    tools_service = MagicMock(name="ToolsService")
    known: dict[str, CodeMieTool] = {"catalog_tool": _CatalogTool(), "excluded_tool": _ExcludedTool()}

    def find(name: str, *_: object) -> CodeMieTool:
        if name not in known:
            raise ValueError(f"Tool *{name}* not found.")
        return known[name]

    tools_service.find_tool_by_invoke_request.side_effect = find
    with (
        patch("codemie.service.assistant.VirtualAssistantService", virtual_assistant_service),
        patch("codemie.service.tools.ToolsService", tools_service),
        patch("codemie.service.tools.ToolkitService", MagicMock(name="ToolkitService")),
    ):
        yield {"assistants": virtual_assistant_service, "tools": tools_service}


def _tool_step_context() -> ProjectScope:
    context = ScriptToolRegistry(RUNNING_USER, workflow_project=PROJECT).context()
    assert context.scope_kind is ScriptScopeKind.WORKFLOW
    assert isinstance(context.scope, ProjectScope)
    return context.scope


def test_project_scope_resolves_a_catalog_tool_for_the_running_user_in_the_workflow_project(
    catalog: dict[str, MagicMock],
) -> None:
    scope = _tool_step_context()
    context = ScriptToolRegistry(RUNNING_USER, workflow_project=PROJECT).context()

    tool = authorize_tool_call(context, "catalog_tool")

    assert tool.name == "catalog_tool"
    assert scope.project == PROJECT
    catalog["assistants"].create_from_tool_invocation.assert_called_once_with("catalog_tool", RUNNING_USER, PROJECT)
    _, _, assistant, user, project = catalog["tools"].find_tool_by_invoke_request.call_args.args
    assert (assistant.id, user, project) == ("virtual-1", RUNNING_USER, PROJECT)
    catalog["assistants"].delete.assert_called_once_with("virtual-1")


def test_project_scope_does_not_list_its_tools() -> None:
    assert _tool_step_context().callable_tools() is None


def test_unknown_tool_is_unavailable_and_its_temporary_assistant_is_removed(catalog: dict[str, MagicMock]) -> None:
    context = ScriptToolRegistry(RUNNING_USER, workflow_project=PROJECT).context()

    with pytest.raises(ToolCallRefused) as excinfo:
        authorize_tool_call(context, "no_such_tool")

    assert excinfo.value.code == CODE_TOOL_UNAVAILABLE
    catalog["assistants"].delete.assert_called_once_with("virtual-1")


def test_tool_that_did_not_opt_in_is_unavailable_even_though_the_catalog_resolves_it(
    catalog: dict[str, MagicMock],
) -> None:
    context = ScriptToolRegistry(RUNNING_USER, workflow_project=PROJECT).context()

    with pytest.raises(ToolCallRefused) as excinfo:
        authorize_tool_call(context, "excluded_tool")

    assert excinfo.value.code == CODE_TOOL_UNAVAILABLE


def test_known_excluded_tool_builds_no_virtual_assistant(catalog: dict[str, MagicMock]) -> None:
    context = ScriptToolRegistry(RUNNING_USER, workflow_project=PROJECT).context()

    with pytest.raises(ToolCallRefused) as excinfo:
        authorize_tool_call(context, "code_executor")

    assert excinfo.value.code == CODE_TOOL_UNAVAILABLE
    catalog["assistants"].create_from_tool_invocation.assert_not_called()


@pytest.mark.parametrize(
    "name",
    [
        "code_executor",
        "generate_image_tool",
        "file_analysis",
        "pptx_tool",
        "pdf_tool",
        "csv_tool",
        "excel_tool",
        "docx_tool",
        "email_analysis_tool",
    ],
)
def test_excluded_tools_are_unavailable_and_never_built(name: str) -> None:
    resolver = MagicMock(side_effect=RuntimeError("the resolver must not be reached"))
    context = ScriptRunContext(
        user=RUNNING_USER, scope=ProjectScope(RUNNING_USER, PROJECT, resolver), scope_kind=ScriptScopeKind.WORKFLOW
    )

    with pytest.raises(ToolCallRefused) as excinfo:
        authorize_tool_call(context, name)

    assert excinfo.value.code == CODE_TOOL_UNAVAILABLE
    resolver.assert_not_called()


def _assistant_step_registry() -> ScriptToolRegistry:
    """What ``ToolkitService.get_tools`` does for a workflow assistant step: a script tool, then the final binding."""
    registry = ScriptToolRegistry(RUNNING_USER)
    script_tool = ExecuteWorkspaceScriptTool(
        conversation_id="exec-1", user=RUNNING_USER, workspace_service=MagicMock(), script_registry=registry
    )
    assistant = Assistant(name="step-assistant", description="d", system_prompt="p", project=PROJECT)
    bind_script_registries([script_tool, _CatalogTool()], assistant)
    return registry


def test_assistant_step_scope_is_only_its_own_tools_and_never_the_workflow_catalog(
    catalog: dict[str, MagicMock],
) -> None:
    """A request's ``workflow_execution_id`` gains no scope either: see test_toolkit_script_registry."""
    context = _assistant_step_registry().context()

    assert context.scope_kind is ScriptScopeKind.ASSISTANT
    assert isinstance(context.scope, ToolListScope)
    assert [tool.name for tool in context.scope.callable_tools() or ()] == ["catalog_tool"]
    assert authorize_tool_call(context, "catalog_tool").name == "catalog_tool"
    with pytest.raises(ToolCallRefused) as excinfo:
        authorize_tool_call(context, "other_tool")
    assert excinfo.value.code == CODE_TOOL_UNAVAILABLE
    catalog["assistants"].create_from_tool_invocation.assert_not_called()
