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

from codemie_tools.base.models import ToolKit, ToolSet, Tool
from .azure_devops_git.tools import AzureDevOpsGitTool
from .azure_devops_git.tools_vars import AZURE_DEVOPS_GIT_TOOL
from .github.tools import GithubTool
from .github.tools_vars import GITHUB_TOOL
from .gitlab.tools import GitlabTool
from .gitlab.tools_vars import GITLAB_TOOL
from ...base.base_toolkit import DiscoverableToolkit


class VcsToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.VCS
    tools: List[Tool] = [
        Tool.from_metadata(AZURE_DEVOPS_GIT_TOOL, tool_class=AzureDevOpsGitTool),
        Tool.from_metadata(GITHUB_TOOL, tool_class=GithubTool),
        Tool.from_metadata(GITLAB_TOOL, tool_class=GitlabTool),
    ]


class VcsToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls):
        return VcsToolkitUI()
