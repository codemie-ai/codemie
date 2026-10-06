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

import base64
import io
import logging
import mimetypes
import os
from typing import Any, Dict, List, Optional, Type

import httpx
from langchain_core.tools import ToolException
from markdownify import markdownify
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_object import MimeType
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.base.http_result import HttpResult
from codemie_tools.file_analysis.docx.models import QueryType as DocxQueryType
from codemie_tools.file_analysis.docx.processor import DocxProcessor
from codemie_tools.file_analysis.pdf.processor import PdfProcessor
from codemie_tools.file_analysis.pptx.processor import PptxProcessor
from codemie_tools.file_analysis.xlsx.processor import XlsxProcessor

from .models import (
    CreatePageAttachmentInput,
    CreatePageCommentInput,
    CreatePageInput,
    DeletePageAttachmentInput,
    DeletePageInput,
    GetPageAttachmentInput,
    GetPageCommentInput,
    GetPageInput,
    GetSpaceInput,
    GetWikiInput,
    ListPageAttachmentsInput,
    ListPageChildrenInput,
    ListPageCommentsInput,
    ListPageTagsInput,
    ListPagesInput,
    ListSpacesInput,
    ListWikiPagesInput,
    ListWikiTagsInput,
    ListWikisInput,
    ModifyPageInput,
    ReadPageAttachmentContentInput,
    SearchSpaceInput,
    SearchWikiInput,
    SetPageTagsInput,
    XWikiConfig,
)
from .tools_vars import (
    CREATE_PAGE_ATTACHMENT_TOOL,
    CREATE_PAGE_COMMENT_TOOL,
    CREATE_PAGE_TOOL,
    DELETE_PAGE_ATTACHMENT_TOOL,
    DELETE_PAGE_TOOL,
    GET_PAGE_ATTACHMENT_TOOL,
    GET_PAGE_COMMENT_TOOL,
    GET_PAGE_TOOL,
    GET_SPACE_TOOL,
    GET_WIKI_TOOL,
    LIST_PAGE_ATTACHMENTS_TOOL,
    LIST_PAGE_CHILDREN_TOOL,
    LIST_PAGE_COMMENTS_TOOL,
    LIST_PAGE_TAGS_TOOL,
    LIST_PAGES_TOOL,
    LIST_SPACES_TOOL,
    LIST_WIKI_PAGES_TOOL,
    LIST_WIKI_TAGS_TOOL,
    LIST_WIKIS_TOOL,
    MODIFY_PAGE_TOOL,
    READ_PAGE_ATTACHMENT_CONTENT_TOOL,
    SEARCH_SPACE_TOOL,
    SEARCH_WIKI_TOOL,
    SET_PAGE_TAGS_TOOL,
)
from .utils import build_spaces_path, validate_creds

logger = logging.getLogger(__name__)


class _XWikiBaseTool(CodeMieTool):
    """Shared HTTP client and auth logic for all xWiki tools."""

    config: XWikiConfig
    tokens_size_limit: int = 20_000
    throw_truncated_error: bool = False

    def _build_auth_headers(self) -> dict:
        if self.config.use_bearer:
            return {"Authorization": f"Bearer {self.config.token}"}
        username = self.config.username or ""
        credentials = base64.b64encode(f"{username}:{self.config.token}".encode()).decode()
        return {"Authorization": f"Basic {credentials}"}

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> tuple[httpx.Response, str]:
        headers = {**self._build_auth_headers(), "Accept": "application/json"}
        try:
            with httpx.Client(timeout=30) as client:
                if method.upper() == "GET":
                    response = client.get(url, params=params or {}, headers=headers)
                else:
                    headers["Content-Type"] = "application/json"
                    response = client.request(method.upper(), url, json=json_body or {}, headers=headers)
        except httpx.RequestError as e:
            raise ToolException(f"xWiki request failed: {e}")
        try:
            text = response.text
        except Exception:
            text = str(response.content)
        return response, text

    def _format_result(
        self,
        method: str,
        url: str,
        response: httpx.Response,
        text: str,
        *,
        is_markdown: bool = False,
    ) -> HttpResult:
        if is_markdown and response.status_code < 300:
            text = markdownify(text, heading_style="ATX")
        return HttpResult(method, url, response.status_code, response.reason_phrase, text, "body_on_new_line")

    def _healthcheck(self) -> None:
        validate_creds(self.config)
        base_url = self.config.url.rstrip("/")
        with httpx.Client(timeout=10) as client:
            response = client.get(f"{base_url}/rest/wikis", headers=self._build_auth_headers())
        if response.status_code != 200:
            raise AssertionError(f"xWiki healthcheck failed: HTTP {response.status_code}")


# ---------------------------------------------------------------------------
# Wiki tools
# ---------------------------------------------------------------------------


class ListWikisTool(_XWikiBaseTool):
    script_callable = True
    name: str = LIST_WIKIS_TOOL.name
    description: str = LIST_WIKIS_TOOL.description
    args_schema: Type[BaseModel] = ListWikisInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, number: int = 50, start: int = 0) -> str:
        validate_creds(self.config)
        url = f"{self.config.url.rstrip('/')}/rest/wikis"
        response, text = self._request("GET", url, params={"number": number, "start": start})
        return self._format_result("GET", url, response, text)


class GetWikiTool(_XWikiBaseTool):
    script_callable = True
    name: str = GET_WIKI_TOOL.name
    description: str = GET_WIKI_TOOL.description
    args_schema: Type[BaseModel] = GetWikiInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki") -> str:
        validate_creds(self.config)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}"
        response, text = self._request("GET", url)
        return self._format_result("GET", url, response, text)


# ---------------------------------------------------------------------------
# Space tools
# ---------------------------------------------------------------------------


class ListSpacesTool(_XWikiBaseTool):
    script_callable = True
    name: str = LIST_SPACES_TOOL.name
    description: str = LIST_SPACES_TOOL.description
    args_schema: Type[BaseModel] = ListSpacesInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki", number: int = 50, start: int = 0) -> str:
        validate_creds(self.config)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}/spaces"
        response, text = self._request("GET", url, params={"number": number, "start": start})
        return self._format_result("GET", url, response, text)


class GetSpaceTool(_XWikiBaseTool):
    script_callable = True
    name: str = GET_SPACE_TOOL.name
    description: str = GET_SPACE_TOOL.description
    args_schema: Type[BaseModel] = GetSpaceInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki", space: str = "") -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}"
        response, text = self._request("GET", url)
        return self._format_result("GET", url, response, text)


# ---------------------------------------------------------------------------
# Page tools
# ---------------------------------------------------------------------------


class ListPagesTool(_XWikiBaseTool):
    script_callable = True
    name: str = LIST_PAGES_TOOL.name
    description: str = LIST_PAGES_TOOL.description
    args_schema: Type[BaseModel] = ListPagesInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki", space: str = "", number: int = 50, start: int = 0) -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages"
        response, text = self._request("GET", url, params={"number": number, "start": start})
        return self._format_result("GET", url, response, text)


class ListWikiPagesTool(_XWikiBaseTool):
    script_callable = True
    name: str = LIST_WIKI_PAGES_TOOL.name
    description: str = LIST_WIKI_PAGES_TOOL.description
    args_schema: Type[BaseModel] = ListWikiPagesInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki", number: int = 50, start: int = 0) -> str:
        validate_creds(self.config)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}/pages"
        response, text = self._request("GET", url, params={"number": number, "start": start})
        return self._format_result("GET", url, response, text)


class ListPageChildrenTool(_XWikiBaseTool):
    script_callable = True
    name: str = LIST_PAGE_CHILDREN_TOOL.name
    description: str = LIST_PAGE_CHILDREN_TOOL.description
    args_schema: Type[BaseModel] = ListPageChildrenInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "", number: int = 50, start: int = 0) -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}/children"
        response, text = self._request("GET", url, params={"number": number, "start": start})
        return self._format_result("GET", url, response, text)


class GetPageTool(_XWikiBaseTool):
    script_callable = True
    name: str = GET_PAGE_TOOL.name
    description: str = GET_PAGE_TOOL.description
    args_schema: Type[BaseModel] = GetPageInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "", is_markdown: bool = False) -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}"
        response, text = self._request("GET", url)
        return self._format_result("GET", url, response, text, is_markdown=is_markdown)


class CreatePageTool(_XWikiBaseTool):
    script_callable = True
    name: str = CREATE_PAGE_TOOL.name
    description: str = CREATE_PAGE_TOOL.description
    args_schema: Type[BaseModel] = CreatePageInput

    def execute(
        self,
        wiki: str = "xwiki",
        space: str = "",
        page: str = "",
        title: str = "",
        content: str = "",
        syntax: str = "xwiki/2.1",
    ) -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}"
        body = {"title": title, "content": content, "syntax": syntax}
        response, text = self._request("PUT", url, json_body=body)
        return self._format_result("PUT", url, response, text)


class ModifyPageTool(_XWikiBaseTool):
    script_callable = True
    name: str = MODIFY_PAGE_TOOL.name
    description: str = MODIFY_PAGE_TOOL.description
    args_schema: Type[BaseModel] = ModifyPageInput

    def execute(
        self,
        wiki: str = "xwiki",
        space: str = "",
        page: str = "",
        content: str = "",
        title: Optional[str] = None,
        syntax: str = "xwiki/2.1",
    ) -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}"
        body: dict = {"content": content, "syntax": syntax}
        if title is not None:
            body["title"] = title
        response, text = self._request("PUT", url, json_body=body)
        return self._format_result("PUT", url, response, text)


class DeletePageTool(_XWikiBaseTool):
    script_callable = True
    name: str = DELETE_PAGE_TOOL.name
    description: str = DELETE_PAGE_TOOL.description
    args_schema: Type[BaseModel] = DeletePageInput

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "") -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}"
        response, text = self._request("DELETE", url)
        return self._format_result("DELETE", url, response, text)


# ---------------------------------------------------------------------------
# Tag tools
# ---------------------------------------------------------------------------


class ListWikiTagsTool(_XWikiBaseTool):
    script_callable = True
    name: str = LIST_WIKI_TAGS_TOOL.name
    description: str = LIST_WIKI_TAGS_TOOL.description
    args_schema: Type[BaseModel] = ListWikiTagsInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki") -> str:
        validate_creds(self.config)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}/tags"
        response, text = self._request("GET", url)
        return self._format_result("GET", url, response, text)


class ListPageTagsTool(_XWikiBaseTool):
    script_callable = True
    name: str = LIST_PAGE_TAGS_TOOL.name
    description: str = LIST_PAGE_TAGS_TOOL.description
    args_schema: Type[BaseModel] = ListPageTagsInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "") -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}/tags"
        response, text = self._request("GET", url)
        return self._format_result("GET", url, response, text)


class SetPageTagsTool(_XWikiBaseTool):
    script_callable = True
    name: str = SET_PAGE_TAGS_TOOL.name
    description: str = SET_PAGE_TAGS_TOOL.description
    args_schema: Type[BaseModel] = SetPageTagsInput

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "", tags: Optional[List[str]] = None) -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}/tags"
        body = {"tags": [{"name": t} for t in (tags or [])]}
        response, text = self._request("PUT", url, json_body=body)
        return self._format_result("PUT", url, response, text)


# ---------------------------------------------------------------------------
# Comment tools
# ---------------------------------------------------------------------------


class ListPageCommentsTool(_XWikiBaseTool):
    script_callable = True
    name: str = LIST_PAGE_COMMENTS_TOOL.name
    description: str = LIST_PAGE_COMMENTS_TOOL.description
    args_schema: Type[BaseModel] = ListPageCommentsInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "", number: int = 20, start: int = 0) -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}/comments"
        response, text = self._request("GET", url, params={"number": number, "start": start})
        return self._format_result("GET", url, response, text)


class GetPageCommentTool(_XWikiBaseTool):
    script_callable = True
    name: str = GET_PAGE_COMMENT_TOOL.name
    description: str = GET_PAGE_COMMENT_TOOL.description
    args_schema: Type[BaseModel] = GetPageCommentInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "", comment_id: int = 0) -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}/comments/{comment_id}"
        response, text = self._request("GET", url)
        return self._format_result("GET", url, response, text)


class CreatePageCommentTool(_XWikiBaseTool):
    script_callable = True
    name: str = CREATE_PAGE_COMMENT_TOOL.name
    description: str = CREATE_PAGE_COMMENT_TOOL.description
    args_schema: Type[BaseModel] = CreatePageCommentInput

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "", text: str = "") -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}/comments"
        body = {"text": text}
        response, response_text = self._request("POST", url, json_body=body)
        return self._format_result("POST", url, response, response_text)


# ---------------------------------------------------------------------------
# Attachment tools
# ---------------------------------------------------------------------------


class ListPageAttachmentsTool(_XWikiBaseTool):
    script_callable = True
    name: str = LIST_PAGE_ATTACHMENTS_TOOL.name
    description: str = LIST_PAGE_ATTACHMENTS_TOOL.description
    args_schema: Type[BaseModel] = ListPageAttachmentsInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "") -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}/attachments"
        response, text = self._request("GET", url)
        return self._format_result("GET", url, response, text)


class GetPageAttachmentTool(_XWikiBaseTool):
    script_callable = True
    name: str = GET_PAGE_ATTACHMENT_TOOL.name
    description: str = GET_PAGE_ATTACHMENT_TOOL.description
    args_schema: Type[BaseModel] = GetPageAttachmentInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "", filename: str = "") -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}/attachments/{filename}"
        response, text = self._request("GET", url)
        return self._format_result("GET", url, response, text)


class CreatePageAttachmentTool(_XWikiBaseTool, FileToolMixin):
    script_callable = True
    name: str = CREATE_PAGE_ATTACHMENT_TOOL.name
    description: str = CREATE_PAGE_ATTACHMENT_TOOL.description
    args_schema: Type[BaseModel] = CreatePageAttachmentInput

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "") -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        base_url = self.config.url.rstrip("/")
        attachments_url = f"{base_url}/rest/wikis/{wiki}{spaces_path}/pages/{page}/attachments"
        auth_headers = self._build_auth_headers()

        files = self._resolve_files()
        if not files:
            raise ToolException("No files available to upload. Provide input files via the tool's file input.")

        results = []
        for filename, (content, mime_type) in files.items():
            try:
                with httpx.Client(timeout=60) as client:
                    response = client.post(
                        attachments_url,
                        headers=auth_headers,
                        files={"file": (filename, content, mime_type)},
                    )
                results.append(f"'{filename}': HTTP {response.status_code} {response.reason_phrase}")
                logger.info(f"Uploaded attachment '{filename}' to {attachments_url}: {response.status_code}")
            except httpx.RequestError as e:
                raise ToolException(f"Failed to upload '{filename}': {e}")

        return "\n".join(results)


class DeletePageAttachmentTool(_XWikiBaseTool):
    script_callable = True
    name: str = DELETE_PAGE_ATTACHMENT_TOOL.name
    description: str = DELETE_PAGE_ATTACHMENT_TOOL.description
    args_schema: Type[BaseModel] = DeletePageAttachmentInput

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "", filename: str = "") -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}/attachments/{filename}"
        response, text = self._request("DELETE", url)
        return self._format_result("DELETE", url, response, text)


class ReadPageAttachmentContentTool(_XWikiBaseTool):
    script_callable = True
    name: str = READ_PAGE_ATTACHMENT_CONTENT_TOOL.name
    description: str = READ_PAGE_ATTACHMENT_CONTENT_TOOL.description
    args_schema: Type[BaseModel] = ReadPageAttachmentContentInput

    def is_safe(self, args: dict) -> bool:
        return True

    _MAX_BASE64_BYTES: int = 50_000

    # Excel extensions used as a fallback when the OS MIME table cannot resolve the
    # type (e.g. a slim Linux container without /etc/mime.types has no .xlsb entry).
    _EXCEL_EXTENSIONS: frozenset = frozenset({".xlsx", ".xls", ".xlsb"})

    _TEXT_EXTENSIONS: frozenset = frozenset(
        {
            ".txt",
            ".md",
            ".markdown",
            ".json",
            ".xml",
            ".csv",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
            ".log",
            ".html",
            ".htm",
            ".rst",
            ".properties",
            ".env",
        }
    )

    def _detect_mime_type(self, filename: str) -> str:
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or "application/octet-stream"

    def _is_text_based(self, mime_type: str, filename: str) -> bool:
        if mime_type.startswith("text/"):
            return True
        ext = os.path.splitext(filename)[1].lower()
        return ext in self._TEXT_EXTENSIONS

    def _build_base64_response(self, content_bytes: bytes, note: str) -> Dict[str, Any]:
        if len(content_bytes) <= self._MAX_BASE64_BYTES:
            return {
                "content_type": "base64",
                "content": base64.b64encode(content_bytes).decode("utf-8"),
                "note": note,
            }
        size_kb = len(content_bytes) / 1024
        return {
            "content_type": "metadata_only",
            "content": None,
            "note": (
                f"{note} "
                f"The file is too large ({size_kb:.1f} KB) to return as base64 without being truncated. "
                f"Only metadata is provided."
            ),
        }

    def _extract_pdf_metadata(self, content_bytes: bytes) -> str:
        import pdfplumber

        parts: list[str] = []
        try:
            with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
                parts.append(f"Total pages: {len(pdf.pages)}")
                if pdf.metadata:
                    meta_lines = [f"  {k}: {v}" for k, v in pdf.metadata.items() if v]
                    if meta_lines:
                        parts.append("Document metadata:")
                        parts.extend(meta_lines)
                page_summaries = []
                for idx, page in enumerate(pdf.pages, start=1):
                    img_count = len(page.images) if page.images else 0
                    page_summaries.append(
                        f"  Page {idx}: {round(page.width, 1)}x{round(page.height, 1)} pt, {img_count} embedded image(s)"
                    )
                if page_summaries:
                    parts.append("Page details:")
                    parts.extend(page_summaries)
        except Exception as e:
            logger.warning(f"Failed to extract PDF metadata: {e}")
            parts.append(f"(metadata extraction failed: {e})")
        return "\n".join(parts)

    def _process_pdf_content(self, filename: str, content_bytes: bytes) -> Dict[str, Any]:
        try:
            text = PdfProcessor.extract_text_as_markdown(content_bytes)
            if text.strip():
                return {"content_type": "text", "content": text, "note": None}
            metadata_text = self._extract_pdf_metadata(content_bytes)
            no_text_note = (
                "PDF appears to contain only images with no selectable text. "
                "A chat model with vision capabilities is required for OCR. "
                "Below is the structural metadata that could be extracted."
            )
            return {
                "content_type": "text",
                "content": f"{no_text_note}\n\n{metadata_text}",
                "note": no_text_note,
            }
        except Exception as e:
            logger.warning(f"PDF text extraction failed for '{filename}': {e}")
            try:
                metadata_text = self._extract_pdf_metadata(content_bytes)
                return {
                    "content_type": "text",
                    "content": f"PDF text extraction failed: {e}. Structural metadata:\n\n{metadata_text}",
                    "note": f"PDF text extraction failed: {e}.",
                }
            except Exception:
                return self._build_base64_response(content_bytes, f"PDF text extraction failed: {e}.")

    def _process_content(self, filename: str, content_bytes: bytes) -> Dict[str, Any]:
        mime_type = self._detect_mime_type(filename)
        mime = MimeType(mime_type)

        if self._is_text_based(mime_type, filename):
            try:
                return {
                    "content_type": "text",
                    "content": content_bytes.decode("utf-8", errors="replace"),
                    "note": None,
                }
            except Exception as e:
                logger.warning(f"Failed to decode text file '{filename}': {e}")
                return self._build_base64_response(content_bytes, f"Text decoding failed: {e}. Returned as base64.")

        if mime.is_pdf:
            return self._process_pdf_content(filename, content_bytes)

        if mime.is_image:
            return self._build_base64_response(
                content_bytes,
                "Image content cannot be described without a chat model. "
                "Provide a chat model via the tool's chat_model field to enable AI-based image description.",
            )

        if mime.is_docx:
            try:
                processor = DocxProcessor(ocr_enabled=False, chat_model=None)
                doc_content = processor.read_document_from_bytes(
                    content=content_bytes,
                    file_name=filename,
                    query=DocxQueryType.TEXT,
                )
                return {"content_type": "text", "content": doc_content.text, "note": None}
            except Exception as e:
                logger.warning(f"DOCX text extraction failed for '{filename}': {e}")
                return self._build_base64_response(content_bytes, f"DOCX extraction failed: {e}.")

        if mime.is_pptx:
            try:
                processor = PptxProcessor(chat_model=None)
                pptx_document = PptxProcessor.open_pptx_document(content_bytes)
                text = processor.extract_text_as_markdown(pptx_document)
                return {"content_type": "text", "content": text, "note": None}
            except Exception as e:
                logger.warning(f"PPTX text extraction failed for '{filename}': {e}")
                return self._build_base64_response(content_bytes, f"PPTX extraction failed: {e}.")

        if mime.is_excel or os.path.splitext(filename)[1].lower() in self._EXCEL_EXTENSIONS:
            try:
                file_ext = os.path.splitext(filename)[1].lower() or ".xlsx"
                processor = XlsxProcessor()
                sheets = processor.load(content_bytes, file_ext=file_ext)
                text = processor.convert(sheets)
                return {"content_type": "text", "content": text, "note": None}
            except Exception as e:
                logger.warning(f"Excel text extraction failed for '{filename}': {e}")
                return self._build_base64_response(content_bytes, f"Excel extraction failed: {e}.")

        return self._build_base64_response(content_bytes, f"File type '{mime_type}' cannot be parsed to text.")

    def execute(self, wiki: str = "xwiki", space: str = "", page: str = "", filename: str = "") -> Dict[str, Any]:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/pages/{page}/attachments/{filename}"

        # Use a raw GET without Accept: application/json — xWiki returns binary content directly
        # from the single-attachment REST endpoint regardless of the Accept header.
        auth_headers = self._build_auth_headers()
        try:
            with httpx.Client(timeout=60) as client:
                response = client.get(url, headers=auth_headers, follow_redirects=True)
        except httpx.RequestError as e:
            raise ToolException(f"Failed to download attachment '{filename}': {e}")

        if response.status_code >= 300:
            raise ToolException(
                f"Failed to download attachment '{filename}': HTTP {response.status_code} {response.reason_phrase}"
            )

        content_bytes = response.content
        mime_type = self._detect_mime_type(filename)
        processed = self._process_content(filename, content_bytes)

        return {
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": len(content_bytes),
            "content_type": processed["content_type"],
            "content": processed["content"],
            "note": processed["note"],
        }


# ---------------------------------------------------------------------------
# Search tools
# ---------------------------------------------------------------------------


class SearchWikiTool(_XWikiBaseTool):
    script_callable = True
    name: str = SEARCH_WIKI_TOOL.name
    description: str = SEARCH_WIKI_TOOL.description
    args_schema: Type[BaseModel] = SearchWikiInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(
        self,
        wiki: str = "xwiki",
        query: str = "",
        scope: str = "content",
        space: Optional[str] = None,
        number: int = 10,
        start: int = 0,
        is_markdown: bool = False,
    ) -> str:
        validate_creds(self.config)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}/search"
        params: dict = {"q": query, "scope": scope, "number": number, "start": start}
        if space:
            params["space"] = space
        response, text = self._request("GET", url, params=params)
        return self._format_result("GET", url, response, text, is_markdown=is_markdown)


class SearchSpaceTool(_XWikiBaseTool):
    script_callable = True
    name: str = SEARCH_SPACE_TOOL.name
    description: str = SEARCH_SPACE_TOOL.description
    args_schema: Type[BaseModel] = SearchSpaceInput

    def is_safe(self, args: dict) -> bool:
        return True

    def execute(
        self,
        wiki: str = "xwiki",
        space: str = "",
        query: str = "",
        number: int = 10,
        start: int = 0,
        is_markdown: bool = False,
    ) -> str:
        validate_creds(self.config)
        spaces_path = build_spaces_path(space)
        url = f"{self.config.url.rstrip('/')}/rest/wikis/{wiki}{spaces_path}/search"
        response, text = self._request("GET", url, params={"q": query, "number": number, "start": start})
        return self._format_result("GET", url, response, text, is_markdown=is_markdown)
