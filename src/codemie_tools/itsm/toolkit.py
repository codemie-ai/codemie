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
from .servicenow.tools import ServiceNowTableTool
from .servicenow.tools_vars import SNOW_TABLE_TOOL


class ITSMToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.ITSM
    tools: List[Tool] = [Tool.from_metadata(SNOW_TABLE_TOOL, tool_class=ServiceNowTableTool)]
    label: str = ToolSet.ITSM.value


class ITSMToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls):
        return ITSMToolkitUI()
