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
from codemie_tools.azure_devops.work_item.toolkit import AzureDevOpsWorkItemToolkit, AzureDevOpsWorkItemToolkitUI


class TestAzureDevOpsWorkItemToolkit:
    def test_get_definition(self):
        toolkit_ui = AzureDevOpsWorkItemToolkit.get_definition()
        assert isinstance(toolkit_ui, AzureDevOpsWorkItemToolkitUI)
        assert toolkit_ui.toolkit == ToolSet.AZURE_DEVOPS_WORK_ITEM
        assert len(toolkit_ui.tools) == 7
        assert toolkit_ui.label == ToolSet.AZURE_DEVOPS_WORK_ITEM.value


class TestAzureDevOpsWorkItemToolkitUI:
    def test_toolkit_property(self):
        toolkit_ui = AzureDevOpsWorkItemToolkitUI()
        assert toolkit_ui.toolkit == ToolSet.AZURE_DEVOPS_WORK_ITEM

    def test_tools_property(self):
        toolkit_ui = AzureDevOpsWorkItemToolkitUI()
        assert len(toolkit_ui.tools) == 7

        # Check that the tools are correctly defined
        tool_names = [tool.name for tool in toolkit_ui.tools]
        assert "search_work_items" in tool_names
        assert "create_work_item" in tool_names
        assert "update_work_item" in tool_names
        assert "get_work_item" in tool_names
        assert "link_work_items" in tool_names
        assert "get_relation_types" in tool_names
        assert "get_comments" in tool_names
