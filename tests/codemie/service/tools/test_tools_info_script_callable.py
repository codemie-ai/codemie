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

"""The tools catalog tells consumers which tools a workspace script may call."""

from unittest.mock import patch

from codemie_tools.base import tool_registry
from codemie_tools.base.models import Tool, ToolKit
from codemie.service.tools.tools_info_service import ToolsInfoService


def _flags(toolkits):
    return {tool["name"]: tool["script_callable"] for toolkit in toolkits for tool in toolkit["tools"]}


def test_mark_script_callable_follows_the_registry():
    toolkits = [{"toolkit": "t", "tools": [{"name": "generic_jira_tool"}, {"name": "code_executor"}, {"name": "nope"}]}]

    ToolsInfoService._mark_script_callable(toolkits)

    assert _flags(toolkits) == {"generic_jira_tool": True, "code_executor": False, "nope": False}


def test_mark_script_callable_skips_entries_without_a_name():
    toolkits = [{"toolkit": "t", "tools": [{"label": "x"}]}, {"toolkit": "empty"}]

    ToolsInfoService._mark_script_callable(toolkits)

    assert "script_callable" not in toolkits[0]["tools"][0]


def test_catalog_flags_real_tools():
    flags = _flags(ToolsInfoService.get_tools_info(show_for_ui=True))

    assert flags["generic_jira_tool"] is True
    assert flags["execute_workspace_script"] is False


def test_tool_model_defaults_to_not_callable_and_serializes_the_flag():
    tool = Tool(name="x")

    assert tool.script_callable is False
    assert ToolKit(toolkit="k", tools=[Tool(name="x", script_callable=True)]).model_dump()["tools"][0][
        "script_callable"
    ]


def test_is_script_callable_unknown_name_is_false():
    with patch.object(tool_registry, "lookup", return_value=None):
        assert tool_registry.is_script_callable("whatever") is False
