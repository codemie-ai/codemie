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

"""Specialized tool for EML and MSG email file analysis, including attachment extraction."""

from __future__ import annotations

import contextlib
import email as _email_lib
import email.message
import logging
import os
import tempfile
from typing import Optional, Type, List

from markitdown import MarkItDown
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_object import FileObject
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.tool_vars import EMAIL_TOOL

logger = logging.getLogger(__name__)

# OLE stream IDs for MSG files (MAPI property format)
_MSG_BODY_UNICODE = "__substg1.0_1000001F"
_MSG_BODY_ASCII = "__substg1.0_1000001E"
_MSG_SUBJECT_UNICODE = "__substg1.0_0037001F"
_MSG_SUBJECT_ASCII = "__substg1.0_0037001E"
_MSG_SENDER_NAME = "__substg1.0_0C1A001F"
_MSG_SENDER_EMAIL = "__substg1.0_0C1F001F"
_MSG_TO = "__substg1.0_0E04001F"
_MSG_ATTACH_DATA = "__substg1.0_37010102"
_MSG_ATTACH_FILENAME_SHORT = "__substg1.0_3704001F"
_MSG_ATTACH_FILENAME_LONG = "__substg1.0_3707001F"

# Extension → MIME type mapping for attachment FileObjects
_EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}


class EmailToolInput(BaseModel):
    attachment_names: list[str] = Field(
        default_factory=list,
        description=(
            "Filenames of attachments to analyze. "
            "Leave empty to receive only email metadata and the list of attached files."
        ),
    )


class EmailAnalysisTool(CodeMieTool, FileToolMixin):
    """Specialized tool for EML and MSG email files.

    Default call returns email headers, body, and a list of attachment names/sizes.
    Pass attachment filenames via `attachment_names` to extract their content using
    the appropriate specialized tool (PDFTool, DocxTool, XlsxTool, PPTXTool, or
    FileAnalysisTool for other formats).
    """

    name: str = EMAIL_TOOL.name or "email_analysis_tool"
    label: str = EMAIL_TOOL.label or "Email Analysis Tool"
    description: str = EMAIL_TOOL.description or ""
    args_schema: Type[BaseModel] = EmailToolInput

    config: FileAnalysisConfig

    def __init__(self, config: FileAnalysisConfig) -> None:
        super().__init__(config=config)

    def _get_supported_mime_types(self) -> Optional[List[str]]:
        return ["message/rfc822", "application/vnd.ms-outlook"]

    def _get_supported_extensions(self) -> Optional[List[str]]:
        return [".eml", ".msg"]

    def execute(self, attachment_names: list[str] | None = None) -> str:
        files = self._get_supported_files()
        if not files:
            raise ValueError(f"{self.name} requires at least one EML or MSG file.")

        results = []
        for file_obj in files:
            content = self._process_email_file(file_obj, attachment_names or [])
            results.append(f"## {file_obj.name}\n\n{content}")

        return "\n\n---\n\n".join(results)

    def _process_email_file(self, file_obj: FileObject, attachment_names: list[str]) -> str:
        ext = os.path.splitext(file_obj.name.lower())[1]
        raw = file_obj.bytes_content()
        if not raw:
            return f"Could not read file content: {file_obj.name}"
        if ext == ".eml":
            return self._process_eml(raw, file_obj.name, attachment_names)
        if ext == ".msg":
            return self._process_msg(raw, file_obj.name, attachment_names)
        return f"Unsupported email format: {file_obj.name}"

    def _process_eml(self, email_bytes: bytes, source_name: str, attachment_names: list[str]) -> str:
        msg = _email_lib.message_from_bytes(email_bytes)

        subject = msg.get("Subject", "")
        sender = msg.get("From", "")
        recipients = msg.get("To", "")
        date = msg.get("Date", "")
        cc = msg.get("Cc", "")

        header_lines = [
            line
            for line in [
                f"**From:** {sender}" if sender else "",
                f"**To:** {recipients}" if recipients else "",
                f"**Cc:** {cc}" if cc else "",
                f"**Subject:** {subject}" if subject else "",
                f"**Date:** {date}" if date else "",
            ]
            if line
        ]

        body = self._extract_eml_body(msg)
        parts = header_lines + (["", body] if body else [])

        if msg.is_multipart():
            attachments = self._list_eml_attachments(msg)
            if attachments:
                parts.append("\n**Attachments:**")
                for att_name, size in attachments:
                    parts.append(f"- {att_name} ({size} bytes)")

            if attachment_names:
                for att_name, data in self._get_eml_attachment_data(msg, attachment_names):
                    logger.info(f"EmailAnalysisTool: analyzing EML attachment '{att_name}' from {source_name}")
                    parts.append(f"\n### Attachment: {att_name}\n\n{self._analyze_attachment(data, att_name)}")

        return "\n".join(parts)

    def _extract_eml_body(self, msg: email.message.Message) -> str:
        plain_parts: list[str] = []
        html_parts: list[str] = []

        if msg.is_multipart():
            for part in msg.walk():
                disposition = part.get("Content-Disposition", "")
                if "attachment" in disposition:
                    continue
                content_type = part.get_content_type()
                decoded = self._decode_part(part)
                if decoded:
                    if content_type == "text/plain":
                        plain_parts.append(decoded)
                    elif content_type == "text/html":
                        html_parts.append(decoded)
        else:
            decoded = self._decode_part(msg)
            if decoded:
                if msg.get_content_type() == "text/html":
                    html_parts.append(decoded)
                else:
                    plain_parts.append(decoded)

        return "\n\n".join(plain_parts) if plain_parts else "\n\n".join(html_parts)

    @staticmethod
    def _decode_part(part: email.message.Message) -> str:
        try:
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes) or not payload:
                return ""
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except Exception as e:
            logger.warning(f"EmailAnalysisTool: failed to decode email part: {e}")
            return ""

    def _list_eml_attachments(self, msg: email.message.Message) -> list[tuple[str, int]]:
        """Return (filename, byte_size) pairs for all EML attachments."""
        result: list[tuple[str, int]] = []
        for part in msg.walk():
            disposition = part.get("Content-Disposition", "")
            if "attachment" not in disposition:
                continue
            filename = part.get_filename()
            if not filename:
                continue
            payload = part.get_payload(decode=True)
            size = len(payload) if isinstance(payload, bytes) else 0
            result.append((filename, size))
        return result

    def _get_eml_attachment_data(self, msg: email.message.Message, names: list[str]) -> list[tuple[str, bytes]]:
        """Return (filename, data) for each requested attachment (case-insensitive match)."""
        lower_names = {n.lower() for n in names}
        result: list[tuple[str, bytes]] = []
        for part in msg.walk():
            disposition = part.get("Content-Disposition", "")
            if "attachment" not in disposition:
                continue
            filename = part.get_filename()
            if not filename or filename.lower() not in lower_names:
                continue
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes) and payload:
                result.append((filename, payload))
        return result

    def _process_msg(self, msg_bytes: bytes, source_name: str, attachment_names: list[str]) -> str:
        try:
            import olefile
        except ImportError:
            logger.error("EmailAnalysisTool: olefile is not installed; cannot parse MSG file")
            return "MSG processing unavailable (olefile not installed)."

        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tmp:
                tmp.write(msg_bytes)
                tmp.flush()
                temp_path = tmp.name

            ole = olefile.OleFileIO(temp_path)
        except Exception as e:
            logger.error(f"EmailAnalysisTool: failed to open MSG file {source_name}: {e}")
            return f"Failed to open MSG file: {e}"

        try:
            subject = self._read_ole_text(ole, _MSG_SUBJECT_UNICODE) or self._read_ole_text(ole, _MSG_SUBJECT_ASCII)
            sender = self._read_ole_text(ole, _MSG_SENDER_EMAIL) or self._read_ole_text(ole, _MSG_SENDER_NAME)
            recipients = self._read_ole_text(ole, _MSG_TO)
            body = self._read_ole_text(ole, _MSG_BODY_UNICODE) or self._read_ole_text(ole, _MSG_BODY_ASCII)

            header_lines = [
                line
                for line in [
                    f"**From:** {sender}" if sender else "",
                    f"**To:** {recipients}" if recipients else "",
                    f"**Subject:** {subject}" if subject else "",
                ]
                if line
            ]

            parts = header_lines + (["", body] if body else [])

            attach_dirs = [
                entry for entry in ole.listdir(streams=False, storages=True) if entry[0].startswith("__attach_")
            ]

            # Build attachment registry: (filename, data_bytes_or_none)
            attach_registry: list[tuple[str, bytes | None]] = []
            for attach_dir in attach_dirs:
                dir_path = "/".join(attach_dir)
                filename = self._read_ole_text(ole, f"{dir_path}/{_MSG_ATTACH_FILENAME_LONG}") or self._read_ole_text(
                    ole, f"{dir_path}/{_MSG_ATTACH_FILENAME_SHORT}"
                )
                if not filename:
                    continue
                data: bytes | None = None
                data_stream = f"{dir_path}/{_MSG_ATTACH_DATA}"
                if ole.exists(data_stream):
                    try:
                        raw = ole.openstream(data_stream).read()
                        data = raw if raw else None
                    except Exception as e:
                        logger.warning(f"EmailAnalysisTool: failed to read MSG attachment '{filename}': {e}")
                attach_registry.append((filename, data))

            if attach_registry:
                parts.append("\n**Attachments:**")
                for att_name, data in attach_registry:
                    size = len(data) if data else 0
                    parts.append(f"- {att_name} ({size} bytes)")

            if attachment_names:
                lower_names = {n.lower() for n in attachment_names}
                for att_name, data in attach_registry:
                    if att_name.lower() not in lower_names or not data:
                        continue
                    logger.info(f"EmailAnalysisTool: analyzing MSG attachment '{att_name}' from {source_name}")
                    parts.append(f"\n### Attachment: {att_name}\n\n{self._analyze_attachment(data, att_name)}")

            return "\n".join(parts)
        finally:
            ole.close()
            if temp_path:
                with contextlib.suppress(Exception):
                    os.unlink(temp_path)

    @staticmethod
    def _read_ole_text(ole, stream_path: str) -> str:
        try:
            if ole.exists(stream_path):
                raw = ole.openstream(stream_path).read()
                if stream_path.endswith("001F"):
                    return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
                return raw.decode("latin-1", errors="replace").rstrip("\x00")
        except Exception as e:
            logger.debug(f"EmailAnalysisTool: could not read OLE stream {stream_path}: {e}")
        return ""

    def _analyze_attachment(self, data: bytes, filename: str) -> str:
        """Delegate attachment analysis to the appropriate specialized tool.

        Images require a temp file because MarkItDown's ImageConverter needs the file extension.
        All other formats are handled by their dedicated tool or FileAnalysisTool.
        """
        ext = os.path.splitext(filename.lower())[1]

        if ext in _IMAGE_EXTENSIONS:
            return self._analyze_image_attachment(data, filename, ext)

        from codemie_tools.file_analysis.docx.models import QueryType as DocxQueryType
        from codemie_tools.file_analysis.docx.tools import DocxTool
        from codemie_tools.file_analysis.file_analysis_tool import FileAnalysisTool
        from codemie_tools.file_analysis.pdf.tools import PDFTool
        from codemie_tools.file_analysis.pdf.tools import QueryType as PDFQueryType
        from codemie_tools.file_analysis.pptx.tools import PPTXTool
        from codemie_tools.file_analysis.pptx.tools import QueryType as PPTXQueryType
        from codemie_tools.file_analysis.xlsx.tools import XlsxTool

        mime = _EXT_TO_MIME.get(ext, "application/octet-stream")
        file_obj = FileObject(name=filename, mime_type=mime, owner="email", content=data)
        cfg = FileAnalysisConfig(input_files=[file_obj], chat_model=self.config.chat_model)

        try:
            if ext == ".pdf":
                return str(PDFTool(config=cfg).execute(pages=[], query=PDFQueryType.TEXT))
            if ext == ".docx":
                return str(DocxTool(config=cfg).execute(query=DocxQueryType.TEXT))
            if ext in (".xlsx", ".xls"):
                return str(XlsxTool(config=cfg).execute())
            if ext == ".pptx":
                return str(PPTXTool(config=cfg).execute(slides=[], query=PPTXQueryType.TEXT))
            # CSV, HTML, TXT, ZIP and other formats: use FileAnalysisTool's core processor directly
            # _is_supported_file() is intentionally bypassed — we are analyzing a known attachment
            result = FileAnalysisTool(config=cfg)._process_single_file(file_obj)
            return result or f"[No extractable text in {filename}]"
        except Exception as e:
            logger.warning(f"EmailAnalysisTool: failed to analyze attachment '{filename}': {e}")
            return f"[Could not extract content from {filename}: {e}]"

    def _analyze_image_attachment(self, data: bytes, filename: str, ext: str) -> str:
        """Analyze image attachments via MarkItDown using a temp file (extension required)."""
        temp_path: str | None = None
        try:
            chat_model = self.config.chat_model
            llm_model = (
                getattr(chat_model, "model_name", None) or getattr(chat_model, "model", None) if chat_model else None
            )
            md = MarkItDown(
                enable_builtins=True,
                llm_client=getattr(chat_model, "root_client", None) if chat_model else None,
                llm_model=llm_model,
            )
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(data)
                tmp.flush()
                temp_path = tmp.name
            result = md.convert(temp_path)
            return result.text_content or f"[No extractable text in {filename}]"
        except Exception as e:
            logger.warning(f"EmailAnalysisTool: failed to analyze image attachment '{filename}': {e}")
            return f"[Could not extract content from {filename}: {e}]"
        finally:
            if temp_path:
                with contextlib.suppress(Exception):
                    os.unlink(temp_path)
