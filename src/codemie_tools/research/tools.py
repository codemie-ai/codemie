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

import re
from typing import Type, Optional

import requests
from bs4 import BeautifulSoup
from langchain_community.utilities import WikipediaAPIWrapper
from langchain_google_community import GoogleSearchAPIWrapper
from markdownify import markdownify as md
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.research.google_places_wrapper import GooglePlacesAPIWrapper
from codemie_tools.research.tools_vars import (
    WEB_SCRAPPER_TOOL,
    GOOGLE_SEARCH_RESULTS_TOOL,
    WIKIPEDIA_TOOL,
    GOOGLE_PLACES_TOOL,
    GOOGLE_PLACES_FIND_NEAR_TOOL,
)


class WebScrapperToolInput(BaseModel):
    url: str = Field(description="URL or resource to scrape information from.")
    extract_images: bool = Field(
        default=False, description="Whether to extract image references and include them in the markdown output."
    )
    extract_links: bool = Field(default=True, description="Whether to preserve links in the markdown output.")


class WebScrapperTool(CodeMieTool):
    tokens_size_limit: int = 10000
    name: str = WEB_SCRAPPER_TOOL.name
    description: str = WEB_SCRAPPER_TOOL.description
    args_schema: Type[BaseModel] = WebScrapperToolInput

    def execute(self, url: str, extract_images: bool = False, extract_links: bool = True) -> str:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:52.0) Gecko/20100101 Firefox/52.0'}
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()  # Raise an exception for bad status codes

            # Parse the HTML content
            soup = BeautifulSoup(response.text, "html.parser")

            # Remove script, style, and other unwanted elements
            for tag in soup(["script", "style", "meta", "noscript", "iframe"]):
                tag.extract()

            # Get page title and create a header
            title = soup.title.string if soup.title else "Web Page Content"
            result = f"# {title.strip()}\n\n" if title else "# Web Page Content\n\n"

            # Add the page URL for reference
            result += f"*Source: {url}*\n\n"

            # Configure markdownify options
            md_options = {
                "strip": ["script", "style"],
                "heading_style": "atx",
                "bullets": "*",
                "autolinks": extract_links,
            }

            # Convert HTML to markdown
            markdown_content = md(str(soup), **md_options)

            # Clean up the markdown content
            markdown_content = self._clean_markdown(markdown_content)

            # Extract and include images if requested
            if extract_images:
                markdown_content = self._include_images(soup, markdown_content, url)

            result += markdown_content

            # Truncate if it exceeds the token limit (rough estimate)
            if len(result) > self.tokens_size_limit * 4:  # Characters to tokens approximation
                result = result[: self.tokens_size_limit * 4] + "\n\n*Content truncated due to length limits.*"

            return result

        except Exception as e:
            return f"Error scraping {url}: {str(e)}"

    def _clean_markdown(self, content: str) -> str:
        """Clean up the markdown content for better readability."""
        # Remove excessive newlines
        content = re.sub(r'\n{3,}', '\n\n', content)

        # Fix spacing around headers
        content = re.sub(r'(\n#{1,6} [^\n]+)\n(?!\n)', r'\1\n\n', content)

        # Ensure proper spacing around lists
        content = re.sub(r'\n(\* [^\n]+)\n(?!\n|\* )', r'\n\1\n\n', content)

        return content.strip()

    def _include_images(self, soup: BeautifulSoup, content: str, base_url: str) -> str:
        """Extract and include image references in the markdown content."""
        images = soup.find_all('img')
        if not images:
            return content

        img_section = "\n\n## Images\n\n"
        added_images = 0

        for img in images:
            if added_images >= 10:  # Limit to 10 images to avoid excessive content
                break

            src = img.get('src', '')
            alt = img.get('alt', 'Image')

            if not src:
                continue

            # Convert relative URLs to absolute
            if not src.startswith(('http://', 'https://')):
                if src.startswith('/'):
                    # Get the base domain from the URL
                    from urllib.parse import urlparse

                    parsed_url = urlparse(base_url)
                    domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
                    src = domain + src
                else:
                    # Handle relative paths without leading slash
                    from urllib.parse import urljoin

                    src = urljoin(base_url, src)

            img_section += f"![{alt}]({src})\n\n"
            added_images += 1

        if added_images > 0:
            content += img_section

        return content


class GoogleSearchResultsInput(BaseModel):
    query: str = Field(description="Query to look up in Google.")


class GooglePlacesSchema(BaseModel):
    query: str = Field(description="Query for google maps")


class GooglePlacesFindNearSchema(BaseModel):
    current_location_query: str = Field(
        description="Detailed user query of current user location or where to start from"
    )
    target: str = Field(description="The target location or query which user wants to find")
    radius: Optional[int] = Field(description="The radius of the search. This is optional field")


class GoogleSearchResults(CodeMieTool):
    name: str = GOOGLE_SEARCH_RESULTS_TOOL.name
    description: str = GOOGLE_SEARCH_RESULTS_TOOL.description
    num_results: int = 10
    api_wrapper: GoogleSearchAPIWrapper
    args_schema: Type[BaseModel] = GoogleSearchResultsInput

    def execute(self, query: str):
        return str(self.api_wrapper.results(query, self.num_results))


class GooglePlacesTool(CodeMieTool):
    name: str = GOOGLE_PLACES_TOOL.name
    description: str = GOOGLE_PLACES_TOOL.description
    api_wrapper: GooglePlacesAPIWrapper
    args_schema: Type[BaseModel] = GooglePlacesSchema

    def execute(self, query: str) -> str:
        return self.api_wrapper.places(query)


class GooglePlacesFindNearTool(CodeMieTool):
    name: str = GOOGLE_PLACES_FIND_NEAR_TOOL.name
    description: str = GOOGLE_PLACES_FIND_NEAR_TOOL.description
    api_wrapper: GooglePlacesAPIWrapper
    args_schema: Type[BaseModel] = GooglePlacesFindNearSchema
    default_radius: int = 10000

    def execute(self, current_location_query: str, target: str, radius: Optional[int] = default_radius) -> str:
        return self.api_wrapper.find_near(current_location_query=current_location_query, target=target, radius=radius)


class WikipediaQueryInput(BaseModel):
    query: str = Field(description="Query to look up on Wikipedia.")


class WikipediaQueryRun(CodeMieTool):
    name: str = WIKIPEDIA_TOOL.name
    description: str = WIKIPEDIA_TOOL.description
    api_wrapper: WikipediaAPIWrapper
    args_schema: Type[BaseModel] = WikipediaQueryInput

    def execute(self, query: str):
        return self.api_wrapper.run(query)
