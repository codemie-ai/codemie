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

from __future__ import annotations

import base64
import mimetypes
import os
import re
from typing import Any, Iterator, Optional

import requests
from azure.devops.connection import Connection
from azure.devops.v7_1.work_item_tracking import WorkItemTrackingClient, Wiql, TeamContext
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from msrest.authentication import BasicAuthentication
from pydantic import AnyHttpUrl

from codemie.configs import logger
from codemie.datasource.exceptions import (
    InvalidQueryException,
    MissingIntegrationException,
    UnauthorizedException,
)
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    clean = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", clean).strip()


# ADO system field name constants
_FIELD_TITLE = "System.Title"
_FIELD_WORK_ITEM_TYPE = "System.WorkItemType"
_FIELD_STATE = "System.State"

# Fields fetched for each work item
_DEFAULT_FIELDS = [
    "System.Id",
    _FIELD_TITLE,
    "System.Description",
    _FIELD_WORK_ITEM_TYPE,
    _FIELD_STATE,
    "System.AssignedTo",
    "System.AreaPath",
    "System.IterationPath",
    "System.Tags",
    "System.CreatedDate",
    "System.ChangedDate",
    "Microsoft.VSTS.Common.Priority",
]

# Supported MIME type categories (mirrors AzureDevOpsWikiLoader)
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

# Relation type indicating a file attachment on a work item
_ATTACHED_FILE_REL = "AttachedFile"

# Maximum attachment size to download (20 MB); larger files are skipped to prevent OOM
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


class AzureDevOpsWorkItemLoader(BaseLoader, BaseDatasourceLoader):
    """
    A LangChain loader for Azure DevOps Work Items using the official Azure DevOps SDK.

    Indexes work item fields, comments and file attachments (parity with ADO Wiki datasource).

    Example::

        loader = AzureDevOpsWorkItemLoader(
            base_url="https://dev.azure.com/organization",
            wiql_query="SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project",
            access_token="<personal_access_token>",
            organization="organization",
            project="project",
        )
    """

    DOCUMENTS_COUNT_KEY = "documents_count_key"
    API_VERSION = "7.1"

    def __init__(
        self,
        base_url: AnyHttpUrl,
        wiql_query: str,
        access_token: str,
        organization: str,
        project: str,
        batch_size: int = 50,
        chat_model: Optional[BaseChatModel] = None,
        index_comments: bool = True,
        index_attachments: bool = True,
    ):
        base_url_str = str(base_url).rstrip("/")
        if not base_url_str.endswith(organization):
            self.base_url = f"{base_url_str}/{organization}"
        else:
            self.base_url = base_url_str

        self.wiql_query = (
            wiql_query if wiql_query else "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project"
        )
        self.access_token = access_token
        self.organization = organization
        self.project = project
        self.batch_size = batch_size
        self.index_comments = index_comments
        self.index_attachments = index_attachments
        self._chat_model = chat_model
        self._connection = None
        self._work_item_client: WorkItemTrackingClient | None = None
        self._session: Optional[requests.Session] = None

    def _init_client(self):
        """Initialize Azure DevOps connection, work item tracking client and HTTP session."""
        credentials = BasicAuthentication("", self.access_token)
        self._connection = Connection(base_url=self.base_url, creds=credentials)
        self._work_item_client = self._connection.clients_v7_1.get_work_item_tracking_client()
        self._init_session()

    def _init_session(self) -> None:
        """Initialize authenticated HTTP session for direct REST API calls."""
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
        """Validate that credentials are correct by querying the project."""
        if not self.access_token:
            logger.error("Missing Access Token for Azure DevOps Work Items integration")
            raise MissingIntegrationException("AzureDevOps Work Items")

        try:
            self._run_wiql("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project", top=1)
        except InvalidQueryException:
            raise
        except Exception as e:
            logger.error(f"Cannot authenticate user for Azure DevOps Work Items. Failed with error {e}")
            raise UnauthorizedException(datasource_type="AzureDevOps Work Items")

    def _run_wiql(self, query: str, top: int | None = None) -> list[Any]:
        """Execute a WIQL query and return work item references."""
        try:
            wiql = Wiql(query=query)
            result = self._work_item_client.query_by_wiql(
                wiql,
                top=top,
                team_context=TeamContext(project=self.project),
            )
            return result.work_items or []
        except Exception as e:
            error_str = str(e).lower()
            if any(kw in error_str for kw in ("invalid", "syntax", "tf51005", "wiql")):
                raise InvalidQueryException("WIQL", str(e))
            raise

    def _fetch_work_item(self, work_item_id: int) -> dict[str, Any] | None:
        """Fetch full details of a single work item."""
        try:
            item = self._work_item_client.get_work_item(
                id=work_item_id,
                project=self.project,
                fields=_DEFAULT_FIELDS,
            )
            fields = item.fields or {}
            return {
                "id": item.id,
                "url": item.url,
                "fields": fields,
            }
        except Exception as e:
            logger.warning(f"Failed to fetch work item {work_item_id}: {e}")
            return None

    def _transform_to_doc(self, work_item: dict[str, Any]) -> Document:
        """Transform an Azure DevOps Work Item dict into a LangChain Document."""
        fields = work_item.get("fields", {})
        work_item_id = work_item.get("id")

        title = fields.get(_FIELD_TITLE, "")
        description = _strip_html(fields.get("System.Description") or "")
        wi_type = fields.get(_FIELD_WORK_ITEM_TYPE, "")
        state = fields.get(_FIELD_STATE, "")
        area_path = fields.get("System.AreaPath", "")
        iteration_path = fields.get("System.IterationPath", "")
        tags = fields.get("System.Tags", "")
        priority = fields.get("Microsoft.VSTS.Common.Priority", "")
        assigned_to = fields.get("System.AssignedTo", {})
        if isinstance(assigned_to, dict):
            assigned_to = assigned_to.get("displayName", "")

        # Compose human-readable content from all fields
        content_parts = [f"# {title}"]
        if description:
            content_parts.append(description)
        if wi_type:
            content_parts.append(f"Type: {wi_type}")
        if state:
            content_parts.append(f"State: {state}")
        if assigned_to:
            content_parts.append(f"Assigned To: {assigned_to}")
        if area_path:
            content_parts.append(f"Area: {area_path}")
        if iteration_path:
            content_parts.append(f"Iteration: {iteration_path}")
        if tags:
            content_parts.append(f"Tags: {tags}")
        if priority:
            content_parts.append(f"Priority: {priority}")

        page_content = "\n".join(content_parts)

        source_url = f"{self.base_url}/{self.project}/_workitems/edit/{work_item_id}"

        metadata = {
            "source": source_url,
            "work_item_id": work_item_id,
            "work_item_type": wi_type,
            "state": state,
            "title": title,
            "area_path": area_path,
            "iteration_path": iteration_path,
        }

        return Document(page_content=page_content, metadata=metadata)

    def _get_work_item_comments(self, work_item_id: int) -> list[dict[str, Any]]:
        """Fetch all comments for a work item via the SDK."""
        try:
            result = self._work_item_client.get_comments(
                project=self.project,
                work_item_id=work_item_id,
            )
            comments: list[dict[str, Any]] = []
            for comment in result.comments or []:
                comment_dict = comment.as_dict() if hasattr(comment, "as_dict") else {}
                created_by = comment_dict.get("created_by") or {}
                comments.append(
                    {
                        "comment_id": comment_dict.get("id"),
                        "content": _strip_html(comment_dict.get("text") or ""),
                        "author": created_by.get("display_name", "Unknown"),
                        "created_date": comment_dict.get("created_date"),
                        "modified_date": comment_dict.get("modified_date"),
                    }
                )
            return comments
        except Exception as e:
            logger.warning(f"Failed to fetch comments for work item {work_item_id}: {e}")
            return []

    def _build_comments_doc(self, work_item: dict[str, Any], comments: list[dict[str, Any]]) -> Document | None:
        """Build a Document containing all comments for a work item."""
        if not comments:
            return None

        work_item_id = work_item.get("id")
        fields = work_item.get("fields", {})
        title = fields.get(_FIELD_TITLE, "")
        wi_type = fields.get(_FIELD_WORK_ITEM_TYPE, "")
        state = fields.get(_FIELD_STATE, "")

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
        source = f"{self.base_url}/{self.project}/_workitems/edit/{work_item_id}#comments"

        comment_count = len(comments)
        authors = list({c.get("author", "Unknown") for c in comments if c.get("author")})
        summary = f"Comments and discussion threads from {comment_count} comment(s) by {', '.join(authors[:3])}"
        if len(authors) > 3:
            summary += f" and {len(authors) - 3} others"

        metadata = {
            "source": source,
            "work_item_id": work_item_id,
            "work_item_type": wi_type,
            "state": state,
            "title": title,
            "content_type": "comments",
            "summary": summary,
        }
        return Document(page_content=comments_content, metadata=metadata)

    def _get_work_item_attachments(self, work_item_id: int) -> list[dict[str, Any]]:
        """Fetch attachment relations for a work item by requesting it with $expand=Relations."""
        try:
            item = self._work_item_client.get_work_item(
                id=work_item_id,
                project=self.project,
                expand="Relations",
            )
            relations = item.relations or []
            attachments: list[dict[str, Any]] = []
            for rel in relations:
                rel_dict = rel.as_dict() if hasattr(rel, "as_dict") else {}
                rel_type = rel_dict.get("rel", "")
                if rel_type == _ATTACHED_FILE_REL:
                    url = rel_dict.get("url", "")
                    attributes = rel_dict.get("attributes") or {}
                    name = attributes.get("name", "")
                    resource_size = attributes.get("resourceSize", 0)
                    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    attachments.append(
                        {
                            "name": name,
                            "url": url,
                            "size": resource_size,
                            "content_type": content_type,
                            "comment": attributes.get("comment", ""),
                        }
                    )
            return attachments
        except Exception as e:
            logger.warning(f"Failed to fetch attachments for work item {work_item_id}: {e}")
            return []

    def _download_attachment(self, attachment_url: str, attachment_name: str) -> bytes | None:
        """Download an attachment by its URL using the authenticated HTTP session."""
        if not attachment_url:
            logger.warning("Attempted to download work item attachment with no URL")
            return None
        if not self._session:
            return None

        try:
            response = self._session.get(attachment_url, timeout=60)
            response.raise_for_status()
            if not response.content:
                logger.warning(f"Empty content received for attachment '{attachment_name}'")
                return None
            logger.debug(f"Downloaded attachment '{attachment_name}' ({len(response.content)} bytes)")
            return response.content
        except Exception as e:
            logger.error(f"Failed to download attachment '{attachment_name}': {e}")
            return None

    def _build_attachment_doc(
        self,
        work_item: dict[str, Any],
        attachment: dict[str, Any],
    ) -> Document | None:
        """Download and index an attachment, returning a Document with extracted text and metadata."""
        name = attachment.get("name", "")
        att_url = attachment.get("url", "")
        content_type = attachment.get("content_type", "")

        if not att_url:
            logger.debug(f"Skipping attachment '{name}': no URL")
            return None

        if attachment.get("size", 0) > MAX_ATTACHMENT_BYTES:
            logger.debug(f"Skipping oversized attachment '{name}'")
            return None

        content_bytes = self._download_attachment(att_url, name)
        if not content_bytes:
            return None

        text = self._extract_attachment_text(content_bytes, content_type, name)
        if not text or not text.strip():
            logger.debug(f"No text extracted from attachment '{name}' (type: {content_type})")
            return None

        clean_text = text.strip()
        summary = (clean_text[:300] + "...") if len(clean_text) > 300 else clean_text

        work_item_id = work_item.get("id")
        fields = work_item.get("fields", {})
        title = fields.get(_FIELD_TITLE, "")
        wi_type = fields.get(_FIELD_WORK_ITEM_TYPE, "")
        state = fields.get(_FIELD_STATE, "")

        source = f"{self.base_url}/{self.project}/_workitems/edit/{work_item_id}#attachment-{name}"

        metadata = {
            "source": source,
            "work_item_id": work_item_id,
            "work_item_type": wi_type,
            "state": state,
            "title": title,
            "content_type": "attachment",
            "attachment_name": name,
            "attachment_mime_type": content_type,
            "attachment_summary": summary,
            "summary": f"File attachment: {name} ({content_type}) - {summary}",
        }
        return Document(page_content=clean_text, metadata=metadata)

    def _extract_attachment_text(self, content_bytes: bytes, content_type: str, filename: str) -> str:
        """Dispatch to the appropriate extractor based on MIME type or file extension."""
        ext = os.path.splitext(filename)[1].lower() if filename else ""

        if content_type in IMAGE_MIME_TYPES or ext in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}:
            return self._extract_image_text(content_bytes)
        if content_type in PDF_MIME_TYPES or ext == ".pdf":
            return self._extract_pdf_text(content_bytes)
        if content_type in DOCX_MIME_TYPES or ext in {".docx", ".doc"}:
            return self._extract_docx_text(content_bytes, filename)
        if content_type in XLSX_MIME_TYPES or ext in {".xlsx", ".xls"}:
            return self._extract_xlsx_text(content_bytes)
        if content_type in PPTX_MIME_TYPES or ext in {".pptx", ".ppt"}:
            return self._extract_pptx_text(content_bytes)
        if content_type in MSG_MIME_TYPES or ext == ".msg":
            return self._extract_msg_text(content_bytes)

        logger.debug(f"Unsupported attachment type '{content_type}' for '{filename}', skipping")
        return ""

    def _extract_image_text(self, content_bytes: bytes) -> str:
        """Extract text from an image using LLM vision/OCR capabilities."""
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
        """Extract text from a PDF attachment using pdfplumber."""
        try:
            from codemie_tools.file_analysis.pdf.processor import PdfProcessor

            return PdfProcessor.extract_text_as_markdown(content_bytes) or ""
        except Exception as e:
            logger.warning(f"Failed to extract text from PDF attachment: {e}")
            return ""

    def _extract_docx_text(self, content_bytes: bytes, filename: str) -> str:
        """Extract text from a DOCX/DOC attachment."""
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
        """Extract text from an XLSX/XLS attachment as markdown tables."""
        try:
            from codemie_tools.file_analysis.xlsx.processor import XlsxProcessor

            processor = XlsxProcessor()
            sheets = processor.load(content_bytes)
            return processor.convert(sheets) or ""
        except Exception as e:
            logger.warning(f"Failed to extract text from Excel attachment: {e}")
            return ""

    def _extract_pptx_text(self, content_bytes: bytes) -> str:
        """Extract text from a PPTX/PPT attachment."""
        try:
            from codemie_tools.file_analysis.pptx.processor import PptxProcessor

            processor = PptxProcessor(chat_model=self._chat_model)
            pptx_doc = PptxProcessor.open_pptx_document(content_bytes)
            return processor.extract_text_as_markdown(pptx_doc) or ""
        except Exception as e:
            logger.warning(f"Failed to extract text from PPTX attachment: {e}")
            return ""

    def _extract_msg_text(self, content_bytes: bytes) -> str:
        """Extract email content and metadata from an MSG attachment via MarkItDown."""
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

    def _yield_attachment_docs(self, item: dict[str, Any], work_item_id: int) -> Iterator[Document]:
        """Yield Documents for all attachments of a single work item."""
        for attachment in self._get_work_item_attachments(work_item_id):
            try:
                attachment_doc = self._build_attachment_doc(item, attachment)
                if attachment_doc:
                    yield attachment_doc
            except Exception as e:
                att_name = attachment.get("name", "unknown")
                logger.warning(f"Skipping attachment '{att_name}' for work item {work_item_id}: {e}")

    def _process_single_work_item(self, ref: Any) -> Iterator[Document]:
        """Fetch and yield all documents (main, comments, attachments) for a single work item reference."""
        item = self._fetch_work_item(ref.id)
        if not item:
            return

        work_item_id = item.get("id")

        yield self._transform_to_doc(item)

        if self.index_comments:
            comments = self._get_work_item_comments(work_item_id)
            if comments:
                comments_doc = self._build_comments_doc(item, comments)
                if comments_doc:
                    yield comments_doc

        if self.index_attachments:
            yield from self._yield_attachment_docs(item, work_item_id)

    def lazy_load(self) -> Iterator[Document]:
        """Load work items, comments and attachments matching the WIQL query and yield LangChain Documents."""
        self._init_client()
        self._validate_creds()

        work_item_refs = self._run_wiql(self.wiql_query)
        logger.info(f"Found {len(work_item_refs)} work items for query in project {self.project}")

        for batch_start in range(0, len(work_item_refs), self.batch_size):
            batch = work_item_refs[batch_start : batch_start + self.batch_size]
            logger.info(
                f"Processing work items batch {batch_start // self.batch_size + 1}: "
                f"items {batch_start + 1}-{batch_start + len(batch)} of {len(work_item_refs)}"
            )
            for ref in batch:
                yield from self._process_single_work_item(ref)

    def _create_stats_response(self, total: int) -> dict[str, Any]:
        return {
            self.DOCUMENTS_COUNT_KEY: total,
            self.TOTAL_DOCUMENTS_KEY: total,
            self.SKIPPED_DOCUMENTS_KEY: 0,
        }

    def fetch_remote_stats(self) -> dict[str, Any]:
        """Count work items matching the WIQL query without fetching full details."""
        self._init_client()
        self._validate_creds()

        try:
            refs = self._run_wiql(self.wiql_query)
            total = len(refs)
            logger.info(f"Health check: {total} work items found in project {self.project}")
            return self._create_stats_response(total)
        except Exception as e:
            logger.error(f"Failed to fetch work item remote stats: {e}")
            return self._create_stats_response(0)
