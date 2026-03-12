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

import json
from typing import Any, Type, Optional

from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.elastic.elastic_wrapper import SearchElasticIndexResults
from codemie_tools.data_management.elastic.models import ElasticConfig, SearchElasticIndexInput
from codemie_tools.data_management.elastic.tools_vars import SEARCH_ES_INDEX_TOOL


class SearchElasticIndex(CodeMieTool):
    config: Optional[ElasticConfig] = Field(exclude=True, default=None)
    name: str = SEARCH_ES_INDEX_TOOL.name
    description: str = SEARCH_ES_INDEX_TOOL.description
    args_schema: Type[BaseModel] = SearchElasticIndexInput

    def execute(self, index: str, query: str, **kwargs: Any) -> Any:
        if not self.config:
            raise ValueError("Elastic configuration is not provided")
        mapping = json.loads(query)
        response = SearchElasticIndexResults.search(index=index, query=mapping, elastic_config=self.config)
        return response
