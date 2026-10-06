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

"""Hands a run's final tool list to the script tool's registry."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from langchain_core.tools import BaseTool

from codemie.service.script_tool_calls.context import ScriptToolRegistry
from codemie.service.script_tool_calls.exclusions import is_excluded_from_script_calls

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant, VirtualAssistant


@runtime_checkable
class ScriptRegistryHolder(Protocol):
    """A tool that carries a registry for its script runs (the script tool), so the binding names no tool class."""

    script_registry: ScriptToolRegistry | None


def bind_script_registries(tools: Sequence[BaseTool], assistant: Assistant | VirtualAssistant | None = None) -> None:
    """Fill each script tool's registry with the callable tools of the final list.

    The list is the final tool list of an assistant (a chat, or a workflow assistant node, stored or inline), so
    the script gets that assistant's tool list as its scope. Workflow scope is not decided here: a registry built for
    a bare workflow tool step already carries its trusted project, and a request field never creates it. A script tool
    without a registry is skipped, and an unfilled registry keeps refusing every call.
    """
    registries = [
        tool.script_registry
        for tool in tools
        if isinstance(tool, ScriptRegistryHolder) and tool.script_registry is not None
    ]
    if not registries:
        return

    callable_tools: dict[str, BaseTool] = {}
    for tool in tools:
        if not is_excluded_from_script_calls(tool):
            callable_tools.setdefault(tool.name, tool)

    for registry in registries:
        registry.fill(list(callable_tools.values()), assistant)
