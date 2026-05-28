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

from dataclasses import replace
from typing import Any

import langgraph_supervisor.supervisor
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolNode as LangGraphToolNode
from langgraph.types import Command, Send

from codemie.configs import config

ToolNodeOutput = Command | list[ToolMessage] | dict[str, list[ToolMessage]]


def ensure_langgraph_supervisor_compatibility() -> None:
    """Apply LangGraph supervisor compatibility patches once."""
    langgraph_supervisor.supervisor._supports_disable_parallel_tool_calls = lambda model: any(
        candidate in getattr(model, "model_name", "model") for candidate in config.DISABLE_PARALLEL_TOOLS_CALLING_MODELS
    )

    if getattr(LangGraphToolNode._combine_tool_outputs, "__name__", "") != "_patched_toolnode_combine_tool_outputs":
        LangGraphToolNode._combine_tool_outputs = _patched_toolnode_combine_tool_outputs


def _merge_parallel_parent_command_updates(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not left:
        return right
    if not right:
        return left

    merged_update = dict(left)
    for key, value in right.items():
        if key == "messages" and isinstance(merged_update.get(key), list) and isinstance(value, list):
            merged_update[key] = [*merged_update[key], *value]
            continue
        merged_update[key] = value
    return merged_update


def _is_parallel_parent_command(command: Command) -> bool:
    return (
        command.graph is Command.PARENT
        and isinstance(command.goto, list)
        and all(isinstance(send, Send) for send in command.goto)
    )


def _merge_parallel_parent_commands(parent_command: Command | None, command: Command) -> Command:
    if not parent_command:
        return command

    return replace(
        parent_command,
        goto=[*parent_command.goto, *command.goto],
        update=_merge_parallel_parent_command_updates(parent_command.update, command.update),
    )


def _wrap_tool_output(
    tool_node: LangGraphToolNode,
    output: ToolMessage,
    input_type: str,
) -> list[ToolMessage] | dict[str, list[ToolMessage]]:
    return [output] if input_type == "list" else {tool_node._messages_key: [output]}


def _patched_toolnode_combine_tool_outputs(
    self: LangGraphToolNode,
    outputs: list[ToolMessage | Command],
    input_type: str,
) -> list[ToolNodeOutput]:
    if not any(isinstance(output, Command) for output in outputs):
        return outputs if input_type == "list" else {self._messages_key: outputs}

    combined_outputs: list[ToolNodeOutput] = []
    parent_command: Command | None = None

    for output in outputs:
        if isinstance(output, ToolMessage):
            combined_outputs.append(_wrap_tool_output(self, output, input_type))
            continue

        if _is_parallel_parent_command(output):
            parent_command = _merge_parallel_parent_commands(parent_command, output)
            continue

        combined_outputs.append(output)

    if parent_command:
        combined_outputs.append(parent_command)

    return combined_outputs


def apply_langgraph_supervisor_compatibility_patch() -> None:
    ensure_langgraph_supervisor_compatibility()
