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

"""``tool_access``: the filter the tool of a step names, the toolkits built for the listed tools, and the tool list the
step's tool gets."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from codemie.core.workflow_models import WorkflowTool
from codemie.core.workflow_models.workflow_models import WorkflowAssistantTool
from codemie.rest_api.models.assistant import ToolDetails, ToolKitDetails, VirtualAssistant
from codemie.rest_api.security.user import User
from codemie.service.script_tool_calls.context import ScriptToolRegistry
from codemie.service.tools.tool_service import ToolsService
from codemie_tools.base import tool_registry
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.workspace.execute_workspace_script_tool import ExecuteWorkspaceScriptTool

HOST = "execute_workspace_script"
USER = User(id="runner", username="runner")


class TestRegistryFilter:
    def test_the_script_tool_names_script_callable_as_its_filter(self) -> None:
        assert tool_registry.tool_access_filter(HOST) == "script_callable"
        assert tool_registry.takes_tool_access(HOST) is True

    @pytest.mark.parametrize("tool", ["generic_jira_tool", "no_such_tool"])
    def test_a_tool_without_a_filter_takes_no_tool_access(self, tool: str) -> None:
        assert tool_registry.tool_access_filter(tool) is None
        assert tool_registry.takes_tool_access(tool) is False
        assert tool_registry.can_be_listed_for(tool, "generic_jira_tool") is False

    def test_a_callable_tool_can_be_listed_for_the_script_tool(self) -> None:
        assert tool_registry.can_be_listed_for(HOST, "generic_jira_tool") is True

    @pytest.mark.parametrize("tool", [HOST, "list_workspace_files", "tavily_search_results_json", "no_such_tool"])
    def test_a_tool_that_is_not_callable_or_unknown_cannot(self, tool: str) -> None:
        assert tool_registry.can_be_listed_for(HOST, tool) is False


def _toolkit(name: str) -> ToolKitDetails:
    return ToolKitDetails(toolkit="tk", tools=[ToolDetails(name=name)])


class TestToolkitsForToolAccess:
    def _build(self, access: list[WorkflowAssistantTool] | None, owner: str | None = None) -> tuple[list, MagicMock]:
        with patch.object(ToolsService, "get_toolkit", side_effect=lambda name, *a, **k: _toolkit(name)) as get_toolkit:
            toolkits = ToolsService.get_toolkits_for_tool_access(HOST, access, USER, "proj", owner_user_id=owner)
        return toolkits, get_toolkit

    def test_one_toolkit_per_listed_tool_with_its_alias_and_the_owner(self) -> None:
        toolkits, get_toolkit = self._build(
            [WorkflowAssistantTool(name="generic_jira_tool", integration_alias="jira-a")], owner="creator"
        )

        assert [tool.name for toolkit in toolkits for tool in toolkit.tools] == ["generic_jira_tool"]
        get_toolkit.assert_called_once_with("generic_jira_tool", USER, "proj", "jira-a", owner_user_id="creator")

    def test_no_list_gives_no_toolkits(self) -> None:
        assert self._build(None)[0] == []

    def test_a_tool_the_filter_refuses_is_never_built(self) -> None:
        toolkits, get_toolkit = self._build(
            [WorkflowAssistantTool(name="list_workspace_files"), WorkflowAssistantTool(name="generic_jira_tool")]
        )

        assert [tool.name for toolkit in toolkits for tool in toolkit.tools] == ["generic_jira_tool"]
        assert [call.args[0] for call in get_toolkit.call_args_list] == ["generic_jira_tool"]

    def test_a_tool_listed_twice_is_dropped(self) -> None:
        toolkits, _ = self._build(
            [
                WorkflowAssistantTool(name="generic_jira_tool", integration_alias="jira-a"),
                WorkflowAssistantTool(name="generic_jira_tool"),
                WorkflowAssistantTool(name="generic_confluence_tool"),
            ]
        )

        assert [tool.name for toolkit in toolkits for tool in toolkit.tools] == ["generic_confluence_tool"]


class _Args(BaseModel):
    pass


class _Listed(CodeMieTool):
    name: str = "listed"
    description: str = "d"
    args_schema: type[BaseModel] = _Args
    script_callable = True

    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return "ok"


class _Extra(_Listed):
    name: str = "extra"


class TestFindToolWithAccess:
    def _assistant(self) -> VirtualAssistant:
        return VirtualAssistant(
            name="v",
            description="d",
            system_prompt="p",
            project="proj",
            toolkits=[_toolkit(HOST), _toolkit("listed")],
        )

    def test_the_script_tool_sees_exactly_the_listed_tools(self) -> None:
        registry = ScriptToolRegistry(USER)
        script = MagicMock(spec=ExecuteWorkspaceScriptTool)
        script.name = HOST
        script.script_registry = registry
        listed, extra = _Listed(), _Extra()
        step = WorkflowTool(id="s", tool=HOST, tool_access=[WorkflowAssistantTool(name="listed")])

        with patch("codemie.service.tools.ToolkitService.get_tools", return_value=[script, listed, extra]):
            found = ToolsService.find_tool_with_access(step, self._assistant(), USER, "exec-1")

        assert found is script
        scope = registry.context().scope
        assert scope.resolve("listed") is listed
        assert scope.resolve("extra") is None  # assembled by get_tools, but not listed in the step
        assert [tool.name for tool in scope.callable_tools()] == ["listed"]

    def test_a_step_with_no_listed_tools_leaves_the_script_with_none(self) -> None:
        registry = ScriptToolRegistry(USER)
        script = MagicMock(spec=ExecuteWorkspaceScriptTool)
        script.name = HOST
        script.script_registry = registry
        assistant = self._assistant().model_copy(update={"toolkits": [_toolkit(HOST)]})
        step = WorkflowTool(id="s", tool=HOST)

        with patch("codemie.service.tools.ToolkitService.get_tools", return_value=[script, _Extra()]):
            ToolsService.find_tool_with_access(step, assistant, USER, "exec-1")

        scope = registry.context().scope
        assert scope.resolve("extra") is None
        assert list(scope.callable_tools()) == []

    def test_the_assembly_is_asked_for_no_extra_context_tools_and_the_step_tool_configs(self) -> None:
        script = MagicMock(spec=ExecuteWorkspaceScriptTool)
        script.name = HOST
        script.script_registry = None
        step = WorkflowTool(id="s", tool=HOST)

        with patch("codemie.service.tools.ToolkitService.get_tools", return_value=[script]) as get_tools:
            ToolsService.find_tool_with_access(step, self._assistant(), USER, "exec-1", request_headers={"h": "v"})

        kwargs = get_tools.call_args.kwargs
        assert kwargs["exclude_extra_context_tools"] is True
        assert kwargs["request_headers"] == {"h": "v"}
        assert kwargs["request"].workflow_execution_id == "exec-1"
        assert kwargs["user"] is USER

    def test_a_missing_tool_is_an_error(self) -> None:
        step = WorkflowTool(id="s", tool=HOST)

        with patch("codemie.service.tools.ToolkitService.get_tools", return_value=[_Listed()]):
            with pytest.raises(ValueError):
                ToolsService.find_tool_with_access(step, self._assistant(), USER, "exec-1")
