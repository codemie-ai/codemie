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

"""Extra workflow step settings a tool takes, described for the UI as a component and its props.

A tool class names the step fields it takes (``CodeMieTool.step_config_fields``); this module says how the UI edits
each field. ``component`` comes from a closed vocabulary the UI implements; an unknown one falls back to a plain
editor. Everything feature-specific (filters, labels, help) is sent from here, not kept in the UI.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from codemie_tools.base.tool_registry import step_config_fields, tool_access_filter


class StepConfigEntry(BaseModel):
    field: str = Field(description="Name of the workflow tool step field this entry edits")
    component: str = Field(description="UI component that edits the field")
    props: dict[str, Any] = Field(default_factory=dict, description="Props for the component")


_DEFAULT_TEXT: tuple[str, str] = (
    "Tools available to the step",
    "Pick the tools this step may use, the way you pick the tools of an assistant. A step with no tools has none. "
    "For each tool leave the automatic integration lookup (the running user's integration in the workflow's project) "
    "or pick an integration explicitly. In a global workflow an explicitly picked integration is the workflow "
    "author's, for everyone who runs the workflow.",
)
#: Label and help by the filter flag the tool of the step names, so the text says what the step's tool does with them.
_TEXT_BY_FILTER: dict[str, tuple[str, str]] = {
    "script_callable": (
        "Tools the script may call",
        "Pick the tools the script may call, the way you pick the tools of an assistant. A step with no tools calls "
        "none. For each tool leave the automatic integration lookup (the running user's integration in the "
        "workflow's project) or pick an integration explicitly. In a global workflow an explicitly picked "
        "integration is the workflow author's, for everyone who runs the workflow.",
    ),
}


def _tool_access_entry(tool_name: str) -> StepConfigEntry:
    """The ``tool_access`` editor of a step with ``tool_name``: only tools that pass the filter the tool names."""
    flag = tool_access_filter(tool_name)
    label, help_text = _TEXT_BY_FILTER.get(flag or "", _DEFAULT_TEXT)
    return StepConfigEntry(
        field="tool_access",
        component="tool_integration_picker",
        props={
            "label": label,
            "help": help_text,
            "tool_filter": {flag: True} if flag else {},
            "unique_tools": True,
        },
    )


def get_step_config(tool_name: str) -> list[StepConfigEntry]:
    """The step settings of ``tool_name``, in the order the tool class declares them (empty for most tools)."""
    return [_tool_access_entry(tool_name) for field in step_config_fields(tool_name) if field == "tool_access"]
