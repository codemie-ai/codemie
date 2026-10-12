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

"""Transport-agnostic enrichment: message body plus extracted text of its attachments.

Input attachments are neutral ``(filename, mime, content)`` tuples where content is ``bytes``, a zero-arg
callable returning bytes (fetched lazily, only if the attachment passes the count and size caps), or ``None``
for item/reference attachments. An optional fourth element is the declared size in bytes: the per-attachment and
running-total caps are checked against it before any fetch (unknown size: fetch, then check the real length). Fetch errors propagate as typed ExchangeError. Skip notes use fixed sentences.
"""

from __future__ import annotations

from typing import Callable, Iterable

from codemie_tools.file_analysis.pdf.processor import PdfProcessor
from codemie_tools.file_analysis.workers.docx_workers import extract_docx_text

MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024

NOTE_NOT_A_FILE = "attachment is not a file"
NOTE_TOO_LARGE = "attachment too large"
NOTE_COUNT_EXCEEDED = "attachment limit exceeded"
NOTE_TOTAL_EXCEEDED = "total attachment size limit exceeded"
NOTE_UNSUPPORTED = "unsupported attachment type"
NOTE_NO_OCR = "no OCR model provided"
NOTE_EXTRACTION_FAILED = "text extraction failed"

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

Content = bytes | Callable[[], bytes] | None
AttachmentInput = tuple[str, str, Content] | tuple[str, str, Content, int | None]
Ocr = Callable[[bytes], str]


def _kind(filename: str, mime: str) -> str | None:
    mime = (mime or "").lower()
    name = (filename or "").lower()
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if mime == _DOCX_MIME or name.endswith(".docx"):
        return "docx"
    return None


def _skip(name: str, reason: str) -> str:
    return f"[skipped: {name}] {reason}"


def _extract(kind: str, data: bytes, ocr: Ocr | None) -> str:
    if kind == "image":
        return ocr(data)
    if kind == "pdf":
        return PdfProcessor.extract_text_as_markdown(data)
    return extract_docx_text(data)


def enrich(body: str, attachments: Iterable[AttachmentInput], ocr: Ocr | None) -> str:
    parts = [body or ""]
    total = 0
    for index, (name, mime, content, *rest) in enumerate(attachments):
        declared = rest[0] if rest and isinstance(rest[0], int) else None
        if index >= MAX_ATTACHMENTS:
            parts.append(_skip(name, NOTE_COUNT_EXCEEDED))
            continue
        if content is None:
            parts.append(_skip(name, NOTE_NOT_A_FILE))
            continue
        kind = _kind(name, mime)
        if kind is None:
            parts.append(_skip(name, NOTE_UNSUPPORTED))
            continue
        if declared is not None and declared > MAX_ATTACHMENT_BYTES:
            parts.append(_skip(name, NOTE_TOO_LARGE))
            continue
        if declared is not None and total + declared > MAX_TOTAL_BYTES:
            parts.append(_skip(name, NOTE_TOTAL_EXCEEDED))
            continue
        if kind == "image" and ocr is None:
            parts.append(_skip(name, NOTE_NO_OCR))
            continue
        data = content() if callable(content) else content
        if len(data) > MAX_ATTACHMENT_BYTES:
            parts.append(_skip(name, NOTE_TOO_LARGE))
            continue
        if total + len(data) > MAX_TOTAL_BYTES:
            parts.append(_skip(name, NOTE_TOTAL_EXCEEDED))
            continue
        total += max(len(data), declared or 0)
        try:
            text = _extract(kind, data, ocr)
        except Exception:  # noqa: BLE001 - provider text must never surface
            text = ""
        if not text or not text.strip():
            parts.append(_skip(name, NOTE_EXTRACTION_FAILED))
            continue
        parts.append(f"--- Attachment: {name} ---\n{text}")
    return "\n\n".join(parts)
