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

from typing import List, Any
from operator import itemgetter
from langchain_core.output_parsers import StrOutputParser

from codemie.chains import BaseChain
from codemie.core.dependecies import get_llm_by_credentials
from codemie.templates.kb_sources_selector_prompt import KB_SOURCES_SELECTOR_PROMPT

NONE_RESPONSE = "None"


class KBSourcesSelectorChain(BaseChain):
    """
    This chain is responsible for selecting the best KB sources given a query and list of sources
    """

    def __init__(self, query: str, sources: List[str], llm_model: str, request_id: str):
        self.query = query
        self.sources = sources
        self.llm_model = llm_model
        self.request_id = request_id

    def generate(self) -> Any:
        result = self._chain().invoke(
            input={
                "question": self.query,
                "sources": self.sources,
            }
        )

        if result == NONE_RESPONSE:
            return []

        return set(map(str.strip, result.split(",")))

    def _chain(self):
        llm = get_llm_by_credentials(self.llm_model, streaming=False, request_id=self.request_id)

        return (
            {"question": itemgetter("question"), "sources": itemgetter("sources")}
            | KB_SOURCES_SELECTOR_PROMPT
            | llm
            | StrOutputParser()
        )
