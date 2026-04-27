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

"""LangChain-compatible loader for EML (RFC 2822) email files with attachment extraction."""

from __future__ import annotations

import email
import email.message
import os
from typing import Iterator

from langchain_core.documents import Document

from codemie.configs import logger

_CONTENT_TYPE_PLAIN = "text/plain"
_CONTENT_TYPE_HTML = "text/html"


class EmlLoader:
    """Load an EML file and optionally extract embedded attachments.

    Yields one Document for the email body, then one Document per attachment
    (dispatched through extract_documents_from_bytes) when include_email_attachments=True.
    """

    def __init__(self, file_path: str, include_email_attachments: bool = True) -> None:
        self.file_path = file_path
        self.include_email_attachments = include_email_attachments

    def lazy_load(self) -> Iterator[Document]:
        with open(self.file_path, "rb") as f:
            msg = email.message_from_bytes(f.read())

        source = os.path.basename(self.file_path)
        body_text = self._extract_body(msg)
        subject = msg.get("Subject", "")
        sender = msg.get("From", "")
        recipients = msg.get("To", "")
        date = msg.get("Date", "")

        header_block = "\n".join(
            part
            for part in [
                f"From: {sender}" if sender else "",
                f"To: {recipients}" if recipients else "",
                f"Subject: {subject}" if subject else "",
                f"Date: {date}" if date else "",
            ]
            if part
        )
        full_body = f"{header_block}\n\n{body_text}".strip() if header_block else body_text

        if full_body:
            yield Document(
                page_content=full_body,
                metadata={
                    "source": source,
                    "email_subject": subject,
                    "email_from": sender,
                    "email_to": recipients,
                    "email_date": date,
                },
            )

        if self.include_email_attachments:
            yield from self._extract_attachments(msg, source)

    def _extract_body(self, msg: email.message.Message) -> str:
        """Return the plain-text (or HTML-fallback) body of the email."""
        plain_parts: list[str] = []
        html_parts: list[str] = []

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = part.get("Content-Disposition", "")
                if "attachment" in disposition:
                    continue
                if content_type == _CONTENT_TYPE_PLAIN:
                    plain_parts.append(self._decode_payload(part))
                elif content_type == _CONTENT_TYPE_HTML:
                    html_parts.append(self._decode_payload(part))
        else:
            payload = self._decode_payload(msg)
            if msg.get_content_type() == _CONTENT_TYPE_HTML:
                html_parts.append(payload)
            else:
                plain_parts.append(payload)

        if plain_parts:
            return "\n\n".join(p for p in plain_parts if p)
        return "\n\n".join(p for p in html_parts if p)

    @staticmethod
    def _decode_payload(part: email.message.Message) -> str:
        try:
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes) or not payload:
                return ""
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except Exception as e:
            logger.warning(f"EmlLoader: failed to decode email part: {e}")
            return ""

    def _extract_attachments(self, msg: email.message.Message, email_source: str) -> Iterator[Document]:
        # Import here to avoid circular dependency (this module is imported by file_extraction_utils)
        from codemie.datasource.loader.file_extraction_utils import extract_documents_from_bytes

        if not msg.is_multipart():
            return

        for part in msg.walk():
            disposition = part.get("Content-Disposition", "")
            if "attachment" not in disposition:
                continue

            filename = part.get_filename()
            if not filename:
                continue

            attachment_bytes = part.get_payload(decode=True)
            if not isinstance(attachment_bytes, bytes) or not attachment_bytes:
                continue

            logger.info(f"EmlLoader: extracting attachment '{filename}' from {email_source}")
            try:
                docs = extract_documents_from_bytes(
                    file_bytes=attachment_bytes,
                    file_name=filename,
                )
                for doc in docs:
                    doc.metadata["email_source"] = email_source
                    doc.metadata["attachment_filename"] = filename
                    yield doc
            except Exception as e:
                logger.warning(f"EmlLoader: failed to extract attachment '{filename}': {e}")
