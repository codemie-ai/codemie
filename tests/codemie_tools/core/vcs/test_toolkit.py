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

from codemie_tools.base.models import ToolSet
from codemie_tools.core.vcs.azure_devops_git.tools_vars import AZURE_DEVOPS_GIT_TOOL
from codemie_tools.core.vcs.github.tools_vars import GITHUB_TOOL
from codemie_tools.core.vcs.gitlab.tools_vars import GITLAB_TOOL
from codemie_tools.core.vcs.toolkit import VcsToolkit, VcsToolkitUI


class TestVcsToolkit:
    """Tests for VcsToolkit class."""

    def test_get_definition(self):
        """Test get_definition method."""
        definition = VcsToolkit.get_definition()

        assert isinstance(definition, VcsToolkitUI)
        assert definition.toolkit == ToolSet.VCS

        # Check that the tools list contains the expected tools
        tool_names = [tool.name for tool in definition.tools]
        assert GITHUB_TOOL.name in tool_names
        assert GITLAB_TOOL.name in tool_names
        assert AZURE_DEVOPS_GIT_TOOL.name in tool_names


class TestVcsToolkitUI:
    """Tests for VcsToolkitUI class."""

    def test_toolkit_property(self):
        """Test toolkit property."""
        toolkit_ui = VcsToolkitUI()
        assert toolkit_ui.toolkit == ToolSet.VCS

    def test_tools_property(self):
        """Test tools property."""
        toolkit_ui = VcsToolkitUI()

        assert isinstance(toolkit_ui.tools, list)
        assert len(toolkit_ui.tools) == 3  # GitHub, GitLab, and Azure DevOps Git tools

        # Check that the tools list contains the expected tools
        tool_names = [tool.name for tool in toolkit_ui.tools]
        assert GITHUB_TOOL.name in tool_names
        assert GITLAB_TOOL.name in tool_names
        assert AZURE_DEVOPS_GIT_TOOL.name in tool_names

        # Check that the tools have the correct class
        for tool in toolkit_ui.tools:
            if tool.name == GITHUB_TOOL.name:
                assert tool.tool_class.__name__ == "GithubTool"
            elif tool.name == GITLAB_TOOL.name:
                assert tool.tool_class.__name__ == "GitlabTool"
            elif tool.name == AZURE_DEVOPS_GIT_TOOL.name:
                assert tool.tool_class.__name__ == "AzureDevOpsGitTool"
