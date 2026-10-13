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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from langchain_core.tools import BaseTool

from codemie.service.monitoring.tool_run_context import ToolRunContext
from codemie.rest_api.security.user import User
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import CODE_NO_CONTEXT
from codemie_tools.data_management.code_executor.tool_call_protocol import ToolCallRefused

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant, VirtualAssistant


class ScriptScopeKind(StrEnum):
    """Where a run's tool scope comes from."""

    NONE = "none"
    ASSISTANT = "assistant"


class ScriptScope(Protocol):
    """The tools a script run may call: how to find one by name, and how to list them all."""

    def resolve(self, name: str) -> BaseTool | None:
        """The tool called ``name`` in this scope, or ``None`` when the scope has no such tool."""
        ...

    def callable_tools(self) -> Sequence[BaseTool]:
        """Every tool the scope offers, for whoever builds a script (a generator, a test run, a result-shape
        renderer)."""
        ...


class NoScope:
    """Fail-closed scope: the run has no tool-calling context, so every call is refused ``no_context``."""

    def resolve(self, name: str) -> BaseTool | None:
        raise ToolCallRefused(
            CODE_NO_CONTEXT, "This script run has no tool-calling context; no call will succeed in this run."
        )

    def callable_tools(self) -> Sequence[BaseTool]:
        return ()


class ToolListScope:
    """The tools of an assembled tool list: the final list of an assistant, or the tools a workflow tool step lists in
    its ``tool_access``. A name that is not in the list is unavailable."""

    def __init__(self, tools: Sequence[BaseTool], assistant_id: str | None = None) -> None:
        self.assistant_id: str | None = assistant_id
        #: The assistant's direct-call tool output limit in tokens, when known (caps the inner-call savings estimate).
        self.tools_tokens_size_limit: int | None = None
        self._tools: dict[str, BaseTool] = {tool.name: tool for tool in tools}

    def resolve(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def callable_tools(self) -> Sequence[BaseTool]:
        return tuple(self._tools.values())


@dataclass(frozen=True, kw_only=True)
class ScriptRunContext(ToolRunContext):
    """Immutable description of who runs a script and which tools it may call.

    The attribution (user, project, assistant, workflow, model) is the ``ToolRunContext`` part: captured in the request
    thread, because the channel's polling thread has no request contextvars.
    """

    user: User
    scope: ScriptScope
    scope_kind: ScriptScopeKind
    #: True for a workflow tool step: no model wrote its script, so its runs get no savings estimate.
    is_tool_step: bool = False
    #: The assistant's direct-call tool output limit in tokens, when known (caps the inner-call savings estimate).
    tools_tokens_size_limit: int | None = None

    def __post_init__(self) -> None:
        if not self.user_name:  # the run's user is the user the context was built for
            object.__setattr__(self, "user_name", getattr(self.user, "name", "") or "")

    @property
    def counts_toward_savings(self) -> bool:
        """The one rule for the savings estimate (both of its halves): a run an assistant's model wrote."""
        return self.scope_kind is ScriptScopeKind.ASSISTANT and not self.is_tool_step

    @classmethod
    def without_context(cls, user: User, metadata: Mapping[str, object] | None = None) -> ScriptRunContext:
        """Context that refuses every tool call."""
        return cls(
            user=user,
            scope=NoScope(),
            scope_kind=ScriptScopeKind.NONE,
            **_attribution(user, metadata, project_fallback=None),
        )


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _attribution(
    user: User, metadata: Mapping[str, object] | None, *, project_fallback: str | None
) -> dict[str, object]:
    """The context's attribution: read from the script tool's own metadata (filled by the assistant agent; absent for a
    workflow tool step, whose project comes from ``project_fallback``, its virtual assistant's project)."""
    return ToolRunContext.attribution_from_metadata(
        metadata,
        _text(user.id),
        user_name=getattr(user, "name", None) or None,
        project_fallback=project_fallback,
        workflow_from_thread=False,  # the registry carries the workflow id, read in the request by the toolkit
    )


class ScriptToolRegistry:
    """Mutable holder created with the script tool and filled once the run's tool list is final.

    Unfilled (``fill`` never called) it yields a no-scope context, so an early or failed hookup refuses tool calls
    instead of allowing them. Every run that assembles its tools through ``ToolkitService.get_tools`` (a chat, a
    workflow assistant node, a workflow tool step with ``tool_access``) fills it with the final tool list.
    """

    def __init__(
        self,
        user: User,
        *,
        assistant_id: str | None = None,
        workflow_id: str | None = None,
        project: str | None = None,
        is_tool_step: bool = False,
    ) -> None:
        self._user: User = user
        self._assistant_id: str = assistant_id or ""
        self._workflow_id: str = workflow_id or ""
        self._project: str | None = project
        self._is_tool_step: bool = is_tool_step
        self._tool_scope: ToolListScope | None = None

    def fill(self, tools: Sequence[BaseTool], assistant: Assistant | VirtualAssistant | None = None) -> None:
        """Record the run's final tools, and the assistant they belong to."""
        self._tool_scope = ToolListScope(tools, assistant.id if assistant is not None else None)
        limit = getattr(assistant, "tools_tokens_size_limit", None)
        self._tool_scope.tools_tokens_size_limit = limit if isinstance(limit, int) and limit > 0 else None

    def context(self, metadata: Mapping[str, object] | None = None) -> ScriptRunContext:
        """The run's context. Call it in the request thread: attribution is read from ``metadata`` (the script tool's
        own) and the request's contextvars here, because the polling thread sees neither."""
        attribution = _attribution(self._user, metadata, project_fallback=self._project)
        # The ids the registry was built with (the toolkit read them in the request) win over the metadata's.
        attribution.update(
            {k: v for k, v in (("assistant_id", self._assistant_id), ("workflow_id", self._workflow_id)) if v}
        )
        if self._tool_scope is None:
            return ScriptRunContext(user=self._user, scope=NoScope(), scope_kind=ScriptScopeKind.NONE, **attribution)
        return ScriptRunContext(
            user=self._user,
            scope=self._tool_scope,
            scope_kind=ScriptScopeKind.ASSISTANT,
            is_tool_step=self._is_tool_step,
            tools_tokens_size_limit=self._tool_scope.tools_tokens_size_limit,
            **attribution,
        )
