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
from codemie_tools.azure_devops.work_item.tools import (
    SearchWorkItemsTool,
    CreateWorkItemTool,
    UpdateWorkItemTool,
    GetWorkItemTool,
    LinkWorkItemsTool,
    GetRelationTypesTool,
    GetCommentsTool,
)
from codemie_tools.azure_devops.work_item.tools_vars import (
    SEARCH_WORK_ITEMS_TOOL,
    CREATE_WORK_ITEM_TOOL,
    UPDATE_WORK_ITEM_TOOL,
    GET_WORK_ITEM_TOOL,
    LINK_WORK_ITEMS_TOOL,
    GET_RELATION_TYPES_TOOL,
    GET_COMMENTS_TOOL,
)


class AzureDevOpsWorkItemToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.AZURE_DEVOPS_WORK_ITEM
    tools: List[Tool] = [
        Tool.from_metadata(SEARCH_WORK_ITEMS_TOOL, tool_class=SearchWorkItemsTool),
        Tool.from_metadata(CREATE_WORK_ITEM_TOOL, tool_class=CreateWorkItemTool),
        Tool.from_metadata(UPDATE_WORK_ITEM_TOOL, tool_class=UpdateWorkItemTool),
        Tool.from_metadata(GET_WORK_ITEM_TOOL, tool_class=GetWorkItemTool),
        Tool.from_metadata(LINK_WORK_ITEMS_TOOL, tool_class=LinkWorkItemsTool),
        Tool.from_metadata(GET_RELATION_TYPES_TOOL, tool_class=GetRelationTypesTool),
        Tool.from_metadata(GET_COMMENTS_TOOL, tool_class=GetCommentsTool),
    ]
    label: str = ToolSet.AZURE_DEVOPS_WORK_ITEM.value
    settings_config: bool = True


class AzureDevOpsWorkItemToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls):
        return AzureDevOpsWorkItemToolkitUI()
