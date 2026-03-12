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

from typing import Any, List, Tuple

from langchain_core.documents import Document

from codemie.configs import logger
from codemie.core.dependecies import get_indexed_repo, get_embeddings_model, get_repo_from_fields
from codemie.core.models import CodeFields
from codemie.service.llm_service.llm_service import llm_service
from codemie.enterprise.loader import observe
from codemie.service.search_and_rerank.base import SearchAndRerankBase, es_response_to_document
from codemie.service.search_and_rerank.rrf import RRF

DocumentSearchResult = Tuple[Document, float, int]


class SearchAndRerankCode(SearchAndRerankBase):
    """
    A class that performs combined vector and text search operations on code repositories
    with subsequent result reranking.

    This class uses both k-nearest neighbors (KNN) vector search and text-based search
    to find relevant code snippets, then combines and reranks the results using
    Reciprocal Rank Fusion (RRF).
    """

    # Class-level constants
    KNN_MULTIPLIER: int = 3
    MAX_CANDIDATES: int = 10_000
    TEXT_SEARCH_SIZE: int = 100

    def __init__(
        self,
        query: str,
        keywords_list: List[str],
        file_path: List[str],
        code_fields: CodeFields,
        top_k: int,
        use_knn_search: bool = True,
    ):
        """
        Initialize the search and rerank operation.

        Args:
            query (str): The query string used for the search.
            keywords_list (List[str]): A list of keywords for exact matching.
            file_path (List[str]): A list of file paths for exact matching.
            code_fields (CodeFields): Object with information on the currently chosen index.
            top_k (int): The number of top documents to return after reranking.

        Raises:
            ValueError: If query is empty or top_k is less than 1.
        """
        if not query.strip():
            raise ValueError("Query string cannot be empty")
        if top_k < 1:
            raise ValueError("top_k must be greater than 0")

        self.query = query
        self.keywords_list = keywords_list or []
        self.file_path = file_path or []
        self.code_fields = code_fields
        self.top_k = top_k
        self.use_knn_search = use_knn_search
        self.index_name = self._get_index_name()

    def execute(self):
        """
        Execute the search and rerank operation.

        Returns:
            List[Document]: A list of ranked documents matching the search criteria.

        """
        search_results = self._knn_vector_search() if self.use_knn_search else []
        if self.file_path or self.keywords_list:
            search_results.extend(self._text_search(by_path=not self.use_knn_search))

        if not search_results:
            return []

        return self._rrf_fusion(search_results)

    @observe(name="rrf_fusion")
    def _rrf_fusion(self, search_results: list[tuple[Document, Any, Any]]) -> list[Document]:
        """Execute RRF reranking. Extracted to allow LangFuse span instrumentation."""
        return RRF(
            search_results,
            doc_paths=self.file_path,
            top_k=self.top_k,
            exact_match_field='file_path',
            source_field='source',
            chunk_field='chunk_num',
        ).execute()

    def _get_index_name(self) -> str:
        """
        Get the Elasticsearch index name for the current code fields.

        Returns:
            str: The index identifier.
        """
        return get_indexed_repo(self.code_fields).get_identifier()

    @observe(name="knn_vector_search")
    def _knn_vector_search(self):
        """
        Searches for relevant documents using Elasticsearch k-nearest neighbors (knn).

        Returns:
            List[DocumentSearchResult]: List of documents with their search scores and metadata.

        Raises:
            ElasticsearchException: If the search operation fails.
            ValueError: If embedding generation fails.
        """
        git_repo = get_repo_from_fields(self.code_fields)
        embeddings_model = llm_service.get_embedding_deployment_name(git_repo.embeddings_model)
        embeddings = get_embeddings_model(embeddings_model)
        query_vector = embeddings.embed_query(self.query)

        knn_top_k = self.top_k * self.KNN_MULTIPLIER
        num_candidates = min(self.KNN_MULTIPLIER * knn_top_k, self.MAX_CANDIDATES)

        knn = {
            "field": "vector",
            "filter": [],
            "k": knn_top_k,
            "num_candidates": num_candidates,
            "query_vector": query_vector,
        }

        try:
            search_results = self.es.search(
                index=self.index_name, knn=knn, source=self.ES_SOURCE_FIELDS, size=knn_top_k
            )
        except Exception as e:
            logger.error(f"Elasticsearch search error: {e}")
            return []

        return es_response_to_document(search_results)

    @observe(name="text_search")
    def _text_search(self, by_path: bool):
        """
        Searches for relevant documents using Elasticsearch text search.

        Returns:
            List[DocumentSearchResult]: List of documents with their search scores and metadata.

        """
        should_clauses = []
        must_clause = []
        minimum_should_match = 1
        logger.debug(f"Filtering on keywords {self.keywords_list} for files {self.file_path}")

        if self.file_path:
            if by_path:
                minimum_should_match = 0
                must_clause = [{"match": {"metadata.file_path.keyword": path}} for path in self.file_path]
            else:
                should_clauses.extend([{"match_phrase": {"metadata.file_path": path}} for path in self.file_path])

        if self.keywords_list:
            should_clauses.extend([{"match_phrase": {"text": keyword}} for keyword in self.keywords_list])

        if not (should_clauses or must_clause):
            return should_clauses

        es_query = {
            "bool": {"minimum_should_match": minimum_should_match, "should": should_clauses, "must": must_clause}
        }

        try:
            search_results = self.es.search(
                index=self.index_name, query=es_query, source=self.ES_SOURCE_FIELDS, size=self.TEXT_SEARCH_SIZE
            )
        except Exception as e:
            logger.error(f"Elasticsearch search error: {e}")
            return []

        return es_response_to_document(search_results)
