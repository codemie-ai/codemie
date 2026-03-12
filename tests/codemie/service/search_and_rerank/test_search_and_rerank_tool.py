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

"""Unit tests for SearchAndRerankTool."""

from unittest.mock import MagicMock, Mock, patch

import pytest
from elasticsearch import ApiError
from langchain_core.documents import Document

from codemie.service.search_and_rerank.tool import SearchAndRerankTool


class TestSearchAndRerankTool:
    """Test suite for the SearchAndRerankTool class."""

    @pytest.fixture
    def tool_instance(self):
        """Create a SearchAndRerankTool instance for testing."""
        return SearchAndRerankTool(query="test query", top_k=5, index_name="tools_index")

    @pytest.fixture
    def es_mocked_response(self):
        """Create a mock Elasticsearch response."""
        return {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "text": "test tool content",
                            "metadata": {"name": "test_tool", "toolkit": "test_toolkit", "description": "Test tool"},
                        },
                        "_score": 0.95,
                        "_id": "1",
                    },
                    {
                        "_source": {
                            "text": "another tool content",
                            "metadata": {
                                "name": "another_tool",
                                "toolkit": "another_toolkit",
                                "description": "Another test tool",
                            },
                        },
                        "_score": 0.85,
                        "_id": "2",
                    },
                ]
            }
        }

    # Initialization and Validation Tests

    def test_initialization_success(self, tool_instance):
        """Test successful initialization of SearchAndRerankTool."""
        assert tool_instance.query == "test query"
        assert tool_instance.top_k == 5
        assert tool_instance.index_name == "tools_index"

    def test_empty_query_validation(self):
        """Test that SearchAndRerankTool raises ValueError for empty query."""
        with pytest.raises(ValueError, match="Query cannot be empty"):
            SearchAndRerankTool(query="", top_k=5, index_name="tools_index")

    def test_none_query_validation(self):
        """Test that SearchAndRerankTool raises ValueError for None query."""
        with pytest.raises(ValueError, match="Query cannot be empty"):
            SearchAndRerankTool(query=None, top_k=5, index_name="tools_index")

    def test_zero_top_k_validation(self):
        """Test that SearchAndRerankTool raises ValueError for top_k = 0."""
        with pytest.raises(ValueError, match="top_k must be a positive integer"):
            SearchAndRerankTool(query="test query", top_k=0, index_name="tools_index")

    def test_negative_top_k_validation(self):
        """Test that SearchAndRerankTool raises ValueError for negative top_k."""
        with pytest.raises(ValueError, match="top_k must be a positive integer"):
            SearchAndRerankTool(query="test query", top_k=-5, index_name="tools_index")

    def test_empty_index_name_validation(self):
        """Test that SearchAndRerankTool raises ValueError for empty index_name."""
        with pytest.raises(ValueError, match="index_name cannot be empty"):
            SearchAndRerankTool(query="test query", top_k=5, index_name="")

    def test_none_index_name_validation(self):
        """Test that SearchAndRerankTool raises ValueError for None index_name."""
        with pytest.raises(ValueError, match="index_name cannot be empty"):
            SearchAndRerankTool(query="test query", top_k=5, index_name=None)

    # Class Constants Tests

    def test_class_constants(self):
        """Test that class constants have expected values."""
        assert SearchAndRerankTool.KNN_MULTIPLIER == 3
        assert SearchAndRerankTool.MAX_KNN_CANDIDATES == 10_000
        assert SearchAndRerankTool.TEXT_SEARCH_SIZE == 100

    # execute() Method Tests

    @patch("codemie.service.search_and_rerank.tool.RRF")
    def test_execute_full_flow(self, mock_rrf, mocker, tool_instance):
        """Test complete execution flow with both KNN and text search."""
        # Mock KNN search results
        knn_doc = (
            Document(page_content="knn result", metadata={"name": "knn_tool", "toolkit": "knn_toolkit"}),
            0.95,
            "knn_id",
        )
        mocker.patch.object(tool_instance, '_knn_vector_search', return_value=[knn_doc])

        # Mock text search results
        text_doc = (
            Document(page_content="text result", metadata={"name": "text_tool", "toolkit": "text_toolkit"}),
            0.85,
            "text_id",
        )
        mocker.patch.object(tool_instance, '_text_search', return_value=[text_doc])

        # Mock RRF results
        expected_result = [Document(page_content="final result", metadata={"name": "final_tool"})]
        mock_rrf.return_value.execute.return_value = expected_result

        # Execute
        result = tool_instance.execute()

        # Assertions
        assert result == expected_result
        mock_rrf.assert_called_once_with(
            search_results=[knn_doc, text_doc],
            doc_paths=[],  # No exact path matching for tools
            top_k=5,
            exact_match_field='name',
            source_field='toolkit',
            chunk_field='name',
        )

    @patch("codemie.service.search_and_rerank.tool.RRF")
    def test_execute_with_no_results(self, mock_rrf, mocker, tool_instance):
        """Test execute when both searches return empty results."""
        # Mock empty search results
        mocker.patch.object(tool_instance, '_knn_vector_search', return_value=[])
        mocker.patch.object(tool_instance, '_text_search', return_value=[])

        # Execute
        result = tool_instance.execute()

        # Assertions
        assert result == []
        mock_rrf.assert_not_called()  # RRF should not be called with empty results

    @patch("codemie.service.search_and_rerank.tool.RRF")
    def test_execute_with_only_knn_results(self, mock_rrf, mocker, tool_instance):
        """Test execute when only KNN search returns results."""
        # Mock KNN search with results
        knn_doc = (
            Document(page_content="knn result", metadata={"name": "knn_tool"}),
            0.95,
            "knn_id",
        )
        mocker.patch.object(tool_instance, '_knn_vector_search', return_value=[knn_doc])

        # Mock empty text search
        mocker.patch.object(tool_instance, '_text_search', return_value=[])

        # Mock RRF results
        expected_result = [Document(page_content="knn result", metadata={"name": "knn_tool"})]
        mock_rrf.return_value.execute.return_value = expected_result

        # Execute
        result = tool_instance.execute()

        # Assertions
        assert result == expected_result
        mock_rrf.assert_called_once()

    @patch("codemie.service.search_and_rerank.tool.RRF")
    def test_execute_with_only_text_results(self, mock_rrf, mocker, tool_instance):
        """Test execute when only text search returns results."""
        # Mock empty KNN search
        mocker.patch.object(tool_instance, '_knn_vector_search', return_value=[])

        # Mock text search with results
        text_doc = (
            Document(page_content="text result", metadata={"name": "text_tool"}),
            0.85,
            "text_id",
        )
        mocker.patch.object(tool_instance, '_text_search', return_value=[text_doc])

        # Mock RRF results
        expected_result = [Document(page_content="text result", metadata={"name": "text_tool"})]
        mock_rrf.return_value.execute.return_value = expected_result

        # Execute
        result = tool_instance.execute()

        # Assertions
        assert result == expected_result
        mock_rrf.assert_called_once()

    # _knn_vector_search() Method Tests

    @patch("codemie.service.search_and_rerank.tool.get_embeddings_model")
    @patch("codemie.service.search_and_rerank.tool.llm_service")
    def test_knn_vector_search_success(
        self, mock_llm_service, mock_embeddings, mocker, tool_instance, es_mocked_response
    ):
        """Test successful KNN vector search."""
        # Setup mocks
        mock_llm_service.default_embedding_model = "test-embedding-model"
        mock_llm_service.get_embedding_deployment_name.return_value = "test-embedding-deployment"

        mock_embedding_instance = Mock()
        mock_embedding_instance.embed_query.return_value = [0.1, 0.2, 0.3, 0.4]
        mock_embeddings.return_value = mock_embedding_instance

        # Mock Elasticsearch
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        es_mock.return_value.search.return_value = es_mocked_response

        # Execute
        result = tool_instance._knn_vector_search()

        # Assertions
        assert isinstance(result, list)
        assert len(result) == 2
        doc, score, doc_id = result[0]
        assert isinstance(doc, Document)
        assert doc.page_content == "test tool content"
        assert doc.metadata["name"] == "test_tool"
        assert score == 0.95
        assert doc_id == "1"

        # Verify Elasticsearch was called with correct parameters
        es_mock.return_value.search.assert_called_once()
        call_args = es_mock.return_value.search.call_args[1]
        assert 'knn' in call_args
        assert call_args['knn']['field'] == 'vector'
        assert call_args['knn']['k'] == 15  # top_k * KNN_MULTIPLIER = 5 * 3
        assert call_args['knn']['num_candidates'] == 45  # min(3 * k, 10_000) = 45
        assert call_args['knn']['query_vector'] == [0.1, 0.2, 0.3, 0.4]
        assert call_args['index'] == 'tools_index'
        assert call_args['size'] == 15

    @patch("codemie.service.search_and_rerank.tool.get_embeddings_model")
    @patch("codemie.service.search_and_rerank.tool.llm_service")
    def test_knn_vector_search_with_large_top_k(self, mock_llm_service, mock_embeddings, mocker, es_mocked_response):
        """Test KNN vector search with large top_k value to verify max candidates logic."""
        # Create instance with large top_k
        tool = SearchAndRerankTool(query="test query", top_k=5000, index_name="tools_index")

        # Setup mocks
        mock_llm_service.default_embedding_model = "test-embedding-model"
        mock_llm_service.get_embedding_deployment_name.return_value = "test-embedding-deployment"

        mock_embedding_instance = Mock()
        mock_embedding_instance.embed_query.return_value = [0.1, 0.2, 0.3]
        mock_embeddings.return_value = mock_embedding_instance

        # Mock Elasticsearch
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        es_mock.return_value.search.return_value = es_mocked_response

        # Execute
        tool._knn_vector_search()

        # Verify num_candidates is capped at MAX_KNN_CANDIDATES
        call_args = es_mock.return_value.search.call_args[1]
        assert call_args['knn']['num_candidates'] == 10_000  # Should be capped at MAX_KNN_CANDIDATES

    @patch("codemie.service.search_and_rerank.tool.get_embeddings_model")
    @patch("codemie.service.search_and_rerank.tool.llm_service")
    def test_knn_vector_search_embedding_error(self, mock_llm_service, mock_embeddings, tool_instance):
        """Test KNN vector search handles embedding errors gracefully."""
        # Setup mocks to raise exception
        mock_llm_service.default_embedding_model = "test-embedding-model"
        mock_llm_service.get_embedding_deployment_name.return_value = "test-embedding-deployment"
        mock_embeddings.side_effect = Exception("Embedding error")

        # Execute
        result = tool_instance._knn_vector_search()

        # Assertions
        assert result == []

    @patch("codemie.service.search_and_rerank.tool.get_embeddings_model")
    @patch("codemie.service.search_and_rerank.tool.llm_service")
    def test_knn_vector_search_elasticsearch_error(self, mock_llm_service, mock_embeddings, mocker, tool_instance):
        """Test KNN vector search handles Elasticsearch errors gracefully."""
        # Setup mocks
        mock_llm_service.default_embedding_model = "test-embedding-model"
        mock_llm_service.get_embedding_deployment_name.return_value = "test-embedding-deployment"

        mock_embedding_instance = Mock()
        mock_embedding_instance.embed_query.return_value = [0.1, 0.2, 0.3]
        mock_embeddings.return_value = mock_embedding_instance

        # Mock Elasticsearch to raise exception
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        es_mock.return_value.search.side_effect = ApiError("ES error", meta=MagicMock(), body=MagicMock())

        # Execute
        result = tool_instance._knn_vector_search()

        # Assertions
        assert result == []

    # _text_search() Method Tests

    def test_text_search_success(self, mocker, tool_instance, es_mocked_response):
        """Test successful text search with various matching strategies."""
        # Mock Elasticsearch
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        es_mock.return_value.search.return_value = es_mocked_response

        # Execute
        result = tool_instance._text_search()

        # Assertions
        assert isinstance(result, list)
        assert len(result) == 2
        doc, score, doc_id = result[0]
        assert isinstance(doc, Document)
        assert doc.page_content == "test tool content"
        assert doc.metadata["name"] == "test_tool"

        # Verify Elasticsearch was called with correct parameters
        es_mock.return_value.search.assert_called_once()
        call_args = es_mock.return_value.search.call_args[1]
        assert 'query' in call_args
        assert call_args['index'] == 'tools_index'
        assert call_args['size'] == 100  # TEXT_SEARCH_SIZE

        # Verify query structure
        query = call_args['query']
        assert 'bool' in query
        assert 'should' in query['bool']
        assert query['bool']['minimum_should_match'] == 1

    def test_text_search_query_structure(self, mocker, tool_instance):
        """Test that text search constructs proper query with boost factors."""
        # Mock Elasticsearch
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        es_mock.return_value.search.return_value = {"hits": {"hits": []}}

        # Execute
        tool_instance._text_search()

        # Get the query that was constructed
        call_args = es_mock.return_value.search.call_args[1]
        should_clauses = call_args['query']['bool']['should']

        # Verify high-boost clauses exist
        # Exact match on tool name (boost: 5.0)
        assert any(
            clause.get('match_phrase', {}).get('metadata.name', {}).get('boost') == 5.0 for clause in should_clauses
        )

        # Match on tokenized name parts (boost: 4.0)
        assert any(
            clause.get('match', {}).get('metadata.name_tokens', {}).get('boost') == 4.0 for clause in should_clauses
        )

        # Match in description (boost: 2.0)
        assert any(
            clause.get('match_phrase', {}).get('metadata.description', {}).get('boost') == 2.0
            for clause in should_clauses
        )

        # Match toolkit name (boost: 1.5)
        assert any(clause.get('match', {}).get('metadata.toolkit', {}).get('boost') == 1.5 for clause in should_clauses)

    def test_text_search_token_matching(self, mocker):
        """Test that text search includes individual token matches for better partial matching."""
        # Create instance with multi-word query
        tool = SearchAndRerankTool(query="log ticket jira", top_k=5, index_name="tools_index")

        # Mock Elasticsearch
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        es_mock.return_value.search.return_value = {"hits": {"hits": []}}

        # Execute
        tool._text_search()

        # Get the query that was constructed
        call_args = es_mock.return_value.search.call_args[1]
        should_clauses = call_args['query']['bool']['should']

        # Verify that individual tokens are matched
        # Each token should have matches in name_tokens, name, and description
        tokens = ["log", "ticket", "jira"]

        for token in tokens:
            # Check for name_tokens match (boost: 3.0)
            assert any(
                clause.get('match', {}).get('metadata.name_tokens', {}).get('query') == token
                and clause.get('match', {}).get('metadata.name_tokens', {}).get('boost') == 3.0
                for clause in should_clauses
            )

            # Check for name match with fuzziness (boost: 2.5)
            assert any(
                clause.get('match', {}).get('metadata.name', {}).get('query') == token
                and clause.get('match', {}).get('metadata.name', {}).get('boost') == 2.5
                and clause.get('match', {}).get('metadata.name', {}).get('fuzziness') == 'AUTO'
                for clause in should_clauses
            )

            # Check for description match (boost: 1.0)
            assert any(
                clause.get('match', {}).get('metadata.description', {}).get('query') == token
                and clause.get('match', {}).get('metadata.description', {}).get('boost') == 1.0
                for clause in should_clauses
            )

    def test_text_search_skips_short_tokens(self, mocker):
        """Test that text search skips very short tokens (length <= 2)."""
        # Create instance with query containing short tokens
        tool = SearchAndRerankTool(query="a log to jira", top_k=5, index_name="tools_index")

        # Mock Elasticsearch
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        es_mock.return_value.search.return_value = {"hits": {"hits": []}}

        # Execute
        tool._text_search()

        # Get the query that was constructed
        call_args = es_mock.return_value.search.call_args[1]
        should_clauses = call_args['query']['bool']['should']

        # Verify that short tokens ("a", "to") are NOT included as individual matches
        short_tokens = ["a", "to"]
        for token in short_tokens:
            assert not any(
                clause.get('match', {}).get('metadata.name_tokens', {}).get('query') == token
                for clause in should_clauses
            )

        # Verify that longer tokens ("log", "jira") ARE included
        long_tokens = ["log", "jira"]
        for token in long_tokens:
            assert any(
                clause.get('match', {}).get('metadata.name_tokens', {}).get('query') == token
                for clause in should_clauses
            )

    def test_text_search_elasticsearch_error(self, mocker, tool_instance):
        """Test text search handles Elasticsearch errors gracefully."""
        # Mock Elasticsearch to raise exception
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        es_mock.return_value.search.side_effect = ApiError("ES error", meta=MagicMock(), body=MagicMock())

        # Execute
        result = tool_instance._text_search()

        # Assertions
        assert result == []

    # _rrf_fusion Tests

    @patch("codemie.service.search_and_rerank.tool.RRF")
    def test_rrf_fusion_calls_rrf_with_correct_params(self, mock_rrf, tool_instance):
        """_rrf_fusion delegates to RRF with empty doc_paths and fixed fields."""
        search_results = [
            (Document(page_content="tool1", metadata={"name": "t1", "toolkit": "tk1"}), 0.9, "id1"),
        ]
        expected = [Document(page_content="tool1", metadata={"name": "t1"})]
        mock_rrf.return_value.execute.return_value = expected

        result = tool_instance._rrf_fusion(search_results)

        assert result == expected
        mock_rrf.assert_called_once_with(
            search_results=search_results,
            doc_paths=[],
            top_k=tool_instance.top_k,
            exact_match_field='name',
            source_field='toolkit',
            chunk_field='name',
        )

    @patch("codemie.service.search_and_rerank.tool.RRF")
    def test_rrf_fusion_with_empty_results(self, mock_rrf, tool_instance):
        """_rrf_fusion passes empty list to RRF and returns its result."""
        mock_rrf.return_value.execute.return_value = []

        result = tool_instance._rrf_fusion([])

        assert result == []
        mock_rrf.assert_called_once_with(
            search_results=[],
            doc_paths=[],
            top_k=tool_instance.top_k,
            exact_match_field='name',
            source_field='toolkit',
            chunk_field='name',
        )

    # tokenize_tool_name() Static Method Tests

    def test_tokenize_tool_name_with_underscores(self):
        """Test tokenization of tool names with underscores."""
        result = SearchAndRerankTool.tokenize_tool_name("generic_jira_tool")
        assert result == ['generic', 'jira', 'tool']

    def test_tokenize_tool_name_with_camel_case(self):
        """Test tokenization of camelCase tool names."""
        result = SearchAndRerankTool.tokenize_tool_name("ZephyrSquad")
        assert result == ['zephyr', 'squad']

    def test_tokenize_tool_name_mixed_format(self):
        """Test tokenization of tool names with mixed formats."""
        result = SearchAndRerankTool.tokenize_tool_name("search_code_repo_by_path")
        assert result == ['search', 'code', 'repo', 'by', 'path']

    def test_tokenize_tool_name_with_camel_and_underscore(self):
        """Test tokenization of tool names with both camelCase and underscores."""
        result = SearchAndRerankTool.tokenize_tool_name("MyCustom_ToolName")
        assert result == ['my', 'custom', 'tool', 'name']

    def test_tokenize_tool_name_lowercase_preservation(self):
        """Test that tokenization converts all tokens to lowercase."""
        result = SearchAndRerankTool.tokenize_tool_name("UPPERCASE_TOOL_NAME")
        assert result == ['uppercase', 'tool', 'name']
        assert all(token.islower() for token in result)

    def test_tokenize_tool_name_single_word(self):
        """Test tokenization of single-word tool names."""
        result = SearchAndRerankTool.tokenize_tool_name("tool")
        assert result == ['tool']

    def test_tokenize_tool_name_empty_string(self):
        """Test tokenization of empty string."""
        result = SearchAndRerankTool.tokenize_tool_name("")
        assert result == []

    def test_tokenize_tool_name_multiple_underscores(self):
        """Test tokenization with multiple consecutive underscores."""
        result = SearchAndRerankTool.tokenize_tool_name("tool___name___test")
        assert result == ['tool', 'name', 'test']

    def test_tokenize_tool_name_complex_camel_case(self):
        """Test tokenization with complex camelCase patterns."""
        result = SearchAndRerankTool.tokenize_tool_name("HTTPSConnectionTool")
        # Should handle consecutive capitals
        assert 'tool' in result
        assert len(result) > 1

    # Integration Tests

    @patch("codemie.service.search_and_rerank.tool.RRF")
    def test_execute_with_large_result_sets(self, mock_rrf, mocker, tool_instance):
        """Test execution with large result sets from both searches."""
        # Create large result sets
        knn_docs = [
            (
                Document(page_content=f"knn content {i}", metadata={"name": f"knn_tool_{i}", "toolkit": "knn_toolkit"}),
                0.9 - i * 0.001,
                f"knn_{i}",
            )
            for i in range(50)
        ]

        text_docs = [
            (
                Document(
                    page_content=f"text content {i}", metadata={"name": f"text_tool_{i}", "toolkit": "text_toolkit"}
                ),
                0.8 - i * 0.001,
                f"text_{i}",
            )
            for i in range(50)
        ]

        # Mock searches
        mocker.patch.object(tool_instance, '_knn_vector_search', return_value=knn_docs)
        mocker.patch.object(tool_instance, '_text_search', return_value=text_docs)

        # Mock RRF to return top_k results
        top_k_results = [
            Document(page_content=f"top result {i}", metadata={"name": f"top_tool_{i}"})
            for i in range(tool_instance.top_k)
        ]
        mock_rrf.return_value.execute.return_value = top_k_results

        # Execute
        result = tool_instance.execute()

        # Assertions
        assert len(result) == tool_instance.top_k
        assert result == top_k_results

        # Verify RRF was called with all results
        call_args = mock_rrf.call_args[1]
        assert len(call_args['search_results']) == 100  # 50 + 50

    @patch("codemie.service.search_and_rerank.tool.RRF")
    @patch("codemie.service.search_and_rerank.tool.get_embeddings_model")
    @patch("codemie.service.search_and_rerank.tool.llm_service")
    def test_execute_end_to_end(self, mock_llm_service, mock_embeddings, mock_rrf, mocker, es_mocked_response):
        """Test complete end-to-end execution without mocking internal methods."""
        # Create instance
        tool = SearchAndRerankTool(query="jira tool", top_k=3, index_name="tools_index")

        # Setup embedding mocks
        mock_llm_service.default_embedding_model = "test-embedding-model"
        mock_llm_service.get_embedding_deployment_name.return_value = "test-embedding-deployment"

        mock_embedding_instance = Mock()
        mock_embedding_instance.embed_query.return_value = [0.1, 0.2, 0.3]
        mock_embeddings.return_value = mock_embedding_instance

        # Mock Elasticsearch
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        es_mock.return_value.search.return_value = es_mocked_response

        # Mock RRF
        expected_docs = [
            Document(page_content="tool 1", metadata={"name": "jira_tool", "toolkit": "jira"}),
            Document(page_content="tool 2", metadata={"name": "generic_jira", "toolkit": "jira"}),
        ]
        mock_rrf.return_value.execute.return_value = expected_docs

        # Execute
        result = tool.execute()

        # Assertions
        assert result == expected_docs
        assert len(result) <= tool.top_k

        # Verify both searches were executed
        assert es_mock.return_value.search.call_count >= 2  # At least KNN and text searches

    def test_query_truncation_in_logging(self, mocker, tool_instance):
        """Test that very long queries are truncated in debug logs."""
        # Create instance with very long query
        long_query = "a" * 200
        tool = SearchAndRerankTool(query=long_query, top_k=5, index_name="tools_index")

        # Mock searches to avoid actual execution
        mocker.patch.object(tool, '_knn_vector_search', return_value=[])
        mocker.patch.object(tool, '_text_search', return_value=[])

        # Mock logger to capture debug calls
        logger_mock = mocker.patch('codemie.service.search_and_rerank.tool.logger')

        # Execute
        tool.execute()

        # Verify logging was called
        logger_mock.debug.assert_called()

        # Check that query is truncated in log message
        log_calls = logger_mock.debug.call_args_list
        start_log = log_calls[0][0][0]
        assert "..." in start_log  # Query should be truncated
        assert len(long_query[:100]) < len(long_query)  # Truncated version is shorter

    # Error Handling and Edge Cases

    def test_execute_with_partial_search_failure(self, mocker, tool_instance):
        """Test execute continues when one search fails but the other succeeds."""
        # Mock KNN search to fail
        mocker.patch.object(tool_instance, '_knn_vector_search', return_value=[])

        # Mock text search to succeed
        text_doc = (
            Document(page_content="text result", metadata={"name": "text_tool"}),
            0.85,
            "text_id",
        )
        mocker.patch.object(tool_instance, '_text_search', return_value=[text_doc])

        # Mock RRF
        with patch("codemie.service.search_and_rerank.tool.RRF") as mock_rrf:
            expected_result = [Document(page_content="text result", metadata={"name": "text_tool"})]
            mock_rrf.return_value.execute.return_value = expected_result

            # Execute
            result = tool_instance.execute()

            # Assertions
            assert result == expected_result
            mock_rrf.assert_called_once()

    def test_empty_elasticsearch_response(self, mocker, tool_instance):
        """Test handling of empty Elasticsearch responses."""
        # Mock Elasticsearch to return empty response
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        es_mock.return_value.search.return_value = {"hits": {"hits": []}}

        # Execute text search
        result = tool_instance._text_search()

        # Assertions
        assert result == []

    def test_malformed_elasticsearch_response(self, mocker, tool_instance):
        """Test handling of malformed Elasticsearch responses."""
        # Mock Elasticsearch to return malformed response
        es_mock = mocker.patch(
            'codemie.service.search_and_rerank.SearchAndRerankBase.es', new_callable=mocker.PropertyMock
        )
        es_mock.return_value.search.return_value = {"invalid": "structure"}

        # Execute text search (should handle gracefully)
        result = tool_instance._text_search()

        # Assertions - should return empty list or handle error
        assert isinstance(result, list)
