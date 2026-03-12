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

from codemie_tools.base.models import ToolSet
from codemie_tools.azure_devops.test_plan.toolkit import AzureDevOpsTestPlanToolkit, AzureDevOpsTestPlanToolkitUI


class TestAzureDevOpsTestPlanToolkit:
    def test_get_definition(self):
        toolkit_ui = AzureDevOpsTestPlanToolkit.get_definition()
        assert isinstance(toolkit_ui, AzureDevOpsTestPlanToolkitUI)
        assert toolkit_ui.toolkit == ToolSet.AZURE_DEVOPS_TEST_PLAN
        assert len(toolkit_ui.tools) == 9
        assert toolkit_ui.label == ToolSet.AZURE_DEVOPS_TEST_PLAN.value


class TestAzureDevOpsTestPlanToolkitUI:
    def test_toolkit_property(self):
        toolkit_ui = AzureDevOpsTestPlanToolkitUI()
        assert toolkit_ui.toolkit == ToolSet.AZURE_DEVOPS_TEST_PLAN

    def test_tools_property(self):
        toolkit_ui = AzureDevOpsTestPlanToolkitUI()
        assert len(toolkit_ui.tools) == 9

        # Check that the tools are correctly defined
        tool_names = [tool.name for tool in toolkit_ui.tools]
        expected_names = [
            "create_test_plan",
            "delete_test_plan",
            "get_test_plan",
            "create_test_suite",
            "delete_test_suite",
            "get_test_suite",
            "add_test_case",
            "get_test_case",
            "get_test_cases",
        ]
        assert sorted(tool_names) == sorted(expected_names)
