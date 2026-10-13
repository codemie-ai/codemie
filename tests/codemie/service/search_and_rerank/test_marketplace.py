# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from codemie.service.search_and_rerank.marketplace import SearchAndRerankMarketplace


class TestSearchAndRerankMarketplace:
    @pytest.fixture
    def kb_index_mock(self):
        mock = MagicMock()
        mock.repo_name = "test_repo"
        mock.project_name = "test_project"
        mock.full_name = "test_repo"
        mock.embeddings_model = "test-embeddings"
        mock.get_index_identifier.return_value = "test_project-test_repo"
        return mock

    @pytest.fixture
    def instance(self, kb_index_mock):
        mock_es = MagicMock()
        mock_es.indices.exists.return_value = True
        with patch("codemie.service.search_and_rerank.base.ElasticSearchClient.get_client", return_value=mock_es):
            yield SearchAndRerankMarketplace(
                query="what is codemie",
                kb_index=kb_index_mock,
                llm_model="test-model",
                top_k=10,
                request_id="req-1",
            )

    def test_execute_returns_documents_sorted_by_popularity(self, instance):
        doc_low = Document(page_content="low", metadata={"popularity_score": 0.1})
        doc_high = Document(page_content="high", metadata={"popularity_score": 0.9})
        doc_mid = Document(page_content="mid", metadata={"popularity_score": 0.5})

        with patch.object(
            instance.__class__.__bases__[0], "execute", return_value=([doc_low, doc_high, doc_mid], ["path1"])
        ):
            result = instance.execute()

        assert result == [doc_high, doc_mid, doc_low]

    def test_execute_with_missing_popularity_score(self, instance):
        doc_with_score = Document(page_content="has score", metadata={"popularity_score": 0.7})
        doc_no_score = Document(page_content="no score", metadata={})

        with patch.object(
            instance.__class__.__bases__[0], "execute", return_value=([doc_no_score, doc_with_score], [])
        ):
            result = instance.execute()

        assert result == [doc_with_score, doc_no_score]

    def test_execute_returns_list_not_tuple(self, instance):
        doc = Document(page_content="doc", metadata={"popularity_score": 0.5})

        with patch.object(instance.__class__.__bases__[0], "execute", return_value=([doc], ["path1"])):
            result = instance.execute()

        assert isinstance(result, list)
        assert not isinstance(result, tuple)

    def test_execute_dedupes_chunks_per_assistant(self, instance):
        # Arrange: RRF order; assistant "a" has two chunks, "x" has no id
        a_chunk_1 = Document(page_content="a#1", metadata={"id": "a", "chunk_num": 1, "popularity_score": 0.5})
        b_chunk_0 = Document(page_content="b#0", metadata={"id": "b", "chunk_num": 0, "popularity_score": 0.9})
        a_chunk_0 = Document(page_content="a#0", metadata={"id": "a", "chunk_num": 0, "popularity_score": 0.5})
        no_id = Document(page_content="x", metadata={"popularity_score": 0.1})

        with patch.object(
            instance.__class__.__bases__[0],
            "execute",
            return_value=([a_chunk_1, b_chunk_0, a_chunk_0, no_id], ["path1"]),
        ):
            # Act
            result = instance.execute()

        # Assert: best-ranked chunk per assistant kept, then popularity order
        assert result == [b_chunk_0, a_chunk_1, no_id]

    def test_execute_overfetches_then_returns_top_k_distinct_assistants(self, instance):
        # Arrange: chunks of one long assistant would fill the top_k slots without over-fetching
        instance.top_k = 2
        docs = [
            Document(page_content="a#0", metadata={"id": "a", "source": "A", "popularity_score": 0.5}),
            Document(page_content="a#1", metadata={"id": "a", "source": "A", "popularity_score": 0.5}),
            Document(page_content="b#0", metadata={"id": "b", "source": "B", "popularity_score": 0.4}),
            Document(page_content="c#0", metadata={"id": "c", "source": "C", "popularity_score": 0.3}),
        ]
        seen_top_k = []

        def base_execute(_self, routing_field_name=None):
            seen_top_k.append(_self.top_k)
            return docs[: _self.top_k], []

        with patch.object(instance.__class__.__bases__[0], "execute", base_execute):
            # Act
            result = instance.execute()

        # Assert
        assert seen_top_k == [2 * SearchAndRerankMarketplace.DEDUP_OVERFETCH_FACTOR]
        assert [d.page_content for d in result] == ["a#0", "b#0"]
        assert instance.top_k == 2

    def test_execute_reports_returned_chunks_as_complete_coverage(self, instance):
        # Arrange: aggregation saw 44 chunks for "A"; marketplace intentionally returns one per assistant
        instance.source_chunk_totals = {"A": 44, "B": 1, "Z": 3}
        docs = [
            Document(page_content="a#7", metadata={"id": "a", "source": "A", "popularity_score": 0.5}),
            Document(page_content="a#2", metadata={"id": "a", "source": "A", "popularity_score": 0.5}),
            Document(page_content="b#0", metadata={"id": "b", "source": "B", "popularity_score": 0.4}),
        ]

        with patch.object(instance.__class__.__bases__[0], "execute", return_value=(docs, [])):
            # Act
            instance.execute()

        # Assert
        assert instance.source_chunk_totals == {"A": 1, "B": 1}

    def test_long_assistants_stay_routable(self, instance):
        # Arrange: one assistant indexed as 44 chunks
        instance.es.search.return_value = {
            "aggregations": {
                "unique_sources": {
                    "buckets": [
                        {
                            "key": "Long Assistant",
                            "doc_count": 44,
                            "source_metadata": {
                                "hits": {"hits": [{"_source": {"metadata": {"source": "Long Assistant"}}}]}
                            },
                        }
                    ]
                }
            }
        }

        # Act
        sources = instance._fetch_unique_sources("source")

        # Assert
        assert sources and "Long Assistant" in sources[0]
