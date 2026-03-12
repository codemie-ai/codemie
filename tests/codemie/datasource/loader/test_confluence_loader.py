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

from unittest.mock import MagicMock, patch

import pytest

from codemie.datasource.loader.confluence_loader import ConfluenceDatasourceLoader


@pytest.fixture
def mock_confluence_client():
    """Mock Confluence API client."""
    mock_client = MagicMock()
    return mock_client


@pytest.fixture
def confluence_loader(mock_confluence_client):
    """Create a ConfluenceDatasourceLoader instance with mocked Confluence client."""
    with patch('langchain_community.document_loaders.ConfluenceLoader.__init__', return_value=None):
        loader = ConfluenceDatasourceLoader(
            url="https://confluence.example.com",
            username="test_user",
            api_key="test_api_key",
        )
        loader.confluence = mock_confluence_client
        loader.cql = "type=page AND space=TEST"
        loader.number_of_retries = 3
        loader.min_retry_seconds = 1
        loader.max_retry_seconds = 5
        return loader


class TestFetchRemoteStats:
    """Tests for fetch_remote_stats method."""

    def test_fetch_remote_stats_success(self, confluence_loader, mock_confluence_client):
        """Test successful fetching of remote statistics."""
        # Arrange
        mock_response = {
            'totalSize': 42,
            'results': [],
            '_links': {},
        }
        mock_confluence_client.cql.return_value = mock_response

        # Act
        result = confluence_loader.fetch_remote_stats()

        # Assert
        assert result['documents_count_key'] == 42
        assert result['total_documents'] == 42
        assert result['skipped_documents'] == 0
        mock_confluence_client.cql.assert_called_once_with("type=page AND space=TEST", start=0, limit=1)

    def test_fetch_remote_stats_with_zero_pages(self, confluence_loader, mock_confluence_client):
        """Test fetch_remote_stats when no pages are found."""
        # Arrange
        mock_response = {
            'totalSize': 0,
            'results': [],
            '_links': {},
        }
        mock_confluence_client.cql.return_value = mock_response

        # Act
        result = confluence_loader.fetch_remote_stats()

        # Assert
        assert result['documents_count_key'] == 0
        assert result['total_documents'] == 0
        assert result['skipped_documents'] == 0

    def test_fetch_remote_stats_invalid_response_type(self, confluence_loader, mock_confluence_client):
        """Test fetch_remote_stats raises ValueError when response is not a dict."""
        # Arrange
        mock_confluence_client.cql.return_value = "invalid_response"

        # Act & Assert
        with pytest.raises(ValueError, match="Cannot retrieve data with provided configuration"):
            confluence_loader.fetch_remote_stats()

    def test_fetch_remote_stats_none_response(self, confluence_loader, mock_confluence_client):
        """Test fetch_remote_stats raises ValueError when response is None."""
        # Arrange
        mock_confluence_client.cql.return_value = None

        # Act & Assert
        with pytest.raises(ValueError, match="Cannot retrieve data with provided configuration"):
            confluence_loader.fetch_remote_stats()

    def test_fetch_remote_stats_missing_total_size(self, confluence_loader, mock_confluence_client):
        """Test fetch_remote_stats raises KeyError when totalSize is missing."""
        # Arrange
        mock_response = {
            'results': [],
            '_links': {},
        }
        mock_confluence_client.cql.return_value = mock_response

        # Act & Assert
        with pytest.raises(KeyError):
            confluence_loader.fetch_remote_stats()


class TestSearchContentByCql:
    """Tests for _search_content_by_cql method."""

    def test_search_content_by_cql_first_request(self, confluence_loader, mock_confluence_client):
        """Test _search_content_by_cql on the first request (no next_url)."""
        # Arrange
        mock_response = {
            'results': [
                {'id': '1', 'title': 'Page 1'},
                {'id': '2', 'title': 'Page 2'},
            ],
            '_links': {
                'next': '/rest/api/content/search?cql=...&start=2',
            },
        }
        mock_confluence_client.get.return_value = mock_response

        # Act
        results, next_url = confluence_loader._search_content_by_cql(
            cql="type=page AND space=TEST",
            limit=2,
            start=0,
        )

        # Assert
        assert len(results) == 2
        assert results[0]['id'] == '1'
        assert results[1]['id'] == '2'
        assert next_url == '/rest/api/content/search?cql=...&start=2'
        mock_confluence_client.get.assert_called_once_with(
            "rest/api/content/search",
            params={
                "cql": "type=page AND space=TEST",
                "limit": 2,
                "start": 0,
            },
        )

    def test_search_content_by_cql_with_next_url(self, confluence_loader, mock_confluence_client):
        """Test _search_content_by_cql with next_url parameter."""
        # Arrange
        next_url = "/rest/api/content/search?cql=...&start=2"
        mock_response = {
            'results': [
                {'id': '3', 'title': 'Page 3'},
            ],
            '_links': {},
        }
        mock_confluence_client.get.return_value = mock_response

        # Act
        results, returned_next_url = confluence_loader._search_content_by_cql(
            cql="type=page AND space=TEST",
            next_url=next_url,
        )

        # Assert
        assert len(results) == 1
        assert results[0]['id'] == '3'
        assert returned_next_url == ''
        mock_confluence_client.get.assert_called_once_with(next_url)

    def test_search_content_by_cql_include_archived_spaces(self, confluence_loader, mock_confluence_client):
        """Test _search_content_by_cql with includeArchivedSpaces parameter."""
        # Arrange
        mock_response = {
            'results': [{'id': '1', 'title': 'Archived Page'}],
            '_links': {},
        }
        mock_confluence_client.get.return_value = mock_response

        # Act
        results, next_url = confluence_loader._search_content_by_cql(
            cql="type=page",
            include_archived_spaces=True,
        )

        # Assert
        assert len(results) == 1
        assert next_url == ''
        mock_confluence_client.get.assert_called_once_with(
            "rest/api/content/search",
            params={
                "cql": "type=page",
                "includeArchivedSpaces": True,
            },
        )

    def test_search_content_by_cql_no_results(self, confluence_loader, mock_confluence_client):
        """Test _search_content_by_cql when no results are returned."""
        # Arrange
        mock_response = {
            'results': [],
            '_links': {},
        }
        mock_confluence_client.get.return_value = mock_response

        # Act
        results, next_url = confluence_loader._search_content_by_cql(cql="type=page AND space=NONEXISTENT")

        # Assert
        assert results == []
        assert next_url == ''

    def test_search_content_by_cql_missing_links(self, confluence_loader, mock_confluence_client):
        """Test _search_content_by_cql when _links is missing from response."""
        # Arrange
        mock_response = {
            'results': [{'id': '1', 'title': 'Page 1'}],
        }
        mock_confluence_client.get.return_value = mock_response

        # Act
        results, next_url = confluence_loader._search_content_by_cql(cql="type=page")

        # Assert
        assert len(results) == 1
        assert next_url == ''


class TestPaginateRequest:
    """Tests for paginate_request method."""

    def test_paginate_request_with_cql_single_page(self, confluence_loader):
        """Test paginate_request with CQL query fetching a single page."""
        # Arrange
        confluence_loader.cql = "type=page AND space=TEST"

        def mock_retrieval_method(**kwargs):
            if kwargs.get("next_url") == "":
                return (
                    [
                        {'id': '1', 'title': 'Page 1'},
                        {'id': '2', 'title': 'Page 2'},
                    ],
                    "",  # No next URL, indicating end of results
                )
            return ([], "")

        # Act
        results = confluence_loader.paginate_request(mock_retrieval_method, max_pages=10)

        # Assert
        assert len(results) == 2
        assert results[0]['id'] == '1'
        assert results[1]['id'] == '2'

    def test_paginate_request_with_cql_multiple_pages(self, confluence_loader):
        """Test paginate_request with CQL query fetching multiple pages."""
        # Arrange
        confluence_loader.cql = "type=page AND space=TEST"
        call_count = 0

        def mock_retrieval_method(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    [{'id': '1', 'title': 'Page 1'}, {'id': '2', 'title': 'Page 2'}],
                    "/rest/api/content/search?start=2",
                )
            elif call_count == 2:
                return (
                    [{'id': '3', 'title': 'Page 3'}],
                    "",  # No more pages
                )
            return ([], "")

        # Act
        results = confluence_loader.paginate_request(mock_retrieval_method, max_pages=10)

        # Assert
        assert len(results) == 3
        assert results[0]['id'] == '1'
        assert results[1]['id'] == '2'
        assert results[2]['id'] == '3'

    def test_paginate_request_with_cql_max_pages_limit(self, confluence_loader):
        """Test paginate_request respects max_pages limit with CQL."""
        # Arrange
        confluence_loader.cql = "type=page AND space=TEST"
        call_count = 0

        def mock_retrieval_method(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    [{'id': '1', 'title': 'Page 1'}, {'id': '2', 'title': 'Page 2'}],
                    "/rest/api/content/search?start=2",
                )
            elif call_count == 2:
                return (
                    [{'id': '3', 'title': 'Page 3'}, {'id': '4', 'title': 'Page 4'}],
                    "/rest/api/content/search?start=4",
                )
            return ([], "")

        # Act
        results = confluence_loader.paginate_request(mock_retrieval_method, max_pages=3)

        # Assert
        assert len(results) == 3
        assert results[0]['id'] == '1'
        assert results[1]['id'] == '2'
        assert results[2]['id'] == '3'

    def test_paginate_request_without_cql(self, confluence_loader):
        """Test paginate_request without CQL query (using start parameter)."""
        # Arrange
        confluence_loader.cql = None
        call_count = 0

        def mock_retrieval_method(**kwargs):
            nonlocal call_count
            start = kwargs.get("start", 0)
            call_count += 1
            if start == 0:
                return [{'id': '1', 'title': 'Page 1'}, {'id': '2', 'title': 'Page 2'}]
            elif start == 2:
                return [{'id': '3', 'title': 'Page 3'}]
            else:
                return []

        # Act
        results = confluence_loader.paginate_request(mock_retrieval_method, max_pages=10)

        # Assert
        assert len(results) == 3
        assert results[0]['id'] == '1'
        assert results[2]['id'] == '3'

    def test_paginate_request_without_cql_max_pages_limit(self, confluence_loader):
        """Test paginate_request respects max_pages limit without CQL."""
        # Arrange
        confluence_loader.cql = None

        def mock_retrieval_method(**kwargs):
            start = kwargs.get("start", 0)
            if start == 0:
                return [{'id': '1', 'title': 'Page 1'}, {'id': '2', 'title': 'Page 2'}]
            elif start == 2:
                return [{'id': '3', 'title': 'Page 3'}, {'id': '4', 'title': 'Page 4'}]
            return []

        # Act
        results = confluence_loader.paginate_request(mock_retrieval_method, max_pages=3)

        # Assert
        assert len(results) == 3

    def test_paginate_request_empty_results(self, confluence_loader):
        """Test paginate_request when no results are returned."""
        # Arrange
        confluence_loader.cql = "type=page AND space=NONEXISTENT"

        def mock_retrieval_method(**kwargs):
            return ([], "")

        # Act
        results = confluence_loader.paginate_request(mock_retrieval_method, max_pages=10)

        # Assert
        assert results == []

    @patch('codemie.datasource.loader.confluence_loader.logger')
    def test_paginate_request_with_retries(self, mock_logger, confluence_loader):
        """Test paginate_request retries on failure."""
        # Arrange
        confluence_loader.cql = "type=page AND space=TEST"
        confluence_loader.number_of_retries = 2
        call_count = 0

        def mock_retrieval_method(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Temporary failure")
            return (
                [{'id': '1', 'title': 'Page 1'}],
                "",
            )

        # Act
        results = confluence_loader.paginate_request(mock_retrieval_method, max_pages=10)

        # Assert
        assert len(results) == 1
        assert results[0]['id'] == '1'

    def test_paginate_request_exhausts_retries(self, confluence_loader):
        """Test paginate_request fails after exhausting retries."""
        # Arrange
        confluence_loader.cql = "type=page AND space=TEST"
        confluence_loader.number_of_retries = 2

        def mock_retrieval_method(**kwargs):
            raise Exception("Persistent failure")

        # Act & Assert
        with pytest.raises(Exception, match="Persistent failure"):
            confluence_loader.paginate_request(mock_retrieval_method, max_pages=10)

    def test_paginate_request_break_on_empty_batch_without_cql(self, confluence_loader):
        """Test paginate_request breaks when empty batch is returned (without CQL)."""
        # Arrange
        confluence_loader.cql = None
        call_count = 0

        def mock_retrieval_method(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{'id': '1', 'title': 'Page 1'}]
            return []  # Empty batch

        # Act
        results = confluence_loader.paginate_request(mock_retrieval_method, max_pages=10)

        # Assert
        assert len(results) == 1
        assert results[0]['id'] == '1'
