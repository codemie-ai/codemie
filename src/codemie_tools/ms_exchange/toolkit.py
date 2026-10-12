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

from __future__ import annotations

from codemie_tools.base.base_toolkit import DiscoverableToolkit
from codemie_tools.base.models import Tool, ToolKit, ToolSet
from codemie_tools.ms_exchange.tools import EnrichBodyTool, ReadTool, RetrieveByIdTool, RetrieveFoldersTool
from codemie_tools.ms_exchange.tools_vars import (
    EXCHANGE_ENRICH_BODY_TOOL,
    EXCHANGE_READ_TOOL,
    EXCHANGE_RETRIEVE_BY_ID_TOOL,
    EXCHANGE_RETRIEVE_FOLDERS_TOOL,
)


class ExchangeToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.MS_EXCHANGE
    tools: list[Tool] = [
        Tool.from_metadata(EXCHANGE_RETRIEVE_FOLDERS_TOOL, tool_class=RetrieveFoldersTool),
        Tool.from_metadata(EXCHANGE_READ_TOOL, tool_class=ReadTool),
        Tool.from_metadata(EXCHANGE_RETRIEVE_BY_ID_TOOL, tool_class=RetrieveByIdTool),
        Tool.from_metadata(EXCHANGE_ENRICH_BODY_TOOL, tool_class=EnrichBodyTool),
    ]
    settings_config: bool = True
    label: str = ToolSet.MS_EXCHANGE.value


class ExchangeToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls) -> ExchangeToolkitUI:
        return ExchangeToolkitUI()
