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

"""Unit tests for EmlLoader."""

import email.mime.base
import email.mime.multipart
import email.mime.text
import tempfile
import os
from email import encoders as email_encoders
from unittest.mock import patch

from langchain_core.documents import Document

from codemie.datasource.loader.eml_loader import EmlLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_eml(
    subject: str = "Test Subject",
    sender: str = "alice@example.com",
    to: str = "bob@example.com",
    body: str = "Hello World",
    attachments: list | None = None,
) -> bytes:
    msg = email.mime.multipart.MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = "Mon, 01 Jan 2026 10:00:00 +0000"
    msg.attach(email.mime.text.MIMEText(body, "plain"))
    for fname, data in attachments or []:
        part = email.mime.base.MIMEBase("application", "octet-stream")
        part.set_payload(data)
        email_encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
        msg.attach(part)
    return msg.as_bytes()


def _write_tmp_eml(eml_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as f:
        f.write(eml_bytes)
        return f.name


# ---------------------------------------------------------------------------
# Basic header + body extraction
# ---------------------------------------------------------------------------


def test_lazy_load_yields_document():
    path = _write_tmp_eml(_build_eml())
    try:
        docs = list(EmlLoader(path).lazy_load())
        assert len(docs) >= 1
        assert isinstance(docs[0], Document)
    finally:
        os.unlink(path)


def test_lazy_load_body_in_page_content():
    path = _write_tmp_eml(_build_eml(body="Review the attached report."))
    try:
        docs = list(EmlLoader(path).lazy_load())
        assert "Review the attached report." in docs[0].page_content
    finally:
        os.unlink(path)


def test_lazy_load_subject_in_metadata():
    path = _write_tmp_eml(_build_eml(subject="Project Alpha"))
    try:
        docs = list(EmlLoader(path).lazy_load())
        assert docs[0].metadata["email_subject"] == "Project Alpha"
    finally:
        os.unlink(path)


def test_lazy_load_sender_in_metadata():
    path = _write_tmp_eml(_build_eml(sender="alice@example.com"))
    try:
        docs = list(EmlLoader(path).lazy_load())
        assert docs[0].metadata["email_from"] == "alice@example.com"
    finally:
        os.unlink(path)


def test_lazy_load_recipient_in_metadata():
    path = _write_tmp_eml(_build_eml(to="bob@example.com"))
    try:
        docs = list(EmlLoader(path).lazy_load())
        assert docs[0].metadata["email_to"] == "bob@example.com"
    finally:
        os.unlink(path)


def test_lazy_load_source_metadata_is_basename():
    path = _write_tmp_eml(_build_eml())
    try:
        docs = list(EmlLoader(path).lazy_load())
        assert docs[0].metadata["source"] == os.path.basename(path)
    finally:
        os.unlink(path)


def test_lazy_load_header_block_included_in_page_content():
    path = _write_tmp_eml(_build_eml(sender="alice@example.com", subject="Hello"))
    try:
        docs = list(EmlLoader(path).lazy_load())
        content = docs[0].page_content
        assert "alice@example.com" in content
        assert "Hello" in content
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# include_email_attachments=False — no attachment extraction
# ---------------------------------------------------------------------------


def test_no_attachment_extraction_when_disabled():
    eml = _build_eml(attachments=[("report.pdf", b"%PDF-1.4")])
    path = _write_tmp_eml(eml)
    try:
        with patch("codemie.datasource.loader.eml_loader.EmlLoader._extract_attachments") as mock_extract:
            list(EmlLoader(path, include_email_attachments=False).lazy_load())
        mock_extract.assert_not_called()
    finally:
        os.unlink(path)


def test_attachment_extraction_called_when_enabled():
    eml = _build_eml(attachments=[("report.pdf", b"%PDF-1.4")])
    path = _write_tmp_eml(eml)
    try:
        with patch(
            "codemie.datasource.loader.eml_loader.EmlLoader._extract_attachments", return_value=iter([])
        ) as mock_extract:
            list(EmlLoader(path, include_email_attachments=True).lazy_load())
        mock_extract.assert_called_once()
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Attachment extraction
# ---------------------------------------------------------------------------


def test_extract_attachments_yields_documents():
    eml = _build_eml(attachments=[("report.pdf", b"%PDF-1.4 text content")])
    path = _write_tmp_eml(eml)
    try:
        attachment_doc = Document(page_content="pdf content", metadata={"source": "report.pdf"})
        with patch(
            "codemie.datasource.loader.file_extraction_utils.extract_documents_from_bytes",
            return_value=[attachment_doc],
        ):
            docs = list(EmlLoader(path, include_email_attachments=True).lazy_load())
        attachment_docs = [d for d in docs if d.metadata.get("attachment_filename") == "report.pdf"]
        assert len(attachment_docs) == 1
    finally:
        os.unlink(path)


def test_extract_attachments_sets_email_source_metadata():
    eml = _build_eml(attachments=[("data.txt", b"content")])
    path = _write_tmp_eml(eml)
    try:
        attachment_doc = Document(page_content="data", metadata={})
        with patch(
            "codemie.datasource.loader.file_extraction_utils.extract_documents_from_bytes",
            return_value=[attachment_doc],
        ):
            docs = list(EmlLoader(path, include_email_attachments=True).lazy_load())
        att_docs = [d for d in docs if "attachment_filename" in d.metadata]
        assert att_docs[0].metadata["email_source"] == os.path.basename(path)
        assert att_docs[0].metadata["attachment_filename"] == "data.txt"
    finally:
        os.unlink(path)


def test_extract_attachments_skips_on_exception():
    eml = _build_eml(attachments=[("bad.pdf", b"%PDF")])
    path = _write_tmp_eml(eml)
    try:
        with patch(
            "codemie.datasource.loader.file_extraction_utils.extract_documents_from_bytes",
            side_effect=RuntimeError("parse error"),
        ):
            docs = list(EmlLoader(path, include_email_attachments=True).lazy_load())
        # Body doc still yielded; no attachment doc
        body_docs = [d for d in docs if "attachment_filename" not in d.metadata]
        assert len(body_docs) >= 1
    finally:
        os.unlink(path)


def test_extract_attachments_skips_non_multipart():
    # Plain (non-multipart) email has no attachments
    simple_bytes = b"From: alice@example.com\r\nSubject: Hi\r\n\r\nBody text"
    path = _write_tmp_eml(simple_bytes)
    try:
        with patch("codemie.datasource.loader.file_extraction_utils.extract_documents_from_bytes") as mock_extract:
            list(EmlLoader(path, include_email_attachments=True).lazy_load())
        mock_extract.assert_not_called()
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# _decode_payload
# ---------------------------------------------------------------------------


def test_decode_payload_returns_empty_for_none_payload():
    import email.message

    part = email.message.Message()
    part.set_payload(None)
    assert EmlLoader._decode_payload(part) == ""


def test_decode_payload_returns_empty_for_non_bytes_payload():
    import email.message
    from unittest.mock import patch as _patch

    part = email.message.Message()
    with _patch.object(part, "get_payload", return_value="string not bytes"):
        result = EmlLoader._decode_payload(part)
    assert result == ""


# ---------------------------------------------------------------------------
# HTML fallback body
# ---------------------------------------------------------------------------


def test_html_body_used_as_fallback_when_no_plain():
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["Subject"] = "HTML only"
    msg["From"] = "a@b.com"
    msg["To"] = "c@d.com"
    msg.attach(email.mime.text.MIMEText("<b>Rich content</b>", "html"))
    path = _write_tmp_eml(msg.as_bytes())
    try:
        docs = list(EmlLoader(path, include_email_attachments=False).lazy_load())
        assert len(docs) == 1
        assert "Rich content" in docs[0].page_content
    finally:
        os.unlink(path)
