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

"""Run context and tool registry for platform tool calls made from workspace scripts."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from langchain_core.tools import BaseTool

from codemie.rest_api.security.user import User
from codemie.service.script_tool_calls.workflow_resolution import resolve_workflow_tool
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import CODE_NO_CONTEXT
from codemie_tools.data_management.code_executor.tool_call_protocol import ToolCallRefused

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant, VirtualAssistant

WorkflowToolResolver = Callable[[User, str, str], BaseTool]


class ScriptScopeKind(StrEnum):
    """What decided the tools a script run may call (for logs and, later, audit)."""

    NONE = "none"
    ASSISTANT = "assistant"
    WORKFLOW = "workflow"


class ScriptScope(Protocol):
    """The tools a script run may call: how to find one by name, and how to list them all."""

    def resolve(self, name: str) -> BaseTool | None:
        """The tool called ``name`` in this scope, or ``None`` when the scope has no such tool."""
        ...

    def callable_tools(self) -> Sequence[BaseTool] | None:
        """Every tool the scope offers, for whoever builds a script (a generator, a test run, a result-shape
        renderer); ``None`` means the whole catalog, which is resolved by name and cannot be listed."""
        ...


class NoScope:
    """Fail-closed scope: the run has no tool-calling context, so every call is refused ``no_context``."""

    def resolve(self, name: str) -> BaseTool | None:
        raise ToolCallRefused(
            CODE_NO_CONTEXT, "This script run has no tool-calling context; no call will succeed in this run."
        )

    def callable_tools(self) -> Sequence[BaseTool] | None:
        return ()


class ToolListScope:
    """The tools of one assistant's final tool list (a chat, or a workflow assistant node, stored or inline)."""

    def __init__(self, tools: Sequence[BaseTool], assistant_id: str | None = None) -> None:
        self.assistant_id: str | None = assistant_id
        self._tools: dict[str, BaseTool] = {tool.name: tool for tool in tools}

    def resolve(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def callable_tools(self) -> Sequence[BaseTool] | None:
        return tuple(self._tools.values())


class ProjectScope:
    """The tools a workflow tool step may call: any catalog tool, resolved by name for the user in the project."""

    def __init__(self, user: User, project: str, resolver: WorkflowToolResolver = resolve_workflow_tool) -> None:
        self._user: User = user
        self.project: str = project
        self._resolver: WorkflowToolResolver = resolver

    def resolve(self, name: str) -> BaseTool | None:
        try:
            return self._resolver(self._user, self.project, name)
        except ValueError:
            return None

    def callable_tools(self) -> Sequence[BaseTool] | None:
        return None


@dataclass(frozen=True)
class ScriptRunContext:
    """Immutable description of who runs a script and which tools it may call."""

    user: User
    scope: ScriptScope
    scope_kind: ScriptScopeKind

    @classmethod
    def without_context(cls, user: User) -> ScriptRunContext:
        """Context that refuses every tool call."""
        return cls(user=user, scope=NoScope(), scope_kind=ScriptScopeKind.NONE)


class ScriptToolRegistry:
    """Mutable holder created with the script tool and filled once the run's tool list is final.

    Unfilled (``fill`` never called) it yields a no-scope context, so an early or failed hookup refuses tool calls
    instead of allowing them. The one exception is a workflow project given at construction, only for a bare
    workflow tool step (the toolkit reads it from the step's explicit marker): such a step never reaches
    ``ToolkitService.get_tools`` (it builds its tool directly), so that trusted project alone gives it the project
    scope. Every assistant, including a workflow assistant node, is filled by ``get_tools`` and gets the tool list.
    """

    def __init__(self, user: User, workflow_project: str | None = None) -> None:
        self._user: User = user
        self._workflow_project: str | None = workflow_project
        self._tool_scope: ToolListScope | None = None

    def fill(self, tools: Sequence[BaseTool], assistant: Assistant | VirtualAssistant | None = None) -> None:
        """Record the run's final tools, and the assistant they belong to."""
        self._tool_scope = ToolListScope(tools, assistant.id if assistant is not None else None)

    def context(self) -> ScriptRunContext:
        if self._workflow_project is not None:
            return ScriptRunContext(
                user=self._user,
                scope=ProjectScope(self._user, self._workflow_project),
                scope_kind=ScriptScopeKind.WORKFLOW,
            )
        if self._tool_scope is None:
            return ScriptRunContext.without_context(self._user)
        return ScriptRunContext(user=self._user, scope=self._tool_scope, scope_kind=ScriptScopeKind.ASSISTANT)
