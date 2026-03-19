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

"""
SharePoint Online datasource loader using Microsoft Graph API.

This loader supports indexing SharePoint Online site pages, documents, and lists
using Microsoft Graph API with Azure AD application authentication.
"""

from __future__ import annotations

import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import urlparse

import requests
from langchain_community.document_loaders import CSVLoader, PyMuPDFLoader, UnstructuredPowerPointLoader
from langchain_community.document_loaders.parsers import BaseImageBlobParser
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_markitdown import (
    DocxLoader,
    XlsxLoader,
    HtmlLoader,
    EpubLoader,
    IpynbLoader,
    OutlookMsgLoader,
    PlainTextLoader,
    ZipLoader,
)

from codemie.configs import logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.utils import _create_pathspec_from_filter
from codemie.datasource.datasources_config import SHAREPOINT_CONFIG
from codemie.datasource.exceptions import (
    MissingIntegrationException,
    UnauthorizedException,
)
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.rest_api.models.index import IndexKnowledgeBaseFileTypes

_MAX_RETRY_AFTER_SECONDS = 60


@dataclass
class SharePointAuthConfig:
    """Authentication configuration for SharePoint loader."""

    auth_type: str = "app"
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    access_token: str = field(default="")
    refresh_token: str = field(default="")
    expires_at: int = 0
    setting_id: str | None = None


class SharePointLoader(BaseLoader, BaseDatasourceLoader):
    """
    Loader for SharePoint Online documents using Microsoft Graph API.

    Supports:
    - Site Pages (.aspx)
    - Document Libraries: PDF, DOCX, XLSX, PPTX, CSV, TXT, XML, HTML, EPUB, IPYNB, MSG,
      YAML, YML, JSON, ZIP, Audio, Images
    - Lists and List Items
    - Wiki Pages

    Uses the same file processing loaders as FilesDatasourceLoader for consistency.

    Authentication: Azure AD App Registration (tenant_id, client_id, client_secret)

    Example:
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/sitename",
            path_filter="/Shared Documents/*",
            tenant_id="<tenant-id>",
            client_id="<client-id>",
            client_secret="<client-secret>",
            include_pages=True,
            include_documents=True,
        )
        documents = list(loader.lazy_load())
    """

    DOCUMENTS_COUNT_KEY = "documents_count_key"
    TOTAL_DOCUMENTS_KEY = "total_documents"
    SKIPPED_DOCUMENTS_KEY = "skipped_documents"

    # Constants for Microsoft Graph API
    ODATA_NEXT_LINK = "@odata.nextLink"
    FORM_TEMPLATES_FOLDER = "Form Templates"

    # File loaders mapping (same as FilesDatasourceLoader)
    _LOADERS = {
        IndexKnowledgeBaseFileTypes.CSV.value: CSVLoader,
        IndexKnowledgeBaseFileTypes.PDF.value: PyMuPDFLoader,
        IndexKnowledgeBaseFileTypes.PPTX.value: UnstructuredPowerPointLoader,
        IndexKnowledgeBaseFileTypes.DOCX.value: DocxLoader,
        IndexKnowledgeBaseFileTypes.XLSX.value: XlsxLoader,
        IndexKnowledgeBaseFileTypes.HTML.value: HtmlLoader,
        IndexKnowledgeBaseFileTypes.EPUB.value: EpubLoader,
        IndexKnowledgeBaseFileTypes.IPYNB.value: IpynbLoader,
        IndexKnowledgeBaseFileTypes.MSG.value: OutlookMsgLoader,
        IndexKnowledgeBaseFileTypes.ZIP.value: ZipLoader,
    }

    # File extensions to explicitly skip (executables, system files, media files)
    # All other files will be attempted with PlainTextLoader as fallback
    SKIP_EXTENSIONS = {
        # Executables and binaries
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".app",
        ".msi",
        ".dmg",
        ".pkg",
        ".deb",
        ".rpm",
        # Media files (images, audio, video)
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".webp",
        ".svg",
        ".ico",
        ".mp3",
        ".wav",
        ".m4a",
        ".aac",
        ".ogg",
        ".flac",
        ".wma",
        ".mp4",
        ".avi",
        ".mov",
        ".wmv",
        ".mkv",
        ".flv",
        ".webm",
    }

    def __init__(
        self,
        site_url: str,
        path_filter: str,
        auth_config: SharePointAuthConfig | None = None,
        include_pages: bool = True,
        include_documents: bool = True,
        include_lists: bool = True,
        max_file_size_mb: int = 50,
        files_filter: str = "",
        request_uuid: str | None = None,
    ):
        """
        Initialize SharePoint loader.

        Args:
            site_url: Full SharePoint site URL (e.g., https://tenant.sharepoint.com/sites/sitename)
            path_filter: Path filter for documents (e.g., "/Shared Documents/*")
            auth_config: Authentication configuration (credentials and auth type)
            include_pages: Whether to include site pages
            include_documents: Whether to include documents from libraries
            include_lists: Whether to include list items
            max_file_size_mb: Maximum file size to process (MB)
            files_filter: Gitignore-style filter for document extensions/names
            request_uuid: Request UUID for tracking LLM usage
        """
        if auth_config is None:
            auth_config = SharePointAuthConfig()

        self.site_url = site_url
        self.path_filter = path_filter
        self.tenant_id = auth_config.tenant_id
        self.client_id = auth_config.client_id
        self.client_secret = auth_config.client_secret
        self.include_pages = include_pages

        # OAuth fields
        self.auth_type = auth_config.auth_type
        self._stored_access_token = auth_config.access_token or None
        self._stored_refresh_token = auth_config.refresh_token
        self._stored_expires_at = auth_config.expires_at
        self._oauth_setting_id = auth_config.setting_id

        # Log configuration for debugging
        logger.info(
            f"SharePointLoader initialized - "
            f"auth_type={auth_config.auth_type}, "
            f"include_pages={include_pages}, "
            f"include_documents={include_documents}, "
            f"include_lists={include_lists}"
        )
        self.include_documents = include_documents
        self.include_lists = include_lists
        self.max_file_size_mb = max_file_size_mb
        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024
        self.files_filter = files_filter or ""
        self.request_uuid = request_uuid

        self._access_token: str | None = None
        self._site_id: str | None = None
        self._site_hostname: str | None = None
        self._site_path: str | None = None

        # Statistics tracking
        self._total_files_found = 0
        self._total_files_processed = 0
        self._total_files_skipped = 0

        # Loader kwargs (same as FilesDatasourceLoader)
        self._loader_kwargs = {
            IndexKnowledgeBaseFileTypes.CSV.value: {"csv_args": {"delimiter": ","}},
            IndexKnowledgeBaseFileTypes.PDF.value: {
                "mode": "page",
                "images_inner_format": "markdown-img",
                "extract_images": True,
                "extract_tables": "markdown",
            },
            IndexKnowledgeBaseFileTypes.XLSX.value: {"split_by_page": True},
        }

        # Parse site URL
        self._parse_site_url()

    def _parse_site_url(self):
        """Parse SharePoint site URL to extract hostname and path."""
        parsed = urlparse(self.site_url)
        self._site_hostname = parsed.netloc
        self._site_path = parsed.path

    def _get_access_token(self) -> str:
        """
        Acquire OAuth2 access token.

        For app auth: uses client credentials flow (tenant_id + client_id + client_secret).
        For oauth auth: returns stored delegated token, refreshing it when expired.

        Returns:
            Access token string

        Raises:
            UnauthorizedException: If authentication fails
        """
        if self._access_token:
            return self._access_token

        if self.auth_type == "oauth":
            return self._get_oauth_access_token()

        return self._get_app_access_token()

    def _get_app_access_token(self) -> str:
        """Acquire token via client credentials (app auth)."""
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }

        try:
            response = requests.post(token_url, data=data, timeout=SHAREPOINT_CONFIG.loader_timeout)
            response.raise_for_status()
            token_data = response.json()
            self._access_token = token_data["access_token"]
            logger.info("Successfully acquired SharePoint access token")
            return self._access_token
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to acquire SharePoint access token: {e}")
            raise UnauthorizedException(datasource_type="SharePoint")

    def _get_oauth_access_token(self) -> str:
        """Return stored OAuth delegated access token."""
        if not self._stored_access_token:
            raise UnauthorizedException(datasource_type="SharePoint")
        self._access_token = self._stored_access_token
        return self._access_token

    def _get_headers(self) -> dict:
        """Get HTTP headers with authorization token."""
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    def _get_site_id(self) -> str:
        """
        Get SharePoint site ID from site URL using Graph API.

        Returns:
            Site ID string

        Raises:
            UnauthorizedException: If site not accessible
        """
        if self._site_id:
            return self._site_id

        # Graph API endpoint format: /sites/{hostname}:{site-path}
        site_path_encoded = self._site_path.rstrip("/")
        url = (
            f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}/"
            f"sites/{self._site_hostname}:{site_path_encoded}"
        )

        try:
            response = requests.get(url, headers=self._get_headers(), timeout=SHAREPOINT_CONFIG.loader_timeout)
            response.raise_for_status()
            site_data = response.json()
            self._site_id = site_data["id"]
            logger.info(f"Resolved SharePoint site ID: {self._site_id}")
            return self._site_id
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to resolve SharePoint site ID: {e}")
            raise UnauthorizedException(datasource_type="SharePoint")

    def _validate_creds(self):
        """
        Validate credentials by attempting to access the site.

        Raises:
            MissingIntegrationException: If credentials missing
            UnauthorizedException: If authentication fails
        """
        if self.auth_type == "oauth":
            if not self._stored_access_token:
                logger.error("Missing SharePoint OAuth access token")
                raise MissingIntegrationException("SharePoint")
        else:
            if not self.tenant_id or not self.client_id or not self.client_secret:
                logger.error("Missing SharePoint credentials")
                raise MissingIntegrationException("SharePoint")

        # Validate by getting site ID
        self._get_site_id()

    def _make_graph_request(self, url: str, retry_count: int = 0) -> dict | None:
        """
        Make a Graph API request with error handling and retry logic.

        Args:
            url: Graph API URL
            retry_count: Current retry attempt

        Returns:
            Response JSON or None if failed
        """
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=SHAREPOINT_CONFIG.loader_timeout)

            if response.status_code == 401 and retry_count < SHAREPOINT_CONFIG.max_retries:
                # Token expired, refresh and retry
                logger.warning("Access token expired, refreshing...")
                self._access_token = None
                return self._make_graph_request(url, retry_count + 1)

            if response.status_code == 429 and retry_count < SHAREPOINT_CONFIG.max_retries:
                # Rate limited, wait and retry
                retry_after = min(int(response.headers.get("Retry-After", 5)), _MAX_RETRY_AFTER_SECONDS)
                logger.warning(f"Rate limited, retrying after {retry_after} seconds...")
                time.sleep(retry_after)
                return self._make_graph_request(url, retry_count + 1)

            if response.status_code == 404:
                logger.info(f"Resource not found: {url}")
                return None

            if response.status_code == 403:
                logger.warning(f"Access denied to resource: {url}")
                return None

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Graph API request failed: {e}")
            if retry_count < SHAREPOINT_CONFIG.max_retries:
                return self._make_graph_request(url, retry_count + 1)
            return None

    def _should_process_page(self, page: dict) -> bool:
        """Check if page should be processed based on path filter."""
        if not self.path_filter or self.path_filter == "*":
            return True

        page_url = page.get("webUrl", "")
        if not self._matches_path_filter(page_url):
            logger.debug(f"Skipping page not matching path filter: {page.get('title', '')}")
            return False
        return True

    def _fetch_page_details(self, site_id: str, page_id: str, page: dict) -> dict:
        """Fetch detailed page content with canvasLayout."""
        page_detail_url = (
            f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}/"
            f"sites/{site_id}/pages/{page_id}?$expand=canvasLayout"
        )
        page_detail = self._make_graph_request(page_detail_url)
        return page_detail if page_detail else page

    def _create_page_dict(self, page_data: dict, content: str) -> dict:
        """Create page dictionary with extracted data."""
        return {
            "type": "page",
            "id": page_data.get("id"),
            "title": page_data.get("title", ""),
            "content": content,
            "url": page_data.get("webUrl", ""),
            "created": page_data.get("createdDateTime"),
            "modified": page_data.get("lastModifiedDateTime"),
        }

    def _load_site_pages(self) -> Iterator[dict]:
        """
        Load site pages from SharePoint.

        Yields:
            Dictionary containing page data
        """
        site_id = self._get_site_id()
        url = f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}/sites/{site_id}/pages"

        pages_found = 0
        pages_with_content = 0

        while url:
            data = self._make_graph_request(url)
            if not data:
                break

            for page in data.get("value", []):
                pages_found += 1

                if not self._should_process_page(page):
                    continue

                page_id = page.get("id")
                page_data = self._fetch_page_details(site_id, page_id, page)
                content = self._extract_page_content(page_data)

                if content:
                    pages_with_content += 1
                    yield self._create_page_dict(page_data, content)
                else:
                    logger.warning(f"Page has no content: {page_data.get('title', '')} ({page_data.get('webUrl', '')})")

            url = data.get(self.ODATA_NEXT_LINK)

        if pages_found > 0:
            logger.info(
                f"SharePoint pages scan complete. Found: {pages_found}, "
                f"With content: {pages_with_content}, Empty: {pages_found - pages_with_content}"
            )

    def _strip_html_to_text(self, html: str) -> str:
        """Strip HTML tags and normalize whitespace."""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _extract_webpart_text(self, webpart: dict) -> str | None:
        """Extract text from a single webpart."""
        inner_html = webpart.get("innerHtml", "")
        if not inner_html:
            return None

        text = self._strip_html_to_text(inner_html)
        return text if text else None

    def _extract_canvas_sections(self, canvas_layout: dict) -> list[str]:
        """Extract text from all canvas layout sections."""
        text_parts = []
        html_sections = canvas_layout.get("horizontalSections", [])

        for section in html_sections:
            for column in section.get("columns", []):
                for webpart in column.get("webparts", []):
                    text = self._extract_webpart_text(webpart)
                    if text:
                        text_parts.append(text)

        return text_parts

    def _extract_page_content(self, page: dict) -> str:
        """
        Extract text content from SharePoint page.

        Args:
            page: Page data from Graph API

        Returns:
            Extracted text content
        """
        text_parts = []

        # Extract title
        title = page.get("title", "")
        if title:
            text_parts.append(f"# {title}")

        # Extract canvas layout content
        canvas_layout = page.get("canvasLayout", {})
        if canvas_layout:
            text_parts.extend(self._extract_canvas_sections(canvas_layout))

        # Fallback to description if no content found
        if not text_parts or len(text_parts) == 1:  # Only title
            description = page.get("description", "")
            if description:
                text_parts.append(description)

        content = "\n\n".join(text_parts)
        if not content:
            logger.debug(
                f"No content extracted from page. "
                f"Title: {page.get('title', '')}, "
                f"Has canvasLayout: {bool(canvas_layout)}"
            )
        return content

    def _get_all_drives(self) -> list[dict]:
        """
        Get all document libraries (drives) for the SharePoint site.

        Returns:
            List of drive dictionaries with 'id' and 'name'
        """
        site_id = self._get_site_id()
        drives_url = f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}/sites/{site_id}/drives"

        drives = []
        url = drives_url
        while url:
            data = self._make_graph_request(url)
            if not data:
                break

            for drive in data.get("value", []):
                drives.append(
                    {"id": drive.get("id"), "name": drive.get("name", "Unknown"), "web_url": drive.get("webUrl", "")}
                )

            url = data.get(self.ODATA_NEXT_LINK)

        if drives:
            logger.info(f"Found {len(drives)} document libraries: {[d['name'] for d in drives]}")
        else:
            logger.warning("No document libraries found in SharePoint site")

        return drives

    def _build_folder_url(self, site_id: str, drive_id: str, folder_path: str) -> str:
        """Build URL for listing folder contents."""
        base_url = (
            f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}"
            f"/sites/{site_id}/drives/{drive_id}"
        )

        if folder_path == "root":
            return f"{base_url}/root/children"
        return f"{base_url}/items/{folder_path}/children"

    def _should_skip_file(self, item: dict) -> tuple[bool, str | None]:
        """
        Check if file should be skipped.

        Returns:
            (should_skip, skip_reason)
        """
        file_size = item.get("size", 0)
        file_name = item.get("name", "")
        file_ext = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""

        # Check file size
        if file_size > self.max_file_size_bytes:
            logger.warning(f"Skipping large file: {file_name} ({file_size} bytes > {self.max_file_size_bytes} bytes)")
            return True, "size"

        # Check file extension
        if file_ext in self.SKIP_EXTENSIONS:
            logger.debug(f"Skipping media/executable file: {file_name} (ext: {file_ext})")
            return True, "extension"

        # Apply user-defined files filter (gitignore-style patterns)
        if self.files_filter.strip():
            include_spec, exclude_spec, has_include_patterns = _create_pathspec_from_filter(self.files_filter)
            if exclude_spec.match_file(file_name):
                logger.debug(f"Skipping file excluded by files_filter: {file_name}")
                return True, "files_filter"
            if has_include_patterns and not include_spec.match_file(file_name):
                logger.debug(f"Skipping file not matching files_filter include patterns: {file_name}")
                return True, "files_filter"

        # Check path filter
        if self.path_filter and self.path_filter != "*":
            item_url = item.get("webUrl", "")
            if not self._matches_path_filter(item_url):
                logger.debug(f"Skipping file not matching path filter: {file_name}")
                return True, "path_filter"

        return False, None

    def _process_file_item(self, site_id: str, drive_id: str, item: dict) -> Iterator[dict]:
        """Process a single file item and yield document dictionaries."""
        self._total_files_found += 1

        should_skip, _ = self._should_skip_file(item)
        if should_skip:
            self._total_files_skipped += 1
            return

        file_name = item.get("name", "")
        file_ext = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""

        logger.debug(f"Processing file: {file_name}")
        documents = self._download_and_extract_file(site_id, drive_id, item["id"], file_ext, file_name)

        if documents:
            self._total_files_processed += 1
            for doc in documents:
                yield {
                    "type": "document",
                    "id": item.get("id"),
                    "title": file_name,
                    "content": doc.page_content,
                    "url": item.get("webUrl", ""),
                    "created": item.get("createdDateTime"),
                    "modified": item.get("lastModifiedDateTime"),
                    "file_type": file_ext,
                    "metadata": doc.metadata,
                }
        else:
            logger.warning(f"Failed to extract content from file: {file_name}")
            self._total_files_skipped += 1

    def _load_documents_recursive(self, drive_id: str, folder_path: str = "root") -> Iterator[dict]:
        """
        Recursively load documents from SharePoint document library.

        Args:
            drive_id: Drive ID (required)
            folder_path: Folder path or "root"

        Yields:
            Dictionary containing document data
        """
        site_id = self._get_site_id()
        url = self._build_folder_url(site_id, drive_id, folder_path)

        while url:
            data = self._make_graph_request(url)
            if not data:
                break

            for item in data.get("value", []):
                if "folder" in item:
                    # Recursively process folder
                    yield from self._load_documents_recursive(drive_id, item["id"])
                elif "file" in item:
                    yield from self._process_file_item(site_id, drive_id, item)

            url = data.get(self.ODATA_NEXT_LINK)

    def _download_and_extract_file(
        self, site_id: str, drive_id: str, item_id: str, file_ext: str, file_name: str
    ) -> list[Document]:
        """
        Download file from SharePoint and extract documents using appropriate loader.

        Args:
            site_id: Site ID
            drive_id: Drive ID
            item_id: Item ID
            file_ext: File extension (with dot)
            file_name: Original file name

        Returns:
            List of LangChain Document objects or empty list if extraction fails
        """
        # Download file content
        download_url = (
            f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}/"
            f"sites/{site_id}/drives/{drive_id}/items/{item_id}/content"
        )

        try:
            response = requests.get(download_url, headers=self._get_headers(), timeout=SHAREPOINT_CONFIG.loader_timeout)
            response.raise_for_status()
            file_bytes = response.content

            # Extract documents using file loaders
            return self._extract_documents_from_bytes(file_bytes, file_ext, file_name)

        except Exception as e:
            logger.error(f"Failed to download/extract file {file_name}: {e}")
            return []

    def _extract_documents_from_bytes(self, file_bytes: bytes, file_ext: str, file_name: str) -> list[Document]:
        """
        Extract documents from file bytes using appropriate loader.

        This method reuses the same loaders as FilesDatasourceLoader for consistency.

        Args:
            file_bytes: File content as bytes
            file_ext: File extension (with dot, e.g., ".pdf")
            file_name: Original file name

        Returns:
            List of LangChain Document objects
        """
        documents = []

        # Remove dot from extension for lookup
        file_ext_no_dot = file_ext.lstrip(".")

        # Get loader class
        loader_class = self._LOADERS.get(file_ext_no_dot)
        if not loader_class:
            # Default to PlainTextLoader for unsupported types
            loader_class = PlainTextLoader

        # Save to temp file and process
        with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as temp_file:
            temp_file.write(file_bytes)
            temp_file.flush()
            temp_file_path = temp_file.name

        try:
            loader_kwargs = self._loader_kwargs.get(file_ext_no_dot, {})

            # Configure image processing for PDF files if multimodal LLM is available
            if file_ext_no_dot == IndexKnowledgeBaseFileTypes.PDF.value:
                from codemie.service.llm_service.llm_service import llm_service

                multimodal_llms = llm_service.get_multimodal_llms()
                if multimodal_llms:
                    # Use the first available multimodal LLM for image processing
                    llm = get_llm_by_credentials(
                        llm_model=multimodal_llms[0],
                        streaming=False,
                        request_id=self.request_uuid if self.request_uuid else str(uuid.uuid4()),
                    )
                    from langchain_community.document_loaders.parsers import LLMImageBlobParser

                    images_parser: BaseImageBlobParser = LLMImageBlobParser(model=llm)
                else:
                    from langchain_community.document_loaders.parsers import TesseractBlobParser

                    images_parser = TesseractBlobParser()

                loader_kwargs["images_parser"] = images_parser

            loader = loader_class(temp_file_path, **loader_kwargs)

            try:
                for document in loader.lazy_load():
                    document.metadata["source"] = file_name
                    # Remove temp file_path from metadata
                    if "file_path" in document.metadata:
                        document.metadata["file_path"] = file_name
                    documents.append(document)
            except UnicodeDecodeError as e:
                logger.warning(
                    f"Failed to load file due to encoding error: {file_name}. "
                    f"File cannot be decoded with default encoding: {e}",
                    exc_info=True,
                )
            except ValueError:
                logger.warning(f"Unsupported file type: {file_ext} for file {file_name}", exc_info=True)

        finally:
            # Clean up temp file
            import os

            try:
                os.unlink(temp_file_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp file {temp_file_path}: {e}")

        return documents

    # System list names to filter out
    SYSTEM_LIST_NAMES = {
        "Web Template Extensions",
        "Master Page Gallery",
        "Theme Gallery",
        "Style Library",
        "Site Assets",
        "Site Pages",
        "Events",
    }

    def _should_skip_list(self, list_info: dict) -> tuple[bool, str | None]:
        """
        Check if list should be skipped.

        Returns:
            (should_skip, skip_reason)
        """
        list_name = list_info.get("displayName", "")
        list_metadata = list_info.get("list", {})
        is_hidden = list_info.get("hidden", False)
        template = list_metadata.get("template", "")

        # Filter out document libraries
        if template == "documentLibrary":
            return True, f"document library: {list_name}"

        # Filter out hidden/system lists
        if is_hidden:
            return True, f"hidden list: {list_name}"

        # Filter out catalog/system lists by name
        is_catalog_list = list_name.startswith("_") or "_catalogs" in list_name.lower()
        is_form_templates = list_name.startswith(self.FORM_TEMPLATES_FOLDER)
        if is_catalog_list or is_form_templates:
            return True, f"system list: {list_name}"

        # Filter out common system lists by display name
        if list_name in self.SYSTEM_LIST_NAMES or list_name == self.FORM_TEMPLATES_FOLDER:
            return True, f"system list: {list_name}"

        return False, None

    def _build_list_item_content(self, list_name: str, fields: dict) -> str:
        """Build content string from list item fields."""
        content_parts = [f"List: {list_name}"]
        for key, value in fields.items():
            if value and not key.startswith("@"):
                content_parts.append(f"{key}: {value}")
        return "\n".join(content_parts)

    def _process_list_items(self, site_id: str, list_id: str, list_name: str) -> Iterator[dict]:
        """Process all items in a SharePoint list."""
        items_url = (
            f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}/"
            f"sites/{site_id}/lists/{list_id}/items?expand=fields"
        )

        while items_url:
            items_data = self._make_graph_request(items_url)
            if not items_data:
                break

            for item in items_data.get("value", []):
                fields = item.get("fields", {})
                if fields:
                    content = self._build_list_item_content(list_name, fields)
                    yield {
                        "type": "list_item",
                        "id": item.get("id"),
                        "title": f"{list_name} - Item {item.get('id')}",
                        "content": content,
                        "url": item.get("webUrl", ""),
                        "created": item.get("createdDateTime"),
                        "modified": item.get("lastModifiedDateTime"),
                    }

            items_url = items_data.get(self.ODATA_NEXT_LINK)

    def _load_lists(self) -> Iterator[dict]:
        """
        Load list items from SharePoint.

        Filters out:
        - Document libraries (template type 101)
        - System/hidden lists (hidden=true)
        - Catalog lists (displayName starts with underscore or contains _catalogs)

        Yields:
            Dictionary containing list item data
        """
        site_id = self._get_site_id()
        lists_url = f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}/sites/{site_id}/lists"

        lists_data = self._make_graph_request(lists_url)
        if not lists_data:
            return

        for list_info in lists_data.get("value", []):
            should_skip, skip_reason = self._should_skip_list(list_info)
            if should_skip:
                logger.info(f"Skipping {skip_reason}")
                continue

            list_id = list_info.get("id")
            list_name = list_info.get("displayName", "")
            logger.info(f"Loading user-created list: {list_name}")

            yield from self._process_list_items(site_id, list_id, list_name)

    def _matches_path_filter(self, url: str) -> bool:
        """
        Check if URL matches the path filter.

        Args:
            url: URL to check

        Returns:
            True if matches, False otherwise
        """
        if self.path_filter == "*":
            return True

        # Simple wildcard matching
        filter_pattern = self.path_filter.replace("*", ".*")
        return bool(re.search(filter_pattern, url, re.IGNORECASE))

    def _transform_to_doc(self, item: dict) -> Document:
        """
        Transform SharePoint item to LangChain Document.

        Args:
            item: SharePoint item dictionary

        Returns:
            LangChain Document
        """
        # Start with metadata from file loader if available
        metadata = item.get("metadata", {}).copy() if "metadata" in item else {}

        # Add SharePoint-specific metadata
        metadata.update(
            {
                "source": item.get("url", metadata.get("source", "")),
                "title": item.get("title", ""),
                "type": item.get("type", ""),
                "id": item.get("id", ""),
                "created": item.get("created", ""),
                "modified": item.get("modified", ""),
            }
        )

        return Document(
            page_content=item.get("content", ""),
            metadata=metadata,
        )

    def _load_and_yield_pages(self) -> Iterator[Document]:
        """Load SharePoint pages and yield as documents."""
        pages_count = 0
        for page in self._load_site_pages():
            if page.get("content"):
                yield self._transform_to_doc(page)
                pages_count += 1
        logger.info(f"Loaded {pages_count} SharePoint pages")

    def _load_and_yield_documents_from_drive(self, drive_id: str, drive_name: str) -> Iterator[Document]:
        """Load documents from a single drive/library."""
        files_before = self._total_files_processed
        for doc in self._load_documents_recursive(drive_id):
            if doc.get("content"):
                yield self._transform_to_doc(doc)

        drive_docs_count = self._total_files_processed - files_before
        logger.info(f"Loaded {drive_docs_count} documents from '{drive_name}' library")

    def _load_and_yield_all_documents(self) -> Iterator[Document]:
        """Load documents from all document libraries."""
        drives = self._get_all_drives()
        if not drives:
            logger.warning("No document libraries found, skipping document loading")
            return

        for drive in drives:
            drive_id = drive["id"]
            drive_name = drive["name"]
            logger.info(f"Loading documents from library: {drive_name}")
            yield from self._load_and_yield_documents_from_drive(drive_id, drive_name)

        logger.info(
            f"Documents summary: Found {self._total_files_found} files, "
            f"Processed {self._total_files_processed}, Skipped {self._total_files_skipped}"
        )

    def _load_and_yield_lists(self) -> Iterator[Document]:
        """Load SharePoint list items and yield as documents."""
        lists_count = 0
        for list_item in self._load_lists():
            if list_item.get("content"):
                yield self._transform_to_doc(list_item)
                lists_count += 1
        logger.info(f"Loaded {lists_count} SharePoint list items")

    def lazy_load(self) -> Iterator[Document]:
        """
        Load documents lazily (streaming).

        Yields:
            LangChain Document objects
        """
        self._validate_creds()

        # Reset stats for this load
        self._total_files_found = 0
        self._total_files_processed = 0
        self._total_files_skipped = 0

        # Load site pages
        if self.include_pages:
            logger.info("Loading SharePoint site pages...")
            try:
                yield from self._load_and_yield_pages()
            except Exception as e:
                logger.error(f"Failed to load site pages: {e}", exc_info=True)

        # Load documents from all document libraries
        if self.include_documents:
            logger.info("Loading SharePoint documents from all libraries...")
            try:
                yield from self._load_and_yield_all_documents()
            except Exception as e:
                logger.error(f"Failed to load documents: {e}", exc_info=True)

        # Load lists
        if self.include_lists:
            logger.info("Loading SharePoint lists...")
            try:
                yield from self._load_and_yield_lists()
            except Exception as e:
                logger.error(f"Failed to load lists: {e}", exc_info=True)

        logger.info("SharePoint loader completed successfully")

    def _would_skip_file_for_count(self, item: dict) -> bool:
        """Check if file would be skipped during processing."""
        file_size = item.get("size", 0)
        file_name = item.get("name", "")
        file_ext = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""

        return file_size > self.max_file_size_bytes or file_ext in self.SKIP_EXTENSIONS

    def _count_files_recursive(self, site_id: str, drive_id: str, folder_path: str = "root") -> tuple[int, int]:
        """
        Recursively count all files in a drive, including skipped ones.

        Args:
            site_id: Site ID
            drive_id: Drive ID
            folder_path: Folder path or "root"

        Returns:
            Tuple of (total_files, skipped_files)
        """
        total_files = 0
        skipped_files = 0
        url = self._build_folder_url(site_id, drive_id, folder_path)

        while url:
            data = self._make_graph_request(url)
            if not data:
                break

            for item in data.get("value", []):
                if "folder" in item:
                    # Recursively count folder
                    folder_total, folder_skipped = self._count_files_recursive(site_id, drive_id, item["id"])
                    total_files += folder_total
                    skipped_files += folder_skipped
                elif "file" in item:
                    total_files += 1
                    if self._would_skip_file_for_count(item):
                        skipped_files += 1

            url = data.get(self.ODATA_NEXT_LINK)

        return total_files, skipped_files

    def _count_pages(self, site_id: str) -> int:
        """Count SharePoint pages."""
        pages_count = 0
        url = f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}/sites/{site_id}/pages"
        while url:
            data = self._make_graph_request(url)
            if not data:
                break
            pages_count += len(data.get("value", []))
            url = data.get(self.ODATA_NEXT_LINK)
        return pages_count

    def _count_documents(self, site_id: str) -> tuple[int, int]:
        """Count documents from all libraries recursively."""
        total_count = 0
        skipped_count = 0
        drives = self._get_all_drives()
        for drive in drives:
            drive_id = drive["id"]
            drive_total, drive_skipped = self._count_files_recursive(site_id, drive_id)
            total_count += drive_total
            skipped_count += drive_skipped
            logger.info(
                f"Library '{drive['name']}': {drive_total} files "
                f"({drive_total - drive_skipped} processable, {drive_skipped} skipped)"
            )
        return total_count, skipped_count

    def _should_skip_list_for_count(self, list_info: dict[str, Any]) -> bool:
        """Check if a SharePoint list should be skipped during counting."""
        list_name = list_info.get("displayName", "Unknown")
        list_metadata = list_info.get("list", {})
        is_hidden = list_info.get("hidden", False)
        template = list_metadata.get("template", "")

        # Filter out document libraries
        if template == "documentLibrary":
            logger.info(f"Skipping document library during count: {list_name}")
            return True

        # Filter out hidden/system lists
        if is_hidden:
            logger.info(f"Skipping hidden list during count: {list_name}")
            return True

        # Filter out catalog/system lists by name
        is_catalog_list = list_name.startswith("_") or "_catalogs" in list_name.lower()
        is_form_templates = list_name.startswith(self.FORM_TEMPLATES_FOLDER)
        if is_catalog_list or is_form_templates:
            logger.info(f"Skipping system list during count: {list_name}")
            return True

        # Filter out common system lists by display name
        system_list_names = {
            "Web Template Extensions",
            "Master Page Gallery",
            "Theme Gallery",
            "Style Library",
            "Site Assets",
            "Site Pages",
            self.FORM_TEMPLATES_FOLDER,
            "Events",
        }
        if list_name in system_list_names:
            logger.info(f"Skipping system list during count: {list_name}")
            return True

        return False

    def _count_list_items(self, site_id: str, list_id: str) -> int:
        """Count items in a specific SharePoint list."""
        list_items_count = 0
        items_url = (
            f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}/"
            f"sites/{site_id}/lists/{list_id}/items"
        )

        while items_url:
            items_data = self._make_graph_request(items_url)
            if not items_data:
                break
            list_items_count += len(items_data.get("value", []))
            items_url = items_data.get(self.ODATA_NEXT_LINK)

        return list_items_count

    def _count_lists(self, site_id: str) -> int:
        """Count list items across user-created lists."""
        lists_count = 0
        lists_url = f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}/sites/{site_id}/lists"

        lists_data = self._make_graph_request(lists_url)
        if lists_data:
            for list_info in lists_data.get("value", []):
                if self._should_skip_list_for_count(list_info):
                    continue

                list_id = list_info.get("id")
                list_name = list_info.get("displayName", "Unknown")
                list_items_count = self._count_list_items(site_id, list_id)
                lists_count += list_items_count
                logger.info(f"User-created list '{list_name}': {list_items_count} items")

        return lists_count

    def fetch_remote_stats(self) -> dict[str, Any]:
        """
        Count all documents for health check.

        Returns:
            Dictionary with document counts
        """
        self._validate_creds()

        total_count = 0
        skipped_count = 0

        # Count pages
        if self.include_pages:
            try:
                site_id = self._get_site_id()
                pages_count = self._count_pages(site_id)
                total_count += pages_count
                logger.info(f"Found {pages_count} SharePoint pages")
            except Exception as e:
                logger.error(f"Failed to count pages: {e}")

        # Count documents from all libraries recursively
        if self.include_documents:
            try:
                site_id = self._get_site_id()
                docs_total, docs_skipped = self._count_documents(site_id)
                total_count += docs_total
                skipped_count += docs_skipped
            except Exception as e:
                logger.error(f"Failed to count documents: {e}")

        # Count list items
        if self.include_lists:
            try:
                site_id = self._get_site_id()
                lists_count = self._count_lists(site_id)
                total_count += lists_count
                logger.info(f"Found {lists_count} total list items across user-created lists only")
            except Exception as e:
                logger.error(f"Failed to count lists: {e}")

        processable_count = total_count - skipped_count
        logger.info(
            f"SharePoint stats: {total_count} total items, "
            f"{processable_count} will be processed, {skipped_count} will be skipped"
        )

        return {
            self.DOCUMENTS_COUNT_KEY: processable_count,
            self.TOTAL_DOCUMENTS_KEY: total_count,
            self.SKIPPED_DOCUMENTS_KEY: skipped_count,
        }
