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
from codemie_tools.base.models import ToolKit, ToolSet, Tool
from codemie_tools.azure_devops.test_plan.tools import (
    CreateTestPlanTool,
    DeleteTestPlanTool,
    GetTestPlanTool,
    CreateTestSuiteTool,
    DeleteTestSuiteTool,
    GetTestSuiteTool,
    AddTestCaseTool,
    GetTestCaseTool,
    GetTestCasesTool,
)
from codemie_tools.azure_devops.test_plan.tools_vars import (
    CREATE_TEST_PLAN_TOOL,
    DELETE_TEST_PLAN_TOOL,
    GET_TEST_PLAN_TOOL,
    CREATE_TEST_SUITE_TOOL,
    DELETE_TEST_SUITE_TOOL,
    GET_TEST_SUITE_TOOL,
    ADD_TEST_CASE_TOOL,
    GET_TEST_CASE_TOOL,
    GET_TEST_CASES_TOOL,
)


class AzureDevOpsTestPlanToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.AZURE_DEVOPS_TEST_PLAN
    tools: List[Tool] = [
        Tool.from_metadata(CREATE_TEST_PLAN_TOOL, tool_class=CreateTestPlanTool),
        Tool.from_metadata(DELETE_TEST_PLAN_TOOL, tool_class=DeleteTestPlanTool),
        Tool.from_metadata(GET_TEST_PLAN_TOOL, tool_class=GetTestPlanTool),
        Tool.from_metadata(CREATE_TEST_SUITE_TOOL, tool_class=CreateTestSuiteTool),
        Tool.from_metadata(DELETE_TEST_SUITE_TOOL, tool_class=DeleteTestSuiteTool),
        Tool.from_metadata(GET_TEST_SUITE_TOOL, tool_class=GetTestSuiteTool),
        Tool.from_metadata(ADD_TEST_CASE_TOOL, tool_class=AddTestCaseTool),
        Tool.from_metadata(GET_TEST_CASE_TOOL, tool_class=GetTestCaseTool),
        Tool.from_metadata(GET_TEST_CASES_TOOL, tool_class=GetTestCasesTool),
    ]
    label: str = ToolSet.AZURE_DEVOPS_TEST_PLAN.value
    settings_config: bool = True


class AzureDevOpsTestPlanToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls):
        return AzureDevOpsTestPlanToolkitUI()
