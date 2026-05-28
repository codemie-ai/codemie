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
