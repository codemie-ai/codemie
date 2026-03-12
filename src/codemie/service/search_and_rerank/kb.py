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

from dataclasses import dataclass
from typing import Any, ClassVar, Optional

from langchain_core.documents import Document
from pydantic import BaseModel, Field

from codemie.chains.kb_sources_selector_chain import KBSourcesSelectorChain
from codemie.configs import logger
from codemie.core.dependecies import get_embeddings_model, get_llm_by_credentials
from codemie.rest_api.models.index import IndexInfo
from codemie.service.llm_service.llm_service import llm_service
from codemie.enterprise.loader import observe
from codemie.service.search_and_rerank.base import SearchAndRerankBase, es_response_to_document
from codemie.service.search_and_rerank.rrf import RRF
from codemie.templates.kb_sources_selector_prompt import KB_SOURCES_SELECTOR_PROMPT


class LLMSourcesRouting(BaseModel):
    """
    Pydantic model for LLM source routing results.

    Attributes:
        sources: List of relevant sources or section numbers from the knowledge base.
    """

    sources: list[str] = Field(
        description="List of relevant sources or section numbers from the knowledge base to return",
    )


@dataclass
class SearchAndRerankKB(SearchAndRerankBase):
    """
    Class for searching and reranking knowledge base documents.

    This class performs both semantic vector search and text-based search
    to find the most relevant documents from a knowledge base, using a
    combination of KNN vector search and traditional text search.

    Attributes:
        query: The search query string.
        kb_index: Information about the knowledge base index.
        llm_model: The LLM model to use for source selection.
        top_k: Number of top results to return.
        request_id: Unique identifier for the request.
    """

    query: str
    kb_index: IndexInfo
    llm_model: str
    top_k: int
    request_id: str

    # Class constants
    DEFAULT_SEARCH_SIZE: ClassVar[int] = 100
    MAX_SOURCES_FETCH: ClassVar[int] = 1000
    KNN_CANDIDATES_MULTIPLIER: ClassVar[int] = 3
    MAX_KNN_CANDIDATES: ClassVar[int] = 10_000
    MAX_CHUNKS_FOR_SINGLE_DOCUMENT: ClassVar[int] = 20
    ROUTING_FIELD_NAME_DEFAULT: ClassVar[str] = "summary"

    def __post_init__(self) -> None:
        """Initialize additional attributes after dataclass initialization."""
        if not self.query:
            raise ValueError("Query cannot be empty")
        if self.top_k <= 0:
            raise ValueError("top_k must be a positive integer")

        self.index_name = self.kb_index.get_index_identifier()
        self.exact_match_field, self.source_field, self.source_chunk_file = self._get_meta_search_fields()
        self.chain = KBSourcesSelectorChain(
            query=self.query, sources=[], llm_model=self.llm_model, request_id=self.request_id
        )

    @staticmethod
    def _get_meta_search_fields() -> tuple[str, str, str]:
        """
        Return the fields to search for exact match in metadata.

        Returns:
            tuple[str, str, str]: Tuple containing search field names
                (exact_match_field, source_field, source_chunk_file).
        """
        return 'source', 'source', 'chunk_num'

    def execute(self, routing_field_name: Optional[str] = None) -> list[Document]:
        """
        Execute the search and rerank process.

        Args:
            routing_field_name: Field name in the document metadata to use for routing.
                               Defaults to the class constant if None.

        Returns:
            List[Document]: Ranked list of documents.
        """
        # Use class constant if no routing_field_name provided
        if routing_field_name is None:
            routing_field_name = self.ROUTING_FIELD_NAME_DEFAULT

        # Collect search results from vector search
        search_results = self._knn_vector_search()
        # Get document paths from LLM source routing
        doc_paths = self._get_llm_sources(routing_field_name)

        # Add text search results
        search_results.extend(self._text_search(doc_paths))

        # Use reciprocal rank fusion to rerank results
        return self._rrf_fusion(search_results, doc_paths)

    @observe(name="rrf_fusion")
    def _rrf_fusion(self, search_results: list[tuple[Document, Any, Any]], doc_paths: list[str]) -> list[Document]:
        """Execute RRF reranking. Extracted to allow LangFuse span instrumentation."""
        return RRF(
            search_results,
            doc_paths=doc_paths,
            top_k=self.top_k,
            exact_match_field=self.exact_match_field,
            source_field=self.source_field,
            chunk_field=self.source_chunk_file,
        ).execute()

    def _get_llm_sources(self, routing_field_name: str) -> list[str]:
        """
        Get the list of sources from the KB index and find the relevant sources using LLM.

        Args:
            routing_field_name: Field name in metadata to use for source routing.

        Returns:
            list[str]: List of relevant source paths selected by the LLM.
        """
        # Extract unique sources from the index with aggregation
        sources = self._fetch_unique_sources(routing_field_name)

        if not sources:
            logger.debug(f"No sources found for request {self.request_id}")
            return []

        # Use LLM to select relevant sources
        return self._llm_routing(sources)

    def _fetch_unique_sources(self, routing_field_name: str) -> list[str]:
        """
        Fetch unique sources from Elasticsearch with their metadata.

        Args:
            routing_field_name: Field name in metadata to use for source routing.

        Returns:
            List[str]: List of formatted source entries.
        """
        agg_query = {
            "size": 0,  # We don't need the documents, just the aggregation results
            "query": {"match_all": {}},
            "aggs": {
                "unique_sources": {
                    "terms": {"field": "metadata.source.keyword", "size": self.MAX_SOURCES_FETCH},
                    "aggs": {
                        "source_metadata": {
                            "top_hits": {
                                "size": 1,
                                "_source": ["metadata"],
                            }
                        }
                    },
                }
            },
        }

        try:
            results = self.es.search(index=self.index_name, body=agg_query)
        except Exception as e:
            logger.error(f"Elasticsearch aggregation error for request {self.request_id}: {e}")
            return []

        sources = []

        # Process the aggregation results
        for bucket in results.get("aggregations", {}).get("unique_sources", {}).get("buckets", []):
            chunks_count = bucket["doc_count"]  # Number of chunks for this source

            # Only include sources with manageable chunk counts
            if chunks_count <= self.MAX_CHUNKS_FOR_SINGLE_DOCUMENT:
                hit_list = bucket.get("source_metadata", {}).get("hits", {}).get("hits", [])
                if hit_list:
                    hit = hit_list[0]
                    source_data = hit.get("_source", {})

                    if "metadata" in source_data and "source" in source_data["metadata"]:
                        # Format the source with metadata information
                        formatted_source = self._format_hit(hit, routing_field_name)
                        sources.append(formatted_source)

        return sources

    def _format_hit(self, hit: dict[str, Any], routing_field_name: str) -> str:
        """
        Format an Elasticsearch hit into a structured source entry.

        Args:
            hit: The Elasticsearch hit document.
            routing_field_name: The metadata field to use for routing information.

        Returns:
            str: Formatted source entry with XML-like tags.
        """
        metadata = hit.get("_source", {}).get("metadata", {})
        source = metadata.get("source", "")

        if not source:
            logger.warning(f"Missing source in document metadata for request {self.request_id}")
            return ""

        if routing_field_name in metadata:
            routing = metadata[routing_field_name]
            return f"<source>{source}</source><summary>{routing}</summary>"
        else:
            return f"<source>{source}</source>"

    def _llm_routing(self, sources: list[str]) -> list[str]:
        """
        Use LLM to select the most relevant sources for the query.

        Args:
            sources: List of formatted source entries to choose from.

        Returns:
            List[str]: The sources selected by the LLM as most relevant.
        """
        if not sources:
            logger.debug(f"No sources provided for LLM routing for request {self.request_id}")
            return []

        # Join all sources with newlines for readability
        sections = "\n".join(sources)

        try:
            # Get the appropriate LLM model
            llm = get_llm_by_credentials(llm_model=self.llm_model, request_id=self.request_id)

            # Create the search chain with structured output
            search_chain = KB_SOURCES_SELECTOR_PROMPT | llm.with_structured_output(LLMSourcesRouting)

            # Run the chain with the query and sources
            selected_sources = search_chain.invoke({"sources": str(sections), "question": self.query})

            return selected_sources.sources

        except Exception as e:
            logger.error(f"Error in LLM routing for request {self.request_id}: {e}")
            return []

    @observe(name="text_search")
    def _text_search(self, doc_paths: Optional[list[str]] = None) -> list[tuple[Document, Any, Any]]:
        """
        Perform text-based search in the knowledge base using exact matches.

        Args:
            doc_paths: Optional list of document paths to restrict the search to.
                      If provided, adds path-specific match clauses to the query.

        Returns:
            List[Tuple[Document, Any, Any]]: Search results with documents and metadata.
        """
        # Build query with multiple "should" clauses for different match criteria
        should_clauses = [
            {"match_phrase": {"text": self.query}},
            {"match_phrase": {f"metadata.{self.exact_match_field}": self.query}},
            {"match_phrase": {f"metadata.{self.source_field}": self.query}},
        ]

        # Add document path filters if provided
        if doc_paths and isinstance(doc_paths, list):
            # Only add valid paths (non-empty strings)
            path_clauses = [
                {"match_phrase": {"metadata.source": path}} for path in doc_paths if path and isinstance(path, str)
            ]
            should_clauses.extend(path_clauses)

        # Construct the main Elasticsearch query
        es_query = {
            "bool": {
                "minimum_should_match": 1,
                "should": should_clauses,
            }
        }

        try:
            search_results = self.es.search(
                index=self.index_name, query=es_query, source=self.ES_SOURCE_FIELDS, size=self.DEFAULT_SEARCH_SIZE
            )
            return es_response_to_document(search_results)
        except Exception as e:
            logger.error(f"Elasticsearch text search error for request {self.request_id}: {e}")
            return []

    @observe(name="knn_vector_search")
    def _knn_vector_search(self) -> list[tuple[Document, Any, Any]]:
        """
        Perform KNN vector search using embeddings for semantic similarity matching.

        This method:
        1. Gets the embedding model specified in the KB index
        2. Creates a vector embedding of the query
        3. Performs a KNN search in the vector space

        Returns:
            List[Tuple[Document, Any, Any]]: Search results with documents and metadata.
        """
        try:
            # Get the appropriate embeddings model
            embeddings_model = llm_service.get_embedding_deployment_name(self.kb_index.embeddings_model)
            embeddings = get_embeddings_model(embeddings_model)

            # Create embedding vector from the query
            query_vector = embeddings.embed_query(self.query)

            # Calculate appropriate values for top_k and candidates
            knn_top_k = self.top_k * self.KNN_CANDIDATES_MULTIPLIER
            num_candidates = min(self.KNN_CANDIDATES_MULTIPLIER * knn_top_k, self.MAX_KNN_CANDIDATES)

            # Construct KNN query parameters
            knn = {
                "field": "vector",
                "filter": [],
                "k": knn_top_k,
                "num_candidates": num_candidates,
                "query_vector": query_vector,
            }

            # Execute the search
            search_results = self.es.search(
                index=self.index_name, knn=knn, source=self.ES_SOURCE_FIELDS, size=knn_top_k
            )

            return es_response_to_document(search_results)

        except Exception as e:
            logger.error(f"Elasticsearch KNN vector search error for request {self.request_id}: {e}")
            return []
