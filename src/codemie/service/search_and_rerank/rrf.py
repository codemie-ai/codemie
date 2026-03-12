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

from typing import List
from langchain_core.documents import Document


class RRF:
    """
    The Reciprocal Rank Fusion (RRF) is an advanced
    algorithmic technique designed to amalgamate multiple result sets,
    each having distinct relevance indicators, into a unified result set.
    https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf
    magic_number = 60 - Number takes from above paper
    """

    MAGIC_NUMBER = 60

    def __init__(
        self,
        search_results: list[tuple],
        doc_paths: list[str],
        top_k: int,
        exact_match_field: str,
        source_field: str,
        chunk_field: str,
    ):
        self.search_results = search_results
        self.doc_paths = doc_paths
        self.exact_match_field = exact_match_field
        self.source_field = source_field
        self.chunk_field = chunk_field
        self.top_k = top_k

    def execute(self) -> List[Document]:
        """
        Execute the RRF algorithm and return top K documents.
        """
        exact_path_match_docs, fused_scores = self._preprocess_documents()

        reranked_results = self._filter_duplicates(self._rank_documents(fused_scores))
        exact_path_match_docs = self._filter_duplicates(exact_path_match_docs)

        # Sort exact matches by source, then page, then chunk_num
        exact_path_match_docs.sort(
            key=lambda doc: (
                doc.metadata.get(self.source_field, ""),
                doc.metadata.get("page", 0),
                doc.metadata.get(self.chunk_field, 0),
            )
        )

        docs = exact_path_match_docs + reranked_results[: self.top_k]

        return docs

    def _preprocess_documents(self):
        """
        Preprocess documents into exact matches and others.
        """
        exact_path_match_docs = {}
        fused_scores = {}

        sorted_doc_scores = sorted(self.search_results, key=lambda x: x[1], reverse=True)

        for rank, (doc, _score, _id) in enumerate(sorted_doc_scores):
            if doc.metadata[self.exact_match_field] in self.doc_paths:
                exact_path_match_docs[_id] = doc
                continue

            fused_scores.setdefault(_id, [_score, doc])
            fused_scores[_id][0] += 1 / (rank + self.MAGIC_NUMBER)

        return exact_path_match_docs, fused_scores

    def _rank_documents(self, fused_scores: dict) -> dict:
        """
        Rank documents based on the preprocessed scores.
        """
        sorted_fused_scores = sorted(fused_scores.items(), key=lambda x: x[1][0], reverse=True)

        return dict(sorted_fused_scores)

    def _filter_duplicates(self, results: dict) -> list:
        """
        Filter out duplicate documents. Each source should only have one document.
        """
        seen_sources = set()
        filtered_results = []

        for value in results.values():
            try:
                _, doc = value
            except ValueError:
                doc = value

            source = doc.metadata[self.source_field]
            chunk = doc.metadata.get(self.chunk_field, 0)
            key = f"{source}-{chunk}"

            if key not in seen_sources:
                filtered_results.append(doc)
                seen_sources.add(key)

        return filtered_results
