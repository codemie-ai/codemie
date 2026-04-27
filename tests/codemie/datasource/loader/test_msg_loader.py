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

"""Unit tests for OutlookMsgWithAttachmentsLoader and helpers."""

from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

from codemie.datasource.loader.msg_loader import (
    OutlookMsgWithAttachmentsLoader,
    _read_stream_text,
    _read_stream_bytes,
)


# ---------------------------------------------------------------------------
# _read_stream_text
# ---------------------------------------------------------------------------


def test_read_stream_text_unicode_stream():
    ole = MagicMock()
    ole.exists.return_value = True
    text = "Hello"
    ole.openstream.return_value.read.return_value = text.encode("utf-16-le") + b"\x00\x00"
    result = _read_stream_text(ole, "__substg1.0_0037001F")
    assert result == "Hello"


def test_read_stream_text_latin1_stream():
    ole = MagicMock()
    ole.exists.return_value = True
    ole.openstream.return_value.read.return_value = b"Subject\x00"
    result = _read_stream_text(ole, "__substg1.0_0037001E")
    assert result == "Subject"


def test_read_stream_text_returns_empty_when_stream_missing():
    ole = MagicMock()
    ole.exists.return_value = False
    assert _read_stream_text(ole, "nonexistent") == ""


def test_read_stream_text_returns_empty_on_exception():
    ole = MagicMock()
    ole.exists.return_value = True
    ole.openstream.side_effect = OSError("read error")
    assert _read_stream_text(ole, "__substg1.0_0037001F") == ""


# ---------------------------------------------------------------------------
# _read_stream_bytes
# ---------------------------------------------------------------------------


def test_read_stream_bytes_returns_bytes():
    ole = MagicMock()
    ole.exists.return_value = True
    ole.openstream.return_value.read.return_value = b"\x89PNG"
    result = _read_stream_bytes(ole, "stream_path")
    assert result == b"\x89PNG"


def test_read_stream_bytes_returns_none_when_missing():
    ole = MagicMock()
    ole.exists.return_value = False
    assert _read_stream_bytes(ole, "stream_path") is None


def test_read_stream_bytes_returns_none_on_exception():
    ole = MagicMock()
    ole.exists.return_value = True
    ole.openstream.side_effect = OSError("boom")
    assert _read_stream_bytes(ole, "stream_path") is None


# ---------------------------------------------------------------------------
# OutlookMsgWithAttachmentsLoader.lazy_load — olefile not installed
# ---------------------------------------------------------------------------


def test_lazy_load_yields_nothing_when_olefile_missing():
    loader = OutlookMsgWithAttachmentsLoader("dummy.msg")
    with patch.dict("sys.modules", {"olefile": None}):
        docs = list(loader.lazy_load())
    assert docs == []


# ---------------------------------------------------------------------------
# OutlookMsgWithAttachmentsLoader.lazy_load — file open failure
# ---------------------------------------------------------------------------


def test_lazy_load_yields_nothing_when_file_open_fails():
    loader = OutlookMsgWithAttachmentsLoader("nonexistent.msg")
    mock_ole_module = MagicMock()
    mock_ole_module.OleFileIO.side_effect = OSError("file not found")
    with patch.dict("sys.modules", {"olefile": mock_ole_module}):
        docs = list(loader.lazy_load())
    assert docs == []


# ---------------------------------------------------------------------------
# OutlookMsgWithAttachmentsLoader.lazy_load — body document
# ---------------------------------------------------------------------------


def _make_ole_mock(subject="Test Subject", sender="alice@example.com", to="bob@example.com", body="Hello World"):
    """Return an olefile mock pre-wired with standard header streams."""

    def exists_side_effect(path):
        known = {
            "__substg1.0_0037001F",  # subject unicode
            "__substg1.0_0C1F001F",  # sender email
            "__substg1.0_0E04001F",  # to
            "__substg1.0_1000001F",  # body unicode
        }
        return path in known

    def openstream_side_effect(path):
        mapping = {
            "__substg1.0_0037001F": subject.encode("utf-16-le") + b"\x00\x00",
            "__substg1.0_0C1F001F": sender.encode("utf-16-le") + b"\x00\x00",
            "__substg1.0_0E04001F": to.encode("utf-16-le") + b"\x00\x00",
            "__substg1.0_1000001F": body.encode("utf-16-le") + b"\x00\x00",
        }
        stream = MagicMock()
        stream.read.return_value = mapping[path]
        return stream

    ole = MagicMock()
    ole.exists.side_effect = exists_side_effect
    ole.openstream.side_effect = openstream_side_effect
    ole.listdir.return_value = []
    return ole


def _loader_with_mock_ole(file_path="test.msg", include_email_attachments=True, ole=None):
    """Return (loader, mock_olefile_module) ready for use as a patch target."""
    if ole is None:
        ole = _make_ole_mock()
    mock_olefile = MagicMock()
    mock_olefile.OleFileIO.return_value = ole
    return OutlookMsgWithAttachmentsLoader(file_path, include_email_attachments=include_email_attachments), mock_olefile


def test_lazy_load_yields_body_document():
    loader, mock_olefile = _loader_with_mock_ole()
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        docs = list(loader.lazy_load())
    assert len(docs) >= 1
    assert isinstance(docs[0], Document)


def test_lazy_load_body_contains_email_body_text():
    loader, mock_olefile = _loader_with_mock_ole(ole=_make_ole_mock(body="Please review the contract."))
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        docs = list(loader.lazy_load())
    assert "Please review the contract." in docs[0].page_content


def test_lazy_load_subject_in_metadata():
    loader, mock_olefile = _loader_with_mock_ole(ole=_make_ole_mock(subject="Q1 Report"))
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        docs = list(loader.lazy_load())
    assert docs[0].metadata["email_subject"] == "Q1 Report"


def test_lazy_load_sender_in_metadata():
    loader, mock_olefile = _loader_with_mock_ole(ole=_make_ole_mock(sender="alice@example.com"))
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        docs = list(loader.lazy_load())
    assert docs[0].metadata["email_from"] == "alice@example.com"


def test_lazy_load_recipient_in_metadata():
    loader, mock_olefile = _loader_with_mock_ole(ole=_make_ole_mock(to="bob@example.com"))
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        docs = list(loader.lazy_load())
    assert docs[0].metadata["email_to"] == "bob@example.com"


def test_lazy_load_source_metadata_is_basename():
    loader, mock_olefile = _loader_with_mock_ole(file_path="/some/path/meeting.msg")
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        docs = list(loader.lazy_load())
    assert docs[0].metadata["source"] == "meeting.msg"


def test_lazy_load_header_included_in_page_content():
    loader, mock_olefile = _loader_with_mock_ole(
        ole=_make_ole_mock(sender="alice@example.com", subject="Budget Review")
    )
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        docs = list(loader.lazy_load())
    content = docs[0].page_content
    assert "alice@example.com" in content
    assert "Budget Review" in content


def test_lazy_load_ole_closed_after_load():
    loader, mock_olefile = _loader_with_mock_ole()
    ole = mock_olefile.OleFileIO.return_value
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        list(loader.lazy_load())
    ole.close.assert_called_once()


# ---------------------------------------------------------------------------
# include_attachments flag
# ---------------------------------------------------------------------------


def test_attachment_extraction_skipped_when_disabled():
    loader, mock_olefile = _loader_with_mock_ole(include_email_attachments=False)
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        with patch.object(loader, "_extract_attachments", return_value=iter([])) as mock_extract:
            list(loader.lazy_load())
    mock_extract.assert_not_called()


def test_attachment_extraction_called_when_enabled():
    loader, mock_olefile = _loader_with_mock_ole(include_email_attachments=True)
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        with patch.object(loader, "_extract_attachments", return_value=iter([])) as mock_extract:
            list(loader.lazy_load())
    mock_extract.assert_called_once()


# ---------------------------------------------------------------------------
# _extract_attachments
# ---------------------------------------------------------------------------


def _make_ole_with_attachment(filename="report.pdf", data=b"%PDF-1.4"):
    """Ole mock with body streams and one attachment directory.

    Uses the actual OLE stream IDs so the path matching in the loader works correctly.
    """
    # Actual stream paths that the loader will query
    body_streams = {
        "__substg1.0_0037001F": "Subject".encode("utf-16-le") + b"\x00\x00",
        "__substg1.0_0C1F001F": "sender@example.com".encode("utf-16-le") + b"\x00\x00",
        "__substg1.0_0E04001F": "to@example.com".encode("utf-16-le") + b"\x00\x00",
        "__substg1.0_1000001F": "Body text".encode("utf-16-le") + b"\x00\x00",
    }
    attach_dir = "__attach_00000000"
    attach_streams = {
        f"{attach_dir}/__substg1.0_3707001F": filename.encode("utf-16-le") + b"\x00\x00",  # long filename
        f"{attach_dir}/__substg1.0_37010102": data,  # attach data
    }
    all_streams = {**body_streams, **attach_streams}

    def exists_side_effect(path):
        return path in all_streams

    def openstream_side_effect(path):
        stream = MagicMock()
        stream.read.return_value = all_streams[path]
        return stream

    ole = MagicMock()
    ole.exists.side_effect = exists_side_effect
    ole.openstream.side_effect = openstream_side_effect
    ole.listdir.return_value = [[attach_dir]]
    return ole


def test_extract_attachments_yields_document():
    ole = _make_ole_with_attachment("report.pdf", b"%PDF-1.4")
    loader, mock_olefile = _loader_with_mock_ole(include_email_attachments=True, ole=ole)
    attachment_doc = Document(page_content="pdf content", metadata={})
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        with patch(
            "codemie.datasource.loader.file_extraction_utils.extract_documents_from_bytes",
            return_value=[attachment_doc],
        ):
            docs = list(loader.lazy_load())
    att_docs = [d for d in docs if "attachment_filename" in d.metadata]
    assert len(att_docs) == 1
    assert att_docs[0].metadata["attachment_filename"] == "report.pdf"


def test_extract_attachments_sets_email_source_metadata():
    ole = _make_ole_with_attachment("data.xlsx", b"PK\x03\x04")
    loader, mock_olefile = _loader_with_mock_ole(
        file_path="/path/to/email.msg", include_email_attachments=True, ole=ole
    )
    attachment_doc = Document(page_content="xlsx content", metadata={})
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        with patch(
            "codemie.datasource.loader.file_extraction_utils.extract_documents_from_bytes",
            return_value=[attachment_doc],
        ):
            docs = list(loader.lazy_load())
    att_docs = [d for d in docs if "attachment_filename" in d.metadata]
    assert att_docs[0].metadata["email_source"] == "email.msg"


def test_extract_attachments_skips_on_extraction_exception():
    ole = _make_ole_with_attachment("bad.pdf", b"%PDF")
    loader, mock_olefile = _loader_with_mock_ole(include_email_attachments=True, ole=ole)
    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        with patch(
            "codemie.datasource.loader.file_extraction_utils.extract_documents_from_bytes",
            side_effect=RuntimeError("parse error"),
        ):
            docs = list(loader.lazy_load())
    # Body doc still yielded; no attachment doc
    att_docs = [d for d in docs if "attachment_filename" in d.metadata]
    assert len(att_docs) == 0
    body_docs = [d for d in docs if "attachment_filename" not in d.metadata]
    assert len(body_docs) >= 1
