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


class TestLazyLoad:
    """Tests for lazy_load method."""

    @pytest.fixture
    def loader_for_lazy(self, confluence_loader):
        """Extend the base fixture with attributes required by lazy_load."""
        from langchain_community.document_loaders.confluence import ContentFormat

        confluence_loader.content_format = ContentFormat.VIEW
        confluence_loader.max_pages = 1000
        confluence_loader.limit = 20
        confluence_loader.include_archived_content = False
        confluence_loader.include_restricted_content = False
        confluence_loader.include_attachments = False
        confluence_loader.include_comments = False
        confluence_loader.include_labels = False
        confluence_loader.ocr_languages = None
        confluence_loader.keep_markdown_format = True
        confluence_loader.keep_newlines = False
        return confluence_loader

    def test_lazy_load_single_chunk(self, loader_for_lazy):
        """Fewer than 1000 pages — paginate_request called once, all Documents yielded."""
        from langchain_core.documents import Document
        from unittest.mock import patch

        pages = [{'id': str(i)} for i in range(500)]
        docs = [Document(page_content=f"page {i}") for i in range(500)]

        with (
            patch.object(loader_for_lazy, 'paginate_request', return_value=pages) as mock_paginate,
            patch.object(loader_for_lazy, 'process_pages', return_value=docs),
        ):
            result = list(loader_for_lazy.lazy_load())

        assert len(result) == 500
        assert mock_paginate.call_count == 1
        _, kwargs = mock_paginate.call_args
        assert kwargs['start'] == 0
        assert kwargs['max_pages'] == 1000

    def test_lazy_load_multiple_chunks(self, loader_for_lazy):
        """2500 pages across 3 chunks — start=0, 1000, 2000 passed; all Documents yielded."""
        from langchain_core.documents import Document
        from unittest.mock import patch

        chunk1 = [{'id': str(i)} for i in range(1000)]
        chunk2 = [{'id': str(i)} for i in range(1000, 2000)]
        chunk3 = [{'id': str(i)} for i in range(2000, 2500)]

        docs1 = [Document(page_content=f"p{i}") for i in range(1000)]
        docs2 = [Document(page_content=f"p{i}") for i in range(1000, 2000)]
        docs3 = [Document(page_content=f"p{i}") for i in range(2000, 2500)]

        with (
            patch.object(loader_for_lazy, 'paginate_request', side_effect=[chunk1, chunk2, chunk3]) as mock_paginate,
            patch.object(loader_for_lazy, 'process_pages', side_effect=[docs1, docs2, docs3]),
        ):
            result = list(loader_for_lazy.lazy_load())

        assert len(result) == 2500
        assert mock_paginate.call_count == 3

        starts = [c.kwargs['start'] for c in mock_paginate.call_args_list]
        assert starts == [0, 1000, 2000]

    def test_lazy_load_empty_space(self, loader_for_lazy):
        """paginate_request returns empty list — lazy_load yields nothing."""
        from unittest.mock import patch

        with patch.object(loader_for_lazy, 'paginate_request', return_value=[]) as mock_paginate:
            result = list(loader_for_lazy.lazy_load())

        assert result == []
        assert mock_paginate.call_count == 1


class TestLazyLoadIntegration:
    """Integration tests for lazy_load — mocks only self.confluence HTTP client.

    Verifies the full chain: lazy_load → paginate_request → _search_content_by_cql → HTTP.
    Unit tests mock paginate_request, so they don't cover whether start=N actually
    reaches the Confluence API. These tests do.
    """

    @staticmethod
    def _make_page(page_id: str) -> dict:
        return {
            "id": page_id,
            "title": f"Page {page_id}",
            "body": {"view": {"value": f"<p>Content of page {page_id}</p>"}},
            "version": {"when": "2024-01-01"},
            "_links": {"webui": f"/pages/{page_id}"},
            "status": "current",
        }

    @pytest.fixture
    def loader_integration(self, mock_confluence_client):
        """Loader with only the HTTP client mocked — all other logic runs for real."""
        from langchain_community.document_loaders.confluence import ContentFormat

        with patch('langchain_community.document_loaders.ConfluenceLoader.__init__', return_value=None):
            loader = ConfluenceDatasourceLoader(
                url="https://confluence.example.com",
                username="test_user",
                api_key="test_api_key",
            )
        loader.confluence = mock_confluence_client
        loader.base_url = "https://confluence.example.com"
        loader.cql = "type=page AND space=TEST"
        loader.number_of_retries = 3
        loader.min_retry_seconds = 1
        loader.max_retry_seconds = 5
        loader.content_format = ContentFormat.VIEW
        loader.max_pages = 3  # small chunk size to keep mock responses manageable
        loader.limit = 3
        loader.include_archived_content = False
        loader.include_restricted_content = True  # skip is_public_page API call
        loader.include_attachments = False
        loader.include_comments = False
        loader.include_labels = False
        loader.ocr_languages = None
        loader.keep_markdown_format = False
        loader.keep_newlines = False
        return loader

    def test_start_offset_sent_per_chunk(self, loader_integration, mock_confluence_client):
        """start=0, start=3 and start=6 reach the Confluence API as chunk boundaries.

        When a chunk returns exactly chunk_size pages, lazy_load probes the next offset
        to check whether more pages exist — hence 3 HTTP calls for 2 full chunks.
        """
        chunk1 = [self._make_page(str(i)) for i in range(3)]
        chunk2 = [self._make_page(str(i)) for i in range(3, 6)]

        mock_confluence_client.get.side_effect = [
            {"results": chunk1, "_links": {}},  # chunk 1: full, probe next
            {"results": chunk2, "_links": {}},  # chunk 2: full, probe next
            {"results": [], "_links": {}},  # chunk 3: empty, stop
        ]

        result = list(loader_integration.lazy_load())

        assert len(result) == 6
        calls = mock_confluence_client.get.call_args_list
        assert len(calls) == 3
        assert calls[0].kwargs["params"]["start"] == 0
        assert calls[1].kwargs["params"]["start"] == 3
        assert calls[2].kwargs["params"]["start"] == 6

    def test_all_documents_yielded_across_chunks(self, loader_integration, mock_confluence_client):
        """All pages from all chunks are returned as Documents with correct metadata."""
        chunk1 = [self._make_page(str(i)) for i in range(3)]
        chunk2 = [self._make_page(str(i)) for i in range(3, 5)]  # partial last chunk

        mock_confluence_client.get.side_effect = [
            {"results": chunk1, "_links": {}},
            {"results": chunk2, "_links": {}},
        ]

        result = list(loader_integration.lazy_load())

        assert len(result) == 5
        titles = [doc.metadata["title"] for doc in result]
        assert titles == [f"Page {i}" for i in range(5)]
