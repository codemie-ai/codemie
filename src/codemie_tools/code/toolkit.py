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
from codemie_tools.code.sonar.tools import SonarTool
from codemie_tools.code.sonar.tools_vars import SONAR_TOOL


class CodeToolkitUI(ToolKit):
    """UI definition for Code toolkit - ONLY Sonar tool with credentials.

    Other code tools (search, tree, read) are added separately on the backend
    via context and do not require credentials.
    """

    toolkit: ToolSet = ToolSet.CODEBASE_TOOLS
    tools: List[Tool] = [Tool.from_metadata(SONAR_TOOL, tool_class=SonarTool)]
    label: str = ToolSet.CODEBASE_TOOLS.value


class CodeToolkit(DiscoverableToolkit):
    """Discoverable toolkit for code-related tools requiring credentials.

    Currently contains only Sonar tool. Other code tools (search, tree, read)
    are added separately on the backend via context.
    """

    @classmethod
    def get_definition(cls):
        return CodeToolkitUI()
