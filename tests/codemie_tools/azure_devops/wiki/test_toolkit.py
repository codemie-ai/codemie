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
from codemie_tools.azure_devops.wiki.toolkit import AzureDevOpsWikiToolkit, AzureDevOpsWikiToolkitUI


class TestAzureDevOpsWikiToolkit:
    def test_get_definition(self):
        toolkit_ui = AzureDevOpsWikiToolkit.get_definition()
        assert isinstance(toolkit_ui, AzureDevOpsWikiToolkitUI)
        assert toolkit_ui.toolkit == ToolSet.AZURE_DEVOPS_WIKI
        assert len(toolkit_ui.tools) == 20
        assert toolkit_ui.label == ToolSet.AZURE_DEVOPS_WIKI.value


class TestAzureDevOpsWikiToolkitUI:
    def test_toolkit_property(self):
        toolkit_ui = AzureDevOpsWikiToolkitUI()
        assert toolkit_ui.toolkit == ToolSet.AZURE_DEVOPS_WIKI

    def test_tools_property(self):
        toolkit_ui = AzureDevOpsWikiToolkitUI()
        assert len(toolkit_ui.tools) == 20

        # Check that the tools are correctly defined
        tool_names = [tool.name for tool in toolkit_ui.tools]
        assert "get_wiki" in tool_names
        assert "list_wikis" in tool_names
        assert "list_pages" in tool_names
        assert "get_wiki_page_by_path" in tool_names
        assert "get_wiki_page_by_id" in tool_names
        assert "delete_page_by_path" in tool_names
        assert "delete_page_by_id" in tool_names
        assert "create_wiki_page" in tool_names
        assert "modify_wiki_page" in tool_names
        assert "rename_wiki_page" in tool_names
        assert "move_wiki_page" in tool_names
        assert "search_wiki_pages" in tool_names
        assert "get_wiki_page_comments_by_id" in tool_names
        assert "get_wiki_page_comments_by_path" in tool_names
        assert "add_attachment_to_wiki_page" in tool_names
        assert "get_wiki_page_stats_by_id" in tool_names
        assert "get_wiki_page_stats_by_path" in tool_names
        assert "add_wiki_comment_by_id" in tool_names
        assert "add_wiki_comment_by_path" in tool_names
        assert "get_wiki_attachment_content" in tool_names
