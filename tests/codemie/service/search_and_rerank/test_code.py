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
from langchain_core.documents import Document

from codemie.service.search_and_rerank import SearchAndRerankCode
from codemie.core.models import CodeFields
from codemie.core.constants import CodeIndexType


class TestSearchAndRerankCode:
    @pytest.fixture
    def es_mocked_response(self):
        return {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "text": "test content",
                            "metadata": {"source": "test source", "file_path": "/path"},
                        },
                        "_score": 0.5,
                        "_id": "1",
                    }
                ]
            }
        }

    @pytest.fixture
    def code_fields(self):
        return CodeFields(
            app_name='test_app',
            repo_name='test_repo',
            index_type=CodeIndexType.CODE,
        )

    @pytest.fixture
    def code_instance(self, mocker, code_fields):
        get_index_name_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankCode._get_index_name',
        )
        get_index_name_mock.return_value = 'test_index'

        return SearchAndRerankCode(
            query="test query", keywords_list=["keyword1"], file_path=["/path"], code_fields=code_fields, top_k=10
        )

    def test_execute_returns_list(self, mocker, code_instance, es_mocked_response):
        es_search_mock = mocker.MagicMock()
        es_search_mock.search.return_value = es_mocked_response

        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )

        es_mock.return_value = es_search_mock

        knn_vector_search_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankCode._knn_vector_search',
        )
        knn_vector_search_mock.return_value = []

        result = code_instance.execute()

        assert isinstance(result, list)
        assert len(result) == 1

    def test_get_index_name(self, mocker, code_fields):
        get_indexed_repo_mock = mocker.patch('codemie.service.search_and_rerank.code.get_indexed_repo')
        mock_repo = mocker.MagicMock()
        mock_repo.get_identifier.return_value = 'test_repo_identifier'
        get_indexed_repo_mock.return_value = mock_repo

        code_instance = SearchAndRerankCode(
            query="test query", keywords_list=[], file_path=[], code_fields=code_fields, top_k=10
        )

        result = code_instance._get_index_name()
        assert result == 'test_repo_identifier'
        get_indexed_repo_mock.assert_called_with(code_fields)

    def test_knn_vector_search(self, mocker, code_instance):
        # Mock embeddings model and vector
        mock_embeddings = mocker.MagicMock()
        mock_embeddings.embed_query.return_value = [0.1, 0.2, 0.3]
        mock_get_embeddings = mocker.patch('codemie.service.search_and_rerank.code.get_embeddings_model')
        mock_get_embeddings.return_value = mock_embeddings

        # Mock git repo and embeddings model name
        mock_git_repo = mocker.MagicMock()
        mock_git_repo.embeddings_model = "test_model"
        mock_get_repo = mocker.patch('codemie.service.search_and_rerank.code.get_repo_from_fields')
        mock_get_repo.return_value = mock_git_repo

        # Mock LLM service
        mock_llm_service = mocker.patch('codemie.service.search_and_rerank.code.llm_service')
        mock_llm_service.get_embedding_deployment_name.return_value = "test_deployment"

        # Mock elasticsearch response
        es_response = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "text": "knn test content",
                            "metadata": {"source": "knn source", "file_path": "/knn/path"},
                        },
                        "_score": 0.8,
                        "_id": "2",
                    }
                ]
            }
        }

        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        mock_es_client = mocker.MagicMock()
        mock_es_client.search.return_value = es_response
        es_mock.return_value = mock_es_client

        results = code_instance._knn_vector_search()

        assert len(results) == 1
        doc, _, _ = results[0]
        assert doc.page_content == "knn test content"
        assert doc.metadata["source"] == "knn source"
        assert doc.metadata["file_path"] == "/knn/path"

        # Verify the KNN search parameters
        mock_es_client.search.assert_called_once()
        call_args = mock_es_client.search.call_args[1]
        assert call_args["index"] == code_instance.index_name
        assert call_args["size"] == code_instance.top_k * 3
        assert "knn" in call_args

    def test_combined_search_results(self, mocker, code_instance):
        # Mock responses for both search types
        knn_response = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "text": "knn content",
                            "metadata": {"source": "knn source", "file_path": "/knn/path"},
                        },
                        "_score": 0.8,
                        "_id": "2",
                    }
                ]
            }
        }

        text_response = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "text": "text content",
                            "metadata": {"source": "text source", "file_path": "/text/path"},
                        },
                        "_score": 0.6,
                        "_id": "3",
                    }
                ]
            }
        }

        # Mock embeddings and other KNN dependencies
        mock_embeddings = mocker.MagicMock()
        mock_embeddings.embed_query.return_value = [0.1, 0.2, 0.3]
        mocker.patch('codemie.service.search_and_rerank.code.get_embeddings_model', return_value=mock_embeddings)
        mock_git_repo = mocker.MagicMock()
        mock_git_repo.embeddings_model = "test_model"
        mocker.patch('codemie.service.search_and_rerank.code.get_repo_from_fields', return_value=mock_git_repo)
        mocker.patch(
            'codemie.service.search_and_rerank.code.llm_service.get_embedding_deployment_name',
            return_value="test_deployment",
        )

        # Mock elasticsearch client
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        mock_es_client = mocker.MagicMock()
        mock_es_client.search.side_effect = [knn_response, text_response]
        es_mock.return_value = mock_es_client

        results = code_instance.execute()

        assert len(results) == 2
        # Verify both search methods were called
        assert mock_es_client.search.call_count == 2

    # _rrf_fusion Tests

    def test_rrf_fusion_calls_rrf_with_correct_params(self, mocker, code_instance):
        """_rrf_fusion delegates to RRF with instance file_path and fixed fields."""
        search_results = [
            (Document(page_content="code1", metadata={"file_path": "/a.py"}), 0.9, "id1"),
        ]
        expected = [Document(page_content="code1", metadata={"file_path": "/a.py"})]

        rrf_mock = mocker.patch('codemie.service.search_and_rerank.code.RRF')
        rrf_mock.return_value.execute.return_value = expected

        result = code_instance._rrf_fusion(search_results)

        assert result == expected
        rrf_mock.assert_called_once_with(
            search_results,
            doc_paths=code_instance.file_path,
            top_k=code_instance.top_k,
            exact_match_field='file_path',
            source_field='source',
            chunk_field='chunk_num',
        )

    def test_rrf_fusion_with_empty_results(self, mocker, code_instance):
        """_rrf_fusion passes empty list to RRF and returns its result."""
        rrf_mock = mocker.patch('codemie.service.search_and_rerank.code.RRF')
        rrf_mock.return_value.execute.return_value = []

        result = code_instance._rrf_fusion([])

        assert result == []
        rrf_mock.assert_called_once_with(
            [],
            doc_paths=code_instance.file_path,
            top_k=code_instance.top_k,
            exact_match_field='file_path',
            source_field='source',
            chunk_field='chunk_num',
        )

    def test_multiple_keywords_and_paths(self, mocker, code_fields):
        # Mock elasticsearch response for search
        es_response = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "text": "content1",
                            "metadata": {"source": "source1", "file_path": "/path1"},
                        },
                        "_score": 0.7,
                        "_id": "4",
                    },
                    {
                        "_source": {
                            "text": "content2",
                            "metadata": {"source": "source2", "file_path": "/path2"},
                        },
                        "_score": 0.6,
                        "_id": "5",
                    },
                ]
            }
        }

        # Mock indexed repo response
        indexed_repo_response = {
            "_index": "repositories",
            "_id": "test_app-test_repo-code",
            "_source": {
                "repo_name": "test_repo",
                "app_name": "test_app",
                "index_type": "code",
                "identifier": "test_app-test_repo-code",
            },
            "found": True,
        }

        # Mock ES client for search
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        mock_es_client = mocker.MagicMock()
        # Set up search response
        mock_es_client.search.return_value = es_response
        # Set up get response for indexed repo
        mock_es_client.get.return_value = indexed_repo_response
        es_mock.return_value = mock_es_client

        # Mock get_indexed_repo to return a mocked repository
        mock_repo = mocker.MagicMock()
        mock_repo.get_identifier.return_value = "test_app-test_repo-code"
        mocker.patch('codemie.service.search_and_rerank.code.get_indexed_repo', return_value=mock_repo)

        code_instance = SearchAndRerankCode(
            query="test query",
            keywords_list=["keyword1", "keyword2"],
            file_path=["/path1", "/path2"],
            code_fields=code_fields,
            top_k=10,
        )

        # Mock KNN search to return empty results for this test
        mocker.patch.object(code_instance, '_knn_vector_search', return_value=[])

        results = code_instance._text_search(by_path=False)

        assert len(results) == 2
        # Verify the query includes both paths and keywords
        call_args = mock_es_client.search.call_args[1]
        query = call_args["query"]["bool"]["should"]
        assert len(query) == 4  # 2 paths + 2 keywords
