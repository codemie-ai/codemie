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

"""The script tool's description carries the SDK reference only while the tool-calling switch is on."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie_tools.data_management.code_executor.sdk_reference import compose_description, read_sdk_reference
from codemie_tools.data_management.code_executor.tool_calling_limits import ToolCallingSettings
from codemie_tools.data_management.workspace.execute_workspace_script_tool import ExecuteWorkspaceScriptTool
from codemie_tools.data_management.workspace.toolkit import AgentWorkspaceToolkit
from codemie_tools.data_management.workspace.tools_vars import EXECUTE_WORKSPACE_SCRIPT_TOOL

SWITCH_HELPER = "codemie_tools.data_management.workspace.toolkit.resolve_tool_calling_settings"
_ON = ToolCallingSettings(run_timeout_seconds=120.0, max_parallel_calls=3)


@pytest.fixture(autouse=True)
def workspace_service() -> Iterator[AgentWorkspaceService]:
    with patch("codemie_tools.data_management.workspace.toolkit.AgentWorkspaceService") as service_cls:
        service = object.__new__(AgentWorkspaceService)
        service.get_workspace = MagicMock(return_value=MagicMock(id="workspace-1"))  # type: ignore[method-assign]
        service.create_workspace = MagicMock(return_value=MagicMock(id="workspace-1"))  # type: ignore[method-assign]
        service_cls.return_value = service
        yield service


def _script_tool() -> ExecuteWorkspaceScriptTool:
    toolkit = AgentWorkspaceToolkit(
        conversation_id="conversation-1",
        user=User(id="user-1", username="u1"),
        image_generator=MagicMock(),
    )
    tools = [tool for tool in toolkit.get_tools() if isinstance(tool, ExecuteWorkspaceScriptTool)]
    assert len(tools) == 1
    return tools[0]


class TestScriptToolDescription:
    def test_switch_on_appends_the_sdk_reference_after_the_base_text(self) -> None:
        with patch(SWITCH_HELPER, return_value=_ON):
            description = _script_tool().description

        base = EXECUTE_WORKSPACE_SCRIPT_TOOL.description
        assert base is not None
        assert description.startswith(base.strip())
        assert description.endswith(read_sdk_reference(_ON))

    def test_switch_off_leaves_the_description_unchanged(self) -> None:
        with patch(SWITCH_HELPER, return_value=None):
            description = _script_tool().description

        assert description == EXECUTE_WORKSPACE_SCRIPT_TOOL.description

    def test_the_switch_is_read_each_time_the_toolkit_is_built(self) -> None:
        with patch(SWITCH_HELPER, return_value=_ON):
            on = _script_tool().description
        with patch(SWITCH_HELPER, return_value=None):
            off = _script_tool().description

        assert on != off

    def test_the_numbers_in_the_reference_are_the_ones_of_the_run_settings(self) -> None:
        with patch(SWITCH_HELPER, return_value=_ON):
            description = _script_tool().description

        assert "Up to 3 calls run at the same time" in description

    def test_the_tool_carries_the_settings_it_was_built_with_and_none_when_off(self) -> None:
        with patch(SWITCH_HELPER, return_value=_ON):
            on = _script_tool()
        with patch(SWITCH_HELPER, return_value=None):
            off = _script_tool()

        assert on.tool_calling is _ON
        assert off.tool_calling is None

    def test_the_setting_is_read_once_per_toolkit_build(self) -> None:
        with patch(SWITCH_HELPER, return_value=_ON) as resolve:
            _script_tool()

        resolve.assert_called_once_with()


class TestComposeDescription:
    def test_joins_non_empty_sections_with_blank_lines(self) -> None:
        assert compose_description("base ", ["one", "", "  ", "two "]) == "base\n\none\n\ntwo"

    def test_without_sections_returns_the_trimmed_base(self) -> None:
        assert compose_description("base", []) == "base"
