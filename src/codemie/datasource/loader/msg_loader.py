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

"""LangChain-compatible loader for Outlook MSG files with attachment extraction.

Uses olefile to read the OLE compound document structure directly, yielding
one Document for the email body and one Document per attachment when
include_attachments=True.
"""

from __future__ import annotations

import os
from typing import Iterator

from langchain_core.documents import Document

from codemie.configs import logger

# MAPI property type suffix that indicates a Unicode (UTF-16-LE) string stream
_MAPI_PT_UNICODE = "001F"

# OLE property stream IDs used in MSG files (MAPI property format)
_BODY_UNICODE = "__substg1.0_1000001F"
_BODY_ASCII = "__substg1.0_1000001E"
_SUBJECT_UNICODE = "__substg1.0_0037001F"
_SUBJECT_ASCII = "__substg1.0_0037001E"
_SENDER_NAME = "__substg1.0_0C1A001F"
_SENDER_EMAIL = "__substg1.0_0C1F001F"
_MESSAGE_TO = "__substg1.0_0E04001F"
_ATTACH_DATA = "__substg1.0_37010102"  # PR_ATTACH_DATA_BIN
_ATTACH_FILENAME_SHORT = "__substg1.0_3704001F"  # PR_ATTACH_FILENAME
_ATTACH_FILENAME_LONG = "__substg1.0_3707001F"  # PR_ATTACH_LONG_FILENAME


def _read_stream_text(ole, stream_path: str) -> str:
    """Read a text stream from an OLE file, decoding UTF-16-LE for *001F streams."""
    try:
        if ole.exists(stream_path):
            raw = ole.openstream(stream_path).read()
            if stream_path.endswith(_MAPI_PT_UNICODE):
                return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
            return raw.decode("latin-1", errors="replace").rstrip("\x00")
    except Exception as e:
        logger.debug(f"MsgLoader: could not read stream {stream_path}: {e}")
    return ""


def _read_stream_bytes(ole, stream_path: str) -> bytes | None:
    try:
        if ole.exists(stream_path):
            return ole.openstream(stream_path).read()
    except Exception as e:
        logger.debug(f"MsgLoader: could not read binary stream {stream_path}: {e}")
    return None


class OutlookMsgWithAttachmentsLoader:
    """Load an Outlook MSG file, yielding body and attachment documents.

    Replaces the upstream OutlookMsgLoader for the purpose of attachment
    extraction.  When include_email_attachments=False the behaviour is equivalent
    to the original loader (body + headers only).
    """

    def __init__(self, file_path: str, include_email_attachments: bool = True) -> None:
        self.file_path = file_path
        self.include_email_attachments = include_email_attachments

    def lazy_load(self) -> Iterator[Document]:
        try:
            import olefile
        except ImportError:
            logger.error("MsgLoader: olefile is not installed; cannot parse MSG file")
            return

        source = os.path.basename(self.file_path)
        try:
            ole = olefile.OleFileIO(self.file_path)
        except Exception as e:
            logger.error(f"MsgLoader: failed to open MSG file {source}: {e}")
            return

        try:
            subject = _read_stream_text(ole, _SUBJECT_UNICODE) or _read_stream_text(ole, _SUBJECT_ASCII)
            sender = _read_stream_text(ole, _SENDER_EMAIL) or _read_stream_text(ole, _SENDER_NAME)
            recipients = _read_stream_text(ole, _MESSAGE_TO)
            body = _read_stream_text(ole, _BODY_UNICODE) or _read_stream_text(ole, _BODY_ASCII)

            header_block = "\n".join(
                part
                for part in [
                    f"From: {sender}" if sender else "",
                    f"To: {recipients}" if recipients else "",
                    f"Subject: {subject}" if subject else "",
                ]
                if part
            )
            full_body = f"{header_block}\n\n{body}".strip() if header_block else body

            if full_body:
                yield Document(
                    page_content=full_body,
                    metadata={
                        "source": source,
                        "email_subject": subject,
                        "email_from": sender,
                        "email_to": recipients,
                    },
                )

            if self.include_email_attachments:
                yield from self._extract_attachments(ole, source)
        finally:
            ole.close()

    def _extract_attachments(self, ole, msg_source: str) -> Iterator[Document]:
        # Import here to avoid circular dependency
        from codemie.datasource.loader.file_extraction_utils import extract_documents_from_bytes

        attach_dirs = [entry for entry in ole.listdir(streams=False, storages=True) if entry[0].startswith("__attach_")]

        for attach_dir in attach_dirs:
            dir_path = "/".join(attach_dir)
            filename = _read_stream_text(ole, f"{dir_path}/{_ATTACH_FILENAME_LONG}") or _read_stream_text(
                ole, f"{dir_path}/{_ATTACH_FILENAME_SHORT}"
            )
            if not filename:
                continue

            data_stream = f"{dir_path}/{_ATTACH_DATA}"
            attachment_bytes = _read_stream_bytes(ole, data_stream)
            if not attachment_bytes:
                continue

            logger.info(f"MsgLoader: extracting attachment '{filename}' from {msg_source}")
            try:
                docs = extract_documents_from_bytes(
                    file_bytes=attachment_bytes,
                    file_name=filename,
                )
                for doc in docs:
                    doc.metadata["email_source"] = msg_source
                    doc.metadata["attachment_filename"] = filename
                    yield doc
            except Exception as e:
                logger.warning(f"MsgLoader: failed to extract attachment '{filename}': {e}")
