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
from codemie_tools.data_management.elastic.tools import SearchElasticIndex
from codemie_tools.data_management.elastic.tools_vars import SEARCH_ES_INDEX_TOOL
from codemie_tools.data_management.sql.tools import SQLTool
from codemie_tools.data_management.sql.tools_vars import SQL_TOOL


class DataManagementToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.DATA_MANAGEMENT
    tools: List[Tool] = [
        Tool.from_metadata(SEARCH_ES_INDEX_TOOL, tool_class=SearchElasticIndex),
        Tool.from_metadata(SQL_TOOL, tool_class=SQLTool),
    ]
    label: str = ToolSet.DATA_MANAGEMENT.value


class DataManagementToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls):
        return DataManagementToolkitUI()
