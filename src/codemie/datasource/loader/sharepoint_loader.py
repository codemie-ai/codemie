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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import quote, unquote, urlparse

import requests
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document

from codemie.configs import logger
from codemie.core.utils import _create_pathspec_from_filter
from codemie.datasource.datasources_config import SHAREPOINT_CONFIG
from codemie.datasource.exceptions import (
    MissingIntegrationException,
    UnauthorizedException,
)
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.datasource.loader.file_extraction_utils import extract_documents_from_bytes

_MAX_RETRY_AFTER_SECONDS = 60


def _encode_url_path(path: str) -> str:
    """Percent-encode each segment of a folder path for use in a Graph API URL."""
    return "/".join(quote(seg, safe="") for seg in path.strip("/").split("/"))


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
            files_filter="/Shared Documents/*",
            auth_config=SharePointAuthConfig(
                tenant_id="<tenant-id>",
                client_id="<client-id>",
                client_secret="<client-secret>",
            ),
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
        auth_config: SharePointAuthConfig | None = None,
        include_pages: bool = True,
        include_documents: bool = True,
        include_lists: bool = True,
        max_file_size_mb: int = 50,
        files_filter: str = "",
        request_uuid: str | None = None,
        modified_since: datetime | None = None,
    ):
        """
        Initialize SharePoint loader.

        Args:
            site_url: Full SharePoint site URL (e.g., https://tenant.sharepoint.com/sites/sitename)
            auth_config: Authentication configuration (credentials and auth type)
            include_pages: Whether to include site pages
            include_documents: Whether to include documents from libraries
            include_lists: Whether to include list items
            max_file_size_mb: Maximum file size to process (MB)
            files_filter: Multi-line filter. Lines starting with '/' or a SharePoint URL scope
                traversal to that folder; other lines are gitignore-style file name patterns.
            request_uuid: Request UUID for tracking LLM usage
        """
        if auth_config is None:
            auth_config = SharePointAuthConfig()

        self.site_url = site_url
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

        self.modified_since = modified_since

        self._access_token: str | None = None
        self._site_id: str | None = None
        self._site_hostname: str | None = None
        self._site_path: str | None = None
        # Maps drive_id -> site-relative library path (e.g. "Shared Documents")
        # Populated lazily by _get_all_drives() and used in _get_file_relative_path()
        self._drive_library_paths: dict[str, str] = {}

        # Statistics tracking
        self._total_files_found = 0
        self._total_files_processed = 0
        self._total_files_skipped = 0

        # Parse site URL
        self._parse_site_url()

    def _normalize_path_filter(self, line: str) -> str:
        """
        Normalize a files_filter line that contains a SharePoint URL into a site-relative glob.

        Supported input formats
        -----------------------
        1. SharePoint "Copy Link" (folder sharing link)::

               https://tenant/:f:/r/sites/Site/Shared%20Documents/folder?csf=1&web=1&e=…

        2. Direct folder URL (with optional user-appended wildcard)::

               https://tenant/sites/Site/Shared%20Documents/folder
               https://tenant/sites/Site/Shared%20Documents/folder/*
               https://tenant/sites/Site/Shared%20Documents/folder/*.pdf

        Non-URL values (plain paths, "*") are returned unchanged.

        Output examples
        ---------------
        * URL without wildcard  →  ``/Shared Documents/folder/*``
        * URL with ``/*``       →  ``/Shared Documents/folder/*``
        * URL with ``/*.pdf``   →  ``/Shared Documents/folder/*.pdf``
        """
        if not line:
            return line or ""

        stripped = line.strip()
        if not re.match(r"https?://", stripped, re.IGNORECASE):
            return stripped

        parsed = urlparse(stripped)

        # Reject URLs from a different SharePoint tenant/hostname outright
        if parsed.netloc and parsed.netloc.lower() != self._site_hostname.lower():
            logger.warning(
                f"files_filter URL hostname '{parsed.netloc}' doesn't match "
                f"site_url hostname '{self._site_hostname}', using as-is: {line!r}"
            )
            return line

        url_path = unquote(parsed.path)

        # Detect a user-appended wildcard at the very end of the URL path
        # Matches "/*" or "/*.ext" (e.g. "/*.pdf", "/*.docx")
        wildcard_suffix = ""
        wildcard_match = re.search(r"(/\*(?:\.\w+)?)$", url_path)
        if wildcard_match:
            wildcard_suffix = wildcard_match.group(1)
            url_path = url_path[: wildcard_match.start()]

        # Handle SharePoint Copy Link format: /:X:/r/... (X = f, g, b, …)
        # The actual site path follows after /r/ or /s/
        copy_link_match = re.match(r"^/:[a-z]:/[rs](/.+)$", url_path, re.IGNORECASE)
        if copy_link_match:
            url_path = copy_link_match.group(1)

        # Strip the site path prefix to obtain a site-relative path
        site_path = self._site_path.rstrip("/")
        site_path_lower = site_path.lower()
        if url_path.lower().startswith(site_path_lower):
            relative_path = url_path[len(site_path_lower) :]
        else:
            logger.warning(f"files_filter URL doesn't match site_url '{self.site_url}', using as-is: {line!r}")
            return line

        relative_path = relative_path.rstrip("/")
        # When relative_path is empty the URL pointed at the site root — index everything
        suffix = wildcard_suffix or "/*"
        normalized = suffix if not relative_path else relative_path + suffix
        logger.info(f"Normalized files_filter URL → {normalized!r}")
        return normalized

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

    def _process_page(self, site_id: str, page: dict) -> dict | None:
        """Fetch details, extract content, and apply modified_since for a single page.

        Returns a page dict to yield, or None if the page should be skipped.
        """
        page_data = self._fetch_page_details(site_id, page.get("id"), page)
        content = self._extract_page_content(page_data)
        if not content:
            logger.warning(f"Page has no content: {page_data.get('title', '')} ({page_data.get('webUrl', '')})")
            return None
        if self._is_not_modified_since(page_data.get("lastModifiedDateTime")):
            logger.debug(f"Skipping unmodified page: {page_data.get('title', '')}")
            return None
        return self._create_page_dict(page_data, content)

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
                page_dict = self._process_page(site_id, page)
                if page_dict:
                    pages_with_content += 1
                    yield page_dict

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
        # Computed once; self.site_url is immutable for the lifetime of this loader.
        site_prefix = urlparse(self.site_url).path.rstrip("/")
        url = drives_url
        while url:
            data = self._make_graph_request(url)
            if not data:
                break

            for drive in data.get("value", []):
                drive_id = drive.get("id")
                drive_web_url = drive.get("webUrl", "")
                drives.append({"id": drive_id, "name": drive.get("name", "Unknown"), "web_url": drive_web_url})
                # Cache the site-relative library path extracted from the drive's webUrl.
                # drive.webUrl looks like https://tenant/sites/MySite/Shared Documents
                # We need "Shared Documents" (the URL segment, not the display name).
                if drive_id and drive_web_url:
                    drive_path = urlparse(unquote(drive_web_url)).path
                    if drive_path.lower().startswith(site_prefix.lower() + "/"):
                        self._drive_library_paths[drive_id] = drive_path[len(site_prefix) + 1 :]

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

    def _extract_drive_folder(self, item: dict) -> tuple[str, str]:
        """
        Parse parentReference from a Graph API driveItem into (drive_id, folder_in_drive).

        parentReference.path has the form:
            "/drives/{driveId}/root:/folder/subfolder"  → folder_in_drive = "folder/subfolder"
            "/drives/{driveId}/root:"                   → folder_in_drive = "" (library root)

        Returns:
            Tuple of (drive_id, folder_in_drive) — both empty strings on missing data.
        """
        parent_ref = item.get("parentReference", {})
        drive_id = parent_ref.get("driveId", "")
        parent_path = unquote(parent_ref.get("path", ""))
        folder_in_drive = parent_path.split(":", 1)[1].strip("/") if ":" in parent_path else ""
        return drive_id, folder_in_drive

    def _get_file_relative_path(self, item: dict) -> str:
        """
        Build the site-relative path of a file for filter matching.

        Combines the library URL-path (from the drive's webUrl, cached in
        _drive_library_paths) with the folder path from parentReference.path,
        yielding a path like:
            "Shared Documents/test folder/another folder/file.xlsx"

        This is reliable for all file types, including Office files whose
        webUrl points to the Online viewer (/_layouts/15/Doc.aspx) rather
        than the actual file path.

        Falls back to just the filename when drive mapping is unavailable.
        """
        file_name = item.get("name", "")
        drive_id, folder_in_drive = self._extract_drive_folder(item)

        library_path = self._drive_library_paths.get(drive_id, "")
        if not library_path:
            logger.debug(
                f"Drive {drive_id!r} not in library path cache; "
                f"files_filter path matching falling back to filename only for {file_name!r}"
            )
            return file_name

        if folder_in_drive:
            return f"{library_path}/{folder_in_drive}/{file_name}"
        return f"{library_path}/{file_name}"

    def _get_file_library_relative_path(self, item: dict) -> str:
        """
        Build the path of a file relative to its document library root.

        Unlike _get_file_relative_path this omits the library name itself, e.g.:
            "CodeMie Dev team/testing/file.xlsx"
        instead of:
            "Shared Documents/CodeMie Dev team/testing/file.xlsx"

        Used together with _get_file_relative_path so that files_filter patterns
        work regardless of whether the user includes the library name or not.
        """
        file_name = item.get("name", "")
        _, folder_in_drive = self._extract_drive_folder(item)
        if folder_in_drive:
            return f"{folder_in_drive}/{file_name}"
        return file_name

    @staticmethod
    def _parse_folder_scope(path: str) -> tuple[str, str] | None:
        """
        Parse a normalised site-relative path into (library_name, sub_path).

        Returns None when path is a wildcard or empty (traverse all libraries from root).

        Examples:
            "/Shared Documents/Team/Project/*" → ("Shared Documents", "Team/Project")
            "/Shared Documents/*"              → ("Shared Documents", "")
            "*"                                → None
        """
        if not path or path.strip() in ("*", ""):
            return None

        # Decode percent-encoded characters, strip surrounding slashes/spaces
        p = unquote(path.strip()).strip("/")
        segments = p.split("/")

        library_name = segments[0].strip()
        if not library_name or library_name == "*":
            return None

        # If any intermediate (non-final) segment is a wildcard the path cannot be
        # resolved to a single folder — fall back to the library root.
        segments_after_library = segments[1:]
        intermediate = segments_after_library[:-1] if segments_after_library else []
        if any(re.match(r"^\*(?:\.\w+)?$", s.strip()) for s in intermediate if s.strip()):
            return library_name, ""

        # Strip wildcard-only final segments ("*", "*.pdf", etc.)
        sub_segments = [
            s.strip() for s in segments_after_library if s.strip() and not re.match(r"^\*(?:\.\w+)?$", s.strip())
        ]
        sub_path = "/".join(sub_segments)

        return library_name, sub_path

    @staticmethod
    def _has_global_patterns(files_filter: str) -> bool:
        """Return True when files_filter contains any non-path (global) include pattern.

        Lines starting with '/', 'http', '!', or '#' are not global patterns.
        A global pattern (e.g. '*.xlsx') causes full-site traversal.
        """
        for line in files_filter.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            if line.startswith("/") or line.lower().startswith("http"):
                continue  # folder scope line — not a global pattern
            return True
        return False

    def _parse_folder_rules(self, files_filter: str) -> list[tuple[str, str, str]]:
        """Parse folder-scoped lines from files_filter into (library_name, sub_path, type_wildcard) tuples.

        Each line is normalised (URL → site-relative path) then parsed with
        _parse_folder_scope. Lines that are global patterns, comments, or negations
        are skipped. Duplicate scopes are deduplicated (case-insensitive library name).

        type_wildcard is the file-extension pattern embedded in the scope line, e.g. "*.json"
        for "/Shared Documents/folder/*.json". Empty string means no per-scope restriction.
        """
        rules: list[tuple[str, str, str]] = []
        for raw in (files_filter or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            if not line.startswith("/") and not line.lower().startswith("http"):
                continue  # global pattern — not a folder scope
            normalised = self._normalize_path_filter(line)
            if not normalised or normalised.strip() in ("*", ""):
                continue
            # If normalization could not strip the URL (e.g. hostname mismatch), skip it
            if re.match(r"https?://", normalised.strip(), re.IGNORECASE):
                continue
            # Extract file-type wildcard from normalised path before _parse_folder_scope strips it
            # e.g. "/Shared Documents/Team/*.json" → type_wildcard = "*.json"
            type_wildcard = ""
            wc_match = re.search(r"/(\*\.\w+)$", normalised)
            if wc_match:
                type_wildcard = wc_match.group(1)
            scope = self._parse_folder_scope(normalised)
            if scope and (scope[0].lower(), scope[1]) not in {(r[0].lower(), r[1]) for r in rules}:
                rules.append((*scope, type_wildcard))
        return rules

    @staticmethod
    def _global_patterns_from_filter(files_filter: str) -> str:
        """Extract non-scope lines (global gitignore patterns) from files_filter.

        Returns a newline-joined string of lines that are NOT folder-scope lines
        (i.e. not starting with '/', 'http', '#', or '!').  '!' negation lines are
        included because they are valid pathspec patterns.
        """
        parts = []
        for line in files_filter.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("/") or stripped.lower().startswith("http"):
                continue  # folder-scope line — not a global pattern
            parts.append(stripped)
        return "\n".join(parts)

    def _resolve_all_scopes(self, drives: list[dict]) -> list[tuple[list[dict], str, str]]:
        """Return list of (target_drives, sub_path, scope_file_filter) triples for document traversal.

        scope_file_filter is the per-scope effective files_filter string to use during
        that scope's traversal — it contains global patterns plus any typed wildcard
        embedded in the scope URL (e.g. "*.json" from "/folder/*.json").

        - Empty files_filter or any global pattern → [(all_drives, "", original_ff)]
        - Only folder-scoped lines → one entry per unique (library, sub_path) pair
        """
        ff = self.files_filter or ""
        if not ff.strip() or self._has_global_patterns(ff):
            return [(drives, "", ff)]
        folder_rules = self._parse_folder_rules(ff)
        if not folder_rules:
            return [(drives, "", ff)]
        global_patterns = self._global_patterns_from_filter(ff)
        scopes: list[tuple[list[dict], str, str]] = []
        seen: set[tuple[str, str]] = set()
        full_fallback_added = False
        for library_name, sub_path, type_wildcard in folder_rules:
            key = (library_name.lower(), sub_path)
            if key in seen:
                continue
            seen.add(key)
            # Build per-scope file filter: global patterns + this scope's type restriction
            scope_parts = [p for p in [global_patterns, type_wildcard] if p]
            scope_filter = "\n".join(scope_parts)
            matched = [
                d
                for d in drives
                if self._drive_library_paths.get(d["id"], "").lower() == library_name.lower()
                or d.get("name", "").lower() == library_name.lower()
            ]
            if not matched:
                logger.warning(
                    f"No library found matching '{library_name}' from files_filter, falling back to all libraries"
                )
                # sub_path has no meaning when the library is unknown — use root to avoid
                # spurious _resolve_folder_start calls across every drive.
                # Guard against duplicate (drives, "") when multiple unknown names fall back.
                if not full_fallback_added:
                    scopes.append((drives, "", global_patterns))
                    full_fallback_added = True
            else:
                logger.info(f"files_filter scoped to {len(matched)} library(s) matching '{library_name}'")
                scopes.append((matched, sub_path, scope_filter))
        return scopes

    def _resolve_folder_start(self, drive_id: str, sub_path: str, site_id: str | None = None) -> str:
        """
        Resolve a decoded folder sub-path within a drive to its Graph API item ID.

        Uses the /root:/{encoded-path} endpoint.  Returns "root" when sub_path is
        empty or when the folder cannot be resolved (falling back to the library root).

        Args:
            drive_id: The Graph API drive ID.
            sub_path: Decoded path relative to the library root, e.g.
                      "BC SCM Practice/01. Offerings (Offerings, PoV)".
            site_id: Optional pre-resolved site ID; obtained via _get_site_id() if omitted.

        Returns:
            The Graph API item ID of the target folder, or "root".
        """
        if not sub_path:
            return "root"

        if site_id is None:
            site_id = self._get_site_id()
        encoded_path = _encode_url_path(sub_path)
        url = (
            f"{SHAREPOINT_CONFIG.graph_base_url}/{SHAREPOINT_CONFIG.graph_api_version}"
            f"/sites/{site_id}/drives/{drive_id}/root:/{encoded_path}"
        )
        data = self._make_graph_request(url)
        if data and data.get("id"):
            folder_id = data["id"]
            logger.info(f"Resolved folder scope '{sub_path}' → item ID {folder_id}")
            return folder_id

        logger.warning(f"Could not resolve folder path '{sub_path}' in drive {drive_id}, starting from library root")
        return "root"

    def _should_skip_by_files_filter(self, item: dict, file_name: str) -> bool:
        """Return True if the file matches an exclude pattern or misses a required include pattern.

        During scoped traversal self.files_filter is temporarily set to the per-scope filter
        (global patterns + this scope's type wildcard) by _load_and_yield_all_documents.
        Folder-scope lines are stripped here so pathspec never sees them as include patterns.
        """
        if not self.files_filter.strip():
            return False
        # Strip folder-scope lines — they control traversal, not per-file filtering.
        file_only_filter = "\n".join(
            line
            for line in self.files_filter.splitlines()
            if not line.strip().startswith("/") and not line.strip().lower().startswith("http")
        )
        if not file_only_filter.strip():
            return False
        include_spec, exclude_spec, has_include_patterns = _create_pathspec_from_filter(file_only_filter)
        # Build two candidate paths so users can write the filter with or without the library name prefix.
        full_path = self._get_file_relative_path(item)
        in_lib_path = self._get_file_library_relative_path(item)
        logger.debug(
            f"files_filter match: full_path={full_path!r} in_lib_path={in_lib_path!r} filter={self.files_filter!r}"
        )
        if exclude_spec.match_file(full_path) or exclude_spec.match_file(in_lib_path):
            logger.debug(f"Skipping file excluded by files_filter: {file_name}")
            return True
        if has_include_patterns and not (include_spec.match_file(full_path) or include_spec.match_file(in_lib_path)):
            logger.debug(f"Skipping file not matching files_filter include patterns: {file_name}")
            return True
        return False

    def _should_skip_file(self, item: dict) -> tuple[bool, str | None]:
        """
        Check if file should be skipped.

        Returns:
            (should_skip, skip_reason)
        """
        if self._is_not_modified_since(item.get("lastModifiedDateTime")):
            return True, "not_modified"

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
        if self._should_skip_by_files_filter(item, file_name):
            return True, "files_filter"

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
        documents = self._download_and_extract_file(site_id, drive_id, item["id"], file_name)

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

    def _download_and_extract_file(self, site_id: str, drive_id: str, item_id: str, file_name: str) -> list[Document]:
        """
        Download file from SharePoint and extract documents using appropriate loader.

        Args:
            site_id: Site ID
            drive_id: Drive ID
            item_id: Item ID
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
            return self._extract_documents_from_bytes(file_bytes, file_name)

        except Exception as e:
            logger.error(f"Failed to download/extract file {file_name}: {e}")
            return []

    def _extract_documents_from_bytes(self, file_bytes: bytes, file_name: str) -> list[Document]:
        """
        Extract documents from file bytes using appropriate loader.

        Args:
            file_bytes: File content as bytes
            file_name: Original file name

        Returns:
            List of LangChain Document objects
        """
        return extract_documents_from_bytes(
            file_bytes=file_bytes,
            file_name=file_name,
            request_uuid=self.request_uuid,
            csv_separator=",",
        )

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
                if self._is_not_modified_since(item.get("lastModifiedDateTime")):
                    logger.debug(f"Skipping unmodified list item: {item.get('id')}")
                    continue
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

    def _is_not_modified_since(self, modified_str: str | None) -> bool:
        """Return True when the item has NOT changed after self.modified_since.

        An item without a modification timestamp is always considered changed
        (safe default: include it).
        """
        if self.modified_since is None or not modified_str:
            return False
        try:
            item_dt = datetime.fromisoformat(modified_str.rstrip("Z")).replace(tzinfo=timezone.utc)
            cutoff = (
                self.modified_since if self.modified_since.tzinfo else self.modified_since.replace(tzinfo=timezone.utc)
            )
            return item_dt <= cutoff
        except (ValueError, TypeError):
            return False

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

    def _load_and_yield_documents_from_drive(
        self, drive_id: str, drive_name: str, start_folder: str = "root"
    ) -> Iterator[Document]:
        """Load documents from a single drive/library, starting at start_folder."""
        files_before = self._total_files_processed
        for doc in self._load_documents_recursive(drive_id, start_folder):
            if doc.get("content"):
                yield self._transform_to_doc(doc)

        drive_docs_count = self._total_files_processed - files_before
        logger.info(f"Loaded {drive_docs_count} documents from '{drive_name}' library")

    def _load_and_yield_all_documents(self) -> Iterator[Document]:
        """Load documents from document libraries, scoped by files_filter when configured."""
        drives = self._get_all_drives()
        if not drives:
            logger.warning("No document libraries found, skipping document loading")
            return

        scopes = self._resolve_all_scopes(drives)
        site_id = self._get_site_id() if any(sub for _, sub, _ in scopes) else None
        seen_drive_sub: set[tuple[str, str]] = set()
        original_files_filter = self.files_filter

        try:
            for target_drives, sub_path, scope_filter in scopes:
                self.files_filter = scope_filter  # apply per-scope file filter during traversal
                for drive in target_drives:
                    key = (drive["id"], sub_path)
                    if key in seen_drive_sub:
                        continue
                    seen_drive_sub.add(key)
                    drive_id = drive["id"]
                    drive_name = drive["name"]
                    start_folder = self._resolve_folder_start(drive_id, sub_path, site_id) if sub_path else "root"
                    logger.info(f"Loading documents from library: {drive_name}")
                    yield from self._load_and_yield_documents_from_drive(drive_id, drive_name, start_folder)
        finally:
            self.files_filter = original_files_filter

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
        """Check if file would be skipped during processing (excluding modified_since)."""
        file_size = item.get("size", 0)
        file_name = item.get("name", "")
        file_ext = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""

        if file_size > self.max_file_size_bytes or file_ext in self.SKIP_EXTENSIONS:
            return True
        return self._should_skip_by_files_filter(item, file_name)

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
        """Count documents from libraries, scoped by files_filter when configured."""
        total_count = 0
        skipped_count = 0
        drives = self._get_all_drives()
        if not drives:
            logger.warning("No document libraries found, skipping document counting")
            return total_count, skipped_count
        scopes = self._resolve_all_scopes(drives)
        seen_drive_sub: set[tuple[str, str]] = set()
        original_files_filter = self.files_filter
        try:
            for target_drives, sub_path, scope_filter in scopes:
                self.files_filter = scope_filter  # apply per-scope filter so _would_skip_file_for_count is accurate
                for drive in target_drives:
                    key = (drive["id"], sub_path)
                    if key in seen_drive_sub:
                        continue
                    seen_drive_sub.add(key)
                    start_folder = self._resolve_folder_start(drive["id"], sub_path, site_id) if sub_path else "root"
                    drive_total, drive_skipped = self._count_files_recursive(site_id, drive["id"], start_folder)
                    total_count += drive_total
                    skipped_count += drive_skipped
                    logger.info(
                        f"Library '{drive['name']}': {drive_total} files "
                        f"({drive_total - drive_skipped} processable, {drive_skipped} skipped)"
                    )
        finally:
            self.files_filter = original_files_filter
        return total_count, skipped_count

    def _should_skip_list_for_count(self, list_info: dict[str, Any]) -> bool:
        """Check if a SharePoint list should be skipped during counting."""
        should_skip, reason = self._should_skip_list(list_info)
        if should_skip:
            logger.info(f"Skipping {reason} during count")
        return should_skip

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

    def validate_connection(self) -> None:
        """
        Validate SharePoint credentials and site accessibility.

        Makes a single lightweight API call (site ID lookup) to verify the connection
        without traversing any files or folders.

        Note: For oauth auth_type, only the presence of a stored access token is
        checked — token expiry is not validated here and will surface at indexing time.

        Raises:
            MissingIntegrationException: If credentials are missing
            UnauthorizedException: If site is inaccessible with current credentials
        """
        self._validate_creds()

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
