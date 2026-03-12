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

from __future__ import annotations

import base64
import mimetypes
import os
import re
from typing import Any, Iterator, Optional

import requests
from azure.devops.connection import Connection
from azure.devops.v7_1.wiki import WikiClient
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from msrest.authentication import BasicAuthentication
from pydantic import AnyHttpUrl

from codemie.configs import logger
from codemie.datasource.exceptions import (
    MissingIntegrationException,
    UnauthorizedException,
)
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader


class AzureDevOpsWikiLoader(BaseLoader, BaseDatasourceLoader):
    """
    A Langchain loader for Azure DevOps Wiki using the official Azure DevOps SDK.

    Example:
    loader = AzureDevOpsWikiLoader(
        base_url="https://dev.azure.com/organization",
        wiki_query="/path/*",
        access_token="<personal_access_token>",
        organization="organization",
        project="project",
    )
    """

    DOCUMENTS_COUNT_KEY = "documents_count_key"

    API_VERSION = "7.1"
    WIKI_ATTACHMENT_PATTERN = r"!?\[.*?\]\((/?\.attachments/[^)]+)\)"

    # Supported MIME type categories
    IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/jpg", "image/bmp", "image/webp"})
    PDF_MIME_TYPES = frozenset({"application/pdf"})
    DOCX_MIME_TYPES = frozenset(
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        }
    )
    XLSX_MIME_TYPES = frozenset(
        {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
        }
    )
    PPTX_MIME_TYPES = frozenset(
        {
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.ms-powerpoint",
        }
    )
    MSG_MIME_TYPES = frozenset({"application/vnd.ms-outlook"})

    def __init__(
        self,
        base_url: AnyHttpUrl,
        wiki_query: str,
        access_token: str,
        organization: str,
        project: str,
        wiki_identifier: str = None,
        chat_model: Optional[BaseChatModel] = None,
    ):
        # Ensure base_url includes the organization
        base_url_str = str(base_url).rstrip("/")
        if not base_url_str.endswith(organization):
            self.base_url = f"{base_url_str}/{organization}"
        else:
            self.base_url = base_url_str

        self.wiki_query = wiki_query
        self.access_token = access_token
        self.organization = organization
        self.project = project
        self.wiki_identifier = wiki_identifier  # Wiki name to filter by (optional)
        self._chat_model = chat_model
        self._connection = None
        self._wiki_client = None
        self._session: Optional[requests.Session] = None
        self._attachments_index_cache: dict[str, dict[str, Any]] = {}

    def _init_client(self):
        """Initialize Azure DevOps connection, wiki client and HTTP session"""
        credentials = BasicAuthentication("", self.access_token)
        self._connection = Connection(base_url=self.base_url, creds=credentials)
        self._wiki_client: WikiClient = self._connection.clients.get_wiki_client()
        self._init_session()

    def _init_session(self) -> None:
        """Initialize authenticated HTTP session for direct REST API calls"""
        self._session = requests.Session()
        self._session.headers.update(self._create_auth_header())

    def _get_rest_api_base_url(self) -> str:
        return f"{self.base_url}/{self.project}/_apis"

    def _create_auth_header(self) -> dict[str, str]:
        encoded_pat = base64.b64encode(f":{self.access_token}".encode()).decode()
        return {
            "Authorization": f"Basic {encoded_pat}",
            "Content-Type": "application/json",
        }

    def _validate_creds(self):
        """Validates that credentials are correct"""
        if not self.access_token:
            logger.error("Missing Access Token for Azure DevOps integration")
            raise MissingIntegrationException("AzureDevOps Wiki")

        try:
            # Test authentication by fetching wikis
            self._get_wikis()
        except Exception as e:
            logger.error(f"Cannot authenticate user. Failed with error {e}")
            raise UnauthorizedException(datasource_type="AzureDevOps Wiki")

    def lazy_load(self) -> Iterator[Document]:
        """Loads wiki pages, comments and attachments as Langchain Documents"""
        self._init_client()
        self._validate_creds()

        for page in self._load_wiki_pages_streaming():
            # Main page content document
            yield self._transform_to_doc(page)

            # Comments document
            comments = page.get("comments", [])
            if comments:
                comments_doc = self._build_comments_doc(page, comments)
                if comments_doc:
                    yield comments_doc

            # Attachment documents
            repository_id = page.get("repository_id", "")
            for attachment in page.get("attachments", []):
                attachment_doc = self._build_attachment_doc(page, attachment, repository_id)
                if attachment_doc:
                    yield attachment_doc

    def _get_wikis(self) -> list[dict[str, Any]]:
        """Get all wikis for the project"""
        wikis = self._wiki_client.get_all_wikis(project=self.project)
        return [wiki.as_dict() for wiki in wikis]

    def _get_page_content(self, wiki_id: str, page_id: int, page_path: str) -> str:
        """Get the content of a specific wiki page"""
        try:
            # Get page with content included
            page_response = self._wiki_client.get_page(
                project=self.project,
                wiki_identifier=wiki_id,
                path=page_path,
                include_content=True,
                recursion_level="none",
            )

            # Extract page from response wrapper
            page = page_response.page if hasattr(page_response, "page") else page_response

            # Get content from the page
            content = page.content if hasattr(page, "content") else ""
            return content if content else ""
        except Exception as e:
            logger.warning(f"Failed to get content for page {page_id}: {e}")
            return ""

    def _should_process_wiki(self, wiki_name: str) -> bool:
        """Check if wiki should be processed based on wiki_identifier filter"""
        if self.wiki_identifier and wiki_name != self.wiki_identifier:
            logger.info(f"Skipping wiki {wiki_name} (looking for {self.wiki_identifier})")
            return False
        return True

    def _resolve_attachments_from_content(self, content: str, wiki_id: str, repository_id: str) -> list[dict[str, Any]]:
        """Extract and resolve attachment references from page content"""
        if not content or not repository_id:
            return []

        attachment_paths = self._extract_wiki_attachment_paths(content)
        if not attachment_paths:
            return []

        attachments: list[dict[str, Any]] = []
        attachments_index = self._get_wiki_attachments_index(wiki_id, repository_id)

        for att_path in attachment_paths:
            entry = attachments_index.get(att_path) or attachments_index.get(att_path.lstrip("/"))
            if entry and entry not in attachments:
                attachments.append(entry)
            else:
                logger.debug(f"Attachment not found in index: {att_path}")

        return attachments

    def _fetch_page_with_content(
        self, wiki_id: str, path: str, wiki_name: str, repository_id: str
    ) -> dict[str, Any] | None:
        """Fetch a single page's metadata, content, comments and attachment references"""
        try:
            # Fetch page metadata (to get ID)
            page_response = self._wiki_client.get_page(
                project=self.project,
                wiki_identifier=wiki_id,
                path=path,
                include_content=False,
            )
            page_with_id = page_response.page if hasattr(page_response, "page") else page_response

            if not (hasattr(page_with_id, "id") and page_with_id.id):
                logger.warning(f"Page at path {path} has no ID")
                return None

            page_id = page_with_id.id
            content = self._get_page_content(wiki_id, page_id, path)

            if not content:
                logger.warning(f"Page {path} has no content")

            # Fetch comments for this page
            comments = self._get_page_comments(wiki_id, page_id)

            # Find attachment references in page content and resolve via index
            attachments = self._resolve_attachments_from_content(content, wiki_id, repository_id)

            return {
                **page_with_id.as_dict(),
                "content": content,
                "wiki_name": wiki_name,
                "wiki_id": wiki_id,
                "repository_id": repository_id,
                "comments": comments,
                "attachments": attachments,
            }

        except Exception as e:
            logger.warning(f"Failed to fetch page at path {path}: {e}")
            return None

    def _process_wiki_batch(
        self, wiki_id: str, wiki_name: str, repository_id: str, batch_paths: list[str]
    ) -> Iterator[dict[str, Any]]:
        """Process a batch of wiki pages and yield results"""
        for path in batch_paths:
            page_data = self._fetch_page_with_content(wiki_id, path, wiki_name, repository_id)
            if page_data:
                yield page_data

    def _load_wiki_pages_streaming(self) -> Iterator[dict[str, Any]]:
        """
        Stream wiki pages as they are fetched (batch by batch).
        This allows indexing to start immediately instead of waiting for all metadata.
        """
        wikis = self._get_wikis()
        logger.info(f"Found {len(wikis)} wikis in project {self.project}")

        for wiki in wikis:
            wiki_id = wiki["id"]
            wiki_name = wiki["name"]
            repository_id = wiki.get("repository_id") or wiki.get("repositoryId", "")

            if not self._should_process_wiki(wiki_name):
                continue

            try:
                # Get all page paths from tree structure first (fast operation)
                logger.info(f"Fetching page tree for wiki {wiki_name}")
                page_paths = self._get_all_page_paths(wiki_id)

                # Filter paths by query
                matching_paths = [p for p in page_paths if self._matches_query(p)]
                total_matching = len(matching_paths)

                logger.info(f"Found {total_matching} matching pages in wiki {wiki_name}")

                # Process pages in batches of 100
                batch_size = 100
                for batch_start in range(0, total_matching, batch_size):
                    batch_end = min(batch_start + batch_size, total_matching)
                    batch_paths = matching_paths[batch_start:batch_end]

                    logger.info(
                        f"Processing batch {batch_start // batch_size + 1}: "
                        f"pages {batch_start + 1}-{batch_end} of {total_matching}"
                    )

                    yield from self._process_wiki_batch(wiki_id, wiki_name, repository_id, batch_paths)

            except Exception as e:
                logger.warning(f"Failed to load pages from wiki {wiki_name}: {e}")
                continue

    def _get_all_page_paths(self, wiki_id: str) -> list[str]:
        """Get all page paths from wiki tree structure (fast, no IDs needed)"""
        try:
            # Get the page tree structure
            root_page = self._wiki_client.get_page(
                project=self.project,
                wiki_identifier=wiki_id,
                path="/",
                recursion_level="full",
                include_content=False,
            )

            # Extract the actual page from response wrapper
            actual_page = root_page.page if hasattr(root_page, "page") else root_page

            # Collect all page paths from the tree
            page_paths = []

            def collect_paths(page_node):
                node_path = getattr(page_node, "path", None)
                # Skip root path "/" as it's just a container
                if node_path and node_path != "/":
                    page_paths.append(node_path)

                # Recursively collect paths from sub-pages
                if hasattr(page_node, "sub_pages") and page_node.sub_pages:
                    for subpage in page_node.sub_pages:
                        collect_paths(subpage)

            collect_paths(actual_page)
            return page_paths

        except Exception as e:
            logger.error(f"Failed to get page paths for wiki {wiki_id}: {e}")
            return []

    def _matches_query(self, page_path: str) -> bool:
        """Check if a page path matches the wiki_query filter"""
        if not self.wiki_query or self.wiki_query == "*":
            return True

        # Simple pattern matching - can be extended
        if "*" in self.wiki_query:
            prefix = self.wiki_query.replace("*", "")
            return page_path.startswith(prefix)

        return page_path == self.wiki_query

    def _transform_to_doc(self, page: dict[str, Any]) -> Document:
        """Transform Azure DevOps Wiki page to Langchain Document"""
        content = page.get("content", "")
        path = page.get("path", "")
        page_id = page.get("id")
        wiki_name = page.get("wiki_name")

        # Extract page name from path (last segment)
        page_name = path.split("/")[-1] if path else ""

        # Azure DevOps URLs use hyphens instead of spaces
        page_name_url = page_name.replace(" ", "-")

        # Construct proper URL: https://dev.azure.com/{org}/{project}/_wiki/wikis/{wiki_name}/{page_id}/{page_name}
        # Note: base_url already includes organization
        metadata = {
            "source": f"{self.base_url}/{self.project}/_wiki/wikis/{wiki_name}/{page_id}/{page_name_url}",
            "page_id": page_id,
            "page_path": path,
            "wiki_name": wiki_name,
            "wiki_id": page.get("wiki_id"),
            "order": page.get("order", 0),
        }

        # Add git information if available
        if "git_item_path" in page:
            metadata["git_item_path"] = page["git_item_path"]
        if "remote_url" in page:
            metadata["remote_url"] = page["remote_url"]

        return Document(page_content=content, metadata=metadata)

    def _get_page_comments(self, wiki_id: str, page_id: int) -> list[dict[str, Any]]:
        """Get all comments for a specific wiki page via the Azure DevOps REST API"""
        if not self._session:
            return []
        url = f"{self._get_rest_api_base_url()}/wiki/wikis/{wiki_id}/pages/{page_id}/comments"
        params = {"api-version": self.API_VERSION}
        try:
            response = self._session.get(url, params=params)
            response.raise_for_status()
            comment_data = response.json().get("comments", [])
            comments: list[dict[str, Any]] = []
            for comment in comment_data:
                comments.append(
                    {
                        "comment_id": comment.get("id"),
                        "content": comment.get("text", ""),
                        "author": comment.get("createdBy", {}).get("displayName", "Unknown"),
                        "created_date": comment.get("createdDate"),
                        "modified_date": comment.get("modifiedDate"),
                        "parent_id": comment.get("parentId"),
                    }
                )
            return comments
        except Exception as e:
            logger.warning(f"Failed to fetch comments for page {page_id}: {e}")
            return []

    def _build_comments_doc(self, page: dict[str, Any], comments: list[dict[str, Any]]) -> Document | None:
        """Build a Document containing all comments for a wiki page"""
        if not comments:
            return None

        path = page.get("path", "")
        page_id = page.get("id")
        wiki_name = page.get("wiki_name", "")
        page_name = path.split("/")[-1] if path else ""
        page_name_url = page_name.replace(" ", "-")

        # Format each comment as readable text
        parts: list[str] = []
        for comment in comments:
            author = comment.get("author", "Unknown")
            created = comment.get("created_date", "")
            text = (comment.get("content") or "").strip()
            if text:
                parts.append(f"**{author}** ({created}):\n{text}")

        if not parts:
            return None

        comments_content = "\n\n---\n\n".join(parts)
        # Use a unique source URL for comments to ensure they're indexed separately
        source = f"{self.base_url}/{self.project}/_wiki/wikis/{wiki_name}/{page_id}/{page_name_url}#comments"

        # Create summary for LLM routing
        comment_count = len(comments)
        authors = list({c.get("author", "Unknown") for c in comments if c.get("author")})
        summary = f"Comments and discussion threads from {comment_count} comment(s) by {', '.join(authors[:3])}"
        if len(authors) > 3:
            summary += f" and {len(authors) - 3} others"

        metadata = {
            "source": source,
            "page_id": page_id,
            "page_path": path,
            "wiki_name": wiki_name,
            "wiki_id": page.get("wiki_id"),
            "content_type": "comments",
            "summary": summary,  # Add summary for LLM routing
        }
        return Document(page_content=comments_content, metadata=metadata)

    def _extract_wiki_attachment_paths(self, content: str) -> list[str]:
        """Extract all /.attachments/ paths referenced in wiki page markdown content"""
        matches = re.findall(self.WIKI_ATTACHMENT_PATTERN, content)
        normalized: list[str] = []
        for match in matches:
            path = match.strip()
            if not path.startswith("/"):
                path = f"/{path}"
            if not path.startswith("/.attachments/"):
                continue
            if path not in normalized:
                normalized.append(path)
        return normalized

    def _get_wiki_attachments_index(self, wiki_id: str, repository_id: str) -> dict[str, dict[str, Any]]:
        """Build and cache an index of all wiki attachments keyed by path (with and without leading slash)"""
        if wiki_id in self._attachments_index_cache:
            return self._attachments_index_cache[wiki_id]

        if not self._session:
            return {}

        url = f"{self._get_rest_api_base_url()}/git/repositories/{repository_id}/items"
        params = {
            "scopePath": "/.attachments",
            "recursionLevel": "full",
            "includeContentMetadata": "true",
            "api-version": self.API_VERSION,
        }
        attachments_index: dict[str, dict[str, Any]] = {}
        try:
            response = self._session.get(url, params=params)
            response.raise_for_status()
            items = response.json().get("value", [])
            for item in items:
                if item.get("isFolder"):
                    continue
                item_path = item.get("path", "")
                name = item_path.split("/")[-1] if item_path else ""
                content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
                entry = {
                    "name": name,
                    "path": item_path,
                    "size": item.get("size", 0),
                    "content_type": content_type,
                    "download_url": item.get("url"),
                    "object_id": item.get("objectId"),
                }
                attachments_index[item_path] = entry
                attachments_index[item_path.lstrip("/")] = entry
        except Exception as e:
            logger.warning(f"Failed to fetch wiki attachments index for wiki {wiki_id}: {e}")

        self._attachments_index_cache[wiki_id] = attachments_index
        return attachments_index

    def _download_wiki_attachment(self, repository_id: str, object_id: str | None) -> bytes | None:
        """Download a wiki attachment blob by repository ID and git object ID"""
        if not object_id:
            logger.warning("Attempted to download wiki attachment with no object_id")
            return None
        if not repository_id:
            logger.warning(f"Attempted to download wiki attachment {object_id} with no repository_id")
            return None
        if not self._session:
            return None

        url = f"{self._get_rest_api_base_url()}/git/repositories/{repository_id}/blobs/{object_id}"
        params = {"api-version": self.API_VERSION}
        try:
            response = self._session.get(url, params=params)
            response.raise_for_status()
            if not response.content:
                logger.warning(f"Empty content received for wiki attachment {object_id}")
                return None
            logger.debug(f"Downloaded wiki attachment {object_id} ({len(response.content)} bytes)")
            return response.content
        except Exception as e:
            logger.error(f"Failed to download wiki attachment {object_id}: {e}")
            return None

    def _build_attachment_doc(
        self,
        page: dict[str, Any],
        attachment: dict[str, Any],
        repository_id: str,
    ) -> Document | None:
        """Download and index an attachment, returning a Document with extracted text and metadata"""
        name = attachment.get("name", "")
        att_path = attachment.get("path", "")
        content_type = attachment.get("content_type", "")
        object_id = attachment.get("object_id")

        if not object_id:
            logger.debug(f"Skipping attachment '{name}': no object_id")
            return None

        content_bytes = self._download_wiki_attachment(repository_id, object_id)
        if not content_bytes:
            return None

        text = self._extract_attachment_text(content_bytes, content_type, name)
        if not text or not text.strip():
            logger.debug(f"No text extracted from attachment '{name}' (type: {content_type})")
            return None

        clean_text = text.strip()
        summary = (clean_text[:300] + "...") if len(clean_text) > 300 else clean_text

        page_path = page.get("path", "")
        page_id = page.get("id")
        wiki_name = page.get("wiki_name", "")
        page_name = page_path.split("/")[-1] if page_path else ""
        page_name_url = page_name.replace(" ", "-")

        # Use the attachment path as part of the URL to make it unique
        source = (
            f"{self.base_url}/{self.project}/_wiki/wikis/{wiki_name}/{page_id}/{page_name_url}/attachments{att_path}"
        )

        metadata = {
            "source": source,
            "page_id": page_id,
            "page_path": page_path,
            "wiki_name": wiki_name,
            "wiki_id": page.get("wiki_id"),
            "content_type": "attachment",
            "attachment_name": name,
            "attachment_path": att_path,
            "attachment_mime_type": content_type,
            "attachment_summary": summary,
            "summary": f"File attachment: {name} ({content_type}) - {summary}",  # Add summary for LLM routing
        }
        return Document(page_content=clean_text, metadata=metadata)

    def _extract_attachment_text(self, content_bytes: bytes, content_type: str, filename: str) -> str:
        """Dispatch to the appropriate extractor based on MIME type or file extension"""
        ext = os.path.splitext(filename)[1].lower() if filename else ""

        if content_type in self.IMAGE_MIME_TYPES or ext in {
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".webp",
        }:
            return self._extract_image_text(content_bytes)
        if content_type in self.PDF_MIME_TYPES or ext == ".pdf":
            return self._extract_pdf_text(content_bytes)
        if content_type in self.DOCX_MIME_TYPES or ext in {".docx", ".doc"}:
            return self._extract_docx_text(content_bytes, filename)
        if content_type in self.XLSX_MIME_TYPES or ext in {".xlsx", ".xls"}:
            return self._extract_xlsx_text(content_bytes)
        if content_type in self.PPTX_MIME_TYPES or ext in {".pptx", ".ppt"}:
            return self._extract_pptx_text(content_bytes)
        if content_type in self.MSG_MIME_TYPES or ext == ".msg":
            return self._extract_msg_text(content_bytes)

        logger.debug(f"Unsupported attachment type '{content_type}' for '{filename}', skipping")
        return ""

    def _extract_image_text(self, content_bytes: bytes) -> str:
        """Extract text from an image using LLM vision/OCR capabilities"""
        if not self._chat_model:
            logger.debug("No chat model configured; skipping image OCR for attachment")
            return ""
        try:
            from codemie_tools.utils.image_processor import ImageProcessor

            processor = ImageProcessor(chat_model=self._chat_model)
            return processor.extract_text_from_image_bytes(content_bytes) or ""
        except Exception as e:
            logger.warning(f"Failed to extract text from image attachment: {e}")
            return ""

    def _extract_pdf_text(self, content_bytes: bytes) -> str:
        """Extract text from a PDF attachment using pdfplumber"""
        try:
            from codemie_tools.file_analysis.pdf.processor import PdfProcessor

            return PdfProcessor.extract_text_as_markdown(content_bytes) or ""
        except Exception as e:
            logger.warning(f"Failed to extract text from PDF attachment: {e}")
            return ""

    def _extract_docx_text(self, content_bytes: bytes, filename: str) -> str:
        """Extract text from a DOCX/DOC attachment"""
        try:
            from codemie_tools.file_analysis.docx.models import QueryType
            from codemie_tools.file_analysis.docx.processor import DocxProcessor

            processor = DocxProcessor(ocr_enabled=bool(self._chat_model), chat_model=self._chat_model)
            result = processor.read_document_from_bytes(content_bytes, filename, query=QueryType.TEXT)
            return result.text or ""
        except Exception as e:
            logger.warning(f"Failed to extract text from DOCX attachment '{filename}': {e}")
            return ""

    def _extract_xlsx_text(self, content_bytes: bytes) -> str:
        """Extract text from an XLSX/XLS attachment as markdown tables"""
        try:
            from codemie_tools.file_analysis.xlsx.processor import XlsxProcessor

            processor = XlsxProcessor()
            sheets = processor.load(content_bytes)
            return processor.convert(sheets) or ""
        except Exception as e:
            logger.warning(f"Failed to extract text from Excel attachment: {e}")
            return ""

    def _extract_pptx_text(self, content_bytes: bytes) -> str:
        """Extract text from a PPTX/PPT attachment"""
        try:
            from codemie_tools.file_analysis.pptx.processor import PptxProcessor

            processor = PptxProcessor(chat_model=self._chat_model)
            pptx_doc = PptxProcessor.open_pptx_document(content_bytes)
            return processor.extract_text_as_markdown(pptx_doc) or ""
        except Exception as e:
            logger.warning(f"Failed to extract text from PPTX attachment: {e}")
            return ""

    def _extract_msg_text(self, content_bytes: bytes) -> str:
        """Extract email content and metadata from an MSG attachment via MarkItDown"""
        try:
            from io import BytesIO

            from markitdown import MarkItDown

            md = MarkItDown()
            buffer = BytesIO(content_bytes)
            result = md.convert(buffer)
            return result.text_content or ""
        except Exception as e:
            logger.warning(f"Failed to extract text from MSG attachment: {e}")
            return ""

    def _count_matching_paths(self, page_node) -> int:
        """Recursively count pages that match the query filter"""
        count = 0
        node_path = getattr(page_node, "path", None)

        # Count this page if it matches the query (exclude root "/")
        if node_path and node_path != "/" and self._matches_query(node_path):
            count += 1

        # Recursively count sub-pages
        if hasattr(page_node, "sub_pages") and page_node.sub_pages:
            for subpage in page_node.sub_pages:
                count += self._count_matching_paths(subpage)

        return count

    def _get_wiki_page_count(self, wiki_id: str, wiki_name: str) -> int:
        """Get the count of matching pages for a single wiki"""
        try:
            # Get only the tree structure for counting (no individual page fetches)
            root_page = self._wiki_client.get_page(
                project=self.project,
                wiki_identifier=wiki_id,
                path="/",
                recursion_level="full",
                include_content=False,
            )

            # Extract the actual page from response wrapper
            actual_page = root_page.page if hasattr(root_page, "page") else root_page

            wiki_page_count = self._count_matching_paths(actual_page)
            logger.info(f"Health check: Found {wiki_page_count} pages in wiki {wiki_name}")
            return wiki_page_count

        except Exception as e:
            logger.warning(f"Failed to count pages for wiki {wiki_name}: {e}")
            return 0

    def _create_stats_response(self, total_pages: int) -> dict[str, Any]:
        """Create standardized stats response dictionary"""
        return {
            self.DOCUMENTS_COUNT_KEY: total_pages,
            self.TOTAL_DOCUMENTS_KEY: total_pages,
            self.SKIPPED_DOCUMENTS_KEY: 0,
        }

    def fetch_remote_stats(self) -> dict[str, Any]:
        """Fetch statistics about available wiki pages (quick count from tree structure only)"""
        self._init_client()
        self._validate_creds()

        try:
            wikis = self._get_wikis()
            total_pages = 0

            for wiki in wikis:
                wiki_id = wiki["id"]
                wiki_name = wiki["name"]

                if not self._should_process_wiki(wiki_name):
                    continue

                total_pages += self._get_wiki_page_count(wiki_id, wiki_name)

            logger.info(f"Health check: Total {total_pages} pages across all wikis")
            return self._create_stats_response(total_pages)

        except Exception as e:
            logger.error(f"Failed to fetch remote stats: {e}")
            return self._create_stats_response(0)
