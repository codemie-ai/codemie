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

"""The single exclusion module: which tools a workspace script must not call."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import ClassVar

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from codemie.core.constants import SUPERVISOR_HANDOFF_TOOL_PREFIX
from codemie.service.script_tool_calls.exclusions import is_excluded_from_script_calls
from codemie.service.script_tool_calls import exclusions
from codemie_tools.base.codemie_tool import CodeMieTool
from tests.tool_discovery import discover_tool_classes, qualified_name


class _Args(BaseModel):
    pass


class _PlainTool(CodeMieTool):
    script_callable = True
    name: str = "plain_tool"
    description: str = "a tool"
    args_schema: type[BaseModel] = _Args

    def execute(self) -> str:
        return "ok"


class _FlaggedTool(_PlainTool):
    name: str = "flagged_tool"
    script_callable: ClassVar[bool] = False


class _StreamBoundTool(_PlainTool):
    name: str = "stream_bound_tool"
    thread_generator: object = None


# Each entry: module path, class name. Located by search of the repository; all are CodeMieTool subclasses.
_NOT_SCRIPT_CALLABLE: list[tuple[str, str]] = [
    # sandbox-spawning tools
    ("codemie_tools.data_management.code_executor.code_executor_tool", "CodeExecutorTool"),
    ("codemie_tools.data_management.workspace.execute_workspace_script_tool", "ExecuteWorkspaceScriptTool"),
    # every other tool of the workspace toolkit
    ("codemie_tools.data_management.workspace.tools", "ListWorkspaceFilesTool"),
    ("codemie_tools.data_management.workspace.tools", "ReadWorkspaceFileTool"),
    ("codemie_tools.data_management.workspace.tools", "WriteWorkspaceFileTool"),
    ("codemie_tools.data_management.workspace.tools", "EditWorkspaceFileTool"),
    ("codemie_tools.data_management.workspace.tools", "DeleteWorkspaceFileTool"),
    ("codemie_tools.data_management.workspace.tools", "GrepWorkspaceFilesTool"),
    ("codemie_tools.data_management.workspace.generate_image_tool_v2", "GenerateWorkspaceImageToolV2"),
    ("codemie_tools.data_management.workspace.inspect_workspace_image_tool", "InspectWorkspaceImageTool"),
    # host file-system and command-line tools
    ("codemie_tools.data_management.file_system.tools", "ReadFileTool"),
    ("codemie_tools.data_management.file_system.tools", "ListDirectoryTool"),
    ("codemie_tools.data_management.file_system.tools", "WriteFileTool"),
    ("codemie_tools.data_management.file_system.tools", "CommandLineTool"),
    ("codemie_tools.data_management.file_system.tools", "DiffUpdateFileTool"),
    ("codemie_tools.data_management.file_system.tools", "ReplaceStringTool"),
    ("codemie_tools.data_management.file_system.generate_image_tool", "GenerateImageTool"),
    # tools that cannot run for a script by their nature
    ("codemie.service.mcp.toolkit", "MCPTool"),
    ("codemie.service.mcp.toolkit", "ContextAwareMCPTool"),
    ("codemie.agents.tools.interactive.request_user_input", "RequestUserInputTool"),
    # IDE tools
    ("codemie.agents.tools.ide.ide_tool", "IdeTool"),
    # platform analytics tools
    ("codemie.agents.tools.platform.platform_tool", "GetAssistantsTool"),
    ("codemie.agents.tools.platform.platform_tool", "GetConversationMetricsTool"),
    ("codemie.agents.tools.platform.platform_tool", "GetRawConversationsTool"),
    ("codemie.agents.tools.platform.platform_tool", "GetSpendingTool"),
    ("codemie.agents.tools.platform.platform_tool", "GetKeySpendingTool"),
    ("codemie.agents.tools.platform.platform_tool", "GetConversationAnalyticsTool"),
    # provider tools are built at run time from a provider's configuration; every built class inherits this base
    ("codemie.service.provider.provider_tool_factory", "ProviderToolBase"),
]


def _tool_class(module: str, name: str) -> type[BaseTool]:
    tool_class: type[BaseTool] = getattr(importlib.import_module(module), name)
    return tool_class


class TestScriptCallableFlag:
    def test_a_tool_is_not_callable_by_default_a_class_opts_in(self) -> None:
        class _NotOptedIn(CodeMieTool):
            name: str = "not_opted_in"
            description: str = "a tool"
            args_schema: type[BaseModel] = _Args

            def execute(self) -> str:
                return "ok"

        assert CodeMieTool.script_callable is False
        assert is_excluded_from_script_calls(_NotOptedIn())
        assert not is_excluded_from_script_calls(_PlainTool()), "_PlainTool sets script_callable = True"

    def test_flagged_tool_is_excluded(self) -> None:
        assert is_excluded_from_script_calls(_FlaggedTool())

    @pytest.mark.parametrize(("module", "name"), _NOT_SCRIPT_CALLABLE)
    def test_listed_tool_classes_are_not_script_callable(self, module: str, name: str) -> None:
        tool_class = _tool_class(module, name)

        assert tool_class.script_callable is False  # type: ignore[attr-defined]

    @pytest.mark.parametrize(("module", "name"), _NOT_SCRIPT_CALLABLE)
    def test_instances_of_listed_classes_are_excluded(self, module: str, name: str) -> None:
        instance = _tool_class(module, name).model_construct()

        assert is_excluded_from_script_calls(instance)

    def test_a_subclass_of_a_flagged_class_stays_excluded(self) -> None:
        class _Sub(_FlaggedTool):
            name: str = "sub_tool"

        assert is_excluded_from_script_calls(_Sub())


class TestExistingExclusionsStayInPlace:
    def test_script_tool_mcp_user_input_and_stream_tools_are_excluded(self) -> None:
        from codemie.agents.tools.interactive.request_user_input import RequestUserInputTool
        from codemie.service.mcp.toolkit import ContextAwareMCPTool, MCPTool

        for tool_class in (MCPTool, ContextAwareMCPTool, RequestUserInputTool):
            assert is_excluded_from_script_calls(tool_class.model_construct())
        assert is_excluded_from_script_calls(_StreamBoundTool(thread_generator=object()))

    def test_a_tool_without_a_thread_generator_is_callable(self) -> None:
        assert not is_excluded_from_script_calls(_StreamBoundTool())


class TestHandoffTools:
    def test_supervisor_handoff_tool_names_are_excluded(self) -> None:
        handoff = _PlainTool(name=f"{SUPERVISOR_HANDOFF_TOOL_PREFIX}_other_assistant")

        assert is_excluded_from_script_calls(handoff)

    def test_a_tool_that_merely_contains_the_prefix_is_callable(self) -> None:
        assert not is_excluded_from_script_calls(_PlainTool(name=f"my_{SUPERVISOR_HANDOFF_TOOL_PREFIX}_tool"))


class TestTheModuleNamesNoToolClass:
    def test_exclusions_import_nothing_from_the_tool_packages(self) -> None:
        tree = ast.parse(Path(exclusions.__file__).read_text(encoding="utf-8"))
        imported = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)] + [
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        ]

        offenders = [
            name for name in imported if name.startswith(("codemie_tools", "codemie.agents", "codemie.service"))
        ]

        assert offenders == [], "an exclusion is a flag on the tool class, not a class named here"

    def test_the_rules_are_the_flag_the_handoff_prefix_and_the_stream_attribute(self) -> None:
        source = Path(exclusions.__file__).read_text(encoding="utf-8")

        assert "script_callable" in source
        assert "SUPERVISOR_HANDOFF_TOOL_PREFIX" in source
        assert "thread_generator" in source
        assert "isinstance(" not in source


_SNAPSHOT: Path = Path(__file__).with_name("callable_tool_classes.txt")


class TestCallableToolSnapshot:
    """A tool class that becomes callable from a script must show up in the diff of the change that adds it."""

    def test_the_callable_tool_classes_match_the_reviewed_list(self) -> None:
        actual = sorted(
            qualified_name(tool_class) for tool_class in discover_tool_classes() if tool_class.script_callable
        )
        reviewed = sorted(line for line in _SNAPSHOT.read_text(encoding="utf-8").splitlines() if line.strip())

        added = sorted(set(actual) - set(reviewed))
        removed = sorted(set(reviewed) - set(actual))

        assert not added and not removed, (
            "The set of tool classes a script may call changed.\n"
            f"  newly callable (review each, or set `script_callable = False` on it): {added}\n"
            f"  no longer callable or no longer present (remove from {_SNAPSHOT.name}): {removed}"
        )

    def test_no_listed_class_is_flagged_not_callable_and_none_is_listed_twice(self) -> None:
        reviewed = [line for line in _SNAPSHOT.read_text(encoding="utf-8").splitlines() if line.strip()]
        flagged = {
            qualified_name(tool_class) for tool_class in discover_tool_classes() if not tool_class.script_callable
        }

        assert len(reviewed) == len(set(reviewed))
        assert not (set(reviewed) & flagged)

    def test_the_discovery_finds_the_known_tools(self) -> None:
        names = {qualified_name(tool_class) for tool_class in discover_tool_classes()}

        assert "codemie_tools.core.project_management.jira.tools.GenericJiraIssueTool" in names
        assert "codemie.service.mcp.toolkit.MCPTool" in names
        assert (
            "codemie_tools.data_management.workspace.execute_workspace_script_tool.ExecuteWorkspaceScriptTool" in names
        )
