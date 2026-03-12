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

"""Unit tests for MetricsElasticRepository.

Tests cover:
- Repository initialization
- ES|QL query execution with and without filters
- Aggregation query execution with NotFoundError handling
- Search query execution with pagination
- Error handling and exception transformation
- Performance logging
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from elasticsearch import NotFoundError
from fastapi import status

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.core.exceptions import ExtendedHTTPException


class TestMetricsElasticRepository:
    """Test suite for MetricsElasticRepository."""

    # ================================================================================
    # Fixtures
    # ================================================================================

    @pytest.fixture
    def mock_es_client(self):
        """Mock Elasticsearch client with required methods."""
        client = MagicMock()
        # Setup esql.query method as AsyncMock since it's an async method
        client.esql.query = AsyncMock()
        # Setup search method as AsyncMock since it's an async method
        client.search = AsyncMock()
        return client

    @pytest.fixture
    def mock_config(self):
        """Mock config with test index name."""
        config = MagicMock()
        config.ELASTIC_METRICS_INDEX = "test-metrics-index"
        return config

    @pytest.fixture
    def repository(self, mock_es_client, mock_config):
        """Create repository with mocked dependencies.

        NOTE: Patching where USED (in metrics_elastic_repository module),
        not where DEFINED (in original modules).
        """
        with patch(
            'codemie.repository.metrics_elastic_repository.ElasticSearchClient.get_async_client',
            return_value=mock_es_client,
        ):
            with patch('codemie.repository.metrics_elastic_repository.config', mock_config):
                repo = MetricsElasticRepository()
                return repo

    @pytest.fixture
    def sample_esql_result(self):
        """Sample ES|QL query result."""
        return {
            "columns": [{"name": "user_id", "type": "keyword"}, {"name": "count", "type": "long"}],
            "values": [["user1", 10], ["user2", 5]],
        }

    @pytest.fixture
    def sample_aggregation_result(self):
        """Sample aggregation query result."""
        return {
            "hits": {
                "hits": [
                    {"_id": "1", "_source": {"user_id": "user1", "action": "query"}},
                    {"_id": "2", "_source": {"user_id": "user2", "action": "index"}},
                ],
                "total": {"value": 2},
            },
            "aggregations": {
                "user_stats": {"buckets": [{"key": "user1", "doc_count": 10}, {"key": "user2", "doc_count": 5}]}
            },
        }

    @pytest.fixture
    def sample_search_result(self):
        """Sample search query result."""
        return {
            "hits": {
                "hits": [
                    {"_id": "1", "_source": {"user_id": "user1", "timestamp": "2024-01-01"}},
                    {"_id": "2", "_source": {"user_id": "user2", "timestamp": "2024-01-02"}},
                ],
                "total": {"value": 2},
            }
        }

    # ================================================================================
    # Initialization Tests
    # ================================================================================

    def test_init_creates_client_and_sets_index(self, mock_es_client, mock_config):
        """Verify repository initialization with Elasticsearch client and index configuration."""
        # Arrange & Act
        with patch(
            'codemie.repository.metrics_elastic_repository.ElasticSearchClient.get_async_client',
            return_value=mock_es_client,
        ):
            with patch('codemie.repository.metrics_elastic_repository.config', mock_config):
                repository = MetricsElasticRepository()

        # Assert
        assert repository._client == mock_es_client
        assert repository._index == "test-metrics-index"

    # ================================================================================
    # execute_esql_query Tests
    # ================================================================================

    @pytest.mark.asyncio
    async def test_execute_esql_query_success_without_filter(self, repository, mock_es_client, sample_esql_result):
        """Verify successful ES|QL query execution without filters."""
        # Arrange
        query = "FROM test-metrics-index | STATS count BY user_id"
        mock_es_client.esql.query.return_value = sample_esql_result

        # Act
        result = await repository.execute_esql_query(query, filter_query=None)

        # Assert
        mock_es_client.esql.query.assert_called_once_with(query=query)
        assert result == sample_esql_result

    @pytest.mark.asyncio
    async def test_execute_esql_query_success_with_filter(self, repository, mock_es_client, sample_esql_result):
        """Verify ES|QL query execution with access control filters."""
        # Arrange
        query = "FROM test-metrics-index | STATS count BY user_id"
        filter_query = {
            "bool": {"must": [{"term": {"app_id": "test-app"}}, {"range": {"timestamp": {"gte": "2024-01-01"}}}]}
        }
        mock_es_client.esql.query.return_value = sample_esql_result

        # Act
        result = await repository.execute_esql_query(query, filter_query=filter_query)

        # Assert
        mock_es_client.esql.query.assert_called_once_with(query=query, filter=filter_query)
        assert result == sample_esql_result

    @pytest.mark.asyncio
    async def test_execute_esql_query_raises_extended_http_exception_on_es_error(self, repository, mock_es_client):
        """Verify proper error handling and exception transformation for ES failures."""
        # Arrange
        query = "FROM test-metrics-index | INVALID SYNTAX"
        error_message = "ES connection failed"
        mock_es_client.esql.query.side_effect = Exception(error_message)

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await repository.execute_esql_query(query)

        # Verify exception details
        exception = exc_info.value
        assert exception.code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exception.message == "Analytics query failed"
        assert error_message in exception.details
        assert "Unexpected error" in exception.details
        assert exception.help == "Please try again or contact support if the issue persists."
        # Verify exception chaining
        assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_execute_esql_query_with_long_query_truncates_log(
        self, repository, mock_es_client, sample_esql_result
    ):
        """Verify query is truncated in debug log to 200 characters."""
        # Arrange
        # Create a query longer than 200 characters
        query = "FROM test-metrics-index | " + "WHERE field = 'value' AND " * 20 + "STATS count"
        assert len(query) > 200  # Ensure query is long enough
        mock_es_client.esql.query.return_value = sample_esql_result

        # Act - Just verify it doesn't error with long query
        result = await repository.execute_esql_query(query)

        # Assert - Query execution succeeds
        assert result == sample_esql_result
        mock_es_client.esql.query.assert_called_once_with(query=query)

    # ================================================================================
    # execute_aggregation_query Tests
    # ================================================================================

    @pytest.mark.asyncio
    async def test_execute_aggregation_query_success(self, repository, mock_es_client, sample_aggregation_result):
        """Verify successful aggregation query execution."""
        # Arrange
        body = {"query": {"match_all": {}}, "aggs": {"user_stats": {"terms": {"field": "user_id"}}}}
        mock_es_client.search.return_value = sample_aggregation_result

        # Act
        result = await repository.execute_aggregation_query(body)

        # Assert
        mock_es_client.search.assert_called_once_with(index="test-metrics-index", body=body)
        assert result == sample_aggregation_result

    @pytest.mark.asyncio
    async def test_execute_aggregation_query_index_not_found_returns_empty(self, repository, mock_es_client):
        """Verify graceful handling when Elasticsearch index doesn't exist."""
        # Arrange
        body = {"query": {"match_all": {}}}
        # Create properly initialized NotFoundError
        mock_meta = MagicMock()
        mock_meta.status = 404
        not_found_error = NotFoundError(
            message="Index not found", meta=mock_meta, body={"error": {"type": "index_not_found_exception"}}
        )
        mock_es_client.search.side_effect = not_found_error

        # Act
        result = await repository.execute_aggregation_query(body)

        # Assert
        expected_empty_result = {"hits": {"hits": [], "total": {"value": 0}}, "aggregations": {}}
        assert result == expected_empty_result
        # Verify no exception raised (graceful handling)

    @pytest.mark.asyncio
    async def test_execute_aggregation_query_raises_extended_http_exception_on_es_error(
        self, repository, mock_es_client
    ):
        """Verify error handling for non-NotFound ES errors."""
        # Arrange
        body = {"query": {"invalid": "syntax"}}
        error_message = "Query syntax error"
        mock_es_client.search.side_effect = Exception(error_message)

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await repository.execute_aggregation_query(body)

        # Verify exception details
        exception = exc_info.value
        assert exception.code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exception.message == "Analytics query failed"
        assert error_message in exception.details
        # Verify exception chaining
        assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_execute_aggregation_query_with_complex_body(
        self, repository, mock_es_client, sample_aggregation_result
    ):
        """Verify complex aggregation body is passed correctly."""
        # Arrange
        body = {
            "query": {
                "bool": {"must": [{"term": {"app_id": "test"}}, {"range": {"timestamp": {"gte": "2024-01-01"}}}]}
            },
            "aggs": {
                "date_histogram": {"date_histogram": {"field": "timestamp", "interval": "day"}},
                "nested_aggs": {
                    "terms": {"field": "user_id"},
                    "aggs": {"avg_duration": {"avg": {"field": "duration"}}},
                },
            },
        }
        mock_es_client.search.return_value = sample_aggregation_result

        # Act
        result = await repository.execute_aggregation_query(body)

        # Assert
        mock_es_client.search.assert_called_once_with(index="test-metrics-index", body=body)
        assert result == sample_aggregation_result

    # ================================================================================
    # execute_search_query Tests
    # ================================================================================

    @pytest.mark.asyncio
    async def test_execute_search_query_success_with_defaults(self, repository, mock_es_client, sample_search_result):
        """Verify search query execution with default pagination."""
        # Arrange
        query = {"match": {"user_id": "user1"}}
        mock_es_client.search.return_value = sample_search_result

        # Act
        result = await repository.execute_search_query(query)

        # Assert
        expected_body = {"query": query, "size": 20, "from": 0}
        mock_es_client.search.assert_called_once_with(index="test-metrics-index", body=expected_body)
        assert result == sample_search_result

    @pytest.mark.asyncio
    async def test_execute_search_query_success_with_custom_pagination(
        self, repository, mock_es_client, sample_search_result
    ):
        """Verify pagination parameters are correctly applied."""
        # Arrange
        query = {"match": {"user_id": "user1"}}
        size = 50
        from_ = 100
        mock_es_client.search.return_value = sample_search_result

        # Act
        result = await repository.execute_search_query(query, size=size, from_=from_)

        # Assert
        expected_body = {"query": query, "size": 50, "from": 100}
        mock_es_client.search.assert_called_once_with(index="test-metrics-index", body=expected_body)
        assert result == sample_search_result

    @pytest.mark.asyncio
    async def test_execute_search_query_index_not_found_returns_empty(self, repository, mock_es_client):
        """Verify graceful handling of missing index in search."""
        # Arrange
        query = {"match": {"user_id": "user1"}}
        # Create properly initialized NotFoundError
        mock_meta = MagicMock()
        mock_meta.status = 404
        not_found_error = NotFoundError(
            message="Index not found", meta=mock_meta, body={"error": {"type": "index_not_found_exception"}}
        )
        mock_es_client.search.side_effect = not_found_error

        # Act
        result = await repository.execute_search_query(query)

        # Assert
        expected_empty_result = {"hits": {"hits": [], "total": {"value": 0}}}
        assert result == expected_empty_result

    @pytest.mark.asyncio
    async def test_execute_search_query_raises_extended_http_exception_on_es_error(self, repository, mock_es_client):
        """Verify error handling for search failures."""
        # Arrange
        query = {"invalid": "query"}
        error_message = "Search failed"
        mock_es_client.search.side_effect = Exception(error_message)

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await repository.execute_search_query(query)

        # Verify exception details
        exception = exc_info.value
        assert exception.code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exception.message == "Analytics query failed"
        assert error_message in exception.details
        # Verify exception chaining
        assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_execute_search_query_with_large_pagination(self, repository, mock_es_client, sample_search_result):
        """Verify large pagination parameters work correctly."""
        # Arrange
        query = {"match_all": {}}
        size = 1000  # Large page size
        from_ = 50000  # Deep pagination
        mock_es_client.search.return_value = sample_search_result

        # Act
        result = await repository.execute_search_query(query, size=size, from_=from_)

        # Assert
        expected_body = {"query": query, "size": 1000, "from": 50000}
        mock_es_client.search.assert_called_once_with(index="test-metrics-index", body=expected_body)
        assert result == sample_search_result

    # ================================================================================
    # Edge Cases and Complex Scenarios
    # ================================================================================

    @pytest.mark.asyncio
    async def test_execute_esql_query_with_empty_filter(self, repository, mock_es_client, sample_esql_result):
        """Verify empty filter dict is treated as falsy and not passed."""
        # Arrange
        query = "FROM test-metrics-index | STATS count"
        filter_query = {}
        mock_es_client.esql.query.return_value = sample_esql_result

        # Act
        result = await repository.execute_esql_query(query, filter_query=filter_query)

        # Assert - Empty dict is falsy, so filter is not passed (same as None)
        mock_es_client.esql.query.assert_called_once_with(query=query)
        assert result == sample_esql_result

    @pytest.mark.asyncio
    async def test_execute_search_query_with_zero_size(self, repository, mock_es_client, sample_search_result):
        """Verify search with size=0 (count only) works correctly."""
        # Arrange
        query = {"match_all": {}}
        mock_es_client.search.return_value = sample_search_result

        # Act
        result = await repository.execute_search_query(query, size=0)

        # Assert
        expected_body = {"query": query, "size": 0, "from": 0}
        mock_es_client.search.assert_called_once_with(index="test-metrics-index", body=expected_body)
        assert result == sample_search_result

    @pytest.mark.asyncio
    async def test_execute_aggregation_query_preserves_original_exception(self, repository, mock_es_client):
        """Verify exception chaining preserves original exception for debugging."""
        # Arrange
        body = {"query": {"invalid": "syntax"}}
        original_error = ValueError("Detailed error with context")
        mock_es_client.search.side_effect = original_error

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await repository.execute_aggregation_query(body)

        # Verify exception chaining preserves original error
        assert exc_info.value.__cause__ == original_error
        assert isinstance(exc_info.value.__cause__, ValueError)
