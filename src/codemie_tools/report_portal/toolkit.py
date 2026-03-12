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
from .tools import (
    GetExtendedLaunchDataTool,
    GetExtendedLaunchDataAsRawTool,
    GetLaunchDetailsTool,
    GetAllLaunchesTool,
    FindTestItemByIdTool,
    GetTestItemsForLaunchTool,
    GetLogsForTestItemTool,
    GetUserInformationTool,
    GetDashboardDataTool,
    UpdateTestItemTool,
)
from .tools_vars import (
    GET_EXTENDED_LAUNCH_DATA_TOOL,
    GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL,
    GET_LAUNCH_DETAILS_TOOL,
    GET_ALL_LAUNCHES_TOOL,
    FIND_TEST_ITEM_BY_ID_TOOL,
    GET_TEST_ITEMS_FOR_LAUNCH_TOOL,
    GET_LOGS_FOR_TEST_ITEM_TOOL,
    GET_USER_INFORMATION_TOOL,
    GET_DASHBOARD_DATA_TOOL,
    UPDATE_TEST_ITEM_TOOL,
)


class ReportPortalToolkitUI(ToolKit):
    """UI definition for Report Portal Toolkit."""

    toolkit: ToolSet = ToolSet.REPORT_PORTAL
    tools: List[Tool] = [
        Tool.from_metadata(GET_EXTENDED_LAUNCH_DATA_TOOL, tool_class=GetExtendedLaunchDataTool),
        Tool.from_metadata(GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL, tool_class=GetExtendedLaunchDataAsRawTool),
        Tool.from_metadata(GET_LAUNCH_DETAILS_TOOL, tool_class=GetLaunchDetailsTool),
        Tool.from_metadata(GET_ALL_LAUNCHES_TOOL, tool_class=GetAllLaunchesTool),
        Tool.from_metadata(FIND_TEST_ITEM_BY_ID_TOOL, tool_class=FindTestItemByIdTool),
        Tool.from_metadata(GET_TEST_ITEMS_FOR_LAUNCH_TOOL, tool_class=GetTestItemsForLaunchTool),
        Tool.from_metadata(GET_LOGS_FOR_TEST_ITEM_TOOL, tool_class=GetLogsForTestItemTool),
        Tool.from_metadata(GET_USER_INFORMATION_TOOL, tool_class=GetUserInformationTool),
        Tool.from_metadata(GET_DASHBOARD_DATA_TOOL, tool_class=GetDashboardDataTool),
        Tool.from_metadata(UPDATE_TEST_ITEM_TOOL, tool_class=UpdateTestItemTool),
    ]
    label: str = ToolSet.REPORT_PORTAL.value
    settings_config: bool = True


class ReportPortalToolkit(DiscoverableToolkit):
    """Toolkit for Report Portal integration."""

    @classmethod
    def get_definition(cls):
        """Return toolkit definition for UI autodiscovery."""
        return ReportPortalToolkitUI()
