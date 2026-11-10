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


"""Whether a Research web tool is usable (standalone needs its API key and the webSearch flag)."""

from codemie_tools.research.tools_vars import (
    GOOGLE_PLACES_FIND_NEAR_TOOL,
    GOOGLE_PLACES_TOOL,
    GOOGLE_SEARCH_RESULTS_TOOL,
    TAVILY_SEARCH_TOOL,
)

_GOOGLE_TOOLS = {GOOGLE_SEARCH_RESULTS_TOOL.name, GOOGLE_PLACES_TOOL.name, GOOGLE_PLACES_FIND_NEAR_TOOL.name}


def research_tool_available(tool_name: str, cfg, web_search_enabled: bool) -> bool:
    """Outside standalone every tool is available; in standalone key-backed tools need the webSearch flag and a key.
    Wikipedia and Web Scrapper need neither."""
    if not cfg.STANDALONE:
        return True
    if tool_name in _GOOGLE_TOOLS:
        return web_search_enabled and bool(cfg.GOOGLE_SEARCH_API_KEY)
    if tool_name == TAVILY_SEARCH_TOOL.name:
        return web_search_enabled and bool(cfg.TAVILY_API_KEY)
    return True
