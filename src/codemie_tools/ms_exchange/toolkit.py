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
from codemie_tools.ms_exchange.tools import (
    DeleteTool,
    EnrichBodyTool,
    ForwardTool,
    MoveTool,
    ReadTool,
    RetrieveByIdTool,
    RetrieveFoldersTool,
    SendTool,
    TagTool,
)
from codemie_tools.ms_exchange.tools_vars import (
    EXCHANGE_DELETE_TOOL,
    EXCHANGE_ENRICH_BODY_TOOL,
    EXCHANGE_FORWARD_TOOL,
    EXCHANGE_MOVE_TOOL,
    EXCHANGE_READ_TOOL,
    EXCHANGE_RETRIEVE_BY_ID_TOOL,
    EXCHANGE_RETRIEVE_FOLDERS_TOOL,
    EXCHANGE_SEND_TOOL,
    EXCHANGE_TAG_TOOL,
)


class ExchangeToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.MS_EXCHANGE
    tools: list[Tool] = [
        Tool.from_metadata(EXCHANGE_RETRIEVE_FOLDERS_TOOL, tool_class=RetrieveFoldersTool),
        Tool.from_metadata(EXCHANGE_READ_TOOL, tool_class=ReadTool),
        Tool.from_metadata(EXCHANGE_RETRIEVE_BY_ID_TOOL, tool_class=RetrieveByIdTool),
        Tool.from_metadata(EXCHANGE_ENRICH_BODY_TOOL, tool_class=EnrichBodyTool),
        Tool.from_metadata(EXCHANGE_SEND_TOOL, tool_class=SendTool),
        Tool.from_metadata(EXCHANGE_FORWARD_TOOL, tool_class=ForwardTool),
        Tool.from_metadata(EXCHANGE_MOVE_TOOL, tool_class=MoveTool),
        Tool.from_metadata(EXCHANGE_TAG_TOOL, tool_class=TagTool),
        Tool.from_metadata(EXCHANGE_DELETE_TOOL, tool_class=DeleteTool),
    ]
    settings_config: bool = True
    label: str = ToolSet.MS_EXCHANGE.value


class ExchangeToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls) -> ExchangeToolkitUI:
        return ExchangeToolkitUI()
