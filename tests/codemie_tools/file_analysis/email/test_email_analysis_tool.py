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

"""Unit tests for EmailAnalysisTool."""

import email.mime.base
import email.mime.multipart
import email.mime.text
import struct
from email import encoders as email_encoders
from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.email.tools import (
    EmailAnalysisTool,
    _MSG_BODY_HTML,
    _MSG_BODY_RTF,
    _MSG_DATE,
    _MSG_INTERNET_CPID,
    _MSG_TRANSPORT_HEADERS,
)
from codemie_tools.file_analysis.models import FileAnalysisConfig


def _build_eml(
    subject: str = "Test Subject",
    sender: str = "alice@example.com",
    to: str = "bob@example.com",
    cc: str = "",
    body: str = "Hello World",
    attachments: list | None = None,
) -> bytes:
    """Build a minimal RFC-2822 EML message as bytes."""
    msg = email.mime.multipart.MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg.attach(email.mime.text.MIMEText(body, "plain"))
    for fname, data in attachments or []:
        part = email.mime.base.MIMEBase("application", "octet-stream")
        part.set_payload(data)
        email_encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
        msg.attach(part)
    return msg.as_bytes()


def _make_tool(eml_bytes: bytes, filename: str = "test.eml") -> EmailAnalysisTool:
    file_obj = FileObject(name=filename, mime_type="message/rfc822", owner="test", content=eml_bytes)
    return EmailAnalysisTool(config=FileAnalysisConfig(input_files=[file_obj]))


def _make_config() -> FileAnalysisConfig:
    file_obj = FileObject(name="x.eml", mime_type="message/rfc822", owner="test", content=_build_eml())
    return FileAnalysisConfig(input_files=[file_obj])


def test_supported_mime_types_includes_rfc822():
    assert "message/rfc822" in EmailAnalysisTool(_make_config())._get_supported_mime_types()


def test_supported_mime_types_includes_outlook():
    assert "application/vnd.ms-outlook" in EmailAnalysisTool(_make_config())._get_supported_mime_types()


def test_supported_extensions_includes_eml():
    assert ".eml" in EmailAnalysisTool(_make_config())._get_supported_extensions()


def test_supported_extensions_includes_msg():
    assert ".msg" in EmailAnalysisTool(_make_config())._get_supported_extensions()


def test_execute_raises_when_no_email_files():
    file_obj = FileObject(name="doc.pdf", mime_type="application/pdf", owner="test", content=b"data")
    tool = EmailAnalysisTool(config=FileAnalysisConfig(input_files=[file_obj]))
    with pytest.raises(ValueError, match="requires at least one EML or MSG file"):
        tool.execute()


def test_execute_eml_contains_from_header():
    result = _make_tool(_build_eml(sender="alice@example.com")).execute()
    assert "alice@example.com" in result


def test_execute_eml_contains_to_header():
    result = _make_tool(_build_eml(to="bob@example.com")).execute()
    assert "bob@example.com" in result


def test_execute_eml_contains_subject():
    result = _make_tool(_build_eml(subject="Project Alpha")).execute()
    assert "Project Alpha" in result


def test_execute_eml_contains_body():
    result = _make_tool(_build_eml(body="Please review the attached report.")).execute()
    assert "Please review the attached report." in result


def test_execute_eml_contains_cc_when_present():
    result = _make_tool(_build_eml(cc="charlie@example.com")).execute()
    assert "charlie@example.com" in result


def test_execute_eml_lists_attachment_filenames_by_default():
    eml = _build_eml(attachments=[("report.pdf", b"%PDF-1.4"), ("data.xlsx", b"PK\x03\x04")])
    result = _make_tool(eml).execute()
    assert "report.pdf" in result
    assert "data.xlsx" in result


def test_execute_eml_shows_attachment_sizes():
    payload = b"%PDF-1.4 minimal"
    eml = _build_eml(attachments=[("doc.pdf", payload)])
    result = _make_tool(eml).execute()
    assert "bytes" in result


def test_execute_eml_does_not_extract_attachment_content_by_default():
    eml = _build_eml(attachments=[("report.pdf", b"%PDF-1.4")])
    result = _make_tool(eml).execute()
    assert "### Attachment:" not in result


def test_execute_eml_analyzes_requested_attachment():
    eml = _build_eml(attachments=[("report.pdf", b"%PDF-1.4")])
    tool = _make_tool(eml)
    with patch.object(tool, "_analyze_attachment", return_value="PDF extracted text") as mock_analyze:
        result = tool.execute(attachment_names=["report.pdf"])
    mock_analyze.assert_called_once()
    assert "### Attachment: report.pdf" in result
    assert "PDF extracted text" in result


def test_execute_eml_attachment_name_match_is_case_insensitive():
    eml = _build_eml(attachments=[("Report.PDF", b"%PDF-1.4")])
    tool = _make_tool(eml)
    with patch.object(tool, "_analyze_attachment", return_value="content") as mock_analyze:
        tool.execute(attachment_names=["report.pdf"])
    mock_analyze.assert_called_once()


def test_execute_eml_skips_unlisted_attachments():
    eml = _build_eml(attachments=[("a.pdf", b"data"), ("b.xlsx", b"data")])
    tool = _make_tool(eml)
    with patch.object(tool, "_analyze_attachment", return_value="text") as mock_analyze:
        tool.execute(attachment_names=["a.pdf"])
    assert mock_analyze.call_count == 1
    assert mock_analyze.call_args[0][1] == "a.pdf"


def test_execute_eml_no_analysis_when_attachment_names_empty():
    eml = _build_eml(attachments=[("doc.pdf", b"%PDF")])
    tool = _make_tool(eml)
    with patch.object(tool, "_analyze_attachment") as mock_analyze:
        tool.execute(attachment_names=[])
    mock_analyze.assert_not_called()


def test_analyze_attachment_dispatches_to_pdf_tool():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.pdf.tools.PDFTool") as mock_pdf_cls:
        mock_pdf_cls.return_value.execute.return_value = "pdf text"
        result = tool._analyze_attachment(b"%PDF-1.4", "contract.pdf")
    mock_pdf_cls.assert_called_once()
    assert result == "pdf text"


def test_analyze_attachment_dispatches_to_docx_tool():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.docx.tools.DocxTool") as mock_docx_cls:
        mock_docx_cls.return_value.execute.return_value = "docx text"
        result = tool._analyze_attachment(b"PK\x03\x04", "letter.docx")
    mock_docx_cls.assert_called_once()
    assert result == "docx text"


def test_analyze_attachment_dispatches_to_xlsx_tool():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.xlsx.tools.XlsxTool") as mock_xlsx_cls:
        mock_xlsx_cls.return_value.execute.return_value = "xlsx text"
        result = tool._analyze_attachment(b"PK\x03\x04", "budget.xlsx")
    mock_xlsx_cls.assert_called_once()
    assert result == "xlsx text"


def test_analyze_attachment_dispatches_to_xlsx_tool_for_xls():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.xlsx.tools.XlsxTool") as mock_xlsx_cls:
        mock_xlsx_cls.return_value.execute.return_value = "xls text"
        result = tool._analyze_attachment(b"\xd0\xcf\x11\xe0", "legacy.xls")
    mock_xlsx_cls.assert_called_once()
    assert result == "xls text"


def test_analyze_attachment_dispatches_to_pptx_tool():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.pptx.tools.PPTXTool") as mock_pptx_cls:
        mock_pptx_cls.return_value.execute.return_value = "pptx text"
        result = tool._analyze_attachment(b"PK\x03\x04", "slides.pptx")
    mock_pptx_cls.assert_called_once()
    assert result == "pptx text"


def test_analyze_attachment_uses_file_analysis_tool_for_unknown_format():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.file_analysis_tool.FileAnalysisTool") as mock_fa_cls:
        mock_fa_cls.return_value._process_single_file.return_value = "plain text content"
        result = tool._analyze_attachment(b"plain content", "notes.txt")
    mock_fa_cls.assert_called_once()
    assert result == "plain text content"


def test_analyze_attachment_uses_file_analysis_tool_for_csv():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.file_analysis_tool.FileAnalysisTool") as mock_fa_cls:
        mock_fa_cls.return_value._process_single_file.return_value = "csv content"
        result = tool._analyze_attachment(b"a,b\n1,2", "data.csv")
    mock_fa_cls.assert_called_once()
    assert result == "csv content"


def test_analyze_attachment_returns_error_message_on_exception():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.pdf.tools.PDFTool") as mock_pdf_cls:
        mock_pdf_cls.return_value.execute.side_effect = RuntimeError("parse error")
        result = tool._analyze_attachment(b"%PDF", "broken.pdf")
    assert "Could not extract content from broken.pdf" in result


def test_analyze_image_attachment_calls_markitdown():
    tool = EmailAnalysisTool(config=_make_config())
    mock_md = MagicMock()
    mock_md.convert.return_value.text_content = "A graph showing revenue trends"
    with patch("codemie_tools.file_analysis.email.tools.MarkItDown", return_value=mock_md):
        result = tool._analyze_image_attachment(b"\x89PNG\r\n\x1a\n", "chart.png", ".png")
    assert result == "A graph showing revenue trends"


def test_analyze_image_attachment_passes_temp_file_path_not_bytes():
    """MarkItDown must receive a file path string so it can detect the image type by extension."""
    tool = EmailAnalysisTool(config=_make_config())
    mock_md = MagicMock()
    mock_md.convert.return_value.text_content = "description"
    with patch("codemie_tools.file_analysis.email.tools.MarkItDown", return_value=mock_md):
        tool._analyze_image_attachment(b"\x89PNG\r\n", "photo.png", ".png")
    called_arg = mock_md.convert.call_args[0][0]
    assert isinstance(called_arg, str)
    assert called_arg.endswith(".png")


def test_analyze_image_attachment_uses_root_client_for_llm():
    """root_client (full OpenAI client) must be passed — not client (Completions sub-object)."""
    mock_chat_model = MagicMock()
    mock_chat_model.root_client = MagicMock(name="root_openai_client")
    file_obj = FileObject(name="x.eml", mime_type="message/rfc822", owner="test", content=_build_eml())
    config = FileAnalysisConfig.model_construct(input_files=[file_obj], chat_model=mock_chat_model)
    tool = EmailAnalysisTool(config=config)
    mock_md = MagicMock()
    mock_md.convert.return_value.text_content = "description"
    with patch("codemie_tools.file_analysis.email.tools.MarkItDown", return_value=mock_md) as mock_md_cls:
        tool._analyze_image_attachment(b"\x89PNG", "img.png", ".png")
    _, kwargs = mock_md_cls.call_args
    assert kwargs["llm_client"] is mock_chat_model.root_client


def test_analyze_image_attachment_llm_client_none_when_no_chat_model():
    tool = EmailAnalysisTool(config=_make_config())  # no chat_model
    mock_md = MagicMock()
    mock_md.convert.return_value.text_content = "ok"
    with patch("codemie_tools.file_analysis.email.tools.MarkItDown", return_value=mock_md) as mock_md_cls:
        tool._analyze_image_attachment(b"\x89PNG", "img.png", ".png")
    _, kwargs = mock_md_cls.call_args
    assert kwargs["llm_client"] is None


def test_analyze_image_attachment_returns_error_on_conversion_failure():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.email.tools.MarkItDown") as mock_md_cls:
        mock_md_cls.return_value.convert.side_effect = Exception("File conversion failed")
        result = tool._analyze_image_attachment(b"\x89PNG", "photo.png", ".png")
    assert "Could not extract content from photo.png" in result


def test_analyze_image_attachment_routes_png_via_image_handler():
    """PNG extension must reach _analyze_image_attachment, not the generic dispatcher."""
    tool = EmailAnalysisTool(config=_make_config())
    with patch.object(tool, "_analyze_image_attachment", return_value="image text") as mock_img:
        result = tool._analyze_attachment(b"\x89PNG", "diagram.png")
    mock_img.assert_called_once()
    assert result == "image text"


@pytest.mark.parametrize("ext", [".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"])
def test_analyze_attachment_routes_all_image_types_to_image_handler(ext):
    tool = EmailAnalysisTool(config=_make_config())
    with patch.object(tool, "_analyze_image_attachment", return_value="img") as mock_img:
        tool._analyze_attachment(b"imgdata", f"photo{ext}")
    mock_img.assert_called_once()


def test_execute_with_multiple_eml_files_returns_both():
    eml1 = _build_eml(subject="First Email")
    eml2 = _build_eml(subject="Second Email")
    f1 = FileObject(name="first.eml", mime_type="message/rfc822", owner="test", content=eml1)
    f2 = FileObject(name="second.eml", mime_type="message/rfc822", owner="test", content=eml2)
    tool = EmailAnalysisTool(config=FileAnalysisConfig(input_files=[f1, f2]))
    result = tool.execute()
    assert "First Email" in result
    assert "Second Email" in result
    assert "---" in result  # separator between files


# ---------------------------------------------------------------------------
# Helpers for MSG / OLE unit tests
# ---------------------------------------------------------------------------


def _utf16le(text: str) -> bytes:
    return text.encode("utf-16-le")


def _make_ole_mock(streams: dict[str, bytes]) -> MagicMock:
    """Build a minimal OLE mock whose streams are provided as a dict."""
    ole = MagicMock()
    ole.exists.side_effect = lambda path: path in streams

    def _openstream(path: str) -> MagicMock:
        mock_stream = MagicMock()
        mock_stream.read.return_value = streams.get(path, b"")
        return mock_stream

    ole.openstream.side_effect = _openstream
    ole.listdir.return_value = []
    return ole


def _make_msg_ole(
    subject: str = "Subject",
    sender_name: str = "Sender",
    sender_smtp: str = "sender@test.com",
    to_addr: str = "to@test.com",
    cc_addr: str = "",
    body: str = "Body text",
) -> MagicMock:
    """Return an OLE mock pre-loaded with typical MSG streams."""
    streams: dict[str, bytes] = {
        "__substg1.0_0037001F": _utf16le(subject),
        "__substg1.0_0C1A001F": _utf16le(sender_name),
        "__substg1.0_5D01001F": _utf16le(sender_smtp),
        "__substg1.0_0E04001F": _utf16le(to_addr),
        "__substg1.0_1000001F": _utf16le(body),
    }
    if cc_addr:
        streams["__substg1.0_0E03001F"] = _utf16le(cc_addr)
    return _make_ole_mock(streams)


def _run_process_msg(
    tool: EmailAnalysisTool,
    ole_mock: MagicMock,
    source_name: str = "test.msg",
    attachment_names: list[str] | None = None,
) -> str:
    """Run _process_msg with all file-system and OLE calls mocked out."""
    mock_olefile = MagicMock()
    mock_olefile.OleFileIO.return_value = ole_mock

    mock_tmp = MagicMock()
    mock_tmp.__enter__ = MagicMock(return_value=mock_tmp)
    mock_tmp.__exit__ = MagicMock(return_value=False)
    mock_tmp.name = "/tmp/fake_test.msg"

    with (
        patch.dict("sys.modules", {"olefile": mock_olefile}),
        patch("codemie_tools.file_analysis.email.tools.tempfile.NamedTemporaryFile", return_value=mock_tmp),
        patch("codemie_tools.file_analysis.email.tools.os.unlink"),
    ):
        return tool._process_msg(b"fake", source_name, attachment_names or [])


# ---------------------------------------------------------------------------
# _read_msg_date tests
# ---------------------------------------------------------------------------


def test_read_msg_date_returns_date_from_transport_headers():
    headers = "From: alice\r\nDate: Mon, 01 Jan 2024 12:00:00 +0000\r\nTo: bob"
    ole = _make_ole_mock({_MSG_TRANSPORT_HEADERS: _utf16le(headers)})
    assert EmailAnalysisTool._read_msg_date(ole) == "Mon, 01 Jan 2024 12:00:00 +0000"


def test_read_msg_date_returns_empty_when_no_date_line_in_headers():
    headers = "From: alice\r\nSubject: Hello\r\nTo: bob"
    ole = _make_ole_mock({_MSG_TRANSPORT_HEADERS: _utf16le(headers)})
    assert EmailAnalysisTool._read_msg_date(ole) == ""


def test_read_msg_date_falls_back_to_filetime():
    import datetime as _dt

    dt = _dt.datetime(2024, 1, 15, 10, 30, 0, tzinfo=_dt.timezone.utc)
    epoch_diff = 116444736000000000
    filetime = int(dt.timestamp() * 10_000_000) + epoch_diff
    raw = struct.pack("<Q", filetime)
    ole = _make_ole_mock({_MSG_DATE: raw})
    result = EmailAnalysisTool._read_msg_date(ole)
    assert "15 Jan 2024" in result
    assert "+0000" in result


def test_read_msg_date_returns_empty_when_no_streams():
    ole = _make_ole_mock({})
    assert EmailAnalysisTool._read_msg_date(ole) == ""


# ---------------------------------------------------------------------------
# _read_msg_html_body tests
# ---------------------------------------------------------------------------


def test_read_msg_html_body_returns_empty_when_no_stream():
    ole = _make_ole_mock({})
    assert EmailAnalysisTool._read_msg_html_body(ole) == ""


def test_read_msg_html_body_strips_html_tags():
    html = b"<html><body><p>Hello World</p></body></html>"
    ole = _make_ole_mock({_MSG_BODY_HTML: html})
    result = EmailAnalysisTool._read_msg_html_body(ole)
    assert "Hello World" in result
    assert "<p>" not in result


def test_read_msg_html_body_decodes_html_entities():
    html = b"<p>AT&amp;T &lt;Hello&gt;</p>"
    ole = _make_ole_mock({_MSG_BODY_HTML: html})
    result = EmailAnalysisTool._read_msg_html_body(ole)
    assert "AT&T" in result
    assert "<Hello>" in result


def test_read_msg_html_body_strips_style_and_script_blocks():
    html = b"<style>body{color:red}</style><p>Visible</p><script>alert(1)</script>"
    ole = _make_ole_mock({_MSG_BODY_HTML: html})
    result = EmailAnalysisTool._read_msg_html_body(ole)
    assert "Visible" in result
    assert "color:red" not in result
    assert "alert(1)" not in result


def test_read_msg_html_body_uses_cpid_charset():
    """When PR_INTERNET_CPID signals UTF-16-LE, the HTML bytes are decoded correctly."""
    text = "Héllo"
    html_bytes = f"<p>{text}</p>".encode("utf-16-le")
    cpid_raw = struct.pack("<I", 1200)  # 1200 = utf-16-le
    ole = _make_ole_mock({_MSG_BODY_HTML: html_bytes, _MSG_INTERNET_CPID: cpid_raw})
    result = EmailAnalysisTool._read_msg_html_body(ole)
    assert text in result


# ---------------------------------------------------------------------------
# _read_msg_rtf_body tests
# ---------------------------------------------------------------------------


def test_read_msg_rtf_body_returns_empty_when_no_stream():
    ole = _make_ole_mock({})
    assert EmailAnalysisTool._read_msg_rtf_body(ole) == ""


def test_read_msg_rtf_body_returns_empty_when_compressed_rtf_missing():
    ole = _make_ole_mock({_MSG_BODY_RTF: b"some data"})
    with patch.dict("sys.modules", {"compressed_rtf": None}):
        result = EmailAnalysisTool._read_msg_rtf_body(ole)
    assert result == ""


def test_read_msg_rtf_body_extracts_plain_text():
    ole = _make_ole_mock({_MSG_BODY_RTF: b"compressed"})
    mock_crtf = MagicMock()
    mock_crtf.decompress.return_value = b"{\\rtf1 Hello World}"
    with patch.dict("sys.modules", {"compressed_rtf": mock_crtf}):
        result = EmailAnalysisTool._read_msg_rtf_body(ole)
    assert "Hello" in result
    assert "World" in result


# ---------------------------------------------------------------------------
# _process_msg integration tests (CC, Date, sender resolution)
# ---------------------------------------------------------------------------


def test_process_msg_includes_cc():
    tool = EmailAnalysisTool(config=_make_config())
    ole = _make_msg_ole(cc_addr="charlie@example.com")
    result = _run_process_msg(tool, ole)
    assert "charlie@example.com" in result


def test_process_msg_includes_date_from_transport_headers():
    tool = EmailAnalysisTool(config=_make_config())
    ole = _make_msg_ole()
    with patch.object(EmailAnalysisTool, "_read_msg_date", return_value="Mon, 01 Jan 2024 12:00:00 +0000"):
        result = _run_process_msg(tool, ole)
    assert "01 Jan 2024" in result


def test_process_msg_formats_sender_as_name_plus_email():
    tool = EmailAnalysisTool(config=_make_config())
    ole = _make_msg_ole(sender_name="Alice Smith", sender_smtp="alice@example.com")
    result = _run_process_msg(tool, ole)
    assert "Alice Smith <alice@example.com>" in result


def test_process_msg_filters_x500_sender_address():
    """Exchange X.500 DN (/O=...) should be discarded; only the display name is used."""
    tool = EmailAnalysisTool(config=_make_config())
    streams = {
        "__substg1.0_0037001F": _utf16le("Subject"),
        "__substg1.0_0C1A001F": _utf16le("Alice Smith"),
        "__substg1.0_5D01001F": _utf16le("/O=EXCHANGE/OU=First/CN=Alice"),
        "__substg1.0_0E04001F": _utf16le("to@test.com"),
        "__substg1.0_1000001F": _utf16le("Body"),
    }
    ole = _make_ole_mock(streams)
    result = _run_process_msg(tool, ole)
    assert "/O=EXCHANGE" not in result
    assert "Alice Smith" in result


def test_process_msg_body_falls_back_to_html_when_no_plain_text():
    tool = EmailAnalysisTool(config=_make_config())
    ole = _make_msg_ole(body="")
    with patch.object(EmailAnalysisTool, "_read_msg_html_body", return_value="HTML body content"):
        result = _run_process_msg(tool, ole)
    assert "HTML body content" in result
