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

from codemie_tools.base.models import ToolMetadata

GOOGLE_SEARCH_RESULTS_TOOL = ToolMetadata(
    name="google_search_tool_json",
    label="Google Search",
    description="""
    A wrapper around Google Search.
    Useful for when you need to answer questions in real time, google information or browse the internet for additional details.
    Input should be a search query. Output is a JSON array of the query results.
    """.strip(),
    user_description="""
    A wrapper around Google Search.
    Useful for when you need to answer questions in real time, google information or browse the internet for additional details.
    """.strip(),
)

GOOGLE_PLACES_TOOL = ToolMetadata(
    name="google_places",
    label="Google Places",
    description="""
    A wrapper around Google Places.
    Useful for when you need to validate or discover addressed from ambiguous text.
    Input should be a search query.
    """.strip(),
    user_description="""
    A wrapper around Google Places.
    Useful for when you need to validate or discover addressed from ambiguous text.
    """.strip(),
)

GOOGLE_PLACES_FIND_NEAR_TOOL = ToolMetadata(
    name="google_places_find_near",
    label="Google Places Find Near",
    description="""
    A wrapper around Google Places API, especially for finding places near a location.
    Useful for when you need to validate or discover addressed from ambiguous text.
    Input schema is the following:
    - current_location_query: detailed user query of current user location or where to start from;
    - target: the target location or query which user wants to find;
    - radius: the radius of the search. This is optional field;
    """.strip(),
    user_description="""
    A wrapper around Google Places API, especially for finding places near a location.
    Useful for when you need to validate or discover addressed from ambiguous text.
    """.strip(),
)

TAVILY_SEARCH_TOOL = ToolMetadata(
    name="tavily_search_results_json",
    label="Tavily Search",
    description="""A web search engine optimized for comprehensive, accurate, and trusted results.
    Useful for when you need to answer questions about current events.
    Input should be a search query.
    """.strip(),
    user_description="""A web search engine optimized for comprehensive, accurate, and trusted results.
    Useful for when you need to answer questions about current events.
    """.strip(),
)


WIKIPEDIA_TOOL = ToolMetadata(
    name="wikipedia",
    label="Wikipedia",
    description="""
    A wrapper around Wikipedia.
    Useful for when you need to answer general questions about people, places, companies, facts, historical events, or other subjects.
    Input should be a search query.
    """.strip(),
    user_description="""
    A wrapper around Wikipedia.
    Useful for when you need to answer general questions about people, places, companies, facts, historical events, or other subjects.
    """.strip(),
)

WEB_SCRAPPER_TOOL = ToolMetadata(
    name="web_scrapper",
    label="Web Scraper",
    description="""
    A tool to scrape the web and convert HTML content to markdown format. Input should be a URL and optionally parameters for extracting images and preserving links. The output will be well-formatted markdown content from the website.
    """.strip(),
    user_description="""
    Extracts and formats content from a specified web page as markdown.
    Use this tool when you need to gather information from a website that doesn't offer an API.
    Retains formatting, headers, lists, and optionally links and images.
    """.strip(),
)

PYTHON_WEB_SCRAPPER_TOOL = ToolMetadata(
    name="advanced_web_scrapper",
    description="",
)
