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

import pytest

from codemie_tools.research.toolkit import ResearchToolkit
from codemie_tools.research.tools import WebScrapperTool, WikipediaQueryRun, GoogleSearchResults


class TestResearchToolkit:
    @pytest.fixture
    def research_config_dict(self):
        return {
            'google_search_api_key': 'test_google_search_api_key',
            'google_search_cde_id': 'test_google_search_cde_id',
            'tavily_search_key': 'еуые_tavily_search_key',
        }

    @pytest.fixture
    def toolkit(self, research_config_dict):
        return ResearchToolkit.get_toolkit(configs=research_config_dict)

    def test_initialization(self, research_config_dict, toolkit):
        assert toolkit.research_config.google_search_api_key == research_config_dict['google_search_api_key']
        assert toolkit.research_config.google_search_cde_id == research_config_dict['google_search_cde_id']

    def test_get_tools_ui_info(self, toolkit):
        ui_info = toolkit.get_tools_ui_info()
        assert 'tools' in ui_info, "UI info does not contain 'tools'"
        assert len(ui_info['tools']) == 6, "Incorrect number of tools in UI info"

    def test_get_tools(self):
        toolkit = ResearchToolkit.get_toolkit(configs={'tavily_api_key': 'test_tavily_api_key'})
        tools = toolkit.get_tools()
        assert len(tools) == 2, "Incorrect number of tools returned"
        assert any(isinstance(tool, WikipediaQueryRun) for tool in tools), "WikipediaQueryRun tool missing"
        assert any(isinstance(tool, WebScrapperTool) for tool in tools), "WebScrapperTool missing"

    def test_google_search_tool(self, toolkit):
        tool = toolkit.google_search_tool()
        assert isinstance(tool, GoogleSearchResults), "google_search_tool did not return GoogleSearchResults"

    def test_get_wikipedia_tool(self, toolkit):
        tool = toolkit.get_wikipedia_tool()
        assert isinstance(tool, WikipediaQueryRun), "get_wikipedia_tool did not return WikipediaQueryRun"

    def test_get_webscrapper_tool(self, toolkit):
        tool = toolkit.get_webscrapper_tool()
        assert isinstance(tool, WebScrapperTool), "get_webscrapper_tool did not return WebScrapperTool"
