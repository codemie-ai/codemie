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
from unittest.mock import MagicMock, patch
from langchain_core.documents import Document

from codemie.datasource.loader.azure_devops_wiki_loader import AzureDevOpsWikiLoader
from codemie.datasource.exceptions import MissingIntegrationException, UnauthorizedException


@pytest.fixture
def mock_wiki_client():
    """Mock WikiClient for testing"""
    mock_client = MagicMock()
    return mock_client


@pytest.fixture
def mock_connection(mock_wiki_client):
    """Mock Azure DevOps Connection"""
    mock_conn = MagicMock()
    mock_conn.clients.get_wiki_client.return_value = mock_wiki_client
    return mock_conn


@pytest.fixture
def loader():
    """Create a basic loader instance"""
    return AzureDevOpsWikiLoader(
        base_url="https://dev.azure.com",
        wiki_query="*",
        access_token="fake-token",
        organization="test-org",
        project="test-project",
        wiki_identifier=None,
    )


@pytest.fixture
def loader_with_wiki_filter():
    """Create a loader with wiki_identifier filter"""
    return AzureDevOpsWikiLoader(
        base_url="https://dev.azure.com",
        wiki_query="/docs/*",
        access_token="fake-token",
        organization="test-org",
        project="test-project",
        wiki_identifier="test.wiki",
    )


def test_loader_initialization(loader):
    """Test loader initializes with correct properties"""
    assert loader.base_url == "https://dev.azure.com/test-org"
    assert loader.wiki_query == "*"
    assert loader.access_token == "fake-token"
    assert loader.organization == "test-org"
    assert loader.project == "test-project"
    assert loader.wiki_identifier is None


def test_loader_initialization_with_organization_in_url():
    """Test that organization is not duplicated in base_url"""
    loader = AzureDevOpsWikiLoader(
        base_url="https://dev.azure.com/test-org",
        wiki_query="*",
        access_token="fake-token",
        organization="test-org",
        project="test-project",
    )
    assert loader.base_url == "https://dev.azure.com/test-org"


def test_loader_initialization_trailing_slash():
    """Test that trailing slash is removed from base_url"""
    loader = AzureDevOpsWikiLoader(
        base_url="https://dev.azure.com/",
        wiki_query="*",
        access_token="fake-token",
        organization="test-org",
        project="test-project",
    )
    assert loader.base_url == "https://dev.azure.com/test-org"
    assert not loader.base_url.endswith("/")


@patch("codemie.datasource.loader.azure_devops_wiki_loader.Connection")
def test_init_client(mock_connection_class, loader):
    """Test client initialization"""
    mock_conn = MagicMock()
    mock_connection_class.return_value = mock_conn

    loader._init_client()

    assert loader._connection is not None
    assert loader._wiki_client is not None
    mock_connection_class.assert_called_once()
    mock_conn.clients.get_wiki_client.assert_called_once()


def test_validate_creds_missing_token():
    """Test validation fails when access token is missing"""
    loader = AzureDevOpsWikiLoader(
        base_url="https://dev.azure.com",
        wiki_query="*",
        access_token="",
        organization="test-org",
        project="test-project",
    )

    with pytest.raises(MissingIntegrationException):
        loader._validate_creds()


@patch("codemie.datasource.loader.azure_devops_wiki_loader.Connection")
def test_validate_creds_invalid_credentials(mock_connection_class, loader):
    """Test validation fails with invalid credentials"""
    mock_conn = MagicMock()
    mock_wiki_client = MagicMock()
    mock_wiki_client.get_all_wikis.side_effect = Exception("Authentication failed")
    mock_conn.clients.get_wiki_client.return_value = mock_wiki_client
    mock_connection_class.return_value = mock_conn

    loader._init_client()

    with pytest.raises(UnauthorizedException, match="AzureDevOps Wiki"):
        loader._validate_creds()


def test_should_process_wiki(loader):
    """Test wiki filtering logic"""
    # No filter - should process all wikis
    assert loader._should_process_wiki("any-wiki") is True
    assert loader._should_process_wiki("another-wiki") is True


def test_should_process_wiki_with_filter(loader_with_wiki_filter):
    """Test wiki filtering with wiki_identifier"""
    assert loader_with_wiki_filter._should_process_wiki("test.wiki") is True
    assert loader_with_wiki_filter._should_process_wiki("other.wiki") is False


def test_matches_query_wildcard(loader):
    """Test query matching with wildcard"""
    loader.wiki_query = "*"
    assert loader._matches_query("/any/path") is True
    assert loader._matches_query("/") is True


def test_matches_query_empty(loader):
    """Test query matching with empty string"""
    loader.wiki_query = ""
    assert loader._matches_query("/any/path") is True


def test_matches_query_prefix(loader):
    """Test query matching with prefix pattern"""
    loader.wiki_query = "/docs/*"
    assert loader._matches_query("/docs/page1") is True
    assert loader._matches_query("/docs/subfolder/page2") is True
    assert loader._matches_query("/other/page") is False


def test_matches_query_exact(loader):
    """Test query matching with exact path"""
    loader.wiki_query = "/exact/path"
    assert loader._matches_query("/exact/path") is True
    assert loader._matches_query("/exact/path/subpage") is False
    assert loader._matches_query("/other/path") is False


def test_transform_to_doc(loader):
    """Test transformation of Azure DevOps page to Langchain Document"""
    page = {
        "id": 123,
        "path": "/Test Page/Subpage",
        "content": "This is page content",
        "wiki_name": "test.wiki",
        "wiki_id": "wiki-123",
        "order": 1,
        "git_item_path": "/Test-Page/Subpage.md",
        "remote_url": "https://dev.azure.com/org/project/_git/test.wiki",
    }

    doc = loader._transform_to_doc(page)

    assert isinstance(doc, Document)
    assert doc.page_content == "This is page content"
    assert doc.metadata["page_id"] == 123
    assert doc.metadata["page_path"] == "/Test Page/Subpage"
    assert doc.metadata["wiki_name"] == "test.wiki"
    assert doc.metadata["wiki_id"] == "wiki-123"
    assert doc.metadata["order"] == 1
    assert doc.metadata["git_item_path"] == "/Test-Page/Subpage.md"
    assert doc.metadata["remote_url"] == "https://dev.azure.com/org/project/_git/test.wiki"

    # Check URL construction with hyphens instead of spaces
    expected_url = "https://dev.azure.com/test-org/test-project/_wiki/wikis/test.wiki/123/Subpage"
    assert doc.metadata["source"] == expected_url


def test_transform_to_doc_with_spaces_in_name(loader):
    """Test URL construction replaces spaces with hyphens"""
    page = {
        "id": 456,
        "path": "/This is a page",
        "content": "Content",
        "wiki_name": "test.wiki",
        "wiki_id": "wiki-123",
    }

    doc = loader._transform_to_doc(page)

    # Page name should have hyphens, not spaces
    expected_url = "https://dev.azure.com/test-org/test-project/_wiki/wikis/test.wiki/456/This-is-a-page"
    assert doc.metadata["source"] == expected_url


def test_get_wikis(loader, mock_wiki_client):
    """Test fetching wikis from Azure DevOps"""
    loader._wiki_client = mock_wiki_client

    mock_wiki1 = MagicMock()
    mock_wiki1.as_dict.return_value = {"id": "wiki-1", "name": "wiki1.wiki"}
    mock_wiki2 = MagicMock()
    mock_wiki2.as_dict.return_value = {"id": "wiki-2", "name": "wiki2.wiki"}

    mock_wiki_client.get_all_wikis.return_value = [mock_wiki1, mock_wiki2]

    wikis = loader._get_wikis()

    assert len(wikis) == 2
    assert wikis[0]["id"] == "wiki-1"
    assert wikis[1]["id"] == "wiki-2"
    mock_wiki_client.get_all_wikis.assert_called_once_with(project="test-project")


def test_get_page_content(loader, mock_wiki_client):
    """Test fetching page content"""
    loader._wiki_client = mock_wiki_client

    mock_page = MagicMock()
    mock_page.content = "Page content here"

    mock_response = MagicMock()
    mock_response.page = mock_page

    mock_wiki_client.get_page.return_value = mock_response

    content = loader._get_page_content("wiki-123", 456, "/test/path")

    assert content == "Page content here"
    mock_wiki_client.get_page.assert_called_once_with(
        project="test-project",
        wiki_identifier="wiki-123",
        path="/test/path",
        include_content=True,
        recursion_level="none",
    )


def test_get_page_content_no_content(loader, mock_wiki_client):
    """Test fetching page with no content"""
    loader._wiki_client = mock_wiki_client

    mock_page = MagicMock()
    mock_page.content = None

    mock_response = MagicMock()
    mock_response.page = mock_page

    mock_wiki_client.get_page.return_value = mock_response

    content = loader._get_page_content("wiki-123", 456, "/test/path")

    assert content == ""


def test_get_page_content_error(loader, mock_wiki_client):
    """Test error handling when fetching page content"""
    loader._wiki_client = mock_wiki_client
    mock_wiki_client.get_page.side_effect = Exception("API error")

    content = loader._get_page_content("wiki-123", 456, "/test/path")

    assert content == ""


def test_get_all_page_paths(loader, mock_wiki_client):
    """Test collecting all page paths from wiki tree"""
    loader._wiki_client = mock_wiki_client

    # Create a mock page tree structure
    mock_subpage2 = MagicMock()
    mock_subpage2.path = "/docs/subpage2"
    mock_subpage2.sub_pages = None

    mock_subpage1 = MagicMock()
    mock_subpage1.path = "/docs/subpage1"
    mock_subpage1.sub_pages = [mock_subpage2]

    mock_root = MagicMock()
    mock_root.path = "/"
    mock_root.sub_pages = [mock_subpage1]

    mock_response = MagicMock()
    mock_response.page = mock_root

    mock_wiki_client.get_page.return_value = mock_response

    paths = loader._get_all_page_paths("wiki-123")

    assert len(paths) == 2
    assert "/docs/subpage1" in paths
    assert "/docs/subpage2" in paths
    assert "/" not in paths  # Root should be excluded

    mock_wiki_client.get_page.assert_called_once_with(
        project="test-project",
        wiki_identifier="wiki-123",
        path="/",
        recursion_level="full",
        include_content=False,
    )


def test_count_matching_paths(loader):
    """Test counting pages that match the query"""
    loader.wiki_query = "/docs/*"

    # Create a mock page tree
    mock_subpage2 = MagicMock()
    mock_subpage2.path = "/other/page"
    mock_subpage2.sub_pages = None

    mock_subpage1 = MagicMock()
    mock_subpage1.path = "/docs/page1"
    mock_subpage1.sub_pages = None

    mock_root = MagicMock()
    mock_root.path = "/"
    mock_root.sub_pages = [mock_subpage1, mock_subpage2]

    count = loader._count_matching_paths(mock_root)

    # Should only count /docs/page1
    assert count == 1


def test_fetch_page_with_content(loader, mock_wiki_client):
    """Test fetching a single page with its content"""
    loader._wiki_client = mock_wiki_client

    # Mock metadata fetch
    mock_page_meta = MagicMock()
    mock_page_meta.id = 123
    mock_page_meta.path = "/test/page"
    mock_page_meta.as_dict.return_value = {"id": 123, "path": "/test/page", "order": 1}

    mock_meta_response = MagicMock()
    mock_meta_response.page = mock_page_meta

    # Mock content fetch
    mock_page_content = MagicMock()
    mock_page_content.content = "Page content"

    mock_content_response = MagicMock()
    mock_content_response.page = mock_page_content

    # Setup different return values for different calls
    mock_wiki_client.get_page.side_effect = [mock_meta_response, mock_content_response]

    page_data = loader._fetch_page_with_content("wiki-123", "/test/page", "test.wiki", "")

    assert page_data is not None
    assert page_data["id"] == 123
    assert page_data["path"] == "/test/page"
    assert page_data["content"] == "Page content"
    assert page_data["wiki_name"] == "test.wiki"
    assert page_data["wiki_id"] == "wiki-123"


def test_fetch_page_with_content_no_id(loader, mock_wiki_client):
    """Test handling of page without ID"""
    loader._wiki_client = mock_wiki_client

    mock_page = MagicMock()
    mock_page.id = None

    mock_response = MagicMock()
    mock_response.page = mock_page

    mock_wiki_client.get_page.return_value = mock_response

    page_data = loader._fetch_page_with_content("wiki-123", "/test/page", "test.wiki", "")

    assert page_data is None


def test_fetch_page_with_content_error(loader, mock_wiki_client):
    """Test error handling when fetching page"""
    loader._wiki_client = mock_wiki_client
    mock_wiki_client.get_page.side_effect = Exception("API error")

    page_data = loader._fetch_page_with_content("wiki-123", "/test/page", "test.wiki", "")

    assert page_data is None


@patch("codemie.datasource.loader.azure_devops_wiki_loader.Connection")
def test_fetch_remote_stats(mock_connection_class, loader, mock_wiki_client):
    """Test fetching remote statistics"""
    mock_conn = MagicMock()
    mock_conn.clients.get_wiki_client.return_value = mock_wiki_client
    mock_connection_class.return_value = mock_conn

    # Mock wikis
    mock_wiki1 = MagicMock()
    mock_wiki1.as_dict.return_value = {"id": "wiki-1", "name": "wiki1.wiki"}
    mock_wiki_client.get_all_wikis.return_value = [mock_wiki1]

    # Mock page tree
    mock_page1 = MagicMock()
    mock_page1.path = "/page1"
    mock_page1.sub_pages = None

    mock_page2 = MagicMock()
    mock_page2.path = "/page2"
    mock_page2.sub_pages = None

    mock_root = MagicMock()
    mock_root.path = "/"
    mock_root.sub_pages = [mock_page1, mock_page2]

    mock_response = MagicMock()
    mock_response.page = mock_root

    mock_wiki_client.get_page.return_value = mock_response

    stats = loader.fetch_remote_stats()

    assert stats[AzureDevOpsWikiLoader.DOCUMENTS_COUNT_KEY] == 2
    assert stats[AzureDevOpsWikiLoader.TOTAL_DOCUMENTS_KEY] == 2
    assert stats[AzureDevOpsWikiLoader.SKIPPED_DOCUMENTS_KEY] == 0


@patch("codemie.datasource.loader.azure_devops_wiki_loader.Connection")
def test_fetch_remote_stats_with_filter(mock_connection_class, loader_with_wiki_filter, mock_wiki_client):
    """Test fetching remote statistics with wiki filter"""
    mock_conn = MagicMock()
    mock_conn.clients.get_wiki_client.return_value = mock_wiki_client
    mock_connection_class.return_value = mock_conn

    # Mock multiple wikis
    mock_wiki1 = MagicMock()
    mock_wiki1.as_dict.return_value = {"id": "wiki-1", "name": "test.wiki"}
    mock_wiki2 = MagicMock()
    mock_wiki2.as_dict.return_value = {"id": "wiki-2", "name": "other.wiki"}
    mock_wiki_client.get_all_wikis.return_value = [mock_wiki1, mock_wiki2]

    # Mock page tree for test.wiki (only this should be counted)
    mock_page1 = MagicMock()
    mock_page1.path = "/docs/page1"
    mock_page1.sub_pages = None

    mock_root = MagicMock()
    mock_root.path = "/"
    mock_root.sub_pages = [mock_page1]

    mock_response = MagicMock()
    mock_response.page = mock_root

    mock_wiki_client.get_page.return_value = mock_response

    stats = loader_with_wiki_filter.fetch_remote_stats()

    # Should only count pages from test.wiki that match /docs/* query
    assert stats[AzureDevOpsWikiLoader.DOCUMENTS_COUNT_KEY] == 1


@patch("codemie.datasource.loader.azure_devops_wiki_loader.Connection")
def test_fetch_remote_stats_error(mock_connection_class, loader, mock_wiki_client):
    """Test error handling in fetch_remote_stats"""
    mock_conn = MagicMock()
    mock_conn.clients.get_wiki_client.return_value = mock_wiki_client
    mock_connection_class.return_value = mock_conn

    # Mock get_all_wikis to succeed for validation, but then fail for actual stats
    mock_wiki_client.get_all_wikis.side_effect = [
        # First call succeeds for validation
        [MagicMock(as_dict=lambda: {"id": "wiki-1", "name": "test.wiki"})],
        # Second call fails
        Exception("API error"),
    ]

    stats = loader.fetch_remote_stats()

    assert stats[AzureDevOpsWikiLoader.DOCUMENTS_COUNT_KEY] == 0
    assert stats[AzureDevOpsWikiLoader.TOTAL_DOCUMENTS_KEY] == 0
    assert stats[AzureDevOpsWikiLoader.SKIPPED_DOCUMENTS_KEY] == 0


@patch("codemie.datasource.loader.azure_devops_wiki_loader.Connection")
def test_lazy_load(mock_connection_class, loader, mock_wiki_client):
    """Test lazy loading of wiki pages"""
    mock_conn = MagicMock()
    mock_conn.clients.get_wiki_client.return_value = mock_wiki_client
    mock_connection_class.return_value = mock_conn

    # Mock wikis
    mock_wiki1 = MagicMock()
    mock_wiki1.as_dict.return_value = {"id": "wiki-1", "name": "wiki1.wiki"}
    mock_wiki_client.get_all_wikis.return_value = [mock_wiki1]

    # Mock page tree
    mock_page1 = MagicMock()
    mock_page1.path = "/page1"
    mock_page1.sub_pages = None

    mock_root = MagicMock()
    mock_root.path = "/"
    mock_root.sub_pages = [mock_page1]

    mock_tree_response = MagicMock()
    mock_tree_response.page = mock_root

    # Mock page metadata
    mock_page_meta = MagicMock()
    mock_page_meta.id = 123
    mock_page_meta.as_dict.return_value = {"id": 123, "path": "/page1"}

    mock_meta_response = MagicMock()
    mock_meta_response.page = mock_page_meta

    # Mock page content
    mock_page_content = MagicMock()
    mock_page_content.content = "Page content"

    mock_content_response = MagicMock()
    mock_content_response.page = mock_page_content

    # Setup different return values for different calls
    mock_wiki_client.get_page.side_effect = [mock_tree_response, mock_meta_response, mock_content_response]

    docs = list(loader.lazy_load())

    assert len(docs) == 1
    assert isinstance(docs[0], Document)
    assert docs[0].page_content == "Page content"
    assert docs[0].metadata["page_id"] == 123
    assert docs[0].metadata["wiki_name"] == "wiki1.wiki"


def test_create_stats_response(loader):
    """Test stats response creation"""
    stats = loader._create_stats_response(42)

    assert stats[AzureDevOpsWikiLoader.DOCUMENTS_COUNT_KEY] == 42
    assert stats[AzureDevOpsWikiLoader.TOTAL_DOCUMENTS_KEY] == 42
    assert stats[AzureDevOpsWikiLoader.SKIPPED_DOCUMENTS_KEY] == 0


@pytest.fixture
def loader_with_session(loader):
    """Loader with a mocked HTTP session"""
    loader._session = MagicMock()
    return loader


def test_init_session(loader):
    """_init_session creates a requests.Session with Authorization header"""
    loader._init_session()

    assert loader._session is not None


def test_get_rest_api_base_url(loader):
    """_get_rest_api_base_url returns the correct REST API base URL"""
    result = loader._get_rest_api_base_url()

    assert result == "https://dev.azure.com/test-org/test-project/_apis"


def test_create_auth_header(loader):
    """_create_auth_header returns a dict with a valid Basic auth header"""
    header = loader._create_auth_header()

    assert "Authorization" in header
    assert header["Authorization"].startswith("Basic ")
    assert header["Content-Type"] == "application/json"
    # Verify the token is Base64-encoded
    import base64

    expected_token = base64.b64encode(b":fake-token").decode()
    assert header["Authorization"] == f"Basic {expected_token}"


def test_get_page_comments_no_session(loader):
    """Returns empty list when no session is initialised"""
    loader._session = None
    result = loader._get_page_comments("wiki-123", 456)

    assert result == []


def test_get_page_comments_success(loader_with_session):
    """Parses comment data from API response"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "comments": [
            {
                "id": 1,
                "text": "Great page!",
                "createdBy": {"displayName": "John Doe"},
                "createdDate": "2024-01-01T00:00:00Z",
                "modifiedDate": "2024-01-01T00:00:00Z",
                "parentId": None,
            }
        ]
    }
    loader_with_session._session.get.return_value = mock_response

    result = loader_with_session._get_page_comments("wiki-123", 456)

    assert len(result) == 1
    assert result[0]["comment_id"] == 1
    assert result[0]["content"] == "Great page!"
    assert result[0]["author"] == "John Doe"
    assert result[0]["created_date"] == "2024-01-01T00:00:00Z"


def test_get_page_comments_empty_response(loader_with_session):
    """Returns empty list when API returns no comments"""
    mock_response = MagicMock()
    mock_response.json.return_value = {"comments": []}
    loader_with_session._session.get.return_value = mock_response

    result = loader_with_session._get_page_comments("wiki-123", 456)

    assert result == []


def test_get_page_comments_http_error(loader_with_session):
    """Returns empty list on HTTP errors"""
    loader_with_session._session.get.side_effect = Exception("Connection refused")

    result = loader_with_session._get_page_comments("wiki-123", 456)

    assert result == []


def test_build_comments_doc_empty_list(loader):
    """Returns None for an empty comments list"""
    page = {"id": 123, "path": "/test", "wiki_name": "test.wiki", "wiki_id": "wiki-123"}
    result = loader._build_comments_doc(page, [])

    assert result is None


def test_build_comments_doc_all_blank_content(loader):
    """Returns None when all comments have blank text"""
    page = {"id": 123, "path": "/test", "wiki_name": "test.wiki", "wiki_id": "wiki-123"}
    comments = [{"author": "Alice", "created_date": "2024-01-01", "content": "   "}]

    result = loader._build_comments_doc(page, comments)

    assert result is None


def test_build_comments_doc_with_comments(loader):
    """Creates a Document with formatted comment text and correct metadata"""
    page = {"id": 123, "path": "/Docs/Architecture", "wiki_name": "test.wiki", "wiki_id": "wiki-123"}
    comments = [
        {"author": "Alice", "created_date": "2024-01-01", "content": "Looks good!"},
        {"author": "Bob", "created_date": "2024-01-02", "content": "Thanks!"},
    ]

    result = loader._build_comments_doc(page, comments)

    assert result is not None
    assert isinstance(result, Document)
    assert "Looks good!" in result.page_content
    assert "Thanks!" in result.page_content
    assert "Alice" in result.page_content
    assert result.metadata["content_type"] == "comments"
    assert result.metadata["page_id"] == 123
    assert result.metadata["wiki_name"] == "test.wiki"
    assert result.metadata["wiki_id"] == "wiki-123"
    assert "test.wiki" in result.metadata["source"]
    # Verify comments have a unique source URL with #comments suffix
    assert result.metadata["source"].endswith("#comments")
    # Verify summary field is added for LLM routing
    assert "summary" in result.metadata
    assert "Comments" in result.metadata["summary"]
    assert "Alice" in result.metadata["summary"] or "Bob" in result.metadata["summary"]


def test_extract_wiki_attachment_paths_empty_content(loader):
    """Returns empty list for empty content"""
    assert loader._extract_wiki_attachment_paths("") == []


def test_extract_wiki_attachment_paths_no_markdown_attachments(loader):
    """Returns empty list when no .attachments links are present"""
    content = "Plain text with a [link](https://example.com)"
    assert loader._extract_wiki_attachment_paths(content) == []


def test_extract_wiki_attachment_paths_single(loader):
    """Extracts a single attachment path"""
    content = "See this ![diagram](/.attachments/diagram.png)"
    result = loader._extract_wiki_attachment_paths(content)

    assert result == ["/.attachments/diagram.png"]


def test_extract_wiki_attachment_paths_multiple(loader):
    """Extracts multiple distinct attachment paths"""
    content = "![img1](/.attachments/img1.png)\nSee also ![img2](/.attachments/img2.jpg)"
    result = loader._extract_wiki_attachment_paths(content)

    assert "/.attachments/img1.png" in result
    assert "/.attachments/img2.jpg" in result
    assert len(result) == 2


def test_extract_wiki_attachment_paths_deduplication(loader):
    """Duplicate paths are returned only once"""
    content = "![a](/.attachments/img.png)\n![b](/.attachments/img.png)"
    result = loader._extract_wiki_attachment_paths(content)

    assert result == ["/.attachments/img.png"]


def test_extract_wiki_attachment_paths_leading_slash_normalisation(loader):
    """Paths without a leading slash are normalised"""
    content = "![img](.attachments/img.png)"
    result = loader._extract_wiki_attachment_paths(content)

    assert result == ["/.attachments/img.png"]


def test_get_wiki_attachments_index_no_session(loader):
    """Returns empty dict when session is not initialised"""
    loader._session = None
    result = loader._get_wiki_attachments_index("wiki-123", "repo-456")

    assert result == {}


def test_get_wiki_attachments_index_cache_hit(loader):
    """Returns cached result on second call without making HTTP request"""
    cached = {"/.attachments/test.png": {"name": "test.png"}}
    loader._attachments_index_cache["wiki-123"] = cached

    result = loader._get_wiki_attachments_index("wiki-123", "repo-456")

    assert result is cached


def test_get_wiki_attachments_index_success(loader_with_session):
    """Builds index from API response and caches it"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "value": [
            {
                "path": "/.attachments/document.pdf",
                "isFolder": False,
                "size": 2048,
                "url": "https://example.com/download",
                "objectId": "abc123",
            }
        ]
    }
    loader_with_session._session.get.return_value = mock_response

    result = loader_with_session._get_wiki_attachments_index("wiki-123", "repo-456")

    assert "/.attachments/document.pdf" in result
    entry = result["/.attachments/document.pdf"]
    assert entry["name"] == "document.pdf"
    assert entry["object_id"] == "abc123"
    assert entry["size"] == 2048
    # Should also be accessible without leading slash
    assert ".attachments/document.pdf" in result
    # Result should be cached
    assert "wiki-123" in loader_with_session._attachments_index_cache


def test_get_wiki_attachments_index_skips_folders(loader_with_session):
    """Folder entries from the API are skipped"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "value": [
            {"path": "/.attachments", "isFolder": True},
            {"path": "/.attachments/file.png", "isFolder": False, "objectId": "xyz"},
        ]
    }
    loader_with_session._session.get.return_value = mock_response

    result = loader_with_session._get_wiki_attachments_index("wiki-123", "repo-456")

    assert "/.attachments" not in result
    assert "/.attachments/file.png" in result


def test_get_wiki_attachments_index_http_error(loader_with_session):
    """Returns empty dict (and still caches it) on HTTP error"""
    loader_with_session._session.get.side_effect = Exception("Server error")

    result = loader_with_session._get_wiki_attachments_index("wiki-123", "repo-456")

    assert result == {}


def test_download_wiki_attachment_no_object_id(loader):
    """Returns None when object_id is None"""
    assert loader._download_wiki_attachment("repo-456", None) is None


def test_download_wiki_attachment_no_repo_id(loader):
    """Returns None when repository_id is empty"""
    assert loader._download_wiki_attachment("", "obj-123") is None


def test_download_wiki_attachment_no_session(loader):
    """Returns None when session is not initialised"""
    loader._session = None
    assert loader._download_wiki_attachment("repo-456", "obj-123") is None


def test_download_wiki_attachment_success(loader_with_session):
    """Returns bytes on successful download"""
    mock_response = MagicMock()
    mock_response.content = b"binary data here"
    loader_with_session._session.get.return_value = mock_response

    result = loader_with_session._download_wiki_attachment("repo-456", "obj-123")

    assert result == b"binary data here"


def test_download_wiki_attachment_empty_content(loader_with_session):
    """Returns None when server returns empty body"""
    mock_response = MagicMock()
    mock_response.content = b""
    loader_with_session._session.get.return_value = mock_response

    result = loader_with_session._download_wiki_attachment("repo-456", "obj-123")

    assert result is None


def test_download_wiki_attachment_http_error(loader_with_session):
    """Returns None on HTTP error"""
    loader_with_session._session.get.side_effect = Exception("Timeout")

    result = loader_with_session._download_wiki_attachment("repo-456", "obj-123")

    assert result is None


def test_build_attachment_doc_no_object_id(loader):
    """Returns None when attachment has no object_id"""
    page = {"id": 123, "path": "/test", "wiki_name": "test.wiki"}
    attachment = {"name": "file.pdf", "path": "/.attachments/file.pdf", "content_type": "application/pdf"}

    result = loader._build_attachment_doc(page, attachment, "repo-456")

    assert result is None


def test_build_attachment_doc_download_fails(loader):
    """Returns None when attachment download returns None"""
    page = {"id": 123, "path": "/test", "wiki_name": "test.wiki", "wiki_id": "wiki-123"}
    attachment = {
        "name": "file.pdf",
        "path": "/.attachments/file.pdf",
        "content_type": "application/pdf",
        "object_id": "abc123",
    }
    loader._download_wiki_attachment = MagicMock(return_value=None)

    result = loader._build_attachment_doc(page, attachment, "repo-456")

    assert result is None


def test_build_attachment_doc_no_text_extracted(loader):
    """Returns None when no text can be extracted from the attachment"""
    page = {"id": 123, "path": "/test", "wiki_name": "test.wiki", "wiki_id": "wiki-123"}
    attachment = {
        "name": "file.pdf",
        "path": "/.attachments/file.pdf",
        "content_type": "application/pdf",
        "object_id": "abc123",
    }
    loader._download_wiki_attachment = MagicMock(return_value=b"content")
    loader._extract_attachment_text = MagicMock(return_value="   ")  # blank text

    result = loader._build_attachment_doc(page, attachment, "repo-456")

    assert result is None


def test_build_attachment_doc_success(loader):
    """Creates a Document with attachment text and correct metadata"""
    page = {"id": 123, "path": "/Docs/Guide", "wiki_name": "test.wiki", "wiki_id": "wiki-123"}
    attachment = {
        "name": "spec.pdf",
        "path": "/.attachments/spec.pdf",
        "content_type": "application/pdf",
        "object_id": "abc123",
    }
    loader._download_wiki_attachment = MagicMock(return_value=b"pdf bytes")
    loader._extract_attachment_text = MagicMock(return_value="Extracted PDF text content")

    result = loader._build_attachment_doc(page, attachment, "repo-456")

    assert result is not None
    assert isinstance(result, Document)
    assert result.page_content == "Extracted PDF text content"
    assert result.metadata["content_type"] == "attachment"
    assert result.metadata["attachment_name"] == "spec.pdf"
    assert result.metadata["attachment_path"] == "/.attachments/spec.pdf"
    assert result.metadata["attachment_mime_type"] == "application/pdf"
    assert result.metadata["page_id"] == 123
    assert result.metadata["wiki_name"] == "test.wiki"
    assert result.metadata["wiki_id"] == "wiki-123"
    assert "attachment_summary" in result.metadata
    # Verify attachments have a unique source URL with /attachments path
    assert "/attachments/" in result.metadata["source"]
    assert "spec.pdf" in result.metadata["source"]
    # Verify summary field is added for LLM routing
    assert "summary" in result.metadata
    assert "attachment" in result.metadata["summary"].lower()
    assert "spec.pdf" in result.metadata["summary"]


def test_build_attachment_doc_summary_truncated(loader):
    """attachment_summary is truncated to 300 chars with ellipsis for long text"""
    page = {"id": 1, "path": "/page", "wiki_name": "test.wiki", "wiki_id": "wiki-1"}
    attachment = {
        "name": "big.pdf",
        "path": "/.attachments/big.pdf",
        "content_type": "application/pdf",
        "object_id": "x",
    }
    long_text = "A" * 400
    loader._download_wiki_attachment = MagicMock(return_value=b"bytes")
    loader._extract_attachment_text = MagicMock(return_value=long_text)

    result = loader._build_attachment_doc(page, attachment, "repo")

    assert result.metadata["attachment_summary"].endswith("...")
    assert len(result.metadata["attachment_summary"]) == 303  # 300 + "..."


def test_extract_attachment_text_dispatches_to_image_by_mime(loader):
    """Dispatches to _extract_image_text for image/* MIME types"""
    loader._extract_image_text = MagicMock(return_value="OCR result")

    result = loader._extract_attachment_text(b"bytes", "image/png", "pic.png")

    loader._extract_image_text.assert_called_once_with(b"bytes")
    assert result == "OCR result"


def test_extract_attachment_text_dispatches_to_image_by_extension(loader):
    """Dispatches to _extract_image_text for .jpg extension even with unknown MIME"""
    loader._extract_image_text = MagicMock(return_value="OCR result")

    loader._extract_attachment_text(b"bytes", "application/octet-stream", "photo.jpg")

    loader._extract_image_text.assert_called_once()


def test_extract_attachment_text_dispatches_to_pdf(loader):
    """Dispatches to _extract_pdf_text for application/pdf"""
    loader._extract_pdf_text = MagicMock(return_value="PDF text")

    result = loader._extract_attachment_text(b"bytes", "application/pdf", "doc.pdf")

    loader._extract_pdf_text.assert_called_once_with(b"bytes")
    assert result == "PDF text"


def test_extract_attachment_text_dispatches_to_docx(loader):
    """Dispatches to _extract_docx_text for DOCX MIME type"""
    loader._extract_docx_text = MagicMock(return_value="Word text")
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    result = loader._extract_attachment_text(b"bytes", mime, "file.docx")

    loader._extract_docx_text.assert_called_once_with(b"bytes", "file.docx")
    assert result == "Word text"


def test_extract_attachment_text_dispatches_to_xlsx(loader):
    """Dispatches to _extract_xlsx_text for XLSX MIME type"""
    loader._extract_xlsx_text = MagicMock(return_value="Table text")
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    result = loader._extract_attachment_text(b"bytes", mime, "data.xlsx")

    loader._extract_xlsx_text.assert_called_once_with(b"bytes")
    assert result == "Table text"


def test_extract_attachment_text_dispatches_to_pptx(loader):
    """Dispatches to _extract_pptx_text for PPTX MIME type"""
    loader._extract_pptx_text = MagicMock(return_value="Slide text")
    mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    result = loader._extract_attachment_text(b"bytes", mime, "slides.pptx")

    loader._extract_pptx_text.assert_called_once_with(b"bytes")
    assert result == "Slide text"


def test_extract_attachment_text_dispatches_to_msg_by_extension(loader):
    """Dispatches to _extract_msg_text for .msg extension"""
    loader._extract_msg_text = MagicMock(return_value="Email text")

    result = loader._extract_attachment_text(b"bytes", "application/octet-stream", "email.msg")

    loader._extract_msg_text.assert_called_once_with(b"bytes")
    assert result == "Email text"


def test_extract_attachment_text_unsupported_type(loader):
    """Returns empty string for unsupported MIME type / extension"""
    result = loader._extract_attachment_text(b"bytes", "application/unknown", "file.xyz")

    assert result == ""


def test_extract_image_text_no_chat_model(loader):
    """Returns empty string when no chat_model is configured"""
    loader._chat_model = None

    result = loader._extract_image_text(b"image bytes")

    assert result == ""


def test_extract_image_text_with_chat_model(loader):
    """Delegates to ImageProcessor when chat_model is present"""
    import sys

    mock_chat_model = MagicMock()
    loader._chat_model = mock_chat_model

    mock_image_processor = MagicMock()
    mock_image_processor.extract_text_from_image_bytes.return_value = "Extracted OCR text"
    mock_module = MagicMock()
    mock_module.ImageProcessor.return_value = mock_image_processor

    with patch.dict(sys.modules, {"codemie_tools.utils.image_processor": mock_module}):
        result = loader._extract_image_text(b"image bytes")

    assert result == "Extracted OCR text"


def test_extract_image_text_processor_exception(loader):
    """Returns empty string when ImageProcessor raises an exception"""
    import sys

    loader._chat_model = MagicMock()

    mock_module = MagicMock()
    mock_module.ImageProcessor.side_effect = Exception("Vision model unavailable")

    with patch.dict(sys.modules, {"codemie_tools.utils.image_processor": mock_module}):
        result = loader._extract_image_text(b"image bytes")

    assert result == ""


def test_extract_pdf_text_success(loader):
    """Returns extracted markdown from PdfProcessor"""
    import sys

    mock_module = MagicMock()
    mock_module.PdfProcessor.extract_text_as_markdown.return_value = "# PDF Heading\nContent"

    with patch.dict(sys.modules, {"codemie_tools.file_analysis.pdf.processor": mock_module}):
        result = loader._extract_pdf_text(b"pdf bytes")

    assert result == "# PDF Heading\nContent"


def test_extract_pdf_text_error(loader):
    """Returns empty string when PdfProcessor raises"""
    import sys

    mock_module = MagicMock()
    mock_module.PdfProcessor.extract_text_as_markdown.side_effect = Exception("Corrupt PDF")

    with patch.dict(sys.modules, {"codemie_tools.file_analysis.pdf.processor": mock_module}):
        result = loader._extract_pdf_text(b"bad bytes")

    assert result == ""


def test_extract_docx_text_success(loader):
    """Returns extracted text from DocxProcessor"""
    import sys

    mock_processor_instance = MagicMock()
    mock_result = MagicMock()
    mock_result.text = "DOCX paragraph text"
    mock_processor_instance.read_document_from_bytes.return_value = mock_result

    mock_docx_module = MagicMock()
    mock_docx_module.DocxProcessor.return_value = mock_processor_instance
    mock_query_module = MagicMock()

    with patch.dict(
        sys.modules,
        {
            "codemie_tools.file_analysis.docx.processor": mock_docx_module,
            "codemie_tools.file_analysis.docx.models": mock_query_module,
        },
    ):
        result = loader._extract_docx_text(b"docx bytes", "file.docx")

    assert result == "DOCX paragraph text"


def test_extract_docx_text_error(loader):
    """Returns empty string when DocxProcessor raises"""
    import sys

    mock_module = MagicMock()
    mock_module.DocxProcessor.side_effect = Exception("Not a docx")

    with patch.dict(
        sys.modules,
        {
            "codemie_tools.file_analysis.docx.processor": mock_module,
            "codemie_tools.file_analysis.docx.models": MagicMock(),
        },
    ):
        result = loader._extract_docx_text(b"bad bytes", "bad.docx")

    assert result == ""


def test_extract_xlsx_text_success(loader):
    """Returns markdown tables from XlsxProcessor"""
    import sys

    mock_processor_instance = MagicMock()
    mock_processor_instance.load.return_value = [MagicMock()]
    mock_processor_instance.convert.return_value = "| col1 | col2 |\n|------|------|"
    mock_module = MagicMock()
    mock_module.XlsxProcessor.return_value = mock_processor_instance

    with patch.dict(sys.modules, {"codemie_tools.file_analysis.xlsx.processor": mock_module}):
        result = loader._extract_xlsx_text(b"xlsx bytes")

    assert "col1" in result


def test_extract_xlsx_text_error(loader):
    """Returns empty string when XlsxProcessor raises"""
    import sys

    mock_module = MagicMock()
    mock_module.XlsxProcessor.side_effect = Exception("Bad file")

    with patch.dict(sys.modules, {"codemie_tools.file_analysis.xlsx.processor": mock_module}):
        result = loader._extract_xlsx_text(b"bad bytes")

    assert result == ""


def test_extract_pptx_text_success(loader):
    """Returns slide text from PptxProcessor"""
    import sys

    mock_pptx_doc = MagicMock()
    mock_processor_instance = MagicMock()
    mock_processor_instance.extract_text_as_markdown.return_value = "Slide 1 content"
    mock_module = MagicMock()
    mock_module.PptxProcessor.return_value = mock_processor_instance
    mock_module.PptxProcessor.open_pptx_document.return_value = mock_pptx_doc

    with patch.dict(sys.modules, {"codemie_tools.file_analysis.pptx.processor": mock_module}):
        result = loader._extract_pptx_text(b"pptx bytes")

    assert result == "Slide 1 content"


def test_extract_pptx_text_error(loader):
    """Returns empty string when PptxProcessor raises"""
    import sys

    mock_module = MagicMock()
    mock_module.PptxProcessor.side_effect = Exception("Bad PPTX")

    with patch.dict(sys.modules, {"codemie_tools.file_analysis.pptx.processor": mock_module}):
        result = loader._extract_pptx_text(b"bad bytes")

    assert result == ""


def test_extract_msg_text_success(loader):
    """Returns email content extracted by MarkItDown"""
    import sys

    mock_result = MagicMock()
    mock_result.text_content = "Email subject and body"
    mock_md_instance = MagicMock()
    mock_md_instance.convert.return_value = mock_result
    mock_module = MagicMock()
    mock_module.MarkItDown.return_value = mock_md_instance

    with patch.dict(sys.modules, {"markitdown": mock_module}):
        result = loader._extract_msg_text(b"msg bytes")

    assert result == "Email subject and body"


def test_extract_msg_text_error(loader):
    """Returns empty string when MarkItDown raises"""
    import sys

    mock_module = MagicMock()
    mock_module.MarkItDown.side_effect = Exception("Import failed")

    with patch.dict(sys.modules, {"markitdown": mock_module}):
        result = loader._extract_msg_text(b"bad bytes")

    assert result == ""


@patch("codemie.datasource.loader.azure_devops_wiki_loader.Connection")
def test_lazy_load_yields_comments_doc(mock_connection_class, loader, mock_wiki_client):
    """lazy_load yields an extra comments Document when page has comments"""
    mock_conn = MagicMock()
    mock_conn.clients.get_wiki_client.return_value = mock_wiki_client
    mock_connection_class.return_value = mock_conn

    mock_wiki1 = MagicMock()
    mock_wiki1.as_dict.return_value = {"id": "wiki-1", "name": "wiki1.wiki"}
    mock_wiki_client.get_all_wikis.return_value = [mock_wiki1]

    # Page tree
    mock_page_node = MagicMock()
    mock_page_node.path = "/page1"
    mock_page_node.sub_pages = None
    mock_root = MagicMock()
    mock_root.path = "/"
    mock_root.sub_pages = [mock_page_node]
    mock_tree_response = MagicMock()
    mock_tree_response.page = mock_root

    # Page metadata
    mock_page_meta = MagicMock()
    mock_page_meta.id = 100
    mock_page_meta.as_dict.return_value = {"id": 100, "path": "/page1"}
    mock_meta_response = MagicMock()
    mock_meta_response.page = mock_page_meta

    # Page content — no attachment patterns
    mock_page_content = MagicMock()
    mock_page_content.content = "Simple page content"
    mock_content_response = MagicMock()
    mock_content_response.page = mock_page_content

    mock_wiki_client.get_page.side_effect = [mock_tree_response, mock_meta_response, mock_content_response]

    # Inject session and mock _get_page_comments to return one comment
    loader._session = MagicMock()
    loader._get_page_comments = MagicMock(
        return_value=[{"author": "Alice", "created_date": "2024-01-01", "content": "Nice page!"}]
    )

    docs = list(loader.lazy_load())

    # Should yield page doc + comments doc
    assert len(docs) == 2
    page_doc = next(d for d in docs if d.metadata.get("content_type") != "comments")
    comments_doc = next(d for d in docs if d.metadata.get("content_type") == "comments")
    assert page_doc.page_content == "Simple page content"
    assert "Nice page!" in comments_doc.page_content


@patch("codemie.datasource.loader.azure_devops_wiki_loader.Connection")
def test_lazy_load_yields_attachment_doc(mock_connection_class, loader, mock_wiki_client):
    """lazy_load yields an extra attachment Document when page references attachments"""
    mock_conn = MagicMock()
    mock_conn.clients.get_wiki_client.return_value = mock_wiki_client
    mock_connection_class.return_value = mock_conn

    mock_wiki1 = MagicMock()
    mock_wiki1.as_dict.return_value = {"id": "wiki-1", "name": "wiki1.wiki", "repositoryId": "repo-1"}
    mock_wiki_client.get_all_wikis.return_value = [mock_wiki1]

    # Page tree
    mock_page_node = MagicMock()
    mock_page_node.path = "/page1"
    mock_page_node.sub_pages = None
    mock_root = MagicMock()
    mock_root.path = "/"
    mock_root.sub_pages = [mock_page_node]
    mock_tree_response = MagicMock()
    mock_tree_response.page = mock_root

    # Page metadata
    mock_page_meta = MagicMock()
    mock_page_meta.id = 100
    mock_page_meta.as_dict.return_value = {"id": 100, "path": "/page1"}
    mock_meta_response = MagicMock()
    mock_meta_response.page = mock_page_meta

    # Page content with attachment reference
    mock_page_content = MagicMock()
    mock_page_content.content = "See ![diagram](/.attachments/diagram.png)"
    mock_content_response = MagicMock()
    mock_content_response.page = mock_page_content

    mock_wiki_client.get_page.side_effect = [mock_tree_response, mock_meta_response, mock_content_response]

    # Inject session and mock attachment methods
    attachment_entry = {
        "name": "diagram.png",
        "path": "/.attachments/diagram.png",
        "content_type": "image/png",
        "object_id": "obj123",
    }
    loader._session = MagicMock()
    loader._get_page_comments = MagicMock(return_value=[])
    loader._get_wiki_attachments_index = MagicMock(return_value={"/.attachments/diagram.png": attachment_entry})
    loader._build_attachment_doc = MagicMock(
        return_value=Document(
            page_content="Diagram shows architecture",
            metadata={
                "source": "https://dev.azure.com/test-org/test-project/_wiki/wikis/wiki1.wiki/100/page1",
                "page_id": 100,
                "wiki_name": "wiki1.wiki",
                "content_type": "attachment",
                "attachment_name": "diagram.png",
            },
        )
    )

    docs = list(loader.lazy_load())

    # Should yield page doc + attachment doc
    assert len(docs) == 2
    attachment_doc = next(d for d in docs if d.metadata.get("content_type") == "attachment")
    assert attachment_doc.metadata["attachment_name"] == "diagram.png"
