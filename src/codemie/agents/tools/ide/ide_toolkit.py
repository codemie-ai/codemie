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

from pydantic import BaseModel

from langchain_core.tools import BaseTool
from codemie.agents.tools.base import BaseToolkit
from codemie.agents.tools.ide.ide_tool import IdeTool
from codemie.core.models import IdeToolDefinition


class IdeToolkit(BaseModel, BaseToolkit):
    request_id: str
    tool_definitions: List[IdeToolDefinition]

    def get_tools_ui_info(self, *args, **kwargs):
        raise NotImplementedError("get_tools_ui_info() for IdeToolkit should never be called")

    def get_tools(self) -> List[BaseTool]:
        return [IdeTool(definition=df, request_id=self.request_id) for df in self.tool_definitions]
