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

import os
import re
import base64
import uuid
import httpx
import traceback
from typing import Type, Optional, Dict, Tuple, List
from urllib.parse import quote

from azure.devops.connection import Connection
from azure.devops.exceptions import AzureDevOpsServiceError
from azure.devops.v7_0.core import CoreClient
from azure.devops.v7_0.search import SearchClient
from azure.devops.v7_0.search.models import WikiSearchRequest
from azure.devops.v7_0.wiki import (
    WikiClient,
    WikiPageCreateOrUpdateParameters,
    WikiCreateParametersV2,
    WikiPageMoveParameters,
    WikiV2,
)
from azure.devops.v7_0.wiki.models import GitVersionDescriptor
from langchain_core.tools import ToolException
from msrest.authentication import BasicAuthentication
from pydantic import BaseModel

from codemie_tools.azure_devops.wiki.models import (
    AzureDevOpsWikiConfig,
    GetWikiInput,
    GetPageByPathInput,
    GetPageByIdInput,
    ModifyPageInput,
    CreatePageInput,
    RenamePageInput,
    MovePageInput,
    SearchWikiPagesInput,
    GetPageCommentsByIdInput,
    GetPageCommentsByPathInput,
    AddAttachmentInput,
    GetPageStatsByIdInput,
    GetPageStatsByPathInput,
    ListWikisInput,
    ListPagesInput,
)
from codemie_tools.azure_devops.wiki.tools_vars import (
    GET_WIKI_TOOL,
    GET_WIKI_PAGE_BY_PATH_TOOL,
    GET_WIKI_PAGE_BY_ID_TOOL,
    DELETE_PAGE_BY_PATH_TOOL,
    DELETE_PAGE_BY_ID_TOOL,
    CREATE_WIKI_PAGE_TOOL,
    MODIFY_WIKI_PAGE_TOOL,
    RENAME_WIKI_PAGE_TOOL,
    MOVE_WIKI_PAGE_TOOL,
    SEARCH_WIKI_PAGES_TOOL,
    GET_WIKI_PAGE_COMMENTS_BY_ID_TOOL,
    GET_WIKI_PAGE_COMMENTS_BY_PATH_TOOL,
    ADD_ATTACHMENT_TOOL,
    GET_PAGE_STATS_BY_ID_TOOL,
    GET_PAGE_STATS_BY_PATH_TOOL,
    LIST_WIKIS_TOOL,
    LIST_PAGES_TOOL,
)
from codemie_tools.base.codemie_tool import CodeMieTool, logger
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.azure_devops.attachment_mixin import AzureDevOpsAttachmentMixin

# Ensure Azure DevOps cache directory is set
if not os.environ.get("AZURE_DEVOPS_CACHE_DIR", None):
    os.environ["AZURE_DEVOPS_CACHE_DIR"] = ""

# Constants for error messages
INVALID_VERSION_ERROR = "The version '{0}' either is invalid or does not exist."

# Constants for attachment handling
MAX_ATTACHMENT_SIZE = 19 * 1024 * 1024  # 19MB default
WIKI_ATTACHMENTS_API_VERSION = "7.2-preview.1"


class BaseAzureDevOpsWikiTool(CodeMieTool, AzureDevOpsAttachmentMixin):
    """Base class for Azure DevOps Wiki tools with attachment support."""

    config: AzureDevOpsWikiConfig
    __client: Optional[WikiClient] = None
    __core_client: Optional[CoreClient] = None
    __search_client: Optional[SearchClient] = None
    __connection: Optional[Connection] = None

    @property
    def _connection(self) -> Connection:
        """Get or create Azure DevOps connection (lazy initialization)."""
        if self.__connection is None:
            try:
                # Set up connection to Azure DevOps using Personal Access Token (PAT)
                credentials = BasicAuthentication("", self.config.token)
                self.__connection = Connection(base_url=self.config.organization_url, creds=credentials)
            except Exception as e:
                logger.error(f"Failed to connect to Azure DevOps: {e}")
                raise ToolException(f"Failed to connect to Azure DevOps: {e}")
        return self.__connection

    @_connection.setter
    def _connection(self, value: Connection) -> None:
        """Set the Azure DevOps connection (useful for testing)."""
        self.__connection = value

    @property
    def _client(self) -> WikiClient:
        """Get or create Azure DevOps wiki client (lazy initialization)."""
        if self.__client is None:
            self.__client = self._connection.clients.get_wiki_client()
        return self.__client

    @_client.setter
    def _client(self, value: WikiClient) -> None:
        """Set the Azure DevOps wiki client (useful for testing)."""
        self.__client = value

    @property
    def _core_client(self) -> CoreClient:
        """Get or create Azure DevOps core client (lazy initialization)."""
        if self.__core_client is None:
            self.__core_client = self._connection.clients.get_core_client()
        return self.__core_client

    @_core_client.setter
    def _core_client(self, value: CoreClient) -> None:
        """Set the Azure DevOps core client (useful for testing)."""
        self.__core_client = value

    @property
    def _search_client(self) -> SearchClient:
        """Get or create Azure DevOps search client (lazy initialization)."""
        if self.__search_client is None:
            self.__search_client = self._connection.clients.get_search_client()
        return self.__search_client

    @_search_client.setter
    def _search_client(self, value: SearchClient) -> None:
        """Set the Azure DevOps search client (useful for testing)."""
        self.__search_client = value

    def _extract_page_id_from_path(self, page_name: str) -> Optional[int]:
        """
        Extract page ID from path format like '/10330/This-is-sub-page'.

        Returns page ID if found, None otherwise.
        """
        if not page_name or not page_name.startswith("/"):
            return None

        parts = page_name.lstrip("/").split("/", 1)
        if parts and parts[0].isdigit():
            return int(parts[0])
        return None

    def _get_full_path_from_id(self, wiki_identified: str, page_id: int) -> str:
        """
        Get the full hierarchical path of a page using its ID.

        Returns the full path like '/Parent/Child/Page'.
        """
        try:
            page = self._client.get_page_by_id(
                project=self.config.project,
                wiki_identifier=wiki_identified,
                id=page_id,
                include_content=False,
            )
            return page.page.path
        except Exception as e:
            logger.error(f"Failed to get page path from ID {page_id}: {str(e)}")
            raise ToolException(f"Failed to get page path from ID {page_id}: {str(e)}")

    def _get_project_id(self) -> Optional[str]:
        """Get project ID from project name."""
        projects = self._core_client.get_projects()
        for project in projects:
            if project.name == self.config.project:
                return project.id
        return None

    def _create_wiki_if_not_exists(self, wiki_identified: str) -> Optional[str]:
        """Create wiki if it doesn't exist."""
        all_wikis = [wiki.name for wiki in self._client.get_all_wikis(project=self.config.project)]
        if wiki_identified in all_wikis:
            return None

        logger.info(f"Wiki name '{wiki_identified}' doesn't exist. New wiki will be created.")
        try:
            project_id = self._get_project_id()
            if not project_id:
                return "Project ID has not been found."

            self._client.create_wiki(
                project=self.config.project,
                wiki_create_params=WikiCreateParametersV2(name=wiki_identified, project_id=project_id),
            )
            logger.info(f"Wiki '{wiki_identified}' has been created")
            return None
        except Exception as create_wiki_e:
            error_msg = f"Unable to create new wiki due to error: {create_wiki_e}"
            logger.error(error_msg)
            return error_msg

    def _construct_page_url(self, wiki_identified: str, page_id: int, page_name: str) -> str:
        """
        Construct a full Azure DevOps wiki page URL.

        Args:
            wiki_identified: Wiki name or ID
            page_id: Page ID
            page_name: Page name (will be converted to URL slug)

        Returns:
            Full page URL like: https://dev.azure.com/{org}/{project}/_wiki/wikis/{wiki}/{id}/{slug}
        """
        # Convert page name to URL slug (spaces to hyphens)
        page_slug = page_name.replace(" ", "-")
        # Build full URL
        page_url = (
            f"{self.config.organization_url}/{self.config.project}/"
            f"_wiki/wikis/{wiki_identified}/{page_id}/{page_slug}"
        )
        return page_url

    def _get_page_info(
        self, wiki_identified: str, page_path: str, include_content: bool = False
    ) -> tuple[Optional[int], Optional[str], Optional[str]]:
        """
        Get page information (ID, name, URL) from a page path.

        Args:
            wiki_identified: Wiki name or ID
            page_path: Full hierarchical page path (e.g., "/Parent/Child/Page")
            include_content: Whether to include page content in the response

        Returns:
            Tuple of (page_id, page_name, page_url), or (None, None, None) if page not found
        """
        try:
            page_response = self._client.get_page(
                project=self.config.project,
                wiki_identifier=wiki_identified,
                path=page_path,
                include_content=include_content,
            )
            if hasattr(page_response.page, "id"):
                page_id = page_response.page.id
                # Extract page name from path (last segment)
                page_name = page_path.split("/")[-1] if page_path else ""
                # Construct full URL
                page_url = self._construct_page_url(wiki_identified, page_id, page_name)
                return page_id, page_name, page_url
            return None, None, None
        except Exception as e:
            logger.debug(f"Could not get page info for {page_path}: {str(e)}")
            return None, None, None

    def _parse_attachment_urls(self, content: str) -> List[Tuple[str, str]]:
        """
        Parse attachment URLs from wiki page markdown content.

        Looks for markdown links that point to Azure DevOps attachment URLs.
        Pattern: [filename](attachment_url)

        Args:
            content: Wiki page markdown content

        Returns:
            List of tuples: [(filename, attachment_url), ...]
        """
        attachments = []

        # Pattern to match markdown links: [text](url)
        # Looking for URLs that contain attachment patterns
        markdown_link_pattern = r"\[([^\]]+)\]\(([^\)]+)\)"
        matches = re.findall(markdown_link_pattern, content)

        for filename, url in matches:
            # Check if URL looks like an Azure DevOps attachment URL
            # Attachment URLs typically contain: /_apis/wit/attachments/ or similar
            if "/_apis/wit/attachments/" in url or "/attachments/" in url:
                attachments.append((filename, url))
                logger.debug(f"Found attachment: {filename} -> {url}")

        return attachments

    def _get_attachments_from_content(self, content: str) -> Dict[str, bytes]:
        """
        Extract and download all attachments from wiki page content.

        Args:
            content: Wiki page markdown content

        Returns:
            Dict mapping filename to attachment content (bytes)
        """
        attachments = {}
        attachment_urls = self._parse_attachment_urls(content)

        if not attachment_urls:
            logger.debug("No attachments found in page content")
            return attachments

        logger.info(f"Found {len(attachment_urls)} attachments to download")

        for filename, url in attachment_urls:
            try:
                content_bytes = self._download_attachment(url, filename)
                attachments[filename] = content_bytes
            except Exception as e:
                logger.warning(f"Skipping attachment '{filename}' due to error: {str(e)}")
                continue

        return attachments

    def _upload_wiki_attachment(self, wiki_identified: str, filename: str, content: bytes) -> str:
        """
        Upload a file as an attachment to a wiki using Azure DevOps Wiki Attachments API.

        Args:
            wiki_identified: Wiki ID or wiki name
            filename: Name of the file to upload
            content: File content as bytes

        Returns:
            str: The attachment path (e.g., '/.attachments/filename.pdf') for use in markdown links

        Raises:
            ToolException: If upload fails
        """
        try:
            # Construct the API URL for wiki attachments
            # PUT https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wikiIdentifier}/attachments?name={name}&api-version=7.2-preview.1
            api_url = (
                f"{self.config.organization_url}/{self.config.project}"
                f"/_apis/wiki/wikis/{wiki_identified}/attachments?name={quote(filename)}"
                f"&api-version={WIKI_ATTACHMENTS_API_VERSION}"
            )

            # Azure DevOps Wiki API requires Base64-encoded content
            base64_content = base64.b64encode(content).decode('utf-8')

            # Upload the attachment using PUT method with Base64-encoded content
            with httpx.Client(timeout=120.0) as client:
                response = client.put(
                    api_url,
                    content=base64_content,
                    headers={"Content-Type": "application/octet-stream"},
                    auth=("", self.config.token),  # Basic auth with empty username
                )
                response.raise_for_status()
                result = response.json()

                # Log the full response for debugging
                logger.debug(f"Wiki attachment API response: {result}")

                # Extract the attachment path from the response
                # The API returns 'path' which contains the relative path like '/.attachments/filename.pdf'
                # This path can be used directly in markdown links
                attachment_path = result.get("path")

                if not attachment_path:
                    # Log full response to help debug
                    logger.error(f"Unexpected API response structure for '{filename}': {result}")
                    raise ToolException(
                        f"No path returned from wiki attachment upload. API response keys: {list(result.keys())}"
                    )

                logger.info(f"Uploaded wiki attachment '{filename}' successfully to path: {attachment_path}")
                return attachment_path

        except httpx.HTTPStatusError as e:
            error_msg = (
                f"Failed to upload wiki attachment '{filename}': HTTP {e.response.status_code} - {e.response.text}"
            )
            logger.error(error_msg)
            raise ToolException(error_msg)
        except Exception as e:
            error_msg = f"Failed to upload wiki attachment '{filename}': {str(e)}"
            logger.error(error_msg)
            raise ToolException(error_msg)

    def _generate_unique_filename(self, original_filename: str) -> str:
        """
        Generate a unique filename by adding a UUID suffix.

        Args:
            original_filename: Original filename

        Returns:
            Unique filename with UUID
        """
        # Split filename and extension
        name_parts = original_filename.rsplit('.', 1)
        unique_id = str(uuid.uuid4())
        if len(name_parts) == 2:
            base_name, extension = name_parts
            # Add UUID: filename-66b92dee-8665-4b92-b710-11213538b568.pdf
            unique_filename = f"{base_name}-{unique_id}.{extension}"
        else:
            # No extension
            unique_filename = f"{original_filename}-{unique_id}"

        return unique_filename

    def _upload_with_duplicate_handling(self, wiki_identified: str, filename: str, content: bytes) -> Tuple[str, str]:
        """
        Upload attachment with automatic duplicate filename handling.

        If a file with the same name already exists, automatically generates
        a unique filename by adding a timestamp suffix and retries.

        Args:
            wiki_identified: Wiki ID or wiki name
            filename: Original filename
            content: File content as bytes

        Returns:
            Tuple of (attachment_path, actual_filename_used)

        Raises:
            ToolException: If upload fails after retry
        """
        try:
            # Try uploading with original filename
            attachment_path = self._upload_wiki_attachment(wiki_identified, filename, content)
            return attachment_path, filename

        except ToolException as e:
            error_text = str(e)

            # Check if error is due to duplicate filename
            if "already exists" in error_text.lower():
                logger.warning(f"File '{filename}' already exists in wiki, generating unique name...")

                # Generate unique filename and retry
                unique_filename = self._generate_unique_filename(filename)
                logger.info(f"Retrying upload with unique filename: '{unique_filename}'")

                try:
                    attachment_path = self._upload_wiki_attachment(wiki_identified, unique_filename, content)
                    logger.info(f"Successfully uploaded with unique name: '{filename}' → '{unique_filename}'")
                    return attachment_path, unique_filename

                except ToolException as retry_error:
                    # If retry also fails, raise with context
                    raise ToolException(
                        f"Failed to upload '{filename}' even after renaming to '{unique_filename}': {retry_error}"
                    )
            else:
                # Not a duplicate error, re-raise original exception
                raise

    def _get_wiki_page_comments(
        self,
        wiki_identified: str,
        page_id: int,
        limit_total: Optional[int] = None,
        include_deleted: Optional[bool] = False,
        expand: Optional[str] = "none",
        order: Optional[str] = None,
    ) -> Dict:
        """
        Get comments for a wiki page using the undocumented Azure DevOps API.

        Args:
            wiki_identified: Wiki name or ID
            page_id: Page ID
            limit_total: Maximum number of comments to return (None for all)
            include_deleted: Include deleted comments
            expand: Expand parameters (all, none, reactions, renderedText, renderedTextOnly)
            order: Sort order (asc, desc)

        Returns:
            Dict with 'comments', 'count', 'total_count', 'has_more' keys
        """
        try:
            # Construct the API URL
            api_url = (
                f"{self.config.organization_url}/{self.config.project}"
                f"/_apis/wiki/wikis/{wiki_identified}/pages/{page_id}/comments"
            )

            # Build query parameters
            params = {"api-version": "7.1"}

            # Set pagination limit per request (default 100 like work_item comments)
            limit_per_request = 100
            if limit_total is not None and limit_total < limit_per_request:
                limit_per_request = limit_total

            params["$top"] = limit_per_request

            if include_deleted:
                params["includeDeleted"] = "true"
            if expand and expand != "none":
                params["$expand"] = expand
            if order:
                params["$orderBy"] = order

            # Collect all comments with pagination
            all_comments = []
            continuation_token = None

            logger.info(f"Fetching comments for wiki page {page_id} in wiki '{wiki_identified}'")

            while True:
                # Add continuation token if present
                if continuation_token:
                    params["continuationToken"] = continuation_token

                # Make the HTTP request
                with httpx.Client(timeout=120.0) as client:
                    response = client.get(
                        api_url,
                        params=params,
                        auth=("", self.config.token),  # Basic auth with empty username
                    )
                    response.raise_for_status()
                    result = response.json()

                # Extract comments from response
                comments = result.get("comments", [])
                all_comments.extend(comments)

                logger.debug(f"Retrieved {len(comments)} comments (total so far: {len(all_comments)})")

                # Check if we should continue pagination
                continuation_token = result.get("continuationToken")

                # Stop if no more pages or reached limit
                if not continuation_token:
                    break
                if limit_total is not None and len(all_comments) >= limit_total:
                    all_comments = all_comments[:limit_total]
                    break

            # Build response
            total_count = result.get("totalCount", len(all_comments))
            has_more = continuation_token is not None and (limit_total is None or len(all_comments) < total_count)

            logger.info(
                f"Retrieved {len(all_comments)} comments " f"(total available: {total_count}, has_more: {has_more})"
            )

            return {
                "comments": all_comments,
                "count": len(all_comments),
                "total_count": total_count,
                "has_more": has_more,
            }

        except httpx.HTTPStatusError as e:
            error_msg = f"Failed to get wiki page comments: HTTP {e.response.status_code}"
            if e.response.status_code == 404:
                error_msg += f" - Wiki page {page_id} not found or has no comments"
            elif e.response.status_code == 401:
                error_msg += " - Unauthorized. Check your Personal Access Token"
            elif e.response.status_code == 403:
                error_msg += " - Forbidden. Insufficient permissions to read wiki comments"
            logger.error(error_msg)
            raise ToolException(error_msg)
        except Exception as e:
            error_msg = f"Failed to get wiki page comments: {str(e)}"
            logger.error(error_msg)
            raise ToolException(error_msg)

    def _get_wiki_page_stats(
        self,
        wiki_identified: str,
        page_id: int,
        page_views_for_days: int = 30,
    ) -> Dict:
        """
        Get view statistics for a wiki page using the Azure DevOps REST API.

        Args:
            wiki_identified: Wiki name or ID
            page_id: Page ID
            page_views_for_days: Number of last days to retrieve statistics for (1–30, default 30)

        Returns:
            Dict with 'page_id', 'path', 'total_views', 'days_with_views',
            'view_stats', 'is_visited', and 'page_views_for_days' keys
        """
        try:
            api_url = (
                f"{self.config.organization_url}/{self.config.project}"
                f"/_apis/wiki/wikis/{wiki_identified}/pages/{page_id}/stats"
            )
            params = {
                "pageViewsForDays": page_views_for_days,
                "api-version": "7.1",
            }

            logger.info(
                f"Fetching stats for wiki page {page_id} in wiki '{wiki_identified}' "
                f"for last {page_views_for_days} days"
            )

            with httpx.Client(timeout=120.0) as client:
                response = client.get(
                    api_url,
                    params=params,
                    auth=("", self.config.token),
                )
                response.raise_for_status()
                result = response.json()

            logger.debug(f"Wiki page stats API response: {result}")

            view_stats_raw = result.get("viewStats", [])
            view_stats = [{"day": stat.get("day", ""), "count": stat.get("count", 0)} for stat in view_stats_raw]
            total_views = sum(stat["count"] for stat in view_stats)
            days_with_views = sum(1 for stat in view_stats if stat["count"] > 0)

            logger.info(
                f"Page {page_id} stats: total_views={total_views}, "
                f"days_with_views={days_with_views} out of {page_views_for_days} days"
            )

            return {
                "page_id": page_id,
                "path": result.get("path", ""),
                "total_views": total_views,
                "days_with_views": days_with_views,
                "view_stats": view_stats,
                "is_visited": total_views > 0,
                "page_views_for_days": page_views_for_days,
            }

        except httpx.HTTPStatusError as e:
            error_msg = f"Failed to get wiki page stats: HTTP {e.response.status_code}"
            if e.response.status_code == 404:
                error_msg += f" - Wiki page {page_id} not found in wiki '{wiki_identified}'"
            elif e.response.status_code == 401:
                error_msg += " - Unauthorized. Check your Personal Access Token"
            elif e.response.status_code == 403:
                error_msg += " - Forbidden. Insufficient permissions to read wiki page stats"
            logger.error(error_msg)
            raise ToolException(error_msg)
        except Exception as e:
            error_msg = f"Failed to get wiki page stats: {str(e)}"
            logger.error(error_msg)
            raise ToolException(error_msg)

    @staticmethod
    def _serialize_page_response(result) -> dict:
        """Safely convert WikiPageResponse to a plain dict.

        WikiPageResponse.eTag is typed as '[str]' in the SDK attribute map but the
        Azure DevOps API returns it as a plain str from the HTTP header.
        Calling result.as_dict() triggers msrest serialize_iter which rejects strings,
        raising 'Refuse str type as a valid iter type'. Extract fields manually instead.
        """
        page = result.page
        return {
            "etag": result.eTag if isinstance(result.eTag, str) else (result.eTag[0] if result.eTag else None),
            "page": {
                "id": page.id,
                "path": page.path,
                "url": page.url,
                "remote_url": page.remote_url,
                "order": page.order,
            }
            if page
            else None,
        }

    @staticmethod
    def _serialize_page_move_response(result) -> dict:
        """Safely convert WikiPageMoveResponse to a plain dict.

        Same eTag issue as WikiPageResponse — see _serialize_page_response.
        """
        page_move = result.page_move
        page = page_move.page if page_move else None
        return {
            "etag": result.eTag if isinstance(result.eTag, str) else (result.eTag[0] if result.eTag else None),
            "page_move": {
                "path": page_move.path,
                "new_path": page_move.new_path,
                "new_order": page_move.new_order,
                "page": {
                    "id": page.id,
                    "path": page.path,
                    "url": page.url,
                    "remote_url": page.remote_url,
                }
                if page
                else None,
            }
            if page_move
            else None,
        }


class GetWikiTool(BaseAzureDevOpsWikiTool):
    """Tool to get information about a wiki in Azure DevOps."""

    name: str = GET_WIKI_TOOL.name
    description: str = GET_WIKI_TOOL.description
    args_schema: Type[BaseModel] = GetWikiInput

    def execute(self, wiki_identified: str):
        """Extract ADO wiki information."""
        try:
            wiki: WikiV2 = self._client.get_wiki(project=self.config.project, wiki_identifier=wiki_identified)
            return wiki.as_dict()
        except Exception as e:
            logger.error(f"Error during the attempt to extract wiki: {str(e)}")
            raise ToolException(f"Error during the attempt to extract wiki: {str(e)}")


class ListWikisTool(BaseAzureDevOpsWikiTool):
    """Tool to list all wikis in an Azure DevOps project."""

    name: str = LIST_WIKIS_TOOL.name
    description: str = LIST_WIKIS_TOOL.description
    args_schema: Type[BaseModel] = ListWikisInput

    def execute(self):
        """
        List all wikis available in the Azure DevOps project.

        Returns:
            List of wiki dictionaries, each containing:
            - id: Wiki unique identifier
            - name: Wiki name (e.g., "MyProject.wiki")
            - url: Full URL to access the wiki
            - type: Wiki type (projectWiki or codeWiki)
            - projectId: Project UUID
            - repositoryId: Repository UUID (for code wikis)
            - remoteUrl: Git repository URL (for code wikis)
            - versions: Available versions/branches
        """
        try:
            wikis = self._client.get_all_wikis(project=self.config.project)
            wiki_list = [wiki.as_dict() for wiki in wikis]

            logger.info(f"Successfully retrieved {len(wiki_list)} wiki(s) from project '{self.config.project}'")

            return wiki_list

        except AzureDevOpsServiceError as e:
            error_message = f"Azure DevOps API error while listing wikis: {str(e)}"
            logger.error(error_message)
            if e.status_code == 401:
                raise ToolException(
                    "Authentication failed. Please verify your Personal Access Token has valid permissions."
                )
            elif e.status_code == 403:
                raise ToolException(
                    "Access denied. Please verify your PAT has 'Wiki' read permissions for this project."
                )
            elif e.status_code == 404:
                raise ToolException(f"Project '{self.config.project}' not found. Please verify the project name.")
            else:
                raise ToolException(error_message)

        except Exception as e:
            error_message = f"Error listing wikis in project '{self.config.project}': {str(e)}"
            logger.error(f"{error_message}. Stacktrace: {traceback.format_exc()}")
            raise ToolException(error_message)


class ListPagesTool(BaseAzureDevOpsWikiTool):
    """Tool to list all pages within an Azure DevOps Wiki with hierarchical structure."""

    name: str = LIST_PAGES_TOOL.name
    description: str = LIST_PAGES_TOOL.description
    args_schema: Type[BaseModel] = ListPagesInput

    def _flatten_page_tree(self, page_tree: Dict, include_root: bool = False) -> List[Dict]:
        """
        Flatten hierarchical page tree into a flat list.

        Args:
            page_tree: Hierarchical page structure with subPages
            include_root: Whether to include the root page itself (the top-level page in page_tree)

        Returns:
            Flat list of all pages
        """
        flat_pages = []

        # Handle root page based on include_root parameter
        if include_root and page_tree.get("id"):
            page_copy = {k: v for k, v in page_tree.items() if k != "subPages"}
            flat_pages.append(page_copy)

        # Recursively traverse all subPages
        def traverse(page: Dict):
            # Create a copy without subPages for flat list
            page_copy = {k: v for k, v in page.items() if k != "subPages"}
            flat_pages.append(page_copy)

            # Recursively traverse subPages
            for sub_page in page.get("subPages", []):
                traverse(sub_page)

        # Process all subPages of the root
        for sub_page in page_tree.get("subPages", []):
            traverse(sub_page)

        return flat_pages

    def execute(
        self,
        wiki_identified: str,
        path: str = "/",
        page_size: Optional[int] = None,
        skip: int = 0,
    ):
        """
        List all pages in a wiki with paginated flat list (default: 20 pages) or full hierarchical structure.

        Args:
            wiki_identified: Wiki ID or wiki name (e.g., "MyProject.wiki")
            path: Wiki path to retrieve pages from (default: "/" for root/all pages)
            page_size: Number of pages to return per request. If None, defaults to 20 pages.
                      Specify a custom value (e.g., 10, 25, 50) for different page sizes.
            skip: Number of pages to skip for pagination (default: 0)

        Returns:
            Paginated response with:
            - pages: Flat array of pages (without subPages hierarchy)
            - pagination: Object with:
                - page_size: Number of pages requested
                - skip: Number of pages skipped
                - returned_count: Actual number of pages returned
                - total_count: Total number of pages available
                - has_more: Boolean indicating if more pages are available
        """
        try:
            # Default page_size to 20 if not specified
            if page_size is None:
                page_size = 20
            # Construct API URL for Pages - Get endpoint
            # GET https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wikiIdentifier}/pages
            api_url = (
                f"{self.config.organization_url}/{self.config.project}" f"/_apis/wiki/wikis/{wiki_identified}/pages"
            )

            # Build query parameters - always fetch full hierarchy
            # Note: Azure DevOps Wiki API doesn't support $top/$skip for pages endpoint
            # We'll implement client-side pagination instead
            params = {
                "path": path,
                "recursionLevel": "full",  # Get complete hierarchy
                "api-version": "7.1",
            }

            logger.info(
                f"Listing pages for wiki '{wiki_identified}' from path '{path}' "
                f"with client-side pagination (page_size={page_size}, skip={skip})"
            )

            # Make authenticated HTTP request
            auth = BasicAuthentication("", self.config.token)
            response = httpx.get(api_url, params=params, auth=(auth.username, auth.password), timeout=30.0)

            # Handle HTTP errors
            if response.status_code == 401:
                raise ToolException(
                    "Authentication failed. Please verify your Personal Access Token has valid permissions."
                )
            elif response.status_code == 403:
                raise ToolException(
                    "Access denied. Please verify your PAT has 'Wiki' read permissions for this project."
                )
            elif response.status_code == 404:
                # Could be wiki not found or path not found
                error_detail = response.text
                if "does not exist" in error_detail.lower() or "not found" in error_detail.lower():
                    if path and path != "/":
                        raise ToolException(
                            f"Path '{path}' does not exist in wiki '{wiki_identified}'. "
                            "Please verify the path is correct."
                        )
                    else:
                        raise ToolException(f"Wiki '{wiki_identified}' not found. Please verify the wiki identifier.")
                else:
                    raise ToolException(f"Resource not found: {error_detail}")
            elif response.status_code == 503:
                raise ToolException("Azure DevOps API is currently unavailable. Please try again later.")

            # Raise for any other HTTP errors
            response.raise_for_status()

            # Parse response JSON
            page_tree = response.json()

            # Check if wiki has no pages
            if not page_tree or (isinstance(page_tree, dict) and not page_tree.get("subPages")):
                logger.info(f"Wiki '{wiki_identified}' contains no pages at path '{path}'")
                # Return empty paginated response
                return {
                    "pages": [],
                    "pagination": {
                        "page_size": page_size,
                        "skip": skip,
                        "returned_count": 0,
                        "total_count": 0,
                        "has_more": False,
                    },
                }

            # Flatten the hierarchical page tree
            all_pages = self._flatten_page_tree(page_tree, include_root=False)
            total_count = len(all_pages)

            # Apply client-side pagination
            end_index = skip + page_size
            paginated_pages = all_pages[skip:end_index]
            returned_count = len(paginated_pages)
            has_more = end_index < total_count

            logger.info(
                f"Successfully retrieved {returned_count} of {total_count} pages for wiki '{wiki_identified}' "
                f"(page_size={page_size}, skip={skip})"
            )

            return {
                "pages": paginated_pages,
                "pagination": {
                    "page_size": page_size,
                    "skip": skip,
                    "returned_count": returned_count,
                    "total_count": total_count,
                    "has_more": has_more,
                },
            }

        except httpx.HTTPError as e:
            error_message = f"HTTP error while listing pages for wiki '{wiki_identified}': {str(e)}"
            logger.error(error_message)
            raise ToolException(f"Failed to list pages: {str(e)}")

        except ToolException:
            # Re-raise ToolException without wrapping
            raise

        except Exception as e:
            error_message = f"Error listing pages for wiki '{wiki_identified}' from path '{path}': {str(e)}"
            logger.error(f"{error_message}. Stacktrace: {traceback.format_exc()}")
            raise ToolException(error_message)


class GetWikiPageByPathTool(BaseAzureDevOpsWikiTool):
    """Tool to get wiki page content by path in Azure DevOps, with optional attachment download."""

    name: str = GET_WIKI_PAGE_BY_PATH_TOOL.name
    description: str = GET_WIKI_PAGE_BY_PATH_TOOL.description
    args_schema: Type[BaseModel] = GetPageByPathInput

    def execute(self, wiki_identified: str, page_name: str, include_attachments: bool = False):
        """
        Extract ADO wiki page content and optionally download attachments.

        Args:
            wiki_identified: Wiki ID or name
            page_name: Page path
            include_attachments: Whether to download and return attachment content

        Returns:
            If include_attachments=False: str (page content)
            If include_attachments=True: dict with 'content' and 'attachments' keys
        """
        try:
            # Try to extract page ID from path format like '/10330/This-is-sub-page'
            page_id = self._extract_page_id_from_path(page_name)

            if page_id is not None:
                # Get full hierarchical path using page ID
                logger.info(f"Extracted page ID {page_id} from path, discovering full path...")
                full_path = self._get_full_path_from_id(wiki_identified, page_id)
                logger.info(f"Discovered full path: {full_path}")
                page_name = full_path

            # Get page content using the (possibly resolved) path
            page = self._client.get_page(
                project=self.config.project,
                wiki_identifier=wiki_identified,
                path=page_name,
                include_content=True,
            )
            content = page.page.content

            # If attachments not requested, return content only (backward compatible)
            if not include_attachments:
                return content

            # Download attachments if requested
            attachments = self._get_attachments_from_content(content)

            return {
                "content": content,
                "attachments": attachments,
                "attachment_count": len(attachments),
            }

        except Exception as e:
            logger.error(f"Error during the attempt to extract wiki page: {str(e)}")
            raise ToolException(f"Error during the attempt to extract wiki page: {str(e)}")


class GetWikiPageByIdTool(BaseAzureDevOpsWikiTool):
    """Tool to get wiki page content by ID in Azure DevOps, with optional attachment download."""

    name: str = GET_WIKI_PAGE_BY_ID_TOOL.name
    description: str = GET_WIKI_PAGE_BY_ID_TOOL.description
    args_schema: Type[BaseModel] = GetPageByIdInput

    def execute(self, wiki_identified: str, page_id: int, include_attachments: bool = False):
        """
        Extract ADO wiki page content and optionally download attachments.

        Args:
            wiki_identified: Wiki ID or name
            page_id: Page ID
            include_attachments: Whether to download and return attachment content

        Returns:
            If include_attachments=False: str (page content)
            If include_attachments=True: dict with 'content' and 'attachments' keys
        """
        try:
            page = self._client.get_page_by_id(
                project=self.config.project,
                wiki_identifier=wiki_identified,
                id=page_id,
                include_content=True,
            )
            content = page.page.content

            # If attachments not requested, return content only (backward compatible)
            if not include_attachments:
                return content

            # Download attachments if requested
            attachments = self._get_attachments_from_content(content)

            return {
                "content": content,
                "attachments": attachments,
                "attachment_count": len(attachments),
            }

        except Exception as e:
            logger.error(f"Error during the attempt to extract wiki page: {str(e)}")
            raise ToolException(f"Error during the attempt to extract wiki page: {str(e)}")


class DeletePageByPathTool(BaseAzureDevOpsWikiTool):
    """Tool to delete wiki page by path in Azure DevOps."""

    name: str = DELETE_PAGE_BY_PATH_TOOL.name
    description: str = DELETE_PAGE_BY_PATH_TOOL.description
    args_schema: Type[BaseModel] = GetPageByPathInput

    def execute(self, wiki_identified: str, page_name: str):
        """Delete ADO wiki page by path."""
        try:
            # Try to extract page ID from path format like '/10330/This-is-sub-page'
            page_id = self._extract_page_id_from_path(page_name)

            if page_id is not None:
                # Get full hierarchical path using page ID
                logger.info(f"Extracted page ID {page_id} from path, discovering full path...")
                full_path = self._get_full_path_from_id(wiki_identified, page_id)
                logger.info(f"Discovered full path: {full_path}")
                page_name = full_path

            # Delete page using the (possibly resolved) path
            self._client.delete_page(project=self.config.project, wiki_identifier=wiki_identified, path=page_name)
            return f"Page '{page_name}' in wiki '{wiki_identified}' has been deleted"
        except Exception as e:
            logger.error(f"Unable to delete wiki page: {str(e)}")
            raise ToolException(f"Unable to delete wiki page: {str(e)}")


class DeletePageByIdTool(BaseAzureDevOpsWikiTool):
    """Tool to delete wiki page by ID in Azure DevOps."""

    name: str = DELETE_PAGE_BY_ID_TOOL.name
    description: str = DELETE_PAGE_BY_ID_TOOL.description
    args_schema: Type[BaseModel] = GetPageByIdInput

    def execute(self, wiki_identified: str, page_id: int):
        """Delete ADO wiki page by ID."""
        try:
            self._client.delete_page_by_id(project=self.config.project, wiki_identifier=wiki_identified, id=page_id)
            return f"Page with id '{page_id}' in wiki '{wiki_identified}' has been deleted"
        except Exception as e:
            logger.error(f"Unable to delete wiki page: {str(e)}")
            raise ToolException(f"Unable to delete wiki page: {str(e)}")


class RenameWikiPageTool(BaseAzureDevOpsWikiTool):
    """Tool to rename wiki page in Azure DevOps."""

    name: str = RENAME_WIKI_PAGE_TOOL.name
    description: str = RENAME_WIKI_PAGE_TOOL.description
    args_schema: Type[BaseModel] = RenamePageInput

    def _verify_page_exists(self, wiki_identified: str, page_path: str) -> None:
        """
        Verify that the page exists.

        Raises ToolException if page doesn't exist.
        """
        try:
            self._client.get_page(project=self.config.project, wiki_identifier=wiki_identified, path=page_path)
            logger.info(f"Page '{page_path}' exists and can be renamed")
        except Exception as e:
            error_msg = f"Page '{page_path}' not found. Cannot rename a page that doesn't exist. Error: {str(e)}"
            logger.error(error_msg)
            raise ToolException(error_msg)

    def execute(
        self,
        wiki_identified: str,
        old_page_name: str,
        new_page_name: str,
        version_identifier: str,
        version_type: str = "branch",
    ):
        """Rename page in Azure DevOps wiki from old page name to new page name."""
        try:
            # Try to extract page ID from old path format like '/10330/This-is-sub-page'
            page_id = self._extract_page_id_from_path(old_page_name)

            if page_id is not None:
                # Get full hierarchical path using page ID
                logger.info(f"Extracted page ID {page_id} from path, discovering full path...")
                full_path = self._get_full_path_from_id(wiki_identified, page_id)
                logger.info(f"Discovered full path: {full_path}")
                old_page_name = full_path

            # Verify the page exists before attempting rename
            self._verify_page_exists(wiki_identified, old_page_name)

            # Construct new path based on input format
            if not new_page_name.startswith("/"):
                # If new_page_name is just a name (not a path), keep it in the same parent directory
                # Extract parent path from old_page_name
                parent_path = "/".join(old_page_name.rsplit("/", 1)[:-1])
                new_page_name = f"{parent_path}/{new_page_name}" if parent_path else f"/{new_page_name}"
            # If new_page_name starts with "/", use it as-is (full path move)

            logger.info(f"Renaming page from '{old_page_name}' to '{new_page_name}'")

            # Rename the page
            try:
                result = self._client.create_page_move(
                    project=self.config.project,
                    wiki_identifier=wiki_identified,
                    comment=f"Page rename from '{old_page_name}' to '{new_page_name}'",
                    page_move_parameters=WikiPageMoveParameters(new_path=new_page_name, path=old_page_name),
                    version_descriptor=GitVersionDescriptor(version=version_identifier, version_type=version_type),
                )
                return {
                    "response": self._serialize_page_move_response(result),
                    "status": "Success",
                    "message": f"Page renamed from '{old_page_name}' to '{new_page_name}'",
                }
            except AzureDevOpsServiceError as e:
                if INVALID_VERSION_ERROR in str(e):
                    # Retry the request without version_descriptor
                    result = self._client.create_page_move(
                        project=self.config.project,
                        wiki_identifier=wiki_identified,
                        comment=f"Page rename from '{old_page_name}' to '{new_page_name}'",
                        page_move_parameters=WikiPageMoveParameters(new_path=new_page_name, path=old_page_name),
                    )
                    return {
                        "response": self._serialize_page_move_response(result),
                        "status": "Success",
                        "message": f"Page renamed from '{old_page_name}' to '{new_page_name}' (without version)",
                    }
                else:
                    raise
        except Exception as e:
            logger.error(f"Unable to rename wiki page: {str(e)}")
            raise ToolException(f"Unable to rename wiki page: {str(e)}")


class MoveWikiPageTool(BaseAzureDevOpsWikiTool):
    """Tool to move wiki page to a different location in Azure DevOps.

    This tool uses the Azure DevOps page-moves endpoint to properly relocate pages
    while preserving all metadata, version history, and references. This is the
    correct way to re-arrange wiki pages for better organization.
    """

    name: str = MOVE_WIKI_PAGE_TOOL.name
    description: str = MOVE_WIKI_PAGE_TOOL.description
    args_schema: Type[BaseModel] = MovePageInput

    def _verify_page_exists(self, wiki_identified: str, page_path: str) -> None:
        """
        Verify that the source page exists.

        Raises ToolException if page doesn't exist.
        """
        try:
            self._client.get_page(project=self.config.project, wiki_identifier=wiki_identified, path=page_path)
            logger.info(f"Source page '{page_path}' exists and can be moved")
        except Exception as e:
            error_msg = f"Source page '{page_path}' not found. Cannot move a page that doesn't exist. Error: {str(e)}"
            logger.error(error_msg)
            raise ToolException(error_msg)

    def execute(
        self,
        wiki_identified: str,
        source_page_path: str,
        destination_page_path: str,
        version_identifier: str,
        version_type: str = "branch",
    ):
        """Move page to a different location in Azure DevOps wiki.

        Args:
            wiki_identified: Wiki ID or name (e.g., "MyWiki.wiki")
            source_page_path: Current page path to move (supports /page_id/page-slug format)
            destination_page_path: Destination path where page will be moved (full path required)
            version_identifier: Version string (branch name, tag, or commit SHA)
            version_type: Version type (branch, tag, or commit). Default is "branch"

        Returns:
            dict: Response with status, message, source and destination paths

        Raises:
            ToolException: If page doesn't exist, invalid paths, or API call fails
        """
        try:
            # Try to extract page ID from source path format like '/10330/This-is-sub-page'
            page_id = self._extract_page_id_from_path(source_page_path)

            if page_id is not None:
                # Get full hierarchical path using page ID
                logger.info(f"Extracted page ID {page_id} from source path, discovering full path...")
                full_path = self._get_full_path_from_id(wiki_identified, page_id)
                logger.info(f"Discovered full source path: {full_path}")
                source_page_path = full_path

            # Verify the source page exists before attempting move
            self._verify_page_exists(wiki_identified, source_page_path)

            # Ensure destination path starts with "/"
            if not destination_page_path.startswith("/"):
                destination_page_path = f"/{destination_page_path}"

            logger.info(f"Moving page from '{source_page_path}' to '{destination_page_path}'")

            # Move the page using Azure DevOps page-moves endpoint
            try:
                result = self._client.create_page_move(
                    project=self.config.project,
                    wiki_identifier=wiki_identified,
                    comment=f"Page moved from '{source_page_path}' to '{destination_page_path}'",
                    page_move_parameters=WikiPageMoveParameters(
                        path=source_page_path, new_path=destination_page_path, new_order=0
                    ),
                    version_descriptor=GitVersionDescriptor(version=version_identifier, version_type=version_type),
                )
                return {
                    "response": self._serialize_page_move_response(result),
                    "status": "Success",
                    "message": f"Page successfully moved from '{source_page_path}' to '{destination_page_path}'",
                    "source_path": source_page_path,
                    "destination_path": destination_page_path,
                }
            except AzureDevOpsServiceError as e:
                # Check if error message indicates invalid version (partial match for actual version string)
                if "either is invalid or does not exist" in str(e):
                    # Retry the request without version_descriptor
                    logger.info(f"Version '{version_identifier}' invalid, retrying without version descriptor")
                    result = self._client.create_page_move(
                        project=self.config.project,
                        wiki_identifier=wiki_identified,
                        comment=f"Page moved from '{source_page_path}' to '{destination_page_path}'",
                        page_move_parameters=WikiPageMoveParameters(
                            path=source_page_path, new_path=destination_page_path, new_order=0
                        ),
                    )
                    return {
                        "response": self._serialize_page_move_response(result),
                        "status": "Success",
                        "message": f"Page successfully moved from '{source_page_path}' to '{destination_page_path}' (without version)",
                        "source_path": source_page_path,
                        "destination_path": destination_page_path,
                    }
                else:
                    raise
        except Exception as e:
            logger.error(f"Unable to move wiki page: {str(e)}")
            raise ToolException(f"Unable to move wiki page: {str(e)}")


class CreateWikiPageTool(BaseAzureDevOpsWikiTool, FileToolMixin):
    """Tool to create a new wiki page in Azure DevOps with optional file attachments."""

    name: str = CREATE_WIKI_PAGE_TOOL.name
    description: str = CREATE_WIKI_PAGE_TOOL.description
    args_schema: Type[BaseModel] = CreatePageInput

    def _process_attachments(self) -> str:
        """
        Process all attached files and generate markdown links for them.

        Returns:
            str: Markdown text with attachment links
        """
        files = self._resolve_files()

        if not files:
            return ""

        attachment_links = []
        logger.info(f"Processing {len(files)} attachments...")

        for filename, (content, mime_type) in files.items():
            try:
                # Upload the attachment and get its URL
                attachment_url = self._upload_attachment(filename, content)

                # Create markdown link for the attachment
                attachment_links.append(f"[{filename}]({attachment_url})")

            except Exception as e:
                logger.warning(f"Skipping attachment '{filename}' due to error: {str(e)}")
                continue

        if attachment_links:
            return "\n\n## Attachments\n\n" + "\n".join([f"- {link}" for link in attachment_links])

        return ""

    def execute(
        self,
        wiki_identified: str,
        parent_page_path: str,
        new_page_name: str,
        page_content: str,
        version_identifier: str,
        version_type: str = "branch",
    ):
        """Create a new ADO wiki page under the specified parent page."""
        try:
            # Create wiki if needed
            error = self._create_wiki_if_not_exists(wiki_identified)
            if error:
                raise ToolException(error)

            # Try to extract page ID from parent path format like '/10330/This-is-sub-page'
            page_id = self._extract_page_id_from_path(parent_page_path)

            if page_id is not None:
                # Get full hierarchical path using page ID
                logger.info(f"Extracted page ID {page_id} from parent path, discovering full path...")
                parent_page_path = self._get_full_path_from_id(wiki_identified, page_id)
                logger.info(f"Discovered full parent path: {parent_page_path}")

            # Construct the full path for the new page
            if parent_page_path == "/":
                full_path = f"/{new_page_name}"
            else:
                # Ensure parent path starts with /
                parent = parent_page_path if parent_page_path.startswith("/") else f"/{parent_page_path}"
                full_path = f"{parent}/{new_page_name}"

            logger.info(f"Creating new page at path: {full_path}")

            # Process attachments if any
            attachments_markdown = self._process_attachments()
            if attachments_markdown:
                page_content = page_content + attachments_markdown
                logger.info("Added attachment links to page content")

            # Create the page
            try:
                result = self._client.create_or_update_page(
                    project=self.config.project,
                    wiki_identifier=wiki_identified,
                    path=full_path,
                    parameters=WikiPageCreateOrUpdateParameters(content=page_content),
                    version=None,  # New page, no version
                    version_descriptor=GitVersionDescriptor(version=version_identifier, version_type=version_type),
                )
                return {
                    "response": self._serialize_page_response(result),
                    "message": f"Page '{full_path}' has been created successfully",
                }
            except AzureDevOpsServiceError as e:
                if INVALID_VERSION_ERROR in str(e):
                    # Note: page_content already includes attachments from above
                    # Retry without version descriptor
                    result = self._client.create_or_update_page(
                        project=self.config.project,
                        wiki_identifier=wiki_identified,
                        path=full_path,
                        parameters=WikiPageCreateOrUpdateParameters(content=page_content),
                        version=None,
                    )
                    return {
                        "response": self._serialize_page_response(result),
                        "message": f"Page '{full_path}' has been created successfully (without version)",
                    }
                else:
                    raise
        except Exception as e:
            error_msg = f"Unable to create wiki page: {str(e)}"
            logger.error(error_msg)
            raise ToolException(error_msg)


class ModifyWikiPageTool(BaseAzureDevOpsWikiTool):
    """Tool to update existing wiki page in Azure DevOps."""

    name: str = MODIFY_WIKI_PAGE_TOOL.name
    description: str = MODIFY_WIKI_PAGE_TOOL.description
    args_schema: Type[BaseModel] = ModifyPageInput

    def _get_page_version(self, wiki_identified: str, page_name: str) -> str:
        """
        Get page version (eTag) if page exists.

        Raises ToolException if page doesn't exist.
        """
        try:
            page = self._client.get_page(project=self.config.project, wiki_identifier=wiki_identified, path=page_name)
            version = page.eTag
            logger.info(f"Existing page found with eTag: {version}")
            return version
        except Exception as e:
            error_msg = (
                f"Page '{page_name}' not found. Use 'create_wiki_page' tool to create new pages. Error: {str(e)}"
            )
            logger.error(error_msg)
            raise ToolException(error_msg)

    def execute(
        self,
        wiki_identified: str,
        page_name: str,
        page_content: str,
        version_identifier: str,
        version_type: str = "branch",
    ):
        """Update existing ADO wiki page content."""
        try:
            # Try to extract page ID from path format like '/10330/This-is-sub-page'
            page_id = self._extract_page_id_from_path(page_name)

            if page_id is not None:
                # Get full hierarchical path using page ID
                logger.info(f"Extracted page ID {page_id} from path, discovering full path...")
                full_path = self._get_full_path_from_id(wiki_identified, page_id)
                logger.info(f"Discovered full path: {full_path}")
                page_name = full_path

            # Get page version (will fail if page doesn't exist)
            version = self._get_page_version(wiki_identified, page_name)

            # Update the page
            try:
                result = self._client.create_or_update_page(
                    project=self.config.project,
                    wiki_identifier=wiki_identified,
                    path=page_name,
                    parameters=WikiPageCreateOrUpdateParameters(content=page_content),
                    version=version,
                    version_descriptor=GitVersionDescriptor(version=version_identifier, version_type=version_type),
                )
                return {
                    "response": self._serialize_page_response(result),
                    "message": f"Page '{page_name}' has been updated successfully",
                }
            except AzureDevOpsServiceError as e:
                if INVALID_VERSION_ERROR in str(e):
                    # Retry without version descriptor
                    result = self._client.create_or_update_page(
                        project=self.config.project,
                        wiki_identifier=wiki_identified,
                        path=page_name,
                        parameters=WikiPageCreateOrUpdateParameters(content=page_content),
                        version=version,
                    )
                    return {
                        "response": self._serialize_page_response(result),
                        "message": f"Page '{page_name}' has been updated successfully (without version)",
                    }
                else:
                    raise
        except Exception as e:
            error_msg = f"Unable to modify wiki page: {str(e)}"
            logger.error(error_msg)
            raise ToolException(error_msg)


class SearchWikiPagesTool(BaseAzureDevOpsWikiTool):
    """Tool to search for text content across wiki pages in Azure DevOps."""

    name: str = SEARCH_WIKI_PAGES_TOOL.name
    description: str = SEARCH_WIKI_PAGES_TOOL.description
    args_schema: Type[BaseModel] = SearchWikiPagesInput

    def execute(
        self,
        wiki_identified: str,
        search_text: str,
        include_context: Optional[bool] = True,
        max_results: Optional[int] = 50,
    ):
        """
        Search for text content across wiki pages using Azure DevOps Search API.

        Args:
            wiki_identified: Wiki name or ID
            search_text: Text to search for
            include_context: Whether to include content snippets (hits)
            max_results: Maximum number of results to return

        Returns:
            Search results with full URLs added to each result
        """
        try:
            # Create search request using SDK models
            search_request = WikiSearchRequest(
                search_text=search_text,
                skip=0,
                top=min(max_results, 100),  # API max is 100 per request
                filters={"Wiki": [wiki_identified]},
                order_by=None,
                include_facets=False,
            )

            # Call search API using SDK
            search_response = self._search_client.fetch_wiki_search_results(
                request=search_request, project=self.config.project
            )

            # Convert SDK response to dict
            response_dict = search_response.as_dict()

            # Process results to add full URLs
            if "results" in response_dict:
                from urllib.parse import quote

                for result in response_dict["results"]:
                    # Remove hits if include_context is False
                    if not include_context and "hits" in result:
                        del result["hits"]

                    # Construct URL using query parameter pattern
                    # Format: {organization_url}/{project}/_wiki/wikis/{wikiName}?pagePath={encodedPath}
                    wiki_name = result.get("wiki", {}).get("name")
                    path = result.get("path", "")

                    if wiki_name and path:
                        # Remove .md extension and URL-encode the path (keep leading slash)
                        page_path = path.replace(".md", "").replace(".MD", "")
                        encoded_path = quote(page_path, safe="")

                        # Construct full URL using config's organization_url and project
                        full_url = (
                            f"{self.config.organization_url}/{self.config.project}"
                            f"/_wiki/wikis/{quote(wiki_name, safe='')}"
                            f"?pagePath={encoded_path}"
                        )
                        result["full_url"] = full_url

            return response_dict

        except Exception as e:
            error_msg = f"Unable to search wiki pages: {str(e)}"
            logger.error(error_msg)
            raise ToolException(error_msg)


class GetWikiPageCommentsByIdTool(BaseAzureDevOpsWikiTool):
    """Tool to get comments for a wiki page by ID in Azure DevOps."""

    name: str = GET_WIKI_PAGE_COMMENTS_BY_ID_TOOL.name
    description: str = GET_WIKI_PAGE_COMMENTS_BY_ID_TOOL.description
    args_schema: Type[BaseModel] = GetPageCommentsByIdInput

    def execute(
        self,
        wiki_identified: str,
        page_id: int,
        limit_total: Optional[int] = None,
        include_deleted: Optional[bool] = False,
        expand: Optional[str] = "none",
        order: Optional[str] = None,
    ):
        """
        Get comments for a wiki page by page ID.

        Args:
            wiki_identified: Wiki ID or name
            page_id: Page ID
            limit_total: Maximum number of comments to return (None for all)
            include_deleted: Include deleted comments
            expand: Expand parameters (all, none, reactions, renderedText, renderedTextOnly)
            order: Sort order (asc, desc)

        Returns:
            Dict with 'comments', 'count', 'total_count', 'has_more' keys
        """
        try:
            return self._get_wiki_page_comments(
                wiki_identified=wiki_identified,
                page_id=page_id,
                limit_total=limit_total,
                include_deleted=include_deleted,
                expand=expand,
                order=order,
            )
        except Exception as e:
            logger.error(f"Error getting wiki page comments: {str(e)}")
            raise ToolException(f"Error getting wiki page comments: {str(e)}")


class GetWikiPageCommentsByPathTool(BaseAzureDevOpsWikiTool):
    """Tool to get comments for a wiki page by path in Azure DevOps."""

    name: str = GET_WIKI_PAGE_COMMENTS_BY_PATH_TOOL.name
    description: str = GET_WIKI_PAGE_COMMENTS_BY_PATH_TOOL.description
    args_schema: Type[BaseModel] = GetPageCommentsByPathInput

    def execute(
        self,
        wiki_identified: str,
        page_name: str,
        limit_total: Optional[int] = None,
        include_deleted: Optional[bool] = False,
        expand: Optional[str] = "none",
        order: Optional[str] = None,
    ):
        """
        Get comments for a wiki page by page path.

        Automatically resolves page ID from path. Supports both:
        1. Path with ID format: '/10330/Page-Name' (extracts ID)
        2. Full path format: '/Parent/Child/Page' (looks up ID)

        Args:
            wiki_identified: Wiki ID or name
            page_name: Wiki page path
            limit_total: Maximum number of comments to return (None for all)
            include_deleted: Include deleted comments
            expand: Expand parameters (all, none, reactions, renderedText, renderedTextOnly)
            order: Sort order (asc, desc)

        Returns:
            Dict with 'comments', 'count', 'total_count', 'has_more' keys
        """
        try:
            # Try to extract page ID from path format like '/10330/This-is-sub-page'
            page_id = self._extract_page_id_from_path(page_name)

            if page_id is not None:
                # Successfully extracted page ID from path
                logger.info(f"Extracted page ID {page_id} from path '{page_name}'")
            else:
                # Need to look up page ID from full path
                logger.info(f"Looking up page ID for path '{page_name}'")
                page_id, _, _ = self._get_page_info(
                    wiki_identified=wiki_identified,
                    page_path=page_name,
                    include_content=False,
                )

                if page_id is None:
                    raise ToolException(f"Could not find page with path '{page_name}' in wiki '{wiki_identified}'")

                logger.info(f"Resolved page ID {page_id} for path '{page_name}'")

            # Get comments using the resolved page ID
            return self._get_wiki_page_comments(
                wiki_identified=wiki_identified,
                page_id=page_id,
                limit_total=limit_total,
                include_deleted=include_deleted,
                expand=expand,
                order=order,
            )
        except Exception as e:
            logger.error(f"Error getting wiki page comments: {str(e)}")
            raise ToolException(f"Error getting wiki page comments: {str(e)}")


class AddWikiAttachmentTool(BaseAzureDevOpsWikiTool, FileToolMixin):
    """Tool to add file attachments to existing wiki pages in Azure DevOps."""

    name: str = ADD_ATTACHMENT_TOOL.name
    description: str = ADD_ATTACHMENT_TOOL.description
    args_schema: Type[BaseModel] = AddAttachmentInput

    def _validate_files(self, files: Dict[str, Tuple[bytes, str]]) -> Dict[str, Tuple[bytes, str]]:
        """
        Validate files against size limits and return validated files.

        Args:
            files: Dict mapping filename to (content, mime_type) tuples

        Returns:
            Dict of validated files

        Raises:
            ToolException: If any file exceeds size limit or no files provided
        """
        if not files:
            raise ToolException(
                "No files provided for upload. "
                "Please provide files using the 'input_files' configuration parameter. "
                "Example: config.input_files = ['path/to/file.pdf', 'path/to/image.png']"
            )

        validated_files = {}
        for filename, (content, mime_type) in files.items():
            file_size = len(content)
            if file_size > MAX_ATTACHMENT_SIZE:
                size_mb = file_size / (1024 * 1024)
                max_size_mb = MAX_ATTACHMENT_SIZE / (1024 * 1024)
                error_msg = f"File '{filename}' ({size_mb:.2f}MB) exceeds maximum size of {max_size_mb:.0f}MB"
                logger.error(error_msg)
                raise ToolException(error_msg)
            validated_files[filename] = (content, mime_type)
            logger.debug(f"Validated file '{filename}': {file_size} bytes, type: {mime_type}")

        logger.info(f"Validated {len(validated_files)} file(s) for upload")
        return validated_files

    def _process_and_upload_attachments(self, wiki_identified: str) -> Tuple[str, int]:
        """
        Process all attached files, upload them, and generate markdown links.

        Args:
            wiki_identified: Wiki ID or wiki name

        Returns:
            Tuple of (markdown_section, attachment_count)
        """
        files = self._resolve_files()
        validated_files = self._validate_files(files)

        attachment_links = []
        upload_errors = {}  # Track errors per file
        renamed_files = {}  # Track files that were renamed due to duplicates
        logger.info(f"Processing {len(validated_files)} attachments...")

        for filename, (content, mime_type) in validated_files.items():
            try:
                # Upload the attachment using wiki-specific API with duplicate handling
                logger.info(f"Uploading attachment '{filename}' ({len(content)} bytes)...")
                attachment_path, actual_filename = self._upload_with_duplicate_handling(
                    wiki_identified, filename, content
                )

                # Create markdown link for the attachment using the path
                # Azure DevOps wiki uses relative paths like '/.attachments/filename.pdf'
                attachment_links.append(f"[{actual_filename}]({attachment_path})")
                logger.info(f"Successfully uploaded '{filename}'")

                # Track if file was renamed
                if actual_filename != filename:
                    renamed_files[filename] = actual_filename

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Failed to upload attachment '{filename}': {error_msg}")
                upload_errors[filename] = error_msg
                continue

        # Provide detailed feedback about upload results
        if upload_errors:
            logger.warning(f"Failed to upload {len(upload_errors)} file(s): {', '.join(upload_errors.keys())}")

        if renamed_files:
            logger.info(
                f"Renamed {len(renamed_files)} duplicate file(s): "
                + ", ".join([f"'{orig}' → '{new}'" for orig, new in renamed_files.items()])
            )

        if attachment_links:
            markdown_section = "\n\n## Attachments\n\n" + "\n".join([f"- {link}" for link in attachment_links])
            success_count = len(attachment_links)

            # Add note about renamed files if any
            if renamed_files:
                logger.info(f"Successfully uploaded {success_count} attachment(s) ({len(renamed_files)} renamed)")
            else:
                logger.info(f"Successfully uploaded {success_count} attachment(s)")

            return markdown_section, success_count

        # No attachments were uploaded successfully - provide detailed error info
        if upload_errors:
            error_details = "\n".join([f"  - {fname}: {err}" for fname, err in upload_errors.items()])
            raise ToolException(f"All {len(upload_errors)} file(s) failed to upload:\n{error_details}")

        return "", 0

    def _merge_attachments_section(self, current_content: str, new_attachments_markdown: str) -> str:
        """
        Merge new attachments with existing attachments section if it exists.

        Args:
            current_content: Current page content (may contain existing attachments)
            new_attachments_markdown: New attachments markdown section to add

        Returns:
            Updated content with merged attachments section
        """
        # Pattern to match "## Attachments" header (with or without extra whitespace/newlines/indentation)
        attachments_pattern = r'(?:^|\n)\s*##\s+Attachments\s*\n'

        # Check if current content has an "## Attachments" section
        match = re.search(attachments_pattern, current_content)

        if match:
            # Extract new attachment links from the new markdown (skip the header)
            new_links = new_attachments_markdown.split("\n\n## Attachments\n\n", 1)[-1]

            # Find the position right after the "## Attachments" header
            section_start = match.end()

            # Find all existing attachment links after the header
            # Pattern matches markdown links like - [filename](path)
            remaining_content = current_content[section_start:]

            # Find the end of attachment links by looking for the next section header or end of content
            # Attachments are lines starting with optional whitespace, then "- ["
            attachment_line_pattern = r'^\s*-\s*\['
            lines_after_header = remaining_content.split('\n')
            last_attachment_line = 0

            for i, line in enumerate(lines_after_header):
                if re.match(attachment_line_pattern, line):
                    last_attachment_line = i
                elif last_attachment_line > 0 and line.strip() and not re.match(attachment_line_pattern, line):
                    # Hit a non-empty, non-attachment line after finding attachments - stop here
                    break

            # Calculate insert position: after the last attachment line
            if last_attachment_line > 0:
                # Join lines up to and including the last attachment
                lines_to_keep = lines_after_header[: last_attachment_line + 1]
                insert_position = section_start + len('\n'.join(lines_to_keep)) + 1  # +1 for the newline
            else:
                # No existing attachments found, insert right after header
                insert_position = section_start

            # Insert new attachment links after existing ones
            updated_content = current_content[:insert_position] + "\n" + new_links + current_content[insert_position:]
            logger.info("Merged new attachments with existing attachments section")
            return updated_content
        else:
            # No existing attachments section, append new section to the end
            updated_content = current_content + new_attachments_markdown
            logger.info("Added new attachments section to page")
            return updated_content

    def execute(
        self,
        wiki_identified: str,
        page_name: str,
        version_identifier: str,
        version_type: str = "branch",
    ):
        """
        Add file attachments to an existing ADO wiki page.

        Args:
            wiki_identified: Wiki ID or wiki name
            page_name: Wiki page path
            version_identifier: Version string identifier (name of tag/branch, SHA1 of commit)
            version_type: Version type (branch, tag, or commit). Default is "branch"

        Returns:
            Dict with message, attachments_added count, and page_url
        """
        try:
            # Try to extract page ID from path format like '/10330/This-is-sub-page'
            page_id = self._extract_page_id_from_path(page_name)

            if page_id is not None:
                # Get full hierarchical path using page ID
                logger.info(f"Extracted page ID {page_id} from path, discovering full path...")
                full_path = self._get_full_path_from_id(wiki_identified, page_id)
                logger.info(f"Discovered full path: {full_path}")
                page_name = full_path

            # Get the existing page content and version
            try:
                page = self._client.get_page(
                    project=self.config.project,
                    wiki_identifier=wiki_identified,
                    path=page_name,
                    include_content=True,
                )
            except Exception as page_error:
                error_msg = (
                    f"Page '{page_name}' not found. Use 'create_wiki_page' tool to create new pages. "
                    f"Error: {str(page_error)}"
                )
                logger.error(error_msg)
                raise ToolException(error_msg)

            # Get page version (eTag) and current content
            version = page.eTag
            current_content = page.page.content
            logger.info(f"Retrieved existing page with eTag: {version}")

            # Process and upload attachments
            attachments_markdown, attachment_count = self._process_and_upload_attachments(wiki_identified)

            # Merge with existing attachments section if it exists
            updated_content = self._merge_attachments_section(current_content, attachments_markdown)
            logger.info(f"Added {attachment_count} attachment(s) to page content")

            # Update the page with new content
            try:
                _ = self._client.create_or_update_page(
                    project=self.config.project,
                    wiki_identifier=wiki_identified,
                    path=page_name,
                    parameters=WikiPageCreateOrUpdateParameters(content=updated_content),
                    version=version,
                    version_descriptor=GitVersionDescriptor(version=version_identifier, version_type=version_type),
                )

                # Construct page URL
                page_url = self._construct_page_url(wiki_identified, page.page.id, page_name.split("/")[-1])

                return {
                    "message": f"Added {attachment_count} attachment(s) to page '{page_name}' successfully",
                    "attachments_added": attachment_count,
                    "page_url": page_url,
                }
            except AzureDevOpsServiceError as e:
                if INVALID_VERSION_ERROR in str(e):
                    # Retry without version descriptor
                    logger.info("Retrying page update without version descriptor")
                    _ = self._client.create_or_update_page(
                        project=self.config.project,
                        wiki_identifier=wiki_identified,
                        path=page_name,
                        parameters=WikiPageCreateOrUpdateParameters(content=updated_content),
                        version=version,
                    )

                    # Construct page URL
                    page_url = self._construct_page_url(wiki_identified, page.page.id, page_name.split("/")[-1])

                    return {
                        "message": f"Added {attachment_count} attachment(s) to page '{page_name}' successfully (without version)",
                        "attachments_added": attachment_count,
                        "page_url": page_url,
                    }
                else:
                    raise

        except ToolException:
            # Re-raise ToolExceptions as-is
            raise
        except Exception as e:
            error_msg = f"Unable to add attachments to wiki page: {str(e)}"
            logger.error(error_msg)
            raise ToolException(error_msg)


class GetPageStatsByIdTool(BaseAzureDevOpsWikiTool):
    """Tool to get view statistics for a wiki page by ID in Azure DevOps."""

    name: str = GET_PAGE_STATS_BY_ID_TOOL.name
    description: str = GET_PAGE_STATS_BY_ID_TOOL.description
    args_schema: Type[BaseModel] = GetPageStatsByIdInput

    def execute(self, wiki_identified: str, page_id: int, page_views_for_days: int = 30):
        """Retrieve view statistics for a wiki page by its ID."""
        return self._get_wiki_page_stats(
            wiki_identified=wiki_identified,
            page_id=page_id,
            page_views_for_days=page_views_for_days,
        )


class GetPageStatsByPathTool(BaseAzureDevOpsWikiTool):
    """Tool to get view statistics for a wiki page by path in Azure DevOps."""

    name: str = GET_PAGE_STATS_BY_PATH_TOOL.name
    description: str = GET_PAGE_STATS_BY_PATH_TOOL.description
    args_schema: Type[BaseModel] = GetPageStatsByPathInput

    def execute(self, wiki_identified: str, page_name: str, page_views_for_days: int = 30):
        """Retrieve view statistics for a wiki page by its path."""
        page_id = self._extract_page_id_from_path(page_name)

        if page_id is None:
            logger.info(f"Resolving page ID from path '{page_name}'...")
            try:
                page_response = self._client.get_page(
                    project=self.config.project,
                    wiki_identifier=wiki_identified,
                    path=page_name,
                    include_content=False,
                )
                page_id = page_response.page.id
                logger.info(f"Resolved path '{page_name}' to page ID {page_id}")
            except Exception as e:
                logger.error(f"Failed to resolve page path '{page_name}' to ID: {str(e)}")
                raise ToolException(f"Failed to resolve page path '{page_name}' to ID: {str(e)}")

        return self._get_wiki_page_stats(
            wiki_identified=wiki_identified,
            page_id=page_id,
            page_views_for_days=page_views_for_days,
        )
