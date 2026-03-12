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

from typing import List, Optional, Type
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool

from codemie.agents.tools.code.tools_models import (
    SearchInput,
    FilteredDocuments,
    GetRepoTreeInput,
    GetRepoTreeInputV2,
    SearchInputByPaths,
)
from codemie.agents.tools.code.base_tools import (
    BaseCodeToolMixin,
    CodeRepoBaseToolMixin,
    SearchCodeRepoBaseToolMixin,
)
from codemie.agents.tools.code.tools_vars import (
    REPO_TREE_TOOL,
    CODE_SEARCH_TOOL,
    CODE_SEARCH_BY_PATHS_TOOL,
    REPO_TREE_TOOL_V2,
)
from codemie.agents.utils import get_repo_tree, get_repo_tree_by_search_phrase_path
from codemie.configs import logger
from codemie.core.constants import REQUEST_ID, TOOL_TYPE, ToolType
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.models import CodeFields
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.search_and_rerank import SearchAndRerankCode
from codemie.templates.coding_prompts import REPO_TREE_FILTER_RELEVANCE_PROMPT


class GetRepoFileTreeTool(CodeMieTool, BaseCodeToolMixin):
    base_name: str = REPO_TREE_TOOL.name
    name: str = REPO_TREE_TOOL.name
    description: str = REPO_TREE_TOOL.description
    args_schema: Optional[Type[BaseModel]] = GetRepoTreeInput
    tokens_size_limit: int = 20000

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        if self.metadata:
            self.metadata[TOOL_TYPE] = ToolType.PLUGIN
        else:
            self.metadata = {TOOL_TYPE: ToolType.PLUGIN}

    def execute(self, *args, **kwargs):
        return get_repo_tree(code_fields=self.code_fields)


class GetRepoFileTreeToolV2(CodeMieTool, CodeRepoBaseToolMixin):
    code_fields: CodeFields = Field(exclude=True)
    base_name: str = REPO_TREE_TOOL_V2.name
    name: str = REPO_TREE_TOOL_V2.name
    description: str = REPO_TREE_TOOL_V2.description
    args_schema: Type[BaseModel] = GetRepoTreeInputV2
    tokens_size_limit: int = 20000
    max_tokens_per_batch: int = 50000
    user_input: Optional[str] = Field(default="", exclude=True)

    def execute(self, query: str, file_path: Optional[str] = None):
        if file_path:
            repo_tree = get_repo_tree_by_search_phrase_path(code_fields=self.code_fields, file_path=file_path)
            if not repo_tree:
                repo_tree = get_repo_tree(code_fields=self.code_fields)
        else:
            repo_tree = get_repo_tree(code_fields=self.code_fields)
        tokens_count = self.calculate_tokens_count(repo_tree)
        if tokens_count > self.tokens_size_limit:
            logger.info(f"Applying filtering by relevance: {self.with_filtering}, tokens_count: {tokens_count}")
            return self.filter_tree_by_relevance(query, repo_tree)
        return repo_tree

    def filter_tree_by_relevance(self, query: str, sources: List[str]):
        try:
            task = f"Initial user input: {self.user_input}, \n Rephrased query: {query}"
            llm_model = self.metadata.get("llm_model", llm_service.default_llm_model)
            request_id = self.metadata.get(REQUEST_ID, "")
            llm = get_llm_by_credentials(llm_model=llm_model, request_id=request_id)
            filter_chain = REPO_TREE_FILTER_RELEVANCE_PROMPT | llm.with_structured_output(FilteredDocuments)
            batches = self._create_batches(
                sources, max_tokens=self.max_tokens_per_batch, calculate_tokens_count=self.calculate_tokens_count
            )
            final_filtered_documents = []
            for batch in batches:
                logger.debug(f"Initial count: {len(batch)}")
                filtered_sources = filter_chain.invoke({"sources": str(batch), "query": task})
                logger.debug(f"Filtered count: {len(filtered_sources.sources)}, sources: {filtered_sources}")
                final_filtered_documents.extend(filtered_sources.sources)

            logger.debug(f"Filtered sources size: {len(final_filtered_documents)}, list: {final_filtered_documents}")
            return final_filtered_documents
        except Exception as e:
            logger.error(f"Error filtering documents by relevance: {str(e)}, for query: {query}")
            return sources


class SearchCodeRepoTool(CodeMieTool, SearchCodeRepoBaseToolMixin):
    base_name: str = CODE_SEARCH_TOOL.name
    name: str = CODE_SEARCH_TOOL.name
    description: str = CODE_SEARCH_TOOL.description
    handle_tool_error: bool = True
    args_schema: Type[BaseModel] = SearchInput

    def execute(
        self, query: str, keywords_list: Optional[List[str]] = None, file_path: Optional[List[str]] = None, *args
    ):
        keywords_list = keywords_list or []
        file_path = file_path or []
        search_results = SearchAndRerankCode(
            query=query,
            keywords_list=keywords_list,
            file_path=file_path,
            code_fields=self.code_fields,
            top_k=self.top_k,
        ).execute()
        if self.with_filtering:
            request_id = self.metadata.get(REQUEST_ID, "")
            llm_model = self.metadata.get("llm_model", llm_service.default_llm_model)
            search_results = self._filter_documents_by_relevance(
                query,
                search_results,
                request_id=request_id,
                llm_model=llm_model,
                calculate_tokens_count=self.calculate_tokens_count,
            )
        return self._filter_and_format_documents(search_results, calculate_tokens_count=self.calculate_tokens_count)


class SearchCodeRepoByPathsTool(CodeMieTool, SearchCodeRepoBaseToolMixin):
    base_name: str = CODE_SEARCH_BY_PATHS_TOOL.name
    name: str = CODE_SEARCH_BY_PATHS_TOOL.name
    description: str = CODE_SEARCH_BY_PATHS_TOOL.description
    handle_tool_error: bool = True
    args_schema: Type[BaseModel] = SearchInputByPaths

    def execute(
        self,
        query: str,
        file_path: List[str],
        keywords_list: Optional[List[str]] = None,
        limit_docs_count: Optional[int] = None,
        *args,
    ):
        file_path = file_path or []
        search_results = SearchAndRerankCode(
            query=query,
            keywords_list=keywords_list,
            file_path=file_path,
            code_fields=self.code_fields,
            top_k=self.top_k,
            use_knn_search=False,
        ).execute()

        request_id = self.metadata.get(REQUEST_ID, "")
        llm_model = self.metadata.get("llm_model", llm_service.default_llm_model)
        documents = self._filter_documents_by_relevance(
            query,
            search_results,
            self.calculate_tokens_count,
            request_id,
            llm_model,
            keywords_list,
            limit_docs_count,
        )
        return self._filter_and_format_documents(documents, calculate_tokens_count=self.calculate_tokens_count)
