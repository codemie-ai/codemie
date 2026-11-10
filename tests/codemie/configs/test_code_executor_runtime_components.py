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


from unittest.mock import patch

import pytest

from codemie.configs.customer_config import customer_config
from codemie_tools.data_management.workspace.toolkit import AgentWorkspaceToolkit
from codemie_tools.data_management.workspace.tools_vars import EXECUTE_WORKSPACE_SCRIPT_TOOL

AVAILABLE = "codemie.configs.code_executor_availability.code_executor_available"


@pytest.mark.parametrize(
    "standalone,available,expected", [(True, False, False), (True, True, True), (False, False, True)]
)
def test_script_runner_component(standalone, available, expected):
    with patch(AVAILABLE, return_value=available), patch("codemie.configs.customer_config.config") as cfg:
        cfg.STANDALONE = standalone
        assert customer_config.resolve_component("features:workspaceScriptRunner").settings.enabled is expected
        flag = customer_config.resolve_component("features:dynamicCodeInterpreter").settings.enabled
        assert flag is available


def test_ui_hides_script_tool_when_unavailable():
    with patch(AVAILABLE, return_value=False), patch("codemie.configs.customer_config.config") as cfg:
        cfg.STANDALONE = True
        names = [t["name"] for t in AgentWorkspaceToolkit.get_tools_ui_info()["tools"]]
    assert EXECUTE_WORKSPACE_SCRIPT_TOOL.name not in names
    assert names


def test_ui_keeps_script_tool_outside_standalone():
    with patch(AVAILABLE, return_value=False), patch("codemie.configs.customer_config.config") as cfg:
        cfg.STANDALONE = False
        names = [t["name"] for t in AgentWorkspaceToolkit.get_tools_ui_info()["tools"]]
    assert EXECUTE_WORKSPACE_SCRIPT_TOOL.name in names
