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

"""Search and rerank implementation for tool metadata.

This module provides SearchAndRerankTool class for hybrid search on tool metadata
using KNN vector search and enhanced text search with RRF reranking.
"""

import re
from dataclasses import dataclass
from typing import List, Tuple, Any, ClassVar

from langchain_core.documents import Document

from codemie.configs.logger import logger
from codemie.core.dependecies import get_embeddings_model
from codemie.service.llm_service.llm_service import llm_service
from codemie.enterprise.loader import observe
from codemie.service.search_and_rerank.base import SearchAndRerankBase, es_response_to_document
from codemie.service.search_and_rerank.rrf import RRF


@dataclass
class SearchAndRerankTool(SearchAndRerankBase):
    """Search and rerank tool metadata using hybrid approach.

    Combines KNN vector search and multi-strategy text search with RRF reranking
    to find the most relevant tools based on user queries.

    This class performs:
    1. KNN vector search for semantic similarity
    2. Enhanced text search with token matching, boosting, and fuzzy matching
    3. RRF reranking to combine results from both searches

    Attributes:
        query: The search query string
        top_k: Number of top results to return
        index_name: Elasticsearch index name for tools
        tool_names_filter: Optional list of tool names to filter results.
                          If provided, only tools with names in this list will be returned.
    """

    query: str
    top_k: int
    index_name: str
    tool_names_filter: List[str] | None = None

    # Class constants for search configuration
    KNN_MULTIPLIER: ClassVar[int] = 3
    MAX_KNN_CANDIDATES: ClassVar[int] = 10_000
    TEXT_SEARCH_SIZE: ClassVar[int] = 100

    def __post_init__(self) -> None:
        """Validate attributes after dataclass initialization."""
        if not self.query:
            raise ValueError("Query cannot be empty")
        if self.top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        if not self.index_name:
            raise ValueError("index_name cannot be empty")

    def execute(self) -> List[Document]:
        """Execute hybrid search and rerank operation.

        Returns:
            List[Document]: Ranked list of tool documents matching the search criteria.
        """
        logger.debug(
            f"Starting tool hybrid search. Query='{self.query[:100]}...', TopK={self.top_k}, Index={self.index_name}"
        )

        # Perform KNN vector search
        knn_results = self._knn_vector_search()
        logger.debug(f"Tool KNN search returned {len(knn_results)} results")

        # Perform enhanced text search
        text_results = self._text_search()
        logger.debug(f"Tool text search returned {len(text_results)} results")

        # Combine results
        all_results = knn_results + text_results

        if not all_results:
            logger.info(f"No tools found for query: '{self.query[:50]}...'")
            return []

        # Apply RRF reranking
        reranked_docs = self._rrf_fusion(all_results)

        logger.debug(f"Tool RRF reranking returned {len(reranked_docs)} documents")

        return reranked_docs

    @observe(name="rrf_fusion")
    def _rrf_fusion(self, search_results: list[tuple[Document, Any, Any]]) -> list[Document]:
        """Execute RRF reranking. Extracted to allow LangFuse span instrumentation."""
        return RRF(
            search_results=search_results,
            doc_paths=[],
            top_k=self.top_k,
            exact_match_field='name',
            source_field='toolkit',
            chunk_field='name',
        ).execute()

    @observe(name="knn_vector_search")
    def _knn_vector_search(self) -> List[Tuple[Document, Any, Any]]:
        """Perform KNN vector search using embeddings for semantic similarity.

        Returns:
            List[Tuple[Document, Any, Any]]: Search results with documents, scores, and IDs.
        """
        try:
            # Get embeddings model
            default_embedding_model = llm_service.default_embedding_model
            embedding_deployment_name = llm_service.get_embedding_deployment_name(default_embedding_model)
            embeddings = get_embeddings_model(embedding_deployment_name)

            # Create query vector
            query_vector = embeddings.embed_query(self.query)

            # Calculate KNN parameters
            knn_top_k = self.top_k * self.KNN_MULTIPLIER
            num_candidates = min(self.KNN_MULTIPLIER * knn_top_k, self.MAX_KNN_CANDIDATES)

            # Build filter for tool names if provided
            knn_filter = []
            if self.tool_names_filter:
                # Filter by tool names using terms query
                knn_filter.append({"terms": {"metadata.name.keyword": self.tool_names_filter}})

            # Construct KNN query
            knn = {
                "field": "vector",
                "filter": knn_filter,
                "k": knn_top_k,
                "num_candidates": num_candidates,
                "query_vector": query_vector,
            }

            # Execute KNN search
            search_results = self.es.search(
                index=self.index_name, knn=knn, source=self.ES_SOURCE_FIELDS, size=knn_top_k
            )

            logger.debug(
                f"Tool KNN search executed. Index={self.index_name}, TopK={knn_top_k}, "
                f"Candidates={num_candidates}, Hits={len(search_results.get('hits', {}).get('hits', []))}"
            )

            return es_response_to_document(search_results)

        except Exception as e:
            logger.error(f"Tool KNN vector search failed: {e}", exc_info=True)
            return []

    @observe(name="text_search")
    def _text_search(self) -> List[Tuple[Document, Any, Any]]:
        """Perform enhanced text-based search with multiple strategies.

        Searches across multiple fields with different strategies:
        - Exact phrase matching on tool name and description
        - Token matching on individual words (e.g., 'jira' matches 'generic_jira_tool')
        - Toolkit name matching
        - Fuzzy matching on individual query terms

        Returns:
            List[Tuple[Document, Any, Any]]: Search results with documents, scores, and IDs.
        """
        try:
            # Tokenize the query for individual word matching
            query_tokens = self.query.lower().split()

            # Build query with multiple "should" clauses with boosting
            should_clauses = [
                # High boost: Exact match on tool name
                {"match_phrase": {"metadata.name": {"query": self.query, "boost": 5.0}}},
                # High boost: Match on tokenized name parts (e.g., 'jira' matches 'generic jira tool')
                {"match": {"metadata.name_tokens": {"query": self.query, "boost": 4.0}}},
                # Medium boost: Match in description
                {"match_phrase": {"metadata.description": {"query": self.query, "boost": 2.0}}},
                # Medium boost: Match full text content
                {"match_phrase": {"text": {"query": self.query, "boost": 2.0}}},
                # Lower boost: Match toolkit name
                {"match": {"metadata.toolkit": {"query": self.query, "boost": 1.5}}},
            ]

            # Add individual token matches for better partial matching
            # E.g., query "log ticket jira" will match tools with 'jira' in name_tokens
            for token in query_tokens:
                if len(token) > 2:  # Skip very short tokens
                    should_clauses.extend(
                        [
                            # Match individual tokens in name_tokens field
                            {"match": {"metadata.name_tokens": {"query": token, "boost": 3.0}}},
                            # Match individual tokens in tool name
                            {"match": {"metadata.name": {"query": token, "boost": 2.5, "fuzziness": "AUTO"}}},
                            # Match individual tokens in description
                            {"match": {"metadata.description": {"query": token, "boost": 1.0}}},
                        ]
                    )

            # Build filter for tool names if provided
            filter_clauses = []
            if self.tool_names_filter:
                filter_clauses.append({"terms": {"metadata.name.keyword": self.tool_names_filter}})

            # Construct Elasticsearch query
            es_query = {
                "bool": {
                    "minimum_should_match": 1,
                    "should": should_clauses,
                    "filter": filter_clauses,
                }
            }

            # Execute text search
            search_results = self.es.search(
                index=self.index_name, query=es_query, source=self.ES_SOURCE_FIELDS, size=self.TEXT_SEARCH_SIZE
            )

            logger.debug(
                f"Tool text search executed. Index={self.index_name}, Query='{self.query}', "
                f"Tokens={query_tokens}, Hits={len(search_results.get('hits', {}).get('hits', []))}"
            )

            return es_response_to_document(search_results)

        except Exception as e:
            logger.error(f"Tool text search failed: {e}", exc_info=True)
            return []

    @staticmethod
    def tokenize_tool_name(tool_name: str) -> List[str]:
        """Tokenize tool name into searchable parts.

        Splits by underscore and camelCase to extract individual words.
        Examples:
            'generic_jira_tool' -> ['generic', 'jira', 'tool']
            'ZephyrSquad' -> ['zephyr', 'squad']
            'search_code_repo_by_path' -> ['search', 'code', 'repo', 'by', 'path']

        Args:
            tool_name: The tool name to tokenize

        Returns:
            List of lowercase tokens
        """
        # Split by underscore first
        parts = tool_name.replace('_', ' ')
        # Split camelCase (e.g., 'ZephyrSquad' -> 'Zephyr Squad')
        parts = re.sub(r'([a-z])([A-Z])', r'\1 \2', parts)
        # Lowercase and split into tokens
        tokens = parts.lower().split()
        return tokens
