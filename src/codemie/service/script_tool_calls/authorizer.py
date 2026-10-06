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

"""Decide whether a workspace script may call a given platform tool."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from codemie.service.script_tool_calls.context import ScriptRunContext
from codemie.service.script_tool_calls.exclusions import is_excluded_from_script_calls
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    CODE_TOOL_BLOCKED,
    CODE_TOOL_UNAVAILABLE,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import ToolCallRefused
from codemie_tools.data_management.workspace.tools_vars import EXECUTE_WORKSPACE_SCRIPT_TOOL

_SCRIPT_TOOL_NAME: str = EXECUTE_WORKSPACE_SCRIPT_TOOL.name


def _unavailable(name: str) -> ToolCallRefused:
    return ToolCallRefused(
        CODE_TOOL_UNAVAILABLE,
        f"Tool '{name}' is not available to this script run; use a tool from your own tool list.",
    )


def authorize_tool_call(context: ScriptRunContext, name: str) -> BaseTool:
    """Return the tool ``name`` the script may call, or raise ``ToolCallRefused``.

    The only place that decides. The script tool is blocked by name; every other tool is looked up in the run's scope
    (a scope with no context refuses with ``no_context``) and must not be excluded, whichever scope produced it.
    """
    if name == _SCRIPT_TOOL_NAME:
        raise ToolCallRefused(CODE_TOOL_BLOCKED, f"Tool '{name}' can never be called from a script; this is permanent.")
    tool = context.scope.resolve(name)
    if tool is None or is_excluded_from_script_calls(tool):
        raise _unavailable(name)
    return tool
