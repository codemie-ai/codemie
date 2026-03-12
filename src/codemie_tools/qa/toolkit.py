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

from typing import List

from codemie_tools.base.base_toolkit import DiscoverableToolkit
from codemie_tools.base.models import ToolKit, Tool, ToolSet
from codemie_tools.qa.zephyr.tools import ZephyrGenericTool
from codemie_tools.qa.zephyr.tools_vars import ZEPHYR_TOOL
from codemie_tools.qa.zephyr_squad.tools import ZephyrSquadGenericTool
from codemie_tools.qa.zephyr_squad.tools_vars import ZEPHYR_SQUAD_TOOL
from codemie_tools.qa.xray.tools import XrayGetTestsTool, XrayCreateTestTool, XrayExecuteGraphQLTool
from codemie_tools.qa.xray.tools_vars import XRAY_GET_TESTS_TOOL, XRAY_CREATE_TEST_TOOL, XRAY_EXECUTE_GRAPHQL_TOOL


class QualityAssuranceToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.QUALITY_ASSURANCE
    tools: List[Tool] = [
        Tool.from_metadata(ZEPHYR_TOOL, tool_class=ZephyrGenericTool),
        Tool.from_metadata(ZEPHYR_SQUAD_TOOL, tool_class=ZephyrSquadGenericTool),
        Tool.from_metadata(XRAY_GET_TESTS_TOOL, tool_class=XrayGetTestsTool),
        Tool.from_metadata(XRAY_CREATE_TEST_TOOL, tool_class=XrayCreateTestTool),
        Tool.from_metadata(XRAY_EXECUTE_GRAPHQL_TOOL, tool_class=XrayExecuteGraphQLTool),
    ]
    label: str = ToolSet.QUALITY_ASSURANCE.value


class QualityAssuranceToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls):
        return QualityAssuranceToolkitUI()
