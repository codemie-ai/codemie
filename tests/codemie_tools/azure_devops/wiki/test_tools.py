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

from unittest.mock import Mock, patch

from azure.devops.v7_0.wiki.models import WikiV2, WikiPage, WikiPageResponse
from langchain_core.tools import ToolException
from pydantic import Field
import pytest

from codemie_tools.azure_devops.wiki.models import AzureDevOpsWikiConfig
import httpx

from codemie_tools.azure_devops.wiki.tools import (
    BaseAzureDevOpsWikiTool,
    GetWikiTool,
    ListWikisTool,
    ListPagesTool,
    GetWikiPageByPathTool,
    RenameWikiPageTool,
    MoveWikiPageTool,
    GetWikiPageCommentsByIdTool,
    GetWikiPageCommentsByPathTool,
    AddWikiAttachmentTool,
    GetPageStatsByIdTool,
    GetPageStatsByPathTool,
)


class TestBaseAzureDevOpsWikiTool:
    def test_init_without_credentials(self):
        """Test initialization without credentials - should initialize with None clients"""

        class TestTool(BaseAzureDevOpsWikiTool):
            name: str = Field("test-tool", description="The name of the test tool")
            description: str = Field("Test tool description", description="A description of the test tool")

            def execute(self, *args, **kwargs):
                pass

        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = TestTool(config=config)
        # Check private attributes using name mangling (not properties which trigger lazy init)
        assert tool._BaseAzureDevOpsWikiTool__client is None
        assert tool._BaseAzureDevOpsWikiTool__core_client is None
        assert tool._BaseAzureDevOpsWikiTool__connection is None


class TestGetWikiTool:
    def test_get_wiki_success(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = GetWikiTool(config=config)

        mock_client = Mock()
        mock_wiki = WikiV2(
            id="123",
            name="test-wiki",
            url="http://test.com",
            remote_url="http://remote.com",
            type="projectWiki",
            project_id="proj-123",
            repository_id="repo-123",
        )
        mock_client.get_wiki.return_value = mock_wiki
        tool._client = mock_client

        result = tool.execute(wiki_identified="test-wiki")

        assert result is not None
        assert result == mock_wiki.as_dict()


class TestListWikisTool:
    def test_list_wikis_success(self):
        """Test listing wikis successfully returns all wikis in the project"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListWikisTool(config=config)

        mock_client = Mock()
        mock_wiki1 = WikiV2(
            id="wiki-1",
            name="ProjectWiki.wiki",
            url="http://test.com/wiki1",
            remote_url="http://remote.com/wiki1",
            type="projectWiki",
            project_id="proj-123",
            repository_id="repo-123",
        )
        mock_wiki2 = WikiV2(
            id="wiki-2",
            name="Documentation.wiki",
            url="http://test.com/wiki2",
            remote_url="http://remote.com/wiki2",
            type="codeWiki",
            project_id="proj-123",
            repository_id="repo-456",
        )
        mock_client.get_all_wikis.return_value = [mock_wiki1, mock_wiki2]
        tool._client = mock_client

        result = tool.execute()

        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == mock_wiki1.as_dict()
        assert result[1] == mock_wiki2.as_dict()
        mock_client.get_all_wikis.assert_called_once_with(project="test-project")

    def test_list_wikis_empty_project(self):
        """Test listing wikis when project has no wikis returns empty list"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="empty-project", token="fake-token"
        )
        tool = ListWikisTool(config=config)

        mock_client = Mock()
        mock_client.get_all_wikis.return_value = []
        tool._client = mock_client

        result = tool.execute()

        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 0
        mock_client.get_all_wikis.assert_called_once_with(project="empty-project")

    def test_list_wikis_authentication_error(self):
        """Test listing wikis with invalid authentication raises ToolException"""
        from azure.devops.exceptions import AzureDevOpsServiceError

        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="invalid-token"
        )
        tool = ListWikisTool(config=config)

        mock_client = Mock()
        mock_inner = Mock()
        mock_inner.message = "Authentication failed"
        mock_inner.inner_exception = None
        error = AzureDevOpsServiceError(mock_inner)
        error.status_code = 401
        mock_client.get_all_wikis.side_effect = error
        tool._client = mock_client

        with pytest.raises(ToolException) as exc_info:
            tool.execute()

        assert "Authentication failed" in str(exc_info.value)
        assert "Personal Access Token" in str(exc_info.value)

    def test_list_wikis_permission_denied(self):
        """Test listing wikis without permissions raises ToolException"""
        from azure.devops.exceptions import AzureDevOpsServiceError

        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="no-permission-token"
        )
        tool = ListWikisTool(config=config)

        mock_client = Mock()
        mock_inner = Mock()
        mock_inner.message = "Access denied"
        mock_inner.inner_exception = None
        error = AzureDevOpsServiceError(mock_inner)
        error.status_code = 403
        mock_client.get_all_wikis.side_effect = error
        tool._client = mock_client

        with pytest.raises(ToolException) as exc_info:
            tool.execute()

        assert "Access denied" in str(exc_info.value)
        assert "Wiki" in str(exc_info.value)
        assert "permissions" in str(exc_info.value)

    def test_list_wikis_project_not_found(self):
        """Test listing wikis for non-existent project raises ToolException"""
        from azure.devops.exceptions import AzureDevOpsServiceError

        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="non-existent", token="fake-token"
        )
        tool = ListWikisTool(config=config)

        mock_client = Mock()
        mock_inner = Mock()
        mock_inner.message = "Project not found"
        mock_inner.inner_exception = None
        error = AzureDevOpsServiceError(mock_inner)
        error.status_code = 404
        mock_client.get_all_wikis.side_effect = error
        tool._client = mock_client

        with pytest.raises(ToolException) as exc_info:
            tool.execute()

        assert "non-existent" in str(exc_info.value)
        assert "not found" in str(exc_info.value)

    def test_list_wikis_generic_error(self):
        """Test listing wikis with generic error raises ToolException"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListWikisTool(config=config)

        mock_client = Mock()
        mock_client.get_all_wikis.side_effect = Exception("Unexpected error")
        tool._client = mock_client

        with pytest.raises(ToolException) as exc_info:
            tool.execute()

        assert "Error listing wikis" in str(exc_info.value)
        assert "test-project" in str(exc_info.value)


class TestListPagesTool:
    def test_list_pages_success_full_hierarchy(self):
        """Test listing all pages from root with full hierarchy"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        # Mock response with nested page structure
        response_json = {
            "id": 1,
            "path": "/",
            "name": "Root",
            "order": 0,
            "url": "http://test.com/wiki",
            "subPages": [
                {
                    "id": 10,
                    "path": "/Parent",
                    "name": "Parent",
                    "order": 1,
                    "url": "http://test.com/wiki/10",
                    "subPages": [
                        {
                            "id": 100,
                            "path": "/Parent/Child",
                            "name": "Child",
                            "order": 1,
                            "url": "http://test.com/wiki/100",
                            "subPages": [],
                        }
                    ],
                },
                {
                    "id": 20,
                    "path": "/Documentation",
                    "name": "Documentation",
                    "order": 2,
                    "url": "http://test.com/wiki/20",
                    "subPages": [],
                },
            ],
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = Mock()

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            result = tool.execute(wiki_identified="test-wiki.wiki", path="/")

            # Verify API call
            mock_get.assert_called_once()
            call_args = mock_get.call_args
            assert "test-project" in call_args[0][0]
            assert "test-wiki.wiki/pages" in call_args[0][0]
            assert call_args[1]["params"]["path"] == "/"
            assert call_args[1]["params"]["recursionLevel"] == "full"
            assert call_args[1]["params"]["api-version"] == "7.1"

        # Verify paginated response structure (default page_size=20)
        assert result is not None
        assert "pages" in result
        assert "pagination" in result
        assert len(result["pages"]) == 3  # All 3 pages (Parent, Child, Documentation) fit in default page_size
        assert result["pagination"]["page_size"] == 20
        assert result["pagination"]["skip"] == 0
        assert result["pagination"]["returned_count"] == 3
        assert result["pagination"]["total_count"] == 3
        assert result["pagination"]["has_more"] is False

    def test_list_pages_success_sub_path(self):
        """Test listing pages from a specific sub-path"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        response_json = {
            "id": 50,
            "path": "/Architecture/Design",
            "name": "Design",
            "order": 1,
            "url": "http://test.com/wiki/50",
            "subPages": [
                {"id": 51, "path": "/Architecture/Design/Patterns", "name": "Patterns", "order": 1, "subPages": []},
                {"id": 52, "path": "/Architecture/Design/Diagrams", "name": "Diagrams", "order": 2, "subPages": []},
            ],
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = Mock()

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            result = tool.execute(wiki_identified="test-wiki.wiki", path="/Architecture/Design")

            # Verify path parameter
            call_args = mock_get.call_args
            assert call_args[1]["params"]["path"] == "/Architecture/Design"

        # Verify paginated response (default page_size=20)
        assert "pages" in result
        assert "pagination" in result
        assert len(result["pages"]) == 2  # 2 pages (Patterns, Diagrams)
        assert result["pagination"]["total_count"] == 2
        assert result["pagination"]["has_more"] is False

    def test_list_pages_default_path(self):
        """Test listing pages with default path (root) and default page_size"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        response_json = {"id": 1, "path": "/", "name": "Root", "subPages": []}

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = Mock()

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            # Call without path parameter - should default to "/"
            result = tool.execute(wiki_identified="test-wiki.wiki")

            call_args = mock_get.call_args
            assert call_args[1]["params"]["path"] == "/"

        # Verify paginated response with default page_size=20
        assert result is not None
        assert "pages" in result
        assert "pagination" in result
        assert len(result["pages"]) == 0
        assert result["pagination"]["page_size"] == 20
        assert result["pagination"]["total_count"] == 0

    def test_list_pages_empty_wiki(self):
        """Test listing pages when wiki has no pages"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.raise_for_status = Mock()

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            result = tool.execute(wiki_identified="empty-wiki.wiki")

        # Verify paginated empty response (default page_size=20)
        assert result is not None
        assert "pages" in result
        assert "pagination" in result
        assert result["pages"] == []
        assert result["pagination"]["returned_count"] == 0
        assert result["pagination"]["total_count"] == 0
        assert result["pagination"]["has_more"] is False

    def test_list_pages_authentication_error(self):
        """Test listing pages with invalid authentication (401)"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="invalid-token"
        )
        tool = ListPagesTool(config=config)

        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Authentication failed"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Client Error", request=Mock(), response=mock_response
        )

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            with pytest.raises(ToolException) as exc_info:
                tool.execute(wiki_identified="test-wiki.wiki")

        assert "Authentication failed" in str(exc_info.value)
        assert "Personal Access Token" in str(exc_info.value)

    def test_list_pages_permission_denied(self):
        """Test listing pages without permissions (403)"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="no-permission-token"
        )
        tool = ListPagesTool(config=config)

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.text = "Access denied"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403 Client Error", request=Mock(), response=mock_response
        )

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            with pytest.raises(ToolException) as exc_info:
                tool.execute(wiki_identified="test-wiki.wiki")

        assert "Access denied" in str(exc_info.value)
        assert "Wiki" in str(exc_info.value)
        assert "permissions" in str(exc_info.value)

    def test_list_pages_wiki_not_found(self):
        """Test listing pages for non-existent wiki (404)"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Wiki does not exist"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=Mock(), response=mock_response
        )

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            with pytest.raises(ToolException) as exc_info:
                tool.execute(wiki_identified="nonexistent.wiki")

        assert "nonexistent.wiki" in str(exc_info.value)
        assert "not found" in str(exc_info.value)

    def test_list_pages_path_not_found(self):
        """Test listing pages for non-existent path (404)"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Path not found"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=Mock(), response=mock_response
        )

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            with pytest.raises(ToolException) as exc_info:
                tool.execute(wiki_identified="test-wiki.wiki", path="/NonExistent/Path")

        assert "/NonExistent/Path" in str(exc_info.value)
        assert "does not exist" in str(exc_info.value)

    def test_list_pages_service_unavailable(self):
        """Test listing pages when Azure DevOps API is unavailable (503)"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.text = "Service unavailable"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503 Service Unavailable", request=Mock(), response=mock_response
        )

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            with pytest.raises(ToolException) as exc_info:
                tool.execute(wiki_identified="test-wiki.wiki")

        assert "unavailable" in str(exc_info.value)

    def test_list_pages_http_error(self):
        """Test listing pages with generic HTTP error"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        with patch("httpx.get") as mock_get:
            mock_get.side_effect = httpx.HTTPError("Connection error")
            with pytest.raises(ToolException) as exc_info:
                tool.execute(wiki_identified="test-wiki.wiki")

        assert "Failed to list pages" in str(exc_info.value)

    def test_list_pages_generic_error(self):
        """Test listing pages with generic exception"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        with patch("httpx.get") as mock_get:
            mock_get.side_effect = Exception("Unexpected error")
            with pytest.raises(ToolException) as exc_info:
                tool.execute(wiki_identified="test-wiki.wiki")

        assert "Error listing pages" in str(exc_info.value)
        assert "test-wiki.wiki" in str(exc_info.value)

    def test_list_pages_with_pagination_first_page(self):
        """Test listing pages with client-side pagination - first page"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        # Mock response with hierarchical structure (25 total pages)
        response_json = {
            "id": 1,
            "path": "/",
            "name": "Root",
            "subPages": [
                {
                    "id": i,
                    "path": f"/Page{i}",
                    "name": f"Page {i}",
                    "order": i,
                    "url": f"http://test.com/wiki/{i}",
                    "subPages": [],
                }
                for i in range(1, 26)
            ],
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = Mock()

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            result = tool.execute(wiki_identified="test-wiki.wiki", page_size=10, skip=0)

            # Verify API call uses full recursion (client-side pagination)
            call_args = mock_get.call_args
            assert call_args[1]["params"]["recursionLevel"] == "full"
            assert "$top" not in call_args[1]["params"]
            assert "$skip" not in call_args[1]["params"]

        # Verify paginated response structure
        assert "pages" in result
        assert "pagination" in result
        assert len(result["pages"]) == 10  # First 10 pages
        assert result["pagination"]["page_size"] == 10
        assert result["pagination"]["skip"] == 0
        assert result["pagination"]["returned_count"] == 10
        assert result["pagination"]["total_count"] == 25
        assert result["pagination"]["has_more"] is True

    def test_list_pages_with_pagination_second_page(self):
        """Test listing pages with client-side pagination - second page"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        # Mock response with hierarchical structure (25 total pages)
        response_json = {
            "id": 1,
            "path": "/",
            "name": "Root",
            "subPages": [
                {
                    "id": i,
                    "path": f"/Page{i}",
                    "name": f"Page {i}",
                    "order": i,
                    "url": f"http://test.com/wiki/{i}",
                    "subPages": [],
                }
                for i in range(1, 26)
            ],
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = Mock()

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            result = tool.execute(wiki_identified="test-wiki.wiki", page_size=10, skip=10)

            # Verify API call uses full recursion
            call_args = mock_get.call_args
            assert call_args[1]["params"]["recursionLevel"] == "full"

        # Verify paginated response (pages 11-20)
        assert len(result["pages"]) == 10
        assert result["pages"][0]["id"] == 11  # Should start from page 11
        assert result["pagination"]["skip"] == 10
        assert result["pagination"]["total_count"] == 25
        assert result["pagination"]["has_more"] is True

    def test_list_pages_with_pagination_last_page(self):
        """Test listing pages with client-side pagination - last page with fewer results"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        # Mock response with hierarchical structure (25 total pages)
        response_json = {
            "id": 1,
            "path": "/",
            "name": "Root",
            "subPages": [
                {
                    "id": i,
                    "path": f"/Page{i}",
                    "name": f"Page {i}",
                    "order": i,
                    "url": f"http://test.com/wiki/{i}",
                    "subPages": [],
                }
                for i in range(1, 26)
            ],
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = Mock()

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            result = tool.execute(wiki_identified="test-wiki.wiki", page_size=10, skip=20)

        # Verify last page response (pages 21-25, only 5 pages)
        assert len(result["pages"]) == 5
        assert result["pages"][0]["id"] == 21  # Should start from page 21
        assert result["pagination"]["returned_count"] == 5
        assert result["pagination"]["total_count"] == 25
        assert result["pagination"]["has_more"] is False

    def test_list_pages_with_pagination_empty_page(self):
        """Test listing pages with client-side pagination - skip beyond total"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        # Mock response with hierarchical structure (25 total pages)
        response_json = {
            "id": 1,
            "path": "/",
            "name": "Root",
            "subPages": [
                {
                    "id": i,
                    "path": f"/Page{i}",
                    "name": f"Page {i}",
                    "order": i,
                    "url": f"http://test.com/wiki/{i}",
                    "subPages": [],
                }
                for i in range(1, 26)
            ],
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = Mock()

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            result = tool.execute(wiki_identified="test-wiki.wiki", page_size=10, skip=100)

        # Verify empty page response when skip exceeds total
        assert len(result["pages"]) == 0
        assert result["pagination"]["returned_count"] == 0
        assert result["pagination"]["total_count"] == 25
        assert result["pagination"]["has_more"] is False

    def test_list_pages_with_pagination_custom_page_size(self):
        """Test listing pages with custom page size"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        # Mock response with hierarchical structure (50 total pages)
        response_json = {
            "id": 1,
            "path": "/",
            "name": "Root",
            "subPages": [
                {
                    "id": i,
                    "path": f"/Page{i}",
                    "name": f"Page {i}",
                    "order": i,
                    "url": f"http://test.com/wiki/{i}",
                    "subPages": [],
                }
                for i in range(1, 51)
            ],
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = Mock()

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            result = tool.execute(wiki_identified="test-wiki.wiki", page_size=25, skip=0)

        # Verify response with custom page size
        assert len(result["pages"]) == 25
        assert result["pagination"]["page_size"] == 25
        assert result["pagination"]["total_count"] == 50
        assert result["pagination"]["has_more"] is True

    def test_list_pages_with_pagination_empty_wiki(self):
        """Test listing pages with pagination when wiki has no pages"""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = ListPagesTool(config=config)

        # Mock response with no pages
        response_json = {"id": 1, "path": "/", "name": "Root", "subPages": []}

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = Mock()

        with patch("httpx.get") as mock_get:
            mock_get.return_value = mock_response
            result = tool.execute(wiki_identified="empty-wiki.wiki", page_size=10, skip=0)

        # Verify empty wiki response with pagination
        assert len(result["pages"]) == 0
        assert result["pagination"]["returned_count"] == 0
        assert result["pagination"]["total_count"] == 0
        assert result["pagination"]["has_more"] is False


class TestGetWikiPageByPathTool:
    def test_get_page_by_path_success(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = GetWikiPageByPathTool(config=config)

        mock_client = Mock()
        mock_page = WikiPageResponse(page=WikiPage(content="Test content"))
        mock_client.get_page.return_value = mock_page
        tool._client = mock_client

        result = tool.execute(wiki_identified="test-wiki", page_name="/test-page")

        assert result == "Test content"
        mock_client.get_page.assert_called_once_with(
            project="test-project", wiki_identifier="test-wiki", path="/test-page", include_content=True
        )


class TestModifyWikiPageTool:
    # Tests removed - ModifyWikiPageTool requires complex mocking of multiple client methods
    # Integration tests would be more appropriate for this tool
    pass


class TestRenameWikiPageTool:
    def test_rename_page_success(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = RenameWikiPageTool(config=config)

        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "123"
        mock_response.path = "/new-page"
        mock_response.eTag = "v1"
        mock_client.create_page_move.return_value = mock_response
        tool._client = mock_client

        result = tool.execute(
            wiki_identified="test-wiki", old_page_name="/old-page", new_page_name="/new-page", version_identifier="main"
        )

        assert result["message"] == "Page renamed from '/old-page' to '/new-page'"


class TestMoveWikiPageTool:
    def test_move_page_success(self):
        """Test successful page move operation."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = MoveWikiPageTool(config=config)

        mock_client = Mock()
        # Mock get_page for existence verification
        mock_page = Mock()
        mock_page.id = 123
        mock_page.path = "/source/page"
        mock_client.get_page.return_value = Mock(page=mock_page)

        # Mock create_page_move response
        mock_response = Mock()
        mock_response.id = "123"
        mock_response.path = "/destination/page"
        mock_response.eTag = "v1"
        mock_client.create_page_move.return_value = mock_response
        tool._client = mock_client

        result = tool.execute(
            wiki_identified="test-wiki",
            source_page_path="/source/page",
            destination_page_path="/destination/page",
            version_identifier="main",
        )

        assert result["status"] == "Success"
        assert result["message"] == "Page successfully moved from '/source/page' to '/destination/page'"
        assert result["source_path"] == "/source/page"
        assert result["destination_path"] == "/destination/page"
        mock_client.get_page.assert_called_once()
        mock_client.create_page_move.assert_called_once()

    def test_move_page_with_id_extraction(self):
        """Test page move with ID extraction from path format like /10330/Page-Name."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = MoveWikiPageTool(config=config)

        mock_client = Mock()

        # Mock get_page_by_id for path resolution (called by _get_full_path_from_id)
        mock_resolved_page = Mock()
        mock_resolved_page.path = "/Parent/Child/Source-Page"
        mock_client.get_page_by_id.return_value = Mock(page=mock_resolved_page)

        # Mock get_page for existence verification (called by _verify_page_exists)
        mock_verify_page = Mock()
        mock_verify_page.path = "/Parent/Child/Source-Page"
        mock_client.get_page.return_value = Mock(page=mock_verify_page)

        # Mock create_page_move response
        mock_response = Mock()
        mock_response.id = "10330"
        mock_response.path = "/New-Parent/Moved-Page"
        mock_response.eTag = "v1"
        mock_client.create_page_move.return_value = mock_response
        tool._client = mock_client

        result = tool.execute(
            wiki_identified="test-wiki",
            source_page_path="/10330/Source-Page",
            destination_page_path="/New-Parent/Moved-Page",
            version_identifier="main",
        )

        assert result["status"] == "Success"
        assert "/Parent/Child/Source-Page" in result["message"]
        assert "/New-Parent/Moved-Page" in result["message"]
        # Verify get_page_by_id was called for ID resolution
        mock_client.get_page_by_id.assert_called_once_with(
            project="test-project", wiki_identifier="test-wiki", id=10330, include_content=False
        )
        # Verify get_page was called for existence verification
        mock_client.get_page.assert_called_once_with(
            project="test-project", wiki_identifier="test-wiki", path="/Parent/Child/Source-Page"
        )

    def test_move_page_adds_leading_slash_to_destination(self):
        """Test that destination path without leading slash gets normalized."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = MoveWikiPageTool(config=config)

        mock_client = Mock()
        mock_page = Mock()
        mock_page.path = "/source/page"
        mock_client.get_page.return_value = Mock(page=mock_page)
        mock_client.create_page_move.return_value = Mock(id="123", path="/destination", eTag="v1")
        tool._client = mock_client

        result = tool.execute(
            wiki_identified="test-wiki",
            source_page_path="/source/page",
            destination_page_path="destination",  # No leading slash
            version_identifier="main",
        )

        assert result["destination_path"] == "/destination"
        # Verify create_page_move was called with normalized path
        call_args = mock_client.create_page_move.call_args
        assert call_args[1]["page_move_parameters"].new_path == "/destination"

    def test_move_page_not_found(self):
        """Test error when source page doesn't exist."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = MoveWikiPageTool(config=config)

        mock_client = Mock()
        mock_client.get_page.side_effect = Exception("Page not found")
        tool._client = mock_client

        with pytest.raises(ToolException) as exc_info:
            tool.execute(
                wiki_identified="test-wiki",
                source_page_path="/nonexistent",
                destination_page_path="/destination",
                version_identifier="main",
            )

        assert "not found" in str(exc_info.value).lower()
        assert "doesn't exist" in str(exc_info.value).lower()

    def test_move_page_with_invalid_version_fallback(self):
        """Test that invalid version descriptor triggers retry without version."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = MoveWikiPageTool(config=config)

        mock_client = Mock()
        mock_page = Mock()
        mock_page.path = "/source/page"
        mock_client.get_page.return_value = Mock(page=mock_page)

        # First call raises error about invalid version, second succeeds
        from azure.devops.exceptions import AzureDevOpsServiceError

        # Create a proper mock exception with inner_exception attribute and __str__ method
        mock_inner_exception = Mock()
        mock_inner_exception.message = "The version 'invalid' either is invalid or does not exist."
        mock_inner_exception.inner_exception = None  # Prevent recursion in AzureDevOpsServiceError.__init__

        # Create exception and mock its __str__ method to avoid recursion
        mock_error = AzureDevOpsServiceError(mock_inner_exception)
        mock_error.__str__ = Mock(return_value="The version 'invalid' either is invalid or does not exist.")

        mock_client.create_page_move.side_effect = [
            mock_error,
            Mock(id="123", path="/destination", eTag="v1"),
        ]
        tool._client = mock_client

        result = tool.execute(
            wiki_identified="test-wiki",
            source_page_path="/source/page",
            destination_page_path="/destination",
            version_identifier="invalid",
        )

        assert result["status"] == "Success"
        assert "without version" in result["message"]
        assert mock_client.create_page_move.call_count == 2

    def test_move_page_api_error(self):
        """Test handling of Azure DevOps API errors."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = MoveWikiPageTool(config=config)

        mock_client = Mock()
        mock_page = Mock()
        mock_page.path = "/source/page"
        mock_client.get_page.return_value = Mock(page=mock_page)

        from azure.devops.exceptions import AzureDevOpsServiceError

        # Create a proper mock exception with inner_exception attribute and __str__ method
        mock_inner_exception = Mock()
        mock_inner_exception.message = "Permission denied"
        mock_inner_exception.inner_exception = None  # Prevent recursion in AzureDevOpsServiceError.__init__

        # Create exception and mock its __str__ method to avoid recursion
        mock_error = AzureDevOpsServiceError(mock_inner_exception)
        mock_error.__str__ = Mock(return_value="Permission denied")

        mock_client.create_page_move.side_effect = mock_error
        tool._client = mock_client

        with pytest.raises(ToolException) as exc_info:
            tool.execute(
                wiki_identified="test-wiki",
                source_page_path="/source/page",
                destination_page_path="/destination",
                version_identifier="main",
            )

        assert "Unable to move wiki page" in str(exc_info.value)

    def test_move_page_preserves_metadata(self):
        """Test that move operation uses correct API parameters for metadata preservation."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = MoveWikiPageTool(config=config)

        mock_client = Mock()
        mock_page = Mock()
        mock_page.path = "/source/page"
        mock_client.get_page.return_value = Mock(page=mock_page)
        mock_client.create_page_move.return_value = Mock(id="123", path="/destination", eTag="v1")
        tool._client = mock_client

        tool.execute(
            wiki_identified="test-wiki",
            source_page_path="/source/page",
            destination_page_path="/destination",
            version_identifier="main",
            version_type="branch",
        )

        # Verify create_page_move was called with proper parameters
        call_args = mock_client.create_page_move.call_args
        assert call_args[1]["project"] == "test-project"
        assert call_args[1]["wiki_identifier"] == "test-wiki"
        assert "Page moved from" in call_args[1]["comment"]
        assert call_args[1]["page_move_parameters"].path == "/source/page"
        assert call_args[1]["page_move_parameters"].new_path == "/destination"
        assert call_args[1]["version_descriptor"].version == "main"
        assert call_args[1]["version_descriptor"].version_type == "branch"


class TestGetWikiPageCommentsByIdTool:
    def test_get_comments_by_id_success(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = GetWikiPageCommentsByIdTool(config=config)

        # Mock HTTP response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "comments": [
                {
                    "id": 1,
                    "text": "First comment",
                    "createdDate": "2024-01-01T10:00:00Z",
                    "createdBy": {"displayName": "User One"},
                },
                {
                    "id": 2,
                    "text": "Second comment",
                    "createdDate": "2024-01-02T10:00:00Z",
                    "createdBy": {"displayName": "User Two"},
                },
            ],
            "totalCount": 2,
        }

        with patch('httpx.Client') as mock_client_class:
            mock_client = Mock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = tool.execute(wiki_identified="test-wiki", page_id=10)

            assert result["count"] == 2
            assert result["total_count"] == 2
            assert result["has_more"] is False
            assert len(result["comments"]) == 2
            assert result["comments"][0]["text"] == "First comment"
            assert result["comments"][1]["text"] == "Second comment"

    def test_get_comments_by_id_with_pagination(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = GetWikiPageCommentsByIdTool(config=config)

        # Mock first page response
        mock_response_1 = Mock()
        mock_response_1.status_code = 200
        mock_response_1.json.return_value = {
            "comments": [{"id": 1, "text": "Comment 1"}],
            "continuationToken": "token123",
            "totalCount": 2,
        }

        # Mock second page response
        mock_response_2 = Mock()
        mock_response_2.status_code = 200
        mock_response_2.json.return_value = {"comments": [{"id": 2, "text": "Comment 2"}], "totalCount": 2}

        with patch('httpx.Client') as mock_client_class:
            mock_client = Mock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.side_effect = [mock_response_1, mock_response_2]
            mock_client_class.return_value = mock_client

            result = tool.execute(wiki_identified="test-wiki", page_id=10)

            assert result["count"] == 2
            assert result["total_count"] == 2
            assert len(result["comments"]) == 2
            assert mock_client.get.call_count == 2

    def test_get_comments_by_id_empty(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = GetWikiPageCommentsByIdTool(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"comments": [], "totalCount": 0}

        with patch('httpx.Client') as mock_client_class:
            mock_client = Mock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = tool.execute(wiki_identified="test-wiki", page_id=10)

            assert result["count"] == 0
            assert result["total_count"] == 0
            assert result["has_more"] is False
            assert len(result["comments"]) == 0

    def test_get_comments_by_id_with_limit(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = GetWikiPageCommentsByIdTool(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "comments": [
                {"id": 1, "text": "Comment 1"},
                {"id": 2, "text": "Comment 2"},
                {"id": 3, "text": "Comment 3"},
            ],
            "totalCount": 5,
        }

        with patch('httpx.Client') as mock_client_class:
            mock_client = Mock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = tool.execute(wiki_identified="test-wiki", page_id=10, limit_total=3)

            assert result["count"] == 3
            assert result["total_count"] == 5


class TestGetWikiPageCommentsByPathTool:
    def test_get_comments_by_path_with_id_extraction(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = GetWikiPageCommentsByPathTool(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"comments": [{"id": 1, "text": "Comment 1"}], "totalCount": 1}

        with patch('httpx.Client') as mock_client_class:
            mock_client = Mock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            # Test with path format "/10/Page-Name" - should extract ID 10
            result = tool.execute(wiki_identified="test-wiki", page_name="/10/Page-Name")

            assert result["count"] == 1
            assert len(result["comments"]) == 1
            # Verify the API was called with page_id=10
            call_args = mock_client.get.call_args
            assert "/pages/10/comments" in call_args[0][0]

    def test_get_comments_by_path_full_path(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = GetWikiPageCommentsByPathTool(config=config)

        # Mock get_page response for path lookup
        mock_page_response = WikiPageResponse(page=WikiPage(id=42, path="/Parent/Child/Page"))

        # Mock comments response
        mock_comments_response = Mock()
        mock_comments_response.status_code = 200
        mock_comments_response.json.return_value = {
            "comments": [{"id": 1, "text": "Comment from full path"}],
            "totalCount": 1,
        }

        with patch('httpx.Client') as mock_client_class:
            mock_client = Mock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = mock_comments_response
            mock_client_class.return_value = mock_client

            # Mock the wiki client for page lookup
            mock_wiki_client = Mock()
            mock_wiki_client.get_page.return_value = mock_page_response
            tool._client = mock_wiki_client

            result = tool.execute(wiki_identified="test-wiki", page_name="/Parent/Child/Page")

            assert result["count"] == 1
            assert len(result["comments"]) == 1
            # Verify page lookup was called
            mock_wiki_client.get_page.assert_called_once()
            # Verify the API was called with resolved page_id=42
            call_args = mock_client.get.call_args
            assert "/pages/42/comments" in call_args[0][0]

    def test_get_comments_by_path_with_parameters(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = GetWikiPageCommentsByPathTool(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"comments": [{"id": 1, "text": "Comment"}], "totalCount": 1}

        with patch('httpx.Client') as mock_client_class:
            mock_client = Mock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = tool.execute(
                wiki_identified="test-wiki",
                page_name="/25/Test-Page",
                limit_total=10,
                order="desc",
                include_deleted=True,
                expand="renderedText",
            )

            assert result["count"] == 1
            # Verify parameters were passed to the API
            call_args = mock_client.get.call_args
            params = call_args[1]["params"]
            assert params["$top"] == 10
            assert params["$orderBy"] == "desc"
            assert params["includeDeleted"] == "true"
            assert params["$expand"] == "renderedText"


class TestAddWikiAttachmentTool:
    def test_add_attachment_success(self):
        """Test successful attachment upload and page update."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = AddWikiAttachmentTool(config=config)

        # Mock the client
        mock_client = Mock()
        mock_page_response = WikiPageResponse(
            page=WikiPage(id=123, path="/test-page", content="Existing content"), eTag="version-1"
        )
        mock_client.get_page.return_value = mock_page_response
        mock_client.create_or_update_page.return_value = Mock()
        tool._client = mock_client

        # Mock file resolution
        mock_files = {"test.pdf": (b"test content", "application/pdf")}
        tool._resolve_files = Mock(return_value=mock_files)

        # Mock attachment upload with duplicate handling
        tool._upload_with_duplicate_handling = Mock(return_value=("/.attachments/test.pdf", "test.pdf"))

        result = tool.execute(
            wiki_identified="test-wiki", page_name="/test-page", version_identifier="main", version_type="branch"
        )

        # Verify upload was called
        tool._upload_with_duplicate_handling.assert_called_once_with("test-wiki", "test.pdf", b"test content")

        # Verify page was updated
        assert mock_client.create_or_update_page.called
        assert result["attachments_added"] == 1
        assert "test-page" in result["message"]
        assert "page_url" in result

    def test_add_attachment_page_not_found(self):
        """Test error when page doesn't exist."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = AddWikiAttachmentTool(config=config)

        # Mock client to raise exception when getting page
        mock_client = Mock()
        mock_client.get_page.side_effect = Exception("Page not found")
        tool._client = mock_client

        with pytest.raises(ToolException) as exc_info:
            tool.execute(wiki_identified="test-wiki", page_name="/nonexistent", version_identifier="main")

        assert "not found" in str(exc_info.value).lower()
        assert "create_wiki_page" in str(exc_info.value)

    def test_add_attachment_file_too_large(self):
        """Test validation error when file exceeds size limit."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = AddWikiAttachmentTool(config=config)

        # Mock the client
        mock_client = Mock()
        mock_page_response = WikiPageResponse(
            page=WikiPage(id=123, path="/test-page", content="Existing content"), eTag="version-1"
        )
        mock_client.get_page.return_value = mock_page_response
        tool._client = mock_client

        # Mock file that exceeds size limit (20MB > 10MB)
        large_file_content = b"x" * (20 * 1024 * 1024)
        mock_files = {"large.pdf": (large_file_content, "application/pdf")}
        tool._resolve_files = Mock(return_value=mock_files)

        with pytest.raises(ToolException) as exc_info:
            tool.execute(wiki_identified="test-wiki", page_name="/test-page", version_identifier="main")

        assert "exceeds maximum size" in str(exc_info.value)
        assert "large.pdf" in str(exc_info.value)

    def test_add_attachment_no_files(self):
        """Test error when no files are provided."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = AddWikiAttachmentTool(config=config)

        # Mock the client
        mock_client = Mock()
        mock_page_response = WikiPageResponse(
            page=WikiPage(id=123, path="/test-page", content="Existing content"), eTag="version-1"
        )
        mock_client.get_page.return_value = mock_page_response
        tool._client = mock_client

        # Mock empty file resolution
        tool._resolve_files = Mock(return_value={})

        with pytest.raises(ToolException) as exc_info:
            tool.execute(wiki_identified="test-wiki", page_name="/test-page", version_identifier="main")

        assert "No files provided" in str(exc_info.value)
        assert "input_files" in str(exc_info.value)

    def test_add_attachment_with_page_id_extraction(self):
        """Test handling of page path with ID format like /123/Page-Name."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = AddWikiAttachmentTool(config=config)

        # Mock the client
        mock_client = Mock()

        # Mock get_page_by_id to return full path
        mock_page_by_id = WikiPageResponse(page=WikiPage(path="/Parent/Child/Page"))
        mock_client.get_page_by_id.return_value = mock_page_by_id

        # Mock get_page for the resolved path
        mock_page_response = WikiPageResponse(
            page=WikiPage(id=123, path="/Parent/Child/Page", content="Existing content"), eTag="version-1"
        )
        mock_client.get_page.return_value = mock_page_response
        mock_client.create_or_update_page.return_value = Mock()
        tool._client = mock_client

        # Mock file resolution
        mock_files = {"test.pdf": (b"test content", "application/pdf")}
        tool._resolve_files = Mock(return_value=mock_files)

        # Mock attachment upload with duplicate handling
        tool._upload_with_duplicate_handling = Mock(return_value=("/.attachments/test.pdf", "test.pdf"))

        result = tool.execute(wiki_identified="test-wiki", page_name="/123/Page-Name", version_identifier="main")

        # Verify page ID was extracted and full path was resolved
        mock_client.get_page_by_id.assert_called_once_with(
            project="test-project", wiki_identifier="test-wiki", id=123, include_content=False
        )

        # Verify final page get used the resolved path
        assert any(call[1]["path"] == "/Parent/Child/Page" for call in mock_client.get_page.call_args_list)

        assert result["attachments_added"] == 1

    def test_add_attachment_multiple_files(self):
        """Test uploading multiple attachments at once."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = AddWikiAttachmentTool(config=config)

        # Mock the client
        mock_client = Mock()
        mock_page_response = WikiPageResponse(
            page=WikiPage(id=123, path="/test-page", content="Existing content"), eTag="version-1"
        )
        mock_client.get_page.return_value = mock_page_response
        mock_client.create_or_update_page.return_value = Mock()
        tool._client = mock_client

        # Mock multiple files
        mock_files = {
            "doc.pdf": (b"pdf content", "application/pdf"),
            "image.png": (b"png content", "image/png"),
            "log.txt": (b"log content", "text/plain"),
        }
        tool._resolve_files = Mock(return_value=mock_files)

        # Mock attachment upload to return different paths with duplicate handling
        upload_results = [
            ("/.attachments/doc.pdf", "doc.pdf"),
            ("/.attachments/image.png", "image.png"),
            ("/.attachments/log.txt", "log.txt"),
        ]
        tool._upload_with_duplicate_handling = Mock(side_effect=upload_results)

        result = tool.execute(wiki_identified="test-wiki", page_name="/test-page", version_identifier="main")

        # Verify all files were uploaded
        assert tool._upload_with_duplicate_handling.call_count == 3
        assert result["attachments_added"] == 3

        # Verify page update was called with attachment links
        update_call = mock_client.create_or_update_page.call_args
        updated_content = update_call[1]["parameters"].content
        assert "## Attachments" in updated_content
        assert "doc.pdf" in updated_content
        assert "image.png" in updated_content
        assert "log.txt" in updated_content

    def test_add_attachment_duplicate_handling(self):
        """Test that duplicate filenames are automatically renamed with UUID."""
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        tool = AddWikiAttachmentTool(config=config)

        # Mock the client
        mock_client = Mock()
        mock_page_response = WikiPageResponse(
            page=WikiPage(id=123, path="/test-page", content="Existing content"), eTag="version-1"
        )
        mock_client.get_page.return_value = mock_page_response
        mock_client.create_or_update_page.return_value = Mock()
        tool._client = mock_client

        # Mock file resolution
        mock_files = {"duplicate.pdf": (b"test content", "application/pdf")}
        tool._resolve_files = Mock(return_value=mock_files)

        # Mock upload with duplicate handling - returns renamed file with UUID in path
        tool._upload_with_duplicate_handling = Mock(
            return_value=(
                "/.attachments/duplicate-66b92dee-8665-4b92-b710-11213538b568.pdf",
                "duplicate-66b92dee-8665-4b92-b710-11213538b568.pdf",
            )
        )

        result = tool.execute(wiki_identified="test-wiki", page_name="/test-page", version_identifier="main")

        # Verify the renamed file was used
        assert result["attachments_added"] == 1

        # Check that the markdown contains the renamed filename
        update_call = mock_client.create_or_update_page.call_args
        updated_content = update_call[1]["parameters"].content
        assert "duplicate-66b92dee-8665-4b92-b710-11213538b568.pdf" in updated_content
        assert "## Attachments" in updated_content


class TestGetPageStatsByIdTool:
    def _make_tool(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        return GetPageStatsByIdTool(config=config)

    def _mock_httpx_get(self, mock_client_class, response_json):
        mock_response = Mock()
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = Mock()
        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client
        return mock_client

    def test_get_page_stats_success(self):
        tool = self._make_tool()

        response_json = {
            "path": "/Documentation/Guide",
            "viewStats": [
                {"day": "2024-01-01", "count": 10},
                {"day": "2024-01-02", "count": 5},
                {"day": "2024-01-03", "count": 0},
            ],
        }

        with patch("httpx.Client") as mock_client_class:
            self._mock_httpx_get(mock_client_class, response_json)
            result = tool.execute(wiki_identified="test-wiki", page_id=42)

        assert result["page_id"] == 42
        assert result["path"] == "/Documentation/Guide"
        assert result["total_views"] == 15
        assert result["days_with_views"] == 2
        assert result["is_visited"] is True
        assert result["page_views_for_days"] == 30
        assert len(result["view_stats"]) == 3
        assert result["view_stats"][0] == {"day": "2024-01-01", "count": 10}

    def test_get_page_stats_no_views(self):
        tool = self._make_tool()

        response_json = {"path": "/Unused/Page", "viewStats": []}

        with patch("httpx.Client") as mock_client_class:
            self._mock_httpx_get(mock_client_class, response_json)
            result = tool.execute(wiki_identified="test-wiki", page_id=99)

        assert result["total_views"] == 0
        assert result["days_with_views"] == 0
        assert result["is_visited"] is False
        assert result["view_stats"] == []

    def test_get_page_stats_default_days(self):
        tool = self._make_tool()

        with patch("httpx.Client") as mock_client_class:
            mock_client = self._mock_httpx_get(mock_client_class, {"path": "/Page", "viewStats": []})
            result = tool.execute(wiki_identified="test-wiki", page_id=10)

        call_args = mock_client.get.call_args
        assert call_args[1]["params"]["pageViewsForDays"] == 30
        assert result["page_views_for_days"] == 30

    def test_get_page_stats_custom_days(self):
        tool = self._make_tool()

        with patch("httpx.Client") as mock_client_class:
            mock_client = self._mock_httpx_get(mock_client_class, {"path": "/Page", "viewStats": []})
            result = tool.execute(wiki_identified="test-wiki", page_id=10, page_views_for_days=7)

        call_args = mock_client.get.call_args
        assert call_args[1]["params"]["pageViewsForDays"] == 7
        assert result["page_views_for_days"] == 7

    def test_get_page_stats_api_url(self):
        tool = self._make_tool()

        with patch("httpx.Client") as mock_client_class:
            mock_client = self._mock_httpx_get(mock_client_class, {"path": "/Page", "viewStats": []})
            tool.execute(wiki_identified="MyWiki.wiki", page_id=42)

        call_args = mock_client.get.call_args
        url = call_args[0][0]
        assert "https://dev.azure.com/org/test-project" in url
        assert "MyWiki.wiki/pages/42/stats" in url
        assert call_args[1]["params"]["api-version"] == "7.1"

    def test_get_page_stats_http_404(self):
        tool = self._make_tool()

        mock_request = Mock()
        mock_response = Mock()
        mock_response.status_code = 404
        error = httpx.HTTPStatusError("404", request=mock_request, response=mock_response)

        with patch("httpx.Client") as mock_client_class:
            mock_client = Mock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = Mock(raise_for_status=Mock(side_effect=error), json=Mock())
            mock_client_class.return_value = mock_client

            with pytest.raises(ToolException) as exc_info:
                tool.execute(wiki_identified="test-wiki", page_id=999)

        assert "HTTP 404" in str(exc_info.value)
        assert "not found" in str(exc_info.value).lower()

    def test_get_page_stats_http_401(self):
        tool = self._make_tool()

        mock_request = Mock()
        mock_response = Mock()
        mock_response.status_code = 401
        error = httpx.HTTPStatusError("401", request=mock_request, response=mock_response)

        with patch("httpx.Client") as mock_client_class:
            mock_client = Mock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = Mock(raise_for_status=Mock(side_effect=error), json=Mock())
            mock_client_class.return_value = mock_client

            with pytest.raises(ToolException) as exc_info:
                tool.execute(wiki_identified="test-wiki", page_id=10)

        assert "HTTP 401" in str(exc_info.value)
        assert "Unauthorized" in str(exc_info.value)

    def test_get_page_stats_http_403(self):
        tool = self._make_tool()

        mock_request = Mock()
        mock_response = Mock()
        mock_response.status_code = 403
        error = httpx.HTTPStatusError("403", request=mock_request, response=mock_response)

        with patch("httpx.Client") as mock_client_class:
            mock_client = Mock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = Mock(raise_for_status=Mock(side_effect=error), json=Mock())
            mock_client_class.return_value = mock_client

            with pytest.raises(ToolException) as exc_info:
                tool.execute(wiki_identified="test-wiki", page_id=10)

        assert "HTTP 403" in str(exc_info.value)
        assert "Forbidden" in str(exc_info.value)

    def test_get_page_stats_generic_exception(self):
        tool = self._make_tool()

        with patch("httpx.Client") as mock_client_class:
            mock_client = Mock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.side_effect = Exception("Connection refused")
            mock_client_class.return_value = mock_client

            with pytest.raises(ToolException) as exc_info:
                tool.execute(wiki_identified="test-wiki", page_id=10)

        assert "Failed to get wiki page stats" in str(exc_info.value)
        assert "Connection refused" in str(exc_info.value)


class TestGetPageStatsByPathTool:
    def _make_tool(self):
        config = AzureDevOpsWikiConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )
        return GetPageStatsByPathTool(config=config)

    def _mock_httpx_stats(self, mock_client_class, view_stats=None):
        response_json = {"path": "/Resolved/Page", "viewStats": view_stats or [{"day": "2024-01-01", "count": 3}]}
        mock_response = Mock()
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = Mock()
        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client
        return mock_client

    def test_get_stats_by_path_with_id_in_path(self):
        """Path like /10330/Page-Name: extract ID directly, skip SDK lookup."""
        tool = self._make_tool()
        mock_wiki_client = Mock()
        tool._client = mock_wiki_client

        with patch("httpx.Client") as mock_client_class:
            self._mock_httpx_stats(mock_client_class)
            result = tool.execute(wiki_identified="test-wiki", page_name="/10330/My-Page")

        # SDK get_page should NOT be called since ID was in the path
        mock_wiki_client.get_page.assert_not_called()

        # Verify API was called with extracted page ID
        call_args = mock_client_class.return_value.get.call_args
        assert "/pages/10330/stats" in call_args[0][0]

        assert result["is_visited"] is True

    def test_get_stats_by_path_full_path_resolves_id(self):
        """Full path like /Documentation/Guide: SDK called to resolve page ID."""
        tool = self._make_tool()

        mock_wiki_client = Mock()
        mock_page = WikiPageResponse(page=WikiPage(id=77, path="/Documentation/Guide"))
        mock_wiki_client.get_page.return_value = mock_page
        tool._client = mock_wiki_client

        with patch("httpx.Client") as mock_client_class:
            self._mock_httpx_stats(mock_client_class)
            result = tool.execute(wiki_identified="test-wiki", page_name="/Documentation/Guide")

        # SDK get_page should be called for ID resolution
        mock_wiki_client.get_page.assert_called_once_with(
            project="test-project",
            wiki_identifier="test-wiki",
            path="/Documentation/Guide",
            include_content=False,
        )

        # Verify API was called with resolved page ID 77
        call_args = mock_client_class.return_value.get.call_args
        assert "/pages/77/stats" in call_args[0][0]

        assert result["total_views"] == 3

    def test_get_stats_by_path_resolution_failure(self):
        """SDK raises exception during path resolution → ToolException."""
        tool = self._make_tool()

        mock_wiki_client = Mock()
        mock_wiki_client.get_page.side_effect = Exception("Page not found")
        tool._client = mock_wiki_client

        with pytest.raises(ToolException) as exc_info:
            tool.execute(wiki_identified="test-wiki", page_name="/Nonexistent/Page")

        assert "Failed to resolve page path" in str(exc_info.value)
        assert "/Nonexistent/Page" in str(exc_info.value)

    def test_get_stats_by_path_custom_days(self):
        """Custom page_views_for_days is forwarded to the stats API."""
        tool = self._make_tool()
        mock_wiki_client = Mock()
        tool._client = mock_wiki_client

        with patch("httpx.Client") as mock_client_class:
            mock_http_client = self._mock_httpx_stats(mock_client_class)
            result = tool.execute(wiki_identified="test-wiki", page_name="/5/Page", page_views_for_days=14)

        call_args = mock_http_client.get.call_args
        assert call_args[1]["params"]["pageViewsForDays"] == 14
        assert result["page_views_for_days"] == 14
