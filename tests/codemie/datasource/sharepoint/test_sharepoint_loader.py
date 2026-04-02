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

"""Tests for SharePoint loader."""

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.documents import Document

from codemie.datasource.loader.sharepoint_loader import SharePointAuthConfig, SharePointLoader
from codemie.datasource.exceptions import (
    MissingIntegrationException,
)


@pytest.fixture
def sharepoint_loader():
    """Create SharePoint loader instance for testing."""
    return SharePointLoader(
        site_url="https://tenant.sharepoint.com/sites/testsite",
        path_filter="*",
        auth_config=SharePointAuthConfig(
            tenant_id="test-tenant-id",
            client_id="test-client-id",
            client_secret="test-client-secret",
        ),
        include_pages=True,
        include_documents=True,
        include_lists=True,
        max_file_size_mb=50,
    )


class TestSharePointLoaderInit:
    """Test SharePoint loader initialization."""

    def test_init_with_all_params(self, sharepoint_loader):
        """Test initialization with all parameters."""
        assert sharepoint_loader.site_url == "https://tenant.sharepoint.com/sites/testsite"
        assert sharepoint_loader.path_filter == "*"
        assert sharepoint_loader.tenant_id == "test-tenant-id"
        assert sharepoint_loader.client_id == "test-client-id"
        assert sharepoint_loader.client_secret == "test-client-secret"
        assert sharepoint_loader.include_pages is True
        assert sharepoint_loader.include_documents is True
        assert sharepoint_loader.include_lists is True
        assert sharepoint_loader.max_file_size_mb == 50
        assert sharepoint_loader.max_file_size_bytes == 50 * 1024 * 1024

    def test_constants_defined(self):
        """Test that constants are properly defined."""
        assert SharePointLoader.ODATA_NEXT_LINK == "@odata.nextLink"
        assert SharePointLoader.FORM_TEMPLATES_FOLDER == "Form Templates"
        assert SharePointLoader.DOCUMENTS_COUNT_KEY == "documents_count_key"
        assert "Site Pages" in SharePointLoader.SYSTEM_LIST_NAMES


class TestSharePointLoaderValidation:
    """Test SharePoint loader validation methods."""

    def test_validate_creds_missing_tenant_id(self):
        """Test validation with missing tenant_id."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        with pytest.raises(MissingIntegrationException):
            loader._validate_creds()

    def test_validate_creds_missing_client_id(self):
        """Test validation with missing client_id."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="",
                client_secret="test-client-secret",
            ),
        )
        with pytest.raises(MissingIntegrationException):
            loader._validate_creds()

    def test_validate_creds_missing_client_secret(self):
        """Test validation with missing client_secret."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="",
            ),
        )
        with pytest.raises(MissingIntegrationException):
            loader._validate_creds()


class TestSharePointLoaderHelperMethods:
    """Test SharePoint loader helper methods."""

    def test_strip_html_to_text(self, sharepoint_loader):
        """Test HTML stripping."""
        html = "<p>Test <b>content</b> with <a href='#'>link</a></p>"
        result = sharepoint_loader._strip_html_to_text(html)
        assert result == "Test content with link"

    def test_strip_html_with_multiple_spaces(self, sharepoint_loader):
        """Test HTML stripping normalizes whitespace."""
        html = "<p>Test    content   with    spaces</p>"
        result = sharepoint_loader._strip_html_to_text(html)
        assert result == "Test content with spaces"

    def test_extract_webpart_text_with_content(self, sharepoint_loader):
        """Test webpart text extraction with content."""
        webpart = {"innerHtml": "<p>Test content</p>"}
        result = sharepoint_loader._extract_webpart_text(webpart)
        assert result == "Test content"

    def test_extract_webpart_text_without_content(self, sharepoint_loader):
        """Test webpart text extraction without content."""
        webpart = {}
        result = sharepoint_loader._extract_webpart_text(webpart)
        assert result is None

    def test_should_skip_list_document_library(self, sharepoint_loader):
        """Test skipping document library."""
        list_info = {
            "displayName": "Documents",
            "list": {"template": "documentLibrary"},
            "hidden": False,
        }
        should_skip, reason = sharepoint_loader._should_skip_list(list_info)
        assert should_skip is True
        assert "document library" in reason

    def test_should_skip_list_hidden(self, sharepoint_loader):
        """Test skipping hidden list."""
        list_info = {
            "displayName": "Hidden List",
            "list": {"template": "genericList"},
            "hidden": True,
        }
        should_skip, reason = sharepoint_loader._should_skip_list(list_info)
        assert should_skip is True
        assert "hidden list" in reason

    def test_should_skip_list_system_name(self, sharepoint_loader):
        """Test skipping system list by name."""
        list_info = {
            "displayName": "_catalogs/test",
            "list": {"template": "genericList"},
            "hidden": False,
        }
        should_skip, reason = sharepoint_loader._should_skip_list(list_info)
        assert should_skip is True
        assert "system list" in reason

    def test_should_skip_list_form_templates(self, sharepoint_loader):
        """Test skipping Form Templates folder."""
        list_info = {
            "displayName": "Form Templates",
            "list": {"template": "genericList"},
            "hidden": False,
        }
        should_skip, reason = sharepoint_loader._should_skip_list(list_info)
        assert should_skip is True

    def test_should_not_skip_list_user_created(self, sharepoint_loader):
        """Test not skipping user-created list."""
        list_info = {
            "displayName": "My Custom List",
            "list": {"template": "genericList"},
            "hidden": False,
        }
        should_skip, reason = sharepoint_loader._should_skip_list(list_info)
        assert should_skip is False
        assert reason is None

    def test_matches_path_filter_wildcard(self, sharepoint_loader):
        """Test path filter with wildcard."""
        sharepoint_loader.path_filter = "*"
        result = sharepoint_loader._matches_path_filter("https://tenant.sharepoint.com/sites/testsite/page1")
        assert result is True

    def test_matches_path_filter_specific(self):
        """Test path filter with specific pattern."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="/Shared Documents/*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        result = loader._matches_path_filter("https://tenant.sharepoint.com/sites/testsite/Shared Documents/file.pdf")
        assert result is True

    def test_matches_path_filter_no_match(self):
        """Test path filter with no match."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="/Shared Documents/*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        result = loader._matches_path_filter("https://tenant.sharepoint.com/sites/testsite/Other/file.pdf")
        assert result is False


class TestSharePointLoaderFileSkipping:
    """Test file skipping logic."""

    def test_should_skip_file_large_size(self, sharepoint_loader):
        """Test skipping large files."""
        item = {
            "name": "large_file.pdf",
            "size": 100 * 1024 * 1024,  # 100MB
            "webUrl": "https://test.com/file.pdf",
        }
        should_skip, reason = sharepoint_loader._should_skip_file(item)
        assert should_skip is True
        assert reason == "size"

    def test_should_skip_file_executable(self, sharepoint_loader):
        """Test skipping executable files."""
        item = {
            "name": "program.exe",
            "size": 1024,
            "webUrl": "https://test.com/program.exe",
        }
        should_skip, reason = sharepoint_loader._should_skip_file(item)
        assert should_skip is True
        assert reason == "extension"

    def test_should_skip_file_path_filter(self):
        """Test skipping file not matching path filter."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="/Shared Documents/*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        item = {
            "name": "file.pdf",
            "size": 1024,
            "webUrl": "https://tenant.sharepoint.com/sites/testsite/Other/file.pdf",
        }
        should_skip, reason = loader._should_skip_file(item)
        assert should_skip is True
        assert reason == "path_filter"

    def test_should_not_skip_file_valid(self, sharepoint_loader):
        """Test not skipping valid file."""
        item = {
            "name": "document.pdf",
            "size": 1024,
            "webUrl": "https://test.com/document.pdf",
        }
        should_skip, reason = sharepoint_loader._should_skip_file(item)
        assert should_skip is False
        assert reason is None

    def test_would_skip_file_for_count(self, sharepoint_loader):
        """Test file skip check for counting."""
        item = {
            "name": "large.pdf",
            "size": 100 * 1024 * 1024,
        }
        result = sharepoint_loader._would_skip_file_for_count(item)
        assert result is True

        item = {
            "name": "program.exe",
            "size": 1024,
        }
        result = sharepoint_loader._would_skip_file_for_count(item)
        assert result is True

        item = {
            "name": "document.pdf",
            "size": 1024,
        }
        result = sharepoint_loader._would_skip_file_for_count(item)
        assert result is False


class TestSharePointLoaderUrlBuilding:
    """Test URL building methods."""

    def test_build_folder_url_root(self, sharepoint_loader):
        """Test building URL for root folder."""
        with patch.object(sharepoint_loader, "_get_site_id", return_value="test-site-id"):
            url = sharepoint_loader._build_folder_url("test-site-id", "test-drive-id", "root")
            assert "root/children" in url
            assert "test-site-id" in url
            assert "test-drive-id" in url

    def test_build_folder_url_subfolder(self, sharepoint_loader):
        """Test building URL for subfolder."""
        with patch.object(sharepoint_loader, "_get_site_id", return_value="test-site-id"):
            url = sharepoint_loader._build_folder_url("test-site-id", "test-drive-id", "folder-id-123")
            assert "items/folder-id-123/children" in url
            assert "test-site-id" in url
            assert "test-drive-id" in url


class TestSharePointLoaderPageProcessing:
    """Test page processing methods."""

    def test_should_process_page_wildcard(self, sharepoint_loader):
        """Test processing page with wildcard filter."""
        page = {"title": "Test Page", "webUrl": "https://test.com/page"}
        result = sharepoint_loader._should_process_page(page)
        assert result is True

    def test_should_process_page_no_filter(self):
        """Test processing page without filter."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter=None,
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        page = {"title": "Test Page", "webUrl": "https://test.com/page"}
        result = loader._should_process_page(page)
        assert result is True

    def test_create_page_dict(self, sharepoint_loader):
        """Test creating page dictionary."""
        page_data = {
            "id": "page-123",
            "title": "Test Page",
            "webUrl": "https://test.com/page",
            "createdDateTime": "2024-01-01T00:00:00Z",
            "lastModifiedDateTime": "2024-01-02T00:00:00Z",
        }
        content = "Test page content"
        result = sharepoint_loader._create_page_dict(page_data, content)

        assert result["type"] == "page"
        assert result["id"] == "page-123"
        assert result["title"] == "Test Page"
        assert result["content"] == "Test page content"
        assert result["url"] == "https://test.com/page"

    def test_extract_canvas_sections_empty(self, sharepoint_loader):
        """Test extracting canvas sections with empty layout."""
        canvas_layout = {"horizontalSections": []}
        result = sharepoint_loader._extract_canvas_sections(canvas_layout)
        assert result == []

    def test_extract_canvas_sections_with_content(self, sharepoint_loader):
        """Test extracting canvas sections with content."""
        canvas_layout = {
            "horizontalSections": [
                {
                    "columns": [
                        {
                            "webparts": [
                                {"innerHtml": "<p>Content 1</p>"},
                                {"innerHtml": "<p>Content 2</p>"},
                            ]
                        }
                    ]
                }
            ]
        }
        result = sharepoint_loader._extract_canvas_sections(canvas_layout)
        assert len(result) == 2
        assert "Content 1" in result
        assert "Content 2" in result


class TestSharePointLoaderListProcessing:
    """Test list processing methods."""

    def test_build_list_item_content(self, sharepoint_loader):
        """Test building list item content."""
        fields = {
            "Title": "Test Item",
            "Description": "Test Description",
            "@odata.type": "#Microsoft.SharePoint.Item",  # Should be skipped
        }
        content = sharepoint_loader._build_list_item_content("Test List", fields)

        assert "List: Test List" in content
        assert "Title: Test Item" in content
        assert "Description: Test Description" in content
        assert "@odata.type" not in content


class TestSharePointLoaderTransform:
    """Test document transformation."""

    def test_transform_to_doc_page(self, sharepoint_loader):
        """Test transforming page to document."""
        item = {
            "type": "page",
            "id": "page-123",
            "title": "Test Page",
            "content": "Page content",
            "url": "https://test.com/page",
            "created": "2024-01-01T00:00:00Z",
            "modified": "2024-01-02T00:00:00Z",
        }
        doc = sharepoint_loader._transform_to_doc(item)

        assert isinstance(doc, Document)
        assert doc.page_content == "Page content"
        assert doc.metadata["source"] == "https://test.com/page"
        assert doc.metadata["title"] == "Test Page"
        assert doc.metadata["type"] == "page"

    def test_transform_to_doc_document(self, sharepoint_loader):
        """Test transforming document to LangChain Document."""
        item = {
            "type": "document",
            "id": "doc-123",
            "title": "test.pdf",
            "content": "Document content",
            "url": "https://test.com/doc",
            "created": "2024-01-01T00:00:00Z",
            "modified": "2024-01-02T00:00:00Z",
            "metadata": {"page": 1, "file_type": ".pdf"},
        }
        doc = sharepoint_loader._transform_to_doc(item)

        assert isinstance(doc, Document)
        assert doc.page_content == "Document content"
        assert doc.metadata["source"] == "https://test.com/doc"
        assert doc.metadata["file_type"] == ".pdf"
        assert doc.metadata["page"] == 1


class TestSharePointLoaderStats:
    """Test statistics tracking."""

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_fetch_remote_stats_pages(self, mock_request, mock_site_id, mock_validate, sharepoint_loader):
        """Test fetching remote stats for pages."""
        mock_site_id.return_value = "test-site-id"
        mock_request.return_value = {
            "value": [{"id": "page1"}, {"id": "page2"}],
            "@odata.nextLink": None,
        }

        sharepoint_loader.include_pages = True
        sharepoint_loader.include_documents = False
        sharepoint_loader.include_lists = False

        stats = sharepoint_loader.fetch_remote_stats()

        assert SharePointLoader.DOCUMENTS_COUNT_KEY in stats
        assert SharePointLoader.TOTAL_DOCUMENTS_KEY in stats
        assert stats[SharePointLoader.TOTAL_DOCUMENTS_KEY] == 2

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_fetch_remote_stats_documents(self, mock_request, mock_site_id, mock_validate, sharepoint_loader):
        """Test fetching remote stats for documents."""
        mock_site_id.return_value = "test-site-id"

        def mock_request_side_effect(url):
            if "drives" in url and "drives/" not in url:
                return {"value": [{"id": "drive1", "name": "Documents"}]}
            elif "root/children" in url or "items/" in url:
                return {
                    "value": [
                        {
                            "id": "file1",
                            "name": "doc.pdf",
                            "size": 1024,
                            "webUrl": "https://test.com/doc.pdf",
                            "file": {},
                        },
                        {
                            "id": "file2",
                            "name": "sheet.xlsx",
                            "size": 2048,
                            "webUrl": "https://test.com/sheet.xlsx",
                            "file": {},
                        },
                    ]
                }
            return {"value": []}

        mock_request.side_effect = mock_request_side_effect

        sharepoint_loader.include_pages = False
        sharepoint_loader.include_documents = True
        sharepoint_loader.include_lists = False

        stats = sharepoint_loader.fetch_remote_stats()

        assert stats[SharePointLoader.TOTAL_DOCUMENTS_KEY] == 2

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_fetch_remote_stats_lists(self, mock_request, mock_site_id, mock_validate, sharepoint_loader):
        """Test fetching remote stats for lists."""
        mock_site_id.return_value = "test-site-id"

        def mock_request_side_effect(url):
            if "lists" in url and "items" not in url:
                return {
                    "value": [
                        {
                            "id": "list1",
                            "displayName": "Custom List",
                            "list": {"template": "genericList"},
                            "hidden": False,
                        }
                    ]
                }
            elif "items" in url:
                return {
                    "value": [
                        {"id": "item1", "fields": {"Title": "Item 1"}},
                        {"id": "item2", "fields": {"Title": "Item 2"}},
                    ]
                }
            return {"value": []}

        mock_request.side_effect = mock_request_side_effect

        sharepoint_loader.include_pages = False
        sharepoint_loader.include_documents = False
        sharepoint_loader.include_lists = True

        stats = sharepoint_loader.fetch_remote_stats()

        assert stats[SharePointLoader.TOTAL_DOCUMENTS_KEY] == 2

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_fetch_remote_stats_all_types(self, mock_request, mock_site_id, mock_validate, sharepoint_loader):
        """Test fetching remote stats with all content types enabled."""
        mock_site_id.return_value = "test-site-id"

        def mock_request_side_effect(url):
            if "pages" in url:
                return {"value": [{"id": "page1"}]}
            elif "drives" in url and "drives/" not in url:
                return {"value": [{"id": "drive1", "name": "Documents"}]}
            elif "root/children" in url or "items/" in url and "lists" not in url:
                return {
                    "value": [
                        {
                            "id": "file1",
                            "name": "doc.pdf",
                            "size": 1024,
                            "webUrl": "https://test.com/doc.pdf",
                            "file": {},
                        }
                    ]
                }
            elif "lists" in url and "items" not in url:
                return {
                    "value": [
                        {
                            "id": "list1",
                            "displayName": "Custom List",
                            "list": {"template": "genericList"},
                            "hidden": False,
                        }
                    ]
                }
            elif "lists" in url and "items" in url:
                return {"value": [{"id": "item1", "fields": {"Title": "Item 1"}}]}
            return {"value": []}

        mock_request.side_effect = mock_request_side_effect

        sharepoint_loader.include_pages = True
        sharepoint_loader.include_documents = True
        sharepoint_loader.include_lists = True

        stats = sharepoint_loader.fetch_remote_stats()

        # 1 page + 1 document + 1 list item = 3 total
        assert stats[SharePointLoader.TOTAL_DOCUMENTS_KEY] == 3


class TestSharePointLoaderAuthentication:
    """Test authentication methods."""

    @patch("codemie.datasource.loader.sharepoint_loader.requests.post")
    def test_get_access_token_success(self, mock_post):
        """Test successful token acquisition."""
        mock_post.return_value.json.return_value = {
            "access_token": "test-token-value",
            "expires_in": 3600,
        }
        mock_post.return_value.raise_for_status = lambda: None

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )

        token = loader._get_access_token()

        assert token == "test-token-value"
        mock_post.assert_called_once()

    @patch("codemie.datasource.loader.sharepoint_loader.requests.post")
    def test_get_access_token_failure(self, mock_post):
        """Test token acquisition failure."""
        from codemie.datasource.exceptions import UnauthorizedException
        import requests

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("Auth failed")
        mock_post.return_value = mock_response

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )

        with pytest.raises(UnauthorizedException):
            loader._get_access_token()

    @patch("codemie.datasource.loader.sharepoint_loader.requests.post")
    def test_get_headers(self, mock_post):
        """Test header generation with access token."""
        mock_post.return_value.json.return_value = {"access_token": "test-token"}
        mock_post.return_value.raise_for_status = lambda: None

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )

        headers = loader._get_headers()

        assert headers["Authorization"] == "Bearer test-token"
        assert headers["Content-Type"] == "application/json"


class TestSharePointLoaderGraphAPI:
    """Test Graph API request methods."""

    @patch("codemie.datasource.loader.sharepoint_loader.requests.get")
    def test_make_graph_request_success(self, mock_get):
        """Test successful Graph API request."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"value": [{"id": "1"}]}

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        loader._access_token = "test-token"

        result = loader._make_graph_request("https://graph.microsoft.com/v1.0/test")

        assert result == {"value": [{"id": "1"}]}

    @patch("codemie.datasource.loader.sharepoint_loader.requests.get")
    def test_make_graph_request_not_found(self, mock_get):
        """Test Graph API request with 404 response."""
        mock_get.return_value.status_code = 404

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        loader._access_token = "test-token"

        result = loader._make_graph_request("https://graph.microsoft.com/v1.0/test")

        assert result is None

    @patch("codemie.datasource.loader.sharepoint_loader.requests.get")
    def test_make_graph_request_forbidden(self, mock_get):
        """Test Graph API request with 403 response."""
        mock_get.return_value.status_code = 403

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        loader._access_token = "test-token"

        result = loader._make_graph_request("https://graph.microsoft.com/v1.0/test")

        assert result is None

    @patch("codemie.datasource.loader.sharepoint_loader.requests.get")
    def test_make_graph_request_network_error(self, mock_get):
        """Test Graph API request with network error."""
        import requests

        mock_get.side_effect = requests.exceptions.RequestException("Network error")

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        loader._access_token = "test-token"

        result = loader._make_graph_request("https://graph.microsoft.com/v1.0/test")

        # After retries, should return None
        assert result is None
        assert mock_get.call_count == 4  # Initial + 3 retries


class TestSharePointLoaderPageMethods:
    """Test page loading methods."""

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_fetch_page_details(self, mock_request):
        """Test fetching page details."""
        mock_request.return_value = {
            "id": "page-123",
            "title": "Test Page",
            "webUrl": "https://test.com/page",
            "createdDateTime": "2024-01-01T00:00:00Z",
            "lastModifiedDateTime": "2024-01-02T00:00:00Z",
            "canvasLayout": {"horizontalSections": [{"columns": [{"webparts": [{"innerHtml": "<p>Content</p>"}]}]}]},
        }

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        loader._access_token = "test-token"

        page = {"id": "page-123", "title": "Test Page", "webUrl": "https://test.com/page"}
        result = loader._fetch_page_details("site-id", "page-123", page)

        assert result["id"] == "page-123"
        assert "canvasLayout" in result


class TestSharePointLoaderRetryLogic:
    """Test retry logic in Graph API requests."""

    @patch("codemie.datasource.loader.sharepoint_loader.requests.get")
    def test_make_graph_request_401_retry(self, mock_get):
        """Test retry logic for 401 unauthorized (token expired)."""
        # First call returns 401, second call succeeds
        mock_response_401 = MagicMock()
        mock_response_401.status_code = 401

        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.json.return_value = {"value": [{"id": "1"}]}

        mock_get.side_effect = [mock_response_401, mock_response_success]

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        loader._access_token = "expired-token"

        with patch.object(loader, "_get_access_token", return_value="new-token"):
            result = loader._make_graph_request("https://graph.microsoft.com/v1.0/test")

        assert result == {"value": [{"id": "1"}]}
        assert mock_get.call_count == 2
        # Token is set to None when expired, and _get_access_token will be called on next request
        assert loader._access_token is None

    @patch("time.sleep")
    @patch("codemie.datasource.loader.sharepoint_loader.requests.get")
    def test_make_graph_request_429_retry(self, mock_get, mock_sleep):
        """Test retry logic for 429 rate limiting."""
        # First call returns 429, second call succeeds
        mock_response_429 = MagicMock()
        mock_response_429.status_code = 429
        mock_response_429.headers = {"Retry-After": "2"}

        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.json.return_value = {"value": [{"id": "1"}]}

        mock_get.side_effect = [mock_response_429, mock_response_success]

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        loader._access_token = "test-token"

        result = loader._make_graph_request("https://graph.microsoft.com/v1.0/test")

        assert result == {"value": [{"id": "1"}]}
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(2)


class TestSharePointLoaderPathFilterEdgeCases:
    """Test path filter edge cases."""

    def test_should_process_page_no_match(self):
        """Test page processing with non-matching path filter."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="/Documents/*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )

        page = {
            "title": "Other Page",
            "webUrl": "https://tenant.sharepoint.com/sites/testsite/SitePages/other.aspx",
        }
        result = loader._should_process_page(page)
        assert result is False


class TestSharePointLoaderPageContentExtraction:
    """Test page content extraction edge cases."""

    def test_extract_page_content_with_description_fallback(self, sharepoint_loader):
        """Test page content extraction falls back to description when minimal content."""
        page = {
            "title": "Test Page",
            "description": "This is the page description",
            "canvasLayout": {"horizontalSections": []},
        }

        content = sharepoint_loader._extract_page_content(page)

        # Should include title and description
        assert "Test Page" in content
        assert "This is the page description" in content

    def test_extract_page_content_empty_returns_empty(self, sharepoint_loader):
        """Test page content extraction returns empty string when no content."""
        page = {
            "title": "",
            "description": "",
            "canvasLayout": {"horizontalSections": []},
        }

        content = sharepoint_loader._extract_page_content(page)

        assert content == ""


class TestSharePointLoaderSiteIdCaching:
    """Test site ID caching behavior."""

    @patch("codemie.datasource.loader.sharepoint_loader.requests.get")
    def test_get_site_id_cached(self, mock_get):
        """Test that site ID is cached and not fetched twice."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"id": "test-site-id"}
        mock_get.return_value.raise_for_status = lambda: None

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        loader._access_token = "test-token"

        # First call should fetch from API
        site_id_1 = loader._get_site_id()
        # Second call should use cached value
        site_id_2 = loader._get_site_id()

        assert site_id_1 == "test-site-id"
        assert site_id_2 == "test-site-id"
        # Should only call API once
        assert mock_get.call_count == 1

    @patch("codemie.datasource.loader.sharepoint_loader.requests.get")
    def test_get_site_id_request_exception(self, mock_get):
        """Test site ID fetch with request exception."""
        from codemie.datasource.exceptions import UnauthorizedException
        import requests

        mock_get.side_effect = requests.exceptions.RequestException("Connection error")

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        loader._access_token = "test-token"

        with pytest.raises(UnauthorizedException):
            loader._get_site_id()


class TestSharePointLoaderValidateCredsIntegration:
    """Test credential validation with site ID fetching."""

    @patch("codemie.datasource.loader.sharepoint_loader.requests.get")
    def test_validate_creds_calls_get_site_id(self, mock_get):
        """Test that validate_creds calls _get_site_id."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"id": "test-site-id"}
        mock_get.return_value.raise_for_status = lambda: None

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )
        loader._access_token = "test-token"

        loader._validate_creds()

        # Should have called API to get site ID
        assert mock_get.call_count == 1
        assert loader._site_id == "test-site-id"


class TestSharePointLoaderFileProcessingEdgeCases:
    """Test file processing edge cases."""

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._download_and_extract_file")
    def test_process_file_item_skipped(self, mock_download, sharepoint_loader):
        """Test file processing when file should be skipped."""
        sharepoint_loader._total_files_found = 0
        sharepoint_loader._total_files_skipped = 0

        item = {
            "id": "file1",
            "name": "large.pdf",
            "size": 100 * 1024 * 1024,  # 100MB - exceeds default limit
            "webUrl": "https://test.com/large.pdf",
        }

        result = list(sharepoint_loader._process_file_item("site-id", "drive-id", item))

        assert len(result) == 0
        assert sharepoint_loader._total_files_found == 1
        assert sharepoint_loader._total_files_skipped == 1
        mock_download.assert_not_called()

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._download_and_extract_file")
    def test_process_file_item_no_documents_extracted(self, mock_download, sharepoint_loader):
        """Test file processing when no documents are extracted."""
        sharepoint_loader._total_files_found = 0
        sharepoint_loader._total_files_skipped = 0
        sharepoint_loader._total_files_processed = 0

        # Return empty list (no documents extracted)
        mock_download.return_value = []

        item = {
            "id": "file1",
            "name": "empty.pdf",
            "size": 1024,
            "webUrl": "https://test.com/empty.pdf",
            "createdDateTime": "2024-01-01T00:00:00Z",
            "lastModifiedDateTime": "2024-01-02T00:00:00Z",
        }

        result = list(sharepoint_loader._process_file_item("site-id", "drive-id", item))

        assert len(result) == 0
        assert sharepoint_loader._total_files_found == 1
        assert sharepoint_loader._total_files_skipped == 1
        assert sharepoint_loader._total_files_processed == 0

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._download_and_extract_file")
    def test_process_file_item_successful(self, mock_download, sharepoint_loader):
        """Test successful file processing."""
        sharepoint_loader._total_files_found = 0
        sharepoint_loader._total_files_skipped = 0
        sharepoint_loader._total_files_processed = 0

        # Return a document
        mock_doc = Document(
            page_content="Test content",
            metadata={"page": 1, "source": "test.pdf"},
        )
        mock_download.return_value = [mock_doc]

        item = {
            "id": "file1",
            "name": "test.pdf",
            "size": 1024,
            "webUrl": "https://test.com/test.pdf",
            "createdDateTime": "2024-01-01T00:00:00Z",
            "lastModifiedDateTime": "2024-01-02T00:00:00Z",
        }

        result = list(sharepoint_loader._process_file_item("site-id", "drive-id", item))

        assert len(result) == 1
        assert result[0]["type"] == "document"
        assert result[0]["title"] == "test.pdf"
        assert result[0]["content"] == "Test content"
        assert sharepoint_loader._total_files_found == 1
        assert sharepoint_loader._total_files_skipped == 0
        assert sharepoint_loader._total_files_processed == 1


class TestSharePointLoaderDocumentLoadingBreaks:
    """Test document loading with data breaks."""

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_load_documents_recursive_data_break(self, mock_request, mock_site_id, mock_validate):
        """Test document loading when API returns None."""
        mock_site_id.return_value = "test-site-id"
        mock_request.return_value = None  # API returns None (error)

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )

        result = list(loader._load_documents_recursive("drive-id", "root"))

        assert len(result) == 0

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_get_all_drives_data_break(self, mock_request, mock_site_id, mock_validate):
        """Test getting drives when API returns None."""
        mock_site_id.return_value = "test-site-id"
        mock_request.return_value = None  # API returns None (error)

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )

        drives = loader._get_all_drives()

        assert len(drives) == 0

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_get_all_drives_no_drives_found(self, mock_request, mock_site_id, mock_validate):
        """Test getting drives when no drives are found."""
        mock_site_id.return_value = "test-site-id"
        mock_request.return_value = {"value": []}  # No drives

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
        )

        drives = loader._get_all_drives()

        assert len(drives) == 0


class TestSharePointLoaderLazyLoad:
    """Test lazy loading methods."""

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_lazy_load_pages_only(self, mock_request, mock_site_id, mock_validate):
        """Test lazy loading with pages only."""
        mock_site_id.return_value = "test-site-id"

        mock_request.side_effect = [
            # Pages list
            {
                "value": [
                    {
                        "id": "page1",
                        "title": "Test Page",
                        "webUrl": "https://test.com/page1",
                        "createdDateTime": "2024-01-01T00:00:00Z",
                        "lastModifiedDateTime": "2024-01-02T00:00:00Z",
                    }
                ]
            },
            # Page details
            {
                "id": "page1",
                "title": "Test Page",
                "webUrl": "https://test.com/page1",
                "createdDateTime": "2024-01-01T00:00:00Z",
                "lastModifiedDateTime": "2024-01-02T00:00:00Z",
                "canvasLayout": {
                    "horizontalSections": [{"columns": [{"webparts": [{"innerHtml": "<p>Page content</p>"}]}]}]
                },
            },
        ]

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
            include_pages=True,
            include_documents=False,
            include_lists=False,
        )

        docs = list(loader.lazy_load())

        assert len(docs) == 1
        assert docs[0].metadata["type"] == "page"
        assert "Page content" in docs[0].page_content

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_lazy_load_pages_with_empty_content(self, mock_request, mock_site_id, mock_validate):
        """Test lazy loading with pages that have no content."""
        mock_site_id.return_value = "test-site-id"

        mock_request.side_effect = [
            # Pages list
            {
                "value": [
                    {
                        "id": "page1",
                        "title": "Empty Page",
                        "webUrl": "https://test.com/page1",
                        "createdDateTime": "2024-01-01T00:00:00Z",
                        "lastModifiedDateTime": "2024-01-02T00:00:00Z",
                    }
                ]
            },
            # Page details with no content
            {
                "id": "page1",
                "title": "",
                "webUrl": "https://test.com/page1",
                "createdDateTime": "2024-01-01T00:00:00Z",
                "lastModifiedDateTime": "2024-01-02T00:00:00Z",
                "canvasLayout": {"horizontalSections": []},
            },
        ]

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
            include_pages=True,
            include_documents=False,
            include_lists=False,
        )

        docs = list(loader.lazy_load())

        # Empty pages should not be yielded
        assert len(docs) == 0

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_lazy_load_pages_filtered_by_path(self, mock_request, mock_site_id, mock_validate):
        """Test lazy loading with pages filtered by path."""
        mock_site_id.return_value = "test-site-id"

        mock_request.return_value = {
            "value": [
                {
                    "id": "page1",
                    "title": "Filtered Page",
                    "webUrl": "https://tenant.sharepoint.com/sites/testsite/Other/page1",
                    "createdDateTime": "2024-01-01T00:00:00Z",
                    "lastModifiedDateTime": "2024-01-02T00:00:00Z",
                }
            ]
        }

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="/Documents/*",  # Only match Documents folder
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
            include_pages=True,
            include_documents=False,
            include_lists=False,
        )

        docs = list(loader.lazy_load())

        # Page should be filtered out due to path filter
        assert len(docs) == 0

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_lazy_load_pages_data_break(self, mock_request, mock_site_id, mock_validate):
        """Test lazy loading when API returns None (break)."""
        mock_site_id.return_value = "test-site-id"
        mock_request.return_value = None  # API returns None (error)

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
            include_pages=True,
            include_documents=False,
            include_lists=False,
        )

        docs = list(loader.lazy_load())

        assert len(docs) == 0

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._process_file_item")
    def test_lazy_load_documents_only(self, mock_process_file, mock_request, mock_site_id, mock_validate):
        """Test lazy loading with documents only."""
        mock_site_id.return_value = "test-site-id"

        mock_request.side_effect = [
            # Drives list
            {"value": [{"id": "drive1", "name": "Documents"}]},
            # Files in drive
            {
                "value": [
                    {
                        "id": "file1",
                        "name": "document.pdf",
                        "size": 1024,
                        "webUrl": "https://test.com/doc.pdf",
                        "file": {},
                    }
                ]
            },
        ]

        # Mock file processing to return a document dict
        mock_process_file.return_value = iter(
            [
                {
                    "type": "document",
                    "id": "file1",
                    "title": "document.pdf",
                    "content": "Document content",
                    "url": "https://test.com/doc.pdf",
                    "created": "2024-01-01T00:00:00Z",
                    "modified": "2024-01-02T00:00:00Z",
                }
            ]
        )

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
            include_pages=False,
            include_documents=True,
            include_lists=False,
        )

        docs = list(loader.lazy_load())

        assert len(docs) == 1
        assert docs[0].metadata["type"] == "document"

    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._validate_creds")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._get_site_id")
    @patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")
    def test_lazy_load_lists_only(self, mock_request, mock_site_id, mock_validate):
        """Test lazy loading with lists only."""
        mock_site_id.return_value = "test-site-id"

        mock_request.side_effect = [
            # Lists
            {
                "value": [
                    {
                        "id": "list1",
                        "displayName": "Custom List",
                        "list": {"template": "genericList"},
                        "hidden": False,
                    }
                ]
            },
            # List items
            {
                "value": [
                    {
                        "id": "item1",
                        "fields": {
                            "Title": "Test Item",
                            "Description": "Item description",
                        },
                        "webUrl": "https://test.com/lists/item1",
                        "createdDateTime": "2024-01-01T00:00:00Z",
                        "lastModifiedDateTime": "2024-01-02T00:00:00Z",
                    }
                ]
            },
        ]

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
            include_pages=False,
            include_documents=False,
            include_lists=True,
        )

        docs = list(loader.lazy_load())

        assert len(docs) == 1
        assert docs[0].metadata["type"] == "list_item"
        assert "Test Item" in docs[0].page_content


class TestSharePointLoaderOAuthAuthentication:
    """Test OAuth delegated token authentication (auth_type='oauth')."""

    def test_get_oauth_access_token_success(self):
        """Test _get_access_token returns stored token for OAuth auth type."""

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                auth_type="oauth",
                access_token="stored-oauth-token",
            ),
        )

        token = loader._get_access_token()

        assert token == "stored-oauth-token"
        assert loader._access_token == "stored-oauth-token"

    def test_get_oauth_access_token_missing_raises(self):
        """Test _get_access_token raises UnauthorizedException when stored token is absent."""
        from codemie.datasource.exceptions import UnauthorizedException

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                auth_type="oauth",
                access_token="",
            ),
        )

        with pytest.raises(UnauthorizedException):
            loader._get_access_token()

    def test_get_oauth_access_token_cached(self):
        """Test that OAuth token is cached and returned on subsequent calls without re-resolving."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                auth_type="oauth",
                access_token="stored-oauth-token",
            ),
        )

        token1 = loader._get_access_token()
        token2 = loader._get_access_token()

        assert token1 == token2 == "stored-oauth-token"

    def test_validate_creds_oauth_missing_token_raises(self):
        """Test _validate_creds raises MissingIntegrationException for OAuth with no stored token."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                auth_type="oauth",
                access_token="",
            ),
        )

        with pytest.raises(MissingIntegrationException):
            loader._validate_creds()

    @patch("codemie.datasource.loader.sharepoint_loader.requests.get")
    def test_validate_creds_oauth_valid_token_calls_get_site_id(self, mock_get):
        """Test _validate_creds with a valid OAuth token resolves site ID."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"id": "test-site-id"}
        mock_get.return_value.raise_for_status = lambda: None

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(
                auth_type="oauth",
                access_token="valid-oauth-token",
            ),
        )

        loader._validate_creds()

        assert loader._site_id == "test-site-id"
        assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# Tests for _is_not_modified_since (new incremental-reindex filter)
# ---------------------------------------------------------------------------


class TestIsNotModifiedSince:
    """Tests for SharePointLoader._is_not_modified_since."""

    def test_no_cutoff_returns_false(self, sharepoint_loader):
        """When modified_since is None every item is treated as changed."""
        assert sharepoint_loader.modified_since is None
        assert sharepoint_loader._is_not_modified_since("2024-01-01T00:00:00Z") is False

    def test_no_modified_str_returns_false(self):
        """An item without a modification timestamp is always considered changed."""
        from datetime import datetime, timezone

        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(tenant_id="t", client_id="c", client_secret="s"),
            modified_since=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        assert loader._is_not_modified_since(None) is False
        assert loader._is_not_modified_since("") is False

    def test_item_older_than_cutoff_returns_true(self):
        """An item modified before (or at) the cutoff should be skipped."""
        from datetime import datetime, timezone

        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(tenant_id="t", client_id="c", client_secret="s"),
            modified_since=cutoff,
        )
        assert loader._is_not_modified_since("2024-05-31T00:00:00Z") is True

    def test_item_exactly_at_cutoff_returns_true(self):
        """An item modified at exactly the cutoff instant should be skipped."""
        from datetime import datetime, timezone

        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(tenant_id="t", client_id="c", client_secret="s"),
            modified_since=cutoff,
        )
        assert loader._is_not_modified_since("2024-06-01T00:00:00Z") is True

    def test_item_newer_than_cutoff_returns_false(self):
        """An item modified after the cutoff should be included."""
        from datetime import datetime, timezone

        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(tenant_id="t", client_id="c", client_secret="s"),
            modified_since=cutoff,
        )
        assert loader._is_not_modified_since("2024-06-02T00:00:00Z") is False

    def test_invalid_date_string_returns_false(self):
        """Invalid modification timestamp is treated as changed (safe default)."""
        from datetime import datetime, timezone

        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(tenant_id="t", client_id="c", client_secret="s"),
            modified_since=cutoff,
        )
        assert loader._is_not_modified_since("not-a-date") is False

    def test_naive_cutoff_treated_as_utc(self):
        """A naive (no tzinfo) modified_since is treated as UTC."""
        from datetime import datetime

        naive_cutoff = datetime(2024, 6, 1)
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(tenant_id="t", client_id="c", client_secret="s"),
            modified_since=naive_cutoff,
        )
        assert loader._is_not_modified_since("2024-05-31T00:00:00Z") is True
        assert loader._is_not_modified_since("2024-06-02T00:00:00Z") is False


# ---------------------------------------------------------------------------
# Tests for _should_skip_by_files_filter (new helper)
# ---------------------------------------------------------------------------


class TestShouldSkipByFilesFilter:
    """Tests for SharePointLoader._should_skip_by_files_filter."""

    def test_empty_filter_never_skips(self, sharepoint_loader):
        """An empty files_filter means no items are skipped."""
        sharepoint_loader.files_filter = ""
        item = {"name": "file.py", "parentReference": {"path": "/drives/x/root:/Shared Documents/file.py"}}
        assert sharepoint_loader._should_skip_by_files_filter(item, "file.py") is False

    def test_whitespace_filter_never_skips(self, sharepoint_loader):
        """A whitespace-only files_filter is treated the same as empty."""
        sharepoint_loader.files_filter = "   "
        item = {"name": "file.py", "parentReference": {"path": "/drives/x/root:/Shared Documents/file.py"}}
        assert sharepoint_loader._should_skip_by_files_filter(item, "file.py") is False

    def test_exclude_pattern_skips_matching_file(self, sharepoint_loader):
        """A file matching an exclude pattern should be skipped."""
        sharepoint_loader.files_filter = "!*.log"
        item = {
            "name": "error.log",
            "parentReference": {"path": "/drives/x/root:/Shared Documents"},
            "parentName": "Shared Documents",
        }
        with (
            patch.object(sharepoint_loader, "_get_file_relative_path", return_value="Shared Documents/error.log"),
            patch.object(sharepoint_loader, "_get_file_library_relative_path", return_value="error.log"),
        ):
            result = sharepoint_loader._should_skip_by_files_filter(item, "error.log")
        assert result is True

    def test_include_pattern_skips_non_matching_file(self, sharepoint_loader):
        """A file that doesn't match the include pattern should be skipped."""
        sharepoint_loader.files_filter = "*.py"
        item = {
            "name": "readme.txt",
            "parentReference": {"path": "/drives/x/root:/Shared Documents"},
            "parentName": "Shared Documents",
        }
        with (
            patch.object(sharepoint_loader, "_get_file_relative_path", return_value="Shared Documents/readme.txt"),
            patch.object(sharepoint_loader, "_get_file_library_relative_path", return_value="readme.txt"),
        ):
            result = sharepoint_loader._should_skip_by_files_filter(item, "readme.txt")
        assert result is True

    def test_include_pattern_passes_matching_file(self, sharepoint_loader):
        """A file that matches the include pattern should not be skipped."""
        sharepoint_loader.files_filter = "*.py"
        item = {
            "name": "main.py",
            "parentReference": {"path": "/drives/x/root:/Shared Documents"},
            "parentName": "Shared Documents",
        }
        with (
            patch.object(sharepoint_loader, "_get_file_relative_path", return_value="Shared Documents/main.py"),
            patch.object(sharepoint_loader, "_get_file_library_relative_path", return_value="main.py"),
        ):
            result = sharepoint_loader._should_skip_by_files_filter(item, "main.py")
        assert result is False


# ---------------------------------------------------------------------------
# Tests for _should_skip_file — modified_since check added
# ---------------------------------------------------------------------------


class TestShouldSkipFileModifiedSince:
    """Tests for the not_modified check added to _should_skip_file."""

    def test_not_modified_returns_true_not_modified(self):
        """_should_skip_file returns (True, 'not_modified') for unmodified items."""
        from datetime import datetime, timezone

        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(tenant_id="t", client_id="c", client_secret="s"),
            modified_since=cutoff,
        )
        item = {
            "name": "old.docx",
            "size": 1024,
            "lastModifiedDateTime": "2024-05-01T00:00:00Z",
        }
        should_skip, reason = loader._should_skip_file(item)
        assert should_skip is True
        assert reason == "not_modified"

    def test_modified_file_continues_to_normal_checks(self):
        """A recently modified file should not be short-circuited by not_modified check."""
        from datetime import datetime, timezone

        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(tenant_id="t", client_id="c", client_secret="s"),
            modified_since=cutoff,
        )
        item = {
            "name": "recent.docx",
            "size": 1024,
            "lastModifiedDateTime": "2024-07-01T00:00:00Z",
        }
        should_skip, reason = loader._should_skip_file(item)
        # reason should NOT be "not_modified" even if the file happens to be skipped for other reasons
        assert reason != "not_modified"


# ---------------------------------------------------------------------------
# Tests for _process_page (new helper that wraps page loading logic)
# ---------------------------------------------------------------------------


class TestProcessPage:
    """Tests for SharePointLoader._process_page."""

    def test_skips_when_should_not_process(self, sharepoint_loader):
        """Returns None when _should_process_page returns False."""
        page = {"id": "p1", "title": "Hidden"}
        with patch.object(sharepoint_loader, "_should_process_page", return_value=False):
            result = sharepoint_loader._process_page("site-id", page)
        assert result is None

    def test_skips_when_no_content(self, sharepoint_loader):
        """Returns None when _extract_page_content returns empty string."""
        page = {"id": "p1", "title": "Empty"}
        page_data = {"title": "Empty", "webUrl": "http://test", "lastModifiedDateTime": "2099-01-01T00:00:00Z"}
        with (
            patch.object(sharepoint_loader, "_should_process_page", return_value=True),
            patch.object(sharepoint_loader, "_fetch_page_details", return_value=page_data),
            patch.object(sharepoint_loader, "_extract_page_content", return_value=""),
        ):
            result = sharepoint_loader._process_page("site-id", page)
        assert result is None

    def test_skips_unmodified_page(self):
        """Returns None when the page has not changed since modified_since."""
        from datetime import datetime, timezone

        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            path_filter="*",
            auth_config=SharePointAuthConfig(tenant_id="t", client_id="c", client_secret="s"),
            modified_since=cutoff,
        )
        page = {"id": "p1", "title": "Old Page"}
        page_data = {
            "title": "Old Page",
            "webUrl": "http://test",
            "lastModifiedDateTime": "2024-05-01T00:00:00Z",
        }
        with (
            patch.object(loader, "_should_process_page", return_value=True),
            patch.object(loader, "_fetch_page_details", return_value=page_data),
            patch.object(loader, "_extract_page_content", return_value="some content"),
        ):
            result = loader._process_page("site-id", page)
        assert result is None

    def test_returns_page_dict_for_changed_page(self, sharepoint_loader):
        """Returns a page dict when the page passes all filters."""
        page = {"id": "p1", "title": "New Page"}
        page_data = {
            "title": "New Page",
            "webUrl": "http://test",
            "lastModifiedDateTime": "2099-01-01T00:00:00Z",
        }
        expected_dict = {"title": "New Page", "content": "some content"}
        with (
            patch.object(sharepoint_loader, "_should_process_page", return_value=True),
            patch.object(sharepoint_loader, "_fetch_page_details", return_value=page_data),
            patch.object(sharepoint_loader, "_extract_page_content", return_value="some content"),
            patch.object(sharepoint_loader, "_create_page_dict", return_value=expected_dict),
        ):
            result = sharepoint_loader._process_page("site-id", page)
        assert result == expected_dict
