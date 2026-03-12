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
from langchain_core.tools import BaseTool
from codemie_tools.base.models import ToolKit, Tool, ToolSet

from codemie.agents.tools.kb.search_kb import SearchKBTool, SEARCH_KB_TOOL
from codemie.agents.tools.base import BaseToolkit

from codemie.rest_api.models.index import IndexInfo


class KBToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.KB_TOOLS
    tools: List[Tool] = [
        Tool.from_metadata(SEARCH_KB_TOOL),
    ]


class KBToolkit(BaseToolkit):
    @classmethod
    def get_tools_ui_info(cls, *args, **kwargs):
        # Default implementation
        return KBToolkitUI().model_dump()

    @classmethod
    def get_tools(cls, kb_index: IndexInfo, llm_model: str) -> List[BaseTool]:
        search_tool = SearchKBTool(
            kb_index=kb_index,
            llm_model=llm_model,
        )

        return [search_tool]
