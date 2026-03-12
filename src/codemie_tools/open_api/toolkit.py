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
from codemie_tools.base.models import Tool, ToolKit, ToolSet
from codemie_tools.open_api.tools import InvokeRestApiBySpec, GetOpenApiSpec
from codemie_tools.open_api.tools_vars import OPEN_API_TOOL, OPEN_API_SPEC_TOOL


class OpenApiToolkitUI(ToolKit):
    """UI representation of the OpenAPI toolkit."""

    toolkit: ToolSet = ToolSet.OPEN_API
    tools: List[Tool] = [
        Tool.from_metadata(OPEN_API_TOOL, tool_class=InvokeRestApiBySpec),
        Tool.from_metadata(OPEN_API_SPEC_TOOL, tool_class=GetOpenApiSpec),
    ]
    settings_config: bool = True


class OpenApiToolkit(DiscoverableToolkit):
    """Toolkit for OpenAPI tools."""

    @classmethod
    def get_definition(cls):
        """Return the toolkit definition."""
        return OpenApiToolkitUI()
