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

"""Marketplace-specific search and rerank implementation with popularity scoring."""

from collections import Counter
from dataclasses import dataclass
from typing import ClassVar, Optional

from langchain_core.documents import Document

from codemie.core.models import sanitize_es_index_name
from codemie.service.search_and_rerank.kb import SearchAndRerankKB


@dataclass
class SearchAndRerankMarketplace(SearchAndRerankKB):
    """
    Marketplace-specific search and rerank that incorporates popularity scoring.

    This class extends SearchAndRerankKB to boost search results based on:
    - Number of unique users
    - Number of likes
    - Number of dislikes

    Popular and well-liked assistants will rank higher in search results.

    Long assistants are indexed as several chunks; results are reduced to one chunk per assistant.
    """

    # Base search is over-fetched so de-duplicating chunks still leaves top_k distinct assistants
    DEDUP_OVERFETCH_FACTOR: ClassVar[int] = 3
    # Every assistant must stay routable by the LLM source routing, however many chunks it has
    MAX_CHUNKS_FOR_SINGLE_DOCUMENT: ClassVar[int] = 10_000

    def __post_init__(self) -> None:
        super().__post_init__()
        # If the resolved index does not exist in ES (e.g. new naming was computed but
        # the actual index uses legacy repo_name-only naming), fall back automatically.
        if not self.es.indices.exists(index=self.index_name):
            legacy_name = sanitize_es_index_name(self.kb_index.repo_name)
            if legacy_name != self.index_name and self.es.indices.exists(index=legacy_name):
                self.index_name = legacy_name

    def execute(self, routing_field_name: Optional[str] = None) -> list[Document]:
        """
        Execute search and rerank with popularity boost.

        Args:
            routing_field_name: Field name in the document metadata to use for routing.

        Returns:
            List of reranked documents with popularity boost applied.
        """
        top_k = self.top_k
        self.top_k = top_k * self.DEDUP_OVERFETCH_FACTOR
        try:
            results, _ = super().execute(routing_field_name)
        finally:
            self.top_k = top_k

        unique_results = self._best_chunk_per_assistant(results)[:top_k]
        # One chunk per assistant is the intended, complete answer for marketplace search;
        # report it as such so the search tool does not flag results as partial.
        self.source_chunk_totals = dict(Counter(doc.metadata.get('source') for doc in unique_results))

        results_with_scores = []
        for doc in unique_results:
            # popularity_score is already normalized to [0, 1] range by AssistantLoader
            popularity_score = doc.metadata.get('popularity_score', 0.0)
            results_with_scores.append((doc, popularity_score))

        results_with_scores.sort(key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in results_with_scores]

    @staticmethod
    def _best_chunk_per_assistant(results: list[Document]) -> list[Document]:
        """
        Keep only the best-ranked chunk of each assistant.

        Long assistants are indexed as several chunks; without this, one assistant could occupy
        several result slots and receive its popularity boost more than once.
        Documents without an assistant id are kept as-is.
        """
        seen_ids: set[str] = set()
        unique_results = []
        for doc in results:
            assistant_id = doc.metadata.get('id')
            if assistant_id is not None:
                if assistant_id in seen_ids:
                    continue
                seen_ids.add(assistant_id)
            unique_results.append(doc)
        return unique_results
