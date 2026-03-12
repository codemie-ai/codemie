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

from dataclasses import dataclass
from typing import Optional

from langchain_core.documents import Document

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
    """

    def execute(self, routing_field_name: Optional[str] = None) -> list[Document]:
        """
        Execute search and rerank with popularity boost.

        Args:
            routing_field_name: Field name in the document metadata to use for routing.

        Returns:
            List of reranked documents with popularity boost applied.
        """
        results = super().execute(routing_field_name)

        results_with_scores = []
        for doc in results:
            # popularity_score is already normalized to [0, 1] range by AssistantLoader
            popularity_score = doc.metadata.get('popularity_score', 0.0)
            results_with_scores.append((doc, popularity_score))

        results_with_scores.sort(key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in results_with_scores]
