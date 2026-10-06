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

"""The single place that decides which tools a workspace script must not call through ``tool.call``."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from codemie.core.constants import SUPERVISOR_HANDOFF_TOOL_PREFIX


def is_excluded_from_script_calls(tool: BaseTool) -> bool:
    """True for tools a script must not call. Three rules, none of which names a tool class:

    - the class does not set ``script_callable = True``: the flag is **opt-in**, ``False`` on the base class, so a new
      tool is not callable from a script until its class says so (the script tool itself, MCP tools,
      ``request_user_input``, sandbox-spawning tools such as ``code_executor``, the other workspace tools, host
      file-system and command-line tools, IDE tools and platform analytics tools keep an explicit ``False``);
    - the name starts with the supervisor handoff prefix (handoff tools are built outside the tool catalog);
    - the instance depends on the chat stream (it holds a ``thread_generator``).

    """
    if getattr(tool, "script_callable", False) is not True:
        return True
    if tool.name.startswith(f"{SUPERVISOR_HANDOFF_TOOL_PREFIX}_"):
        return True
    return getattr(tool, "thread_generator", None) is not None
