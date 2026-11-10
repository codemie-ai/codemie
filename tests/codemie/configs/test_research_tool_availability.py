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


import itertools
from types import SimpleNamespace

import pytest

from codemie.configs.research_tool_availability import research_tool_available
from codemie_tools.research.tools_vars import (
    GOOGLE_PLACES_FIND_NEAR_TOOL,
    GOOGLE_PLACES_TOOL,
    GOOGLE_SEARCH_RESULTS_TOOL,
    TAVILY_SEARCH_TOOL,
    WEB_SCRAPPER_TOOL,
    WIKIPEDIA_TOOL,
)

GOOGLE = [GOOGLE_SEARCH_RESULTS_TOOL.name, GOOGLE_PLACES_TOOL.name, GOOGLE_PLACES_FIND_NEAR_TOOL.name]


@pytest.mark.parametrize("standalone,flag,key", list(itertools.product([True, False], [True, False], ["", "k", " "])))
def test_matrix(standalone, flag, key):
    cfg = SimpleNamespace(STANDALONE=standalone, GOOGLE_SEARCH_API_KEY=key, TAVILY_API_KEY=key)
    for name in GOOGLE + [TAVILY_SEARCH_TOOL.name]:
        assert research_tool_available(name, cfg, flag) is (flag and bool(key) if standalone else True)
    for name in (WIKIPEDIA_TOOL.name, WEB_SCRAPPER_TOOL.name):
        assert research_tool_available(name, cfg, flag) is True
