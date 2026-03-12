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

import pytest
import uuid
from codemie.service.search_and_rerank.rrf import RRF
from langchain_core.documents import Document


class TestRRF:
    @pytest.fixture
    def sample_docs(self):
        return [
            [
                Document(page_content="test content 1", metadata={'source': 'source1', 'exact_match_field': 'path1'}),
                0.25,
                uuid.uuid4(),
            ],
            [
                Document(page_content="test content 2", metadata={'source': 'source2', 'exact_match_field': 'path2'}),
                0.5,
                uuid.uuid4(),
            ],
        ]

    def test_basic_rrf(self, sample_docs):
        rrf_instance = RRF(
            search_results=sample_docs,
            doc_paths=['path1'],
            top_k=2,
            exact_match_field='exact_match_field',
            source_field='source',
            chunk_field='chunk_num',
        )
        results = rrf_instance.execute()
        assert len(results) == 2
        assert results[0].metadata['source'] == 'source1'
        assert results[1].metadata['source'] == 'source2'

    def test_duplicate_filtering(self):
        doc1_id = uuid.uuid4()
        doc2_id = uuid.uuid4()
        docs = [
            [
                Document(
                    page_content="content 1",
                    metadata={'source': 'source1', 'exact_match_field': 'path1', 'chunk_num': 1},
                ),
                0.8,
                doc1_id,
            ],
            [
                Document(
                    page_content="content 2",
                    metadata={'source': 'source1', 'exact_match_field': 'path2', 'chunk_num': 1},
                ),
                0.7,
                doc2_id,
            ],
        ]

        rrf_instance = RRF(
            search_results=docs,
            doc_paths=[],
            top_k=2,
            exact_match_field='exact_match_field',
            source_field='source',
            chunk_field='chunk_num',
        )
        results = rrf_instance.execute()
        assert len(results) == 1
        assert results[0].metadata['source'] == 'source1'
        assert results[0].page_content == "content 1"

    def test_exact_match_handling(self):
        doc1_id = uuid.uuid4()
        doc2_id = uuid.uuid4()
        doc3_id = uuid.uuid4()
        docs = [
            [
                Document(page_content="exact match", metadata={'source': 'source1', 'exact_match_field': 'path1'}),
                0.5,
                doc1_id,
            ],
            [
                Document(page_content="higher score", metadata={'source': 'source2', 'exact_match_field': 'path2'}),
                0.9,
                doc2_id,
            ],
            [
                Document(page_content="another doc", metadata={'source': 'source3', 'exact_match_field': 'path3'}),
                0.7,
                doc3_id,
            ],
        ]

        rrf_instance = RRF(
            search_results=docs,
            doc_paths=['path1'],
            top_k=2,
            exact_match_field='exact_match_field',
            source_field='source',
            chunk_field='chunk_num',
        )
        results = rrf_instance.execute()
        assert len(results) == 3
        assert results[0].page_content == "exact match"
        assert results[1].page_content == "higher score"
        assert results[2].page_content == "another doc"

    def test_same_source_different_chunks(self):
        doc1_id = uuid.uuid4()
        doc2_id = uuid.uuid4()
        docs = [
            [
                Document(
                    page_content="chunk 1", metadata={'source': 'source1', 'exact_match_field': 'path1', 'chunk_num': 1}
                ),
                0.8,
                doc1_id,
            ],
            [
                Document(
                    page_content="chunk 2", metadata={'source': 'source1', 'exact_match_field': 'path1', 'chunk_num': 2}
                ),
                0.7,
                doc2_id,
            ],
        ]

        rrf_instance = RRF(
            search_results=docs,
            doc_paths=[],
            top_k=2,
            exact_match_field='exact_match_field',
            source_field='source',
            chunk_field='chunk_num',
        )
        results = rrf_instance.execute()
        assert len(results) == 2
        assert results[0].metadata['chunk_num'] == 1
        assert results[1].metadata['chunk_num'] == 2

    def test_missing_chunk_field(self):
        doc1_id = uuid.uuid4()
        doc2_id = uuid.uuid4()
        docs = [
            [
                Document(page_content="doc 1", metadata={'source': 'source1', 'exact_match_field': 'path1'}),
                0.8,
                doc1_id,
            ],
            [
                Document(page_content="doc 2", metadata={'source': 'source1', 'exact_match_field': 'path2'}),
                0.7,
                doc2_id,
            ],
        ]

        rrf_instance = RRF(
            search_results=docs,
            doc_paths=[],
            top_k=2,
            exact_match_field='exact_match_field',
            source_field='source',
            chunk_field='chunk_num',
        )
        results = rrf_instance.execute()
        assert len(results) == 1  # Should only return one document since they have same source and default chunk value

    def test_rrf_score_calculation(self):
        doc1_id = uuid.uuid4()
        doc2_id = uuid.uuid4()
        docs = [
            [
                Document(page_content="doc 1", metadata={'source': 'source1', 'exact_match_field': 'path1'}),
                0.9,  # Initial score
                doc1_id,
            ],
            [
                Document(page_content="doc 2", metadata={'source': 'source2', 'exact_match_field': 'path2'}),
                0.8,  # Initial score
                doc2_id,
            ],
        ]

        rrf_instance = RRF(
            search_results=docs,
            doc_paths=[],
            top_k=2,
            exact_match_field='exact_match_field',
            source_field='source',
            chunk_field='chunk_num',
        )

        # Get intermediate results to verify score calculation
        _, fused_scores = rrf_instance._preprocess_documents()

        # First document should have score = 0.9 + 1/(1+60) ≈ 0.9164
        # Second document should have score = 0.8 + 1/(2+60) ≈ 0.8161
        doc1_final_score = fused_scores[doc1_id][0]
        doc2_final_score = fused_scores[doc2_id][0]

        assert doc1_final_score > doc2_final_score
        assert abs(doc1_final_score - (0.9 + 1 / 61)) > 0.0001
        assert abs(doc2_final_score - (0.8 + 1 / 62)) > 0.0001
