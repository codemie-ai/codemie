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

from abc import ABC, abstractmethod
from typing import Any

from langchain_core.documents import Document

from codemie.clients.elasticsearch import ElasticSearchClient


class SearchAndRerankBase(ABC):
    """
    ABC to implement searching documents in Elasticsearch index based
    on the provided query, keywords, and file paths.
    The search should be conducted using a combination of techniques:

    1. Keyword-based Search:
       Exact matches are performed on the provided keywords_list and file_path.

    2. Query-based Search:
       A query string is used to search for relevant documents using
       the Elasticsearch k-nearest neighbors (knn) approach.

    3. Pick exactly matched documents with exact_match_field (e.x. file_path)
       Documents with exact match on exact_match_field are excluded from further
       reranking and are always returned in the results first

    3. Reranking:
       Both search results are reranked using the Reciprocal Rank Fusion (RRF)
       algorithm to improve relevance.

    The executte method returns a list of search results, including:
    - All documents that have exact matches with exact_match field.
    - Top-k documents selected after reranking, where k is specified by the top_k parameter.
    """

    ES_SOURCE_FIELDS = ["text", "metadata"]

    @abstractmethod
    def execute(self) -> list[Document] | list:
        # To be implemented by subclasses.
        pass

    @property
    def es(self) -> ElasticSearchClient:
        return ElasticSearchClient.get_client()


def es_response_to_document(response: dict) -> list[tuple[Document, Any, Any]]:
    """
    Converts the Elasticsearch response to a list of Document objects.
    """
    docs_and_scores = []

    for hit in response["hits"]["hits"]:
        docs_and_scores.append(
            (
                Document(
                    page_content=hit["_source"].get("text", ""),
                    metadata=hit["_source"]["metadata"],
                ),
                hit["_score"],
                hit["_id"],
            )
        )
    return docs_and_scores
