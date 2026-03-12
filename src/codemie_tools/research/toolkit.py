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

import logging
from typing import List, Dict, Any, Optional

from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.utilities.tavily_search import TavilySearchAPIWrapper
from langchain_community.utilities.wikipedia import WikipediaAPIWrapper
from langchain_google_community import GoogleSearchAPIWrapper
from pydantic import BaseModel

from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.models import ToolKit, ToolSet, Tool
from codemie_tools.research.google_places_wrapper import GooglePlacesAPIWrapper
from codemie_tools.research.tools import (
    WebScrapperTool,
    WikipediaQueryRun,
    GoogleSearchResults,
    GooglePlacesTool,
    GooglePlacesFindNearTool,
)
from codemie_tools.research.tools_vars import (
    GOOGLE_SEARCH_RESULTS_TOOL,
    WIKIPEDIA_TOOL,
    WEB_SCRAPPER_TOOL,
    GOOGLE_PLACES_TOOL,
    GOOGLE_PLACES_FIND_NEAR_TOOL,
    TAVILY_SEARCH_TOOL,
)

logger = logging.getLogger(__name__)


class ResearchConfig(BaseModel):
    google_search_api_key: Optional[str] = None
    google_search_cde_id: Optional[str] = None
    tavily_search_key: Optional[str] = None


class ResearchToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.RESEARCH
    tools: List[Tool] = [
        Tool.from_metadata(GOOGLE_SEARCH_RESULTS_TOOL),
        Tool.from_metadata(GOOGLE_PLACES_TOOL),
        Tool.from_metadata(GOOGLE_PLACES_FIND_NEAR_TOOL),
        Tool.from_metadata(WIKIPEDIA_TOOL),
        Tool.from_metadata(TAVILY_SEARCH_TOOL),
        Tool.from_metadata(WEB_SCRAPPER_TOOL),
    ]


class ResearchToolkit(BaseToolkit):
    research_config: ResearchConfig

    @classmethod
    def get_toolkit(cls, configs: Dict[str, Any]):
        research_config = ResearchConfig(**configs)
        return cls(research_config=research_config)

    @classmethod
    def get_tools_ui_info(cls, *args, **kwargs):
        return ResearchToolkitUI().model_dump()

    def get_tools(self):
        tools = [self.get_wikipedia_tool(), self.get_webscrapper_tool()]
        if self.research_config.tavily_search_key:
            tools.append(self.get_tavily_tool())
        if self.research_config.google_search_api_key:
            tools.append(self.google_search_tool())
            tools.append(self.google_places_tool())
            tools.append(self.google_places_find_near_tool())
        return tools

    def google_search_tool(self):
        api_wrapper = GoogleSearchAPIWrapper(
            google_api_key=self.research_config.google_search_api_key,
            google_cse_id=self.research_config.google_search_cde_id,
        )
        return GoogleSearchResults(handle_validation_error=True, api_wrapper=api_wrapper)

    def google_places_tool(self):
        api_wrapper = GooglePlacesAPIWrapper(
            gplaces_api_key=self.research_config.google_search_api_key,
        )
        return GooglePlacesTool(handle_validation_error=True, api_wrapper=api_wrapper)

    def google_places_find_near_tool(self):
        api_wrapper = GooglePlacesAPIWrapper(
            gplaces_api_key=self.research_config.google_search_api_key,
        )
        return GooglePlacesFindNearTool(handle_validation_error=True, api_wrapper=api_wrapper)

    def get_wikipedia_tool(self):
        return WikipediaQueryRun(handle_validation_error=True, api_wrapper=WikipediaAPIWrapper())

    def get_webscrapper_tool(self):
        return WebScrapperTool(
            handle_validation_error=True,
        )

    def get_tavily_tool(self):
        api_wrapper = TavilySearchAPIWrapper(tavily_api_key=self.research_config.tavily_search_key)
        return TavilySearchResults(name=TAVILY_SEARCH_TOOL.name, handle_validation_error=True, api_wrapper=api_wrapper)
